#!/usr/bin/env python3
"""Collisions and clearances of every moving part over one machine cycle (python-fcl on the parts' real meshes).

Input = what the web-viewer pipeline already has: the parts at the HOME pose, and motion.json with a `cycle` block
(web-viewer «Cycle motion»: groups {kind linear|rotary, axis, travel_mm | travel_deg + pivot_mm, parent,
segments [[t0,t1,f0,f1],...]}, node_groups {part name: group | "static"}). The parts come from either
  --model build/model/parts.json   the code model: every part's own STEP + the world matrix of each placement; a
                                   placement's name is looked up in the groups, else it moves as the viewer moves it:
                                   its part's `motion_group`, else with what it is docked to (a screw on a slider).
                                   Only changed parts are tessellated (<out>/meshes/<key>-<tol>.npz); stored vendor
                                   triangles (`mesh`) are checked as they are.
  --step build/cad/<machine>.step  one machine STEP with named parts (mesh cache <out>/meshes.npz, redone when the STEP changes)

    python cycle_clearance.py --model build/model/parts.json --motion build/twin/motion.json \
        [--groups build/twin/motion_groups.json] [--clear 0.5] [--max-step 0.5] \
        [--expect "*rail_block*~*rail*" ...] [--only "cup*"] [--skip "*link_rod*" ...] [--out build/clearance]

Pairs checked: each moving part vs every part of another group (static or moving). Same-group pairs never move
relative to each other and are skipped. Each pair is sampled over the cycle so that no point of ITS two parts moves more
than --max-step mm between samples (hold windows are sampled once).
Incremental: a pair's measurement is kept in <out>/pairs.json under its inputs (both meshes as placed, the motion of
both groups with their parents, period, --clear/--max-step/--tol, this script): a one-part edit re-measures that part's
pairs, a changed group motion the pairs of that group and its children. The run prints "measured N, cached M".
Per pair: minimum distance over the cycle and when; HIT = meshes intersect
(times listed), CLOSE = closer than --clear, SLIDE = touching (or the same gap <= 0.1) over the whole cycle (a sliding
fit: rail block on its rail, rod in its body) - listed, not failed; declare the expected ones with --expect to keep them quiet.
Exit 1 on any HIT or CLOSE that is not expected. Writes <out>/clearance.json (`samples` = the distinct sample times
over all moving parts).
Not seen: a part entirely inside another without its surface crossing (fcl tests surfaces), parts that change group,
flexible parts (belts, hoses, paper), procedural extras of the viewer.
"""
import argparse
import fnmatch
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import time
from pathlib import Path

import fcl
import numpy as np


def keys(label):
    base = label.split(" - ")[0].split("_-_")[0]
    # last: a repeated instance ("screw_2") falls back to its first one's name, as the viewer does (motion.mjs nodeGroup)
    return [label, base, re.sub(r"\s+", "_", base), re.sub(r"[\[\]\.:/\s]", "_", label), re.sub(r"_\d+$", "", base)]


def load_parts(step, cache, tol):
    if cache.exists() and cache.stat().st_mtime > step.stat().st_mtime:
        z = np.load(cache, allow_pickle=True)
        return list(z["names"]), [np.asarray(v, float) for v in z["verts"]], [np.asarray(f, np.int32) for f in z["tris"]]
    from build123d import import_step
    t0 = time.time()
    root = import_step(str(step))
    names, verts, tris = [], [], []

    def walk(n):
        ch = list(getattr(n, "children", []) or [])
        if ch:
            for c in ch:
                walk(c)
            return
        if n.volume <= 0:
            return
        v, f = n.tessellate(tol, 0.3)   # world coordinates: build123d applies the shape's full location
        if not f:
            return
        names.append(n.label or f"part{len(names)}")
        verts.append(np.array([[p.X, p.Y, p.Z] for p in v], float))
        tris.append(np.array(f, np.int32))
    walk(root)
    cache.parent.mkdir(parents=True, exist_ok=True)
    def objs(xs):   # 1-D object array even when every part has the same vertex count
        o = np.empty(len(xs), object); o[:] = xs; return o
    np.savez(cache, names=objs(names), verts=objs(verts), tris=objs(tris))
    print(f"{step}: {len(names)} parts tessellated in {time.time() - t0:.0f} s")
    return names, verts, tris


def mesh_of(step, tol):
    """One part's STEP as (verts, tris) in its own frame."""
    from build123d import import_step
    v, f = import_step(str(step)).tessellate(tol, 0.3)
    return np.array([[p.X, p.Y, p.Z] for p in v], float).reshape(-1, 3), np.array(f, np.int32).reshape(-1, 3)


def stable(v):
    """A cache key's numbers to 1e-6 (and -0.0 to 0.0): a rebuild that changes nothing leaves noise of 1e-13 in placement
    matrices and of 1e-16 in a group's axis, which re-measured 627 of 908 pairs of a real machine for a two-part edit."""
    if isinstance(v, float):
        return round(v, 6) + 0.0
    if isinstance(v, (list, tuple)):
        return [stable(x) for x in v]
    if isinstance(v, dict):
        return {k: stable(x) for k, x in v.items()}
    return v


def write_atomic(path, write):
    """write(tmp) then rename: a run killed mid-write (timeout, OOM) leaves no half file for the next run to choke on."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f"{path.stem}.tmp-", suffix=path.suffix)   # unique: job containers
    os.close(fd)                                                                                      # all run as pid 1
    try:
        write(Path(tmp))
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def motion_groups(parts):   # a copy of derive.py's; test_c4_groups_agree_with_derive_motion_groups keeps them equal
    """placement -> group, the viewer's rule (machine-project derive.py motion_groups): the part's own `motion_group`, else
    the group of the placement it is docked to, by any joint, up the joint graph (a screw docked on the pusher moves with it)."""
    own, up = {}, {}
    for p in parts:
        for pl in p.get("placements") or [{"name": p.get("id") or p.get("name")}]:
            own[pl["name"]] = p.get("motion_group")
            if isinstance(pl.get("joint"), dict):
                up[pl["name"]] = pl["joint"].get("to")
    out = {}
    for name in own:
        n, seen = name, set()
        while own.get(n) is None and n in up and n not in seen:
            seen.add(n)
            n = up[n]
        out[name] = own.get(n)
    return out


def load_model(model, cache, tol):
    """Every placement of every part of a code model (parts.json) as (names, verts, tris, groups, ids, pids): the part's
    own mesh (its STEP, tessellated once per content key, or its stored `mesh` triangles) moved by the placement's world
    matrix; groups = motion_groups(); ids = (part key, placement matrix, tol), what a pair's cached result depends on;
    pids = the placement's part id."""
    root = model.resolve().parent.parent.parent   # build/model/parts.json -> the project

    def path(rel):
        q = Path(rel)
        return q if q.is_absolute() or not (root / q).exists() else root / q   # else relative to the cwd

    parts = json.loads(model.read_text())["parts"]
    names, verts, tris, ids, pids, used, t0, made, skipped = [], [], [], [], [], set(), time.time(), 0, []
    for p in parts:
        if p.get("step"):
            step = path(p["step"])
            key = p.get("key") or hashlib.sha256(step.read_bytes()).hexdigest()
            f = cache / f"{key}-{tol:g}.npz"
            used.add(f.name)
            try:
                z = np.load(f)
                v, tr = z["verts"], z["tris"]
            except Exception:   # missing or a half-written file from a killed run: measure it again
                v, tr = mesh_of(step, tol)
                made += 1
                write_atomic(f, lambda t, v=v, tr=tr: np.savez_compressed(t, verts=v, tris=tr))
        elif p.get("mesh"):   # vendor triangles stored with the model (derive.py _mesh_part)
            m = json.loads(path(p["mesh"]).read_text())
            v, tr = np.array(m["v"], float).reshape(-1, 3), np.array(m["f"], np.int32).reshape(-1, 3)
            key = p.get("key") or hashlib.sha256(v.tobytes() + tr.tobytes()).hexdigest()
        else:
            skipped.append(p.get("id") or p.get("name"))
            continue
        pls = p.get("placements") or [{"name": p.get("id") or p.get("name"), "loc": None}]
        for pl in pls:
            M = np.array(pl["loc"], float).reshape(4, 4) if pl.get("loc") else np.eye(4)
            names.append(pl["name"])
            verts.append(v @ M[:3, :3].T + M[:3, 3])
            tris.append(tr)
            ids.append([key, pl.get("loc"), tol])
            pids.append(p.get("id") or p.get("name"))
    for f in cache.glob("*.npz") if cache.is_dir() else []:
        if f.name not in used:   # meshes of parts that changed since: never read again
            f.unlink(missing_ok=True)
    if made:
        print(f"{model}: {made} parts tessellated in {time.time() - t0:.0f} s")
    if skipped:
        print(f"{len(skipped)} parts have no STEP and no mesh, not checked: {', '.join(map(str, skipped[:8]))}")
    return names, verts, tris, motion_groups(parts), ids, pids


def frac(segs, t):
    """Group fraction at time t: linear inside a segment, hold between, before the first hold the last end (wrap)."""
    segs = sorted(segs)
    f = segs[-1][3]
    for t0, t1, f0, f1 in segs:
        if t < t0:
            break
        f = f0 + (f1 - f0) * min(1.0, (t - t0) / (t1 - t0)) if t1 > t0 else f1
    return f


def axis_vec(a):
    if isinstance(a, str):
        v = np.zeros(3); v["xyz".index(a.lower())] = 1.0; return v
    v = np.array(a, float); return v / np.linalg.norm(v)


def rot(axis, ang):
    x, y, z = axis; c, s = math.cos(ang), math.sin(ang); C = 1 - c
    return np.array([[c + x * x * C, x * y * C - z * s, x * z * C + y * s],
                     [y * x * C + z * s, c + y * y * C, y * z * C - x * s],
                     [z * x * C - y * s, z * y * C + x * s, c + z * z * C]])


def group_pose(groups, g, t, memo):
    """4x4 world transform of group g at time t (parent first: M = M_parent * M_own)."""
    if g in memo:
        return memo[g]
    G = groups[g]; f = frac(G["segments"], t) if G.get("segments") else 0.0
    M = np.eye(4); a = axis_vec(G["axis"])
    if G.get("kind", "linear") == "rotary":
        R = rot(a, math.radians(G["travel_deg"] * f)); p = np.array(G["pivot_mm"], float)
        M[:3, :3] = R; M[:3, 3] = p - R @ p
    else:
        M[:3, 3] = a * G["travel_mm"] * f
    if G.get("parent"):
        M = group_pose(groups, G["parent"], t, memo) @ M
    memo[g] = M
    return M


def group_poses(groups, g, ts):
    """group_pose for an array of times at once: (len(ts), 4, 4)."""
    G, n = groups[g], len(ts)
    f = np.zeros(n)
    if G.get("segments"):   # frac(), vectorised: segments in order of start, each later one overrides from its start on
        segs = sorted(G["segments"])
        f[:] = segs[-1][3]
        for t0, t1, f0, f1 in segs:
            val = f0 + (f1 - f0) * np.minimum(1.0, (ts - t0) / (t1 - t0)) if t1 > t0 else np.full(n, float(f1))
            f = np.where(ts >= t0, val, f)
    M = np.tile(np.eye(4), (n, 1, 1)); a = axis_vec(G["axis"])
    if G.get("kind", "linear") == "rotary":
        ang = np.radians(G["travel_deg"] * f); c, s = np.cos(ang), np.sin(ang); C = 1 - c; x, y, z = a
        R = np.stack([np.stack([c + x * x * C, x * y * C - z * s, x * z * C + y * s], -1),
                      np.stack([y * x * C + z * s, c + y * y * C, y * z * C - x * s], -1),
                      np.stack([z * x * C - y * s, z * y * C + x * s, c + z * z * C], -1)], 1)
        p = np.array(G["pivot_mm"], float)
        M[:, :3, :3] = R; M[:, :3, 3] = p - R @ p
    else:
        M[:, :3, 3] = a[None] * (G["travel_mm"] * f)[:, None]
    if G.get("parent"):
        M = group_poses(groups, G["parent"], ts) @ M
    return M


def chain(groups, g):
    out = []
    while g and g in groups:
        out.append(g); g = groups[g].get("parent")
    return out


def sample_times(groups, reach, period, max_step, only=None):
    """Segment ends + enough samples inside every moving window that no point moves > max_step between samples.
    only = the groups that move the parts in question (a part's chain), else all."""
    ts = {0.0}
    for g, G in groups.items():
        if only is not None and g not in only:
            continue
        for t0, t1, f0, f1 in G.get("segments", []):
            ts.update((t0, t1))
            if G.get("kind", "linear") == "rotary":
                move = abs(math.radians(G["travel_deg"]) * (f1 - f0)) * reach[g]
            else:
                move = abs(G["travel_mm"] * (f1 - f0))
            n = int(math.ceil(move / max_step))
            ts.update(np.linspace(t0, t1, n + 1).tolist() if n > 1 else [])
    # children move whenever a parent moves: their parents' samples are already in the set
    return sorted(t for t in ts if 0 <= t <= period)


def track_poses(track, ts):
    """Poses of a tracked part at the times ts: (len(ts), 4, 4). track = (period, (n, 4, 4) samples, sample k at k*period/n);
    the page shows sample floor((t mod period) / period * n + 0.5) mod n (a tracked world module of the viewer), and so
    does this. Its pairs are measured at its samples only (Cycle.measure): between two of them the carriage it rides with
    moves on (8 mm a sample on that shuttle) while the pose holds, and a blend cuts the carriage's corners just as wrongly."""
    period, M = track
    n = len(M)
    k = np.floor(((np.asarray(ts, float) % period) / period) * n + 0.5).astype(int) % n
    return M[k]


def tracks_of(names, data, period_s):
    """({part index: (period, (n, 4, 4) samples)}, [what was not applied]) from pose tracks {n, period, nodes: {placement
    name: 12 * n floats, each sample a row-major 3x4 in the CAD frame, mm}}: what a world module applies on the page over a
    node's home pose in place of its group's motion (crank links: the framework cycle moves whole groups only).
    Matched by exact placement name, as the page finds its node. A track of another period than the cycle's is a stale
    one (the cycle time changed, the tracks were not remade): none is applied."""
    out, skipped = {}, []
    if not isinstance(data, dict) or not data.get("nodes") or not data.get("n"):
        return out, skipped
    try:   # a typo in a project's world data costs its tracks, never the whole check
        n, period = int(data["n"]), float(data.get("period") or 0)
        if not isinstance(data["nodes"], dict) or n < 1:
            raise ValueError("nodes is not {name: floats}")
    except (TypeError, ValueError) as e:
        return out, [f"unreadable: {e}"[:200]]
    if period <= 0 or abs(period - period_s) > 1e-3:
        return out, [f"all {len(data['nodes'])}: track period {period:g} s, cycle {period_s:g} s"]
    at = {nm: i for i, nm in enumerate(names)}
    for nm, arr in data["nodes"].items():
        if nm not in at:
            skipped.append(f"{nm}: no such part")
        elif not isinstance(arr, list) or len(arr) != 12 * n:
            skipped.append(f"{nm}: {len(arr) if isinstance(arr, list) else '?'} numbers, not {12 * n}")
        else:
            try:
                M = np.tile(np.eye(4), (n, 1, 1))
                M[:, :3, :] = np.asarray(arr, float).reshape(n, 3, 4)
            except (TypeError, ValueError):
                skipped.append(f"{nm}: not numbers")
                continue
            if not np.isfinite(M).all():   # a null / NaN pose (a link solver out of reach) would hide the part from every pair
                skipped.append(f"{nm}: not finite numbers")
                continue
            out[at[nm]] = (period, M)
    return out, skipped


def mesh_cache(model, out):
    """Where a --model run keeps its per-part meshes. On a remote worker the runner gives the job a per-machine cache
    (XDG_CACHE_HOME): meshes kept there are never shipped back to the project on the server, where nothing would delete the superseded
    ones. One folder per project: a job sees no other project's geometry, and superseded meshes can be deleted by what this
    model no longer uses."""
    xdg = os.environ.get("XDG_CACHE_HOME")
    return Path(xdg) / "cycle_clearance" / Path(model).resolve().parent.parent.parent.name if xdg else Path(out) / "meshes"


def assign_groups(names, pids, own, gmap, groups):
    """Each part's group, the viewer's order (motion.mjs): node_groups by instance name, else the part's own / docked group,
    else a group that lists the part in its `parts`; "static" for none, or for a group the cycle does not have."""
    grp = []
    for n, pid in zip(names, pids):
        g = next((gmap[k] for k in keys(n) if k in gmap), None) or own.get(n) or \
            next((gg for gg, G in groups.items() if pid in (G.get("parts") or [])), None) or "static"
        grp.append(g if g in groups else "static")
    return grp


class Cycle:
    """The parts of a machine over one cycle: each moving part's sample times, a part's world box at a time, its swept box,
    and one pair measured over the cycle. The CLI below and machine-project clearance.py (the check every model build
    runs) both measure through it.

    A part's samples depend on its own vertices and its own chain's motion only, so a pair's result depends on its two
    parts alone and can be cached by them."""

    def __init__(self, verts, tris, grp, groups, period, max_step, tracks=None):
        self.verts, self.tris, self.grp, self.groups, self.period, self.max_step = verts, tris, grp, groups, period, max_step
        # tracked parts (tracks_of) are posed by their track, not by a group: one group "track:<hash>" per distinct track, so
        # the parts of one link (rod ends, rod, nuts) stay one rigid body, measured where the model stands
        self.tracks = tracks or {}
        self.track_id = {i: "track:" + hashlib.sha256(np.round(TM, 6).tobytes()).hexdigest()[:16] for i, (_, TM) in self.tracks.items()}
        for i, g in self.track_id.items():
            grp[i] = g
        self.lo = np.array([v.min(0) for v in verts]).reshape(-1, 3)
        self.hi = np.array([v.max(0) for v in verts]).reshape(-1, 3)
        self.pts, self.motion, self.chains, self.swept, self._poses, self._objs = {}, {}, {}, {}, {}, {}
        for i, g in enumerate(grp):
            if g == "static":
                continue
            if i in self.tracks:   # its samples, the only times its pose is known
                tp, TM = self.tracks[i]
                self.pts[i] = sorted({round(k * tp / len(TM), 6) for k in range(len(TM))})
                self.motion[i] = [tp, ["track", g]]
                self.chains[i] = [g]
                lo, hi = self.boxes(i, self.at(i, np.array(self.pts[i])))
                self.swept[i] = (lo.min(0), hi.max(0))
                continue
            ch = chain(groups, g)
            reach = {}
            for gg in ch:   # farthest vertex of this part from each pivot, for rotary step sizing
                piv = np.array(groups[gg].get("pivot_mm", (0, 0, 0)), float)
                reach[gg] = float(np.linalg.norm(verts[i] - piv, axis=1).max())
            self.pts[i] = sample_times(groups, reach, period, max_step, only=set(ch))
            self.motion[i] = [period, [[gg, groups[gg]] for gg in ch]]
            self.chains[i] = ch
            lo, hi = self.boxes(i, self.at(i, np.array(self.pts[i])))   # all samples at once: one by one was 40 s warm
            self.swept[i] = (lo.min(0), hi.max(0))

    def at(self, i, ts):
        """Part i's world transforms at the times ts: (len(ts), 4, 4)."""
        if self.grp[i] == "static":
            return np.tile(np.eye(4), (len(ts), 1, 1))
        if i in self.tracks:
            return track_poses(self.tracks[i], ts)
        return group_poses(self.groups, self.grp[i], ts)

    def boxes(self, i, M):
        """Part i's world boxes (lo, hi), each (len(M), 3), under the transforms M."""
        lo, hi = self.lo[i], self.hi[i]
        c = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
        w = np.einsum("nij,kj->nki", M[:, :3, :3], c) + M[:, None, :3, 3]
        return w.min(1), w.max(1)

    def poses(self, t):
        if t not in self._poses:
            memo = {}
            self._poses[t] = {g: group_pose(self.groups, g, t, memo) for g in self.groups}
            self._poses[t].update({self.track_id[i]: track_poses(tr, [t])[0] for i, tr in self.tracks.items()})
        return self._poses[t]

    def reach(self, i):
        """(lo, hi) of everywhere part i goes over the cycle."""
        return self.swept.get(i, (self.lo[i], self.hi[i]))

    def candidates(self, moving, margin):
        """Pairs (i, j), i < j, of a moving part and a part of another group whose swept boxes come within margin."""
        out = set()
        for i in moving:
            li, hi = self.reach(i)
            for j in range(len(self.grp)):
                if j == i or self.grp[j] == self.grp[i]:
                    continue
                lj, hj = self.reach(j)
                if np.all(li - margin <= hj) and np.all(lj - margin <= hi):
                    out.add((min(i, j), max(i, j)))
        return out

    def obj(self, k):
        if k not in self._objs:
            m = fcl.BVHModel(); m.beginModel(len(self.verts[k]), len(self.tris[k])); m.addSubModel(self.verts[k], self.tris[k]); m.endModel()
            self._objs[k] = fcl.CollisionObject(m, fcl.Transform())
        return self._objs[k]

    def place(self, k, P):
        """The fcl object of part k where it stands at the poses P ({}: where the model stands)."""
        o = self.obj(k)
        M = P.get(self.grp[k])
        M = np.eye(4) if M is None else M
        o.setTransform(fcl.Transform(M[:3, :3], M[:3, 3]))
        return o

    def measure(self, i, j, clear, margin, keep=0.0):
        """One pair over the cycle: the least distance measured and when, the hit times, how many samples were within reach.
        Between two samples neither part moves more than max_step per group of its chain, so while the two stand farther
        apart than the motion until a later sample could close, the samples in between can neither hit nor come within
        `clear`: they are skipped (conservative advancement). Every HIT and CLOSE sample is still measured, and a pair that
        touches all the cycle never skips, so a SLIDE reads as before. No sample nearer than `keep` is skipped either, so a
        least distance under `keep` is exact; a pair whose boxes came within `margin` and was never measured is measured
        once, where its boxes came closest.
        The motion that counts is j's relative to i, measured exactly: j's box corners in i's frame (how far a point moves
        under a rigid motion is convex in the point, so no vertex of j moves farther than the farthest corner). A shaft
        turning in the bore of the lever that carries it moves 0.007 mm a sample against it while the lever's swing sets
        the samples: 13 000 of them were measured one by one before, 2 x 57 s of a real machine's 3 min."""
        tracked = [k for k in (i, j) if k in self.tracks]   # a tracked part: at its samples only (track_poses)
        ts = sorted(set().union(*(self.pts[k] for k in tracked))) if tracked else sorted(set(self.pts.get(i, [])) | set(self.pts.get(j, [])))
        r = {"min": math.inf, "max": 0.0, "t": None, "hits": [], "windows": [], "n": 0, "samples": len(ts)}
        Mi, Mj = self.at(i, np.array(ts)), self.at(j, np.array(ts))
        (li, hi_), (lj, hj) = self.boxes(i, Mi), self.boxes(j, Mj)
        gap = np.maximum(np.maximum((li - hj).max(1), (lj - hi_).max(1)), 0.0)   # how far apart the boxes are: <= the distance
        Ri = np.transpose(Mi[:, :3, :3], (0, 2, 1))   # Mi^-1 Mj, applied to j's corners: where j stands in i's frame
        R, tr = Ri @ Mj[:, :3, :3], np.einsum("nij,nj->ni", Ri, Mj[:, :3, 3] - Mi[:, :3, 3])
        lo, hi = self.lo[j], self.hi[j]
        cj = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
        W = np.einsum("nij,kj->nki", R, cj) + tr[:, None, :]

        def next_after(k, room):   # the first later sample where j may have moved `room` against i
            m = k + 1
            while room > 0 and m < len(ts):
                e = min(len(ts), m + 512)
                far = np.nonzero(np.linalg.norm(W[m:e] - W[k], axis=2).max(1) >= room)[0]
                if len(far):
                    return m + int(far[0])
                m = e
            return m if room > 0 else k + 1

        oi, oj = self.obj(i), self.obj(j)
        req, dreq, k, last = fcl.CollisionRequest(), fcl.DistanceRequest(), 0, -2
        while k < len(ts):
            t, d = ts[k], float(gap[k])
            if d <= margin:
                oi.setTransform(fcl.Transform(Mi[k][:3, :3], Mi[k][:3, 3]))
                oj.setTransform(fcl.Transform(Mj[k][:3, :3], Mj[k][:3, 3]))
                r["n"] += 1
                if fcl.collide(oi, oj, req, fcl.CollisionResult()):
                    r["hits"].append(round(t, 4)); d = 0.0
                    if last == k - 1:   # a hit never skips, so the next sample of an overlap is the next one: one window
                        r["windows"][-1][1] = round(t, 4)
                    else:
                        r["windows"].append([round(t, 4), round(t, 4)])
                    last = k
                else:
                    d = fcl.distance(oi, oj, dreq, fcl.DistanceResult())
                r["max"] = max(r["max"], d)
                if d < r["min"]:
                    r["min"], r["t"] = d, round(t, 4)
            k = next_after(k, d - max(clear, keep))
        if r["min"] == math.inf and gap.min() <= margin:
            k = int(gap.argmin())
            oi.setTransform(fcl.Transform(Mi[k][:3, :3], Mi[k][:3, 3]))
            oj.setTransform(fcl.Transform(Mj[k][:3, :3], Mj[k][:3, 3]))
            r["n"], r["min"], r["t"] = 1, fcl.distance(oi, oj, dreq, fcl.DistanceResult()), round(ts[k], 4)
            r["max"] = r["min"]
        if r["min"] == math.inf:
            r["min"] = None   # never within reach at a sample: JSON has no infinity
        return r


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--model", type=Path, help="build/model/parts.json of a code model: incremental per part")
    src.add_argument("--step", type=Path, help="one machine STEP at the home pose with named parts")
    ap.add_argument("--motion", required=True, type=Path)
    ap.add_argument("--groups", type=Path, help="extra {part name: group} map (web-viewer motion_groups.json)")
    ap.add_argument("--clear", type=float, default=0.5, help="minimum clearance, mm")
    ap.add_argument("--max-step", type=float, default=0.5, help="max motion of any point between samples, mm")
    ap.add_argument("--tol", type=float, default=0.1, help="tessellation tolerance, mm")
    ap.add_argument("--expect", nargs="*", default=[], help='expected contacts "a~b" (fnmatch on part names)')
    ap.add_argument("--only", help="check only moving parts whose name matches this fnmatch pattern")
    ap.add_argument("--skip", nargs="*", default=[], help="leave out parts matching these patterns (e.g. crank links the cycle cannot move)")
    ap.add_argument("--out", type=Path, default=Path("build/clearance"))
    a = ap.parse_args()

    cyc = json.loads(a.motion.read_text())
    cyc = cyc.get("cycle", cyc)
    groups, period = cyc["groups"], float(cyc["period_s"])
    gmap = dict(json.loads(a.groups.read_text())) if a.groups else {}
    gmap.update(cyc.get("node_groups", {}))
    if a.model:
        names, verts, tris, own, ids, pids = load_model(a.model, mesh_cache(a.model, a.out), a.tol)
    else:
        (names, verts, tris), own = load_parts(a.step, a.out / "meshes.npz", a.tol), {}
        pids = list(names)
        ids = [hashlib.sha256(np.ascontiguousarray(v).tobytes() + np.ascontiguousarray(f).tobytes()).hexdigest()
               for v, f in zip(verts, tris)]

    keep = [k for k, n in enumerate(names) if not any(fnmatch.fnmatch(n, x) for x in a.skip)]
    names, verts, tris, ids, pids = ([xs[k] for k in keep] for xs in (names, verts, tris, ids, pids))
    grp = assign_groups(names, pids, own, gmap, groups)
    moving = [i for i, g in enumerate(grp) if g != "static" and (not a.only or fnmatch.fnmatch(names[i], a.only))]
    print(f"{len(names)} parts, {sum(g != 'static' for g in grp)} moving in {len(set(grp) - {'static'})} groups; checking {len(moving)}")
    if not moving:
        sys.exit("no moving part matched a group: check node_groups / --groups names against the STEP labels")

    C = Cycle(verts, tris, grp, groups, period, a.max_step)
    pts, motion = C.pts, C.motion
    margin = max(a.clear, 2.0)
    pairs = C.candidates(moving, margin)

    code = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    def pair_key(i, j):
        bits = sorted([stable([ids[k], motion.get(k)]) for k in (i, j)], key=json.dumps)   # the part as placed + its motion
        return hashlib.sha256(json.dumps(stable([code, bits, a.clear, a.max_step, a.tol]), sort_keys=True).encode()).hexdigest()[:32]

    cache_f = a.out / "pairs.json"
    for f in a.out.glob("pairs.tmp-*.json"):   # left by a run killed while writing (a young one may be another run's)
        if time.time() - f.stat().st_mtime > 600:
            f.unlink(missing_ok=True)
    try:
        cache = json.loads(cache_f.read_text())
    except (OSError, ValueError):
        cache = {}
    res, new_cache, measured = {}, {}, 0
    for i, j in sorted(pairs):
        key = pair_key(i, j)
        if key in cache:
            res[(i, j)] = new_cache[key] = cache[key]
            continue
        measured += 1
        res[(i, j)] = new_cache[key] = C.measure(i, j, a.clear, margin, keep=margin)
    a.out.mkdir(parents=True, exist_ok=True)
    # An --only run keeps the other pairs' results for the next full run. Not --skip: it is part of the standard command,
    # and merging there would keep every superseded pair forever.
    if a.only:
        new_cache = {**cache, **new_cache}
    write_atomic(cache_f, lambda t: t.write_text(json.dumps(new_cache)))
    nts = sorted({t for v in pts.values() for t in v})
    print(f"{len(nts)} sample times over {period} s, {len(pairs)} candidate pairs: measured {measured}, cached {len(pairs) - measured}")

    def expected(i, j):
        for e in a.expect:
            x, y = e.split("~")
            if (fnmatch.fnmatch(names[i], x) and fnmatch.fnmatch(names[j], y)) or \
               (fnmatch.fnmatch(names[j], x) and fnmatch.fnmatch(names[i], y)):
                return True
        return False

    rows, bad = [], 0
    for (i, j), r in res.items():
        if r["min"] is None:
            continue
        if r["n"] == r["samples"] and (len(r["hits"]) == r["samples"] or (r["max"] - r["min"] < 0.01 and r["min"] <= 0.1)):
            st = "SLIDE"   # touching (or a modelled fit <= 0.1) over the whole cycle: rail block on its rail, not a collision
        elif r["hits"]:
            st = "HIT"
        elif r["min"] < a.clear:
            st = "CLOSE"
        else:
            st = "ok"
        exp = expected(i, j)
        if st in ("HIT", "CLOSE") and not exp:
            bad += 1
        rows.append({"a": names[i], "b": names[j], "groups": [grp[i], grp[j]], "min_mm": round(r["min"], 3), "t_s": r["t"],
                     "status": st + (" (expected)" if exp and st != "ok" else ""),
                     "hit_window_s": [r["hits"][0], r["hits"][-1]] if r["hits"] else None, "hit_samples": len(r["hits"])})
    rows.sort(key=lambda x: x["min_mm"])
    for x in rows:
        if x["status"] != "ok" or x["min_mm"] < 3 * a.clear:
            w = f" hit {x['hit_window_s'][0]}..{x['hit_window_s'][1]} s ({x['hit_samples']} samples)" if x["hit_window_s"] else ""
            print(f"{x['status']:<16} {x['min_mm']:7.3f} mm at t {x['t_s']} s  {x['a']} [{x['groups'][0]}] ~ {x['b']} [{x['groups'][1]}]{w}")
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "clearance.json").write_text(json.dumps({"clear_mm": a.clear, "samples": len(nts), "pairs": rows}, ensure_ascii=False, indent=1))
    print(f"{'FAIL' if bad else 'PASS'}: {bad} unexpected HIT/CLOSE, {sum(x['status'].startswith('SLIDE') for x in rows)} SLIDE, "
          f"{len(rows)} pairs within reach -> {a.out}/clearance.json")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
