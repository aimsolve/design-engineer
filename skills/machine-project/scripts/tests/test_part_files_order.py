"""Issue #15 refinement: parts.json lists the parts in the order the nodes return them (folder layout).

The client reads the purchase list (`derive.py bought`) top to bottom; it must follow the order the designer wrote the nodes in:
placed variants AND purchase-list rows without a shape, in the order they appear in each node's returned list, nodes in the
order the entry script calls them.

Project (scaffold, example node removed, two folder nodes; names chosen so that alphabetical order, "placed first, rows last"
and "rows first" all differ from the required order):
    frame/__init__.py  -> [place(zeta), place(alpha), P("glue", None, process="bought", qty=2), place(mid)]
    tower/__init__.py  -> [place(yak), P("bolt", None, process="bought", qty=4), place(apple)]
    assemble.py        -> frame.build() + tower.build()   (O5 swaps it to tower first)
Every part is bought (each with its own code) so the `derive.py bought` rows can be compared one to one with parts.json.

  O1  parts.json ids: zeta, alpha, glue, mid, yak, bolt, apple
  O2  `derive.py bought` rows (by code) follow the same order
  O3  a variant placed again later in the list keeps the position of its FIRST placement
  O4  the order holds on a warm rebuild with no change, and after editing one part file (incremental cache)
  O5  nodes are ordered by the entry script: tower.build() + frame.build() puts tower's parts first
  O6  a row returned before any placed part comes first
"""
import shutil
import textwrap

import helpers as H
import partfiles as F

ORDER = ["zeta", "alpha", "glue", "mid", "yak", "bolt", "apple"]
CODE = {n: f"C-{n.upper()}" for n in ORDER}

PART = '''
from build123d import Box
from geom import P


def build():
    return P("{name}", Box({size}, 10, 10), "#aaaaaa", process="bought", code="{code}", desc="{name} item", kind="hardware")
'''

FRAME = '''
from build123d import Pos
from geom import P, place
from . import zeta, alpha, mid


def build():
    return [{items}]
'''
FRAME_ITEMS = ('place(zeta.build(), Pos(0, 0, 0)), place(alpha.build(), Pos(0, 50, 0)), '
               'P("glue", None, "#999999", process="bought", code="C-GLUE", desc="glue", qty=2, unit="pcs", kind="hardware"), '
               'place(mid.build(), Pos(0, 100, 0))')

TOWER = '''
from build123d import Pos
from geom import P, place
from . import yak, apple


def build():
    return [place(yak.build(), Pos(500, 0, 0)),
            P("bolt", None, "#999999", process="bought", code="C-BOLT", desc="bolt", qty=4, unit="pcs", kind="fasteners"),
            place(apple.build(), Pos(500, 50, 0))]
'''

ASSEMBLE = '''import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from parts import frame, tower

items = {expr}
'''


def _project(tmp_path, frame_items=FRAME_ITEMS, entry="frame.build() + tower.build()"):
    root = F.scaffold(tmp_path / "p")
    cad = H.cad_dir(root)
    parts = cad / "parts"
    (parts / "example.py").unlink(missing_ok=True)
    shutil.rmtree(parts / "example", ignore_errors=True)
    (cad / "geom.py").write_text(F.geom_src())
    for node, members, src in (("frame", ("zeta", "alpha", "mid"), FRAME.replace("{items}", frame_items)),
                               ("tower", ("yak", "apple"), TOWER)):
        (parts / node).mkdir()
        (parts / node / "__init__.py").write_text(textwrap.dedent(src).lstrip())
        for i, n in enumerate(members):
            (parts / node / f"{n}.py").write_text(
                PART.replace("{name}", n).replace("{code}", CODE[n]).replace("{size}", str(10 + i)).lstrip())
    (cad / "assemble.py").write_text(ASSEMBLE.replace("{expr}", entry))
    return root


def _ids(root):
    return [p["id"] for p in H.read_parts(root)]


def _bought_codes(root):
    d = H.run_derive(root, "bought").ok()
    return [r.get("code") for r in d.manifest["bought"]]


def _assert_order(root, expect, when=""):
    got = _ids(root)
    assert got == expect, f"parts.json ids{when} must follow the order the nodes return them:\n  expected {expect}\n  got      {got}"


# ================================================================ parts.json order
def test_o1_parts_json_lists_parts_and_rows_in_node_return_order(tmp_path):
    root = _project(tmp_path)
    F.run_model(root).ok()
    _assert_order(root, ORDER)


def test_o2_derive_bought_rows_follow_the_same_order(tmp_path):
    root = _project(tmp_path)
    F.run_model(root).ok()
    _assert_order(root, ORDER)
    expect = [CODE[n] for n in ORDER]
    got = _bought_codes(root)
    assert got == expect, f"`derive.py bought` rows must be in node return order:\n  expected {expect}\n  got      {got}"


def test_o3_variant_placed_again_later_keeps_its_first_position(tmp_path):
    items = FRAME_ITEMS + ", place(zeta.build(), Pos(0, 150, 0))"      # zeta placed first and again at the end
    root = _project(tmp_path, frame_items=items)
    F.run_model(root).ok()
    _assert_order(root, ORDER)
    assert H.by_id(root)["zeta"]["qty"] == 2, f"zeta placed twice: qty 2 expected: {H.by_id(root)['zeta']}"


def test_o4_order_holds_on_warm_rebuild_and_after_editing_one_part(tmp_path):
    root = _project(tmp_path)
    F.run_model(root).ok()
    _assert_order(root, ORDER, " (cold build)")
    F.run_model(root).ok()
    _assert_order(root, ORDER, " (warm rebuild, nothing changed)")
    H.edit(F.part_file(root, "frame/alpha.py"), "Box(11, 10, 10)", "Box(33, 10, 10)")
    m = F.run_model(root).ok()
    assert m.rebuilt("frame/alpha"), f"precondition: alpha rebuilt after its edit; rebuilt: {m.rebuilt_tokens}"
    _assert_order(root, ORDER, " (after editing frame/alpha.py)")
    assert _bought_codes(root) == [CODE[n] for n in ORDER], "`derive.py bought` order after the incremental rebuild"


def test_o5_entry_script_node_order_decides_which_node_comes_first(tmp_path):
    root = _project(tmp_path, entry="tower.build() + frame.build()")
    F.run_model(root).ok()
    _assert_order(root, ORDER[4:] + ORDER[:4])


def test_o6_row_returned_before_any_placed_part_comes_first(tmp_path):
    items = ('P("glue", None, "#999999", process="bought", code="C-GLUE", desc="glue", qty=2, unit="pcs", kind="hardware"), '
             'place(zeta.build(), Pos(0, 0, 0)), place(alpha.build(), Pos(0, 50, 0)), place(mid.build(), Pos(0, 100, 0))')
    root = _project(tmp_path, frame_items=items)
    F.run_model(root).ok()
    _assert_order(root, ["glue", "zeta", "alpha", "mid", "yak", "bolt", "apple"])
