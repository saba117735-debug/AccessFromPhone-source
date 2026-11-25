/* conn.js: decides when this phone has lost its PC, and when it is back.
 * No page code in here (no document, no fetch), so every timing case is tested on its own: tests/js/conn_test.js.
 *
 *   connected     the last check worked
 *   wobbling      checks are failing, but not for long enough to bother anyone (the grace period)
 *   disconnected  failing for the whole grace period: the page shows its blocking popup
 *   reconnecting  the popup is up and a retry is in progress
 */
(function (root) {
  "use strict";

  function createMonitor(options) {
    var o = options || {};
    var now = o.now || Date.now;
    var graceMs = o.graceMs == null ? 8000 : o.graceMs;            // checks must keep failing this long before the popup
    var minShownMs = o.minShownMs == null ? 1500 : o.minShownMs;   // once shown, the popup never just flashes
    var slowMs = o.slowMs == null ? 3500 : o.slowMs;               // a check slower than this counts as "slow"
    var flapWindowMs = o.flapWindowMs == null ? 60000 : o.flapWindowMs;

    var m = { state: "connected", lastOk: now(), firstMiss: null, misses: 0, okStreak: 0, shownAt: 0, shows: [], holdMs: 0, status: 0, slow: false, slowHits: 0 };

    function emit(previous) { if (o.onChange) o.onChange(m.state, previous, m); }
    function to(next) {
      if (next === m.state) return;
      var previous = m.state;
      m.state = next;
      if (next === "disconnected" && previous === "wobbling") { m.shownAt = now(); m.shows.push(m.shownAt); }  // a new popup (a failed retry is not one)
      emit(previous);
    }

    // On a network that keeps dropping and coming back, the popup would open and close all the time. After three popups within a
    // minute, "back" has to be seen twice in a row before it closes.
    function oksNeeded() {
      var t = now();
      m.shows = m.shows.filter(function (at) { return t - at < flapWindowMs; });
      return m.shows.length >= 3 ? 2 : 1;
    }

    function trackSlow(rtt) {
      if (rtt == null) return;
      var was = m.slow;
      if (rtt > slowMs) { if (++m.slowHits >= 2) m.slow = true; }
      else if (rtt < slowMs * 0.6) { m.slowHits = 0; m.slow = false; }
      if (was !== m.slow && o.onSlow) o.onSlow(m.slow);
    }

    /** A check worked (`rtt` = how long it took, in ms). */
    m.ok = function (rtt) {
      var t = now();
      m.lastOk = t; m.misses = 0; m.firstMiss = null; m.status = 0;
      trackSlow(rtt);
      if (m.state === "connected") return;
      if (m.state === "wobbling") { m.okStreak = 0; to("connected"); return; }  // it was only a blip: nobody saw anything
      m.okStreak++;
      if (m.okStreak >= oksNeeded()) {
        m.okStreak = 0;
        m.holdMs = Math.max(0, minShownMs - (t - m.shownAt));  // the page keeps the popup this much longer, so it never flashes
        to("connected");
      }
    };

    /** A check failed. `info.status` = the HTTP status if the PC (or the relay) answered with an error. */
    m.miss = function (info) {
      var t = now();
      m.misses++; m.okStreak = 0;
      if (info && info.status) m.status = info.status;
      if (m.firstMiss === null) m.firstMiss = t;
      if (m.state === "connected") to("wobbling");
      if (m.state === "wobbling") { if (t - m.firstMiss >= graceMs && m.misses >= 2) to("disconnected"); }
      else if (m.state === "reconnecting") to("disconnected");
    };

    /** The person (or the timer) started a retry while the popup is up. */
    m.retrying = function () { if (m.state === "disconnected") to("reconnecting"); };

    /** How long to wait before the next check, in ms: quick while unsure, calm when fine, steady while the popup is up. */
    m.delay = function () {
      return { connected: 5000, wobbling: 2000, disconnected: 6000, reconnecting: 1500 }[m.state];
    };

    /** Forget everything (signed out, or switched to another PC). */
    m.reset = function () {
      m.firstMiss = null; m.misses = 0; m.okStreak = 0; m.shows = []; m.slow = false; m.slowHits = 0; m.status = 0; m.lastOk = now();
      if (m.state !== "connected") { var previous = m.state; m.state = "connected"; emit(previous); }
    };

    return m;
  }

  var api = { createMonitor: createMonitor };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.AfpConn = api;
})(typeof window !== "undefined" ? window : this);
