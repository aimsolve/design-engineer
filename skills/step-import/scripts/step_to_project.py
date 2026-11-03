#!/usr/bin/env python3
"""A client's machine STEP -> a code project of the framework (skill machine-project «Code model»): the client's CAD is the
input of the port, our code is the model from then on.

    python step_to_project.py MACHINE.step PROJECT_ROOT [--title T] [--overrides parts_overrides.json] [--library ROOT]
        (cuts the STEP into PROJECT_ROOT/build/client_import with step_import.py first; an import dir works as well)

Writes into PROJECT_ROOT/mechanics/cad (the project's own files there are left alone, the generated ones are replaced):
  params.py, assemble.py, geom.py, recreated.py           the machine root and the framework templates
  parts/__init__.py                                         the machine: the top node, placed where the client's CAD has it
  parts/<node>/[<sub>/...]__init__.py                       one folder per distinct sub-assembly of the STEP, TITLE = its name;
                                                            build() docks its parts and sub-nodes by joints, in STEP order
  parts/<node>/params.py                                    the named numbers of the node's recreated parts
  parts/<node>/<part>.py                                    one file per distinct part, in the first node that uses it
  parts/fasteners/{screw,nut,washer}.py                     framework templates: the standard fasteners recognised
  vendor/client/<id>.step                                   the client's geometry of every part not recreated (bought parts:
                                                            the maker's 3D; made parts: listed with the reason)
  migration/reference.json                                  what the client's CAD says (per part: volume, bbox, holes; per
                                                            placement: its world matrix) - migration_check.py compares with it
  migration/report.json                                     what each part became and why

Deterministic: the same input writes the same files. Recognition is cached in build/step_to_project/ by the part's STEP
and the code of recreate.py, so a re-run after a fix recomputes only what changed.

Placement: every part of a node is docked onto the part it touches (the nearest one already placed, starting from the
node's largest made part); the joint frame sits on the surface of the part it docks to, at the closest point, axes along the
docking part's own. A sub-node is docked by its own anchor part. A recognised screw / nut / washer is docked by its template
joint (`head` / `seat`) on a frame of the part it holds, so its place is the client's to the micron and its row is a real
standard part."""
import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import import_i18n as I  # noqa: E402  (the words of part names in other languages, the texts of the rows)
TEMPLATES = HERE.parents[1] / "machine-project" / "templates"
CACHE_V = "v1"

PY_KEYWORDS = {"and", "as", "assert", "async", "await", "break", "class", "continue", "def", "del", "elif", "else", "except",
               "finally", "for", "from", "global", "if", "import", "in", "is", "lambda", "nonlocal", "not", "or", "pass",
               "raise", "return", "try", "while", "with", "yield", "parts", "params", "geom", "recreated", "build"}
DRAWING = re.compile(r"(?<![\d.])\d{2}\.\d{2}(?:\.\d{2})?\.\d{3}(?!\d)(?!\.\d)")
KIND = {"fastener": "fasteners", "electronics": "electrical", "motor": "motors", "pneumatic": "pneumatic", "bearing": "bearings"}
TYPE_KIND = {"cable_channel": "electrical", "din_rail": "electrical", "connector": "electrical", "pulley": "other",
             "belt": "other", "conveyor_belt": "other", "foot": "other"}


# ---------------------------------------------------------------- names
def slug(text, fallback="part"):
    """A Python identifier from a client's name: drop the cipher and the maker's project code before it (`B-KV`, `ABC-1`,
    or `ABC` followed by a drawing number), transliterate, lower snake case."""
    t = _drop_code(DRAWING.sub("\0", str(text))).replace("\0", " ")
    t = "".join(I.translit(c) for c in t.lower())
    t = re.sub(r"[^a-z0-9]+", "_", t).strip("_")
    t = re.sub(r"_+", "_", t)
    if not t:
        t = fallback
    if t[0].isdigit():
        t = "p" + t
    if t in PY_KEYWORDS:
        t += "_"
    return t[:48].rstrip("_")


def _drop_code(t):
    """`t` (its drawing numbers replaced by NUL) without a leading project code: upper-case letters, a dash and upper-case
    letters or digits (`B-KV`, `ABC-1`), or upper-case letters alone right before a drawing number (`ABC 02.01.121`); any
    script."""
    m = re.match(r"[\s\0]*([^\W\d_]{1,6})(?:-([^\W_]{1,6}))?(\s*)", t)
    if not m or not m.group(1).isupper():
        return t
    rest = t[m.end():]
    if m.group(2) is not None:
        if m.group(2) != m.group(2).upper() or not (m.group(3) or rest.startswith("\0")):
            return t
    elif len(m.group(1)) < 2 or not rest.startswith("\0"):
        return t
    return " " + rest


# names a node's build() itself uses: a part or sub-node module of that name would be shadowed by them
RESERVED = {"fasteners", "place", "params", "Location", "Plane", "P_"} | {f"c{i}" for i in range(1000)}


PART_ID = re.compile(r"""\bP\(\s*(["'])(.+?)\1""")


def project_part_ids(cad, skip):
    """The part ids the project's own part files declare (`P("<id>", ...)`), but those under parts/<skip>/ (a re-run of the
    same node port rewrites them). Only literal ids: one built in code (P(f"bolt_{i}")) is not seen."""
    ids = set()
    for f in sorted((cad / "parts").rglob("*.py")):
        rel = f.relative_to(cad / "parts").as_posix()
        if rel.startswith(skip + "/"):
            continue
        ids |= {m.group(2) for m in PART_ID.finditer(f.read_text(encoding="utf-8", errors="replace"))}
    return ids


def step_of(import_dir, part):
    """The client's STEP of a part (a part renamed by a node port keeps his file)."""
    return import_dir / "parts" / f"{part.get('his_id') or part['id']}.step"


PARAM_LINE = re.compile(r"^([A-Z][A-Z0-9_]*) = (.*)$", re.M)


def merge_params(mine, new):
    """A node's params.py the agent changed, with the numbers this run generates: a generated name the file has gets this
    run's value, one it lacks is added at the end; every other line of the agent's stays."""
    gen = dict(PARAM_LINE.findall(new))
    have = set()

    def put(m):
        have.add(m.group(1))
        return f"{m.group(1)} = {gen[m.group(1)]}" if m.group(1) in gen else m.group(0)
    out = PARAM_LINE.sub(put, mine)
    add = [f"{k} = {v}" for k, v in gen.items() if k not in have]
    if add:
        out = out.rstrip("\n") + "\n# recreated from the client's CAD by step_to_project.py\n" + "\n".join(add) + "\n"
    return out


def node_key(folder):
    """The key of a node port's own folders (migration/<key>/, vendor/client/<key>/): its whole folder path."""
    return folder.replace("/", "__")


def node_folder(cad, parent, name):
    """The folder of a node ported under `parent`: its name, made free among what the parent already holds (a part file of
    the same name would be shadowed by the package), unless it is this same node's earlier port (re-run)."""
    base, taken = slug(name, "node"), set()
    pdir = cad / "parts" / parent
    for x in pdir.iterdir() if pdir.is_dir() else []:
        if not x.name.startswith(("_", ".")):
            taken.add(x.stem if x.is_file() else x.name)
    cand, k = base, 2
    while cand in taken:
        pj = cad / "migration" / node_key(f"{parent}/{cand}") / "placement.json"
        if pj.is_file() and json.loads(pj.read_text(encoding="utf-8")).get("node") == name:
            break                                     # the same node ported here before: a re-run
        cand, k = f"{base}_{k}", k + 1
    return f"{parent}/{cand}"


def _doc(text):
    """A client's name made safe inside a generated docstring (a backslash or three quotes would break the module)."""
    return str(text).replace("\\", "\\\\").replace('"""', "'''")


def unique(name, taken):
    n, k = name, 2
    while n in taken:
        n = f"{name}_{k}"
        k += 1
    taken.add(n)
    return n


# ---------------------------------------------------------------- matrices
def mat(m16):
    return np.array(m16, dtype=float).reshape(4, 4)


def inv(m):
    r = np.eye(4)
    r[:3, :3] = m[:3, :3].T
    r[:3, 3] = -m[:3, :3].T @ m[:3, 3]
    return r


def loc_code(m, nd=6):
    """4x4 -> build123d Location source: a pure translation as Location((x, y, z)), else from a Plane (exact, no Euler)."""
    t = [round(float(v), nd) + 0.0 for v in m[:3, 3]]
    R = m[:3, :3]
    if np.allclose(R, np.eye(3), atol=1e-9):
        return f"Location(({_n(t[0])}, {_n(t[1])}, {_n(t[2])}))"
    x, z = R[:, 0], R[:, 2]
    return (f"Location(Plane(({_n(t[0])}, {_n(t[1])}, {_n(t[2])}), x_dir=({_d(x[0])}, {_d(x[1])}, {_d(x[2])}), "
            f"z_dir=({_d(z[0])}, {_d(z[1])}, {_d(z[2])})))")


def _n(v, nd=6):
    v = round(float(v), nd) + 0.0
    return repr(int(v)) if v == int(v) and abs(v) < 1e12 else repr(v)


def _d(v):
    v = float(v)
    for s in (0.0, 1.0, -1.0):
        if abs(v - s) < 1e-12:
            return _n(s)
    return repr(round(v, 12) + 0.0)


def from_np(m):
    from build123d import Location
    from OCP.gp import gp_Trsf
    t = gp_Trsf()
    t.SetValues(*[float(x) for x in m[:3].reshape(-1)])
    return Location(t)


# ---------------------------------------------------------------- the tree
class Product:
    def __init__(self, ref, name, drawing):
        self.ref, self.name, self.drawing = ref, name, drawing
        self.children = []          # [(kind "part"|"node", id|ref, local 4x4, instance name)]
        self.parents = []
        self.folder = None
        self.anchor = None          # index into children
        self.dock = {}              # child index -> (parent child index, frame 4x4 in this product's coordinates)
        self.out_index = None       # index of the anchor part in the flat list this node's build() returns
        self.anchor_m = None        # where the anchor part sits in this product (through sub-nodes)


def load_tree(import_dir):
    tree = json.loads((import_dir / "instances.json").read_text(encoding="utf-8"))
    products, order = {}, []

    def visit(n, parent_ref):
        ref = n.get("ref") or n["name"]
        if ref not in products:
            products[ref] = Product(ref, n.get("product") or n["name"], n.get("drawing"))
            order.append(ref)
            for c in n.get("children", []):
                if "children" in c:
                    products[ref].children.append(("node", c.get("ref") or c["name"], mat(c["matrix"]), c["name"]))
                    visit(c, ref)
                else:
                    products[ref].children.append(("part", c["part"], mat(c["matrix"]), c["name"]))
        if parent_ref is not None and parent_ref not in products[ref].parents:
            products[ref].parents.append(parent_ref)
    visit(tree, None)
    root_ref = tree.get("ref") or tree["name"]
    return products, order, root_ref, mat(tree["matrix"])


def bottom_up(products, root_ref):
    """Every product after all of its sub-products (the CAD order is not that when a sub-product is used in several places)."""
    out, seen = [], set()

    def visit(ref):
        if ref in seen:
            return
        seen.add(ref)
        for kind, cid, _, _ in products[ref].children:
            if kind == "node":
                visit(cid)
        out.append(ref)
    visit(root_ref)
    return out


# ---------------------------------------------------------------- per part: what it becomes
def file_sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


SCREW_STDS = ("DIN 912", "DIN 933", "DIN 7984", "DIN 7985", "ISO 7380", "ISO 10642")
FASTENER = [   # (regex on the client's name, template, the template stds it may be); size / length: the name, else the geometry
    (re.compile(r"4762|ISO\s*4762|DIN\s*912", re.I), "screw", ("DIN 912",)),
    (re.compile(r"24017|ISO\s*4017|DIN\s*933", re.I), "screw", ("DIN 933",)),
    (re.compile(r"thin.*cap screw|DIN\s*7984", re.I), "screw", ("DIN 7984", "DIN 912")),
    (re.compile(r"ISO\s*-?\s*4032", re.I), "nut", ("ISO 4032", "DIN 934")),
    (re.compile(r"hex nut gradec|ISO\s*-?\s*4034", re.I), "nut", ("ISO 4034", "ISO 4032", "DIN 934")),
    (re.compile(r"washer|" + I.words("washer"), re.I), "washer", ("DIN 125", "DIN 433", "DIN 9021")),
    (re.compile(r"\bnut\b|" + I.words("nut"), re.I), "nut", ("DIN 934", "ISO 4032", "ISO 4034", "DIN 985")),
    (re.compile(r"screw|bolt|" + I.words("screw_or_bolt"), re.I), "screw", SCREW_STDS),
]
JOINT_OF = {"screw": "head", "nut": "seat", "washer": "seat"}


def fastener_of(part):
    """A standard fastener the framework templates draw: (template, stds, size or None, length or None), or None."""
    if part.get("kind") != "standard":
        return None
    nm = part["name"]
    for rx, tpl, stds in FASTENER:
        if rx.search(nm):
            m = re.search(r"\bM(\d+(?:[.,]\d+)?)\s*[x\u00d7" + I.like("x") + r"]\s*(\d+(?:[.,]\d+)?)", nm, re.I)
            m1 = re.search(r"\bM(\d+(?:[.,]\d+)?)\b", nm, re.I)
            size = f"M{(m or m1).group(1).replace(',', '.')}" if (m or m1) else None
            length = float(m.group(2).replace(",", ".")) if m else None
            return tpl, stds, size, length
    return None


def analyse_part(part, step_file, cache_dir, recreate_sha):
    """-> {"kind": sheet|turned|prism|client (made) | fastener|vendor (bought), ...}; cached by the STEP and recreate.py."""
    key = hashlib.sha256(f"{CACHE_V}|{file_sha(step_file)}|{recreate_sha}|{json.dumps(part, sort_keys=True)}".encode()).hexdigest()[:24]
    cf = cache_dir / f"{key}.json"
    if cf.is_file():
        return json.loads(cf.read_text())
    out = {"id": part["id"]}
    if part.get("kind") == "made":
        r = recreate_within(step_file, part["name"], RECREATE_BUDGET_S)
        if r["kind"]:
            out.update(kind=r["kind"], code=r["code"], params=r["params"], compare=r.get("compare"))
        else:
            out.update(kind="client", reason=r["reason"])
            if r.get("transient"):                # a timeout or a crash says nothing about the part: try again next run
                return out
    else:
        from build123d import import_step
        fa = fastener_of(part)
        fit = fastener_fit(import_step(str(step_file)), fa) if fa else None
        if fit and fit.get("ok"):
            out.update(kind="fastener", template=fit["template"], args=fit["args"], frame=fit["frame"], fit=fit["check"])
        else:
            out.update(kind="vendor", reason=(fit or {}).get("reason", "") or "no framework template for it")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cf.write_text(json.dumps(out, default=float))
    return out


RECREATE_BUDGET_S = 900   # one part's recognition; OCC can stall for hours on engraved lettering


def _recreate_child(step_file, name, conn):
    from build123d import import_step
    import recreate as RC
    conn.send(RC.recreate(import_step(str(step_file)), name))


def recreate_within(step_file, name, budget):
    """recreate.recreate on the part's STEP in a child process; a part that takes longer than `budget` s stays on the client's
    geometry with that reason."""
    import multiprocessing as mp
    ctx = mp.get_context("spawn")
    recv, send = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_recreate_child, args=(str(step_file), name, send), daemon=True)
    proc.start()
    send.close()                                  # the child holds the only writer: its death ends the wait at once
    try:
        r = recv.recv() if recv.poll(budget) else None
        why = f"recognition did not finish in {budget} s"
    except EOFError:
        r, why = None, "the recogniser crashed"
    proc.join(5)
    if proc.is_alive():
        proc.kill()
    if r is None and proc.exitcode not in (None, 0):
        why += f" (exit code {proc.exitcode})"
    return r or {"kind": None, "reason": why, "transient": True}


def analysis_sha():
    """What a part's analysis depends on: recreate.py, the shape and fastener templates, and the fastener code here (not
    the rest of this file: writing the project changes no analysis)."""
    import inspect
    h = hashlib.sha256()
    for f in ("recreate.py",):
        h.update(file_sha(HERE / f).encode())
    for t in ("recreated", "screw", "nut", "washer"):
        h.update(file_sha(TEMPLATES / f"{t}.py").encode())
    for fn in (fastener_of, fastener_fit, _template):
        h.update(inspect.getsource(fn).encode())
    h.update(repr([(r.pattern, t, st) for r, t, st in FASTENER]).encode())
    return h.hexdigest()


def _template(name):
    if str(TEMPLATES) not in sys.path:
        sys.path.insert(0, str(TEMPLATES))
    import importlib
    return importlib.import_module(name)


def fastener_fit(shape, fa):
    """The template variant and its frame (in the client's part coordinates) that draw the same fastener: the axis is the
    one most of its cylinder area turns about, the head is at the wider end, and the variant whose bounding box comes
    closest wins if it is within 0.6 mm or 6 % (and its centre as close)."""
    from build123d import GeomType
    tpl, stds, size, length = fa
    T = _template(tpl)
    table = {"screw": getattr(T, "HEADS", {}), "nut": getattr(T, "NUTS", {}), "washer": getattr(T, "WASHERS", {})}[tpl]
    cyls = [f for f in shape.faces() if f.geom_type == GeomType.CYLINDER]
    if not cyls:
        return {"ok": False, "reason": f"{tpl}: no cylinder, so no axis"}
    by_dir = {}
    for f in cyls:
        d = np.array(tuple(f.axis_of_rotation.direction))
        d = d if max(d, key=abs) > 0 else -d
        k = tuple(np.round(d, 4))
        by_dir[k] = by_dir.get(k, 0) + f.area
    d = np.array(max(by_dir, key=by_dir.get), dtype=float)
    d /= np.linalg.norm(d)
    co = [f for f in cyls if abs(abs(float(np.dot(np.array(tuple(f.axis_of_rotation.direction)), d))) - 1) < 1e-4]
    o = np.array(tuple(co[0].axis_of_rotation.position))
    verts = np.array([tuple(v) for v in shape.vertices()] + [tuple(f.center()) for f in shape.faces()])
    proj = (verts - o) @ d
    lo, hi = float(proj.min()), float(proj.max())
    radial = np.linalg.norm((verts - o) - np.outer(proj, d), axis=1)
    band = 0.2 * (hi - lo)
    r_lo = radial[proj < lo + band].max(initial=0)
    r_hi = radial[proj > hi - band].max(initial=0)
    holes = [f.radius for f in co]
    bb = shape.bounding_box()
    dims_c = [bb.size.X, bb.size.Y, bb.size.Z]         # per axis of his part: a hex turned 30° has the same sorted box
    best = None
    for std in stds:
        if std not in table:
            continue
        sizes = [size] if size else sorted(table[std], key=lambda s_: abs(float(s_[1:].split("x")[0]) - 2 * min(holes)))[:2]
        for sz in sizes:
            if sz not in table[std]:
                continue
            if tpl == "screw":
                head_hi = r_hi >= r_lo
                k = table[std][sz][1]
                csk = std == "ISO 10642"
                bearing = (hi - (0 if csk else k)) if head_hi else (lo + (0 if csk else k))
                L = length or round(abs((lo if head_hi else hi) - bearing) + (k if csk else 0), 1)
                zdir = d if head_hi else -d
                args = {"size": sz, "length": float(L), "std": std}
                origin = o + d * bearing
            else:
                zdir, origin = d, o + d * lo
                args = {"size": sz, "std": std}
            try:
                built = T.build(**args)["shape"]
            except Exception:                       # noqa: BLE001  e.g. a csk screw shorter than its head
                continue
            for ang in range(0, 60, 3):             # hex heads: the turn about the axis that lines the flats up
                x0 = np.cross(zdir, [0, 0, 1.0]) if abs(zdir[2]) < 0.9 else np.cross(zdir, [1.0, 0, 0])
                x0 /= np.linalg.norm(x0)
                y0 = np.cross(zdir, x0)
                a = math.radians(ang)
                x = math.cos(a) * x0 + math.sin(a) * y0
                F = np.eye(4)
                F[:3, 0], F[:3, 1], F[:3, 2], F[:3, 3] = x, np.cross(zdir, x), zdir, origin
                pb = built.moved(from_np(F)).bounding_box()
                dims_t = [pb.size.X, pb.size.Y, pb.size.Z]
                off = max(abs(a_ - b_) for a_, b_ in zip(dims_c, dims_t))
                ctr = float(np.linalg.norm(np.array(tuple(bb.center())) - np.array(tuple(pb.center()))))
                err = max(off, ctr)
                if best is None or err < best[0] - 1e-9:
                    best = (err, off, ctr, args, F)
                if tpl != "nut" and not (tpl == "screw" and args["std"] == "DIN 933"):
                    break                           # round heads: the turn does not matter
    if best is None:
        return {"ok": False, "reason": f"{tpl}: no variant of {', '.join(stds)} in size {size or '?'} in the framework template"}
    err, off, ctr, args, F = best
    tol = max(0.6, 0.06 * max(dims_c))
    check = {"bbox_dims_mm": round(off, 3), "centre_mm": round(ctr, 3), "tol_mm": round(tol, 3)}
    ok = err <= tol
    spec = " ".join(f"{v:g}" if isinstance(v, float) else str(v) for v in args.values())
    return {"ok": ok, "template": tpl, "args": args, "frame": F.reshape(-1).tolist(), "check": check,
            "reason": "" if ok else f"closest {tpl} {spec} is off by {off:.2f} mm in size, {ctr:.2f} mm in place (tolerance {tol:.2f})"}


# ---------------------------------------------------------------- docking
def _exact_contact(a, b):
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    d = BRepExtrema_DistShapeShape(a, b)
    if not d.IsDone() or d.NbSolution() == 0:
        return float("inf"), None
    p = d.PointOnShape1(1)
    return d.Value(), np.array([p.X(), p.Y(), p.Z()])


def _face_boxes(s, cache):
    """(faces, their boxes [n, 6], vertices [m, 3]) of a placed shape, once per shape object."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    import recreate as RC
    hit = cache.get(id(s))
    if hit is not None and hit[0] is s:
        return hit[1]
    faces = RC._faces(s.wrapped)
    boxes = np.empty((len(faces), 6))
    for k, f in enumerate(faces):
        bx = Bnd_Box()
        BRepBndLib.Add_s(f, bx, False)
        boxes[k] = (*RC._v(bx.CornerMin()), *RC._v(bx.CornerMax()))
    verts = np.array([(v.X, v.Y, v.Z) for v in s.vertices()]).reshape(-1, 3)
    cache[id(s)] = (s, (faces, boxes, verts))
    return faces, boxes, verts


def _inside(solid, shape):
    """Is a vertex of `shape` inside `solid` (OCC counts a part within another's material as touching it)?"""
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_IN, TopAbs_VERTEX
    from OCP.TopExp import TopExp_Explorer
    from OCP.BRep import BRep_Tool
    from OCP.TopoDS import TopoDS
    ex = TopExp_Explorer(shape, TopAbs_VERTEX)
    if not ex.More():
        return False
    p = BRep_Tool.Pnt_s(TopoDS.Vertex(ex.Current()))
    c = BRepClass3d_SolidClassifier(solid, p, 1e-7)
    return c.State() == TopAbs_IN


FACE_PAIRS = 100
MESH_FACES = 2000           # over this many faces between the two (a drum of ~6000 holes) any exact measure takes hours
MESH_EDGE = 5.0             # mm: the mesh path's longest triangle edge


def _mesh(s, cache):
    """(nodes [n, 3], triangles [m, 3, 3], KD tree of the triangles' centroids) of a placed shape's 0.2 mm mesh, once per
    shape object. The mesh is made on a copy, so the part's own shape keeps whatever mesh it had."""
    from scipy.spatial import cKDTree
    from OCP.BRep import BRep_Tool
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopLoc import TopLoc_Location
    import recreate as RC
    hit = cache.get(("mesh", id(s)))
    if hit is not None and hit[0] is s:
        return hit[1]
    w = BRepBuilderAPI_Copy(s.wrapped).Shape()
    BRepMesh_IncrementalMesh(w, 0.2, False, 0.5, True)
    nodes, tris = [], []
    for f in RC._faces(w):
        loc = TopLoc_Location()
        t = BRep_Tool.Triangulation_s(f, loc)
        if t is not None:
            tr = loc.Transformation()
            nd = np.array([RC._v(t.Node(i).Transformed(tr)) for i in range(1, t.NbNodes() + 1)])
            nodes.append(nd)
            tris.append(nd[np.array([t.Triangle(i).Get() for i in range(1, t.NbTriangles() + 1)]) - 1])
    tris = np.concatenate(tris) if tris else np.zeros((0, 3, 3))
    for _ in range(10):                                # a large flat face is a few huge triangles: split them to
        edge = np.linalg.norm(tris - np.roll(tris, 1, axis=1), axis=2).max(1)   # MESH_EDGE, so a point's nearest
        big = edge > MESH_EDGE                         # centres always include the triangle right under it
        if not big.any():
            break
        a, b, c = tris[big, 0], tris[big, 1], tris[big, 2]
        ab, bc, ca = (a + b) / 2, (b + c) / 2, (c + a) / 2
        tris = np.concatenate([tris[~big], np.stack([a, ab, ca], 1), np.stack([ab, b, bc], 1), np.stack([ca, bc, c], 1),
                               np.stack([ab, bc, ca], 1)])
    nodes = tris.reshape(-1, 3)
    out = (nodes, tris, cKDTree(tris.mean(1)) if len(tris) else None)
    cache[("mesh", id(s))] = (s, out)
    return out


def _mesh_contact(a, b, cache):
    """(distance, point on a) between two parts' meshes: each one's nodes to the other's triangles (the 12 whose centres are
    nearest; no triangle edge over MESH_EDGE, so the one under a node is among them). Within about the mesh deflection
    (0.2 mm) of the true distance either way, over by at most about MESH_EDGE where a node misses the nearest point."""
    import recreate as RC
    na, ta, ka = _mesh(a, cache)
    nb, tb, kb = _mesh(b, cache)
    if not len(na) or not len(nb):
        return _exact_contact(a.wrapped, b.wrapped)
    best, pt = float("inf"), None
    for src, dst, tree, from_a in ((na, tb, kb, True), (nb, ta, ka, False)):
        k = min(12, len(dst))
        _, idx = tree.query(src, k=k)
        idx = idx.reshape(len(src), -1)
        d = np.concatenate([RC._point_tri(src[s:s + 20000], dst[idx[s:s + 20000]]).min(1) for s in range(0, len(src), 20000)])
        i = int(np.argmin(d))
        if d[i] < best:
            best = float(d[i])
            pt = src[i].copy() if from_a else na[int(np.argmin(np.linalg.norm(na - src[i], axis=1)))].copy()
    return best, pt


def contact(a, b, cache=None):
    """(distance, point on a) between two placed shapes, as OCC's BRepExtrema between the whole shapes measures it. Parts
    whose boxes overlap are measured face pair by face pair in the order of their boxes' gap, from the nearest two vertices' distance down, stopping when no
    box left is nearer than the best distance found (BRepExtrema between two parts of thousands of faces took minutes: rows
    of pinned rollers made a port run for hours). Where a vertex of one lies inside the other's solid, OCC's whole-shape measure
    decides, as before. Two parts of over MESH_FACES faces together are measured on their 0.2 mm meshes (_mesh_contact),
    within about the mesh deflection under and a few mm over the true distance. That distance only chooses what a part
    docks onto; its place is the client's either way."""
    from scipy.spatial import cKDTree
    cache = {} if cache is None else cache
    fa, ba, va = _face_boxes(a, cache)
    fb, bb, vb = _face_boxes(b, cache)
    if len(fa) + len(fb) > MESH_FACES:                 # a perforated drum: measured on the meshes
        return _mesh_contact(a, b, cache)
    if bbox_gap(a, b, cache) > 0.0:                   # apart: one BRepExtrema is quick; the cost is in parts that touch
        return _exact_contact(a.wrapped, b.wrapped)
    if not fa or not fb or not len(va) or not len(vb):
        return _exact_contact(a.wrapped, b.wrapped)
    gap = np.sqrt((np.maximum(0.0, np.maximum(ba[:, None, :3] - bb[None, :, 3:], bb[None, :, :3] - ba[:, None, 3:])) ** 2).sum(-1))
    order = np.argsort(gap, axis=None)
    dv, iv = cKDTree(vb).query(va)                     # the nearest two vertices: a distance the minimum cannot exceed
    k0 = int(np.argmin(dv))
    best, pt = float(dv[k0]), va[k0].copy()           # stays the answer when no face pair comes nearer
    for n, k in enumerate(order):
        i, j = divmod(int(k), len(fb))
        if gap[i, j] >= best:
            break
        if n == FACE_PAIRS:                            # near but not touching: one BRepExtrema is quicker than more pairs
            return _exact_contact(a.wrapped, b.wrapped)
        d, q = _exact_contact(fa[i], fb[j])
        if q is not None and d < best:
            best, pt = d, q
            if best <= 0.0:
                break
    if best > 0.0:
        for x, y in ((a, b), (b, a)):
            if any(_inside(sol.wrapped, y.wrapped) for sol in x.solids()):
                return _exact_contact(a.wrapped, b.wrapped)   # rare: OCC's answer on the whole shapes, as before
    return best, pt


def bbox_gap(a, b, cache=None):
    """The gap between two placed shapes' tight boxes; with `cache` each shape's box is computed once (a tight box of a part
    of thousands of B-spline faces takes seconds, and the docking asks for it for every pair)."""
    def box(s):
        if cache is None:
            return s.bounding_box()
        hit = cache.get(("box", id(s)))
        if hit is None or hit[0] is not s:
            hit = cache[("box", id(s))] = (s, s.bounding_box())
        return hit[1]
    ba, bb = box(a), box(b)
    g = [max(0.0, max(ba.min.X - bb.max.X, bb.min.X - ba.max.X)), max(0.0, max(ba.min.Y - bb.max.Y, bb.min.Y - ba.max.Y)),
         max(0.0, max(ba.min.Z - bb.max.Z, bb.min.Z - ba.max.Z))]
    return math.sqrt(sum(x * x for x in g))


# ---------------------------------------------------------------- main
def convert(import_dir, root, title=None, overrides=None, log=print, library=None, node=None):
    """See the module doc. Returns the report it writes to mechanics/cad/migration/report.json. node: the folder (under
    mechanics/cad/parts/) of an existing node of the project: the STEP is a node of the client's machine, ported into one new
    folder under it and nothing else of the project is touched (the agent docks it, see migration/<node>/placement.json)."""
    from build123d import import_step
    import_dir, root = Path(import_dir).resolve(), Path(root).resolve()
    cad = root / "mechanics" / "cad"
    if node is not None:
        node = str(node).strip("/")
        if not node or not (cad / "parts" / node / "__init__.py").is_file():
            raise SystemExit(f"--node {node}: not a node of this project (no mechanics/cad/parts/{node}/__init__.py)")
    elif not (cad / "migration" / "generated.json").is_file():   # a first port writes the whole parts/: never over files
        mine = sorted(str(f.relative_to(cad)) for f in (cad / "parts").rglob("*")   # it did not write (a scaffold's example)
                      if f.is_file() and "__pycache__" not in f.parts) if (cad / "parts").is_dir() else []
        if mine:
            raise SystemExit(f"mechanics/cad/parts/ already holds {len(mine)} file(s) this converter did not write "
                             f"({', '.join(mine[:5])}{', ...' if len(mine) > 5 else ''}): a first port writes the whole parts/. "
                             "Move them away (a scaffold's example parts: delete them), or port into an existing node with --node.")
    pj = json.loads((import_dir / "parts.json").read_text(encoding="utf-8"))
    parts = {p["id"]: p for p in pj["parts"]}
    rows = {}                                         # the purchase row / process fields of the overrides: they go into the
    if overrides:                                     # part file's P(...) only, never into `parts` (the analysis cache key)
        ov = {k: v for k, v in json.loads(Path(overrides).read_text(encoding="utf-8")).items() if not k.startswith("_")}
        for pid, o in ov.items():
            if pid in parts:
                parts[pid].update({k: o[k] for k in ("type", "material") if k in o})
                rows[pid] = {k: o[k] for k in ROW_FIELDS if k in o}
    products, order, root_ref, root_m = load_tree(import_dir)
    in_machine = False
    if node is not None:                              # a machine export holding just the node: the node is what is inside
        while len(products[root_ref].children) == 1 and products[root_ref].children[0][0] == "node":
            in_machine = True
            _, cid, m, _ = products[root_ref].children[0]
            root_ref, root_m = cid, root_m @ m
        inside = set(bottom_up(products, root_ref))
        order = [r for r in order if r in inside]
        keep = {cid for r in inside for kind, cid, _, _ in products[r].children if kind == "part"}
        parts = {pid: p for pid, p in parts.items() if pid in keep}
        # an id the project already has (a fastener of the machine, the node it replaces) would be a second part of that id: the
        # model refuses to build. The node's copy gets the next free id; name and drawing number stay his
        taken = project_part_ids(cad, node_folder(cad, node, products[root_ref].name))
        new = {}
        for pid in sorted(parts):
            if pid in taken:
                k = 2
                while f"{pid}_{k}" in taken or f"{pid}_{k}" in parts:
                    k += 1
                new[pid] = f"{pid}_{k}"
                taken.add(new[pid])
                log(f"[step_to_project] {pid}: the project already has this id, the node's part is {new[pid]}")
        if new:
            parts = {new.get(pid, pid): (dict(p, id=new[pid], his_id=pid) if pid in new else p) for pid, p in parts.items()}
            rows = {new.get(pid, pid): r for pid, r in rows.items()}
            for r in inside:
                products[r].children = [(kind, new.get(cid, cid) if kind == "part" else cid, m, iname)
                                        for kind, cid, m, iname in products[r].children]
    cache_dir = root / "build" / "step_to_project"
    recreate_sha = analysis_sha()

    # ---- what each part becomes
    info = {}
    for i, pid in enumerate(parts):
        info[pid] = analyse_part(parts[pid], step_of(import_dir, parts[pid]), cache_dir, recreate_sha)
        log(f"[step_to_project] {i + 1}/{len(parts)} {pid}: {info[pid]['kind']}")
    if node is not None:                              # the project's own fastener templates are kept: one older than the
        for pid, inf in list(info.items()):           # framework's may not know the variant (a nut std added since)
            tf = cad / "parts" / "fasteners" / f"{inf.get('template')}.py"
            if inf["kind"] == "fastener" and tf.is_file():
                text = tf.read_text(encoding="utf-8")
                miss = [v for v in inf["args"].values() if isinstance(v, str) and json.dumps(v) not in text]
                if miss:
                    info[pid] = {"kind": "vendor", "reason": f"the project's parts/fasteners/{inf['template']}.py does not know "
                                 f"{' '.join(miss)} (an older copy of the template): the maker's 3D"}
                    log(f"[step_to_project] {pid}: {info[pid]['reason']}")

    # ---- folders: one per distinct product, under the folder of the first product that uses it; a part in the first
    # folder that uses it
    used = {}
    for ref in order:
        p = products[ref]
        if ref == root_ref:
            p.folder = node_folder(cad, node, p.name) if node else slug(p.name, "machine")
            continue
        parent = products[p.parents[0]]
        p.folder = parent.folder + "/" + unique(slug(p.name, "node"), used.setdefault(parent.folder, set(RESERVED)))
    part_home, part_mod = {}, {}
    for ref in order:
        for kind, cid, _, _ in products[ref].children:
            if kind == "part" and cid not in part_home:
                part_home[cid] = products[ref].folder
                part_mod[cid] = unique(slug(parts[cid]["name"]), used.setdefault(products[ref].folder, set(RESERVED)))

    def own_frame(pid):
        """The frame a template fastener docks by (its template joint, in the client's part coordinates), else None."""
        return mat(info[pid]["frame"]) if info[pid]["kind"] == "fastener" else None

    shapes = {}

    def own_shape(pid):
        if pid not in shapes:
            shapes[pid] = import_step(str(step_of(import_dir, parts[pid])))
        return shapes[pid]

    def anchor_part(ref):
        """(part id, its matrix in the product's coordinates) of the part the product is docked by."""
        p = products[ref]
        kind, cid, m, _ = p.children[p.anchor]
        if kind == "part":
            return cid, m
        sp, sm = anchor_part(cid)
        return sp, m @ sm

    def rep(p, i):
        kind, cid, m, _ = p.children[i]
        if kind == "part":
            return cid, m
        sp, sm = anchor_part(cid)
        return sp, m @ sm

    touched = {}                                       # (part j, part i, i in j's coordinates) -> (distance, point in j's)
    # ---- docking inside each product (every instance of a product is the same inside): a tree from the anchor (the
    # largest made part), each child onto the nearest part already docked; a fastener holds nothing unless nothing else is near
    up = bottom_up(products, root_ref)
    for k_ref, ref in enumerate(up):
        p = products[ref]
        n = len(p.children)
        log(f"[step_to_project] docking {k_ref + 1}/{len(up)} {p.name} ({n})")
        if not n:
            raise SystemExit(f"«{p.name}» holds no part: a STEP to port is an assembly (a single part goes into a node "
                             "of the project as a part file, by hand)")
        holders = [i for i in range(n) if p.children[i][0] == "node" or info[p.children[i][1]]["kind"] != "fastener"]
        made = [i for i in holders if p.children[i][0] == "part" and parts[p.children[i][1]].get("kind") == "made"]
        pool = made or holders or list(range(n))
        p.anchor = max(pool, key=lambda i: (parts[rep(p, i)[0]].get("volume_mm3") or 0, -i))
        placed, surf = {}, {}

        def shp(i, p=p, placed=placed):
            if i not in placed:
                pid, m = rep(p, i)
                placed[i] = own_shape(pid).moved(from_np(m))
            return placed[i]

        def touch(j, i, p=p, shp=shp, surf=surf):
            """contact(j, i), once per pair of parts in the same relative place (a row of rollers, of screws): the point is
            kept in j's own coordinates."""
            (pj, mj), (pi, mi) = rep(p, j), rep(p, i)
            key = (pj, pi, tuple(np.round(inv(mj) @ mi, 6).reshape(-1)))
            if key not in touched:
                dist, pt = contact(shp(j), shp(i), surf)
                touched[key] = (dist, None if pt is None else (inv(mj) @ np.append(pt, 1.0))[:3])
            dist, lp = touched[key]
            return dist, None if lp is None else (mj @ np.append(lp, 1.0))[:3]
        left = [i for i in range(n) if i != p.anchor]
        best = {i: (float("inf"), None, None, None) for i in left}     # (score = gap + penalty, onto, point, gap)
        frontier = [p.anchor]
        while left:
            for j in frontier:
                penalty = 0.0 if j in holders else 1000.0
                for i in left:
                    g = bbox_gap(shp(i), shp(j), surf)
                    if g + penalty >= best[i][0]:
                        continue
                    if g < 5:
                        dist, pt = touch(j, i)
                    else:
                        dist, pt = g, None
                    if dist + penalty < best[i][0] - 1e-9:
                        best[i] = (dist + penalty, j, pt, dist)
            i = min(left, key=lambda k: (best[k][0], k))
            score, j, pt, dist = best[i]
            if pt is None:
                dist, pt = touch(j, i)
            if pt is None:                                 # no geometry to measure (an empty leaf): dock at its own origin
                pt = rep(p, i)[1][:3, 3]
            pid_i, m_i = rep(p, i)
            if own_frame(pid_i) is not None:              # a template fastener: its own joint frame, where it stands
                F = m_i @ own_frame(pid_i)
            else:                                          # on the surface of the part it docks to, axes of the docked part
                F = np.eye(4)
                F[:3, :3] = m_i[:3, :3]
                F[:3, 3] = pt
            p.dock[i] = (j, F, dist)
            left.remove(i)
            frontier = [i]

    for ref in (r for r in products if products[r].anchor is not None):   # a node stands in its anchor part's frame
        products[ref].anchor_m = anchor_part(ref)[1]
    # ---- the joints each part file declares (own coordinates); the same frame keeps one label
    joints = {pid: {} for pid in parts}

    def joint(pid, m, prefix):
        js = joints[pid]
        for lab, mm in js.items():
            if (lab == prefix or lab.startswith(prefix + "_")) and np.allclose(mm, m, atol=1e-7):
                return lab
        lab = unique(prefix, set(js)) if prefix in js else prefix
        js[lab] = m
        return lab

    for pid, inf in info.items():                     # a template fastener always carries its template joint
        if inf["kind"] == "fastener":
            joints[pid][JOINT_OF[inf["template"]]] = mat(inf["frame"])
    plans = {}
    for ref in order:
        p = products[ref]
        plan = []
        for i in range(len(p.children)):
            if i == p.anchor:
                plan.append(None)
                continue
            j, F, dist = p.dock[i]
            pj_id, pj_m = rep(p, j)
            ci_id, ci_m = rep(p, i)
            plab = joint(pj_id, inv(pj_m) @ F, "to_" + part_mod[ci_id])
            clab = JOINT_OF[info[ci_id]["template"]] if info[ci_id]["kind"] == "fastener" else joint(ci_id, inv(ci_m) @ F, "dock")
            plan.append((j, plab, clab, dist))
        plans[ref] = plan

    return write_project(root, cad, title or products[root_ref].name, parts, info, products, order, root_ref, root_m,
                         part_home, part_mod, joints, plans, import_dir, library, node, rows, in_machine)


def _P_fields(part, inf, lang=I.DEFAULT):
    """The geom.P(...) keywords of a part besides name, shape and colour: its process and catalogue fields (the material
    and the unit in the project's language `lang`)."""
    name = part["name"]
    mat_ = part.get("material")
    f = {"title": name}
    if part.get("kind") == "made":
        if part.get("drawing"):
            f["code"] = part["drawing"]
        if mat_ == "printed":
            f.update(process="print", material=I.t("material.printed", lang))
        elif re.search(I.words("polyamide"), name, re.I):
            f.update(process="other", material=I.t("material.polyamide", lang))
        elif inf["kind"] == "sheet":
            f.update(process="bent" if "bend(" in inf["code"] else "laser", material=I.t("material.steel", lang),
                     thickness=inf["params"]["thickness"])
        elif inf["kind"] == "turned":
            f.update(process="other", material=I.t("material.steel", lang))   # a turned part: its STEP (the kits know print|laser|bent|other)
        else:
            f.update(process="other", material=I.t("material.steel", lang))
        return f
    kind = TYPE_KIND.get(part.get("type")) or KIND.get(part.get("material"), "other")
    f.update(process="bought", kind=kind, desc=name, unit=I.t("unit.pcs", lang), code=part.get("drawing") or "")
    if part.get("type") or part.get("material"):
        f["class"] = part.get("type") or part.get("material")
    if inf["kind"] == "fastener":                     # the row orders the standard part: its code and its trade name
        tp = _template(inf["template"]).build(**inf["args"])
        f.update(kind="fasteners", code=tp["code"], desc=tp["desc"])
    return f


def _py(v):
    return json.dumps(v, ensure_ascii=False) if isinstance(v, str) else repr(v)


# what an --overrides entry may set on a part besides type / material (those two are the import's classes and steer the
# recognition): geom.P fields as they are (bought rows, processes)
ROW_FIELDS = ("process", "thickness", "code", "desc", "link", "supplier", "unit", "class", "kind", "qty", "counted_in",
              "note")
GROUP_OF_TYPE = {"belt": "belts", "conveyor_belt": "belts", "cable_channel": "electrical", "din_rail": "electrical"}


def step_sha(path):
    """The hash of a STEP's DATA section: the same geometry cut again (a new FILE_NAME timestamp) hashes the same."""
    data = Path(path).read_bytes()
    i = data.find(b"\nDATA;")
    return hashlib.sha256(data[i:] if i >= 0 else data).hexdigest()


def library_part(lib_root, part, inf_kind, step_file, lang=I.DEFAULT):
    """Write a bought part as a part of the client's library (his level, shared by his projects; never the framework's
    catalogue, which every client sees) at <lib_root>/library/<group>/<module>.py with its STEP beside it; -> (group, module,
    code). The same STEP already there under that module is reused; another one gets its own module."""
    f = _P_fields(part, {"kind": inf_kind}, lang)
    group = GROUP_OF_TYPE.get(part.get("type")) or f["kind"]
    gdir = Path(lib_root) / "library" / group
    gdir.mkdir(parents=True, exist_ok=True)
    sha = step_sha(step_file)
    base = slug(part["name"])
    mod, k = base, 2
    while ((gdir / f"{mod}.py").is_file() or (gdir / f"{mod}.step").is_file()) \
            and not ((gdir / f"{mod}.step").is_file() and step_sha(gdir / f"{mod}.step") == sha):
        mod, k = f"{base}_{k}", k + 1
    if not (gdir / f"{mod}.step").is_file():
        shutil.copyfile(step_file, gdir / f"{mod}.step")
    art = re.search(r"\b(?=[A-Z0-9-]*\d)(?=[A-Z0-9-]*[A-Z])[A-Z0-9][A-Z0-9-]{4,}\b", part["name"])
    code = art.group(0) if art else ""
    doc = (f"{_doc(part['name'])} - a BOUGHT part of the client's library (his level), as it is sold.\n"
           "Geometry: the 3D the client's CAD carries for it (ported by step-import step_to_project.py), "
           f"copied unchanged into {mod}.step beside this module: every size is in that STEP, no table here.\n"
           "Ports: `origin` - the part's own frame as that 3D has it; a project docks it with joints of its own part file.")
    text = (f'"""{doc}\n"""\nimport os\n\nfrom build123d import Location, RigidJoint, import_step\nfrom geom import P\n\n'
            f"CODE = {_py(code)}\nVENDOR = {{CODE or {_py(mod)}: {_py(mod + '.step')}}}\n\n\n"
            "def build():\n"
            "    shape = import_step(os.path.join(os.path.dirname(os.path.abspath(__file__)), VENDOR[CODE or "
            f"{_py(mod)}]))\n"
            '    RigidJoint("origin", shape, Location())\n'
            f"    return P({_py(mod)}, shape, {_py(part.get('color') or '#9aa5b1')}, process=\"bought\", kind={_py(f['kind'])}, code=CODE,\n"
            f"             desc={_py(part['name'])}, title={_py(part['name'])}, unit={_py(f['unit'])})\n")
    if not (gdir / f"{mod}.py").is_file():         # an existing module (his other project, a code added by hand) stays
        (gdir / f"{mod}.py").write_text(text, encoding="utf-8")
    return group, mod, code


def write_project(root, cad, title, parts, info, products, order, root_ref, root_m, part_home, part_mod, joints, plans, import_dir,
                  library=None, node=None, rows=None, in_machine=False):
    import recreate as RC
    from build123d import import_step
    base = products[root_ref].folder
    nslug = node_key(base)
    vend = f"vendor/client/{nslug}" if node else "vendor/client"     # node mode: only the node's own folders are rewritten
    mig = f"migration/{nslug}" if node else "migration"
    pdir = cad / "parts" / base if node else cad / "parts"
    # parts/ and vendor/client/ are rewritten, except what the agent wrote or changed there since the last run (a part drawn by
    # hand, a helper, a node of its own): migration/generated.json holds the hash of every file this converter wrote
    gen_f = cad / mig / "generated.json"
    old_gen = json.loads(gen_f.read_text(encoding="utf-8")) if gen_f.is_file() else {}
    kept = {}
    for d in (pdir, cad / vend):
        for f in sorted(d.rglob("*")) if d.is_dir() else []:
            rel = str(f.relative_to(cad))
            if f.is_file() and "__pycache__" not in f.parts and old_gen.get(rel) != file_sha(f):
                kept[rel] = f.read_bytes()
    generated = {}
    # parts/ and vendor/client/ are written into a staging folder and swapped in only when the whole run went through: a
    # run that fails half way (OCC, memory, ^C) leaves the project as it was
    stage = root / "build" / "port_stage"             # outside the project's files: a killed run leaves nothing in them
    if stage.exists():
        shutil.rmtree(stage)
    STAGED = (str(pdir.relative_to(cad)) + "/", vend + "/")

    def at(rel):
        return (stage if rel.startswith(STAGED) else cad) / rel

    late = {}                                         # the files outside parts/ and vendor/client/: written after the swap

    def put(rel, text):
        if not rel.startswith(STAGED):
            late[rel] = text
            generated[rel] = hashlib.sha256(text.encode("utf-8")).hexdigest()
            return
        f = at(rel)
        f.parent.mkdir(parents=True, exist_ok=True)
        if not f.is_file() or f.read_text(encoding="utf-8") != text:
            f.write_text(text, encoding="utf-8")
        generated[rel] = file_sha(f)

    def flush():
        for rel, text in late.items():
            f = cad / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            if not f.is_file() or f.read_text(encoding="utf-8") != text:
                f.write_text(text, encoding="utf-8")

    try:
        return _write_project_staged(root, cad, title, parts, info, products, order, root_ref, root_m, part_home, part_mod, joints,
                                     plans, import_dir, library, RC, import_step, pdir, gen_f, old_gen, kept, generated, stage,
                                     STAGED, at, put, node, vend, mig, rows or {}, flush, in_machine)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def _write_project_staged(root, cad, title, parts, info, products, order, root_ref, root_m, part_home, part_mod, joints, plans,
                          import_dir, library, RC, import_step, pdir, gen_f, old_gen, kept, generated, stage, STAGED, at, put,
                          node=None, vend="vendor/client", mig="migration", row_fields=None, flush=lambda: None,
                          in_machine=False):
    lang = I.project_lang(root)       # the rows' material and unit are written in the project's language
    (stage / vend).mkdir(parents=True, exist_ok=True)
    for rel, tpl in (("geom.py", "geom"), ("recreated.py", "recreated")):   # the project's own copies stay
        if not (cad / rel).is_file() or (not node and old_gen.get(rel) == file_sha(cad / rel)):
            put(rel, (TEMPLATES / f"{tpl}.py").read_text(encoding="utf-8").replace("{machine}", _doc(title)))
    tpls = sorted({i["template"] for i in info.values() if i["kind"] == "fastener"})
    for tpl in tpls:
        if not node or not (cad / f"parts/fasteners/{tpl}.py").is_file():
            put(f"parts/fasteners/{tpl}.py", (TEMPLATES / f"{tpl}.py").read_text(encoding="utf-8").replace("{machine}", _doc(title)))
    if tpls and not (node and (cad / "parts/fasteners/__init__.py").is_file()):
        put("parts/fasteners/__init__.py", '"""Standard fasteners (framework templates). The project\'s part files of its screws, nuts and '
            'washers build them; this node places nothing."""\n\n\ndef build():\n    return []\n')
    if not (cad / "params.py").is_file():
        put("params.py", f'"""Numbers of {_doc(title)}. A part recreated from the client\'s CAD keeps its own in its node\'s params.py '
            '(parts/<node>/params.py, named <PART>_<NUMBER>)."""\n')

    def pname(pid, k):
        return f"{part_mod[pid].upper()}_{k.upper()}"
    node_params = {}
    for pid, inf in info.items():
        if inf["kind"] in ("sheet", "turned", "prism") and inf.get("params"):
            node_params.setdefault(part_home[pid], []).append(pid)
    for folder, pids in sorted(node_params.items()):
        lines = [f'"""Numbers of the parts of node {folder} recreated from the client\'s CAD, mm."""',
                 "from params import *  # noqa: F401,F403", ""]
        for pid in sorted(pids, key=lambda x: part_mod[x]):
            for k, v in info[pid]["params"].items():
                lines.append(f"{pname(pid, k)} = {v!r}")
        put(f"parts/{folder}/params.py", "\n".join(lines) + "\n")

    # part files. Two different parts the client gave one drawing number (a mirrored or changed copy, his `_2`): the second and
    # further get the number of a variant (GOST 2.113 "execution"), <number>-01, -02, ...: one code is one part in every kit list
    variant, seen_code = {}, {}
    for pid in sorted(parts):
        d = parts[pid].get("drawing") if parts[pid].get("kind") == "made" else None
        if d:
            variant[pid] = f"{d}-{seen_code[d]:02d}" if d in seen_code else d
            seen_code[d] = seen_code.get(d, 0) + 1
    rows = []
    for pid, part in parts.items():
        inf = info[pid]
        folder, mod = part_home[pid], part_mod[pid]
        f = _P_fields(part, inf, lang)
        if pid in variant:
            f["code"] = variant[pid]
        f.update((row_fields or {}).get(pid, {}))      # the project's own row wins (its purchase list, a process it knows)
        imports = ["from build123d import Location, Plane, RigidJoint  # noqa: F401", "from geom import P"]
        body = []
        if inf["kind"] in ("sheet", "turned", "prism"):
            code = RC.imports_helpers(inf["code"])
            end = code.index('"""', 3) + 3
            doc = code[3:end - 3].rstrip()
            rest = code[end:].strip("\n")
            imports += [ln for ln in rest.split("\n") if ln.startswith("from recreated import ")]
            rest = "\n".join(ln for ln in rest.split("\n") if not ln.startswith("from recreated import ")).strip("\n")
            rest = rest.replace("def build(", "def shape(", 1)
            if inf.get("params"):
                imports.append("from . import params as P_")
            doc += ("\nRecreated from the client's CAD by step-import step_to_project.py; mechanics/cad/migration/reference.json "
                    "holds the client's measures it was checked against.")
            sig = ", ".join(f"{k}=P_.{pname(pid, k)}" for k in inf.get("params", {}))
            call = ", ".join(f"{k}={k}" for k in inf.get("params", {}))
            body += [rest, "", "", f"def build({sig}):", f"    s = shape({call})"]
            if inf["kind"] == "sheet":
                f["thickness"] = "@thickness"         # the recreated sheet's parameter, whatever an override says
            kind = inf["kind"]
        elif inf["kind"] == "fastener":
            a = inf["args"]
            spec = ", ".join(f"{k} {v:g}" if isinstance(v, float) else f"{k} {v}" for k, v in a.items())
            doc = (f"{part['name']}\nA standard part: the framework template parts/fasteners/{inf['template']}.py ({spec}), set where "
                   f"the client's CAD has it (its joint `{JOINT_OF[inf['template']]}`); size against the client's part: {inf['fit']}.")
            imports.append(f"from parts.fasteners import {inf['template']}")
            args = ", ".join(f"{k}={_py(v)}" for k, v in a.items())
            body += ["", "", "def build():", f"    s = {inf['template']}.build({args})[\"shape\"].moved({loc_code(mat(inf['frame']))})"]
            kind = "bought"
        elif library and part.get("kind") != "made":       # a bought part: the catalogue's, docked here by this machine's joints
            group, lmod, code = library_part(library, part, inf["kind"], step_of(import_dir, part), lang)
            doc = f"{part['name']}\nBought part: the catalogue part library.{group}.{lmod} (the client's library), docked where the client's CAD has it."
            imports.append(f"from library.{group} import {lmod}")
            if code and "code" not in (row_fields or {}).get(pid, {}):   # the article read from his name, unless the
                f["code"] = code                                         # project's own row gives the code
            body += ["", "", "def build():", f"    s = {lmod}.build()[\"shape\"]"]
            kind = "bought"
            inf = dict(inf, library=f"library.{group}.{lmod}")
        else:
            src = f"{vend}/{pid}.step"
            hand = kept.get(f"parts/{folder}/{mod}.py")
            if hand is None or b"import_step(" in hand:   # a part the agent has drawn by hand needs his STEP no more
                shutil.copyfile(step_of(import_dir, part), at(src))
                generated[src] = file_sha(at(src))
            depth = len(folder.split("/")) + 1
            imports += ["from pathlib import Path", "from build123d import import_step"]
            if part.get("kind") == "made":
                why = inf.get("reason") or "not recognised"
                doc = (f"{part['name']}\nThe client's geometry (mechanics/cad/{src}): no parametric recreation matched it, "
                       "see standin_reason.")
                f["standin_reason"] = f"client geometry kept: {why}"[:500]
                kind = "client"
            else:
                doc = f"{part['name']}\nBought part: the maker's 3D as the client's CAD has it (mechanics/cad/{src})."
                kind = "bought"
            body += ["", "", "def build():",
                     f"    s = import_step(str(Path(__file__).resolve().parents[{depth}] / "
                     f"{' / '.join(json.dumps(x, ensure_ascii=False) for x in src.split('/'))}))"]
        for lab, m in joints[pid].items():
            body.append(f"    RigidJoint({json.dumps(lab)}, s, {loc_code(m)})")
        fields = []
        for k, v in f.items():
            if v == "@thickness":
                fields.append(f"{k}=thickness")
            elif k == "class":
                fields.append(f"**{{\"class\": {_py(v)}}}")
            else:
                fields.append(f"{k}={_py(v)}")
        body.append(f"    return P({json.dumps(pid, ensure_ascii=False)}, s, {json.dumps(part.get('color') or '#9aa5b1')},")
        body.append("             " + ",\n             ".join(fields) + ")")
        doc = _doc(doc)
        put(f"parts/{folder}/{mod}.py", f'"""{doc}\n"""\n' + "\n".join(imports) + "\n" + "\n".join(body) + "\n")
        row = {"id": pid, "name": part["name"], "kind": kind, "file": f"mechanics/cad/parts/{folder}/{mod}.py",
               "process": f.get("process")}
        if part.get("his_id"):
            row["his_id"] = part["his_id"]                # a node port: his id was taken in the project
        if kind == "client":
            row["reason"] = inf.get("reason") or "not recognised"
        if inf["kind"] == "fastener":
            row.update(how="standard", template=inf["template"], args=inf["args"], fit=inf["fit"])
        elif kind == "bought":
            row.update(how="library" if inf.get("library") else "maker_3d", why_not_standard=inf.get("reason") or "",
                       **({"library": inf["library"]} if inf.get("library") else {}))
        for k in ("compare", "params"):
            if inf.get(k):
                row[k] = inf[k]
        rows.append(row)

    # node files
    out_index = {}
    for ref in bottom_up(products, root_ref):
        p = products[ref]
        folder = p.folder
        local, absolute = set(), {}
        lines, var = [], {}
        src = {}
        for i, (kind, cid, _, _) in enumerate(p.children):
            src[i] = (part_home[cid], part_mod[cid]) if kind == "part" else tuple(products[cid].folder.rsplit("/", 1))
        here = {mod for home, mod in src.values() if home == folder}
        others = {}
        for home, mod in set(src.values()):
            if home != folder:
                others.setdefault(mod, set()).add(home)

        def name_of(home, mod):
            """A module of another folder whose name is taken here (a local one, or one of a third folder) gets an alias:
            `from parts.a import vtulka` and `from . import vtulka` would be one name, one module."""
            if home == folder or (mod not in here and len(others[mod]) == 1):
                return mod
            return f"_{home.replace('/', '__')}__{mod}"
        for i in _dock_order(p):
            kind, cid, m, _ = p.children[i]
            v = var[i] = f"c{i}"
            home, mod = src[i]
            if home == folder:
                local.add(mod)
            else:
                absolute.setdefault(home.replace("/", "."), set()).add((mod, name_of(home, mod)))
            lines.append(f"    {v} = {name_of(home, mod)}.build()")
            if i == p.anchor:                         # a node's build() stands in its anchor part's frame
                lines.append(f"    {v} = place({v}, {loc_code(root_m @ m @ (products[cid].anchor_m if kind == 'node' else np.eye(4)))})"
                             if ref == root_ref and not node else f"    {v} = place({v})")
                continue
            j, plab, clab, dist = plans[ref][i]
            pv = var[j] if p.children[j][0] == "part" else f"{var[j]}[{out_index[p.children[j][1]]}]"
            cv = v if kind == "part" else f"{v}[{out_index[cid]}]"
            lines.append(f'    {pv}["shape"].joints[{json.dumps(plab)}].connect_to({cv}["shape"].joints[{json.dumps(clab)}])'
                         + (f"  # {dist:.2f} mm apart" if dist > 0.05 else ""))
            lines.append(f"    {v} = place({v})")
        ret, pos = [], 0
        for i, (kind, cid, _, _) in enumerate(p.children):
            if i == p.anchor:
                out_index[ref] = pos if kind == "part" else pos + out_index[cid]
            ret.append(f"[{var[i]}]" if kind == "part" else var[i])
            pos += 1 if kind == "part" else _flat_len(products, cid)
        imp = ["from build123d import Location, Plane  # noqa: F401", "from geom import place"]
        for pkg in sorted(absolute):
            imp.append(f"from parts.{pkg} import " + ", ".join(m if a == m else f"{m} as {a}" for m, a in sorted(absolute[pkg])))
        if local:
            imp.append("from . import " + ", ".join(sorted(local)))
        doc = (f'"""Node «{_doc(p.name)}»: the client\'s sub-assembly, used {_uses(products, ref)} in the machine. Docks its parts and '
               f'sub-nodes by joints;\nreturns them in the order of the client\'s CAD."""')
        put(f"parts/{folder}/__init__.py", doc + "\n" + "\n".join(imp) + "\n\n" + f"TITLE = {json.dumps(p.name, ensure_ascii=False)}\n\n\n"
            "def build():\n" + "\n".join(lines) + "\n    return " + " + ".join(ret) + "\n")
    rootf = products[root_ref].folder
    if node:                                          # the agent docks the node: where his CAD has its anchor part
        def anchor_at(ref, M):
            kind, cid, m, _ = products[ref].children[products[ref].anchor]
            return (cid, M @ m) if kind == "part" else anchor_at(cid, M @ m)
        apid, aM = anchor_at(root_ref, root_m)
        put(f"{mig}/placement.json", json.dumps({"node": products[root_ref].name, "folder": rootf, "anchor": apid,
                                                "matrix": [round(float(x), 9) for x in aM.reshape(-1)],
                                                # machine: the STEP is his machine holding the node, the matrix is in the
                                                # machine's frame and the node must stand there; own: the node on its own
                                                "frame": "machine" if in_machine else "own",
                                                "how": "place the node's build() list here: the node stands in the frame of its "
                                                       "anchor part; this is where his CAD has that part (mm, row-major)"},
                                               ensure_ascii=False, indent=1) + "\n")
    if not node:
        put("parts/__init__.py", f'"""The machine «{_doc(title)}»: its top node, placed where the client\'s CAD has it."""\n'
        f"from . import {rootf}\n\n\ndef build():\n    return {rootf}.build()\n")
    if not node and (not old_gen or not (cad / "assemble.py").is_file()   # the agent's own assemble.py stays
                     or old_gen.get("assemble.py") == file_sha(cad / "assemble.py")):
        put("assemble.py", '"""Rebuild entry: the machine is parts.build() (skill machine-project «Code model»), built by\n'
            '`python <fw>/skills/machine-project/scripts/cad_incremental.py --model assemble.py` (the router runs it through derive.py)."""\n'
            "import sys\nfrom pathlib import Path\n\nsys.path.insert(0, str(Path(__file__).parent))\nfrom parts import build  # noqa: E402\n\n"
            'items = build()\nprint(f"{len(items)} placed parts")\n')

    # reference (what the client's CAD says) + report
    ref_parts = {}
    for pid, part in parts.items():
        s = import_step(str(step_of(import_dir, part))).wrapped
        ref_parts[pid] = {"name": part["name"], "drawing": part.get("drawing"), "kind": part.get("kind"),
                          "volume_mm3": round(RC._volume(s), 3), "bbox_mm": [round(x, 4) for x in RC._bbox(s)],
                          "holes": RC._concave_holes(s), "instances": part.get("instances"), "model_id": _model_id(pid),
                          "sha256": file_sha(step_of(import_dir, part)), **({"his_id": part["his_id"]} if part.get("his_id") else {})}
    placements = []

    def walk(ref, M, path):
        for kind, cid, m, iname in products[ref].children:
            W = M @ m
            if kind == "part":
                placements.append({"part": cid, "path": path + [iname], "matrix": [round(float(x), 9) for x in W.reshape(-1)]})
            else:
                walk(cid, W, path + [iname])
    walk(root_ref, root_m, [products[root_ref].name])
    asm = json.loads((import_dir / "parts.json").read_text(encoding="utf-8"))["assembly"]
    put(f"{mig}/reference.json", json.dumps({"source": asm.get("source"), "root": products[root_ref].name, "parts": ref_parts,
                                                "placements": placements}, ensure_ascii=False, indent=0) + "\n")
    made = [r for r in rows if parts[r["id"]].get("kind") == "made"]
    totals = {"parts": len(rows), "placements": len(placements), "nodes": len(products),
              "made": len(made), "recreated": sum(1 for r in made if r["kind"] in ("sheet", "turned", "prism")),
              "client_geometry": sum(1 for r in made if r["kind"] == "client"),
              "bought": sum(1 for r in rows if r["kind"] == "bought"),
              "bought_standard": sum(1 for r in rows if r.get("how") == "standard"),
              "bought_maker_3d": sum(1 for r in rows if r.get("how") == "maker_3d"),
              "bought_library": sum(1 for r in rows if r.get("how") == "library")}
    for k in ("sheet", "turned", "prism"):
        totals[k] = sum(1 for r in made if r["kind"] == k)
    for rel, data in list(kept.items()):              # the agent's files win over what this run would write
        f = at(rel)
        if f.is_file() and f.read_bytes() == data:     # the same as this run writes: the converter's, not the agent's
            del kept[rel]
            continue
        if rel.endswith("/params.py") and f.is_file():  # a node's numbers the agent added to: his lines stay, every number
            data = merge_params(data.decode("utf-8"), f.read_text(encoding="utf-8")).encode("utf-8")   # this run writes is set
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
        generated.pop(rel, None)
    old = stage.parent / "port_old"
    if old.exists():                                  # an earlier swap died half way: its folders are the project's
        raise SystemExit(f"{old} holds the project's folders from a swap that did not finish: move them back into "
                         "mechanics/cad (parts/, vendor/client/) first")
    done = []
    try:
        for k, d in enumerate((str(pdir.relative_to(cad)), vend)):   # the swap: the run went through; the old folder is set
            if (cad / d).is_dir():                                    # aside first, so no moment leaves the project without it
                old.mkdir(parents=True, exist_ok=True)
                shutil.move(str(cad / d), str(old / str(k)))
            (cad / d).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(stage / d), str(cad / d))
            done.append((k, d))
    except BaseException:                             # put back what was set aside
        for k, d in [(k, d) for k, d in enumerate((str(pdir.relative_to(cad)), vend))]:
            if (old / str(k)).exists():
                if (cad / d).exists():
                    shutil.rmtree(cad / d)
                shutil.move(str(old / str(k)), str(cad / d))
        raise
    shutil.rmtree(old, ignore_errors=True)
    report = {"title": title, "source": asm.get("source"), "totals": totals, "parts": rows, "kept": sorted(kept)}
    put(f"{mig}/report.json", json.dumps(report, ensure_ascii=False, indent=1) + "\n")
    flush()
    gen_f.parent.mkdir(parents=True, exist_ok=True)
    gen_f.write_text(json.dumps(dict(sorted(generated.items())), indent=1) + "\n", encoding="utf-8")
    return report


def _model_id(name):
    """A part id as the framework spells it (cad_incremental._part_id)."""
    return re.sub(r"_+", "_", re.sub(r"[^\w]+", "_", str(name))).strip("_")


def _flat_len(products, ref):
    return sum(1 if kind == "part" else _flat_len(products, cid) for kind, cid, _, _ in products[ref].children)


def _dock_order(p):
    """The anchor, then each child after the one it docks to."""
    done, out = {p.anchor}, [p.anchor]
    pending = [i for i in range(len(p.children)) if i != p.anchor]
    while pending:
        for i in list(pending):
            if p.dock[i][0] in done:
                out.append(i)
                done.add(i)
                pending.remove(i)
    return out


def _uses(products, ref):
    cnt = 0

    def count(r):
        nonlocal cnt
        if r == ref:
            cnt += 1
        for kind, cid, _, _ in products[r].children:
            if kind == "node":
                count(cid)
    for r in products:
        if not products[r].parents:
            count(r)
    return f"{cnt} time" + ("s" if cnt != 1 else "")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("import_dir", help="the client's STEP (cut first into ROOT/build/client_import by step_import.py), or "
                    "step_import.py's --out dir: parts.json, parts/<id>.step, instances.json")
    ap.add_argument("root", help="the project root (mechanics/cad is written there)")
    ap.add_argument("--title")
    ap.add_argument("--library", help="the client's library root (/clients/<client id>, holding library/; never the framework's "
                    "catalogue): bought parts that are not standard fasteners become its parts; without it they stay in vendor/client/")
    ap.add_argument("--overrides", help="JSON {part id: {type, material, ...}} over the import (a project's parts_overrides.json): "
                    "type / material steer the recognition; " + ", ".join(ROW_FIELDS) + " go into the part's P(...) as they are "
                    "(qty = units per placement)")
    ap.add_argument("--node", help="a node folder of the project (under mechanics/cad/parts/): the STEP is a node of the client's "
                    "machine, ported as one new folder under it; nothing else of the project is touched")
    a = ap.parse_args()
    if a.overrides:                                   # a bad file is told now, not after the hours of the cut
        try:
            json.loads(Path(a.overrides).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise SystemExit(f"--overrides {a.overrides}: {e}")
    imp, out = Path(a.import_dir), None
    if imp.is_file():                                 # the client's STEP itself: cut it first (one job for the whole port)
        out = Path(a.root) / "build" / ("client_import" if not a.node else f"node_import/{slug(imp.stem, 'node')}")
        new = out.with_name(out.name + ".new")        # the import the project was checked against stays until this run is through
        if new.exists():
            shutil.rmtree(new)
        subprocess.run([sys.executable, "-u", str(HERE / "step_import.py"), str(imp), "--out", str(new)], check=True)
        imp = new
    rep = convert(imp, a.root, a.title, a.overrides, library=a.library, node=a.node)
    if out is not None:
        if out.exists():
            shutil.rmtree(out)
        new.rename(out)
    t = rep["totals"]
    print(f"[step_to_project] {t['parts']} parts, {t['placements']} placements, {t['nodes']} nodes | made {t['made']}: "
          f"recreated {t['recreated']} (sheet {t['sheet']}, turned {t['turned']}, prism {t['prism']}), client geometry "
          f"{t['client_geometry']} | bought {t['bought']}: standard {t['bought_standard']}, library {t['bought_library']}, "
          f"maker 3D in vendor/ {t['bought_maker_3d']}")


if __name__ == "__main__":
    main()
