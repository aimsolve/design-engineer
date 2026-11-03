"""The print kit: `derive.py print` (one STL laid on the bed + the part STEP per `process: print` part).

Done-when map:
  DW1  `print_rot` may be `[axis, degrees]` (axis "X" | "Y" | "Z", e.g. ["Y", 90]): a rotation of that many degrees about that axis,
       the STL identical to the one from the equivalent [rx, ry, rz]. The [rx, ry, rz] form keeps working.
  DW2  `print_with: "<other part id>"`: the part gets no manifest entry and no file of its own; it is printed fused into the other
       part's STL (one body when they touch), each part at its FIRST placement in the machine. The other part's entry keeps its
       id, name (title else name), node, process and qty (= number of placements).
  DW3  The print STL is made from the part as it stands at its FIRST placement (placement rotation applied before print_rot /
       lay-flat). Identity placement or no placements: exactly as today.
  DW4  `derive.py print MANIFEST <part id>` gives the same STL for that part as the whole scope, a fused part included.

The model (build/model/parts.json + STEPs in each part's OWN coordinates + placements, 4x4 row-major) is hand-written here, like
test_instances.py, so these tests depend only on derive.py. Geometry is read back from the STL with trimesh.
"""
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
import trimesh

import helpers as H


# ---------------------------------------------------------------- placement matrices (4x4 row-major, flattened to 16)
def T(x, y, z):
    return [1, 0, 0, x, 0, 1, 0, y, 0, 0, 1, z, 0, 0, 0, 1]


def RX90(x, y, z):          # rotate +90 deg about X, then translate
    return [1, 0, 0, x, 0, 0, -1, y, 0, 1, 0, z, 0, 0, 0, 1]


def write_model(root, rows):
    """rows: [(pid, shape in its own coordinates, extra fields, [(placement name, loc)] or None)], all process "print"."""
    root = Path(root)
    (root / "mechanics" / "cad" / "parts").mkdir(parents=True, exist_ok=True)
    (root / "build/model/step").mkdir(parents=True, exist_ok=True)
    parts = []
    for pid, shape, extra, pls in rows:
        p = H._write_part(root, pid, "print", shape, dict(extra))
        if pls is not None:
            p["placements"] = [{"name": n, "loc": loc} for n, loc in pls]
            p["qty"] = len(pls)
        parts.append(p)
    H._write_parts_json(root, parts)
    return root


def stl_of(d, root, pid):
    stls = [f for f in H.file_list(H.entry(d, pid), root) if f.suffix == ".stl"]
    assert len(stls) == 1 and stls[0].is_file(), f"{pid}: expected one STL in its entry, got {stls}"
    return stls[0]


def mesh(path):
    m = trimesh.load(str(path), force="mesh")
    assert len(m.faces) > 0, path
    return m


def extents(path):
    """STL bbox size along X, Y, Z (mm)."""
    return mesh(path).bounds[1] - mesh(path).bounds[0]


def verts(path):
    """Sorted unique vertices, rounded: equal for two STLs of the same geometry in the same pose."""
    v = np.round(mesh(path).vertices, 3) + 0.0
    return np.unique(v, axis=0)


def same_geometry(a, b):
    va, vb = verts(a), verts(b)
    return va.shape == vb.shape and np.allclose(va, vb, atol=2e-3)


def on_bed(path):
    lo = mesh(path).bounds[0]
    assert np.allclose(lo, 0, atol=1e-3), f"{path.name}: bbox must start at the origin, min={lo}"


# ================================================================ DW1: print_rot as [axis, degrees]
def _rot_model(root, axis_form=True):
    from build123d import Box
    blk = lambda: Box(10, 20, 30)            # 10 x 20 x 30, axis-aligned in its own coordinates
    rows = [
        ("by_x", blk(), {"print_rot": ["X", 90]}, None),
        ("by_y", blk(), {"print_rot": ["Y", 90]}, None),
        ("by_z", blk(), {"print_rot": ["Z", 90]}, None),
        ("by_z45", blk(), {"print_rot": ["Z", 45]}, None),
    ] if axis_form else []
    rows += [
        ("vec_x", blk(), {"print_rot": [90, 0, 0]}, None),
        ("vec_y", blk(), {"print_rot": [0, 90, 0]}, None),
        ("vec_z", blk(), {"print_rot": [0, 0, 90]}, None),
        ("vec_z45", blk(), {"print_rot": [0, 0, 45]}, None),
    ]
    return write_model(root, rows)


@pytest.fixture(scope="module")
def rot_run(tmp_path_factory):
    root = _rot_model(tmp_path_factory.mktemp("rot") / "proj")
    return root, H.run_derive(root, "print")


@pytest.fixture(scope="module")
def vec_run(tmp_path_factory):
    """The vector form alone, so its regression guard does not depend on the axis form being accepted."""
    root = _rot_model(tmp_path_factory.mktemp("vec") / "proj", axis_form=False)
    return root, H.run_derive(root, "print")


@pytest.mark.parametrize("pid,size", [("by_x", (10, 30, 20)), ("by_y", (30, 20, 10)), ("by_z", (20, 10, 30))])
def test_axis_degrees_print_rot_rotates_about_that_axis(rot_run, pid, size):
    """DW1: ["X"|"Y"|"Z", 90] turns the 10x20x30 block a quarter turn about that axis; the STL bbox shows it."""
    root, d = rot_run
    d.ok()
    stl = stl_of(d, root, pid)
    on_bed(stl)
    assert np.allclose(extents(stl), size, atol=0.05), (pid, extents(stl))


@pytest.mark.parametrize("axis_pid,vec_pid", [("by_x", "vec_x"), ("by_y", "vec_y"), ("by_z", "vec_z"), ("by_z45", "vec_z45")])
def test_axis_degrees_print_rot_equals_the_equivalent_vector(rot_run, axis_pid, vec_pid):
    """DW1: ["Y", 90] gives the same STL geometry as [0, 90, 0] (also for a non-right angle)."""
    root, d = rot_run
    d.ok()
    a, b = stl_of(d, root, axis_pid), stl_of(d, root, vec_pid)
    assert same_geometry(a, b), f"{axis_pid} vs {vec_pid}: {extents(a)} vs {extents(b)}"


@pytest.mark.parametrize("pid,size", [("vec_x", (10, 30, 20)), ("vec_y", (30, 20, 10)), ("vec_z", (20, 10, 30))])
def test_vector_print_rot_keeps_working(vec_run, pid, size):
    """DW1 regression guard: [rx, ry, rz] in degrees as today."""
    root, d = vec_run
    d.ok()
    stl = stl_of(d, root, pid)
    on_bed(stl)
    assert np.allclose(extents(stl), size, atol=0.05), (pid, extents(stl))


# ================================================================ DW3: first placement before print_rot / lay-flat
def _placed_model(root):
    from build123d import Box, Pos, Rot
    blk = lambda: Box(10, 20, 30)
    tilted = lambda: Rot(20, 30, 40) * Box(10, 20, 30)
    rows = [
        # first placement turns the block 90 deg about X (10x20x30 -> 10x30x20); the second is a plain shift and must not count
        ("arm", blk(), {"print_rot": [0, 0, 0]}, [("arm", RX90(5, 5, 5)), ("arm_2", T(100, 0, 0))]),
        # placement first, then print_rot: RX90 -> 10x30x20, then Z90 -> 30x10x20 (the other order would give 20x30x10)
        ("arm_z", blk(), {"print_rot": [0, 0, 90]}, [("arm_z", RX90(0, 0, 0))]),
        # identity / translation-only placements print exactly like no placements
        ("plain", tilted(), {}, None),
        ("plain_id", tilted(), {}, [("plain_id", T(0, 0, 0))]),
        ("plain_shift", tilted(), {}, [("plain_shift", T(40, -30, 7)), ("plain_shift_2", RX90(0, 0, 0))]),
    ]
    return write_model(root, rows)


@pytest.fixture(scope="module")
def placed_run(tmp_path_factory):
    root = _placed_model(tmp_path_factory.mktemp("placed") / "proj")
    return root, H.run_derive(root, "print")


def test_stl_is_the_part_as_it_stands_at_its_first_placement(placed_run):
    """DW3: print_rot [0,0,0] = 'as it stands in the machine'; the first placement's rotation is applied."""
    root, d = placed_run
    d.ok()
    stl = stl_of(d, root, "arm")
    on_bed(stl)
    assert np.allclose(extents(stl), (10, 30, 20), atol=0.05), extents(stl)


def test_placement_rotation_comes_before_print_rot(placed_run):
    """DW3: placement (RX90) first, then print_rot (Z90)."""
    root, d = placed_run
    d.ok()
    stl = stl_of(d, root, "arm_z")
    on_bed(stl)
    assert np.allclose(extents(stl), (30, 10, 20), atol=0.05), extents(stl)


@pytest.mark.parametrize("pid", ["plain_id", "plain_shift"])
def test_identity_or_translation_placement_prints_as_without_placements(placed_run, pid):
    """DW3 regression guard: lay-flat result unchanged when the first placement has no rotation."""
    root, d = placed_run
    d.ok()
    a, b = stl_of(d, root, "plain"), stl_of(d, root, pid)
    on_bed(b)
    assert same_geometry(a, b), (extents(a), extents(b))


def test_qty_is_the_number_of_placements(placed_run):
    root, d = placed_run
    d.ok()
    assert H.entry(d, "arm")["qty"] == 2 and H.entry(d, "plain_shift")["qty"] == 2 and H.entry(d, "arm_z")["qty"] == 1


# ================================================================ DW2: print_with fuses a part into another part's STL
# body: 20x20x10 centred (z -5..5), placed at the origin (first) and far away (second)
# lid:  20x20x4 centred (z -2..2), first placement lifts it onto the body's top (z 5..9: touching); second is far away
LID_ON_TOP = T(0, 0, 7)
LID_GAP = T(0, 0, 20)


def _fused_model(root, lid_first=LID_ON_TOP, with_link=True):
    from build123d import Box
    lid_extra = {"print_with": "body"} if with_link else {}
    rows = [   # lid listed BEFORE body on purpose: the order in parts.json must not matter
        ("lid", Box(20, 20, 4), dict(lid_extra, title="Lid"), [("lid", lid_first), ("lid_2", T(0, 300, 0))]),
        ("body", Box(20, 20, 10), {"title": "Housing"}, [("body", T(0, 0, 0)), ("body_2", T(300, 0, 0))]),
    ]
    return write_model(root, rows)


def _derived_names(root):
    d = Path(root) / "build/derived"
    return [str(p.relative_to(d)) for p in d.rglob("*")] if d.is_dir() else []


def test_print_with_part_has_no_entry_and_no_file_of_its_own(tmp_path):
    """DW2: lid is not in the manifest; nothing in build/derived belongs to it alone."""
    root = _fused_model(tmp_path / "proj")
    d = H.run_derive(root, "print").ok()
    assert [e["id"] for e in d.manifest["parts"]] == ["body"], d.manifest["parts"]
    lid_key = H.by_id(root)["lid"]["key"]
    assert not [n for n in _derived_names(root) if lid_key in n or "lid" in n.lower()], _derived_names(root)
    assert len(list((root / "build/derived").iterdir())) == 1, "only body may have a derived result"


def test_touching_print_with_part_is_fused_into_one_body(tmp_path):
    """DW2: body + lid at their first placements touch -> one watertight body 20x20x14, volume 4000 + 1600."""
    root = _fused_model(tmp_path / "proj")
    d = H.run_derive(root, "print").ok()
    stl = stl_of(d, root, "body")
    on_bed(stl)
    m = mesh(stl)
    assert sorted(np.round(m.bounds[1] - m.bounds[0], 2)) == [14, 20, 20], m.bounds
    assert m.body_count == 1, f"touching parts must be fused into one body, got {m.body_count}"
    assert m.is_watertight
    assert abs(m.volume - 5600) < 1, m.volume


def test_non_touching_print_with_part_still_goes_into_the_same_stl(tmp_path):
    """DW2 edge: lid's first placement leaves a gap -> two bodies in body's STL, each where the first placements put them."""
    root = _fused_model(tmp_path / "proj", lid_first=LID_GAP)
    d = H.run_derive(root, "print").ok()
    assert [e["id"] for e in d.manifest["parts"]] == ["body"]
    m = mesh(stl_of(d, root, "body"))
    assert m.body_count == 2, m.body_count
    assert abs(m.volume - 5600) < 1, m.volume
    assert sorted(np.round(m.bounds[1] - m.bounds[0], 2)) == [20, 20, 27], m.bounds     # z -5 .. 22


def test_print_with_keeps_the_other_parts_entry(tmp_path):
    """DW2: body's id, name (title), node, process and qty are as without print_with; qty = its own placements, not lid's."""
    fused = H.run_derive(_fused_model(tmp_path / "a"), "print").ok()
    plain = H.run_derive(_fused_model(tmp_path / "b", with_link=False), "print").ok()
    keys = ("id", "name", "node", "process", "qty")
    got, want = H.entry(fused, "body"), H.entry(plain, "body")
    assert {k: got.get(k) for k in keys} == {k: want.get(k) for k in keys}
    assert got["name"] == "Housing" and got["qty"] == 2


# ================================================================ DW4: per-part request == whole scope
def _mixed_model(root):
    from build123d import Box, Rot
    rows = [
        ("knob", Rot(20, 30, 40) * Box(10, 20, 30), {}, None),
        ("arm", Box(10, 20, 30), {"print_rot": [0, 90, 0]}, [("arm", RX90(5, 5, 5)), ("arm_2", T(100, 0, 0))]),
        ("lid", Box(20, 20, 4), {"print_with": "body"}, [("lid", LID_ON_TOP)]),
        ("body", Box(20, 20, 10), {"title": "Housing"}, [("body", T(0, 0, 0))]),
    ]
    return write_model(root, rows)


@pytest.fixture(scope="module")
def mixed(tmp_path_factory):
    base = _mixed_model(tmp_path_factory.mktemp("mixed") / "base")
    whole_root = Path(shutil.copytree(base, base.parent / "whole"))
    return base, whole_root, H.run_derive(whole_root, "print")


@pytest.mark.parametrize("pid", ["knob", "arm", "body"])
def test_per_part_request_gives_the_same_stl_as_the_whole_scope(mixed, tmp_path, pid):
    base, whole_root, whole = mixed
    whole.ok()
    root = Path(shutil.copytree(base, tmp_path / "one"))
    one = H.run_derive(root, "print", part_id=pid).ok()
    assert [e["id"] for e in one.manifest["parts"]] == [pid], one.manifest["parts"]
    a, b = stl_of(whole, whole_root, pid), stl_of(one, root, pid)
    assert H.sha_file(a) == H.sha_file(b), f"{pid}: per-part STL differs from the whole scope ({extents(a)} vs {extents(b)})"


def test_per_part_request_for_the_fused_part_includes_its_print_with_partner(mixed, tmp_path):
    """DW4 + DW2: asked for body alone, the STL still contains lid (one body 20x20x14)."""
    base, _, _ = mixed
    root = Path(shutil.copytree(base, tmp_path / "one"))
    one = H.run_derive(root, "print", part_id="body").ok()
    m = mesh(stl_of(one, root, "body"))
    assert m.body_count == 1 and abs(m.volume - 5600) < 1, (m.body_count, m.volume)


def test_print_with_a_partner_that_is_not_a_print_part_fails_loudly(tmp_path):
    """A part fused into another that the scope does not have (a typo, or a part that is not printed) must not vanish from the kit
    unnoticed: derive.py print fails and names it."""
    from build123d import Box
    root = write_model(tmp_path / "p", [("body", Box(20, 20, 10), {}, None), ("lid", Box(20, 20, 4), {"print_with": "bodyy"}, None)])
    d = H.run_derive(root, "print")
    assert d.rc != 0 and "lid" in d.log and "bodyy" in d.log, (d.rc, d.log[-500:])


def test_print_with_chain_or_self_fails_loudly(tmp_path):
    """a -> b -> c, or a part printed with itself, would drop a part from the kit unseen: derive.py print fails instead."""
    from build123d import Box
    chain = write_model(tmp_path / "chain", [("a", Box(5, 5, 5), {"print_with": "b"}, None), ("b", Box(5, 5, 5), {"print_with": "c"}, None),
                                             ("c", Box(5, 5, 5), {}, None)])
    selfref = write_model(tmp_path / "self", [("a", Box(5, 5, 5), {"print_with": "a"}, None)])
    for root in (chain, selfref):
        d = H.run_derive(root, "print")
        assert d.rc != 0 and "printed with" in d.log, d.log[-400:]
