/* offline.js: a copy of your recent chats kept on this phone, so you can read them when the PC can't be reached.
 *
 * The copy never leaves the phone. It is read-only: nothing here sends anything to the PC. The size is capped, the oldest chats go first,
 * and it is wiped when you sign out, when the PC removes this phone, or when you switch it off in Settings.
 *
 * Where it is kept (first that works):
 *   the Android app   its own private file (the app's built-in page can then show it even when the PC's page can't load)
 *   a browser         IndexedDB, then localStorage
 * The size and shortening rules are plain functions and are tested on their own: tests/js/offline_test.js.
 */
(function (root) {
  "use strict";

  var LIMITS = { maxChats: 50, maxBytes: 3000000, maxOutput: 20000 };

  function size(x) { try { return JSON.stringify(x).length; } catch (e) { return 0; } }

  function shorten(job, maxOutput) {
    var out = (job.outputs || []).map(function (t) {
      return typeof t === "string" && t.length > maxOutput ? t.slice(0, maxOutput) + "\n… (shortened in the saved copy)" : t;
    });
    return Object.assign({}, job, { outputs: out });
  }

  /* The newest chats that fit: whole conversations, newest first, until the count or the size runs out. */
  function trim(jobs, audit, limits) {
    var o = Object.assign({}, LIMITS, limits || {});
    var groups = {};
    (jobs || []).forEach(function (j) { var c = j.conversation || "default"; (groups[c] = groups[c] || []).push(shorten(j, o.maxOutput)); });
    var newest = function (list) { return Math.max.apply(null, list.map(function (j) { return Date.parse(j.created_at) || 0; })); };
    var order = Object.keys(groups).sort(function (a, b) { return newest(groups[b]) - newest(groups[a]); });
    var kept = [], bytes = 2, chats = 0, dropped = 0;
    for (var i = 0; i < order.length; i++) {
      var list = groups[order[i]].sort(function (a, b) { return (Date.parse(b.created_at) || 0) - (Date.parse(a.created_at) || 0); });
      var cost = size(list);
      if (chats < o.maxChats && bytes + cost <= o.maxBytes) { kept = kept.concat(list); bytes += cost; chats++; continue; }
      if (!chats) {  // one chat bigger than everything: keep its newest turns rather than nothing
        var part = [];
        for (var k = 0; k < list.length && bytes + size(list[k]) <= o.maxBytes; k++) { part.push(list[k]); bytes += size(list[k]); }
        if (part.length) { kept = kept.concat(part); chats++; dropped += list.length - part.length; continue; }
      }
      dropped += list.length;
    }
    var ids = {};
    kept.forEach(function (j) { ids[j.id] = true; });
    var events = (audit || []).filter(function (e) { return ids[e.job]; });
    while (events.length && bytes + size(events) > o.maxBytes) events = events.slice(Math.ceil(events.length / 10));  // the oldest events first
    return { jobs: kept, audit: events, chats: chats, dropped: dropped };
  }

  /* Changes whenever something worth saving changed (and not when only the clock moved). */
  function signature(jobs) {
    return (jobs || []).map(function (j) { return j.id + ":" + j.status + ":" + (j.outputs || []).length + ":" + (j.changed || []).length; }).join("|");
  }

  function snapshot(pc, info, jobs, audit, now, limits) {
    var t = trim(jobs, audit, limits);
    return { v: 1, savedAt: now, pc: pc || "", info: info ? { pc: info.pc, user: info.user, version: info.version } : null, jobs: t.jobs, audit: t.audit, chats: t.chats, dropped: t.dropped };
  }

  function valid(snap) {
    return !!snap && snap.v === 1 && Array.isArray(snap.jobs) && Array.isArray(snap.audit) && typeof snap.savedAt === "number";
  }

  function age(savedAt, now) {
    var s = Math.max(0, Math.round((now - savedAt) / 1000));
    if (s < 90) return "a moment ago";
    var m = Math.round(s / 60);
    if (m < 60) return m + " minutes ago";
    var h = Math.round(m / 60);
    if (h < 24) return h + (h === 1 ? " hour ago" : " hours ago");
    var d = Math.round(h / 24);
    return d + (d === 1 ? " day ago" : " days ago");
  }

  /* ------------------------------------------------------------------ where it is kept */

  function memoryBackend() {
    var held = null;
    return { name: "memory", get: function () { return Promise.resolve(held); }, set: function (v) { held = v; return Promise.resolve(); }, del: function () { held = null; return Promise.resolve(); } };
  }

  function nativeBackend(app) {
    return {
      name: "app",
      get: function () { try { var t = app.savedChats(); return Promise.resolve(t ? JSON.parse(t) : null); } catch (e) { return Promise.resolve(null); } },
      set: function (v) { try { app.saveChats(JSON.stringify(v)); } catch (e) { /* full or gone: the next save tries again */ } return Promise.resolve(); },
      del: function () { try { app.clearChats(); } catch (e) { /* nothing to clear */ } return Promise.resolve(); },
    };
  }

  function idbBackend(idb) {
    var NAME = "afp-offline", STORE = "kv", KEY = "chats";
    function open() {
      return new Promise(function (ok, bad) {
        var req = idb.open(NAME, 1);
        req.onupgradeneeded = function () { req.result.createObjectStore(STORE); };
        req.onsuccess = function () { ok(req.result); };
        req.onerror = function () { bad(req.error); };
      });
    }
    function run(mode, fn) {
      return open().then(function (db) {
        return new Promise(function (ok, bad) {
          var tx = db.transaction(STORE, mode), result = fn(tx.objectStore(STORE));
          tx.oncomplete = function () { db.close(); ok(result && result.result !== undefined ? result.result : undefined); };
          tx.onerror = tx.onabort = function () { db.close(); bad(tx.error); };
        });
      });
    }
    return {
      name: "idb",
      get: function () { return run("readonly", function (s) { return s.get(KEY); }).then(function (v) { return v || null; }); },
      set: function (v) { return run("readwrite", function (s) { return s.put(v, KEY); }); },
      del: function () { return run("readwrite", function (s) { return s.delete(KEY); }); },
    };
  }

  function lsBackend(ls) {
    var KEY = "afp_offline_chats";
    return {
      name: "localStorage",
      get: function () { try { var t = ls.getItem(KEY); return Promise.resolve(t ? JSON.parse(t) : null); } catch (e) { return Promise.resolve(null); } },
      set: function (v) { try { ls.setItem(KEY, JSON.stringify(v)); return Promise.resolve(); } catch (e) { return Promise.reject(e); } },
      del: function () { try { ls.removeItem(KEY); } catch (e) { /* none */ } return Promise.resolve(); },
    };
  }

  function pick(scope) {
    scope = scope || root;
    try { if (scope.AfpApp && typeof scope.AfpApp.saveChats === "function" && typeof scope.AfpApp.savedChats === "function") return nativeBackend(scope.AfpApp); } catch (e) { /* no bridge */ }
    try { if (scope.indexedDB) return idbBackend(scope.indexedDB); } catch (e) { /* private window */ }
    try { if (scope.localStorage) return lsBackend(scope.localStorage); } catch (e) { /* blocked */ }
    return memoryBackend();
  }

  /* One object the page talks to. `has()` can be asked at once once init() has finished, so the popup can decide about its button. */
  function create(options) {
    options = options || {};
    var backend = options.backend || pick(options.scope);
    var now = options.now || function () { return Date.now(); };
    var state = { snap: null, ready: false, lastSig: "", timer: null };

    function init() {
      return backend.get().then(function (s) { state.snap = valid(s) ? s : null; state.ready = true; return state.snap; },
        function () { state.ready = true; return null; });
    }
    function has() { return !!state.snap; }
    function current() { return state.snap; }
    function load() { return init(); }

    function save(pc, info, jobs, audit) {
      var snap = snapshot(pc, info, jobs, audit, now(), options.limits);
      return backend.set(snap).then(function () { state.snap = snap; return snap; }, function () { return null; });
    }

    /* Saves a moment after the last change, and only if something changed: a busy chat is not rewritten every second.
     * `source()` is asked when the save happens, so it holds what the page knows by then (the PC's name arrives a little after the chats). */
    function queue(source, delay) {
      var sig = signature(source().jobs);
      if (sig === state.lastSig) return;
      clearTimeout(state.timer);
      state.timer = setTimeout(function () { var s = source(); state.lastSig = signature(s.jobs); save(s.pc, s.info, s.jobs, s.audit); }, delay == null ? 4000 : delay);
    }

    function clear() {
      clearTimeout(state.timer);
      state.snap = null; state.lastSig = "";
      return backend.del().catch(function () {});
    }

    return { init: init, has: has, current: current, load: load, save: save, queue: queue, clear: clear, backend: backend.name };
  }

  var api = { LIMITS: LIMITS, trim: trim, signature: signature, snapshot: snapshot, valid: valid, age: age, create: create, pick: pick, memoryBackend: memoryBackend };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.AfpOffline = api;
})(typeof window !== "undefined" ? window : this);
