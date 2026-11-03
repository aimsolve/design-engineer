"""Shared fixtures-as-functions for the issue #2 model-build tests (cad_incremental --model, derive.py), ported to the folder
layout by issue #30 (an old parts/<node>.py is refused now).

Everything runs against a tiny synthetic code-model project written into a temp dir with the real layout:
    <root>/mechanics/cad/params.py, geom.py (the framework template: P(), place()), assemble.py
    parts/<node>/__init__.py   build() places the node's part files
    parts/<node>/<part>.py     build() -> ONE part dict in its own coordinates (file stem = part id)
    parts/_helper.py           a private helper module (not a node)
Build units are the part files and the node __init__s (UNITS); a changed part file also rebuilds the node that places it.
Part files tick their part id, node __init__s tick node_<node> (one empty file per call in $TEST_COUNTER_DIR), so a test can
prove which code did, or did not, run. The marker is written with os.open, not open(), so it is not a "file the code read".
"""
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import procgroup
import sys
import textwrap
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
CAD_INC = SCRIPTS / "cad_incremental.py"
DERIVE = SCRIPTS / "derive.py"
SHOP_VENV = Path(os.environ.get("SHOP_DRAW_VENV", "/tmp/shopdrawvenv")) / "bin" / "python"

TICK = '''
import os, uuid
def _tick(n):
    d = os.environ["TEST_COUNTER_DIR"]
    os.makedirs(d, exist_ok=True)
    os.close(os.open(os.path.join(d, n + "." + uuid.uuid4().hex), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
'''

PARAMS = """A_W = 10.0
UNUSED = 99.0
C_D = 3.0
PLATE_T = 2.0
LASER_L = 30.0
BENT_L = 40.0
PRINT_R = 4.0
"""

HELPER = """def scaled(x):
    return x * 1
"""

GEOM = (Path(__file__).resolve().parent.parent.parent / "templates" / "geom.py").read_text().replace("{machine}", "m")

# node -> {part id: part file source with {TICK}}; every part file ticks its part id
PART_FILES = {
    "frame": {"frame": """
import params
from build123d import Box
from parts import _helper
{TICK}
def build():
    _tick("frame")
    return {"name": "frame", "shape": Box(_helper.scaled(params.A_W), 8, 2), "color": "#888888", "process": "mill"}
"""},
    "sheet_laser": {"laser_plate": """
import params
from build123d import Box
{TICK}
def build():
    _tick("laser_plate")
    return {"name": "laser_plate", "shape": Box(params.LASER_L, 20, params.PLATE_T), "color": "#aaaaaa",
            "process": "laser", "material": "S235JR", "thickness": params.PLATE_T}
"""},
    "sheet_bent": {"bent_angle": """
import params
from build123d import Box
{TICK}
def build():
    _tick("bent_angle")
    return {"name": "bent_angle", "shape": Box(params.BENT_L, 20, params.PLATE_T), "color": "#aaaaaa",
            "process": "bent", "material": "S235JR", "thickness": params.PLATE_T}
"""},
    "printed": {"knob": """
import params
from build123d import Sphere
{TICK}
def build():
    _tick("knob")
    return {"name": "knob", "shape": Sphere(params.PRINT_R), "color": "#ff0000", "process": "print", "material": "PLA"}
"""},
    "misc": {"misc_block": """
from build123d import Box
from parts import _helper
{TICK}
def build():
    _tick("misc_block")
    return {"name": "misc_block", "shape": Box(_helper.scaled(6), 6, 2), "color": "#00ff00", "process": "other"}
"""},
    "hardware": {
        "screw_a": """
from build123d import Cylinder
{TICK}
def build():
    _tick("screw_a")
    return {"name": "screw_a", "shape": Cylinder(1, 6), "color": "#cccccc", "process": "bought", "code": "M5x10"}
""",
        "screw_b": """
from build123d import Cylinder
{TICK}
def build():
    _tick("screw_b")
    return {"name": "screw_b", "shape": Cylinder(1, 6), "color": "#cccccc", "process": "bought", "code": "M5x10"}
""",
        "nut_a": """
from build123d import Cylinder
{TICK}
def build():
    _tick("nut_a")
    return {"name": "nut_a", "shape": Cylinder(2, 2), "color": "#cccccc", "process": "bought", "code": "NUT-M5"}
"""},
    "datanode": {"data_plate": """
import os
from build123d import Box
{TICK}
def build():
    _tick("data_plate")
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "dims.txt")) as f:
        L = float(f.read().strip())
    return {"name": "data_plate", "shape": Box(L, 5, 2), "color": "#0000ff", "process": "mill"}
"""},
    "callee": {"callee_part": """
import params
from build123d import Box
{TICK}
def build():
    _tick("callee_part")
    return {"name": "callee_part", "shape": Box(4, 4, params.C_D), "color": "#123456", "process": "mill"}
"""},
    # caller_part reads the size of what ANOTHER NODE (callee) builds: its record must cover what the callee read
    "caller": {"caller_part": """
from build123d import Box
from parts import callee
{TICK}
def build():
    _tick("caller_part")
    h = callee.build()[0]["shape"].bounding_box().size.Z
    return {"name": "caller_part", "shape": Box(4, 4, h + 1), "color": "#654321", "process": "mill"}
"""},
}
# node -> [(part id, (x, y, z)), ...]: where the node places its part files (the old layout's world positions)
PLACEMENTS = {
    "frame": [("frame", (0, 0, 0))],
    "sheet_laser": [("laser_plate", (20, 0, 0)), ("laser_plate", (20, 30, 0))],
    "sheet_bent": [("bent_angle", (40, 0, 0))],
    "printed": [("knob", (60, 0, 0))],
    "misc": [("misc_block", (70, 0, 0))],
    "hardware": [("screw_a", (80, 0, 0)), ("screw_a", (80, 10, 0)), ("screw_b", (82, 0, 0))]
                + [("nut_a", (84, 10 * i, 0)) for i in range(4)],
    "datanode": [("data_plate", (90, 0, 0))],
    "callee": [("callee_part", (100, 0, 0))],
    "caller": [("caller_part", (110, 0, 0))],
}
# node -> name of its build function (a folder node's is build(): today's --model does not take a build_*() in a node __init__)
FUNCS = {"frame": "build", "sheet_laser": "build", "sheet_bent": "build", "printed": "build", "misc": "build",
         "hardware": "build", "datanode": "build", "callee": "build", "caller": "build"}
# part id -> node that places it
OWNER = {pid: node for node, files in PART_FILES.items() for pid in files}
PART_IDS = set(OWNER)
# qty = number of placements
QTY = {pid: sum(1 for node in PLACEMENTS for p, _ in PLACEMENTS[node] if p == pid) for pid in PART_IDS}


def part_unit(pid, node=None):
    """The `[cad_incremental] rebuilt:` token of a part file (`parts.` prefix stripped): <node>.<part>.build"""
    return f"{node or OWNER[pid]}.{pid}.build"


def node_unit(node):
    return f"{node}.{FUNCS.get(node, 'build')}"


def units_for(*pids):
    """Every unit an edit of these parts' inputs rebuilds: their part files and the nodes that place them."""
    return {part_unit(p) for p in pids} | {node_unit(OWNER[p]) for p in pids}


UNITS = {part_unit(p) for p in PART_IDS} | {node_unit(n) for n in FUNCS}
N_UNITS = len(UNITS)          # the summary line "nodes rebuilt N, reused M" counts build units: part files + node __init__s


def write_node(root, node, part_files, placements=None, func="build"):
    """A folder node parts/<node>/: `part_files` {part id: source, {TICK} allowed} and an __init__.py whose `func`() places
    them at `placements` [(part id, (x, y, z))] (default: each once at the origin). The __init__ ticks node_<node>."""
    d = Path(root) / "mechanics" / "cad" / "parts" / node
    d.mkdir(parents=True, exist_ok=True)
    for pid, src in part_files.items():
        (d / f"{pid}.py").write_text(textwrap.dedent(src).replace("{TICK}", TICK).lstrip())
    pl = placements or [(pid, (0, 0, 0)) for pid in part_files]
    mods = sorted({pid for pid, _ in pl})
    lines = ["from build123d import Pos", "from geom import place", f"from . import {', '.join(mods)}", TICK,
             f"def {func}():", f"    _tick('node_{node}')", "    return ["]
    lines += [f"        place({pid}.build(), Pos{tuple(xyz)})," for pid, xyz in pl]
    lines += ["    ]"]
    (d / "__init__.py").write_text("\n".join(lines) + "\n")


def assemble_src(extra=()):
    mods = list(FUNCS) + list(extra)
    funcs = dict(FUNCS, **{e: "build" for e in extra})
    lines = ["import json, os, sys", "from pathlib import Path",
             "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))",
             "from parts import " + ", ".join(mods), "out = []"]
    lines += [f"out += {m}.{funcs[m]}()" for m in mods]
    lines += ["root = Path(os.path.abspath(__file__)).parent.parent.parent",
              "Path(root, 'assembled.json').write_text(json.dumps({p['name']: p['shape'].volume for p in out}))"]
    return "\n".join(lines) + "\n"


def write_base(root, params=PARAMS):
    """mechanics/cad/ with params.py, geom.py and an empty parts/ package: what every folder-layout test project starts from."""
    cad = Path(root) / "mechanics" / "cad"
    (cad / "parts").mkdir(parents=True)
    (cad / "params.py").write_text(params)
    (cad / "geom.py").write_text(GEOM)
    (cad / "parts" / "__init__.py").write_text("")
    return cad


def make_project(root, extra=()):
    root = Path(root)
    cad = write_base(root)
    (cad / "data").mkdir()
    (cad / "data" / "dims.txt").write_text("12\n")
    (cad / "parts" / "_helper.py").write_text(HELPER)
    for node in FUNCS:
        write_node(root, node, PART_FILES[node], PLACEMENTS[node], FUNCS[node])
    (cad / "assemble.py").write_text(assemble_src(extra))
    return root


def env_for(root):
    e = dict(os.environ)
    e["TEST_COUNTER_DIR"] = str(Path(root) / "counter")
    e["PYTHONDONTWRITEBYTECODE"] = "1"
    return e


class Run:
    def __init__(self, cp):
        self.rc = cp.returncode
        self.log = (cp.stdout or "") + (cp.stderr or "")
        ms = re.findall(r"\[cad_incremental\] [\d.]+ s \| nodes rebuilt (\d+), reused (\d+) \| parts (\d+)", self.log)
        self.summary = tuple(int(x) for x in ms[-1]) if ms else None
        rb = [l for l in self.log.splitlines() if l.startswith("[cad_incremental] rebuilt:")]
        toks = rb[-1].split("rebuilt:", 1)[1].split(",") if rb else []
        # build units as the log names them, the package prefix stripped: "frame.frame.build", "frame.build"
        self.rebuilt = {t.strip()[len("parts."):] if t.strip().startswith("parts.") else t.strip() for t in toks if t.strip()}
        self.has_rebuilt_line = bool(rb)

    def ok(self):
        assert self.rc == 0 and self.summary, f"--model build did not succeed (rc={self.rc}):\n{self.log[-1500:]}"
        return self


def run_model(root, extra_args=(), script="assemble.py"):
    cad = Path(root) / "mechanics" / "cad"
    cp = procgroup.run([sys.executable, str(CAD_INC), "--model", *extra_args, script], cwd=cad, env=env_for(root),
                        capture_output=True, text=True, timeout=300)
    return Run(cp)


def ticks(root):
    d = Path(root) / "counter"
    return sorted(p.name.split(".")[0] for p in d.iterdir()) if d.is_dir() else []


def clear_ticks(root):
    shutil.rmtree(Path(root) / "counter", ignore_errors=True)


def read_parts(root):
    f = Path(root) / "build/model/parts.json"
    assert f.is_file(), "build/model/parts.json missing: the first --model build did not produce a model"
    return json.loads((Path(root) / "build/model/parts.json").read_text())["parts"]


def by_id(root):
    return {p["id"]: p for p in read_parts(root)}


def part_keys(root):
    return {p["id"]: p["key"] for p in read_parts(root)}


def derived_dirs(root):
    d = Path(root) / "build/derived"
    return {p.name for p in d.iterdir()} if d.is_dir() else set()


def meta_part_keys(meta_file):
    """The part keys a derived dir's meta.json lists (entries are plain keys or {"key": ...})."""
    parts = json.loads(Path(meta_file).read_text())["parts"]
    return {x if isinstance(x, str) else x["key"] for x in parts}


def foreign_machine(root):
    """Simulate STEPs written by another machine: same parts.json keys, but every STEP differs in the last bits of a
    coordinate (still a valid STEP). Returns {part id: content sha after}; asserts each file really changed and still loads."""
    out = {}
    for p in read_parts(root):
        f = Path(root) / p["step"]
        before = sha_file(f)
        text = f.read_text()
        data = text.index("DATA;")
        m = re.compile(r"(CARTESIAN_POINT\('[^']*',\()(-?\d+\.\d*)").search(text, data)
        assert m, f"{p['id']}: no CARTESIAN_POINT to alter in {f.name}"
        digits = m.group(2) if m.group(2).endswith(".") is False else m.group(2) + "0"
        f.write_text(text[:m.start(2)] + digits + "0000001" + text[m.end(2):])
        out[p["id"]] = sha_file(f)
        assert out[p["id"]] != before, p["id"]
        step_bbox(f)                                   # still loads
    return out


def step_files(root):
    d = Path(root) / "build/model/step"
    return {p.name: (p.stat().st_mtime_ns, p.stat().st_size) for p in d.iterdir()} if d.is_dir() else {}


def dir_size(path):
    return sum(p.stat().st_size for p in Path(path).rglob("*") if p.is_file())


def edit(path, old, new):
    t = Path(path).read_text()
    assert old in t, f"{old!r} not in {path}"
    Path(path).write_text(t.replace(old, new, 1))


def cad_dir(root):
    return Path(root) / "mechanics" / "cad"


def step_bbox(path):
    from build123d import import_step
    return import_step(str(path)).bounding_box()


def sha_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ---------------------------------------------------------------- derive.py side
def make_derived_model(root, overrides=None):
    """A hand-written build/model/ (parts.json + STEPs named by part key) plus node files that must never be imported.
    Independent of cad_incremental --model, so derive tests fail only for derive reasons."""
    from build123d import Box, Cylinder, Sphere, Pos, export_step
    root = Path(root)
    cad = root / "mechanics" / "cad" / "parts"
    cad.mkdir(parents=True)
    (cad / "__init__.py").write_text("")
    (root / "mechanics" / "cad" / "params.py").write_text("X = 1\n")
    (cad / "boom.py").write_text(
        "import os, uuid\n_d = os.environ['TEST_COUNTER_DIR']\nos.makedirs(_d, exist_ok=True)\n"
        "os.close(os.open(os.path.join(_d, 'imported.' + uuid.uuid4().hex), os.O_CREAT | os.O_EXCL | os.O_WRONLY))\n"
        "def build():\n    raise RuntimeError('derive must not run node code')\n")
    specs = [
        ("frame", "mill", Pos(0, 0, 0) * Box(30, 10, 5), {}),
        ("shaft", "turn", Pos(0, 20, 0) * Cylinder(3, 40), {}),
        ("misc_block", "other", Pos(0, 40, 0) * Box(6, 6, 2), {}),
        ("laser_plate", "laser", Pos(50, 0, 0) * Box(30, 20, 2), {"material": "S235JR", "thickness": 2.0, "qty": 2}),
        ("bent_angle", "bent", Pos(50, 30, 0) * Box(40, 20, 2), {"material": "S235JR", "thickness": 2.0, "qty": 1}),
        ("knob", "print", Pos(100, 0, 0) * Sphere(4), {"material": "PLA"}),
        ("cap", "print", Pos(100, 20, 0) * Sphere(3), {"material": "PLA"}),
        ("screw_a", "bought", Pos(120, 0, 0) * Cylinder(1, 6), {"code": "M5x10", "desc": "Screw M5x10", "qty": 2}),
        ("screw_b", "bought", Pos(122, 0, 0) * Cylinder(1, 6), {"code": "M5x10", "desc": "Screw M5x10", "qty": 1}),
        ("nut_a", "bought", Pos(124, 0, 0) * Cylinder(2, 2), {"code": "NUT-M5", "desc": "Nut M5", "qty": 4}),
    ]
    parts = []
    (root / "build/model/step").mkdir(parents=True)
    for pid, proc, shape, extra in specs:
        parts.append(_write_part(root, pid, proc, shape, extra))
    _write_parts_json(root, parts)
    return root


def _write_part(root, pid, proc, shape, extra):
    from build123d import export_step
    tmp = Path(root) / "build/model/step/_tmp.step"
    export_step(shape, str(tmp))
    sha = sha_file(tmp)
    # a stand-in for the producing node's input fingerprint + part id: a hash that is NOT the content sha
    key = hashlib.sha256(f"fixture-inputs|{pid}|{sha}".encode()).hexdigest()
    dst = Path(root) / "build/model/step" / f"{key}.step"
    tmp.replace(dst)
    p = {"id": pid, "name": pid, "node": "boom", "func": "boom.build", "process": proc, "color": "#999999",
         "key": key, "sha256": sha, "step": f"build/model/step/{key}.step"}
    p.update(extra)
    return p


def _write_parts_json(root, parts):
    (Path(root) / "build/model/parts.json").write_text(json.dumps({"parts": parts}))
    (Path(root) / "build/model/nodes.json").write_text(json.dumps({}))


def replace_part(root, pid, shape=None, **fields):
    """Simulate a one-part model change: new STEP for `pid` (and/or changed fields), old STEP removed."""
    root = Path(root)
    parts = read_parts(root)
    for i, p in enumerate(parts):
        if p["id"] == pid:
            if shape is not None:
                extra = {k: v for k, v in p.items() if k not in ("id", "name", "node", "func", "process", "color", "key", "sha256", "step")}
                extra.update(fields)
                (root / p["step"]).unlink()
                parts[i] = _write_part(root, pid, p["process"], shape, extra)
            else:
                p.update(fields)
    _write_parts_json(root, parts)


def run_derive(root, scope, manifest=None, part_id=None, where=()):
    root = Path(root)
    manifest = Path(manifest) if manifest else root / "build" / f"manifest_{scope}.json"
    args = [sys.executable, str(DERIVE), scope, str(manifest)] + ([part_id] if part_id else [])
    for w in where:
        args += ["--where", w]
    cp = procgroup.run(args, cwd=root, env=env_for(root), capture_output=True, text=True, timeout=300)
    return Derived(cp, scope, manifest)


class Derived:
    def __init__(self, cp, scope, manifest):
        self.rc = cp.returncode
        self.log = (cp.stdout or "") + (cp.stderr or "")
        self.manifest_path = manifest
        m = re.findall(rf"\[derive\] {scope}: parts (\d+), reused (\d+), computed (\d+)", self.log)
        self.counts = tuple(int(x) for x in m[-1]) if m else None
        self.manifest = json.loads(manifest.read_text()) if manifest.exists() else None

    def ok(self):
        assert self.rc == 0 and self.counts and self.manifest is not None, f"derive failed (rc={self.rc}):\n{self.log[-1500:]}"
        return self

    @property
    def parts_k(self): return self.counts[0]
    @property
    def reused(self): return self.counts[1]
    @property
    def computed(self): return self.counts[2]


def file_list(entry, root):
    """Resolve entry['files'] (strings, or dicts with a path/file key) to absolute Paths."""
    out = []
    for f in entry.get("files", []):
        s = f if isinstance(f, str) else (f.get("path") or f.get("file"))
        p = Path(s)
        out.append(p if p.is_absolute() else Path(root) / p)
    return out


def entry(d, pid):
    got = [e for e in d.manifest["parts"] if e["id"] == pid]
    assert got, f"part {pid!r} not in manifest parts: {[e['id'] for e in d.manifest['parts']]}"
    return got[0]


def stl_triangles(path):
    b = Path(path).read_bytes()
    if b[:5] == b"solid" and b"facet" in b[:400]:
        return b.count(b"endfacet")
    n = struct.unpack("<I", b[80:84])[0]
    assert len(b) == 84 + 50 * n, "not a valid binary STL"
    return n


def glb_names(path):
    b = Path(path).read_bytes()
    magic, version, length = struct.unpack("<4sII", b[:12])
    assert magic == b"glTF" and length == len(b)
    clen, ctype = struct.unpack("<I4s", b[12:20])
    assert ctype == b"JSON"
    j = json.loads(b[20:20 + clen])
    names = {n.get("name") for n in j.get("nodes", [])} | {m.get("name") for m in j.get("meshes", [])}
    return j, {n for n in names if n}


def find_glb(d, root):
    strings = []
    def walk(x):
        if isinstance(x, str): strings.append(x)
        elif isinstance(x, dict): [walk(v) for v in x.values()]
        elif isinstance(x, list): [walk(v) for v in x]
    walk(d.manifest)
    for s in strings:
        if s.endswith(".glb"):
            p = Path(s)
            p = p if p.is_absolute() else Path(root) / p
            if p.exists():
                return p
    found = sorted((Path(root) / "build/derived").rglob("*.glb"))
    assert found, f"no .glb in manifest or build/derived; manifest={d.manifest}"
    return found[0]
