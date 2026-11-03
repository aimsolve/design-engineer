"""aas_sync.py reads build/model/parts.json when the cached model exists, else build/parts.json."""
import json
import subprocess
import procgroup
import sys

import helpers as H


def _project(tmp_path, legacy=True, model=False):
    root = tmp_path / "p"
    procgroup.run([sys.executable, str(H.SCRIPTS / "scaffold.py"), str(root), "--name", "m", "--cad", "build123d"], check=True, capture_output=True)
    (root / "build/model").mkdir(parents=True)
    if legacy:
        (root / "build/parts.json").write_text(json.dumps({"parts": [{"id": "legacy_part", "name": "legacy"}]}))
    if model:
        (root / "build/model/parts.json").write_text(json.dumps({"parts": [{"id": "model_part", "name": "model", "process": "bought", "kind": "fasteners"}]}))
    return root


def _sync(root, *a):
    cp = procgroup.run([sys.executable, str(H.SCRIPTS / "aas_sync.py"), str(root), *a], capture_output=True, text=True)
    assert cp.returncode == 0, cp.stderr
    return (root / "aas" / "m.aas.json").read_text()


def test_default_is_the_cached_model_when_it_exists(tmp_path):
    text = _sync(_project(tmp_path, legacy=True, model=True))
    assert "model_part" in text and "legacy_part" not in text
    assert '"bought"' in text.lower() or "bought" in text           # Kind follows the process, not the bought kind
    assert "fasteners" not in text


def test_default_falls_back_to_build_parts_json(tmp_path):
    text = _sync(_project(tmp_path, legacy=True, model=False))
    assert "legacy_part" in text


def test_explicit_parts_wins(tmp_path):
    root = _project(tmp_path, legacy=True, model=True)
    assert "legacy_part" in _sync(root, "--parts", "build/parts.json")


# --- issue #29: HierarchicalStructures follows the placement nodes of build/model/parts.json --------------------------------
# A part may carry "placements": [{"name", "node", "loc"}]; each placement is one copy of the part placed by that node.
# Nodes are flat names; node "" is the model root. Leaves are found by their SameAs.second -> PartCatalog P_<slug(id)>.

import importlib.util

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "step-import" / "scripts"))
import aas_lib  # noqa: E402

HS_ID = f"{aas_lib.ID}m/sm/HierarchicalStructures"     # a new project's ids: the configured base (DE_AAS_BASE)
PC_ID = f"{aas_lib.ID}m/sm/PartCatalog"
EYE = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]


def _pl(name, node):
    return {"name": name, "node": node, "loc": list(EYE)}


def _model_project(tmp_path, parts):
    root = _project(tmp_path, legacy=False, model=False)
    (root / "build/model/parts.json").write_text(json.dumps({"parts": parts}))
    return root


def _aas(root):
    return json.loads(_sync(root))


def _entry(d):
    hs = next(s for s in d["submodels"] if s["idShort"] == "HierarchicalStructures")
    return next(e for e in hs["submodelElements"] if e["idShort"] == "EntryNode")


def _text(e):
    return (e.get("displayName") or [{}])[0].get("text")


def _children(e):
    return [c for c in e.get("statements", []) if c.get("modelType") == "Entity"]


def _same_as(e):
    return next((c for c in e.get("statements", []) if c.get("idShort") == "SameAs"), None)


def _part_of(e):
    """The PartCatalog idShort a leaf points at, or None for a node entity."""
    sa = _same_as(e)
    return sa["second"]["keys"][-1]["value"] if sa else None


def _bulk(e):
    p = next((c for c in e.get("statements", []) if c.get("idShort") == "BulkCount"), None)
    return None if p is None else int(float(p["value"]))


def _node(entry, text):
    hits = [c for c in _children(entry) if _part_of(c) is None and _text(c) == text]
    assert len(hits) == 1, f"want exactly one node entity with displayName {text!r} under EntryNode, got {[_text(c) for c in _children(entry)]}"
    n = hits[0]
    assert n["modelType"] == "Entity" and n.get("entityType") == "CoManagedEntity"
    assert n["idShort"].startswith("a_"), n["idShort"]
    return n


def _leaves(parent, pid):
    return [c for c in _children(parent) if _part_of(c) == pid]


def _first_path(leaf):
    keys = _same_as(leaf)["first"]["keys"]
    assert keys[0] == {"type": "Submodel", "value": HS_ID}
    assert all(k["type"] == "Entity" for k in keys[1:])
    return [k["value"] for k in keys[1:]]


def _two_node_parts():
    return [
        {"id": "bracket", "name": "Bracket", "process": "printed", "node": "parts",
         "placements": [_pl("bracket_1", "A"), _pl("bracket_2", "A"), _pl("bracket_3", "B")]},
        {"id": "plate", "name": "Plate", "process": "printed", "node": "parts", "placements": [_pl("plate_1", "A")]},
        {"id": "screw_m3", "name": "Screw M3", "process": "bought", "node": "B", "qty": 4},
        {"id": "nut_m3", "name": "Nut M3", "process": "bought", "node": "A"},
        {"id": "base", "name": "Base", "process": "printed", "node": "", "placements": [_pl("base_1", "")]},
    ]


def test_part_placed_by_two_nodes_is_a_leaf_under_each_with_its_count(tmp_path):
    """Done-when 1: one part placed twice by node A and once by node B -> a node entity per node, a leaf under each,
    BulkCount 2 under A and 1 under B, SameAs.first = EntryNode -> node -> leaf."""
    entry = _entry(_aas(_model_project(tmp_path, _two_node_parts())))
    a, b = _node(entry, "A"), _node(entry, "B")
    assert _leaves(entry, "P_bracket") == [], "a placed part must not also sit flat under EntryNode"
    la, lb = _leaves(a, "P_bracket"), _leaves(b, "P_bracket")
    assert len(la) == 1 and len(lb) == 1, "one leaf per (node, part), not one per placement"
    assert _bulk(la[0]) == 2 and _bulk(lb[0]) == 1
    assert la[0]["idShort"].startswith("p_bracket_")
    assert _first_path(la[0]) == ["EntryNode", a["idShort"], la[0]["idShort"]]
    assert _first_path(lb[0]) == ["EntryNode", b["idShort"], lb[0]["idShort"]]
    pl = _leaves(a, "P_plate")
    assert len(pl) == 1 and _bulk(pl[0]) == 1 and _leaves(b, "P_plate") == []


def test_unplaced_part_sits_under_its_node_with_qty(tmp_path):
    """Done-when 2: a part without placements but with "node" sits under that node; BulkCount = qty, default 1."""
    entry = _entry(_aas(_model_project(tmp_path, _two_node_parts())))
    a, b = _node(entry, "A"), _node(entry, "B")
    s = _leaves(b, "P_screw_m3")
    assert len(s) == 1 and _bulk(s[0]) == 4
    assert _first_path(s[0]) == ["EntryNode", b["idShort"], s[0]["idShort"]]
    n = _leaves(a, "P_nut_m3")
    assert len(n) == 1 and _bulk(n[0]) == 1
    assert _leaves(entry, "P_screw_m3") == [] and _leaves(entry, "P_nut_m3") == []


def test_placement_at_root_node_is_directly_under_entry_node(tmp_path):
    """Done-when 3: a placement with node "" puts the leaf directly under EntryNode (no node entity named "")."""
    entry = _entry(_aas(_model_project(tmp_path, _two_node_parts())))
    base = _leaves(entry, "P_base")
    assert len(base) == 1 and _bulk(base[0]) == 1
    assert _first_path(base[0]) == ["EntryNode", base[0]["idShort"]]
    assert not [c for c in _children(entry) if _part_of(c) is None and _text(c) in ("", None)]
    for n in (_node(entry, "A"), _node(entry, "B")):
        assert _leaves(n, "P_base") == []


def _flat_leaf(pid, name):
    sem = lambda v: {"type": "ExternalReference", "keys": [{"type": "GlobalReference", "value": v}]}
    leaf = "p_" + pid + "_0"
    return {
        "idShort": leaf, "displayName": [{"language": "und", "text": name}], "modelType": "Entity",
        "semanticId": sem("https://admin-shell.io/idta/HierarchicalStructures/Node/1/0"),
        "statements": [{
            "idShort": "SameAs", "modelType": "RelationshipElement",
            "semanticId": sem("https://admin-shell.io/idta/HierarchicalStructures/SameAs/1/0"),
            "first": {"type": "ModelReference", "keys": [
                {"type": "Submodel", "value": HS_ID}, {"type": "Entity", "value": "EntryNode"}, {"type": "Entity", "value": leaf}]},
            "second": {"type": "ModelReference", "keys": [
                {"type": "Submodel", "value": PC_ID}, {"type": "SubmodelElementCollection", "value": "Parts"},
                {"type": "SubmodelElementCollection", "value": "P_" + pid}]},
        }],
        "entityType": "CoManagedEntity",
    }


def test_old_layout_without_placements_keeps_the_flat_bom(tmp_path):
    """Done-when 4: a parts list where no part has "placements" gives exactly the pre-#29 HierarchicalStructures:
    every part one leaf p_<slug>_0 directly under EntryNode, no BulkCount (even with qty). Expected value pinned by hand."""
    root = _project(tmp_path, legacy=False, model=False)
    (root / "build/parts.json").write_text(json.dumps({"parts": [
        {"id": "Frame Plate", "name": "frame", "qty": 3},
        {"id": "bolt_m4", "name": "bolt", "process": "bought"},
    ]}))
    d = json.loads(_sync(root, "--parts", "build/parts.json"))
    hs = next(s for s in d["submodels"] if s["idShort"] == "HierarchicalStructures")
    sem = lambda v: {"type": "ExternalReference", "keys": [{"type": "GlobalReference", "value": v}]}
    assert hs == {
        "idShort": "HierarchicalStructures", "modelType": "Submodel", "id": HS_ID,
        "semanticId": sem("https://admin-shell.io/idta/HierarchicalStructures/1/1/Submodel"),
        "submodelElements": [
            {"idShort": "ArcheType", "modelType": "Property",
             "semanticId": sem("https://admin-shell.io/idta/HierarchicalStructures/ArcheType/1/0"),
             "value": "Full", "valueType": "xs:string"},
            {"idShort": "EntryNode", "displayName": [{"language": "und", "text": "m"}], "modelType": "Entity",
             "semanticId": sem("https://admin-shell.io/idta/HierarchicalStructures/EntryNode/1/0"),
             "entityType": "CoManagedEntity",
             "statements": [_flat_leaf("Frame_Plate", "frame"), _flat_leaf("bolt_m4", "bolt")]},
        ],
    }


def _sibling_dupes(el, path="", out=None):
    out = [] if out is None else out
    for key in ("submodelElements", "statements", "value", "annotations"):
        kids = el.get(key) if isinstance(el, dict) else None
        if not isinstance(kids, list):
            continue
        ids = [k.get("idShort") for k in kids if isinstance(k, dict) and k.get("idShort")]
        out += [f"{path}/{key}: {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
        for k in kids:
            if isinstance(k, dict):
                _sibling_dupes(k, f"{path}/{k.get('idShort', '?')}", out)
    return out


def test_placement_aas_passes_structure_check_and_sibling_ids_are_unique(tmp_path):
    """Done-when 5: the written AAS passes check.structure_problems and every idShort is unique among its siblings."""
    spec = importlib.util.spec_from_file_location("mp_check", H.SCRIPTS / "check.py")
    check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check)
    d = _aas(_model_project(tmp_path, _two_node_parts()))
    assert check.structure_problems(d) == []
    dupes = []
    for sm in d["submodels"]:
        _sibling_dupes(sm, sm["idShort"], dupes)
    assert dupes == []
    entry = _entry(d)
    assert _node(entry, "A") and _node(entry, "B")      # not vacuous: the placement tree is really there


def test_node_names_that_slug_alike_get_distinct_ids(tmp_path):
    root = _model_project(tmp_path, [
        {"id": "x", "name": "X", "process": "printed", "node": "parts", "placements": [_pl("x_1", "A B"), _pl("x_2", "A_B")]}])
    entry = _entry(_aas(root))
    ids = [c["idShort"] for c in _children(entry)]
    assert len(ids) == len(set(ids)) == 2
    for text in ("A B", "A_B"):
        n = _node(entry, text)
        assert _first_path(_leaves(n, "P_x")[0]) == ["EntryNode", n["idShort"], "p_x_0"]


def test_placement_without_node_is_in_the_parts_own_node(tmp_path):
    """As derive.placed() (the viewer): a placement whose node is null/empty counts under the part's own node."""
    root = _model_project(tmp_path, [
        {"id": "x", "name": "X", "process": "printed", "node": "A", "placements": [_pl("x_1", None), _pl("x_2", "A")]}])
    entry = _entry(_aas(root))
    assert [_bulk(l) for l in _leaves(_node(entry, "A"), "P_x")] == [2]
    assert _leaves(entry, "P_x") == []


def test_part_ids_that_slug_alike_get_distinct_leaf_ids(tmp_path):
    root = _model_project(tmp_path, [
        {"id": "a b", "name": "1", "process": "printed", "node": "n", "placements": [_pl("ab_1", "A")]},
        {"id": "a_b", "name": "2", "process": "printed", "node": "n", "placements": [_pl("ab_2", "A")]}])
    ids = [c["idShort"] for c in _children(_node(_entry(_aas(root)), "A"))]
    assert len(ids) == len(set(ids)) == 2
