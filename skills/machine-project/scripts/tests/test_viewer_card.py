"""The viewer catalogue: a part's `motion_group` and `card` reach the viewer catalogue (viewer dir parts.json).

Done-when map:
  DW1  a part with motion_group "<g>" -> every one of its placements' entries has motion_group == "<g>"; a part without it -> no
       motion_group key.
  DW2  a part with card {...} -> those keys on every one of its entries, over the guessed ones: material, kind, density, thickness,
       and name (the card name replaces the part name; a repeat keeps its suffix: «Screw», «Screw_2»). Keys not in the card keep
       today's values.
  DW3  without motion_group / card every entry is exactly as today (guard: other projects' pages must not change).
  DW4  a change of card or motion_group changes the viewer output (no stale cached viewer dir).

The model (build/model/parts.json + STEPs in each part's own coordinates + placements) is hand-written, like test_instances.py,
so these tests depend only on `derive.py viewer`.

    korpus   Box 30x20x2, laser   placed 2x: korpus, korpus_2   FULL card (name/material/kind/density/thickness), motion_group "lid"
    vint     Cylinder r2 h10, bought   placed 2x: vint, vint_2   card {name: «Screw»} only
    roller   Cylinder r5 h10, print    placed once               card {material, density} only
    shaft    Cylinder r3 h40, turn     placed once               motion_group "drum", no card
    frame    Box 60x10x5, mill         placed once               nothing (plain)
The "plain" project is the same model with every card / motion_group removed.
"""
import json
from pathlib import Path

import pytest

import helpers as H
import partfiles as F


def T(x, y, z):
    return [1, 0, 0, x, 0, 1, 0, y, 0, 0, 1, z, 0, 0, 0, 1]


KORPUS_CARD = {"name": "Housing", "material": "Steel 08ps", "kind": "made", "density": 7.85, "thickness": "0.5 mm"}
VINT_CARD = {"name": "Screw"}
ROLLER_CARD = {"material": "Steel 45", "density": 7.8}


def _shapes():
    from build123d import Box, Cylinder
    return {"korpus": Box(30, 20, 2), "vint": Cylinder(2, 10), "roller": Cylinder(5, 10), "shaft": Cylinder(3, 40),
            "frame": Box(60, 10, 5)}


# part id -> (process, node, placements [(name, loc)] or None, card or None, motion_group or None)
SPEC = {
    "korpus": ("laser", "body", [("korpus", T(0, 0, 0)), ("korpus_2", T(0, 0, 50))], KORPUS_CARD, "lid"),
    "vint": ("bought", "body", [("vint", T(40, 0, 0)), ("vint_2", T(60, 0, 0))], VINT_CARD, None),
    "roller": ("print", "drive", None, ROLLER_CARD, None),
    "shaft": ("turn", "drive", None, None, "drum"),
    "frame": ("mill", "frame", None, None, None),
}
OWNER = {"korpus": "korpus", "korpus_2": "korpus", "vint": "vint", "vint_2": "vint", "roller": "roller", "shaft": "shaft",
         "frame": "frame"}


def write_model(root, plain=False):
    root = Path(root)
    (root / "mechanics" / "cad" / "parts").mkdir(parents=True)
    (root / "build/model/step").mkdir(parents=True)
    shapes, parts = _shapes(), []
    for pid, (proc, node, pls, card, motion) in SPEC.items():
        p = H._write_part(root, pid, proc, shapes[pid], {})
        p["node"] = node
        if pls is not None:
            p["placements"] = [{"name": n, "loc": loc} for n, loc in pls]
            p["qty"] = len(pls)
        if not plain:
            if card is not None:
                p["card"] = dict(card)
            if motion is not None:
                p["motion_group"] = motion
        parts.append(p)
    H._write_parts_json(root, parts)
    return root


def set_fields(root, pid, **fields):
    """Change (value) or drop (None) top-level fields of one part in build/model/parts.json; the STEP stays byte-identical."""
    f = Path(root) / "build/model/parts.json"
    data = json.loads(f.read_text())
    for p in data["parts"]:
        if p["id"] == pid:
            for k, v in fields.items():
                if v is None:
                    p.pop(k, None)
                else:
                    p[k] = v
    f.write_text(json.dumps(data))


def viewer(root):
    d = H.run_derive(root, "viewer").ok()
    vd = F.abs_path(root, d.manifest["viewer_dir"])
    return {p["id"]: p for p in json.loads((vd / "parts.json").read_text())["parts"]}


@pytest.fixture
def proj(tmp_path):
    return write_model(tmp_path / "proj")


@pytest.fixture
def plain(tmp_path):
    return write_model(tmp_path / "plain", plain=True)


# ================================================================ DW1: motion_group
def test_motion_group_on_every_placement_of_the_part(proj):
    """DW1: korpus (motion_group "lid", placed 2x) -> korpus and korpus_2 both carry "lid"; shaft (placed once) carries "drum"."""
    cat = viewer(proj)
    assert cat["korpus"].get("motion_group") == "lid", cat["korpus"]
    assert cat["korpus_2"].get("motion_group") == "lid", cat["korpus_2"]
    assert cat["shaft"].get("motion_group") == "drum", cat["shaft"]


def test_part_without_motion_group_has_no_motion_group_key(proj):
    """DW1: vint, vint_2, roller, frame have no motion_group in parts.json -> no key at all (not null, not "")."""
    cat = viewer(proj)
    for vid in ("vint", "vint_2", "roller", "frame"):
        assert "motion_group" not in cat[vid], (vid, cat[vid].get("motion_group"))


# ================================================================ DW2: card
def test_full_card_overrides_guessed_fields_on_every_placement(proj):
    """DW2: korpus's card sets material, kind, density, thickness on korpus AND korpus_2, over today's name-based guess."""
    cat = viewer(proj)
    for vid in ("korpus", "korpus_2"):
        e = cat[vid]
        assert e.get("material") == "Steel 08ps", (vid, e.get("material"))
        assert e.get("kind") == "made", (vid, e.get("kind"))
        assert e.get("density") == pytest.approx(7.85), (vid, e.get("density"))
        assert e.get("thickness") == "0.5 mm", (vid, e.get("thickness"))


def test_card_name_replaces_part_name_and_repeat_keeps_suffix(proj):
    """DW2: card name «Housing» on korpus placed as korpus, korpus_2 -> «Housing», «Housing_2»; «Screw» on vint, vint_2 -> «Screw»,
    «Screw_2». Viewer ids stay the placement ids."""
    cat = viewer(proj)
    assert {vid: cat[vid]["name"] for vid in ("korpus", "korpus_2", "vint", "vint_2")} == \
        {"korpus": "Housing", "korpus_2": "Housing_2", "vint": "Screw", "vint_2": "Screw_2"}
    assert sorted(cat) == sorted(OWNER), sorted(cat)


def test_keys_not_in_the_card_keep_todays_values(proj, plain):
    """DW2: vint's card has only a name, roller's only material + density. Every other key of their entries equals the entry the
    same part gets with no card (today's guessed kind / material, module, color, bbox, files, part_id ...)."""
    cat, base = viewer(proj), viewer(plain)
    for vid, card in (("vint", VINT_CARD), ("vint_2", VINT_CARD), ("roller", ROLLER_CARD)):
        got = {k: v for k, v in cat[vid].items() if k not in card}
        want = {k: v for k, v in base[vid].items() if k not in card}
        assert got == want, f"{vid}: keys outside the card changed:\n got  {got}\n want {want}"
    assert cat["roller"].get("material") == "Steel 45", cat["roller"]
    assert cat["roller"].get("density") == pytest.approx(7.8), cat["roller"]


# ================================================================ DW3: guard
# today's non-geometric fields of the plain catalogue (geometry: bbox_mm / volume_mm3 / triangles compared between projects)
TODAY = {
    "korpus": {"name": "korpus", "module": "body"},
    "korpus_2": {"name": "korpus_2", "module": "body", "part_id": "korpus"},
    "vint": {"name": "vint", "module": "body"},
    "vint_2": {"name": "vint_2", "module": "body", "part_id": "vint"},
    "roller": {"name": "roller", "module": "drive"},
    "shaft": {"name": "shaft", "module": "drive"},
    "frame": {"name": "frame", "module": "frame"},
}
# today's name-based guess for these part names, and the fixture's colour
TODAY_COMMON = {"kind": "purchased", "material": "other", "color": "#999999", "instances": 1, "drawing": None, "type": None}
TODAY_KEYS = {"id", "name", "drawing", "kind", "material", "module", "color", "instances", "bbox_mm", "volume_mm3", "triangles",
              "files", "type"}


def test_plain_model_catalogue_is_exactly_todays(plain):
    """DW3 (regression guard, passes before and after): with no card / motion_group anywhere, the entries have today's key set
    (no motion_group, density, thickness), today's names (part name + suffix), modules and part_id."""
    cat = viewer(plain)
    assert sorted(cat) == sorted(TODAY)
    for vid, want in TODAY.items():
        want = dict(TODAY_COMMON, **want)
        e = cat[vid]
        keys = TODAY_KEYS | ({"part_id"} if "part_id" in want else set())
        assert set(e) == keys, f"{vid}: keys {sorted(e)} != today's {sorted(keys)}"
        for k, v in want.items():
            assert e[k] == v, (vid, k, e[k])


def test_part_without_card_or_motion_group_is_unchanged_next_to_decorated_parts(proj, plain):
    """DW3: frame has neither field; its entry in the project where other parts have card / motion_group equals its entry in the
    plain project, key for key."""
    cat, base = viewer(proj), viewer(plain)
    assert cat["frame"] == base["frame"], f"frame changed:\n got  {cat['frame']}\n want {base['frame']}"


# ================================================================ DW4: no stale cached viewer
def test_card_change_reaches_viewer_without_geometry_change(proj):
    """DW4: derive once, then change only korpus's card in parts.json (same STEP, same key, same sha) and derive again: the new
    card values show, not the cached ones."""
    viewer(proj)
    set_fields(proj, "korpus", card=dict(KORPUS_CARD, name="Cover", material="AISI 304", density=7.9))
    cat = viewer(proj)
    assert cat["korpus"]["name"] == "Cover", cat["korpus"]
    assert cat["korpus_2"]["name"] == "Cover_2", cat["korpus_2"]
    assert cat["korpus_2"].get("material") == "AISI 304", cat["korpus_2"]
    assert cat["korpus"].get("density") == pytest.approx(7.9), cat["korpus"]


def test_card_removed_reaches_viewer(proj):
    """DW4: removing vint's card after a derive brings back today's names vint, vint_2."""
    before = viewer(proj)
    assert (before["vint"]["name"], before["vint_2"]["name"]) == ("Screw", "Screw_2"), "precondition: the card name shows (DW2)"
    set_fields(proj, "vint", card=None)
    cat = viewer(proj)
    assert (cat["vint"]["name"], cat["vint_2"]["name"]) == ("vint", "vint_2"), (cat["vint"]["name"], cat["vint_2"]["name"])


def test_motion_group_change_and_addition_reach_viewer(proj):
    """DW4: after a derive, korpus's motion_group "lid" -> "door" and frame gets motion_group "base": both show on re-derive."""
    viewer(proj)
    set_fields(proj, "korpus", motion_group="door")
    set_fields(proj, "frame", motion_group="base")
    cat = viewer(proj)
    assert (cat["korpus"].get("motion_group"), cat["korpus_2"].get("motion_group")) == ("door", "door"), cat["korpus"]
    assert cat["frame"].get("motion_group") == "base", cat["frame"]


def test_part_with_in_viewer_false_is_not_on_the_page_but_keeps_its_kits(proj):
    """A part the machine page must not show (an assembly gauge standing at the origin) says `in_viewer: false`: no catalogue entry,
    no glb node; its other scopes are untouched (it is still printed / in model.step)."""
    pid = next(iter(SPEC))
    set_fields(proj, pid, in_viewer=False)
    cat = set(viewer(proj))
    assert not any(i == pid or i.startswith(pid + "_") for i in cat), sorted(cat)
    d = H.run_derive(proj, "step").ok()
    assert d.manifest.get("model_step"), d.manifest
