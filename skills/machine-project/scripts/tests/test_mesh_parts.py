"""Issue #31: mesh-only parts in the model.

A part dict may carry `mesh: {"v": [[x, y, z], ...], "f": [[i, j, k], ...]}` (triangles, mm, machine coordinates) and no `shape`:
a view-only part (e.g. a unit of an electrical cabinet taken from a vendor model). Any process is allowed.

Done-when map:
  DW1  a node (or a part file) returns a mesh part; `--model` stores it: parts.json entry has `mesh` = a path under build/model/ to a
       file holding the triangles, no `step`, the item's other fields, qty from the dict (units, like a row), and ONE identity
       placement (it stands where its triangles are).
  DW2  a second `--model` with nothing changed rebuilds 0 nodes and leaves the stored mesh file unchanged; changed triangles change it.
  DW3  derive.py viewer: model.glb has one mesh node with extras.part = its id and the same triangles (world bbox within 0.01 mm);
       the viewer parts.json lists it with an stl file to download.
  DW4  derive.py step (model.step and per-part), print and laser leave it out; bought makes its row like any bought part.
  DW5  any process is accepted for a mesh part (view-only), and print / laser / step never get a file from it.

The project (written with test_review_defects._proj, folder layout):
    frame/plate.py   a 10 x 10 x 2 mill Box at the origin, placed once by frame/__init__.py (a normal shaped part)
    cab/__init__.py  returns four mesh parts directly (no place()), each an axis-aligned box of 12 triangles far from the plate:
        "Cabinet unit"  bought, code CU-1, kind electrical, desc, qty 2        x 200..240
        "Panel print"   print, PLA                                             x 300..310
        "Panel laser"   laser, S235JR, thickness 2                             x 400..410
        "Panel other"   other                                                  x 500..510
"""
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

import helpers as H
from test_review_defects import _proj
from test_instances import read_glb, accessor, world_matrices, mesh_nodes, node_world_bbox_mm

IDENTITY = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]


def box_mesh(x0, y0, z0, x1, y1, z1):
    """An axis-aligned box as 8 vertices and 12 outward triangles (plain lists, as a node would write them)."""
    v = [[x, y, z] for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]          # index = 4*ix + 2*iy + iz
    f = [[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5],                               # x0, x1
         [0, 4, 5], [0, 5, 1], [2, 3, 7], [2, 7, 6],                               # y0, y1
         [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]]                               # z0, z1
    return {"v": [[float(c) for c in p] for p in v], "f": f}


BOXES = {
    "Cabinet_unit": (200.0, 10.0, 5.0, 240.0, 30.0, 65.0),
    "Panel_print": (300.0, 0.0, 0.0, 310.0, 10.0, 10.0),
    "Panel_laser": (400.0, 0.0, 0.0, 410.0, 10.0, 2.0),
    "Panel_other": (500.0, 0.0, 0.0, 510.0, 10.0, 10.0),
}
MESH_IDS = set(BOXES)

PLATE = "from build123d import Box\ndef build():\n    return {'name': 'plate', 'shape': Box(10, 10, 2), 'color': '#888888', 'process': 'mill'}\n"
FRAME_INIT = """
    from build123d import Pos
    from geom import place
    from . import plate
    def build():
        return [place(plate.build(), Pos(0, 0, 0))]
    """


def cab_init(boxes=BOXES, only=None):
    items = [
        dict(name="Cabinet unit", mesh=box_mesh(*boxes["Cabinet_unit"]), color="#334455", process="bought", code="CU-1",
             kind="electrical", desc="Cabinet unit 24V", supplier="Vendor", qty=2),
        dict(name="Panel print", mesh=box_mesh(*boxes["Panel_print"]), color="#ff0000", process="print", material="PLA"),
        dict(name="Panel laser", mesh=box_mesh(*boxes["Panel_laser"]), color="#aaaaaa", process="laser", material="S235JR",
             thickness=2.0),
        dict(name="Panel other", mesh=box_mesh(*boxes["Panel_other"]), color="#00ff00", process="other"),
    ]
    if only:
        items = [x for x in items if x["name"] in only]
    return f"ITEMS = {items!r}\ndef build():\n    return [dict(x) for x in ITEMS]\n"


def mesh_project(root, boxes=BOXES, only=None):
    return _proj(Path(root), {}, "from parts import frame, cab\nout = frame.build() + cab.build()\n", files={
        "frame/plate.py": PLATE, "frame/__init__.py": FRAME_INIT, "cab/__init__.py": cab_init(boxes, only)})


def load_triangles(path):
    """The stored mesh file as (vertices Nx3 mm, faces Mx3), whatever format the implementation chose: JSON / npz with
    v|vertices and f|t|faces, or anything trimesh reads (stl, ply, obj, glb)."""
    path = Path(path)
    data = None
    try:
        data = json.loads(path.read_text())
    except (UnicodeDecodeError, ValueError):
        try:
            data = dict(np.load(path, allow_pickle=False))
        except Exception:
            data = None
    if isinstance(data, dict):
        vk = next((k for k in ("v", "vertices", "pos") if k in data), None)
        fk = next((k for k in ("f", "t", "faces", "idx") if k in data), None)
        assert vk and fk, f"{path}: no vertex/face arrays in {sorted(data)}"
        return np.asarray(data[vk], dtype=float).reshape(-1, 3), np.asarray(data[fk], dtype=int).reshape(-1, 3)
    import trimesh
    m = trimesh.load(str(path), force="mesh", process=False)
    return np.asarray(m.vertices, dtype=float), np.asarray(m.faces, dtype=int)


def bbox_of(v, f):
    pts = np.asarray(v)[np.asarray(f).ravel()]
    return np.array([pts.min(0), pts.max(0)])


def expected_bbox(pid, boxes=BOXES):
    b = boxes[pid]
    return np.array([b[:3], b[3:]])


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The mesh project after one `--model` run (read-only for the tests that use it)."""
    root = mesh_project(tmp_path_factory.mktemp("mesh") / "p")
    r = H.run_model(root)
    return root, r


def _ok(built):
    root, r = built
    r.ok()
    return root


# ---------------------------------------------------------------- DW1 + DW5: stored by --model, any process
def test_model_build_accepts_mesh_parts_of_every_process(built):
    """DW1, DW5: a node returning mesh parts (bought, print, laser, other) without a shape builds; every one is in parts.json."""
    root, r = built
    assert r.rc == 0 and r.summary, f"--model must accept mesh-only parts of any process:\n{r.log[-1500:]}"
    assert MESH_IDS | {"plate"} <= set(H.by_id(root)), sorted(H.by_id(root))


def test_mesh_part_entry_points_to_a_stored_triangle_file_and_has_no_step(built):
    """DW1: `mesh` is a path under build/model/ to an existing file holding the same triangles; no `step`."""
    root = _ok(built)
    parts = H.by_id(root)
    for pid in MESH_IDS:
        p = parts[pid]
        assert "step" not in p, f"{pid}: a mesh part has no STEP, got {p.get('step')!r}"
        assert isinstance(p.get("mesh"), str), f"{pid}: `mesh` must be a path to the stored file, got {type(p.get('mesh')).__name__}"
        assert p["mesh"].startswith("build/model/"), f"{pid}: mesh file must live under build/model/, got {p['mesh']!r}"
        f = root / p["mesh"]
        assert f.is_file(), f"{pid}: stored mesh file {p['mesh']} missing"
        v, tri = load_triangles(f)
        assert len(tri) == 12, f"{pid}: stored file has {len(tri)} triangles, the part gave 12"
        np.testing.assert_allclose(bbox_of(v, tri), expected_bbox(pid), atol=1e-6, err_msg=pid)
    assert len({parts[pid]["mesh"] for pid in MESH_IDS}) == len(MESH_IDS), "each mesh part has its own file"


def test_mesh_part_keeps_its_fields_and_qty_from_the_dict(built):
    """DW1: the item's other fields as for any part, qty from the dict (units, like a row), node = the node that returned it."""
    root = _ok(built)
    p = H.by_id(root)["Cabinet_unit"]
    want = {"name": "Cabinet unit", "process": "bought", "code": "CU-1", "kind": "electrical", "desc": "Cabinet unit 24V",
            "supplier": "Vendor", "color": "#334455", "node": "cab", "qty": 2}
    assert {k: p.get(k) for k in want} == want
    assert len(p.get("key", "")) == 64
    lp = H.by_id(root)["Panel_laser"]
    assert lp["process"] == "laser" and lp["material"] == "S235JR" and lp["thickness"] == 2.0


def test_mesh_part_is_placed_once_where_it_stands(built):
    """DW1: one placement, named by the part id, identity location (the triangles are already in machine coordinates)."""
    root = _ok(built)
    for pid in MESH_IDS:
        pls = H.by_id(root)[pid].get("placements")
        assert pls and len(pls) == 1, f"{pid}: exactly one placement expected, got {pls!r}"
        assert pls[0]["name"] == pid, pls
        np.testing.assert_allclose(pls[0]["loc"], IDENTITY, atol=1e-12, err_msg=pid)


def test_a_part_file_may_return_a_mesh_part(tmp_path):
    """DW1 (part-file door): parts/cab/unit.py build() returns the mesh part, the node returns it as is (no place(): it stands
    where its triangles are)."""
    unit = f"def build():\n    return {dict(name='unit', mesh=box_mesh(0, 0, 0, 20, 20, 40), process='bought', code='U-1')!r}\n"
    root = _proj(tmp_path / "p", {}, "from parts import frame, cab\nout = frame.build() + cab.build()\n", files={
        "frame/plate.py": PLATE, "frame/__init__.py": FRAME_INIT, "cab/unit.py": unit,
        "cab/__init__.py": "from . import unit\ndef build():\n    return [unit.build()]\n"})
    H.run_model(root).ok()
    p = H.by_id(root)["unit"]
    assert "step" not in p and isinstance(p.get("mesh"), str) and (root / p["mesh"]).is_file(), p
    v, tri = load_triangles(root / p["mesh"])
    assert len(tri) == 12
    np.testing.assert_allclose(bbox_of(v, tri), [[0, 0, 0], [20, 20, 40]], atol=1e-6)
    assert len(p.get("placements") or []) == 1


# ---------------------------------------------------------------- DW2: reuse, and a change of triangles
def test_unchanged_mesh_part_is_reused_and_its_file_kept_changed_triangles_change_the_file(tmp_path):
    root = mesh_project(tmp_path / "p", only={"Cabinet unit"})          # bought only: independent of DW5
    H.run_model(root).ok()
    p1 = H.by_id(root)["Cabinet_unit"]
    assert isinstance(p1.get("mesh"), str), f"mesh not stored as a file path: {p1.get('mesh')!r}"
    sha1 = H.sha_file(root / p1["mesh"])

    r = H.run_model(root).ok()
    assert r.summary[0] == 0, f"nothing changed: no node may rebuild, rebuilt {r.rebuilt}"
    p2 = H.by_id(root)["Cabinet_unit"]
    assert p2["mesh"] == p1["mesh"] and H.sha_file(root / p2["mesh"]) == sha1, "an unchanged mesh file must stay as it is"

    moved = dict(BOXES, Cabinet_unit=(200.0, 10.0, 5.0, 250.0, 30.0, 65.0))
    (root / "mechanics/cad/parts/cab/__init__.py").write_text(cab_init(moved, {"Cabinet unit"}))
    H.run_model(root).ok()
    p3 = H.by_id(root)["Cabinet_unit"]
    f3 = root / p3["mesh"]
    assert f3.is_file() and H.sha_file(f3) != sha1, "changed triangles must change the stored mesh file"
    v, tri = load_triangles(f3)
    np.testing.assert_allclose(bbox_of(v, tri), expected_bbox("Cabinet_unit", moved), atol=1e-6)


# ---------------------------------------------------------------- DW3: viewer
@pytest.fixture(scope="module")
def viewer(built):
    """derive.py viewer on the built project, run once; the tests check rc themselves (a failure here is a test failure)."""
    root, r = built
    return root, (H.run_derive(root, "viewer") if r.rc == 0 else None)


def _viewer_ok(viewer):
    root, d = viewer
    assert d is not None, "the --model build of the mesh project failed (see test_model_build_accepts_mesh_parts_of_every_process)"
    return root, d.ok()


def test_viewer_glb_has_one_mesh_node_per_mesh_part_with_the_same_triangles(viewer):
    root, d = _viewer_ok(viewer)
    js, binc = read_glb(H.find_glb(d, root))
    W = world_matrices(js)
    by_part = {}
    for i, n in mesh_nodes(js):
        by_part.setdefault((n.get("extras") or {}).get("part"), []).append(i)
    assert "plate" in by_part, "sanity: the shaped part is in the glb"
    for pid in MESH_IDS:
        assert len(by_part.get(pid, [])) == 1, f"{pid}: one glb mesh node with extras.part={pid!r} expected, got {by_part.get(pid)}"
        i = by_part[pid][0]
        prims = js["meshes"][js["nodes"][i]["mesh"]]["primitives"]
        ntri = sum(js["accessors"][p["indices"]]["count"] for p in prims) // 3
        assert ntri == 12, f"{pid}: glb mesh has {ntri} triangles, the part gave 12"
        np.testing.assert_allclose(node_world_bbox_mm(js, binc, i, W), expected_bbox(pid), atol=0.01, err_msg=pid)


def test_viewer_parts_json_lists_mesh_part_with_an_stl_download(viewer):
    root, d = _viewer_ok(viewer)
    vdir = root / d.manifest["viewer_dir"]
    cat = {c["id"]: c for c in json.loads((vdir / "parts.json").read_text())["parts"]}
    for pid in MESH_IDS:
        assert pid in cat, f"{pid} missing from the viewer parts.json: {sorted(cat)}"
        stl = (cat[pid].get("files") or {}).get("stl")
        assert stl and (vdir / stl).is_file(), f"{pid}: no stl file to download: {cat[pid].get('files')}"
        assert H.stl_triangles(vdir / stl) == 12
        np.testing.assert_allclose(cat[pid]["bbox_mm"], expected_bbox(pid), atol=0.01, err_msg=pid)


# ---------------------------------------------------------------- DW4 + DW5: kit scopes
def _files_for(d, pid):
    return [f for e in d.manifest["parts"] if e["id"] == pid for f in e.get("files", [])]


def test_step_scope_leaves_mesh_parts_out_of_model_step_and_per_part_steps(built):
    root = _ok(built)
    d = H.run_derive(root, "step").ok()
    for pid in MESH_IDS:
        assert not _files_for(d, pid), f"{pid}: the step scope must make no file for a mesh part: {_files_for(d, pid)}"
    assert "plate" in [e["id"] for e in d.manifest["parts"]], "sanity: the shaped part still gets its STEP"
    ms = root / d.manifest["model_step"]
    assert ms.is_file()
    text = ms.read_text(errors="replace")
    for name in ("Cabinet unit", "Cabinet_unit", "Panel other", "Panel_other"):
        assert name not in text, f"model.step must not contain the mesh part {name!r}"
    assert H.step_bbox(ms).max.X < 100, "model.step reaches the mesh parts' region (x >= 200): a mesh part got into it"


def test_step_scope_per_part_request_makes_no_file_for_a_mesh_part(built):
    root = _ok(built)
    d = H.run_derive(root, "step", part_id="Panel_other")
    if d.rc == 0 and d.manifest is not None:
        assert not _files_for(d, "Panel_other"), f"per-part step of a mesh part made files: {_files_for(d, 'Panel_other')}"
    made = [p for p in (root / "build").rglob("*.step") if "Panel" in p.name]
    assert not made, f"a STEP file was written for a mesh part: {made}"


def test_print_scope_makes_no_file_for_a_print_mesh_part(built):
    root = _ok(built)
    d = H.run_derive(root, "print").ok()
    assert not _files_for(d, "Panel_print"), f"print scope made files for a mesh part: {_files_for(d, 'Panel_print')}"


@pytest.mark.skipif(not H.SHOP_VENV.exists(), reason="laser scope needs the shop-drawings venv (SHOP_DRAW_VENV)")
def test_laser_scope_makes_no_file_for_a_laser_mesh_part(built):
    root = _ok(built)
    d = H.run_derive(root, "laser").ok()
    assert not _files_for(d, "Panel_laser"), f"laser scope made files for a mesh part: {_files_for(d, 'Panel_laser')}"


def test_bought_scope_makes_the_row_of_a_bought_mesh_part(built):
    root = _ok(built)
    d = H.run_derive(root, "bought").ok()
    rows = [r for r in d.manifest["bought"] if r["code"] == "CU-1"]
    assert len(rows) == 1, f"one bought row for the mesh part expected: {d.manifest['bought']}"
    r = rows[0]
    assert (r["name"], r["qty"], r["kind"], r["supplier"], r["node"]) == ("Cabinet unit 24V", 2, "electrical", "Vendor", "cab"), r
    assert not any(r["code"] in ("Panel print", "Panel laser", "Panel other") for r in d.manifest["bought"])


def test_a_mesh_part_returned_through_another_node_is_refused(tmp_path):
    """A mesh part stands where its triangles are: a node that returns another node's mesh parts (twice, as a placed sub-assembly
    would) cannot move or copy them, so the model build refuses it loudly instead of drawing one unit at the raw place."""
    root = _proj(tmp_path / "p", {}, "from parts import frame, rack\nout = frame.build() + rack.build()\n", files={
        "frame/plate.py": PLATE, "frame/__init__.py": FRAME_INIT,
        "cab/__init__.py": f"def build():\n    return [{dict(name='unit', mesh=box_mesh(0, 0, 0, 20, 20, 40), process='bought')!r}]\n",
        "rack/__init__.py": "from parts import cab\ndef build():\n    return cab.build() + cab.build()\n"})
    r = H.run_model(root)
    assert r.rc != 0 and "mesh part" in r.log, r.log[-800:]
