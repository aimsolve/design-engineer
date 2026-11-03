#!/usr/bin/env python3
"""Tests for scripts/import_merge.py: several client STEPs cut separately by step_import.py become ONE import dir under one
root, so step_to_project.py converts them as one machine and the viewer shows them side by side.

    python import_merge.py OUT --root "Root name" DIR1 DIR2 [DIR3 ...] [--gap MM (1000)] [--floor-same MM (500)]

Run (numpy + pytest only, no OCP / build123d: the inputs are synthetic JSON + dummy part files):
  <venv>/bin/python -m pytest -q agent/skills/step-import/scripts/tests/test_import_merge.py

Synthetic inputs (STEP frame: Y up, X-Z the floor; every input's root and a few products share XCAF ref strings on purpose):

  Line A  ref 0:1:1:1   bbox [[0,0,0],[3000,900,2000]]
    Table:1  product "Table"  ref 0:1:1:2  at (0,0,0)          Top:1 -> top (0,800,0), Leg:1 -> leg (100,0,100) turned 90 Y
    Table:2  product "Table"  ref 0:1:1:2  at (2000,0,0) +30 Y (the same product placed twice)
    Plate:1  -> plate         at (500,0,1500) turned 45 Z
    parts: top (Top 01.01.001, 12 tri, 1000 mm3), leg (Leg 01.01.002, 24 tri, 500), plate (Plate 01.02.001, 12 tri, 2000)

  Line B  ref 0:1:1:1   bbox [[0,50,0],[2500,900,1000]]      Y min differs from A by 50 < 500 -> shift (0, 0, 3000)
    Table:1       product "Table"       ref 0:1:1:5  identical to A's Table, but its Top is B's id "top_2"  -> shares A's ref
    Table wide:1  product "Table wide"  ref 0:1:1:2  (A's Table ref string!) Top:1 -> B's "top" (a different Top) -> own ref
    Plate:1       -> plate
    parts: top_2 (= A's top, volume 1000.0005), top (Top 01.01.003, 12 tri, 1500) -> top_v2, leg (= A's leg),
           plate (Plate 01.02.001, 12 tri, 2600: same id as A's, other volume) -> plate_v2

  Line C  ref 0:1:1:1   root matrix at (5,0,0)   bbox [[-200,1200,-500],[800,2000,500]]
                        Y min differs from A by 1200 >= 500 -> shift (0, -1200, 5500)
    Table:1       product "Table"       ref 0:1:1:3  like A's Table but Leg at (100,0,100.5) -> NOT identical, own ref
    Table wide:1  product "Table wide"  ref 0:1:1:4  identical to B's Table wide (its Top is C's id "top_2") -> B's ref
    Plate:1       -> plate
    parts: top (= A's top), top_2 (= B's top -> top_v2), leg (= A's leg), plate (Plate 01.02.001, 12 tri, 3000) -> plate_v3

Contract under test (the ticket's "done when", not the code):
  1. OUT has parts.json, instances.json, parts/<id>.step|.stl for every part; OUT root name = product = --root, one child per
     input in input order, each child that input's root node.
  2. Placement: input 1 stays; next input along +Z: min Z = previous shifted max Z + gap; Y min onto input 1's Y min unless they
     differ by < floor-same; X never. Leaf world placements = input world + shift (1e-9 mm), rotation unchanged. assembly
     bbox_mm = union of shifted boxes; tree = {"name": root, "drawing": null, "children": [input trees]}; instances and
     triangles summed.
  3. Parts: same name, drawing, triangles, volume (rel <= 1e-3) = same part (one row, instances summed); another part whose id
     is taken -> <id>_v2, _v3 ..., files renamed. Every leaf's part is a row; each row's instances = leaves pointing to it.
  4. Sub-assemblies: identical products share a ref; non-identical never do. step_to_project.load_tree on OUT sees them.
  5. Errors: < 2 inputs, or an input without instances.json -> non-zero exit with a message naming the problem.
"""
import copy
import itertools
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
MERGE = SCRIPTS / "import_merge.py"
TOL = 1e-9


# ---------------------------------------------------------------- matrices
def T(x=0.0, y=0.0, z=0.0, rot=None):
    """16 floats row-major: rotation `rot` (3x3) then translation (x, y, z) in mm."""
    m = np.eye(4)
    if rot is not None:
        m[:3, :3] = rot
    m[:3, 3] = (x, y, z)
    return [float(v) for v in m.reshape(-1)]


def rot_y(deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def rot_z(deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def M(m16):
    return np.array(m16, dtype=float).reshape(4, 4)


# ---------------------------------------------------------------- synthetic step_import output
def leaf(name, product, part, matrix, ref):
    return {"name": name, "product": product, "drawing": None, "ref": ref, "matrix": matrix, "part": part}


def node(name, product, ref, matrix, children, drawing=None):
    return {"name": name, "product": product, "drawing": drawing, "ref": ref, "matrix": matrix, "children": children}


def leaves(n, path=()):
    """(path of child indices, leaf node) for every leaf under n."""
    if "children" not in n:
        yield path, n
        return
    for i, c in enumerate(n["children"]):
        yield from leaves(c, path + (i,))


def all_nodes(n):
    yield n
    for c in n.get("children", []):
        yield from all_nodes(c)


def tree_of(n):
    """The parts.json assembly tree (names + drawings of the products, no leaves) as step_import writes it."""
    return {"name": n["name"], "drawing": n.get("drawing"),
            "children": [tree_of(c) for c in n.get("children", []) if "children" in c]}


def make_input(d, root, parts, bbox, triangles):
    """Write a step_import-like dir: parts.json, instances.json, parts/<id>.step|.stl (dummy, unique content).
    parts: {id: (name, drawing, triangles, volume_mm3)}; instances per part are counted from the leaves."""
    d = Path(d)
    (d / "parts").mkdir(parents=True)
    count = {}
    for _, lf in leaves(root):
        count[lf["part"]] = count.get(lf["part"], 0) + 1
    rows = []
    for pid, (name, drawing, tri, vol) in parts.items():
        (d / "parts" / f"{pid}.step").write_text(f"ISO-10303-21; {d.name}/{pid} {name}\n", encoding="utf-8")
        (d / "parts" / f"{pid}.stl").write_text(f"solid {d.name}/{pid}\nendsolid\n", encoding="utf-8")
        rows.append({"id": pid, "name": name, "drawing": drawing, "kind": "made", "material": "steel", "module": None,
                     "color": None, "instances": count.get(pid, 0), "bbox_mm": [[0, 0, 0], [10, 10, 10]],
                     "volume_mm3": vol, "triangles": tri, "files": {"step": f"parts/{pid}.step", "stl": f"parts/{pid}.stl"},
                     "type": None})
    asm = {"root": root["name"], "nodes": sum(1 for _ in all_nodes(root)), "instances": sum(count.values()),
           "distinct_parts": len(rows), "triangles": triangles, "bbox_mm": bbox, "source": f"{d.name}.step",
           "tessellation": {"linear": 0.5}, "tree": tree_of(root)}
    (d / "parts.json").write_text(json.dumps({"assembly": asm, "parts": rows}, indent=1), encoding="utf-8")
    (d / "instances.json").write_text(json.dumps(root, indent=1), encoding="utf-8")
    return d


def table(name, product, ref, matrix, top_id, leg_z=100.0):
    return node(name, product, ref, matrix, [
        leaf("Top:1", "Top", top_id, T(0, 800, 0), "0:1:1:90"),
        leaf("Leg:1", "Leg", "leg", T(100, 0, leg_z, rot_y(90)), "0:1:1:91")])


def line_a(d):
    root = node("Line A", "Line A", "0:1:1:1", T(), [
        table("Table:1", "Table", "0:1:1:2", T(0, 0, 0), "top"),
        table("Table:2", "Table", "0:1:1:2", T(2000, 0, 0, rot_y(30)), "top"),
        leaf("Plate:1", "Plate", "plate", T(500, 0, 1500, rot_z(45)), "0:1:1:3")], drawing="10.00.000")
    parts = {"top": ("Top", "01.01.001", 12, 1000.0), "leg": ("Leg", "01.01.002", 24, 500.0),
             "plate": ("Plate", "01.02.001", 12, 2000.0)}
    return make_input(d, root, parts, [[0, 0, 0], [3000, 900, 2000]], 1000)


def line_b(d):
    root = node("Line B", "Line B", "0:1:1:1", T(), [
        table("Table:1", "Table", "0:1:1:5", T(0, 50, 0), "top_2"),
        table("Table wide:1", "Table wide", "0:1:1:2", T(1500, 50, 0), "top"),
        leaf("Plate:1", "Plate", "plate", T(0, 50, 500), "0:1:1:3")])
    parts = {"top_2": ("Top", "01.01.001", 12, 1000.0005), "top": ("Top", "01.01.003", 12, 1500.0),
             "leg": ("Leg", "01.01.002", 24, 500.0), "plate": ("Plate", "01.02.001", 12, 2600.0)}
    return make_input(d, root, parts, [[0, 50, 0], [2500, 900, 1000]], 2000)


def line_c(d):
    root = node("Line C", "Line C", "0:1:1:1", T(5, 0, 0), [
        table("Table:1", "Table", "0:1:1:3", T(0, 1200, 0), "top", leg_z=100.5),
        table("Table wide:1", "Table wide", "0:1:1:4", T(300, 1200, -400, rot_y(-15)), "top_2"),
        leaf("Plate:1", "Plate", "plate", T(-100, 1200, 0), "0:1:1:2")])
    parts = {"top": ("Top", "01.01.001", 12, 1000.0), "top_2": ("Top", "01.01.003", 12, 1500.0),
             "leg": ("Leg", "01.01.002", 24, 500.0), "plate": ("Plate", "01.02.001", 12, 3000.0)}
    return make_input(d, root, parts, [[-200, 1200, -500], [800, 2000, 500]], 3000)


# ---------------------------------------------------------------- running the CLI
def run_merge(out, root, dirs, *extra):
    assert MERGE.exists(), f"{MERGE} does not exist yet"
    return subprocess.run([sys.executable, str(MERGE), str(out), "--root", root, *map(str, dirs), *map(str, extra)],
                          capture_output=True, text=True, timeout=120)


def load(out):
    return (json.loads((out / "parts.json").read_text(encoding="utf-8")),
            json.loads((out / "instances.json").read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def merged(tmp_path_factory):
    base = tmp_path_factory.mktemp("merge")
    dirs = [line_a(base / "A"), line_b(base / "B"), line_c(base / "C")]
    out = base / "OUT"
    r = run_merge(out, "Production lines", dirs)
    assert r.returncode == 0, f"import_merge failed:\n{r.stdout}\n{r.stderr}"
    pj, inst = load(out)
    return {"dirs": dirs, "out": out, "parts": pj, "inst": inst}


def world_of(root, path):
    m = M(root["matrix"])
    n = root
    for i in path:
        n = n["children"][i]
        m = m @ M(n["matrix"])
    return m


# ---------------------------------------------------------------- 1. the merged dir
def test_out_has_files_for_every_part(merged):          # done when 1
    out, pj = merged["out"], merged["parts"]
    assert (out / "parts.json").is_file() and (out / "instances.json").is_file()
    assert pj["parts"], "OUT/parts.json lists no parts"
    for p in pj["parts"]:
        assert (out / "parts" / f"{p['id']}.step").is_file(), p["id"]
        assert (out / "parts" / f"{p['id']}.stl").is_file(), p["id"]
        assert p["files"] == {"step": f"parts/{p['id']}.step", "stl": f"parts/{p['id']}.stl"}, p


def test_root_has_one_child_per_input_in_order(merged):  # done when 1
    inst = merged["inst"]
    assert inst["name"] == "Production lines" and inst["product"] == "Production lines"
    assert len(inst["matrix"]) == 16
    srcs = [json.loads((d / "instances.json").read_text(encoding="utf-8")) for d in merged["dirs"]]
    assert [c["name"] for c in inst["children"]] == ["Line A", "Line B", "Line C"]

    def shape(n):   # the input's node structure: names, products, drawings, leaf/node, in order
        return (n["name"], n.get("product"), n.get("drawing"), [shape(c) for c in n.get("children", [])] if "children" in n else None)
    for got, src in zip(inst["children"], srcs):
        assert shape(got) == shape(src)


# ---------------------------------------------------------------- 2. placement
SHIFTS = [np.array([0.0, 0.0, 0.0]), np.array([0.0, 0.0, 3000.0]), np.array([0.0, -1200.0, 5500.0])]


def check_world(inst, dirs, shifts):
    for k, (d, shift) in enumerate(zip(dirs, shifts)):
        src = json.loads((d / "instances.json").read_text(encoding="utf-8"))
        n_leaves = 0
        for path, _ in leaves(src):
            want = world_of(src, path)
            got = world_of(inst, (k,) + path)
            assert np.allclose(got[:3, :3], want[:3, :3], rtol=0, atol=TOL), (d.name, path, "rotation changed")
            assert np.allclose(got[:3, 3], want[:3, 3] + shift, rtol=0, atol=TOL), \
                (d.name, path, got[:3, 3].tolist(), (want[:3, 3] + shift).tolist())
            assert np.allclose(got[3], [0, 0, 0, 1], rtol=0, atol=TOL)
            n_leaves += 1
        assert n_leaves == 5


def test_leaf_world_placement_is_input_plus_shift(merged):   # done when 2
    # A stays; B: min Z 0 -> 2000 + 1000, Y 50 vs 0 (< 500) kept; C: min Z -500 -> 4000 + 1000, Y 1200 vs 0 (>= 500) -> 0
    check_world(merged["inst"], merged["dirs"], SHIFTS)


def test_gap_and_floor_same_options(tmp_path):              # done when 2
    a, b = line_a(tmp_path / "A"), line_b(tmp_path / "B")
    out = tmp_path / "OUT"
    r = run_merge(out, "Two", [a, b], "--gap", "250", "--floor-same", "10")
    assert r.returncode == 0, r.stderr
    pj, inst = load(out)
    # B: Y 50 vs 0 now differs by >= 10 -> put on 0; Z min 0 -> 2000 + 250
    check_world(inst, [a, b], [np.zeros(3), np.array([0.0, -50.0, 2250.0])])
    assert np.allclose(pj["assembly"]["bbox_mm"], [[0, 0, 0], [3000, 900, 3250]], rtol=0, atol=TOL)


def test_assembly_summary_is_merged(merged):                # done when 2
    asm = merged["parts"]["assembly"]
    # shifted boxes: A [[0,0,0],[3000,900,2000]], B [[0,50,3000],[2500,900,4000]], C [[-200,0,5000],[800,800,6000]]
    assert np.allclose(asm["bbox_mm"], [[-200, 0, 0], [3000, 900, 6000]], rtol=0, atol=TOL)
    srcs = [json.loads((d / "parts.json").read_text(encoding="utf-8"))["assembly"] for d in merged["dirs"]]
    assert asm["tree"] == {"name": "Production lines", "drawing": None, "children": [s["tree"] for s in srcs]}
    assert asm["instances"] == 15
    assert asm["triangles"] == 6000


# ---------------------------------------------------------------- 3. parts
def test_parts_deduplicated_and_renamed(merged):            # done when 3
    rows = {p["id"]: p for p in merged["parts"]["parts"]}
    assert set(rows) == {"top", "leg", "plate", "top_v2", "plate_v2", "plate_v3"}
    # A's top + B's top_2 (volume 5e-7 off) + C's top: one row
    assert rows["top"]["instances"] == 4
    assert rows["leg"]["instances"] == 6
    # B's "top" is another Top (drawing 01.01.003) whose id was taken; C's "top_2" is that same one
    assert (rows["top_v2"]["drawing"], rows["top_v2"]["volume_mm3"], rows["top_v2"]["instances"]) == ("01.01.003", 1500.0, 2)
    assert (rows["plate_v2"]["volume_mm3"], rows["plate_v2"]["instances"]) == (2600.0, 1)
    assert (rows["plate_v3"]["volume_mm3"], rows["plate_v3"]["instances"]) == (3000.0, 1)
    assert (rows["plate"]["volume_mm3"], rows["plate"]["instances"]) == (2000.0, 1)


def test_renamed_part_files_carry_the_right_geometry(merged):   # done when 3
    out = merged["out"]
    a, b, c = merged["dirs"]
    src = {"top": a / "parts/top", "leg": a / "parts/leg", "plate": a / "parts/plate",
           "top_v2": b / "parts/top", "plate_v2": b / "parts/plate", "plate_v3": c / "parts/plate"}
    for pid, s in src.items():
        for ext in ("step", "stl"):
            assert (out / "parts" / f"{pid}.{ext}").read_text(encoding="utf-8") == Path(f"{s}.{ext}").read_text(encoding="utf-8"), \
                (pid, ext)


def test_leaves_point_to_rows_and_counts_match(merged):     # done when 3
    rows = {p["id"]: p for p in merged["parts"]["parts"]}
    count = {}
    for _, lf in leaves(merged["inst"]):
        assert lf["part"] in rows, lf
        count[lf["part"]] = count.get(lf["part"], 0) + 1
    assert {pid: p["instances"] for pid, p in rows.items()} == count
    # B's Table:1 Top (B id top_2) -> top; B's Table wide Top (B id top) -> top_v2; C's Table wide Top (C id top_2) -> top_v2
    b, c = merged["inst"]["children"][1], merged["inst"]["children"][2]
    assert b["children"][0]["children"][0]["part"] == "top"
    assert b["children"][1]["children"][0]["part"] == "top_v2"
    assert c["children"][1]["children"][0]["part"] == "top_v2"
    assert c["children"][2]["part"] == "plate_v3"


def test_part_identity_needs_name_drawing_triangles_and_volume(tmp_path):   # done when 3
    def one(d, name, parts):
        root = node(name, name, "0:1:1:1", T(), [leaf(f"{pid}:1", parts[pid][0], pid, T(i * 10, 0, 0), f"0:1:1:{i + 2}")
                                               for i, pid in enumerate(parts)])
        return make_input(d, root, parts, [[0, 0, 0], [100, 10, 10]], 10)
    a = one(tmp_path / "A", "A", {"bolt": ("Bolt", "02.00.001", 40, 100.0)})
    b = one(tmp_path / "B", "B", {
        "bolt": ("Bolt", "02.00.001", 40, 100.05),      # rel 5e-4 -> same part
        "bolt_2": ("Bolt", "02.00.001", 44, 100.0),     # other triangle count -> different
        "bolt_3": ("Bolt", "02.00.002", 40, 100.0),     # other drawing -> different
        "bolt_4": ("Bolt", "02.00.001", 40, 100.2),     # rel 2e-3 -> different
        "bolt_5": ("Screw", "02.00.001", 40, 100.0)})   # other name -> different
    out = tmp_path / "OUT"
    r = run_merge(out, "AB", [a, b])
    assert r.returncode == 0, r.stderr
    pj, inst = load(out)
    rows = {p["id"]: p for p in pj["parts"]}
    assert rows["bolt"]["instances"] == 2
    assert len(rows) == 5
    # the B ids not taken in OUT stay as they are; only the colliding one is renamed
    assert set(rows) == {"bolt", "bolt_2", "bolt_3", "bolt_4", "bolt_5"}
    b_leaves = [lf["part"] for _, lf in leaves(inst["children"][1])]
    assert b_leaves == ["bolt", "bolt_2", "bolt_3", "bolt_4", "bolt_5"]


def test_second_collision_gets_v3(tmp_path):                # done when 3
    def one(d, name, vol):
        root = node(name, name, "0:1:1:1", T(), [leaf("Nut:1", "Nut", "nut", T(), "0:1:1:2")])
        return make_input(d, root, {"nut": ("Nut", None, 20, vol)}, [[0, 0, 0], [10, 10, 10]], 20)
    dirs = [one(tmp_path / "A", "A", 10.0), one(tmp_path / "B", "B", 20.0), one(tmp_path / "C", "C", 30.0),
            one(tmp_path / "D", "D", 20.0)]
    out = tmp_path / "OUT"
    r = run_merge(out, "ABCD", dirs)
    assert r.returncode == 0, r.stderr
    pj, inst = load(out)
    rows = {p["id"]: p for p in pj["parts"]}
    assert {pid: (p["volume_mm3"], p["instances"]) for pid, p in rows.items()} == \
        {"nut": (10.0, 1), "nut_v2": (20.0, 2), "nut_v3": (30.0, 1)}
    assert [c["children"][0]["part"] for c in inst["children"]] == ["nut", "nut_v2", "nut_v3", "nut_v2"]


# ---------------------------------------------------------------- 4. sub-assemblies
def identical(n1, n2):
    """Same product: name, children in order with the same names, same part ids / identical sub-products, matrices 1e-9."""
    if (n1.get("product") or n1["name"]) != (n2.get("product") or n2["name"]):
        return False
    c1, c2 = n1.get("children", []), n2.get("children", [])
    if len(c1) != len(c2):
        return False
    for a, b in zip(c1, c2):
        if a["name"] != b["name"] or ("children" in a) != ("children" in b):
            return False
        if not np.allclose(M(a["matrix"]), M(b["matrix"]), rtol=0, atol=TOL):
            return False
        if "children" in a:
            if not identical(a, b):
                return False
        elif a["part"] != b["part"]:
            return False
    return True


def test_identical_products_share_a_ref_others_never(merged):  # done when 4
    inst = merged["inst"]
    a, b, c = inst["children"]
    assert b["children"][0]["ref"] == a["children"][0]["ref"] == a["children"][1]["ref"], "B's Table is A's Table"
    assert c["children"][1]["ref"] == b["children"][1]["ref"], "C's Table wide is B's Table wide"
    assert c["children"][0]["ref"] != a["children"][0]["ref"], "C's Table has its Leg 0.5 mm higher"
    assert b["children"][1]["ref"] != a["children"][0]["ref"], "B's Table wide reused A's ref string"
    assert len({inst["ref"] if "ref" in inst else inst["name"], a["ref"], b["ref"], c["ref"]}) == 4, "roots share a ref"
    products = [n for n in all_nodes(inst) if "children" in n]
    for n1, n2 in itertools.combinations(products, 2):
        r1, r2 = n1.get("ref") or n1["name"], n2.get("ref") or n2["name"]
        assert (r1 == r2) == identical(n1, n2), (n1["name"], r1, n2["name"], r2)


def test_step_to_project_load_tree_sees_one_product_per_ref(merged):   # done when 4
    sys.path.insert(0, str(SCRIPTS))
    from step_to_project import load_tree
    products, order, root_ref, root_m = load_tree(merged["out"])
    assert len(products) == len(order) == 7    # root, Line A/B/C, Table, Table wide, C's Table
    root = products[root_ref]
    assert root.name == "Production lines"
    assert [(k, products[cid].name) for k, cid, _, _ in root.children] == \
        [("node", "Line A"), ("node", "Line B"), ("node", "Line C")]
    a_ref, b_ref, c_ref = (cid for _, cid, _, _ in root.children)

    def kids(ref):
        return [(k, cid if k == "part" else products[cid].name, name) for k, cid, _, name in products[ref].children]
    assert kids(a_ref) == [("node", "Table", "Table:1"), ("node", "Table", "Table:2"), ("part", "plate", "Plate:1")]
    assert kids(b_ref) == [("node", "Table", "Table:1"), ("node", "Table wide", "Table wide:1"), ("part", "plate_v2", "Plate:1")]
    assert kids(c_ref) == [("node", "Table", "Table:1"), ("node", "Table wide", "Table wide:1"), ("part", "plate_v3", "Plate:1")]
    a_table = products[a_ref].children[0][1]
    assert products[b_ref].children[0][1] == a_table
    assert kids(a_table) == [("part", "top", "Top:1"), ("part", "leg", "Leg:1")]
    wide = products[b_ref].children[1][1]
    assert products[c_ref].children[1][1] == wide
    assert kids(wide) == [("part", "top_v2", "Top:1"), ("part", "leg", "Leg:1")]
    c_table = products[c_ref].children[0][1]
    assert c_table != a_table
    assert products[c_table].children[1][2][2, 3] == pytest.approx(100.5, abs=TOL)
    assert sorted(products[a_table].parents) == sorted([a_ref, b_ref])


# ---------------------------------------------------------------- 5. errors
def test_one_input_is_refused(tmp_path):                    # done when 5
    a = line_a(tmp_path / "A")
    r = run_merge(tmp_path / "OUT", "One", [a])
    out = r.stdout + r.stderr
    assert r.returncode != 0
    assert "Traceback" not in out, out
    assert "two" in out.lower() or "2" in out, out          # says how many inputs it needs
    assert not (tmp_path / "OUT" / "instances.json").exists()


def test_input_without_instances_json_is_refused(tmp_path):    # done when 5
    a = line_a(tmp_path / "A")
    b = line_b(tmp_path / "B_no_tree")
    (b / "instances.json").unlink()
    r = run_merge(tmp_path / "OUT", "Two", [a, b])
    out = r.stdout + r.stderr
    assert r.returncode != 0
    assert "Traceback" not in out, out
    assert "instances.json" in out and "B_no_tree" in out, out


# ---------------------------------------------------------------- 6. re-run, box identity, one input's own parts, --up z
def patch_parts_json(d, fn):
    """Rewrite d/parts.json through fn(dict) (in place)."""
    p = Path(d) / "parts.json"
    pj = json.loads(p.read_text(encoding="utf-8"))
    fn(pj)
    p.write_text(json.dumps(pj, indent=1), encoding="utf-8")


def set_part_boxes(d, boxes):
    """Give the parts of input d their own bbox_mm: {id: [[x,y,z],[x,y,z]]}."""
    def fn(pj):
        for row in pj["parts"]:
            if row["id"] in boxes:
                row["bbox_mm"] = boxes[row["id"]]
    patch_parts_json(d, fn)


def out_files(out):
    return {f.name: f.read_text(encoding="utf-8") for f in (out / "parts").iterdir()}


def test_rerun_into_existing_out_leaves_only_the_new_merge(tmp_path):   # spec 1: re-run after a new export
    a, b = line_a(tmp_path / "A"), line_b(tmp_path / "B")
    out = tmp_path / "OUT"
    r = run_merge(out, "Two", [a, b])
    assert r.returncode == 0, r.stderr
    assert {"plate_v2.step", "top_v2.step"} <= set(out_files(out)), "first merge: B's plate and top are their own parts"

    # a new export of B, written as fresh files (no inode shared with the old ones): its plate is now A's plate
    # (volume 2000), its other Top got a new volume and new file content
    import shutil
    shutil.rmtree(b)
    b = line_b(tmp_path / "B")

    def fn(pj):
        for row in pj["parts"]:
            if row["id"] == "plate":
                row["volume_mm3"] = 2000.0
            if row["id"] == "top":
                row["volume_mm3"] = 1600.0
    patch_parts_json(b, fn)
    for ext in ("step", "stl"):
        f = b / "parts" / f"top.{ext}"
        f.unlink()
        f.write_text(f"rev2 B/top {ext}\n", encoding="utf-8")
        f = b / "parts" / f"plate.{ext}"
        f.unlink()
        f.write_text(f"rev2 B/plate {ext}\n", encoding="utf-8")

    r = run_merge(out, "Two", [a, b])
    assert r.returncode == 0, r.stderr
    pj, inst = load(out)
    rows = {p["id"]: p for p in pj["parts"]}
    assert set(rows) == {"top", "leg", "plate", "top_v2"}, sorted(rows)
    assert (rows["plate"]["instances"], rows["top_v2"]["volume_mm3"]) == (2, 1600.0)
    assert inst["children"][1]["children"][2]["part"] == "plate"

    got = out_files(out)
    assert set(got) == {f"{pid}.{ext}" for pid in rows for ext in ("step", "stl")}, \
        f"stale files left in OUT/parts: {sorted(set(got) - {f'{pid}.{ext}' for pid in rows for ext in ('step', 'stl')})}"
    want = {"top": a / "parts/top", "leg": a / "parts/leg", "plate": a / "parts/plate", "top_v2": b / "parts/top"}
    for pid, s in want.items():
        for ext in ("step", "stl"):
            assert got[f"{pid}.{ext}"] == Path(f"{s}.{ext}").read_text(encoding="utf-8"), (pid, ext)
    assert got["top_v2.step"].startswith("rev2")

    # the same as a merge into a fresh dir
    fresh = tmp_path / "FRESH"
    r = run_merge(fresh, "Two", [a, b])
    assert r.returncode == 0, r.stderr
    assert out_files(fresh) == got
    assert load(fresh) == (pj, inst)


def one_input(d, name, parts, boxes=None):
    """An input whose root holds one leaf per part (in the given order); boxes: {id: bbox_mm} per part."""
    root = node(name, name, "0:1:1:1", T(), [leaf(f"{pid}:1", parts[pid][0], pid, T(i * 10, 0, 0), f"0:1:1:{i + 2}")
                                           for i, pid in enumerate(parts)])
    d = make_input(d, root, parts, [[0, 0, 0], [100, 10, 10]], 10)
    if boxes:
        set_part_boxes(d, boxes)
    return d


def test_part_identity_needs_the_same_box(tmp_path):        # spec 2: a mirror copy is another part
    box = [[0, 0, 0], [100, 50, 10]]
    mirror = [[-100, 0, 0], [0, 50, 10]]
    same = ("Bracket", "03.00.001", 36, 250.0)
    a = one_input(tmp_path / "A", "A", {"bracket": same}, {"bracket": box})
    b = one_input(tmp_path / "B", "B", {"bracket": same, "bracket_2": same, "bracket_3": same}, {
        "bracket": mirror,                                          # taken id, other box -> bracket_v2
        "bracket_2": [[0, 0, 0], [100.0005, 50, 10.0005]],          # box within 1e-3 -> A's bracket
        "bracket_3": [[0, 30, 0], [100, 80, 10]]})                  # free id, other box -> stays bracket_3
    out = tmp_path / "OUT"
    r = run_merge(out, "AB", [a, b])
    assert r.returncode == 0, r.stderr
    pj, inst = load(out)
    rows = {p["id"]: p for p in pj["parts"]}
    assert set(rows) == {"bracket", "bracket_v2", "bracket_3"}, sorted(rows)
    assert rows["bracket"]["instances"] == 2
    assert np.allclose(rows["bracket_v2"]["bbox_mm"], mirror, rtol=0, atol=TOL)
    assert (rows["bracket_v2"]["instances"], rows["bracket_3"]["instances"]) == (1, 1)
    assert [lf["part"] for _, lf in leaves(inst["children"][1])] == ["bracket_v2", "bracket", "bracket_3"]
    assert (out / "parts/bracket_v2.step").read_text(encoding="utf-8") == (b / "parts/bracket.step").read_text(encoding="utf-8")


def test_lookalike_parts_of_one_input_stay_two(tmp_path):   # spec 3: only earlier inputs are matched
    same = ("Nut", "04.00.001", 20, 10.0)
    a = one_input(tmp_path / "A", "A", {"nut": same, "nut_2": same})
    b = one_input(tmp_path / "B", "B", {"washer": ("Washer", "04.00.002", 16, 3.0)})
    out = tmp_path / "OUT"
    r = run_merge(out, "AB", [a, b])
    assert r.returncode == 0, r.stderr
    pj, inst = load(out)
    rows = {p["id"]: p for p in pj["parts"]}
    assert set(rows) == {"nut", "nut_2", "washer"}, sorted(rows)
    assert (rows["nut"]["instances"], rows["nut_2"]["instances"]) == (1, 1)
    assert [lf["part"] for _, lf in leaves(inst["children"][0])] == ["nut", "nut_2"]
    for pid in ("nut", "nut_2"):
        assert (out / f"parts/{pid}.step").read_text(encoding="utf-8") == (a / f"parts/{pid}.step").read_text(encoding="utf-8")


def test_lookalike_parts_of_one_later_input_stay_two(tmp_path):   # spec 3: the same inside a later input
    a = one_input(tmp_path / "A", "A", {"washer": ("Washer", "04.00.002", 16, 3.0)})
    same = ("Nut", "04.00.001", 20, 10.0)
    b = one_input(tmp_path / "B", "B", {"nut": same, "nut_2": same})
    out = tmp_path / "OUT"
    r = run_merge(out, "AB", [a, b])
    assert r.returncode == 0, r.stderr
    pj, inst = load(out)
    rows = {p["id"]: p for p in pj["parts"]}
    assert set(rows) == {"washer", "nut", "nut_2"}, sorted(rows)
    assert [lf["part"] for _, lf in leaves(inst["children"][1])] == ["nut", "nut_2"]


Z_UP_BOXES = [[[0, 0, 0], [3000, 2000, 900]],           # A
              [[0, 0, 50], [2500, 1000, 900]],          # B: Z min 50 vs 0 (< 500) kept
              [[-200, -500, 1200], [800, 500, 2000]]]   # C: Z min 1200 vs 0 (>= 500) -> 0


def z_up_inputs(base):
    dirs = [line_a(base / "A"), line_b(base / "B"), line_c(base / "C")]
    for d, box in zip(dirs, Z_UP_BOXES):
        patch_parts_json(d, lambda pj, box=box: pj["assembly"].__setitem__("bbox_mm", box))
    return dirs


def test_up_z_lines_inputs_along_y_and_floors_z(tmp_path):  # spec 4: --up z
    dirs = z_up_inputs(tmp_path)
    out = tmp_path / "OUT"
    r = run_merge(out, "Z up", dirs, "--up", "z")
    assert r.returncode == 0, r.stderr
    pj, inst = load(out)
    # B: Y min 0 -> 2000 + 1000; C: Y min -500 -> 4000 + 1000, Z 1200 -> 0; X never moves
    check_world(inst, dirs, [np.zeros(3), np.array([0.0, 3000.0, 0.0]), np.array([0.0, 5500.0, -1200.0])])
    # shifted boxes: A [[0,0,0],[3000,2000,900]], B [[0,3000,50],[2500,4000,900]], C [[-200,5000,0],[800,6000,800]]
    assert np.allclose(pj["assembly"]["bbox_mm"], [[-200, 0, 0], [3000, 6000, 900]], rtol=0, atol=TOL)


def test_up_z_with_gap_and_floor_same(tmp_path):            # spec 4: --up z honours --gap and --floor-same
    a, b = z_up_inputs(tmp_path)[:2]
    out = tmp_path / "OUT"
    r = run_merge(out, "Z up", [a, b], "--up", "z", "--gap", "250", "--floor-same", "10")
    assert r.returncode == 0, r.stderr
    pj, inst = load(out)
    # B: Z 50 vs 0 now >= 10 -> put on 0; Y min 0 -> 2000 + 250
    check_world(inst, [a, b], [np.zeros(3), np.array([0.0, 2250.0, -50.0])])
    assert np.allclose(pj["assembly"]["bbox_mm"], [[0, 0, 0], [3000, 3250, 900]], rtol=0, atol=TOL)


def test_up_y_is_the_default(tmp_path):                     # spec 4: --up y = the existing behaviour
    dirs = [line_a(tmp_path / "A"), line_b(tmp_path / "B"), line_c(tmp_path / "C")]
    r = run_merge(tmp_path / "OUT", "Production lines", dirs, "--up", "y")
    assert r.returncode == 0, r.stderr
    pj, inst = load(tmp_path / "OUT")
    check_world(inst, dirs, SHIFTS)
    assert np.allclose(pj["assembly"]["bbox_mm"], [[-200, 0, 0], [3000, 900, 6000]], rtol=0, atol=TOL)


# ---------------------------------------------------------------- 7. OUT must not be (inside) an input
def dir_files(d):
    """{relative path: bytes} of every file under d."""
    return {str(f.relative_to(d)): f.read_bytes() for f in Path(d).rglob("*") if f.is_file()}


@pytest.mark.parametrize("where", ["first input", "inside first input", "second input", "inside second input parts"])
def test_out_that_is_or_is_inside_an_input_is_refused(tmp_path, where):   # review: a merge into its own input wipes it
    """`import_merge.py OUT ... DIR1 DIR2` with OUT one of the input dirs (or a dir inside one) exits non-zero with a message
    that names that input, and leaves the input as it was: same parts/ file names and contents, parts.json and
    instances.json unchanged."""
    a, b = line_a(tmp_path / "LineA_in"), line_b(tmp_path / "LineB_in")
    victim = a if "first" in where else b
    out = {"first input": a, "inside first input": a / "merged", "second input": b,
           "inside second input parts": b / "parts" / "merged"}[where]
    before = dir_files(victim)
    r = run_merge(out, "Two", [a, b])
    text = r.stdout + r.stderr
    assert r.returncode != 0, f"OUT = {where}: the merge ran\n{text}"
    assert "Traceback" not in text, text
    assert victim.name in text, f"the message does not name the input OUT is in ({victim.name}):\n{text}"
    after = dir_files(victim)
    assert sorted(after) == sorted(before), \
        f"{victim.name}: files added {sorted(set(after) - set(before))}, removed {sorted(set(before) - set(after))}"
    changed = [f for f in before if after[f] != before[f]]
    assert not changed, f"{victim.name}: files rewritten {changed}"
