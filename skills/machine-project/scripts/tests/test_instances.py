"""Issue #32: a part file placed N times is ONE geometry used N times (instances), not N copies.

Done-when map:
  DW1  model.step: a part placed N>1 times -> N solids, each labelled with ITS placement's shown name (placement name, or part
       name + suffix for an id-suffixed repeat, as derive.shown_name); a part placed once keeps title, else desc, else name.
  DW2  model.glb: one glTF mesh per part file with geometry, referenced by one node per placement; each node keeps its own
       extras.part (the placement's viewer id) and its placement transform; world geometry of every copy unchanged; the glb is
       smaller than one mesh per copy.
  DW3  viewer parts.json: unchanged shape - one entry per placement (id, name, node, world bbox, part_id for repeats, files).
  DW4  bake_edges.py: edges.json has an entry for every copy's part id, the edge bytes are stored once per part file.

The model (build/model/parts.json + STEPs in each part's OWN coordinates + placements) is hand-written here, like
helpers.make_derived_model, so these tests depend only on derive.py / step_import.py / bake_edges.py.

    rail     Cylinder r20 h100 (axis Z), title "Rail guide"   placed 3x: rail, rail_2 (translated), rail_3 (rotated 90 deg about X,
             node frame/sub)
    bracket  Box 20x10x5 off-centre, desc "Bracket L"         placed 2x by explicit names: bracket_left (no node: the part's),
             bracket_right (rotated 90 deg about Z)
    foot     Box 30x30x10, title "Foot pad"                   placed once (id "foot")
    spacer   Box 8x8x4, desc "Spacer M8"                       placed once (id "spacer")
    block    old layout: world-coordinate STEP, no placements
"""
import json
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

import helpers as H
import partfiles as F

BAKE_EDGES = H.SCRIPTS.parent.parent / "step-import" / "scripts" / "bake_edges.py"


# ---------------------------------------------------------------- placement matrices (4x4 row-major, flattened to 16)
def T(x, y, z):
    return [1, 0, 0, x, 0, 1, 0, y, 0, 0, 1, z, 0, 0, 0, 1]


def RX90(x, y, z):          # rotate +90 deg about X, then translate
    return [1, 0, 0, x, 0, 0, -1, y, 0, 1, 0, z, 0, 0, 0, 1]


def RZ90(x, y, z):          # rotate +90 deg about Z, then translate
    return [0, -1, 0, x, 1, 0, 0, y, 0, 0, 1, z, 0, 0, 0, 1]


def _shapes():
    from build123d import Box, Cylinder, Pos
    return {
        "rail": Cylinder(20, 100),
        "bracket": Pos(10, 0, 0) * Box(20, 10, 5),
        "foot": Box(30, 30, 10),
        "spacer": Box(8, 8, 4),
        "block": Pos(-100, 0, 0) * Box(5, 5, 5),
    }


# part id -> (process, node, extra fields, [(placement name, placement node or None, loc)] or None for the old layout)
SPEC = {
    "rail": ("mill", "frame", {"title": "Rail guide"},
             [("rail", "frame", T(0, 0, 0)), ("rail_2", "frame", T(100, 0, 0)), ("rail_3", "frame/sub", RX90(200, 50, 0))]),
    "bracket": ("mill", "frame", {"desc": "Bracket L"},
                [("bracket_left", None, T(0, -50, 0)), ("bracket_right", "frame", RZ90(0, 50, 0))]),
    "foot": ("mill", "legs", {"title": "Foot pad"}, [("foot", "legs", T(0, 0, -20))]),
    "spacer": ("mill", "legs", {"desc": "Spacer M8"}, [("spacer", None, T(50, 50, 50))]),
    "block": ("mill", "misc", {}, None),
}

# placement (viewer) id -> what every output must show for it
SHOWN = {"rail": "rail", "rail_2": "rail_2", "rail_3": "rail_3", "bracket_left": "bracket_left",
         "bracket_right": "bracket_right", "foot": "foot", "spacer": "spacer", "block": "block"}
STEP_LABEL = dict(SHOWN, foot="Foot pad", spacer="Spacer M8")          # placed once: title, else desc, else name
OWNER = {"rail": "rail", "rail_2": "rail", "rail_3": "rail", "bracket_left": "bracket", "bracket_right": "bracket",
         "foot": "foot", "spacer": "spacer", "block": "block"}
NODE = {"rail": "frame", "rail_2": "frame", "rail_3": "frame/sub", "bracket_left": "frame", "bracket_right": "frame",
        "foot": "legs", "spacer": "legs", "block": "misc"}
PART_FILES_WITH_GEOMETRY = 5


def _locs():
    out = {}
    for pid, (_, node, _, pls) in SPEC.items():
        for name, _, loc in (pls or [(pid, None, None)]):
            out[name] = loc
    return out


def _moved(shape, loc):
    from build123d import Location
    from OCP.gp import gp_Trsf
    if loc is None:
        return shape
    t = gp_Trsf()
    t.SetValues(*[float(x) for x in loc[:12]])
    return shape.moved(Location(t))


def expected_bbox(vid):
    """World bbox (mm) of a placement, from the part's own shape and its placement matrix (independent of derive)."""
    bb = _moved(_shapes()[OWNER[vid]], _locs()[vid]).bounding_box()
    return np.array([[bb.min.X, bb.min.Y, bb.min.Z], [bb.max.X, bb.max.Y, bb.max.Z]])


# a tessellated cylinder's bbox can sit inside the true one by up to the chord deflection (0.5 mm); boxes are exact
def tol(vid):
    return 0.55 if OWNER[vid] == "rail" else 0.02


def write_model(root, flat=False):
    """The instanced model; with flat=True the same 8 copies as 8 separate world-coordinate parts (one mesh per copy: the
    pre-#32 cost, the size baseline)."""
    root = Path(root)
    (root / "mechanics" / "cad" / "parts").mkdir(parents=True)
    (root / "build/model/step").mkdir(parents=True)
    shapes, parts = _shapes(), []
    for pid, (proc, node, extra, pls) in SPEC.items():
        if flat:
            for name, pnode, loc in (pls or [(pid, node, None)]):
                p = H._write_part(root, name, proc, _moved(shapes[pid], loc), dict(extra))
                p["node"] = pnode or node
                parts.append(p)
            continue
        p = H._write_part(root, pid, proc, shapes[pid], dict(extra))
        p["node"] = node
        if pls is not None:
            p["placements"] = [{"name": n, **({"node": nd} if nd else {}), "loc": loc} for n, nd, loc in pls]
            p["qty"] = len(pls)
        parts.append(p)
    H._write_parts_json(root, parts)
    return root


@pytest.fixture(scope="session")
def inst_base(tmp_path_factory):
    import conftest
    shared = conftest._shared(tmp_path_factory, "instances", lambda s: (write_model(s / "proj"), write_model(s / "flat", flat=True)))
    return shared


@pytest.fixture
def iproj(inst_base, tmp_path):
    return Path(shutil.copytree(inst_base / "proj", tmp_path / "proj"))


@pytest.fixture
def flatproj(inst_base, tmp_path):
    return Path(shutil.copytree(inst_base / "flat", tmp_path / "flat"))


# ---------------------------------------------------------------- glb reading (independent of the code under test)
CT = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
NC = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


def read_glb(path):
    b = Path(path).read_bytes()
    assert b[:4] == b"glTF", f"{path}: not a GLB"
    off, js, binc = 12, None, b""
    while off < len(b):
        n, t = struct.unpack_from("<II", b, off)
        chunk = b[off + 8: off + 8 + n]
        if t == 0x4E4F534A:
            js = json.loads(chunk)
        elif t == 0x004E4942:
            binc = chunk
        off += 8 + n
    return js, binc


def accessor(js, binc, i):
    a = js["accessors"][i]
    bv = js["bufferViews"][a["bufferView"]]
    nc, dt = NC[a["type"]], CT[a["componentType"]]
    isz = np.dtype(dt).itemsize
    stride = bv.get("byteStride") or nc * isz
    start = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    rows = [np.frombuffer(binc, dtype=dt, count=nc, offset=start + k * stride) for k in range(a["count"])]
    arr = np.array(rows, dtype=np.float64)
    if a.get("normalized") and dt == np.int16:
        arr = np.maximum(arr / 32767.0, -1.0)
    return arr


def node_matrix(n):
    if "matrix" in n:
        return np.array(n["matrix"], dtype=float).reshape(4, 4).T          # glTF stores column-major
    m = np.eye(4)
    if "scale" in n:
        m = np.diag([*n["scale"], 1.0]) @ m
    if "rotation" in n:
        x, y, z, w = n["rotation"]
        r = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                      [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                      [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
        R = np.eye(4); R[:3, :3] = r
        m = R @ m
    if "translation" in n:
        Tm = np.eye(4); Tm[:3, 3] = n["translation"]
        m = Tm @ m
    return m


def world_matrices(js):
    """node index -> world matrix (parents applied)."""
    out = {}

    def walk(i, parent):
        w = parent @ node_matrix(js["nodes"][i])
        out[i] = w
        for c in js["nodes"][i].get("children", []):
            walk(c, w)
    for r in js["scenes"][js.get("scene", 0)]["nodes"]:
        walk(r, np.eye(4))
    return out


def mesh_nodes(js):
    return [(i, n) for i, n in enumerate(js["nodes"]) if "mesh" in n]


def node_world_bbox_mm(js, binc, i, W):
    n = js["nodes"][i]
    pts = []
    for prim in js["meshes"][n["mesh"]]["primitives"]:
        P = accessor(js, binc, prim["attributes"]["POSITION"])
        pts.append(P @ W[i][:3, :3].T + W[i][:3, 3])
    v = np.concatenate(pts) * 1000.0                                          # glb is in metres
    return np.array([v.min(0), v.max(0)])


def mesh_bytes(js, mi):
    """Binary bytes one glTF mesh stores (its accessors' buffer views)."""
    views = set()
    for prim in js["meshes"][mi]["primitives"]:
        for a in list(prim["attributes"].values()) + [prim["indices"]]:
            views.add(js["accessors"][a]["bufferView"])
    return sum(js["bufferViews"][v]["byteLength"] for v in views)


def flat(bb):
    return [float(x) for row in np.asarray(bb, dtype=float).tolist() for x in row]


def norm(label):
    """A STEP product name as it reads back: the writer/reader turns spaces into underscores ("Foot pad" -> "Foot_pad")."""
    return (label or "").replace(" ", "_")


def step_labels(root, d):
    return [(norm(lbl), sh) for _, lbl, sh in F.leaves(F.step_tree(F.abs_path(root, d.manifest["model_step"])))]


def _viewer(root):
    d = H.run_derive(root, "viewer").ok()
    vd = F.abs_path(root, d.manifest["viewer_dir"])
    return d, vd


# ================================================================ DW1: model.step labels
def test_model_step_labels_every_copy_of_a_repeated_part_by_its_own_placement_name(iproj):
    """DW1: rail (title "Rail guide") placed 3x -> solids rail, rail_2, rail_3; bracket (desc "Bracket L") placed 2x by explicit
    names -> bracket_left, bracket_right. Not N copies of the title / desc."""
    d = H.run_derive(iproj, "step").ok()
    labels = [lbl for lbl, _ in step_labels(iproj, d)]
    repeated = sorted(l for l in labels if l in (norm("Rail guide"), norm("Bracket L")) or l.startswith(("rail", "bracket")))
    assert repeated == sorted(["rail", "rail_2", "rail_3", "bracket_left", "bracket_right"]), \
        f"each copy of a repeated part must carry its own placement name, got labels {labels}"
    assert sorted(labels) == sorted(map(norm, STEP_LABEL.values())), labels


def test_model_step_each_copy_label_sits_on_that_copys_geometry(iproj):
    """DW1: the label rail_3 is on the rotated copy at (200, 50, 0), bracket_right on the rotated bracket - labels are not
    just renamed in some order."""
    d = H.run_derive(iproj, "step").ok()
    by_label = {}
    for lbl, sh in step_labels(iproj, d):
        by_label.setdefault(lbl, []).append(sh)
    for vid in ("rail", "rail_2", "rail_3", "bracket_left", "bracket_right"):
        got = by_label.get(vid, [])
        assert len(got) == 1, f"exactly one solid labelled {vid!r} expected, got {len(got)}; labels {sorted(map(str, by_label))}"
        bb = got[0].bounding_box()
        want = expected_bbox(vid)
        assert [bb.min.X, bb.min.Y, bb.min.Z, bb.max.X, bb.max.Y, bb.max.Z] == pytest.approx(flat(want), abs=1e-3), vid


def test_model_step_part_placed_once_keeps_title_else_desc_else_name(iproj):
    """DW1: foot (title) -> "Foot pad", spacer (desc) -> "Spacer M8", block (old layout, name only) -> "block"; one solid each,
    and the model holds exactly one solid per placement. (Regression guard: passes before and after.)"""
    d = H.run_derive(iproj, "step").ok()
    tree = F.step_tree(F.abs_path(iproj, d.manifest["model_step"]))
    labels = [lbl for lbl, _ in step_labels(iproj, d)]
    for want in ("Foot pad", "Spacer M8", "block"):
        assert labels.count(norm(want)) == 1, f"{want!r} expected once, labels {labels}"
    assert len(tree.solids()) == len(SHOWN)


# ================================================================ DW2: model.glb instancing
def test_glb_stores_one_mesh_per_part_file_referenced_by_one_node_per_placement(iproj):
    """DW2: 5 part files with geometry -> 5 meshes; 8 placements -> 8 mesh nodes, each with its own extras.part; the 3 rail nodes
    share one mesh, the 2 bracket nodes share one mesh."""
    _, vd = _viewer(iproj)
    js, _ = read_glb(vd / "model.glb")
    assert len(js["meshes"]) == PART_FILES_WITH_GEOMETRY, \
        f"one mesh per part file expected ({PART_FILES_WITH_GEOMETRY}), got {len(js['meshes'])}"
    nodes = mesh_nodes(js)
    ids = [(n.get("extras") or {}).get("part") for _, n in nodes]
    assert sorted(ids) == sorted(SHOWN), f"one mesh node per placement, extras.part = viewer id; got {ids}"
    mesh_of = {(n.get("extras") or {}).get("part"): n["mesh"] for _, n in nodes}
    assert mesh_of["rail"] == mesh_of["rail_2"] == mesh_of["rail_3"], mesh_of
    assert mesh_of["bracket_left"] == mesh_of["bracket_right"], mesh_of
    assert len({mesh_of["rail"], mesh_of["bracket_left"], mesh_of["foot"], mesh_of["spacer"], mesh_of["block"]}) == 5, mesh_of


def test_glb_every_copy_sits_where_its_placement_puts_it(iproj):
    """DW2: world bbox of each node's (dequantised, transformed) vertices = the placed shape's bbox, rotations included."""
    _, vd = _viewer(iproj)
    js, binc = read_glb(vd / "model.glb")
    W = world_matrices(js)
    seen = set()
    for i, n in mesh_nodes(js):
        vid = (n.get("extras") or {}).get("part")
        seen.add(vid)
        got = node_world_bbox_mm(js, binc, i, W)
        assert flat(got) == pytest.approx(flat(expected_bbox(vid)), abs=tol(vid)), f"{vid}: world bbox {got.tolist()}"
    assert seen == set(SHOWN)


def test_glb_world_geometry_matches_one_mesh_per_copy_baseline(iproj, flatproj):
    """DW2: per copy, the world bbox in the instanced glb equals the one in a glb built from the same copies as separate parts."""
    _, vd = _viewer(iproj)
    _, fd = _viewer(flatproj)
    out = {}
    for d in (vd, fd):
        js, binc = read_glb(d / "model.glb")
        W = world_matrices(js)
        out[d] = {(n.get("extras") or {}).get("part"): node_world_bbox_mm(js, binc, i, W) for i, n in mesh_nodes(js)}
    assert set(out[vd]) == set(out[fd]) == set(SHOWN)
    for vid in SHOWN:
        assert flat(out[vd][vid]) == pytest.approx(flat(out[fd][vid]), abs=0.02), vid
    js, _ = read_glb(vd / "model.glb")
    assert len(js["meshes"]) == PART_FILES_WITH_GEOMETRY, "instanced glb must hold one mesh per part file"


def test_glb_with_repeats_is_smaller_than_one_mesh_per_copy(iproj, flatproj):
    """DW2: the instanced glb saves at least ~the bytes of the 3 duplicate meshes (rail_2, rail_3, bracket_right)."""
    _, vd = _viewer(iproj)
    _, fd = _viewer(flatproj)
    inst, flat = (vd / "model.glb").stat().st_size, (fd / "model.glb").stat().st_size
    fjs, _ = read_glb(fd / "model.glb")
    fmesh = {(n.get("extras") or {}).get("part"): n["mesh"] for _, n in mesh_nodes(fjs)}
    dup = sum(mesh_bytes(fjs, fmesh[v]) for v in ("rail_2", "rail_3", "bracket_right"))
    assert inst <= flat - 0.9 * dup, f"instanced glb {inst} B vs one-mesh-per-copy {flat} B; duplicate mesh bytes {dup} B"


# ================================================================ DW3: viewer catalogue unchanged
def test_viewer_catalogue_has_one_entry_per_placement_with_world_bbox_and_files(iproj):
    """DW3 (regression guard, passes before and after): id, name, node, world bbox, part_id for repeats, step+stl for every copy."""
    _, vd = _viewer(iproj)
    cat = {p["id"]: p for p in json.loads((vd / "parts.json").read_text())["parts"]}
    assert sorted(cat) == sorted(SHOWN), sorted(cat)
    for vid, p in cat.items():
        assert p["name"] == SHOWN[vid], (vid, p["name"])
        assert p["module"] == NODE[vid], (vid, p["module"])
        if vid == OWNER[vid]:
            assert "part_id" not in p, (vid, p.get("part_id"))
        else:
            assert p.get("part_id") == OWNER[vid], (vid, p.get("part_id"))
        assert flat(p["bbox_mm"]) == pytest.approx(flat(expected_bbox(vid)), abs=tol(vid)), (vid, p["bbox_mm"])
        assert set(p["files"]) >= {"step", "stl"}, (vid, p["files"])
        for ext, rel in p["files"].items():
            f = vd / rel
            assert f.is_file() and f.stat().st_size > 0, f"{vid}: {ext} file {rel} missing or empty"


# ================================================================ DW4: edges for every copy, bytes once
def _bake(vd, tmp_path):
    out = Path(shutil.copytree(vd, tmp_path / "viewer"))
    cp = subprocess.run([sys.executable, str(BAKE_EDGES), str(out)], capture_output=True, text=True, timeout=300)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    return out, json.loads((out / "edges.json").read_text())


def test_bake_edges_gives_every_copy_its_contour_lines(iproj, tmp_path):
    """DW4: edges.json lists every placement's viewer id (rail_2, rail_3, bracket_right included), each with edges."""
    _, vd = _viewer(iproj)
    _, ej = _bake(vd, tmp_path)
    missing = set(SHOWN) - set(ej["parts"])
    assert not missing, f"no edges for copies {sorted(missing)}; edges.json parts {sorted(ej['parts'])}"
    for vid in SHOWN:
        assert ej["parts"][vid][1] > 0, f"{vid}: 0 edges"


def test_bake_edges_stores_edge_bytes_once_per_part_file(iproj, tmp_path):
    """DW4: copies of one part file point at the same edge bytes; edges.bin holds one block per part file (5), not per copy (8)."""
    _, vd = _viewer(iproj)
    out, ej = _bake(vd, tmp_path)
    P = ej["parts"]
    for group in (("rail", "rail_2", "rail_3"), ("bracket_left", "bracket_right")):
        got = [tuple(P.get(v, [None] * 5)) for v in group]
        assert len(set(got)) == 1, f"copies {group} must share one edge block, got {got}"
    blocks = {tuple(v) for v in P.values()}
    assert len(blocks) == PART_FILES_WITH_GEOMETRY, f"{len(blocks)} edge blocks, expected one per part file"
    need = 0
    for nv, ne, vo, io, w in blocks:
        need += nv * 8 + ne * 2 * w
    assert (out / "edges.bin").stat().st_size <= need + 4 * len(blocks), "edges.bin holds more than one block per part file"


def test_model_step_relabels_the_part_files_own_wrapper_in_each_copy(tmp_path):
    """DW1, as the CAD pipeline writes a multi-solid part (a tower_joint_screws_0 of a real machine): the part STEP is a compound labelled
    with the part's name holding a compound with the same label. Each copy carries its placement name at both levels, so a CAD tree
    shows screws > screws and screws_2 > screws_2, never screws_2 > screws."""
    import re
    from build123d import Box, Compound, Pos
    inner = Compound(children=[Box(5, 5, 10), Pos(20, 0, 0) * Box(5, 5, 10)])
    inner.label = "screws"
    outer = Compound(children=[inner])
    outer.label = "screws"
    root = tmp_path / "proj"
    (root / "mechanics" / "cad" / "parts").mkdir(parents=True)
    (root / "build/model/step").mkdir(parents=True)
    p = H._write_part(root, "screws", "bought", outer, {"title": "Joint screws", "desc": "M5x10 DIN 912"})
    p["node"] = "tower"
    p["placements"] = [{"name": "screws", "loc": T(0, 0, 0)}, {"name": "screws_2", "loc": T(0, 100, 0)}]
    p["qty"] = 2
    H._write_parts_json(root, [p])
    d = H.run_derive(root, "step").ok()
    names = re.findall(r"PRODUCT\('([^']*)'", F.abs_path(root, d.manifest["model_step"]).read_text())
    assert names.count("screws") == 2 and names.count("screws_2") == 2, names


def _one_part_model_step(tmp_path, shape, placements, **fields):
    """model.step of a project holding one part `w` (node tower) placed at `placements` (names), 100 mm apart along Y."""
    root = tmp_path / "proj"
    (root / "mechanics" / "cad" / "parts").mkdir(parents=True)
    (root / "build/model/step").mkdir(parents=True)
    p = H._write_part(root, "w", "bought", shape, fields)
    p["node"] = "tower"
    p["placements"] = [{"name": n, "loc": T(0, 100 * i, 0)} for i, n in enumerate(placements)]
    p["qty"] = len(placements)
    H._write_parts_json(root, [p])
    return F.abs_path(root, H.run_derive(root, "step").ok().manifest["model_step"])


def _leaf_shapes(sh):
    kids = list(sh.children)
    return [sh] if not kids else [x for k in kids for x in _leaf_shapes(k)]


def test_model_step_holds_a_parts_geometry_once_for_all_its_placements(tmp_path):
    """The STEP kit OOM (a real machine, 1001 placements): a 3-solid part placed 30 times is 3 solid bodies in the file, referenced 30
    times, and reads back as 90 solids where the placements put them."""
    from build123d import Box, Compound, Pos
    f = _one_part_model_step(tmp_path, Compound(children=[Box(5, 5, 5), Pos(10, 0, 0) * Box(5, 5, 5), Pos(20, 0, 0) * Box(5, 5, 5)]),
                             ["w"] + [f"w_{i}" for i in range(2, 31)])
    assert f.read_text().count("MANIFOLD_SOLID_BREP(") == 3
    tree = F.step_tree(f)
    assert len(tree.solids()) == 90
    assert tree.bounding_box().max.Y == pytest.approx(29 * 100 + 2.5, abs=1e-3)


def test_model_step_every_copy_keeps_the_part_colour(tmp_path):
    """Copies 2..N of a part whose solids sit under its own wrapper (screws > screws > solids) show the part colour too."""
    from build123d import Box, Compound, Pos
    inner = Compound(children=[Box(5, 5, 10), Pos(20, 0, 0) * Box(5, 5, 10)])
    inner.label = "w"
    outer = Compound(children=[inner])
    outer.label = "w"
    f = _one_part_model_step(tmp_path, outer, ["w", "w_2", "w_3"], color="#00ff00")
    leaves = _leaf_shapes(F.step_tree(f))
    assert len(leaves) == 6
    for s in leaves:
        assert s.color is not None and tuple(s.color)[:3] == pytest.approx((0, 1, 0), abs=1e-3), s.color


def test_model_step_a_part_placed_once_is_written_as_before(tmp_path):
    """Nothing to share: a one-solid part placed once is one product named by its title (no extra SOLID level), and no
    instance carries an OCCT label entry (=>[0:1:1:5]) or a bare number for a name."""
    import re
    from build123d import Box
    txt = _one_part_model_step(tmp_path, Box(5, 5, 5), ["w"], title="Block title").read_text()
    assert re.findall(r"PRODUCT\('([^']*)'", txt) == ["proj", "tower", "Block title"]
    assert re.findall(r"NEXT_ASSEMBLY_USAGE_OCCURRENCE\('[^']*','([^']*)'", txt) == ["tower", "Block title"]
    shared = _one_part_model_step(tmp_path / "shared", Box(5, 5, 5), ["w", "w_2"]).read_text()
    # a shared leaf's later instances may come out unnamed (the writer keys them by shape): a reader shows the product name
    assert "=>[" not in shared and not any(n.isdigit() for n in re.findall(r"NEXT_ASSEMBLY_USAGE_OCCURRENCE\('[^']*','([^']*)'", shared))
