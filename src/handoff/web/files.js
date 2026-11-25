/* The file browser: walk through your PC's folders from the phone, one folder at a time, and open what you find.
 *
 *   AfpFiles.mount(host, { ctx, data }) -> { dispose(), dirty() }
 *
 * `data` is the first answer from /api/browse. Everything else the screen needs from the app comes through `ctx`
 * (its element helper, toasts, haptics and the PC calls, which always carry this phone's token), so nothing here talks to
 * the PC on its own. Names come from the PC's disk and only ever reach the page as text, never as markup.
 */
(function () {
  "use strict";

  const ICONS = {
    folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/>',
    root: '<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/>',
    file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
    text: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/>',
    image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="m21 16-5-5-9 9"/>',
    pdf: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M8 16h1.5a1.5 1.5 0 0 0 0-3H8v5"/>',
    video: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m10 9 5 3-5 3z"/>',
    audio: '<path d="M9 18V6l11-2v12"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>',
    archive: '<rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9M10 13h4"/>',
    sheet: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M3 15h18M9 4v16"/>',
    doc: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M8 13h8M8 17h5"/>',
    app: '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M9 9h6v6H9z"/>',
    chevR: '<path d="m9 6 6 6-6 6"/>',
    up: '<path d="m15 18-6-6 6-6"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
    x: '<path d="M18 6 6 18M6 6l12 12"/>',
    more: '<circle cx="12" cy="5" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="12" cy="19" r="1.4"/>',
    refresh: '<path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 3v6h-6"/>',
    eye: '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    eyeOff: '<path d="M3 3l18 18M10.6 6.1A10 10 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-3.2 4.2M6.6 6.6A17 17 0 0 0 2 12s3.6 7 10 7a9.7 9.7 0 0 0 4.4-1"/>',
    asc: '<path d="M12 19V5M6 11l6-6 6 6"/>',
    desc: '<path d="M12 5v14M6 13l6 6 6-6"/>',
    download: '<path d="M12 4v11M7 11l5 5 5-5M5 20h14"/>',
    copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5h.01"/>',
    grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    open: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    lock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    share: '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="M8.6 10.5l6.8-4M8.6 13.5l6.8 4"/>',
  };

  function icon(name) {
    const s = document.createElement("span");
    s.className = "ico"; s.setAttribute("aria-hidden", "true");
    s.innerHTML = '<svg viewBox="0 0 24 24" focusable="false">' + (ICONS[name] || ICONS.file) + "</svg>";  // fixed strings above, never file names
    return s;
  }

  function fmtSize(n) {
    if (n < 1024) return n + " B";
    const units = ["KB", "MB", "GB", "TB"];
    let v = n / 1024, i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return (v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2)).replace(/\.?0+$/, "") + " " + units[i];
  }

  function fmtDate(ts) {
    if (!ts) return "";
    const d = new Date(ts * 1000), now = new Date();
    const same = d.toDateString() === now.toDateString();
    if (same) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    const opts = d.getFullYear() === now.getFullYear() ? { day: "numeric", month: "short" } : { day: "numeric", month: "short", year: "numeric" };
    return d.toLocaleDateString([], opts);
  }

  const ACCESS = { writable: "Read and change", read_only: "Read only", granted: "Allowed by you", granted_change: "Allowed by you, can edit", shared: "Shared with you", shared_change: "Shared with you, can edit" };
  const plural = (n, one, many) => n + " " + (n === 1 ? one : many);

  function mount(host, o) {
    const ctx = o.ctx, h = ctx.h;
    const st = { path: "", data: null, entries: [], sort: "name", order: "asc", q: "", hidden: false, loading: false, token: 0 };
    let disposed = false, timer = null, observer = null, searchTimer = null, overlay = null;

    /* ---- the frame: header, places path, tools, list ---- */
    const title = h("div", { class: "cv-title" });
    const up = h("button", { class: "iconbtn", type: "button", "aria-label": "Up one folder", title: "Up one folder", onclick: () => { ctx.haptic("tick"); goUp(); } }, icon("up"));
    const head = h("header", { class: "cv-head" }, up, title,
      h("button", { class: "iconbtn", type: "button", "aria-label": "Close", title: "Close", onclick: () => ctx.close() }, icon("x")));
    const crumbs = h("nav", { class: "fb-crumbs", "aria-label": "Where you are" });
    const search = h("input", { class: "text-input sm", type: "search", placeholder: "Search here", "aria-label": "Search in this folder", autocomplete: "off" });
    const sort = h("select", { class: "im-select", "aria-label": "Sort by" },
      [["name", "Name"], ["modified", "Date"], ["size", "Size"], ["type", "Type"]].map((s) => h("option", { value: s[0], text: s[1] })));
    const orderBtn = h("button", { class: "iconbtn", type: "button" });
    const hiddenBtn = h("button", { class: "iconbtn", type: "button" });
    const refreshBtn = h("button", { class: "iconbtn", type: "button", "aria-label": "Refresh", title: "Refresh", onclick: () => { ctx.haptic("tick"); load(st.path); } }, icon("refresh"));
    const tools = h("div", { class: "fb-tools" }, h("label", { class: "gl-search" }, icon("search"), search), sort, orderBtn, hiddenBtn, refreshBtn);
    const summary = h("div", { class: "fb-summary" });
    const list = h("div", { class: "fb-list", role: "list" });
    const more = h("div", { class: "fb-more", hidden: true }, h("span", { class: "spin" }), h("span", { text: "Loading more…" }));
    const scroller = h("div", { class: "fb-scroll" }, list, more);
    host.replaceChildren(head, crumbs, tools, summary, scroller);

    function paintTools() {
      orderBtn.replaceChildren(icon(st.order === "asc" ? "asc" : "desc"));
      const orderLabel = st.order === "asc" ? "Ascending. Tap to reverse." : "Descending. Tap to reverse.";
      orderBtn.setAttribute("aria-label", orderLabel); orderBtn.title = orderLabel;
      hiddenBtn.replaceChildren(icon(st.hidden ? "eye" : "eyeOff"));
      const hl = st.hidden ? "Hidden files are shown. Tap to hide them." : "Hidden files are not shown. Tap to show them.";
      hiddenBtn.setAttribute("aria-label", hl); hiddenBtn.title = hl; hiddenBtn.setAttribute("aria-pressed", String(st.hidden));
    }
    orderBtn.onclick = () => { ctx.haptic("tick"); st.order = st.order === "asc" ? "desc" : "asc"; paintTools(); load(st.path); };
    hiddenBtn.onclick = () => { ctx.haptic("tick"); st.hidden = !st.hidden; paintTools(); load(st.path); };
    sort.onchange = () => { st.sort = sort.value; st.order = st.sort === "modified" || st.sort === "size" ? "desc" : "asc"; paintTools(); load(st.path); };
    search.oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { st.q = search.value.trim(); load(st.path); }, 280); };
    paintTools();

    /* ---- loading ---- */
    const query = (path, offset, limit) => new URLSearchParams({ path, q: st.q, sort: st.sort, order: st.order, hidden: String(st.hidden), offset: String(offset), limit: String(limit) }).toString();

    async function fetchPage(path, offset, limit) {
      return (await ctx.get("/browse?" + query(path, offset, limit))).json();
    }

    async function load(path, opts) {
      opts = opts || {};
      const token = ++st.token;
      const changedFolder = path !== st.path || !st.data;
      if (changedFolder) { st.q = ""; search.value = ""; }
      st.loading = true;
      if (changedFolder) list.replaceChildren(h("div", { class: "fb-skel", "aria-busy": "true", "aria-label": "Opening" }, ...[0, 1, 2, 3, 4, 5].map(() => h("div", { class: "fb-sk" }, h("i", { class: "sk circle" }), h("div", { class: "grow" }, h("i", { class: "sk line" }), h("i", { class: "sk line short" }))))));
      try {
        const data = await fetchPage(path, 0, 200);
        if (disposed || token !== st.token) return;
        st.path = data.path; st.data = data; st.entries = data.entries.slice();
        paint(changedFolder || opts.top);
        if (changedFolder) scroller.scrollTop = 0;
      } catch (e) {
        if (disposed || token !== st.token) return;
        st.loading = false;
        if (e.code === 401) return;
        if (e.data && e.data.code === "needs_access") {
          ctx.askAccess(e.data.folder, false, () => load(path, opts));
          if (!st.data) list.replaceChildren(h("div", { class: "fb-empty" }, icon("lock"), h("p", { text: "This folder has to be allowed first." }),
            h("button", { class: "btn", type: "button", onclick: () => load("") }, "Back to my folders")));
          return;
        }
        ctx.haptic("error");
        const message = e.message || "Couldn't open that folder.";
        if (st.data) { ctx.toast(message); return; }
        list.replaceChildren(h("div", { class: "fb-empty" }, icon("info"), h("p", { text: message }),
          h("button", { class: "btn", type: "button", onclick: () => load("") }, "Back to my folders")));
      } finally { if (token === st.token) st.loading = false; }
    }

    async function loadMore() {
      if (st.loading || !st.data || !st.data.more) return;
      st.loading = true; more.hidden = false;
      const token = st.token;
      try {
        const data = await fetchPage(st.path, st.entries.length, 200);
        if (disposed || token !== st.token) return;
        st.data.more = data.more; st.data.total = data.total;
        st.entries.push(...data.entries);
        appendRows(data.entries);
        paintSummary();
      } catch (e) { if (e.code !== 401) ctx.toast(e.message); }
      finally { st.loading = false; more.hidden = !(st.data && st.data.more); }
    }

    function goUp() {
      if (st.data && st.data.root) { ctx.close(); return; }
      load(st.data ? st.data.parent || "" : "");
    }

    /* ---- painting ---- */
    function paint(keepTop) {
      const d = st.data;
      title.replaceChildren(h("b", { text: d.root ? "My folders" : d.name }), h("span", { class: "cv-path", text: d.root ? "Pick a place to start" : d.path, title: d.path }));
      up.hidden = !!d.root;  // the list of places has nowhere to go up to: only the one close button at the right
      paintCrumbs();
      tools.hidden = !!d.root;
      paintSummary();
      list.replaceChildren();
      if (!st.entries.length) {
        list.append(h("div", { class: "fb-empty" }, icon(st.q ? "search" : "folder"),
          h("p", { text: d.root ? "No folders are open to the app yet. Ask it to open one, or allow one in Settings." : st.q ? "Nothing here matches “" + st.q + "”." : "This folder is empty." })));
      } else appendRows(st.entries);
      more.hidden = !d.more;
      watchEnd();
    }

    function paintCrumbs() {
      const d = st.data, parts = [];
      const piece = (label, path, last) => {
        const b = h("button", { class: "fb-crumb" + (last ? " on" : ""), type: "button", text: label, title: path || "My folders" });
        if (last) b.setAttribute("aria-current", "page"); else b.onclick = () => { ctx.haptic("tick"); load(path); };
        return b;
      };
      parts.push(piece("My folders", "", d.root));
      if (!d.root) d.crumbs.forEach((c, i) => { parts.push(h("span", { class: "fb-sep", "aria-hidden": "true", text: "›" })); parts.push(piece(c.name, c.path, i === d.crumbs.length - 1)); });
      crumbs.replaceChildren(...parts);
      crumbs.scrollLeft = crumbs.scrollWidth;
    }

    function paintSummary() {
      const d = st.data;
      if (d.root) { summary.textContent = plural(d.total, "place", "places"); return; }
      const bits = [];
      if (d.folders) bits.push(plural(d.folders, "folder", "folders"));
      if (d.files) bits.push(plural(d.files, "file", "files"));
      summary.replaceChildren(h("span", { text: bits.length ? bits.join(" · ") : (st.q ? "No matches" : "Empty") }));
      if (d.truncated) summary.append(h("span", { class: "fb-warn", text: " · Only the first 100,000 are listed. Search to narrow it down." }));
      if (!d.can_change) summary.append(h("span", { class: "fb-tag", text: "Read only" }));
      if (st.entries.some((e) => e.type === "image")) {
        summary.append(h("button", { class: "fb-link", type: "button", onclick: () => { ctx.haptic("light"); ctx.openGallery(st.path); } }, icon("image"), "Pictures"));
      }
    }

    /* On the Admin phone a place also says which phone allowed it and who else has it. */
    function rootSub(e) {
      if (!e.shareable) return ACCESS[e.access] || "";
      const who = (e.owners || []).join(", ") || "A phone", shared = e.shared_with || [];
      return "Allowed by " + who + " · " + (shared.length ? "Shared with " + shared.join(", ") : "Not shared");
    }

    function row(e) {
      const folder = e.kind === "folder";
      const sub = e.type === "root" ? rootSub(e) : folder ? fmtDate(e.modified) : [fmtSize(e.size), fmtDate(e.modified)].filter(Boolean).join(" · ");
      const main = h("button", { class: "fb-main", type: "button", role: "listitem", "aria-label": (folder ? "Folder " : "File ") + e.name },
        h("span", { class: "fb-ic t-" + e.type }, icon(e.type)),
        h("span", { class: "fb-text" }, h("b", { class: "fb-name", text: e.name }),
          e.type === "root" ? h("span", { class: "fb-where", text: e.path, title: e.path }) : null,  // two places can share a name: the path tells them apart
          h("span", { class: "fb-sub", text: sub })),
        folder ? h("span", { class: "fb-go" }, icon("chevR")) : null);
      main.onclick = () => { ctx.haptic("tick"); openEntry(e); };
      let pressTimer = null;
      main.addEventListener("touchstart", () => { pressTimer = setTimeout(() => { pressTimer = null; ctx.haptic("medium"); menu(e); }, 520); }, { passive: true });
      for (const ev of ["touchend", "touchmove", "touchcancel"]) main.addEventListener(ev, () => { if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; } }, { passive: true });
      main.addEventListener("contextmenu", (ev) => { ev.preventDefault(); menu(e); });
      const dots = h("button", { class: "iconbtn fb-dots", type: "button", "aria-label": "More for " + e.name, title: "More", onclick: () => { ctx.haptic("tick"); menu(e); } }, icon("more"));
      const el = h("div", { class: "fb-row" + (e.hidden ? " is-hidden" : "") }, main, e.type === "root" && !e.shareable ? null : dots);
      return el;
    }

    function appendRows(items) {
      const frag = document.createDocumentFragment();
      for (const e of items) frag.append(row(e));
      list.append(frag);
    }

    function watchEnd() {
      if (observer) observer.disconnect();
      if (typeof IntersectionObserver !== "function") return;
      observer = new IntersectionObserver((hits) => { if (hits.some((x) => x.isIntersecting)) loadMore(); }, { root: scroller, rootMargin: "300px" });
      observer.observe(more);
    }

    /* ---- what a tap does ---- */
    function openEntry(e) {
      if (e.kind === "folder") { load(e.path); return; }
      if (e.type === "video" || e.type === "audio") { play(e); return; }
      ctx.openFile(e.path, st.path);
    }

    async function play(e) {
      let link;
      try { link = await (await ctx.post("/browse/stream?path=" + encodeURIComponent(e.path))).json(); }
      catch (err) { if (err.code === 401) return; ctx.toast(err.message); ctx.haptic("error"); return; }
      closeOverlay();
      const media = h(e.type === "video" ? "video" : "audio", { class: "fb-media", controls: "", autoplay: "", playsinline: "", preload: "metadata", src: link.url });
      const err = h("p", { class: "fb-media-err", hidden: true, text: "This phone or browser can't play this file. You can download it instead." });
      media.addEventListener("error", () => { err.hidden = false; });
      overlay = h("div", { class: "fb-overlay", role: "dialog", "aria-label": "Playing " + e.name },
        h("div", { class: "fb-player" },
          h("header", { class: "cv-head" },
            h("button", { class: "iconbtn", type: "button", "aria-label": "Close player", onclick: closeOverlay }, icon("x")),
            h("div", { class: "cv-title" }, h("b", { text: e.name }), h("span", { class: "fb-sub", text: fmtSize(link.size) }))),
          media, err,
          h("div", { class: "actions fb-actions" }, h("button", { class: "btn", type: "button", onclick: () => ctx.download(e.path) }, icon("download"), "Download"))));
      host.append(overlay);
    }

    function closeOverlay() {
      if (!overlay) return;
      overlay.querySelectorAll("video,audio").forEach((m) => { try { m.pause(); m.removeAttribute("src"); m.load(); } catch (_) { /* already gone */ } });
      overlay.remove(); overlay = null;
    }

    function menu(e) {
      closeOverlay();
      const folder = e.kind === "folder";
      const item = (ic, label, run) => h("button", { class: "fb-item", type: "button", onclick: () => { ctx.haptic("tick"); closeOverlay(); run(); } }, icon(ic), h("span", { text: label }));
      const items = [item("open", folder ? "Open folder" : "Open", () => openEntry(e))];
      if (!folder) items.push(item("download", "Download", () => ctx.download(e.path)));
      if (folder) items.push(item("image", "Pictures in this folder", () => ctx.openGallery(e.path)));
      items.push(item("copy", "Copy path", () => ctx.copy(e.path)));
      items.push(item("info", "Details", () => details(e)));
      if (e.shareable && ctx.shareFolder) items.push(item("share", "Share", () => ctx.shareFolder(e.path, () => load(st.path))));
      overlay = h("div", { class: "fb-overlay fb-sheet-wrap", onclick: (ev) => { if (ev.target === overlay) closeOverlay(); } },
        h("div", { class: "fb-sheet", role: "menu", "aria-label": "Actions for " + e.name },
          h("div", { class: "fb-sheet-title" }, h("b", { text: e.name }), h("span", { text: e.path })), ...items));
      host.append(overlay);
    }

    function details(e) {
      const row2 = (k, v) => h("div", { class: "fb-detail" }, h("span", { text: k }), h("b", { text: v }));
      closeOverlay();
      overlay = h("div", { class: "fb-overlay fb-sheet-wrap", onclick: (ev) => { if (ev.target === overlay) closeOverlay(); } },
        h("div", { class: "fb-sheet", role: "dialog", "aria-label": "Details" },
          h("div", { class: "fb-sheet-title" }, h("b", { text: e.name })),
          row2("Where", e.path), e.shareable ? row2("Allowed by", (e.owners || []).join(", ")) : null, e.shareable ? row2("Shared with", (e.shared_with || []).join(", ") || "No one") : null, row2("Kind", e.kind === "folder" ? "Folder" : e.type), e.kind === "folder" ? null : row2("Size", fmtSize(e.size) + " (" + e.size.toLocaleString() + " bytes)"),
          row2("Changed", e.modified ? new Date(e.modified * 1000).toLocaleString() : "—"),
          h("div", { class: "actions", style: "margin-top:12px" }, h("button", { class: "btn", type: "button", onclick: closeOverlay }, "Close"))));
      host.append(overlay);
    }

    /* ---- live: the list follows the folder while you look at it ---- */
    function signature(entries) { return entries.map((e) => e.name + "|" + e.size + "|" + e.modified).join("\n"); }
    async function poll() {
      if (disposed || document.hidden || st.loading || overlay || !st.data || st.data.root) return;
      const token = st.token, path = st.path, count = Math.min(Math.max(st.entries.length, 200), 500);
      try {
        const data = await fetchPage(path, 0, count);
        if (disposed || token !== st.token || path !== st.path || overlay) return;
        if (data.total === st.data.total && signature(data.entries) === signature(st.entries.slice(0, count))) return;
        const top = scroller.scrollTop;
        st.data = data; st.entries = data.entries.slice();
        paint(false);
        scroller.scrollTop = top;
      } catch (e) { /* the connection monitor reports a lost PC; a missed refresh is harmless */ }
    }
    timer = setInterval(poll, 8000);

    if (o.data) { st.data = o.data; st.path = o.data.path; st.entries = o.data.entries.slice(); paint(true); }
    else load("");

    return {
      dispose() { disposed = true; clearInterval(timer); clearTimeout(searchTimer); if (observer) observer.disconnect(); closeOverlay(); },
      dirty() { return false; },
      back() { if (overlay) { closeOverlay(); return true; } return false; },
    };
  }

  window.AfpFiles = { mount, fmtSize };
})();
