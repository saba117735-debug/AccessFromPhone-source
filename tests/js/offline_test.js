// The saved-chats rules: what is kept, how much, how it is read back. Run with:  node tests/js/offline_test.js   (pytest runs it too)
const assert = require("assert");
const Offline = require("../../src/handoff/web/offline.js");

const run = [];
function test(name, fn) { Promise.resolve().then(fn).then(() => run.push([name, true]), (e) => run.push([name, false, e])); }

let n = 0;
function job(conv, minutes, extra) {
  n += 1;
  return Object.assign({ id: "j" + n, prompt: "p" + n, status: "done", conversation: conv, created_at: new Date(Date.UTC(2025, 0, 1, 12, minutes)).toISOString(), outputs: ["out"], changed: [] }, extra || {});
}

test("whole chats are kept, newest first, up to the count", () => {
  const jobs = [];
  for (let c = 0; c < 60; c++) { jobs.push(job("c" + c, c)); jobs.push(job("c" + c, c + 0.5 > 59 ? 59 : c)); }
  const t = Offline.trim(jobs, [], { maxChats: 50 });
  assert.strictEqual(t.chats, 50);
  const convs = new Set(t.jobs.map((j) => j.conversation));
  assert.strictEqual(convs.size, 50);
  assert.ok(convs.has("c59") && !convs.has("c0"), "the newest are kept, the oldest are dropped");
  assert.strictEqual(t.dropped, 20);
  for (const c of convs) assert.strictEqual(t.jobs.filter((j) => j.conversation === c).length, 2, "a chat is never cut in half");
});

test("the size cap drops the oldest chats first", () => {
  const big = "x".repeat(1000);
  const jobs = [];
  for (let c = 0; c < 10; c++) jobs.push(job("c" + c, c, { outputs: [big] }));
  const t = Offline.trim(jobs, [], { maxBytes: 4500 });
  assert.ok(t.chats >= 2 && t.chats < 10, "some fit, not all: " + t.chats);
  assert.ok(JSON.stringify(t.jobs).length <= 4500);
  assert.ok(t.jobs.some((j) => j.conversation === "c9") && !t.jobs.some((j) => j.conversation === "c0"));
});

test("one chat too big for the cap keeps its newest turns instead of nothing", () => {
  const jobs = [];
  for (let i = 0; i < 20; i++) jobs.push(job("only", i, { outputs: ["y".repeat(500)] }));
  const t = Offline.trim(jobs, [], { maxBytes: 3000 });
  assert.ok(t.jobs.length > 0 && t.jobs.length < 20);
  assert.strictEqual(t.jobs[0].created_at, jobs[19].created_at, "newest first");
  assert.ok(t.dropped > 0);
});

test("a huge result is shortened and says so", () => {
  const t = Offline.trim([job("a", 1, { outputs: ["z".repeat(50000)] })], [], { maxOutput: 100 });
  assert.ok(t.jobs[0].outputs[0].length < 200 && t.jobs[0].outputs[0].includes("shortened"));
});

test("only the audit events of kept chats are saved", () => {
  const a = job("a", 1), b = job("b", 2);
  const audit = [{ job: a.id, event: "submitted" }, { job: b.id, event: "done" }, { job: "other", event: "x" }, { job: "-", event: "note" }];
  const t = Offline.trim([a, b], audit, { maxChats: 1 });
  assert.deepStrictEqual(t.audit.map((e) => e.job), [b.id]);
});

test("the signature changes when a job changes, not when nothing did", () => {
  const a = job("a", 1, { status: "running" });
  const s1 = Offline.signature([a]);
  assert.strictEqual(Offline.signature([Object.assign({}, a)]), s1);
  assert.notStrictEqual(Offline.signature([Object.assign({}, a, { status: "done" })]), s1);
  assert.notStrictEqual(Offline.signature([Object.assign({}, a, { outputs: ["1", "2"] })]), s1);
});

test("a saved copy is read back as it was saved, and a damaged one is ignored", async () => {
  const mem = Offline.memoryBackend();
  const store = Offline.create({ backend: mem, now: () => 5000 });
  assert.strictEqual(await store.init(), null);
  assert.strictEqual(store.has(), false);
  const j = job("a", 1);
  const snap = await store.save("MY-PC", { pc: "MY-PC", user: "afza", version: "1" }, [j], [{ job: j.id, event: "submitted" }]);
  assert.ok(Offline.valid(snap) && snap.savedAt === 5000 && snap.pc === "MY-PC");
  const again = Offline.create({ backend: mem });
  const read = await again.init();
  assert.strictEqual(read.jobs.length, 1);
  assert.strictEqual(again.has(), true);
  await mem.set({ v: 99, nonsense: true });
  assert.strictEqual(await Offline.create({ backend: mem }).init(), null);
});

test("clearing removes the copy and cancels a save that was waiting", async () => {
  const mem = Offline.memoryBackend();
  const store = Offline.create({ backend: mem });
  await store.init();
  store.queue(() => ({ pc: "PC", info: null, jobs: [job("a", 1)], audit: [] }), 30);
  await store.clear();
  await new Promise((r) => setTimeout(r, 80));
  assert.strictEqual(await mem.get(), null, "the waiting save must not bring it back");
  assert.strictEqual(store.has(), false);
});

test("an unchanged list is not saved again", async () => {
  let writes = 0;
  const mem = Offline.memoryBackend(), set = mem.set;
  mem.set = (v) => { writes++; return set(v); };
  const store = Offline.create({ backend: mem });
  await store.init();
  const jobs = [job("a", 1)];
  store.queue(() => ({ pc: "PC", info: null, jobs, audit: [] }), 10);
  await new Promise((r) => setTimeout(r, 50));
  store.queue(() => ({ pc: "PC", info: null, jobs: jobs.map((j) => Object.assign({}, j)), audit: [] }), 10);
  await new Promise((r) => setTimeout(r, 50));
  assert.strictEqual(writes, 1);
});

test("a failing store never throws into the page", async () => {
  const broken = { name: "x", get: () => Promise.reject(new Error("no")), set: () => Promise.reject(new Error("full")), del: () => Promise.reject(new Error("no")) };
  const store = Offline.create({ backend: broken });
  assert.strictEqual(await store.init(), null);
  assert.strictEqual(await store.save("PC", null, [job("a", 1)], []), null);
  await store.clear();
});

test("the backend is the app's own file when the app is there, else the browser's", () => {
  const app = { saveChats() {}, savedChats() { return ""; }, clearChats() {} };
  assert.strictEqual(Offline.pick({ AfpApp: app, indexedDB: {} }).name, "app");
  assert.strictEqual(Offline.pick({ indexedDB: {} }).name, "idb");
  assert.strictEqual(Offline.pick({ localStorage: { getItem() {} } }).name, "localStorage");
  assert.strictEqual(Offline.pick({}).name, "memory");
});

test("the age is said in plain words", () => {
  const t0 = 1000000;
  assert.strictEqual(Offline.age(t0, t0 + 30000), "a moment ago");
  assert.strictEqual(Offline.age(t0, t0 + 10 * 60000), "10 minutes ago");
  assert.strictEqual(Offline.age(t0, t0 + 3600000), "1 hour ago");
  assert.strictEqual(Offline.age(t0, t0 + 5 * 3600000), "5 hours ago");
  assert.strictEqual(Offline.age(t0, t0 + 2 * 86400000), "2 days ago");
});

setTimeout(() => {
  let failed = 0;
  for (const [name, ok, e] of run) { console.log((ok ? "ok   " : "FAIL ") + name); if (!ok) { failed++; console.log("     " + (e && e.stack || e)); } }
  console.log(`\n${run.length - failed}/${run.length} passed`);
  process.exit(failed || run.length < 11 ? 1 : 0);
}, 600);
