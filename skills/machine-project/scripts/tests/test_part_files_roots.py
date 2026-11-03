"""Issue #15 (one part = one file, node = folder): what assemble.py and node code may call, and the model must survive it.

  T2  assemble.py calls tower.build() AND tower.carriage.build() (a sub-node tower already places): qty and placements of every
      part are the same as when assemble.py calls only tower.build()
  T3  assemble.py calls a part file only to read a size (frame.rail.build()["shape"]...): qty and placements unchanged (rail 3)
  T4  a node __init__ calls a part's build() at import time to read a size, then places the part in build(): qty = placements
  T5  a part file calls another NODE's build() to read its shapes while assemble.py also calls that node as a root: the read
      node's parts stay in the model with their normal qty
The synthetic folder-layout project and its expected qty (F.QTY) are partfiles.py's.
"""
import json

import pytest

import helpers as H
import partfiles as F
from partfiles import new_baseline  # noqa: F401  (fixture)

HEAD = """import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
"""


def _qty_and_placements(root):
    return {p["id"]: (p.get("qty"), json.dumps(p.get("placements"), sort_keys=True)) for p in H.read_parts(root)}


def _qtys(root):
    return {p["id"]: p.get("qty") for p in H.read_parts(root)}


def _same_model_as_baseline(new_baseline, root):
    """Every part's qty and placements equal those of the plain project (assemble.py = frame + tower)."""
    base_root, m, _ = new_baseline
    m.ok()
    want, got = _qty_and_placements(base_root), _qty_and_placements(root)
    assert set(got) == set(want), f"part set differs from the plain project: missing {sorted(set(want) - set(got))}, " \
                                  f"extra {sorted(set(got) - set(want))}; qty now {_qtys(root)}"
    diff = {k: (want[k][0], got[k][0]) for k in want if want[k] != got[k]}
    assert not diff, f"qty/placements differ from the plain project for {sorted(diff)} (qty plain, now): {diff}"


# ================================================================ T2 assemble.py also calls a sub-node directly
ASSEMBLE_SUBNODE_TOO = HEAD + """from parts import frame, tower

items = frame.build() + tower.build()
sub = tower.carriage.build()
print(len(items), "items", len(sub), "in the carriage")
"""


def test_t2_root_and_its_own_sub_node_both_called_count_once(new_baseline, tmp_path):
    root = F.write_project(tmp_path / "p", assemble=ASSEMBLE_SUBNODE_TOO)
    F.run_model(root).ok()
    q = _qtys(root)
    assert q.get("knob") == 2 and q.get("slider") == 1, \
        f"knob/slider counted twice (tower places the carriage; assemble.py only read it): knob {q.get('knob')} (want 2), " \
        f"slider {q.get('slider')} (want 1)"
    _same_model_as_baseline(new_baseline, root)


# ================================================================ T3 assemble.py reads a size from a part file
ASSEMBLE_READS_RAIL = HEAD + """from parts import frame, tower

items = frame.build() + tower.build()
rail_len = frame.rail.build()["shape"].bounding_box().size.X
print("rail length", rail_len)
"""


def test_t3_part_file_read_from_assemble_is_not_an_extra_placement(new_baseline, tmp_path):
    root = F.write_project(tmp_path / "p", assemble=ASSEMBLE_READS_RAIL)
    F.run_model(root).ok()
    q = _qtys(root)
    assert q.get("rail") == 3, f"rail qty {q.get('rail')} != 3: reading frame.rail.build() in assemble.py is not a placement"
    assert len(F.placements(F.parts(root)["rail"])) == 3
    _same_model_as_baseline(new_baseline, root)


# ================================================================ T4 a node reads a part's size at import time
SHELF_NODE = '''
    from build123d import Pos
    from geom import place
    from . import board
    {TICK}

    W = board.build()["shape"].bounding_box().size.X     # import time: only a size

    def build():
        _tick("node_shelf")
        return [place(board.build(), Pos(0, 0, 0)), place(board.build(), Pos(W + 10, 0, 0))]
    '''
BOARD_PART = '''
    from build123d import Box
    from geom import P
    {TICK}

    def build():
        _tick("board")
        return P("board", Box(40, 20, 3), "#aa8844", process="laser", material="S235JR", thickness=3.0, code="BD-1")
    '''
ASSEMBLE_SHELF = HEAD + """from parts import shelf

items = shelf.build()
"""


def test_t4_import_time_read_of_a_part_is_not_a_placement(tmp_path):
    only = {k: None for k in F.FILES}
    only.update({"shelf/__init__.py": SHELF_NODE, "shelf/board.py": BOARD_PART})
    root = F.write_project(tmp_path / "p", files=only, assemble=ASSEMBLE_SHELF, legacy=False)
    F.run_model(root).ok()
    ps = F.parts(root)
    assert "board" in ps, f"board missing from parts.json: {sorted(ps)}"
    pl = F.placements(ps["board"])
    assert ps["board"]["qty"] == len(pl) == 2, \
        f"board: qty {ps['board']['qty']}, placements {len(pl)}; both must be 2 (the import-time read is not a placement)"
    assert sorted(F.origin(p) for p in pl) == [(0.0, 0.0, 0.0), (50.0, 0.0, 0.0)], [F.origin(p) for p in pl]


# ================================================================ T5 a part file reads another node that is also a root
TABLE_NODE = '''
    from build123d import Pos
    from geom import place
    from . import top
    {TICK}

    def build():
        _tick("node_table")
        return [place(top.build(), Pos(0, 0, 0))]
    '''
TOP_PART = '''
    from build123d import Box
    from geom import P
    from parts import frame
    {TICK}

    def build():
        _tick("top")
        zmax = max(it["shape"].moved(it["loc"]).bounding_box().max.Z for it in frame.build() if it.get("shape") is not None)
        return P("top", Box(400, 400, zmax), "#ddcc99", process="mill", material="MDF")
    '''
ASSEMBLE_TABLE = {
    "frame_first": HEAD + "from parts import frame, tower, table\n\n"
                          "items = frame.build() + tower.build() + table.build()\n",
    "table_first": HEAD + "from parts import frame, tower, table\n\n"
                          "items = table.build() + frame.build() + tower.build()\n",
}


@pytest.mark.parametrize("order", sorted(ASSEMBLE_TABLE))
def test_t5_node_read_by_a_part_file_and_called_as_root_keeps_its_parts(tmp_path, order):
    root = F.write_project(tmp_path / "p", files={"table/__init__.py": TABLE_NODE, "table/top.py": TOP_PART},
                           assemble=ASSEMBLE_TABLE[order])
    F.run_model(root).ok()
    ps = F.parts(root)
    want = dict(F.QTY, top=1)
    missing = sorted(set(want) - set(ps))
    assert not missing, f"parts missing from the model (frame is read by table/top.py and is a root too): {missing}"
    got = {k: ps[k].get("qty") for k in want}
    assert got == want, f"qty {got} != {want}"
    for pid in ("rail", "foot", "plate", "screw"):
        assert len(F.placements(ps[pid])) == want[pid], f"{pid}: {len(F.placements(ps[pid]))} placements != {want[pid]}"
    size = H.step_bbox(root / ps["top"]["step"]).size
    assert size.Z == pytest.approx(10, abs=1e-6), f"top height = the frame's highest point (10), got {size.Z}"

