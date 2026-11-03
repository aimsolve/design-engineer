"""Mesh parts: a part whose geometry is a closed triangle mesh - a smooth housing, a grip - with no B-rep.

build123d is weak at smooth shells (blends, dimples, grips: OCC fillets and offsets fail on them). The way the framework makes
one: the skin is a signed distance field meshed by manifold3d, every fit feature (a screen window, button holes, screw bosses)
is an exact build123d solid, and manifold3d combines them with exact mesh booleans. The part file returns the result as
`Mesh`, an empty build123d Compound carrying the triangles, so it gets joints, docking and place() like any part:

    import meshpart as mp                                     # not `as M`: build123d's `*` defines M (a unit)
    from build123d import Cylinder, Location, RigidJoint
    from geom import P

    def build():
        skin = mp.sdf(shell_f, bounds, edge=0.5)              # shell_f(x, y, z): signed distance, negative inside, mm
        inner = mp.sdf(shell_f, bounds, edge=0.5, level=-2.0) # the same field 2 mm in: a uniform wall
        bosses = mp.solid(boss_solids) ^ skin                 # exact build123d solids, clipped to the skin
        body = (skin - inner) + bosses - mp.solid(cutters)
        shell = mp.Mesh(body)
        RigidJoint("boss_1", shell, Location((22, 40, 5), (180, 0, 0)))
        return P("shell", shell, "#d9d4ca", process="print", material="PETG")

The model keeps its triangles (build/model/mesh/<key>.json, the part's own coordinates; parts.json `mesh` + `mesh_solid`);
print, viewer, assembly drawings, clearance and the fit checks read them. The model STEP leaves it out (there is none).

The rest of this module is what the framework's scripts use to read such a part: `read`, `Placed` (a placed mesh as the fit
checks and the clearance check handle a solid), `common`, `axis_holes`, `lay_flat`, `stl_bytes`.
"""
import contextlib
import json
import math
import sys

import numpy as np
from build123d import Compound

SIMPLIFY = 0.005    # mm: Mesh() merges triangles that stay within this of the surface (an SDF skin has ~10x more than it needs)
FLAT_SHARE = 0.01   # lay_flat: a flat face is one of at least this share of the area; none: the part prints as modelled
SOLID_TOL = 0.02    # mm: a STEP part tessellated to meet a mesh part in a boolean (chords lie inside a convex solid: no false clash)


class Mesh(Compound):
    """A mesh part's shape: no B-rep (OCC sees an empty compound, volume 0), its triangles in `mesh_vertices` (n x 3 float, mm)
    and `mesh_triangles` (m x 3 int), in the shape's own coordinates; its `location` moves them, as it moves a B-rep. Joints,
    location, color and bounding_box() work as on any build123d shape."""

    def __init__(self, body=None, vertices=None, triangles=None, simplify=SIMPLIFY):
        super().__init__([])
        if body is not None:
            if simplify:
                body = body.simplify(simplify)
            mesh = body.to_mesh()
            vertices, triangles = np.asarray(mesh.vert_properties)[:, :3], np.asarray(mesh.tri_verts)
        self.mesh_vertices = np.asarray(vertices, dtype=np.float64).reshape(-1, 3)
        self.mesh_triangles = np.asarray(triangles, dtype=np.int64).reshape(-1, 3)

    def manifold(self):
        return manifold_of(self.mesh_vertices, self.mesh_triangles)

    def bounding_box(self, *a, **k):
        """The box of the triangles where the shape's location puts them (OCC would give the empty compound's: nothing)."""
        from build123d import BoundBox
        from OCP.Bnd import Bnd_Box
        v = moved(self.mesh_vertices, self.location)
        b = Bnd_Box()
        b.Update(*map(float, v.min(0)), *map(float, v.max(0)))
        return BoundBox(b)

    def problem(self):
        """None when the triangles close a solid, else what is wrong with them (the model build refuses such a part)."""
        if not len(self.mesh_triangles):
            return "it has no triangles"
        if self.mesh_triangles.min() < 0 or self.mesh_triangles.max() >= len(self.mesh_vertices):
            return "a triangle indexes a vertex it does not have"
        m = self.manifold()
        if m.status().name != "NoError" or m.is_empty():
            return f"its triangles do not close a solid ({m.status().name})"
        if m.volume() <= 0:
            return "its triangles face inwards (negative volume)"
        return None


def sdf(f, bounds, edge=0.5, level=0.0):
    """Closed mesh of {f(x, y, z) < level} over the box `bounds` (x0, y0, z0, x1, y1, z1), f = signed distance in mm, negative
    inside. `edge` is the triangle size: 0.5 takes ~5 s for a 60 x 100 x 15 mm shell; leave a mm of margin in `bounds`."""
    import manifold3d as mf
    g, fns = _plain_copy(f)
    with _untraced(fns):
        return mf.Manifold.level_set(lambda x, y, z: level - g(x, y, z), [float(b) for b in bounds], float(edge))


def _plain_copy(f):
    """A copy of the closure `f` (and of the closures it calls: a ztop() helper inside the field) whose numbers are plain float /
    int, and the list of those copies. In a model build a params value is a tracing subclass of float (cad_incremental marks what
    was computed from it) and counts as read where it is used; f runs a million times, and its arithmetic through that subclass
    made a 10 s shell take 100 s. So each such number is counted as used here, once, and the copy gets it plain; the caller's
    own closures are left as they were."""
    import types
    ci = sys.modules.get("cad_incremental")
    rec = getattr(ci, "REC", None) if ci is not None else None
    done, fns = {}, []

    def copy(fn):
        if id(fn) in done:
            return done[id(fn)]
        if not isinstance(fn, types.FunctionType) or not fn.__closure__:
            done[id(fn)] = fn
            fns.append(fn)
            return fn
        cells = []
        for c in fn.__closure__:
            try:
                v = c.cell_contents
            except ValueError:                             # an empty cell (a name assigned later): kept as it is
                cells.append(c)
                continue
            if isinstance(v, types.FunctionType):
                v = copy(v)
            else:
                p = _plain(v)
                if p is not v and rec is not None and rec.on:
                    rec.use(ci.deps(v))                    # the params it came from are inputs of this part
                v = p
            cells.append(types.CellType(v))
        g = types.FunctionType(fn.__code__, fn.__globals__, fn.__name__, fn.__defaults__, tuple(cells))
        g.__kwdefaults__ = fn.__kwdefaults__
        done[id(fn)] = g
        fns.append(g)
        return g
    return copy(f), fns


def _plain(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, float) and type(v) is not float:
        return float.__float__(v)
    if isinstance(v, int) and type(v) is not int:
        return int.__int__(v)
    if type(v) is tuple:
        p = tuple(_plain(x) for x in v)
        return p if any(a is not b for a, b in zip(p, v)) else v
    return v


@contextlib.contextmanager
def _untraced(fns):
    """math.* and a project module's max / min are tracing wrappers during a model build; the field's plain numbers need none
    of that, so for the million calls of level_set the originals stand in (restored after, whatever happens)."""
    saved = []
    for n in dir(math):
        w = getattr(math, n)
        if getattr(w, "__cad_taint__", False) and getattr(w, "__wrapped__", None) is not None:
            saved.append((math.__dict__, n, w))
    for g in fns:
        gl = getattr(g, "__globals__", None) or {}
        for n in ("max", "min"):
            w = gl.get(n)
            if getattr(w, "__cad_taint__", False) and getattr(w, "__wrapped__", None) is not None:
                saved.append((gl, n, w))
    try:
        for d, n, w in saved:
            d[n] = w.__wrapped__
        yield
    finally:
        for d, n, w in saved:
            d[n] = w


def solid(shape, tol=0.01, angle=0.1):
    """A build123d solid as a manifold3d Manifold, tessellated within `tol` mm (a fit feature keeps its size to that)."""
    verts, tris = shape.tessellate(tol, angle)
    if not tris:
        raise ValueError("meshpart.solid: the shape has no faces to mesh")
    v = np.array([[p.X, p.Y, p.Z] for p in verts], dtype=np.float64)
    v, inv = np.unique(np.round(v, 6), axis=0, return_inverse=True)   # build123d gives each face its own vertices: weld them
    t = inv.reshape(-1)[np.asarray(tris, dtype=np.int64)]
    t = t[(t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])]
    m = manifold_of(v, t)
    if m.status().name != "NoError":
        raise ValueError(f"meshpart.solid: the tessellation does not close a solid ({m.status().name})")
    return m if m.volume() >= 0 else manifold_of(v, t[:, ::-1])


def manifold_of(vertices, triangles):
    import manifold3d as mf
    return mf.Manifold(mf.Mesh(vert_properties=np.ascontiguousarray(vertices, dtype=np.float32),
                               tri_verts=np.ascontiguousarray(triangles, dtype=np.uint32)))


def triangles_of(m):
    """(vertices n x 3 float64, triangles m x 3 int64) of a Manifold."""
    mesh = m.to_mesh()
    return np.asarray(mesh.vert_properties, dtype=np.float64)[:, :3], np.asarray(mesh.tri_verts, dtype=np.int64)


# ---------------------------------------------------------------- the stored part

def read(path):
    """(vertices, triangles) of a stored mesh file {"v": [x, y, z, ...], "f": [i, j, k, ...]}."""
    with open(path, encoding="utf-8") as fh:
        m = json.load(fh)
    return np.array(m["v"], dtype=np.float64).reshape(-1, 3), np.array(m["f"], dtype=np.int64).reshape(-1, 3)


def matrix(m):
    """A 4x4 row-major placement (16 numbers), a build123d Location, or None (identity) as a 4x4 array."""
    if m is None:
        return np.eye(4)
    if hasattr(m, "wrapped"):
        t = m.wrapped.Transformation()
        return np.array([[t.Value(r, c) for c in (1, 2, 3, 4)] for r in (1, 2, 3)] + [[0.0, 0, 0, 1]])
    return np.asarray([float(x) for x in m], dtype=np.float64).reshape(4, 4)


def moved(v, m):
    m = matrix(m)
    return v @ m[:3, :3].T + m[:3, 3]


class _Box:
    def __init__(self, lo, hi):
        from types import SimpleNamespace as N
        self.min, self.max = N(X=float(lo[0]), Y=float(lo[1]), Z=float(lo[2])), N(X=float(hi[0]), Y=float(hi[1]), Z=float(hi[2]))


class Placed:
    """A mesh part as it stands, for the checks that boolean solids (fit_checks, clearance): bounding_box(), solids(),
    moved(loc), volume, distance_to(other) and `&` against another Placed or a build123d solid, the way they use a STEP."""

    def __init__(self, m):
        self.m = m

    @classmethod
    def of(cls, vertices, triangles, loc=None):
        return cls(manifold_of(moved(vertices, loc), triangles))

    def bounding_box(self):
        b = self.m.bounding_box()
        return _Box(b[:3], b[3:])

    def solids(self):
        return [self]

    def moved(self, loc):
        m = matrix(loc)
        return Placed(self.m.transform(m[:3, :]))

    @property
    def volume(self):
        return float(self.m.volume())

    def points(self):
        return triangles_of(self.m)[0]

    def distance_to(self, other, reach=1000.0):
        return float(self.m.min_gap(as_manifold(other), reach))

    def __and__(self, other):
        return common(self, other)

    def __rand__(self, other):
        return common(other, self)


def as_manifold(x):
    return x.m if isinstance(x, Placed) else solid(x, SOLID_TOL)


def common(a, b):
    """What two solids share, as a Placed (`.volume`, `.points()`); either may be a Placed mesh or a build123d solid."""
    return Placed(as_manifold(a) ^ as_manifold(b))


# ---------------------------------------------------------------- holes along an axis (fit_checks' `axis_holes` for a mesh)

def axis_holes(placed, origin, into, reach=15.0, step=0.1):
    """Every round hole of a mesh coaxial with the ray (origin, into), as [(diameter, t_start, t_end)] sorted by t_start, t along
    `into` - what fit_checks.axis_holes reads off the cylinder faces of a STEP, here read off cross sections: the mesh is cut
    across the axis every `step` mm; where the axis runs through a void enclosed by material whose boundary is a circle about
    the axis, that circle's diameter is the hole's there. A counterbore gives one hole per diameter, a countersink a run of
    widening ones; neighbours of one diameter are one hole, its ends bisected to 0.01 mm."""
    z = np.asarray(_unit(into), dtype=np.float64)
    a = np.cross(z, [1.0, 0, 0] if abs(z[0]) < 0.9 else [0, 1.0, 0])
    a /= np.linalg.norm(a)
    R = np.array([a, np.cross(z, a), z])                   # rows: the axis frame; R @ (p - origin) = p in it
    T = np.hstack([R, (-R @ np.asarray(origin, dtype=np.float64))[:, None]])
    m = as_manifold(placed).transform(T)
    import manifold3d as mf
    lo, hi = m.bounding_box()[2], m.bounding_box()[5]
    if not hi > lo:
        return []
    m = m ^ mf.Manifold.cube([2 * reach, 2 * reach, hi - lo + 2], True).translate([0, 0, (lo + hi) / 2])

    def at(t):
        return _hole_at(m, t)
    ts = np.arange(lo + step / 2, hi, step)
    ds = [at(t) for t in ts]
    runs, i = [], 0                                        # [first, last] slice index of one diameter
    while i < len(ts):
        if ds[i] is None:
            i += 1
            continue
        j = i
        while j + 1 < len(ts) and ds[j + 1] is not None and abs(ds[j + 1] - ds[i]) <= 0.02:
            j += 1
        runs.append([i, j])
        i = j + 1
    out, k = [], 0
    while k < len(runs):
        i, j = runs[k]
        if i == j:                                         # a slice of its own: a cone (a countersink) goes one diameter per
            e = k                                          # slice; its run of slices is one hole at its widest, as STEP says
            while e + 1 < len(runs) and runs[e + 1][0] == runs[e][1] + 1 and runs[e + 1][0] == runs[e + 1][1]:
                e += 1
            if e > k:
                out.append((round(max(ds[runs[q][0]] for q in range(k, e + 1)), 4),
                            round(float(ts[i] - step), 4), round(float(ts[runs[e][1]] + step), 4)))
                k = e + 1
                continue
        d = float(np.median(ds[i:j + 1]))
        same = lambda t, d=d: (lambda x: x is not None and abs(x - d) <= 0.02)(at(t))   # noqa: E731
        t0 = _edge(same, ts[i], ts[i - 1] if i > 0 else lo)
        t1 = _edge(same, ts[j], ts[j + 1] if j + 1 < len(ts) else hi)
        out.append((round(d, 4), round(float(t0), 4), round(float(t1), 4)))
        k += 1
    out.sort(key=lambda h: (h[1], h[0]))
    for a, b in zip(out, out[1:]):                         # holes that meet (a counterbore over its shaft hole) are read as
        if 0 < b[1] - a[2] < step:                         # touching: bisection leaves a hair between them
            out[out.index(a)] = (a[0], a[1], b[1])
    return sorted(out, key=lambda h: (h[1], h[0]))


def _edge(inside, t_in, t_out, n=7):
    """Bisect between a section that has the hole and one that has not; the boundary to (step / 2^n)."""
    for _ in range(n):
        mid = (t_in + t_out) / 2
        if inside(mid):
            t_in = mid
        else:
            t_out = mid
    return (t_in + t_out) / 2


def _hole_at(m, t, round_tol=0.05):
    """Diameter of the round hole the axis (0, 0) runs through in the section z = t, or None (material there, open air, or a
    hole that is not a circle about the axis)."""
    loops = [np.asarray(p, dtype=np.float64) for p in m.slice(float(t)).to_polygons()]
    wn, inner = 0, None
    for p in loops:
        if len(p) < 3:
            continue
        w = _winding(p)
        wn += w
        if w and (inner is None or _area(p) < _area(inner[0])):   # the loop closest around the axis bounds where it runs
            inner = (p, w)
    if wn != 0 or inner is None or inner[1] > 0:           # in material, in open air, or the innermost loop is an outline
        return None
    hole = inner[0]
    A = np.column_stack([2 * hole, np.ones(len(hole))])    # the circle through its vertices (least squares): centre, radius
    cx, cy, k = np.linalg.lstsq(A, (hole ** 2).sum(1), rcond=None)[0]
    r = np.hypot(hole[:, 0] - cx, hole[:, 1] - cy)
    if r.max() - r.min() > round_tol or math.hypot(cx, cy) > round_tol:   # not round, or off this axis (fit_checks' 0.05)
        return None
    return 2 * float(r.mean())


def _winding(p):
    """Winding number of the closed polygon p about the origin (counter-clockwise +1)."""
    ang = np.arctan2(p[:, 1], p[:, 0])
    d = np.diff(np.append(ang, ang[0]))
    d = (d + np.pi) % (2 * np.pi) - np.pi
    return int(round(d.sum() / (2 * np.pi)))


def _area(p):
    return abs(0.5 * np.sum(p[:, 0] * np.roll(p[:, 1], -1) - np.roll(p[:, 0], -1) * p[:, 1]))


def _unit(v):
    v = np.asarray(v, dtype=np.float64)
    n = np.linalg.norm(v)
    return v / n if n else np.array([0.0, 0, 1])


# ---------------------------------------------------------------- printing

def lay_flat(vertices, triangles, rot=None):
    """The mesh turned onto its print bed, as derive.py turns a STEP part: `rot` = [rx, ry, rz] degrees (or [axis, degrees]) if
    given, else its largest flat face down (coplanar triangles summed by area; none over FLAT_SHARE of the area: as modelled,
    the STEP rule); then moved so its box starts at the origin."""
    v = np.asarray(vertices, dtype=np.float64)
    t = np.asarray(triangles, dtype=np.int64)
    if rot is not None:
        from build123d import Rot
        if isinstance(rot[0], str):
            rot = [rot[1] if ax == rot[0].upper() else 0 for ax in "XYZ"]
        v = v @ matrix(Rot(*rot))[:3, :3].T                # exactly what derive.py's Rot(*rot) does to a STEP part
    else:
        n = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
        area = np.linalg.norm(n, axis=1)
        ok = area > 1e-12
        if ok.any():
            n, area, t0 = n[ok] / area[ok, None], area[ok] / 2, t[ok]
            off = np.einsum("ij,ij->i", n, v[t0[:, 0]])
            key = np.hstack([np.round(n * 1000), np.round(off * 100)[:, None]]).astype(np.int64)
            uk, inv = np.unique(key, axis=0, return_inverse=True)
            sums = np.bincount(inv.reshape(-1), weights=area)
            best = int(np.argmax(sums))
            if sums[best] >= FLAT_SHARE * area.sum():     # a real flat face; a curved skin's best "face" is one triangle
                nb = n[inv.reshape(-1) == best].mean(0)
                v = v @ _turn(nb / np.linalg.norm(nb), np.array([0.0, 0, -1])).T
    return v - v.min(0)


def _turn(a, b):
    """Rotation matrix taking unit vector a onto unit vector b."""
    c = float(np.dot(a, b))
    if c > 1 - 1e-12:
        return np.eye(3)
    if c < -1 + 1e-12:                                     # opposite: half a turn about any axis across them
        p = np.cross(a, [1.0, 0, 0] if abs(a[0]) < 0.9 else [0, 1.0, 0])
        p /= np.linalg.norm(p)
        return 2 * np.outer(p, p) - np.eye(3)
    k = np.cross(a, b)
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + K + K @ K / (1 + c)


def stl_bytes(vertices, triangles):
    """Binary STL of the triangles."""
    v = np.asarray(vertices, dtype=np.float64)
    t = np.asarray(triangles, dtype=np.int64)
    p = v[t]
    n = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    n /= np.where((ln := np.linalg.norm(n, axis=1, keepdims=True)) == 0, 1, ln)
    rec = np.zeros(len(t), dtype=[("n", "<f4", 3), ("p", "<f4", (3, 3)), ("a", "<u2")])
    rec["n"], rec["p"] = n, p
    return b"meshpart".ljust(80, b" ") + np.uint32(len(t)).tobytes() + rec.tobytes()
