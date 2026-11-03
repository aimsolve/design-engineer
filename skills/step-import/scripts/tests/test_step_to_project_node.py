#!/usr/bin/env python3
"""Tests for `step_to_project.py IMPORT ROOT --node <parent node folder>`: a STEP of a NODE (a
sub-assembly) the client sends for an EXISTING code project is ported into that project as a node folder, and nothing
else of the project changes.

Run (build123d 0.13.0; one pytest process at a time on the Mac):
  cd agent/skills/step-import/scripts/tests && <venv>/bin/python -m pytest -q test_step_to_project_node.py

The existing project: machine-project's scaffold.py with --cad build123d (params.py, assemble.py, geom.py, parts/__init__.py,
the node parts/example/ with block.py, the node parts/fasteners/). The node is ported into parts/example/.

The source is synthetic, written here with build123d + OCP XCAF (shapes reused from test_step_to_project.py):

    Machine 01.00.000                        root product (the client's machine; the node is all of it he exported)
      Pusher unit 02.00.000    at NODE_AT    THE NODE: placed in his machine at (300, 150, 40) turned 90 deg Z
        Bracket 02.00.001                    made: bent sheet L, at (2, 1, 0) in the node
        Screw DIN 912 M5x25      (x2)        standard, at (20, 12.5, -10) turned 30 deg Z and at (20, 30, -10)
        Clamp 02.01.000                      sub-node, at (60, 0, 10) turned 180 deg Z
          Clamp plate 02.01.001              made: flat 30 x 20 x 3 plate, 2 holes d5, at the sub-node's origin

  -> 3 distinct parts, 4 leaf instances. Cut with `step_import.py in.step --out IMPORT`, converted with
  `step_to_project.py IMPORT ROOT --node example`.
  A second, cheaper source is the same node exported on its own (its root product IS the node, no machine around it).

Contract under test (from the ticket, not from the code):
  - the CLI exits 0 and writes the node as ONE new folder parts/example/<node>/ (__init__.py with TITLE = the node's
    product name, one <part>.py per distinct part of the node somewhere inside it, the sub-node as a folder inside it
    with TITLE = its product name);
  - every file of the project that existed before the run is byte-identical after it, and none is removed; no new file
    appears under parts/ outside the new node folder (assemble.py, params.py, geom.py, the root, the other nodes and
    their part files are left alone: the agent docks the node itself);
  - mechanics/cad/migration/ holds, in a JSON file, an object naming the node (its product name or its folder) with a
    "matrix" (16 floats row-major, or 4 rows of 4; mm, rigid) = where the client's CAD places the node. Which frame of the
    node it locates (the node's own origin, or the part the node is anchored on) is the converter's choice; what is
    tested is that it is the one the agent docks by:
  - the node folder is a working code node: docked by hand at that matrix (parts/__init__.py build() places the node's
    build() list there), `cad_incremental.py --model assemble.py` builds, and every one of the node's leaf instances
    stands where the client's CAD has it, within 1e-6 mm (for the machine-root source: in the machine);
  - `--node` naming a folder that is not a node of the project fails (exit != 0) and changes nothing.
"""
import hashlib
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path

import pytest
from build123d import Align, Box, Cylinder, Location, Pos

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_step_to_project import (CAD_INC, CONVERT, SCAFFOLD, STEP_IMPORT, bracket_shape, leaves, log,  # noqa: E402
                                  mat, mdiff, model_parts, module_title, run, screw_shape, spell)

MACHINE = "Machine 01.00.000"
NODE = "Pusher unit 02.00.000"
SUB = "Clamp 02.01.000"
BRACKET, SCREW, CLAMP_PLATE = "Bracket 02.00.001", "Screw DIN 912 M5x25", "Clamp plate 02.01.001"
PARENT = "example"                                   # the scaffold's node the new node goes under
NODE_AT = Location((300, 150, 40), (0, 0, 90))
BRACKET_IN_NODE = Location((2, 1, 0))
SCREWS_IN_NODE = [Location((20, 12.5, -10), (0, 0, 30)), Location((20, 30, -10))]
SUB_IN_NODE = Location((60, 0, 10), (0, 0, 180))
PLATE_IN_SUB = Location((0, 0, 0))
MATRIX_TOL = 1e-6
IDENTITY = [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]
MIN = (Align.MIN, Align.MIN, Align.MIN)


# ---------------------------------------------------------------- the synthetic node STEP

def clamp_plate_shape():
    p = Box(30, 20, 3, align=MIN)
    for x, y in [(6, 10), (24, 10)]:
        p -= Pos(x, y, 0) * Cylinder(2.5, 10)
    return p


def write_node_step(path, in_machine=True):
    """The node as an XCAF assembly written as AP214 STEP: under a machine root at NODE_AT (in_machine), or as the root."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
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
        t = shape.wrapped.Location().Transformation()
        out = BRepBuilderAPI_Transform(shape.wrapped.Located(TopLoc_Location()), t, True).Shape()
        assert out.Location().IsIdentity()
        return out

    loc = lambda l: TopLoc_Location(l.wrapped.Transformation())
    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    app.InitDocument(doc)
    st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    root = named(st.NewShape(), MACHINE) if in_machine else None
    node = named(st.NewShape(), NODE)
    sub = named(st.NewShape(), SUB)
    lb = named(st.AddShape(baked(bracket_shape()), False), BRACKET)
    ls = named(st.AddShape(baked(screw_shape()), False), SCREW)
    lp = named(st.AddShape(baked(clamp_plate_shape()), False), CLAMP_PLATE)
    st.AddComponent(sub, lp, loc(PLATE_IN_SUB))
    st.AddComponent(node, lb, loc(BRACKET_IN_NODE))
    for at in SCREWS_IN_NODE:
        st.AddComponent(node, ls, loc(at))
    st.AddComponent(node, sub, loc(SUB_IN_NODE))
    if in_machine:
        st.AddComponent(root, node, loc(NODE_AT))
    st.UpdateAssemblies()
    w = STEPCAFControl_Writer()
    w.SetNameMode(True)
    w.Transfer(doc, STEPControl_AsIs)
    assert w.Write(str(path)) == 1, "test setup: STEP write failed"


# ---------------------------------------------------------------- helpers

def snapshot(root):
    """{relative path: sha256} of every file of the project (build/ and caches left out)."""
    out = {}
    for p in Path(root).rglob("*"):
        rel = p.relative_to(root)
        if p.is_file() and "build" not in rel.parts and "__pycache__" not in rel.parts:
            out[str(rel)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def node_dirs(root):
    parent = Path(root) / "mechanics" / "cad" / "parts" / PARENT
    return {p.name for p in parent.iterdir() if p.is_dir() and p.name != "__pycache__"}


def json_objects(x):
    if isinstance(x, dict):
        yield x
        for v in x.values():
            yield from json_objects(v)
    elif isinstance(x, list):
        for v in x:
            yield from json_objects(v)


def strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for k, v in x.items():
            yield k
            yield from strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from strings(v)


def node_matrices(root, folder):
    """[(file, matrix)] of every object in mechanics/cad/migration/*.json that holds a 'matrix' and names the node (its
    product name, or its folder name / path)."""
    out = []
    for f in sorted((Path(root) / "mechanics" / "cad" / "migration").rglob("*.json")):
        try:
            data = json.loads(f.read_text())
        except ValueError:
            continue
        for o in json_objects(data):
            if "matrix" not in o:
                continue
            names = set(strings({k: v for k, v in o.items() if k != "matrix"}))
            if any(NODE in s or s == folder or s.rstrip("/").endswith("/" + folder) for s in names):
                try:
                    out.append((f.name, mat(o["matrix"])))
                except (AssertionError, TypeError, ValueError):
                    pass
    return out


# ---------------------------------------------------------------- fixtures (they never assert on the code under test)

def _import(tmp, in_machine):
    write_node_step(tmp / "node.step", in_machine=in_machine)
    cp = run([sys.executable, STEP_IMPORT, tmp / "node.step", "--out", tmp / "imp"])
    assert cp.returncode == 0, "test setup: step_import.py failed\n" + log(cp)
    return tmp / "imp"


def _project(tmp):
    root = tmp / "t"
    cp = run([sys.executable, SCAFFOLD, root, "--name", "t", "--title", "T", "--cad", "build123d"])
    assert cp.returncode == 0, "test setup: scaffold.py --cad build123d failed\n" + log(cp)
    assert (root / "mechanics" / "cad" / "parts" / PARENT / "__init__.py").is_file(), "test setup: no parts/example node"
    return root


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    return _import(tmp_path_factory.mktemp("src"), in_machine=True)


@pytest.fixture(scope="module")
def ported(source, tmp_path_factory):
    """(root, files before, node folders before, CompletedProcess of `step_to_project.py IMPORT ROOT --node example`)."""
    root = _project(tmp_path_factory.mktemp("proj"))
    before, dirs = snapshot(root), node_dirs(root)
    return root, before, dirs, run([sys.executable, CONVERT, source, root, "--node", PARENT])


def dock(root0, folder, m, dest):
    """A copy of the project at `dest` where the machine (parts/__init__.py build(), called by assemble.py) places the ported
    node's build() list at the matrix `m` (the agent's docking, done the simplest way), built with `cad_incremental.py
    --model`: (root, CompletedProcess)."""
    root = Path(dest) / "t"
    shutil.copytree(root0, root, symlinks=True, ignore=shutil.ignore_patterns("build", "__pycache__"))
    cad = root / "mechanics" / "cad"
    (cad / "parts" / "__init__.py").write_text(
        "import importlib\nfrom build123d import Location\nfrom OCP.gp import gp_Trsf\nfrom OCP.TopLoc import TopLoc_Location\n"
        "from geom import place\n\n\n"
        "def build():\n"
        f"    node = importlib.import_module(__name__ + '.{PARENT}.{folder}')\n"
        f"    m = {m!r}\n"
        "    t = gp_Trsf()\n    t.SetValues(*m[0], *m[1], *m[2])\n"
        "    return place(node.build(), Location(TopLoc_Location(t)))\n")
    (cad / "assemble.py").write_text(
        "import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).parent))\nimport parts\n\n"
        "items = parts.build()\nprint(f'{len(items)} placed parts')\n")
    return root, run([sys.executable, CAD_INC, "--model", "assemble.py"], cwd=cad)


def misplaced(root, instances):
    """[] when every leaf instance of `instances` (world matrices, composed) has its own placement in the built model of
    `root` within 1e-6 mm, else what is wrong."""
    model = {spell(p["id"]): p for p in model_parts(root)}
    want = defaultdict(list)
    for pid, m, _ in leaves(instances):
        want[spell(pid)].append(m)
    errs = []
    for pid, mats in want.items():
        if pid not in model:
            errs.append(f"part {pid} of the node is not in the built model: {sorted(model)}")
            continue
        left = [mat(pl["loc"]) for pl in model[pid].get("placements") or []]
        if len(left) != len(mats):
            errs.append(f"{pid}: {len(left)} placements, the node has {len(mats)} instances")
            continue
        for w in mats:
            best = min(range(len(left)), key=lambda i: mdiff(left[i], w))
            if mdiff(left[best], w) > MATRIX_TOL:
                errs.append(f"{pid}: no placement at {w}; nearest {left[best]}")
            left.pop(best)
    return errs


def check_docking(root0, folder, instances, tmp):
    """Dock the node at each matrix the migration reports for it; passes when one of them builds and puts every part where
    his CAD has it."""
    ms = node_matrices(root0, folder)
    assert ms, f"no object in mechanics/cad/migration/*.json names the node ({NODE!r} or {folder!r}) with a 'matrix'"
    tried = []
    for i, (fname, m) in enumerate(ms):
        root, cp = dock(root0, folder, m, Path(tmp) / f"dock{i}")
        if cp.returncode != 0:
            tried.append(f"{fname} {m}: cad_incremental.py --model failed\n{log(cp)}")
            continue
        errs = misplaced(root, instances)
        if not errs:
            return
        tried.append(f"{fname} {m}: " + "; ".join(errs))
    pytest.fail("docked at the reported matrix, the node's parts do not stand where his CAD has them:\n" + "\n".join(tried))


def need_port(ported):
    root, before, dirs, cp = ported
    assert cp.returncode == 0, "step_to_project.py IMPORT ROOT --node example failed\n" + log(cp)
    return root, before, dirs


def need_node(ported):
    root, _, dirs = need_port(ported)
    new = sorted(node_dirs(root) - dirs)
    assert len(new) == 1, f"one new node folder under parts/{PARENT}/ expected, got {new}"
    return root, root / "mechanics" / "cad" / "parts" / PARENT / new[0]


def need_instances(source):
    f = source / "instances.json"
    assert f.is_file(), "step_import.py wrote no instances.json"
    return json.loads(f.read_text())


# ---------------------------------------------------------------- the node folder

def test_cli_ports_the_node_into_one_new_folder_under_the_parent(ported):
    """Exit 0; exactly one new folder under parts/example/, its __init__.py with TITLE = the node's product name."""
    _, node = need_node(ported)
    assert (node / "__init__.py").is_file(), f"{node.name}/__init__.py not written"
    assert module_title(node / "__init__.py") == NODE, \
        f"TITLE of {node.name}/__init__.py is {module_title(node / '__init__.py')!r}, want {NODE!r}"


def test_the_node_folder_holds_its_parts_and_its_sub_node(ported):
    """One <part>.py per distinct part of the node inside the folder (3), and the sub-node as a folder with its TITLE."""
    _, node = need_node(ported)
    files = [p for p in node.rglob("*.py") if p.name not in ("__init__.py", "params.py") and not p.name.startswith("_")]
    assert len(files) == 3, f"one <part>.py per distinct part (3: bracket, screw, clamp plate), got " \
                            f"{[str(f.relative_to(node)) for f in files]}"
    subs = {p.parent: module_title(p) for p in node.rglob("__init__.py") if p.parent != node}
    assert SUB in subs.values(), f"no sub-node folder with TITLE {SUB!r} inside {node.name}: {subs}"


# ---------------------------------------------------------------- the rest of the project stays as it was

def test_every_existing_file_of_the_project_is_left_untouched(ported):
    """assemble.py, params.py, geom.py, kit.json, parts/__init__.py, parts/example/*, parts/fasteners/*: byte-identical,
    none removed."""
    root, before, _ = need_port(ported)
    after = snapshot(root)
    gone = sorted(set(before) - set(after))
    changed = sorted(f for f in before if f in after and after[f] != before[f])
    assert not gone and not changed, f"the port changed existing files: changed {changed}, removed {gone}"


def test_no_new_file_under_parts_outside_the_node_folder(ported):
    root, node = need_node(ported)
    _, before, _ = need_port(ported)
    parts = root / "mechanics" / "cad" / "parts"
    new = [f for f in set(snapshot(root)) - set(before)
           if (root / f).is_relative_to(parts) and not (root / f).is_relative_to(node)]
    assert not new, f"new files under parts/ outside the node folder {node.name}: {sorted(new)}"


# ---------------------------------------------------------------- where the client's CAD places the node

def is_rigid(m):
    r = [row[:3] for row in m[:3]]
    rrt = [[sum(r[i][k] * r[j][k] for k in range(3)) for j in range(3)] for i in range(3)]
    return mdiff([row + [0.0] for row in rrt] + [[0.0, 0.0, 0.0, 1.0]], IDENTITY) < 1e-6 and \
        max(abs(x) for x in m[3][:3]) < 1e-9 and abs(m[3][3] - 1) < 1e-9


def test_migration_reports_where_his_cad_places_the_node(ported):
    """mechanics/cad/migration/*.json: an object naming the node (product name or folder) with a rigid 4x4 "matrix" (mm),
    and it is not the identity: the source has the node at (300, 150, 40) turned 90 deg in his machine."""
    root, node = need_node(ported)
    ms = node_matrices(root, node.name)
    assert ms, f"no object in mechanics/cad/migration/*.json names the node ({NODE!r} or {node.name!r}) with a 'matrix'"
    assert all(is_rigid(m) for _, m in ms), f"not a rigid 4x4 placement: {ms}"
    assert any(mdiff(m, IDENTITY) > 1 for _, m in ms), f"the node is reported at the origin, his CAD has it elsewhere: {ms}"


def test_the_docked_node_builds_and_every_part_stands_where_his_cad_has_it(ported, source, tmp_path):
    """The machine places the node's build() at its reported matrix; cad_incremental --model builds; each of the node's 4
    leaf instances has a placement whose world matrix equals the source instance's (instances.json, composed) within 1e-6."""
    root, node = need_node(ported)
    instances = need_instances(source)
    assert len(leaves(instances)) == 4, "test setup: the source has 4 leaf instances"
    check_docking(root, node.name, instances, tmp_path)


def test_a_node_exported_on_its_own_is_ported_and_docks(tmp_path):
    """The node's own STEP (its root product IS the node, no machine around it): ported as one folder with that TITLE, a
    matrix reported, and docked at it every part stands where that STEP has it."""
    (tmp_path / "solo").mkdir()
    src = _import(tmp_path / "solo", in_machine=False)
    root = _project(tmp_path)
    dirs = node_dirs(root)
    cp = run([sys.executable, CONVERT, src, root, "--node", PARENT])
    assert cp.returncode == 0, "step_to_project.py --node on a node exported on its own failed\n" + log(cp)
    new = sorted(node_dirs(root) - dirs)
    assert len(new) == 1, f"one new node folder expected, got {new}"
    assert module_title(root / "mechanics/cad/parts" / PARENT / new[0] / "__init__.py") == NODE
    check_docking(root, new[0], need_instances(src), tmp_path)
    pj = json.loads(next((root / "mechanics/cad/migration").rglob("placement.json")).read_text())
    assert pj.get("frame") == "own", pj
    m = [row[:] for row in node_matrices(root, new[0])[0][1]]
    m[0][3] += 500                                    # anywhere: its place is the agent's, the check must say it did not check it
    built, cp = dock(root, new[0], m, tmp_path / "far")
    assert cp.returncode == 0, log(cp)
    cp = run([sys.executable, MIGRATION_CHECK, built, "--import", src, "--part", pj["anchor"]])
    assert cp.returncode == 0 and "NOT CHECKED" in cp.stdout, log(cp)


# ---------------------------------------------------------------- failure path

def test_an_unknown_parent_node_is_refused_and_nothing_changes(source, tmp_path):
    root = _project(tmp_path)
    before = snapshot(root)
    cp = run([sys.executable, CONVERT, source, root, "--node", "no_such_node"])
    assert cp.returncode != 0, "--node naming a folder that is not a node of the project must fail\n" + log(cp)
    assert "unrecognized arguments" not in cp.stderr, "step_to_project.py has no --node option\n" + log(cp)
    assert "no_such_node" in cp.stdout + cp.stderr, "the refusal does not name the missing node folder\n" + log(cp)
    assert snapshot(root) == before, "a refused --node run changed the project"
    for empty in ("/", ""):                           # strips to nothing: never a quiet full port of the existing project
        cp = run([sys.executable, CONVERT, source, root, "--node", empty])
        assert cp.returncode != 0, f"--node {empty!r} must fail\n" + log(cp)
        assert snapshot(root) == before, f"a refused --node {empty!r} run changed the project"


# ---------------------------------------------------------------- third review: checking a node, two nodes, imports

MIGRATION_CHECK = HERE.parent / "migration_check.py"


def test_migration_check_part_checks_a_ported_and_docked_node(ported, source, tmp_path):
    """--part on a part of the node finds the node's own migration folder, checks it in the frame the node is docked in:
    exit 0; a part his reference places 0.01 mm elsewhere inside the node: exit 1."""
    root, node = need_node(ported)
    m = node_matrices(root, node.name)[0][1]
    built, cp = dock(root, node.name, m, tmp_path)
    assert cp.returncode == 0, log(cp)
    mig = next((built / "mechanics/cad/migration").rglob("reference.json"))
    ref = json.loads(mig.read_text())
    pid = next(i for i, r in ref["parts"].items() if r["name"] == CLAMP_PLATE)
    cmd = [sys.executable, MIGRATION_CHECK, built, "--import", source, "--part", pid]
    cp = run(cmd)
    assert cp.returncode == 0, log(cp)
    for pl in ref["placements"]:
        if pl["part"] == pid:
            pl["matrix"][3] += 0.01
    mig.write_text(json.dumps(ref))
    assert run(cmd).returncode == 1, "a node part 0.01 mm off his must fail"


def test_two_node_ports_of_one_name_keep_each_others_files(source, tmp_path):
    """The same node ported under two parents: two folders, two migration folders, the first port's files intact."""
    root = _project(tmp_path)
    assert run([sys.executable, CONVERT, source, root, "--node", PARENT]).returncode == 0
    first = snapshot(root)
    cp = run([sys.executable, CONVERT, source, root, "--node", "fasteners"])
    assert cp.returncode == 0, log(cp)
    after = snapshot(root)
    assert all(after.get(k) == v for k, v in first.items() if "/migration/" in k or "/parts/example/" in k or "/vendor/" in k), \
        "the second node port changed the first one's files"
    placements = sorted(str(f.relative_to(root)) for f in (root / "mechanics/cad/migration").rglob("placement.json"))
    assert len(placements) == 2, placements


def test_a_node_whose_parts_the_project_already_has_builds_beside_them(source, tmp_path):
    """The same node ported under two parents (a node sharing parts with the project, or replacing one): the second
    port's parts get ids the project does not have yet, keep his name, and both nodes docked together build (review 4)."""
    root = _project(tmp_path)
    folders = []
    for parent in (PARENT, "fasteners"):
        was = {d.name for d in (root / "mechanics/cad/parts" / parent).iterdir() if d.is_dir()}
        cp = run([sys.executable, CONVERT, source, root, "--node", parent])
        assert cp.returncode == 0, log(cp)
        new = [d.name for d in (root / "mechanics/cad/parts" / parent).iterdir() if d.is_dir() and d.name not in was
               and d.name != "__pycache__"]
        assert len(new) == 1, new
        folders.append(f"{parent}.{new[0]}")
    reports = [json.loads(f.read_text()) for f in sorted((root / "mechanics/cad/migration").rglob("report.json"))]
    assert len(reports) == 2
    ids = [{r["id"] for r in rep["parts"]} for rep in reports]
    assert not ids[0] & ids[1], f"two node ports declare the same part ids: {ids[0] & ids[1]}"
    renamed = [r for rep in reports for r in rep["parts"] if r.get("his_id")]
    assert renamed and all(r["id"] != r["his_id"] for r in renamed)
    cad = root / "mechanics" / "cad"
    (cad / "parts" / "__init__.py").write_text(
        "import importlib\n\n\ndef build():\n"
        f"    return [x for f in {folders!r} for x in importlib.import_module(__name__ + '.' + f).build()]\n")
    (cad / "assemble.py").write_text(
        "import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).parent))\nimport parts\n\n"
        "items = parts.build()\nprint(f'{len(items)} placed parts')\n")
    cp = run([sys.executable, CAD_INC, "--model", "assemble.py"], cwd=cad)
    assert cp.returncode == 0, "both node ports docked together do not build\n" + log(cp)
    built = {spell(p["id"]): p for p in model_parts(root)}
    for r in renamed:
        assert spell(r["id"]) in built and built[spell(r["id"])].get("title") == r["name"], r


def test_migration_check_part_fails_a_node_docked_away_from_his_machine(ported, source, tmp_path):
    """The node came inside his machine (placement.json frame «machine»): docked 500 mm from where his machine has it,
    --part on any of its parts fails (critic)."""
    root, node = need_node(ported)
    pj = json.loads(next((root / "mechanics/cad/migration").rglob("placement.json")).read_text())
    assert pj.get("frame") == "machine", pj
    m = [row[:] for row in node_matrices(root, node.name)[0][1]]
    m[0][3] += 500
    built, cp = dock(root, node.name, m, tmp_path)
    assert cp.returncode == 0, log(cp)
    ref = json.loads(next((built / "mechanics/cad/migration").rglob("reference.json")).read_text())
    cp = run([sys.executable, MIGRATION_CHECK, built, "--import", source, "--part", pj["anchor"]])
    assert cp.returncode == 1, "a node docked 500 mm off his machine's place must fail\n" + log(cp)
    assert ref["parts"]


def test_a_project_with_an_older_fastener_template_still_builds(source, tmp_path):
    """The project's parts/fasteners/screw.py is an older copy that does not know DIN 912: the node's screw stays on the
    maker's 3D instead of calling a variant the project's template lacks, and the docked node builds (review 7)."""
    root = _project(tmp_path)
    tf = root / "mechanics/cad/parts/fasteners/screw.py"
    tf.write_text(tf.read_text().replace('"DIN 912"', '"DIN 9120"'))
    cp = run([sys.executable, CONVERT, source, root, "--node", PARENT])
    assert cp.returncode == 0, log(cp)
    rows = json.loads(next((root / "mechanics/cad/migration").rglob("report.json")).read_text())["parts"]
    screw = [r for r in rows if r["name"] == SCREW]
    assert screw and all(r.get("how") != "standard" for r in screw), screw
    folder = next(d.name for d in (root / "mechanics/cad/parts" / PARENT).iterdir() if (d / "__init__.py").is_file())
    _, cp = dock(root, folder, node_matrices(root, folder)[0][1], tmp_path / "dock")
    assert cp.returncode == 0, "the node with the older template does not build\n" + log(cp)


def test_migration_check_part_checks_a_renamed_node_part(source, tmp_path):
    """A node part renamed because the project had its id (<id>_2): --part on it finds his STEP through his_id, passes,
    and fails when his reference places it 0.01 mm elsewhere (review 5)."""
    root = _project(tmp_path)
    for parent in (PARENT, "fasteners"):
        cp = run([sys.executable, CONVERT, source, root, "--node", parent])
        assert cp.returncode == 0, log(cp)
    second = [d for d in (root / "mechanics/cad/parts/fasteners").iterdir() if (d / "__init__.py").is_file()]
    assert len(second) == 1, second
    folder = second[0].name
    mig = next(f for f in (root / "mechanics/cad/migration").rglob("reference.json") if "fasteners" in str(f))
    ref = json.loads(mig.read_text())
    pid = next(i for i, r in ref["parts"].items() if r.get("his_id") and r["name"] == CLAMP_PLATE)
    cad = root / "mechanics" / "cad"
    m = json.loads((mig.parent / "placement.json").read_text())["matrix"]
    m = [m[0:4], m[4:8], m[8:12]]
    (cad / "parts" / "__init__.py").write_text(
        "import importlib\nfrom build123d import Location\nfrom OCP.gp import gp_Trsf\nfrom OCP.TopLoc import TopLoc_Location\n"
        "from geom import place\n\n\n"
        "def build():\n"
        f"    node = importlib.import_module(__name__ + '.fasteners.{folder}')\n"
        f"    m = {m!r}\n"
        "    t = gp_Trsf()\n    t.SetValues(*m[0], *m[1], *m[2])\n"
        "    return place(node.build(), Location(TopLoc_Location(t)))\n")
    (cad / "assemble.py").write_text(
        "import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).parent))\nimport parts\n\n"
        "items = parts.build()\nprint(f'{len(items)} placed parts')\n")
    cp = run([sys.executable, CAD_INC, "--model", "assemble.py"], cwd=cad)
    assert cp.returncode == 0, log(cp)
    cmd = [sys.executable, MIGRATION_CHECK, root, "--import", source, "--part", pid]
    cp = run(cmd)
    assert cp.returncode == 0, log(cp)
    for pl in ref["placements"]:
        if pl["part"] == pid:
            pl["matrix"][3] += 0.01
    mig.write_text(json.dumps(ref))
    assert run(cmd).returncode == 1, "a renamed node part 0.01 mm off his must fail"


def test_a_node_port_from_the_step_leaves_the_machines_import_alone(tmp_path):
    """The CLI given the node's STEP cuts it into a folder of its own: the machine's build/client_import stays as it was."""
    root = _project(tmp_path)
    machine = root / "build" / "client_import"
    machine.mkdir(parents=True)
    (machine / "instances.json").write_text('{"name": "the machine"}')
    write_node_step(tmp_path / "node.step", in_machine=True)
    cp = run([sys.executable, CONVERT, tmp_path / "node.step", root, "--node", PARENT])
    assert cp.returncode == 0, log(cp)
    assert (machine / "instances.json").read_text() == '{"name": "the machine"}'
    assert not (machine / "parts.json").exists(), "the node was cut into the machine's import folder"
