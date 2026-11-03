"""Issue #16, done-when 4: check.py on a code-model project assembled by joints.

  K1  a fully jointed project (only the root placed with a Location) passes check.py
  K2  one extra part placed with a Location and no joint (not the root) -> check.py FAILs, the FAIL line names the placement
  K3  a part placed without any joint and without a Location next to the root -> FAIL naming it
  K4  a docked part that is placed with an explicit Location instead of its joint (rail) -> FAIL naming it
Projects are scaffolded (so everything else check.py looks at passes) and carry jointfiles.py's model instead of the example node.
"""
import helpers as H
import partfiles as F
import jointfiles as J

FRAME = J.FILES["frame/__init__.py"]


def _check(root):
    F.run_model(root)              # the build may itself refuse a loose placement; check.py must FAIL it either way
    return F.run_check(root)


def _out(cp):
    return (cp.stdout + cp.stderr)[-2500:]


def test_k1_fully_jointed_project_passes_check(tmp_path):
    root = J.scaffolded(tmp_path / "p")
    F.run_model(root).ok()
    cp = F.run_check(root)
    assert cp.returncode == 0, f"a fully jointed project must pass; FAIL lines: {F.fail_lines(cp)}\n{_out(cp)}"


def _fails_naming(root, name):
    cp = _check(root)
    fl = F.fail_lines(cp)
    assert cp.returncode == 1 and fl, f"check.py must FAIL (exit 1), got {cp.returncode}\n{_out(cp)}"
    assert any(name in ln and "joint" in ln.lower() for ln in fl), f"no FAIL line names placement {name!r} and the joint: {fl}"


def test_k2_extra_part_placed_by_location_without_joint_fails_check(tmp_path):
    frame = FRAME.replace("from . import base, rail, screw", "from . import base, rail, screw, bracket") \
                 .replace("    return out", "    out.append(place(bracket.build(), Pos(50, 0, 0)))\n            return out")
    root = J.scaffolded(tmp_path / "p", files={"frame/__init__.py": frame, "frame/bracket.py": J.BRACKET})
    _fails_naming(root, "bracket")


def test_k3_part_placed_without_location_and_without_joint_fails_check(tmp_path):
    frame = FRAME.replace("from . import base, rail, screw", "from . import base, rail, screw, bracket") \
                 .replace("    return out", "    out.append(place(bracket.build()))\n            return out")
    root = J.scaffolded(tmp_path / "p", files={"frame/__init__.py": frame, "frame/bracket.py": J.BRACKET})
    _fails_naming(root, "bracket")


def test_k4_docked_part_placed_by_location_instead_of_its_joint_fails_check(tmp_path):
    frame = FRAME.replace('            b["shape"].joints["rail"].connect_to(r["shape"].joints["bottom"])\n', "") \
                 .replace("place(r)]", "place(r, Pos(100, 50, 10))]")
    assert frame != FRAME and "connect_to(r[" not in frame
    root = J.scaffolded(tmp_path / "p", files={"frame/__init__.py": frame})
    _fails_naming(root, "rail")


def test_k5_jointed_project_with_a_placed_bought_row_passes_check(tmp_path):
    """A bought row without a shape (pipe by the metre) returned via place(row) has a placement but no STEP and no joint:
    check.py's joints gate must ignore it."""
    frame = FRAME.replace("from geom import place", "from geom import P, place") \
                 .replace("    return out", '    out.append(place(P("pipe", None, "#999999", process="bought", code="TR50", '
                                          'desc="Truba 50", qty=3, unit="m", kind="profile")))\n            return out')
    root = J.scaffolded(tmp_path / "p", files={"frame/__init__.py": frame})
    F.run_model(root).ok()
    assert "pipe" in H.by_id(root), f"precondition: the row is in the model: {sorted(H.by_id(root))}"
    cp = F.run_check(root)
    assert cp.returncode == 0, f"a placed bought row must not trip the joints gate; FAIL lines: {F.fail_lines(cp)}\n{_out(cp)}"


def test_k6_stray_jointless_part_listed_before_the_root_is_the_one_named(tmp_path):
    """The root is the jointless placement the others are docked to (base), not the first one in the list: a stray bracket
    returned BEFORE base is what check.py names."""
    frame = FRAME.replace("from . import base, rail, screw", "from . import base, rail, screw, bracket") \
                 .replace("out = [place(b, Pos(0, 0, 0)), place(r)]",
                          "out = [place(bracket.build(), Pos(50, 0, 0)), place(b, Pos(0, 0, 0)), place(r)]")
    assert "bracket.build()" in frame
    root = J.scaffolded(tmp_path / "p", files={"frame/__init__.py": frame, "frame/bracket.py": J.BRACKET})
    _fails_naming(root, "bracket")
