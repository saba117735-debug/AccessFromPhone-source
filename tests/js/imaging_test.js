// The picture viewer/editor's maths, every rule on its own. Run with:  node tests/js/imaging_test.js   (pytest runs it too)
const assert = require("assert");
const C = require("../../src/handoff/web/imaging-core.js");

const results = [];
function test(name, fn) { try { fn(); results.push([name, true]); } catch (e) { results.push([name, false, e]); } }
const near = (a, b, eps = 0.01) => assert.ok(Math.abs(a - b) <= eps, `${a} is not near ${b}`);
const rectNear = (r, x, y, w, h, eps = 0.01) => { near(r.x, x, eps); near(r.y, y, eps); near(r.w, w, eps); near(r.h, h, eps); };
const px = (r, g, b, a = 255) => new Uint8ClampedArray([r, g, b, a]);

/* ---- colour ---- */
test("neutral settings leave every pixel alone", () => {
  const d = px(12, 130, 250); C.applyAdjust(d, C.params());
  assert.deepStrictEqual([...d], [12, 130, 250, 255]);
  assert.ok(C.isNeutral(C.params()) && !C.isNeutral(C.params({ contrast: 1 })));
});
test("brightness lightens and darkens, and never leaves 0..255", () => {
  const up = px(100, 100, 100); C.applyAdjust(up, C.params({ brightness: 20 })); assert.ok(up[0] > 100);
  const down = px(100, 100, 100); C.applyAdjust(down, C.params({ brightness: -20 })); assert.ok(down[0] < 100);
  const hi = px(250, 250, 250); C.applyAdjust(hi, C.params({ brightness: 100 })); assert.strictEqual(hi[0], 255);
  const lo = px(5, 5, 5); C.applyAdjust(lo, C.params({ brightness: -100 })); assert.strictEqual(lo[0], 0);
});
test("contrast pushes light things lighter and dark things darker, and leaves mid grey", () => {
  const light = px(180, 180, 180), dark = px(70, 70, 70), mid = px(128, 128, 128);
  for (const d of [light, dark, mid]) C.applyAdjust(d, C.params({ contrast: 40 }));
  assert.ok(light[0] > 180 && dark[0] < 70); near(mid[0], 128, 1);
});
test("saturation -100 makes grey, +100 makes colour stronger", () => {
  const grey = px(200, 50, 50); C.applyAdjust(grey, C.params({ saturation: -100 }));
  assert.ok(Math.abs(grey[0] - grey[1]) <= 1 && Math.abs(grey[1] - grey[2]) <= 1);
  const strong = px(200, 100, 100); C.applyAdjust(strong, C.params({ saturation: 100 }));
  assert.ok(strong[0] - strong[1] > 100);
});
test("warmth adds red and takes away blue (and the reverse for cool)", () => {
  const warm = px(100, 100, 100); C.applyAdjust(warm, C.params({ warmth: 50 })); assert.ok(warm[0] > 100 && warm[2] < 100);
  const cool = px(100, 100, 100); C.applyAdjust(cool, C.params({ warmth: -50 })); assert.ok(cool[0] < 100 && cool[2] > 100);
});
test("grey 1 gives equal channels, sepia gives brown", () => {
  const g = px(220, 40, 90); C.applyAdjust(g, C.params({ gray: 1 })); assert.ok(g[0] === g[1] && g[1] === g[2]);
  const s = px(100, 100, 100); C.applyAdjust(s, C.params({ sepia: 1 })); assert.ok(s[0] > s[1] && s[1] > s[2]);
});
test("the see-through (alpha) channel is never touched", () => {
  const d = px(100, 100, 100, 77); C.applyAdjust(d, C.params({ brightness: 50, sepia: 1, gray: 0.5 })); assert.strictEqual(d[3], 77);
});
test("every filter is made of the same sliders, and 'Original' changes nothing", () => {
  for (const p of C.PRESETS) for (const k of Object.keys(p.p)) assert.ok(k in C.NEUTRAL, `${p.id}.${k}`);
  assert.ok(C.isNeutral(C.params(C.PRESETS.find((p) => p.id === "none").p)));
  assert.ok(C.PRESETS.length >= 8);
});

/* ---- viewing ---- */
test("a picture is fitted to the box without being cut", () => {
  near(C.fitScale(2000, 1000, 400, 400), 0.2);
  const v = C.fitView(2000, 1000, 400, 400);
  near(v.scale, 0.2); near(v.tx, 0); near(v.ty, 100);   // centred up and down
});
test("a picture smaller than the box stays centred, a bigger one can't be dragged out of view", () => {
  const small = C.clampView({ scale: 1, tx: 500, ty: -300 }, 100, 100, 400, 400);
  near(small.tx, 150); near(small.ty, 150);
  const big = C.clampView({ scale: 2, tx: 500, ty: 500 }, 400, 400, 400, 400);
  near(big.tx, 0); near(big.ty, 0);
  const far = C.clampView({ scale: 2, tx: -9999, ty: -9999 }, 400, 400, 400, 400);
  near(far.tx, -400); near(far.ty, -400);
});
test("zooming keeps the point under the finger where it is", () => {
  const before = { scale: 1, tx: 10, ty: 20 };
  const spot = C.toContent(before, 150, 160);
  const after = C.zoomAt(before, 2.5, 150, 160, 0.1, 10);
  const back = C.toScreen(after, spot.x, spot.y);
  near(back.x, 150); near(back.y, 160); near(after.scale, 2.5);
});
test("zoom stops at its limits", () => {
  near(C.zoomAt({ scale: 1, tx: 0, ty: 0 }, 100, 0, 0, 0.5, 8).scale, 8);
  near(C.zoomAt({ scale: 1, tx: 0, ty: 0 }, 0.001, 0, 0, 0.5, 8).scale, 0.5);
});

/* ---- crop ---- */
const B = { w: 400, h: 300 };
test("crop shapes: free has none, 'original' keeps the picture's, flipping turns landscape into portrait", () => {
  assert.strictEqual(C.aspectRatio("free", 400, 300, false), null);
  near(C.aspectRatio("orig", 400, 300, false), 4 / 3);
  near(C.aspectRatio("1:1", 400, 300, false), 1);
  near(C.aspectRatio("16:9", 400, 300, false), 16 / 9);
  near(C.aspectRatio("16:9", 400, 300, true), 9 / 16);
});
test("a free crop moves its own edges and nothing else", () => {
  rectNear(C.dragCrop({ x: 100, y: 100, w: 100, h: 100 }, "e", 30, 999, B, null), 100, 100, 130, 100);
  rectNear(C.dragCrop({ x: 100, y: 100, w: 100, h: 100 }, "nw", -20, -30, B, null), 80, 70, 120, 130);
});
test("a crop can't leave the picture or become tiny", () => {
  rectNear(C.dragCrop({ x: 100, y: 100, w: 100, h: 100 }, "se", 9999, 9999, B, null), 100, 100, 300, 200);
  rectNear(C.dragCrop({ x: 100, y: 100, w: 100, h: 100 }, "nw", -9999, -9999, B, null), 0, 0, 200, 200);
  const tiny = C.dragCrop({ x: 100, y: 100, w: 100, h: 100 }, "e", -9999, 0, B, null, 24);
  near(tiny.w, 24);
});
test("moving a crop keeps its size and stays inside", () => {
  rectNear(C.dragCrop({ x: 100, y: 100, w: 100, h: 100 }, "move", 50, -20, B, null), 150, 80, 100, 100);
  rectNear(C.dragCrop({ x: 100, y: 100, w: 100, h: 100 }, "move", 9999, 9999, B, null), 300, 200, 100, 100);
  rectNear(C.dragCrop({ x: 100, y: 100, w: 100, h: 100 }, "move", -9999, -9999, B, null), 0, 0, 100, 100);
});
test("a square crop stays square when a corner is dragged, with the opposite corner fixed", () => {
  const r = C.dragCrop({ x: 50, y: 50, w: 100, h: 100 }, "se", 40, 10, B, 1);
  near(r.w, r.h); near(r.x, 50); near(r.y, 50); assert.ok(r.w > 100);
  const nw = C.dragCrop({ x: 100, y: 100, w: 100, h: 100 }, "nw", -30, -5, B, 1);
  near(nw.w, nw.h); near(nw.x + nw.w, 200); near(nw.y + nw.h, 200);
});
test("an edge drag with a fixed shape changes both sides to match", () => {
  const r = C.dragCrop({ x: 50, y: 50, w: 120, h: 90 }, "e", 40, 0, B, 4 / 3);
  near(r.w / r.h, 4 / 3, 0.01); near(r.x, 50); near(r.w, 160, 1);
});
test("a fixed-shape crop never leaves the picture, and keeps its shape when it hits the edge", () => {
  const r = C.dragCrop({ x: 250, y: 150, w: 100, h: 100 }, "se", 500, 500, B, 1);
  assert.ok(r.x + r.w <= B.w + 0.01 && r.y + r.h <= B.h + 0.01); near(r.w, r.h);
  const wide = C.dragCrop({ x: 0, y: 0, w: 160, h: 90 }, "se", 9999, 0, B, 16 / 9);
  near(wide.w / wide.h, 16 / 9, 0.01); assert.ok(wide.w <= B.w + 0.01 && wide.h <= B.h + 0.01);
});
test("choosing a shape fits the biggest one of that shape around the old centre", () => {
  const r = C.fitAspect({ x: 0, y: 0, w: 400, h: 300 }, 1, B);
  near(r.w, r.h); near(r.w, 300); near(r.x + r.w / 2, 200);
  const keep = C.fitAspect({ x: 10, y: 10, w: 50, h: 40 }, null, B);
  rectNear(keep, 10, 10, 50, 40);
});
test("the right crop handle is found, edges and corners first, then the inside", () => {
  const r = { x: 100, y: 100, w: 200, h: 100 };
  const at = (x, y) => C.cropHandleAt(r, x, y, 12);
  assert.strictEqual(at(100, 100), "nw"); assert.strictEqual(at(300, 200), "se"); assert.strictEqual(at(300, 100), "ne"); assert.strictEqual(at(100, 200), "sw");
  assert.strictEqual(at(200, 100), "n"); assert.strictEqual(at(200, 200), "s"); assert.strictEqual(at(100, 150), "w"); assert.strictEqual(at(300, 150), "e");
  assert.strictEqual(at(200, 150), "move"); assert.strictEqual(at(10, 10), null);
});

/* ---- annotations follow the picture ---- */
const pen = { type: "pen", pts: [[10, 20], [30, 40]], color: "#f00", size: 6 };
const box = { type: "rect", x: 10, y: 20, w: 30, h: 40, color: "#0f0", size: 4, fill: false };
const arrow = { type: "arrow", x1: 10, y1: 20, x2: 60, y2: 80, color: "#00f", size: 5 };
test("cropping moves everything by the crop's corner", () => {
  const [p, b, a] = C.transformObjects([pen, box, arrow], { kind: "crop", x: 5, y: 10 });
  assert.deepStrictEqual(p.pts, [[5, 10], [25, 30]]); rectNear(b, 5, 10, 30, 40); near(a.x2, 55); near(a.y2, 70);
});
test("turning a picture clockwise turns what is drawn on it, and turning back puts it all home", () => {
  const w = 200, h = 100;
  const cw = C.transformObjects([box], { kind: "rotate", dir: 90, w, h })[0];
  rectNear(cw, 40, 10, 40, 30);   // (10,20)-(40,60) -> corners (80,10) and (40,40)
  const back = C.transformObjects([cw], { kind: "rotate", dir: -90, w: h, h: w })[0];
  rectNear(back, 10, 20, 30, 40);
  const pts = C.transformObjects([pen], { kind: "rotate", dir: 90, w, h })[0].pts;
  assert.deepStrictEqual(pts, [[80, 10], [60, 30]]);
});
test("four quarter turns, or two half turns, change nothing", () => {
  let objs = [pen, box, arrow], w = 200, h = 100;
  for (let i = 0; i < 4; i++) { objs = C.transformObjects(objs, { kind: "rotate", dir: 90, w, h }); [w, h] = [h, w]; }
  assert.deepStrictEqual(objs.map((o) => Object.assign({}, o)), [pen, box, arrow].map((o) => Object.assign({}, o)));
  const half = C.transformObjects(C.transformObjects([box], { kind: "rotate", dir: 180, w: 200, h: 100 }), { kind: "rotate", dir: 180, w: 200, h: 100 })[0];
  rectNear(half, 10, 20, 30, 40);
});
test("flipping mirrors positions, and flipping twice is the identity", () => {
  const f = C.transformObjects([box], { kind: "flip", axis: "h", w: 200, h: 100 })[0];
  rectNear(f, 160, 20, 30, 40);
  rectNear(C.transformObjects([f], { kind: "flip", axis: "h", w: 200, h: 100 })[0], 10, 20, 30, 40);
  rectNear(C.transformObjects([box], { kind: "flip", axis: "v", w: 200, h: 100 })[0], 10, 40, 30, 40);
});
test("resizing scales positions and sizes together", () => {
  const [p, b] = C.transformObjects([pen, box], { kind: "scale", k: 0.5 });
  assert.deepStrictEqual(p.pts, [[5, 10], [15, 20]]); near(p.size, 3); rectNear(b, 5, 10, 15, 20); near(b.size, 2);
});
test("text stays upright and keeps its centre when the picture turns", () => {
  const t = { type: "text", x: 20, y: 10, w: 60, h: 20, text: "Hi", size: 16, color: "#fff" };
  const r = C.transformObjects([t], { kind: "rotate", dir: 90, w: 200, h: 100 })[0];
  assert.ok(r.text === "Hi" && r.size === 16);
  near(r.x + r.w / 2, 100 - (10 + 10)); near(r.y + r.h / 2, 20 + 30);   // the centre (50,20) -> (80,50)
  assert.strictEqual(r.w, 20); assert.strictEqual(r.h, 60);             // its measured box swaps (it is redrawn upright)
});
test("changing a copy never changes the original objects", () => {
  const before = JSON.stringify([pen, box]);
  C.transformObjects([pen, box], { kind: "crop", x: 5, y: 5 });
  C.transformObjects([pen, box], { kind: "rotate", dir: 90, w: 50, h: 50 });
  assert.strictEqual(JSON.stringify([pen, box]), before);
});

/* ---- selecting and moving ---- */
test("a tap finds the topmost thing under it, strokes by distance and boxes by area", () => {
  const objs = [box, pen];
  assert.strictEqual(C.hitTest(objs, 20, 30, 4), 1);           // on the pen stroke, which is drawn above the box
  assert.strictEqual(C.hitTest(objs, 35, 55, 2), 0);           // inside the box, away from the stroke
  assert.strictEqual(C.hitTest([box, pen, arrow], 20, 30, 4), 2);  // the arrow is on top of both there: the topmost wins
  assert.strictEqual(C.hitTest([arrow], 35, 50, 2), 0);        // on the arrow
  assert.strictEqual(C.hitTest(objs, 300, 300, 4), -1);
  assert.strictEqual(C.hitTest([], 1, 1, 4), -1);
});
test("a finger a little off still finds a thin line", () => {
  const line = { type: "line", x1: 0, y1: 0, x2: 100, y2: 0, size: 2, color: "#000" };
  assert.strictEqual(C.hitTest([line], 50, 6, 8), 0);
  assert.strictEqual(C.hitTest([line], 50, 30, 8), -1);
});
test("moving an object moves all of its points", () => {
  assert.deepStrictEqual(C.moveObject(pen, 5, -5).pts, [[15, 15], [35, 35]]);
  const a = C.moveObject(arrow, 1, 2); assert.deepStrictEqual([a.x1, a.y1, a.x2, a.y2], [11, 22, 61, 82]);
  const b = C.moveObject(box, -10, 0); assert.strictEqual(b.x, 0);
  assert.strictEqual(pen.pts[0][0], 10);  // the original is untouched
});
test("a box's corner can be grabbed and dragged with the opposite corner staying put", () => {
  assert.strictEqual(C.cornerAt(box, 41, 61, 8), "se"); assert.strictEqual(C.cornerAt(box, 25, 40, 8), null);
  rectNear(C.dragCorner(box, "se", 20, 10), 10, 20, 50, 50);
  rectNear(C.dragCorner(box, "nw", 5, 5), 15, 25, 25, 35);
  rectNear(C.dragCorner(box, "se", -50, -50), -10, 10, 20, 10);    // dragged past the other corner: it turns over, still a proper box
});
test("a stroke drawn with a shaky finger is thinned, but keeps its ends", () => {
  const raw = Array.from({ length: 50 }, (_, i) => [i * 0.5, 0]);
  const out = C.thin(raw, 3);
  assert.ok(out.length < raw.length && out.length >= 3); assert.deepStrictEqual(out[0], raw[0]); assert.deepStrictEqual(out[out.length - 1], raw[raw.length - 1]);
  assert.deepStrictEqual(C.thin([[1, 1]], 3), [[1, 1]]);
});

/* ---- names, sizes, words ---- */
test("an edited copy is named after the original, once", () => {
  assert.strictEqual(C.editedName("photo.png"), "photo (edited).png");
  assert.strictEqual(C.editedName("photo (edited).png"), "photo (edited).png");
  assert.strictEqual(C.editedName("photo (edited 3).jpg"), "photo (edited).jpg");
  assert.strictEqual(C.editedName("holiday.photo.JPG", "webp"), "holiday.photo (edited).webp");
  assert.strictEqual(C.editedName("noext"), "noext (edited).png");
});
test("file kinds: jpg and jpeg are the same, others differ", () => {
  assert.ok(C.sameKind("jpg", "jpeg") && C.sameKind("png", "png") && !C.sameKind("png", "jpg"));
  assert.strictEqual(C.extOf("A.B.PNG"), "png"); assert.strictEqual(C.extOf("none"), "");
});
test("a picture over the pixel budget is scaled down in shape, one under it is left alone", () => {
  const s = C.capSize(6000, 4000, 16e6);
  assert.ok(s.scaled && s.w * s.h <= 16e6 && Math.abs(s.w / s.h - 1.5) < 0.01);
  assert.deepStrictEqual(C.capSize(800, 600, 16e6), { w: 800, h: 600, scaled: false });
});
test("the record of what was done reads like a sentence", () => {
  assert.strictEqual(C.describeEdits(["crop", "rotate", "text", "text", "arrow"]), "cropped, rotated, 2 text notes, arrow");
  assert.strictEqual(C.describeEdits(["blur"]), "hidden area");
  assert.strictEqual(C.describeEdits(["pen", "pen", "pen"]), "3 drawings");
  assert.strictEqual(C.describeEdits([]), "");
  assert.strictEqual(C.describeEdits(["mystery"]), "");
});
test("sizes are written for people", () => {
  assert.strictEqual(C.fmtBytes(500), "500 B"); assert.strictEqual(C.fmtBytes(1536), "1.5 KB"); assert.strictEqual(C.fmtBytes(5 * 1048576), "5.0 MB");
});

/* ---- undo and redo ---- */
test("undo and redo walk through the states, and a new change drops the redo path", () => {
  const h = C.History(5); h.reset("a"); h.push("b"); h.push("c");
  assert.strictEqual(h.undo(), "b"); assert.strictEqual(h.undo(), "a"); assert.ok(!h.canUndo() && h.canRedo());
  assert.strictEqual(h.undo(), "a");                       // can't go before the start
  assert.strictEqual(h.redo(), "b");
  h.push("x");                                              // a different future: the old "c" is gone
  assert.ok(!h.canRedo()); assert.strictEqual(h.undo(), "b"); assert.strictEqual(h.redo(), "x");
});
test("history keeps only the newest few states", () => {
  const h = C.History(3); h.reset(0); for (let i = 1; i <= 6; i++) h.push(i);
  assert.strictEqual(h.current(), 6); h.undo(); h.undo(); assert.strictEqual(h.current(), 4); assert.ok(!h.canUndo());
});

let failed = 0;
for (const [name, ok, e] of results) { console.log((ok ? "ok   " : "FAIL ") + name); if (!ok) { failed++; console.log("     " + (e && e.message)); } }
console.log(`\n${results.length - failed} of ${results.length} passed`);
process.exit(failed ? 1 : 0);
