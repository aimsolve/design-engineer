// Visual feedback of the assembly viewer: draw on a still frame, write what is wrong, send one JPEG (3D frame + drawing) to config.json `feedback`.
// Loaded by index.html only when config.json has `feedback` (build.py --feedback URL); otherwise nothing of this exists on the page.
//
//   POST cfg.feedback  {comment, image: "data:image/jpeg;base64,...", view: {url, hash, cycle_t, parts: [part ids the shapes hit], shapes, camera, sim?: firmware-machine snapshot}}
//   POST cfg.feedback  {comment, image, view: {url, hash, cycle_t, part, node, part_name, selected, parts: [part]}}          (part card)
//
// A drawn note says what each shape lies on (#106): every shape's key points are cast as rays into the model (projectShapes below) ->
//   shapes  [{type, text?, closed?, px: [[x,y]...], pts: [{px, role, part, n, at: [x,y,z] mm, normal: [x,y,z]} | {px, role, miss: true}], inside?: {part: count, "": misses}}]
//   camera  {w, h, fov, near, far, position, target, up, view: 16 row-major, proj: 16 row-major}
// Stage 2 (#106): a finished shape also gets `path` (its 3D polyline, assembly mm: on the surface, a miss continuing the nearest wall) and
// becomes a 3D object in the scene, so the camera stays free and the note can be drawn on from several sides: `view` = index into
// `views` (the cameras it was drawn from; `camera` = the last). The plane tool puts the next shapes on a clicked face's plane, also past
// the part's edge (`plane`, key points `plane: true` and `off: true` beyond the edge).
// at, normal and camera are in the model's assembly frame (mm, the CAD's own axes). A drawn note carries no `selected`: the last clicked part
// is not what was circled (it did not match in 14 of 46 real notes), so agents must not lean on it.
//   POST <feedback with /stt/ in place of /feedback/>  raw audio (audio/webm | audio/mp4)  ->  {text}
//
// The part card (#info) gets a box "what to change in this part": the photo is the frame with the selection highlighted. Both boxes have a mic:
// the recording is recognised by the router (the owner's Whisper) and the text lands in the box for editing; nothing is sent until Send.
//
// A shape is drawn on a 2D canvas over the WebGL one; once finished it is cast into the model and lives in the 3D scene (stage 2 below), so
// the camera stays free. The cycle / sim pause at the first stroke and the cycle bar hides while the note has shapes: the parts must stay
// where they were drawn on. The pictures are taken per view (render + drawImage in the same tick: the renderer has no preserveDrawingBuffer).

const RED = "#e03131", LW = 3, MAX_VIEWS = 4, MAX_SIDE = 1600, MAX_B64 = 700 * 1000;   // the host nginx caps request bodies at 1 MB
const svg = (d) => `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
const ICON = {
  select: svg('<path d="M5 3.5v15l4.2-4.1 2.9 6.4 2.6-1.2-2.9-6.3h5.9z"/>'),
  pen: svg('<path d="M4 20l1-4.5L16 4.5l3.5 3.5L8.5 19z"/><path d="M14 7l3 3"/>'),
  line: svg('<path d="M5 19L19 5"/>'),
  arrow: svg('<path d="M5 19L19 5"/><path d="M10 5h9v9"/>'),
  rect: svg('<rect x="4" y="6" width="16" height="12" rx="1.5"/>'),
  ellipse: svg('<circle cx="12" cy="12" r="8"/>'),
  text: svg('<path d="M6 6h12M12 6v13M9 19h6"/>'),
  pin: svg('<path d="M12 21s-6-5.6-6-11a6 6 0 0 1 12 0c0 5.4-6 11-6 11z"/><circle cx="12" cy="10" r="2.2"/>'),
  move: svg('<path d="M4 18c3-8 9-11 15-11" stroke-dasharray="2.5 2.5"/><path d="M15 4l4 3-4 3"/><circle cx="4.5" cy="18" r="1.6"/>'),
  face: svg('<path d="M4 8l8-4 8 4-8 4z" fill="currentColor" fill-opacity=".35"/><path d="M4 8v8l8 4 8-4V8"/><path d="M12 12v8"/>'),
  dim: svg('<path d="M4 7v10M20 7v10M4 12h16"/><path d="M7 9.5L4 12l3 2.5M17 9.5l3 2.5-3 2.5"/>'),
  plane: svg('<path d="M3 15l7-4 11 4-7 4z"/><path d="M10 11V4M7 7l3-3 3 3"/>'),
  close: svg('<path d="M6 6l12 12M18 6L6 18"/>'),
  mic: svg('<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/>'),
  stop: svg('<rect x="6.5" y="6.5" width="11" height="11" rx="2" fill="currentColor"/>'),
};
const MIC_MAX_S = 120, MIC_BPS = 32000, MIC_MAX_B = 900 * 1000;   // ~0.5 MB at 32 kbit/s; a browser that ignores the bitrate (Safari AAC)
                                                                   // is stopped by size, under the 1 MB body cap of the host nginx
const TOOLS = ["select", "pen", "line", "arrow", "rect", "ellipse", "text", "plane", "pin", "move", "face", "dim"];

// ---- what a shape lies on: its key points and a grid inside it, each picked in 3D (pick(x, y) -> null | {part, n, at, normal}) ----
const EDGE_N = 8, GRID_N = 5, ALONG_N = 8;   // ≤ 1 + 8 key points per shape + a 5x5 grid inside: ~30 rays, a fraction of a second on a phone
function boxPts(pts) {
  let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
  for (const p of pts) { x0 = Math.min(x0, p[0]); y0 = Math.min(y0, p[1]); x1 = Math.max(x1, p[0]); y1 = Math.max(y1, p[1]); }
  return { x0, y0, x1, y1 };
}
function along(pts, k) {        // k points evenly spaced by length along a polyline, both ends included
  const seg = [0];
  for (let i = 1; i < pts.length; i++) seg.push(seg[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
  const L = seg[seg.length - 1], out = [];
  if (!L || k < 2) return [pts[0]];
  for (let j = 0, i = 1; j < k; j++) {
    const d = (L * j) / (k - 1);
    while (i < pts.length - 1 && seg[i] < d) i++;
    const f = (d - seg[i - 1]) / (seg[i] - seg[i - 1] || 1), a = pts[i - 1], b = pts[i];
    out.push([a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f]);
  }
  return out;
}
function inPoly(p, poly) {      // even-odd rule
  let c = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const a = poly[i], b = poly[j];
    if ((a[1] > p[1]) !== (b[1] > p[1]) && p[0] < ((b[0] - a[0]) * (p[1] - a[1])) / (b[1] - a[1]) + a[0]) c = !c;
  }
  return c;
}
export function isClosed(s) {   // a hand-drawn loop: the pen ends near where it started
  if (s.type !== "pen" || s.pts.length < 4) return false;
  const b = boxPts(s.pts), diag = Math.hypot(b.x1 - b.x0, b.y1 - b.y0), a = s.pts[0], e = s.pts[s.pts.length - 1];
  return diag > 12 && Math.hypot(e[0] - a[0], e[1] - a[1]) < Math.max(24, diag * 0.25);
}
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]], dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const r1 = (v) => v.map((x) => Math.round(x * 10) / 10);
function onPlane(r, at, n) {    // ray ∩ plane, in front of the camera, or null
  const d = dot(r.dir, n);
  if (Math.abs(d) < 1e-9) return null;
  const t = dot(sub(at, r.origin), n) / d;
  return t > 0 ? r.origin.map((o, i) => o + r.dir[i] * t) : null;
}
// 3D points of screen samples: on the surface where they hit; a miss continues the wall of the nearest sample (along the shape) that hit
function lift(samples, pick, ray) {
  const hits = samples.map((p) => { const h = pick(p[0], p[1]); return h && h.part && h.at ? h : null; });
  if (!hits.some(Boolean)) return null;
  return samples.map((p, i) => {
    if (hits[i]) return hits[i].at;
    let h = null;
    for (let d = 1; !h; d++) h = hits[i - d] || hits[i + d];
    return (h.normal && onPlane(ray(p[0], p[1]), h.at, h.normal)) || h.at;
  });
}
function outline(s, b, c, closed) {   // screen samples along the drawn shape, for its 3D path
  const P = 24, rx = (b.x1 - b.x0) / 2, ry = (b.y1 - b.y0) / 2;   // 24 rays round an outline: smooth enough, and a phone stays under ~0.3 s
  if (s.type === "arrow" || s.type === "line" || s.type === "move" || s.type === "dim") return [s.pts[0], s.pts[s.pts.length - 1]];
  if (s.type === "pin" || s.type === "face") return [s.pts[0]];
  if (s.type === "ellipse") return [...Array(P)].map((_, i) => [c[0] + rx * Math.cos((i * 2 * Math.PI) / P), c[1] + ry * Math.sin((i * 2 * Math.PI) / P)]);
  if (s.type === "rect") return along([[b.x0, b.y0], [b.x1, b.y0], [b.x1, b.y1], [b.x0, b.y1], [b.x0, b.y0]], P + 1).slice(0, P);
  if (s.type === "pen") return closed ? along(s.pts, Math.min(32, Math.max(9, s.pts.length)) + 1).slice(0, -1) : along(s.pts, Math.min(32, Math.max(2, s.pts.length)));
  return null;
}
// opts.ray (x, y) -> {origin, dir} in assembly mm adds `path` (the shape in 3D); opts.plane {at, normal, part} puts everything on that plane
export function projectShape(s, pick0, opts = {}) {
  const r = (p) => [Math.round(p[0]), Math.round(p[1])], { ray } = opts, memo = new Map();
  const plane = s.type === "face" ? null : opts.plane;   // "this face" is the face clicked, whatever plane is set for drawing
  const pick = (x, y) => { const k = Math.round(x * 4) + "," + Math.round(y * 4); if (!memo.has(k)) memo.set(k, pick0(x, y)); return memo.get(k); };   // a pixel is cast once
  const key = [], b = boxPts(s.pts), c = [(b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2], closed = isClosed(s);
  let inside = null;
  if (s.type === "arrow") key.push([s.pts[0], "tail"], [s.pts[1], "tip"]);
  else if (s.type === "move" || s.type === "dim") key.push([s.pts[0], "from"], [s.pts[s.pts.length - 1], "to"]);   // stage 3: ready-made marks
  else if (s.type === "pin" || s.type === "face") key.push([s.pts[0], s.type]);
  else if (s.type === "line") along(s.pts, 5).forEach((p, i) => key.push([p, i === 0 || i === 4 ? "end" : "along"]));
  else if (s.type === "text") key.push([[s.pts[0][0] + (s.w || 10 * (s.text || "").length) / 2, s.pts[0][1] + 11], "anchor"]);   // w: measured on the page; ~10 px a letter at 18 px
  else if (s.type === "pen" && !closed) along(s.pts, Math.min(ALONG_N, Math.max(2, s.pts.length))).forEach((p) => key.push([p, "stroke"]));
  else {                        // an area: rect, ellipse, a closed pen loop
    const rx = (b.x1 - b.x0) / 2, ry = (b.y1 - b.y0) / 2;
    const edge = s.type === "rect" ? [[b.x0, b.y0], [c[0], b.y0], [b.x1, b.y0], [b.x1, c[1]], [b.x1, b.y1], [c[0], b.y1], [b.x0, b.y1], [b.x0, c[1]]]
      : s.type === "ellipse" ? [...Array(EDGE_N)].map((_, i) => [c[0] + rx * Math.cos((i * 2 * Math.PI) / EDGE_N), c[1] + ry * Math.sin((i * 2 * Math.PI) / EDGE_N)])
      : along(s.pts, EDGE_N + 1).slice(0, EDGE_N);
    key.push([c, "centre"], ...edge.map((p) => [p, "edge"]));
    const isIn = s.type === "rect" ? () => true : s.type === "ellipse" ? (p) => ((p[0] - c[0]) / rx) ** 2 + ((p[1] - c[1]) / ry) ** 2 < 1 : (p) => inPoly(p, s.pts);
    inside = {};
    for (let i = 0; i < GRID_N; i++) for (let j = 0; j < GRID_N; j++) {
      const p = [b.x0 + ((b.x1 - b.x0) * (i + 0.5)) / GRID_N, b.y0 + ((b.y1 - b.y0) * (j + 0.5)) / GRID_N];
      if (!isIn(p)) continue;
      const h = pick(p[0], p[1]), k = h && h.part ? h.part : "";
      inside[k] = (inside[k] || 0) + 1;
    }
  }
  const onPl = (p) => plane && ray ? onPlane(ray(p[0], p[1]), plane.at, plane.normal) : null;
  const pts = key.map(([p, role]) => {
    const h = pick(p[0], p[1]);
    if (plane && ray) {         // on the face's plane: on the part where the surface is that plane, else past its edge
      const at = onPl(p);
      if (!at) return { px: r(p), role, miss: true };
      const q = opts.pickOn && plane.part ? opts.pickOn(p[0], p[1], plane.part) : h;   // that part's own surface: a part in front is not "past the edge"
      const on = q && q.part && q.at && Math.hypot(...sub(q.at, at)) <= 1;
      return { px: r(p), role, at: r1(at), normal: plane.normal, plane: true, ...(on ? { part: q.part, n: q.n } : { off: true }) };
    }
    return h && h.part ? { px: r(p), role, part: h.part, n: h.n, at: h.at, normal: h.normal } : { px: r(p), role, miss: true };
  });
  const px = s.type === "pen" ? along(s.pts, Math.min(24, s.pts.length)).map(r) : s.pts.map(r);
  const out = { type: s.type, ...(s.text ? { text: s.text } : {}), ...(closed ? { closed: true } : {}), px, pts, ...(inside ? { inside } : {}) };
  if (ray) {
    const sm = outline(s, b, c, closed) || [key[0][0]];
    const path = plane ? sm.map(onPl).filter(Boolean) : lift(sm, pick, ray);
    if (path && path.length) out.path = path.map(r1); else out.flat = true;
    if (s.type === "dim" && pts.length === 2 && pts.every((p) => p.at)) out.length = Math.round(Math.hypot(...sub(pts[1].at, pts[0].at)) * 10) / 10;
    if (plane) out.plane = { at: r1(plane.at), normal: plane.normal, ...(plane.part ? { part: plane.part } : {}) };
  }
  return out;
}
// ---- "this face": the flat face a click landed on = the triangles that share edges with the hit one and lie in its plane ----
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm = (v) => { const l = Math.hypot(...v); return l > 0 ? v.map((x) => x / l) : null; };
function hull2(P) {             // convex hull, monotone chain
  const p = [...P].sort((a, b) => a[0] - b[0] || a[1] - b[1]), cr = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lo = [], up = [];
  for (const q of p) { while (lo.length > 1 && cr(lo[lo.length - 2], lo[lo.length - 1], q) <= 0) lo.pop(); lo.push(q); }
  for (const q of p.reverse()) { while (up.length > 1 && cr(up[up.length - 2], up[up.length - 1], q) <= 0) up.pop(); up.push(q); }
  return lo.slice(0, -1).concat(up.slice(0, -1));
}
export function faceRegion(tris, start, eps = {}) {
  const dist = eps.dist ?? 0.05, cosA = Math.cos(((eps.angle ?? 1) * Math.PI) / 180);
  const nrm = (t) => norm(cross(sub(t[1], t[0]), sub(t[2], t[0])));
  const n0 = nrm(tris[start]); if (!n0) return null;
  const d = dot(n0, tris[start][0]), ek = (a, b) => (a < b ? a + "|" + b : b + "|" + a);
  const cells = new Map(), ids = [], TOL = 0.01;   // weld: a vertex within 0.01 mm of an earlier one is that vertex (the 27 cells round it, not a rounding grid)
  const vk = (v) => {
    const c = v.map((x) => Math.floor(x / TOL));
    for (let i = -1; i <= 1; i++) for (let j = -1; j <= 1; j++) for (let k = -1; k <= 1; k++)
      for (const id of cells.get(`${c[0] + i},${c[1] + j},${c[2] + k}`) || []) if (Math.hypot(...sub(ids[id], v)) <= TOL) return id;
    const key = c.join(","); ids.push(v); if (!cells.has(key)) cells.set(key, []); cells.get(key).push(ids.length - 1);
    return ids.length - 1;
  };
  const ok = (t) => { const n = nrm(t); return n && dot(n, n0) >= cosA && t.every((v) => Math.abs(dot(n0, v) - d) <= dist); };
  // only the triangles in that plane can belong to the face: test the plane first (3 dot products each), weld just those
  const cosSoft = Math.cos((15 * Math.PI) / 180), soft = new Set();   // a neighbour bent only a little away: the click was on a curved surface
  const cand = [], vkeys = [];
  tris.forEach((t, i) => {
    if (i === start || ok(t)) { cand.push(i); vkeys[i] = t.map(vk); return; }
    const n = nrm(t); if (n && dot(n, n0) >= cosSoft) { soft.add(i); cand.push(i); vkeys[i] = t.map(vk); }
  });
  const edges = new Map();
  for (const i of cand) { const k = vkeys[i]; for (const [a, b] of [[0, 1], [1, 2], [2, 0]]) { const e = ek(k[a], k[b]); if (!edges.has(e)) edges.set(e, []); edges.get(e).push(i); } }
  const seen = new Set([start]), q = [start];
  let bent = 0;
  while (q.length) {
    const i = q.pop(), k = vkeys[i];
    for (const [a, b] of [[0, 1], [1, 2], [2, 0]]) for (const j of edges.get(ek(k[a], k[b])) || []) {
      if (soft.has(j)) { bent++; continue; }
      if (!seen.has(j)) { seen.add(j); q.push(j); }
    }
  }
  const out = [...seen].sort((a, b) => a - b);
  let area = 0; const c = [0, 0, 0];
  for (const i of out) {
    const t = tris[i], a = Math.hypot(...cross(sub(t[1], t[0]), sub(t[2], t[0]))) / 2;
    area += a; for (let k = 0; k < 3; k++) c[k] += (a * (t[0][k] + t[1][k] + t[2][k])) / 3;
  }
  const centre = area > 0 ? c.map((x) => x / area) : tris[start][0];
  // its size: the smallest rectangle round it in its own plane (rotating calipers on the hull), long side first
  const u = norm(cross(Math.abs(n0[0]) < 0.9 ? [1, 0, 0] : [0, 1, 0], n0)), w = cross(n0, u);
  const H = hull2(out.flatMap((i) => tris[i].map((v) => [dot(sub(v, centre), u), dot(sub(v, centre), w)])));
  let best = null;
  for (let i = 0; i < H.length; i++) {
    const e = norm([H[(i + 1) % H.length][0] - H[i][0], H[(i + 1) % H.length][1] - H[i][1], 0]); if (!e) continue;
    const xs = H.map((p) => p[0] * e[0] + p[1] * e[1]), ys = H.map((p) => -p[0] * e[1] + p[1] * e[0]);
    const sz = [Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)];
    if (!best || sz[0] * sz[1] < best[0] * best[1]) best = sz;
  }
  const size = (best || [0, 0]).sort((a, b) => b - a);
  return { tris: out, normal: n0, centre, area, size, plane: { normal: n0, d }, ...(bent >= 2 ? { curved: true } : {}) };
}
export const projectShapes = (shapes, pick, opts) => shapes.map((s) => projectShape(s, pick, opts));
export function shapeParts(list) {   // the part ids the projected shapes hit, first appearance first
  const out = [];
  for (const s of list) for (const k of [...s.pts.map((p) => p.part), ...Object.keys(s.inside || {})]) if (k && !out.includes(k)) out.push(k);
  return out;
}

export function mountFeedback({ api, L, cfg }) {
  const F = L.fb, W = () => window.innerWidth, H = () => window.innerHeight;
  const css = document.createElement("style");
  css.textContent = `#fbOpen{position:fixed;z-index:4;top:16px;right:16px;width:40px;height:40px;padding:0;border:0;border-radius:50%;background:#fff;color:var(--ink);
  box-shadow:0 1px 6px #0002;display:flex;align-items:center;justify-content:center}
#info:not([hidden]) ~ #fbOpen{right:372px} .hc #info:not([hidden]) ~ #fbOpen{right:462px}
#fbDraw{position:fixed;inset:0;z-index:3;touch-action:none;pointer-events:none}
#fbDraw.live{pointer-events:auto;cursor:crosshair}
#fbTop{position:fixed;z-index:6;top:16px;left:8px;right:8px;display:flex;flex-direction:column;align-items:center;gap:6px;pointer-events:none}
#fbTop>*{pointer-events:auto}
#fbBar{display:flex;flex-wrap:wrap;justify-content:center;align-items:center;gap:2px;padding:4px;background:#fff;border-radius:22px;box-shadow:0 2px 10px #0000001f;max-width:calc(100vw - 16px)}
#fbBar button{width:36px;height:36px;padding:0;border:0;border-radius:9px;background:none;color:#2b3137;display:flex;align-items:center;justify-content:center}
#fbBar button.on{background:#e9ecef} #fbBar button[data-tool="plane"].set{color:#1c7ed6;background:#e7f1fb} #fbBar button:hover{background:#f1f3f5} #fbBar i{width:1px;height:22px;background:#dee2e6;margin:0 3px}
#fbHint{max-width:320px;background:#fff;border-radius:8px;box-shadow:0 1px 6px #0002;padding:4px 10px;font-size:12px;color:var(--muted);text-align:center}
#fbPop{position:fixed;z-index:7;width:300px;max-width:calc(100vw - 16px);background:#fff;border-radius:12px;box-shadow:0 4px 18px #0000002e;padding:10px}
#fbPop textarea{width:100%;min-height:64px;resize:vertical;font:inherit;padding:6px 8px;border:1px solid var(--line);border-radius:8px}
#fbPop .row{justify-content:flex-end;margin:8px 0 0} #fbPop .st{font-size:12px;color:#c92a2a;margin-top:6px} #fbPop .st.ok{color:#2b8a3e;font-weight:600}
#fbPop .send{background:var(--acc);border-color:var(--acc);color:#fff} #fbPop .send:disabled{opacity:.5;cursor:default}
.fbTxt{position:fixed;z-index:6;font:600 18px/1.2 -apple-system,system-ui,sans-serif;color:${RED};background:#fffd;border:1px dashed ${RED};border-radius:4px;padding:1px 4px;min-width:60px;outline:none}
.fbMic{width:34px;height:34px;padding:0;flex:0 0 34px !important;border:1px solid var(--line);border-radius:8px;background:#fff;color:#2b3137;display:flex;align-items:center;justify-content:center}
.fbMic.rec{background:${RED};border-color:${RED};color:#fff} .fbMic:disabled{opacity:.5;cursor:default}
#fbPart{margin-top:10px;border-top:1px solid var(--line);padding-top:10px} #fbPart textarea{width:100%;min-height:56px;resize:vertical;font:inherit;padding:6px 8px;border:1px solid var(--line);border-radius:8px}
#fbPart .row{margin:6px 0 0;align-items:center} #fbPart .send{background:var(--acc);border-color:var(--acc);color:#fff} #fbPart .send:disabled{opacity:.5;cursor:default}
#fbPart .st{font-size:12px;color:#c92a2a;margin-top:6px} #fbPart .st.ok{color:#2b8a3e;font-weight:600}
@media(max-width:700px){#fbOpen{top:auto;bottom:96px;right:8px} #info:not([hidden]) ~ #fbOpen{right:8px} #fbTop{top:auto;bottom:96px;flex-direction:column-reverse} #fbBar button{width:34px;height:34px}}`;
  document.head.appendChild(css);

  const open = document.createElement("button"); open.id = "fbOpen"; open.title = F.open; open.setAttribute("aria-label", F.open); open.innerHTML = ICON.pen;
  const info = document.getElementById("info"); info ? info.after(open) : document.body.appendChild(open);   // after #info: the CSS moves it aside while the card is open
  // a firmware machine's operator-app panel (#appPanel, top-right on desktop) would cover the button: keep it just left of the panel
  const dodgeApp = () => {
    const ap = document.getElementById("appPanel"); open.style.right = "";
    if (!ap || ap.hidden || innerWidth <= 700) return;
    const r = ap.getBoundingClientRect(), b = open.getBoundingClientRect();
    if (r.width && r.left < b.right && r.top < b.bottom && r.bottom > b.top) open.style.right = Math.round(innerWidth - r.left + 12) + "px";
  };
  addEventListener("resize", dodgeApp); setInterval(dodgeApp, 1000); dodgeApp();
  const draw = document.createElement("canvas"); draw.id = "fbDraw"; document.body.appendChild(draw);
  const top = document.createElement("div"); top.id = "fbTop"; top.hidden = true;
  top.innerHTML = `<div id="fbBar">${TOOLS.map((t) => `<button data-tool="${t}" title="${F.tools[t]}" aria-label="${F.tools[t]}">${ICON[t]}</button>`).join("")}<i></i><button data-close title="${F.close}" aria-label="${F.close}">${ICON.close}</button></div><div id="fbHint" hidden></div>`;
  document.body.appendChild(top);
  if (!api.THREE || !api.rayAt) top.querySelector('[data-tool="plane"]').hidden = true;   // an index.html older than this file: no 3D notes
  const pop = document.createElement("div"); pop.id = "fbPop"; pop.hidden = true;
  pop.innerHTML = `<textarea placeholder="${F.ask}"></textarea><div class="st" hidden></div><div class="row"><button class="cancel">${F.cancel}</button><button class="send" disabled>${F.send}</button></div>`;
  document.body.appendChild(pop);
  const fbUrl = new URL(cfg.feedback === "auto" ? "/feedback/" + location.pathname.split("/")[1] : cfg.feedback, location.href);
  const sttUrl = fbUrl.pathname.includes("/feedback/") ? new URL(fbUrl.pathname.replace("/feedback/", "/stt/"), fbUrl) : null;   // a custom feedback URL: no mic
  const ta = pop.querySelector("textarea"), st = pop.querySelector(".st"), bSend = pop.querySelector(".send"), bCancel = pop.querySelector(".cancel"), hint = top.querySelector("#fbHint");
  const popMic = micButton(ta, st, () => place());
  if (popMic) pop.querySelector(".row").prepend(popMic.el);

  let tool = null, shapes = [], cur = null, snap = null, busy = false, txt = null, views = [], plane = null, lastDraw = "pen", hintCam = null;
  const ctx = draw.getContext("2d");
  const rnd = (a, d) => a.map((x) => Math.round(x * 10 ** d) / 10 ** d);
  const pick3 = (x, y) => {     // a pixel of the drawing -> the part under it and the point in assembly mm (the camera is still that of the frame)
    const h = api.pick(x, y), asm = api.toAsm || ((v) => v);   // an index.html older than this file (a tab open across a deploy): viewer frame
    return h && h.part ? { part: h.part, n: h.n, at: rnd(asm(h.point), 1), normal: h.normal ? rnd(asm(h.normal, true), 3) : null } : null;
  };
  const pickOn3 = (x, y, id) => { const h = api.pickOn(x, y, id); return h ? { part: h.part, n: h.n, at: rnd(api.toAsm(h.point), 1) } : null; };
  const L_MM = L.mm;
  const fmt1 = (x) => (Math.round(x * 10) / 10).toFixed(1).replace(".", L.decimal || ".");
  const trisOf = new Map();   // a mesh's triangles in assembly mm, once per mesh (a big part has tens of thousands)
  const meshTris = (n) => { if (!trisOf.has(n)) trisOf.set(n, api.meshTris ? api.meshTris(n) : null); return trisOf.get(n); };
  const keep = (s) => {       // projected now, while the camera is the one it was drawn on; then it lives in the 3D scene and survives a rotation
    let fr = null;
    if (s.type === "face") {  // the whole flat face under the click, from that part's own triangles (found before anything is counted)
      const h = api.pick(s.pts[0][0], s.pts[0][1]), tris = h && meshTris(h.n), f = tris && h.face != null ? faceRegion(tris, h.face) : null;
      if (!f) return false;
      fr = { h, f, tris };
    }
    const v = viewIdx();
    s.proj = projectShape(s, pick3, api.rayAt ? { ray: api.rayAt, plane: plane && plane.data, pickOn: api.pickOn && pickOn3 } : {});
    s.view = s.proj.view = v;
    if (s.type === "pin") s.proj.num = s.num = shapes.filter((x) => x.type === "pin").length + 1;
    if (fr) {
      const { h, f, tris } = fr, hn = h.normal && api.toAsm(h.normal, true);
      if (hn && dot(f.normal, hn) < 0) { f.normal = f.normal.map((x) => -x); f.plane.d = -f.plane.d; }   // the side clicked (a mirrored mesh winds the other way)
      s.region = f.tris.map((i) => tris[i]);
      s.proj.face = { part: h.part, n: h.n, tri: h.face, ...(f.curved ? { curved: true } : {}), normal: rnd(f.normal, 3), centre: rnd(f.centre, 1), area: f.area < 10 ? Math.round(f.area * 10) / 10 : Math.round(f.area),
                      size: rnd(f.size, 1), plane: { normal: rnd(f.normal, 3), d: Math.round(f.plane.d * 10) / 10 } };
    }
    shapes.push(s); build3d(s); api.invalidate();
    if (views[v]) views[v].pic = shot(MAX_SIDE);   // this view's picture with what is drawn so far (the chat gets one per view, an album), at its sent size
  };
  // ---- the views a note was drawn from: a new one each time the camera moved between shapes ----
  function viewIdx() {
    const ci = api.camInfo ? api.camInfo() : null, last = views[views.length - 1];
    if (!ci) return 0;
    const same = last && ci.view.every((x, i) => Math.abs(x - last.camera.view[i]) <= 1e-4 * (1 + Math.abs(x)));   // the camera, not the window size (a phone keyboard)
    if (!same) {
      views.push({ camera: ci });
      if (views.length > MAX_VIEWS) {   // the last 4 views only (the album's size): an older shape keeps its 3D place but no longer names a view
        views.shift();
        for (const s of shapes) { s.view -= 1; if (s.view < 0) { s.view = -1; delete s.proj.view; } else s.proj.view = s.view; }
      }
    }
    return views.length - 1;
  }
  // ---- shapes in 3D: tubes along the path (screen-constant width), an arrowhead cone, text as a sprite; depth-tested, so a part in front hides them ----
  const T = api.THREE, MAT = T && new T.MeshBasicMaterial({ color: RED, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -4 });
  const toW = (p) => new T.Vector3(...api.fromAsm(p));
  const perPx = (at) => (2 * api.camera.position.distanceTo(at) * Math.tan((api.camera.fov * Math.PI) / 360)) / Math.max(1, H());   // mm per CSS px there
  function build3d(s) {
    if (!T || !s.proj.path || !api.fromAsm) return;
    const P = s.proj.path.map(toW), ctr = P.reduce((a, p) => a.add(p), new T.Vector3()).multiplyScalar(1 / P.length);
    s.g = { P, ctr, k: 0, closed: s.type === "rect" || s.type === "ellipse" || !!s.proj.closed, obj: null };
    restyle(s);
  }
  function label(text, at, k, round) {   // a red-on-white label sprite ~18 px high at a 3D point, kept in front of its face by the ticker
    const c = document.createElement("canvas"), x = c.getContext("2d"), f = "700 36px -apple-system,system-ui,sans-serif";
    x.font = f; const tw = Math.ceil(x.measureText(text).width);
    c.width = round ? 48 : tw + 24; c.height = 48;
    x.font = f; x.textAlign = "center"; x.textBaseline = "middle";
    if (round) { x.fillStyle = RED; x.beginPath(); x.arc(24, 24, 22, 0, Math.PI * 2); x.fill(); x.fillStyle = "#fff"; }
    else { x.fillStyle = "#fffe"; x.strokeStyle = RED; x.lineWidth = 4; x.beginPath(); x.roundRect(2, 2, c.width - 4, 44, 10); x.fill(); x.stroke(); x.fillStyle = RED; }
    x.fillText(text, c.width / 2, 26);
    const sp = new T.Sprite(new T.SpriteMaterial({ map: new T.CanvasTexture(c), depthTest: true }));
    sp.userData.anchor = at.clone(); sp.scale.set((c.width / 2.6) * k, (c.height / 2.6) * k, 1);
    sp.position.copy(at).addScaledVector(api.camera.position.clone().sub(at).normalize(), sp.scale.x / 2);   // in front of its face from the start (the view's picture is taken now)
    return sp;
  }
  function restyle(s) {       // (re)build at the width that is ~LW px on screen from the camera now
    const g = s.g, k = perPx(g.ctr);
    if (g.obj && (s.type === "face" || Math.abs(Math.log(k / g.k)) < 0.25)) return false;   // a tinted face has no on-screen width
    if (g.obj) dispose(g.obj);
    g.k = k; g.obj = new T.Group(); g.obj.userData.fbShape = true;
    const r = (LW / 2) * k;
    if (s.type === "face" && s.region) {   // the face itself, tinted, a hair in front of the part
      const pos = s.region.flatMap((t) => t.flatMap((v) => api.fromAsm(v))), geo = new T.BufferGeometry();
      geo.setAttribute("position", new T.Float32BufferAttribute(pos, 3));
      g.obj.add(new T.Mesh(geo, new T.MeshBasicMaterial({ color: RED, transparent: true, opacity: 0.45, depthWrite: false, side: T.DoubleSide,
        polygonOffset: true, polygonOffsetFactor: -4, polygonOffsetUnits: -8 })));
    } else if (s.type === "pin") {
      g.obj.add(new T.Mesh(new T.SphereGeometry(3 * k, 12, 8), MAT), label(String(s.num || 1), g.P[0], k, true));
      g.obj.children[0].position.copy(g.P[0]);
    } else if ((s.type === "move" || s.type === "dim") && g.P.length > 1) {
      const a = g.P[0], b = g.P[g.P.length - 1], d = b.clone().sub(a), len = d.length(); d.normalize();
      const seg = (p, q, rr) => { const m = new T.Mesh(new T.CylinderGeometry(rr, rr, p.distanceTo(q), 6), MAT); m.position.copy(p).add(q).multiplyScalar(0.5); m.quaternion.setFromUnitVectors(new T.Vector3(0, 1, 0), q.clone().sub(p).normalize()); return m; };
      const dot3 = (p, rr) => { const m = new T.Mesh(new T.SphereGeometry(rr, 10, 8), MAT); m.position.copy(p); return m; };
      if (s.type === "move") {  // dashed, with a head: «from here to there»
        const h = 14 * k;
        for (let t = 0; t < len - h; t += 13 * k) g.obj.add(seg(a.clone().addScaledVector(d, t), a.clone().addScaledVector(d, Math.min(t + 8 * k, len - h)), r));
        const cone = new T.Mesh(new T.ConeGeometry(5 * k, h, 12), MAT);
        cone.position.copy(b).addScaledVector(d, -h / 2); cone.quaternion.setFromUnitVectors(new T.Vector3(0, 1, 0), d); g.obj.add(cone, dot3(a, 3 * k));
      } else {                  // a dimension: a thin line, an end mark each side and the value
        g.obj.add(seg(a, b, r * 0.6), dot3(a, 2.5 * k), dot3(b, 2.5 * k));
        if (s.proj.length != null) g.obj.add(label(`${fmt1(s.proj.length)} ${L_MM}`, a.clone().add(b).multiplyScalar(0.5), k, false));
      }
    } else if (s.type === "text") {
      const c = document.createElement("canvas"), x = c.getContext("2d"), f = "600 36px -apple-system,system-ui,sans-serif";
      x.font = f; c.width = Math.ceil(x.measureText(s.text).width) + 16; c.height = 48;
      x.font = f; x.textBaseline = "middle"; x.lineJoin = "round"; x.lineWidth = 8; x.strokeStyle = "#fff"; x.strokeText(s.text, 8, 24); x.fillStyle = RED; x.fillText(s.text, 8, 24);
      const sp = new T.Sprite(new T.SpriteMaterial({ map: new T.CanvasTexture(c), depthTest: true }));
      sp.position.copy(g.P[0]); sp.scale.set((c.width / 2) * k, (c.height / 2) * k, 1); g.obj.add(sp);
    } else if (g.P.length > 1) {
      const path = new T.CurvePath();
      for (let i = 0; i < g.P.length - 1; i++) path.add(new T.LineCurve3(g.P[i], g.P[i + 1]));
      if (g.closed) path.add(new T.LineCurve3(g.P[g.P.length - 1], g.P[0]));
      g.obj.add(new T.Mesh(new T.TubeGeometry(path, Math.min(256, path.curves.length * 2), r, 6, false), MAT));
      if (s.type === "arrow") {
        const a = g.P[0], b = g.P[g.P.length - 1], d = b.clone().sub(a).normalize(), h = 14 * k;
        const cone = new T.Mesh(new T.ConeGeometry(5 * k, h, 12), MAT);
        cone.position.copy(b).addScaledVector(d, -h / 2); cone.quaternion.setFromUnitVectors(new T.Vector3(0, 1, 0), d); g.obj.add(cone);
      }
    }
    api.scene.add(g.obj);
    return true;
  }
  if (T) api.tickers.push(() => {
    if (hintCam && !hintCam.equals(api.camera.matrixWorld)) { hintCam = null; hint.hidden = true; }
    let ch = false;
    for (const s of shapes) {
      if (!s.g) continue;
      ch = restyle(s) || ch;
      for (const sp of s.g.obj ? s.g.obj.children : []) {   // a label faces the camera: pulled towards it by half its width, so the face it sits on never cuts it
        const at = sp.isSprite && (sp.userData.anchor || (s.type === "text" && s.g.P[0])); if (!at) continue;
        const want = at.clone().addScaledVector(api.camera.position.clone().sub(at).normalize(), sp.scale.x / 2);
        if (want.distanceToSquared(sp.position) > 1e-6) { sp.position.copy(want); ch = true; }
      }
    }
    if (ch) api.invalidate();
  });
  function dispose(obj) { api.scene.remove(obj); obj.traverse((o) => { if (o.geometry) o.geometry.dispose(); if (o.material && o.material !== MAT) { if (o.material.map) o.material.map.dispose(); o.material.dispose(); } }); }
  function drop3d() { for (const s of shapes) if (s.g && s.g.obj) dispose(s.g.obj); }
  // ---- the drawing plane: a face, extended past the part's edge (a sketch on a face, as in CAD) ----
  function setPlane(x, y) {
    const h = pick3(x, y); if (!h || !h.normal) return false;
    clearPlane();
    const b = api.box ? api.box(h.n) : null, size = b ? Math.max(60, 1.0 * Math.hypot(b[1][0] - b[0][0], b[1][1] - b[0][1], b[1][2] - b[0][2])) : 200;
    const m = new T.Mesh(new T.PlaneGeometry(size, size), new T.MeshBasicMaterial({ color: 0x1c7ed6, transparent: true, opacity: 0.2, depthWrite: false, side: T.DoubleSide }));
    m.add(new T.LineSegments(new T.EdgesGeometry(m.geometry), new T.LineBasicMaterial({ color: 0x1c7ed6 })));
    const n = new T.Vector3(...api.fromAsm(h.normal)).normalize();
    m.position.copy(toW(h.at)).addScaledVector(n, 0.05 * perPx(toW(h.at))); m.quaternion.setFromUnitVectors(new T.Vector3(0, 0, 1), n);
    api.scene.add(m); api.invalidate();
    plane = { data: { at: h.at, normal: h.normal, part: h.part }, mesh: m };
    return true;
  }
  function clearPlane() { if (!plane) return; api.scene.remove(plane.mesh); plane.mesh.traverse((o) => { if (o.geometry) o.geometry.dispose(); if (o.material) o.material.dispose(); }); plane = null; api.invalidate(); }
  let dpr = 1;
  function size() {
    dpr = Math.min(window.devicePixelRatio || 1, 3);
    draw.width = Math.round(W() * dpr); draw.height = Math.round(H() * dpr); draw.style.width = W() + "px"; draw.style.height = H() + "px";
    paint(); if (!pop.hidden) place();
  }
  window.addEventListener("resize", size);

  // ---- shapes: {type, pts: [[x,y], ...]} in CSS px; text: {type: "text", pts: [[x,y]], text} (x,y = top-left) ----
  function stroke(c, s, k) {     // k = CSS px -> canvas px
    c.strokeStyle = RED; c.fillStyle = RED; c.lineWidth = Math.max(2, LW * k); c.lineCap = c.lineJoin = "round";
    const P = s.pts.map((p) => [p[0] * k, p[1] * k]), a = P[0], b = P[P.length - 1];
    c.beginPath();
    if (s.type === "pen") { c.moveTo(a[0], a[1]); P.forEach((p) => c.lineTo(p[0], p[1])); c.stroke(); }
    else if (s.type === "pin") {
      c.arc(a[0], a[1], 9 * k, 0, Math.PI * 2); c.fill();
      c.fillStyle = "#fff"; c.font = `700 ${12 * k}px sans-serif`; c.textAlign = "center"; c.textBaseline = "middle"; c.fillText(String(s.num || ""), a[0], a[1]);
      c.textAlign = "start";
    } else if (s.type === "move" || s.type === "dim") {
      if (s.type === "move") c.setLineDash([8 * k, 5 * k]);
      c.moveTo(a[0], a[1]); c.lineTo(b[0], b[1]); c.stroke(); c.setLineDash([]);
      const ang = Math.atan2(b[1] - a[1], b[0] - a[0]), h = Math.max(10, 16 * k);
      c.beginPath();
      if (s.type === "move") for (const d of [-0.45, 0.45]) { c.moveTo(b[0], b[1]); c.lineTo(b[0] - h * Math.cos(ang + d), b[1] - h * Math.sin(ang + d)); }
      else for (const p of [a, b]) { c.moveTo(p[0] - 6 * k * Math.sin(ang), p[1] + 6 * k * Math.cos(ang)); c.lineTo(p[0] + 6 * k * Math.sin(ang), p[1] - 6 * k * Math.cos(ang)); }
      c.stroke();
    } else if (s.type === "line" || s.type === "arrow") {
      c.moveTo(a[0], a[1]); c.lineTo(b[0], b[1]);
      if (s.type === "arrow") {
        const ang = Math.atan2(b[1] - a[1], b[0] - a[0]), h = Math.max(10, 16 * k);
        for (const d of [-0.45, 0.45]) { c.moveTo(b[0], b[1]); c.lineTo(b[0] - h * Math.cos(ang + d), b[1] - h * Math.sin(ang + d)); }
      }
      c.stroke();
    } else if (s.type === "rect") { c.strokeRect(Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.abs(b[0] - a[0]), Math.abs(b[1] - a[1])); }
    else if (s.type === "ellipse") { c.ellipse((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, Math.abs(b[0] - a[0]) / 2, Math.abs(b[1] - a[1]) / 2, 0, 0, Math.PI * 2); c.stroke(); }
    else if (s.type === "text") {
      c.font = `600 ${18 * k}px -apple-system,system-ui,sans-serif`; c.textBaseline = "top";
      c.lineWidth = 4 * k; c.strokeStyle = "#fff"; c.strokeText(s.text, a[0], a[1]); c.fillText(s.text, a[0], a[1]);   // white halo: readable on dark and light parts
    }
  }
  function bbox(list) {
    let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
    for (const s of list) {
      let pts = s.pts;
      if (s.type === "text") { ctx.font = "600 18px -apple-system,system-ui,sans-serif"; const p = s.pts[0]; pts = [p, [p[0] + ctx.measureText(s.text).width, p[1] + 22]]; }
      for (const p of pts) { x0 = Math.min(x0, p[0]); y0 = Math.min(y0, p[1]); x1 = Math.max(x1, p[0]); y1 = Math.max(y1, p[1]); }
    }
    return { x0, y0, x1, y1 };
  }
  function paint() {
    ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, draw.width, draw.height);
    for (const s of shapes.filter((x) => !x.g).concat(cur ? [cur] : [])) stroke(ctx, s, dpr);   // the rest is in the 3D scene
  }

  // ---- mode: which tool, who gets the pointer. The select tool leaves the camera free: the shapes are in 3D and turn with the model ----
  function setTool(t) {
    commitText();
    if (!["select", "plane", "pin", "face"].includes(t)) lastDraw = t;
    tool = t;
    top.querySelectorAll("[data-tool]").forEach((b) => { b.classList.toggle("on", b.dataset.tool === t); if (b.dataset.tool === "plane") b.classList.toggle("set", !!plane); });
    sync();
  }
  function sync() {      // a drawing tool takes the pointer; the select tool hands it back to the camera
    draw.classList.toggle("live", !!tool && tool !== "select");
    const cb = document.getElementById("cyclebar"); if (cb) cb.style.visibility = shapes.length ? "hidden" : "";   // the note's moment stays put
    const h = tool === "plane" ? F.planeHint : tool === "pin" || tool === "face" ? F.clickHint : tool === "move" || tool === "dim" ? F.dragHint
      : tool === "select" && shapes.length ? F.rotate : "";
    hint.hidden = !h; hint.textContent = h;
    hintCam = h === F.rotate ? api.camera.matrixWorld.clone() : null;   // the rotate hint goes once the model is turned: it said its piece
    if (!pop.hidden) place();
  }
  function showBar(on) {
    top.hidden = !on; open.hidden = on;
    if (on) setTool("pen"); else { clearAll(); tool = null; sync(); }
  }
  function clearAll() {
    if (popMic) popMic.abort();
    if (txt) { const el = txt; txt = null; el.remove(); }
    drop3d(); clearPlane(); trisOf.clear();
    shapes = []; views = []; cur = null; snap = null; pop.hidden = true; ta.value = ""; st.hidden = true; bSend.disabled = true; busy = false; bCancel.disabled = false; paint(); sync(); api.invalidate();
  }
  open.addEventListener("click", () => showBar(true));
  top.addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    if (b.hasAttribute("data-close")) showBar(false);
    else if (b.dataset.tool === "plane" && plane) { clearPlane(); setTool(lastDraw); }   // pressed again: back to drawing on the parts
    else setTool(b.dataset.tool);
  });

  // the moment of this note (the cycle and the sim pause at the first stroke); its pictures are taken at send, the shapes being in the scene
  function capture() {
    const s = api.cycle ? api.cycle.state() : null;
    snap = { cycle_t: s ? Math.round(s.t * 1000) / 1000 : null, sim: simSnap() };
  }
  function shot(side) {       // the view on screen, without the drawing plane's patch; `side`: kept no bigger than that (a phone at dpr 3 is ~12 MB a frame)
    if (plane) plane.mesh.visible = false;
    const f = frame();
    if (plane) { plane.mesh.visible = true; api.invalidate(); }
    const k = side ? Math.min(1, side / Math.max(f.frame.width, f.frame.height)) : 1;
    if (k < 1) {
      const c = document.createElement("canvas"); c.width = Math.round(f.frame.width * k); c.height = Math.round(f.frame.height * k);
      c.getContext("2d").drawImage(f.frame, 0, 0, c.width, c.height); f.frame = c;
    }
    return f;
  }
  function frame() {          // this view now: render and copy in the same tick (no preserveDrawingBuffer)
    api.renderNow();
    const g = api.canvas, c = document.createElement("canvas"); c.width = g.width; c.height = g.height;
    c.getContext("2d").drawImage(g, 0, 0);
    return { frame: c, w: W(), h: H() };
  }
  // a firmware machine has no timeline: what was going on at this moment = the sim's own state, each actuator, the inputs, the machine's
  // world (weights, cells…) and the last serial lines per board. Small and JSON-only; a field that fails is left out, never breaks the note.
  function simSnap() {
    const sim = api.sim; if (!sim || !sim.state) return null;
    const take = (f) => { try { const v = f(); const j = JSON.stringify(v); return j && j.length < 6000 ? JSON.parse(j) : undefined; } catch (e) { return undefined; } };
    const st = take(() => sim.state()) || {};
    return { t_s: st.t, clock: st.clock, paused: st.paused, speed: st.speed, error: st.error || undefined, boards: st.boards, status: st.status,
             actuators: take(() => sim.actuators()), inputs: take(() => (sim.inputs() || []).map((i) => ({ id: i.id, label: i.label }))),
             world: take(() => sim.vm.m.world),
             serial: take(() => Object.fromEntries((st.boards || []).map((b) => [b.id, (sim.logs(b.id) || []).slice(-15)]))) };
  }

  // ---- pointer drawing (first pointer only: a second finger is ignored, not a pinch) ----
  let pid = null, textAt = null;
  draw.addEventListener("pointerdown", (e) => {
    textAt = null;
    if (busy) return;
    if (tool === "select") return;
    if (pid !== null) return;
    if (txt) { commitText(); return; }          // a click outside the text box ends it
    e.preventDefault();
    if (api.cycle) api.cycle.pause(); if (api.sim && api.sim.pause) api.sim.pause();   // the moment drawing starts is the frame of the note
    if (!snap) capture();
    const p = [e.clientX, e.clientY];
    if (tool === "plane") { if (setPlane(p[0], p[1])) setTool(lastDraw); return; }
    if (tool === "pin" || tool === "face") {   // one click on a part; empty space makes nothing (a pin may sit on the drawing plane)
      if (pick3(p[0], p[1]) || (tool === "pin" && plane)) keep({ type: tool, pts: [p] });
      paint(); place(); sync(); return;
    }
    if (tool === "text") { textAt = p; return; }   // the box opens on `click`: a tap's synthetic mouse events move focus after pointerdown and would blur it
    pid = e.pointerId; try { draw.setPointerCapture(pid); } catch (err) { /* synthetic */ }
    cur = { type: tool, pts: tool === "pen" ? [p] : [p, p] };
  });
  draw.addEventListener("pointermove", (e) => {
    if (e.pointerId !== pid || !cur) return;
    const p = [e.clientX, e.clientY];
    if (cur.type === "pen") cur.pts.push(p); else cur.pts[1] = p;
    paint();
  });
  function endDraw(e) {
    if (e.pointerId !== pid) return;
    pid = null;
    const s = cur; cur = null;
    const b = s && bbox([s]);
    if (s && e.type === "pointerup" && Math.max(b.x1 - b.x0, b.y1 - b.y0) > 4) keep(s);   // a bare click draws nothing
    paint(); place(); sync();
  }
  draw.addEventListener("click", () => { if (textAt && tool === "text" && !txt) startText(textAt); textAt = null; });
  draw.addEventListener("pointerup", endDraw); draw.addEventListener("pointercancel", endDraw);
  draw.addEventListener("wheel", (e) => e.preventDefault(), { passive: false });

  function startText(p) {
    txt = document.createElement("div"); txt.className = "fbTxt"; txt.contentEditable = "true";
    txt.style.left = p[0] + "px"; txt.style.top = p[1] - 4 + "px"; txt.dataset.x = p[0]; txt.dataset.y = p[1];
    document.body.appendChild(txt);
    txt.focus();
    txt.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); commitText(); }
      else if (e.key === "Escape") { txt.textContent = ""; commitText(); }
    });
    txt.addEventListener("blur", () => commitText());
  }
  function commitText() {
    if (!txt) return;
    const el = txt, t = el.textContent.trim(), x = +el.dataset.x, y = +el.dataset.y;
    txt = null; el.remove();     // null first: remove() fires blur, which calls back in here
    if (t) { ctx.font = "600 18px -apple-system,system-ui,sans-serif"; keep({ type: "text", pts: [[x, y]], text: t, w: ctx.measureText(t).width }); }
    paint(); place(); sync();
  }

  // ---- popup at the bottom of the screen, above the cycle bar: the drawing stays free to see, turn and add to from any side ----
  function place() {
    if (!shapes.length) { pop.hidden = true; return; }
    const wasHidden = pop.hidden; pop.hidden = false;
    const pw = pop.offsetWidth, ph = pop.offsetHeight, m = 8, x = Math.max(m, Math.min(W() - pw - m, W() / 2 - pw / 2));
    // never over the toolbar or its hint (on a phone they sit at the bottom) nor over the cycle bar
    const tb = [...top.children].filter((c) => !c.hidden).map((c) => c.getBoundingClientRect()).filter((r) => r.left < x + pw && r.right > x);
    const cb = document.getElementById("cyclebar"), cr = cb && !cb.hidden && cb.style.visibility !== "hidden" ? cb.getBoundingClientRect() : null;
    const low = [H() - m, ...tb.filter((r) => r.top > H() / 2).map((r) => r.top - m), ...(cr && cr.height && cr.left < x + pw && cr.right > x ? [cr.top - m] : [])];
    pop.style.left = x + "px"; pop.style.top = Math.max(m, Math.min(...low) - ph) + "px";
    if (wasHidden && !matchMedia("(pointer:coarse)").matches) ta.focus();   // phones: no keyboard popping over the drawing until they tap
  }
  ta.addEventListener("input", () => { bSend.disabled = busy || !ta.value.trim(); st.hidden = true; });
  bCancel.addEventListener("click", () => clearAll());

  // ---- send ----
  function image(sn, list, cap = MAX_B64) {
    const fw = sn.frame.width, fh = sn.frame.height, k = Math.min(1, MAX_SIDE / Math.max(fw, fh));   // device pixels: a phone frame keeps its detail
    let w = Math.round(fw * k), h = Math.round(fh * k), q = 0.85, url = "";
    for (let i = 0; i < 12; i++) {
      const c = document.createElement("canvas"); c.width = w; c.height = h;
      const g = c.getContext("2d");
      g.drawImage(sn.frame, 0, 0, w, h);
      list.forEach((s) => stroke(g, s, w / sn.w));
      url = c.toDataURL("image/jpeg", q);
      if (url.length < cap) break;
      if (q > 0.45) q -= 0.1; else { w = Math.round(w * 0.8); h = Math.round(h * 0.8); }   // quality first, then pixels
    }
    return url;
  }
  bSend.addEventListener("click", async () => {
    if (busy || !shapes.length || !ta.value.trim()) return;
    busy = true; bSend.disabled = bCancel.disabled = true; st.hidden = true;
    if (api.writeHash) api.writeHash();
    const list = shapes.map((s) => s.proj), now = viewIdx();   // the picture is the view on screen now: a new one if the camera moved after the last shape
    // one picture per view drawn from (the earlier ones as they were after their last shape), the view on screen last; ≤ 4 in all, and
    // all of them under the host nginx's 1 MB body cap together
    const flat = (i) => shapes.filter((s) => !s.g && s.view === i);   // shapes that could not go to 3D are drawn flat, in their own view only
    const before = views.map((v, i) => [v, i]).filter(([v, i]) => i !== now && v.pic && shapes.some((s) => s.view === i)).slice(-3);
    const cap = MAX_B64 / (before.length + 1);
    const pic = image(shot(), flat(now), cap), more = before.map(([v, i]) => image(v.pic, flat(i), cap));
    const cams = views.map((v) => v.camera);
    const body = { comment: ta.value.trim(), image: pic, ...(more.length ? { images: more } : {}),
      view: { url: location.href, hash: location.hash, cycle_t: snap.cycle_t, parts: shapeParts(list), shapes: list,
              ...(cams.length ? { views: cams, camera: cams[cams.length - 1] } : {}), ...(snap.sim ? { sim: snap.sim } : {}) } };
    const err = await post(body);
    if (err && views.length && !shapes.some((s) => s.view === views.length - 1)) views.pop();   // the view taken for the picture only: not a view of the note
    if (err) { busy = false; bSend.disabled = !ta.value.trim(); bCancel.disabled = false; st.className = "st"; st.textContent = err; st.hidden = false; place(); return; }
    st.className = "st ok"; st.textContent = F.sent; st.hidden = false; place();
    setTimeout(() => { clearAll(); }, 2000);
  });

  async function post(body) {     // null when sent, else the message to show
    try {
      const r = await fetch(fbUrl, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (r.ok) return null;
      let j = null; try { j = await r.json(); } catch (e) { /* not json */ }
      return (j && j.error) || F.failed;
    } catch (e) { return F.failed; }
  }

  // ---- mic: record -> stt -> the text is appended to the box; the person edits it and sends as usual ----
  function micButton(box, status, moved = () => {}) {     // -> {el, abort} or null (no stt endpoint / no recorder in this browser)
    if (!sttUrl || !window.MediaRecorder || !navigator.mediaDevices) return null;
    const b = document.createElement("button"); b.type = "button"; b.className = "fbMic";
    let rec = null, timer = null, dropped = false;
    const look = (on) => { b.classList.toggle("rec", on); b.innerHTML = on ? ICON.stop : ICON.mic; b.title = on ? F.micStop : F.mic; b.setAttribute("aria-label", b.title); };
    const say = (t, muted) => { status.className = "st"; status.style.color = muted ? "var(--muted)" : ""; status.textContent = t || ""; status.hidden = !t; moved(); };
    look(false);
    b.addEventListener("click", async () => {
      if (rec) { rec.stop(); return; }
      let stream, r;
      try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); } catch (e) { say(F.micDenied); return; }
      const type = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find((t) => MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(t));
      try { r = new MediaRecorder(stream, { ...(type ? { mimeType: type } : {}), audioBitsPerSecond: MIC_BPS }); }
      catch (e) { stream.getTracks().forEach((t) => t.stop()); say(F.micFailed); return; }
      const chunks = [];
      let bytes = 0;
      rec = r; dropped = false;
      r.ondataavailable = (e) => {
        if (!e.data || !e.data.size) return;
        chunks.push(e.data); bytes += e.data.size;
        if (bytes >= MIC_MAX_B && r.state === "recording") r.stop();   // the size cap, whatever bitrate the browser really uses
      };
      r.onstop = async () => {
        clearTimeout(timer); stream.getTracks().forEach((t) => t.stop()); rec = null; look(false);
        if (dropped) return;                                        // the note was cancelled while recording
        const ctype = (r.mimeType || type || "audio/webm").split(";")[0], blob = new Blob(chunks, { type: ctype });
        if (!blob.size) return;
        if (blob.size > 1000 * 1000) { say(F.micLong); return; }
        b.disabled = true; say(F.micBusy, true);
        let text = "", err = F.micFailed;
        try {
          const res = await fetch(sttUrl, { method: "POST", headers: { "Content-Type": ctype }, body: blob });
          const j = await res.json().catch(() => null);
          if (res.ok && j && j.text) text = String(j.text).trim(); else if (j && j.error) err = j.error;
        } catch (e) { /* network: the generic message */ }
        b.disabled = false;
        if (!text) { say(err); return; }
        say("");
        box.value = (box.value.trim() ? box.value.trimEnd() + " " : "") + text;
        box.dispatchEvent(new Event("input"));
        box.focus();
      };
      r.start(1000);                 // 1 s chunks: the size check above runs while recording
      look(true); say("");
      timer = setTimeout(() => r.state !== "inactive" && r.stop(), MIC_MAX_S * 1000);
    });
    return { el: b, abort: () => { if (rec) { dropped = true; rec.stop(); } } };
  }

  // ---- the part card: what to change in the selected part (or node); the photo is the frame with the selection highlighted ----
  const infoBody = document.querySelector("#info .body");
  if (infoBody && api.selInfo) {
    const box = document.createElement("div"); box.id = "fbPart";
    box.innerHTML = `<textarea></textarea><div class="st" hidden></div><div class="row"><button class="send" disabled>${F.send}</button></div>`;
    infoBody.appendChild(box);
    const pta = box.querySelector("textarea"), pst = box.querySelector(".st"), pSend = box.querySelector(".send");
    const cardMic = micButton(pta, pst);
    if (cardMic) box.querySelector(".row").prepend(cardMic.el);
    let pbusy = false, target = null;
    const keyOf = (s) => (s ? (s.part ? "p:" + s.part : "n:" + s.n) : null);
    const refresh = () => {
      const s = api.selInfo();
      pta.placeholder = s && !s.part ? F.asmAsk : F.partAsk;
      pSend.disabled = pbusy || !s || !pta.value.trim();
    };
    api.selectHooks.push(() => {   // another part or node: the text was about the old one, so the box starts empty
      const k = keyOf(api.selInfo());
      if (k !== target && !pbusy) { target = k; pta.value = ""; if (cardMic) cardMic.abort(); pst.hidden = true; }
      refresh();
    });
    pta.addEventListener("input", () => { if (!pbusy && pst.classList.contains("ok")) pst.hidden = true; refresh(); });
    pSend.addEventListener("click", async () => {
      const s = api.selInfo();
      if (pbusy || !s || !pta.value.trim()) return;
      pbusy = true; refresh(); pst.hidden = true;
      api.renderNow();              // the highlighted selection, copied in the same tick (no preserveDrawingBuffer)
      const g = api.canvas, c = document.createElement("canvas"); c.width = g.width; c.height = g.height; c.getContext("2d").drawImage(g, 0, 0);
      const cs = api.cycle ? api.cycle.state() : null;
      if (api.writeHash) api.writeHash();
      const body = { comment: pta.value.trim(), image: image({ frame: c, w: W() }, []),
        view: { url: location.href, hash: location.hash, cycle_t: cs ? Math.round(cs.t * 1000) / 1000 : null,
                part: s.part || null, node: s.node || null, part_name: s.name || null, selected: s.part || null, parts: s.part ? [s.part] : [] } };
      const err = await post(body);
      pbusy = false; pst.style.color = "";
      if (err) { pst.className = "st"; pst.textContent = err; pst.hidden = false; refresh(); return; }
      pta.value = ""; pst.className = "st ok"; pst.textContent = F.sent; pst.hidden = false; refresh();
    });
    target = keyOf(api.selInfo());
    refresh();
  }

  size();
  api.feedback = { open: () => showBar(true), close: () => showBar(false), tool: setTool, state: () => ({ tool, shapes: shapes.length, views: views.length, popup: !pop.hidden, plane: !!plane }) };
  return api.feedback;
}
