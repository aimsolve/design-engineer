// Motion binder of the assembly viewer: makes the CAD move from the simulated actuators, driven ONLY by the machine's motion.json (schema: MOTION.md of the
// machine repo; summary in skills/web-viewer/SKILL.md). Loaded by sim_ui.mjs when sim/config.json has "motion".
//
//   cfg.motion = { file: "motion.json", id_map: { "<motion.json id>": "<sim actuators() id>" }, labels: [{ node, source, unit, order }] }
//
// Frames: motion.json pivots/axes are in the GLB frame x1000 (mm). The three.js scene of the model is `model` (gltf.scene, metres); every transform is built
// in that frame and applied ON TOP of the node's own home matrix (the GLB stores int16-quantised positions with a dequant matrix on instance nodes):
//     local' = inv(Wp) * M * Wp * local        Wp = home world matrix of the node's parent (model frame), M = motion in the model frame.
// A node below a node that moves too rides on it through the scene graph; `parent` in motion.json is composed explicitly only when the node is NOT a
// descendant of the parent's nodes. Nothing moves until an actuator has produced its first STEP edge; Reset (vm.resets changes) puts every node home.
import * as THREE from "three";
import { DICT } from "./i18n.mjs";   // fallback texts when the caller passes no L (the page always does)

// ---------------------------------------------------------------------------------------------------------------------------------------------------------
// CYCLE kind (code-CAD machines, no simulator): motion.json carries a `cycle` block written by skills/web-viewer/scripts/cycle_from_params.py
//   cycle = { period_s, loop, groups: { name: { kind?: "linear"|"rotary", axis: [x,y,z] | "x|y|z", travel_mm (linear) | travel_deg + pivot_mm (rotary), parent?,
//                                              parts?: [part ids], segments: [[t0, t1, f0, f1], ...] } } }
// A part belongs to a group through parts.json `motion_group` (step-import --motion-groups) or cycle.groups[g].parts. Position of a group at time t = frac(g, t) * travel
// along its axis, on top of the part's home matrix; a group with `parent` rides on the parent's motion (M = M_parent * M_own). frac: linear inside a segment, holds the
// last value between segments, and before the first segment holds the LAST segment's end (the cycle wraps). t = (now * speed) % period_s; Play/Pause, speed, scrub, #t=<s>.
// Axes/pivots are in the GLB frame in mm (the importer keeps the CAD frame); `model` is in metres.
// ---------------------------------------------------------------------------------------------------------------------------------------------------------
export function cycleFrac(segs, t) {
  if (!segs || !segs.length) return 0;
  let f = segs[segs.length - 1][3];
  for (const s of segs) {
    if (t < s[0]) break;
    f = s[2] + (s[3] - s[2]) * Math.min(1, (t - s[0]) / Math.max(1e-9, s[1] - s[0]));
  }
  return f;
}

export async function mountCycle(ctx) {
  const { api, L, $, PARTS, BYPART, narrow } = ctx, C = (L && L.cycle) || {};
  const motion = await fetch(new URL(ctx.file || "motion.json", location.href)).then((r) => r.json());
  const cy = motion.cycle; if (!cy || !cy.groups) return null;
  const period = cy.period_s, model = api.model, invalidate = api.invalidate;
  model.updateMatrixWorld(true);
  const invF = new THREE.Matrix4().copy(model.matrixWorld).invert();
  const AX = { x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1] };
  const G = {};
  for (const [name, g] of Object.entries(cy.groups)) {
    const a = typeof g.axis === "string" ? AX[g.axis.toLowerCase()] : g.axis;
    G[name] = { name, g, axis: new THREE.Vector3(...a).normalize(), rotary: g.kind === "rotary", pivot: g.pivot_mm ? new THREE.Vector3(...g.pivot_mm).multiplyScalar(0.001) : null, recs: [], m: new THREE.Matrix4(), own: new THREE.Matrix4(), f: 0 };
  }
  const warn = [];
  for (const [name, d] of Object.entries(G)) if (d.g.parent && !G[d.g.parent]) warn.push(`${name}: parent ${d.g.parent} unknown`);
  const addRec = (d, o) => { const wp = new THREE.Matrix4().multiplyMatrices(invF, o.parent.matrixWorld); d.recs.push({ obj: o, home: o.matrix.clone(), wp, invWp: wp.clone().invert() }); };
  const claimed = new Set();
  // cycle.node_groups {instance name: group | "static"}: a code-CAD export reuses one shape (a hub, a rod end, a rail
  // block) in several groups, so the per-part motion_group cannot tell its instances apart; the GLB node keeps the
  // instance name ("cup_hub - Rigid flange coupling ..."), three sanitises it ("cup_hub_-_Rigid_..."), match on the part before " - ".
  const san = (s) => THREE.PropertyBinding.sanitizeNodeName(s);
  const NODE = new Map(Object.entries(cy.node_groups || {}).map(([k, v]) => [san(k.split(" - ")[0]), v]));
  const nodeGroup = (o) => {
    if (!NODE.size) return undefined;
    const n = o.name.split("_-_")[0];
    return NODE.has(n) ? NODE.get(n) : NODE.get(n.replace(/_\d+$/, ""));   // GLTFLoader appends _1, _2 to repeated names
  };
  for (const id of Object.keys(BYPART)) {
    const partName = (PARTS[id] && PARTS[id].motion_group) || Object.keys(G).find((k) => (G[k].g.parts || []).includes(id));
    for (const o of BYPART[id]) {
      // the GLB node's own group (extras.motion_group, a converted client tree: copies of one part in different groups)
      const ng = nodeGroup(o) ?? (o.userData && o.userData.motion_group);
      const name = ng === undefined ? partName : (ng === "static" ? null : ng);
      if (!name || !G[name]) continue;
      addRec(G[name], o); claimed.add(o);
    }
  }
  const moving = Object.values(G).filter((d) => d.recs.length);
  const T = new THREE.Matrix4(), T2 = new THREE.Matrix4(), work = new THREE.Matrix4();
  const ownM = (d, f) => {
    if (d.rotary) {
      const p = d.pivot || new THREE.Vector3();
      T.makeTranslation(p.x, p.y, p.z); T2.makeTranslation(-p.x, -p.y, -p.z);
      return d.own.makeRotationAxis(d.axis, f * (d.g.travel_deg || 0) * Math.PI / 180).premultiply(T).multiply(T2);
    }
    const mm = f * (d.g.travel_mm || 0) * 0.001;
    return d.own.makeTranslation(d.axis.x * mm, d.axis.y * mm, d.axis.z * mm);
  };
  const totalM = (d, depth = 0) => {   // parent chain, M = M_parent * M_own
    d.m.copy(ownM(d, d.f));
    const par = d.g.parent && G[d.g.parent];
    if (par && depth < 8) d.m.premultiply(totalM(par, depth + 1).clone());
    return d.m;
  };
  function setTime(t) {
    t = ((t % period) + period) % period; state.t = t;
    for (const d of Object.values(G)) d.f = cycleFrac(d.g.segments, t);
    for (const d of moving) {
      totalM(d);
      for (const r of d.recs) { r.obj.matrix.multiplyMatrices(r.invWp, d.m).multiply(r.wp).multiply(r.home); r.obj.matrixWorldNeedsUpdate = true; }
    }
    if (world && world.root.visible) world.update(t);
    update(); invalidate();
  }
  const state = { t: 0, playing: true, speed: 1 };
  // ---- the machine's product (build.py --world): what the machine works on - products, packaging, glue - drawn by the project's world module in mm of the
  // CAD frame. The module exports mount({THREE, root, data, period, frac, node, api}) -> {update(t)}; it is called on every cycle time change.
  let world = null;
  const conf = api.config || {}, dataUrl = api.dataUrl || ((f) => f);   // config.json as the page read it; data URLs carry its build stamp
  if (conf.world) {
    try {
      const wm = await import(new URL(dataUrl(conf.world.module), location.href).href);
      const data = conf.world.data ? await fetch(new URL(dataUrl(conf.world.data), location.href)).then((r) => r.json()) : null;
      const root = new THREE.Group(); root.name = "world"; root.scale.setScalar(0.001); model.add(root);
      const node = (name) => { let hit = null; model.traverse((o) => { if (!hit && o.name.split("_-_")[0] === san(name)) hit = o; }); return hit; };
      const hook = await wm.mount({ THREE, root, data, period, frac: (g, t) => cycleFrac(cy.groups[g] && cy.groups[g].segments, t), node, api });
      root.traverse((o) => { o.raycast = () => {}; });   // never picked: a click goes to the machine part behind it
      world = { root, update: (hook && hook.update) || (() => {}), label: conf.world.label || C.world || (L.motion || DICT.ru.motion).world };
    } catch (err) { console.error(err); warn.push("world: " + err); }
  }
  // hash: #t=<s> freezes the cycle
  const hashT = (h = location.hash) => { const m = /[#&]t=([0-9.]+)/.exec(h); return m ? parseFloat(m[1]) : null; };
  api.hashExtra = (api.hashExtra || []).concat([() => (state.playing ? "" : "t=" + Math.round(state.t * 1000) / 1000)]);

  // ---- bar ----
  const css = document.createElement("style");
  css.textContent = `#cyclebar{position:fixed;z-index:4;left:392px;right:16px;bottom:14px;display:flex;justify-content:center;pointer-events:none}
#cyclebar .bar{pointer-events:auto;display:flex;align-items:center;gap:6px;flex-wrap:wrap;justify-content:center;padding:5px 12px;background:var(--panel);backdrop-filter:blur(6px);border:1px solid var(--line);border-radius:22px}
#cyclebar button{padding:3px 8px;border-radius:14px;font-size:12px}#cyclebar button.on{background:var(--acc);color:#fff}
#cyclebar input[type=range]{width:min(340px,40vw)}#cyclebar .clk{font:600 12px ui-monospace,Menlo,monospace;min-width:108px;text-align:center}
@media(max-width:700px){#cyclebar{left:8px;right:8px;bottom:8px}}`;
  document.head.appendChild(css);
  const bar = document.createElement("div"); bar.id = "cyclebar";
  bar.innerHTML = `<div class="bar" title="${C.tip || ""}"><button id="cyPlay"></button><span id="cySpeeds" style="display:inline-flex;gap:4px"></span>
    <input id="cyScrub" type="range" min="0" max="${period}" step="0.001" value="0" aria-label="${C.t || "t"}"><span class="clk" id="cyClk"></span></div>`;
  document.body.appendChild(bar);
  const $p = (id) => bar.querySelector("#" + id), speeds = [0.25, 1, 2];
  for (const v of speeds) { const b = document.createElement("button"); b.dataset.speed = v; b.textContent = "×" + v; b.onclick = () => { state.speed = v; update(); }; $p("cySpeeds").appendChild(b); }
  function update() {
    $p("cyPlay").textContent = state.playing ? C.pause || "Pause" : C.play || "Play";
    $p("cyScrub").value = state.t; $p("cyClk").textContent = state.t.toFixed(2).replace(".", L.decimal || ".") + " / " + period.toFixed(2).replace(".", L.decimal || ".") + " " + (C.s || DICT.ru.cycle.s);
    bar.querySelectorAll("[data-speed]").forEach((b) => b.classList.toggle("on", +b.dataset.speed === state.speed));
  }
  const setPlaying = (p) => { state.playing = p; last = performance.now(); update(); invalidate(); };
  $p("cyPlay").onclick = () => setPlaying(!state.playing);
  if (world) {   // a plain checkbox in the panel, under "View", saying in the project's words what it shows
    const row = document.createElement("label"); row.className = "row sub"; row.id = "worldRow"; row.style.cssText = "gap:6px;align-items:center;cursor:pointer";
    row.innerHTML = `<input type="checkbox" id="cyWorld" checked> <span></span>`; row.querySelector("span").textContent = world.label;
    const vr = document.getElementById("viewRow"); vr ? vr.after(row) : bar.querySelector(".bar").appendChild(row);
    row.querySelector("input").onchange = (e) => { world.root.visible = e.target.checked; setTime(state.t); };
  }
  $p("cyScrub").addEventListener("input", (e) => { state.playing = false; setTime(+e.target.value); });
  let last = performance.now();
  api.tickers.push((now) => {
    const dt = Math.min(0.1, (now - last) / 1000); last = now;
    if (state.playing) setTime(state.t + dt * state.speed);
  });
  const fromHash = () => { const h = hashT(); if (h != null && isFinite(h)) { state.playing = false; setTime(h); } };
  window.addEventListener("hashchange", fromHash);
  setTime(0); update();
  { const h = hashT(ctx.hash0 || ""); if (h != null && isFinite(h)) { state.playing = false; setTime(h); } else fromHash(); }
  if (warn.length) console.warn("[cycle] " + warn.join("; "));
  api.cycle = { state: () => ({ ...state, period, groups: moving.map((d) => ({ name: d.name, parts: d.recs.length, f: d.f })) }), setTime: (t) => { state.playing = false; setTime(t); }, play: () => setPlaying(true), pause: () => setPlaying(false), frac: (g, t) => cycleFrac(cy.groups[g] && cy.groups[g].segments, t), warn };
  return api.cycle;
}

const RAD = Math.PI / 180, BINARY_MS = 150;
const norm = (s) => String(s).replace(/ /g, "_");
const nm = (o) => norm(o.userData && o.userData.name != null ? o.userData.name : o.name);

export async function mount(ctx) {
  const { api, sim, cfg } = ctx, mc = cfg.motion || {}, L = ctx.L || {};
  const model = api.model, invalidate = api.invalidate;
  const base = new URL("./", location.href);
  const motion = await fetch(new URL((api.dataUrl || ((f) => f))(`sim/${mc.file || "motion.json"}`), base)).then((r) => r.json());
  const idMap = mc.id_map || {};
  const unresolved = [];
  model.updateMatrixWorld(true);
  const invF = new THREE.Matrix4().copy(model.matrixWorld).invert();

  // ---------- path -> node ----------
  const seg = (s) => { const m = /^(.*)\[(\d+)\]$/.exec(s); return m ? [norm(m[1]), +m[2]] : [norm(s), 0]; };
  const resolve = (path) => {
    let cur = null;
    for (const s of path.split("/")) {
      const [name, k] = seg(s);
      const kids = (cur ? cur.children : model.children).filter((c) => nm(c) === name);
      if (k >= kids.length) return null;
      cur = kids[k];
    }
    return cur;
  };

  // ---------- clones: mechanisms missing from the CAD (motion.json "clones") ----------
  // {id, actuator, from: [node paths], offset_mm: [dx,dy,dz] (GLB frame), label, note}: each `from` node is cloned (geometry shared) as the LAST sibling of the same name under
  // the same parent, moved by the offset, and drawn as a translucent amber ghost; the part card says `note`. The actuator's `nodes` address the clone by its path (same-name index
  // = existing siblings + clones added earlier, in `clones`/`from` order), so it is driven like any CAD node.
  const ghosts = new WeakMap(), ghostMeshes = [], cloneRoots = [];
  const ghostOf = (b) => { let g = ghosts.get(b); if (!g) { g = b.clone(); g.transparent = true; g.opacity = 0.55; g.depthWrite = false; if (g.emissive) { g.emissive = new THREE.Color(0xffa030); g.emissiveIntensity = 0.3; } g.userData = { ghost: true }; ghosts.set(b, g); } return g; };
  for (const c of motion.clones || []) {
    const off = new THREE.Vector3(...(c.offset_mm || [0, 0, 0])).multiplyScalar(0.001);
    for (const path of c.from || []) {
      const src = resolve(path);
      if (!src) { unresolved.push(`clone ${c.id}: ${path}`); continue; }
      const par = src.parent, srcs = [], stash = [];
      src.traverse((o) => { srcs.push(o); if (o.userData.base) { stash.push([o, o.userData.base]); delete o.userData.base; } });   // Object3D.clone() JSON-copies userData
      const cl = src.clone(true);
      stash.forEach(([o, b]) => { o.userData.base = b; });
      const clones = []; cl.traverse((o) => clones.push(o));
      const Wp = new THREE.Matrix4().multiplyMatrices(invF, par.matrixWorld);
      cl.matrix.copy(new THREE.Matrix4().copy(Wp).invert().multiply(new THREE.Matrix4().makeTranslation(off.x, off.y, off.z)).multiply(Wp).multiply(src.matrix));
      cl.matrixAutoUpdate = false; par.add(cl); cloneRoots.push(cl);
      clones.forEach((o, i) => {
        o.userData.n = api.nodes.length; api.nodes.push(o); o.userData.notInCad = { id: c.id, label: c.label, note: c.note }; o.matrixAutoUpdate = false;
        if (o.isMesh) { o.userData.base = srcs[i].userData.base; ghostMeshes.push(o); if (api.byPart) (api.byPart[o.userData.part] = api.byPart[o.userData.part] || []).push(o); }
      });
    }
  }
  if (cloneRoots.length) {
    model.updateMatrixWorld(true);
    api.tickers.push(() => {   // (re)apply the ghost look whenever the viewer resets a mesh's base material (colour mode switch)
      let ch = false;
      for (const m of ghostMeshes) { const b = m.userData.base; if (b && !b.userData.ghost) { const g = ghostOf(b); if (m.material === b) m.material = g; m.userData.base = g; ch = true; } }
      if (ch) invalidate();
    });
    api.selectHooks.push((o) => {
      const n = o && o.userData.notInCad; if (!n) return;
      const t = document.getElementById("infoT"), p = document.getElementById("infoP");
      const M = L.motion || DICT.ru.motion;
      if (t && !t.textContent.endsWith(" — " + M.noCad)) t.textContent += " — " + M.noCad;
      if (p) p.insertAdjacentHTML("afterbegin", `<dt>${M.source}</dt><dd style="color:#b54708;font-weight:600">${n.note}</dd>`);
    });
  }

  // ---------- drives ----------
  const drives = [], byMotionId = {};
  const P = (a) => (a ? new THREE.Vector3(a[0], a[1], a[2]).multiplyScalar(0.001) : null);
  const A = (a) => (a ? new THREE.Vector3(a[0], a[1], a[2]).normalize() : null);
  function makeItem(spec, drive, isMain) {
    const it = { spec, kind: spec.kind, axis: A(spec.axis), pivot: P(spec.pivot), recs: [], main: isMain, drive };
    for (const path of spec.nodes || []) {
      const o = resolve(path);
      if (!o) { unresolved.push(`${drive.id}: ${path}`); continue; }
      it.recs.push({ obj: o, path, home: o.matrix.clone(), wp: null, invWp: null, item: it });
      if (spec.kind === "coupler") {   // four-bar rod: pin a_pt rides on node a, the rod points at pin b_pt on node b
        const i = spec.nodes.indexOf(path), r = it.recs[it.recs.length - 1];
        Object.assign(r, { a: spec.a[i], b: spec.b[i], a0: P(spec.a_pt.slice(3 * i, 3 * i + 3)), b0: P(spec.b_pt.slice(3 * i, 3 * i + 3)) });
      }
      if (spec.kind === "stretch") {   // spring: end a_pt fixed, end b_pt rides on node b; stretched along a->b
        const i = spec.nodes.indexOf(path), r = it.recs[it.recs.length - 1];
        Object.assign(r, { b: spec.b[i], a0: P(spec.a_pt.slice(3 * i, 3 * i + 3)), b0: P(spec.b_pt.slice(3 * i, 3 * i + 3)) });
      }
    }
    return it;
  }
  for (const [id, spec] of Object.entries(motion.actuators || {})) {
    const simId = idMap[id] || id;
    const d = { id, simId, spec, items: [], engaged: false, f: 0, target: 0, p: 0, sig: "", carries: [], parent: null, marks: null, applied: false };
    d.items.push(makeItem(spec, d, true));
    for (const s of spec.also || []) d.items.push(makeItem(s, d, false));
    for (const path of spec.carries || []) { const o = resolve(path); if (o) d.carries.push({ obj: o, path, home: o.matrix.clone() }); else unresolved.push(`${id}: carries ${path}`); }
    drives.push(d); byMotionId[id] = d;
  }
  for (const d of drives) if (d.spec.parent) { d.parent = byMotionId[d.spec.parent] || null; if (!d.parent) unresolved.push(`${d.id}: parent ${d.spec.parent} unknown`); }
  const allRecs = [];
  for (const d of drives) {
    for (const it of d.items) for (const r of it.recs) allRecs.push(r);
    for (const c of d.carries) allRecs.push(c);
  }
  for (const r of allRecs) {   // home parent world, model frame
    r.wp = new THREE.Matrix4().multiplyMatrices(invF, r.obj.parent.matrixWorld);
    r.invWp = r.wp.clone().invert();
  }
  const isAncestorMoved = (obj, drive) => {   // does any node of `drive` sit above obj?
    const set = drive._objs || (drive._objs = new Set(drive.items.flatMap((it) => it.recs.map((r) => r.obj))));
    for (let p = obj.parent; p; p = p.parent) if (set.has(p)) return true;
    return false;
  };
  for (const d of drives) for (const it of d.items) for (const r of it.recs) r.composeParent = !!(d.parent && !isAncestorMoved(r.obj, d.parent));

  // ---------- belts: marks, extents ----------
  const up = new THREE.Vector3(0, 1, 0);
  const tmpB = new THREE.Box3(), tmpM = new THREE.Matrix4();
  const boxIn = (obj) => {   // box of every mesh under obj, model frame
    const b = new THREE.Box3();
    obj.traverse((m) => { if (!m.isMesh) return; if (!m.geometry.boundingBox) m.geometry.computeBoundingBox(); tmpM.multiplyMatrices(invF, m.matrixWorld); b.union(tmpB.copy(m.geometry.boundingBox).applyMatrix4(tmpM)); });
    return b;
  };
  const markGroup = new THREE.Group(); markGroup.name = "motion-marks"; model.add(markGroup);
  // belt movement indicator: flat, see-through blue chevrons pointing along the top-run travel, so nobody takes them for parts of the machine
  const markMat = new THREE.MeshBasicMaterial({ color: 0x1e9bff, transparent: true, opacity: 0.55, depthWrite: false, side: THREE.DoubleSide });
  const chevGeo = (() => { const sh = new THREE.Shape(); sh.moveTo(0.5, 0); sh.lineTo(-0.1, 0.5); sh.lineTo(-0.5, 0.5); sh.lineTo(0.1, 0); sh.lineTo(-0.5, -0.5); sh.lineTo(-0.1, -0.5); sh.closePath();
    const g = new THREE.ShapeGeometry(sh); g.rotateX(-Math.PI / 2); return g; })();   // unit chevron in the x(travel)-z(width) plane, tip at +x
  for (const d of drives) {
    if (d.spec.kind !== "belt" || !d.items[0].recs.length) continue;
    const belt = d.items[0].recs[0].obj, a = A(d.spec.axis), w = new THREE.Vector3().crossVectors(a, up).normalize();
    const b = boxIn(belt), R = (d.spec.roller_radius_mm || 20) * 0.001;
    let umin = 1e9, umax = -1e9, wmin = 1e9, wmax = -1e9;
    for (let i = 0; i < 8; i++) { const c = new THREE.Vector3(i & 1 ? b.max.x : b.min.x, i & 2 ? b.max.y : b.min.y, i & 4 ? b.max.z : b.min.z); umin = Math.min(umin, c.dot(a)); umax = Math.max(umax, c.dot(a)); wmin = Math.min(wmin, c.dot(w)); wmax = Math.max(wmax, c.dot(w)); }
    d.belt = { a, w, umin, umax, top: b.max.y };
    const pitch = (d.spec.mark_pitch_mm || (typeof d.spec.marks === "number" ? d.spec.marks : 150)) * 0.001, len = 0.03, usable = umax - umin - 2 * (R + len);
    const n = Math.max(2, Math.floor(usable / pitch)); d.belt.pitch = pitch; d.belt.n = n; d.belt.lo = umin + R + len; d.belt.span = n * pitch;
    const im = new THREE.InstancedMesh(chevGeo, markMat, n);
    im.frustumCulled = false; im.userData.mark = true; im.raycast = () => {};
    d.belt.im = im; d.belt.wc = (wmin + wmax) / 2; d.belt.ww = (wmax - wmin) * 0.4; d.belt.len = len;
    markGroup.add(im);
    // spec.marks === false: no movement indicator on this belt
    if (d.spec.marks === false) { im.visible = false; d.belt.n = 0; }
    // the belt top run: marks sit 0.8 mm above it
    d.belt.basis = new THREE.Matrix4().makeBasis(a, up, new THREE.Vector3().crossVectors(a, up));
    if (d.carries.length) {
      for (const c of d.carries) c.u0 = boxIn(c.obj).getCenter(new THREE.Vector3()).dot(a);
    }
  }
  const setMarks = (d, s) => {   // s in metres along axis
    if (!d.belt.n) return;
    const B = d.belt, m = new THREE.Matrix4(), pos = new THREE.Vector3(), sc = new THREE.Vector3(B.len, 1, B.ww);
    const origin = new THREE.Vector3();
    for (let i = 0; i < B.n; i++) {
      const u = B.lo + (((i * B.pitch + s) % B.span) + B.span) % B.span;
      pos.copy(B.a).multiplyScalar(u).addScaledVector(B.w, B.wc).addScaledVector(up, B.top + 0.0008 + 0.0006);
      // project: position along a and w carries the offsets; the up component is set from the belt top
      origin.copy(pos);
      m.copy(B.basis).setPosition(origin); m.scale(sc); B.im.setMatrixAt(i, m);
    }
    B.im.instanceMatrix.needsUpdate = true;
  };
  for (const d of drives) if (d.belt) setMarks(d, 0);
  // note: pos above mixes axes (a, w, up) which are orthonormal, so this is the exact model-frame position

  // ---------- suction tints ----------
  const tint = new WeakMap();
  const tintable = [];
  for (const d of drives) for (const it of d.items) if (it.kind === "binary") for (const r of it.recs) r.obj.traverse((m) => { if (m.isMesh) tintable.push({ m, d }); });
  for (const t of tintable) t.on = false;
  const tintOf = (base) => { let x = tint.get(base); if (!x) { x = base.clone(); x.emissive = new THREE.Color(0x19c2ff); x.emissiveIntensity = 0.9; x.userData.tint = true; tint.set(base, x); } return x; };
  function applyTints(now) {
    let ch = false;
    for (const t of tintable) {
      const on = t.d.f > 0.5, cur = t.m.userData.base;
      if (on && !cur.userData.tint) { const nb = tintOf(cur); t.m.userData.orig = cur; if (t.m.material === cur) t.m.material = nb; t.m.userData.base = nb; ch = true; }
      else if (!on && cur.userData.tint) { const o = t.m.userData.orig; if (t.m.material === cur) t.m.material = o; t.m.userData.base = o; ch = true; }
    }
    return ch;
  }

  // ---------- math ----------
  const Mr = new THREE.Matrix4(), Tr = new THREE.Matrix4(), Tn = new THREE.Matrix4(), Mtmp = new THREE.Matrix4();
  const rotAbout = (out, axis, ang, pivot) => { Tr.makeTranslation(pivot.x, pivot.y, pivot.z); Tn.makeTranslation(-pivot.x, -pivot.y, -pivot.z); out.makeRotationAxis(axis, ang); out.premultiply(Tr).multiply(Tn); return out; };
  const transl = (out, axis, mm) => out.makeTranslation(axis.x * mm * 0.001, axis.y * mm * 0.001, axis.z * mm * 0.001);
  const restOf = (spec, path) => (spec.rest_deg == null ? 0 : typeof spec.rest_deg === "number" ? spec.rest_deg : spec.rest_deg[path] || 0);

  // motion (model frame) of a rec at the drive's current state; identity if not engaged
  const motionOf = (out, d, it, r) => {
    out.identity();
    const s = it.spec;
    if (it.kind === "rotary" && d.engaged && it.axis && it.pivot && r.follow != null) return rotAbout(out, it.axis, r.follow, it.pivot);   // four-bar output
    if (it.kind === "rotary" && d.engaged && it.axis && it.pivot) return rotAbout(out, it.axis, (d.p * (s.per_step || 0) - restOf(s, r.path)) * RAD, it.pivot);
    if (it.kind === "linear" && it.axis) {
      let mm = s.offset_mm || 0;   // offset_mm: the CAD draws this item away from its LOW/home pose (e.g. a cylinder drawn extended)
      if (s.binary_travel != null && s.per_step == null) mm += d.f * s.binary_travel; else if (d.engaged) mm += d.p * (s.per_step || 0);
      return transl(out, it.axis, mm);
    }
    return out;
  };
  const cA = new THREE.Vector3(), cB = new THREE.Vector3(), cD0 = new THREE.Vector3(), cD = new THREE.Vector3(), cU = new THREE.Vector3(), cV = new THREE.Vector3(), cQ = new THREE.Quaternion(), cM = new THREE.Matrix4();
  const recOf = (d, path) => { for (const it of d.items) if (it.kind !== "coupler" && it.kind !== "stretch") for (const r of it.recs) if (r.path === path) return r; return null; };
  // four-bar: crank a turns with the drive; pin b is solved on the circle about crank b's pivot (rod length kept), crank b and the
  // nodes of its rotary item under the rod's own assembly turn with it (r.follow = angle from home); the rod joins both pins.
  const solveCouplers = (d) => {
    for (const it of d.items) if (it.kind === "coupler") for (const r of it.recs) {
      const ra = recOf(d, r.a), rb = recOf(d, r.b); r.ok = false;
      if (!ra || !rb || !d.engaged) continue;
      const n = rb.item.axis, pv = rb.item.pivot;
      if (!r.fol) { const pre = r.path.slice(0, r.path.lastIndexOf("/") + 1); r.fol = rb.item.recs.filter((x) => x.path.startsWith(pre)); r.prevB = r.b0.clone(); }
      cA.copy(r.a0).applyMatrix4(motionOf(cM, d, ra.item, ra));
      cU.subVectors(r.b0, pv); cU.addScaledVector(n, -cU.dot(n)); const lb = cU.length(); cU.normalize(); cV.crossVectors(n, cU);
      const lc = cD0.subVectors(r.b0, r.a0).length();
      cD.subVectors(cA, pv); const ax = cD.dot(cU), ay = cD.dot(cV), dd = Math.hypot(ax, ay);
      const a = (lb * lb - lc * lc + dd * dd) / (2 * dd), h = Math.sqrt(Math.max(lb * lb - a * a, 0)), ux = ax / dd, uy = ay / dd;
      let best = null;
      for (const sg of [1, -1]) {
        const x = a * ux - sg * h * uy, y = a * uy + sg * h * ux;
        cB.copy(pv).addScaledVector(cU, x).addScaledVector(cV, y); cB.addScaledVector(n, cA.clone().sub(pv).dot(n) - cB.clone().sub(pv).dot(n));
        const e = cB.distanceTo(r.prevB); if (!best || e < best.e) best = { e, p: cB.clone(), ang: Math.atan2(y, x) };
      }
      r.prevB.copy(best.p); r.pa = cA.clone(); r.pb = best.p; r.ok = true;
      for (const f of r.fol) f.follow = best.ang;
    }
  };
  const couplerOf = (out, d, r) => {   // rigid rod: a-pin on crank a, pointing at the solved b-pin
    out.identity(); if (!r.ok) return out;
    cD0.subVectors(r.b0, r.a0).normalize(); cD.subVectors(r.pb, r.pa).normalize(); cQ.setFromUnitVectors(cD0, cD);
    out.makeRotationFromQuaternion(cQ); Tn.makeTranslation(-r.a0.x, -r.a0.y, -r.a0.z); Tr.makeTranslation(r.pa.x, r.pa.y, r.pa.z);
    return out.premultiply(Tr).multiply(Tn);
  };
  const sS = new THREE.Matrix4();
  const stretchOf = (out, d, r) => {   // spring between a fixed point and a point riding on node b: turn + scale along its axis
    out.identity(); const rb = recOf(d, r.b); if (!rb || !d.engaged) return out;
    cB.copy(r.b0).applyMatrix4(motionOf(cM, d, rb.item, rb));
    cD0.subVectors(r.b0, r.a0); const l0 = cD0.length(); cD0.normalize(); cD.subVectors(cB, r.a0); const l = cD.length(); cD.normalize();
    const k = l / l0 - 1, x = cD0.x, y = cD0.y, z = cD0.z;
    sS.set(1 + k * x * x, k * x * y, k * x * z, 0, k * x * y, 1 + k * y * y, k * y * z, 0, k * x * z, k * y * z, 1 + k * z * z, 0, 0, 0, 0, 1);
    cQ.setFromUnitVectors(cD0, cD); out.makeRotationFromQuaternion(cQ).multiply(sS);
    Tn.makeTranslation(-r.a0.x, -r.a0.y, -r.a0.z); Tr.makeTranslation(r.a0.x, r.a0.y, r.a0.z);
    return out.premultiply(Tr).multiply(Tn);
  };
  const mainM = (d) => { const it = d.items[0]; if (!it.recs.length) return Mtmp.identity(); return motionOf(new THREE.Matrix4(), d, it, it.recs[0]); };
  const stateOf = (d, acts) => {
    const a = acts[d.simId]; if (!a) return false;
    let ch = false;
    if (a.kind === "stepper") {
      // dir_ignored (belts whose DIR input is tied to GND): position = number of STEP edges, never signed by DIR
      const eng = a.steps > 0; let p = (d.spec.start_steps || 0) + (d.spec.dir_ignored ? a.steps : a.pos * (d.spec.dir_sign || 1));
      if (d.spec.range_steps && d.spec.stops === "absorb") {   // open-loop stepper driven into a hard stop loses the extra steps: the stop shifts the zero
        const [lo, hi] = d.spec.range_steps; p += d.off || 0;
        if (p < lo) { d.off = (d.off || 0) + lo - p; p = lo; } else if (p > hi) { d.off = (d.off || 0) - (p - hi); p = hi; }
      } else if (d.spec.range_steps) p = Math.max(d.spec.range_steps[0], Math.min(d.spec.range_steps[1], p));   // mechanical stops (the stub world can over-travel)
      if (eng !== d.engaged || p !== d.p) ch = true;
      d.engaged = eng; d.p = p;
    } else { d.target = a.on ? 1 : 0; }
    return ch;
  };

  // ---------- per frame ----------
  let last = performance.now(), resets = sim.vm.resets;
  const work = new THREE.Matrix4(), pm = new THREE.Matrix4();
  function apply(d) {
    solveCouplers(d);
    for (const it of d.items) {
      if (it.kind === "belt" || it.kind === "binary") continue;
      for (const r of it.recs) {
        if (it.kind === "coupler") couplerOf(work, d, r); else if (it.kind === "stretch") stretchOf(work, d, r); else motionOf(work, d, it, r);
        if (r.composeParent) { pm.copy(mainM(d.parent)); work.premultiply(pm); }
        r.obj.matrix.multiplyMatrices(r.invWp, work).multiply(r.wp).multiply(r.home); r.obj.matrixWorldNeedsUpdate = true;
      }
    }
    if (d.spec.kind === "belt" && d.belt) {
      const s = d.engaged ? d.p * (d.spec.per_step || 0) * 0.001 : 0; setMarks(d, s);
      for (const c of d.carries) {
        const B = d.belt; let nu = c.u0 + s; nu = B.umin + ((((nu - B.umin) % (B.umax - B.umin)) + (B.umax - B.umin)) % (B.umax - B.umin));
        work.makeTranslation(B.a.x * (nu - c.u0), B.a.y * (nu - c.u0), B.a.z * (nu - c.u0));
        c.obj.matrix.multiplyMatrices(c.invWp, work).multiply(c.wp).multiply(c.home); c.obj.matrixWorldNeedsUpdate = true;
      }
    }
  }
  // ---------- grip: suction tips pick items (trays) up, release them onto a belt; an item leaving the belt end goes back on the stack ----------
  // spec on the vacuum actuator: grip = { tips: [paths], items: [paths], stack: path (bottom item of the supply stack), belt: motion id, reach_mm }
  // Rising edge: each tip takes the nearest stacked/free item within reach (items on the belt are held by the belt). Falling edge: over the belt -> rides it.
  const grips = [], gT = new THREE.Matrix4(), gM = new THREE.Matrix4(), gV = new THREE.Vector3();
  for (const d of drives) {
    const g = d.spec.grip; if (!g) continue;
    const tips = [];
    for (const path of g.tips || []) { const o = resolve(path); if (!o) { unresolved.push(`${d.id}: grip tip ${path}`); continue; }
      tips.push({ obj: o, T0: new THREE.Matrix4().multiplyMatrices(invF, o.matrixWorld), c0: boxIn(o).getCenter(new THREE.Vector3()), cur: new THREE.Matrix4() }); }
    const items = [];
    for (const path of g.items || []) { const o = resolve(path); if (!o) { unresolved.push(`${d.id}: grip item ${path}`); continue; }
      const wp = new THREE.Matrix4().multiplyMatrices(invF, o.parent.matrixWorld), b = boxIn(o);
      items.push({ obj: o, path, home: o.matrix.clone(), wp, invWp: wp.clone().invert(), W0: new THREE.Matrix4().multiplyMatrices(invF, o.matrixWorld), c0: b.getCenter(new THREE.Vector3()), h: b.max.y - b.min.y, M: new THREE.Matrix4(), st: "free" }); }
    const bottom = items.find((it) => it.path === g.stack) || null;
    grips.push({ d, tips, items, bottom, belt: byMotionId[g.belt] || null, reach: (g.reach_mm || 70) * 0.001, on: false, stack: [] });
  }
  const tipNow = (t) => { t.obj.updateWorldMatrix(true, false); return t.cur.multiplyMatrices(invF, t.obj.matrixWorld); };
  const tipCentre = (t) => gV.copy(t.c0).applyMatrix4(gT.copy(tipNow(t)).multiply(gM.copy(t.T0).invert()));
  const itemCentre = (it) => it.c0.clone().applyMatrix4(it.M);
  const beltS = (bd) => (bd && bd.engaged ? bd.p * (bd.spec.per_step || 0) * 0.001 : 0);
  const onBelt = (G, c) => { const B = G.belt && G.belt.belt; if (!B) return false; const u = c.dot(B.a), w = c.dot(B.w);
    return u > B.umin && u < B.umax && Math.abs(w - B.wc) < B.ww * 1.25 && c.y - B.top > -0.01 && c.y - B.top < 0.15; };
  const setStack = (G) => { if (!G.bottom) return; G.stack.forEach((it, k) => { it.st = "stack"; it.M.makeTranslation(0, k * G.bottom.h, 0).multiply(G.bottom.W0).multiply(gM.copy(it.W0).invert()); }); };
  const putItem = (it) => { it.obj.matrix.multiplyMatrices(it.invWp, it.M).multiply(it.wp).multiply(it.home); it.obj.matrixWorldNeedsUpdate = true; };
  function gripInit() {
    for (const G of grips) {
      G.on = false; G.stack = []; G.drop = null;
      for (const it of G.items) {
        it.M.identity(); it.st = "free"; const c = itemCentre(it);
        if (onBelt(G, c)) { it.st = "belt"; it.sJoin = beltS(G.belt); it.Mj = it.M.clone(); }
        else if (G.bottom) G.stack.push(it);   // trays the CAD draws on the table or on a tip start on the supply stack
        putItem(it);
      }
      G.stack.sort((a, b) => (a === G.bottom ? -1 : b === G.bottom ? 1 : 0));
      setStack(G); G.stack.forEach(putItem);
    }
  }
  function gripTick() {
    let ch = false;
    for (const G of grips) {
      const on = G.d.target === 1, s = beltS(G.belt), B = G.belt && G.belt.belt;
      if (on && !G.on) for (const t of G.tips) {
        if (G.items.some((it) => it.st === "held" && it.tip === t)) continue;   // a tip holds one tray
        const tc = tipCentre(t).clone(); let best = null, bd = G.reach;
        for (const it of G.items) if (it.st === "stack" || it.st === "free") { const e = itemCentre(it).distanceTo(tc); if (e < bd) { bd = e; best = it; } }
        if (best) { if (best.st === "stack") { G.stack.splice(G.stack.indexOf(best), 1); G.drop = best; } best.st = "held"; best.tip = t; best.Tg = tipNow(t).clone(); best.Mg = best.M.clone(); ch = true; }
      }
      if (!on && G.on) for (const it of G.items) if (it.st === "held") {
        if (onBelt(G, itemCentre(it))) { it.st = "belt"; it.sJoin = s; it.Mj = it.M.clone(); } else it.st = "free";
      }
      G.on = on;
      if (G.drop && (G.drop.st !== "held" || itemCentre(G.drop).distanceTo(G.bottom.c0) > G.bottom.h * 1.05)) {   // the rest of the stack drops once the picked tray is clear
        G.drop = null; setStack(G); G.stack.forEach(putItem); ch = true; }
      for (const it of G.items) {
        const prev = it.M.clone();
        if (it.st === "held") it.M.copy(tipNow(it.tip)).multiply(gM.copy(it.Tg).invert()).multiply(it.Mg);
        else if (it.st === "belt" && B) {
          const ds = s - it.sJoin; it.M.makeTranslation(B.a.x * ds, B.a.y * ds, B.a.z * ds).multiply(it.Mj);
          const u = itemCentre(it).dot(B.a);
          if ((u > B.umax || u < B.umin) && G.bottom) { G.stack.push(it); setStack(G); }   // off the belt end: back on top of the supply stack
        }
        if (!prev.equals(it.M)) { putItem(it); ch = true; }
      }
    }
    return ch;
  }
  const homeW = new Map(); for (const r of allRecs) homeW.set(r.obj, new THREE.Matrix4().multiplyMatrices(invF, r.obj.matrixWorld));   // for deltas()
  for (const G of grips) for (const it of G.items) homeW.set(it.obj, it.W0.clone());
  const offsetDrives = drives.filter((d) => d.items.some((it) => it.spec.offset_mm));
  const applyOffsets = () => { for (const d of offsetDrives) { d.applied = true; apply(d); } };
  function goHome() {
    for (const r of allRecs) { r.obj.matrix.copy(r.home); r.obj.matrixWorldNeedsUpdate = true; }
    for (const d of drives) { d.f = 0; d.target = 0; d.engaged = false; d.sig = ""; d.off = 0; if (d.belt) setMarks(d, 0); }
    applyOffsets(); gripInit();
  }
  applyOffsets(); gripInit();
  function tick(now, dtOverride) {
    if (sim.vm.resets !== resets) { resets = sim.vm.resets; goHome(); applyTints(now); invalidate(); }
    const dt = dtOverride != null ? dtOverride : Math.min(100, now - last); last = now;
    const acts = sim.actuators();
    let dirty = false;
    for (const d of drives) {
      let ch = stateOf(d, acts);
      if (d.f !== d.target) { d.f += Math.sign(d.target - d.f) * Math.min(Math.abs(d.target - d.f), dt / BINARY_MS); ch = true; }
      if (!ch) continue;
      const sig = d.engaged + "|" + d.p + "|" + d.f;
      if (sig === d.sig) continue;
      d.sig = sig;
      if (!d.engaged && d.f === 0 && !d.applied) continue;   // still at home
      d.applied = d.engaged || d.f !== 0 || offsetDrives.includes(d);
      apply(d); dirty = true;
    }
    // a child that composes its parent's motion must follow when the parent moves
    if (dirty) {
      for (const d of drives) if (d.parent && d.parent.sig !== d._psig && d.items.some((it) => it.recs.some((r) => r.composeParent))) { d._psig = d.parent.sig; apply(d); }
      applyTints(now); invalidate();
    } else if (applyTints(now)) invalidate();
    if (grips.length && gripTick()) invalidate();
    updateLabels();
  }

  // ---------- floating weight labels (optional) ----------
  const labels = [];
  const host = document.getElementById("stage") || document.body;
  const lcss = document.createElement("style");
  lcss.textContent = ".wlab{position:absolute;left:0;top:0;transform:translate(-50%,-100%);pointer-events:none;font:600 12px/1 ui-monospace,Menlo,monospace;background:#101418d9;color:#fff;padding:3px 6px;border-radius:5px;white-space:nowrap;z-index:3}.wlab.hide{display:none}#mvWarn{font-size:11px;color:#b54708;margin:4px 0}";
  document.head.appendChild(lcss);
  for (const spec of mc.labels || []) {
    const o = resolve(spec.node); if (!o) { unresolved.push(`label: ${spec.node}`); continue; }
    const b = boxIn(o), top = new THREE.Vector3((b.min.x + b.max.x) / 2, b.max.y + (spec.dy_mm || 30) * 0.001, (b.min.z + b.max.z) / 2);
    const el = document.createElement("div"); el.className = "wlab hide"; host.appendChild(el);
    labels.push({ spec, el, top: top.applyMatrix4(model.matrixWorld), txt: "" });
  }
  const readPath = (root, path) => path.split(".").reduce((x, k) => (x == null ? x : x[k]), root);
  const v3 = new THREE.Vector3();
  function updateLabels() {
    if (!labels.length) return;
    const W = window.innerWidth, H = window.innerHeight, m = sim.vm.m;
    for (const l of labels) {
      const val = readPath(m, l.spec.source);
      const txt = typeof val === "number" ? Math.round(val) + " " + (l.spec.unit || "") : "";
      if (txt !== l.txt) { l.el.textContent = txt; l.txt = txt; }
      v3.copy(l.top).project(api.camera);
      const vis = txt && v3.z < 1 && Math.abs(v3.x) < 1.1 && Math.abs(v3.y) < 1.1 && api.camera.position.distanceTo(l.top) < (mc.label_max_dist || 2500);
      l.el.classList.toggle("hide", !vis);
      if (vis) l.el.style.transform = `translate(${((v3.x + 1) / 2) * W}px,${((1 - v3.y) / 2) * H}px) translate(-50%,-100%)`;
    }
  }

  // ---------- report ----------
  const nBound = allRecs.length;
  if (unresolved.length) console.warn(`[motion] ${unresolved.length} unresolved paths:\n` + unresolved.join("\n"));
  const body = document.querySelector("#vmSec .secbody");
  if (body && unresolved.length) { const w = document.createElement("div"); w.id = "mvWarn"; w.textContent = (L.motion || DICT.ru.motion).unresolved.replace("{n}", unresolved.length); body.appendChild(w); }
  api.tickers.push(tick);
  const worldOf = (o) => Array.from(o.matrixWorld.elements);
  return {
    grips: () => grips.map((G) => G.items.map((it) => { const c = itemCentre(it); return { path: it.path.split('/').slice(-2).join('/'), st: it.st, c: [c.x * 1000, c.y * 1000, c.z * 1000].map(Math.round) }; })),
    bound: nBound, unresolved, clones: cloneRoots.map((o) => o.userData.n), drives: drives.map((d) => ({ id: d.id, simId: d.simId, kind: d.spec.kind, nodes: d.items.reduce((n, it) => n + it.recs.length, 0), carries: d.carries.length })),
    // for tests: every bound node's current and home world matrix (the home one is captured at bind time)
    advance(ms) { tick(performance.now(), ms); },   // scripts: update the CAD to the sim state right after sim.step(ms) (rAF may not run headless)
    deltas() {   // model-frame motion of every moved node vs its home (for offline frame rendering): [{path, d: 16 numbers}]
      model.updateMatrixWorld(true); const out = [], Wn = new THREE.Matrix4(), D = new THREE.Matrix4(), I = new THREE.Matrix4();
      const objs = [...allRecs.map((r) => [r.obj, r.path]), ...grips.flatMap((G) => G.items.map((it) => [it.obj, it.path]))];
      for (const [o, path] of objs) { Wn.multiplyMatrices(invF, o.matrixWorld); D.copy(Wn).multiply(I.copy(homeW.get(o)).invert());
        const e = D.elements; let dev = 0; for (let k = 0; k < 16; k++) dev = Math.max(dev, Math.abs(e[k] - (k % 5 === 0 ? 1 : 0)));
        if (dev > 1e-6) out.push({ path, d: Array.from(e) }); }
      return out;
    },
    probe() { model.updateMatrixWorld(true); return allRecs.map((r) => ({ id: r.item ? r.item.drive.id : "carry", path: r.path, world: worldOf(r.obj) })); },
    homeProbe: (() => allRecs.map((r) => ({ id: r.item ? r.item.drive.id : "carry", path: r.path, world: worldOf(r.obj) })))(),
    marks: () => drives.filter((d) => d.belt).map((d) => ({ id: d.id, n: d.belt.n, pitch: d.belt.pitch, top: d.belt.top })),
  };
}
