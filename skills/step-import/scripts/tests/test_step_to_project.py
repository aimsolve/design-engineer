#!/usr/bin/env python3
"""Tests for scripts/step_to_project.py: a client STEP machine, already cut by step_import.py, becomes a code project of the
framework (skill machine-project, «Code model»: a file per part, a folder per node, build123d joints, cad_incremental --model).

Run (build123d 0.13.0; one pytest process at a time on the Mac):
  uv venv --python 3.12 <venv> && uv pip install --python <venv>/bin/python 'build123d==0.13.0' pytest trimesh numpy
  cd agent/skills/step-import/scripts/tests && <venv>/bin/python -m pytest -q test_step_to_project.py

The source is synthetic, written here with build123d + OCP XCAF to a STEP (no client file):

    Machine 01.00.000                       root product
      Plate 01.01.001                       made: flat 120 x 80 x 4 plate, 4 holes d6, at the root's origin
      Bracket unit 01.02.000   (x2)         sub-assembly product, placed twice: at (10, 20, 4) and at (100, 60, 4) turned 90 deg Z
        Bracket 01.02.001                   made: bent sheet L (t 2, bend r 2, 3 holes), at (2, 1, 0) in the unit
        Screw DIN 912 M5x25                 standard: a screw-like cylinder with a head, at (20, 12.5, -10) turned 30 deg Z

  -> 3 distinct parts, 5 leaf instances. It is cut with `step_import.py in.step --out IMPORT` and converted with
  `step_to_project.py IMPORT ROOT --title T` into a project scaffolded by machine-project's scaffold.py.

Contract under test (from the ticket, not from the code):
  IMPORT/instances.json  the instance tree {"name","product","drawing","ref","matrix": 16 floats row-major 4x4, mm, relative to
                         the parent, "part": id (leaf) | "children": [...]}, written by step_import.py next to parts.json
  ROOT/mechanics/cad/    params.py, assemble.py, geom.py, parts/__init__.py (the machine root), one folder per distinct
                         sub-assembly product parts/<node>/__init__.py with module-level TITLE = the product name, one <part>.py per
                         distinct part, migration/report.json {"parts": [{"id", "kind": sheet|turned|prism|bought|client,
                         "reason" (client)}...], "totals": {...}}
  then: cad_incremental.py --model assemble.py exits 0; build/model/parts.json has the source's part ids (as the framework
  spells an id: non-word characters -> "_") and its placement count; every placement but the root's has a `joint`; every
  placement's world matrix equals its source instance's within 1e-6 mm; check.py reports no FAIL for the CAD layout / model
  parts / part files / joints gates.
"""
import ast
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pytest
from build123d import Align, Axis, Box, Cylinder, Location, Pos, Rot, import_step

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
STEP_IMPORT = SCRIPTS / "step_import.py"
CONVERT = SCRIPTS / "step_to_project.py"
MP = SCRIPTS.parents[1] / "machine-project" / "scripts"
CAD_INC = MP / "cad_incremental.py"
CHECK = MP / "check.py"
SCAFFOLD = MP / "scaffold.py"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(HERE))
from step_names_i18n import NAMES  # noqa: E402

RU = NAMES["ru"]

ROOT_PRODUCT = "Machine 01.00.000"
UNIT_PRODUCT = "Bracket unit 01.02.000"
PLATE, BRACKET, SCREW = "Plate 01.01.001", "Bracket 01.02.001", "Screw DIN 912 M5x25"
MIN = (Align.MIN, Align.MIN, Align.MIN)
GATES = {"CAD layout", "model parts", "part files", "joints"}
MATRIX_TOL = 1e-6
TIMEOUT = 900

# where the leaves must end up (world), as build123d Locations: the expected world placement of each source instance
UNIT_AT = [Location((10, 20, 4)), Location((100, 60, 4), (0, 0, 90))]
BRACKET_IN_UNIT = Location((2, 1, 0))
SCREW_IN_UNIT = Location((20, 12.5, -10), (0, 0, 30))


# ---------------------------------------------------------------- the synthetic source

def plate_shape():
    p = Box(120, 80, 4, align=MIN)
    for x, y in [(10, 10), (110, 10), (10, 70), (110, 70)]:
        p -= Pos(x, y, 0) * Cylinder(3, 20)
    return p


def bracket_shape(t=2.0, r=2.0):
    """Bent sheet L in its own coordinates (corner at (5, -3, 7)): base 40 x 25, wall 30, 3 holes."""
    def bend(s, x, z, rad):
        es = s.edges().filter_by(Axis.Y).filter_by(lambda e: abs(e.center().X - x) < 1e-6 and abs(e.center().Z - z) < 1e-6)
        assert len(es) == 1
        return s.fillet(rad, es)
    s = Box(40, 25, t, align=MIN) + Box(t, 25, 30, align=MIN)
    s = bend(bend(s, t, t, r), 0, 0, r + t)
    s -= Pos(25, 8, 0) * Cylinder(2.5, 10)
    s -= Pos(25, 18, 0) * Cylinder(2.5, 10)
    s -= Pos(0, 12.5, 20) * Rot(0, 90, 0) * Cylinder(3, 10)
    return Pos(5, -3, 7) * s


def screw_shape():
    shaft = Cylinder(2.5, 20, align=(Align.CENTER, Align.CENTER, Align.MIN))
    head = Pos(0, 0, 20) * Cylinder(4, 5, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return shaft + head


def write_step(path):
    """A 2-level XCAF assembly with product names, the unit product shared by its two placements, written as AP214 STEP."""
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.TopLoc import TopLoc_Location
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    def named(label, nm):
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(nm))
        return label

    def baked(shape):
        """The shape's own location moved into its geometry: XCAF would otherwise turn a located shape into an extra
        placement, and the part's 'own coordinates' would not be the ones built here."""
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
        t = shape.wrapped.Location().Transformation()
        s = shape.wrapped.Located(TopLoc_Location())
        out = BRepBuilderAPI_Transform(s, t, True).Shape()
        assert out.Location().IsIdentity()
        return out

    loc = lambda l: TopLoc_Location(l.wrapped.Transformation())
    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    app.InitDocument(doc)
    st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    root = named(st.NewShape(), ROOT_PRODUCT)
    unit = named(st.NewShape(), UNIT_PRODUCT)
    lp = named(st.AddShape(baked(plate_shape()), False), PLATE)
    lb = named(st.AddShape(baked(bracket_shape()), False), BRACKET)
    ls = named(st.AddShape(baked(screw_shape()), False), SCREW)
    st.AddComponent(unit, lb, loc(BRACKET_IN_UNIT))
    st.AddComponent(unit, ls, loc(SCREW_IN_UNIT))
    st.AddComponent(root, lp, loc(Location((0, 0, 0))))
    for at in UNIT_AT:
        st.AddComponent(root, unit, loc(at))
    st.UpdateAssemblies()
    w = STEPCAFControl_Writer()
    w.SetNameMode(True)
    w.Transfer(doc, STEPControl_AsIs)
    assert w.Write(str(path)) == 1, "test setup: STEP write failed"   # IFSelect_RetDone


# ---------------------------------------------------------------- helpers

def run(cmd, cwd=None):
    return subprocess.run([str(c) for c in cmd], cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT,
                          env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))


def log(cp):
    return f"rc={cp.returncode}\n--- stdout\n{cp.stdout[-3000:]}\n--- stderr\n{cp.stderr[-3000:]}"


def spell(pid):
    """A part id as the framework spells it (cad_gates._part_id): non-word runs -> '_'."""
    return re.sub(r"_+", "_", re.sub(r"[^\w]+", "_", str(pid))).strip("_")


def mat(m):
    """16 row-major numbers, or 4 rows of 4, -> 4x4 list of floats."""
    flat = [float(x) for row in m for x in row] if m and isinstance(m[0], list) else [float(x) for x in m]
    assert len(flat) == 16, f"a matrix has 16 numbers, got {len(flat)}: {m!r}"
    return [flat[i * 4:(i + 1) * 4] for i in range(4)]


def mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def loc_mat(l):
    t = l.wrapped.Transformation()
    return [[t.Value(i, j) for j in range(1, 5)] for i in range(1, 4)] + [[0.0, 0.0, 0.0, 1.0]]


def mdiff(a, b):
    return max(abs(a[i][j] - b[i][j]) for i in range(4) for j in range(4))


def leaves(node, parent=None, path=()):
    """[(part id, world 4x4, product path)] of every leaf instance of instances.json."""
    m = mul(parent, mat(node["matrix"])) if parent is not None else mat(node["matrix"])
    if node.get("part") is not None:
        return [(node["part"], m, path)]
    out = []
    for c in node.get("children") or []:
        out += leaves(c, m, path + (node.get("product") or node.get("name"),))
    return out


def sub_products(node, top=True):
    """Product names of every sub-assembly (a node with children) below the root."""
    out = []
    if node.get("children"):
        if not top:
            out.append(node.get("product") or node.get("name"))
        for c in node["children"]:
            out += sub_products(c, False)
    return out


def model_parts(root):
    f = Path(root) / "build" / "model" / "parts.json"
    assert f.is_file(), "build/model/parts.json missing: the --model build did not produce a model"
    return json.loads(f.read_text())["parts"]


def module_title(path):
    t = ast.parse(Path(path).read_text())
    for n in t.body:
        if isinstance(n, ast.Assign) and any(isinstance(x, ast.Name) and x.id == "TITLE" for x in n.targets) \
                and isinstance(n.value, ast.Constant):
            return n.value.value
    return None


# ---------------------------------------------------------------- fixtures (built once per module; they never assert on the code
# under test, so a missing behaviour is a FAILED test that names it, not a setup error)

@pytest.fixture(scope="module")
def source(tmp_path_factory):
    """IMPORT dir: in.step cut by step_import.py (parts.json, parts/<id>.step and, by the contract, instances.json)."""
    d = tmp_path_factory.mktemp("src")
    write_step(d / "in.step")
    cp = run([sys.executable, STEP_IMPORT, d / "in.step", "--out", d / "imp"])
    assert cp.returncode == 0, "test setup: step_import.py failed\n" + log(cp)
    return d / "imp"


@pytest.fixture(scope="module")
def src_parts(source):
    return {p["id"]: p for p in json.loads((source / "parts.json").read_text())["parts"]}


@pytest.fixture(scope="module")
def converted(source, tmp_path_factory):
    """PROJECT_ROOT scaffolded by machine-project, then converted through the CLI: (root, CompletedProcess)."""
    root = tmp_path_factory.mktemp("proj") / "t"
    cp = run([sys.executable, SCAFFOLD, root, "--name", "t", "--title", "T"])
    assert cp.returncode == 0, "test setup: scaffold.py failed\n" + log(cp)
    return root, run([sys.executable, CONVERT, source, root, "--title", "T"])


@pytest.fixture(scope="module")
def built(converted):
    """(root, CompletedProcess of `cad_incremental.py --model assemble.py` run from mechanics/cad), or (root, None) when the
    conversion itself failed."""
    root, cp = converted
    if cp.returncode != 0:
        return root, None
    return root, run([sys.executable, CAD_INC, "--model", "assemble.py"], cwd=root / "mechanics" / "cad")


def need_instances(source):
    f = source / "instances.json"
    assert f.is_file(), "step_import.py wrote no instances.json next to parts.json"
    return json.loads(f.read_text())


def need_project(converted):
    root, cp = converted
    assert cp.returncode == 0, "step_to_project.py IMPORT ROOT --title T failed\n" + log(cp)
    return root


def need_model(built):
    root, cp = built
    assert cp is not None, "step_to_project.py failed, so there is no project to build (see the CLI test)"
    assert cp.returncode == 0, "cad_incremental.py --model assemble.py failed on the converted project\n" + log(cp)
    return model_parts(root)


def need_report(converted):
    f = need_project(converted) / "mechanics" / "cad" / "migration" / "report.json"
    assert f.is_file(), "no mechanics/cad/migration/report.json"
    return json.loads(f.read_text())


# ---------------------------------------------------------------- input: instances.json from step_import

def test_step_import_writes_the_instance_tree(source, src_parts):
    """instances.json: 5 leaves (plate once, bracket and screw twice) pointing at parts.json ids, the unit product under the
    root twice, and world matrices (composed from the per-node row-major ones) where the source STEP put each instance."""
    instances = need_instances(source)
    lv = leaves(instances)
    assert Counter(pid for pid, _, _ in lv) == Counter({pid: p["instances"] for pid, p in src_parts.items()}), lv
    assert ROOT_PRODUCT in (instances.get("product"), instances.get("name")), instances.get("name")
    assert sub_products(instances) == [UNIT_PRODUCT, UNIT_PRODUCT]
    by_name = {p["name"]: pid for pid, p in src_parts.items()}
    want = [(by_name[PLATE], loc_mat(Location((0, 0, 0))))]
    for at in UNIT_AT:
        want += [(by_name[BRACKET], loc_mat(at * BRACKET_IN_UNIT)), (by_name[SCREW], loc_mat(at * SCREW_IN_UNIT))]
    for pid, w in want:
        assert any(p == pid and mdiff(m, w) < 1e-6 for p, m, _ in lv), f"no instance of {pid} at world {w}; have {lv}"


# ---------------------------------------------------------------- the project files

def test_cli_converts_and_writes_the_code_project_layout(converted, src_parts):
    """The CLI exits 0 and writes params.py, assemble.py, geom.py, parts/__init__.py, migration/report.json and one <part>.py
    per distinct part (3) in the node folders."""
    root = need_project(converted)
    cad = root / "mechanics" / "cad"
    for f in ("params.py", "assemble.py", "geom.py", "parts/__init__.py", "migration/report.json"):
        assert (cad / f).is_file(), f"mechanics/cad/{f} not written"
    parts_dir = cad / "parts"
    files = [p for p in parts_dir.rglob("*.py")
             if p.parent != parts_dir and p.name not in ("__init__.py", "params.py") and not p.name.startswith("_")
             and not any(x.startswith("_") for x in p.relative_to(parts_dir).parts[:-1])]
    assert len(files) == len(src_parts), \
        f"one <part>.py per distinct part: {len(src_parts)} parts, files {[str(f.relative_to(cad)) for f in files]}"


def test_one_node_folder_per_sub_assembly_product_with_its_title(converted, source):
    """The unit is placed twice but is one product: exactly one node folder, whose __init__.py has TITLE = the product name."""
    root = need_project(converted)
    instances = need_instances(source)
    parts_dir = root / "mechanics" / "cad" / "parts"
    titles = {p.parent: module_title(p) for p in parts_dir.rglob("__init__.py") if p.parent != parts_dir}
    for prod in set(sub_products(instances)):
        having = [str(d.relative_to(parts_dir)) for d, t in titles.items() if t == prod]
        assert len(having) == 1, f"product {prod!r}: node folders with that TITLE {having}; all titles {list(titles.values())}"


def test_report_counts_every_part(converted, src_parts):
    """report.json: one entry per source part; a made part is recreated (sheet|turned|prism) or client geometry with a reason;
    a standard / purchased part is bought; totals present."""
    report = need_report(converted)
    rows = report.get("parts")
    assert isinstance(rows, list), f"report.json has no parts list: {list(report)}"
    by_id = {r["id"]: r for r in rows}
    assert set(by_id) == set(src_parts), f"report ids {sorted(by_id)} vs source {sorted(src_parts)}"
    for pid, p in src_parts.items():
        r = by_id[pid]
        if p["kind"] == "made":
            assert r["kind"] in {"sheet", "turned", "prism", "client"}, r
            if r["kind"] == "client":
                assert isinstance(r.get("reason"), str) and r["reason"].strip(), f"client geometry without a reason: {r}"
        else:
            assert r["kind"] == "bought", f"{pid} is {p['kind']} in the source: {r}"
    assert isinstance(report.get("totals"), dict) and report["totals"], f"no totals: {report.get('totals')!r}"


def test_flat_plate_and_bent_bracket_are_recreated_not_kept_as_client_geometry(converted, src_parts):
    """The two made parts are the shapes recreate() handles (flat plate, bent L): they become code, not client geometry."""
    report = need_report(converted)
    by_name = {p["name"]: pid for pid, p in src_parts.items()}
    by_id = {r["id"]: r for r in report.get("parts") or []}
    assert by_id.get(by_name[PLATE], {}).get("kind") in {"sheet", "prism"}, by_id.get(by_name[PLATE])
    assert by_id.get(by_name[BRACKET], {}).get("kind") == "sheet", by_id.get(by_name[BRACKET])


def test_convert_function_returns_the_report(source, tmp_path):
    """convert(import_dir, root, title) is the CLI as a function: it returns the report it wrote."""
    import step_to_project
    root = tmp_path / "t2"
    assert run([sys.executable, SCAFFOLD, root, "--name", "t", "--title", "T"]).returncode == 0
    rep = step_to_project.convert(str(source), str(root), title="T")
    assert isinstance(rep, dict) and isinstance(rep.get("parts"), list), rep
    assert rep == json.loads((root / "mechanics/cad/migration/report.json").read_text())


# ---------------------------------------------------------------- the built model

def test_model_builds(built):
    """`cad_incremental.py --model assemble.py` in mechanics/cad exits 0 and writes build/model/parts.json."""
    need_model(built)


def test_model_has_the_source_parts_and_placement_count(built, src_parts, source):
    model = need_model(built)
    instances = need_instances(source)
    assert {spell(p["id"]) for p in model} == {spell(i) for i in src_parts}, \
        f"model ids {sorted(p['id'] for p in model)} vs source {sorted(src_parts)}"
    n = sum(len(p.get("placements") or []) for p in model)
    assert n == len(leaves(instances)) == 5, f"placements {n}, source instances {len(leaves(instances))}"


def test_every_placement_but_the_root_is_docked_by_a_joint(built):
    model = need_model(built)
    pls = [pl for p in model for pl in p.get("placements") or []]
    assert pls, "no placements in the model"
    loose = [pl["name"] for pl in pls if not isinstance(pl.get("joint"), dict)]
    assert len(loose) <= 1, f"placed without a joint (only the root may be): {loose}"


def test_every_placement_stands_where_the_source_instance_stands(built, source):
    """Each placement's world matrix (parts.json `loc`) equals one source instance's world matrix of the same part within
    1e-6 mm, one to one."""
    model = need_model(built)
    want = defaultdict(list)
    for pid, m, _ in leaves(need_instances(source)):
        want[spell(pid)].append(m)
    for p in model:
        left = list(want[spell(p["id"])])
        for pl in p.get("placements") or []:
            got = mat(pl["loc"])
            best = min(range(len(left)), key=lambda i: mdiff(left[i], got), default=None)
            assert best is not None, f"{p['id']}: more placements than source instances"
            assert mdiff(left[best], got) <= MATRIX_TOL, \
                f"{pl['name']}: world matrix {got} is {mdiff(left[best], got):.3g} off the nearest source instance {left[best]}"
            left.pop(best)
        assert not left, f"{p['id']}: source instances not placed: {left}"


def test_model_geometry_matches_the_source_parts(built, source, src_parts):
    """The STEP the model wrote for each part (own coordinates) has the source part's volume (0.5 %) and bbox (0.05 mm)."""
    model = need_model(built)
    root, _ = built
    src_by_spell = {spell(i): p for i, p in src_parts.items()}
    for p in model:
        s = src_by_spell[spell(p["id"])]
        ref = import_step(str(source / s["files"]["step"]))
        got = import_step(str(root / p["step"]))
        assert abs(got.volume - ref.volume) <= 0.005 * ref.volume, f"{p['id']}: volume {got.volume:.2f} vs {ref.volume:.2f}"
        rb, gb = ref.bounding_box(), got.bounding_box()
        d = max(abs(a - b) for a, b in zip((rb.min.X, rb.min.Y, rb.min.Z, rb.max.X, rb.max.Y, rb.max.Z),
                                          (gb.min.X, gb.min.Y, gb.min.Z, gb.max.X, gb.max.Y, gb.max.Z)))
        assert d <= 0.05, f"{p['id']}: bbox off by {d:.4f} mm (own coordinates must be the source's)"


def test_check_py_reports_no_fail_on_the_model_gates(built):
    """check.py on the converted, built project: the CAD layout, model parts, part files and joints gates ran and none FAILs."""
    need_model(built)
    root, _ = built
    r = run([sys.executable, CHECK, root, "--no-validate", "--json"])
    out = json.loads(r.stdout.strip().splitlines()[-1])
    bad = [f for f in out["fails"] if f["rule"] in GATES]
    assert not bad, f"check.py FAILs on the model gates: {bad}"
    for g in ("CAD layout", "model parts", "part files", "joints"):
        assert re.search(rf"^(PASS|WARN)\s+{re.escape(g)}\b", r.stderr, re.M), \
            f"check.py did not pass the {g!r} gate (is the converted project a code model?)\n{r.stderr[-2000:]}"


# ---------------------------------------------------------------- review: fasteners, re-runs, the migration check

def test_a_screw_drawn_as_the_standard_becomes_the_template_fastener():
    """A screw whose 3D is the DIN 912 M5x25 the framework draws is recognised as that template, with its size and length
    (the fixture's screw is a rough cylinder with a head, which the template rightly does not match)."""
    import step_to_project as S
    shape = S._template("screw").build(size="M5", length=25, std="DIN 912")["shape"]
    fa = S.fastener_of({"kind": "standard", "name": "Screw DIN 912 M5x25"})
    assert fa is not None and fa[0] == "screw", fa
    fit = S.fastener_fit(shape, fa)
    assert fit and fit.get("ok"), fit
    assert fit["args"].get("size") == "M5" and float(fit["args"].get("length")) == 25, fit["args"]


def test_a_rerun_keeps_a_part_file_the_agent_wrote(converted, source, tmp_path):
    """A part drawn by hand (any file the converter did not write as it is) survives a second run and is reported as kept."""
    import shutil
    root = tmp_path / "again"
    shutil.copytree(need_project(converted), root, symlinks=True)
    plate = next(f for f in (root / "mechanics" / "cad" / "parts").rglob("*.py") if f'"{PLATE}' in f.read_text() or
                 PLATE.split()[1] in f.read_text() and "return P(" in f.read_text())
    hand = plate.read_text() + "\n# drawn by hand\n"
    plate.write_text(hand)
    mine = root / "mechanics" / "cad" / "parts" / "_helper.py"
    mine.write_text("X = 1\n")
    cp = run([sys.executable, CONVERT, source, root, "--title", "T"])
    assert cp.returncode == 0, log(cp)
    assert plate.read_text() == hand, "the hand-written part file was overwritten by the re-run"
    assert mine.read_text() == "X = 1\n", "a file of the agent's own under parts/ was removed by the re-run"
    kept = json.loads((root / "mechanics" / "cad" / "migration" / "report.json").read_text())["kept"]
    assert str(plate.relative_to(root / "mechanics" / "cad")) in kept and "parts/_helper.py" in kept, kept


def test_merge_params_keeps_the_agents_lines_and_sets_every_generated_number():
    import step_to_project as S
    mine = '"""Numbers."""\nfrom params import *  # noqa: F401,F403\n\nPLATE_T = 3.0\nMY_HOLE = 6.5  # by hand\n'
    new = '"""Numbers."""\nfrom params import *  # noqa: F401,F403\n\nPLATE_T = 4.0\nBRACKET_W = 20.0\n'
    out = S.merge_params(mine, new)
    assert "PLATE_T = 4.0" in out and "BRACKET_W = 20.0" in out and "MY_HOLE = 6.5  # by hand" in out, out
    assert "PLATE_T = 3.0" not in out


def test_a_rerun_after_the_agent_edited_a_nodes_params_still_has_every_number(converted, source, tmp_path):
    """The agent added a number to a node's params.py and one generated number is gone from it (an older run): a re-run
    keeps the agent's line and brings the generated one back, so the part files that use it import (review 9)."""
    import shutil
    root = tmp_path / "params"
    shutil.copytree(need_project(converted), root, symlinks=True)
    pf = next(f for f in (root / "mechanics" / "cad" / "parts").rglob("params.py") if " = " in f.read_text())
    lines = pf.read_text().splitlines()
    gone = next(x for x in lines if " = " in x and x[:1].isupper())
    pf.write_text("\n".join(x for x in lines if x != gone) + "\nMY_HOLE = 6.5\n")
    cp = run([sys.executable, CONVERT, source, root, "--title", "T"])
    assert cp.returncode == 0, log(cp)
    text = pf.read_text()
    assert "MY_HOLE = 6.5" in text and gone.split(" = ")[0] + " = " in text, text


def test_a_title_with_quotes_and_a_backslash_gives_python_that_parses(source, tmp_path):
    import ast
    import step_to_project as S
    root = tmp_path / "q"
    root.mkdir()
    S.convert(source, root, RU["machine"] + ' """ C:\\N\\x', log=lambda *a: None)
    for f in (root / "mechanics" / "cad").rglob("*.py"):
        ast.parse(f.read_text(encoding="utf-8"), str(f))


def test_bad_overrides_fail_before_the_cut_and_leave_the_import(tmp_path):
    """A broken --overrides file is told at once, before the hours of cutting the STEP; the project's import stays (critic)."""
    root = tmp_path / "p"
    imp = root / "build" / "client_import"
    imp.mkdir(parents=True)
    (imp / "marker").write_text("checked against this")
    step = tmp_path / "in.step"
    write_step(step)
    bad = tmp_path / "ov.json"
    bad.write_text("{not json")
    cp = run([sys.executable, CONVERT, step, root, "--overrides", bad])
    assert cp.returncode != 0 and "--overrides" in cp.stdout + cp.stderr and "Traceback" not in cp.stderr, log(cp)
    assert (imp / "marker").read_text() == "checked against this" and not (root / "build" / "client_import.new").exists()


def test_migration_check_passes_then_fails_on_a_shifted_placement(built, source, tmp_path):
    """migration_check.py --part: exit 0 on the converted model; exit 1 once his reference places that part 0.01 mm away."""
    import shutil
    need_model(built)
    root = tmp_path / "chk"
    shutil.copytree(built[0], root, symlinks=True)
    ref0 = json.loads((root / "mechanics/cad/migration/reference.json").read_text())
    pid = next(i for i, r in ref0["parts"].items() if r["name"] == PLATE)
    cmd = [sys.executable, SCRIPTS / "migration_check.py", root, "--import", source, "--part", pid]
    cp = run(cmd)
    assert cp.returncode == 0, log(cp)
    ref_f = root / "mechanics/cad/migration/reference.json"
    ref = json.loads(ref_f.read_text())
    for pl in ref["placements"]:
        if pl["part"] == pid:
            m = pl["matrix"]
            if isinstance(m[0], list):
                m[0][3] += 0.01
            else:
                m[3] += 0.01
            break
    ref_f.write_text(json.dumps(ref))
    cp = run(cmd)
    assert cp.returncode == 1, "a placement 0.01 mm off his must fail the check\n" + log(cp)


# ---------------------------------------------------------------- second review: failures, the library, codes, --part

def _fresh_project(tmp_path, name):
    root = tmp_path / name
    cp = run([sys.executable, SCAFFOLD, root, "--name", "t", "--title", "T"])
    assert cp.returncode == 0, log(cp)
    return root


def cad_files(root):
    """Every file under mechanics/cad but build/ and caches: {path: bytes}."""
    cad = Path(root) / "mechanics" / "cad"
    return {str(f.relative_to(cad)): f.read_bytes() for f in cad.rglob("*")
            if f.is_file() and "__pycache__" not in f.parts and "build" not in f.relative_to(cad).parts}


def test_a_rerun_that_fails_half_way_leaves_the_project_as_it_was(converted, source, tmp_path, monkeypatch):
    """The converter dies after writing most of the new files: the agent's files and the old generated ones are all still there."""
    import shutil
    import recreate
    import step_to_project as S
    root = tmp_path / "fail"
    shutil.copytree(need_project(converted), root, symlinks=True)
    mine = root / "mechanics/cad/parts/_helper.py"
    mine.write_text("X = 1\n")
    before = cad_files(root)

    def boom(*a, **k):
        raise RuntimeError("OCC died")
    monkeypatch.setattr(recreate, "_concave_holes", boom)
    with pytest.raises(RuntimeError):
        S.convert(source, root, "T", log=lambda *a: None)
    assert cad_files(root) == before, "a failed re-run changed or lost files under mechanics/cad"
    assert not (root / "build/port_stage").exists() and not (root / "mechanics/cad/.port_stage").exists()


def test_a_failed_first_run_does_not_freeze_the_converters_files(source, tmp_path, monkeypatch):
    """The first run fails, the second goes through: nothing the converter wrote is mistaken for the agent's."""
    import recreate
    import step_to_project as S
    root = _fresh_project(tmp_path, "first")
    before = cad_files(root)
    real = recreate._concave_holes
    monkeypatch.setattr(recreate, "_concave_holes", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("OCC died")))
    with pytest.raises(RuntimeError):
        S.convert(source, root, "T", log=lambda *a: None)
    assert cad_files(root) == before, "a failed first run left files in mechanics/cad"
    monkeypatch.setattr(recreate, "_concave_holes", real)
    rep = S.convert(source, root, "T", log=lambda *a: None)
    assert rep["kept"] == [], rep["kept"]


def test_a_first_port_refuses_parts_it_did_not_write(source, tmp_path):
    """A project scaffolded with --cad build123d has its own parts/ (an example node): a first full port refuses, names
    them and changes nothing, instead of keeping them over its machine root (review 5)."""
    root = tmp_path / "t"
    cp = run([sys.executable, SCAFFOLD, root, "--name", "t", "--title", "T", "--cad", "build123d"])
    assert cp.returncode == 0, "test setup: scaffold.py --cad build123d failed\n" + log(cp)
    before = {str(f.relative_to(root)): f.read_bytes() for f in root.rglob("*") if f.is_file()}
    cp = run([sys.executable, CONVERT, source, root, "--title", "T"])
    assert cp.returncode != 0, "a first port over a parts/ it did not write must refuse\n" + log(cp)
    assert "parts/" in cp.stdout + cp.stderr, log(cp)
    after = {str(f.relative_to(root)): f.read_bytes() for f in root.rglob("*") if f.is_file()}
    assert after == before, "a refused port changed the project"


def test_library_mode_writes_the_clients_library_and_keeps_a_curated_module(source, tmp_path):
    """--library: bought parts other than standard fasteners become modules with their STEP in <lib>/library/<group>/;
    a module someone edited there is never rewritten by the next conversion."""
    import step_to_project as S
    lib = tmp_path / "clients" / "42"
    root = _fresh_project(tmp_path, "lib")
    S.convert(source, root, "T", log=lambda *a: None, library=lib)
    mods = sorted((lib / "library").rglob("*.py"))
    report = json.loads((root / "mechanics/cad/migration/report.json").read_text())
    libs = [r for r in report["parts"] if r.get("how") == "library"]
    assert len(mods) == len(libs) and all(m.with_suffix(".step").is_file() for m in mods), (mods, libs)
    if mods:
        mods[0].write_text(mods[0].read_text() + "\n# a real article added by hand\n")
        curated = mods[0].read_text()
        S.convert(source, _fresh_project(tmp_path, "lib2"), "T", log=lambda *a: None, library=lib)
        assert mods[0].read_text() == curated, "the client's library module was rewritten"


def test_overrides_row_fields_go_into_the_part_file_and_not_into_the_analysis(source, src_parts, tmp_path):
    """--overrides may carry a project's purchase row and process (a legacy kit's parts_meta.json): those fields land in the
    part file's P(...) as they are, while type / material steer the recognition. The row fields are not part of what a part's
    analysis is cached by, so adding them recomputes nothing."""
    import step_to_project as S
    bought = next(k for k, p in src_parts.items() if p.get("kind") != "made")
    made = next(k for k, p in src_parts.items() if p.get("kind") == "made")
    root = _fresh_project(tmp_path, "ov")
    S.convert(source, root, "T", log=lambda *a: None)
    cache = root / "build" / "step_to_project"
    before = sorted(f.name for f in cache.rglob("*") if f.is_file())
    ov = tmp_path / "ov.json"
    ov.write_text(json.dumps({"_doc": "row fields only", bought: {"desc": "Bearing 6000-2RS", "link": "https://shop.example/x",
                                                                  "supplier": "shop", "qty": 2, "kind": "bearings"},
                              made: {"process": "bent"}}, ensure_ascii=False), encoding="utf-8")
    rep = S.convert(source, root, "T", overrides=str(ov), log=lambda *a: None)
    assert sorted(f.name for f in cache.rglob("*") if f.is_file()) == before, "row fields changed the analysis cache key"
    files = {r["id"]: root / r["file"] for r in rep["parts"]}
    text = files[bought].read_text(encoding="utf-8")
    for want in ('desc="Bearing 6000-2RS"', 'link="https://shop.example/x"', 'supplier="shop"', "qty=2", 'kind="bearings"'):
        assert want in text, f"{want} not in the bought part's P(...):\n{text}"
    assert 'process="bent"' in files[made].read_text(encoding="utf-8")


def test_an_overrides_code_wins_over_the_article_read_from_his_name(source, tmp_path, monkeypatch):
    """--library reads an article out of a bought part's name (`MGN12H` of «Carriage HIWIN MGN12H») and puts it in the row's
    code; a code the project's own row gives (its old purchase list) is the one the row keeps."""
    import step_to_project as S
    orig = S.library_part
    monkeypatch.setattr(S, "library_part", lambda *a, **k: orig(*a, **k)[:2] + ("MGN12H",))   # an article read from his name
    rep = S.convert(source, _fresh_project(tmp_path, "art"), "T", log=lambda *a: None, library=tmp_path / "clients" / "a")
    lib = [r for r in rep["parts"] if r.get("how") == "library"]
    assert lib, "test setup: the source has no bought part that goes to the library"
    pid = lib[0]["id"]
    root = tmp_path / "art"
    assert 'code="MGN12H"' in (root / lib[0]["file"]).read_text(encoding="utf-8")
    ov = tmp_path / "ov.json"
    ov.write_text(json.dumps({pid: {"code": "Carriage HIWIN MGN12H"}}, ensure_ascii=False), encoding="utf-8")
    root = _fresh_project(tmp_path, "row")
    rep = S.convert(source, root, "T", overrides=str(ov), log=lambda *a: None, library=tmp_path / "clients" / "b")
    text = (root / next(r["file"] for r in rep["parts"] if r["id"] == pid)).read_text(encoding="utf-8")
    assert 'code="Carriage HIWIN MGN12H"' in text and 'code="MGN12H"' not in text, text


def test_two_parts_with_one_drawing_number_get_an_ispolnenie_code(tmp_path):
    """His two different parts with one drawing number: the first keeps it, the second gets <number>-01."""
    import step_to_project as S
    parts = {"A 01.01.001": {"kind": "made", "drawing": "01.01.001"}, "A 01.01.001_2": {"kind": "made", "drawing": "01.01.001"},
             "B 01.01.002": {"kind": "made", "drawing": "01.01.002"}}
    variant, seen = {}, {}
    for pid in sorted(parts):                       # the rule as write_project applies it
        d = parts[pid]["drawing"]
        variant[pid] = f"{d}-{seen[d]:02d}" if d in seen else d
        seen[d] = seen.get(d, 0) + 1
    assert variant == {"A 01.01.001": "01.01.001", "A 01.01.001_2": "01.01.001-01", "B 01.01.002": "01.01.002"}
    src = Path(S.__file__).read_text()
    assert 'f"{d}-{seen_code[d]:02d}"' in src, "write_project must give the second part the -01 code"


def test_two_parts_of_one_module_name_in_two_folders_both_build(tmp_path):
    """«01.01.001 <bushing>» homed in unit B and «01.02.001 <bushing>» in unit A (a Cyrillic word), A using both: two modules
    of one transliterated name, imported
    into one node without one hiding the other; each part placed as often as his CAD has it (review 6)."""
    from build123d import Box, export_step
    import step_to_project as S
    imp = tmp_path / "imp"
    (imp / "parts").mkdir(parents=True)
    shapes = {"01.01.001": Box(40, 20, 4), "01.02.001": Box(30, 10, 6), "01.03.001": Box(100, 100, 5)}
    names = {"01.01.001": "01.01.001 " + RU["bushing"], "01.02.001": "01.02.001 " + RU["bushing"], "01.03.001": "01.03.001 " + RU["slab"]}
    parts = []
    for pid, sh in shapes.items():
        export_step(sh, str(imp / "parts" / f"{pid}.step"))
        parts.append({"id": pid, "name": names[pid], "drawing": pid, "kind": "made", "volume_mm3": sh.volume})
    (imp / "parts.json").write_text(json.dumps({"assembly": {"source": "x.step"}, "parts": parts}))
    eye = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]

    def at(x, y, z):
        return [1, 0, 0, x, 0, 1, 0, y, 0, 0, 1, z, 0, 0, 0, 1]
    tree = {"name": "Machine 01.00.000", "product": "Machine 01.00.000", "ref": "R", "matrix": eye, "children": [
        {"name": "Plate", "product": names["01.03.001"], "ref": "p3", "matrix": eye, "part": "01.03.001"},
        {"name": "B", "product": "Unit B 01.01.000", "ref": "B", "matrix": at(0, 0, 5), "children": [
            {"name": "x", "product": names["01.01.001"], "ref": "px", "matrix": at(0, 0, 0), "part": "01.01.001"}]},
        {"name": "A", "product": "Unit A 01.02.000", "ref": "A", "matrix": at(50, 0, 5), "children": [
            {"name": "y", "product": names["01.02.001"], "ref": "py", "matrix": eye, "part": "01.02.001"},
            {"name": "x2", "product": names["01.01.001"], "ref": "px", "matrix": at(0, 15, 6), "part": "01.01.001"}]}]}
    (imp / "instances.json").write_text(json.dumps(tree))
    root = tmp_path / "proj"
    root.mkdir()
    S.convert(imp, root, "T", log=lambda *a: None)
    cp = run([sys.executable, CAD_INC, "--model", "assemble.py"], cwd=root / "mechanics" / "cad")
    assert cp.returncode == 0, log(cp)
    count = {spell(p["id"]): len(p.get("placements") or []) for p in model_parts(root)}
    assert {k: count.get(spell(k)) for k in shapes} == {"01.01.001": 2, "01.02.001": 1, "01.03.001": 1}, count


def test_a_library_part_cut_again_is_the_same_module(tmp_path):
    """A re-run cuts the STEP again (a new FILE_NAME timestamp): the bought part is the module already in his library, not
    a _2 copy (critic)."""
    import step_to_project as S
    from build123d import Box, export_step
    a = tmp_path / "a.step"
    export_step(Box(10, 20, 30), str(a))
    text = a.read_text()
    b = tmp_path / "b.step"
    lines = text.split("\n")
    stamp = next(i for i, x in enumerate(lines) if x.startswith("FILE_NAME("))
    lines[stamp] = "FILE_NAME('Open CASCADE Shape Model','2099-01-01T00:00:00',('Author'),("
    b.write_text("\n".join(lines))
    part = {"name": "Stepper motor NEMA17 17HS4401", "kind": "bought", "type": "motor"}
    first = S.library_part(tmp_path / "lib", part, "bought", a)
    second = S.library_part(tmp_path / "lib", part, "bought", b)
    assert first == second, (first, second)
    c = tmp_path / "c.step"
    export_step(Box(10, 20, 31), str(c))
    assert S.library_part(tmp_path / "lib", part, "bought", c)[1] != first[1], "another geometry is another module"


def test_migration_check_part_refuses_an_unknown_id(built, source):
    """--part with an id that is no part of his CAD exits 1 instead of passing on zero parts."""
    need_model(built)
    cp = run([sys.executable, SCRIPTS / "migration_check.py", built[0], "--import", source, "--part", "no_such_part"])
    assert cp.returncode == 1, log(cp)


def test_migration_check_full_report(built, source, tmp_path):
    """The whole report on the converted model: exit 0 and every row PASS; a title changed in the model fails «names»; a
    part his CAD does not have fails «parts» until migration/added.json says why it was added."""
    import shutil
    need_model(built)
    root = tmp_path / "full"
    shutil.copytree(built[0], root, symlinks=True)
    cp = run([sys.executable, MP / "derive.py", "viewer", "build/kit/viewer.json"], cwd=root)
    assert cp.returncode == 0, log(cp)
    cmd = [sys.executable, SCRIPTS / "migration_check.py", root, "--import", source, "--skip", "lists"]
    cp = run(cmd)
    assert cp.returncode == 0, log(cp)
    md = (root / "build/migration/report.md").read_text()
    for row in ("parts", "placements", "names", "geometry", "tree"):
        assert re.search(rf"^\| {row} \| PASS \|", md, re.M), f"{row} is not PASS:\n{md}"
    for row in ("lists", "shots"):                    # skipped and not given: said, not dropped (critic)
        assert re.search(rf"^\| {row} \| NOT CHECKED \|", md, re.M), f"{row} is not reported as NOT CHECKED:\n{md}"
    pj = root / "build/model/parts.json"
    model = json.loads(pj.read_text())
    good = json.dumps(model)
    model["parts"][0]["title"] = "something else"
    pj.write_text(json.dumps(model))
    cp = run(cmd)
    assert cp.returncode == 1 and re.search(r"^\| names \| FAIL \|", (root / "build/migration/report.md").read_text(), re.M), log(cp)
    model = json.loads(good)
    extra = dict(model["parts"][0], id="added_board", title="Board", name="added_board")
    model["parts"].append(extra)
    pj.write_text(json.dumps(model))
    cp = run(cmd)
    assert cp.returncode == 1 and re.search(r"^\| parts \| FAIL \|", (root / "build/migration/report.md").read_text(), re.M), log(cp)
    (root / "mechanics/cad/migration/added.json").write_text(json.dumps({"added_board": "the wiring needs it"}))
    cp = run(cmd)
    assert cp.returncode == 0, log(cp)
    assert re.search(r"^\| added \| PASS \|", (root / "build/migration/report.md").read_text(), re.M)
