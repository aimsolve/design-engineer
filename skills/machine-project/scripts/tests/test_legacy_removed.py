"""Issue #30: the old node layout and the legacy cache modes are gone from cad_incremental.py.

Each test names the "done when" condition it serves:
  DW1  --model on a project with an old-layout node file parts/<node>.py (module-level build(), called from assemble.py) exits
       non-zero, stderr names that file and says nodes are folders parts/<node>/. Private helpers parts/_<name>.py are fine;
       a pure folder-layout project still builds.
  DW2  --per-part, --per-part-fn, --who, --map, --cache, --parts are rejected (non-zero exit, nothing runs).
  DW3  a plain run `cad_incremental.py script.py ARGS` (also with --no-cache / --full) behaves like `python script.py ARGS`:
       params importable from mechanics/cad, args in sys.argv, exit code passed through, and no build/cad_cache nor
       build/params_map*.json left under the project root. Also from the project root with a script in another folder.
  DW4  a plain run of assemble.py (no --model) exits non-zero and tells to use --model.
  DW5  a --model run deletes leftover build/cad_cache/ and build/params_map{,.keys,.parts}.json.
  DW7  a folder node's __init__.py build() returning a part WITH a shape it built itself (not a part file's part placed with
       geom.place) fails the --model build, stderr names the part and says to move it to parts/<node>/<part>.py; a node
       returning a row without a shape (bought, shape=None, qty=3) still builds.
The check.py gate side (DW6) is in ../test_check_gates.py.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import helpers as H
import partfiles as F
import procgroup


# ---------------------------------------------------------------- tiny folder-layout projects
PEG = '''
    from build123d import Box
    from geom import P
    {TICK}

    def build():
        _tick("peg")
        return P("peg", Box(4, 4, 4), "#22aa22", process="print", material="PLA")
    '''
KIT = '''
    from build123d import Pos
    from geom import P, place
    from . import peg
    {TICK}

    def build():
        _tick("node_kit")
        return [place(peg.build(), Pos(0, 0, 0)), place(peg.build(), Pos(10, 0, 0)),
                P("pipe", None, "#999999", process="bought", code="TR50", desc="Truba 50", qty=3, unit="m", kind="profile")]
    '''
ASM_KIT = """import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from parts import kit

items = kit.build()
"""


def _only(files):
    only = {k: None for k in F.FILES}
    only.update(files)
    return only


def kit_project(root, files=None, assemble=ASM_KIT):
    """One folder node parts/kit/ placing parts/kit/peg.py twice and returning a bought row (shape None, qty=3)."""
    return F.write_project(root, files=_only(dict({"kit/__init__.py": KIT, "kit/peg.py": PEG}, **(files or {}))),
                           assemble=assemble, legacy=False)


def cli(cwd, *args, root=None):
    """cad_incremental.py ARGS run from `cwd`; stdout and stderr kept apart."""
    cp = procgroup.run([sys.executable, str(H.CAD_INC), *args], cwd=cwd, env=H.env_for(root or cwd),
                       capture_output=True, text=True, timeout=300)
    return cp


def leftovers(root):
    b = Path(root) / "build"
    return sorted(str(p.relative_to(root)) for p in [b / "cad_cache", *b.glob("params_map*.json")] if p.exists())


# ================================================================ DW1 old-layout node file is refused
OLD_NODE = '''
from build123d import Box, Pos

def build():
    return [{"name": "old_block", "shape": Pos(-100, 0, 0) * Box(5, 5, 5), "color": "#00ff00", "process": "other", "qty": 2}]
'''
ASM_KIT_AND_OLD = """import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from parts import kit, oldnode

items = kit.build() + oldnode.build()
"""


def _names_folder_rule(err, node):
    return f"parts/{node}/" in err or "parts/<node>/" in err


def test_dw1_old_layout_node_next_to_folder_nodes_fails_naming_the_file(tmp_path):
    root = kit_project(tmp_path / "p", assemble=ASM_KIT_AND_OLD)
    (H.cad_dir(root) / "parts" / "oldnode.py").write_text(OLD_NODE)
    m = F.run_model(root)
    assert m.rc != 0, f"an old-layout node parts/oldnode.py must stop the --model build, it exited 0:\n{m.log[-1500:]}"
    assert "oldnode.py" in m.err, f"stderr must name the old node file parts/oldnode.py:\n{m.err[-1500:]}"
    assert _names_folder_rule(m.err, "oldnode"), f"stderr must say nodes are folders parts/<node>/:\n{m.err[-1500:]}"


def _old_layout_project(root):
    """A project in the old layout only: parts/frame.py and parts/sheet.py, each a module-level build() -> world-coordinate dicts."""
    cad = Path(root) / "mechanics" / "cad"
    (cad / "parts").mkdir(parents=True)
    (cad / "params.py").write_text("W = 10.0\n")
    (cad / "parts" / "__init__.py").write_text("")
    (cad / "parts" / "frame.py").write_text("import params\nfrom build123d import Box\n\ndef build():\n"
                                           "    return [{'name': 'frame', 'shape': Box(params.W, 8, 2), 'process': 'mill'}]\n")
    (cad / "parts" / "sheet.py").write_text("from build123d import Box, Pos\n\ndef build():\n"
                                           "    return [{'name': 'sheet', 'shape': Pos(20, 0, 0) * Box(30, 20, 2), 'process': 'laser',"
                                           " 'material': 'S235JR', 'thickness': 2.0, 'qty': 2}]\n")
    (cad / "assemble.py").write_text("import os, sys\nsys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
                                     "from parts import frame, sheet\nout = frame.build() + sheet.build()\n")
    return Path(root)


def test_dw1_project_made_only_of_old_layout_nodes_fails(tmp_path):
    root = _old_layout_project(tmp_path / "old")
    r = procgroup.run([sys.executable, str(H.CAD_INC), "--model", "assemble.py"], cwd=H.cad_dir(root), env=H.env_for(root),
                      capture_output=True, text=True, timeout=300)
    assert r.returncode != 0, f"an old-layout project must not build:\n{(r.stdout + r.stderr)[-1500:]}"
    assert re.search(r"(frame|sheet)\.py", r.stderr), f"stderr must name an old node file:\n{r.stderr[-1500:]}"
    assert "parts/<node>/" in r.stderr or re.search(r"parts/\w+/", r.stderr), \
        f"stderr must say nodes are folders parts/<node>/:\n{r.stderr[-1500:]}"


HELPER_USER = '''
    from build123d import Box
    from geom import P
    from parts import _shared
    {TICK}

    def build():
        _tick("peg")
        return P("peg", Box(_shared.SIDE, 4, 4), "#22aa22", process="print", material="PLA")
    '''


def test_dw1_private_helper_module_in_parts_is_allowed_and_folder_project_builds(tmp_path):
    """Regression guard: parts/_shared.py (a helper, no build) used by a part file; the folder project builds as before."""
    root = kit_project(tmp_path / "p", files={"kit/peg.py": HELPER_USER})
    (H.cad_dir(root) / "parts" / "_shared.py").write_text("SIDE = 6.0\n\ndef build():\n    return []\n")
    m = F.run_model(root).ok()
    ps = F.parts(root)
    assert set(ps) == {"peg", "pipe"}, sorted(ps)
    assert ps["peg"]["qty"] == 2 and ps["pipe"]["qty"] == 3
    assert H.step_bbox(root / ps["peg"]["step"]).size.X == pytest.approx(6.0, abs=1e-6)
    assert "_shared.py" not in m.err


# ================================================================ DW2 legacy flags are rejected
SPY = """import os, sys
from pathlib import Path
Path(os.environ["TEST_COUNTER_DIR"]).mkdir(parents=True, exist_ok=True)
Path(os.environ["TEST_COUNTER_DIR"], "spy_ran").write_text("1")
"""


@pytest.mark.parametrize("flag", ["--per-part", "--per-part-fn", "--who", "--map", "--cache", "--parts"])
def test_dw2_removed_flag_is_an_argument_error(tmp_path, flag):
    cad = tmp_path / "p" / "mechanics" / "cad"
    cad.mkdir(parents=True)
    (cad / "params.py").write_text("X = 1\n")
    (cad / "spy.py").write_text(SPY)
    cp = cli(cad, flag, "x", "spy.py", root=tmp_path / "p")
    assert cp.returncode != 0, f"{flag} must be rejected, it exited 0:\n{(cp.stdout + cp.stderr)[-800:]}"
    assert flag in cp.stderr, f"the error must name {flag}:\n{cp.stderr[-800:]}"
    assert not (tmp_path / "p" / "counter" / "spy_ran").exists(), f"{flag}: the script ran although the flag is gone"
    assert leftovers(tmp_path / "p") == []


# ================================================================ DW3 a plain run is `python script.py ARGS`
ECHO = """import json, os, sys
from pathlib import Path
import params
Path(os.environ["TEST_COUNTER_DIR"]).mkdir(parents=True, exist_ok=True)
Path(os.environ["TEST_COUNTER_DIR"], "seen.json").write_text(json.dumps({"argv": sys.argv, "x": params.X}))
sys.exit(int(sys.argv[2]))
"""


def _cad_project(root):
    cad = Path(root) / "mechanics" / "cad"
    cad.mkdir(parents=True)
    (cad / "params.py").write_text("X = 42\n")
    (cad / "echo.py").write_text(ECHO)
    return Path(root)


@pytest.mark.parametrize("flags", [[], ["--no-cache"], ["--full"]], ids=["plain", "no-cache", "full"])
def test_dw3_plain_run_is_python_script_args_and_leaves_no_cache(tmp_path, flags):
    root = _cad_project(tmp_path / "p")
    cp = cli(H.cad_dir(root), *flags, "echo.py", "--all", "7", root=root)
    assert cp.returncode == 7, f"the script's exit code (7) must pass through, got {cp.returncode}:\n{(cp.stdout + cp.stderr)[-800:]}"
    seen = json.loads((root / "counter" / "seen.json").read_text())
    assert Path(seen["argv"][0]).name == "echo.py" and seen["argv"][1:] == ["--all", "7"], seen["argv"]
    assert seen["x"] == 42, "the script must import params from mechanics/cad"
    assert leftovers(root) == [], f"a plain run left the legacy cache / params map behind: {leftovers(root)}"


SIM = """import json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mechanics" / "cad"))
import params
Path(os.environ["TEST_COUNTER_DIR"]).mkdir(parents=True, exist_ok=True)
Path(os.environ["TEST_COUNTER_DIR"], "sim.json").write_text(json.dumps({"argv": sys.argv[1:], "x": params.X}))
sys.exit(5)
"""


def test_dw3_plain_run_from_project_root_of_a_script_in_another_folder(tmp_path):
    root = _cad_project(tmp_path / "p")
    (root / "sim").mkdir()
    (root / "sim" / "x.py").write_text(SIM)
    cp = cli(root, "sim/x.py", "a", root=root)
    assert cp.returncode == 5, f"exit code 5 must pass through, got {cp.returncode}:\n{(cp.stdout + cp.stderr)[-800:]}"
    seen = json.loads((root / "counter" / "sim.json").read_text())
    assert seen == {"argv": ["a"], "x": 42}, seen
    assert leftovers(root) == [], f"a plain run left the legacy cache / params map behind: {leftovers(root)}"


# ================================================================ DW4 assemble.py without --model is refused
def test_dw4_plain_run_of_assemble_py_tells_to_use_model(tmp_path):
    root = kit_project(tmp_path / "p")
    cp = cli(H.cad_dir(root), "assemble.py", root=root)
    assert cp.returncode != 0, f"a plain run of assemble.py stores nothing and must fail:\n{(cp.stdout + cp.stderr)[-800:]}"
    assert "--model" in cp.stderr, f"stderr must tell to use --model:\n{cp.stderr[-800:]}"
    assert not (root / "build" / "model" / "parts.json").exists()
    assert leftovers(root) == []


# ================================================================ DW5 --model removes legacy leftovers
def test_dw5_model_run_deletes_legacy_cache_and_params_maps(tmp_path):
    root = kit_project(tmp_path / "p")
    b = root / "build"
    (b / "cad_cache" / "ops").mkdir(parents=True)
    (b / "cad_cache" / "ops" / "junk.bin").write_bytes(b"0" * 100)
    for n in ("params_map.json", "params_map.keys.json", "params_map.parts.json"):
        (b / n).write_text("{}")
    F.run_model(root).ok()
    assert leftovers(root) == [], f"--model must delete the legacy leftovers: {leftovers(root)}"


# ================================================================ DW7 a node builds no shaped part itself
SELF_BUILT = '''
    from build123d import Box, Pos
    from geom import P, place
    from . import peg

    def build():
        return [place(peg.build(), Pos(0, 0, 0)),
                P("lid", Box(20, 20, 2), "#336699", process="laser", material="S235JR", thickness=2)]
    '''


def test_dw7_node_returning_a_shaped_part_it_built_itself_fails_naming_the_part(tmp_path):
    root = kit_project(tmp_path / "p", files={"kit/__init__.py": SELF_BUILT})
    m = F.run_model(root)
    assert m.rc != 0, f"a shaped part built in parts/kit/__init__.py must stop the build, it exited 0:\n{m.log[-1500:]}"
    assert "lid" in m.err, f"stderr must name the part 'lid':\n{m.err[-1500:]}"
    assert re.search(r"parts/(kit|<node>)/(lid|<part>)\.py", m.err), \
        f"stderr must say to move it to its own file parts/<node>/<part>.py:\n{m.err[-1500:]}"


def test_dw7_node_returning_a_row_without_shape_still_builds(tmp_path):
    """Regression guard: the bought row P('pipe', None, ..., qty=3) returned by the node without place() is fine."""
    root = kit_project(tmp_path / "p")
    F.run_model(root).ok()
    pipe = F.parts(root)["pipe"]
    assert pipe["process"] == "bought" and pipe["qty"] == 3 and "step" not in pipe


# ---------------------------------------------------------------- DW7 review: a single dict, and a cache from the previous version
LID_SINGLE = '''
    from build123d import Box
    from geom import P


    def _lid():
        return P("lid", Box(20, 20, 2), "#336699", process="laser", material="S235JR", thickness=2)


    def build():
        return _lid()
    '''


def test_dw7_node_returning_a_single_shaped_dict_it_built_itself_fails_naming_the_part(tmp_path):
    """Not a list: parts/kit/__init__.py build() returns ONE shaped part dict made by a helper of the node."""
    root = kit_project(tmp_path / "p", files={"kit/__init__.py": LID_SINGLE, "kit/peg.py": None})
    m = F.run_model(root)
    assert m.rc != 0, f"a single shaped dict built in parts/kit/__init__.py must stop the build, it exited 0:\n{m.log[-1500:]}"
    assert "lid" in m.err, f"stderr must name the part 'lid':\n{m.err[-1500:]}"
    assert re.search(r"parts/(kit|<node>)/(lid|<part>)\.py", m.err), \
        f"stderr must say to move it to its own file parts/<node>/<part>.py:\n{m.err[-1500:]}"


BASE_COMMIT = "fd872c6"      # the framework before #30: it stored a node's own shaped part in build/model/nodes.json
SUB_NODE = '''
    from build123d import Box


    def build():
        return [{"name": "blk", "shape": Box(7, 7, 7), "color": "#777777", "process": "other"}]
    '''
TOP_NODE = '''
    from . import sub


    def build():
        return sub.build()
    '''
ASM_TOP = """import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from parts import top

items = top.build()
"""


def _base_scripts(tmp_path):
    """The machine-project scripts (plus step-import, which they import) as they were at BASE_COMMIT, in a temp dir."""
    cp = subprocess.run(["git", "-C", str(H.SCRIPTS), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if cp.returncode != 0:
        pytest.skip(f"not a git checkout: the base framework ({BASE_COMMIT}) cannot be read from git")
    repo = Path(cp.stdout.strip())
    if subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{BASE_COMMIT}^{{commit}}"], capture_output=True).returncode != 0:
        pytest.skip(f"commit {BASE_COMMIT} (the base framework) is not in this checkout (shallow clone?)")
    rel = H.SCRIPTS.parent.parent.relative_to(repo)          # agent/skills
    arch = subprocess.run(["git", "-C", str(repo), "archive", BASE_COMMIT, f"{rel}/machine-project", f"{rel}/step-import"],
                          capture_output=True, check=True)
    out = tmp_path / "base"
    out.mkdir()
    subprocess.run(["tar", "-x", "-C", str(out)], input=arch.stdout, check=True)
    return out / rel / "machine-project" / "scripts"


def test_dw7_upgrade_from_a_previous_version_cache_with_a_node_own_shaped_part_fails_naming_it(tmp_path):
    """The previous version accepted top -> sub where sub's __init__ builds its own shaped part 'blk', and stored it in
    build/model/nodes.json (sub as a non-part record with an own part, top's ret pointing at it). The current version must not
    reuse that record silently (exit 0 with 'blk' gone from parts.json): it must fail naming 'blk'."""
    root = F.write_project(tmp_path / "p", files=_only({"top/__init__.py": TOP_NODE, "top/sub/__init__.py": SUB_NODE}),
                           assemble=ASM_TOP, legacy=False)
    base = _base_scripts(tmp_path)
    cp = procgroup.run([sys.executable, str(base / "cad_incremental.py"), "--model", "assemble.py"], cwd=H.cad_dir(root),
                       env=H.env_for(root), capture_output=True, text=True, timeout=300)
    assert cp.returncode == 0, f"precondition: the base version builds this project:\n{(cp.stdout + cp.stderr)[-1500:]}"
    assert "blk" in {p["id"] for p in H.read_parts(root)}, "precondition: the base version stored 'blk'"
    assert "blk" in (root / "build/model/nodes.json").read_text(), "precondition: nodes.json from the base version records 'blk'"

    m = F.run_model(root)
    assert m.rc != 0, (f"a cache holding a node's own shaped part must not be reused silently; exit 0 and parts.json now has "
                       f"{sorted(p['id'] for p in H.read_parts(root)) if (root / 'build/model/parts.json').exists() else None}:\n"
                       f"{m.log[-1500:]}")
    assert "blk" in m.err, f"stderr must name the part 'blk':\n{m.err[-1500:]}"


def test_dw1_folder_model_built_by_base_is_reused_unchanged(tmp_path):
    """Upgrade path for a migrated project: a folder-layout model built by the base framework is reused as it is by the current
    one (nothing rebuilt, no part or node code runs, parts.json byte-identical)."""
    root = F.write_project(tmp_path / "p", legacy=False)
    base = _base_scripts(tmp_path)
    cp = procgroup.run([sys.executable, str(base / "cad_incremental.py"), "--model", "assemble.py"], cwd=H.cad_dir(root),
                       env=H.env_for(root), capture_output=True, text=True, timeout=300)
    assert cp.returncode == 0, f"precondition: the base version builds the folder project:\n{(cp.stdout + cp.stderr)[-1500:]}"
    pj = root / "build/model/parts.json"
    before = pj.read_bytes()
    H.clear_ticks(root)

    m = F.run_model(root).ok()
    assert "nodes rebuilt 0" in m.log, f"the current version rebuilt units of a model the base version built:\n{m.log[-1500:]}"
    assert H.ticks(root) == [], f"part or node code ran on the upgrade run: {H.ticks(root)}"
    assert pj.read_bytes() == before, "build/model/parts.json changed when the current version reused the base version's model"
