"""Laser-clean DXF a shop's laser software reads (CypCut and the like refuse or mis-cut a DXF with layers, text or loose lines):
AutoCAD 2000 (AC1015), mm, ASCII, every cut contour ONE closed LWPOLYLINE (arcs as bulges) on layer 0, round holes as CIRCLE;
no text, no bend lines, no other layers (bends are on the PDF).
  write(face, path, edges_fn)   face = the flat blank face at 0,0; edges_fn = shop_drawing._edges (LINE / ARC / CIRCLE / polyline)
  check(path, lang) -> [problems]   used by check_drawings.py (the problems in the project's language, drawings_i18n.py)
  python laser_dxf.py IN.dxf OUT.dxf   any DXF's cut layers (OUTLINE / HOLES / 0) -> this format, e.g. sm_unfold's x.flat.dxf
"""
import math
import sys
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import drawings_i18n as I  # noqa: E402

LAYER = "0"
TOL = 0.01


def _prims(ents):
    """DXF entities -> ("LINE", a, b) / ("ARC", c, r, a0, a1) / ("CIRCLE", c, r); polylines exploded, splines flattened"""
    out = []
    for e in ents:
        for v in (list(e.virtual_entities()) if e.dxftype() in ("LWPOLYLINE", "POLYLINE") else [e]):
            t = v.dxftype()
            if t == "LINE":
                out.append(("LINE", tuple(v.dxf.start)[:2], tuple(v.dxf.end)[:2]))
            elif t == "ARC":
                out.append(("ARC", tuple(v.dxf.center)[:2], v.dxf.radius, v.dxf.start_angle, v.dxf.end_angle))
            elif t == "CIRCLE":
                out.append(("CIRCLE", tuple(v.dxf.center)[:2], v.dxf.radius))
            elif t in ("SPLINE", "ELLIPSE"):
                pts = list(v.flattening(0.05))
                out += [("LINE", (a.x, a.y), (b.x, b.y)) for a, b in zip(pts[:-1], pts[1:])]
    return out


def _segments(face, edges_fn):
    msp = ezdxf.new("R2010").modelspace()
    edges_fn(msp, face.outer_wire().edges(), "C")
    for w in face.inner_wires():
        edges_fn(msp, w.edges(), "C")
    return _prims(msp)


def write(face, path, edges_fn):
    return write_prims(_segments(face, edges_fn), path)


def write_prims(prims, path):
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 4
    doc.header["$MEASUREMENT"] = 1
    msp = doc.modelspace()
    items = []                                           # (start, end, bulge)
    for s in prims:
        if s[0] == "CIRCLE":
            msp.add_circle(s[1], s[2], dxfattribs={"layer": LAYER})
        elif s[0] == "LINE":
            if math.dist(s[1], s[2]) > 1e-6:
                items.append([s[1], s[2], 0.0])
        else:
            c, r, a0, a1 = s[1], s[2], math.radians(s[3]), math.radians(s[4])
            sweep = (a1 - a0) % (2 * math.pi)
            if r * sweep < 1e-6:
                continue
            items.append([(c[0] + r * math.cos(a0), c[1] + r * math.sin(a0)), (c[0] + r * math.cos(a1), c[1] + r * math.sin(a1)),
                          math.tan(sweep / 4)])
    # Join the pieces by their end points, whatever order they come in. Points within TOL are one node (single linkage, so an
    # edge flattened into pieces shorter than TOL - a lofted bend end - collapses into one node and drops out as a self-loop
    # instead of closing on itself and leaving the outline open where it was).
    ends = [p for a, b, _ in items for p in (a, b)]
    parent = list(range(len(ends)))

    def root(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k
    cell = {}
    for k, p in enumerate(ends):
        cx, cy = int(math.floor(p[0] / TOL)), int(math.floor(p[1] / TOL))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for m in cell.get((cx + dx, cy + dy), ()):
                    if math.dist(p, ends[m]) < TOL:
                        parent[root(k)] = root(m)
        cell.setdefault((cx, cy), []).append(k)
    edges = [(root(2 * k), root(2 * k + 1), it) for k, it in enumerate(items)]
    nodes = {}
    for k, p in enumerate(ends):
        nodes.setdefault(root(k), []).append(p)
    edges = [e for e in edges if e[0] != e[1]]
    kept = {n for e in edges for n in e[:2]}
    # a node wider than the tolerance that no edge leaves is a whole contour that collapsed (a hole under ~1 mm drawn in
    # pieces): it would vanish from the DXF in silence, so it counts as a contour that does not close
    lost = sum(1 for n, pts in nodes.items() if n not in kept and max(math.dist(pts[0], q) for q in pts) > TOL)
    at = {}
    for k, (na, nb, _) in enumerate(edges):
        at.setdefault(na, []).append(k)
        at.setdefault(nb, []).append(k)
    used = [False] * len(edges)
    open_n = 0
    for k0 in range(len(edges)):
        if used[k0]:
            continue
        used[k0] = True
        na, nb, (a, b, bu) = edges[k0]
        start, node, pts, end = na, nb, [(a[0], a[1], bu)], b
        while node != start:
            nxt = next((k for k in at[node] if not used[k]), None)
            if nxt is None:
                break
            used[nxt] = True
            ea, eb, (a2, b2, bu2) = edges[nxt]
            nxt_start = a2 if ea == node else b2
            if math.dist(end, nxt_start) > TOL:            # a collapsed run of pieces between them: keep this edge's real end
                pts.append((end[0], end[1], 0.0))
            if ea == node:
                pts.append((a2[0], a2[1], bu2))
                node, end = eb, b2
            else:
                pts.append((b2[0], b2[1], -bu2))
                node, end = ea, a2
        closed = node == start
        if not closed:
            pts.append((end[0], end[1], 0.0))                # show the whole open chain (diagnostics)
        open_n += not closed
        if closed and math.dist(end, pts[0][:2]) > TOL:    # the run that closed it was collapsed: keep the real last point
            pts.append((end[0], end[1], 0.0))
        msp.add_lwpolyline(pts, format="xyb", close=closed, dxfattribs={"layer": LAYER})
    open_n += lost
    doc.saveas(path)
    raw = open(path, "rb").read()
    if any(c >= 128 for c in raw):
        raise ValueError(f"{path}: non-ASCII bytes in the DXF")
    return open_n


def check(path, lang=I.DEFAULT):
    """problems that stop the shop's laser program from reading the file (empty = fine)"""
    errs = []
    raw = open(path, "rb").read()
    if any(c >= 128 for c in raw):
        errs.append(I.t("dxf.not_ascii", lang))
    doc = ezdxf.readfile(path)
    if doc.dxfversion != "AC1015":
        errs.append(I.t("dxf.version", lang, version=doc.dxfversion))
    kinds = {}
    for e in doc.modelspace():
        kinds[e.dxftype()] = kinds.get(e.dxftype(), 0) + 1
        if e.dxf.layer != LAYER:
            errs.append(I.t("dxf.layer", lang, kind=e.dxftype(), layer=e.dxf.layer))
            break
    extra = sorted(set(kinds) - {"LWPOLYLINE", "CIRCLE"})
    if extra:
        errs.append(I.t("dxf.extra", lang, kinds=", ".join(extra)))
    n_open = sum(1 for e in doc.modelspace().query("LWPOLYLINE") if not e.closed)
    if n_open:
        errs.append(I.t("dxf.open", lang, n=n_open))
    if not kinds.get("LWPOLYLINE") and not kinds.get("CIRCLE"):
        errs.append(I.t("dxf.no_outline", lang))
    return errs


def convert(src, dst):
    """cut geometry of any DXF (layers OUTLINE, HOLES, 0) rewritten laser-clean; returns the number of open contours"""
    return write_prims(_prims(e for e in ezdxf.readfile(src).modelspace() if e.dxf.layer.upper() in ("OUTLINE", "HOLES", "0")), dst)


if __name__ == "__main__":
    n = convert(sys.argv[1], sys.argv[2])
    errs = check(sys.argv[2], I.project_lang())
    print(f"{sys.argv[2]}: {n} open contour(s); " + ("; ".join(errs) if errs else "laser-clean"))
    sys.exit(1 if n or errs else 0)
