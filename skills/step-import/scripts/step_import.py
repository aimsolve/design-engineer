#!/usr/bin/env python3
"""STEP assembly -> instanced GLB (metres) + parts.json catalogue + per-part STEP/STL.

env -u VIRTUAL_ENV uv run --isolated --no-project --with cadquery-ocp --with trimesh --with numpy \
    python -u step_import.py in.step --out build/ [--deflection 0.5] [--angle 0.5] [--no-parts]
    python3 step_import.py --reclassify build/      # re-derive kind + material in parts.json, no STEP needed
"""
import argparse, json, os, re, struct, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import params as PRM   # type + params extractor (RULES table lives there)
import import_i18n as I   # the words of part names in other languages
try:
    import numpy as np
except ImportError:
    np = None
try:   # OCP is only needed to read a STEP; --reclassify runs without it
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs, STEPControl_Reader
    from OCP.TDocStd import TDocStd_Document
    from OCP.TCollection import TCollection_ExtendedString, TCollection_AsciiString
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ShapeTool, XCAFDoc_ColorType
    from OCP.Quantity import Quantity_Color, Quantity_TOC_sRGB
    from OCP.OCP.collections import Sequence_TDF_Label as LabelSeq
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.IMeshTools import IMeshTools_Parameters
    from OCP.Message import Message_ProgressRange
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDF import TDF_Label, TDF_Tool
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopoDS import TopoDS
    from OCP.TopLoc import TopLoc_Location
    from OCP.BRep import BRep_Tool
    from OCP.BRepLib import BRepLib_ToolTriangulatedShape
    from OCP.GProp import GProp_GProps
    from OCP.BRepGProp import BRepGProp
    from OCP.StlAPI import StlAPI_Writer  # noqa: F401  (STL is written from our own triangles)
except ImportError:
    STEPCAFControl_Reader = None

MM = 1e-3
# A drawing number of four or three groups: ABC-1 01.02.03.456 and ABC 02.01.121; the digit guards keep a date
# (20.10.2026) and a longer run (02.01.1210) out. A match makes the part `made` and `steel` before the STANDARD /
# MATERIAL tables are read, so a false positive also changes its id and module (tests/test_drawing_number.py, class LookAlikes).
# Keep identical to DRAWING in step_struct.py: tests/test_step_struct_schemas.py asserts the two are the same, by
# matching this line as a single-line `re.compile(r"…")` - keep it on one line or that guard stops working.
DRAWING = re.compile(r"(?<![\d.])\d{2}\.\d{2}(?:\.\d{2})?\.\d{3}(?!\d)(?!\.\d)")
_X = "[x" + I.like("x") + "]"                     # an `x` of a size, typed in any script
STANDARD = re.compile(
    r"(?<![^\W\d_])(DIN|ISO|GOST|nut|screw|bolt|washer|bearing|rivet|circlip|o-ring|stud|dowel|brugola|"
    + I.words("standard") + "|" + I.words("fastener") + "|" + I.words("bearing") + r")(?![^\W\d_])"   # fasteners
    r"|\d+\s?" + _X + r"\s?\d+\s?" + _X + r"\s?\d+\b.*\d{3,5}"           # bearing 10x26x8_6000
    r"|\b\d{4,5}[- _]?(2RS|ZZ|RS)\b|(2RS|ZZ)\b|\bSS \d{4}",           # bearing codes
    re.I)


class quiet:
    def __enter__(self):
        sys.stdout.flush(); self.o = os.dup(1); os.dup2(os.open(os.devnull, os.O_WRONLY), 1)
    def __exit__(self, *a):
        os.dup2(self.o, 1); os.close(self.o)


def entry(l):
    s = TCollection_AsciiString(); TDF_Tool.Entry_s(l, s); return s.ToCString()


def name(l):
    a = TDataStd_Name()
    return a.Get().ToExtString().strip() if l.FindAttribute(TDataStd_Name.GetID_s(), a) else ""


def components(l):
    s = LabelSeq()
    if XCAFDoc_ShapeTool.GetComponents_s(l, s, False):
        return [s.Value(i) for i in range(1, s.Length() + 1)]
    return []


def label_color(ct, *labels):
    """XCAF surface/generic colour of the first label that has one -> '#rrggbb' (sRGB, as written in the STEP) or None."""
    c = Quantity_Color()
    for l in labels:
        for t in (XCAFDoc_ColorType.XCAFDoc_ColorSurf, XCAFDoc_ColorType.XCAFDoc_ColorGen):
            if ct.GetColor_s(l, t, c):
                return "#%02x%02x%02x" % tuple(round(min(1.0, max(0.0, x)) * 255) for x in c.Values(Quantity_TOC_sRGB))
    return None


def referred(l):
    r = TDF_Label()
    return r if XCAFDoc_ShapeTool.GetReferredShape_s(l, r) else l


def trsf_matrix(loc):
    """TopLoc_Location -> column-major glTF 4x4, translation in metres."""
    t = loc.Transformation(); s = t.ScaleFactor()
    m = np.eye(4)
    for i in range(3):
        for j in range(3): m[i, j] = t.Value(i + 1, j + 1)
        m[i, 3] = t.Value(i + 1, 4) * MM
    return m


def extract(shape):
    """Triangles of a meshed shape in mm: (verts Nx3, tris Mx3, normals Nx3 unit), outward winding.
    Normals come from the B-rep surface (BRepLib_ToolTriangulatedShape.ComputeNormals), flipped for reversed faces.
    Vertices are welded only when position AND (int8-quantised) normal match, so creases stay split across faces
    while smooth surfaces (cylinder seams, coplanar neighbours) still share vertices."""
    V, T, N, off = [], [], [], 0
    ex = TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        f = TopoDS.Face(ex.Current()); ex.Next()
        loc = TopLoc_Location(); tri = BRep_Tool.Triangulation_s(f, loc)
        if tri is None: continue
        BRepLib_ToolTriangulatedShape.ComputeNormals_s(f, tri)
        tr = loc.Transformation(); ident = loc.IsIdentity()
        n = tri.NbNodes()
        pts = np.empty((n, 3)); nrm = np.empty((n, 3))
        rev = f.Orientation() == TopAbs_REVERSED
        for i in range(n):
            p = tri.Node(i + 1)
            d = tri.Normal(i + 1)
            if not ident: p = p.Transformed(tr); d = d.Transformed(tr)
            pts[i] = (p.X(), p.Y(), p.Z()); nrm[i] = (d.X(), d.Y(), d.Z())
        if rev: nrm = -nrm     # ComputeNormals gives the surface normal; the face is reversed w.r.t. its surface
        tt = np.empty((tri.NbTriangles(), 3), dtype=np.int64)
        for i in range(tri.NbTriangles()):
            a, b, c = tri.Triangle(i + 1).Get()
            tt[i] = (a - 1, c - 1, b - 1) if rev else (a - 1, b - 1, c - 1)
        # fallback for degenerate normals (poles/apex): geometric normal of the face's triangles
        bad = ~np.isfinite(nrm).all(1) | (np.linalg.norm(nrm, axis=1) < 0.5)
        if bad.any():
            g = np.cross(pts[tt[:, 1]] - pts[tt[:, 0]], pts[tt[:, 2]] - pts[tt[:, 0]])
            acc = np.zeros((n, 3)); 
            for k in range(3): np.add.at(acc, tt[:, k], g)
            nrm[bad] = acc[bad]
        V.append(pts); N.append(nrm); T.append(tt + off); off += n
    if not V: return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64), np.zeros((0, 3))
    V, T, N = np.vstack(V), np.vstack(T), np.vstack(N)
    N = N / np.where((l := np.linalg.norm(N, axis=1, keepdims=True)) == 0, 1, l)
    qn = np.round(N * 127).astype(np.int64)
    key = np.hstack([np.round(V, 4) * 1e4, qn]).round().astype(np.int64)
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    return V[first], inv.reshape(-1)[T], qn[first].astype(np.int8)


def load_groups(path):
    g = json.load(open(path, encoding="utf-8"))
    if isinstance(g, list): g = {x["name"]: x.get("group") or x.get("node") for x in g}
    return {k.split("/", 1)[-1]: v for k, v in g.items()}   # 'arms/tabletop' -> 'tabletop'


def apply_motion_groups(parts, mg):
    """Second map for code-CAD machines: part name -> motion group (the group that moves as one in the cycle, e.g. bom.json `group`). Stored as parts[key]['motion_group']."""
    n = 0
    for p in parts.values():
        g = mg.get(p["name"])
        if g is not None: p["motion_group"] = g; n += 1
    print(f"motion groups: {n} of {len(parts)} parts ({', '.join(sorted(set(mg.values())))})", flush=True)


def apply_groups(root, tree, parts, groups):
    """Wrap the root's direct children into one assembly node per group (first-seen order). Unmapped parts stay at the root."""
    gnodes, gtrees, rest = {}, {}, []
    for c in root["children"]:
        nm, g = c["name"], None
        if "key" in c: g = groups.get(nm) or groups.get(parts[c["key"]]["name"])
        if g is None: rest.append(c); continue
        if g not in gnodes:
            gnodes[g] = {"name": g, "matrix": np.eye(4), "children": []}; gtrees[g] = {"name": g, "drawing": None, "children": []}
        gnodes[g]["children"].append(c); parts[c["key"]]["module"] = g
    root["children"] = list(gnodes.values()) + rest
    tree["children"] = list(gtrees.values())
    n = sum(len(x["children"]) for x in gnodes.values())
    print(f"groups: {len(gnodes)} ({', '.join(f'{k} {len(v['children'])}' for k, v in gnodes.items())}), {len(rest)} ungrouped at the root", flush=True)
    return root, tree


def add_mesh_parts(items, root, tree, parts, order, meshes, used):
    """--mesh-parts: append mesh-only parts as instances (identity matrix, coordinates already in the STEP frame) under their group node.
    Flat shading: every triangle gets its own three vertices and face normal, so box edges stay crisp."""
    gnode = {c["name"]: c for c in root["children"] if "key" not in c}
    gtree = {c["name"]: c for c in tree["children"]}
    for n, it in enumerate(items):
        key = f"mesh:{n}"   # geometry is pre-placed, so every item is its own part (same-name units get id suffixes _2, _3)
        v = np.asarray(it["pos"], float).reshape(-1, 3); t = np.asarray(it["idx"], np.int64).reshape(-1, 3)
        tri = v[t].reshape(-1, 3); fv = np.arange(len(tri)).reshape(-1, 3)
        fn = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
        area2 = np.linalg.norm(fn, axis=1, keepdims=True); fn = fn / np.where(area2 == 0, 1, area2)
        qn = np.round(np.repeat(fn, 3, axis=0) * 127).astype(np.int8)
        vol = float(np.einsum("ij,ij->i", v[t[:, 0]], np.cross(v[t[:, 1]], v[t[:, 2]])).sum() / 6)
        base = slug(it["name"]); pid = base; i = 2
        while pid in used: pid = f"{base}_{i}"; i += 1
        used.add(pid)
        parts[key] = {"id": pid, "name": it["name"], "drawing": None, "module": it.get("group"), "instances": 0, "color": it.get("color"),
                      "volume_mm3": round(abs(vol), 3), "triangles": int(len(t)), "mesh_index": len(meshes), "v": tri, "t": fv,
                      "bbox_mm": [np.round(v.min(0), 3).tolist(), np.round(v.max(0), 3).tolist()] if len(v) else None}
        meshes.append(((tri * MM).astype(np.float32), fv.astype(np.uint16 if len(tri) < 65536 else np.uint32), qn))
        order.append(key)
        parts[key]["instances"] += 1
        g = it.get("group") or "mesh_parts"
        if g not in gnode:
            gnode[g] = {"name": g, "matrix": np.eye(4), "children": []}; root["children"].append(gnode[g])
            gtree[g] = {"name": g, "drawing": None, "children": []}; tree["children"].append(gtree[g])
        gnode[g]["children"].append({"name": it["name"], "matrix": np.eye(4), "children": [], "key": key})
    print(f"mesh parts: {len(items)} instances", flush=True)


def slug(s):
    s = re.sub(r"[^\w.\-]+", "_", s.strip(), flags=re.U).strip("_")
    return s[:80] or "part"


def classify(nm, drawing):
    if drawing: return "made"
    return "standard" if STANDARD.search(nm) else "purchased"


# Material classes for the viewer (colour) - ordered, first match wins, matched against the lower-cased part name.
# Made parts (they have a drawing number) are only ever printed or steel. Edit here, then `--reclassify <out>`.
MATERIAL_RULES = [
    ("glass",       r"vetro|glass|" + I.words("glass")),
    ("rubber",      r"\bbelt|" + I.words("rubber")),
    ("bearing",     r"bearing|kfl0|\d+" + _X + r"\d+" + _X + r"\d+|(?<!din )\b(608|6\d{3}|618\d\d|619\d\d)\b|2rs|\bzz\b|ss 6\d{3}|"
                    + I.words("bearing")),
    ("steel",       r"din rail|" + I.words("din_rail")),   # before fastener: 'din'
    ("fastener",    r"\b(din|iso)\b|nut|screw|bolt|washer|rivet|stud|brugola|" + I.words("fastener")),
    ("electronics", r"esp ?32|arduino|pcb|driver|dm\d{3}|tb6600|mosfet|irf\d|nes-|lrs-|mean ?well|connector|terminal|dip.?switch|"
                    r"converter|dc-dc|encoder|sensor|\bpse\b|\bled\b|plug \d|pg\d\d|\bsink\b|\bcover\b|shield|adapter|"
                    + I.words("electronics")),
    ("motor",       r"nema|stepper|motor|" + I.words("motor")),
    ("pneumatic",   r"\bmal\d|4v210|\btm0000|serie tm|tn16x10|manometr|generator_cv|\bcv\d+hsm|cylinder|" + I.words("pneumatic")),
]
MATERIAL_RULES = [(m, re.compile(p, re.I)) for m, p in MATERIAL_RULES]
MATERIALS = [m for m, _ in MATERIAL_RULES] + ["printed", "other"]


def material_of(nm, drawing):
    n = nm or ""
    if re.search(I.words("printed"), n, re.I): return "printed"
    if drawing: return "steel"            # own drawing = sheet metal / machined by default
    for m, rx in MATERIAL_RULES:
        if rx.search(n): return m
    return "other"


def reclassify(out, geometry=True):
    """Rewrite kind + material in an existing parts.json (seconds, no STEP needed)."""
    import collections, random
    path = os.path.join(os.path.abspath(os.path.expanduser(out)), "parts.json")
    d = json.load(open(path, encoding="utf-8"))
    for p in d["parts"]:
        p["kind"] = classify(p["name"], p["drawing"]); p["material"] = material_of(p["name"], p["drawing"])
    PRM.apply_all(d["parts"], os.path.dirname(path), geometry=geometry)
    json.dump(d, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    PRM.report(d["parts"])
    cnt = collections.Counter(); inst = collections.Counter()
    for p in d["parts"]: cnt[p["material"]] += 1; inst[p["material"]] += p["instances"]
    print("material  parts  instances")
    for m, c in cnt.most_common(): print(f"  {m:12s}{c:5d}{inst[m]:8d}")
    rnd = random.Random(1); pool = [p for p in d["parts"] if p["kind"] != "made" or p["material"] != "steel"]
    print("samples:")
    for p in rnd.sample(pool, min(15, len(pool))): print(f"  {p['material']:12s}{p['kind']:10s}{p['name']}")
    print("other:", [p["name"] for p in d["parts"] if p["material"] == "other"])


def write_glb(path, meshes, root, extras, qpos=False, mesh_names=None):
    """meshes: list of (verts_m float32, tris uint32, normals int8 Nx3). root: node tree dict. One glTF mesh per entry."""
    blob = bytearray(); views, accs, gmeshes = [], [], []
    def add(data, target, ctype, count, typ, mn=None, mx=None):
        while len(blob) % 4: blob.append(0)
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": data.nbytes, "target": target})
        blob.extend(data.tobytes())
        a = {"bufferView": len(views) - 1, "componentType": ctype, "count": count, "type": typ}
        if mn is not None: a["min"], a["max"] = mn, mx
        accs.append(a); return len(accs) - 1
    dq = []   # per mesh: (centre, half-extent) - positions are int16 normalised, the instance node carries T(c)*S(h)
    for v, t, qn in meshes:
        mn, mx = v.min(0).astype(np.float64), v.max(0).astype(np.float64)
        c = (mn + mx) / 2; h = np.maximum((mx - mn) / 2, 1e-6); dq.append((c, h))
        if qpos:   # int16 normalised positions, 8-byte stride (trimesh does NOT dequantise these)
            p4 = np.zeros((len(v), 4), dtype=np.int16); p4[:, :3] = np.round((v - c) / h * 32767)
            pa = add(p4, 34962, 5122, len(v), "VEC3", p4[:, :3].min(0).tolist(), p4[:, :3].max(0).tolist())
            accs[pa]["normalized"] = True; views[-1]["byteStride"] = 8
        else:
            pa = add(v, 34962, 5126, len(v), "VEC3", v.min(0).tolist(), v.max(0).tolist())
        n4 = np.zeros((len(v), 4), dtype=np.int8); n4[:, :3] = qn      # normalized BYTE VEC3, 4-byte stride
        na = add(n4, 34962, 5120, len(v), "VEC3"); accs[na]["normalized"] = True; views[-1]["byteStride"] = 4
        ia = add(t.reshape(-1), 34963, 5123 if len(v) < 65536 else 5125, t.size, "SCALAR")
        gm = {"primitives": [{"attributes": {"POSITION": pa, "NORMAL": na}, "indices": ia, "material": 0}]}
        if mesh_names: gm["name"] = mesh_names[len(gmeshes)]
        gmeshes.append(gm)
    nodes = []
    def emit(n):
        d = {"name": n["name"]}
        m = n["matrix"] if n.get("matrix") is not None else np.eye(4)
        if "mesh" in n and qpos:
            c, h = dq[n["mesh"]]; D = np.diag([*h, 1.0]); D[:3, 3] = c; m = m @ D
        if not np.allclose(m, np.eye(4)): d["matrix"] = m.T.reshape(-1).tolist()
        if "mesh" in n:
            d["mesh"] = n["mesh"]; d["extras"] = {"part": n["part"]}
            if n.get("motion_group"): d["extras"]["motion_group"] = n["motion_group"]   # this instance's own group (tree mode)
        idx = len(nodes); nodes.append(d)
        if n["children"]: d["children"] = [emit(c) for c in n["children"]]
        return idx
    emit(root)
    g = {"asset": {"version": "2.0", "generator": "step_import.py", "extras": extras},
         "extensionsUsed": ["KHR_mesh_quantization"], "extensionsRequired": ["KHR_mesh_quantization"],
         "scene": 0, "scenes": [{"nodes": [0]}], "nodes": nodes, "meshes": gmeshes, "accessors": accs,
         "bufferViews": views, "buffers": [{"byteLength": len(blob)}],
         "materials": [{"name": "default", "pbrMetallicRoughness": {"baseColorFactor": [0.62, 0.64, 0.67, 1],
                        "metallicFactor": 0.2, "roughnessFactor": 0.6}, "doubleSided": True}]}
    js = json.dumps(g, separators=(",", ":")).encode(); js += b" " * (-len(js) % 4)
    while len(blob) % 4: blob.append(0)
    with open(path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(blob)))
        f.write(struct.pack("<II", len(js), 0x4E4F534A)); f.write(js)
        f.write(struct.pack("<II", len(blob), 0x004E4942)); f.write(blob)


def write_stl(path, v, t):
    tri = v[t]  # Mx3x3
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    l = np.linalg.norm(n, axis=1, keepdims=True); n = n / np.where(l == 0, 1, l)
    rec = np.zeros(len(t), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec["n"], rec["v"] = n, tri
    with open(path, "wb") as f:
        f.write(b"step_import".ljust(80, b"\0")); f.write(struct.pack("<I", len(t))); f.write(rec.tobytes())


def mesh_step_file(path, deflection=0.5, angle=0.5):
    """Per-part meshing for the cached model: ONE part STEP (a single placed shape, world coordinates) -> dict with
    v (Nx3 mm), t (Mx3), qn (int8 normals), volume_mm3, bbox_mm. No XCAF tree, no assembly: the caller caches the result by the STEP's sha."""
    r = STEPControl_Reader()
    with quiet():
        assert r.ReadFile(str(path)) == IFSelect_RetDone, f"cannot read STEP {path}"
        r.TransferRoots()
    sh = r.OneShape()
    prm = IMeshTools_Parameters(); prm.Deflection = deflection; prm.Angle = angle; prm.Relative = False; prm.InParallel = True
    BRepMesh_IncrementalMesh(sh, prm)
    v, t, qn = extract(sh)
    g = GProp_GProps(); BRepGProp.VolumeProperties_s(sh, g)
    return {"v": v, "t": t, "qn": qn, "volume_mm3": round(g.Mass(), 3),
            "bbox_mm": [np.round(v.min(0), 3).tolist(), np.round(v.max(0), 3).tolist()] if len(v) else None}


def write_model_dir(out, items, title, deflection=0.5, angle=0.5, quantize=True, source="model"):
    """Assemble a viewer input dir (model.glb + parts.json + parts/<id>.step|stl) from per-part meshes, the same layout main() writes.
    items: [{id, name, node, color, v, t, qn, volume_mm3, bbox_mm, step_src, stl_src, mesh_key?, matrix?, motion_group?, card?}] (card:
    catalogue keys over the guessed ones, its name the catalogue's, the glb node keeps `name`), one group node per `node` in
    first-seen order. v is in world coordinates, or with `matrix` (4x4 row-major, mm) in the part's own ones, the matrix becoming the
    instance node's. Items with the same `mesh_key` share one glb mesh (the first item's v/t/qn): a part placed N times is one mesh,
    N nodes. bbox_mm is the world one. step_src / stl_src are existing files, hard-linked into parts/."""
    os.makedirs(os.path.join(out, "parts"), exist_ok=True)
    if any(it.get("path") is not None for it in items):
        return _write_tree_model_dir(out, items, title, deflection, angle, quantize, source)
    meshes, names, cat, mesh_ix = [], [], [], {}
    root = {"name": title, "matrix": np.eye(4), "children": []}
    tree = {"name": title, "drawing": None, "children": []}
    gnode = {}
    for it in items:
        g = it.get("node") or "parts"
        if g not in gnode:
            gnode[g] = {"name": g, "matrix": np.eye(4), "children": []}; root["children"].append(gnode[g])
            tree["children"].append({"name": g, "drawing": None, "children": []})
        files = {}
        for ext, src in (("step", it.get("step_src")), ("stl", it.get("stl_src"))):
            if not src: continue
            dst = os.path.join(out, "parts", f"{it['id']}.{ext}")
            if os.path.lexists(dst): os.remove(dst)
            try: os.link(src, dst)
            except OSError:
                import shutil; shutil.copyfile(src, dst)
            files[ext] = f"parts/{it['id']}.{ext}"
        node = {"name": it["name"], "matrix": np.eye(4), "children": []}
        if it.get("matrix") is not None:
            node["matrix"] = np.array(it["matrix"], dtype=float).reshape(4, 4).copy(); node["matrix"][:3, 3] *= MM
        if len(it["v"]):
            mk = it.get("mesh_key") or it["id"]
            if mk not in mesh_ix:
                mesh_ix[mk] = len(meshes)
                meshes.append(((it["v"] * MM).astype(np.float32), it["t"].astype(np.uint16 if len(it["v"]) < 65536 else np.uint32), it["qn"]))
                names.append(mk)
            m0 = meshes[mesh_ix[mk]]
            if len(m0[0]) != len(it["v"]) or not np.array_equal(m0[1], it["t"]):
                raise ValueError(f"mesh_key {mk}: {it['id']} is not the same mesh as the first item with that key")
            node["mesh"] = mesh_ix[mk]; node["part"] = it["id"]
        gnode[g]["children"].append(node)
        cat.append({"id": it["id"], "name": it["name"], "drawing": None, "kind": classify(it["name"], None), "material": material_of(it["name"], None),
                    "module": g, "color": it.get("color"), "instances": 1, "bbox_mm": it["bbox_mm"], "volume_mm3": it["volume_mm3"],
                    "triangles": int(len(it["t"])), "files": files, **({"part_id": it["part_id"]} if it.get("part_id") else {}),
                    **({"motion_group": it["motion_group"]} if it.get("motion_group") else {})})
    tess = {"deflection_mm": deflection, "angle_rad": angle}
    bb = [c["bbox_mm"] for c in cat if c["bbox_mm"]]
    bbox = [np.min([b[0] for b in bb], 0).round(2).tolist(), np.max([b[1] for b in bb], 0).round(2).tolist()] if bb else None
    write_glb(os.path.join(out, "model.glb"), meshes, root, {"units": "m", "source_units": "mm", "up": "Z", "source": source, "tessellation": tess},
              qpos=quantize, mesh_names=names)
    PRM.apply_all(cat, out, geometry=True)
    for c, it in zip(cat, items):                  # the model's own card (name, material, kind, density, thickness) over the guesses
        c.update(it.get("card") or {})
    asm = {"root": title, "nodes": 1 + len(gnode) + len(items), "instances": len(items), "distinct_parts": len(cat),
           "triangles": int(sum(c["triangles"] for c in cat)), "bbox_mm": bbox, "source": source, "tessellation": tess, "tree": tree}
    with open(os.path.join(out, "parts.json"), "w") as f:
        json.dump({"assembly": asm, "parts": cat}, f, ensure_ascii=False, indent=1)


def _write_tree_model_dir(out, items, title, deflection, angle, quantize, source):
    """write_model_dir for items that carry `path` [((key), title), ...] (a model whose nodes have titles, e.g. one converted
    from a client's CAD): nested group nodes, one per copy of a node, named by the title; leaves named by the part's name;
    one catalogue entry per part id (as an import of the client's STEP has it), the GLB nodes of its placements share it.
    A single outermost group becomes the root, so the paths read like the client's CAD."""
    meshes, names, mesh_ix, cat = [], [], {}, {}
    moving = any(it.get("motion_group") for it in items)   # copies of one part may sit in different groups: each GLB node says its own
    tops = {(it.get("path") or [(None, None)])[0][0] for it in items}
    single = len(tops) == 1 and None not in tops
    root = {"name": title, "matrix": np.eye(4), "children": []}
    tree = {"name": title, "drawing": None, "children": []}
    groups = {(): (root, tree)}
    for it in items:
        path = it.get("path") or []
        if single:
            if not groups.get("named"):
                root["name"] = tree["name"] = path[0][1]
                d = DRAWING.search(path[0][1])
                tree["drawing"] = d.group(0) if d else None
                groups["named"] = True
            path = path[1:]
        parent, ptree, key = root, tree, ()
        for k, (gk, gt) in enumerate(path):
            key = key + (gk,)
            if key not in groups:
                d = DRAWING.search(gt or "")
                g = {"name": gt, "matrix": np.eye(4), "children": []}
                gtr = {"name": gt, "drawing": d.group(0) if d else None, "children": []}
                parent["children"].append(g)
                ptree["children"].append(gtr)
                groups[key] = (g, gtr)
            parent, ptree = groups[key]
        files = {}
        pid = it["id"]
        for ext, src in (("step", it.get("step_src")), ("stl", it.get("stl_src"))):
            if not src:
                continue
            dst = os.path.join(out, "parts", f"{pid}.{ext}")
            if not os.path.lexists(dst):
                try:
                    os.link(src, dst)
                except OSError:
                    import shutil
                    shutil.copyfile(src, dst)
            files[ext] = f"parts/{pid}.{ext}"
        node = {"name": it["name"], "matrix": np.eye(4), "children": []}
        if it.get("matrix") is not None:
            node["matrix"] = np.array(it["matrix"], dtype=float).reshape(4, 4).copy(); node["matrix"][:3, 3] *= MM
        if len(it["v"]):
            mk = it.get("mesh_key") or pid
            if mk not in mesh_ix:
                mesh_ix[mk] = len(meshes)
                meshes.append(((it["v"] * MM).astype(np.float32), it["t"].astype(np.uint16 if len(it["v"]) < 65536 else np.uint32), it["qn"]))
                names.append(mk)
            node["mesh"] = mesh_ix[mk]; node["part"] = pid
            if moving:
                node["motion_group"] = it.get("motion_group") or "static"
        parent["children"].append(node)
        if pid in cat:
            cat[pid]["instances"] += 1
            if it.get("motion_group") and not cat[pid].get("motion_group"):
                cat[pid]["motion_group"] = it["motion_group"]
            continue
        d = DRAWING.search(it["name"])
        drawing = d.group(0) if d else None
        cat[pid] = {"id": pid, "name": it["name"], "drawing": drawing, "kind": classify(it["name"], drawing),
                    "material": material_of(it["name"], drawing), "module": it.get("node") or "parts", "color": it.get("color"),
                    "instances": 1, "bbox_mm": it["bbox_mm"], "volume_mm3": it["volume_mm3"], "triangles": int(len(it["t"])),
                    "files": files, **({"motion_group": it["motion_group"]} if it.get("motion_group") else {})}
        cat[pid]["_card"] = it.get("card")
    cat = list(cat.values())
    tess = {"deflection_mm": deflection, "angle_rad": angle}
    write_glb(os.path.join(out, "model.glb"), meshes, root, {"units": "m", "source_units": "mm", "up": "Z", "source": source, "tessellation": tess},
              qpos=quantize, mesh_names=names)
    cards = [c.pop("_card") for c in cat]
    PRM.apply_all(cat, out, geometry=True)
    for c, card in zip(cat, cards):
        c.update(card or {})
    lo = np.array([np.inf] * 3); hi = -lo

    def walk(n, M):
        nonlocal lo, hi
        M = M @ n["matrix"]
        if "mesh" in n:
            v = meshes[n["mesh"]][0].astype(float)
            w = v @ M[:3, :3].T + M[:3, 3]
            lo = np.minimum(lo, w.min(0)); hi = np.maximum(hi, w.max(0))
        for c in n["children"]:
            walk(c, M)
    walk(root, np.eye(4))
    bbox = [(lo / MM).round(2).tolist(), (hi / MM).round(2).tolist()] if np.isfinite(lo).all() else None
    n_groups = len([k for k in groups if isinstance(k, tuple) and k])
    asm = {"root": root["name"], "nodes": 1 + n_groups + len(items), "instances": len(items), "distinct_parts": len(cat),
           "triangles": int(sum(c["triangles"] * c["instances"] for c in cat)), "bbox_mm": bbox, "source": source,
           "tessellation": tess, "tree": tree}
    with open(os.path.join(out, "parts.json"), "w") as f:
        json.dump({"assembly": asm, "parts": cat}, f, ensure_ascii=False, indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", nargs="?"); ap.add_argument("--out")
    ap.add_argument("--reclassify", metavar="OUT_DIR", help="only rewrite kind/material/type/params in OUT_DIR/parts.json (no STEP, no OCP needed)")
    ap.add_argument("--deflection", type=float, default=0.5); ap.add_argument("--angle", type=float, default=0.5)
    ap.add_argument("--quantize-positions", action="store_true", help="int16 positions in the GLB (-3.4 MB on a 250-part machine) - three/Blender read it, trimesh does not")
    ap.add_argument("--no-parts", action="store_true")
    ap.add_argument("--groups", metavar="JSON", help="name -> group map ({part name: group}; or a list of {name, node|group}; a name may be 'group/name'). For a FLAT STEP (code-CAD export, "
                    "all parts directly under the root): wraps the parts in one assembly node per group in the GLB + parts.json tree and sets each part's `module` to the group")
    ap.add_argument("--motion-groups", metavar="JSON", help="same formats as --groups, but names the MOTION group of each part (bom.json `group` for a code-CAD machine): written to parts.json as `motion_group`, "
                    "used by the web viewer's cycle animation. Independent of --groups (modules)")
    ap.add_argument("--mesh-parts", metavar="JSON", help="view-only parts that exist only as meshes (no B-rep: bought units imported as STL, concept "
                    "boxes): a list of {name, group, color, pos: flat xyz mm in the STEP frame, idx: flat triangle indices}. Each becomes a part "
                    "(STL download only) under its group node.")
    ap.add_argument("--no-geometry", action="store_true", help="skip STL geometry cross-checks in the params extractor (numpy + parts/*.stl otherwise)")
    a = ap.parse_args()
    if a.reclassify: return reclassify(a.reclassify, not a.no_geometry)
    if not a.input or not a.out or STEPCAFControl_Reader is None: ap.error("input STEP + --out required (and OCP installed)")
    out = os.path.abspath(os.path.expanduser(a.out)); os.makedirs(out, exist_ok=True)
    if not a.no_parts: os.makedirs(os.path.join(out, "parts"), exist_ok=True)

    t0 = time.time()
    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf")); app.NewDocument(TCollection_ExtendedString("XmlOcaf"), doc)
    r = STEPCAFControl_Reader(); r.SetNameMode(True); r.SetColorMode(True)
    assert r.ReadFile(a.input) == IFSelect_RetDone, "cannot read STEP"
    r.Transfer(doc, Message_ProgressRange())
    st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main()); ctool = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
    free = LabelSeq(); st.GetFreeShapes(free)
    print(f"loaded {time.time()-t0:.0f}s, free shapes {free.Length()}", flush=True)

    # --- walk the tree; one part record per referred leaf label -------------------------------
    parts = {}            # entry -> record
    order = []
    ninst = [0]; nnodes = [0]

    def walk(inst, parent_drawing_mod, path):
        """inst: component (or root) label. returns (node dict, assembly-tree dict|None)."""
        nnodes[0] += 1
        ref = referred(inst)
        pn = name(ref) or name(inst); inn = name(inst)
        nm = inn if inn and not re.match(r"^NAUO\d+$", inn) else pn
        drawing = (DRAWING.search(pn) or DRAWING.search(nm))
        drawing = drawing.group(0) if drawing else None
        mod = drawing[:5] if drawing else parent_drawing_mod
        comps = components(ref)
        node = {"name": nm or pn or "node", "matrix": trsf_matrix(st.GetLocation_s(inst)), "children": [], "product": pn, "drawing": drawing, "ref": entry(ref)}
        if comps:
            tree = {"name": pn or nm, "drawing": drawing, "children": []}
            for c in comps:
                cn, ct = walk(c, mod, path + [nm])
                node["children"].append(cn)
                if ct: tree["children"].append(ct)
            return node, tree
        key = entry(ref); ninst[0] += 1
        if key not in parts:
            parts[key] = {"label": ref, "name": pn or nm, "drawing": drawing, "module": mod, "instances": 0, "color": label_color(ctool, inst, ref)}
            order.append(key)
        parts[key]["instances"] += 1
        node["key"] = key
        return node, None

    root, tree = walk(free.Value(1), None, [])
    if tree is None: tree = {"name": root["name"], "drawing": None, "children": []}
    print(f"tree: {nnodes[0]} nodes, {ninst[0]} instances, {len(parts)} distinct parts", flush=True)

    if a.groups:
        root, tree = apply_groups(root, tree, parts, load_groups(a.groups))

    if a.motion_groups:
        apply_motion_groups(parts, load_groups(a.motion_groups))

    # --- unique ids ---------------------------------------------------------------------------
    used = set()
    for k in order:
        p = parts[k]; base = p["drawing"] or slug(p["name"]); pid = base; i = 2
        while pid in used: pid = f"{base}_{i}"; i += 1
        used.add(pid); p["id"] = pid

    # --- mesh each distinct shape once ---------------------------------------------------------
    prm = IMeshTools_Parameters(); prm.Deflection = a.deflection; prm.Angle = a.angle; prm.Relative = False; prm.InParallel = True
    t0 = time.time(); meshes = []; gb = np.array([np.inf] * 3 + [-np.inf] * 3)
    for n, k in enumerate(order):
        p = parts[k]; sh = st.GetShape_s(p["label"])
        BRepMesh_IncrementalMesh(sh, prm)
        v, t, qn = extract(sh); p["shape"] = sh
        g = GProp_GProps(); BRepGProp.VolumeProperties_s(sh, g)
        p["volume_mm3"] = round(g.Mass(), 3); p["triangles"] = int(len(t))
        if len(v):
            p["bbox_mm"] = [np.round(v.min(0), 3).tolist(), np.round(v.max(0), 3).tolist()]
        else: p["bbox_mm"] = None
        p["mesh_index"] = len(meshes); p["v"] = v; p["t"] = t
        meshes.append(((v * MM).astype(np.float32), t.astype(np.uint16 if len(v) < 65536 else np.uint32), qn))
    print(f"meshed {time.time()-t0:.0f}s", flush=True)
    if a.mesh_parts:
        add_mesh_parts(json.load(open(a.mesh_parts)), root, tree, parts, order, meshes, used)

    # --- node tree: attach mesh + part id; global bbox by transforming local bbox corners ------
    def attach(n, M):
        M = M @ n["matrix"]
        if "key" in n:
            p = parts[n["key"]]; n["mesh"] = p["mesh_index"]; n["part"] = p["id"]
            if len(p["v"]):
                w = p["v"] * MM @ M[:3, :3].T + M[:3, 3]
                gb[:3] = np.minimum(gb[:3], w.min(0)); gb[3:] = np.maximum(gb[3:], w.max(0))
        for c in n["children"]: attach(c, M)
    attach(root, np.eye(4))
    total_tris = sum(parts[k]["triangles"] * parts[k]["instances"] for k in order)
    bbox_mm = [(gb[:3] / MM).round(2).tolist(), (gb[3:] / MM).round(2).tolist()]
    tess = {"deflection_mm": a.deflection, "angle_rad": a.angle}
    write_glb(os.path.join(out, "model.glb"), meshes, root,
              {"units": "m", "source_units": "mm", "up": "Z", "source": os.path.basename(a.input), "tessellation": tess}, qpos=a.quantize_positions)

    # --- the instance tree with its placements (step_to_project.py rebuilds the assembly from it) ---------------------
    def inst_json(n):
        m = np.array(n["matrix"], dtype=float).copy(); m[:3, 3] /= MM
        return {"name": n["name"], "product": n.get("product"), "drawing": n.get("drawing"), "ref": n.get("ref"),
                "matrix": [round(float(x), 9) for x in m.reshape(-1)], **({"part": n["part"]} if "part" in n else {}),
                **({"children": [inst_json(c) for c in n["children"]]} if n["children"] else {})}
    with open(os.path.join(out, "instances.json"), "w") as f:
        json.dump(inst_json(root), f, ensure_ascii=False)

    # --- per-part files --------------------------------------------------------------------------
    if not a.no_parts:
        for k in order:
            p = parts[k]; pid = p["id"]
            if "shape" in p:   # mesh-only parts (--mesh-parts) have no B-rep: STL only
                with quiet():   # the STEP writer prints ~10 lines per part to fd 1
                    w = STEPControl_Writer(); w.Transfer(p["shape"], STEPControl_AsIs); w.Write(os.path.join(out, "parts", pid + ".step"))
            write_stl(os.path.join(out, "parts", pid + ".stl"), p["v"], p["t"])
    cat = []
    for k in order:
        p = parts[k]
        cat.append({"id": p["id"], "name": p["name"], "drawing": p["drawing"], "kind": classify(p["name"], p["drawing"]), "material": material_of(p["name"], p["drawing"]),
                    "module": p["module"], **({"motion_group": p["motion_group"]} if p.get("motion_group") else {}), "color": p["color"], "instances": p["instances"], "bbox_mm": p["bbox_mm"],
                    "volume_mm3": p["volume_mm3"], "triangles": p["triangles"],
                    "files": None if a.no_parts else {**({"step": f"parts/{p['id']}.step"} if "shape" in p else {}), "stl": f"parts/{p['id']}.stl"}})
    PRM.apply_all(cat, out, geometry=not a.no_parts and not a.no_geometry)
    asm = {"root": root["name"], "nodes": nnodes[0], "instances": ninst[0], "distinct_parts": len(cat),
           "triangles": int(total_tris), "bbox_mm": bbox_mm, "source": os.path.basename(a.input),
           "tessellation": tess, "tree": tree}
    with open(os.path.join(out, "parts.json"), "w") as f:
        json.dump({"assembly": asm, "parts": cat}, f, ensure_ascii=False, indent=1)
    print(f"done: {len(cat)} parts, {total_tris} tris, glb {os.path.getsize(os.path.join(out,'model.glb'))/1e6:.1f} MB", flush=True)


if __name__ == "__main__":
    main()
