// Mechanisms a machine once had to patch into its own copy of sim/motion.js, as behaviour of the SHARED motion module
// (a project page runs the template's motion.mjs, so what the patches did must live here, driven by motion.json data).
// Run: node --test skills/web-viewer/templates/assembly/motion_patches.test.mjs
//
// What is checked (motion.json -> what the CAD does), on a tiny synthetic scene, through mount({api, sim, cfg}) and api.tickers:
//   belt marks       flat, see-through, blue movement chevrons on every belt; `marks: false` on a belt hides them
//   kind "coupler"   (in a drive's `also`) four-bar rod: pin a rides crank a, pin b stays on crank b's circle, crank b follows the rod
//   kind "stretch"   spring: end a_pt fixed, end b_pt rides node b
//   linear offset_mm the item sits offset_mm along its axis from page load, on top of the steps, and again after Reset
//   grip             vacuum actuator with grip {tips, items, reach_mm}: rising edge picks the item under a tip, it rides the tip, falling edge drops it
import test from "node:test";
import assert from "node:assert/strict";
import { registerHooks } from "node:module";

const THREE_URL = new URL("./vendor/three.module.min.js", import.meta.url).href;
registerHooks({ resolve: (spec, ctx, next) => (spec === "three" ? { url: THREE_URL, shortCircuit: true } : next(spec, ctx)) });
const THREE = await import("three");
const { mount } = await import("./motion.mjs");

// ---- minimal browser surface motion.mjs touches
let MOTION = null;
const el = () => ({ style: {}, classList: { toggle() {}, add() {}, remove() {} }, appendChild() {}, insertAdjacentHTML() {}, textContent: "" });
globalThis.location = { href: "http://test.local/demo-proj/" };
globalThis.fetch = async () => ({ ok: true, json: async () => structuredClone(MOTION) });
globalThis.document = { getElementById: () => null, createElement: el, head: el(), body: el(), querySelector: () => null };
globalThis.window = { innerWidth: 1200, innerHeight: 800 };

const MM = 0.001;
function node(parent, name, pos = [0, 0, 0], size = null) {
  const o = size ? new THREE.Mesh(new THREE.BoxGeometry(...size.map((s) => s * MM)), new THREE.MeshStandardMaterial({ color: 0x808080 })) : new THREE.Group();
  o.name = name; o.position.set(...pos.map((v) => v * MM)); o.updateMatrix(); o.matrixAutoUpdate = false;
  if (o.isMesh) o.userData.base = o.material;
  parent.add(o);
  return o;
}

async function rig(motion, buildScene) {
  MOTION = motion;
  const model = new THREE.Group(); model.matrixAutoUpdate = false;
  const n = buildScene(model);
  model.updateMatrixWorld(true);
  const home = new Map(); model.traverse((o) => home.set(o, o.matrixWorld.clone()));
  let acts = {}, now = 1000;
  const api = { model, invalidate() {}, tickers: [], selectHooks: [], nodes: [], camera: new THREE.PerspectiveCamera() };
  const sim = { vm: { resets: 0, m: {} }, actuators: () => acts };
  const m = await mount({ api, sim, cfg: { motion: { file: "motion.json" } } });
  return {
    m, model, n, sim,
    set(a) { acts = a; },
    tick(k = 1) { for (let i = 0; i < k; i++) { now += 50; api.tickers.forEach((f) => f(now)); } model.updateMatrixWorld(true); },
    // where the material point that sat at world X (mm) at load time is now, in mm
    at(o, X) { model.updateMatrixWorld(true); const p = new THREE.Vector3(...X.map((v) => v * MM)).applyMatrix4(home.get(o).clone().invert()).applyMatrix4(o.matrixWorld); return [p.x / MM, p.y / MM, p.z / MM]; },
  };
}
const near = (a, b, tol, msg) => assert.ok(a.every((v, i) => Math.abs(v - b[i]) <= tol), `${msg}: got [${a.map((v) => v.toFixed(2))}], want [${b.map((v) => v.toFixed(2))}]`);
const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
const stepper = (steps, pos = steps) => ({ kind: "stepper", steps, pos, vel: 0, on: false });
const valve = (on) => ({ kind: "binary", steps: 0, pos: 0, vel: 0, on });

// ---------------------------------------------------------------- belt marks
const beltScene = (model) => ({ belt: node(model, "belt", [0, 0, 0], [1000, 10, 200]) });
const markMeshes = (model) => { const g = model.getObjectByName("motion-marks"); const r = []; if (g) g.traverse((o) => { if (o.isMesh && o.visible) r.push(o); }); return r; };

test("belt marks are flat, see-through, blue chevrons", async () => {
  const r = await rig({ actuators: { conv: { kind: "belt", nodes: ["belt"], axis: [1, 0, 0], per_step: 0.1 } } }, beltScene);
  const marks = markMeshes(r.model);
  assert.ok(marks.length > 0, "a belt gets movement marks");
  for (const im of marks) {
    const mat = im.material;
    assert.ok(mat.transparent && mat.opacity < 1, `marks are see-through (transparent=${mat.transparent}, opacity=${mat.opacity})`);
    const c = mat.color;
    assert.ok(c.b > c.r && c.b > c.g && c.b > 0.5, `marks are blue (rgb ${c.r.toFixed(2)} ${c.g.toFixed(2)} ${c.b.toFixed(2)})`);
    im.geometry.computeBoundingBox();
    const s = im.geometry.boundingBox.getSize(new THREE.Vector3());
    assert.ok(Math.min(s.x, s.y, s.z) < 1e-9, `marks are flat shapes, not boxes (geometry extents ${s.x} ${s.y} ${s.z})`);
  }
});

test("marks: false on a belt shows no marks", async () => {
  const r = await rig({ actuators: { conv: { kind: "belt", nodes: ["belt"], axis: [1, 0, 0], per_step: 0.1, marks: false } } }, beltScene);
  assert.equal(markMeshes(r.model).length, 0, "no visible mark mesh");
  assert.equal(r.m.marks()[0].n, 0, "marks() reports none on that belt");
});

// ---------------------------------------------------------------- coupler (four-bar rod)
// crank a turns about (0,0,0) z, its pin at (50,0,0); crank b turns about (200,0,0) z, its pin at (200,80,0); the rod joins the two pins (length 170).
const fourBar = (model) => {
  const asm = node(model, "asm");
  return { crankA: node(asm, "crankA", [25, 0, 0], [50, 8, 8]), crankB: node(asm, "crankB", [200, 40, 0], [8, 80, 8]), rod: node(asm, "rod", [125, 40, 0], [150, 6, 6]) };
};
const FOUR_BAR = { actuators: { drv: { kind: "rotary", nodes: ["asm/crankA"], axis: [0, 0, 1], pivot: [0, 0, 0], per_step: 1, also: [
  { kind: "rotary", nodes: ["asm/crankB"], axis: [0, 0, 1], pivot: [200, 0, 0], per_step: 0 },
  { kind: "coupler", nodes: ["asm/rod"], a: ["asm/crankA"], b: ["asm/crankB"], a_pt: [50, 0, 0], b_pt: [200, 80, 0] }] } } };

test("coupler: the rod's pin a rides crank a, pin b stays on crank b's circle, crank b follows", async () => {
  const r = await rig(FOUR_BAR, fourBar);
  r.set({ drv: stepper(30) }); r.tick(2);                      // crank a turned 30 degrees
  const pinA = r.at(r.n.crankA, [50, 0, 0]);
  near(pinA, [50 * Math.cos(Math.PI / 6), 50 * Math.sin(Math.PI / 6), 0], 0.01, "crank a pin turned 30 deg (sanity)");
  near(r.at(r.n.rod, [50, 0, 0]), pinA, 0.05, "rod end a sits on crank a's pin");
  const rodB = r.at(r.n.rod, [200, 80, 0]);
  assert.ok(Math.abs(dist(rodB, [200, 0, 0]) - 80) < 0.05, `rod end b stays on crank b's circle (r=${dist(rodB, [200, 0, 0]).toFixed(2)}, want 80)`);
  assert.ok(dist(rodB, [200, 80, 0]) > 1, "rod end b moved with the linkage");
  near(r.at(r.n.crankB, [200, 80, 0]), rodB, 0.05, "crank b turned to follow the rod");
});

// ---------------------------------------------------------------- stretch (spring)
const springScene = (model) => { const asm = node(model, "asm"); return { slider: node(asm, "slider", [100, 0, 0], [10, 10, 10]), spring: node(asm, "spring", [50, 0, 0], [100, 4, 4]) }; };

test("stretch: the spring keeps end a fixed and its end b rides node b", async () => {
  const r = await rig({ actuators: { lift: { kind: "linear", nodes: ["asm/slider"], axis: [0, 1, 0], per_step: 1, also: [
    { kind: "stretch", nodes: ["asm/spring"], b: ["asm/slider"], a_pt: [0, 0, 0], b_pt: [100, 0, 0] }] } } }, springScene);
  r.set({ lift: stepper(20) }); r.tick(2);                      // slider up 20 mm
  near(r.at(r.n.slider, [100, 0, 0]), [100, 20, 0], 0.01, "slider moved 20 mm (sanity)");
  near(r.at(r.n.spring, [0, 0, 0]), [0, 0, 0], 0.05, "spring end a stays put");
  near(r.at(r.n.spring, [100, 0, 0]), [100, 20, 0], 0.05, "spring end b follows the slider");
});

// ---------------------------------------------------------------- linear offset_mm
const cylScene = (model) => ({ cyl: node(model, "cyl", [0, 0, 0], [20, 60, 20]) });

test("linear offset_mm: applied from page load, added to the steps, restored after reset", async () => {
  const r = await rig({ actuators: { head: { kind: "linear", nodes: ["cyl"], axis: [0, 1, 0], per_step: 1, offset_mm: -30 } } }, cylScene);
  r.set({ head: stepper(0) }); r.tick(1);
  near(r.at(r.n.cyl, [0, 0, 0]), [0, -30, 0], 0.01, "at page load the item sits offset_mm along its axis");
  r.set({ head: stepper(10) }); r.tick(1);
  near(r.at(r.n.cyl, [0, 0, 0]), [0, -20, 0], 0.01, "steps move it from the offset pose");
  r.sim.vm.resets++; r.set({ head: stepper(0) }); r.tick(1);
  near(r.at(r.n.cyl, [0, 0, 0]), [0, -30, 0], 0.01, "after Reset it is back at the offset pose, not at the CAD pose");
});

// ---------------------------------------------------------------- vacuum grip
const gripScene = (model) => { const head = node(model, "head"); return { tip: node(head, "tip", [0, 100, 0], [20, 20, 20]), tray: node(model, "tray", [0, 80, 0], [100, 10, 100]) }; };
const GRIP = { actuators: {
  head: { kind: "linear", nodes: ["head"], axis: [0, 1, 0], per_step: 1 },
  sucker: { kind: "binary", nodes: [], grip: { tips: ["head/tip"], items: ["tray"], reach_mm: 70 } } } };

test("grip: vacuum on picks the tray under the tip, it rides the tip, vacuum off drops it", async () => {
  const r = await rig(GRIP, gripScene);
  r.set({ head: stepper(0), sucker: valve(false) }); r.tick(2);
  near(r.at(r.n.tray, [0, 80, 0]), [0, 80, 0], 0.01, "tray at rest before the vacuum");
  r.set({ head: stepper(0), sucker: valve(true) }); r.tick(2);  // rising edge: pick
  r.set({ head: stepper(50), sucker: valve(true) }); r.tick(2); // head up 50 mm
  near(r.at(r.n.tip, [0, 100, 0]), [0, 150, 0], 0.01, "tip went up 50 mm (sanity)");
  near(r.at(r.n.tray, [0, 80, 0]), [0, 130, 0], 0.05, "held tray rides the tip");
  r.set({ head: stepper(50), sucker: valve(false) }); r.tick(2); // falling edge: release
  r.set({ head: stepper(0), sucker: valve(false) }); r.tick(2);  // head back down
  near(r.at(r.n.tray, [0, 80, 0]), [0, 130, 0], 0.05, "released tray stays where it was dropped");
});

test("grip: an item out of reach is not picked", async () => {
  const r = await rig({ actuators: { ...GRIP.actuators, sucker: { ...GRIP.actuators.sucker, grip: { ...GRIP.actuators.sucker.grip, reach_mm: 5 } } } }, gripScene);
  r.set({ head: stepper(0), sucker: valve(false) }); r.tick(2);
  r.set({ head: stepper(0), sucker: valve(true) }); r.tick(2);
  r.set({ head: stepper(50), sucker: valve(true) }); r.tick(2);
  near(r.at(r.n.tray, [0, 80, 0]), [0, 80, 0], 0.01, "tray 20 mm away with reach 5 mm stays");
});
