/* ux.js: the page's shared touch and motion helpers.
 *
 *   swipe(row, opts)       a row that slides left to show Delete, or all the way to delete (returns the wrapper to put on the page)
 *   pull(scroller, fn)     pull a list down to refresh it
 *   burst(where)           a small "it worked" animation
 *   spot(root)             a soft light that follows a finger on any .spot element
 *   art(name)              an empty-state picture
 *   skeleton(kind, n)      grey shapes standing in for a list that is loading
 *
 * What a gesture means (how far is "delete", when does a pull count) is plain maths in the functions at the top, tested on their own:
 * tests/js/ux_test.js. The pictures are fixed strings written here, never text from the PC or from another app.
 * Every helper does nothing harmful where the page is missing (the tests load this file in Node).
 */
(function (root) {
  "use strict";

  var REVEAL = 88;          // px the row stays open at, showing the Delete button
  var PULL_LIMIT = 120;     // the most a pull can move
  var PULL_READY = 50;      // how far it must move to refresh (a finger travels about 85 px to get there)

  /* ---------------------------------------------------------------- the maths (pure)  */

  /* A rubber band: moves with the finger at first, then gets harder and never passes `limit`. */
  function resist(delta, limit) {
    if (!(delta > 0)) return 0;
    return limit * (1 - 1 / (delta / limit * 0.55 + 1));
  }

  /* Where a swipe lands. x is how far the row is dragged (0 or negative), vx the speed in px per ms (negative = leftwards), width the row's width. */
  function swipeTarget(x, vx, width, reveal) {
    reveal = reveal || REVEAL;
    if (x <= -width * 0.5) return "delete";
    if (vx < -0.9 && x < -reveal * 0.6) return "delete";   // a hard flick
    if (vx > 0.6) return "close";                           // a flick back
    if (x <= -reveal * 0.5) return "open";
    return "close";
  }

  /* A row follows the finger one to one up to the open position, then follows at 70% (so it feels heavier, but a drag across about two thirds of the row still deletes). */
  function swipeOffset(startX, dx, width, reveal) {
    reveal = reveal || REVEAL;
    var x = startX + dx;
    if (x > 0) return 0;   // never to the right of rest
    if (x >= -reveal) return x;
    return Math.max(-width, -(reveal + (-reveal - x) * 0.7));
  }

  function pullState(dy, ready) {
    var offset = dy > 0 ? PULL_LIMIT * (1 - 1 / (dy / PULL_LIMIT + 1)) : 0;   // gentler than a swipe: about half a finger's travel at first
    ready = ready || PULL_READY;
    return { offset: offset, progress: Math.min(1, offset / ready), ready: offset >= ready };
  }

  /* ---------------------------------------------------------------- small helpers  */

  var hasDom = typeof document !== "undefined";
  var calm = function () {
    if (!hasDom) return true;
    var el = document.documentElement;
    return !!(el && el.dataset && el.dataset.motion === "off") || !!(root.matchMedia && root.matchMedia("(prefers-reduced-motion: reduce)").matches);
  };

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text) n.textContent = text;
    return n;
  }

  /* An icon from fixed strings (the same few shapes the page draws everywhere). */
  var ICON = {
    trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    arrow: '<path d="M12 5v14M5 12l7 7 7-7"/>',
    check: '<path d="m5 12 5 5 9-10"/>',
  };
  function icon(name, size) {
    var s = el("span", "ico");
    s.setAttribute("aria-hidden", "true");
    s.innerHTML = '<svg viewBox="0 0 24 24" focusable="false">' + (ICON[name] || "") + "</svg>";  // a fixed string above
    if (size) s.style.width = s.style.height = size + "px";
    return s;
  }

  /* ---------------------------------------------------------------- swipe to delete  */

  var openRow = null;

  function closeOpen(except) {
    if (openRow && openRow !== except) { openRow.close(); }
  }

  /* Wraps `row` so it can be swiped left. opts: { onDelete(): Promise|void, label, tick(), danger } Returns the wrapper. */
  function swipe(row, opts) {
    opts = opts || {};
    var wrap = el("div", "swp"), bg = el("div", "swp-bg"), act = el("button", "swp-act");
    act.type = "button";
    act.setAttribute("aria-label", opts.label || "Delete");
    act.tabIndex = -1;
    act.append(icon("trash", 22), el("span", "", opts.label || "Delete"));
    bg.append(act);
    var fg = el("div", "swp-fg");
    fg.append(row);
    wrap.append(bg, fg);

    var x = 0, start = null, mode = "", busy = false, moved = false;
    var api = { el: wrap, close: close, open: open };

    function paint(v, animate) {
      x = v;
      fg.style.transition = animate ? "transform 0.38s cubic-bezier(0.22, 1, 0.36, 1)" : "none";
      fg.style.transform = v ? "translate3d(" + v + "px,0,0)" : "";
      var p = Math.min(1, Math.abs(v) / REVEAL);
      bg.style.setProperty("--p", p.toFixed(3));
      wrap.classList.toggle("is-open", v <= -REVEAL * 0.5);
      act.tabIndex = v <= -REVEAL * 0.5 ? 0 : -1;
    }
    function close() { if (openRow === api) openRow = null; paint(0, true); }
    function open() { closeOpen(api); openRow = api; paint(-REVEAL, true); }

    function remove() {
      if (busy) return;
      busy = true;
      if (opts.tick) opts.tick("medium");
      var width = wrap.offsetWidth, height = wrap.offsetHeight;
      wrap.style.height = height + "px";
      paint(-width - 20, true);
      wrap.classList.add("is-going");
      var done = function () {
        wrap.style.transition = calm() ? "none" : "height 0.3s cubic-bezier(0.22, 1, 0.36, 1), margin 0.3s ease, opacity 0.3s ease";
        wrap.style.height = "0px"; wrap.style.opacity = "0"; wrap.style.marginBottom = "0px";
      };
      var restore = function () {
        busy = false; wrap.classList.remove("is-going"); wrap.style.height = ""; paint(0, true);
      };
      setTimeout(function () {
        done();
        Promise.resolve().then(function () { return opts.onDelete && opts.onDelete(); }).then(function () {
          setTimeout(function () { if (wrap.parentNode) wrap.remove(); }, calm() ? 0 : 320);
        }, function () {   // it did not work: slide back, so the row is never lost without the PC agreeing
          wrap.style.transition = "none"; wrap.style.opacity = ""; wrap.style.marginBottom = ""; restore();
        });
      }, calm() ? 0 : 200);
    }

    act.addEventListener("click", function (e) { e.preventDefault(); e.stopPropagation(); remove(); });

    fg.addEventListener("pointerdown", function (e) {
      if (busy || (e.pointerType === "mouse" && e.button !== 0)) return;
      if (e.target.closest && e.target.closest("input, textarea, select, .swp-noswipe")) return;
      start = { x: e.clientX, y: e.clientY, from: x, t: e.timeStamp, lastX: e.clientX, lastT: e.timeStamp, v: 0, id: e.pointerId };
      mode = ""; moved = false;
    });
    fg.addEventListener("pointermove", function (e) {
      if (!start || e.pointerId !== start.id) return;
      var dx = e.clientX - start.x, dy = e.clientY - start.y;
      if (!mode) {
        if (Math.abs(dx) < 8 && Math.abs(dy) < 8) return;
        mode = Math.abs(dx) > Math.abs(dy) * 1.2 ? "h" : "v";
        if (mode === "h") {
          closeOpen(api);
          try { fg.setPointerCapture(e.pointerId); } catch (err) { /* the pointer is gone */ }
          wrap.classList.add("is-dragging");
        }
      }
      if (mode !== "h") return;
      moved = true;
      var dt = e.timeStamp - start.lastT;
      if (dt > 0) start.v = (e.clientX - start.lastX) / dt * 0.6 + start.v * 0.4;
      start.lastX = e.clientX; start.lastT = e.timeStamp;
      var before = x;
      paint(swipeOffset(start.from, dx, wrap.offsetWidth), false);
      if (opts.tick && before > -REVEAL * 0.5 && x <= -REVEAL * 0.5) opts.tick("tick");
    });
    function finish(e) {
      if (!start || (e && e.pointerId !== start.id)) return;
      var was = mode, v = start.v;
      start = null; mode = "";
      wrap.classList.remove("is-dragging");
      if (was !== "h") return;
      var target = swipeTarget(x, v, wrap.offsetWidth);
      if (target === "delete") remove(); else if (target === "open") open(); else close();
    }
    fg.addEventListener("pointerup", finish);
    fg.addEventListener("pointercancel", finish);
    fg.addEventListener("click", function (e) {
      if (moved) { e.preventDefault(); e.stopPropagation(); moved = false; return; }   // a drag is not a tap
      if (x < 0) { e.preventDefault(); e.stopPropagation(); close(); }                 // a tap on an open row closes it
    }, true);
    return wrap;
  }

  /* ---------------------------------------------------------------- pull to refresh  */

  /* Makes `scroller` (a scrolling element) refreshable by pulling it down from the top. `refresh()` may return a promise. Returns { stop }. */
  function pull(scroller, refresh, opts) {
    opts = opts || {};
    var chip = el("div", "ptr"), disc = el("div", "ptr-chip");
    disc.append(icon("arrow", 20));
    chip.append(disc);
    scroller.prepend(chip);
    var startY = null, active = false, busy = false, dy = 0, live = null;

    function paint(offset, progress, spinning) {
      disc.style.transform = "translate3d(0," + (offset - 56) + "px,0) scale(" + (0.6 + 0.4 * progress).toFixed(3) + ")";
      disc.style.opacity = String(Math.min(1, progress * 1.4));
      disc.classList.toggle("is-ready", progress >= 1);
      disc.classList.toggle("is-spinning", !!spinning);
      disc.firstChild.style.transform = spinning ? "" : "rotate(" + (progress >= 1 ? 180 : Math.round(progress * 140)) + "deg)";
      var kid = scroller.querySelector(":scope > .dy");
      if (kid) { kid.style.transition = active ? "none" : "transform 0.35s cubic-bezier(0.22, 1, 0.36, 1)"; kid.style.transform = offset ? "translate3d(0," + (offset * 0.45).toFixed(1) + "px,0)" : ""; }
    }
    function rest() { paint(0, 0, false); disc.style.opacity = "0"; }
    rest();

    function onStart(e) {
      if (busy || scroller.scrollTop > 0 || e.touches.length !== 1) { startY = null; return; }
      startY = e.touches[0].clientY; active = false; dy = 0;
    }
    function onMove(e) {
      if (startY === null || busy) return;
      var d = e.touches[0].clientY - startY;
      if (d <= 0 || scroller.scrollTop > 0) { if (active) { active = false; rest(); } return; }
      if (e.cancelable) e.preventDefault();   // the list is at the top: this pull is ours, not the browser's
      active = true; dy = d;
      var s = pullState(d);
      if (opts.tick && !live && s.ready) opts.tick("tick");
      live = s.ready ? true : null;
      paint(s.offset, s.progress, false);
    }
    function onEnd() {
      if (startY === null) return;
      var s = pullState(dy);
      var go = active && s.ready;
      startY = null; active = false; live = null;
      if (!go) { rest(); return; }
      busy = true;
      paint(PULL_READY * 0.8, 1, true);
      Promise.resolve().then(refresh).catch(function () { /* the list says what went wrong */ }).then(function () {
        busy = false; rest();
      });
    }
    scroller.addEventListener("touchstart", onStart, { passive: true });
    scroller.addEventListener("touchmove", onMove, { passive: false });
    scroller.addEventListener("touchend", onEnd, { passive: true });
    scroller.addEventListener("touchcancel", onEnd, { passive: true });
    return { stop: function () { scroller.removeEventListener("touchstart", onStart); scroller.removeEventListener("touchmove", onMove); scroller.removeEventListener("touchend", onEnd); scroller.removeEventListener("touchcancel", onEnd); chip.remove(); } };
  }

  /* ---------------------------------------------------------------- "it worked"  */

  /* where: an element (the burst is centred on it), { x, y } (screen pixels) or nothing (the middle of the screen). */
  function burst(where) {
    if (!hasDom) return null;
    var x = root.innerWidth / 2, y = root.innerHeight / 2;
    if (where && where.getBoundingClientRect) { var r = where.getBoundingClientRect(); x = r.left + r.width / 2; y = r.top + r.height / 2; }
    else if (where && typeof where.x === "number") { x = where.x; y = where.y; }
    var fx = el("div", "fx-burst");
    fx.setAttribute("aria-hidden", "true");
    fx.style.left = x + "px"; fx.style.top = y + "px";
    fx.append(el("i", "fx-ring"), icon("check", 28));
    for (var i = 0; i < 10; i++) {
      var p = el("i", "fx-dot");
      p.style.setProperty("--a", (i * 36 + (i % 2 ? 9 : 0)) + "deg");
      p.style.setProperty("--d", (34 + (i % 3) * 9) + "px");
      p.style.setProperty("--s", String(0.7 + (i % 4) * 0.18));
      fx.append(p);
    }
    (document.getElementById("layer") || document.body).append(fx);
    setTimeout(function () { fx.remove(); }, calm() ? 600 : 1100);
    return fx;
  }

  /* ---------------------------------------------------------------- a light under the finger  */

  var spotted = false;
  function spot(rootEl) {
    if (!hasDom || spotted) return;
    spotted = true;
    (rootEl || document).addEventListener("pointerdown", function (e) {
      var t = e.target.closest && e.target.closest(".spot");
      if (!t || calm()) return;
      var r = t.getBoundingClientRect();
      t.style.setProperty("--px", ((e.clientX - r.left) / r.width * 100).toFixed(1) + "%");
      t.style.setProperty("--py", ((e.clientY - r.top) / r.height * 100).toFixed(1) + "%");
      t.classList.add("lit");
      var off = function () { t.classList.remove("lit"); root.removeEventListener("pointerup", off, true); root.removeEventListener("pointercancel", off, true); };
      root.addEventListener("pointerup", off, true); root.addEventListener("pointercancel", off, true);
    }, { passive: true });
  }

  /* ---------------------------------------------------------------- empty-state pictures  */

  var G = '<defs><linearGradient id="ag" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="var(--a1)"/><stop offset="1" stop-color="var(--a2)"/></linearGradient></defs>';
  var ART = {
    inbox: '<ellipse class="sh" cx="60" cy="86" rx="38" ry="5"/><path class="f2" d="M18 54 32 22h56l14 32v26a6 6 0 0 1-6 6H24a6 6 0 0 1-6-6z"/><path class="f1" d="M18 54h26l5 10h22l5-10h26v26a6 6 0 0 1-6 6H24a6 6 0 0 1-6-6z"/><rect class="pp" x="40" y="14" width="40" height="34" rx="5"/><path class="ln" d="M48 24h24M48 31h24M48 38h14"/><circle class="sp s1" cx="100" cy="20" r="3"/><circle class="sp s2" cx="16" cy="34" r="2"/>',
    notes: '<ellipse class="sh" cx="60" cy="86" rx="34" ry="5"/><rect class="f2" x="30" y="12" width="60" height="72" rx="10"/><rect class="pp" x="36" y="8" width="60" height="72" rx="10" transform="rotate(5 66 44)"/><path class="ln" d="M48 30h36M48 42h36M48 54h22" transform="rotate(5 66 44)"/><path class="f1" d="m84 66 14-14 8 8-14 14-10 2z"/><circle class="sp s1" cx="20" cy="26" r="3"/><circle class="sp s2" cx="104" cy="22" r="2"/>',
    remind: '<ellipse class="sh" cx="60" cy="88" rx="30" ry="5"/><circle class="f2" cx="60" cy="48" r="34"/><circle class="pp" cx="60" cy="48" r="28"/><path class="ln" d="M60 28v21l14 8"/><circle class="f1" cx="60" cy="49" r="3.5"/><path class="f1" d="M30 18a18 18 0 0 1 16-10M90 18a18 18 0 0 0-16-10" fill="none" stroke-width="5" stroke-linecap="round" style="stroke:var(--a1)"/><circle class="sp s1" cx="100" cy="76" r="3"/><circle class="sp s2" cx="18" cy="70" r="2"/>',
    watch: '<ellipse class="sh" cx="60" cy="88" rx="38" ry="5"/><path class="f2" d="M8 48s20-30 52-30 52 30 52 30-20 30-52 30S8 48 8 48z"/><circle class="pp" cx="60" cy="48" r="20"/><circle class="f1" cx="60" cy="48" r="11"/><circle class="pp" cx="55" cy="43" r="3.5"/><circle class="sp s1" cx="104" cy="22" r="3"/><circle class="sp s2" cx="14" cy="24" r="2"/>',
    recent: '<ellipse class="sh" cx="60" cy="88" rx="34" ry="5"/><rect class="f2" x="22" y="26" width="56" height="58" rx="9" transform="rotate(-8 50 55)"/><rect class="pp" x="38" y="14" width="56" height="62" rx="9"/><path class="ln" d="M48 32h30M48 44h30M48 56h18"/><path class="f1" d="m92 8 3.2 6.4 7 1-5.1 5 1.2 7-6.3-3.3-6.3 3.3 1.2-7-5.1-5 7-1z"/>',
    print: '<ellipse class="sh" cx="60" cy="88" rx="38" ry="5"/><rect class="pp" x="36" y="8" width="48" height="34" rx="4"/><path class="ln" d="M44 18h32M44 26h32"/><rect class="f2" x="14" y="36" width="92" height="40" rx="12"/><rect class="f1" x="30" y="56" width="60" height="28" rx="5"/><path class="ln" d="M40 66h40M40 74h24" style="stroke:rgba(255,255,255,.85)"/><circle class="pp" cx="92" cy="46" r="3.5"/>',
    snap: '<ellipse class="sh" cx="60" cy="88" rx="38" ry="5"/><rect class="f2" x="12" y="26" width="96" height="58" rx="14"/><path class="f2" d="M40 26l6-12h28l6 12z"/><circle class="pp" cx="60" cy="55" r="22"/><circle class="f1" cx="60" cy="55" r="14"/><circle class="pp" cx="54" cy="49" r="4"/><circle class="sp s1" cx="96" cy="38" r="3"/>',
    brief: '<ellipse class="sh" cx="60" cy="88" rx="38" ry="5"/><rect class="f2" x="14" y="16" width="92" height="64" rx="14"/><rect class="pp" x="24" y="26" width="30" height="20" rx="6"/><rect class="pp" x="60" y="26" width="36" height="20" rx="6"/><rect class="pp" x="24" y="52" width="72" height="18" rx="6"/><path class="f1" d="M30 62h20M60 62h32" stroke-width="4" stroke-linecap="round" style="stroke:var(--a1)"/>',
    done: '<ellipse class="sh" cx="60" cy="88" rx="30" ry="5"/><circle class="f2" cx="60" cy="48" r="34"/><circle class="f1" cx="60" cy="48" r="26"/><path d="m47 49 9 9 19-20" fill="none" stroke="#fff" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/><circle class="sp s1" cx="102" cy="24" r="3"/><circle class="sp s2" cx="16" cy="30" r="2"/>',
  };
  function art(name) {
    var s = el("span", "art");
    s.setAttribute("aria-hidden", "true");
    s.innerHTML = '<svg viewBox="0 0 120 96" focusable="false">' + G + (ART[name] || ART.done) + "</svg>";   // fixed pictures above
    return s;
  }

  /* ---------------------------------------------------------------- loading shapes  */

  function skeleton(kind, n) {
    var box = el("div", "skl");
    box.setAttribute("aria-busy", "true"); box.setAttribute("aria-label", "Loading");
    for (var i = 0; i < (n || 3); i++) {
      var row = el("div", "skl-row");
      if (kind === "card") { row.classList.add("is-card"); row.append(el("i", "shim w60"), el("i", "shim w90"), el("i", "shim w40")); }
      else if (kind === "stat") { row.classList.add("is-stat"); row.append(el("i", "shim ring"), el("i", "shim w60")); }
      else { row.append(el("i", "shim dot"), (function () { var c = el("div", "skl-col"); c.append(el("i", "shim w70"), el("i", "shim w45")); return c; })()); }
      box.append(row);
    }
    return box;
  }

  /* ---------------------------------------------------------------- oil paint transition  */

  /* A small seeded random generator, so the same seed always paints the same picture (and the maths can be tested). */
  function mulberry(seed) {
    var a = seed >>> 0;
    return function () {
      a = (a + 0x6D2B79F5) >>> 0;
      var t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  var clamp01 = function (x) { return x < 0 ? 0 : x > 1 ? 1 : x; };
  var easeInOut = function (x) { return 0.5 - 0.5 * Math.cos(Math.PI * clamp01(x)); };

  function hexToHsl(hex) {
    var m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
    var n = parseInt(m ? m[1] : "22d3ee", 16), r = (n >> 16 & 255) / 255, g = (n >> 8 & 255) / 255, b = (n & 255) / 255;
    var mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2, d = mx - mn, h = 0, s = 0;
    if (d) {
      s = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
      h = mx === r ? (g - b) / d + (g < b ? 6 : 0) : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
      h *= 60;
    }
    return [h, s, l];
  }
  function hsl(h, s, l) {
    return "hsl(" + Math.round(((h % 360) + 360) % 360) + "," + Math.round(clamp01(s) * 100) + "%," + Math.round(clamp01(l) * 100) + "%)";
  }

  /* The colours on the palette: the accent's two colours, a lighter and a deeper one, a warm contrast, and a dark base. Returns [h, s, l] triples. */
  function palette(c1, c2) {
    var a = hexToHsl(c1), b = hexToHsl(c2);
    return [a, b, [a[0], a[1], Math.min(0.78, a[2] + 0.14)], [b[0], b[1], Math.max(0.28, b[2] - 0.14)], [(a[0] + 160) % 360, 0.72, 0.58], [b[0], 0.55, 0.24], a, b];
  }

  /* Where the brush strokes go. Every style covers the whole screen (even with a stroke's thin ends), and each stroke is { pts: [[x, y]...], width, tone (0..1, picks a palette colour), delay, dur }
     with delay and dur as fractions of the phase (delay + dur <= 1).
       waves  (Chat)      rows of wide wavy strokes, with a second layer of slanted ones over them
       scan   (the PC)    straight lines drawn top to bottom, like a screen being scanned
       stack  (Files)     slanted slabs that cascade down the page like papers being dealt, with short tabs on top
       swirl  (Settings)  spirals that wind out from the middle like a dial being turned */
  function strokePlan(seed, w, h, opts) {
    opts = opts || {};
    if (opts.style === "scan") return scanPlan(seed, w, h, opts);
    if (opts.style === "stack") return stackPlan(seed, w, h, opts);
    if (opts.style === "swirl") return swirlPlan(seed, w, h, opts);
    return wavePlan(seed, w, h, opts);
  }

  function scanPlan(seed, w, h, opts) {
    var rnd = mulberry(seed), rows = Math.max(10, opts.rows || Math.round(h / 46)), band = h / (rows - 1), out = [], n = 24;
    for (var r = 0; r < rows; r++) {
      var y0 = r * band, pts = [];
      for (var i = 0; i < n; i++) { var u = i / (n - 1); pts.push([(-0.14 + 1.28 * u) * w, y0 + Math.sin(6.283 * u * 1.5 + r) * band * 0.05 + (rnd() - 0.5) * band * 0.04]); }
      out.push({ pts: pts, width: band * (opts.wipe ? 2.1 : 1.8) + rnd() * band * 0.15, tone: rnd(), delay: (r / rows) * 0.52 + rnd() * 0.03, dur: 0.45 });
    }
    return out;
  }

  function stackPlan(seed, w, h, opts) {
    var rnd = mulberry(seed), rows = Math.max(6, opts.rows || Math.round(h / 70)), band = h / (rows - 1), out = [], n = 24, slope = -0.2 - rnd() * 0.05;
    var spill = Math.ceil(Math.abs(slope) * w * 0.7 / band), total = rows + spill * 2;
    for (var r = 0; r < total; r++) {
      var y0 = (r - spill) * band, pts = [];
      for (var i = 0; i < n; i++) { var u = i / (n - 1), x = (-0.14 + 1.28 * u) * w; pts.push([x, y0 + slope * (x - w / 2) + Math.sin(6.283 * u * 1.2 + r * 2) * band * 0.06]); }
      if (r % 2) pts.reverse();
      out.push({ pts: pts, width: band * (opts.wipe ? 2.1 : 1.8) + rnd() * band * 0.15, tone: rnd(), delay: (r / total) * 0.5 + rnd() * 0.04, dur: 0.45 });
    }
    if (!opts.wipe) {
      for (var k = 0, extra = Math.ceil(rows * 0.8); k < extra; k++) {   // short tabs sticking out of the stack
        var cy = rnd() * h, x0 = (rnd() * 0.55 - 0.1) * w, x1 = x0 + (0.3 + rnd() * 0.4) * w, q = [];
        for (var j = 0; j < n; j++) { var t = j / (n - 1), px = x0 + (x1 - x0) * t; q.push([px, cy + slope * (px - w / 2)]); }
        out.push({ pts: q, width: band * (0.8 + rnd() * 0.5), tone: rnd(), delay: 0.3 + rnd() * 0.25, dur: 0.45 });
      }
    }
    return out;
  }

  function swirlPlan(seed, w, h, opts) {
    var rnd = mulberry(seed), count = Math.max(8, opts.rows || Math.round(h / 70)), out = [], n = 48, cx = w / 2, cy = h / 2;
    var reach = Math.hypot(w, h) / 2 * 1.06, band = reach / (count - 1);
    for (var i = 0; i < count + 1; i++) {   // one ring more than needed, so the corners are covered however the spiral ends
      var a0 = rnd() * 6.283, dir = i % 2 ? -1 : 1, turns = 1.4 + rnd() * 0.15, wob = band * 0.08, ph = rnd() * 6.28, pts = [];
      for (var k = 0; k < n; k++) {
        var u = k / (n - 1), ang = a0 + dir * turns * 6.283 * u, rad = i * band + band * 0.6 * u + wob * Math.sin(u * 17 + ph);
        pts.push([cx + Math.cos(ang) * rad, cy + Math.sin(ang) * rad]);
      }
      out.push({ pts: pts, width: band * (opts.wipe ? 2.2 : 1.9) + rnd() * band * 0.15 + (i === 0 ? band : 0), tone: rnd(), delay: (i / (count + 1)) * 0.5 + rnd() * 0.03, dur: 0.45 });
    }
    return out;
  }

  function wavePlan(seed, w, h, opts) {
    var rnd = mulberry(seed), rows = Math.max(6, opts.rows || Math.round(h / 70)), band = h / (rows - 1), out = [], n = 30;
    var wide = opts.wipe ? 2.1 : 1.75;
    for (var r = 0; r < rows; r++) {
      var y0 = r * band, amp = band * (0.1 + rnd() * 0.13), freq = 0.7 + rnd() * 1.3, phase = rnd() * 6.28, pts = [];   // the first row is centred on the top edge and the last on the bottom edge
      for (var i = 0; i < n; i++) {
        var u = i / (n - 1), x = (-0.14 + 1.28 * u) * w;
        pts.push([x, y0 + amp * Math.sin(6.283 * u * freq + phase) + (rnd() - 0.5) * band * 0.06]);
      }
      if (r % 2) pts.reverse();
      out.push({ pts: pts, width: band * wide + rnd() * band * 0.2, tone: rnd(), delay: opts.wipe ? (r / rows) * 0.5 + rnd() * 0.06 : rnd() * 0.5, dur: 0.45 });
    }
    if (!opts.wipe) {
      var extra = Math.ceil(rows * 0.7);
      for (var k = 0; k < extra; k++) {
        var cy = rnd() * h, slope = (rnd() - 0.5) * 0.7 * h / w, a2 = band * (0.2 + rnd() * 0.3), f2 = 1 + rnd() * 1.5, p2 = rnd() * 6.28, q = [];
        for (var j = 0; j < n; j++) {
          var t = j / (n - 1), px = (-0.14 + 1.28 * t) * w;
          q.push([px, cy + slope * (px - w / 2) + a2 * Math.sin(6.283 * t * f2 + p2)]);
        }
        if (rnd() < 0.5) q.reverse();
        out.push({ pts: q, width: band * (0.9 + rnd() * 0.5), tone: rnd(), delay: 0.3 + rnd() * 0.25, dur: 0.45 });
      }
    }
    return out;
  }

  function pointAt(pts, p) {
    var f = clamp01(p) * (pts.length - 1), i = Math.min(pts.length - 2, Math.floor(f)), t = f - i, a = pts[i], b = pts[i + 1];
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, b[0] - a[0], b[1] - a[1]];
  }
  /* A brush is thinner where it lands and where it lifts. */
  var taper = function (p) { return 0.4 + 0.6 * Math.min(1, p / 0.12, (1 - p) / 0.12 + 0.0001, 1); };

  var BRISTLES = 9;
  var active = null;

  /* What each tab's paint looks like: the stroke style, the colours (Chat uses the person's accent instead) and the little animation on the title's icon. */
  var THEMES = {
    chat: { style: "waves", above: false },
    pc: { style: "scan", above: true, palette: ["#38bdf8", "#2563eb", "#0ea5e9", "#1e3a8a", "#94a3b8", "#67e8f9", "#e0f2fe", "#0f172a"] },
    files: { style: "stack", above: true, palette: ["#fbbf24", "#f59e0b", "#fb923c", "#92400e", "#fde68a", "#d97706", "#fef3c7", "#b45309"] },
    settings: { style: "swirl", above: true, palette: ["#a78bfa", "#8b5cf6", "#ec4899", "#4f46e5", "#2dd4bf", "#c4b5fd", "#6366f1", "#f0abfc"] },
  };

  /* Paints over the screen, calls onCovered() when nothing underneath shows (switch the page there), then scrapes the paint away.
     opts: { theme: "chat" | "pc" | "files" | "settings", colors: [c1, c2] (for chat), label, badge (a node), level: "full" | "simple", seed, onCovered(), onDone(), safety (ms) }. Returns { skip() }.
     Starting a new paint while one is running lets the first one finish at once (so its page is switched) and then starts the new one. */
  function paint(opts) {
    opts = opts || {};
    if (active) active.skip();
    var theme = THEMES[opts.theme] || THEMES.chat;
    var covered = false, finished = false, me = null;
    var cover = function () { if (covered) return; covered = true; try { if (opts.onCovered) opts.onCovered(); } catch (e) { /* the page still switches */ } };
    var finish = function () { if (finished) return; finished = true; if (opts.onDone) try { opts.onDone(); } catch (e) { /* nothing to undo */ } };
    var canvas = hasDom && document.createElement("canvas"), ctx = canvas && canvas.getContext && canvas.getContext("2d");
    if (!ctx || calm()) { cover(); finish(); return { skip: function () {} }; }

    var full = opts.level !== "simple";
    var W = Math.max(1, root.innerWidth || 360), H = Math.max(1, root.innerHeight || 640), dpr = Math.min(root.devicePixelRatio || 1, full ? 1.5 : 1);
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    ctx.scale(dpr, dpr); ctx.lineCap = "round"; ctx.lineJoin = "round";
    var seed = opts.seed || Math.floor(Math.random() * 1e9);
    var pal = theme.palette ? theme.palette.map(hexToHsl) : palette((opts.colors || [])[0], (opts.colors || [])[1]);
    var rows = Math.round(H / (full ? (theme.style === "scan" ? 46 : 70) : (theme.style === "scan" ? 70 : 100)));
    var ink = strokePlan(seed, W, H, { rows: rows, style: theme.style }), wipe = strokePlan(seed + 7, W, H, { rows: Math.max(6, Math.round(rows * 0.8)), wipe: true, style: theme.style });
    var rnd = mulberry(seed + 3);
    ink.forEach(function (s) {
      var c = pal[Math.floor(s.tone * pal.length) % pal.length];
      s.cols = []; s.bw = []; s.last = 0;
      for (var k = 0; k < BRISTLES; k++) {
        var u = k / (BRISTLES - 1) - 0.5, base = c[2] - u * 0.2 + (rnd() - 0.5) * 0.1, hue = c[0] + (rnd() - 0.5) * 8, sat = c[1] * (0.9 + rnd() * 0.15);
        if (k === 2) base += 0.13;   // a ridge of paint catching the light
        if (k === 6) base -= 0.11;   // and the groove beside it
        s.cols.push([hsl(hue, sat, base), hsl(hue, sat, base + 0.05), hsl(hue, sat, base - 0.05)]);
        s.bw.push(1.5 + rnd() * 0.7);   // some bristles are fatter, so the stroke is streaky
      }
    });
    wipe.forEach(function (s) { s.last = 0; });

    var T_IN = full ? 560 : 400, T_HOLD = full ? 220 : 120, T_OUT = full ? 560 : 400, T_FADE = 140;
    var wrap = el("div", "pnt"), title = el("div", "pnt-title");
    wrap.setAttribute("aria-hidden", "true");
    wrap.classList.add("k-" + theme.style);
    canvas.className = "pnt-cv";
    if (!full) wrap.classList.add("is-flat");
    if (opts.badge) { var ic = el("span", "pnt-ic"); ic.append(opts.badge); title.append(ic); }
    title.append(el("b", "", opts.label || ""));
    wrap.append(canvas, el("i", "pnt-sheen"), title);
    var bar = document.getElementById("tabbar");
    if (theme.above || !bar || !bar.parentNode) { wrap.classList.add("is-top"); document.body.append(wrap); }   // Files, Settings and the PC open over the tab bar, so the paint covers it too
    else bar.parentNode.insertBefore(wrap, bar);                                                                 // Chat's paint goes just under the bar, so the bar's glass highlight keeps sliding on top of it

    var start = 0, raf = 0, backup = 0, phase = "in";
    function dab(s, a, b) {
      var steps = Math.max(1, Math.ceil((b - a) * (s.pts.length - 1))), i;   // one chord per sample point, so a curved stroke is drawn along its curve
      for (i = 0; i < steps; i++) {
        var p0 = a + (b - a) * i / steps, p1 = a + (b - a) * (i + 1) / steps, A = pointAt(s.pts, p0), B = pointAt(s.pts, p1);
        var len = Math.hypot(A[2], A[3]) || 1, nx = -A[3] / len, ny = A[2] / len, tp = taper((p0 + p1) / 2), span = s.width * tp;
        for (var k = 0; k < BRISTLES; k++) {
          var off = (k / (BRISTLES - 1) - 0.5) * span + Math.sin(p0 * 31 + k * 1.7) * span * 0.012;
          ctx.strokeStyle = s.cols[k][Math.floor(p0 * 9 + k * 1.3) % 3];   // the shade changes along each bristle, as paint does when the brush runs dry
          ctx.lineWidth = span / (BRISTLES - 1) * s.bw[k] + 0.6;
          ctx.beginPath(); ctx.moveTo(A[0] + nx * off, A[1] + ny * off); ctx.lineTo(B[0] + nx * off, B[1] + ny * off); ctx.stroke();
        }
      }
    }
    function scrape(s, a, b) {
      var steps = Math.max(1, Math.ceil((b - a) * (s.pts.length - 1))), i;
      [[0.4, 1], [1, 0.7]].forEach(function (pass) {
        ctx.globalAlpha = pass[0]; ctx.lineWidth = s.width * pass[1]; ctx.beginPath();
        for (i = 0; i <= steps; i++) { var P = pointAt(s.pts, a + (b - a) * i / steps); if (i) ctx.lineTo(P[0], P[1]); else ctx.moveTo(P[0], P[1]); }
        ctx.stroke();
      });
      ctx.globalAlpha = 1;
    }
    function advance(list, t, T, draw) {
      list.forEach(function (s) {
        var p = easeInOut((t / T - s.delay) / s.dur);
        if (p > s.last) { draw(s, s.last, p); s.last = p; }
      });
    }
    function end() {
      if (active === me) active = null;
      cancelAnimationFrame(raf); clearTimeout(backup);
      cover();
      wrap.classList.add("is-gone");
      setTimeout(function () { if (wrap.parentNode) wrap.remove(); finish(); }, 160);
    }
    function frame(now) {
      if (!start) start = now;
      var t = now - start;
      if (t < T_IN) {
        advance(ink, t, T_IN, dab);
        if (full && t > 40) wrap.classList.add("is-in");
        if (t > T_IN * 0.35) wrap.classList.add("is-titled");
      } else {
        if (phase === "in") { advance(ink, T_IN, T_IN, dab); phase = "hold"; cover(); }
        var o = t - T_IN - T_HOLD;
        if (o >= 0) {
          if (phase === "hold") { phase = "out"; wrap.classList.add("is-wiping"); ctx.globalCompositeOperation = "destination-out"; ctx.strokeStyle = "#000"; }
          advance(wipe, Math.min(o, T_OUT), T_OUT, scrape);
          if (o > T_OUT) wrap.classList.add("is-fading");
          if (o > T_OUT + T_FADE) { end(); return; }
        }
      }
      raf = requestAnimationFrame(frame);
    }
    wrap.addEventListener("pointerdown", function (e) { e.preventDefault(); end(); });   // a tap skips it
    backup = setTimeout(end, (opts.safety || 0) || T_IN + T_HOLD + T_OUT + T_FADE + 2500);   // a hidden tab never runs frames; this still lets go of the page
    raf = requestAnimationFrame(frame);
    me = active = { skip: end };
    return me;
  }

  var api = { resist: resist, swipeTarget: swipeTarget, swipeOffset: swipeOffset, pullState: pullState, REVEAL: REVEAL, PULL_READY: PULL_READY,
              swipe: swipe, pull: pull, burst: burst, spot: spot, art: art, skeleton: skeleton, arts: function () { return Object.keys(ART); },
              paint: paint, strokePlan: strokePlan, palette: palette, hexToHsl: hexToHsl, mulberry: mulberry, easeInOut: easeInOut, pointAt: pointAt, themes: function () { return Object.keys(THEMES); } };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.AfpUx = api;
})(typeof window !== "undefined" ? window : this);
