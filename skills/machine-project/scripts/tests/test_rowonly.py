"""Rollout of issue #2: row-only bought parts (no shape) and loud errors for malformed node items. Folder layout: the rows
are returned by a node's __init__.py build() (parts/a/__init__.py), shaped parts come from part files it places."""
import json
import textwrap
from pathlib import Path

import helpers as H
from test_review_defects import _proj

ROW = {"name": "Truba 50", "process": "bought", "code": "TR50", "qty": 3, "unit": "m"}


PLATE = "from build123d import Box\ndef build():\n    return {'name': 'plate', 'shape': Box(5, 5, 1), 'process': 'mill'}\n"


def _row_project(tmp_path, items="[ROW, place(plate.build(), Pos(0, 0, 0))]"):
    """Node a returns `items` from its __init__.py build(): by default the row and its part file plate.py placed once."""
    return _proj(tmp_path / "p", {}, "from parts import a\nout = a.build()\n", files={"a/plate.py": PLATE, "a/__init__.py": f"""
        from build123d import Box, Pos
        from geom import place
        from . import plate
        ROW = {ROW!r}
        def build():
            return {items}
        """})


def test_row_only_part_is_in_parts_json_without_a_step(tmp_path):
    root = _row_project(tmp_path)
    H.run_model(root).ok()
    p = H.by_id(root)["Truba_50"]
    assert p["process"] == "bought" and p["code"] == "TR50" and p["qty"] == 3 and p["unit"] == "m"
    assert "step" not in p and "sha256" not in p
    assert len(p["key"]) == 64
    steps = list((root / "build/model/step").glob("*.step"))
    assert len(steps) == 1, "only the plate has a STEP"


def test_row_only_part_survives_node_reuse_and_param_change(tmp_path):
    root = _proj(tmp_path / "p", {}, "from parts import a\nout = a.build()\n", files={"a/plate.py": PLATE, "a/__init__.py": """
        import params
        from build123d import Pos
        from geom import place
        from . import plate
        def build():
            return [{"name": "Truba", "process": "bought", "code": "T", "qty": params.A, "unit": "m"},
                    place(plate.build(), Pos(0, 0, 0))]
        """})
    H.run_model(root).ok()
    k1 = H.part_keys(root)
    r = H.run_model(root).ok()
    assert r.summary[0] == 0 and H.part_keys(root) == k1
    assert H.by_id(root)["Truba"]["qty"] == 10.0
    H.edit(root / "mechanics/cad/params.py", "10.0", "12.0")
    r = H.run_model(root).ok()
    assert r.rebuilt == {"a.build"}, f"only the node that returns the row reads A: {r.rebuilt}"
    assert H.by_id(root)["Truba"]["qty"] == 12.0 and H.part_keys(root)["Truba"] != k1["Truba"]


def test_a_caller_gets_the_row_only_part_back_from_a_reused_callee(tmp_path):
    root = _proj(tmp_path / "p", {}, "from parts import caller\nout = caller.build()\n", files={
        "callee/__init__.py": "def build():\n    return [{'name': 'Bolt', 'process': 'bought', 'code': 'B', 'qty': 2}]\n",
        "caller/__init__.py": "from parts import callee\ndef build():\n    return callee.build()\n"})
    H.run_model(root).ok()
    r = H.run_model(root).ok()
    assert r.summary[0] == 0 and H.by_id(root)["Bolt"]["qty"] == 2


def _derived_with_row(tmp_path):
    root = H.make_derived_model(tmp_path / "proj")
    parts = H.read_parts(root)
    parts.append({"id": "Truba_50", "name": "Truba 50", "node": "boom", "func": "boom.build", "process": "bought", "key": "f" * 64,
                  "code": "TR50", "qty": 3, "unit": "m"})
    H._write_parts_json(root, parts)
    return root


def test_derive_bought_lists_the_row_with_qty_and_unit(tmp_path):
    root = _derived_with_row(tmp_path)
    d = H.run_derive(root, "bought").ok()
    row = [r for r in d.manifest["bought"] if r["code"] == "TR50"][0]
    assert row["qty"] == 3 and row["unit"] == "m"


def test_every_geometry_scope_skips_a_row_only_part(tmp_path):
    root = _derived_with_row(tmp_path)
    for scope in ("print", "step", "viewer"):
        d = H.run_derive(root, scope).ok()
        assert "Truba_50" not in [e["id"] for e in d.manifest["parts"]], scope
    if H.SHOP_VENV.exists():
        d = H.run_derive(root, "laser").ok()
        assert "Truba_50" not in [e["id"] for e in d.manifest["parts"]]


def test_derive_gc_keeps_working_with_a_row_only_part(tmp_path):
    root = _derived_with_row(tmp_path)
    H.run_derive(root, "viewer").ok()
    assert H.run_derive(root, "viewer").ok().computed == 0


# ---------------------------------------------------------------- loud errors
def test_a_tuple_item_is_a_hard_error_naming_node_and_item(tmp_path):
    root = _row_project(tmp_path, "[('plate', Box(5, 5, 1))]")
    r = H.run_model(root)
    assert r.rc != 0 and "a.build" in r.log and "plate" in r.log and "tuple" in r.log, r.log[-600:]


def test_a_dict_without_name_is_a_hard_error(tmp_path):
    root = _row_project(tmp_path, "[{'shape': Box(5, 5, 1), 'process': 'mill'}]")
    r = H.run_model(root)
    assert r.rc != 0 and "a.build" in r.log and "name" in r.log, r.log[-600:]


def test_a_non_bought_dict_without_shape_is_a_hard_error(tmp_path):
    root = _row_project(tmp_path, "[{'name': 'ghost', 'process': 'laser'}]")
    r = H.run_model(root)
    assert r.rc != 0 and "a.build" in r.log and "ghost" in r.log and "shape" in r.log, r.log[-600:]
