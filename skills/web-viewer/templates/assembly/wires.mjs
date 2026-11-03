// Wires inside the machine (machine-project harness.py, #113): build.py publishes wires.json when the model routes wires
// (ports with locations, routes through waypoints). Each wire is a tube of its own diameter, coloured by its net, in mm of the
// CAD frame; a wire to a moving part follows the cycle (its routes at sample times of the cycle, the nearest one shown).
// A switch in the View row hides them. Never picked: a click goes to the part behind.
export async function mountWires({ api, L, file }) {
  const THREE = api.THREE;
  const r = await fetch(file, { cache: "no-store" });
  if (!r.ok) return null;
  const data = await r.json();
  const ws = data.wires || [];
  const root = new THREE.Group(); root.name = "wires"; root.scale.setScalar(0.001);   // the model scene is in metres
  api.model.add(root);
  const mats = {};
  const mat = (c) => mats[c] || (mats[c] = new THREE.MeshStandardMaterial({ color: c, roughness: 0.6, metalness: 0.05 }));
  const tube = (pts, rad) => {
    const curve = new THREE.CatmullRomCurve3(pts.map((p) => new THREE.Vector3(p[0], p[1], p[2])), false, "centripetal");
    return new THREE.TubeGeometry(curve, Math.max(24, pts.length * 3), rad, 10, false);
  };
  const moving = [];
  for (const w of ws) {
    if (!w.path || w.path.length < 2) continue;
    const rad = (w.od_mm || 1.5) / 2;
    const m = new THREE.Mesh(tube(w.path, rad), mat(w.color || "#3d3d3d"));
    m.name = w.id; m.userData.wire = w.id; m.raycast = () => {};
    root.add(m);
    if (w.frames && w.frames.times && w.frames.times.length) moving.push({ w, m, rad, cache: {}, shown: -1 });
  }
  if (moving.length) {
    api.tickers.push(() => {
      if (!api.cycle || !root.visible) return;
      const t = api.cycle.state().t;
      for (const d of moving) {
        const ts = d.w.frames.times;
        let k = 0;
        for (let i = 1; i < ts.length; i++) if (Math.abs(ts[i] - t) < Math.abs(ts[k] - t)) k = i;
        if (k === d.shown) continue;
        d.m.geometry = d.cache[k] || (d.cache[k] = tube(d.w.frames.paths[k], d.rad));
        d.shown = k; api.invalidate();
      }
    });
  }
  const T = L.wires || {};
  const b = document.createElement("button"); b.id = "bWires"; b.className = "on";   // a switch of the View row, like Grid / Edges
  b.textContent = (T.show || "Wires ({n})").replace("{n}", ws.length); b.title = T.tip || "";
  const vr = document.getElementById("viewRow"); if (vr) vr.appendChild(b);
  b.onclick = () => { root.visible = !root.visible; b.className = root.visible ? "on" : ""; api.wires.visible = root.visible; api.invalidate(); };
  api.wires = { n: root.children.length, moving: moving.length, visible: true,
                list: ws.map((w) => ({ id: w.id, net: w.net, color: w.color, od_mm: w.od_mm, cut_mm: w.cut_mm })) };
  api.invalidate();
  return api.wires;
}
