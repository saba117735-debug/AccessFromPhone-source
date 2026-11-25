/* daily.js: the everyday tools on Home. A control centre (live volume and brightness sliders, media keys) and nine tools that open as sheets:
 * Inbox, Notes, Remind, Watch, Recent, Snap, Print, PC alerts and the Briefing.
 *
 * The PC decides everything (what is allowed, who may do it); this file only shows it and sends the person's taps. It is fetched the first time Home
 * has the PC's answer. `AfpDaily.create(deps)` gets the few page helpers it needs (h, ico, api, ask, toast, haptic, openSheet, ...) and gives back
 * { sync, homeCard, open... }. The wording, time maths and file kinds are plain functions, tested on their own: tests/js/daily_test.js.
 * Touch and motion (swipe to delete, pull to refresh, the success burst, loading shapes, empty-state pictures) come from ux.js; without it everything
 * still works, just without the gestures. Everything that comes from the PC or from other apps is put on the page with textContent, never as HTML.
 */
(function (root) {
  "use strict";

  var POWER_WORDS = { lock: "Lock this PC", screen_off: "Black out the screen", sleep: "Put this PC to sleep", hibernate: "Hibernate this PC", restart: "Restart this PC", shutdown: "Shut down this PC" };
  var POWER_SHORT = { lock: "Lock", screen_off: "Screen off", sleep: "Sleep", hibernate: "Hibernate", restart: "Restart", shutdown: "Shut down" };
  var REPEAT_WORDS = { daily: "Every day", weekdays: "Weekdays", weekly: "Every week" };
  var PRINTABLE = ["pdf", "txt", "png", "jpg", "jpeg", "bmp", "gif", "tif", "tiff", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "rtf", "odt", "csv"];
  var KINDS = {
    image: ["png", "jpg", "jpeg", "gif", "bmp", "webp", "heic", "tif", "tiff", "svg"], video: ["mp4", "mkv", "mov", "avi", "webm", "m4v"], audio: ["mp3", "wav", "flac", "m4a", "ogg", "aac"],
    pdf: ["pdf"], doc: ["doc", "docx", "txt", "rtf", "odt", "md"], sheet: ["xls", "xlsx", "csv", "ods"], slides: ["ppt", "pptx", "odp"],
    archive: ["zip", "rar", "7z", "tar", "gz"], code: ["js", "ts", "py", "json", "html", "css", "xml", "yaml", "yml", "java", "c", "cpp", "cs", "go", "rs", "sh", "ps1", "bat"],
  };
  var KIND_ICON = { folder: "folder", image: "image", video: "play", audio: "volume", pdf: "file", doc: "file", sheet: "table", slides: "monitor", archive: "download", code: "braces", link: "globe", text: "note", file: "file" };

  /* ---------------------------------------------------------------- wording and time (pure)  */

  function sizeText(n) {
    n = Math.max(0, Number(n) || 0);
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return Math.round(n / 1024) + " KB";
    if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(n < 10 * 1024 * 1024 ? 1 : 0) + " MB";
    return (n / 1024 / 1024 / 1024).toFixed(1) + " GB";
  }

  var pad2 = function (n) { return (n < 10 ? "0" : "") + n; };
  function clock(date) {
    var hh = date.getHours(), ap = hh >= 12 ? "pm" : "am";
    return ((hh % 12) || 12) + ":" + pad2(date.getMinutes()) + " " + ap;
  }
  var DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"], MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function sameDay(a, b) { return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate(); }

  /* "today 5:30 pm", "tomorrow 8:00 am", "Mon 4 May, 9:00 am" (seconds since 1970, and now in seconds). */
  function whenText(ts, now) {
    var d = new Date(ts * 1000), n = new Date((now == null ? Date.now() / 1000 : now) * 1000), t = new Date(n.getTime() + 86400000);
    if (sameDay(d, n)) return "today " + clock(d);
    if (sameDay(d, t)) return "tomorrow " + clock(d);
    return DAYS[d.getDay()] + " " + d.getDate() + " " + MONTHS[d.getMonth()] + ", " + clock(d);
  }

  function agoText(ts, now) {
    var s = Math.max(0, Math.round((now == null ? Date.now() / 1000 : now) - ts));
    if (s < 60) return "just now";
    if (s < 3600) return Math.round(s / 60) + " min ago";
    if (s < 86400) return Math.round(s / 3600) + " h ago";
    return Math.round(s / 86400) + " d ago";
  }

  /* "in 12 min", "in 2 h 14 m", "in 3 days": how long until a time. A time that has come says "now". */
  function inText(ts, now) {
    var s = Math.round(ts - (now == null ? Date.now() / 1000 : now));
    if (s < 45) return "now";
    var m = Math.round(s / 60);
    if (m < 60) return "in " + m + " min";
    var h = Math.floor(m / 60), r = m % 60;
    if (h < 24) return "in " + h + " h" + (r ? " " + r + " m" : "");
    var days = Math.round(h / 24);
    return "in " + days + (days === 1 ? " day" : " days");
  }

  function speedText(bytesPerSecond) {
    var b = Math.max(0, Number(bytesPerSecond) || 0);
    return b < 1024 * 1024 ? Math.round(b / 1024) + " KB/s" : (b / 1024 / 1024).toFixed(1) + " MB/s";
  }
  function etaText(seconds) {
    var s = Math.round(seconds);
    if (!isFinite(s) || s < 0) return "";
    if (s < 5) return "a few seconds left";
    if (s < 60) return s + " s left";
    return Math.ceil(s / 60) + " min left";
  }
  function percent(loaded, total) { return total > 0 ? Math.max(0, Math.min(100, Math.round(loaded / total * 100))) : 0; }

  function repeatText(r) { return REPEAT_WORDS[r] || ""; }
  function powerText(k) { return POWER_WORDS[k] || k; }
  function powerShort(k) { return POWER_SHORT[k] || k; }

  /* Ready-made times, all in the future: in 10 minutes, in an hour, this evening (6 pm), tomorrow morning (8 am). */
  function quickTimes(now) {
    var base = new Date((now == null ? Date.now() / 1000 : now) * 1000), out = [];
    out.push({ label: "In 10 min", ts: Math.round(base.getTime() / 1000) + 600 });
    out.push({ label: "In 1 hour", ts: Math.round(base.getTime() / 1000) + 3600 });
    var evening = new Date(base); evening.setHours(18, 0, 0, 0);
    if (evening.getTime() > base.getTime() + 15 * 60000) out.push({ label: "This evening", ts: Math.round(evening.getTime() / 1000) });
    var morning = new Date(base); morning.setDate(morning.getDate() + 1); morning.setHours(8, 0, 0, 0);
    out.push({ label: "Tomorrow 8 am", ts: Math.round(morning.getTime() / 1000) });
    return out;
  }

  /* An <input type="datetime-local"> value ("2026-05-04T21:30") as seconds since 1970 in the phone's own time zone, or null. */
  function inputToTs(value) {
    var m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(String(value || ""));
    if (!m) return null;
    var d = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], 0, 0);
    if (isNaN(d.getTime()) || d.getMonth() !== +m[2] - 1 || d.getDate() !== +m[3] || d.getHours() !== +m[4]) return null;  // "31 February" or "25:00" rolls over: not a real time
    return Math.round(d.getTime() / 1000);
  }
  function tsToInput(ts) {
    var d = new Date(ts * 1000);
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate()) + "T" + pad2(d.getHours()) + ":" + pad2(d.getMinutes());
  }

  function taskText(t) { return t.kind === "remind" ? t.text : powerText(t.action); }
  function watchText(w) { return w.label + (w.then ? " · then " + powerText(w.then).toLowerCase() : ""); }

  function extensionOf(name) { var m = /\.([A-Za-z0-9]{1,6})$/.exec(String(name || "")); return m ? m[1].toLowerCase() : ""; }
  function printable(name) { return PRINTABLE.indexOf(extensionOf(name)) >= 0; }

  /* What a file or folder is, for its icon and colour: folder, image, video, audio, pdf, doc, sheet, slides, archive, code or file. */
  function kindOf(name, isDir) {
    if (isDir) return "folder";
    var ext = extensionOf(name);
    for (var k in KINDS) if (Object.prototype.hasOwnProperty.call(KINDS, k) && KINDS[k].indexOf(ext) >= 0) return k;
    return "file";
  }

  /* Notes grouped by day, newest first: [{ date, items: [...] }]. */
  function groupNotes(notes) {
    var groups = [], by = {};
    (notes || []).forEach(function (n) {
      if (!by[n.date]) { by[n.date] = { date: n.date, items: [] }; groups.push(by[n.date]); }
      by[n.date].items.push(n);
    });
    return groups;
  }

  /* The day heading for a note's date ("2026-05-04"). */
  function dayText(date, now) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date || "");
    if (!m) return date || "";
    var d = new Date(+m[1], +m[2] - 1, +m[3]), n = new Date((now == null ? Date.now() / 1000 : now) * 1000), y = new Date(n.getTime() - 86400000);
    if (sameDay(d, n)) return "Today";
    if (sameDay(d, y)) return "Yesterday";
    return DAYS[d.getDay()] + " " + d.getDate() + " " + MONTHS[d.getMonth()];
  }

  /* How long a PC has been up ("3 d 4 h", "5 h", "20 min"). */
  function uptimeText(seconds) {
    var s = Math.max(0, Math.round(seconds || 0)), d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
    if (d) return d + " d " + h + " h";
    if (h) return h + " h" + (m ? " " + m + " m" : "");
    return Math.max(1, m) + " min";
  }

  /* What a request that failed should say, using the PC's own words when it gave some. */
  function errorText(e) { return (e && e.message) ? e.message : "That didn't work."; }

  /* ---------------------------------------------------------------- the component  */

  function create(d) {
    var h = d.h, ico = d.ico;
    var st = { info: null, polling: 0, lastAlert: 0, counts: null, countsAt: 0, level: null, levels: { volume: null, brightness: null, at: 0 }, sliders: {} };

    var info = function () { return st.info && st.info.daily && st.info.daily.enabled ? st.info.daily : null; };
    var level = function () { return st.level || "operator"; };
    var rank = function (l) { return ["viewer", "editor", "operator", "admin"].indexOf(l); };
    var may = function (needed) { return rank(level()) >= rank(needed); };
    var Ux = function () { return root.AfpUx || null; };
    var tick = function (kind) { d.haptic(kind || "tick"); };

    function fail(e) { if (e && e.code === 401) return; d.toast(errorText(e)); d.haptic("error"); }

    function call(path, method, body, raw) {
      var opts = { method: method || "GET" };
      if (body !== undefined) opts.body = JSON.stringify(body);
      return d.api("/daily" + path, opts).then(function (res) { return raw ? res : res.json(); });
    }

    /* "it worked": a small burst where the person tapped, and a buzz */
    function done(where) { d.haptic("success"); if (Ux()) Ux().burst(where); }

    function button(icon, label, onclick, cls) {
      return h("button", { class: "btn " + (cls || ""), type: "button", onclick: onclick }, icon ? ico(icon) : null, label ? h("span", { text: label }) : null);
    }
    function lockedNote(needed, what) {
      if (may(needed)) return h("span", { hidden: true });   // (a real node: the sheets fill themselves with replaceChildren, which would print a null as the word "null")
      return h("p", { class: "dy-lock" }, ico("lock"), h("span", { text: what + " needs " + (needed === "admin" ? "the Admin phone" : needed + " access") + ". You can look, but not change." }));
    }

    /* A calm "nothing here" with a picture, a line and, if it helps, the thing to do next. */
    function emptyState(art, title, text, action) {
      return h("div", { class: "dy-empty" }, Ux() ? Ux().art(art) : null, h("b", { text: title }), text ? h("p", { text: text }) : null, action || null);
    }

    /* ---- one row, in the same shape everywhere: an icon tile, a title and a line under it, and whatever belongs on the right ---- */
    function tile(iconName, kind) { return h("span", { class: "dy-ric k-" + (kind || "file") }, ico(iconName)); }

    function row(o) {
      var node = h(o.onTap ? "button" : "div", { class: "dy-row spot" + (o.onTap ? " is-tap" : ""), type: o.onTap ? "button" : undefined, onclick: o.onTap ? function () { tick("light"); o.onTap(); } : undefined },
        o.tileNode || tile(o.icon, o.kind),
        h("div", { class: "dy-rtx" }, h("b", { text: o.title }), o.sub ? h("small", { text: o.sub }) : null, o.below || null),
        o.trail || null);
      if (o.swipe && Ux()) return Ux().swipe(node, { label: o.swipe.label || "Delete", onDelete: o.swipe.onDelete, tick: tick });
      return node;
    }

    /* A swipe-to-delete row asks the PC first; if it says no, the row slides back and the reason is shown. */
    function deleter(path, after) {
      return function () {
        return call(path, "DELETE").then(function () { tick("medium"); if (after) after(); }, function (e) { fail(e); throw e; });
      };
    }

    function chips(list, current, onPick, cls) {
      return h("div", { class: "dy-chips " + (cls || "") }, list.map(function (c) {
        return h("button", { class: "dy-chip", type: "button", "aria-pressed": String(current === c.value), onclick: function () { tick("tick"); onPick(c.value); } }, c.icon ? ico(c.icon) : null, h("span", { text: c.label }));
      }));
    }

    /* ---- sync: called by the page whenever it has the PC's answer; keeps the alert watcher running ---- */

    function sync(pcInfo) {
      st.info = pcInfo;
      if (info() && !st.polling) {
        st.polling = setInterval(pollAlerts, 15000);
        setTimeout(pollAlerts, 1500);
      } else if (!info() && st.polling) { clearInterval(st.polling); st.polling = 0; }
    }

    function pollAlerts() {
      if (document.hidden || !info()) return;
      call("/alerts?after=" + st.lastAlert).then(function (out) {
        (out.alerts || []).forEach(function (a) {
          st.lastAlert = Math.max(st.lastAlert, a.id);
          d.toast(a.title + (a.body ? ": " + a.body : ""));
          d.haptic("alert");
        });
      }).catch(function () { /* the next check will try again */ });
    }

    function refreshCounts(card) {
      if (Date.now() - st.countsAt < 20000 && st.counts) { paintCounts(card); return; }
      st.countsAt = Date.now();
      call("/state").then(function (out) {
        st.counts = out.counts; st.level = out.level; st.lastAlert = Math.max(st.lastAlert, out.alert_id || 0);
        paintCounts(card); paintSliders();
      }).catch(function () { /* quiet */ });
    }
    function paintCounts(card) {
      if (!card || !st.counts) return;
      var put = function (key, n) { var el = card.querySelector('[data-badge="' + key + '"]'); if (el) { el.textContent = n > 0 ? String(n) : ""; el.hidden = !(n > 0); } };
      put("inbox", (st.counts.in_inbox || 0) + (st.counts.to_phone || 0));
      put("tasks", st.counts.tasks || 0);
      put("watch", st.counts.watchers || 0);
    }

    /* ================================================================ the control centre */

    /* A slider that is a glass pill: the fill is drawn, the native range input on top does the touching (so a finger, a keyboard and a screen reader all work). */
    function slider(kind, iconName, label) {
      var input = h("input", { type: "range", min: kind === "brightness" ? "5" : "0", max: "100", step: "1", value: "50", "aria-label": label });
      var value = h("span", { class: "dy-sv", text: "" });
      var iconBtn = h("button", { class: "dy-si", type: "button", "aria-label": kind === "volume" ? "Mute or unmute" : label, tabindex: kind === "volume" ? "0" : "-1" }, ico(iconName));
      var box = h("div", { class: "dy-slider is-loading", "data-kind": kind }, h("i", { class: "dy-fill" }), iconBtn,
        h("div", { class: "dy-st" }, h("b", { text: label }), value), input);
      var s = { kind: kind, box: box, input: input, value: value, iconBtn: iconBtn, dragging: false, sent: 0, state: null };

      var show = function (pct) { box.style.setProperty("--v", pct + "%"); value.textContent = pct + "%"; input.setAttribute("aria-valuetext", pct + " percent"); };
      s.paint = function (state) {
        s.state = state;
        box.classList.remove("is-loading");
        box.classList.toggle("is-off", !state || !state.available);
        box.classList.toggle("is-muted", !!(state && state.muted));
        iconBtn.firstChild.replaceWith(ico(kind === "volume" ? (state && state.muted ? "volumeOff" : (state && state.level < 40 ? "volumeLow" : "volume")) : (state && state.level < 40 ? "sunDim" : "sun")));
        var lock = !may("operator") || !state || !state.available;
        input.disabled = lock;
        if (!state || !state.available) { value.textContent = kind === "brightness" ? "Not on this screen" : "No sound device"; box.style.setProperty("--v", "0%"); return; }
        if (!s.dragging) { input.value = String(state.level); show(state.level); }
        if (kind === "volume") value.textContent = state.muted ? "Muted" : state.level + "%";
      };
      var send = function (final) {
        var pct = Number(input.value), now = Date.now();
        if (!final && now - s.sent < 110) return;   // a drag is sent a few times a second, not on every pixel
        s.sent = now;
        var req = kind === "volume" ? call("/volume", "POST", { level: pct }) : call("/brightness", "POST", { level: pct });
        req.then(function (out) { if (final || !s.dragging) { st.levels[kind] = out; s.paint(out); } if (final) tick("tick"); })
          .catch(function (e) { fail(e); loadLevels(true); });
      };
      input.addEventListener("input", function () {
        s.dragging = true; box.classList.add("is-drag"); show(Number(input.value));
        if (kind === "volume") { box.classList.remove("is-muted"); send(false); }
      });
      input.addEventListener("change", function () { s.dragging = false; box.classList.remove("is-drag"); send(true); });
      input.addEventListener("pointerup", function () { s.dragging = false; box.classList.remove("is-drag"); });
      if (kind === "volume") iconBtn.addEventListener("click", function () {
        if (!may("operator") || !s.state || !s.state.available) return;
        tick("light");
        call("/volume", "POST", { muted: !s.state.muted }).then(function (out) { st.levels.volume = out; s.paint(out); }).catch(fail);
      });
      return s;
    }

    function paintSliders() {
      if (st.medias) st.medias.hidden = !may("operator");   // a phone that may only look has no media keys
      Object.keys(st.sliders).forEach(function (k) { if (st.levels[k]) st.sliders[k].paint(st.levels[k]); });
    }

    function loadLevels(force) {
      var fresh = Date.now() - st.levels.at < 4000;
      Object.keys(st.sliders).forEach(function (k) { if (st.levels[k]) st.sliders[k].paint(st.levels[k]); });
      if (fresh && !force) return;
      st.levels.at = Date.now();
      // an answer that was asked for before the finger last touched the slider is out of date
      var take = function (k, v) {
        var s = st.sliders[k];
        st.levels[k] = v;
        if (s && !s.dragging && Date.now() - s.sent > 2500) s.paint(v);
      };
      call("/volume").then(function (v) { take("volume", v); }).catch(function () { /* quiet */ });
      if (st.sliders.brightness) call("/brightness").then(function (b) { take("brightness", b); }).catch(function () { /* quiet */ });
    }

    /* The PC changes these by itself too (an automatic-brightness laptop, the keyboard's volume keys), so while the card is on screen it re-reads them every few seconds. */
    function watchLevels(card) {
      if (st.levelTimer) clearInterval(st.levelTimer);
      st.levelTimer = setInterval(function () {
        if (card.isConnected === false) { clearInterval(st.levelTimer); st.levelTimer = 0; return; }
        if (typeof document !== "undefined" && document.hidden) return;
        loadLevels(true);
      }, 6000);
    }

    function media(action, iconName, label, big) {
      return h("button", { class: "dy-media" + (big ? " is-big" : ""), type: "button", "aria-label": label, title: label, onclick: function (e) {
        tick("light");
        var el = e.currentTarget; el.classList.add("is-hit"); setTimeout(function () { el.classList.remove("is-hit"); }, 260);
        call("/control", "POST", { action: action, steps: 1 }).catch(fail);
      } }, ico(iconName));
    }

    var TOOLS = [
      ["inbox", "inbox", "Inbox", "inbox"], ["notes", "note", "Notes", ""], ["tasks", "clock", "Remind", "tasks"], ["watch", "eye", "Watch", "watch"],
      ["recent", "star", "Recent", ""], ["snap", "camera", "Snap", ""], ["print", "printer", "Print", ""], ["pcnotes", "bell", "PC alerts", ""], ["brief", "sparkle", "Briefing", ""],
    ];

    function homeCard() {
      var f = info();
      if (!f) return null;
      var run = { inbox: openInbox, notes: openNotes, tasks: openTasks, watch: openWatch, recent: openRecent, snap: openSnap, print: openPrint, pcnotes: openPcNotices, brief: openBriefing };
      var show = { print: f.printing, pcnotes: f.notifications };
      var tiles = TOOLS.filter(function (t) { return show[t[0]] === undefined || show[t[0]]; }).map(function (t) {
        return h("button", { class: "dy-tile spot t-" + t[0], type: "button", "data-tool": t[0], onclick: function () { d.haptic("light"); run[t[0]](); } },
          h("span", { class: "dy-tic" }, ico(t[1]), t[3] ? h("i", { class: "dy-badge", "data-badge": t[3], hidden: true }) : null), h("span", { class: "dy-tl", text: t[2] }));
      });
      var controls = null;
      st.sliders = {};
      if (f.controls) {
        st.sliders = { volume: slider("volume", "volume", "Volume"), brightness: slider("brightness", "sun", "Brightness") };
        st.medias = h("div", { class: "dy-medias", role: "group", "aria-label": "Media", hidden: !may("operator") }, media("previous_track", "prev", "Previous"), media("play_pause", "play", "Play or pause", true), media("next_track", "next", "Next"));
        controls = h("div", { class: "dy-controls" }, h("div", { class: "dy-sliders" }, st.sliders.volume.box, st.sliders.brightness.box), st.medias);
      }
      var card = h("section", { class: "hm-card dy-card", "aria-label": "Everyday tools" },
        h("div", { class: "hm-head" }, h("b", { text: "Control centre" }), h("span", { class: "hm-chip ok" }, h("i", { class: "dy-live" }), "Live")),
        controls, h("div", { class: "dy-tiles" }, tiles));
      refreshCounts(card);
      if (f.controls) { loadLevels(); watchLevels(card); }
      return card;
    }

    /* ================================================================ sheets */

    /* A sheet that shows loading shapes, fills itself from the PC, can be pulled down to refresh, and says what went wrong in words. */
    function sheetOf(title, route, fill, skeleton) {
      var body = h("div", { class: "dy" });
      var handle = { body: body, pull: null };
      var sheet = d.openSheet({ title: title, body: body, route: route, onClose: function () { if (handle.pull) handle.pull.stop(); } });
      handle.sheet = sheet;
      var refill = function () {
        return Promise.resolve(fill(body, refill, sheet)).catch(function (e) {
          if (e && e.code === 401) return;
          body.replaceChildren(emptyState("remind", "That didn't load", errorText(e), button("retry", "Try again", refill, "primary sm")));
        });
      };
      body.replaceChildren(skeleton && Ux() ? Ux().skeleton(skeleton, 3) : h("p", { class: "note", text: "Asking your PC…" }));
      if (Ux() && sheet.body) handle.pull = Ux().pull(sheet.body, refill, { tick: tick });
      handle.refill = refill;
      refill();
      return handle;
    }

    /* Fills a sheet. A missing piece (null) is left out: replaceChildren would print it as the word "null". */
    function put(body, kids) { body.replaceChildren.apply(body, kids.filter(function (k) { return k; })); }

    function confirm(title, message, ok, tone) {
      return d.ask({ title: title, message: message, ok: ok || "Yes, do it", cancel: "Cancel", tone: tone || "accent", icon: tone === "danger" ? "alert" : "info" });
    }

    /* When the last swipe-deleted row goes, the list says it is empty instead of leaving a blank sheet. */
    function whenEmpty(body, refill) { return function () { setTimeout(function () { if (!body.querySelector(".swp")) refill(); }, 380); }; }

    function swipeHint() {
      var key = "afp_hint_swipe";
      try { if (localStorage.getItem(key)) return null; } catch (e) { return null; }
      var hint = h("p", { class: "dy-hint" }, ico("arrowLeft"), h("span", { text: "Swipe a row to the left to delete it." }));
      setTimeout(function () { try { localStorage.setItem(key, "1"); } catch (e) { /* private mode */ } }, 6000);
      return hint;
    }

    /* ---- the inbox ---- */

    function saveFrom(path, name) {
      d.toast("Fetching " + name + "…");
      return d.api(path).then(function (res) { return res.blob(); }).then(function (blob) { d.saveBlob(blob, name); }).catch(fail);
    }

    /* One file going to the PC, with a progress bar. XMLHttpRequest, because fetch cannot report how much of an upload has gone. */
    function upload(file, onProgress) {
      var xhr = new XMLHttpRequest();
      var promise = new Promise(function (resolve, reject) {
        xhr.open("PUT", "/api/daily/inbox/file?name=" + encodeURIComponent(file.name));
        xhr.setRequestHeader("X-Token", d.token());
        xhr.setRequestHeader("Content-Type", "application/octet-stream");
        xhr.upload.onprogress = function (e) { if (e.lengthComputable) onProgress(e.loaded, e.total); };
        xhr.onload = function () {
          if (xhr.status >= 200 && xhr.status < 300) { try { resolve(JSON.parse(xhr.responseText)); } catch (e) { resolve({}); } return; }
          var msg = "That didn't work.";
          try { var body = JSON.parse(xhr.responseText); msg = (body.detail && (body.detail.message || body.detail)) || msg; } catch (e) { /* keep it */ }
          var err = new Error(typeof msg === "string" ? msg : "That didn't work."); err.status = xhr.status;
          if (xhr.status === 401) d.api("/ping").catch(function () { /* this signs the page out, as for any other request */ });
          reject(err);
        };
        xhr.onerror = function () { reject(new Error("The connection dropped. Try again.")); };
        xhr.onabort = function () { var e = new Error("Cancelled"); e.cancelled = true; reject(e); };
        xhr.send(file);
      });
      return { promise: promise, abort: function () { xhr.abort(); } };
    }

    function uploadRow(file) {
      var bar = h("i", { class: "dy-prog-bar" }), meta = h("small", { text: sizeText(file.size) });
      var cancel = h("button", { class: "iconbtn dy-x", type: "button", "aria-label": "Cancel " + file.name }, ico("x"));
      var node = h("div", { class: "dy-row dy-up is-wait", role: "group", "aria-label": file.name },
        tile(KIND_ICON[kindOf(file.name)], kindOf(file.name)),
        h("div", { class: "dy-rtx" }, h("b", { text: file.name }), h("div", { class: "dy-prog", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": "0" }, bar), meta), cancel);
      return { node: node, bar: bar, meta: meta, cancel: cancel };
    }

    function openInbox() {
      var tab = "pc", queue = [], running = 0, uploads = h("div", { class: "dy-uploads" }), sh = null;
      sh = sheetOf("Inbox", "daily-inbox", function (body, refill) {
        return call("/inbox").then(function (data) {
          var f = info() || {};
          var seg = h("div", { class: "seg", role: "group", "aria-label": "Direction" },
            [["pc", "To the PC", data.to_pc.length], ["phone", "To this phone", data.to_phone.length]].map(function (t) {
              return h("button", { type: "button", "aria-pressed": String(tab === t[0]), onclick: function () { tab = t[0]; refill(); } }, t[1], t[2] ? h("i", { class: "dy-count", text: String(t[2]) }) : null);
            }));
          var kids = [seg];
          if (tab === "pc") {
            var box = h("textarea", { class: "dy-text", rows: "2", placeholder: "Paste text or a link to send to the PC", "aria-label": "Text to send to the PC", maxlength: "20000" });
            var grow = function () { box.style.height = "auto"; box.style.height = Math.min(160, box.scrollHeight) + "px"; };
            box.addEventListener("input", grow);
            var file = h("input", { type: "file", multiple: true, hidden: true });
            var sendBtn = null;
            var sendText = function () {
              var text = box.value.trim(); if (!text) { box.focus(); return; }
              call("/inbox/text", "POST", { text: text }).then(function () { done(sendBtn); box.value = ""; grow(); refill(); }).catch(fail);
            };
            sendBtn = button("arrowUp", "Send", sendText, "primary sm");
            var attach = button("file", "Choose files…", function () { file.click(); }, "ghost sm");
            file.onchange = function () { enqueue(Array.prototype.slice.call(file.files || [])); file.value = ""; };
            var compose = h("div", { class: "dy-compose" }, box, h("div", { class: "dy-actions" }, attach, h("span", { class: "grow" }), sendBtn, file));
            kids.push(lockedNote("editor", "Sending"), may("editor") ? compose : null, uploads);
            if (!data.to_pc.length && !uploads.childNodes.length) kids.push(emptyState("inbox", "Nothing sent yet", "Text, links and files you send show up here, and files land in " + data.folders.inbox + " on the PC. On a tablet or computer you can drag files onto this sheet."));
            else if (data.to_pc.length) kids.push(swipeHint());
            data.to_pc.forEach(function (item) { kids.push(inboxRow(item, body, refill)); });
          } else {
            kids.push(h("div", { class: "dy-path" }, ico("folder"), h("span", { text: data.folders.to_phone }), button("copy", "", function () { d.copy(data.folders.to_phone); }, "iconbtn")));
            if (!data.to_phone.length) kids.push(emptyState("inbox", "No files waiting", "Put a file in that folder on the PC and it appears here to save."));
            data.to_phone.forEach(function (fl) {
              var k = kindOf(fl.name);
              kids.push(row({ icon: KIND_ICON[k], kind: k, title: fl.name, sub: sizeText(fl.size) + " · " + agoText(fl.time) + (fl.risky ? " · program: be careful" : ""),
                trail: button("download", "Save", function () { saveFrom("/daily/tophone/file?name=" + encodeURIComponent(fl.name), fl.name); }, "sm") }));
            });
          }
          put(body, kids);
          void f;
        });
      }, "row");

      /* uploads: two at a time, each with its own bar; a finished one turns into a tick and then makes way for the list */
      function enqueue(files) {
        if (!may("editor")) { d.toast("Sending needs editor access."); return; }
        var limit = ((info() || {}).max_upload_mb || 100) * 1048576;
        files.forEach(function (f) {
          if (f.size > limit) { d.toast(f.name + " is larger than the " + Math.round(limit / 1048576) + " MB limit."); d.haptic("error"); return; }
          var r = uploadRow(f); uploads.append(r.node); queue.push({ file: f, ui: r });
        });
        pump();
      }
      function pump() {
        while (running < 2 && queue.length) start(queue.shift());
      }
      function start(job) {
        running += 1;
        var ui = job.ui, last = { at: Date.now(), bytes: 0, rate: 0 };
        ui.node.classList.remove("is-wait"); ui.node.classList.add("is-live");
        var req = upload(job.file, function (loaded, total) {
          var now = Date.now(), dt = (now - last.at) / 1000;
          if (dt > 0.25) { last.rate = last.rate ? last.rate * 0.6 + (loaded - last.bytes) / dt * 0.4 : (loaded - last.bytes) / dt; last.at = now; last.bytes = loaded; }
          var p = percent(loaded, total);
          ui.bar.style.transform = "scaleX(" + (p / 100) + ")";
          ui.bar.parentNode.setAttribute("aria-valuenow", String(p));
          ui.meta.textContent = sizeText(loaded) + " of " + sizeText(total) + " · " + p + "%" + (last.rate ? " · " + speedText(last.rate) + (loaded < total ? " · " + etaText((total - loaded) / last.rate) : "") : "");
        });
        ui.cancel.onclick = function () { req.abort(); };
        req.promise.then(function () {
          ui.node.classList.remove("is-live"); ui.node.classList.add("is-done"); ui.bar.style.transform = "scaleX(1)"; ui.meta.textContent = "Sent · " + sizeText(job.file.size);
          ui.cancel.replaceWith(h("span", { class: "dy-ok" }, ico("check"))); done(ui.node);
        }, function (e) {
          ui.node.classList.remove("is-live");
          if (e.cancelled) { ui.node.classList.add("is-gone"); setTimeout(function () { ui.node.remove(); }, 260); return; }
          ui.node.classList.add("is-bad"); ui.meta.textContent = errorText(e); d.haptic("error");
          var retry = button("retry", "", function () { ui.node.remove(); enqueue([job.file]); }, "iconbtn"); retry.setAttribute("aria-label", "Try " + job.file.name + " again");
          ui.cancel.replaceWith(retry);
        }).then(function () {
          running -= 1; pump();
          if (!running && !queue.length) setTimeout(function () {
            uploads.querySelectorAll(".is-done").forEach(function (n) { n.classList.add("is-gone"); });
            setTimeout(function () { uploads.querySelectorAll(".is-done").forEach(function (n) { n.remove(); }); sh.refill(); }, 320);
          }, 1100);
        });
      }

      /* drag files onto the sheet (a tablet or a computer) */
      var host = sh.sheet.body && sh.sheet.body.parentNode, over = 0;
      if (host) {
        var veil = h("div", { class: "dy-drop", "aria-hidden": "true" }, ico("download"), h("b", { text: "Drop to send to your PC" }));
        host.append(veil);
        var has = function (e) { return e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types || [], "Files") >= 0; };
        host.addEventListener("dragenter", function (e) { if (has(e)) { e.preventDefault(); over += 1; host.classList.add("is-drop"); } });
        host.addEventListener("dragover", function (e) { if (has(e)) e.preventDefault(); });
        host.addEventListener("dragleave", function () { over = Math.max(0, over - 1); if (!over) host.classList.remove("is-drop"); });
        host.addEventListener("drop", function (e) {
          if (!has(e)) return;
          e.preventDefault(); over = 0; host.classList.remove("is-drop");
          if (tab !== "pc") { tab = "pc"; sh.refill(); }
          enqueue(Array.prototype.slice.call(e.dataTransfer.files || []));
        });
      }
    }

    function inboxRow(item, body, refill) {
      var acts = [];
      if (item.kind === "file") {
        acts.push(button("download", "Save", function () { saveFrom("/daily/inbox/" + item.id + "/file", item.name); }, "dy-act"));
      } else {
        acts.push(button("copy", "Copy", function () { (item.more ? call("/inbox/" + item.id + "/text") : Promise.resolve({ text: item.text })).then(function (o) { return d.copy(o.text); }).catch(fail); }, "dy-act"));
        if (may("operator")) acts.push(button("clipboard", "To PC clipboard", function () { call("/inbox/" + item.id + "/clipboard", "POST").then(function () { d.toast("On the PC's clipboard"); }).catch(fail); }, "dy-act"));
        if (item.kind === "link") {
          if (/^https?:\/\//i.test(item.text)) acts.push(h("a", { class: "btn dy-act", href: item.text, target: "_blank", rel: "noopener noreferrer" }, ico("newtab"), h("span", { text: "Open here" })));
          if (may("operator")) acts.push(button("globe", "Open on PC", function () {
            confirm("Open this link on the PC?", item.text, "Open on the PC").then(function (yes) { if (yes) call("/inbox/" + item.id + "/open", "POST").then(function () { d.toast("Opening on the PC"); }).catch(fail); });
          }, "dy-act"));
        }
      }
      var k = item.kind === "file" ? kindOf(item.name) : item.kind;
      var title = item.kind === "file" ? item.name : item.text.split("\n")[0].slice(0, 120);
      var sub = (item.kind === "file" ? sizeText(item.size) + " · " : "") + agoText(item.time) + (item.from ? " · " + item.from : "");
      return row({ icon: KIND_ICON[k] || "file", kind: k, title: title, sub: sub,
        below: h("div", { class: "dy-below" }, item.kind === "text" && item.text.length > 120 ? h("p", { class: "dy-body", text: item.text }) : null, h("div", { class: "dy-acts" }, acts)),
        trail: item.risky ? h("span", { class: "hm-chip warn", text: "program" }) : null,
        swipe: may("editor") ? { onDelete: deleter("/inbox/" + item.id, whenEmpty(body, refill)) } : null });
    }

    /* ---- notes, with dictation ---- */

    function openNotes() {
      sheetOf("Notes", "daily-notes", function (body, refill) {
        return call("/notes").then(function (data) {
          var field = h("textarea", { class: "dy-text", rows: "3", placeholder: "A note or a to-do. Speak or type.", "aria-label": "Note", maxlength: "5000" });
          var isTodo = false, listening = false, baseText = "";
          var wave = h("span", { class: "dy-wave", "aria-hidden": "true" }, [0, 1, 2, 3, 4].map(function (i) { return h("i", { style: "animation-delay:" + (i * 0.12) + "s" }); }));
          var mic = h("button", { class: "dy-mic", type: "button", "aria-label": "Dictate" }, ico("mic"), wave);
          var stopMic = function () { listening = false; mic.classList.remove("on"); mic.setAttribute("aria-label", "Dictate"); };
          mic.onclick = function () {
            if (!d.speech || d.speech.status() !== "ok") { d.toast("Dictation needs a secure (https) connection and a browser that supports it. You can type instead."); return; }
            if (listening) { d.speech.stop().then(function (t) { field.value = t ? (baseText + " " + t).trim() : field.value; }); stopMic(); return; }
            baseText = field.value.trim(); listening = true; mic.classList.add("on"); mic.setAttribute("aria-label", "Stop dictating"); d.haptic("light");
            var res = d.speech.start(d.lang(), { text: function (t) { field.value = (baseText + " " + t).trim(); }, error: function () { stopMic(); d.toast("The microphone isn't available."); } });
            if (res !== "ok") { stopMic(); d.toast("Dictation isn't available here."); }
          };
          var todo = h("button", { class: "dy-chip", type: "button", "aria-pressed": "false", onclick: function () { isTodo = !isTodo; todo.setAttribute("aria-pressed", String(isTodo)); tick("tick"); } }, ico("check"), h("span", { text: "To-do" }));
          var save = button("check", "Save", function () {
            if (listening) { d.speech.abort(); stopMic(); }
            var text = field.value.trim(); if (!text) { field.focus(); return; }
            call("/notes", "POST", { text: text, todo: isTodo }).then(function () { done(save); d.toast(isTodo ? "To-do saved on the PC" : "Note saved on the PC"); refill(); }).catch(fail);
          }, "primary sm");
          var kids = [lockedNote("editor", "Saving notes")];
          if (may("editor")) kids.push(h("div", { class: "dy-compose" }, field, h("div", { class: "dy-actions" }, mic, todo, h("span", { class: "grow" }), save)));
          kids.push(h("div", { class: "dy-path" }, ico("folder"), h("span", { text: data.folder })));
          if (!data.notes.length) kids.push(emptyState("notes", "No notes yet", "Dictate a thought or type a to-do. It is saved on your PC as a plain text file (one a day) you can open anywhere."));
          groupNotes(data.notes).forEach(function (g) {
            kids.push(h("div", { class: "hm-sect" }, h("b", { text: dayText(g.date) })));
            g.items.forEach(function (n) { kids.push(h("div", { class: "dy-note" + (n.todo ? " is-todo" : "") }, h("span", { class: "dy-nm" }, n.todo ? h("i", { class: "dy-box" }) : ico("note")), h("div", { class: "grow" }, h("span", { class: "dy-body", text: n.text }), h("small", { text: n.time })))); });
          });
          put(body, kids);
        });
      }, "card");
    }

    /* ---- reminders and scheduled actions ---- */

    function openTasks() {
      var open = false;
      sheetOf("Reminders and timers", "daily-tasks", function (body, refill) {
        return call("/tasks").then(function (data) {
          var now = data.now, kids = [lockedNote("operator", "Scheduling")];
          if (may("operator")) {
            var form = taskForm(data, refill, function () { open = false; });
            var toggle = button(open ? "x" : "plus", open ? "Close" : "New reminder or timer", function () { open = !open; form.classList.toggle("is-open", open); toggle.replaceChildren(ico(open ? "x" : "plus"), h("span", { text: open ? "Close" : "New reminder or timer" })); tick("light"); }, "primary block");
            form.classList.toggle("is-open", open);
            kids.push(toggle, form);
          }
          if (!data.tasks.length) kids.push(emptyState("remind", "Nothing scheduled", "Set a reminder, or have the PC lock or sleep at a time you choose."));
          else kids.push(swipeHint());
          data.tasks.forEach(function (t) {
            kids.push(row({ icon: t.kind === "remind" ? "bell" : "power", kind: t.kind === "remind" ? "remind" : "power", title: taskText(t),
              sub: whenText(t.due, now) + (t.repeat ? " · " + repeatText(t.repeat) : "") + (t.last_result ? " · last: " + t.last_result : ""),
              trail: h("span", { class: "dy-when", text: inText(t.due, now) }),
              swipe: may("operator") ? { onDelete: deleter("/tasks/" + t.id, whenEmpty(body, refill)) } : null }));
          });
          put(body, kids);
        });
      }, "row");
    }

    function taskForm(data, refill, closed) {
      var what = "remind", repeatValue = "", chosen = null;
      var text = h("input", { class: "text-input", type: "text", maxlength: "120", placeholder: "Remind me to…", "aria-label": "Reminder text" });
      var when = h("input", { class: "text-input", type: "datetime-local", "aria-label": "Date and time", min: tsToInput(data.now) });
      var whatBox = h("div"), timeBox = h("div"), repeatBox = h("div");
      var paintWhat = function () {
        var list = [{ value: "remind", label: "Reminder", icon: "bell" }].concat(data.power.map(function (p) { return { value: p, label: powerShort(p), icon: p === "lock" ? "lock" : p === "sleep" ? "moon" : "power" }; }));
        whatBox.replaceChildren(chips(list, what, function (v) { what = v; text.hidden = v !== "remind"; paintWhat(); }, "is-scroll"));
      };
      var paintTimes = function () {
        timeBox.replaceChildren(chips(quickTimes(data.now).map(function (q) { return { value: q.ts, label: q.label }; }), chosen, function (v) { chosen = v; when.value = tsToInput(v); paintTimes(); }));
      };
      var paintRepeat = function () {
        repeatBox.replaceChildren(chips([{ value: "", label: "Once" }].concat(data.repeats.map(function (r) { return { value: r, label: repeatText(r) }; })), repeatValue, function (v) { repeatValue = v; paintRepeat(); }, "is-scroll"));
      };
      when.oninput = function () { chosen = inputToTs(when.value); paintTimes(); };
      paintWhat(); paintTimes(); paintRepeat();
      var add = button("plus", "Add", function () {
        var due = chosen || inputToTs(when.value);
        if (!due) { d.toast("Pick a time."); return; }
        var req = { kind: what === "remind" ? "remind" : "power", due: due, repeat: repeatValue, action: what === "remind" ? "" : what, text: text.value.trim() };
        if (req.kind === "remind" && !req.text) { text.focus(); d.toast("Say what to be reminded about."); return; }
        var go = function () { call("/tasks", "POST", req).then(function () { done(add); d.toast("Scheduled for " + whenText(due)); closed(); refill(); }).catch(fail); };
        if (req.kind === "power") confirm(powerText(req.action) + " " + whenText(due) + "?", "It will happen by itself, even if nobody is looking. If the PC is off then, it is skipped, not done late.", "Schedule it", "danger").then(function (yes) { if (yes) go(); });
        else go();
      }, "primary block");
      return h("div", { class: "dy-form" }, h("div", { class: "dy-form-in" }, h("small", { class: "dy-label", text: "What" }), whatBox, text, h("small", { class: "dy-label", text: "When" }), timeBox, when, h("small", { class: "dy-label", text: "Repeat" }), repeatBox, add));
    }

    /* ---- watchers: tell me when something finishes ---- */

    function openWatch() {
      var open = false;
      sheetOf("Tell me when…", "daily-watch", function (body, refill) {
        return call("/watch").then(function (data) {
          var kids = [lockedNote("operator", "Watching")];
          if (may("operator")) {
            var form = watchForm(data, refill, function () { open = false; });
            var toggle = button(open ? "x" : "plus", open ? "Close" : "Watch something", function () { open = !open; form.classList.toggle("is-open", open); toggle.replaceChildren(ico(open ? "x" : "plus"), h("span", { text: open ? "Close" : "Watch something" })); tick("light"); }, "primary block");
            form.classList.toggle("is-open", open);
            kids.push(toggle, form);
          }
          if (!data.watchers.length) kids.push(emptyState("watch", "Nothing is being watched", "Pick a program, a download or the PC going quiet, and I'll tell you the moment it's done."));
          else kids.push(swipeHint());
          data.watchers.forEach(function (w) {
            kids.push(row({ tileNode: h("span", { class: "dy-ric k-watch" }, ico("eye"), h("i", { class: "dy-pulse" })), title: w.label, sub: (w.then ? "Then " + powerText(w.then).toLowerCase() + " · " : "") + "watching since " + agoText(w.since),
              swipe: may("operator") ? { onDelete: deleter("/watch/" + w.id, whenEmpty(body, refill)) } : null }));
          });
          put(body, kids);
        });
      }, "row");
    }

    function watchForm(data, refill, closed) {
      var type = "process", target = "", thenValue = "";
      var kindBox = h("div"), picker = h("div", { class: "dy-pick" }), thenBox = h("div");
      var paintKind = function () {
        kindBox.replaceChildren(chips([{ value: "process", label: "A program", icon: "cpu" }, { value: "folder", label: "A download", icon: "download" }, { value: "quiet", label: "PC goes quiet", icon: "gauge" }], type, function (v) { type = v; target = ""; paintKind(); fillPicker(); }, "is-scroll"));
      };
      var paintThen = function () {
        thenBox.replaceChildren(chips([{ value: "", label: "Just tell me" }].concat(data.power.map(function (p) { return { value: p, label: powerShort(p) }; })), thenValue, function (v) { thenValue = v; paintThen(); }, "is-scroll"));
      };
      var fillPicker = function () {
        if (type === "process") {
          picker.replaceChildren(Ux() ? Ux().skeleton("row", 1) : h("p", { class: "note", text: "Loading programs…" }));
          call("/processes").then(function (out) {
            picker.replaceChildren(h("small", { class: "dy-label", text: "Tell me when this program stops running" }), chips(out.processes.slice(0, 14).map(function (p) { return { value: p.name, label: p.name + (p.count > 1 ? " ×" + p.count : "") }; }), target, function (v) { target = v; fillPicker(); }, "is-wrap"));
          }).catch(function (e) { picker.replaceChildren(h("p", { class: "err", text: errorText(e) })); });
        } else if (type === "folder") {
          picker.replaceChildren(h("small", { class: "dy-label", text: "Tell me when a new file finishes arriving in" }), chips(data.places.map(function (p) { return { value: p.path, label: p.name, icon: "folder" }; }), target, function (v) { target = v; fillPicker(); }, "is-wrap"));
        } else {
          var range = h("input", { type: "range", min: "5", max: "40", step: "5", value: target || "15", "aria-label": "How quiet" });
          var say = h("b", { text: "under " + (target || 15) + "% busy" });
          target = target || "15";
          range.oninput = function () { target = range.value; say.textContent = "under " + range.value + "% busy"; };
          picker.replaceChildren(h("small", { class: "dy-label", text: "Tell me when the processor stays quiet for half a minute: a render, an install or an update has finished" }), h("div", { class: "dy-range" }, range, say));
        }
      };
      var add = button("eye", "Watch", function () {
        if (!target) { d.toast("Pick one first."); return; }
        var send = function () { call("/watch", "POST", { kind: type, target: target, then: thenValue }).then(function () { done(add); d.toast("I'll tell you"); closed(); refill(); }).catch(fail); };
        if (thenValue) confirm(powerText(thenValue) + " when it's done?", "It will happen by itself when the watch finishes, even if nobody is looking.", "Yes, do it then", "danger").then(function (yes) { if (yes) send(); });
        else send();
      }, "primary block");
      paintKind(); paintThen(); fillPicker();
      return h("div", { class: "dy-form" }, h("div", { class: "dy-form-in" }, h("small", { class: "dy-label", text: "Watch" }), kindBox, picker, h("small", { class: "dy-label", text: "Then" }), thenBox, add));
    }

    /* ---- the PC's own notifications ---- */

    function openPcNotices() {
      sheetOf("PC notifications", "daily-pcnotes", function (body, refill) {
        return call("/notifications").then(function (data) {
          if (!data.enabled) { body.replaceChildren(emptyState("brief", "Switched off", "To show this PC's notifications here, set daily: notifications: true in config.yaml on the PC. They can hold private messages, so it is off until you choose.")); return; }
          var kids = [];
          if (data.problem) kids.push(h("p", { class: "err", text: data.problem }));
          kids.push(h("p", { class: "dy-fine", text: "Read from Windows' own list. Hiding one only hides it here; nothing is dismissed on the PC." }));
          if (!data.items.length) kids.push(emptyState("brief", "All quiet", "No recent notifications on your PC."));
          else if (may("editor")) kids.push(button("x", "Hide all", function () { call("/notifications/dismiss", "POST", {}).then(refill).catch(fail); }, "ghost sm"));
          data.items.forEach(function (n) {
            kids.push(row({ icon: "bell", kind: "remind", title: n.title || n.app, sub: n.app + " · " + agoText(n.time), below: n.body ? h("p", { class: "dy-body", text: n.body }) : null,
              swipe: may("editor") ? { label: "Hide", onDelete: function () { return call("/notifications/dismiss", "POST", { id: n.id }).then(function () { tick("medium"); whenEmpty(body, refill)(); }, function (e) { fail(e); throw e; }); } } : null }));
          });
          put(body, kids);
        });
      }, "row");
    }

    /* ---- recent files and pinned places ---- */

    function openPath(entry) {
      d.closeSheet();
      setTimeout(function () { if (entry.is_dir) d.openFolder(entry.path); else d.openFile(entry.path); }, 260);
    }

    function openRecent() {
      var tab = "pinned";
      sheetOf("Recent and pinned", "daily-recent", function (body, refill) {
        return Promise.all([call("/favourites"), call("/recent")]).then(function (res) {
          var fav = res[0], rec = res[1].files;
          var pinned = {}; fav.favourites.forEach(function (f) { pinned[f.path] = true; });
          var seg = h("div", { class: "seg", role: "group", "aria-label": "List" }, [["pinned", "Pinned", fav.favourites.length], ["recent", "Recent", rec.length]].map(function (t) {
            return h("button", { type: "button", "aria-pressed": String(tab === t[0]), onclick: function () { tab = t[0]; refill(); } }, t[1], t[2] ? h("i", { class: "dy-count", text: String(t[2]) }) : null);
          }));
          var kids = [seg];
          if (tab === "pinned") {
            var left = fav.suggestions.filter(function (s) { return !pinned[s.path]; });
            if (left.length && may("editor")) kids.push(h("div", { class: "dy-shelf", role: "group", "aria-label": "Pin a usual folder" }, left.map(function (s) {
              return h("button", { class: "dy-pin", type: "button", onclick: function (e) { var el = e.currentTarget; call("/favourites", "POST", { path: s.path, label: s.name }).then(function () { done(el); refill(); }).catch(fail); } }, tile("folder", "folder"), h("b", { text: s.name }), h("small", { text: "Pin" }));
            })));
            if (!fav.favourites.length) kids.push(emptyState("recent", "Pin your places", "Pin the folders and files you open all the time and they will be one tap away."));
            else kids.push(swipeHint());
            fav.favourites.forEach(function (f) {
              var isDir = !/\.[A-Za-z0-9]{1,6}$/.test(f.path), k = kindOf(f.path, isDir);
              kids.push(row({ icon: KIND_ICON[k], kind: k, title: f.label, sub: f.path, onTap: function () { openPath({ path: f.path, is_dir: isDir }); },
                swipe: may("editor") ? { label: "Unpin", onDelete: deleter("/favourites?path=" + encodeURIComponent(f.path), whenEmpty(body, refill)) } : null }));
            });
          } else {
            if (!rec.length) kids.push(emptyState("recent", "Nothing recent", "Windows has no recent files to show right now."));
            rec.forEach(function (f) {
              var k = kindOf(f.name, f.is_dir);
              var star = null;
              if (may("editor") && !pinned[f.path]) {
                star = h("span", { class: "iconbtn dy-star", role: "button", tabindex: "0", "aria-label": "Pin " + f.name }, ico("star"));
                var pin = function (e) { e.stopPropagation(); e.preventDefault(); call("/favourites", "POST", { path: f.path, label: "" }).then(function () { done(star); d.toast("Pinned"); refill(); }).catch(fail); };
                star.addEventListener("click", pin);
                star.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") pin(e); });
              }
              kids.push(row({ icon: KIND_ICON[k], kind: k, title: f.name, sub: f.folder + " · " + agoText(f.time), onTap: function () { openPath(f); }, trail: star }));
            });
          }
          put(body, kids);
        });
      }, "row");
    }

    /* ---- printing ---- */

    function printFile(path, name) {
      return call("/print/info").then(function (p) {
        if (!p.enabled) { d.toast("Printing from the phone is switched off on this PC."); return null; }
        if (!p.printer) { d.toast("This PC has no default printer."); return null; }
        return confirm("Print " + name + "?", "It will print on " + p.printer + (p.offline ? " (which looks offline)" : "") + " using the program Windows uses for this kind of file. It can't be unprinted from here.", "Print").then(function (yes) {
          if (!yes) return null;
          return call("/print", "POST", { path: path }).then(function (out) { done(); d.toast("Sent to " + out.printer); });
        });
      }).catch(function (e) {
        if (e && e.data && e.data.code === "needs_access") { d.askAccess(e.data.folder, false, function () { printFile(path, name); }); return; }
        fail(e);
      });
    }

    function openPrint() {
      sheetOf("Print", "daily-print", function (body) {
        return Promise.all([call("/print/info"), call("/recent")]).then(function (res) {
          var p = res[0], files = res[1].files.filter(function (f) { return !f.is_dir && printable(f.name); });
          if (!p.enabled) { body.replaceChildren(emptyState("print", "Printing is off", "Printing from the phone is switched off on this PC (daily: printing in config.yaml).")); return; }
          var status = h("div", { class: "dy-printer" + (p.printer && !p.offline ? " is-ok" : " is-bad") }, h("span", { class: "dy-ric k-print" }, ico("printer")),
            h("div", { class: "dy-rtx" }, h("b", { text: p.printer || "No default printer" }), h("small", { text: p.printer ? (p.offline ? "Offline. Check it is on and connected." : "Ready. You confirm each print.") : "Set one in Windows first." })), h("i", { class: "dy-dot" }));
          var kids = [status, lockedNote("operator", "Printing")];
          if (!files.length) kids.push(emptyState("print", "Nothing to print yet", "Open a document in Files, or pin it, and it will show up here."));
          else kids.push(h("div", { class: "hm-sect" }, h("b", { text: "Recent documents" })));
          files.forEach(function (f) {
            var k = kindOf(f.name);
            kids.push(row({ icon: KIND_ICON[k], kind: k, title: f.name, sub: f.folder, trail: may("operator") ? button("printer", "Print", function () { printFile(f.path, f.name); }, "sm") : null }));
          });
          put(body, kids);
        });
      }, "row");
    }

    /* ---- snapshots ---- */

    function showPicture(blob, name, title) {
      var url = URL.createObjectURL(blob);
      var img = h("img", { class: "dy-pic", src: url, alt: title });
      var file = null;
      try { file = typeof File !== "undefined" ? new File([blob], name, { type: blob.type || "image/jpeg" }) : null; } catch (e) { file = null; }
      var canShare = !!(file && typeof navigator !== "undefined" && navigator.canShare && navigator.canShare({ files: [file] }));
      var sheet = d.openSheet({
        title: title, route: "daily-pic", onClose: function () { URL.revokeObjectURL(url); },
        body: h("div", { class: "dy" }, img, h("div", { class: "dy-actions" }, button("download", "Save to this phone", function () { d.saveBlob(blob, name); }, "primary"),
          canShare ? button("share", "Share", function () { navigator.share({ files: [file], title: title }).catch(function () { /* cancelled */ }); }, "ghost") : null, button("x", "Close", function () { sheet.close(); }, "ghost"))),
      });
    }

    function snapScreen(status, btn) {
      status.textContent = "Taking a screenshot…"; btn.disabled = true;
      call("/snap/screen", "POST", undefined, true).then(function (res) { return res.blob().then(function (blob) { return { blob: blob, name: res.headers.get("X-Saved-As") || "screenshot.jpg" }; }); })
        .then(function (got) { status.textContent = ""; btn.disabled = false; done(); showPicture(got.blob, got.name, "Screenshot"); })
        .catch(function (e) {
          status.textContent = ""; btn.disabled = false;
          if (e && e.data && e.data.code === "screen_not_allowed" && d.openScreen) { d.toast("Ask to look at the screen first. The owner says yes on the PC."); d.openScreen(); return; }
          fail(e);
        });
    }

    function snapWebcam(status, btn) {
      confirm("Take a photo with this PC's webcam?", "Everyone paired is told it was taken, and it is written to the activity log. Anyone in front of the PC may see the camera light.", "Take photo", "danger").then(function (yes) {
        if (!yes) return;
        status.textContent = "Taking a photo…"; btn.disabled = true;
        call("/snap/webcam", "POST", undefined, true).then(function (res) { return res.blob().then(function (blob) { return { blob: blob, name: res.headers.get("X-Saved-As") || "webcam.jpg" }; }); })
          .then(function (got) { status.textContent = ""; btn.disabled = false; done(); showPicture(got.blob, got.name, "Webcam photo"); })
          .catch(function (e) { status.textContent = ""; btn.disabled = false; fail(e); });
      });
    }

    function openSnap() {
      var f = info() || {};
      var status = h("p", { class: "dy-fine", role: "status" });
      var shot = button("camera", "Screenshot of the PC", null, "primary block");
      shot.onclick = function () { snapScreen(status, shot); };
      var cam;
      if (!f.webcam) cam = h("p", { class: "dy-fine", text: "The webcam is off. To allow the Admin phone to use it, set daily: webcam: true in config.yaml on the PC (and install opencv-python)." });
      else if (!may("admin")) cam = h("p", { class: "dy-fine", text: "The webcam is only for the Admin phone." });
      else { cam = button("camera", "Photo from the PC's webcam", null, "block"); cam.onclick = function () { snapWebcam(status, cam); }; }
      var body = h("div", { class: "dy" },
        h("div", { class: "dy-snap" }, Ux() ? Ux().art("snap") : null, h("b", { text: "The PC's screen" }), h("p", { text: "One full-size picture. It needs the owner's yes on the PC, like looking at the screen does. A copy is kept in the Screenshots folder on the PC." }),
          lockedNote("operator", "Screenshots"), may("operator") ? shot : null),
        h("div", { class: "dy-snap" }, h("b", { text: "The PC's webcam" }), cam),
        status);
      d.openSheet({ title: "Snap", body: body, route: "daily-snap" });
    }

    /* ---- the briefing ---- */

    var SVG_NS = "http://www.w3.org/2000/svg";
    /* A ring that fills to `pct`. Drawn with SVG shapes made here, never from markup. */
    function ring(pct, tone, value, label) {
      var C = 2 * Math.PI * 32;
      var svg = document.createElementNS(SVG_NS, "svg"); svg.setAttribute("viewBox", "0 0 80 80"); svg.setAttribute("aria-hidden", "true");
      var track = document.createElementNS(SVG_NS, "circle"), arc = document.createElementNS(SVG_NS, "circle");
      [track, arc].forEach(function (c) { c.setAttribute("cx", "40"); c.setAttribute("cy", "40"); c.setAttribute("r", "32"); c.setAttribute("fill", "none"); c.setAttribute("stroke-width", "8"); });
      track.setAttribute("class", "dy-rtrack"); arc.setAttribute("class", "dy-rarc"); arc.setAttribute("stroke-linecap", "round");
      arc.setAttribute("stroke-dasharray", String(C)); arc.setAttribute("stroke-dashoffset", String(C)); arc.setAttribute("transform", "rotate(-90 40 40)");
      svg.append(track, arc);
      var p = Math.max(0, Math.min(100, pct || 0));
      setTimeout(function () { arc.setAttribute("stroke-dashoffset", String(C * (1 - p / 100))); }, 60);
      return h("div", { class: "dy-stat tone-" + tone, role: "group", "aria-label": label + " " + value }, h("div", { class: "dy-ring" }, svg, h("b", { text: value })), h("small", { text: label }));
    }
    var toneOf = function (p, warn, bad) { return p >= bad ? "bad" : p >= warn ? "warn" : "ok"; };

    function openBriefing() {
      sheetOf("Briefing", "daily-brief", function (body) {
        return call("/briefing").then(function (b) {
          var s = b.status || {}, stats = [];
          if (s.available) {
            if (s.battery) stats.push(ring(s.battery.percent, s.battery.percent <= 15 && !s.battery.plugged ? "bad" : s.battery.percent <= 30 && !s.battery.plugged ? "warn" : "ok", s.battery.percent + "%", s.battery.plugged ? "Charging" : "Battery"));
            if (s.memory) stats.push(ring(s.memory.percent, toneOf(s.memory.percent, 80, 92), s.memory.percent + "%", "Memory"));
            if (s.disk) stats.push(ring(s.disk.percent, toneOf(s.disk.percent, 85, 95), sizeText(s.disk.free), "Disk free"));
            if (typeof s.cpu_percent === "number") stats.push(ring(s.cpu_percent, toneOf(s.cpu_percent, 70, 90), s.cpu_percent + "%", "Processor"));
          }
          var kids = [];
          if (stats.length) kids.push(h("div", { class: "dy-stats" }, stats));
          if (s.available && s.uptime_s) kids.push(h("div", { class: "dy-uptime" }, ico("clock"), h("span", { text: "Up for " + uptimeText(s.uptime_s) })));
          kids.push(h("div", { class: "dy-lines" }, b.lines.map(function (l) { return h("p", {}, ico("sparkle"), h("span", { text: l })); })));
          if (b.updates && b.updates.checking) kids.push(h("p", { class: "dy-fine", text: "Checking Windows for updates… open this again in a minute." }));
          if (b.next && b.next.length) {
            kids.push(h("div", { class: "hm-sect" }, h("b", { text: "Next up" })));
            b.next.forEach(function (t) { kids.push(row({ icon: t.kind === "remind" ? "bell" : "power", kind: t.kind === "remind" ? "remind" : "power", title: taskText(t), sub: whenText(t.due), trail: h("span", { class: "dy-when", text: inText(t.due) }) })); });
          }
          if (b.top_programs && b.top_programs.length) {
            kids.push(h("div", { class: "hm-sect" }, h("b", { text: "Using the most memory" })));
            var top = Math.max.apply(null, b.top_programs.map(function (p) { return p.memory_mb; })) || 1;
            b.top_programs.forEach(function (p) {
              kids.push(row({ icon: "cpu", kind: "code", title: p.name, sub: p.memory_mb + " MB" + (p.count > 1 ? " · " + p.count + " running" : ""),
                below: h("div", { class: "dy-meter" }, h("i", { style: "width:" + Math.max(4, Math.round(p.memory_mb / top * 100)) + "%" })) }));
            });
          }
          put(body, kids);
        });
      }, "stat");
    }

    return { sync: sync, homeCard: homeCard, openInbox: openInbox, openNotes: openNotes, openTasks: openTasks, openWatch: openWatch, openRecent: openRecent,
             openPrint: openPrint, openSnap: openSnap, openBriefing: openBriefing, openPcNotices: openPcNotices, printFile: printFile, enabled: function () { return !!info(); } };
  }

  var api = { create: create, sizeText: sizeText, whenText: whenText, agoText: agoText, inText: inText, repeatText: repeatText, powerText: powerText, powerShort: powerShort,
              quickTimes: quickTimes, inputToTs: inputToTs, tsToInput: tsToInput, taskText: taskText, watchText: watchText, printable: printable, kindOf: kindOf,
              groupNotes: groupNotes, dayText: dayText, errorText: errorText, speedText: speedText, etaText: etaText, percent: percent, uptimeText: uptimeText };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.AfpDaily = api;
})(typeof window !== "undefined" ? window : this);
