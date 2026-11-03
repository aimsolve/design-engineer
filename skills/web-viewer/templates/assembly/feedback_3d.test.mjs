// Notes on the model, stage 2, page side: projectShape(shape, pick, opts) also gives each drawn shape its 3D `path` (assembly mm),
// so the drawing sticks to the model when the camera turns, and with a drawing plane every point lies on that face's plane.
// Run: node --test agent/skills/web-viewer/templates/assembly/feedback_3d.test.mjs
//
// Contract under test (from the spec, not from the code):
//   projectShape(shape, pick, opts), opts = {ray: (x, y) => ({origin, dir}) in assembly mm, plane: null | {at, normal, part}}
//   without opts: exactly stage 1 (no `path`, no `flat`, no `plane`).
//   with opts.ray: + `path`:  arrow, line -> exactly [start, end];  rect, ellipse, closed pen -> closed outline of 8..64 points on the drawn
//     outline;  open pen -> 2..64 points along the stroke, both ends;  text -> 1 point (the anchor).  path <= 64 points.
//     no plane: a sample that hits lies on the surface (its `at`, within 1 mm; a lift <= 1 mm along the normal is allowed);
//     a sample that misses lies on ray ∩ the tangent plane of the NEAREST (by order along the shape) sample that hit;
//     no sample hits -> no `path`, `flat: true`.
//   with opts.plane: every path point and key point = ray ∩ plane; key point = {px, role, at, normal: plane.normal, plane: true}
//     + `part` when the surface hit at that pixel is within 1 mm of the plane point, else `off: true`; shape.plane = {at, normal, part};
//     area `inside` counts keep their stage-1 meaning.
//
// The scene is synthetic and ray-cast exactly, so every expected answer is known:
//   screen px -> assembly mm: X = (px - 100) / 4, Y = (py - 100) / 4 (1 mm = 4 px);
//   box A "10.01.001" x 0..40, y 0..40, z 0..20 (top at z = 20, px 100..260);  box B "10.02.001" x 80..120, y 0..40, z 0..50 (px 420..580);
//   the gap between them (x 40..80, px 260..420) and everything else is empty space.
import test from "node:test";
import assert from "node:assert/strict";

const mod = await import("./feedback.mjs");
const MAX_PATH = 64;
const A = "10.01.001", B = "10.02.001";
const BOX_A = { part: A, n: 3, lo: [0, 0, 0], hi: [40, 40, 20] };
const BOX_B = { part: B, n: 5, lo: [80, 0, 0], hi: [120, 40, 50] };

function proj() {
  assert.equal(typeof mod.projectShape, "function", "feedback.mjs must export projectShape(shape, pick, opts)");
  return mod.projectShape;
}

// ---------------------------------------------------------------- scene: exact ray casting against boxes
const toMm = (px, py) => [(px - 100) / 4, (py - 100) / 4];
const toPx = (x, y) => [4 * x + 100, 4 * y + 100];
const unit = (v) => { const l = Math.hypot(...v); return v.map((c) => c / l); };
const sub = (a, b) => a.map((c, i) => c - b[i]);
const dot = (a, b) => a.reduce((s, c, i) => s + c * b[i], 0);

const rayDown = (px, py) => { const [x, y] = toMm(px, py); return { origin: [x, y, 100], dir: [0, 0, -1] }; };
// a perspective camera: eye at (20, 20, 200), the pixel's ray aims at its (X, Y, 0)
const EYE = [20, 20, 200];
const rayPersp = (px, py) => { const [x, y] = toMm(px, py); return { origin: [...EYE], dir: unit([x - EYE[0], y - EYE[1], -EYE[2]]) }; };
// a slanted parallel ray, looking down and towards -X (sees box A's +X side face)
const SLANT = [-0.6, 0, -0.8];
const raySlant = (px, py) => { const [x, y] = toMm(px, py); return { origin: [x, y, 100], dir: [...SLANT] }; };

function cast(r, boxes) {   // nearest entry of the ray into any box: {part, n, at, normal} | null
  let best = null;
  for (const b of boxes) {
    let t0 = -Infinity, t1 = Infinity, ax = -1, sgn = 0;
    let ok = true;
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
const scene = (ray, boxes = [BOX_A, BOX_B]) => ({ ray, pick: (x, y) => cast(ray(x, y), boxes) });
const DOWN = scene(rayDown);

// ---------------------------------------------------------------- helpers
const finite3 = (p) => Array.isArray(p) && p.length === 3 && p.every((c) => typeof c === "number" && Number.isFinite(c));
function nearV(a, b, tol, msg) {
  assert.ok(finite3(a), `${msg}: not a finite [x,y,z]: ${JSON.stringify(a)}`);
  const d = Math.hypot(...sub(a, b));
  assert.ok(d <= tol, `${msg}: ${JSON.stringify(a.map((c) => +c.toFixed(3)))} vs ${JSON.stringify(b.map((c) => +c.toFixed(3)))} (${d.toFixed(3)} mm)`);
}
function nearXY(a, b, tol, msg) {
  const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
  assert.ok(d <= tol, `${msg}: ${JSON.stringify(a)} vs ${JSON.stringify(b)} (${d.toFixed(3)} mm)`);
}
function onSurface(p, at, msg) {   // the hit's point, or lifted <= 1 mm along +Z (all hits here are on top faces)
  nearV(p, at, 1.01, msg);
  assert.ok(p[2] >= at[2] - 0.01, `${msg}: below the surface: ${p[2]} < ${at[2]}`);
}
function pathOf(s, msg) {
  assert.ok(Array.isArray(s.path), `${msg}: the shape has a path: ${JSON.stringify(Object.keys(s))}`);
  s.path.forEach((p, i) => assert.ok(finite3(p), `${msg}: path[${i}] = ${JSON.stringify(p)}`));
  return s.path;
}
const run = (shape, sc = DOWN, plane = null) => proj()(shape, sc.pick, { ray: sc.ray, plane });
const pxOf = (p) => toPx(p[0], p[1]);   // straight-down rays: a point's pixel is its (x, y)
function distToPolyline(q, poly, closed = false) {
  let best = Infinity;
  const segs = poly.length - 1 + (closed ? 1 : 0);
  for (let i = 0; i < segs; i++) {
    const a = poly[i], b = poly[(i + 1) % poly.length], ab = sub(b, a), l2 = dot(ab, ab);
    const t = l2 ? Math.max(0, Math.min(1, dot(sub(q, a), ab) / l2)) : 0;
    best = Math.min(best, Math.hypot(q[0] - a[0] - t * ab[0], q[1] - a[1] - t * ab[1]));
  }
  return best;
}
const quadrants = (pts, cx, cy) => new Set(pts.map(([x, y]) => `${x >= cx ? "+" : "-"}${y >= cy ? "+" : "-"}`));

// ---------------------------------------------------------------- stage 1 stays as it was

test("without opts: stage-1 output, no path / flat / plane", () => {
  const shapes = [
    { type: "arrow", pts: [[180, 180], [500, 180]] },
    { type: "line", pts: [[180, 180], [340, 180]] },
    { type: "rect", pts: [[120, 120], [240, 240]] },
    { type: "pen", pts: Array.from({ length: 200 }, (_, i) => [140 + 2 * i, 180]) },
    { type: "text", pts: [[150, 150]], text: "here" },
  ];
  for (const sh of shapes) {
    const s = proj()(sh, DOWN.pick);
    for (const k of ["path", "flat", "plane"]) assert.ok(!(k in s), `${sh.type}: no \`${k}\` without opts: ${JSON.stringify(Object.keys(s))}`);
    assert.ok(Array.isArray(s.pts) && s.pts.length > 0, `${sh.type}: stage-1 key points are there`);
    for (const p of s.pts) assert.ok(!("plane" in p) && !("off" in p), `${sh.type}: no plane/off flags on stage-1 points`);
  }
  assert.equal(typeof mod.projectShapes, "function", "projectShapes(shapes, pick) is still exported");
  assert.equal(mod.projectShapes([shapes[0]], DOWN.pick).length, 1);
});

test("with a ray and no plane: the stage-1 fields are unchanged, only path is added", () => {
  for (const sh of [{ type: "arrow", pts: [[180, 180], [500, 180]] }, { type: "ellipse", pts: [[120, 120], [560, 240]] },
    { type: "pen", pts: Array.from({ length: 200 }, (_, i) => [140 + 2 * i, 180]) }]) {
    const old = proj()(sh, DOWN.pick);
    const { path, flat, ...rest } = run(sh);
    if (rest.plane == null) delete rest.plane;
    assert.deepEqual(rest, old, `${sh.type}: the key points, inside counts etc. are what stage 1 gives`);
    assert.ok(Array.isArray(path), `${sh.type}: path added`);
  }
});

// ---------------------------------------------------------------- arrow / line: [start, end]

test("arrow from A to B: path = [tail on A's top, tip on B's top]", () => {
  const s = run({ type: "arrow", pts: [[180, 180], [500, 180]] });
  const path = pathOf(s, "arrow");
  assert.equal(path.length, 2, "an arrow's path is exactly its two ends");
  onSurface(path[0], [20, 20, 20], "tail");
  onSurface(path[1], [100, 20, 50], "tip");
  onSurface(path[0], s.pts[0].at, "tail = the tail key point's at");
  onSurface(path[1], s.pts[1].at, "tip = the tip key point's at");
  assert.ok(!s.flat, "a shape that hits is not flat");
});

test("arrow drawn backwards: path keeps the drawn order (start = tail)", () => {
  const path = pathOf(run({ type: "arrow", pts: [[500, 180], [180, 180]] }), "arrow");
  onSurface(path[0], [100, 20, 50], "tail on B");
  onSurface(path[1], [20, 20, 20], "tip on A");
});

test("line from A out over the gap: the end continues A's top plane (z = 20)", () => {
  const path = pathOf(run({ type: "line", pts: [[180, 180], [340, 180]] }), "line");
  assert.equal(path.length, 2, "a line's path is exactly its two ends");
  onSurface(path[0], [20, 20, 20], "start on A");
  nearV(path[1], [60, 20, 20], 1.01, "end over the gap: ray ∩ A's top plane");
  assert.ok(Math.abs(path[1][0] - 60) <= 0.05 && Math.abs(path[1][1] - 20) <= 0.05, `end xy is the pixel's ray: ${path[1]}`);
});

test("line from the gap onto B: the start continues B's top plane (z = 50), not A's", () => {
  const path = pathOf(run({ type: "line", pts: [[340, 180], [500, 180]] }), "line");
  nearV(path[0], [60, 20, 50], 1.01, "start over the gap lies on the wall it continues: B");
  onSurface(path[1], [100, 20, 50], "end on B");
});

test("arrow whose tail is in empty space: the tail lies on the tip's tangent plane", () => {
  const path = pathOf(run({ type: "arrow", pts: [[340, 180], [500, 180]] }), "arrow");
  nearV(path[0], [60, 20, 50], 1.01, "tail");
});

test("perspective rays: a miss is ray ∩ the hit's tangent plane, not the hit's depth or a vertical drop", () => {
  // start aims at (20,20,0): hits A's top at (20,20,20). End aims at (60,20,0): misses; ray ∩ z = 20 -> (56, 20, 20).
  const sc = scene(rayPersp, [BOX_A]);
  const s = proj()({ type: "line", pts: [[180, 180], [340, 180]] }, sc.pick, { ray: sc.ray, plane: null });
  const path = pathOf(s, "line");
  onSurface(path[0], [20, 20, 20], "start on A");
  nearV(path[1], [56, 20, 20], 0.05, "end = ray ∩ A's top plane");
});

// ---------------------------------------------------------------- areas: closed outlines

test("rect half on A, half over the gap: closed outline of 8..64 points on the drawn rectangle, all on A's top plane", () => {
  const R = [[200, 140], [340, 220]];   // x 25..60 mm, y 10..30 mm
  const path = pathOf(run({ type: "rect", pts: R }), "rect");
  assert.ok(path.length >= 8 && path.length <= MAX_PATH, `8..64 points: ${path.length}`);
  const sides = { l: 0, r: 0, t: 0, b: 0 };
  for (const p of path) {
    const [x, y] = pxOf(p);
    assert.ok(Math.abs(p[2] - 20) <= 1.01, `on A's top or its continuation: ${p}`);
    const onL = Math.abs(x - 200) <= 2, onR = Math.abs(x - 340) <= 2, onT = Math.abs(y - 140) <= 2, onB = Math.abs(y - 220) <= 2;
    const inX = x >= 198 && x <= 342, inY = y >= 138 && y <= 222;
    assert.ok(((onL || onR) && inY) || ((onT || onB) && inX), `on the rectangle's outline: px ${x.toFixed(1)},${y.toFixed(1)}`);
    if (onL && inY) sides.l++; if (onR && inY) sides.r++; if (onT && inX) sides.t++; if (onB && inX) sides.b++;
  }
  assert.ok(Object.values(sides).every((n) => n > 0), `the outline goes round all four sides: ${JSON.stringify(sides)}`);
});

test("ellipse on A: closed outline of 8..64 points on the ellipse, round all of it", () => {
  const path = pathOf(run({ type: "ellipse", pts: [[120, 140], [240, 220]] }), "ellipse");   // centre (180,180) px, a 60 px, b 40 px
  assert.ok(path.length >= 8 && path.length <= MAX_PATH, `8..64 points: ${path.length}`);
  const px = path.map(pxOf);
  for (const [x, y] of px) {
    const e = ((x - 180) / 60) ** 2 + ((y - 180) / 40) ** 2;
    assert.ok(Math.abs(e - 1) <= 0.1, `on the ellipse, not its bbox: ${x.toFixed(1)},${y.toFixed(1)} (${e.toFixed(3)})`);
  }
  assert.equal(quadrants(px, 180, 180).size, 4, "points in all four quadrants of the ellipse");
  for (const p of path) assert.ok(p[2] >= 19.99 && p[2] <= 21.01, `on A's top: ${p}`);
});

test("closed pen loop on A: closed outline of 8..64 points along the loop", () => {
  const loop = Array.from({ length: 301 }, (_, i) => { const a = (2 * Math.PI * i) / 300; return [180 + 60 * Math.cos(a), 180 + 60 * Math.sin(a)]; });
  const s = run({ type: "pen", pts: loop });
  assert.equal(s.closed, true);
  const path = pathOf(s, "loop");
  assert.ok(path.length >= 8 && path.length <= MAX_PATH, `8..64 points: ${path.length}`);
  const px = path.map(pxOf);
  for (const q of px) assert.ok(Math.abs(Math.hypot(q[0] - 180, q[1] - 180) - 60) <= 2, `on the drawn loop: ${q}`);
  assert.equal(quadrants(px, 180, 180).size, 4, "round the whole loop");
});

// ---------------------------------------------------------------- open pen

const wavy = (x0, x1, y = 180) => Array.from({ length: x1 - x0 + 1 }, (_, i) => [x0 + i, y + 12 * Math.sin((x0 + i) / 6)]);

test("open pen: 2..64 points along the stroke, both ends included", () => {
  const pts = wavy(140, 240);   // on A
  const s = run({ type: "pen", pts });
  assert.ok(!s.closed);
  const path = pathOf(s, "stroke");
  assert.ok(path.length >= 2 && path.length <= MAX_PATH, `2..64 points: ${path.length}`);
  nearXY(path[0].slice(0, 2), toMm(...pts[0]), 0.3, "first point = where the stroke starts (xy)");
  nearXY(path[path.length - 1].slice(0, 2), toMm(...pts[pts.length - 1]), 0.3, "last point = where it ends (xy)");
  for (const p of path) assert.ok(distToPolyline(pxOf(p), pts) <= 2, `on the stroke: ${pxOf(p)}`);
});

test("open pen across the gap: each point over the gap lies on the plane of the nearest wall it continues", () => {
  const pts = wavy(140, 540);   // x 10 .. 110 mm: A, the gap (40..80), B
  const path = pathOf(run({ type: "pen", pts }), "stroke");
  assert.ok(path.length >= 2 && path.length <= MAX_PATH, `2..64 points: ${path.length}`);
  let nearA = 0, nearB = 0;
  for (const p of path) {
    assert.ok(distToPolyline(pxOf(p), pts) <= 2, `on the stroke: ${pxOf(p)}`);
    const x = p[0];
    if (x < 40) assert.ok(p[2] >= 19.99 && p[2] <= 21.01, `on A: ${p}`);
    else if (x > 80) assert.ok(p[2] >= 49.99 && p[2] <= 51.01, `on B: ${p}`);
    else if (x < 52) { nearA++; assert.ok(Math.abs(p[2] - 20) <= 1.01, `just past A's edge: continues A's top (z 20): ${p}`); }
    else if (x > 68) { nearB++; assert.ok(Math.abs(p[2] - 50) <= 1.01, `just before B's edge: continues B's top (z 50): ${p}`); }
    else assert.ok(Math.abs(p[2] - 20) <= 1.01 || Math.abs(p[2] - 50) <= 1.01, `mid-gap: on one of the two walls' planes: ${p}`);
  }
  assert.ok(nearA > 0 && nearB > 0, `the wavy stroke is followed over the gap (points near both edges: ${nearA}, ${nearB})`);
});

test("open pen starting in empty space: the leading misses continue the first wall hit", () => {
  const pts = wavy(20, 240);    // x -20 .. 35 mm: empty, then A
  const path = pathOf(run({ type: "pen", pts }), "stroke");
  nearXY(path[0].slice(0, 2), toMm(...pts[0]), 0.3, "first point xy");
  for (const p of path) assert.ok(Math.abs(p[2] - 20) <= 1.01, `on A's top plane: ${p}`);
});

// ---------------------------------------------------------------- text

test("text: path is one point, the anchor's at", () => {
  const s = run({ type: "text", pts: [[130, 150]], text: "gap" });
  const path = pathOf(s, "text");
  assert.equal(path.length, 1);
  assert.ok(!s.pts[0].miss, "setup: the anchor hits A");
  onSurface(path[0], s.pts[0].at, "anchor");
});

// ---------------------------------------------------------------- nothing hit -> flat

test("no sample hits the model: no path, flat: true", () => {
  for (const sh of [{ type: "line", pts: [[20, 20], [80, 60]] }, { type: "arrow", pts: [[300, 300], [380, 340]] },
    { type: "pen", pts: wavy(270, 410, 180) }, { type: "rect", pts: [[270, 120], [410, 240]] }, { type: "text", pts: [[20, 400]], text: "x" }]) {
    const s = run(sh);
    assert.equal(s.flat, true, `${sh.type} over empty space only: flat`);
    assert.ok(!("path" in s) || s.path == null, `${sh.type}: no path: ${JSON.stringify(s.path)}`);
  }
});

// ---------------------------------------------------------------- bounds

test("path stays within 64 points for very long or very big shapes", () => {
  const long = Array.from({ length: 5000 }, (_, i) => [110 + (i % 450), 110 + Math.floor(i / 450) * 12]);
  const loop = Array.from({ length: 5001 }, (_, i) => { const a = (2 * Math.PI * i) / 5000; return [340 + 230 * Math.cos(a), 180 + 70 * Math.sin(a)]; });
  for (const [name, sh, min] of [["long pen", { type: "pen", pts: long }, 2], ["long loop", { type: "pen", pts: loop }, 8],
    ["big rect", { type: "rect", pts: [[110, 110], [1900, 1000]] }, 8], ["big ellipse", { type: "ellipse", pts: [[0, 0], [1900, 1000]] }, 8]]) {
    const p = pathOf(run(sh), name);
    assert.ok(p.length >= min && p.length <= MAX_PATH, `${name}: ${p.length} points`);
    const pl = pathOf(run(sh, DOWN, { at: [20, 20, 20], normal: [0, 0, 1], part: A }), `${name} on a plane`);
    assert.ok(pl.length >= min && pl.length <= MAX_PATH, `${name} on a plane: ${pl.length} points`);
  }
});

test("a two-point pen stroke still has both ends", () => {
  const p = pathOf(run({ type: "pen", pts: [[150, 150], [230, 200]] }), "short stroke");
  assert.ok(p.length >= 2);
  nearXY(p[0].slice(0, 2), toMm(150, 150), 0.3, "first");
  nearXY(p[p.length - 1].slice(0, 2), toMm(230, 200), 0.3, "last");
});

// ---------------------------------------------------------------- drawing plane

const TOP_A = { at: [20, 20, 20], normal: [0, 0, 1], part: A };
function onPlane(p, plane, msg, tol = 0.05) {
  assert.ok(finite3(p), `${msg}: ${JSON.stringify(p)}`);
  const d = dot(sub(p, plane.at), unit(plane.normal));
  assert.ok(Math.abs(d) <= tol, `${msg}: ${d.toFixed(3)} mm off the plane: ${JSON.stringify(p)}`);
}
function onRay(p, r, msg) {
  const v = sub(p, r.origin), c = [v[1] * r.dir[2] - v[2] * r.dir[1], v[2] * r.dir[0] - v[0] * r.dir[2], v[0] * r.dir[1] - v[1] * r.dir[0]];
  assert.ok(Math.hypot(...c) / Math.hypot(...r.dir) <= 0.2 && dot(v, r.dir) > 0, `${msg}: not on its pixel's ray: ${JSON.stringify(p)}`);
}
function planePoint(p, plane, ray, msg) {
  assert.equal(p.plane, true, `${msg}: plane: true`);
  onPlane(p.at, plane, `${msg}.at`);
  onRay(p.at, ray(p.px[0], p.px[1]), `${msg}.at`);
  nearV(p.normal, plane.normal, 0.01, `${msg}.normal = the plane's normal`);
}

test("plane: a line from A over B and a line into empty space lie in A's top plane; ends beyond A are off", () => {
  for (const [name, end, under] of [["over B", [500, 180], B], ["into empty space", [20, 40], null]]) {
    const s = run({ type: "line", pts: [[180, 180], end] }, DOWN, TOP_A);
    assert.deepEqual(s.plane, TOP_A, `${name}: the shape carries its plane`);
    const path = pathOf(s, name);
    assert.equal(path.length, 2);
    path.forEach((p, i) => onPlane(p, TOP_A, `${name}: path[${i}]`));
    nearV(path[1], [...toMm(...end), 20], 0.05, `${name}: the end is ray ∩ plane, not the surface under it`);
    assert.ok(!s.flat);
    s.pts.forEach((p, i) => planePoint(p, TOP_A, rayDown, `${name}: pts[${i}] (${p.role})`));
    const first = s.pts[0], last = s.pts[s.pts.length - 1];
    assert.equal(first.part, A, `${name}: the start is on A (its surface is the plane point)`);
    assert.ok(!first.off, `${name}: the start is not off`);
    assert.equal(last.off, true, `${name}: the end is beyond A's edge`);
    assert.ok(!last.part, `${name}: an off point names no part (not even ${under}): ${JSON.stringify(last)}`);
  }
});

test("plane: a shape wholly over empty space still gets a path on the plane, never flat", () => {
  const s = run({ type: "pen", pts: wavy(20, 80, 180) }, DOWN, TOP_A);   // x -20 .. -5 mm
  assert.ok(!s.flat, "with a plane nothing is flat");
  const path = pathOf(s, "pen in the air");
  assert.ok(path.length >= 2 && path.length <= MAX_PATH);
  path.forEach((p, i) => onPlane(p, TOP_A, `path[${i}]`));
  for (const p of s.pts) { planePoint(p, TOP_A, rayDown, p.role); assert.equal(p.off, true); }
});

test("plane: rect, ellipse, text — every path and key point on the plane, inside counts as in stage 1", () => {
  for (const sh of [{ type: "rect", pts: [[200, 140], [520, 220]] }, { type: "ellipse", pts: [[120, 120], [560, 240]] },
    { type: "text", pts: [[130, 150]], text: "here" }]) {
    const s = run(sh, DOWN, TOP_A);
    const path = pathOf(s, sh.type);
    if (sh.type === "text") assert.equal(path.length, 1);
    else assert.ok(path.length >= 8 && path.length <= MAX_PATH, `${sh.type}: ${path.length}`);
    path.forEach((p, i) => onPlane(p, TOP_A, `${sh.type} path[${i}]`));
    s.pts.forEach((p, i) => planePoint(p, TOP_A, rayDown, `${sh.type} pts[${i}]`));
    const old = proj()(sh, DOWN.pick);
    if ("inside" in old) assert.deepEqual(s.inside, old.inside, `${sh.type}: inside counts keep their stage-1 meaning`);
  }
});

test("plane on A's vertical side face, slanted rays: points are ray ∩ that plane; below the part they are off", () => {
  const SIDE = { at: [40, 20, 10], normal: [1, 0, 0], part: A };
  const sc = scene(raySlant, [BOX_A]);
  // px 520 (x 105): the ray hits A's +X face at (40, 20, 13.3). px 600 (x 125): passes under A; plane point (40, 20, -13.3).
  const s = proj()({ type: "line", pts: [[520, 180], [600, 180]] }, sc.pick, { ray: sc.ray, plane: SIDE });
  assert.deepEqual(s.plane, SIDE);
  const path = pathOf(s, "line on the side face");
  nearV(path[0], [40, 20, 100 - 0.8 * (65 / 0.6)], 0.05, "start");
  nearV(path[1], [40, 20, 100 - 0.8 * (85 / 0.6)], 0.05, "end");
  s.pts.forEach((p, i) => planePoint(p, SIDE, raySlant, `pts[${i}]`));
  assert.equal(s.pts[0].part, A, "the start is on A's side face");
  assert.equal(s.pts[s.pts.length - 1].off, true, "the end is beyond the face's edge");
});

test("no plane given: no plane on the shape or its points", () => {
  const s = run({ type: "arrow", pts: [[180, 180], [500, 180]] });
  assert.ok(!("plane" in s) || s.plane == null);
  for (const p of s.pts) assert.ok(!p.plane && !p.off, JSON.stringify(p));
});
