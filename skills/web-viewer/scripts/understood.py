#!/usr/bin/env python3
""""Understood like this": the client's drawn note rendered back in the very same view, with the parts and points the agent understood
highlighted, so the client sees at a glance whether we read his drawing right before anyone changes the model.

  job run "python /opt/design-engineer/skills/web-viewer/scripts/understood.py <note id> [--parts id,id] [--points 1,2.tip] \
           [--at X,Y,Z[:label]] [--path "X,Y,Z;X,Y,Z;...[:label]"] [--view N] [--out PNG] public/model.glb public/edges.bin"

NOTE        a note id = the chat message id (-> <project>/build/notes/<id>.json, written by the router when the note came) or a path to such a JSON:
            {msg_id, site: "public" | "public/model", url, hash, cycle_t, shapes, camera, views?}
--parts     part ids to highlight (magenta); default: every part the shapes hit. `none` = no highlight.
--points    which drawn points get a numbered marker: N = shape N's key points (arrow tail and tip, line ends, an area's centre, a text's
            anchor, a pen stroke's ends), N.role = that role only (1.tip); default: all of them that hit the model. `none` = none.
--at        an extra marker at an assembly point in mm (as the note's `at`), e.g. a spot the agent means that nobody drew; repeatable.
--path      the agent's sketch of the change it will make (where a new gusset or rib goes, which way), a green 3D polyline through
            assembly points in mm, ≥2 of them; repeatable. Show the change itself when the words leave a direction or a side open.
--view      which of the note's views (1-based; the client may have turned the model between shapes), default the last one.
FILES       big site files named only so the worker job gets them (a job ships files over 1 MB, runner_api.SMALL, only when its
            command names them); the router's `drawing` text gives the command with them filled in.
--out       default <project>/build/understood/<msg id>.png. The picture has the size of the client's view (x2 for a phone).

The site is served the way the stack does (templates/assembly/viewer_stack_server.py) and opened in headless Chromium at the note's
camera, cycle moment and hidden parts; the client's shapes are drawn into the scene in 3D (thin red, from their `path`, so a shape
drawn from another side sits where it was drawn), the frame is copied without any page UI, and the markers go on top. Not reproduced: a firmware machine's sim state at that moment.
"""
import argparse, base64, json, math, re, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "templates" / "assembly"))
from viewer_stack_server import serve_stack  # noqa: E402

KEY_ROLES = ("tail", "tip", "end", "centre", "anchor", "from", "to", "face")   # an open pen stroke's markers are its two ends;
# a pin is not here: it carries its own number on the picture (the client's «1»), a figure-number marker on top would hide it


def markers(shapes, sel):
    """[(label, [x, y] css px)] of the chosen drawn points. sel: None = all key points, else {shape no: None | {roles}}."""
    return [(lab, px) for lab, px, _ in markers3(shapes, sel)]


def markers3(shapes, sel):
    """[(label, px, at | None)]: as markers(), with the assembly point, so a marker lands right in any view."""
    out = []
    for i, s in enumerate(shapes, 1):
        if sel is not None and i not in sel:
            continue
        pts = [p for p in s.get("pts") or [] if not p.get("miss")]
        if s.get("type") == "pen" and "inside" not in s:
            strokes = [p for p in s.get("pts") or [] if p.get("role") == "stroke"]
            pts = [p for p in (strokes[:1] + strokes[-1:] if len(strokes) > 1 else strokes) if not p.get("miss")]
        else:
            pts = [p for p in pts if p.get("role") in KEY_ROLES]
        roles = sel.get(i) if sel else None
        out += [(str(i), p["px"], p.get("at")) for p in pts if roles is None or p.get("role") in roles]
    return out


def parse_points(v):
    if v is None:
        return None
    if v.strip() == "none":
        return {}
    sel = {}
    for tok in filter(None, (t.strip() for t in v.split(","))):
        m = re.fullmatch(r"(\d+)(?:\.(\w+))?", tok)
        if not m:
            sys.exit(f"--points: {tok!r} is not N or N.role (1, 2.tip)")
        n = int(m[1])
        if m[2]:
            if sel.get(n, set()) is not None:
                sel.setdefault(n, set()).add(m[2])
        else:
            sel[n] = None
    return sel


def parse_path(v):
    """'X,Y,Z;X,Y,Z;...[:label]' -> ([[x, y, z], ...], label)."""
    pts, _, label = v.partition(":")
    try:
        out = [[float(x) for x in q.split(",")] for q in pts.split(";") if q.strip()]
        assert len(out) >= 2 and all(len(q) == 3 and all(math.isfinite(x) for x in q) for q in out)
    except (ValueError, AssertionError):
        sys.exit(f"--path {v!r}: want at least two X,Y,Z points in mm separated by ';' (and an optional :label)")
    return out, label.strip()


def project(cam, p):
    """An assembly point (mm) -> css px of the note's view, through its camera matrices (row-major), or None behind the camera."""
    V, P = cam["view"], cam["proj"]
    mul = lambda M, v: [sum(M[r * 4 + c] * v[c] for c in range(4)) for r in range(4)]
    c = mul(P, mul(V, [*p, 1.0]))
    if c[3] <= 0:
        return None
    return [(c[0] / c[3] + 1) / 2 * cam["w"], (1 - c[1] / c[3]) / 2 * cam["h"]]


PAGE_JS = """async ({parts, shapes, marks, sketches, cam}) => {
  const v = window.__viewer, T = v.THREE, M = new Map(), objs = [];
  if (cam && cam.position && cam.target && v.fromAsm && v.setCam) {     // the note's view, whichever the client drew this shape from
    const p = new T.Vector3(...v.fromAsm(cam.position)), t = new T.Vector3(...v.fromAsm(cam.target)), d = p.clone().sub(t), dist = d.length();
    v.setCam({ yaw: Math.atan2(d.x, d.z), pitch: Math.asin(Math.max(-1, Math.min(1, d.y / dist))), dist, target: t.toArray() });
  }
  for (const id of parts) for (const m of v.byPart[id] || []) {
    if (!M.has(m.material)) {
      const h = m.material.clone();
      h.color && h.color.setHex(0xff1fcf); h.emissive && h.emissive.setHex(0x8a0a6c);
      h.transparent = false; h.opacity = 1; h.depthWrite = true; h.map = null; h.metalness = 0; h.roughness = 0.7; h.envMapIntensity = 0.3;
      M.set(m.material, h);
    }
    m.material = M.get(m.material);
  }
  v.invalidate();
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  const H = innerHeight, perPx = (at) => (2 * v.camera.position.distanceTo(at) * Math.tan((v.camera.fov * Math.PI) / 360)) / H;
  const tube = (path, closed, color, px, opacity) => {      // a polyline in 3D, ~px wide on screen, hidden behind parts
    const P = path.map((q) => new T.Vector3(...v.fromAsm(q))); if (P.length < 2) return;
    const c = P.reduce((a, q) => a.add(q), new T.Vector3()).multiplyScalar(1 / P.length), cp = new T.CurvePath();
    for (let i = 0; i < P.length - 1; i++) cp.add(new T.LineCurve3(P[i], P[i + 1]));
    if (closed) cp.add(new T.LineCurve3(P[P.length - 1], P[0]));
    const mat = new T.MeshBasicMaterial({ color, transparent: opacity < 1, opacity, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -4 });
    const m = new T.Mesh(new T.TubeGeometry(cp, Math.min(256, cp.curves.length * 2), (px / 2) * perPx(c), 6, false), mat);
    v.scene.add(m); objs.push(m);
  };
  for (const s of shapes) if (s.path) tube(s.path, s.type === "rect" || s.type === "ellipse" || !!s.closed, 0xe03131, 2, 0.75);
  for (const s of shapes) {                                                   // "this face": the flat face found again from its point (the note keeps no triangles)
    const p0 = s.type === "face" && (s.pts || [])[0], fc = s.face || {};
    const p = p0 && { n: fc.n ?? p0.n, at: fc.centre && p0.plane ? fc.centre : p0.at, normal: fc.normal || p0.normal };
    if (!p || p.n == null || !p.at || !p.normal || !v.meshTris) continue;
    const tris = v.meshTris(p.n) || [], dt = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2], sb = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
    const fits = (t) => {   // a triangle in the note's plane, facing its way (a rebuilt model renumbers triangles)
      const n = [sb(t[1], t[0]), sb(t[2], t[0])], cr = [n[0][1] * n[1][2] - n[0][2] * n[1][1], n[0][2] * n[1][0] - n[0][0] * n[1][2], n[0][0] * n[1][1] - n[0][1] * n[1][0]], l = Math.hypot(...cr);
      return l > 0 && Math.abs(dt(cr, p.normal) / l) >= 0.9998 && Math.abs(dt(p.normal, sb(p.at, t[0]))) <= 0.2;
    };
    let best = Number.isInteger(fc.tri) && tris[fc.tri] && (fc.curved || fits(tris[fc.tri])) ? fc.tri : -1, bd = best >= 0 ? -1 : 1e9;   // the clicked triangle, when it still fits
    if (best < 0) tris.forEach((t, i) => {
      const c = [0, 1, 2].map((k) => (t[0][k] + t[1][k] + t[2][k]) / 3), d = Math.hypot(...sb(c, p.at));
      const n = [sb(t[1], t[0]), sb(t[2], t[0])], cr = [n[0][1] * n[1][2] - n[0][2] * n[1][1], n[0][2] * n[1][0] - n[0][0] * n[1][2], n[0][0] * n[1][1] - n[0][1] * n[1][0]];
      const l = Math.hypot(...cr); if (!l || Math.abs(dt(cr, p.normal) / l) < 0.99 || Math.abs(dt(p.normal, sb(p.at, t[0]))) > 0.2) return;
      if (d < bd) { bd = d; best = i; }
    });
    if (best < 0) continue;
    const { faceRegion } = await import("/_viewer/feedback.js"), f = faceRegion(tris, best); if (!f) continue;
    const geo = new T.BufferGeometry();
    geo.setAttribute("position", new T.Float32BufferAttribute(f.tris.flatMap((i) => tris[i].flatMap((q) => v.fromAsm(q))), 3));
    const m = new T.Mesh(geo, new T.MeshBasicMaterial({ color: 0xe03131, transparent: true, opacity: 0.45, depthWrite: false, side: T.DoubleSide, polygonOffset: true, polygonOffsetFactor: -4, polygonOffsetUnits: -8 }));
    v.scene.add(m); objs.push(m);
  }
  for (const k of sketches) tube(k.path, false, 0x12b886, 4, 1);
  v.invalidate();
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  v.renderNow();
  const g = v.canvas, c = document.createElement("canvas"); c.width = g.width; c.height = g.height;
  const x = c.getContext("2d"); x.drawImage(g, 0, 0);
  objs.forEach((o) => v.scene.remove(o));
  const k = g.width / g.clientWidth, scr = (a) => { const q = new T.Vector3(...v.fromAsm(a)).project(v.camera); return q.z < 1 ? [((q.x + 1) / 2) * innerWidth, ((1 - q.y) / 2) * innerHeight] : null; };
  x.lineCap = x.lineJoin = "round";
  x.strokeStyle = x.fillStyle = "rgba(224,49,49,0.6)"; x.lineWidth = 1.6 * k;    // a shape without a 3D path (an older page): flat, only in its own view
  for (const s of shapes) {
    if (s.path || !s.here) continue;
    const P = (s.px || []).map((p) => [p[0] * k, p[1] * k]); if (!P.length) continue;
    const a = P[0], b = P[P.length - 1];
    x.beginPath();
    if (s.type === "rect") x.strokeRect(Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.abs(b[0] - a[0]), Math.abs(b[1] - a[1]));
    else if (s.type === "ellipse") { x.ellipse((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, Math.abs(b[0] - a[0]) / 2, Math.abs(b[1] - a[1]) / 2, 0, 0, Math.PI * 2); x.stroke(); }
    else if (s.type === "text") { x.font = `600 ${16 * k}px sans-serif`; x.textBaseline = "top"; x.fillText(s.text || "", a[0], a[1]); }
    else {
      x.moveTo(a[0], a[1]); P.forEach((p) => x.lineTo(p[0], p[1]));
      if (s.type === "arrow") { const t = Math.atan2(b[1] - a[1], b[0] - a[0]), h = 12 * k; for (const d of [-0.45, 0.45]) { x.moveTo(b[0], b[1]); x.lineTo(b[0] - h * Math.cos(t + d), b[1] - h * Math.sin(t + d)); } }
      x.stroke();
    }
  }
  for (const s of shapes) if (s.path && s.type === "text") {                  // a text label at its 3D anchor
    const p = scr(s.path[0]); if (!p) continue;
    x.font = `600 ${16 * k}px sans-serif`; x.textAlign = "center"; x.textBaseline = "middle"; x.fillStyle = "rgba(224,49,49,0.9)"; x.fillText(s.text || "", p[0] * k, p[1] * k);
  }
  const dot = (label, p, color) => {
    const X = p[0] * k, Y = p[1] * k, r = 10 * k;
    x.beginPath(); x.arc(X, Y, r, 0, Math.PI * 2); x.fillStyle = color; x.fill(); x.lineWidth = 2.5 * k; x.strokeStyle = "#fff"; x.stroke();
    x.fillStyle = "#fff"; x.font = `700 ${12 * k}px sans-serif`; x.textAlign = "center"; x.textBaseline = "middle"; x.fillText(label, X, Y + 0.5 * k);
  };
  for (const s of shapes) {                                                   // the ready-made marks' own signs: a pin's number, a move's head, a dimension's value
    const P = (s.path || []).map(scr);
    const pin0 = s.type === "pin" && (P[0] || ((s.pts || [])[0] && ((s.pts[0].at && scr(s.pts[0].at)) || s.pts[0].px)));   // no 3D path: its point, or where it was drawn
    if (pin0) dot(String(s.num || ""), pin0, "#e03131");
    if ((s.type === "move" || s.type === "dim") && P[0] && P[1]) {
      const a = P[0], b = P[1], t = Math.atan2(b[1] - a[1], b[0] - a[0]), h = 14;
      x.strokeStyle = "rgba(224,49,49,0.9)"; x.lineWidth = 2.5 * k; x.beginPath();
      if (s.type === "move") for (const d of [-0.45, 0.45]) { x.moveTo(b[0] * k, b[1] * k); x.lineTo((b[0] - h * Math.cos(t + d)) * k, (b[1] - h * Math.sin(t + d)) * k); }
      x.stroke();
      if (s.type === "dim" && s.length != null) {
        const { DICT } = await import("/_viewer/i18n.js"), LL = DICT[document.documentElement.lang] || DICT.ru;   // the project's language, as the page shows it
        const txt = s.length.toFixed(1).replace(".", LL.decimal) + " " + LL.mm, mx = ((a[0] + b[0]) / 2) * k, my = ((a[1] + b[1]) / 2) * k;
        x.font = `700 ${13 * k}px sans-serif`; x.textAlign = "center"; x.textBaseline = "middle";
        const w = x.measureText(txt).width + 10 * k; x.fillStyle = "#fffe"; x.fillRect(mx - w / 2, my - 10 * k, w, 20 * k);
        x.strokeStyle = "#e03131"; x.lineWidth = 1.5 * k; x.strokeRect(mx - w / 2, my - 10 * k, w, 20 * k); x.fillStyle = "#e03131"; x.fillText(txt, mx, my);
      }
    }
  }
  for (const [label, px, at] of marks) { const p = at ? scr(at) : px; if (p) dot(label, p, "#12b886"); }   // what we understood: numbered markers
  for (const kq of sketches) { const p = scr(kq.path[kq.path.length - 1]); if (p && kq.label) dot(kq.label, p, "#0b7285"); }
  return c.toDataURL("image/png");
}"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("note")
    ap.add_argument("files", nargs="*", help="big site files, named only so the worker job ships them")
    ap.add_argument("--project", default=".")
    ap.add_argument("--parts")
    ap.add_argument("--points")
    ap.add_argument("--at", action="append", default=[])
    ap.add_argument("--path", action="append", default=[])
    ap.add_argument("--view", type=int)
    ap.add_argument("--out")
    ap.add_argument("--gpu", action="store_true", help="use the machine's GPU (default: SwiftShader, the workers have no GPU)")
    ap.add_argument("--swiftshader", action="store_true", help=argparse.SUPPRESS)   # the default; kept for symmetry with the viewer tests
    argv, i = sys.argv[1:], 0
    while i < len(argv) - 1:      # coordinates start with "-" more often than not: the word after --at / --path is always its value
        if argv[i] in ("--at", "--path"):
            argv[i:i + 2] = [f"{argv[i]}={argv[i + 1]}"]
        i += 1
    a = ap.parse_args(argv)

    proj = Path(a.project).resolve()
    nf = proj / "build" / "notes" / f"{a.note}.json" if a.note.isdigit() else Path(a.note)
    if not nf.is_file():
        sys.exit(f"no note {nf}: the router writes build/notes/<msg id>.json for a note drawn on the model page")
    note = json.loads(nf.read_text(encoding="utf-8"))
    shapes, views = note.get("shapes") or [], [c for c in note.get("views") or [] if isinstance(c, dict)]
    if a.view is not None and not 1 <= a.view <= max(1, len(views)):
        sys.exit(f"--view {a.view}: the note has {max(1, len(views))} view(s)")
    vi = (a.view or len(views) or 1) - 1
    cam = views[vi] if views else note.get("camera") or {}
    sketches = [dict(zip(("path", "label"), parse_path(v))) for v in a.path]
    if sketches and not cam.get("view"):
        sys.exit("--path needs the note's camera (a note from an older page has none): mark the spot with --at instead")
    site = (proj / (note.get("site") or "public")).resolve()
    missing = [f for f in ("config.json", "parts.json", "model.glb") if not (site / f).is_file()]
    if missing:
        sys.exit(f"{site} lacks {', '.join(missing)}: in a worker job name the big files in the command "
                 f"(… understood.py {a.note} {note.get('site') or 'public'}/model.glb …)")
    hit = []
    for s in shapes:
        for k in [p.get("part") for p in s.get("pts") or []] + list((s.get("inside") or {}).keys()):
            if k and k not in hit:
                hit.append(k)
    parts = [] if a.parts == "none" else [x.strip() for x in a.parts.split(",") if x.strip()] if a.parts else hit
    marks = [list(m) for m in markers3(shapes, parse_points(a.points))]
    for i, s in enumerate(a.at, 1):
        xyz, _, label = s.partition(":")
        try:
            p = [float(v) for v in xyz.split(",")]
            assert len(p) == 3
        except (ValueError, AssertionError):
            sys.exit(f"--at {s!r}: want X,Y,Z[:label] in mm")
        if not cam.get("view"):
            sys.exit("--at needs the note's camera (a note from an older page has none)")
        if project(cam, p) is None:
            print(f"--at {s}: behind the camera, not drawn", file=sys.stderr)
        else:
            marks.append([label or f"+{i}", None, p])

    w, h = int(cam.get("w") or 1280), int(cam.get("h") or 800)
    dsf = 2 if max(w, h) < 1000 else 1
    hash_ = note.get("hash") if isinstance(note.get("hash"), str) and note["hash"].startswith("#") else ""
    hash_ = re.sub(r"&?(sel|seln)=[^&]*", "", hash_).replace("#&", "#")   # the page's own selection is not what we understood
    out = Path(a.out) if a.out else proj / "build" / "understood" / f"{note.get('msg_id', nf.stem)}.png"
    out.parent.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "note-view").symlink_to(site, target_is_directory=True)
        srv, base = serve_stack(Path(td))
        try:
            with sync_playwright() as pw:
                args = ["--enable-gpu", "--ignore-gpu-blocklist"] if a.gpu else ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
                br = pw.chromium.launch(args=args)
                page = br.new_page(viewport={"width": w, "height": h}, device_scale_factor=dsf)
                page.goto(f"{base}/note-view/{hash_}")
                page.wait_for_function("window.__viewer && (window.__viewer.ready || window.__viewer.error)", timeout=300000)
                err = page.evaluate("window.__viewer.error || null")
                if err:
                    sys.exit(f"the viewer failed to load the site: {err}")
                if isinstance(note.get("cycle_t"), (int, float)) and page.evaluate("!!(window.__viewer.config || {}).cycle"):
                    page.wait_for_function("!!window.__viewer.cycle", timeout=60000)
                    page.evaluate("(t) => window.__viewer.cycle.setTime(t)", note["cycle_t"])
                page.wait_for_function("!window.__viewer.state().flying", timeout=60000)
                if cam.get("view") and not cam.get("position"):     # an older note: the hash restores the camera; a different window size would not
                    now = page.evaluate("window.__viewer.camInfo ? window.__viewer.camInfo() : null")
                    if now and now.get("view") and max(abs(x - y) for x, y in zip(now["view"], cam["view"])) > max(1.0, 1e-3 * max(map(abs, cam["view"]))):
                        print("warning: the reproduced camera differs from the note's (an older note: no camera position to set it from)", file=sys.stderr)
                # a shape without a 3D path is drawn flat, and only in the view it was drawn in (its px belong to that view)
                flat = [dict(s, here=(s.get("view", len(views) - 1 if views else 0) == vi)) for s in shapes]
                url = page.evaluate(PAGE_JS, {"parts": parts, "shapes": flat, "marks": marks, "sketches": sketches, "cam": cam})
                br.close()
        finally:
            srv.shutdown()
    out.write_bytes(base64.b64decode(url.split(",", 1)[1]))
    print(f"wrote {out} ({w}x{h}{' x2' if dsf == 2 else ''}, view {vi + 1} of {max(1, len(views))}); parts: {', '.join(parts) or '—'}; "
          f"markers: {len(marks)}; sketches: {len(sketches)}")


if __name__ == "__main__":
    main()
