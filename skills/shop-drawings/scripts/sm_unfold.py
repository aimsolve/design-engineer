"""FreeCAD SheetMetal flat pattern of one folded sheet part (STEP) -> <name>.flat.dxf + <name>.flat.json, with three checks.

Second, independent unfold next to unfold.py (which does 90 degree bends only). Run with FreeCAD's python, not the main one:
  /usr/bin/python3 sm_unfold.py --step part.step --out DIR [--k 0.33] [--face N] [--R mm] [--t mm] [--compare kit.dxf]
K is the ANSI K-factor, BA = angle * (R + K t): 0.33 steel / stainless (default), 0.40 aluminium.
Thickness and inner radius come from the geometry. A model with sharp bends (no cylinders) gets R (default t) added by filleting
the bend edges first (inner R, outer R + t), the same assumption the generator makes.
--face N = 1-based face of the shape that is unfolded (after the sharp-bend fillets; for a model with real radii = the STEP
face number); default = largest planar sheet face, then the next ones if SheetMetal fails on it.
DXF layers as in the kit: OUTLINE (outer contour), HOLES (inner wires), BEND (dashed bend lines). R12 ASCII, written here.
Checks (one line each, exit 1 on any FAIL): (a) flat area x t vs the folded volume (neutral-axis shift corrected),
(b) blank length vs sum of straight legs + sum of angle x (R + K t), (c) blank size vs the kit DXF cut layers (--compare).
"""
import argparse
import json
import math
import os
import sys

MOD = "/usr/share/freecad/Mod"
for _p in ["/usr/lib/freecad-python3/lib", "/usr/lib/freecad/Ext"] + (
        sorted(os.path.join(MOD, d) for d in os.listdir(MOD) if os.path.isdir(os.path.join(MOD, d))) if os.path.isdir(MOD) else []):
    if _p not in sys.path:
        sys.path.append(_p)


_SEL = []


class Fail(Exception):
    pass


def _import_freecad():
    global FreeCAD, Part, SM
    try:
        import FreeCAD, Part  # noqa: E401
    except ImportError as ex:
        raise Fail("FreeCAD is not importable (%s): run with /usr/bin/python3 in the worker" % ex)
    import FreeCADGui  # headless stub module without Selection; SheetMetal reads Gui.Selection.getSelection()[0] (IndexError if empty)
    if not hasattr(FreeCADGui, "Selection"):
        import types
        FreeCADGui.Selection = types.SimpleNamespace(getSelection=lambda *a, **k: _SEL)
    import SheetMetalUnfolder as SM
    SM.KFACTORSTANDARD = "ansi"  # unset => DIN, which halves K


# ------------------------------------------------------------------ geometry helpers
def is_plane(f):
    return f.Surface.__class__.__name__ == "Plane"


def is_line(e):
    return e.Curve.__class__.__name__ in ("Line", "LineSegment")


def nrm(f):
    return f.normalAt(0, 0)


def detect_t(sh):
    """thickness = the opposite-parallel planar face distance that carries the most area"""
    pl = [f for f in sh.Faces if is_plane(f)]
    acc = {}
    for i, f in enumerate(pl):
        n, c = nrm(f), f.CenterOfMass
        for g in pl[i + 1:]:
            if nrm(g).dot(n) < -0.999:
                d = abs((g.CenterOfMass - c).dot(n))
                if d > 0.05:
                    acc[round(d, 2)] = acc.get(round(d, 2), 0.0) + min(f.Area, g.Area)
    if not acc:
        raise Fail("no pair of parallel planar faces: not a sheet part")
    return max(acc, key=acc.get)


def sheet_flags(sh, t):
    """per face: a planar face with an opposite parallel planar face t away (a sheet face, not a rim)"""
    fl = []
    for f in sh.Faces:
        ok = False
        if is_plane(f):
            n, c = nrm(f), f.CenterOfMass
            ok = any(g is not f and is_plane(g) and nrm(g).dot(n) < -0.999 and abs(abs((g.CenterOfMass - c).dot(n)) - t) < 0.02
                     for g in sh.Faces)
        fl.append(ok)
    return fl


def bend_cylinders(sh, t):
    """bends of a folded sheet: coaxial cylinder pairs R / R + t -> dict(R, angle rad, width, axis)"""
    cyl = [f for f in sh.Faces if f.Surface.__class__.__name__ == "Cylinder"]
    out = []
    for f in cyl:
        s1 = f.Surface
        u0, u1, v0, v1 = f.ParameterRange
        if u1 - u0 > 2 * math.pi - 0.01:
            continue  # full-circle: a hole
        for g in cyl:
            s2 = g.Surface
            if abs(s2.Radius - s1.Radius - t) > 0.02 or abs(s1.Axis.dot(s2.Axis)) < 0.999:
                continue
            dv = s2.Center - s1.Center
            if (dv - s1.Axis * dv.dot(s1.Axis)).Length < 0.02:
                a = s1.Axis
                out.append({"R": s1.Radius, "angle": u1 - u0, "width": v1 - v0, "axis": FreeCAD.Vector(a.x, a.y, a.z)})
                break
    return out


def _edge_faces(sh):
    m = {}
    for i, f in enumerate(sh.Faces):
        for e in f.Edges:
            m.setdefault(e.hashCode(), [e, []])[1].append(i)
    return m


def _convex(sh, e, f1, f2):
    d = e.Vertexes[-1].Point - e.Vertexes[0].Point
    d.normalize()
    m = e.CenterOfMass
    tv = nrm(f2).cross(d)
    for sg in (1, -1):
        tv = nrm(f2).cross(d) * sg
        if f2.distToShape(Part.Vertex(m + tv * 0.05))[0] < 1e-6:
            break
    return nrm(f1).dot(tv) < 0


def heal_sharp(sh, t, R):
    """fillet the sharp bend edges: concave R, convex R + t. A bend = a convex and a concave straight edge on the same two sheet
    faces' opposites (normals reversed), parallel and overlapping along the axis."""
    fl = sheet_flags(sh, t)
    cands = []
    for e, fi in _edge_faces(sh).values():
        if len(fi) != 2 or not is_line(e) or not all(fl[i] for i in fi):
            continue
        f1, f2 = sh.Faces[fi[0]], sh.Faces[fi[1]]
        n1, n2 = nrm(f1), nrm(f2)
        if abs(n1.dot(n2)) > 0.999:
            continue
        d = e.Vertexes[-1].Point - e.Vertexes[0].Point
        d.normalize()
        cands.append((e, d, n1, n2, _convex(sh, e, f1, f2)))
    inner, outer = [], []
    for e, d, n1, n2, cv in cands:
        if not cv:
            continue
        for e2, d2, m1, m2, cv2 in cands:
            if cv2 or abs(d.dot(d2)) < 0.999:
                continue
            if not ((m1.dot(n1) < -0.999 and m2.dot(n2) < -0.999) or (m1.dot(n2) < -0.999 and m2.dot(n1) < -0.999)):
                continue
            a0, a1 = sorted(v.Point.dot(d) for v in e.Vertexes)
            b0, b1 = sorted(v.Point.dot(d) for v in e2.Vertexes)
            if min(a1, b1) - max(a0, b0) > 0.1:
                inner.append(e2)
                outer.append(e)
                break
    if not outer:
        return None  # no bend: a flat plate whose pockets happen to have faces t apart
    ends = lambda es: [(e.Vertexes[0].Point, e.Vertexes[-1].Point) for e in es]  # noqa: E731

    def refind(sh2, pts):
        found = []
        for p0, p1 in pts:
            for e in sh2.Edges:
                if is_line(e):
                    q0, q1 = e.Vertexes[0].Point, e.Vertexes[-1].Point
                    if ((q0 - p0).Length < 1e-3 and (q1 - p1).Length < 1e-3) or ((q0 - p1).Length < 1e-3 and (q1 - p0).Length < 1e-3):
                        found.append(e)
                        break
        if len(found) != len(pts):
            raise Part.OCCError("bend edge lost after the first fillet")
        return found
    sh2, err = None, None
    for first, r1, second, r2 in ((inner, R, outer, R + t), (outer, R + t, inner, R)):  # OCC accepts only one order on some corners
        try:
            pts2 = ends(second)
            sh2 = sh.makeFillet(r1, first)
            sh2 = sh2.makeFillet(r2, refind(sh2, pts2))
            break
        except Part.OCCError as ex:
            sh2, err = None, ex
    if sh2 is None:
        raise Fail("cannot add bend radii R %.2f / %.2f to the sharp model (%s): a hole or notch inside the bend zone, "
                   "a bend narrower than R + t, or not a constant-thickness sheet; try a smaller --R" % (R, R + t, err))
    sh2 = sh2.removeSplitter()
    if not sh2.isValid() or len(sh2.Solids) != 1:
        raise Fail("model with added bend radii is not a valid solid")
    return sh2


# ------------------------------------------------------------------ unfold
def unfold_try(doc, sh, fidx, K):
    """-> (flat shape, fold lines). Raises Fail with the SheetMetal reason."""
    o = doc.addObject("Part::Feature", "P%d" % fidx)
    o.Shape = sh
    doc.recompute()
    _SEL[:] = [o]
    face = sh.Faces[fidx - 1]
    try:
        res = SM.getUnfold({1000: K}, o, face, "Face%d" % fidx)
    except Exception as ex:  # SheetMetal raises IndexError / AttributeError on geometry it cannot parse
        raise Fail("SheetMetal crashed on Face%d: %s: %s" % (fidx, ex.__class__.__name__, ex))
    flat, folds = res[0], res[1]
    if flat is None:
        why = "no result"
        try:  # getUnfold hides the reason; replay its first stages to read the error code
            T = SM.SheetTree(sh, fidx - 1, {1000: K})
            if T.error_code is None:
                T.Bend_analysis(fidx - 1, None)
            if T.error_code is None:
                T.unfold_tree2(T.root)
            if T.error_code is not None:
                why = "%s (Face%d)" % (SM.unfold_error.get(T.error_code, T.error_code), (T.failed_face_idx or 0) + 1)
        except Exception as ex:
            why = "%s: %s" % (ex.__class__.__name__, ex)
        raise Fail("SheetMetal cannot unfold from Face%d: %s" % (fidx, why))
    return flat, folds


# ------------------------------------------------------------------ flat face -> 2D
class Frame:
    def __init__(self, face):
        self.n = nrm(face)
        self.o = face.CenterOfMass
        ow = face.OuterWire
        lines = [e for e in ow.Edges if is_line(e)]
        if lines:
            e = max(lines, key=lambda e: e.Length)
            xd = e.Vertexes[-1].Point - e.Vertexes[0].Point
        else:
            xd = FreeCAD.Vector(1, 0, 0) if abs(self.n.x) < 0.9 else FreeCAD.Vector(0, 1, 0)
        xd = xd - self.n * xd.dot(self.n)
        xd.normalize()
        self.x, self.y = xd, self.n.cross(xd)

    def p(self, v):
        d = v - self.o
        return (d.dot(self.x), d.dot(self.y))


def edge_prims(e, fr):
    """-> list of ('LINE', p0, p1) / ('CIRCLE', c, r) / ('ARC', c, r, a0, a1 degrees ccw) / ('POLY', pts)"""
    cn = e.Curve.__class__.__name__
    if is_line(e):
        return [("LINE", fr.p(e.Vertexes[0].Point), fr.p(e.Vertexes[-1].Point))]
    if cn == "Circle":
        c = fr.p(e.Curve.Center)
        r = e.Curve.Radius
        if e.isClosed():
            return [("CIRCLE", c, r)]
        p0, p1 = fr.p(e.valueAt(e.FirstParameter)), fr.p(e.valueAt(e.LastParameter))
        pm = fr.p(e.valueAt((e.FirstParameter + e.LastParameter) / 2))
        a0, am, a1 = [math.atan2(q[1] - c[1], q[0] - c[0]) for q in (p0, pm, p1)]
        ccw = ((am - a0 + math.pi) % (2 * math.pi) - math.pi) > 0
        if not ccw:
            a0, a1 = a1, a0
        return [("ARC", c, r, math.degrees(a0) % 360.0, math.degrees(a1) % 360.0)]
    return [("POLY", [fr.p(v) for v in e.discretize(Deflection=0.01)])]


def sample(prims):
    pts = []
    for p in prims:
        if p[0] == "LINE":
            pts += [p[1], p[2]]
        elif p[0] == "CIRCLE":
            pts += [(p[1][0] + p[2] * math.cos(a / 90 * math.pi / 2), p[1][1] + p[2] * math.sin(a / 90 * math.pi / 2)) for a in range(360 * 2)]
        elif p[0] == "ARC":
            a0, a1 = p[3], p[4]
            if a1 <= a0:
                a1 += 360
            n = max(8, int((a1 - a0) / 0.5))
            pts += [(p[1][0] + p[2] * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
                     p[1][1] + p[2] * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]
        else:
            pts += p[1]
    return pts


# ------------------------------------------------------------------ DXF R12 writer
def dxf_text(layers, ents):
    """layers: {name: (color, linetype)}; ents: [(layer, prim)]"""
    o = []

    def g(c, v):
        o.append("%3d\n%s" % (c, v))
    g(0, "SECTION"), g(2, "HEADER"), g(9, "$ACADVER"), g(1, "AC1009"), g(9, "$INSUNITS"), g(70, 4), g(0, "ENDSEC")
    g(0, "SECTION"), g(2, "TABLES")
    g(0, "TABLE"), g(2, "LTYPE"), g(70, 2)
    g(0, "LTYPE"), g(2, "CONTINUOUS"), g(70, 0), g(3, "Solid line"), g(72, 65), g(73, 0), g(40, 0.0)
    g(0, "LTYPE"), g(2, "DASHED"), g(70, 0), g(3, "Dashed"), g(72, 65), g(73, 2), g(40, 9.0), g(49, 6.0), g(49, -3.0)
    g(0, "ENDTAB")
    g(0, "TABLE"), g(2, "LAYER"), g(70, len(layers))
    for nm, (col, lt) in layers.items():
        g(0, "LAYER"), g(2, nm), g(70, 0), g(62, col), g(6, lt)
    g(0, "ENDTAB"), g(0, "ENDSEC")
    g(0, "SECTION"), g(2, "ENTITIES")
    for ly, p in ents:
        lt = layers[ly][1]
        if p[0] == "LINE":
            g(0, "LINE"), g(8, ly), g(6, lt), g(10, "%.5f" % p[1][0]), g(20, "%.5f" % p[1][1]), g(30, 0), \
                g(11, "%.5f" % p[2][0]), g(21, "%.5f" % p[2][1]), g(31, 0)
        elif p[0] == "CIRCLE":
            g(0, "CIRCLE"), g(8, ly), g(6, lt), g(10, "%.5f" % p[1][0]), g(20, "%.5f" % p[1][1]), g(30, 0), g(40, "%.5f" % p[2])
        elif p[0] == "ARC":
            g(0, "ARC"), g(8, ly), g(6, lt), g(10, "%.5f" % p[1][0]), g(20, "%.5f" % p[1][1]), g(30, 0), g(40, "%.5f" % p[2]), \
                g(50, "%.5f" % p[3]), g(51, "%.5f" % p[4])
        else:
            g(0, "POLYLINE"), g(8, ly), g(6, lt), g(66, 1), g(70, 0)
            for q in p[1]:
                g(0, "VERTEX"), g(8, ly), g(10, "%.5f" % q[0]), g(20, "%.5f" % q[1]), g(30, 0)
            g(0, "SEQEND"), g(8, ly)
    g(0, "ENDSEC"), g(0, "EOF")
    return "\n".join(o) + "\n"


def dxf_entities(path):
    """[(type, layer, {group code: [values]})] of the ENTITIES section of any ASCII DXF (VERTEX rows take their POLYLINE's layer)"""
    rows = open(path, errors="replace").read().replace("\r", "").split("\n")
    ents, cur, inent = [], None, False
    for i in range(0, len(rows) - 1, 2):
        c, v = rows[i].strip(), rows[i + 1].strip()
        if c == "2" and v == "ENTITIES":
            inent = True
            continue
        if c == "0" and v == "ENDSEC":
            inent = False
        if not inent:
            continue
        if c == "0":
            cur = {"t": v, "f": {}}
            ents.append(cur)
        elif cur is not None:
            cur["f"].setdefault(c, []).append(v)
    out, owner = [], ""
    for en in ents:
        ly = (en["f"].get("8") or [""])[0]
        if en["t"] == "VERTEX":
            ly = owner
        elif en["t"] == "POLYLINE":
            owner = ly
        out.append((en["t"], ly, {k: [float(x) for x in v] if 10 <= int(k) < 60 else v for k, v in en["f"].items()}))
    return out


def dxf_cut_extent(path, layers=("OUTLINE", "HOLES", "0")):
    """(dx, dy) of the cut layers of any DXF (the kit's laser DXF has everything on layer 0) (LINE, CIRCLE, ARC, LWPOLYLINE, POLYLINE/VERTEX, SPLINE control points)"""
    pts = []
    for typ, ly, f in dxf_entities(path):
        if ly not in layers:
            continue
        num = lambda k: f.get(k, [])  # noqa: E731
        if typ == "LINE":
            pts += list(zip(num("10") + num("11"), num("20") + num("21")))
        elif typ == "CIRCLE":
            x, y, r = num("10")[0], num("20")[0], num("40")[0]
            pts += [(x - r, y - r), (x + r, y + r)]
        elif typ == "ARC":
            x, y, r = num("10")[0], num("20")[0], num("40")[0]
            a0, a1 = num("50")[0], num("51")[0]
            if a1 <= a0:
                a1 += 360
            angs = [a0, a1] + [q for q in (0, 90, 180, 270, 360, 450) if a0 < q < a1]
            pts += [(x + r * math.cos(math.radians(a)), y + r * math.sin(math.radians(a))) for a in angs]
        elif typ in ("LWPOLYLINE", "VERTEX", "SPLINE"):
            pts += list(zip(num("10"), num("20")))
    if not pts:
        raise Fail("no cut-layer geometry (OUTLINE/HOLES/0) in %s" % path)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return max(xs) - min(xs), max(ys) - min(ys)


def dxf_circles(path):
    """[(x, y, r)] of CIRCLE entities on the HOLES layer (layer 0 in the kit's laser DXF)"""
    return [(f["10"][0], f["20"][0], f["40"][0]) for typ, ly, f in dxf_entities(path) if typ == "CIRCLE" and ly in ("HOLES", "0")]


def match_holes(mine, kit, bx, by, tol=0.1):
    """name of the symmetry of the rectangle (identity / flip X / flip Y / rotate 180) that maps my circles onto the kit's, else None"""
    for nm, fx, fy in (("same side", 0, 0), ("mirrored in X", 1, 0), ("mirrored in Y", 0, 1), ("turned 180", 1, 1)):
        left = list(kit)
        ok = True
        for x, y, r in mine:
            q = (bx - x if fx else x, by - y if fy else y)
            hit = next((k for k in left if abs(k[0] - q[0]) <= tol and abs(k[1] - q[1]) <= tol and abs(k[2] - r) <= tol), None)
            if hit is None:
                ok = False
                break
            left.remove(hit)
        if ok:
            return nm
    return None


# ------------------------------------------------------------------ the legs formula
def leg_sum(sh, t, bends):
    """sum of straight legs measured on the folded model, or None + reason. Bends must be parallel; sheet planar faces whose normal
    is perpendicular to the bend axis, grouped by plane = leg sides; extent along (n x axis); legs = sides / 2 must be bends + 1."""
    ax = bends[0]["axis"]
    if any(abs(b["axis"].dot(ax)) < 0.999 for b in bends):
        return None, "bends not parallel"
    fl = sheet_flags(sh, t)
    groups = {}
    for f, ok in zip(sh.Faces, fl):
        if not ok:
            continue
        n = nrm(f)
        if abs(n.dot(ax)) > 1e-3:
            continue
        u = n.cross(ax)
        u.normalize()
        key = (round(n.x, 3), round(n.y, 3), round(n.z, 3), round(f.CenterOfMass.dot(n), 2))
        pts = [v.Point.dot(u) for v in f.Vertexes]
        # arcs of cut-outs stay inside the vertices' extent for a leg end; fine for a straight-sided blank
        g = groups.setdefault(key, [1e18, -1e18, u])
        g[0], g[1] = min(g[0], min(pts)), max(g[1], max(pts))
    if len(groups) != 2 * (len(bends) + 1):
        return None, "legs not measurable (%d leg sides for %d bends)" % (len(groups), len(bends))
    return sum(g[1] - g[0] for g in groups.values()) / 2.0, ""


# ------------------------------------------------------------------ main
def run(a):
    _import_freecad()
    K = a.k
    name = a.name or os.path.splitext(os.path.basename(a.step))[0]
    os.makedirs(a.out, exist_ok=True)
    try:
        sh = Part.read(a.step)
    except Exception as ex:
        raise Fail("cannot read STEP: %s" % ex)
    if len(sh.Solids) != 1:
        raise Fail("STEP holds %d solids, need exactly one sheet part" % len(sh.Solids))
    sh = Part.Solid(sh.Solids[0]) if sh.ShapeType != "Solid" else sh
    t = a.t or detect_t(sh)
    bends = bend_cylinders(sh, t)
    note = []
    if bends:
        R = min(b["R"] for b in bends)
        if a.R and abs(a.R - R) > 0.05:
            note.append("model has real bend radii R %.2f, --R %.2f ignored" % (R, a.R))
    else:
        R = a.R or t
        healed = heal_sharp(sh, t, R) if _has_bend_edges(sh, t) else None
        if healed is not None:
            sh = healed
            bends = bend_cylinders(sh, t)
            note.append("sharp model: bend radii R %.2f / %.2f added by fillet (R %s)" % (R, R + t, "from --R" if a.R else "= t assumed"))
    print("thickness t = %.3f, inner radius R = %s, bends %d%s" % (
        t, ("%.3f" % R) if bends else "-", len(bends), ("  [" + "; ".join(note) + "]") if note else ""))
    vol = sh.Volume

    doc = FreeCAD.newDocument("sm")
    folds = None
    if bends:
        pl = sorted(((f.Area, i + 1) for i, f in enumerate(sh.Faces) if is_plane(f)), reverse=True)
        fl = sheet_flags(sh, t)
        order = [i for _, i in pl if fl[i - 1]]
        if a.face:
            order = [a.face]
        why = []
        flat = None
        for fi in order[:4]:
            try:
                flat, folds = unfold_try(doc, sh, fi, K)
                print("base face Face%d (area %.0f)" % (fi, sh.Faces[fi - 1].Area))
                break
            except Fail as ex:
                why.append(str(ex))
        if flat is None:
            raise Fail("; ".join(why) or "no sheet face to unfold from")
        tops = [f for f in flat.Faces if is_plane(f)]
        top = max(tops, key=lambda f: f.Area)
    else:
        pls = [f for f in sh.Faces if is_plane(f)]
        top = max(pls, key=lambda f: f.Area)
        print("flat part, no bends: the largest planar face is the blank")

    fr = Frame(top)
    ents = []
    outer = [p for e in top.OuterWire.Edges for p in edge_prims(e, fr)]
    pts = sample(outer)
    x0, y0 = min(p[0] for p in pts), min(p[1] for p in pts)
    sx, sy = max(p[0] for p in pts) - x0, max(p[1] for p in pts) - y0

    def shift(p):
        if p[0] == "LINE":
            return ("LINE", (p[1][0] - x0, p[1][1] - y0), (p[2][0] - x0, p[2][1] - y0))
        if p[0] == "CIRCLE":
            return ("CIRCLE", (p[1][0] - x0, p[1][1] - y0), p[2])
        if p[0] == "ARC":
            return ("ARC", (p[1][0] - x0, p[1][1] - y0), p[2], p[3], p[4])
        return ("POLY", [(q[0] - x0, q[1] - y0) for q in p[1]])
    ents += [("OUTLINE", shift(p)) for p in outer]
    nholes = 0
    for w in top.Wires:
        if w.isSame(top.OuterWire):
            continue
        nholes += 1
        ents += [("HOLES", shift(p)) for e in w.Edges for p in edge_prims(e, fr)]

    bend_lines = []
    if folds is not None and folds.Edges:
        raw = []
        for e in folds.Edges:
            p0, p1 = fr.p(e.Vertexes[0].Point), fr.p(e.Vertexes[-1].Point)
            raw.append((p0, p1))
        bend_lines = raw
    dirs = None
    if bend_lines:
        d = (bend_lines[0][1][0] - bend_lines[0][0][0], bend_lines[0][1][1] - bend_lines[0][0][1])
        L = math.hypot(*d) or 1.0
        dirs = (d[0] / L, d[1] / L)
        # SheetMetal gives the two edges of each bend strip: keep their middle line, one per bend
        mids = []
        for p0, p1 in bend_lines:
            if (p1[0] - p0[0]) * dirs[0] + (p1[1] - p0[1]) * dirs[1] < 0:
                p0, p1 = p1, p0  # same direction for every edge before averaging
            c = ((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2)
            off = -c[0] * dirs[1] + c[1] * dirs[0]
            mids.append((off, p0, p1))
        mids.sort()
        # cluster by offset: edges of one bend are within one bend allowance of each other
        ba_min = min(b["angle"] for b in bends) * (R + K * t) if bends else 1.0
        clusters = []
        for m in mids:
            if clusters and m[0] - clusters[-1][-1][0] <= ba_min * 1.5:
                clusters[-1].append(m)
            else:
                clusters.append([m])
        bend_lines = []
        for cl in clusters:
            p0 = (sum(m[1][0] for m in cl) / len(cl), sum(m[1][1] for m in cl) / len(cl))
            p1 = (sum(m[2][0] for m in cl) / len(cl), sum(m[2][1] for m in cl) / len(cl))
            bend_lines.append((p0, p1))
        for p0, p1 in bend_lines:
            ents.append(("BEND", ("LINE", (p0[0] - x0, p0[1] - y0), (p1[0] - x0, p1[1] - y0))))

    layers = {"OUTLINE": (7, "CONTINUOUS"), "HOLES": (5, "CONTINUOUS"), "BEND": (3, "DASHED")}
    dxf_path = os.path.join(a.out, name + ".flat.dxf")
    open(dxf_path, "w").write(dxf_text(layers, ents))

    area = top.Area
    checks, fails = [], 0

    def report(ok, what, nums):
        nonlocal fails
        fails += 0 if ok in ("ok", "skip") else 1
        checks.append({"check": what, "status": ok, **nums})
        print("%s %s %s" % (ok if ok != "FAIL" else "FAIL", what, " ".join("%s=%s" % kv for kv in nums.items())))

    # (a) volume: the flat blank keeps the neutral-axis length, the folded solid is the mid-surface; K != 0.5 shifts bend volume
    shift_v = sum(b["angle"] * b["width"] * t * t * (K - 0.5) for b in bends)
    exp = (vol + shift_v) / t
    err = (area - exp) / exp
    report("ok" if abs(err) <= 0.005 else "FAIL", "flat area x t vs folded volume",
           {"flat_area": "%.1f" % area, "expected_area": "%.1f" % exp, "diff_pct": "%.3f" % (err * 100), "tol_pct": 0.5,
            **({"hint": "not_constant_thickness_(countersinks_pockets_bosses)"} if abs(err) > 0.005 and not bends else {})})

    # (b) hand formula
    blank_len = None
    if bends and dirs:
        perp = (-dirs[1], dirs[0])
        vals = [p[0] * perp[0] + p[1] * perp[1] for p in pts]
        blank_len = max(vals) - min(vals)
        legs, why = leg_sum(sh, t, bends)
        ba = sum(b["angle"] * (b["R"] + K * t) for b in bends)
        if legs is None:
            report("skip", "hand formula legs + sum BA", {"reason": why.replace(" ", "_")})
        else:
            hand = legs + ba
            report("ok" if abs(hand - blank_len) <= 0.1 else "FAIL", "hand formula legs + sum BA",
                   {"freecad_blank": "%.3f" % blank_len, "hand": "%.3f" % hand, "legs": "%.3f" % legs, "BA": "%.3f" % ba,
                    "diff_mm": "%.3f" % (blank_len - hand)})
    elif bends:
        report("FAIL", "hand formula legs + sum BA", {"reason": "SheetMetal_returned_no_bend_lines"})
    else:
        report("skip", "hand formula legs + sum BA", {"reason": "no_bends"})

    # (c) compare with the kit DXF
    if a.compare:
        try:
            kx, ky = dxf_cut_extent(a.compare)
        except Fail as ex:
            report("FAIL", "blank vs kit dxf", {"reason": str(ex).replace(" ", "_")})
        else:
            mine, kit = sorted([sx, sy], reverse=True), sorted([kx, ky], reverse=True)
            ok = abs(mine[0] - kit[0]) <= 0.1 and abs(mine[1] - kit[1]) <= 0.1
            report("ok" if ok else "FAIL", "blank vs kit dxf",
                   {"freecad": "%.2f x %.2f" % tuple(mine), "kit": "%.2f x %.2f" % tuple(kit)})
            kc = dxf_circles(a.compare)
            mc = [(p[1][0], p[1][1], p[2]) for ly, p in ents if ly == "HOLES" and p[0] == "CIRCLE"]
            if ok and (kc or mc):
                if len(kc) != len(mc):
                    report("FAIL", "round holes vs kit dxf", {"freecad": len(mc), "kit": len(kc)})
                else:
                    how = match_holes(mc, kc, sx, sy)
                    report("ok" if how else "FAIL", "round holes vs kit dxf",
                           {"count": len(mc), "match": ("%s" % how).replace(" ", "_")} if how else
                           {"count": len(mc), "match": "none_within_0.1_mm_in_any_orientation"})

    res = {"name": name, "t": t, "R": R if bends else None, "K": K,
           "bends": [{"angle_deg": round(math.degrees(b["angle"]), 2), "width": round(b["width"], 2)} for b in bends],
           "bend_count": len(bends), "blank": [round(sx, 3), round(sy, 3)], "blank_len_perp_to_bends": blank_len and round(blank_len, 3),
           "flat_area": round(area, 2), "holes": nholes, "folded_volume": round(vol, 1), "notes": note, "checks": checks}
    open(os.path.join(a.out, name + ".flat.json"), "w").write(json.dumps(res, indent=1))
    print("blank %.2f x %.2f mm, %d bends %s, %d holes -> %s" % (
        sx, sy, len(bends), [round(math.degrees(b["angle"]), 1) for b in bends], nholes, dxf_path))
    return 1 if fails else 0


def _has_bend_edges(sh, t):
    """a sheet with bends (sharp) vs a flat plate: any two non-parallel planar sheet faces at all"""
    fl = sheet_flags(sh, t)
    ns = [nrm(f) for f, ok in zip(sh.Faces, fl) if ok]
    return any(abs(n.dot(m)) < 0.999 for n in ns for m in ns)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--step", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--name")
    ap.add_argument("--k", type=float, default=0.33, help="ANSI K-factor: 0.33 steel/stainless, 0.40 aluminium")
    ap.add_argument("--R", type=float, help="inner bend radius for a sharp model (default t)")
    ap.add_argument("--t", type=float, help="thickness (default: detected)")
    ap.add_argument("--face", type=int, help="1-based face to unfold from")
    ap.add_argument("--compare", help="kit .dxf of the same part (shop_drawing.py) to compare blank size with")
    a = ap.parse_args()
    try:
        sys.exit(run(a))
    except Fail as ex:
        print("FAIL unfold: %s" % ex)
        sys.exit(1)
    except Exception as ex:  # FreeCAD / OCC errors outside the guarded calls
        print("FAIL unfold: unexpected %s: %s" % (ex.__class__.__name__, ex))
        sys.exit(1)


if __name__ == "__main__":
    main()
