"""Issue #15, second review: probes the reviewer wrote (partial runs with rows, row variants of a sub-node, a part file as a
root, row-only part files placed N times). Kept as regression tests."""
import json
import os
from pathlib import Path
import helpers as H
import partfiles as F

def qp(root):
    return {p["id"]: (p.get("qty"), len(p.get("placements") or [])) for p in H.read_parts(root)}

PEG = '''
    from build123d import Box
    from geom import P

    def build():
        return P("peg", Box(4, 4, 4), "#22aa22", process="print", material="PLA")
    '''
KIT = '''
    from build123d import Pos
    from geom import P, place
    from . import peg

    def build(n=2):
        return [place(peg.build(), Pos(10 * i, 0, 0)) for i in range(n)] + [
            P("bolt", None, "#999", process="bought", code="B1", qty=2, unit="pc", kind="fasteners")]
    '''
ASM = """import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from parts import frame, kit
items = frame.build() + kit.build()
"""
CHECK = """import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from parts import kit
kit.build(n=5)
"""

def test_p1_check_calls_root_with_args(tmp_path):
    only = {k: None for k in F.FILES if not k.startswith("frame")}
    only.update({"kit/__init__.py": KIT, "kit/peg.py": PEG})
    root = F.write_project(tmp_path / "p", files=only, assemble=ASM, legacy=False)
    (H.cad_dir(root) / "check.py").write_text(CHECK)
    F.run_model(root).ok()
    before = qp(root)
    r = H.run_model(root, script="check.py")
    assert r.rc == 0, r.log[-2000:]
    after = qp(root)
    assert after == before, (before, after)

LEG = '''
    from build123d import Pos
    from geom import P, place
    from . import peg

    def build(h=1):
        return [place(peg.build(), Pos(0, 0, h))] + [P("pad", None, "#999", process="bought", code="PAD", unit="pc", kind="other")]
    '''
LEGS = '''
    from build123d import Pos
    from geom import place
    from . import leg

    def build():
        return place(leg.build(h=1), Pos(0, 0, 0)) + place(leg.build(h=2), Pos(100, 0, 0))
    '''
ASM2 = """import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from parts import legs
items = legs.build()
"""

def test_p2_two_variants_of_subnode_same_row(tmp_path):
    only = {k: None for k in F.FILES}
    only.update({"legs/__init__.py": LEGS, "legs/leg/__init__.py": LEG, "legs/leg/peg.py": PEG})
    root = F.write_project(tmp_path / "p", files=only, assemble=ASM2, legacy=False)
    m = F.run_model(root)
    print(m.log[-1500:])
    m.ok()
    print(qp(root))
    assert qp(root)["pad"][0] == 2

ROWPART = '''
    from geom import P

    def build():
        return P("nut", None, "#999", process="bought", code="N1", qty=3, unit="pc", kind="fasteners")
    '''
NUTS = '''
    from build123d import Pos
    from geom import place
    from . import nut, peg

    def build():
        return [place(nut.build(), Pos(0, 0, 0)), place(nut.build(), Pos(10, 0, 0)), place(peg.build(), Pos(0,0,0))]
    '''
ASM3 = ASM2.replace("legs", "nuts")

def test_p4_row_part_file_placed_twice(tmp_path):
    only = {k: None for k in F.FILES}
    only.update({"nuts/__init__.py": NUTS, "nuts/nut.py": ROWPART, "nuts/peg.py": PEG})
    root = F.write_project(tmp_path / "p", files=only, assemble=ASM3, legacy=False)
    F.run_model(root).ok()
    print(qp(root))
    assert qp(root)["nut"][0] == 6
    d = H.run_derive(root, "bought").ok()
    rows = {r["code"]: r for r in d.manifest["bought"]}
    assert rows["N1"]["qty"] == 6, rows
    F.run_model(root).ok()
    assert qp(root)["nut"][0] == 6

ASM6 = """import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from parts.kit import peg
items = [peg.build()]
"""

def test_p6_assemble_calls_part_file_directly(tmp_path):
    only = {k: None for k in F.FILES}
    only.update({"kit/__init__.py": KIT, "kit/peg.py": PEG})
    root = F.write_project(tmp_path / "p", files=only, assemble=ASM6, legacy=False)
    m = F.run_model(root); print(m.log[-1500:]); m.ok()
    print(qp(root))
    assert "peg" in qp(root)

CARR = F.FILES["tower/carriage/__init__.py"].replace(
    "Pos(5, 0, 30))]", 'Pos(5, 0, 30)), P("glue", None, "#999", process="bought", code="GL1", qty=params.GLUE, unit="pc", kind="other")]'
).replace("from geom import place", "import params\n        from geom import P, place")
TWICE = '''
    from build123d import Pos
    from geom import place
    from . import carriage, knob

    def build():
        return place(carriage.build(), Pos(0, 0, 50)) + place(carriage.build(), Pos(0, 200, 50))
    '''

def test_p8_row_qty_param_change_refreshes(tmp_path):
    root = F.write_project(tmp_path / "p", files={"tower/__init__.py": TWICE, "tower/carriage/__init__.py": CARR},
                           params=F.PARAMS + "GLUE = 2\n")
    F.run_model(root).ok()
    assert qp(root)["glue"][0] == 4
    p = H.cad_dir(root) / "params.py"
    p.write_text(p.read_text().replace("GLUE = 2", "GLUE = 5"))
    m = F.run_model(root); m.ok(); print(m.log[-800:])
    assert qp(root)["glue"][0] == 10
    m = F.run_model(root); m.ok()
    assert qp(root)["glue"][0] == 10
    assert "nodes rebuilt 0" in m.log
