"""Issues #18 / #48: what a node re-run costs when ONE of its part files changed, and which part files a param read by a
node's shared module belongs to.

Done-when map:
  DW1   a reused part's geometry is never read back unless the node code asks for its shape: a run that rebuilds ONE part file
        of a node must read a small constant number of part geometries, not one per reused placement, and that cost must not
        grow with the number of parts the node places
  DW1s  soundness of DW1: a param that reaches only a NON-shape field of a part (also one nested in a container) is still
        recorded as a read, so changing it rebuilds the part file and whoever reads that field
  DW3   a param a node's shared module reads at import time rebuilds only the part files that use the derived name; a part that
        calls a function of that module keeps the param behind it (soundness)

The project is written with helpers.py's harness (write_base / write_node / run_model / ticks / the `[cad_incremental] rebuilt:`
line) into a project of its own: nodes `big` (8 part files) and `small` (2) for the cost of a re-run, `src` + `rdr` for the taint
on a non-shape field, `shared` (a node with its own _layout.py) for the per-name attribution. Geometry reads are counted the way
test_lazy_reuse.py counts them: a sitecustomize wraps build123d's importers in the build process.
"""
import itertools
import json
import shutil
import sys
from pathlib import Path

import procgroup
import pytest

import helpers as H

PARAMS = """B1 = 10.0
B2 = 11.0
B3 = 12.0
B4 = 13.0
B5 = 14.0
B6 = 15.0
B7 = 16.0
B8 = 17.0
S1 = 20.0
S2 = 21.0
PLATE_T = 2.0
BANK_GRADE = "S235"
SRC_GRADE = "S235"
SPAN_A_MM = 30.0
SPAN_B_MM = 31.0
SPAN_C_MM = 32.0
"""

# A part file whose build() returns, besides the shape, the ordinary non-shape fields of a part: plain ones, one derived from a
# param, and param values inside a container - what the framework walks when the node hands these dicts back.
BANK_PART = '''
import params
from build123d import Box
{TICK}
def build():
    _tick("@PID@")
    w = params.@PARAM@
    return {"name": "@PID@", "shape": Box(w, 10, params.PLATE_T), "color": "#888888", "process": "mill",
            "material": "S235JR", "thickness": params.PLATE_T, "note": "w=%.1f" % w,
            "meta": {"grade": params.BANK_GRADE}, "bom": [params.BANK_GRADE, "@PID@"]}
'''

# src: a param that reaches no geometry at all, only a non-shape field nested in a dict and a list
SRC_PART = '''
import params
from build123d import Box
{TICK}
def build():
    _tick("g_src")
    return {"name": "g_src", "shape": Box(6, 6, 2), "color": "#888888", "process": "mill",
            "meta": {"grade": params.SRC_GRADE}, "bom": [params.SRC_GRADE]}
'''

# rdr: its geometry comes from that non-shape field of another node's part
RDR_PART = '''
from build123d import Box
from parts import src
{TICK}
def build():
    _tick("r_grade")
    grade = src.build()[0]["meta"]["grade"]
    return {"name": "r_grade", "shape": Box(4, 4, 2.0 + len(grade)), "color": "#654321", "process": "mill"}
'''

# the node's shared module: three params read at IMPORT time, one of them handed out through a function
LAYOUT = '''import params

SPAN_A = params.SPAN_A_MM * 2
SPAN_B = params.SPAN_B_MM * 3
SPAN_C = params.SPAN_C_MM + 1


def span_c():
    return SPAN_C
'''

SHARED_PART = '''
from build123d import Box
from . import _layout
{TICK}
def build():
    _tick("@PID@")
    return {"name": "@PID@", "shape": Box(@EXPR@, 5, 2), "color": "#888888", "process": "mill"}
'''

ASSEMBLE = '''import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parts import @MODS@
out = []
@CALLS@
for p in out:
    p["name"], p["process"]                      # the part dicts are used; no shape is touched
'''

SITECUSTOMIZE = '''
import os, uuid
import build123d


def _wrap(name):
    real = getattr(build123d, name, None)
    if real is None:
        return
    def counting(path, *a, **k):
        d = os.environ["GEOM_READS_DIR"]
        os.makedirs(d, exist_ok=True)
        fd = os.open(os.path.join(d, uuid.uuid4().hex), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, (name + " " + os.path.basename(str(path))).encode())
        os.close(fd)
        return real(path, *a, **k)
    setattr(build123d, name, counting)


for _n in ("import_step", "import_brep"):        # every way a stored part geometry can be read back
    _wrap(_n)
'''


def _bank(pid, param):
    return BANK_PART.replace("@PID@", pid).replace("@PARAM@", param)


def _shared(pid, expr):
    return SHARED_PART.replace("@PID@", pid).replace("@EXPR@", expr)


BIG = {f"b{i}": _bank(f"b{i}", f"B{i}") for i in range(1, 9)}
SMALL = {f"s{i}": _bank(f"s{i}", f"S{i}") for i in (1, 2)}
SHARED = {"p_a": _shared("p_a", "_layout.SPAN_A"), "p_b": _shared("p_b", "_layout.SPAN_B"),
          "p_c": _shared("p_c", "_layout.span_c()")}
NODES = [("big", BIG), ("small", SMALL), ("shared", SHARED), ("src", {"g_src": SRC_PART}), ("rdr", {"r_grade": RDR_PART})]


def _make(root):
    root = Path(root)
    H.write_base(root, PARAMS)
    for node, parts in NODES:
        H.write_node(root, node, parts, [(pid, (30 * i, 0, 0)) for i, pid in enumerate(parts)])
    (H.cad_dir(root) / "parts" / "shared" / "_layout.py").write_text(LAYOUT)
    mods = ", ".join(n for n, _ in NODES)
    calls = "\n".join(f"out += {n}.build()" for n, _ in NODES)
    (H.cad_dir(root) / "assemble.py").write_text(ASSEMBLE.replace("@MODS@", mods).replace("@CALLS@", calls))
    return root


def _copy(src, dst):
    shutil.copytree(src, dst)                    # copy2: source mtimes are preserved, which the incremental build keys on
    for p in Path(dst).rglob("__pycache__"):
        shutil.rmtree(p, ignore_errors=True)
    shutil.rmtree(Path(dst) / "counter", ignore_errors=True)
    return dst


@pytest.fixture(scope="session")
def base(tmp_path_factory):
    """(root, Run) after one clean --model build, built once per run; a failed cold build surfaces as a failing assertion."""
    root = _make(tmp_path_factory.mktemp("cachereads") / "proj")
    return root, H.run_model(root)


@pytest.fixture
def proj(base, tmp_path):
    root, cold = base
    return _copy(root, tmp_path / "proj"), cold


@pytest.fixture
def copies(base, tmp_path):
    """(make_a_copy(), cold Run): several private copies of the cold-built project, for comparing two runs."""
    root, cold = base
    n = itertools.count(1)
    return (lambda: _copy(root, tmp_path / f"proj{next(n)}")), cold


def _run_counted(root, tag):
    """--model, with build123d's importers counted in the build process: (Run, [what was read back])."""
    env = H.env_for(root)
    sc = Path(root).parent / "sc"
    sc.mkdir(exist_ok=True)
    (sc / "sitecustomize.py").write_text(SITECUSTOMIZE)
    env["PYTHONPATH"] = str(sc)
    d = Path(root).parent / f"reads_{tag}"
    env["GEOM_READS_DIR"] = str(d)
    cp = procgroup.run([sys.executable, str(H.CAD_INC), "--model", "assemble.py"], cwd=H.cad_dir(root), env=env,
                       capture_output=True, text=True, timeout=300)
    return H.Run(cp), sorted(p.read_text() for p in d.iterdir()) if d.is_dir() else []


def _pu(node, pid):
    """The `[cad_incremental] rebuilt:` token of a part file / of a node __init__ (helpers.part_unit / node_unit)."""
    return f"{node}.{pid}.build"


def _nu(node):
    return f"{node}.build"


# What a run that rebuilds ONE part file may read back, whatever the node places: the one part that really was rebuilt, plus the
# framework's own fixed cost. Measured today on the 2-part node `small` (3 reads for 1 reused part), while the 8-part node `big`
# reads 8 - one per reused placement, which is what must go.
FLOOR = 3


def _state(root):
    """part id -> (cache key, content sha of its STEP): what must stay untouched for every part that was not rebuilt."""
    return {p["id"]: (p["key"], H.sha_file(Path(root) / p["step"])) for p in H.read_parts(root) if "step" in p}


def _x(root, pid):
    return H.step_bbox(Path(root) / H.by_id(root)[pid]["step"]).size.X


# ================================================================ DW1: the cost of a node re-run
def test_rebuilding_one_part_file_does_not_read_the_reused_parts_back(copies):
    """DW1: params.B5 -> big/b5.py rebuilds and big/__init__.py re-runs, placing 7 parts it reused. Those 7 are handed out as
    lazy stand-ins and nothing in the node code touches a shape, so the run must read at most a constant number of geometries."""
    mk, cold = copies
    cold.ok()
    root = mk()
    before = _state(root)
    H.edit(H.cad_dir(root) / "params.py", "B5 = 14.0", "B5 = 24.0")
    r, reads = _run_counted(root, "big")
    r.ok()
    assert [t for t in H.ticks(root) if t in BIG] == ["b5"], f"only b5.build may run, ran: {H.ticks(root)}"
    assert {_pu("big", "b5"), _nu("big")} <= r.rebuilt, r.rebuilt
    assert len(reads) <= FLOOR, (f"{len(reads)} part geometries were read back on a run that rebuilt ONE part file of a node "
                                 f"placing {len(BIG)} parts; the {len(BIG) - 1} reused ones must stay lazy, so at most {FLOOR} "
                                 f"reads (the rebuilt part and the framework's fixed cost) may happen: {reads}")
    assert _x(root, "b5") == pytest.approx(24.0, abs=1e-6), "b5 must be rebuilt with the new width"
    after = _state(root)
    moved = {pid for pid, v in after.items() if pid != "b5" and before.get(pid) != v}
    assert not moved, f"the reused parts' keys and STEPs must be untouched, these changed: {sorted(moved)}"


def test_the_geometry_reads_of_a_node_re_run_do_not_grow_with_the_parts_it_places(copies):
    """DW1: the same edit in a node placing 2 parts and in one placing 8 must cost the same number of geometry reads - the cost
    belongs to the ONE part file that was rebuilt, not to the reused placements."""
    mk, cold = copies
    cold.ok()
    a, b = mk(), mk()
    H.edit(H.cad_dir(a) / "params.py", "S1 = 20.0", "S1 = 30.0")
    H.edit(H.cad_dir(b) / "params.py", "B5 = 14.0", "B5 = 24.0")
    ra, na = _run_counted(a, "small")
    rb, nb = _run_counted(b, "big2")
    ra.ok()
    rb.ok()
    assert [t for t in H.ticks(a) if t in SMALL] == ["s1"], H.ticks(a)
    assert [t for t in H.ticks(b) if t in BIG] == ["b5"], H.ticks(b)
    assert len(nb) <= len(na), (f"a re-run of a node placing {len(BIG)} parts read {len(nb)} geometries, one placing "
                                f"{len(SMALL)} read {len(na)}; both rebuilt exactly one part file, so the bigger node must not "
                                f"cost more: the reused placements must be free\nbig: {nb}\nsmall: {na}")


def test_a_param_that_reaches_only_a_non_shape_field_still_rebuilds_its_readers(proj):
    """DW1s: SRC_GRADE reaches no geometry, only src/g_src.py's `meta` dict and `bom` list; rdr/r_grade.py builds its thickness
    from that field. Keeping the reused parts lazy must not make the framework blind to this (real) dependency."""
    root, cold = proj
    cold.ok()
    assert H.step_bbox(Path(root) / H.by_id(root)["r_grade"]["step"]).size.Z == pytest.approx(6.0, abs=1e-6), 'cold: 2 + len("S235")'
    H.edit(H.cad_dir(root) / "params.py", 'SRC_GRADE = "S235"', 'SRC_GRADE = "S235JR"')
    r = H.run_model(root).ok()
    want = {_pu("src", "g_src"), _nu("src"), _pu("rdr", "r_grade"), _nu("rdr")}
    assert want <= r.rebuilt, f"a param behind a non-shape field must rebuild the part and the part that reads it: {r.rebuilt}"
    assert H.step_bbox(Path(root) / H.by_id(root)["r_grade"]["step"]).size.Z == pytest.approx(8.0, abs=1e-6), \
        'r_grade must be rebuilt from the new field: 2 + len("S235JR")'


# ================================================================ DW3: a param read by a node's shared module
def test_a_param_a_shared_module_read_at_import_time_rebuilds_only_the_parts_using_that_name(proj):
    """DW3: parts/shared/_layout.py reads SPAN_A_MM, SPAN_B_MM and SPAN_C_MM at import time. p_b is the only part file that uses
    the name derived from SPAN_B_MM, so changing SPAN_B_MM rebuilds p_b and the node - and nothing else."""
    root, cold = proj
    cold.ok()
    assert _x(root, "p_b") == pytest.approx(93.0, abs=1e-6), "cold: SPAN_B = 31 * 3"
    H.edit(H.cad_dir(root) / "params.py", "SPAN_B_MM = 31.0", "SPAN_B_MM = 41.0")
    r = H.run_model(root).ok()
    assert [t for t in H.ticks(root) if t in SHARED] == ["p_b"], \
        f"only p_b.py uses the name _layout derived from params.SPAN_B_MM, ran: {H.ticks(root)}"
    assert {_pu("shared", "p_b"), _nu("shared")} <= r.rebuilt, r.rebuilt
    assert not {_pu("shared", "p_a"), _pu("shared", "p_c")} & r.rebuilt, r.rebuilt
    assert _x(root, "p_b") == pytest.approx(123.0, abs=1e-6)
    assert _x(root, "p_a") == pytest.approx(60.0, abs=1e-6), "p_a must still be the reused one"


def test_the_other_param_of_the_same_shared_module_rebuilds_only_its_own_part(proj):
    """DW3, the other way round: SPAN_A_MM belongs to p_a only."""
    root, cold = proj
    cold.ok()
    H.edit(H.cad_dir(root) / "params.py", "SPAN_A_MM = 30.0", "SPAN_A_MM = 35.0")
    r = H.run_model(root).ok()
    assert [t for t in H.ticks(root) if t in SHARED] == ["p_a"], f"only p_a may rebuild, ran: {H.ticks(root)}"
    assert {_pu("shared", "p_a"), _nu("shared")} <= r.rebuilt, r.rebuilt
    assert not {_pu("shared", "p_b"), _pu("shared", "p_c")} & r.rebuilt, r.rebuilt
    assert _x(root, "p_a") == pytest.approx(70.0, abs=1e-6)


def test_a_part_calling_a_function_of_the_shared_module_keeps_the_param_behind_it(proj):
    """DW3 (soundness): _layout.span_c() hands out a module-level value derived from SPAN_C_MM. p_c calls it, so changing
    SPAN_C_MM must still rebuild p_c with the new value - precision must not cost soundness."""
    root, cold = proj
    cold.ok()
    assert _x(root, "p_c") == pytest.approx(33.0, abs=1e-6), "cold: SPAN_C = 32 + 1"
    H.edit(H.cad_dir(root) / "params.py", "SPAN_C_MM = 32.0", "SPAN_C_MM = 52.0")
    r = H.run_model(root).ok()
    assert "p_c" in H.ticks(root), f"p_c.py calls _layout.span_c(), which reads SPAN_C_MM: it must rebuild, ran {H.ticks(root)}"
    assert {_pu("shared", "p_c"), _nu("shared")} <= r.rebuilt, r.rebuilt
    assert _x(root, "p_c") == pytest.approx(53.0, abs=1e-6)


# ================================================================ soundness: values the param mark cannot follow
# A helper module turns params into values that cannot carry a mark (a concatenated str, a bool from a comparison) or reads a data
# file, all at IMPORT time, and hands them out only through functions a part file imports BY NAME. The part's record must still
# hold those params and that file. Own project: machine params GRADE and W, a data file data/t.json.
HELPER_PARAMS = """GRADE = "S235"
W = 7.0
"""

TBL2 = '''import params

LABEL = "g-" + params.GRADE
WIDE = params.W > 5


def label():
    return LABEL


def wide():
    return WIDE
'''

JSONHELPER = '''import json
import os

DATA = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "t.json")))


def width():
    return DATA["w"]
'''

TBL_PART = '''
from build123d import Box
from parts._tbl2 import label, wide
{TICK}
def build():
    _tick("t_part")
    return {"name": "t_part", "shape": Box(5, 5, 2.0 + len(label()) + (1 if wide() else 0)), "color": "#888888",
            "process": "mill"}
'''

JSON_PART = '''
from build123d import Box
from parts._jsonhelper import width
{TICK}
def build():
    _tick("j_part")
    return {"name": "j_part", "shape": Box(width(), 2, 2), "color": "#888888", "process": "mill"}
'''


def _make_helpers(root):
    root = Path(root)
    cad = H.write_base(root, HELPER_PARAMS)
    (cad / "data").mkdir()
    (cad / "data" / "t.json").write_text('{"w": 12.0}\n')
    (cad / "parts" / "_tbl2.py").write_text(TBL2)
    (cad / "parts" / "_jsonhelper.py").write_text(JSONHELPER)
    H.write_node(root, "tblnode", {"t_part": TBL_PART})
    H.write_node(root, "jsonnode", {"j_part": JSON_PART})
    (cad / "assemble.py").write_text(ASSEMBLE.replace("@MODS@", "tblnode, jsonnode")
                                     .replace("@CALLS@", "out += tblnode.build()\nout += jsonnode.build()"))
    return root


@pytest.fixture(scope="session")
def helper_base(tmp_path_factory):
    root = _make_helpers(tmp_path_factory.mktemp("helpers") / "proj")
    return root, H.run_model(root)


@pytest.fixture
def helper_proj(helper_base, tmp_path):
    root, cold = helper_base
    return _copy(root, tmp_path / "proj"), cold


def _z(root, pid):
    return H.step_bbox(Path(root) / H.by_id(root)[pid]["step"]).size.Z


def test_an_unmarkable_value_reached_through_a_function_imported_by_name_rebuilds_the_part(helper_proj):
    """parts/_tbl2.py makes LABEL = "g-" + params.GRADE (a str) and WIDE = params.W > 5 (a bool) at import time and hands them
    out only through label() / wide(); tblnode/t_part.py does `from parts._tbl2 import label, wide`. Z = 2 + len(label) + wide."""
    root, cold = helper_proj
    cold.ok()
    assert _z(root, "t_part") == pytest.approx(9.0, abs=1e-6), 'cold: 2 + len("g-S235") + 1'
    H.edit(H.cad_dir(root) / "params.py", 'GRADE = "S235"', 'GRADE = "S235JRLONG"')
    r = H.run_model(root).ok()
    assert "t_part" in H.ticks(root), f"t_part.py's size comes from params.GRADE through _tbl2.label(); it must rebuild: {r.rebuilt}"
    assert _pu("tblnode", "t_part") in r.rebuilt, r.rebuilt
    assert _z(root, "t_part") == pytest.approx(15.0, abs=1e-6), 'the STEP must be the new part: 2 + len("g-S235JRLONG") + 1'

    H.clear_ticks(root)
    H.edit(H.cad_dir(root) / "params.py", "W = 7.0", "W = 3.0")
    r = H.run_model(root).ok()
    assert "t_part" in H.ticks(root), f"t_part.py's size comes from params.W > 5 through _tbl2.wide(); it must rebuild: {r.rebuilt}"
    assert _z(root, "t_part") == pytest.approx(14.0, abs=1e-6), "W = 3 is not > 5: 2 + 12 + 0"


def test_a_data_file_a_helper_reads_at_import_time_rebuilds_the_part_that_calls_it(helper_proj):
    """parts/_jsonhelper.py loads data/t.json at import time and hands out width(); jsonnode/j_part.py does
    `from parts._jsonhelper import width`. The file belongs to j_part's record (the router ships job folders by that list) and
    editing it rebuilds j_part."""
    root, cold = helper_proj
    cold.ok()
    nodes = json.loads((Path(root) / "build/model/nodes.json").read_text())
    rec = nodes.get("parts.jsonnode.j_part.build")
    assert rec is not None, f"no record for j_part in nodes.json: {sorted(nodes)}"
    files = [f if isinstance(f, str) else f[0] for f in rec.get("files", [])]
    assert "mechanics/cad/data/t.json" in files, f"j_part's record must list the data file its helper opened: {files}"
    assert H.step_bbox(Path(root) / H.by_id(root)["j_part"]["step"]).size.X == pytest.approx(12.0, abs=1e-6)
    (H.cad_dir(root) / "data" / "t.json").write_text('{"w": 20.0}\n')
    r = H.run_model(root).ok()
    assert "j_part" in H.ticks(root), f"j_part's width comes from data/t.json through _jsonhelper.width(): {r.rebuilt}"
    assert H.step_bbox(Path(root) / H.by_id(root)["j_part"]["step"]).size.X == pytest.approx(20.0, abs=1e-6)


# ================================================================ soundness: flow between module-level names of a shared module
# parts/_h.py first binds a name to a param (G = params.GRADE), then derives from THAT NAME in a way that cannot carry the param's
# mark (str concatenation, comparison, int(), dict lookup, an `if` choosing a value, a decorated function). A part file that takes
# the derived name must still follow the param. One part file per derived name; each sizes its Box (Z) from what it took.
FLOW_PARAMS = """GRADE = "S235"
W = 7.0
SIZE = "S"
FLAG = True
"""

FLOW_H = '''import functools

import params

G = params.GRADE
LABEL = "g-" + G
W = params.W
WIDE = W > 5
K = int(W)
SZ = params.SIZE
PROFILE = {"S": 3.0, "L": 6.0}[SZ]
if params.FLAG:
    MODE = 2.0
else:
    MODE = 4.0
WD = params.W > 5


def deco(f):
    @functools.wraps(f)
    def w(*a, **k):
        return f(*a, **k)
    return w


@deco
def wide2():
    return WD
'''

FLOW_PART = '''
from build123d import Box
from parts._h import @NAME@
{TICK}
def build():
    _tick("@PID@")
    return {"name": "@PID@", "shape": Box(5, 5, @EXPR@), "color": "#888888", "process": "mill"}
'''

# part id -> (name it imports from parts/_h.py, the Z it builds from it)
FLOW_CASES = {"f_label": ("LABEL", "2.0 + len(LABEL)"), "f_wide": ("WIDE", "3.0 if WIDE else 1.0"), "f_k": ("K", "float(K)"),
              "f_prof": ("PROFILE", "PROFILE"), "f_mode": ("MODE", "MODE"), "f_deco": ("wide2", "3.0 if wide2() else 1.0")}
# param edit -> (old, new) text in params.py, the part files that must rebuild, and every part's Z afterwards (a clean build's)
FLOW_EDITS = {
    "GRADE": (('GRADE = "S235"', 'GRADE = "S235JRLONG"'), {"f_label"},
              {"f_label": 14.0, "f_wide": 3.0, "f_k": 7.0, "f_prof": 3.0, "f_mode": 2.0, "f_deco": 3.0}),
    "W": (("W = 7.0", "W = 3.0"), {"f_wide", "f_k", "f_deco"},
          {"f_label": 8.0, "f_wide": 1.0, "f_k": 3.0, "f_prof": 3.0, "f_mode": 2.0, "f_deco": 1.0}),
    "SIZE": (('SIZE = "S"', 'SIZE = "L"'), {"f_prof"},
             {"f_label": 8.0, "f_wide": 3.0, "f_k": 7.0, "f_prof": 6.0, "f_mode": 2.0, "f_deco": 3.0}),
    "FLAG": (("FLAG = True", "FLAG = False"), {"f_mode"},
             {"f_label": 8.0, "f_wide": 3.0, "f_k": 7.0, "f_prof": 3.0, "f_mode": 4.0, "f_deco": 3.0}),
}
FLOW_COLD = {"f_label": 8.0, "f_wide": 3.0, "f_k": 7.0, "f_prof": 3.0, "f_mode": 2.0, "f_deco": 3.0}


def _make_flow(root):
    root = Path(root)
    cad = H.write_base(root, FLOW_PARAMS)
    (cad / "parts" / "_h.py").write_text(FLOW_H)
    H.write_node(root, "hn", {pid: FLOW_PART.replace("@NAME@", n).replace("@PID@", pid).replace("@EXPR@", e)
                              for pid, (n, e) in FLOW_CASES.items()})
    (cad / "assemble.py").write_text(ASSEMBLE.replace("@MODS@", "hn").replace("@CALLS@", "out += hn.build()"))
    return root


@pytest.fixture(scope="session")
def flow_base(tmp_path_factory):
    root = _make_flow(tmp_path_factory.mktemp("flow") / "proj")
    return root, H.run_model(root)


def _zs(root):
    return {pid: round(_z(root, pid), 6) for pid in FLOW_CASES}


@pytest.mark.parametrize("param", list(FLOW_EDITS))
def test_a_value_derived_from_an_earlier_module_level_name_rebuilds_the_part_that_takes_it(flow_base, tmp_path, param):
    """parts/_h.py: G = params.GRADE; LABEL = "g-" + G (and W -> WIDE, K; SIZE -> PROFILE; FLAG -> MODE by an `if`; W -> WD
    behind a decorated wide2()). Editing the param rebuilds every part file that took a name derived from it, and the STEPs are
    what a clean build of the edited project gives."""
    base, cold = flow_base
    cold.ok()
    root = _copy(base, tmp_path / "proj")
    assert _zs(root) == FLOW_COLD, f"cold build: {_zs(root)}"
    (old, new), must, want = FLOW_EDITS[param]
    H.edit(H.cad_dir(root) / "params.py", old, new)
    r = H.run_model(root).ok()
    ran = set(H.ticks(root)) & set(FLOW_CASES)
    assert must <= ran, f"params.{param} reaches {sorted(must)} through parts/_h.py; rebuilt only {sorted(ran)} ({r.rebuilt})"
    got = _zs(root)
    assert got == want, f"after editing params.{param} the STEPs must be the new parts: got {got}, want {want}"
    clean = _copy(root, tmp_path / "clean")
    shutil.rmtree(clean / "build" / "model")
    H.run_model(clean).ok()
    assert _zs(clean) == got, f"the incremental result must equal a clean build: {got} != {_zs(clean)}"


# ================================================================ soundness: module-level MUTATION of a name, and a star import
# parts/_m.py fills a container at import time from a param - by a loop, inside an `if`, through a call at module level, inside a
# function called at import time - and parts/_s.py does `from params import *` and derives from the names it got. A part file
# takes ONE name (the container, or the derived value) and sizes its Box (Z) from it.
MUT_PARAMS = """N = 3
BIG = True
A = 2.0
B = 4.0
GRADE = "S235"
HOLES = [1.0, 2.0, 3.0]
"""

MUT_M = '''import params

POSTS = []
for k in range(params.N):
    POSTS.append(1.0)

TABLE = {}
if params.BIG:
    EXTRA = 1                                    # the branch binds one name and mutates another
    TABLE["h"] = 9.0
else:
    TABLE["h"] = 3.0


def reg(t, v):
    t.append(v)


REG = []
reg(REG, params.A)

ROWS = []


def fill():
    ROWS.append(params.B)


fill()
'''

MUT_S = '''from params import *

LABEL = "g-" + GRADE
N_HOLES = len(HOLES)
'''

MUT_PART = '''
from build123d import Box
from parts.@MOD@ import @NAME@
{TICK}
def build():
    _tick("@PID@")
    return {"name": "@PID@", "shape": Box(5, 5, @EXPR@), "color": "#888888", "process": "mill"}
'''

# part id -> (helper module, the ONE name it imports, the Z it builds from it)
MUT_CASES = {"m_loop": ("_m", "POSTS", "float(len(POSTS))"), "m_if": ("_m", "TABLE", 'TABLE["h"]'),
             "m_reg": ("_m", "REG", "REG[0]"), "m_rows": ("_m", "ROWS", "ROWS[0]"),
             "s_label": ("_s", "LABEL", "2.0 + len(LABEL)"), "s_holes": ("_s", "N_HOLES", "float(N_HOLES)")}
MUT_COLD = {"m_loop": 3.0, "m_if": 9.0, "m_reg": 2.0, "m_rows": 4.0, "s_label": 8.0, "s_holes": 3.0}
# param -> (old, new) text in params.py, the part that must rebuild, its Z afterwards (every other part keeps its cold Z)
MUT_EDITS = {
    "N-loop": (("N = 3", "N = 5"), "m_loop", 5.0),
    "BIG-if": (("BIG = True", "BIG = False"), "m_if", 3.0),
    "A-call": (("A = 2.0", "A = 6.0"), "m_reg", 6.0),
    "B-fill": (("B = 4.0", "B = 7.0"), "m_rows", 7.0),
    "GRADE-star": (('GRADE = "S235"', 'GRADE = "S235JRLONG"'), "s_label", 14.0),
    "HOLES-star": (("HOLES = [1.0, 2.0, 3.0]", "HOLES = [1.0, 2.0, 3.0, 4.0, 5.0]"), "s_holes", 5.0),
}


def _make_mut(root):
    root = Path(root)
    cad = H.write_base(root, MUT_PARAMS)
    (cad / "parts" / "_m.py").write_text(MUT_M)
    (cad / "parts" / "_s.py").write_text(MUT_S)
    H.write_node(root, "mn", {pid: MUT_PART.replace("@MOD@", m).replace("@NAME@", n).replace("@PID@", pid).replace("@EXPR@", e)
                              for pid, (m, n, e) in MUT_CASES.items()})
    (cad / "assemble.py").write_text(ASSEMBLE.replace("@MODS@", "mn").replace("@CALLS@", "out += mn.build()"))
    return root


@pytest.fixture(scope="session")
def mut_base(tmp_path_factory):
    root = _make_mut(tmp_path_factory.mktemp("mut") / "proj")
    return root, H.run_model(root)


def _zs_of(root, ids):
    return {pid: round(_z(root, pid), 6) for pid in ids}


@pytest.mark.parametrize("case", list(MUT_EDITS))
def test_a_name_a_shared_module_filled_or_star_imported_rebuilds_the_part_that_takes_it(mut_base, tmp_path, case):
    """Loop: POSTS.append in `for k in range(params.N)`. If: TABLE["h"] set in `if params.BIG` (whose branch also binds EXTRA).
    Call: reg(REG, params.A) at
    module level. Fill: fill() appends params.B to ROWS, called at import time. Star: _s.py does `from params import *`, then
    LABEL = "g-" + GRADE and N_HOLES = len(HOLES). Editing the param rebuilds the part that took the name; STEPs = a clean build."""
    base, cold = mut_base
    cold.ok()
    root = _copy(base, tmp_path / "proj")
    assert _zs_of(root, MUT_CASES) == MUT_COLD, f"cold build: {_zs_of(root, MUT_CASES)}"
    (old, new), pid, z = MUT_EDITS[case]
    H.edit(H.cad_dir(root) / "params.py", old, new)
    r = H.run_model(root).ok()
    mod, name, _ = MUT_CASES[pid]
    assert pid in H.ticks(root), (f"{pid}.py takes {name} from parts/{mod}.py, which `{new}` changes: it must rebuild, "
                                  f"ran {H.ticks(root)} ({sorted(r.rebuilt)})")
    got = _zs_of(root, MUT_CASES)
    assert got == dict(MUT_COLD, **{pid: z}), f"after `{new}`: got {got}, want {pid} = {z} and the rest unchanged"
    clean = _copy(root, tmp_path / "clean")
    shutil.rmtree(clean / "build" / "model")
    H.run_model(clean).ok()
    assert _zs_of(clean, MUT_CASES) == got, f"the incremental result must equal a clean build: {got} != {_zs_of(clean, MUT_CASES)}"


# ================================================================ soundness: objects, other modules' containers, vars()
# ns/_layout.py fills a types.SimpleNamespace through a function at import time; parts/_wa.py and parts/_wb.py append a param
# into ANOTHER module's list (parts/_oa.py, parts/_ob.py) - by attribute, and by the name imported from it; vn/_layout.py's K is
# read by its part through vars(). Each part takes ONE thing and sizes its Box (Z) from it.
OBJ_PARAMS = """W = 4.0
X = 3.0
VW = 4.0
"""

OBJ_NS_LAYOUT = '''import types

import params

CFG = types.SimpleNamespace()


def _setup(cfg):
    cfg.w = float(params.W)
    return True


_ok = _setup(CFG)
'''

OBJ_WA = '''import params
from parts import _oa

_oa.TABLE.append(int(params.X))
'''

OBJ_WB = '''import params
from parts._ob import TABLE

TABLE.append(int(params.X))
'''

OBJ_VN_LAYOUT = '''import params

W = params.VW
K = int(W)
'''

OBJ_PART = '''
@HEAD@
from build123d import Box
{TICK}
def build():
    _tick("@PID@")
    return {"name": "@PID@", "shape": Box(5, 5, @EXPR@), "color": "#888888", "process": "mill"}
'''

# node -> {part id: (import lines, Z expression)}
OBJ_NODES = {
    "ns": {"c_cfg": ("from ._layout import CFG", "CFG.w")},
    # the writers run because a SIBLING part file imports them (first in the node); the readers only import the list
    "ow": {"a_writer": ("import parts._wa\nimport parts._wb", "1.0"),
           "o_attr": ("from parts._oa import TABLE", "float(TABLE[0])"),
           "o_name": ("from parts._ob import TABLE", "float(TABLE[0])")},
    "vn": {"v_vars": ("import parts.vn._layout as L", 'float(vars(L)["K"])')},
}
OBJ_IDS = [pid for parts in OBJ_NODES.values() for pid in parts]
OBJ_COLD = {"c_cfg": 4.0, "a_writer": 1.0, "o_attr": 3.0, "o_name": 3.0, "v_vars": 4.0}
# case -> (old, new) text in params.py, the part that must rebuild, its Z afterwards
OBJ_EDITS = {
    "namespace-filled-by-a-function": (("W = 4.0", "W = 6.0"), "c_cfg", 6.0),
    "other-modules-list-by-attribute": (("X = 3.0", "X = 7.0"), "o_attr", 7.0),
    "other-modules-list-by-imported-name": (("X = 3.0", "X = 7.0"), "o_name", 7.0),
    "read-through-vars": (("VW = 4.0", "VW = 6.0"), "v_vars", 6.0),
}


def _make_obj(root):
    root = Path(root)
    cad = H.write_base(root, OBJ_PARAMS)
    (cad / "parts" / "_oa.py").write_text("TABLE = []\n")
    (cad / "parts" / "_ob.py").write_text("TABLE = []\n")
    (cad / "parts" / "_wa.py").write_text(OBJ_WA)
    (cad / "parts" / "_wb.py").write_text(OBJ_WB)
    for node, parts in OBJ_NODES.items():
        H.write_node(root, node, {pid: OBJ_PART.replace("@HEAD@", head).replace("@PID@", pid).replace("@EXPR@", e)
                                  for pid, (head, e) in parts.items()})
    (cad / "parts" / "ns" / "_layout.py").write_text(OBJ_NS_LAYOUT)
    (cad / "parts" / "vn" / "_layout.py").write_text(OBJ_VN_LAYOUT)
    (cad / "assemble.py").write_text(ASSEMBLE.replace("@MODS@", ", ".join(OBJ_NODES))
                                     .replace("@CALLS@", "\n".join(f"out += {n}.build()" for n in OBJ_NODES)))
    return root


@pytest.fixture(scope="session")
def obj_base(tmp_path_factory):
    root = _make_obj(tmp_path_factory.mktemp("obj") / "proj")
    return root, H.run_model(root)


@pytest.mark.parametrize("case", list(OBJ_EDITS))
def test_a_value_stored_in_an_object_another_module_or_read_by_vars_rebuilds_the_part(obj_base, tmp_path, case):
    """namespace: CFG = SimpleNamespace(), _setup(CFG) sets CFG.w = float(params.W) at import time. by attribute: parts/_wa.py
    does `from parts import _oa; _oa.TABLE.append(int(params.X))`. by imported name: parts/_wb.py does
    `from parts._ob import TABLE; TABLE.append(int(params.X))`; both writers are imported by the sibling a_writer.py, the readers
    import only the list. vars: the part reads vars(L)["K"], K = int(W), W = params.VW (a param of its own,
    so each case fails only for its own reason).
    Editing the param rebuilds the part that took the value; STEPs = a clean build."""
    base, cold = obj_base
    cold.ok()
    root = _copy(base, tmp_path / "proj")
    assert _zs_of(root, OBJ_IDS) == OBJ_COLD, f"cold build: {_zs_of(root, OBJ_IDS)}"
    (old, new), pid, z = OBJ_EDITS[case]
    H.edit(H.cad_dir(root) / "params.py", old, new)
    r = H.run_model(root).ok()
    assert pid in H.ticks(root), f"{case}: `{new}` changes what {pid}.py took, it must rebuild; ran {H.ticks(root)} ({sorted(r.rebuilt)})"
    got = _zs_of(root, OBJ_IDS)
    assert got[pid] == pytest.approx(z, abs=1e-6), f"{case}: after `{new}` {pid} must be {z}, got {got}"
    clean = _copy(root, tmp_path / "clean")
    shutil.rmtree(clean / "build" / "model")
    H.run_model(clean).ok()
    assert _zs_of(clean, OBJ_IDS) == got, f"the incremental result must equal a clean build: {got} != {_zs_of(clean, OBJ_IDS)}"


# ================================================================ stability: a function behind an lru_cache helper
# parts/_vh.py: size(k) calls an @functools.lru_cache helper _sec(k) and adds params.S. Several part files take `size` by name
# and call it with their own k, so the cache fills differently over the run. A part keyed by what it took must get the same key
# on the next run when nothing changed.
LRU_VH = '''import functools

import params


@functools.lru_cache(maxsize=None)
def _sec(k):
    return float(k) * 2


def size(k):
    return _sec(k) + params.S
'''

LRU_PART = '''
from build123d import Box
from parts._vh import size
{TICK}
def build():
    _tick("@PID@")
    return {"name": "@PID@", "shape": Box(size(@K@), 1, 1), "color": "#888888", "process": "mill"}
'''

LRU_KS = {"l1": 1, "l2": 2, "l3": 3, "l4": 4}


def test_a_function_calling_an_lru_cache_helper_keeps_its_key_on_a_no_change_run(tmp_path):
    """Cold build; edit S -> the parts using size() rebuild with the new sizes; then two no-change runs rebuild nothing."""
    root = Path(tmp_path / "proj")
    cad = H.write_base(root, "S = 1.0\n")
    (cad / "parts" / "_vh.py").write_text(LRU_VH)
    H.write_node(root, "lru", {pid: LRU_PART.replace("@PID@", pid).replace("@K@", str(k)) for pid, k in LRU_KS.items()},
                 [(pid, (10 * i, 0, 0)) for i, pid in enumerate(LRU_KS)])
    (cad / "assemble.py").write_text(ASSEMBLE.replace("@MODS@", "lru").replace("@CALLS@", "out += lru.build()"))
    H.run_model(root).ok()
    assert {pid: round(_x(root, pid), 6) for pid in LRU_KS} == {pid: 2.0 * k + 1.0 for pid, k in LRU_KS.items()}
    H.clear_ticks(root)
    H.edit(cad / "params.py", "S = 1.0", "S = 3.0")
    r = H.run_model(root).ok()
    assert set(LRU_KS) <= set(H.ticks(root)), f"size() adds params.S: every part using it must rebuild, ran {H.ticks(root)}"
    assert {pid: round(_x(root, pid), 6) for pid in LRU_KS} == {pid: 2.0 * k + 3.0 for pid, k in LRU_KS.items()}
    for n in (1, 2):
        H.clear_ticks(root)
        r = H.run_model(root).ok()
        assert r.summary[0] == 0 and H.ticks(root) == [], \
            f"no-change run {n} after the edit must rebuild nothing: nodes rebuilt {r.summary[0]}, {sorted(r.rebuilt)}"


# ================================================================ stability: a helper holding an absolute path of the project
# parts/_ph.py keeps the project's own folder in module-level globals (an os.path string and a pathlib.Path) and w() uses them.
# The same project built from another folder - another worker's checkout, a copy - is the same project: nothing may rebuild.
PATH_PH = '''import os
import pathlib

_DIR = os.path.dirname(os.path.abspath(__file__))
_P = pathlib.Path(__file__).parent


def w():
    return 3.0 if os.path.isdir(_DIR) and _P.is_dir() else 1.0
'''

PATH_PART = '''
import params
from build123d import Box
from parts._ph import w
{TICK}
def build():
    _tick("pw")
    return {"name": "pw", "shape": Box(w() + params.PW, 1, 1), "color": "#888888", "process": "mill"}
'''

# a part taking the absolute path string itself (e.g. to open a vendor file next to the helper)
PATH_PART_DIR = '''
import os
from build123d import Box
from parts._ph import _DIR
{TICK}
def build():
    _tick("pd")
    return {"name": "pd", "shape": Box(3.0 if os.path.isdir(_DIR) else 1.0, 1, 1), "color": "#888888", "process": "mill"}
'''


def test_the_same_project_built_from_another_folder_rebuilds_nothing(tmp_path):
    """Cold build; copy the whole project (build/model included) to another path; the copy's no-change run rebuilds nothing -
    neither pw.py (takes w(), which uses _DIR and _P) nor pd.py (takes the _DIR string itself) - and editing params.PW in the
    copy still rebuilds pw."""
    root = Path(tmp_path / "here" / "proj")
    cad = H.write_base(root, "PW = 1.0\n")
    (cad / "parts" / "_ph.py").write_text(PATH_PH)
    H.write_node(root, "pn", {"pd": PATH_PART_DIR, "pw": PATH_PART})
    (cad / "assemble.py").write_text(ASSEMBLE.replace("@MODS@", "pn").replace("@CALLS@", "out += pn.build()"))
    H.run_model(root).ok()
    assert _x(root, "pw") == pytest.approx(4.0, abs=1e-6), "cold: w() = 3 (its folders exist) + PW 1"
    assert _x(root, "pd") == pytest.approx(3.0, abs=1e-6), "cold: _DIR exists"
    other = _copy(root, tmp_path / "elsewhere" / "proj")
    r = H.run_model(other).ok()
    assert r.summary[0] == 0 and H.ticks(other) == [], \
        f"the same project from another folder must rebuild nothing: nodes rebuilt {r.summary[0]}, {sorted(r.rebuilt)}"
    H.edit(H.cad_dir(other) / "params.py", "PW = 1.0", "PW = 2.0")
    r = H.run_model(other).ok()
    assert "pw" in H.ticks(other), f"pw.py reads params.PW: it must rebuild in the copy, rebuilt {sorted(r.rebuilt)}"
    assert "pd" not in H.ticks(other), f"pd.py reads no PW: {H.ticks(other)}"
    assert _x(other, "pw") == pytest.approx(5.0, abs=1e-6)


# ================================================================ a taken global the build also changes; a taken param string
def _one_node_project(root, params_src, node, parts, layout):
    root = Path(root)
    cad = H.write_base(root, params_src)
    H.write_node(root, node, parts)
    (cad / "parts" / node / "_layout.py").write_text(layout)
    (cad / "assemble.py").write_text(ASSEMBLE.replace("@MODS@", node).replace("@CALLS@", f"out += {node}.build()"))
    return root


SEEN_PART = '''
from build123d import Box
from . import _layout as L
{TICK}
def build():
    _tick("t_seen")
    L.SEEN.append(1)
    return {"name": "t_seen", "shape": Box(5, 5, 2), "color": "#888888", "process": "mill"}
'''


def test_a_part_that_takes_a_module_global_and_appends_to_it_is_reused_on_no_change_runs(tmp_path):
    """t_seen.py takes L.SEEN (a list in its node's _layout.py) and appends to it in build(). Its own write must not make its
    key differ from run to run: both no-change runs after the cold build rebuild nothing."""
    root = _one_node_project(tmp_path / "proj", "UNUSED = 1.0\n", "tw", {"t_seen": SEEN_PART}, "SEEN = []\n")
    H.run_model(root).ok()
    for n in (1, 2):
        H.clear_ticks(root)
        r = H.run_model(root).ok()
        assert r.summary[0] == 0 and H.ticks(root) == [], \
            f"no-change run {n} must rebuild nothing: nodes rebuilt {r.summary[0]}, {sorted(r.rebuilt)}"


LABEL_LAYOUT = '''import params

LABEL = params.GRADE
OTHER = params.UNRELATED
'''

LABEL_PART = '''
from build123d import Box
from ._layout import LABEL
{TICK}
def build():
    _tick("t_label")
    return {"name": "t_label", "shape": Box(5, 5, 2.0 + len(LABEL)), "color": "#888888", "process": "mill"}
'''


def test_a_taken_string_from_a_param_follows_that_param_only(tmp_path):
    """_layout.py: LABEL = params.GRADE, OTHER = params.UNRELATED; t_label.py takes only LABEL. UNRELATED rebuilds nothing,
    GRADE rebuilds t_label to its new size."""
    root = _one_node_project(tmp_path / "proj", 'GRADE = "S235"\nUNRELATED = 1.0\n', "tl", {"t_label": LABEL_PART},
                             LABEL_LAYOUT)
    H.run_model(root).ok()
    assert _z(root, "t_label") == pytest.approx(6.0, abs=1e-6), 'cold: 2 + len("S235")'
    H.clear_ticks(root)
    H.edit(H.cad_dir(root) / "params.py", "UNRELATED = 1.0", "UNRELATED = 2.0")
    r = H.run_model(root).ok()
    assert r.summary[0] == 0 and H.ticks(root) == [], \
        f"t_label takes only LABEL (from GRADE); UNRELATED must rebuild nothing: {r.summary[0]}, {sorted(r.rebuilt)}"
    H.clear_ticks(root)
    H.edit(H.cad_dir(root) / "params.py", 'GRADE = "S235"', 'GRADE = "S235JR"')
    r = H.run_model(root).ok()
    assert "t_label" in H.ticks(root), f"LABEL is params.GRADE: t_label must rebuild, rebuilt {sorted(r.rebuilt)}"
    assert _z(root, "t_label") == pytest.approx(8.0, abs=1e-6), 'GRADE "S235JR": 2 + 6'


# ================================================================ a project module imported INSIDE build(); vars() + attribute
INNER_PART = '''
from build123d import Box
{TICK}
def build():
    from lay import C
    _tick("q_in")
    return {"name": "q_in", "shape": Box(C, 5, 2), "color": "#888888", "process": "mill"}
'''


def test_a_value_taken_from_a_project_module_imported_inside_build_follows_its_param(tmp_path):
    """mechanics/cad/lay.py: C = float(params.GLOBAL_W) + 1.0 (no param mark survives float()); q_in.py does `from lay import C`
    inside build(). After two no-edit runs (not asserted: a module imported inside build() may force a re-run), GLOBAL_W
    10 -> 20 must give q_in 21 wide, not 11."""
    root = Path(tmp_path / "proj")
    cad = H.write_base(root, "GLOBAL_W = 10.0\n")
    (cad / "lay.py").write_text("import params\n\nC = float(params.GLOBAL_W) + 1.0\n")
    H.write_node(root, "lz", {"q_in": INNER_PART})
    (cad / "assemble.py").write_text(ASSEMBLE.replace("@MODS@", "lz").replace("@CALLS@", "out += lz.build()"))
    H.run_model(root).ok()
    assert _x(root, "q_in") == pytest.approx(11.0, abs=1e-6)
    for _ in range(2):
        H.run_model(root).ok()
    H.clear_ticks(root)
    H.edit(cad / "params.py", "GLOBAL_W = 10.0", "GLOBAL_W = 20.0")
    r = H.run_model(root).ok()
    assert _x(root, "q_in") == pytest.approx(21.0, abs=1e-6), f"q_in is stale (ran {H.ticks(root)}, {sorted(r.rebuilt)})"


VARS_ATTR_PART = '''
from build123d import Box
import parts.vv._layout as L
{TICK}
def build():
    _tick("v_mix")
    return {"name": "v_mix", "shape": Box(float(vars(L)["K"]) + L.Q, 1, 1), "color": "#888888", "process": "mill"}
'''


def test_vars_mixed_with_an_attribute_take_follows_the_param_behind_vars(tmp_path):
    """vv/_layout.py: Q = 1.0, K = int(params.GLOBAL_W); v_mix.py reads K through vars(L) and Q as L.Q. The attribute take
    must not stand for everything the part read: GLOBAL_W 10 -> 20 rebuilds it 21 wide."""
    root = _one_node_project(tmp_path / "proj", "GLOBAL_W = 10.0\n", "vv", {"v_mix": VARS_ATTR_PART},
                             "import params\n\nQ = 1.0\nK = int(params.GLOBAL_W)\n")
    H.run_model(root).ok()
    assert _x(root, "v_mix") == pytest.approx(11.0, abs=1e-6)
    for _ in range(2):
        H.run_model(root).ok()
    H.clear_ticks(root)
    H.edit(H.cad_dir(root) / "params.py", "GLOBAL_W = 10.0", "GLOBAL_W = 20.0")
    r = H.run_model(root).ok()
    assert "v_mix" in H.ticks(root), f"K comes from GLOBAL_W: v_mix must rebuild, rebuilt {sorted(r.rebuilt)}"
    assert _x(root, "v_mix") == pytest.approx(21.0, abs=1e-6)


# ================================================================ a node that writes before it calls its part
def _node_init(root, node, body, imports=""):
    """A node __init__.py whose build() runs `body` (one line) and then places its part p_<node> at the origin."""
    (H.cad_dir(root) / "parts" / node / "__init__.py").write_text(
        f"from build123d import Pos\nfrom geom import place\n{imports}from . import p_{node}\n{H.TICK}\n"
        f"def build():\n    _tick('node_{node}')\n    {body}\n    return [place(p_{node}.build(), Pos(0, 0, 0))]\n")


WRITE_PART_PARAM = '''
import params
from build123d import Box
{TICK}
def build():
    _tick("p_wa")
    return {"name": "p_wa", "shape": Box(params.DERIVED, 1, 1), "color": "#888888", "process": "mill"}
'''

WRITE_PART_LIST = '''
from build123d import Box
from parts._reg import HOLES
{TICK}
def build():
    _tick("p_wb")
    return {"name": "p_wb", "shape": Box(1.0 + len(HOLES), 1, 1), "color": "#888888", "process": "mill"}
'''


@pytest.mark.parametrize("case", ["params-write", "module-list-write"])
def test_a_node_that_writes_before_it_calls_its_part_is_reused_on_no_change_runs(tmp_path, case):
    """params-write: wa/__init__.build() does params.DERIVED = params.A * 2, then calls p_wa (reads params.DERIVED).
    module-list-write: wb/__init__.build() does _reg.HOLES.append(3.0), then calls p_wb (takes HOLES from parts/_reg.py).
    The second and third no-change runs rebuild nothing, the part has the right size, and A still rebuilds p_wa."""
    root = Path(tmp_path / "proj")
    cad = H.write_base(root, "A = 10.0\nDERIVED = 1.0\n")
    (cad / "parts" / "_reg.py").write_text("HOLES = []\n")
    if case == "params-write":
        node, pid, want = "wa", "p_wa", 20.0
        H.write_node(root, node, {pid: WRITE_PART_PARAM})
        _node_init(root, node, "params.DERIVED = params.A * 2", "import params\n")
    else:
        node, pid, want = "wb", "p_wb", 2.0
        H.write_node(root, node, {pid: WRITE_PART_LIST})
        _node_init(root, node, "_reg.HOLES.append(3.0)", "from parts import _reg\n")
    (cad / "assemble.py").write_text(ASSEMBLE.replace("@MODS@", node).replace("@CALLS@", f"out += {node}.build()"))
    H.run_model(root).ok()
    for n in (2, 3):
        H.clear_ticks(root)
        r = H.run_model(root).ok()
        assert r.summary[0] == 0 and H.ticks(root) == [], \
            f"{case}: no-change run {n} must rebuild nothing: nodes rebuilt {r.summary[0]}, {sorted(r.rebuilt)}"
    assert _x(root, pid) == pytest.approx(want, abs=1e-6), f"{case}: {pid} must be {want} wide"
    if case == "params-write":
        H.clear_ticks(root)
        H.edit(cad / "params.py", "A = 10.0", "A = 15.0")
        r = H.run_model(root).ok()
        assert pid in H.ticks(root), f"DERIVED = A * 2: p_wa must rebuild, rebuilt {sorted(r.rebuilt)}"
        assert _x(root, pid) == pytest.approx(30.0, abs=1e-6)


# ================================================================ a module imported INSIDE build(): no rebuild on every run
INNER_HELPER_PART = '''
from build123d import Box
{TICK}
def build():
    from parts._lay import W
    _tick("p_in")
    return {"name": "p_in", "shape": Box(W, 1, 1), "color": "#888888", "process": "mill"}
'''

INNER_NP_PART = '''
from build123d import Box
{TICK}
def build():
    from parts.inn.params import W
    _tick("p_in")
    return {"name": "p_in", "shape": Box(W, 1, 1), "color": "#888888", "process": "mill"}
'''


@pytest.mark.parametrize("case", ["project-helper", "node-params"])
def test_a_module_imported_inside_build_does_not_rebuild_the_part_on_every_run(tmp_path, case):
    """project-helper: p_in.py does `from parts._lay import W` inside build(), W = float(params.GW). node-params: p_in.py does
    `from parts.inn.params import W` inside build(). Two no-change runs after the cold build rebuild nothing; editing the value
    rebuilds p_in to the new size."""
    root = Path(tmp_path / "proj")
    cad = H.write_base(root, "GW = 10.0\n")
    if case == "project-helper":
        (cad / "parts" / "_lay.py").write_text("import params\n\nW = float(params.GW)\n")
        H.write_node(root, "inn", {"p_in": INNER_HELPER_PART})
        edit = (cad / "params.py", "GW = 10.0", "GW = 20.0")
    else:
        H.write_node(root, "inn", {"p_in": INNER_NP_PART})
        (cad / "parts" / "inn" / "params.py").write_text("W = 10.0\n")
        edit = (cad / "parts" / "inn" / "params.py", "W = 10.0", "W = 20.0")
    (cad / "assemble.py").write_text(ASSEMBLE.replace("@MODS@", "inn").replace("@CALLS@", "out += inn.build()"))
    H.run_model(root).ok()
    assert _x(root, "p_in") == pytest.approx(10.0, abs=1e-6)
    for n in (1, 2):
        H.clear_ticks(root)
        r = H.run_model(root).ok()
        assert r.summary[0] == 0 and H.ticks(root) == [], \
            f"{case}: no-change run {n} must rebuild nothing: nodes rebuilt {r.summary[0]}, {sorted(r.rebuilt)}"
    H.clear_ticks(root)
    H.edit(*edit)
    r = H.run_model(root).ok()
    assert "p_in" in H.ticks(root), f"{case}: W changed, p_in must rebuild, rebuilt {sorted(r.rebuilt)}"
    assert _x(root, "p_in") == pytest.approx(20.0, abs=1e-6)


# ================================================================ a taken list mutated in place by a node mid-run
SHARED_LIST_PART = '''
from build123d import Box
from parts._reg import HOLES
{TICK}
def build():
    _tick("@PID@")
    return {"name": "@PID@", "shape": Box(1.0 + len(HOLES), 1, 1), "color": "#888888", "process": "mill"}
'''


def test_a_taken_list_appended_to_by_a_later_node_gives_the_clean_build_size(tmp_path):
    """Nodes q (built first) and n both place a part that takes HOLES from parts/_reg.py (size 1 + len(HOLES)). n's __init__ then
    starts appending to HOLES before it calls its part: p_n must come out at the clean-build size (2), not stale at 1."""
    root = Path(tmp_path / "proj")
    cad = H.write_base(root, "UNUSED = 1.0\n")
    (cad / "parts" / "_reg.py").write_text("HOLES = []\n")
    for node in ("q", "n"):
        H.write_node(root, node, {f"p_{node}": SHARED_LIST_PART.replace("@PID@", f"p_{node}")})
        _node_init(root, node, "pass  # APPEND", "from parts import _reg\n")
    (cad / "assemble.py").write_text(ASSEMBLE.replace("@MODS@", "q, n").replace("@CALLS@", "out += q.build()\nout += n.build()"))
    H.run_model(root).ok()
    assert (_x(root, "p_q"), _x(root, "p_n")) == pytest.approx((1.0, 1.0), abs=1e-6)
    H.edit(cad / "parts" / "n" / "__init__.py", "pass  # APPEND", "_reg.HOLES.append(3.0)")
    r = H.run_model(root).ok()
    got = (round(_x(root, "p_q"), 6), round(_x(root, "p_n"), 6))
    clean = _copy(root, tmp_path / "clean")
    shutil.rmtree(clean / "build" / "model")
    H.run_model(clean).ok()
    want = (round(_x(clean, "p_q"), 6), round(_x(clean, "p_n"), 6))
    assert want == (1.0, 2.0), f"precondition: a clean build gives p_q 1, p_n 2, got {want}"
    assert got == want, f"p_n took HOLES after n appended to it: incremental {got} != clean {want} ({sorted(r.rebuilt)})"


# ================================================================ a param value hidden in an Enum member or a bound builtin method
def _size_of(root, pid):
    s = H.step_bbox(Path(root) / H.by_id(root)[pid]["step"]).size
    return (round(s.X, 6), round(s.Y, 6), round(s.Z, 6))


def _clean_size_of(root, dst, pid):
    """The size `pid` gets in a clean build of `root` as it is now: copy the project, drop build/model, build it."""
    clean = _copy(root, dst)
    shutil.rmtree(clean / "build" / "model")
    H.run_model(clean).ok()
    return _size_of(clean, pid)


HIDDEN_LAY = '''import enum
import params as P


class Rail(enum.Enum):
    SHORT = float(P.GW)
    LONG = 100.0


DEFAULT = Rail.SHORT
DIMS = {"w": float(P.GW)}
dim = DIMS.get
'''

HIDDEN_PARTS = {
    # takes an Enum member whose value came from params.GW
    "enum-member": '''
from build123d import Box
from parts._lay import DEFAULT
{TICK}
def build():
    _tick("q_hid")
    return {"name": "q_hid", "shape": Box(DEFAULT.value, 5, 2), "color": "#888888", "process": "mill"}
''',
    # takes a bound builtin method (dict.get) of a dict holding params.GW
    "bound-dict-get": '''
from build123d import Box
from parts import _lay
{TICK}
def build():
    _tick("q_hid")
    return {"name": "q_hid", "shape": Box(_lay.dim("w"), 5, 2), "color": "#888888", "process": "mill"}
''',
}


@pytest.mark.parametrize("case", list(HIDDEN_PARTS))
def test_a_param_hidden_in_an_enum_member_or_a_bound_method_gives_the_clean_build_size(tmp_path, case):
    """parts/_lay.py: `class Rail(enum.Enum): SHORT = float(P.GW)`, DEFAULT = Rail.SHORT, DIMS = {"w": float(P.GW)},
    dim = DIMS.get. enum-member: q_hid.py takes DEFAULT and builds Box(DEFAULT.value, 5, 2); bound-dict-get: q_hid.py builds
    Box(_lay.dim("w"), 5, 2). A no-edit run rebuilds nothing; GW 5 -> 9 must give q_hid the clean-build size (9 wide)."""
    root = Path(tmp_path / "proj")
    cad = H.write_base(root, "GW = 5.0\n")
    (cad / "parts" / "_lay.py").write_text(HIDDEN_LAY)
    H.write_node(root, "hid", {"q_hid": HIDDEN_PARTS[case]})
    (cad / "assemble.py").write_text(ASSEMBLE.replace("@MODS@", "hid").replace("@CALLS@", "out += hid.build()"))
    H.run_model(root).ok()
    assert _size_of(root, "q_hid")[0] == pytest.approx(5.0, abs=1e-6), f"{case}: cold q_hid must be 5 wide"
    H.clear_ticks(root)
    r = H.run_model(root).ok()
    assert r.summary[0] == 0 and H.ticks(root) == [], \
        f"{case}: a no-edit run must rebuild nothing: nodes rebuilt {r.summary[0]}, {sorted(r.rebuilt)}"
    H.clear_ticks(root)
    H.edit(cad / "params.py", "GW = 5.0", "GW = 9.0")
    r = H.run_model(root).ok()
    got = _size_of(root, "q_hid")
    want = _clean_size_of(root, tmp_path / "clean", "q_hid")
    assert want[0] == pytest.approx(9.0, abs=1e-6), f"{case}: precondition: a clean build gives q_hid 9 wide, got {want}"
    assert got == want, f"{case}: GW 5 -> 9: incremental q_hid {got} != clean {want} (ran {H.ticks(root)}, {sorted(r.rebuilt)})"


# ================================================================ a module-level name defined only for some params values
COND_LAY = '''import params as P

W = 5.0
if P.MODE == "big":
    OPT = 3.0
'''

COND_PARTS = {
    # asks for the maybe-missing name with getattr(module, name, default)
    "getattr-default": '''
from build123d import Box
from parts import _lay
{TICK}
def build():
    _tick("q_opt")
    return {"name": "q_opt", "shape": Box(_lay.W, getattr(_lay, "OPT", 1.0), 2), "color": "#888888", "process": "mill"}
''',
    # imports it inside build(), falling back on ImportError
    "try-import": '''
from build123d import Box
{TICK}
def build():
    from parts._lay import W
    try:
        from parts._lay import OPT
    except ImportError:
        OPT = 1.0
    _tick("q_opt")
    return {"name": "q_opt", "shape": Box(W, OPT, 2), "color": "#888888", "process": "mill"}
''',
}


@pytest.mark.parametrize("case", list(COND_PARTS))
def test_a_name_a_module_defines_only_for_some_params_gives_the_clean_build_size(tmp_path, case):
    """parts/_lay.py: W = 5.0; `if P.MODE == "big": OPT = 3.0`. q_opt.py builds Box(W, OPT or 1.0, 2), asking for OPT with
    getattr(_lay, "OPT", 1.0) (getattr-default) or `try: from parts._lay import OPT / except ImportError: OPT = 1.0` inside
    build() (try-import). A no-edit run rebuilds nothing; MODE small -> big must give q_opt the clean-build size (Y = 3)."""
    root = Path(tmp_path / "proj")
    cad = H.write_base(root, 'MODE = "small"\n')
    (cad / "parts" / "_lay.py").write_text(COND_LAY)
    H.write_node(root, "opt", {"q_opt": COND_PARTS[case]})
    (cad / "assemble.py").write_text(ASSEMBLE.replace("@MODS@", "opt").replace("@CALLS@", "out += opt.build()"))
    H.run_model(root).ok()
    assert _size_of(root, "q_opt")[1] == pytest.approx(1.0, abs=1e-6), f"{case}: cold (small) q_opt must be 1 in Y"
    H.clear_ticks(root)
    r = H.run_model(root).ok()
    assert r.summary[0] == 0 and H.ticks(root) == [], \
        f"{case}: a no-edit run must rebuild nothing: nodes rebuilt {r.summary[0]}, {sorted(r.rebuilt)}"
    H.clear_ticks(root)
    H.edit(cad / "params.py", 'MODE = "small"', 'MODE = "big"')
    r = H.run_model(root).ok()
    got = _size_of(root, "q_opt")
    want = _clean_size_of(root, tmp_path / "clean", "q_opt")
    assert want[1] == pytest.approx(3.0, abs=1e-6), f"{case}: precondition: a clean build gives q_opt 3 in Y, got {want}"
    assert got == want, \
        f"{case}: MODE small -> big: incremental q_opt {got} != clean {want} (ran {H.ticks(root)}, {sorted(r.rebuilt)})"


# ================================================================ review 11: a set filled in place, hidden values, import-time calls
def _r11_part(pid, imports, expr):
    """A part file `pid` that builds Box(<expr>, 1, 1) after `imports`; it ticks its id."""
    return (imports + "\n{TICK}\ndef build():\n    _tick(\"" + pid + "\")\n    return {\"name\": \"" + pid
            + "\", \"shape\": Box(" + expr + ", 1, 1), \"color\": \"#888888\", \"process\": \"mill\"}\n")


def _r11_assemble(cad, nodes):
    (cad / "assemble.py").write_text(
        ASSEMBLE.replace("@MODS@", ", ".join(nodes)).replace("@CALLS@", "\n".join(f"out += {n}.build()" for n in nodes)))


# p_w keeps SEEN = set() in its own module and adds to it in build(); p_r (another node) takes len(p_w.SEEN)
OWN_SET_WRITER = '''
from build123d import Box
{TICK}
SEEN = set()
def build():
    _tick("p_w")
    SEEN.add("a")
    return {"name": "p_w", "shape": Box(1, 1, 1), "color": "#888888", "process": "mill"}
'''

OWN_SET_READER = '''
from build123d import Box
from parts.w import p_w
{TICK}
def build():
    _tick("p_r")
    return {"name": "p_r", "shape": Box(1.0 + len(p_w.SEEN), 1, 1), "color": "#888888", "process": "mill"}
'''


def test_a_set_one_part_fills_in_its_own_module_keeps_its_readers_at_the_clean_build_size(tmp_path):
    """Node w: p_w.py holds SEEN = set() and does SEEN.add("a") in build(). Node r (built after w): p_r.py builds
    Box(1 + len(p_w.SEEN), 1, 1). Cold, then two no-edit runs: after each, p_r must be the clean-build size (2 wide)."""
    root = Path(tmp_path / "proj")
    cad = H.write_base(root, "UNUSED = 1.0\n")
    H.write_node(root, "w", {"p_w": OWN_SET_WRITER})
    H.write_node(root, "r", {"p_r": OWN_SET_READER})
    _r11_assemble(cad, ["w", "r"])
    H.run_model(root).ok()
    want = _clean_size_of(root, tmp_path / "clean", "p_r")
    assert want[0] == pytest.approx(2.0, abs=1e-6), f"precondition: a clean build gives p_r 2 wide, got {want}"
    for n in (1, 2):
        H.clear_ticks(root)
        r = H.run_model(root).ok()
        got = _size_of(root, "p_r")
        assert got == want, f"no-edit run {n}: incremental p_r {got} != clean {want} (ran {H.ticks(root)}, {sorted(r.rebuilt)})"


# p_w fills SEEN in parts/_reg.py in place (so it also takes SEEN); p_r takes len(_reg.SEEN)
REG_SET_WRITER = '''
from build123d import Box
from parts import _reg
{TICK}
def build():
    _tick("p_w")
    _reg.SEEN.add("a")
    return {"name": "p_w", "shape": Box(1, 1, 1), "color": "#888888", "process": "mill"}
'''

REG_SET_READER = '''
from build123d import Box
from parts import _reg
{TICK}
def build():
    _tick("p_r")
    return {"name": "p_r", "shape": Box(1.0 + len(_reg.SEEN), 1, 1), "color": "#888888", "process": "mill"}
'''


def test_a_part_that_fills_a_shared_set_in_place_is_not_rebuilt_on_a_no_edit_run(tmp_path):
    """parts/_reg.py: SEEN = set(). Node w: p_w.py does _reg.SEEN.add("a") in build(). Node r: p_r.py builds
    Box(1 + len(_reg.SEEN), 1, 1). After the cold build, a no-edit run must not rebuild p_w (its own add must not change its
    key), and p_r must still be the clean-build size (2 wide)."""
    root = Path(tmp_path / "proj")
    cad = H.write_base(root, "UNUSED = 1.0\n")
    (cad / "parts" / "_reg.py").write_text("SEEN = set()\n")
    H.write_node(root, "w", {"p_w": REG_SET_WRITER})
    H.write_node(root, "r", {"p_r": REG_SET_READER})
    _r11_assemble(cad, ["w", "r"])
    H.run_model(root).ok()
    H.clear_ticks(root)
    r = H.run_model(root).ok()
    assert "p_w" not in H.ticks(root) and "w.p_w.build" not in r.rebuilt, \
        f"a no-edit run must not rebuild p_w: ran {H.ticks(root)}, {sorted(r.rebuilt)}"
    got = _size_of(root, "p_r")
    want = _clean_size_of(root, tmp_path / "clean", "p_r")
    assert want[0] == pytest.approx(2.0, abs=1e-6), f"precondition: a clean build gives p_r 2 wide, got {want}"
    assert got == want, f"no-edit run: incremental p_r {got} != clean {want}"


# a value derived from params.GW reached through a door the reader cannot see as a plain attribute
HIDDEN_DOOR = {
    # PEP 562: the module serves W from a dict through __getattr__
    "module-getattr": ("import params as P\n_SIZES = {'W': float(P.GW)}\n\n\ndef __getattr__(n):\n    try:\n"
                       "        return _SIZES[n]\n    except KeyError:\n        raise AttributeError(n)\n",
                       "from build123d import Box\nfrom parts import _lay", "_lay.W"),
    # a taken function that looks its value up with globals()[name]
    "globals-lookup": ("import params as P\nW_RAIL = float(P.GW)\n\n\ndef dim(n):\n    return globals()[n.upper()]\n",
                       "from build123d import Box\nfrom parts._lay import dim", 'dim("w_rail")'),
    # a taken function returning its own attribute
    "fn-attribute": ("import params as P\n\n\ndef width():\n    return width.base\n\n\nwidth.base = float(P.GW)\n",
                     "from build123d import Box\nfrom parts._lay import width", "width()"),
}


@pytest.mark.parametrize("case", list(HIDDEN_DOOR))
def test_a_param_served_by_module_getattr_globals_or_a_function_attribute_gives_the_clean_build_size(tmp_path, case):
    """parts/_lay.py derives a value from params.GW (5) and serves it through module __getattr__ (module-getattr),
    `globals()[n.upper()]` in a taken function (globals-lookup) or a taken function's own attribute width.base (fn-attribute).
    p_v.py builds Box(<that value>, 1, 1). Cold, a no-edit run, then GW 5 -> 9: p_v must be the clean-build size (9 wide)."""
    lay, imports, expr = HIDDEN_DOOR[case]
    root = Path(tmp_path / "proj")
    cad = H.write_base(root, "GW = 5.0\n")
    (cad / "parts" / "_lay.py").write_text(lay)
    H.write_node(root, "v", {"p_v": _r11_part("p_v", imports, expr)})
    _r11_assemble(cad, ["v"])
    H.run_model(root).ok()
    assert _size_of(root, "p_v")[0] == pytest.approx(5.0, abs=1e-6), f"{case}: cold p_v must be 5 wide"
    H.run_model(root).ok()
    H.clear_ticks(root)
    H.edit(cad / "params.py", "GW = 5.0", "GW = 9.0")
    r = H.run_model(root).ok()
    got = _size_of(root, "p_v")
    want = _clean_size_of(root, tmp_path / "clean", "p_v")
    assert want[0] == pytest.approx(9.0, abs=1e-6), f"{case}: precondition: a clean build gives p_v 9 wide, got {want}"
    assert got == want, f"{case}: GW 5 -> 9: incremental p_v {got} != clean {want} (ran {H.ticks(root)}, {sorted(r.rebuilt)})"


IMPORT_CALL_PA = '''
from build123d import Box
from parts import _lay
{TICK}
def build():
    _tick("pa")
    return {"name": "pa", "shape": Box(_lay.W, 1, 1), "color": "#888888", "process": "mill"}
'''

# pb calls pa's build() when its module is imported, not inside its own build()
IMPORT_CALL_PB = '''
from build123d import Box
from parts._lay import OTHER
from parts.a import pa as _pa
{TICK}
H = _pa.build()["shape"].bounding_box().size.X
def build():
    _tick("pb")
    return {"name": "pb", "shape": Box(H + OTHER * 0, 2, 2), "color": "#888888", "process": "mill"}
'''


def test_a_part_calling_another_parts_build_at_import_time_follows_the_callees_param(tmp_path):
    """parts/_lay.py: W = float(P.GW) (GW 5), OTHER = 1.0. Node a: pa.py builds Box(_lay.W, 1, 1). Node b: pb.py does
    H = pa.build()["shape"]...size.X at module top level and builds Box(H, 2, 2). Cold; edit pb.py's code (2 -> 2.0) and run;
    then GW 5 -> 9 and run: pb must be the clean-build size (9 wide)."""
    root = Path(tmp_path / "proj")
    cad = H.write_base(root, "GW = 5.0\n")
    (cad / "parts" / "_lay.py").write_text("import params as P\nW = float(P.GW)\nOTHER = 1.0\n")
    H.write_node(root, "a", {"pa": IMPORT_CALL_PA})
    H.write_node(root, "b", {"pb": IMPORT_CALL_PB})
    _r11_assemble(cad, ["a", "b"])
    H.run_model(root).ok()
    assert _size_of(root, "pb")[0] == pytest.approx(5.0, abs=1e-6), "cold pb must be 5 wide"
    H.edit(cad / "parts" / "b" / "pb.py", "Box(H + OTHER * 0, 2, 2)", "Box(H + OTHER * 0, 2.0, 2)")
    H.run_model(root).ok()
    H.clear_ticks(root)
    H.edit(cad / "params.py", "GW = 5.0", "GW = 9.0")
    r = H.run_model(root).ok()
    got = _size_of(root, "pb")
    want = _clean_size_of(root, tmp_path / "clean", "pb")
    assert want[0] == pytest.approx(9.0, abs=1e-6), f"precondition: a clean build gives pb 9 wide, got {want}"
    assert got == want, f"GW 5 -> 9: incremental pb {got} != clean {want} (ran {H.ticks(root)}, {sorted(r.rebuilt)})"


REBIND_SET_PART = '''
from build123d import Box
from parts import _reg
{TICK}
def build():
    _tick("p_w")
    _reg.USED = sorted(_reg.USED | {"a"})
    return {"name": "p_w", "shape": Box(1, 1, 1), "color": "#888888", "process": "mill"}
'''


def test_a_set_global_a_part_rebinds_to_a_list_neither_crashes_nor_rebuilds_on_a_no_edit_run(tmp_path):
    """parts/_reg.py: USED = set(). p_w.py does _reg.USED = sorted(_reg.USED | {"a"}) in build(). After the cold build, a
    no-edit run must succeed and rebuild nothing."""
    root = Path(tmp_path / "proj")
    cad = H.write_base(root, "UNUSED = 1.0\n")
    (cad / "parts" / "_reg.py").write_text("USED = set()\n")
    H.write_node(root, "w", {"p_w": REBIND_SET_PART})
    _r11_assemble(cad, ["w"])
    H.run_model(root).ok()
    H.clear_ticks(root)
    r = H.run_model(root)
    assert r.rc == 0 and r.summary, f"the no-edit run must not fail (rc={r.rc}):\n{r.log[-1500:]}"
    assert r.summary[0] == 0 and H.ticks(root) == [], \
        f"a no-edit run must rebuild nothing: nodes rebuilt {r.summary[0]}, {sorted(r.rebuilt)}"


MACHINE_DOOR = {
    # the machine params module read through vars()
    "machine-vars": ("GW = 5.0\n", "import params as P", 'vars(P)["GW"]', ("GW = 5.0", "GW = 9.0")),
    # a machine param holding an Enum member whose value is edited
    "machine-enum": ("import enum\n\n\nclass Rail(enum.Enum):\n    SHORT = 5.0\n    LONG = 100.0\n\n\nRAIL = Rail.SHORT\n",
                     "import params as P", "P.RAIL.value", ("SHORT = 5.0", "SHORT = 9.0")),
}


@pytest.mark.parametrize("case", list(MACHINE_DOOR))
def test_a_machine_param_read_through_vars_or_an_enum_member_gives_the_clean_build_size(tmp_path, case):
    """p_v.py builds Box(<value>, 1, 1) from the machine params: vars(P)["GW"] with GW = 5.0 (machine-vars), or P.RAIL.value
    with RAIL = Rail.SHORT and an Enum whose SHORT = 5.0 (machine-enum). Edit that 5.0 to 9.0: p_v must be the clean-build
    size (9 wide)."""
    params_src, imports, expr, (old, new) = MACHINE_DOOR[case]
    root = Path(tmp_path / "proj")
    cad = H.write_base(root, params_src)
    H.write_node(root, "v", {"p_v": _r11_part("p_v", "from build123d import Box\n" + imports, expr)})
    _r11_assemble(cad, ["v"])
    H.run_model(root).ok()
    assert _size_of(root, "p_v")[0] == pytest.approx(5.0, abs=1e-6), f"{case}: cold p_v must be 5 wide"
    H.clear_ticks(root)
    H.edit(cad / "params.py", old, new)
    r = H.run_model(root).ok()
    got = _size_of(root, "p_v")
    want = _clean_size_of(root, tmp_path / "clean", "p_v")
    assert want[0] == pytest.approx(9.0, abs=1e-6), f"{case}: precondition: a clean build gives p_v 9 wide, got {want}"
    assert got == want, f"{case}: 5 -> 9: incremental p_v {got} != clean {want} (ran {H.ticks(root)}, {sorted(r.rebuilt)})"


# ================================================================ review 12: values behind methods, caches, patched classes, objects
# parts/<module>.py sources derived from params.GW (5), the imports p_v.py takes and the expression it builds Box(<expr>, 1, 1) from
R12_GW_DOOR = {
    # an Enum member whose method multiplies its value by a module global derived from GW
    "enum-method": ({"_lay": "import enum\nimport params as P\nK = float(P.GW)\n\n\nclass Rail(enum.Enum):\n    SHORT = 1.0\n\n"
                             "    def width(self):\n        return self.value * K\n"},
                    "from build123d import Box\nfrom parts._lay import Rail", "Rail.SHORT.width()"),
    # a NamedTuple instance whose property multiplies a field by a module global derived from GW
    "namedtuple-property": ({"_lay": "from typing import NamedTuple\nimport params as P\nK = float(P.GW)\n\n\n"
                                     "class Dim(NamedTuple):\n    a: float\n\n    @property\n    def w(self):\n"
                                     "        return self.a * K\n\n\nD = Dim(1.0)\n"},
                            "from build123d import Box\nfrom parts._lay import D", "D.w"),
    # parts/_lay.py patches a class attribute of ANOTHER module (_base.Cfg.W) at import time; p_v reads Cfg.W from _base
    "class-patch": ({"_base": "class Cfg:\n    W = 1.0\n",
                     "_lay": "import params as P\nfrom parts._base import Cfg\nCfg.W = float(P.GW)\nOTHER = 1.0\n"},
                    "from build123d import Box\nfrom parts._base import Cfg\nfrom parts._lay import OTHER", "Cfg.W * OTHER"),
    # an lru_cache function already called once at import time (the cache is warm before any part runs)
    "lru-warm": ({"_lay": "import functools\nimport params as P\n\n\n@functools.lru_cache(maxsize=None)\ndef width():\n"
                          "    return float(P.GW)\n\n\nW0 = width()\n"},
                 "from build123d import Box\nfrom parts import _lay", "_lay.width()"),
}


@pytest.mark.parametrize("case", list(R12_GW_DOOR))
def test_a_param_behind_an_enum_method_a_namedtuple_property_a_patched_class_or_a_warm_lru_cache_gives_the_clean_build_size(
        tmp_path, case):
    """A value derived from params.GW (5) reaches p_v.py's Box(<value>, 1, 1) through an Enum member's method (enum-method), a
    NamedTuple property (namedtuple-property), a class attribute that parts/_lay.py patches on parts/_base.py's class at import
    time (class-patch), or an lru_cache function already called at import time (lru-warm). Cold, a no-edit run, then
    GW 5 -> 9: p_v must be the clean-build size (9 wide)."""
    modules, imports, expr = R12_GW_DOOR[case]
    root = Path(tmp_path / "proj")
    cad = H.write_base(root, "GW = 5.0\n")
    for name, src in modules.items():
        (cad / "parts" / f"{name}.py").write_text(src)
    H.write_node(root, "v", {"p_v": _r11_part("p_v", imports, expr)})
    _r11_assemble(cad, ["v"])
    H.run_model(root).ok()
    assert _size_of(root, "p_v")[0] == pytest.approx(5.0, abs=1e-6), f"{case}: cold p_v must be 5 wide"
    H.run_model(root).ok()
    H.clear_ticks(root)
    H.edit(cad / "params.py", "GW = 5.0", "GW = 9.0")
    r = H.run_model(root).ok()
    got = _size_of(root, "p_v")
    want = _clean_size_of(root, tmp_path / "clean", "p_v")
    assert want[0] == pytest.approx(9.0, abs=1e-6), f"{case}: precondition: a clean build gives p_v 9 wide, got {want}"
    assert got == want, f"{case}: GW 5 -> 9: incremental p_v {got} != clean {want} (ran {H.ticks(root)}, {sorted(r.rebuilt)})"


# an object whose __getattr__ raises KeyError (not AttributeError) for a name it does not hold
KEYERROR_GETATTR_CLASS = ("class Cfg:\n    def __init__(self):\n        self._d = {'w': 5.0}\n\n"
                          "    def __getattr__(self, k):\n        return self._d[k]\n")
KEYERROR_GETATTR_HOME = {
    # the class and its instance in one project module
    "same-module": {"_cfg": KEYERROR_GETATTR_CLASS + "\n\nCFG = Cfg()\n"},
    # the class in parts/_base.py, the instance held by parts/_cfg.py
    "other-module": {"_base": KEYERROR_GETATTR_CLASS, "_cfg": "from parts._base import Cfg\nCFG = Cfg()\n"},
}


@pytest.mark.parametrize("home", list(KEYERROR_GETATTR_HOME))
def test_an_object_whose_getattr_raises_keyerror_neither_crashes_the_build_nor_hides_a_param(tmp_path, home):
    """parts/_cfg.py holds CFG = Cfg(), where Cfg.__getattr__ returns self._d[k] and so raises KeyError (not AttributeError)
    for any other name; Cfg is defined in _cfg.py itself (same-module) or in parts/_base.py (other-module). p_v.py builds
    Box(CFG.w * P.GW / 5.0, 1, 1). The cold build must succeed; after a no-edit run and GW 5 -> 9, p_v must be the clean-build
    size (9 wide)."""
    root = Path(tmp_path / "proj")
    cad = H.write_base(root, "GW = 5.0\n")
    for name, src in KEYERROR_GETATTR_HOME[home].items():
        (cad / "parts" / f"{name}.py").write_text(src)
    H.write_node(root, "v", {"p_v": _r11_part("p_v", "from build123d import Box\nimport params as P\nfrom parts._cfg import CFG",
                                               "CFG.w * P.GW / 5.0")})
    _r11_assemble(cad, ["v"])
    r = H.run_model(root)
    assert r.rc == 0 and r.summary, f"{home}: the cold build must not fail (rc={r.rc}):\n{r.log[-1500:]}"
    assert _size_of(root, "p_v")[0] == pytest.approx(5.0, abs=1e-6), f"{home}: cold p_v must be 5 wide"
    H.run_model(root).ok()
    H.clear_ticks(root)
    H.edit(cad / "params.py", "GW = 5.0", "GW = 9.0")
    r = H.run_model(root)
    assert r.rc == 0 and r.summary, f"{home}: the GW 5 -> 9 run must not fail (rc={r.rc}):\n{r.log[-1500:]}"
    got = _size_of(root, "p_v")
    want = _clean_size_of(root, tmp_path / "clean", "p_v")
    assert want[0] == pytest.approx(9.0, abs=1e-6), f"{home}: precondition: a clean build gives p_v 9 wide, got {want}"
    assert got == want, f"{home}: GW 5 -> 9: incremental p_v {got} != clean {want} (ran {H.ticks(root)}, {sorted(r.rebuilt)})"


# node v's build() sets _lay.SCALE before it calls p_v.build(); p_v reads SCALE only through _lay.width()
NODE_WRITES_SCALE_INIT = '''from build123d import Pos
from geom import place
from parts import _lay
from . import p_v
{TICK}
def build():
    _tick("node_v")
    _lay.SCALE = 2.0
    return [place(p_v.build(), Pos(0, 0, 0))]
'''


def test_a_module_global_a_node_writes_before_calling_its_part_does_not_rebuild_on_no_edit_runs(tmp_path):
    """parts/_lay.py: SCALE = 1.0; def width(): return SCALE * 3. Node v's build() does `_lay.SCALE = 2.0` and then calls
    p_v.build(), which builds Box(_lay.width(), 1, 1). After the cold build, two no-edit runs must each rebuild nothing, and
    p_v must be the clean-build size (6 wide)."""
    root = Path(tmp_path / "proj")
    cad = H.write_base(root, "GW = 5.0\n")
    (cad / "parts" / "_lay.py").write_text("SCALE = 1.0\n\n\ndef width():\n    return SCALE * 3\n")
    H.write_node(root, "v", {"p_v": _r11_part("p_v", "from build123d import Box\nfrom parts import _lay", "_lay.width()")})
    (cad / "parts" / "v" / "__init__.py").write_text(NODE_WRITES_SCALE_INIT.replace("{TICK}", H.TICK))
    _r11_assemble(cad, ["v"])
    H.run_model(root).ok()
    for n in (1, 2):
        H.clear_ticks(root)
        r = H.run_model(root).ok()
        assert r.summary[0] == 0 and H.ticks(root) == [], \
            f"no-edit run {n} must rebuild nothing: nodes rebuilt {r.summary[0]}, ran {H.ticks(root)}, {sorted(r.rebuilt)}"
    want = _clean_size_of(root, tmp_path / "clean", "p_v")
    assert want[0] == pytest.approx(6.0, abs=1e-6), f"precondition: a clean build gives p_v 6 wide, got {want}"
    assert _size_of(root, "p_v") == want, f"incremental p_v {_size_of(root, 'p_v')} != clean {want}"


# a callable object whose dict fills in place on every call; width() calls it, so the memo grows during the run
MEMO_LAY = '''import params as P


class Memo:
    def __init__(self):
        self.items = {}

    def __call__(self, k):
        return self.items.setdefault(k, len(k))


M = Memo()


def width():
    return float(P.GW) + M("ab") * 0
'''


def test_a_memo_object_filled_during_the_run_neither_rebuilds_on_a_no_edit_run_nor_hides_a_param(tmp_path):
    """parts/_lay.py holds M = Memo(), a callable that fills self.items in place, and width() = float(P.GW) + M("ab") * 0.
    p_v.py builds Box(_lay.width(), 1, 1). After the cold build a no-edit run rebuilds nothing (the memo filling during the
    run must not change p_v's key); then GW 5 -> 9: p_v must be the clean-build size (9 wide)."""
    root = Path(tmp_path / "proj")
    cad = H.write_base(root, "GW = 5.0\n")
    (cad / "parts" / "_lay.py").write_text(MEMO_LAY)
    H.write_node(root, "v", {"p_v": _r11_part("p_v", "from build123d import Box\nfrom parts import _lay", "_lay.width()")})
    _r11_assemble(cad, ["v"])
    H.run_model(root).ok()
    assert _size_of(root, "p_v")[0] == pytest.approx(5.0, abs=1e-6), "cold p_v must be 5 wide"
    H.clear_ticks(root)
    r = H.run_model(root).ok()
    assert r.summary[0] == 0 and H.ticks(root) == [], \
        f"a no-edit run must rebuild nothing: nodes rebuilt {r.summary[0]}, ran {H.ticks(root)}, {sorted(r.rebuilt)}"
    H.clear_ticks(root)
    H.edit(cad / "params.py", "GW = 5.0", "GW = 9.0")
    r = H.run_model(root).ok()
    got = _size_of(root, "p_v")
    want = _clean_size_of(root, tmp_path / "clean", "p_v")
    assert want[0] == pytest.approx(9.0, abs=1e-6), f"precondition: a clean build gives p_v 9 wide, got {want}"
    assert got == want, f"GW 5 -> 9: incremental p_v {got} != clean {want} (ran {H.ticks(root)}, {sorted(r.rebuilt)})"


# a compiled regex and a build123d Pos derived from GW, both held in a project module; the Pos is reached through a helper
REGEX_POS_LAY = '''import re
from build123d import Pos
import params as P

PAT = re.compile("a+")
AT = Pos(0, 0, float(P.GW))


def at():
    return AT
'''

REGEX_POS_PART = '''
from build123d import Box
from parts import _lay
{TICK}
def build():
    _tick("p_v")
    n = len(_lay.PAT.match("aaa").group(0))
    return {"name": "p_v", "shape": Box(_lay.at().position.Z, n, 1), "color": "#888888", "process": "mill"}
'''


def test_a_part_using_a_compiled_regex_and_a_pos_from_a_project_module_is_reused_and_follows_the_param(tmp_path):
    """parts/_lay.py: PAT = re.compile("a+"), AT = Pos(0, 0, float(P.GW)), def at(): return AT. p_v.py builds
    Box(_lay.at().position.Z, len(_lay.PAT.match("aaa").group(0)), 1) (5 x 3 x 1). After the cold build a no-edit run rebuilds
    nothing; then GW 5 -> 9: p_v must be the clean-build size (9 x 3 x 1)."""
    root = Path(tmp_path / "proj")
    cad = H.write_base(root, "GW = 5.0\n")
    (cad / "parts" / "_lay.py").write_text(REGEX_POS_LAY)
    H.write_node(root, "v", {"p_v": REGEX_POS_PART})
    _r11_assemble(cad, ["v"])
    H.run_model(root).ok()
    assert _size_of(root, "p_v") == pytest.approx((5.0, 3.0, 1.0), abs=1e-6), "cold p_v must be 5 x 3 x 1"
    H.clear_ticks(root)
    r = H.run_model(root).ok()
    assert r.summary[0] == 0 and H.ticks(root) == [], \
        f"a no-edit run must rebuild nothing: nodes rebuilt {r.summary[0]}, ran {H.ticks(root)}, {sorted(r.rebuilt)}"
    H.clear_ticks(root)
    H.edit(cad / "params.py", "GW = 5.0", "GW = 9.0")
    r = H.run_model(root).ok()
    got = _size_of(root, "p_v")
    want = _clean_size_of(root, tmp_path / "clean", "p_v")
    assert want == pytest.approx((9.0, 3.0, 1.0), abs=1e-6), f"precondition: a clean build gives p_v 9 x 3 x 1, got {want}"
    assert got == want, f"GW 5 -> 9: incremental p_v {got} != clean {want} (ran {H.ticks(root)}, {sorted(r.rebuilt)})"


# over-keying guard: pa takes its shape from _lay.box() (behind GW), pb reads _other.H (behind GH)
ISOLATION_PA = '''
from parts import _lay
{TICK}
def build():
    _tick("pa")
    return {"name": "pa", "shape": _lay.box(), "color": "#888888", "process": "mill"}
'''

ISOLATION_PB = '''
from build123d import Box
from parts import _other
{TICK}
def build():
    _tick("pb")
    return {"name": "pb", "shape": Box(_other.H, 1, 1), "color": "#888888", "process": "mill"}
'''


def test_editing_a_param_one_module_derives_does_not_rebuild_a_part_that_only_uses_another_modules_param(tmp_path):
    """params: GW = 5, GH = 7. parts/_lay.py: W = float(P.GW); def box(): return Box(W, 1, 1). parts/_other.py:
    H = float(P.GH). Node a: pa.py returns _lay.box(). Node b: pb.py builds Box(_other.H, 1, 1). GH 7 -> 8 must rebuild pb
    (to the clean-build size, 8 wide) and must NOT rebuild pa."""
    root = Path(tmp_path / "proj")
    cad = H.write_base(root, "GW = 5.0\nGH = 7.0\n")
    (cad / "parts" / "_lay.py").write_text(
        "from build123d import Box\nimport params as P\n\nW = float(P.GW)\n\n\ndef box():\n    return Box(W, 1, 1)\n")
    (cad / "parts" / "_other.py").write_text("import params as P\n\nH = float(P.GH)\n")
    H.write_node(root, "a", {"pa": ISOLATION_PA})
    H.write_node(root, "b", {"pb": ISOLATION_PB})
    _r11_assemble(cad, ["a", "b"])
    H.run_model(root).ok()
    H.run_model(root).ok()
    H.clear_ticks(root)
    H.edit(cad / "params.py", "GH = 7.0", "GH = 8.0")
    r = H.run_model(root).ok()
    ran = H.ticks(root)
    assert "pb" in ran, f"GH 7 -> 8 must rebuild pb, which reads _other.H: ran {ran}, {sorted(r.rebuilt)}"
    assert "pa" not in ran and "a.pa.build" not in r.rebuilt, \
        f"GH 7 -> 8 must not rebuild pa, which only uses GW through _lay.box(): ran {ran}, {sorted(r.rebuilt)}"
    want = _clean_size_of(root, tmp_path / "clean", "pb")
    assert want[0] == pytest.approx(8.0, abs=1e-6), f"precondition: a clean build gives pb 8 wide, got {want}"
    assert _size_of(root, "pb") == want, f"GH 7 -> 8: incremental pb {_size_of(root, 'pb')} != clean {want}"


# ================================================================ review 13: a param behind a library decorator, an enum member's or a
# library class's own attribute, a setup module patching a shared object, or a module patching build123d itself
R13_GW_DOOR = {
    # a contextlib.contextmanager generator in a project module yields a module global derived from GW
    "contextmanager": ({"_lay": "import contextlib\nimport params as P\nW = float(P.GW)\nOTHER = 1.0\n\n\n"
                                "@contextlib.contextmanager\ndef width():\n    yield W\n"},
                       "from build123d import Box\nfrom parts import _lay\n\n\ndef _ctx():\n    with _lay.width() as w:\n"
                       "        return w\n", "_ctx()"),
    # a functools.singledispatch function multiplies its argument by a module global derived from GW
    "singledispatch": ({"_lay": "import functools\nimport params as P\nW = float(P.GW)\n\n\n@functools.singledispatch\n"
                                "def width(x):\n    return W * x\n"},
                       "from build123d import Box\nfrom parts._lay import width", "width(1.0)"),
    # an Enum member's own attribute, set in the member's __init__ from a params read
    "enum-attr": ({"_lay": "import enum\nimport params as P\n\n\nclass Rail(enum.Enum):\n    SHORT = 1\n\n"
                           "    def __init__(self, v):\n        self.width = float(P.GW) * v\n"},
                  "from build123d import Box\nfrom parts._lay import Rail", "Rail.SHORT.width"),
    # an Enum member's own attribute, patched onto the member at module top level from a params read
    "enum-attr-patch": ({"_lay": "import enum\nimport params as P\n\n\nclass Rail(enum.Enum):\n    SHORT = 1\n\n\n"
                                 "Rail.SHORT.width = float(P.GW)\n"},
                        "from build123d import Box\nfrom parts._lay import Rail", "Rail.SHORT.width"),
    # an instance of a namedtuple subclass gets an attribute set at module top level from a params read
    "nt-subclass-attr": ({"_lay": "import collections\nimport params as P\n\n\n"
                                  "class Dim(collections.namedtuple('Dim', 'a')):\n    pass\n\n\nD = Dim(1.0)\n"
                                  "D.w = float(P.GW)\n"},
                         "from build123d import Box\nfrom parts._lay import D", "D.w"),
    # parts/_setup.py patches CFG.w (an instance held by parts/_lay.py) at import; p_v imports _setup only for OTHER
    "setup-in-part-closure": ({"_lay": "class Cfg:\n    w = 1.0\n\n\nCFG = Cfg()\n",
                               "_setup": "import params as P\nfrom parts._lay import CFG\nCFG.w = float(P.GW)\nOTHER = 1.0\n"},
                              "from build123d import Box\nfrom parts._setup import OTHER\nfrom parts._lay import CFG",
                              "CFG.w * OTHER"),
    # a project module sets a standard-library class's attribute (textwrap.TextWrapper.width) at import time
    "libclass-attr": ({"_lay": "import textwrap\nimport params as P\ntextwrap.TextWrapper.width = int(P.GW)\nOTHER = 1.0\n"},
                      "from build123d import Box\nimport textwrap\nfrom parts._lay import OTHER",
                      "float(textwrap.TextWrapper.width) * OTHER"),
    # a project module patches build123d itself (build123d.Box.default_w) at import time; p_v takes only OTHER from it
    "build123d-patch": ({"_patch": "import build123d\nimport params as P\nbuild123d.Box.default_w = float(P.GW)\nOTHER = 1.0\n"},
                        "from build123d import Box\nimport build123d\nfrom parts._patch import OTHER",
                        "build123d.Box.default_w * OTHER"),
}


@pytest.mark.parametrize("case", list(R13_GW_DOOR))
def test_a_param_behind_a_library_decorator_an_enum_or_library_attribute_or_a_patching_module_gives_the_clean_build_size(
        tmp_path, case):
    """A value derived from params.GW (5) reaches p_v.py's Box(<value>, 1, 1) through: a contextlib.contextmanager
    (contextmanager); a functools.singledispatch function (singledispatch); an Enum member attribute set in __init__ (enum-attr)
    or patched on at import (enum-attr-patch); an attribute set on a namedtuple-subclass instance (nt-subclass-attr); a setup
    module that patches an object of another module and that p_v imports only for OTHER (setup-in-part-closure); a stdlib class
    attribute a project module sets (libclass-attr); or a build123d class attribute a project module sets (build123d-patch).
    Cold (5 wide), a no-edit run that rebuilds nothing, then GW 5 -> 9: p_v must be the clean-build size (9 wide)."""
    modules, imports, expr = R13_GW_DOOR[case]
    root = Path(tmp_path / "proj")
    cad = H.write_base(root, "GW = 5.0\n")
    for name, src in modules.items():
        (cad / "parts" / f"{name}.py").write_text(src)
    H.write_node(root, "v", {"p_v": _r11_part("p_v", imports, expr)})
    _r11_assemble(cad, ["v"])
    H.run_model(root).ok()
    assert _size_of(root, "p_v")[0] == pytest.approx(5.0, abs=1e-6), f"{case}: cold p_v must be 5 wide"
    H.clear_ticks(root)
    r = H.run_model(root).ok()
    assert r.summary[0] == 0 and H.ticks(root) == [], \
        f"{case}: a no-edit run must rebuild nothing: nodes rebuilt {r.summary[0]}, {sorted(r.rebuilt)}"
    H.clear_ticks(root)
    H.edit(cad / "params.py", "GW = 5.0", "GW = 9.0")
    r = H.run_model(root).ok()
    got = _size_of(root, "p_v")
    want = _clean_size_of(root, tmp_path / "clean", "p_v")
    assert want[0] == pytest.approx(9.0, abs=1e-6), f"{case}: precondition: a clean build gives p_v 9 wide, got {want}"
    assert got == want, f"{case}: GW 5 -> 9: incremental p_v {got} != clean {want} (ran {H.ticks(root)}, {sorted(r.rebuilt)})"


# ================================================================ review 14: a module patching build123d through a helper, an alias, a
# loop or a star import; a node package constant taken while the package is still importing; a memo filled during the build
_R14_PATCH_IMPORTS = "from build123d import Box\nimport build123d\nfrom parts._patch import OTHER"
_R14_PKG_INIT = ("import params as P\nfrom build123d import Pos\nfrom geom import place\nSPAN = float(P.GW)\nOTHER = 1.0\n"
                 "from . import p_v\n{TICK}\ndef build():\n    _tick('node_v')\n    return [place(p_v.build(), Pos(0, 0, 0))]\n")
R14_GW_DOOR = {
    # parts/_patch.py sets build123d.Box.default_w = float(P.GW) inside a helper function it calls at import
    "patch-fn": ({"_patch": "import build123d\nimport params as P\n\n\ndef _install():\n"
                            "    build123d.Box.default_w = float(P.GW)\n\n\n_install()\nOTHER = 1.0\n"},
                 _R14_PATCH_IMPORTS, "build123d.Box.default_w * OTHER"),
    # ... through a local alias of the class (B = build123d.Box; B.default_w = ...)
    "patch-alias": ({"_patch": "import build123d\nimport params as P\nB = build123d.Box\nB.default_w = float(P.GW)\n"
                               "OTHER = 1.0\n"},
                    _R14_PATCH_IMPORTS, "build123d.Box.default_w * OTHER"),
    # ... in a loop over build123d classes
    "patch-loop": ({"_patch": "import build123d\nimport params as P\nfor _c in (build123d.Box, build123d.Cylinder):\n"
                              "    _c.default_w = float(P.GW)\nOTHER = 1.0\n"},
                   _R14_PATCH_IMPORTS, "build123d.Box.default_w * OTHER"),
    # ... on a name it got from `from build123d import *`
    "patch-star": ({"_patch": "from build123d import *\nimport params as P\nBox.default_w = float(P.GW)\nOTHER = 1.0\n"},
                   _R14_PATCH_IMPORTS, "build123d.Box.default_w * OTHER"),
    # node v's __init__.py defines SPAN = float(P.GW) and OTHER before importing p_v; p_v takes SPAN with `from . import SPAN`
    # while the package is still importing, and also takes the package as V for V.OTHER
    "pkg-partial": ({"v/__init__": _R14_PKG_INIT},
                    "from build123d import Box\nfrom . import SPAN\nimport parts.v as V", "SPAN * V.OTHER"),
    # control for pkg-partial: p_v takes only SPAN from the half-imported package
    "pkg-partial-ctrl": ({"v/__init__": _R14_PKG_INIT}, "from build123d import Box\nfrom . import SPAN", "SPAN"),
}


def _r14_project(root, modules, imports, expr):
    """mechanics/cad with GW = 5.0, node v holding p_v (Box(<expr>, 1, 1) after `imports`), and `modules` {parts-relative
    name: source} written over it ({TICK} allowed, so "v/__init__" replaces the node's package file)."""
    cad = H.write_base(root, "GW = 5.0\n")
    H.write_node(root, "v", {"p_v": _r11_part("p_v", imports, expr)})
    for name, src in modules.items():
        (cad / "parts" / f"{name}.py").write_text(src.replace("{TICK}", H.TICK))
    _r11_assemble(cad, ["v"])
    return cad


@pytest.mark.parametrize("case", list(R14_GW_DOOR))
def test_a_param_behind_a_build123d_patch_in_a_helper_alias_loop_or_star_import_or_a_half_imported_node_package_gives_the_clean_build_size(
        tmp_path, case):
    """A value derived from params.GW (5) reaches p_v.py's Box(<value>, 1, 1) through: a project module that sets
    build123d.Box.default_w inside a helper it calls at import (patch-fn), through a local alias of the class (patch-alias), in a
    loop over build123d classes (patch-loop) or on the Box name of `from build123d import *` (patch-star), p_v importing that
    module only for OTHER; or node v's own __init__.py constant SPAN that p_v takes while the package is still importing, with
    (pkg-partial) or without (pkg-partial-ctrl) also taking the package itself. Cold (5 wide), a no-edit run that rebuilds
    nothing, then GW 5 -> 9: p_v must be the clean-build size (9 wide)."""
    modules, imports, expr = R14_GW_DOOR[case]
    root = Path(tmp_path / "proj")
    cad = _r14_project(root, modules, imports, expr)
    H.run_model(root).ok()
    assert _size_of(root, "p_v")[0] == pytest.approx(5.0, abs=1e-6), f"{case}: cold p_v must be 5 wide"
    H.clear_ticks(root)
    r = H.run_model(root).ok()
    assert r.summary[0] == 0 and H.ticks(root) == [], \
        f"{case}: a no-edit run must rebuild nothing: nodes rebuilt {r.summary[0]}, {sorted(r.rebuilt)}"
    H.clear_ticks(root)
    H.edit(cad / "params.py", "GW = 5.0", "GW = 9.0")
    r = H.run_model(root).ok()
    got = _size_of(root, "p_v")
    want = _clean_size_of(root, tmp_path / "clean", "p_v")
    assert want[0] == pytest.approx(9.0, abs=1e-6), f"{case}: precondition: a clean build gives p_v 9 wide, got {want}"
    assert got == want, f"{case}: GW 5 -> 9: incremental p_v {got} != clean {want} (ran {H.ticks(root)}, {sorted(r.rebuilt)})"


_R14_MEMO_DICT = ("from build123d import Box\n_CACHE = {}\n\n\ndef plate(w):\n    if w not in _CACHE:\n"
                  "        _CACHE[w] = Box(w, 1, 1)\n    return _CACHE[w]\n")
R14_MEMO = {
    # a module-level dict that plate(w) fills with build123d shapes during the build
    "memo-dict": ({"_lay": _R14_MEMO_DICT}, "from build123d import Box\nimport params as P\nfrom parts._lay import plate",
                  "plate(float(P.GW)).bounding_box().size.X"),
    # ... the same memo held as a mutable default argument
    "memo-default": ({"_lay": "from build123d import Box\n\n\ndef plate(w, _cache={}):\n    if w not in _cache:\n"
                              "        _cache[w] = Box(w, 1, 1)\n    return _cache[w]\n"},
                     "from build123d import Box\nimport params as P\nfrom parts._lay import plate",
                     "plate(float(P.GW)).bounding_box().size.X"),
    # a mutable default argument that memoizes plain floats
    "memo-default-floats": ({"_lay": "def scaled(w, _cache={}):\n    return _cache.setdefault(w, w * 1.0)\n"},
                            "from build123d import Box\nimport params as P\nfrom parts._lay import scaled",
                            "scaled(float(P.GW))"),
    # a class attribute dict a classmethod fills with floats
    "memo-classattr": ({"_lay": "class Dims:\n    _memo = {}\n\n    @classmethod\n    def width(cls, w):\n"
                                "        return cls._memo.setdefault(w, w * 1.0)\n"},
                       "from build123d import Box\nimport params as P\nfrom parts._lay import Dims", "Dims.width(float(P.GW))"),
}


@pytest.mark.parametrize("case", list(R14_MEMO))
def test_a_memo_a_helper_fills_during_the_build_rebuilds_nothing_on_a_no_edit_run_and_follows_the_param(tmp_path, case):
    """parts/_lay.py memoizes what p_v asks it for, filling the memo in place during the build: a module-level dict of build123d
    shapes (memo-dict), the same as a mutable default argument (memo-default), a mutable default of floats
    (memo-default-floats), or a class attribute dict a classmethod fills (memo-classattr). p_v.py passes float(P.GW) (5) in and
    builds Box(<the width it gets back>, 1, 1). Main check: after the cold build a no-edit run rebuilds nothing (the memo having
    grown during the run must not change p_v's key). Then GW 5 -> 9: p_v must be the clean-build size (9 wide)."""
    modules, imports, expr = R14_MEMO[case]
    root = Path(tmp_path / "proj")
    cad = _r14_project(root, modules, imports, expr)
    H.run_model(root).ok()
    assert _size_of(root, "p_v")[0] == pytest.approx(5.0, abs=1e-6), f"{case}: cold p_v must be 5 wide"
    H.clear_ticks(root)
    r = H.run_model(root).ok()
    assert r.summary[0] == 0 and H.ticks(root) == [], \
        f"{case}: a no-edit run must rebuild nothing: nodes rebuilt {r.summary[0]}, ran {H.ticks(root)}, {sorted(r.rebuilt)}"
    H.clear_ticks(root)
    H.edit(cad / "params.py", "GW = 5.0", "GW = 9.0")
    r = H.run_model(root).ok()
    got = _size_of(root, "p_v")
    want = _clean_size_of(root, tmp_path / "clean", "p_v")
    assert want[0] == pytest.approx(9.0, abs=1e-6), f"{case}: precondition: a clean build gives p_v 9 wide, got {want}"
    assert got == want, f"{case}: GW 5 -> 9: incremental p_v {got} != clean {want} (ran {H.ticks(root)}, {sorted(r.rebuilt)})"


# ================================================================ review 15: a singledispatch implementation, a dict whose order a param
# gives, a string-set literal (a frozenset constant) in a helper or in the part's own build()
R15_EDIT = {
    # parts/_lay.py: width() is a functools.singledispatch whose float implementation multiplies by K = float(P.GW); the base
    # function does not name K. p_v builds Box(width(1.0), 1, 1). Edit GW 5 -> 9: clean gives 9 wide
    "singledispatch-impl": ("GW = 5.0\n",
                            {"_lay": "import functools\nimport params as P\nK = float(P.GW)\n\n\n@functools.singledispatch\n"
                                     "def width(x):\n    return x\n\n\n@width.register\ndef _(x: float):\n    return x * K\n"},
                            "from build123d import Box\nfrom parts._lay import width", "width(1.0)",
                            ("GW = 5.0", "GW = 9.0"), 5.0, 9.0),
    # parts/_lay.py builds LAYERS = {n: P.T[n] for n in P.ORDER}; p_v builds the first layer. Edit swaps ORDER: the values are
    # the same, only their order changes, and the clean build gives 1 wide instead of 4
    "take-dict-order": ("ORDER = [\"base\", \"top\"]\nT = {\"base\": 4.0, \"top\": 1.0}\n",
                        {"_lay": "import params as P\nLAYERS = {n: float(P.T[n]) for n in P.ORDER}\n"},
                        "from build123d import Box\nfrom parts._lay import LAYERS", "list(LAYERS.values())[0]",
                        ("ORDER = [\"base\", \"top\"]", "ORDER = [\"top\", \"base\"]"), 4.0, 1.0),
}


@pytest.mark.parametrize("case", list(R15_EDIT))
def test_a_param_behind_a_singledispatch_implementation_or_the_order_of_a_derived_dict_gives_the_clean_build_size(tmp_path, case):
    """A machine param reaches p_v.py's Box(<value>, 1, 1) through a functools.singledispatch implementation registered for float
    that reads a module global derived from GW (singledispatch-impl), or through the ORDER of a dict a helper module builds from
    P.ORDER, the part taking the first value (take-dict-order). Cold build, a no-edit run that rebuilds nothing, then the edit:
    p_v must be the clean-build size."""
    params, modules, imports, expr, (old, new), cold_w, clean_w = R15_EDIT[case]
    root = Path(tmp_path / "proj")
    cad = H.write_base(root, params)
    H.write_node(root, "v", {"p_v": _r11_part("p_v", imports, expr)})
    for name, src in modules.items():
        (cad / "parts" / f"{name}.py").write_text(src.replace("{TICK}", H.TICK))
    _r11_assemble(cad, ["v"])
    H.run_model(root).ok()
    assert _size_of(root, "p_v")[0] == pytest.approx(cold_w, abs=1e-6), f"{case}: cold p_v must be {cold_w} wide"
    H.clear_ticks(root)
    r = H.run_model(root).ok()
    assert r.summary[0] == 0 and H.ticks(root) == [], \
        f"{case}: a no-edit run must rebuild nothing: nodes rebuilt {r.summary[0]}, ran {H.ticks(root)}, {sorted(r.rebuilt)}"
    H.clear_ticks(root)
    H.edit(cad / "params.py", old, new)
    r = H.run_model(root).ok()
    got = _size_of(root, "p_v")
    want = _clean_size_of(root, tmp_path / "clean", "p_v")
    assert want[0] == pytest.approx(clean_w, abs=1e-6), f"{case}: precondition: a clean build gives p_v {clean_w} wide, got {want}"
    assert got == want, f"{case}: {old!r} -> {new!r}: incremental p_v {got} != clean {want} (ran {H.ticks(root)}, {sorted(r.rebuilt)})"


R15_SET_LITERAL = {
    # a helper function in parts/_lay.py tests `kind in {"laser", "bent", "mill", "print"}` (a frozenset constant in its code)
    "helper-fn": ({"_lay": "def width(kind, w):\n    return w if kind in {\"laser\", \"bent\", \"mill\", \"print\"} else 1.0\n"},
                  "from build123d import Box\nimport params as P\nfrom parts._lay import width",
                  "width(\"mill\", float(P.GW))"),
    # the same membership test written inside p_v's own build()
    "own-build": ({}, "from build123d import Box\nimport params as P",
                  "(float(P.GW) if \"mill\" in {\"laser\", \"bent\", \"mill\"} else 1.0)"),
}


@pytest.mark.parametrize("case", list(R15_SET_LITERAL))
def test_a_string_set_literal_in_a_helper_or_the_parts_build_rebuilds_nothing_on_no_edit_runs_and_follows_the_param(
        tmp_path, case):
    """p_v.py builds Box(GW if "mill" in {<string set literal>} else 1, 1, 1), the membership test sitting in a helper function
    of parts/_lay.py (helper-fn) or in p_v's own build() (own-build). Python compiles the literal to a frozenset constant whose
    iteration order depends on the per-process string hash seed, and every --model run is a fresh process. Main check: after the
    cold build, three no-edit runs in a row each rebuild nothing. Then GW 5 -> 9: p_v must be the clean-build size (9 wide)."""
    modules, imports, expr = R15_SET_LITERAL[case]
    root = Path(tmp_path / "proj")
    cad = _r14_project(root, modules, imports, expr)
    H.run_model(root).ok()
    assert _size_of(root, "p_v")[0] == pytest.approx(5.0, abs=1e-6), f"{case}: cold p_v must be 5 wide"
    for i in range(3):
        H.clear_ticks(root)
        r = H.run_model(root).ok()
        assert r.summary[0] == 0 and H.ticks(root) == [], \
            (f"{case}: no-edit run {i + 1} of 3 must rebuild nothing: nodes rebuilt {r.summary[0]}, ran {H.ticks(root)}, "
             f"{sorted(r.rebuilt)}")
    H.clear_ticks(root)
    H.edit(cad / "params.py", "GW = 5.0", "GW = 9.0")
    r = H.run_model(root).ok()
    got = _size_of(root, "p_v")
    want = _clean_size_of(root, tmp_path / "clean", "p_v")
    assert want[0] == pytest.approx(9.0, abs=1e-6), f"{case}: precondition: a clean build gives p_v 9 wide, got {want}"
    assert got == want, f"{case}: GW 5 -> 9: incremental p_v {got} != clean {want} (ran {H.ticks(root)}, {sorted(r.rebuilt)})"


# ================================================================ review 16: an enum's _missing_ reading a module global derived from a param
R16_ENUM_MISSING_LAYOUT = '''import enum
import params as P

LIMIT = 10 if P.BIG else 3


class Size(enum.Enum):
    S = 1
    L = 2

    @classmethod
    def _missing_(cls, v):
        return cls.L if v > LIMIT else cls.S
'''


def test_r16_an_enum_missing_hook_reading_a_param_derived_global_gives_the_clean_build_size(tmp_path):
    """Node v's _layout.py sets LIMIT = 10 if params.BIG else 3 at import and defines enum Size(S = 1, L = 2) whose _missing_
    maps an unknown value to L when it is above LIMIT, else to S. p_v.py builds Box(Size(5).value, 1, 1), so the param reaches
    the part only through the _missing_ hook. Cold (BIG False: Size(5) is L, 2 wide), a no-edit run that rebuilds nothing, then
    BIG False -> True: p_v must be the clean-build size (Size(5) is S, 1 wide)."""
    root = Path(tmp_path / "proj")
    cad = H.write_base(root, "GW = 5.0\nBIG = False\n")
    H.write_node(root, "v", {"p_v": _r11_part("p_v", "from build123d import Box\nfrom ._layout import Size", "Size(5).value")})
    (cad / "parts" / "v" / "_layout.py").write_text(R16_ENUM_MISSING_LAYOUT)
    _r11_assemble(cad, ["v"])
    H.run_model(root).ok()
    assert _size_of(root, "p_v")[0] == pytest.approx(2.0, abs=1e-6), "cold p_v must be 2 wide (Size(5) is L)"
    H.clear_ticks(root)
    r = H.run_model(root).ok()
    assert r.summary[0] == 0 and H.ticks(root) == [], \
        f"a no-edit run must rebuild nothing: nodes rebuilt {r.summary[0]}, ran {H.ticks(root)}, {sorted(r.rebuilt)}"
    H.clear_ticks(root)
    H.edit(cad / "params.py", "BIG = False", "BIG = True")
    r = H.run_model(root).ok()
    got = _size_of(root, "p_v")
    want = _clean_size_of(root, tmp_path / "clean", "p_v")
    assert want[0] == pytest.approx(1.0, abs=1e-6), f"precondition: a clean build gives p_v 1 wide (Size(5) is S), got {want}"
    assert got == want, f"BIG False -> True: incremental p_v {got} != clean {want} (ran {H.ticks(root)}, {sorted(r.rebuilt)})"
