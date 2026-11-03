"""Synthetic projects for issue #78 «mesh parts»: first-class printed parts whose geometry is a closed triangle mesh
(`meshpart.Mesh`), built by `cad_incremental.py --model` like any part file. Shared by test_mesh_solid*.py.

The MAIN project (node `frame`, folder layout, every part in its own coordinates, docked by stock build123d joints):

    base   STEP, mill   Box 200 x 100 x 10, corner at the origin; placed at Pos(0, 0, 0) (the root).
                        joints on its top face (Z up): shell_seat_a (SEAT_X, 20, 10), shell_seat_b (100, 20, 10),
                        wedge_seat (160, 70, 10) turned 90 deg about Z, twin_seat (40, 70, 10)
    shell  MESH, print  meshpart.Mesh(meshpart.solid(Box(SHELL_W, 10, 30), centred in X/Y, bottom at z = 0)), PETG;
                        joints bottom (origin) and top (0, 0, 30). Built twice: shell_a docked on shell_seat_a,
                        shell_b on shell_seat_b (mesh part docked onto a STEP part, two placements, qty 2)
    cap    STEP, mill   Box 10 x 10 x 4 centred, bottom at z = 0, joint bottom; docked on shell_a's `top`
                        (a STEP part docked onto a joint of the mesh part)
    wedge  MESH, print  Box 10 x 20 x 30 centred, bottom at 0, made with meshpart.solid, print_rot ["X", 90], returned as a
                        copy.deepcopy of the Mesh (joints included); docked on wedge_seat, so its first placement is turned
                        90 deg about Z
    twin   MESH, other  a 12 mm cube given as raw vertices + triangles (Mesh(vertices=..., triangles=...)), joint bottom at
                        the centre of its bottom face, returned as copy.copy of the Mesh; docked on twin_seat

    params.py: SHELL_W = 40.0 (read by shell.py only), SEAT_X = 30.0 (read by base.py only)

World boxes (mm) with the params above:
    shell_a [10, 15, 10] .. [50, 25, 40]      shell_b [80, 15, 10] .. [120, 25, 40]      cap [25, 15, 40] .. [35, 25, 44]
    wedge   [150, 65, 10] .. [170, 75, 40]    twin    [34, 64, 10] .. [46, 76, 22]
"""
import json
import textwrap
from pathlib import Path

import numpy as np
import pytest

import helpers as H

PRE = "import sys, os\nsys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"

PARAMS = "SHELL_W = 40.0\nSEAT_X = 30.0\nUNUSED = 1.0\n"

BASE = '''
import params
from build123d import Align, Box, Location, RigidJoint
from geom import P


def build():
    s = Box(200, 100, 10, align=(Align.MIN, Align.MIN, Align.MIN))
    RigidJoint("shell_seat_a", s, Location((params.SEAT_X, 20, 10)))
    RigidJoint("shell_seat_b", s, Location((100, 20, 10)))
    RigidJoint("wedge_seat", s, Location((160, 70, 10), (0, 0, 90)))
    RigidJoint("twin_seat", s, Location((40, 70, 10)))
    return P("base", s, "#888888", process="mill", material="S235JR")
'''

SHELL = '''
import params
import meshpart
from build123d import Align, Box, Location, RigidJoint
from geom import P


def build():
    body = meshpart.solid(Box(params.SHELL_W, 10, 30, align=(Align.CENTER, Align.CENTER, Align.MIN)))
    m = meshpart.Mesh(body)
    RigidJoint("bottom", m, Location((0, 0, 0)))
    RigidJoint("top", m, Location((0, 0, 30)))
    return P("shell", m, "#dddddd", process="print", material="PETG")
'''

CAP = '''
from build123d import Align, Box, Location, RigidJoint
from geom import P


def build():
    s = Box(10, 10, 4, align=(Align.CENTER, Align.CENTER, Align.MIN))
    RigidJoint("bottom", s, Location((0, 0, 0)))
    return P("cap", s, "#4444aa", process="mill", material="AL6060")
'''

WEDGE = '''
import copy
import meshpart
from build123d import Align, Box, Location, RigidJoint
from geom import P


def build():
    m = meshpart.Mesh(meshpart.solid(Box(10, 20, 30, align=(Align.CENTER, Align.CENTER, Align.MIN))))
    RigidJoint("bottom", m, Location((0, 0, 0)))
    return P("wedge", copy.deepcopy(m), "#dd8800", process="print", material="PETG", print_rot=["X", 90])
'''

TWIN = '''
import copy
import meshpart
from build123d import Location, RigidJoint
from geom import P

A = 12.0
V = [[x, y, z] for x in (-A / 2, A / 2) for y in (-A / 2, A / 2) for z in (0.0, A)]      # index = 4*ix + 2*iy + iz
F = [[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1], [2, 3, 7], [2, 7, 6],
     [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]]


def build():
    m = meshpart.Mesh(vertices=V, triangles=F)
    RigidJoint("bottom", m, Location((0, 0, 0)))
    return P("twin", copy.copy(m), "#22aa22", process="other")
'''

FRAME = '''
from build123d import Pos
from geom import place
from . import base, cap, shell, twin, wedge


def build():
    b = base.build()
    out = [place(b, Pos(0, 0, 0))]
    s1 = shell.build()
    b["shape"].joints["shell_seat_a"].connect_to(s1["shape"].joints["bottom"])
    out.append(place(s1, name="shell_a"))
    s2 = shell.build()
    b["shape"].joints["shell_seat_b"].connect_to(s2["shape"].joints["bottom"])
    out.append(place(s2, name="shell_b"))
    c = cap.build()
    s1["shape"].joints["top"].connect_to(c["shape"].joints["bottom"])
    out.append(place(c))
    w = wedge.build()
    b["shape"].joints["wedge_seat"].connect_to(w["shape"].joints["bottom"])
    out.append(place(w))
    t = twin.build()
    b["shape"].joints["twin_seat"].connect_to(t["shape"].joints["bottom"])
    out.append(place(t))
    return out
'''

MAIN_FILES = {"frame/__init__.py": FRAME, "frame/base.py": BASE, "frame/shell.py": SHELL, "frame/cap.py": CAP,
              "frame/wedge.py": WEDGE, "frame/twin.py": TWIN}
MESH_IDS = {"shell", "wedge", "twin"}
STEP_IDS = {"base", "cap"}

WORLD = {
    "shell_a": ((10, 15, 10), (50, 25, 40)),
    "shell_b": ((80, 15, 10), (120, 25, 40)),
    "cap": ((25, 15, 40), (35, 25, 44)),
    "wedge": ((150, 65, 10), (170, 75, 40)),
    "twin": ((34, 64, 10), (46, 76, 22)),
}


def write_project(root, files, params=PARAMS, screw=False, assemble="from parts import frame\nout = frame.build()\n"):
    """A folder-layout project under `root`: params.py, geom.py (framework template), parts/<rel> = files[rel], assemble.py.
    screw=True copies the framework's templates/screw.py in as parts/frame/screw.py (as a project gets it)."""
    root = Path(root)
    cad = H.write_base(root, params=params)
    for rel, src in files.items():
        p = cad / "parts" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(src).lstrip())
    if screw:
        tpl = H.SCRIPTS.parent / "templates" / "screw.py"
        (cad / "parts" / "frame" / "screw.py").write_text(tpl.read_text().replace("{machine}", "m"))
    (cad / "assemble.py").write_text(PRE + assemble)
    return root


def _build_main(shared):
    root = write_project(shared / "proj", MAIN_FILES)
    r = H.run_model(root)
    (shared / "cold.json").write_text(json.dumps({"rc": r.rc, "log": r.log}))


class _CP:
    def __init__(self, d):
        self.returncode, self.stdout, self.stderr = d["rc"], d["log"], ""


@pytest.fixture(scope="session")
def mesh_main(tmp_path_factory):
    """(root, Run): the main project after one cold --model build, built once per pytest run. Read only."""
    import conftest
    shared = conftest._shared(tmp_path_factory, "meshsolid_main", _build_main)
    return shared / "proj", H.Run(_CP(json.loads((shared / "cold.json").read_text())))


def main_ok(mesh_main):
    root, r = mesh_main
    assert r.rc == 0 and r.summary, f"the --model build of the mesh-part project failed (rc={r.rc}):\n{r.log[-2500:]}"
    return root


@pytest.fixture
def mesh_copy(mesh_main, tmp_path):
    """A private copy of the built main project (source mtimes kept, so the incremental build sees nothing changed)."""
    import conftest
    root = main_ok(mesh_main)
    return conftest._clean_copy(root, tmp_path / "proj")


# ---------------------------------------------------------------- reading (independent of the code under test)
def load_mesh(root, entry):
    """The stored mesh file of a parts.json entry as (vertices Nx3, faces Mx3): {"v": [flat floats], "f": [flat ints]}."""
    rel = entry.get("mesh")
    assert isinstance(rel, str) and rel.startswith("build/model/mesh/") and rel.endswith(".json"), \
        f"{entry.get('id')}: `mesh` must be build/model/mesh/<key>.json, got {rel!r}"
    f = Path(root) / rel
    assert f.is_file(), f"{entry.get('id')}: stored mesh file {rel} missing"
    d = json.loads(f.read_text())
    assert set(d) >= {"v", "f"}, f"{rel}: needs v and f, has {sorted(d)}"
    return np.asarray(d["v"], dtype=float).reshape(-1, 3), np.asarray(d["f"], dtype=int).reshape(-1, 3)


def trimesh_of(v, f):
    import trimesh
    return trimesh.Trimesh(vertices=v, faces=f, process=True)


def matrix(loc):
    flat = [float(x) for row in loc for x in row] if loc and isinstance(loc[0], list) else [float(x) for x in loc]
    assert len(flat) == 16, loc
    return np.array(flat).reshape(4, 4)


def world_bbox(v, loc):
    M = matrix(loc)
    w = v @ M[:3, :3].T + M[:3, 3]
    return np.array([w.min(0), w.max(0)])


def placement_map(root):
    return {pl["name"]: (p, pl) for p in H.read_parts(root) for pl in (p.get("placements") or [])}
