/* screen.js: looking at the PC's screen from the phone, and (when allowed) controlling it like a real PC.
 *
 * The PC decides everything: nobody sees the screen until the Owner says yes on the PC, only one phone looks at a time, taking control needs Operate level, your turn
 * and the Owner's separate yes (the Admin phone has it, any other phone can ASK from here), and the PC shows a banner with a Stop button the whole time.
 * This file asks, waits, shows the pictures, and turns touches into mouse and keyboard events. It is a component: `AfpScreen.create(deps)` gets the page's helpers
 * (h, ico, api, toast, haptic, openSheet) and gives back { open, close, wants, state }. Everything that can be worked out without a page is a plain function
 * tested on its own: tests/js/screen_test.js (the chat shortcut, the wording, automatic quality, where a touch lands, and the trackpad: what a touch means).
 *
 * The live picture: the phone asks for one JPEG at a time and asks for the next when the last has arrived, so a slow link just gets fewer pictures a second, never a
 * growing delay. "Auto" quality watches how long each picture took and steps the size down quickly and back up slowly.
 *
 * Controlling feels like a laptop's trackpad: one finger moves the pointer (faster swipes go further), a tap clicks, a two-finger tap right-clicks, two fingers scroll,
 * tap-then-drag drags, and holding a finger for two seconds brings up a see-through mouse pad. The phone draws its own pointer so it follows the finger instantly.
 */
(function (root) {
  "use strict";

  /* ---------------------------------------------------------------- quality  */

  var LADDER = [  // from "small and quick" to "big and sharp": Auto moves along this. The top is the PC's own width (no shrinking, so text is as drawn)
    { w: 640, q: 45 }, { w: 800, q: 55 }, { w: 1024, q: 62 }, { w: 1280, q: 68 }, { w: 1600, q: 72 }, { w: 1920, q: 76 },
  ];
  var PRESETS = [
    { id: "auto", name: "Auto", gap: 0, note: "Adapts to your network" },
    { id: "fast", name: "Fast", w: 800, q: 50, gap: 0, note: "Smoothest on a slow link" },
    { id: "balanced", name: "Balanced", w: 1280, q: 68, gap: 0, note: "A good middle" },
    { id: "sharp", name: "Sharp", w: 1920, q: 80, gap: 40, note: "Best for reading small text" },
  ];
  var BUSY_FOR = 1500;             // ms of "being used" after the last input
  var LIVE_FLOOR = 2;              // while controlling, Auto never goes below this rung (1024 px): a slower picture is better than a blurry one
  var REFINE = { w: 1920, q: 90 };   // the one extra-sharp picture sent when the screen stops changing
  var PEEK = { w: 320, q: 25, gap: 200 };   // afterwards, tiny look-ins to see whether anything has changed (the answer, not the picture, is what counts)
  function preset(id) { for (var i = 0; i < PRESETS.length; i++) if (PRESETS[i].id === id) return PRESETS[i]; return PRESETS[0]; }
  function nextPreset(id) { for (var i = 0; i < PRESETS.length; i++) if (PRESETS[i].id === id) return PRESETS[(i + 1) % PRESETS.length]; return PRESETS[0]; }

  /* Automatic quality. `state` is { i: where on the ladder, good: how many quick pictures in a row, at: when it last moved }; `sample` is { ms: how long the last picture
     took, now }. Steps DOWN at once when a picture is slow (a slow link must not pile up), and UP only after several quick ones and a pause (so it doesn't flap). */
  function adapt(state, sample) {
    var s = { i: state.i, good: state.good, at: state.at }, top = LADDER.length - 1;
    // `live`: the mouse or keyboard is in use, so each picture should come quickly (the screen answers the hand), but never so small that text blurs
    var floor = sample.live ? Math.min(LIVE_FLOOR, top) : 0, vslow = sample.live ? 320 : 600, slow = sample.live ? 170 : 380, quick = sample.live ? 80 : 170;
    if (sample.ms > vslow) { s.i = Math.max(floor, s.i - 2); s.good = 0; s.at = sample.now; }
    else if (sample.ms > slow) { s.i = Math.max(floor, s.i - 1); s.good = 0; s.at = sample.now; }
    else if (sample.ms < quick) {
      s.good += 1;
      if (s.good >= 4 && sample.now - s.at > 1500 && s.i < top) { s.i += 1; s.good = 0; s.at = sample.now; }
    } else s.good = 0;
    if (s.i < floor) s.i = floor;
    return s;
  }
  function networkWord(ms) { return ms < 200 ? "Good" : ms < 450 ? "Fair" : "Slow"; }

  /* ---------------------------------------------------------------- the chat shortcut and the words  */

  /* Did the person type something like "I want to access my PC screen"? Then the screen opens instead of a request being sent to the assistant. */
  function wants(text) {
    var t = String(text || "").toLowerCase();
    if (!t || t.length > 140 || /[\\/]|:\\/.test(t)) return false;           // a path or a long message is about files, not about looking
    if (/\bremote desktop\b/.test(t)) return true;
    if (!/\bscreen\b(?![._-]?\w)/.test(t)) return false;                      // "screen" as a word (not screenshot, screen.txt, screen_log)
    if (/\b(?:lock|turn off|turn on|dim|brighten|brightness|saver|timeout|resolution|rotate|record)\b/.test(t)) return false;
    return /\b(?:access|see|show|view|share|watch|mirror|cast|present|look at|open|display|check|monitor|remote|control)\b/.test(t);
  }

  function leftText(seconds, forever) {
    if (forever) return "until the PC owner takes it back";
    if (seconds == null) return "";
    if (seconds < 90) return "less than a minute";
    var m = Math.round(seconds / 60);
    if (m < 90) return m + " minutes";
    var h = Math.round(m / 60);
    return h + (h === 1 ? " hour" : " hours");
  }

  function headline(view) {
    if (!view) return { kind: "wait", title: "PC screen", sub: "Checking…" };
    switch (view.state) {
      case "off": return { kind: "off", title: "Screen viewing is off", sub: "The PC owner switched it off in this PC's settings." };
      case "none": return { kind: "none", title: "See your PC's screen", sub: "A live picture of what is on the PC. The PC owner has to say yes on the PC first. Looking is the default; taking control is a separate switch." };
      case "pending": return { kind: "pending", title: "Waiting for a yes on the PC", sub: "Whoever is at the PC sees a window with Allow and Deny. If nobody answers in a couple of minutes, ask again." };
      case "granted": return { kind: "granted", title: "You can look now", sub: view.trusted ? "This is the Admin phone, so it can look without asking. The PC still shows a banner while you look, with a Stop button." : "Allowed for " + leftText(view.left, view.forever) + ". The PC shows a banner while you look, with a Stop button." };
      case "busy": return { kind: "busy", title: (view.viewer || "Someone") + " is looking", sub: "Only one phone can look at a time. Try again when they are done." };
      case "viewing": return { kind: "viewing", title: "Live", sub: "" };
      default: return { kind: "wait", title: "PC screen", sub: "" };
    }
  }

  /* ---------------------------------------------------------------- geometry: touch <-> PC screen  */

  /* A point on the phone's screen, in the picture element's own (unturned) pixels. `rect` is the element's box on the phone, `rot` whether the picture is turned
     a quarter turn to fill an upright phone (then the element's x axis runs down the phone and its y axis runs to the left). */
  function clientToLocal(cx, cy, rect, rot) {
    return rot ? { lx: cy - rect.top, ly: (rect.left + rect.width) - cx } : { lx: cx - rect.left, ly: cy - rect.top };
  }
  function layoutSize(rect, rot) { return rot ? { W: rect.height, H: rect.width } : { W: rect.width, H: rect.height }; }

  /* The picture inside its element. "Fit" keeps its shape and shows all of it, so there can be bars (they are not part of the PC's screen). "Fill" (`cover`) scales it until
     it covers the whole element and crops what spills over, so nothing is left empty. { ox, oy, cw, ch } is the picture itself (it can be bigger than the element in Fill). */
  function contentBox(W, H, nat, cover) {
    var scale = cover ? Math.max(W / nat.w, H / nat.h) : Math.min(W / nat.w, H / nat.h), cw = nat.w * scale, ch = nat.h * scale;
    return { ox: (W - cw) / 2, oy: (H - ch) / 2, cw: cw, ch: ch };
  }

  /* Which part of the PC's screen can be seen, as fractions (all of it in Fit; the middle part in Fill). The pointer is kept inside it so it never hides off the edge. */
  function visibleRange(W, H, nat, cover) {
    var b = contentBox(W, H, nat, cover);
    return { x0: Math.max(0, -b.ox / b.cw), x1: Math.min(1, (W - b.ox) / b.cw), y0: Math.max(0, -b.oy / b.ch), y1: Math.min(1, (H - b.oy) / b.ch) };
  }

  /* Where on the PC's screen is a touch? Returns { x, y } as fractions of the PC's screen (0 to 1) plus { lx, ly } (the touch in the element's pixels), or null in the bars. */
  function pointToScreen(cx, cy, rect, nat, rot, cover) {
    var sz = layoutSize(rect, rot);
    if (!(sz.W > 0 && sz.H > 0 && nat && nat.w > 0 && nat.h > 0)) return null;
    var p = clientToLocal(cx, cy, rect, rot), b = contentBox(sz.W, sz.H, nat, cover);
    var u = (p.lx - b.ox) / b.cw, v = (p.ly - b.oy) / b.ch;
    if (u < 0 || u > 1 || v < 0 || v > 1) return null;
    return { x: u, y: v, lx: p.lx, ly: p.ly };
  }

  /* The other way: where in the picture element is this point of the PC's screen? (To draw the phone's own pointer there.) */
  function screenToLocal(fx, fy, W, H, nat, cover) {
    if (!(W > 0 && H > 0 && nat && nat.w > 0 && nat.h > 0)) return null;
    var b = contentBox(W, H, nat, cover);
    return { lx: b.ox + fx * b.cw, ly: b.oy + fy * b.ch };
  }

  /* A touch in "frame" coordinates: the picture's own axes, so a swipe along the picture means the same whether or not the phone is holding it turned. Only
     differences between two such points are used, so the origin does not matter. */
  function frameOf(cx, cy, rot) { return rot ? { x: cy, y: -cx } : { x: cx, y: cy }; }

  function clamp01(v) { return v < 0 ? 0 : v > 1 ? 1 : v; }

  /* ---------------------------------------------------------------- zooming: which part of the PC's screen is shown
     The "window" { x0, y0, w } is the part being looked at, as fractions of the PC's screen (the picture has the screen's own shape, so it is as tall as it is wide: one `w`).
     Zooming does not enlarge the picture on the phone: the PC is asked for just that part, at full detail, so it stays sharp. Until that arrives, the picture
     already on the phone is scaled and moved to match (winTransform), then swapped. */
  var ZMAX = 5;
  var FULL_WIN = { x0: 0, y0: 0, w: 1 };
  /* The window is what the phone's picture box shows, and the box has the phone's shape, not the PC screen's. `f.k` is the screen's shape over the box's shape, so the window is
     `w` wide and `w * k` tall (as fractions of the PC's screen). Zoomed out ("full") the window is as big as it must be to show the whole screen (Fit: it can spill over the edges,
     which are the bars) or to cover the whole box (Fill). Zoomed in, it is smaller, and the box is always full of picture. With the default frame (k = 1, Fit) it is the screen-shaped square. */
  var FRAME0 = { k: 1, fill: false };
  function fullW(f) { f = f || FRAME0; return f.fill ? Math.min(1, 1 / f.k) : Math.max(1, 1 / f.k); }
  function winH(win, f) { return win.w * (f || FRAME0).k; }
  function clampWin(win, f) {
    f = f || FRAME0;
    var fw = fullW(f), w = Math.min(fw, Math.max(fw / ZMAX, win.w)), h = w * f.k;   // bigger than the screen in a direction: sits in the middle (those are the bars)
    return { x0: w >= 1 ? (1 - w) / 2 : Math.min(1 - w, Math.max(0, win.x0)), y0: h >= 1 ? (1 - h) / 2 : Math.min(1 - h, Math.max(0, win.y0)), w: w };
  }
  function fullWin(f) { return clampWin({ x0: 0, y0: 0, w: fullW(f) }, f); }
  /* Zoom by `factor` (2 = twice as close), keeping the point (fx, fy) of the PC's screen exactly where it is on the phone (under the fingers). */
  function zoomWin(win, factor, fx, fy, f) {
    f = f || FRAME0;
    var fw = fullW(f), nw = Math.min(fw, Math.max(fw / ZMAX, win.w / factor)), u = (fx - win.x0) / win.w, v = (fy - win.y0) / (win.w * f.k);
    return clampWin({ x0: fx - u * nw, y0: fy - v * nw * f.k, w: nw }, f);
  }
  function panWin(win, dx, dy, f) { return clampWin({ x0: win.x0 + dx, y0: win.y0 + dy, w: win.w }, f); }
  /* The part of the PC's screen that is really inside the window (the picture the PC sends). */
  function seenRect(win, f) { return { x0: Math.max(0, win.x0), y0: Math.max(0, win.y0), x1: Math.min(1, win.x0 + win.w), y1: Math.min(1, win.y0 + winH(win, f)) }; }
  function winToScreen(win, u, v, f) { var r = seenRect(win, f); return { x: r.x0 + u * (r.x1 - r.x0), y: r.y0 + v * (r.y1 - r.y0) }; }       // a place in the shown picture (0 to 1) -> the PC's screen
  function screenToWin(win, fx, fy, f) { var r = seenRect(win, f); return { u: (fx - r.x0) / (r.x1 - r.x0), v: (fy - r.y0) / (r.y1 - r.y0) }; }  // the other way (can be outside 0..1: not in view)
  /* Move the window the least needed so the point stays at least `margin` (a fraction of the window) away from its edges: the view follows the pointer. */
  function followWin(win, px, py, margin, f) {
    f = f || FRAME0;
    var m = margin == null ? 0.12 : margin, h = winH(win, f), mx = m * win.w, my = m * h, x0 = win.x0, y0 = win.y0;
    if (px < x0 + mx) x0 = px - mx; else if (px > x0 + win.w - mx) x0 = px + mx - win.w;
    if (py < y0 + my) y0 = py - my; else if (py > y0 + h - my) y0 = py + my - h;
    return clampWin({ x0: x0, y0: y0, w: win.w }, f);
  }
  function sameWin(a, b) { return Math.abs(a.x0 - b.x0) < 1e-4 && Math.abs(a.y0 - b.y0) < 1e-4 && Math.abs(a.w - b.w) < 1e-4; }
  /* The picture on the phone shows `have`, but `want` is what should be seen now: scale it by `a` and move it by (tx, ty) (element pixels, from its top-left)
     so it looks like `want` in the meantime. `box` is where the picture sits in its element ({ ox, oy, cw, ch } from contentBox). */
  function winTransform(have, want, box) {
    var a = have.w / want.w, c = (have.x0 - want.x0) / want.w, e = (have.y0 - want.y0) / want.w;
    return { a: a, tx: box.ox * (1 - a) + c * box.cw, ty: box.oy * (1 - a) + e * box.ch };
  }
  /* The same for the general case: the picture on the phone shows the part `r` of the PC's screen (a seenRect), laid in the box `b` of an element W by H; scale it by `a` and
     move it by (tx, ty) so it looks like the window `want`. */
  function windowTransform(r, b, want, W, H, f) {
    var a = (W / want.w) * (r.x1 - r.x0) / b.cw;
    return { a: a, tx: (r.x0 - want.x0) / want.w * W - a * b.ox, ty: (r.y0 - want.y0) / winH(want, f) * H - a * b.oy };
  }
  /* The PC's sound arrives as plain 16-bit little-endian mono samples; the browser plays floats from -1 to 1. */
  function pcmToFloat(bytes) {
    var n = bytes.length >> 1, out = new Float32Array(n), dv = new DataView(bytes.buffer, bytes.byteOffset, n * 2);
    for (var i = 0; i < n; i++) out[i] = dv.getInt16(i * 2, true) / 32768;
    return out;
  }
  /* When to play the next piece of sound so it follows the last one without gaps: `next` is where the last one ended, `now` the audio clock. A little is kept in hand
     (`lead`) against network hiccups; if the phone has fallen far behind (`maxAhead` seconds queued) the piece is dropped, so the sound stays live instead of drifting late. */
  function planSound(now, next, seconds, lead, maxAhead) {
    var start = next < now + 0.02 ? now + lead : next;
    if (start - now > maxAhead) return { drop: true, start: next, next: next };
    return { drop: false, start: start, next: start + seconds };
  }
  /* A swipe along the picture, in the picture's own axes (see frameOf: the same thing for a difference of two points). */
  function frameVec(dx, dy, rot) { return rot ? { x: dy, y: -dx } : { x: dx, y: dy }; }

  /* Two fingers can mean "scroll" or "zoom". Judged by WHICH WAY each finger goes, not by how far apart they end up: both fingers going the same way is a scroll
     (left to the trackpad or Touch mode); going opposite ways is a pinch. (The old test, the distance between them, took a scroll with one finger above the other
     for a pinch, because the first finger to move changes the distance by its whole step.) The browser reports each finger on its own, so the choice waits until BOTH have
     moved a little; a lone finger moving far while the other stays still is a pinch around that finger. Decided once, early. `emit` gets { kind: "start" }, then
     { kind: "pinch", factor, cx, cy, dx, dy } (the centre moves too, which pans), then { kind: "end" }. */
  function createPinch(emit, opts) {
    var o = Object.assign({ pinch: 14, each: 4, spread: 6, lone: 60 }, opts || {});
    var ptrs = {}, ids = [], mode = "idle", start = null, last = null;
    function pair() {
      var a = ptrs[ids[0]], b = ptrs[ids[1]];
      return { d: Math.max(1, Math.hypot(a.x - b.x, a.y - b.y)), cx: (a.x + b.x) / 2, cy: (a.y + b.y) / 2 };
    }
    function down(id, x, y) {
      if (!ptrs[id]) ids.push(id);
      ptrs[id] = { x: x, y: y };
      if (ids.length === 2 && mode === "idle") {
        start = last = pair(); mode = "deciding";
        start.at = {}; ids.forEach(function (k) { start.at[k] = { x: ptrs[k].x, y: ptrs[k].y }; });   // where each finger began
      }
    }
    function move(id, x, y) {
      if (!ptrs[id]) return;
      ptrs[id].x = x; ptrs[id].y = y;
      if (ids.length < 2 || (id !== ids[0] && id !== ids[1])) return;
      var p = pair();
      if (mode === "deciding") {
        var dd = Math.abs(p.d - start.d);
        var a = ptrs[ids[0]], b = ptrs[ids[1]], sa = start.at[ids[0]], sb = start.at[ids[1]];
        var ax = a.x - sa.x, ay = a.y - sa.y, bx = b.x - sb.x, by = b.y - sb.y, ma = Math.hypot(ax, ay), mb = Math.hypot(bx, by);
        if (ma >= o.each && mb >= o.each) {
          if (ax * bx + ay * by > 0) mode = "other";                                          // both going the same way: a scroll, not ours
          else if (dd >= o.spread) { mode = "pinch"; emit({ kind: "start" }); last = p; }     // going opposite ways and the gap is changing: a pinch
        } else if (Math.max(ma, mb) >= o.lone && dd >= o.pinch) { mode = "pinch"; emit({ kind: "start" }); last = p; }   // one finger stays, the other goes far: a pinch around it
      } else if (mode === "pinch") {
        emit({ kind: "pinch", factor: p.d / last.d, cx: p.cx, cy: p.cy, dx: p.cx - last.cx, dy: p.cy - last.cy });
        last = p;
      }
    }
    function release(id) {
      if (!ptrs[id]) return;
      delete ptrs[id]; ids = ids.filter(function (k) { return k !== id; });
      if (mode === "pinch" && ids.length < 2) { mode = "done"; emit({ kind: "end" }); }
      if (!ids.length) mode = "idle";
      else if (ids.length < 2 && mode === "deciding") mode = "idle";
      else if (ids.length < 2 && mode === "other") mode = "idle";
    }
    function reset() { ptrs = {}; ids = []; mode = "idle"; }
    return { down: down, move: move, up: release, cancel: release, reset: reset, mode: function () { return mode; }, active: function () { return mode === "pinch"; } };
  }

  /* How far the pointer goes for a finger movement: a slow, small movement is precise; a quick swipe goes further (like a laptop's trackpad). `speed` is the person's
     setting (1 = normal). */
  function accelerate(dx, dy, dt, speed) {
    var dist = Math.hypot(dx, dy), v = dist / Math.max(dt, 8);   // pixels per millisecond
    var gain = (speed || 1) * (1 + Math.min(2.4, Math.max(0, v - 0.25) * 1.6));
    return { dx: dx * gain, dy: dy * gain };
  }

  /* ---------------------------------------------------------------- what a touch means: the trackpad  */

  /* One finger moves the pointer. A quick tap clicks. Touch, release, touch again and move: a drag (press, move, release). Two fingers moving scroll; two fingers
     tapping together right-click. Holding one finger still for `hold` ms asks for the mouse pad (a ring is shown from `ring` ms so the wait feels intentional).
     `emit` gets { kind: "move", dx, dy, dt } | { kind: "click", button } | "drag-start" | "drag-end" | { kind: "scroll", dy } | "hold-ring" | "hold-cancel" | "hold".
     `timers` is { set, clear } (the page's own, or a fake in tests); every event carries its time `t` in ms. */
  function createTrackpad(emit, timers, opts) {
    var o = Object.assign({ slop: 9, tap: 700, tapDrag: 300, two: 420, step: 26, ring: 450, hold: 2000 }, opts || {});
    var ptrs = {}, n = 0, mode = "idle", s = null, last = null, downT = 0, lastTap = null, rt = null, ht = null, ringOn = false;
    var acc = 0, lastY = 0, startAvg = null, moved = false, twoT = 0;

    function avg() {
      var x = 0, y = 0, c = 0, id;
      for (id in ptrs) { x += ptrs[id].x; y += ptrs[id].y; c++; }
      return { x: x / c, y: y / c };
    }
    function stopTimers() { if (rt != null) { timers.clear(rt); rt = null; } if (ht != null) { timers.clear(ht); ht = null; } }
    function cancelRing() { if (ringOn) { ringOn = false; emit({ kind: "hold-cancel" }); } }
    function emitMove(x, y, t) { emit({ kind: "move", dx: x - last.x, dy: y - last.y, dt: t - last.t }); last = { x: x, y: y, t: t }; }

    function down(id, x, y, t) {
      ptrs[id] = { x: x, y: y }; n++;
      if (n === 1) {
        s = { x: x, y: y }; last = { x: x, y: y, t: t }; downT = t; stopTimers();
        if (lastTap && t - lastTap.t < o.tapDrag && Math.hypot(x - lastTap.x, y - lastTap.y) < 45) mode = "dragpending";   // a second touch right after a tap: a drag, if it moves
        else {
          mode = "one";
          rt = timers.set(function () { rt = null; if (mode === "one") { ringOn = true; emit({ kind: "hold-ring" }); } }, o.ring);
          ht = timers.set(function () { ht = null; if (mode === "one") { mode = "held"; ringOn = false; emit({ kind: "hold" }); } }, o.hold);
        }
      } else if (n === 2) {
        stopTimers(); cancelRing();
        if (mode === "drag") emit({ kind: "drag-end" });
        mode = "two"; twoT = t; moved = false; acc = 0; startAvg = avg(); lastY = startAvg.y;
      }
    }
    function move(id, x, y, t) {
      if (!ptrs[id]) return;
      ptrs[id].x = x; ptrs[id].y = y;
      if (mode === "one") {
        if (Math.hypot(x - s.x, y - s.y) > o.slop) { stopTimers(); cancelRing(); mode = "move"; lastTap = null; emitMove(x, y, t); }
      } else if (mode === "move" || mode === "drag") emitMove(x, y, t);
      else if (mode === "dragpending") {
        if (Math.hypot(x - s.x, y - s.y) > o.slop) { mode = "drag"; lastTap = null; emit({ kind: "drag-start" }); emitMove(x, y, t); }
      } else if (mode === "two") {
        var a = avg();
        if (!moved) { if (Math.hypot(a.x - startAvg.x, a.y - startAvg.y) > o.slop) { moved = true; acc = 0; lastY = a.y; } return; }
        acc += a.y - lastY; lastY = a.y;
        var steps = acc > 0 ? Math.floor(acc / o.step) : Math.ceil(acc / o.step);
        if (steps !== 0) { acc -= steps * o.step; emit({ kind: "scroll", dy: steps }); }   // fingers down = the page moves down = scroll up (+)
      }
    }
    function release(id, t, cancelled) {
      if (!ptrs[id]) return;
      delete ptrs[id]; n--;
      if (n === 0) {
        stopTimers(); cancelRing();
        if (mode === "one" && !cancelled && t - downT < o.tap) { emit({ kind: "click", button: "left" }); lastTap = { x: s.x, y: s.y, t: t }; }
        else if (mode === "dragpending" && !cancelled) { emit({ kind: "click", button: "left" }); lastTap = null; }   // the second click of a double-click
        else if (mode === "drag") { emit({ kind: "drag-end" }); lastTap = null; }
        mode = "idle";
      } else if (n === 1 && mode === "two") {
        if (!moved && !cancelled && t - twoT < o.two) emit({ kind: "click", button: "right" });   // two fingers tapped together
        mode = "consumed";                                                                    // the finger left does nothing until everyone lets go
      }
    }
    function reset() { stopTimers(); ringOn = false; ptrs = {}; n = 0; mode = "idle"; lastTap = null; }
    return { down: down, move: move, up: function (id, x, y, t) { release(id, t, false); }, cancel: function (id, x, y, t) { release(id, t, true); }, reset: reset, mode: function () { return mode; } };
  }

  /* ---------------------------------------------------------------- what a touch means: pointing straight at the picture ("Touch" mode)  */

  /* One finger: a tap clicks where it lands, a long press right-clicks, a drag drags. Two fingers: scroll. */
  function createGestures(emit, timers, opts) {
    var o = Object.assign({ slop: 10, hold: 550, step: 28 }, opts || {});
    var pointers = {}, count = 0, mode = "idle", start = null, timer = null, last = null, acc = 0, lastY = 0;

    function avg() {
      var x = 0, y = 0, n = 0, id;
      for (id in pointers) { x += pointers[id].x; y += pointers[id].y; n++; }
      return { x: x / n, y: y / n };
    }
    function stopTimer() { if (timer != null) { timers.clear(timer); timer = null; } }

    function down(id, x, y) {
      pointers[id] = { x: x, y: y }; count++;
      if (count === 1) {
        mode = "pending"; start = { x: x, y: y }; last = start;
        stopTimer();
        timer = timers.set(function () { timer = null; if (mode === "pending") { mode = "held"; emit({ kind: "click", x: start.x, y: start.y, button: "right" }); } }, o.hold);
      } else if (count === 2) {
        stopTimer();
        if (mode === "drag") emit({ kind: "drag-end", x: last.x, y: last.y });
        mode = "scroll"; acc = 0; lastY = avg().y;
      }
    }
    function move(id, x, y) {
      if (!pointers[id]) return;
      pointers[id].x = x; pointers[id].y = y;
      if (mode === "pending") {
        if (Math.hypot(x - start.x, y - start.y) > o.slop) { stopTimer(); mode = "drag"; emit({ kind: "drag-start", x: start.x, y: start.y }); last = { x: x, y: y }; emit({ kind: "drag-move", x: x, y: y }); }
      } else if (mode === "drag") { last = { x: x, y: y }; emit({ kind: "drag-move", x: x, y: y }); }
      else if (mode === "scroll") {
        var a = avg();
        acc += a.y - lastY; lastY = a.y;
        var steps = acc > 0 ? Math.floor(acc / o.step) : Math.ceil(acc / o.step);
        if (steps !== 0) { acc -= steps * o.step; emit({ kind: "scroll", x: a.x, y: a.y, dy: steps }); }
      }
    }
    function release(id, x, y, cancelled) {
      if (!pointers[id]) return;
      delete pointers[id]; count--;
      if (count === 0) {
        stopTimer();
        if (mode === "pending" && !cancelled) emit({ kind: "click", x: start.x, y: start.y, button: "left" });
        else if (mode === "drag") emit({ kind: "drag-end", x: x, y: y });
        mode = "idle";
      } else if (count === 1 && mode === "scroll") mode = "consumed";
    }
    function reset() { stopTimer(); pointers = {}; count = 0; mode = "idle"; }
    return { down: down, move: move, up: function (id, x, y) { release(id, x, y, false); }, cancel: function (id, x, y) { release(id, x, y, true); }, reset: reset, mode: function () { return mode; } };
  }

  /* ---------------------------------------------------------------- a computer's keyboard and wheel  */

  var KEYNAMES = { Enter: "enter", Backspace: "backspace", Tab: "tab", Escape: "esc", Delete: "delete", Home: "home", End: "end", PageUp: "pageup", PageDown: "pagedown",
                   ArrowLeft: "left", ArrowUp: "up", ArrowRight: "right", ArrowDown: "down", " ": "space", Insert: "insert", PrintScreen: "printscreen", Pause: "pause",
                   CapsLock: "capslock", ContextMenu: "menu" };
  /* A key pressed on this computer, as the event the PC understands (or null for a key that does nothing alone, like Shift). A plain letter or symbol is TYPED (so capitals and
     symbols come out as pressed, whatever the PC's layout); with Ctrl, Alt or the Windows key it is a shortcut: the key and its modifiers go down and up together. */
  function mapKey(e) {
    var k = e && e.key;
    if (!k || k === "Control" || k === "Shift" || k === "Alt" || k === "Meta" || k === "AltGraph" || k === "OS" || k === "Dead" || k === "Process" || k === "Unidentified" || k === "Compose") return null;
    var altgr = !!(e.getModifierState && e.getModifierState("AltGraph"));   // Windows reports AltGr as Ctrl+Alt: those keys type characters
    var mods = [];
    if (e.ctrlKey && !altgr) mods.push("ctrl");
    if (e.altKey && !altgr) mods.push("alt");
    if (e.metaKey) mods.push("win");
    var printable = k.length === 1 && k !== " ";
    if (printable && !mods.length) return { t: "text", text: k };
    var key = KEYNAMES[k];
    if (!key && /^F([1-9]|1[0-2])$/.test(k)) key = k.toLowerCase();
    if (!key && k.length === 1) key = k.toLowerCase();
    if (!key) return null;
    if (e.shiftKey) mods.push("shift");
    return { t: "key", key: key, mods: mods };
  }
  /* A turn of the wheel (or a touchpad's small slides) in whole notches: `acc` carries what has not made a notch yet. Positive notches scroll UP, as the PC expects. */
  function wheelStep(acc, deltaY, deltaMode) {
    var px = deltaMode === 1 ? deltaY * 40 : deltaMode === 2 ? deltaY * 400 : deltaY;
    var a = acc + px, n = a < 0 ? Math.ceil(a / 100) : Math.floor(a / 100);
    if (n !== 0) a -= n * 100;
    n = Math.max(-10, Math.min(10, n));
    return { acc: a, notches: -n };
  }

  /* ---------------------------------------------------------------- the component  */

  function store(key, fallback) { try { var v = localStorage.getItem(key); return v === null ? fallback : v; } catch (e) { return fallback; } }
  function keep(key, value) { try { localStorage.setItem(key, String(value)); } catch (e) { /* private mode: it is just not remembered */ } }

  function create(d) {
    var h = d.h, ico = d.ico;
    var st = { view: null, sheet: null, body: null, poll: 0, loop: 0, note: "", quality: "auto", auto: { i: 3, good: 0, at: 0 }, ms: 0, win: Object.assign({}, FULL_WIN), imgWin: Object.assign({}, FULL_WIN), imgR: null, isFull: true, f: FRAME0, scrAR: 0, animUntil: 0, leftAt: 0, picSeq: 0, warned: false, holding: false, snd: null, vol: Math.min(1, Math.max(0, isNaN(parseFloat(store("afp_scr_vol", "0.8"))) ? 0.8 : parseFloat(store("afp_scr_vol", "0.8")))), clipTab: false, pinching: false, wake: null, pan: null, pinch: null, url: "", frames: 0, since: 0, bytes: 0,
               problem: "", autoStart: false, busy: false, chain: Promise.resolve(), full: false, home: null, native: false, ctlTimer: 0, sig: "", control: false, kb: false, mods: {},
               queue: [], flushTimer: 0, nat: null, ptr: { x: 0.5, y: 0.5 }, hdr: null, lastMove: 0, curDirty: false, pad: false, pop: "", padTimer: 0,
               pmode: store("afp_scr_pmode", "trackpad") === "touch" ? "touch" : "trackpad", speed: Math.min(2, Math.max(0.6, parseFloat(store("afp_scr_speed", "1")) || 1)),
               fit: store("afp_scr_fit", "fit") === "fill" ? "fill" : "fit", cursor: Math.min(2.4, Math.max(0.6, parseFloat(store("afp_scr_cursor", "1")) || 1)), track: null, padTrack: null, gestures: null, ring: null, tick: 0 };
    var timers = { set: function (fn, ms) { return setTimeout(fn, ms); }, clear: function (id) { clearTimeout(id); } };

    function callJson(method, path, body) {
      return d.api("/screen" + path, method === "POST" ? { method: "POST", body: JSON.stringify(body || {}) } : {}).then(function (r) { return r.json(); });
    }

    /* The sheet is redrawn only when something it SHOWS has changed (a new state, a new viewer); the words that merely count (time left) are updated in place.
       Rebuilding on every check made the whole screen blink and threw away what was being typed. */
    function setView(v) {
      st.view = v; st.leftAt = Date.now();
      var wasControl = st.control;
      st.control = !!(v && v.control && v.control.on);
      if (wasControl && !st.control && v && v.state === "viewing") {   // control was taken away on the PC (its Stop, its panic key, or the time ran out)
        stopControlLocal(); d.haptic("warning");
        var blocked = v.control && v.control.blocked;
        d.toast(blocked > 0 ? "Control was cut on the PC with its panic key. You can ask again in about " + blocked + " seconds." : "Control ended on the PC.");
      }
      if (!st.body || !st.body.isConnected) return;
      var sig = JSON.stringify([v && v.state, v && v.viewer, v && v.trusted, st.busy]);
      if (sig !== st.sig || !st.body.firstChild) { st.sig = sig; render(); }
      else { patchText(); if (v && v.state === "viewing") { updateDock(); if (st.control && !wasControl) startControlLocal(); } }
      if (v && v.control && v.control.allowed && st.pop === "ctlwait") { st.pop = ""; renderPop(); d.haptic("success"); d.toast("Control allowed. Tap Control to start."); }   // the Owner said yes
    }
    function patchText() {
      var hl = headline(st.view), t = st.body.querySelector(".acc-title"), s = st.body.querySelector(".acc-sub");
      if (t && t.textContent !== hl.title) t.textContent = hl.title;
      if (s && s.textContent !== hl.sub) s.textContent = hl.sub;
    }

    function refresh() {
      return callJson("GET", "").then(function (v) {
        var was = st.view && st.view.state, first = st.autoStart;
        st.autoStart = false;
        if (first && v.state === "viewing" && was !== "viewing" && !(st.closedAt && Date.now() - st.closedAt < 4000)) st.resuming = true;   // (not when this screen was only just closed here: the PC may still be finishing that stop)   // the PC already had this phone looking (the app was closed meanwhile): say so while it loads
        setView(v);
        if (v.state === "granted" && (was === "pending" || first)) { d.haptic("success"); start(); }   // allowed, and that is what opening this screen asked for: begin at once
        if (was === "viewing" && v.state !== "viewing") { endLoop(); exitFull(); stopControlLocal(); d.haptic("warning"); d.toast(v.state === "granted" || v.state === "none" ? "Looking was stopped on the PC." : "Looking ended."); }  // it ended on the PC (Stop, a time limit, Pause, its panic key)
        return v;
      }).catch(function (e) { if (e && e.code === 401) throw e; });
    }

    function act(path, body) {
      if (st.busy) return Promise.resolve();
      st.busy = true; st.sig = "";
      return callJson("POST", path, body).then(setView).catch(function (e) {
        if (e && e.code === 401) return;
        d.toast((e && e.message) || "That didn't work."); d.haptic("error"); refresh();
      }).then(function () { st.busy = false; st.sig = ""; if (st.view) setView(st.view); });
    }

    function start() {
      return act("/start").then(function () { if (st.view && st.view.state === "viewing") beginLoop(); });
    }

    /* ---- the live picture ---- */
    function currentCfg() {
      var p = preset(st.quality);
      if (st.settled) return { w: PEEK.w, q: PEEK.q, gap: PEEK.gap, peek: true };    // the sharp picture is up: only look whether anything changed
      if (st.still >= 2) return { w: REFINE.w, q: REFINE.q, gap: 0, refine: true };  // nothing moved for two pictures: one extra-sharp one
      return st.quality === "auto" ? { w: LADDER[st.auto.i].w, q: LADDER[st.auto.i].q, gap: p.gap } : p;
    }
    /* Something happened that the picture should follow (a touch, a key, the view moving, the PC changing): back to ordinary pictures at once. */
    function busy() { return st.control && Date.now() < (st.busyUntil || 0); }
    function unsettle() {
      if (!st.settled && !st.still) return;
      st.settled = false; st.still = 0;
      if (st.wake) st.wake();
    }
    function beginLoop() {
      endLoop();
      st.frames = 0; st.bytes = 0; st.since = Date.now(); st.problem = ""; st.ms = 0; st.booted = false; st.still = 0; st.settled = false;   // a new viewing shows its loading screen until the first picture is there
      st.win = Object.assign({}, FULL_WIN); st.imgWin = Object.assign({}, FULL_WIN); st.imgR = null; st.isFull = true; st.scrAR = 0; st.f = FRAME0;   // a new viewing starts zoomed out
      /* Pictures are asked for in a pipeline: while one is on its way the next is already asked for (two on a good link, three on a slow one), so waiting for an answer never leaves
         the PC idle. They can come back out of order; one older than what is already on screen is dropped. This was one at a time, which on any link with a delay of its own capped
         the rate near 7 a second whatever the PC could do. */
      var token = ++st.loop, timer = 0, inflight = 0, reqSeq = 0, shownReq = 0;
      var depth = function () { return st.settled ? 1 : busy() ? 2 : st.ms > 220 ? 3 : 2; };
      st.wake = function () { if (inflight < depth() && timer) { clearTimeout(timer); timer = 0; next(); } };   // the view moved: ask for the sharp picture now, not after the usual wait
      function later(ms) { clearTimeout(timer); timer = setTimeout(function () { timer = 0; next(); }, ms); }
      function next() {
        if (token !== st.loop || !st.body || !st.body.isConnected) return;
        if (document.hidden) { later(600); return; }                                       // the screen is off or the app is in the background: stop asking
        if ((st.pinching || st.pan) && Date.now() - (st.gestureAt || 0) < 1500) { later(120); return; }                                 // fingers are moving the view: swapping pictures under them makes it flicker; the sharp one comes when they let go
        var cfg = currentCfg(), began = Date.now(), asked = Object.assign({}, st.win);
        var R = seenRect(asked, st.f), whole = R.x0 < 0.001 && R.y0 < 0.001 && R.x1 > 0.999 && R.y1 > 0.999;
        var region = whole ? "" : "&rx=" + R.x0.toFixed(5) + "&ry=" + R.y0.toFixed(5) + "&rw=" + (R.x1 - R.x0).toFixed(5) + "&rh=" + (R.y1 - R.y0).toFixed(5);   // zoomed (or Fill): just the part the box shows, at full detail
        inflight += 1;
        var mineReq = ++reqSeq, pipe = inflight;
        if (inflight < depth()) later(12);   // the next picture is asked for right behind this one
        // while controlling, the phone draws its own pointer (it follows the finger at once), so the picture comes without the PC's
        d.api("/screen/frame?w=" + cfg.w + "&q=" + cfg.q + "&pointer=" + (st.control ? 0 : 1) + "&ps=" + st.cursor.toFixed(1) + region).then(function (r) {
          var at = r.headers && r.headers.get("X-Pointer"), shape = r.headers ? r.headers.get("X-Cursor") : null;   // shape: which pointer the PC is showing right now
          var still = !!(r.headers && r.headers.get("X-Still") === "1");   // the PC's screen has not changed since its last picture
          return r.blob().then(function (blob) { return { blob: blob, at: at, shape: shape, still: still }; });
        }).then(function (got) {
          if (token !== st.loop) return;
          if (cfg.peek) {   // only a look-in: unchanged means the sharp picture stays; changed means ordinary pictures again, right now (this tiny one is thrown away)
            inflight = Math.max(0, inflight - 1);
            if (got.still && st.settled) { later(cfg.gap); return; }
            st.settled = false; st.still = 0; later(0); return;
          }
          if (mineReq < shownReq) { inflight = Math.max(0, inflight - 1); later(0); return; }   // an older picture that arrived after a newer one: of no use
          shownReq = mineReq;
          var blob = got.blob, url = URL.createObjectURL(blob), img = currentImg(), mine = ++st.picSeq;
          if (img) {
            var apply = function () {
              var im = currentImg(); if (!im || token !== st.loop) return;
              if (mine !== st.picSeq) { URL.revokeObjectURL(url); return; }   // a newer picture came while this one waited for a glide to end: never put an older one on top
              im.onload = function () {
                if (st.url) URL.revokeObjectURL(st.url);
                st.url = url; st.nat = { w: im.naturalWidth, h: im.naturalHeight }; im.onload = null;
                st.imgWin = asked; st.imgR = R;   // this picture shows exactly the part that was asked for; the view may have moved on since
                var live = currentView();
                if (whole && st.nat.w > 0 && st.nat.h > 0) {   // the whole screen: its shape is the PC screen's own shape
                  st.scrAR = st.nat.w / st.nat.h;
                  if (live) { live.style.setProperty("--ar", st.nat.w + " / " + st.nat.h); live.style.setProperty("--arn", String(st.scrAR)); }
                }
                var G = geom(); if (G) paintZoom(G, false);   // re-fit in this same frame: a frame with the new picture under the old zoom would flash
                placeCursor();
                if (token === st.loop && !st.booted) { st.booted = true; endBoot(); paintStatus(); }   /* (not for a picture of a viewing that was stopped meanwhile: it would make the next viewing skip its loading scene) */   // the first picture is here: the loading screen melts into it
              };
              im.src = url;
            };
            var wait = st.animUntil - Date.now();   // a zoom is still gliding: the sharp picture comes when it has arrived, so nothing jumps
            if (wait > 0) setTimeout(apply, wait); else apply();
          }
          if (got.at) {
            var parts = got.at.split(",").map(parseFloat);
            if (parts.length === 2 && isFinite(parts[0]) && isFinite(parts[1])) {
              st.hdr = { x: clamp01(parts[0]), y: clamp01(parts[1]) };
              if (st.control && Date.now() - st.lastMove > 900) { st.ptr = { x: st.hdr.x, y: st.hdr.y }; placeCursor(); }   // someone moved the PC's own mouse: follow it
            }
          }
          setShape(got.shape || "");   // while controlling, the phone's own pointer takes the PC pointer's shape (no word from the PC = unknown = the ordinary arrow)
          var ms = (Date.now() - began) / pipe;   // with pictures queued behind each other, how long one took to come is its share of the wait
          st.frames += 1; st.bytes = blob.size; st.problem = "";
          if (cfg.refine) { st.settled = true; st.still = 0; }                                  // the sharp picture is up: rest, and only look in now and then
          else {
            st.still = got.still ? st.still + 1 : 0;
            st.ms = st.ms ? st.ms * 0.7 + ms * 0.3 : ms;
            if (st.quality === "auto") { st.auto = adapt(st.auto, { ms: ms, now: Date.now(), live: busy() }); updateDock(); }   // (the big sharp picture would look like a slow link)
          }
          paintStatus();
          inflight = Math.max(0, inflight - 1); later(sameWin(asked, st.win) ? Math.max(16, cfg.gap - ms) : 0);   // never faster than about 55 a second (a fast PC would only keep the phone busy for nothing); the view moved while this was coming: ask again at once
        }).catch(function (e) {
          inflight = Math.max(0, inflight - 1);
          if (token !== st.loop) return;
          if (e && e.code === 401) return;
          if (e && e.status === 403) { endLoop(); refresh(); return; }                    // not looking any more (stopped, expired, paused)
          st.problem = (e && e.message) || "The picture stopped coming. Trying again…";
          paintStatus();
          later(2000);                                                                    // a locked PC or a bad moment on the network: keep trying calmly
        });
      }
      next();
    }
    function endLoop() { stopSound(); st.loop += 1; st.booted = false; if (st.url) { URL.revokeObjectURL(st.url); st.url = ""; } }   // (booted = false: the next viewing, even a second one in the same session, gets its loading screen again)
    /* The loading screen over the picture until the first picture arrives: a little monitor being scanned, rings spreading from it, and a line that says what is going on
       (and, if the PC is slow or locked, says so). It melts into the picture instead of cutting to it. */
    function buildBoot() {
      var resume = !!st.resuming;
      var sub = h("small", { class: "scr-boot-sub", text: resume ? "Looking from where you left off. Please wait…" : "Reaching your PC…" });
      var el = h("div", { class: "scr-boot" + (resume ? " resume" : ""), role: "status", "aria-live": "polite" },
        h("span", { class: "scr-boot-ring", "aria-hidden": "true" }), h("span", { class: "scr-boot-ring r2", "aria-hidden": "true" }),
        h("div", { class: "scr-boot-mon", "aria-hidden": "true" }, h("i", { class: "scr-boot-line" })),
        resume ? h("span", { class: "scr-boot-tag" }, ico("retry"), "Resuming") : null,
        h("b", { class: "scr-boot-title", text: resume ? "Picking up your previous session" : "Connecting to your PC" }), sub,
        h("div", { class: "scr-boot-bar", "aria-hidden": "true" }, h("i")));
      var began = Date.now();
      var tick = setInterval(function () {
        if (!el.isConnected) { if (Date.now() - began > 1500) clearInterval(tick); return; }
        var s = (Date.now() - began) / 1000;
        var text = st.problem ? st.problem
          : s < 2.5 ? (resume ? "Looking from where you left off. Please wait…" : "Reaching your PC…")
          : s < 8 ? (resume ? "Reconnecting to your PC's screen…" : "Getting the first picture…")
          : "Taking longer than usual. The PC may be waking up, or locked.";
        if (sub.textContent !== text) sub.textContent = text;
      }, 400);
      return el;
    }
    function endBoot() {
      st.resuming = false;
      var boot = document.querySelector(".scr-boot"), img = currentImg();
      if (img) { img.classList.remove("scr-wait"); img.classList.add("scr-reveal"); setTimeout(function () { img.classList.remove("scr-reveal"); }, 900); }
      if (boot) { boot.classList.add("done"); setTimeout(function () { boot.remove(); }, 700); }
    }
    function currentImg() { return document.querySelector(".scr-view .scr-img"); }
    function currentView() { return document.querySelector(".scr-view"); }

    /* The one line above the picture: what is happening, how fast, how big, how the network is. */
    function paintStatus() {
      var el = st.body && st.body.querySelector(".scr-top");
      if (!el) return;
      var secs = Math.max(1, (Date.now() - st.since) / 1000), net = st.ms ? networkWord(st.ms) : "";
      var bars = st.ms < 200 ? 3 : st.ms < 450 ? 2 : 1;
      el.dataset.state = st.problem ? "problem" : st.control ? "control" : "live";
      var booting = !st.booted && !st.problem && !st.control;   // before the first picture: the loading screen says it; this line just says "Connecting"
      el.replaceChildren.apply(el, [
        h("span", { class: "scr-dot", "aria-hidden": "true" }),
        h("b", { class: "scr-state", text: st.problem ? "Reconnecting" : st.control ? "Controlling" : booting ? (st.resuming ? "Resuming" : "Connecting") : "Live" }),
        booting ? null : st.problem ? h("span", { class: "scr-meta", text: st.problem }) : h("span", { class: "scr-meta", text: (st.frames / secs).toFixed(0) + " fps · " + Math.round(st.bytes / 1024) + " KB" + (st.quality === "auto" ? " · Auto " + LADDER[st.auto.i].w : "") + (st.control && st.inMs ? " · mouse " + Math.round(st.inMs) + " ms" : "") }),
        net ? h("span", { class: "scr-net", "data-bars": String(bars), "aria-label": "Network " + net.toLowerCase(), title: "Network " + net.toLowerCase() }, h("i"), h("i"), h("i")) : null].filter(Boolean));
    }

    /* ---- the screen ---- */
    function open(opts) {
      if (st.sheet && st.body && st.body.isConnected) return;
      st.body = h("div", { class: "scr" });
      st.autoStart = true; st.sig = ""; st.pop = "";
      st.resuming = !!(opts && opts.resume);   // opened by the app itself to pick up the viewing the PC is still holding: the loading scene says so
      render();
      st.sheet = d.openSheet({ title: "PC screen", body: st.body, full: true, route: "screen", onClose: close });
      refresh();
      clearInterval(st.poll);
      clearInterval(st.limitTimer); st.limitTimer = setInterval(paintLimit, 1000);
      st.poll = setInterval(function () {
        if (!st.body || !st.body.isConnected) { clearInterval(st.poll); clearInterval(st.limitTimer); return; }
        var looking = st.view && st.view.state === "viewing" && st.loop, waiting = st.view && st.view.control && st.view.control.pending;
        st.tick += 1;
        if (!document.hidden && (!looking || waiting || st.tick % 4 === 0)) refresh();   // while looking, the pictures tell us it is alive; a slow check still notices a change on the PC (control allowed, Stop)
      }, 1500);
    }

    function close() {
      clearInterval(st.poll); clearInterval(st.limitTimer); endLoop(); exitFull(); st.closedAt = Date.now();
      var was = st.view && st.view.state, asking = st.view && st.view.control && st.view.control.pending;
      stopControlLocal(); st.sheet = null; st.pop = "";
      if (was === "viewing") callJson("POST", "/stop").catch(function () { /* the PC ends it by itself when the pictures stop being asked for */ });  // leaving ends the viewing: nothing is left running
      if (was === "pending" || asking) callJson("POST", "/cancel").catch(function () { /* it times out on the PC */ });
    }

    function render() {
      var body = st.body, v = st.view, hl = headline(v);
      if (v && v.state === "viewing") { renderLive(body); return; }
      if (!v) { body.replaceChildren(h("div", { class: "scr-loadpanel" }, buildBoot())); return; }   // before the PC has answered: the loading scene, not a card saying "Checking…"
      st.resuming = false;
      if (body.querySelector(".scr-view")) exitFull();
      var parts = [];
      parts.push(h("section", { class: "acc-hero scr-hero", "data-kind": hl.kind === "none" || hl.kind === "granted" ? "free" : hl.kind === "pending" || hl.kind === "busy" ? "waiting" : hl.kind },
        h("div", { class: "acc-orb", "aria-hidden": "true" }, hl.kind === "pending" ? h("span", { class: "spin big" }) : ico(hl.kind === "off" ? "lock" : "eye", 34)),
        h("h2", { class: "acc-title", text: hl.title }), h("p", { class: "acc-sub", text: hl.sub })));
      var actions = h("div", { class: "acc-actions" });
      if (v && v.state === "none") {
        var input = h("input", { class: "text-input acc-msg", type: "text", maxlength: "120", placeholder: "Add a note for the PC owner (optional)", "aria-label": "Note for the PC owner", value: st.note });
        input.addEventListener("input", function () { st.note = input.value; });
        var ask = h("button", { class: "btn primary block", type: "button", disabled: st.busy }, ico("eye"), "Ask to view the screen");
        ask.onclick = function () { d.haptic("medium"); var note = st.note; st.note = ""; act("/request", { note: note }); };
        actions.append(input, ask);
      } else if (v && v.state === "pending") {
        var cancel = h("button", { class: "btn ghost block", type: "button", disabled: st.busy }, "Cancel my request");
        cancel.onclick = function () { d.haptic("light"); act("/cancel"); };
        actions.append(cancel);
      } else if (v && v.state === "granted") {
        var go = h("button", { class: "btn primary block", type: "button", disabled: st.busy }, ico("play"), "Start viewing");
        go.onclick = function () { d.haptic("medium"); start(); };
        actions.append(go);
      } else if (v && v.state === "busy") {
        var again = h("button", { class: "btn block", type: "button" }, ico("retry"), "Check again");
        again.onclick = function () { d.haptic("tick"); refresh(); };
        actions.append(again);
      }
      parts.push(actions);
      parts.push(h("ul", { class: "scr-facts" },
        h("li", {}, ico("eye"), h("span", { text: "Looking is the default. Controlling the PC (mouse and keyboard) is a separate switch, and needs Operate level, your turn, and the PC owner's yes." })),
        h("li", {}, ico("shield"), h("span", { text: "The PC shows a banner while you look, with a Stop button, and the owner can end it any time." })),
        h("li", {}, ico("lock"), h("span", { text: "Anything on the screen can be seen, including private windows. A locked PC shows nothing." }))));
      body.replaceChildren.apply(body, parts);
    }

    /* ---------------------------------------------------------------- the live view  */

    function dockButton(cls, label, fn, aria) {
      var b = h("button", { class: "scr-d " + cls, type: "button", "aria-label": aria || label });
      b.onclick = function () { fn(b); if (st.full) closeMenu(); };   // in full screen the menu closes after each choice
      b.dataset.label = label;
      return b;
    }

    function renderLive(body) {
      if (body.querySelector(".scr-view") || currentView()) { updateDock(); return; }
      var img = h("img", { class: "scr-img", alt: "Live picture of the PC's screen", draggable: "false" });
      var cursor = h("div", { class: "scr-cursor", "aria-hidden": "true", hidden: true });
      st.shapeId = undefined;
      cursor.innerHTML = '<svg viewBox="0 0 24 24" width="26" height="26" focusable="false"><path d="M3 2l15 9.5-6.3 1.4 3.8 7.2-3 1.6-3.8-7.3L3 19z" fill="#fff" stroke="#111" stroke-width="1.4" stroke-linejoin="round"/></svg>';
      var keychip = h("button", { class: "scr-keychip", type: "button", hidden: true, "aria-label": "Your keyboard is typing on the PC. Tap to give it back." }, ico("keyboard"), h("span", { text: "Typing on the PC" }));
      keychip.onclick = function () { d.haptic("tick"); releaseKeys(); };
      var chip = h("button", { class: "scr-zoomchip", type: "button", hidden: true, "aria-label": "Zoom back out to the whole screen" });
      chip.onclick = function (e) { e.stopPropagation(); d.haptic("tick"); zoomTo(1, null, null, true); };
      var limit = h("div", { class: "scr-limit", hidden: true, role: "status" });
      /* Two small round buttons: a download icon (arms the picture) and a "?" (shows a short live demo of how to get a file). Icons only, so nothing sits over the PC's screen but two dots. */
      var pickBtn = h("button", { class: "scr-pickbtn", type: "button", title: "Download a file or folder from this screen", "aria-label": "Download a file or folder from this screen", "aria-pressed": "false" }, ico("download"));
            pickBtn.onclick = function (e) { e.stopPropagation(); d.haptic("tick"); setPick(!st.pick); };   // arms the picture: the next touch on a file picks it (the lit icon and the glowing frame say so; nothing is added over the picture)
      var helpBtn = h("button", { class: "scr-pickhelp", type: "button", title: "What is this?", "aria-label": "What is the download button?", text: "?" });
      helpBtn.onclick = function (e) { e.stopPropagation(); d.haptic("tick"); showDemo(); };   // the "?" is where the demo lives
      var pickGroup = h("div", { class: "scr-pickgrp" }, pickBtn, helpBtn);
      var stage = h("div", { class: "scr-stage" + (st.control ? " ctrl" : "") + (wantsTouch() ? " grab" : "") + (st.pick ? " picking" : ""), tabindex: "0", "aria-label": "The PC's screen" }, img, cursor, chip, keychip, limit, pickGroup, st.booted ? null : buildBoot());
      if (!st.booted) img.classList.add("scr-wait");
      var lastTap = 0;
      stage.addEventListener("click", function (e) {
        if (st.control) return;                                                          // while controlling, taps belong to the PC
        if (e.target.closest && e.target.closest(".scr-zoomchip, .scr-pickgrp")) return;
        var now = Date.now();
        if (now - (st.gestureAt || 0) < 500) { lastTap = 0; return; }                  // the end of a pinch or drag is not a tap
        if (now - lastTap < 320) { lastTap = 0; zoomTap(e.clientX, e.clientY); } else { lastTap = now; if (st.full) closeMenu(); }   // a double tap zooms in there, or back out
      });
      bindGrab(stage);   // before the other touch handling, so a pick or a long press on a file is seen first
      bindStage(stage);
      var dock = h("div", { class: "scr-dock", role: "toolbar", "aria-label": "Screen controls" },
        dockButton("stop", "Stop", stopAll, "Stop looking"),
        dockButton("ctl", "Control", controlTap, "Control the PC"),
        dockButton("keys", "Keys", toggleKeyboard, "Keyboard"),
        dockButton("pad", "Pad", togglePad, "Mouse pad"),
        dockButton("opts", "Options", toggleOptions, "Options"),
        dockButton("fit", "Fill", toggleFit, "Fill or fit the picture"),
        dockButton("zoom", "Zoom", function () { zoomTap(); }, "Zoom in or out"),
        dockButton("full", "Full", function () { st.autoFull = false; if (st.full) { st.noAuto = window.innerWidth > window.innerHeight; exitFull(); } else enterFull(); }, "Full screen"));
      var burger = h("button", { class: "scr-burger", type: "button", "aria-label": "Open the menu", "aria-expanded": "false" });
      burger.onclick = function () { d.haptic("tick"); toggleMenu(); };
      var view = h("div", { class: "scr-view" }, stage, buildPad(), buildKeyboard(), buildCoach(), dock, h("div", { class: "scr-pop", hidden: true }), burger);
      body.replaceChildren(h("div", { class: "scr-top", role: "status", "data-state": "live" }), view);
      updateDock();
      paintStatus();
      if (!st.loop) beginLoop();
    }

    /* How long this phone may still look (and control): the PC owner gave a time, and it is shown, so it never ends as a surprise. The Admin phone has no limit. */
    function leftNow() {
      var v = st.view;
      if (!v || v.state !== "viewing" || v.left == null || v.trusted || v.forever) return null;
      return Math.max(0, v.left - (Date.now() - st.leftAt) / 1000);
    }
    function paintLimit() {
      var chip = document.querySelector(".scr-limit"); if (!chip) return;
      var left = leftNow();
      if (left == null) { chip.hidden = true; st.warned = false; return; }
      var m = Math.floor(left / 60), sec = Math.floor(left % 60);
      chip.hidden = false;
      chip.textContent = (st.control ? "Control" : "Looking") + " · " + (left >= 90 ? Math.ceil(left / 60) + " min" : m + ":" + (sec < 10 ? "0" : "") + sec) + " left";
      chip.classList.toggle("warn", left < 180); chip.classList.toggle("alert", left < 60);
      if (left < 60 && !st.warned) { st.warned = true; d.haptic("warning"); d.toast("Less than a minute left. Ask the PC owner for more time on the PC."); }
      if (left > 90) st.warned = false;
      if (left <= 0 && !st.busy) refresh();
    }

    function stopAll() { d.haptic("medium"); exitFull(); endLoop(); stopControlLocal(); act("/stop"); }

    /* The row of buttons under the picture (a floating bar in full screen). Each is an icon over a short label, so nothing is ever cut off. */
    function updateDock() {
      var v = st.view, can = !!(v && v.control && v.control.allowed), pending = !!(v && v.control && v.control.pending), ctrl = st.control;
      var set = function (sel, icon, label, on, disabled, pressed) {
        document.querySelectorAll(".scr-dock " + sel).forEach(function (b) {
          if (b.dataset.shown !== icon + "|" + label) { b.replaceChildren(ico(icon), h("span", { text: label })); b.dataset.shown = icon + "|" + label; }
          b.classList.toggle("on", !!on); b.disabled = !!disabled;
          if (pressed !== undefined) b.setAttribute("aria-pressed", String(!!pressed));
        });
      };
      set(".stop", "stop", "Stop", false, false);
      set(".ctl", ctrl ? "pointer" : pending ? "clock" : can ? "pointer" : "lock", pending && !ctrl ? "Waiting" : !ctrl && !can ? "Ask" : "Control", ctrl, false, ctrl);
      set(".keys", "keyboard", "Keys", st.kb && ctrl, !ctrl, st.kb && ctrl);
      set(".pad", "pad", "Pad", st.pad && ctrl, !ctrl, st.pad && ctrl);
      set(".opts", "sliders", "Options", st.pop === "opts", false, st.pop === "opts");
      set(".full", st.full ? "shrink" : "expand", st.full ? "Exit" : "Full", false, false);
      set(".fit", "image", st.fit === "fill" ? "Fit" : "Fill", st.fit === "fill", false);
      set(".zoom", zoomed() ? "zoomout" : "zoomin", zoomed() ? "Reset" : "Zoom", zoomed(), false, zoomed());
      var burger = document.querySelector(".scr-burger"), lv = currentView();
      if (burger) {
        var open = !!(lv && lv.classList.contains("ctl-on")), key = (open ? "x" : "menu");
        if (burger.dataset.shown !== key) { burger.replaceChildren(ico(open ? "x" : "menu")); burger.dataset.shown = key; }
        burger.dataset.dot = ctrl ? "control" : pending ? "wait" : "";   // a coloured dot says control is on (or being asked for) even while the menu is closed
        burger.setAttribute("aria-expanded", String(open)); burger.setAttribute("aria-label", open ? "Close the menu" : "Open the menu");
      }
      document.querySelectorAll(".scr-dock .ctl").forEach(function (b) { b.classList.toggle("ask", !ctrl && !can && !pending); });
      var stage = document.querySelector(".scr-stage");
      if (stage) { stage.classList.toggle("ctrl", ctrl); stage.classList.toggle("fill", st.fit === "fill"); stage.classList.toggle("grab", wantsTouch()); }
      var live = currentView(); if (live) live.style.setProperty("--cs", String(st.cursor));
      var cursor = document.querySelector(".scr-cursor");
      if (cursor) cursor.hidden = !ctrl;
      if (ctrl) placeCursor();
    }

    /* Zoom button or double tap: zoomed in -> back to the whole screen; otherwise close in (2.5 times) on the tapped place, the pointer while controlling, or the middle. */
    function zoomTap(cx, cy) {
      d.haptic("tick");
      if (zoomed()) { zoomTo(1, null, null, true); return; }
      var at = { x: 0.5, y: 0.5 };
      if (cx != null) { var G = geom(), q = G && pointToScreen(cx, cy, G.rect, G.nat, G.rot, G.cover); if (q) at = winToScreen(st.win, q.x, q.y, st.f); }
      else if (st.control) at = { x: st.ptr.x, y: st.ptr.y };
      var w = fullW(st.f) / 2.5, h = w * st.f.k;
      setWin({ x0: at.x - w / 2, y0: at.y - h / 2, w: w }, true, true);
    }

    /* ---------------------------------------------------------------- Control: asking, switching on and off  */

    function controlTap() {
      var v = st.view, can = !!(v && v.control && v.control.allowed), pending = !!(v && v.control && v.control.pending);
      d.haptic("tick");
      if (st.control) { toggleControl(); return; }
      if (can) { st.pop = ""; renderPop(); toggleControl(); return; }
      st.pop = pending ? "ctlwait" : "ctlask"; renderPop(); updateDock();
    }

    function askForControl() {
      callJson("POST", "/control/request").then(function (v) {
        st.view = Object.assign({}, st.view, v);
        st.pop = v.control && v.control.allowed ? "" : "ctlwait";
        renderPop(); updateDock();
        if (v.control && v.control.allowed) toggleControl();
        else d.toast("Asked. The PC owner sees a window with Allow and Deny.");
      }).catch(function (e) {
        if (e && e.code === 401) return;
        st.pop = ""; renderPop();
        d.toast((e && e.message) || "Couldn't ask for control."); d.haptic("error");
      });
    }

    function toggleControl() {
      var on = !st.control;
      d.haptic("medium");
      callJson("POST", "/control", { on: on }).then(function (v) {
        st.view = Object.assign({}, st.view, v); st.control = !!(v.control && v.control.on);
        if (st.control) startControlLocal(); else stopControlLocal();
        updateDock(); paintStatus();
      }).catch(function (e) {
        if (e && e.code === 401) return;
        d.toast((e && e.message) || "Couldn't take control."); d.haptic("error"); refresh();
      });
    }

    /* While a mouse button is held (a drag), a finger resting still sends nothing: a small "still here" keeps the PC from letting go (it lets go of anything held when it hears nothing for a few seconds, e.g. the connection dropped). */
    function heartbeat() { if (st.control && st.holding && !document.hidden) send([{ t: "move", x: st.ptr.x, y: st.ptr.y }], true); }
    document.addEventListener("visibilitychange", function () { if (document.hidden && st.control && st.holding) { st.holding = false; send([{ t: "up", x: st.ptr.x, y: st.ptr.y, button: "left" }], true); } });
    function startControlLocal() {
      clearInterval(st.beat); st.beat = setInterval(heartbeat, 1500);
      openSocket();
      st.ptr = st.hdr ? { x: st.hdr.x, y: st.hdr.y } : { x: 0.5, y: 0.5 };   // the phone's pointer starts exactly where the PC's is
      st.lastMove = 0;
      placeCursor(); burgerTouch(); updateDock();
      if (store("afp_scr_coach", "") !== "1") { var c = document.querySelector(".scr-coach"); if (c) c.hidden = false; }
    }
    function stopControlLocal() {
      clearInterval(st.beat); st.holding = false; closeSocket();
      st.control = false; st.kb = false; st.pad = false; st.queue = []; clearTimeout(st.flushTimer); st.flushTimer = 0;
      ["track", "padTrack", "gestures"].forEach(function (k) { if (st[k]) st[k].reset(); });
      removeRing(); syncKeyboard(); syncPad(); releaseKeys();
      var c = document.querySelector(".scr-coach"); if (c) c.hidden = true;
      updateDock();
    }

    /* ---------------------------------------------------------------- sending mouse and keyboard to the PC  */

    function send(events, now) {
      if (!st.control) return;
      events.forEach(function (e) { if (e.t === "down") st.holding = true; else if (e.t === "up" || e.t === "click") st.holding = false; });
      st.queue.push.apply(st.queue, events);
      st.busyUntil = Date.now() + BUSY_FOR;   // in use: pictures stay small until a moment after the last input
      unsettle();   // the person is doing something: the picture follows at full speed
      if (now) flush(); else if (!st.flushTimer) st.flushTimer = setTimeout(flush, 16);   // moves are gathered (about one batch a frame), so a drag is a stream of small batches, not a flood
    }
    function flush() {
      clearTimeout(st.flushTimer); st.flushTimer = 0;
      if (!st.queue.length) return;
      var batch = coalesce(st.queue.splice(0, st.queue.length)).slice(-40);
      // The fast lane (one open WebSocket) when it is up: in order, with no new request to set up and no queue behind the pictures.
      if (st.ws && st.wsReady && st.ws.readyState === 1) {
        try { st.ws.send(JSON.stringify({ events: batch, ts: Date.now() })); return; } catch (e) { /* the ordinary request below */ }
      }
      // One batch at a time, in order: two requests in flight can be handled by the PC in the other order (a release before its press).
      st.chain = st.chain.then(function () {
        var t0 = Date.now();
        return d.api("/screen/input", { method: "POST", body: JSON.stringify({ events: batch }) }).then(function () { st.inMs = st.inMs ? st.inMs * 0.8 + (Date.now() - t0) * 0.2 : Date.now() - t0; if (!st.inPaint) st.inPaint = setTimeout(function () { st.inPaint = 0; paintStatus(); }, 600); });
      }).catch(inputError);
    }
    function inputError(e) {
      if (e && e.code === 401) return;
      if (e && e.data && e.data.code === "session_held") { stopControlLocal(); d.toast(e.message || "Someone else has the turn, so control ended."); d.haptic("warning"); refresh(); }
      else if (e && e.status === 403) { stopControlLocal(); d.toast((e && e.message) || "Control ended."); refresh(); }
      /* anything else (a hiccup on the network, a refused event): the next touch carries on */
    }
    /* The fast lane. Opened when control starts; if it cannot be opened (a relay that does not pass WebSockets, an old PC) the ordinary requests carry on as before. */
    function lane(name) { var s = document.querySelector(".scr-stage"); if (s) s.dataset.lane = name; }   // "fast" or "request": which way the mouse and keys travel
    function openSocket() {
      if (st.ws || !st.control || typeof WebSocket === "undefined" || !d.token) return;
      var ws;
      try { ws = new WebSocket(location.origin.replace(/^http/, "ws") + "/api/screen/ws"); } catch (e) { return; }
      st.ws = ws; st.wsReady = false;
      var giveUp = setTimeout(function () { if (st.ws === ws && !st.wsReady) { try { ws.close(); } catch (e) { /* closing anyway */ } } }, 4000);
      ws.onopen = function () { try { ws.send(JSON.stringify({ token: d.token() })); } catch (e) { /* closed meanwhile */ } };
      ws.onmessage = function (m) {
        var msg; try { msg = JSON.parse(m.data); } catch (e) { return; }
        if (msg.ready) { clearTimeout(giveUp); st.chain = st.chain.then(function () { if (st.ws === ws) { st.wsReady = true; lane("fast"); } }); return; }   // after any request already sent: nothing overtakes it
        if (msg.ts) { var rtt = Date.now() - msg.ts; st.inMs = st.inMs ? st.inMs * 0.8 + rtt * 0.2 : rtt; if (!st.inPaint) st.inPaint = setTimeout(function () { st.inPaint = 0; paintStatus(); }, 600); }
        if (msg.error) inputError({ status: msg.status, code: msg.status, data: msg.error, message: msg.error.message });
      };
      ws.onclose = ws.onerror = function () { clearTimeout(giveUp); if (st.ws === ws) { st.ws = null; st.wsReady = false; lane("request"); } };
    }
    function closeSocket() { var ws = st.ws; st.ws = null; st.wsReady = false; lane("request"); if (ws) { try { ws.close(); } catch (e) { /* already closed */ } } }
    /* In a batch, a run of moves only needs its last position. */
    function coalesce(list) {
      var out = [];
      list.forEach(function (e) { var prev = out[out.length - 1]; if (e.t === "move" && prev && prev.t === "move") out[out.length - 1] = e; else out.push(e); });
      return out;
    }

    /* ---------------------------------------------------------------- the phone's own pointer, and where things are  */

    function geom() {
      var img = currentImg(); if (!img || !st.nat) return null;
      var view = img.closest(".scr-view"), rot = !!(view && view.classList.contains("rot")), stage = img.parentNode;
      var rect;
      if (!img.style.transform) rect = img.getBoundingClientRect();
      else {   // the picture is being scaled and moved while the sharp one comes: its box without that (position and size from the layout, which the transform does not touch)
        var r = stage.getBoundingClientRect(), inset = stage.clientLeft || 0, ow = img.offsetWidth, oh = img.offsetHeight, ol = img.offsetLeft, ot = img.offsetTop;
        rect = rot ? { top: r.top + inset + ol, height: ow, left: (r.right - inset - ot) - oh, width: oh } : { left: r.left + inset + ol, top: r.top + inset + ot, width: ow, height: oh };
      }
      var sz = layoutSize(rect, rot);
      if (!(sz.W > 0 && sz.H > 0)) return null;
      var k = (st.scrAR || st.nat.w / st.nat.h) / (sz.W / sz.H);
      st.f = { k: Math.abs(k - 1) < 0.01 ? 1 : k, fill: st.fit === "fill" };   // the screen's shape over the box's shape
      syncFull();
      var b = contentBox(sz.W, sz.H, st.nat, false);   // Fill is the window being smaller, so the picture itself is never cropped by the browser
      return { rect: rect, rot: rot, W: sz.W, H: sz.H, nat: st.nat, cw: b.cw, ch: b.ch, box: b, cover: false, f: st.f };
    }
    /* The box or Fit/Fill changed: zoomed out, the window follows (whole screen, or the whole box); zoomed in, it only has to stay inside the screen. */
    function syncFull() {
      var n = st.isFull ? fullWin(st.f) : clampWin(st.win, st.f);
      if (!sameWin(n, st.win)) { st.win = n; setTimeout(function () { if (st.wake) st.wake(); }, 0); }
    }
    /* Until the sharp picture of the new part arrives, the one on the phone is scaled and moved to look like it. */
    function paintZoom(g, animate) {
      var img = currentImg(); if (!img) return;
      if (animate == null && Date.now() < st.animUntil) return;   // a glide is under way: leave it alone
      var glide = "transform 300ms cubic-bezier(0.22, 0.8, 0.24, 1)";
      if (sameWin(st.imgWin, st.win)) { img.style.transition = animate ? glide : "none"; img.style.transform = ""; if (animate) st.animUntil = Date.now() + 320; else img.style.willChange = ""; return; }
      var t = windowTransform(st.imgR || { x0: 0, y0: 0, x1: 1, y1: 1 }, g.box, st.win, g.W, g.H, st.f);
      img.style.willChange = "transform";
      img.style.transition = animate ? glide : "none";
      img.style.transform = "translate(" + t.tx.toFixed(2) + "px," + t.ty.toFixed(2) + "px) scale(" + t.a.toFixed(5) + ")";
      if (animate) st.animUntil = Date.now() + 320;
    }
    function zoomed() { return st.win.w < fullW(st.f) - 0.001; }
    function zoomLevel() { return fullW(st.f) / st.win.w; }
    /* The view changed (pinch, drag, button, pointer reaching an edge): repaint at once; the PC is asked for the sharp picture of the new part. */
    function viewMoved(now, animate) {
      st.settled = false; st.still = 0;   // the view changed: ordinary pictures of the new part, not the rest
      var g = geom(); if (g) paintZoom(g, animate ? true : undefined);
      placeCursor(); paintZoomChip(); syncGrab();
      if (now && st.wake) st.wake();
    }
    function setWin(win, now, animate) { st.win = clampWin(win, st.f); st.isFull = st.win.w >= fullW(st.f) - 0.001; viewMoved(now, animate); }
    function refit() { viewMoved(true, true); }   // Fit or Fill changed: the window follows, with a glide
    function zoomTo(level, fx, fy, animate) {
      var z = Math.min(ZMAX, Math.max(1, level)), c = st.win, f = st.f;
      if (z <= 1.001) { setWin(fullWin(f), true, animate); return; }
      var ax = fx == null ? c.x0 + c.w / 2 : fx, ay = fy == null ? c.y0 + winH(c, f) / 2 : fy;
      setWin(zoomWin(c, z / zoomLevel(), ax, ay, f), true, animate);
    }
    function paintZoomChip() {
      var chip = document.querySelector(".scr-zoomchip"); if (!chip) return;
      chip.hidden = !zoomed();
      if (zoomed()) chip.textContent = zoomLevel().toFixed(1) + "×  ·  Reset";
      var slider = document.querySelector(".scr-range[aria-label=\"Zoom\"]");
      if (slider && document.activeElement !== slider) { slider.value = String(zoomLevel()); var v = document.querySelector(".scr-zoom-v"); if (v) v.textContent = zoomLevel().toFixed(1) + "×"; }
      updateDock();
    }
    /* The picture takes over touches (to pan and pinch) when zoomed, in full screen, or while controlling; otherwise the page scrolls over it as usual. */
    function wantsTouch() { return st.control || st.full || zoomed(); }
    function syncGrab() { var stage = document.querySelector(".scr-stage"); if (stage) stage.classList.toggle("grab", wantsTouch()); }
    /* The pointer on the PC changes with what is under it (a text bar over text, a hand over a link, resize arrows, a busy ring, an app's own shape). While controlling, the phone draws its own
       pointer (it follows the finger at once), so each picture says which shape the PC is showing, and when it changes the phone fetches that shape once and uses it. While only looking,
       the PC draws its real pointer into the picture, so there is nothing to do here. "none" = the PC has hidden its pointer; "" = unknown (the arrow stays). */
    function showShape(c, sh) {
      var img = h("img", { src: sh.url, alt: "", draggable: "false" });
      // sized with the same --cs as the arrow (the pointer-size slider in Options), so the slider still works with the real shape
      img.style.width = "calc(" + (sh.w * 0.85).toFixed(1) + "px * var(--cs, 1))"; img.style.marginLeft = "calc(" + (-sh.hx * 0.85).toFixed(2) + "px * var(--cs, 1))"; img.style.marginTop = "calc(" + (-sh.hy * 0.85).toFixed(2) + "px * var(--cs, 1))";
      c.replaceChildren(img);
    }
    function setShape(id) {
      if (id === st.shapeId) return;
      st.shapeId = id;
      var c = document.querySelector(".scr-cursor"); if (!c) return;
      if (!c.__arrow) c.__arrow = c.innerHTML;
      c.dataset.shape = id === "none" ? "none" : "";
      if (id === "none") return;
      if (!id) { c.innerHTML = c.__arrow; return; }
      st.shapes = st.shapes || {};
      if (st.shapes[id]) { showShape(c, st.shapes[id]); return; }
      d.api("/screen/cursor?id=" + encodeURIComponent(id)).then(function (r) {
        var hot = (r.headers.get("X-Hotspot") || "0,0").split(",").map(Number), size = (r.headers.get("X-Size") || "32,32").split(",").map(Number);
        return r.blob().then(function (blob) { return { url: URL.createObjectURL(blob), hx: hot[0] || 0, hy: hot[1] || 0, w: size[0] || 32 }; });
      }).then(function (sh) {
        st.shapes[id] = sh;
        var now = document.querySelector(".scr-cursor");
        if (st.shapeId === id && now) { if (!now.__arrow) now.__arrow = now.innerHTML; showShape(now, sh); }
      }).catch(function () { /* the arrow stays */ });
    }
    function placeCursor() {
      if (st.curDirty) return;
      st.curDirty = true;
      requestAnimationFrame(function () {
        st.curDirty = false;
        var c = document.querySelector(".scr-cursor"), g = geom();
        if (g) paintZoom(g);
        if (!c || !g || !st.control) return;
        var u = screenToWin(st.win, st.ptr.x, st.ptr.y, st.f), p = screenToLocal(u.u, u.v, g.W, g.H, g.nat, g.cover);
        c.style.visibility = u.u < -0.01 || u.u > 1.01 || u.v < -0.01 || u.v > 1.01 ? "hidden" : "";   // zoomed in on another part: the pointer is out of view
        if (p) c.style.transform = "translate(" + p.lx.toFixed(1) + "px," + p.ly.toFixed(1) + "px)";
      });
    }
    /* While zoomed in, the view follows the pointer when it comes near an edge, so it is never lost off the side. */
    function followPointer() {
      if (zoomed()) {
        var n = followWin(st.win, st.ptr.x, st.ptr.y, 0.12, st.f);
        if (!sameWin(n, st.win)) { setWin(n, true); return; }
      }
      placeCursor();
    }
    function ripple(stage, g) {
      var u = screenToWin(st.win, st.ptr.x, st.ptr.y, st.f), p = screenToLocal(u.u, u.v, g.W, g.H, g.nat, g.cover); if (!p) return;
      var r = h("span", { class: "scr-ripple", "aria-hidden": "true" });
      r.style.left = p.lx + "px"; r.style.top = p.ly + "px";
      stage.append(r);
      setTimeout(function () { r.remove(); }, 520);
    }
    function showRing(frame, rect, rot, cx, cy) {
      removeRing();
      var stage = document.querySelector(".scr-stage"); if (!stage) return;
      var p = clientToLocal(cx, cy, rect, rot);
      var ring = h("span", { class: "scr-ring", "aria-hidden": "true" });
      ring.innerHTML = '<svg viewBox="0 0 64 64" width="64" height="64"><circle cx="32" cy="32" r="27" fill="none" stroke="rgba(255,255,255,.25)" stroke-width="4"/><circle class="scr-ring-arc" cx="32" cy="32" r="27" fill="none" stroke="#22d3ee" stroke-width="4" stroke-linecap="round" transform="rotate(-90 32 32)"/></svg>';
      ring.style.left = p.lx + "px"; ring.style.top = p.ly + "px";
      stage.append(ring); st.ring = ring;
    }
    function removeRing() { if (st.ring) { st.ring.remove(); st.ring = null; } }

    /* ---------------------------------------------------------------- getting a file from the picture
       Long-press a file or folder on the picture (when only looking), or tap "Get a file" and then touch it (also while controlling, where a long press is a right-click).
       The PC says which file or folder is there, and a small card offers "Download to phone". A folder comes as one zip. */
    function sizeText(n) { return n < 1024 ? n + " B" : n < 1048576 ? (n / 1024).toFixed(n < 10240 ? 1 : 0) + " KB" : n < 1073741824 ? (n / 1048576).toFixed(1) + " MB" : (n / 1073741824).toFixed(2) + " GB"; }
    function closeGrab() { if (st.grabCard) { st.grabCard.remove(); st.grabCard = null; } st.grabSeq = (st.grabSeq || 0) + 1; }
    function setPick(on) {
      st.pick = !!on;
      var stage = document.querySelector(".scr-stage"), btn = document.querySelector(".scr-pickbtn");
      if (stage) stage.classList.toggle("picking", st.pick);
      if (btn) { btn.classList.toggle("on", st.pick); btn.setAttribute("aria-pressed", String(st.pick)); btn.replaceChildren(ico(st.pick ? "x" : "download")); }
      if (st.pick) closeGrab();
    }
    function grabCard(kids, tone) {
      var view = currentView(); if (!view) return null;
      closeGrab();
      var card = h("div", { class: "scr-grab" + (tone ? " " + tone : ""), role: "dialog", "aria-label": "Download from the PC's screen" }, kids);
      view.append(card); st.grabCard = card;
      return card;
    }
    function grabAt(cx, cy) {
      var G = geom(); if (!G) return;
      var q = pointToScreen(cx, cy, G.rect, G.nat, G.rot, G.cover);
      if (!q) return;                                                         // the touch was on a bar, not on the PC's screen
      var p = winToScreen(st.win, q.x, q.y, st.f), stage = document.querySelector(".scr-stage");
      st.gestureAt = Date.now();
      if (stage) { var dot = h("span", { class: "scr-ripple scr-grabdot", "aria-hidden": "true" }); dot.style.left = q.lx + "px"; dot.style.top = q.ly + "px"; stage.append(dot); setTimeout(function () { dot.remove(); }, 900); }
      d.haptic("medium");
      var seq = (st.grabSeq = (st.grabSeq || 0) + 1);
      var close = h("button", { class: "scr-grab-x", type: "button", "aria-label": "Close" }, ico("x")); close.onclick = function () { closeGrab(); };
      grabCard([h("div", { class: "scr-grab-row" }, h("span", { class: "spin" }), h("span", { class: "grow" }, h("b", { text: "Finding the file…" }), h("small", { text: "Looking at what is under your finger" })), close)], "busy");
      st.grabSeq = seq;
      d.api("/screen/item", { method: "POST", body: JSON.stringify({ x: p.x, y: p.y }) }).then(function (r) { return r.json(); }).then(function (out) {
        if (st.grabSeq !== seq) return;                                        // closed, or a newer touch, meanwhile
        grabResult(out);
      }).catch(function (e) {
        if (st.grabSeq !== seq) return;
        d.haptic("error");
        grabMessage("Couldn't check that", (e && e.message) || "The PC didn't answer. Try again.", "bad");
      });
    }
    /* The demo behind the "?": the real way to get a file, shown live in three steps that run by themselves (and can be tapped through): 1 press and hold a file, 2 allow the folder
       (the first time for a folder), 3 download. Each step is drawn with the same cards and buttons as the app, so what is shown is what is on the screen. With "reduce motion"
       nothing moves by itself: the steps are still, and the dots change them. It fits any size: below the picture on a normal phone, in two columns when the room is short (sideways). */
    function showDemo() {
      var controlling = !!st.control;
      var STEPS = [
        { title: "Press and hold", text: controlling ? "While you control the PC a long press is a right-click. Tap the download icon first, then tap the file once." : "Hold your finger on a file or folder on the PC's screen until a card appears." },
        { title: "Allow the folder", text: "The first time for a folder, tap Allow this folder, so this phone may take files from it. If it already may, this step is skipped." },
        { title: "Download", text: "Tap Download to phone. You see how far it has got, then Open when it is saved. A folder comes as one zip." }];
      var still = !!(window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches) || document.documentElement.dataset.motion === "off";
      var finger = function (ringToo) {
        var f = h("span", { class: "demo-finger", "aria-hidden": "true" });
        if (ringToo) {
          var ring = document.createElementNS("http://www.w3.org/2000/svg", "svg");
          ring.setAttribute("viewBox", "0 0 36 36"); ring.setAttribute("class", "demo-ring"); ring.setAttribute("aria-hidden", "true");
          ring.innerHTML = '<circle cx="18" cy="18" r="14" fill="none" stroke="rgba(255,255,255,.3)" stroke-width="3"/><circle class="arc" cx="18" cy="18" r="14" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" transform="rotate(-90 18 18)"/>';
          f.append(ring);
        }
        return f;
      };
      var head = function (sub) { return h("div", { class: "scr-grab-row" }, h("span", { class: "scr-grab-ico" }, ico("file")), h("span", { class: "grow" }, h("b", { text: "Report.xlsx" }), h("small", { text: sub }))); };
      var s1 = h("div", { class: "demo-scene demo-s1" },
        h("div", { class: "demo-desk" }, h("div", { class: "demo-file" }, ico("file", 20), h("small", { text: "Report.xlsx" })), h("div", { class: "demo-file two" }, ico("folder", 20), h("small", { text: "Trip" })), h("div", { class: "demo-taskbar" })),
        finger(true));
      var s2 = h("div", { class: "demo-scene demo-s2" },
        h("div", { class: "demo-mock" }, head("File"), h("p", { class: "demo-note", text: "This phone hasn't been given this folder yet." }), h("span", { class: "btn primary demo-btn allow" }, ico("folder"), "Allow this folder")), finger(false));
      var s3 = h("div", { class: "demo-scene demo-s3" },
        h("div", { class: "demo-mock" }, head("7 B"),
          h("span", { class: "btn primary demo-btn get" }, ico("download"), "Download to phone"),
          h("div", { class: "demo-prog" }, h("div", { class: "demo-track" }, h("i")), h("small", { text: "Saved to Downloads" }))), finger(false));
      var stage = h("div", { class: "demo-stage", "aria-hidden": "true" }, s1, s2, s3);
      var title = h("b", { class: "demo-title" }), text = h("p", { class: "demo-text" });
      var dots = STEPS.map(function (stp, k) {
        var b = h("button", { class: "demo-dot", type: "button", "aria-label": "Step " + (k + 1) + ": " + stp.title }, String(k + 1));
        b.onclick = function () { d.haptic("tick"); stop(); go(k + 1); };
        return b;
      });
      var got = h("button", { class: "btn primary demo-got", type: "button" }, ico("check"), "Got it");
      var root = null, sheet = null, timer = 0, step = 1;
      function stop() { clearInterval(timer); timer = 0; }
      function go(n) {
        step = n; root.dataset.step = String(n);
        title.textContent = n + " of 3 · " + STEPS[n - 1].title; text.textContent = STEPS[n - 1].text;
        dots.forEach(function (b, k) { b.classList.toggle("on", k === n - 1); if (k === n - 1) b.setAttribute("aria-current", "step"); else b.removeAttribute("aria-current"); });
      }
      got.onclick = function () { d.haptic("tick"); stop(); if (sheet) sheet.close(); };
      // a sheet like the app's others (room for all of it on any phone, scrolls if it ever must, turned the right way in a sideways full screen; see app.css for how sheets sit over the full-screen picture)
      root = h("div", { class: "scr-demo" + (still ? " still" : "") },
        h("div", { class: "demo-body" }, stage, h("div", { class: "demo-side" }, h("div", { class: "demo-caption", "aria-live": "polite" }, title, text), h("div", { class: "demo-dots", role: "group", "aria-label": "Steps" }, dots), got)));
      sheet = d.openSheet({ title: "Download from this screen", body: root, route: "demo", onClose: stop });
      go(1);
      if (!still) timer = setInterval(function () { if (!root.isConnected) { stop(); return; } go(step % 3 + 1); }, 3600);   // the next step by itself, round and round, until someone taps a number or closes it
    }
    function grabMessage(title, text, tone, action) {
      var close = h("button", { class: "scr-grab-x", type: "button", "aria-label": "Close" }, ico("x")); close.onclick = function () { closeGrab(); };
      var row = h("div", { class: "scr-grab-row" }, h("span", { class: "scr-grab-ico " + (tone || "") }, ico(tone === "bad" ? "alert" : "search")), h("span", { class: "grow" }, h("b", { text: title }), h("small", { text: text })), close);
      grabCard([row, action ? h("div", { class: "scr-grab-acts" }, action) : null].filter(Boolean), tone);
    }
    function grabResult(out) {
      var items = out.items || [];
      if (!items.length) {
        d.haptic("warning");
        var again = h("button", { class: "btn", type: "button" }, ico("download"), "Try another spot"); again.onclick = function () { closeGrab(); setPick(true); };
        grabMessage("No file found there", "Long-press a file or folder in an Explorer window or on the desktop. Files drawn inside other programs can't be picked this way.", "", again);
        return;
      }
      if (items.length === 1) { grabOne(items[0]); return; }
      d.haptic("tick");
      var close = h("button", { class: "scr-grab-x", type: "button", "aria-label": "Close" }, ico("x")); close.onclick = function () { closeGrab(); };
      var list = h("div", { class: "scr-grab-list" }, items.map(function (it) {
        var b = h("button", { class: "scr-grab-pick", type: "button" }, h("span", { class: "scr-grab-ico" }, ico(it.folder ? "folder" : "file")), h("span", { class: "grow" }, h("b", { text: it.name }), h("small", { text: it.folder ? "Folder" : sizeText(it.size) })), ico("chevron"));
        b.onclick = function () { d.haptic("tick"); grabOne(it); };
        return b;
      }));
      grabCard([h("div", { class: "scr-grab-row" }, h("span", { class: "grow" }, h("b", { text: "Which one?" }), h("small", { text: "Windows hides the file type here. Pick the file you meant." })), close), list]);
    }
    function grabOne(it) {
      var close = h("button", { class: "scr-grab-x", type: "button", "aria-label": "Close" }, ico("x")); close.onclick = function () { closeGrab(); };
      var sub = it.folder ? (it.allowed ? "Folder · comes as one zip" : "Folder") : it.allowed && it.size != null ? sizeText(it.size) : "File";   // a size is only known for a file the phone may see
      var head = h("div", { class: "scr-grab-row" }, h("span", { class: "scr-grab-ico" }, ico(it.folder ? "folder" : "file")), h("span", { class: "grow" }, h("b", { text: it.name, title: it.path }), h("small", { text: sub })), close);
      if (!it.allowed) {
        var why = it.needs_access ? "This phone hasn't been given this folder yet." : (it.reason || "This can't be downloaded.");
        var ask = null, err = h("p", { class: "scr-grab-err", role: "alert", hidden: true });
        if (it.needs_access && d.allowFolder) {
          var folderName = it.needs_access;
          ask = h("button", { class: "btn primary", type: "button" }, ico("folder"), "Allow this folder");
          ask.onclick = function () {                                            // allowed right here, in this card: no second sheet, and a second tap does nothing while it works
            if (ask.disabled) return;
            var seq = st.grabSeq;
            ask.disabled = true; err.hidden = true; d.haptic("medium");
            ask.replaceChildren(h("span", { class: "spin" }), document.createTextNode("Allowing…"));
            d.allowFolder(it.needs_access).then(function () {
              if (st.grabSeq !== seq) return;
              d.haptic("success"); it.allowed = true; it.needs_access = ""; it.size = null;
              grabOne(it);
            }).catch(function (e) {
              if (st.grabSeq !== seq) return;
              d.haptic("error"); ask.disabled = false; ask.replaceChildren(ico("retry"), document.createTextNode("Try again"));
              err.hidden = false; err.textContent = "Not allowed. " + ((e && e.message) || "The PC didn't answer.");
            });
          };
          why = "Allow this folder to open and download files from it. You can take it back in Settings.";
        }
        d.haptic("warning");
        grabCard([head, h("p", { class: "scr-grab-note", text: why }), it.needs_access ? h("p", { class: "scr-grab-path", text: it.needs_access, title: it.needs_access }) : null, err, ask ? h("div", { class: "scr-grab-acts" }, ask) : null].filter(Boolean), "warn");
        return;
      }
      var go = h("button", { class: "btn primary scr-grab-go", type: "button" }, ico("download"), "Download to phone");
      var no = h("button", { class: "btn ghost", type: "button" }, "Cancel"); no.onclick = function () { closeGrab(); };
      var card = grabCard([head, h("div", { class: "scr-grab-acts" }, go, no)]);
      if (!card) return;
      go.onclick = function () {
        var seq = st.grabSeq;
        go.disabled = true; no.disabled = true; d.haptic("medium");
        go.replaceChildren(h("span", { class: "spin" }), document.createTextNode(it.folder ? "Packing the folder…" : "Getting ready…"));
        d.api((it.folder ? "/browse/offer_folder?path=" : "/browse/offer?path=") + encodeURIComponent(it.path), { method: "POST" }).then(function (r) { return r.json(); }).then(function (info) {
          if (st.grabSeq !== seq) return;
          // from here on it is an ordinary download (resumable, in Downloads, with the landing animation), and this card follows it live
          Promise.resolve(d.download ? d.download(info.path, info.size) : null).then(function (live) {
            if (st.grabSeq !== seq) return;
            grabProgress(it, info, live);
          }).catch(function (e) {
            if (st.grabSeq !== seq) return;
            d.haptic("error"); go.disabled = false; no.disabled = false; go.replaceChildren(ico("retry"), document.createTextNode("Try again"));
            var err = card.querySelector(".scr-grab-err") || h("p", { class: "scr-grab-err", role: "alert" });
            err.textContent = "Not downloaded. " + ((e && e.message) || "It couldn't start.");
            if (!err.parentNode) card.insertBefore(err, card.lastChild);
          });
        }).catch(function (e) {
          if (st.grabSeq !== seq) return;
          d.haptic("error");
          go.disabled = false; no.disabled = false; go.replaceChildren(ico("retry"), document.createTextNode("Try again"));
          var err = card.querySelector(".scr-grab-err") || h("p", { class: "scr-grab-err", role: "alert" });
          err.textContent = "Not downloaded. " + ((e && e.message) || "The PC didn't answer.");
          if (!err.parentNode) card.insertBefore(err, card.lastChild);
        });
      };
    }
    /* The card after "Download to phone": it stays, and follows the download (name, size, how far, speed, time left, pause, cancel) until the file is saved, then offers Open.
       `live` is the app's live view of that download; in a plain browser there is none (the browser shows its own), so the card says where to look. */
    function grabProgress(it, info, live) {
      var close = h("button", { class: "scr-grab-x", type: "button", "aria-label": "Close" }, ico("x")); close.onclick = function () { closeGrab(); };
      var size = info && info.size ? sizeText(info.size) : "";
      var head = h("div", { class: "scr-grab-row" }, h("span", { class: "scr-grab-ico" }, ico(it.folder ? "folder" : "file")),
        h("span", { class: "grow" }, h("b", { text: (info && info.name) || it.name, title: it.path }), h("small", { text: [it.folder ? "Folder, sent as one zip" : "", size ? "Size " + size : ""].filter(Boolean).join(" · ") || "Download" })), close);
      var body = live || h("div", { class: "scr-grab-note" }, h("b", { text: "Downloading in your browser" }), h("span", { text: " The browser shows its own progress and keeps the file in its Downloads. It is " + (size || "on its way") + "." }));
      var done = h("button", { class: "btn ghost scr-grab-done", type: "button" }, "Done"); done.onclick = function () { closeGrab(); };
      grabCard([head, body, live ? null : h("div", { class: "scr-grab-acts" }, done)].filter(Boolean));   // (with a live view, Open is inside it and the x closes the card; the download goes on either way)
      d.haptic("success");
    }
    /* A long press on the picture while only looking (a finger held still, a little over half a second) */
    function bindGrab(stage) {
      var hold = null, start = null;
      var cancel = function () { if (hold) { clearTimeout(hold); hold = null; } start = null; };
      stage.addEventListener("pointerdown", function (e) {
        if (st.pick) {                                                            // armed: the next touch is the pick, whatever else it would have done
          if (e.target.closest && e.target.closest(".scr-zoomchip, .scr-pickgrp")) return;
          e.preventDefault(); e.stopImmediatePropagation(); start = { id: e.pointerId, x: e.clientX, y: e.clientY }; return;
        }
        cancel();
        if (st.control || !e.isPrimary || (e.target.closest && e.target.closest(".scr-zoomchip, .scr-pickgrp"))) return;
        start = { id: e.pointerId, x: e.clientX, y: e.clientY };
        hold = setTimeout(function () { hold = null; if (start) { var s = start; start = null; grabAt(s.x, s.y); } }, 550);
      }, true);
      stage.addEventListener("pointermove", function (e) {
        if (!start || start.id !== e.pointerId) return;
        if (Math.abs(e.clientX - start.x) > 10 || Math.abs(e.clientY - start.y) > 10) { if (st.pick) { start = null; } else cancel(); }
        if (st.pick) e.stopImmediatePropagation();
      }, true);
      var lift = function (e) {
        if (st.pick) {
          if (!start || start.id !== e.pointerId) return;
          e.preventDefault(); e.stopImmediatePropagation();
          var s = start; start = null;
          if (e.type === "pointerup") { setPick(false); grabAt(s.x, s.y); }
          return;
        }
        cancel();
      };
      stage.addEventListener("pointerup", lift, true);
      stage.addEventListener("pointercancel", lift, true);
      stage.addEventListener("click", function (e) { if (e.target.closest && e.target.closest(".scr-zoomchip, .scr-pickgrp")) return; if (st.pick || Date.now() - (st.gestureAt || 0) < 500) e.stopImmediatePropagation(); }, true);   // the lift that ended a pick or a long press is not also a tap
    }

    /* What the trackpad (on the picture or on the pad) asked for, done on the PC at the pointer. `scale` makes the small pad feel like the big picture. */
    function onTrack(g, scale) {
      var G = geom(); if (!G) return;
      var stage = document.querySelector(".scr-stage");
      if (g.kind === "move") {
        var a = accelerate(g.dx, g.dy, g.dt, st.speed * (scale || 1));
        var sr = seenRect(st.win, st.f);
        st.ptr = { x: Math.min(sr.x1, Math.max(sr.x0, st.ptr.x + a.dx / G.W * st.win.w)), y: Math.min(sr.y1, Math.max(sr.y0, st.ptr.y + a.dy / G.H * winH(st.win, st.f))) };   // kept on the part that can be seen; zoomed in, the same swipe moves it less: finer
        st.lastMove = Date.now(); followPointer();
        send([{ t: "move", x: st.ptr.x, y: st.ptr.y }], false);
      } else if (g.kind === "click") { ripple(stage, G); d.haptic("tick"); send([{ t: "click", x: st.ptr.x, y: st.ptr.y, button: g.button }], true); }
      else if (g.kind === "drag-start") { d.haptic("light"); send([{ t: "down", x: st.ptr.x, y: st.ptr.y, button: "left" }], true); }
      else if (g.kind === "drag-end") send([{ t: "up", x: st.ptr.x, y: st.ptr.y, button: "left" }], true);
      else if (g.kind === "scroll") send([{ t: "scroll", x: st.ptr.x, y: st.ptr.y, dy: g.dy }], false);
      else if (g.kind === "hold") { removeRing(); d.haptic("success"); openPad(); }
    }

    /* ---- a computer's mouse and keyboard: the pointer is simply where the mouse is, the buttons and wheel are the PC's, and the keys go to the PC ---- */
    function deskPoint(e) {
      var G = geom(); if (!G) return null;
      var q = pointToScreen(e.clientX, e.clientY, G.rect, G.nat, G.rot, G.cover); if (!q) return null;
      return winToScreen(st.win, q.x, q.y, st.f);
    }
    var DESK_BUTTON = { 0: "left", 1: "middle", 2: "right" };
    function deskMark(stage) { if (!st.desk) { st.desk = true; stage.classList.add("desk"); } }
    function deskMove(e) {
      var p = deskPoint(e); if (!p) return;
      st.ptr = { x: p.x, y: p.y }; st.lastMove = Date.now(); placeCursor();
      send([{ t: "move", x: p.x, y: p.y }], false);
    }
    function deskDown(stage, e) {
      deskMark(stage);
      var p = deskPoint(e), button = DESK_BUTTON[e.button]; if (!p || !button) return;
      e.preventDefault();
      try { stage.setPointerCapture(e.pointerId); } catch (x) { /* not capturable */ }
      st.ptr = { x: p.x, y: p.y }; st.lastMove = Date.now(); placeCursor();
      try { stage.focus({ preventScroll: true }); } catch (x2) { stage.focus(); }
      holdKeys();   // a click on the picture hands this computer's keyboard to the PC
      send([{ t: "down", x: p.x, y: p.y, button: button }], true);
    }
    function deskUp(stage, e) {
      var p = deskPoint(e) || st.ptr, button = DESK_BUTTON[e.button]; if (!button) return;
      try { stage.releasePointerCapture(e.pointerId); } catch (x) { /* not held */ }
      send([{ t: "up", x: p.x, y: p.y, button: button }], true);
    }
    function deskWheel(e) {
      e.preventDefault();
      var p = deskPoint(e) || st.ptr, w = wheelStep(st.wheelAcc || 0, e.deltaY, e.deltaMode);
      st.wheelAcc = w.acc;
      if (w.notches) send([{ t: "scroll", x: p.x, y: p.y, dy: w.notches }], false);
    }
    /* The keyboard. While it is "on the PC" (after a click on the picture) every key goes there, so typing, shortcuts and Ctrl+C / Ctrl+V work as at the PC itself. */
    function holdKeys() { if (!st.kcap) { st.kcap = true; syncKeyChip(); } }
    function releaseKeys() { if (st.kcap) { st.kcap = false; syncKeyChip(); } }
    function syncKeyChip() {
      var chip = document.querySelector(".scr-keychip"); if (!chip) return;
      chip.hidden = !(st.kcap && st.control);
    }
    function editable(t) { return !!(t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)); }
    function pcToLocal() {   // what the PC just copied, onto this computer's clipboard
      d.api("/screen/clipboard").then(function (r) { return r.json(); }).then(function (x) {
        var text = x.text || ""; if (!text) return;
        var done = function () { d.toast("Copied " + text.length + " characters from the PC."); };
        if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done).catch(function () { d.toast("The PC copied it, but this browser did not allow copying here."); });
      }).catch(clipError);
    }
    function onDeskKey(e) {
      if (!st.kcap || !st.control || editable(e.target)) return;
      if ((e.ctrlKey || e.metaKey) && !e.altKey && (e.key === "v" || e.key === "V")) return;   // the browser's paste event brings the text (see onDeskPaste)
      var m = mapKey(e); if (!m) return;
      e.preventDefault(); e.stopPropagation();
      send([m], true);
      if ((e.ctrlKey || e.metaKey) && !e.altKey && (e.key === "c" || e.key === "C" || e.key === "x" || e.key === "X")) setTimeout(pcToLocal, 300);   // copied on the PC: bring it here
    }
    function onDeskPaste(e) {
      if (!st.kcap || !st.control || editable(e.target)) return;
      e.preventDefault();
      var text = e.clipboardData && e.clipboardData.getData("text/plain");
      if (!text) { d.toast("Only text can be pasted on the PC."); return; }
      d.api("/screen/clipboard", { method: "POST", body: JSON.stringify({ text: text }) }).then(function () {
        send([{ t: "key", key: "v", mods: ["ctrl"] }], true);
        d.toast("Pasted " + text.length + " characters on the PC.");
      }).catch(clipError);
    }
    if (!st.deskBound) {
      st.deskBound = true;
      document.addEventListener("keydown", onDeskKey, true);
      document.addEventListener("paste", onDeskPaste, true);
      window.addEventListener("blur", releaseKeys);
    }

    /* Touch events on the picture. In Trackpad mode a finger moves the pointer; in Touch mode it points straight at things. */
    function bindStage(stage) {
      var last = { cx: 0, cy: 0 };
      st.track = createTrackpad(function (g) {
        if (g.kind === "hold-ring") { var G = geom(); if (G) showRing(null, G.rect, G.rot, last.cx, last.cy); }
        else if (g.kind === "hold-cancel") removeRing();
        else onTrack(g, 1);
      }, timers);
      st.gestures = createGestures(function (g) {
        var G = geom(); if (!G) return;
        var q = pointToScreen(g.x, g.y, G.rect, G.nat, G.rot, G.cover);
        if (!q) return;
        var p = winToScreen(st.win, q.x, q.y, st.f);   // a place in the shown picture is a place on the PC's screen through the zoom window
        st.ptr = { x: p.x, y: p.y }; st.lastMove = Date.now(); placeCursor();
        if (g.kind === "click") { ripple(stage, G); d.haptic("tick"); send([{ t: "click", x: p.x, y: p.y, button: g.button }], true); }
        else if (g.kind === "drag-start") send([{ t: "down", x: p.x, y: p.y, button: "left" }], true);
        else if (g.kind === "drag-move") send([{ t: "move", x: p.x, y: p.y }], false);
        else if (g.kind === "drag-end") send([{ t: "up", x: p.x, y: p.y, button: "left" }], true);
        else if (g.kind === "scroll") send([{ t: "scroll", x: p.x, y: p.y, dy: g.dy }], false);
      }, timers);
      /* Two fingers pinching zoom the view (and moving them while pinched pans it). The pinch is told apart from two fingers scrolling, which stay with the trackpad. */
      st.pinch = createPinch(function (g) {
        st.gestureAt = Date.now();
        if (g.kind === "start") { st.pinching = true; st.pan = null; ["track", "gestures"].forEach(function (k) { if (st[k]) st[k].reset(); }); removeRing(); d.haptic("tick"); }
        else if (g.kind === "pinch") {
          var G = geom(); if (!G) return;
          var q = pointToScreen(g.cx - g.dx, g.cy - g.dy, G.rect, G.nat, G.rot, G.cover);   // the point that was under the fingers a moment ago must end up under them now
          var a = q ? winToScreen(st.win, q.x, q.y, st.f) : { x: st.win.x0 + st.win.w / 2, y: st.win.y0 + winH(st.win, st.f) / 2 };
          var win = zoomWin(st.win, g.factor, a.x, a.y, st.f), fv = frameVec(g.dx, g.dy, G.rot);
          setWin(panWin(win, -fv.x / G.W * win.w, -fv.y / G.H * winH(win, st.f), st.f), false);
        } else if (g.kind === "end") { st.pinching = false; if (st.wake) st.wake(); }
      });
      var feed = function (kind, e) {
        if (!st.control) return;
        if (st.pinch.active() || st.pinch.mode() === "done") return;                       // a pinch is under way, or just ended with a finger still down
        var G = geom(); if (!G) return;
        last = { cx: e.clientX, cy: e.clientY };
        if (st.pmode === "touch") { st.gestures[kind](e.pointerId, e.clientX, e.clientY); return; }
        var f = frameOf(e.clientX, e.clientY, G.rot);
        st.track[kind](e.pointerId, f.x, f.y, e.timeStamp);
      };
      var panWith = function (e) {   // one finger drags the picture while zoomed in and not controlling
        var G = geom(); if (!G || !st.pan || st.pan.id !== e.pointerId) return;
        var f = frameVec(e.clientX - st.pan.x, e.clientY - st.pan.y, G.rot);
        st.pan.x = e.clientX; st.pan.y = e.clientY; st.gestureAt = Date.now();
        setWin(panWin(st.win, -f.x / G.W * st.win.w, -f.y / G.H * winH(st.win, st.f), st.f), false);
      };
      stage.addEventListener("pointerdown", function (e) {
        if (e.target.closest && e.target.closest(".scr-zoomchip, .scr-pickgrp")) return;
        if (e.pointerType === "mouse" && st.control) { deskDown(stage, e); return; }
        if (e.isPrimary) st.pinch.reset();   // the first finger of a gesture: any finger remembered from before is gone, even if its "up" never reached us
        var grab = wantsTouch();   // zoomed in, full screen or controlling: the picture takes the touch. Otherwise the page scrolls over it, but a pinch is still noticed.
        if (grab) { e.preventDefault(); try { stage.setPointerCapture(e.pointerId); } catch (x) { /* not capturable */ } }
        st.pinch.down(e.pointerId, e.clientX, e.clientY);
        if (st.control) { if (grab) feed("down", e); }
        else if (grab && zoomed() && st.pinch.mode() === "idle") st.pan = { id: e.pointerId, x: e.clientX, y: e.clientY };
        else st.pan = null;
      });
      stage.addEventListener("pointermove", function (e) {
        if (e.pointerType === "mouse" && st.control) { deskMove(e); return; }
        if (st.pinch) st.pinch.move(e.pointerId, e.clientX, e.clientY); if (st.control) feed("move", e); else panWith(e);
      });
      stage.addEventListener("wheel", function (e) { if (st.control) deskWheel(e); }, { passive: false });
      stage.addEventListener("blur", function () { releaseKeys(); });
      var lift = function (kind) {
        return function (e) {
          if (e.pointerType === "mouse" && st.control) { deskUp(stage, e); return; }
          if (st.pinch) st.pinch[kind](e.pointerId);
          if (st.control) { feed(kind, e); if (kind === "cancel") removeRing(); }
          else if (st.pan && st.pan.id === e.pointerId) { st.pan = null; if (st.wake) st.wake(); }
        };
      };
      stage.addEventListener("pointerup", lift("up"));
      stage.addEventListener("pointercancel", lift("cancel"));
      stage.addEventListener("lostpointercapture", function (e) { if (st.pinch) st.pinch.cancel(e.pointerId); });
      stage.addEventListener("contextmenu", function (e) { e.preventDefault(); });   // a long press must not open the browser's menu (a long press on a file asks the PC for it)
    }

    /* ---------------------------------------------------------------- the see-through mouse pad  */

    function buildPad() {
      var surface = h("div", { class: "scr-pad-surface", role: "application", "aria-label": "Mouse pad: slide a finger to move the pointer, tap to click, two fingers to scroll" }, h("span", { class: "scr-pad-hint", text: "Mouse pad" }));
      var strip = h("div", { class: "scr-pad-strip", "aria-label": "Scroll strip", role: "slider" }, h("i"), h("i"), h("i"));
      var mkBtn = function (cls, label, button) {
        var b = h("button", { class: "scr-pad-b " + cls, type: "button", "aria-label": label, text: button === "left" ? "Left click" : "Right click" });
        b.addEventListener("pointerdown", function (e) { e.preventDefault(); padTouched(); d.haptic("tick"); b.classList.add("down"); try { b.setPointerCapture(e.pointerId); } catch (x) { /* ok */ } send([{ t: "down", x: st.ptr.x, y: st.ptr.y, button: button }], true); });
        var up = function () { if (!b.classList.contains("down")) return; b.classList.remove("down"); send([{ t: "up", x: st.ptr.x, y: st.ptr.y, button: button }], true); };
        b.addEventListener("pointerup", up); b.addEventListener("pointercancel", up);
        return b;
      };
      var close = h("button", { class: "scr-pad-x", type: "button", "aria-label": "Close the mouse pad" }, ico("x"));
      close.onclick = function () { d.haptic("tick"); st.pad = false; syncPad(); updateDock(); };
      var pad = h("div", { class: "scr-pad", hidden: true }, close, surface, strip, h("div", { class: "scr-pad-btns" }, mkBtn("l", "Left click", "left"), mkBtn("r", "Right click", "right")));

      st.padTrack = createTrackpad(function (g) { if (g.kind === "hold-ring" || g.kind === "hold-cancel" || g.kind === "hold") return; onTrack(g, 0.55 * Math.max(1, (geom() || { cw: 300 }).cw / Math.max(120, surface.clientWidth || 300))); }, timers);
      var feed = function (kind, e) {
        var G = geom(); if (!G) return;
        padTouched();
        var f = frameOf(e.clientX, e.clientY, G.rot);
        st.padTrack[kind](e.pointerId, f.x, f.y, e.timeStamp);
      };
      surface.addEventListener("pointerdown", function (e) { e.preventDefault(); try { surface.setPointerCapture(e.pointerId); } catch (x) { /* ok */ } feed("down", e); });
      surface.addEventListener("pointermove", function (e) { feed("move", e); });
      surface.addEventListener("pointerup", function (e) { feed("up", e); });
      surface.addEventListener("pointercancel", function (e) { feed("cancel", e); });
      var sy = null;   // the strip scrolls with one finger
      strip.addEventListener("pointerdown", function (e) { e.preventDefault(); padTouched(); try { strip.setPointerCapture(e.pointerId); } catch (x) { /* ok */ } var G = geom(); sy = G ? frameOf(e.clientX, e.clientY, G.rot) : null; strip.dataset.acc = "0"; });
      strip.addEventListener("pointermove", function (e) {
        if (!sy) return;
        var G = geom(); if (!G) return;
        var f = frameOf(e.clientX, e.clientY, G.rot), acc = parseFloat(strip.dataset.acc || "0") + (f.y - sy.y); sy = f;
        var steps = acc > 0 ? Math.floor(acc / 22) : Math.ceil(acc / 22);
        if (steps !== 0) { acc -= steps * 22; send([{ t: "scroll", x: st.ptr.x, y: st.ptr.y, dy: steps }], false); }
        strip.dataset.acc = String(acc);
      });
      var end = function () { sy = null; };
      strip.addEventListener("pointerup", end); strip.addEventListener("pointercancel", end);
      return pad;
    }
    function padTouched() {
      var pad = document.querySelector(".scr-pad"); if (!pad) return;
      pad.classList.add("touched");
      clearTimeout(st.padTimer);
      st.padTimer = setTimeout(function () { pad.classList.remove("touched"); }, 2600);   // it fades back to see-through when not in use
    }
    function openPad() { if (!st.control) return; st.pad = true; syncPad(); updateDock(); padTouched(); }
    function togglePad() { d.haptic("tick"); st.pad = !st.pad; syncPad(); updateDock(); if (st.pad) padTouched(); }
    function syncPad() {
      document.querySelectorAll(".scr-pad").forEach(function (p) { p.hidden = !(st.pad && st.control); });
      var view = currentView(); if (view) view.classList.toggle("pad-on", st.pad && st.control);
    }

    /* ---------------------------------------------------------------- the keyboard  */

    function buildKeyboard() {
      var input = h("input", { class: "scr-kb-in", type: "text", autocomplete: "off", autocapitalize: "off", autocorrect: "off", spellcheck: "false", "aria-label": "Type on the PC", placeholder: "Tap here, then type on the PC" });
      var held = function () { return Object.keys(st.mods).filter(function (m) { return st.mods[m]; }); };
      var sendKey = function (key) { var mods = held(); st.mods = {}; syncKeyboard(); send([{ t: "key", key: key, mods: mods }], true); };
      var typed = function (text) {
        if (!text) return;
        if (held().length && text.length === 1) { sendKey(text); return; }              // Ctrl then "c" is the shortcut, not the letter c
        send([{ t: "text", text: text }], true);
      };
      input.addEventListener("input", function (e) { if (e.isComposing) return; typed(input.value); input.value = ""; });
      input.addEventListener("compositionend", function (e) { typed(e.data || input.value); input.value = ""; });
      input.addEventListener("keydown", function (e) {
        var map = { Backspace: "backspace", Enter: "enter", Tab: "tab", Escape: "esc", ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down", Delete: "delete", Home: "home", End: "end", PageUp: "pageup", PageDown: "pagedown", Insert: "insert" };
        if (/^F([1-9]|1[0-2])$/.test(e.key)) { e.preventDefault(); sendKey(e.key.toLowerCase()); return; }
        if (map[e.key]) { e.preventDefault(); sendKey(map[e.key]); }
      });
      var plain = function (b) { b.onmousedown = function (e) { e.preventDefault(); }; return b; };   // a tap on a key must not take the typing focus (or the phone's keyboard) away
      var key = function (label, k, aria) { var b = plain(h("button", { class: "scr-key", type: "button", "aria-label": aria || label, text: label })); b.onclick = function () { d.haptic("tick"); sendKey(k); }; return b; };
      var mod = function (label, m) { var b = plain(h("button", { class: "scr-key mod", type: "button", "data-mod": m, "aria-pressed": "false", text: label })); b.onclick = function () { st.mods[m] = !st.mods[m]; d.haptic("tick"); syncKeyboard(); }; return b; };
      /* a shortcut is one press: the keys go down together and come up together on the PC, so nothing is left held */
      var combo = function (label, k, mods, aria, sure) {
        var b = plain(h("button", { class: "scr-key combo", type: "button", "aria-label": aria || label, text: label })), armed = 0;
        b.onclick = function () {
          if (sure && !armed) { armed = setTimeout(function () { armed = 0; b.textContent = label; b.classList.remove("sure"); }, 2200); b.textContent = "Tap again"; b.classList.add("sure"); d.haptic("warning"); return; }   // closing a window cannot be undone: ask twice
          clearTimeout(armed); armed = 0; b.textContent = label; b.classList.remove("sure");
          d.haptic("tick"); st.mods = {}; syncKeyboard(); send([{ t: "key", key: k, mods: mods }], true);
        };
        return b;
      };
      var rows = {
        main: h("div", { class: "scr-keys", "data-tab": "main" }, key("Esc", "esc"), key("Tab", "tab"), mod("Ctrl", "ctrl"), mod("Alt", "alt"), mod("Shift", "shift"), mod("Win", "win"), key("←", "left", "Left arrow"), key("↑", "up", "Up arrow"), key("↓", "down", "Down arrow"), key("→", "right", "Right arrow"), key("Del", "delete"), key("⌫", "backspace", "Backspace"), key("Enter", "enter")),
        fn: h("div", { class: "scr-keys", "data-tab": "fn", hidden: true }, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12].map(function (n) { return key("F" + n, "f" + n); }).concat([key("Home", "home"), key("End", "end"), key("PgUp", "pageup", "Page up"), key("PgDn", "pagedown", "Page down"), key("Ins", "insert", "Insert"), key("PrtSc", "printscreen", "Print screen")])),
        cut: h("div", { class: "scr-keys", "data-tab": "cut", hidden: true },
          combo("Copy", "c", ["ctrl"], "Copy (Ctrl C)"), combo("Paste", "v", ["ctrl"], "Paste (Ctrl V)"), combo("Cut", "x", ["ctrl"], "Cut (Ctrl X)"), combo("Undo", "z", ["ctrl"], "Undo (Ctrl Z)"), combo("Redo", "y", ["ctrl"], "Redo (Ctrl Y)"),
          combo("Select all", "a", ["ctrl"], "Select all (Ctrl A)"), combo("Save", "s", ["ctrl"], "Save (Ctrl S)"), combo("Find", "f", ["ctrl"], "Find (Ctrl F)"),
          combo("Alt+Tab", "tab", ["alt"], "Switch window (Alt Tab)"), combo("Desktop", "d", ["win"], "Show the desktop (Win D)"), combo("Task Mgr", "esc", ["ctrl", "shift"], "Task Manager (Ctrl Shift Esc)"),
          combo("Close", "f4", ["alt"], "Close the window (Alt F4)", true)),
        clip: buildClipboard(plain)
      };
      var tabs = h("div", { class: "scr-kb-tabs", role: "tablist", "aria-label": "Kinds of keys" });
      [["main", "Keys"], ["fn", "F & nav"], ["cut", "Shortcuts"], ["clip", "Clipboard"]].forEach(function (t, i) {
        var b = plain(h("button", { class: "scr-kb-tab" + (i === 0 ? " on" : ""), type: "button", role: "tab", "aria-selected": String(i === 0), text: t[1] }));
        b.onclick = function () {
          d.haptic("tick");
          tabs.querySelectorAll(".scr-kb-tab").forEach(function (x) { var on = x === b; x.classList.toggle("on", on); x.setAttribute("aria-selected", String(on)); });
          Object.keys(rows).forEach(function (k) { rows[k].hidden = k !== t[0]; });
        };
        tabs.append(b);
      });
      return h("div", { class: "scr-kb", hidden: true }, input, tabs, rows.main, rows.fn, rows.cut, rows.clip);
    }

    /* ---- the clipboard: text copied on the phone, pasted on the PC, or copied on the PC and brought to the phone ---- */
    function clipError(e) { if (e && e.code === 401) return; d.toast((e && e.message) || "The clipboard didn't work."); d.haptic("error"); }
    function buildClipboard(plain) {
      var ta = h("textarea", { class: "scr-clip-in", rows: "2", autocomplete: "off", spellcheck: "false", "aria-label": "Text to paste on the PC, or what was copied from it", placeholder: "Paste text here to send it, or see what the PC copied" });
      var shown = function (text, why) { ta.value = text; if (why) d.toast(why); };
      var pasteToPC = function () {
        var go = function (text) {
          if (!text) { d.toast("Nothing to paste. Copy something on this phone first, or type it in the box."); return; }
          d.api("/screen/clipboard", { method: "POST", body: JSON.stringify({ text: text }) }).then(function () {
            send([{ t: "key", key: "v", mods: ["ctrl"] }], true);   // the PC pastes where its cursor is
            ta.value = ""; d.haptic("success"); d.toast("Pasted " + text.length + " characters on the PC.");
          }).catch(clipError);
        };
        if (ta.value) { go(ta.value); return; }
        if (navigator.clipboard && navigator.clipboard.readText) navigator.clipboard.readText().then(go).catch(function () { ta.focus(); d.toast("Your phone didn't allow reading its clipboard. Paste into the box, then tap again."); });
        else { ta.focus(); d.toast("Paste your text into the box, then tap again."); }
      };
      var fromPC = function (copyFirst) {
        var get = function () {
          d.api("/screen/clipboard").then(function (r) { return r.json(); }).then(function (x) {
            var text = x.text || "";
            if (!text) { shown("", "The PC's clipboard has no text."); return; }
            ta.value = text;
            var done = function () { d.haptic("success"); d.toast("Copied " + text.length + " characters from the PC to this phone."); };
            if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done).catch(function () { ta.select(); d.toast("Select the text in the box and copy it."); });
            else { ta.select(); d.toast("Select the text in the box and copy it."); }
          }).catch(clipError);
        };
        if (copyFirst) { send([{ t: "key", key: "c", mods: ["ctrl"] }], true); setTimeout(get, 350); } else get();
      };
      var btn = function (label, primary, fn, aria) { var b = plain(h("button", { class: "scr-key" + (primary ? " primary" : ""), type: "button", text: label, "aria-label": aria || label })); b.onclick = function () { d.haptic("tick"); fn(); }; return b; };
      return h("div", { class: "scr-clip", "data-tab": "clip", hidden: true }, ta,
        h("div", { class: "scr-clip-btns" }, btn("Paste on PC", true, pasteToPC, "Send this phone's copied text to the PC and paste it there"), btn("Copy selection", false, function () { fromPC(true); }, "Copy what is selected on the PC and bring it to this phone"), btn("Get PC clipboard", false, function () { fromPC(false); }, "Bring the PC's clipboard text to this phone")),
        h("small", { class: "scr-clip-note", text: "Text only. The PC's own pointer decides where it pastes." }));
    }
    function toggleKeyboard() {
      st.kb = !st.kb; d.haptic("tick"); syncKeyboard(); updateDock();
      var input = document.querySelector(".scr-kb-in");
      if (st.kb && input) setTimeout(function () { input.focus(); }, 60);
    }
    function syncKeyboard() {
      document.querySelectorAll(".scr-kb").forEach(function (k) { k.hidden = !(st.kb && st.control); });
      document.querySelectorAll(".scr-key.mod").forEach(function (b) { var on = !!st.mods[b.dataset.mod]; b.classList.toggle("on", on); b.setAttribute("aria-pressed", String(on)); });
      var view = currentView(); if (view) view.classList.toggle("kb-on", st.kb && st.control);
    }

    /* ---------------------------------------------------------------- the PC's sound  */

    function startSound() {
      if (st.snd) return;
      var AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) { d.toast("This browser can't play the PC's sound."); return; }
      /* "Clear" is 48 kHz (the rate phones play at, so nothing is re-sampled and nothing is lost); "Light" is 16 kHz for a slow link. The player is made at the same rate as the
         sound, because a phone that has to convert every little piece on its own puts a tiny click at each join. */
      var want = store("afp_scr_snd", "48000") === "16000" ? 16000 : 48000, ctx;
      try { ctx = new AC({ sampleRate: want }); } catch (e) { ctx = new AC(); }
      var gain = ctx.createGain(), ctl = typeof AbortController === "function" ? new AbortController() : null, snd = { ctx: ctx, gain: gain, ctl: ctl, next: 0, lead: 0.25 };
      gain.gain.value = st.vol; gain.connect(ctx.destination);
      if (ctx.resume) { var go = ctx.resume(); if (go && go.catch) go.catch(function () { /* closed again already */ }); }   // a tap on the switch is what lets the phone play sound
      st.snd = snd;
      var fail = function (e) {
        if (st.snd !== snd) return;
        stopSound(); if (st.pop === "opts") renderPop();
        if (e && e.name === "AbortError") return;
        d.toast((e && e.message) || "The PC's sound stopped."); d.haptic("error");
      };
      d.api("/screen/audio?rate=" + want, ctl ? { signal: ctl.signal } : {}).then(function (r) {
        var rate = parseInt(r.headers.get("X-Audio-Rate"), 10) || 16000;   // what the PC really sends
        var reader = r.body.getReader(), rest = new Uint8Array(0);
        var pump = function () {
          return reader.read().then(function (x) {
            if (x.done) { fail({ message: "The PC stopped sharing its sound." }); return; }
            if (st.snd !== snd || ctx.state === "closed") { try { reader.cancel(); } catch (q) { /* gone */ } return; }
            var bytes = x.value;
            if (rest.length) { var all = new Uint8Array(rest.length + bytes.length); all.set(rest); all.set(bytes, rest.length); bytes = all; }
            var n = bytes.length & ~1; rest = bytes.slice(n);
            if (n) {
              var samples = pcmToFloat(bytes.subarray(0, n)), buf = ctx.createBuffer(1, samples.length, rate);
              buf.getChannelData(0).set(samples);
              if (snd.next && snd.next < ctx.currentTime + 0.02) snd.lead = Math.min(0.8, snd.lead + 0.08);   // it ran dry: from now on keep a little more in hand (up to 0.8 s), so a rough link gets a steadier sound
              var plan = planSound(ctx.currentTime, snd.next, samples.length / rate, snd.lead, 1.8);
              snd.next = plan.next;
              if (!plan.drop) { var src = ctx.createBufferSource(); src.buffer = buf; src.connect(gain); src.start(plan.start); }
            }
            return pump();
          });
        };
        return pump();
      }).catch(fail);
      d.haptic("tick");
    }
    function stopSound() {
      var snd = st.snd; if (!snd) return;
      st.snd = null;
      try { if (snd.ctl) snd.ctl.abort(); } catch (e) { /* done */ }
      d.api("/screen/audio/stop", { method: "POST", body: "{}" }).catch(function () { /* the PC ends it by itself when the looking ends */ });   // tell the PC at once, so it stops capturing
      try { var done = snd.ctx.close(); if (done && done.catch) done.catch(function () { /* already closed */ }); } catch (e) { /* done */ }
    }

    /* ---------------------------------------------------------------- the options and ask-for-control panel  */

    function toggleOptions() { d.haptic("tick"); st.pop = st.pop === "opts" ? "" : "opts"; renderPop(); updateDock(); }
    function toggleFit() { st.fit = st.fit === "fill" ? "fit" : "fill"; keep("afp_scr_fit", st.fit); d.haptic("tick"); updateDock(); refit(); placeCursor(); if (st.pop === "opts") renderPop(); }

    function seg(items, current, change, label) {
      var box = h("div", { class: "scr-seg", role: "radiogroup", "aria-label": label });
      items.forEach(function (it) {
        var b = h("button", { class: "scr-seg-b" + (it.id === current ? " on" : ""), type: "button", role: "radio", "aria-checked": String(it.id === current), text: it.name });
        b.onclick = function () { d.haptic("tick"); change(it.id); };
        box.append(b);
      });
      return box;
    }

    function renderPop() {
      var pop = document.querySelector(".scr-pop"); if (!pop) return;
      var lv = currentView(); if (lv) lv.classList.toggle("pop-on", !!st.pop);   // in full screen the first-time tips step aside while a panel is open
      if (!st.pop) { pop.hidden = true; pop.replaceChildren(); return; }
      pop.hidden = false;
      var v = st.view, parts = [];
      var head = function (title, sub, noClose) {
        var x = null;
        if (!noClose) { x = h("button", { class: "scr-pop-x", type: "button", "aria-label": "Close" }, ico("x")); x.onclick = function () { st.pop = ""; renderPop(); updateDock(); }; }
        return h("div", { class: "scr-pop-h" }, h("b", { text: title }), sub ? h("small", { text: sub }) : null, x);
      };
      if (st.pop === "opts") {
        var q = preset(st.quality);
        parts.push(head("Picture"), seg(PRESETS, st.quality, function (id) { st.quality = id; renderPop(); updateDock(); paintStatus(); }, "Picture quality"),
          h("p", { class: "scr-pop-note", text: st.quality === "auto" ? "Auto adapts to your network: now " + LADDER[st.auto.i].w + " px wide." : q.note + "." }));
        parts.push(seg([{ id: "fit", name: "Fit" }, { id: "fill", name: "Fill" }], st.fit, function (id) { st.fit = id; keep("afp_scr_fit", id); renderPop(); updateDock(); refit(); placeCursor(); }, "How the picture fills the screen"),
          h("p", { class: "scr-pop-note", text: st.fit === "fit" ? "Fit shows the whole screen (bars if your phone is a different shape)." : "Fill covers the whole display, cropping a little." }));
        var zs = h("input", { class: "scr-range", type: "range", min: "1", max: String(ZMAX), step: "0.1", value: String(zoomLevel()), "aria-label": "Zoom" });
        var zv = h("span", { class: "scr-range-v scr-zoom-v", text: zoomLevel().toFixed(1) + "×" });
        zs.addEventListener("input", function () { zoomTo(parseFloat(zs.value)); zv.textContent = zoomLevel().toFixed(1) + "×"; });
        parts.push(h("label", { class: "scr-range-row" }, h("span", { text: "Zoom" }), zs, zv),
          h("p", { class: "scr-pop-note", text: "Pinch or double-tap. Zoomed parts stay sharp." }));
        var hasSound = !(v && v.audio === false);
        parts.push(head("Sound", "", true),
          seg([{ id: "off", name: "Off" }, { id: "on", name: "On" }], st.snd ? "on" : "off", function (id) { if (id === "on") startSound(); else stopSound(); renderPop(); }, "The PC's sound"));
        if (hasSound) {
          var clear = store("afp_scr_snd", "48000") !== "16000";
          parts.push(seg([{ id: "48000", name: "Clear" }, { id: "16000", name: "Light" }], clear ? "48000" : "16000", function (id) { keep("afp_scr_snd", id); if (st.snd) { stopSound(); startSound(); } renderPop(); }, "Sound quality"),
            h("p", { class: "scr-pop-note", text: clear ? "Clear: full quality (about 0.8 Mbit/s). Best on Wi-Fi." : "Light: smaller (about 0.25 Mbit/s). Voices stay fine; music is duller." }));
        }
        if (st.snd) {
          var vs = h("input", { class: "scr-range", type: "range", min: "0", max: "1", step: "0.05", value: String(st.vol), "aria-label": "Volume" });
          var vv = h("span", { class: "scr-range-v", text: Math.round(st.vol * 100) + "%" });
          vs.addEventListener("input", function () { st.vol = parseFloat(vs.value); if (st.snd) st.snd.gain.gain.value = st.vol; vv.textContent = Math.round(st.vol * 100) + "%"; keep("afp_scr_vol", String(st.vol)); });
          parts.push(h("label", { class: "scr-range-row" }, h("span", { text: "Volume" }), vs, vv));
        }
        parts.push(h("p", { class: "scr-pop-note", text: hasSound ? "Plays the PC's sound here while you look. It stops when you stop." : "Sound is switched off in this PC's settings." }));
        parts.push(head("Mouse", st.control ? "" : "Pointer and cursor", true),
          seg([{ id: "trackpad", name: "Trackpad" }, { id: "touch", name: "Touch" }], st.pmode, function (id) { st.pmode = id; keep("afp_scr_pmode", id); ["track", "gestures", "padTrack"].forEach(function (k) { if (st[k]) st[k].reset(); }); renderPop(); }, "Pointer style"),
          h("p", { class: "scr-pop-note", text: st.pmode === "trackpad" ? "Like a laptop: slide to move the pointer, tap to click, two fingers to scroll, two-finger tap to right-click, tap then drag to drag. Hold still for two seconds for the mouse pad." : "Point straight at things: tap to click where you tap, press and hold to right-click, drag to drag, two fingers to scroll." }));
        var slider = h("input", { class: "scr-range", type: "range", min: "0.6", max: "2", step: "0.1", value: String(st.speed), "aria-label": "Pointer speed" });
        slider.addEventListener("input", function () { st.speed = parseFloat(slider.value); keep("afp_scr_speed", st.speed); val.textContent = st.speed.toFixed(1) + "×"; });
        var val = h("span", { class: "scr-range-v", text: st.speed.toFixed(1) + "×" });
        if (st.pmode === "trackpad") parts.push(h("label", { class: "scr-range-row" }, h("span", { text: "Pointer speed" }), slider, val));
        var size = h("input", { class: "scr-range", type: "range", min: "0.6", max: "2.4", step: "0.1", value: String(st.cursor), "aria-label": "Cursor size" });
        var sval = h("span", { class: "scr-range-v", text: st.cursor.toFixed(1) + "×" });
        size.addEventListener("input", function () {
          st.cursor = parseFloat(size.value); keep("afp_scr_cursor", st.cursor); sval.textContent = st.cursor.toFixed(1) + "×";
          var live = currentView(); if (live) live.style.setProperty("--cs", String(st.cursor));   // the phone's own pointer changes size as you slide
        });
        parts.push(h("label", { class: "scr-range-row" }, h("span", { text: "Cursor size" }), size, sval));
      } else if (st.pop === "ctlask") {
        var lvl = v && v.me;
        parts.push(head("Control needs the PC owner's yes"),
          h("p", { class: "scr-pop-note", text: "You can look at the screen. To move the mouse and type, the person at the PC has to allow it. They'll see a window with Allow and Deny." }));
        var go = h("button", { class: "btn primary block", type: "button" }, ico("bell"), "Ask the PC owner");
        go.onclick = function () { d.haptic("medium"); go.disabled = true; askForControl(); };
        parts.push(go); void lvl;
      } else if (st.pop === "ctlwait") {
        parts.push(head("Waiting for the PC owner"), h("div", { class: "scr-wait" }, h("span", { class: "spin" }), h("span", { text: "They see a window on the PC with Allow and Deny. Control starts when they allow it." })));
        var nope = h("button", { class: "btn ghost block", type: "button" }, "Cancel the request");
        nope.onclick = function () { d.haptic("light"); callJson("POST", "/cancel").catch(function () { /* times out on the PC */ }); st.pop = ""; renderPop(); refresh(); };
        parts.push(nope);
      }
      pop.replaceChildren.apply(pop, parts);
    }
    document.addEventListener("pointerdown", function (e) {
      if (!st.pop) return;
      var pop = document.querySelector(".scr-pop");
      if (pop && !pop.contains(e.target) && !e.target.closest(".scr-dock")) { st.pop = ""; renderPop(); updateDock(); }   // a tap outside closes it
    }, true);

    /* A short first-time guide, shown once when control is first switched on. */
    function buildCoach() {
      var ok = h("button", { class: "scr-coach-ok", type: "button" }, "Got it");
      ok.onclick = function () { keep("afp_scr_coach", "1"); var c = document.querySelector(".scr-coach"); if (c) c.hidden = true; d.haptic("tick"); };
      return h("div", { class: "scr-coach", hidden: true, role: "dialog", "aria-label": "How to control the PC" },
        h("b", { text: "You're controlling the PC" }),
        h("ul", {}, h("li", { text: "Slide one finger to move the pointer" }), h("li", { text: "Tap to click · two-finger tap to right-click" }), h("li", { text: "Two fingers to scroll" }), h("li", { text: "Hold still for 2 seconds for the mouse pad" })), ok);
    }

    /* ---------------------------------------------------------------- full screen, in landscape: the real Fullscreen API where there is one (and a landscape lock),
       otherwise a cover over the page that turns the picture sideways when the phone is held upright (iPhone has no element fullscreen). The view moves out of the sheet
       while it is up.  */
    function enterFull() {
      var view = currentView();
      if (!view || st.full) return;
      st.full = true; st.home = view.parentNode;
      document.body.appendChild(view);
      view.classList.add("full");
      document.documentElement.classList.add("scr-noscroll");
      var go = view.requestFullscreen ? view.requestFullscreen({ navigationUI: "hide" }) : Promise.reject();
      Promise.resolve(go).then(function () { st.native = true; return screen.orientation && screen.orientation.lock ? screen.orientation.lock("landscape") : null; })
        .catch(function () { /* no real fullscreen or no lock here: the cover and the turned picture do the job */ }).then(function () { applyRot(); placeCursor(); });
      layoutFull(); applyRot(); burgerTouch(); d.haptic("tick"); updateDock();   // it starts with nothing over the picture; the corner button shows itself for a moment so it can be found
    }
    function exitFull() {
      if (!st.full) return;
      st.full = false;
      var view = currentView();
      try { if (document.fullscreenElement && document.exitFullscreen) document.exitFullscreen().catch(function () { /* already out */ }); } catch (e) { /* already out */ }
      try { if (screen.orientation && screen.orientation.unlock) screen.orientation.unlock(); } catch (e) { /* nothing was locked */ }
      st.native = false; clearTimeout(st.ctlTimer);
      if (view) {
        view.classList.remove("full", "rot", "ctl-on");
        ["--vw", "--vh", "--vl", "--vt"].forEach(function (k) { view.style.removeProperty(k); });
        if (st.home && st.home.isConnected) st.home.insertBefore(view, st.home.firstChild); else view.remove();
      }
      st.home = null;
      document.documentElement.classList.remove("scr-noscroll", "scr-rot");
      ["--svw", "--svh"].forEach(function (k) { document.documentElement.style.removeProperty(k); });
      updateDock(); placeCursor();
    }
    function applyRot() {
      var view = currentView();
      if (view && st.full) {   // held upright and not locked: turn the picture to fill the screen
        var rot = window.innerHeight > window.innerWidth;
        view.classList.toggle("rot", rot);
        document.documentElement.classList.toggle("scr-rot", rot);   // the app's dialogs, sheets, toasts and the download animation turn with it (see app.css), so they are never sideways to the picture
        layoutFull(); placeCursor();
      }
    }
    /* The full-screen view is exactly as big as the part of the screen that can be SEEN (the visual viewport), not whatever the page thinks its height is: a browser toolbar,
       the Android navigation bar or a gesture bar can sit over the bottom of a page that is "100% tall", and the bottom of the picture would be hidden behind it. */
    function layoutFull() {
      var view = currentView(); if (!view || !st.full) return;
      var vv = window.visualViewport, w = Math.min(vv ? vv.width : window.innerWidth, window.innerWidth), h = Math.min(vv ? vv.height : window.innerHeight, window.innerHeight);
      view.style.setProperty("--vw", Math.round(w) + "px"); view.style.setProperty("--vh", Math.round(h) + "px");
      view.style.setProperty("--vl", Math.round(vv ? vv.offsetLeft : 0) + "px"); view.style.setProperty("--vt", Math.round(vv ? vv.offsetTop : 0) + "px");
      document.documentElement.style.setProperty("--svw", Math.round(w) + "px"); document.documentElement.style.setProperty("--svh", Math.round(h) + "px");   // the same size, for the overlays that turn with the view
    }
    /* Turning the phone sideways while looking goes straight to full screen, and turning it back returns to the normal view (like a video). Choosing Exit while sideways
       keeps the normal view until the phone is turned upright again. */
    function onTurn() {
      var view = currentView();
      if (!view || !st.view || st.view.state !== "viewing") return;
      var landscape = window.innerWidth > window.innerHeight;
      if (!landscape) st.noAuto = false;
      if (landscape && !st.full && !st.noAuto) { st.autoFull = true; enterFull(); }
      else if (!landscape && st.full && st.autoFull) { st.autoFull = false; exitFull(); }
      else if (st.full) applyRot();
    }
    /* Full screen: nothing covers the picture. One dimmed button in the corner (the burger) opens a small menu with every control; the menu closes, and the button
       dims again, after each choice. A coloured dot on the button says control is on, so it is never a surprise. */
    function burgerTouch() {
      var b = document.querySelector(".scr-burger"); if (!b) return;
      b.classList.add("touched");
      clearTimeout(st.burgerTimer);
      st.burgerTimer = setTimeout(function () { var x = document.querySelector(".scr-burger"); if (x) x.classList.remove("touched"); }, 2600);   // it fades back until it is touched again
    }
    function openMenu() { var v = currentView(); if (!v || !st.full) return; v.classList.add("ctl-on"); burgerTouch(); updateDock(); }
    function closeMenu() { var v = currentView(); if (!v) return; v.classList.remove("ctl-on"); burgerTouch(); updateDock(); }
    function toggleMenu() { var v = currentView(); if (v && v.classList.contains("ctl-on")) closeMenu(); else openMenu(); }
    function menuOpen() { var v = currentView(); return !!(v && v.classList.contains("ctl-on")); }
    window.addEventListener("resize", onTurn);
    window.addEventListener("orientationchange", onTurn);
    if (window.visualViewport) { window.visualViewport.addEventListener("resize", function () { layoutFull(); placeCursor(); }); window.visualViewport.addEventListener("scroll", layoutFull); }
    document.addEventListener("fullscreenchange", function () { if (st.full && st.native && !document.fullscreenElement) exitFull(); });  // left by the system's own gesture
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && st.full && !st.kb) { if (menuOpen()) closeMenu(); else exitFull(); } });
    document.addEventListener("pointerdown", function (e) {
      if (st.full && menuOpen() && !e.target.closest(".scr-dock") && !e.target.closest(".scr-burger")) closeMenu();   // a touch anywhere else puts the menu away
    }, true);

    return { open: open, close: close, wants: wants, state: function () { return st.view; } };
  }

  var api = { create: create, wants: wants, headline: headline, leftText: leftText, PRESETS: PRESETS, LADDER: LADDER, preset: preset, nextPreset: nextPreset, adapt: adapt, networkWord: networkWord,
              pointToScreen: pointToScreen, screenToLocal: screenToLocal, ZMAX: ZMAX, FULL_WIN: FULL_WIN, FRAME0: FRAME0, fullW: fullW, fullWin: fullWin, winH: winH, seenRect: seenRect, windowTransform: windowTransform, pcmToFloat: pcmToFloat, planSound: planSound, clampWin: clampWin, zoomWin: zoomWin, panWin: panWin, winToScreen: winToScreen, screenToWin: screenToWin, followWin: followWin, sameWin: sameWin, winTransform: winTransform, frameVec: frameVec, createPinch: createPinch,
               visibleRange: visibleRange, clientToLocal: clientToLocal, contentBox: contentBox, frameOf: frameOf, accelerate: accelerate, clamp01: clamp01,
              createGestures: createGestures, createTrackpad: createTrackpad, mapKey: mapKey, wheelStep: wheelStep, KEYNAMES: KEYNAMES, REFINE: REFINE, PEEK: PEEK };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.AfpScreen = api;
})(typeof window !== "undefined" ? window : this);
