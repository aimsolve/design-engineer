"""Issue #21 «Part library across projects, split by client and workspace»: the framework script part_library.py (roots / sync /
check) and the project mirror mechanics/cad/library/ it writes. Written from the spec BEFORE the implementation; every test
names the done-when condition (L1..L9) it serves.

  pytest skills/machine-project/scripts/tests/test_library.py -q
  (the whole suite: pytest skills/machine-project/scripts/tests -n auto --dist worksteal -q -W ignore)

The three levels are temp dirs laid out exactly as a container has them, and handed to part_library.py by flag, never by discovery:
    <base>/catalog/library/<group>/...        --catalog <base>/catalog     level "catalog"
    <base>/clients/<client id>/library/<group>/   --mount <base>/clients/<id>  level "client:<client id>"
    <base>/workspace/library/<group>/         --mount <base>/workspace     level "workspace"
A root is the directory that CONTAINS library/. $ENGINEER_CATALOG (the single catalogue path of the no-flag case) is cleared
from the environment so a test can never pick up a real root of this machine. The script is deliberately NOT called
library.py: the scripts dir is sys.path[0] during a model build, so a module named `library` would shadow the project's
library/ namespace package.

The project is helpers.make_project() (the folder layout: parts/<node>/<part>.py) plus one extra node `lib` whose part files do
nothing but return what a library part's build() returns:
    parts/lib/kfl004.py   from library.bearings import kfl004 as lib   (catalog group `bearings`)
    parts/lib/vac_pad.py  from library.gripper import vac_pad as lib     (client group `gripper`)
Each ticks its own part id, so helpers.ticks() proves which units a library edit rebuilt (L3).

  L1 sync copies a REFERENCED catalog group into the mirror and writes .sources.json; an unreferenced group is not copied
  L2 the model builds with a library part: it is in build/model/parts.json with its joints and its bought fields, and a
     project part docked onto its port records that docking as the placement's `joint` {to, at, by, type}
  L3 cache granularity: a library edit rebuilds the importing part and its node, nothing else; an unreferenced group's edit nothing
  L4 isolation: a group that lives only in another client's (unmounted) root fails sync, naming the group; nothing of it is copied
  L5 two levels offering the same relative path: exit 1 naming both source paths, the mirror untouched
  L6 dropping the import removes the group from the mirror and from the stamp
  L7 check: FAIL on a hand-edited mirror file (named), FAIL on a referenced group missing from the mirror, PASS right after a
     sync; a second sync changes no mtime and reports no change
  L8 a client group and a catalog group both referenced: both land, each stamp entry's level is right, both build
  L9 the check.py gate `library`: FAIL when check would fail or kit.json inputs do not cover the mirror; FAIL on a project
     file that shadows the namespace package; no rows at all for a project without a library
  L10 the REAL catalogue of this repo (agent/catalog): its three parts build into a model with their ids, bought fields and
     ports, and the cylinder's `rod` LinearJoint keeps the range (0, stroke)
  L11 a forged .sources.json level buys nothing: a file the project wrote itself is not blessed as library code
  L12 sync never touches files it did not write: a project's own library/ folder (no stamp) is refused untouched; a stray
     file beside a mirrored group survives and check names it
  L13 a library part importing another library group: sync copies the groups transitively; check FAILs a missing one
  L14 one catalogue part used several ways: one part file placed by two nodes; variants (mounts, flange) get their own ids
  L15 only library/, build/, vendor/ DIRECTLY under mechanics/cad/ are skipped: a node folder parts/vendor/ is scanned
  L16 a hand edit of the copy is never silently overwritten: sync refuses naming the file and its source; deleting the copy
     and syncing restores the source
  L17 a --mount that is neither a clients/<id> folder, a workspace folder nor the catalogue: exit 2, a FAIL line naming it
"""
import json
import os
import contextlib
import importlib.util
import re
import shutil
import sys
import textwrap
from pathlib import Path

import pytest

import helpers as H
import partfiles as F
import procgroup

PART_LIBRARY = H.SCRIPTS / "part_library.py"
MIRROR = "mechanics/cad/library"
# the framework's own catalogue, which every container has at /opt/design-engineer/catalog: agent/catalog in this repo
CATALOG_ROOT = H.SCRIPTS.parents[2] / "catalog"


# ---------------------------------------------------------------- library files (the part files the levels hold)

KFL004 = """
from build123d import Align, Box, Location, RigidJoint
from geom import P

HEIGHT = 12.0


def build(d=20.0):
    s = Box(40, 30, HEIGHT, align=(Align.CENTER, Align.CENTER, Align.MIN))
    RigidJoint("mount", s, Location((0, 0, 0)))
    return P("kfl004", s, "#cccccc", process="bought", code="KFL004", desc="Bearing KFL004 d%g" % d, kind="bearings")
"""

VAC_PAD = """
from build123d import Align, Cylinder, Location, RigidJoint
from geom import P

LIP = 8.0


def build():
    s = Cylinder(15, LIP, align=(Align.CENTER, Align.CENTER, Align.MIN))
    RigidJoint("stem", s, Location((0, 0, LIP)))
    return P("vac_pad", s, "#222222", process="bought", code="VP-15", desc="Vacuum pad 15", kind="other")
"""

SPACER = """
from build123d import Cylinder
from geom import P


def build():
    return P("spacer", Cylinder(4, 3), "#999999", process="bought", code="SP-4", desc="Spacer 4", kind="fasteners")
"""

STEP_BESIDE = "ISO-10303-21;\nHEADER;\n/* a vendor STEP that belongs to the part file */\nENDSEC;\nEND-ISO-10303-21;\n"

# what a project part file looks like: it only forwards the library part (the file stem is the part name, so the id is stable)
USER = {
    "kfl004": """
        from library.bearings import kfl004 as lib
        {TICK}

        def build():
            _tick("kfl004")
            return lib.build()
        """,
    "vac_pad": """
        from library.gripper import vac_pad as lib
        {TICK}

        def build():
            _tick("vac_pad")
            return lib.build()
        """,
}

# a part of the project's own with the port the library part docks into (the point of the issue: ports, not coordinates)
BRACKET = """
    from build123d import Align, Box, Location, RigidJoint
    from geom import P
    {TICK}

    def build():
        _tick("bracket")
        s = Box(60, 40, 10, align=(Align.MIN, Align.CENTER, Align.MIN))
        RigidJoint("seat", s, Location((30, 0, 10)))
        return P("bracket", s, "#777777", process="mill", material="S235JR")
    """

# the node only docks and places: the bracket is the root, the library bearing hangs on its `seat`
DOCKING_NODE = """
    from build123d import Pos
    from geom import place
    from . import bracket, kfl004
    {TICK}

    def build():
        _tick("node_lib")
        br = bracket.build()
        brg = kfl004.build()
        br["shape"].joints["seat"].connect_to(brg["shape"].joints["mount"])
        return [place(br, Pos(0, 0, 0)), place(brg)]
    """

CATALOG = {"bearings/kfl004.py": KFL004, "bearings/kfl004.step": STEP_BESIDE, "misc/spacer.py": SPACER}
CLIENT_A = "client:555"
CLIENT_B = "client:777"


# ---------------------------------------------------------------- roots on disk

def root_dir(base, level):
    if level == "catalog":
        return Path(base) / "catalog"
    if level == "workspace":
        return Path(base) / "workspace"
    return Path(base) / "clients" / level.split(":", 1)[1]


def write_roots(base, groups):
    """groups: {level: {"<group>/<file>": text}} -> {level: root dir}. The root is the dir that CONTAINS library/."""
    out = {}
    for level, files in groups.items():
        r = root_dir(base, level)
        (r / "library").mkdir(parents=True, exist_ok=True)
        for rel, text in files.items():
            p = r / "library" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(textwrap.dedent(text).lstrip())
        out[level] = r
    return out


def flags(roots):
    """--catalog for the framework catalog, --mount for every mounted root (client / workspace)."""
    out = []
    for level, d in sorted(roots.items()):
        out += ["--catalog", str(d)] if level == "catalog" else ["--mount", str(d)]
    return out


def source(roots, level, rel):
    return roots[level] / "library" / rel


# ---------------------------------------------------------------- running part_library.py

def lib_cli(*args):
    e = dict(os.environ)
    e.pop("ENGINEER_CATALOG", None)
    e["PYTHONDONTWRITEBYTECODE"] = "1"
    cp = procgroup.run([sys.executable, str(PART_LIBRARY), *[str(a) for a in args]], capture_output=True, text=True, timeout=180, env=e)
    return cp


def log(cp):
    return ((cp.stdout or "") + (cp.stderr or ""))[-3000:]


def sync(root, roots, expect=0):
    cp = lib_cli("sync", root, *flags(roots))
    if expect is not None:
        assert cp.returncode == expect, f"part_library.py sync exited {cp.returncode}, want {expect}:\n{log(cp)}"
    return cp


def check(root, expect=None):
    cp = lib_cli("check", root)
    if expect is not None:
        assert cp.returncode == expect, f"part_library.py check exited {cp.returncode}, want {expect}:\n{log(cp)}"
    return cp


# ---------------------------------------------------------------- reading the mirror

def mirror(root):
    return Path(root) / "mechanics" / "cad" / "library"


def mirror_files(root):
    m = mirror(root)
    if not m.is_dir():
        return set()
    return {str(p.relative_to(m)) for p in m.rglob("*")
            if p.is_file() and p.name != ".sources.json" and "__pycache__" not in p.parts}


def mirror_state(root):
    m = mirror(root)
    return {rel: ((m / rel).stat().st_mtime_ns, (m / rel).read_bytes()) for rel in mirror_files(root)}


def mtimes(root):
    m = mirror(root)
    return {rel: (m / rel).stat().st_mtime_ns for rel in mirror_files(root)}


def stamp(root):
    f = mirror(root) / ".sources.json"
    assert f.is_file(), f"no {MIRROR}/.sources.json after a sync; the mirror holds {sorted(mirror_files(root))}"
    d = json.loads(f.read_text())
    assert d.get("version") == 1 and isinstance(d.get("files"), dict), f"the stamp is not {{version: 1, files: {{...}}}}: {d}"
    return d


def levels(root):
    return {rel: e.get("level") for rel, e in stamp(root)["files"].items()}


def sha1(path):
    import hashlib
    return hashlib.sha1(Path(path).read_bytes()).hexdigest()


# ---------------------------------------------------------------- the project

def make_lib_project(root, parts=("kfl004",)):
    """helpers' folder-layout project plus the node `lib` whose part files forward library parts (and which assemble.py calls)."""
    root = H.make_project(root, extra=("lib",))
    H.write_node(root, "lib", {pid: USER[pid] for pid in parts},
                 [(pid, (200 + 60 * i, 0, 0)) for i, pid in enumerate(parts)])
    return root


def make_docked_project(root):
    """Same project, but the node `lib` DOCKS the library bearing onto a port of its own bracket part instead of placing it
    at coordinates: bracket is the root (placed at Pos(0, 0, 0), as the framework's own joint project does), kfl004 hangs on
    its `seat` joint. A node that only docks and places is the #15 invariant; the library import stays in the part file."""
    root = H.make_project(root, extra=("lib",))
    d = H.cad_dir(root) / "parts" / "lib"
    d.mkdir(parents=True, exist_ok=True)
    for stem, src in (("kfl004", USER["kfl004"]), ("bracket", BRACKET), ("__init__", DOCKING_NODE)):
        (d / f"{stem}.py").write_text(textwrap.dedent(src).replace("{TICK}", H.TICK).lstrip())
    return root


def set_lib_parts(root, parts):
    """Rewrite the node `lib` with exactly these part files (what an agent dropping an import does)."""
    d = H.cad_dir(root) / "parts" / "lib"
    for p in list(d.glob("*.py")):
        p.unlink()
    H.write_node(root, "lib", {pid: USER[pid] for pid in parts},
                 [(pid, (200 + 60 * i, 0, 0)) for i, pid in enumerate(parts)])


# ================================================================ the script itself

def test_part_library_py_exists_and_is_plain_python3():
    assert PART_LIBRARY.is_file(), f"the framework script {PART_LIBRARY} does not exist yet: roots / sync / check live there"
    cp = lib_cli("roots", "--catalog", "/nonexistent-root-for-this-test")
    assert cp.returncode == 0, f"part_library.py roots must exit 0 even when no root exists:\n{log(cp)}"


def test_roots_prints_one_level_and_path_per_existing_root(tmp_path):
    """`roots` names each root that holds a library/: catalog, client:<id>, workspace."""
    roots = write_roots(tmp_path, {"catalog": CATALOG, CLIENT_A: {"gripper/vac_pad.py": VAC_PAD},
                                   "workspace": {"ws_group/ws_part.py": SPACER}})
    bare = tmp_path / "clients" / "999"              # a client dir without library/ is not a root
    bare.mkdir(parents=True)
    cp = lib_cli("roots", *flags(roots), "--mount", str(bare))
    assert cp.returncode == 0, log(cp)
    got = {ln.split(None, 1)[0]: ln.split(None, 1)[1].strip() for ln in cp.stdout.splitlines() if ln.split()}
    assert got == {"catalog": str(roots["catalog"]), CLIENT_A: str(roots[CLIENT_A]), "workspace": str(roots["workspace"])}, \
        f"roots lines: {cp.stdout!r}"


# ================================================================ L1 sync copies what is referenced, and only that

def test_l1_sync_copies_the_referenced_catalog_group_and_writes_the_stamp(fresh, tmp_path):
    roots = write_roots(tmp_path / "roots", {"catalog": CATALOG})
    H.write_node(fresh, "lib", {"kfl004": USER["kfl004"]}, [("kfl004", (200, 0, 0))])

    cp = sync(fresh, roots)
    assert mirror_files(fresh) == {"bearings/kfl004.py", "bearings/kfl004.step"}, \
        f"every file of the referenced group (and nothing else) is mirrored; got {sorted(mirror_files(fresh))}\n{log(cp)}"
    assert (mirror(fresh) / "bearings/kfl004.py").read_text() == source(roots, "catalog", "bearings/kfl004.py").read_text()
    assert not (mirror(fresh) / "misc").exists(), "the unreferenced group `misc` must not be copied"
    assert not list(mirror(fresh).rglob("__init__.py")), "library/ is a PEP 420 namespace dir: no __init__.py"

    st = stamp(fresh)
    assert set(st["files"]) == {"bearings/kfl004.py", "bearings/kfl004.step"}, f"stamp files: {sorted(st['files'])}"
    for rel, e in st["files"].items():
        assert e["level"] == "catalog", f"{rel}: level {e.get('level')!r}, want 'catalog'"
        assert e["sha1"] == sha1(mirror(fresh) / rel), f"{rel}: the stamp sha1 is not the file's"
    assert "bearings" in cp.stdout, f"sync says what it synced: {cp.stdout!r}"


def test_l1_a_project_that_references_no_group_syncs_nothing(fresh, tmp_path):
    roots = write_roots(tmp_path / "roots", {"catalog": CATALOG})
    cp = sync(fresh, roots)
    assert "nothing to sync" in cp.stdout, f"a project with no library import: `library: nothing to sync`; got {cp.stdout!r}"
    assert mirror_files(fresh) == set()


# ================================================================ L2 the model builds with a library part

def test_l2_the_model_builds_with_a_library_part(tmp_path):
    roots = write_roots(tmp_path / "roots", {"catalog": CATALOG})
    root = make_lib_project(tmp_path / "proj")
    sync(root, roots)

    run = H.run_model(root).ok()
    e = H.by_id(root).get("kfl004")
    assert e, f"the library part is not in build/model/parts.json: {sorted(H.by_id(root))}\n{run.log[-1500:]}"
    assert e["process"] == "bought" and e["code"] == "KFL004" and "Bearing KFL004" in e["desc"], \
        f"the bought fields of the library part are lost: {e}"
    assert len(F.placements(e)) == 1, f"one placement: {F.placements(e)}"
    js = e.get("joints")
    assert isinstance(js, dict) and js.get("mount", {}).get("type") == "rigid", \
        f"the joints the library part declares must reach parts.json: {js}"


def test_l2_a_library_part_is_docked_by_its_port_not_placed_by_coordinates(tmp_path):
    """The point of the issue: the library part brings a joint, and the project docks its own part to it. parts.json must
    carry that as the placement's `joint` record {to, at, by, type} (what check.py's `joints` gate reads), not a bare loc."""
    roots = write_roots(tmp_path / "roots", {"catalog": CATALOG})
    root = make_docked_project(tmp_path / "proj")
    sync(root, roots)

    run = H.run_model(root).ok()
    ids = H.by_id(root)
    assert "bracket" in ids and "kfl004" in ids, \
        f"both the project part with the port and the library part are in the model: {sorted(ids)}\n{run.log[-1500:]}"

    pls = F.placements(ids["kfl004"])
    assert len(pls) == 1, f"one placement of the docked library part: {pls}"
    j = pls[0].get("joint")
    assert isinstance(j, dict), \
        f"a docked library part's placement must carry the joint record, not only a location: {pls[0]}"
    assert {k: j.get(k) for k in ("to", "at", "by", "type")} == \
        {"to": "bracket", "at": "seat", "by": "mount", "type": "rigid"}, f"placement joint {j}"
    assert j.get("value") is None, f"a rigid docking has no value: {j}"

    root_pl = F.placements(ids["bracket"])
    assert len(root_pl) == 1 and "joint" not in root_pl[0], f"the bracket is the root of this chain: {root_pl}"

    # The joints gate reads exactly this record. It FAILs on the rest of the helpers fixture (whose parts are placed by
    # Location and carry no joints at all), so what must hold is that it never names the docked library part.
    blamed = [d for s, d in check_rows(root, "joints") if s == "FAIL" and re.search(r"(^|\W)kfl004(\W|$)", d or "")]
    assert not blamed, f"check.py's joints gate must need no exception for a library part: {blamed}"


# ================================================================ L3 cache granularity

def test_l3_a_library_edit_rebuilds_only_the_importing_part_and_its_node(tmp_path):
    roots = write_roots(tmp_path / "roots", dict({"catalog": CATALOG}, **{CLIENT_A: {"gripper/vac_pad.py": VAC_PAD}}))
    root = make_lib_project(tmp_path / "proj", parts=("kfl004", "vac_pad"))
    sync(root, roots)
    H.run_model(root).ok()

    H.clear_ticks(root)
    H.edit(source(roots, "catalog", "bearings/kfl004.py"), "HEIGHT = 12.0", "HEIGHT = 18.0")
    sync(root, roots)
    assert "18.0" in (mirror(root) / "bearings/kfl004.py").read_text(), "sync did not bring the edited source into the mirror"
    H.run_model(root).ok()
    assert set(H.ticks(root)) == {"kfl004", "node_lib"}, \
        f"a library edit rebuilds the importing part and its node only, ran: {sorted(set(H.ticks(root)))}"

    H.clear_ticks(root)
    H.edit(source(roots, "catalog", "misc/spacer.py"), "SP-4", "SP-5")      # an unreferenced group: nothing to rebuild
    sync(root, roots)
    H.run_model(root).ok()
    assert H.ticks(root) == [], f"an unreferenced library group's edit rebuilds nothing, ran: {sorted(set(H.ticks(root)))}"


# ================================================================ L4 isolation: no mount, no path

def test_l4_a_group_of_an_unmounted_client_root_is_unreachable(fresh, tmp_path):
    """Catalog + client B are mounted; client A's root holds `gripper` and is NOT mounted."""
    roots = write_roots(tmp_path / "roots", {"catalog": CATALOG, CLIENT_B: {"grip/grip40.py": SPACER},
                                             CLIENT_A: {"gripper/vac_pad.py": VAC_PAD}})
    mounted = {k: v for k, v in roots.items() if k != CLIENT_A}
    H.write_node(fresh, "lib", {"vac_pad": USER["vac_pad"]}, [("vac_pad", (200, 0, 0))])

    cp = sync(fresh, mounted, expect=1)
    assert "gripper" in log(cp), f"the refusal names the group that is in no root: {log(cp)}"
    assert str(roots["catalog"]) in log(cp) or str(roots[CLIENT_B]) in log(cp), \
        f"the refusal names the roots that were searched: {log(cp)}"
    assert not (mirror(fresh) / "gripper").exists() and "vac_pad" not in str(sorted(mirror_files(fresh))), \
        f"nothing of the unmounted client's root may land in the mirror: {sorted(mirror_files(fresh))}"


# ================================================================ L5 the same path from two levels

def test_l5_the_same_relative_path_from_two_roots_is_refused_and_the_mirror_is_left_alone(fresh, tmp_path):
    roots = write_roots(tmp_path / "roots", {"catalog": CATALOG})
    H.write_node(fresh, "lib", {"kfl004": USER["kfl004"]}, [("kfl004", (200, 0, 0))])
    sync(fresh, roots)
    before = mirror_state(fresh)
    before_stamp = stamp(fresh)

    both = write_roots(tmp_path / "roots", {CLIENT_A: {"bearings/kfl004.py": KFL004.replace("HEIGHT = 12.0", "HEIGHT = 44.0")}})
    roots.update(both)
    cp = sync(fresh, roots, expect=1)
    a, b = source(roots, "catalog", "bearings/kfl004.py"), source(roots, CLIENT_A, "bearings/kfl004.py")
    assert str(a) in log(cp) and str(b) in log(cp), f"the refusal names BOTH absolute source paths:\n{log(cp)}"
    assert mirror_state(fresh) == before, "a refused sync leaves the mirror exactly as it was"
    assert stamp(fresh) == before_stamp, "a refused sync leaves .sources.json as it was"


# ================================================================ L6 dropping the import

def test_l6_dropping_the_import_removes_the_group_from_the_mirror_and_the_stamp(fresh, tmp_path):
    roots = write_roots(tmp_path / "roots", dict({"catalog": CATALOG}, **{CLIENT_A: {"gripper/vac_pad.py": VAC_PAD}}))
    set_lib_parts(fresh, ("kfl004", "vac_pad"))
    sync(fresh, roots)
    assert {"bearings/kfl004.py", "gripper/vac_pad.py"} <= mirror_files(fresh)

    set_lib_parts(fresh, ("kfl004",))
    cp = sync(fresh, roots)
    assert mirror_files(fresh) == {"bearings/kfl004.py", "bearings/kfl004.step"}, \
        f"the group nobody imports any more is deleted from the mirror: {sorted(mirror_files(fresh))}\n{log(cp)}"
    assert not (mirror(fresh) / "gripper").exists(), "the empty group dir goes too"
    assert set(stamp(fresh)["files"]) == {"bearings/kfl004.py", "bearings/kfl004.step"}, \
        f"the stamp still lists the removed files: {sorted(stamp(fresh)['files'])}"


# ================================================================ L7 check, and sync being idempotent

def test_l7_check_passes_after_a_sync_and_a_second_sync_changes_nothing(fresh, tmp_path):
    roots = write_roots(tmp_path / "roots", {"catalog": CATALOG})
    H.write_node(fresh, "lib", {"kfl004": USER["kfl004"]}, [("kfl004", (200, 0, 0))])
    sync(fresh, roots)
    cp = check(fresh, expect=0)
    assert "library: ok" in cp.stdout, f"check says `library: ok (<n> files)`: {cp.stdout!r}"

    was = mtimes(fresh)
    cp2 = sync(fresh, roots)
    assert mtimes(fresh) == was, "a second sync must not rewrite a file (no mtime change)"
    named = [rel for rel in was if rel in cp2.stdout]
    assert not named, f"a second sync reports no change, it named {named}: {cp2.stdout!r}"
    check(fresh, expect=0)


def test_l7_check_fails_on_a_hand_edited_mirror_file_and_names_it(fresh, tmp_path):
    roots = write_roots(tmp_path / "roots", {"catalog": CATALOG})
    H.write_node(fresh, "lib", {"kfl004": USER["kfl004"]}, [("kfl004", (200, 0, 0))])
    sync(fresh, roots)

    H.edit(mirror(fresh) / "bearings/kfl004.py", "HEIGHT = 12.0", "HEIGHT = 99.0")
    cp = check(fresh, expect=1)
    assert "bearings/kfl004.py" in log(cp), f"check names the hand-edited mirror file: {log(cp)}"


def test_l7_check_fails_when_a_referenced_group_is_not_in_the_mirror(fresh, tmp_path):
    roots = write_roots(tmp_path / "roots", {"catalog": CATALOG})
    H.write_node(fresh, "lib", {"kfl004": USER["kfl004"]}, [("kfl004", (200, 0, 0))])
    sync(fresh, roots)

    shutil.rmtree(mirror(fresh) / "bearings")
    cp = check(fresh, expect=1)
    assert "bearings" in log(cp), f"check names the group the code imports but the mirror lacks: {log(cp)}"


# ================================================================ L8 two levels at once

def test_l8_a_client_group_and_a_catalog_group_both_land_with_the_right_level(tmp_path):
    roots = write_roots(tmp_path / "roots", dict({"catalog": CATALOG}, **{CLIENT_A: {"gripper/vac_pad.py": VAC_PAD}}))
    root = make_lib_project(tmp_path / "proj", parts=("kfl004", "vac_pad"))
    cp = sync(root, roots)

    assert mirror_files(root) == {"bearings/kfl004.py", "bearings/kfl004.step", "gripper/vac_pad.py"}, \
        f"both levels merge into one mirror: {sorted(mirror_files(root))}\n{log(cp)}"
    assert levels(root) == {"bearings/kfl004.py": "catalog", "bearings/kfl004.step": "catalog",
                            "gripper/vac_pad.py": CLIENT_A}, f"stamp levels: {levels(root)}"

    H.run_model(root).ok()
    ids = H.by_id(root)
    assert "vac_pad" in ids and ids["vac_pad"]["code"] == "VP-15", \
        f"the client-level part must build like any other: {sorted(ids)}"
    assert "kfl004" in ids


# ================================================================ L9 the check.py gate `library`

KIT_INPUTS = ["mechanics/cad/params.py", "mechanics/cad/parts/**/*.py"]
KIT_CMD = "python3 derive.py print {manifest}"


def write_kit(root, inputs):
    (Path(root) / "kit.json").write_text(json.dumps(
        {"timeout": 600, "inputs": inputs, "scopes": {"print": {"export": KIT_CMD}}}, ensure_ascii=False))


def check_rows(root, rule):
    """[(status, detail)] of the check.py rows whose rule is exactly `rule` (a row is `<status> <rule>: <detail>`)."""
    cp = F.run_check(root)
    text = (cp.stdout or "") + "\n" + (cp.stderr or "")
    pat = re.compile(rf"^(PASS|FAIL|WARN)\s+{re.escape(rule)}(?::\s*(.*))?$")
    return [(m.group(1), m.group(2)) for m in (pat.match(ln) for ln in text.splitlines()) if m]


def gate_rows(root):
    return check_rows(root, "library")


def gate_json_fails(root):
    cp = procgroup.run([sys.executable, str(H.SCRIPTS / "check.py"), str(root), "--no-validate", "--json"],
                       capture_output=True, text=True, timeout=300)
    lines = (cp.stdout or "").splitlines()
    assert lines, f"check.py --json printed no JSON object (rc={cp.returncode}):\n{(cp.stderr or '')[-1500:]}"
    return [f["rule"] for f in json.loads(lines[-1])["fails"]]


def test_l9_the_gate_fails_when_kit_json_inputs_do_not_cover_the_mirror(fresh, tmp_path):
    roots = write_roots(tmp_path / "roots", {"catalog": CATALOG})
    H.write_node(fresh, "lib", {"kfl004": USER["kfl004"]}, [("kfl004", (200, 0, 0))])
    sync(fresh, roots)
    write_kit(fresh, KIT_INPUTS)

    rows = gate_rows(fresh)
    assert [s for s, _ in rows] == ["FAIL"], f"a mirror outside kit.json inputs is a `library` FAIL; rows: {rows}"
    assert "library" in gate_json_fails(fresh), "check.py --json carries the library FAIL in `fails`"

    write_kit(fresh, KIT_INPUTS + [f"{MIRROR}/**/*"])
    rows = gate_rows(fresh)
    assert [s for s, _ in rows] == ["PASS"], f"covered inputs and a clean mirror: one PASS row; rows: {rows}"
    assert "library" not in gate_json_fails(fresh)


def test_l9_the_gate_fails_on_a_hand_edited_mirror(fresh, tmp_path):
    roots = write_roots(tmp_path / "roots", {"catalog": CATALOG})
    H.write_node(fresh, "lib", {"kfl004": USER["kfl004"]}, [("kfl004", (200, 0, 0))])
    sync(fresh, roots)
    write_kit(fresh, KIT_INPUTS + [f"{MIRROR}/**/*"])
    H.edit(mirror(fresh) / "bearings/kfl004.py", "HEIGHT = 12.0", "HEIGHT = 99.0")

    assert [s for s, _ in gate_rows(fresh)] == ["FAIL"], "the gate FAILs exactly when `part_library.py check` would"
    assert "library" in gate_json_fails(fresh)


def test_l9_a_project_without_a_library_gets_no_library_row(fresh):
    """The eight live projects have no mirror and no library import: no new row, PASS or FAIL."""
    write_kit(fresh, KIT_INPUTS)
    assert gate_rows(fresh) == [], f"a project with no mechanics/cad/library/ gets no library rows: {gate_rows(fresh)}"
    assert "library" not in gate_json_fails(fresh)


@pytest.mark.parametrize("rel", ["library.py", "library/__init__.py"])
def test_l9_the_gate_fails_on_a_project_file_that_shadows_the_namespace_package(fresh, tmp_path, rel):
    """mechanics/cad/library.py or library/__init__.py in the project itself: `library` is no longer the namespace dir the
    three levels merge into, so every library import would resolve somewhere else."""
    roots = write_roots(tmp_path / "roots", {"catalog": CATALOG})
    H.write_node(fresh, "lib", {"kfl004": USER["kfl004"]}, [("kfl004", (200, 0, 0))])
    sync(fresh, roots)
    write_kit(fresh, KIT_INPUTS + [f"{MIRROR}/**/*"])

    p = H.cad_dir(fresh) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# a project file that shadows the library namespace package\n")
    rows = gate_rows(fresh)
    assert [s for s, _ in rows] == ["FAIL"], f"mechanics/cad/{rel} must be a `library` FAIL; rows: {rows}"
    assert rel.rsplit("/", 1)[-1] in rows[0][1] and "library" in gate_json_fails(fresh), \
        f"the FAIL detail names the shadowing file: {rows}"


# ================================================================ L10 the real catalogue of this repo

# group, module, build() kwargs, the project's part file stem, kind, the ports the catalogue promises
REAL_PARTS = [
    ("bearings", "kfl004", {}, "unit_a", "bearings", {"mount", "hole_1", "hole_2", "shaft"}),
    ("pneumatic", "airtac_sc", {"bore": 50, "stroke": 100}, "cyl_a", "pneumatic", {"rear", "rod"}),
    ("profile", "alu_profile", {"size": 30, "length": 400.0}, "beam_a", "profile", {"end_a", "end_b"}),
]
STROKE = 100.0


def real_user_src(group, module, kwargs):
    """A project part file that uses one catalogue part and passes the project's own arguments."""
    args = ", ".join(f"{k}={v!r}" for k, v in kwargs.items())
    return ("\n        from library.%s import %s\n        {TICK}\n\n        def build():\n"
            "            _tick(%r)\n            return %s.build(%s)\n        " % (group, module, module, module, args))


@contextlib.contextmanager
def _geom_importable(tmp_path):
    """The framework's geom.py on sys.path under its project name, and the catalogue as the `library` package (a part
    imports its texts from `library.i18n`, as it does from a project's mirror), so a catalogue module can be imported here."""
    d = tmp_path / "geomshim"
    d.mkdir(parents=True, exist_ok=True)
    (d / "geom.py").write_text(F.geom_src())
    saved = sys.modules.pop("geom", None)
    lib = {k: sys.modules.pop(k) for k in list(sys.modules) if k == "library" or k.startswith("library.")}
    sys.path[:0] = [str(d), str(CATALOG_ROOT)]
    try:
        yield
    finally:
        sys.path.remove(str(d))
        sys.path.remove(str(CATALOG_ROOT))
        sys.modules.pop("geom", None)
        for k in [k for k in sys.modules if k == "library" or k.startswith("library.")]:
            sys.modules.pop(k)
        sys.modules.update(lib)
        if saved is not None:
            sys.modules["geom"] = saved


def catalog_part(tmp_path, group, module, **kwargs):
    """(module, what its build() returns), computed OUTSIDE the model build: the id, code, unit and ports the catalogue owns,
    so this test asserts what the model carries rather than re-spelling names the catalogue is free to choose."""
    with _geom_importable(tmp_path):
        path = CATALOG_ROOT / "library" / group / f"{module}.py"
        assert path.is_file(), f"the repo's catalogue has no {group}/{module}.py (looked in {CATALOG_ROOT})"
        spec = importlib.util.spec_from_file_location(f"catalog_{group}_{module}", path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m, m.build(**kwargs)


def part_id(name):
    """The id cad_incremental gives a part of that name."""
    return re.sub(r"_+", "_", re.sub(r"[^\w]+", "_", str(name))).strip("_")


def test_l10_the_repo_catalogue_parts_build_with_their_ports_and_purchase_rows(tmp_path):
    """The three parts of agent/catalog, through a real sync of the real catalogue into a real model build."""
    assert (CATALOG_ROOT / "library").is_dir(), f"no catalogue at {CATALOG_ROOT}"
    root = H.make_project(tmp_path / "proj", extra=("cat",))
    H.write_node(root, "cat", {stem: real_user_src(g, mod, kw) for g, mod, kw, stem, _, _ in REAL_PARTS},
                 [(stem, (300 + 150 * i, 0, 0)) for i, (_, _, _, stem, _, _) in enumerate(REAL_PARTS)])
    cp = lib_cli("sync", root, "--catalog", CATALOG_ROOT)
    assert cp.returncode == 0, f"sync of the real catalogue failed:\n{log(cp)}"

    run = H.run_model(root).ok()
    ids = H.by_id(root)
    for group, module, kwargs, _stem, kind, ports in REAL_PARTS:
        m, want = catalog_part(tmp_path, group, module, **kwargs)
        pid = part_id(want["name"])
        e = ids.get(pid)
        assert e, f"{group}/{module}: the model has no part {pid!r} (the catalogue names it {want['name']!r}); " \
                  f"ids: {sorted(ids)}\n{run.log[-1500:]}"
        assert e.get("process") == "bought", f"{pid}: a catalogue part is bought, not {e.get('process')!r}"
        assert e.get("code") and e["code"] == want.get("code"), f"{pid}: code {e.get('code')!r}, the catalogue says {want.get('code')!r}"
        assert e.get("kind") == kind == want.get("kind"), f"{pid}: kind {e.get('kind')!r}, expected {kind!r}"
        assert e.get("unit") and e["unit"] == want.get("unit"), f"{pid}: unit {e.get('unit')!r}, the catalogue says {want.get('unit')!r}"
        js = e.get("joints") or {}
        assert set(js) == ports == set(want["shape"].joints), \
            f"{pid}: ports in the model {sorted(js)}, the catalogue declares {sorted(want['shape'].joints)}, expected {sorted(ports)}"
        if not getattr(m, "VENDOR", None):   # an envelope, not the maker's STEP (or a module with no STEP at all): say so
            assert e.get("standin_reason"), f"{pid}: built as an envelope without a standin_reason: {sorted(e)}"
    assert ids[part_id(catalog_part(tmp_path, "bearings", "kfl004")[1]["name"])]["code"] == "KFL004", \
        "the KFL unit keeps its vendor code in the model"


def test_l10_the_cylinders_rod_joint_keeps_the_catalogue_stroke(tmp_path):
    """#16 done-when 1 for a catalogue part: the rod is a LinearJoint whose range is (0, stroke) of THIS variant."""
    root = H.make_project(tmp_path / "proj", extra=("cat",))
    H.write_node(root, "cat", {"cyl_a": real_user_src("pneumatic", "airtac_sc", {"bore": 50, "stroke": 100})},
                 [("cyl_a", (300, 0, 0))])
    cp = lib_cli("sync", root, "--catalog", CATALOG_ROOT)
    assert cp.returncode == 0, log(cp)
    H.run_model(root).ok()

    _m, want = catalog_part(tmp_path, "pneumatic", "airtac_sc", bore=50, stroke=100)
    e = H.by_id(root)[part_id(want["name"])]
    rod = (e.get("joints") or {}).get("rod")
    assert rod, f"the cylinder has no `rod` joint in the model: {sorted(e.get('joints') or {})}"
    assert rod["type"] == "linear", f"`rod` is a LinearJoint: {rod}"
    assert [float(x) for x in rod["range"]] == [0.0, STROKE], f"`rod` range {rod.get('range')}, expected (0, {STROKE:g})"


# ================================================================ L11 a forged stamp level

def test_l11_a_forged_unmounted_level_is_refused_and_the_mirror_is_left_as_it_was(fresh, tmp_path):
    """.sources.json lives inside the project, so the agent can write it and nothing recorded there proves where a file came
    from. A well-formed `client:999` entry for a file the project wrote itself, in a group the code imports and no mounted
    root holds, is the same refusal as L4: exit 1 naming the group, the file and the stamp byte-identical."""
    roots = write_roots(tmp_path / "roots", {"catalog": CATALOG})
    set_lib_parts(fresh, ("vac_pad",))                     # the code imports library.gripper; no mounted root has `gripper`
    forged = mirror(fresh) / "gripper/vac_pad.py"
    forged.parent.mkdir(parents=True, exist_ok=True)
    forged.write_text("# written by the project, in no root at all\n" + textwrap.dedent(VAC_PAD).lstrip())
    stamp_file = mirror(fresh) / ".sources.json"
    stamp_file.write_text(json.dumps(
        {"version": 1, "files": {"gripper/vac_pad.py": {"level": "client:999", "sha1": sha1(forged)}}}))
    before = (forged.read_bytes(), stamp_file.read_bytes())

    cp = sync(fresh, roots, expect=1)
    assert "gripper" in log(cp), f"the refusal names the group no mounted root holds: {log(cp)}"
    assert (forged.read_bytes(), stamp_file.read_bytes()) == before, \
        "a refused sync leaves the forged file and the stamp byte-identical"
    assert mirror_files(fresh) == {"gripper/vac_pad.py"}, f"nothing else lands in the mirror: {sorted(mirror_files(fresh))}"


@pytest.mark.parametrize("level", ["client999", "mine"])
def test_l11_check_fails_a_stamp_level_not_spelled_the_way_the_platform_writes_one(fresh, tmp_path, level):
    """The platform writes `catalog`, `client:<digits>` or `workspace`; any other level in the stamp is a FAIL naming it."""
    roots = write_roots(tmp_path / "roots", {"catalog": CATALOG})
    H.write_node(fresh, "lib", {"kfl004": USER["kfl004"]}, [("kfl004", (200, 0, 0))])
    sync(fresh, roots)
    check(fresh, expect=0)

    st = stamp(fresh)
    st["files"]["bearings/kfl004.py"]["level"] = level        # the sha1 still matches: only the level is wrong
    (mirror(fresh) / ".sources.json").write_text(json.dumps(st))
    cp = check(fresh, expect=1)
    assert level in log(cp), f"check names the malformed level {level!r}: {log(cp)}"


# ================================================================ L12 sync only ever touches what it wrote

def test_l12_a_library_folder_the_project_wrote_itself_is_refused_and_left_untouched(fresh, tmp_path):
    """mechanics/cad/library/ with no .sources.json is the project's own code, not a mirror: sync must not merge into it,
    delete from it or rewrite it."""
    roots = write_roots(tmp_path / "roots", {"catalog": CATALOG})
    H.write_node(fresh, "lib", {"kfl004": USER["kfl004"]}, [("kfl004", (200, 0, 0))])
    own = {"helpers.py": "def k():\n    return 1\n", "steps/motor.step": STEP_BESIDE}
    for rel, text in own.items():
        f = mirror(fresh) / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    before = mirror_state(fresh)

    cp = sync(fresh, roots, expect=1)
    assert MIRROR in log(cp), f"the refusal names the folder {MIRROR}: {log(cp)}"
    assert mirror_state(fresh) == before, "every file of the project's own library/ stays exactly as it was"
    assert not (mirror(fresh) / ".sources.json").exists(), "no stamp is written into a folder sync does not own"


def test_l12_a_stray_file_beside_a_mirrored_group_survives_sync_and_check_names_it(fresh, tmp_path):
    """sync removes only files its previous stamp recorded."""
    roots = write_roots(tmp_path / "roots", {"catalog": CATALOG})
    H.write_node(fresh, "lib", {"kfl004": USER["kfl004"]}, [("kfl004", (200, 0, 0))])
    sync(fresh, roots)
    stray = mirror(fresh) / "bearings" / "notes.txt"
    stray.write_text("the project's own note, next to a mirrored group\n")

    sync(fresh, roots, expect=None)
    assert stray.is_file() and stray.read_text().startswith("the project's own note"), "sync deleted a file it never wrote"
    cp = check(fresh, expect=1)
    assert "bearings/notes.txt" in log(cp), f"check names the file the stamp does not record: {log(cp)}"


# ================================================================ L13 transitive groups

HUB = """
from library.bearings import kfl004

HEIGHT = kfl004.HEIGHT + 3.0
"""

MINE_BRACKET = """
from build123d import Box
from geom import P
from library.hub import hub


def build():
    return P("mine_bracket", Box(40, 30, hub.HEIGHT), "#555555", process="mill", material="S235JR")
"""

USER_MINE = """
    from library.mine import bracket as lib
    {TICK}

    def build():
        _tick("mine_bracket")
        return lib.build()
    """


def test_l13_groups_a_library_part_imports_are_synced_transitively_and_build(tmp_path):
    """project -> library.mine (client) -> library.hub (catalog) -> library.bearings (catalog): all three are copied, the
    unrelated `misc` is not, and the model builds."""
    roots = write_roots(tmp_path / "roots", {"catalog": dict(CATALOG, **{"hub/hub.py": HUB}),
                                             CLIENT_A: {"mine/bracket.py": MINE_BRACKET}})
    root = H.make_project(tmp_path / "proj", extra=("lib",))
    H.write_node(root, "lib", {"mine_bracket": USER_MINE}, [("mine_bracket", (200, 0, 0))])
    cp = sync(root, roots)

    groups = {rel.split("/", 1)[0] for rel in mirror_files(root)}
    assert groups == {"mine", "hub", "bearings"}, \
        f"a group a mirrored library file imports is copied too, until nothing new: {sorted(groups)}\n{log(cp)}"
    run = H.run_model(root).ok()
    assert "mine_bracket" in H.by_id(root), f"the part built through two library hops is in the model: {run.log[-1200:]}"


def test_l13_check_fails_when_a_group_a_library_file_imports_is_missing_from_the_copy(fresh, tmp_path):
    roots = write_roots(tmp_path / "roots", {"catalog": dict(CATALOG, **{"hub/hub.py": HUB}),
                                             CLIENT_A: {"mine/bracket.py": MINE_BRACKET}})
    H.write_node(fresh, "lib", {"mine_bracket": USER_MINE}, [("mine_bracket", (200, 0, 0))])
    sync(fresh, roots)
    check(fresh, expect=0)

    assert (mirror(fresh) / "bearings").is_dir(), \
        f"sync did not copy `bearings`, which hub/hub.py imports: {sorted(mirror_files(fresh))}"
    # drop `bearings` from the copy AND from the stamp: the only thing left wrong is that hub/hub.py imports it
    shutil.rmtree(mirror(fresh) / "bearings")
    st = stamp(fresh)
    st["files"] = {k: v for k, v in st["files"].items() if not k.startswith("bearings/")}
    (mirror(fresh) / ".sources.json").write_text(json.dumps(st))
    cp = check(fresh, expect=1)
    assert "bearings" in log(cp), f"check names the group a mirrored library file imports but the copy lacks: {log(cp)}"


# ================================================================ L14 one catalogue part used several ways

def test_l14_one_project_part_file_placed_by_two_nodes(tmp_path):
    """parts/bought/bearing.py (a folder of part files, like parts/fasteners/) forwards kfl004; two nodes import and place it."""
    root = H.make_project(tmp_path / "proj", extra=("left", "right"))
    bought = H.cad_dir(root) / "parts" / "bought"
    bought.mkdir(parents=True)
    (bought / "__init__.py").write_text("")
    (bought / "bearing.py").write_text(textwrap.dedent(real_user_src("bearings", "kfl004", {})).replace("{TICK}", H.TICK).lstrip())
    for node, x in (("left", 300), ("right", 500)):
        d = H.cad_dir(root) / "parts" / node
        d.mkdir(parents=True)
        (d / "__init__.py").write_text(textwrap.dedent(f"""
            from build123d import Pos
            from geom import place
            from parts.bought import bearing


            def build():
                return [place(bearing.build(), Pos({x}, 0, 0))]
            """).lstrip())
    cp = lib_cli("sync", root, "--catalog", CATALOG_ROOT)
    assert cp.returncode == 0, log(cp)

    run = H.run_model(root).ok()
    _m, want = catalog_part(tmp_path, "bearings", "kfl004")
    e = H.by_id(root).get(part_id(want["name"]))
    assert e, f"no {want['name']!r} in the model: {sorted(H.by_id(root))}\n{run.log[-1200:]}"
    assert len(F.placements(e)) == 2, f"one part, two placements (one per node): {F.placements(e)}"


VARIANTS = [   # (label, group, module, kwargs): pairs that differ in one argument and so must be different parts
    ("profile mounts x+", "profile", "alu_profile", {"size": 30, "length": 400.0, "mounts": (("x+", 100),)}),
    ("profile mounts y+", "profile", "alu_profile", {"size": 30, "length": 400.0, "mounts": (("y+", 200),)}),
    ("bearing default", "bearings", "kfl004", {}),
    ("bearing flange 10", "bearings", "kfl004", {"flange_mm": 10}),
]


def test_l14_variants_that_differ_only_in_one_argument_get_their_own_ids_and_build(tmp_path):
    names = {label: catalog_part(tmp_path, g, mod, **kw)[1]["name"] for label, g, mod, kw in VARIANTS}
    assert names["profile mounts x+"] != names["profile mounts y+"], \
        f"two profiles that differ only in `mounts` must be named apart by the catalogue: {names}"
    assert names["bearing default"] != names["bearing flange 10"], \
        f"kfl004.build(flange_mm=10) must be named apart from kfl004.build(): {names}"

    root = H.make_project(tmp_path / "proj", extra=("cat",))
    stems = {label: f"v{i}" for i, (label, *_rest) in enumerate(VARIANTS)}
    H.write_node(root, "cat", {stems[label]: real_user_src(g, mod, kw) for label, g, mod, kw in VARIANTS},
                 [(stems[label], (300 + 150 * i, 0, 0)) for i, (label, *_r) in enumerate(VARIANTS)])
    cp = lib_cli("sync", root, "--catalog", CATALOG_ROOT)
    assert cp.returncode == 0, log(cp)
    run = H.run_model(root).ok()
    ids = H.by_id(root)
    missing = {label: part_id(n) for label, n in names.items() if part_id(n) not in ids}
    assert not missing, f"every variant is its own part in the model, missing {missing}; ids {sorted(ids)}\n{run.log[-1200:]}"
    js = ids[part_id(names["profile mounts x+"])].get("joints") or {}
    assert "x+_100" in js, f"the mount port of that variant reaches the model: {sorted(js)}"


# ================================================================ L15 what the scan skips

def test_l15_a_node_folder_named_vendor_is_scanned(fresh, tmp_path):
    """Only mechanics/cad/{library,build,vendor}/ are skipped: parts/vendor/ is a node like any other."""
    roots = write_roots(tmp_path / "roots", {"catalog": CATALOG})
    H.write_node(fresh, "vendor", {"kfl004": USER["kfl004"]}, [("kfl004", (200, 0, 0))])
    cp = sync(fresh, roots)
    assert "bearings/kfl004.py" in mirror_files(fresh), \
        f"an import in parts/vendor/ is a library reference: {sorted(mirror_files(fresh))}\n{log(cp)}"


# ================================================================ L16 a hand edit of the copy

def test_l16_sync_refuses_to_overwrite_a_hand_edited_copy_and_a_deleted_copy_is_restored(fresh, tmp_path):
    roots = write_roots(tmp_path / "roots", {"catalog": CATALOG})
    H.write_node(fresh, "lib", {"kfl004": USER["kfl004"]}, [("kfl004", (200, 0, 0))])
    sync(fresh, roots)
    copy, src = mirror(fresh) / "bearings/kfl004.py", source(roots, "catalog", "bearings/kfl004.py")
    H.edit(copy, "HEIGHT = 12.0", "HEIGHT = 99.0")
    edited = copy.read_bytes()

    cp = sync(fresh, roots, expect=1)
    assert "bearings/kfl004.py" in log(cp) and str(src) in log(cp), \
        f"the refusal names the edited file and its source {src}: {log(cp)}"
    assert copy.read_bytes() == edited, "the hand edit must stay exactly as edited"

    copy.unlink()
    sync(fresh, roots)
    assert copy.read_bytes() == src.read_bytes(), "deleting the copy and syncing brings the source back"
    check(fresh, expect=0)


# ================================================================ L17 a mount the script cannot place

def test_l17_a_mount_that_is_no_known_level_is_refused_naming_the_path(fresh, tmp_path):
    odd = tmp_path / "elsewhere" / "xyz"
    (odd / "library" / "bearings").mkdir(parents=True)
    (odd / "library" / "bearings" / "kfl004.py").write_text(textwrap.dedent(KFL004).lstrip())
    H.write_node(fresh, "lib", {"kfl004": USER["kfl004"]}, [("kfl004", (200, 0, 0))])
    for args in (("roots", "--mount", odd), ("sync", fresh, "--mount", odd)):
        cp = lib_cli(*args)
        assert cp.returncode == 2, f"{args[0]}: a mount that is no level exits 2, got {cp.returncode}:\n{log(cp)}"
        assert re.search(r"(?m)^FAIL\b.*" + re.escape(str(odd)), log(cp)), f"{args[0]}: a FAIL line names {odd}:\n{log(cp)}"
    assert mirror_files(fresh) == set(), "nothing is copied from a mount the script cannot place"
