"""Issue #78 «mesh parts», the outputs: what derive.py and check.py's joints gate make of a mesh part.
The model is the main project of meshsolid_proj.py, built once per run by `cad_incremental.py --model`.

Done-when served here (numbers of the ticket):
  DW3   derive.py print (whole scope and per part): an STL, no STEP; watertight; the mesh's volume; laid on its largest flat face
        at its first placement, or turned by print_rot
  DW4   derive.py viewer: model.glb has the mesh part once per placement where it stands; the viewer parts.json offers its STL
  DW5   derive.py step: model.step from the other parts, the manifest says `not_in_step`, no failure
  DW6   bought / laser: a print mesh part is in neither
  DW9   derive.py assembly: the node placing the mesh part gets a PDF whose parts table lists it
  DW10  cad_gates' joints gate counts mesh placements like STEP ones

    python -m pytest skills/machine-project/scripts/tests/test_mesh_solid_derive.py -n 8 -q -W ignore     (from agent/)
    (DW9 needs SHOP_DRAW_VENV, default /tmp/shopdrawvenv)
"""
import json
import os
import re
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest
import trimesh

import helpers as H
import meshsolid_proj as MP
from meshsolid_proj import mesh_main, mesh_copy, main_ok, load_mesh, trimesh_of, placement_map  # noqa: F401
from test_instances import read_glb, world_matrices, mesh_nodes, node_world_bbox_mm


@pytest.fixture(scope="module")
def scopes(mesh_main, tmp_path_factory):
    """A copy of the built project with derive.py print / step / bought / viewer run once each (rc checked by the tests)."""
    import conftest
    root = conftest._clean_copy(main_ok(mesh_main), tmp_path_factory.mktemp("msd") / "proj")
    return root, {s: H.run_derive(root, s) for s in ("print", "step", "bought", "viewer")}


def _scope(scopes, s):
    root, runs = scopes
    return root, runs[s].ok()


def _stls(root, d, pid):
    return [f for f in H.file_list(H.entry(d, pid), root) if f.suffix.lower() == ".stl"]


# ================================================================ DW3: print
def test_dw3_print_row_of_a_mesh_part_has_an_stl_and_no_step(scopes):
    root, d = _scope(scopes, "print")
    for pid in ("shell", "wedge"):
        e = H.entry(d, pid)
        files = H.file_list(e, root)
        assert [f for f in files if f.suffix.lower() == ".stl"], f"{pid}: no STL in its print row: {files}"
        assert not [f for f in files if f.suffix.lower() in (".step", ".stp")], f"{pid}: a mesh part has no STEP: {files}"
        for f in files:
            assert f.is_file() and f.stat().st_size > 0, f"{pid}: {f} missing or empty"
    assert H.entry(d, "shell").get("qty") == 2, H.entry(d, "shell")


def test_dw3_print_stl_is_watertight_with_the_meshs_volume(scopes):
    root, d = _scope(scopes, "print")
    for pid, vol in (("shell", 40 * 10 * 30), ("wedge", 10 * 20 * 30)):
        (stl,) = _stls(root, d, pid)
        m = trimesh.load(str(stl), force="mesh")
        assert m.is_watertight, f"{pid}: print STL is not watertight"
        v, f = load_mesh(root, H.by_id(root)[pid])
        stored = abs(trimesh_of(v, f).volume)
        assert stored == pytest.approx(vol, rel=1e-3), f"{pid}: stored mesh volume {stored}"
        assert abs(m.volume) == pytest.approx(stored, rel=0.005), f"{pid}: STL volume {m.volume} vs mesh {stored}"


def test_dw3_print_stl_lies_on_its_largest_flat_face(scopes):
    """shell stands 40 x 10 x 30 at its first placement: its largest face is 40 x 30, so it is printed 10 high, that face on z=0."""
    root, d = _scope(scopes, "print")
    (stl,) = _stls(root, d, "shell")
    m = trimesh.load(str(stl), force="mesh")
    lo, hi = m.bounds
    assert lo[2] == pytest.approx(0.0, abs=1e-3), f"min z must be 0, got {lo[2]}"
    assert hi[2] - lo[2] == pytest.approx(10.0, abs=0.01), f"laid on the 40 x 30 face the part is 10 high: bounds {m.bounds}"
    assert sorted(np.round(hi[:2] - lo[:2], 2)) == pytest.approx([30.0, 40.0], abs=0.01), m.bounds
    tri = m.triangles
    on_bed = np.all(np.abs(tri[:, :, 2]) < 1e-3, axis=1)
    assert m.area_faces[on_bed].sum() == pytest.approx(1200.0, rel=0.01), "the 40 x 30 face must lie on z = 0"


def test_dw3_print_rot_turns_the_part_after_its_first_placement(scopes):
    """wedge: own 10 x 20 x 30, first placement turned 90 deg about Z (20 x 10 x 30), then print_rot ["X", 90] -> 20 x 30 x 10."""
    root, d = _scope(scopes, "print")
    (stl,) = _stls(root, d, "wedge")
    m = trimesh.load(str(stl), force="mesh")
    assert m.bounds[0][2] == pytest.approx(0.0, abs=1e-3), m.bounds
    np.testing.assert_allclose(m.bounds[1] - m.bounds[0], [20, 30, 10], atol=0.01)


def test_dw3_per_part_print_gives_the_same_stl(scopes, tmp_path):
    root, d = _scope(scopes, "print")
    (whole,) = _stls(root, d, "shell")
    one = H.run_derive(root, "print", manifest=root / "build/kit/print_one.json", part_id="shell").ok()
    stls = _stls(root, one, "shell")
    assert len(stls) == 1 and not [f for f in H.file_list(H.entry(one, "shell"), root) if f.suffix.lower() in (".step", ".stp")]
    a, b = trimesh.load(str(whole), force="mesh"), trimesh.load(str(stls[0]), force="mesh")
    np.testing.assert_allclose(a.bounds, b.bounds, atol=1e-3)
    assert abs(a.volume) == pytest.approx(abs(b.volume), rel=1e-4)


# ================================================================ DW4: viewer
def test_dw4_glb_has_the_mesh_part_once_per_placement_where_it_stands(scopes):
    root, d = _scope(scopes, "viewer")
    js, binc = read_glb(H.find_glb(d, root))
    W = world_matrices(js)
    ids = {"shell", "shell_a", "shell_b"}
    got = [(i, (n.get("extras") or {}).get("part")) for i, n in mesh_nodes(js) if (n.get("extras") or {}).get("part") in ids]
    assert len(got) == 2, f"two glb mesh nodes for the two shell placements expected, got {got}"
    boxes = sorted(node_world_bbox_mm(js, binc, i, W).tolist() for i, _ in got)
    want = sorted(np.array(MP.WORLD[n], dtype=float).tolist() for n in ("shell_a", "shell_b"))
    np.testing.assert_allclose(boxes, want, atol=0.05)
    for name in ("wedge", "twin"):
        hit = [i for i, n in mesh_nodes(js) if (n.get("extras") or {}).get("part") == name]
        assert len(hit) == 1, f"{name}: one glb node expected, got {hit}"
        np.testing.assert_allclose(node_world_bbox_mm(js, binc, hit[0], W), MP.WORLD[name], atol=0.05, err_msg=name)


def test_dw4_viewer_parts_json_lists_the_mesh_part_with_an_stl(scopes):
    root, d = _scope(scopes, "viewer")
    vdir = root / d.manifest["viewer_dir"]
    cat = json.loads((vdir / "parts.json").read_text())["parts"]
    mine = [c for c in cat if c["id"] in ("shell", "shell_a", "shell_b") or c.get("part_id") == "shell"]
    assert len(mine) >= 1, f"the mesh part is missing from the viewer parts.json: {[c['id'] for c in cat]}"
    for c in mine:
        stl = (c.get("files") or {}).get("stl")
        assert stl and (vdir / stl).is_file() and H.stl_triangles(vdir / stl) >= 12, f"{c['id']}: no STL to download: {c.get('files')}"


# ================================================================ DW5: step
def test_dw5_step_scope_builds_model_step_without_the_mesh_parts_and_says_so(scopes):
    root, d = _scope(scopes, "step")
    nis = d.manifest.get("not_in_step")
    assert isinstance(nis, list) and set(nis) == MP.MESH_IDS, f"the step manifest must list the mesh parts in not_in_step: {nis!r}"
    ms = root / d.manifest["model_step"]
    assert ms.is_file() and ms.stat().st_size > 0
    text = ms.read_text(errors="replace")
    for pid in MP.MESH_IDS:
        assert f"'{pid}'" not in text and f"'{pid}_" not in text, f"model.step must not hold the mesh part {pid!r}"
    assert "'base'" in text and "'cap'" in text, "the STEP parts must be in model.step"
    for pid in MP.MESH_IDS:
        assert not [f for e in d.manifest["parts"] if e["id"] == pid for f in e.get("files", [])], f"{pid}: no STEP file"


def test_dw5_per_part_step_of_a_mesh_part_writes_no_step(scopes):
    """The per-part form is not named by DW5: it may refuse a mesh part (it has no STEP to give), but only by saying which part,
    and it never writes a STEP for it."""
    root, _ = _scope(scopes, "step")
    d = H.run_derive(root, "step", manifest=root / "build/kit/step_one.json", part_id="twin")
    if d.rc != 0:
        assert "twin" in d.log and "Traceback" not in d.log, f"a refusal must name the part, not crash:\n{d.log[-1500:]}"
    else:
        assert not [f for e in (d.manifest or {}).get("parts", []) if e["id"] == "twin" for f in e.get("files", [])], d.manifest
    assert not [p for p in (root / "build").rglob("*.step") if "twin" in p.name], "a STEP file was written for the mesh part"


# ================================================================ DW6: bought / laser
def test_dw6_print_mesh_part_is_not_in_bought(scopes):
    root, d = _scope(scopes, "bought")
    text = json.dumps(d.manifest["bought"], ensure_ascii=False)
    for pid in MP.MESH_IDS:
        assert f'"{pid}"' not in text, f"{pid} must not be a bought row: {d.manifest['bought']}"


@pytest.mark.skipif(not H.SHOP_VENV.exists(), reason="laser scope needs the shop-drawings venv (SHOP_DRAW_VENV)")
def test_dw6_print_mesh_part_is_not_in_laser(mesh_main, tmp_path):
    import conftest
    root = conftest._clean_copy(main_ok(mesh_main), tmp_path / "proj")
    d = H.run_derive(root, "laser").ok()
    assert not {e["id"] for e in d.manifest["parts"]} & MP.MESH_IDS, d.manifest["parts"]


# ================================================================ DW9: assembly drawings
VENV = Path(os.environ.get("SHOP_DRAW_VENV", "/tmp/shopdrawvenv"))


@pytest.mark.skipif(not (VENV / "bin" / "python").exists(), reason=f"drawings venv not available at {VENV}")
def test_dw9_assembly_drawing_of_the_node_lists_the_mesh_part(mesh_main, tmp_path):
    import conftest
    from test_assembly_drawings import run_assembly, pdf_pages
    root = conftest._clean_copy(main_ok(mesh_main), tmp_path / "proj")
    r = run_assembly(root).ok()
    pdf = r.pdf("frame")
    assert pdf.is_file() and pdf.stat().st_size > 0
    text = " ".join(p["flat"] for p in pdf_pages(pdf))
    assert "shell" in text, f"the parts table of node 'frame' must list the mesh part 'shell':\n{text[:1500]}"
    assert "wedge" in text, f"the parts table of node 'frame' must list the mesh part 'wedge':\n{text[:1500]}"


# ================================================================ DW10: joints gate
def _gate(root):
    if str(H.SCRIPTS) not in sys.path:
        sys.path.insert(0, str(H.SCRIPTS))
    import cad_gates
    return cad_gates.gate_joints(str(root))


def test_dw10_joints_gate_counts_docked_mesh_placements(mesh_main):
    """base is the root; shell_a, shell_b, cap, wedge and twin are docked: 5 docked placements, the mesh ones included."""
    root = main_ok(mesh_main)
    (st, rule, detail), = _gate(root)
    assert st == "PASS", (st, detail)
    assert re.search(r"\b5 placements docked", detail) and "root base" in detail, \
        f"the gate must count the 4 docked mesh placements too: {detail!r}"


def test_dw10_joints_gate_fails_an_undocked_mesh_placement(mesh_copy):
    """shell_b's joint removed from parts.json: a mesh part placed without a joint is reported like a STEP part would be."""
    root = mesh_copy
    pj = root / "build/model/parts.json"
    data = json.loads(pj.read_text())
    for p in data["parts"]:
        for pl in p.get("placements") or []:
            if pl["name"] == "shell_b":
                pl.pop("joint", None)
    pj.write_text(json.dumps(data))
    (st, rule, detail), = _gate(root)
    assert st == "FAIL" and "shell_b" in detail, f"an undocked mesh placement must FAIL the joints gate: {st} {detail!r}"
