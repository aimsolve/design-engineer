"""derive.py node_paths: the viewer groups of a model converted from a client's CAD (node TITLEs). A sub-assembly the client
reuses under two parents lives in the folder of the first one; each copy must still be shown under its own parent."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import derive  # noqa: E402
from samples_i18n import SAMPLES  # noqa: E402

RU = SAMPLES["ru"]


def _pl(name, node, to=None):
    return {"name": name, "node": node, **({"joint": {"to": to}} if to else {})}


def _project(tmp_path):
    base = tmp_path / "mechanics" / "cad" / "parts"
    files = {
        "m/__init__.py": 'TITLE = "Machine"\nfrom . import a, b\n',
        "m/a/__init__.py": 'TITLE = "Conveyor A"\nfrom . import frame\n',
        "m/a/frame/__init__.py": 'TITLE = "Frame"\n',
        "m/b/__init__.py": 'TITLE = "Conveyor B"\nfrom parts.m.a import frame\n',
    }
    for rel, text in files.items():
        (base / rel).parent.mkdir(parents=True, exist_ok=True)
        (base / rel).write_text(text, encoding="utf-8")
    return tmp_path


def test_reused_sub_assembly_shows_under_each_parent(tmp_path):
    titles, children = derive._node_graph(_project(tmp_path))
    assert children["m/b"] == {"m/a/frame"} and "m/a/frame" in children["m/a"]
    parts = [{"placements": [
        _pl("base", "m"),
        _pl("a_plate", "m/a", "base"), _pl("a_leg", "m/a/frame", "a_plate"), _pl("a_leg_2", "m/a/frame", "a_leg"),
        _pl("b_plate", "m/b", "base"), _pl("b_leg", "m/a/frame", "b_plate"), _pl("b_leg_2", "m/a/frame", "b_leg"),
    ]}]
    paths = derive.node_paths(parts, titles, children)
    names = lambda n: [t for _, t in paths[n]]   # noqa: E731
    assert names("a_leg_2") == ["Machine", "Conveyor A", "Frame"]
    assert names("b_leg_2") == ["Machine", "Conveyor B", "Frame"]
    # the two copies of the frame are two groups, each keyed by its own top placement
    assert paths["a_leg_2"][-1][0] == ("m/a/frame", "a_leg") and paths["b_leg_2"][-1][0] == ("m/a/frame", "b_leg")
    assert paths["b_plate"][-1][0] == ("m/b", "b_plate")


def test_folders_without_imports_still_nest(tmp_path):
    titles = {"m": "Machine", "m/a": "A"}
    parts = [{"placements": [_pl("base", "m"), _pl("x", "m/a", "base"), _pl("y", "m/a", "x")]}]
    paths = derive.node_paths(parts, titles)
    assert [t for _, t in paths["y"]] == ["Machine", "A"] and paths["y"][-1][0] == ("m/a", "x")


def test_a_node_anchored_by_its_own_sub_node(tmp_path):
    """A conveyor with no part of its own is docked by its frame (a sub-node): each copy is still one «Conveyor» group under
    the machine, holding its frame and its own parts."""
    base = tmp_path / "mechanics" / "cad" / "parts"
    for rel, text in {"m/__init__.py": 'TITLE = "Machine"\nfrom . import a\n', "m/a/__init__.py": 'TITLE = "Conveyor"\nfrom . import frame\n',
                      "m/a/frame/__init__.py": 'TITLE = "Frame"\n'}.items():
        (base / rel).parent.mkdir(parents=True, exist_ok=True)
        (base / rel).write_text(text, encoding="utf-8")
    titles, children = derive._node_graph(tmp_path)
    parts = [{"placements": [
        _pl("base", "m"),
        _pl("leg1", "m/a/frame", "base"), _pl("foot1", "m/a/frame", "leg1"), _pl("motor1", "m/a", "leg1"),
        _pl("leg2", "m/a/frame", "base"), _pl("foot2", "m/a/frame", "leg2"), _pl("motor2", "m/a", "leg2"),
    ]}]
    paths = derive.node_paths(parts, titles, children)
    for k in "12":
        assert paths[f"foot{k}"] == [(("m", "base"), "Machine"), (("m/a", f"leg{k}"), "Conveyor"), (("m/a/frame", f"leg{k}"), "Frame")]
        assert paths[f"motor{k}"] == [(("m", "base"), "Machine"), (("m/a", f"leg{k}"), "Conveyor")]


def test_model_step_names_from_a_code_page_maker_step(tmp_path):
    """A product name a maker's STEP carries in cp1251 (not UTF-8) comes out as its Cyrillic, ASCII-escaped, not a crash."""
    f = tmp_path / "m.step"
    f.write_bytes(b"ISO-10303-21;\n#1=PRODUCT('" + RU["motor"].encode("cp1251") + b"','',$,());\n#2=PRODUCT('plain','',$,());\n")
    derive._fix_step_names(f)
    out = f.read_bytes()
    out.decode("ascii")
    assert "\\X2\\0414\\X0\\".encode() in out and b"'plain'" in out, out


def test_two_copies_docked_onto_each_other_stay_two_groups(tmp_path):
    """The converter docks the second copy of a sub-assembly onto the first (`c8[0]...connect_to(c5[0]...)`): still two
    groups, one per copy."""
    base = tmp_path / "mechanics" / "cad" / "parts"
    for rel, text in {"m/__init__.py": 'TITLE = "Panel"\nfrom . import t\n\n\ndef build():\n    c0 = t.build()\n    c1 = t.build()\n'
                                       '    c0[0]["shape"].joints["to_board"].connect_to(c1[0]["shape"].joints["dock"])\n',
                      "m/t/__init__.py": 'TITLE = "Terminal"\n'}.items():
        (base / rel).parent.mkdir(parents=True, exist_ok=True)
        (base / rel).write_text(text, encoding="utf-8")
    titles, children = derive._node_graph(tmp_path)
    seams = derive._copy_seams(tmp_path)
    assert seams.get(("to_board", "dock")) == {("m", "m/t")}
    parts = [{"placements": [
        _pl("panel", "m"),
        {"name": "board1", "node": "m/t", "joint": {"to": "panel", "at": "to_t", "by": "dock"}},
        _pl("usb1", "m/t", "board1"),
        {"name": "board2", "node": "m/t", "joint": {"to": "board1", "at": "to_board", "by": "dock"}},
        _pl("usb2", "m/t", "board2"),
    ]}]
    paths = derive.node_paths(parts, titles, children, seams)
    assert paths["usb1"][-1][0] == ("m/t", "board1") and paths["usb2"][-1][0] == ("m/t", "board2"), (paths["usb1"], paths["usb2"])
    assert [t for _, t in paths["usb2"]] == ["Panel", "Terminal"]


def test_a_reused_sub_assembly_docked_alike_by_both_parents(tmp_path):
    """Both parents dock their copy of the shared frame with the same joints: each copy still shows under its own parent."""
    base = tmp_path / "mechanics" / "cad" / "parts"
    dock = '\n\ndef build():\n    c1 = frame.build()\n    c0["shape"].joints["to_frame"].connect_to(c1[0]["shape"].joints["dock"])\n'
    for rel, text in {"m/__init__.py": 'TITLE = "Machine"\nfrom . import a, b\n',
                      "m/a/__init__.py": 'TITLE = "Conveyor A"\nfrom . import frame' + dock,
                      "m/a/frame/__init__.py": 'TITLE = "Frame"\n',
                      "m/b/__init__.py": 'TITLE = "Conveyor B"\nfrom parts.m.a import frame' + dock}.items():
        (base / rel).parent.mkdir(parents=True, exist_ok=True)
        (base / rel).write_text(text, encoding="utf-8")
    titles, children = derive._node_graph(tmp_path)
    seams = derive._copy_seams(tmp_path)
    j = lambda to: {"to": to, "at": "to_frame", "by": "dock"}   # noqa: E731
    parts = [{"placements": [
        _pl("base", "m"), _pl("a_plate", "m/a", "base"), _pl("b_plate", "m/b", "base"),
        {"name": "a_leg", "node": "m/a/frame", "joint": j("a_plate")}, _pl("a_foot", "m/a/frame", "a_leg"),
        {"name": "b_leg", "node": "m/a/frame", "joint": j("b_plate")}, _pl("b_foot", "m/a/frame", "b_leg"),
    ]}]
    paths = derive.node_paths(parts, titles, children, seams)
    assert [t for _, t in paths["a_foot"]] == ["Machine", "Conveyor A", "Frame"]
    assert [t for _, t in paths["b_foot"]] == ["Machine", "Conveyor B", "Frame"]


def test_shared_sub_nodes_docked_onto_each_other_inside_the_second_parent(tmp_path):
    """Two products share two sub-nodes (both live in the first product's folder); in the second product the plate docks
    onto the side, the side onto the product's own base: the plate's copy shows under the second product, not the first."""
    base = tmp_path / "mechanics" / "cad" / "parts"
    for rel, text in {"m/__init__.py": 'TITLE = "Machine"\nfrom . import a, b\n',
                      "m/a/__init__.py": 'TITLE = "Conveyor"\nfrom . import side, plate\n',
                      "m/a/side/__init__.py": 'TITLE = "Side"\n', "m/a/plate/__init__.py": 'TITLE = "Plate"\n',
                      "m/b/__init__.py": 'TITLE = "Conveyor"\nfrom parts.m.a import side, plate\n'}.items():
        (base / rel).parent.mkdir(parents=True, exist_ok=True)
        (base / rel).write_text(text, encoding="utf-8")
    titles, children = derive._node_graph(tmp_path)
    parts = [{"placements": [
        _pl("base", "m"),
        _pl("a_base", "m/a", "base"), _pl("a_side", "m/a/side", "a_base"), _pl("a_plate", "m/a/plate", "a_side"),
        _pl("b_base", "m/b", "base"), _pl("b_side", "m/a/side", "b_base"), _pl("b_plate", "m/a/plate", "b_side"),
    ]}]
    paths = derive.node_paths(parts, titles, children, derive._copy_seams(tmp_path))
    assert paths["b_plate"][1][0] == ("m/b", "b_base"), paths["b_plate"]
    assert paths["a_plate"][1][0] == ("m/a", "a_base"), paths["a_plate"]


def test_only_a_machine_ported_from_his_cad_gets_his_tree(tmp_path):
    """The viewer shows his tree when the machine node parts/__init__.py docks has a TITLE (a full port); a titled node
    ported into a project of our own leaves that project's viewer as it was (review 5)."""
    base = tmp_path / "mechanics" / "cad" / "parts"
    ours = {"__init__.py": "from . import example\n\n\ndef build():\n    return example.build()\n",
            "example/__init__.py": "from . import pusher\n", "example/pusher/__init__.py": 'TITLE = "Pusher"\n'}
    for rel, text in ours.items():
        (base / rel).parent.mkdir(parents=True, exist_ok=True)
        (base / rel).write_text(text, encoding="utf-8")
    assert not derive.is_ported(*derive._node_graph(tmp_path))
    (base / "example" / "__init__.py").write_text('TITLE = "Machine"\nfrom . import pusher\n', encoding="utf-8")
    assert derive.is_ported(*derive._node_graph(tmp_path))


def test_a_root_with_no_part_of_its_own_holds_its_sub_assemblies(tmp_path):
    """His machine holds only sub-assemblies (the usual SolidWorks top): the frame anchors it, two rollers dock on the frame;
    every part is under «Machine», the two rollers are two groups (critic)."""
    titles = {"m": "Machine", "m/frame": "Frame", "m/roller": "Roller"}
    children = {".": {"m"}, "m": {"m/frame", "m/roller"}, "m/frame": set(), "m/roller": set()}
    parts = [{"placements": [_pl("beam", "m/frame"), _pl("leg", "m/frame", "beam"),
                             _pl("shaft", "m/roller", "beam"), _pl("shaft_2", "m/roller", "beam")]}]
    paths = derive.node_paths(parts, titles, children)
    names = lambda n: [t for _, t in paths[n]]   # noqa: E731
    assert names("beam") == ["Machine", "Frame"] and names("leg") == ["Machine", "Frame"]
    assert names("shaft") == ["Machine", "Roller"] and names("shaft_2") == ["Machine", "Roller"]
    assert paths["shaft"][-1][0] != paths["shaft_2"][-1][0], "two rollers, two groups"
    assert paths["beam"][0][0] == paths["shaft"][0][0], "one machine group"


def test_a_label_pair_that_docks_two_levels_does_not_split_the_parent(tmp_path):
    """(to_noga, dock) docks the frame into the installer AND one leg onto the other inside the frame: the frame stays one
    group with both legs, two leg groups (a real machine's installer / frame)."""
    titles = {"u": "Installer", "u/r": "Frame", "u/r/k": "Carcass", "u/r/n": "Leg"}
    children = {"u": {"u/r"}, "u/r": {"u/r/k", "u/r/n"}, "u/r/k": set(), "u/r/n": set()}
    seams = {("to_noga", "dock"): {("u", "u/r"), ("u/r", "u/r/n")}, ("to_noga", "dock_2"): {("u/r", "u/r/n")}}

    def pl(name, node, to=None, at=None, by=None):
        return {"name": name, "node": node, **({"joint": {"to": to, "at": at, "by": by}} if to else {})}
    parts = [{"placements": [pl("base", "u"), pl("frame", "u/r/k", "base", "to_noga", "dock"),
                             pl("leg_2", "u/r/n", "frame", "to_noga", "dock_2"), pl("leg", "u/r/n", "leg_2", "to_noga", "dock")]}]
    paths = derive.node_paths(parts, titles, children, seams)
    rama = {[k for k, t in paths[n] if t == "Frame"][0] for n in ("frame", "leg", "leg_2")}
    assert len(rama) == 1, f"the frame is split: {rama}"
    assert paths["leg"][-1][0] != paths["leg_2"][-1][0], "two legs, two groups"


def test_a_node_placed_by_a_location_joins_its_parents_copy(tmp_path):
    """A ported node appended to the converted machine's list with place(..., Location) (no joint): it shows inside the one
    machine group, not in a second copy of it (critic)."""
    titles = {"m": "Machine", "m/frame": "Frame", "m/new": "New node"}
    children = {".": {"m"}, "m": {"m/frame", "m/new"}, "m/frame": set(), "m/new": set()}
    parts = [{"placements": [_pl("base", "m"), _pl("beam", "m/frame", "base"), _pl("new_a", "m/new"),
                             _pl("new_b", "m/new", "new_a")]}]
    paths = derive.node_paths(parts, titles, children)
    tops = {paths[n][0][0] for n in ("base", "beam", "new_a", "new_b")}
    assert len(tops) == 1, f"the machine is split: {tops}"
    assert [t for _, t in paths["new_b"]] == ["Machine", "New node"]
