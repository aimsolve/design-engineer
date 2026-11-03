"""Issue #15, done-when 4: a scaffolded new project uses the folder layout (node = folder, part = file) and check.py enforces it.

check.py runs as the router runs it (--no-validate). Negative cases start from a conforming new-layout project and break one thing;
each FAIL must name the offending file.
"""
import glob
import json
import textwrap
from pathlib import Path

import pytest

import helpers as H
import partfiles as F


# ================================================================ scaffold
def test_scaffold_writes_the_folder_layout(tmp_path):
    root = F.scaffold(tmp_path / "p")
    parts = H.cad_dir(root) / "parts"
    assert not (parts / "example.py").exists(), "the template must not write the old one-module node parts/example.py"
    assert (parts / "example" / "__init__.py").is_file(), "node = folder: parts/example/__init__.py"
    part_files = [p for p in (parts / "example").glob("*.py") if p.name != "__init__.py" and not p.name.startswith("_")]
    assert part_files, "the example node must have at least one part file parts/example/<part>.py"
    src = part_files[0].read_text()
    assert "def build(" in src
    assert "place(" in (parts / "example" / "__init__.py").read_text(), "the node __init__ places its parts with place()"
    assert "def place(" in (H.cad_dir(root) / "geom.py").read_text(), "geom.py provides place()"


def test_scaffolded_project_builds_with_placements(tmp_path):
    root = F.scaffold(tmp_path / "p")
    F.run_model(root).ok()
    ps = H.read_parts(root)
    shaped = [p for p in ps if p.get("step")]
    assert shaped, ps
    for p in shaped:
        pl = F.placements(p)
        assert p["qty"] == len(pl) >= 1, p
        assert p["node"] == "example", p["node"]
        assert all(x["node"] == "example" for x in pl)


def _assert_folder_layout(root):
    parts = H.cad_dir(root) / "parts"
    assert (parts / "example" / "__init__.py").is_file() and not (parts / "example.py").exists(), \
        "the scaffold must write the folder layout (parts/example/__init__.py, no parts/example.py)"


def test_scaffolded_project_passes_check(tmp_path):
    root = F.scaffold(tmp_path / "p")
    _assert_folder_layout(root)
    F.run_model(root).ok()
    cp = F.run_check(root)
    assert cp.returncode == 0, f"FAIL lines: {F.fail_lines(cp)}\n{(cp.stdout + cp.stderr)[-2500:]}"


def test_scaffolded_project_derives_print_and_step(tmp_path):
    root = F.scaffold(tmp_path / "p")
    _assert_folder_layout(root)
    d = H.run_derive(root, "print").ok()
    ps = H.by_id(root)
    assert d.manifest["parts"]
    for e in d.manifest["parts"]:
        assert e["qty"] == len(F.placements(ps[e["id"]])), e
    assert H.run_derive(root, "step").ok().manifest["model_step"]


def test_scaffolded_kit_inputs_cover_part_files_in_node_folders(tmp_path):
    """The router hashes kit.json inputs to know a download is stale: an edit of parts/<node>/<part>.py must be covered."""
    root = F.scaffold(tmp_path / "p")
    kit = json.loads((root / "kit.json").read_text())
    globs = list(kit.get("inputs", []))
    for s in kit.get("scopes", {}).values():
        globs += s.get("inputs", [])
    covered = set()
    for g in globs:
        covered |= {Path(p).resolve() for p in glob.glob(str(root / g), recursive=True)}
    node = H.cad_dir(root) / "parts" / "example"
    assert node.is_dir(), "precondition: the scaffold writes the node folder parts/example/"
    for f in node.glob("*.py"):
        assert f.resolve() in covered, f"kit.json inputs {globs} do not cover {f.relative_to(root)}"
    deeper = node / "sub" / "deep.py"
    deeper.parent.mkdir()
    deeper.write_text("")
    covered = {Path(p).resolve() for g in globs for p in glob.glob(str(root / g), recursive=True)}
    assert deeper.resolve() in covered, f"kit.json inputs {globs} do not cover a nested node folder"


# ================================================================ check.py on the new layout
GOOD_NODE = '''
from build123d import Pos
from geom import P, place
from . import plate


def build():
    return [place(plate.build(), Pos(0, 0, 0)), place(plate.build(), Pos(0, 50, 0)),
            P("pipe", None, "#999999", process="bought", code="TR50", qty=3, unit="m", kind="profile")]
'''
GOOD_PART = '''
from build123d import Box
from geom import P


def build(w=40):
    return P("plate", Box(w, 30, 2), "#aaaaaa", process="laser", material="S235JR", thickness=2.0, code="PL-1")
'''
ASSEMBLE = '''import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from parts import frame

items = frame.build()
'''


def _new_layout(root):
    """Scaffold, then replace the example node with a conforming folder node frame/ (node __init__ + one part file)."""
    F.scaffold(root)
    parts = H.cad_dir(root) / "parts"
    for old in ("example.py",):
        (parts / old).unlink(missing_ok=True)
    import shutil
    shutil.rmtree(parts / "example", ignore_errors=True)
    (parts / "frame").mkdir()
    (parts / "frame" / "__init__.py").write_text(GOOD_NODE.lstrip())
    (parts / "frame" / "plate.py").write_text(GOOD_PART.lstrip())
    (H.cad_dir(root) / "geom.py").write_text(F.geom_src())
    (H.cad_dir(root) / "assemble.py").write_text(ASSEMBLE)
    return root


def _fails_naming(root, *names):
    cp = F.run_check(root)
    fl = F.fail_lines(cp)
    assert cp.returncode == 1 and fl, f"check.py must FAIL (exit 1), got {cp.returncode}\n{(cp.stdout + cp.stderr)[-2500:]}"
    for n in names:
        assert any(n in ln for ln in fl), f"no FAIL line names {n!r}: {fl}"
    return fl


def test_conforming_new_layout_passes_check(tmp_path):
    root = _new_layout(tmp_path / "p")
    cp = F.run_check(root)
    assert cp.returncode == 0, f"FAIL lines: {F.fail_lines(cp)}\n{(cp.stdout + cp.stderr)[-2500:]}"


def test_check_fails_when_a_node_returns_a_shaped_part_directly(tmp_path):
    root = _new_layout(tmp_path / "p")
    node = H.cad_dir(root) / "parts" / "frame" / "__init__.py"
    H.edit(node, "from build123d import Pos", "from build123d import Box, Pos")
    H.edit(node, "P(\"pipe\"", "P(\"bracket\", Box(5, 5, 5), \"#123456\", process=\"mill\"),\n            P(\"pipe\"")
    _fails_naming(root, "frame")


def test_check_fails_when_a_node_returns_a_shaped_dict_literal_directly(tmp_path):
    root = _new_layout(tmp_path / "p")
    node = H.cad_dir(root) / "parts" / "frame" / "__init__.py"
    H.edit(node, "from build123d import Pos", "from build123d import Box, Pos")
    H.edit(node, "P(\"pipe\"", "{\"name\": \"bracket\", \"shape\": Box(5, 5, 5), \"process\": \"mill\"},\n            P(\"pipe\"")
    _fails_naming(root, "frame")


def test_check_fails_when_a_part_file_returns_a_list(tmp_path):
    root = _new_layout(tmp_path / "p")
    part = H.cad_dir(root) / "parts" / "frame" / "plate.py"
    H.edit(part, "    return P(\"plate\"", "    return [P(\"plate\"")
    H.edit(part, "code=\"PL-1\")", "code=\"PL-1\")]")
    _fails_naming(root, "plate")


def test_check_fails_when_qty_is_set_on_a_placed_part(tmp_path):
    root = _new_layout(tmp_path / "p")
    part = H.cad_dir(root) / "parts" / "frame" / "plate.py"
    H.edit(part, "code=\"PL-1\")", "code=\"PL-1\", qty=2)")
    _fails_naming(root, "plate", "qty")


def test_check_fails_when_qty_is_added_to_a_placed_copy_in_the_node(tmp_path):
    root = _new_layout(tmp_path / "p")
    node = H.cad_dir(root) / "parts" / "frame" / "__init__.py"
    H.edit(node, "place(plate.build(), Pos(0, 50, 0))", "{**place(plate.build(), Pos(0, 50, 0)), \"qty\": 2}")
    _fails_naming(root, "qty")


def test_old_layout_project_fails_check_naming_the_file(tmp_path):
    """the old layout is gone: a parts/<node>.py returning world-coordinate parts with qty FAILs the CAD layout gate."""
    root = _new_layout(tmp_path / "p")
    import shutil
    shutil.rmtree(H.cad_dir(root) / "parts" / "frame")
    (H.cad_dir(root) / "parts" / "frame.py").write_text(textwrap.dedent('''
        from build123d import Box, Pos


        def build():
            return [{"name": "plate", "shape": Pos(0, 50, 0) * Box(40, 30, 2), "process": "laser", "material": "S235JR",
                     "thickness": 2.0, "qty": 2}]
        ''').lstrip())
    cp = F.run_check(root)
    assert cp.returncode != 0, f"an old-layout node must FAIL check.py:\n{(cp.stdout + cp.stderr)[-2500:]}"
    assert any("frame.py" in ln for ln in F.fail_lines(cp)), F.fail_lines(cp)
