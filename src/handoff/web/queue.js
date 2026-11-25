/* queue.js: requests typed while the PC can't be reached are held here, on this phone, and sent when it answers again.
 *
 * Nothing in here talks to the PC or touches the page (no fetch, no document), so every rule is tested on its own: tests/js/queue_test.js.
 * Holding a request is NOT approving it: when the PC gets it, it makes a fresh plan from the files as they are then, and anything that
 * changes, deletes, sends or runs something asks for approval again, exactly like a request typed while connected.
 *
 * An item: { id, text, conv, file, created, expires, state, error }
 *   queued    waiting for the PC
 *   sending   being handed over right now (after a reload it is "queued" again; the PC ignores a repeat of the same id)
 *   failed    the PC refused it for good (the reason is in `error`)
 *   expired   waited longer than the time limit; kept on screen so it is never lost silently
 * An item leaves the queue once the PC has accepted it: from then on it is an ordinary request in the chat.
 */
(function (root) {
  "use strict";

  var TTL_MS = 24 * 3600 * 1000;
  var MAX_ITEMS = 50;
  var KEY = "afp_queue";

  /* Power and lock requests are never held: they must be confirmed while the PC can be reached, never run by themselves later. */
  var POWER_START = /^\s*(?:please\s+|can you\s+|could you\s+)?(?:unlock|lock|shut\s?down|restart|reboot|hibernate|sleep|power\s*off|turn\s*off|switch\s*off|log\s?off|sign\s?out)\b/i;
  var POWER_ANY = /\b(?:unlock|shut\s?down|restart|reboot|hibernate|power\s*off)\b.{0,24}\b(?:pc|computer|laptop|machine|windows|system|it)\b/i;
  function neverQueue(text) {
    var t = String(text || "");
    return POWER_START.test(t) || POWER_ANY.test(t);
  }

  function newId(now) { return "q" + now.toString(36) + Math.random().toString(36).slice(2, 8); }

  function memoryBackend() {
    var held = null;
    return { get: function () { return held; }, set: function (v) { held = v; } };
  }
  function lsBackend(ls) {
    return {
      get: function () { try { var t = ls.getItem(KEY); return t ? JSON.parse(t) : null; } catch (e) { return null; } },
      set: function (v) { try { ls.setItem(KEY, JSON.stringify(v)); } catch (e) { /* full or blocked: it stays in memory for this visit */ } },
    };
  }
  function pick(scope) {
    scope = scope || root;
    try { if (scope.localStorage) return lsBackend(scope.localStorage); } catch (e) { /* blocked */ }
    return memoryBackend();
  }

  function create(options) {
    options = options || {};
    var backend = options.backend || pick(options.scope);
    var now = options.now || function () { return Date.now(); };
    var ttl = options.ttlMs == null ? TTL_MS : options.ttlMs;
    var listeners = [];
    var list = [];

    function load() {
      var saved = backend.get();
      list = Array.isArray(saved) ? saved.filter(function (i) { return i && typeof i.id === "string" && typeof i.text === "string"; }) : [];
      list.forEach(function (i) { if (i.state === "sending") i.state = "queued"; });
      expire();
    }
    function save() { backend.set(list); }
    function changed() { save(); listeners.forEach(function (f) { try { f(); } catch (e) { /* a listener must not break the queue */ } }); }

    /* Anything past its time limit stops waiting. It stays visible (with Try again) rather than vanishing. */
    function expire() {
      var t = now(), any = false;
      list.forEach(function (i) { if ((i.state === "queued") && t >= i.expires) { i.state = "expired"; any = true; } });
      return any;
    }

    function items() { expire(); return list.slice().sort(function (a, b) { return a.created - b.created; }); }
    function get(id) { return list.find(function (i) { return i.id === id; }) || null; }
    function active() { return items().filter(function (i) { return i.state === "queued" || i.state === "sending"; }); }

    function add(req) {
      var text = String(req.text || "").trim();
      if (!text) return null;
      if (active().length >= MAX_ITEMS) return null;
      var t = now();
      var item = { id: newId(t), text: text, conv: req.conv || "default", file: req.file || null, created: t, expires: t + ttl, state: "queued", error: "" };
      list.push(item); changed();
      return item;
    }
    function edit(id, text) {
      var i = get(id); text = String(text || "").trim();
      if (!i || !text || (i.state !== "queued" && i.state !== "expired" && i.state !== "failed")) return false;
      i.text = text; i.state = "queued"; i.error = ""; i.expires = now() + ttl; changed();
      return true;
    }
    function cancel(id) {
      var i = get(id);
      if (!i || i.state === "sending") return false;  // it is already on its way: stopping it is the chat's Stop button
      list = list.filter(function (x) { return x.id !== id; }); changed();
      return true;
    }
    function remove(id) { var n = list.length; list = list.filter(function (x) { return x.id !== id; }); if (list.length !== n) changed(); }
    function retry(id) {
      var i = get(id);
      if (!i || (i.state !== "expired" && i.state !== "failed")) return false;
      i.state = "queued"; i.error = ""; i.expires = now() + ttl; changed();
      return true;
    }
    function mark(id, state, error) { var i = get(id); if (!i) return; i.state = state; i.error = error || ""; changed(); }

    /* The next one to hand over: the oldest that is waiting, and only when nothing is being handed over (one at a time, in order). */
    function next() {
      expire();
      var sorted = items();
      if (sorted.some(function (i) { return i.state === "sending"; })) return null;
      return sorted.find(function (i) { return i.state === "queued"; }) || null;
    }
    function clear() { list = []; changed(); }
    function onChange(fn) { listeners.push(fn); }

    load();
    return { items: items, get: get, active: active, add: add, edit: edit, cancel: cancel, remove: remove, retry: retry, mark: mark, next: next, clear: clear, expire: expire, onChange: onChange, reload: load };
  }

  var api = { TTL_MS: TTL_MS, MAX_ITEMS: MAX_ITEMS, neverQueue: neverQueue, create: create, memoryBackend: memoryBackend };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.AfpQueue = api;
})(typeof window !== "undefined" ? window : this);
