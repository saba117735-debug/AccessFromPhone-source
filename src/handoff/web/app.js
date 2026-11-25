"use strict";
/*
 * AccessFromPhone: the phone page.
 *
 * Reading order: helpers, settings, data model, chat rendering, voice, sheets (settings, activity, help),
 * then start-up. Everything that comes from files or the AI is put on the page with textContent, never as HTML.
 */
(() => {
  const $ = (sel, from = document) => from.querySelector(sel);
  const root = document.documentElement;

  /* ===================================================================== helpers */

  function h(tag, props, ...kids) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v === undefined || v === null || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k === "vars") for (const [a, b] of Object.entries(v)) node.style.setProperty(a, b);
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat(Infinity)) {
      if (kid === null || kid === undefined || kid === false) continue;
      node.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    }
    return node;
  }

  const ICONS = {
    pause: '<path d="M8 5v14M16 5v14"/>',
    play: '<path d="M7 4.5v15l12-7.5z"/>',
    home: '<path d="M3 11l9-8 9 8"/><path d="M5 10v10h5v-6h4v6h5V10"/>',
    chat: '<path d="M21 12a8 8 0 0 1-11.5 7.2L4 20l1-4.5A8 8 0 1 1 21 12z"/>',
    unlock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 7.5-2"/>',
    moon: '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
    image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="m21 16-5-5-9 9"/>',
    wifiOff: '<path d="M2.5 9a15 15 0 0 1 4.4-2.8M21.5 9a15 15 0 0 0-8.2-3.9M5.5 12.5a10.5 10.5 0 0 1 3.4-2.1M18.5 12.5a10.5 10.5 0 0 0-3.2-2.1M8.6 16a6 6 0 0 1 6.8 0"/><circle cx="12" cy="19.5" r="1"/><path d="M3 3l18 18"/>',
    power: '<path d="M12 3v8"/><path d="M6.4 6.6a8 8 0 1 0 11.2 0"/>',
    bell: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/>',
    menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
    panel: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    share: '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="M8.6 10.5l6.8-4M8.6 13.5l6.8 4"/>',
    users: '<circle cx="9" cy="8" r="3.2"/><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6"/><circle cx="17" cy="9" r="2.4"/><path d="M17 14.5c2.2 0 4 1.8 4 4"/>',
    zoomin: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5M8 11h6M11 8v6"/>',
    zoomout: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5M8 11h6"/>',
    sliders: '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
    mic: '<rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
    keyboard: '<rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/>',
    arrowUp: '<path d="M12 19V5M5 12l7-7 7 7"/>',
    arrowDown: '<path d="M12 5v14M5 12l7 7 7-7"/>',
    arrowLeft: '<path d="M19 12H5M12 5l-7 7 7 7"/>',
    x: '<path d="M6 6l12 12M18 6 6 18"/>',
    check: '<path d="m5 12 5 5 9-10"/>',
    copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>',
    download: '<path d="M12 4v11M7 11l5 5 5-5M5 20h14"/>',
    undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>',
    retry: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
    trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    pencil: '<path d="M4 20h4L19 9l-4-4L4 16z"/>',
    more: '<circle cx="5" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="19" cy="12" r="1.3"/>',
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    volume: '<path d="M4 9v6h4l5 4V5L8 9z"/><path d="M16 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11"/>',
    volumeOff: '<path d="M4 9v6h4l5 4V5L8 9z"/><path d="m17 9 5 6M22 9l-5 6"/>',
    volumeLow: '<path d="M4 9v6h4l5 4V5L8 9z"/><path d="M16 10a3 3 0 0 1 0 4"/>',
    shield: '<path d="M12 3 5 6v6c0 4.5 3 7.5 7 9 4-1.5 7-4.5 7-9V6z"/><path d="m9 12 2 2 4-4"/>',
    folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    terminal: '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m6 9 3 3-3 3M12 15h5"/>',
    file: '<path d="M6 3h8l5 5v13H6z"/><path d="M14 3v5h5"/>',
    activity: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    logout: '<path d="M9 4H5v16h4M16 8l4 4-4 4M20 12H9"/>',
    chevron: '<path d="m9 6 6 6-6 6"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.01"/>',
    alert: '<path d="M12 3 2 20h20z"/><path d="M12 10v5M12 17.5v.01"/>',
    stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    vibrate: '<rect x="8" y="3" width="8" height="18" rx="2"/><path d="M4 8v8M20 8v8M1.5 10.5v3M22.5 10.5v3"/>',
    palette: '<path d="M12 3a9 9 0 1 0 0 18c1.5 0 2-1 1.5-2-.6-1.2.3-2.5 1.7-2.5H17a4 4 0 0 0 4-4C21 6.5 17 3 12 3z"/><circle cx="7.5" cy="11" r="1"/><circle cx="10" cy="7" r="1"/><circle cx="15" cy="7.5" r="1"/>',
    type: '<path d="M5 6V4h14v2M12 4v16M9 20h6"/>',
    cpu: '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>',
    cloud: '<path d="M7 18a5 5 0 1 1 1-9.9A6 6 0 0 1 19 11a3.5 3.5 0 0 1-1 7z"/>',
    move: '<path d="M5 12h14M13 6l6 6-6 6"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    monitor: '<rect x="2" y="4" width="20" height="13" rx="2"/><path d="M8 21h8M12 17v4"/>',
    pointer: '<path d="M5 3l14 7-6 2-2 6z"/><path d="M13 12l5 6"/>',
    pad: '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M3 14h18M12 14v6"/>',
    battery: '<rect x="2" y="7" width="18" height="10" rx="2.5"/><path d="M22 10.5v3"/><rect x="4.5" y="9.5" width="8" height="5" rx="1" fill="currentColor" stroke="none"/>',
    bolt: '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
    plug: '<path d="M9 3v5M15 3v5M6 8h12v3a6 6 0 0 1-12 0zM12 17v4"/>',
    gauge: '<path d="M4 16a8 8 0 1 1 16 0"/><path d="M12 16l4-5"/><circle cx="12" cy="16" r="1.2"/>',
    wifi: '<path d="M2.5 9a15 15 0 0 1 19 0M5.5 12.5a10.5 10.5 0 0 1 13 0M8.6 16a6 6 0 0 1 6.8 0"/><circle cx="12" cy="19.5" r="1"/>',
    phone: '<rect x="7" y="2" width="10" height="20" rx="2"/><path d="M11 18h2"/>',
    eye: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/>',
    table: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M9 4v16"/>',
    braces: '<path d="M8 4c-2 0-3 1-3 3v2c0 1.5-1 3-3 3 2 0 3 1.5 3 3v2c0 2 1 3 3 3M16 4c2 0 3 1 3 3v2c0 1.5 1 3 3 3-2 0-3 1.5-3 3v2c0 2-1 3-3 3"/>',
    newtab: '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    expand: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
    shrink: '<path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5"/>',
    sparkle: '<path d="M12 3l1.8 4.6L18 9.4l-4.2 1.8L12 16l-1.8-4.8L6 9.4l4.2-1.8z"/><path d="M19 15l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z"/>',
    wrap: '<path d="M4 6h16M4 12h12a3 3 0 0 1 0 6H9"/><path d="m11 15-3 3 3 3"/>',
    textMinus: '<path d="M3 18 8 6l5 12M4.8 14h6.4M16 11h5"/>',
    textPlus: '<path d="M3 18 8 6l5 12M4.8 14h6.4M18.5 8.5v5M16 11h5"/>',
    scan: '<path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3M4 12h16"/>',
    inbox: '<path d="M3 13l3-8h12l3 8v6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/><path d="M3 13h5l1 2h6l1-2h5"/>',
    note: '<path d="M5 3h14v18H5z"/><path d="M8 8h8M8 12h8M8 16h5"/>',
    camera: '<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>',
    printer: '<path d="M7 9V3h10v6"/><rect x="4" y="9" width="16" height="8" rx="2"/><path d="M7 14h10v7H7z"/>',
    star: '<path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8"/>',
    sunDim: '<circle cx="12" cy="12" r="3"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6 7 7M17 17l1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4"/>',
    next: '<path d="M5 5l10 7-10 7z"/><path d="M19 5v14"/>',
    prev: '<path d="M19 5 9 12l10 7z"/><path d="M5 5v14"/>',
    clipboard: '<rect x="6" y="4" width="12" height="17" rx="2"/><path d="M9 4h6v3H9zM9 12h6M9 16h4"/>',
  };
  function ico(name, size) {
    const s = h("span", { class: "ico", "aria-hidden": "true" });
    s.innerHTML = `<svg viewBox="0 0 24 24" focusable="false">${ICONS[name] || ""}</svg>`; // fixed strings above, never user text
    if (size) { s.style.width = s.style.height = size + "px"; }
    return s;
  }

  const pad = (n) => String(n).padStart(2, "0");
  const clip = (text, n) => (text.length > n ? text.slice(0, n - 1).trimEnd() + "…" : text);
  const fmtTime = (iso) => { const d = new Date(iso); return isNaN(d) ? "" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); };
  const fmtClock = (iso) => { const d = new Date(iso); return isNaN(d) ? "" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }); };
  const sameDay = (a, b) => a.toDateString() === b.toDateString();
  function ago(iso) {
    const d = new Date(iso), now = new Date(), mins = Math.round((now - d) / 60000);
    if (isNaN(d)) return "";
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins} min ago`;
    if (sameDay(d, now)) return fmtTime(iso);
    const y = new Date(now); y.setDate(now.getDate() - 1);
    if (sameDay(d, y)) return "Yesterday";
    return d.toLocaleDateString([], { day: "numeric", month: "short" });
  }
  function groupOf(iso) {
    const d = new Date(iso), now = new Date(), day = 86400000;
    if (sameDay(d, now)) return "Today";
    const y = new Date(now); y.setDate(now.getDate() - 1);
    if (sameDay(d, y)) return "Yesterday";
    if (now - d < 7 * day) return "Previous 7 days";
    return "Earlier";
  }
  const newId = () => "c" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);

  /* ===================================================================== storage and settings */

  const store = {
    get(key, fallback) { try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; } },
    set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode: carry on */ } },
    raw(key) { try { return localStorage.getItem(key) || ""; } catch { return ""; } },
    setRaw(key, value) { try { localStorage.setItem(key, value); } catch { /* ignore */ } },
    del(key) { try { localStorage.removeItem(key); } catch { /* ignore */ } },
  };

  const DEFAULTS = {
    name: "", theme: "dark", accent: "cyan", text: "m", reduceMotion: false,
    motion: "",   // "full" (3D), "simple" (same look, flat and gentle) or "off". "" = chosen for this phone automatically (see motionLevel)
    haptics: true, ticks: false, sounds: true,
    input: "auto", hold: "hold", lang: "en-IN", autoSend: true, speak: "voice", rate: 1,
    openTrails: false, editorWrap: false, editorSize: 13,
    saveChats: true,  // keep the recent chats on this phone, to read when the PC can't be reached
  };
  const cfg = { ...DEFAULTS, ...store.get("afp_settings", {}) };
  const saveCfg = () => store.set("afp_settings", cfg);

  // The copy of recent chats kept on this phone (offline.js). If that file did not load, the page simply has no offline reading.
  const Saved = window.AfpOffline ? window.AfpOffline.create() : { init: () => Promise.resolve(null), has: () => false, current: () => null, load: () => Promise.resolve(null), queue() {}, clear: () => Promise.resolve(), save: () => Promise.resolve(null) };
  const savedReady = Saved.init();

  // Requests typed while the PC can't be reached wait here, on this phone (queue.js). If that file did not load, there is simply no queue.
  const Q = window.AfpQueue ? window.AfpQueue.create() : null;

  // Who is using the PC, and the "may I have a turn?" question (access.js). It is given the page's helpers, and keeps its own state.
  const Access = window.AfpAccess ? window.AfpAccess.create({
    h, ico, ask, toast, haptic, openSheet, ago,
    api: (path, opts) => api(path, opts),
    sect: (text) => sect(text),
    bar: () => $("#sessionBar"), tab: () => $('#tabbar .tab[data-tab="access"]'), layer: () => $("#layer"),
    openScreen: () => { if (Screen) Screen.open(); },
  }) : null;

  // Looking at the PC's screen, view only, with the owner's yes (screen.js).
  const Screen = window.AfpScreen ? window.AfpScreen.create({ h, ico, toast, haptic, openSheet, token: () => S.token, api: (path, opts) => api(path, opts), allowFolder: async (folder) => { const out = await (await api("/access/grant", { method: "POST", body: JSON.stringify({ path: folder, change: false }) })).json(); if (S.info) { S.info.folders.granted_details = out.granted; S.info.folders.granted = out.granted.map((g) => g.path); } }, download: (path, size) => DL.startAndWatch(path, size) }) : null;

  const ACCENTS = [["cyan", "#22d3ee", "#3b82f6"], ["violet", "#a78bfa", "#6366f1"], ["green", "#34d399", "#0ea5a4"], ["amber", "#fbbf24", "#f97316"], ["rose", "#fb7185", "#e11d48"]];
  const darkQuery = window.matchMedia ? matchMedia("(prefers-color-scheme: dark)") : null;
  /* Graphics and motion. "full" = 3D depth and tilt; "simple" = the same look, flat, with gentle movement; "off" = nothing moves.
     Unless the person chose, a phone set to "remove animations" starts on Off, one that says it is low-end (little memory or few cores) on Simple, and the rest on Full. */
  const autoMotion = () => {
    if (window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches) return "off";
    const lowMemory = navigator.deviceMemory && navigator.deviceMemory <= 2, lowCpu = navigator.hardwareConcurrency && navigator.hardwareConcurrency <= 2;
    return lowMemory || lowCpu ? "simple" : "full";
  };
  const motionLevel = () => ["full", "simple", "off"].includes(cfg.motion) ? cfg.motion : cfg.reduceMotion ? "off" : autoMotion();
  function applyLook() {
    const dark = cfg.theme === "dark" || (cfg.theme === "system" && darkQuery && darkQuery.matches);
    root.dataset.theme = dark ? "dark" : "light";
    root.dataset.accent = cfg.accent;
    root.dataset.text = cfg.text;
    const level = motionLevel();
    if (level === "full") delete root.dataset.motion; else root.dataset.motion = level;
    const meta = $("#themeColor");
    if (meta) meta.setAttribute("content", dark ? "#0b111b" : "#f1f6fd");
  }
  if (darkQuery && darkQuery.addEventListener) darkQuery.addEventListener("change", () => { if (cfg.theme === "system") applyLook(); });

  /* ===================================================================== haptics */

  const PATTERNS = { tick: 6, light: 10, medium: 20, heavy: 35, success: [10, 50, 10], warning: [20, 40, 20], error: [30, 50, 30, 50, 30], alert: [40, 70, 40, 70, 110] };
  const canVibrate = typeof navigator.vibrate === "function";
  function haptic(kind = "light") {
    if (!cfg.haptics) return;
    // The phone app vibrates natively: that works even before the first tap (a browser ignores vibration until then) and is the same on every phone.
    if (window.AfpApp && window.AfpApp.haptic) { try { window.AfpApp.haptic(kind); } catch { /* the app is closing */ } return; }
    if (!canVibrate) return;
    if (navigator.userActivation && !navigator.userActivation.hasBeenActive) return; // the browser ignores it until the first tap
    try { navigator.vibrate(PATTERNS[kind] || 10); } catch { /* not allowed yet: needs a tap first */ }
  }

  /* ===================================================================== sounds: made on the phone, no files needed */

  let audio = null;
  function audioCtx() {
    try {
      const AC = window.AudioContext || window.webkitAudioContext; if (!AC) return null;
      if (!audio) audio = new AC();
      if (audio.state === "suspended") audio.resume();
      return audio;
    } catch { return null; }
  }
  /** "lock": a camera shutter (the screen was captured). "unlock": a rising start-up chime. */
  function chime(kind) {
    if (!cfg.sounds) return;
    const ctx = audioCtx(); if (!ctx) return;
    const t0 = ctx.currentTime + 0.02;
    const master = ctx.createGain(); master.gain.value = 0.9; master.connect(ctx.destination);
    try {
      if (kind === "lock") {
        const click = (at, freq, len) => {
          const n = Math.floor(ctx.sampleRate * len), buf = ctx.createBuffer(1, n, ctx.sampleRate), d = buf.getChannelData(0);
          for (let i = 0; i < n; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / n, 3);
          const src = ctx.createBufferSource(); src.buffer = buf;
          const f = ctx.createBiquadFilter(); f.type = "bandpass"; f.frequency.value = freq; f.Q.value = 0.9;
          const g = ctx.createGain(); g.gain.value = 1.4;
          src.connect(f); f.connect(g); g.connect(master); src.start(t0 + at);
        };
        click(0, 2600, 0.05); click(0.085, 1700, 0.09);  // the two halves of a shutter: open, close
        const thump = ctx.createOscillator(), tg = ctx.createGain();
        thump.frequency.setValueAtTime(190, t0 + 0.085); thump.frequency.exponentialRampToValueAtTime(70, t0 + 0.2);
        tg.gain.setValueAtTime(0.0001, t0 + 0.085); tg.gain.exponentialRampToValueAtTime(0.5, t0 + 0.1); tg.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.22);
        thump.connect(tg); tg.connect(master); thump.start(t0 + 0.085); thump.stop(t0 + 0.25);
      } else {
        [[392, 0], [523.25, 0.12], [659.25, 0.24], [783.99, 0.36], [1046.5, 0.5]].forEach(([freq, at], i, all) => {
          const last = i === all.length - 1;
          [["sine", 1], ["triangle", 0.35]].forEach(([type, level]) => {
            const o = ctx.createOscillator(), g = ctx.createGain();
            o.type = type; o.frequency.value = freq;
            g.gain.setValueAtTime(0.0001, t0 + at); g.gain.exponentialRampToValueAtTime(0.22 * level, t0 + at + 0.03);
            g.gain.exponentialRampToValueAtTime(0.0001, t0 + at + (last ? 1.6 : 0.9));
            o.connect(g); g.connect(master); o.start(t0 + at); o.stop(t0 + at + (last ? 1.7 : 1));
          });
        });
      }
    } catch { /* no sound: nothing is lost */ }
  }
  // The first tap unlocks audio in a browser, so the chime can play later when the PC reports back.
  document.addEventListener("pointerdown", () => { if (cfg.sounds) audioCtx(); }, { once: true, capture: true });

  /** A lock or unlock request that was waiting and has now finished: play its sound once. */
  const powerWaiting = new Set(), powerHeard = new Set();
  /** "lock", "unlock" or "": from the step's own name, or (a PC that is not updated yet) from its description. */
  const powerKind = (s) => s.power === "lock" || s.power === "unlock" ? s.power : /sign-in at the lock screen/i.test(s.detail || "") ? "unlock" : /lock the screen/i.test(s.detail || "") ? "lock" : "";
  const screenSynced = new Set();
  function powerSounds() {
    for (const j of S.jobs) {
      if (j.status === "done" && !screenSynced.has(j.id) && (j.steps || []).some((s) => s.action === "power" && /black/i.test(s.detail || ""))) { screenSynced.add(j.id); loadInfo(); }   // the Home button now says the opposite
      const step = (j.steps || []).map((s) => (s.action === "power" ? { power: powerKind(s) } : null)).find((s) => s && s.power);
      if (!step || powerHeard.has(j.id)) continue;
      if (j.status === "done") { powerHeard.add(j.id); if (powerWaiting.has(j.id) || S.mine.has(j.id)) { powerWaiting.delete(j.id); chime(step.power); haptic("success"); } }
      else if (["failed", "rejected", "stopped", "expired", "undone"].includes(j.status)) powerHeard.add(j.id);
      else powerWaiting.add(j.id);
    }
  }

  /* ===================================================================== state */

  const S = {
    token: store.raw("afp_token"),
    jobs: [], audit: [], info: null, loaded: false, online: true, failures: 0,
    conv: store.raw("afp_conv"), query: "", model: null,
    hidden: new Set(store.get("afp_hidden", [])), titles: store.get("afp_titles", {}), autoTitles: store.get("afp_autotitles", {}), titleAsked: new Set(),
    nodes: new Map(), threadConv: null, firstPaint: true, stick: true, hero: null,
    mine: new Set(), voiceJobs: new Set(), spoken: new Set(), seen: new Map(),
    muted: store.get("afp_muted", false), speaking: false,
    focus: {}, editorOpened: new Set(), mode: "type", drafts: {}, openTrails: new Set(), sheets: [], deferredInstall: null,
    offline: null,  // reading saved chats because the PC can't be reached: { savedAt, boot }
    tab: "home",  // which main screen is showing (Files and PC open their own panels). The app always opens on Home, so Back has somewhere to go.
    chatPushed: false,  // the chat was opened from Home and has its own history entry
  };
  const urlParams = new URLSearchParams(location.search);
  const fromUrl = urlParams.get("token");
  if (fromUrl) { S.token = fromUrl; store.setRaw("afp_token", fromUrl); history.replaceState(history.state, "", location.pathname + location.hash); }
  // A pairing link (the PC's QR code) carries a one-time code; it is claimed once the page is up, then it leaves the address bar.
  const nameFromUrl = (urlParams.get("name") || "").trim().slice(0, 40);  // the app passes what the person called this phone on its connect screen
  if (nameFromUrl) store.setRaw("afp_device", nameFromUrl);
  // The code travels after the # (a browser never sends that to a server, so no proxy or relay log can hold it). An older QR code still has it as ?pair=, so both work.
  const pairInHash = /(?:^#\/?|&)pair=([A-Za-z0-9-]{8,12})(?:&|$)/.exec(location.hash);
  const pairFromUrl = (pairInHash ? pairInHash[1] : urlParams.get("pair")) || "";
  window.addEventListener("hashchange", () => { if (/(?:^#\/?|&)pair=/.test(location.hash)) location.reload(); });  // a pairing link opened in a page that is already open: start over with it
  if (pairFromUrl) history.replaceState(history.state, "", location.pathname + (pairInHash ? "" : location.hash));

  /* ===================================================================== api */

  let Link = null;  // the connection monitor (see "losing the PC" below)
  async function api(path, opts = {}) {
    if (S.offline) { const e = new Error("Your PC is offline, so that has to wait. You can read your saved chats."); e.offline = true; throw e; }  // reading saved chats never reaches for the PC
    const res = await fetch("/api" + path, { ...opts, headers: { "X-Token": S.token, "Content-Type": "application/json", ...(opts.headers || {}) } });
    if (res.status === 401) {
      // The PC no longer knows this phone (removed or blocked there): forget the token so the sign-in screen can say so.
      if (S.token) { S.signedOut = true; S.token = ""; store.del("afp_token"); }
      Saved.clear();  // the PC no longer knows this phone: its saved chats go too
      if (Link) Link.reset();  // "signed out" is the answer now, not "can't reach your PC"
      showAuth(); const e = new Error("Not signed in"); e.code = 401; throw e;
    }
    if (!res.ok) {
      let detail = res.statusText, data = null;
      try {
        const body = await res.json();
        if (body.detail && typeof body.detail === "object") { data = body.detail; detail = data.message || detail; }
        else if (typeof body.detail === "string") detail = body.detail;
      } catch { /* keep status text */ }
      const err = new Error(detail);
      err.status = res.status; err.data = data;
      throw err;
    }
    return res;
  }

  /* ===================================================================== data model */

  const LABEL = { planning: "Planning", running: "Running", stopped: "Stopped", needs_access: "Waiting for access", awaiting_approval: "Waiting for approval", done: "Done", rejected: "Rejected", failed: "Failed", undone: "Undone", expired: "Expired" };
  const EVENT = {
    submitted: ["Request received"], awaiting_approval: ["Waiting for approval", "wait"], approved: ["You approved", "good"],
    rejected: ["You rejected", "bad"], expired: ["Approval expired", "bad"], replied: ["Replied", "good"], done: ["Finished", "good"],
    failed: ["Failed", "bad"], stopped: ["You stopped it", "wait"],
    access_requested: ["Asked to open a folder", "wait"], access_granted: ["You allowed a folder", "good"],
    access_denied: ["You declined a folder", "bad"], access_revoked: ["You took back a folder"], list_dir: ["Listed a folder"], read_file: ["Read a file"], find_files: ["Searched for files"],
    write_file: ["Wrote a file", "good"], delete_file: ["Deleted a file", "bad"], move_file: ["Moved a file", "good"],
    copy_file: ["Copied a file", "good"], send_file: ["File ready to download", "good"], download: ["Downloaded a file"],
    owner_setup: ["The owner set up this PC", "good"], device_paired: ["A phone joined", "good"], pair_failed: ["A wrong pairing code was tried", "bad"],
    device_signed_out: ["A phone signed itself out"], device_removed: ["A phone was removed on the PC"], device_blocked: ["A phone was blocked", "bad"],
    device_unblocked: ["A phone was unblocked"], device_renamed: ["A phone was renamed"], admin_login_failed: ["A wrong password was tried on the PC", "bad"],
    password_changed: ["The PC password was changed"], password_reset: ["The PC password was reset with the recovery code", "wait"],
    opened_in_editor: ["Opened in the editor"], opened_in_viewer: ["Opened full screen"],
    undo: ["Undid the changes"], run_command: ["Ran a command", "wait"], command_finished: ["Command finished"],
  };
  const NOTE_EVENTS = new Set(["download", "opened_in_editor", "opened_in_viewer", "access_granted", "access_revoked"]);
  /* events that belong to no request (job "-") appear in the chat as one-line notes; say what really happened */
  function noteText(n) {
    const what = n.path || n.folders || "a file";
    switch (n.event) {
      case "download": return "Downloaded " + what;
      case "opened_in_editor": return "Opened " + what + " in the editor";
      case "opened_in_viewer": return "Opened " + what + " full screen";
      case "access_granted": return "You allowed " + (n.mode === "edit" ? "editing in " : "reading ") + what;
      case "access_revoked": return "You took back access to " + what;
      default: return (EVENT[n.event] || [n.event])[0] + " " + what;
    }
  }
  const SKIP_DETAIL = new Set(["time", "job", "event", "prompt", "conversation"]);
  const legacyId = (time) => { const d = new Date(time || 0); return "old-" + d.getFullYear() + pad(d.getMonth() + 1) + pad(d.getDate()); };

  function buildModel() {
    const events = new Map(), convOf = new Map(), downloads = [];
    for (const ev of S.audit) {
      if (ev.job === "-") {
        if (!NOTE_EVENTS.has(ev.event)) continue; // pairing and sign-in events live in the activity log, not in the chat
        const last = downloads[downloads.length - 1];
        // opening the same file again right away is one note, not six
        if (last && last.event === ev.event && last.path === ev.path && new Date(ev.time) - new Date(last.time) < 120000) continue;
        downloads.push(ev); continue;
      }
      if (!events.has(ev.job)) events.set(ev.job, []);
      events.get(ev.job).push(ev);
      if (ev.event === "submitted") convOf.set(ev.job, ev.conversation || legacyId(ev.time));
    }
    const live = new Map(S.jobs.map((j) => [j.id, j]));
    const turns = [];
    for (const id of new Set([...events.keys(), ...live.keys()])) {
      const job = live.get(id) || null, list = events.get(id) || [];
      const sub = list.find((e) => e.event === "submitted") || list[0];
      turns.push({
        id, job, events: list,
        prompt: job ? job.prompt : (sub && sub.prompt) || "(request not recorded)",
        time: job ? job.created_at : (sub && sub.time) || "",
        conv: job ? job.conversation : convOf.get(id) || legacyId(sub && sub.time),
      });
    }
    turns.sort((a, b) => new Date(a.time) - new Date(b.time));
    const byId = new Map();
    for (const t of turns) {
      let c = byId.get(t.conv);
      if (!c) { c = { id: t.conv, turns: [], first: t.time, last: t.time, notes: [] }; byId.set(t.conv, c); }
      c.turns.push(t); c.last = t.time;
    }
    const ordered = [...byId.values()].sort((a, b) => new Date(a.first) - new Date(b.first));
    for (const d of downloads) {
      let target = null;
      for (const c of ordered) if (new Date(c.first) <= new Date(d.time)) target = c;
      (target || ordered[0] || { notes: [] }).notes.push(d);
    }
    // A chat's name: the one you typed, else the one this PC's own model wrote, else one made on the phone from the first request's words.
    for (const c of ordered) c.title = S.titles[c.id] || S.autoTitles[c.id] || (window.AfpTitles ? window.AfpTitles.make(c.turns[0].prompt) : clip(c.turns[0].prompt.replace(/\s+/g, " "), 60));
    const convs = ordered.slice().sort((a, b) => new Date(b.last) - new Date(a.last));
    return { convs, byId };
  }

  /* ===================================================================== look: orb and glyph */

  function orbSVG() {
    const bars = Array.from({ length: 48 }, (_, i) => {
      const k = (0.5 + 0.5 * Math.abs(Math.sin(i * 1.7) * Math.cos(i * 0.6))).toFixed(3);
      return `<g transform="rotate(${(360 / 48) * i} 120 120)"><rect class="bar" x="118.8" y="42" width="2.4" height="16" rx="1.2" fill="url(#oBar)" style="--i:${i};--k:${k}"/></g>`;
    }).join("");
    const wrap = h("div", { class: "orb", "aria-hidden": "true" });
    wrap.innerHTML = `<svg viewBox="0 0 240 240" focusable="false"><defs>
      <radialGradient id="oHalo" cx="50%" cy="50%" r="50%"><stop offset="0%" stop-color="#22d3ee" stop-opacity=".28"/><stop offset="55%" stop-color="#3b82f6" stop-opacity=".1"/><stop offset="100%" stop-color="#3b82f6" stop-opacity="0"/></radialGradient>
      <radialGradient id="oEye" cx="38%" cy="32%" r="75%"><stop offset="0%" stop-color="#e0fbff"/><stop offset="28%" stop-color="#22d3ee"/><stop offset="68%" stop-color="#2563eb"/><stop offset="100%" stop-color="#0b1d4a"/></radialGradient>
      <linearGradient id="oBar" x1="0" y1="1" x2="0" y2="0"><stop offset="0%" stop-color="#3b82f6"/><stop offset="100%" stop-color="#67e8f9"/></linearGradient>
      <linearGradient id="oScan" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#22d3ee" stop-opacity="0"/><stop offset="100%" stop-color="#67e8f9"/></linearGradient></defs>
      <circle cx="120" cy="120" r="118" fill="url(#oHalo)"/>
      <g class="spin-a ticks"><circle cx="120" cy="120" r="108" fill="none" stroke="rgba(34,211,238,.55)" stroke-width="7" stroke-dasharray="1.4 8.7"/></g>
      <g class="spin-a arcs"><circle cx="120" cy="120" r="96" fill="none" stroke="#3b82f6" stroke-width="2" stroke-linecap="round" stroke-dasharray="110 26 54 26 170 26 70 26"/></g>
      <circle cx="120" cy="120" r="86" fill="none" stroke="rgba(148,163,184,.2)"/>
      <g class="spin-a scan"><circle cx="120" cy="120" r="86" fill="none" stroke="url(#oScan)" stroke-width="3" stroke-linecap="round" stroke-dasharray="130 410"/></g>
      <g class="bars">${bars}</g>
      <circle class="ripple" cx="120" cy="120" r="46" fill="none" stroke="#22d3ee" stroke-width="1.5"/>
      <circle class="ripple b" cx="120" cy="120" r="46" fill="none" stroke="#22d3ee" stroke-width="1.5"/>
      <g class="eye"><circle cx="120" cy="120" r="40" fill="url(#oEye)"/><circle cx="120" cy="120" r="40" fill="none" stroke="rgba(224,251,255,.35)"/><circle cx="120" cy="120" r="27" fill="none" stroke="rgba(255,255,255,.22)"/><ellipse cx="108" cy="104" rx="13" ry="7" fill="rgba(255,255,255,.55)" transform="rotate(-28 108 104)"/></g></svg>`;
    return wrap;
  }
  function glyph(size, still) {
    const g = h("span", { class: "ico glyph" + (still ? " still" : ""), "aria-hidden": "true" });
    g.innerHTML = `<svg viewBox="0 0 24 24" focusable="false"><g class="ring"><circle cx="12" cy="12" r="9.5" fill="none" stroke="var(--a1)" stroke-width="1.8" stroke-linecap="round" stroke-dasharray="14 5.7"/></g><g class="core"><circle cx="12" cy="12" r="4" fill="var(--a1)"/><circle cx="10.8" cy="10.8" r="1.3" fill="#fff" opacity=".85"/></g></svg>`;
    if (size) { g.style.width = g.style.height = size + "px"; }
    return g;
  }
  const initials = (name) => (name.trim().split(/\s+/).map((p) => p[0]).slice(0, 2).join("") || "?").toUpperCase();
  function avatarColor(name) {
    let n = 0;
    for (const ch of name) n = (n * 31 + ch.charCodeAt(0)) % 360;
    return `linear-gradient(135deg, hsl(${n} 70% 52%), hsl(${(n + 40) % 360} 70% 42%))`;
  }
  const displayName = () => (cfg.name || (S.info && S.info.user) || "You").trim() || "You";
  function avatar(big) { const n = displayName(); return h("span", { class: "avatar" + (big ? " lg" : ""), vars: { "--av": avatarColor(n) }, "aria-hidden": "true" }, initials(n)); }

  /* ===================================================================== toasts, menus, sheets */

  function toast(message, action) {
    const box = $("#toasts");
    const el = h("div", { class: "toast" }, h("span", { text: message }));
    if (action) el.append(h("button", { type: "button", onclick: () => { action.run(); gone(); } }, action.label));
    box.append(el);
    let timer = setTimeout(gone, action ? 6000 : 3200);
    function gone() { clearTimeout(timer); el.classList.add("bye"); setTimeout(() => el.remove(), 220); }
    return gone;
  }

  let openMenu = null, menuAway = null;
  function closeMenu() {
    if (menuAway) { document.removeEventListener("pointerdown", menuAway, true); menuAway = null; }
    if (openMenu) { openMenu.remove(); openMenu = null; }
  }
  function menu(anchor, items, head) {
    closeMenu();
    const el = h("div", { class: "menu", role: "menu" });
    if (head) el.append(h("div", { class: "menu-head" }, h("b", { text: head[0] }), h("span", { text: head[1] })));
    for (const item of items) {
      if (item === "-") { el.append(h("div", { class: "menu-sep" })); continue; }
      el.append(h("button", { type: "button", role: "menuitem", class: "menu-item" + (item.danger ? " danger" : ""), onclick: () => { closeMenu(); haptic("tick"); item.run(); } }, ico(item.icon), item.label));
    }
    $("#layer").append(el);
    const a = anchor.getBoundingClientRect(), w = el.offsetWidth, ht = el.offsetHeight;
    let left = Math.min(Math.max(8, a.left), innerWidth - w - 8), top = a.bottom + 6;
    if (top + ht > innerHeight - 8) top = Math.max(8, a.top - ht - 6);
    el.style.left = left + "px"; el.style.top = top + "px";
    openMenu = el;
    menuAway = (e) => { if (!el.contains(e.target)) closeMenu(); };
    setTimeout(() => { if (openMenu === el) document.addEventListener("pointerdown", menuAway, true); }, 0);
  }

  // Sheets keep the browser's Back button useful: Back closes the sheet instead of leaving the page.
  let ignorePops = 0;
  function openSheet({ title, body, full = false, route = "sheet", push = true, guard = null, onClose = null }) {
    const wrap = h("div", { class: "sheet-wrap", "data-route": route });   // (the route lets the stylesheet tell the PC screen's own sheet from a sheet opened over it)
    const sheet = h("div", { class: "sheet" + (full ? " full" : ""), role: "dialog", "aria-modal": "true", "aria-label": title, tabindex: "-1" });
    const inner = h("div", { class: "sheet-body" }, body);
    sheet.append(h("div", { class: "sheet-grip" }),
      h("div", { class: "sheet-head" }, h("h2", { text: title }), h("button", { class: "iconbtn", type: "button", "aria-label": "Close", onclick: () => closeSheet() }, ico("x"))), inner);
    wrap.append(sheet);
    wrap.addEventListener("pointerdown", (e) => { if (e.target === wrap) closeSheet(); });
    $("#layer").append(wrap);
    const rec = { wrap, route, pushed: push, guard, onClose, opener: document.activeElement };
    S.sheets.push(rec);
    if (push) history.pushState({ sheet: route }, "", "#/" + route);
    sheet.focus({ preventScroll: true });
    haptic("light");
    return { body: inner, close: closeSheet };
  }
  /* a panel that is not a pop-up (the file canvas): it takes part in Back, Escape and the unsaved-changes guard */
  function openPanel({ route, hash, push = true, guard = null, onClose = null, el = null }) {
    const rec = { wrap: el, route, pushed: push, guard, onClose, custom: true };
    S.sheets.push(rec);
    if (push) history.pushState({ sheet: route }, "", hash || "#/" + route);
    return rec;
  }
  function removeTopSheet() {
    const rec = S.sheets.pop();
    if (!rec) return null;
    if (rec.custom) { if (rec.onClose) rec.onClose(); return rec; }
    rec.wrap.classList.add("out");
    rec.wrap.inert = true;
    setTimeout(() => rec.wrap.remove(), 200);
    if (rec.onClose) rec.onClose();
    if (rec.opener && rec.opener.isConnected) rec.opener.focus({ preventScroll: true });
    return rec;
  }
  function closeSheet() {
    const top = S.sheets[S.sheets.length - 1];
    if (top && top.guard && !top.guard()) return; // e.g. unsaved edits: the user chose to keep editing
    const rec = removeTopSheet();
    if (rec && rec.pushed) { ignorePops += 1; history.back(); }
  }
  /* Back, one rule everywhere: it closes the top-most thing (a menu, the chat list, a voice recording, the tour, a pop-up or a file),
     and only when nothing is open does it leave the chat for Home. Home is the bottom of the stack: Back there leaves the app. */
  const undoPop = () => history.pushState(history.state || { keep: 1 }, "", location.href);  // an overlay without its own history entry took the Back: put the entry back
  window.addEventListener("popstate", () => {
    if (ignorePops > 0) { ignorePops -= 1; return; }
    if (S.dialog) { S.dialog(false); undoPop(); return; }
    const scanner = $(".scan[role='dialog']"), scanX = scanner && $(".scan-x", scanner);
    if (scanX && scanner.offsetParent !== null) { scanX.click(); undoPop(); return; }
    if (openMenu) { closeMenu(); undoPop(); return; }
    const tour = $(".onb");
    if (tour && !tour.classList.contains("out")) { document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })); undoPop(); return; }  // Back closes the tour, like Escape
    if (V.phase !== "idle") { voiceEnd(true); undoPop(); return; }
    const top = S.sheets[S.sheets.length - 1];
    if (!top && document.body.classList.contains("drawer")) { closeDrawer(); undoPop(); return; }
    if (top && top.guard && !top.guard()) { history.pushState({ sheet: top.route }, "", "#/" + top.route); return; } // Back with unsaved edits
    if (top) { removeTopSheet(); return; } // the Back button closed it
    if (S.chatPushed) { S.chatPushed = false; setTab("home", { push: false }); }  // Back from the chat: Home, not out of the app
  });

  /* A centred question with two buttons, in the app's own look (instead of the browser's plain box). Resolves true for the main button.
     Back, Escape and a tap outside all mean "no". */
  function ask({ title, message = "", ok = "OK", cancel = "Cancel", tone = "accent", icon = "alert" }) {
    return new Promise((resolve) => {
      const wrap = h("div", { class: "dlg-wrap", role: "presentation" });
      const yes = h("button", { class: "btn " + (tone === "danger" ? "danger" : "primary"), type: "button" }, ok);
      const no = h("button", { class: "btn ghost", type: "button" }, cancel || "Close");
      if (!cancel) no.hidden = true;
      const box = h("div", { class: "dlg " + tone, role: "alertdialog", "aria-modal": "true", "aria-label": title },
        h("div", { class: "dlg-ico" }, ico(icon, 26)), h("h2", { text: title }), message ? h("p", { text: message }) : null, h("div", { class: "dlg-btns" + (cancel ? "" : " one") }, no, yes));
      wrap.append(box);
      const opener = document.activeElement;
      let done = false;
      const finish = (v) => {
        if (done) return; done = true; S.dialog = null;
        wrap.classList.add("out"); setTimeout(() => wrap.remove(), 160);
        wrap.inert = true;
        if (opener && opener.isConnected) opener.focus({ preventScroll: true });
        if (v) haptic(tone === "danger" ? "medium" : "light");
        resolve(v);
      };
      yes.onclick = () => finish(true); no.onclick = () => finish(false);
      wrap.addEventListener("pointerdown", (e) => { if (e.target === wrap) finish(false); });
      S.dialog = finish;
      (S.locked ? document.body : $("#layer")).append(wrap);  // on the lock screen too: it has to sit above it, not behind
      haptic("light");
      setTimeout(() => (tone === "danger" ? no : yes).focus(), 60);  // a destructive question starts on the safe button
    });
  }
  const askDiscard = (title) => ask({ title, message: "Your changes to this file haven't been saved. If you carry on, they will be lost.", ok: "Discard changes", cancel: "Keep editing", tone: "danger", icon: "pencil" });

  function askText(title, value, confirm) {
    return new Promise((resolve) => {
      let done = false;
      const input = h("input", { class: "text-input", type: "text", value, maxlength: "80", "aria-label": title });
      const finish = (v) => { if (done) return; done = true; sheet.close(); resolve(v); };
      const sheet = openSheet({
        title, route: "rename",
        body: h("form", { onsubmit: (e) => { e.preventDefault(); finish(input.value.trim() || null); } }, input,
          h("div", { class: "actions", style: "margin-top:14px" }, h("button", { class: "btn primary", type: "submit" }, confirm), h("button", { class: "btn ghost", type: "button", onclick: () => finish(null) }, "Cancel"))),
      });
      setTimeout(() => { input.focus(); input.select(); }, 60);
    });
  }

  /* ===================================================================== sidebar */

  function sideShell() {
    S.sideList = h("nav", { class: "convs", "aria-label": "Your chats" });
    S.sideFoot = h("div", { class: "side-foot" });
    const search = h("label", { class: "search" }, ico("search"),
      h("input", { type: "search", placeholder: "Search chats", "aria-label": "Search chats", value: S.query, oninput: (e) => { S.query = e.target.value; update(true); } }));
    $("#side").replaceChildren(
      h("div", { class: "side-head" },
        h("div", { class: "brand" }, glyph(26), h("span", { class: "brand-name", text: "AccessFromPhone" })),
        h("button", { class: "iconbtn only-mobile", type: "button", "aria-label": "Close menu", onclick: closeDrawer }, ico("x"))),
      h("button", { class: "newchat", type: "button", onclick: newChat }, ico("plus"), "New chat"),
      search, S.sideList, S.sideFoot);
  }

  function renderSide() {
    if (!S.sideList) sideShell();
    const q = S.query.trim().toLowerCase();
    const convs = S.model.convs.filter((c) => !S.hidden.has(c.id) && (!q || c.title.toLowerCase().includes(q) || c.turns.some((t) => t.prompt.toLowerCase().includes(q))));
    const sig = JSON.stringify([convs.map((c) => [c.id, c.title, c.last]), S.conv, q, displayName(), S.online, S.loaded, S.info && S.info.pc, S.muted]);
    if (sig === S.sideSig) return;
    S.sideSig = sig;

    const list = [];
    if (!S.loaded) list.push(h("div", { class: "skel" }), h("div", { class: "skel" }), h("div", { class: "skel" }));
    else if (!convs.length) list.push(h("div", { class: "side-note", text: q ? "No chats match that." : "Your chats will appear here." }));
    let lastGroup = "";
    for (const c of convs) {
      const g = groupOf(c.last);
      if (g !== lastGroup) { list.push(h("div", { class: "grp", text: g })); lastGroup = g; }
      const more = h("button", { class: "iconbtn conv-more", type: "button", "aria-label": "Chat options", onclick: (e) => { e.stopPropagation(); chatMenu(more, c); } }, ico("more"));
      list.push(h("div", { class: "conv", "data-active": String(c.id === S.conv) },
        h("button", { class: "conv-open", type: "button", onclick: () => openChat(c.id) },
          h("span", { class: "conv-title", text: c.title }), h("span", { class: "conv-time", text: ago(c.last) })), more));
    }
    S.sideList.replaceChildren(...list);

    const pc = (S.info && S.info.pc) || "your PC";
    const speaks = "speechSynthesis" in window;
    const state = S.online ? "ok" : "off";
    // A div, not a button: the battery is a button of its own and sits on the "Connected" line.
    const open = (e) => profileMenu(e.currentTarget);
    const profile = h("div", { class: "profile", role: "button", tabindex: "0", "aria-label": "Profile and settings", onclick: open,
      onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(e); } } },
      h("span", { class: "av-wrap" }, avatar(), h("i", { class: "av-dot", "data-s": state })),
      h("span", { class: "profile-text" }, h("div", { class: "profile-top" }, h("span", { class: "profile-name", text: displayName() })),
        h("div", { class: "profile-sub" }, h("b", { class: "st", "data-s": state, text: S.online ? "Connected" : "Offline" }),
          S.online ? h("span", { class: "pcname", text: pc }) : null)));
    S.sideFoot.replaceChildren(h("div", { class: "profile-row" }, profile, speaks ? muteButton() : null));
  }

  function chatMenu(anchor, c) {
    menu(anchor, [
      { icon: "pencil", label: "Rename", run: async () => { const v = await askText("Rename chat", c.title, "Save"); if (v) { S.titles[c.id] = v; store.set("afp_titles", S.titles); update(true); } } },
      { icon: "trash", label: "Remove from this device", danger: true, run: () => hideChat(c) },
    ]);
  }
  function hideChat(c) {
    S.hidden.add(c.id); store.set("afp_hidden", [...S.hidden]);
    if (S.conv === c.id) { const next = S.model.convs.find((x) => !S.hidden.has(x.id)); S.conv = next ? next.id : newId(); store.setRaw("afp_conv", S.conv); }
    update(true);
    toast("Chat removed from this device", { label: "Undo", run: () => { S.hidden.delete(c.id); store.set("afp_hidden", [...S.hidden]); update(true); } });
  }
  function profileMenu(anchor) {
    const items = [
      { icon: "folder", label: "My files", run: () => openFolder("") },
      { icon: "sliders", label: "Settings", run: () => openSettings() },
      { icon: "gauge", label: "PC status", run: () => openPcStatus() },
      { icon: "activity", label: "Activity log", run: () => openActivity() },
    ];
    if (S.deferredInstall && !window.AfpApp) items.push({ icon: "phone", label: "Install app", run: async () => { S.deferredInstall.prompt(); S.deferredInstall = null; } });
    items.push("-", { icon: "logout", label: "Sign out", danger: true, run: signOut });
    menu(anchor, items, [displayName(), `${(S.info && S.info.pc) || "This PC"}${S.info ? " · " + S.info.user : ""}`]);
  }

  function openDrawer() { document.body.classList.add("drawer"); $("#scrim").hidden = false; haptic("tick"); }
  function closeDrawer() { document.body.classList.remove("drawer"); $("#scrim").hidden = true; }
  function newChat() {
    S.conv = newId(); store.setRaw("afp_conv", S.conv); closeDrawer(); haptic("light"); S.stick = true;
    if (S.tab !== "chat") setTab("chat"); else update(true);
    const f = $(".field"); if (f) f.focus();
  }
  function openChat(id) {
    if (id !== S.conv) { S.conv = id; store.setRaw("afp_conv", id); S.stick = true; haptic("tick"); }
    closeDrawer();
    if (S.tab !== "chat") setTab("chat"); else update(true);
  }

  /* ===================================================================== top bar */

  function renderTop() {
    const top = $("#top"), model = S.model, conv = model.byId.get(S.conv);
    const ai = S.info && S.info.ai;
    const kind = ai ? ai.kind : "none";
    const aiLabel = !ai ? "…" : kind === "hosted" ? `${ai.model} · hosted` : kind === "local" ? `${ai.model} · on this PC` : kind === "none" ? "No AI · typed commands" : "Custom AI";
    const speaks = "speechSynthesis" in window;
    const sig = JSON.stringify([conv && conv.title, S.conv, aiLabel, kind, S.muted, speaks, displayName(), S.hidden.size, PC.data && PC.data.battery ? [PC.data.battery.percent, PC.data.battery.plugged] : PC.data ? 1 : 0, netKey()]);
    if (sig === S.topSig) return;
    S.topSig = sig;
    top.replaceChildren(
      h("button", { class: "iconbtn home-btn", type: "button", "aria-label": "Back to Home", onclick: () => { haptic("tick"); setTab("home"); } }, ico("arrowLeft")),
      h("button", { class: "iconbtn menu-btn", type: "button", "aria-label": "Open chats", onclick: openDrawer }, ico("menu")),
      h("button", { class: "iconbtn only-desk", type: "button", "aria-label": "Show or hide the chat list", onclick: toggleSide }, ico("panel")),
      h("div", { class: "top-title" }, glyph(26), h("h1", { text: conv && !S.hidden.has(conv.id) ? conv.title : "New chat" })),
      h("button", { class: "chip-ai", type: "button", "data-kind": kind, "aria-label": aiLabel + ". Open AI settings", title: ai ? ai.note : "", onclick: () => openSettings() }, ico(kind === "hosted" ? "cloud" : "cpu"), h("span", { class: "t", text: aiLabel })),
      netChip("only-collapsed"), pcChip("only-collapsed"),
      h("button", { class: "iconbtn only-collapsed", type: "button", "aria-label": "New chat", onclick: newChat }, ico("plus")),
      speaks ? muteButton("only-collapsed") : null,
      h("button", { class: "iconbtn only-collapsed", type: "button", "aria-label": "Profile and settings", onclick: (e) => profileMenu(e.currentTarget) }, avatar()));
  }
  function toggleSide() {
    const on = document.body.classList.toggle("side-collapsed");
    store.set("afp_side_collapsed", on); haptic("tick");
    S.topSig = ""; renderTop();
  }
  function muteButton(extra = "") {
    return h("button", { class: "iconbtn mute-btn " + extra, type: "button", "aria-label": S.muted ? "Turn spoken replies on" : "Turn spoken replies off", "aria-pressed": String(S.muted), title: S.muted ? "Spoken replies are off" : "Spoken replies are on", onclick: toggleMute }, ico(S.muted ? "volumeOff" : "volume"));
  }
  function toggleMute() {
    S.muted = !S.muted; store.set("afp_muted", S.muted); if (S.muted) stopSpeaking(); haptic("tick"); S.topSig = ""; S.sideSig = ""; renderTop(); renderSide();
    toast(S.muted ? "Spoken replies are off" : "Spoken replies are on");
  }

  /* ===================================================================== thread */

  const STEP_LABEL = { read: "Read", write: "Write", delete: "Delete", send: "Send", move: "Move", copy: "Copy", run: "Run", deploy: "Deploy", power: "Power", app: "App" };
  const BUSY = new Set(["planning", "running"]);
  const NO_UNDO = " (cannot be undone)";
  const isReply = (j) => j.status === "done" && j.steps.length === 0 && j.outputs.length > 0;

  function eventList(events) {
    const ul = h("ul", { class: "events" });
    for (const ev of events) {
      const [label, tone] = EVENT[ev.event] || [ev.event, ""];
      const detail = Object.entries(ev).filter(([k, v]) => !SKIP_DETAIL.has(k) && v !== "").map(([k, v]) => `${k}: ${v}`).join(" · ");
      ul.append(h("li", { class: tone || "" }, h("span", { class: "t", text: fmtClock(ev.time) }), h("span", { class: "e", text: label }), detail ? h("span", { class: "d", text: detail }) : null));
    }
    return ul;
  }
  function trail(id, events) {
    if (!events.length) return null;
    const d = h("details", { class: "trail" }, h("summary", {}, ico("chevron"), `Audit trail · ${events.length} event${events.length === 1 ? "" : "s"}`), eventList(events));
    d.open = S.openTrails.has(id) || cfg.openTrails;
    d.addEventListener("toggle", () => { if (d.open) S.openTrails.add(id); else S.openTrails.delete(id); });
    return d;
  }

  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); }
    catch {
      const t = h("textarea", { style: "position:fixed;opacity:0" }); t.value = text; document.body.append(t); t.select();
      try { document.execCommand("copy"); } catch { /* nothing more to try */ } t.remove();
    }
    haptic("tick"); toast("Copied");
  }

  /* ===================================================================== downloads
     A file of any size. The PC gives a link and the file arrives in 2 MB pieces; in the phone app each piece is added to a partial file kept
     inside the app, so a dropped connection, a pause or even closing the app never loses what already came: it carries on from that byte.
     Tapping Download again for the same file also resumes. A finished file is copied into Downloads. In a browser, the browser downloads the link itself. */
  const DL = (() => {
    const CHUNK = 2 * 1024 * 1024, DELAYS = [1, 2, 4, 8, 15, 30], MAX_RUNNING = 2, WEEK = 7 * 864e5;
    const native = () => !!(window.AfpApp && window.AfpApp.partSize);
    let list = store.get("afp_dls", []);
    if (!Array.isArray(list)) list = [];
    const running = new Set(), views = new Set(), fillers = new Map();   // fillers: a view that draws itself (the live card on the screen view) instead of the whole list
    let pillTimer = 0, aliveAt = 0, sheet = null;

    const keyOf = (path) => { let a = 5381, b = 52711; for (const c of path.toLowerCase()) { a = ((a * 33) ^ c.charCodeAt(0)) >>> 0; b = (b * 31 + c.charCodeAt(0)) >>> 0; } return "d" + a.toString(36) + b.toString(36); };
    const save = () => { try { store.set("afp_dls", list.map(({ ctl, wake, stop, ...d }) => d)); } catch { /* storage full or blocked: the list just isn't remembered */ } };
    const fmtDur = (sec) => sec < 60 ? "under a minute" : sec < 3600 ? Math.round(sec / 60) + " min" : Math.floor(sec / 3600) + " h " + Math.round((sec % 3600) / 60) + " min";
    const MIME = { zip: "application/zip", pdf: "application/pdf", jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", gif: "image/gif", mp4: "video/mp4", mkv: "video/x-matroska", mp3: "audio/mpeg", txt: "text/plain", apk: "application/vnd.android.package-archive", json: "application/json", csv: "text/csv", docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" };
    const mimeOf = (name) => MIME[(name.split(".").pop() || "").toLowerCase()] || "application/octet-stream";
    const pct = (d) => d.size ? Math.min(100, Math.floor(d.got * 100 / d.size)) : 0;
    const active = () => list.filter((d) => d.state !== "done");
    const fatal = (message) => Object.assign(new Error(message), { fatal: true });

    // what the person sees in Android's notification shade, and keeping the app alive meanwhile
    function alive(force) {
      if (!native()) return;
      const now = Date.now();
      if (!force && now - aliveAt < 1000) return;
      aliveAt = now;
      const on = list.filter((d) => d.state === "running");
      try {
        if (!on.length) { window.AfpApp.keepAliveOff(); return; }
        const d = on[0];
        window.AfpApp.keepAlive(on.length > 1 ? on.length + " downloads" : d.name, `${pct(d)}%  -  ${fmtSize(d.got)} of ${fmtSize(d.size)}${d.speed ? "  -  " + fmtSize(d.speed) + "/s" : ""}`, pct(d));
      } catch { /* an older app build has no notification: the download still works */ }
    }

    function sleepOr(ms, d) { return new Promise((resolve) => { const t = setTimeout(resolve, ms); d.wake = () => { clearTimeout(t); resolve(); }; }); }

    async function ensureLink(d) {
      let info;
      try { info = await (await api("/files/link?path=" + encodeURIComponent(d.path), { method: "POST" })).json(); }
      catch (e) {
        if (e.code === 401) throw fatal("You were signed out. Sign in again, then tap Resume.");
        if (e.status === 403) throw fatal("Your PC needs your approval again. Ask for the file again; it will carry on from " + fmtSize(d.got) + ".");
        if (e.status === 404) throw fatal("That file is no longer on the PC.");
        if (e.status === 413) throw fatal(e.message);
        throw e; // no connection: tried again later
      }
      if (d.size && (info.size !== d.size || info.modified !== d.modified)) {  // the file changed on the PC: pieces of the old one are useless
        try { window.AfpApp.discardPart(d.id); } catch { /* nothing kept */ }
        d.got = 0; d.note = "The file changed on the PC, so it is downloading again from the start.";
      }
      Object.assign(d, { size: info.size, modified: info.modified, link: info.url, name: info.name, mime: mimeOf(info.name) });
    }

    async function run(d) {
      running.add(d.id);
      Object.assign(d, { state: "running", err: "", retry: 0, stop: null, speed: 0, offline: false });
      d.started = Date.now(); alive(true); paint(true);
      try {
        if (!d.link) await ensureLink(d).catch(async (e) => { if (e.fatal) throw e; d.offline = true; });
        d.got = Math.min(window.AfpApp.partSize(d.id), d.size || 0);
        const free = window.AfpApp.freeBytes();
        if (d.size && free && d.size - d.got + 64 * 1048576 > free) throw fatal(`Not enough space on this phone. It needs about ${fmtSize(d.size - d.got)} more free. Free some up, then tap Retry.`);
        let tick = performance.now(), tickGot = d.got;
        while (!d.stop && (!d.size || d.got < d.size || !d.link)) {
          try {
            if (!d.link) { await ensureLink(d); d.offline = false; d.got = Math.min(window.AfpApp.partSize(d.id), d.size); if (d.got >= d.size) break; }
            const end = Math.min(d.got + CHUNK, d.size) - 1;
            const ctl = d.ctl = new AbortController(), timer = setTimeout(() => ctl.abort(), 30000);  // a stalled connection is a dropped one
            let res, buf;
            try {
              res = await fetch(d.link, { headers: { Range: `bytes=${d.got}-${end}` }, signal: ctl.signal });
              if (res.status === 404) { d.link = ""; continue; }  // the link ran out (or the PC restarted): ask for a fresh one
              if (res.status !== 206 && !(res.status === 200 && d.got === 0)) throw new Error("The PC answered " + res.status);
              buf = new Uint8Array(await res.arrayBuffer());
            } finally { clearTimeout(timer); }
            if (!buf.length) throw new Error("empty piece");
            let bin = "";
            for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
            if (!window.AfpApp.appendPart(d.id, btoa(bin), d.got)) throw fatal("Couldn't write to this phone's storage. Is it full?");
            d.got += buf.length; d.retry = 0; d.offline = false;
            const now = performance.now();
            if (now - tick > 1200) { const rate = (d.got - tickGot) * 1000 / (now - tick); d.speed = d.speed ? d.speed * 0.6 + rate * 0.4 : rate; tick = now; tickGot = d.got; }
            paint(); alive();
          } catch (e) {
            if (d.stop) break;
            if (e.fatal) throw e;
            if (d.retry >= DELAYS.length) { d.state = "waiting"; d.speed = 0; d.offline = true; break; }  // keep what we have; carries on by itself when the network is back
            d.offline = true; d.speed = 0; paint(true);
            await sleepOr(DELAYS[d.retry++] * 1000, d);
          }
        }
        if (d.stop === "cancel") { try { window.AfpApp.discardPart(d.id); } catch { /* none */ } list = list.filter((x) => x !== d); }
        else if (d.stop === "pause") d.state = "paused";
        else if (d.state === "waiting") { /* stays waiting */ }
        else if (d.size && d.got >= d.size) {
          if (!d.size) window.AfpApp.appendPart(d.id, "", 0);
          d.state = "finishing"; d.speed = 0; paint(true);
          window.AfpApp.finishPart(d.id, d.name, d.mime || mimeOf(d.name));  // answers through window.__afpSaved
        }
      } catch (e) {
        d.state = "failed"; d.err = e.message || "The download stopped."; d.speed = 0; haptic("error");
      } finally {
        running.delete(d.id); d.ctl = d.wake = null; d.touched = Date.now();
        save(); alive(true); paint(true); pump();
      }
    }

    /* A finished download lands on the phone: a file card appears in 3D, tilts, then shrinks and drops into a glowing tray at the bottom with a ripple.
       Returns false (and does nothing) when the person asked their phone for less motion, or the browser cannot animate it. */
    function land(name, landed, sub) {
      const calm = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
      if (calm || typeof Element.prototype.animate !== "function") return false;
      document.querySelectorAll(".dlfx").forEach((n) => n.remove());
      const ext = ((name.split(".").pop() || "").slice(0, 4) || "file").toUpperCase();
      const card = h("div", { class: "dlfx-card" }, h("span", { class: "dlfx-file" }, ico("file", 30), h("em", { text: ext })), h("b", { text: name }), h("small", { text: sub || "Saved to this phone" }));
      const tray = h("div", { class: "dlfx-tray" }), ring = h("div", { class: "dlfx-ring" }), ring2 = h("div", { class: "dlfx-ring" });
      const fx = h("div", { class: "dlfx", "aria-hidden": "true" }, tray, card, ring, ring2);
      document.body.append(fx);
      // from where the card is to the tray. The box the animation lives in is measured, not the window: with the PC screen turned sideways in full screen the box is turned too (see app.css), so "down" is the phone's left edge there
      const dy = Math.round(fx.offsetHeight - 54 - (card.offsetTop + card.offsetHeight / 2));
      const at = (t, y, z, rx, ry, s, o) => ({ transform: `translate(-50%,-50%) translate3d(0,${y}px,${z}px) rotateX(${rx}deg) rotateY(${ry}deg) scale(${s})`, opacity: o, offset: t });
      const fly = card.animate([
        at(0, 46, -420, 38, -28, 0.55, 0), at(0.26, 0, 0, 10, 12, 1, 1), at(0.4, -4, 40, 4, -6, 1.04, 1),
        at(0.62, dy * 0.45, -160, 38, 10, 0.55, 1), at(0.86, dy, -380, 78, -8, 0.16, 0.95), at(1, dy + 8, -420, 84, 0, 0.08, 0)],
        { duration: 1500, easing: "cubic-bezier(.45,.05,.2,1)", fill: "forwards" });
      const pulse = (el, delay) => el.animate([{ transform: "translateX(-50%) scale(0.2)", opacity: 0.9 }, { transform: "translateX(-50%) scale(2.6)", opacity: 0 }], { duration: 700, delay, easing: "ease-out", fill: "both" });
      tray.animate([{ opacity: 0, transform: "translateX(-50%) scaleX(0.4)" }, { opacity: 1, transform: "translateX(-50%) scaleX(1)", offset: 0.5 }, { opacity: 0, transform: "translateX(-50%) scaleX(1.15)" }], { duration: 1500, easing: "ease-in-out", fill: "both" });
      haptic("light");
      const impact = setTimeout(() => { pulse(ring, 0); pulse(ring2, 130); haptic("success"); landed(); }, 1230);
      const end = () => { clearTimeout(impact); fx.remove(); };
      fly.onfinish = () => setTimeout(end, 700);
      setTimeout(end, 3000);   // never left behind
      return true;
    }

    window.__afpSaved = (key, ok, uri) => {
      const d = list.find((x) => x.id === key); if (!d) return;
      if (ok) {
        Object.assign(d, { state: "done", uri, got: d.size, err: "", finishedAt: Date.now() });
        const say = () => toast("Saved to Downloads: " + d.name, window.AfpApp.openSaved ? { label: "Open", run: () => open(d) } : undefined);
        if (!land(d.name, say)) { haptic("success"); say(); }
      }
      else { d.state = "failed"; d.err = "Couldn't copy it into Downloads (is the phone full?). The download is kept: free some space, then tap Retry."; haptic("error"); }
      save(); paint(true); pump();
    };

    function pump() {
      while (running.size < MAX_RUNNING) {
        const next = list.find((d) => d.state === "queued"); if (!next) break;
        run(next);
      }
    }

    function browserDownload(link) {
      const a = h("a", { href: link.url, download: link.name });
      document.body.append(a); a.click(); a.remove();
      const say = () => toast("Downloading " + link.name + " (" + fmtSize(link.size) + ")");
      if (!land(link.name, say, "Downloading to this device")) say();
    }

    async function start(path, opts = {}) {
      haptic("light");
      if (!native()) {
        try { browserDownload(await (await api("/files/link?path=" + encodeURIComponent(path), { method: "POST" })).json()); haptic("success"); }
        catch (e) { toast(e.message); haptic("error"); }
        return;
      }
      const id = keyOf(path);
      let d = list.find((x) => x.id === id);
      if (d && (d.state === "running" || d.state === "queued" || d.state === "finishing")) { if (!opts.quiet) { toast("Already downloading"); openDownloads(); } return; }
      if (d && d.state === "done") { list = list.filter((x) => x !== d); d = null; }
      if (!d) { d = { id, path, name: path.split(/[\\/]/).pop(), size: Number(opts.size) || 0, got: 0, link: "" }; list.unshift(d); }   // (size: known already when it was picked on the PC screen, so the card never says "0 B of 0 B")
      d.link = ""; d.err = ""; d.note = ""; d.state = "queued"; d.touched = Date.now();
      save(); paint(true);
      if (!opts.quiet) toast(d.got ? "Resuming " + d.name : "Download started", { label: "View", run: openDownloads });
      pump();
    }

    /* One download, drawn live (name, size, speed, time left, pause and cancel, and a big Open when it is saved): used by the card on the PC screen view, so a file picked there
       can be followed without leaving it. It uses the same row as the Downloads list, so the two always agree. */
    function watch(path) {
      const id = keyOf(path), box = h("div", { class: "dl dl-one", "aria-live": "polite" });
      box.dataset.fresh = String(Date.now());
      fillers.set(box, (b) => {
        const d = list.find((x) => x.id === id);
        if (!d) { b.replaceChildren(h("div", { class: "dl-gone" }, ico("x"), h("span", { text: "This download was removed." }))); return; }
        const kids = [row(d)];
        if (d.state === "done" && !d.gone) kids.push(h("div", { class: "actions dl-after" }, h("button", { class: "btn primary", type: "button", onclick: () => { haptic("light"); open(d); } }, ico("newtab"), "Open")));
        b.replaceChildren(...kids);
      });
      views.add(box); fillers.get(box)(box);
      return box;
    }
    /** Start a download and hand back the live view of it. In a plain browser there is nothing to follow (the browser shows its own), so it hands back null. */
    async function startAndWatch(path, size) {
      await start(path, { quiet: true, size });
      return native() ? watch(path) : null;
    }

    const pause = (d) => { if (d.state === "running") { d.stop = "pause"; if (d.ctl) d.ctl.abort(); if (d.wake) d.wake(); } else if (d.state === "queued" || d.state === "waiting") { d.state = "paused"; save(); paint(true); } };
    const resume = (d) => { if (["paused", "waiting", "failed"].includes(d.state)) { if (d.state === "failed") d.link = ""; d.state = "queued"; d.err = ""; save(); paint(true); pump(); } };
    async function cancel(d) {
      const yes = await ask({ title: "Cancel this download?", message: d.got ? `The ${fmtSize(d.got)} already received will be deleted.` : "Nothing has been received yet.", ok: "Cancel download", cancel: "Keep it", tone: "danger", icon: "trash" });
      if (!yes) return;
      if (d.state === "running") { d.stop = "cancel"; if (d.ctl) d.ctl.abort(); if (d.wake) d.wake(); return; }
      try { window.AfpApp.discardPart(d.id); } catch { /* none */ }
      list = list.filter((x) => x !== d); save(); paint(true); alive(true); haptic("light");
    }
    async function remove(d, file) {
      if (file) {
        const yes = await ask({ title: "Delete " + d.name + "?", message: "It is removed from this phone's Downloads folder. The original stays on your PC.", ok: "Delete", tone: "danger", icon: "trash" });
        if (!yes) return;
        try { window.AfpApp.deleteSaved(d.uri); } catch { /* already gone */ }
        toast("Deleted");
      }
      try { window.AfpApp.discardPart(d.id); } catch { /* none */ }
      list = list.filter((x) => x !== d); save(); paint(true); haptic("light");
    }
    function open(d) {
      let ok = false;
      try { ok = window.AfpApp.openSaved(d.uri, d.mime || mimeOf(d.name)); } catch { /* old app */ }
      if (!ok) toast("No app can open this here. Find it in your Downloads folder.");
    }
    const clearFinished = () => { list = list.filter((d) => d.state !== "done"); save(); paint(true); };

    // ---- the pieces on screen
    function line(d) {
      const left = d.speed > 0 && d.size ? fmtDur((d.size - d.got) / d.speed) + " left" : "";
      switch (d.state) {
        case "queued": return "Starting…";
        case "running": return d.offline ? `Connection lost. Trying again${d.retry ? " (" + d.retry + ")" : ""}…` : !d.size ? "Getting ready…" : [`${pct(d)}%`, `${fmtSize(d.got)} of ${fmtSize(d.size)}`, d.speed ? fmtSize(d.speed) + "/s" : "", left].filter(Boolean).join("  ·  ");
        case "waiting": return `Waiting for the connection  ·  kept ${fmtSize(d.got)} (${pct(d)}%). Carries on by itself.`;
        case "paused": return `Paused at ${pct(d)}%  ·  ${fmtSize(d.got)} of ${fmtSize(d.size)}`;
        case "finishing": return "Saving to Downloads…";
        case "failed": return d.err;
        case "done": return d.gone ? "Removed from Downloads" : `Saved to Downloads  ·  ${fmtSize(d.size)}  ·  ${ago(new Date(d.finishedAt || d.touched || Date.now()).toISOString())}`;
        default: return "";
      }
    }
    const btn = (icon, label, on, cls = "") => h("button", { class: "iconbtn dl-btn " + cls, type: "button", "aria-label": label, title: label, onclick: (e) => { e.stopPropagation(); on(); } }, ico(icon));
    function row(d) {
      const actions = d.state === "running" || d.state === "queued" ? [btn("pause", "Pause", () => pause(d)), btn("x", "Cancel", () => cancel(d), "bad")]
        : d.state === "waiting" ? [btn("play", "Resume now", () => resume(d)), btn("x", "Cancel", () => cancel(d), "bad")]
        : d.state === "paused" ? [btn("play", "Resume", () => resume(d)), btn("x", "Cancel", () => cancel(d), "bad")]
        : d.state === "failed" ? [btn("retry", "Retry", () => resume(d)), btn("x", "Remove", () => remove(d, false), "bad")]
        : d.state === "done" ? [d.gone ? null : btn("newtab", "Open", () => open(d)), btn("trash", d.gone ? "Remove from list" : "Delete", () => remove(d, !d.gone), "bad")] : [];
      const ext = (d.name.split(".").pop() || "").toLowerCase();
      return h("div", { class: "dl-row" + (d.state === "done" && Date.now() - (d.finishedAt || 0) < 3500 ? " landed" : ""), "data-state": d.state + (d.offline && d.state === "running" ? " offline" : "") },
        h("span", { class: "dl-ico" }, ico(["png", "jpg", "jpeg", "gif", "webp"].includes(ext) ? "image" : ["zip", "7z", "rar"].includes(ext) ? "folder" : "file")),
        h("div", { class: "dl-main" }, h("div", { class: "dl-name", text: d.name }),
          h("div", { class: "dl-sub", text: line(d) }),
          d.note && d.state !== "done" ? h("div", { class: "dl-note", text: d.note }) : null,
          d.state === "done" || d.state === "failed" ? null : h("div", { class: "dl-bar" }, h("i", { style: `width:${pct(d)}%` }))),
        h("div", { class: "dl-act" }, actions));
    }
    function fill(body) {
      const items = [...list];
      if (!items.length) { body.replaceChildren(h("div", { class: "dl-empty" }, ico("download", 34), h("b", { text: "No downloads yet" }), h("p", { text: "Ask for a file from the chat or open one in Files and tap Download. Big ones can be paused and carry on after a dropped connection." }))); return; }
      const finished = items.filter((d) => d.state === "done").length;
      body.replaceChildren(...[...items.map(row), finished ? h("button", { class: "hm-link dl-clear", type: "button", onclick: clearFinished }, "Clear finished (the files stay on your phone)") : null].filter(Boolean));
    }
    function pill() {
      const bar = $("#dlbar"); if (!bar) return;
      const act = active();
      if (!act.length || !native()) { bar.hidden = true; return; }
      const on = act.filter((d) => d.state === "running" || d.state === "queued"), waiting = act.filter((d) => d.state === "waiting"), bad = act.filter((d) => d.state === "failed"), fin = act.filter((d) => d.state === "finishing");
      let icon = "download", title, sub, tone = "run", p = null;
      if (on.length) { const d = on[0]; title = on.length > 1 ? `${on.length} downloads` : d.name; sub = on.length > 1 ? "Tap to see them" : d.offline ? "Connection lost. Trying again…" : line(d).split("  ·  ").slice(0, 3).join("  ·  "); p = on.length > 1 ? null : pct(d); if (d.offline) { icon = "wifiOff"; tone = "wait"; } }
      else if (fin.length) { title = fin[0].name; sub = "Saving to Downloads…"; }
      else if (waiting.length) { icon = "wifiOff"; title = "Waiting for the connection"; sub = `${waiting.length === 1 ? waiting[0].name : waiting.length + " downloads"} will carry on by itself`; tone = "wait"; }
      else if (bad.length) { icon = "alert"; title = bad.length === 1 ? "A download stopped" : bad.length + " downloads stopped"; sub = "Tap to retry"; tone = "bad"; }
      else { icon = "pause"; title = act.length === 1 ? act[0].name : act.length + " downloads paused"; sub = "Tap to resume"; tone = "wait"; }
      bar.hidden = false; bar.dataset.tone = tone;
      bar.replaceChildren(h("button", { class: "dl-pill", type: "button", onclick: () => { haptic("light"); openDownloads(); } }, ico(icon), h("span", { class: "grow" }, h("b", { text: title }), h("span", { text: sub })), p == null ? ico("chevron") : h("em", { text: p + "%" }),
        p == null ? null : h("i", { class: "dl-pillbar", style: `width:${p}%` })));
    }
    function paint(now) {
      if (!now && pillTimer) return;
      const go = () => { pillTimer = 0; pill(); for (const v of views) { if (v.isConnected) (fillers.get(v) || fill)(v); else if (!v.dataset.fresh || Date.now() - Number(v.dataset.fresh) > 3000) { views.delete(v); fillers.delete(v); } } };
      if (now) { clearTimeout(pillTimer); go(); } else pillTimer = setTimeout(go, 350);
    }
    function mount() { const body = h("div", { class: "dl" }); views.add(body); fill(body); (async () => { for (const d of list) if (d.state === "done" && d.uri && !d.gone) { try { if (!window.AfpApp.savedExists(d.uri)) { d.gone = true; paint(true); } } catch { /* old app */ } } })(); return body; }
    function openDownloads() {
      if (sheet) return;
      if (S.tab === "chat") { /* the pill is hidden there, but the sheet works anywhere */ }
      sheet = openSheet({ title: "Downloads", body: mount(), route: "downloads", onClose: () => { sheet = null; } });
    }

    // ---- the network coming back, and a tidy start
    window.addEventListener("online", () => {
      list.filter((d) => d.state === "waiting").forEach((d) => { d.state = "queued"; });
      running.forEach((id) => { const d = list.find((x) => x.id === id); if (d && d.wake) d.wake(); });
      paint(true); pump();
    });
    window.addEventListener("offline", () => { running.forEach((id) => { const d = list.find((x) => x.id === id); if (d && d.ctl) d.ctl.abort(); }); });
    const now = Date.now();
    list = list.filter((d) => !(d.state !== "done" && now - (d.touched || d.at || now) > WEEK));  // a week untouched: the leftovers go
    list.forEach((d) => { if (["running", "queued", "waiting", "finishing"].includes(d.state)) { d.state = "paused"; d.note = "Stopped when the app closed. Tap Resume to carry on."; } d.speed = 0; d.offline = false; });
    save();
    try { if (native()) window.AfpApp.dropPartsExcept(JSON.stringify(list.filter((d) => d.state !== "done").map((d) => d.id))); } catch { /* an older app build */ }
    setTimeout(() => paint(true), 0);
    return { start, startAndWatch, land, open: openDownloads, mount, count: () => active().length, paint };
  })();
  const openDownloads = () => DL.open();
  const download = (path) => DL.start(path);

  async function act(id, verb, buttons, body, onRefused) {
    buttons.forEach((b) => (b.disabled = true));
    haptic(verb === "reject" ? "light" : "medium");
    try {
      const job = await (await api(`/jobs/${id}/${verb}`, { method: "POST", ...(body ? { body: JSON.stringify(body) } : {}) })).json();
      S.jobs = S.jobs.map((j) => (j.id === id ? job : j));
      S.stick = true; update();
    } catch (e) {
      haptic("error"); buttons.forEach((b) => (b.disabled = false));
      if (onRefused && e.data && e.data.code) onRefused(e.message, e.data); else toast(e.message);  // a wrong PIN is said on the card itself, and the request stays waiting
    }
    refresh();
  }

  /* "May I open this folder?" Allow and keep, allow once, or no. Reading only: nothing in it can be changed. */
  function accessCard(j) {
    const card = h("div", { class: "plan access" });
    card.append(h("div", { class: "plan-title" }, ico("lock"), "Allow access to this folder?"));
    card.append(h("ul", { class: "paths access-paths" }, (j.access || []).map((f) => h("li", { text: f }))));
    card.append(h("p", { class: "note", text: "To do this I need to look inside it. I can read and search files there, but I can never change anything." }));
    const keep = h("button", { class: "btn primary", type: "button" }, ico("folder"), "Allow this folder");
    const once = h("button", { class: "btn", type: "button" }, "Just this once");
    const no = h("button", { class: "btn ghost", type: "button" }, "Don't allow");
    const all = [keep, once, no];
    keep.onclick = () => decideAccess(j.id, "always", all);
    once.onclick = () => decideAccess(j.id, "once", all);
    no.onclick = () => decideAccess(j.id, "deny", all);
    card.append(h("div", { class: "actions" }, keep, once, no));
    card.append(h("p", { class: "note small", text: "“Allow this folder” is remembered. You can take it back any time in Settings." }));
    return card;
  }
  async function decideAccess(id, decision, buttons) {
    buttons.forEach((b) => (b.disabled = true));
    haptic(decision === "deny" ? "light" : "medium");
    try {
      const job = await (await api(`/jobs/${id}/access`, { method: "POST", body: JSON.stringify({ decision }) })).json();
      S.jobs = S.jobs.map((j) => (j.id === id ? job : j));
      S.stick = true; update(true);
      if (decision === "always") loadInfo(); // so Settings lists the new folder
    } catch (e) { toast(e.message); haptic("error"); buttons.forEach((b) => (b.disabled = false)); }
    refresh();
  }
  async function revokeAccess(folder) {
    haptic("medium");
    try {
      const out = await (await api("/access/revoke", { method: "POST", body: JSON.stringify({ path: folder }) })).json();
      if (S.info) { S.info.folders.granted = out.granted; S.info.folders.granted_details = out.granted_details; }
      if (S.refillSettings) S.refillSettings();
      toast("Access removed");
    } catch (e) { toast(e.message); haptic("error"); }
  }

  /* How much a request can matter, in three colours: green only reads, amber changes files, red deletes, sends, runs or powers something. */
  const RISK_RANK = { read: 0, write: 1, move: 1, copy: 1, app: 1, deploy: 2, delete: 2, send: 2, run: 2, power: 2 };
  const RISK_TEXT = { safe: "Safe · only reads", change: "Changes files", high: "High impact" };
  function planRisk(steps) {
    let rank = 0;
    for (const s of steps) rank = Math.max(rank, s.action in RISK_RANK ? RISK_RANK[s.action] : 1);  // an action nobody has named yet counts as a change
    return ["safe", "change", "high"][rank];
  }
  const lastName = (p) => String(p || "").split(/[\\/]/).filter(Boolean).pop() || "your workspace";
  function folderName(p) {  // the folder a file is in, as the last two names: "Documents/Reports"
    const parts = String(p || "").split(/[\\/]/).filter(Boolean);
    if (parts.length <= 1) return parts[0] || "your workspace";
    parts.pop();
    return parts.slice(-2).join("/");
  }
  /* What the steps mean, in everyday words: "Delete 3 files in Documents/Reports". One line per kind of action. */
  function plainPlan(steps) {
    const groups = new Map();
    for (const s of steps) { if (!groups.has(s.action)) groups.set(s.action, []); groups.get(s.action).push(s); }
    const lines = [];
    for (const [action, list] of groups) {
      const n = list.length, one = n === 1;
      const same = list.every((s) => folderName(s.path) === folderName(list[0].path));
      const where = same ? folderName(list[0].path) : "several folders";
      const things = one ? lastName(list[0].path) : `${n} files`;
      const verb = { read: "Read", write: "Change", delete: "Delete", move: "Move", copy: "Copy", deploy: "Deploy" }[action];
      if (verb) lines.push(`${verb} ${things} in ${where}`);
      else if (action === "send") lines.push(`Send ${things} to this phone`);
      else if (action === "run") lines.push(one ? "Run a command on this PC" : `Run ${n} commands on this PC`);
      else if (action === "power") lines.push("Change this PC's power state");
      else if (action === "app") lines.push(one ? "Open an app on this PC" : `Open ${n} apps on this PC`);
      else lines.push(`${action} (${n})`);
    }
    return lines;
  }

  function planCard(j, readonly) {
    const state = j.status;
    const card = h("div", { class: "plan", "data-state": state === "done" ? "done" : "wait" });
    const title = readonly ? "What I did" : "Needs your approval";
    card.append(h("div", { class: "plan-title" }, ico(readonly ? "check" : "alert"), title));
    if (!readonly && j.steps.length) {  // in plain words first, coloured by how much it can matter; the exact steps stay below
      const risk = planRisk(j.steps);
      card.dataset.risk = risk;
      card.append(h("div", { class: "risk-head" }, h("span", { class: "risk-chip", "data-risk": risk, text: RISK_TEXT[risk] }),
        h("ul", { class: "plain" }, plainPlan(j.steps).map((t) => h("li", { text: t })))));
    }
    if (j.summary) card.append(h("p", { class: "note", text: j.summary })); // e.g. "Read actionsClient.ts (you typed actionClient.ts)"
    const ul = h("ul", { class: "steps" });
    for (const s of j.steps) {
      const isRun = s.action === "run";
      const detail = s.detail || "";
      const cmd = isRun ? (detail.endsWith(NO_UNDO) ? detail.slice(0, -NO_UNDO.length) : detail) : "";
      ul.append(h("li", { class: "step" }, h("span", { class: "step-badge", "data-a": s.action, text: STEP_LABEL[s.action] || s.action }),
        h("div", { class: "step-path" }, isRun ? h("span", { text: s.path === "." ? "in your workspace" : "in " + s.path }) : h("span", { text: s.action === "power" || s.action === "app" ? "This PC" : s.path === "." ? "Your workspace folder" : s.path }),
          isRun ? h("code", { class: "cmd", text: cmd }) : (detail ? h("span", { class: "step-detail", text: detail }) : null),
          s.action === "send" && s.size != null ? h("span", { class: "step-size" }, ico("download"), s.kind === "folder" ? `Folder, ${s.files}${s.partial ? "+" : ""} file${s.files === 1 ? "" : "s"}, ${fmtSize(s.size)}${s.partial ? "+" : ""} (sent as one zip)` : fmtSize(s.size)) : null,
          s.edit ? h("div", { class: "minidiff" }, h("div", { class: "del", text: "− " + s.edit.old }), h("div", { class: "add", text: "+ " + s.edit.new })) : null)));
    }
    card.append(ul);
    const acts = new Set(j.steps.map((s) => s.action));
    const pw = j.steps.find((s) => s.action === "power");
    const appStep = j.steps.find((s) => s.action === "app" && s.warning);
    if (!readonly && pw) card.append(h("div", { class: "warn-line" }, ico("alert"), pw.warning || "This changes the power state of the PC."));
    else if (!readonly && appStep) card.append(h("div", { class: "warn-line" }, ico("alert"), appStep.warning));
    else if (!readonly && acts.has("run")) card.append(h("div", { class: "warn-line" }, ico("alert"), "A command can't be undone. Read all of it before you approve. The safety filter is a speed bump, not a sandbox."));
    else if (!readonly && acts.has("send")) card.append(h("div", { class: "warn-line" }, ico("info"), "A copy will be sent to this phone. Any size works; large ones take a while."));
    if (!readonly && Access && Access.isViewer()) {
      card.append(h("p", { class: "note viewer-note" }, ico("eye"), "You have view-only access, so you can read this plan but not approve it."));
      return card;
    }
    if (!readonly) {
      const bio = Lock.approveBio(), confirmWith = Lock.approveOn();   // an app lock with "Confirm approvals" on: Approve asks for the fingerprint (or the PIN)
      const yes = h("button", { class: "btn primary approve" + (confirmWith ? " secure" : ""), type: "button" }, ico(bio ? "scan" : confirmWith ? "lock" : "check"), bio ? "Approve with fingerprint" : confirmWith ? "Approve with PIN" : "Approve");
      const no = h("button", { class: "btn ghost", type: "button" }, "Reject");
      const needsPin = j.steps.some((s) => s.needs_pin);
      const what = shorten(j.summary || j.prompt || "this request", 70);
      const seal = () => { card.classList.add("sealing"); setTimeout(() => card.classList.remove("sealing"), 1000); };   // the card turns green and lifts away
      if (needsPin) {  // unlocking the PC: the approval also needs the unlock PIN, typed here every time and never kept
        const pin = h("input", { class: "text-input pin-input", type: "password", autocomplete: "off", autocapitalize: "off", spellcheck: "false", maxlength: "32", placeholder: "Unlock PIN", "aria-label": "Unlock PIN" });
        const err = h("p", { class: "pin-err", role: "alert", hidden: true });
        const ready = () => { yes.disabled = pin.value.length < 6; };
        ready();
        pin.addEventListener("input", () => { err.hidden = true; ready(); });
        pin.addEventListener("keydown", (e) => { if (e.key === "Enter" && !yes.disabled) { e.preventDefault(); yes.click(); } });
        card.append(h("label", { class: "pin-field" }, h("span", { text: "Type your unlock PIN to allow this" }), pin), err);
        yes.onclick = async () => {
          const value = pin.value;
          if (!(await Lock.confirm(what))) return;
          seal();
          act(j.id, "approve", [yes, no], { pin: value }, (message) => { err.textContent = message; err.hidden = false; pin.value = ""; ready(); pin.focus(); });
          pin.value = "";
        };
      } else yes.onclick = async () => { if (!(await Lock.confirm(what))) return; seal(); act(j.id, "approve", [yes, no]); };
      no.onclick = () => act(j.id, "reject", [yes, no]);
      card.append(h("div", { class: "actions" }, yes, no));
    }
    return card;
  }

  function liveBot(t) {
    const j = t.job, st = j.status, busy = BUSY.has(st);
    const main = h("div", { class: "bot-main" });
    main.append(h("div", { class: "bot-head" }, h("span", { class: "who", text: "Access" }),
      h("span", { class: "chip " + st }, LABEL[st] || st), h("span", { class: "stamp", text: fmtTime(j.created_at) })));
    if (busy) {
      const tr = h("div", { class: "trace", role: "status" });
      (j.activity || []).forEach((a) => tr.append(h("div", { class: "tr-row" }, h("span", { class: "tr-ok", text: "✓" }), h("span", { text: a }))));
      tr.append(h("div", { class: "tr-row now" }, h("span", { class: "spin" }), h("span", { class: "shimmer", text: st === "running" ? "Running on your PC…" : "Thinking…" })));
      main.append(tr);
      main.append(h("div", { class: "actions" }, h("button", { class: "btn sm danger", type: "button", onclick: () => stopJob(j.id) }, ico("stop"), st === "running" ? "Stop the command" : "Stop")));
    } else (j.activity || []).forEach((a) => main.append(h("p", { class: "note", text: "Looked at: " + a })));

    if (j.summary && j.steps.length === 0 && !isReply(j)) main.append(h("p", { class: "note", text: j.summary }));
    if (j.open && st === "done") {
      const view = j.open_mode === "view", gallery = j.open_mode === "gallery", folder = j.open_mode === "folder";
      main.append(h("div", { class: "actions" },
        h("button", { class: "btn primary", type: "button", onclick: () => { haptic("light"); if (folder) openFolder(j.open); else if (gallery) openGallery(j.open); else openFile(j.open, j.open_mode || "edit"); } },
          ico(folder ? "folder" : gallery ? "grid" : view ? "expand" : "pencil"), folder ? "Browse this folder" : gallery ? "Open the pictures" : view ? "Open full screen" : "Open in the editor")));
    }
    if (st === "needs_access") main.append(accessCard(j));
    if (st === "awaiting_approval") main.append(planCard(j, false));
    else if (j.steps.length) main.append(planCard(j, true));

    for (const o of j.outputs) {
      if (isReply(j)) main.append(h("div", { class: "answer" + (S.mine.has(j.id) && S.fresh.has(j.id) ? " reveal" : "") }, markdownNodes(o)));
      else {
        const copy = h("button", { class: "iconbtn out-copy", type: "button", "aria-label": "Copy", onclick: () => copyText(o) }, ico("copy"));
        main.append(h("div", { class: "out-wrap" }, h("pre", { class: "out", text: o }), copy));
        const only = j.steps.length === 1 ? j.steps[0] : null;
        if (only && only.action === "read" && !only.detail.startsWith("find") && /\.[A-Za-z0-9]{1,8}$/.test(only.path) && st === "done") {
          main.append(h("div", { class: "actions" },
            h("button", { class: "btn sm", type: "button", onclick: () => { haptic("light"); openFile(only.path, "view"); } }, ico("expand"), "Open full screen"),
            h("button", { class: "btn sm", type: "button", onclick: () => { haptic("light"); openFile(only.path, "edit"); } }, ico("pencil"), "Edit")));
        }
      }
    }
    if (j.error) main.append(h("p", { class: "err", text: j.error }));

    const acts = h("div", { class: "actions" });
    if (st === "stopped" && (j.changed || []).length) {
      const u = h("button", { class: "btn sm ghost", type: "button" }, ico("undo"), "Undo what it did so far");
      u.onclick = () => act(j.id, "undo", [u]);
      acts.append(u);
    }
    if (st === "done") {
      const files = h("div", { class: "files" });
      for (const p of j.files || []) files.append(h("button", { class: "file-chip", type: "button", onclick: () => download(p) }, ico("download"), h("span", { class: "n", text: "Download " + p.split(/[\\/]/).pop() })));
      for (const p of j.changed || []) {
        files.append(h("button", { class: "file-chip", type: "button", onclick: () => download(p) }, ico("download"), h("span", { class: "n", text: "Download " + p.split(/[\\/]/).pop() })));
        files.append(h("button", { class: "file-chip", type: "button", onclick: () => { haptic("light"); openFile(p, "edit"); } }, ico("pencil"), h("span", { class: "n", text: "Edit " + p.split(/[\\/]/).pop() })));
      }
      if (files.children.length) main.append(files);
      if ((j.changed || []).length) {
        const u = h("button", { class: "btn sm ghost", type: "button" }, ico("undo"), "Undo");
        u.onclick = () => act(j.id, "undo", [u]);
        acts.append(u);
      }
    }
    if (["failed", "expired", "rejected", "stopped"].includes(st)) acts.append(h("button", { class: "btn sm", type: "button", onclick: () => { haptic("light"); send(j.prompt); } }, ico("retry"), "Try again"));
    if (acts.children.length) main.append(acts);
    if ((j.changed || []).length && ["done", "stopped"].includes(st)) main.append(h("p", { class: "note small undo-note", text: "Undo puts changed files back as they were. It can't take back a command, a file already sent to your phone, or an app that was opened." }));
    const tr = trail(t.id, t.events);
    if (tr) main.append(tr);
    return h("div", { class: "bot" }, h("span", { class: "bot-mark" }, glyph(26, true)), main);
  }

  function oldStatus(events) {
    const names = events.map((e) => e.event);
    for (const n of ["undo", "failed", "rejected", "expired", "done", "replied"]) if (names.includes(n)) return n === "undo" ? "undone" : n === "replied" ? "done" : n;
    return null;
  }
  function oldBot(t) {
    const st = oldStatus(t.events), main = h("div", { class: "bot-main" });
    main.append(h("div", { class: "bot-head" }, h("span", { class: "who", text: "Access" }), h("span", { class: "chip " + (st || "expired") }, st ? LABEL[st] : "Not finished"), h("span", { class: "stamp", text: "earlier session" })));
    const failed = t.events.find((e) => e.event === "failed");
    if (failed && failed.error) main.append(h("p", { class: "err", text: failed.error }));
    main.append(h("p", { class: "note", text: "Saved from the audit log. The reply text isn't kept after a restart." }));
    const tr = trail(t.id, t.events);
    if (tr) main.append(tr);
    return h("div", { class: "bot" }, h("span", { class: "bot-mark" }, glyph(26, true)), main);
  }

  function turnEl(t, enter) {
    return h("article", { class: "turn" + (enter ? " enter" : ""), "data-id": t.id },
      h("div", { class: "you" }, t.prompt, h("span", { class: "when", text: fmtTime(t.time) })),
      t.job ? liveBot(t) : oldBot(t));
  }

  function greeting() {
    const hr = new Date().getHours();
    const part = hr < 5 ? "Hello" : hr < 12 ? "Good morning" : hr < 17 ? "Good afternoon" : "Good evening";
    const first = displayName().split(/\s+/)[0];
    return first && first !== "You" ? `${part}, ${first}` : part;
  }
  function suggestions() {
    const ro = S.info && S.info.folders.read_only[0];
    const name = (p) => p.split(/[\\/]/).filter(Boolean).pop();
    const same = (t) => ({ label: t, text: t });
    const out = [same("What files are in my workspace?")];
    if (ro) out.push({ label: `Find .bak files in ${name(ro)}`, text: `Find .bak files in ${ro}` });
    out.push(same("Create notes.txt with buy milk"));
    if (S.info && S.info.commands_enabled) out.push(same("Run git status"));
    out.push(same("What can you do?"));
    return out;
  }
  function heroEl() {
    const sug = h("div", { class: "suggest" });
    suggestions().forEach(([icon, label, text], i) => sug.append(h("button", { class: "sug", type: "button", vars: { "--d": i * 0.04 + "s" }, onclick: () => { haptic("light"); send(text); } }, ico(icon), label)));
    const orb = h("div", { class: "orb-wrap" }, orbSVG());
    return h("div", { class: "hero" }, orb, h("h2", { text: greeting() }),
      h("p", { text: "Ask your PC to find, read, move or send files. I'll show a plan and wait for your tap before changing anything." }), sug);
  }

  function skeletonThread() {
    const lines = (...w) => w.map((c) => h("i", { class: "sk line " + c }));
    return h("div", { class: "skel-thread", "aria-hidden": "true" },
      h("div", { class: "sk-me" }, ...lines("", "w60")),
      h("div", { class: "sk-ai" }, h("i", { class: "sk dotc" }), h("div", {}, ...lines("", "w80", "w40"))),
      h("div", { class: "sk-me" }, ...lines("w80")),
      h("div", { class: "sk-ai" }, h("i", { class: "sk dotc" }), h("div", {}, ...lines("", "w60"))));
  }

  function renderThread() {
    const thread = $("#thread"), conv = S.model.byId.get(S.conv);
    if (S.threadConv !== S.conv) {
      thread.replaceChildren(); S.nodes.clear(); S.hero = null; S.threadConv = S.conv; S.firstPaint = true; S.stick = true;
      thread.classList.remove("swap"); void thread.offsetWidth; thread.classList.add("swap");
    }
    // Until the PC has answered the first time, show the shape of a chat instead of an empty or wrong-looking one.
    if (!S.loaded && S.token) {
      if (!S.skelThread) { S.hero = null; S.nodes.clear(); S.skelThread = skeletonThread(); thread.replaceChildren(S.skelThread); }
      return;
    }
    if (S.skelThread) { S.skelThread = null; S.hero = null; thread.replaceChildren(); }
    const held = Q ? Q.items().filter((i) => i.conv === S.conv) : [];
    if ((!conv || !conv.turns.length) && !held.length) {
      if (!S.hero || S.hero.sig !== greeting() + (S.info ? S.info.pc : "")) {
        S.hero = { el: heroEl(), sig: greeting() + (S.info ? S.info.pc : "") };
        thread.replaceChildren(S.hero.el);
      }
      return;
    }
    if (S.hero) { S.hero = null; thread.replaceChildren(); }

    const items = (conv ? conv.turns : []).map((t) => ({ key: t.id, time: t.time, turn: t }))
      .concat((conv ? conv.notes : []).map((n) => ({ key: "n:" + n.time + n.path, time: n.time, note: n })))
      .concat(held.map((i) => ({ key: "q:" + i.id, time: new Date(i.created).toISOString(), held: i })));  // waiting on this phone: always after what the PC already knows
    items.sort((a, b) => (a.held ? 1 : 0) - (b.held ? 1 : 0) || new Date(a.time) - new Date(b.time));
    const keep = new Set(items.map((i) => i.key));
    for (const [key, rec] of S.nodes) if (!keep.has(key)) { rec.el.remove(); S.nodes.delete(key); }
    items.forEach((item, index) => {
      let rec = S.nodes.get(item.key);
      const sig = item.turn ? JSON.stringify([item.turn.job, item.turn.events.length, S.openTrails.has(item.key), cfg.openTrails, S.mine.has(item.key)])
        : item.held ? JSON.stringify([item.held, !!(S.away || S.offline || pcUnreachable()), item.held.state === "queued" && S.jobs.length && heldBehind(item.held), Q.active().filter((i) => i.conv === S.conv).length]) : "";
      if (!rec) {
        const el = item.held ? queuedEl(item.held) : item.turn ? turnEl(item.turn, !S.firstPaint) : h("div", { class: "sys", text: `${fmtTime(item.note.time)} · ${noteText(item.note)}` });
        rec = { el, sig }; S.nodes.set(item.key, rec);
      } else if (rec.sig !== sig && (item.turn || item.held)) {
        const el = item.held ? queuedEl(item.held) : turnEl(item.turn, false); rec.el.replaceWith(el); rec.el = el; rec.sig = sig;
      }
      if (thread.children[index] !== rec.el) thread.insertBefore(rec.el, thread.children[index] || null);
    });
    S.firstPaint = false;
  }

  /* scrolling: follow new messages while you are at the bottom, otherwise offer a way back down */
  function atBottom() { const s = $("#scroller"); return s.scrollHeight - s.scrollTop - s.clientHeight < 140; }
  function syncScroll() {
    const s = $("#scroller");
    if (S.stick) s.scrollTop = s.scrollHeight;
    $("#jump").hidden = atBottom() || !S.model.byId.get(S.conv);
  }

  /* ===================================================================== dock: typing and voice */

  const LANGS = [
    ["en-IN", "English (India)", "English"], ["en-US", "English (US)", "English"], ["en-GB", "English (UK)", "English"],
    ["hi-IN", "हिन्दी", "Hindi"], ["ur-IN", "اردو", "Urdu"], ["kn-IN", "ಕನ್ನಡ", "Kannada"], ["ta-IN", "தமிழ்", "Tamil"], ["te-IN", "తెలుగు", "Telugu"],
    ["ml-IN", "മലയാളം", "Malayalam"], ["mr-IN", "मराठी", "Marathi"], ["bn-IN", "বাংলা", "Bengali"], ["gu-IN", "ગુજરાતી", "Gujarati"], ["pa-IN", "ਪੰਜਾਬੀ", "Punjabi"],
    ["ar-SA", "العربية", "Arabic"], ["es-ES", "Español", "Spanish"], ["fr-FR", "Français", "French"], ["de-DE", "Deutsch", "German"], ["pt-BR", "Português", "Portuguese"],
    ["it-IT", "Italiano", "Italian"], ["ru-RU", "Русский", "Russian"], ["ja-JP", "日本語", "Japanese"], ["ko-KR", "한국어", "Korean"], ["zh-CN", "中文", "Chinese"], ["id-ID", "Bahasa Indonesia", "Indonesian"],
  ];
  const langInfo = (code) => LANGS.find((l) => l[0] === code) || LANGS[0];

  const Speech = (() => {
    const Ctor = window.SpeechRecognition || window.webkitSpeechRecognition;
    const MAX_MS = 60000;
    let rec = null, want = false, base = "", cur = "", cb = {}, ended = null, began = 0;
    const text = () => (base + " " + cur).replace(/\s+/g, " ").trim();
    function status() { return !Ctor ? "unsupported" : !window.isSecureContext ? "insecure" : "ok"; }
    function build(lang) {
      const r = new Ctor();
      r.lang = lang; r.continuous = true; r.interimResults = true; r.maxAlternatives = 1;
      r.onstart = () => cb.started && cb.started();
      r.onresult = (e) => {
        let t = "";
        for (let i = 0; i < e.results.length; i++) t += e.results[i][0].transcript + " ";
        cur = t.trim(); cb.text && cb.text(text());
      };
      r.onerror = (e) => {
        const err = e.error || "error";
        if (["not-allowed", "service-not-allowed", "audio-capture", "language-not-supported"].includes(err)) want = false;
        cb.error && cb.error(err);
      };
      r.onend = () => {
        if (want && Date.now() - began < MAX_MS) { base = text(); cur = ""; try { r.start(); return; } catch { /* fall through */ } }
        want = false;
        const f = ended; ended = null; if (f) f(text());
      };
      return r;
    }
    return {
      status,
      start(lang, callbacks) {
        if (status() !== "ok") return status();
        want = true; base = ""; cur = ""; cb = callbacks; began = Date.now();
        try { rec = build(lang); rec.start(); } catch { want = false; return "error"; }
        return "ok";
      },
      stop() {
        return new Promise((resolve) => {
          want = false; ended = resolve;
          try { rec && rec.stop(); } catch { /* already stopped */ }
          setTimeout(() => { if (ended) { const f = ended; ended = null; f(text()); } }, 1800);
        });
      },
      abort() { want = false; ended = null; try { rec && rec.abort(); } catch { /* already stopped */ } },
    };
  })();

  const V = { phase: "idle", held: false, tap: false, cancel: false, seconds: 0, timer: 0, ptr: null, text: "", ready: false };
  const D = {}; // the dock's live elements

  function chooseMode() {
    const ok = Speech.status() === "ok";
    const coarse = window.matchMedia && matchMedia("(pointer: coarse)").matches;
    S.mode = cfg.input === "voice" ? "voice" : cfg.input === "type" ? "type" : ok && coarse ? "voice" : "type";
  }
  function setMode(mode) { S.mode = mode; haptic("tick"); renderDock(); }

  function renderDock() {
    const dock = $("#dock");
    if (S.draftConv !== undefined && D.field) S.drafts[S.draftConv] = D.field.value;
    S.draftConv = S.conv;
    dock.replaceChildren();
    for (const k of Object.keys(D)) delete D[k];
    const inner = h("div", { class: "dock-inner" });
    dock.append(inner);
    if (S.offline && !Q) { renderOfflineComposer(inner); return; }  // (with the queue, the box stays usable: what is typed is held until the PC answers)
    if (S.mode === "voice") renderVoicePanel(inner); else renderComposer(inner);
    paintVoice();
  }

  /* Reading saved chats: the message box is there, but switched off, and says why. */
  function renderOfflineComposer(inner) {
    const field = h("textarea", { class: "field", rows: "1", disabled: "", placeholder: "Your PC is offline. Saved chats are read only.", "aria-label": "Message (switched off: your PC is offline)" });
    Object.assign(D, { field, sync() {}, syncSend() {} });
    inner.append(h("div", { class: "composer off" }, field));
  }

  function composerHint() { return Q && (S.away || S.offline || pcUnreachable()) ? "Message your PC · it will be queued" : "Message your PC…"; }

  function renderComposer(inner) {
    const field = h("textarea", { class: "field", rows: "1", placeholder: composerHint(), "aria-label": "Message", enterkeyhint: "send", autocapitalize: "sentences" });
    field.value = S.drafts[S.conv] || "";
    const sendBtn = h("button", { class: "round send", type: "button", "aria-label": "Send", disabled: true }, ico("arrowUp"));
    const mic = h("button", { class: "round micbtn", type: "button", "aria-label": "Speak instead of typing", onclick: () => { if (Speech.status() !== "ok") { voiceHelp(Speech.status()); return; } cfg.input = "voice"; saveCfg(); setMode("voice"); } }, ico("mic"));
    let stopping = false;
    const syncSend = () => {
      const empty = field.value.trim().length === 0, busy = busyJobs().length > 0;
      const stop = busy && empty;  // working, and nothing typed: the button stops the work instead
      if (stop !== stopping) { stopping = stop; sendBtn.replaceChildren(ico(stop ? "stop" : "arrowUp")); }
      sendBtn.classList.toggle("stop", stop);
      sendBtn.setAttribute("aria-label", stop ? "Stop" : "Send");
      sendBtn.disabled = empty && !busy;
      sendBtn.classList.toggle("ready", !stop && !empty);
    };
    const sync = () => {
      field.style.height = "auto"; field.style.height = Math.min(field.scrollHeight, 168) + "px";
      syncSend();
      if (D.chips) D.chips.hidden = field.value.trim().length > 0 || !D.chips.children.length;  // suggestions only while the box is empty
    };
    const go = () => {
      if (stopping) { stopBusy(); return; }
      const t = field.value; if (!t.trim()) return; field.value = ""; S.drafts[S.conv] = ""; sync(); send(t);
    };
    field.addEventListener("input", sync);
    field.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing && matchMedia("(hover: hover)").matches) { e.preventDefault(); go(); } });
    sendBtn.onclick = go;
    const chips = h("div", { class: "sugg-row", role: "group", "aria-label": "Suggestions" });
    Object.assign(D, { field, sync, syncSend, chips });
    paintChips();
    inner.append(chips, h("div", { class: "composer" }, mic, field, sendBtn), h("p", { class: "dock-hint only-desk", text: "Enter to send · Shift+Enter for a new line · / to focus" }));
    sync();
  }

  function renderVoicePanel(inner) {
    const preview = h("div", { class: "v-preview", "aria-live": "off" });
    const feed = h("div", { class: "v-feed", role: "status", "aria-live": "polite" });
    const icon = h("span", { class: "hold-icon" });
    const label = h("span", { class: "hold-label" });
    const hold = h("button", { class: "hold", type: "button", "aria-describedby": "vhelp" }, h("span", { class: "hold-halo" }), icon, label);
    const kb = h("button", { class: "v-sec", type: "button", "aria-label": "Type a message", onclick: () => { cfg.input = "type"; saveCfg(); setMode("type"); setTimeout(() => D.field && D.field.focus(), 40); } }, ico("keyboard"));
    const cancel = h("button", { class: "v-sec", type: "button", "aria-label": "Cancel voice message", disabled: true, onclick: () => voiceEnd(true) }, ico("x"));
    const helpText = h("span");
    const modeBtn = h("button", { type: "button", onclick: () => { cfg.hold = cfg.hold === "hold" ? "tap" : "hold"; saveCfg(); haptic("tick"); paintVoice(); } });
    const panel = h("div", { class: "voice", "data-active": "false", "data-cancel": "false", "data-phase": "idle" }, preview, feed,
      h("div", { class: "v-controls" }, kb, hold, cancel), h("div", { class: "v-help", id: "vhelp" }, helpText, modeBtn));
    bindHold(hold);
    Object.assign(D, { panel, preview, feed, icon, label, hold, cancel, helpText, modeBtn });
    inner.append(panel);
  }

  function bindHold(hold) {
    hold.addEventListener("contextmenu", (e) => e.preventDefault());
    hold.addEventListener("pointerdown", (e) => {
      if (cfg.hold === "tap" || e.button !== 0 || V.ptr || V.tap || V.phase === "transcribing") return;
      e.preventDefault();
      V.ptr = { id: e.pointerId, x: e.clientX };
      try { hold.setPointerCapture(e.pointerId); } catch { V.ptr = null; return; }
      V.held = true; voiceBegin();
    });
    hold.addEventListener("pointermove", (e) => {
      if (!V.ptr || V.ptr.id !== e.pointerId) return;
      const cancel = e.clientX < V.ptr.x - 72;
      if (cancel !== V.cancel) { V.cancel = cancel; haptic("tick"); paintVoice(); }
    });
    const up = (aborted) => (e) => {
      if (!V.ptr || V.ptr.id !== e.pointerId) return;
      V.ptr = null; V.held = false;
      try { hold.releasePointerCapture(e.pointerId); } catch { /* already released */ }
      voiceEnd(aborted || V.cancel);
    };
    hold.addEventListener("pointerup", up(false));
    hold.addEventListener("pointercancel", up(true));
    hold.addEventListener("click", (e) => {
      if (cfg.hold !== "tap" && e.detail !== 0) return;
      if (V.phase === "transcribing") return;
      if (V.tap) { V.tap = false; voiceEnd(false); } else { V.tap = true; voiceBegin(); }
    });
    hold.addEventListener("keydown", (e) => { if (e.key === "Escape" && V.phase !== "idle") { e.stopPropagation(); voiceEnd(true); } });
  }

  async function voiceBegin() {
    const status = Speech.status();
    if (status !== "ok") { voiceReset(); voiceHelp(status); return; }
    stopSpeaking();
    V.phase = "starting"; V.cancel = false; V.seconds = 0; V.text = ""; V.ready = false;
    haptic("medium"); paintVoice();
    const result = Speech.start(cfg.lang, {
      started() { if (V.phase === "starting") { V.phase = "listening"; V.ready = true; haptic("tick"); startLevel(); paintVoice(); } },
      text(t) { V.text = t; paintVoice(); },
      error: voiceError,
    });
    if (result !== "ok") { voiceReset(); toast("Couldn't start the microphone."); return; }
    V.timer = setInterval(() => { V.seconds += 1; if (V.seconds >= 60) { V.held = false; V.tap = false; voiceEnd(false); } else paintVoice(); }, 1000);
  }

  function voiceError(err) {
    if (err === "no-speech" || err === "aborted") return;
    if (err === "not-allowed" || err === "service-not-allowed") { voiceReset(); voiceHelp("denied"); }
    else if (err === "network") { voiceReset(); toast("Voice needs the internet: your browser's speech service couldn't be reached."); haptic("error"); }
    else if (err === "audio-capture") { voiceReset(); toast("No microphone was found."); haptic("error"); }
    else if (err === "language-not-supported") { voiceReset(); toast("This browser can't listen in " + langInfo(cfg.lang)[2] + ". Pick another language in Settings."); }
  }

  async function voiceEnd(cancelled) {
    if (V.phase === "idle") return;
    clearInterval(V.timer); stopLevel();
    V.held = false; V.tap = false;
    if (cancelled) { Speech.abort(); haptic("warning"); voiceReset(); return; }
    V.phase = "transcribing"; paintVoice();
    const text = await Speech.stop();
    voiceReset();
    if (!text) { toast("I didn't catch that. Try again."); haptic("warning"); return; }
    haptic("success");
    if (cfg.autoSend) send(text, { voice: true });
    else { setMode("type"); if (D.field) { D.field.value = text; D.sync(); D.field.focus(); } }
  }

  function voiceReset() {
    clearInterval(V.timer); stopLevel();
    Object.assign(V, { phase: "idle", held: false, tap: false, cancel: false, seconds: 0, ptr: null, text: "" });
    paintVoice();
  }

  function paintVoice() {
    paintPhase();
    if (!D.panel) return;
    const active = V.held || V.tap, draining = V.phase === "transcribing" && !active;
    D.panel.dataset.active = String(active || V.phase === "transcribing");
    D.panel.dataset.cancel = String(V.cancel);
    D.panel.dataset.phase = V.phase;
    const tapMode = cfg.hold === "tap";
    const label = V.cancel ? "Release to cancel" : V.phase === "starting" ? (V.ready ? "Reconnecting mic" : "Getting microphone ready")
      : draining ? "Finishing your words" : active ? (V.tap ? "Tap to ask" : "Release to ask") : V.phase === "speaking" ? "Hold to interrupt" : tapMode ? "Tap to talk" : "Hold to talk";
    if (D.label.textContent !== label) { D.label.textContent = label; D.label.style.animation = "none"; void D.label.offsetWidth; D.label.style.animation = ""; }
    D.icon.replaceChildren(ico(V.cancel ? "x" : V.tap ? "stop" : "mic"));
    D.preview.textContent = V.text || (active ? "Your words will appear here" : "A question, a task, your next step. Just say it.");
    D.cancel.disabled = !(active || V.phase === "transcribing");
    D.hold.disabled = draining;
    if (active) {
      const mm = Math.floor(V.seconds / 60) + ":" + pad(V.seconds % 60);
      const wave = h("span", { class: "v-wave", "aria-hidden": "true" });
      for (let i = 0; i < 13; i++) wave.append(h("i", { vars: { "--i": i } }));
      const hint = V.cancel ? "Let go to discard" : V.seconds >= 50 ? `${60 - V.seconds}s left` : V.phase === "starting" ? "Keep holding" : V.tap ? "Tap again when finished" : null;
      D.feed.replaceChildren(h("span", { class: "v-clock", text: mm }), wave, h("span", {}, hint ? hint : [ico("arrowLeft"), " Slide left to cancel"]));
    } else D.feed.replaceChildren(h("span", { text: draining ? "Keeping the last words, then asking" : tapMode ? "Tap. Speak. Tap." : "Hold. Speak. Release." }));
    D.helpText.textContent = tapMode ? "Tap to start. Tap again to ask." : `Release sends your words in ${langInfo(cfg.lang)[2]}.`;
    D.modeBtn.textContent = tapMode ? "Use hold controls" : "Use tap controls";
    D.modeBtn.disabled = active || draining;
  }

  /* the orb's decorative activity while listening (not the real microphone level) */
  let levelFrame = 0;
  function startLevel() {
    cancelAnimationFrame(levelFrame);
    const t0 = performance.now(); let level = 0, written = -1, last = 0;
    const tick = (now) => {
      if (now - last >= 32) {
        last = now; const t = (now - t0) / 1000;
        const goal = Math.max(0, 0.35 + 0.25 * Math.sin(t * 5.1) + 0.15 * Math.sin(t * 9.7 + 1));
        level += (goal - level) * 0.25;
        if (Math.abs(level - written) >= 0.012) { written = level; document.body.style.setProperty("--lvl", level.toFixed(3)); }
      }
      levelFrame = requestAnimationFrame(tick);
    };
    levelFrame = requestAnimationFrame(tick);
  }
  function stopLevel() { cancelAnimationFrame(levelFrame); document.body.style.setProperty("--lvl", "0"); }

  /* a soft tick in time with the idle orb, like turning a dial; off unless you ask for it */
  let tickTimer = 0;
  function syncTicks() {
    const want = cfg.ticks && cfg.haptics && !document.hidden && S.hero && V.phase === "idle" && !matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (want && !tickTimer) { let n = 0; tickTimer = setInterval(() => { if (++n * 1100 > 90000 || document.hidden) { clearInterval(tickTimer); tickTimer = 0; return; } haptic("tick"); }, 1100); }
    if (!want && tickTimer) { clearInterval(tickTimer); tickTimer = 0; }
  }

  /* stopping */
  const busyJobs = () => S.jobs.filter((j) => j.conversation === S.conv && BUSY.has(j.status));
  async function stopJob(id) {
    haptic("heavy");
    try {
      const job = await (await api(`/jobs/${id}/stop`, { method: "POST" })).json();
      S.jobs = S.jobs.map((j) => (j.id === id ? job : j));
      S.stick = true; update(true);
    } catch (e) { if (e.code !== 401) toast(e.message); }
    refresh();
  }
  async function stopBusy() { for (const j of busyJobs()) await stopJob(j.id); }

  /* spoken replies */
  function stopSpeaking() { try { window.speechSynthesis && speechSynthesis.cancel(); } catch { /* none */ } S.speaking = false; paintPhase(); }
  function speak(text) {
    if (S.muted || !("speechSynthesis" in window) || !text) return;
    try {
      speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(clip(text.replace(/\s+/g, " "), 420));
      u.lang = cfg.lang; u.rate = cfg.rate;
      u.onstart = () => { S.speaking = true; paintPhase(); };
      u.onend = u.onerror = () => { S.speaking = false; paintPhase(); };
      speechSynthesis.speak(u);
    } catch { /* speech unavailable */ }
  }
  function phaseNow() {
    if (["starting", "listening", "transcribing"].includes(V.phase)) return "listening";
    if (S.jobs.some((j) => BUSY.has(j.status) && j.conversation === S.conv)) return "thinking";
    return S.speaking ? "speaking" : "idle";
  }
  function paintPhase() { document.body.dataset.phase = phaseNow(); }

  /* ===================================================================== sending */

  S.fresh = new Set();
  async function send(text, { voice = false } = {}) {
    text = text.trim();
    if (!text) return;
    if (Screen && !S.offline && !S.away && !pcUnreachable() && Screen.wants(text)) { haptic("light"); Screen.open(); return; }  // "I want to access my PC screen": that opens the screen, it is not a request for the assistant
    if (S.offline || S.away || pcUnreachable()) { queueRequest(text); return; }  // the PC can't be reached: hold it on the phone, never lose it
    haptic("light");
    const file = S.focus[S.conv]; delete S.focus[S.conv]; // one message only: after that the chat's own history decides
    try {
      const job = await (await api("/jobs", { method: "POST", body: JSON.stringify({ prompt: text, conversation: S.conv, file }) })).json();
      S.mine.add(job.id); S.fresh.add(job.id); if (voice) S.voiceJobs.add(job.id);
      S.seen.set(job.id, "new");
      S.jobs = [job, ...S.jobs.filter((j) => j.id !== job.id)];
      S.stick = true; update(true);
    } catch (e) {
      if (e.code === 401) { /* signed out: the sign-in screen is showing */ }
      else if (e.data && e.data.code === "session_held") {  // someone else is using the PC: say who, offer to ask, and keep what was typed
        toast(e.message, Access ? { label: "Ask", run: () => Access.open() } : null); haptic("warning");
        if (D.field && !D.field.value) { D.field.value = text; D.sync(); }
      }
      else if (Q && (!e.status || e.status >= 500 || e.status === 423) && !e.offline) { Link.miss({ status: e.status || 0 }); queueRequest(text, { quiet: true }); }  // the PC dropped just as it was sent: held instead
      else { toast(e.message || "Couldn't send that."); haptic("error"); }
    }
    refresh();
  }

  /* ===================================================================== requests held while the PC is unreachable */

  S.away = false;   // "Keep chatting" was chosen: the popup is closed and a slim strip says the PC can't be reached
  S.qsent = new Set();  // jobs that came out of the queue, so later ones in the same chat wait their turn
  let flushing = false;

  function pcUnreachable() { return !!Link && (Link.state === "disconnected" || Link.state === "reconnecting"); }

  /* Hold a request on the phone. Asks once per visit (a short, calm question); after that it is a quiet "Queued" and a tick. */
  async function queueRequest(text, { quiet = false } = {}) {
    if (!Q) { toast("Your PC can't be reached, so that has to wait."); haptic("error"); return; }
    const bad = AfpQueue.neverQueue(text);
    if (bad) {
      await ask({ title: "This needs your PC", message: "For your safety, unlocking, locking, shutting down or restarting is never queued. Try again when your PC is back.", ok: "OK", cancel: "", icon: "lock" });
      if (D.field) { D.field.value = text; D.sync(); }  // what was typed is not lost
      return;
    }
    if (!quiet && !S.queueOk) {
      const yes = await ask({ title: "Your PC can't be reached", message: "Save this request on your phone and send it when your PC is back? Nothing will run yet.", ok: "Queue request", cancel: "Cancel", icon: "clock" });
      if (!yes) { if (D.field && !D.field.value) { D.field.value = text; D.sync(); } return; }  // cancelled: the text goes back in the box
      S.queueOk = true;
    }
    const file = S.focus[S.conv]; delete S.focus[S.conv];
    const item = Q.add({ text, conv: S.conv, file: file || null });
    if (!item) { toast("The queue is full. Cancel one first."); haptic("error"); return; }
    haptic("success"); S.stick = true;
    if (S.queueOk && !quiet) toast("Queued · sends when your PC is back");
  }

  function heldBehind(item) {
    return S.jobs.some((j) => S.qsent.has(j.id) && j.conversation === item.conv && (BUSY.has(j.status) || j.status === "awaiting_approval" || j.status === "needs_access"));
  }

  /* The PC answers again: hand the held requests over one at a time, in order. Each carries its own id, so a dropped connection
     in the middle can never make the PC run one twice. The PC then plans each from the files as they are NOW and asks for approval as usual. */
  async function flushQueue() {
    if (!Q || flushing || S.offline || !S.token || !Link || Link.state !== "connected") return;
    flushing = true;
    try {
      for (let item = Q.next(); item; item = Q.next()) {
        if (heldBehind(item)) break;
        Q.mark(item.id, "sending");
        try {
          const job = await (await api("/jobs", { method: "POST", body: JSON.stringify({ prompt: item.text, conversation: item.conv, file: item.file, client_id: item.id }) })).json();
          S.qsent.add(job.id); S.mine.add(job.id); S.fresh.add(job.id); S.seen.set(job.id, "new");
          S.jobs = [job, ...S.jobs.filter((j) => j.id !== job.id)];
          Q.remove(item.id);
        } catch (e) {
          if (e.code === 401) break;
          if (e.data && e.data.code === "session_held") { Q.mark(item.id, "queued"); break; }  // someone is using the PC: it stays queued and goes when the turn is free
          if (e.status && e.status < 500 && e.status !== 423) { Q.mark(item.id, "failed", e.message || "Your PC refused it."); continue; }
          Q.mark(item.id, "queued"); Link.miss({ status: e.status || 0 }); break;  // it dropped again: the rest keep waiting
        }
      }
    } finally { flushing = false; }
    refresh();
  }

  function queueTick() {
    if (!Q) return;
    if (Q.expire()) update(true);
    if (Q.active().length && !S.offline && Link && Link.state === "connected") flushQueue();
    if (S.model && (Q.items().length || S.queueShown)) { S.queueShown = Q.items().length > 0; update(true); }  // positions and "waiting" notes stay current
  }

  /* The slim strip under the header: amber while the PC can't be reached, green for a moment when it is back. */
  function paintAway(state) {
    const bar = $("#awayBar");
    if (!bar) return;
    clearTimeout(S.awayTimer);
    if (state === "back") {
      bar.hidden = false; bar.dataset.tone = "ok";
      bar.replaceChildren(h("span", { class: "away-ico", "aria-hidden": "true" }, ico("check")),
        h("span", { class: "away-text" }, h("b", { text: "PC is back" }), Q && Q.active().length ? h("span", { text: " · sending your requests…" }) : null));
      S.awayTimer = setTimeout(() => paintAway(), 4000);
      return;
    }
    if (!S.away || S.offline) { bar.hidden = true; bar.replaceChildren(); return; }
    bar.hidden = false; bar.dataset.tone = "warn";
    const n = Q ? Q.active().length : 0;
    bar.replaceChildren(h("span", { class: "away-ico", "aria-hidden": "true" }, ico("wifiOff")),
      h("span", { class: "away-text" }, h("b", { text: "PC unreachable" }), h("span", { text: n ? ` · ${n} request${n > 1 ? "s" : ""} queued` : " · Requests can be queued" })),
      h("button", { class: "away-more", type: "button", onclick: () => { haptic("tick"); S.away = false; paintAway(); if (Link.state !== "connected") showLost(); } }, "Details"));
  }
  function keepChatting() {
    haptic("tick");
    S.away = true;
    hideLost(); paintAway();
    if (S.model) renderDock();
    if (D.field) setTimeout(() => D.field.focus({ preventScroll: true }), 80);
  }

  /* A request waiting on this phone, shown as a normal message of yours with its own small status line. */
  function queuedEl(item) {
    const iso = new Date(item.created).toISOString();
    const state = item.state, mine = Q.active().filter((i) => i.conv === item.conv), place = mine.findIndex((i) => i.id === item.id);
    const behind = state === "queued" && S.jobs.length && heldBehind(item);
    const away = S.away || S.offline || pcUnreachable();
    const text = state === "sending" ? "Sending to your PC…"
      : state === "expired" ? "Expired · it waited too long"
      : state === "failed" ? (item.error || "Your PC couldn't take this")
      : behind ? "Waiting for your approval on the request above"
      : away ? "Queued on this phone · waiting for your PC" : "Queued · sending now";
    const acts = h("div", { class: "q-acts" });
    const btn = (label, icon, fn) => h("button", { class: "q-btn", type: "button", onclick: () => { haptic("light"); fn(); } }, ico(icon), label);
    if (state === "queued") acts.append(btn("Edit", "pencil", () => { if (Q.cancel(item.id)) editQueued(item.text); }), btn("Cancel", "x", () => Q.cancel(item.id)));
    else if (state === "expired" || state === "failed") acts.append(btn("Try again", "retry", () => { Q.retry(item.id); flushQueue(); }), btn("Remove", "x", () => Q.cancel(item.id)));
    const order = state === "queued" && mine.length > 1 && place >= 0 ? h("span", { class: "q-pos", text: `${place + 1} of ${mine.length}` }) : null;
    return h("article", { class: "turn queued enter", "data-id": "q:" + item.id },
      h("div", { class: "you" }, item.text, h("span", { class: "when", text: fmtTime(iso) })),
      h("div", { class: "q-state", "data-state": state }, h("span", { class: "q-ico", "aria-hidden": "true" }, state === "sending" ? h("span", { class: "spin" }) : ico(state === "failed" || state === "expired" ? "alert" : "clock")),
        h("span", { class: "q-text", text }), order, acts));
  }
  function editQueued(text) {
    S.drafts[S.conv] = text;
    if (S.mode !== "type") { S.mode = "type"; renderDock(); }
    if (D.field) { D.field.value = text; D.sync(); setTimeout(() => D.field.focus(), 40); }
  }

  /* When this PC has its own AI model, it names the chat you are in (once), better than the words alone can. Never for a hosted model:
     that would send it requests that otherwise never leave the PC. Your own rename always wins. Old chats keep the name made from their words. */
  async function nameChat() {
    const conv = S.conv;
    if (!conv || S.titles[conv] || S.autoTitles[conv] || S.titleAsked.has(conv) || S.offline) return;
    if (!S.info || !S.info.ai || S.info.ai.kind !== "local") return;
    const chat = S.model && S.model.byId.get(conv);
    if (!chat || !chat.turns.length) return;
    S.titleAsked.add(conv);  // once per visit, whatever the answer
    try {
      const reply = await (await api("/title", { method: "POST", body: JSON.stringify({ prompt: chat.turns[0].prompt }) })).json();
      const title = window.AfpTitles ? window.AfpTitles.tidy(reply.title) : reply.title;
      if (!title || S.titles[conv]) return;
      S.autoTitles[conv] = title; store.set("afp_autotitles", S.autoTitles);
      S.sideSig = ""; S.topSig = ""; update(true);
    } catch { /* the name made from the words stays */ }
  }

  /* what changed since the last poll: a tick, a tone, and (for voice requests) a spoken answer */
  function observe() {
    for (const j of S.jobs) {
      const before = S.seen.get(j.id);
      if (before === j.status) continue;
      S.seen.set(j.id, j.status);
      if (!S.loaded || before === undefined) continue;
      if (j.status === "awaiting_approval" || j.status === "needs_access") haptic("warning");
      else if (j.status === "done") haptic("success");
      else if (j.status === "failed" || j.status === "expired") haptic("error");
      if (j.status === "done" && S.mine.has(j.id) && (j.changed || []).length) {  // a change was just made: undo is one tap away, wherever you are
        const n = j.changed.length;
        toast(`Done · changed ${n} file${n === 1 ? "" : "s"}`, { label: "Undo", run: () => act(j.id, "undo", []) });
      }
      const spoken = S.voiceJobs.has(j.id) || (cfg.speak === "always" && S.mine.has(j.id));
      if (cfg.speak !== "off" && spoken && !S.spoken.has(j.id + j.status) && !BUSY.has(j.status) && j.status !== "new") {
        S.spoken.add(j.id + j.status);
        const line = j.status === "needs_access" ? "I need your permission to open a folder." : j.status === "awaiting_approval" ? "I need your approval. " + (j.summary || j.steps.map((s) => `${s.action} ${s.path}`).join(", "))
          : j.status === "done" ? (isReply(j) ? j.outputs[0] : j.summary || "Done.")
          : j.status === "failed" ? "That didn't work. " + (j.error || "") : "";
        if (line) speak(line);
      }
    }
  }

  /* ===================================================================== polling and rendering */

  async function refresh() {
    if (!S.token) return;
    if (S.offline) return;  // showing saved chats: nothing from the PC may replace them until the PC is back
    if (Link.state === "disconnected" || Link.state === "reconnecting") return;  // the popup's own checks handle it; its recovery calls this again
    try {
      const [jobs, audit] = await Promise.all([api("/jobs").then((r) => r.json()), api("/audit").then((r) => r.json())]);
      Link.ok();
      S.jobs = jobs; S.audit = audit;
      if (cfg.saveChats) Saved.queue(savedSource);  // a copy for reading when the PC is away (saved a moment later, and only if something changed)
      observe(); S.loaded = true; update();
      if (!S.resumeTried) { S.resumeTried = true; resumeScreen(); }   // once per start of the app
      nameChat();
      for (const j of jobs) if (!BUSY.has(j.status)) S.fresh.delete(j.id);
    } catch (e) {
      if (e.code === 401) return;
      Link.miss({ status: e.status || 0 });
    }
  }

  /* ===================================================================== losing the PC, and getting it back */

  // When to show the popup and when it may close is decided in conn.js (tested on its own). This is the page's part: the heartbeat,
  // working out WHY the PC can't be reached, and the popup that blocks the whole app until it can be.

  // Title and text for each reason. The first nine are the same words the phone app shows before the page has loaded
  // (tests/test_connection_messages.py keeps the two in step). The last five come from the PC itself, which told the phone first.
  const LOST = {
    phone_offline: ["This phone is offline", "Turn on Wi-Fi or mobile data. This goes away by itself as soon as there is a connection.", "wifiOff", { settings: true }],
    other_network: ["Not on your PC's network", "Your PC is on your home or office Wi-Fi, and this phone isn't. Connect to that Wi-Fi, or set up access from anywhere on the PC.", "wifiOff", { settings: true }],
    app_not_running: ["Your PC is on, but the app isn't running", "Open the app on your PC (Start menu or the tray icon). This goes away by itself.", "cpu", {}],
    pc_unreachable: ["Can't reach your PC", "It may be switched off, asleep, or have lost its Wi-Fi or internet, or this phone may be on a different Wi-Fi. This goes away by itself as soon as it answers.", "wifiOff", {}],
    pc_offline: ["Your PC is offline", "It is switched off, asleep, or has no internet connection. This goes away by itself as soon as it is back.", "wifiOff", {}],
    name_not_found: ["Can't find your PC's address", "Check this phone's internet connection. If that is fine, the PC may be offline. If its address changed, scan a new code to reconnect.", "wifiOff", {}],
    address_changed: ["Your PC has a new address", "It is on this Wi-Fi, but at a different address than before. Scan the code on its page to reconnect.", "wifiOff", { lead: "switch" }],
    pc_error: ["Your PC had a problem", "It answered with an error. Give it a moment. If it keeps happening, restart the app on the PC.", "cpu", {}],
    certificate: ["This PC looks different", "Its security certificate changed since you connected. If you didn't reinstall the app on the PC, don't continue. Connect again by scanning its QR code.", "lock", { lead: "switch", noRetry: true }],
    pc_sleep: ["Your PC went to sleep", "It told this phone it was going to sleep. This goes away by itself when someone wakes the PC.", "power", {}],
    pc_restart: ["Your PC is restarting", "A restart takes a minute or two. This goes away by itself when it is back.", "power", {}],
    pc_shutdown: ["Your PC shut down", "It told this phone it was shutting down. It stays off until someone starts it. If it was only restarting, this goes away by itself.", "power", {}],
    pc_closing: ["The app was closed on your PC", "Open it again on the PC (Start menu or the tray icon). This goes away by itself.", "cpu", {}],
    paused: ["Phone access is paused", "Pause was pressed, so no phone can reach this PC right now. Nothing was deleted or undone. To turn it back on, open AccessFromPhone on the PC and tap Resume. This goes away by itself.", "shield", {}],
    pc_battery: ["Your PC ran out of battery", "Its battery was low and it switched off. Plug it in and turn it on. This goes away by itself.", "battery", {}],
  };
  // What the phone app's own check calls them -> the reasons above.
  const NATIVE_CAUSE = {
    PHONE_OFFLINE: "phone_offline", OTHER_NETWORK: "other_network", APP_NOT_RUNNING: "app_not_running", PC_UNREACHABLE: "pc_unreachable",
    PC_OFFLINE: "pc_offline", NAME_NOT_FOUND: "name_not_found", ADDRESS_CHANGED: "address_changed", PC_ERROR: "pc_error", CERTIFICATE: "certificate",
  };
  // A notice from the PC only explains a silence that has no better explanation of its own.
  const EXPLAINABLE = new Set(["pc_unreachable", "pc_offline", "app_not_running", "name_not_found"]);
  const NOTICE_MINUTES = 20;

  function rememberNotice(kind) {
    if (typeof kind === "string" && kind.startsWith("pc_") && LOST[kind]) store.setRaw("afp_notice", JSON.stringify({ kind, at: Date.now() }));
  }
  /** The newest "your PC is about to go away" notice from the PC, from any place it may have been kept, or null. */
  async function lastNotice() {
    const found = [];
    try { const n = JSON.parse(store.raw("afp_notice") || "null"); if (n) found.push(n); } catch { /* none */ }
    try { if (window.AfpApp && window.AfpApp.lastNotice) { const [kind, at] = String(window.AfpApp.lastNotice() || "").split("|"); if (kind) found.push({ kind, at: Number(at) }); } } catch { /* none */ }
    try { if (window.caches) { const hit = await caches.match("/__notice"); if (hit) found.push(await hit.json()); } } catch { /* none */ }
    const fresh = found.filter((n) => n && LOST[n.kind] && n.kind.startsWith("pc_") && Date.now() - n.at < NOTICE_MINUTES * 60000 && n.at >= Link.lastOk - 60000);
    fresh.sort((a, b) => b.at - a.at);
    return fresh[0] || null;
  }
  /** Ask the phone app to look at the connection (what network, is the PC answering at all, did its certificate change). */
  function askNative() {
    return new Promise((resolve) => {
      const done = (event) => { clearTimeout(timer); window.removeEventListener("afp-diagnosis", done); resolve((event && event.detail) || {}); };
      const timer = setTimeout(() => done(null), 9000);
      window.addEventListener("afp-diagnosis", done);
      try { window.AfpApp.diagnose(); } catch { done(null); }
    });
  }
  /** Work out why the PC can't be reached: { cause, hint }. */
  async function whyLost() {
    let cause = "", hint = "";
    if (!navigator.onLine) cause = "phone_offline";
    else if (Link.status === 423) cause = "paused";  // the PC answered, and said phone access is paused
    else if (window.AfpApp && window.AfpApp.diagnose) {
      const d = await askNative();
      cause = NATIVE_CAUSE[d.kind] || (d.kind === "OK" ? "pc_error" : "pc_unreachable");
      if (d.powerSave && EXPLAINABLE.has(cause)) hint = "Battery saver is on. It can stop this app using the network in the background. Try turning it off.";
    } else if ([502, 503, 504].includes(Link.status)) cause = "pc_offline";
    else if (Link.status >= 500) cause = "pc_error";
    else cause = "pc_unreachable";
    const notice = EXPLAINABLE.has(cause) ? await lastNotice() : null;
    return { cause: notice ? notice.kind : cause, hint };
  }

  // -- the heartbeat: a small check now and then, only when nothing else has just talked to the PC
  let beatTimer = 0, nextCheckAt = 0;
  async function ping() {
    if (!S.token) return;
    const began = performance.now(), ctl = new AbortController(), timer = setTimeout(() => ctl.abort(), 4000);
    try {
      const res = await fetch("/api/ping", { headers: { "X-Token": S.token }, cache: "no-store", signal: ctl.signal });
      if (res.status === 401) { try { await api("/ping"); } catch { /* api() has shown the signed-out screen */ } return; }
      if (!res.ok) { Link.miss({ status: res.status }); return; }
      const data = await res.json();
      const updated = S.pcVersion && data.v && data.v !== S.pcVersion;
      if (data.v) S.pcVersion = data.v;
      if (updated && S.wasLost) { toast("Your PC was updated. Refreshing…"); setTimeout(() => location.reload(), 700); return; }
      Link.ok(Math.round(performance.now() - began));
    } catch { Link.miss({}); }
    finally { clearTimeout(timer); }
  }
  function schedulePing(delay) {
    clearTimeout(beatTimer);
    const wait = delay == null ? Link.delay() : delay;
    nextCheckAt = Date.now() + wait;
    beatTimer = setTimeout(async () => {
      const idle = Date.now() - Link.lastOk >= 4000 || Link.state !== "connected";  // a recent answer from the job list counts as a check
      if (S.token && !document.hidden && idle) await ping();
      schedulePing();
    }, wait);
  }

  // -- the popup
  let lostEl = null, countdown = 0, lostFocus = null, lostSince = 0;
  const lostSections = () => [$("#app"), $("#layer")].filter(Boolean);

  function buildLost() {
    const el = h("div", { class: "lost", id: "lost", role: "alertdialog", "aria-modal": "true", "aria-labelledby": "lostTitle", "aria-describedby": "lostText" },
      h("div", { class: "lost-card" },
        h("div", { class: "lost-orb", id: "lostIcon" }),
        h("h2", { id: "lostTitle" }), h("p", { id: "lostText" }),
        h("p", { class: "lost-hint", id: "lostHint", hidden: true }),
        h("div", { class: "lost-meta", id: "lostMeta" }),
        h("div", { class: "lost-status", id: "lostStatus", "aria-live": "polite" }),
        h("div", { class: "lost-actions" },
          h("button", { class: "btn primary", id: "lostRetry", type: "button", onclick: retryNow }, ico("retry"), "Try again"),
          h("div", { class: "row" },
            h("button", { class: "btn", id: "lostSwitch", type: "button", onclick: switchPc }, ico("scan"), "Switch PC"),
            h("button", { class: "btn", id: "lostWifi", type: "button", hidden: true, onclick: () => { haptic("tick"); try { window.AfpApp.openWifiSettings(); } catch { /* not in the app */ } } }, ico("wifi"), "Wi-Fi settings")),
          Q ? h("button", { class: "btn lost-keep", id: "lostKeep", type: "button", onclick: keepChatting }, ico("chat"), "Keep chatting · queue requests") : null,
          h("button", { class: "btn ghost lost-saved", id: "lostSaved", type: "button", hidden: true, onclick: () => enterOffline() }, ico("clock"), "View saved chats"))));
    return el;
  }
  function paintLost(cause, hint) {
    if (!lostEl) return;
    const [title, text, icon, flags] = LOST[cause] || LOST.pc_unreachable;
    $("#lostTitle", lostEl).textContent = title;
    $("#lostText", lostEl).textContent = text;
    $("#lostIcon", lostEl).replaceChildren(ico(icon));
    const hintEl = $("#lostHint", lostEl); hintEl.textContent = hint || ""; hintEl.hidden = !hint;
    const since = Link.lastOk ? ago(new Date(Link.lastOk).toISOString()) : "";
    $("#lostMeta", lostEl).replaceChildren(
      ...(S.info && S.info.pc ? [h("span", { class: "chip", text: S.info.pc })] : []),
      ...(since ? [h("span", { text: "Last connected " + since })] : []));
    $("#lostRetry", lostEl).hidden = !!flags.noRetry;
    $("#lostSwitch", lostEl).classList.toggle("primary", flags.lead === "switch");
    $("#lostWifi", lostEl).hidden = !(flags.settings && window.AfpApp && window.AfpApp.openWifiSettings);
    const saved = $("#lostSaved", lostEl); saved.hidden = !(cfg.saveChats && Saved.has());  // old chats can be read even though nothing else can be done
    lostEl.dataset.cause = cause;
    S.lostCause = cause; if (S.tab === "home" && S.model) renderHome();  // Home's connection card says the same thing
  }
  function paintStatus() {
    if (!lostEl) return;
    const box = $("#lostStatus", lostEl), cause = lostEl.dataset.cause;
    if ((LOST[cause] || [])[3] && LOST[cause][3].noRetry) { box.replaceChildren(); return; }
    if (Link.state === "reconnecting") { box.replaceChildren(h("span", { class: "spin", "aria-hidden": "true" }), h("span", { text: "Checking…" })); return; }
    const secs = Math.max(1, Math.ceil((nextCheckAt - Date.now()) / 1000));
    box.replaceChildren(h("span", { class: "spin slow", "aria-hidden": "true" }), h("span", { text: `Checking again in ${secs} s` }));
  }
  async function refineLost() {
    const mine = lostEl;
    const { cause, hint } = await whyLost();
    if (lostEl === mine) paintLost(cause, hint);  // (the popup may have gone, or been replaced, while the phone looked)
  }
  function showLost() {
    if (!S.token || $(".auth") || lostEl || S.offline) return;
    if (S.away) { paintAway(); return; }  // the person chose to keep chatting: the strip says it, no popup on top of their chat
    lostSince = Date.now(); S.wasLost = true;
    if (V.phase !== "idle") voiceEnd(true);  // a half-spoken request must not be sent into the void
    stopSpeaking();
    lostFocus = document.activeElement;
    lostEl = buildLost();
    document.body.append(lostEl);
    paintLost(!navigator.onLine ? "phone_offline" : Link.status === 423 ? "paused" : "pc_unreachable", "");  // something at once; the real reason follows in a moment
    document.body.classList.add("is-lost");
    for (const part of lostSections()) part.setAttribute("inert", "");  // nothing behind it can be tapped, typed into, or reached by keyboard
    if (window.AfpApp && window.AfpApp.setBlocking) window.AfpApp.setBlocking(true);  // and the Back button must not get past it
    haptic("alert");
    clearInterval(countdown); countdown = setInterval(paintStatus, 1000);
    paintStatus(); refineLost();
    const retry = $("#lostRetry", lostEl); if (retry && !retry.hidden) retry.focus({ preventScroll: true });
  }
  function hideLost() {
    clearInterval(countdown);
    for (const part of lostSections()) part.removeAttribute("inert");
    document.body.classList.remove("is-lost");
    if (window.AfpApp && window.AfpApp.setBlocking) window.AfpApp.setBlocking(false);
    const el = lostEl; lostEl = null;
    if (el) { el.classList.add("out"); setTimeout(() => el.remove(), 260); }
    try { if (lostFocus && lostFocus.focus) lostFocus.focus({ preventScroll: true }); } catch { /* it is gone */ }
    lostFocus = null;
  }
  function retryNow() {
    if (Link.state === "reconnecting") return;
    haptic("tick");
    Link.retrying();
    ping();
  }
  function switchPc() {
    haptic("tick");
    if (window.AfpApp && window.AfpApp.switchPc) { window.AfpApp.switchPc(); return; }  // the app scans the other PC's code, and keeps this one to go back to
    openScanner((found) => {
      if (found.url && found.origin !== location.origin) location.href = found.url;
      else toast("That code is for this PC. Scan the code shown on the other one.");
    });
  }
  /* ---- reading saved chats while the PC can't be reached ---- */
  const savedSource = () => ({ pc: (S.info && S.info.pc) || "", info: S.info, jobs: S.jobs, audit: S.audit });
  function savedBar() {
    let bar = $("#savedBar");
    if (!bar) return null;
    if (!S.offline) { bar.hidden = true; bar.replaceChildren(); return null; }
    const trying = (Link && Link.state === "reconnecting") || !!S.offline.checking;
    const message = `Reading chats saved ${window.AfpOffline.age(S.offline.savedAt, Date.now())}. New requests wait on this phone until it is back.`;
    bar.hidden = false;
    bar.replaceChildren(
      h("span", { class: "saved-ico", "aria-hidden": "true" }, ico("clock")),
      h("div", { class: "saved-text", tabindex: "0", "aria-label": "Offline notice. Scroll to read." },
        h("div", { class: "saved-track" }, [false, true].map((duplicate) =>
          h("div", { class: "saved-copy", "aria-hidden": duplicate ? "true" : null },
            h("b", { text: "Your PC is offline" }), h("span", { text: message }))))),
      h("button", { class: "saved-retry", type: "button", disabled: trying, onclick: retrySaved }, trying ? h("span", { class: "spin", "aria-hidden": "true" }) : null, trying ? "Checking…" : "Try again"));
    return bar;
  }
  function retrySaved() {
    haptic("tick");
    if (S.offline && S.offline.boot) {  // the app's own copy of this page has no way to reach the PC: the app does it
      if (window.AfpApp && window.AfpApp.retryConnect) {
        S.offline.checking = true; savedBar();
        window.AfpApp.retryConnect();
        setTimeout(() => { if (S.offline) { S.offline.checking = false; savedBar(); } }, 6000);
      } else location.reload();
      return;
    }
    retryNow(); savedBar();
  }
  /* The app was closed while looking at the PC's screen (swiped away from the recent apps, no signal) and is opened again before the PC gave up waiting (about 20 seconds):
     pick the viewing up again at once, as it was, instead of making the person go Home, Access, View the PC's screen. The PC only ever says "viewing" to the phone that IS viewing,
     it is asked once per start of the app, and with the app lock on it waits for the PIN first. After the PC has ended the viewing there is nothing to pick up, and nothing opens. */
  async function resumeScreen() {
    if (!Screen || S.offline) return;
    try {
      if (Lock.el) await new Promise((done) => { const wait = setInterval(() => { if (!Lock.el) { clearInterval(wait); done(); } }, 400); setTimeout(() => { clearInterval(wait); done(); }, 60000); });
      if (S.sheets.length || S.offline) return;   // the person is already somewhere else
      const view = await (await api("/screen")).json();
      if (view && view.state === "viewing" && !S.sheets.length) Screen.open({ resume: true });   // it opens on a loading scene that says it is picking up the previous session
    } catch { /* nothing to pick up */ }
  }

  async function enterOffline() {
    const snap = Saved.current() || await Saved.load();
    if (!snap || !window.AfpOffline.valid(snap)) { toast("There are no saved chats on this phone yet."); return; }
    showSaved(snap, false);
  }
  function showSaved(snap, boot) {
    S.offline = { savedAt: snap.savedAt, boot: !!boot, prevConv: S.conv };
    S.jobs = snap.jobs; S.audit = snap.audit; S.loaded = true;
    // Open the newest saved chat unless the one on screen is itself a saved chat (a brand-new chat would just be empty).
    if (!snap.jobs.some((j) => j.conversation === S.conv) && snap.jobs.length) S.conv = [...snap.jobs].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))[0].conversation;
    if (!S.info && snap.info) S.info = snap.info;
    if (!boot) hideLost();
    document.body.classList.add("is-offline");
    for (const f of document.querySelectorAll(".field")) f.blur();
    savedBar(); S.sideSig = ""; S.topSig = "";
    if (S.model) { renderSide(); renderTop(); }
    update(true); renderDock();
    haptic("tick");
  }
  function leaveSaved() {
    if (!S.offline) return;
    if (S.offline.prevConv) S.conv = S.offline.prevConv;
    S.offline = null;
    document.body.classList.remove("is-offline");
    savedBar(); renderDock();
    toast("Back online"); haptic("success");
    refresh(); loadInfo(); loadPcStatus(); Push.refreshQuietly();
  }
  function recovered() {
    const wait = Link.holdMs || 0;  // the popup stays at least a moment, so it never just flashes
    setTimeout(() => {
      if (Link.state !== "connected") return;  // it dropped again while the popup was being held
      S.lostCause = "";
      if (S.offline) { leaveSaved(); flushQueue(); return; }
      const had = !!lostEl, wasAway = S.away;
      hideLost();
      if (wasAway) { S.away = false; paintAway("back"); }
      if (!S.token || (!had && !wasAway)) return;
      toast("Back online"); haptic("success");
      refresh(); loadInfo(); loadPcStatus(); Push.refreshQuietly();
      flushQueue();
    }, wait);
  }
  function linkChanged(state, previous) {
    S.online = state === "connected" || state === "wobbling";
    S.sideSig = ""; S.topSig = ""; if (S.model) { renderSide(); renderTop(); }
    if (D.field && D.field.placeholder !== undefined && !D.field.disabled) D.field.placeholder = composerHint();
    if (S.away && state !== "connected") paintAway();
    if (S.tab === "home" && S.model) renderHome();
    if (Q && S.model && Q.items().length) update(true);  // "waiting for your PC" and "sending now" follow the connection
    if (state === "wobbling") schedulePing(Link.delay());              // look again soon: it may be only a blip
    else if (state === "disconnected" && previous === "wobbling") { showLost(); schedulePing(); }
    else if (state === "disconnected") {                                // a retry failed: say so, and look at the reason again
      if (S.offline) { savedBar(); haptic("error"); }
      if (lostEl) { haptic("error"); const card = $(".lost-card", lostEl); card.classList.remove("shake"); void card.offsetWidth; card.classList.add("shake"); refineLost(); }
      schedulePing();
    } else if (state === "reconnecting") { paintStatus(); if (S.offline) savedBar(); }
    else if (state === "connected" && (previous === "disconnected" || previous === "reconnecting")) { recovered(); schedulePing(); }
    else if (state === "connected") schedulePing();
  }
  function paintSlow(slow) {
    const box = $("#offline"), text = $("#offlineText");
    if (!box || !text) return;
    text.textContent = "Your connection to the PC is slow.";
    box.classList.remove("long"); box.hidden = !slow || !!lostEl;
  }
  Link = AfpConn.createMonitor({ onChange: linkChanged, onSlow: paintSlow });
  window.addEventListener("offline", () => { if (S.token) { Link.miss({}); schedulePing(1000); } });  // the phone itself lost its connection: start the grace period now
  window.addEventListener("online", () => { if (S.token) { Link.state === "connected" ? ping() : retryNow(); } });
  document.addEventListener("visibilitychange", () => { if (!document.hidden && S.token) ping(); });  // back in front: check at once
  async function loadInfo() {
    try { S.info = await (await api("/info")).json(); S.topSig = ""; S.sideSig = ""; update(true); if (S.refillSettings) S.refillSettings(); } catch { /* the page works without it */ }
  }

  let renderSig = "";
  function update(force) {
    const sig = JSON.stringify([S.jobs, S.audit.length, S.conv, S.hidden.size, S.query, S.loaded, Q ? Q.items() : 0, !!S.away]);
    if (!force && sig === renderSig) return;
    renderSig = sig;
    S.model = buildModel();
    if (!S.conv) { const first = S.model.convs.find((c) => !S.hidden.has(c.id)); S.conv = first ? first.id : newId(); store.setRaw("afp_conv", S.conv); }
    if (S.draftConv !== S.conv && D.field) renderDock();
    renderSide(); renderTop(); renderThread(); syncScroll(); paintPhase(); syncTicks(); autoOpenEditor();
    if (D.syncSend) D.syncSend();
    paintTabs(); if (S.tab === "home") renderHome();
    if (approvals.fill) approvals.fill();
    powerSounds();
  }
  function pollDelay() {
    if (S.jobs.some((j) => BUSY.has(j.status))) return 1500;
    return S.jobs.some((j) => j.status === "awaiting_approval" || j.status === "needs_access") ? 4000 : 5000;
  }
  function loop() { setTimeout(async () => { if (!document.hidden && S.token) await refresh(); loop(); }, pollDelay()); }

  /* ===================================================================== auth */

  const TOKEN_RE = /^[A-Za-z0-9_-]{16,80}$/;
  const PAIR_RE = /^[A-Za-z0-9]{4}-?[A-Za-z0-9]{4}$/; // the one-time code on the PC's page, e.g. K7M2-9QXP

  /* The PC's QR code holds a link with a one-time pairing code inside (or, with accounts off, the shared token). Anything else is ignored. */
  function readScan(text) {
    let url = null, token = "", pair = "";
    try { url = new URL(text); token = url.searchParams.get("token") || ""; pair = url.searchParams.get("pair") || ""; }
    catch { const t = text.trim(); url = null; if (PAIR_RE.test(t)) pair = t; else token = t; }
    const where = { url: url ? url.href : "", origin: url ? url.origin : "" };
    if (PAIR_RE.test(pair)) return { pair, ...where };
    return TOKEN_RE.test(token) ? { token, ...where } : null;
  }

  /* A name for this phone to start with; the owner can rename it on the PC. */
  function guessDevice() {
    const ua = navigator.userAgent || "";
    if (/iPad/.test(ua)) return "iPad";
    if (/iPhone/.test(ua)) return "iPhone";
    if (/Android/.test(ua)) return /Mobile/.test(ua) ? "Android phone" : "Android tablet";
    if (/Windows/.test(ua)) return "Windows PC";
    if (/Mac/.test(ua)) return "Mac";
    return "My phone";
  }

  function loadScript(src) {
    return new Promise((done) => {
      if (document.querySelector(`script[src="${src}"]`)) { done(); return; }
      const s = h("script", { src });
      s.onload = done; s.onerror = done;
      document.head.append(s);
    });
  }

  /* The pairing code is 8 characters (K7M2-9QXP). One box per character: type, paste or let the camera fill it in. */
  function codeBoxes(onChange) {
    const boxes = Array.from({ length: 8 }, (_, i) => h("input", { class: "cbox", type: "text", maxlength: "1", inputmode: "text", autocapitalize: "characters", autocomplete: "off", spellcheck: "false", "aria-label": "Character " + (i + 1) }));
    const group = h("div", { class: "code-boxes", role: "group", "aria-label": "Pairing code, 8 characters" });
    boxes.forEach((b, i) => { group.append(b); if (i === 3) group.append(h("i", { class: "cdash", "aria-hidden": "true" })); });
    const value = () => boxes.map((b) => b.value).join("");
    const changed = () => { boxes.forEach((b) => b.classList.toggle("on", !!b.value)); onChange(value()); };
    const spread = (text, from) => {
      const chars = text.toUpperCase().replace(/[^A-Z0-9]/g, "").split("");
      let at = from;
      for (const c of chars) { if (at > 7) break; boxes[at++].value = c; }
      boxes[Math.min(at, 7)].focus(); changed();
    };
    boxes.forEach((b, i) => {
      b.addEventListener("input", () => {
        const clean = b.value.toUpperCase().replace(/[^A-Z0-9]/g, "");
        if (clean.length > 1) { b.value = ""; spread(clean, i); return; }
        b.value = clean; changed();
        if (clean && i < 7) boxes[i + 1].focus();
        if (clean) haptic("tick");
      });
      b.addEventListener("keydown", (e) => {
        if (e.key === "Backspace" && !b.value && i > 0) { boxes[i - 1].value = ""; boxes[i - 1].focus(); changed(); e.preventDefault(); }
        else if (e.key === "ArrowLeft" && i > 0) boxes[i - 1].focus();
        else if (e.key === "ArrowRight" && i < 7) boxes[i + 1].focus();
      });
      b.addEventListener("paste", (e) => { e.preventDefault(); spread((e.clipboardData || window.clipboardData).getData("text") || "", 0); });
      b.addEventListener("focus", () => b.select());
    });
    return {
      node: group, value,
      fill: (text) => spread(text, 0), clear: () => { boxes.forEach((b) => { b.value = ""; }); changed(); boxes[0].focus(); },
      focus: () => boxes[Math.max(0, boxes.findIndex((b) => !b.value))].focus(),
    };
  }

  function authSkeleton() {
    return h("div", { class: "auth-skel", "aria-hidden": "true" }, h("i", { class: "sk btn" }), h("i", { class: "sk line short" }),
      h("div", { class: "sk-row" }, Array.from({ length: 8 }, () => h("i", { class: "sk cell" }))), h("i", { class: "sk btn" }));
  }

  function showAuth() {
    if ($(".auth")) return $(".auth");
    const el = h("div", { class: "auth" });
    const nameInput = h("input", { class: "name-input", type: "text", autocomplete: "off", maxlength: "40", value: store.raw("afp_device") || guessDevice(), "aria-label": "Name for this phone" });
    el._name = () => nameInput.value.trim() || guessDevice();
    const title = h("h2", { text: "Connect to your PC" });
    const lead = h("p", { class: "lead", text: S.signedOut ? "This phone was signed out from the PC. Scan a new code to connect again." : "Scan the QR code shown on your PC, or type the code under it." });
    const body = h("div", { class: "auth-actions" }, authSkeleton());
    const status = h("span", { class: "auth-chip" }, h("i", { class: "dot" }), h("span", { text: "Not connected" }));

    const go = h("button", { class: "btn primary connect-btn", type: "button", disabled: true }, "Connect");
    let boxes = null, tokenInput = null;
    // In the phone app, before any PC is chosen, this same screen is built into the app. The app finds the PC on the Wi-Fi by itself,
    // so the code is all it needs. Only if it can't (some office Wi-Fi hides devices) does an address field appear below.
    const noPcYet = !!(window.AfpApp && window.AfpApp.isLocal && window.AfpApp.isLocal());
    let addressInput = null;
    const needsAddress = () => !!addressInput && !addressInput.hidden;
    const canGo = () => !!boxes && boxes.value().length === 8 && (!needsAddress() || !!addressInput.value.trim());
    const submit = () => {
      if (!noPcYet) return connect(boxes ? boxes.value() : tokenInput.value.trim(), el);
      if (!canGo()) return;
      const answer = window.AfpApp.connect(needsAddress() ? addressInput.value.trim() : "", boxes.value(), el._name());
      if (answer === "ok") return;  // the app is opening the PC's page
      haptic("error"); el.classList.remove("shake"); void el.offsetWidth; el.classList.add("shake");
      if (answer === "none" || answer === "many") {  // keep the code they typed; just ask for the address too
        addressInput.hidden = false; go.disabled = !canGo(); addressInput.focus();
        toast(answer === "none" ? "Can't find your PC on this Wi-Fi. Type its address below." : "More than one PC answered. Type the address of yours below.");
      } else toast("That doesn't look like a PC address and code.");
    };
    go.onclick = submit;
    const scan = h("button", { class: "btn scan-btn", type: "button", onclick: () => { if (noPcYet) window.AfpApp.setName(el._name()); openScanner((found) => {
      // A QR code for another address (the PC's IP changed): go there, the pairing code travels in the link.
      if (found.url && found.origin !== location.origin) location.href = found.url; else connect(found.pair || found.token, el);
    }); } }, ico("scan"), "Scan the QR code");
    const ready = (info) => {
      body.replaceChildren();
      if (!info.ready) { lead.textContent = "This PC isn't set up yet. On the PC, open AccessFromPhone's page and sign in first."; }
      if (info.accounts === false) {
        tokenInput = h("input", { class: "text-input", type: "password", autocomplete: "off", placeholder: "Paste the access token", "aria-label": "Access token" });
        tokenInput.addEventListener("input", () => { go.disabled = !tokenInput.value.trim(); });
        body.append(scan, h("div", { class: "or", text: "or paste the token" }), tokenInput, go);
      } else {
        boxes = codeBoxes((v) => { go.disabled = !canGo(); if (canGo()) setTimeout(submit, 220); });
        el._boxes = boxes;
        if (noPcYet) {
          addressInput = h("input", { class: "text-input", type: "text", inputmode: "url", autocomplete: "off", autocapitalize: "none", spellcheck: "false", hidden: true, style: "margin-top:12px", placeholder: "Your PC's address, e.g. 192.168.1.20:8765", "aria-label": "Your PC's address" });
          addressInput.addEventListener("input", () => { go.disabled = !canGo(); });
          body.append(scan, h("div", { class: "or", text: "or type the code" }), boxes.node, addressInput, go);
        } else body.append(scan, h("div", { class: "or", text: "or type the code" }), boxes.node, go);
      }
      body.classList.add("in");
    };
    const failed = () => {
      body.replaceChildren(h("div", { class: "auth-error" }, ico("wifi"), h("p", { text: "Can't reach your PC." }), h("p", { class: "hint", text: "Check that the PC is on and that this phone is on the same Wi-Fi." }),
        h("button", { class: "btn", type: "button", onclick: () => { body.replaceChildren(authSkeleton()); load(); } }, "Try again")));
    };
    const load = () => noPcYet ? ready({ ready: true, accounts: true }) : fetch("/pair/info").then((r) => r.json()).then((i) => { if (el.isConnected && !el.classList.contains("ok")) ready(i); }).catch(() => { if (el.isConnected) failed(); });

    const nameRow = h("label", { class: "auth-name" }, ico("phone"), h("span", { text: "This phone is called" }), nameInput);
    const help = h("details", { class: "auth-help" }, h("summary", { text: "Where do I find the code?" }),
      h("ol", {}, h("li", { text: "On your PC, open AccessFromPhone (it opens in your browser when you start it)." }),
        h("li", { text: "Sign in with your Windows user name and password." }),
        h("li", { text: "Scan the QR code on that page, or type the code under it." })));
    el.append(h("div", { class: "auth-card" },
      h("div", { class: "auth-top" }, h("span", { class: "wordmark", text: "ACCESS" }), status),
      h("div", { class: "orb-wrap" }, orbSVG()), title, lead, body, nameRow, help,
      h("p", { class: "auth-foot" }, ico("lock"), h("span", { text: "Private. It works over your own Wi-Fi and nothing goes to a cloud." }))));
    $("#layer").append(el);
    load();
    return el;
  }

  function rejectAuth(el, message) {
    haptic("error");
    if (el._boxes) setTimeout(() => el._boxes.clear(), 380);
    el.classList.remove("shake"); void el.offsetWidth; el.classList.add("shake");
    toast(message);
  }

  async function connect(value, el) {
    value = (value || "").trim();
    // A pairing code is traded for this phone's own token; anything else is the older shared token.
    const pair = PAIR_RE.test(value) && !TOKEN_RE.test(value);
    if (!pair && !TOKEN_RE.test(value)) { rejectAuth(el, "That doesn't look like a pairing code."); return; }
    el.classList.add("busy"); el.dataset.phase = "thinking";
    try {
      let token = value;
      if (pair) {
        const name = el._name ? el._name() : guessDevice();
        const previous = S.token || store.raw("afp_token") || ""; // still ours: the PC then updates this phone's entry instead of adding a second one
        let install = store.raw("afp_install");   // this app's own id, kept even after signing out: the PC then knows it is the same phone, not a new one
        if (!install) { try { install = Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join(""); } catch { install = String(Date.now()) + Math.random().toString(16).slice(2); } store.setRaw("afp_install", install); }
        const res = await fetch("/pair/claim", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code: value, name, previous, install }) });
        let body = null; try { body = await res.json(); } catch { /* no body */ }
        if (res.status === 409) {  // the PC is full: say so in a pop-up, not a thin red line
          el.classList.remove("busy"); delete el.dataset.phase;
          ask({ title: "Device limit reached", message: ((body && body.detail) || "").replace(/^Device limit reached.s*/, "") || "This PC already has the most devices it allows. Remove one in the PC's window (Phones), then try again.", ok: "Got it", cancel: null, icon: "phone" });
          haptic("error"); return;
        }
        if (!res.ok) throw new Error((body && typeof body.detail === "string" && body.detail) || "The PC didn't accept that code.");
        token = body.token; store.setRaw("afp_device", name);
      }
      const res = await fetch("/api/info", { headers: { "X-Token": token } });
      if (res.status === 401) throw new Error("That code isn't right for this PC.");
      if (!res.ok) throw new Error("The PC answered with an error.");
      const info = await res.json();
      S.token = token; S.signedOut = false; store.setRaw("afp_token", token); S.info = info; S.loaded = false;
      celebrate(el, info);
    } catch (e) {
      el.classList.remove("busy"); delete el.dataset.phase;
      rejectAuth(el, e instanceof TypeError ? "Can't reach your PC. Are you on the same Wi-Fi?" : e.message);
    }
  }

  /* Connected: the orb flares and a tick draws itself, then the sign-in screen irises shut onto the chat behind it. */
  function celebrate(el, info) {
    haptic("success");
    el.classList.remove("busy"); el.classList.add("ok"); el.dataset.phase = "speaking";
    const mark = h("div", { class: "check", "aria-hidden": "true" });
    mark.innerHTML = '<svg viewBox="0 0 64 64" width="64" height="64"><circle class="disc" cx="32" cy="32" r="30"/><path d="M19 33l9 9 17-19"/></svg>';
    const wrap = $(".orb-wrap", el); wrap.append(mark);
    $("h2", el).textContent = "Connected";
    $("p", el).textContent = `Linked to ${info.pc || "your PC"}.`;
    for (const part of el.querySelectorAll(".auth-actions, .auth-name, .auth-help, .auth-foot, .auth-top .auth-chip")) part.remove();
    update(true); loadPcStatus();
    // Open onto the dashboard as soon as it has its data (never longer than 2.5 s), not on a fixed timer.
    const ready = Promise.race([refresh(), new Promise((r) => setTimeout(r, 2500))]);
    const tick = new Promise((r) => setTimeout(r, 650)); // just long enough to see the tick draw
    Promise.all([ready, tick]).then(() => {
      update(true);
      if (!tourDone()) {
        // First time on this phone: a short tour (how it works, a few choices) before the chat, not straight into it.
        openOnboarding({ first: true }); haptic("tick");
        el.classList.add("leave"); setTimeout(() => el.remove(), 500);
        return;
      }
      document.body.classList.add("reveal"); el.classList.add("out"); haptic("tick");
      setTimeout(() => { el.remove(); document.body.classList.remove("reveal"); }, 560);
    });
  }

  /* ===================================================================== this PC's status: battery, network, processor, memory, disk */

  const PC = { data: null, rtt: 0, warned: false, repaint: null, timer: 0 };

  const fmtRate = (bps) => { const m = bps / 1e6; return m >= 100 ? { n: m.toFixed(0), u: "Mbps" } : m >= 1 ? { n: m.toFixed(1), u: "Mbps" } : { n: Math.round(bps / 1000), u: "Kbps" }; };
  const fmtBytes = (b) => b >= 1024 ** 4 ? (b / 1024 ** 4).toFixed(1) + " TB" : b >= 1024 ** 3 ? (b / 1024 ** 3).toFixed(b >= 1024 ** 3 * 100 ? 0 : 1) + " GB" : Math.round(b / 1024 ** 2) + " MB";
  const fmtSpan = (s) => { const d = Math.floor(s / 86400), hr = Math.floor(s % 86400 / 3600), mn = Math.floor(s % 3600 / 60); return d ? `${d} d ${hr} h` : hr ? `${hr} h ${mn} min` : `${mn} min`; };
  const tone = (pct, warnAt, badAt) => pct >= badAt ? "bad" : pct >= warnAt ? "warn" : "ok";
  const quality = (ms) => ms < 60 ? ["Excellent", "ok"] : ms < 150 ? ["Good", "ok"] : ms < 400 ? ["Fair", "warn"] : ["Slow", "bad"];

  let statusBusy = false;
  async function loadPcStatus() {
    if (statusBusy) return;  // one question at a time: a slow answer is never piled on
    statusBusy = true;
    const t0 = performance.now();
    try {
      const d = await (await api("/status")).json();
      PC.rtt = Math.max(1, Math.round(performance.now() - t0));
      PC.data = d;
      if (Access) Access.update(d.session || null);
      const b = d.battery;
      if (b && !b.plugged && b.percent <= 20 && !PC.warned) { PC.warned = true; toast(`The PC's battery is at ${b.percent}%. Plug it in soon.`); haptic("warning"); }
      if (!b || b.plugged || b.percent > 30) PC.warned = false;
      renderTop(); renderSide();  // each redraws only when its own signature changed (battery, Wi-Fi level and speed are in it), so a refresh every 2 s causes no flicker
      if (PC.repaint) PC.repaint();
      if (S.tab === "home") renderHome();
      paintChips();
    } catch (e) {
      if (e && e.status === 404) {  // an older version of the app is still running on the PC
        PC.data = { available: false, reason: "The app on your PC is an older version. Restart AccessFromPhone on the PC to see its status here." };
        S.topSig = ""; S.sideSig = ""; renderTop(); renderSide();
      }
      /* otherwise offline: the banner already says so */
    } finally { statusBusy = false; }
  }

  /* the little chip in the top bar: battery % at a glance, tap for everything */
  function pcChip(extra = "") {
    const d = PC.data, b = d && d.available ? d.battery : null;
    const mini = extra.includes("mini");
    if (mini && !d) return h("span", { class: "chip-pc mini sk-chip", "aria-hidden": "true" });  // a quiet placeholder until the PC answers
    const low = b && !b.plugged && b.percent <= 20;
    const label = b ? `${b.percent}%` : "";
    const level = !b ? "" : b.plugged ? "ok" : b.percent <= 20 ? "bad" : b.percent <= 40 ? "warn" : "ok";
    return h("button", { class: "chip-pc " + extra + (low ? " low" : ""), "data-level": level, type: "button", "aria-label": b ? `PC battery ${b.percent}%${b.plugged ? ", charging" : ""}. Open PC status` : "Open PC status", onclick: (e) => { e.stopPropagation(); haptic("light"); closeDrawer(); openPcStatus(); } },
      ico(b ? "battery" : "gauge"), b && b.plugged ? ico("bolt") : null, label ? h("span", { class: "t", text: label }) : null);
  }

  function ringSVG(id) {
    const wrap = h("div", { class: "pc-ring" });
    wrap.innerHTML = `<svg viewBox="0 0 80 80" aria-hidden="true"><defs><linearGradient id="${id}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="var(--ring-a)"/><stop offset="1" stop-color="var(--ring-b)"/></linearGradient></defs><circle class="trk" cx="40" cy="40" r="34" pathLength="100"/><circle class="arc" cx="40" cy="40" r="34" pathLength="100" stroke="url(#${id})"/></svg><div class="in"></div>`;
    return wrap;
  }
  const setRing = (ring, pct, tn) => { ring.dataset.tone = tn; ring.querySelector(".arc").style.strokeDasharray = `${Math.max(1, Math.min(100, pct))} 100`; };

  function spark(down, up) {
    const W = 300, H = 72, n = Math.max(down.length, 2);
    const max = Math.max(1e6, ...down, ...up) * 1.15;
    const pts = (a) => a.map((v, i) => `${(i / (n - 1) * W).toFixed(1)},${(H - 4 - v / max * (H - 8)).toFixed(1)}`);
    const line = (a, cls) => { const p = pts(a); return p.length < 2 ? "" : `<polyline class="${cls}" points="${p.join(" ")}"/>`; };
    const area = pts(down);
    return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true"><defs><linearGradient id="spA" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--a1)" stop-opacity=".35"/><stop offset="1" stop-color="var(--a1)" stop-opacity="0"/></linearGradient></defs>`
      + (area.length > 1 ? `<polygon class="fill" points="0,${H} ${area.join(" ")} ${W},${H}"/>` : "") + line(up, "up") + line(down, "down") + "</svg>";
  }

  function pcStatusView() {
    const R = {};
    const card = (cls, ...kids) => h("section", { class: "pc-card " + cls }, ...kids);

    R.bRing = ringSVG("rgB"); R.bTitle = h("div", { class: "pc-big" }); R.bSub = h("div", { class: "pc-sub" }); R.bTag = h("span", { class: "tag" });
    const battery = card("pc-battery", R.bRing, h("div", { class: "grow" }, h("div", { class: "pc-label" }, ico("battery"), "Battery"), R.bTitle, R.bSub), R.bTag);

    R.down = h("b"); R.downU = h("small"); R.up = h("b"); R.upU = h("small"); R.spark = h("div", { class: "pc-spark" });
    R.link = h("span"); R.lat = h("span", { class: "tag" });
    const network = card("pc-net", h("div", { class: "pc-label" }, ico("wifi"), "Network speed", h("em", { text: "now" })),
      h("div", { class: "pc-rates" }, h("div", { class: "rate down" }, h("span", { class: "arrow", text: "↓" }), R.down, R.downU, h("i", { text: "download" })),
        h("div", { class: "rate up" }, h("span", { class: "arrow", text: "↑" }), R.up, R.upU, h("i", { text: "upload" }))),
      R.spark, h("div", { class: "pc-meta" }, R.link, R.lat));

    R.cRing = ringSVG("rgC"); R.mRing = ringSVG("rgM"); R.cSub = h("small"); R.mSub = h("small");
    const pair = h("div", { class: "pc-pair" },
      card("pc-mini", R.cRing, h("div", { class: "pc-label" }, "Processor"), R.cSub),
      card("pc-mini", R.mRing, h("div", { class: "pc-label" }, "Memory"), R.mSub));

    R.dBar = h("i"); R.dText = h("div", { class: "pc-big sm" }); R.dSub = h("div", { class: "pc-sub" });
    const disk = card("pc-disk", h("div", { class: "pc-label" }, ico("folder"), "System drive"), R.dText, h("div", { class: "pc-bar" }, R.dBar), R.dSub);

    R.foot = h("p", { class: "pc-foot" });
    const node = h("div", { class: "pcs" }, battery, network, pair, disk, R.foot);

    const skeleton = () => node.classList.add("loading");
    skeleton();
    R.update = (d, rtt) => {
      node.classList.remove("loading");
      if (!d.available) { node.replaceChildren(h("p", { class: "pc-none", text: d.reason || "The PC's status isn't available." })); return; }
      // battery
      const b = d.battery;
      if (!b) {
        setRing(R.bRing, 100, "ok"); R.bRing.querySelector(".in").replaceChildren(ico("plug", 26));
        R.bTitle.textContent = "No battery"; R.bSub.textContent = "This PC runs on mains power."; R.bTag.className = "tag ok"; R.bTag.textContent = "Plugged in";
      } else {
        const tn = b.plugged ? "ok" : b.percent <= 20 ? "bad" : b.percent <= 40 ? "warn" : "ok";
        setRing(R.bRing, b.percent, tn);
        R.bRing.querySelector(".in").replaceChildren(h("b", { text: String(b.percent) }), h("small", { text: "%" }));
        R.bTitle.textContent = b.state === "full" ? "Fully charged" : b.state === "charging" ? "Charging" : "On battery";
        R.bSub.textContent = b.state === "full" ? "Plugged in." : b.state === "charging" ? "Plugged in, filling up." : b.minutes_left ? `About ${fmtSpan(b.minutes_left * 60)} left.` : b.percent <= 20 ? "Running low. Plug it in soon." : "Unplugged.";
        R.bTag.className = "tag " + (b.plugged ? "ok" : tn); R.bTag.textContent = b.plugged ? "Plugged in" : b.percent <= 20 ? "Low" : "Unplugged";
      }
      // network
      const n = d.network, dn = fmtRate(n.down_bps), upr = fmtRate(n.up_bps);
      R.down.textContent = dn.n; R.downU.textContent = dn.u; R.up.textContent = upr.n; R.upU.textContent = upr.u;
      R.spark.innerHTML = spark(n.history.down, n.history.up);
      R.link.textContent = (n.adapter ? n.adapter + " · " : "") + (n.link_mbps ? `link ${n.link_mbps} Mbps` : "link speed unknown");
      const [q, qt] = quality(rtt); R.lat.className = "tag " + qt; R.lat.textContent = `Phone to PC ${rtt} ms · ${q}`;
      // processor and memory
      setRing(R.cRing, d.cpu_percent, tone(d.cpu_percent, 70, 90)); R.cRing.querySelector(".in").replaceChildren(h("b", { text: String(d.cpu_percent) }), h("small", { text: "%" }));
      R.cSub.textContent = d.cpu_percent >= 90 ? "Very busy" : d.cpu_percent >= 70 ? "Busy" : "Fine";
      setRing(R.mRing, d.memory.percent, tone(d.memory.percent, 80, 92)); R.mRing.querySelector(".in").replaceChildren(h("b", { text: String(d.memory.percent) }), h("small", { text: "%" }));
      R.mSub.textContent = `${fmtBytes(d.memory.used)} of ${fmtBytes(d.memory.total)}`;
      // disk
      if (d.disk) {
        R.dText.textContent = `${fmtBytes(d.disk.free)} free`; R.dBar.style.width = d.disk.percent + "%"; R.dBar.dataset.tone = tone(d.disk.percent, 85, 95);
        R.dSub.textContent = `${d.disk.percent}% of ${fmtBytes(d.disk.total)} used` + (d.disk.percent >= 90 ? ". It's getting full." : "");
      }
      R.foot.textContent = `Up for ${fmtSpan(d.uptime_s)} · updates every 2 seconds`;
    };
    return { node, R };
  }

  function openPcStatus() {
    const view = pcStatusView();
    const repaint = () => { if (PC.data) view.R.update(PC.data, PC.rtt); };
    PC.repaint = repaint;
    if (PC.data) repaint();
    loadPcStatus();
    loadPcStatus();  // the page's own 2-second refresh keeps it current; there is no second timer
    openSheet({ title: "This PC", body: view.node, full: true, route: "status", onClose: () => { PC.repaint = null; } });
  }

  /* ===================================================================== Home: the PC at a glance, one-tap actions, what needs you */

  const pendingJobs = () => S.jobs.filter((j) => (j.status === "awaiting_approval" || j.status === "needs_access") && !S.hidden.has(j.conversation));
  const canPower = (kind) => !!(S.info && (S.info.power_actions || []).includes(kind));
  const unlockInfo = () => (PC.data && PC.data.unlock) || null;
  const pcLocked = () => { const u = unlockInfo(); return !!(u && u.locked === true); };
  const unlockReady = () => { const u = unlockInfo(); return !!(u && u.running && u.configured && u.enabled && !u.attention); };
  const shorten = (text, n = 60) => { const t = String(text || "").replace(/\s+/g, " ").trim(); return t.length > n ? t.slice(0, n - 1) + "…" : t; };

  /** Say something to the PC from Home: the chat opens and shows what happens. */
  function askPc(text) {
    if (S.offline) { toast("Your PC is offline, so that has to wait."); haptic("error"); return; }
    haptic("light"); setTab("chat"); send(text);
  }

  /* The everyday tools (quick controls, inbox, notes, reminders, watchers, recent files, print, snap, briefing) are a separate script and style sheet,
     fetched once Home has the PC's answer and only if this PC has them switched on. */
  let Daily = null, dailyLoad = null, dailyFailed = false;
  if (window.AfpUx) window.AfpUx.spot(document);   // the soft light that follows a finger on cards and tiles
  function loadDaily() {
    if (Daily || dailyLoad || dailyFailed) return dailyLoad;
    dailyLoad = new Promise((resolve, reject) => {
      document.head.append(h("link", { rel: "stylesheet", href: "/static/daily.css" }));
      const s = h("script", { src: "/static/daily.js" });
      s.onload = resolve; s.onerror = () => reject(new Error("Couldn't load the everyday tools."));
      document.head.append(s);
    }).then(() => {
      Daily = window.AfpDaily.create({
        h, ico, ask, toast, haptic, openSheet, saveBlob, askAccess,
        token: () => S.token,
        api: (path, opts) => api(path, opts),
        copy: copyText, speech: Speech, lang: () => cfg.lang,
        closeSheet: () => closeSheet(),
        openFolder: (path) => openFolder(path), openFile: (path) => openFile(path),
        openScreen: () => { if (Screen) Screen.open(); },
      });
      S.homeSig = ""; renderHome();
    }).catch(() => { dailyFailed = true; dailyLoad = null; });
    return dailyLoad;
  }
  const dailyOn = () => !!(S.info && S.info.daily && S.info.daily.enabled) && !S.offline;

  /** Home is the bottom of the stack. Opening the chat from it adds one history entry, so Back (or the arrow) returns Home. */
  function setTab(tab, { push = true } = {}) {
    tab = tab === "chat" ? "chat" : "home";
    if (push && tab === "chat" && !S.chatPushed && !S.sheets.length) { history.pushState({ tab: "chat" }, "", "#/chat"); S.chatPushed = true; }
    else if (push && tab === "home" && S.chatPushed) { S.chatPushed = false; ignorePops += 1; history.back(); }
    S.tab = tab; store.setRaw("afp_tab", tab);
    document.body.classList.toggle("tab-home", tab === "home"); document.body.classList.toggle("tab-chat", tab === "chat");
    const home = $("#home"); if (home) home.hidden = tab !== "home";
    if (tab === "home") S.homeEnter = true;
    S.tabSig = ""; S.homeSig = ""; S.topSig = ""; paintTabs();
    if (tab === "home") { renderHome(); if (S.token && !S.offline) loadPcStatus(); }
    else { S.stick = true; if (S.model) update(true); }
  }

  const TAB_DEFS = [["home", "Home", "home"], ["chat", "Chat", "chat"], ["access", "Access", "monitor"], ["files", "Files", "folder"], ["settings", "Settings", "sliders"]];
  const calmMotion = () => motionLevel() === "off" || (window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches);

  /* Each tab that opens a screen gets its own oil-paint transition (ux.js paint): the PC is scanned like a screen, Files is a stack of papers, Settings is a dial being turned.
     The screen is opened once the paint covers the page, so nothing flashes on the way. */
  const TAB_PAINT = { access: ["pc", "Your PC", "monitor"], files: ["files", "Files", "folder"], settings: ["settings", "Settings", "sliders"] };
  function tabTo(key) {
    haptic("tick");
    const opener = key === "files" ? () => openFolder("") : key === "settings" ? () => openSettings()
      : key === "access" ? () => { if (Access) Access.open(); else toast("PC access isn't available in this version."); } : null;
    if (opener) {
      if (window.AfpUx && window.AfpUx.paint && !calmMotion() && (key !== "access" || Access)) {
        const [theme, label, icon] = TAB_PAINT[key];
        window.AfpUx.paint({ theme, label, badge: ico(icon), level: motionLevel(), onCovered: opener });
      } else opener();
    } else if (key === "chat" && S.tab !== "chat" && !calmMotion()) {
      const bar = $("#tabbar");
      if (bar) { bar.style.setProperty("--i", String(TAB_DEFS.findIndex((d) => d[0] === "chat"))); bar.querySelectorAll(".tab").forEach((t) => t.classList.toggle("on", t.dataset.tab === "chat")); }
      clearTimeout(S.tabSwitch);
      if (window.AfpUx && window.AfpUx.paint) {
        // oil paint sweeps over the page, Chat is switched on underneath, then the paint is scraped away
        const accent = ACCENTS.find((a) => a[0] === cfg.accent) || ACCENTS[0];
        window.AfpUx.paint({ theme: "chat", colors: [accent[1], accent[2]], label: "Chat", badge: ico("chat"), level: motionLevel(), onCovered: () => setTab("chat") });
      } else S.tabSwitch = setTimeout(() => setTab("chat"), 190);
    } else setTab(key);
  }

  /* The bar is built once and then only updated, so the highlight can slide (a rebuilt bar would jump). */
  function paintTabs() {
    const bar = $("#tabbar"); if (!bar) return;
    const n = pendingJobs().length, sig = JSON.stringify([S.tab, n]);
    if (sig === S.tabSig && bar.dataset.built) return;
    S.tabSig = sig;
    if (!bar.dataset.built) {
      bar.dataset.built = "1";
      bar.replaceChildren(h("span", { class: "tab-glass", "aria-hidden": "true" }), ...TAB_DEFS.map(([key, label, icon]) =>
        h("button", { class: "tab", type: "button", "data-tab": key, onclick: () => tabTo(key) }, ico(icon), h("span", { class: "tab-label", text: label }))));
    }
    let at = 0;
    TAB_DEFS.forEach(([key, label], i) => {
      const b = bar.querySelector(`.tab[data-tab="${key}"]`); if (!b) return;
      const on = S.tab === key;
      b.classList.toggle("on", on);
      if (on) { b.setAttribute("aria-current", "page"); at = i; } else b.removeAttribute("aria-current");
      const waiting = key === "chat" ? n : 0;
      let badge = b.querySelector("em");
      if (waiting && !badge) { badge = h("em"); b.append(badge); }
      if (!waiting && badge) badge.remove();
      if (badge) badge.textContent = String(waiting);
      b.setAttribute("aria-label", label + (waiting ? `, ${waiting} waiting for you` : ""));
    });
    bar.style.setProperty("--i", String(at));
    if (Access) Access.marks();  // the dot on the Access icon (someone is using the PC, or asking you for a turn)
  }

  function suggestions() {
    const list = [];
    if (pcLocked() && unlockReady()) list.push(["unlock", "Unlock my PC", "unlock my PC"]);
    else if (canPower("lock") && !pcLocked()) list.push(["lock", "Lock my PC", "lock my PC"]);
    list.push(["folder", "Open my folders", "browse my folders"], ["chat", "Teams messages", "show my unread teams messages"], ["battery", "Battery", "battery status"], ["image", "My pictures", "open any photo file"]);
    return list;
  }

  function paintChips() {
    const row = D.chips; if (!row) return;
    const list = suggestions(), sig = JSON.stringify(list.map((x) => x[1]));
    if (row.dataset.sig !== sig) {  // rebuilt only when the suggestions changed, not on every status refresh
      row.dataset.sig = sig;
      row.replaceChildren(...list.map(([icon, label, text]) => h("button", { class: "sugg", type: "button", onclick: () => { haptic("light"); send(text); } }, ico(icon), h("span", { text: label }))));
    }
    row.hidden = !!(D.field && D.field.value.trim()) || !!S.offline;
  }

  function checklist() {
    const u = unlockInfo(), f = (S.info && S.info.folders) || {};
    const pushKnown = !!Push.info;
    const steps = [
      { done: true, title: "Connect your phone", sub: `Paired with ${(S.info && S.info.pc) || "your PC"}` },
      { done: (f.granted || []).length > 0, title: "Allow a folder", sub: "Pick a folder in Files and allow it", action: ["Open", () => openFolder("")] },
      { done: Push.on(), title: "Turn on notifications", sub: "Know when a request needs you", action: ["Turn on", async () => {
        try { if (!Push.info) await Push.load(); const why = Push.why(); if (why) { toast(why); return; } await Push.enable(); await Push.load(); S.homeSig = ""; renderHome(); toast("Notifications are on"); haptic("success"); }
        catch (e) { toast(e.message); haptic("error"); }
      }] },
      { done: !!(u && u.configured), title: "Set up unlocking", sub: "Unlock this PC from your phone", action: ["How", () => openSheet({ title: "Set up unlocking", route: "unlockhelp",
        body: h("div", { class: "help" }, h("p", { text: "This is done once, at the PC itself, because Windows has to ask for permission there." }), h("p", { text: "On the PC, open the AccessFromPhone window and tap “Set up unlocking” on the Unlock card. After that, “Unlock” appears here whenever the PC is locked." })) })] },
    ];
    return { steps, pushKnown, done: steps.filter((s) => s.done).length };
  }

  /* The Wi-Fi chip beside the battery: signal quality as a colour, and the PC's network speed, like a phone's status bar. Tap for details. */
  function netState() {
    if (S.offline || S.away || pcUnreachable()) {
      const paused = S.lostCause === "paused";
      return { level: "bad", off: true, text: paused ? "Paused" : "Offline", aria: paused ? "Phone access is paused. Open PC status" : "PC unreachable. Open PC status" };
    }
    const n = PC.data && PC.data.available && PC.data.network;
    if (!PC.rtt && !n) return null;
    const [label, level] = PC.rtt ? quality(PC.rtt) : ["", ""];
    const rate = n && n.down_bps != null ? fmtRate(n.down_bps) : null;
    return { level, off: false, text: rate ? `${rate.n} ${rate.u}` : `${PC.rtt} ms`, aria: `Connection ${label.toLowerCase()}${PC.rtt ? ", " + PC.rtt + " milliseconds" : ""}${rate ? ", " + rate.n + " " + rate.u : ""}. Open PC status` };
  }
  const netKey = () => { const n = netState(); return n ? n.level + n.text : ""; };
  function netChip(extra = "") {
    const n = netState();
    if (!n) return extra.includes("mini") ? h("span", { class: "chip-pc chip-net mini sk-chip", "aria-hidden": "true" }) : h("span", { class: "chip-net-empty", hidden: true });
    return h("button", { class: "chip-pc chip-net " + extra, "data-level": n.level, type: "button", "aria-label": n.aria, onclick: (e) => { e.stopPropagation(); haptic("light"); closeDrawer(); openPcStatus(); } },
      ico(n.off ? "wifiOff" : "wifi"), h("span", { class: "t", text: n.text }));
  }

  /* The PC as a small object with depth: a monitor that glows by state (cyan = ready, amber = locked, grey = can't be reached), floats, and tilts to a finger or the phone's movement.
     In "Simple" the same monitor is drawn flat with a slow glow; in "Off" it stands still (all of that is in the CSS, keyed on <html data-motion>). */
  function pcStage(state, name, mini) {
    const icon = state === "locked" ? "lock" : state === "offline" ? "wifiOff" : "unlock";
    const say = state === "locked" ? "locked" : state === "offline" ? "can't be reached" : "ready";
    const face = (cls) => h("i", { class: "pcf " + cls, "aria-hidden": "true" });
    const front = h("div", { class: "pcf pcf-front" }, h("div", { class: "pcscr" }, h("span", { class: "pcscan" }), ico(icon, mini ? 22 : 34), name ? h("small", { text: name }) : null));
    const obj = h("div", { class: "pcobj" }, face("pcf-back"), face("pcf-l"), face("pcf-r"), face("pcf-t"), h("i", { class: "pcneck" }), h("i", { class: "pcbase" }), front);
    return h("div", { class: "pcstage" + (mini ? " mini" : ""), "data-state": state, role: "img", "aria-label": (name || "Your PC") + " is " + say },
      h("span", { class: "pcglow" }), h("span", { class: "pcring" }), h("span", { class: "pcring r2" }), h("div", { class: "pcfloat" }, obj));
  }
  /* The monitor leans toward a finger on it, and with the phone's movement. Only in Full: the other levels keep it still. */
  (function leanWithTheHand() {
    let frame = 0, pending = null;
    const lean = (rx, ry) => {
      pending = [rx, ry];
      if (!frame) frame = requestAnimationFrame(() => { frame = 0; document.querySelectorAll(".pcobj").forEach((o) => { o.style.setProperty("--rx", pending[0].toFixed(1) + "deg"); o.style.setProperty("--ry", pending[1].toFixed(1) + "deg"); }); });
    };
    const rest = () => lean(-6, -14);
    const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
    const full = () => !root.dataset.motion;
    document.addEventListener("pointermove", (e) => {
      if (!full() || !e.target.closest) return;
      const stage = e.target.closest(".pcstage"); if (!stage) return;
      const r = stage.getBoundingClientRect();
      lean(clamp(-6 - ((e.clientY - r.top) / r.height - 0.5) * 22, -22, 10), clamp(-14 + ((e.clientX - r.left) / r.width - 0.5) * 44, -40, 14));
    }, { passive: true });
    document.addEventListener("pointerup", () => { if (full()) setTimeout(rest, 500); }, { passive: true });
    window.addEventListener("deviceorientation", (e) => {
      if (!full() || e.gamma == null || !document.querySelector(".pcobj")) return;
      lean(clamp(-6 + (e.beta - 55) * 0.22, -16, 6), clamp(-14 + e.gamma * 0.32, -30, 4));
    }, { passive: true });
  })();
  /* A press on a card or big button leans it toward the finger for as long as it is held (Full only). */
  document.addEventListener("pointerdown", (e) => {
    if (root.dataset.motion || !e.target.closest) return;
    if (e.target.closest("input, textarea, select")) return;
    const t = e.target.closest(".hm-card, .hm-btn, .hm-tile, .plan");
    if (!t) return;
    const r = t.getBoundingClientRect();
    t.style.setProperty("--tx", (-((e.clientY - r.top) / r.height - 0.5) * 5).toFixed(2) + "deg");
    t.style.setProperty("--ty", (((e.clientX - r.left) / r.width - 0.5) * 7).toFixed(2) + "deg");
    t.classList.add("pressing");
    const up = () => { t.classList.remove("pressing"); window.removeEventListener("pointerup", up); window.removeEventListener("pointercancel", up); };
    window.addEventListener("pointerup", up); window.addEventListener("pointercancel", up);
  }, { passive: true });
  /* Graphics and motion in Settings: three levels, a live preview that follows the choice, and one line about what it does. */
  function motionBox() {
    const OPTIONS = [["full", "Full 3D", "Depth, tilt and 3D movement."], ["simple", "Simple", "The same look, flat, with gentle movement."], ["off", "Off", "Nothing moves."]];
    const seg = h("div", { class: "seg", role: "group", "aria-label": "Graphics and motion" });
    const note = h("p", { class: "set-sub gfx-note", role: "status" });
    const paint = () => {
      const level = motionLevel();
      [...seg.children].forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.id === level)));
      note.textContent = OPTIONS.find((o) => o[0] === level)[2] + (["full", "simple", "off"].includes(cfg.motion) ? "" : " Chosen for this phone automatically.");
    };
    OPTIONS.forEach(([id, label]) => {
      const b = h("button", { type: "button", "data-id": id }, label);
      b.onclick = () => { cfg.motion = id; cfg.reduceMotion = id === "off"; saveCfg(); applyLook(); paint(); haptic("tick"); };
      seg.append(b);
    });
    paint();
    return h("div", { class: "set col gfx" },
      h("div", { class: "set-text" }, h("div", { class: "set-label", text: "3D graphics and animation" }),
        h("div", { class: "set-sub", text: "Turn the 3D down on an older phone or to save battery. Simple keeps the same look with softer, flat animation." })),
      seg, pcStage("ok", "", true), note);
  }

  function renderHome() {
    const root = $("#home"); if (!root || S.tab !== "home") return;
    const d = PC.data && PC.data.available ? PC.data : null, u = unlockInfo(), pending = pendingJobs();
    const recent = [...S.jobs].filter((j) => !S.hidden.has(j.conversation)).sort((a, b) => String(b.created_at).localeCompare(String(a.created_at))).slice(0, 3);
    const list = checklist(), listOpen = list.done < list.steps.length && store.raw("afp_checklist_off") !== "1" && !S.offline;
    const readings = d ? [
      ["Processor", d.cpu_percent + "%", d.cpu_percent, tone(d.cpu_percent, 70, 90)],
      ["Memory", d.memory.percent + "%", d.memory.percent, tone(d.memory.percent, 80, 92)],
      ...(d.disk ? [["Disk free", fmtBytes(d.disk.free), d.disk.percent, tone(d.disk.percent, 85, 95)]] : []),
    ] : [];
    const sig = JSON.stringify([S.online, !!S.offline, S.loaded, S.info && [S.info.pc, S.info.power_actions, S.info.screen_dark, (S.info.folders && S.info.folders.granted || []).length, !!(S.info.daily && S.info.daily.enabled)], !!Daily, !!d, !!(d && d.disk), u,
      pending.map((j) => j.id), recent.map((j) => [j.id, j.status]), list.steps.map((s) => s.done), listOpen, netKey()]);
    if (sig === S.homeSig) {
      // Live readings must not replace buttons or steal focus while someone is using Home.
      root.querySelectorAll(".hm-tile").forEach((tile, i) => {
        const reading = readings[i]; if (!reading) return;
        tile.querySelector("b").textContent = reading[1];
        const bar = tile.querySelector(".hm-bar i");
        bar.style.width = `${Math.max(2, Math.min(100, reading[2]))}%`;
        bar.dataset.tone = reading[3];
      });
      const net = root.querySelector(".hm-strip .chip-net");
      if (net) { const nextNet = netChip(); net.className = nextNet.className; net.dataset.level = nextNet.dataset.level || ""; net.setAttribute("aria-label", nextNet.getAttribute("aria-label") || ""); net.replaceChildren(...nextNet.childNodes); }
      const chip = root.querySelector(".hm-strip .chip-pc:not(.chip-net)");
      if (chip) {
        const next = pcChip();
        chip.className = next.className;
        chip.dataset.level = next.dataset.level;
        chip.setAttribute("aria-label", next.getAttribute("aria-label"));
        chip.replaceChildren(...next.childNodes);
      }
      return;
    }
    S.homeSig = sig;
    if (dailyOn()) { if (Daily) Daily.sync(S.info); else loadDaily(); }
    if (!S.pushAsked && S.token && !S.offline) { S.pushAsked = true; Push.load().then(() => { S.homeSig = ""; renderHome(); }).catch(() => {}); }

    const pc = (S.info && S.info.pc) || "Your PC";
    const parts = [];
    parts.push(h("div", { class: "hm-strip" }, h("span", { class: "hm-dot" + (S.online && !S.offline ? "" : " off") }),
      h("div", { class: "hm-name" }, h("b", { text: pc }), h("small", { text: S.offline ? "Offline · reading saved chats" : S.online ? "Online" : "Can't reach it" })), h("span", { class: "grow" }), netChip(), pcChip()));  // Settings lives in the bottom bar; the PC's status opens from the battery chip
    parts.push(h("header", { class: "hm-welcome" }, h("h1", { text: "Your PC, within reach." }),
      h("p", { text: S.offline ? "Your saved chats are still here." : "Check in, find a file, or pick up where you left off." })));
    parts.push(pcStage(S.offline || !S.online ? "offline" : pcLocked() ? "locked" : "ok", pc));

    if (pending.length) {
      const first = pending[0];
      parts.push(h("button", { class: "hm-banner", type: "button", onclick: () => { haptic("light"); openApprovals(); } }, ico("alert"),
        h("div", { class: "grow" }, h("b", { text: pending.length === 1 ? "1 request needs you" : `${pending.length} requests need you` }), h("span", { text: shorten(first.summary || first.prompt) })), ico("chevron")));
    }

    if (!S.offline && !S.loaded && !d && !u) {
      parts.push(h("section", { class: "hm-card sk-card", "aria-busy": "true", "aria-label": "Loading" }, h("div", { class: "hm-pc" }, h("i", { class: "sk circle" }), h("div", { class: "grow" }, h("i", { class: "sk line" }), h("i", { class: "sk line short" }))),
        h("div", { class: "hm-btns" }, h("i", { class: "sk btn2" }), h("i", { class: "sk btn2" })), h("div", { class: "hm-tiles" }, h("i", { class: "sk tile" }), h("i", { class: "sk tile" }), h("i", { class: "sk tile" }))));
      parts.push(h("section", { class: "hm-card sk-card", "aria-hidden": "true" }, h("i", { class: "sk line" }), h("i", { class: "sk line short" })));
      root.replaceChildren(...parts); return;
    }
    const cards = [];
    if (S.offline) {
      cards.push(h("section", { class: "hm-card" }, h("div", { class: "hm-pc" }, h("span", { class: "hm-pcicon warn" }, ico("wifiOff")), h("div", { class: "grow" }, h("b", { text: "Your PC is offline" }), h("small", { text: "Showing chats saved on this phone. New requests wait here until it is back." }))),
        h("div", { class: "hm-btns" }, h("button", { class: "hm-btn primary", type: "button", onclick: () => { haptic("tick"); setTab("chat"); } }, ico("chat"), "Read saved chats"))));
    } else {
      const locked = pcLocked(), buttons = [];
      if (locked && unlockReady()) buttons.push(h("button", { class: "hm-btn primary", type: "button", onclick: () => askPc("unlock my PC") }, ico("unlock"), "Unlock"));
      else if (!locked && canPower("lock")) buttons.push(h("button", { class: "hm-btn primary", type: "button", onclick: () => askPc("lock my PC") }, ico("lock"), "Lock"));
      if (canPower("screen_off")) {
        const dark = !!(S.info && S.info.screen_dark);   // the PC's screen is covered right now: the button gives it back
        buttons.push(h("button", { class: "hm-btn" + (dark ? " primary" : ""), type: "button", onclick: () => askPc(dark ? "turn on my screen" : "black out my screen") }, ico("monitor"), dark ? "Screen on" : "Screen off"));
      }
      if (canPower("sleep")) buttons.push(h("button", { class: "hm-btn", type: "button", onclick: () => askPc("put my PC to sleep") }, ico("moon"), "Sleep"));
      const known = !!d || !!u;
      const title = !known ? "Checking…" : locked ? "Locked" : "Unlocked";
      const sub = !known ? "Asking your PC" : locked ? (unlockReady() ? "Tap Unlock, then approve with your PIN." : "Unlocking isn't set up. Do that on the PC.") : "Ready for requests";
      const tile = (label, value, pct, tn) => h("div", { class: "hm-tile" }, h("small", { text: label }), h("b", { text: value }), h("div", { class: "hm-bar" }, h("i", { style: `width:${Math.max(2, Math.min(100, pct))}%`, "data-tone": tn })));
      cards.push(h("section", { class: "hm-card" }, h("div", { class: "hm-pc" }, h("span", { class: "hm-pcicon " + (!known ? "" : locked ? "warn" : "ok") }, ico(!known ? "gauge" : locked ? "lock" : "unlock")),
        h("div", { class: "grow" }, h("b", { text: title }), h("small", { text: sub }))),
        buttons.length ? h("div", { class: "hm-btns" }, ...buttons) : null,
        d ? h("div", { class: "hm-tiles" }, ...readings.map((r) => tile(...r))) : null));
      const everyday = dailyOn() && Daily ? Daily.homeCard() : null;
      if (everyday) cards.push(everyday);
    }

    if (listOpen) {
      const rows = list.steps.map((s) => h("div", { class: "hm-step" + (s.done ? " done" : "") }, h("span", { class: "mark" }, s.done ? ico("check") : null),
        h("div", { class: "grow" }, h("b", { text: s.title }), h("small", { text: s.sub })), !s.done && s.action ? h("button", { class: "hm-pill", type: "button", onclick: () => { haptic("tick"); s.action[1](); } }, s.action[0]) : null));
      cards.push(h("section", { class: "hm-card" }, h("div", { class: "hm-head" }, h("b", { text: "Get set up" }), h("span", { class: "hm-chip ok", text: `${list.done} of ${list.steps.length} done` })),
        h("div", { class: "hm-progress" }, h("i", { style: `width:${list.done / list.steps.length * 100}%` })), ...rows,
        h("button", { class: "hm-link", type: "button", onclick: () => { store.setRaw("afp_checklist_off", "1"); S.homeSig = ""; renderHome(); } }, "Hide this")));
    }

    if (recent.length) {
      cards.push(h("div", { class: "hm-sect" }, h("b", { text: "Recent" }), h("button", { class: "hm-link", type: "button", onclick: () => { haptic("tick"); setTab("chat"); } }, "Open chat")));
      cards.push(h("section", { class: "hm-card hm-recent" }, ...recent.map((j) => h("button", { class: "hm-row", type: "button", onclick: () => { haptic("tick"); S.conv = j.conversation; store.setRaw("afp_conv", S.conv); setTab("chat"); } },
        h("span", { class: "hm-ric " + (j.status === "done" ? "ok" : j.status === "failed" || j.status === "rejected" ? "bad" : "warn") }, ico(j.status === "done" ? "check" : j.status === "failed" ? "alert" : "clock")),
        h("div", { class: "grow" }, h("b", { text: shorten(j.summary || j.prompt, 48) }), h("small", { text: LABEL[j.status] || j.status })), h("time", { text: ago(j.created_at) })))));
    } else if (S.loaded && !S.offline) {
      cards.push(h("section", { class: "hm-card hm-empty" }, ico("chat"),
        h("div", {}, h("h2", { text: "Start with something simple" }), h("p", { text: "Open Chat and try \"list\" to see your workspace. Your recent requests will appear here." }),
          h("button", { class: "hm-link", type: "button", onclick: () => setTab("chat") }, "Open chat"))));
    }
    root.replaceChildren(...parts, h("div", { class: "hm-body" }, ...cards));
    if (S.homeEnter !== false) {   // the cards rise in once when you arrive at Home, not on every refresh (that would flicker)
      S.homeEnter = false; root.dataset.enter = "1";
      setTimeout(() => { delete root.dataset.enter; }, 1200);
    }
  }

  /* ===================================================================== approvals: everything waiting for you, in one sheet */

  const approvals = { sheet: null, fill: null };
  function openApprovals() {
    if (approvals.sheet) return;
    const body = h("div", { class: "appr" });
    const fill = () => {
      const list = pendingJobs();
      if (!list.length) { if (approvals.sheet) approvals.sheet.close(); toast("Nothing is waiting for you"); return; }
      body.replaceChildren(...list.map((j) => h("section", { class: "appr-item" },
        h("div", { class: "appr-ask" }, ico("chat"), h("span", { text: shorten(j.prompt, 90) }), h("time", { text: ago(j.created_at) })),
        j.status === "needs_access" ? accessCard(j) : planCard(j, false))),
        h("button", { class: "hm-link appr-chat", type: "button", onclick: () => { const first = list[0]; closeSheet(); setTimeout(() => { S.conv = first.conversation; store.setRaw("afp_conv", S.conv); setTab("chat"); }, 200); } }, "Open the chat instead"));
    };
    fill();
    const title = pendingJobs().length > 1 ? "Needs your approval" : "Needs your approval";
    approvals.fill = fill;
    approvals.sheet = openSheet({ title, body, route: "approvals", onClose: () => { approvals.sheet = null; approvals.fill = null; } });
  }

  /* ===================================================================== connected devices: who holds the PC's places, and removing one */

  /** Ask for the PC owner's sign-in and try it with `attempt(creds)` (which returns "" on success or a message). The sheet stays open on a wrong password. Resolves true on success. */
  function askOwner(mode, owner, why, attempt) {
    return new Promise((resolve) => {
      let done = false;
      const user = h("input", { class: "text-input", type: "text", autocomplete: "off", value: owner || "", "aria-label": "Windows user name" });
      const pass = h("input", { class: "text-input", type: "password", autocomplete: "off", placeholder: mode === "windows" ? "Windows password" : "PC password", "aria-label": "Password" });
      const err = h("p", { class: "pin-err", role: "alert", hidden: true });
      const finish = (v) => { if (done) return; done = true; sheet.close(); resolve(v); };
      const go = h("button", { class: "btn danger", type: "submit" }, ico("trash"), "Remove");
      const sheet = openSheet({
        title: "Owner password needed", route: "owner",
        body: h("form", { onsubmit: async (e) => {
          e.preventDefault();
          if (!pass.value) { err.textContent = "Type the password."; err.hidden = false; return; }
          go.disabled = true; err.hidden = true;
          const problem = await attempt({ username: user.value.trim(), password: pass.value });
          go.disabled = false;
          if (!problem) { finish(true); return; }
          err.textContent = problem; err.hidden = false; pass.value = ""; pass.focus(); haptic("error");
        } },
          h("p", { class: "note", text: why }), mode === "windows" ? h("label", { class: "pin-field" }, h("span", { text: "Windows user name" }), user) : null,
          h("label", { class: "pin-field" }, h("span", { text: "Password" }), pass), err,
          h("div", { class: "actions", style: "margin-top:14px" }, go, h("button", { class: "btn ghost", type: "button", onclick: () => finish(false) }, "Cancel"))),
        onClose: () => finish(false),
      });
      setTimeout(() => pass.focus(), 80);
    });
  }

  function buildDevices() {
    const box = h("div", { class: "devs" }, h("div", { class: "sk-list" }, h("i", { class: "sk row" }), h("i", { class: "sk row" }), h("i", { class: "sk row" })));
    const load = async () => {
      let data;
      try { data = await (await api("/devices")).json(); }
      catch (e) { if (e.code !== 401) box.replaceChildren(h("p", { class: "note", text: e.offline ? "Your PC is offline." : e.message })); return; }
      const used = data.devices.length, full = used >= data.limit;
      const meter = h("div", { class: "dev-meter" + (full ? " full" : "") },
        h("div", { class: "dev-meter-top" }, h("b", { text: `${used} of ${data.limit} places used` }), h("small", { text: full ? "Full. Remove a phone to add another." : `${data.limit - used} free` })),
        h("div", { class: "hm-bar" }, h("i", { style: `width:${Math.min(100, used / data.limit * 100)}%`, "data-tone": full ? "bad" : used >= data.limit - 2 ? "warn" : "" })));
      const remove = async (d) => {
        const ok = await ask({ title: `Remove ${d.name}?`, message: "It is signed out of this PC at once, and can only come back with a new pairing code.", ok: "Continue", cancel: "Keep it", tone: "danger", icon: "trash" });
        if (!ok) return;
        const removed = await askOwner(data.mode, data.owner, `Removing another phone needs the PC owner's ${data.mode === "windows" ? "Windows sign-in" : "password"}.`, async (who) => {
          try { await api(`/devices/${d.id}/remove`, { method: "POST", body: JSON.stringify(who) }); return ""; }
          catch (e) { return e.code === 401 ? "You were signed out." : e.message; }
        });
        if (removed) { toast(`${d.name} removed`); haptic("success"); load(); }
      };
      const rows = data.devices.map((d) => {
        const front = h("div", { class: "dev-front" }, h("span", { class: "dev-ic" }, ico("phone")),
          h("div", { class: "grow" }, h("b", { text: d.name + (d.current ? " (this phone)" : "") }), h("small", { text: (d.blocked ? "Blocked · " : "") + "Last seen " + ago(d.last_seen) })),
          d.current ? h("span", { class: "hm-chip ok", text: "You" }) : h("button", { class: "iconbtn", type: "button", "aria-label": "Remove " + d.name, onclick: () => remove(d) }, ico("trash")));
        const row = h("div", { class: "dev" + (d.current ? " me" : "") }, d.current ? null : h("button", { class: "dev-del", type: "button", tabindex: "-1", "aria-hidden": "true", onclick: () => remove(d) }, ico("trash"), "Remove"), front);
        if (!d.current) swipeToReveal(row, front);
        return row;
      });
      box.replaceChildren(meter, h("div", { class: "dev-list" }, ...rows), h("p", { class: "note small", text: "Swipe a phone to the left, or tap the bin, to remove it." }));
    };
    load();
    return [box];
  }

  /** Drag a row to the left to show what is behind it; let go past halfway and it stays open. */
  function swipeToReveal(row, front) {
    const max = 96; let x0 = null, y0 = 0, dx = 0, open = false, moving = false;
    const set = (v, anim) => { front.style.transition = anim ? "transform 0.2s var(--ease)" : "none"; front.style.transform = v ? `translateX(${v}px)` : ""; };
    row.addEventListener("touchstart", (e) => { x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; moving = false; dx = open ? -max : 0; }, { passive: true });
    row.addEventListener("touchmove", (e) => {
      if (x0 === null) return;
      const mx = e.touches[0].clientX - x0, my = e.touches[0].clientY - y0;
      if (!moving && Math.abs(my) > Math.abs(mx)) { x0 = null; return; }  // a scroll, not a swipe
      moving = true;
      set(Math.max(-max, Math.min(0, (open ? -max : 0) + mx)), false);
    }, { passive: true });
    row.addEventListener("touchend", (e) => {
      if (x0 === null) return;
      const end = (open ? -max : 0) + (e.changedTouches[0].clientX - x0);
      open = end < -max / 2; set(open ? -max : 0, true); if (open) haptic("tick"); x0 = null;
    }, { passive: true });
  }

  /* ===================================================================== app lock: a PIN (and fingerprint) before the app shows anything */

  function sha256(text) {
    // a small SHA-256 that works on plain http too (the browser's own is only there on a secure page)
    const bytes = unescape(encodeURIComponent(text)).split("").map((c) => c.charCodeAt(0));
    if (!sha256.K) {
      const primes = []; for (let n = 2; primes.length < 64; n++) if (primes.every((p) => n % p)) primes.push(n);
      const frac = (v) => Math.floor((v - Math.floor(v)) * 4294967296);
      sha256.K = primes.map((p) => frac(Math.cbrt(p))); sha256.H = primes.slice(0, 8).map((p) => frac(Math.sqrt(p)));
    }
    const K = sha256.K; let H = sha256.H.slice();
    const len = bytes.length; bytes.push(0x80); while (bytes.length % 64 !== 56) bytes.push(0);
    const bits = len * 8; for (let i = 7; i >= 0; i--) bytes.push(i >= 4 ? 0 : (bits >>> (i * 8)) & 255);
    const rr = (x, n) => (x >>> n) | (x << (32 - n));
    for (let o = 0; o < bytes.length; o += 64) {
      const w = new Array(64);
      for (let i = 0; i < 16; i++) w[i] = (bytes[o + i * 4] << 24) | (bytes[o + i * 4 + 1] << 16) | (bytes[o + i * 4 + 2] << 8) | bytes[o + i * 4 + 3];
      for (let i = 16; i < 64; i++) { const s0 = rr(w[i - 15], 7) ^ rr(w[i - 15], 18) ^ (w[i - 15] >>> 3), s1 = rr(w[i - 2], 17) ^ rr(w[i - 2], 19) ^ (w[i - 2] >>> 10); w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0; }
      let [a, b, c, d, e, f, g, hh] = H;
      for (let i = 0; i < 64; i++) {
        const t1 = (hh + (rr(e, 6) ^ rr(e, 11) ^ rr(e, 25)) + ((e & f) ^ (~e & g)) + K[i] + w[i]) | 0, t2 = ((rr(a, 2) ^ rr(a, 13) ^ rr(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) | 0;
        hh = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
      }
      H = [H[0] + a, H[1] + b, H[2] + c, H[3] + d, H[4] + e, H[5] + f, H[6] + g, H[7] + hh].map((v) => v | 0);
    }
    return H.map((v) => (v >>> 0).toString(16).padStart(8, "0")).join("");
  }
  const pinHash = (pin, salt) => { let v = salt + ":" + pin; for (let i = 0; i < 2000; i++) v = sha256(v); return v; };

  const Lock = {
    conf: () => store.get("afp_lock", null),
    on() { const c = this.conf(); return !!(c && c.hash); },
    save(c) { store.set("afp_lock", c); },
    off() { store.del("afp_lock"); this.hiddenAt = 0; this.hide(); },
    hiddenAt: 0, unlockedAt: 0, fails: 0, waitUntil: 0, el: null,
    bio: () => !!(window.AfpApp && window.AfpApp.canBiometric && (() => { try { return window.AfpApp.canBiometric(); } catch { return false; } })()),
    check(pin) { const c = this.conf(); return !!c && pinHash(pin, c.salt) === c.hash; },
    hide() { if (this.el) { const el = this.el; this.el = null; this.unlockedAt = Date.now(); this.hiddenAt = 0; S.locked = false; document.body.classList.remove("locked"); el.classList.add("out"); setTimeout(() => el.remove(), 220); } },
    show() {
      if (!this.on() || this.el) return;
      const c = this.conf(); let typed = "";
      S.locked = true; document.body.classList.add("locked");
      const dots = h("div", { class: "lk-dots", "aria-hidden": "true" }, ...Array.from({ length: c.len || 4 }, () => h("i")));
      const msg = h("p", { class: "lk-msg", role: "status", text: "Enter your PIN" });
      const paint = () => [...dots.children].forEach((d, i) => d.classList.toggle("on", i < typed.length));
      const wrong = (text) => { typed = ""; paint(); msg.textContent = text; dots.classList.remove("shake"); void dots.offsetWidth; dots.classList.add("shake"); haptic("error"); };
      const tryPin = () => {
        if (Date.now() < this.waitUntil) { wrong(`Too many tries. Wait ${Math.ceil((this.waitUntil - Date.now()) / 1000)} s.`); return; }
        if (this.check(typed)) { this.fails = 0; haptic("success"); this.hide(); return; }
        this.fails += 1;
        if (this.fails % 5 === 0) { this.waitUntil = Date.now() + Math.min(300, 30 * (this.fails / 5)) * 1000; wrong("Too many tries. Wait a moment."); } else wrong("That PIN is not right.");
      };
      const press = (d) => { if (typed.length >= (c.len || 4)) return; haptic("tick"); typed += d; paint(); msg.textContent = "Enter your PIN"; if (typed.length === (c.len || 4)) setTimeout(tryPin, 90); };
      const key = (label, fn, cls = "") => h("button", { class: "lk-key " + cls, type: "button", onclick: fn, "aria-label": /^[0-9]$/.test(label) ? label : undefined }, label);
      const bioBtn = c.bio && this.bio() ? h("button", { class: "lk-key ghost", type: "button", "aria-label": "Use fingerprint", onclick: () => this.fingerprint() }, ico("scan")) : h("span");
      const back = h("button", { class: "lk-key ghost", type: "button", "aria-label": "Delete", onclick: () => { typed = typed.slice(0, -1); paint(); } }, ico("arrowLeft"));
      const pad = h("div", { class: "lk-pad" }, ...["1", "2", "3", "4", "5", "6", "7", "8", "9"].map((d) => key(d, () => press(d))), bioBtn, key("0", () => press("0")), back);
      this.el = h("div", { class: "lockscr", role: "dialog", "aria-modal": "true", "aria-label": "App locked" },
        h("div", { class: "lk-top" }, glyph(44), h("h1", { text: "Access is locked" }), msg, dots), pad,
        h("button", { class: "hm-link lk-forgot", type: "button", onclick: () => this.forgot() }, "Forgot PIN?"));
      document.body.append(this.el);
      document.addEventListener("keydown", this.keys = (e) => { if (!this.el) return; if (/^[0-9]$/.test(e.key)) press(e.key); else if (e.key === "Backspace") { typed = typed.slice(0, -1); paint(); } });
      if (c.bio && this.bio()) setTimeout(() => this.fingerprint(), 350);
    },
    /** Forgot the PIN: prove you own the PC (its password) and only the app lock is removed, or sign out as the last resort. */
    forgot() {
      if (!this.el || this.el.querySelector(".lk-reset")) return;
      const err = h("p", { class: "pin-err", role: "alert", hidden: true });
      const user = h("input", { class: "text-input", type: "text", autocomplete: "off", "aria-label": "Windows user name" });
      const pass = h("input", { class: "text-input", type: "password", autocomplete: "off", "aria-label": "Password" });
      const userRow = h("label", { class: "pin-field", hidden: true }, h("span", { text: "Windows user name" }), user);
      const passLabel = h("span", { text: "PC password" });
      const go = h("button", { class: "btn primary", type: "submit" }, ico("check"), "Remove the PIN lock");
      const close = () => panel.remove();
      const panel = h("div", { class: "lk-reset" },
        h("form", { class: "lk-reset-box", onsubmit: async (e) => {
          e.preventDefault();
          if (!pass.value) { err.textContent = "Type the password."; err.hidden = false; return; }
          go.disabled = true; err.hidden = true;
          try {
            await api("/owner/check", { method: "POST", body: JSON.stringify({ username: user.value.trim(), password: pass.value }) });
            Lock.off(); haptic("success"); toast("The PIN lock is removed. You can set a new one in Settings.");
          } catch (ex) {
            go.disabled = false; pass.value = ""; haptic("error");
            err.textContent = ex.code === 401 ? "This phone was signed out." : ex.offline ? "Your PC is offline, so it can't check the password. You can sign out instead." : ex.message;
            err.hidden = false;
          }
        } },
          h("h2", { text: "Forgot your PIN?" }),
          h("p", { text: "Type your PC's password. It is checked with your PC, and only the PIN lock is removed. You stay connected and keep your chats." }),
          userRow, h("label", { class: "pin-field" }, passLabel, pass), err,
          go,
          h("button", { class: "btn ghost", type: "button", onclick: close }, "Back to the PIN"),
          h("button", { class: "hm-link", type: "button", onclick: async () => {
            if (await ask({ title: "Sign out instead?", message: "Its saved chats are deleted from this phone. You connect again with a new code, as the same phone.", ok: "Sign out", cancel: "Keep trying", tone: "danger", icon: "logout" })) { Lock.off(); S.skipSignOutAsk = true; signOut(); }
          } }, "Sign out of this phone instead")));
      this.el.append(panel);
      api("/devices").then((r) => r.json()).then((d) => { if (d.mode === "windows") { userRow.hidden = false; user.value = d.owner || ""; passLabel.textContent = "Windows password"; } }).catch(() => {});
      setTimeout(() => pass.focus(), 80);
    },
    fingerprint() {
      if (!this.bio()) return;
      window.__afpBio = (ok) => { window.__afpBio = null; if (ok && this.el) { haptic("success"); this.hide(); } };
      try { window.AfpApp.biometric("Unlock Access"); } catch { window.__afpBio = null; }
    },
  };
  /* Approving a request asks the same question as the app lock does, so a borrowed phone that is already open can't approve anything: the fingerprint (or face) when the
     person turned that on for the lock, the PIN otherwise. Without an app lock, or with "Confirm approvals" off, it is a plain tap as before. */
  Object.assign(Lock, {
    confirming: null,
    approveOn() { const c = this.conf(); return !!(c && c.hash && c.approve !== false); },
    approveBio() { const c = this.conf(); return this.approveOn() && !!c.bio && this.bio(); },
    /** Resolves true when the person proved it is them, false if they cancelled. `reason` says what is being approved. */
    confirm(reason) {
      if (!this.approveOn()) return Promise.resolve(true);
      if (this.confirming) return this.confirming;
      const c = this.conf(), len = c.len || 4, canBio = this.approveBio();
      return (this.confirming = new Promise((resolve) => {
        let typed = "", over = false;
        const dots = h("div", { class: "lk-dots", "aria-hidden": "true" }, ...Array.from({ length: len }, () => h("i")));
        const msg = h("p", { class: "cfm-msg", role: "status", text: canBio ? "Touch the fingerprint sensor, or type your PIN." : "Type your PIN." });
        const paint = () => [...dots.children].forEach((d, i) => d.classList.toggle("on", i < typed.length));
        const finish = (ok) => {
          if (over) return; over = true;
          document.removeEventListener("keydown", onKey); window.__afpBio = null;
          this.confirming = null; this.unlockedAt = Date.now();   // (coming back from the system prompt must not lock the app again)
          card.classList.add(ok ? "ok" : "out"); if (ok) haptic("success");
          setTimeout(() => wrap.remove(), ok ? 420 : 220);
          resolve(ok);
        };
        const wrong = (text) => { typed = ""; paint(); msg.textContent = text; dots.classList.remove("shake"); void dots.offsetWidth; dots.classList.add("shake"); haptic("error"); };
        const tryPin = () => {
          if (Date.now() < this.waitUntil) { wrong(`Too many tries. Wait ${Math.ceil((this.waitUntil - Date.now()) / 1000)} s.`); return; }
          if (this.check(typed)) { this.fails = 0; finish(true); return; }
          this.fails += 1;
          if (this.fails % 5 === 0) { this.waitUntil = Date.now() + Math.min(300, 30 * (this.fails / 5)) * 1000; wrong("Too many tries. Wait a moment."); } else wrong("That PIN is not right.");
        };
        const press = (d) => { if (over || typed.length >= len) return; haptic("tick"); typed += d; paint(); if (typed.length === len) setTimeout(tryPin, 90); };
        const onKey = (e) => { if (/^[0-9]$/.test(e.key)) press(e.key); else if (e.key === "Backspace") { typed = typed.slice(0, -1); paint(); } else if (e.key === "Escape") finish(false); };
        const key = (label, fn, cls = "") => h("button", { class: "lk-key " + cls, type: "button", onclick: fn, "aria-label": /^[0-9]$/.test(label) ? label : undefined }, label);
        const ask = () => {
          if (!canBio || over) return;
          msg.textContent = "Touch the fingerprint sensor, or type your PIN.";
          window.__afpBio = (ok) => { window.__afpBio = null; if (over) return; if (ok) finish(true); else { msg.textContent = "That didn't match. Try again, or type your PIN."; haptic("warning"); } };
          try { window.AfpApp.biometric("Approve this request"); } catch { window.__afpBio = null; }
        };
        const bioBtn = canBio ? h("button", { class: "lk-key ghost", type: "button", "aria-label": "Use fingerprint", onclick: ask }, ico("scan")) : h("span");
        const back = h("button", { class: "lk-key ghost", type: "button", "aria-label": "Delete", onclick: () => { typed = typed.slice(0, -1); paint(); } }, ico("arrowLeft"));
        const pad = h("div", { class: "lk-pad" }, ...["1", "2", "3", "4", "5", "6", "7", "8", "9"].map((d) => key(d, () => press(d))), bioBtn, key("0", () => press("0")), back);
        const card = h("div", { class: "cfm-card", role: "dialog", "aria-modal": "true", "aria-label": "Confirm it is you" },
          h("div", { class: "cfm-fp", "aria-hidden": "true" }, h("span", { class: "cfm-ring" }), h("span", { class: "cfm-ring r2" }), ico(canBio ? "scan" : "lock", 34)),
          h("h2", { text: "Confirm it's you" }), h("p", { class: "cfm-reason", text: reason || "Approve this request" }), dots, msg, pad,
          h("button", { class: "btn ghost cfm-cancel", type: "button", onclick: () => finish(false) }, "Cancel"));
        const wrap = h("div", { class: "cfm" }, card);
        wrap.addEventListener("pointerdown", (e) => { if (e.target === wrap) finish(false); });
        document.body.append(wrap); document.addEventListener("keydown", onKey);
        setTimeout(ask, 280);   // the fingerprint prompt comes by itself; the PIN pad is right there if it doesn't
      }));
    },
  });
  // Coming back to the app after a while behind another one: ask again (after the delay chosen in Settings).
  document.addEventListener("visibilitychange", () => {
    if (!Lock.on()) return;
    if (document.hidden) { if (!Lock.el && !Lock.confirming) Lock.hiddenAt = Date.now(); }  // (the system fingerprint prompt can also make the page "hidden" for a moment: that is not leaving)
    else if (!Lock.el && Date.now() - Lock.unlockedAt > 2500 && Lock.hiddenAt && Date.now() - Lock.hiddenAt >= ((Lock.conf().after || 0) * 1000)) Lock.show();
  });

  /** Choose a PIN (twice). Resolves the PIN, or "" if cancelled. */
  function askNewPin(title = "Choose a PIN") {
    return new Promise((resolve) => {
      let first = "", done = false;
      const input = h("input", { class: "text-input pin-input", type: "password", inputmode: "numeric", pattern: "[0-9]*", maxlength: "6", autocomplete: "off", "aria-label": "PIN" });
      const msg = h("p", { class: "note", text: "4 to 6 digits. You type it each time the app opens (or use your fingerprint)." });
      const go = h("button", { class: "btn primary", type: "submit" }, "Continue");
      const finish = (v) => { if (done) return; done = true; sheet.close(); resolve(v); };
      const sheet = openSheet({
        title, route: "newpin",
        body: h("form", { onsubmit: (e) => {
          e.preventDefault();
          const v = input.value;
          if (!/^[0-9]{4,6}$/.test(v)) { msg.textContent = "Use 4 to 6 digits."; haptic("error"); return; }
          if (!first) { first = v; input.value = ""; msg.textContent = "Type it once more to confirm."; go.textContent = "Save PIN"; input.focus(); return; }
          if (v !== first) { first = ""; input.value = ""; msg.textContent = "Those did not match. Start again."; go.textContent = "Continue"; haptic("error"); return; }
          finish(v);
        } }, msg, h("label", { class: "pin-field" }, h("span", { text: "PIN" }), input), h("div", { class: "actions", style: "margin-top:14px" }, go, h("button", { class: "btn ghost", type: "button", onclick: () => finish("") }, "Cancel"))),
        onClose: () => finish(""),
      });
      setTimeout(() => input.focus(), 80);
    });
  }
  function askCurrentPin(why) {
    return new Promise((resolve) => {
      let done = false;
      const input = h("input", { class: "text-input pin-input", type: "password", inputmode: "numeric", maxlength: "6", autocomplete: "off", "aria-label": "Current PIN" });
      const err = h("p", { class: "pin-err", role: "alert", hidden: true });
      const finish = (v) => { if (done) return; done = true; sheet.close(); resolve(v); };
      const sheet = openSheet({
        title: "Enter your PIN", route: "curpin",
        body: h("form", { onsubmit: (e) => { e.preventDefault(); if (Lock.check(input.value)) finish(true); else { err.textContent = "That PIN is not right."; err.hidden = false; input.value = ""; haptic("error"); } } },
          h("p", { class: "note", text: why }), h("label", { class: "pin-field" }, h("span", { text: "Current PIN" }), input), err,
          h("div", { class: "actions", style: "margin-top:14px" }, h("button", { class: "btn primary", type: "submit" }, "Continue"), h("button", { class: "btn ghost", type: "button", onclick: () => finish(false) }, "Cancel"))),
        onClose: () => finish(false),
      });
      setTimeout(() => input.focus(), 80);
    });
  }

  function buildLock() {
    const box = h("div", { class: "lockset" });
    const paint = () => {
      const c = Lock.conf(), on = Lock.on();
      const rows = [card(setRow({ icon: "lock", label: on ? "App lock is on" : "App lock is off", sub: on ? "A PIN is asked when the app opens." : "Ask for a PIN when the app opens, so a borrowed phone can't use it.",
        control: h("span", { class: "hm-chip " + (on ? "ok" : ""), text: on ? "On" : "Off" }) }))];
      if (!on) rows.push(h("div", { class: "actions" }, h("button", { class: "btn primary", type: "button", onclick: async () => {
        const pin = await askNewPin(); if (!pin) return;
        const salt = Math.random().toString(36).slice(2) + Date.now().toString(36);
        Lock.save({ salt, hash: pinHash(pin, salt), len: pin.length, bio: false, after: 0 }); haptic("success"); toast("App lock is on"); paint(); if (S.refillSettings) S.refillSettings();
      } }, ico("lock"), "Set a PIN")));
      else {
        const after = h("div", { class: "seg", role: "group", "aria-label": "Lock again after" });
        [[0, "At once"], [60, "1 min"], [300, "5 min"], [900, "15 min"]].forEach(([v, t]) => {
          const b = h("button", { type: "button", "aria-pressed": String((c.after || 0) === v) }, t);
          b.onclick = () => { Lock.save({ ...Lock.conf(), after: v }); [...after.children].forEach((x) => x.setAttribute("aria-pressed", "false")); b.setAttribute("aria-pressed", "true"); haptic("tick"); };
          after.append(b);
        });
        const items = [h("div", { class: "set col" }, h("div", { class: "set-text" }, h("div", { class: "set-label", text: "Lock again after" }), h("div", { class: "set-sub", text: "How long the app can be out of sight before it asks again." })), after)];
        if (Lock.bio()) {
          const t = h("button", { class: "toggle", type: "button", role: "switch", "aria-checked": String(!!c.bio), "aria-label": "Use fingerprint" });
          t.onclick = () => { const cur = Lock.conf(); Lock.save({ ...cur, bio: !cur.bio }); t.setAttribute("aria-checked", String(!cur.bio)); haptic("tick"); };
          items.push(setRow({ icon: "scan", label: "Use fingerprint or face", sub: "Open the app with your phone's own unlock. The PIN always works too.", control: t }));
        }
        {
          const t2 = h("button", { class: "toggle", type: "button", role: "switch", "aria-checked": String(c.approve !== false), "aria-label": "Confirm approvals" });
          t2.onclick = () => { const cur = Lock.conf(); const next = cur.approve === false; Lock.save({ ...cur, approve: next }); t2.setAttribute("aria-checked", String(next)); haptic("tick"); };
          items.push(setRow({ icon: "check", label: "Confirm approvals", sub: "Approving a request asks for your fingerprint or face (if that is on above), or your PIN. A borrowed phone that is already open can't approve anything.", control: t2 }));
        }
        rows.push(card(...items));
        rows.push(h("div", { class: "actions" },
          h("button", { class: "btn", type: "button", onclick: async () => { if (!(await askCurrentPin("Type your current PIN to change it."))) return; const pin = await askNewPin("Choose a new PIN"); if (!pin) return; const salt = Math.random().toString(36).slice(2) + Date.now().toString(36); Lock.save({ ...Lock.conf(), salt, hash: pinHash(pin, salt), len: pin.length }); haptic("success"); toast("PIN changed"); } }, "Change PIN"),
          h("button", { class: "btn danger", type: "button", onclick: async () => { if (!(await askCurrentPin("Type your PIN to turn the lock off."))) return; Lock.off(); haptic("medium"); toast("App lock is off"); paint(); if (S.refillSettings) S.refillSettings(); } }, "Turn off")));
        rows.push(h("p", { class: "note small", text: "The PIN stays on this phone. It keeps casual snoopers out; it is not a replacement for your phone's own screen lock." }));
      }
      box.replaceChildren(...rows);
    };
    paint();
    return [box];
  }

  /* ===================================================================== first-run tour */

  const TOUR_KEY = "afp_onboarded";
  const tourDone = () => !!store.raw(TOUR_KEY);

  /* a little phone that plays the whole idea on a loop: ask, review the plan, approve, undo */
  function demoSlide(register) {
    const text = h("span", { class: "dm-text" });
    const screen = h("div", { class: "dm-screen" },
      h("div", { class: "dm-me" }, text, h("i", { class: "dm-caret" })),
      h("div", { class: "dm-think" }, h("i"), h("i"), h("i")),
      h("div", { class: "dm-plan" }, h("b", { text: "Here's my plan" }),
        h("ul", {}, h("li", { text: "Find 3 backup files in Reports" }), h("li", { text: "Move them to Archive" })),
        h("div", { class: "dm-actions" }, h("span", { class: "dm-no", text: "Reject" }), h("span", { class: "dm-yes", text: "Approve" })),
        h("i", { class: "dm-finger" })),
      h("div", { class: "dm-done" }, ico("check"), h("span", { text: "Done. 3 files moved." }), h("em", { class: "dm-undo", text: "Undo" })));
    const captions = ["Ask in plain words", "Review the plan", "Tap Approve", "Undo if you change your mind"];
    const steps = captions.map((c, i) => h("li", {}, h("b", { text: String(i + 1) }), h("span", { text: c })));
    const say = "Move my backup files from Reports to Archive";
    let timers = [], typing = 0;
    const later = (ms, fn) => timers.push(setTimeout(fn, ms));
    const cap = (n) => steps.forEach((s, i) => s.classList.toggle("on", i === n));
    const play = () => {
      screen.className = "dm-screen"; text.textContent = ""; cap(0);
      let i = 0; clearInterval(typing);
      later(500, () => { typing = setInterval(() => { text.textContent = say.slice(0, ++i); if (i >= say.length) clearInterval(typing); }, 34); });
      later(2400, () => screen.classList.add("s-think"));
      later(3500, () => { screen.classList.remove("s-think"); screen.classList.add("s-plan"); cap(1); });
      later(5300, () => { screen.classList.add("s-tap"); cap(2); });
      later(6300, () => { screen.classList.remove("s-plan"); screen.classList.add("s-done"); cap(3); });
      later(10200, play);
    };
    if (window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches) { text.textContent = say; screen.classList.add("s-plan"); cap(1); }
    else play();
    register(() => { timers.forEach(clearTimeout); clearInterval(typing); });
    return h("div", { class: "demo" }, h("div", { class: "dm-phone" }, h("i", { class: "dm-notch" }), screen), h("ol", { class: "dm-steps" }, steps));
  }

  function openOnboarding({ first = false } = {}) {
    if ($(".onb")) return;
    const pc = (S.info && S.info.pc) || "your PC";
    const overlay = h("div", { class: "onb", role: "dialog", "aria-modal": "true", "aria-label": "Welcome tour" });
    let index = 0, cleanup = null, picked = "";
    const keep = (fn) => { cleanup = fn; };
    const point = (icon, label) => h("li", {}, ico(icon), h("span", { text: label }));
    const head = (title, lead) => [h("h1", { text: title }), h("p", { class: "lead", text: lead })];

    const welcome = () => {
      const mark = h("div", { class: "check", "aria-hidden": "true" });
      mark.innerHTML = '<svg viewBox="0 0 64 64" width="64" height="64"><circle class="disc" cx="32" cy="32" r="30"/><path d="M19 33l9 9 17-19"/></svg>';
      return h("div", { class: "slide-in" }, h("div", { class: "orb-wrap onb-orb" }, orbSVG(), mark),
        h("span", { class: "onb-pill" }, h("i", { class: "dot" }), h("span", { text: "Connected to " + pc })),
        ...head("You're in.", "Let's take a quick look at how this works, then set it up your way. About 30 seconds."),
        h("ul", { class: "onb-points" }, point("shield", "Every change waits for your tap"), point("undo", "Anything can be undone"), point("lock", "Your files stay on your PC")));
    };
    const how = () => h("div", { class: "slide-in" }, ...head("Just say what you need", "Your PC plans it, you approve it. Nothing happens behind your back."), demoSlide(keep));
    const about = () => {
      const name = h("input", { class: "onb-input", type: "text", value: cfg.name || "", maxlength: "30", placeholder: "Your first name", autocomplete: "given-name", "aria-label": "What should I call you?" });
      name.addEventListener("input", () => { cfg.name = name.value.trim(); saveCfg(); });
      const voiceOk = Speech.status() === "ok";
      const mode = h("div", { class: "onb-choice", role: "radiogroup", "aria-label": "How will you talk to your PC?" });
      const options = [["voice", "mic", "Voice", voiceOk ? "Hold the button and speak" : "Needs the secure link and a mic"], ["type", "keyboard", "Type", "Write it like a message"]];
      const paint = () => [...mode.children].forEach((b) => b.setAttribute("aria-checked", String(b.dataset.v === (S.mode === "voice" ? "voice" : "type"))));
      options.forEach(([v, icon, label, sub]) => {
        const b = h("button", { class: "opt", type: "button", role: "radio", "data-v": v, disabled: v === "voice" && !voiceOk }, ico(icon, 26), h("b", { text: label }), h("small", { text: sub }));
        b.onclick = () => { cfg.input = v; saveCfg(); chooseMode(); renderDock(); haptic("light"); paint(); };
        mode.append(b);
      });
      paint();
      const buzz = h("button", { class: "toggle", type: "button", role: "switch", "aria-checked": String(!!cfg.haptics), "aria-label": "Vibration feedback" });
      buzz.onclick = () => { cfg.haptics = !cfg.haptics; saveCfg(); buzz.setAttribute("aria-checked", String(cfg.haptics)); haptic("medium"); };
      return h("div", { class: "slide-in" }, ...head("Make it yours", "A few quick choices. You can change any of them later in Settings."),
        h("label", { class: "onb-field" }, h("span", { text: "What should I call you?" }), name), h("div", { class: "onb-label", text: "How will you talk to your PC?" }), mode,
        h("div", { class: "onb-row" }, ico("vibrate"), h("div", {}, h("b", { text: "Vibration" }), h("small", { text: "A tiny buzz when you tap and when things finish" })), buzz));
    };
    const look = () => {
      const themes = h("div", { class: "onb-themes", role: "radiogroup", "aria-label": "Theme" });
      [["dark", "Dark"], ["light", "Light"], ["system", "Auto"]].forEach(([v, label]) => {
        const b = h("button", { class: "theme t-" + v, type: "button", role: "radio", "aria-checked": String(cfg.theme === v), "data-v": v }, h("i", { class: "swatch-preview" }, h("s"), h("s"), h("s")), h("span", { text: label }));
        b.onclick = () => { cfg.theme = v; saveCfg(); applyLook(); haptic("light"); [...themes.children].forEach((c) => c.setAttribute("aria-checked", String(c.dataset.v === v))); };
        themes.append(b);
      });
      const accents = h("div", { class: "onb-accents", role: "radiogroup", "aria-label": "Accent colour" });
      ACCENTS.forEach(([id, c1, c2]) => {
        const b = h("button", { class: "acc", type: "button", role: "radio", "aria-label": id, "aria-checked": String(cfg.accent === id), "data-v": id, style: `background:linear-gradient(135deg,${c1},${c2})` });
        b.onclick = () => { cfg.accent = id; saveCfg(); applyLook(); haptic("light"); [...accents.children].forEach((c) => c.setAttribute("aria-checked", String(c.dataset.v === id))); };
        accents.append(b);
      });
      return h("div", { class: "slide-in" }, ...head("Pick your look", "Tap one. The whole screen changes as you choose."),
        h("div", { class: "onb-label", text: "Theme" }), themes, h("div", { class: "onb-label", text: "Accent colour" }), accents,
        h("div", { class: "onb-sample" }, h("span", { class: "dot" }), h("b", { text: "This is how buttons and highlights will look" })));
    };
    const safe = () => {
      const f = S.info && S.info.folders;
      const list = h("div", { class: "onb-folders" });
      if (!f) list.append(h("i", { class: "sk row" }), h("i", { class: "sk row" }));
      else {
        const rows = [...f.writable.map((p) => [p, "Can change", "pencil"]), ...f.read_only.map((p) => [p, "Read only", "eye"]), ...(f.granted_details || []).map((g) => [g.path, g.change ? "Can change" : "Read only", g.change ? "pencil" : "eye"])];
        if (!rows.length) list.append(h("p", { class: "onb-none", text: "No folders are open yet. I'll ask you the first time I need one." }));
        rows.slice(0, 5).forEach(([p, tag, icon]) => list.append(h("div", { class: "frow" }, ico("folder"), h("span", { class: "fp", text: p }), h("em", { class: "ftag " + (icon === "pencil" ? "w" : "r") }, ico(icon, 14), tag))));
        if (rows.length > 5) list.append(h("p", { class: "onb-none", text: "and " + (rows.length - 5) + " more" }));
      }
      return h("div", { class: "slide-in" }, ...head("You decide what I can touch", "These are the only folders I can use on " + pc + " right now."), list,
        h("ul", { class: "onb-points" }, point("lock", "Anywhere else, I ask first. You can say once, or always."), point("shield", "Secrets such as passwords and keys are never reachable"), point("undo", "Every change is saved first, so Undo always works")));
    };
    const ready = () => {
      const chips = h("div", { class: "onb-chips" });
      ["What files are in my workspace?", "Create notes.txt with buy milk", "What can you do?"].forEach((t) => { const c = h("button", { class: "chip-try", type: "button" }, ico("sparkle", 16), t); c.onclick = () => { picked = t; finish(); }; chips.append(c); });
      return h("div", { class: "slide-in" }, h("div", { class: "onb-burst", "aria-hidden": "true" }, h("i"), h("i"), h("i"), h("i"), h("i"), h("i"), ico("check", 44)),
        ...head("You're all set", "Try one of these, or just say what you need."), chips, h("p", { class: "onb-tip", text: "You can replay this tour any time in Settings." }));
    };

    const steps = [[welcome, "Show me how"], [how, "Next"], [about, "Next"], [look, "Next"], [safe, "Looks good"], [ready, "Start chatting"]];
    const segs = steps.map(() => h("i", {}));
    const stage = h("div", { class: "onb-stage" });
    const back = h("button", { class: "onb-back", type: "button", "aria-label": "Back" }, ico("chevron"));
    const skip = h("button", { class: "onb-skip", type: "button", text: first ? "Skip" : "Close" });
    const next = h("button", { class: "btn primary onb-next", type: "button" });

    function show(to, dir) {
      if (cleanup) { cleanup(); cleanup = null; }
      const old = stage.firstElementChild;
      const slide = h("section", { class: "slide " + (dir > 0 ? "from-right" : dir < 0 ? "from-left" : ""), "aria-live": "polite" }, steps[to][0]());
      if (old) { old.classList.add(dir >= 0 ? "to-left" : "to-right"); setTimeout(() => old.remove(), 380); }
      stage.append(slide);
      index = to;
      segs.forEach((s, i) => s.classList.toggle("on", i <= to));
      back.style.visibility = to ? "visible" : "hidden";
      next.textContent = steps[to][1];
      if (to === steps.length - 1) next.prepend(ico("sparkle", 18));
    }
    function go(delta) {
      const to = index + delta;
      if (to < 0) return;
      haptic("tick");
      if (to >= steps.length) { finish(); return; }
      show(to, delta);
    }
    function finish() {
      if (cleanup) { cleanup(); cleanup = null; }
      store.setRaw(TOUR_KEY, "1"); haptic("success");
      document.body.classList.add("reveal"); overlay.classList.add("out");
      document.removeEventListener("keydown", onKey);
      setTimeout(() => { overlay.remove(); document.body.classList.remove("reveal"); update(true); if (picked) send(picked); }, 600);
    }
    const onKey = (e) => {
      if (e.key === "ArrowRight") go(1); else if (e.key === "ArrowLeft") go(-1); else if (e.key === "Escape") finish();
    };
    back.onclick = () => go(-1); next.onclick = () => go(1); skip.onclick = finish;
    document.addEventListener("keydown", onKey);
    let x0 = null;
    stage.addEventListener("touchstart", (e) => { x0 = e.touches[0].clientX; }, { passive: true });
    stage.addEventListener("touchend", (e) => { if (x0 === null) return; const dx = e.changedTouches[0].clientX - x0; x0 = null; if (Math.abs(dx) > 70) go(dx < 0 ? 1 : -1); }, { passive: true });

    overlay.append(h("div", { class: "onb-col" }, h("header", { class: "onb-top" }, back, h("div", { class: "onb-prog", "aria-hidden": "true" }, segs), skip), stage, h("footer", { class: "onb-foot" }, next)));
    $("#layer").append(overlay);
    show(0, 0);
    next.focus({ preventScroll: true });
  }

  async function openScanner(onFound) {
    if (window.AfpApp && window.AfpApp.scan) { window.AfpApp.scan(); return; }  // the phone app scans natively, then reloads with the code
    const video = h("video", { playsinline: "", muted: "", autoplay: "" });
    video.muted = true;
    const hint = h("p", { class: "scan-hint", text: "Point the camera at the QR code on your PC" });
    let stream = null, alive = true;
    const finish = (found) => {
      if (!alive) return;
      alive = false;
      if (stream) stream.getTracks().forEach((t) => t.stop());
      view.classList.add("out"); setTimeout(() => view.remove(), 260);
      if (found) onFound(found);
    };
    const view = h("div", { class: "scan", role: "dialog", "aria-modal": "true", "aria-label": "Scan the QR code" }, video,
      h("div", { class: "scan-frame" }, h("i"), h("i"), h("i"), h("i"), h("span", { class: "scan-line" })),
      h("button", { class: "iconbtn scan-x", type: "button", "aria-label": "Close scanner", onclick: () => finish(null) }, ico("x")),
      hint, h("button", { class: "btn scan-alt", type: "button", onclick: () => finish(null) }, ico("keyboard"), "Type the token instead"));
    $("#layer").append(view);
    haptic("light");

    if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      finish(null); toast("The camera needs the secure link. Open the https link on your PC's screen first."); return;
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 } }, audio: false });
    } catch (e) {
      finish(null); toast(e && e.name === "NotAllowedError" ? "The camera is blocked. Allow it for this page, or type the token." : "No camera was found.");
      haptic("warning"); return;
    }
    if (!alive) { stream.getTracks().forEach((t) => t.stop()); return; }
    video.srcObject = stream;
    try { await video.play(); } catch { /* it starts when the first frame arrives */ }

    let detector = null;
    if ("BarcodeDetector" in window) { try { detector = new BarcodeDetector({ formats: ["qr_code"] }); } catch { detector = null; } }
    if (!detector) await loadScript("/static/vendor/jsQR.js");
    const canvas = document.createElement("canvas"), ctx = canvas.getContext("2d", { willReadFrequently: true });
    let last = 0, busy = false;
    const tick = async (now) => {
      if (!alive) return;
      if (!busy && now - last > 110 && video.readyState >= 2 && video.videoWidth) {
        last = now; busy = true;
        let text = "";
        try {
          if (detector) { const codes = await detector.detect(video); text = codes[0] ? codes[0].rawValue : ""; }
          else if (window.jsQR) {
            canvas.width = 640; canvas.height = Math.round(640 * video.videoHeight / video.videoWidth);
            ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
            const img = ctx.getImageData(0, 0, canvas.width, canvas.height);
            const hit = window.jsQR(img.data, img.width, img.height, { inversionAttempts: "attemptBoth" });
            text = hit ? hit.data : "";
          }
        } catch { /* a bad frame: try the next one */ }
        busy = false;
        if (text) {
          const found = readScan(text);
          if (found) { haptic("success"); view.classList.add("hit"); hint.textContent = "Got it"; setTimeout(() => finish(found), 380); return; }
          if (!hint.dataset.bad) {
            hint.dataset.bad = "1"; hint.textContent = "That QR code isn't from AccessFromPhone. Use the one on your PC."; haptic("warning");
            setTimeout(() => { hint.textContent = "Point the camera at the QR code on your PC"; delete hint.dataset.bad; }, 2400);
          }
        }
      }
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  async function renamePhone() {
    const now = S.info && S.info.device ? S.info.device.name : "";
    const name = ((await askText("Name this phone", now, "Rename")) || "").trim();
    if (!name || name === now) return;
    try {
      await api("/device/rename", { method: "POST", body: JSON.stringify({ name }) });
      store.setRaw("afp_device", name); toast("Renamed"); haptic("success"); loadInfo();
    } catch (e) { if (e.code !== 401) toast(e.message); }
  }

  /** Close every pop-up and panel at once (signing out was just confirmed, so unsaved edits are dropped), and the chat entry too, leaving the history at Home. */
  function closeAllSheets() {
    let pushed = 0;
    while (S.sheets.length) { const rec = removeTopSheet(); if (rec && rec.pushed) pushed += 1; }
    if (S.chatPushed) { S.chatPushed = false; pushed += 1; }
    if (pushed) { ignorePops += 1; history.go(-pushed); }
    closeDrawer(); setTab("home", { push: false });
  }

  async function signOut() {
    const already = S.skipSignOutAsk; S.skipSignOutAsk = false;
    if (!already && !(await ask({ title: "Sign out of this phone?", message: "Its saved chats are deleted from this phone and it can't reach the PC until you connect again with a new code. The PC keeps its entry, so connecting again is still this same phone.", ok: "Sign out", cancel: "Stay signed in", tone: "danger", icon: "logout" }))) return;
    Lock.off(); closeAllSheets();
    // With accounts on, this phone is removed from the PC's list, so its token is dead even if someone copied it.
    if (S.info && S.info.accounts) { try { await api("/device", { method: "DELETE" }); } catch { /* already gone, or the PC is off: forget it here anyway */ } }
    S.signedOut = false;
    store.del("afp_push");  // this phone is no longer the PC's: drop its browser notification address too
    store.del("afp_notice");
    S.wasLost = false; S.pcVersion = "";
    if (Link) Link.reset();  // and there is no PC to lose any more
    if ("serviceWorker" in navigator) navigator.serviceWorker.getRegistration().then((r) => r && r.pushManager.getSubscription()).then((s) => s && s.unsubscribe()).catch(() => {});
    Saved.clear();  // signing out takes the saved chats off this phone too
    PC.data = null; store.del("afp_token"); S.token = ""; S.jobs = []; S.audit = []; S.loaded = false; S.info = null; haptic("medium"); showAuth();
    if (window.AfpApp && window.AfpApp.forget) setTimeout(() => window.AfpApp.forget(), 50);  // the phone app: back to its "connect to your PC" screen
  }

  /* ===================================================================== notifications */

  // "Plan ready, tap to approve" on a locked phone. Browser push (the page on the home screen) or Firebase push (the Android app):
  // the PC says which it can send. The phone only hands over an address; nothing about your requests is stored by anyone else.
  const Push = {
    info: null,
    native: !!(window.AfpApp && window.AfpApp.enablePush),
    async load() { try { this.info = await (await api("/push")).json(); } catch (e) { if (e.code === 401) throw e; this.info = null; } return this.info; },
    /** Why notifications can't be turned on here, or "" when they can. */
    why() {
      const i = this.info;
      if (!i) return "Checking…";
      if (!i.enabled) return "Not set up on this PC yet" + (i.why ? ": " + i.why : " (push: in config.yaml).");
      if (this.native) return i.fcm ? "" : "This PC isn't set up to notify the app yet.";
      const apple = /iPhone|iPad|iPod/.test(navigator.userAgent);
      const installed = matchMedia("(display-mode: standalone)").matches || navigator.standalone;
      if (apple && !installed) return "On iPhone, add this page to your Home Screen first (Share, then Add to Home Screen), then open it from there.";
      if (!window.isSecureContext || !("serviceWorker" in navigator) || !("PushManager" in window)) return "This browser needs a trusted https link for notifications. Your own address on the internet works; so does the Android app.";
      if (!i.web) return "Browser notifications aren't set up on this PC.";
      if (typeof Notification !== "undefined" && Notification.permission === "denied") return "Blocked for this site. Allow notifications in the browser's site settings, then come back.";
      return "";
    },
    on() { return !!(this.info && this.info.subscribed && this.info.subscribed.length); },
    bytes(text) { const raw = atob(text.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (text.length % 4)) % 4)); return Uint8Array.from(raw, (c) => c.charCodeAt(0)); },
    async enable() {
      if (this.native) { window.AfpApp.enablePush(); return; }  // the app asks Android for permission, then answers with an "afp-push" event
      const permission = await Notification.requestPermission();
      if (permission !== "granted") throw new Error("Notifications were not allowed.");
      const reg = await navigator.serviceWorker.ready;
      const key = this.bytes(this.info.web);
      let sub = await reg.pushManager.getSubscription();
      if (sub && sub.options && sub.options.applicationServerKey && String(new Uint8Array(sub.options.applicationServerKey)) !== String(key)) { await sub.unsubscribe(); sub = null; }  // the PC's keys changed
      if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
      await this.send({ kind: "web", ...sub.toJSON() });
    },
    async send(body) { const r = await (await api("/push/subscribe", { method: "POST", body: JSON.stringify(body) })).json(); this.info.subscribed = r.subscribed; store.setRaw("afp_push", "1"); },
    async disable() {
      store.del("afp_push");
      if ("serviceWorker" in navigator) { try { const reg = await navigator.serviceWorker.getRegistration(); const sub = reg && (await reg.pushManager.getSubscription()); if (sub) await sub.unsubscribe(); } catch { /* nothing to remove */ } }
      if (window.AfpApp && window.AfpApp.disablePush) window.AfpApp.disablePush();
      try { const r = await (await api("/push/unsubscribe", { method: "POST", body: JSON.stringify({}) })).json(); if (this.info) this.info.subscribed = r.subscribed; } catch (e) { if (e.code === 401) return; throw e; }
    },
    /** On every start: if it was on, make sure the PC still has this phone's current address (browsers and Firebase change it now and then). */
    async refreshQuietly() {
      if (!S.token || store.raw("afp_push") !== "1") return;
      try { await this.load(); if (this.info && this.info.enabled && !this.why()) { if (this.native || Notification.permission === "granted") await this.enable(); } } catch { /* try again next start */ }
    },
  };
  // The Android app answers enablePush() (and a changed token) with this event.
  window.addEventListener("afp-push", async (event) => {
    const d = event.detail || {};
    if (d.error) { toast(d.error); return; }
    if (!d.token || !S.token) return;
    try { if (!Push.info) await Push.load(); await Push.send({ kind: "fcm", token: d.token }); toast("Notifications are on"); haptic("success"); } catch (e) { toast("Couldn't turn notifications on: " + e.message); }
    refreshNotifyRow();
  });

  /** Open the request a notification was about. */
  async function openJob(id) {
    if (!S.token || !id) return;
    let job = S.jobs.find((j) => j.id === id);
    if (!job) { await refresh(); job = S.jobs.find((j) => j.id === id); }
    if (!job) { toast("That request is no longer on this PC"); return; }
    closeSheet(); closeDrawer();
    S.hidden.delete(job.conversation);
    if (job.conversation !== S.conv) { S.conv = job.conversation; store.setRaw("afp_conv", S.conv); }
    S.stick = true;
    setTimeout(() => { setTab("chat"); update(true); }, S.sheets.length || CV.rec ? 160 : 0);  // (after a closing pop-up has finished stepping Back)
  }
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.addEventListener("message", (event) => {
      const m = event.data || {};
      rememberNotice(m.kind);  // "your PC is going to sleep": kept, so if the PC then goes quiet the popup can say why
      if (m.type === "open") openJob(m.job);
      else if (m.type === "push") { haptic("success"); refresh(); }  // the page is open: it just updates, no banner
    });
    // The page's files are kept for offline use, and notifications need this. The Android app uses its own notifications instead.
    if (!window.AfpApp && window.isSecureContext) window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => { /* an untrusted certificate: no notifications, everything else works */ }));
  }
  window.addEventListener("afp-nudge", (event) => { rememberNotice(event.detail && event.detail.kind); haptic("success"); refresh(); });  // the app got a notification while open: just update
  window.addEventListener("hashchange", () => {
    const m = /^#\/?job\/([\w-]+)$/.exec(location.hash);
    if (m) { history.replaceState(history.state, "", location.pathname); openJob(m[1]); }
  });

  /* ===================================================================== sheets: settings, language, activity, help */

  const setIco = (name) => h("span", { class: "set-ico" }, ico(name));
  function setRow({ icon, label, sub, control, onclick, col }) {
    const kids = [icon ? setIco(icon) : null, h("div", { class: "set-text" }, h("div", { class: "set-label", text: label }), sub ? h("div", { class: "set-sub", text: sub }) : null), control || null];
    return onclick ? h("button", { class: "set", type: "button", onclick }, kids, ico("chevron")) : h("div", { class: "set" + (col ? " col" : "") }, kids);
  }
  function toggleRow(key, label, sub, icon, after) {
    const b = h("button", { class: "toggle", type: "button", role: "switch", "aria-checked": String(!!cfg[key]), "aria-label": label });
    b.onclick = () => { cfg[key] = !cfg[key]; saveCfg(); b.setAttribute("aria-checked", String(cfg[key])); haptic("tick"); if (after) after(); };
    return setRow({ icon, label, sub, control: b });
  }
  function segRow(key, label, options, after, icon, sub) {
    const wrap = h("div", { class: "seg", role: "group", "aria-label": label });
    options.forEach(([value, text]) => {
      const b = h("button", { type: "button", "aria-pressed": String(cfg[key] === value) }, text);
      b.onclick = () => { cfg[key] = value; saveCfg(); [...wrap.children].forEach((c) => c.setAttribute("aria-pressed", "false")); b.setAttribute("aria-pressed", "true"); haptic("tick"); if (after) after(value); };
      wrap.append(b);
    });
    return h("div", { class: "set col" }, h("div", { class: "set-text" }, h("div", { class: "set-label", text: label }), sub ? h("div", { class: "set-sub", text: sub }) : null), wrap);
  }
  const card = (...rows) => h("div", { class: "card" }, rows);
  let notifyPaint = null;
  const refreshNotifyRow = () => { if (notifyPaint) notifyPaint(); };
  /** The notifications switch. It asks the PC what is possible, and says plainly when it isn't (and what to do about it). */
  function notifyRow() {
    const row = h("div");
    const paint = () => {
      const reason = Push.why(), on = Push.on() && !reason;
      const b = h("button", { class: "toggle", type: "button", role: "switch", "aria-checked": String(on), "aria-label": "Notifications" });
      if (reason && !on) b.disabled = true;
      b.onclick = async () => {
        b.disabled = true;
        try {
          if (on) { await Push.disable(); toast("Notifications are off"); }
          else { await Push.enable(); if (!Push.native) toast("Notifications are on"); }
          haptic("tick");
        } catch (e) { toast(e.message || "Couldn't change notifications"); }
        paint();
      };
      const test = on ? setRow({ icon: "bell", label: "Send a test", sub: "Lock your phone first to see it arrive.", onclick: async () => {
        try { const r = await (await api("/push/test", { method: "POST" })).json(); toast(r.sent ? "Sent. It should arrive in a moment." : r.errors[0] || "Nothing was sent"); } catch (e) { toast(e.message || "Couldn't send"); }
      } }) : null;
      row.replaceChildren(setRow({ icon: "bell", label: "Notifications", sub: reason || (on ? "On for this phone." : "Tell me when a plan is ready or a job finishes."), control: b }), test);
    };
    notifyPaint = paint;
    paint();
    Push.load().then(paint).catch(() => {});
    return row;
  }
  const sect = (text) => h("div", { class: "sect", text });
  const kv = (k, v) => h("div", { class: "kv" }, h("span", { text: k }), h("span", { text: v }));

  function micStatus() {
    const s = Speech.status();
    return s === "ok" ? ["ok", "Ready"] : s === "insecure" ? ["warn", "Needs https"] : ["bad", "Not supported"];
  }

  /* ===================================================================== Settings: a short list of modules, each with its own small page */

  function buildDevice() {
    const info = S.info;
    const nameInput = h("input", { class: "text-input", type: "text", value: cfg.name || "", placeholder: (info && info.user) || "Your name", maxlength: "40", "aria-label": "Display name" });
    nameInput.addEventListener("change", () => { cfg.name = nameInput.value.trim(); saveCfg(); S.sideSig = ""; S.topSig = ""; update(true); });
    return [
      h("div", { style: "display:flex;align-items:center;gap:14px;padding:6px 2px 4px" }, avatar(true),
        h("div", { style: "min-width:0" }, h("div", { style: "font-weight:750;font-size:1.1rem", text: displayName() }), h("div", { class: "set-sub", text: info ? `${info.pc} · ${info.user}` : "" }))),
      card(h("div", { class: "set col" }, h("div", { class: "set-text" }, h("div", { class: "set-label", text: "Display name" }), h("div", { class: "set-sub", text: "Only used on this phone." })), nameInput)),
      info && info.accounts && info.device ? card(kv("This phone", info.device.name), kv("PC owner", info.owner || "-"),
        setRow({ icon: "pencil", label: "Rename this phone", sub: "The name you see on the PC's page.", onclick: renamePhone })) : null,
      h("div", { class: "actions", style: "margin-top:14px" },
        h("button", { class: "btn danger", type: "button", onclick: signOut }, ico("logout"), "Sign out")),
    ];
  }

  /* What to hear about, and when not to. Saved on the PC (per phone), so it holds even when this page is closed. */
  const PREF_ROWS = [
    ["approval", "bell", "Plans and folders waiting for you", "A plan is ready to approve, or a folder needs your OK."],
    ["done", "check", "A request finished", "Only for ones that took a while."],
    ["failed", "alert", "A request failed", ""],
    ["pc", "power", "Your PC going away", "Going to sleep, restarting, shutting down, or the battery is low."],
    ["security", "shield", "Security alerts", "A new phone paired, wrong codes tried, access paused. These ignore quiet hours."],
  ];
  function prefsBox() {
    const box = h("div", { class: "prefs" });
    let prefs = null, saving = 0;
    const save = async () => {
      const mine = ++saving;
      try { const next = await (await api("/push/prefs", { method: "POST", body: JSON.stringify(prefs) })).json(); if (mine === saving) prefs = next; }
      catch (e) { if (e.code !== 401) toast(e.message || "Couldn't save that."); }
    };
    const paint = () => {
      if (!prefs) { box.replaceChildren(h("i", { class: "sk row" }), h("i", { class: "sk row" })); return; }
      const sw = (on, label, change) => { const b = h("button", { class: "toggle", type: "button", role: "switch", "aria-checked": String(!!on), "aria-label": label });
        b.onclick = () => { const v = b.getAttribute("aria-checked") !== "true"; b.setAttribute("aria-checked", String(v)); haptic("tick"); change(v); }; return b; };
      const time = (key, label) => { const i = h("input", { class: "text-input time-input", type: "time", value: prefs.quiet[key], "aria-label": label }); i.onchange = () => { if (i.value) { prefs.quiet[key] = i.value; prefs.quiet.tz = -new Date().getTimezoneOffset(); save(); } else i.value = prefs.quiet[key]; }; return i; };
      const quiet = h("div", { class: "set col" }, h("div", { class: "set-text" }, h("div", { class: "set-label", text: "Quiet hours" }),
        h("div", { class: "set-sub", text: "Nothing is sent to this phone between these times, except security alerts. It is not saved for later." })),
        h("div", { class: "quiet-times" }, h("label", {}, h("span", { text: "From" }), time("start", "Quiet hours start")), h("label", {}, h("span", { text: "Until" }), time("end", "Quiet hours end"))));
      quiet.hidden = !prefs.quiet.on;
      box.replaceChildren(card(
        ...PREF_ROWS.map(([key, icon, label, sub]) => setRow({ icon, label, sub, control: sw(prefs.types[key], label, (v) => { prefs.types[key] = v; save(); }) })),
        setRow({ icon: "moon", label: "Quiet hours", sub: prefs.quiet.on ? `${prefs.quiet.start} to ${prefs.quiet.end}` : "Off", control: sw(prefs.quiet.on, "Quiet hours", (v) => { prefs.quiet.on = v; prefs.quiet.tz = -new Date().getTimezoneOffset(); quiet.hidden = !v; save(); paint(); }) }),
        quiet));
    };
    paint();
    api("/push/prefs").then((r) => r.json()).then((d) => { prefs = d; paint(); }).catch(() => { box.replaceChildren(h("p", { class: "set-sub", style: "padding:8px 4px", text: "Can't load these right now." })); });
    return box;
  }
  function buildNotify() {
    const info = S.info;
    return [
      sect("Notifications"),
      card(notifyRow()),
      Push.on() ? sect("What to hear about") : null,
      Push.on() ? prefsBox() : null,
      h("p", { class: "set-sub", style: "padding:8px 4px", text: "A notice that a plan is ready or a job finished, even when this page is closed. By default it never says what you asked: only that something needs you." }),
    ].filter(Boolean);
  }

  function buildLook() {
    const info = S.info;
    const swatches = h("div", { class: "swatches", role: "group", "aria-label": "Accent colour" });
    ACCENTS.forEach(([id, c1, c2]) => {
      const b = h("button", { class: "sw", type: "button", "aria-label": id, "aria-pressed": String(cfg.accent === id), vars: { "--c1": c1, "--c2": c2 } });
      b.onclick = () => { cfg.accent = id; saveCfg(); applyLook(); [...swatches.children].forEach((c) => c.setAttribute("aria-pressed", "false")); b.setAttribute("aria-pressed", "true"); haptic("light"); };
      swatches.append(b);
    });
    return [
      sect("Appearance"),
      card(
        segRow("theme", "Theme", [["dark", "Dark"], ["light", "Light"], ["system", "System"]], () => applyLook()),
        h("div", { class: "set col" }, h("div", { class: "set-text" }, h("div", { class: "set-label", text: "Accent colour" })), swatches),
        segRow("text", "Text size", [["s", "Small"], ["m", "Medium"], ["l", "Large"]], () => applyLook())),
      sect("Graphics and motion"),
      card(motionBox()),
      sect("Feedback"),
      card(
        toggleRow("haptics", "Haptic feedback", canVibrate ? "A small buzz on taps, approvals and results." : "This browser can't vibrate (iPhone browsers don't allow it).", "vibrate", () => haptic("success")),
        toggleRow("sounds", "Lock and unlock sounds", "A camera click when your PC locks, and a start-up chime when it unlocks.", "volume", () => chime("unlock")),
        toggleRow("ticks", "Dial ticks while idle", "A soft tick in time with the orb on the start screen.", "clock", () => syncTicks())),
    ];
  }

  function buildVoice() {
    const info = S.info;
    const rate = h("input", { type: "range", min: "0.7", max: "1.3", step: "0.05", value: String(cfg.rate), "aria-label": "Speaking speed" });
    rate.addEventListener("input", () => { cfg.rate = Number(rate.value); saveCfg(); });
    rate.addEventListener("change", () => { haptic("tick"); speak("This is how fast I will speak."); });
    const [micTone, micText] = micStatus();
    return [
      sect("Voice"),
      card(
        setRow({ icon: "globe", label: "Speaking language", sub: `${langInfo(cfg.lang)[1]} · ${langInfo(cfg.lang)[2]}`, onclick: () => openLanguages() }),
        setRow({ icon: "mic", label: "Microphone", sub: Speech.status() === "ok" ? "Your browser can listen." : Speech.status() === "insecure" ? "Voice needs an https link. Tap for the steps." : "This browser can't listen.", control: h("span", { class: "pill " + micTone, text: micText }), onclick: Speech.status() === "ok" ? null : () => voiceHelp(Speech.status()) }),
        segRow("input", "Start in", [["auto", "Auto"], ["voice", "Voice"], ["type", "Keyboard"]], () => { chooseMode(); renderDock(); }),
        segRow("hold", "Voice button", [["hold", "Hold to talk"], ["tap", "Tap to talk"]], () => paintVoice()),
        toggleRow("autoSend", "Send when I stop talking", "Off: your words wait in the box so you can fix them first.", "arrowUp"),
        segRow("speak", "Read replies aloud", [["off", "Never"], ["voice", "After voice"], ["always", "Always"]], null),
        h("div", { class: "set col" }, h("div", { class: "set-text" }, h("div", { class: "set-label", text: "Speaking speed" })), rate)),
      h("p", { class: "set-sub", style: "padding:8px 4px", text: "Your browser's speech service (Google in Chrome, Apple in Safari) turns your voice into text, so the audio goes to them. Your PC's AI only ever receives the text." }),
    ];
  }

  function buildChats() {
    const info = S.info;
    return [
      sect("Chats"),
      card(
        toggleRow("openTrails", "Show audit trails open", "Every step the PC took, under each reply.", "activity", () => { S.nodes.clear(); S.threadConv = null; update(true); }),
        toggleRow("saveChats", "Keep recent chats on this phone", "So you can read them when your PC is off or out of reach. The copy stays on this phone, and is removed when you sign out.", "clock", () => {
          if (cfg.saveChats) { toast("Your recent chats will be kept on this phone."); if (S.loaded && !S.offline) Saved.queue(savedSource, 200); }
          else { Saved.clear(); toast("Saved chats removed from this phone."); }
        }),
        setRow({ icon: "trash", label: "Clear saved chats", sub: "Removes the copy kept on this phone. Your chats stay on the PC.", onclick: async () => { await Saved.clear(); toast("Saved chats removed from this phone."); haptic("success"); } }),
        setRow({ icon: "activity", label: "Activity log", sub: "Everything this PC has done for you.", onclick: () => openActivity() }),
        setRow({ icon: "trash", label: "Bring back removed chats", sub: `${S.hidden.size} removed from this phone`, onclick: () => { S.hidden.clear(); store.set("afp_hidden", []); update(true); toast("Chats restored"); haptic("success"); } })),
    ];
  }

  /* "Your data and control": what this app can and can't reach, who is connected, anything worth a second look, and the panic button. */
  async function pauseAccess() {
    const ok = await ask({ title: "Pause all phone access?", message: "Every phone, including this one, is shut out of your PC right away, and anything running is stopped. Nothing is deleted. Only you can turn it back on, on the PC itself.", ok: "Pause everything", cancel: "Keep going", tone: "danger", icon: "shield" });
    if (!ok) return;
    try { await api("/pause", { method: "POST" }); haptic("warning"); toast("Paused. This phone is shut out too."); ping(); schedulePing(1500); }
    catch (e) { if (e.code !== 401) { toast(e.message || "Couldn't pause."); haptic("error"); } }
  }
  function buildTrust() {
    const info = S.info;
    if (!info) return [card(h("div", { style: "padding:6px 14px", "aria-label": "Loading" }, h("i", { class: "sk row" }), h("i", { class: "sk row" }), h("i", { class: "sk row" })))];
    const f = info.folders, aiKind = info.ai.kind;
    const people = h("div", { class: "set" }, setIco("phone"), h("div", { class: "set-text" }, h("div", { class: "set-label", text: "Phones that can reach this PC" }), h("div", { class: "set-sub", text: "Checking…" })));
    const alerts = h("div", { class: "card trust-alerts" }, h("p", { class: "set-sub", style: "padding:12px 14px", text: "Checking…" }));
    api("/security").then((r) => r.json()).then((d) => {
      const n = d.devices;
      people.replaceWith(setRow({ icon: "phone", label: `${n} phone${n === 1 ? "" : "s"} can reach this PC`, sub: "See them, or remove one you don't recognise.", onclick: () => { haptic("tick"); openSettingsPage(settingsModules().find((m) => m.key === "devices")); } }));
      alerts.replaceChildren(...(d.alerts.length
        ? d.alerts.map((a) => h("div", { class: "trust-alert", "data-tone": a.tone }, h("span", { class: "adot", "aria-hidden": "true" }), h("div", { class: "grow" }, h("b", { text: a.text }), a.who ? h("small", { text: a.who }) : null), h("time", { text: ago(a.time) })))
        : [h("p", { class: "set-sub", style: "padding:12px 14px", text: "Nothing unusual lately. New phones, wrong codes and pauses show up here." })]));
    }).catch(() => { people.querySelector(".set-sub").textContent = "Can't check right now."; alerts.replaceChildren(h("p", { class: "set-sub", style: "padding:12px 14px", text: "Can't check right now." })); });
    return [
      h("p", { class: "trust-lead", text: "You decide what this app can reach. It asks before it changes anything, and every change can be undone." }),
      sect("What it can reach"),
      card(
        setRow({ icon: "folder", label: "Only the folders you chose", sub: `${f.writable.length} it may change (with your approval) · ${f.read_only.length} read-only · ${(f.granted || []).length} you allowed later` }),
        setRow({ icon: "lock", label: "Secrets stay out of reach", sub: "Passwords, keys and protected system places are never opened." }),
        setRow({ icon: "terminal", label: "Commands", sub: info.commands_enabled ? "On. Each one needs your approval and shows the whole command. The safety filter is a speed bump, not a sandbox." : "Off.", control: h("span", { class: "pill " + (info.commands_enabled ? "warn" : "ok"), text: info.commands_enabled ? "On" : "Off" }) })),
      sect("What leaves this PC"),
      card(setRow({ icon: aiKind === "hosted" ? "cloud" : "cpu", label: aiKind === "hosted" ? "Hosted AI" : aiKind === "local" ? "AI on this PC" : "No AI model",
        sub: aiKind === "hosted" ? `${info.ai.model}. Your requests and the file text it reads are sent to that service.` : aiKind === "local" ? `${info.ai.model}. Nothing leaves this PC.` : "Typed commands only. Nothing is sent to an AI.",
        control: h("span", { class: "pill " + (aiKind === "hosted" ? "warn" : "ok"), text: aiKind === "hosted" ? "Leaves PC" : "Private" }) })),
      sect("Your control"),
      card(
        setRow({ icon: "shield", label: "Approval comes first", sub: `Asks before: ${info.approval.required_for.join(", ")}. Queued requests are never approvals.` }),
        setRow({ icon: "undo", label: "File changes can be undone", sub: "Each change is saved first, so Undo puts files back. It can't take back a command, a file already sent to your phone, or an app that was opened." }),
        people),
      sect("Worth a second look"),
      alerts,
      h("div", { class: "actions", style: "margin-top:18px" }, h("button", { class: "btn danger block", type: "button", onclick: pauseAccess }, ico("shield"), "Pause all phone access")),
      h("p", { class: "set-sub", style: "padding:8px 4px", text: "Pause shuts every phone out at once. Only you can turn it back on, on the PC itself, never from a phone." }),
    ];
  }

  function buildPc() {
    const info = S.info;
    return [
      sect("This PC and what it may do"),
      info ? thisPcCard(info) : card(h("div", { style: "padding:6px 14px", "aria-label": "Loading" }, h("i", { class: "sk row" }), h("i", { class: "sk row" }), h("i", { class: "sk row" }))),
      h("p", { class: "set-sub", style: "padding:8px 4px", text: "These are changed in config.yaml on the PC, never from the phone, so a stolen phone can't loosen them." }),
    ];
  }

  /* A plain-text summary for a support request. No token, no request text, no file names: only how the app and the link are doing. */
  function diagnosticsText() {
    const info = S.info || {};
    return [
      `AccessFromPhone ${info.version || "?"}`, `Time: ${new Date().toISOString()}`,
      `Link: ${S.online ? "connected" : "not connected"}${S.lostCause ? " (" + S.lostCause + ")" : ""}${PC.rtt ? ", " + PC.rtt + " ms" : ""}${S.away ? ", chose to keep chatting" : ""}`,
      `Secure link: ${info.https ? "https" : "http"}`, `AI: ${info.ai ? info.ai.kind : "?"}`,
      `Queued requests: ${Q ? Q.active().length : "n/a"}`, `Saved chats on this phone: ${cfg.saveChats ? "on" : "off"}`,
      `Notifications: ${Push.on() ? "on" : "off"}`, `Page: ${window.AfpApp ? "Android app" : "browser"}`, `Browser: ${navigator.userAgent}`,
    ].join("\n");
  }
  async function copyDiagnostics() {
    const text = diagnosticsText();
    try { await navigator.clipboard.writeText(text); toast("Copied. Paste it into your message to support."); haptic("success"); }
    catch { await ask({ title: "Diagnostics", message: text, ok: "Close", cancel: "", icon: "info" }); }
  }
  async function clearQueue() {
    const n = Q ? Q.items().length : 0;
    if (!n) { toast("Nothing is waiting in the queue."); return; }
    const yes = await ask({ title: `Clear ${n} queued request${n > 1 ? "s" : ""}?`, message: "They have not been sent to your PC yet. They will be gone, and nothing will run.", ok: "Clear them", cancel: "Keep them", tone: "danger", icon: "trash" });
    if (!yes) return;
    Q.clear(); haptic("success"); toast("Queue cleared");
  }
  function buildAbout() {
    const info = S.info, links = (info && info.links) || {};
    const link = (icon, label, sub, url) => h("a", { class: "set", href: url, target: "_blank", rel: "noopener noreferrer" }, setIco(icon), h("div", { class: "set-text" }, h("div", { class: "set-label", text: label }), h("div", { class: "set-sub", text: sub })), ico("chevron"));
    return [
      card(setRow({ icon: "sparkle", label: "Take the tour", sub: "How it works, in about 30 seconds.", onclick: () => { closeSheet(); setTimeout(() => openOnboarding(), 320); } })),
      card(kv("Version", info ? info.version : "…"), kv("Secure link", info ? (info.https ? "On (https)" : "Off (http)") : "…"), kv("Signed in", S.token ? "Yes" : "No")),
      sect("Help"),
      card(
        setRow({ icon: "copy", label: "Copy diagnostics", sub: "Version and connection details for support. No passwords, requests or file names.", onclick: copyDiagnostics }),
        links.support ? link("info", "Get help", "Opens the support page.", links.support) : null,
        links.privacy ? link("shield", "Privacy policy", "Opens the privacy page.", links.privacy) : null),
      sect("This phone"),
      card(
        setRow({ icon: "clock", label: "Clear the request queue", sub: Q && Q.items().length ? `${Q.items().length} waiting to be sent` : "Nothing waiting", onclick: clearQueue }),
        setRow({ icon: "logout", label: "Forget this PC", sub: "Signs this phone out and removes the chats kept on it. Pair again to come back.", onclick: signOut })),
      h("div", { class: "actions", style: "margin-top:14px" },
        h("button", { class: "btn ghost", type: "button", onclick: resetSettings }, "Reset settings")),
    ];
  }

  const THEME_NAME = { dark: "Dark", light: "Light", system: "System" };
  function settingsModules() {
    const info = S.info;
    const ai = info ? (info.ai.kind === "hosted" ? "Hosted AI" : info.ai.kind === "local" ? "AI on this PC" : "No AI model") : "…";
    return [
      { key: "trust", icon: "shield", title: "Your data and control", sub: "What it can reach · Pause", build: buildTrust },
      { key: "device", icon: "user", title: "Profile and phone", sub: `${displayName()}${info && info.device ? " · " + info.device.name : ""}`, build: buildDevice },
      { key: "notify", icon: "bell", title: "Notifications", sub: Push.on() ? "On" : "Off", build: buildNotify },
      { key: "look", icon: "palette", title: "Look and feel", sub: `${THEME_NAME[cfg.theme] || "Dark"} · ${cfg.accent}${cfg.haptics ? "" : " · no buzz"}`, build: buildLook },
      { key: "voice", icon: "mic", title: "Voice and input", sub: langInfo(cfg.lang)[1], build: buildVoice },
      { key: "devices", icon: "phone", title: "Connected devices", sub: "Who is using this PC", build: buildDevices },
      { key: "lock", icon: "shield", title: "App lock", sub: Lock.on() ? "On" : "Off", build: buildLock },
      { key: "downloads", icon: "download", title: "Downloads", sub: DL.count() ? DL.count() + " in progress or paused" : "Resume, pause, delete", build: () => [DL.mount()] },
      { key: "chats", icon: "clock", title: "Chats and privacy", sub: cfg.saveChats ? "Recent chats kept on this phone" : "Nothing kept on this phone", build: buildChats },
      { key: "pc", icon: "gauge", title: "This PC", sub: ai, build: buildPc },
      { key: "about", icon: "info", title: "About", sub: info ? `Version ${info.version}` : "", build: buildAbout },
    ];
  }

  function openSettingsPage(mod) {
    const body = h("div", { class: "set-page" });
    const fill = () => body.replaceChildren(...mod.build().filter(Boolean));
    fill();
    const sheet = openSheet({ title: mod.title, body, route: "settings-" + mod.key });
    S.refillPage = () => { if (body.isConnected) { const keep = sheet.body.scrollTop; fill(); sheet.body.scrollTop = keep; } };
    loadInfo();
  }

  function openSettings(deep) {
    const body = h("div", { class: "set-menu" });
    const fill = () => {
      const info = S.info;
      body.replaceChildren(
        h("div", { class: "set-who" }, avatar(true), h("div", { class: "grow" }, h("b", { text: displayName() }), h("small", { text: info ? `${info.pc} · ${info.user}` : "" }))),
        card(...settingsModules().map((m) => setRow({ icon: m.icon, label: m.title, sub: m.sub, onclick: () => { haptic("tick"); openSettingsPage(m); } }))),
        h("div", { class: "actions", style: "margin-top:18px" }, h("button", { class: "btn danger block", type: "button", onclick: signOut }, ico("logout"), "Sign out")),
        h("p", { class: "set-foot", text: info ? `Access · version ${info.version}` : "" }));
    };
    fill();
    const sheet = openSheet({ title: "Settings", body, full: true, route: "settings", push: deep !== true });
    S.refillSettings = () => { if (body.isConnected) { const keep = sheet.body.scrollTop; fill(); sheet.body.scrollTop = keep; } if (S.refillPage) S.refillPage(); };
    loadInfo();
  }

  function thisPcCard(info) {
    const aiTone = info.ai.kind === "hosted" ? "warn" : info.ai.kind === "local" ? "ok" : "";
    const ai = h("div", { class: "set" }, setIco(info.ai.kind === "hosted" ? "cloud" : "cpu"),
      h("div", { class: "set-text" }, h("div", { class: "set-label", text: info.ai.kind === "hosted" ? "Hosted AI" : info.ai.kind === "local" ? "AI on this PC" : "No AI model" }),
        h("div", { class: "set-sub", text: info.ai.kind === "hosted" ? `${info.ai.model}. Your requests and the file text it reads leave this PC.` : info.ai.kind === "local" ? `${info.ai.model}. Nothing leaves this PC.` : "Typed commands only. See the README to add one." })),
      h("span", { class: "pill " + aiTone, text: info.ai.kind === "hosted" ? "Leaves PC" : info.ai.kind === "local" ? "Private" : "Off" }));
    const cmd = setRow({ icon: "terminal", label: "Run commands", sub: info.commands_enabled ? `Each one needs your approval, and you see the whole command. A safety filter blocks known-dangerous ones, but it is a speed bump, not a sandbox, so read every command. Stops after ${info.command_timeout_seconds}s.` : "Turned off.", control: h("span", { class: "pill " + (info.commands_enabled ? "warn" : "ok"), text: info.commands_enabled ? "On" : "Off" }) });
    const list = (items) => h("ul", { class: "paths" }, items.length ? items.map((p) => h("li", { text: p })) : [h("li", { text: "None" })]);
    return h("div", {}, card(ai, cmd,
      h("div", { class: "set col" }, h("div", { class: "set-text" }, h("div", { class: "set-label", text: "Folders it can change" }), h("div", { class: "set-sub", text: "Read, search and, with your approval, change." })), list(info.folders.writable)),
      h("div", { class: "set col" }, h("div", { class: "set-text" }, h("div", { class: "set-label", text: "Read-only folders" }), h("div", { class: "set-sub", text: "Always available to read and search, never changed." })), list(info.folders.read_only)),
      setRow({ icon: "lock", label: "Ask before opening other folders", sub: info.ask_for_access ? "If a request needs another folder, you're asked first. You can allow it once or keep it." : "Off: only the folders above are ever used.", control: h("span", { class: "pill " + (info.ask_for_access ? "ok" : "warn"), text: info.ask_for_access ? "On" : "Off" }) }),
      h("div", { class: "set col" }, h("div", { class: "set-text" }, h("div", { class: "set-label", text: "Folders you allowed" }), h("div", { class: "set-sub", text: "Remove one to be asked again next time. \"Can edit\" folders let you save changes by hand." })),
        (info.folders.granted_details || []).length
          ? h("div", { class: "granted" }, info.folders.granted_details.map((g) => h("div", { class: "granted-row" },
              h("span", { class: "granted-path", text: g.path }), h("span", { class: "pill " + (g.change ? "warn" : "ok"), text: g.change ? "Can edit" : "Read only" }),
              g.shared && !g.mine ? h("span", { class: "pill", title: "The Admin shared this folder with this phone", text: "Shared with you" }) : h("button", { class: "btn sm danger", type: "button", onclick: () => revokeAccess(g.path) }, "Remove"))))
          : h("p", { class: "set-sub", text: "None yet. I'll ask when I need one." })),
      kv("Asks before", info.approval.required_for.join(", ")), kv("Approval times out", `${info.approval.timeout_minutes} min`),
      kv("Largest download", info.max_download_mb ? `${info.max_download_mb} MB` : "No limit"), kv("Remembers", `${info.memory_turns} earlier messages`)));
  }
  function resetSettings() {
    Object.assign(cfg, DEFAULTS); saveCfg(); applyLook(); chooseMode(); renderDock(); closeSheet(); toast("Settings reset"); haptic("success");
  }

  function openLanguages() {
    const body = h("div"), list = h("div");
    const q = h("input", { class: "text-input", type: "search", placeholder: "Search languages", "aria-label": "Search languages", style: "margin-bottom:10px" });
    const paint = () => {
      const term = q.value.trim().toLowerCase();
      list.replaceChildren(...LANGS.filter((l) => !term || (l[1] + " " + l[2] + " " + l[0]).toLowerCase().includes(term)).map((l) => {
        const b = h("button", { class: "pick", type: "button", "aria-pressed": String(cfg.lang === l[0]) }, h("span", {}, h("div", { class: "p1", lang: l[0], text: l[1] }), h("div", { class: "p2", text: l[2] })), cfg.lang === l[0] ? ico("check") : null);
        b.onclick = () => { cfg.lang = l[0]; saveCfg(); haptic("light"); paintVoice(); closeSheet(); };
        return b;
      }));
    };
    q.addEventListener("input", paint); paint();
    body.append(q, list, h("p", { class: "set-sub", style: "padding:10px 4px", text: "Chrome on Android supports all of these. Safari on iPhone supports fewer; if one doesn't work, you'll be told." }));
    openSheet({ title: "Speaking language", body, route: "language" });
  }

  const FILTERS = [
    ["all", "All", null],
    ["changes", "Changes", ["write_file", "delete_file", "move_file", "copy_file", "send_file", "download", "undo"]],
    ["commands", "Commands", ["run_command", "command_finished"]],
    ["approvals", "Approvals", ["awaiting_approval", "approved", "rejected", "expired"]],
    ["errors", "Problems", ["failed", "expired"]],
  ];
  function openActivity(deep) {
    const body = h("div"), filters = h("div", { class: "filters", role: "group", "aria-label": "Filter" }), list = h("div", { class: "card" });
    let current = "all", limit = 80;
    const prompts = new Map(S.audit.filter((e) => e.event === "submitted").map((e) => [e.job, e.prompt]));
    const paint = () => {
      const kinds = FILTERS.find((f) => f[0] === current)[2];
      const rows = S.audit.filter((e) => !kinds || kinds.includes(e.event)).slice().reverse();
      list.replaceChildren(...rows.slice(0, limit).map((ev) => {
        const [label, tone] = EVENT[ev.event] || [ev.event, ""];
        const detail = Object.entries(ev).filter(([k, v]) => !SKIP_DETAIL.has(k) && v !== "").map(([k, v]) => `${k}: ${v}`).join(" · ");
        const sub = [detail, prompts.get(ev.job) ? "“" + clip(prompts.get(ev.job), 70) + "”" : ""].filter(Boolean).join("\n");
        return h("div", { class: "set" }, h("div", { class: "set-text" },
          h("div", { class: "set-label", style: tone === "bad" ? "color:var(--bad)" : tone === "good" ? "color:var(--ok)" : "", text: label }),
          sub ? h("div", { class: "set-sub", style: "white-space:pre-wrap", text: sub }) : null),
          h("span", { class: "set-sub", style: "flex:none;text-align:right", text: `${ago(ev.time)}\n${fmtClock(ev.time)}`.replace(/^(.*)\n/, "$1 · ") }));
      }));
      if (!rows.length) list.replaceChildren(h("div", { class: "set-sub", style: "padding:16px", text: "Nothing here yet." }));
      if (rows.length > limit) list.append(h("button", { class: "set", type: "button", onclick: () => { limit += 80; paint(); } }, h("div", { class: "set-text" }, h("div", { class: "set-label", text: `Show ${Math.min(80, rows.length - limit)} more` }))));
    };
    FILTERS.forEach(([id, text]) => {
      const b = h("button", { type: "button", "aria-pressed": String(id === current) }, text);
      b.onclick = () => { current = id; limit = 80; [...filters.children].forEach((c) => c.setAttribute("aria-pressed", "false")); b.setAttribute("aria-pressed", "true"); haptic("tick"); paint(); };
      filters.append(b);
    });
    paint();
    body.append(filters, list, h("div", { class: "actions", style: "margin-top:14px" },
      h("button", { class: "btn", type: "button", onclick: exportAudit }, ico("download"), "Export as JSON")),
      h("p", { class: "set-sub", style: "padding:10px 4px", text: "This is the PC's own record (data/audit.log). It keeps every request and action, even for chats you removed from this phone." }));
    openSheet({ title: "Activity log", body, full: true, route: "activity", push: deep !== true });
  }
  function exportAudit() {
    const blob = new Blob([JSON.stringify(S.audit, null, 2)], { type: "application/json" });
    const d = new Date();
    saveBlob(blob, `audit-${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}.json`);
  }

  function voiceHelp(reason) {
    const body = h("div", { class: "help" });
    if (reason === "insecure") {
      body.append(h("p", { text: "Browsers only allow the microphone on a secure page, and this link is plain http." }),
        h("p", {}, h("b", { text: "To turn voice on:" })),
        h("ol", {}, h("li", {}, "On the PC, open ", h("code", { text: "config.yaml" }), " and set ", h("code", { text: "server.https: true" }), "."),
          h("li", { text: "Restart AccessFromPhone and open the new link it prints (it starts with https)." }),
          h("li", { text: "Your phone says the connection isn't private. Choose Advanced, then Continue. That is your own PC, and the link is now encrypted." }),
          h("li", { text: "Tap the microphone and allow it." })),
        h("p", { text: "Meanwhile, the microphone key on your phone's keyboard works in the message box without any of this." }));
    } else if (reason === "denied") {
      body.append(h("p", { text: "The microphone is blocked for this page." }),
        h("ol", {}, h("li", { text: "Tap the lock or tune icon in the address bar." }), h("li", { text: "Open Permissions, then Microphone, and choose Allow." }), h("li", { text: "Come back and try again." })),
        h("p", { text: "On iPhone: Settings, Safari, Microphone, then allow this site." }));
    } else {
      body.append(h("p", { text: "This browser can't turn speech into text." }),
        h("p", { text: "Chrome on Android and Safari on iPhone can. Or use the microphone key on your keyboard in the message box." }));
    }
    body.append(h("div", { class: "actions", style: "margin-top:16px" }, h("button", { class: "btn primary", type: "button", onclick: () => { cfg.input = "type"; saveCfg(); closeSheet(); setMode("type"); } }, ico("keyboard"), "Use the keyboard")));
    openSheet({ title: "Voice input", body, route: "voice-help" });
    haptic("warning");
  }

  /* ===================================================================== keys, gestures, start-up */

  /* ===================================================================== the editor: change a file by hand */

  /* A line diff, for "Review changes". Trim what both versions share, then find the longest common run of lines in the
     middle. Big or very different files fall back to "all of it changed", which is still correct. */
  function diffLines(oldText, newText) {
    const a = oldText.split("\n"), b = newText.split("\n");
    let start = 0;
    while (start < a.length && start < b.length && a[start] === b[start]) start++;
    let endA = a.length, endB = b.length;
    while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) { endA--; endB--; }
    const mid = { a: a.slice(start, endA), b: b.slice(start, endB) };
    const ops = a.slice(0, start).map((t) => ["=", t]);
    const n = mid.a.length, m = mid.b.length;
    if (n * m <= 1500000) {
      const table = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
      for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) table[i][j] = mid.a[i] === mid.b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
      let i = 0, j = 0;
      while (i < n && j < m) {
        if (mid.a[i] === mid.b[j]) { ops.push(["=", mid.a[i]]); i++; j++; }
        else if (table[i + 1][j] >= table[i][j + 1]) ops.push(["-", mid.a[i++]]);
        else ops.push(["+", mid.b[j++]]);
      }
      while (i < n) ops.push(["-", mid.a[i++]]);
      while (j < m) ops.push(["+", mid.b[j++]]);
    } else { mid.a.forEach((t) => ops.push(["-", t])); mid.b.forEach((t) => ops.push(["+", t])); }
    b.slice(endB).forEach((t) => ops.push(["=", t]));
    return ops;
  }

  function diffView(oldText, newText) {
    const ops = diffLines(oldText, newText), box = h("div", { class: "diff" });
    const changed = ops.map((o, i) => (o[0] !== "=" ? i : -1)).filter((i) => i >= 0);
    if (!changed.length) return h("p", { class: "note", text: "No changes." });
    const keep = new Set();
    changed.forEach((i) => { for (let k = Math.max(0, i - 2); k <= Math.min(ops.length - 1, i + 2); k++) keep.add(k); });
    let skipped = 0, shown = 0;
    ops.forEach(([kind, text], i) => {
      if (!keep.has(i)) { skipped++; return; }
      if (skipped) { box.append(h("div", { class: "diff-gap", text: `… ${skipped} unchanged line${skipped === 1 ? "" : "s"} …` })); skipped = 0; }
      if (++shown > 400) return;
      box.append(h("div", { class: "diff-row " + (kind === "+" ? "add" : kind === "-" ? "del" : "") }, h("span", { class: "sign", text: kind === "=" ? " " : kind }), h("span", { class: "code", text: text || " " })));
    });
    if (skipped) box.append(h("div", { class: "diff-gap", text: `… ${skipped} unchanged line${skipped === 1 ? "" : "s"} …` }));
    if (shown > 400) box.append(h("div", { class: "diff-gap", text: `… and ${shown - 400} more changed lines` }));
    return box;
  }

  const countChanges = (a, b) => diffLines(a, b).reduce((n, o) => { n[o[0]] = (n[o[0]] || 0) + 1; return n; }, {});

  /* Ask to allow a folder from here: reading it, or (change) editing files in it. The tap is the permission. */
  function askAccess(folder, change, then) {
    const body = h("div", { class: "help" },
      h("p", { text: change ? "To save changes here, this folder has to be allowed for editing." : "To open this file, this folder has to be allowed." }),
      h("ul", { class: "paths access-paths" }, h("li", { text: folder })),
      h("p", { text: change
        ? "Nothing changes until you tap Save, you review each change first, and you can undo it. You can take this back any time in Settings."
        : "I can only read and search there. You can take it back any time in Settings." }));
    const yes = h("button", { class: "btn primary", type: "button" }, ico(change ? "pencil" : "folder"), change ? "Allow editing in this folder" : "Allow this folder");
    const no = h("button", { class: "btn ghost", type: "button", onclick: () => closeSheet() }, "Cancel");
    body.append(h("div", { class: "actions", style: "margin-top:14px" }, yes, no));
    const sheet = openSheet({ title: change ? "Allow editing?" : "Allow access?", body, route: "allow" });
    yes.onclick = async () => {
      yes.disabled = true; haptic("medium");
      try {
        const out = await (await api("/access/grant", { method: "POST", body: JSON.stringify({ path: folder, change }) })).json();
        if (S.info) { S.info.folders.granted_details = out.granted; S.info.folders.granted = out.granted.map((g) => g.path); }
        sheet.close(); haptic("success");
        if (then) setTimeout(then, 260);
      } catch (e) { yes.disabled = false; toast(e.message); haptic("error"); }
    };
  }

  /* ===================================================================== the canvas: a file in its own screen */
  /*
   * One panel for looking at a file and changing it. On a big screen it sits beside the chat; on a phone it is the whole
   * screen. View mode shows colours, line numbers, a Markdown preview, a CSV table, pictures and PDFs. Edit mode is the
   * text box with find and replace, review, save and undo. The file's text lives in one place (the text box), so
   * switching modes never loses what you typed.
   */

  const CV = {
    el: null, rec: null, info: null, area: null, mode: "view", original: "", sha: "", saving: false, lang: "text",
    query: "", matchCase: false, hits: [], cur: -1, rendered: 0, lines: [], state: {}, md: true, table: true, pretty: false,
    blob: "", observer: null, seenJobs: new Set(),
  };

  const LANGS_BY_EXT = {
    json: "json", xml: "xml", html: "xml", htm: "xml", svg: "xml", xaml: "xml", config: "xml", csproj: "xml", inuse: "xml",
    js: "js", mjs: "js", cjs: "js", jsx: "js", ts: "js", tsx: "js", java: "js", cs: "js", c: "js", h: "js", cpp: "js",
    go: "js", rs: "js", kt: "js", swift: "js", php: "js", css: "css", scss: "css", less: "css", py: "py", rb: "py",
    yml: "yaml", yaml: "yaml", ini: "ini", cfg: "ini", conf: "ini", toml: "ini", properties: "ini", env: "ini",
    md: "md", markdown: "md", log: "log", csv: "csv", tsv: "csv", sh: "sh", bash: "sh", ps1: "sh", bat: "sh", cmd: "sh",
    sql: "sql",
  };
  const KEYWORDS = new Set((
    "const let var function return if else for while do class import export from new async await try catch finally throw " +
    "switch case break continue default typeof instanceof extends implements interface type enum public private protected " +
    "static readonly void null undefined true false this super def self None True False in is not and or lambda with as pass " +
    "raise yield elif except global del using namespace string int bool float double long char struct fn pub mut impl match " +
    "use mod where package func go defer select chan range end then begin SELECT FROM WHERE INSERT UPDATE DELETE JOIN ON " +
    "GROUP ORDER BY CREATE TABLE INTO VALUES AND OR NOT NULL echo set fi done esac elif"
  ).split(" "));

  const STR = /"(?:[^"\\]|\\.)*"?|'(?:[^'\\]|\\.)*'?/y;
  const RULES = {
    json: [["key", /"(?:[^"\\]|\\.)*"(?=\s*:)/y], ["str", /"(?:[^"\\]|\\.)*"?/y], ["num", /-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/y], ["kw", /\b(?:true|false|null)\b/y], ["pun", /[{}[\],:]/y]],
    xml: [["com", /<!--.*?(?:-->|$)/y], ["tag", /<[/?!]?[A-Za-z_][\w:.-]*/y], ["tag", /\/?>|\?>/y], ["attr", /[A-Za-z_:][\w:.-]*(?=\s*=)/y], ["str", STR], ["pun", /=/y], ["num", /&[#\w]+;/y]],
    js: [["com", /\/\/.*$/y], ["com", /\/\*.*?(?:\*\/|$)/y], ["str", /"(?:[^"\\]|\\.)*"?|'(?:[^'\\]|\\.)*'?|`(?:[^`\\]|\\.)*`?/y], ["num", /\b\d[\d_]*(?:\.\d+)?\b|\b0x[0-9a-fA-F]+\b/y], ["word", /[A-Za-z_$][\w$]*/y]],
    css: [["com", /\/\*.*?(?:\*\/|$)/y], ["str", STR], ["tag", /@[\w-]+/y], ["num", /#[0-9a-fA-F]{3,8}\b|-?\d*\.?\d+(?:px|em|rem|%|vh|vw|s|ms|deg)?/y], ["attr", /[\w-]+(?=\s*:)/y]],
    py: [["com", /#.*$/y], ["str", STR], ["num", /\b\d[\d_]*(?:\.\d+)?\b/y], ["word", /[A-Za-z_]\w*/y]],
    yaml: [["com", /#.*$/y], ["key", /^\s*(?:-\s+)?[\w.\-/ ]+?(?=:(?:\s|$))/y], ["str", STR], ["num", /-?\d+(?:\.\d+)?\b/y], ["kw", /\b(?:true|false|null|yes|no)\b/y]],
    ini: [["com", /[;#].*$/y], ["tag", /^\s*\[[^\]]*\]/y], ["key", /^[^=\s][^=]*(?==)/y], ["str", STR], ["num", /-?\d+(?:\.\d+)?\b/y]],
    sh: [["com", /(?:#|::|REM\b).*$/y], ["str", STR], ["num", /\b\d+\b/y], ["attr", /\$[A-Za-z_{][\w}]*|%[\w]+%/y], ["word", /[A-Za-z_][\w-]*/y]],
    sql: [["com", /--.*$/y], ["str", /'(?:[^']|'')*'?/y], ["num", /\b\d+(?:\.\d+)?\b/y], ["word", /[A-Za-z_]\w*/y]],
  };

  function langOf(name, text) {
    const ext = (name.split(".").pop() || "").toLowerCase();
    if (LANGS_BY_EXT[ext]) return LANGS_BY_EXT[ext];
    const head = text.slice(0, 200).trimStart();
    if (head.startsWith("<")) return "xml";
    if (head.startsWith("{") || head.startsWith("[")) return "json";
    return "text";
  }

  /* one line into [class, text] pieces; `st` carries comments that run over several lines */
  function colourLine(line, lang, st) {
    if (lang === "log") {
      const stamp = /^\s*(?:\[?\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d[.,\d]*\]?|\d\d:\d\d:\d\d[.,\d]*)/.exec(line);
      const level = /\b(?:ERROR|FATAL|SEVERE|CRITICAL|Exception|FAIL(?:ED)?)\b/i.test(line) ? "log-err" : /\bWARN(?:ING)?\b/i.test(line) ? "log-warn" : "";
      return stamp ? [["com", stamp[0]], [level, line.slice(stamp[0].length)]] : [[level, line]];
    }
    const rules = RULES[lang];
    if (!rules || line.length > 2000) return [["", line]];
    const out = []; let i = 0, plain = "";
    const flush = () => { if (plain) { out.push(["", plain]); plain = ""; } };
    if (st.block) {
      const end = line.indexOf(st.block);
      if (end < 0) return [["com", line]];
      out.push(["com", line.slice(0, end + st.block.length)]); i = end + st.block.length; st.block = "";
    }
    while (i < line.length) {
      let hit = null;
      for (const [cls, re] of rules) {
        re.lastIndex = i;
        const m = re.exec(line);
        if (!m || !m[0].length) continue;
        let kind = cls;
        if (cls === "word") kind = KEYWORDS.has(m[0]) ? "kw" : line[i + m[0].length] === "(" ? "fn" : "";
        hit = [kind, m[0]]; break;
      }
      if (!hit) { plain += line[i++]; continue; }
      flush(); out.push(hit); i += hit[1].length;
      if (hit[0] === "com" && hit[1].startsWith("/*") && !hit[1].endsWith("*/")) st.block = "*/";
      if (hit[0] === "com" && hit[1].startsWith("<!--") && !hit[1].endsWith("-->")) st.block = "-->";
    }
    flush();
    return out;
  }

  /* a small, safe Markdown reader: everything is built as page nodes, never as HTML text */
  function markdownNodes(text) {
    const root = h("div", { class: "md" });
    const inline = (src, into) => {
      const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\s][^*]*\*)|(\[[^\]]+\]\([^)\s]+\))/g;
      let last = 0, m;
      while ((m = re.exec(src))) {
        if (m.index > last) into.append(src.slice(last, m.index));
        const t = m[0];
        if (m[1]) into.append(h("code", { text: t.slice(1, -1) }));
        else if (m[2]) into.append(h("strong", { text: t.slice(2, -2) }));
        else if (m[3]) into.append(h("em", { text: t.slice(1, -1) }));
        else {
          const [, label, url] = /\[([^\]]+)\]\(([^)\s]+)\)/.exec(t);
          into.append(/^https?:\/\//i.test(url) ? h("a", { href: url, target: "_blank", rel: "noopener noreferrer", text: label }) : label);
        }
        last = m.index + t.length;
      }
      if (last < src.length) into.append(src.slice(last));
      return into;
    };
    const lines = text.split("\n");
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (/^```/.test(line)) {
        const code = []; i++;
        while (i < lines.length && !/^```/.test(lines[i])) code.push(lines[i++]);
        root.append(h("pre", { class: "md-code" }, h("code", { text: code.join("\n") })));
      } else if (/^#{1,6}\s/.test(line)) {
        const level = /^#+/.exec(line)[0].length;
        root.append(inline(line.replace(/^#+\s+/, ""), h("h" + Math.min(level + 1, 6))));
      } else if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) root.append(h("hr"));
      else if (/^>\s?/.test(line)) root.append(inline(line.replace(/^>\s?/, ""), h("blockquote")));
      else if (/^\s*(?:[-*+]|\d+\.)\s+/.test(line)) {
        const ordered = /^\s*\d+\./.test(line), list = h(ordered ? "ol" : "ul");
        while (i < lines.length && /^\s*(?:[-*+]|\d+\.)\s+/.test(lines[i])) list.append(inline(lines[i++].replace(/^\s*(?:[-*+]|\d+\.)\s+/, ""), h("li")));
        i--; root.append(list);
      } else if (/^\|.+\|\s*$/.test(line) && /^\|[\s:|-]+\|\s*$/.test(lines[i + 1] || "")) {
        const cells = (row) => row.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
        const table = h("table", { class: "cv-table" }, h("thead", {}, h("tr", {}, cells(line).map((c) => inline(c, h("th"))))));
        const body = h("tbody"); i += 2;
        while (i < lines.length && /^\|.+\|\s*$/.test(lines[i])) body.append(h("tr", {}, cells(lines[i++]).map((c) => inline(c, h("td")))));
        i--; table.append(body); root.append(h("div", { class: "cv-scroll" }, table));
      } else if (line.trim()) {
        const para = [];
        while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|```|>|\s*(?:[-*+]|\d+\.)\s)/.test(lines[i])) para.push(lines[i++]);
        i--; root.append(inline(para.join("\n"), h("p")));
      }
    }
    return root;
  }

  function csvTable(text, sep) {
    const rows = []; let row = [], cell = "", quoted = false;
    for (let i = 0; i < text.length && rows.length < 2001; i++) {
      const ch = text[i];
      if (quoted) { if (ch === '"' && text[i + 1] === '"') { cell += '"'; i++; } else if (ch === '"') quoted = false; else cell += ch; }
      else if (ch === '"') quoted = true;
      else if (ch === sep) { row.push(cell); cell = ""; }
      else if (ch === "\n") { row.push(cell.replace(/\r$/, "")); rows.push(row); row = []; cell = ""; }
      else cell += ch;
    }
    if (cell || row.length) { row.push(cell); rows.push(row); }
    const table = h("table", { class: "cv-table" });
    rows.forEach((r, i) => table.append(h("tr", {}, r.map((c) => h(i === 0 ? "th" : "td", { text: c })))));
    return h("div", { class: "cv-scroll" }, table, rows.length > 2000 ? h("p", { class: "note", text: "Showing the first 2,000 rows." }) : null);
  }

  /* ---- the panel ---- */

  function ensureCanvas() {
    if (CV.el) return;
    CV.el = $("#canvas");
    CV.el.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s" && CV.mode === "edit") { e.preventDefault(); CV.save && CV.save(false); }
      else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "f") { e.preventDefault(); CV.toggleFind && CV.toggleFind(true); }
    });
  }

  const dirty = () => (!!CV.area && CV.area.value !== CV.original) || !!(CV.img && CV.img.dirty());

  async function openFile(path, mode = "view", opts = {}) {
    ensureCanvas();
    if (CV.rec && CV.info && CV.info.path !== path && dirty() && !(await askDiscard("Open another file?"))) return;
    let info;
    try { info = await (await api("/file?path=" + encodeURIComponent(path))).json(); }
    catch (e) {
      if (e.code === 401) return;
      if (e.data && e.data.code === "needs_access") { askAccess(e.data.folder, false, () => openFile(path, mode, opts)); return; }
      toast(e.message); haptic("error"); return;
    }
    S.focus[S.conv] = info.path; // so "it" and "that file" mean this one when you next write to the AI
    showCanvas(info, mode, opts);
  }
  const openEditor = (path) => openFile(path, "edit");

  /** A folder of pictures as a grid. Each picture is a tap from the viewer, and from there the editor. */
  async function openGallery(folder, opts = {}) {
    ensureCanvas();
    if (CV.rec && CV.info && dirty() && !(await askDiscard("Open somewhere else?"))) return;
    let data;
    try { data = await (await api("/images?path=" + encodeURIComponent(folder))).json(); }
    catch (e) {
      if (e.code === 401) return;
      if (e.data && e.data.code === "needs_access") { askAccess(e.data.folder, false, () => openGallery(folder, opts)); return; }
      toast(e.message); haptic("error"); return;
    }
    showCanvas({ kind: "gallery", path: data.folder, name: data.name, folder: data.folder, text: "", sha: "", can_change: false, editable: false, data }, "gallery", opts);
  }

  /** A folder to walk through: tap a folder to go in, tap a file to open it. `path` empty = the places that may be browsed. */
  async function openFolder(path, opts = {}) {
    ensureCanvas();
    if (CV.rec && CV.info && dirty() && !(await askDiscard("Open somewhere else?"))) return;
    let data;
    try { data = await (await api("/browse?path=" + encodeURIComponent(path || ""))).json(); }
    catch (e) {
      if (e.code === 401) return;
      if (e.data && e.data.code === "needs_access") { askAccess(e.data.folder, false, () => openFolder(path, opts)); return; }
      toast(e.message); haptic("error"); return;
    }
    showCanvas({ kind: "folder", path: data.path, name: data.name, folder: data.path, text: "", sha: "", can_change: false, editable: false, data }, "folder", opts);
  }

  /* The folder browser is a separate script and style sheet too, fetched the first time a folder is opened. */
  let filesLoad = null;
  function loadFiles() {
    if (window.AfpFiles) return Promise.resolve();
    if (!filesLoad) {
      filesLoad = new Promise((resolve, reject) => {
        document.head.append(h("link", { rel: "stylesheet", href: "/static/files.css" }));
        const s = h("script", { src: "/static/files.js" });
        s.onload = resolve; s.onerror = () => { filesLoad = null; reject(new Error("Couldn't load the file browser.")); };
        document.head.append(s);
      });
    }
    return filesLoad;
  }
  function filesCtx() {
    return {
      h, toast, haptic,
      get: (path) => api(path),
      post: (path) => api(path, { method: "POST" }),
      close: () => closeCanvas(),
      openFile: (path, from) => openFile(path, "view", { from }),
      openGallery: (folder) => openGallery(folder),
      askAccess,
      shareFolder: (path, done) => { if (Access) Access.shareFolder(path, done); },
      copy: copyText,
      download: async (path) => { try { await api("/browse/offer?path=" + encodeURIComponent(path), { method: "POST" }); } catch (e) { toast(e.message); haptic("error"); return; } download(path); },
    };
  }
  async function renderFolder(info) {
    const el = CV.el;
    el.replaceChildren(h("div", { class: "fb-skel", "aria-busy": "true", "aria-label": "Opening" }, ...[0, 1, 2, 3, 4, 5].map(() => h("div", { class: "fb-sk" }, h("i", { class: "sk circle" }), h("div", { class: "grow" }, h("i", { class: "sk line" }), h("i", { class: "sk line short" }))))));
    try { await loadFiles(); }
    catch (e) { el.replaceChildren(h("div", { class: "cv-empty" }, h("p", { class: "err", text: e.message }))); return; }
    if (CV.info !== info) return;  // closed, or another file opened, while the browser loaded
    el.classList.add("filebrowser");
    document.body.classList.add("canvas-full");  // like pictures, the browser gets the whole screen
    CV.files = window.AfpFiles.mount(el, { ctx: filesCtx(), data: info.data });
  }

  /* The picture tools are a separate script and style sheet, fetched the first time a picture or gallery is opened. */
  let imagingLoad = null;
  function loadImaging() {
    if (window.AfpImg) return Promise.resolve();
    if (!imagingLoad) {
      imagingLoad = new Promise((resolve, reject) => {
        document.head.append(h("link", { rel: "stylesheet", href: "/static/imaging.css" }));
        const add = (src) => new Promise((ok, bad) => { const s = h("script", { src }); s.onload = ok; s.onerror = () => bad(new Error("Couldn't load " + src)); document.head.append(s); });
        add("/static/imaging-core.js").then(() => add("/static/imaging.js")).then(resolve, (e) => { imagingLoad = null; reject(e); });
      });
    }
    return imagingLoad;
  }
  /** Saves something the page made itself (a picture, a file being edited, the audit log), with the same landing animation as every other download. */
  function saveBlob(blob, name) {
    const url = URL.createObjectURL(blob), a = h("a", { href: url, download: name });
    document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 4000);
    if (!DL.land(name, () => {}, "Saved to this device")) haptic("success");
  }
  /** What the picture screens may use from the app: its helpers, and the PC (always with this phone's token). */
  function imgCtx() {
    return {
      h, toast, haptic,
      get: (path) => api(path),
      putImage: async (path, blob, p) => {
        const q = new URLSearchParams({ path, conversation: S.conv || "default", note: p.note || "" });
        if (p.base_sha) q.set("base_sha", p.base_sha);
        if (p.unique) q.set("unique", "true");
        return (await api("/image?" + q.toString(), { method: "PUT", body: blob, headers: { "Content-Type": blob.type || "application/octet-stream" } })).json();
      },
      close: () => closeCanvas(),
      openImage: (path, mode = "view", opts = {}) => openFile(path, mode, opts),
      openGallery: (folder) => openGallery(folder),
      askAccess,
      undoJob: async (id) => { const r = await (await api(`/jobs/${id}/undo`, { method: "POST" })).json(); refresh(); return r; },
      copy: copyText,
      download: saveBlob,
    };
  }
  async function renderPicture(info) {
    const el = CV.el;
    el.replaceChildren(h("div", { class: "cv-empty" }, h("span", { class: "spin" }), h("p", { text: "Opening…" })));
    document.body.classList.add("canvas-full");  // pictures get the whole screen, not the side panel
    try { await loadImaging(); }
    catch (e) { el.replaceChildren(h("div", { class: "cv-empty" }, h("p", { class: "err", text: "Couldn't load the picture tools. " + e.message }))); return; }
    if (CV.info !== info) return;  // closed, or another file opened, while the tools loaded
    CV.img = window.AfpImg.mount(el, { mode: info.kind === "gallery" ? "gallery" : "viewer", ctx: imgCtx(), info, data: info.data, startEditing: CV.startEditing });
  }

  const urlFor = (path, mode) => (mode === "gallery" ? `#/gallery/${encodeURIComponent(path)}` : mode === "folder" ? `#/folder/${encodeURIComponent(path)}` : `#/file/${mode}/${encodeURIComponent(path)}`);

  function showCanvas(info, mode, opts = {}) {
    if (CV.img) { CV.img.dispose(); CV.img = null; }  // the previous picture's screen (next, previous, or back from the editor)
    if (CV.files) { CV.files.dispose(); CV.files = null; }
    if (CV.el) CV.el.classList.remove("filebrowser");
    CV.returnTo = opts.from || "";  // a file opened from the folder browser: closing it goes back to that folder
    if (CV.blob) { URL.revokeObjectURL(CV.blob); CV.blob = ""; }
    S.jobs.forEach((j) => CV.seenJobs.add(j.id)); // only jobs from now on can mean "the file just changed"
    CV.info = info; CV.sha = info.sha; CV.query = ""; CV.hits = []; CV.cur = -1; CV.md = true; CV.table = true; CV.pretty = false;
    CV.original = info.text.replace(/\r\n?/g, "\n"); // a text box holds \n only; the server puts the file's own line endings back on save
    CV.lang = info.kind === "text" ? langOf(info.name, CV.original) : "text";
    CV.area = h("textarea", { class: "editor", spellcheck: "false", autocapitalize: "off", autocomplete: "off", autocorrect: "off", "aria-label": "File text" });
    CV.area.value = CV.original;
    CV.area.readOnly = !info.can_change || !info.editable;
    CV.mode = info.kind === "gallery" ? "gallery" : info.kind === "folder" ? "folder" : info.kind === "text" && (mode === "edit") ? "edit" : "view";
    CV.startEditing = info.kind === "image" && mode === "edit";  // "edit that photo": straight into the picture editor
    if (!CV.rec) {
      CV.rec = openPanel({ route: "file", hash: urlFor(info.path, CV.mode), push: opts.push !== false, el: CV.el, guard: () => { if (!dirty()) return true; askDiscard("Close without saving?").then((yes) => { if (yes && CV.rec) { CV.rec.guard = null; closeSheet(); } }); return false; }, onClose: hideCanvas });
      document.body.classList.add("canvas-open");
      CV.el.hidden = false;
    } else history.replaceState({ sheet: "file" }, "", urlFor(info.path, CV.mode));
    renderCanvas();
    haptic("light");
  }

  function hideCanvas() {
    const back = CV.returnTo; CV.returnTo = "";
    // Going back to the folder a file was opened from: the panel stays up (with a spinner) until the folder is ready, so Home never flashes through.
    if (CV.el && !back) CV.el.hidden = true;
    if (!back) document.body.classList.remove("canvas-open", "canvas-full");
    if (CV.blob) { URL.revokeObjectURL(CV.blob); CV.blob = ""; }
    if (CV.observer) { CV.observer.disconnect(); CV.observer = null; }
    if (CV.img) { CV.img.dispose(); CV.img = null; }
    if (CV.files) { CV.files.dispose(); CV.files = null; }
    window.onbeforeunload = null;
    CV.rec = null; CV.info = null; CV.area = null; CV.saving = false;
    if (CV.el) { CV.el.replaceChildren(); CV.el.classList.remove("filebrowser"); }
    if (back) {  // closing a file opened from the browser lands you back in its folder
      if (CV.el) CV.el.replaceChildren(h("div", { class: "fb-skel", "aria-busy": "true", "aria-label": "Opening" }, ...[0, 1, 2, 3, 4, 5].map(() => h("div", { class: "fb-sk" }, h("i", { class: "sk circle" }), h("div", { class: "grow" }, h("i", { class: "sk line" }), h("i", { class: "sk line short" }))))));
      setTimeout(async () => {
        for (let i = 0; ignorePops > 0 && i < 40; i++) await new Promise((r) => setTimeout(r, 25));  // let the Back that closed the file finish before the folder takes a new history entry
        await openFolder(back);
        if (!CV.rec && CV.el) { CV.el.hidden = true; CV.el.replaceChildren(); document.body.classList.remove("canvas-open", "canvas-full"); }  // the folder could not be opened: leave the panel
      }, 0);
    }
  }

  const closeCanvas = () => { if (CV.rec) closeSheet(); };

  function setCanvasMode(mode) {
    if (mode === CV.mode) return;
    CV.mode = mode; haptic("tick");
    history.replaceState({ sheet: "file" }, "", urlFor(CV.info.path, mode));
    renderCanvas();
  }

  /* ---- everything inside the panel ---- */

  function renderCanvas() {
    if (CV.info.kind === "folder") { renderFolder(CV.info); return; }  // the folder browser has its own screen (files.js)
    if (CV.info.kind === "image" || CV.info.kind === "gallery") { renderPicture(CV.info); return; }  // pictures have their own screens (imaging.js)
    const info = CV.info, el = CV.el, isText = info.kind === "text";
    const tool = (icon, label, run, pressed) => {
      const b = h("button", { class: "iconbtn", type: "button", "aria-label": label, title: label, onclick: () => { haptic("tick"); run(); } }, ico(icon));
      if (pressed !== undefined) b.setAttribute("aria-pressed", String(!!pressed));
      return b;
    };
    const state = h("span", { class: "chip" });
    const meta = h("span", { class: "ed-meta" });
    CV.state = {};

    /* header: where you are, which mode, and what you can do */
    const seg = isText ? h("div", { class: "seg cv-mode", role: "group", "aria-label": "Mode" },
      ["view", "edit"].map((m) => h("button", { type: "button", "aria-pressed": String(CV.mode === m), onclick: () => setCanvasMode(m) }, m === "view" ? "View" : "Edit"))) : null;
    const head = h("header", { class: "cv-head" },
      h("button", { class: "iconbtn", type: "button", "aria-label": "Close the file", title: "Close", onclick: closeCanvas }, ico("x")),
      h("div", { class: "cv-title" }, h("b", { text: info.name }), h("span", { class: "cv-path", text: info.path, title: info.path })),
      seg, state);

    const tools = h("div", { class: "cv-tools" });
    if (isText) {
      tools.append(tool("search", "Find (Ctrl+F)", () => CV.toggleFind(), false));
      tools.append(tool("wrap", "Wrap long lines", () => { cfg.editorWrap = !cfg.editorWrap; saveCfg(); applyCanvasLook(); }, cfg.editorWrap));
      tools.append(tool("textMinus", "Smaller text", () => { cfg.editorSize = Math.max(10, (cfg.editorSize || 13) - 1); saveCfg(); applyCanvasLook(); }));
      tools.append(tool("textPlus", "Bigger text", () => { cfg.editorSize = Math.min(22, (cfg.editorSize || 13) + 1); saveCfg(); applyCanvasLook(); }));
      tools.append(tool("copy", "Copy all the text", () => copyText(CV.area.value)));
      if (CV.lang === "md") tools.append(tool("eye", "Preview or source", () => { CV.md = !CV.md; renderBody(); }, CV.md));
      if (CV.lang === "csv") tools.append(tool("table", "Table or text", () => { CV.table = !CV.table; renderBody(); }, CV.table));
      if (CV.lang === "json") tools.append(tool("braces", "Format JSON", () => { CV.pretty = !CV.pretty; renderBody(); }, CV.pretty));
    }
    tools.append(tool("download", "Download", downloadIt));
    tools.append(tool("newtab", "Open in a new browser tab", () => {
      if (dirty()) { toast("Save your changes first: a new tab shows the saved file."); return; }
      window.open(location.origin + location.pathname + urlFor(info.path, CV.mode), "_blank", "noopener");
    }));
    const big = tool(document.body.classList.contains("canvas-full") ? "shrink" : "expand", "Full screen", () => { document.body.classList.toggle("canvas-full"); renderCanvas(); }, document.body.classList.contains("canvas-full"));
    big.classList.add("only-wide");
    tools.append(big);

    const sub = h("div", { class: "cv-sub" }, meta);
    const banner = h("div", { class: "cv-banner", hidden: true });
    CV.banner = banner;

    /* find (and in edit mode, replace) */
    const findBox = h("input", { class: "text-input sm", type: "search", placeholder: "Find", "aria-label": "Find" });
    const replBox = h("input", { class: "text-input sm", type: "text", placeholder: "Replace with", "aria-label": "Replace with" });
    const count = h("span", { class: "ed-meta" });
    const replRow = h("div", { class: "row2" }, replBox,
      h("button", { class: "btn sm", type: "button", onclick: () => replaceOne() }, "Replace"), h("button", { class: "btn sm", type: "button", onclick: () => replaceAll() }, "All"));
    const caseBtn = h("button", { class: "btn sm ghost", type: "button", "aria-pressed": String(CV.matchCase), title: "Match case", onclick: () => { CV.matchCase = !CV.matchCase; caseBtn.setAttribute("aria-pressed", String(CV.matchCase)); search(); } }, "Aa");
    const findBar = h("div", { class: "findbar", hidden: true },
      h("div", { class: "row2" }, findBox, caseBtn,
        h("button", { class: "btn sm", type: "button", onclick: () => step(-1), "aria-label": "Previous" }, "↑"), h("button", { class: "btn sm", type: "button", onclick: () => step(1), "aria-label": "Next" }, "↓")),
      replRow, count);
    CV.toggleFind = (open) => {
      findBar.hidden = open === undefined ? !findBar.hidden : !open;
      if (!findBar.hidden) { const sel = CV.mode === "edit" ? CV.area.value.slice(CV.area.selectionStart, CV.area.selectionEnd) : String(getSelection()); if (sel && !sel.includes("\n") && sel.length < 80) findBox.value = sel; findBox.focus(); search(); }
      else { CV.query = ""; CV.hits = []; if (CV.mode === "view") renderBody(); }
    };
    findBox.addEventListener("input", () => search());
    findBox.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); step(e.shiftKey ? -1 : 1); } });

    const spots = () => {
      const q = findBox.value; if (!q) return [];
      const text = CV.area.value, hay = CV.matchCase ? text : text.toLowerCase(), needle = CV.matchCase ? q : q.toLowerCase(), found = [];
      for (let i = hay.indexOf(needle); i !== -1 && found.length < 5000; i = hay.indexOf(needle, i + needle.length)) found.push(i);
      return found;
    };
    const lineAt = (offset) => CV.area.value.slice(0, offset).split("\n").length - 1;
    const search = () => {
      CV.query = findBox.value; CV.hits = spots(); CV.cur = CV.hits.length ? 0 : -1;
      count.textContent = CV.query ? `${CV.hits.length}${CV.hits.length >= 5000 ? "+" : ""} match${CV.hits.length === 1 ? "" : "es"}` : "";
      replRow.hidden = CV.mode !== "edit" || CV.area.readOnly;
      if (CV.mode === "view") { renderBody(); if (CV.cur >= 0) jumpTo(CV.cur); }
    };
    const jumpTo = (n) => {
      CV.cur = n; const at = CV.hits[n], line = lineAt(at);
      if (CV.mode === "edit") {
        CV.area.focus({ preventScroll: true }); CV.area.setSelectionRange(at, at + CV.query.length);
        const lh = parseFloat(getComputedStyle(CV.area).lineHeight) || 20;
        CV.area.scrollTop = Math.max(0, line * lh - CV.area.clientHeight / 3);
      } else {
        ensureRendered(line + 40);
        document.querySelectorAll(".cv-code .hit.cur").forEach((n2) => n2.classList.remove("cur"));
        const mark = [...document.querySelectorAll(`.cv-code .ln[data-i="${line}"] .hit`)][hitInLine(n)];
        const target = mark || document.querySelector(`.cv-code .ln[data-i="${line}"]`);
        if (mark) mark.classList.add("cur");
        if (target) target.scrollIntoView({ block: "center" });
      }
    };
    const hitInLine = (n) => { const line = lineAt(CV.hits[n]); let k = 0; for (let j = 0; j < n; j++) if (lineAt(CV.hits[j]) === line) k++; return k; };
    const step = (dir) => {
      if (!CV.hits.length) { haptic("warning"); return; }
      jumpTo((CV.cur + dir + CV.hits.length) % CV.hits.length); haptic("tick");
    };
    const replaceOne = () => {
      if (CV.area.readOnly || !CV.query) return;
      const sel = CV.area.value.slice(CV.area.selectionStart, CV.area.selectionEnd);
      if (CV.matchCase ? sel === CV.query : sel.toLowerCase() === CV.query.toLowerCase()) { CV.area.setRangeText(replBox.value, CV.area.selectionStart, CV.area.selectionEnd, "end"); CV.area.dispatchEvent(new Event("input")); haptic("light"); }
      search(); if (CV.hits.length) jumpTo(Math.min(CV.cur, CV.hits.length - 1));
    };
    const replaceAll = () => {
      if (CV.area.readOnly) return;
      const all = spots(); if (!all.length) { haptic("warning"); return; }
      for (const at of all.reverse()) CV.area.setRangeText(replBox.value, at, at + CV.query.length, "preserve");
      CV.area.dispatchEvent(new Event("input")); search(); haptic("success"); toast(`Replaced ${all.length}`);
    };

    /* the body: a view, a picture, a document, or the text box */
    const body = h("div", { class: "cv-body" });
    CV.body = body;
    const foot = h("div", { class: "actions cv-foot", hidden: CV.mode !== "edit" });
    const save = h("button", { class: "btn primary", type: "button" }, ico("check"), "Save");
    const review = h("button", { class: "btn", type: "button" }, "Review");
    const ask = h("button", { class: "btn ghost", type: "button" }, ico("sparkle"), "Ask AI");
    foot.append(ask, review, save);

    const sync = () => {
      const isDirty = dirty(), editMode = CV.mode === "edit";
      state.className = "chip " + (isText ? (info.editable ? (!info.can_change ? "expired" : isDirty ? "awaiting_approval" : "done") : "") : "");
      state.textContent = !isText ? info.kind.toUpperCase() : !info.editable ? "View only" : !info.can_change ? "Read only" : isDirty ? "Unsaved changes" : "Saved";
      if (isText) {
        const lines = CV.area.value.split("\n").length;
        meta.textContent = `${lines.toLocaleString()} line${lines === 1 ? "" : "s"} · ${fmtSize(new Blob([CV.area.value]).size)}${CV.lang !== "text" ? " · " + CV.lang.toUpperCase() : ""}`;
      } else meta.textContent = fmtSize(info.size);
      save.disabled = !isDirty || !info.can_change || !info.editable || CV.saving;
      review.disabled = !isDirty; ask.disabled = isDirty; ask.title = isDirty ? "Save first, so the AI sees your changes" : "";
      window.onbeforeunload = isDirty ? () => "You have unsaved changes." : null;
      if (!editMode) return;
    };
    CV.sync = sync;
    if (isText) CV.area.addEventListener("input", () => { sync(); if (CV.query) { CV.hits = spots(); count.textContent = `${CV.hits.length} match${CV.hits.length === 1 ? "" : "es"}`; } });

    const note = (cls, text, ...actions) => h("div", { class: cls }, h("span", { text }), ...actions);
    const showBanner = () => {
      banner.replaceChildren(); banner.hidden = true;
      if (isText && !info.editable && info.reason) { banner.append(h("span", { text: info.reason })); banner.hidden = false; }
      else if (isText && CV.mode === "edit" && !info.can_change) {
        banner.append(h("span", { text: "This folder is read-only, so you can look but not change. " }),
          h("button", { class: "btn sm primary", type: "button", onclick: () => askAccess(info.folder, true, () => { CV.original = CV.area.value; closeSheet(); setTimeout(() => openFile(info.path, "edit"), 300); }) }, ico("pencil"), "Allow editing in this folder"));
        banner.hidden = false;
      } else if (!isText && info.reason) { banner.append(h("span", { text: info.reason })); banner.hidden = false; }
    };
    CV.showBanner = showBanner;

    function renderBody() {
      body.replaceChildren();
      if (CV.observer) { CV.observer.disconnect(); CV.observer = null; }
      foot.hidden = CV.mode !== "edit";
      replRow.hidden = CV.mode !== "edit" || CV.area.readOnly;
      showBanner(); sync();
      if (info.kind === "image" || info.kind === "pdf") { mediaView(body, info); return; }
      if (info.kind !== "text") {
        body.append(h("div", { class: "cv-empty" }, ico("file", 40), h("p", { text: info.reason || "This file can't be shown here." }),
          h("button", { class: "btn primary", type: "button", onclick: () => { closeCanvas(); setTimeout(() => send(`download "${info.path}"`), 320); } }, ico("download"), "Ask to send it to my phone")));
        return;
      }
      applyCanvasLook();
      if (CV.mode === "edit") { body.append(CV.area); return; }
      const text = CV.area.value;
      if (CV.lang === "md" && CV.md && !CV.query) { body.append(h("div", { class: "cv-doc" }, markdownNodes(text))); return; }
      if (CV.lang === "csv" && CV.table && !CV.query) { body.append(h("div", { class: "cv-doc" }, csvTable(text, info.name.toLowerCase().endsWith(".tsv") ? "\t" : ","))); return; }
      let shown = text;
      if (CV.lang === "json" && CV.pretty) { try { shown = JSON.stringify(JSON.parse(text), null, 2); } catch { toast("That isn't valid JSON, so it is shown as it is."); } }
      CV.lines = shown.split("\n"); CV.rendered = 0; CV.codeState = {};
      const code = h("div", { class: "cv-code" + (cfg.editorWrap ? " wrap" : "") });
      CV.code = code; body.append(code);
      ensureRendered(200);
    }
    CV.renderBody = renderBody;

    function applyCanvasLook() {
      CV.el.style.setProperty("--ed", (cfg.editorSize || 13) + "px");
      if (CV.area) CV.area.setAttribute("wrap", cfg.editorWrap ? "soft" : "off");
      if (CV.code) CV.code.classList.toggle("wrap", !!cfg.editorWrap);
      const wrapBtn = tools.querySelector('[aria-label="Wrap long lines"]'); if (wrapBtn) wrapBtn.setAttribute("aria-pressed", String(!!cfg.editorWrap));
    }
    CV.applyLook = applyCanvasLook;

    /* ---- actions ---- */
    CV.save = async (overwrite) => {
      if (CV.saving || (!dirty() && !overwrite)) return;
      CV.saving = true; sync(); haptic("medium");
      try {
        const out = await (await api("/file", { method: "PUT", body: JSON.stringify({ path: info.path, text: CV.area.value, base_sha: overwrite ? null : CV.sha, conversation: S.conv }) })).json();
        if (out.saved) {
          CV.sha = out.sha; CV.original = CV.area.value;
          S.jobs = [out.job, ...S.jobs.filter((j) => j.id !== out.job.id)]; S.mine.add(out.job.id); S.stick = true; update(true);
          toast("Saved. You can undo it in the chat."); haptic("success"); refresh();
        } else toast("Nothing to save.");
      } catch (e) {
        if (e.code === 401) { CV.saving = false; return; }
        if (e.status === 409) conflict();
        else if (e.data && e.data.code === "needs_change_access") askAccess(e.data.folder, true, () => { CV.saving = false; CV.save(false); });
        else { toast(e.message); haptic("error"); }
      }
      CV.saving = false; sync();
    };
    const conflict = () => {
      haptic("warning");
      const box = h("div", { class: "help" }, h("p", { text: `${info.name} was changed on the PC after you opened it. Saving now would replace those changes.` }));
      const mine = h("button", { class: "btn danger", type: "button" }, "Save mine anyway");
      const theirs = h("button", { class: "btn", type: "button" }, "Reload what's on the PC (lose my edits)");
      box.append(h("div", { class: "actions", style: "margin-top:12px" }, mine, theirs, h("button", { class: "btn ghost", type: "button", onclick: () => closeSheet() }, "Cancel")));
      const sh = openSheet({ title: "The file changed", body: box, route: "conflict" });
      mine.onclick = () => { sh.close(); setTimeout(() => CV.save(true), 260); };
      theirs.onclick = () => { sh.close(); setTimeout(() => { CV.original = CV.area.value; reopen(CV.mode); }, 260); };
    };
    const reopen = (mode) => { CV.original = CV.area.value; closeSheet(); setTimeout(() => openFile(info.path, mode), 320); };
    CV.reopen = reopen;
    review.onclick = () => {
      const c = countChanges(CV.original, CV.area.value), box = h("div");
      box.append(h("p", { class: "note", text: `${c["+"] || 0} added, ${c["-"] || 0} removed` }), diffView(CV.original, CV.area.value));
      const yes = h("button", { class: "btn primary", type: "button" }, ico("check"), "Save these changes");
      box.append(h("div", { class: "actions", style: "margin-top:12px" }, yes, h("button", { class: "btn ghost", type: "button", onclick: () => closeSheet() }, "Keep editing")));
      const sh = openSheet({ title: "Review changes", body: box, route: "review", full: true });
      yes.onclick = () => { sh.close(); setTimeout(() => CV.save(false), 260); };
    };
    save.onclick = () => CV.save(false);
    ask.onclick = () => {
      if (dirty()) return;
      closeCanvas(); S.focus[S.conv] = info.path; haptic("light");
      setTimeout(() => {
        if (S.mode !== "type") { cfg.input = "type"; saveCfg(); setMode("type"); }
        if (D.field) { D.field.value = "In that file, "; D.sync(); D.field.focus(); D.field.setSelectionRange(D.field.value.length, D.field.value.length); }
        toast("Say what to change. You'll approve it in the chat.");
      }, 320);
    };

    async function downloadIt() {
      let blob;
      if (isText) blob = new Blob([CV.area.value], { type: "text/plain;charset=utf-8" });
      else { try { blob = await mediaBlob(info); } catch { toast("Couldn't fetch the file."); return; } }
      saveBlob(blob, info.name);
    }

    el.replaceChildren(head, tools, sub, banner, findBar, body, foot);
    renderBody();
    if (CV.mode === "edit" && info.editable && info.can_change) setTimeout(() => CV.area.focus({ preventScroll: true }), 350);
  }

  function fmtSize(bytes) {
    return bytes < 1024 ? `${bytes} B` : bytes < 1048576 ? `${(bytes / 1024).toFixed(bytes < 10240 ? 1 : 0)} KB` : bytes < 1073741824 ? `${(bytes / 1048576).toFixed(1)} MB` : `${(bytes / 1073741824).toFixed(2)} GB`;
  }

  /* line-numbered code, drawn a few hundred lines at a time so a big log opens at once */
  function ensureRendered(upTo) {
    if (!CV.code) return;
    const to = Math.min(CV.lines.length, Math.max(upTo, CV.rendered + 1));
    CV.code.querySelector(".cv-more")?.remove();
    const frag = document.createDocumentFragment(), width = String(CV.lines.length).length;
    for (let i = CV.rendered; i < to; i++) frag.append(lineNode(i, width));
    CV.code.append(frag); CV.rendered = to;
    if (CV.rendered < CV.lines.length) {
      const more = h("div", { class: "cv-more", text: `Showing ${CV.rendered.toLocaleString()} of ${CV.lines.length.toLocaleString()} lines` });
      CV.code.append(more);
      CV.observer && CV.observer.disconnect();
      CV.observer = new IntersectionObserver((entries) => { if (entries[0].isIntersecting) ensureRendered(CV.rendered + 400); }, { root: CV.body, rootMargin: "800px" });
      CV.observer.observe(more);
    }
  }

  function lineNode(i, width) {
    const row = h("div", { class: "ln", "data-i": String(i) }, h("span", { class: "no", text: String(i + 1).padStart(width, " ") }));
    const tx = h("span", { class: "tx" });
    const pieces = colourLine(CV.lines[i], CV.lang, CV.codeState);
    const q = CV.query, ql = CV.matchCase ? q : q.toLowerCase();
    for (const [cls, text] of pieces) {
      const piece = cls ? h("span", { class: "tk-" + cls }) : null;
      const into = piece || tx;
      if (!q) { into.append(text); }
      else {
        const hay = CV.matchCase ? text : text.toLowerCase(); let last = 0;
        for (let at = hay.indexOf(ql); at !== -1; at = hay.indexOf(ql, last)) {
          if (at > last) into.append(text.slice(last, at));
          into.append(h("mark", { class: "hit", text: text.slice(at, at + q.length) })); last = at + q.length;
        }
        if (last < text.length) into.append(text.slice(last));
      }
      if (piece) tx.append(piece);
    }
    if (!CV.lines[i]) tx.append("\u200b");
    row.append(tx);
    return row;
  }

  /* pictures and PDFs are fetched with the access token, then shown from memory */
  async function mediaBlob(info) {
    const res = await api("/file/raw?path=" + encodeURIComponent(info.path));
    return res.blob();
  }
  async function mediaView(body, info) {
    if (info.reason) { body.append(h("div", { class: "cv-empty" }, ico("file", 40), h("p", { text: info.reason }))); return; }
    const loading = h("div", { class: "cv-empty" }, h("span", { class: "spin" }), h("p", { text: "Loading…" }));
    body.append(loading);
    try {
      const blob = await mediaBlob(info);
      if (!CV.info || CV.info.path !== info.path) return; // closed or switched while it loaded
      CV.blob = URL.createObjectURL(blob); loading.remove();
      if (info.kind === "image") {
        const img = h("img", { class: "cv-img fit", src: CV.blob, alt: info.name, draggable: "false" });
        img.onclick = () => { img.classList.toggle("fit"); haptic("tick"); };
        img.onerror = () => { img.replaceWith(h("p", { class: "err", text: "This picture can't be shown." })); };
        body.append(h("div", { class: "cv-media" }, img), h("p", { class: "cv-hint", text: "Tap the picture to switch between fitting the screen and full size." }));
      } else {
        body.append(h("iframe", { class: "cv-pdf", src: CV.blob, title: info.name }),
          h("p", { class: "cv-hint" }, "If the document doesn't appear, ", h("a", { href: CV.blob, target: "_blank", rel: "noopener", text: "open the PDF" }), " or download it."));
      }
    } catch (e) { loading.replaceChildren(h("p", { class: "err", text: e.message || "Couldn't load the file." })); }
  }

  /* a file the AI (or you, in another tab) just changed: keep the panel honest */
  function canvasWatch() {
    if (!CV.rec || !CV.info || CV.info.kind !== "text") return;
    for (const j of S.jobs) {
      if (CV.seenJobs.has(j.id) || j.status !== "done" || !(j.changed || []).length) continue;
      CV.seenJobs.add(j.id);
      if (!(j.changed || []).some((p) => p.replace(/\//g, "\\").toLowerCase().endsWith(CV.info.name.toLowerCase()))) continue;
      if (j.prompt === `Edited ${CV.info.name} by hand`) continue; // that was this panel saving
      if (!dirty()) { toast("The AI changed this file. Showing the new version."); CV.reopen(CV.mode); }
      else { CV.banner.replaceChildren(h("span", { text: "This file was just changed on the PC. " }), h("button", { class: "btn sm", type: "button", onclick: () => CV.reopen(CV.mode) }, "Reload (lose my edits)")); CV.banner.hidden = false; }
    }
  }

  /* "Edit that file" / "open it full screen" open the panel by themselves the first time, for the request you just made */
  function autoOpenEditor() {
    canvasWatch();
    for (const j of S.jobs) {
      if (j.open && j.status === "done" && S.mine.has(j.id) && !S.editorOpened.has(j.id) && S.sheets.every((r) => r.custom)) {
        S.editorOpened.add(j.id);
        if (j.open_mode === "folder") openFolder(j.open); else if (j.open_mode === "gallery") openGallery(j.open); else openFile(j.open, j.open_mode || "edit");
        return;
      }
    }
  }


  document.addEventListener("keydown", (e) => {
    const topSheet = S.sheets[S.sheets.length - 1];
    const focusScope = S.dialog ? $(".dlg-wrap:not(.out)") : topSheet && !topSheet.custom ? topSheet.wrap : null;
    if (e.key === "Tab" && focusScope && !openMenu) {
      const controls = [...focusScope.querySelectorAll('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), a[href], summary, [tabindex="0"]')].filter((el) => el.getClientRects().length);
      const first = controls[0], last = controls[controls.length - 1], active = document.activeElement;
      if (!first) { e.preventDefault(); return; }
      if (!focusScope.contains(active) || (e.shiftKey && (active === first || active.matches(".sheet"))) || (!e.shiftKey && active === last)) {
        e.preventDefault(); (e.shiftKey ? last : first).focus(); return;
      }
    }
    if (e.key === "Escape") {
      if (S.dialog) { S.dialog(false); return; }
      const scanner = $(".scan[role='dialog']");
      if (scanner) { $(".scan-x", scanner).click(); return; }
      if (openMenu) closeMenu(); else if (S.sheets.length) closeSheet(); else if (document.body.classList.contains("drawer")) closeDrawer();
    } else if (e.key === "/" && !/^(INPUT|TEXTAREA)$/.test((document.activeElement || {}).tagName || "") && !S.sheets.length) {
      e.preventDefault(); if (D.field) D.field.focus(); else { cfg.input = "type"; setMode("type"); setTimeout(() => D.field && D.field.focus(), 40); }
    } else if (e.altKey && (e.key === "n" || e.key === "N")) { e.preventDefault(); newChat(); }
  });
  let swipe = null;
  document.addEventListener("touchstart", (e) => { const t = e.touches[0]; swipe = { x: t.clientX, y: t.clientY, edge: t.clientX < 24 }; }, { passive: true });
  document.addEventListener("touchend", (e) => {
    if (!swipe) return;
    const t = e.changedTouches[0], dx = t.clientX - swipe.x, dy = Math.abs(t.clientY - swipe.y);
    if (dy < 40 && dx > 70 && swipe.edge && !document.body.classList.contains("drawer") && innerWidth < 900) openDrawer();
    else if (dy < 40 && dx < -70 && document.body.classList.contains("drawer")) closeDrawer();
    swipe = null;
  }, { passive: true });
  $("#scrim").addEventListener("click", closeDrawer);
  $("#offlineRetry").addEventListener("click", () => { haptic("light"); ping(); });
  $("#scroller").addEventListener("scroll", () => { S.stick = atBottom(); $("#jump").hidden = S.stick; }, { passive: true });
  $("#jump").append(ico("arrowDown"));
  if ("ResizeObserver" in window) new ResizeObserver(() => $(".main").style.setProperty("--dock-h", $("#dock").offsetHeight + "px")).observe($("#dock"));
  $("#jump").addEventListener("click", () => {
    S.stick = true; haptic("tick");
    const reduced = calmMotion();
    $("#scroller").scrollTo({ top: $("#scroller").scrollHeight, behavior: reduced ? "auto" : "smooth" });
  });
  window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); S.deferredInstall = e; });
  document.addEventListener("visibilitychange", () => { if (document.hidden) { if (V.phase !== "idle") voiceEnd(true); } else refresh(); syncTicks(); });
  window.addEventListener("blur", () => { if (V.phase !== "idle" && V.held) voiceEnd(true); });
  window.addEventListener("online", refresh);

  /* The app opens its own copy of this page, at #/offline, when the PC's page can't load but chats were saved. */
  const bootRoute = (location.hash || "").replace(/^#\/?/, "");
  document.addEventListener("afp-retry-failed", () => {  // the app looked for the PC and it still isn't answering
    if (!S.offline) return;
    S.offline.checking = false; savedBar(); toast("Still can't reach your PC."); haptic("error");
  });
  async function bootSaved() {
    await savedReady;
    setTab("chat");
    const snap = Saved.current();
    if (!snap || !window.AfpOffline.valid(snap)) { showAuth(); return; }
    showSaved(snap, true);
  }

  function start() {
    Lock.show();  // before anything else: a locked app shows nothing until the PIN is in
    const splash = $("#splash");
    if (splash) {
      if (getComputedStyle(splash).display === "none") splash.remove();
      else { splash.addEventListener("animationend", (e) => { if (e.animationName === "sp-out") splash.remove(); }); setTimeout(() => splash.remove(), 1500); }
    }
    applyLook();
    if (store.get("afp_side_collapsed", false)) document.body.classList.add("side-collapsed");
    chooseMode();
    S.model = buildModel();
    if (!S.conv) { S.conv = newId(); store.setRaw("afp_conv", S.conv); }
    renderDock(); renderSide(); renderTop(); renderThread(); paintPhase();
    // Home is always the bottom of the stack. If the chat was where you left off, it opens on top of Home, so Back still has somewhere to go.
    const resumeChat = store.raw("afp_tab") === "chat" && !location.hash.replace("#", "").replace("/", "");
    setTab("home", { push: false });
    if (resumeChat) setTab("chat");
    if (pairFromUrl) { const el = showAuth() || $(".auth"); if (el) { S.token = ""; connect(pairFromUrl, el); } }
    else if (!S.token && bootRoute === "offline") bootSaved();
    else if (!S.token) showAuth(); else { refresh().then(() => { update(true); }); loadInfo(); loadPcStatus(); }
    // The Wi-Fi speed and battery next to each other refresh every 2 seconds, but only while the screen is on, the PC is reachable and nothing else is asking.
    setInterval(() => { if (!document.hidden && S.token && !S.offline && Link && Link.state === "connected") loadPcStatus(); }, 2000);
    loop();
    schedulePing();  // the heartbeat that notices the PC going quiet
    if (Q) {
      Q.onChange(() => { if (S.model) update(true); paintAway(); if (S.tab === "home" && S.model) renderHome(); });
      setInterval(queueTick, 3000);  // hands held requests over as soon as the PC answers, and marks the ones that waited too long
      setTimeout(flushQueue, 1500);  // requests held before the app was closed go out once it is connected
    }
    // Deep links such as /#/settings open that sheet without adding a second history entry.
    const route = (location.hash || "").replace(/^#\/?/, "");
    const file = /^file\/(view|edit)\/(.+)$/.exec(route), gallery = /^gallery\/(.+)$/.exec(route), folderRoute = /^folder\/(.*)$/.exec(route);
    if (file) { let target = ""; try { target = decodeURIComponent(file[2]); } catch { /* a damaged link */ } if (target && S.token) openFile(target, file[1], { push: false }); }
    else if (gallery) { let target = ""; try { target = decodeURIComponent(gallery[1]); } catch { /* a damaged link */ } if (target && S.token) openGallery(target, { push: false }); }
    else if (folderRoute) { let target = ""; try { target = decodeURIComponent(folderRoute[1]); } catch { /* a damaged link */ } if (S.token) openFolder(target, { push: false }); }
    else if (route === "files") { if (S.token) openFolder("", { push: false }); }
    else if (route === "settings") openSettings(true);
    else if (route === "activity") openActivity(true);
    else if (route === "drawer") openDrawer();
    else if (route === "ask") { setTab("chat"); setTimeout(() => { const f = $(".field"); if (f) f.focus(); }, 400); }  // the home-screen shortcut "Ask my PC"
    const job = /^job\/([\w-]+)$/.exec(route);
    if (job) { history.replaceState(history.state, "", location.pathname); openJob(job[1]); }
    Push.refreshQuietly();
  }

  start();  
})();
