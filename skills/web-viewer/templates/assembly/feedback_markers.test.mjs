// Notes on the model, stage 3, page side: the ready-made marks (pin, move, dim, face) in projectShape, and faceRegion(tris, start, eps).
// Run: node --test agent/skills/web-viewer/templates/assembly/feedback_markers.test.mjs
//
// Contract under test (from the spec, not from the code):
//   projectShape(shape, pick, opts), opts = {ray, plane, pickOn} as in stage 2; the old shape types are unchanged (feedback_3d.test.mjs).
//   pin   pts [[x,y]] (a click): one key point, role "pin"; with opts.ray: path = 1 point, the hit.
//   move  pts [start, end] (a drag): key points role "from" (start), "to" (end); path = 2 points; a point over empty space continues the
//         nearest hit's wall (stage-2 rule), or lies on opts.plane.
//   dim   pts [start, end]: key points "from", "to"; path = 2 points; `length` = distance between the two 3D points in mm, 1 decimal,
//         present only when both ends hit the model (or both are on the plane).
//   face  pts [[x,y]]: one key point, role "face", with part / at / normal as any hit.
//   faceRegion(tris, start, eps = {dist: 0.05, angle: 1}): tris = [[[x,y,z] x3], ...] mm; -> the connected set of triangles sharing EDGES
//         (both endpoints equal within 1e-6 relative or 0.01 mm) with tris[start] and lying on its plane (every vertex within dist mm, normal
//         within angle degrees, same side): {tris: [indices], normal: unit, centre: area-weighted, area: mm², size: [a, b] (extents along the
//         face's two principal in-plane axes, a >= b), plane: {normal, d = normal·point}}. A corner-only touch or a parallel triangle off the
//         plane is not included.
//
// Scene (as feedback_3d.test.mjs): screen px -> mm: X = (px - 100) / 4, Y = (py - 100) / 4;
//   box A "10.01.001" x 0..40, y 0..40, z 0..20 (px 100..260);  box B "10.02.001" x 80..120, y 0..40, z 0..50 (px 420..580); gap x 40..80.
import test from "node:test";
import assert from "node:assert/strict";

const mod = await import("./feedback.mjs");
const A = "10.01.001", B = "10.02.001";
const BOX_A = { part: A, n: 3, lo: [0, 0, 0], hi: [40, 40, 20] };
const BOX_B = { part: B, n: 5, lo: [80, 0, 0], hi: [120, 40, 50] };

const toMm = (px, py) => [(px - 100) / 4, (py - 100) / 4];
const unit = (v) => { const l = Math.hypot(...v); return v.map((c) => c / l); };
const sub = (a, b) => a.map((c, i) => c - b[i]);
const dot = (a, b) => a.reduce((s, c, i) => s + c * b[i], 0);
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const rayDown = (px, py) => { const [x, y] = toMm(px, py); return { origin: [x, y, 100], dir: [0, 0, -1] }; };

function cast(r, boxes) {   // nearest entry of the ray into any box: {part, n, at, normal} | null
  let best = null;
  for (const b of boxes) {
    let t0 = -Infinity, t1 = Infinity, ax = -1, sgn = 0, ok = true;
    for (let i = 0; i < 3; i++) {
      const o = r.origin[i], d = r.dir[i];
      if (Math.abs(d) < 1e-12) { if (o < b.lo[i] || o > b.hi[i]) { ok = false; break; } continue; }
      let ta = (b.lo[i] - o) / d, tb = (b.hi[i] - o) / d, s = -1;
      if (ta > tb) { [ta, tb] = [tb, ta]; s = 1; }
      if (ta > t0) { t0 = ta; ax = i; sgn = s; }
      t1 = Math.min(t1, tb);
    }
    if (!ok || t0 > t1 || t0 < 0) continue;
    if (!best || t0 < best.t) {
      const normal = [0, 0, 0]; normal[ax] = sgn;
      best = { t: t0, part: b.part, n: b.n, at: r.origin.map((o, i) => o + t0 * r.dir[i]), normal };
    }
  }
  if (!best) return null;
  const { t, ...hit } = best;
  return hit;
}
const DOWN = { ray: rayDown, pick: (x, y) => cast(rayDown(x, y), [BOX_A, BOX_B]) };
const TOP_A = { at: [20, 20, 20], normal: [0, 0, 1], part: A };

function proj() {
  assert.equal(typeof mod.projectShape, "function", "feedback.mjs must export projectShape(shape, pick, opts)");
  return mod.projectShape;
}
const run = (shape, plane = null) => proj()(shape, DOWN.pick, { ray: DOWN.ray, plane });
const finite3 = (p) => Array.isArray(p) && p.length === 3 && p.every((c) => typeof c === "number" && Number.isFinite(c));
function nearV(a, b, tol, msg) {
  assert.ok(finite3(a), `${msg}: not a finite [x,y,z]: ${JSON.stringify(a)}`);
  const d = Math.hypot(...sub(a, b));
  assert.ok(d <= tol, `${msg}: ${JSON.stringify(a)} vs ${JSON.stringify(b)} (${d.toFixed(3)} mm)`);
}
const roles = (s) => (s.pts || []).map((p) => p.role);
function pathOf(s, n, msg) {
  assert.ok(Array.isArray(s.path), `${msg}: the shape has a 3D path: ${JSON.stringify(Object.keys(s))}`);
  assert.equal(s.path.length, n, `${msg}: path has ${n} point(s): ${JSON.stringify(s.path)}`);
  s.path.forEach((p, i) => assert.ok(finite3(p), `${msg}: path[${i}] = ${JSON.stringify(p)}`));
  return s.path;
}
const oneDecimal = (x) => typeof x === "number" && Number.isFinite(x) && Math.abs(x * 10 - Math.round(x * 10)) < 1e-6;

// points used below (straight-down rays): px (180,180) -> A's top (20, 20, 20); px (500,180) -> B's top (100, 20, 50);
// px (340,180) -> the gap (60, 20) over empty space; px (220,240) -> A's top (30, 35, 20); px (20,40) -> empty space (-20, -15)
const ON_A = [180, 180], ON_B = [500, 180], GAP = [340, 180], ON_A2 = [220, 240], AIR = [20, 40];

// ---------------------------------------------------------------- pin

test("pin on A: one key point, role pin, on A at its mm point; path = that one point", () => {
  const s = run({ type: "pin", pts: [ON_A] });
  assert.equal(s.type, "pin");
  assert.deepEqual(roles(s), ["pin"], "exactly one key point, role pin");
  const p = s.pts[0];
  assert.equal(p.part, A, `the pin is on A: ${JSON.stringify(p)}`);
  nearV(p.at, [20, 20, 20], 0.05, "pin at");
  nearV(p.normal, [0, 0, 1], 0.01, "pin normal (A's top)");
  nearV(pathOf(s, 1, "pin")[0], [20, 20, 20], 0.06, "path = the hit");
  assert.ok(!s.flat, "a pin that hits is not flat");
});

test("pin on B: the hit is B's top (z = 50); shapeParts counts it", () => {
  const s = run({ type: "pin", pts: [ON_B] });
  assert.deepEqual(roles(s), ["pin"]);
  assert.equal(s.pts[0].part, B);
  assert.deepEqual(mod.shapeParts([run({ type: "pin", pts: [ON_A] }), s]), [A, B]);
  nearV(pathOf(s, 1, "pin on B")[0], [100, 20, 50], 0.06, "path");
});

test("pin over empty space: a miss, no 3D path", () => {
  const s = run({ type: "pin", pts: [AIR] });
  assert.deepEqual(roles(s), ["pin"]);
  assert.equal(s.pts[0].miss, true, JSON.stringify(s.pts[0]));
  assert.ok(!s.pts[0].part);
  assert.ok(s.path == null || s.path.length === 0, `no path for a pin in the air: ${JSON.stringify(s.path)}`);
});

test("pin without opts: stage-1 style, one key point, no path", () => {
  const s = proj()({ type: "pin", pts: [ON_A] }, DOWN.pick);
  assert.deepEqual(roles(s), ["pin"]);
  assert.equal(s.pts[0].part, A);
  assert.ok(!("path" in s), "no path without opts.ray");
});

// ---------------------------------------------------------------- move

test("move from A to B: key points from (A) and to (B); path = [from, to] in the drawn order", () => {
  const s = run({ type: "move", pts: [ON_A, ON_B] });
  assert.equal(s.type, "move");
  assert.deepEqual(roles(s), ["from", "to"]);
  assert.equal(s.pts[0].part, A); assert.equal(s.pts[1].part, B);
  const path = pathOf(s, 2, "move");
  nearV(path[0], [20, 20, 20], 0.06, "from");
  nearV(path[1], [100, 20, 50], 0.06, "to");
  assert.ok(!("length" in s), "a move carries no dimension length");
});

test("move drawn backwards: from is the drag's start (B), to its end (A)", () => {
  const s = run({ type: "move", pts: [ON_B, ON_A] });
  assert.deepEqual(roles(s), ["from", "to"]);
  assert.equal(s.pts[0].part, B); assert.equal(s.pts[1].part, A);
  const path = pathOf(s, 2, "move back");
  nearV(path[0], [100, 20, 50], 0.06, "from");
  nearV(path[1], [20, 20, 20], 0.06, "to");
});

test("move from A into the gap: the end continues A's top wall (stage-2 rule)", () => {
  const s = run({ type: "move", pts: [ON_A, GAP] });
  assert.equal(s.pts[1].miss, true, "the end is over empty space");
  const path = pathOf(s, 2, "move into the gap");
  nearV(path[1], [60, 20, 20], 0.06, "end = ray ∩ A's top plane");
});

test("move on a drawing plane: both points on the plane, the end beyond A's edge is off", () => {
  const s = run({ type: "move", pts: [ON_A, ON_B] }, TOP_A);
  assert.deepEqual(roles(s), ["from", "to"]);
  const path = pathOf(s, 2, "move on a plane");
  nearV(path[0], [20, 20, 20], 0.06, "from on the plane");
  nearV(path[1], [100, 20, 20], 0.06, "to = ray ∩ the plane (under B's top), not B's top");
  for (const p of s.pts) assert.equal(p.plane, true, JSON.stringify(p));
  assert.equal(s.pts[0].part, A);
  assert.equal(s.pts[1].off, true, "the end is off A's face");
});

// ---------------------------------------------------------------- dim

test("dim from A to B: from/to, path = 2 points, length = their distance in mm, 1 decimal", () => {
  const s = run({ type: "dim", pts: [ON_A, ON_B] });
  assert.equal(s.type, "dim");
  assert.deepEqual(roles(s), ["from", "to"]);
  assert.equal(s.pts[0].part, A); assert.equal(s.pts[1].part, B);
  const path = pathOf(s, 2, "dim");
  nearV(path[0], [20, 20, 20], 0.06, "from");
  nearV(path[1], [100, 20, 50], 0.06, "to");
  assert.ok(oneDecimal(s.length), `length is a number with 1 decimal: ${JSON.stringify(s.length)}`);
  assert.equal(s.length, 85.4, "|(100,20,50) - (20,20,20)| = 85.44 -> 85.4");
  assert.ok(Math.abs(s.length - Math.hypot(...sub(path[1], path[0]))) <= 0.06, "length = the distance between the path's two points");
});

test("dim on one part: length of a short span on A's top", () => {
  const s = run({ type: "dim", pts: [ON_A, ON_A2] });   // (20,20,20) -> (30,35,20): 18.03 -> 18.0
  assert.equal(s.length, 18.0);
});

test("dim with an end over empty space: path still 2 points, but no length", () => {
  const s = run({ type: "dim", pts: [ON_A, GAP] });
  assert.deepEqual(roles(s), ["from", "to"]);
  pathOf(s, 2, "dim into the gap");
  assert.ok(!("length" in s) || s.length == null, `no length when an end misses: ${s.length}`);
});

test("dim wholly over empty space: no length, no path", () => {
  const s = run({ type: "dim", pts: [AIR, [60, 40]] });
  assert.deepEqual(roles(s), ["from", "to"]);
  assert.ok(!("length" in s) || s.length == null, `no length: ${s.length}`);
  assert.ok(s.path == null || s.path.length === 0, JSON.stringify(s.path));
});

test("dim on a drawing plane: both ends on the plane -> length between the plane points, also past the edge", () => {
  const s = run({ type: "dim", pts: [ON_A, ON_B] }, TOP_A);       // (20,20,20) -> (100,20,20) on A's top plane
  const path = pathOf(s, 2, "dim on a plane");
  nearV(path[1], [100, 20, 20], 0.06, "to on the plane");
  assert.equal(s.length, 80.0, "measured in the plane");
  const t = run({ type: "dim", pts: [ON_A, AIR] }, TOP_A);         // (20,20,20) -> (-20,-15,20): 53.15 -> 53.2
  assert.equal(t.length, 53.2, "an end over empty space on the plane still measures");
});

// ---------------------------------------------------------------- face

test("face click on A's top: one key point, role face, with part, at and normal", () => {
  const s = run({ type: "face", pts: [ON_A2] });
  assert.equal(s.type, "face");
  assert.deepEqual(roles(s), ["face"]);
  const p = s.pts[0];
  assert.equal(p.part, A);
  nearV(p.at, [30, 35, 20], 0.05, "at");
  nearV(p.normal, [0, 0, 1], 0.01, "normal");
  assert.ok(Array.isArray(s.path) && s.path.length >= 1, `a face mark has a 3D path: ${JSON.stringify(s.path)}`);
  for (const q of s.path) assert.ok(finite3(q) && Math.abs(q[2] - 20) <= 1.01, `the path lies on A's top plane: ${JSON.stringify(q)}`);
});

test("face click on empty space: a miss, no path", () => {
  const s = run({ type: "face", pts: [AIR] });
  assert.deepEqual(roles(s), ["face"]);
  assert.equal(s.pts[0].miss, true);
  assert.ok(s.path == null || s.path.length === 0, JSON.stringify(s.path));
});

// ================================================================ faceRegion

function region() {
  assert.equal(typeof mod.faceRegion, "function", "feedback.mjs must export faceRegion(tris, start, eps)");
  return mod.faceRegion;
}
const sq = (x0, y0, x1, y1, z = 0) => [[[x0, y0, z], [x1, y0, z], [x1, y1, z]], [[x0, y0, z], [x1, y1, z], [x0, y1, z]]];   // CCW from +Z
function box(lo, hi) {        // 12 triangles, wound outwards; -> {tris, faces: {"+z": [i, j], ...}}
  const tris = [], faces = {};
  for (let ax = 0; ax < 3; ax++) {
    const u = (ax + 1) % 3, w = (ax + 2) % 3;
    for (const s of [-1, 1]) {
      const q = [[lo[u], lo[w]], [hi[u], lo[w]], [hi[u], hi[w]], [lo[u], hi[w]]].map(([cu, cw]) => {
        const p = [0, 0, 0]; p[ax] = s > 0 ? hi[ax] : lo[ax]; p[u] = cu; p[w] = cw; return p;
      });
      const k = tris.length;
      if (s > 0) tris.push([q[0], q[1], q[2]], [q[0], q[2], q[3]]); else tris.push([q[0], q[2], q[1]], [q[0], q[3], q[2]]);
      faces[(s > 0 ? "+" : "-") + "xyz"[ax]] = [k, k + 1];
    }
  }
  return { tris, faces };
}
const sorted = (a) => [...a].sort((x, y) => x - y);
function triNormal(t) { return unit(cross(sub(t[1], t[0]), sub(t[2], t[0]))); }
function checkPlane(r, normal, d, msg) {
  assert.ok(r.plane && finite3(r.plane.normal) && typeof r.plane.d === "number", `${msg}: plane {normal, d}: ${JSON.stringify(r.plane)}`);
  nearV(r.plane.normal, normal, 1e-3, `${msg}: plane.normal`);
  assert.ok(Math.abs(r.plane.d - d) <= 1e-3, `${msg}: plane.d = ${r.plane.d}, want ${d}`);
}
function checkSize(r, a, b, msg, tol = 0.01) {
  assert.ok(Array.isArray(r.size) && r.size.length === 2, `${msg}: size [a, b]: ${JSON.stringify(r.size)}`);
  assert.ok(r.size[0] >= r.size[1], `${msg}: a >= b: ${r.size}`);
  assert.ok(Math.abs(r.size[0] - a) <= tol && Math.abs(r.size[1] - b) <= tol, `${msg}: size ${JSON.stringify(r.size)}, want [${a}, ${b}]`);
}

test("faceRegion on a box: the top face is its 2 triangles, the 4 sides and the bottom are not", () => {
  const { tris, faces } = box([0, 0, 0], [40, 40, 20]);
  for (const start of faces["+z"]) {
    const r = region()(tris, start);
    assert.deepEqual(sorted(r.tris), sorted(faces["+z"]), `start ${start}: just the top's two triangles`);
    nearV(r.normal, [0, 0, 1], 1e-6, "normal");
    nearV(r.centre, [20, 20, 20], 1e-6, "centre");
    assert.ok(Math.abs(r.area - 1600) <= 1e-6, `area ${r.area}`);
    checkSize(r, 40, 40, "a square top is 40 x 40 (not its diagonal)");
    checkPlane(r, [0, 0, 1], 20, "top");
  }
});

test("faceRegion on a box: a rectangular top, the bottom, a side face", () => {
  const { tris, faces } = box([80, 0, 0], [140, 20, 30]);   // 60 x 20 x 30
  let r = region()(tris, faces["+z"][1]);
  assert.deepEqual(sorted(r.tris), sorted(faces["+z"]));
  nearV(r.centre, [110, 10, 30], 1e-6, "top centre");
  assert.ok(Math.abs(r.area - 1200) <= 1e-6, `top area ${r.area}`);
  checkSize(r, 60, 20, "top");
  checkPlane(r, [0, 0, 1], 30, "top");
  r = region()(tris, faces["-z"][0]);
  assert.deepEqual(sorted(r.tris), sorted(faces["-z"]));
  nearV(r.normal, [0, 0, -1], 1e-6, "bottom normal points down (outwards)");
  checkPlane(r, [0, 0, -1], 0, "bottom");
  r = region()(tris, faces["-x"][0]);
  assert.deepEqual(sorted(r.tris), sorted(faces["-x"]));
  nearV(r.normal, [-1, 0, 0], 1e-6, "-X side");
  nearV(r.centre, [80, 10, 15], 1e-6, "-X side centre");
  assert.ok(Math.abs(r.area - 600) <= 1e-6, `side area ${r.area}`);
  checkSize(r, 30, 20, "-X side is 20 (y) x 30 (z): a >= b");
  checkPlane(r, [-1, 0, 0], -80, "-X side: d = normal·point");
});

test("faceRegion: a rectangle turned 30° in its plane, made of a fan of 4 triangles: size along its own sides", () => {
  const c = Math.cos(Math.PI / 6), s = Math.sin(Math.PI / 6), O = [5, 7, 3];
  const at = (u, v) => [O[0] + u * c - v * s, O[1] + u * s + v * c, O[2]];
  const k = [at(-25, -10), at(25, -10), at(25, 10), at(-25, 10)], m = at(0, 0);
  const tris = [0, 1, 2, 3].map((i) => [m, k[i], k[(i + 1) % 4]]);
  const r = region()(tris, 2);
  assert.deepEqual(sorted(r.tris), [0, 1, 2, 3]);
  assert.ok(Math.abs(r.area - 1000) <= 1e-6, `area ${r.area}`);
  nearV(r.centre, O, 1e-6, "centre");
  checkSize(r, 50, 20, "turned rectangle");
});

test("faceRegion: a tilted face (normal 30° off +Z): plane d = normal·point", () => {
  const n = [0, -Math.sin(Math.PI / 6), Math.cos(Math.PI / 6)], v = [0, Math.cos(Math.PI / 6), Math.sin(Math.PI / 6)], P0 = [0, 0, 10];
  const at = (x, t) => [x, P0[1] + v[1] * t, P0[2] + v[2] * t];
  const tris = [[at(0, 0), at(30, 0), at(30, 10)], [at(0, 0), at(30, 10), at(0, 10)]];
  nearV(triNormal(tris[0]), n, 1e-9, "setup: the triangles face n");
  const r = region()(tris, 0);
  assert.deepEqual(sorted(r.tris), [0, 1]);
  nearV(r.normal, n, 1e-6, "normal");
  checkPlane(r, n, dot(n, P0), "tilted");
  assert.ok(Math.abs(r.area - 300) <= 1e-6, `area ${r.area}`);
  checkSize(r, 30, 10, "tilted");
});

// an L in z = 5 from three 10 mm squares: [0,10]x[0,10], [10,20]x[0,10], [0,10]x[10,20]  (area 300, centroid (8.33, 8.33, 5))
const L = [...sq(0, 0, 10, 10, 5), ...sq(10, 0, 20, 10, 5), ...sq(0, 10, 10, 20, 5)];
const L_CENTRE = [25 / 3, 25 / 3, 5];

test("faceRegion: an L-shaped coplanar face of 6 triangles, whichever triangle it starts from", () => {
  for (let start = 0; start < L.length; start++) {
    const r = region()(L, start);
    assert.deepEqual(sorted(r.tris), [0, 1, 2, 3, 4, 5], `start ${start}`);
    nearV(r.normal, [0, 0, 1], 1e-6, "normal");
    nearV(r.centre, L_CENTRE, 1e-6, "area-weighted centre");
    assert.ok(Math.abs(r.area - 300) <= 1e-6, `area ${r.area}`);
    checkPlane(r, [0, 0, 1], 5, "L");
    assert.ok(Array.isArray(r.size) && r.size.length === 2 && r.size[0] >= r.size[1] && r.size[1] > 0, `size [a >= b]: ${JSON.stringify(r.size)}`);
  }
});

test("faceRegion leaves out: a coplanar square touching at one corner, a parallel square 0.2 mm off, a 3° tilted triangle, a reversed one", () => {
  const corner = sq(20, 10, 30, 20, 5);                   // touches the L only at (20, 10, 5)
  const parallel = sq(20, 0, 30, 10, 5.2);                // beside the L's [10,20]x[0,10] square, 0.2 mm above its plane
  const z3 = 5 + 0.5 * Math.tan((3 * Math.PI) / 180);     // shares the L's edge (0,0)-(10,0); its third vertex 0.026 mm off: only its angle is out
  const tilted = [[[10, 0, 5], [0, 0, 5], [5, -0.5, z3]]];
  const reversed = [[[0, 20, 5], [5, 25, 5], [10, 20, 5]]];   // coplanar, shares the L's edge (0,20)-(10,20), facing -Z
  const tris = [...L, ...corner, ...parallel, ...tilted, ...reversed];
  assert.ok(Math.abs(dot(triNormal(tilted[0]), [0, 0, 1]) - Math.cos((3 * Math.PI) / 180)) < 1e-6, "setup: tilted by 3°");
  assert.ok(triNormal(reversed[0])[2] < -0.99, "setup: the reversed triangle faces -Z");
  const r = region()(tris, 2);
  assert.deepEqual(sorted(r.tris), [0, 1, 2, 3, 4, 5], `only the L: ${JSON.stringify(sorted(r.tris))}`);
  assert.ok(Math.abs(r.area - 300) <= 1e-6, `area ${r.area}`);
  nearV(r.centre, L_CENTRE, 1e-6, "centre of the L alone");
  const c = region()(tris, 6);                           // started on the corner square: just itself
  assert.deepEqual(sorted(c.tris), [6, 7], "the corner square is its own face");
});

test("faceRegion takes in a neighbour within the tolerances (0.3° and 0.026 mm) and joins vertices equal within 0.01 mm", () => {
  const z = 5 + 5 * Math.tan((0.3 * Math.PI) / 180);
  const near = [[[0, 0, 5], [0, 10, 5], [-5, 5, z]]];     // shares the L's edge x = 0, y 0..10
  let r = region()(L.concat(near), 0);
  assert.deepEqual(sorted(r.tris), [0, 1, 2, 3, 4, 5, 6], "a triangle within 1° and 0.05 mm is part of the face");
  assert.ok(Math.abs(r.area - 325) <= 0.01, `area ${r.area}`);
  const jit = [...sq(0, 0, 10, 10, 5), ...sq(10, 0, 20, 10, 5), ...sq(0, 10.005, 10, 20, 5)];   // the third square 5 µm off the shared edge
  r = region()(jit, 0);
  assert.deepEqual(sorted(r.tris), [0, 1, 2, 3, 4, 5], "vertices 0.005 mm apart are the same vertex");
  const gap = [...sq(0, 0, 10, 10, 5), ...sq(0, 10.05, 10, 20, 5)];                              // 0.05 mm apart: two faces
  r = region()(gap, 0);
  assert.deepEqual(sorted(r.tris), [0, 1], "vertices 0.05 mm apart are not the same: no shared edge");
});

test("faceRegion with a looser eps takes the tilted neighbour in", () => {
  const z3 = 5 + 0.5 * Math.tan((3 * Math.PI) / 180);
  const tris = [...L, [[10, 0, 5], [0, 0, 5], [5, -0.5, z3]]];
  assert.deepEqual(sorted(region()(tris, 0).tris), [0, 1, 2, 3, 4, 5], "default eps: 3° is out");
  assert.deepEqual(sorted(region()(tris, 0, { dist: 0.05, angle: 5 }).tris), [0, 1, 2, 3, 4, 5, 6], "angle 5°: in");
});

// ---- stage 3 review #2 regressions ----
test("a face click while a drawing plane is set is the face clicked, not a point on the plane", () => {
  const s = run({ type: "face", pts: [ON_B] }, TOP_A);
  assert.equal(s.pts[0].role, "face");
  assert.equal(s.pts[0].part, B, "on B, where the click landed");
  assert.equal(s.pts[0].n, BOX_B.n);
  assert.ok(!s.pts[0].plane && !s.pts[0].off, JSON.stringify(s.pts[0]));
  assert.ok(Math.abs(s.pts[0].at[2] - 50) <= 0.06, `on B's top (z = 50): ${JSON.stringify(s.pts[0].at)}`);
  assert.equal(s.plane, undefined, "a face mark carries no drawing plane");
});

test("faceRegion: a click on a faceted cylinder is a curved surface, a box top is not", () => {
  const R = 10, N = 36, H = 30, tris = [];   // 10° from facet to facet
  for (let i = 0; i < N; i++) {
    const a = (2 * Math.PI * i) / N, b = (2 * Math.PI * (i + 1)) / N, p = (t, z) => [R * Math.cos(t), R * Math.sin(t), z];
    tris.push([p(a, 0), p(b, 0), p(b, H)], [p(a, 0), p(b, H), p(a, H)]);
  }
  const cyl = mod.faceRegion(tris, 0);
  assert.equal(cyl.curved, true, "its neighbours bend 10° away: curved");
  const box = [[[0, 0, 20], [40, 0, 20], [40, 40, 20]], [[0, 0, 20], [40, 40, 20], [0, 40, 20]], [[0, 0, 20], [0, 0, 0], [40, 0, 0]], [[0, 0, 20], [40, 0, 0], [40, 0, 20]]];
  assert.equal(mod.faceRegion(box, 0).curved, undefined, "a top that meets its side at 90° is flat");
});
