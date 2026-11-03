"""Assembly drawing per node, flat-pack furniture style: one PDF per node, never one for the whole machine.

Each sheet: an exploded isometric view (every part pulled out along the axis of the joint it is docked by, the PartCAD idea:
the axis of the joint on the part it docks to, flipped to point away from that part; a fastener pulled out by its own length),
a dashed insertion line from where it sits to where it is drawn, a numbered balloon per table row, and the table
«No., part, code, qty» (fasteners last, identical ones merged by code: «M5×12 DIN 912 ×8»).
A node with more rows than fit one sheet is split by its joint tree: a big sub-assembly gets its own sheet («Step 1. Assembly …»,
deepest first) and appears on the sheets after it assembled, as one numbered row; identical sub-assemblies are drawn once
(«assemble N pcs»). A sheet still too big (a node without dockings) gets its balloons spread over several sheets.
Texts in the project's language (drawings_i18n.py; --lang overrides).

    python assembly_drawing.py --nodes nodes.json --out build/assy [--png] [--lang ru|en]

nodes.json (written by derive.py assembly) = {"nodes": [{"node": "<name>", "file": "<file stem>",
  "items": [{"name": <placement>, "part": <part id>, "title", "code", "kind", "step": <STEP path, the part in its own frame>
             (or "mesh": <a mesh part's triangles (machine-project mesh parts), {"v": [...], "f": [...]} in its own frame>),
             "loc": [16 floats, row-major placement], "parent": <placement in this node it is docked to> | null,
             "axis": [[x, y, z] origin, [x, y, z] direction] of that docking in world coordinates | null}]}]}
Out: <out>/<file>.pdf per node. Log: `ASSY ok <node>: sheets N, rows M, parts K` or `FAIL <node>: why`; exit 1 on any FAIL.
Needs build123d + matplotlib (the shop-drawings venv: drawings_env.sh).
"""
import argparse
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import drawings_i18n as I  # noqa: E402
import sheet_pdf  # noqa: E402
from sheet_pdf import LW, THIN, TXT, Sheet  # noqa: E402
from build123d import import_step  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from OCP.BRepAdaptor import BRepAdaptor_Curve  # noqa: E402
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform  # noqa: E402
from OCP.BRepMesh import BRepMesh_IncrementalMesh  # noqa: E402
from OCP.GeomAbs import GeomAbs_Line  # noqa: E402
from OCP.HLRAlgo import HLRAlgo_Projector  # noqa: E402
from OCP.HLRBRep import HLRBRep_PolyAlgo, HLRBRep_PolyHLRToShape  # noqa: E402
from OCP.TopAbs import TopAbs_EDGE  # noqa: E402
from OCP.TopExp import TopExp_Explorer  # noqa: E402
from OCP.TopoDS import TopoDS  # noqa: E402
from OCP.gp import gp_Ax1, gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf  # noqa: E402

MAX_ROWS = 28            # table rows per sheet (A2, 5 mm text); more -> sub-assembly steps, then balloons over several sheets
MIN_STEP = 3             # a sub-assembly of fewer distinct parts stays exploded on its parent's sheet
SIZE = "A2"
VIEWS = [np.array(v, float) / np.linalg.norm(v) for v in ((1, -1, 1), (-1, -1, 1), (1, 1, 1), (-1, 1, 1))]   # isometric, from above
BALLOON_R = 4.5
LANG = I.DEFAULT         # the language of the texts on the sheets; main() sets the project's


def is_fastener(it):
    return it.get("kind") == "fasteners"


def row_titles(items):
    """{placement: table title} of the items with a code: a trailing copy number is dropped only where two items of the same
    code differ by it alone («Frame - foot 1» / «… 3» -> one row «Frame - foot», ×2), never a number of the size
    («Washer M5 DIN 125»). Same code = same part to the shop."""
    out, seen = {}, defaultdict(set)
    for it in items:
        if it.get("code"):
            seen[(it.get("code"), re.sub(r"\s+\d+$", "", it.get("title") or it["part"]))].add(it.get("title") or it["part"])
    for it in items:
        if it.get("code"):
            t = it.get("title") or it["part"]
            base = re.sub(r"\s+\d+$", "", t)
            out[it["name"]] = base if len(seen[(it.get("code"), base)]) > 1 else t
    return out


def size_text(code, lang=None):
    """M5x12 -> M5×12: × between numbers, the thread prefix as the documents in the language write it (drawings_i18n)."""
    s = re.sub(rf"(?<=\d)\s*[{I.TIMES_LOOKALIKES}]\s*(?=\d)", "×", str(code or ""))
    return re.sub(r"\bM(?=\d)", I.t("assy.metric", lang or LANG), s)


# ---------------------------------------------------------------- geometry
def mat(m):
    return np.array(m, float).reshape(4, 4)


def trsf(m):
    t = gp_Trsf()
    t.SetValues(*[float(x) for x in np.asarray(m)[:3].reshape(-1)])
    return t


_steps = {}


class MeshShape:
    """A mesh part placed: its triangles in world coordinates. It has no B-rep edges, so the drawing takes its outline
    (silhouette) and its creases (faces meeting at over CREASE degrees), hidden where any part's triangles lie in front."""
    CREASE = 30.0

    def __init__(self, v, f):
        self.v, self.f = np.asarray(v, float), np.asarray(f, np.int64)
        n = np.cross(self.v[self.f[:, 1]] - self.v[self.f[:, 0]], self.v[self.f[:, 2]] - self.v[self.f[:, 0]])
        self.n = n / np.where((ln := np.linalg.norm(n, axis=1, keepdims=True)) == 0, 1, ln)
        e = np.sort(np.concatenate([self.f[:, [0, 1]], self.f[:, [1, 2]], self.f[:, [2, 0]]]), axis=1)
        t = np.tile(np.arange(len(self.f)), 3)
        o = np.lexsort((e[:, 1], e[:, 0]))
        e, t = e[o], t[o]
        i = np.nonzero(np.all(e[1:] == e[:-1], axis=1))[0]
        self.edges, self.ta, self.tb = e[i], t[i], t[i + 1]        # each inner edge with the two triangles sharing it

    def moved(self, d):
        m = MeshShape.__new__(MeshShape)
        m.__dict__.update(self.__dict__, v=self.v + np.asarray(d, float))
        return m

    def outline(self, look):
        """3D segments of the silhouette seen from `look` and of the creases."""
        a, b = self.n[self.ta], self.n[self.tb]
        crease = np.einsum("ij,ij->i", a, b) < math.cos(math.radians(self.CREASE))
        sil = (a @ look > 0) != (b @ look > 0)
        e = self.edges[crease | sil]
        return self.v[e[:, 0]], self.v[e[:, 1]]


def _triangles(shape):
    """World triangles of a meshed TopoDS shape (after BRepMesh), or a MeshShape's own."""
    if isinstance(shape, MeshShape):
        return shape.v[shape.f]
    from OCP.BRep import BRep_Tool
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopLoc import TopLoc_Location
    out = []
    ex = TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(TopoDS.Face(ex.Current()), loc)
        if tri is not None:
            tr = loc.Transformation()
            pts = np.array([[q.X(), q.Y(), q.Z()] for q in (tri.Node(k).Transformed(tr) for k in range(1, tri.NbNodes() + 1))])
            ids = np.array([tri.Triangle(k).Get() for k in range(1, tri.NbTriangles() + 1)], np.int64) - 1
            if len(ids):
                out.append(pts[ids])
        ex.Next()
    return np.concatenate(out) if out else np.zeros((0, 3, 3))


class _Front:
    """Projected triangles (n, 3 corners, [x, y, depth]) binned by their projected box into a grid once; `depth(P2)` = for each
    2D point the depth of the front-most triangle covering it (-inf: none), each point tested only against its cell's few."""

    def __init__(self, T):
        self.T = T
        if not len(T):
            return
        lo, hi = T[:, :, :2].min(1), T[:, :, :2].max(1)
        self.g0, g1 = lo.min(0), hi.max(0)
        self.G = G = int(min(512, max(8, math.sqrt(len(T)))))
        self.cell = np.maximum((g1 - self.g0) / G, 1e-9)
        i0 = np.clip(((lo - self.g0) / self.cell).astype(np.int64), 0, G - 1)
        i1 = np.clip(((hi - self.g0) / self.cell).astype(np.int64), 0, G - 1)
        nx, ny = i1[:, 0] - i0[:, 0] + 1, i1[:, 1] - i0[:, 1] + 1
        cnt = nx * ny                                          # cells each triangle covers
        tri = np.repeat(np.arange(len(T)), cnt)
        k = np.arange(len(tri)) - np.repeat(np.cumsum(cnt) - cnt, cnt)
        cid = (i0[tri, 0] + k % nx[tri]) * G + i0[tri, 1] + k // nx[tri]
        o = np.argsort(cid)
        self.tri = tri[o]
        self.start = np.searchsorted(cid[o], np.arange(G * G + 1))

    def depth(self, P2):
        out = np.full(len(P2), -np.inf)
        if not len(P2) or not len(self.T):
            return out
        G, T = self.G, self.T
        pc = np.clip(((P2 - self.g0) / self.cell).astype(np.int64), 0, G - 1)
        pcell = pc[:, 0] * G + pc[:, 1]
        deg = self.start[pcell + 1] - self.start[pcell]
        pt = np.repeat(np.arange(len(P2)), deg)                # (point, candidate triangle) pairs
        cand = self.tri[self.start[pcell[pt]] + np.arange(len(pt)) - np.repeat(np.cumsum(deg) - deg, deg)]
        a, b, d, q = T[cand, 0], T[cand, 1], T[cand, 2], P2[pt]
        v0, v1, v2 = b[:, :2] - a[:, :2], d[:, :2] - a[:, :2], q - a[:, :2]
        den = v0[:, 0] * v1[:, 1] - v1[:, 0] * v0[:, 1]
        ok = np.abs(den) > 1e-12
        sd = np.where(ok, den, 1)
        u = (v2[:, 0] * v1[:, 1] - v1[:, 0] * v2[:, 1]) / sd
        w = (v0[:, 0] * v2[:, 1] - v2[:, 0] * v0[:, 1]) / sd
        tol = 1e-4                                             # an edge lies on its own triangles' border
        cover = ok & (u >= -tol) & (w >= -tol) & (u + w <= 1 + tol)
        dep = a[:, 2] + u * (b[:, 2] - a[:, 2]) + w * (d[:, 2] - a[:, 2])
        np.maximum.at(out, pt[cover], dep[cover])
        return out


def _projected(tris, look):
    x, y, z = camera(look)
    return np.stack([tris @ x, tris @ y, tris @ z], -1)


def _visible(pts, front, look, eps=0.05):
    """For each 3D point: no triangle of `front` (a _Front) lies in front of it seen along -look (orthographic, as drawn)."""
    x, y, z = camera(look)
    return front.depth(np.stack([pts @ x, pts @ y], 1)) <= pts @ z + eps


def load(it):
    """The placed part (world coordinates) as a TopoDS shape, or a MeshShape for a mesh part."""
    if it.get("mesh"):
        m = json.loads(Path(it["mesh"]).read_text())
        v = np.array(m["v"], float).reshape(-1, 3)
        M = mat(it["loc"])
        return MeshShape(v @ M[:3, :3].T + M[:3, 3], np.array(m["f"], np.int64).reshape(-1, 3))
    if it["step"] not in _steps:
        _steps[it["step"]] = import_step(it["step"]).wrapped
    return BRepBuilderAPI_Transform(_steps[it["step"]], trsf(mat(it["loc"])), True).Shape()


def moved(shape, v):
    if isinstance(shape, MeshShape):
        return shape.moved(v)
    m = np.eye(4)
    m[:3, 3] = v
    return BRepBuilderAPI_Transform(shape, trsf(m), True).Shape()


def bbox(shape):
    if isinstance(shape, MeshShape):
        return shape.v.min(0), shape.v.max(0)
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    b = Bnd_Box()
    BRepBndLib.Add_s(shape, b, True)
    if b.IsVoid():
        return np.zeros(3), np.zeros(3)
    a, c = b.CornerMin(), b.CornerMax()
    return np.array([a.X(), a.Y(), a.Z()]), np.array([c.X(), c.Y(), c.Z()])


def exit_len(o, d, lo, hi):
    """How far the ray o + t*d runs before it leaves the box lo..hi (0 when o is already on or outside its face)."""
    ts = [((hi[i] if d[i] > 0 else lo[i]) - o[i]) / d[i] for i in range(3) if abs(d[i]) > 1e-9]
    return max(0.0, min(ts)) if ts else 0.0


def inside(shape, p):
    if isinstance(shape, MeshShape):                   # a ray along +X crosses a closed mesh an odd number of times from inside
        t = shape.v[shape.f]
        o, d = np.asarray(p, float), np.array([1.0, 1e-7, 2e-7])
        e1, e2 = t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]
        h = np.cross(d, e2)
        a = np.einsum("ij,ij->i", e1, h)
        ok = np.abs(a) > 1e-12
        f = np.where(ok, 1 / np.where(ok, a, 1), 0)
        s_ = o - t[:, 0]
        u = f * np.einsum("ij,ij->i", s_, h)
        q = np.cross(s_, e1)
        w = f * (q @ d)
        tt = f * np.einsum("ij,ij->i", e2, q)
        return bool(np.count_nonzero(ok & (u >= 0) & (w >= 0) & (u + w <= 1) & (tt > 1e-9)) % 2)
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_IN, TopAbs_SOLID
    ex = TopExp_Explorer(shape, TopAbs_SOLID)
    while ex.More():
        c = BRepClass3d_SolidClassifier(ex.Current(), gp_Pnt(*map(float, p)), 1e-4)
        if c.State() == TopAbs_IN:
            return True
        ex.Next()
    return False


def fastener_side(parent, o, d, fbox):
    """+1 when the side of the joint along +d is the free one (the parent's material is on the -d side), -1 the other way, 0 when
    it cannot tell. Probes a ring around the joint a little above and below it, at the fastener's own radius: around a hole the
    ring lies in the plate's material on one side and in air on the other (the hole itself is air on both), on any part shape."""
    size = fbox[1] - fbox[0]
    r = max(0.5 * max(size[i] * (1 - abs(d[i])) for i in range(3)), 0.5)
    a = np.cross(d, [1.0, 0, 0] if abs(d[0]) < 0.9 else [0, 1.0, 0])
    a /= np.linalg.norm(a)
    b = np.cross(d, a)
    ring = [r * (math.cos(t) * a + math.sin(t) * b) for t in np.linspace(0, 2 * math.pi, 8, endpoint=False)]
    eps = min(0.5, 0.25 * r)
    hits = [sum(inside(parent, o + k * eps * d + q) for q in ring) for k in (1, -1)]
    return 0 if hits[0] == hits[1] else 1 if hits[0] < hits[1] else -1


def explode(items, shapes):
    """{placement: offset vector}: each docked item moves along its docking axis (away from its parent) by its own size, plus
    the offset of its parent (a chain opens up like a fan). Roots (docked outside the node, or not docked) stay; without any
    docking in the node the parts move radially from the node centre."""
    box = {n: bbox(s) for n, s in shapes.items()}
    ctr = {n: (a + b) / 2 for n, (a, b) in box.items()}
    size = {n: float(np.linalg.norm(b - a)) for n, (a, b) in box.items()}
    lo = np.min([a for a, _ in box.values()], 0)
    hi = np.max([b for _, b in box.values()], 0)
    span = float(np.linalg.norm(hi - lo)) or 1.0
    by = {it["name"]: it for it in items}
    own = {}
    if not any(it.get("parent") in by for it in items):
        mid = (lo + hi) / 2
        for n, c in ctr.items():
            d = c - mid
            own[n] = d / (np.linalg.norm(d) or 1) * 0.35 * float(np.linalg.norm(d)) if np.linalg.norm(d) > 1e-6 else np.zeros(3)
    for it in items:
        n, p = it["name"], it.get("parent")
        if n in own or p not in by:
            continue
        d = np.array(it["axis"][1], float) if it.get("axis") else ctr[n] - ctr[p]
        if np.linalg.norm(d) < 1e-9:
            d = np.array([0, 0, 1.0])
        d = d / np.linalg.norm(d)
        if is_fastener(it) and it.get("axis"):         # its centre lies inside the part it holds: pull it out to the side
            s = fastener_side(shapes[p], np.array(it["axis"][0], float), d, box[n])   # without material
            if s < 0 or (s == 0 and exit_len(np.array(it["axis"][0], float), d, *box[p]) > exit_len(np.array(it["axis"][0], float), -d, *box[p]) + 1e-6):
                d = -d
        elif np.dot(d, ctr[n] - ctr[p]) < -1e-6:      # an axis has no sign: point away from the parent
            d = -d
        k = 1.3 * size[n] + 4 if is_fastener(it) else min(max(0.6 * size[n], 15.0), 0.3 * span)
        own[n] = d * k
    off = {}

    def total(n, seen=()):
        if n not in off:
            p = by[n].get("parent")
            off[n] = own.get(n, np.zeros(3)) + (total(p, seen + (n,)) if p in by and p not in seen else np.zeros(3))
        return off[n]
    for it in items:
        total(it["name"])
    return off


def camera(look):
    z = look / np.linalg.norm(look)
    y = np.array([0, 0, 1.0]) - z * z[2]
    y /= np.linalg.norm(y)
    return np.cross(y, z), y, z


def views(items, off):
    """The isometric corners, best first for the explode: gaps across the view, parts pulled towards the viewer (a part pulled
    away from the viewer hides behind what it docks to: a screw seen from the back of its plate)."""
    dirs = [v / np.linalg.norm(v) for v in off.values() if np.linalg.norm(v) > 1e-6]

    def score(v):
        return sum(np.linalg.norm(np.cross(v, d)) + 0.6 * float(np.dot(v, d)) for d in dirs)
    return sorted(VIEWS, key=score, reverse=True)


def edge_points(e):
    c = BRepAdaptor_Curve(e)
    a, b = c.FirstParameter(), c.LastParameter()
    n = 2 if c.GetType() == GeomAbs_Line else 10
    return [(c.Value(u).X(), c.Value(u).Y()) for u in np.linspace(a, b, n)]


def hlr(shapes, look):
    """{key: [(p, q) segments]}: visible edges per shape, hidden-line removal over all of them at once (a part in front hides
    the one behind). Polygonal HLR on a mesh (fast and stable; the exact one crashes on big assemblies, OCCT#1546)."""
    keys = [k for k, s in shapes.items() if not isinstance(s, MeshShape)]
    meshes = {k: s for k, s in shapes.items() if isinstance(s, MeshShape)}
    lo = np.min([bbox(s)[0] for s in shapes.values()], 0)
    hi = np.max([bbox(s)[1] for s in shapes.values()], 0)
    defl = max(float(np.linalg.norm(hi - lo)) / 2000, 0.02)
    x, y, z = camera(look)
    out = {}
    if keys:
        algo = HLRBRep_PolyAlgo()
        for k in keys:
            BRepMesh_IncrementalMesh(shapes[k], defl, False, 0.3, True)
            algo.Load(shapes[k])
        ax = gp_Ax2()
        ax.SetAxis(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(*z)))
        ax.SetYDirection(gp_Dir(*y))
        algo.Projector(HLRAlgo_Projector(ax))
        algo.Update()
        h = HLRBRep_PolyHLRToShape()
        h.Update(algo)
    for k in keys:
        seg = []
        for comp in (h.VCompound(shapes[k]), h.Rg1LineVCompound(shapes[k]), h.OutLineVCompound(shapes[k])):
            if comp.IsNull():
                continue
            ex = TopExp_Explorer(comp, TopAbs_EDGE)
            while ex.More():
                pts = edge_points(TopoDS.Edge(ex.Current()))
                seg += list(zip(pts[:-1], pts[1:]))
                ex.Next()
        out[k] = seg
    if meshes:                                       # PolyAlgo cannot take a mesh (a bare triangulation crashes it at random):
        mesh_t = _Front(_projected(np.concatenate([_triangles(m) for m in meshes.values()]), look))   # hide the B-rep lines
        occ_t = _Front(_projected(np.concatenate([_triangles(shapes[k]) for k in keys]), look) if keys else np.zeros((0, 3, 3)))
        for k in keys:                               # a line HLR kept lies on the front-most B-rep surface at that point
            seg = out[k]
            if not seg:
                continue
            A, Bq = np.array([s[0] for s in seg], float), np.array([s[1] for s in seg], float)
            P2 = np.concatenate([A + (Bq - A) * f for f in (0.2, 0.5, 0.8)])
            behind = mesh_t.depth(P2) > occ_t.depth(P2) + 0.05
            keep = behind.reshape(3, -1).sum(0) < 2
            out[k] = [sg for sg, kp in zip(seg, keep) if kp]
        every = _Front(np.concatenate([mesh_t.T, occ_t.T]))
        for k, m in meshes.items():
            a, b = m.outline(z)
            pts = np.concatenate([a + (b - a) * f for f in (0.2, 0.5, 0.8)])
            vis = _visible(pts, every, look).reshape(3, -1).sum(0) >= 2   # a segment is drawn where most of it shows
            out[k] = [(tuple(project(p, look)), tuple(project(q, look))) for p, q in zip(a[vis], b[vis])]
    return out


def project(p, look):
    x, y, _ = camera(look)
    return np.array([np.dot(p, x), np.dot(p, y)])


# ---------------------------------------------------------------- steps: the node's joint tree cut into sheets
def rows_of(items, groups):
    """Table rows of a sheet: parts by part id, fasteners merged by code; a collapsed sub-assembly (groups: root -> step) one row."""
    rows, ft = {}, row_titles(items)
    for it in items:
        if it["name"] in groups:
            g = groups[it["name"]]
            key = ("step", g["no"])
            r = rows.setdefault(key, {"title": I.t("assy.step_row", LANG, title=g["title"]), "code": I.t("assy.step_code", LANG, no=g["no"]),
                                      "fast": False, "members": []})
        elif is_fastener(it):
            title = ft.get(it["name"]) or it.get("title") or it["part"]
            key = ("fast", size_text(it.get("code")), title)
            r = rows.setdefault(key, {"title": title, "code": size_text(it.get("code")), "fast": True, "members": []})
        else:
            key = ("part", it["code"], ft[it["name"]]) if it.get("code") else ("part", it["part"])
            r = rows.setdefault(key, {"title": ft.get(it["name"]) or it.get("title") or it["part"], "code": it.get("code") or "", "fast": False, "members": []})
        r["members"].append(it["name"])
    return list(rows.values())


def plan(items):
    """[(title, items of the sheet, {collapsed root: step}, step | None)] in assembly order: sub-assembly steps first (deepest
    first), the node last. While a subtree has more rows than a sheet holds, its biggest sub-assembly (MIN_STEP distinct
    parts or more, not a fastener) becomes a step of its own and one row on the sheets after it."""
    by = {it["name"]: it for it in items}
    kids = defaultdict(list)
    for it in items:
        if it.get("parent") in by:
            kids[it["parent"]].append(it["name"])
    roots = [it["name"] for it in items if it.get("parent") not in by]
    seen_, todo = set(roots), list(roots)
    while todo:
        todo += [k for k in kids[todo.pop()] if k not in seen_ and not seen_.add(k)]
    for it in items:                                  # a docking cycle (a model error) never reaches a root: draw its parts anyway
        if it["name"] not in seen_:
            roots.append(it["name"])
            seen_.add(it["name"])
            kids[by[it["name"]]["parent"]] = [k for k in kids[by[it["name"]]["parent"]] if k != it["name"]]
            todo = [it["name"]]
            while todo:
                todo += [k for k in kids[todo.pop()] if k not in seen_ and not seen_.add(k)]
    cut, ft = set(), row_titles(items)

    def live(tops):                                   # the sheet of `tops`: their subtrees, a cut sub-assembly as its root only
        out, todo = [], list(tops)
        while todo:
            m = todo.pop()
            out.append(m)
            if m not in cut or m in tops:
                todo += kids[m]
        return out

    def ident(m):                                     # what makes two items the same row: the code and title, else the part
        return ("c", by[m]["code"], ft[m]) if by[m].get("code") else ("p", by[m]["part"])

    def nrows(names):
        return len({("s", m) if m in cut else ident(m) for m in names})

    def split(tops):
        while nrows(live(tops)) > MAX_ROWS:
            cand = [m for m in live(tops) if m not in tops and m not in cut and not is_fastener(by[m]) and nrows(live([m])) >= MIN_STEP]
            if not cand:
                return
            cut.add(max(cand, key=lambda m: nrows(live([m]))))

    order = []

    def post(n):
        for k in kids[n]:
            post(k)
        order.append(n)
    for r in roots:
        post(r)
    for n in order:
        split([n])
    split(roots)

    def sig(n):
        return (ident(n), tuple(sorted(sig(k) for k in kids[n])))
    seen, groups, out = {}, {}, []
    index = list(by).index
    for n in [m for m in order if m in cut]:          # post-order: a step's own sub-steps come before it
        g = seen.get(sig(n))
        if g:
            g["qty"] += 1
            groups[n] = g
            continue
        g = seen[sig(n)] = groups[n] = {"no": len(seen) + 1, "title": by[n].get("title") or by[n]["part"], "qty": 1}
        names = live([n])
        out.append((I.t("assy.step_row", LANG, title=g["title"]), [by[m] for m in sorted(names, key=index)],
                    {m: groups[m] for m in names if m in cut and m != n}, g))
    names = live(roots)
    out.append((I.t("assy.node", LANG), [by[m] for m in sorted(names, key=index)], {m: groups[m] for m in names if m in cut}, None))
    return out


# ---------------------------------------------------------------- the sheet
def balloons(anchors, box):
    """Balloon centres around the drawing: each anchor pushed out to the frame of `box` along the ray from its centre, then
    spread along the frame so no two balloons touch. anchors: [(x, y)] -> [(x, y)]."""
    (x0, y0), (x1, y1) = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    w, h = (x1 - x0) / 2, (y1 - y0) / 2
    per = 4 * (w + h)

    def to_s(a):                                      # point of the frame hit by the ray centre -> anchor, as perimeter length
        dx, dy = a[0] - cx, a[1] - cy
        if abs(dx) < 1e-9 and abs(dy) < 1e-9:
            dy = 1
        t = min(w / abs(dx) if dx else math.inf, h / abs(dy) if dy else math.inf)
        px, py = dx * t, dy * t
        if abs(px - w) < 1e-6:
            return h + py if py > -h else 0                 # right side, bottom -> top: s in [0, 2h]
        if abs(py - h) < 1e-6:
            return 2 * h + (w - px)                        # top, right -> left
        if abs(px + w) < 1e-6:
            return 2 * h + 2 * w + (h - py)                # left, top -> bottom
        return 4 * h + 2 * w + (px + w)                    # bottom, left -> right

    def to_xy(s):
        s %= per
        if s < 2 * h:
            return cx + w, cy - h + s
        s -= 2 * h
        if s < 2 * w:
            return cx + w - s, cy + h
        s -= 2 * w
        if s < 2 * h:
            return cx - w, cy + h - s
        s -= 2 * h
        return cx - w + s, cy - h
    gap = 2 * BALLOON_R + 1.5
    order = sorted(range(len(anchors)), key=lambda i: to_s(anchors[i]))
    want = [to_s(anchors[i]) for i in order]
    groups = [[k] for k in range(len(want))]          # spread along the frame: overlapping runs merge and centre on their wishes
    while True:
        pos = []
        for g in groups:
            c = sum(want[k] for k in g) / len(g)
            pos.append(c - (len(g) - 1) * gap / 2)
        hit = next((k for k in range(len(groups) - 1) if pos[k] + len(groups[k]) * gap > pos[k + 1]), None)
        if hit is None:
            break
        groups[hit:hit + 2] = [groups[hit] + groups[hit + 1]]
    s = [0.0] * len(want)
    for g, p0 in zip(groups, pos):
        for j, k in enumerate(g):
            s[k] = p0 + j * gap
    out = [None] * len(anchors)
    for i, v in zip(order, s):
        out[i] = to_xy(v)
    return out


def draw_sheet(pdf, node, title, sheet_no, sheets, segs, lines, rows, anchors_by_row, step):
    sh = Sheet(SIZE)
    tw = 220                                          # table width on the right
    area = ((30, 75), (sh.W - tw - 25, sh.H - 30))   # drawing area (paper mm), balloons on its frame
    allp = np.array([p for ss in segs.values() for s in ss for p in s] or [(0, 0)])
    lo, hi = allp.min(0), allp.max(0)
    pad = 2 * BALLOON_R + 10
    (ax0, ay0), (ax1, ay1) = area
    s = min((ax1 - ax0 - 2 * pad) / max(hi[0] - lo[0], 1e-6), (ay1 - ay0 - 2 * pad) / max(hi[1] - lo[1], 1e-6))
    ox = (ax0 + ax1) / 2 - s * (lo[0] + hi[0]) / 2
    oy = (ay0 + ay1) / 2 - s * (lo[1] + hi[1]) / 2
    P = lambda p: (ox + s * p[0], oy + s * p[1])  # noqa: E731
    for a, b in lines:                                # insertion lines first, under the parts
        sh.line(P(a), P(b), THIN, ls=(0, (4, 2)), c="#555555")
    for ss in segs.values():
        if ss:
            xs = np.array([[P(a), P(b)] for a, b in ss])
            from matplotlib.collections import LineCollection
            sh.ax.add_collection(LineCollection(xs, linewidths=LW * 0.8, colors="k"))
    fr = ((P(lo)[0] - pad + 4, P(lo)[1] - pad + 4), (P(hi)[0] + pad - 4, P(hi)[1] + pad - 4))
    shown = [i for i, r in enumerate(rows) if anchors_by_row.get(i) is not None]
    centres = balloons([P(anchors_by_row[i]) for i in shown], fr)
    for i, c in zip(shown, centres):
        a = P(anchors_by_row[i])
        d = np.subtract(a, c)
        n = np.linalg.norm(d) or 1
        sh.line((c[0] + d[0] / n * BALLOON_R, c[1] + d[1] / n * BALLOON_R), a, THIN)
        sh.ax.plot([a[0]], [a[1]], marker="o", ms=1.6, color="k")
        sh.ax.add_patch(plt.Circle(c, BALLOON_R, fill=True, fc="white", ec="k", lw=LW * 0.8, zorder=3))
        sh.ax.text(c[0], c[1], str(rows[i]["no"]), ha="center", va="center", fontsize=TXT * 0.75, zorder=4, family="DejaVu Sans")
    # the table, top right: No., part, code, qty
    x0, y_top, rh = sh.W - tw - 5, sh.H - 5, 7.0
    cols = [(x, w, name) for (x, w), name in zip(((0, 12), (12, 120), (132, 64), (196, 24)), I.t("assy.cols", LANG))]
    head = ((I.t("assy.step_head", LANG, no=step["no"]) if step else "") + (title or I.t("assy.node", LANG))).strip()
    sh.text(x0 + tw / 2, y_top - 6, head if len(head) < 60 else head[:58] + "…", size=TXT * 0.8)
    y = y_top - 12
    sh.ax.add_patch(plt.Rectangle((x0, y - rh * (len(rows) + 1)), tw, rh * (len(rows) + 1), fill=False, lw=LW))
    for k, (cx, cw, name) in enumerate(cols):
        if k:
            sh.line((x0 + cx, y), (x0 + cx, y - rh * (len(rows) + 1)), THIN)
        sh.text(x0 + cx + cw / 2, y - rh / 2, name, size=TXT * 0.6)
    for r_i, r in enumerate(rows):
        yy = y - rh * (r_i + 1)
        sh.line((x0, yy), (x0 + tw, yy), THIN if r_i else LW)
        qty = f"×{len(r['members'])}" if r["fast"] else I.t("assy.qty", LANG, n=len(r["members"]))
        cells = [str(r["no"]), r["title"], r["code"], qty]
        for (cx, cw, _), txt in zip(cols, cells):
            txt = str(txt)
            fit = max(4, int(cw / 1.9))
            if len(txt) > fit:
                txt = txt[:fit - 1] + "…"
            sh.text(x0 + cx + (1.5 if cw > 30 else cw / 2), yy - rh / 2, txt, ha="left" if cw > 30 else "center", size=TXT * 0.55)
    notes = [I.t("assy.note_explode", LANG), I.t("assy.note_fasteners", LANG)]
    if step and step["qty"] > 1:
        notes.insert(0, I.t("assy.note_make", LANG, n=step["qty"]))
    if sheets > 1:
        notes.append(I.t("assy.note_sheets", LANG, n=sheets))
    ny = 66
    for nt in reversed(notes):
        sh.text(sh.W - 280, ny, nt, ha="left", size=TXT * 0.55)
        ny += 5.5
    info = {"title": node, "material_line": I.t("assy.kind", LANG), "qty": 1, "code": I.t("assy.code", LANG, no=sheet_no, of=sheets),
            "lang": LANG}
    sh.title(info, sheet_no, sheets, s)
    pdf.savefig(sh.fig)
    plt.close(sh.fig)
    return s


def make_node(spec, out, png=False):
    items = spec["items"]
    if not items:
        raise ValueError("no parts in the node")
    shapes = {it["name"]: load(it) for it in items}
    sheets_plan = plan(items)
    by = {it["name"]: it for it in items}
    pages = []
    for title, its, groups, step in sheets_plan:
        names = {it["name"] for it in its}
        loc_items = [dict(it, parent=it["parent"] if it.get("parent") in names else None) for it in its]
        coll = {}                                     # collapsed sub-assembly root -> its members (drawn as one piece)
        for root in groups:
            if root in names:
                mem, todo = [], [root]
                while todo:
                    m = todo.pop()
                    mem.append(m)
                    todo += [k for k, v in by.items() if v.get("parent") == m]
                coll[root] = mem
        hidden = {m for r, mem in coll.items() for m in mem if m != r}
        vis_items = [it for it in loc_items if it["name"] not in hidden]
        off = explode(vis_items, {it["name"]: shapes[it["name"]] for it in vis_items})
        draw = {}
        for it in vis_items:
            v = off[it["name"]]
            for m in coll.get(it["name"], [it["name"]]):
                draw[m] = moved(shapes[m], v)
        rows = rows_of(vis_items, {r: groups[r] for r in coll})
        rows.sort(key=lambda r: r["fast"])
        for k, r in enumerate(rows):
            r["no"] = k + 1
        best_view = None
        for look in views(vis_items, off):            # the best corner, unless it hides a whole row (a screw behind a shaft)
            segs = hlr(draw, look)
            n_hidden = sum(not any(segs.get(mm) for m in r["members"] for mm in coll.get(m, [m])) for r in rows)
            if best_view is None or n_hidden < best_view[0]:
                best_view = (n_hidden, look, segs)
            if not n_hidden:
                break
        _, look, segs = best_view
        lines = []
        for it in vis_items:
            v = off[it["name"]]
            if it.get("parent") and np.linalg.norm(v - off[it["parent"]]) > 1e-6:
                a = np.array(it["axis"][0], float) if it.get("axis") else (lambda b: (b[0] + b[1]) / 2)(bbox(shapes[it["name"]]))
                lines.append((project(a + off[it["parent"]], look), project(a + v, look)))
        anchor = {}
        for k, r in enumerate(rows):                  # the balloon points at the member with the most visible edge length
            best = None
            for m in r["members"]:
                for mm in coll.get(m, [m]):
                    ss = segs.get(mm) or []
                    L = sum(math.dist(a, b) for a, b in ss)
                    if ss and (best is None or L > best[0]):
                        a, b = max(ss, key=lambda q: math.dist(*q))
                        best = (L, ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2))
            if best is None:                          # hidden from every corner: point at where it is
                lo, hi = bbox(draw[r["members"][0]])
                best = (0, tuple(project((lo + hi) / 2, look)))
            anchor[k] = best[1]
        chunks = [list(range(i, min(i + MAX_ROWS, len(rows)))) for i in range(0, len(rows), MAX_ROWS)] or [[]]
        for ch in chunks:
            pages.append((title, segs, lines, [rows[i] for i in ch], {j: anchor[i] for j, i in enumerate(ch)}, step))
    pdf_path = out / f"{spec['file']}.pdf"
    with PdfPages(pdf_path) as pdf:
        for k, (title, segs, lines, rows, anchors, step) in enumerate(pages):
            draw_sheet(pdf, spec["node"], title, k + 1, len(pages), segs, lines, rows, anchors, step)
    if png:
        import fitz
        doc = fitz.open(pdf_path)
        for k, pg in enumerate(doc):
            pg.get_pixmap(dpi=60).save(out / f"{spec['file']}-{k + 1}.png")
    return len(pages), sum(len(p[3]) for p in pages), len(items)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--nodes", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--png", action="store_true")
    ap.add_argument("--lang", help="language of the texts on the sheets (default: the project's .engineer/locale.json, else ru)")
    a = ap.parse_args()
    global LANG
    LANG = I.lang_of(a.lang) if a.lang else I.project_lang()
    data = json.loads(Path(a.nodes).read_text(encoding="utf-8"))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    fail = 0
    for spec in data["nodes"]:
        try:
            n, r, k = make_node(spec, out, a.png)
            print(f"ASSY ok {spec['node']}: sheets {n}, rows {r}, parts {k}", flush=True)
        except Exception as ex:                       # noqa: BLE001
            fail += 1
            (out / f"{spec['file']}.pdf").unlink(missing_ok=True)
            print(f"FAIL {spec['node']}: {type(ex).__name__}: {ex}", flush=True)
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
