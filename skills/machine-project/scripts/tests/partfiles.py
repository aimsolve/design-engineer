"""Shared synthetic project + helpers for the issue #15 tests (one part = one file, node = folder).

The project is written into a temp dir in the NEW layout:
    mechanics/cad/params.py, geom.py (the framework template: P() and place()), assemble.py
    mechanics/cad/parts/<node>/__init__.py   build() places the node's parts (place(part, loc, name=None))
    mechanics/cad/parts/<node>/<part>.py     build(**params) -> ONE part dict in its OWN coordinates
    mechanics/cad/parts/<node>/_<x>.py       shared node code (not a part)
(no old-layout node any more; an old parts/<node>.py fails the build, see test_legacy_removed.py.)

Every part file and node __init__ writes a tick marker (helpers.TICK, os.open: not a file "the part read") so a test can prove
which code ran. Ticks: part files tick their file stem (rail, foot, plate, screw, knob, slider, gauge, bracket), node
__init__s tick node_<name>.

What the model must contain (one parts.json entry per VARIANT, qty = number of placements):
    rail     frame/rail.py   build(), build(length=600), build(length=600.0) -> ONE variant, 3 placements at x=100, y=0/100/200
    foot     frame/foot.py   4 placements (FEET), height = params.FOOT_H
    plate    frame/plate.py  laser, 2 placements, width read from data/plate_w.txt
    screw    frame/screw.py  bought with a shape, 4 placements
    pipe     row only (shape None, process bought, qty=3) returned by frame/__init__ without place
    knob     tower/knob.py   print, placed once by tower (at 500,0,0) and once by tower/carriage (sub-assembly)
    slider   tower/carriage/slider.py  print, placed by tower/carriage at (5,0,0); carriage placed by tower at (0,0,50)
    gauge    tower/carriage/gauge.py   only read by slider for a dimension: NOT in the model
Helpers used: frame/_common.py by rail, plate and (cross-node, absolute import) slider; foot, screw, knob, gauge do not use it.
"""
import json
import os
import re
import shutil
import sys
import textwrap
from pathlib import Path

import pytest

import helpers as H
import procgroup

TEMPLATES = H.SCRIPTS.parent / "templates"

PARAMS = """FOOT_H = 10.0
KNOB_R = 4.0
PLATE_T = 2.0
UNUSED = 1.0
"""

FEET = [(0, 0), (300, 0), (0, 300), (300, 300)]
RAIL_AT = [(100, 0, 0), (100, 100, 0), (100, 200, 0)]

FILES = {
    "frame/__init__.py": '''
        from build123d import Pos
        from geom import P, place
        from . import rail, foot, plate, screw
        {TICK}
        FEET = {FEET!r}

        def build():
            _tick("node_frame")
            out = [place(rail.build(), Pos(100, 0, 0)),
                   place(rail.build(length=600), Pos(100, 100, 0)),
                   place(rail.build(length=600.0), Pos(100, 200, 0))]
            out += [place(foot.build(), Pos(x, y, -20)) for x, y in FEET]
            out += [place(plate.build(), Pos(0, -100, 0)), place(plate.build(), Pos(0, -200, 0))]
            out += [place(screw.build(), Pos(x, y, 5)) for x, y in FEET]
            out.append(P("pipe", None, "#999999", process="bought", code="TR50", desc="Truba 50", qty=3, unit="m", kind="profile"))
            return out
        ''',
    "frame/_common.py": '''
        SECTION = 20.0

        def section():
            return SECTION
        ''',
    "frame/rail.py": '''
        from build123d import Box
        from geom import P
        from . import _common
        {TICK}

        def build(length=600):
            _tick("rail")
            s = _common.section()
            name = "rail" if length == 600 else "rail_%d" % int(length)
            return P(name, Box(length, s, s), "#888888", process="mill", material="AL6060")
        ''',
    "frame/foot.py": '''
        import params
        from build123d import Box
        from geom import P
        {TICK}

        def build():
            _tick("foot")
            return P("foot", Box(30, 30, params.FOOT_H), "#333333", process="mill", material="S235JR")
        ''',
    "frame/plate.py": '''
        import os
        import params
        from build123d import Box
        from geom import P
        from . import _common
        {TICK}

        def build():
            _tick("plate")
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "plate_w.txt")) as f:
                w = float(f.read().strip())
            return P("plate", Box(_common.section() * 2, w, params.PLATE_T), "#aaaaaa", process="laser",
                     material="S235JR", thickness=params.PLATE_T, code="PL-1")
        ''',
    "frame/screw.py": '''
        from build123d import Cylinder
        from geom import P
        {TICK}

        def build():
            _tick("screw")
            return P("screw", Cylinder(2.5, 10), "#cccccc", process="bought", code="M5x10", desc="Screw M5x10", kind="fasteners")
        ''',
    "tower/__init__.py": '''
        from build123d import Pos
        from geom import place
        from . import carriage, knob
        {TICK}

        def build():
            _tick("node_tower")
            return [place(knob.build(), Pos(500, 0, 0))] + place(carriage.build(), Pos(0, 0, 50))
        ''',
    "tower/knob.py": '''
        import params
        from build123d import Sphere
        from geom import P
        {TICK}

        def build():
            _tick("knob")
            return P("knob", Sphere(params.KNOB_R), "#ff0000", process="print", material="PLA")
        ''',
    "tower/carriage/__init__.py": '''
        from build123d import Pos
        from geom import place
        from parts.tower import knob
        from . import slider
        {TICK}

        def build():
            _tick("node_carriage")
            return [place(slider.build(), Pos(5, 0, 0)), place(knob.build(), Pos(5, 0, 30))]
        ''',
    "tower/carriage/slider.py": '''
        from build123d import Box
        from geom import P
        from parts.frame import _common
        from . import gauge
        {TICK}

        def build():
            _tick("slider")
            w = gauge.build()["shape"].bounding_box().size.X
            return P("slider", Box(w, _common.section(), 10), "#4444aa", process="print", material="PETG")
        ''',
    "tower/carriage/gauge.py": '''
        from build123d import Box
        from geom import P
        {TICK}

        def build():
            _tick("gauge")
            return P("gauge", Box(7, 7, 7), "#000000", process="other")
        ''',
}

LEGACY = '''
from build123d import Box, Pos
{TICK}

def build():
    _tick("legacy")
    return [{{"name": "legacy_block", "shape": Pos(-100, 0, 0) * Box(5, 5, 5), "color": "#00ff00", "node": "legacy",
             "process": "other", "qty": 2}}]
'''

ASSEMBLE = """import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from parts import frame, tower

items = frame.build() + tower.build()
print(len(items), "items")
"""

# variant id -> qty expected from placements (or the row's own qty field)
QTY = {"rail": 3, "foot": 4, "plate": 2, "screw": 4, "pipe": 3, "knob": 2, "slider": 1}
PART_FILES = ("rail", "foot", "plate", "screw", "knob", "slider", "gauge")
COMMON_USERS = {"rail", "plate", "slider"}           # part files that import frame/_common.py (directly or cross-node)


def geom_src():
    return (TEMPLATES / "geom.py").read_text().replace("{machine}", "m")


def _src(s):
    return textwrap.dedent(s).replace("{TICK}", H.TICK).replace("{FEET!r}", repr(FEET)).lstrip()


def write_project(root, files=None, assemble=ASSEMBLE, params=PARAMS, legacy=False):
    """The synthetic new-layout project. `files` overrides / extends FILES (rel path under parts/ -> source with {TICK}).
    legacy=True adds the old-layout node parts/legacy.py (only to prove it is refused)."""
    root = Path(root)
    cad = root / "mechanics" / "cad"
    (cad / "parts").mkdir(parents=True)
    (cad / "data").mkdir()
    (cad / "params.py").write_text(params)
    (cad / "geom.py").write_text(geom_src())
    (cad / "data" / "plate_w.txt").write_text("30\n")
    (cad / "parts" / "__init__.py").write_text("")
    for rel, src in dict(FILES, **(files or {})).items():
        if src is None:
            continue
        p = cad / "parts" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_src(src))
    if legacy:
        (cad / "parts" / "legacy.py").write_text(LEGACY.replace("{TICK}", H.TICK).replace("{{", "{").replace("}}", "}"))
    (cad / "assemble.py").write_text(assemble)
    return root


def part_file(root, rel):
    return H.cad_dir(root) / "parts" / rel


# ---------------------------------------------------------------- running the model build
class Model:
    """One `cad_incremental.py --model assemble.py` run; stderr kept apart (warnings must go there)."""
    def __init__(self, cp, root):
        self.rc = cp.returncode
        self.out = cp.stdout or ""
        self.err = cp.stderr or ""
        self.log = self.out + self.err
        self.root = Path(root)
        rb = [l for l in self.log.splitlines() if l.startswith("[cad_incremental] rebuilt:")]
        self.rebuilt_tokens = [t.strip() for t in rb[-1].split("rebuilt:", 1)[1].split(",") if t.strip()] if rb else []

    def ok(self):
        assert self.rc == 0 and (self.root / "build/model/parts.json").is_file(), \
            f"--model build of the new-layout project failed (rc={self.rc}):\n{self.log[-2500:]}"
        return self

    def failed(self):
        assert self.rc != 0, f"the build must fail loudly (non-zero exit), it exited 0:\n{self.log[-1500:]}"
        return self

    def rebuilt(self, part_path):
        """True when the `[cad_incremental] rebuilt:` line names the part at `part_path` ("frame/foot")."""
        segs = part_path.split("/")
        pat = re.compile(r"(^|[^\w])" + r"[./]".join(map(re.escape, segs)) + r"(?![\w])")
        return any(pat.search(t) for t in self.rebuilt_tokens)


def run_model(root):
    cad = H.cad_dir(root)
    cp = procgroup.run([sys.executable, str(H.CAD_INC), "--model", "assemble.py"], cwd=cad, env=H.env_for(root),
                       capture_output=True, text=True, timeout=300)
    return Model(cp, root)


def run_check(root):
    """check.py as the router runs it (no AAS schema validation)."""
    cp = procgroup.run([sys.executable, str(H.SCRIPTS / "check.py"), str(root), "--no-validate"], capture_output=True, text=True,
                       timeout=300)
    return cp


def fail_lines(cp):
    text = (cp.stdout or "") + "\n" + (cp.stderr or "")
    return [ln.strip() for ln in text.splitlines() if re.match(r"^FAIL\s+(?!:)\S", ln) and not ln.startswith("FAIL:")]


def scaffold(root):
    cp = procgroup.run([sys.executable, str(H.SCRIPTS / "scaffold.py"), str(root), "--name", "m", "--cad", "build123d"],
                       capture_output=True, text=True, timeout=120)
    assert cp.returncode == 0, cp.stderr
    return Path(root)


# ---------------------------------------------------------------- reading the model
def parts(root):
    return H.by_id(root)


def placements(entry):
    pl = entry.get("placements")
    assert isinstance(pl, list), f"{entry.get('id')}: parts.json entry has no placements list: {entry}"
    return pl


def all_placements(root):
    return [(p["id"], pl) for p in H.read_parts(root) for pl in (p.get("placements") or [])]


def matrix(loc):
    """A placement's loc as 4 rows of 4 floats (accepts row-major 16 floats or 4 lists of 4)."""
    assert isinstance(loc, list), f"loc must be a 4x4 matrix as a JSON list, got {loc!r}"
    flat = [float(x) for row in loc for x in row] if loc and isinstance(loc[0], list) else [float(x) for x in loc]
    assert len(flat) == 16, f"loc must have 16 numbers, got {len(flat)}"
    return [flat[i * 4:(i + 1) * 4] for i in range(4)]


def apply(loc, pt=(0.0, 0.0, 0.0)):
    m = matrix(loc)
    v = (*pt, 1.0)
    return tuple(sum(m[r][c] * v[c] for c in range(4)) for r in range(3))


def origin(pl):
    return tuple(round(x, 6) for x in apply(pl["loc"]))


def step_center(root, entry):
    bb = H.step_bbox(Path(root) / entry["step"])
    return ((bb.min.X + bb.max.X) / 2, (bb.min.Y + bb.max.Y) / 2, (bb.min.Z + bb.max.Z) / 2)


def snapshot_steps(root):
    return H.step_files(root)


# ---------------------------------------------------------------- STEP tree / STL
def step_tree(path):
    from build123d import import_step
    return import_step(str(path))


_GENERIC = {"", "COMPOUND", "SOLID", "SHELL", None}


def leaves(shape, path=()):
    """[(group label path, part label, part shape)] of an imported assembly. A part is a labelled leaf, or a labelled compound
    whose children are only unlabelled solids (export_step writes a part as `label` > 'COMPOUND' solid)."""
    out = []
    kids = list(shape.children)
    if not kids:
        return [(path, shape.label, shape)]
    if all(not list(k.children) and (k.label in _GENERIC or k.label == shape.label) for k in kids):
        return [(path, shape.label, shape)]
    for k in kids:
        out += leaves(k, path + (shape.label,))
    return out


def find_group(shape, pred):
    if pred(shape.label or ""):
        return shape
    for k in shape.children:
        g = find_group(k, pred)
        if g is not None:
            return g
    return None


def stl_bbox_size(path):
    import struct
    b = Path(path).read_bytes()
    if b[:5] == b"solid" and b"facet" in b[:400]:
        pts = [tuple(map(float, l.split()[1:4])) for l in b.decode().splitlines() if l.strip().startswith("vertex")]
    else:
        n = struct.unpack("<I", b[80:84])[0]
        pts = []
        for i in range(n):
            off = 84 + 50 * i + 12
            vals = struct.unpack("<9f", b[off:off + 36])
            pts += [vals[0:3], vals[3:6], vals[6:9]]
    return tuple(max(p[k] for p in pts) - min(p[k] for p in pts) for k in range(3))


def abs_path(root, s):
    p = Path(s)
    return p if p.is_absolute() else Path(root) / p


# ---------------------------------------------------------------- shared cold build (once per pytest run)
def _clean_copy(src, dst):
    shutil.copytree(src, dst)
    for p in Path(dst).rglob("__pycache__"):
        shutil.rmtree(p, ignore_errors=True)
    shutil.rmtree(Path(dst) / "counter", ignore_errors=True)
    return dst


def _build_new_baseline(shared):
    root = write_project(shared / "proj")
    m = run_model(root)
    (shared / "cold.json").write_text(json.dumps({"rc": m.rc, "out": m.out, "err": m.err, "ticks": H.ticks(root)}))


@pytest.fixture(scope="session")
def new_baseline(tmp_path_factory):
    """(root, Model, cold ticks): the new-layout project after one clean --model build, built once per run. Read only."""
    import conftest
    shared = conftest._shared(tmp_path_factory, "partfiles", _build_new_baseline)
    d = json.loads((shared / "cold.json").read_text())

    class _CP:
        returncode, stdout, stderr = d["rc"], d["out"], d["err"]
    return shared / "proj", Model(_CP, shared / "proj"), d["ticks"]


@pytest.fixture
def nproj(new_baseline, tmp_path):
    """A private copy of the built new-layout project. A failed cold build is not raised here (that would be a fixture ERROR):
    the copy then has no build/model and every test fails on its own first assertion, with the build log."""
    root, m, _ = new_baseline
    return Path(_clean_copy(root, tmp_path / "proj"))
