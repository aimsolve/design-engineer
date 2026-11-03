#!/usr/bin/env python3
"""Bug: a machine whose root product holds NO part of its own, only sub-assemblies (the usual SolidWorks top), comes out of
step_to_project.py moved / turned as a whole: every placement in build/model/parts.json must still equal its source
instance's world matrix within 1e-6 mm (the contract of test_step_to_project.py).

Run (build123d 0.13.0; one pytest process at a time on the Mac):
  cd agent/skills/step-import/scripts/tests && <venv>/bin/python -m pytest -q test_step_to_project_root_nodes.py

Two synthetic sources, written with build123d + OCP XCAF, cut with step_import.py, converted with step_to_project.py into a
project scaffolded by machine-project's scaffold.py, built with `cad_incremental.py --model assemble.py`:

  two_units                                  root has no part, two sub-assemblies side by side
    Machine 03.00.000
      Unit A 03.01.000     at (200, -40, 15) turned 90 deg Z
        Plate A1 03.01.001   at (12, 7, 3) turned 30 deg Z           made: flat 90 x 60 x 4, 4 holes
        Plate A2 03.01.002   at (-20, 35, 9) turned -60 deg Z        made: flat 40 x 25 x 3, 2 holes
      Unit B 03.02.000     at (-150, 80, 0) turned 90 deg X, then -35 deg Z
        Plate B1 03.02.001   at (5, -8, 2) turned 45 deg Z           made: flat 70 x 50 x 5, 4 holes
        Plate B2 03.02.002   at (30, 10, 20) turned 90 deg Y        made: flat 35 x 20 x 2, 2 holes

  nested                                     the parts are two levels below the root
    Machine 04.00.000
      Frame 04.01.000      at (500, 250, -30) turned 120 deg Z
        Carriage 04.01.100   at (40, -25, 60) turned 90 deg X        a sub-sub-assembly that holds the parts
          Plate C1 04.01.101 at (7, 3, 1) turned 15 deg Z            made: flat 80 x 45 x 4, 4 holes
          Plate C2 04.01.102 at (-10, 22, 6) turned -90 deg Z        made: flat 30 x 30 x 3, 2 holes

Every matrix at every level is non-identity, the anchor part of each sub-assembly included, so a placement that ignores
where a sub-node's anchor sits inside it is off by millimetres, not by 1e-6.
"""
import json
import sys
from pathlib import Path

import pytest
from build123d import Align, Box, Cylinder, Location, Pos

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_step_to_project import (CAD_INC, CONVERT, SCAFFOLD, STEP_IMPORT, leaves, loc_mat, log,  # noqa: E402
                                  mat, mdiff, model_parts, mul, run, spell)

MATRIX_TOL = 1e-6
MIN = (Align.MIN, Align.MIN, Align.MIN)


def plate(w, h, t, holes):
    p = Box(w, h, t, align=MIN)
    for x, y in holes:
        p -= Pos(x, y, 0) * Cylinder(2.5, 3 * t + 10)
    return p


# A tree node: (product name, Location in the parent, children) for an assembly, or (product name, Location, shape) for a part.
def _plate4(w, h, t):
    return plate(w, h, t, [(8, 8), (w - 8, 8), (8, h - 8), (w - 8, h - 8)])


def _plate2(w, h, t):
    return plate(w, h, t, [(w / 3, h / 2), (2 * w / 3, h / 2)])


CASES = {
    "two_units": ("Machine 03.00.000", [
        ("Unit A 03.01.000", Location((200, -40, 15), (0, 0, 90)), [
            ("Plate A1 03.01.001", Location((12, 7, 3), (0, 0, 30)), lambda: _plate4(90, 60, 4)),
            ("Plate A2 03.01.002", Location((-20, 35, 9), (0, 0, -60)), lambda: _plate2(40, 25, 3)),
        ]),
        ("Unit B 03.02.000", Location((-150, 80, 0)) * Location((0, 0, 0), (0, 0, -35)) * Location((0, 0, 0), (90, 0, 0)), [
            ("Plate B1 03.02.001", Location((5, -8, 2), (0, 0, 45)), lambda: _plate4(70, 50, 5)),
            ("Plate B2 03.02.002", Location((30, 10, 20), (0, 90, 0)), lambda: _plate2(35, 20, 2)),
        ]),
    ]),
    "nested": ("Machine 04.00.000", [
        ("Frame 04.01.000", Location((500, 250, -30), (0, 0, 120)), [
            ("Carriage 04.01.100", Location((40, -25, 60), (90, 0, 0)), [
                ("Plate C1 04.01.101", Location((7, 3, 1), (0, 0, 15)), lambda: _plate4(80, 45, 4)),
                ("Plate C2 04.01.102", Location((-10, 22, 6), (0, 0, -90)), lambda: _plate2(30, 30, 3)),
            ]),
        ]),
    ]),
}


def expected_world(case):
    """{product name of a part: world 4x4} from the tree as written (the source of truth, not step_import's reading of it)."""
    out = {}

    def walk(children, parent):
        for name, at, sub in children:
            w = mul(parent, loc_mat(at))
            if callable(sub):
                out[name] = w
            else:
                walk(sub, w)
    walk(CASES[case][1], loc_mat(Location((0, 0, 0))))
    return out


def write_step(path, case):
    """The case's tree as an XCAF assembly with product names; the root product gets no shape of its own."""
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
    root_name, children = CASES[case]
    root = named(st.NewShape(), root_name)

    def add(parent, kids):
        for name, at, sub in kids:
            if callable(sub):
                lbl = named(st.AddShape(baked(sub()), False), name)
            else:
                lbl = named(st.NewShape(), name)
                add(lbl, sub)
            st.AddComponent(parent, lbl, loc(at))
    add(root, children)
    st.UpdateAssemblies()
    w = STEPCAFControl_Writer()
    w.SetNameMode(True)
    w.Transfer(doc, STEPControl_AsIs)
    assert w.Write(str(path)) == 1, "test setup: STEP write failed"


@pytest.fixture(scope="module", params=sorted(CASES))
def built(request, tmp_path_factory):
    """(case, import dir, project root, CompletedProcess of the convert, CompletedProcess of the --model build or None)."""
    case = request.param
    d = tmp_path_factory.mktemp(case)
    write_step(d / "in.step", case)
    cp = run([sys.executable, STEP_IMPORT, d / "in.step", "--out", d / "imp"])
    assert cp.returncode == 0, "test setup: step_import.py failed\n" + log(cp)
    root = d / "proj"
    cp = run([sys.executable, SCAFFOLD, root, "--name", "t", "--title", "T"])
    assert cp.returncode == 0, "test setup: scaffold.py failed\n" + log(cp)
    conv = run([sys.executable, CONVERT, d / "imp", root, "--title", "T"])
    model = run([sys.executable, CAD_INC, "--model", "assemble.py"], cwd=root / "mechanics" / "cad") \
        if conv.returncode == 0 else None
    return case, d / "imp", root, conv, model


def test_every_placement_stands_where_the_source_instance_stands_when_the_root_has_no_part(built):
    """The root product holds only sub-assemblies: each placement's world matrix (parts.json `loc`) equals its source
    instance's world matrix within 1e-6 mm, the parts of every sub-assembly included."""
    case, imp, root, conv, model = built
    assert conv.returncode == 0, "step_to_project.py IMPORT ROOT --title T failed\n" + log(conv)
    assert model is not None and model.returncode == 0, \
        "cad_incremental.py --model assemble.py failed on the converted project\n" + log(model)

    # where every part must stand: the tree as written; step_import's instances.json must agree (else the bug is upstream)
    want_by_name = expected_world(case)
    src = {p["id"]: p for p in json.loads((imp / "parts.json").read_text())["parts"]}
    by_name = {p["name"]: pid for pid, p in src.items()}
    assert set(by_name) == set(want_by_name), f"test setup: parts.json names {sorted(by_name)} vs {sorted(want_by_name)}"
    want = {spell(by_name[n]): w for n, w in want_by_name.items()}
    lv = leaves(json.loads((imp / "instances.json").read_text()))
    assert len(lv) == len(want), f"test setup: instances.json has {len(lv)} leaves, the source {len(want)}"
    for pid, m, _ in lv:
        assert mdiff(m, want[spell(pid)]) < 1e-6, f"test setup: step_import put {pid} at {m}, the source at {want[spell(pid)]}"

    parts = model_parts(root)
    got = {spell(p["id"]): [mat(pl["loc"]) for pl in p.get("placements") or []] for p in parts}
    assert set(got) == set(want), f"model ids {sorted(got)} vs source {sorted(want)}"
    off = []
    for pid, w in sorted(want.items()):
        assert len(got[pid]) == 1, f"{pid}: {len(got[pid])} placements, the source has 1 instance"
        d = mdiff(got[pid][0], w)
        if d > MATRIX_TOL:
            off.append(f"  {pid}: {d:.4g} off\n    model  {[[round(x, 4) for x in r] for r in got[pid][0]]}"
                       f"\n    source {[[round(x, 4) for x in r] for r in w]}")
    assert not off, f"[{case}] {len(off)} of {len(want)} placements are not where the source instance is:\n" + "\n".join(off)
