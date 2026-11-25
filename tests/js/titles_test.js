// How a chat gets its name from the first request. Run with:  node tests/js/titles_test.js   (pytest runs it too)
const assert = require("assert");
const T = require("../../src/handoff/web/titles.js");

const run = [];
function test(name, fn) { try { fn(); run.push([name, true]); } catch (e) { run.push([name, false, e]); } }
const B = String.fromCharCode(92);  // a backslash
const win = (s) => s.split("|").join(B);

test("polite openings and repeated endings are dropped, a path becomes its last name", () => {
  assert.strictEqual(T.make("i want to open " + win("C:|output") + " folder please open it"), "Open output folder");
});

test("power requests get a plain name", () => {
  assert.strictEqual(T.make("lock my PC"), "Lock PC");
  assert.strictEqual(T.make("Please unlock my computer"), "Unlock PC");
  assert.strictEqual(T.make("can you put my laptop to sleep"), "Put laptop to sleep");
});

test("questions lose their question mark and filler", () => {
  assert.strictEqual(T.make("hey, can you tell me what is in notes.txt?"), "Tell me what is in notes.txt");
  assert.strictEqual(T.make("what's the battery status?"), "What's battery status");
});

test("a request about a file keeps the verb and the file", () => {
  assert.strictEqual(T.make("Read " + win("C:|RL520APP|Local|wconfig.ini") + " please"), "Read wconfig.ini");
  assert.strictEqual(T.make("find all .bak files in " + win("C:|Users|Asha|Documents")), "Find all .bak files in Documents");
});

test("a folder name with spaces inside a path does not leak into the name", () => {
  const t = T.make("open " + win("C:|Users|Asha|OneDrive - Unisys|Desktop|Pictures") + " folder");
  assert.strictEqual(t, "Open Pictures folder");
  assert.strictEqual(T.make("copy " + win("C:|a|b.txt") + " to " + win("D:|backup|b.txt")), "Copy b.txt to b.txt");  // two paths stay two names
});

test("long requests are cut at a word, never in the middle of one", () => {
  const t = T.make("show me every single file that was changed in the last three weeks across all of my project folders please");
  assert.ok(t.split(" ").length <= 7 && t.length <= 48 && !/\s$/.test(t), t);
  const u = T.make("abcdefghij ".repeat(8), { maxChars: 30 });
  assert.ok(u.length <= 30 && !/\s$/.test(u), u);
});

test("it never returns an empty name", () => {
  for (const s of ["", "   ", "please", "?!", null, undefined]) assert.ok(T.make(s).length > 0, String(s));
  assert.strictEqual(T.make(""), "New chat");
});

test("the first letter is a capital and the rest is left alone", () => {
  assert.strictEqual(T.make("open Teams"), "Open Teams");
  assert.strictEqual(T.make("send report.PDF to my phone"), "Send report.PDF to phone");
});

test("the model's wording is tidied: one line, no label, no quotes", () => {
  assert.strictEqual(T.tidy('"Open output folder"'), "Open output folder");
  assert.strictEqual(T.tidy("Title: Battery status check.\nThis chat is about..."), "Battery status check");
  assert.strictEqual(T.tidy("**Lock PC**"), "Lock PC");
});

test("a refusal or a rambling answer is not used as a name", () => {
  assert.strictEqual(T.tidy("I'm sorry, I can't help with that"), null);
  assert.strictEqual(T.tidy("x".repeat(80)), null);
  assert.strictEqual(T.tidy(""), null);
  assert.strictEqual(T.tidy(null), null);
});

let failed = 0;
for (const [name, ok, e] of run) { console.log((ok ? "ok   " : "FAIL ") + name); if (!ok) { failed++; console.log("     " + (e && e.message)); } }
console.log(`\n${run.length - failed} of ${run.length} passed`);
process.exit(failed ? 1 : 0);
