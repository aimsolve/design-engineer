// "Device" (L.device): a virtual device (sim/device.json from the model) on the model page. Loaded by sim_ui.mjs when
// sim/config.json has `device`. Nothing device-specific is written here: everything comes from the device's elements.
//   - every display is drawn into a canvas: enlarged in the "Device" window and, as a texture, on the glass of its 3D part
//     (params.glass {center, normal, up, size} in the part's own mm; without it, the +Z face of the part's box)
//   - a button is pressed while the mouse is held on its 3D part or on its button in the window, or while its key is held (params.key)
//   - an encoder turns with the mouse wheel over its 3D part, with its keys (params.keys [ccw, cw]) or ◀ ▶ in the window; its push
//     (params.push_key, a click on the part) works like a button
//   window.__viewer.sim.device = { elements(), down(id), up(id), press(id, ms), turn(id, n), screen(id) -> {w, h, on, frames, pixels}, shot(id) }
import * as THREE from "three";

const STYLE = `
#devwin { top: 16px; right: 72px; left: auto; width: auto; max-width: calc(100vw - 32px); z-index: 6; }
#devwin .body { display: flex; flex-direction: column; gap: 8px; align-items: center; } #devwin.collapsed .body { display: none; }
#devwin canvas { image-rendering: pixelated; background: #000; border-radius: 4px; box-shadow: 0 0 0 6px #1d2733; margin: 6px; display: block; }
#devwin .scr { display: flex; flex-direction: column; align-items: center; } #devwin .scr small { color: var(--muted); font-size: 11px; }
#devwin .ctl { display: flex; flex-wrap: wrap; gap: 6px; justify-content: center; max-width: 360px; }
#devwin .ctl button { min-width: 64px; padding: 6px 10px; border-radius: 16px; touch-action: none; user-select: none; }
#devwin .ctl button.down { background: var(--acc); color: #fff; border-color: var(--acc); }
#devwin .ctl kbd { font: 10px ui-monospace, Menlo, monospace; color: var(--muted); margin-left: 4px; } #devwin .ctl button.down kbd { color: #fff; }
#devwin .enc { display: inline-flex; gap: 2px; } #devwin .enc button { min-width: 38px; }
@media (max-width: 700px) { #devwin { top: 8px; left: 8px; right: 8px; } }
`;
const el = (tag, attrs = {}, html) => { const e = document.createElement(tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (html != null) e.innerHTML = html; return e; };
const KEY_NAME = { Space: "Space", ArrowLeft: "←", ArrowRight: "→", ArrowUp: "↑", ArrowDown: "↓", Enter: "Enter" };
const keyName = (k) => KEY_NAME[k] || String(k).replace(/^Key/, "").replace(/^Digit/, "");
const keyMatch = (e, k) => k && (e.code === k || e.key === k || (k.length === 1 && e.key.toLowerCase() === k.toLowerCase()));
const typing = () => { const a = document.activeElement; return a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA" || a.isContentEditable); };

export function mountDevice({ api, vm, meta, L, esc, BYPART }) {
  if (L.keySpace) KEY_NAME.Space = L.keySpace;
  document.head.appendChild(el("style", {}, STYLE));
  const els = meta.elements, m = () => vm.m;
  const label = (e) => (e.params && e.params.label) || e.id;
  const displays = els.filter((e) => e.params && (e.model === "ssd1306" || /^(st7789|st7735|ili9341)$/.test(e.model)));
  const buttons = els.filter((e) => e.model === "button"), encoders = els.filter((e) => e.model === "encoder");

  // ---- the window
  const win = el("div", { id: "devwin", class: "card" }, `
    <div class="head" id="devHead"><div class="ttl">${esc(L.device)}</div><button class="icon fold" title="${esc(L.fold)}"></button></div>
    <div class="body"><div id="devScr"></div><div class="ctl" id="devCtl"></div></div>`);
  document.body.appendChild(win);
  win.querySelector("#devHead").addEventListener("click", () => win.classList.toggle("collapsed"));
  if (window.__win) window.__win.register(win, { id: "device", head: win.querySelector("#devHead"), cls: "collapsed" });

  // one canvas per display: the window shows it enlarged, the 3D glass uses it as a texture
  const scr = {};
  for (const d of displays) {
    const c = el("canvas", { "data-el": d.id });             // sized from the chip model when the machine is built (see flush)
    const box = el("div", { class: "scr" }); box.appendChild(c);
    if (displays.length > 1) box.appendChild(el("small", {}, esc(label(d))));
    win.querySelector("#devScr").appendChild(box);
    const ctx = c.getContext("2d"), tex = new THREE.CanvasTexture(c);
    if ("colorSpace" in tex) tex.colorSpace = THREE.SRGBColorSpace;
    tex.magFilter = THREE.NearestFilter; tex.generateMipmaps = false; tex.minFilter = THREE.LinearFilter;
    scr[d.id] = { c, ctx, tex, model: null, img: null };
  }
  const ctl = win.querySelector("#devCtl");
  for (const b of buttons) ctl.appendChild(el("button", { "data-btn": b.id }, `${esc(label(b))}${b.params.key ? `<kbd>${esc(keyName(b.params.key))}</kbd>` : ""}`));
  for (const e of encoders) {
    const ks = e.params.keys || [];
    const g = el("span", { class: "enc" }, `<button data-turn="${esc(e.id)}" data-n="-1">◀${ks[0] ? `<kbd>${esc(keyName(ks[0]))}</kbd>` : ""}</button>`
      + `<button data-btn="${esc(e.id)}">${esc(label(e))}${e.params.push_key ? `<kbd>${esc(keyName(e.params.push_key))}</kbd>` : ""}</button>`
      + `<button data-turn="${esc(e.id)}" data-n="1">▶${ks[1] ? `<kbd>${esc(keyName(ks[1]))}</kbd>` : ""}</button>`);
    ctl.appendChild(g);
  }

  // ---- pressing: one source of truth, so a key and the mouse on the same button do not release each other
  const holders = {}, downAt = {};   // id -> Set of what holds it ("mouse", "key", "api"); machine time of the press
  const MIN_US = 60000;              // a finger never presses shorter than ~60 ms: a quick tap of a key / click still reaches the firmware
  const mark = (id) => ctl.querySelectorAll(`[data-btn="${CSS.escape(id)}"]`).forEach((b) => b.classList.toggle("down", !!(holders[id] && holders[id].size)));
  const down = (id, who = "api") => {
    const s = holders[id] || (holders[id] = new Set()), was = s.size; s.add(who);
    if (!was && m()) { vm.resume(); m().down(id); downAt[id] = m().sim.t; }
    mark(id);
  };
  const up = (id, who = "api") => {
    const s = holders[id]; if (!s || !s.has(who)) return; s.delete(who);
    if (!s.size && m()) {
      const mm = m(), left = who === "api" ? 0 : downAt[id] + MIN_US - mm.sim.t;
      if (left > 0) mm.sim.at(mm.sim.t + left, () => { if (!(holders[id] && holders[id].size) && vm.m === mm) mm.up(id); });
      else mm.up(id);
    }
    mark(id);
  };
  const turn = (id, n) => { if (m()) { vm.resume(); m().turn(id, n); } };
  ctl.addEventListener("pointerdown", (e) => {
    const b = e.target.closest("[data-btn]"), t = e.target.closest("[data-turn]");
    if (t) { turn(t.dataset.turn, +t.dataset.n); return; }
    if (!b) return;
    e.preventDefault(); down(b.dataset.btn, "mouse");
    const rel = () => { up(b.dataset.btn, "mouse"); removeEventListener("pointerup", rel); removeEventListener("pointercancel", rel); };
    addEventListener("pointerup", rel); addEventListener("pointercancel", rel);
  });

  // keyboard: held key = held button (auto-repeat ignored); an encoder key turns one click per press (repeat included)
  addEventListener("keydown", (e) => {
    if (typing() || e.metaKey || e.ctrlKey || e.altKey) return;
    for (const b of buttons) if (keyMatch(e, b.params.key)) { e.preventDefault(); if (!e.repeat) down(b.id, "key"); return; }
    for (const c of encoders) {
      const ks = c.params.keys || [];
      if (keyMatch(e, ks[0])) { e.preventDefault(); turn(c.id, -1); return; }
      if (keyMatch(e, ks[1])) { e.preventDefault(); turn(c.id, 1); return; }
      if (keyMatch(e, c.params.push_key)) { e.preventDefault(); if (!e.repeat) down(c.id, "key"); return; }
    }
  });
  addEventListener("keyup", (e) => {
    for (const b of buttons) if (keyMatch(e, b.params.key)) up(b.id, "key");
    for (const c of encoders) if (keyMatch(e, c.params.push_key)) up(c.id, "key");
  });
  addEventListener("blur", () => { for (const id in holders) { up(id, "key"); up(id, "mouse"); } });

  // ---- 3D: which element a mesh is (its part id is the element's placement, or the catalogue part of a single placement)
  const elOfMesh = (o) => {
    if (!o || !o.isMesh) return null;
    const p = o.userData.part;
    return els.find((e) => e.id === p) || els.find((e) => e.part === p && els.filter((x) => x.part === p).length === 1) || null;
  };
  const meshesOf = (e) => BYPART[e.id] || BYPART[e.part] || [];
  const canvas = api.canvas;
  canvas.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || !api.pickAt) return;
    const hit = api.pickAt(e.clientX, e.clientY), d = hit && elOfMesh(hit.object);
    if (!d || (d.model !== "button" && d.model !== "encoder")) return;
    e.stopImmediatePropagation(); e.preventDefault();     // a press, not an orbit
    down(d.id, "mouse");
    const rel = () => { up(d.id, "mouse"); removeEventListener("pointerup", rel, true); removeEventListener("pointercancel", rel, true); };
    addEventListener("pointerup", rel, true); addEventListener("pointercancel", rel, true);
  }, { capture: true });
  // a mouse wheel notch (deltaY ~100 px) = one click; a trackpad's many small events add up to a click per ~60 px
  const wheelAcc = {};
  canvas.addEventListener("wheel", (e) => {
    if (!api.pickAt) return;
    const hit = api.pickAt(e.clientX, e.clientY), d = hit && elOfMesh(hit.object);
    if (!d || d.model !== "encoder") return;
    e.stopImmediatePropagation(); e.preventDefault();     // turns the knob, does not zoom
    const px = e.deltaY * (e.deltaMode === 1 ? 33 : e.deltaMode === 2 ? 300 : 1);
    let a = (wheelAcc[d.id] || 0) + px;
    const n = Math.trunc(a / 60) || (Math.abs(px) >= 60 ? Math.sign(px) : 0);
    if (n) { turn(d.id, Math.max(-5, Math.min(5, n))); a = 0; }
    wheelAcc[d.id] = a;
  }, { capture: true, passive: false });

  // the picture on the glass: a plane over the active area, child of every instance of the display part (follows the part)
  const glassOf = (d, mesh) => {
    const g = d.params.glass;
    if (g && g.center && g.size) {
      const n = new THREE.Vector3(...(g.normal || [0, 0, 1])).normalize(), up = new THREE.Vector3(...(g.up || [0, 1, 0])).normalize();
      return { c: new THREE.Vector3(...g.center).multiplyScalar(0.001), n, up, w: g.size[0] * 0.001, h: g.size[1] * 0.001 };
    }
    if (!mesh.geometry.boundingBox) mesh.geometry.computeBoundingBox();
    const b = mesh.geometry.boundingBox, s = b.getSize(new THREE.Vector3());
    return { c: new THREE.Vector3((b.min.x + b.max.x) / 2, (b.min.y + b.max.y) / 2, b.max.z), n: new THREE.Vector3(0, 0, 1), up: new THREE.Vector3(0, 1, 0), w: s.x * 0.9, h: s.y * 0.9 };
  };
  // device.json carries the placement (`loc`, row-major, mm in the model frame): the glass goes under the mesh's parent at loc x glass,
  // because the viewer's mesh may be quantized (KHR_mesh_quantization: its node matrix = placement x dequantization, not the part frame).
  // Without loc (a hand-made device.json) the mesh's own frame is taken as the part's.
  const placeOf = (d) => { if (!d.loc) return null; const m = new THREE.Matrix4().set(...d.loc); m.elements[12] /= 1000; m.elements[13] /= 1000; m.elements[14] /= 1000; return m; };
  for (const d of displays) {
    for (const mesh of meshesOf(d)) {
      const g = glassOf(d, mesh), right = new THREE.Vector3().crossVectors(g.up, g.n).normalize(), at = placeOf(d);
      const plane = new THREE.Mesh(new THREE.PlaneGeometry(g.w, g.h), new THREE.MeshBasicMaterial({ map: scr[d.id].tex, toneMapped: false, polygonOffset: true, polygonOffsetFactor: -2 }));
      plane.matrixAutoUpdate = false;
      plane.matrix.makeBasis(right, g.up, g.n).setPosition(g.c.clone().addScaledVector(g.n, 0.0002));
      if (at && d.params.glass) plane.matrix.premultiply(at);
      plane.raycast = () => {};                            // a click on the screen picks the display part itself
      plane.userData.deviceScreen = d.id;
      (at && d.params.glass ? mesh.parent : mesh).add(plane);
    }
  }
  api.invalidate();

  // ---- every frame: upload what the displays changed since the last frame (dirty rectangle only)
  const flush = () => {
    const mm = m(); if (!mm) return false;
    let changed = false;
    for (const d of displays) {
      const s = scr[d.id], disp = mm.display(d.id);
      if (s.model !== disp) {                                // a new machine (first build, Reset, firmware switch): size from the chip
        for (const id in holders) holders[id].clear();       // nothing is held on a fresh board
        ctl.querySelectorAll(".down").forEach((b) => b.classList.remove("down"));
        s.model = disp; s.img = new ImageData(disp.fb, disp.w, disp.h); disp.markAll();
        if (s.c.width !== disp.w || s.c.height !== disp.h) {
          const k = Math.max(1, Math.min(4, Math.floor(Math.min(420 / disp.h, 300 / disp.w))));
          s.c.width = disp.w; s.c.height = disp.h; s.c.style.width = disp.w * k + "px"; s.c.style.height = disp.h * k + "px";
          s.tex.dispose();
        }
      }
      const r = disp.takeDirty(); if (!r) continue;
      s.ctx.putImageData(s.img, 0, 0, r[0], r[1], r[2] - r[0] + 1, r[3] - r[1] + 1);
      s.tex.needsUpdate = true; changed = true;
    }
    return changed;
  };
  api.tickers.push(() => { if (flush()) api.invalidate(); });

  const elements = () => els.map((e) => ({ id: e.id, model: e.model, label: label(e), part: e.part }));
  return {
    elements, down: (id) => down(id, "api"), up: (id) => up(id, "api"),
    press(id, ms = 150) { const mm = m(); if (!mm) return; down(id, "api"); mm.sim.at(mm.sim.t + ms * 1000, () => { if (vm.m === mm) up(id, "api"); }); },
    turn,
    screen(id) { const d = m().display(id); return { w: d.w, h: d.h, on: d.on, frames: d.frames, pixels: d.fb }; },
    shot(id) { flush(); return scr[id].c.toDataURL("image/png"); },
    window: win,
  };
}
