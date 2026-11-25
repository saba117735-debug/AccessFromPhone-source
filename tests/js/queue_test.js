// The rules for requests held on the phone while the PC can't be reached. Run with:  node tests/js/queue_test.js   (pytest runs it too)
const assert = require("assert");
const Q = require("../../src/handoff/web/queue.js");

let t = 1000000;
const run = [];
function test(name, fn) { try { fn(); run.push([name, true]); } catch (e) { run.push([name, false, e]); } }
function make(extra) { const backend = Q.memoryBackend(); const q = Q.create({ backend, now: () => t, ...extra }); q.backend = backend; return q; }

test("a request is held as queued, with a time limit", () => {
  const q = make();
  const i = q.add({ text: "open wconfig in C:\\RL520APP\\Local", conv: "c1" });
  assert.strictEqual(i.state, "queued"); assert.strictEqual(i.expires - i.created, Q.TTL_MS);
  assert.strictEqual(q.items().length, 1);
});

test("empty text is not held", () => {
  assert.strictEqual(make().add({ text: "   " }), null);
});

test("the oldest waiting request goes first, one at a time", () => {
  const q = make();
  const a = q.add({ text: "one" }); t += 10; const b = q.add({ text: "two" });
  assert.strictEqual(q.next().id, a.id);
  q.mark(a.id, "sending");
  assert.strictEqual(q.next(), null);  // nothing else goes while one is being handed over: order is kept
  q.remove(a.id);
  assert.strictEqual(q.next().id, b.id);
});

test("cancel removes a waiting request, but not one already on its way", () => {
  const q = make();
  const a = q.add({ text: "one" }), b = q.add({ text: "two" });
  q.mark(b.id, "sending");
  assert.strictEqual(q.cancel(a.id), true); assert.strictEqual(q.cancel(b.id), false);
  assert.deepStrictEqual(q.items().map((i) => i.text), ["two"]);
});

test("edit changes the text and gives it a fresh time limit", () => {
  const q = make();
  const a = q.add({ text: "one" }); t += 5000;
  assert.strictEqual(q.edit(a.id, " one, edited "), true);
  assert.strictEqual(q.get(a.id).text, "one, edited"); assert.strictEqual(q.get(a.id).expires, t + Q.TTL_MS);
  assert.strictEqual(q.edit(a.id, "  "), false);
});

test("a request past its time limit expires but stays visible, and can be retried", () => {
  const q = make({ ttlMs: 1000 });
  const a = q.add({ text: "one" }); t += 1500;
  assert.strictEqual(q.items()[0].state, "expired");
  assert.strictEqual(q.next(), null);  // it never goes by itself after expiring
  assert.strictEqual(q.retry(a.id), true); assert.strictEqual(q.next().id, a.id);
});

test("it survives a reload, and a half-sent request is waiting again (the PC ignores the repeat by its id)", () => {
  const q = make();
  const a = q.add({ text: "one" }); q.mark(a.id, "sending");
  const again = Q.create({ backend: q.backend, now: () => t });
  assert.strictEqual(again.items().length, 1); assert.strictEqual(again.get(a.id).state, "queued"); assert.strictEqual(again.get(a.id).id, a.id);
});

test("damaged saved data is ignored, not fatal", () => {
  const backend = Q.memoryBackend(); backend.set([null, 5, { id: 3 }, { id: "ok", text: "fine", created: t, expires: t + 100, state: "queued" }]);
  assert.deepStrictEqual(Q.create({ backend, now: () => t }).items().map((i) => i.id), ["ok"]);
});

test("a failed request waits for the person; it does not block the ones behind it", () => {
  const q = make();
  const a = q.add({ text: "one" }), b = q.add({ text: "two" });
  q.mark(a.id, "failed", "refused");
  assert.strictEqual(q.next().id, b.id);
});

test("power and lock requests are never held", () => {
  for (const s of ["unlock my pc", "Please shut down the computer", "restart it", "shutdown", "sleep", "lock my pc", "hibernate the laptop", "could you reboot the PC"])
    assert.ok(Q.neverQueue(s), s);
});

test("ordinary requests, even with those words inside a name, are held", () => {
  for (const s of ["open wconfig in C:\\RL520APP\\Local", "find restart_log.txt", "list files in shutdown_reports", "what is in notes.txt", "read the unlock guide pdf"])
    assert.ok(!Q.neverQueue(s), s);
});

test("listeners hear about changes", () => {
  const q = make(); let n = 0; q.onChange(() => n++);
  const a = q.add({ text: "one" }); q.cancel(a.id);
  assert.strictEqual(n, 2);
});

let failed = 0;
for (const [name, ok, e] of run) { console.log((ok ? "ok   " : "FAIL ") + name); if (!ok) { failed++; console.log("     " + (e && e.message)); } }
console.log(`\n${run.length - failed} of ${run.length} passed`);
process.exit(failed ? 1 : 0);
