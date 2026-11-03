"""Sheet-metal shop drawing PDF to the client's drawing standard (bend labels, a readable scale, weld seams, the thickness on a view):
  sheet 1 = flat pattern (bend lines dash-dot, a label along each line: direction, bend angle, angle between the flanges, inner R;
            chain dims between bend lines + overall, «**» = theoretical) + front view (overall sizes) + profile along the bend axis
            (overall, flange, thickness «t *», inner R) + isometric with visible edges; NO hole positions;
  sheet 2 = holes and cut-outs: the flat pattern with every hole numbered, ordinate X / Y from the lower-left corner 0,0 (= DXF 0,0),
            hole table, summary.
Texts in info["lang"] (drawings_i18n.py). Called by shop_drawing.make_part; assembly_drawing.py uses its Sheet.
"""
import math
import textwrap
from collections import defaultdict

import ezdxf
import matplotlib
import numpy as np

import drawings_i18n as I

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
from build123d import GeomType, Vector  # noqa: E402

SHEETS = {"A2": (594, 420), "A1": (841, 594), "A0": (1189, 841)}
SCALES = (5.0, 4.0, 2.5, 2.0, 1.0, 0.5, 0.4, 0.25, 0.2, 0.1)     # small parts enlarged, never 1:10 while a bigger one fits
TXT = 5.0 / 0.3528                     # 5 mm text in pt (A1 read on a screen)
LW, THIN = 0.7, 0.25
PATTERN_MIN = 30                       # more holes of one Ø (no dim_d given) = a perforation: drawn and counted, not dimensioned
E = np.eye(3)


def num(v, nd=1):
    s = f"{v:.{nd}f}".rstrip("0").rstrip(".") if nd else f"{v:.0f}"
    return (s or "0").replace(".", ",")


def sc_txt(q):
    return f"{num(q, 2)}:1" if q >= 1 else f"1:{num(1 / q, 2)}"


class Sheet:
    def __init__(self, size):
        self.W, self.H = SHEETS[size]
        self.fig = plt.figure(figsize=(self.W / 25.4, self.H / 25.4))
        self.ax = self.fig.add_axes([0, 0, 1, 1])
        self.ax.set_xlim(0, self.W)
        self.ax.set_ylim(0, self.H)
        self.ax.set_aspect("equal")
        self.ax.axis("off")
        self.ax.add_patch(plt.Rectangle((20, 5), self.W - 25, self.H - 10, fill=False, lw=LW))

    def line(self, p, q, lw=LW, ls="-", c="k"):
        self.ax.plot([p[0], q[0]], [p[1], q[1]], lw=lw, ls=ls, color=c, solid_capstyle="butt")

    def text(self, x, y, s, rot=0, ha="center", va="center", size=TXT):
        self.ax.text(x, y, s, rotation=rot, ha=ha, va=va, fontsize=size, family="DejaVu Sans")

    def arrow(self, p, q):                          # arrow head at q pointing from p
        d = np.subtract(q, p)
        d = d / (np.hypot(*d) or 1)
        n = np.array([-d[1], d[0]])
        h = np.array(q) - 3.0 * d
        self.ax.fill([q[0], h[0] + 0.6 * n[0], h[0] - 0.6 * n[0]], [q[1], h[1] + 0.6 * n[1], h[1] - 0.6 * n[1]], color="k", lw=0)

    def dim(self, p, q, off, label, horiz=True):
        """linear dim between paper points p, q; off = distance of the dim line from p/q (signed, perpendicular)"""
        if horiz:
            y = p[1] + off
            self.line(p, (p[0], y + math.copysign(2, off)), THIN)
            self.line(q, (q[0], y + math.copysign(2, off)), THIN)
            self.line((p[0], y), (q[0], y), THIN)
            self.arrow((q[0], y), (p[0], y))
            self.arrow((p[0], y), (q[0], y))
            short = abs(q[0] - p[0]) < len(label) * 2.9 + 4          # no room between the arrows: label beside the right end
            self.text(max(p[0], q[0]) + 2 if short else (p[0] + q[0]) / 2, y + 2.6, label, va="bottom", ha="left" if short else "center")
        else:
            x = p[0] + off
            self.line(p, (x + math.copysign(2, off), p[1]), THIN)
            self.line(q, (x + math.copysign(2, off), q[1]), THIN)
            self.line((x, p[1]), (x, q[1]), THIN)
            self.arrow((x, q[1]), (x, p[1]))
            self.arrow((x, p[1]), (x, q[1]))
            short = abs(q[1] - p[1]) < len(label) * 2.9 + 4
            self.text(x - 2.6, max(p[1], q[1]) + 2 if short else (p[1] + q[1]) / 2, label, rot=90, ha="right",
                      va="bottom" if short else "center")

    def title(self, info, sheet_no, sheets, scale):
        x0, y0, w, h = self.W - 275, 5, 270, 55
        self.ax.add_patch(plt.Rectangle((x0, y0), w, h, fill=False, lw=LW))
        for yy in (y0 + 15, y0 + 35):
            self.line((x0 + 70, yy), (x0 + w, yy), THIN)
        self.line((x0 + 70, y0), (x0 + 70, y0 + h), THIN)
        self.line((x0 + 200, y0 + 15), (x0 + 200, y0 + 35), THIN)
        name = info["title"]
        self.text(x0 + 170, y0 + 45, name, size=TXT * (1.0 if len(name) < 40 else 0.8 * min(1.0, 60 / len(name))))
        mt = info["material_line"]
        self.text(x0 + 135, y0 + 25, mt, size=TXT * min(0.75, 0.75 * 48 / max(len(mt), 1)))
        lang = info.get("lang", I.DEFAULT)
        self.text(x0 + 235, y0 + 25, I.t("title.scale", lang, scale=sc_txt(scale)), size=TXT * 0.8)
        self.text(x0 + 170, y0 + 8, I.t("title.sheet", lang, no=sheet_no, of=sheets), size=TXT * 0.9)
        self.text(x0 + 35, y0 + 45, I.t("title.qty", lang, qty=info["qty"]), size=TXT * 0.9)
        if info.get("code"):
            self.text(x0 + 35, y0 + 25, info["code"], size=TXT * min(0.9, 0.9 * 14 / max(len(info["code"]), 1)))


def best_scale(fit):
    for s in SCALES:
        if fit(s):
            return s
    return SCALES[-1]


# ---------------------------------------------------------------- geometry of the folded part
def view_edges(shape, look, up):
    """visible edges (hidden-line removal), camera on the +look side, orthographic -> segments in the view plane, origin = view min.
    build123d's camera frame: Z = look (towards the viewer), Y = up, so screen X = up x look."""
    c = shape.bounding_box().center()
    d = 4 * shape.bounding_box().diagonal
    vis, _hid = shape.project_to_viewport(tuple(c + Vector(*look).normalized() * d), tuple(up), tuple(c))
    seg = []
    for e in vis:
        n = 2 if e.geom_type == GeomType.LINE else 24
        q = [e.position_at(u) for u in np.linspace(0, 1, n)]
        seg += [[(a.X, a.Y), (b.X, b.Y)] for a, b in zip(q[:-1], q[1:])]
    seg = np.array(seg) if seg else np.zeros((0, 2, 2))
    lo = seg.reshape(-1, 2).min(0) if len(seg) else np.zeros(2)
    return seg - lo, lo


# ---------------------------------------------------------------- welds
def weld_seams(shape, t):
    """corner seams: the relief gap at a flange corner = a flange end face (t wide) facing another flange face 0.3..2.5 mm away"""
    allpl = [f for f in shape.faces() if f.geom_type == GeomType.PLANE]
    seams = []
    for f in allpl:
        bb = f.bounding_box()
        dims = sorted([bb.size.X, bb.size.Y, bb.size.Z])
        if not (abs(dims[1] - t) < 0.05 and dims[0] < 0.01):
            continue
        n, c = f.normal_at(), f.center()
        for g in allpl:
            if g is f or g.normal_at().dot(n) > -0.99:
                continue
            d = (g.center() - c).dot(n)
            if 0.3 < d < 2.5 and g.distance_to(c) < 3.0:
                seams.append((c + n * (d / 2), dims[2], bb))
                break
    return seams


# ---------------------------------------------------------------- the drawing
def flat_doc(face, edges_fn):
    """the flat face (already at 0,0) as an in-memory DXF: OUTLINE + HOLES entities, read back as plain segments / arcs / circles"""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    edges_fn(msp, face.outer_wire().edges(), "OUTLINE")
    for w in face.inner_wires():
        edges_fn(msp, w.edges(), "HOLES")
    outline = [((e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)) for e in msp if e.dxftype() == "LINE" and e.dxf.layer == "OUTLINE"]
    arcs = [e for e in msp if e.dxftype() in ("ARC", "LWPOLYLINE") and e.dxf.layer == "OUTLINE"]
    arcs += [e for e in msp if e.dxftype() == "CIRCLE" and e.dxf.layer == "OUTLINE"]      # a round blank (disc, washer)
    holes = [(e.dxf.center.x, e.dxf.center.y, 2 * e.dxf.radius) for e in msp if e.dxftype() == "CIRCLE" and e.dxf.layer == "HOLES"]
    hole_lines, hole_arcs = [], []
    for e in msp:
        if e.dxf.layer != "HOLES" or e.dxftype() == "CIRCLE":
            continue
        if e.dxftype() == "LINE":
            hole_lines.append(((e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)))
        else:
            pts = list(e.flattening(0.2)) if e.dxftype() == "ARC" else [ezdxf.math.Vec3(p[0], p[1], 0) for p in e.get_points("xy")]
            hole_arcs += [((a.x, a.y), (b.x, b.y)) for a, b in zip(pts[:-1], pts[1:])]
    return outline, arcs, holes, hole_lines, hole_arcs


def draw(pdf_path, shape, face, bends, t, R, info, edges_fn, taps=(), png=False):
    """shape = the finished part in its own frame: the biggest flat face in XY, outward +Z, X along its longest straight edge.
    -> (scale text, n holes, warnings). bends = [({"no", "up", "angle"}, (x0, y0), (x1, y1))] in the flat face frame (min corner 0,0).
    info: title (the part's name for the title block), material_line, qty, code, weld (False / True / text), notes (extra lines),
    dim_d (hole Ø list that get positions on sheet 2; None = all, except a Ø with more than PATTERN_MIN holes), lang (texts)"""
    warns = []
    lang = info.get("lang", I.DEFAULT)
    outline, arcs, holes, hole_lines, hole_arcs = flat_doc(face, edges_fn)
    fb = face.bounding_box()
    fx0, fy0, fx1, fy1 = fb.min.X, fb.min.Y, fb.max.X, fb.max.Y
    FW, FH = fx1 - fx0, fy1 - fy0
    bb = shape.bounding_box()
    off = bb.min
    ext = np.array([bb.size.X, bb.size.Y, bb.size.Z])
    bent = bool(bends)

    def draw_flat(sh, ox, oy, s, labels=False):
        P = lambda x, y: (ox + (x - fx0) * s, oy + (y - fy0) * s)          # noqa: E731
        for p, q in outline:
            sh.line(P(*p), P(*q))
        for e in arcs:
            pts = list(e.flattening(0.2)) if e.dxftype() in ("ARC", "CIRCLE") else [ezdxf.math.Vec3(p[0], p[1], 0) for p in e.get_points("xy")]
            for a, b in zip(pts[:-1], pts[1:]):
                sh.line(P(a[0], a[1]), P(b[0], b[1]))
        for x, y, d in holes:
            sh.ax.add_patch(plt.Circle(P(x, y), d / 2 * s, fill=False, lw=THIN * 1.6))
        for p, q in hole_lines + hole_arcs:                                   # non-round cut-outs
            sh.line(P(*p), P(*q), THIN * 1.6)
        for b, p, q in bends:
            sh.line(P(*p), P(*q), THIN * 1.4, ls=(0, (12, 3, 2, 3)))
            if not labels:
                ang = math.degrees(math.atan2(q[1] - p[1], q[0] - p[0]))
                ang = (ang + 90) % 180 - 90                                   # text along the bend line, never upside down
                nx, ny = -math.sin(math.radians(ang)), math.cos(math.radians(ang))
                mx, my = P((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
                way = I.t("dir.up" if b["up"] else "dir.down", lang)        # three short lines: a long one is clipped by the sheet
                turn = I.t("bend.turn", lang, angle=b["angle"])
                between = I.t("bend.between", lang, between=180 - b["angle"])
                sh.text(mx + 3 * nx, my + 3 * ny, f"{way}, {turn}\n({between})\nR {num(R, 2)}",
                        rot=ang, ha="center", va="bottom", size=TXT)
        return P

    # ---------------- sheet 1
    # front view looks at the base face (+Z, right X, up Y); the profile looks along the bend lines: a flange facing ±X bends about Y
    # -> profile under the front view (right X, up Z); flanges facing ±Y -> profile right of it (right Z, up Y)
    cyl_x = cyl_y = face_x = face_y = 0.0                    # the inner-R bend cylinders decide (the R leader needs one), else the flanges
    for f in shape.faces():
        if f.geom_type == GeomType.PLANE:
            n = f.normal_at()
            face_x += f.area * (abs(n.X) > 0.99)
            face_y += f.area * (abs(n.Y) > 0.99)
        elif f.geom_type == GeomType.CYLINDER and abs(f.radius - R) < 0.05:
            d = f.axis_of_rotation.direction
            cyl_x += f.area * (abs(d.X) > 0.99)
            cyl_y += f.area * (abs(d.Y) > 0.99)
    pax = (0 if cyl_x > cyl_y else 1) if cyl_x + cyl_y else (0 if face_y > face_x else 1)
    pr_r, pr_u = (E[2], E[1]) if pax == 0 else (E[0], E[2])
    front_w, front_h = ext[0], ext[1]
    prof_w, prof_h = ext @ pr_r, ext @ pr_u

    def fit1(sz):
        W_, H_ = SHEETS[sz]
        if pax == 1:
            return best_scale(lambda s: (FW + front_w) * s + 120 <= W_ - 40 and max(FH, front_h + prof_h + 40 / s) * s + 200 <= H_)
        return best_scale(lambda s: (FW + front_w + prof_w) * s + 160 <= W_ - 40 and max(FH, front_h) * s + 200 <= H_)
    size = "A1" if max(FW, FH, *ext) * 0.5 < 450 else "A0"
    if size == "A1" and fit1("A0") >= 2 * fit1("A1"):                        # a small part goes big, not 1:1 lost on A1
        size = "A0"
    sh = Sheet(size)
    s = fit1(size)
    fx, fy = 50, sh.H - 40 - FH * s
    P = draw_flat(sh, fx, fy, s)
    bx = sorted({round(p[0], 3) for _, p, q in bends if abs(p[0] - q[0]) < 1e-3})
    by_ = sorted({round(p[1], 3) for _, p, q in bends if abs(p[1] - q[1]) < 1e-3})
    star = " **" if bent else ""
    chain = [fx0] + bx + [fx1]
    if len(chain) > 2:
        for a, b in zip(chain[:-1], chain[1:]):
            sh.dim(P(a, fy1), P(b, fy1), 10, num(b - a) + star)
    sh.dim(P(fx0, fy1), P(fx1, fy1), 22 if len(chain) > 2 else 10, num(FW) + star)
    chain_y = [fy0] + by_ + [fy1]
    if len(chain_y) > 2:
        for a, b in zip(chain_y[:-1], chain_y[1:]):
            sh.dim(P(fx0, a), P(fx0, b), -10, num(b - a) + star, horiz=False)
    sh.dim(P(fx0, fy0), P(fx0, fy1), -22 if len(chain_y) > 2 else -10, num(FH) + star, horiz=False)
    sh.text(fx + FW * s / 2, fy - 8, I.t("flat.bent" if bent else "flat.plain", lang))

    gx, gy = fx + FW * s + 70, sh.H - 40 - front_h * s
    seg, _ = view_edges(shape, E[2], E[1])
    for a, b in seg:
        sh.line((gx + a[0] * s, gy + a[1] * s), (gx + b[0] * s, gy + b[1] * s), LW * 0.8)
    sh.dim((gx, gy + front_h * s), (gx + front_w * s, gy + front_h * s), 10, num(front_w))
    if pax == 1:
        sh.dim((gx + front_w * s, gy), (gx + front_w * s, gy + front_h * s), 12, num(front_h), horiz=False)
    else:                                                                    # the profile stands to the right: height on the left
        sh.dim((gx, gy), (gx, gy + front_h * s), -10, num(front_h), horiz=False)

    px, py = (gx, gy - 40 - prof_h * s) if pax == 1 else (gx + front_w * s + 40, gy)
    seg, _ = view_edges(shape, np.cross(pr_r, pr_u), pr_u)              # camera Z = look = r x u: screen X = u x (r x u) = r
    for a, b in seg:
        sh.line((px + a[0] * s, py + a[1] * s), (px + b[0] * s, py + b[1] * s), LW)
    sh.dim((px, py), (px + prof_w * s, py), -10, num(prof_w))
    sh.dim((px + prof_w * s, py), (px + prof_w * s, py + prof_h * s), 12, num(prof_h), horiz=False)

    def V2(v):                                                               # 3D point -> profile view paper point
        r = np.array([v.X - off.X, v.Y - off.Y, v.Z - off.Z])
        return px + (r @ pr_r) * s, py + (r @ pr_u) * s

    def V2f(v):                                                              # 3D point -> front view paper point
        r = np.array([v.X - off.X, v.Y - off.Y, v.Z - off.Z])
        return gx + r[0] * s, gy + r[1] * s

    # thickness «t *» on a straight leg of the profile, inner R on a bend (cylinder radius R = the inner arc)
    look = Vector(*E[pax])
    pl = [f for f in shape.faces() if f.geom_type == GeomType.PLANE and abs(f.normal_at().dot(look)) < 0.01]
    if pl:
        f_ = max(pl, key=lambda f: f.area)
        n3, c3 = f_.normal_at(), f_.center()
        n2 = np.array([n3.dot(Vector(*pr_r)), n3.dot(Vector(*pr_u))])
        pa = np.array(V2(c3))
        pb = pa - n2 * t * s
        d2 = (pb - pa) / (np.hypot(*(pb - pa)) or 1)
        sh.line(pa - d2 * 14, pa, THIN)
        sh.arrow(pa - d2 * 14, pa)
        sh.line(pb + d2 * 14, pb, THIN)
        sh.arrow(pb + d2 * 14, pb)
        lab = pa - d2 * 16
        sh.text(lab[0], lab[1] + 2.5, f"{num(t, 2)} *", ha="center", va="bottom")
    else:
        warns.append("no flat leg seen edge-on in the profile: thickness not dimensioned on a view")

    def cyl_ok(f):
        try:
            return f.geom_type == GeomType.CYLINDER and abs(f.radius - R) < 0.05 and abs(f.axis_of_rotation.direction.dot(look)) > 0.99
        except Exception:                                                     # noqa: BLE001
            return False
    if bent:
        cy = [f for f in shape.faces() if cyl_ok(f)]
        if cy:
            pc = np.array(V2(max(cy, key=lambda f: V2(f.position_at(0.5, 0.5))[0]).position_at(0.5, 0.5)))
        else:                                                                # sharp model: the profile's inner corner
            pc = np.array((px + prof_w * s, py + prof_h * s))
            other = sorted({round(f.radius, 2) for f in shape.faces() if f.geom_type == GeomType.CYLINDER})
            warns.append(f"no R{num(R, 2)} bend cylinder in the model (cylinders: {other[:6]}): give the model's inner R"
                         if other else "model has sharp bends: R leader at the profile corner")
        lp = pc + np.array([-22, 14])
        sh.line(lp, pc, THIN)
        sh.arrow(lp, pc)
        sh.text(lp[0] - 1, lp[1] + 1, f"R{num(R, 2)}", ha="right", va="bottom")

    # weld seams
    weld = info.get("weld")
    seams = weld_seams(shape, t) if weld else []
    for k, (c_, L_, bb_) in enumerate(seams):
        dv = Vector(*E[int(np.argmax([bb_.size.X, bb_.size.Y, bb_.size.Z]))]) * (L_ / 2)
        for proj, x_mid in ((V2f, gx + front_w * s / 2), (V2, px + prof_w * s / 2)):
            pa, pb = np.array(proj(c_ - dv)), np.array(proj(c_ + dv))
            if np.hypot(*(pb - pa)) < 0.8:
                sh.ax.add_patch(plt.Circle(pa, 1.2, color="k"))
            else:
                sh.line(pa, pb, LW * 3.0)
            pm = (pa + pb) / 2
            side = 1 if pm[0] > x_mid else -1
            lp = pm + np.array([side * 18, 16 + 7 * k])
            sh.line(pm, lp, THIN)
            sh.arrow(lp, pm)
            sh.line(lp, lp + np.array([side * 22, 0]), THIN)
            sh.text(lp[0] + side * 11, lp[1] + 1.2, f"{I.t('weld.mark', lang)} ⊿1", va="bottom", size=TXT * 0.85)

    # isometric, bottom left: visible edges of the part (bend and thickness edges show)
    segi, _ = view_edges(shape, (1, 1, 1), (0, 0, 1))
    if len(segi):
        hi = segi.reshape(-1, 2).max(0)
        box_w, box_h = (gx - 80 if pax == 1 else sh.W - 360), fy - 60    # never under the profile stacked below the front view
        si = min(box_w / max(hi[0], 1e-6), box_h / max(hi[1], 1e-6), s)
        for a, b in segi:
            sh.line((60 + a[0] * si, 30 + a[1] * si), (60 + b[0] * si, 30 + b[1] * si), THIN * 1.6)

    notes = [I.t("note.ref", lang)] + ([I.t("note.theor", lang)] if bent else []) + \
        [I.t("note.tol", lang), I.t("note.qty", lang, qty=info["qty"]), I.t("note.holes_sheet2", lang)]
    if not (holes or hole_lines or hole_arcs):
        notes[-1] = I.t("note.no_holes", lang)
    if weld:
        txt = weld if isinstance(weld, str) else I.t("weld.default", lang)
        where = I.t("weld.where", lang, n=len(seams)) if seams else ""
        notes.append(I.t("weld.note", lang, mark=I.t("weld.mark", lang), where=where, text=txt)
                     + (I.t("weld.zinc", lang) if I.is_zinc(info["material_line"]) else "."))
        if not seams and not isinstance(weld, str):
            warns.append("weld: true but no corner seam found: say where to weld (weld = text)")
    notes += list(info.get("notes") or [])
    lines = []
    for i, nt in enumerate(notes):
        lines += textwrap.wrap(f"{i + 1}.  {nt}", 95, subsequent_indent="     ")
    for ln, part in enumerate(lines):                                        # bottom line just above the title block, growing up
        sh.text(sh.W - 300, 66 + (len(lines) - 1 - ln) * 7, part, ha="left")
    sh.title(info, 1, 2, s)

    # ---------------- sheet 2: holes and cut-outs, every centre / window corner / notch vertex from 0,0
    from shapely.geometry import LineString
    from shapely.ops import polygonize
    wins = [g.bounds for g in polygonize([LineString([p, q]) for p, q in hole_lines + hole_arcs])]
    count = defaultdict(int)
    for x, y, d in holes:
        count[round(d, 2)] += 1
    dd = info.get("dim_d")                                                   # e.g. furniture: only screw holes dimensioned, the rest is a pattern
    dimmed = (lambda d: any(abs(d - x) < 0.05 for x in dd)) if dd is not None else (lambda d: count[round(d, 2)] <= PATTERN_MIN)
    pattern = defaultdict(int)
    for x, y, d in holes:
        if not dimmed(d):
            pattern[round(d, 2)] += 1
    hs = sorted([h for h in holes if dimmed(h[2])], key=lambda h: (round(h[0] - fx0, 1), round(h[1] - fy0, 1)))
    r1 = lambda v: round(v, 1)                                               # noqa: E731
    vx, vy = set(), set()
    for x, y, d in hs:
        vx.add(r1(x - fx0))
        vy.add(r1(y - fy0))
    for x0_, y0_, x1_, y1_ in wins:
        vx |= {r1(x0_ - fx0), r1(x1_ - fx0)}
        vy |= {r1(y0_ - fy0), r1(y1_ - fy0)}
    arc_pts = []
    for e in arcs:
        if e.dxftype() == "ARC":
            c_ = e.dxf.center
            arc_pts.append((c_.x, c_.y, e.dxf.radius, e))
            vx.add(r1(c_.x - fx0))
            vy.add(r1(c_.y - fy0))
            for pt in (e.start_point, e.end_point):
                vx.add(r1(pt.x - fx0))
                vy.add(r1(pt.y - fy0))
    for p, q in outline:                                                     # notch / relief vertices (not the plain rectangle)
        for x, y in (p, q):
            if 0.05 < x - fx0 < FW - 0.05:
                vx.add(r1(x - fx0))
            if 0.05 < y - fy0 < FH - 0.05:
                vy.add(r1(y - fy0))
    hx, hy = {r1(x - fx0) for x, y, d in hs}, {r1(y - fy0) for x, y, d in hs}
    ux = sorted(v for v in vx if 0.05 < v < FW - 0.05 or v in hx)
    uy = sorted(v for v in vy if 0.05 < v < FH - 0.05 or v in hy)
    STEP = 9 if max(len(ux), len(uy)) <= 16 else 6.5
    rows = max(14, math.ceil(len(hs) / 3))
    ncol = max(1, math.ceil(len(hs) / rows))

    def fits(sz, q):
        W_, H_ = SHEETS[sz]
        return 40 + STEP * (len(uy) + 1) + FW * q + 30 + 130 * ncol <= W_ - 20 and 50 + FH * q + 12 + STEP * (len(ux) + 1) <= H_ - 80
    size2 = next((sz for sz in ("A1", "A0") if any(fits(sz, q) for q in SCALES)), "A0")
    sh2 = Sheet(size2)
    s2 = best_scale(lambda q: fits(size2, q))
    fx2, fy2 = 40 + STEP * (len(uy) + 1), sh2.H - 50 - FH * s2
    P2 = draw_flat(sh2, fx2, fy2, s2, labels=True)
    sh2.text(fx2 + FW * s2 / 2, sh2.H - 25, I.t("holes.title", lang, flat=I.t("holes.title_flat", lang) if bent else ""))
    for i, (x, y, d) in enumerate(hs):
        p = P2(x, y)
        sh2.text(p[0] + 2.5, p[1] + 2.5, f"{i + 1}", ha="left", va="bottom", size=TXT * 0.8)
    for k, v in enumerate(ux):
        sh2.dim(P2(fx0, fy0), P2(fx0 + v, fy0), -(10 + STEP * k), num(v))
    for k, v in enumerate(uy):
        sh2.dim(P2(fx0, fy0), P2(fx0, fy0 + v), -(10 + STEP * k), num(v), horiz=False)
    ext_pts = [(x, y) for x, y, d in hs] + [(a, b) for a, b, _, _ in wins] + [(c, d) for _, _, c, d in wins] \
        + [(c[0], c[1]) for c in arc_pts] + [pt for seg_ in outline for pt in seg_]
    for x, y in ext_pts:                                                     # thin extension lines to the dim rows
        p = P2(x, y)
        if r1(x - fx0) in ux:
            sh2.line(p, (p[0], fy2), THIN * 0.6, ls=(0, (4, 2)))
        if r1(y - fy0) in uy:
            sh2.line(p, (fx2, p[1]), THIN * 0.6, ls=(0, (4, 2)))
    for x0_, y0_, x1_, y1_ in wins:
        p = P2(x1_, y1_)
        sh2.text(p[0] + 1.5, p[1] + 1.5, f"{num(x1_ - x0_)}x{num(y1_ - y0_)}", ha="left", va="bottom", size=TXT * 0.8)
    for cx_, cy_, r_, e in arc_pts:
        a_m = math.radians(e.dxf.start_angle + (e.dxf.end_angle - e.dxf.start_angle) % 360 / 2)
        p = P2(cx_ + r_ * math.cos(a_m), cy_ + r_ * math.sin(a_m))
        sh2.text(p[0] + 2, p[1] + 2, f"R{num(r_, 2)}", ha="left", va="bottom", size=TXT * 0.85)
    sh2.text(fx2 - 3, fy2 - 3, "0", ha="right", va="top", size=TXT * 0.8)
    tap = {round(float(d), 2): name for d, name in taps}
    tx, ty = fx2 + FW * s2 + 30, fy2 + FH * s2 - 10
    for c in range(ncol if hs else 0):
        for j, hd in enumerate(I.t("holes.cols", lang)):
            sh2.text(tx + c * 130 + j * 30, ty, hd, ha="right" if j else "left")
    for i, (x, y, d) in enumerate(hs):
        c, r = divmod(i, rows)
        dd = num(d, 2) + (f" {tap[round(d, 2)]}" if round(d, 2) in tap else "")
        for j, v in enumerate((f"{i + 1}", num(x - fx0), num(y - fy0), dd)):
            sh2.text(tx + c * 130 + j * 30, ty - 7.5 * (r + 1), v, ha="right" if j else "left", size=TXT * 0.85)
    groups = defaultdict(int)
    for x, y, d in hs:
        groups[round(d, 2)] += 1
    summ = "; ".join(f"Ø{num(k, 2)}" + (I.t("holes.tap", lang, tap=tap[k]) if k in tap else "") + ": " + I.t("holes.count", lang, n=v)
                     for k, v in sorted(groups.items()))
    if pattern:
        summ += ("; " if summ else "") + "; ".join(f"Ø{num(k, 2)}: " + I.t("holes.perforation", lang, n=v) for k, v in sorted(pattern.items()))
    if wins:
        summ += ("; " if summ else "") + I.t("holes.cutouts", lang, n=len(wins))
    if not summ:
        summ = I.t("holes.none", lang)
    for j, part in enumerate(textwrap.wrap(I.t("holes.laser", lang, flat=I.t("holes.laser_flat", lang) if bent else "") + summ, 95)):
        sh2.text(sh2.W - 300, 80 - 7 * j, part, ha="left")
    sh2.title(info, 2, 2, s2)

    with PdfPages(pdf_path) as pdf:
        pdf.savefig(sh.fig)
        pdf.savefig(sh2.fig)
    if png:
        sh.fig.savefig(str(pdf_path)[:-4] + "_p1.png", dpi=110)
        sh2.fig.savefig(str(pdf_path)[:-4] + "_p2.png", dpi=110)
    plt.close(sh.fig)
    plt.close(sh2.fig)
    return f"{size} {sc_txt(s)}", len(hs), warns
