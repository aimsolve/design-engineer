"""Issue #15 review defects (one part = one file, node = folder): reproducing tests, one per required behaviour.

  R1  kit.json inputs (top level or a scope's own) that do not cover parts/**/*.py in the folder layout -> check.py FAILs naming kit.json/inputs
  R2  a purchase-list row returned by a sub-node that its parent places twice -> qty multiplied by the placements (parts.json and bought)
  R3  assemble.py calling a node with arguments -> that node's parts are in the model, not silently dropped
  R4  a partial run (--model check.py calling a sub-node directly / a node with other arguments) leaves qty and placements unchanged
  (R5, the old-layout rollout test, was removed by #30: an old-layout project no longer builds.)
  R6  check.py on a part file whose build() has a nested helper returning a list -> no "returns a list" FAIL
  R7  build(flag=True) is a different variant from build() with default flag=1 (True == 1 must not merge the variants)
The synthetic folder-layout project is partfiles.py's; small projects are written inline.
"""
import json
from pathlib import Path

import pytest

import helpers as H
import partfiles as F
from test_part_files_template import _new_layout

def _rows(root):
    return {p["id"]: p for p in H.read_parts(root)}


# ================================================================ R1 kit.json inputs must cover parts/**/*.py
def _set_kit(root, mutate):
    kf = Path(root) / "kit.json"
    kit = json.loads(kf.read_text())
    mutate(kit)
    kf.write_text(json.dumps(kit, indent=1))


def _old_glob(globs):
    return ["mechanics/cad/parts/*.py" if g == "mechanics/cad/parts/**/*.py" else g for g in globs]


def _check_fails_on_kit_inputs(root):
    cp = F.run_check(root)
    fl = F.fail_lines(cp)
    assert cp.returncode == 1, f"check.py must exit 1 when kit.json inputs miss the part files, got {cp.returncode}\n" \
                               f"{(cp.stdout + cp.stderr)[-2500:]}"
    assert any("kit.json" in ln and "inputs" in ln for ln in fl), f"no FAIL line names kit.json/inputs: {fl}"


def test_r1_top_level_inputs_with_the_old_glob_fail_check(tmp_path):
    root = _new_layout(tmp_path / "p")
    _set_kit(root, lambda k: k.update(inputs=_old_glob(k["inputs"])))
    assert "mechanics/cad/parts/*.py" in json.loads((root / "kit.json").read_text())["inputs"], "precondition: old glob set"
    _check_fails_on_kit_inputs(root)


def test_r1_a_scopes_own_inputs_with_the_old_glob_fail_check(tmp_path):
    """Top level is right; one scope overrides inputs with the old glob -> that scope's kit goes stale silently -> FAIL."""
    root = _new_layout(tmp_path / "p")

    def mut(k):
        k["scopes"]["bought"]["inputs"] = _old_glob(k["inputs"])
    _set_kit(root, mut)
    _check_fails_on_kit_inputs(root)


def test_r1_recursive_glob_everywhere_passes_check(tmp_path):
    root = _new_layout(tmp_path / "p")

    def mut(k):
        for s in k["scopes"].values():
            s["inputs"] = list(k["inputs"])
    _set_kit(root, mut)
    assert "mechanics/cad/parts/**/*.py" in json.loads((root / "kit.json").read_text())["inputs"]
    cp = F.run_check(root)
    assert cp.returncode == 0, f"FAIL lines: {F.fail_lines(cp)}\n{(cp.stdout + cp.stderr)[-2500:]}"


# ================================================================ R2 a row returned by a twice-placed sub-node
TOWER_TWICE = '''
    from build123d import Pos
    from geom import place
    from . import carriage, knob
    {TICK}

    def build():
        _tick("node_tower")
        return ([place(knob.build(), Pos(500, 0, 0))] + place(carriage.build(), Pos(0, 0, 50))
                + place(carriage.build(), Pos(0, 200, 50)))
    '''
CARRIAGE_WITH_ROW = '''
    from build123d import Pos
    from geom import P, place
    from parts.tower import knob
    from . import slider
    {TICK}

    def build():
        _tick("node_carriage")
        return [place(slider.build(), Pos(5, 0, 0)), place(knob.build(), Pos(5, 0, 30)),
                P("glue", None, "#999", process="bought", code="GL1", {QTY}unit="pc", kind="other")]
    '''


@pytest.mark.parametrize("row_qty,expect", [(2, 4), (None, 2)], ids=["qty2_x2placements", "noqty_x2placements"])
def test_r2_row_in_a_sub_node_placed_twice_counts_every_placement(tmp_path, row_qty, expect):
    carriage = CARRIAGE_WITH_ROW.replace("{QTY}", "" if row_qty is None else f"qty={row_qty}, ")
    root = F.write_project(tmp_path / "p", files={"tower/__init__.py": TOWER_TWICE, "tower/carriage/__init__.py": carriage})
    F.run_model(root).ok()
    ps = _rows(root)
    assert "glue" in ps, f"the glue row is missing from parts.json: {sorted(ps)}"
    assert ps["glue"].get("qty") == expect, f"glue: qty {ps['glue'].get('qty')} != {expect} (row qty x 2 carriage placements)"
    assert ps["slider"]["qty"] == 2, "precondition: the carriage is placed twice"
    d = H.run_derive(root, "bought").ok()
    rows = {r["code"]: r for r in d.manifest["bought"]}
    assert "GL1" in rows, f"GL1 missing from the bought list: {sorted(rows)}"
    assert rows["GL1"]["qty"] == expect, f"bought GL1 qty {rows['GL1']['qty']} != {expect}"


# ================================================================ R3 a node called with arguments from assemble.py
PLATE_PART = '''
    from build123d import Box
    from geom import P

    def build():
        return P("plate", Box(40, 30, 2), "#aaaaaa", process="laser", material="S235JR", thickness=2.0, code="PL-1")
    '''
GAP_NODE = '''
    from build123d import Pos
    from geom import place
    from . import plate

    def build(gap=100):
        return [place(plate.build(), Pos(0, 0, 0)), place(plate.build(), Pos(gap, 0, 0))]
    '''
GAP_ASSEMBLE = """import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from parts import gapped

items = gapped.build(gap=120)
"""


def _gap_project(root):
    only = {k: None for k in F.FILES}
    only.update({"gapped/__init__.py": GAP_NODE, "gapped/plate.py": PLATE_PART})
    return F.write_project(root, files=only, assemble=GAP_ASSEMBLE, legacy=False)


def test_r3_node_called_with_arguments_from_assemble_keeps_its_parts(tmp_path):
    root = _gap_project(tmp_path / "p")
    F.run_model(root).ok()
    ps = _rows(root)
    assert ps, "parts.json is empty: the node called as gapped.build(gap=120) was dropped"
    assert "plate" in ps, f"plate missing from parts.json: {sorted(ps)}"
    assert ps["plate"]["qty"] == 2 == len(F.placements(ps["plate"])), ps["plate"]
    got = sorted(F.origin(pl) for pl in F.placements(ps["plate"]))
    assert got == [(0.0, 0.0, 0.0), (120.0, 0.0, 0.0)], f"placements must use gap=120, got {got}"


# ================================================================ R4 a partial run must not rewrite qty / placements
COUNTED_NODE = '''
    from build123d import Pos
    from geom import place
    from . import peg

    def build(n=2):
        return [place(peg.build(), Pos(10 * i, 0, 0)) for i in range(n)]
    '''
PEG_PART = '''
    from build123d import Box
    from geom import P

    def build():
        return P("peg", Box(4, 4, 4), "#22aa22", process="print", material="PLA")
    '''
ASSEMBLE_R4 = """import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from parts import frame, tower, counted

items = frame.build() + tower.build() + counted.build()
"""
PARTIAL = """import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from parts.tower import carriage
from parts import counted

carriage.build()
counted.build(n=5)
"""


def _qty_and_placements(root):
    return {p["id"]: (p.get("qty"), json.dumps(p.get("placements"), sort_keys=True)) for p in H.read_parts(root)}


def test_r4_partial_run_leaves_qty_and_placements_unchanged(tmp_path):
    root = F.write_project(tmp_path / "p", files={"counted/__init__.py": COUNTED_NODE, "counted/peg.py": PEG_PART},
                           assemble=ASSEMBLE_R4)
    (H.cad_dir(root) / "check.py").write_text(PARTIAL)
    F.run_model(root).ok()
    before = _qty_and_placements(root)
    assert before["peg"][0] == 2 and before["slider"][0] == 1 and before["knob"][0] == 2, \
        f"precondition (entry run): {{id: qty}} = {{{', '.join(f'{k}: {v[0]}' for k, v in before.items())}}}"
    r = H.run_model(root, script="check.py")
    assert r.rc == 0, f"the partial run failed (rc={r.rc}):\n{r.log[-2000:]}"
    after = _qty_and_placements(root)
    assert set(after) == set(before), f"partial run changed the part set: {sorted(set(before) ^ set(after))}"
    changed = {k: (before[k][0], after[k][0]) for k in before if before[k] != after[k]}
    assert not changed, f"partial run rewrote qty/placements of {sorted(changed)} (qty before, after): {changed}"


# ================================================================ R6 nested helper returning a list in a part file
NESTED_HELPER_PART = '''
from build123d import Box
from geom import P


def build(w=40):
    def pts():
        return [(0, 0), (1, 1)]
    n = len(pts())
    return P("plate", Box(w, 30, 2 * n), "#aaaaaa", process="laser", material="S235JR", thickness=4.0, code="PL-1")
'''


def test_r6_nested_helper_returning_a_list_is_not_a_list_returning_part(tmp_path):
    root = _new_layout(tmp_path / "p")
    (H.cad_dir(root) / "parts" / "frame" / "plate.py").write_text(NESTED_HELPER_PART.lstrip())
    cp = F.run_check(root)
    fl = F.fail_lines(cp)
    assert not any("list" in ln for ln in fl), f"a nested helper's list was taken for the part's return: {fl}"
    assert cp.returncode == 0, f"FAIL lines: {fl}\n{(cp.stdout + cp.stderr)[-2500:]}"


# ================================================================ R7 True is not the default 1
PIN_PART = '''
    from build123d import Cylinder
    from geom import P
    {TICK}

    def build(flag=1):
        _tick("pin")
        name = "pin_bool" if isinstance(flag, bool) else "pin"
        return P(name, Cylinder(2, 10), "#cccccc", process="mill", material="S235JR")
    '''
PIN_NODE = '''
    from build123d import Pos
    from geom import place
    from . import pin

    def build():
        return [place(pin.build(), Pos(0, 0, 0)), place(pin.build(flag=True), Pos(20, 0, 0))]
    '''
PIN_ASSEMBLE = """import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from parts import pins

items = pins.build()
"""


def test_r7_bool_argument_is_its_own_variant(tmp_path):
    only = {k: None for k in F.FILES}
    only.update({"pins/__init__.py": PIN_NODE, "pins/pin.py": PIN_PART})
    root = F.write_project(tmp_path / "p", files=only, assemble=PIN_ASSEMBLE, legacy=False)
    F.run_model(root).ok()
    ps = _rows(root)
    assert {"pin", "pin_bool"} <= set(ps), f"build(flag=True) merged with build() (flag=1): parts.json ids {sorted(ps)}"
    assert ps["pin"]["qty"] == 1 and ps["pin_bool"]["qty"] == 1, {k: ps[k]["qty"] for k in ps}
    assert H.ticks(root).count("pin") == 2, f"pin.build must run once per variant (2), ran {H.ticks(root).count('pin')}"
