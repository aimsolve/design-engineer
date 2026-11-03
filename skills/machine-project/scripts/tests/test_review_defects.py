"""Reproductions of the 13 defects an independent review found in `cad_incremental.py --model` and `derive.py`
(rung R2). One test per defect, each asserting the CORRECT behaviour; each fails today.

Numbers in test names / docstrings (D1..D13) are the defect numbers of the review, not the "done when" numbers.
Tiny throw-away projects are written by `_proj` in the folder layout: each node `n` is parts/n/__init__.py placing ONE
part file parts/n/n.py (the caller's source, whose build() returns one part dict); `helpers` are private modules parts/_x.py.
A rebuilt node therefore shows as two build units, "n.n.build" (the part file) and "n.build" (the node).
"""
import json
import os
import pprint
import re
import shutil
import subprocess
import procgroup
import sys
import textwrap
import time
from pathlib import Path

import pytest

import helpers as H

PRE = "import sys, os\nsys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"


def _proj(root, nodes, assemble, params="A=10.0\n", helpers=None, files=None):
    """nodes {n: part file source}: node n places its one part file n.py at the origin. helpers {"_x": source} -> parts/_x.py.
    files {rel path under parts/: source}: written as given (a node __init__ the default does not cover)."""
    root = Path(root)
    cad = H.write_base(root, params=params)
    for n, s in nodes.items():
        H.write_node(root, n, {n: s})
    for n, s in (helpers or {}).items():
        (cad / "parts" / f"{n}.py").write_text(textwrap.dedent(s))
    for rel, s in (files or {}).items():
        (cad / "parts" / rel).parent.mkdir(parents=True, exist_ok=True)
        (cad / "parts" / rel).write_text(textwrap.dedent(s).lstrip())
    (cad / "assemble.py").write_text(PRE + textwrap.dedent(assemble))
    return root


def _u(*nodes):
    """The build units rebuilt when node n's part file has to rebuild: n.n.build and n.build."""
    return {f"{n}.{n}.build" for n in nodes} | {f"{n}.build" for n in nodes}


def _x(root, pid):
    """X extent of a stored part STEP."""
    return H.step_bbox(root / H.by_id(root)[pid]["step"]).size.X


# ------------------------------------------------------------------ D1: `from params import *`
def test_d1_star_import_of_params_rebuilds_only_the_node_that_uses_the_changed_value(tmp_path):
    root = _proj(tmp_path / "p", {
        "a": "from params import *\nfrom build123d import Box\ndef build():\n    return {'name': 'a', 'shape': Box(A, 1, 1), 'process': 'mill'}\n",
        "b": "from params import *\nfrom build123d import Box, Pos\ndef build():\n    return {'name': 'b', 'shape': Pos(50, 0, 0) * Box(B, 1, 1), 'process': 'mill'}\n",
        "c": "from parts import _h\nfrom build123d import Box, Pos\ndef build():\n    return {'name': 'c', 'shape': Pos(90, 0, 0) * Box(_h.width(), 1, 1), 'process': 'mill'}\n",
    }, "from parts import a, b, c\nout = a.build() + b.build() + c.build()\n",
        params="A=10.0\nB=3.0\nC=4.0\nUNUSED=1.0\n",
        # c reaches params only through a helper module that also star-imports params
        helpers={"_h": "from params import *\ndef width():\n    return C\n"})
    assert H.run_model(root).ok().summary == (6, 0, 3)
    cad = root / "mechanics" / "cad"

    H.edit(cad / "params.py", "A=10.0", "A=12.0")
    r = H.run_model(root).ok()
    assert r.rebuilt == _u("a") and r.summary == (2, 4, 3), f"editing A must rebuild only a: {r.rebuilt}\n{r.log[-500:]}"
    assert _x(root, "a") == pytest.approx(12.0)

    H.edit(cad / "params.py", "C=4.0", "C=6.0")
    r = H.run_model(root).ok()
    assert r.rebuilt == _u("c"), f"editing C (read via a star-importing helper) must rebuild only c: {r.rebuilt}"
    assert _x(root, "c") == pytest.approx(6.0)

    H.edit(cad / "params.py", "UNUSED=1.0", "UNUSED=2.0")
    r = H.run_model(root).ok()
    assert r.summary == (0, 6, 3), f"a value no node uses must rebuild nothing: {r.rebuilt}"


# ------------------------------------------------------------------ D2: node writes a params attribute read by another node
def test_d2_value_written_to_params_by_a_reused_node_is_seen_by_a_rebuilt_reader(tmp_path):
    root = _proj(tmp_path / "p", {
        "a": "import params\nfrom build123d import Box\ndef build():\n    params.DERIVED = params.A * 2\n    return {'name': 'a', 'shape': Box(params.A, 1, 1), 'process': 'mill'}\n",
        "b": "import params\nfrom build123d import Box, Pos\ndef build():\n    return {'name': 'b', 'shape': Pos(50, 0, 0) * Box(params.DERIVED, 1, 1), 'process': 'mill'}\n",
    }, "from parts import a, b\nout = a.build() + b.build()\n", params="A=10.0\nDERIVED=1.0\n")
    H.run_model(root).ok()
    assert _x(root, "b") == pytest.approx(20.0), "precondition: clean build, b sees A*2"
    H.edit(root / "mechanics/cad/parts/b/b.py", "def build", "# touched\ndef build")   # b changed, a untouched
    H.run_model(root).ok()
    assert _x(root, "b") == pytest.approx(20.0), "b was rebuilt while a was reused: it must still see params.DERIVED == 20, not the file default"


# ------------------------------------------------------------------ D3: node writes a shared module global
def test_d3_global_written_by_a_reused_node_is_seen_by_a_rebuilt_reader(tmp_path):
    root = _proj(tmp_path / "p", {
        "a": "from build123d import Box\nfrom parts import _reg\ndef build():\n    _reg.HOLES.append(3.0)\n    return {'name': 'a', 'shape': Box(10, 1, 1), 'process': 'mill'}\n",
        "b": "from build123d import Box, Pos\nfrom parts import _reg\ndef build():\n    return {'name': 'b', 'shape': Pos(50, 0, 0) * Box(1 + len(_reg.HOLES), 1, 1), 'process': 'mill'}\n",
    }, "from parts import a, b\nout = a.build() + b.build()\n", helpers={"_reg": "HOLES = []\n"})
    H.run_model(root).ok()
    assert _x(root, "b") == pytest.approx(2.0), "precondition: clean build, b sees one hole"
    H.edit(root / "mechanics/cad/parts/b/b.py", "def build", "# touched\ndef build")
    H.run_model(root).ok()
    assert _x(root, "b") == pytest.approx(2.0), "output must equal a clean build whether a was replayed or never cached"


# ------------------------------------------------------------------ D4: a script that calls only some nodes
def _only_frame_script(project):
    (H.cad_dir(project) / "one.py").write_text(PRE + "from parts import frame\nframe.build()\n")


def test_d4_partial_run_keeps_every_other_nodes_model_and_derived_outputs(project):
    H.run_derive(project, "print").ok()
    steps0 = set(H.step_files(project))
    derived0 = {p.name for p in (project / "build/derived").iterdir()}
    assert derived0 and len(steps0) == len(H.PART_IDS), "precondition: full model and some derived output exist"
    _only_frame_script(project)
    H.run_model(project, script="one.py").ok()
    assert steps0 <= set(H.step_files(project)), "STEPs of nodes the script did not call were deleted"
    assert derived0 <= {p.name for p in (project / "build/derived").iterdir()}, "derived dirs of uncalled nodes were deleted"
    assert {p["id"] for p in H.read_parts(project)} == H.PART_IDS, "parts.json lost the uncalled nodes' parts"
    text = (project / "build/model/nodes.json").read_text()
    assert all(n in text for n in H.FUNCS), "nodes.json lost records of uncalled nodes"
    H.clear_ticks(project)
    r = H.run_model(project).ok()                    # the full run afterwards reuses everything
    assert r.summary == (0, H.N_UNITS, len(H.PART_IDS)) and H.ticks(project) == [], r.log[-600:]


def test_d4_entry_script_is_authoritative_a_node_it_no_longer_calls_is_dropped_and_gced(project):
    H.run_derive(project, "print").ok()
    src = H.assemble_src().replace(" misc,", "").replace("out += misc.build()\n", "")
    assert "misc" not in src and (H.cad_dir(project) / "parts" / "misc").is_dir()   # node folder stays, the call goes
    (H.cad_dir(project) / "assemble.py").write_text(src)
    H.run_model(project).ok()
    assert {p["id"] for p in H.read_parts(project)} == H.PART_IDS - {"misc_block"}
    assert "misc_block" not in (project / "build/model/nodes.json").read_text()
    assert len(H.step_files(project)) == len(H.PART_IDS) - 1, "STEP of the dropped node was not collected"


def test_d4_node_whose_module_was_deleted_disappears_after_a_full_run(project):
    shutil.rmtree(H.cad_dir(project) / "parts" / "misc")
    src = H.assemble_src().replace(" misc,", "").replace("out += misc.build()\n", "")
    assert "misc" not in src
    (H.cad_dir(project) / "assemble.py").write_text(src)
    H.run_model(project).ok()
    ids = {p["id"] for p in H.read_parts(project)}
    assert ids == H.PART_IDS - {"misc_block"}
    assert "misc_block" not in (project / "build/model/nodes.json").read_text()
    assert len(H.step_files(project)) == len(H.PART_IDS) - 1


# ------------------------------------------------------------------ D5: part dict round-trip
_RT_NODE = """
from build123d import Box
def build():
    return {"name": "a", "shape": Box(3, 1, 1), "process": "mill", "pos": (1, 2, 3), "holes": {1: 2, 3: {4: (5, 6)}},
            "tol": 0.05, "tags": ("x", "y"), "note": "n"}
"""
_RT_ASM = ("from pathlib import Path\nfrom pprint import pformat\nfrom parts import a\nout = a.build()\n"
           "seen = pformat([{k: v for k, v in d.items() if k not in ('shape', 'loc')} for d in out])\n"
           "Path(os.path.abspath(__file__)).parent.parent.parent.joinpath('seen.txt').write_text(seen)\n")


def test_d5_reused_node_returns_dicts_equal_to_a_fresh_run(tmp_path):
    root = _proj(tmp_path / "p", {"a": _RT_NODE}, _RT_ASM)
    H.run_model(root).ok()
    fresh = (root / "seen.txt").read_text()
    assert "(1, 2, 3)" in fresh and "{1: 2" in fresh
    (root / "seen.txt").unlink()
    r = H.run_model(root).ok()
    assert r.summary[0] == 0, "precondition: second run reuses the node"
    assert (root / "seen.txt").read_text() == fresh, "tuples / int-keyed dicts / extra fields must survive reuse unchanged"


def test_d5_non_json_extra_field_is_a_loud_error_on_the_first_build(tmp_path):
    root = _proj(tmp_path / "p", {"a": """
        from build123d import Box, Location
        def build():
            return {"name": "a", "shape": Box(3, 1, 1), "process": "mill", "where": Location((1, 2, 3))}
        """}, "from parts import a\nout = a.build()\n")
    r = H.run_model(root)
    assert r.rc != 0, "a non-JSON-serialisable extra field must not be silently dropped"
    assert "where" in r.log, r.log[-600:]


# ------------------------------------------------------------------ D6: caller returns callee.build() + own part
_D6_FILES = {
    "callee/__init__.py": """
        from build123d import Pos
        from geom import place
        from . import leg
        def build():
            return [place(leg.build(), Pos(0, 0, 0))]
        """,
    "callee/leg.py": "import params\nfrom build123d import Box\ndef build():\n    return {'name': 'leg', 'shape': Box(params.A, 1, 1), 'process': 'mill'}\n",
    "caller/__init__.py": """
        from build123d import Pos
        from geom import place
        from parts import callee
        from . import top
        def build():
            return callee.build() + [place(top.build(), Pos(50, 0, 0))]
        """,
    "caller/top.py": "from build123d import Box\ndef build():\n    return {'name': 'top', 'shape': Box(5, 5, 1), 'process': 'mill'}\n",
}
N_D6_UNITS = 4        # callee/leg.py, callee/__init__, caller/top.py, caller/__init__


def _d6_proj(root, nodes=None, assemble="from parts import caller\nout = caller.build()\n"):
    return _proj(root, nodes or {}, assemble, files=_D6_FILES)

def test_d6_caller_returning_callees_parts_has_no_duplicate_and_innermost_attribution(tmp_path):
    root = _d6_proj(tmp_path / "p")
    r = H.run_model(root)
    assert r.rc == 0 and r.summary, f"no duplicate-id error expected:\n{r.log[-600:]}"
    parts = H.read_parts(root)
    assert sorted(p["id"] for p in parts) == ["leg", "top"], "each id exactly once"
    by = {p["id"]: p for p in parts}
    assert by["leg"]["node"].endswith("callee") and by["top"]["node"].endswith("caller"), {k: v["node"] for k, v in by.items()}


# ------------------------------------------------------------------ D7: shape that is a child of a Compound
_D7_PART = """
from build123d import Box, Pos, Compound
def build():
    x = Box(3, 1, 1); y = Pos(10, 0, 0) * Box(1, 1, 1)
    sub = Compound(children=[x, y]); sub.label = "sub"
    return {"name": "{N}", "shape": {N}, "process": "mill"}
"""


def test_d7_part_shape_that_has_a_parent_compound_is_written_as_step(tmp_path):
    root = _proj(tmp_path / "p", {}, "from parts import a\nout = a.build()\n")
    H.write_node(root, "a", {"x": _D7_PART.replace("{N}", "x"), "y": _D7_PART.replace("{N}", "y")})
    r = H.run_model(root)
    assert r.rc == 0 and r.summary, r.log[-600:]
    assert _x(root, "x") == pytest.approx(3.0) and _x(root, "y") == pytest.approx(1.0)


# ------------------------------------------------------------------ D8: input files under build/
@pytest.mark.parametrize("rel", ["build/in/dims.txt", "build/vendor/x.brep"])
def test_d8_changing_an_input_file_under_build_rebuilds_the_node(tmp_path, rel):
    root = _proj(tmp_path / "p", {"a": f"""
        import os
        from build123d import Box
        def build():
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", *{rel.split('/')!r})
            L = float(len(open(p).read()))
            return {{"name": "a", "shape": Box(L, 1, 1), "process": "mill"}}
        """}, "from parts import a\nout = a.build()\n")
    f = root / rel
    f.parent.mkdir(parents=True)
    f.write_text("12345")
    H.run_model(root).ok()
    assert _x(root, "a") == pytest.approx(5.0)
    f.write_text("123456789")
    r = H.run_model(root).ok()
    assert r.rebuilt == _u("a"), f"{rel} changed but the node was reused"
    assert _x(root, "a") == pytest.approx(9.0)


# ------------------------------------------------------------------ D9: colour only on shape.color
def _is_red(c):
    if isinstance(c, str):
        return "ff0000" in c.lower() or c.lower() == "red"
    if isinstance(c, (list, tuple)):
        return len(c) >= 3 and c[0] in (1, 1.0, 255) and c[1] == 0 and c[2] == 0
    return False


def test_d9_colour_set_only_on_shape_color_reaches_parts_json_and_survives_reuse(tmp_path):
    root = _proj(tmp_path / "p", {"a": """
        from build123d import Box, Color
        def build():
            s = Box(3, 1, 1)
            s.color = Color(1, 0, 0)
            return {"name": "a", "shape": s, "process": "mill"}
        """}, "from parts import a\nout = a.build()\n")
    H.run_model(root).ok()
    assert _is_red(H.by_id(root)["a"].get("color")), H.by_id(root)["a"]
    r = H.run_model(root).ok()
    assert r.summary[0] == 0
    assert _is_red(H.by_id(root)["a"].get("color")), "colour lost on reuse"


# ------------------------------------------------------------------ D10: step-import RULES change
def test_d10_viewer_recomputes_when_the_step_import_rules_table_changes(dproj, tmp_path):
    fw = tmp_path / "fw"
    shutil.copytree(H.SCRIPTS.parent.parent, fw, ignore=shutil.ignore_patterns("__pycache__", "tests", ".git"))
    derive = fw / "machine-project/scripts/derive.py"

    def run():
        manifest = dproj / "build/manifest_viewer.json"
        cp = procgroup.run([sys.executable, str(derive), "viewer", str(manifest)], cwd=dproj, env=H.env_for(dproj),
                            capture_output=True, text=True, timeout=300)
        return H.Derived(cp, "viewer", manifest).ok()

    d1 = run()
    assert run().computed == 0, "precondition: unchanged rules reuse everything"
    params = fw / "step-import/scripts/params.py"
    params.write_text(params.read_text() + "\nRULES.insert(0, ('frame_type', re.compile(r'^frame$'), h_none))\n")
    d2 = run()
    assert d2.manifest["viewer_dir"] != d1.manifest["viewer_dir"], "the viewer output must be keyed by the RULES source (PRM.apply_all)"


# ------------------------------------------------------------------ D11: bought rows carry `note`
def test_d11_bought_rows_carry_the_part_note(dproj):
    H.replace_part(dproj, "nut_a", note="use loctite")
    d = H.run_derive(dproj, "bought").ok()
    row = {r["code"]: r for r in d.manifest["bought"]}["NUT-M5"]
    assert row.get("note") == "use loctite", row


# ------------------------------------------------------------------ D12: laser names that are prefixes / contain glob chars
@pytest.mark.skipif(not H.SHOP_VENV.exists(), reason="cadgen venv not available")
@pytest.mark.parametrize("names", [("A", "A.1"), ("[x]",)], ids=["prefix", "glob_chars"])
def test_d12_laser_parts_with_prefix_and_glob_names_get_exactly_their_own_files(tmp_path, names):
    from build123d import Box, Pos
    root = H.make_derived_model(tmp_path / "proj")
    sheets = [(n, Box(30 + 10 * i, 20, 2)) for i, n in enumerate(names)]
    parts = [p for p in H.read_parts(root) if p["process"] not in ("laser", "bent")]
    for i, (name, shape) in enumerate(sheets):
        p = H._write_part(root, re.sub(r"_+", "_", re.sub(r"[^\w]+", "_", name)).strip("_"), "laser", Pos(200 + 60 * i, 0, 0) * shape,
                          {"material": "S235JR", "thickness": 2.0, "qty": 1})
        p["name"] = name
        parts.append(p)
    H._write_parts_json(root, parts)
    d = H.run_derive(root, "laser").ok()
    assert len(d.manifest["parts"]) == len(names)
    seen = set()
    for e in d.manifest["parts"]:
        files = H.file_list(e, root)
        dxf = [f for f in files if f.suffix == ".dxf"]
        assert len(dxf) == 1, f"{e['name']!r}: expected exactly its own DXF, got {[f.name for f in files]}"
        assert len(set(files)) == len(files)
        assert not (set(files) & seen), f"{e['name']!r} shares files with another part"
        seen |= set(files)
        assert all(f.is_file() for f in files)


# ------------------------------------------------------------------ D13: derive GC vs a concurrent rebuild
def test_d13_derive_gc_keeps_anything_newer_than_its_start(dproj):
    """A rebuild running next to derive writes new-model STEPs / derived dirs that parts.json (read at derive start)
    does not list. Anything with an mtime after derive's start must survive; an old orphan still goes."""
    future = time.time() + 3600
    past = time.time() - 3600
    new_step = dproj / "build/model/step" / ("f" * 64 + ".step")
    new_step.write_text("new model")
    new_dir = dproj / "build/derived" / ("e" * 16)
    new_dir.mkdir(parents=True)
    (new_dir / "out.stl").write_text("x")
    old_step = dproj / "build/model/step" / ("0" * 64 + ".step")
    old_step.write_text("old")
    old_dir = dproj / "build/derived" / ("d" * 16)
    old_dir.mkdir()
    (old_dir / "out.stl").write_text("x")
    for p in (new_step, new_dir / "out.stl", new_dir):
        os.utime(p, (future, future))
    for p in (old_step, old_dir / "out.stl", old_dir):
        os.utime(p, (past, past))
    H.run_derive(dproj, "print").ok()
    assert not old_step.exists() and not old_dir.exists(), "precondition: stale orphans are still collected"
    assert new_step.exists(), "derive GC deleted a STEP newer than its own start (a concurrent rebuild's output)"
    assert new_dir.exists() and (new_dir / "out.stl").exists(), "derive GC deleted a derived dir newer than its own start"


# ------------------------------------------------------------------ round 2: nested reuse, untracked string params, concurrency


def _assert_stable_runs(root, n=3):
    for i in range(n):
        r = H.run_model(root)
        assert r.rc == 0, f"run {i} crashed:\n{r.log[-800:]}"
        parts = H.read_parts(root)
        assert sorted(p["id"] for p in parts) == ["leg", "top"], f"run {i}: {[p['id'] for p in parts]}"
        for p in parts:
            assert (root / p["step"]).is_file(), f"run {i}: STEP of {p['id']} is gone"


def test_d6_caller_returning_callee_parts_is_stable_over_repeated_unchanged_runs(tmp_path):
    root = _d6_proj(tmp_path / "p")
    _assert_stable_runs(root)


def test_d6_callee_not_called_directly_by_the_entry_script_is_stable_too(tmp_path):
    root = _d6_proj(tmp_path / "p")
    _assert_stable_runs(root, 4)
    H.edit(root / "mechanics/cad/params.py", "A=10.0", "A=12.0")
    assert H.run_model(root).rc == 0
    assert _x(root, "leg") == pytest.approx(12.0)
    _assert_stable_runs(root)


def test_d6_callee_called_both_directly_and_through_the_caller_is_stable(tmp_path):
    other = {"other": "from build123d import Box, Pos\ndef build():\n    return {'name': 'x', 'shape': Pos(9, 0, 0) * Box(1, 1, 1), 'process': 'mill'}\n"}
    root = _d6_proj(tmp_path / "p", other, "from parts import caller, other\nout = caller.build() + other.build()\n")
    for i in range(3):
        r = H.run_model(root)
        assert r.rc == 0, r.log[-800:]
        assert sorted(p["id"] for p in H.read_parts(root)) == ["leg", "top", "x"]


# ------------------------------------------------------------------ b: untracked uses of a star-imported string param
@pytest.mark.parametrize("expr", ["'steel_' + M", "'-'.join([M, 'x'])", "M", "'%s' % M", "M[:3] + M[3:]"])
def test_star_imported_string_param_used_in_a_part_field_rebuilds_the_node(tmp_path, expr):
    root = _proj(tmp_path / "p", {
        "a": f"from params import *\nfrom build123d import Box\ndef build():\n    return {{'name': 'a', 'shape': Box(1, 1, 1), 'process': 'mill', 'material': {expr}}}\n",
    }, "from parts import a\nout = a.build()\n", params="A=10.0\nM='s235abc'\n")
    assert H.run_model(root).ok().rebuilt == _u("a")
    old = H.by_id(root)["a"]["material"]
    H.edit(root / "mechanics/cad/params.py", "M='s235abc'", "M='s355xyz'")
    r = H.run_model(root).ok()
    assert r.rebuilt == _u("a"), f"{expr}: node not rebuilt after the string param changed"
    new = H.by_id(root)["a"]["material"]
    assert new != old and "xyz" in new, f"{expr}: parts.json still shows {new!r}"


# ------------------------------------------------------------------ c: two writers on one project
def test_two_model_rebuilds_started_together_both_succeed_and_leave_a_valid_model(tmp_path):
    root = _proj(tmp_path / "p", {
        "a": "import params\nfrom build123d import Box\ndef build():\n    return {'name': 'a', 'shape': Box(params.A, 1, 1), 'process': 'mill'}\n",
        "b": "from build123d import Box, Pos\ndef build():\n    return {'name': 'b', 'shape': Pos(50, 0, 0) * Box(2, 1, 1), 'process': 'mill'}\n",
    }, "from parts import a, b\nout = a.build() + b.build()\n")
    cad = root / "mechanics" / "cad"
    for rnd in range(2):
        H.edit(cad / "params.py", f"A={10.0 + rnd}", f"A={11.0 + rnd}")
        cmd = [sys.executable, str(H.CAD_INC), "--model", "assemble.py"]
        procs = [procgroup.popen(cmd, cwd=cad, env=H.env_for(root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) for _ in range(3)]
        outs = [(procgroup.finish(p)[0], p.returncode) for p in procs]
        assert all(rc == 0 for _, rc in outs), outs
        parts = H.read_parts(root)
        assert sorted(p["id"] for p in parts) == ["a", "b"]
        assert all((root / p["step"]).is_file() for p in parts)
        assert not list((root / "build/model").glob("*.tmp"))


def test_derive_and_model_rebuild_started_together_both_succeed(project):
    cad = H.cad_dir(project)
    cmd = [sys.executable, str(H.CAD_INC), "--model", "assemble.py"]
    p1 = procgroup.popen(cmd, cwd=cad, env=H.env_for(project), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    p2 = procgroup.popen([sys.executable, str(H.DERIVE), "print", str(project / "build/m.json")], cwd=project, env=H.env_for(project),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    o1, o2 = procgroup.finish(p1)[0], procgroup.finish(p2)[0]
    assert p1.returncode == 0, o1[-800:]
    assert p2.returncode == 0, o2[-800:]
    parts = H.read_parts(project)
    assert {p["id"] for p in parts} == H.PART_IDS and all((project / p["step"]).is_file() for p in parts)


def test_derive_rebuild_uses_only_its_own_interpreter(monkeypatch):
    import importlib
    sys.path.insert(0, str(Path(H.DERIVE).parent))
    src = Path(H.DERIVE).read_text()
    assert "shutil.which" not in src.split("def rebuild")[1].split("def main")[0], "derive.rebuild must not probe other interpreters"


# ------------------------------------------------------------------ review minors: lock scope, tmp GC, reused count
def test_lock_held_for_root_a_does_not_let_a_rebuild_of_root_b_skip_its_lock(tmp_path, monkeypatch):
    import fcntl
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from cad_incremental import project_lock
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    monkeypatch.setenv("CAD_MODEL_LOCK_HELD", os.path.realpath(a))
    with project_lock(str(b)):
        with open(b / "build/model/.lock", "a") as fh:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with project_lock(str(a)):                           # the held root itself still skips (no self-deadlock)
        assert not (a / "build").exists()


def test_model_gc_deletes_crashed_run_tmp_files_older_than_run_start_and_keeps_newer(tmp_path):
    root = _d6_proj(tmp_path / "p")
    assert H.run_model(root).rc == 0
    old, new = root / "build/model/parts.json.tmp-111", root / "build/model/nodes.json.tmp-222"
    old.write_text("x")
    new.write_text("x")
    past, future = time.time() - 3600, time.time() + 3600
    os.utime(old, (past, past))
    os.utime(new, (future, future))
    assert H.run_model(root).rc == 0
    assert not old.exists(), "stale tmp file of a crashed run survived the GC"
    assert new.exists(), "GC deleted a tmp file newer than the run start"


def test_d6_second_run_counts_callees_restored_through_a_reused_caller_as_reused(tmp_path):
    root = _d6_proj(tmp_path / "p")
    assert H.run_model(root).rc == 0
    r = H.run_model(root)
    assert r.rc == 0 and r.summary[:2] == (0, N_D6_UNITS), f"summary {r.summary}:\n{r.log[-600:]}"


# ------------------------------------------------------------------ B1 / M1 / m2 (second review)
def _ext_proj(tmp_path, value="10.0"):
    """A node that reads W from a module outside mechanics/cad: an input its key cannot see."""
    root = tmp_path / "p"
    ext = tmp_path / "extlib"
    ext.mkdir()
    (ext / "vendorlib.py").write_text(f"W = {value}\n")
    _proj(root, {
        "ven": f"import sys\nsys.path.insert(0, {str(ext)!r})\nimport vendorlib\nfrom build123d import Box\n"
               "def build():\n    return {'name': 'ven', 'shape': Box(vendorlib.W, 3, 3), 'process': 'print'}\n"},
        "from parts import ven\nout = ven.build()\n")
    return root, ext


def _derived_stl(root):
    d = H.run_derive(root, "print").ok()
    files = H.file_list(H.entry(d, "ven"), root)
    return b"".join(f.read_bytes() for f in files if f.suffix.lower() == ".stl")


def test_b1_node_input_the_key_cannot_see_changes_stored_step_and_derived(tmp_path):
    root, ext = _ext_proj(tmp_path)
    H.run_model(root).ok()
    assert _x(root, "ven") == pytest.approx(10.0)
    stl_before = _derived_stl(root)
    key = H.by_id(root)["ven"]["key"]
    (ext / "vendorlib.py").write_text("W = 50.0\n")
    (root / "build/model/nodes.json").unlink()
    r = H.run_model(root).ok()
    assert "ven" in r.log and "input" in r.log.lower(), f"a warning naming the node is expected:\n{r.log[-600:]}"
    assert _x(root, "ven") == pytest.approx(50.0), "the node ran: the fresh geometry must replace the stored STEP"
    assert not any(key in H.meta_part_keys(m) and True for m in (root / "build/derived").glob("*/meta.json")), \
        "derived results listing the stale STEP must be invalidated"
    assert _derived_stl(root) != stl_before, "derive must recompute from the new geometry"


def test_b1_identical_geometry_rerun_keeps_existing_step_bytes(tmp_path):
    root, ext = _ext_proj(tmp_path)
    H.run_model(root).ok()
    foreign = H.foreign_machine(root)
    (root / "build/model/nodes.json").unlink()
    r = H.run_model(root).ok()
    assert r.rebuilt == _u("ven"), r.log[-400:]
    assert {p["id"]: H.sha_file(root / p["step"]) for p in H.read_parts(root)} == foreign, "same geometry: the file must be kept as it is"
    assert "cannot see" not in r.log


def test_b1_node_with_unrecorded_input_gets_a_key_that_includes_the_step_content(tmp_path):
    """A project module built by hand (exec, no import) is never seen by the recorder: the node's record cannot be taken."""
    root = _proj(tmp_path / "p", {
        "a": "import sys, types, os\nfrom build123d import Box\n"
             "def build():\n    f = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'lib.py')\n"
             "    m = types.ModuleType('lib'); m.__file__ = f; sys.modules['lib'] = m\n"
             "    exec(compile(open(f).read(), f, 'exec'), m.__dict__)\n"
             "    import lib\n"
             "    return {'name': 'a', 'shape': Box(lib.W, 1, 1), 'process': 'mill'}\n"},
        "from parts import a\nout = a.build()\n")
    (root / "mechanics/cad/lib.py").write_text("W = 10.0\n")
    r = H.run_model(root).ok()
    assert "rebuilt on every run" in r.log, "precondition: this node must be one whose record cannot be taken\n" + r.log
    k1 = H.by_id(root)["a"]["key"]
    (root / "mechanics/cad/lib.py").write_text("W = 30.0\n")
    H.run_model(root).ok()
    assert _x(root, "a") == pytest.approx(30.0)
    assert H.by_id(root)["a"]["key"] != k1


def test_m1_unhashable_param_change_rebuilds_with_new_geometry(tmp_path):
    root = _proj(tmp_path / "p", {
        "obj": "import params\nfrom build123d import Box\ndef build():\n    return {'name': 'obj', 'shape': Box(params.OBJ.w, 3, 3), 'process': 'mill'}\n"},
        "from parts import obj\nout = obj.build()\n",
        params="class Thing:\n    w = 10.0\nOBJ = Thing()\n")
    H.run_model(root).ok()
    H.edit(root / "mechanics/cad/params.py", "w = 10.0", "w = 50.0")
    r = H.run_model(root).ok()
    assert r.rebuilt == _u("obj"), r.log[-400:]
    assert _x(root, "obj") == pytest.approx(50.0)


def test_m2_derive_no_rebuild_on_parts_json_without_key_says_rebuild_first(tmp_path):
    root = _proj(tmp_path / "p", {"a": "from build123d import Box\ndef build():\n    return {'name': 'a', 'shape': Box(1, 1, 1), 'process': 'print'}\n"},
                 "from parts import a\nout = a.build()\n")
    H.run_model(root).ok()
    pj = root / "build/model/parts.json"
    data = json.loads(pj.read_text())
    for p in data["parts"]:
        p.pop("key")
    pj.write_text(json.dumps(data))
    cp = procgroup.run([sys.executable, str(H.DERIVE), "--no-rebuild", "print", str(root / "m.json")], cwd=root, env=H.env_for(root),
                        capture_output=True, text=True)
    assert cp.returncode != 0 and "rebuild the model first" in cp.stdout + cp.stderr
    assert "KeyError" not in cp.stderr and "Traceback" not in cp.stderr


# ------------------------------------------------------------------ kit manifests keep their derived dirs alive
_KIT_NODES = {"pl": "import params\nfrom build123d import Box\ndef build():\n"
                    "    return {'name': 'pl', 'shape': Box(params.A, 5, 2), 'process': 'print', 'material': 'PLA'}\n"}
_KIT_ASM = "from parts import pl\nout = pl.build()\n"


def _kit_derive(root, scope="print"):
    return H.run_derive(root, scope, manifest=root / "build/kit" / f"{scope}.json").ok()


def _kit_dirs(root, scope="print"):
    """Every derived dir the kit manifest of `scope` points at."""
    text = (root / "build/kit" / f"{scope}.json").read_text()
    return {Path(m) for m in re.findall(r"build/derived/[0-9a-f]+", text)}


def test_kit_manifest_files_survive_an_edit_and_the_revert_recomputes_nothing(tmp_path):
    root = _proj(tmp_path / "p", _KIT_NODES, _KIT_ASM)
    first = _kit_derive(root)
    files = H.file_list(H.entry(first, "pl"), root)
    assert files and _kit_dirs(root)
    H.edit(root / "mechanics/cad/params.py", "A=10.0", "A=20.0")
    H.run_model(root).ok()                                    # the model moves on; the kit manifest is still the old one
    H.run_derive(root, "bought", manifest=root / "build/kit/bought.json").ok()   # a derive GC pass while the old kit is served
    assert all(f.exists() for f in files), "GC deleted outputs a kit manifest still references"
    H.edit(root / "mechanics/cad/params.py", "A=20.0", "A=10.0")
    H.run_model(root).ok()
    again = _kit_derive(root)
    assert again.counts[2] == 0, f"reverted model must reuse the kept outputs, counts {again.counts}"
    assert all(f.exists() for f in H.file_list(H.entry(again, "pl"), root))


def test_unreferenced_stale_derived_dir_is_still_deleted(tmp_path):
    root = _proj(tmp_path / "p", _KIT_NODES, _KIT_ASM)
    _kit_derive(root)
    stale = root / "build/derived" / ("c" * 16)
    stale.mkdir()
    (stale / "meta.json").write_text(json.dumps({"parts": ["0" * 64]}))
    (stale / "out.pdf").write_text("x")
    os.utime(stale, (time.time() - 3600,) * 2)
    os.utime(stale / "meta.json", (time.time() - 3600,) * 2)
    _kit_derive(root)
    assert not stale.exists()


def test_derived_cache_stays_bounded_across_edits(tmp_path):
    root = _proj(tmp_path / "p", _KIT_NODES, _KIT_ASM)
    _kit_derive(root)
    v1 = {p.name for p in _kit_dirs(root)}
    v1 = {Path(p).name for p in v1}
    H.edit(root / "mechanics/cad/params.py", "A=10.0", "A=20.0")
    _kit_derive(root)                                         # the manifest now points at version 2
    H.edit(root / "mechanics/cad/params.py", "A=20.0", "A=30.0")
    _kit_derive(root)
    assert not (v1 & H.derived_dirs(root)), "version 1 is neither referenced nor current: it must be collected"
