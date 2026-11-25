// The wording of the PC access screen. Run with:  node tests/js/access_test.js   (pytest runs it too)
const assert = require("assert");
const A = require("../../src/handoff/web/access.js");

const run = [];
function test(name, fn) { try { fn(); run.push([name, true]); } catch (e) { run.push([name, false, e]); } }

test("time is said in people's words", () => {
  assert.strictEqual(A.span(5), "a moment");
  assert.strictEqual(A.span(90), "2 minutes");
  assert.strictEqual(A.span(60 * 60), "1 hour");
  assert.strictEqual(A.span(60 * 75), "1 hour 15 min");
  assert.strictEqual(A.span(null), "a moment");
});

test("a free PC says it is free and that looking needs no turn", () => {
  const hl = A.headline({ state: "free", holder: null, ask: null, mine_ask: null });
  assert.strictEqual(hl.kind, "free");
  assert.ok(/free/i.test(hl.title) && /never needs one/i.test(hl.sub));
});

test("your own turn says how long, and mentions idling only when it matters", () => {
  const busy = A.headline({ state: "mine", holder: { name: "Me", since: 180, idle: 10 }, ask: null });
  assert.strictEqual(busy.kind, "mine"); assert.ok(/3 minutes/.test(busy.sub) && !/Idle/.test(busy.sub));
  const idle = A.headline({ state: "mine", holder: { name: "Me", since: 600, idle: 200 }, ask: null });
  assert.ok(/Idle for 3 minutes/.test(idle.sub));
});

test("when someone is asking you, the state says so", () => {
  assert.strictEqual(A.headline({ state: "mine", holder: { since: 5, idle: 1 }, ask: { name: "Ben" } }).kind, "ask");
});

test("someone else's turn names them; waiting shows the countdown", () => {
  const other = A.headline({ state: "other", holder: { name: "Asha's phone", since: 300, idle: 0 }, ask: null, mine_ask: null });
  assert.strictEqual(other.title, "Asha's phone is using this PC"); assert.strictEqual(other.kind, "other");
  const waiting = A.headline({ state: "other", holder: { name: "Asha's phone", since: 300, idle: 0 }, mine_ask: { holder: "Asha's phone", expires_in: 42 } });
  assert.strictEqual(waiting.kind, "waiting"); assert.ok(/Waiting for Asha's phone/.test(waiting.title) && /42 s/.test(waiting.sub));
});

test("it copes with no answer yet", () => {
  assert.strictEqual(A.headline(null).kind, "wait");
});

test("notices say what happened to your turn or request", () => {
  assert.ok(/your turn/i.test(A.noticeText({ kind: "granted", by: "" })));
  assert.ok(/Asha is still using/.test(A.noticeText({ kind: "kept", by: "Asha" })));
  assert.ok(/Ben took over/.test(A.noticeText({ kind: "taken", by: "Ben" })));
  assert.strictEqual(A.noticeText(null), null);
  assert.strictEqual(A.noticeText({ kind: "mystery" }), null);
});

test("every level has a plain explanation", () => {
  for (const level of ["viewer", "editor", "operator", "admin"]) assert.ok(A.LEVEL_HELP[level].length > 20, level);
});

let failed = 0;
for (const [name, ok, e] of run) { console.log((ok ? "ok   " : "FAIL ") + name); if (!ok) { failed++; console.log("     " + (e && e.message)); } }
console.log(`\n${run.length - failed} of ${run.length} passed`);
process.exit(failed ? 1 : 0);
