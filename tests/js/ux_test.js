// The page's gesture rules: when a swipe deletes, how a pull stretches, what the empty-state pictures are. Run with:  node tests/js/ux_test.js   (pytest runs it too)
const assert = require("assert");
const U = require("../../src/handoff/web/ux.js");

const run = [];
function test(name, fn) { try { fn(); run.push([name, true]); } catch (e) { run.push([name, false, e]); } }

test("a rubber band never goes backwards or past its limit", () => {
  assert.strictEqual(U.resist(-5, 100), 0);
  assert.strictEqual(U.resist(0, 100), 0);
  assert.strictEqual(U.resist(NaN, 100), 0);
  let last = 0;
  for (let d = 1; d <= 5000; d += 37) { const r = U.resist(d, 100); assert.ok(r > last, "grows"); assert.ok(r < 100, "stays under the limit"); last = r; }
});

test("a small pull follows the finger at about half speed, a long one gets harder", () => {
  assert.ok(Math.abs(U.resist(10, 100) - 5.2) < 0.5);
  assert.ok(U.resist(400, 100) - U.resist(300, 100) < U.resist(100, 100) - U.resist(0, 100));
});

test("a short slow drag closes the row, a longer one opens it, a long one deletes", () => {
  assert.strictEqual(U.swipeTarget(-10, -0.1, 320), "close");
  assert.strictEqual(U.swipeTarget(-43, -0.1, 320), "close");
  assert.strictEqual(U.swipeTarget(-44, -0.1, 320), "open");
  assert.strictEqual(U.swipeTarget(-88, 0, 320), "open");
  assert.strictEqual(U.swipeTarget(-159, 0, 320), "open");
  assert.strictEqual(U.swipeTarget(-160, 0, 320), "delete");
  assert.strictEqual(U.swipeTarget(-300, 0, 320), "delete");
});

test("a hard flick left deletes even from a short drag, a flick right always closes", () => {
  assert.strictEqual(U.swipeTarget(-60, -1.2, 320), "delete");
  assert.strictEqual(U.swipeTarget(-20, -1.2, 320), "close");   // too little to mean it
  assert.strictEqual(U.swipeTarget(-150, 0.9, 320), "close");
});

test("the row follows the finger to the open position, then resists, and never moves right of rest", () => {
  assert.strictEqual(U.swipeOffset(0, -50, 320), -50);
  assert.strictEqual(U.swipeOffset(0, -88, 320), -88);
  assert.ok(U.swipeOffset(0, -200, 320) < -88 && U.swipeOffset(0, -200, 320) > -200);
  assert.ok(U.swipeOffset(0, -2000, 320) >= -320);
  assert.strictEqual(U.swipeTarget(U.swipeOffset(0, -216, 320), 0, 320), "delete");   // a drag across two thirds of the row, with no flick, deletes
  assert.strictEqual(U.swipeTarget(U.swipeOffset(0, -140, 320), 0, 320), "open");       // a drag to a bit under half of it only opens
  assert.strictEqual(U.swipeOffset(0, 40, 320), 0);
  assert.strictEqual(U.swipeOffset(-88, 30, 320), -58);   // dragging an open row back
  assert.strictEqual(U.swipeOffset(-88, 200, 320), 0);
});

test("a pull counts only once it has gone far enough", () => {
  assert.strictEqual(U.pullState(0).ready, false);
  assert.strictEqual(U.pullState(-40).offset, 0);
  assert.strictEqual(U.pullState(30).ready, false);
  assert.strictEqual(U.pullState(70).ready, false);
  assert.strictEqual(U.pullState(90).ready, true);
  assert.strictEqual(U.pullState(200).ready, true);
  const a = U.pullState(30), b = U.pullState(60);
  assert.ok(a.progress < b.progress && b.progress <= 1);
  assert.ok(U.pullState(5000).offset < 120);
});

test("there is a picture for each empty state, and a fallback", () => {
  ["inbox", "notes", "remind", "watch", "recent", "print", "snap", "brief", "done"].forEach((n) => assert.ok(U.arts().includes(n), n));
});

test("nothing that touches the page runs without one (so loading this file in Node is safe)", () => {
  assert.strictEqual(U.burst(), null);
  U.spot();
  let done = 0;   // with no page to paint on, the page must still switch, and say it is done
  U.paint({ onCovered: () => { done += 1; }, onDone: () => { done += 10; } });
  assert.strictEqual(done, 11);
});

/* ---- the oil paint that opens Chat ---- */

const distToSegment = (p, a, b) => {
  const dx = b[0] - a[0], dy = b[1] - a[1], len2 = dx * dx + dy * dy || 1;
  const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len2));
  return Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy));
};

const STYLES = ["waves", "scan", "stack", "swirl"];
const SIZES = [[390, 844], [320, 568], [412, 915], [1280, 720], [768, 1024]];
const covers = (plan, p) => plan.some((s) => { for (let i = 0; i < s.pts.length - 1; i++) if (distToSegment(p, s.pts[i], s.pts[i + 1]) <= s.width * 0.4) return true; return false; });

test("each tab has its own theme and look", () => {
  assert.deepStrictEqual(U.themes().sort(), ["chat", "files", "pc", "settings"]);
});

test("the same seed paints the same picture, a different one a different picture (in every style)", () => {
  STYLES.forEach((style) => {
    const a = U.strokePlan(5, 390, 844, { style }), b = U.strokePlan(5, 390, 844, { style }), c = U.strokePlan(6, 390, 844, { style });
    assert.deepStrictEqual(a, b, style);
    assert.notDeepStrictEqual(a, c, style);
  });
  const r = U.mulberry(9); const first = [r(), r(), r()];
  const r2 = U.mulberry(9); assert.deepStrictEqual(first, [r2(), r2(), r2()]);
  assert.ok(first.every((x) => x >= 0 && x < 1));
});

test("the styles really are different pictures", () => {
  const plans = STYLES.map((style) => JSON.stringify(U.strokePlan(5, 390, 844, { style })));
  assert.strictEqual(new Set(plans).size, STYLES.length);
});

test("the strokes cover every part of the screen, for phones and wide screens alike, whatever the seed and style", () => {
  STYLES.forEach((style) => SIZES.forEach(([w, h]) => {
    for (const seed of [1, 2, 3, 99, 12345]) {
      const plan = U.strokePlan(seed, w, h, { rows: Math.round(h / (style === "scan" ? 46 : 70)), style });
      for (let gy = 0; gy <= 24; gy++) for (let gx = 0; gx <= 14; gx++) {
        const p = [gx / 14 * w, gy / 24 * h];
        assert.ok(covers(plan, p), `${style} ${w}x${h} seed ${seed}: nothing paints over (${Math.round(p[0])}, ${Math.round(p[1])})`);
      }
    }
  }));
});

test("the scraping strokes cover the screen too, so no paint is left behind", () => {
  STYLES.forEach((style) => [[390, 844], [320, 568], [1280, 720]].forEach(([w, h]) => {
    for (const seed of [41, 42]) {
      const plan = U.strokePlan(seed, w, h, { rows: Math.round(h / (style === "scan" ? 46 : 70) * 0.8), wipe: true, style });
      for (let gy = 0; gy <= 24; gy++) for (let gx = 0; gx <= 14; gx++) {
        const p = [gx / 14 * w, gy / 24 * h];
        assert.ok(covers(plan, p), `${style} ${w}x${h} (${Math.round(p[0])}, ${Math.round(p[1])})`);
      }
    }
  }));
});

test("every stroke starts and ends inside the time it is given, in every style", () => {
  STYLES.forEach((style) => [false, true].forEach((wipe) => {
    const plan = U.strokePlan(8, 390, 844, { style, wipe });
    assert.ok(plan.length >= 8, style);
    plan.forEach((s) => {
      assert.ok(s.delay >= 0 && s.dur > 0 && s.delay + s.dur <= 1 + 1e-9, style + JSON.stringify([s.delay, s.dur]));
      assert.ok(s.tone >= 0 && s.tone < 1 && s.width > 20 && s.pts.length >= 20, style);
      s.pts.forEach((p) => assert.ok(Number.isFinite(p[0]) && Number.isFinite(p[1]), style));
    });
    assert.ok(Math.max(...plan.map((s) => s.delay + s.dur)) > 0.9, style + ": the last stroke finishes at the end of the phase");
  }));
});

test("straight styles have strokes that run past both edges of the screen, the spiral reaches the corners", () => {
  ["waves", "scan", "stack"].forEach((style) => {
    const spanning = U.strokePlan(8, 390, 844, { style }).filter((s) => { const xs = s.pts.map((p) => p[0]); return Math.min(...xs) < 0 && Math.max(...xs) > 390; });
    assert.ok(spanning.length >= 8, style + ": " + spanning.length);
  });
  const reach = Math.max(...U.strokePlan(8, 390, 844, { style: "swirl" }).flatMap((s) => s.pts.map((p) => Math.hypot(p[0] - 195, p[1] - 422))));
  assert.ok(reach >= Math.hypot(195, 422), "the outer ring reaches the corners: " + reach);
});

test("the scan is drawn top to bottom, the swirl from the middle outwards, the stack cascades down", () => {
  const order = (style, key) => U.strokePlan(8, 390, 844, { style }).filter((s) => s.width > 0).map((s) => [key(s), s.delay]).sort((a, b) => a[0] - b[0]);
  const rising = (list) => list.slice(1).every((x, i) => x[1] >= list[i][1] - 0.05);
  assert.ok(rising(order("scan", (s) => s.pts[0][1])), "scan");
  const swirl = U.strokePlan(8, 390, 844, { style: "swirl" });
  assert.ok(rising(swirl.map((s) => [Math.hypot(s.pts[0][0] - 195, s.pts[0][1] - 422) + 0, s.delay]).sort((a, b) => a[0] - b[0])), "swirl");
  const stack = U.strokePlan(8, 390, 844, { style: "stack", wipe: true });
  assert.ok(rising(stack.map((s) => [(s.pts[0][1] + s.pts[s.pts.length - 1][1]) / 2, s.delay]).sort((a, b) => a[0] - b[0])), "stack");
});

test("every theme palette is a real list of colours with warm and cool variety", () => {
  const css = require("fs").readFileSync(require("path").join(__dirname, "..", "..", "src", "handoff", "web", "ux.js"), "utf8");
  ["pc", "files", "settings"].forEach((name) => {
    const m = new RegExp(name + ": \\{[^}]*palette: \\[([^\\]]+)\\]").exec(css);
    assert.ok(m, name);
    const colours = [...m[1].matchAll(/"(#[0-9a-f]{6})"/gi)].map((x) => x[1]);
    assert.ok(colours.length >= 8, name);
    colours.forEach((c) => assert.ok(U.hexToHsl(c).every(Number.isFinite), c));
  });
});

test("a brush moves smoothly: easing starts and ends at rest", () => {
  assert.strictEqual(U.easeInOut(0), 0);
  assert.strictEqual(U.easeInOut(1), 1);
  assert.strictEqual(U.easeInOut(-3), 0);
  assert.strictEqual(U.easeInOut(7), 1);
  assert.ok(Math.abs(U.easeInOut(0.5) - 0.5) < 1e-9);
  const mid = U.pointAt([[0, 0], [10, 0], [10, 10]], 0.5);
  assert.deepStrictEqual(mid.slice(0, 2), [10, 0]);
});

test("the palette is built from the accent colours and always has enough variety", () => {
  const p = U.palette("#22d3ee", "#3b82f6");
  assert.ok(p.length >= 6);
  p.forEach(([h, s, l]) => { assert.ok(h >= 0 && h < 360 && s >= 0 && s <= 1 && l > 0 && l < 1, JSON.stringify([h, s, l])); });
  const hues = new Set(p.map((c) => Math.round(c[0] / 30)));
  assert.ok(hues.size >= 3, "a warm contrast colour is in there");
  assert.deepStrictEqual(U.hexToHsl("#ff0000").map((x) => Math.round(x * 100) / 100), [0, 1, 0.5]);
  assert.ok(U.palette(undefined, "nonsense").length >= 6);   // a bad colour never breaks the transition
});

const bad = run.filter((r) => !r[1]);
bad.forEach(([name, , e]) => console.log("FAIL " + name + "\n  " + (e && e.stack || e)));
console.log(`${run.length - bad.length} of ${run.length} passed`);
process.exit(bad.length ? 1 : 0);
