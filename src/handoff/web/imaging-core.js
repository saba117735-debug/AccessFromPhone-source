/* imaging-core.js: the maths behind the picture viewer and editor.
 * No page code in here (no document, no canvas), so every rule is tested on its own: tests/js/imaging_test.js.
 *
 * Positions inside a picture are in the picture's own pixels ("content" coordinates). A view maps them to the screen:
 *     screenX = view.tx + x * view.scale
 */
(function (root) {
  "use strict";

  var clamp = function (v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; };
  var round = Math.round;

  /* ------------------------------------------------------------------ colour */

  var NEUTRAL = { brightness: 0, contrast: 0, saturation: 0, warmth: 0, sepia: 0, gray: 0 };

  /** Every filter is a set of the same sliders the Adjust tool has, so a filter is a starting point you can still tune. */
  var PRESETS = [
    { id: "none", name: "Original", p: {} },
    { id: "vivid", name: "Vivid", p: { contrast: 12, saturation: 35 } },
    { id: "warm", name: "Warm", p: { warmth: 35, saturation: 8 } },
    { id: "cool", name: "Cool", p: { warmth: -35, saturation: 5 } },
    { id: "fade", name: "Fade", p: { contrast: -18, brightness: 8, saturation: -20 } },
    { id: "mono", name: "Mono", p: { gray: 1, contrast: 8 } },
    { id: "noir", name: "Noir", p: { gray: 1, contrast: 40, brightness: -8 } },
    { id: "sepia", name: "Sepia", p: { sepia: 1, contrast: 5 } },
  ];

  function params(over) { return Object.assign({}, NEUTRAL, over || {}); }
  function isNeutral(p) { for (var k in NEUTRAL) if (!p || (p[k] || 0) !== 0) return false; return true; }
  function adjustKey(p) { return Object.keys(NEUTRAL).map(function (k) { return (p && p[k]) || 0; }).join(","); }

  /** Change the colours of RGBA pixel data (a Uint8ClampedArray) in place. Order: brightness, contrast, saturation, warmth, sepia, grey. */
  function applyAdjust(data, p) {
    if (isNeutral(p)) return data;
    var add = (p.brightness || 0) * 1.275;                    // -100..100 -> +-127
    var c = (p.contrast || 0) * 2.55;
    var cf = (259 * (c + 255)) / (255 * (259 - c));           // the standard contrast curve around mid grey
    var sat = 1 + (p.saturation || 0) / 100;                  // 0 (grey) .. 2 (double)
    var warm = (p.warmth || 0) * 0.3;
    var sep = clamp(p.sepia || 0, 0, 1), gray = clamp(p.gray || 0, 0, 1);
    for (var i = 0; i < data.length; i += 4) {
      var r = data[i] + add, g = data[i + 1] + add, b = data[i + 2] + add;
      if (c) { r = cf * (r - 128) + 128; g = cf * (g - 128) + 128; b = cf * (b - 128) + 128; }
      if (sat !== 1) { var l = 0.299 * r + 0.587 * g + 0.114 * b; r = l + (r - l) * sat; g = l + (g - l) * sat; b = l + (b - l) * sat; }
      if (warm) { r += warm; b -= warm; }
      if (sep) {
        var tr = 0.393 * r + 0.769 * g + 0.189 * b, tg = 0.349 * r + 0.686 * g + 0.168 * b, tb = 0.272 * r + 0.534 * g + 0.131 * b;
        r += (tr - r) * sep; g += (tg - g) * sep; b += (tb - b) * sep;
      }
      if (gray) { var m = 0.299 * r + 0.587 * g + 0.114 * b; r += (m - r) * gray; g += (m - g) * gray; b += (m - b) * gray; }
      data[i] = r; data[i + 1] = g; data[i + 2] = b;          // a Uint8ClampedArray clamps and rounds on assignment
    }
    return data;
  }

  /* ------------------------------------------------------------------ viewing: zoom and pan */

  /** The scale at which the whole content fits a box. */
  function fitScale(w, h, boxW, boxH) { return Math.min(boxW / w, boxH / h); }

  /** Keep the picture on screen: centred when it is smaller than the box, never dragged out of view when it is bigger. */
  function clampView(view, w, h, boxW, boxH) {
    var cw = w * view.scale, ch = h * view.scale;
    return {
      scale: view.scale,
      tx: cw <= boxW ? (boxW - cw) / 2 : clamp(view.tx, boxW - cw, 0),
      ty: ch <= boxH ? (boxH - ch) / 2 : clamp(view.ty, boxH - ch, 0),
    };
  }

  /** Zoom by `factor`, keeping the point (cx, cy) on the screen where it is (pinch, wheel, double tap). */
  function zoomAt(view, factor, cx, cy, minScale, maxScale) {
    var next = clamp(view.scale * factor, minScale, maxScale), k = next / view.scale;
    return { scale: next, tx: cx - (cx - view.tx) * k, ty: cy - (cy - view.ty) * k };
  }

  function fitView(w, h, boxW, boxH) {
    var s = fitScale(w, h, boxW, boxH);
    return { scale: s, tx: (boxW - w * s) / 2, ty: (boxH - h * s) / 2 };
  }

  function toContent(view, sx, sy) { return { x: (sx - view.tx) / view.scale, y: (sy - view.ty) / view.scale }; }
  function toScreen(view, x, y) { return { x: view.tx + x * view.scale, y: view.ty + y * view.scale }; }

  /* ------------------------------------------------------------------ crop */

  var ASPECTS = [
    { id: "free", name: "Free" }, { id: "orig", name: "Original" }, { id: "1:1", name: "Square" },
    { id: "4:3", name: "4:3" }, { id: "3:2", name: "3:2" }, { id: "16:9", name: "16:9" },
  ];

  /** A crop shape as width / height, or null for free. `flip` swaps landscape and portrait (4:3 becomes 3:4). */
  function aspectRatio(id, w, h, flip) {
    var r = null;
    if (id === "orig") r = w / h;
    else if (id && id !== "free") { var a = id.split(":"); r = Number(a[0]) / Number(a[1]); }
    return r && flip ? 1 / r : r;
  }

  /** The biggest rectangle of this shape that fits in the bounds, around the old rectangle's centre. */
  function fitAspect(rect, ratio, bounds) {
    if (!ratio) return rect;
    var cx = rect.x + rect.w / 2, cy = rect.y + rect.h / 2;
    var w = Math.min(bounds.w, bounds.h * ratio), h = w / ratio;
    // stay near the old rectangle's size, never bigger than the picture
    var scale = Math.min(1, Math.sqrt((rect.w * rect.h) / (w * h)) * 1.0001);
    w *= scale; h *= scale;
    return keepInside({ x: cx - w / 2, y: cy - h / 2, w: w, h: h }, bounds);
  }

  function keepInside(r, b) {
    var w = Math.min(r.w, b.w), h = Math.min(r.h, b.h);
    return { x: clamp(r.x, 0, b.w - w), y: clamp(r.y, 0, b.h - h), w: w, h: h };
  }

  /**
   * Drag a crop handle. `handle` is "move" or a side/corner: n, s, e, w, ne, nw, se, sw. `dx`, `dy` is how far it moved since the drag
   * began, in picture pixels, and `start` is the rectangle at that moment. With a `ratio` the shape is kept; the opposite side stays put.
   */
  function dragCrop(start, handle, dx, dy, bounds, ratio, minSize) {
    var min = minSize || 24;
    if (handle === "move") return keepInside({ x: start.x + dx, y: start.y + dy, w: start.w, h: start.h }, bounds);
    var left = start.x, top = start.y, right = start.x + start.w, bottom = start.y + start.h;
    var hasW = handle.indexOf("w") >= 0, hasE = handle.indexOf("e") >= 0, hasN = handle.indexOf("n") >= 0, hasS = handle.indexOf("s") >= 0;
    if (hasW) left = clamp(left + dx, 0, right - min);
    if (hasE) right = clamp(right + dx, left + min, bounds.w);
    if (hasN) top = clamp(top + dy, 0, bottom - min);
    if (hasS) bottom = clamp(bottom + dy, top + min, bounds.h);
    if (!ratio) return { x: left, y: top, w: right - left, h: bottom - top };

    // keep the shape: work out the size the drag asks for, then hang it off the fixed side
    var w = right - left, h = bottom - top, corner = (hasW || hasE) && (hasN || hasS);
    if (corner) { if (Math.abs(dx) >= Math.abs(dy) * ratio) h = w / ratio; else w = h * ratio; }
    else if (hasW || hasE) h = w / ratio;
    else w = h * ratio;
    // the most room there is on the free side(s)
    var anchorX = hasW ? start.x + start.w : hasE ? start.x : start.x + start.w / 2;
    var anchorY = hasN ? start.y + start.h : hasS ? start.y : start.y + start.h / 2;
    var roomW = hasW ? anchorX : hasE ? bounds.w - anchorX : 2 * Math.min(anchorX, bounds.w - anchorX);
    var roomH = hasN ? anchorY : hasS ? bounds.h - anchorY : 2 * Math.min(anchorY, bounds.h - anchorY);
    var k = Math.min(1, roomW / w, roomH / h);
    w *= k; h *= k;
    if (w < min) { w = min; h = w / ratio; }
    if (h < min) { h = min; w = h * ratio; }
    var x = hasW ? anchorX - w : hasE ? anchorX : anchorX - w / 2;
    var y = hasN ? anchorY - h : hasS ? anchorY : anchorY - h / 2;
    return keepInside({ x: x, y: y, w: w, h: h }, bounds);
  }

  /** Which crop handle (if any) a point is on or inside. `grab` is the touch size in picture pixels. */
  function cropHandleAt(rect, x, y, grab) {
    var near = function (v, edge) { return Math.abs(v - edge) <= grab; };
    var l = near(x, rect.x), r = near(x, rect.x + rect.w), t = near(y, rect.y), b = near(y, rect.y + rect.h);
    var inX = x > rect.x - grab && x < rect.x + rect.w + grab, inY = y > rect.y - grab && y < rect.y + rect.h + grab;
    if (t && l) return "nw"; if (t && r) return "ne"; if (b && l) return "sw"; if (b && r) return "se";
    if (t && inX) return "n"; if (b && inX) return "s"; if (l && inY) return "w"; if (r && inY) return "e";
    if (x > rect.x && x < rect.x + rect.w && y > rect.y && y < rect.y + rect.h) return "move";
    return null;
  }

  /* ------------------------------------------------------------------ annotations */

  /**
   * Objects drawn on the picture, in picture pixels:
   *   pen / marker   { pts: [[x, y]...], color, size }
   *   line / arrow   { x1, y1, x2, y2, color, size }
   *   rect / ellipse { x, y, w, h, color, size, fill }
   *   text           { x, y, text, size, color, bold, bg }     (x, y = top left)
   *   blur           { x, y, w, h, mode: "pixelate" | "blur", amount }
   */
  var RECTLIKE = { rect: 1, ellipse: 1, blur: 1 };
  var isRectLike = function (o) { return !!RECTLIKE[o.type]; };

  function clone(objs) { return JSON.parse(JSON.stringify(objs)); }

  function normalize(o) {
    if (isRectLike(o)) { if (o.w < 0) { o.x += o.w; o.w = -o.w; } if (o.h < 0) { o.y += o.h; o.h = -o.h; } }
    return o;
  }

  /** Move every object to follow a change to the picture: {kind: "crop", x, y} | {kind: "rotate", dir: 90 | -90 | 180, w, h} | {kind: "flip", axis: "h" | "v", w, h} | {kind: "scale", k}. w, h = the picture BEFORE the change. */
  function transformObjects(objs, op) {
    var pt;
    if (op.kind === "crop") pt = function (x, y) { return [x - op.x, y - op.y]; };
    else if (op.kind === "scale") pt = function (x, y) { return [x * op.k, y * op.k]; };
    else if (op.kind === "flip") pt = op.axis === "h" ? function (x, y) { return [op.w - x, y]; } : function (x, y) { return [x, op.h - y]; };
    else if (op.dir === 90) pt = function (x, y) { return [op.h - y, x]; };
    else if (op.dir === -90) pt = function (x, y) { return [y, op.w - x]; };
    else pt = function (x, y) { return [op.w - x, op.h - y]; };
    var grow = op.kind === "scale" ? op.k : 1;

    return objs.map(function (src) {
      var o = Object.assign({}, src), p;
      if (o.pts) o.pts = o.pts.map(function (q) { return pt(q[0], q[1]); });
      if (o.type === "line" || o.type === "arrow") { p = pt(o.x1, o.y1); o.x1 = p[0]; o.y1 = p[1]; p = pt(o.x2, o.y2); o.x2 = p[0]; o.y2 = p[1]; }
      if (isRectLike(o)) {
        var a = pt(o.x, o.y), b = pt(o.x + o.w, o.y + o.h);
        o.x = Math.min(a[0], b[0]); o.y = Math.min(a[1], b[1]); o.w = Math.abs(b[0] - a[0]); o.h = Math.abs(b[1] - a[1]);
      }
      if (o.type === "text") {
        // text stays upright: it keeps its size and its centre follows the picture
        var cw = o.w || 0, ch = o.h || 0;
        p = pt(o.x + cw / 2, o.y + ch / 2);
        o.x = p[0] - cw / 2; o.y = p[1] - ch / 2;
        if (op.kind === "rotate" && Math.abs(op.dir) === 90) { /* its measured box swaps */ var t = o.w; o.w = o.h; o.h = t; o.x = p[0] - (o.w || 0) / 2; o.y = p[1] - (o.h || 0) / 2; }
      }
      if (grow !== 1) { if (o.size) o.size *= grow; if (o.amount) o.amount *= grow; if (o.w && o.type === "text") o.w *= grow; if (o.h && o.type === "text") o.h *= grow; }
      return o;
    });
  }

  function distToSegment(px, py, x1, y1, x2, y2) {
    var dx = x2 - x1, dy = y2 - y1, len = dx * dx + dy * dy;
    var t = len ? clamp(((px - x1) * dx + (py - y1) * dy) / len, 0, 1) : 0;
    return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
  }

  /** The box around an object, for selecting it and showing handles. Text needs its measured size (o.w, o.h), set when it was drawn. */
  function boundsOf(o) {
    if (isRectLike(o)) return { x: o.x, y: o.y, w: o.w, h: o.h };
    if (o.type === "text") return { x: o.x, y: o.y, w: o.w || o.size * 0.6 * (o.text || "").length, h: o.h || o.size * 1.3 };
    var pts = o.pts || [[o.x1, o.y1], [o.x2, o.y2]], pad = (o.size || 4) / 2;
    var xs = pts.map(function (q) { return q[0]; }), ys = pts.map(function (q) { return q[1]; });
    var x = Math.min.apply(null, xs) - pad, y = Math.min.apply(null, ys) - pad;
    return { x: x, y: y, w: Math.max.apply(null, xs) + pad - x, h: Math.max.apply(null, ys) + pad - y };
  }

  /** The topmost object under a point, or -1. `tol` = how far off a finger may be, in picture pixels. */
  function hitTest(objs, x, y, tol) {
    for (var i = objs.length - 1; i >= 0; i--) {
      var o = objs[i], pad = (o.size || 4) / 2 + tol;
      if (o.pts) {
        for (var j = 1; j < o.pts.length; j++) if (distToSegment(x, y, o.pts[j - 1][0], o.pts[j - 1][1], o.pts[j][0], o.pts[j][1]) <= pad) return i;
        if (o.pts.length === 1 && Math.hypot(x - o.pts[0][0], y - o.pts[0][1]) <= pad) return i;
      } else if (o.type === "line" || o.type === "arrow") {
        if (distToSegment(x, y, o.x1, o.y1, o.x2, o.y2) <= pad) return i;
      } else {
        var b = boundsOf(o);
        if (x >= b.x - tol && x <= b.x + b.w + tol && y >= b.y - tol && y <= b.y + b.h + tol) return i;
      }
    }
    return -1;
  }

  /** Which corner handle of a selected rectangle-like object a point is on ("nw", "ne", "se", "sw"), or null. */
  function cornerAt(o, x, y, grab) {
    var cs = { nw: [o.x, o.y], ne: [o.x + o.w, o.y], se: [o.x + o.w, o.y + o.h], sw: [o.x, o.y + o.h] };
    for (var k in cs) if (Math.hypot(x - cs[k][0], y - cs[k][1]) <= grab) return k;
    return null;
  }

  /** Drag a corner of a rectangle-like object: the opposite corner stays put. */
  function dragCorner(start, corner, dx, dy) {
    var l = start.x, t = start.y, r = start.x + start.w, b = start.y + start.h;
    if (corner.indexOf("w") >= 0) l += dx; else r += dx;
    if (corner.indexOf("n") >= 0) t += dy; else b += dy;
    return { x: Math.min(l, r), y: Math.min(t, b), w: Math.abs(r - l), h: Math.abs(b - t) };
  }

  /** Move an object by (dx, dy). */
  function moveObject(o, dx, dy) {
    var n = Object.assign({}, o);
    if (n.pts) n.pts = n.pts.map(function (q) { return [q[0] + dx, q[1] + dy]; });
    if (n.type === "line" || n.type === "arrow") { n.x1 += dx; n.y1 += dy; n.x2 += dx; n.y2 += dy; }
    if (isRectLike(n) || n.type === "text") { n.x += dx; n.y += dy; }
    return n;
  }

  /** Smooth a hand-drawn stroke a little (drops points that are too close, so strokes stay light). */
  function thin(pts, minDist) {
    if (pts.length < 3) return pts;
    var out = [pts[0]];
    for (var i = 1; i < pts.length - 1; i++) { var l = out[out.length - 1]; if (Math.hypot(pts[i][0] - l[0], pts[i][1] - l[1]) >= minDist) out.push(pts[i]); }
    out.push(pts[pts.length - 1]);
    return out;
  }

  /* ------------------------------------------------------------------ files and sizes */

  var MIME = { png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", webp: "image/webp" };
  function extOf(name) { var m = /\.([A-Za-z0-9]+)$/.exec(name || ""); return m ? m[1].toLowerCase() : ""; }
  function stemOf(name) { return String(name || "").replace(/\.[A-Za-z0-9]+$/, ""); }
  function sameKind(a, b) { var n = function (e) { return e === "jpeg" ? "jpg" : e; }; return n(a) === n(b); }

  /** "photo.png" -> "photo (edited).png"; an edited copy edited again stays "photo (edited).png" (the PC adds (2) if that name is taken). */
  function editedName(name, ext) {
    var stem = stemOf(name).replace(/ \(edited(?: \d+)?\)$/, "");
    return stem + " (edited)." + (ext || extOf(name) || "png");
  }

  /** Shrink a picture's size to fit a pixel budget, keeping its shape. Phones' canvases give up past about 16 million pixels. */
  function capSize(w, h, maxPixels) {
    if (w * h <= maxPixels) return { w: w, h: h, scaled: false };
    var k = Math.sqrt(maxPixels / (w * h));
    return { w: Math.max(1, Math.floor(w * k)), h: Math.max(1, Math.floor(h * k)), scaled: true };
  }

  /** What was done, in a few words, for the record of the save: ["crop", "rotate", "text", "text"] -> "cropped, rotated, 2 text notes". */
  function describeEdits(list) {
    var names = { crop: ["cropped"], rotate: ["rotated"], flip: ["flipped"], resize: ["resized"], adjust: ["colours adjusted"], filter: ["filter applied"],
                  pen: ["drawing", "drawings"], marker: ["highlight", "highlights"], arrow: ["arrow", "arrows"], line: ["line", "lines"], rect: ["box", "boxes"],
                  ellipse: ["oval", "ovals"], text: ["text note", "text notes"], blur: ["hidden area", "hidden areas"] };
    var counts = {}, order = [];
    list.forEach(function (k) { if (!counts[k]) { counts[k] = 0; order.push(k); } counts[k]++; });
    return order.filter(function (k) { return names[k]; }).map(function (k) {
      var n = counts[k], pair = names[k];
      return pair.length === 1 || n === 1 ? pair[0] : n + " " + pair[1];  // "arrow", but "2 arrows"
    }).join(", ");
  }

  function fmtBytes(n) { return n < 1024 ? n + " B" : n < 1048576 ? (n / 1024).toFixed(n < 10240 ? 1 : 0) + " KB" : (n / 1048576).toFixed(1) + " MB"; }

  /* ------------------------------------------------------------------ undo and redo */

  /** A list of states with an index: push after each change, undo and redo move through it. Old states are dropped past `limit`. */
  function History(limit) {
    var states = [], at = -1, max = limit || 30;
    return {
      reset: function (state) { states = [state]; at = 0; },
      push: function (state) { states = states.slice(0, at + 1); states.push(state); if (states.length > max) states.shift(); at = states.length - 1; },
      undo: function () { if (at > 0) at--; return states[at]; },
      redo: function () { if (at < states.length - 1) at++; return states[at]; },
      canUndo: function () { return at > 0; },
      canRedo: function () { return at < states.length - 1; },
      index: function () { return at; },
      current: function () { return states[at]; },
    };
  }

  var api = {
    NEUTRAL: NEUTRAL, PRESETS: PRESETS, ASPECTS: ASPECTS, MIME: MIME,
    params: params, isNeutral: isNeutral, adjustKey: adjustKey, applyAdjust: applyAdjust,
    fitScale: fitScale, clampView: clampView, zoomAt: zoomAt, fitView: fitView, toContent: toContent, toScreen: toScreen,
    aspectRatio: aspectRatio, fitAspect: fitAspect, keepInside: keepInside, dragCrop: dragCrop, cropHandleAt: cropHandleAt,
    isRectLike: isRectLike, clone: clone, normalize: normalize, transformObjects: transformObjects, boundsOf: boundsOf, hitTest: hitTest,
    cornerAt: cornerAt, dragCorner: dragCorner, moveObject: moveObject, thin: thin, distToSegment: distToSegment,
    extOf: extOf, stemOf: stemOf, sameKind: sameKind, editedName: editedName, capSize: capSize, describeEdits: describeEdits, fmtBytes: fmtBytes,
    History: History, clamp: clamp, round: round,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.AfpImgCore = api;
})(typeof window !== "undefined" ? window : this);
