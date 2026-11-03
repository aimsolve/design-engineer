"""The DXF dialect a shop's laser software (CypCut and the like) reads, pinned by a reference file of the kind it saves itself:
AutoCAD 2000 (AC1015), millimetres ($INSUNITS 4, metric), ASCII, the outline as ONE closed LWPOLYLINE with arcs as bulges on
layer 0 (plus the Defpoints layer AutoCAD-style writers add), holes as CIRCLE. The reference is written here with ezdxf (no
customer file in the repo). laser_dxf.check must accept it, refuse it with anything added that such software chokes on, and
laser_dxf.convert must turn a layered drawing-office DXF into the same dialect."""
import sys
from pathlib import Path

import pytest

ezdxf = pytest.importorskip("ezdxf")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import laser_dxf  # noqa: E402

# a 64 x 40 plate with two 2 mm deep notches and rounded top corners (R 4, as bulges), one Ø6 hole
BULGE_90 = 0.41421356            # tan(90° / 4)
OUTLINE = [(0, 0, 0), (12, 0, 0), (12, 2, 0), (52, 2, 0), (52, 0, 0), (64, 0, 0), (64, 36, BULGE_90), (60, 40, 0),
           (4, 40, BULGE_90), (0, 36, 0)]
HOLE = ((32.0, 20.0), 3.0)


def reference(path, extra=None):
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 4
    doc.header["$MEASUREMENT"] = 1                         # ezdxf, like AutoCAD-style writers, adds the Defpoints layer itself
    msp = doc.modelspace()
    msp.add_lwpolyline(OUTLINE, format="xyb", close=True, dxfattribs={"layer": "0"})
    msp.add_circle(*HOLE, dxfattribs={"layer": "0"})
    if extra:
        extra(doc, msp)
    doc.saveas(path)
    return path


def facts(path):
    doc = ezdxf.readfile(str(path))
    msp = doc.modelspace()
    return {"version": doc.dxfversion, "units": doc.header.get("$INSUNITS"), "metric": doc.header.get("$MEASUREMENT"),
            "kinds": sorted((e.dxftype(), e.dxf.layer) for e in msp),
            "closed": [e.closed for e in msp.query("LWPOLYLINE")],
            "ascii": Path(path).read_bytes().isascii()}


def test_reference_dialect_passes_the_check(tmp_path):
    f = reference(tmp_path / "ref.dxf")
    assert laser_dxf.check(str(f), "en") == []
    got = facts(f)
    assert got["version"] == "AC1015" and got["units"] == 4 and got["metric"] == 1 and got["ascii"], got


@pytest.mark.parametrize("what, add", [
    ("text", lambda doc, msp: msp.add_text("PART 1", dxfattribs={"layer": "0", "height": 3})),
    ("other layer", lambda doc, msp: msp.add_circle((10, 10), 1, dxfattribs={"layer": "HOLES"})),
    ("loose line", lambda doc, msp: msp.add_line((0, 0), (64, 40), dxfattribs={"layer": "0"})),
    ("open contour", lambda doc, msp: msp.add_lwpolyline([(70, 0), (80, 0), (80, 10)], dxfattribs={"layer": "0"})),
])
def test_anything_added_to_the_dialect_fails(tmp_path, what, add):
    assert laser_dxf.check(str(reference(tmp_path / "x.dxf", add)), "en"), what


def test_newer_dxf_version_fails(tmp_path):
    doc = ezdxf.new("R2010")
    doc.modelspace().add_lwpolyline(OUTLINE, format="xyb", close=True)
    doc.saveas(tmp_path / "new.dxf")
    assert any("AC1015" in e for e in laser_dxf.check(str(tmp_path / "new.dxf"), "en"))


def test_convert_writes_the_reference_dialect(tmp_path):
    """A drawing-office DXF (R2010, OUTLINE / HOLES / BEND layers, loose lines and arcs, a text) -> the reference's shape."""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    pts = [(x, y) for x, y, _ in OUTLINE]
    for k, (a, b) in enumerate(zip(pts, pts[1:] + pts[:1])):
        if OUTLINE[k][2]:                                        # the rounded corners as ARC entities
            c, start = ((60, 36), 0) if a[0] > 32 else ((4, 36), 90)
            msp.add_arc(c, 4, start, start + 90, dxfattribs={"layer": "OUTLINE"})
        else:
            msp.add_line(a, b, dxfattribs={"layer": "OUTLINE"})
    msp.add_circle(*HOLE, dxfattribs={"layer": "HOLES"})
    msp.add_line((12, 0), (12, 40), dxfattribs={"layer": "BEND"})
    msp.add_text("B1", dxfattribs={"layer": "BEND", "height": 3})
    doc.saveas(tmp_path / "office.dxf")
    assert laser_dxf.convert(str(tmp_path / "office.dxf"), str(tmp_path / "laser.dxf")) == 0
    got, ref = facts(tmp_path / "laser.dxf"), facts(reference(tmp_path / "ref.dxf"))
    assert laser_dxf.check(str(tmp_path / "laser.dxf"), "en") == []
    assert (got["version"], got["units"], got["metric"], got["kinds"], got["closed"]) == \
        (ref["version"], ref["units"], ref["metric"], ref["kinds"], ref["closed"]), (got, ref)
