"""P11 pkg b: generic unfold of a sharp-corner sheet part (constant thickness t, planar legs, 90 deg bends) as drawn in the model.

Legs = slabs of thickness t found from pairs of opposite planar faces t apart; bends = non-parallel legs sharing a corner column.
Spanning tree from the biggest leg; each leg is trimmed at setback R + t from the neighbour's outer face, the bend zone becomes a
strip of width BA = pi/2 (R + K t), children are rotated flat about the bend axis. Loops (box corners) give the column to the
earlier leg (the later one butts, a corner relief of ~R + t stays). params.S1_BEND holds R/t and K.
Checks (returned): refold the flat (pieces cut back out of the fused blank + real R sectors) vs the model volume with its sharp
corners replaced by the same sectors (vol_tol), refolded straight material inside the model, hole count flat == model.
"""
import math

from build123d import Align, Box, Cylinder, Location, Plane, Vector

H = 4000.0
EPS = 0.05


def _vol(s):
    try:
        return s.volume if s is not None else 0.0
    except Exception:
        return 0.0


def _and(a, b):
    for x, y in ((a, b), (b, a)):                 # OCC sometimes returns a null shape for one operand order (STEP compounds near the origin)
        try:
            r = x & y
        except Exception:
            continue
        if _vol(r) > 1e-6:
            return r
    return None


def _cut(a, b):
    try:
        r = a - b
    except Exception:
        r = None
    return r if _vol(r) > 1e-6 else None


def half(e, s):
    """solid {e . x <= s}"""
    return Location(Plane(origin=tuple(e * s), z_dir=tuple(e))) * Box(H, H, H, align=(Align.CENTER, Align.CENTER, Align.MAX))


def slab(n, lo, hi):
    return Location(Plane(origin=tuple(n * lo), z_dir=tuple(n))) * Box(H, H, hi - lo, align=(Align.CENTER, Align.CENTER, Align.MIN))


def _canon(n):
    for c in (n.X, n.Y, n.Z):
        if abs(c) > 1e-6:
            return n if c > 0 else -n
    return n


def _ext(shape, a):
    vs = [a.dot(Vector(*v)) for v in shape.vertices()]
    return min(vs), max(vs)


def _gt(x):
    return str(getattr(x, "geom_type", x)).split(".")[-1].upper()


def find_legs(solid, t):
    pf = [f for f in solid.faces() if _gt(f) == "PLANE"]
    data = [(f, f.normal_at(f.center()), f.center()) for f in pf]
    slabs = []
    for f, n, c in data:
        d = n.dot(c)
        for g, m, cg in data:
            if n.dot(m) < -0.9999 and abs(n.dot(cg) - (d - t)) < EPS:
                nc = _canon(n)
                lo, hi = sorted((nc.dot(c), nc.dot(cg)))
                if not any(abs(s[0].dot(nc)) > 0.9999 and abs(s[1] - lo) < EPS for s in slabs):
                    slabs.append((nc, lo, hi))
                break
    legs = []
    for n, lo, hi in slabs:
        m = _and(solid, slab(n, lo, hi))
        if m is None:
            continue
        # plate-like: both in-plane sizes >= 1.5 t (drops thickness strips / end faces that happen to be t apart)
        u = Vector(1, 0, 0) if abs(n.X) < 0.9 else Vector(0, 1, 0)
        u = (u - n * u.dot(n)).normalized()
        v = n.cross(u)
        su, sv = (lambda e: e[1] - e[0])(_ext(m, u)), (lambda e: e[1] - e[0])(_ext(m, v))
        if min(su, sv) < 1.5 * t:
            continue
        # 468: disjoint pieces in one plane (two legs / two feet of the same part) are separate legs, each with its own local slab
        parts = m.solids() if len(m.solids()) > 1 else [m]
        for mm in parts:
            su_, sv_ = (lambda e: e[1] - e[0])(_ext(mm, u)), (lambda e: e[1] - e[0])(_ext(mm, v))
            if min(su_, sv_) < 1.5 * t:            # a cross-section of a perpendicular leg passing through this plane
                continue
            if len(parts) > 1:
                b = mm.bounding_box()
                box = Location(((b.min.X + b.max.X) / 2, (b.min.Y + b.max.Y) / 2, (b.min.Z + b.max.Z) / 2)) * Box(
                    b.size.X + 2 * t, b.size.Y + 2 * t, b.size.Z + 2 * t)
                S = slab(n, lo, hi) & box
            else:
                S = None
            legs.append({"n": n, "lo": lo, "hi": hi, "mid": (lo + hi) / 2, "m": mm, "vol": _vol(mm), "g": mm.center(), "S0": S})
    return legs


def refold(solid, flat, F, region, trim, bends):
    """Fold the flat blank back: each leg's region is cut out of `flat`, moved home with F[i]^-1, the real bend sectors are added.
    -> (refolded solid, straight material volume, part of it inside `solid`). A region is an infinite slab, so flat material that
    belongs to another leg (a tapered neighbour is wider than the bend column the region was trimmed to) is first taken out of it:
    only what no other leg's flat piece accounts for counts as this leg's, i.e. as extra material."""
    N = len(F)
    pieces = {i: F[i] * trim[i] for i in range(N)}
    ref = None
    straight = inside = 0.0
    for i in range(N):
        reg = F[i] * region[i]
        for j in range(N):
            if j != i:
                reg = _cut(reg, pieces[j])
                if reg is None:
                    break
        pc_ = _and(flat, reg) if reg is not None else None
        if pc_ is None:
            continue
        back = F[i].inverse() * pc_
        straight += _vol(back)
        inside += _vol(_and(back, solid))
        ref = back if ref is None else ref + back
    for b in bends:
        ref = ref + b["sector"]
    return ref, straight, inside


def unfold(solid, t, R, K):
    legs = find_legs(solid, t)
    if not legs:
        raise ValueError("no sheet legs of thickness %.2f found" % t)
    N = len(legs)
    for L in legs:
        L["S"] = L["S0"] if L["S0"] is not None else slab(L["n"], L["lo"], L["hi"])
    rest = solid
    for L in legs:
        rest = rest - L["S"]
    left = _vol(rest)
    if left > 0.005 * _vol(solid):
        b = rest.bounding_box()
        raise ValueError("%.1f %% of the volume is in no %.1f mm sheet leg (left-over %.1f x %.1f x %.1f at %.1f, %.1f, %.1f): "
                         "the model is not constant-thickness sheet there" % (100 * left / _vol(solid), t, b.size.X, b.size.Y,
                                                                               b.size.Z, b.min.X, b.min.Y, b.min.Z))
    adj = {i: [] for i in range(N)}
    col = {}
    for i in range(N):
        for j in range(i + 1, N):
            ci = abs(legs[i]["n"].dot(legs[j]["n"]))
            if ci > 0.9999:
                continue
            c = _and(legs[i]["m"], legs[j]["S"])
            touch = c is not None and _vol(c) > 0.5 * t ** 3
            if ci > 1e-3:
                # flanges on a slanted sheet edge meet the others at the corner relief only -> not a bend, skip;
                # a non-90 deg pair that really shares material is a bend of that angle
                if not touch:
                    continue
            if touch:
                adj[i].append(j)
                adj[j].append(i)
                col[(i, j)] = col[(j, i)] = c
    root = max(range(N), key=lambda i: legs[i]["vol"])
    order, parent, seen = [root], {root: None}, {root}
    k = 0
    while k < len(order):
        i = order[k]
        k += 1
        for j in sorted(adj[i], key=lambda j: -legs[j]["vol"]):
            if j not in seen:
                seen.add(j)
                parent[j] = i
                order.append(j)
    if len(seen) != N:
        raise ValueError("legs not connected (%d of %d)" % (len(seen), N))
    rank = {i: r for r, i in enumerate(order)}
    sb = R + t
    BA = math.pi / 2 * (R + K * t)

    def toward(i, j):
        """unit e in leg i's plane (= +-n_j) pointing from i's body to j's slab; j's outer / near face coordinate along e."""
        nj = legs[j]["n"]
        e = nj if legs[j]["mid"] > nj.dot(legs[i]["g"]) else -nj
        lo, hi = legs[j]["lo"], legs[j]["hi"]
        return e, (hi if e.dot(nj) > 0 else -lo), (lo if e.dot(nj) > 0 else -hi)

    def general_edge(p, c):
        """Bend between legs p (parent) and c at any angle: the same quantities the 90 deg code gets from the slab faces.
        e_p / e_c: unit directions in p's / c's plane, across the bend, from p's body to the bend and from the bend into c's body; O: where
        the outer faces meet (given as its coordinates along e_p / e_c); setback (R + t) tan(phi / 2); bend allowance phi (R + K t)."""
        np_, nc_ = legs[p]["n"], legs[c]["n"]
        cc = np_.dot(nc_)
        a = np_.cross(nc_).normalized()

        def meet(op, oc):
            return (np_ * (op - oc * cc) + nc_ * (oc - op * cc)) * (1.0 / (1.0 - cc * cc))
        M = meet(legs[p]["mid"], legs[c]["mid"])
        e_p = a.cross(np_).normalized()
        if e_p.dot(M - legs[p]["g"]) < 0:
            e_p = -e_p
        e_c = a.cross(nc_).normalized()
        if e_c.dot(legs[c]["g"] - M) < 0:
            e_c = -e_c
        sp = 1.0 if np_.dot(e_c) > 0 else -1.0             # c leaves p's plane to the +n_p side if > 0
        sc = 1.0 if nc_.dot(-e_p) > 0 else -1.0            # p lies on this side of c's plane
        O = meet(legs[p]["lo"] if sp > 0 else legs[p]["hi"], legs[c]["lo"] if sc > 0 else legs[c]["hi"])
        phi = math.acos(max(-1.0, min(1.0, e_p.dot(e_c))))
        s_ = (R + t) * math.tan(phi / 2)
        C = O - e_p * s_ + np_ * (sp * (R + t))
        return {"e_p": e_p, "e_c": e_c, "x_out": e_p.dot(O), "y_out": e_c.dot(O), "s": s_, "BA": phi * (R + K * t), "C": C,
                "u1": np_ * (-sp), "u2": nc_ * (-sc), "angle": math.degrees(phi)}

    keep = {i: [] for i in range(N)}       # half-spaces (e, s): keep e.x <= s
    edges = []
    for c in order[1:]:
        p = parent[c]
        if abs(legs[p]["n"].dot(legs[c]["n"])) < 1e-3:
            e_p, x_out, _ = toward(p, c)
            e_c, y_far, _ = toward(c, p)      # e_c from c's body toward p; p's outer face is the far one along -e_c
            e_c = -e_c                        # now from p into c's body
            y_out = -y_far                    # p's outer face coordinate along e_c
            ed = {"e_p": e_p, "e_c": e_c, "x_out": x_out, "y_out": y_out, "s": sb, "BA": BA, "angle": 90.0}
        else:
            ed = general_edge(p, c)
            e_p, e_c, x_out, y_out = ed["e_p"], ed["e_c"], ed["x_out"], ed["y_out"]
        a_ = e_p.cross(e_c).normalized()
        ar = (a_,) + _ext(col[(p, c)], a_)                # trim only along the bend's length (partial flanges keep the rest)
        keep[p].append((e_p, x_out - ed["s"], ar, False))
        keep[c].append((-e_c, -(y_out + ed["s"]), ar, True))
        edges.append(dict(ed, p=p, c=c))
    for i in range(N):                    # loops: the later leg butts against the earlier one's column
        for j in adj[i]:
            if parent.get(i) == j or parent.get(j) == i or rank[i] < rank[j]:
                continue
            e, _, near = toward(i, j)
            keep[i].append((e, near, None, False))
    trim = {}
    region = {}
    for i in range(N):
        s = legs[i]["m"]
        r = legs[i]["S"]
        for e, sv, ar, child in keep[i]:
            if ar is None:
                hs = half(e, sv)
                s = _and(s, hs)
                r = r & hs
            else:
                cut = half(-e, -sv) & slab(*ar)
                s = _cut(s, cut)
                # refold region: the child's is its whole side of the bend (else, in the flat, it would also take the parent's
                # material outside a partial flange's length); the parent's loses only the bend's length
                r = (r & half(e, sv)) if child else (r - cut)
            if s is None:
                raise ValueError("a %.1f mm-thick leg (%.1f x %.1f x %.1f) is shorter than the setback R + t = %.1f: not bendable as drawn"
                                 % ((t,) + tuple(round(x, 1) for x in legs[i]["m"].bounding_box().size) + (sb,)))
        trim[i], region[i] = s, r

    def frame(e, a, n, mid, along):
        w = a.cross(e)
        return Plane(origin=tuple(e * along + w * (mid * n.dot(w))), x_dir=tuple(e), z_dir=tuple(w))

    nr = legs[root]["n"]
    u = Vector(1, 0, 0) if abs(nr.X) < 0.9 else Vector(0, 1, 0)
    u = (u - nr * u.dot(nr)).normalized()
    F = {root: Location(Plane(origin=tuple(nr * legs[root]["mid"]), x_dir=tuple(u), z_dir=tuple(nr))).inverse()}
    bends = []
    for ed in edges:
        p, c, e_p, e_c = ed["p"], ed["c"], ed["e_p"], ed["e_c"]
        a = e_p.cross(e_c).normalized()
        pc = frame(e_c, a, legs[c]["n"], legs[c]["mid"], ed["y_out"] + ed["s"])
        pt = frame(e_p, a, legs[p]["n"], legs[p]["mid"], ed["x_out"] - ed["s"] + ed["BA"])
        F[c] = F[p] * Location(pt) * Location(pc).inverse()
        e0 = [max(x) for x in zip(_ext(col[(p, c)], a), _ext(trim[p], a), _ext(trim[c], a))][0]
        e1 = [min(x) for x in zip(_ext(col[(p, c)], a), _ext(trim[p], a), _ext(trim[c], a))][1]
        if e1 - e0 < t:
            raise ValueError("bend %d-%d has no width" % (p, c))
        pb = frame(e_p, a, legs[p]["n"], legs[p]["mid"], ed["x_out"] - ed["s"])
        BAe = ed["BA"]
        strip = Location(pb) * Location((BAe / 2, -(e0 + e1) / 2, 0)) * Box(BAe, e1 - e0, t)
        o = Vector(*(F[p] * Location(pb) * Location((BAe / 2, -e0, 0))).position)
        o2 = Vector(*(F[p] * Location(pb) * Location((BAe / 2, -e1, 0))).position)
        up = Vector(*(F[p] * Location(tuple(e_c))).position) - Vector(*(F[p] * Location((0, 0, 0))).position)
        # real bend: annulus R..R+t round the axis through the centre (x_out - sb along e_p, y_out + sb along e_c)
        if "C" in ed:
            C = ed["C"] - a * a.dot(ed["C"])
        else:
            C = e_p * (ed["x_out"] - sb) + e_c * (ed["y_out"] + sb)
            C = C - a * a.dot(C)
        ring = Location(Plane(origin=tuple(C + a * e0), z_dir=tuple(a))) * (
            Cylinder(R + t, e1 - e0, align=(Align.CENTER, Align.CENTER, Align.MIN))
            - Cylinder(R, e1 - e0, align=(Align.CENTER, Align.CENTER, Align.MIN)))
        if "C" in ed:                      # any angle: the wedge between the rays C -> tangent points, bounded to R + t along both
            u1, u2 = ed["u1"], ed["u2"]
            m1 = (u2 - u1 * u1.dot(u2)).normalized()
            m2 = (u1 - u2 * u1.dot(u2)).normalized()
            quad = half(-m1, -m1.dot(C)) & half(-m2, -m2.dot(C)) & slab(a, e0, e1)
            quad = quad & half(u1, u1.dot(C) + R + t) & half(u2, u2.dot(C) + R + t)
        else:
            quad = half(-e_p, -e_p.dot(C)) & half(e_c, e_c.dot(C)) & slab(a, e0, e1)
            # plate4 hat (job 837): bound the corner to R + t round C, else "sharp" also took far legs lying in the same quadrant
            quad = quad & half(e_p, e_p.dot(C) + R + t) & half(-e_c, -e_c.dot(C) + R + t)
        sector = ring & quad
        sharp = _and(solid, quad)
        bends.append({"p": p, "c": c, "strip": F[p] * strip, "line": (o, o2), "up": up.Z > 0, "w": e1 - e0,
                      "sector": sector, "sharp": sharp, "angle": ed["angle"]})
    pieces = [F[i] * trim[i] for i in range(N)]
    flat = pieces[0]
    for s in pieces[1:] + [b["strip"] for b in bends]:
        flat = flat + s
    flat = flat.clean()
    top = max([f for f in flat.faces() if _gt(f) == "PLANE" and f.normal_at(f.center()).Z > 0.999], key=lambda f: f.area)
    # ---- refold check: cut each leg's region back out of the fused blank, move it home, add the real sectors
    ref, straight, inside = refold(solid, flat, F, region, trim, bends)
    v_model = _vol(solid)
    v_expect = v_model - sum(_vol(b["sharp"]) for b in bends) + sum(_vol(b["sector"]) for b in bends)
    v_ref = _vol(ref)
    holes_model = sum(len(f.inner_wires()) for f in solid.faces() if _gt(f) == "PLANE") // 2
    holes_flat = len(top.inner_wires())
    return {"top": top, "flat": flat, "legs": N, "bends": bends, "BA": BA, "R": R, "K": K,
            "v_model": v_model, "v_expect": v_expect, "v_ref": v_ref, "v_err": abs(v_ref - v_expect) / v_expect,
            "inside": inside / straight if straight else 0.0, "F": F, "region": region, "trim": trim, "holes_model": holes_model, "holes_flat": holes_flat}
