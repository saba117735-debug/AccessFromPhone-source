/* imaging.js: the picture gallery, viewer and editor. Loaded the first time a picture or a folder of pictures is opened.
 * The maths lives in imaging-core.js (tested on its own); this file is the screens: what is drawn, and what a finger or a mouse does to it.
 *
 *   AfpImg.mount(host, { mode: "gallery" | "viewer", ctx, ... }) -> { dispose(), dirty() }
 */
(function () {
  "use strict";
  var C = window.AfpImgCore;
  var MAX_PIXELS = 16e6;      // phones' canvases give up past about this many pixels
  var PREVIEW_EDGE = 2400;    // the editor works on a copy this size while you look at it, and on the full picture when saving
  var PALETTE = ["#ef4444", "#f97316", "#facc15", "#22c55e", "#06b6d4", "#3b82f6", "#a855f7", "#ec4899", "#ffffff", "#111827"];

  /* ------------------------------------------------------------------ icons */

  var ICONS = {
    crop: '<path d="M6 2v14a2 2 0 0 0 2 2h14"/><path d="M18 22V8a2 2 0 0 0-2-2H2"/>',
    rotateL: '<path d="M2.5 2v6h6"/><path d="M2.66 15.57a10 10 0 1 0 .57-8.38"/>',
    rotateR: '<path d="M21.5 2v6h-6"/><path d="M21.34 15.57a10 10 0 1 1-.57-8.38"/>',
    flipH: '<path d="M12 3v18"/><path d="M8 7 3 12l5 5V7z"/><path d="m16 7 5 5-5 5V7z"/>',
    flipV: '<path d="M3 12h18"/><path d="m7 8 5-5 5 5H7z"/><path d="m7 16 5 5 5-5H7z"/>',
    adjust: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
    filters: '<circle cx="9" cy="9" r="6"/><circle cx="15" cy="9" r="6"/><circle cx="12" cy="15" r="6"/>',
    pen: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
    marker: '<path d="m9 11-6 6v3h9l3-3"/><path d="m22 12-4.6 4.6a2 2 0 0 1-2.8 0l-5.2-5.2a2 2 0 0 1 0-2.8L14 4"/>',
    arrow: '<path d="M5 19 19 5"/><path d="M9 5h10v10"/>',
    line: '<path d="M5 19 19 5"/>',
    rect: '<rect x="3" y="5" width="18" height="14" rx="2"/>',
    ellipse: '<ellipse cx="12" cy="12" rx="10" ry="7"/>',
    shapes: '<rect x="3" y="3" width="9" height="9" rx="1.5"/><circle cx="16.5" cy="16.5" r="5"/>',
    text: '<path d="M4 7V4h16v3"/><path d="M9 20h6"/><path d="M12 4v16"/>',
    blur: '<path d="M12 2.7 5.6 9.1a9 9 0 1 0 12.8 0z"/>',
    resize: '<path d="M15 3h6v6"/><path d="M9 21H3v-6"/><path d="m21 3-7 7"/><path d="m3 21 7-7"/>',
    move: '<path d="M5 9l-3 3 3 3M9 5l3-3 3 3M15 19l-3 3-3-3M19 9l3 3-3 3M2 12h20M12 2v20"/>',
    undo: '<path d="M3 7v6h6"/><path d="M21 17a9 9 0 0 0-9-9 9 9 0 0 0-6 2.3L3 13"/>',
    redo: '<path d="M21 7v6h-6"/><path d="M3 17a9 9 0 0 1 9-9 9 9 0 0 1 6 2.3l3 2.7"/>',
    eye: '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8S1 12 1 12z"/><circle cx="12" cy="12" r="3"/>',
    save: '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><path d="M17 21v-8H7v8M7 3v5h8"/>',
    download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
    share: '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="m8.6 13.5 6.8 4M15.4 6.5l-6.8 4"/>',
    info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
    grid: '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>',
    x: '<path d="M18 6 6 18M6 6l12 12"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    minus: '<path d="M5 12h14"/>',
    fit: '<path d="M8 3H5a2 2 0 0 0-2 2v3M21 8V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3M16 21h3a2 2 0 0 0 2-2v-3"/>',
    trash: '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/>',
    copy: '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
    front: '<path d="m12 2 10 6-10 6L2 8z"/><path d="m2 14 10 6 10-6"/>',
    chevL: '<path d="m15 18-6-6 6-6"/>',
    chevR: '<path d="m9 18 6-6-6-6"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-5-5L5 21"/>',
  };
  function icon(name) {
    var s = document.createElement("span");
    s.className = "ico"; s.setAttribute("aria-hidden", "true");
    s.innerHTML = '<svg viewBox="0 0 24 24" focusable="false">' + (ICONS[name] || "") + "</svg>";  // fixed strings above, never user text
    return s;
  }

  /* ------------------------------------------------------------------ small shared pieces */

  var dirSep = function (folder) { return folder.indexOf("\\") >= 0 ? "\\" : "/"; };
  var joinPath = function (folder, name) { return folder.replace(/[\\/]+$/, "") + dirSep(folder) + name; };
  var when = function (secs) { var d = new Date(secs * 1000); return isNaN(d) ? "" : d.toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" }) + ", " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); };

  /** Thumbnails, fetched a few at a time with the access token and kept for the next look. */
  var Thumbs = {
    cache: new Map(), waiting: [], running: 0, limit: 6,
    get: function (ctx, item, size) {
      var key = item.path + "|" + item.modified + "|" + size;
      if (this.cache.has(key)) return this.cache.get(key);
      var self = this;
      var promise = new Promise(function (resolve) {
        self.waiting.push(function (done) {
          ctx.get("/thumb?path=" + encodeURIComponent(item.path) + "&size=" + size)
            .then(function (res) { return res.blob(); })
            .then(function (blob) { resolve(URL.createObjectURL(blob)); })
            .catch(function () { resolve(""); })   // no thumbnail (an icon, a damaged file): the tile stays plain
            .then(done);
        });
        self.pump();
      });
      this.cache.set(key, promise);
      if (this.cache.size > 600) { var oldest = this.cache.keys().next().value; var old = this.cache.get(oldest); this.cache.delete(oldest); old.then(function (u) { if (u) URL.revokeObjectURL(u); }); }
      return promise;
    },
    pump: function () {
      var self = this;
      while (this.running < this.limit && this.waiting.length) {
        this.running++;
        this.waiting.shift()(function () { self.running--; self.pump(); });
      }
    },
  };

  /** A small modal with a question and buttons. Resolves to the index of the button that was chosen (the first is the safe one). */
  function ask(ctx, host, title, text, buttons) {
    var h = ctx.h;
    return new Promise(function (resolve) {
      var close = function (n) { box.classList.add("out"); setTimeout(function () { box.remove(); }, 180); resolve(n); };
      var box = h("div", { class: "im-modal", role: "alertdialog", "aria-modal": "true", "aria-label": title },
        h("div", { class: "im-card" }, h("h3", { text: title }), text ? h("p", { text: text }) : null,
          h("div", { class: "im-actions" }, buttons.map(function (b, i) {
            return h("button", { class: "btn" + (b.primary ? " primary" : "") + (b.danger ? " danger" : ""), type: "button", onclick: function () { close(i); } }, b.label);
          }))));
      box.addEventListener("keydown", function (e) { if (e.key === "Escape") close(0); });
      host.append(box);
      var first = box.querySelector("button"); if (first) first.focus();
    });
  }

  /** A colour row: the palette and a free choice. */
  function colours(ctx, current, onPick) {
    var h = ctx.h, row = h("div", { class: "im-colors", role: "group", "aria-label": "Colour" });
    var paint = function (value) { Array.prototype.forEach.call(row.children, function (b) { if (b.dataset.c) b.setAttribute("aria-pressed", String(b.dataset.c.toLowerCase() === String(value).toLowerCase())); }); };
    PALETTE.forEach(function (c) {
      row.append(h("button", { class: "im-swatch", type: "button", "data-c": c, "aria-label": c, style: "--c:" + c, onclick: function () { paint(c); onPick(c); } }));
    });
    var custom = h("input", { class: "im-custom", type: "color", "aria-label": "Another colour", value: /^#[0-9a-f]{6}$/i.test(current) ? current : "#ef4444" });
    custom.addEventListener("input", function () { paint(custom.value); onPick(custom.value); });
    row.append(h("label", { class: "im-swatch im-rainbow", title: "Another colour" }, custom));  // a rainbow circle; the picker itself is invisible on top of it
    paint(current);
    row.setValue = paint;
    return row;
  }

  function slider(ctx, label, min, max, value, onInput, onCommit, fmt) {
    var h = ctx.h, out = h("output", { text: fmt ? fmt(value) : String(value) });
    var input = h("input", { type: "range", min: String(min), max: String(max), value: String(value), "aria-label": label });
    input.addEventListener("input", function () { out.textContent = fmt ? fmt(Number(input.value)) : input.value; onInput(Number(input.value)); });
    input.addEventListener("change", function () { if (onCommit) onCommit(Number(input.value)); });
    var row = h("label", { class: "im-slider" }, h("span", { text: label }), input, out);
    row.set = function (v) { input.value = String(v); out.textContent = fmt ? fmt(v) : String(v); };
    return row;
  }

  function seg(ctx, label, options, current, onPick) {
    var h = ctx.h, wrap = h("div", { class: "seg", role: "group", "aria-label": label });
    options.forEach(function (o) {
      var b = h("button", { type: "button", "aria-pressed": String(o[0] === current) }, o[1]);
      b.onclick = function () { Array.prototype.forEach.call(wrap.children, function (c) { c.setAttribute("aria-pressed", "false"); }); b.setAttribute("aria-pressed", "true"); onPick(o[0]); };
      wrap.append(b);
    });
    return wrap;
  }

  function iconButton(ctx, name, label, run, extra) {
    var b = ctx.h("button", { class: "iconbtn im-btn" + (extra ? " " + extra : ""), type: "button", "aria-label": label, title: label }, icon(name));
    b.addEventListener("click", function () { ctx.haptic("tick"); run(b); });
    return b;
  }

  /* ================================================================== the gallery */

  function mountGallery(host, o) {
    var ctx = o.ctx, h = ctx.h, data = o.data;
    var st = { sort: "new", q: "", size: 160, dense: false };
    var observer = null, disposed = false;

    var title = h("div", { class: "cv-title" }, h("b", { text: data.name }), h("span", { class: "cv-path", text: data.folder, title: data.folder }));
    var count = h("span", { class: "im-count" });
    var search = h("input", { class: "text-input sm", type: "search", placeholder: "Search by name", "aria-label": "Search pictures by name" });
    var sort = h("select", { class: "im-select", "aria-label": "Sort pictures" },
      [["new", "Newest first"], ["old", "Oldest first"], ["name", "Name"], ["big", "Largest first"]].map(function (s) { return h("option", { value: s[0], text: s[1] }); }));
    var grid = h("div", { class: "gl-grid", role: "list" });
    var scroller = h("div", { class: "gl-scroll" }, grid);
    var notice = h("p", { class: "im-note", hidden: true });

    var head = h("header", { class: "cv-head" },
      h("button", { class: "iconbtn", type: "button", "aria-label": "Close", title: "Close", onclick: function () { ctx.close(); } }, icon("x")), title, count);
    var bar = h("div", { class: "gl-bar" }, h("label", { class: "gl-search" }, icon("search"), search), sort,
      iconButton(ctx, "grid", "Bigger or smaller pictures", function () { st.dense = !st.dense; paintGrid(); }));
    host.replaceChildren(head, bar, notice, scroller);

    function items() {
      var q = st.q.trim().toLowerCase();
      var list = data.images.filter(function (i) { return !q || i.name.toLowerCase().indexOf(q) >= 0; });
      var by = { new: function (a, b) { return b.modified - a.modified; }, old: function (a, b) { return a.modified - b.modified; },
                 name: function (a, b) { return a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: "base" }); }, big: function (a, b) { return b.size - a.size; } }[st.sort];
      return list.sort(by);
    }

    function paintGrid() {
      if (observer) observer.disconnect();
      var list = items();
      count.textContent = st.q ? list.length + " of " + data.total : data.total + (data.total === 1 ? " picture" : " pictures");
      notice.hidden = !data.truncated;
      if (data.truncated) notice.textContent = "Showing the newest " + data.images.length + " of " + data.total + " pictures. Use another folder, or search by name.";
      grid.style.setProperty("--tile", (st.dense ? 84 : 128) + "px");
      if (!list.length) {
        grid.replaceChildren(h("div", { class: "gl-empty" }, icon("image"), h("p", { text: data.total ? "No picture has that name." : "There are no pictures in this folder." })));
        return;
      }
      observer = new IntersectionObserver(function (entries) {
        entries.forEach(function (e) {
          if (!e.isIntersecting) return;
          observer.unobserve(e.target);
          var tile = e.target;
          Thumbs.get(ctx, tile._item, st.dense ? 160 : 256).then(function (url) {
            if (disposed || !url || !tile.isConnected) return;
            var img = h("img", { src: url, alt: "", draggable: "false", decoding: "async" });
            img.onload = function () { tile.classList.add("ready"); };
            tile.prepend(img);
          });
        });
      }, { root: scroller, rootMargin: "400px" });
      grid.replaceChildren.apply(grid, list.map(function (item, n) {
        var tile = h("button", { class: "gl-tile", type: "button", role: "listitem", title: item.name + "  ·  " + C.fmtBytes(item.size), "aria-label": item.name,
          onclick: function () { ctx.haptic("light"); ctx.openImage(item.path); } },
          h("span", { class: "gl-ph" }, icon("image")), h("span", { class: "gl-name", text: item.name }));
        tile._item = item;
        if (n < 80) tile.style.animationDelay = Math.min(n, 24) * 12 + "ms";
        observer.observe(tile);
        return tile;
      }));
    }
    search.addEventListener("input", function () { st.q = search.value; paintGrid(); });
    sort.addEventListener("change", function () { st.sort = sort.value; scroller.scrollTop = 0; paintGrid(); });
    paintGrid();

    return { dispose: function () { disposed = true; if (observer) observer.disconnect(); }, dirty: function () { return false; } };
  }

  /* ================================================================== the viewer */

  function mountViewer(host, o) {
    var ctx = o.ctx, h = ctx.h, info = o.info, disposed = false;
    var v = { view: { scale: 1, tx: 0, ty: 0 }, fit: 1, w: 0, h: 0, blob: null, url: "", siblings: null, index: -1, panel: null, editor: null };

    var img = h("img", { class: "iv-img", alt: info.name, draggable: "false" });
    var stage = h("div", { class: "iv-stage", tabindex: "0", "aria-label": "Picture: use arrow keys for the next or previous one, plus and minus to zoom" }, img);
    var spin = h("div", { class: "iv-spin" }, h("span", { class: "spin" }), h("span", { text: "Loading…" }));
    stage.append(spin);
    var sub = h("span", { class: "cv-path" });
    var head = h("header", { class: "cv-head" },
      h("button", { class: "iconbtn", type: "button", "aria-label": "Close", title: "Close", onclick: function () { ctx.close(); } }, icon("x")),
      h("div", { class: "cv-title" }, h("b", { text: info.name, title: info.path }), sub));
    var strip = h("div", { class: "iv-strip", role: "list", "aria-label": "Other pictures in this folder", hidden: true });
    var zoomBox = h("div", { class: "iv-zoom" },
      iconButton(ctx, "plus", "Zoom in", function () { zoomBy(1.5); }), iconButton(ctx, "minus", "Zoom out", function () { zoomBy(1 / 1.5); }), iconButton(ctx, "fit", "Fit the screen", function () { fitNow(true); }));
    var prev = iconButton(ctx, "chevL", "Previous picture", function () { go(-1); }, "iv-nav iv-prev"), next = iconButton(ctx, "chevR", "Next picture", function () { go(1); }, "iv-nav iv-next");
    prev.hidden = next.hidden = true;
    stage.append(zoomBox, prev, next);

    var canEdit = info.editable_image !== false && !info.reason;
    var actions = h("div", { class: "iv-actions" },
      iconButton(ctx, "grid", "All the pictures in this folder", function () { ctx.openGallery(info.folder); }, "iv-act"),
      iconButton(ctx, "info", "Details", function () { showInfo(); }, "iv-act"),
      typeof navigator.share === "function" && typeof File === "function" ? iconButton(ctx, "share", "Share", function () { share(); }, "iv-act") : null,
      iconButton(ctx, "download", "Download", function () { download(); }, "iv-act"),
      canEdit ? h("button", { class: "btn primary iv-edit", type: "button", onclick: function () { ctx.haptic("light"); edit(); } }, icon("pen"), "Edit") : null);
    host.replaceChildren(head, stage, strip, actions);

    function boxSize() { var r = stage.getBoundingClientRect(); return { w: r.width, h: r.height }; }
    function paint(animate) {
      img.style.transition = animate ? "transform 0.22s cubic-bezier(.2,.8,.2,1)" : "none";
      img.style.transform = "translate(" + v.view.tx + "px," + v.view.ty + "px) scale(" + v.view.scale + ")";
    }
    function limits() { return { min: v.fit, max: Math.max(v.fit * 8, 4) }; }
    function atFit() { return v.view.scale <= v.fit * 1.02; }
    function fitNow(animate) { var b = boxSize(); if (!v.w) return; v.fit = C.fitScale(v.w, v.h, b.w, b.h); v.view = C.fitView(v.w, v.h, b.w, b.h); paint(animate); }
    function setView(view, animate) { var b = boxSize(); v.view = C.clampView(view, v.w, v.h, b.w, b.h); paint(animate); }
    function zoomBy(f, cx, cy, animate) {
      var b = boxSize(), L = limits();
      setView(C.zoomAt(v.view, f, cx == null ? b.w / 2 : cx, cy == null ? b.h / 2 : cy, L.min, L.max), animate !== false);
    }

    /* what a finger or mouse does: drag to move, pinch or wheel to zoom, double tap to zoom in and out, swipe to the next picture */
    var pts = new Map(), gesture = null, lastTap = null;
    stage.addEventListener("pointerdown", function (e) {
      if (e.target.closest(".iv-zoom, .iv-nav")) return;
      stage.setPointerCapture(e.pointerId); stage.focus({ preventScroll: true });
      pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
      var r = stage.getBoundingClientRect();
      if (pts.size === 2) {
        var a = Array.from(pts.values());
        gesture = { kind: "pinch", dist: Math.hypot(a[0].x - a[1].x, a[0].y - a[1].y), view: Object.assign({}, v.view), mid: { x: (a[0].x + a[1].x) / 2 - r.left, y: (a[0].y + a[1].y) / 2 - r.top } };
      } else gesture = { kind: "drag", x: e.clientX, y: e.clientY, view: Object.assign({}, v.view), moved: 0, t: Date.now() };
    });
    stage.addEventListener("pointermove", function (e) {
      if (!pts.has(e.pointerId) || !gesture) return;
      pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
      var r = stage.getBoundingClientRect(), L = limits();
      if (gesture.kind === "pinch" && pts.size >= 2) {
        var a = Array.from(pts.values()), d = Math.hypot(a[0].x - a[1].x, a[0].y - a[1].y) || 1;
        var mid = { x: (a[0].x + a[1].x) / 2 - r.left, y: (a[0].y + a[1].y) / 2 - r.top };
        var z = C.zoomAt(gesture.view, d / gesture.dist, gesture.mid.x, gesture.mid.y, L.min * 0.9, L.max);
        z.tx += mid.x - gesture.mid.x; z.ty += mid.y - gesture.mid.y;   // and follow the fingers
        setView(z);
      } else if (gesture.kind === "drag") {
        var dx = e.clientX - gesture.x, dy = e.clientY - gesture.y;
        gesture.moved = Math.max(gesture.moved, Math.hypot(dx, dy));
        if (atFit()) { img.style.transition = "none"; img.style.transform = "translate(" + (gesture.view.tx + dx * 0.6) + "px," + gesture.view.ty + "px) scale(" + v.view.scale + ")"; }  // a little give, for a swipe
        else setView({ scale: gesture.view.scale, tx: gesture.view.tx + dx, ty: gesture.view.ty + dy });
      }
    });
    function release(e) {
      if (!pts.has(e.pointerId)) return;
      var g = gesture; pts.delete(e.pointerId);
      if (pts.size > 0) { gesture = null; return; }
      gesture = null;
      if (!g) return;
      if (g.kind === "pinch") { if (v.view.scale < v.fit) fitNow(true); return; }
      var dx = e.clientX - g.x, dy = e.clientY - g.y;
      if (atFit() && Math.abs(dx) > 70 && Math.abs(dx) > Math.abs(dy) * 1.5 && Date.now() - g.t < 700) { paint(true); go(dx < 0 ? 1 : -1); return; }
      if (atFit()) paint(true);
      if (g.moved < 8) {                      // a tap; two quick ones zoom
        var r = stage.getBoundingClientRect(), now = Date.now();
        if (lastTap && now - lastTap.t < 320 && Math.hypot(e.clientX - lastTap.x, e.clientY - lastTap.y) < 30) {
          lastTap = null; ctx.haptic("tick");
          if (atFit()) zoomBy(Math.max(1, v.fit * 2.5) / v.view.scale, e.clientX - r.left, e.clientY - r.top);  // to full size, or 2.5 times the fitted size if that is bigger
          else fitNow(true);
        } else lastTap = { t: now, x: e.clientX, y: e.clientY };
      }
    }
    stage.addEventListener("pointerup", release); stage.addEventListener("pointercancel", release);
    stage.addEventListener("wheel", function (e) {
      e.preventDefault();
      var r = stage.getBoundingClientRect();
      zoomBy(Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top, false);
    }, { passive: false });
    stage.addEventListener("keydown", function (e) {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      var k = e.key;
      if (k === "ArrowRight") { e.preventDefault(); go(1); }
      else if (k === "ArrowLeft") { e.preventDefault(); go(-1); }
      else if (k === "+" || k === "=") zoomBy(1.5);
      else if (k === "-") zoomBy(1 / 1.5);
      else if (k === "0") fitNow(true);
      else if ((k === "e" || k === "E") && canEdit) edit();
      else if (k === "i" || k === "I") showInfo();
    });
    var resizer = typeof ResizeObserver === "function" ? new ResizeObserver(function () { if (v.w && (atFit() || !v.fit)) fitNow(false); else if (v.w) setView(v.view); }) : null;
    if (resizer) resizer.observe(stage);

    function go(step) {
      if (!v.siblings || v.siblings.length < 2) return;
      var n = v.index + step;
      if (n < 0 || n >= v.siblings.length) { ctx.toast(step > 0 ? "That is the last picture" : "That is the first picture"); ctx.haptic("warning"); return; }
      ctx.openImage(v.siblings[n].path, "view", { replace: true });
    }

    function showSiblings(list) {
      v.siblings = list.slice().sort(function (a, b) { return b.modified - a.modified; });
      v.index = v.siblings.findIndex(function (i) { return i.path === info.path; });
      if (v.index < 0) return;
      var n = v.siblings.length;
      sub.textContent = (v.index + 1) + " of " + n + (info.width ? "  ·  " + info.width + "×" + info.height : "") + "  ·  " + C.fmtBytes(info.size);
      prev.hidden = next.hidden = n < 2;
      if (n < 2) return;
      strip.hidden = false;
      var from = Math.max(0, v.index - 40), to = Math.min(n, v.index + 41), tiles = [];
      for (var i = from; i < to; i++) {
        (function (item, at) {
          var t = h("button", { class: "iv-thumb", type: "button", role: "listitem", "aria-label": item.name, "aria-current": String(at === v.index), onclick: function () { if (at !== v.index) ctx.openImage(item.path, "view", { replace: true }); } });
          Thumbs.get(ctx, item, 96).then(function (url) { if (!disposed && url) t.append(h("img", { src: url, alt: "", draggable: "false" })); });
          tiles.push(t);
        })(v.siblings[i], i);
      }
      strip.replaceChildren.apply(strip, tiles);
      var cur = strip.querySelector('[aria-current="true"]');
      if (cur) strip.scrollLeft = cur.offsetLeft - strip.clientWidth / 2 + cur.clientWidth / 2;
    }

    function showInfo() {
      var row = function (k, val) { return h("div", { class: "im-row" }, h("span", { text: k }), h("b", { text: val })); };
      var modal = h("div", { class: "im-modal", role: "dialog", "aria-modal": "true", "aria-label": "Picture details", onclick: function (e) { if (e.target === modal) modal.remove(); } },
        h("div", { class: "im-card" }, h("h3", { text: info.name }),
          row("Size", info.width ? info.width + " × " + info.height + " pixels" : "Not known"), row("File", C.fmtBytes(info.size)),
          row("Type", (info.mime || "").replace("image/", "").toUpperCase()), row("Changed", when(info.modified)), row("Folder", info.folder),
          h("div", { class: "im-actions" },
            h("button", { class: "btn", type: "button", onclick: function () { ctx.copy(info.path); } }, icon("copy"), "Copy the path"),
            h("button", { class: "btn primary", type: "button", onclick: function () { modal.remove(); } }, "Done"))));
      host.append(modal);
    }

    function download() { if (v.blob) ctx.download(v.blob, info.name); }
    function share() {
      if (!v.blob) return;
      var file = new File([v.blob], info.name, { type: v.blob.type || info.mime });
      if (navigator.canShare && !navigator.canShare({ files: [file] })) { download(); return; }
      navigator.share({ files: [file], title: info.name }).catch(function () { /* cancelled */ });
    }

    function edit() {
      if (!v.blob) return;
      stopViewer();
      v.editor = mountEditor(host, { ctx: ctx, info: info, blob: v.blob, onExit: function (saved) {
        v.editor = null;
        if (saved) ctx.openImage(saved.path, "view", { replace: true }); else ctx.openImage(info.path, "view", { replace: true });
      } });
    }
    function stopViewer() { if (resizer) resizer.disconnect(); }

    sub.textContent = (info.width ? info.width + "×" + info.height + "  ·  " : "") + C.fmtBytes(info.size);
    if (info.reason) { stage.replaceChildren(h("div", { class: "cv-empty" }, icon("image"), h("p", { text: info.reason }))); zoomBox.remove(); }
    else {
      ctx.get("/file/raw?path=" + encodeURIComponent(info.path)).then(function (r) { return r.blob(); }).then(function (blob) {
        if (disposed) return;
        v.blob = blob; v.url = URL.createObjectURL(blob);
        img.onload = function () {
          if (disposed) return;
          v.w = img.naturalWidth || info.width || 1; v.h = img.naturalHeight || info.height || 1;
          img.style.width = v.w + "px"; img.style.height = v.h + "px";
          spin.remove(); fitNow(false); img.classList.add("ready");
        };
        img.onerror = function () { spin.replaceChildren(h("p", { class: "err", text: "This picture can't be shown." })); };
        img.src = v.url;
      }).catch(function (e) { spin.replaceChildren(h("p", { class: "err", text: e.message || "Couldn't load the picture." })); });
      ctx.get("/images?path=" + encodeURIComponent(info.folder)).then(function (r) { return r.json(); }).then(function (d) { if (!disposed) showSiblings(d.images || []); }).catch(function () { /* the picture still works without its neighbours */ });
    }
    if (o.startEditing && canEdit) {
      // straight into the editor ("edit that photo"): wait for the picture, then open it
      var wait = setInterval(function () { if (disposed) { clearInterval(wait); return; } if (v.blob) { clearInterval(wait); edit(); } }, 80);
    }

    return {
      dispose: function () {
        disposed = true; stopViewer();
        if (v.editor) v.editor.dispose();
        if (v.url) URL.revokeObjectURL(v.url);
      },
      dirty: function () { return !!(v.editor && v.editor.dirty()); },
    };
  }

  /* ================================================================== the editor */

  function mountEditor(host, o) {
    var ctx = o.ctx, h = ctx.h, info = o.info, disposed = false;
    var doc = { path: info.path, name: info.name, folder: info.folder, sha: info.sha || null, ext: C.extOf(info.name), saveable: info.saves_as_is !== false };
    var S = {
      base: null, baseId: 0, adj: C.params(), objs: [], sel: -1, tool: "move", ops: [], crop: null, compare: false,
      view: { scale: 1, tx: 0, ty: 0 }, notice: "",
      opt: { pen: { color: "#ef4444", size: 6 }, marker: { color: "#facc15", size: 26 }, shape: { kind: "arrow", color: "#ef4444", size: 5, fill: false },
             text: { color: "#ffffff", size: 34, bold: true, bg: false }, blur: { mode: "pixelate", amount: 14 }, aspect: "free", flip: false },
    };
    var hist = C.History(30), savedAt = 0, preset = "none";
    var cache = { id: -1, w: 0, small: null, adjKey: null, adjusted: null, scene: document.createElement("canvas"), pv: 1 };
    var drawing = null, raf = 0, sceneDirty = true;

    /* -------- the screen */
    var stage = h("canvas", { class: "ed-canvas", tabindex: "0", "aria-label": "The picture being edited" });
    var spin = h("div", { class: "iv-spin" }, h("span", { class: "spin" }), h("span", { text: "Opening the picture…" }));
    var holder = h("div", { class: "ed-stage" }, stage, spin);
    var panel = h("div", { class: "ed-panel" });
    var toolbar = h("div", { class: "ed-tools", role: "toolbar", "aria-label": "Editing tools" });
    var undoBtn = iconButton(ctx, "undo", "Undo", function () { stepHistory(-1); });
    var redoBtn = iconButton(ctx, "redo", "Redo", function () { stepHistory(1); });
    var compareBtn = ctx.h("button", { class: "iconbtn im-btn", type: "button", "aria-label": "Hold to see the picture without colour changes or notes", title: "Hold to compare" }, icon("eye"));
    var title = h("b", { text: "Edit " + doc.name });
    var state = h("span", { class: "chip" });
    var saveBtn = h("button", { class: "btn primary ed-save", type: "button", onclick: function () { ctx.haptic("light"); openSave(); } }, icon("save"), "Save");
    var head = h("header", { class: "cv-head ed-head" },
      h("button", { class: "iconbtn", type: "button", "aria-label": "Close the editor", title: "Close", onclick: function () { tryExit(); } }, icon("x")),
      h("div", { class: "cv-title" }, title, state), undoBtn, redoBtn, compareBtn, saveBtn);
    var zoomBox = h("div", { class: "iv-zoom" },
      iconButton(ctx, "plus", "Zoom in", function () { zoomBy(1.5); }), iconButton(ctx, "minus", "Zoom out", function () { zoomBy(1 / 1.5); }), iconButton(ctx, "fit", "Fit the screen", function () { fitAll(true); }));
    holder.append(zoomBox);
    host.replaceChildren(head, holder, panel, toolbar);
    var g = stage.getContext("2d");

    var TOOLS = [["move", "move", "Select"], ["crop", "crop", "Crop"], ["rotate", "rotateR", "Rotate"], ["adjust", "adjust", "Adjust"], ["filters", "filters", "Filters"],
                 ["pen", "pen", "Draw"], ["shape", "shapes", "Shapes"], ["text", "text", "Text"], ["blur", "blur", "Hide"], ["resize", "resize", "Resize"]];
    TOOLS.forEach(function (t) {
      var b = h("button", { class: "ed-tool", type: "button", "data-tool": t[0], "aria-pressed": "false", onclick: function () { ctx.haptic("tick"); setTool(t[0]); } }, icon(t[1]), h("span", { text: t[2] }));
      toolbar.append(b);
    });

    /* -------- state and history */
    var unit = function () { return Math.max(S.base.width, S.base.height) / 1000; };
    var snapshot = function () { return { base: S.base, adj: Object.assign({}, S.adj), objs: C.clone(S.objs), ops: S.ops.slice(), preset: preset }; };
    function commit(op) { if (op) S.ops.push(op); hist.push(snapshot()); sync(); }
    function restore(s) {
      var shape = s.base !== S.base;  // undoing a crop or a turn changes the picture's shape: show all of it again. Anything else leaves your zoom alone.
      if (shape) { S.base = s.base; S.baseId++; }
      S.adj = Object.assign({}, s.adj); S.objs = C.clone(s.objs); S.ops = s.ops.slice(); preset = s.preset; S.sel = -1; S.crop = null;
      if (S.tool === "crop") S.tool = "move";
      sceneDirty = true; if (shape) fitAll(); buildPanel(); sync(); render();
    }
    function stepHistory(n) {
      if (n < 0 ? !hist.canUndo() : !hist.canRedo()) return;
      restore(n < 0 ? hist.undo() : hist.redo());
    }
    var dirty = function () { return !disposed && hist.index() !== savedAt; };  // once it has been closed (saved, or discarded on purpose) there is nothing left to lose
    function sync() {
      undoBtn.disabled = !hist.canUndo(); redoBtn.disabled = !hist.canRedo();
      state.textContent = dirty() ? "Unsaved" : "No changes";
      state.classList.toggle("warn", dirty());
      saveBtn.disabled = !S.base;
      Array.prototype.forEach.call(toolbar.children, function (b) { b.setAttribute("aria-pressed", String(b.dataset.tool === S.tool || (b.dataset.tool === "pen" && (S.tool === "marker")) || (b.dataset.tool === "shape" && S.tool === "shape"))); });
      window.onbeforeunload = dirty() ? function () { return "You have unsaved changes to this picture."; } : null;
    }

    /* -------- drawing the scene: the picture with its colours, then everything drawn on it */
    function canvas(w, hh, read) { var c = document.createElement("canvas"); c.width = w; c.height = hh; if (read) c.getContext("2d", { willReadFrequently: true }); return c; }

    function drawObject(c2, ob, k) {
      c2.save(); c2.lineCap = "round"; c2.lineJoin = "round";
      var size = (ob.size || 4) * k;
      if (ob.type === "pen" || ob.type === "marker") {
        if (ob.type === "marker") { c2.globalAlpha = 0.4; c2.lineCap = "butt"; }
        c2.strokeStyle = ob.color; c2.lineWidth = size; c2.beginPath();
        ob.pts.forEach(function (p, i) { if (i) c2.lineTo(p[0] * k, p[1] * k); else c2.moveTo(p[0] * k, p[1] * k); });
        if (ob.pts.length === 1) c2.lineTo(ob.pts[0][0] * k + 0.01, ob.pts[0][1] * k);
        c2.stroke();
      } else if (ob.type === "line" || ob.type === "arrow") {
        c2.strokeStyle = ob.color; c2.fillStyle = ob.color; c2.lineWidth = size;
        var x1 = ob.x1 * k, y1 = ob.y1 * k, x2 = ob.x2 * k, y2 = ob.y2 * k;
        var ang = Math.atan2(y2 - y1, x2 - x1), head = ob.type === "arrow" ? Math.max(size * 3.4, 12 * k) : 0;
        c2.beginPath(); c2.moveTo(x1, y1); c2.lineTo(x2 - Math.cos(ang) * head * 0.6, y2 - Math.sin(ang) * head * 0.6); c2.stroke();
        if (head) { c2.beginPath(); c2.moveTo(x2, y2); c2.lineTo(x2 - head * Math.cos(ang - 0.42), y2 - head * Math.sin(ang - 0.42)); c2.lineTo(x2 - head * Math.cos(ang + 0.42), y2 - head * Math.sin(ang + 0.42)); c2.closePath(); c2.fill(); }
      } else if (ob.type === "rect" || ob.type === "ellipse") {
        c2.strokeStyle = ob.color; c2.fillStyle = ob.color; c2.lineWidth = size;
        c2.beginPath();
        if (ob.type === "rect") c2.rect(ob.x * k, ob.y * k, ob.w * k, ob.h * k);
        else c2.ellipse((ob.x + ob.w / 2) * k, (ob.y + ob.h / 2) * k, Math.max(ob.w * k / 2, 0.5), Math.max(ob.h * k / 2, 0.5), 0, 0, Math.PI * 2);
        if (ob.fill) c2.fill(); else c2.stroke();
      } else if (ob.type === "text") {
        var px = ob.size * k, lines = String(ob.text || "").split("\n");
        c2.font = (ob.bold ? "700 " : "500 ") + px + "px system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"; c2.textBaseline = "top";
        var widest = 0; lines.forEach(function (l) { widest = Math.max(widest, c2.measureText(l).width); });
        var pad = ob.bg ? px * 0.28 : 0, lh = px * 1.22;
        ob.w = (widest + pad * 2) / k; ob.h = (lines.length * lh + pad * 2 - (lh - px)) / k;  // measured here, so it can be selected
        if (ob.bg) { c2.fillStyle = lightness(ob.color) > 150 ? "rgba(0,0,0,0.62)" : "rgba(255,255,255,0.82)"; roundRect(c2, ob.x * k, ob.y * k, ob.w * k, ob.h * k, px * 0.25); c2.fill(); }
        else { c2.shadowColor = lightness(ob.color) > 150 ? "rgba(0,0,0,0.55)" : "rgba(255,255,255,0.6)"; c2.shadowBlur = px * 0.12; }
        c2.fillStyle = ob.color;
        lines.forEach(function (l, i) { c2.fillText(l, ob.x * k + pad, ob.y * k + pad + i * lh); });
      } else if (ob.type === "blur") {
        var rx = Math.max(0, Math.round(ob.x * k)), ry = Math.max(0, Math.round(ob.y * k));
        var rw = Math.min(c2.canvas.width - rx, Math.round(ob.w * k)), rh = Math.min(c2.canvas.height - ry, Math.round(ob.h * k));
        if (rw >= 2 && rh >= 2) {
          var f = Math.max(2, Math.round((ob.amount || 14) * k)), tw = Math.max(1, Math.ceil(rw / f)), th = Math.max(1, Math.ceil(rh / f)), t = canvas(tw, th), tc = t.getContext("2d");
          tc.imageSmoothingEnabled = true; tc.drawImage(c2.canvas, rx, ry, rw, rh, 0, 0, tw, th);
          c2.imageSmoothingEnabled = ob.mode === "blur"; c2.imageSmoothingQuality = "high";
          c2.drawImage(t, 0, 0, tw, th, rx, ry, rw, rh);
        }
      }
      c2.restore();
    }
    function lightness(hex) { var n = parseInt(String(hex).replace("#", ""), 16) || 0; return 0.299 * (n >> 16) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255); }
    function roundRect(c2, x, y, w, hh, r) { c2.beginPath(); c2.moveTo(x + r, y); c2.arcTo(x + w, y, x + w, y + hh, r); c2.arcTo(x + w, y + hh, x, y + hh, r); c2.arcTo(x, y + hh, x, y, r); c2.arcTo(x, y, x + w, y, r); c2.closePath(); }

    /** The current picture (colours and notes) at scale k of the full size, drawn onto `target`. */
    function composeInto(target, source, objs, k) {
      var c2 = target.getContext("2d");
      c2.clearRect(0, 0, target.width, target.height);
      c2.imageSmoothingEnabled = true; c2.imageSmoothingQuality = "high";
      c2.drawImage(source, 0, 0, target.width, target.height);
      objs.forEach(function (ob) { drawObject(c2, ob, k); });
    }
    function rebuildScene() {
      sceneDirty = false;
      var b = S.base, pv = Math.min(1, PREVIEW_EDGE / Math.max(b.width, b.height)), w = Math.max(1, Math.round(b.width * pv)), hh = Math.max(1, Math.round(b.height * pv));
      if (cache.id !== S.baseId || cache.w !== w) {
        cache.small = canvas(w, hh, true); var sc = cache.small.getContext("2d"); sc.imageSmoothingQuality = "high"; sc.drawImage(b, 0, 0, w, hh);
        cache.id = S.baseId; cache.w = w; cache.adjKey = null;
      }
      var key = C.adjustKey(S.adj);
      if (cache.adjKey !== key) {
        cache.adjusted = canvas(w, hh, true); var ac = cache.adjusted.getContext("2d"); ac.drawImage(cache.small, 0, 0);
        if (!C.isNeutral(S.adj)) { var id = ac.getImageData(0, 0, w, hh); C.applyAdjust(id.data, S.adj); ac.putImageData(id, 0, 0); }
        cache.adjKey = key;
      }
      cache.pv = pv; cache.scene.width = w; cache.scene.height = hh;
      composeInto(cache.scene, cache.adjusted, S.objs.concat(drawing && drawing.obj ? [drawing.obj] : []), pv);
    }

    /* -------- on the screen */
    function box() { return { w: holder.clientWidth, h: holder.clientHeight }; }
    function resizeStage() {
      var b = box(), dpr = window.devicePixelRatio || 1;
      if (stage.width !== Math.round(b.w * dpr) || stage.height !== Math.round(b.h * dpr)) { stage.width = Math.round(b.w * dpr); stage.height = Math.round(b.h * dpr); stage.style.width = b.w + "px"; stage.style.height = b.h + "px"; }
    }
    function scheduleRender() { if (!raf) raf = requestAnimationFrame(function () { raf = 0; render(); }); }
    function render() {
      if (disposed || !S.base) return;
      resizeStage();
      if (sceneDirty) rebuildScene();
      var dpr = window.devicePixelRatio || 1, b = S.base, v = S.view;
      stage.dataset.size = b.width + "x" + b.height; stage.dataset.tool = S.tool;  // what the picture is now: for screen readers' sake and for tests
      g.setTransform(1, 0, 0, 1, 0, 0); g.clearRect(0, 0, stage.width, stage.height);
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      // a soft backing, so a picture with see-through parts is easy to read
      g.save(); g.translate(v.tx, v.ty); g.scale(v.scale, v.scale);
      g.fillStyle = "#0b1220"; g.fillRect(0, 0, b.width, b.height);
      g.imageSmoothingQuality = "high";
      g.drawImage(S.compare ? cache.small : cache.scene, 0, 0, b.width, b.height);
      g.restore();
      if (S.compare) return;
      overlays();
    }
    function toScreenRect(r) { var a = C.toScreen(S.view, r.x, r.y); return { x: a.x, y: a.y, w: r.w * S.view.scale, h: r.h * S.view.scale }; }
    function overlays() {
      var acc = getComputedStyle(holder).getPropertyValue("--a1").trim() || "#22d3ee";
      if (S.tool === "crop" && S.crop) {
        var s = toScreenRect(S.crop), full = toScreenRect({ x: 0, y: 0, w: S.base.width, h: S.base.height });
        g.save(); g.fillStyle = "rgba(2,6,16,0.62)";
        g.fillRect(full.x, full.y, full.w, s.y - full.y); g.fillRect(full.x, s.y + s.h, full.w, full.y + full.h - s.y - s.h);
        g.fillRect(full.x, s.y, s.x - full.x, s.h); g.fillRect(s.x + s.w, s.y, full.x + full.w - s.x - s.w, s.h);
        g.strokeStyle = "rgba(255,255,255,0.9)"; g.lineWidth = 1.5; g.strokeRect(s.x, s.y, s.w, s.h);
        g.strokeStyle = "rgba(255,255,255,0.35)"; g.lineWidth = 1; g.beginPath();
        for (var i = 1; i < 3; i++) { g.moveTo(s.x + s.w * i / 3, s.y); g.lineTo(s.x + s.w * i / 3, s.y + s.h); g.moveTo(s.x, s.y + s.h * i / 3); g.lineTo(s.x + s.w, s.y + s.h * i / 3); }
        g.stroke(); g.fillStyle = "#fff";
        [[s.x, s.y], [s.x + s.w, s.y], [s.x, s.y + s.h], [s.x + s.w, s.y + s.h]].forEach(function (p) { g.beginPath(); g.arc(p[0], p[1], 8, 0, 7); g.fill(); });
        [[s.x + s.w / 2, s.y], [s.x + s.w / 2, s.y + s.h], [s.x, s.y + s.h / 2], [s.x + s.w, s.y + s.h / 2]].forEach(function (p) { g.fillRect(p[0] - 9, p[1] - 3, 18, 6); g.fillRect(p[0] - 3, p[1] - 9, 6, 18); });
        g.restore();
      }
      var ob = S.objs[S.sel];
      if (ob && S.tool === "move") {
        var r = toScreenRect(C.boundsOf(ob));
        g.save(); g.strokeStyle = acc; g.lineWidth = 1.5; g.setLineDash([6, 4]); g.strokeRect(r.x - 3, r.y - 3, r.w + 6, r.h + 6); g.setLineDash([]); g.fillStyle = "#fff"; g.strokeStyle = acc;
        if (C.isRectLike(ob)) [[r.x, r.y], [r.x + r.w, r.y], [r.x, r.y + r.h], [r.x + r.w, r.y + r.h]].forEach(function (p) { g.beginPath(); g.arc(p[0], p[1], 8, 0, 7); g.fill(); g.stroke(); });
        g.restore();
      }
    }
    // "fitted" leaves a margin all round, so the crop handles at the picture's edge can be reached with a finger
    function fitOf(b) { return C.fitScale(S.base.width, S.base.height, Math.max(40, b.w - 28), Math.max(40, b.h - 28)); }
    function fitAll() { var b = box(); if (!S.base || !b.w) return; var s = fitOf(b); S.view = { scale: s, tx: (b.w - S.base.width * s) / 2, ty: (b.h - S.base.height * s) / 2 }; render(); }
    function zoomBy(f, cx, cy) {
      var b = box(), fit = fitOf(b);
      S.view = C.clampView(C.zoomAt(S.view, f, cx == null ? b.w / 2 : cx, cy == null ? b.h / 2 : cy, fit, Math.max(fit * 10, 6)), S.base.width, S.base.height, b.w, b.h); render();
    }

    /* -------- changing the picture itself: crop, turn, flip, resize (these make a new picture; what is drawn on it follows) */
    function replaceBase(next, op, label) {
      var before = { w: S.base.width, h: S.base.height };
      S.objs = C.transformObjects(S.objs, Object.assign({ w: before.w, h: before.h }, op));
      S.base = next; S.baseId++; S.sel = -1; S.crop = null; sceneDirty = true;
      fitAll(); commit(label);
    }
    function rotate(dir) {
      var w = S.base.width, hh = S.base.height, c = canvas(hh, w), c2 = c.getContext("2d");
      if (dir === 90) { c2.translate(hh, 0); c2.rotate(Math.PI / 2); } else { c2.translate(0, w); c2.rotate(-Math.PI / 2); }
      c2.drawImage(S.base, 0, 0); replaceBase(c, { kind: "rotate", dir: dir }, "rotate");
    }
    function flip(axis) {
      var w = S.base.width, hh = S.base.height, c = canvas(w, hh), c2 = c.getContext("2d");
      if (axis === "h") { c2.translate(w, 0); c2.scale(-1, 1); } else { c2.translate(0, hh); c2.scale(1, -1); }
      c2.drawImage(S.base, 0, 0); replaceBase(c, { kind: "flip", axis: axis }, "flip");
    }
    function applyCrop() {
      var r = S.crop; if (!r) return;
      var x = Math.round(r.x), y = Math.round(r.y), w = Math.max(1, Math.min(Math.round(r.w), S.base.width - x)), hh = Math.max(1, Math.min(Math.round(r.h), S.base.height - y));
      if (w === S.base.width && hh === S.base.height) { ctx.toast("Drag the corners first to choose what to keep."); return; }
      var c = canvas(w, hh); c.getContext("2d").drawImage(S.base, x, y, w, hh, 0, 0, w, hh);
      S.tool = "move"; replaceBase(c, { kind: "crop", x: x, y: y }, "crop"); buildPanel(); ctx.haptic("success");
    }
    function resizeTo(w, hh) {
      var cap = C.capSize(w, hh, MAX_PIXELS); w = cap.w; hh = cap.h;
      var k = w / S.base.width, src = S.base;
      while (src.width / 2 > w) { var half = canvas(Math.round(src.width / 2), Math.round(src.height / 2)), hc = half.getContext("2d"); hc.imageSmoothingQuality = "high"; hc.drawImage(src, 0, 0, half.width, half.height); src = half; }  // in steps, so a big reduction stays sharp
      var c = canvas(w, hh), c2 = c.getContext("2d"); c2.imageSmoothingQuality = "high"; c2.drawImage(src, 0, 0, w, hh);
      replaceBase(c, { kind: "scale", k: k }, "resize"); buildPanel(); ctx.haptic("success");
    }
    function startCrop() {
      S.crop = { x: 0, y: 0, w: S.base.width, h: S.base.height };
      var ratio = C.aspectRatio(S.opt.aspect, S.base.width, S.base.height, S.opt.flip);
      if (ratio) { S.crop = C.fitAspect(S.crop, ratio, { w: S.base.width, h: S.base.height }); }
      else { var m = Math.round(Math.min(S.base.width, S.base.height) * 0.06); S.crop = { x: m, y: m, w: S.base.width - 2 * m, h: S.base.height - 2 * m }; }
    }

    /* -------- the options under the picture, for the tool in use */
    function setTool(t) {
      if (t === "pen" && (S.tool === "pen" || S.tool === "marker")) t = S.tool;  // tapping Draw again keeps the pen you chose
      S.tool = t; S.sel = t === "move" ? S.sel : -1; S.crop = null; drawing = null;
      if (t === "crop") startCrop();
      if (t === "rotate" || t === "adjust" || t === "filters" || t === "resize") S.sel = -1;
      buildPanel(); sync(); render();
    }
    function row() { return h.apply(null, ["div", { class: "ed-row" }].concat(Array.prototype.slice.call(arguments))); }
    function note(text) { return h("p", { class: "ed-hint", text: text }); }

    function buildPanel() {
      var kids = [];
      var ob = S.objs[S.sel];
      if (S.tool === "move") {
        if (!ob) kids.push(note(S.objs.length ? "Tap something you added to change it, move it, or delete it. Drag empty space to move the picture; pinch to zoom." : "Choose a tool below to crop, turn, recolour, draw, add text or hide parts of the picture. Pinch to zoom."));
        else kids.push.apply(kids, selectionControls(ob));
      } else if (S.tool === "crop") {
        var aspects = C.ASPECTS.map(function (a) { return [a.id, a.name]; });
        kids.push(row(seg(ctx, "Crop shape", aspects, S.opt.aspect, function (id) { S.opt.aspect = id; startCrop(); render(); }),
          iconButton(ctx, "rotateR", "Turn the crop shape sideways", function () { S.opt.flip = !S.opt.flip; if (S.opt.aspect !== "free") { startCrop(); render(); } })));
        kids.push(row(h("button", { class: "btn", type: "button", onclick: function () { setTool("move"); } }, icon("x"), "Cancel"),
          h("button", { class: "btn primary", type: "button", onclick: applyCrop }, icon("check"), "Apply crop")));
      } else if (S.tool === "rotate") {
        kids.push(row(h("button", { class: "btn", type: "button", onclick: function () { rotate(-90); } }, icon("rotateL"), "Left"), h("button", { class: "btn", type: "button", onclick: function () { rotate(90); } }, icon("rotateR"), "Right"),
          h("button", { class: "btn", type: "button", onclick: function () { flip("h"); } }, icon("flipH"), "Flip"), h("button", { class: "btn", type: "button", onclick: function () { flip("v"); } }, icon("flipV"), "Flip")));
      } else if (S.tool === "adjust") {
        var defs = [["brightness", "Brightness"], ["contrast", "Contrast"], ["saturation", "Colour"], ["warmth", "Warmth"]], sliders = {};
        defs.forEach(function (d) {
          sliders[d[0]] = slider(ctx, d[1], -100, 100, S.adj[d[0]] || 0,
            function (n) { S.adj[d[0]] = n; preset = "custom"; sceneDirty = true; scheduleRender(); },
            function () { commit("adjust"); }, function (n) { return (n > 0 ? "+" : "") + n; });
          kids.push(sliders[d[0]]);
        });
        kids.push(row(h("button", { class: "btn", type: "button", onclick: function () { S.adj = C.params(); preset = "none"; sceneDirty = true; render(); commit(); buildPanel(); } }, icon("undo"), "Reset colours")));
      } else if (S.tool === "filters") {
        var list = h("div", { class: "ed-filters", role: "listbox", "aria-label": "Filters" });
        C.PRESETS.forEach(function (p) {
          var thumb = canvas(72, 72), tc = thumb.getContext("2d"), b = S.base, m = Math.min(b.width, b.height);
          tc.drawImage(b, (b.width - m) / 2, (b.height - m) / 2, m, m, 0, 0, 72, 72);
          var id = tc.getImageData(0, 0, 72, 72); C.applyAdjust(id.data, C.params(p.p)); tc.putImageData(id, 0, 0);
          var item = h("button", { class: "ed-filter", type: "button", role: "option", "aria-selected": String(preset === p.id), onclick: function () {
            ctx.haptic("tick"); S.adj = C.params(p.p); preset = p.id; sceneDirty = true; render(); commit(p.id === "none" ? null : "filter");
            Array.prototype.forEach.call(list.children, function (c) { c.setAttribute("aria-selected", "false"); }); item.setAttribute("aria-selected", "true");
          } }, thumb, h("span", { text: p.name }));
          list.append(item);
        });
        kids.push(list);
      } else if (S.tool === "pen" || S.tool === "marker") {
        var cur = S.opt[S.tool];
        kids.push(row(seg(ctx, "Pen", [["pen", "Pen"], ["marker", "Highlighter"]], S.tool, function (t) { S.tool = t; buildPanel(); sync(); })));
        kids.push(colours(ctx, cur.color, function (c) { cur.color = c; }));
        kids.push(slider(ctx, "Thickness", 1, S.tool === "marker" ? 80 : 40, cur.size, function (n) { cur.size = n; }, null));
        kids.push(note("Draw with your finger. Use two fingers to move and zoom the picture."));
      } else if (S.tool === "shape") {
        var sh = S.opt.shape;
        kids.push(row(seg(ctx, "Shape", [["arrow", "Arrow"], ["line", "Line"], ["rect", "Box"], ["ellipse", "Oval"]], sh.kind, function (k) { sh.kind = k; buildPanel(); })));
        kids.push(colours(ctx, sh.color, function (c) { sh.color = c; }));
        kids.push(slider(ctx, "Thickness", 1, 30, sh.size, function (n) { sh.size = n; }, null));
        if (sh.kind === "rect" || sh.kind === "ellipse") kids.push(row(seg(ctx, "Fill", [["no", "Outline"], ["yes", "Filled"]], sh.fill ? "yes" : "no", function (f) { sh.fill = f === "yes"; })));
        kids.push(note("Drag on the picture to draw it. You can move or resize it afterwards."));
      } else if (S.tool === "text") {
        kids.push(note("Tap the picture where you want the words. You can type, move and restyle them afterwards."));
      } else if (S.tool === "blur") {
        var bl = S.opt.blur;
        kids.push(row(seg(ctx, "Hide with", [["pixelate", "Pixels"], ["blur", "Blur"]], bl.mode, function (m) { bl.mode = m; })));
        kids.push(slider(ctx, "Strength", 4, 40, bl.amount, function (n) { bl.amount = n; }, null));
        kids.push(note("Drag over what you want to hide, such as a name or a number. It is flattened into the picture when you save."));
      } else if (S.tool === "resize") {
        var w0 = S.base.width, h0 = S.base.height, wIn = h("input", { class: "text-input sm", type: "number", min: "16", max: "20000", value: String(w0), "aria-label": "Width in pixels", inputmode: "numeric" });
        var hIn = h("input", { class: "text-input sm", type: "number", min: "16", max: "20000", value: String(h0), "aria-label": "Height in pixels", inputmode: "numeric" });
        wIn.addEventListener("input", function () { hIn.value = String(Math.max(1, Math.round((Number(wIn.value) || 0) * h0 / w0))); });
        hIn.addEventListener("input", function () { wIn.value = String(Math.max(1, Math.round((Number(hIn.value) || 0) * w0 / h0))); });
        var pct = [25, 50, 75].map(function (p) { return h("button", { class: "btn sm", type: "button", onclick: function () { wIn.value = String(Math.round(w0 * p / 100)); hIn.value = String(Math.round(h0 * p / 100)); } }, p + "%"); });
        kids.push(row.apply(null, pct.concat([wIn, h("span", { class: "ed-x", text: "×" }), hIn])));
        kids.push(row(h("button", { class: "btn primary", type: "button", onclick: function () {
          var w = Math.round(Number(wIn.value)), hh = Math.round(Number(hIn.value));
          if (!(w >= 16 && hh >= 16)) { ctx.toast("Choose a size of at least 16 pixels."); return; }
          if (w === w0 && hh === h0) { ctx.toast("That is the size it already is."); return; }
          resizeTo(w, hh);
        } }, icon("check"), "Resize"), h("span", { class: "ed-meta", text: "Now " + w0 + " × " + h0 + " px. Locked to the same shape." })));
      }
      if (S.notice) kids.push(h("p", { class: "ed-notice", text: S.notice }));
      panel.replaceChildren.apply(panel, kids);
    }

    /** What you can do to the thing you tapped. */
    function selectionControls(ob) {
      var kids = [], change = function (fn) { fn(ob); S.objs[S.sel] = ob; sceneDirty = true; render(); };
      var label = { pen: "Drawing", marker: "Highlight", line: "Line", arrow: "Arrow", rect: "Box", ellipse: "Oval", text: "Text", blur: "Hidden area" }[ob.type];
      if (ob.type === "text") {
        var area = h("textarea", { class: "ed-text", rows: "2", "aria-label": "The words", spellcheck: "true" }); area.value = ob.text;
        area.addEventListener("input", function () { ob.text = area.value; S.objs[S.sel] = ob; sceneDirty = true; scheduleRender(); });
        area.addEventListener("change", function () { commit("text"); });
        kids.push(area);
        kids.push(slider(ctx, "Size", 8, 160, Math.round(ob.size / unit()), function (n) { var cx = ob.x + (ob.w || 0) / 2, cy = ob.y + (ob.h || 0) / 2; ob.size = n * unit(); ob.x = cx - (ob.w || 0) / 2; ob.y = cy - (ob.h || 0) / 2; change(function () {}); }, function () { commit(); }));
        kids.push(row(seg(ctx, "Weight", [["b", "Bold"], ["n", "Regular"]], ob.bold ? "b" : "n", function (w) { change(function (x) { x.bold = w === "b"; }); commit(); }),
          seg(ctx, "Backing", [["no", "None"], ["yes", "Label"]], ob.bg ? "yes" : "no", function (w) { change(function (x) { x.bg = w === "yes"; }); commit(); })));
        kids.push(colours(ctx, ob.color, function (c) { change(function (x) { x.color = c; }); commit(); }));
        setTimeout(function () { if (!disposed && document.activeElement === document.body) area.focus({ preventScroll: true }); }, 0);
      } else if (ob.type === "blur") {
        kids.push(row(seg(ctx, "Hide with", [["pixelate", "Pixels"], ["blur", "Blur"]], ob.mode, function (m) { change(function (x) { x.mode = m; }); commit(); })));
        kids.push(slider(ctx, "Strength", 4, 40, ob.amount, function (n) { change(function (x) { x.amount = n; }); }, function () { commit(); }));
      } else {
        kids.push(colours(ctx, ob.color, function (c) { change(function (x) { x.color = c; }); commit(); }));
        kids.push(slider(ctx, "Thickness", 1, 80, Math.max(1, Math.round(ob.size / unit())), function (n) { change(function (x) { x.size = n * unit(); }); }, function () { commit(); }));
        if (ob.type === "rect" || ob.type === "ellipse") kids.push(row(seg(ctx, "Fill", [["no", "Outline"], ["yes", "Filled"]], ob.fill ? "yes" : "no", function (f) { change(function (x) { x.fill = f === "yes"; }); commit(); })));
      }
      kids.push(row(h("span", { class: "ed-meta", text: label + " selected" }),
        iconButton(ctx, "copy", "Duplicate", function () { var c = C.moveObject(ob, 24 * unit(), 24 * unit()); S.objs.push(c); S.sel = S.objs.length - 1; sceneDirty = true; commit(); buildPanel(); render(); }),
        iconButton(ctx, "front", "Bring to the front", function () { S.objs.push(S.objs.splice(S.sel, 1)[0]); S.sel = S.objs.length - 1; sceneDirty = true; commit(); render(); }),
        iconButton(ctx, "trash", "Delete", function () { removeSelected(); })));
      return kids;
    }
    function removeSelected() {
      if (S.sel < 0) return;
      S.objs.splice(S.sel, 1); S.sel = -1; sceneDirty = true; commit(); buildPanel(); render(); ctx.haptic("medium");
    }

    /* -------- fingers and mouse on the picture */
    var ptrs = new Map(), act = null;
    var at = function (e) { var r = stage.getBoundingClientRect(); return C.toContent(S.view, e.clientX - r.left, e.clientY - r.top); };
    var grab = function () { return Math.max(18 / S.view.scale, 6); };
    stage.addEventListener("pointerdown", function (e) {
      if (!S.base) return;
      stage.setPointerCapture(e.pointerId); stage.focus({ preventScroll: true });
      ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (ptrs.size === 2) {                       // two fingers: look around, never draw
        if (drawing) { drawing = null; sceneDirty = true; }
        var a = Array.from(ptrs.values()), r = stage.getBoundingClientRect();
        act = { kind: "pinch", dist: Math.hypot(a[0].x - a[1].x, a[0].y - a[1].y) || 1, view: Object.assign({}, S.view), mid: { x: (a[0].x + a[1].x) / 2 - r.left, y: (a[0].y + a[1].y) / 2 - r.top } };
        return;
      }
      var p = at(e);
      act = { kind: "none", sx: e.clientX, sy: e.clientY, start: p, view: Object.assign({}, S.view), moved: false };
      if (S.tool === "move") {
        var cur = S.objs[S.sel], corner = cur && C.isRectLike(cur) ? C.cornerAt(cur, p.x, p.y, grab() * 1.4) : null;
        if (corner) { act.kind = "corner"; act.corner = corner; act.orig = Object.assign({}, cur); ctx.haptic("tick"); return; }
        var hit = C.hitTest(S.objs, p.x, p.y, grab());
        if (hit >= 0) { var changed = hit !== S.sel; S.sel = hit; act.kind = "drag"; act.orig = S.objs[hit]; act.rebuild = changed; if (changed) ctx.haptic("tick"); render(); return; }
        if (S.sel >= 0) { S.sel = -1; buildPanel(); render(); }
        act.kind = "pan";
      } else if (S.tool === "crop" && S.crop) {
        var hd = C.cropHandleAt(S.crop, p.x, p.y, grab() * 1.3);
        if (hd) { act.kind = "crop"; act.handle = hd; act.rect = Object.assign({}, S.crop); if (hd !== "move") ctx.haptic("tick"); } else act.kind = "pan";
      } else if (S.tool === "pen" || S.tool === "marker") {
        var o = S.opt[S.tool];
        drawing = { obj: { type: S.tool, pts: [[p.x, p.y]], color: o.color, size: o.size * unit() } }; act.kind = "ink"; ctx.haptic("tick"); sceneDirty = true; scheduleRender();
      } else if (S.tool === "shape" || S.tool === "blur") {
        act.kind = "shape";
      } else if (S.tool === "text") {
        act.kind = "text";
      } else act.kind = "pan";
    });
    stage.addEventListener("pointermove", function (e) {
      if (!ptrs.has(e.pointerId) || !act) return;
      ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
      var b = box(), fit = fitOf(b);
      if (act.kind === "pinch" && ptrs.size >= 2) {
        var a = Array.from(ptrs.values()), r = stage.getBoundingClientRect(), d = Math.hypot(a[0].x - a[1].x, a[0].y - a[1].y) || 1;
        var mid = { x: (a[0].x + a[1].x) / 2 - r.left, y: (a[0].y + a[1].y) / 2 - r.top };
        var z = C.zoomAt(act.view, d / act.dist, act.mid.x, act.mid.y, fit * 0.9, Math.max(fit * 10, 6)); z.tx += mid.x - act.mid.x; z.ty += mid.y - act.mid.y;
        S.view = C.clampView(z, S.base.width, S.base.height, b.w, b.h); scheduleRender(); return;
      }
      var p = at(e), dx = p.x - act.start.x, dy = p.y - act.start.y;
      if (!act.moved && Math.hypot(e.clientX - act.sx, e.clientY - act.sy) < 4) return;
      act.moved = true;
      if (act.kind === "pan") { S.view = C.clampView({ scale: act.view.scale, tx: act.view.tx + (e.clientX - act.sx), ty: act.view.ty + (e.clientY - act.sy) }, S.base.width, S.base.height, b.w, b.h); scheduleRender(); }
      else if (act.kind === "drag") { S.objs[S.sel] = C.moveObject(act.orig, dx, dy); sceneDirty = true; scheduleRender(); }
      else if (act.kind === "corner") { var nb = C.dragCorner(act.orig, act.corner, dx, dy); S.objs[S.sel] = Object.assign({}, act.orig, nb); sceneDirty = true; scheduleRender(); }
      else if (act.kind === "crop") { S.crop = C.dragCrop(act.rect, act.handle, dx, dy, { w: S.base.width, h: S.base.height }, C.aspectRatio(S.opt.aspect, S.base.width, S.base.height, S.opt.flip), 24); scheduleRender(); }
      else if (act.kind === "ink") { var pts = drawing.obj.pts, last = pts[pts.length - 1]; if (Math.hypot(p.x - last[0], p.y - last[1]) >= 0.6 / S.view.scale) { pts.push([p.x, p.y]); sceneDirty = true; scheduleRender(); } }
      else if (act.kind === "shape") {
        if (S.tool === "blur") drawing = { obj: { type: "blur", x: Math.min(act.start.x, p.x), y: Math.min(act.start.y, p.y), w: Math.abs(dx), h: Math.abs(dy), mode: S.opt.blur.mode, amount: S.opt.blur.amount } };
        else {
          var s = S.opt.shape, size = s.size * unit();
          drawing = { obj: s.kind === "line" || s.kind === "arrow"
            ? { type: s.kind, x1: act.start.x, y1: act.start.y, x2: p.x, y2: p.y, color: s.color, size: size }
            : { type: s.kind, x: Math.min(act.start.x, p.x), y: Math.min(act.start.y, p.y), w: Math.abs(dx), h: Math.abs(dy), color: s.color, size: size, fill: s.fill } };
        }
        sceneDirty = true; scheduleRender();
      }
    });
    function finish(e) {
      if (!ptrs.has(e.pointerId)) return;
      var a = act; ptrs.delete(e.pointerId);
      if (ptrs.size > 0) { act = null; return; }
      act = null;
      if (!a) return;
      if (a.kind === "pinch") return;
      var p = at(e);
      if (a.kind === "ink" && drawing) {
        var ob = drawing.obj; drawing = null; ob.pts = C.thin(ob.pts, Math.max(0.5, ob.size * 0.08));
        S.objs.push(ob); sceneDirty = true; commit(ob.type); render();
      } else if (a.kind === "shape" && drawing) {
        var sh = drawing.obj; drawing = null;
        var big = sh.pts ? true : (sh.w !== undefined ? sh.w > 6 && sh.h > 6 : Math.hypot(sh.x2 - sh.x1, sh.y2 - sh.y1) > 8);
        if (big) { S.objs.push(sh); S.sel = S.objs.length - 1; S.tool = "move"; sceneDirty = true; commit(sh.type); buildPanel(); sync(); ctx.haptic("light"); }
        else sceneDirty = true;
        render();
      } else if (a.kind === "text" && !a.moved) {
        var t = { type: "text", x: p.x, y: p.y, text: "Text", size: S.opt.text.size * unit(), color: S.opt.text.color, bold: S.opt.text.bold, bg: S.opt.text.bg };
        S.objs.push(t); S.sel = S.objs.length - 1; S.tool = "move"; sceneDirty = true; rebuildScene(); t.x -= (t.w || 0) / 2; t.y -= (t.h || 0) / 2;
        sceneDirty = true; commit("text"); buildPanel(); sync(); render();
      } else if ((a.kind === "drag" || a.kind === "corner") && a.moved) { commit(); render(); ctx.haptic("tick"); }  // dropped
      else if (a.kind === "drag" && a.rebuild) { buildPanel(); render(); }
      else if (a.kind === "crop") render();
    }
    stage.addEventListener("pointerup", finish);
    stage.addEventListener("pointercancel", function (e) { drawing = null; sceneDirty = true; finish(e); });
    stage.addEventListener("wheel", function (e) { e.preventDefault(); var r = stage.getBoundingClientRect(); zoomBy(Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top); }, { passive: false });
    stage.addEventListener("keydown", function (e) {
      var mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === "z") { e.preventDefault(); stepHistory(e.shiftKey ? 1 : -1); }
      else if (mod && e.key.toLowerCase() === "y") { e.preventDefault(); stepHistory(1); }
      else if (mod && e.key.toLowerCase() === "s") { e.preventDefault(); openSave(); }
      else if ((e.key === "Delete" || e.key === "Backspace") && S.sel >= 0) { e.preventDefault(); removeSelected(); }
      else if (e.key === "Escape") { if (S.tool === "crop") setTool("move"); else if (S.sel >= 0) { S.sel = -1; buildPanel(); render(); } }
    });
    ["pointerdown"].forEach(function (n) { compareBtn.addEventListener(n, function () { S.compare = true; render(); }); });
    ["pointerup", "pointerleave", "pointercancel"].forEach(function (n) { compareBtn.addEventListener(n, function () { if (S.compare) { S.compare = false; render(); } }); });
    var resizer = typeof ResizeObserver === "function" ? new ResizeObserver(function () { if (S.base) { var b = box(), fit = fitOf(b); if (S.view.scale <= fit * 1.02 || !S.view.scale) fitAll(); else { S.view = C.clampView(S.view, S.base.width, S.base.height, b.w, b.h); render(); } } }) : null;
    if (resizer) resizer.observe(holder);

    /* -------- saving */
    function exportCanvas(forJpeg) {
      var b = S.base, out = canvas(b.width, b.height), c2 = out.getContext("2d", { willReadFrequently: true });
      if (forJpeg) { c2.fillStyle = "#ffffff"; c2.fillRect(0, 0, b.width, b.height); }  // JPEG has no see-through parts
      c2.drawImage(b, 0, 0);
      if (!C.isNeutral(S.adj)) { var id = c2.getImageData(0, 0, b.width, b.height); C.applyAdjust(id.data, S.adj); c2.putImageData(id, 0, 0); }
      C.clone(S.objs).forEach(function (ob) { drawObject(c2, ob, 1); });
      return out;
    }
    function exportBlob(type, quality) {
      var c = exportCanvas(type === "image/jpeg");
      return new Promise(function (resolve, reject) { c.toBlob(function (blob) { blob ? resolve(blob) : reject(new Error("The picture couldn't be prepared for saving.")); }, type, quality); });
    }

    function openSave() {
      if (!S.base) return;
      var fmt = { png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", webp: "image/webp" }[doc.ext] ? (doc.ext === "jpeg" ? "jpg" : doc.ext) : "png";
      var form = { fmt: fmt, q: 92, name: C.editedName(doc.name, fmt) }, busy = false;
      var nameIn = h("input", { class: "text-input", type: "text", value: form.name, "aria-label": "File name", spellcheck: "false", autocapitalize: "off" });
      var sizeNote = h("span", { class: "ed-meta" }), qRow = null;
      var replaceBtn = h("button", { class: "btn", type: "button", onclick: function () { go(true); } }, icon("save"), "Replace the original");
      var copyBtn = h("button", { class: "btn primary", type: "button", onclick: function () { go(false); } }, icon("copy"), "Save a copy");
      var dlBtn = h("button", { class: "btn", type: "button", onclick: function () { downloadIt(); } }, icon("download"), "Download");
      var fmtRow = seg(ctx, "Format", [["png", "PNG"], ["jpg", "JPEG"], ["webp", "WebP"]], fmt, function (f) {
        form.fmt = f;
        nameIn.value = nameIn.value.replace(/\.(png|jpe?g|webp)$/i, "") + "." + f;  // the name follows the format you pick
        refresh();
      });
      qRow = slider(ctx, "Quality", 50, 100, form.q, function (n) { form.q = n; }, function () { estimate(); }, function (n) { return n + "%"; });
      var modal = h("div", { class: "im-modal", role: "dialog", "aria-modal": "true", "aria-label": "Save the picture", onclick: function (e) { if (e.target === modal && !busy) modal.remove(); } },
        h("div", { class: "im-card ed-save-card" }, h("h3", { text: "Save the picture" }),
          h("label", { class: "ed-field" }, h("span", { text: "Name" }), nameIn), h("div", { class: "ed-field" }, h("span", { text: "Format" }), fmtRow), qRow, sizeNote,
          h("p", { class: "ed-fine", text: "Colours and everything you added are flattened into the picture. Information stored inside the original, such as where a photo was taken, isn't kept." }),
          h("div", { class: "im-actions stack" }, copyBtn, replaceBtn, dlBtn, h("button", { class: "btn ghost", type: "button", onclick: function () { if (!busy) modal.remove(); } }, "Cancel"))));
      host.append(modal);
      var estTimer = 0;
      function mime() { return form.fmt === "jpg" ? "image/jpeg" : "image/" + form.fmt; }
      function refresh() {
        qRow.hidden = form.fmt === "png";
        replaceBtn.hidden = !(doc.saveable && C.sameKind(form.fmt, doc.ext));
        estimate();
      }
      function estimate() {
        clearTimeout(estTimer);
        estTimer = setTimeout(function () {
          sizeNote.textContent = "Working out the size…";
          exportBlob(mime(), form.q / 100).then(function (b) { if (modal.isConnected) sizeNote.textContent = S.base.width + " × " + S.base.height + " px  ·  about " + C.fmtBytes(b.size); }).catch(function () { sizeNote.textContent = ""; });
        }, 250);
      }
      function setBusy(on, label) { busy = on; [copyBtn, replaceBtn, dlBtn].forEach(function (b) { b.disabled = on; }); if (label) sizeNote.textContent = label; }
      async function downloadIt() {
        try { setBusy(true, "Preparing…"); var b = await exportBlob(mime(), form.q / 100); ctx.download(b, nameIn.value.trim() || C.editedName(doc.name, form.fmt)); modal.remove(); }
        catch (e) { ctx.toast(e.message); setBusy(false); }
      }
      async function go(replace) {
        var name = nameIn.value.trim();
        if (!replace && !/^[^\\/:*?"<>|]+\.(png|jpe?g|webp)$/i.test(name)) { ctx.toast("Give it a name that ends in .png, .jpg or .webp, without \\ / : * ? \" < > |"); nameIn.focus(); return; }
        try {
          setBusy(true, "Saving…");
          var blob = await exportBlob(replace ? ({ png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", webp: "image/webp" }[doc.ext]) : mime(), form.q / 100);
          var target = replace ? doc.path : joinPath(doc.folder, name);
          var res = await ctx.putImage(target, blob, { base_sha: replace ? doc.sha : null, unique: !replace, note: C.describeEdits(S.ops) });
          modal.remove(); afterSave(res, replace);
        } catch (e) {
          setBusy(false, "");
          if (e.status === 409) {
            var pick = await ask(ctx, host, "The picture changed on your PC", "Someone saved over " + doc.name + " after you opened it. Replacing it would lose their change.", [{ label: "Keep editing" }, { label: "Save a copy instead", primary: true }]);
            if (pick === 1) go(false);
          } else if (e.status === 403 && e.data && e.data.code === "needs_change_access") {
            modal.remove(); ctx.askAccess(e.data.folder, true, function () { openSave(); });
          } else ctx.toast(e.message || "Couldn't save the picture.");
        }
      }
      refresh();
    }
    function afterSave(res, replaced) {
      savedAt = hist.index(); S.notice = "";
      doc.path = res.path; doc.name = res.name; doc.sha = res.sha; doc.folder = res.path.replace(/[\\/][^\\/]*$/, "");
      doc.ext = C.extOf(res.name); doc.saveable = ["png", "jpg", "jpeg", "webp", "bmp"].indexOf(doc.ext) >= 0;
      title.textContent = "Edit " + doc.name; sync(); ctx.haptic("success"); o.saved = res;
      if (!res.saved) { ctx.toast("Nothing was different, so nothing was saved."); return; }
      ctx.toast("Saved " + res.name + (replaced ? " over the original" : ""), res.job ? { label: "Undo", run: function () { ctx.undoJob(res.job.id).then(function () { ctx.toast("Undone on the PC."); }).catch(function (e) { ctx.toast(e.message); }); } } : null);
    }

    function leave() {
      window.onbeforeunload = null; disposed = true;
      if (resizer) resizer.disconnect();
      o.onExit(o.saved || null);
    }
    async function tryExit() {
      if (dirty()) {
        var pick = await ask(ctx, host, "Close without saving?", "You have changes to " + doc.name + " that haven't been saved.", [{ label: "Keep editing" }, { label: "Save…", primary: true }, { label: "Discard", danger: true }]);
        if (pick === 0) return;
        if (pick === 1) { openSave(); return; }
      }
      leave();
    }

    /* -------- start: open the picture (as big as a phone's canvas can take) */
    var url = URL.createObjectURL(o.blob), im = new Image();
    im.onload = function () {
      URL.revokeObjectURL(url);
      if (disposed) return;
      var w = im.naturalWidth, hh = im.naturalHeight, cap = C.capSize(w, hh, MAX_PIXELS);
      var c = canvas(cap.w, cap.h), c2 = c.getContext("2d"); c2.imageSmoothingQuality = "high"; c2.drawImage(im, 0, 0, cap.w, cap.h);
      S.base = c; S.baseId = 1;
      if (cap.scaled) S.notice = "This picture is " + w + " × " + hh + " pixels, more than a phone can edit at once. It was reduced to " + cap.w + " × " + cap.h + " for editing, and saving keeps that size.";
      spin.remove(); hist.reset(snapshot()); savedAt = 0; buildPanel(); sync(); fitAll(); stage.focus({ preventScroll: true });
    };
    im.onerror = function () { URL.revokeObjectURL(url); spin.replaceChildren(h("p", { class: "err", text: "This picture can't be opened for editing." })); };
    im.src = url;

    return {
      dispose: function () { disposed = true; window.onbeforeunload = null; if (resizer) resizer.disconnect(); if (raf) cancelAnimationFrame(raf); },
      dirty: dirty,
    };
  }

  /* ------------------------------------------------------------------ the one way in */

  window.AfpImg = {
    mount: function (host, o) {
      host.classList.add("imaging");
      var inner = o.mode === "gallery" ? mountGallery(host, o) : mountViewer(host, o);
      return { dispose: function () { host.classList.remove("imaging"); inner.dispose(); }, dirty: inner.dirty };
    },
  };
})();
