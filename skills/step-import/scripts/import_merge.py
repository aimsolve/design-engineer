#!/usr/bin/env python3
"""Several client STEPs cut one by one (step_import.py --out DIR) -> one import dir under one root, so step_to_project.py
ports them as one machine and the viewer shows them side by side (two variants of a line, each a 500 MB STEP that would not
import together):

    python import_merge.py OUT --root "Root name" DIR1 DIR2 [...] [--gap MM] [--floor-same MM] [--up y|z]

DIR1 stays where it is; each next one is shifted past the previous one by --gap along the floor (+Z when Y is up, the
SolidWorks default; +Y when Z is up), its lowest point put on the floor of DIR1 unless the two differ by less than
--floor-same (the same drawing frame).
A part of a later DIR with the same name, drawing, triangle count, volume and box as one of an earlier DIR is that part
(its id in the later DIR may differ: step_import's `_2` suffixes follow file order; a mirror has another box); a different
part under a taken id gets `<id>_v2`.
A sub-assembly identical to one already merged (name, children, their parts and placements) keeps that one's ref, so the
converter builds it once; any other ref of a later DIR that is taken is made unique.
Writes OUT/parts.json, OUT/instances.json and OUT/parts/ (hard links when possible; OUT/parts/ is written afresh). No model.glb: the converter and
migration_check.py do not read it."""
import argparse
import json
import os
import shutil
import sys
from collections import Counter
from pathlib import Path

import numpy as np

GAP_MM = 1000.0
FLOOR_SAME_MM = 500.0
MATRIX_TOL = 1e-9


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def link(src, dst):
    dst.unlink(missing_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copyfile(src, dst)


def same_part(a, b):
    """The same part in two exports: name, drawing, triangles, volume and box (a mirror copy has the same volume)."""
    box = lambda p: np.array(p.get("bbox_mm") or [[0, 0, 0], [0, 0, 0]], dtype=float)   # noqa: E731
    return (a["name"] == b["name"] and a.get("drawing") == b.get("drawing") and a.get("triangles") == b.get("triangles")
            and abs(a["volume_mm3"] - b["volume_mm3"]) <= 1e-3 * max(1.0, abs(a["volume_mm3"]))
            and np.abs(box(a) - box(b)).max() <= 1e-3)


def signature(n, pid_map, ref_map):
    """What makes two products the same: name, and per child its name, matrix and part id or sub-product ref (mapped)."""
    kids = []
    for c in n.get("children", []):
        what = ("part", pid_map[c["part"]]) if "part" in c else ("node", ref_map[c.get("ref") or c["name"]])
        kids.append((c["name"], what, np.array(c["matrix"], dtype=float)))
    return (n.get("product") or n["name"], n.get("drawing")), kids


def same_signature(a, b):
    return a[0] == b[0] and len(a[1]) == len(b[1]) and all(
        x[0] == y[0] and x[1] == y[1] and np.abs(x[2] - y[2]).max() <= MATRIX_TOL for x, y in zip(a[1], b[1]))


def merge(out, root_name, dirs, gap=GAP_MM, floor_same=FLOOR_SAME_MM, up="y"):
    out, dirs = Path(out), [Path(d) for d in dirs]
    if len(dirs) < 2:
        raise SystemExit("import_merge: give at least two step_import dirs")
    for d in dirs:
        for f in ("parts.json", "instances.json"):
            if not (d / f).is_file():
                raise SystemExit(f"import_merge: {d / f} is missing (not a step_import.py --out dir?)")
    for d in dirs:
        o, i = out.resolve(), d.resolve()
        if o == i or i in o.parents or o in i.parents:
            raise SystemExit(f"import_merge: OUT {out} is (inside) the input {d}: its parts/ would be wiped before read")
    shutil.rmtree(out / "parts", ignore_errors=True)  # a re-run after a new export: no file of the last one stays
    (out / "parts").mkdir(parents=True)
    u, w = (1, 2) if up == "y" else (2, 1)             # the up axis and the one the inputs are lined up along
    parts, order = {}, []              # merged id -> part row
    by_key = {}                        # (name, drawing, triangles) -> merged ids of earlier inputs
    sigs = {}                          # merged ref -> signature
    refs = set()
    trees, children = [], []
    lo_all, hi_all = np.full(3, np.inf), np.full(3, -np.inf)
    zmax = floor = None
    inst = tri = 0
    tess = None
    for n, d in enumerate(dirs):
        P, T = load(d / "parts.json"), load(d / "instances.json")
        asm = P["assembly"]
        if tess and asm.get("tessellation") and asm["tessellation"] != tess:
            print(f"warning: {d} was cut with tessellation {asm['tessellation']}, the first input with {tess}", flush=True)
        tess = tess or asm.get("tessellation")
        lo, hi = np.array(asm["bbox_mm"][0], dtype=float), np.array(asm["bbox_mm"][1], dtype=float)
        dz = 0.0 if zmax is None else zmax + gap - lo[w]
        floor = lo[u] if floor is None else floor
        dy = floor - lo[u]
        if abs(dy) < floor_same:
            dy = 0.0
        shift = np.zeros(3)
        shift[u], shift[w] = dy, dz
        zmax = hi[w] + dz
        lo_all, hi_all = np.minimum(lo_all, lo + shift), np.maximum(hi_all, hi + shift)
        print(f"{d}: bbox {lo.tolist()} .. {hi.tolist()} mm, shift {shift.round(1).tolist()}", flush=True)

        pid_map, mine = {}, []
        for p in P["parts"]:
            key = (p["name"], p.get("drawing"), p.get("triangles"))
            hit = next((q for q in by_key.get(key, []) if same_part(parts[q], p)), None)
            if hit is not None:
                pid_map[p["id"]] = hit
                continue
            pid, k = p["id"], 2
            while pid in parts:
                pid, k = f"{p['id']}_v{k}", k + 1
            q = dict(p, id=pid)
            if q.get("files"):
                q["files"] = {}
                for kind, f in p["files"].items():
                    dst = f"parts/{pid}{Path(f).suffix}"
                    link(d / f, out / dst)
                    q["files"][kind] = dst
            parts[pid] = q
            order.append(pid)
            pid_map[p["id"]] = pid
            mine.append((key, pid))
        for key, pid in mine:                          # matched by the next inputs only: two parts of one file stay two
            by_key.setdefault(key, []).append(pid)

        ref_map = {}

        def visit(node):
            """Bottom up: map this file's ref onto a merged one, then rewrite the node."""
            for c in node.get("children", []):
                if "children" in c:
                    visit(c)
            ref = node.get("ref") or node["name"]
            if ref in ref_map:
                return
            sig = signature(node, pid_map, ref_map)
            hit = next((r for r, s in sigs.items() if same_signature(s, sig)), None)
            if hit is None:
                hit, k = ref, 2
                while hit in refs:
                    hit, k = f"{n + 1}/{ref}" if k == 2 else f"{n + 1}/{ref}#{k}", k + 1
                refs.add(hit)
                sigs[hit] = sig
            ref_map[ref] = hit

        def rewrite(node):
            m = dict(node)
            if "part" in node:
                m["part"] = pid_map[node["part"]]
            else:
                m["ref"] = ref_map[node.get("ref") or node["name"]]
                m["children"] = [rewrite(c) for c in node.get("children", [])]
            return m

        visit(T)
        top = rewrite(T)
        M = np.array(top["matrix"], dtype=float).reshape(4, 4)
        M[:3, 3] += shift
        top["matrix"] = [round(float(x), 9) for x in M.reshape(-1)]
        children.append(top)
        trees.append(asm["tree"])
        inst += asm.get("instances", 0)
        tri += asm.get("triangles", 0)

    rows = [parts[i] for i in order]
    root_ref = "merged"
    while root_ref in refs:
        root_ref += "_"
    T = {"name": root_name, "product": root_name, "drawing": None, "ref": root_ref,
         "matrix": [float(x) for x in np.eye(4).reshape(-1)], "children": children}
    leaves, nodes = [], [0]

    def count(node):
        nodes[0] += 1
        if "part" in node:
            leaves.append(node["part"])
        for c in node.get("children", []):
            count(c)
    count(T)
    per = Counter(leaves)
    for p in rows:
        p["instances"] = per[p["id"]]
    asm = {"root": root_name, "nodes": nodes[0], "instances": inst, "distinct_parts": len(rows), "triangles": tri,
           "bbox_mm": [np.round(lo_all, 3).tolist(), np.round(hi_all, 3).tolist()],
           "source": " + ".join(str(d) for d in dirs), "tessellation": tess,
           "tree": {"name": root_name, "drawing": None, "children": trees}}
    (out / "parts.json").write_text(json.dumps({"assembly": asm, "parts": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "instances.json").write_text(json.dumps(T, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out}: {len(rows)} distinct parts, {len(leaves)} instances, {len(refs)} products", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out")
    ap.add_argument("dirs", nargs="+", help="step_import.py --out dirs, the first one stays in place")
    ap.add_argument("--root", required=True, help="the name of the merged root (the viewer's top node)")
    ap.add_argument("--gap", type=float, default=GAP_MM)
    ap.add_argument("--floor-same", type=float, default=FLOOR_SAME_MM)
    ap.add_argument("--up", choices=("y", "z"), default="y", help="the STEPs' up axis (SolidWorks: y)")
    a = ap.parse_args()
    merge(a.out, a.root, a.dirs, a.gap, a.floor_same, a.up)


if __name__ == "__main__":
    sys.exit(main())
