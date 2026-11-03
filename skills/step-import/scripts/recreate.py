#!/usr/bin/env python3
"""A client's part (a B-rep solid out of their STEP) -> build123d code that builds the same solid from named numbers.

    recreate(shape, name) -> {"kind": "sheet" | "turned" | "prism" | None, "code": <module text>, "params": {...}, "reason": str}
    compare(ref, got)     -> {"volume_rel", "bbox_mm", "sym_diff_rel", "deviation_mm", "holes": {"ref", "got"}, "ok"}

The code is a module whose `build(**params)` returns the solid in the part's own coordinates, built from the framework
template `recreated.py` (machine-project/templates; a project keeps its copy at mechanics/cad/recreated.py):

  turned  every face is a surface of revolution about one axis: the half-section profile, revolved. Params: the diameters
          `d1..` and the stations along the axis `z1..` the profile is drawn from.
  sheet   constant thickness: the faces of one skin are panels (planar) and bends (tube sectors), each thickened inwards.
          Params: `thickness`, `bend_radius` (the most common inner radius). Outlines stay coordinates in each panel's frame.
  prism   2.5D along one axis: slabs between the levels of the faces square to it, each its section extruded.

Nothing is trusted: the caller builds the code and compares it with the source (`compare`, tolerances in TOL); a part whose
code does not match stays on the client's geometry with the reason. `recreate` tries turned, sheet, prism in that order and
returns the first whose own build matches; `reason` then says why the others did not."""
import math
import os
import sys
import traceback
from collections import Counter, defaultdict

from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.BRepGProp import BRepGProp
from OCP.BRepTools import BRepTools, BRepTools_WireExplorer
from OCP.Bnd import Bnd_Box
from OCP.GCPnts import GCPnts_QuasiUniformDeflection
from OCP.GProp import GProp_GProps
from OCP.GeomAbs import (GeomAbs_Circle, GeomAbs_Cone, GeomAbs_Cylinder, GeomAbs_Line, GeomAbs_Plane, GeomAbs_Sphere,
                         GeomAbs_Torus)
from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Dir, gp_Lin, gp_Pln, gp_Pnt

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(HERE, "..", "..", "machine-project", "templates")

# a recreated part matches its source when all of these hold
TOL = {"volume_rel": 0.005, "bbox_mm": 0.05, "sym_diff_rel": 0.01, "deviation_mm": 0.1}
ND = 4                      # decimals of a coordinate in the code (0.1 um)


# ---------------------------------------------------------------- small geometry helpers
def _v(p):
    return (p.X(), p.Y(), p.Z())


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b, k=1.0):
    return (a[0] + k * b[0], a[1] + k * b[1], a[2] + k * b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a):
    n = math.sqrt(_dot(a, a))
    return (a[0] / n, a[1] / n, a[2] / n) if n > 1e-15 else a


def _norm_len(a):
    return math.sqrt(_dot(a, a))


def _snap_dir(d):
    """A direction with components snapped to 0 / ±1 when they are within 1e-9 (code reads (0, 0, 1), not 6.1e-17)."""
    d = tuple(0.0 if abs(c) < 1e-9 else (math.copysign(1.0, c) if abs(abs(c) - 1) < 1e-9 else c) for c in d)
    return _norm(d)


def _r(x, nd=ND):
    x = round(float(x), nd)
    return 0.0 if x == 0 else x


def _fmt(x, nd=ND):
    x = _r(x, nd)
    return repr(int(x)) + ".0" if x == int(x) and abs(x) < 1e15 else repr(x)


def _fmtv(v, nd=ND):
    return "(" + ", ".join(_fmt(c, nd) for c in v) + ")"


def _faces(shape):
    out, ex = [], TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        out.append(TopoDS.Face(ex.Current()))
        ex.Next()
    return out


def _volume(shape):
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    return g.Mass()


def _area(face):
    g = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, g)
    return g.Mass()


def _bbox(shape):
    b = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, b, False, False)
    return _v(b.CornerMin()) + _v(b.CornerMax())


def _wrapped(s):
    return getattr(s, "wrapped", s)


class _Surf:
    """A face's surface: kind, and for plane / revolution surfaces their axis."""

    def __init__(self, face):
        self.face = face
        a = BRepAdaptor_Surface(face)
        self.adaptor = a
        self.type = a.GetType()
        self.rev = face.Orientation() == TopAbs_REVERSED
        if self.type == GeomAbs_Plane:
            ax = a.Plane().Position()
        elif self.type == GeomAbs_Cylinder:
            c = a.Cylinder()
            ax, self.radius = c.Position(), c.Radius()
        elif self.type == GeomAbs_Cone:
            ax = a.Cone().Position()
        elif self.type == GeomAbs_Torus:
            ax = a.Torus().Position()
        elif self.type == GeomAbs_Sphere:
            ax = a.Sphere().Position()
        else:
            ax = None
        if ax is not None:
            self.loc, self.dir, self.xdir, self.direct = _v(ax.Location()), _v(ax.Direction()), _v(ax.XDirection()), ax.Direct()

    def normal(self, u, v):
        """Outward normal of the face (orientation applied) at (u, v)."""
        from OCP.BRepLProp import BRepLProp_SLProps
        pr = BRepLProp_SLProps(self.adaptor, u, v, 1, 1e-7)
        n = _v(pr.Normal())
        return (-n[0], -n[1], -n[2]) if self.rev else n


def _samples(face, k=5):
    """Up to k points inside the face (centroids of its largest mesh triangles) with their (u, v)."""
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopLoc import TopLoc_Location
    from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
    BRepMesh_IncrementalMesh(face, 0.2, False, 0.3, True)
    loc = TopLoc_Location()
    tri = BRep_Tool.Triangulation_s(face, loc)
    if tri is None:
        return []
    tr = loc.Transformation()
    cands = []
    for i in range(1, tri.NbTriangles() + 1):
        a, b, c = tri.Triangle(i).Get()
        p = [_v(tri.Node(j).Transformed(tr)) for j in (a, b, c)]
        area = 0.5 * math.sqrt(_dot(*(2 * [_cross(_sub(p[1], p[0]), _sub(p[2], p[0]))])))
        cands.append((area, tuple(sum(q[i_] for q in p) / 3 for i_ in range(3))))
    cands.sort(key=lambda x: -x[0])
    surf = BRep_Tool.Surface_s(face)
    out = []
    for _, c in cands[:k]:
        pr = GeomAPI_ProjectPointOnSurf(gp_Pnt(*c), surf)
        if pr.NbPoints():
            u, v = pr.LowerDistanceParameters()
            out.append((_v(pr.NearestPoint()), u, v))
    return out


# ---------------------------------------------------------------- compare
def _concave_holes(shape):
    """Round holes: concave cylindrical faces grouped by (axis line, radius) whose angles add up to a full turn. Faces join a
    group within 0.01 mm / 1e-3 mm: a STEP writer can move the two halves of one hole's axis by a micron, and rounding would
    split them whenever that axis lies on a rounding boundary."""
    groups = []                                   # [axis point, direction, radius, turn]
    for f in _faces(shape):
        s = _Surf(f)
        if s.type != GeomAbs_Cylinder:
            continue
        u0, u1, v0, v1 = BRepTools.UVBounds_s(f)
        u, v = (u0 + u1) / 2, (v0 + v1) / 2
        p = _v(s.adaptor.Value(u, v))
        n = s.normal(u, v)
        foot = _add(s.loc, s.dir, _dot(_sub(p, s.loc), s.dir))
        if _dot(n, _sub(foot, p)) <= 0:          # normal points away from the axis: a boss, not a hole
            continue
        d = _snap_dir(s.dir if max(s.dir, key=abs) > 0 else tuple(-c for c in s.dir))
        o = _sub(s.loc, tuple(c * _dot(s.loc, d) for c in d))     # the axis point nearest the origin
        for g in groups:
            if max(abs(x - y) for x, y in zip(g[1], d)) < 1e-4 and abs(g[2] - s.radius) < 1e-3 and max(abs(x - y) for x, y in zip(g[0], o)) < 0.01:
                g[3] += u1 - u0
                break
        else:
            groups.append([o, d, s.radius, u1 - u0])
    return sum(1 for g in groups if g[3] > 2 * math.pi * 0.95)


def _mesh(shape, defl):
    """Triangles of a shape's surface: (centroids, areas, triangle corners [n, 3, 3])."""
    import numpy as np
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopLoc import TopLoc_Location
    BRepMesh_IncrementalMesh(shape, defl, False, 0.2, True)
    tris = []
    for f in _faces(shape):
        loc = TopLoc_Location()
        t = BRep_Tool.Triangulation_s(f, loc)
        if t is None:
            continue
        tr = loc.Transformation()
        nodes = np.array([_v(t.Node(i).Transformed(tr)) for i in range(1, t.NbNodes() + 1)])
        idx = np.array([t.Triangle(i).Get() for i in range(1, t.NbTriangles() + 1)]) - 1
        tris.append(nodes[idx])
    T = np.concatenate(tris) if tris else np.zeros((0, 3, 3))
    c = T.mean(1)
    a = 0.5 * np.linalg.norm(np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]), axis=1)
    return c, a, T


def _point_tri(p, T):
    """Distance from points p [n, 3] to triangles T [n, k, 3, 3] (each point to its k candidates): [n, k]."""
    import numpy as np
    a, b, c = T[..., 0, :], T[..., 1, :], T[..., 2, :]
    p = p[:, None, :]
    ab, ac, ap = b - a, c - a, p - a
    d1, d2 = (ab * ap).sum(-1), (ac * ap).sum(-1)
    bp = p - b
    d3, d4 = (ab * bp).sum(-1), (ac * bp).sum(-1)
    cp = p - c
    d5, d6 = (ab * cp).sum(-1), (ac * cp).sum(-1)
    va, vb, vc = d3 * d6 - d5 * d4, d5 * d2 - d1 * d6, d1 * d4 - d3 * d2
    den = np.where(va + vb + vc == 0, 1e-30, va + vb + vc)
    v, w = vb / den, vc / den
    q = a + ab * v[..., None] + ac * w[..., None]                                 # inside the face
    with np.errstate(divide="ignore", invalid="ignore"):
        e = [(a, ab, np.clip(d1 / (d1 - d3), 0, 1)), (a, ac, np.clip(d2 / (d2 - d6), 0, 1)),
             (b, c - b, np.clip((d4 - d3) / ((d4 - d3) + (d5 - d6)), 0, 1))]
    inside = (va >= 0) & (vb >= 0) & (vc >= 0)
    best = np.where(inside, np.linalg.norm(p - q, axis=-1), np.inf)
    for o, dvec, t in e:                                                            # nearest point of each edge
        t = np.nan_to_num(t, nan=0.0)
        best = np.minimum(best, np.linalg.norm(p - (o + dvec * t[..., None]), axis=-1))
    return best


def _deviation(src, dst, dst_shape, near, k=12, exact_max=4000):
    """For each triangle of src: the distance of its centroid to dst's surface, with its area. The mesh answer (the k
    triangles of dst whose centroids are nearest) can only overstate the distance; every point it puts beyond `near` is
    measured again on dst's exact faces."""
    import numpy as np
    from scipy.spatial import cKDTree
    from OCP.BRep import BRep_Builder
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.TopoDS import TopoDS_Compound
    c_src, a_src, _ = src
    c_dst, _, T = dst
    tree = cKDTree(c_dst)
    k = min(k, len(c_dst))
    _, idx = tree.query(c_src, k=k)
    idx = idx.reshape(len(c_src), -1)
    d = np.empty(len(c_src))
    for s in range(0, len(c_src), 20000):
        sl = slice(s, s + 20000)
        d[sl] = _point_tri(c_src[sl], T[idx[sl]]).min(1)
    far = np.nonzero(d > near)[0]
    if len(far):
        comp, bld = TopoDS_Compound(), BRep_Builder()          # the boundary: a point inside the solid is not at distance 0
        bld.MakeCompound(comp)
        for f in _faces(dst_shape):
            bld.Add(comp, f)
        for i in far[np.argsort(-d[far])][:exact_max]:
            ex = BRepExtrema_DistShapeShape(BRepBuilderAPI_MakeVertex(gp_Pnt(*c_src[i])).Vertex(), comp)
            if ex.IsDone() and ex.NbSolution():
                d[i] = min(d[i], ex.Value())
    return d, a_src


def surface_deviation(ref, got, defl=0.01):
    """(max distance between the two surfaces, both ways, mm; their XOR volume estimated as the integral of the distance over
    both surfaces / 2). The volume between two near-equal surfaces is that integral to first order; OCC's booleans on two
    solids that share most of their faces may hang or hand the arguments back untouched, this cannot."""
    mr, mg = _mesh(ref, defl), _mesh(got, defl)
    if not len(mr[0]) or not len(mg[0]):
        return float("inf"), float("inf")
    noise = 2 * defl                         # two meshes of one curved surface differ by up to their deflections
    d1, a1 = _deviation(mg, mr, ref, noise)
    d2, a2 = _deviation(mr, mg, got, noise)
    x1, x2 = (d1 - noise).clip(0), (d2 - noise).clip(0)
    return float(max(x1.max(), x2.max())), float(((x1 * a1).sum() + (x2 * a2).sum()) / 2)


def compare(ref, got):
    """How far `got` is from `ref` (both shapes in the same coordinates): see the module doc and TOL."""
    ref, got = _wrapped(ref), _wrapped(got)
    vr, vg = _volume(ref), _volume(got)
    br, bg = _bbox(ref), _bbox(got)
    bb = max(abs(a - b) for a, b in zip(br, bg))
    dev, xor = surface_deviation(ref, got)
    xor = max(xor, abs(vg - vr))            # the surface integral misses a hole that is not there; XOR >= |V1 - V2| always
    sym = xor / vr if vr else float("inf")
    hr, hg = _concave_holes(ref), _concave_holes(got)
    out = {"volume_rel": abs(vg - vr) / vr if vr else float("inf"), "bbox_mm": bb, "sym_diff_rel": sym,
           "deviation_mm": dev, "holes": {"ref": hr, "got": hg}}
    out["ok"] = (out["volume_rel"] <= TOL["volume_rel"] and bb <= TOL["bbox_mm"] and sym <= TOL["sym_diff_rel"]
                 and dev <= TOL["deviation_mm"] and hr == hg)
    return out


# ---------------------------------------------------------------- outlines -> code
def _edge_steps(edge, to_uv, defl=0.01):
    """One oriented edge -> (start uv, steps after the start) in the frame of to_uv."""
    c = BRepAdaptor_Curve(edge)
    f, l = c.FirstParameter(), c.LastParameter()
    if edge.Orientation() == TopAbs_REVERSED:
        f, l = l, f
    P = lambda t: to_uv(_v(c.Value(t)))            # noqa: E731
    t = c.GetType()
    if t == GeomAbs_Line:
        return P(f), [P(l)]
    if t == GeomAbs_Circle:
        span = abs(l - f)
        if span > math.pi * 1.5:                    # a long arc: two halves (a three-point arc cannot be a full circle)
            m = (f + l) / 2
            return P(f), [("arc", P((f + m) / 2), P(m)), ("arc", P((m + l) / 2), P(l))]
        return P(f), [("arc", P((f + l) / 2), P(l))]
    d = GCPnts_QuasiUniformDeflection(c, defl, min(f, l), max(f, l))
    ts = [d.Parameter(i) for i in range(1, d.NbPoints() + 1)]
    if f > l:
        ts = ts[::-1]
    ts[0], ts[-1] = f, l
    return P(f), [("pts", [P(x) for x in ts[1:]])]


def _wire_steps(wire, face, to_uv):
    """A wire of a face -> outline steps (see recreated.py) in uv, or ("circle", u, v, d) for a single full circle."""
    ex = BRepTools_WireExplorer(wire, face)
    edges = []
    while ex.More():
        edges.append(ex.Current())
        ex.Next()
    if len(edges) == 1:
        c = BRepAdaptor_Curve(edges[0])
        if c.GetType() == GeomAbs_Circle and abs(abs(c.LastParameter() - c.FirstParameter()) - 2 * math.pi) < 1e-6:
            ci = c.Circle()
            u, v = to_uv(_v(ci.Location()))
            return ("circle", u, v, 2 * ci.Radius())
    steps = []
    for e in edges:
        s0, rest = _edge_steps(e, to_uv)
        if not steps:
            steps.append(s0)
        steps.extend(rest)
    if len(steps) > 1 and not isinstance(steps[-1][0], str) and _close(steps[-1], steps[0]):
        steps.pop()                                 # the outline closes itself
    return steps


def _close(a, b, tol=1e-6):
    return abs(a[0] - b[0]) < tol and abs(a[1] - b[1]) < tol


def _fmt_uv(p):
    return f"({_fmt(p[0])}, {_fmt(p[1])})"


def _fmt_steps(steps, sub=None, indent="        "):
    """Outline steps -> Python literal. `sub(value, axis)` may return a name for a coordinate (a param)."""
    def uv(p):
        if sub is None:
            return _fmt_uv(p)
        return f"({sub(p[0], 0)}, {sub(p[1], 1)})"
    items = []
    for s in steps:
        if isinstance(s[0], str) and s[0] == "arc":
            items.append(f'("arc", {_fmt_uv(s[1])}, {uv(s[2])})')
        elif isinstance(s[0], str) and s[0] == "pts":
            items.append('("pts", [' + ", ".join(_fmt_uv(q) for q in s[1]) + "])")
        else:
            items.append(uv(s))
    lines, cur = [], ""
    for it in items:
        if len(cur) + len(it) > 110:
            lines.append(cur)
            cur = ""
        cur += it + ", "
    lines.append(cur)
    return "[\n" + "".join(indent + ln.rstrip() + "\n" for ln in lines if ln) + indent[:-4] + "]"


def _plane_frame(normal, point):
    """A readable frame on a plane: origin = the foot of the part's origin, x = a global axis (or the nearest one projected)."""
    z = _snap_dir(normal)
    origin = tuple(c * _dot(point, z) for c in z)
    for g in ((1, 0, 0), (0, 1, 0), (0, 0, 1)):
        x = _sub(g, tuple(c * _dot(g, z) for c in z))
        if math.sqrt(_dot(x, x)) > 0.5:
            break
    x = _snap_dir(_norm(x))
    return origin, x, z


def _to_uv(origin, x, z):
    y = _cross(z, x)
    return lambda p: (_dot(_sub(p, origin), x), _dot(_sub(p, origin), y))


def _flat_code(face, origin, x, z):
    """A planar face -> (outer steps, holes [(u, v, d)], cuts [steps])."""
    to_uv = _to_uv(origin, x, z)
    outer = BRepTools.OuterWire_s(face)
    holes, cuts = [], []
    ex = TopExp_Explorer(face, TopAbs_EDGE)            # wires: walk them with the face for orientation
    from OCP.TopAbs import TopAbs_WIRE
    wx = TopExp_Explorer(face, TopAbs_WIRE)
    outer_steps = None
    while wx.More():
        w = TopoDS.Wire(wx.Current())
        st = _wire_steps(w, face, to_uv)
        if w.IsSame(outer):
            if st and st[0] == "circle":
                _, u, v, d = st
                r = d / 2
                st = [(u + r, v), ("arc", (u, v + r), (u - r, v)), ("arc", (u, v - r), (u + r, v))]
            outer_steps = st
        elif st and st[0] == "circle":
            holes.append(st[1:])
        else:
            cuts.append(st)
        wx.Next()
    del ex
    return outer_steps, holes, cuts


def _flat_args(face, origin, x, z, t_expr=None):
    outer, holes, cuts = _flat_code(face, origin, x, z)
    fr = f"frame({_fmtv(origin)}, {_fmtv(x, 9)}, {_fmtv(z, 9)})"
    h = "[" + ", ".join(f"({_fmt(u)}, {_fmt(v)}, {_fmt(d)})" for u, v, d in holes) + "]"
    c = "[" + ", ".join(_fmt_steps(s, indent="            ") for s in cuts) + "]"
    return fr, _fmt_steps(outer, indent="            "), h, c


# ---------------------------------------------------------------- turned
def _common_axis(faces):
    axis = None
    for f in faces:
        s = _Surf(f)
        if s.type == GeomAbs_Plane:
            continue
        if s.type not in (GeomAbs_Cylinder, GeomAbs_Cone, GeomAbs_Torus, GeomAbs_Sphere):
            return None
        if axis is None:
            axis = (s.loc, _snap_dir(s.dir))
            continue
        if s.type != GeomAbs_Sphere and abs(abs(_dot(axis[1], s.dir)) - 1) > 1e-7:
            return None
        off = _sub(s.loc, axis[0])
        perp = _sub(off, tuple(c * _dot(off, axis[1]) for c in axis[1]))
        if math.sqrt(_dot(perp, perp)) > 1e-5:
            return None
    if axis is None:
        return None
    for f in faces:
        s = _Surf(f)
        if s.type == GeomAbs_Plane and abs(abs(_dot(s.dir, axis[1])) - 1) > 1e-7:
            return None
    return axis


def _turned(shape, name):
    faces = _faces(shape)
    ax = _common_axis(faces)
    if ax is None:
        return None, "not turned: a face is not a surface of revolution about one axis"
    p0, d = ax
    o = _add(p0, d, -_dot(p0, d))                      # the axis point nearest the part's origin
    o = tuple(_r(c, 9) for c in o)
    _, x, _ = _plane_frame(d, (0, 0, 0))
    x = _snap_dir(_sub(x, tuple(c * _dot(x, d) for c in d)))
    b = _bbox(shape)
    big = 4 * max(b[3] - b[0], b[4] - b[1], b[5] - b[2]) + 10
    # half plane: from the axis outwards along x, spanning the part along the axis
    from OCP.gp import gp_Ax3
    pln = gp_Pln(gp_Ax3(gp_Pnt(*o), gp_Dir(*_cross(x, d)), gp_Dir(*x)))
    hf = BRepBuilderAPI_MakeFace(pln, 0, big, -big, big).Face()      # u >= 0: along x, away from the axis
    com = BRepAlgoAPI_Common(shape, hf)
    sec = _faces(com.Shape())
    if len(sec) != 1:
        return None, f"not turned: the half-section has {len(sec)} faces"
    to_r = lambda p: (_dot(_sub(p, o), x), _dot(_sub(p, o), d))      # noqa: E731
    wires = []
    from OCP.TopAbs import TopAbs_WIRE
    wx = TopExp_Explorer(sec[0], TopAbs_WIRE)
    while wx.More():
        wires.append(TopoDS.Wire(wx.Current()))
        wx.Next()
    if len(wires) != 1:
        return None, "not turned: the half-section has a hole (a closed void inside)"
    steps = _wire_steps(wires[0], sec[0], to_r)
    if steps and steps[0] == "circle":
        return None, "not turned: the half-section is a circle"
    # named numbers: diameters of the corners, stations along the axis
    rs = sorted({_r(s[0]) for s in _corners(steps) if _r(s[0]) > 0})
    zs = sorted({_r(s[1]) for s in _corners(steps)})
    params = {}
    rname, zname = {}, {}
    if len(rs) <= 12 and len(zs) <= 16:
        for i, r_ in enumerate(rs, 1):
            rname[r_] = f"d{i}"
            params[f"d{i}"] = _r(2 * r_)
        for i, z_ in enumerate(zs, 1):
            zname[z_] = f"z{i}"
            params[f"z{i}"] = z_

    def sub(val, axis):
        val = _r(val)
        if axis == 0:
            return f"{rname[val]} / 2" if val in rname else _fmt(val)
        return zname[val] if val in zname else _fmt(val)
    sig = ", ".join(f"{k}={_fmt(v)}" for k, v in params.items())
    code = (f'"""{name}: turned, the half-section revolved about its axis (recreated from the client\'s CAD)."""\n'
            "from recreated import frame, turned\n\n\n"
            f"def build({sig}):\n"
            f"    return turned(frame({_fmtv(o, 9)}, {_fmtv(x, 9)}, {_fmtv(d, 9)}), {_fmt_steps(steps, sub)})\n")
    return {"kind": "turned", "code": selfcontained(code), "params": params}, None


def _corners(steps):
    """The end points of the steps (not arc mid points, not polyline points)."""
    out = []
    for s in steps:
        if isinstance(s[0], str):
            out.append(s[2] if s[0] == "arc" else s[1][-1])
        else:
            out.append(s)
    return out


# ---------------------------------------------------------------- sheet
def _thickness_hits(shape, faces):
    """Per face: [(sample, outward normal, hit distance, hit face)] of a ray from a point of the face inwards."""
    inter = IntCurvesFace_ShapeIntersector()
    inter.Load(shape, 1e-6)
    out = {}
    for i, f in enumerate(faces):
        s = _Surf(f)
        if s.type not in (GeomAbs_Plane, GeomAbs_Cylinder):
            out[i] = []
            continue
        rows = []
        for p, u, v in _samples(f):
            n = s.normal(u, v)
            inter.Perform(gp_Lin(gp_Pnt(*p), gp_Dir(*(-c for c in n))), 1e-4, 1e6)
            best = None
            for k in range(1, inter.NbPnt() + 1):
                w = inter.WParameter(k)
                if w > 1e-4 and (best is None or w < best[0]):
                    best = (w, inter.Face(k), inter.Pnt(k))
            if best is not None:
                rows.append((p, n, best[0], best[1], _v(best[2])))
        out[i] = rows
    return out


def _sheet(shape, name):
    faces = _faces(shape)
    hits = _thickness_hits(shape, faces)
    area = [_area(f) for f in faces]
    votes = Counter()
    for i, rows in hits.items():
        if _Surf(faces[i]).type != GeomAbs_Plane:
            continue
        for r in rows:
            votes[round(r[2], 2)] += area[i] / max(len(rows), 1)
    if not votes:
        return None, "not sheet: no planar faces"
    t = votes.most_common(1)[0][0]
    total = sum(area)
    if t > 8:
        return None, f"not sheet: the dominant wall is {t} mm thick"
    skin = set()
    from build123d import Face as BFace
    idx = {BFace(f): i for i, f in enumerate(faces)}
    for i, rows in hits.items():
        good = 0
        for p, n, w, hf, hp in rows:
            if abs(w - t) > 0.02:
                continue
            hs = _Surf(hf)
            from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
            pr = GeomAPI_ProjectPointOnSurf(gp_Pnt(*hp), BRep_Tool.Surface_s(hf))
            if not pr.NbPoints():
                continue
            uu, vv = pr.LowerDistanceParameters()
            if _dot(hs.normal(uu, vv), n) < -0.999:
                good += 1
        if rows and good >= max(1, (len(rows) + 1) // 2):
            skin.add(i)
    if sum(area[i] for i in skin) < 0.6 * total:
        return None, f"not sheet: only {100 * sum(area[i] for i in skin) / total:.0f}% of the surface is {t} mm sheet"
    # the two skins: connected components of skin faces through shared edges
    emap = defaultdict(list)
    for i in skin:
        for e in BFace(faces[i]).edges():
            emap[e].append(i)
    adj = defaultdict(set)
    for fl in emap.values():
        for a in fl:
            for b in fl:
                if a != b:
                    adj[a].add(b)
    comps, seen = [], set()
    for i in sorted(skin):
        if i in seen:
            continue
        st, comp = [i], []
        seen.add(i)
        while st:
            j = st.pop()
            comp.append(j)
            for k in adj[j]:
                if k not in seen:
                    seen.add(k)
                    st.append(k)
        comps.append(comp)
    comps.sort(key=lambda c: -sum(area[i] for i in c))
    # a component's faces are on one skin; assign each component a side by the faces its rays hit
    side = {}
    for ci, comp in enumerate(comps):
        if ci in side:
            continue
        side[ci] = 0
        st = [ci]
        while st:
            c = st.pop()
            for i in comps[c]:
                for row in hits[i]:
                    j = idx.get(BFace(row[3]))
                    for cj, other in enumerate(comps):
                        if j in other and cj not in side:
                            side[cj] = 1 - side[c]
                            st.append(cj)
    attempts = []
    for fit_ends in (False, True):          # the bend ends fitted only after the plain candidates: they win when they match
        for want in (0, 1):
            chosen = [i for ci, comp in enumerate(comps) if side[ci] == want for i in comp]
            res = _sheet_code(shape, faces, chosen, t, name, fit_ends)
            if fit_ends and isinstance(res, str):
                continue
            if fit_ends:
                res["late"] = True             # recreate() tries it after every other recogniser (prism too)
            attempts.append(res)
    good = [a for a in attempts if not isinstance(a, str)]
    if not good:
        return None, "not sheet: " + "; ".join(attempts)
    plain = [a for a in attempts if isinstance(a, str)]
    return good, ("sheet: " + "; ".join(plain)) if plain else None


def _bend_trims(face, u0, u1):
    """A bend face whose outline in (u, v) is two straight lines across the turn between the axial lines at u0 and u1:
    ((v at u0 of one end, of the other), (the same at u1)), else None (a cut of another shape)."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve2d
    from OCP.TopAbs import TopAbs_WIRE
    wires = []
    wx = TopExp_Explorer(face, TopAbs_WIRE)
    while wx.More():
        wires.append(TopoDS.Wire(wx.Current()))
        wx.Next()
    if len(wires) != 1:
        return None
    ex = BRepTools_WireExplorer(wires[0], face)
    at0, at1, cross = [], [], []
    while ex.More():
        c = BRepAdaptor_Curve2d(ex.Current(), face)
        a, b = c.FirstParameter(), c.LastParameter()
        pts = [c.Value(a + (b - a) * k / 6) for k in range(7)]
        uv = [(p.X(), p.Y()) for p in pts]
        if all(abs(q[0] - u0) < 1e-7 for q in uv):
            at0 += [q[1] for q in uv]
        elif all(abs(q[0] - u1) < 1e-7 for q in uv):
            at1 += [q[1] for q in uv]
        else:
            (ua, va), (ub, vb) = uv[0], uv[-1]
            if abs(ub - ua) < 1e-9:
                return None
            for uq, vq in uv:                    # straight in (u, v): a helix on the cylinder
                if abs(va + (vb - va) * (uq - ua) / (ub - ua) - vq) > 1e-4:
                    return None
            cross.append(((ua, va), (ub, vb)))
        ex.Next()
    if len(cross) != 2 or not at0 or not at1:
        return None
    return (min(at0), max(at0)), (min(at1), max(at1))


def _end_curves(face, loc, zd, xs, ang):
    """The two cut ends of a bend face in the bend's frame (theta about zd from xs, w along zd from loc): [(theta, w) arrays
    sorted by theta, of the lower end, of the upper], from its edges that are not axial lines (an end may be several edges);
    None when an end does not run across the whole turn as one w per theta."""
    import numpy as np
    from OCP.TopAbs import TopAbs_WIRE
    yd = _cross(zd, xs)
    wires = []
    wx = TopExp_Explorer(face, TopAbs_WIRE)
    while wx.More():
        wires.append(TopoDS.Wire(wx.Current()))
        wx.Next()
    if len(wires) != 1:
        return None
    ex = BRepTools_WireExplorer(wires[0], face)
    pieces = []
    while ex.More():
        c = BRepAdaptor_Curve(ex.Current())
        a, b = c.FirstParameter(), c.LastParameter()
        pts = [_v(c.Value(a + (b - a) * k / 128)) for k in range(129)]
        ex.Next()
        th, w = [], []
        for p in pts:
            d = _sub(p, loc)
            wv = _dot(d, zd)
            rad = _sub(d, tuple(wv * q for q in zd))
            th.append(math.atan2(_dot(rad, yd), _dot(rad, xs)))
            w.append(wv)
        th, w = np.array(th), np.array(w)
        th = np.where(th < -1e-6, th + 2 * math.pi, th)
        if np.ptp(th) > 1e-7:                          # an axial line keeps one theta
            pieces.append((th, w))
    if not pieces:
        return None
    mid = (min(p[1].min() for p in pieces) + max(p[1].max() for p in pieces)) / 2
    ends = []
    for lower in (True, False):
        grp = [p for p in pieces if (p[1].mean() < mid) == lower]
        if not grp:
            return None
        th = np.concatenate([p[0] for p in grp])
        w = np.concatenate([p[1] for p in grp])
        o = np.argsort(th, kind="stable")
        th, w = th[o], w[o]
        if th.min() > 1e-6 or th.max() < ang - 1e-6:
            return None
        same = np.diff(th) < 1e-5                       # where two edges meet; a jog along Z there is not one w per theta
        if np.any(same & (np.abs(np.diff(w)) > 1e-3)):
            return None
        keep = np.concatenate([[True], ~same])
        ends.append((th[keep], w[keep]))
    return ends


def _bend_ends(f, s, faces, t, loc, zd, xs, ang, convex):
    """The cut ends of a bend whose ends are not helices, as recreated.bend takes them: per end (a, b, c) on the inner radius
    then on the outer one when w(theta) = a + b cos(theta) + c sin(theta) holds within 0.1 um (a plane cut, or SolidWorks'
    radial cut between slanted panel ends), else ("pts", w on the inner radius, w on the outer) at every 5 degrees (a
    closed corner's free-form cut). The other skin's coaxial face gives its radius; when it cannot be read the cut is taken
    as radial (the same at both radii). None if this face's ends cannot be read."""
    import numpy as np
    mine = _end_curves(f, loc, zd, xs, ang)
    if mine is None:
        return None
    other = None
    want_r = s.radius - t if convex else s.radius + t
    for g in faces:
        if g is f:
            continue
        q = _Surf(g)
        if q.type != GeomAbs_Cylinder or abs(q.radius - want_r) > 0.02 or abs(abs(_dot(q.dir, s.dir)) - 1) > 1e-9:
            continue
        off = _sub(q.loc, s.loc)
        if _norm_len(_sub(off, tuple(_dot(off, s.dir) * c for c in s.dir))) > 1e-4:
            continue
        cv = _end_curves(g, loc, zd, xs, ang)
        if cv and all(abs(cv[i][1][0] - mine[i][1][0]) < 2 * t + 1 for i in (0, 1)):
            other = cv
            break
    other = other or mine
    inner, outer = (other, mine) if convex else (mine, other)
    fits = []
    for th, w in (inner[0], outer[0], inner[1], outer[1]):
        A = np.stack([np.ones_like(th), np.cos(th), np.sin(th)], 1)
        k = np.linalg.lstsq(A, w, rcond=None)[0]
        fits.append(tuple(float(x) for x in k) if np.abs(A @ k - w).max() <= 1e-4 else None)
    if all(fits):
        return fits[0] + fits[1], fits[2] + fits[3]
    n = max(2, math.ceil(math.degrees(ang) / 5))
    at = np.linspace(0.0, ang, n + 1)
    return tuple(("pts", [float(x) for x in np.interp(at, *inner[i])], [float(x) for x in np.interp(at, *outer[i])])
                 for i in (0, 1))


def _sheet_code(shape, faces, chosen, t, name, fit_ends=False):
    """fit_ends: bends whose cut ends are not helices get `ends=` (_bend_ends) instead of being drawn square; a string if no
    bend has such ends (the candidate would be the same as without)."""
    pieces, radii = [], Counter()
    skipped = 0.0
    fitted = 0
    for i in chosen:
        f = faces[i]
        s = _Surf(f)
        if s.type == GeomAbs_Plane:
            u0, u1, v0, v1 = BRepTools.UVBounds_s(f)
            n = s.normal((u0 + u1) / 2, (v0 + v1) / 2)
            origin, x, z = _plane_frame(n, s.loc)
            fr, outer, holes, cuts = _flat_args(f, origin, x, z)
            pieces.append(("panel", f"panel({fr}, thickness,\n          {outer},\n          {holes}, {cuts})"))
        elif s.type == GeomAbs_Cylinder:
            u0, u1, v0, v1 = BRepTools.UVBounds_s(f)
            r = s.radius
            ang = u1 - u0
            length = v1 - v0
            sg = 1.0 if s.direct else -1.0              # the frame turns about z = sg * axis; w = sg * v along it
            pa = _v(s.adaptor.Value(u0, v0))
            foot = _add(s.loc, s.dir, _dot(_sub(pa, s.loc), s.dir))
            xs = _norm(_sub(pa, foot))
            n = s.normal((u0 + u1) / 2, (v0 + v1) / 2)
            pm = _v(s.adaptor.Value((u0 + u1) / 2, (v0 + v1) / 2))
            footm = _add(s.loc, s.dir, _dot(_sub(pm, s.loc), s.dir))
            convex = _dot(n, _sub(pm, footm)) > 0
            tr = _bend_trims(f, u0, u1)
            ends = None
            if fit_ends and tr is None:
                ends = _bend_ends(f, s, faces, t, s.loc, tuple(sg * c for c in s.dir), xs, ang, convex)
            if ends:
                fitted += 1
                e0 = ends[0]                            # this skin's start at theta 0 is the frame's origin
                w0 = (e0[2 if convex else 1][0] if e0[0] == "pts" else e0[3] + e0[4] if convex else e0[0] + e0[1])
                tr = ((v0, v1), (v0, v1))
            elif tr is None:
                if _area(f) < 0.9 * r * ang * length:  # trimmed a little (a relief) is drawn whole: compare() judges it
                    return f"bend of r {r:.3f} is cut across ({100 * _area(f) / (r * ang * length):.0f}% of its sector)"
                tr = ((v0, v1), (v0, v1))
            (pa_, qa_), (pb_, qb_) = tr
            if not ends:
                w0, w1 = sorted((sg * pa_, sg * qa_))
                w0b, w1b = sorted((sg * pb_, sg * qb_))
                length = w1 - w0
                trim = (_r(w0b - w0), _r(w1 - w1b))
            else:
                trim = (0, 0)
                ends = tuple(("pts", [_r(c - w0) for c in e[1]], [_r(c - w0) for c in e[2]]) if e[0] == "pts" else
                             tuple(_r(c - (w0 if j % 3 == 0 else 0)) for j, c in enumerate(e)) for e in ends)
            zd = _snap_dir(tuple(sg * c for c in s.dir))
            org = _add(s.loc, s.dir, sg * w0)
            org = _add(org, xs, 0.0)
            radii[_r(r - t if convex else r, 3)] += 1
            pieces.append(("bend", (org, xs, zd, r, ang, length, convex, trim, ends)))
        else:
            skipped += _area(f)
    if skipped > 0.02 * sum(_area(faces[i]) for i in chosen):
        return "a skin face is neither flat nor a plain bend"
    if fit_ends and not fitted:
        return "no bend end to fit"
    br = radii.most_common(1)[0][0] if radii else None
    params = {"thickness": _r(t, 3)}
    if br is not None:
        params["bend_radius"] = br
    body = []
    for kind, a in pieces:
        if kind == "panel":
            body.append(a)
            continue
        org, xs, zd, r, ang, length, convex, trim, ends = a
        inner = _r(r - t if convex else r, 3)
        rexpr = ("bend_radius" if inner == br else _fmt(inner, 3)) + (" + thickness" if convex else "")
        tr = f", trim=({_fmt(trim[0])}, {_fmt(trim[1])})" if trim[0] or trim[1] else ""
        if ends:
            tr = ", ends=(" + ", ".join(
                f'("pts", [{", ".join(_fmt(c) for c in e[1])}], [{", ".join(_fmt(c) for c in e[2])}])' if e[0] == "pts" else
                "(" + ", ".join(_fmt(c) for c in e) + ")" for e in ends) + ")"
        body.append(f"bend(frame({_fmtv(org)}, {_fmtv(xs, 9)}, {_fmtv(zd, 9)}), {rexpr}, thickness, "
                    f"{_fmt(math.degrees(ang), 6)}, {_fmt(length)}, convex={convex}{tr})")
    sig = ", ".join(f"{k}={_fmt(v, 3)}" for k, v in params.items())
    code = (f'"""{name}: sheet part, {sum(1 for k, _ in pieces if k == "panel")} panels and '
            f'{sum(1 for k, _ in pieces if k == "bend")} bends (recreated from the client\'s CAD).\n'
            "Each panel is its outline on the outer surface (frame: origin, x, z out of the part) extruded inwards by the thickness;\n"
            'outlines are in mm in the panel\'s frame, holes are (u, v, diameter)."""\n'
            "from recreated import bend, frame, panel, sheet\n\n\n"
            f"def build({sig}):\n"
            "    return sheet([\n" + "".join("        " + b.replace("\n", "\n    ") + ",\n" for b in body) + "    ])\n")
    return {"kind": "sheet", "code": selfcontained(code), "params": params}


# ---------------------------------------------------------------- prism (2.5D)
def _prism(shape, name):
    faces = _faces(shape)
    dirs = Counter()
    for f in faces:
        s = _Surf(f)
        if s.type == GeomAbs_Plane:
            d = _snap_dir(s.dir if max(s.dir, key=abs) > 0 else tuple(-c for c in s.dir))
            dirs[tuple(round(c, 6) for c in d)] += _area(f)
    out = []
    for d, _ in dirs.most_common(3):
        res = _prism_along(shape, faces, d, name)
        if isinstance(res, str):
            out.append(res)
            continue
        return [res], None
    return None, "not prismatic: " + "; ".join(out)


def _prism_along(shape, faces, d, name):
    levels = set()
    for f in faces:
        s = _Surf(f)
        if s.type == GeomAbs_Plane and abs(abs(_dot(s.dir, d)) - 1) < 1e-7:
            levels.add(round(_dot(s.loc, d), 6))
    b = _bbox(shape)
    corners = [(b[i], b[j], b[k]) for i in (0, 3) for j in (1, 4) for k in (2, 5)]
    lo, hi = min(_dot(c, d) for c in corners), max(_dot(c, d) for c in corners)
    levels = sorted(z for z in levels if lo - 1e-6 <= z <= hi + 1e-6)
    if len(levels) < 2:
        return f"along {d}: fewer than two levels"
    if len(levels) > 40:
        return f"along {d}: {len(levels)} levels"
    origin, x, z = _plane_frame(d, (0, 0, 0))
    big = 4 * max(b[3] - b[0], b[4] - b[1], b[5] - b[2]) + 10
    layers = []
    for z0, z1 in zip(levels, levels[1:]):
        zm = (z0 + z1) / 2
        pln = gp_Pln(gp_Pnt(*tuple(c * zm for c in z)), gp_Dir(*z))
        sec = _faces(BRepAlgoAPI_Common(shape, BRepBuilderAPI_MakeFace(pln, -big, big, -big, big).Face()).Shape())
        for f in sec:
            o2 = tuple(c * zm for c in z)
            outer, holes, cuts = _flat_code(f, o2, x, z)
            layers.append([z0, z1, outer, holes, cuts])
    merged = []
    for L in layers:                                  # one slab where consecutive layers have the same outline
        prev = next((m for m in reversed(merged) if m[1] == L[0] and m[2:] == L[2:]), None)
        if prev is not None:
            prev[1] = L[1]
        else:
            merged.append(L)
    rows = []
    for z0, z1, outer, holes, cuts in merged:
        h = "[" + ", ".join(f"({_fmt(u)}, {_fmt(v)}, {_fmt(dd)})" for u, v, dd in holes) + "]"
        c = "[" + ", ".join(_fmt_steps(s_, indent="            ") for s_ in cuts) + "]"
        rows.append(f"({_fmt(z0)}, {_fmt(z1)},\n         {_fmt_steps(outer, indent='            ')},\n         {h}, {c})")
    code = (f'"""{name}: prismatic, {len(merged)} slabs along {tuple(_r(c, 6) for c in z)} (recreated from the client\'s CAD).\n'
            'Each slab: (from, to along the axis, outline, holes (u, v, diameter), other cut-outs), mm in the frame."""\n'
            "from recreated import frame, slabs\n\n\n"
            "def build():\n"
            f"    return slabs(frame({_fmtv(origin)}, {_fmtv(x, 9)}, {_fmtv(z, 9)}), [\n"
            + "".join("        " + r_ + ",\n" for r_ in rows) + "    ])\n")
    return {"kind": "prism", "code": selfcontained(code), "params": {}}


# ---------------------------------------------------------------- self-contained code
BLOCK = "# --- recreated.py (framework template): {} ---"
BLOCK_END = "# --- end recreated.py ---"


def _helpers():
    """The template's source without its docstring: what a recreated module carries in place of `from recreated import ...`."""
    src = open(os.path.join(TEMPLATES, "recreated.py"), encoding="utf-8").read()
    end = src.index('"""', 3) + 3
    return src[end:].strip("\n")


def selfcontained(code):
    """`from recreated import a, b` -> the template's code between BLOCK markers: the module then needs build123d only.
    step_to_project.py turns the block back into the import (`imports_helpers`), so a project keeps one copy."""
    lines = code.split("\n")
    for i, ln in enumerate(lines):
        if ln.startswith("from recreated import "):
            names = ln[len("from recreated import "):]
            lines[i] = BLOCK.format(names) + "\n" + _helpers() + "\n" + BLOCK_END
            break
    return "\n".join(lines)


def imports_helpers(code):
    """The reverse of selfcontained(): the BLOCK back into `from recreated import a, b`."""
    out, skip = [], False
    for ln in code.split("\n"):
        m = ln.startswith("# --- recreated.py (framework template): ")
        if m:
            out.append("from recreated import " + ln[len("# --- recreated.py (framework template): "):-4])
            skip = True
            continue
        if skip:
            if ln == BLOCK_END:
                skip = False
            continue
        out.append(ln)
    return "\n".join(out)


# ---------------------------------------------------------------- driver
def run_code(code, **params):
    """Execute a recreated module's text and return build(**params) (a build123d shape)."""
    if TEMPLATES not in sys.path:
        sys.path.insert(0, TEMPLATES)
    ns = {"__name__": "recreated_part"}
    exec(compile(code, "<recreated>", "exec"), ns)          # noqa: S102  our own generated text
    return ns["build"](**params)


def recreate(shape, name):
    """See the module doc. Each candidate's code is built and compared with `shape`; the first that matches wins."""
    name = str(name).replace("\\", "/").replace('"""', "'''")   # it goes into the generated module's docstring
    shape_w = _wrapped(shape)
    why, late = [], []

    def try_(cand):
        try:
            got = run_code(cand["code"])
            cmp = compare(shape_w, got)
        except Exception as e:                       # noqa: BLE001
            why.append(f"{cand['kind']}: its code does not build ({type(e).__name__}: {e})")
            if os.environ.get("RECREATE_DEBUG"):
                traceback.print_exc()
            return None
        if cmp["ok"]:
            return dict(cand, reason="", compare=cmp)
        why.append(f"{cand['kind']}: off by volume {100 * cmp['volume_rel']:.2f}%, bbox {cmp['bbox_mm']:.3f} mm, "
                   f"surface up to {cmp['deviation_mm']:.3f} mm, symmetric difference {100 * cmp['sym_diff_rel']:.2f}%, "
                   f"holes {cmp['holes']['got']}/{cmp['holes']['ref']}")
        return None

    for fn in (_turned, _sheet, _prism):
        try:
            res, reason = fn(shape_w, name)
        except Exception as e:                       # noqa: BLE001  a recogniser that trips is a reason, not a crash
            res, reason = None, f"{fn.__name__[1:]}: {type(e).__name__}: {e}"
            if os.environ.get("RECREATE_DEBUG"):
                traceback.print_exc()
        if res is None or reason:
            why.append(reason)
        if res is None:
            continue
        for cand in (res if isinstance(res, list) else [res]):
            if cand.pop("late", False):              # bend ends fitted: after every other recogniser, so a part any of
                late.append(cand)                    # them matched before the fitting existed keeps that match
                continue
            hit = try_(cand)
            if hit:
                return hit
    for cand in late:
        hit = try_(cand)
        if hit:
            return hit
    return {"kind": None, "code": "", "params": {}, "reason": "; ".join(why)}


if __name__ == "__main__":                            # python recreate.py part.step [...]: what each part would become
    from build123d import import_step
    for f in sys.argv[1:]:
        r = recreate(import_step(f), os.path.basename(f))
        print(f, r["kind"], r.get("params"), r["reason"][:300])
