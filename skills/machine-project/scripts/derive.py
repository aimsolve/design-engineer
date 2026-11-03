#!/usr/bin/env python3
"""Per-part derived outputs of the cached model.

  python derive.py [--no-rebuild] [--cached-only] [--where FIELD=VALUE ...] SCOPE MANIFEST [PART_ID]
                                                                  cwd = project root; SCOPE in bought | print | step | laser | viewer | assembly

First brings the model up to date: when mechanics/cad/assemble.py exists, runs `cad_incremental.py --model assemble.py` there
(a subprocess; the cache decides which nodes run, usually none) so an export never reads a stale model; its `[cad_incremental]`
lines go to this log. A client-CAD project (no assemble.py) with `"model": "steps"` in kit.json gets model_from_steps.py instead. --no-rebuild skips that. Then reads ONLY build/model/parts.json + the part STEPs it lists (written by `cad_incremental.py --model`); never imports the project's
node code. Every result lives in build/derived/<key>/ with meta.json {"parts": [<part key>, ...]}:
key = sha of (part key, deriver name + sha of the deriver's code, the part fields the deriver uses). An existing key dir is
reused, never recomputed. At the end, GC deletes whatever the current parts.json and the manifests in build/kit/ do not reference (derived dirs whose meta lists a
sha that is no longer current, unknown dirs, part STEPs of old versions).

--cached-only (the router's probe): compute nothing; exit 3 as soon as a result is not in the cache yet, else write the manifest
piece as usual and exit 0. No GC either way; exit 4 at once when another run holds the project lock.

--where FIELD=VALUE (repeatable, all must hold) restricts the scope to the parts whose field equals VALUE (a list-valued field matches when
it contains VALUE; compared as text): a subset kit of a model that stays the WHOLE machine, e.g. `--where stage=s1` for the parts a project
marks `"stage": "s1"`. It is a view: the model, the keys and the GC are untouched, and the whole-machine STEP / viewer model of the
scope hold the matching parts only.

A part may carry `title` (a human name in any language) next to `name` (its id and file stem): the manifest `name`, the bought row name
(`desc` first: the trade name) and the drawing's title block use it; file names stay the `name`; the model STEP labels a part
`title`, else `desc`, else `name`; a part placed several times labels each copy by its placement (`shown_name`).

MANIFEST = the piece the router reads: {"parts": [{id, name, node, process, qty, material?, thickness?, files: [rel paths]}],
"model_step"?, "bought"?, "model_glb"?, "viewer_dir"?}; paths are relative to the project root. Last log line:
  [derive] <scope>: parts <K>, reused <R>, computed <C>

  bought  rows grouped by (`code`, name) (qty summed), no 3D
  print   per print part: STL laid flat on its largest planar face (or the part's `print_rot` [rx, ry, rz] / [axis, degrees]) + the
          part STEP; the part as at its first placement, a `print_with: <id>` part fused into that part's STL (no file of its own)
  step    STEP per mill/turn/other part + model.step (all part STEPs grouped by node, names + colours)
  laser   laser -> DXF contour, bent -> DXF unfold + PDF (+ STEP), via skills/shop-drawings (its own venv, drawings_env.sh)
  assembly one PDF per node: exploded view along the dockings, numbered balloons, parts table, big nodes in
          steps, via skills/shop-drawings assembly_drawing.py; manifest "assembly": [{node, files}]
  viewer  per-part mesh cached by part key; model.glb + parts.json + parts/ (the importer's out dir for web-viewer build.py) in
          one dir keyed by all meshes: a one-part change re-meshes one part. manifest "viewer_dir" = that dir (copy it, do not edit it).
"""
import hashlib
import inspect
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import procgroup
import text_i18n as T
from cad_incremental import fresh_since, kit_referenced_derived, project_lock        # stdlib-only at import; derive still never imports the project's node code
SKILLS = HERE.parents[1]
SHOP = SKILLS / "shop-drawings" / "scripts"
IMPORTER = SKILLS / "step-import" / "scripts"
ROOT = Path.cwd()
LANG = T.project_lang(ROOT)          # the texts derive writes for a person (purchase rows, units, a failed drawing's note)
MODEL = ROOT / "build" / "model"
DERIVED = ROOT / "build" / "derived"

SHEET = ("laser", "bent")
DEFLECTION, ANGLE = 0.5, 0.5


# ---------------------------------------------------------------- keys, cache
def sha(*objs):
    h = hashlib.sha256()
    for o in objs:
        h.update(o if isinstance(o, bytes) else json.dumps(o, sort_keys=True, ensure_ascii=False).encode())
        h.update(b"\0")
    return h.hexdigest()


def file_sha(*paths):
    return sha(*[Path(p).read_bytes() for p in paths])


def fn_sha(fn):
    return sha(inspect.getsource(fn).encode())


def rel(p):
    return str(Path(p).resolve().relative_to(ROOT.resolve()))


class NotCached(Exception):
    """--cached-only met a result that would have to be computed."""


CACHED_ONLY = False


class Cache:
    """build/derived/<key>/; counts reused / computed per unit."""

    def __init__(self):
        self.reused = self.computed = 0
        DERIVED.mkdir(parents=True, exist_ok=True)

    def dir(self, key):
        return DERIVED / key[:20]

    def get(self, key):
        d = self.dir(key)
        return d if (d / "meta.json").is_file() else None

    def build(self, key, shas, fill, count=True):
        """Return (dir, was_cached). fill(tmp_dir) writes the result; it lands atomically under its key."""
        d = self.get(key)
        if d:
            if count:
                self.reused += 1
            return d
        if CACHED_ONLY:
            raise NotCached(key)
        tmp = DERIVED / f".tmp-{os.getpid()}-{time.time_ns()}"
        tmp.mkdir()
        try:
            fill(tmp)
            (tmp / "meta.json").write_text(json.dumps({"parts": list(shas)}))
            d = self.dir(key)
            try:
                tmp.rename(d)
            except OSError:                                          # another derive won the race: keep its result
                shutil.rmtree(tmp, ignore_errors=True)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        if count:
            self.computed += 1
        return d


def gc(parts, start):
    """Delete what the model does not reference, except anything modified at or after `start` (a rebuild running next to us)."""
    cur = {p["key"] for p in parts}
    kit_used = kit_referenced_derived(ROOT)               # dirs a kit manifest still serves to clients
    for d in list(DERIVED.iterdir()) if DERIVED.is_dir() else []:
        if not d.is_dir() or fresh_since(d, start) or d.name in kit_used:
            continue
        if d.name.startswith(".tmp-"):
            if time.time() - d.stat().st_mtime > 3600:               # a crashed run; fresh ones may belong to a parallel derive
                shutil.rmtree(d, ignore_errors=True)
            continue
        try:
            listed = json.loads((d / "meta.json").read_text())["parts"]
        except (OSError, ValueError, KeyError):
            listed = None
        if listed is None or not set(listed) <= cur:
            shutil.rmtree(d, ignore_errors=True)
    step = MODEL / "step"
    for f in list(step.iterdir()) if step.is_dir() else []:
        if f.name not in {f"{s}.step" for s in cur} and not fresh_since(f, start):
            f.unlink()


def entry(p, files=(), **extra):
    e = {"id": p["id"], "name": p.get("title") or p["name"], "node": p.get("node"), "process": p["process"], "qty": p.get("qty", 1)}
    for k in ("material", "thickness", "finish", "code"):
        if p.get(k) not in (None, ""):
            e[k] = p[k]
    e["files"] = [rel(f) for f in files]
    if p.get("ids"):
        e["ids"] = p["ids"]
    e.update(extra)
    return e


def step_path(p):
    return ROOT / p["step"]


def placed(p):
    """[(name, node, 4x4 row-major matrix or None)] of a part: the placements of a part file's variant (its STEP is in its own
    coordinates), or the part itself where it stands (old layout: its STEP is already in world coordinates)."""
    pls = p.get("placements")
    if pls:
        return [(x["name"], x.get("node") or p.get("node"), x["loc"]) for x in pls]
    return [(p["id"], p.get("node"), None)]


def shown_name(p, placement, base=None):
    """A placement's human label: the part's name (or `base`), and for a repeat its suffix («Table leg_2»); an explicit name
    as is (with a base: the base and the copy's own suffix)."""
    rest = placement[len(p["id"]):] if placement.startswith(p["id"]) else None
    if rest is not None and re.fullmatch(r"(_\d+)?", rest):
        return (base or p["name"]) + rest
    return placement if base is None else base + (re.search(r"_\d+$", placement) or [""])[0]


def to_location(m):
    from build123d import Location
    from OCP.gp import gp_Trsf
    t = gp_Trsf()
    t.SetValues(*[float(x) for x in m[:12]])
    return Location(t)


def in_view(p, where):
    """--where FIELD=VALUE: the part's field equals VALUE as text, or is a list containing it."""
    for k, v in where:
        got = p.get(k)
        if got is None or not (v in [str(x) for x in got] if isinstance(got, (list, tuple)) else str(got) == v):
            return False
    return True


def safe(name):
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "part"


# ---------------------------------------------------------------- bought
BOUGHT_FIELDS = ("code", "qty", "desc", "unit", "supplier", "link", "kind", "class", "node", "name", "note", "title", "verified")
# the kinds whose wrong choice costs the client money, so their rows say whether the part was ever checked against the
# maker's document (fit_checks.MATING_KINDS says the same thing to the agent)
VERIFY_KINDS = ("fasteners", "electrical", "motors", "pneumatic", "bearings", "profile")


def d_bought(parts, cache, out, model_parts=()):
    """A bought part is a row, not a file: the cache holds the row per part (so the counts say what changed). Rows with the same
    (code, name) merge, qty summed, node = every node they come from. model_parts = every machine part, for the model-wide rows:
      buy: "length"   stock sold by length (hose, DIN rail): one row per (code, name) in metres, the copies' longest sides (or a
                      row's `buy_length`, mm) summed x buy_margin (1.2), rounded up; the name says the modelled length
      cut: {stock, note, label, total, code?, class?, all_total?}   any part: a piece of a cut list (profile, tube), one row per
                      piece length, a total per stock in metres, one more total per all_total; a bought part with cut has no own row
      screws_from_holes: {name, code, supplier, class, kind, unit, margin}   on a row-only part: screws counted from the model's
                      holes (_screws), rows from this template ({n} = 2 / 3, {L} = length); the part itself makes no row
      verified        the part's check against the maker's drawing or STEP (fit_checks.py) is carried into the row, and a
                      row of a VERIFY_KINDS part with no such record carries `verified: false`: the manager answers clients
                      out of these rows, so the row itself says whether the product in it was ever checked. Two parts merging
                      into one row, one of them unverified, make the row unverified"""
    rows = {}

    def add(r, nodes):
        k = (r["code"], r["name"])                           # one row per article AND name: two names under one code stay two rows
        if k in rows:
            rows[k][0]["qty"] += r["qty"]
            rows[k][1].update(nodes)
            if "verified" in rows[k][0] or "verified" in r:   # one unverified part makes the merged row unverified:
                rows[k][0]["verified"] = rows[k][0].get("verified") and r.get("verified") or False
        else:
            rows[k] = (dict(r), set(nodes))
    length = {}
    for p in parts:
        if p.get("counted_in") or p.get("cut") or p.get("screws_from_holes"):   # counted_in: a piece of a vendor assembly
            continue
        key = sha("bought-v2", fn_sha(d_bought), p["key"], {k: p.get(k) for k in BOUGHT_FIELDS}, "code" in p, LANG,
                  T.unit(p.get("unit", "pcs"), LANG))

        def fill(tmp, p=p):
            row = {"name": p.get("desc") or p.get("title") or p["name"], "code": p["code"] if "code" in p else p["name"],
                   "qty": p.get("qty", 1), "unit": T.unit(p.get("unit", "pcs"), LANG), "supplier": p.get("supplier", ""), "link": p.get("link", ""),
                   "kind": p.get("kind", ""), "class": p.get("class", ""), "node": p.get("node", ""), "note": p.get("note", ""),
                   **({"verified": p["verified"]} if p.get("verified") else
                      {"verified": False} if p.get("kind") in VERIFY_KINDS else {})}
            (tmp / "row.json").write_text(json.dumps(row, ensure_ascii=False))
        d = cache.build(key, [p["key"]], fill)
        r = json.loads((d / "row.json").read_text())
        if p.get("buy") == "length" and (p.get("step") or p.get("mesh") or "buy_length" in p):
            g = length.setdefault((r["code"], r["name"]), [r, set(), 0.0, p.get("buy_margin", 1.2)])
            g[1].add(r["node"])
            g[2] += p["buy_length"] * p.get("qty", 1) if "buy_length" in p else _geom(p, cache)["long"] * len(placed(p))
            continue
        add(r, [r["node"]])
    for (code, name), (r, nodes, mm, margin) in length.items():
        add(dict(r, name=T.t("bought.by_length", LANG, name=name, mm=mm), unit=T.t("unit.m", LANG),
                 qty=max(1, math.ceil(mm * margin / 1000))), nodes)
    bad = [p["id"] for p in model_parts if p.get("cut") and not p.get("step")]
    if bad:
        sys.exit(f"[derive] cut list pieces without a modelled shape (the length comes from it): {bad}")
    for r in _cut_rows([p for p in model_parts if p.get("cut")], cache):
        add(r, r.pop("nodes"))
    tpl = next((p["screws_from_holes"] for p in model_parts if p.get("screws_from_holes")), None)
    if tpl:
        for (n, L), count in sorted(_screws(model_parts, cache).items()):
            add({"name": tpl["name"].format(n=n, L=L), "code": tpl.get("code", "").format(n=n, L=L), "qty": math.ceil(count * tpl.get("margin", 1)),
                 "unit": T.unit(tpl.get("unit", "pcs"), LANG), "supplier": tpl.get("supplier", ""), "link": "", "kind": tpl.get("kind", ""),
                 "class": tpl.get("class", ""), "node": "", "note": ""}, [])
    out["bought"] = [dict(r, node=", ".join(sorted(x for x in nodes if x))) for r, nodes in rows.values()]


def _geom(p, cache):
    """A part's longest bounding-box side (its own coordinates, mm) and its round holes (_holes): read once per part version."""
    key = sha("part-geom-v1", fn_sha(_geom), fn_sha(_holes), fn_sha(_mesh_part), p["key"])

    def fill(tmp):
        if p.get("mesh"):                                     # view-only triangles: no holes to count
            b = _mesh_part(p)["bbox_mm"]
            (tmp / "geom.json").write_text(json.dumps({"long": max(hi - lo for lo, hi in zip(*b)), "holes": []}))
            return
        from build123d import import_step
        s = import_step(str(step_path(p)))
        z = s.bounding_box().size
        (tmp / "geom.json").write_text(json.dumps({"long": max(z.X, z.Y, z.Z), "holes": _holes(s)}))
    return json.loads((cache.build(key, [p["key"]], fill) / "geom.json").read_text())


def _holes(s):
    """Round holes of a shape that a M2 / M3 screw may go through: concave cylinders r 0.7 .. 2.3 along X, Y or Z, at least 0.5 mm
    long; each as its axis segment p0 -> p1 and diameter d (one per axis position and start, the face split in halves is one)."""
    from build123d import GeomType
    out, seen = [], set()
    for fc in s.faces():
        if fc.geom_type != GeomType.CYLINDER:
            continue
        try:
            r, ax = fc.radius, fc.axis_of_rotation
        except Exception:                                    # noqa: BLE001 - a face OCC cannot describe is no hole
            continue
        if r is None or ax is None or not 0.7 <= r <= 2.3:
            continue
        dv, ao = [ax.direction.X, ax.direction.Y, ax.direction.Z], [ax.position.X, ax.position.Y, ax.position.Z]
        k = max(range(3), key=lambda i: abs(dv[i]))
        if abs(dv[k]) < 0.999:
            continue
        p = fc.position_at(0.5, 0.5)
        n = fc.normal_at(p)
        if sum((ao[i] - c) * nv for i, (c, nv) in enumerate(zip((p.X, p.Y, p.Z), (n.X, n.Y, n.Z))) if i != k) <= 0:
            continue                                         # normal away from the axis: a boss, not a hole
        bb = fc.bounding_box()
        lo, hi = (bb.min.X, bb.min.Y, bb.min.Z)[k], (bb.max.X, bb.max.Y, bb.max.Z)[k]
        key = (k, round(ao[(k + 1) % 3], 1), round(ao[(k + 2) % 3], 1), round(lo, 1))
        if hi - lo < 0.5 or key in seen:
            continue
        seen.add(key)
        p0, p1 = list(ao), list(ao)
        p0[k], p1[k] = lo, hi
        out.append({"p0": p0, "p1": p1, "d": 2 * r})
    return out


SCREW_L = {2: (3, 4, 5, 6, 8, 10, 12, 16, 20), 3: (4, 5, 6, 8, 10, 12, 14, 16, 18, 20, 25, 30, 35, 40)}


def _screws(model_parts, cache):
    """{(2 | 3, length): count} of the M2 / M3 screws the model's holes ask for. The holes of every placed part (not `holes: false`)
    in machine coordinates; holes of different placed parts on one axis (within 0.4 mm) whose extents touch (gap <= 1 mm) are one
    stack = one screw, if it joins two placed parts. M2 when the smallest d < 2.2 or the largest < 2.9, else M3; all d >= 3.7 = a
    rivet / M4 joint, none. Length = the largest standard one <= the span of the core holes (d < 3.0 / 4.4: not the head pockets)
    - 0.5, else of all holes; a span over 45 is a lengthwise hole, none. `holes_skip_with: <regex>` on a part: a stack with it and
    a part whose id matches is no screw (a block over its own rail). Plus per placement `screws: [[n, L, count]]`, and
    `screws_unpaired` when that placement is in no counted stack (a `product` part counts no screws)."""
    import numpy as np
    H, who = [], []                                          # holes; who[i] = (part, placement name)
    for p in model_parts:
        if not p.get("step") or p.get("holes") is False or p.get("product"):
            continue
        hs = _geom(p, cache)["holes"]
        for name, _, m in placed(p):
            M = np.array(m, dtype=float).reshape(4, 4) if m is not None else np.eye(4)
            pi = len(who)
            who.append((p, name))
            for h in hs:
                w0, w1 = (M[:3, :3] @ np.array(h[x]) + M[:3, 3] for x in ("p0", "p1"))
                dv = w1 - w0
                k = int(np.argmax(np.abs(dv)))
                if abs(dv[k]) < 0.999 * np.linalg.norm(dv):
                    continue                                 # turned off the axes by its placement
                H.append({"i": pi, "k": k, "a": w0[(k + 1) % 3], "b": w0[(k + 2) % 3], "lo": min(w0[k], w1[k]), "hi": max(w0[k], w1[k]),
                          "d": h["d"]})
    par = list(range(len(H)))

    def root(x):
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x
    byax = {}
    for j, h in enumerate(H):
        byax.setdefault((h["k"], round(h["a"]), round(h["b"])), []).append(j)
    for j, h in enumerate(H):
        for da in (-1, 0, 1):
            for db in (-1, 0, 1):
                for m in byax.get((h["k"], round(h["a"]) + da, round(h["b"]) + db), []):
                    g = H[m]
                    if m > j and abs(g["a"] - h["a"]) <= 0.4 and abs(g["b"] - h["b"]) <= 0.4 and g["lo"] <= h["hi"] + 1.0 and h["lo"] <= g["hi"] + 1.0:
                        par[root(m)] = root(j)
    groups = {}
    for j in range(len(H)):
        groups.setdefault(root(j), []).append(H[j])
    count, used = {}, set()
    for g in groups.values():
        pis = {h["i"] for h in g}
        ids = {who[i][0]["id"] for i in pis}
        if len(pis) < 2 or any(re.search(who[i][0]["holes_skip_with"], o) for i in pis if who[i][0].get("holes_skip_with")
                                for o in ids - {who[i][0]["id"]}):
            continue
        ds = [h["d"] for h in g]
        if min(ds) >= 3.7:
            continue
        n = 2 if min(ds) < 2.2 or max(ds) < 2.9 else 3
        core = [h for h in g if h["d"] < (3.0 if n == 2 else 4.4)] or g
        span = max(h["hi"] for h in core) - min(h["lo"] for h in core)
        if span > 45:
            continue
        fit = [L for L in SCREW_L[n] if L <= span - 0.5]
        if not fit:
            span = max(h["hi"] for h in g) - min(h["lo"] for h in g)
            fit = [L for L in SCREW_L[n] if L <= span - 0.5]
        if not fit:
            continue
        count[(n, fit[-1])] = count.get((n, fit[-1]), 0) + 1
        used |= pis
    for i, (p, name) in enumerate(who + [(p, name) for p in model_parts if not p.get("step") or p.get("holes") is False
                                         for name, _, _ in placed(p)]):
        for n, L, c in p.get("screws") or []:
            count[(n, L)] = count.get((n, L), 0) + c
        if i >= len(who) or i not in used:
            for n, L, c in p.get("screws_unpaired") or []:
                count[(n, L)] = count.get((n, L), 0) + c
    return count


def _cut_rows(cut_parts, cache):
    """The cut list rows of the parts with `cut` (see d_bought), stock by stock in model order: the piece lengths longest first, then the
    stock's total; the all_total rows last."""
    stocks, out, all_total = {}, [], {}
    for p in cut_parts:
        c = p["cut"]
        st = stocks.setdefault(c["stock"], {"c": c, "len": {}})
        L = round(_geom(p, cache)["long"])
        for name, node, _ in placed(p):
            e = st["len"].setdefault(L, {"labels": [], "nodes": set()})
            e["labels"].append(c["label"])
            e["nodes"].add(node or p.get("node") or "")
    for stock, st in stocks.items():
        c = st["c"]
        base = {"unit": T.t("unit.pcs_cut", LANG), "supplier": "", "link": "", "kind": "profile", "class": c.get("class", ""), "code": c.get("code", ""), "note": ""}
        for L in sorted(st["len"], reverse=True):
            e = st["len"][L]
            out.append(dict(base, name=T.t("bought.cut_piece", LANG, stock=stock, length=L, note=c['note'],
                                           labels='; '.join(sorted(e['labels']))), qty=len(e["labels"]),
                            nodes=e["nodes"]))
        mm = sum(L * len(e["labels"]) for L, e in st["len"].items())
        out.append(dict(base, name=T.t("bought.cut_total", LANG, total=c['total'], count=sum(len(e['labels']) for e in st['len'].values())),
                        qty=round(mm / 1000, 3), unit=T.t("unit.m", LANG), nodes=set()))
        if c.get("all_total"):
            all_total[c["all_total"]] = all_total.get(c["all_total"], 0) + mm
    for name, mm in all_total.items():
        out.append({"name": name, "code": "", "qty": round(mm / 1000, 3), "unit": T.t("unit.m", LANG), "supplier": "", "link": "", "kind": "profile",
                    "class": next(st["c"].get("class", "") for st in stocks.values() if st["c"].get("all_total") == name), "note": "", "nodes": set()})
    return out


# ---------------------------------------------------------------- print
def _lay_flat(shape, rot=None):
    """The shape turned onto its print bed: `rot` = [rx, ry, rz] degrees, or [axis, degrees] with axis X / Y / Z (the part's
    `print_rot`) if given, else the largest planar face down; then moved so its bbox starts at the origin (z = 0 is the bed).
    No planar face: as modelled."""
    from build123d import Axis, Pos, Rot, Vector
    if rot is not None:
        if isinstance(rot[0], str):
            rot = [rot[1] if a == rot[0].upper() else 0 for a in "XYZ"]
        shape = Rot(*rot) * shape
    else:
        faces = [f for f in shape.faces() if str(f.geom_type).endswith("PLANE")]
        if faces:
            n = max(faces, key=lambda f: f.area).normal_at()
            down = Vector(0, 0, -1)
            axis = n.cross(down)
            if axis.length > 1e-9:
                shape = shape.rotate(Axis((0, 0, 0), (axis.X, axis.Y, axis.Z)), n.get_angle(down))
            elif n.Z > 0:
                shape = shape.rotate(Axis.X, 180)
    b = shape.bounding_box()
    return Pos(-b.min.X, -b.min.Y, -b.min.Z) * shape


IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


def _first_placed(p):
    """The part's shape as it stands at its first placement (print_rot and a fused partner are in machine axes)."""
    from build123d import import_step
    s = import_step(str(step_path(p)))
    m = (p.get("placements") or [{}])[0].get("loc")
    return s if m is None or [float(x) for x in m] == IDENTITY else s.moved(to_location(m))


def d_print(parts, cache, out, print_parts=()):
    """STL per print part, laid flat (see _lay_flat), plus the part's STEP. The STL is the part at its first placement; a print part
    with `print_with: <id>` has no file of its own: it is fused into that part's STL, each at its first placement (print_parts =
    every print part of the model, where the partners are found)."""
    from build123d import export_stl
    partners, by_id = {}, {q["id"]: q for q in print_parts}
    for q in print_parts:
        w = q.get("print_with")
        if w:
            if w not in by_id or w == q["id"] or by_id[w].get("print_with"):
                sys.exit(f"[derive] print part {q['id']!r} is printed with {w!r}: that must be another print part of the model, itself "
                         f"printed on its own (no chains)")
            partners.setdefault(w, []).append(q)
    for p in parts:
        if p.get("print_with"):
            continue
        with_ = partners.get(p["id"], [])
        if p.get("mesh_solid"):
            if with_:
                sys.exit(f"[derive] print part {p['id']!r} is a mesh part: it is not fused with {[q['id'] for q in with_]!r}; "
                         f"combine them in its part file (meshpart.solid) or print them apart")
            _print_mesh(p, cache, out)
            continue
        if any(q.get("mesh_solid") for q in with_):
            sys.exit(f"[derive] mesh part {[q['id'] for q in with_ if q.get('mesh_solid')][0]!r} cannot be printed with {p['id']!r}: "
                     f"combine them in the mesh part's file (meshpart.solid) or print them apart")
        key = sha("print-stl-v3", fn_sha(d_print), fn_sha(_lay_flat), fn_sha(_first_placed), p["key"], p["name"], p.get("print_rot"),
                  (p.get("placements") or [{}])[0].get("loc"), [(q["key"], (q.get("placements") or [{}])[0].get("loc")) for q in with_])

        def fill(tmp, p=p, with_=with_):
            s = _first_placed(p)
            if with_:
                s = s.fuse(*[_first_placed(q) for q in with_]).clean()
            export_stl(_lay_flat(s, p.get("print_rot")), str(tmp / f"{safe(p['name'])}.stl"), tolerance=0.02, angular_tolerance=0.1)
            shutil.copyfile(step_path(p), tmp / f"{safe(p['name'])}.step")
        d = cache.build(key, [p["key"]] + [q["key"] for q in with_], fill)
        out["parts"].append(entry(p, [d / f"{safe(p['name'])}.stl", d / f"{safe(p['name'])}.step"]))


def _print_mesh(p, cache, out):
    """A mesh part: its triangles as they stand at its first placement, laid flat like a STEP part (print_rot, else its
    largest flat face down). STL only: a mesh part has no STEP."""
    import meshpart
    first = (p.get("placements") or [{}])[0].get("loc")
    key = sha("print-mesh-stl-v1", fn_sha(_print_mesh), file_sha(HERE / "meshpart.py"), p["key"], p["name"], p.get("print_rot"), first)

    def fill(tmp):
        v, f = meshpart.read(ROOT / p["mesh"])
        v = meshpart.lay_flat(meshpart.moved(v, first), f, p.get("print_rot"))
        (tmp / f"{safe(p['name'])}.stl").write_bytes(meshpart.stl_bytes(v, f))
    d = cache.build(key, [p["key"]], fill)
    out["parts"].append(entry(p, [d / f"{safe(p['name'])}.stl"]))


# ---------------------------------------------------------------- step
def d_step(parts, cache, out, whole=True):
    for p in parts:
        if p["process"] in ("print", "laser", "bent", "bought"):
            continue
        key = sha("step-copy-v1", fn_sha(d_step), p["key"], p["name"])

        def fill(tmp, p=p):
            shutil.copyfile(step_path(p), tmp / f"{safe(p['name'])}.step")
        d = cache.build(key, [p["key"]], fill)
        out["parts"].append(entry(p, [d / f"{safe(p['name'])}.step"]))


def model_step(all_parts, cache, out):
    """Whole-machine STEP: every placement of every part STEP in one assembly, a group per node (a nested node `tower/carriage` is
    a group inside its parent's), names and colours kept. Written straight into an XCAF document: a part's geometry (its leaf
    shapes) is one product shared by all its placements, and each placement is a small assembly of its own carrying its name, so
    a machine with a thousand placements of a few hundred parts stays the size of its parts (a copy per placement OOM'd at 1001)."""
    key = sha("model-step-v5", fn_sha(model_step), fn_sha(_fix_step_names), fn_sha(placed), fn_sha(shown_name),
              [(p["key"], p["name"], p.get("title"), p.get("desc"), p.get("color"), placed(p)) for p in all_parts])

    def fill(tmp):
        from build123d import Color, import_step
        from OCP.APIHeaderSection import APIHeaderSection_MakeHeader
        from OCP.IFSelect import IFSelect_ReturnStatus
        from OCP.Interface import Interface_Static
        from OCP.Message import Message, Message_Gravity
        from OCP.STEPCAFControl import STEPCAFControl_Controller, STEPCAFControl_Writer
        from OCP.STEPControl import STEPControl_Controller, STEPControl_StepModelType
        from OCP.TCollection import TCollection_ExtendedString, TCollection_HAsciiString
        from OCP.TDataStd import TDataStd_Name
        from OCP.TDocStd import TDocStd_Document
        from OCP.TopLoc import TopLoc_Location
        from OCP.XCAFApp import XCAFApp_Application
        from OCP.XCAFDoc import XCAFDoc_ColorType, XCAFDoc_DocumentTool
        from OCP.XSControl import XSControl_WorkSession

        doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
        app = XCAFApp_Application.GetApplication_s()
        app.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), doc)
        app.InitDocument(doc)
        XCAFDoc_DocumentTool.SetLengthUnit_s(doc, 0.001)
        st, ct = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main()), XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
        st.SetAutoNaming_s(True)                        # unnamed leaves come out as SOLID / COMPOUND, as export_step wrote them

        def label(name, color=None, *lbls):
            for lb in lbls:
                if name:
                    TDataStd_Name.Set_s(lb, TCollection_ExtendedString(name))
                if color is not None:
                    ct.SetColor(lb, color.wrapped, XCAFDoc_ColorType.XCAFDoc_ColorGen)

        def assembly(parent, name, loc=None, color=None):
            a = st.NewShape()
            comp = st.AddComponent(parent, a, loc or TopLoc_Location())
            label(name, color, a, comp)
            return a

        root = st.NewShape()
        label(ROOT.name, None, root)
        groups = {}

        def group(path):                                # "tower/carriage" -> the carriage group inside the tower group
            if path not in groups:
                head, _, seg = path.rpartition("/")
                groups[path] = assembly(group(head) if head else root, seg)
            return groups[path]

        for p in all_parts:
            base = import_step(str(step_path(p)))
            pls = placed(p)
            shared = len(pls) > 1                       # placed once: nothing to share, the part's tree as export_step wrote it
            leaves = {}                                 # id(leaf shape) -> its one label

            def leaf(sh, name, color):
                """The leaf's product, named by its own label (a shared leaf cannot carry a copy's name: the part file's own
                label is left to auto naming, SOLID / COMPOUND), coloured as the part shows it; its instance named alike."""
                if id(sh) not in leaves:
                    own = name if not shared and sh is base else None if shared and sh.label == base.label else sh.label
                    lb = st.AddShape(sh.wrapped.Located(TopLoc_Location()), False)
                    label(own, color, lb)
                    leaves[id(sh)] = (lb, own or type(sh).__name__.upper())
                return leaves[id(sh)]

            def add_leaf(parent, sh, loc, name, color):
                lb, shown = leaf(sh, name, color)
                label(shown, sh.color, st.AddComponent(parent, lb, loc))   # its own colour per instance too (two maker nuts)

            def put(sh, parent, loc, name, color, top=False):
                if not sh.children and not shared:      # a one-shape part placed once: the shape itself under its name
                    return add_leaf(parent, sh, loc, name, color)
                a = assembly(parent, name, loc, color)
                for c in sh.children or [sh]:
                    cc = c.color if c.color is not None else color      # an uncoloured child shows its parent's colour
                    if c is sh:
                        add_leaf(a, c, TopLoc_Location(), name, color)
                    elif c.children:            # a copy's own wrapper by the copy's name (screws_2 > screws_2), the rest as is
                        put(c, a, c.wrapped.Location(), name if top and shared and c.label == base.label else c.label, cc)
                    else:
                        add_leaf(a, c, c.wrapped.Location(), c.label, cc)

            color = Color(p["color"]) if p.get("color") else base.color
            for name, node, m in pls:
                loc = base.wrapped.Location() if m is None else to_location(m).wrapped * base.wrapped.Location()
                put(base, group(node or "parts"), loc,
                    shown_name(p, name) if shared else p.get("title") or p.get("desc") or shown_name(p, name), color, top=True)
        st.UpdateAssemblies()

        for pr in Message.DefaultMessenger_s().Printers():     # OCCT writes only failures to the log, as export_step
            pr.SetTraceLevel(Message_Gravity.Message_Fail)
        writer = STEPCAFControl_Writer(XSControl_WorkSession(), False)
        writer.SetColorMode(True)
        writer.SetLayerMode(True)
        writer.SetNameMode(True)
        header = APIHeaderSection_MakeHeader(writer.Writer().Model())
        if not header.IsDone():
            header = APIHeaderSection_MakeHeader(0)
            header.Apply(writer.Writer().Model())
        header.SetName(TCollection_HAsciiString(ROOT.name))
        header.SetOriginatingSystem(TCollection_HAsciiString("build123d"))
        STEPCAFControl_Controller.Init_s()
        STEPControl_Controller.Init_s()
        Interface_Static.SetIVal_s("write.surfacecurve.mode", 1)
        writer.Transfer(doc, STEPControl_StepModelType.STEPControl_AsIs)
        f = tmp / "model.step"
        if writer.Write(str(f)) != IFSelect_ReturnStatus.IFSelect_RetDone:
            raise RuntimeError("model.step: the STEP writer failed")
        _fix_step_names(f)
    d = cache.build(key, [p["key"] for p in all_parts], fill, count=False)
    out["model_step"] = rel(d / "model.step")


def _fix_step_names(f):
    """The STEP writer stores product names double-encoded (UTF-8 bytes read as Latin-1, then UTF-8 again) and the header as raw
    UTF-8: undo the double encoding per string, then write every non-ASCII character as \\X2\\<UCS-2 hex>\\X0\\ (plain ASCII file).
    A string that is not UTF-8 at all (a maker's STEP of a bought part written in a code page) is read as cp1251 (the
    Russian makers'), else Latin-1."""
    def fix(m):
        b = m.group(0)
        try:
            s = b.decode("utf-8")
        except UnicodeDecodeError:
            try:
                s = b.decode("cp1251")
            except UnicodeDecodeError:
                s = b.decode("latin-1")
        try:
            s = s.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        return "".join(c if ord(c) < 128 else "\\X2\\%04X\\X0\\" % ord(c) for c in s).encode("ascii")
    Path(f).write_bytes(re.sub(rb"'[^']*'", fix, Path(f).read_bytes()))


# ---------------------------------------------------------------- laser / bent
SHEET_FIELDS = ("name", "material", "thickness", "t", "R", "K", "qty", "finish", "taps", "group", "dim_d", "note", "code", "weld", "title")


def shop_py():
    v = Path(os.environ.get("SHOP_DRAW_VENV", "/tmp/shopdrawvenv")) / "bin" / "python"
    return str(v)


def fail_reasons(log, name):
    """Reasons printed under `FAIL <name>` (shop_drawing.py) or `FAIL <folder>/<name>` (check_drawings.py) lines of a log.
    The name must match exactly: «Side» is not «Side left» (shop_drawing.py pads the name to 32 columns, or follows it with a colon)."""
    out, on = [], False
    for line in log.splitlines():
        m = re.match(r"FAIL\s+(.*)$", line)
        if m:
            head = m.group(1)
            if "/" in head and not re.search(r"\s{2,}", head):         # check_drawings: `FAIL <folder>/<name>`
                head = head.rsplit("/", 1)[1]
            rest = head[len(name):] if head.startswith(name) else None
            if rest is not None and rest.strip() and not (rest.startswith(":") or rest.startswith("  ") or (len(name) >= 32 and rest.startswith(" "))):
                rest = None                                              # a longer name that merely starts with ours
            on = rest is not None
            if on and rest.strip(": "):
                out.append(rest.strip(": "))
        elif on and line.startswith("      "):
            out.append(line.strip())
        else:
            on = False
    return out


def run_shop(script, *args, cwd=None, soft=False):
    """shop-drawings scripts run in their own venv, created on first use by drawings_env.sh. soft: a failing exit code is not fatal,
    the caller reads the per-part FAIL lines of the returned log."""
    cp = procgroup.run(["bash", str(SHOP / "drawings_env.sh"), script, *map(str, args)], cwd=cwd, capture_output=True, text=True)
    sys.stdout.write(cp.stdout[-3000:])
    if soft:
        if cp.returncode and not re.search(r"^FAIL ", cp.stdout, re.M):
            sys.stderr.write(cp.stderr[-3000:])
            raise SystemExit(f"[derive] {script} crashed (rc={cp.returncode})")
        return cp.stdout
    if cp.returncode:
        sys.stderr.write(cp.stderr[-3000:])
        raise SystemExit(f"[derive] {script} failed (rc={cp.returncode})")


def _invariants(p):
    """Rotation-invariant fingerprint of a part's solid: volume, surface area, sorted principal moments of inertia (bbox sides are not:
    the same plate lies turned in the assembly)."""
    from build123d import import_step
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    sh = import_step(str(step_path(p)))
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(sh.wrapped, g)
    m = g.PrincipalProperties().Moments()
    return (sh.volume, sh.area, *sorted(m))


def _chirality(p):
    """Handedness of a part's solid, a pseudo-scalar that flips sign under a mirror and not under any turn: det[v, u, Q v] / scale, with
    v = the 3rd moment vector int x |x|^2, Q = the inertia-like tensor int x x^T, u = int x (x^T Q x), all about the centroid. ~0 for a part with a
    mirror (or centre) symmetry: it is its own mirror image."""
    import numpy as np
    from build123d import import_step
    sh = import_step(str(step_path(p)))
    verts, tris = sh.tessellate(0.05, 0.2)
    V = np.array([[v.X, v.Y, v.Z] for v in verts])
    V = V - V.mean(axis=0)                                            # tetrahedra (0, A, B, C) over the surface triangles: exact for a closed mesh
    T = V[np.array(tris)]
    vol6 = np.einsum("ni,ni->n", T[:, 0], np.cross(T[:, 1], T[:, 2]))
    keast = [((1 / 6, 1 / 6, 1 / 6), 0.45), ((0.5, 1 / 6, 1 / 6), 0.45), ((1 / 6, 0.5, 1 / 6), 0.45), ((1 / 6, 1 / 6, 0.5), 0.45), ((0.25,) * 3, -0.8)]

    def integral(f):                                                  # degree-3 rule on each tetrahedron: exact for the cubic integrands below
        tot = 0
        for (l1, l2, l3), w in keast:
            tot = tot + w * np.einsum("n,n...->...", vol6 / 6.0, f(l1 * T[:, 0] + l2 * T[:, 1] + l3 * T[:, 2]))
        return tot
    vol = integral(lambda x: np.ones(len(x)))
    if abs(vol) < 1e-9:
        return 0.0
    sign = 1.0 if vol > 0 else -1.0                                   # inward-facing triangles flip every moment alike
    c = integral(lambda x: x) / vol
    Q = sign * integral(lambda x: np.einsum("ni,nj->nij", x - c, x - c))
    v = sign * integral(lambda x: (x - c) * np.einsum("ni,ni->n", x - c, x - c)[:, None])
    u = sign * integral(lambda x: (x - c) * np.einsum("ni,ij,nj->n", x - c, Q, x - c)[:, None])
    Qv = Q @ v
    if np.linalg.norm(v) < 1e-9 * vol * (np.trace(Q) / vol) ** 1.5:      # v = 0 about the centroid: centrally symmetric, so no handedness (a normalised det would only amplify noise)
        return 0.0
    scale = np.linalg.norm(v) * np.linalg.norm(u) * np.linalg.norm(Qv)
    return float(np.linalg.det(np.array([v, u, Qv])) / scale) if scale > 1e-9 else 0.0


def _same_handed(a, b):
    # Measured tessellation noise of symmetric parts is <= 2e-14 (centrally symmetric ones are zeroed in _chirality); a hole 0.5 mm off the
    # middle of a 100 mm bracket gives 1.7e-5. Opposite signs above 1e-8 are mirror images.
    return not (a * b < 0 and abs(a) > 1e-8 and abs(b) > 1e-8)


_SAME_FIELDS = tuple(f for f in SHEET_FIELDS if f not in ("name", "qty", "code", "thickness", "t"))


def _mismatch(ref, p):
    """Why `p` is not the same part as `ref` (same code), or None: different fields, process, volume / area (absolute 0.5), principal
    moments, or a mirror image of a bent part (a mirrored laser blank is the same blank turned over and may merge)."""
    thick = lambda q: q.get("t") if q.get("t") is not None else q.get("thickness")          # noqa: E731
    diff = [f for f in _SAME_FIELDS if ref.get(f) != p.get(f)]
    if thick(ref) != thick(p):
        diff.append("thickness")
    if diff:
        return f"{ref['id']} and {p['id']} differ in {', '.join(diff)}"
    if p["process"] != ref["process"]:
        return f"{ref['id']} is {ref['process']}, {p['id']} is {p['process']}"
    fa, fb = _invariants(ref), _invariants(p)
    if abs(fa[0] - fb[0]) > 0.5 or abs(fa[1] - fb[1]) > 0.5 or any(abs(x - y) > 2e-3 * max(abs(x), 1.0) for x, y in zip(fa[2:], fb[2:])):
        return (f"{ref['id']} and {p['id']} differ in shape (volume {fa[0]:.1f} / {fb[0]:.1f} mm3, area {fa[1]:.1f} / {fb[1]:.1f} mm2, "
                "principal moments)")
    if ref["process"] != "laser" and not _same_handed(_chirality(ref), _chirality(p)):
        return f"{ref['id']} and {p['id']} are mirror images of each other (a bent part is not its own mirror image)"
    return None


def group_identical(sheet, part_id=None):
    """Sheet parts that share one non-empty `code` are one drawing: the first by id stands for the group, qty is the sum, `ids` lists the
    members (`keys` too, when there are several: one part keeps its own key). The members must be the same part (_mismatch); if not, the
    group entry carries `merge_error` naming the pair, and d_laser makes it FAIL like any failed part. With part_id: only the group that
    part belongs to (its entry then carries that id)."""
    groups = {}
    for p in sheet:
        groups.setdefault(p.get("code") or ("\0" + p["id"]), []).append(p)
    out = []
    for code, ps in groups.items():
        if part_id and not any(p["id"] == part_id for p in ps):
            continue
        ps = sorted(ps, key=lambda p: p["id"])
        # --cached-only (the router's probe, in a container without build123d): the check needs geometry, and its verdict is already
        # in the cache: the drawing key holds every member's key, a failed check left an error note instead of a drawing
        why = None if CACHED_ONLY else next(filter(None, (_mismatch(ps[0], p) for p in ps[1:])), None)
        rep = dict(ps[0], qty=sum(p.get("qty", 1) for p in ps), ids=[p["id"] for p in ps])
        if len(ps) > 1:
            rep["keys"] = [p["key"] for p in ps]
        if why:
            rep["merge_error"] = f"parts with code {code!r} are not identical: {why}; give different parts different codes"
        if part_id:
            rep["id"] = part_id
        out.append(rep)
    return out


_CYR = T.translit()


def _error_note(p, reason, cache):
    """A failed laser part ships a plain-text `<stem><error.suffix>.txt` (text_i18n: `_ERROR` in English) in place of its drawing
    (the router lists only parts that have files)."""
    pid = str(p["id"])
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", "".join(_CYR.get(ch, ch) for ch in pid)).strip("_") or "part"
    if stem != pid:                                                   # transliterated / stripped: keep ids that collapse to one stem apart
        stem += "-" + hashlib.sha1(pid.encode("utf-8")).hexdigest()[:6]
    text, name = T.t("error.text", LANG, reason=reason), stem + T.t("error.suffix", LANG) + ".txt"

    def fill(tmp):
        (tmp / name).write_text(text, encoding="utf-8")
    d = cache.build(sha("laser-error-v1", fn_sha(_error_note), p["id"], text, name), [p["key"]], fill, count=False)
    return d / name


def d_laser(parts, cache, out):
    code = sha(*[(SHOP / f).read_bytes() for f in ("shop_drawing.py", "sheet_pdf.py", "laser_dxf.py", "unfold.py", "drawings_i18n.py")],
               fn_sha(d_laser), LANG)                # the texts on the sheets and the kit names: their table and the language
    todo = []
    keys = {}
    errors = {}
    for p in parts:
        if p.get("merge_error"):
            errors[p["id"]] = p["merge_error"]
            continue
        fields = {k: p.get(k) for k in SHEET_FIELDS}
        fields["t"] = p.get("t") if p.get("t") is not None else p.get("thickness")
        keys[p["id"]] = sha("sheet-drawing-v1", code, p.get("keys") or p["key"], fields)
        if cache.get(keys[p["id"]]):
            cache.reused += 1
        else:
            todo.append(p)
    if todo and CACHED_ONLY:                                # the drawings batch is the computing: the probe stops before it
        raise NotCached(todo[0]["id"])
    if todo:
        batch = DERIVED / f".tmp-laser-{os.getpid()}-{time.time_ns()}"
        (batch / "src").mkdir(parents=True)
        try:
            rows = []
            for p in todo:
                row = {k: p[k] for k in ("material", "qty", "finish", "taps", "group", "dim_d", "note", "R", "K", "code", "weld", "title")
                       if p.get(k) is not None}
                row.update(name=p["name"], step=str(step_path(p)), t=p.get("t") or p.get("thickness"))
                if row["t"] is None:
                    del row["t"]
                row.setdefault("material", "")
                rows.append(row)
            (batch / "src" / "parts.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
            log = run_shop("shop_drawing.py", "--parts", batch / "src" / "parts.json", "--out", batch / "files", "--no-zip",
                           "--lang", LANG, soft=True)
            log += "\n" + run_shop("check_drawings.py", batch / "files", soft=True)
            for p in todo:
                got = sorted(f for f in (batch / "files").rglob("*") if f.is_file() and f.stem == p["name"] and f.suffix in (".dxf", ".pdf", ".step"))
                why = fail_reasons(log, p["name"])
                if not why and len(got) != 3:
                    why = [f"expected dxf+pdf+step, got {[f.name for f in got]}"]
                if why:                                           # this part ships nothing; the others still do
                    errors[p["id"]] = "; ".join(why)[:600]
                    continue

                def fill(tmp, got=got):
                    for f in got:
                        shutil.copyfile(f, tmp / f.name)
                cache.build(keys[p["id"]], p.get("keys") or [p["key"]], fill)
        finally:
            shutil.rmtree(batch, ignore_errors=True)
    for p in parts:
        if p["id"] in errors:                                  # no drawing, but a note in its place: the kit and the list still show the part
            out["parts"].append(entry(p, [_error_note(p, errors[p["id"]], cache)], error=errors[p["id"]]))
            continue
        d = cache.dir(keys[p["id"]])
        files = sorted(f for f in d.iterdir() if f.suffix in (".dxf", ".pdf", ".step"))
        out["parts"].append(entry(p, files))


# ---------------------------------------------------------------- assembly drawings
def _mul(a, b):
    return [sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4)) for r in range(4) for c in range(4)]


def assembly_nodes(parts):
    """{node: [item]} for shop-drawings assembly_drawing.py: every placement of a part with a STEP, in the node that placed it;
    a fastener in the node of the part it is docked to (a screw placed by a folder node holds a part of another node).
    parent = the placement of the same node it is docked to; axis = [origin, direction] of the joint it is docked at on that
    parent, in world coordinates (the direction the part is pulled out along on the drawing). A node of fasteners only (screws
    a folder node placed without docking them) gets no drawing."""
    eye = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]
    pls = {}
    for p in parts:
        if not (p.get("step") or p.get("mesh_solid")):  # a row or a view-only mesh part: nothing to draw
            continue
        joints = {x["name"]: x.get("joint") for x in p.get("placements") or []}
        for name, node, m in placed(p):
            pls[name] = (p, node, m or eye, joints.get(name))

    def node_of(n, seen=()):
        p, node, _, j = pls[n]
        if p.get("kind") == "fasteners" and j and j.get("to") in pls and n not in seen:
            return node_of(j["to"], seen + (n,))
        return node
    nodes = {}
    for n, (p, node, m, j) in pls.items():
        nd = node_of(n)
        if not nd:
            continue
        it = {"name": n, "part": p["id"], "key": p["key"], "title": p.get("title") or p["name"], "code": p.get("code") or "",
              "kind": p.get("kind") or "", "loc": m, "parent": None, "axis": None,
              **({"step": str(step_path(p))} if p.get("step") else {"mesh": str(ROOT / p["mesh"])})}
        if j and j.get("to") in pls and node_of(j["to"]) == nd:
            q, _, mq, _ = pls[j["to"]]
            it["parent"] = j["to"]
            jq = (q.get("joints") or {}).get(j.get("at"))
            if jq and jq.get("loc"):
                w = _mul(mq, jq["loc"])
                it["axis"] = [[w[3], w[7], w[11]], [w[2], w[6], w[10]]]
        nodes.setdefault(nd, []).append(it)
    return {nd: its for nd, its in nodes.items() if any(it["kind"] != "fasteners" for it in its)}   # loose screws of a
                                                                                                   # folder node: no sheet


def d_assembly(parts, cache, out):
    """One PDF per node (never the whole machine): exploded view along the dockings, numbered balloons, the parts table; big
    nodes in steps. Manifest: "assembly": [{node, files}] (no part rows: the drawings belong to nodes)."""
    nodes = assembly_nodes(parts)
    code = sha(file_sha(*[SHOP / f for f in ("assembly_drawing.py", "sheet_pdf.py", "drawings_i18n.py")]), fn_sha(d_assembly),
               fn_sha(assembly_nodes), LANG)
    keys, todo, errors = {}, [], {}
    for nd, items in nodes.items():
        keys[nd] = sha("assembly-drawing-v1", code, nd, [{k: v for k, v in it.items() if k not in ("step", "mesh")} for it in items])
        if cache.get(keys[nd]):
            cache.reused += 1
        else:
            todo.append(nd)
    if todo and CACHED_ONLY:
        raise NotCached(todo[0])
    if todo:
        batch = DERIVED / f".tmp-assy-{os.getpid()}-{time.time_ns()}"
        (batch / "files").mkdir(parents=True)
        try:
            spec = {"nodes": [{"node": nd, "file": f"n{i}", "items": nodes[nd]} for i, nd in enumerate(todo)]}
            (batch / "nodes.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
            log = run_shop("assembly_drawing.py", "--nodes", batch / "nodes.json", "--out", batch / "files", "--lang", LANG, soft=True)
            for i, nd in enumerate(todo):
                f = batch / "files" / f"n{i}.pdf"
                why = fail_reasons(log, nd)
                if why or not f.is_file():
                    errors[nd] = "; ".join(why or ["no drawing written"])[:600]
                    continue
                cache.build(keys[nd], sorted({it["key"] for it in nodes[nd]}), lambda tmp, f=f, nd=nd: shutil.copyfile(f, tmp / f"{safe(nd)}.pdf"))
        finally:
            shutil.rmtree(batch, ignore_errors=True)
    if nodes and len(errors) == len(nodes):            # nothing drawn: a failed build, not «no nodes» on the page
        sys.exit(f"[derive] assembly: no node could be drawn: {errors}"[:2000])
    out["assembly"] = []
    for nd in nodes:
        if nd in errors:
            out["assembly"].append({"node": nd, "files": [], "error": errors[nd]})
            continue
        out["assembly"].append({"node": nd, "files": [rel(cache.dir(keys[nd]) / f"{safe(nd)}.pdf")]})


# ---------------------------------------------------------------- viewer
def _importer():
    sys.path.insert(0, str(IMPORTER))
    import step_import
    return step_import


def _viewer_code():
    """step_import.py, the RULES table it applies (params.py) and the words and texts it reads them with (import_i18n.py)
    are the code that makes a mesh and its catalogue fields."""
    return sha(file_sha(IMPORTER / "step_import.py", IMPORTER / "params.py", IMPORTER / "import_i18n.py"), fn_sha(d_viewer), fn_sha(_mesh_part),
               fn_sha(_smooth_normals), DEFLECTION, ANGLE, LANG)     # LANG: the texts import_i18n writes are the project's


def d_viewer(all_parts, cache, out, whole=True):
    si = _importer()
    import numpy as np
    code = _viewer_code()
    mesh_dirs = {}
    for p in all_parts:
        key = sha("viewer-mesh-v1", code, p["key"])

        def fill(tmp, p=p):
            m = _mesh_part(p) if p.get("mesh") else si.mesh_step_file(step_path(p), DEFLECTION, ANGLE)
            np.savez(tmp / "mesh.npz", v=m["v"], t=m["t"], qn=m["qn"])
            si.write_stl(str(tmp / "part.stl"), m["v"], m["t"])
            (tmp / "stats.json").write_text(json.dumps({"volume_mm3": m["volume_mm3"], "bbox_mm": m["bbox_mm"]}))
        mesh_dirs[p["id"]] = (cache.build(key, [p["key"]], fill), key)
    if not whole:
        return
    # one glb for the whole model, keyed by every mesh + the catalogue fields (name, node, colour) and placements it shows;
    # a part file's variant is meshed once in its own coordinates: one glb mesh, one node per placement carrying its matrix
    # (one viewer entry per placement)
    groups = motion_groups(all_parts)
    titles, children = _node_graph()
    seams = _copy_seams()
    # his tree only for a machine ported from his CAD (the machine node parts/__init__.py docks has a TITLE): a node ported
    # into a project of our own leaves that project's viewer as it was
    paths = node_paths(all_parts, titles, children, seams) if is_ported(titles, children) else None
    ckey = sha("viewer-model-v5", code, fn_sha(placed), fn_sha(shown_name), fn_sha(motion_groups), fn_sha(node_paths), fn_sha(is_ported),
               fn_sha(_node_graph), fn_sha(_copy_seams), titles,
               sorted((k, sorted(v)) for k, v in children.items()), sorted((k, sorted(v)) for k, v in seams.items()),
               [(mesh_dirs[p["id"]][1], p["id"], p["name"], p.get("color"), placed(p), p.get("card"), p.get("motion_group"),
                 [p.get("joints"), [pl.get("joint") for pl in p.get("placements") or []]]) for p in all_parts])

    def fill_model(tmp):
        items = []
        for p in all_parts:
            md = mesh_dirs[p["id"]][0]
            z = np.load(md / "mesh.npz")
            st = json.loads((md / "stats.json").read_text())
            for name, node, m in placed(p):
                if paths is not None:                  # the client's tree: his names, one catalogue entry per part
                    items.append({"id": p["id"], "name": p.get("title") or p["name"], "node": node, "path": paths.get(name),
                                  "color": p.get("color"), "mesh_key": p["id"], "matrix": m, "motion_group": groups.get(name),
                                  "card": p.get("card"), "v": z["v"], "t": z["t"], "qn": z["qn"], "volume_mm3": st["volume_mm3"],
                                  "bbox_mm": st["bbox_mm"], "step_src": str(step_path(p)) if p.get("step") else None,
                                  "stl_src": str(md / "part.stl")})
                    continue
                bbox = st["bbox_mm"]
                if m is not None and len(z["v"]):
                    M = np.array(m, dtype=float).reshape(4, 4)
                    w = z["v"] @ M[:3, :3].T + M[:3, 3]
                    bbox = [np.round(w.min(0), 3).tolist(), np.round(w.max(0), 3).tolist()]
                items.append({"id": name, "name": shown_name(p, name), "node": node, "color": p.get("color"),
                              **({"part_id": p["id"]} if name != p["id"] else {}),     # the kit's part/<id> of a repeat placement
                              "mesh_key": p["id"], "matrix": m, "motion_group": groups.get(name),
                              "card": dict(p["card"], **({"name": shown_name(p, name, p["card"]["name"])} if p["card"].get("name") else {}))
                              if p.get("card") else None,
                              "v": z["v"], "t": z["t"], "qn": z["qn"], "volume_mm3": st["volume_mm3"], "bbox_mm": bbox,
                              "step_src": str(step_path(p)) if p.get("step") else None, "stl_src": str(md / "part.stl")})
        si.write_model_dir(str(tmp), items, ROOT.name, DEFLECTION, ANGLE, quantize=True)
    d = cache.build(ckey, [p["key"] for p in all_parts], fill_model, count=False)
    out["viewer_dir"] = rel(d)
    out["model_glb"] = rel(d / "model.glb")


def is_ported(titles, children):
    """True when the machine is one ported from a client's CAD: a node parts/__init__.py imports has a TITLE."""
    return any(k in titles for k in children.get(".", ()))


def node_titles(root=None):
    """{node folder: TITLE} of every node whose __init__.py says `TITLE = "..."` (read, never imported): a node with a title is
    shown in the viewer as a group of that name per copy of the node, nested like the nodes (a model converted from a
    client's CAD shows his tree; step-import step_to_project.py writes the titles)."""
    return _node_graph(root)[0]


def _node_graph(root=None):
    """({node folder: TITLE}, {node folder: {sub-node folders its __init__.py imports}}), read with ast, never imported. A
    sub-node used by several nodes (a product the client reuses) lives in the folder of one of them and is imported by all."""
    import ast
    base = (root or ROOT) / "mechanics" / "cad" / "parts"
    titles, children = {}, {}
    for f in sorted(base.rglob("__init__.py")) if base.is_dir() else []:
        try:
            t = ast.parse(f.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, ValueError):
            continue
        me = str(f.parent.relative_to(base))
        kids = children.setdefault(me, set())
        for n in t.body:
            if isinstance(n, ast.Assign) and any(isinstance(x, ast.Name) and x.id == "TITLE" for x in n.targets) \
                    and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
                titles[me] = n.value.value
            elif isinstance(n, ast.ImportFrom):
                if n.level == 1 and not n.module:
                    pkg = f.parent
                elif n.level == 0 and n.module and n.module.split(".")[0] == "parts":
                    pkg = base.joinpath(*n.module.split(".")[1:])
                else:
                    continue
                for a in n.names:
                    if (pkg / a.name / "__init__.py").is_file():
                        kids.add(str((pkg / a.name).relative_to(base)))
    return titles, children


COPY_DOCK = re.compile(r'\.joints\["([^"]+)"\]\.connect_to\((c\d+)\[\d+\]\["shape"\]\.joints\["([^"]+)"\]\)')
BUILT = re.compile(r"^\s*(c\d+) = (\w+)\.build\(", re.M)


def _copy_seams(root=None):
    """{(joint on the docked-to part, joint of the docking part): {(folder of the node whose build() docks that way, folder of
    the sub-node it docks)}}: where a node docks a copy of a SUB-NODE (the docking part indexed in the sub-node's list,
    `cN[k]`, as step_to_project writes it). One copy of a reused sub-assembly ends there, even when it docks onto a
    sibling copy of itself."""
    base = (root or ROOT) / "mechanics" / "cad" / "parts"
    _, children = _node_graph(root)
    out = {}
    for f in sorted(base.rglob("__init__.py")) if base.is_dir() else []:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        me = str(f.parent.relative_to(base))
        kids = {k.rsplit("/", 1)[-1]: k for k in children.get(me, ())}
        built = {v: kids[m] for v, m in BUILT.findall(text) if m in kids}
        for at, var, by in COPY_DOCK.findall(text):
            if var in built:
                out.setdefault((at, by), set()).add((me, built[var]))
    return out


def node_paths(all_parts, titles, children=None, seams=None):
    """placement name -> [(group key, title), ...] from the outermost node to its own. A copy of node G is the docked subtree of
    G's parts reached from the placement up its joints while the part stays inside G (G's folder or a node G imports, at any
    depth), keyed by that subtree's top placement (so the 8 copies of a conveyor are 8 groups, each holding its own parts and
    sub-nodes). Above the top, the joint leads into the node that placed this copy: of the nodes importing G, the one the part
    it docks to belongs to (a sub-node the client reuses under two parents is shown under each of them)."""
    folder, up, seam = {}, {}, {}
    for p in all_parts:
        for pl in p.get("placements") or []:
            folder[pl["name"]] = pl.get("node") or p.get("node") or ""
            j = pl.get("joint")
            if isinstance(j, dict) and j.get("to"):
                up[pl["name"]] = j["to"]
                if (j.get("at"), j.get("by")) in (seams or {}):
                    seam[pl["name"]] = seams[(j.get("at"), j.get("by"))]   # maybe the anchor of a sub-node's copy (checked below)
    children = children or {}
    parents, below = {}, {}
    for g, ks in children.items():
        for k in ks:
            parents.setdefault(k, set()).add(g)

    def desc(g):                                    # g and every node it imports, at any depth
        if g not in below:
            below[g] = {g}
            for k in children.get(g, ()):
                below[g] |= desc(k)
        return below[g]

    def inside(name, g):
        f = folder.get(name, "")
        return f == g or f.startswith(g + "/") or f in desc(g)

    def up_to(g, targets):
        """The import chain from g's importer up to the nearest of `targets`: [importer, ..., target], or []."""
        todo, back = [g], {g: None}
        while todo:
            q = todo.pop(0)
            for r in sorted(parents.get(q, ())):
                if r not in back:
                    back[r] = q
                    if r in targets:
                        chain = [r]
                        while back[chain[-1]] != g:
                            chain.append(back[chain[-1]])
                        return chain[::-1]
                    todo.append(r)
        return []

    def parents_to(g, z, seen=()):
        """The nodes from g's importer up to the nearest one that holds z: [importer, ..., holder]. More than one when g is
        the anchor of a node that is itself docked by g (a node with no part of its own)."""
        qs = sorted(parents.get(g, ()))
        if not qs:                                  # no import graph: the folder above
            segs = g.split("/")
            up_ = next(("/".join(segs[:k]) for k in range(len(segs) - 1, 0, -1) if "/".join(segs[:k]) in titles), None)
            return [up_] if up_ else []
        held = [q for q in qs if inside(z, q)]
        if len(held) > 1:                           # z is itself in a sub-node both import: further up, a part placed by one
            w, seen_w = z, set()
            while w in folder and w not in seen_w:
                if folder[w] in held:
                    return [folder[w]]
                seen_w.add(w)
                w = up.get(w)
        if held:
            return [min(held, key=lambda q: len(desc(q)))]   # the nearest of them
        chains = [[q] + c for q in qs if q not in seen for c in [parents_to(q, z, seen + (g,))] if c and inside(z, c[-1])]
        return min(chains, key=len) if chains else []

    for name in list(seam):                      # a seam only where the part lies in the sub-node that is docked that way
        docked = {(F, sub) for F, sub in seam[name] if inside(name, sub)}
        if docked:                                   # one label pair may dock two levels (Machine->Frame, Frame->Leg):
            deep = min(len(desc(sub)) for _, sub in docked)   # the deepest sub-node holding the part is the one docked
            docked = {(F, sub) for F, sub in docked if len(desc(sub)) == deep}
            seam[name] = {F for F, _ in docked}
        else:
            del seam[name]
    out, orphan = {}, set()
    for name, f in folder.items():
        path, g, y, seen = [], f, name, set()
        while g is not None and len(path) < 64:
            while y in up and up[y] in folder and inside(up[y], g) and y not in seen and not (y in seam and folder[y] == g):
                seen.add(y)
                y = up[y]
            if g in titles:
                path.append(((g, y), titles[g]))
            z = up.get(y)
            if z is None or z not in folder:            # the top: the nodes that import this one hold it (a root node with no
                q, seen_q = g, set()                    # part of its own is docked by a sub-node's copy, keyed by the same top)
                while sorted(parents.get(q, ())) and q not in seen_q:
                    seen_q.add(q)
                    q = sorted(parents[q])[0]
                    if q in titles:
                        path.append(((q, y), titles[q]))
                        orphan.add((q, y))
                break
            targets = {q for q in seam.get(y, ()) if inside(z, q)} or seam.get(y, set())
            if len(targets) > 1:                    # both parents dock alike and both hold z: the one a part above z is placed by
                w, seen_w = z, set()
                while w in folder and w not in seen_w and folder[w] not in targets:
                    seen_w.add(w)
                    w = up.get(w)
                targets = {folder[w]} if w in folder and folder[w] in targets else targets
            chain = (up_to(g, targets) if targets else []) or parents_to(g, z)
            for q in chain[:-1]:                    # a node anchored by its own sub-node: its copy is keyed by the same top
                if q in titles:
                    path.append(((q, y), titles[q]))
            g = chain[-1] if chain else None
            y = z
        out[name] = path[::-1]
    # a node placed in its parent by a Location, not a joint (a ported node appended to a converted node's list) opened the
    # parent's group under its own top: it belongs to the parent's one copy reached by joints (or, for a top node holding
    # only such members, to one copy)
    keys = {}
    for path in out.values():
        for key, _ in path:
            keys.setdefault(key[0], set()).add(key)
    remap = {}
    for g, ks in keys.items():
        real = ks - orphan
        if len(real) == 1:
            remap.update({k: next(iter(real)) for k in ks & orphan})
        elif not real and g in children.get(".", ()) and len(ks) > 1:
            first = min(ks, key=str)
            remap.update({k: first for k in ks})
    if remap:
        out = {n: [(remap.get(k, k), t) for k, t in path] for n, path in out.items()}
    return out


def _mesh_part(p):
    """A mesh part's triangles as the viewer meshes a STEP: flat-shaded (a vertex per corner with its face's normal, welded where
    position and normal match), volume from the closed triangles, bbox."""
    import numpy as np
    m = json.loads((ROOT / p["mesh"]).read_text())
    V, T = np.array(m["v"], dtype=float).reshape(-1, 3), np.array(m["f"], dtype=np.int64).reshape(-1, 3)
    n = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    n /= np.where((ln := np.linalg.norm(n, axis=1, keepdims=True)) == 0, 1, ln)
    cn = _smooth_normals(V, T, n) if p.get("mesh_solid") else np.repeat(n, 3, axis=0)   # a smooth housing shaded smooth
    P, qn = V[T].reshape(-1, 3), np.round(cn * 127).astype(np.int64)
    _, first, inv = np.unique(np.hstack([np.round(P, 4) * 1e4, qn]).round().astype(np.int64), axis=0, return_index=True, return_inverse=True)
    vol = abs(np.einsum("ij,ij->i", V[T[:, 0]], np.cross(V[T[:, 1]], V[T[:, 2]])).sum()) / 6
    return {"v": P[first], "t": inv.reshape(-1, 3), "qn": qn[first].astype(np.int8), "volume_mm3": round(float(vol), 3),
            "bbox_mm": [np.round(V.min(0), 3).tolist(), np.round(V.max(0), 3).tolist()]}


def _smooth_normals(V, T, n, crease=30.0):
    """A normal per triangle corner: the area-weighted mean of the normals of the triangles around that vertex that turn less
    than `crease` degrees from this one. A curved skin reads smooth, a window edge or a hole stays sharp - what the viewer gets
    from a STEP's surfaces, made from triangles."""
    import numpy as np
    a = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    area = np.linalg.norm(a, axis=1)
    vert, face = T.reshape(-1), np.repeat(np.arange(len(T)), 3)        # corner c = 3 * face + k sits on vert[c]
    order = np.argsort(vert, kind="stable")
    start = np.searchsorted(vert[order], np.arange(len(V) + 1))
    deg = np.diff(start)[vert]                                          # triangles around each corner's vertex
    corner = np.repeat(np.arange(len(vert)), deg)
    pos = np.arange(len(corner)) - np.repeat(np.cumsum(deg) - deg, deg)
    other = face[order][start[vert[corner]] + pos]                      # (corner, a triangle around its vertex) pairs
    ok = np.einsum("ij,ij->i", n[face[corner]], n[other]) >= math.cos(math.radians(crease))
    out = np.zeros((len(vert), 3))
    np.add.at(out, corner[ok], n[other[ok]] * area[other[ok], None])
    return out / np.where((ln := np.linalg.norm(out, axis=1, keepdims=True)) == 0, 1, ln)


def motion_groups(all_parts):
    """placement name -> motion group: the part's own `motion_group`, else the group of the part it is docked to, up the joint
    graph (a screw docked on the pusher moves with the pusher; one screw file serves every group)."""
    own, up = {}, {}
    for p in all_parts:
        for pl in p.get("placements") or [{"name": p["id"]}]:
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


def viewer_part(p, cache, out):
    """One part (the viewer's per-part download): its cached mesh as STL."""
    d_viewer([p], cache, out, whole=False)
    key = sha("viewer-mesh-v1", _viewer_code(), p["key"])
    out["parts"].append(entry(p, [cache.dir(key) / "part.stl"]))


# ---------------------------------------------------------------- main
def rebuild():
    """Bring build/model up to date: the same command an agent runs; the node cache decides what (if anything) executes."""
    cad = ROOT / "mechanics" / "cad"
    if not (cad / "assemble.py").is_file():
        return rebuild_from_steps()
    if procgroup.run([sys.executable, "-c", "import build123d"], capture_output=True).returncode:
        raise SystemExit(f"[derive] {sys.executable} has no build123d, so it cannot rebuild the model: run derive.py with the worker "
                         "python (the one that has build123d), or pass --no-rebuild")
    cp = procgroup.run([sys.executable, str(HERE / "cad_incremental.py"), "--model", "assemble.py"], cwd=cad, capture_output=True,
                        text=True, env=dict(os.environ, CAD_MODEL_LOCK_HELD=os.path.realpath(ROOT)))
    for line in (cp.stdout + cp.stderr).splitlines():
        if line.startswith("[cad_incremental]"):
            print(line)
    if cp.returncode:
        sys.stderr.write((cp.stdout + cp.stderr)[-3000:])
        raise SystemExit(f"[derive] model rebuild failed (rc={cp.returncode})")


def rebuild_from_steps():
    """A client-CAD project (no node code) declares `"model": "steps"` in kit.json (optional "model_parts", "model_steps": paths of the
    parts list and the STEP dir, default build/parts.json and build/parts): its model is made from those by model_from_steps.py."""
    try:
        kit = json.loads((ROOT / "kit.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if kit.get("model") != "steps":
        return
    cp = procgroup.run([sys.executable, str(HERE / "model_from_steps.py"), str(ROOT), str(ROOT / kit.get("model_parts", "build/parts.json")),
                         str(ROOT / kit.get("model_steps", "build/parts"))], capture_output=True, text=True,
                        env=dict(os.environ, CAD_MODEL_LOCK_HELD=os.path.realpath(ROOT)))
    if cp.returncode:
        sys.stderr.write((cp.stdout + cp.stderr)[-3000:])
        raise SystemExit(f"[derive] model_from_steps failed (rc={cp.returncode})")


def main():
    if "--cached-only" in sys.argv:                     # the router's probe waits for nobody: a build holding the lock = ask later
        import fcntl
        (ROOT / "build" / "model").mkdir(parents=True, exist_ok=True)
        with open(ROOT / "build" / "model" / ".lock", "a") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("[derive] the project is being built: try again later (--cached-only)")
                sys.exit(4)
            os.environ["CAD_MODEL_LOCK_HELD"] = os.path.realpath(ROOT)
            return _main()
    with project_lock(str(ROOT)):                       # one writer per project: a rebuild, this derive and its GC never overlap
        os.environ["CAD_MODEL_LOCK_HELD"] = os.path.realpath(ROOT)
        _main()


def _main():
    global CACHED_ONLY
    no_rebuild = "--no-rebuild" in sys.argv
    CACHED_ONLY = "--cached-only" in sys.argv
    sys.argv = [a for a in sys.argv if a not in ("--no-rebuild", "--cached-only")]
    where, argv = [], []
    it = iter(sys.argv)
    for a in it:
        if a == "--where":
            w = next(it, "")
            if "=" not in w or w.startswith("="):
                sys.exit(f"[derive] --where needs FIELD=VALUE, got {w!r}")
            where.append(tuple(w.split("=", 1)))
        else:
            argv.append(a)
    sys.argv = argv
    if len(sys.argv) < 3 or sys.argv[1] not in ("bought", "print", "step", "laser", "viewer", "assembly"):
        sys.exit(__doc__)
    scope, manifest = sys.argv[1], Path(sys.argv[2])
    part_id = sys.argv[3] if len(sys.argv) > 3 else None
    if not no_rebuild:
        rebuild()
    start = time.time()
    pj = MODEL / "parts.json"
    if not pj.is_file():
        sys.exit("[derive] build/model/parts.json missing: run cad_incremental.py --model first")
    all_parts = json.loads(pj.read_text())["parts"]
    if any("key" not in p for p in all_parts):
        sys.exit("[derive] build/model/parts.json has no part keys (written by an older cad_incremental): rebuild the model first")
    parts = all_parts
    if part_id:
        parts = [p for p in all_parts if p["id"] == part_id]
        if not parts:
            sys.exit(f"[derive] no part with id {part_id!r}")
        if not all(in_view(p, where) for p in parts):
            sys.exit(f"[derive] part {part_id!r} does not match {' '.join(f'--where {k}={v}' for k, v in where)}")
    if scope != "viewer":                                  # `product: true` = what the machine makes (boxes, stacks), not a machine part
        all_parts_machine = [p for p in all_parts if not p.get("product")]
        parts = [p for p in parts if not p.get("product")]
    else:
        all_parts_machine = all_parts
    if where:                                              # a view: GC below still follows the whole model
        parts = [p for p in parts if in_view(p, where)]
        all_parts_machine = [p for p in all_parts_machine if in_view(p, where)]
    geometry = lambda p: bool(p.get("step"))               # a row-only bought part has no STEP: every geometry scope skips it; a mesh
    solid = lambda p: geometry(p) or bool(p.get("mesh_solid"))   # part (view-only triangles) is shown by the viewer only; a solid
    sel = {"bought": lambda p: p["process"] == "bought",   # mesh part is printed and drawn too, but has no STEP
           "print": lambda p: p["process"] == "print" and solid(p), "laser": lambda p: p["process"] in SHEET and geometry(p),
           "viewer": lambda p: (geometry(p) or bool(p.get("mesh"))) and p.get("in_viewer", True),   # in_viewer: false = not on the page
           "step": lambda p: p["process"] not in ("print", "laser", "bent", "bought") and geometry(p),
           "assembly": lambda p: solid(p) and p.get("in_viewer", True)}[scope]
    parts = [p for p in parts if sel(p)]
    if part_id and not parts and scope != "viewer" and any(p["id"] == part_id and p.get("product") for p in all_parts):
        sys.exit(f"[derive] part {part_id!r} is a product, excluded from kits")
    if part_id and not parts and scope == "viewer" and any(p["id"] == part_id and p.get("in_viewer") is False for p in all_parts):
        sys.exit(f"[derive] part {part_id!r} is not on the model page (in_viewer: false)")
    if part_id and not parts:
        sys.exit(f"[derive] part {part_id!r} has process {[p['process'] for p in all_parts if p['id'] == part_id][0]!r}, not for scope {scope}")
    t0 = time.time()
    cache = Cache()
    out = {"parts": []}
    try:
        _derive(scope, part_id, parts, all_parts, all_parts_machine, geometry, cache, out)
    except NotCached:
        print(f"[derive] {scope}: not cached yet (--cached-only)")
        sys.exit(3)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    if not CACHED_ONLY:
        gc(all_parts, start)
    print(f"[derive] {scope}: {time.time() - t0:.1f} s")
    print(f"[derive] {scope}: parts {len(parts)}, reused {cache.reused}, computed {cache.computed}")
    for e in out["parts"] + out.get("assembly", []):                # last lines of the log: what did not get made
        if e.get("error"):
            print(f"[derive] {scope}: FAILED {e.get('name') or e.get('node')}: {e['error']}")


def _derive(scope, part_id, parts, all_parts, all_parts_machine, geometry, cache, out):
    if scope == "bought":
        d_bought(parts, cache, out, [] if part_id else all_parts_machine)
    elif scope == "print":
        if part_id and parts[0].get("print_with"):
            sys.exit(f"[derive] part {part_id!r} is printed as one piece with {parts[0]['print_with']!r}: ask for that part")
        d_print(parts, cache, out, [p for p in all_parts if p["process"] == "print" and not p.get("product")])
    elif scope == "step":
        d_step(parts, cache, out)
        if not part_id:
            model_step([p for p in all_parts_machine if geometry(p)], cache, out)
            meshes = [p["id"] for p in all_parts_machine if p.get("mesh_solid")]
            if meshes:                                 # said, never silent: the client's STEP lacks the housing
                out["not_in_step"] = meshes
                print(f"[derive] step: mesh parts have no STEP and are not in model.step: {', '.join(meshes)}")
    elif scope == "laser":
        d_laser(group_identical([p for p in all_parts_machine if p["process"] in SHEET and geometry(p)], part_id), cache, out)
    elif scope == "assembly":
        if part_id:
            sys.exit("[derive] assembly drawings are per node: run the scope without a part")
        d_assembly(parts, cache, out)
    elif scope == "viewer":
        if part_id:
            viewer_part(parts[0], cache, out)
        else:
            d_viewer(parts, cache, out)


if __name__ == "__main__":
    main()
