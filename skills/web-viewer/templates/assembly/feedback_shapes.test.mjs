// Notes on the model, stage 1, page side: projectShapes(shapes, pick) turns the client's 2D drawing into per-shape parts and points in mm.
// Run: node --test agent/skills/web-viewer/templates/assembly/feedback_shapes.test.mjs
//
// Contract under test (from the spec, not from the code):
//   projectShapes(shapes, pick) -> [{type, text?, closed?, px: [[x,y],...], pts: [P...], inside?: {"<part>": n, "": misses}}]
//   shapes: the page's drawing in CSS px ({type: pen|line|arrow|rect|ellipse|text, pts, text?}); pick(x, y) -> null | {part, n, at, normal}
//   P on a hit = {px, role, part, n, at, normal}; P on empty space = {px, role, miss: true}
//   arrow: exactly [tail (pts[0]), tip (pts[1])];  line: "end" at both ends, "along" between;
//   rect / ellipse: one "centre", "edge" points on the outline, `inside` = grid samples strictly inside the shape (ellipse: not its bbox corners);
//   pen: closed loop -> closed: true + like an area (centre, edge, inside); open stroke -> "stroke" samples incl. both ends, no inside;
//   text: one "anchor" (centre of the text box), text kept;  at most 24 points per shape.
//
// The fake `pick` below is a flat synthetic scene in screen space, so every expected answer is known exactly.
import test from "node:test";
import assert from "node:assert/strict";

// importing must not need a DOM: no document / window here
const mod = await import("./feedback.mjs");
const projectShapes = mod.projectShapes;
const MAX_PTS = 24;

function fn() {
  assert.equal(typeof projectShapes, "function", "feedback.mjs must export projectShapes(shapes, pick)");
  return projectShapes;
}

// ---- synthetic scene: two parts as screen rectangles, at = (x/2, y/2, z) mm, top faces (normal +Z)
const P1 = { id: "10.01.001", n: 3, x0: 100, y0: 100, x1: 200, y1: 200, z: 20 };
const P2 = { id: "10.02.001", n: 5, x0: 300, y0: 100, x1: 400, y1: 200, z: 30 };
function scenePick(x, y) {
  for (const p of [P1, P2]) {
    if (x >= p.x0 && x <= p.x1 && y >= p.y0 && y <= p.y1) return { part: p.id, n: p.n, at: [x / 2, y / 2, p.z], normal: [0, 0, 1] };
  }
  return null;
}

const near = (a, b, tol, msg) => {
  assert.equal(a.length, b.length, msg);
  a.forEach((v, i) => assert.ok(Math.abs(v - b[i]) <= tol, `${msg}: ${JSON.stringify(a)} vs ${JSON.stringify(b)}`));
};
const roles = (s) => s.pts.map((p) => p.role);
const partsOf = (s) => new Set(s.pts.filter((p) => !p.miss).map((p) => p.part));

function assertHit(p, part, msg) {
  assert.ok(p && !p.miss, `${msg}: expected a hit on ${part}, got ${JSON.stringify(p)}`);
  assert.equal(p.part, part, msg);
  assert.equal(typeof p.n, "number", `${msg}: hit carries the node index n`);
  const want = scenePick(p.px[0], p.px[1]);
  near(p.at, want.at, 0.06, `${msg}: at = the picked point in mm`);
  near(p.normal, want.normal, 0.01, `${msg}: normal of the picked face`);
}
function assertMiss(p, msg) {
  assert.equal(p.miss, true, `${msg}: empty space is miss: true`);
  for (const k of ["part", "at", "normal"]) assert.ok(!(k in p) || p[k] == null, `${msg}: a miss has no ${k}`);
  assert.ok(Array.isArray(p.px) && p.px.length === 2, `${msg}: a miss keeps its px`);
}

// ---------------------------------------------------------------- module surface

test("feedback.mjs imports in node without a DOM and exports projectShapes", () => {
  fn();
});

test("no shapes -> empty list", () => {
  assert.deepEqual(fn()([], scenePick), []);
});

test("one output shape per input shape, in the same order, with the type kept", () => {
  const out = fn()([
    { type: "arrow", pts: [[150, 150], [350, 150]] },
    { type: "rect", pts: [[120, 120], [180, 180]] },
    { type: "line", pts: [[110, 150], [390, 150]] },
  ], scenePick);
  assert.deepEqual(out.map((s) => s.type), ["arrow", "rect", "line"]);
});

// ---------------------------------------------------------------- arrow

test("arrow: exactly two points, tail at pts[0] on part A, tip at pts[1] on part B", () => {
  const [s] = fn()([{ type: "arrow", pts: [[150, 150], [350, 160]] }], scenePick);
  assert.equal(s.type, "arrow");
  assert.equal(s.pts.length, 2, "an arrow is just its tail and its tip");
  assert.deepEqual(roles(s), ["tail", "tip"]);
  near(s.pts[0].px, [150, 150], 0.51, "tail px is where the stroke started");
  near(s.pts[1].px, [350, 160], 0.51, "tip px is the arrowhead end");
  assertHit(s.pts[0], P1.id, "tail");
  assertHit(s.pts[1], P2.id, "tip");
  near(s.pts[0].at, [75, 75, 20], 0.06, "tail at in mm");
  near(s.pts[1].at, [175, 80, 30], 0.06, "tip at in mm");
  assert.ok(!("inside" in s) || s.inside == null, "an arrow has no inside counts");
  assert.equal(s.px.length, 2); near(s.px[0], [150, 150], 0.51, "px[0]"); near(s.px[1], [350, 160], 0.51, "px[1]");
});

test("arrow drawn backwards (from B to A): tail is B, tip is A — the arrowhead decides", () => {
  const [s] = fn()([{ type: "arrow", pts: [[350, 150], [150, 150]] }], scenePick);
  assertHit(s.pts[0], P2.id, "tail"); assertHit(s.pts[1], P1.id, "tip");
});

test("arrow from empty space to a part: the tail is a miss, the tip a hit", () => {
  const [s] = fn()([{ type: "arrow", pts: [[20, 20], [350, 150]] }], scenePick);
  assert.deepEqual(roles(s), ["tail", "tip"]);
  assertMiss(s.pts[0], "tail on empty space");
  near(s.pts[0].px, [20, 20], 0.51, "miss px");
  assertHit(s.pts[1], P2.id, "tip");
});

// ---------------------------------------------------------------- line

test("line: both ends are 'end', the points between are 'along' and lie on the segment", () => {
  const [s] = fn()([{ type: "line", pts: [[110, 150], [390, 150]] }], scenePick);
  const r = roles(s);
  assert.equal(r[0], "end"); assert.equal(r[r.length - 1], "end");
  assert.equal(r.filter((x) => x === "end").length, 2, "exactly two ends");
  assert.ok(r.filter((x) => x === "along").length >= 1, "at least one point along the line");
  assert.ok(r.every((x) => x === "end" || x === "along"), `only end/along roles: ${r}`);
  near(s.pts[0].px, [110, 150], 0.51, "first end"); near(s.pts[s.pts.length - 1].px, [390, 150], 0.51, "last end");
  for (const p of s.pts) assert.ok(Math.abs(p.px[1] - 150) <= 0.51 && p.px[0] >= 109.5 && p.px[0] <= 390.5, `on the segment: ${p.px}`);
  assertHit(s.pts[0], P1.id, "start end"); assertHit(s.pts[s.pts.length - 1], P2.id, "far end");
  assert.ok(s.pts.some((p) => p.miss), "the gap between the parts is sampled as empty space");
  assert.ok(s.pts.length <= MAX_PTS);
});

// ---------------------------------------------------------------- rect

test("rect: one centre, edge points on the outline, inside counts parts and misses", () => {
  // corners (30,30)-(270,270): P1 (100..200) in the middle, a wide ring of empty space around it
  const [s] = fn()([{ type: "rect", pts: [[30, 30], [270, 270]] }], scenePick);
  const r = roles(s);
  assert.equal(r.filter((x) => x === "centre").length, 1, "exactly one centre");
  assert.ok(r.filter((x) => x === "edge").length >= 4, "edge points on the outline");
  assert.ok(r.every((x) => x === "centre" || x === "edge"), `only centre/edge roles: ${r}`);
  const c = s.pts.find((p) => p.role === "centre");
  near(c.px, [150, 150], 0.51, "centre of the rectangle"); assertHit(c, P1.id, "centre");
  for (const p of s.pts.filter((q) => q.role === "edge")) {
    const [x, y] = p.px;
    const onSide = (Math.abs(x - 30) <= 0.51 || Math.abs(x - 270) <= 0.51) && y >= 29.5 && y <= 270.5 ||
                   (Math.abs(y - 30) <= 0.51 || Math.abs(y - 270) <= 0.51) && x >= 29.5 && x <= 270.5;
    assert.ok(onSide, `edge point on the rectangle's outline: ${p.px}`);
  }
  assert.ok(s.inside && typeof s.inside === "object", "rect has inside counts");
  assert.ok(s.inside[P1.id] > 0, `P1 is inside: ${JSON.stringify(s.inside)}`);
  assert.ok(s.inside[""] > 0, `the empty ring is counted as misses under "": ${JSON.stringify(s.inside)}`);
  assert.ok(!(P2.id in s.inside), "P2 is not inside");
  assert.ok(s.pts.length <= MAX_PTS);
});

test("rect drawn from the bottom-right corner to the top-left one: same centre, same parts", () => {
  const [s] = fn()([{ type: "rect", pts: [[190, 190], [110, 110]] }], scenePick);
  near(s.pts.find((p) => p.role === "centre").px, [150, 150], 0.51, "centre");
  assert.ok(s.inside[P1.id] > 0);
  assert.ok(!s.inside[""], `a rect wholly on P1 has no misses inside: ${JSON.stringify(s.inside)}`);
});

// ---------------------------------------------------------------- ellipse vs its bounding box

test("ellipse: inside counts only what is inside the ellipse, never the corners of its bbox", () => {
  // bbox (0,0)-(200,200), centre (100,100), radius 100. Part "IN" = the disc, part "CORNER" = the bbox outside the disc.
  const R = 100, pick = (x, y) => {
    if (x < 0 || y < 0 || x > 200 || y > 200) return null;
    const d = Math.hypot(x - 100, y - 100);
    return d <= R + 0.5 ? { part: "IN", n: 1, at: [x, y, 0], normal: [0, 0, 1] } : { part: "CORNER", n: 2, at: [x, y, 0], normal: [0, 0, 1] };
  };
  const [s] = fn()([{ type: "ellipse", pts: [[0, 0], [200, 200]] }], pick);
  assert.ok(s.inside && s.inside.IN > 0, `the disc is inside: ${JSON.stringify(s.inside)}`);
  assert.ok(!s.inside.CORNER, `the bbox corners are outside the ellipse: ${JSON.stringify(s.inside)}`);
  const c = s.pts.find((p) => p.role === "centre");
  near(c.px, [100, 100], 0.51, "centre of the ellipse"); assert.equal(c.part, "IN");
  for (const p of s.pts.filter((q) => q.role === "edge")) {
    const d = Math.hypot(p.px[0] - 100, p.px[1] - 100);
    assert.ok(Math.abs(d - R) <= 1.5, `edge point on the ellipse, not on its bbox: ${p.px} (r=${d.toFixed(1)})`);
  }
});

test("a flat ellipse: edge points follow both semi-axes", () => {
  const [s] = fn()([{ type: "ellipse", pts: [[100, 130], [300, 170]] }], scenePick);   // a = 100, b = 20, centre (200,150)
  const edges = s.pts.filter((q) => q.role === "edge");
  assert.ok(edges.length >= 4);
  for (const p of edges) {
    const e = ((p.px[0] - 200) / 100) ** 2 + ((p.px[1] - 150) / 20) ** 2;
    assert.ok(Math.abs(e - 1) <= 0.1, `on the ellipse: ${p.px} (${e.toFixed(3)})`);
  }
});

// ---------------------------------------------------------------- pen: closed loop vs open stroke

const circle = (cx, cy, r, n, turn = 1) => Array.from({ length: n + 1 }, (_, i) => {
  const a = (2 * Math.PI * turn * i) / n; return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
});

test("pen loop that ends where it started is closed and treated as an area", () => {
  const [s] = fn()([{ type: "pen", pts: circle(150, 150, 45, 300) }], scenePick);
  assert.equal(s.closed, true, "a hand-drawn loop is closed");
  const r = roles(s);
  assert.equal(r.filter((x) => x === "centre").length, 1, "one centre");
  assert.ok(r.filter((x) => x === "edge").length >= 3, "edge points on the stroke");
  near(s.pts.find((p) => p.role === "centre").px, [150, 150], 3, "centre of the loop");
  for (const p of s.pts.filter((q) => q.role === "edge")) {
    const d = Math.hypot(p.px[0] - 150, p.px[1] - 150);
    assert.ok(Math.abs(d - 45) <= 1.5, `edge points lie on the stroke: ${p.px}`);
  }
  assert.ok(s.inside && s.inside[P1.id] > 0, `P1 inside the loop: ${JSON.stringify(s.inside)}`);
  assert.ok(!(P2.id in s.inside), "P2 is outside the loop");
});

test("pen loop that does not quite meet (a small gap) still counts as closed", () => {
  const pts = circle(150, 150, 45, 300, 0.97);   // ~8 px gap between the ends of a 280 px loop
  const [s] = fn()([{ type: "pen", pts }], scenePick);
  assert.equal(s.closed, true);
});

test("pen loop: inside counts use the stroke's polygon, not its bounding box", () => {
  // an L-shaped closed stroke; P2-like part only in the bbox's empty corner
  const L = [[0, 0], [200, 0], [200, 60], [60, 60], [60, 200], [0, 200], [0, 2]];
  const pick = (x, y) => (x > 120 && y > 120 && x <= 200 && y <= 200 ? { part: "NOTCH", n: 9, at: [x, y, 0], normal: [0, 0, 1] }
                         : x >= 0 && y >= 0 && x <= 200 && y <= 200 ? { part: "L", n: 8, at: [x, y, 0], normal: [0, 0, 1] } : null);
  const dense = [];
  for (let i = 0; i + 1 < L.length; i++) for (let k = 0; k < 20; k++) dense.push([L[i][0] + (L[i + 1][0] - L[i][0]) * k / 20, L[i][1] + (L[i + 1][1] - L[i][1]) * k / 20]);
  dense.push(L[L.length - 1]);
  const [s] = fn()([{ type: "pen", pts: dense }], pick);
  assert.equal(s.closed, true);
  assert.ok(s.inside.L > 0, JSON.stringify(s.inside));
  assert.ok(!s.inside.NOTCH, `the notch outside the L is not inside: ${JSON.stringify(s.inside)}`);
});

test("open pen stroke: 'stroke' samples from the first to the last point, not closed, no inside", () => {
  const pts = Array.from({ length: 281 }, (_, i) => [100 + i, 150 + 3 * Math.sin(i / 7)]);   // wavy, 100 -> 380
  const [s] = fn()([{ type: "pen", pts }], scenePick);
  assert.ok(!s.closed, "an open stroke is not closed");
  assert.ok(!("inside" in s) || s.inside == null, "an open stroke has no inside counts");
  assert.ok(s.pts.length >= 2 && s.pts.every((p) => p.role === "stroke"), `only stroke samples: ${roles(s)}`);
  near(s.pts[0].px, pts[0], 0.51, "the first sample is where the stroke starts");
  near(s.pts[s.pts.length - 1].px, pts[pts.length - 1], 0.51, "the last sample is where it ends");
  const hit = partsOf(s);
  assert.ok(hit.has(P1.id) && hit.has(P2.id), `the stroke crosses both parts: ${[...hit]}`);
  assert.ok(s.pts.some((p) => p.miss), "and the gap between them");
});

// ---------------------------------------------------------------- bounded sample count

test("a very long pen stroke yields at most 24 points", () => {
  const pts = Array.from({ length: 5000 }, (_, i) => [100 + (i % 300), 120 + Math.floor(i / 300) * 4]);
  const [s] = fn()([{ type: "pen", pts }], scenePick);
  assert.ok(s.pts.length <= MAX_PTS, `${s.pts.length} pts`);
});

test("a very long closed loop yields at most 24 points", () => {
  const [s] = fn()([{ type: "pen", pts: circle(250, 150, 140, 5000) }], scenePick);
  assert.equal(s.closed, true);
  assert.ok(s.pts.length <= MAX_PTS, `${s.pts.length} pts`);
});

test("big rect and ellipse: at most 24 points each", () => {
  for (const type of ["rect", "ellipse", "line"]) {
    const [s] = fn()([{ type, pts: [[0, 0], [1900, 1000]] }], scenePick);
    assert.ok(s.pts.length <= MAX_PTS, `${type}: ${s.pts.length} pts`);
  }
});

// ---------------------------------------------------------------- text

test("text: one 'anchor' point at the centre of the text box, the text kept", () => {
  const [s] = fn()([{ type: "text", pts: [[110, 140]], text: "gap 2 mm" }], scenePick);
  assert.equal(s.type, "text");
  assert.equal(s.text, "gap 2 mm");
  assert.equal(s.pts.length, 1);
  assert.equal(s.pts[0].role, "anchor");
  const [x, y] = s.pts[0].px;
  assert.ok(x > 110 && x < 110 + 150, `anchor is right of the top-left corner, inside the text: ${s.pts[0].px}`);
  assert.ok(y > 140 && y < 140 + 30, `anchor is below the top edge, within one line: ${s.pts[0].px}`);
  assertHit(s.pts[0], P1.id, "anchor");
});

test("text on empty space: the anchor is a miss, the text is still there", () => {
  const [s] = fn()([{ type: "text", pts: [[500, 500]], text: "put it here" }], scenePick);
  assert.equal(s.text, "put it here");
  assertMiss(s.pts[0], "anchor");
});
