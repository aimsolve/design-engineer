#!/usr/bin/env python3
"""«The client will not notice»: a project converted from his CAD (step_to_project.py) against what he had before.

    python migration_check.py ROOT --import build/client_import [--before DIR] [--shots BEFORE_SITE AFTER_SITE] [--out DIR]
    python migration_check.py ROOT --import build/client_import --part ID [--part ID ...]     # one part, after editing its code

ROOT      the converted project with its model built (build/model/parts.json + part STEPs: derive.py or cad_incremental ran)
--import  step_import.py's cut of the client's STEP the conversion started from (parts/<id>.step = his geometry)
--before  the project as it was (a copy: build/parts.json, build/kit/<scope>.json of its old download kits); without it the
          lists are compared with the reference only
--shots   two built viewer sites (web-viewer build.py --out): screenshots from the same standard views, side by side
--out     where the report goes (default ROOT/build/migration): report.md, report.json, shots/

Checks, each PASS / FAIL with its numbers (exit 1 on any FAIL):
  parts       the same set of parts, ids as the framework spells them (a dot is "_")
  placements  each part placed as many times as in his CAD; every placement matched one to one with his, the worst
              distance and turn printed (tolerance 0.001 mm, 1e-6 rad)
  names       title = his product name, code = his drawing number, for every part
  geometry    each part's model STEP against his part: recreated parts by recreate.compare (volume, bbox, surface deviation,
              holes, TOL there); his geometry kept: the same file; standard fasteners: the template within its fit tolerance
  lists       print / laser+bent / other / bought: the parts and rows before and after (derive.py manifests in build/kit/),
              each difference listed
  tree        the viewer's node tree (build/kit/viewer.json's model) against his: the same groups with the same names
  shots       the viewer before and after from the same angles (needs --shots), stitched side by side"""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import import_i18n as I  # noqa: E402  (look-alike letters of other scripts)

POS_TOL, ROT_TOL = 1e-3, 1e-6


def model_id(name):
    return re.sub(r"_+", "_", re.sub(r"[^\w]+", "_", str(name))).strip("_")


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


class Report:
    def __init__(self):
        self.rows, self.detail = [], {}

    def add(self, name, ok, summary, detail=None):
        """ok None: a check that could not be made (said so in the row, not counted as failed)."""
        self.rows.append((name, "NOT CHECKED" if ok is None else "PASS" if ok else "FAIL", summary))
        if detail:
            self.detail[name] = detail

    @property
    def ok(self):
        return all(r[1] != "FAIL" for r in self.rows)


def check_parts(rep, ref, model):
    want = {model_id(pid): pid for pid in ref["parts"]}
    have = {p["id"] for p in model}
    missing, extra = sorted(set(want) - have), sorted(have - set(want))
    rep.add("parts", not missing and not extra, f"{len(have)} in the model, {len(want)} in his CAD; missing {len(missing)}, extra {len(extra)}",
            {"missing": missing, "extra": extra})
    return want


def check_placements(rep, ref, model, want, removed=None):
    """removed: {part id: copies of his removed on purpose}: that many of his placements may have no model placement."""
    removed = {model_id(k): v for k, v in (removed or {}).items()}
    by_part = defaultdict(list)
    for pl in ref["placements"]:
        by_part[model_id(pl["part"])].append(np.array(pl["matrix"], dtype=float).reshape(4, 4))
    worst_t, worst_r, bad_count, unmatched = 0.0, 0.0, [], []
    n_model = 0
    for p in model:
        pls = [np.array(x["loc"], dtype=float).reshape(4, 4) for x in p.get("placements") or []]
        n_model += len(pls)
        src = by_part.get(p["id"], [])
        if len(src) - removed.get(p["id"], 0) != len(pls):
            bad_count.append(f"{p['id']}: {len(pls)} placements, his CAD {len(src)}")
        free = list(range(len(src)))
        for m in pls:                                     # greedy nearest: placements of one part are far apart
            if not free:
                break
            k = min(free, key=lambda j: np.linalg.norm(src[j][:3, 3] - m[:3, 3]) + np.abs(src[j][:3, :3] - m[:3, :3]).sum())
            free.remove(k)
            dt = float(np.linalg.norm(src[k][:3, 3] - m[:3, 3]))
            dr = float(np.abs(src[k][:3, :3] - m[:3, :3]).max())
            worst_t, worst_r = max(worst_t, dt), max(worst_r, dr)
            if dt > POS_TOL or dr > ROT_TOL:
                unmatched.append(f"{p['id']}: {dt:.4f} mm, {dr:.2e}")
    total_ref = len(ref["placements"]) - sum(removed.values())
    rep.add("placements", not bad_count and not unmatched and n_model == total_ref,
            f"{n_model} placements in the model, {total_ref} in his CAD{' less the ones removed on purpose' if removed else ''}; worst {worst_t:.6f} mm / {worst_r:.1e} rad "
            f"(tolerance {POS_TOL} mm / {ROT_TOL} rad); count differs for {len(bad_count)} part(s), off for {len(unmatched)}",
            {"count": bad_count, "off": unmatched[:50]})


def node_offset(placement, refs, model):
    """How far the node stands from where his machine has it: (mm, rad) of its frame against placement.json, or None when
    the node came on its own (`frame` own: any place is the agent's)."""
    if placement.get("frame") != "machine":
        return None
    moved = node_frame(placement, refs, model)
    T = None
    for r, n in zip(refs, moved):
        a, b = np.array(r["matrix"], dtype=float).reshape(4, 4), np.array(n["matrix"], dtype=float).reshape(4, 4)
        T = b @ np.linalg.inv(a)
        break
    if T is None:
        return None
    R = T[:3, :3]
    return float(np.linalg.norm(T[:3, 3])), float(np.arccos(np.clip((np.trace(R) - 1) / 2, -1, 1)))


def node_frame(placement, refs, model):
    """His placements of a ported node, moved into the model's frame: the node stands where the agent docked its anchor part,
    his CAD has that part at placement["matrix"]; of the anchor's placements in the model the one his others fit best."""
    aM = np.array(placement["matrix"], dtype=float).reshape(4, 4)
    anchor = next((p for p in model if p["id"] == model_id(placement["anchor"])), None)
    if anchor is None:
        return refs
    his = [(model_id(r["part"]), np.array(r["matrix"], dtype=float).reshape(4, 4)) for r in refs]
    ours = defaultdict(list)
    for p in model:
        for x in p.get("placements") or []:
            ours[p["id"]].append(np.array(x["loc"], dtype=float).reshape(4, 4))
    best, best_err = None, None
    for x in anchor.get("placements") or []:
        T = np.array(x["loc"], dtype=float).reshape(4, 4) @ np.linalg.inv(aM)
        err = sum(min((np.linalg.norm((T @ m)[:3, 3] - o[:3, 3]) for o in ours.get(pid, [])), default=1e9) for pid, m in his)
        if best_err is None or err < best_err:
            best, best_err = T, err
    if best is None:
        return refs
    return [dict(r, matrix=(best @ m).reshape(-1).tolist()) for r, (_, m) in zip(refs, his)]


def check_names(rep, ref, model, want):
    bad = []
    for p in model:
        r = ref["parts"].get(want.get(p["id"], ""), None)
        if r is None:
            continue
        if (p.get("title") or p.get("name")) != r["name"]:
            bad.append(f"{p['id']}: title {p.get('title')!r} vs his {r['name']!r}")
        if r.get("drawing") and r.get("kind") == "made" and not re.fullmatch(re.escape(r["drawing"]) + r"(-\d\d)?", p.get("code") or ""):
            bad.append(f"{p['id']}: code {p.get('code')!r} vs his drawing {r['drawing']!r}")
    rep.add("names", not bad, f"{len(model) - len(bad)} of {len(model)} parts carry his name and drawing number", {"differ": bad})


def _drawn_as_code(root, r):
    """True when a bought part's file takes a library part whose module carries no 3D file beside it (no .step / .stp /
    .brep): the library draws it as code, so it cannot be his 3D face for face."""
    f = root / (r.get("file") or "")
    if not f.is_file():
        return False
    m = re.search(r"^from library\.(\w+) import (\w+)", f.read_text(encoding="utf-8"), re.M)
    if not m:
        return False
    lib = root / "mechanics" / "cad" / "library" / m.group(1) / f"{m.group(2)}.py"
    return lib.is_file() and not any(lib.with_suffix(x).is_file() for x in (".step", ".stp", ".brep"))


def check_geometry(rep, root, ref, model, want, import_dir, report):
    import recreate as RC
    from build123d import import_step
    kinds = {r["id"]: r for r in report["parts"]}
    out, bad = [], []
    for p in model:
        pid = want.get(p["id"])
        if pid is None or not p.get("step"):
            continue
        r = kinds.get(pid, {})
        mine = import_step(str(root / p["step"])).wrapped
        his_file = (ref["parts"].get(pid) or {}).get("his_id") or pid     # a node port renamed an id the project had
        if not (import_dir / "parts" / f"{his_file}.step").is_file():
            raise SystemExit(f"{pid}: no {import_dir}/parts/{his_file}.step: --import must be the cut of the STEP this part came "
                             "from (a node port: build/node_import/<name>)")
        his = import_step(str(import_dir / "parts" / f"{his_file}.step")).wrapped
        if r.get("how") == "standard":                # the template's part against his: box sizes and centre in his frame
            fit = dict(r.get("fit") or {})
            bm, bh = RC._bbox(mine), RC._bbox(his)
            dims = max(abs((bm[k + 3] - bm[k]) - (bh[k + 3] - bh[k])) for k in range(3))
            centre = max(abs((bm[k + 3] + bm[k]) / 2 - (bh[k + 3] + bh[k]) / 2) for k in range(3))
            tol = fit.get("tol_mm") or max(0.6, 0.06 * max(bh[k + 3] - bh[k] for k in range(3)))
            fit.update(bbox_dims_mm=round(dims, 4), centre_mm=round(centre, 4), tol_mm=tol)
            ok = dims <= tol and centre <= tol
            row = {"id": pid, "kind": f"standard {r['template']} {' '.join(str(v) for v in r['args'].values())}", "ok": ok, **fit}
        elif r.get("kind") == "bought":               # his own 3D (the library's copy or vendor/): it must be the same shape
            fm, fh = sorted(round(RC._area(f), 6) for f in RC._faces(mine)), sorted(round(RC._area(f), 6) for f in RC._faces(his))
            vm, vh = RC._volume(mine), RC._volume(his)
            bb = max(abs(x - y) for x, y in zip(RC._bbox(mine), RC._bbox(his)))
            ok = len(fm) == len(fh) and all(abs(x - y) <= 1e-6 * max(1.0, y) for x, y in zip(fm, fh)) \
                and abs(vm - vh) <= 1e-9 * max(1.0, vh) and bb <= 1e-6
            row = {"id": pid, "kind": "bought (his 3D)", "ok": ok, "faces": [len(fm), len(fh)], "volume_rel": abs(vm - vh) / vh if vh else 0.0,
                   "bbox_mm": bb}
            if not ok and _drawn_as_code(root, r):    # a stand-in drawn as code (no maker 3D in the library): his shape within the
                c = RC.compare(his, mine)             # tolerances of a part drawn by hand; one with a 3D file must be his, exactly
                row = {"id": pid, "kind": "bought (drawn)", "ok": c["ok"], "volume_rel": c["volume_rel"], "bbox_mm": c["bbox_mm"],
                       "deviation_mm": c["deviation_mm"], "sym_diff_rel": c["sym_diff_rel"], "holes": c["holes"]}
                ok = c["ok"]
        else:
            c = RC.compare(his, mine)
            ok = c["ok"]
            kind = r.get("kind")
            if kind == "client":          # the converter left it on his geometry; the agent's code replaced it, or it still stands in
                kind = "client geometry" if p.get("standin_reason") else "by hand"
            row = {"id": pid, "kind": kind, "ok": ok, "volume_rel": c["volume_rel"], "bbox_mm": c["bbox_mm"],
                   "deviation_mm": c["deviation_mm"], "sym_diff_rel": c["sym_diff_rel"], "holes": c["holes"]}
            if p.get("standin_reason"):
                row["standin_reason"] = p["standin_reason"]
        out.append(row)
        if not ok:
            bad.append(row)
    cnt = Counter(r["kind"] if not str(r["kind"]).startswith("standard") else "standard" for r in out)
    rep.add("geometry", not bad, f"{len(out) - len(bad)} of {len(out)} parts within tolerance ({', '.join(f'{k} {v}' for k, v in sorted(cnt.items()))})",
            {"parts": out, "off": bad})
    return out


def _sizes(name):
    """The sizes a bought row names (M6x20, M12): a renamed row keeps them."""
    return set(re.findall(r"M\d+(?:[.,]\d+)?(?:x\d+(?:[.,]\d+)?)?", re.sub(r"\s*[x\u00d7" + I.like("x") + r"]\s*", "x", str(name))))


def _kit_rows(kit, scope, field):
    """The rows of one kit list: a list of row objects, None when the file is not there, "unreadable" when it cannot be used
    (not JSON, not a list of objects, a quantity that is not a number, no permission)."""
    f = kit / f"{scope}.json" if kit is not None else None
    if f is None or not f.exists():
        return None
    try:
        d = json.loads(f.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return "unreadable"
    rows = d.get(field) if isinstance(d, dict) else None
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows) \
            or not all(r.get("qty") is None or isinstance(r["qty"], (int, float)) and not isinstance(r["qty"], bool) for r in rows):
        return "unreadable"
    if field == "bought" and not all(isinstance(r.get("desc") or r.get("name"), str) for r in rows):
        return "unreadable"                           # a bought row is matched by its name
    return rows


def check_lists(rep, root, before, ref, want):
    """Download lists before and after: print, sheet (laser + bent), other (STEP files), bought. Every old list that can be
    read is compared and a difference in it fails; what could not be compared is said (NOT CHECKED when nothing failed)."""
    kit_a = root / "build" / "kit"
    kit = Path(before) / "build" / "kit" if before else None
    new = {s: _kit_rows(kit_a, s, "bought" if s == "bought" else "parts") for s in ("bought", "print", "laser", "step")}
    new = {s: (v if isinstance(v, list) else []) for s, v in new.items()}
    old = {s: _kit_rows(kit, s, "bought" if s == "bought" else "parts") for s in ("bought", "print", "other", "laser", "step")}
    if old["other"] is None:                          # the old STEP list: other.json (old kits) or step.json
        old["other"] = old.pop("step")
    else:
        old.pop("step")
    notes = []
    if kit is None or not kit.is_dir():
        rep.add("lists", None, f"--before {before}: no build/kit/ there, so the old download lists were not compared (the old "
                "project's kit lists, from derive.py)")
        return
    for s, v in old.items():
        if v == "unreadable":
            notes.append(f"the old {s} list is unreadable")
    key = lambda r: model_id(r.get("id") or "")                                    # noqa: E731
    out = {}
    scopes = (("print", "print", "print", ("print",)), ("laser + bent", "laser", "laser", ("laser", "bent")),
              ("other (STEP)", "step", "other", ("other", "turn", "mill")))
    for name, s_new, s_old, procs in scopes:
        a = {key(r): r.get("qty") for r in new[s_new] if r.get("process") in procs}
        b_rows = old[s_old]
        if not isinstance(b_rows, list):
            out[name] = {"after": len(a), "before": None, "added": sorted(a), "removed": [], "qty_changed": [], "compared": False}
            continue
        b = {key(r): r.get("qty") for r in b_rows if r.get("process") in procs}
        out[name] = {"after": len(a), "before": len(b), "added": sorted(set(a) - set(b)), "removed": sorted(set(b) - set(a)),
                     "qty_changed": sorted(k for k in set(a) & set(b) if a[k] != b[k]), "compared": True}
    # a sheet part that was a bare STEP before and is in the laser / bent kit now (its DXF and drawing beside the STEP) moved
    moved = sorted(set(out["other (STEP)"]["removed"]) & set(out["laser + bent"]["added"]))
    out["other (STEP)"]["removed"] = [k for k in out["other (STEP)"]["removed"] if k not in moved]
    out["laser + bent"]["added"] = [k for k in out["laser + bent"]["added"] if k not in moved]
    known = {key(r) for v in old.values() if isinstance(v, list) for r in v if "id" in r}
    gaps = [s_old for name, _, s_old, _ in scopes if not out[name]["compared"]]
    for name, _, _, _ in scopes if gaps else ():      # a list the old kit lacks: a part listed now (in any list) that no
        new_here = [k for k in out[name]["added"] if k not in known]   # old list has may have been in the missing one
        if new_here:
            notes.append(f"the old kit has no usable {', '.join(gaps)} list and {len(new_here)} part(s) now in {name} are in "
                         f"no old list ({', '.join(new_here[:6])}{', ...' if len(new_here) > 6 else ''})")
            out[name]["unverified"] = new_here
    ab, bb = new["bought"], old["bought"]
    bought_ok = isinstance(bb, list)
    if not bought_ok and old["bought"] != "unreadable":
        notes.append("the old kit has no bought list")
    bb = bb if bought_ok else []
    qa, qb = sum(r.get("qty") or 0 for r in ab), sum(r.get("qty") or 0 for r in bb)
    na, nb = Counter(), Counter()
    for rows, c in ((ab, na), (bb, nb)):
        for r in rows:
            c[r.get("desc") or r.get("name")] += r.get("qty") or 0
    removed, added = sorted(k for k in nb if k not in na), sorted(k for k in na if k not in nb)
    row_a, row_b = ({(r.get("desc") or r.get("name")): r for r in rows} for rows in (ab, bb))

    def same_item(k, x):
        """His row k may be our row x renamed: the same kind and article, or a fastener of his with no article given the
        standard name of its size (the converter's templates: «hex screw … M6 x 20» -> «Hex bolt … M6x20 DIN 933»)."""
        rb, ra = row_b[k], row_a[x]
        if (rb.get("kind") or "") != (ra.get("kind") or ""):
            return False
        cb, ca = str(rb.get("code") or ""), str(ra.get("code") or "")
        return cb == ca if cb else rb.get("kind") == "fasteners"
    renamed, free = [], list(added)
    for k in sorted(removed, key=lambda r: (-len(_sizes(r)), r)):   # a row of his renamed (a standard name for his screw):
        fit = [x for x in free if na[x] == nb[k] and _sizes(k) <= _sizes(x) and same_item(k, x)]   # same pieces and sizes; rows naming
        m = min(fit, key=lambda x: (len(_sizes(x) - _sizes(k)), x)) if fit else None   # sizes first, the closest match
        if m is not None:
            renamed.append(f"{k} -> {m}")
            free.remove(m)
    lost_rows = [k for k in removed if not any(r.startswith(k + " -> ") for r in renamed)]
    def per_name(rows, field):                        # rows of one name may repeat: their set of values, order aside
        out_ = {}
        for r in rows:
            v = str(r.get(field) or "")
            if v:
                out_.setdefault(r.get("desc") or r.get("name"), set()).add(v)
        return out_
    now = {k: k for k in nb if k in na}               # his row -> ours: the same name, or the one it was renamed to
    now.update(r.split(" -> ", 1) for r in renamed)
    diffs = {}
    for field in ("code", "unit", "kind"):            # every field his list shows, through renames as well
        va, vb = per_name(ab, field), per_name(bb, field)
        diffs[field] = sorted(f"{k}: {', '.join(sorted(vb[k]))} -> {', '.join(sorted(va.get(m, set())))}"
                              for k, m in now.items() if k in vb and va.get(m, set()) != vb[k])
        diffs[field + "_new"] = sorted(m for k, m in now.items() if k not in vb and m in va)   # his had none: told
    code_changed, unit_changed, kind_changed = diffs["code"], diffs["unit"], diffs["kind"]
    code_new, unit_new = diffs["code_new"], diffs["unit_new"]
    out["bought"] = {"after_rows": len(ab), "before_rows": len(bb) if bought_ok else None, "after_qty": qa,
                     "before_qty": qb if bought_ok else None, "removed": removed, "added": sorted(free), "renamed": renamed,
                     "lost": lost_rows, "compared": bought_ok, "code_changed": code_changed, "code_new": code_new,
                     "unit_changed": unit_changed, "unit_new": unit_new, "kind_changed": kind_changed,
                     "qty_changed": sorted(f"{k}: {nb[k]} -> {na[k]}" for k in nb if k in na and na[k] != nb[k]),
                     "after_names": sorted(f"{r.get('name')} | {r.get('code')} | {r.get('qty')} | {r.get('kind')}" for r in ab),
                     "before_names": sorted(f"{r.get('name')} | {r.get('code')} | {r.get('qty')} | {r.get('kind')}" for r in bb)}
    made_all = {model_id(pid) for pid, r in ref["parts"].items() if r["kind"] == "made"}
    listed = {key(r) for s in ("print", "laser", "step") for r in new[s]
              if r.get("process") in ("print", "laser", "bent", "other", "turn", "mill")}
    lost = sorted(made_all - listed)
    failed = bool(lost) or (bought_ok and (qa < qb or out["bought"]["qty_changed"] or lost_rows or code_changed
                                           or unit_changed or kind_changed)) \
        or any(v["removed"] or v["qty_changed"] for k, v in out.items() if k != "bought")
    summ = "; ".join(f"{k}: {'?' if v['before'] is None else v['before']} -> {v['after']}" for k, v in out.items() if k != "bought")
    summ += (f"; moved from STEP to laser / bent: {len(moved)}; bought rows {len(bb) if bought_ok else '?'} -> {len(ab)}, "
             f"pieces {qb if bought_ok else '?'} -> {qa} (his rows renamed {len(renamed)}, gone or changed {len(lost_rows)}, "
             f"quantity changed {len(out['bought']['qty_changed'])}, article changed {len(code_changed)}, unit changed "
             f"{len(unit_changed)}, kind changed {len(kind_changed)}, article given where his had none {len(code_new)}, "
             f"unit given where his had none {len(unit_new)}, added "
             f"{len(out['bought']['added'])}); made parts in no "
             f"list: {len(lost)}")
    if notes:
        summ += "; NOT COMPARED: " + "; ".join(notes)
    out["moved_to_laser"] = moved
    rep.add("lists", False if failed else (None if notes else True), summ, dict(out, made_in_no_list=lost, not_compared=notes))


def check_tree(rep, root, ref, import_dir, added=(), removed=None):
    """added: group paths added on purpose (not in his CAD); removed: {group path: copies of his removed on purpose}."""
    """The viewer model's node tree: every group of his CAD (by its path of names) is there, as many copies as in his CAD
    (a copy merged into another, or one split in two, shows as a count that differs)."""
    vj = root / "build" / "kit" / "viewer.json"
    if not vj.is_file():
        rep.add("tree", False, "no build/kit/viewer.json: run derive.py viewer first")
        return
    vd = root / load(vj)["viewer_dir"]
    have, his = Counter(), Counter()                     # group path -> copies

    def walk(n, path, into, name):
        path = path + [name(n, path)]
        into["/".join(path)] += 1
        for c in n.get("children") or []:
            if c.get("children") is not None:
                walk(c, path, into, name)
    walk(load(vd / "parts.json")["assembly"]["tree"], [], have, lambda n, path: n["name"])
    inst = Path(import_dir) / "instances.json"
    if inst.is_file():                                   # his groups, named as reference.json names them (root: the product)
        walk(load(inst), [], his, lambda n, path: n.get("product") or n["name"])   # the viewer names a group by its product (TITLE)
        his = Counter({k: v for k, v in his.items()})
    else:
        for pl in ref["placements"]:
            for k in range(1, len(pl["path"])):
                his["/".join(pl["path"][:k])] = 1
    for g, k in (removed or {}).items():
        if g in his:
            his[g] -= k
    his = Counter({g: k for g, k in his.items() if k > 0})
    missing = sorted(g for g in his if g not in have)
    extra = sorted(g for g in have if g not in his and not any(g == a or g.startswith(a + "/") for a in added))
    differ = sorted(f"{g}: {his[g]} -> {have[g]}" for g in his if g in have and have[g] != his[g])
    rep.add("tree", not missing and not extra and not differ, f"{len(have)} group paths in the viewer, {len(his)} in his CAD; "
            f"missing {len(missing)}, not in his CAD {len(extra)}, a different number of copies {len(differ)}",
            {"missing": missing[:40], "extra": extra[:40], "copies_differ": differ[:40]})


def shots(rep, before_site, after_site, out):
    """The two sites served side by side, each screenshotted from the standard views; one PNG per view, before | after."""
    sys.path.insert(0, str(HERE.parents[1] / "web-viewer" / "templates" / "assembly"))
    from viewer_stack_server import serve_stack, REPO
    from playwright.sync_api import sync_playwright
    import shutil
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="migration_shots_"))
    for name, site in (("shot-before", before_site), ("shot-after", after_site)):   # a project id has a hyphen (the stack's rule)
        shutil.copytree(site, tmp / name, symlinks=True)
    srv, base = serve_stack(tmp, REPO)
    out.mkdir(parents=True, exist_ok=True)
    files = []
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(args=["--use-angle=metal", "--enable-gpu", "--ignore-gpu-blocklist"])
            try:
                for view in ("iso", "front", "top", "right"):
                    imgs = []
                    for name in ("shot-before", "shot-after"):
                        ctx = b.new_context(viewport={"width": 1280, "height": 800}, device_scale_factor=1)
                        page = ctx.new_page()
                        page.goto(f"{base}/{name}/?app=0", wait_until="load")
                        page.wait_for_function("window.__viewer && (window.__viewer.ready || window.__viewer.error)", timeout=240000)
                        page.evaluate(f"window.__viewer.setView({json.dumps(view)})")
                        page.wait_for_timeout(2500)
                        f = out / f"{view}_{name}.png"
                        page.screenshot(path=str(f))
                        imgs.append(f)
                        ctx.close()
                    files.append((view, imgs))
            finally:
                b.close()
    finally:
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    diffs = stitch(files, out)
    rep.add("shots", shots_ok(diffs), "views " + ", ".join(f"{v}: {100 * d:.1f}% pixels differ" for v, d in diffs) + f" (side by side in {out})",
            {"views": diffs})


SHOT_LIMIT = 0.05      # share of pixels of a view that may differ: edge antialiasing and tessellation give ~2 %; a part
                       # missing or moved gives more


def shots_ok(diffs):
    return all(d <= SHOT_LIMIT for _, d in diffs)


def stitch(files, out):
    """[(view, [before.png, after.png])] -> out/<view>.png side by side; [(view, share of pixels that differ by more than 40)]."""
    from PIL import Image
    out.mkdir(parents=True, exist_ok=True)
    diffs = []
    for view, (fa, fb) in files:
        A, B = Image.open(fa).convert("RGB"), Image.open(fb).convert("RGB")
        W = Image.new("RGB", (A.width + B.width + 10, max(A.height, B.height)), "white")
        W.paste(A, (0, 0))
        W.paste(B, (A.width + 10, 0))
        W.save(out / f"{view}.png")
        a, b_ = np.asarray(A, dtype=float), np.asarray(B, dtype=float)
        h, w = min(a.shape[0], b_.shape[0]), min(a.shape[1], b_.shape[1])
        diffs.append((view, float((np.abs(a[:h, :w] - b_[:h, :w]).max(2) > 40).mean())))
    return diffs


def write(rep, out):
    out.mkdir(parents=True, exist_ok=True)
    lines = ["| check | result | numbers |", "|---|---|---|"] + [f"| {n} | {r} | {s} |" for n, r, s in rep.rows]
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "report.json").write_text(json.dumps({"rows": rep.rows, "detail": rep.detail}, ensure_ascii=False, indent=1, default=float),
                                     encoding="utf-8")
    print("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("root")
    ap.add_argument("--import", dest="import_dir", required=True)
    ap.add_argument("--before")
    ap.add_argument("--shots", nargs=2, metavar=("BEFORE_SITE", "AFTER_SITE"))
    ap.add_argument("--out")
    ap.add_argument("--skip", action="append", default=[], choices=("geometry", "lists", "tree"),
                    help="a check to skip (geometry is the slow one); reported as NOT CHECKED")
    ap.add_argument("--part", action="append", default=[], metavar="ID",
                    help="only these parts (his id): their geometry and placements, nothing else - the check an agent runs after "
                         "writing a part's code by hand (the model built first: derive.py or cad_incremental --model)")
    ap.add_argument("--migration", help="the port's migration folder (default mechanics/cad/migration; a node port writes "
                                        "mechanics/cad/migration/<node>/, found by itself from --part)")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    mig = Path(a.migration).resolve() if a.migration else root / "mechanics" / "cad" / "migration"
    if not a.migration and a.part:                    # a node port keeps its reference in a folder of its own
        ids = {model_id(x) for x in a.part}
        for f in sorted((root / "mechanics" / "cad" / "migration").rglob("reference.json")):
            if ids & {model_id(k) for k in load(f)["parts"]}:
                mig = f.parent
                break
    ref = load(mig / "reference.json")
    report = load(mig / "report.json")
    model = load(root / "build" / "model" / "parts.json")["parts"]
    rep = Report()
    if (mig / "placement.json").is_file():            # a node: his frame is the node's export; the model's is where it is docked
        pj = load(mig / "placement.json")
        off = node_offset(pj, ref["placements"], model)
        if off is not None:                           # his machine holds the node: it must stand where his machine has it
            rep.add("node frame", off[0] <= POS_TOL and off[1] <= ROT_TOL,
                    f"the node «{pj.get('node')}» stands {off[0]:.6f} mm / {off[1]:.2e} rad from where his machine has it "
                    f"(placement.json; tolerance {POS_TOL} mm / {ROT_TOL} rad)")
        else:                                         # exported on its own: his CAD says nothing of its place in the machine
            rep.add("node frame", None, f"the node «{pj.get('node')}» came exported on its own (placement.json "
                    "frame own), so where it stands in the machine is the agent's docking, not his CAD's; its parts are "
                    "checked against each other")
        ref = dict(ref, placements=node_frame(pj, ref["placements"], model))
    if a.part:                                        # one part at a time: the agent's loop over the parts left on his geometry
        ids = {model_id(x) for x in a.part}
        unknown = sorted(ids - {model_id(k) for k in ref["parts"]}) + sorted(ids - {p["id"] for p in model})
        if unknown:                                   # a typo must not pass as "0 of 0 parts within tolerance"
            sys.exit(f"--part: not a part of his CAD or of the built model: {', '.join(dict.fromkeys(unknown))}")
        model = [p for p in model if p["id"] in ids]
        ref = dict(ref, parts={k: v for k, v in ref["parts"].items() if model_id(k) in ids},
                   placements=[pl for pl in ref["placements"] if model_id(pl["part"]) in ids])
        want = {model_id(k): k for k in ref["parts"]}
        check_placements(rep, ref, model, want)
        check_names(rep, ref, model, want)
        check_geometry(rep, root, ref, model, want, Path(a.import_dir).resolve(), report)
        rep.add("lists, tree, shots", None, "a --part run checks these parts only; the download lists, the viewer tree and "
                "the screenshots are the full check's (no --part)")
        write(rep, Path(a.out) if a.out else root / "build" / "migration" / "part")
        sys.exit(0 if rep.ok else 1)
    added_f = root / "mechanics" / "cad" / "migration" / "added.json"   # added on purpose: {part id or group path: why}
    added = load(added_f) if added_f.is_file() else {}
    added_groups = [k for k in added if "/" in k]
    if added:
        rep.add("added", True, f"{len(added)} part(s) / group(s) added to his machine on purpose: "
                + "; ".join(f"{k} ({v})" for k, v in added.items()), {"added": added})
        model = [p for p in model if p["id"] not in added and model_id(p["id"]) not in {model_id(k) for k in added}]
    removed_f = root / "mechanics" / "cad" / "migration" / "removed.json"   # of his, removed on purpose:
    removed = load(removed_f) if removed_f.is_file() else {}                # {part id or group path: {"copies": n, "why": ...}}
    if removed:
        rep.add("removed", True, f"{len(removed)} part(s) / group(s) of his removed on purpose: "
                + "; ".join(f"{k} ×{v['copies']} ({v['why']})" for k, v in removed.items()), {"removed": removed})
    removed_parts = {k: v["copies"] for k, v in removed.items() if "/" not in k}
    removed_groups = {k: v["copies"] for k, v in removed.items() if "/" in k}
    want = check_parts(rep, ref, model)
    check_placements(rep, ref, model, want, removed_parts)
    check_names(rep, ref, model, want)
    if "geometry" not in a.skip:
        check_geometry(rep, root, ref, model, want, Path(a.import_dir).resolve(), report)
    else:
        rep.add("geometry", None, "skipped (--skip geometry)")
    if "lists" in a.skip:
        rep.add("lists", None, "skipped (--skip lists)")
    elif not a.before:                                # no old lists: what is listed is shown, nothing is compared
        rep.add("lists", None, "no --before: the old project's download lists were not compared")
    else:
        check_lists(rep, root, a.before, ref, want)
    if "tree" not in a.skip:
        check_tree(rep, root, ref, Path(a.import_dir).resolve(), added_groups, removed_groups)
    else:
        rep.add("tree", None, "skipped (--skip tree)")
    out = Path(a.out) if a.out else root / "build" / "migration"
    if not a.shots:
        rep.add("shots", None, "no --shots OLD_SITE NEW_SITE: the views were not compared")
    else:
        try:
            shots(rep, Path(a.shots[0]), Path(a.shots[1]), out / "shots")
        except Exception as e:                        # noqa: BLE001  the report is still written, with this as its FAIL
            rep.add("shots", False, f"screenshots failed: {type(e).__name__}: {str(e)[:200]}")
    write(rep, out)
    sys.exit(0 if rep.ok else 1)


if __name__ == "__main__":
    main()
