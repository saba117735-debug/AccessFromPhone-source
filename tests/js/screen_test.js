// The PC screen viewer's logic: the chat shortcut, the wording, automatic quality, where a touch lands on the PC's screen, and what a touch means.
// Run with:  node tests/js/screen_test.js   (pytest runs it too)
const assert = require("assert");
const S = require("../../src/handoff/web/screen.js");

const run = [];
function test(name, fn) { try { fn(); run.push([name, true]); } catch (e) { run.push([name, false, e]); } }

// ---------------------------------------------------------------- the chat shortcut and the words

test("typing what you want opens the screen instead of sending a request", () => {
  for (const s of ["i want to access my PC screen", "show my pc screen", "Share my screen", "can I see my laptop screen", "view the screen", "remote desktop", "open my PC's screen please",
    "I want to look at my computer screen", "I want to control my PC screen"])
    assert.ok(S.wants(s), s);
});

test("ordinary requests about files and settings are left alone", () => {
  for (const s of ["take a screenshot", "read screen.txt", "open screen_log.txt in notes", "lock the screen", "turn off the screen", "what is the screen resolution", "show files on my desktop",
    "find report in C:\\Users\\Asha\\Desktop", "open the Desktop folder", "list files", "access my files", "screen saver timeout", "", "record my screen"])
    assert.ok(!S.wants(s), s);
});

test("a long message is about something else, even if it says screen", () => {
  assert.ok(!S.wants("please write a long report about the screen use of my team over the last quarter and then show the numbers for every single week of the year in a table"));
});

test("each state of the permission says what it is", () => {
  assert.strictEqual(S.headline({ state: "none" }).kind, "none");
  assert.ok(/yes on the PC/.test(S.headline({ state: "none" }).sub));
  assert.ok(/Allow and Deny/.test(S.headline({ state: "pending" }).sub));
  assert.ok(/15 minutes/.test(S.headline({ state: "granted", left: 900, forever: false }).sub));
  assert.ok(/Admin phone/.test(S.headline({ state: "granted", trusted: true }).sub));
  assert.strictEqual(S.headline({ state: "busy", viewer: "Ben's phone" }).title, "Ben's phone is looking");
  assert.strictEqual(S.headline({ state: "off" }).kind, "off");
  assert.strictEqual(S.headline(null).kind, "wait");
});

test("time left is said in people's words", () => {
  assert.strictEqual(S.leftText(30, false), "less than a minute");
  assert.strictEqual(S.leftText(900, false), "15 minutes");
  assert.strictEqual(S.leftText(7200, false), "2 hours");
  assert.strictEqual(S.leftText(null, true), "until the PC owner takes it back");
});

// ---------------------------------------------------------------- automatic quality

const fresh = (i) => ({ i, good: 0, at: 0 });

test("quality steps through auto, fast, balanced, sharp and round again; unknown means auto", () => {
  assert.deepStrictEqual(["auto", "fast", "balanced", "sharp", "auto"].map((id) => S.nextPreset(id).id), ["fast", "balanced", "sharp", "auto", "fast"]);
  assert.strictEqual(S.preset("nope").id, "auto");
  assert.ok(S.PRESETS.filter((p) => p.w).every((p) => p.w <= 1920 && p.q >= 20 && p.q <= 85));
  assert.ok(S.LADDER.every((r, i) => i === 0 || (r.w > S.LADDER[i - 1].w && r.q >= S.LADDER[i - 1].q)));  // the ladder only ever gets bigger and sharper
});

test("a slow picture steps the quality down at once, a very slow one by two", () => {
  assert.strictEqual(S.adapt(fresh(3), { ms: 450, now: 1000 }).i, 2);
  assert.strictEqual(S.adapt(fresh(3), { ms: 900, now: 1000 }).i, 1);
  assert.strictEqual(S.adapt(fresh(0), { ms: 900, now: 1000 }).i, 0);  // never below the bottom
});

test("quality only goes back up after a few quick pictures AND a short pause, one step at a time", () => {
  let s = fresh(2);
  for (let k = 0; k < 3; k++) s = S.adapt(s, { ms: 100, now: 10000 + k * 100 });
  assert.strictEqual(s.i, 2);                                       // three quick ones: not yet
  s = S.adapt(s, { ms: 100, now: 10300 });
  assert.strictEqual(s.i, 3);                                       // the fourth, and enough time since the start: one step up
  for (let k = 0; k < 4; k++) s = S.adapt(s, { ms: 100, now: 10400 + k * 100 });
  assert.strictEqual(s.i, 3);                                       // quick again, but it moved a moment ago: it waits
  s = S.adapt(s, { ms: 100, now: 12000 });
  assert.strictEqual(s.i, 4);
});

test("the sharpest rung is the PC's own width, and the sharp picture is bigger still", () => {
  assert.strictEqual(S.LADDER[S.LADDER.length - 1].w, 1920);
  assert.ok(S.REFINE.w >= 1920 && S.REFINE.q > S.LADDER[S.LADDER.length - 1].q);
  assert.ok(S.PEEK.w < 640 && S.PEEK.gap >= 100);                   // the look-ins while nothing moves are small and unhurried
});

test("while controlling, a slower picture steps Auto down sooner, but never into blur", () => {
  assert.strictEqual(S.adapt(fresh(5), { ms: 200, now: 1000, live: true }).i, 4);          // 200 ms is fine for looking, too slow while the hand is moving
  assert.strictEqual(S.adapt(fresh(5), { ms: 200, now: 1000 }).i, 5);                       // the same picture when not controlling: no change
  assert.strictEqual(S.adapt(fresh(5), { ms: 400, now: 1000, live: true }).i, 3);
  const floor = S.adapt(fresh(2), { ms: 900, now: 1000, live: true }).i;
  assert.strictEqual(floor, 2);                                                              // never below 1024 px while controlling
  assert.strictEqual(S.adapt(fresh(0), { ms: 50, now: 1000, live: true }).i >= 2, true);   // and a lower start is lifted to it
  assert.strictEqual(S.adapt(fresh(0), { ms: 900, now: 1000 }).i, 0);                       // looking only can still go all the way down
});

test("while controlling it climbs back up when the pictures are quick", () => {
  let s = fresh(2);
  for (let k = 0; k < 4; k++) s = S.adapt(s, { ms: 50, now: 10000 + k * 100, live: true });
  assert.strictEqual(s.i, 3);
});

test("an in-between picture changes nothing and breaks a run of quick ones", () => {
  let s = { i: 3, good: 4, at: 0 };
  s = S.adapt(s, { ms: 250, now: 9000 });
  assert.deepStrictEqual([s.i, s.good], [3, 0]);
});

test("it never goes past the top of the ladder", () => {
  let s = fresh(S.LADDER.length - 1);
  for (let k = 0; k < 40; k++) s = S.adapt(s, { ms: 50, now: 100000 + k * 5000 });
  assert.strictEqual(s.i, S.LADDER.length - 1);
});

test("a slow link settles low and a fast one climbs high", () => {
  let slow = fresh(3), fast = fresh(0), t = 0;
  for (let k = 0; k < 60; k++) { t += 5000; slow = S.adapt(slow, { ms: 800, now: t }); fast = S.adapt(fast, { ms: 90, now: t }); }
  assert.strictEqual(slow.i, 0); assert.strictEqual(fast.i, S.LADDER.length - 1);
});

test("the network is named in a word", () => {
  assert.deepStrictEqual([100, 300, 800].map(S.networkWord), ["Good", "Fair", "Slow"]);
});

// ---------------------------------------------------------------- where a touch lands

const rect = (left, top, width, height) => ({ left, top, width, height });

test("a touch in the middle of the picture is the middle of the PC's screen", () => {
  const p = S.pointToScreen(150, 100, rect(100, 50, 100, 100), { w: 1920, h: 1920 }, false);
  assert.ok(Math.abs(p.x - 0.5) < 1e-9 && Math.abs(p.y - 0.5) < 1e-9);
});

test("the black bars around a wide picture are not part of the PC's screen", () => {
  const box = rect(0, 0, 400, 400), nat = { w: 1920, h: 1080 };  // 16:9 inside a square: bars above and below
  assert.strictEqual(S.pointToScreen(200, 5, box, nat, false), null);
  assert.strictEqual(S.pointToScreen(200, 395, box, nat, false), null);
  const top = S.pointToScreen(200, 400 / 2 - (400 * 9 / 16) / 2 + 1, box, nat, false);
  assert.ok(top && top.y < 0.01 && Math.abs(top.x - 0.5) < 1e-9);   // just inside the picture's top edge
  assert.ok(S.pointToScreen(0, 200, box, nat, false).x === 0);       // its left edge
});

test("the corners of the picture are the corners of the PC's screen", () => {
  const box = rect(10, 20, 800, 450), nat = { w: 1920, h: 1080 };
  const tl = S.pointToScreen(10, 20, box, nat, false), br = S.pointToScreen(810, 470, box, nat, false);
  assert.deepStrictEqual([tl.x, tl.y], [0, 0]); assert.ok(Math.abs(br.x - 1) < 1e-9 && Math.abs(br.y - 1) < 1e-9);
});

test("a picture turned sideways on an upright phone maps back correctly", () => {
  // Phone 400 wide by 800 tall. The picture's own box is 800 wide by 400 tall, turned a quarter turn clockwise: on screen it occupies x 0..400, y 0..800.
  const onScreen = rect(0, 0, 400, 800), nat = { w: 1920, h: 960 };   // a 2:1 picture fills the 800x400 box exactly
  const topLeft = S.pointToScreen(400 - 1, 1, onScreen, nat, true);    // the picture's top-left lands at the phone's top-RIGHT corner
  assert.ok(topLeft.x < 0.01 && topLeft.y < 0.01, JSON.stringify(topLeft));
  const topRight = S.pointToScreen(400 - 1, 799, onScreen, nat, true); // its top-right is the phone's bottom-right
  assert.ok(topRight.x > 0.99 && topRight.y < 0.01, JSON.stringify(topRight));
  const bottomLeft = S.pointToScreen(1, 1, onScreen, nat, true);       // its bottom-left is the phone's top-left
  assert.ok(bottomLeft.x < 0.01 && bottomLeft.y > 0.99, JSON.stringify(bottomLeft));
  const middle = S.pointToScreen(200, 400, onScreen, nat, true);
  assert.ok(Math.abs(middle.x - 0.5) < 1e-9 && Math.abs(middle.y - 0.5) < 1e-9);
});

test("it copes with a picture that has not arrived yet or a box with no size", () => {
  assert.strictEqual(S.pointToScreen(1, 1, rect(0, 0, 100, 100), null, false), null);
  assert.strictEqual(S.pointToScreen(1, 1, rect(0, 0, 0, 0), { w: 10, h: 10 }, false), null);
});

// ---------------------------------------------------------------- what a touch means

function rig(opts) {
  const events = [], timers = []; let nextId = 1;
  const fake = { set: (fn, ms) => { const id = nextId++; timers.push({ id, fn, ms }); return id; }, clear: (id) => { const i = timers.findIndex((t) => t.id === id); if (i >= 0) timers.splice(i, 1); } };
  const g = S.createGestures((e) => events.push(e), fake, opts);
  return { g, events, timers, fire: () => { const t = timers.shift(); if (t) t.fn(); } };
}

test("a quick tap is one left click where the finger was", () => {
  const r = rig(); r.g.down(1, 50, 60); r.g.up(1, 50, 60);
  assert.deepStrictEqual(r.events, [{ kind: "click", x: 50, y: 60, button: "left" }]);
  assert.strictEqual(r.timers.length, 0);   // and the long-press timer is gone
});

test("a tiny wobble is still a tap, not a drag", () => {
  const r = rig(); r.g.down(1, 50, 60); r.g.move(1, 54, 63); r.g.up(1, 54, 63);
  assert.deepStrictEqual(r.events, [{ kind: "click", x: 50, y: 60, button: "left" }]);
});

test("pressing and holding right-clicks once, and letting go afterwards does nothing more", () => {
  const r = rig(); r.g.down(1, 50, 60); r.fire(); r.g.up(1, 50, 60);
  assert.deepStrictEqual(r.events, [{ kind: "click", x: 50, y: 60, button: "right" }]);
});

test("dragging presses where it began, follows the finger, and lets go where it ended", () => {
  const r = rig(); r.g.down(1, 10, 10); r.g.move(1, 30, 10); r.g.move(1, 60, 40); r.g.up(1, 60, 40);
  assert.deepStrictEqual(r.events.map((e) => e.kind), ["drag-start", "drag-move", "drag-move", "drag-end"]);
  assert.deepStrictEqual([r.events[0].x, r.events[0].y], [10, 10]);                  // the press is where the finger first touched, not where it had moved to
  assert.deepStrictEqual([r.events[3].x, r.events[3].y], [60, 40]);
  assert.strictEqual(r.timers.length, 0);                                           // moving cancelled the long press
});

test("two fingers scroll: moving down scrolls up, in steps, and nothing is clicked", () => {
  const r = rig({ step: 20 }); r.g.down(1, 100, 100); r.g.down(2, 140, 100);
  r.g.move(1, 100, 130); r.g.move(2, 140, 130);   // both fingers down by 30
  const scrolls = r.events.filter((e) => e.kind === "scroll");
  assert.ok(scrolls.length >= 1 && scrolls.every((e) => e.dy > 0));
  assert.strictEqual(scrolls.reduce((n, e) => n + e.dy, 0), 1);   // 30 px at 20 px a step: one notch so far (10 px waiting)
  r.g.move(1, 100, 70); r.g.move(2, 140, 70);     // back up by 60
  const total = r.events.filter((e) => e.kind === "scroll").reduce((n, e) => n + e.dy, 0);
  assert.strictEqual(total, -1);                   // +1 down, then 60 px back up at 20 px a step is two notches the other way: net one notch up
  r.g.up(1, 100, 70); r.g.up(2, 140, 70);
  assert.ok(!r.events.some((e) => e.kind === "click"));
});

test("a second finger during a drag ends the drag and turns it into a scroll", () => {
  const r = rig(); r.g.down(1, 10, 10); r.g.move(1, 40, 40); r.g.down(2, 80, 80);
  assert.deepStrictEqual(r.events.map((e) => e.kind), ["drag-start", "drag-move", "drag-end"]);
  assert.strictEqual(r.g.mode(), "scroll");
});

test("lifting one finger after a scroll does not click or drag with the other", () => {
  const r = rig(); r.g.down(1, 10, 10); r.g.down(2, 50, 10); r.g.up(2, 50, 10); r.g.move(1, 90, 90); r.g.up(1, 90, 90);
  assert.deepStrictEqual(r.events, []);
  assert.strictEqual(r.g.mode(), "idle");
});

test("a cancelled touch (the system took it over) clicks nothing", () => {
  const r = rig(); r.g.down(1, 50, 60); r.g.cancel(1, 50, 60);
  assert.deepStrictEqual(r.events, []);
});

test("resetting forgets everything, including a pending long press", () => {
  const r = rig(); r.g.down(1, 50, 60); r.g.reset();
  assert.strictEqual(r.timers.length, 0); assert.strictEqual(r.g.mode(), "idle");
  r.g.up(1, 50, 60);
  assert.deepStrictEqual(r.events, []);
});


// ---------------------------------------------------------------- the trackpad: one finger moves the pointer

function pad(opts) {
  const events = [], timers = []; let nextId = 1;
  const fake = { set: (fn, ms) => { const id = nextId++; timers.push({ id, fn, ms }); return id; }, clear: (id) => { const i = timers.findIndex((t) => t.id === id); if (i >= 0) timers.splice(i, 1); } };
  const g = S.createTrackpad((e) => events.push(e), fake, opts);
  return { g, events, timers, fire: (ms) => { const i = timers.findIndex((t) => t.ms === ms); if (i >= 0) { const [t] = timers.splice(i, 1); t.fn(); } }, kinds: () => events.map((e) => e.kind + (e.button ? ":" + e.button : "")) };
}

test("one finger sliding moves the pointer by how far it moved, and nothing is clicked", () => {
  const r = pad(); r.g.down(1, 100, 100, 0); r.g.move(1, 130, 100, 30); r.g.move(1, 160, 110, 60); r.g.up(1, 160, 110, 90);
  const moves = r.events.filter((e) => e.kind === "move");
  assert.strictEqual(moves.length, 2);
  assert.deepStrictEqual([moves[0].dx, moves[0].dy, moves[1].dx, moves[1].dy], [30, 0, 30, 10]);   // each is the change since the one before
  assert.ok(!r.kinds().includes("click:left"));
  assert.strictEqual(r.timers.length, 0);                                                           // moving cancelled the hold
});

test("a small wobble under the slop is still a tap, which clicks", () => {
  const r = pad(); r.g.down(1, 50, 50, 0); r.g.move(1, 54, 53, 40); r.g.up(1, 54, 53, 120);
  assert.deepStrictEqual(r.kinds(), ["click:left"]);
});

test("a long touch with no movement is not a click", () => {
  const r = pad(); r.g.down(1, 50, 50, 0); r.g.up(1, 50, 50, 900);
  assert.deepStrictEqual(r.kinds(), []);
});

test("tap, then touch again and slide: a drag with the button held down", () => {
  const r = pad(); r.g.down(1, 80, 80, 0); r.g.up(1, 80, 80, 80);                       // the tap (one click)
  r.g.down(1, 82, 82, 200); r.g.move(1, 120, 90, 240); r.g.move(1, 160, 100, 280); r.g.up(1, 160, 100, 320);
  assert.deepStrictEqual(r.kinds(), ["click:left", "drag-start", "move", "move", "drag-end"]);
  const order = r.kinds();
  assert.ok(order.indexOf("drag-start") < order.indexOf("move") && order.lastIndexOf("move") < order.indexOf("drag-end"));
});

test("tap and tap again without moving is a double click (two clicks, quickly)", () => {
  const r = pad(); r.g.down(1, 80, 80, 0); r.g.up(1, 80, 80, 60); r.g.down(1, 81, 80, 160); r.g.up(1, 81, 80, 220);
  assert.deepStrictEqual(r.kinds(), ["click:left", "click:left"]);
});

test("a second tap that comes too late or lands far away is just another click, not a drag", () => {
  const late = pad(); late.g.down(1, 80, 80, 0); late.g.up(1, 80, 80, 60); late.g.down(1, 80, 80, 700); late.g.move(1, 150, 80, 740);
  assert.ok(!late.kinds().includes("drag-start"));
  const far = pad(); far.g.down(1, 80, 80, 0); far.g.up(1, 80, 80, 60); far.g.down(1, 300, 300, 150); far.g.move(1, 380, 300, 190);
  assert.ok(!far.kinds().includes("drag-start"));
});

test("two fingers tapping together is a right click", () => {
  const r = pad(); r.g.down(1, 100, 100, 0); r.g.down(2, 140, 100, 10); r.g.up(2, 140, 100, 120); r.g.up(1, 100, 100, 130);
  assert.deepStrictEqual(r.kinds(), ["click:right"]);
});

test("two fingers sliding scroll (down = scroll up) and do not click or move the pointer", () => {
  const r = pad({ step: 20 }); r.g.down(1, 100, 100, 0); r.g.down(2, 140, 100, 5);
  for (let k = 1; k <= 5; k++) { r.g.move(1, 100, 100 + k * 25, 20 * k); r.g.move(2, 140, 100 + k * 25, 20 * k); }
  r.g.up(2, 140, 225, 200); r.g.up(1, 100, 225, 210);
  const scrolls = r.events.filter((e) => e.kind === "scroll");
  assert.ok(scrolls.length > 0 && scrolls.every((e) => e.dy > 0));
  assert.ok(!r.kinds().includes("move") && !r.kinds().includes("click:right"));
  const up = pad({ step: 20 }); up.g.down(1, 100, 300, 0); up.g.down(2, 140, 300, 5);
  for (let k = 1; k <= 4; k++) { up.g.move(1, 100, 300 - k * 25, 20 * k); up.g.move(2, 140, 300 - k * 25, 20 * k); }
  assert.ok(up.events.filter((e) => e.kind === "scroll").every((e) => e.dy < 0));
});

test("after a two-finger scroll, the finger left behind does nothing", () => {
  const r = pad(); r.g.down(1, 10, 10, 0); r.g.down(2, 50, 10, 5); r.g.move(1, 10, 90, 40); r.g.move(2, 50, 90, 40); r.g.up(2, 50, 90, 60);
  const before = r.events.length;
  r.g.move(1, 200, 200, 80); r.g.up(1, 200, 200, 120);
  assert.strictEqual(r.events.length, before);
});

test("holding one finger still shows a ring after a moment and brings up the mouse pad at two seconds", () => {
  const r = pad(); r.g.down(1, 60, 60, 0);
  assert.deepStrictEqual(r.timers.map((t) => t.ms).sort((a, b) => a - b), [450, 2000]);
  r.fire(450);  assert.deepStrictEqual(r.kinds(), ["hold-ring"]);
  r.fire(2000); assert.deepStrictEqual(r.kinds(), ["hold-ring", "hold"]);
  r.g.up(1, 60, 60, 2300);
  assert.deepStrictEqual(r.kinds(), ["hold-ring", "hold"]);               // letting go afterwards clicks nothing
});

test("moving before two seconds cancels the hold and takes the ring away", () => {
  const r = pad(); r.g.down(1, 60, 60, 0); r.fire(450); r.g.move(1, 100, 60, 700);
  assert.deepStrictEqual(r.kinds().slice(0, 2), ["hold-ring", "hold-cancel"]);
  assert.strictEqual(r.timers.length, 0); assert.ok(!r.kinds().includes("hold"));
});

test("lifting before two seconds cancels the hold (and a quick tap never shows a ring)", () => {
  const quick = pad(); quick.g.down(1, 60, 60, 0); quick.g.up(1, 60, 60, 100);
  assert.deepStrictEqual(quick.kinds(), ["click:left"]); assert.strictEqual(quick.timers.length, 0);
  const slow = pad(); slow.g.down(1, 60, 60, 0); slow.fire(450); slow.g.up(1, 60, 60, 600);
  assert.deepStrictEqual(slow.kinds().slice(0, 2), ["hold-ring", "hold-cancel"]);
});

test("a second finger during the hold cancels it", () => {
  const r = pad(); r.g.down(1, 60, 60, 0); r.fire(450); r.g.down(2, 100, 60, 800);
  assert.ok(r.kinds().includes("hold-cancel")); assert.strictEqual(r.timers.length, 0);
});

test("a system-cancelled touch clicks nothing, and reset forgets everything", () => {
  const r = pad(); r.g.down(1, 60, 60, 0); r.g.cancel(1, 60, 60, 50);
  assert.deepStrictEqual(r.kinds(), []);
  const q = pad(); q.g.down(1, 60, 60, 0); q.g.reset();
  assert.strictEqual(q.timers.length, 0); assert.strictEqual(q.g.mode(), "idle");
});

// ---------------------------------------------------------------- pointer speed and geometry

test("a quick swipe goes further than a slow one for the same distance, never backwards, and speed scales it", () => {
  const slow = S.accelerate(40, 0, 400, 1), fast = S.accelerate(40, 0, 40, 1), faster = S.accelerate(40, 0, 10, 1);
  assert.ok(slow.dx >= 40 && slow.dx < fast.dx && fast.dx < faster.dx);
  assert.ok(faster.dx <= 40 * 3.4 + 1e-9);                                 // capped: a flick does not fling the pointer across the screen five times
  assert.strictEqual(S.accelerate(-40, 0, 40, 1).dx, -fast.dx);           // symmetric
  assert.ok(Math.abs(S.accelerate(30, 40, 40, 2).dx - 2 * S.accelerate(30, 40, 40, 1).dx) < 1e-9);
  assert.strictEqual(S.accelerate(10, 10, 0, 1).dx > 0, true);            // a zero time step does not divide by zero
});

test("the pointer is kept on the screen", () => {
  assert.deepStrictEqual([-0.2, 0, 0.4, 1, 1.7].map(S.clamp01), [0, 0, 0.4, 1, 1]);
});

test("a point on the PC's screen and a place in the picture are each other's inverse", () => {
  const nat = { w: 1920, h: 1080 }, W = 400, H = 400;
  for (const [fx, fy] of [[0, 0], [1, 1], [0.5, 0.5], [0.25, 0.8]]) {
    const p = S.screenToLocal(fx, fy, W, H, nat);
    const back = S.pointToScreen(p.lx, p.ly, { left: 0, top: 0, width: W, height: H }, nat, false);
    assert.ok(Math.abs(back.x - fx) < 1e-9 && Math.abs(back.y - fy) < 1e-9, JSON.stringify([fx, fy, back]));
  }
});

test("the same holds when the picture is turned sideways on an upright phone", () => {
  const nat = { w: 1920, h: 960 }, rect = { left: 0, top: 0, width: 400, height: 800 };   // element 800 x 400, turned: it covers x 0..400, y 0..800 on the phone
  for (const [fx, fy] of [[0, 0], [1, 0], [0, 1], [0.3, 0.7], [0.5, 0.5]]) {
    const L = S.screenToLocal(fx, fy, 800, 400, nat);
    // where that local point is on the phone: x' = 400 - ly, y' = lx (a quarter turn clockwise, shifted back onto the screen)
    const cx = 400 - L.ly, cy = L.lx;
    const back = S.pointToScreen(cx, cy, rect, nat, true);
    assert.ok(back && Math.abs(back.x - fx) < 1e-9 && Math.abs(back.y - fy) < 1e-9, JSON.stringify([fx, fy, back]));
  }
});

test("a finger moving along the picture means the same movement whether or not the picture is turned", () => {
  // Turned: the picture's x axis runs DOWN the phone, so a finger moving down is moving right along the picture.
  const a = S.frameOf(100, 100, true), b = S.frameOf(100, 160, true);
  assert.deepStrictEqual([b.x - a.x, b.y - a.y], [60, 0]);
  const c = S.frameOf(100, 100, true), d = S.frameOf(40, 100, true);   // moving left on the phone is moving DOWN the picture when turned
  assert.deepStrictEqual([d.x - c.x, d.y - c.y], [0, 60]);
  const e = S.frameOf(10, 10, false), f = S.frameOf(30, 5, false);
  assert.deepStrictEqual([f.x - e.x, f.y - e.y], [20, -5]);
});

test("the picture's own box is found inside a taller or wider element", () => {
  const b = S.contentBox(400, 400, { w: 1920, h: 1080 });
  assert.ok(Math.abs(b.cw - 400) < 1e-9 && Math.abs(b.ch - 225) < 1e-9 && Math.abs(b.oy - 87.5) < 1e-9 && b.ox === 0);
  assert.strictEqual(S.screenToLocal(0.5, 0.5, 0, 0, { w: 1, h: 1 }), null);
});


// ---------------------------------------------------------------- Fit and Fill

test("Fit keeps the whole picture, Fill covers the whole element and crops what spills over", () => {
  const nat = { w: 1920, h: 1080 };                       // 16:9 PC screen
  const fit = S.contentBox(800, 400, nat, false), fill = S.contentBox(800, 400, nat, true);
  assert.ok(Math.abs(fit.ch - 400) < 1e-9 && fit.cw < 800 && fit.ox > 0);   // a 2:1 box: the 16:9 picture fits by height, with bars at the sides
  assert.ok(Math.abs(fill.cw - 800) < 1e-9 && fill.ch > 400 && fill.oy < 0);  // Fill fits by width and spills over the top and bottom
});

test("in Fit all of the PC's screen is visible; in Fill only the middle part is", () => {
  const nat = { w: 1920, h: 1080 };
  assert.deepStrictEqual(S.visibleRange(800, 400, nat, false), { x0: 0, x1: 1, y0: 0, y1: 1 });
  const v = S.visibleRange(800, 300, nat, true);   // a wide, short box: the 16:9 picture is cropped top and bottom
  assert.strictEqual(v.x0, 0); assert.strictEqual(v.x1, 1);
  assert.ok(Math.abs(v.y0 - 1 / 6) < 1e-9 && Math.abs(v.y1 - 5 / 6) < 1e-9);   // the middle two thirds, centred
});

test("a touch in a Fill picture lands on the part of the PC's screen you can see", () => {
  const nat = { w: 1920, h: 1080 }, rect = { left: 0, top: 0, width: 800, height: 300 };
  const top = S.pointToScreen(400, 0, rect, nat, false, true), bottom = S.pointToScreen(400, 300, rect, nat, false, true), mid = S.pointToScreen(400, 150, rect, nat, false, true);
  const v = S.visibleRange(800, 300, nat, true);
  assert.ok(Math.abs(top.y - v.y0) < 1e-9 && Math.abs(bottom.y - v.y1) < 1e-9 && Math.abs(mid.y - 0.5) < 1e-9);
  assert.strictEqual(S.pointToScreen(2, 150, { left: 0, top: 0, width: 800, height: 400 }, nat, false, false), null);   // in Fit, a taller box has no picture at its very edge: that is a bar
});

test("Fill and the pointer are each other's inverse too, also when turned sideways", () => {
  const nat = { w: 1920, h: 1080 };
  for (const [fx, fy] of [[0.5, 0.5], [0.2, 0.4], [0.9, 0.6]]) {
    const L = S.screenToLocal(fx, fy, 800, 400, nat, true);
    const back = S.pointToScreen(L.lx, L.ly, { left: 0, top: 0, width: 800, height: 400 }, nat, false, true);
    assert.ok(Math.abs(back.x - fx) < 1e-9 && Math.abs(back.y - fy) < 1e-9, JSON.stringify([fx, fy, back]));
    const cx = 400 - L.ly, cy = L.lx;   // turned a quarter turn on a 400 x 800 upright phone
    const rot = S.pointToScreen(cx, cy, { left: 0, top: 0, width: 400, height: 800 }, nat, true, true);
    assert.ok(Math.abs(rot.x - fx) < 1e-9 && Math.abs(rot.y - fy) < 1e-9, JSON.stringify([fx, fy, rot]));
  }
});

// ---------------------------------------------------------------- zooming

const near = (a, b, e = 1e-9) => assert.ok(Math.abs(a - b) < e, a + " vs " + b);

test("zooming keeps the point under the fingers exactly where it is on the phone", () => {
  const win = { x0: 0.2, y0: 0.1, w: 0.5 };
  const fx = 0.35, fy = 0.3;                            // a point of the PC's screen, inside the window
  const before = S.screenToWin(win, fx, fy), after = S.screenToWin(S.zoomWin(win, 1.6, fx, fy), fx, fy);
  near(before.u, after.u); near(before.v, after.v);
});

test("zoom stops at the limits, and the window never leaves the screen", () => {
  assert.strictEqual(S.zoomWin(S.FULL_WIN, 0.2, 0.5, 0.5).w, 1);                       // cannot zoom out past the whole screen
  near(S.zoomWin(S.FULL_WIN, 100, 0.5, 0.5).w, 1 / S.ZMAX);                            // nor in past the maximum
  const w = S.zoomWin({ x0: 0.7, y0: 0.7, w: 0.3 }, 2, 0.99, 0.99);
  assert.ok(w.x0 >= 0 && w.y0 >= 0 && w.x0 + w.w <= 1 + 1e-9 && w.y0 + w.w <= 1 + 1e-9);
  const p = S.panWin({ x0: 0.1, y0: 0.1, w: 0.4 }, -5, 5);
  assert.deepStrictEqual([p.x0, +(p.y0 + p.w).toFixed(9)], [0, 1]);
});

test("a place in the shown picture and a place on the PC's screen are each other's inverse", () => {
  const win = { x0: 0.3, y0: 0.2, w: 0.4 };
  const s = S.winToScreen(win, 0.25, 0.75), back = S.screenToWin(win, s.x, s.y);
  near(back.u, 0.25); near(back.v, 0.75);
  near(s.x, 0.3 + 0.25 * 0.4); near(s.y, 0.2 + 0.75 * 0.4);
});

test("the view follows the pointer near an edge, and leaves it alone in the middle", () => {
  const win = { x0: 0.3, y0: 0.3, w: 0.4 };
  assert.ok(S.sameWin(S.followWin(win, 0.5, 0.5, 0.12), win));
  const moved = S.followWin(win, 0.69, 0.5, 0.12);                 // near the right edge
  assert.ok(moved.x0 > win.x0 && moved.y0 === win.y0);
  const u = S.screenToWin(moved, 0.69, 0.5).u;
  assert.ok(u <= 1 - 0.12 + 1e-9);                                  // and now it is inside the margin
});

test("until the sharp picture arrives, the old one is scaled and moved to look like the new view", () => {
  const box = { ox: 0, oy: 0, cw: 800, ch: 450 };
  const have = { x0: 0, y0: 0, w: 1 }, want = { x0: 0.25, y0: 0.25, w: 0.5 };   // zoomed in on the middle
  const t = S.winTransform(have, want, box);
  near(t.a, 2);
  // the middle of the screen (0.5, 0.5) was at (400, 225); it must stay at the middle of the view after the transform
  near(t.a * 400 + t.tx, 400); near(t.a * 225 + t.ty, 225);
  // and a sharp picture that already shows the wanted part needs no change
  const same = S.winTransform(want, want, box);
  near(same.a, 1); near(same.tx, 0); near(same.ty, 0);
});

test("the stand-in also works in a box with bars (Fit) or a crop (Fill)", () => {
  const nat = { w: 1920, h: 1080 };
  for (const cover of [false, true]) {
    const box = S.contentBox(700, 400, nat, cover);
    const have = { x0: 0, y0: 0, w: 1 }, want = { x0: 0.5, y0: 0.2, w: 0.4 };
    const t = S.winTransform(have, want, box);
    // the screen point (0.7, 0.4) is inside the wanted window; it should land where the wanted picture would put it
    const u = (0.7 - want.x0) / want.w, v = (0.4 - want.y0) / want.w;
    const wantedX = box.ox + u * box.cw, wantedY = box.oy + v * box.ch;
    const nowX = box.ox + 0.7 * box.cw, nowY = box.oy + 0.4 * box.ch;
    near(t.a * nowX + t.tx, wantedX, 1e-6); near(t.a * nowY + t.ty, wantedY, 1e-6);
  }
});

// ---------------------------------------------------------------- two fingers: pinch or scroll?

function pinchRun(moves) {
  const out = [];
  const p = S.createPinch((g) => out.push(g));
  p.down(1, 100, 200); p.down(2, 200, 200);
  moves(p);
  return { out, p };
}

test("two fingers moving apart are a pinch that zooms in", () => {
  const { out } = pinchRun((p) => { p.move(1, 80, 200); p.move(2, 220, 200); p.move(1, 50, 200); p.move(2, 250, 200); });
  assert.strictEqual(out[0].kind, "start");
  const zooms = out.filter((g) => g.kind === "pinch");
  assert.ok(zooms.length >= 1 && zooms.every((g) => g.factor > 1));
});

test("two fingers coming together zoom out", () => {
  const { out } = pinchRun((p) => { p.move(1, 120, 200); p.move(2, 180, 200); p.move(1, 140, 200); p.move(2, 160, 200); });
  const zooms = out.filter((g) => g.kind === "pinch");
  assert.ok(zooms.length >= 1 && zooms.every((g) => g.factor < 1));
});

test("two fingers sliding together are a scroll: no pinch at all", () => {
  const { out, p } = pinchRun((q) => { for (let i = 1; i <= 8; i++) { q.move(1, 100, 200 + i * 12); q.move(2, 200, 200 + i * 12); } });
  assert.strictEqual(out.length, 0);
  assert.strictEqual(p.mode(), "other");
});

test("a pinch ends when a finger lifts, and the finger left does not start another", () => {
  const { out, p } = pinchRun((q) => { q.move(1, 70, 200); q.move(2, 230, 200); q.up(1); });
  assert.strictEqual(out[out.length - 1].kind, "end");
  assert.strictEqual(p.mode(), "done");
  p.up(2); assert.strictEqual(p.mode(), "idle");
});

test("moving both fingers the same way while pinched pans the view too", () => {
  const { out } = pinchRun((q) => { q.move(1, 70, 200); q.move(2, 230, 200); q.move(1, 90, 230); q.move(2, 250, 230); });
  const pans = out.filter((g) => g.kind === "pinch");
  assert.ok(pans.some((g) => g.dy > 0));
});

function stackedRun(moves) {   // one finger above the other (the line between them runs along the scroll)
  const out = [];
  const p = S.createPinch((g) => out.push(g));
  p.down(1, 150, 200); p.down(2, 150, 300);
  moves(p);
  return { out, p };
}

test("two fingers stacked one above the other and sliding down are a scroll, even in big steps", () => {
  for (const step of [6, 14, 30]) {
    const { out, p } = stackedRun((q) => { for (let i = 1; i <= 6; i++) { q.move(1, 150, 200 + i * step); q.move(2, 150, 300 + i * step); } });
    assert.strictEqual(out.length, 0, "step " + step);
    assert.strictEqual(p.mode(), "other", "step " + step);
  }
});

test("the same stacked fingers sliding up are a scroll too, whichever finger reports first", () => {
  const { out, p } = stackedRun((q) => { for (let i = 1; i <= 6; i++) { q.move(2, 150, 300 - i * 20); q.move(1, 150, 200 - i * 20); } });
  assert.strictEqual(out.length, 0);
  assert.strictEqual(p.mode(), "other");
});

test("stacked fingers moving apart or together are still a pinch", () => {
  const apart = stackedRun((q) => { q.move(1, 150, 180); q.move(2, 150, 320); q.move(1, 150, 150); q.move(2, 150, 350); });
  assert.strictEqual(apart.out[0].kind, "start");
  assert.ok(apart.out.filter((g) => g.kind === "pinch").every((g) => g.factor > 1));
  const together = stackedRun((q) => { q.move(1, 150, 220); q.move(2, 150, 280); q.move(1, 150, 240); q.move(2, 150, 260); });
  assert.strictEqual(together.out[0].kind, "start");
  assert.ok(together.out.filter((g) => g.kind === "pinch").every((g) => g.factor < 1));
});

test("one finger staying put while the other goes far is a pinch around it", () => {
  const { out } = pinchRun((q) => { for (let i = 1; i <= 8; i++) q.move(2, 200 + i * 12, 200); });
  assert.strictEqual(out[0].kind, "start");
});

test("one finger drifting a little while the other rests is neither", () => {
  const { out, p } = pinchRun((q) => { q.move(1, 100, 212); q.move(1, 100, 224); });
  assert.strictEqual(out.length, 0);
  assert.strictEqual(p.mode(), "deciding");
});

test("a tiny wobble of two resting fingers is neither", () => {
  const { out } = pinchRun((q) => { q.move(1, 101, 201); q.move(2, 199, 199); q.move(1, 100, 200); });
  assert.strictEqual(out.length, 0);
});

// ---- a phone much wider than the PC's screen: the window has the phone's shape, so zoomed in there are no bars
const WIDE = { k: (16 / 9) / (1000 / 420), fill: false };   // the PC screen's shape over the phone's shape (< 1: the phone is wider)
const TALL = { k: (16 / 9) / 0.5, fill: false };

test("zoomed out, the window shows the whole screen (Fit, with bars) or covers the whole box (Fill)", () => {
  near(S.fullW(WIDE), 1 / WIDE.k); near(S.fullW({ k: WIDE.k, fill: true }), 1);
  near(S.fullW(TALL), 1); near(S.fullW({ k: TALL.k, fill: true }), 1 / TALL.k);
  near(S.fullW(), 1);                                          // the default frame is the old, screen-shaped one
  const full = S.fullWin(WIDE);
  near(full.x0, (1 - full.w) / 2); near(full.y0, 0);           // spills over the sides equally: those are the bars
});

test("zoomed in on a wide phone the window is wide, inside the screen, and as tall as the phone needs", () => {
  const w = S.zoomWin(S.fullWin(WIDE), 3, 0.5, 0.5, WIDE);
  near(w.w, S.fullW(WIDE) / 3);
  near(S.winH(w, WIDE), w.w * WIDE.k);
  const r = S.seenRect(w, WIDE);
  assert.ok(r.x0 >= 0 && r.x1 <= 1 && r.y0 >= 0 && r.y1 <= 1);
  near(((r.x1 - r.x0) * 1920) / ((r.y1 - r.y0) * 1080), 1000 / 420, 1e-6);   // shaped like the phone
  const edge = S.panWin(w, -5, 5, WIDE), er = S.seenRect(edge, WIDE);
  assert.ok(er.x0 >= 0 && er.y1 <= 1 + 1e-9, "panning cannot leave the screen");
  near(er.y1, 1);                                                // and the bottom of the screen (the taskbar) can be reached
});

test("what is under the fingers stays under them while zooming, also with a different shaped box", () => {
  const win = S.zoomWin(S.fullWin(WIDE), 2, 0.4, 0.4, WIDE), fx = 0.45, fy = 0.35;
  const before = S.screenToWin(win, fx, fy, WIDE), after = S.screenToWin(S.zoomWin(win, 1.7, fx, fy, WIDE), fx, fy, WIDE);
  near(before.u, after.u, 1e-6); near(before.v, after.v, 1e-6);
});

test("the stand-in picture is moved and scaled to look like the wanted window, bars and all", () => {
  const W = 1000, H = 420, whole = { x0: 0, y0: 0, x1: 1, y1: 1 }, b = S.contentBox(W, H, { w: 1920, h: 1080 }, false);
  const idle = S.windowTransform(whole, b, S.fullWin(WIDE), W, H, WIDE);
  near(idle.a, 1); near(idle.tx, 0); near(idle.ty, 0);                       // the picture already shows the window: nothing to do
  const zoomed = S.zoomWin(S.fullWin(WIDE), 2, 0.5, 0.5, WIDE), t = S.windowTransform(whole, b, zoomed, W, H, WIDE);
  near(t.a, 2, 1e-6);                                                         // twice as close = twice as big
  const centre = { x: b.ox + 0.5 * b.cw, y: b.oy + 0.5 * b.ch };              // the middle of the screen stays in the middle of the box
  near(t.a * centre.x + t.tx, W / 2, 1e-6); near(t.a * centre.y + t.ty, H / 2, 1e-6);
});

// ---- the PC's sound
test("the PC's sound bytes become the floats a browser plays (16-bit little endian)", () => {
  const bytes = new Uint8Array([0x00, 0x00, 0xFF, 0x7F, 0x00, 0x80, 0x00, 0x40]);   // 0, 32767, -32768, 16384
  const f = S.pcmToFloat(bytes);
  assert.strictEqual(f.length, 4);
  near(f[0], 0); near(f[1], 32767 / 32768); near(f[2], -1); near(f[3], 0.5);
  const shifted = S.pcmToFloat(new Uint8Array([9, 0x00, 0x40, 0x00, 0xC0]).subarray(1));   // a view that does not start at the start of its buffer
  near(shifted[0], 0.5); near(shifted[1], -0.5);
});

test("sound pieces follow each other without gaps, start with a little in hand, and drop when far behind", () => {
  const first = S.planSound(10, 0, 0.1, 0.3, 1.2);
  near(first.start, 10.3); near(first.next, 10.4); assert.strictEqual(first.drop, false);   // the first piece waits a little: a hiccup does not make a gap
  const second = S.planSound(10.05, first.next, 0.1, 0.3, 1.2);
  near(second.start, first.next); near(second.next, first.next + 0.1);                       // the next one starts exactly where the last ended
  const late = S.planSound(20, 10.4, 0.1, 0.3, 1.2);                                         // the audio clock ran past: start again from now, with a little in hand
  near(late.start, 20.3);
  const far = S.planSound(10, 12, 0.1, 0.3, 1.2);                                            // two seconds queued up: drop this piece, stay live
  assert.strictEqual(far.drop, true); near(far.next, 12);
});

// ---------------------------------------------------------------- a computer's keyboard and wheel

const key = (k, extra) => Object.assign({ key: k, ctrlKey: false, altKey: false, shiftKey: false, metaKey: false, getModifierState: () => false }, extra || {});

test("a plain letter, capital or symbol is typed as pressed", () => {
  assert.deepStrictEqual(S.mapKey(key("a")), { t: "text", text: "a" });
  assert.deepStrictEqual(S.mapKey(key("A", { shiftKey: true })), { t: "text", text: "A" });
  assert.deepStrictEqual(S.mapKey(key("!", { shiftKey: true })), { t: "text", text: "!" });
  assert.deepStrictEqual(S.mapKey(key("é")), { t: "text", text: "é" });
});

test("with Ctrl, Alt or the Windows key it is a shortcut", () => {
  assert.deepStrictEqual(S.mapKey(key("c", { ctrlKey: true })), { t: "key", key: "c", mods: ["ctrl"] });
  assert.deepStrictEqual(S.mapKey(key("T", { ctrlKey: true, shiftKey: true })), { t: "key", key: "t", mods: ["ctrl", "shift"] });
  assert.deepStrictEqual(S.mapKey(key("d", { metaKey: true })), { t: "key", key: "d", mods: ["win"] });
  assert.deepStrictEqual(S.mapKey(key("Tab", { altKey: true })), { t: "key", key: "tab", mods: ["alt"] });
});

test("named keys use the PC's names, and Shift counts with them", () => {
  assert.deepStrictEqual(S.mapKey(key("Enter")), { t: "key", key: "enter", mods: [] });
  assert.deepStrictEqual(S.mapKey(key("Escape")), { t: "key", key: "esc", mods: [] });
  assert.deepStrictEqual(S.mapKey(key("ArrowLeft", { shiftKey: true })), { t: "key", key: "left", mods: ["shift"] });
  assert.deepStrictEqual(S.mapKey(key("F5")), { t: "key", key: "f5", mods: [] });
  assert.deepStrictEqual(S.mapKey(key(" ")), { t: "key", key: "space", mods: [] });
  assert.deepStrictEqual(S.mapKey(key("Backspace")), { t: "key", key: "backspace", mods: [] });
});

test("a modifier alone, and keys the PC has no word for, send nothing", () => {
  for (const k of ["Control", "Shift", "Alt", "Meta", "AltGraph", "Dead", "Unidentified", "F13", "MediaPlayPause"]) assert.strictEqual(S.mapKey(key(k)), null, k);
});

test("AltGr (Ctrl+Alt on Windows) types its character instead of sending a shortcut", () => {
  const e = key("@", { ctrlKey: true, altKey: true, getModifierState: (m) => m === "AltGraph" });
  assert.deepStrictEqual(S.mapKey(e), { t: "text", text: "@" });
});

test("every key name sent exists on the PC's side", () => {
  const fs = require("fs");
  const py = fs.readFileSync(require("path").join(__dirname, "../../src/handoff/screen/inject.py"), "utf-8");
  for (const name of Object.values(S.KEYNAMES)) assert.ok(new RegExp('"' + name + '":').test(py), name + " is not a key the PC knows");
});

test("a wheel notch is one notch, a touchpad's small slides add up, and up is positive", () => {
  assert.deepStrictEqual(S.wheelStep(0, 100, 0), { acc: 0, notches: -1 });   // wheel turned down: scroll down
  assert.deepStrictEqual(S.wheelStep(0, -100, 0), { acc: 0, notches: 1 });   // wheel turned up
  let acc = 0, total = 0;
  for (let i = 0; i < 25; i++) { const r = S.wheelStep(acc, 8, 0); acc = r.acc; total += r.notches; }   // 200 px of slow touchpad slides
  assert.strictEqual(total, -2);
  assert.strictEqual(S.wheelStep(0, 3, 1).notches, -1);                       // 3 lines
  assert.ok(Math.abs(S.wheelStep(0, 100000, 0).notches) <= 10);               // never a flood
});

let failed = 0;
for (const [name, ok, e] of run) { console.log((ok ? "ok   " : "FAIL ") + name); if (!ok) { failed++; console.log("     " + (e && e.message)); } }
console.log(`\n${run.length - failed} of ${run.length} passed`);
process.exit(failed ? 1 : 0);
