"""Test-side probe for the shop-drawings tests: runs in the drawings venv (build123d, ezdxf, pymupdf), prints JSON.

    python kit_probe.py pdf <file.pdf>                 -> {"pages": [plain text], "layout": [pdftotext -layout text]}
    python kit_probe.py dxf <file.dxf>                 -> header version, ascii, entities, circles, extents
    python kit_probe.py make <kind> <out.step>         -> a synthetic part: small_bracket | box | cabinet_u | ... (see make)
    python kit_probe.py old_format <in.dxf> <out.dxf>  -> same geometry, the old R2010 layout (OUTLINE/HOLES/BEND + TEXT)
    python kit_probe.py open_contour <in.dxf> <out.dxf> -> the outer contour left open
    python kit_probe.py strip_flat_dims <in.pdf> <out.pdf> -> page 1 with every "**" text removed
    python kit_probe.py drop_page <in.pdf> <out.pdf> <page index>

Nothing here knows how the generator works: it only reads and edits its output files.
"""
import re
import json
import shutil
import subprocess
import procgroup
import sys
import warnings

warnings.filterwarnings("ignore")

# The synthetic U wall (test_cabinet_drawing.py reads the same numbers): web W x L, flanges H high (outer), t, inner R;
# 12 web holes (x, y, Ø) and 6 holes per flange (y, z, Ø), clear of the bend zones.
CABINET_U = {"t": 1.0, "R": 1.0, "W": 300.0, "L": 200.0, "H": 60.0,
             "web_holes": [(x, y, 6.0) for x in (37.5, 112.5, 187.5, 262.5) for y in (22.2, 100.0, 177.8)],
             "flange_holes": [(y, z, d) for y in (17.5, 100.0, 182.5) for z, d in ((-15.5, 9.0), (-40.7, 6.6))]}


def pdf(path):
    import pymupdf
    doc = pymupdf.open(path)
    pages = [p.get_text() for p in doc]
    layout = []
    if shutil.which("pdftotext"):
        for i in range(len(pages)):
            r = procgroup.run(["pdftotext", "-layout", "-f", str(i + 1), "-l", str(i + 1), path, "-"],
                               capture_output=True, text=True)
            layout.append(r.stdout)
    else:
        layout = [p.get_text(sort=True) for p in doc]
    return {"pages": pages, "layout": layout}


def dxf(path):
    import ezdxf
    from ezdxf import bbox
    raw = open(path, "rb").read()
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    ents = []
    for e in msp:
        d = {"type": e.dxftype(), "layer": e.dxf.get("layer", "0"), "linetype": e.dxf.get("linetype", "BYLAYER")}
        if e.dxftype() == "LWPOLYLINE":
            d["closed"] = bool(e.closed)
            d["n"] = len(e)
        ents.append(d)
    circles = [[e.dxf.center.x, e.dxf.center.y, e.dxf.radius] for e in msp.query("CIRCLE")]
    cut = [e for e in msp if e.dxftype() in ("LWPOLYLINE", "CIRCLE", "ARC", "LINE", "SPLINE", "POLYLINE")
           and e.dxf.get("layer", "0").upper() != "BEND"]
    ext = bbox.extents(cut)                                 # the blank: cut geometry only
    layer_lt = {l.dxf.name: l.dxf.get("linetype", "Continuous") for l in doc.layers}
    return {"version": doc.header.get("$ACADVER"), "ascii": raw.isascii(), "binary": raw[:22] == b"AutoCAD Binary DXF\r\n\x1a\x00",
            "entities": ents, "circles": circles, "layer_linetypes": layer_lt,
            "extmin": [ext.extmin.x, ext.extmin.y] if ext.has_data else None,
            "extmax": [ext.extmax.x, ext.extmax.y] if ext.has_data else None}


def make(kind, out):
    from build123d import Box, Cylinder, Pos, Rot, export_step
    if kind == "small_bracket":       # L bracket 40 x 30 base, 20 flange, t 2, sharp bend, three Ø4.5 holes
        t = 2.0
        base = Pos(20, 15, t / 2) * Box(40, 30, t)
        flange = Pos(20, t / 2, 10) * Box(40, t, 20)
        part = base + flange
        for x in (10, 30):
            part -= Pos(x, 18, t / 2) * Cylinder(2.25, 3 * t)
        part -= Pos(20, t / 2, 13) * Rot(90, 0, 0) * Cylinder(2.25, 3 * t)
    elif kind == "box":               # tray 120 x 80, walls 25 on all four sides, t 1.5; walls meet at the corners, 0.5 gap
        t, g, L, W, H = 1.5, 0.5, 120.0, 80.0, 25.0
        part = Pos(L / 2, W / 2, t / 2) * Box(L, W, t)
        part += Pos(L / 2, t / 2, H / 2) * Box(L, t, H)                       # front, full length
        part += Pos(L / 2, W - t / 2, H / 2) * Box(L, t, H)                   # back, full length
        side = W - 2 * t - 2 * g
        part += Pos(t / 2, W / 2, H / 2) * Box(t, side, H)                    # left, short by the gap at both ends
        part += Pos(L - t / 2, W / 2, H / 2) * Box(t, side, H)                # right
        for x in (30, 60, 90):
            part -= Pos(x, W / 2, t / 2) * Cylinder(3, 3 * t)
    elif kind == "disc":              # round blank Ø80 x 2 with one Ø5 hole off centre
        part = Pos(0, 0, 1) * Cylinder(40, 2)
        part -= Pos(20, 0, 1) * Cylinder(2.5, 6)
    elif kind == "perf":              # 100 x 100 x 1.5 plate, 6 x 6 = 36 equal Ø1.5 holes, pitch 12
        part = Pos(50, 50, 0.75) * Box(100, 100, 1.5)
        for i in range(6):
            for j in range(6):
                part -= Pos(20 + 12 * i, 20 + 12 * j, 0.75) * Cylinder(0.75, 5)
    elif kind == "up_flange":         # 120 x 40 x 2 plate, 20 mm flange bent UP (+Z) along the long edge (bend line along X)
        t = 2.0
        part = Pos(60, 20, t / 2) * Box(120, 40, t)
        part += Pos(60, 40 - t / 2, 10) * Box(120, t, 20)
        for x in (30, 90):
            part -= Pos(x, 15, t / 2) * Cylinder(3, 3 * t)
    elif kind == "cabinet_u":         # U wall: web 300 x 200, two 60 mm flanges bent down, t 1, inner R 1; 24 round holes
        from build123d import Axis, Face, Vector, Wire, extrude
        t, R, W, L, H = CABINET_U["t"], CABINET_U["R"], CABINET_U["W"], CABINET_U["L"], CABINET_U["H"]
        prof = [(0, t), (W, t), (W, t - H), (W - t, t - H), (W - t, 0), (t, 0), (t, t - H), (0, t - H)]
        part = extrude(Face(Wire.make_polygon([Vector(x, 0, z) for x, z in prof], close=True)), amount=L, dir=(0, 1, 0))
        along_y = part.edges().filter_by(Axis.Y)
        at = lambda x, z: [e for e in along_y if abs(e.center().X - x) < 1e-6 and abs(e.center().Z - z) < 1e-6]   # noqa: E731
        part = part.fillet(R + t, at(0, t) + at(W, t))                         # outer side of the bends
        part = part.fillet(R, [e for e in part.edges().filter_by(Axis.Y)
                               if any(abs(e.center().X - x) < 1e-6 and abs(e.center().Z) < 1e-6 for x in (t, W - t))])
        for x, y, d in CABINET_U["web_holes"]:
            part -= Pos(x, y, t / 2) * Cylinder(d / 2, 3 * t)
        for y, z, d in CABINET_U["flange_holes"]:
            for x in (t / 2, W - t / 2):
                part -= Pos(x, y, z) * Rot(0, 90, 0) * Cylinder(d / 2, 3 * t)
    elif kind == "cube":              # 30 mm solid block: not sheet metal
        part = Pos(15, 15, 15) * Box(30, 30, 30)
    elif kind == "far_tilted_plate":  # 5 mm plate 60 x 167 tilted 16.8 deg, 1.5 m from the origin (a plate placed in a machine)
        part = Pos(129, 0, 1531) * Rot(0, -16.8, 0) * Box(5, 60, 167)
    elif kind == "flat_strip":        # 6 x 40 x 3 flat strip: two big non-parallel faces by aspect, no bend (an arm plate)
        part = Pos(3, 20, 1.5) * Box(6, 40, 3)
        part -= Pos(3, 30, 1.5) * Cylinder(1.5, 9)
    elif re.fullmatch(r"lip\d+(_hole)?(_far)?", kind):   # t 2: base 60 x 20, a 30 mm lip bent N deg UP (sharp corners); _hole: a 6 mm hole in the base; _far: 2069 mm from the origin
        import math
        from build123d import Face, Vector, Wire, extrude
        deg, hole, far = int(re.match(r"lip(\d+)", kind).group(1)), "_hole" in kind, kind.endswith("_far")
        t, a = 2.0, math.radians(deg)
        ux, uz, nx, nz = math.cos(a), math.sin(a), -math.sin(a), math.cos(a)     # lip direction / its up-side normal
        s = t * math.tan(a / 2)
        mitre = (60 + t * nx + s * ux, t)                                        # where the top faces meet
        pts = [(0, 0), (60, 0), (60 + 30 * ux, 30 * uz), (60 + 30 * ux + t * nx, 30 * uz + t * nz), mitre, (0, t)]
        part = extrude(Face(Wire.make_polygon([Vector(x, -10, z) for x, z in pts], close=True)), amount=20, dir=(0, 1, 0))
        if hole:
            part -= Pos(20, 3, 0) * Cylinder(3, 10)
        if far:
            part = Pos(-2069, 0, 0) * part
    elif kind in ("taper_lip", "straight_lip"):   # t 1.5, base 60 + a 5 deg leg 60 + a 45 deg lip 40 (sharp corners); taper_lip: the outline widens 20 -> 120 along x, straight_lip: constant 60
        import math
        from build123d import Face, Vector, Wire, extrude
        t = 1.5
        angs, lens = [0.0, 5.0, 50.0], [60.0, 60.0, 40.0]
        P = [(0.0, 0.0)]
        D = [(math.cos(math.radians(a)), math.sin(math.radians(a))) for a in angs]
        for (dx, dz), L in zip(D, lens):
            P.append((P[-1][0] + dx * L, P[-1][1] + dz * L))
        N = [(-dz, dx) for dx, dz in D]
        off = [((x + t * N[min(i, 2)][0], z + t * N[min(i, 2)][1])) for i, (x, z) in enumerate(P)]        # ends
        def hit(i):            # inner corner i (between legs i-1 and i): intersect the two offset lines
            (ax, az), (bx, bz) = (P[i][0] + t * N[i - 1][0], P[i][1] + t * N[i - 1][1]), (P[i][0] + t * N[i][0], P[i][1] + t * N[i][1])
            d1, d2 = D[i - 1], D[i]
            det = d1[0] * -d2[1] + d1[1] * d2[0]
            k = ((bx - ax) * -d2[1] + (bz - az) * d2[0]) / det
            return (ax + k * d1[0], az + k * d1[1])
        inner = [(P[0][0] + t * N[0][0], P[0][1] + t * N[0][1]), hit(1), hit(2), (P[3][0] + t * N[2][0], P[3][1] + t * N[2][1])]
        prof = Face(Wire.make_polygon([Vector(x, -100, z) for x, z in P + inner[::-1]], close=True))
        part = extrude(prof, amount=200, dir=(0, 1, 0))
        w0, w1 = (10, 60) if kind == "taper_lip" else (30, 30)
        plan = Face(Wire.make_polygon([Vector(-5, -w0, -20), Vector(P[3][0] + 5, -w1, -20), Vector(P[3][0] + 5, w1, -20), Vector(-5, w0, -20)], close=True))
        part = part & extrude(plan, amount=120, dir=(0, 0, 1))
    else:
        raise SystemExit(f"unknown kind {kind}")
    export_step(part, out)
    return {"ok": True}


def refold(step, t, R, K, mode="none"):
    """unfold.unfold the part, then fold its flat back again (as the generator does); mode breaks the flat first:
    missing = leg 1 cut out of the blank, mirrored = leg 1 turned end for end, shifted = leg 1 moved 5 mm along its bend (a wrong bend allowance)."""
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "scripts"))
    import unfold
    from build123d import Location, Plane, import_step
    sol = import_step(step)
    sol = sol.solids()[0] if sol.solids() else sol
    u = unfold.unfold(sol, float(t), float(R), float(K))
    flat, F, ctr = u["flat"], u["F"], None
    piece = F[1] * u["trim"][1]
    if mode == "missing":
        flat = flat - piece
    elif mode in ("mirrored", "shifted"):
        e = u["bends"][0]["strip"].center() - piece.center()
        e = e.normalized()
        if mode == "shifted":
            moved = Location((e.X * -5, e.Y * -5, e.Z * -5)) * piece
        else:     # mirror across the plane through the piece centre, normal along the line from the strip to the piece
            moved = piece.mirror(Plane(origin=piece.center(), z_dir=tuple(e)))
        flat = flat - piece + moved
    ref, straight, inside = unfold.refold(sol, flat, F, u["region"], u["trim"], u["bends"])
    v_expect = u["v_expect"]
    return {"legs": u["legs"], "bends": len(u["bends"]), "v_err": abs(ref.volume - v_expect) / v_expect,
            "inside": inside / straight if straight else 0.0}


def old_format(src, out):
    import ezdxf
    s = ezdxf.readfile(src)
    doc = ezdxf.new("R2010")
    for name in ("OUTLINE", "HOLES", "BEND", "NOTE"):
        doc.layers.add(name, linetype="DASHED" if name == "BEND" else "Continuous")
    m = doc.modelspace()
    sm = s.modelspace()
    for e in sm.query("LWPOLYLINE"):
        m.add_lwpolyline(list(e.get_points("xyb")), format="xyb", close=e.closed, dxfattribs={"layer": "OUTLINE"})
    for e in sm.query("CIRCLE"):
        m.add_circle(e.dxf.center, e.dxf.radius, dxfattribs={"layer": "HOLES"})
    for e in sm.query("LINE"):
        m.add_line(e.dxf.start, e.dxf.end, dxfattribs={"layer": "OUTLINE"})
    m.add_line((10, 0), (10, 50), dxfattribs={"layer": "BEND"})
    m.add_text("B1 UP 90 R1", dxfattribs={"layer": "BEND", "height": 3}).set_placement((12, 5))
    m.add_text("PART", dxfattribs={"layer": "NOTE", "height": 3}).set_placement((20, 20))
    doc.saveas(out)
    return {"ok": True}


def open_contour(src, out):
    import ezdxf
    doc = ezdxf.readfile(src)
    polys = sorted(doc.modelspace().query("LWPOLYLINE"), key=lambda e: -len(e))
    if not polys:
        raise SystemExit("no LWPOLYLINE to open")
    biggest = max(polys, key=lambda e: max(p[0] for p in e.get_points("xy")) - min(p[0] for p in e.get_points("xy")))
    biggest.closed = False
    pts = list(biggest.get_points("xyb"))
    biggest.set_points(pts[:-1], format="xyb")          # drop the last vertex too: a real gap, not just a flag
    doc.saveas(out)
    return {"ok": True}


def strip_flat_dims(src, out):
    import pymupdf
    doc = pymupdf.open(src)
    page = doc[0]
    hits = page.search_for("**")
    for r in hits:
        page.add_redact_annot(r)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)
    doc.save(out)
    return {"removed": len(hits), "left": page.get_text().count("**")}


def drop_page(src, out, idx):
    import pymupdf
    doc = pymupdf.open(src)
    doc.delete_page(int(idx))
    doc.save(out)
    return {"pages": len(doc)}


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    print(json.dumps({"pdf": pdf, "dxf": dxf, "make": make, "old_format": old_format, "open_contour": open_contour,
                      "strip_flat_dims": strip_flat_dims, "drop_page": drop_page, "refold": refold}[cmd](*args), ensure_ascii=False))
