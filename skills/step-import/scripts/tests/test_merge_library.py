"""merge_library.py: a port's staged bought parts go into the client's real library without overwriting a part another of
his projects uses (review 8).

  python3 -m pytest -q test_merge_library.py
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
pytest.importorskip("build123d")
from build123d import Box, export_step  # noqa: E402

import merge_library as M  # noqa: E402
import step_to_project as S  # noqa: E402


def staged_part(lib, shape, name="Stepper motor NEMA17 17HS4401"):
    step = lib.parent / f"{len(list(lib.parent.glob('*.step')))}.step"
    export_step(shape, str(step))
    return S.library_part(lib, {"name": name, "kind": "bought", "type": "motor"}, "bought", step)


def project_importing(root, group, mod):
    f = root / "mechanics" / "cad" / "parts" / "machine" / "motor.py"
    f.parent.mkdir(parents=True)
    f.write_text(f"from geom import P\nfrom library.{group} import {mod}\n\n\ndef build():\n    return {mod}.build()\n")
    return f


def test_a_clash_with_another_projects_part_gets_a_free_name(tmp_path):
    client, staged, root = tmp_path / "client", tmp_path / "staged", tmp_path / "proj"
    group, mod, _ = staged_part(client, Box(10, 20, 30))      # his library already holds this motor (another machine)
    old = {f.name: f.read_bytes() for f in (client / "library" / group).iterdir()}
    g2, m2, _ = staged_part(staged, Box(11, 20, 30))          # the port's motor of the same name, another geometry
    assert (g2, m2) == (group, mod)
    part = project_importing(root, group, mod)
    out = M.merge(staged, client, root, log=lambda *a: None)
    assert out[f"{group}/{mod}"] == f"renamed {mod}_2", out
    for name, data in old.items():
        assert (client / "library" / group / name).read_bytes() == data, f"{name} of his other project was overwritten"
    assert (client / "library" / group / f"{mod}_2.step").is_file()
    assert f'"{mod}_2.step"' in (client / "library" / group / f"{mod}_2.py").read_text()
    assert f"from library.{group} import {mod}_2 as {mod}" in part.read_text()


def test_the_same_geometry_is_reused_and_nothing_is_copied(tmp_path):
    client, staged, root = tmp_path / "client", tmp_path / "staged", tmp_path / "proj"
    group, mod, _ = staged_part(client, Box(10, 20, 30), name="Motor A")
    staged_part(staged, Box(10, 20, 30), name="Motor B")      # the same 3D under another name
    part = project_importing(root, group, "motor_b")
    before = sorted(f.name for f in (client / "library" / group).iterdir())
    out = M.merge(staged, client, root, log=lambda *a: None)
    assert out[f"{group}/motor_b"] == f"reused {mod}", out
    assert sorted(f.name for f in (client / "library" / group).iterdir()) == before
    assert f"from library.{group} import {mod} as motor_b" in part.read_text()


def test_a_new_part_is_copied_as_it_is(tmp_path):
    client, staged, root = tmp_path / "client", tmp_path / "staged", tmp_path / "proj"
    group, mod, _ = staged_part(staged, Box(10, 20, 30))
    part = project_importing(root, group, mod)
    text = part.read_text()
    assert M.merge(staged, client, root, log=lambda *a: None)[f"{group}/{mod}"] == "copied"
    assert (client / "library" / group / f"{mod}.py").is_file() and (client / "library" / group / f"{mod}.step").is_file()
    assert part.read_text() == text


def test_a_part_drawn_as_code_and_a_brep_part_are_merged_too(tmp_path):
    """A stand-in drawn as code (no 3D file) and a part kept as BREP (its STEP is over the 20 MB a project may commit) are
    copied like a STEP part; the same code module is reused; a different module of the same name gets a free name."""
    client, staged, root = tmp_path / "client", tmp_path / "staged", tmp_path / "proj"
    lib = staged / "library" / "other"
    lib.mkdir(parents=True)
    (lib / "spring.py").write_text('def build(length=80):\n    return "spring"\n')
    (lib / "board.py").write_text('VENDOR = {"board": "board.brep"}\n')
    (lib / "board.brep").write_bytes(b"brep v1")
    mine = client / "library" / "other"
    mine.mkdir(parents=True)
    (mine / "board.py").write_text('VENDOR = {"board": "board.brep"}\n')
    (mine / "board.brep").write_bytes(b"brep of another board")
    part = root / "mechanics" / "cad" / "parts" / "machine" / "b.py"
    part.parent.mkdir(parents=True)
    part.write_text("from library.other import board\n")
    out = M.merge(staged, client, root, log=lambda *a: None)
    assert out == {"other/board": "renamed board_2", "other/spring": "copied"}, out
    assert (mine / "spring.py").read_text() == 'def build(length=80):\n    return "spring"\n'
    assert (mine / "board_2.brep").read_bytes() == b"brep v1" and (mine / "board.brep").read_bytes() == b"brep of another board"
    assert '"board_2.brep"' in (mine / "board_2.py").read_text()
    assert "from library.other import board_2 as board" in part.read_text()
    assert M.merge(staged, client, root, log=lambda *a: None)["other/spring"] == "reused spring"
