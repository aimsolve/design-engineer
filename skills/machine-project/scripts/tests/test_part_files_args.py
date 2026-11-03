"""Issue #15 (one part = one file, node = folder): part-file arguments, placements outside nodes, shared node helpers, viewer ids.

  A1  one part file called with two non-JSON arguments whose repr is the same but whose values differ (Spec(10), Spec(30)):
      different part names -> two parts.json entries with the right sizes; the same name -> a loud error naming the part.
      Never one entry carrying both placements on one geometry.
  A2  assemble.py placing a node twice (place(example.build(), ...) + place(example.build(), ...)) -> a loud non-zero exit saying
      the placement must be made in a node (never a silent qty that ignores the script's placements)
  A3  a shared node module parts/<node>/_common.py with a helper named build_cube(name, s) -> P(...) (or build_profile(s) -> a
      sketch), used by a part file -> builds, no "rebuilt on every run" warning, the helper is not in the `rebuilt:` line, a second
      run reuses everything
  A4  a part file `def build(w=40, at=Location())` called as plain build() -> the second run reuses it (no tick, no warning)
  A5  viewer scope: a placement after the first carries part_id = the part's parts.json id (the kit's part/<id> download); the
      first placement's id is the part id
  A6  integer arguments above 2**53 (2**53 vs 2**53 + 1) are two variants
Small projects are written inline with partfiles.write_project (the synthetic project's files switched off); A5 uses nproj.
"""
import json

import pytest

import helpers as H
import partfiles as F
from partfiles import new_baseline, nproj  # noqa: F401  (fixtures)


def _only(files, assemble):
    """write_project args for a project made only of `files` (the synthetic machine switched off, no legacy node)."""
    only = {k: None for k in F.FILES}
    only.update(files)
    return dict(files=only, assemble=assemble, legacy=False)


def _assemble(*nodes, items=None):
    return ("import sys\nfrom pathlib import Path\n\nsys.path.insert(0, str(Path(__file__).parent))\n"
            f"from parts import {', '.join(nodes)}\n\nitems = {items or ' + '.join(n + '.build()' for n in nodes)}\n")


def _size(root, entry):
    s = H.step_bbox(root / entry["step"]).size
    return tuple(round(v, 4) for v in (s.X, s.Y, s.Z))


def _no_rebuild_warning(m):
    assert "every run" not in m.log and "warn" not in m.err.lower(), \
        f"no 'rebuilt on every run' warning expected:\n{m.log[-1500:]}"


# ================================================================ A1 non-JSON arguments with one repr, different values
SPEC = '''
    class Spec:
        """A non-JSON argument whose repr does not tell its values apart."""
        def __init__(self, s):
            self.s = s

        def __repr__(self):
            return "Spec(...)"
    '''
CUBE_SPEC = '''
    from build123d import Box
    from geom import P
    {TICK}

    def build(spec):
        _tick("cube")
        return P({NAME}, Box(spec.s, spec.s, spec.s), "#808080", process="mill", material="S235JR")
    '''
SPEC_NODE = '''
    from build123d import Pos
    from geom import place
    from . import cube
    from ._spec import Spec

    def build():
        return [place(cube.build(Spec(10)), Pos(0, 0, 0)), place(cube.build(Spec(30)), Pos(100, 0, 0))]
    '''


def _spec_project(root, name_expr):
    files = {"specs/__init__.py": SPEC_NODE, "specs/_spec.py": SPEC, "specs/cube.py": CUBE_SPEC.replace("{NAME}", name_expr)}
    return F.write_project(root, **_only(files, _assemble("specs")))


def test_a1_same_repr_different_values_different_names_are_two_entries(tmp_path):
    root = _spec_project(tmp_path / "p", '"cube_%d" % spec.s')
    F.run_model(root).ok()
    ps = F.parts(root)
    assert {"cube_10", "cube_30"} <= set(ps), \
        f"Spec(10) and Spec(30) (same repr) must be two variants cube_10 and cube_30, parts.json ids: {sorted(ps)}"
    for pid, s in (("cube_10", 10.0), ("cube_30", 30.0)):
        assert ps[pid]["qty"] == 1 == len(F.placements(ps[pid])), f"{pid}: one placement expected: {ps[pid]}"
        assert _size(root, ps[pid]) == (s, s, s), f"{pid}: STEP size {_size(root, ps[pid])} != {s} cube"
    assert H.ticks(root).count("cube") == 2, f"cube.build must run once per value (2), ran {H.ticks(root).count('cube')}"


def test_a1_same_repr_different_values_same_name_is_a_loud_error(tmp_path):
    """Same part name for two different geometries: a loud error naming the part, never one entry with both placements."""
    root = _spec_project(tmp_path / "p", '"cube"')
    m = F.run_model(root)
    if m.rc == 0:
        ps = F.parts(root)
        merged = {k: v for k, v in ps.items() if len(v.get("placements") or []) >= 2}
        pytest.fail(f"exit 0: parts.json {sorted(ps)}; entries holding both placements on one geometry: {sorted(merged)}\n"
                    "two different geometries with one part name must be a loud error naming 'cube'")
    assert "cube" in m.log, f"the error must name the part 'cube':\n{m.log[-1500:]}"
    for bad in ("ImportError", "SyntaxError", "NameError", "ModuleNotFoundError"):
        assert bad not in m.log, f"the build crashed ({bad}) instead of reporting the contract error:\n{m.log[-1500:]}"


# ================================================================ A2 assemble.py placing a node
BLOCK = '''
    from build123d import Box
    from geom import P

    def build(width=100):
        return P("block", Box(width, width, width), "#9aa5b1", process="print", material="PETG")
    '''
EXAMPLE_NODE = '''
    from build123d import Pos
    from geom import place
    from . import block

    def build():
        return [place(block.build(), Pos(0, 0, 0)), place(block.build(), Pos(150, 0, 0))]
    '''


def test_a2_placement_in_assemble_is_a_loud_error_saying_it_belongs_in_a_node(tmp_path):
    asm = ("import sys\nfrom pathlib import Path\n\nsys.path.insert(0, str(Path(__file__).parent))\n"
           "from build123d import Pos\nfrom geom import place\nfrom parts import example\n\n"
           "items = place(example.build(), Pos(0, 0, 0)) + place(example.build(), Pos(0, 1000, 0))\n")
    root = F.write_project(tmp_path / "p", **_only({"example/__init__.py": EXAMPLE_NODE, "example/block.py": BLOCK}, asm))
    m = F.run_model(root)
    if m.rc == 0:
        ps = F.parts(root)
        pytest.fail(f"assemble.py placed the example node twice and the build exited 0 (block qty "
                    f"{ps.get('block', {}).get('qty')}, placements {[F.origin(p) for p in ps.get('block', {}).get('placements') or []]})"
                    ": it must fail loudly saying the placement must be made in a node")
    low = m.log.lower()
    assert "node" in low and "place" in low, f"the error must say the placement must be made in a node:\n{m.log[-1500:]}"
    for bad in ("ImportError", "SyntaxError", "NameError", "ModuleNotFoundError", "Traceback"):
        assert bad not in m.log, f"the build crashed ({bad}) instead of reporting the contract error:\n{m.log[-1500:]}"


# ================================================================ A3 a shared node helper named build_*
COMMON_CUBE = '''
    from geom import P
    from build123d import Box

    def build_cube(name, s):
        return P(name, Box(s, s, s), "#808080", process="mill", material="S235JR")
    '''
PART_CUBE = '''
    from ._common import build_cube
    {TICK}

    def build():
        _tick("cube")
        return build_cube("cube", 20)
    '''
COMMON_PROFILE = '''
    from build123d import Rectangle

    def build_profile(s):
        return Rectangle(s, s)
    '''
PART_PROFILE = '''
    from build123d import extrude
    from geom import P
    from ._common import build_profile
    {TICK}

    def build():
        _tick("cube")
        return P("cube", extrude(build_profile(20), 20), "#808080", process="mill", material="S235JR")
    '''
CUBE_NODE = '''
    from build123d import Pos
    from geom import place
    from . import cube

    def build():
        return [place(cube.build(), Pos(0, 0, 0)), place(cube.build(), Pos(50, 0, 0))]
    '''


@pytest.mark.parametrize("helper,common,part", [("build_cube", COMMON_CUBE, PART_CUBE),
                                                ("build_profile", COMMON_PROFILE, PART_PROFILE)],
                         ids=["build_cube_returns_P", "build_profile_returns_sketch"])
def test_a3_shared_node_helper_named_build_is_not_a_part(tmp_path, helper, common, part):
    files = {"shop/__init__.py": CUBE_NODE, "shop/_common.py": common, "shop/cube.py": part}
    root = F.write_project(tmp_path / "p", **_only(files, _assemble("shop")))
    m1 = F.run_model(root).ok()
    _no_rebuild_warning(m1)
    assert not any(helper in t for t in m1.rebuilt_tokens), f"the helper {helper} is in the rebuilt: line: {m1.rebuilt_tokens}"
    ps = F.parts(root)
    assert set(ps) == {"cube"} and ps["cube"]["qty"] == 2, f"one part cube placed twice expected: { {k: v.get('qty') for k, v in ps.items()} }"
    assert _size(root, ps["cube"]) == (20.0, 20.0, 20.0)
    H.clear_ticks(root)
    before = (root / "build/model/parts.json").read_bytes()
    m2 = F.run_model(root).ok()
    _no_rebuild_warning(m2)
    assert H.ticks(root) == [], f"the second run must reuse everything, part code ran: {H.ticks(root)}"
    assert m2.rebuilt_tokens == [], f"the second run rebuilt {m2.rebuilt_tokens}"
    assert (root / "build/model/parts.json").read_bytes() == before, "the second run changed parts.json"


# ================================================================ A4 a non-JSON default that the call does not pass
PART_LOC_DEFAULT = '''
    from build123d import Box, Location
    from geom import P
    {TICK}

    def build(w=40, at=Location()):
        _tick("plate")
        return P("plate", Box(w, 30, 2), "#aaaaaa", process="mill", material="S235JR")
    '''
PLATE_NODE = '''
    from build123d import Pos
    from geom import place
    from . import plate

    def build():
        return [place(plate.build(), Pos(0, 0, 0))]
    '''


def test_a4_non_json_default_not_passed_is_cached(tmp_path):
    files = {"deck/__init__.py": PLATE_NODE, "deck/plate.py": PART_LOC_DEFAULT}
    root = F.write_project(tmp_path / "p", **_only(files, _assemble("deck")))
    F.run_model(root).ok()
    assert "plate" in F.parts(root) and H.ticks(root).count("plate") == 1, f"precondition: plate built once: {H.ticks(root)}"
    H.clear_ticks(root)
    m2 = F.run_model(root).ok()
    assert H.ticks(root) == [], f"plate.build() (no arguments passed) must be reused on the second run, ran: {H.ticks(root)}"
    _no_rebuild_warning(m2)


# ================================================================ A5 viewer placements carry the part id
def test_a5_viewer_placements_after_the_first_carry_part_id(nproj):
    d = H.run_derive(nproj, "viewer").ok()
    vd = F.abs_path(nproj, d.manifest["viewer_dir"])
    vparts = {p["id"]: p for p in json.loads((vd / "parts.json").read_text())["parts"]}
    model_ids = set(F.parts(nproj))
    for pid, extra in (("rail", ["rail_2", "rail_3"]), ("foot", ["foot_2", "foot_3", "foot_4"]), ("knob", ["knob_2"])):
        assert pid in vparts and pid in model_ids, f"precondition: {pid} in the viewer ({sorted(vparts)}) and parts.json"
        assert vparts[pid].get("part_id", pid) == pid, f"the first placement {pid}: part_id {vparts[pid].get('part_id')!r}"
        for vid in extra:
            assert vid in vparts, f"{vid} missing from the viewer catalogue: {sorted(vparts)}"
            assert vid not in model_ids, f"precondition: {vid} is a placement, not a parts.json id"
            assert vparts[vid].get("part_id") == pid, \
                f"viewer entry {vid}: part_id {vparts[vid].get('part_id')!r} != {pid!r} (the kit's part/<id> download)"


# ================================================================ A6 integers above 2**53
BIG_PART = '''
    from build123d import Box
    from geom import P
    {TICK}

    def build(n=0):
        _tick("big")
        odd = n % 2 == 1
        return P("big_odd" if odd else "big_even", Box(10, 10, 20 if odd else 10), "#808080", process="mill", material="S235JR")
    '''
BIG_NODE = '''
    from build123d import Pos
    from geom import place
    from . import big

    def build():
        return [place(big.build(n=2 ** 53), Pos(0, 0, 0)), place(big.build(n=2 ** 53 + 1), Pos(50, 0, 0))]
    '''


def test_a6_integers_above_2_53_are_two_variants(tmp_path):
    files = {"huge/__init__.py": BIG_NODE, "huge/big.py": BIG_PART}
    root = F.write_project(tmp_path / "p", **_only(files, _assemble("huge")))
    F.run_model(root).ok()
    ps = F.parts(root)
    assert {"big_even", "big_odd"} <= set(ps), f"build(n=2**53) and build(n=2**53 + 1) merged: parts.json ids {sorted(ps)}"
    assert ps["big_even"]["qty"] == 1 and ps["big_odd"]["qty"] == 1, {k: v.get("qty") for k, v in ps.items()}
    assert _size(root, ps["big_odd"])[2] == 20.0 and _size(root, ps["big_even"])[2] == 10.0
    assert H.ticks(root).count("big") == 2, f"big.build must run once per value (2), ran {H.ticks(root).count('big')}"
