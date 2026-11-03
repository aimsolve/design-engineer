"""Shapes of the parts recreated from a client's CAD (framework template, step-import `step_to_project.py`).

A part file builds its solid out of these, in its own coordinates, from numbers it keeps as arguments of its build():
  sheet part    = panels (a flat outline extruded inwards by the thickness) + bends (a sector of a tube between the panels)
  turned part   = a half-section profile (r, z) revolved about the part's axis
  prismatic part = slabs: an outline extruded between two levels along the part's axis
An outline is a list of steps in the frame's (u, v): `(u, v)` a straight line to that point, `("arc", (um, vm), (u, v))` an arc
through (um, vm) to (u, v), `("pts", [(u, v), ...])` a polyline, `("spline", [(u, v), ...])` a smooth curve through the points
(a curve the client drew as a spline, e.g. a letter: one face where a polyline makes one per segment). The first item is the
start point and the outline closes itself. A hole is `(u, v, d)`; any other cut-out is an outline of its own."""
from build123d import Axis, Edge, Face, Plane, Vector, Wire, extrude, revolve


def frame(origin, x_dir, z_dir):
    return Plane(origin=origin, x_dir=x_dir, z_dir=z_dir)


def outline(pl, steps):
    """steps (see the module doc) -> a closed Wire on plane pl."""
    at = lambda uv: pl.from_local_coords((uv[0], uv[1], 0))   # noqa: E731
    start = steps[0]
    cur, edges = start, []
    for s in list(steps[1:]) + [start]:
        if isinstance(s[0], str) and s[0] == "arc":
            (ax, ay), (bx, by), (cx, cy) = cur, s[1], s[2]
            if abs((bx - ax) * (cy - ay) - (by - ay) * (cx - ax)) > 1e-10:
                edges.append(Edge.make_three_point_arc(at(cur), at(s[1]), at(s[2])))
            elif (cx - ax) ** 2 + (cy - ay) ** 2 > 1e-12:    # an arc too small to tell from its chord at 0.1 um
                edges.append(Edge.make_line(at(cur), at(s[2])))
            cur = s[2]
        elif isinstance(s[0], str) and s[0] == "pts":
            for q in s[1]:
                if (q[0] - cur[0]) ** 2 + (q[1] - cur[1]) ** 2 > 1e-12:
                    edges.append(Edge.make_line(at(cur), at(q)))
                cur = q
        elif isinstance(s[0], str) and s[0] == "spline":
            pts = [cur] + [q for q in s[1] if (q[0] - cur[0]) ** 2 + (q[1] - cur[1]) ** 2 > 1e-12]
            edges.append(Edge.make_spline([at(q) for q in pts]) if len(pts) > 2 else Edge.make_line(at(pts[0]), at(pts[-1])))
            cur = pts[-1]
        else:
            if (s[0] - cur[0]) ** 2 + (s[1] - cur[1]) ** 2 > 1e-12:
                edges.append(Edge.make_line(at(cur), at(s)))
            cur = s
    return Wire(edges)


def circle(pl, u, v, d):
    return Wire([Edge.make_circle(d / 2, Plane(origin=pl.from_local_coords((u, v, 0)), x_dir=pl.x_dir, z_dir=pl.z_dir))])


def flat(pl, outer, holes=(), cuts=()):
    """The planar face: outer outline minus round holes (u, v, d) and other cut-outs (outlines)."""
    inner = [circle(pl, *h) for h in holes] + [outline(pl, c) for c in cuts]
    return Face(outline(pl, outer), inner)


def panel(pl, t, outer, holes=(), cuts=()):
    """A flat piece of sheet: the outline on its outer surface (plane pl, Z out of the part) extruded inwards by t."""
    return extrude(flat(pl, outer, holes, cuts), amount=t, dir=-pl.z_dir)


def bend(pl, r, t, angle, length, convex=True, trim=(0, 0), ends=None):
    """A bend: the tube sector about the Z axis of pl, starting at its X axis and turning `angle` degrees about +Z, `length` along
    Z. convex: the outer surface is the cylinder of radius r (the material is r - t .. r); else the inner one (r .. r + t).
    trim (s, e): the ends of a bend closed into a corner: at the far angle the bend starts s later and ends e earlier along Z,
    both straight across the turn (a sector between two helices; lofted through a section every 10 degrees).
    ends (start, end): ends cut across the turn, each (a, b, c) or (a, b, c, a', b', c'): at the turn theta that end is at
    a + b cos(theta) + c sin(theta) along Z on the inner radius and a' + b' cos(theta) + c' sin(theta) on the outer one (one
    triple: the same on both, as SolidWorks cuts a bend between two panels whose ends are slanted; two: a plane cut), or
    ("pts", [w inner], [w outer]) at evenly spaced turns from 0 to `angle` (a closed corner's free-form cut). The sector is
    revolved whole (its skins stay true cylinders, so the sheet still unfolds) and each end cut off by a cutter lofted through
    a section every 5 degrees (at the points' turns), reaching one section past each end of the turn (so a bend of
    `ends` turns well under 360 degrees, as sheet bends do); `length` and `trim` are then unused."""
    import math
    r0, r1 = (r - t, r) if convex else (r, r + t)
    x, z = pl.x_dir, pl.z_dir
    o = Vector(pl.origin)
    y = z.cross(x)

    def sector(w0, w1):
        prof = Wire([Edge.make_line(o + x * r0 + z * w0, o + x * r1 + z * w0), Edge.make_line(o + x * r1 + z * w0, o + x * r1 + z * w1),
                     Edge.make_line(o + x * r1 + z * w1, o + x * r0 + z * w1), Edge.make_line(o + x * r0 + z * w1, o + x * r0 + z * w0)])
        return revolve(Face(prof), Axis(pl.origin, pl.z_dir), angle)

    if ends:
        from build123d import Solid
        pts = [e for e in ends if e[0] == "pts"]
        n = len(pts[0][1]) - 1 if pts else max(2, math.ceil(abs(angle) / 5))
        if any(len(e[1]) != n + 1 or len(e[2]) != n + 1 for e in pts):
            raise ValueError(f"bend ends: every ('pts', inner, outer) needs {n + 1} values on both radii")

        def at(e, k):
            """(w inner, w outer) of end e at section k (-1 and n + 1: one step past the turn, extrapolated)."""
            if e[0] == "pts":
                j = min(max(k, 0), n)
                if j != k:
                    i = 1 if k < 0 else n - 1
                    return tuple(2 * e[q][j] - e[q][i] for q in (1, 2))
                return e[1][k], e[2][k]
            a = math.radians(angle * k / n)
            return (e[0] + e[1] * math.cos(a) + e[2] * math.sin(a), e[-3] + e[-2] * math.cos(a) + e[-1] * math.sin(a))

        if sum(at(ends[0], k)[0] for k in range(n + 1)) > sum(at(ends[1], k)[0] for k in range(n + 1)):
            ends = ends[::-1]                           # the start is the lower end whichever way they were given
        ws = [w for e in ends for k in range(-1, n + 2) for w in at(e, k)]
        span = max(ws) - min(ws) + t + 2
        lo, hi = min(ws) - span, max(ws) + span
        body = sector(min(ws) - 1, max(ws) + 1)
        d, di = t / 2 + 0.5, min(t / 2 + 0.5, r0 / 2)  # the cutters reach past both skins (not onto the axis)
        for e, far in ((ends[0], lo), (ends[1], hi)):
            secs = []
            for k in range(-1, n + 2):
                a = math.radians(angle * k / n)
                rad = x * math.cos(a) + y * math.sin(a)
                wi, wo = at(e, k)
                sl = (wo - wi) / (r1 - r0)
                secs.append(Wire.make_polygon([o + rad * (r0 - di) + z * (wi - sl * di), o + rad * (r1 + d) + z * (wo + sl * d),
                                               o + rad * (r1 + d) + z * far, o + rad * (r0 - di) + z * far], close=True))
            body = body - Solid.make_loft(secs)
        return body.solids()[0] if len(body.solids()) == 1 else body
    if trim[0] or trim[1]:
        from build123d import Solid
        n = max(2, math.ceil(abs(angle) / 10))
        secs = []
        for k in range(n + 1):
            f = k / n
            a = math.radians(angle * f)
            rad = x * math.cos(a) + y * math.sin(a)
            w0, w1 = trim[0] * f, length - trim[1] * f
            secs.append(Wire.make_polygon([o + rad * r0 + z * w0, o + rad * r1 + z * w0, o + rad * r1 + z * w1,
                                           o + rad * r0 + z * w1], close=True))
        return Solid.make_loft(secs)
    return sector(0, length)


def sheet(pieces):
    """The union of a sheet part's panels and bends."""
    out = pieces[0]
    if len(pieces) > 1:
        out = out.fuse(*pieces[1:]).clean()
        if len(out.solids()) > 1:     # pieces that meet along a long slanted edge miss each other by microns
            out = pieces[0].fuse(*pieces[1:], tol=1e-3).clean()
    return out


def turned(pl, profile):
    """A turned part: profile = outline in (r, z) of the half-section (u = r >= 0, v = z along the axis), revolved about Z of pl."""
    face = Face(outline(Plane(origin=pl.origin, x_dir=pl.x_dir, z_dir=pl.x_dir.cross(pl.z_dir)), profile))
    return revolve(face, Axis(pl.origin, pl.z_dir), 360)


def slabs(pl, layers):
    """A prismatic part: layers [(z0, z1, outer, holes, cuts)], each its outline in pl's (u, v) extruded from z0 to z1 along Z."""
    pieces = []
    for z0, z1, outer, holes, cuts in layers:
        lp = Plane(origin=pl.from_local_coords((0, 0, z0)), x_dir=pl.x_dir, z_dir=pl.z_dir)
        pieces.append(extrude(flat(lp, outer, holes, cuts), amount=z1 - z0))
    return sheet(pieces)
