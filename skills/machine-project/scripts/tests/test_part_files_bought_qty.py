"""Issue #15 refinement: qty on a SHAPED part in the folder layout.

A shaped part's qty is the number of placements and may not be set, EXCEPT on a bought part (process="bought"): there qty means
units per placement. A PVC pipe drawn once and bought as 5 x 1 m is P("pipe", shape, ..., process="bought", code="d90", qty=5).

  B1  bought shaped part qty=5 placed twice -> parts.json qty 10 (2 placements) and `derive.py bought` row d90 qty 10
  B2  the same part placed once -> qty 5 in parts.json and in the bought row
  B3  positional process form P(name, shape, color, None, "bought", qty=5) placed twice -> qty 10
  B4  editing qty=5 -> qty=6 on an already built project -> qty 12 on the next run (no stale cached qty)
  B5  bought shaped part WITHOUT qty placed twice -> qty 2 (units per placement default 1)
  C1  check.py PASSes the bought shaped part with qty (keyword form)
  C2  check.py PASSes the bought shaped part with qty (positional process form)
  G1  a NON-bought shaped part with qty still fails the build loudly, naming the part and qty
  G2  check.py still FAILs a non-bought shaped part with qty, naming the part and qty
Projects start from test_part_files_template._new_layout (scaffolded, conforming folder node frame/ with a laser plate placed
twice) and add frame/pipe.py placed N times by frame/__init__.py.
"""
import textwrap

import helpers as H
import partfiles as F
from test_part_files_template import _new_layout

PIPE_KW = '''
from build123d import Cylinder
from geom import P


def build():
    return P("pipe", Cylinder(45, 1000), "#dddddd", process="bought", code="d90", desc="PVC pipe d90", {QTY}unit="m",
             kind="profile")
'''
PIPE_POS = '''
from build123d import Cylinder
from geom import P


def build():
    return P("pipe", Cylinder(45, 1000), "#dddddd", None, "bought", code="d90", desc="PVC pipe d90", {QTY}unit="m",
             kind="profile")
'''
PIPE_MILL = '''
from build123d import Cylinder
from geom import P


def build():
    return P("pipe", Cylinder(45, 1000), "#dddddd", process="mill", material="PVC", {QTY}code="d90")
'''


def _node(n_pipes):
    pipes = "".join(f"place(pipe.build(), Pos({200 * (i + 1)}, 0, 0)), " for i in range(n_pipes))
    return textwrap.dedent(f'''
        from build123d import Pos
        from geom import place
        from . import plate, pipe


        def build():
            return [place(plate.build(), Pos(0, 0, 0)), place(plate.build(), Pos(0, 50, 0)), {pipes}]
        ''').lstrip()


def _project(tmp_path, pipe_src, qty=5, n_pipes=2):
    root = _new_layout(tmp_path / "p")
    frame = H.cad_dir(root) / "parts" / "frame"
    (frame / "pipe.py").write_text(pipe_src.replace("{QTY}", "" if qty is None else f"qty={qty}, ").lstrip())
    (frame / "__init__.py").write_text(_node(n_pipes))
    return root


def _bought_qty(root):
    d = H.run_derive(root, "bought").ok()
    rows = [r for r in d.manifest["bought"] if r.get("code") == "d90"]
    assert len(rows) == 1, f"one bought row with code d90 expected: {d.manifest['bought']}"
    return rows[0]["qty"]


def _assert_pipe_qty(root, expect, n_placements):
    pipe = F.parts(root).get("pipe")
    assert pipe is not None, f"pipe missing from parts.json: {sorted(F.parts(root))}"
    assert len(F.placements(pipe)) == n_placements, f"pipe: {n_placements} placements expected, got {len(F.placements(pipe))}"
    assert pipe.get("qty") == expect, \
        f"pipe (bought, qty=units per placement): parts.json qty {pipe.get('qty')} != {expect} (units x {n_placements} placements)"
    assert _bought_qty(root) == expect, f"derive.py bought: row d90 qty must be {expect}"


# ================================================================ build: bought shaped part with qty
def test_b1_bought_shaped_part_qty5_placed_twice_counts_10(tmp_path):
    root = _project(tmp_path, PIPE_KW, qty=5, n_pipes=2)
    F.run_model(root).ok()
    _assert_pipe_qty(root, 10, 2)


def test_b2_bought_shaped_part_qty5_placed_once_counts_5(tmp_path):
    root = _project(tmp_path, PIPE_KW, qty=5, n_pipes=1)
    F.run_model(root).ok()
    _assert_pipe_qty(root, 5, 1)


def test_b3_positional_process_form_qty5_placed_twice_counts_10(tmp_path):
    root = _project(tmp_path, PIPE_POS, qty=5, n_pipes=2)
    F.run_model(root).ok()
    _assert_pipe_qty(root, 10, 2)


def test_b4_editing_units_per_placement_updates_qty_on_the_next_run(tmp_path):
    root = _project(tmp_path, PIPE_KW, qty=5, n_pipes=2)
    F.run_model(root).ok()
    assert F.parts(root)["pipe"].get("qty") == 10, f"precondition: qty 10 after the first run: {F.parts(root)['pipe']}"
    H.edit(F.part_file(root, "frame/pipe.py"), "qty=5,", "qty=6,")
    F.run_model(root).ok()
    _assert_pipe_qty(root, 12, 2)


def test_b5_bought_shaped_part_without_qty_counts_placements(tmp_path):
    root = _project(tmp_path, PIPE_KW, qty=None, n_pipes=2)
    F.run_model(root).ok()
    _assert_pipe_qty(root, 2, 2)


# ================================================================ check.py: bought shaped part with qty passes
def _check_passes(root):
    cp = F.run_check(root)
    assert cp.returncode == 0, f"check.py must PASS a bought shaped part with qty; FAIL lines: {F.fail_lines(cp)}\n" \
                               f"{(cp.stdout + cp.stderr)[-2500:]}"


def test_c1_check_passes_bought_shaped_part_with_qty_keyword_form(tmp_path):
    _check_passes(_project(tmp_path, PIPE_KW, qty=5, n_pipes=2))


def test_c2_check_passes_bought_shaped_part_with_qty_positional_form(tmp_path):
    _check_passes(_project(tmp_path, PIPE_POS, qty=5, n_pipes=2))


# ================================================================ guard: non-bought shaped part with qty still refused
def test_g1_non_bought_shaped_part_with_qty_fails_the_build_loudly(tmp_path):
    root = _project(tmp_path, PIPE_MILL, qty=5, n_pipes=2)
    m = F.run_model(root).failed()
    for bad in ("ImportError", "SyntaxError", "NameError", "ModuleNotFoundError"):
        assert bad not in m.log, f"the build crashed ({bad}) instead of reporting the contract error:\n{m.log[-1500:]}"
    for w in ("pipe", "qty"):
        assert w in m.log, f"the error must name {w!r}:\n{m.log[-1500:]}"


def test_g2_check_fails_non_bought_shaped_part_with_qty(tmp_path):
    root = _project(tmp_path, PIPE_MILL, qty=5, n_pipes=2)
    cp = F.run_check(root)
    fl = F.fail_lines(cp)
    assert cp.returncode == 1 and fl, f"check.py must FAIL (exit 1), got {cp.returncode}\n{(cp.stdout + cp.stderr)[-2500:]}"
    assert any("pipe" in ln and "qty" in ln for ln in fl), f"no FAIL line names pipe and qty: {fl}"
