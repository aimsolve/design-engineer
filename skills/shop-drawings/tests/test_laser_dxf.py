"""Done-when 4: the produced DXF is what a shop's laser software (CypCut and the like) reads cleanly: AC1015, pure ASCII, layer 0,
closed LWPOLYLINE contours + CIRCLE holes only, nothing else; extents = the flat blank, min corner at 0,0.
The dialect itself (test_laser_dxf_dialect.py) is pinned by a reference file written with ezdxf the way such software saves one."""
import pytest

from test_cabinet_drawing import BLANK

pytestmark = pytest.mark.slow


@pytest.fixture(params=["cabinet", "small", "weld_box"])
def kit(request):
    return request.getfixturevalue(request.param)


def test_version_ac1015(kit):
    assert kit.dxf["version"] == "AC1015"


def test_pure_ascii(kit):
    assert not kit.dxf["binary"]
    assert kit.dxf["ascii"], "no non-ASCII bytes anywhere in the file"


def test_everything_on_layer_0(kit):
    layers = {e["layer"] for e in kit.dxf["entities"]}
    assert layers == {"0"}, f"got layers {layers}"


def test_only_closed_polylines_and_circles(kit):
    types = {e["type"] for e in kit.dxf["entities"]}
    assert types <= {"LWPOLYLINE", "CIRCLE"}, f"got {types} (no TEXT / MTEXT / LINE / bend lines)"
    assert all(e["closed"] for e in kit.dxf["entities"] if e["type"] == "LWPOLYLINE"), "every contour closed"


def test_no_dashed_linetypes(kit):
    for e in kit.dxf["entities"]:
        assert e["linetype"].upper() in ("BYLAYER", "CONTINUOUS"), e
    assert kit.dxf["layer_linetypes"].get("0", "Continuous").upper() == "CONTINUOUS"


def test_min_corner_at_origin(kit):
    x0, y0 = kit.dxf["extmin"]
    assert abs(x0) <= 0.01 and abs(y0) <= 0.01, f"min corner {x0:.3f},{y0:.3f}"


def test_cabinet_contours_and_extents(cabinet):
    ents = cabinet.dxf["entities"]
    polys = [e for e in ents if e["type"] == "LWPOLYLINE"]
    circles = [e for e in ents if e["type"] == "CIRCLE"]
    assert len(polys) == 1, f"one outer contour (the U wall has no non-round cut-outs), got {len(polys)}"
    assert len(circles) == 24, f"24 round holes, got {len(circles)}"
    (x0, y0), (x1, y1) = cabinet.dxf["extmin"], cabinet.dxf["extmax"]
    w, h = sorted((x1 - x0, y1 - y0), reverse=True)
    assert abs(w - BLANK[0]) <= 0.5 and abs(h - BLANK[1]) <= 0.5, f"blank {w:.1f} x {h:.1f}, want {BLANK}"
