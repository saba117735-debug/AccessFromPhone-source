// Every timing case of the connection monitor. Run with:  node tests/js/conn_test.js   (pytest runs it too)
const assert = require("assert");
const { createMonitor } = require("../../src/handoff/web/conn.js");

let t = 0;
const clock = () => t;
const run = [];
function test(name, fn) { t = 0; try { fn(); run.push([name, true]); } catch (e) { run.push([name, false, e]); } }
function make(extra) { const log = []; const m = createMonitor({ now: clock, onChange: (s, p) => log.push(p + ">" + s), ...extra }); m.log = log; return m; }

test("starts connected and stays quiet while checks work", () => {
  const m = make();
  for (let i = 0; i < 10; i++) { t += 5000; m.ok(120); }
  assert.strictEqual(m.state, "connected"); assert.deepStrictEqual(m.log, []);
});

test("one or two failed checks are a blip: nothing is shown, and it recovers silently", () => {
  const m = make();
  m.miss(); t += 2000; m.miss();
  assert.strictEqual(m.state, "wobbling");
  t += 2000; m.ok(100);
  assert.strictEqual(m.state, "connected"); assert.deepStrictEqual(m.log, ["connected>wobbling", "wobbling>connected"]);
  assert.strictEqual(m.shows.length, 0);
});

test("the popup waits out the grace period, then appears", () => {
  const m = make({ graceMs: 8000 });
  m.miss(); t += 2000; m.miss(); t += 2000; m.miss(); t += 2000; m.miss();
  assert.strictEqual(m.state, "wobbling");  // 6s: still inside the grace period
  t += 2500; m.miss();                       // 8.5s
  assert.strictEqual(m.state, "disconnected");
  assert.strictEqual(m.shows.length, 1);
});

test("a single failed check, however old, is not enough", () => {
  const m = make();
  m.miss(); t += 60000;  // the page was in the background for a minute, then one check failed
  m.miss();              // second miss, long after the first: counts
  assert.strictEqual(m.state, "disconnected");
  const m2 = make(); m2.miss(); assert.strictEqual(m2.state, "wobbling");
});

test("a failed retry keeps the popup and is not a new popup", () => {
  const m = make({ graceMs: 0 });
  m.miss(); m.miss();
  assert.strictEqual(m.state, "disconnected");
  m.retrying(); assert.strictEqual(m.state, "reconnecting");
  t += 1000; m.miss({ status: 503 });
  assert.strictEqual(m.state, "disconnected"); assert.strictEqual(m.shows.length, 1); assert.strictEqual(m.status, 503);
});

test("recovery closes the popup, but never before it has been up long enough to read", () => {
  const m = make({ graceMs: 0, minShownMs: 1500 });
  m.miss(); m.miss(); t += 400;
  m.ok(100);
  assert.strictEqual(m.state, "connected");
  assert.strictEqual(m.holdMs, 1100);  // the page keeps it 1.1s more
  const m2 = make({ graceMs: 0, minShownMs: 1500 });
  m2.miss(); m2.miss(); t += 9000; m2.ok(100);
  assert.strictEqual(m2.holdMs, 0);
});

test("a network that keeps dropping must show 'back' twice in a row before the popup closes", () => {
  const m = make({ graceMs: 0 });
  for (let i = 0; i < 3; i++) { m.miss(); m.miss(); if (i < 2) { t += 5000; m.ok(100); t += 100; } }
  assert.strictEqual(m.state, "disconnected"); assert.strictEqual(m.shows.length, 3);
  m.ok(100); assert.notStrictEqual(m.state, "connected");  // once is not enough now
  m.miss(); assert.strictEqual(m.state, "disconnected");   // and a miss in between starts the count again
  m.ok(100); m.ok(100); assert.strictEqual(m.state, "connected");
});

test("the delay between checks fits the state", () => {
  const m = make({ graceMs: 0 });
  assert.strictEqual(m.delay(), 5000);
  m.miss(); assert.strictEqual(m.delay(), 2000);
  m.miss(); assert.strictEqual(m.delay(), 6000);
  m.retrying(); assert.strictEqual(m.delay(), 1500);
});

test("a slow connection is noticed after two slow checks, and forgiven when it speeds up", () => {
  const seen = []; const m = make({ onSlow: (s) => seen.push(s) });
  m.ok(5000); assert.strictEqual(m.slow, false);
  m.ok(5200); assert.strictEqual(m.slow, true);
  m.ok(3000); assert.strictEqual(m.slow, true);   // not clearly faster yet
  m.ok(300); assert.strictEqual(m.slow, false);
  assert.deepStrictEqual(seen, [true, false]);
  assert.strictEqual(m.state, "connected");        // slow is never "disconnected"
});

test("reset forgets everything (signed out, or switched to another PC)", () => {
  const m = make({ graceMs: 0 });
  m.miss(); m.miss(); m.reset();
  assert.strictEqual(m.state, "connected"); assert.strictEqual(m.misses, 0); assert.strictEqual(m.shows.length, 0);
  assert.ok(m.log.includes("disconnected>connected"));
});

let failed = 0;
for (const [name, ok, e] of run) { console.log((ok ? "ok   " : "FAIL ") + name); if (!ok) { failed++; console.log("     " + (e && e.message)); } }
console.log(`\n${run.length - failed} of ${run.length} passed`);
process.exit(failed ? 1 : 0);
