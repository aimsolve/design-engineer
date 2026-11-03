"""Issue #15 (one part = one file, node = folder): part-file calls whose arguments the variant key must see, and shapes a node
mutates in place.

  U1  a part file `def build(hole=Pos(10, 0, 0))` (plate minus a hole at `hole`) called with a Location argument: editing the
      node call to `hole=Pos(-30, 0, 0)` -> the stored part STEP (parts.json `step`) and `derive.py step` hold the hole at -30.
      Volume and bbox are the same in both runs by design, so only a point-in-solid test tells the two apart.
  U2  a part called with a numpy integer argument (np.int64(3)) is a keyed variant: the second run reuses it (no tick, no
      "every run" warning)
  U3  build(t=(1, 2)) and build(t=[1, 2]), with a part name that depends on type(t): two parts.json entries, never one
  U4  a node that calls `.move(Pos(0, 0, 999))` on a part's shape before placing it -> a loud non-zero exit naming the part
Each project is written inline with partfiles.write_project (the synthetic machine switched off, no legacy node).
"""
import pytest

import helpers as H
import partfiles as F


def _only(files, assemble):
    only = {k: None for k in F.FILES}
    only.update(files)
    return dict(files=only, assemble=assemble, legacy=False)


def _assemble(*nodes):
    return ("import sys\nfrom pathlib import Path\n\nsys.path.insert(0, str(Path(__file__).parent))\n"
            f"from parts import {', '.join(nodes)}\n\nitems = {' + '.join(n + '.build()' for n in nodes)}\n")


def _solid(path):
    from build123d import import_step
    shape = import_step(str(path))
    solids = shape.solids()
    assert len(solids) == 1, f"{path}: one solid expected, got {len(solids)}"
    return solids[0]


def _no_rebuild_warning(m):
    assert "every run" not in m.log and "warn" not in m.err.lower(), \
        f"no 'rebuilt on every run' warning expected:\n{m.log[-1500:]}"


# ================================================================ U1 a Location argument changes the geometry
HOLE_PART = '''
    from build123d import Box, Cylinder, Pos
    from geom import P
    {TICK}

    def build(hole=Pos(10, 0, 0)):
        _tick("plate")
        return P("plate", Box(100, 100, 5) - hole * Cylinder(3, 10), "#aaaaaa", process="mill", material="S235JR")
    '''
HOLE_NODE = '''
    from build123d import Pos
    from geom import place
    from . import plate

    def build():
        return [place(plate.build(hole=Pos({X}, 0, 0)), Pos(0, 0, 0))]
    '''


def _hole_at(solid):
    """'+10' / '-30' / something else: which of the two hole positions is empty in the part's own coordinates."""
    from build123d import Vector
    at10, at30 = solid.is_inside(Vector(10, 0, 0)), solid.is_inside(Vector(-30, 0, 0))
    return {(False, True): "+10", (True, False): "-30"}.get((at10, at30), f"inside(+10)={at10}, inside(-30)={at30}")


def test_u1_editing_a_location_argument_moves_the_hole_in_the_part_step(tmp_path):
    files = {"deck/__init__.py": HOLE_NODE.replace("{X}", "10"), "deck/plate.py": HOLE_PART}
    root = F.write_project(tmp_path / "p", **_only(files, _assemble("deck")))
    F.run_model(root).ok()
    e1 = F.parts(root)["plate"]
    s1 = _solid(root / e1["step"])
    assert _hole_at(s1) == "+10", f"precondition: the first run's plate STEP has its hole at x=+10: {_hole_at(s1)}"
    vol1, size1 = s1.volume, s1.bounding_box().size

    H.edit(F.part_file(root, "deck/__init__.py"), "hole=Pos(10, 0, 0)", "hole=Pos(-30, 0, 0)")
    F.run_model(root).ok()
    e2 = F.parts(root)["plate"]
    s2 = _solid(root / e2["step"])
    assert s2.volume == pytest.approx(vol1, rel=1e-6) and tuple(s2.bounding_box().size) == pytest.approx(tuple(size1)), \
        "by design both plates have the same volume and bbox"
    assert _hole_at(s2) == "-30", \
        f"after editing the node call to hole=Pos(-30, 0, 0) the stored part STEP {e2['step']} must have the hole at x=-30, " \
        f"got {_hole_at(s2)} (the old variant was reused: the Location argument is not in the variant key)"

    d = H.run_derive(root, "step").ok()
    steps = [f for f in H.file_list(H.entry(d, "plate"), root) if f.suffix in (".step", ".stp")]
    assert len(steps) == 1, f"one part STEP expected for plate in the step download: {steps}"
    assert _hole_at(_solid(steps[0])) == "-30", f"derive.py step: the plate STEP {steps[0]} must have the hole at x=-30"


# ================================================================ U2 a numpy integer argument is a keyed variant
NP_PART = '''
    from build123d import Box
    from geom import P
    {TICK}

    def build(n=1):
        _tick("bar")
        return P("bar_%d" % int(n), Box(10 * n, 10, 10), "#808080", process="mill", material="S235JR")
    '''
NP_NODE = '''
    import numpy as np
    from build123d import Pos
    from geom import place
    from . import bar

    def build():
        return [place(bar.build(np.int64(3)), Pos(0, 0, 0))]
    '''


def test_u2_numpy_integer_argument_is_reused_on_the_second_run(tmp_path):
    files = {"rack/__init__.py": NP_NODE, "rack/bar.py": NP_PART}
    root = F.write_project(tmp_path / "p", **_only(files, _assemble("rack")))
    m1 = F.run_model(root).ok()
    ps = F.parts(root)
    assert "bar_3" in ps and H.ticks(root).count("bar") == 1, f"precondition: bar_3 built once: {sorted(ps)}, {H.ticks(root)}"
    _no_rebuild_warning(m1)
    H.clear_ticks(root)
    m2 = F.run_model(root).ok()
    _no_rebuild_warning(m2)
    assert H.ticks(root) == [], f"bar.build(np.int64(3)) must be reused on the second run, part code ran: {H.ticks(root)}"
    assert not m2.rebuilt("rack/bar"), f"the second run rebuilt bar: {m2.rebuilt_tokens}"


# ================================================================ U3 a tuple and a list with the same JSON are two variants
TL_PART = '''
    from build123d import Box
    from geom import P
    {TICK}

    def build(t=(1, 2)):
        _tick("seq")
        tup = type(t) is tuple
        return P("seq_tuple" if tup else "seq_list", Box(10, 10, 10 if tup else 20), "#808080", process="mill", material="S235JR")
    '''
TL_NODE = '''
    from build123d import Pos
    from geom import place
    from . import seq

    def build():
        return [place(seq.build(t=(1, 2)), Pos(0, 0, 0)), place(seq.build(t=[1, 2]), Pos(50, 0, 0))]
    '''


def test_u3_tuple_and_list_arguments_are_two_entries(tmp_path):
    files = {"pair/__init__.py": TL_NODE, "pair/seq.py": TL_PART}
    root = F.write_project(tmp_path / "p", **_only(files, _assemble("pair")))
    F.run_model(root).ok()
    ps = F.parts(root)
    assert {"seq_tuple", "seq_list"} <= set(ps), f"build(t=(1, 2)) and build(t=[1, 2]) merged: parts.json ids {sorted(ps)}"
    assert ps["seq_tuple"]["qty"] == 1 and ps["seq_list"]["qty"] == 1, {k: v.get("qty") for k, v in ps.items()}
    assert H.step_bbox(root / ps["seq_list"]["step"]).size.Z == pytest.approx(20)
    assert H.step_bbox(root / ps["seq_tuple"]["step"]).size.Z == pytest.approx(10)


# ================================================================ U4 a node mutating a part's shape in place
MOVE_PART = '''
    from build123d import Box
    from geom import P
    {TICK}

    def build():
        _tick("lid")
        return P("lid", Box(40, 40, 4), "#808080", process="mill", material="S235JR")
    '''
MOVE_NODE = '''
    from build123d import Pos
    from geom import place
    from . import lid

    def build():
        p = lid.build()
        p["shape"].move(Pos(0, 0, 999))
        return [place(p, Pos(0, 0, 0))]
    '''


def test_u4_node_moving_a_parts_shape_is_a_loud_error_naming_the_part(tmp_path):
    files = {"box/__init__.py": MOVE_NODE, "box/lid.py": MOVE_PART}
    root = F.write_project(tmp_path / "p", **_only(files, _assemble("box")))
    m = F.run_model(root)
    if m.rc == 0:
        lid = F.parts(root).get("lid", {})
        z = H.step_bbox(root / lid["step"]).center().Z if lid.get("step") else None
        pytest.fail(f"the node moved lid's shape in place (.move(Pos(0, 0, 999))) and the build exited 0 (lid STEP centre z={z}):"
                    " it must fail loudly naming the part 'lid' (change the part in its file, never the placed copy)")
    assert "lid" in m.log, f"the error must name the part 'lid':\n{m.log[-1500:]}"
    for bad in ("ImportError", "SyntaxError", "NameError", "ModuleNotFoundError"):
        assert bad not in m.log, f"the build crashed ({bad}) instead of reporting the contract error:\n{m.log[-1500:]}"
