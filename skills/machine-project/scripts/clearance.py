#!/usr/bin/env python3
"""No two parts of a built code model share space: fixed parts where the model stands, moving ones
over the whole machine cycle. Every model build runs it; nobody has to remember to.

  python3 clearance.py <project> [--motion motion.json] [--groups groups.json]      exit 1 on a clash

Who runs it: the viewer build every site build ends with (web-viewer templates/assembly/build.py), on the project's code
model (build/model/parts.json + nodes.json, cad_incremental.py --model) with the cycle it is about to publish (--motion)
and the parts' motion groups as the viewer moves them. There, and only there, the cycle is the one the client will see:
it is made after the model (the machine's motion script reads the built model), so a check inside the model build
would measure the last build's motion. A clash does not stop the build: the site gets
clearance.json, the page shows a red mark, the platform's model-ready notice tells the agent which parts overlap, and the
pinned board shows it too.

What is a clash: two parts whose solids overlap by more than CLASH_V mm3 (OCC boolean common, as #47 and PartCAD do; fcl on
the meshes only picks the pairs and the times worth a boolean). Parts that touch share no volume and pass. Measured on a
real pneumatic machine: every touching pair gave exactly 0, every real overlap 37..1819 mm3.

The contacts a machine means to have come from its joints, never from a list of pairs:
  - two parts docked to each other (one's `joint.to` is the other): `docked parts clash` owns that pair, here it is
    not measured at all;
  - a screw (fit_checks: a male fastener that spells its thread) runs along the axis of the joint it is docked by, from its
    head's bearing face into what is behind it: an overlap that lies wholly inside its shank (d/2 + SHANK around that
    axis, over its length) is its thread or its passage through a part behind its host - the depth and the hole are
    #47 `fastener fit`'s. An overlap anywhere else, its head sunk into a part, is a clash; so is any overlap with a part
    that moves against the screw (a bar sweeping through a protruding shank mid-stroke): a thread is cut at rest.
A part marked `product` (what the machine handles: sweets in a cassette, a label stack) is not checked: it is drawn
where it stands at one instant, and it moves in the viewer's world module, not in the cycle.
Pairs: parts of one group (all the fixed ones, or the parts of one moving group) do not move against each other and are
measured where the model stands; a moving part against any part of another group is swept over the cycle (sim
cycle_clearance.Cycle, the same sweep as #10's tool) and its overlap measured at up to PICKS of its hit times.
Without a cycle every part is measured where the model stands.

Incremental: a pair's verdict is kept in build/clearance/overlaps.json (pairs.json is cycle_clearance's) under everything
it depends on (both parts as placed, their groups' motion, the screw axes involved, this code): a one-part change measures
that part's pairs only. Saved every SAVE_S as well, so a cold run killed by the job's limit keeps what it measured. Meshes
are shared with cycle_clearance's --model cache.

Not seen: two docked parts that swing into each other (a clevis turning into the plate it is pinned to): docked pairs
are #47's, at the model pose; a moving part wholly inside another without a surface crossing at any sample (where the
model stands, a box inside another box is booleaned); view-only mesh parts (a vendor unit's triangles: no boolean, reported
as not measured). A solid mesh part (meshpart.py: a printed housing) is booleaned on its triangles by manifold3d, the
STEP part against it tessellated.
"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "sim" / "scripts"))
import fit_checks as fc  # noqa: E402
import cycle_clearance as cc  # noqa: E402

CLASH_V = fc.CLASH_V   # mm3: one number for "overlap" in the framework (#47's docked pairs, here everything else)
TOL = 0.1              # mm, tessellation of the meshes fcl picks pairs with
MAX_STEP = 0.5         # mm of motion of any point between two samples of the cycle
MARGIN = 0.05          # mm: boxes farther apart than this cannot touch
SHANK = 0.5            # mm around a screw's shank and past its end that still counts as its thread / passage
PICKS = 7              # hit times of a moving pair whose overlap is measured (evenly over its hits, first and last included)
SAVE_S = 60             # s between saves of the pair cache during a run
RANK = {"clash": 3, "unmeasured": 2, "thread": 1, "touch": 0}   # which of a moving pair's picks speaks for it
SHOW = 6               # clash lines printed (the router hands the agent the last 1500 chars of the log); the rest: report.json


def _code():
    h = hashlib.sha256()
    for f in (Path(__file__), Path(cc.__file__), Path(fc.__file__), HERE / "meshpart.py"):
        h.update(f.resolve().read_bytes())
    return h.hexdigest()[:16]


def _screw_axis(byname, name):
    """(d, length, origin, direction) of a docked male fastener in model coordinates - its shank runs from origin (its head's
    bearing face, the host joint's origin) along direction (-Z of that joint) - or None."""
    p, pl = byname[name]
    j = pl.get("joint")
    if not isinstance(j, dict) or not fc._is_fastener(p):
        return None
    d, L = fc.screw_spec(p)
    host = byname.get(str(j.get("to")))
    hj = (host[0].get("joints") or {}).get(str(j.get("at"))) if host else None
    if not isinstance(hj, dict) or not hj.get("loc") or not host[1].get("loc"):
        return None
    o, z = fc._joint_axis(hj["loc"])
    return [d, L, list(fc._xform(host[1]["loc"], o)), list(fc._rotate(host[1]["loc"], tuple(-c for c in z)))]


def _inside_shank(axis, pts, M=None):
    """Every point within the shank of the screw `axis` (moved by its group's pose M)."""
    d, L, o, u = axis
    o, u = np.array(o, float), np.array(u, float)
    if M is not None:
        o, u = M[:3, :3] @ o + M[:3, 3], M[:3, :3] @ u
    pts = np.asarray(pts, float).reshape(-1, 3)
    if not len(pts):
        return False
    s = (pts - o) @ u
    radial = np.linalg.norm((pts - o) - np.outer(s, u), axis=1)
    reach = (L if L else 1e9) + SHANK
    return bool(np.all(radial <= d / 2 + SHANK) and np.all(s >= -SHANK) and np.all(s <= reach))


def _box(s):
    bb = s.bounding_box()
    return np.array([bb.min.X, bb.min.Y, bb.min.Z]), np.array([bb.max.X, bb.max.Y, bb.max.Z])


def _common(a, b):
    """(volume, [overlap pieces]) of two placed shapes, solid by solid; (None, []) when OCC could not say. A common of two
    located compounds came back as a sliver outside one of them (a vendor knob against a tube: 72.7 mm3 where the
    knob is not), so each pair of solids is booleaned on its own, and a piece outside either solid's box is not believed."""
    v, out = 0.0, []
    try:
        for sa in a.solids():
            la, ha = _box(sa)
            for sb in b.solids():
                lb, hb = _box(sb)
                if np.any(la > hb + MARGIN) or np.any(lb > ha + MARGIN):
                    continue
                c = fc._and(sa, sb)
                if c is None:
                    continue
                x = fc._vol(c)
                if x is None:
                    return None, []
                if x <= 0:
                    continue
                lc, hc = _box(c)
                if np.any(lc < np.maximum(la, lb) - 0.5) or np.any(hc > np.minimum(ha, hb) + 0.5):
                    return None, []
                v += x
                out.append(c)
    except Exception:                            # noqa: BLE001 - OCC fails on some coincident faces: the caller says so
        return None, []
    return v, out


class Solids:
    """Each placement's exact solid (its STEP, placed), read once; at a time of the cycle, moved by its group's pose."""

    def __init__(self, root, byname):
        self.root, self.byname, self.done = root, byname, {}

    def at(self, name, M):
        if name not in self.done:
            p, pl = self.byname[name]
            if p.get("mesh_solid"):
                mp = fc._meshpart()
                self.done[name] = mp.Placed.of(*mp.read(str(self.root / p["mesh"])), pl["loc"])
            elif not p.get("step"):
                self.done[name] = None
            else:
                from build123d import import_step
                s = import_step(str(self.root / p["step"]))
                self.done[name] = s.moved(fc._matrix_loc(pl["loc"]))
        s = self.done[name]
        if s is None or M is None:
            return s
        return s.moved(fc._matrix_loc(M.reshape(-1).tolist()))


def run(root, cycle=None, groups=None, tracks=None):
    """The report of a project's built model: {clashes, unmeasured, pairs, measured, cached, seconds, cycle}; also written to
    build/clearance/report.json. `cycle` = the `cycle` block of a motion.json (None: every part where the model stands),
    `groups` = {placement name: group} as the viewer moves the parts (their motion_group), `tracks` = pose tracks a world
    module applies on the page in place of a node's group ({n, period, nodes}, cycle_clearance.tracks_of: crank links)."""
    root = Path(root).resolve()
    t0 = time.time()
    model = root / "build" / "model" / "parts.json"
    out = root / "build" / "clearance"
    parts = json.loads(model.read_text())["parts"]
    byname = {}
    for p in parts:
        for pl in p.get("placements") or ():
            if isinstance(pl, dict) and pl.get("name"):
                byname[str(pl["name"])] = (p, pl)
    names, verts, tris, own, ids, pids = cc.load_model(model, cc.mesh_cache(model, out), TOL)
    cg = (cycle or {}).get("groups") or {}
    if cg:
        # the viewer's groups by exact placement name: only node_groups fall back from `screw_2` to `screw` (motion.mjs), and a
        # repeat of a part fixed to the frame must not ride with its first copy on a slider
        own = {**own, **{str(k): v for k, v in (groups or {}).items()}}
        grp = cc.assign_groups(names, pids, own, cycle.get("node_groups") or {}, cg)
    else:
        grp = ["static"] * len(names)
    period_s = float((cycle or {}).get("period_s") or 0.0)
    trk, skipped = cc.tracks_of(names, tracks, period_s) if cg else ({}, [])
    C = cc.Cycle(verts, tris, grp, cg, period_s, MAX_STEP, trk)   # sets a tracked part's group to its "track:<hash>"
    docked = set()
    for n, (p, pl) in byname.items():
        j = pl.get("joint")
        if isinstance(j, dict) and j.get("to") is not None:
            docked.add(frozenset((n, str(j["to"]))))
    axes = {n: _screw_axis(byname, n) for n in names if n in byname}

    pairs = set(C.candidates([i for i, g in enumerate(grp) if g != "static"], MARGIN))
    lo, hi = C.lo, C.hi
    for i in range(len(names)):   # the same group: where the model stands
        near = np.all(lo[i] - MARGIN <= hi[i + 1:], 1) & np.all(lo[i + 1:] - MARGIN <= hi[i], 1)
        pairs.update((i, int(j)) for j in np.nonzero(near)[0] + i + 1 if grp[int(j)] == grp[i])
    # what the machine handles (`product`, as derive.py keeps it out of the kits) is drawn where it stands at one instant
    # and moves in the viewer's world module: a pusher passing through the products drawn in its path is no clash
    product = {n for n, (p, _) in byname.items() if p.get("product")}
    pairs = sorted(p for p in pairs if frozenset((names[p[0]], names[p[1]])) not in docked
                   and names[p[0]] not in product and names[p[1]] not in product)

    code = _code()

    def pair_key(i, j):
        bits = sorted([cc.stable([ids[k], C.motion.get(k), axes.get(names[k])]) for k in (i, j)], key=json.dumps)
        return hashlib.sha256(json.dumps(cc.stable([code, bits]), sort_keys=True).encode()).hexdigest()[:32]

    cache_f = out / "overlaps.json"
    try:
        cache = json.loads(cache_f.read_text())
    except (OSError, ValueError):
        cache = {}
    solids = Solids(root, byname)
    req = cc.fcl.CollisionRequest()

    def overlap(i, j, t):
        """(status, volume, point) of the pair at time t (None: where the model stands)."""
        P = C.poses(t) if t is not None else {}
        M = {k: (P[grp[k]] if t is not None and grp[k] != "static" else None) for k in (i, j)}
        a, b = solids.at(names[i], M[i]), solids.at(names[j], M[j])
        v, commons = _common(a, b) if a is not None and b is not None else (None, [])
        if v is not None and v <= CLASH_V:
            return "touch", v, None
        if v is not None:
            # the overlap's boundary, sampled along every edge: a circle has a single vertex, which says nothing of its radius
            pts = np.concatenate([c.points() if fc._is_mesh(c) else
                                  np.array([tuple(e.position_at(u)) for e in c.edges() for u in (0.0, 0.25, 0.5, 0.75)])
                                  for c in commons])
            at = ((pts.min(0) + pts.max(0)) / 2).tolist()
        else:                                    # no boolean: where the meshes cross is what the screw rule can still judge
            r = cc.fcl.CollisionResult()
            cc.fcl.collide(C.place(i, P or {}), C.place(j, P or {}),
                           cc.fcl.CollisionRequest(num_max_contacts=100000, enable_contact=True), r)
            pts = [c.pos for c in r.contacts]
            at = np.mean(pts, 0).tolist() if pts else None
        for k in (i, j) if grp[i] == grp[j] else ():   # a thread is cut at rest: a part moving against the screw clashes
            if axes.get(names[k]) and _inside_shank(axes[names[k]], pts, M[k]):
                return "thread", v, at
        return ("clash" if v is not None else "unmeasured"), v, at

    def measure(i, j):
        if grp[i] == grp[j]:
            hit = cc.fcl.collide(C.place(i, {}), C.place(j, {}), req, cc.fcl.CollisionResult())
            inside = any(np.all(lo[a] >= lo[b] - MARGIN) and np.all(hi[a] <= hi[b] + MARGIN) for a, b in ((i, j), (j, i)))
            times, window = ([None] if hit or inside else []), None   # no surface crosses when one sits wholly in the other
        else:
            r = C.measure(i, j, 0.0, MARGIN)
            times, window = r["hits"], r["windows"]
        if not times:
            return {"status": "clear"}
        picks = sorted({times[round(k * (len(times) - 1) / (PICKS - 1))] for k in range(PICKS)}) if len(times) > PICKS else times
        worst = {"status": "touch"}
        for t in picks:
            st, v, at = overlap(i, j, t)
            if RANK[st] > RANK[worst["status"]] or (st == worst["status"] == "clash" and v > worst["volume_mm3"]):
                worst = {"status": st, "volume_mm3": None if v is None else round(v, 1), "t_s": t,
                         "window_s": next((w for w in window if w[0] <= t <= w[1]), None) if window else None, "windows_s": window,
                         "at_mm": None if at is None else [round(float(x), 1) for x in at]}
        return worst

    out.mkdir(parents=True, exist_ok=True)
    new, measured, clashes, unmeasured, saved = {}, 0, [], [], time.time()
    for i, j in pairs:
        key = pair_key(i, j)
        if key in cache:
            r = cache[key]
        else:
            measured += 1
            r = measure(i, j)
            if time.time() - saved > SAVE_S:   # the old entries too: a killed run must not lose what it has not reached yet
                keep = {**cache, **new, key: r}
                cc.write_atomic(cache_f, lambda t: t.write_text(json.dumps(keep)))
                saved = time.time()
        new[key] = r
        if r["status"] in ("clash", "unmeasured"):
            row = {"a": names[i], "b": names[j], "groups": [grp[k].split(":")[0] for k in (i, j)], "now": key not in cache,
                   **{k: v for k, v in r.items() if k != "status"}}
            (clashes if r["status"] == "clash" else unmeasured).append(row)
    out.mkdir(parents=True, exist_ok=True)
    cc.write_atomic(cache_f, lambda t: t.write_text(json.dumps(new)))
    clashes.sort(key=lambda r: (not r["now"], -(r["volume_mm3"] or 0)))   # what this build's change made first
    rep = {"cycle": bool(cg), **({"tracks": {"applied": len(trk), "skipped": skipped}} if trk or skipped else {}),
           "pairs": len(pairs), "measured": measured, "cached": len(pairs) - measured,
           "seconds": round(time.time() - t0, 1), "clashes": clashes, "unmeasured": unmeasured}
    cc.write_atomic(out / "report.json", lambda t: t.write_text(json.dumps(rep, ensure_ascii=False, indent=1)))
    return rep


def _where(r):
    if r["t_s"] is None:
        return "where the model stands"
    ws = r.get("windows_s") or [r["window_s"]]
    spans = ", ".join(f"{a:g}..{b:g}" for a, b in ws[:3]) + (f" and {len(ws) - 3} more" if len(ws) > 3 else "")
    return f"at t {r['t_s']:g} s of the cycle (overlapping {spans} s)"


def lines(rep):
    """What the build prints: a summary, then the clashes (largest first) - the last lines of the log the agent gets."""
    out = [f"clearance: {rep['pairs']} pairs {'over the cycle' if rep['cycle'] else 'where the model stands (no cycle)'}: "
           f"measured {rep['measured']}, cached {rep['cached']}, {rep['seconds']:g} s"]
    if (rep.get("tracks") or {}).get("skipped"):   # a renamed link falls back to its group: say so, never silently
        sk = rep["tracks"]["skipped"]
        out.append(f"WARN  pose tracks not applied ({len(sk)}): " + "; ".join(sk[:5]) + (" ..." if len(sk) > 5 else ""))
    for r in rep["unmeasured"][:SHOW]:
        out.append(f"WARN  not measured (no boolean): {r['a']} [{r['groups'][0]}] ~ {r['b']} [{r['groups'][1]}] {_where(r)}")
    for r in rep["clashes"][:SHOW]:
        at = f", at ({r['at_mm'][0]:g}, {r['at_mm'][1]:g}, {r['at_mm'][2]:g}) mm" if r.get("at_mm") else ""
        old = "" if r.get("now") else " (pair unchanged since an earlier build)"
        out.append(f"CLASH {r['a']} [{r['groups'][0]}] x {r['b']} [{r['groups'][1]}]: {r['volume_mm3']:g} mm3 {_where(r)}{at}{old}")
    if len(rep["clashes"]) > SHOW:
        out.append(f"... and {len(rep['clashes']) - SHOW} more clashes in build/clearance/report.json")
    if rep["clashes"]:
        out.append(f"CLASH: {len(rep['clashes'])} pair(s) of parts overlap - a defect of the model. Parts may touch, never "
                   f"share space: move or reshape one part of each pair. A contact the machine means comes from a joint - "
                   f"dock the part to what it sits in (skill machine-project)")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("project")
    ap.add_argument("--motion", type=Path, help="motion.json with a `cycle` block (default: no motion)")
    ap.add_argument("--groups", type=Path, help="{placement name: group} (a machine's motion_groups.json)")
    ap.add_argument("--tracks", type=Path, help="pose tracks {n, period, nodes} or a world data JSON with CRANK_TRACKS")
    a = ap.parse_args(argv)
    cyc = json.loads(a.motion.read_text()) if a.motion else None
    trk = json.loads(a.tracks.read_text()) if a.tracks else None
    rep = run(a.project, cyc.get("cycle", cyc) if cyc else None, json.loads(a.groups.read_text()) if a.groups else None,
              (trk.get("CRANK_TRACKS") or trk) if isinstance(trk, dict) else None)
    print("\n".join(lines(rep)))
    return 1 if rep["clashes"] else 0


if __name__ == "__main__":
    sys.exit(main())
