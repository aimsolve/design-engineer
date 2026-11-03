"""Issues #18 / #48: a node may keep its own parameters next to its parts (mechanics/cad/parts/<node>/params.py), and a part
file that only asks whether a key is IN a params dict does not depend on the rest of the dict.

Done-when map:
  DW4a  parts/<node>/params.py is a params module, not a project module: changing P.X rebuilds only the part files reading X,
        changing P.Y only those reading Y
  DW4b  a name the node's params module does not define falls through to the machine's mechanics/cad/params.py and is recorded
        there: changing it rebuilds the readers, changing an unrelated machine param rebuilds nothing
  DW6   a part file whose only use of a params dict is `"x" in params.D` is not rebuilt when an unrelated key changes, and IS
        rebuilt when "x" is added to or removed from D

Everything is written with helpers.py's harness (write_base / write_node / run_model / ticks / the `[cad_incremental] rebuilt:`
line). Each done-when condition gets a project of its own, so one missing behaviour cannot hide another.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import helpers as H

PREVIOUS = "18713d6"        # main before #18: the last commit whose cad_incremental.py wrote cache VERSION 2

PARAMS = """GLOBAL_W = 10.0
UNUSED = 99.0
PLATE_T = 2.0
D = {"x": 1.0, "y": 2.0}
"""

NODE_PARAMS = """X = 5.0
Y = 7.0
"""

# a part file reading the node's own params module
NP_PART = '''
from build123d import Box
from . import params as P
{TICK}
def build():
    _tick("@PID@")
    return {"name": "@PID@", "shape": Box(P.@NAME@, 5, 2), "color": "#888888", "process": "mill"}
'''

# a part file whose only use of a params dict is a membership test
DICT_PART = '''
import params
from build123d import Box
{TICK}
def build():
    _tick("d_in")
    t = 3.0 if "x" in params.D else 1.0
    return {"name": "d_in", "shape": Box(5, 5, t), "color": "#00ff00", "process": "mill"}
'''

ASSEMBLE = '''import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parts import @MODS@
out = []
@CALLS@
for p in out:
    p["name"], p["process"]
'''


def _np_part(pid, name):
    return NP_PART.replace("@PID@", pid).replace("@NAME@", name)


def _assemble(root, nodes):
    (H.cad_dir(root) / "assemble.py").write_text(
        ASSEMBLE.replace("@MODS@", ", ".join(nodes)).replace("@CALLS@", "\n".join(f"out += {n}.build()" for n in nodes)))


def _np_project(root):
    """DW4a: node `nodeparams` with its own params.py (X, Y); q_x.py reads P.X, q_y.py reads P.Y."""
    root = Path(root)
    H.write_base(root, PARAMS)
    H.write_node(root, "nodeparams", {"q_x": _np_part("q_x", "X"), "q_y": _np_part("q_y", "Y")},
                 [("q_x", (0, 0, 0)), ("q_y", (30, 0, 0))])
    (H.cad_dir(root) / "parts" / "nodeparams" / "params.py").write_text(NODE_PARAMS)
    _assemble(root, ["nodeparams"])
    return root


def _fall_project(root):
    """DW4b: node `fall` whose params.py does NOT define GLOBAL_W; its part file reads P.GLOBAL_W anyway."""
    root = Path(root)
    H.write_base(root, PARAMS)
    H.write_node(root, "fall", {"q_g": _np_part("q_g", "GLOBAL_W")}, [("q_g", (0, 0, 0))])
    (H.cad_dir(root) / "parts" / "fall" / "params.py").write_text("OTHER = 1.0\n")
    _assemble(root, ["fall"])
    return root


def _dict_project(root):
    """DW6: node `dicts` with one part file that only asks `"x" in params.D`."""
    root = Path(root)
    H.write_base(root, PARAMS)
    H.write_node(root, "dicts", {"d_in": DICT_PART}, [("d_in", (0, 0, 0))])
    _assemble(root, ["dicts"])
    return root


def _copy(src, dst):
    shutil.copytree(src, dst)                    # copy2: source mtimes are preserved, which the incremental build keys on
    for p in Path(dst).rglob("__pycache__"):
        shutil.rmtree(p, ignore_errors=True)
    shutil.rmtree(Path(dst) / "counter", ignore_errors=True)
    return dst


@pytest.fixture(scope="session")
def np_base(tmp_path_factory):
    root = _np_project(tmp_path_factory.mktemp("nodeparams") / "proj")
    return root, H.run_model(root)


@pytest.fixture(scope="session")
def dict_base(tmp_path_factory):
    root = _dict_project(tmp_path_factory.mktemp("dicts") / "proj")
    return root, H.run_model(root)


@pytest.fixture
def np_proj(np_base, tmp_path):
    root, cold = np_base
    return _copy(root, tmp_path / "proj"), cold


@pytest.fixture
def dict_proj(dict_base, tmp_path):
    root, cold = dict_base
    return _copy(root, tmp_path / "proj"), cold


def _pu(node, pid):
    return f"{node}.{pid}.build"


def _nu(node):
    return f"{node}.build"


def _size(root, pid):
    return H.step_bbox(Path(root) / H.by_id(root)[pid]["step"]).size


def _node_params(root, node):
    return H.cad_dir(root) / "parts" / node / "params.py"


# ================================================================ DW4a: the node's own params module
def test_a_param_of_the_nodes_own_params_module_rebuilds_only_the_parts_that_read_it(np_proj):
    """DW4a: parts/nodeparams/params.py is a params module, so X belongs to q_x.py alone - a project module would rebuild both
    part files, because both import it."""
    root, cold = np_proj
    cold.ok()
    assert _size(root, "q_x").X == pytest.approx(5.0, abs=1e-6), "cold: q_x is built from P.X"
    H.edit(_node_params(root, "nodeparams"), "X = 5.0", "X = 9.0")
    r = H.run_model(root).ok()
    assert [t for t in H.ticks(root) if t in ("q_x", "q_y")] == ["q_x"], \
        f"only q_x.py reads P.X, these part files rebuilt: {H.ticks(root)}"
    assert {_pu("nodeparams", "q_x"), _nu("nodeparams")} <= r.rebuilt, r.rebuilt
    assert _pu("nodeparams", "q_y") not in r.rebuilt, r.rebuilt
    assert _size(root, "q_x").X == pytest.approx(9.0, abs=1e-6)
    assert _size(root, "q_y").X == pytest.approx(7.0, abs=1e-6), "q_y must be the reused one"


def test_another_name_of_the_nodes_params_module_rebuilds_only_its_own_part(np_proj):
    """DW4a, the other way round: Y belongs to q_y.py alone."""
    root, cold = np_proj
    cold.ok()
    H.edit(_node_params(root, "nodeparams"), "Y = 7.0", "Y = 11.0")
    r = H.run_model(root).ok()
    assert [t for t in H.ticks(root) if t in ("q_x", "q_y")] == ["q_y"], \
        f"only q_y.py reads P.Y, these part files rebuilt: {H.ticks(root)}"
    assert {_pu("nodeparams", "q_y"), _nu("nodeparams")} <= r.rebuilt, r.rebuilt
    assert _pu("nodeparams", "q_x") not in r.rebuilt, r.rebuilt
    assert _size(root, "q_y").X == pytest.approx(11.0, abs=1e-6)


def test_rewriting_a_formula_in_the_nodes_params_without_changing_its_value_rebuilds_nothing(np_proj):
    """DW4a: parts/<node>/params.py is a params module, so what counts is the VALUE each name has, not the file's text:
    rewriting X = 5.0 as X = 2.5 * 2 changes nothing any part read."""
    root, cold = np_proj
    cold.ok()
    H.edit(_node_params(root, "nodeparams"), "X = 5.0", "X = 2.5 * 2")
    r = H.run_model(root).ok()
    assert r.summary[0] == 0 and H.ticks(root) == [], \
        f"X still equals 5.0, nothing may rebuild: nodes rebuilt {r.summary[0]}, {sorted(r.rebuilt)}"


# ================================================================ DW4b: fall-through to the machine's params.py
def test_a_name_the_nodes_params_does_not_define_falls_through_to_the_machine_params(tmp_path):
    """DW4b: parts/fall/params.py defines only OTHER; q_g.py reads P.GLOBAL_W, which must resolve to mechanics/cad/params.py
    and be recorded there - an unrelated machine param rebuilds nothing, GLOBAL_W rebuilds q_g."""
    root = _fall_project(tmp_path / "proj")
    cold = H.run_model(root)
    assert cold.rc == 0 and cold.summary, (
        "a name the node's params module does not define must fall through to mechanics/cad/params.py (P.GLOBAL_W):\n"
        f"{cold.log[-1500:]}")
    assert _size(root, "q_g").X == pytest.approx(10.0, abs=1e-6), "q_g must be built from the machine's GLOBAL_W"

    H.clear_ticks(root)
    H.edit(H.cad_dir(root) / "params.py", "UNUSED = 99.0", "UNUSED = 98.0")
    r = H.run_model(root).ok()
    assert H.ticks(root) == [] and r.summary[0] == 0, \
        f"a machine param nothing reads must rebuild nothing, rebuilt {r.rebuilt or r.summary}"

    H.clear_ticks(root)
    H.edit(H.cad_dir(root) / "params.py", "GLOBAL_W = 10.0", "GLOBAL_W = 14.0")
    r = H.run_model(root).ok()
    assert "q_g" in H.ticks(root), f"GLOBAL_W is the value q_g.py read through its node's params: {H.ticks(root)}"
    assert {_pu("fall", "q_g"), _nu("fall")} <= r.rebuilt, r.rebuilt
    assert _size(root, "q_g").X == pytest.approx(14.0, abs=1e-6)


# ================================================================ DW6: `key in <params dict>`
def test_a_membership_test_does_not_depend_on_the_rest_of_the_dict(dict_proj):
    """DW6: d_in.py only asks whether "x" is in params.D; another key's value, and another key appearing, leave it alone."""
    root, cold = dict_proj
    cold.ok()
    assert _size(root, "d_in").Z == pytest.approx(3.0, abs=1e-6), 'cold: "x" is in D'
    H.edit(H.cad_dir(root) / "params.py", '"y": 2.0', '"y": 9.0')
    r = H.run_model(root).ok()
    assert H.ticks(root) == [] and r.summary[0] == 0, \
        f'`"x" in params.D` must not depend on D["y"], rebuilt {r.rebuilt or r.summary}'

    H.clear_ticks(root)
    H.edit(H.cad_dir(root) / "params.py", '"y": 9.0', '"y": 9.0, "z": 5.0')
    r = H.run_model(root).ok()
    assert H.ticks(root) == [] and r.summary[0] == 0, \
        f'`"x" in params.D` must not depend on an unrelated key appearing in D, rebuilt {r.rebuilt or r.summary}'


def test_adding_or_removing_the_tested_key_rebuilds_the_part(dict_proj):
    """DW6, the other side: the membership of "x" itself is the dependency."""
    root, cold = dict_proj
    cold.ok()
    H.edit(H.cad_dir(root) / "params.py", 'D = {"x": 1.0, ', 'D = {')
    r = H.run_model(root).ok()
    assert "d_in" in H.ticks(root), f'removing "x" from D must rebuild d_in.py: {H.ticks(root)}'
    assert {_pu("dicts", "d_in"), _nu("dicts")} <= r.rebuilt, r.rebuilt
    assert _size(root, "d_in").Z == pytest.approx(1.0, abs=1e-6), '"x" is gone from D'

    H.clear_ticks(root)
    H.edit(H.cad_dir(root) / "params.py", 'D = {', 'D = {"x": 1.0, ')
    r = H.run_model(root).ok()
    assert "d_in" in H.ticks(root), f'adding "x" back to D must rebuild d_in.py: {H.ticks(root)}'
    assert _size(root, "d_in").Z == pytest.approx(3.0, abs=1e-6)


# ================================================================ a node params module that starts defining a name
SHADOW_PART = '''
from build123d import Box
from . import params as P
{TICK}
def build():
    _tick("q_s")
    return {"name": "q_s", "shape": Box(P.X, 1, 1), "color": "#888888", "process": "mill"}
'''


def test_a_name_the_node_params_starts_defining_rebuilds_the_part_with_the_nodes_value(tmp_path):
    """DW4b: the machine has X = 5.0, parts/shadow/params.py only Y, so q_s.py's P.X falls through to 5.0. When the node's
    params.py starts defining X = 9.0, P.X is the node's 9.0 now: q_s must rebuild with it."""
    root = Path(tmp_path / "proj")
    H.write_base(root, "X = 5.0\n")
    H.write_node(root, "shadow", {"q_s": SHADOW_PART})
    _node_params(root, "shadow").write_text("Y = 1.0\n")
    _assemble(root, ["shadow"])
    cold = H.run_model(root).ok()
    assert _size(root, "q_s").X == pytest.approx(5.0, abs=1e-6), f"cold: P.X falls through to the machine's 5.0 ({cold.summary})"
    H.clear_ticks(root)
    _node_params(root, "shadow").write_text("Y = 1.0\nX = 9.0\n")
    r = H.run_model(root).ok()
    assert "q_s" in H.ticks(root), f"the node's params.py now defines X: q_s.py must rebuild, rebuilt {sorted(r.rebuilt)}"
    assert _size(root, "q_s").X == pytest.approx(9.0, abs=1e-6), "q_s must be built from the node's X = 9.0"


# ================================================================ a cache written by the previous version
def _previous_script(dst):
    """cad_incremental.py as it was before #18 (cache VERSION 2, the one live projects' caches were written by), with procgroup.py
    next to it. Pinned to a commit, not to origin/main, which holds this very version once #18 is merged. Outside a git checkout
    (a frozen copy of the tree), CAD_INC_PREVIOUS names a saved copy of it."""
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copy2(H.SCRIPTS / "procgroup.py", dst / "procgroup.py")
    if os.environ.get("CAD_INC_PREVIOUS"):
        shutil.copy2(os.environ["CAD_INC_PREVIOUS"], dst / "cad_incremental.py")
        return dst / "cad_incremental.py"
    cp = subprocess.run(["git", "-C", str(H.SCRIPTS), "show", f"{PREVIOUS}:agent/skills/machine-project/scripts/cad_incremental.py"],
                        capture_output=True, text=True)
    assert cp.returncode == 0, ("need the previous cad_incremental.py: run inside the git checkout, or set CAD_INC_PREVIOUS to a "
                                f"saved copy of it\n{cp.stderr}")
    (dst / "cad_incremental.py").write_text(cp.stdout)
    return dst / "cad_incremental.py"


def test_a_cache_from_the_previous_version_does_not_leave_a_node_rebuilt_on_every_run(tmp_path, monkeypatch):
    """A live project already has parts/<node>/params.py and a cache the previous cad_incremental.py wrote. After switching to
    this one and touching the node's __init__.py, the first run rebuilds; the second must rebuild nothing and must not warn that a
    unit is rebuilt on every run."""
    root = _np_project(tmp_path / "proj")
    monkeypatch.setattr(H, "CAD_INC", _previous_script(tmp_path / "previous"))
    H.run_model(root).ok()
    monkeypatch.undo()
    H.edit(H.cad_dir(root) / "parts" / "nodeparams" / "__init__.py", "def build():", "# touched\ndef build():")
    H.run_model(root).ok()
    H.clear_ticks(root)
    r = H.run_model(root).ok()
    assert "rebuilt on every run" not in r.log, [ln for ln in r.log.splitlines() if "every run" in ln]
    assert r.summary[0] == 0 and H.ticks(root) == [], \
        f"the second run after the upgrade must rebuild nothing: nodes rebuilt {r.summary[0]}, {sorted(r.rebuilt)}"


# ================================================================ a params function with a lambda / a generator expression
LAMBDA_PARAMS = """PITCH = 2.0


def holes(n):
    return sorted((i * PITCH for i in range(n)), key=lambda x: -x)
"""

LAMBDA_PART = '''
import params
from build123d import Box
{TICK}
def build():
    _tick("h_part")
    h = params.holes(3)
    return {"name": "h_part", "shape": Box(5, 5, 1.0 + h[0]), "color": "#888888", "process": "mill"}
'''


def test_a_params_function_with_a_lambda_and_a_generator_builds_and_follows_its_param(tmp_path):
    """The machine's params.py defines holes(n) with a generator expression and a lambda; a part calls params.holes(3) and is
    1 + the largest hole high. The build must work, a no-change run rebuild nothing, and PITCH rebuild the part."""
    root = Path(tmp_path / "proj")
    H.write_base(root, LAMBDA_PARAMS)
    H.write_node(root, "holes", {"h_part": LAMBDA_PART})
    _assemble(root, ["holes"])
    cold = H.run_model(root)
    assert cold.rc == 0 and cold.summary, f"a params function with a lambda / a generator must build:\n{cold.log[-1500:]}"
    assert _size(root, "h_part").Z == pytest.approx(5.0, abs=1e-6), "cold: 1 + 2 * 2"
    H.clear_ticks(root)
    r = H.run_model(root).ok()
    assert r.summary[0] == 0 and H.ticks(root) == [], f"a no-change run must rebuild nothing: {sorted(r.rebuilt)}"
    H.edit(H.cad_dir(root) / "params.py", "PITCH = 2.0", "PITCH = 3.0")
    r = H.run_model(root).ok()
    assert "h_part" in H.ticks(root), f"holes() reads PITCH: h_part must rebuild, rebuilt {sorted(r.rebuilt)}"
    assert _size(root, "h_part").Z == pytest.approx(7.0, abs=1e-6), "1 + 2 * 3"


# ================================================================ getattr with a default, the name defined nowhere yet
GETATTR_PART = '''
from build123d import Box
from . import params as P
{TICK}
def build():
    _tick("g_part")
    return {"name": "g_part", "shape": Box(getattr(P, "Z", 1.0), 5, 2), "color": "#888888", "process": "mill"}
'''


def test_getattr_with_a_default_follows_a_name_the_machine_params_starts_defining(tmp_path):
    """g_part.py does getattr(P, "Z", 1.0) on its node's params module; neither that nor the machine's params.py defines Z, so
    it is 1.0 wide. When the MACHINE's params.py starts defining Z = 4.0, the fall-through finds it: g_part must rebuild at 4.0."""
    root = Path(tmp_path / "proj")
    H.write_base(root, "X = 5.0\n")
    H.write_node(root, "gz", {"g_part": GETATTR_PART})
    _node_params(root, "gz").write_text("Y = 1.0\n")
    _assemble(root, ["gz"])
    H.run_model(root).ok()
    assert _size(root, "g_part").X == pytest.approx(1.0, abs=1e-6), "cold: Z defined nowhere, the default 1.0"
    H.clear_ticks(root)
    H.edit(H.cad_dir(root) / "params.py", "X = 5.0\n", "X = 5.0\nZ = 4.0\n")
    r = H.run_model(root).ok()
    assert "g_part" in H.ticks(root), f"the machine's params.py now defines Z: g_part must rebuild, rebuilt {sorted(r.rebuilt)}"
    assert _size(root, "g_part").X == pytest.approx(4.0, abs=1e-6)


# ================================================================ a node's params.py importing another module
def test_a_nodes_params_importing_another_nodes_params_keeps_both_nodes_in_the_model(tmp_path):
    """parts/aa/params.py does `from parts.bb.params import BW` and AW = BW * 2; parts/bb/params.py has BW = 3.0. aa/a_w.py
    reads P.AW, bb/b_w.py reads P.BW. Both parts are in the model, and editing BW rebuilds both with the right sizes."""
    root = Path(tmp_path / "proj")
    H.write_base(root, "UNUSED = 1.0\n")
    H.write_node(root, "aa", {"a_w": _np_part("a_w", "AW")})
    H.write_node(root, "bb", {"b_w": _np_part("b_w", "BW")})
    _node_params(root, "bb").write_text("BW = 3.0\n")
    _node_params(root, "aa").write_text("from parts.bb.params import BW\n\nAW = BW * 2\n")
    _assemble(root, ["aa", "bb"])
    H.run_model(root).ok()
    ids = {p["id"] for p in H.read_parts(root)}
    assert {"a_w", "b_w"} <= ids, f"both nodes' parts must be in build/model/parts.json, got {sorted(ids)}"
    assert (_size(root, "a_w").X, _size(root, "b_w").X) == pytest.approx((6.0, 3.0), abs=1e-6)
    H.clear_ticks(root)
    H.edit(_node_params(root, "bb"), "BW = 3.0", "BW = 5.0")
    r = H.run_model(root).ok()
    assert {"a_w", "b_w"} <= set(H.ticks(root)), f"BW feeds both parts: both must rebuild, ran {H.ticks(root)} ({sorted(r.rebuilt)})"
    assert {p["id"] for p in H.read_parts(root)} >= {"a_w", "b_w"}, "both parts must still be in the model"
    assert (_size(root, "a_w").X, _size(root, "b_w").X) == pytest.approx((10.0, 5.0), abs=1e-6)


STD_PART = '''
from build123d import Box
from parts._std import PITCH
{TICK}
def build():
    _tick("b_std")
    return {"name": "b_std", "shape": Box(PITCH, 5, 2), "color": "#888888", "process": "mill"}
'''


def test_a_helper_first_imported_by_a_nodes_params_does_not_make_its_users_rebuild_on_every_run(tmp_path):
    """parts/ss/params.py does `from parts._std import PITCH` (SW = PITCH * 2) and is imported - by a_par.py, first in the node
    - before any part imports parts/_std.py itself; b_std.py then takes PITCH from parts/_std.py directly. Neither may be rebuilt
    on every run: the second no-change run rebuilds nothing and nobody warns."""
    root = Path(tmp_path / "proj")
    cad = H.write_base(root, "UNUSED = 1.0\n")
    (cad / "parts" / "_std.py").write_text("PITCH = 5.0\n")
    H.write_node(root, "ss", {"a_par": _np_part("a_par", "SW"), "b_std": STD_PART})
    _node_params(root, "ss").write_text("from parts._std import PITCH\n\nSW = PITCH * 2\n")
    _assemble(root, ["ss"])
    H.run_model(root).ok()
    assert (_size(root, "a_par").X, _size(root, "b_std").X) == pytest.approx((10.0, 5.0), abs=1e-6)
    H.clear_ticks(root)
    r1 = H.run_model(root).ok()
    H.clear_ticks(root)
    r2 = H.run_model(root).ok()
    warned = [ln for r in (r1, r2) for ln in r.log.splitlines() if "every run" in ln]
    assert not warned, warned
    assert r2.summary[0] == 0 and H.ticks(root) == [], \
        f"the second no-change run must rebuild nothing: nodes rebuilt {r2.summary[0]}, {sorted(r2.rebuilt)}"


# ================================================================ a helper reached only through a node's params.py
GEOM_HELPER = "XS = [10.0, 20.0]\n\n\ndef span(i):\n    return XS[i]\n"

WIDTH_PART = '''
from build123d import Box
from . import params as P
{TICK}
def build():
    _tick("q_w")
    return {"name": "q_w", "shape": Box(P.width(), 5, 2), "color": "#888888", "process": "mill"}
'''


@pytest.mark.parametrize("edit, want", [(("XS = [10.0, 20.0]", "XS = [10.0, 30.0]"), 30.0),
                                        (("    return XS[i]\n", "    return XS[i] + 5.0\n"), 25.0)],
                         ids=["data-in-the-helper", "code-of-the-helper-function"])
def test_a_helper_reached_only_through_a_nodes_params_is_followed(tmp_path, edit, want):
    """parts/_geom.py (XS, span(i)) is imported only by parts/hp/params.py, whose width() returns span(1); q_w.py builds
    Box(P.width(), ...). Editing XS, or span's code, in _geom.py must rebuild q_w to the new size."""
    root = Path(tmp_path / "proj")
    cad = H.write_base(root, "UNUSED = 1.0\n")
    (cad / "parts" / "_geom.py").write_text(GEOM_HELPER)
    H.write_node(root, "hp", {"q_w": WIDTH_PART})
    _node_params(root, "hp").write_text("from parts._geom import span\n\n\ndef width():\n    return span(1)\n")
    _assemble(root, ["hp"])
    H.run_model(root).ok()
    assert _size(root, "q_w").X == pytest.approx(20.0, abs=1e-6), "cold: span(1) = XS[1] = 20"
    H.clear_ticks(root)
    H.edit(cad / "parts" / "_geom.py", *edit)
    r = H.run_model(root).ok()
    assert "q_w" in H.ticks(root), f"q_w's width comes from parts/_geom.py through hp/params.width(): {sorted(r.rebuilt)}"
    assert _size(root, "q_w").X == pytest.approx(want, abs=1e-6)


# ================================================================ the previous version reading a cache this one wrote
RAIL_PART = '''
from build123d import Box
from ._layout import RAIL_Y
{TICK}
def build():
    _tick("r_part")
    return {"name": "r_part", "shape": Box(RAIL_Y, 5, 2), "color": "#888888", "process": "mill"}
'''


def test_the_previous_version_reading_this_versions_cache_does_not_serve_a_stale_part(tmp_path, monkeypatch):
    """lay/_layout.py: RAIL_Y = int(params.A) + 1; r_part.py takes RAIL_Y. This version builds; A goes 10 -> 20; then the
    PREVIOUS cad_incremental.py (a worker not yet updated) builds the same project. It must not reuse r_part at 11."""
    root = Path(tmp_path / "proj")
    cad = H.write_base(root, "A = 10.0\nUNUSED = 1.0\n")
    H.write_node(root, "lay", {"r_part": RAIL_PART})
    (cad / "parts" / "lay" / "_layout.py").write_text("import params\n\nRAIL_Y = int(params.A) + 1\n")
    _assemble(root, ["lay"])
    H.run_model(root).ok()
    assert _size(root, "r_part").X == pytest.approx(11.0, abs=1e-6)
    H.edit(cad / "params.py", "A = 10.0", "A = 20.0")
    monkeypatch.setattr(H, "CAD_INC", _previous_script(tmp_path / "previous"))
    r = H.run_model(root).ok()
    assert _size(root, "r_part").X == pytest.approx(21.0, abs=1e-6), \
        f"the previous version served r_part from this version's record at the old size (rebuilt {sorted(r.rebuilt)})"


# ================================================================ a node param whose repr hides part of its value
ARR_PART = '''
from build123d import Box
from . import params as P
{TICK}
def build():
    _tick("a_mid")
    return {"name": "a_mid", "shape": Box(1.0 + float(P.ARR[1000]), 5, 2), "color": "#888888", "process": "mill"}
'''


def test_a_change_hidden_by_a_params_repr_still_rebuilds_the_part(tmp_path):
    """parts/arr/params.py: ARR = numpy.zeros(2000) (its repr shows only the ends: '[0. 0. 0. ... 0. 0. 0.]'); a_mid.py reads
    ARR[1000]. Setting ARR[1000] = 7.0 must rebuild it."""
    root = Path(tmp_path / "proj")
    H.write_base(root, "UNUSED = 1.0\n")
    H.write_node(root, "arr", {"a_mid": ARR_PART})
    _node_params(root, "arr").write_text("import numpy\n\nARR = numpy.zeros(2000)\n")
    _assemble(root, ["arr"])
    H.run_model(root).ok()
    assert _size(root, "a_mid").X == pytest.approx(1.0, abs=1e-6)
    H.clear_ticks(root)
    H.edit(_node_params(root, "arr"), "ARR = numpy.zeros(2000)\n", "ARR = numpy.zeros(2000)\nARR[1000] = 7.0\n")
    r = H.run_model(root).ok()
    assert "a_mid" in H.ticks(root), f"ARR[1000] changed: a_mid must rebuild, rebuilt {sorted(r.rebuilt)}"
    assert _size(root, "a_mid").X == pytest.approx(8.0, abs=1e-6)


# ================================================================ the node's params.py imported INSIDE build()
LAZY_NP_PART = '''
from build123d import Box
{TICK}
def build():
    from . import params as P
    _tick("q_lazy")
    return {"name": "q_lazy", "shape": Box(P.X, 5, 2), "color": "#888888", "process": "mill"}
'''


def test_a_nodes_params_imported_inside_build_still_follows_its_values(tmp_path):
    """q_lazy.py imports its node's params.py inside build(). Cold, two no-edit runs (not asserted: a module imported only
    inside build() cannot be checked before the build), then X 5 -> 9: q_lazy must be 9 wide."""
    root = Path(tmp_path / "proj")
    H.write_base(root, PARAMS)
    H.write_node(root, "lz", {"q_lazy": LAZY_NP_PART})
    _node_params(root, "lz").write_text("X = 5.0\n")
    _assemble(root, ["lz"])
    H.run_model(root).ok()
    assert _size(root, "q_lazy").X == pytest.approx(5.0, abs=1e-6)
    for _ in range(2):
        H.run_model(root).ok()
    H.clear_ticks(root)
    H.edit(_node_params(root, "lz"), "X = 5.0", "X = 9.0")
    r = H.run_model(root).ok()
    assert _size(root, "q_lazy").X == pytest.approx(9.0, abs=1e-6), f"q_lazy is stale (ran {H.ticks(root)}, {sorted(r.rebuilt)})"


# ================================================================ a sub-node's own params.py
TOP_INIT = '''from build123d import Pos
from geom import place
from . import sub


def build():
    return place(sub.build(), Pos(0, 0, 0))     # a sub-assembly: place() of a list is a list
'''


def test_a_sub_nodes_own_params_module_rebuilds_only_its_readers_and_falls_through(tmp_path):
    """parts/top/sub/params.py defines LOCAL and OTHER; sub's part files read P.LOCAL (s_loc), P.OTHER (s_oth) and P.GLOBAL_W
    (s_glob), which sub does not define - it falls through to mechanics/cad/params.py. Each edit rebuilds only its readers."""
    root = Path(tmp_path / "proj")
    cad = H.write_base(root, PARAMS)
    (cad / "parts" / "top").mkdir()
    (cad / "parts" / "top" / "__init__.py").write_text(TOP_INIT)
    H.write_node(root, "top/sub", {"s_loc": _np_part("s_loc", "LOCAL"), "s_oth": _np_part("s_oth", "OTHER"),
                                   "s_glob": _np_part("s_glob", "GLOBAL_W")},
                 [("s_loc", (0, 0, 0)), ("s_oth", (20, 0, 0)), ("s_glob", (40, 0, 0))])
    H.edit(cad / "parts" / "top" / "sub" / "__init__.py", "_tick('node_top/sub')", "_tick('node_sub')")
    (cad / "parts" / "top" / "sub" / "params.py").write_text("LOCAL = 3.0\nOTHER = 4.0\n")
    _assemble(root, ["top"])
    cold = H.run_model(root)
    assert cold.rc == 0 and cold.summary, f"a sub-node's own params.py (with a fall-through name) must build:\n{cold.log[-1500:]}"
    assert [round(_size(root, p).X, 6) for p in ("s_loc", "s_oth", "s_glob")] == [3.0, 4.0, 10.0]
    sub_params = cad / "parts" / "top" / "sub" / "params.py"
    for path, old, new, pid, want in ((sub_params, "LOCAL = 3.0", "LOCAL = 6.0", "s_loc", 6.0),
                                      (sub_params, "OTHER = 4.0", "OTHER = 8.0", "s_oth", 8.0),
                                      (cad / "params.py", "GLOBAL_W = 10.0", "GLOBAL_W = 12.0", "s_glob", 12.0)):
        H.clear_ticks(root)
        H.edit(path, old, new)
        r = H.run_model(root).ok()
        ran = [t for t in H.ticks(root) if t in ("s_loc", "s_oth", "s_glob")]
        assert ran == [pid], f"`{new}`: only {pid} reads it, these part files rebuilt: {ran} ({sorted(r.rebuilt)})"
        assert _size(root, pid).X == pytest.approx(want, abs=1e-6)


# ================================================================ the first run after the upgrade, no edit
@pytest.mark.parametrize("make", [_np_project, H.make_project], ids=["node-params-project", "generic-project"])
def test_the_first_run_after_the_upgrade_with_no_edit_rebuilds_nothing(tmp_path, monkeypatch, make):
    """The previous cad_incremental.py (PREVIOUS) builds; this version then runs with nothing changed: nodes rebuilt 0."""
    root = make(tmp_path / "proj")
    monkeypatch.setattr(H, "CAD_INC", _previous_script(tmp_path / "previous"))
    H.run_model(root).ok()
    monkeypatch.undo()
    H.clear_ticks(root)
    r = H.run_model(root).ok()
    assert r.summary[0] == 0 and H.ticks(root) == [], \
        f"the first run after the upgrade, nothing changed, must rebuild nothing: {r.summary[0]}, {sorted(r.rebuilt)}"


MATH_PARAMS = """import math
from math import sin, sqrt, radians

A = 10.0


def reach():
    return sqrt(2.0) * 10 + sin(radians(30.0))


def diag():
    return math.sqrt(A * A + 1)


def big():
    return max(A, 3.0)
"""

MATH_PART = '''
import params
from build123d import Box
{TICK}
def build():
    _tick("@PID@")
    return {"name": "@PID@", "shape": Box(@EXPR@, 1, 1), "color": "#888888", "process": "mill"}
'''


def test_the_first_run_after_the_upgrade_rebuilds_nothing_with_params_functions_using_math(tmp_path, monkeypatch):
    """The machine's params.py does `from math import sin, sqrt, radians` and defines reach() / diag() / big() using them (and
    max); parts read params.reach(), params.diag(), params.big(). PREVIOUS builds; this version, nothing changed: rebuilt 0."""
    root = Path(tmp_path / "proj")
    H.write_base(root, MATH_PARAMS)
    H.write_node(root, "mt", {pid: MATH_PART.replace("@PID@", pid).replace("@EXPR@", expr)
                              for pid, expr in (("m_reach", "params.reach()"), ("m_diag", "params.diag()"),
                                                ("m_big", "params.big()"))},
                 [("m_reach", (0, 0, 0)), ("m_diag", (30, 0, 0)), ("m_big", (60, 0, 0))])
    _assemble(root, ["mt"])
    monkeypatch.setattr(H, "CAD_INC", _previous_script(tmp_path / "previous"))
    H.run_model(root).ok()
    monkeypatch.undo()
    H.clear_ticks(root)
    r = H.run_model(root).ok()
    assert r.summary[0] == 0 and H.ticks(root) == [], \
        f"the first run after the upgrade, nothing changed, must rebuild nothing: {r.summary[0]}, {sorted(r.rebuilt)}"


# ================================================================ a node's params.py imported INSIDE build(), its value from elsewhere
NODE_PARAMS_FROM = {
    # the node's params.py takes its value straight from the machine params; float() leaves no param mark on it
    "machine-params": "import params as MP\n\nX = float(MP.GW)\n",
    # ... or through a project module that derives it from the machine params
    "project-module": "from parts._lay import W\n\nX = W\n",
}


@pytest.mark.parametrize("source", list(NODE_PARAMS_FROM))
def test_a_nodes_params_imported_inside_build_follows_the_machine_param_behind_its_value(tmp_path, source):
    """q_lazy.py imports its node's params.py inside build() (`from . import params as P`, Box(P.X, 5, 2)). That params.py gets
    X from the machine params: machine-params `X = float(MP.GW)`; project-module `from parts._lay import W; X = W` with
    parts/_lay.py `W = float(P.GW)`. Cold, two no-edit runs, then GW 5 -> 9: q_lazy must be what a clean build of the edited
    project gives (9 wide), and the next no-edit run rebuilds nothing."""
    root = Path(tmp_path / "proj")
    cad = H.write_base(root, "GW = 5.0\n")
    (cad / "parts" / "_lay.py").write_text("import params as P\n\nW = float(P.GW)\n")
    H.write_node(root, "lz", {"q_lazy": LAZY_NP_PART})
    _node_params(root, "lz").write_text(NODE_PARAMS_FROM[source])
    _assemble(root, ["lz"])
    H.run_model(root).ok()
    assert _size(root, "q_lazy").X == pytest.approx(5.0, abs=1e-6)
    for _ in range(2):
        H.run_model(root).ok()
    H.clear_ticks(root)
    H.edit(cad / "params.py", "GW = 5.0", "GW = 9.0")
    r = H.run_model(root).ok()
    got = round(_size(root, "q_lazy").X, 6)
    clean = _copy(root, tmp_path / "clean")
    shutil.rmtree(clean / "build" / "model")
    H.run_model(clean).ok()
    want = round(_size(clean, "q_lazy").X, 6)
    assert want == 9.0, f"{source}: precondition: a clean build gives q_lazy 9 wide, got {want}"
    assert got == want, \
        f"{source}: GW 5 -> 9: incremental q_lazy {got} wide != clean {want} (ran {H.ticks(root)}, {sorted(r.rebuilt)})"
    H.clear_ticks(root)
    r = H.run_model(root).ok()
    assert r.summary[0] == 0 and H.ticks(root) == [], \
        f"{source}: a no-edit run after the edit must rebuild nothing: nodes rebuilt {r.summary[0]}, {sorted(r.rebuilt)}"


# ================================================================ review 11: the node's params read through vars() / __dict__; an object
NP_DICT_READERS = {
    # reads the node's params module through vars()
    "vars": '''
from build123d import Box
from . import params as NP
{TICK}
def build():
    _tick("p_v")
    return {"name": "p_v", "shape": Box(vars(NP)["W"], 1, 1), "color": "#888888", "process": "mill"}
''',
    # copies the module's __dict__ in a comprehension at module top level
    "dict-comprehension": '''
from build123d import Box
from . import params as NP
{TICK}
DIMS = {k: v for k, v in NP.__dict__.items() if k.isupper()}
def build():
    _tick("p_v")
    return {"name": "p_v", "shape": Box(DIMS["W"], 1, 1), "color": "#888888", "process": "mill"}
''',
}

# where the node's W comes from, and the edit that turns it from 5 into 9
NP_DICT_SOURCES = {
    "node-literal": ("W = 5.0\n", "parts/v/params.py", "W = 5.0", "W = 9.0"),
    "machine-float": ("import params as MP\n\nW = float(MP.GW)\n", "params.py", "GW = 5.0", "GW = 9.0"),
}


@pytest.mark.parametrize("source", list(NP_DICT_SOURCES))
@pytest.mark.parametrize("reader", list(NP_DICT_READERS))
def test_a_nodes_params_read_through_vars_or_its_dict_gives_the_clean_build_size(tmp_path, reader, source):
    """Node v has its own params.py defining W; p_v.py builds Box(W, 1, 1), reading W as vars(NP)["W"] (vars) or from
    DIMS = {k: v for k, v in NP.__dict__.items() ...} at module top level (dict-comprehension). W is 5.0 in the node's params
    (node-literal, edited to 9.0) or float(MP.GW) with the machine GW = 5.0 (machine-float, GW edited to 9.0). After the edit,
    p_v must be the clean-build size (9 wide)."""
    np_src, edited, old, new = NP_DICT_SOURCES[source]
    root = Path(tmp_path / "proj")
    cad = H.write_base(root, "GW = 5.0\n")
    H.write_node(root, "v", {"p_v": NP_DICT_READERS[reader]})
    _node_params(root, "v").write_text(np_src)
    _assemble(root, ["v"])
    H.run_model(root).ok()
    assert _size(root, "p_v").X == pytest.approx(5.0, abs=1e-6), f"{reader}/{source}: cold p_v must be 5 wide"
    H.clear_ticks(root)
    H.edit(cad / edited, old, new)
    r = H.run_model(root).ok()
    got = round(_size(root, "p_v").X, 6)
    clean = _copy(root, tmp_path / "clean")
    shutil.rmtree(clean / "build" / "model")
    H.run_model(clean).ok()
    want = round(_size(clean, "p_v").X, 6)
    assert want == 9.0, f"{reader}/{source}: precondition: a clean build gives p_v 9 wide, got {want}"
    assert got == want, \
        f"{reader}/{source}: 5 -> 9: incremental p_v {got} wide != clean {want} (ran {H.ticks(root)}, {sorted(r.rebuilt)})"


NP_OBJECT_PART = '''
from build123d import Box
from . import params as NP
{TICK}
def build():
    _tick("p_at")
    return {"name": "p_at", "shape": Box(NP.W, 1, NP.AT.position.Z), "color": "#888888", "process": "mill"}
'''


def test_a_build123d_object_in_a_nodes_params_is_reused_unchanged_and_followed_when_edited(tmp_path):
    """Node at's params.py: `from build123d import Pos; W = 5.0; AT = Pos(0, 0, 3)`. p_at.py builds Box(NP.W, 1,
    NP.AT.position.Z). After the cold build a no-edit run rebuilds nothing; then AT -> Pos(0, 0, 5) must give p_at the
    clean-build size (5 high)."""
    root = Path(tmp_path / "proj")
    H.write_base(root, "GW = 5.0\n")
    H.write_node(root, "at", {"p_at": NP_OBJECT_PART})
    _node_params(root, "at").write_text("from build123d import Pos\n\nW = 5.0\nAT = Pos(0, 0, 3)\n")
    _assemble(root, ["at"])
    H.run_model(root).ok()
    assert _size(root, "p_at").Z == pytest.approx(3.0, abs=1e-6), "cold p_at must be 3 high"
    H.clear_ticks(root)
    r = H.run_model(root).ok()
    assert r.summary[0] == 0 and H.ticks(root) == [], \
        f"a no-edit run must rebuild nothing: nodes rebuilt {r.summary[0]}, {sorted(r.rebuilt)}"
    H.clear_ticks(root)
    H.edit(_node_params(root, "at"), "AT = Pos(0, 0, 3)", "AT = Pos(0, 0, 5)")
    r = H.run_model(root).ok()
    got = tuple(round(v, 6) for v in (lambda s: (s.X, s.Y, s.Z))(_size(root, "p_at")))
    clean = _copy(root, tmp_path / "clean")
    shutil.rmtree(clean / "build" / "model")
    H.run_model(clean).ok()
    want = tuple(round(v, 6) for v in (lambda s: (s.X, s.Y, s.Z))(_size(clean, "p_at")))
    assert want == (5.0, 1.0, 5.0), f"precondition: a clean build gives p_at 5 x 1 x 5, got {want}"
    assert got == want, f"AT z 3 -> 5: incremental p_at {got} != clean {want} (ran {H.ticks(root)}, {sorted(r.rebuilt)})"


# ================================================================ review 12: a node's params behind an Enum method or an lru_cache
def _np_expr_part(pid, expr):
    """A part file `pid` reading its node's params.py as NP and building Box(<expr>, 1, 1); it ticks its id."""
    return ("from build123d import Box\nfrom . import params as NP\n{TICK}\ndef build():\n    _tick(\"" + pid + "\")\n"
            "    return {\"name\": \"" + pid + "\", \"shape\": Box(" + expr + ", 1, 1), \"color\": \"#888888\", "
            "\"process\": \"mill\"}\n")


def _np_gw_edit_sizes(root, tmp_path, pids):
    """GW 5 -> 9 on a built project: {pid: (incremental X, clean-build X)} and the run."""
    cad = H.cad_dir(root)
    H.clear_ticks(root)
    H.edit(cad / "params.py", "GW = 5.0", "GW = 9.0")
    r = H.run_model(root).ok()
    clean = _copy(root, tmp_path / "clean")
    shutil.rmtree(clean / "build" / "model")
    H.run_model(clean).ok()
    return {p: (round(_size(root, p).X, 6), round(_size(clean, p).X, 6)) for p in pids}, r


NP_ENUM = '''import enum
from params import *

K = float(GW)


class Mode(enum.Enum):
    A = 1.0

    def w(self):
        return self.value * K


MODE = Mode.A
'''


def test_a_machine_param_behind_an_enum_method_in_a_nodes_params_gives_the_clean_build_size(tmp_path):
    """Node v's params.py does `from params import *` (machine GW = 5), K = float(GW), and holds MODE = Mode.A, an Enum member
    whose w() returns value * K. p_v.py builds Box(NP.MODE.w(), 1, 1). Cold, a no-edit run, then GW 5 -> 9: p_v must be the
    clean-build size (9 wide)."""
    root = Path(tmp_path / "proj")
    H.write_base(root, "GW = 5.0\n")
    H.write_node(root, "v", {"p_v": _np_expr_part("p_v", "NP.MODE.w()")})
    _node_params(root, "v").write_text(NP_ENUM)
    _assemble(root, ["v"])
    H.run_model(root).ok()
    assert _size(root, "p_v").X == pytest.approx(5.0, abs=1e-6), "cold p_v must be 5 wide"
    H.run_model(root).ok()
    sizes, r = _np_gw_edit_sizes(root, tmp_path, ["p_v"])
    got, want = sizes["p_v"]
    assert want == 9.0, f"precondition: a clean build gives p_v 9 wide, got {want}"
    assert got == want, f"GW 5 -> 9: incremental p_v {got} wide != clean {want} (ran {H.ticks(root)}, {sorted(r.rebuilt)})"


NP_LRU = '''import functools
import params as P

OTHER = 1.0


@functools.lru_cache(maxsize=None)
def width():
    return float(P.GW)
'''


def test_a_machine_param_behind_an_lru_cache_in_a_nodes_params_gives_both_callers_the_clean_build_size(tmp_path):
    """Node v's params.py holds an lru_cache function width() returning float(P.GW) (machine GW = 5) and OTHER = 1.0. Two part
    files call it: p_a.py builds Box(NP.width(), 1, 1), p_v.py Box(NP.width() + 0.0 * NP.OTHER, 1, 1); whichever runs second
    gets the cached value. Cold, a no-edit run, then GW 5 -> 9: BOTH must be the clean-build size (9 wide)."""
    root = Path(tmp_path / "proj")
    H.write_base(root, "GW = 5.0\n")
    H.write_node(root, "v", {"p_a": _np_expr_part("p_a", "NP.width()"), "p_v": _np_expr_part("p_v", "NP.width() + 0.0 * NP.OTHER")})
    _node_params(root, "v").write_text(NP_LRU)
    _assemble(root, ["v"])
    H.run_model(root).ok()
    assert _size(root, "p_a").X == pytest.approx(5.0, abs=1e-6) and _size(root, "p_v").X == pytest.approx(5.0, abs=1e-6), \
        "cold p_a and p_v must be 5 wide"
    H.run_model(root).ok()
    sizes, r = _np_gw_edit_sizes(root, tmp_path, ["p_a", "p_v"])
    for pid, (got, want) in sizes.items():
        assert want == 9.0, f"precondition: a clean build gives {pid} 9 wide, got {want}"
    stale = {pid: s for pid, s in sizes.items() if s[0] != s[1]}
    assert not stale, f"GW 5 -> 9: {{pid: (incremental, clean)}} differ: {stale} (ran {H.ticks(root)}, {sorted(r.rebuilt)})"


# ================================================================ review 13: a machine param behind a dict's .values() or a
# contextmanager in a node's params
NP_R13 = {
    # the machine's params.py holds DIMS = {"w": 5.0}; the node's params does `from params import *`; p_v sums DIMS.values()
    "np-dict-values": ('DIMS = {"w": 5.0}\n', "from params import *\n",
                       "from build123d import Box\nfrom . import params as NP", "sum(NP.DIMS.values())",
                       ('"w": 5.0', '"w": 9.0')),
    # the node's params holds a contextlib.contextmanager yielding W = float(P.GW) (machine GW = 5)
    "np-contextmanager": ("GW = 5.0\n",
                          "import contextlib\nimport params as P\nW = float(P.GW)\n\n\n"
                          "@contextlib.contextmanager\ndef width():\n    yield W\n",
                          "from build123d import Box\nfrom . import params as NP\n\n\ndef _w():\n"
                          "    with NP.width() as w:\n        return w\n", "_w()",
                          ("GW = 5.0", "GW = 9.0")),
}


@pytest.mark.parametrize("case", list(NP_R13))
def test_a_machine_param_behind_dict_values_or_a_contextmanager_in_a_nodes_params_gives_the_clean_build_size(tmp_path, case):
    """Node v's params.py exposes a machine param worth 5: np-dict-values re-exports the machine's DIMS = {"w": 5.0} through
    `from params import *` and p_v.py builds Box(sum(NP.DIMS.values()), 1, 1); np-contextmanager defines a
    contextlib.contextmanager width() yielding float(P.GW) and p_v.py builds Box(<the yielded value>, 1, 1). Cold (5 wide), a
    no-edit run that rebuilds nothing, then the machine value 5 -> 9: p_v must be the clean-build size (9 wide)."""
    machine, np_src, imports, expr, (old, new) = NP_R13[case]
    root = Path(tmp_path / "proj")
    cad = H.write_base(root, machine)
    H.write_node(root, "v", {"p_v": (imports + "\n{TICK}\ndef build():\n    _tick(\"p_v\")\n    return {\"name\": \"p_v\", "
                                     "\"shape\": Box(" + expr + ", 1, 1), \"color\": \"#888888\", \"process\": \"mill\"}\n")})
    _node_params(root, "v").write_text(np_src)
    _assemble(root, ["v"])
    H.run_model(root).ok()
    assert _size(root, "p_v").X == pytest.approx(5.0, abs=1e-6), f"{case}: cold p_v must be 5 wide"
    H.clear_ticks(root)
    r = H.run_model(root).ok()
    assert r.summary[0] == 0 and H.ticks(root) == [], \
        f"{case}: a no-edit run must rebuild nothing: nodes rebuilt {r.summary[0]}, {sorted(r.rebuilt)}"
    H.clear_ticks(root)
    H.edit(cad / "params.py", old, new)
    r = H.run_model(root).ok()
    got = round(_size(root, "p_v").X, 6)
    clean = _copy(root, tmp_path / "clean")
    shutil.rmtree(clean / "build" / "model")
    H.run_model(clean).ok()
    want = round(_size(clean, "p_v").X, 6)
    assert want == 9.0, f"{case}: precondition: a clean build gives p_v 9 wide, got {want}"
    assert got == want, f"{case}: 5 -> 9: incremental p_v {got} wide != clean {want} (ran {H.ticks(root)}, {sorted(r.rebuilt)})"


# ================================================================ review 14: a node's params.py with an import-time side effect: it
# patches build123d, or an object of a project module the part reads
NP_R14 = {
    # node v's params.py sets build123d.Box.default_w = float(P.GW) (machine GW = 5); p_v reads it times NP.OTHER
    "np-patch": ({"v/params": "import build123d\nimport params as P\nbuild123d.Box.default_w = float(P.GW)\nOTHER = 1.0\n"},
                 "from build123d import Box\nimport build123d\nfrom . import params as NP", "build123d.Box.default_w * NP.OTHER"),
    # node v's params.py sets CFG.w = float(P.GW) on parts/_lay.py's CFG; p_v imports NP (reading nothing from it) and reads CFG.w
    "np-side-effect": ({"_lay": "class Cfg:\n    w = 1.0\n\n\nCFG = Cfg()\n",
                        "v/params": "import params as P\nfrom parts._lay import CFG\nCFG.w = float(P.GW)\nOTHER = 1.0\n"},
                       "from build123d import Box\nfrom . import params as NP\nfrom parts._lay import CFG", "CFG.w"),
    # ... and p_v also reads NP.OTHER
    "np-side-effect-read": ({"_lay": "class Cfg:\n    w = 1.0\n\n\nCFG = Cfg()\n",
                             "v/params": "import params as P\nfrom parts._lay import CFG\nCFG.w = float(P.GW)\nOTHER = 1.0\n"},
                            "from build123d import Box\nfrom . import params as NP\nfrom parts._lay import CFG",
                            "CFG.w * NP.OTHER"),
}


@pytest.mark.parametrize("case", list(NP_R14))
def test_a_machine_param_a_nodes_params_patches_into_build123d_or_a_project_object_gives_the_clean_build_size(tmp_path, case):
    """Node v's params.py, at import, writes float(P.GW) (machine GW = 5) somewhere else: onto build123d.Box.default_w, which
    p_v.py reads times NP.OTHER (np-patch), or onto CFG.w of parts/_lay.py, which p_v.py reads alone (np-side-effect) or times
    NP.OTHER (np-side-effect-read). p_v.py builds Box(<value>, 1, 1). Cold (5 wide), a no-edit run that rebuilds nothing, then
    GW 5 -> 9: p_v must be the clean-build size (9 wide)."""
    modules, imports, expr = NP_R14[case]
    root = Path(tmp_path / "proj")
    cad = H.write_base(root, "GW = 5.0\n")
    H.write_node(root, "v", {"p_v": (imports + "\n{TICK}\ndef build():\n    _tick(\"p_v\")\n    return {\"name\": \"p_v\", "
                                     "\"shape\": Box(" + expr + ", 1, 1), \"color\": \"#888888\", \"process\": \"mill\"}\n")})
    for name, src in modules.items():
        (cad / "parts" / f"{name}.py").write_text(src)
    _assemble(root, ["v"])
    H.run_model(root).ok()
    assert _size(root, "p_v").X == pytest.approx(5.0, abs=1e-6), f"{case}: cold p_v must be 5 wide"
    H.clear_ticks(root)
    r = H.run_model(root).ok()
    assert r.summary[0] == 0 and H.ticks(root) == [], \
        f"{case}: a no-edit run must rebuild nothing: nodes rebuilt {r.summary[0]}, {sorted(r.rebuilt)}"
    sizes, r = _np_gw_edit_sizes(root, tmp_path, ["p_v"])
    got, want = sizes["p_v"]
    assert want == 9.0, f"{case}: precondition: a clean build gives p_v 9 wide, got {want}"
    assert got == want, f"{case}: GW 5 -> 9: incremental p_v {got} wide != clean {want} (ran {H.ticks(root)}, {sorted(r.rebuilt)})"


# ================================================================ review 15: the ORDER of a dict or enum in a node's params.py
R15_NP_ORDER = {
    # node v's params.py holds LAYERS = {"base": 4.0, "top": 1.0}; the edit swaps the order of the same two entries
    "np-dict-order": ("GW = 5.0\n", "LAYERS = {\"base\": 4.0, \"top\": 1.0}\n", "list(NP.LAYERS.values())[0]",
                      "parts/v/params.py", "LAYERS = {\"base\": 4.0, \"top\": 1.0}", "LAYERS = {\"top\": 1.0, \"base\": 4.0}"),
    # node v's params.py builds LAYERS from the machine's ORDER list and T dict; the edit swaps the machine's ORDER
    "np-derived-order": ("ORDER = [\"base\", \"top\"]\nT = {\"base\": 4.0, \"top\": 1.0}\n",
                         "from params import *\nLAYERS = {n: float(T[n]) for n in ORDER}\n", "list(NP.LAYERS.values())[0]",
                         "params.py", "ORDER = [\"base\", \"top\"]", "ORDER = [\"top\", \"base\"]"),
    # node v's params.py defines enum Layer(BASE = 4.0, TOP = 1.0); the part takes the first member; the edit swaps the members
    "np-enum-order": ("GW = 5.0\n", "import enum\n\n\nclass Layer(enum.Enum):\n    BASE = 4.0\n    TOP = 1.0\n",
                      "list(NP.Layer)[0].value",
                      "parts/v/params.py", "    BASE = 4.0\n    TOP = 1.0\n", "    TOP = 1.0\n    BASE = 4.0\n"),
}


@pytest.mark.parametrize("case", list(R15_NP_ORDER))
def test_the_order_of_a_dict_or_enum_in_a_nodes_params_gives_the_clean_build_size(tmp_path, case):
    """Node v's params.py holds an ordered collection whose FIRST entry p_v.py builds as Box(<first>, 1, 1): a dict literal
    (np-dict-order), a dict built from the machine's ORDER list (np-derived-order), or an Enum iterated in definition order
    (np-enum-order). The edit changes only the order (4.0 first -> 1.0 first). Cold (4 wide), a no-edit run that rebuilds
    nothing, then the edit: p_v must be the clean-build size (1 wide)."""
    params, node_params, expr, edit_file, old, new = R15_NP_ORDER[case]
    root = Path(tmp_path / "proj")
    cad = H.write_base(root, params)
    H.write_node(root, "v", {"p_v": _np_expr_part("p_v", expr)})
    _node_params(root, "v").write_text(node_params)
    _assemble(root, ["v"])
    H.run_model(root).ok()
    assert _size(root, "p_v").X == pytest.approx(4.0, abs=1e-6), f"{case}: cold p_v must be 4 wide"
    H.clear_ticks(root)
    r = H.run_model(root).ok()
    assert r.summary[0] == 0 and H.ticks(root) == [], \
        f"{case}: a no-edit run must rebuild nothing: nodes rebuilt {r.summary[0]}, {sorted(r.rebuilt)}"
    H.clear_ticks(root)
    H.edit(cad / edit_file, old, new)
    r = H.run_model(root).ok()
    got = round(_size(root, "p_v").X, 6)
    clean = _copy(root, tmp_path / "clean")
    shutil.rmtree(clean / "build" / "model")
    H.run_model(clean).ok()
    want = round(_size(clean, "p_v").X, 6)
    assert want == 1.0, f"{case}: precondition: a clean build gives p_v 1 wide, got {want}"
    assert got == want, f"{case}: order swap: incremental p_v {got} wide != clean {want} (ran {H.ticks(root)}, {sorted(r.rebuilt)})"


def test_a_reordered_dict_in_a_nodes_params_read_by_two_parts_gives_both_the_clean_build_size(tmp_path):
    """Node v's params.py holds LAYERS = {"base": 4.0, "top": 1.0}; p_a.py and p_v.py both build Box(<first value>, 1, 1).
    Cold (both 4 wide), a no-edit run that rebuilds nothing, then the dict's two entries swap order: both parts must be the
    clean-build size (1 wide)."""
    root = Path(tmp_path / "proj")
    H.write_base(root, "GW = 5.0\n")
    expr = "list(NP.LAYERS.values())[0]"
    H.write_node(root, "v", {"p_a": _np_expr_part("p_a", expr), "p_v": _np_expr_part("p_v", expr)})
    _node_params(root, "v").write_text("LAYERS = {\"base\": 4.0, \"top\": 1.0}\n")
    _assemble(root, ["v"])
    H.run_model(root).ok()
    assert [round(_size(root, p).X, 6) for p in ("p_a", "p_v")] == [4.0, 4.0], "cold p_a and p_v must be 4 wide"
    H.clear_ticks(root)
    r = H.run_model(root).ok()
    assert r.summary[0] == 0 and H.ticks(root) == [], \
        f"a no-edit run must rebuild nothing: nodes rebuilt {r.summary[0]}, {sorted(r.rebuilt)}"
    H.clear_ticks(root)
    H.edit(_node_params(root, "v"), "{\"base\": 4.0, \"top\": 1.0}", "{\"top\": 1.0, \"base\": 4.0}")
    r = H.run_model(root).ok()
    got = {p: round(_size(root, p).X, 6) for p in ("p_a", "p_v")}
    clean = _copy(root, tmp_path / "clean")
    shutil.rmtree(clean / "build" / "model")
    H.run_model(clean).ok()
    want = {p: round(_size(clean, p).X, 6) for p in ("p_a", "p_v")}
    assert want == {"p_a": 1.0, "p_v": 1.0}, f"precondition: a clean build gives both parts 1 wide, got {want}"
    assert got == want, f"order swap: incremental {got} != clean {want} (ran {H.ticks(root)}, {sorted(r.rebuilt)})"


# ================================================================ review 16: a class in a node's params.py whose default comes from a param
R16_NP_CLASS = '''from params import *

W = 2.0 if A < 2 else 6.0


class Rail:
    def __init__(self, w=W):
        self.__dict__.update(w=w)
'''


def test_r16_a_class_in_a_nodes_params_storing_a_param_derived_default_through_its_dict_gives_the_clean_build_size(tmp_path):
    """Node v's params.py takes the machine params by star import, sets W = 2.0 if A < 2 else 6.0, and defines class Rail whose
    __init__(self, w=W) stores w with self.__dict__.update(w=w) (no plain attribute assignment). p_v.py builds
    Box(NP.Rail().w, 1, 1). Cold (A 1: 2 wide), a no-edit run that rebuilds nothing, then the machine's A 1 -> 3: p_v must be
    the clean-build size (6 wide)."""
    root = Path(tmp_path / "proj")
    cad = H.write_base(root, "A = 1.0\nB = 2.0\n")
    H.write_node(root, "v", {"p_v": _np_expr_part("p_v", "NP.Rail().w")})
    _node_params(root, "v").write_text(R16_NP_CLASS)
    _assemble(root, ["v"])
    H.run_model(root).ok()
    assert _size(root, "p_v").X == pytest.approx(2.0, abs=1e-6), "cold p_v must be 2 wide"
    H.clear_ticks(root)
    r = H.run_model(root).ok()
    assert r.summary[0] == 0 and H.ticks(root) == [], \
        f"a no-edit run must rebuild nothing: nodes rebuilt {r.summary[0]}, {sorted(r.rebuilt)}"
    H.clear_ticks(root)
    H.edit(cad / "params.py", "A = 1.0", "A = 3.0")
    r = H.run_model(root).ok()
    got = round(_size(root, "p_v").X, 6)
    clean = _copy(root, tmp_path / "clean")
    shutil.rmtree(clean / "build" / "model")
    H.run_model(clean).ok()
    want = round(_size(clean, "p_v").X, 6)
    assert want == 6.0, f"precondition: a clean build gives p_v 6 wide, got {want}"
    assert got == want, f"A 1 -> 3: incremental p_v {got} wide != clean {want} (ran {H.ticks(root)}, {sorted(r.rebuilt)})"
