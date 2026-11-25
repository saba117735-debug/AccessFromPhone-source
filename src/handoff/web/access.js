/* access.js: the "PC access" screen, the slim banner, and the takeover question. One person uses the PC at a time.
 *
 * The PC decides everything (who may do what, whose turn it is); this file only shows it and sends the person's answers. It is written as a
 * component: `AfpAccess.create(deps)` gets the few page helpers it needs (h, ico, api, ask, toast, haptic, openSheet, ...), keeps its own state,
 * and gives back { update, open, level, isViewer, state }. The wording rules are plain functions (`headline`, `span`, `noticeText`) tested on their
 * own: tests/js/access_test.js.
 *
 * The page calls update(view) with the "session" part of its regular 2-second status check, so a question for the person using the PC
 * appears within a couple of seconds, and the banner stays current, with no extra polling.
 */
(function (root) {
  "use strict";

  var LEVEL_HELP = {
    viewer: "You can look and read, but not change anything.",
    editor: "You can read and change files. Every change is saved first, so Undo works.",
    operator: "You can also run approved commands and open apps.",
    admin: "You manage who can do what on this PC.",
  };

  /* ---------------------------------------------------------------- wording (pure)  */

  function span(seconds) {
    var s = Math.max(0, Math.round(seconds || 0));
    if (s < 45) return "a moment";
    var m = Math.round(s / 60);
    if (m < 60) return m + (m === 1 ? " minute" : " minutes");
    var h = Math.floor(m / 60), r = m % 60;
    return h + (h === 1 ? " hour" : " hours") + (r ? " " + r + " min" : "");
  }

  /* The big title and line under it for each state of the session. */
  function headline(view) {
    if (!view) return { kind: "wait", title: "PC access", sub: "Checking…" };
    var h = view.holder;
    if (view.state === "mine") {
      var idle = h && h.idle >= 45 ? " Idle for " + span(h.idle) + "." : "";
      return { kind: view.ask ? "ask" : "mine", title: "You're using this PC", sub: "For " + span(h && h.since) + "." + idle + " Others can ask you for a turn." };
    }
    if (view.state === "other") {
      if (view.mine_ask) return { kind: "waiting", title: "Waiting for " + view.mine_ask.holder, sub: "They've been asked. If they don't answer in " + view.mine_ask.expires_in + " s, they keep going." };
      return { kind: "other", title: (h ? h.name : "Someone") + " is using this PC", sub: "For " + span(h && h.since) + ". You can look around, or ask for a turn." };
    }
    return { kind: "free", title: "This PC is free", sub: "Nobody is using it. Start your turn when you want to make changes. Looking and reading never needs one." };
  }

  /* What to say when something happened to this phone's turn or request ({kind, by} from the PC), or null. */
  function noticeText(n) {
    if (!n) return null;
    if (n.kind === "granted") return "It's your turn. You can make changes now.";
    if (n.kind === "kept") return (n.by || "They") + " is still using this PC. Try again in a couple of minutes.";
    if (n.kind === "taken") return (n.by || "The Admin") + " took over, so your turn ended.";
    return null;
  }

  /* ---------------------------------------------------------------- the component  */

  function create(d) {
    var h = d.h, ico = d.ico;
    var st = { view: null, sheet: null, paint: null, prompt: null, notice: "", note: "", busy: false, sig: "", people: null, peopleFresh: false, barSig: "" };

    function level() { return st.view && st.view.me ? st.view.me.level : null; }
    function isViewer() { return level() === "viewer"; }
    function isAdmin() { return !!(st.view && st.view.me && st.view.me.role === "admin"); }

    /* ---- one action, with a calm failure ---- */
    function call(path, body, quiet) {
      return d.api("/session" + path, { method: "POST", body: JSON.stringify(body || {}) }).then(function (r) { return r.json(); }).then(function (view) {
        st.view = Object.assign({}, st.view, view); changed(); return view;
      }).catch(function (e) {
        if (e && e.code === 401) throw e;
        if (!quiet) { d.toast((e && e.message) || "That didn't work."); d.haptic("error"); }
        refresh();
        return null;
      });
    }
    function refresh() {
      return d.api("/session").then(function (r) { return r.json(); }).then(function (view) { st.view = view; changed(); }).catch(function () { /* the next check will do */ });
    }

    /* ---- the page tells us what the PC says ---- */
    function update(view) {
      st.view = view || null;
      changed();
      var text = noticeText(st.view && st.view.notice), key = st.view && st.view.notice ? st.view.notice.kind + "|" + (st.view.notice.by || "") : "";
      if (key && key !== st.notice) { d.toast(text); d.haptic(st.view.notice.kind === "granted" ? "success" : "warning"); }
      st.notice = key;
      var ask = st.view && st.view.ask;
      if (ask && !st.prompt) showPrompt(ask); else if (!ask && st.prompt) hidePrompt();
      else if (ask && st.prompt) st.prompt.tick(ask);
    }

    function changed() {
      paintBar(); paintTab();
      if (st.paint) st.paint();
    }

    /* ---- the slim banner under the header: who is using the PC ---- */
    function paintBar() {
      var bar = d.bar && d.bar();
      if (!bar) return;
      var v = st.view;
      if (!v || v.state === "free" || v.state === "mine") { if (!bar.hidden) { bar.hidden = true; bar.replaceChildren(); } st.barSig = ""; return; }
      var hl = headline(v);
      var barSig = hl.kind + "|" + hl.title + "|" + (v.mine_ask ? "d" : "a");
      if (barSig === st.barSig && !bar.hidden) return;   // the same words as a moment ago: leave it alone (a redraw every 2 seconds is a flicker)
      st.barSig = barSig;
      bar.hidden = false;
      bar.dataset.kind = hl.kind;
      bar.replaceChildren(h("span", { class: "sb-ico", "aria-hidden": "true" }, ico("monitor")), h("span", { class: "sb-text" }, h("b", { text: hl.title })),
        h("button", { class: "sb-btn", type: "button", onclick: function () { d.haptic("tick"); open(); } }, v.mine_ask ? "Details" : "Ask"));
    }

    /* ---- a dot on the bottom-bar icon: someone else is using the PC, or someone is asking you ---- */
    function paintTab() {
      var tab = d.tab && d.tab();
      if (!tab) return;
      var v = st.view, state = !v ? "" : v.ask ? "ask" : v.state === "other" ? "other" : v.state === "mine" ? "mine" : "";
      tab.dataset.session = state;
      tab.setAttribute("aria-label", "PC access" + (state === "ask" ? ", someone is asking for a turn" : state === "other" ? ", someone else is using it" : state === "mine" ? ", you are using it" : ""));
    }

    /* ---- the question for the person using the PC ---- */
    function showPrompt(ask) {
      var left = h("span", { class: "acc-count", text: ask.expires_in + " s" });
      var who = h("b", { text: ask.name });
      var note = h("p", { class: "acc-note-q", hidden: !ask.message, text: ask.message ? "“" + ask.message + "”" : "" });
      var end = h("button", { class: "btn primary", type: "button" }, ico("check"), "Let them have it");
      var keep = h("button", { class: "btn ghost", type: "button" }, "I'm still using it");
      var box = h("div", { class: "dlg acc-ask", role: "alertdialog", "aria-modal": "true", "aria-label": "Someone wants a turn on this PC" },
        h("div", { class: "dlg-ico" }, ico("monitor", 26)), h("h2", {}, who, " wants a turn"),
        h("p", {}, "You're using this PC. Handing it over ends your turn. Anything waiting for your approval is cancelled."), note,
        h("p", { class: "acc-timer" }, "No answer in ", left, " and you keep going."),
        h("div", { class: "dlg-btns" }, keep, end));
      var wrap = h("div", { class: "dlg-wrap", role: "presentation" }, box);
      var close = function () { wrap.classList.add("out"); wrap.inert = true; setTimeout(function () { wrap.remove(); }, 160); st.prompt = null; };
      end.onclick = function () { d.haptic("medium"); close(); call("/answer", { decision: "end" }); };
      keep.onclick = function () { d.haptic("light"); close(); call("/answer", { decision: "continue" }); };
      (d.layer ? d.layer() : document.body).append(wrap);
      d.haptic("warning");
      setTimeout(function () { keep.focus(); }, 60);  // the safe answer is the one under the thumb
      st.prompt = { close: close, tick: function (a) { left.textContent = a.expires_in + " s"; } };
    }
    function hidePrompt() { if (st.prompt) st.prompt.close(); }

    /* ---- the screen ---- */
    function open() {
      if (st.sheet && st.sheet.body && st.sheet.body.isConnected) return;
      var body = h("div", { class: "access-page" });
      st.sig = ""; st.peopleFresh = false;
      st.paint = function () { if (!body.isConnected) { st.paint = null; return; } render(body); };
      render(body);
      st.sheet = d.openSheet({ title: "PC access", body: body, full: true, route: "access", onClose: function () { st.paint = null; st.sheet = null; } });
      refresh();
    }

    /* The screen is redrawn only when something it SHOWS changes (who has the turn, a question, a level). The words that just count up ("for 3 minutes",
       a countdown) are updated in place. Redrawing on every 2-second check made the whole card blink and threw away what was being typed. */
    function render(body) {
      var v = st.view, hl = headline(v), me = v && v.me;
      var sig = JSON.stringify([v && v.state, v && v.holder && v.holder.name, v && v.holder && v.holder.level, v && v.ask && v.ask.name, v && v.mine_ask && v.mine_ask.holder, me && me.role, me && me.level]);
      if (sig === st.sig && body.firstChild) {
        var t = body.querySelector(".acc-title"), sub = body.querySelector(".acc-sub");
        if (t && t.textContent !== hl.title) t.textContent = hl.title;
        if (sub && sub.textContent !== hl.sub) sub.textContent = hl.sub;
        return;
      }
      st.sig = sig;
      build(body);
    }

    function build(body) {
      var v = st.view, hl = headline(v), me = v && v.me;
      var keepScroll = body.parentElement ? body.parentElement.scrollTop : 0;
      var parts = [];

      var chip = me ? h("span", { class: "acc-chip", "data-level": me.level, text: me.role === "admin" ? "Admin" : me.level_name }) : null;
      var hero = h("section", { class: "acc-hero", "data-kind": hl.kind },
        h("div", { class: "acc-orb", "aria-hidden": "true" }, ico("monitor", 34)),
        h("h2", { class: "acc-title", text: hl.title }), h("p", { class: "acc-sub", text: hl.sub }),
        chip ? h("div", { class: "acc-chips" }, chip, h("span", { class: "acc-help", text: LEVEL_HELP[me.level] || "" })) : null);

      var actions = h("div", { class: "acc-actions" });
      if (v && me && me.level === "viewer") {  // a view-only phone never takes a turn: there is nothing to start or ask for
        actions.append(h("p", { class: "acc-viewer" }, ico("eye"), "You have view-only access, so you don't need a turn: you can look and read at any time, and you never block anyone else."));
      } else if (v) {
        var busy = function (btn, fn) { return function () { if (st.busy) return; st.busy = true; btn.disabled = true; d.haptic("medium"); Promise.resolve(fn()).finally(function () { st.busy = false; btn.disabled = false; }); }; };
        if (v.state === "free") {
          var start = h("button", { class: "btn primary block", type: "button" }, ico("play"), "Start my turn");
          start.onclick = busy(start, function () { return call("/start", {}); });
          actions.append(start);
        } else if (v.state === "mine") {
          var stop = h("button", { class: "btn block", type: "button" }, "End my turn");
          stop.onclick = busy(stop, function () { return call("/end", {}); });
          actions.append(stop);
        } else if (v.mine_ask) {
          var cancel = h("button", { class: "btn ghost block", type: "button" }, "Cancel my request");
          cancel.onclick = busy(cancel, function () { return call("/cancel", {}); });
          actions.append(cancel);
        } else {
          var input = h("input", { class: "text-input acc-msg", type: "text", maxlength: "120", placeholder: "Add a note (optional): what do you need?", "aria-label": "Note for " + (v.holder ? v.holder.name : "them"), value: st.note });
          input.addEventListener("input", function () { st.note = input.value; });
          var ask = h("button", { class: "btn primary block", type: "button" }, ico("bell"), "Ask for a turn");
          ask.onclick = busy(ask, function () { var note = st.note; st.note = ""; return call("/takeover", { message: note }); });
          actions.append(input, ask);
          if (isAdmin()) {
            var take = h("button", { class: "btn danger block", type: "button" }, "Take over now");
            take.onclick = busy(take, function () {
              return d.ask({ title: "Take over now?", message: (v.holder ? v.holder.name : "They") + " is signed out of their turn straight away, and anything they had waiting is cancelled.", ok: "Take over", cancel: "Not now", tone: "danger", icon: "alert" })
                .then(function (yes) { return yes ? call("/take", {}) : null; });
            });
            var release = h("button", { class: "btn ghost block", type: "button" }, "End their turn");
            release.onclick = busy(release, function () { return call("/release", {}); });
            actions.append(take, release);
          }
        }
      }
      parts.push(hero, actions);

      if (v && v.state !== "free" && v.holder && v.state === "other") {
        parts.push(h("div", { class: "acc-who" }, h("span", { class: "acc-dot", "aria-hidden": "true" }), h("div", { class: "grow" }, h("b", { text: v.holder.name }), h("small", { text: "Level: " + (v.holder.level === "admin" ? "Admin" : (d.names && d.names[v.holder.level]) || v.holder.level) }))));
      }

      if (d.openScreen) {  // looking at the PC's screen is its own thing (screen.js): it never needs, takes or blocks a turn
        parts.push(h("button", { class: "acc-screen", type: "button", onclick: function () { d.haptic("tick"); d.openScreen(); } },
          h("span", { class: "acc-av" }, ico("eye")), h("div", { class: "grow" }, h("b", { text: "View the PC's screen" }), h("small", { text: "A live picture, view only. The PC owner says yes on the PC." })), ico("chevron")));
      }
      parts.push(h("p", { class: "acc-foot", text: "One person uses this PC at a time, like one person at a keyboard. Anyone can look and read. Only the person whose turn it is can make changes, and the PC checks that on every request." }));
      if (isAdmin()) parts.push(peopleBox(), requestsBox());

      body.replaceChildren.apply(body, parts);
      if (body.parentElement) body.parentElement.scrollTop = keepScroll;
    }

    /* ---- the Admin phone manages everyone else's level (never its own, never "admin") ---- */
    function peopleBox() {
      var box = h("section", { class: "acc-people" }, d.sect("People"), h("p", { class: "set-sub", style: "padding:4px 2px 8px", text: "Loading…" }));
      var paint = function (data) {
        var names = {}; data.levels.forEach(function (l) { names[l.id] = l.name; });
        d.names = Object.assign({}, d.names, names);
        var rows = data.people.filter(function (p) { return !p.blocked; }).map(function (p) {
          if (p.role === "admin") return h("div", { class: "acc-person" }, h("span", { class: "acc-av" }, ico("phone")), h("div", { class: "grow" }, h("b", { text: p.name + (p.current ? " (this phone)" : "") }), h("small", { text: "Admin · manages access" })), h("span", { class: "acc-chip", "data-level": "admin", text: "Admin" }));
          var seg = h("div", { class: "acc-seg", role: "radiogroup", "aria-label": "Access level for " + p.name });
          data.levels.forEach(function (l) {
            var b = h("button", { class: "acc-seg-b" + (p.level === l.id ? " on" : ""), type: "button", role: "radio", "aria-checked": String(p.level === l.id), text: l.id === "viewer" ? "View" : l.id === "editor" ? "Edit" : "Operate", title: l.name });
            b.onclick = function () {
              if (p.level === l.id) return;
              d.haptic("tick");
              d.api("/access/level", { method: "POST", body: JSON.stringify({ device_id: p.id, level: l.id }) }).then(function () {
                p.level = l.id; seg.querySelectorAll(".acc-seg-b").forEach(function (x) { var on = x === b; x.classList.toggle("on", on); x.setAttribute("aria-checked", String(on)); });
                d.toast(p.name + ": " + l.name);
              }).catch(function (e) { d.toast((e && e.message) || "Couldn't change that."); d.haptic("error"); });
            };
            seg.append(b);
          });
          return h("div", { class: "acc-person col" }, h("div", { class: "acc-person-top" }, h("span", { class: "acc-av" }, ico("phone")), h("div", { class: "grow" }, h("b", { text: p.name }), h("small", { text: names[p.level] || p.level }))), seg);
        });
        box.replaceChildren(d.sect("People"), h("div", { class: "card acc-card" }, rows.length ? rows : [h("p", { class: "set-sub", style: "padding:12px 14px", text: "No other phones are paired yet." })]),
          h("p", { class: "set-sub", style: "padding:8px 2px", text: "View: look and read. Edit: also change files. Operate: also run approved commands and open apps. Unlocking and restarting are for the Admin only. The Admin phone itself can only be changed on the PC." }));
      };
      if (st.people) paint(st.people);   // what was fetched a moment ago, at once: no "Loading…" flash when the card is redrawn
      if (!st.peopleFresh) {
        st.peopleFresh = true;   // fetched once per visit (and the Admin's own changes update the copy), not on every redraw
        d.api("/access/people").then(function (r) { return r.json(); }).then(function (data) { st.people = data; paint(data); })
          .catch(function () { if (!st.people) box.replaceChildren(h("p", { class: "set-sub", style: "padding:8px 2px", text: "Can't load the people list right now." })); });
      }
      return box;
    }

    /* ---- the Admin phone shares a folder with chosen phones: opened from the long-press menu of the file browser (files.js) ---- */
    function folderName(path) { var parts = String(path).split(/[\\/]+/).filter(Boolean); return parts.length ? parts[parts.length - 1] : path; }
    function phonesText(names) { return names.length === 1 ? names[0] : names.slice(0, -1).join(", ") + " and " + names[names.length - 1]; }

    function shareFolder(path, done) {
      if (!isAdmin()) return;
      var body = h("div", { class: "acc-share" }), sheet = d.openSheet({ title: "Share folder", body: body, route: "share" }), alive = true;
      var shut = function () { alive = false; sheet.close(); };
      var message = function (e, fallback) {
        if (e && e.code === 401) return "This phone isn't signed in any more. Open the app again.";
        if (e && e.data && e.data.code === "admin_only") return "Only the Admin phone can share folders.";
        var m = String((e && e.message) || "");
        if (!m || /failed to fetch|networkerror|load failed/i.test(m)) return fallback;
        return /[.!?]$/.test(m) ? m : m + ".";
      };

      var failView = function (text) {
        var retry = h("button", { class: "btn primary", type: "button" }, ico("retry"), "Try again"), close = h("button", { class: "btn ghost", type: "button", text: "Close" });
        retry.onclick = function () { d.haptic("tick"); load(); }; close.onclick = shut;
        body.replaceChildren(h("div", { class: "acc-share-state bad", role: "alert" }, h("span", { class: "acc-share-badge" }, ico("alert")), h("b", { text: "Couldn't open sharing" }), h("p", { text: text })), h("div", { class: "actions" }, retry, close));
      };
      var load = function () {
        body.replaceChildren(h("div", { class: "acc-share-state", "aria-busy": "true" }, h("span", { class: "spin" }), h("p", { text: "Getting the phones…" })));
        d.api("/folders").then(function (r) { return r.json(); }).then(function (data) {
          if (!alive) return;
          var real = data.folders.filter(function (f) { return f.path === path; });
          if (!real.length) { failView("This folder isn't on the list any more. Someone may have taken it back."); return; }
          compose(real, data);
        }).catch(function (e) { if (alive) { d.haptic("error"); failView(message(e, "Couldn't reach the PC. Check the connection and try again.")); } });
      };

      var compose = function (entries, data) {
        var owners = entries.map(function (f) { return f.owner; }), ownerNames = entries.map(function (f) { return f.owner_name; });
        var canEdit = entries.some(function (f) { return f.change; });
        var others = data.phones.filter(function (p) { return owners.indexOf(p.id) < 0 && !p.admin; });   // the Admin phone sees every folder already
        var chosen = {}, names = {}, first = {};
        others.forEach(function (p) { names[p.id] = p.name; });
        entries.forEach(function (f) { f.shared.forEach(function (s) { if (names[s.id] !== undefined) chosen[s.id] = !!chosen[s.id] || !!s.change; }); });
        Object.keys(chosen).forEach(function (id) { first[id] = chosen[id]; });

        var save = h("button", { class: "btn primary acc-share-go", type: "button" });
        var trash = h("button", { class: "acc-share-trash", type: "button", "aria-label": "Remove this folder for everyone", title: "Remove this folder for everyone" }, ico("trash"));
        var tally = h("span", { class: "acc-share-tally", "aria-live": "polite" });
        var problem = h("div", { class: "acc-share-error", role: "alert", hidden: true });
        var changed = function () { var a = Object.keys(chosen), b = Object.keys(first); return a.length !== b.length || a.some(function (id) { return !(id in first) || first[id] !== chosen[id]; }); };
        var paintFoot = function () {
          var n = Object.keys(chosen).length, was = Object.keys(first).length;
          tally.textContent = n ? n + (n === 1 ? " phone" : " phones") : "No one yet";
          tally.classList.toggle("on", n > 0);
          var label, dead = false;
          if (changed()) label = n ? "Share with " + n + (n === 1 ? " phone" : " phones") : "Stop sharing";
          else if (was) label = "Close";
          else { label = "Choose a phone to share with"; dead = true; }
          save.replaceChildren(ico(changed() && !n ? "lock" : changed() ? "share" : "check"), document.createTextNode(label));
          save.disabled = dead;
        };

        var rows = others.map(function (p) {
          var status = h("small", { class: "acc-share-status" });
          var top = h("button", { class: "acc-share-top", type: "button", role: "switch", "aria-label": "Share with " + p.name },
            h("span", { class: "acc-share-av", "aria-hidden": "true", text: (p.name.trim().charAt(0) || "?").toUpperCase() }),
            h("span", { class: "grow" }, h("b", { text: p.name }), status),
            h("span", { class: "acc-switch", "aria-hidden": "true" }, h("i")));
          var mk = function (id, icoName, label, hint, disabled) {
            var b = h("button", { class: "acc-seg-b acc-perm", type: "button", role: "radio" }, ico(icoName), h("span", {}, h("b", { text: label }), h("small", { text: hint })));
            b.disabled = !!disabled; b.dataset.id = id; return b;
          };
          var read = mk("read", "eye", "Read only", "Can look and open"), edit = mk("edit", "pencil", "Can edit", canEdit ? "Can also change files" : "Owner allowed reading only", !canEdit);
          var seg = h("div", { class: "acc-seg acc-perms", role: "radiogroup", "aria-label": "What " + p.name + " may do" }, read, edit);
          var row = h("div", { class: "acc-share-row" }, top, h("div", { class: "acc-share-perm" }, h("div", {}, seg)));
          var sync = function () {
            var has = p.id in chosen;
            top.setAttribute("aria-checked", String(has)); row.classList.toggle("on", has);
            status.textContent = !has ? "Not shared" : chosen[p.id] ? "Can open and change files" : "Can open, read only";
            [read, edit].forEach(function (b) { var active = b.dataset.id === (chosen[p.id] ? "edit" : "read"); b.classList.toggle("on", active); b.setAttribute("aria-checked", String(active)); b.tabIndex = has ? 0 : -1; });
            problem.hidden = true; paintFoot();
          };
          top.onclick = function () { d.haptic("tick"); if (p.id in chosen) delete chosen[p.id]; else chosen[p.id] = false; sync(); };
          read.onclick = function () { d.haptic("tick"); chosen[p.id] = false; sync(); };
          edit.onclick = function () { d.haptic("tick"); chosen[p.id] = true; sync(); };
          sync();
          return row;
        });

        trash.onclick = function () {
          d.haptic("tick");
          d.ask({ title: "Remove this folder?", message: phonesText(ownerNames) + (Object.keys(first).length ? " and the phones it is shared with" : "") + " lose access to " + folderName(path) + ". They can ask for it again.", ok: "Remove", cancel: "Keep it", tone: "danger", icon: "trash" }).then(function (yes) {
            if (!yes || !alive) return;
            trash.disabled = true;
            d.api("/folders/revoke", { method: "POST", body: JSON.stringify({ path: path }) }).then(function () {
              d.haptic("success"); if (done) done(); shut(); d.toast("Folder removed");
            }).catch(function (e) { trash.disabled = false; d.haptic("error"); d.toast(message(e, "Couldn't remove it. Try again.")); });
          });
        };

        var hero = h("div", { class: "acc-share-hero" }, h("span", { class: "acc-share-folder", "aria-hidden": "true" }, ico("folder")),
          h("div", { class: "grow" }, h("b", { text: folderName(path) }), h("small", { class: "acc-path", text: path, title: path }),
            h("span", { class: "acc-share-owner" }, ico("phone"), "Allowed by " + phonesText(ownerNames))));
        var label = h("div", { class: "acc-share-label" }, h("span", { text: "Who can open it" }), tally);
        var bar = h("div", { class: "acc-share-bar" }, trash, save);
        body.replaceChildren(hero,
          others.length ? h("div", {}, label, h("div", { class: "acc-share-list" }, rows)) : h("div", { class: "acc-share-state" }, h("span", { class: "acc-share-badge" }, ico("users")), h("b", { text: "No other phones yet" }), h("p", { text: "When more phones are paired, you can share this folder with them here." })),
          problem, bar);
        paintFoot();
        if (!others.length) { save.hidden = true; }
        save.onclick = function () {
          if (!changed()) { shut(); return; }
          var targets = Object.keys(chosen).map(function (id) { return { id: id, change: !!chosen[id] }; });
          save.disabled = true; trash.disabled = true; save.replaceChildren(h("span", { class: "spin" }), document.createTextNode("Sharing…")); d.haptic("medium");
          d.api("/folders/share", { method: "POST", body: JSON.stringify({ path: path, targets: targets }) }).then(function (r) { return r.json(); }).then(function () {
            if (!alive) return;
            d.haptic("success"); if (done) done();
            var who = targets.map(function (t) { return names[t.id] || "a phone"; });
            var edits = targets.filter(function (t) { return t.change; }).length;
            var title = targets.length ? "Shared with " + phonesText(who) : "Sharing stopped";
            var detail = targets.length ? (targets.length === 1 ? "They" : "They each") + " can open " + folderName(path) + " now" + (edits ? (edits === targets.length ? ", and change files in it." : ". " + edits + " can also change files in it.") : ". Read only.") : "Only " + phonesText(ownerNames) + (ownerNames.length > 1 ? " have" : " has") + " " + folderName(path) + " now.";
            var ok = h("button", { class: "btn primary acc-share-go", type: "button" }, ico("check"), "Done"); ok.onclick = function () { shut(); d.toast(title); };
            body.replaceChildren(h("div", { class: "acc-share-state ok", role: "status" }, h("span", { class: "acc-share-badge" }, ico("check")), h("b", { text: title }), h("p", { text: detail })), h("div", { class: "acc-share-bar" }, ok));
            ok.focus({ preventScroll: true });
          }).catch(function (e) {
            if (!alive) return;
            d.haptic("error"); trash.disabled = false; paintFoot();
            var why = message(e, "Couldn't reach the PC. Check the connection and try again.");
            problem.hidden = false; problem.replaceChildren(ico("alert"), h("span", {}, h("b", { text: "Not shared. " }), document.createTextNode(why + " Your choices are kept.")));
            save.replaceChildren(ico("retry"), document.createTextNode("Try again")); save.disabled = false;
          });
        };
      };
      load();
    }

    /* ---- who asked what, and when (read-only: the conversations stay private to each phone) ---- */
    var STATUS = { done: ["Done", "ok"], failed: ["Failed", "bad"], rejected: ["Declined", ""], stopped: ["Stopped", ""], undone: ["Undone", ""], expired: ["Expired", ""],
                   awaiting_approval: ["Waiting", "warn"], needs_access: ["Waiting", "warn"], planning: ["Working", "warn"], running: ["Working", "warn"] };
    function requestsBox() {
      var box = h("section", { class: "acc-requests" }, d.sect("Everyone's requests"), h("p", { class: "set-sub", style: "padding:4px 2px 8px", text: "Loading…" }));
      var paint = function (data) {
        st.requests = data;
        var all = data.requests, phones = [], seen = {};
        all.forEach(function (r) { if (!seen[r.phone]) { seen[r.phone] = true; phones.push(r.phone); } });
        if (st.reqFilter && !seen[st.reqFilter]) st.reqFilter = "";
        var shown = all.filter(function (r) { return !st.reqFilter || r.phone === st.reqFilter; });
        var limit = st.reqMore ? 60 : 8;
        var chips = h("div", { class: "acc-chips", role: "group", "aria-label": "Show requests from" }, [""].concat(phones).map(function (name) {
          var b = h("button", { class: "acc-chipb" + ((st.reqFilter || "") === name ? " on" : ""), type: "button", text: name || "Everyone", "aria-pressed": String((st.reqFilter || "") === name) });
          b.onclick = function () { d.haptic("tick"); st.reqFilter = name; st.reqMore = false; paint(st.requests); };
          return b;
        }));
        var rows = shown.slice(0, limit).map(function (r) {
          var s = STATUS[r.status] || [r.status, ""];
          return h("div", { class: "acc-req" }, h("div", { class: "acc-req-top" }, h("span", { class: "acc-av sm" }, ico("phone")), h("b", { text: r.phone }), h("small", { text: d.ago ? d.ago(r.created_at) : "" }), h("span", { class: "acc-pill " + s[1], text: s[0] })),
            h("p", { class: "acc-req-text", text: r.prompt || "(no text)" }));
        });
        var more = shown.length > limit ? h("button", { class: "btn sm ghost acc-more", type: "button", text: "Show more" }) : null;
        if (more) more.onclick = function () { d.haptic("tick"); st.reqMore = true; paint(st.requests); };
        box.replaceChildren.apply(box, [d.sect("Everyone's requests"), phones.length > 1 ? chips : null, h("div", { class: "card acc-card" }, rows.length ? rows : [h("p", { class: "set-sub", style: "padding:14px", text: "No requests yet." })]), more,
          h("p", { class: "set-sub", style: "padding:8px 2px", text: "You can see who asked what and when. What was said inside each chat stays private to that phone." })].filter(Boolean));
      };
      if (st.requests) paint(st.requests);
      if (Date.now() - (st.requestsAt || 0) > 4000) {
        st.requestsAt = Date.now();
        d.api("/overview").then(function (r) { return r.json(); }).then(paint)
          .catch(function () { if (!st.requests) box.replaceChildren(h("p", { class: "set-sub", style: "padding:8px 2px", text: "Can't load the requests right now." })); });
      }
      return box;
    }

    return { update: update, marks: changed, open: open, refresh: refresh, shareFolder: shareFolder, level: level, isViewer: isViewer, isAdmin: isAdmin, state: function () { return st.view; } };
  }

  var api = { create: create, headline: headline, span: span, noticeText: noticeText, LEVEL_HELP: LEVEL_HELP };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.AfpAccess = api;
})(typeof window !== "undefined" ? window : this);
