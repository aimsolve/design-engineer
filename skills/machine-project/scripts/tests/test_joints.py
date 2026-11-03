"""Issue #16 «Ports on parts and assembly by joints»: cad_incremental --model on a project docked by stock build123d joints.

Done-when map:
  DW1  every part ends where its joints put it (here: the placements in parts.json; the STEP / viewer side is test_joints_derive.py)
  DW2  editing a size of a part that moves its joint moves everything docked to it (transitively) on the next build, and the docked
       part files are not rebuilt, only re-placed
  DW3  template screws placed N times = ONE bought entry with qty N, size spelled "M5x12 DIN 912" (derive side: test_joints_derive.py)
  DW5  parts.json carries the joint graph: per part `joints` {label: type, loc, axis, range}; per placement `joint` {to, at, by, type,
       value}; the root placement has none
  C    contract items those rest on: independent placements of one variant (also when reused), a no-change run reads no STEP and
       writes byte-identical parts.json, reused parts expose the same .joints, out-of-range values fail naming the part / joint
The project is jointfiles.py's synthetic machine (read its docstring for every expected coordinate).
"""
import hashlib
import json
from pathlib import Path

import pytest

import helpers as H
import partfiles as F
import jointfiles as J
from jointfiles import joint_baseline, jproj  # noqa: F401  (fixtures)


def _ok(joint_baseline):
    root, m, ticks = joint_baseline
    m.ok()
    return root, m, ticks


def _approx3(a, b, tol=1e-6):
    return all(abs(float(x) - float(y)) <= tol for x, y in zip(a, b))


# ================================================================ DW1 (model side): placements where the joints put them
def test_cold_build_of_a_jointed_project_succeeds_with_every_part(joint_baseline):
    root, m, _ = _ok(joint_baseline)
    ids = set(H.by_id(root))
    assert {"base", "rail", "slider", "arm"} <= ids, f"parts.json ids: {sorted(ids)}"
    assert len(J.screw_entries(root, "M5x12")) == 1 and len(J.screw_entries(root, "M4x8")) == 1, sorted(ids)


def test_part_steps_stay_in_their_own_coordinates(joint_baseline):
    """A docked part's STEP is still the part at its own origin: the joint only moves the placement."""
    root, _, _ = _ok(joint_baseline)
    ps = H.by_id(root)
    bb = H.step_bbox(root / ps["rail"]["step"])
    assert _approx3((bb.min.X, bb.min.Y, bb.min.Z), (-80, -10, 0)) and _approx3((bb.max.X, bb.max.Y, bb.max.Z), (80, 10, 20)), \
        f"rail STEP must be the rail in its own coordinates, got {bb.min} .. {bb.max}"
    bb = H.step_bbox(root / ps["arm"]["step"])
    assert _approx3((bb.min.X, bb.min.Y, bb.min.Z), (0, -5, 0)) and _approx3((bb.max.X, bb.max.Y, bb.max.Z), (50, 5, 5)), \
        f"arm STEP must be the arm in its own coordinates (not turned), got {bb.min} .. {bb.max}"


@pytest.mark.parametrize("name", ["base", "rail", "slider", "arm"])
def test_placement_origin_is_where_the_joints_put_the_part(joint_baseline, name):
    """rail on base.rail; slider on rail.carriage at position 30 (through the root); arm on slider.hinge at 90 deg (inside the
    head sub-node, moved with it)."""
    root, _, _ = _ok(joint_baseline)
    pm = J.placement_map(root)
    assert name in pm, f"no placement named {name!r}: {sorted(pm)}"
    got = J.translation(pm[name][1]["loc"])
    assert _approx3(got, J.ORIGIN[name]), f"{name}: placement origin {got}, the joints put it at {J.ORIGIN[name]}"


def test_revolute_angle_turns_the_docked_part(joint_baseline):
    """slider.hinge <- arm.pivot at angle=90 about Z: the arm's +X axis points along world +Y."""
    root, _, _ = _ok(joint_baseline)
    rot = J.rotation(J.placement_map(root)["arm"][1]["loc"])
    x_axis = tuple(rot[r][0] for r in range(3))
    assert _approx3(x_axis, (0, 1, 0)), f"arm turned 90 deg about Z expected (its X along world Y), rotation {rot}"


def test_one_screw_variant_placed_through_several_joints_gets_independent_placements(joint_baseline):
    """Each build() call hands out its own shape: 4 M5 screws docked into hole_1..4 = 4 placements at 4 different holes."""
    root, _, _ = _ok(joint_baseline)
    for spelled, holes in (("M5x12", J.HOLES_M5), ("M4x8", J.HOLES_M4)):
        e = J.screw_entries(root, spelled)
        assert len(e) == 1, f"one parts.json entry for {spelled} expected, got {[p['id'] for p in e]}"
        pls = F.placements(e[0])
        assert len(pls) == len(holes), f"{spelled}: {len(holes)} placements expected, got {len(pls)}"
        xy = sorted(tuple(round(v, 6) for v in J.translation(pl["loc"])[:2]) for pl in pls)
        assert xy == sorted((float(x), float(y)) for x, y in holes.values()), \
            f"{spelled}: every placement must sit in its own hole, got XY {xy}"


def test_screw_shaft_goes_into_the_hole_from_under_the_head(joint_baseline):
    """The template's `head` joint is under the head, Z from shaft to head: docked on a hole joint (Z out of the plate) the shaft
    goes down into the base: the screw's lowest point is at z = T - length (T - 12 for M5x12), the head above z = T."""
    root, _, _ = _ok(joint_baseline)
    e = J.screw_entries(root, "M5x12")[0]
    from build123d import Location, import_step
    from OCP.gp import gp_Trsf
    sh = import_step(str(root / e["step"]))
    for pl in F.placements(e):
        m = F.matrix(pl["loc"])
        trsf = gp_Trsf()
        trsf.SetValues(*m[0][:4], *m[1][:4], *m[2][:4])
        loc = Location(trsf)
        bb = sh.moved(loc).bounding_box()
        assert abs(bb.min.Z - (J.T - 12)) < 1e-3, f"{pl['name']}: M5x12 shaft end must be at z={J.T - 12}, got {bb.min.Z}"
        assert bb.max.Z > J.T + 1e-3, f"{pl['name']}: the head must stand above the plate (z > {J.T}), got max z {bb.max.Z}"


# ================================================================ DW5: the joint graph in parts.json
def _joints(root, pid):
    p = H.by_id(root)[pid]
    js = p.get("joints")
    assert isinstance(js, dict), f"{pid}: parts.json entry has no `joints` dict: {sorted(p)}"
    return js


def test_part_entries_list_their_rigid_joints_in_own_coordinates(joint_baseline):
    root, _, _ = _ok(joint_baseline)
    js = _joints(root, "base")
    assert set(js) == {"rail"} | set(J.HOLES), f"base joints: {sorted(js)}"
    assert js["rail"]["type"] == "rigid"
    assert _approx3(J.translation(js["rail"]["loc"]), (100, 50, J.T)), js["rail"]
    for h, (x, y) in J.HOLES.items():
        assert js[h]["type"] == "rigid" and _approx3(J.translation(js[h]["loc"]), (x, y, J.T)), (h, js[h])
    assert _joints(root, "arm") == {"pivot": _joints(root, "arm")["pivot"]} and _joints(root, "arm")["pivot"]["type"] == "rigid"
    assert _approx3(J.translation(_joints(root, "arm")["pivot"]["loc"]), (0, 0, 0))


def test_linear_joint_carries_axis_and_range(joint_baseline):
    root, _, _ = _ok(joint_baseline)
    js = _joints(root, "rail")
    assert set(js) == {"bottom", "carriage"}, sorted(js)
    assert js["bottom"]["type"] == "rigid"
    c = js["carriage"]
    assert c["type"] == "linear", c
    assert [float(x) for x in c["range"]] == [-60.0, 60.0], c
    assert J.axis_dir(c) == (1.0, 0.0, 0.0), c
    assert _approx3(J.translation(c["loc"]), (0, 0, 20)), c
    o = J.axis_origin(c)
    assert o is None or _approx3(o, (0, 0, 20)), c


def test_revolute_joint_carries_axis_and_range(joint_baseline):
    root, _, _ = _ok(joint_baseline)
    js = _joints(root, "slider")
    assert set(js) == {"mount", "hinge"}, sorted(js)
    h = js["hinge"]
    assert h["type"] == "revolute", h
    assert [float(x) for x in h["range"]] == [0.0, 90.0], h
    assert J.axis_dir(h) == (0.0, 0.0, 1.0), h
    assert _approx3(J.translation(h["loc"]), (0, 0, 10)), h


def test_template_screw_carries_its_head_joint(joint_baseline):
    root, _, _ = _ok(joint_baseline)
    e = J.screw_entries(root, "M5x12")[0]
    js = e.get("joints")
    assert isinstance(js, dict) and js.get("head", {}).get("type") == "rigid", f"screw entry joints: {js}"


EXPECTED_EDGES = {
    "rail": {"to": "base", "at": "rail", "by": "bottom", "type": "rigid"},
    "slider": {"to": "rail", "at": "carriage", "by": "mount", "type": "linear", "value": 30},
    "arm": {"to": "slider", "at": "hinge", "by": "pivot", "type": "revolute", "value": 90},
}


@pytest.mark.parametrize("name", sorted(EXPECTED_EDGES))
def test_placement_records_who_it_is_docked_to_and_by_which_joints(joint_baseline, name):
    root, _, _ = _ok(joint_baseline)
    pm = J.placement_map(root)
    assert name in pm, sorted(pm)
    j = pm[name][1].get("joint")
    assert isinstance(j, dict), f"placement {name}: no `joint` record: {pm[name][1]}"
    want = EXPECTED_EDGES[name]
    got = {k: j.get(k) for k in ("to", "at", "by", "type")}
    assert got == {k: want[k] for k in ("to", "at", "by", "type")}, f"placement {name}: joint {j}, expected {want}"
    if "value" in want:
        assert j.get("value") is not None and abs(float(j["value"]) - want["value"]) < 1e-9, f"{name}: value {j.get('value')}"
    else:
        assert j.get("value") is None, f"{name}: a rigid joint has no value, got {j}"


def test_every_screw_placement_is_docked_to_its_own_hole(joint_baseline):
    root, _, _ = _ok(joint_baseline)
    seen = {}
    for spelled, holes in (("M5x12", J.HOLES_M5), ("M4x8", J.HOLES_M4)):
        for pl in F.placements(J.screw_entries(root, spelled)[0]):
            j = pl.get("joint")
            assert isinstance(j, dict), f"screw placement {pl['name']} has no joint: {pl}"
            assert (j.get("to"), j.get("by"), j.get("type")) == ("base", "head", "rigid"), j
            assert j.get("at") in holes, f"{pl['name']} docked at {j.get('at')}, expected one of {sorted(holes)}"
            x, y = holes[j["at"]]
            assert _approx3(J.translation(pl["loc"])[:2], (x, y)), f"{pl['name']}: placed off its hole {j['at']}"
            seen[j["at"]] = pl["name"]
    assert set(seen) == set(J.HOLES), f"every hole holds exactly one screw: {seen}"


def test_only_the_root_placement_has_no_joint(joint_baseline):
    root, _, _ = _ok(joint_baseline)
    loose = [name for name, (_, pl) in J.placement_map(root).items() if "joint" not in pl]
    assert loose == ["base"], f"exactly one placement without a joint parent (the root, base) expected, got {loose}"


# ================================================================ reuse: no-change run
def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def test_no_change_rebuild_is_byte_identical_and_reruns_nothing(joint_baseline, jproj):
    root, _, _ = _ok(joint_baseline)
    before = _sha(root / "build/model/parts.json")
    H.clear_ticks(jproj)
    m = F.run_model(jproj).ok()
    r = H.Run(type("CP", (), {"returncode": m.rc, "stdout": m.out, "stderr": m.err})())
    assert r.summary and r.summary[0] == 0, f"a no-change run rebuilds nothing: {m.log[-800:]}"
    assert not [t for t in H.ticks(jproj) if t in J.PART_FILES], f"no part file may run: {H.ticks(jproj)}"
    assert _sha(jproj / "build/model/parts.json") == before, "a no-change run must write a byte-identical parts.json"


def test_no_change_rebuild_of_a_jointed_project_reads_no_step(jproj):
    """Docking reused (lazy) parts by their joints must not import their STEPs (test_lazy_reuse's promise)."""
    import test_lazy_reuse as L
    assert (jproj / "build/model/parts.json").is_file(), "precondition: the cold build made a model"
    L._reset(jproj)
    r, reads = L._run(jproj)
    r.ok()
    assert r.summary[0] == 0, r.log[-600:]
    assert reads == 0, f"{reads} part STEPs were imported on a no-change run of the jointed project"


def test_a_reused_part_exposes_the_same_joints_to_node_code(tmp_path):
    """Node code on a no-change run sees the same joint labels, types and (docked) joint locations as on the cold run."""
    root = J.write_project(tmp_path / "p", assemble=J.PROBE_ASSEMBLE)
    F.run_model(root).ok()
    cold = json.loads((root / "joints_probe.json").read_text())
    (root / "joints_probe.json").unlink()
    m = F.run_model(root).ok()
    warm = json.loads((root / "joints_probe.json").read_text())
    assert H.Run(type("CP", (), {"returncode": m.rc, "stdout": m.out, "stderr": m.err})()).summary[0] == 0, m.log[-600:]
    assert any(v["joints"] for v in cold.values()), f"the cold run saw no joints at all: {cold}"
    assert warm == cold, "reused parts must expose the same .joints and locations as freshly built ones"


# ================================================================ DW2: a size change moves everything docked to it
def _origins(root):
    return {name: J.translation(pl["loc"]) for name, (_, pl) in J.placement_map(root).items()}


def _rotations(root):
    return {name: J.rotation(pl["loc"]) for name, (_, pl) in J.placement_map(root).items()}


def test_thicker_base_moves_everything_docked_to_it_without_rebuilding_them(jproj):
    """Edit frame/base.py T 10 -> 16: rail (on base), slider (on rail, via the root), arm (on slider, inside head) and all six
    screws (in base holes) rise by 6 mm; their part files do not run, their STEP keys stay; only base is rebuilt."""
    assert (jproj / "build/model/parts.json").is_file(), "precondition: the cold build made a model"
    o0, r0, k0 = _origins(jproj), _rotations(jproj), H.part_keys(jproj)
    H.edit(F.part_file(jproj, "frame/base.py"), "T = 10.0", "T = 16.0")
    H.clear_ticks(jproj)
    m = F.run_model(jproj).ok()
    o1, r1, k1 = _origins(jproj), _rotations(jproj), H.part_keys(jproj)
    assert set(o1) == set(o0), f"placements changed: {sorted(set(o0) ^ set(o1))}"
    for name in o0:
        want = o0[name] if name == "base" else (o0[name][0], o0[name][1], o0[name][2] + 6.0)
        assert _approx3(o1[name], want), f"{name}: origin {o0[name]} -> {o1[name]}, expected {want}"
        assert r1[name] == r0[name], f"{name}: the rotation must not change"
    assert k1["base"] != k0["base"], "base was edited: its STEP must be rebuilt"
    for pid in k0:
        if pid != "base":
            assert k1[pid] == k0[pid], f"{pid}: docked part re-built (new key) although only its placement moved"
    ran = H.ticks(jproj)
    assert "base" in ran, ran
    for p in J.DOCKED:
        assert p not in ran, f"{p}.py ran again: docked parts must only be re-placed, ticks {ran}"
        assert not m.rebuilt(("frame/" if p == "rail" else "head/") + p), f"{p} listed as rebuilt: {m.rebuilt_tokens}"
    assert not m.rebuilt("frame/screw"), f"the screws were rebuilt: {m.rebuilt_tokens}"
    assert m.rebuilt("frame/base"), m.rebuilt_tokens


def test_a_second_size_edit_keeps_reused_screws_independent(jproj):
    """After base is rebuilt the screw variants are reused (lazily read): their placements must still be one per hole."""
    assert (jproj / "build/model/parts.json").is_file(), "precondition: the cold build made a model"
    H.edit(F.part_file(jproj, "frame/base.py"), "T = 10.0", "T = 13.0")
    F.run_model(jproj).ok()
    H.edit(F.part_file(jproj, "frame/base.py"), "T = 13.0", "T = 14.0")
    F.run_model(jproj).ok()
    pts = []
    for spelled in ("M5x12", "M4x8"):
        pts += [J.translation(pl["loc"]) for pl in F.placements(J.screw_entries(jproj, spelled)[0])]
    assert len(set(pts)) == 6, f"six screws at six different places expected, got {pts}"
    assert sorted((round(x, 6), round(y, 6)) for x, y, _ in pts) == sorted((float(x), float(y)) for x, y in J.HOLES.values())


def test_moving_the_rail_joint_on_the_base_moves_the_head_with_it(jproj):
    """A joint location (not a size) edited in base.py: the rail and the whole head sub-assembly follow; arm keeps its turn."""
    assert (jproj / "build/model/parts.json").is_file(), "precondition: the cold build made a model"
    o0 = _origins(jproj)
    H.edit(F.part_file(jproj, "frame/base.py"), 'RigidJoint("rail", s, Location((100, 50, T)))',
           'RigidJoint("rail", s, Location((90, 50, T)))')
    F.run_model(jproj).ok()
    o1 = _origins(jproj)
    for name in J.DOCKED:
        assert _approx3(o1[name], (o0[name][0] - 10, o0[name][1], o0[name][2])), f"{name}: {o0[name]} -> {o1[name]}"
    rot = J.rotation(J.placement_map(jproj)["arm"][1]["loc"])
    assert _approx3(tuple(rot[r][0] for r in range(3)), (0, 1, 0)), f"arm must stay turned by the hinge: {rot}"


# ================================================================ joint values outside the range
def _range_lines(m):
    return [ln for ln in m.log.splitlines() if "range" in ln.lower()]


def test_linear_position_outside_its_range_fails_naming_part_and_joint(tmp_path):
    root = J.write_project(tmp_path / "p")
    H.edit(H.cad_dir(root) / "parts" / "__init__.py", "position=30", "position=100")
    m = F.run_model(root).failed()
    lines = _range_lines(m)
    assert any("rail" in ln and "carriage" in ln for ln in lines), \
        f"the error must say the value is out of range AND name the part (rail) and its joint (carriage):\n{m.log[-1500:]}"


def test_revolute_angle_outside_its_range_fails_naming_part_and_joint(tmp_path):
    root = J.write_project(tmp_path / "p")
    H.edit(H.cad_dir(root) / "parts" / "head" / "__init__.py", "angle=90", "angle=120")
    m = F.run_model(root).failed()
    lines = _range_lines(m)
    assert any("hinge" in ln and ("slider" in ln or "arm" in ln) for ln in lines), \
        f"the error must say the angle is out of range AND name the part and its joint (slider / hinge):\n{m.log[-1500:]}"


def test_value_inside_the_range_at_its_limit_builds(tmp_path):
    """The range is inclusive: position=60 (the upper limit) builds and the slider sits at x = 100 + 60."""
    root = J.write_project(tmp_path / "p")
    H.edit(H.cad_dir(root) / "parts" / "__init__.py", "position=30", "position=60")
    F.run_model(root).ok()
    assert _approx3(J.translation(J.placement_map(root)["slider"][1]["loc"]), (160, 50, 30))
    assert J.placement_map(root)["slider"][1]["joint"]["value"] == pytest.approx(60)


# ================================================================ the template itself
def test_framework_ships_a_screw_part_template():
    assert J.SCREW_TEMPLATE.is_file(), f"no screw part template at {J.SCREW_TEMPLATE}"
    import ast
    tree = ast.parse(J.SCREW_TEMPLATE.read_text())
    fn = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "build"]
    assert fn, "the screw template must define build()"
    args = [a.arg for a in fn[0].args.args]
    assert args[:3] == ["size", "length", "std"], f"build(size, length, std) expected, got build({', '.join(args)})"


# ================================================================ a sub-node docked by the parent at several of its parts
SHEET = '''
from build123d import Align, Box, Location, RigidJoint
from geom import P
{TICK}


def build(name="sheet_a"):
    _tick("sheet")
    s = Box(40, 40, 2, align=(Align.MIN, Align.MIN, Align.MIN))
    RigidJoint("mount", s, Location((0, 0, 0)))
    RigidJoint("h1", s, Location((10, 10, 2)))
    RigidJoint("h2", s, Location((30, 30, 2)))
    return P(name, s, "#99aa99", process="laser", material="S235JR", thickness=2.0)
'''

PANELS = '''
from geom import place
from parts.frame import screw
from . import sheet
{TICK}


def build():
    _tick("node_panels")
    out = []
    for name in ("sheet_a", "sheet_b"):
        sh = sheet.build(name=name)
        out.append(place(sh))
        for h in ("h1", "h2"):
            s = screw.build("M4", 8, "DIN 912")
            sh["shape"].joints[h].connect_to(s["shape"].joints["head"])
            out.append(place(s))
    return out
'''

ROOT_PANELS = '''
from geom import place
from . import frame, head, panels


def build():
    fr = frame.build()
    hd = head.build()
    pn = panels.build()
    rail = next(p for p in fr if p["name"] == "rail")
    base = next(p for p in fr if p["name"] == "base")
    slider = next(p for p in hd if p["name"] == "slider")
    rail["shape"].joints["carriage"].connect_to(slider["shape"].joints["mount"], position=30)
    a = next(p for p in pn if p["name"] == "sheet_a")
    b = next(p for p in pn if p["name"] == "sheet_b")
    base["shape"].joints["pad_a"].connect_to(a["shape"].joints["mount"])
    base["shape"].joints["pad_b"].connect_to(b["shape"].joints["mount"])
    return fr + place(hd) + place(pn)
'''

BASE_PADS = J.FILES["frame/base.py"].replace(
    '            RigidJoint("rail", s, Location((100, 50, T)))\n',
    '            RigidJoint("rail", s, Location((100, 50, T)))\n'
    '            RigidJoint("pad_a", s, Location((10, 10, T)))\n'
    '            RigidJoint("pad_b", s, Location((150, 60, T), (0, 0, 90)))\n')
# world positions (T = 10): sheet_a at (10, 10, 10) unturned; sheet_b at (150, 60, 10) turned 90 deg about Z.
# sheet holes (10, 10) / (30, 30) on top (z = 2): A -> (20, 20, 12), (40, 40, 12); B -> (140, 70, 12), (120, 90, 12)
SHEET_AT = {"sheet_a": (10, 10, 10), "sheet_b": (150, 60, 10)}
SHEET_SCREWS = {("sheet_a", "h1"): (20, 20), ("sheet_a", "h2"): (40, 40),
                ("sheet_b", "h1"): (140, 70), ("sheet_b", "h2"): (120, 90)}


def _build_panels(shared):
    root = J.write_project(shared / "proj", files={"__init__.py": ROOT_PANELS, "frame/base.py": BASE_PADS,
                                                    "panels/__init__.py": PANELS, "panels/sheet.py": SHEET})
    m = F.run_model(root)
    (shared / "cold.json").write_text(json.dumps({"rc": m.rc, "log": m.log}))


@pytest.fixture(scope="module")
def panels_proj(tmp_path_factory):
    import conftest
    shared = conftest._shared(tmp_path_factory, "joints_panels", _build_panels)
    d = json.loads((shared / "cold.json").read_text())
    assert d["rc"] == 0, f"--model build of the panels project failed:\n{d['log'][-2500:]}"
    return shared / "proj"


def _assert_panels_docked(root):
    pm = J.placement_map(root)
    for name, at in SHEET_AT.items():
        assert name in pm, f"no placement {name!r}: {sorted(pm)}"
        assert _approx3(J.translation(pm[name][1]["loc"]), at), f"{name}: origin {J.translation(pm[name][1]['loc'])}, docked at {at}"
        j = pm[name][1].get("joint") or {}
        assert (j.get("to"), j.get("at"), j.get("by")) == ("base", "pad_" + name[-1], "mount"), f"{name}: joint {j}"
    x_b = tuple(J.rotation(pm["sheet_b"][1]["loc"])[r][0] for r in range(3))
    assert _approx3(x_b, (0, 1, 0)), f"sheet_b must be turned 90 deg about Z by pad_b, X axis -> {x_b}"
    got = {}
    for name, (_, pl) in pm.items():
        j = pl.get("joint") or {}
        if j.get("to") in SHEET_AT:
            got[(j["to"], j.get("at"))] = (name, J.translation(pl["loc"]))
    assert set(got) == set(SHEET_SCREWS), f"each sheet hole holds one screw docked to that sheet: {sorted(got)}"
    for key, (x, y) in SHEET_SCREWS.items():
        name, o = got[key]
        assert _approx3(o[:2], (x, y)), f"{name} in {key}: XY {o[:2]}, its sheet's hole is at {(x, y)}"
    m4 = J.screw_entries(root, "M4x8")
    assert len(m4) == 1 and m4[0]["qty"] == 2 + 4, f"one M4x8 entry, qty 6 (2 in base + 4 in sheets): {m4}"


def test_sub_node_docked_at_several_parts_moves_each_with_its_own_children(panels_proj):
    """panels returns two independent sheets, each with two screws docked inside the sub-node; the root docks sheet_a on
    base.pad_a and sheet_b on base.pad_b (turned), then place(panels) without a Location: each sheet lands on its pad and its
    screws stay in its holes."""
    _assert_panels_docked(panels_proj)


def test_sub_node_docked_at_several_parts_holds_on_a_no_change_rebuild(panels_proj, tmp_path):
    root = Path(F._clean_copy(panels_proj, tmp_path / "proj"))
    before = _sha(root / "build/model/parts.json")
    m = F.run_model(root).ok()
    r = H.Run(type("CP", (), {"returncode": m.rc, "stdout": m.out, "stderr": m.err})())
    assert r.summary and r.summary[0] == 0, f"a no-change run rebuilds nothing: {m.log[-800:]}"
    _assert_panels_docked(root)
    assert _sha(root / "build/model/parts.json") == before, "a no-change run must write a byte-identical parts.json"


# ================================================================ a docking the placed parts no longer show fails the build
FRAME = J.FILES["frame/__init__.py"]
DOCK_RAIL = '            b["shape"].joints["rail"].connect_to(r["shape"].joints["bottom"])\n'
PLACE_BOTH = '            out = [place(b, Pos(0, 0, 0)), place(r)]\n'
assert DOCK_RAIL in FRAME and PLACE_BOTH in FRAME


def _project_lines(root):
    """Every stripped source line of the project: a log line equal to one is a traceback echo, not a message."""
    out = set()
    for p in H.cad_dir(root).rglob("*.py"):
        out |= {ln.strip() for ln in p.read_text().splitlines() if ln.strip()}
    return out


def _message_lines(root, m):
    src = _project_lines(root)
    return [ln for ln in m.log.splitlines() if ln.strip() and ln.strip() not in src]


def _fails_naming(root, words):
    m = F.run_model(root).failed()
    lines = _message_lines(root, m)
    assert any(all(w in ln for w in words) for ln in lines), \
        f"the build must fail with a message naming {words} on one line:\n{m.log[-2000:]}"
    return m


def test_docking_then_moving_the_parent_by_a_location_fails_naming_both(tmp_path):
    """frame docks rail onto base, THEN places base at Pos(0, 0, 100): the rail's docking no longer holds -> loud error."""
    frame = FRAME.replace(PLACE_BOTH, PLACE_BOTH.replace("Pos(0, 0, 0)", "Pos(0, 0, 100)"))
    root = J.write_project(tmp_path / "p", files={"frame/__init__.py": frame})
    _fails_naming(root, ("rail", "base", "bottom"))


def test_placing_the_parent_first_then_docking_builds_everything_higher(joint_baseline, tmp_path):
    """base placed at Pos(0, 0, 100) BEFORE the dockings: rail, every screw and the head end 100 mm higher, rotations as before."""
    ref, _, _ = _ok(joint_baseline)
    frame = FRAME.replace(DOCK_RAIL, "").replace(
        PLACE_BOTH, '            out = [place(b, Pos(0, 0, 100))]\n' + DOCK_RAIL + '            out.append(place(r))\n')
    root = J.write_project(tmp_path / "p", files={"frame/__init__.py": frame})
    F.run_model(root).ok()
    o0, o1 = _origins(ref), _origins(root)
    assert set(o1) == set(o0), sorted(set(o0) ^ set(o1))
    for name in o0:
        want = (o0[name][0], o0[name][1], o0[name][2] + 100)
        assert _approx3(o1[name], want), f"{name}: {o1[name]}, expected {want} (100 mm higher)"
    assert _rotations(root) == _rotations(ref)


def test_docking_in_the_wrong_order_inside_a_node_fails(tmp_path):
    """Inside frame: slider docked onto rail.carriage first, THEN rail docked onto base: the slider's docking is stale."""
    frame = FRAME.replace("from . import base, rail, screw", "from . import base, rail, screw\n        from parts.head import slider") \
        .replace(DOCK_RAIL, '            sl = slider.build()\n'
                            '            r["shape"].joints["carriage"].connect_to(sl["shape"].joints["mount"], position=0)\n'
                            + DOCK_RAIL) \
        .replace(PLACE_BOTH, '            out = [place(b, Pos(0, 0, 0)), place(r), place(sl, name="frame_slider")]\n')
    root = J.write_project(tmp_path / "p", files={"frame/__init__.py": frame})
    _fails_naming(root, ("slider", "rail"))


def test_docking_then_placing_the_docked_part_by_a_location_fails(tmp_path):
    frame = FRAME.replace(PLACE_BOTH, '            out = [place(b, Pos(0, 0, 0)), place(r, Pos(300, 0, 0))]\n')
    root = J.write_project(tmp_path / "p", files={"frame/__init__.py": frame})
    _fails_naming(root, ("rail", "base"))


def test_linear_joint_with_the_default_infinite_range_fails_naming_part_and_joint(tmp_path):
    rail = J.FILES["frame/rail.py"].replace(", linear_range=(-60, 60))", ")")
    assert rail != J.FILES["frame/rail.py"]
    root = J.write_project(tmp_path / "p", files={"frame/rail.py": rail})
    _fails_naming(root, ("rail", "carriage"))


# ================================================================ washer + screw + nut from the templates
BASE_UNDER = J.FILES["frame/base.py"].replace(
    '            RigidJoint("rail", s, Location((100, 50, T)))\n',
    '            RigidJoint("rail", s, Location((100, 50, T)))\n'
    '            RigidJoint("under_1", s, Location((20, 20, 0), (180, 0, 0)))\n'
    '            RigidJoint("under_2", s, Location((180, 20, 0), (180, 0, 0)))\n')
FRAME_WASHERS = FRAME.replace("from . import base, rail, screw", "from . import base, nut, rail, screw, washer") \
    .replace('M5 = ["hole_1", "hole_2", "hole_3", "hole_4"]', 'M5 = ["hole_3", "hole_4"]') \
    .replace("    return out", '''    for h, u in (("hole_1", "under_1"), ("hole_2", "under_2")):
                w = washer.build("M5", "DIN 125")
                b["shape"].joints[h].connect_to(w["shape"].joints["seat"])
                s = screw.build("M5", 12, "DIN 912")
                w["shape"].joints["top"].connect_to(s["shape"].joints["head"])
                n = nut.build("M5", "DIN 934")
                b["shape"].joints[u].connect_to(n["shape"].joints["seat"])
                out += [place(w), place(s), place(n)]
            return out''')
# M5: DIN 125 washer 1 mm thick, DIN 912 head 5 mm high, DIN 934 nut 4 mm high; plate top z = T = 10, bottom z = 0


def _build_washers(shared):
    root = J.write_project(shared / "proj", files={"frame/__init__.py": FRAME_WASHERS, "frame/base.py": BASE_UNDER})
    cad = H.cad_dir(root) / "parts" / "frame"
    for t in ("washer", "nut"):
        src = J.TEMPLATES / f"{t}.py"
        if src.is_file():
            (cad / f"{t}.py").write_text(src.read_text().replace("{machine}", "m"))
    m = F.run_model(root)
    (shared / "cold.json").write_text(json.dumps({"rc": m.rc, "log": m.log}))


@pytest.fixture(scope="module")
def washers_proj(tmp_path_factory):
    import conftest
    shared = conftest._shared(tmp_path_factory, "joints_washers", _build_washers)
    d = json.loads((shared / "cold.json").read_text())
    assert d["rc"] == 0, f"--model build of the washer/nut project failed:\n{d['log'][-2500:]}"
    return shared / "proj"


def _world_boxes(root, entry):
    from build123d import Location, import_step
    from OCP.gp import gp_Trsf
    sh = import_step(str(root / entry["step"]))
    out = {}
    for pl in F.placements(entry):
        m = F.matrix(pl["loc"])
        trsf = gp_Trsf()
        trsf.SetValues(*m[0][:4], *m[1][:4], *m[2][:4])
        b = sh.moved(Location(trsf)).bounding_box()
        out[pl["name"]] = ((b.min.X, b.min.Y, b.min.Z), (b.max.X, b.max.Y, b.max.Z))
    return out


def _one(root, spelled):
    e = J.screw_entries(root, spelled)
    assert len(e) == 1, f"one bought entry spelling {spelled!r} expected, got {[p['id'] for p in e]}"
    return e[0]


def test_washer_screw_nut_stack_on_a_hole(washers_proj):
    """Washer seat on the hole (top of the plate), screw head on the washer's top, nut seat on the joint under the plate
    (Z out of the bottom face): the nut hangs below the plate, the washer lies on it, the head stands on the washer."""
    root = washers_proj
    W = _world_boxes(root, _one(root, "M5 DIN 125"))
    N = _world_boxes(root, _one(root, "M5 DIN 934"))
    assert len(W) == 2 and len(N) == 2, (W, N)
    for name, (lo, hi) in W.items():
        assert abs(lo[2] - J.T) < 1e-3 and abs(hi[2] - (J.T + 1)) < 1e-3, f"washer {name} must lie on the plate z {J.T}..{J.T + 1}: {lo}, {hi}"
    for name, (lo, hi) in N.items():
        assert abs(hi[2] - 0) < 1e-3 and abs(lo[2] - (-4)) < 1e-3, f"nut {name} must hang under the plate z -4..0: {lo}, {hi}"
    centres = sorted((round((lo[0] + hi[0]) / 2, 3), round((lo[1] + hi[1]) / 2, 3)) for lo, hi in list(W.values()))
    assert centres == [(20.0, 20.0), (180.0, 20.0)], f"washers on hole_1 / hole_2: {centres}"
    centres = sorted((round((lo[0] + hi[0]) / 2, 3), round((lo[1] + hi[1]) / 2, 3)) for lo, hi in list(N.values()))
    assert centres == [(20.0, 20.0), (180.0, 20.0)], f"nuts under hole_1 / hole_2: {centres}"
    pm = J.placement_map(root)
    on_washer = [(n, pl) for n, (_, pl) in pm.items() if (pl.get("joint") or {}).get("at") == "top"]
    assert len(on_washer) == 2, f"two screws docked on a washer's top: {[n for n, _ in on_washer]}"
    S = _world_boxes(root, _one(root, "M5x12"))
    for n, pl in on_washer:
        assert pl["joint"]["to"] in W and pl["joint"]["by"] == "head", pl["joint"]
        lo, hi = S[n]
        assert abs(hi[2] - (J.T + 1 + 5)) < 1e-3, f"screw {n}: head on the washer top, so its top at z {J.T + 6}: {hi}"
        assert abs(lo[2] - (J.T + 1 - 12)) < 1e-3, f"screw {n}: shaft end at z {J.T + 1 - 12}: {lo}"


@pytest.mark.parametrize("spelled,qty", [("M5 DIN 125", 2), ("M5 DIN 934", 2), ("M5x12", 4)])
def test_washers_and_nuts_are_bought_rows_counted_by_placements(washers_proj, tmp_path, spelled, qty):
    root = Path(F._clean_copy(washers_proj, tmp_path / "proj"))
    e = _one(root, spelled)
    assert e["qty"] == qty and e.get("kind") == "fasteners", e
    d = H.run_derive(root, "bought").ok()
    rows = [r for r in d.manifest["bought"] if any(spelled in str(r.get(k, "")) for k in ("code", "name", "desc"))]
    assert len(rows) == 1 and rows[0]["qty"] == qty, f"bought row {spelled!r} qty {qty} expected: {rows} / {d.manifest['bought']}"


# ================================================================ upgrade: a model built before #16 is reused unchanged
PRE_JOINTS_COMMIT = "b56f930"      # main right before #16 (merge of #42)


def test_model_built_by_the_framework_before_joints_is_reused_unchanged(tmp_path):
    """test_legacy_removed.py::test_dw1_folder_model_built_by_base_is_reused_unchanged covers this from fd872c6 (before #30);
    this is the same check from the framework #16 replaces: nothing rebuilt, no code runs, parts.json byte-identical."""
    import procgroup
    import sys
    import test_legacy_removed as L
    old = L.BASE_COMMIT
    L.BASE_COMMIT = PRE_JOINTS_COMMIT
    try:
        base = L._base_scripts(tmp_path)
    finally:
        L.BASE_COMMIT = old
    root = F.write_project(tmp_path / "p", legacy=False)
    cp = procgroup.run([sys.executable, str(base / "cad_incremental.py"), "--model", "assemble.py"], cwd=H.cad_dir(root),
                       env=H.env_for(root), capture_output=True, text=True, timeout=300)
    assert cp.returncode == 0, f"precondition: the pre-#16 framework builds the project:\n{(cp.stdout + cp.stderr)[-1500:]}"
    before = (root / "build/model/parts.json").read_bytes()
    H.clear_ticks(root)
    m = F.run_model(root).ok()
    assert "nodes rebuilt 0" in m.log, f"the current framework rebuilt a model the pre-#16 one built:\n{m.log[-1500:]}"
    assert H.ticks(root) == [], f"part or node code ran on the upgrade run: {H.ticks(root)}"
    assert (root / "build/model/parts.json").read_bytes() == before, "parts.json changed on the upgrade run"


# ================================================================ a plain run (a project's own check script, no cad_incremental)
PLAIN = """import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import parts

out = []
for p in parts.build():
    sh = p.get("shape")
    if sh is None:
        continue
    b = sh.bounding_box()
    out.append([p["name"], [round(v, 3) + 0.0 for v in (b.min.X, b.min.Y, b.min.Z, b.max.X, b.max.Y, b.max.Z)]])
Path(sys.argv[1]).write_text(json.dumps(out))
"""


def _plain_boxes(root, tmp_path):
    """Run a check script with plain python (no cad_incremental): [(part name, world bbox of the returned shape)], sorted."""
    import procgroup
    import sys
    script = H.cad_dir(root) / "plain_check.py"
    script.write_text(PLAIN)
    out = tmp_path / "plain.json"
    cp = procgroup.run([sys.executable, "plain_check.py", str(out)], cwd=H.cad_dir(root), env=H.env_for(root),
                       capture_output=True, text=True, timeout=300)
    assert cp.returncode == 0, f"the plain run of parts.build() failed:\n{(cp.stdout + cp.stderr)[-2000:]}"
    return sorted((n, tuple(b)) for n, b in json.loads(out.read_text()))


def _model_boxes(root):
    """The same from the --model build: each placement's part STEP moved by its loc, named by the part's name."""
    out = []
    for e in H.read_parts(root):
        if not e.get("step"):
            continue
        for _, (lo, hi) in _world_boxes(root, e).items():
            out.append((e["name"], tuple(round(v, 3) + 0.0 for v in (*lo, *hi))))
    return sorted(out)


def _same_boxes(a, b, tol=1e-3):
    return len(a) == len(b) and all(na == nb and all(abs(x - y) <= tol for x, y in zip(ba, bb)) for (na, ba), (nb, bb) in zip(a, b))


def test_plain_run_puts_every_shape_where_the_model_build_does(panels_proj, tmp_path):
    """jointfiles project + the panels sub-node docked by the root at two of its parts: in a plain `python` run of parts.build()
    every returned shape (rail, slider, arm, every screw, both sheets and their screws) stands where the --model build put it."""
    root = Path(F._clean_copy(panels_proj, tmp_path / "proj"))
    model, plain = _model_boxes(root), _plain_boxes(root, tmp_path)
    assert {n for n, _ in model} >= {"base", "rail", "slider", "arm", "sheet_a", "sheet_b"}, model
    assert _same_boxes(plain, model), f"plain run differs from the --model build:\nplain {plain}\nmodel {model}"


BASE_OFFSET = J.FILES["frame/base.py"].replace("from build123d import Align, Box, Location, RigidJoint",
                                               "from build123d import Align, Box, Location, Pos, RigidJoint") \
    .replace("s = Box(200, 100, T, align=(Align.MIN, Align.MIN, Align.MIN))",
             "s = Pos(5, 0, 0) * Box(200, 100, T, align=(Align.MIN, Align.MIN, Align.MIN))")


def test_root_part_built_off_its_origin_keeps_the_offset_in_a_plain_run(tmp_path):
    """base built as Pos(5, 0, 0) * Box(...) (joints declared after that), placed with place(base, Pos(0, 0, 0)): the plain run
    and the --model build agree, base spans x 5..205."""
    assert BASE_OFFSET != J.FILES["frame/base.py"] and "Pos(5, 0, 0) *" in BASE_OFFSET
    root = J.write_project(tmp_path / "p", files={"frame/base.py": BASE_OFFSET})
    F.run_model(root).ok()
    model, plain = _model_boxes(root), _plain_boxes(root, tmp_path)
    base = [b for n, b in model if n == "base"]
    assert len(base) == 1 and abs(base[0][0] - 5) < 1e-3 and abs(base[0][3] - 205) < 1e-3, f"--model base box: {base}"
    assert _same_boxes(plain, model), f"plain run differs from the --model build:\nplain {plain}\nmodel {model}"
