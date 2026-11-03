"""Assembly drawings per node, flat-pack furniture style: derive.py assembly + shop-drawings assembly_drawing.py.

The model is a tiny hand-written build/model/ (parts.json with placements, part joints and placement joints + one STEP per part), so
these tests do not depend on cad_incremental --model and fail only for assembly reasons. The machine:
  «Frame»     base plate (root), a bracket docked on it, 3 washers in its holes, 3 identical screws M5x12 DIN 912 on the washers,
              2 nuts M5 on two of the screws (fasteners are placed by the node «fasteners», as parts/fasteners/ does), a product
              part (an apple, product: true) and a bought row without a shape (a cable): neither may be drawn.
  «Drive»     a motor plate docked on the base's side (another node), a shaft on it, one screw M4x10 and a 4th M5x12 screw in it.
  «Conveyor»  a frame docked on the base, 3 sections on it, 9 rollers on each section: 31 distinct rows, more than one sheet holds.
  «Cabinet»   the electrical cabinet: a mesh-only part (no STEP): never drawn.

Run (the drawings venv: build123d 0.13.0, matplotlib, pymupdf; pytest from the same venv):
    cd agent
    SHOP_DRAW_VENV=<venv> <venv>/bin/python -m pytest skills/machine-project/scripts/tests/test_assembly_drawings.py -q

Contract under test (from the spec, not from the code):
  derive.py --no-rebuild assembly MANIFEST  (cwd = project root) -> MANIFEST {"assembly": [{"node", "files": [<one .pdf>]}
      | {"node", "error", "files": []}], "parts": []}; log `[derive] assembly: parts K, reused R, computed C`; a second run with
      nothing changed computes 0. No PDF for the whole machine; mesh-only parts, rows and product parts are not drawn.
  derive.assembly_nodes(parts) -> {node: [item]}, item has name (placement), part (part id), parent, axis; pure (no build123d).
  A PDF: an exploded view, numbered balloons, the table (No., part, code, qty in the project's language, drawings_i18n `assy.cols`); a fastener row shows its size from its code with
  × between numbers ("M5x12 DIN 912" -> "5×12") and its quantity "×N"; identical fasteners are one row. More than 28 rows ->
  several pages, the step sub-assembly sheets first (`assy.step_head`), the node last.
Test names say which done-when they serve: [1] one PDF per node, [2] balloons + table + fastener size/×N, [3] big node = pages, [4] cache.
"""
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import helpers as H

sys.path.insert(0, str(H.SCRIPTS.parent.parent / "shop-drawings" / "scripts"))
import drawings_i18n as I  # noqa: E402

COLS = I.t("assy.cols")                       # the table header of a Russian project (the default language)
STEP = I.t("assy.step_head", no=1).split()[0]   # the word a step sheet starts with

SCRIPTS = H.SCRIPTS
DERIVE = H.DERIVE
VENV = Path(os.environ.get("SHOP_DRAW_VENV", "/tmp/shopdrawvenv"))
if os.environ.get("SHOP_DRAW_VENV") and not (VENV / "bin" / "python").exists():   # asked for a venv that is not there: a green run
    raise RuntimeError(f"SHOP_DRAW_VENV={VENV} has no bin/python: the drawing tests would all skip")   # would test nothing
needs_venv = pytest.mark.skipif(not (VENV / "bin" / "python").exists(),
                                reason=f"drawings venv not available at {VENV} (set SHOP_DRAW_VENV or run skills/shop-drawings/scripts/drawings_env.sh once)")

FRAME, DRIVE, CONV, CAB = "Frame", "Drive", "Conveyor", "Cabinet"
DRAWN_NODES = {FRAME, DRIVE, CONV}
FASTENER_NODE = "fasteners"
N_SECTIONS, ROLLERS_PER = 3, 9
CONV_ROWS = 1 + N_SECTIONS + N_SECTIONS * ROLLERS_PER      # 31 > 28
MAX_ROWS = 28


# ================================================================ rigid 4x4 matrices, pure python (row-major 16 floats, as parts.json)
def T(x=0.0, y=0.0, z=0.0):
    return [1.0, 0.0, 0.0, float(x), 0.0, 1.0, 0.0, float(y), 0.0, 0.0, 1.0, float(z), 0.0, 0.0, 0.0, 1.0]


def R(axis, deg):
    c, s = round(math.cos(math.radians(deg)), 12), round(math.sin(math.radians(deg)), 12)
    m = {"x": [[1, 0, 0], [0, c, -s], [0, s, c]], "y": [[c, 0, s], [0, 1, 0], [-s, 0, c]], "z": [[c, -s, 0], [s, c, 0], [0, 0, 1]]}[axis]
    return [float(m[0][0]), float(m[0][1]), float(m[0][2]), 0.0, float(m[1][0]), float(m[1][1]), float(m[1][2]), 0.0,
            float(m[2][0]), float(m[2][1]), float(m[2][2]), 0.0, 0.0, 0.0, 0.0, 1.0]


def mul(a, b):
    return [sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4)) for r in range(4) for c in range(4)]


def inv(m):
    rt = [[m[c * 4 + r] for c in range(3)] for r in range(3)]       # R transposed
    t = [m[3], m[7], m[11]]
    nt = [-sum(rt[r][k] * t[k] for k in range(3)) for r in range(3)]
    return [rt[0][0], rt[0][1], rt[0][2], nt[0], rt[1][0], rt[1][1], rt[1][2], nt[1], rt[2][0], rt[2][1], rt[2][2], nt[2], 0.0, 0.0, 0.0, 1.0]


def rigid(loc):
    return {"type": "rigid", "loc": loc}


# ================================================================ the synthetic machine
def model_spec():
    """part id -> {title, process, node, shape: (kind, args) | None, joints {label: own loc}, extra fields,
    placements: [(name, node, parent placement | None, at joint, by joint)]}. World locs are computed from the joint graph."""
    holes = [(20, 15), (50, 15), (80, 15)]
    base_j = {f"hole_{i + 1}": T(x, y, 10) for i, (x, y) in enumerate(holes)}
    base_j.update({f"hole_{i + 1}_bottom": mul(T(x, y, 0), R("x", 180)) for i, (x, y) in enumerate(holes)})
    base_j.update({"bracket_seat": T(50, 45, 10), "drive_seat": mul(T(100, 30, 5), R("y", 90)),
                   "conv_seat": mul(T(50, 60, 5), R("x", -90)), "apple_seat": T(10, 50, 10)})
    S = {
        "base": dict(title="Base plate", process="mill", node=FRAME, shape=("box", (100, 60, 10)), joints=base_j,
                     placements=[("base", FRAME, None, None, None)]),
        "bracket": dict(title="Bracket", process="mill", node=FRAME, shape=("box", (30, 30, 5)), joints={"foot": T(15, 15, 0)},
                        placements=[("bracket", FRAME, "base", "bracket_seat", "foot")]),
        "washer_m5": dict(title="Washer", process="bought", node=FASTENER_NODE, shape=("cyl", (5, 1)),
                          joints={"seat": T(0, 0, 0), "top": T(0, 0, 1)}, extra={"kind": "fasteners", "code": "5 DIN 125"},
                          placements=[(n, FASTENER_NODE, "base", f"hole_{i + 1}", "seat")
                                      for i, n in enumerate(["washer_m5", "washer_m5_2", "washer_m5_3"])]),
        "screw_m5x12": dict(title="Screw", process="bought", node=FASTENER_NODE, shape=("screw", (2.5, 12, 4, 5)),
                            joints={"head": T(0, 0, 0), "tip": mul(T(0, 0, -12), R("x", 180))},
                            extra={"kind": "fasteners", "code": "M5x12 DIN 912"},
                            placements=[(n, FASTENER_NODE, w, "top", "head") for n, w in
                                        [("screw_m5x12", "washer_m5"), ("screw_m5x12_2", "washer_m5_2"), ("screw_m5x12_3", "washer_m5_3")]]
                            + [("screw_m5x12_4", FASTENER_NODE, "motor_plate", "hole_b", "head")]),
        "nut_m5": dict(title="Nut", process="bought", node=FASTENER_NODE, shape=("cyl", (4, 4)), joints={"seat": T(0, 0, 0)},
                       extra={"kind": "fasteners", "code": "M5 DIN 934"},
                       placements=[("nut_m5", FASTENER_NODE, "screw_m5x12", "tip", "seat"),
                                   ("nut_m5_2", FASTENER_NODE, "screw_m5x12_2", "tip", "seat")]),
        "apple": dict(title="Apple", process="other", node=FRAME, shape=("sphere", (8,)), joints={"bottom": T(0, 0, -8)},
                      extra={"product": True}, placements=[("apple", FRAME, "base", "apple_seat", "bottom")]),
        "motor_plate": dict(title="Motor plate", process="laser", node=DRIVE, shape=("box", (50, 50, 4)),
                            joints={"mount": T(25, 25, 0), "shaft_hole": T(25, 25, 4), "hole_a": T(8, 8, 4), "hole_b": T(42, 42, 4)},
                            extra={"material": "S235JR", "thickness": 4.0},
                            placements=[("motor_plate", DRIVE, "base", "drive_seat", "mount")]),
        "shaft": dict(title="Shaft", process="turn", node=DRIVE, shape=("cyl", (5, 40)), joints={"end": T(0, 0, 0)},
                      placements=[("shaft", DRIVE, "motor_plate", "shaft_hole", "end")]),
        "screw_m4x10": dict(title="Screw", process="bought", node=FASTENER_NODE, shape=("screw", (2, 10, 3.5, 4)),
                            joints={"head": T(0, 0, 0)}, extra={"kind": "fasteners", "code": "M4x10 DIN 912"},
                            placements=[("screw_m4x10", FASTENER_NODE, "motor_plate", "hole_a", "head")]),
        "conv_frame": dict(title="Conveyor frame", process="mill", node=CONV, shape=("box", (300, 40, 6)),
                           joints={"mount": T(0, 20, 0), **{f"sec_{k + 1}": T(20 + 95 * k, 20, 6) for k in range(N_SECTIONS)}},
                           placements=[("conv_frame", CONV, "base", "conv_seat", "mount")]),
    }
    for k in range(N_SECTIONS):
        sid = f"section_{k + 1}"
        S[sid] = dict(title=f"Section {k + 1}", process="mill", node=CONV, shape=("box", (80 + k, 30, 4)),
                      joints={"foot": T(0, 15, 0), **{f"r{i + 1}": T(5 + 8 * i, 15, 4) for i in range(ROLLERS_PER)}},
                      placements=[(sid, CONV, "conv_frame", f"sec_{k + 1}", "foot")])
        for i in range(ROLLERS_PER):
            n = k * ROLLERS_PER + i + 1
            rid = f"roller_{n:02d}"
            S[rid] = dict(title=f"Roller {n:02d}", process="turn", node=CONV, shape=("cyl", (2.5, 20 + n)), joints={"end": T(0, 0, 0)},
                          placements=[(rid, CONV, sid, f"r{i + 1}", "end")])
    return S


def roller_titles():
    return [f"Roller {n:02d}" for n in range(1, N_SECTIONS * ROLLERS_PER + 1)]


def world_locs(S):
    """placement name -> world 4x4: child = parent_world * at(parent own) * inverse(by(own))."""
    owner = {pl[0]: pid for pid, s in S.items() for pl in s["placements"]}
    spec = {pl[0]: pl for s in S.values() for pl in s["placements"]}
    out = {}

    def loc(name):
        if name not in out:
            _, _, parent, at, by = spec[name]
            if parent is None:
                out[name] = T()
            else:
                out[name] = mul(mul(loc(parent), S[owner[parent]]["joints"][at]), inv(S[owner[name]]["joints"][by]))
        return out[name]
    for n in spec:
        loc(n)
    return out


def parts_json(S, keys=None):
    """The parts list as build/model/parts.json holds it (no STEP paths unless `keys` {part id: (key, sha)} is given)."""
    W = world_locs(S)
    parts = []
    for pid, s in S.items():
        e = {"id": pid, "name": pid, "title": s["title"], "node": s["node"], "func": f"{s['node']}.build", "process": s["process"],
             "color": "#999999", "key": (keys or {}).get(pid, (f"k-{pid}", None))[0]}
        if keys and pid in keys:
            e.update(sha256=keys[pid][1], step=f"build/model/step/{keys[pid][0]}.step")
        else:                                       # every shaped part of a real parts.json names its STEP (not read here)
            e.update(sha256="0" * 64, step=f"build/model/step/{e['key']}.step")
        e.update(s.get("extra", {}))
        e["joints"] = {lab: rigid(m) for lab, m in s["joints"].items()}
        pls = []
        for name, node, parent, at, by in s["placements"]:
            p = {"name": name, "node": node, "loc": W[name]}
            if parent is not None:
                p["joint"] = {"to": parent, "at": at, "by": by, "type": "rigid", "value": None}
            pls.append(p)
        e["placements"] = pls
        e["qty"] = len(pls)
        parts.append(e)
    # a bought row without a shape (never drawn) and the cabinet: a mesh-only part (never drawn)
    parts.append({"id": "cable", "name": "cable", "title": "Power cable", "node": FRAME, "func": f"{FRAME}.build",
                  "process": "bought", "code": "PVS 3x1.5", "qty": 2, "key": "k-cable"})
    parts.append({"id": "cabinet", "name": "cabinet", "title": "Electrical cabinet", "node": CAB, "func": f"{CAB}.build", "process": "bought",
                  "key": "k-cabinet", "mesh": "build/model/mesh/k-cabinet.json", "qty": 1,
                  "placements": [{"name": "cabinet", "node": CAB, "loc": T()}]})
    return parts


def write_model(root):
    """build/model/ (parts.json, nodes.json, step/<key>.step, mesh/) of the synthetic machine under `root`."""
    import hashlib
    from build123d import Align, Box, Cylinder, Pos, Sphere, export_step
    root = Path(root)
    (root / "build/model/step").mkdir(parents=True)
    (root / "build/model/mesh").mkdir(parents=True)
    S = model_spec()
    mn = (Align.MIN, Align.MIN, Align.MIN)
    keys = {}
    for pid, s in S.items():
        kind, a = s["shape"]
        if kind == "box":
            shape = Box(*a, align=mn)
        elif kind == "cyl":
            shape = Cylinder(a[0], a[1], align=(Align.CENTER, Align.CENTER, Align.MIN))
        elif kind == "sphere":
            shape = Sphere(a[0])
        else:                                       # screw: head above the joint, shaft below it
            r, L, rh, hh = a
            shape = Cylinder(rh, hh, align=(Align.CENTER, Align.CENTER, Align.MIN)) + Pos(0, 0, -L) * Cylinder(r, L, align=(Align.CENTER, Align.CENTER, Align.MIN))
        tmp = root / "build/model/step/_tmp.step"
        export_step(shape, str(tmp))
        sha = hashlib.sha256(tmp.read_bytes()).hexdigest()
        key = hashlib.sha256(f"assy-fixture|{pid}|{sha}".encode()).hexdigest()
        tmp.replace(root / "build/model/step" / f"{key}.step")
        keys[pid] = (key, sha)
    (root / "build/model/mesh/k-cabinet.json").write_text(json.dumps({"v": [0, 0, 0, 100, 0, 0, 0, 100, 0, 0, 0, 100],
                                                                      "f": [0, 1, 2, 0, 1, 3, 0, 2, 3, 1, 2, 3]}))
    (root / "build/model/parts.json").write_text(json.dumps({"parts": parts_json(S, keys)}, ensure_ascii=False, indent=1))
    (root / "build/model/nodes.json").write_text("{}")
    return root


# ================================================================ derive.py assembly + reading the PDFs
class Run:
    def __init__(self, root, cp, manifest):
        self.root, self.rc = Path(root), cp.returncode
        self.log = (cp.stdout or "") + (cp.stderr or "")
        m = re.findall(r"\[derive\] assembly: parts (\d+), reused (\d+), computed (\d+)", self.log)
        self.counts = tuple(int(x) for x in m[-1]) if m else None
        self.manifest = json.loads(manifest.read_text()) if manifest.exists() else None

    def ok(self):
        assert self.rc == 0 and self.counts and self.manifest is not None, f"derive assembly failed (rc={self.rc}):\n{self.log[-3000:]}"
        return self

    def entries(self):
        return {e["node"]: e for e in self.manifest.get("assembly") or []}

    def pdf(self, node):
        e = self.entries().get(node)
        assert e and e.get("files"), f"no PDF for node {node!r}: {self.manifest.get('assembly')}\n{self.log[-2000:]}"
        return self.root / e["files"][0]


def run_assembly(root):
    root = Path(root)
    manifest = root / "build/kit/assembly.json"
    env = dict(H.env_for(root), SHOP_DRAW_VENV=str(VENV), MPLCONFIGDIR=os.environ.get("MPLCONFIGDIR", "/tmp/mpl"))
    cp = H.procgroup.run([sys.executable, str(DERIVE), "--no-rebuild", "assembly", str(manifest)], cwd=root, env=env,
                         capture_output=True, text=True, timeout=1800)
    return Run(root, cp, manifest)


def pdf_pages(path):
    """[{text, numbers_in_balloons: {int}, words: [str]}] per page."""
    fitz = pytest.importorskip("pymupdf")
    out = []
    with fitz.open(str(path)) as doc:
        for page in doc:
            text = page.get_text()
            words = page.get_text("words")
            circles = []
            for d in page.get_drawings():
                kinds = [it[0] for it in d.get("items", [])]
                r = d.get("rect")
                if r is None or kinds.count("c") < 2:
                    continue
                w, h = r.width, r.height
                if 3 <= w <= 60 and 3 <= h <= 60 and abs(w - h) <= 0.2 * max(w, h):
                    circles.append(r)
            balloons = set()
            for x0, y0, x1, y1, t, *_ in words:
                if re.fullmatch(r"\d{1,3}", t):
                    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                    if any(c.x0 <= cx <= c.x1 and c.y0 <= cy <= c.y1 for c in circles):
                        balloons.add(int(t))
            out.append({"text": text, "flat": " ".join(text.split()), "balloons": balloons, "words": [w[4] for w in words]})
    return out


@pytest.fixture(scope="session")
def assy_model(tmp_path_factory):
    pytest.importorskip("build123d")
    return write_model(tmp_path_factory.mktemp("assy_model") / "proj")


@pytest.fixture(scope="session")
def first_run(assy_model, tmp_path_factory):
    root = tmp_path_factory.mktemp("assy_first") / "proj"
    shutil.copytree(assy_model, root)
    return run_assembly(root)


def copy_of(run, tmp_path):
    dst = tmp_path / "proj"
    shutil.copytree(run.root, dst)
    return dst


# ================================================================ assembly_nodes: pure, no build123d
def _import_derive():
    spec = importlib.util.spec_from_file_location("derive_under_test", DERIVE)
    m = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPTS))
    spec.loader.exec_module(m)
    return m


def drawable_parts():
    """parts.json without the bought row and the mesh-only cabinet (those two have their own test)."""
    return [p for p in parts_json(model_spec()) if p["id"] not in ("cable", "cabinet")]


def _nodes(parts=None):
    d = _import_derive()
    assert hasattr(d, "assembly_nodes"), "derive.assembly_nodes(parts) is missing"
    return d.assembly_nodes(drawable_parts() if parts is None else parts)


def _names(items):
    return {it["name"] for it in items}


def test_1_assembly_nodes_groups_placements_by_node_and_fasteners_follow_their_docking():
    nodes = _nodes()
    assert FASTENER_NODE not in nodes, f"fasteners are no node of their own: {sorted(nodes)}"
    assert DRAWN_NODES <= set(nodes), sorted(nodes)
    frame = _names(nodes[FRAME])
    assert {"base", "bracket"} <= frame
    assert {"washer_m5", "washer_m5_2", "washer_m5_3"} <= frame, "a washer docked into the base belongs to «Frame»"
    assert {"screw_m5x12", "screw_m5x12_2", "screw_m5x12_3"} <= frame, "a screw on a washer on the base belongs to «Frame»"
    assert {"nut_m5", "nut_m5_2"} <= frame, "a nut on a screw on a washer on the base belongs to «Frame»"
    drive = _names(nodes[DRIVE])
    assert {"motor_plate", "shaft", "screw_m4x10", "screw_m5x12_4"} <= drive
    assert "screw_m5x12_4" not in frame, "the same screw part placed in the drive counts in the drive"
    assert not (frame & drive) and not (frame & _names(nodes[CONV])) and not (drive & _names(nodes[CONV])), "a placement is in one node"
    assert len(nodes[CONV]) == CONV_ROWS


def test_1_assembly_nodes_items_carry_part_parent_and_axis():
    nodes = _nodes()
    by = {it["name"]: it for items in nodes.values() for it in items}
    assert by["screw_m5x12_2"]["part"] == "screw_m5x12" and by["nut_m5_2"]["part"] == "nut_m5"
    assert by["bracket"]["parent"] == "base"
    assert by["shaft"]["parent"] == "motor_plate"
    assert by["roller_10"]["parent"] == "section_2"
    assert not by["base"].get("parent"), "the machine's root has no parent"
    ax = by["bracket"].get("axis")
    # a direction [x, y, z], or an axis as [origin, direction] / {"origin", "direction"} (world coordinates)
    d = ax.get("direction") if isinstance(ax, dict) else ax[1] if isinstance(ax, (list, tuple)) and len(ax) == 2 else ax
    assert isinstance(d, (list, tuple)) and len(d) == 3, f"bracket axis: {ax!r}"
    n = math.sqrt(sum(float(v) ** 2 for v in d))
    assert n > 0 and abs(float(d[2])) / n > 0.99, f"the bracket is docked on the base's top (Z up): axis {ax}"


def test_1_assembly_nodes_takes_parts_json_as_it_is_rows_and_mesh_parts_are_not_drawn():
    """A real parts.json holds bought rows (no shape, no placements) and mesh-only parts (the electrical cabinet: placements, no
    STEP). The helper takes that list as it is and gives neither an item."""
    nodes = _nodes(parts_json(model_spec()))
    parts_drawn = {it["part"] for items in nodes.values() for it in items}
    assert "cable" not in parts_drawn and "cabinet" not in parts_drawn, sorted(parts_drawn)
    assert not nodes.get(CAB), f"the mesh-only cabinet node has nothing to draw: {nodes.get(CAB)}"
    assert DRAWN_NODES <= set(nodes)


def test_1_assembly_nodes_needs_no_build123d(tmp_path):
    """Pure helper: importing derive and grouping the parts must work where build123d is not importable."""
    pj = tmp_path / "parts.json"
    pj.write_text(json.dumps(drawable_parts(), ensure_ascii=False))
    code = ("import sys, json, importlib.util; sys.modules['build123d'] = None; sys.modules['OCP'] = None\n"
            f"sys.path.insert(0, {str(SCRIPTS)!r})\n"
            f"spec = importlib.util.spec_from_file_location('d', {str(DERIVE)!r}); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
            f"n = m.assembly_nodes(json.load(open({str(pj)!r})))\n"
            "print(json.dumps(sorted(n)))\n")
    cp = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert cp.returncode == 0, cp.stderr[-2000:]
    assert DRAWN_NODES <= set(json.loads(cp.stdout.strip().splitlines()[-1]))



def test_1_loose_fasteners_of_a_folder_node_get_no_sheet(tmp_path):
    """Review of #22: screws a folder node placed without docking them stay in that folder node; a node of fasteners only is no
    assembly (it would be one sheet of every loose screw of the machine)."""
    parts = drawable_parts() + [{"id": "loose_screw", "name": "loose_screw", "key": "kloose", "process": "bought", "kind": "fasteners",
                                 "code": "M5x12 DIN 912", "step": "build/model/step/loose.step",
                                 "placements": [{"name": "loose_screw", "node": "fasteners", "loc": [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]}]}]
    pj = tmp_path / "parts.json"
    pj.write_text(json.dumps(parts, ensure_ascii=False))
    code = (f"import sys, json, importlib.util\nsys.path.insert(0, {str(SCRIPTS)!r})\n"
            f"spec = importlib.util.spec_from_file_location('d', {str(DERIVE)!r}); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
            f"print(json.dumps(sorted(m.assembly_nodes(json.load(open({str(pj)!r}))))))\n")
    cp = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert cp.returncode == 0, cp.stderr[-2000:]
    nodes = set(json.loads(cp.stdout.strip().splitlines()[-1]))
    assert "fasteners" not in nodes and DRAWN_NODES <= nodes, nodes


# ================================================================ derive.py assembly: the zip's contents
@needs_venv
def test_1_one_pdf_per_node_and_none_for_the_whole_machine(first_run):
    r = first_run.ok()
    with_files = {n for n, e in r.entries().items() if e.get("files")}
    assert with_files == DRAWN_NODES, f"one PDF per node, nothing else: {r.manifest.get('assembly')}"
    assert r.manifest.get("parts") == [], "the assembly piece lists no parts"
    for n in DRAWN_NODES:
        e = r.entries()[n]
        assert len(e["files"]) == 1 and e["files"][0].endswith(".pdf") and not e.get("error"), e
        assert not Path(e["files"][0]).is_absolute() and ".." not in Path(e["files"][0]).parts, "a path relative to the project"
        f = r.pdf(n)
        assert f.is_file() and f.read_bytes()[:5] == b"%PDF-", f
    assert len({r.entries()[n]["files"][0] for n in DRAWN_NODES}) == 3, "each node has its own file"


@needs_venv
def test_1_mesh_only_rows_and_product_parts_are_not_drawn(first_run):
    r = first_run.ok()
    cab = r.entries().get(CAB)
    assert not (cab and cab.get("files")), f"the mesh-only cabinet node gets no PDF: {cab}"
    every = " ".join(p["flat"] for n in DRAWN_NODES for p in pdf_pages(r.pdf(n)))
    for t in ("Apple", "Power cable", "Electrical cabinet"):
        assert t not in every, f"«{t}» must not be drawn or listed"


@needs_venv
def test_1_a_node_sheet_holds_only_its_own_parts(first_run):
    r = first_run.ok()
    frame = " ".join(p["flat"] for p in pdf_pages(r.pdf(FRAME)))
    drive = " ".join(p["flat"] for p in pdf_pages(r.pdf(DRIVE)))
    assert "Base plate" in frame and "Bracket" in frame
    assert "Motor plate" in drive and "Shaft" in drive
    assert "Motor plate" not in frame and "Roller" not in frame and "Conveyor frame" not in frame
    assert "Bracket" not in drive and "Roller" not in drive


# ================================================================ [2] balloons, the table, fasteners
@needs_venv
@pytest.mark.parametrize("node", [FRAME, DRIVE])
def test_2_table_header(first_run, node):
    pages = pdf_pages(first_run.ok().pdf(node))
    for h in COLS:
        assert h in pages[0]["text"], f"table header «{h}» on the {node} sheet: {pages[0]['flat'][:500]}"


@needs_venv
def test_2_frame_rows_are_numbered_by_balloons_and_identical_fasteners_merge(first_run):
    """«Frame»: base, bracket, washer ×3, screw M5x12 ×3, nut ×2 = 5 rows -> balloons 1..5 (8 fasteners would be 13 rows unmerged,
    the apple and the cable would add 2 more)."""
    pages = pdf_pages(first_run.ok().pdf(FRAME))
    assert len(pages) == 1, f"a small node is one page, got {len(pages)}"
    p = pages[0]
    assert p["balloons"] == set(range(1, 6)), f"numbered balloons 1..5 on the view, got {sorted(p['balloons'])}"
    for n in range(1, 6):
        assert p["words"].count(str(n)) >= 2, f"row {n}: once in its balloon, once in the table's number column"


@needs_venv
def test_2_fastener_row_shows_size_from_code_and_quantity(first_run):
    flat = pdf_pages(first_run.ok().pdf(FRAME))[0]["flat"]
    assert "5×12" in flat, f"the M5x12 DIN 912 row shows 5×12: {flat[:800]}"
    assert "×3" in flat, "three identical screws (and washers) -> ×3"
    assert "×2" in flat, "two nuts -> ×2"
    assert flat.count("5×12") <= 2, "one row for the three screws (size in the part column and maybe the code column), not three rows"


@needs_venv
def test_2_drive_rows_and_its_own_screws(first_run):
    pages = pdf_pages(first_run.ok().pdf(DRIVE))
    assert len(pages) == 1
    p = pages[0]
    assert p["balloons"] == set(range(1, 5)), f"motor plate, shaft, M4x10, M5x12 -> balloons 1..4, got {sorted(p['balloons'])}"
    assert "4×10" in p["flat"] and "5×12" in p["flat"], p["flat"][:800]


# ================================================================ [3] a big node: several pages
@needs_venv
def test_3_big_node_is_split_into_step_sheets_then_the_node(first_run):
    pages = pdf_pages(first_run.ok().pdf(CONV))
    assert len(pages) >= 2, f"{CONV_ROWS} rows do not fit one sheet: got {len(pages)} page(s)"
    assert re.search(rf"{STEP}\s*1\b", pages[0]["flat"]), f"the first sheet is a sub-assembly step «{STEP} 1»: {pages[0]['flat'][:400]}"
    for i, p in enumerate(pages):
        for h in (COLS[0], COLS[1], COLS[3]):
            assert h in p["text"], f"page {i + 1} has the table header «{h}»"
        assert p["balloons"], f"page {i + 1} has numbered balloons"
        assert max(p["balloons"]) <= MAX_ROWS, f"page {i + 1}: at most {MAX_ROWS} rows, balloons up to {max(p['balloons'])}"
    every = " ".join(p["flat"] for p in pages)
    missing = [t for t in roller_titles() + ["Conveyor frame"] + [f"Section {k + 1}" for k in range(N_SECTIONS)] if t not in every]
    assert not missing, f"every part of the node is on some sheet: missing {missing}"
    assert "Conveyor frame" in pages[-1]["flat"], "the node itself (its frame) comes last"


@needs_venv
def test_3_small_nodes_have_no_steps(first_run):
    for n in (FRAME, DRIVE):
        pages = pdf_pages(first_run.ok().pdf(n))
        assert len(pages) == 1 and STEP not in pages[0]["flat"], f"{n}: one sheet, no steps"


# ================================================================ [4] cache
@needs_venv
def test_4_second_run_with_nothing_changed_computes_nothing(first_run, tmp_path):
    r1 = first_run.ok()
    root = copy_of(r1, tmp_path)
    files = {n: root / r1.entries()[n]["files"][0] for n in DRAWN_NODES}
    before = {n: (f.stat().st_mtime_ns, f.read_bytes()) for n, f in files.items()}
    r2 = run_assembly(root).ok()
    assert r2.counts[2] == 0, f"nothing changed -> computed 0:\n{r2.log[-1500:]}"
    assert r2.manifest["assembly"] == r1.manifest["assembly"]
    assert {n: (f.stat().st_mtime_ns, f.read_bytes()) for n, f in files.items()} == before, "the PDFs were not redrawn"


@needs_venv
def test_4_a_changed_part_redraws_its_node(first_run, tmp_path):
    """The cache is not stale: a renamed shaft shows on the drive sheet after the next run."""
    r1 = first_run.ok()
    root = copy_of(r1, tmp_path)
    pj = root / "build/model/parts.json"
    data = json.loads(pj.read_text())
    for p in data["parts"]:
        if p["id"] == "shaft":
            p["title"] = "Drive shaft"
    pj.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    r2 = run_assembly(root).ok()
    assert r2.counts[2] >= 1, r2.log[-1500:]
    assert "Drive shaft" in pdf_pages(r2.pdf(DRIVE))[0]["flat"]
