"""laser_dxf.write_prims chains the outline into ONE closed contour also when an edge was flattened into pieces shorter than
the joining tolerance (a lofted bend end in a recreated sheet: 401 sub-0.01 mm pieces each closed on itself as a one-point
contour and left a 0.79 mm gap in the outline, so the part shipped an error note instead of its DXF) - in
whatever order the pieces come."""
import sys
from pathlib import Path

import pytest

ezdxf = pytest.importorskip("ezdxf")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import laser_dxf  # noqa: E402

N, TOP = 400, 0.8                                         # 400 pieces of 0.002 mm up the right side's top end
PIECES = [("LINE", (100.0, 50.0 - TOP + TOP * k / N), (100.0, 50.0 - TOP + TOP * (k + 1) / N)) for k in range(N)]
WIRE = [("LINE", (0.0, 0.0), (100.0, 0.0)), ("LINE", (100.0, 0.0), (100.0, 50.0 - TOP))] + PIECES + \
       [("LINE", (100.0, 50.0), (0.0, 50.0)), ("LINE", (0.0, 50.0), (0.0, 0.0))]


def rev(p):
    return ("LINE", p[2], p[1])


ORDERS = {
    "pieces first": PIECES + [p for p in WIRE if p not in PIECES],
    "wire order": WIRE,
    "starting after the pieces": WIRE[2 + N:] + WIRE[:2 + N],
    "starting inside the pieces": WIRE[2 + N // 2:] + WIRE[:2 + N // 2],
    "pieces reversed": WIRE[:2] + [rev(p) for p in reversed(PIECES)] + WIRE[2 + N:],
    "pieces after the next edge": WIRE[:2] + WIRE[2 + N:3 + N] + PIECES + WIRE[3 + N:],
}


@pytest.mark.parametrize("order", ORDERS)
def test_an_edge_of_sub_tolerance_pieces_still_closes_one_contour(tmp_path, order):
    f = tmp_path / "x.dxf"
    assert laser_dxf.write_prims(ORDERS[order], str(f)) == 0
    pl = list(ezdxf.readfile(str(f)).modelspace().query("LWPOLYLINE"))
    assert len(pl) == 1 and pl[0].closed, [(len(p), p.closed) for p in pl]


def test_two_outlines_and_a_hole_stay_three_contours(tmp_path):
    sq = lambda x0, y0, s: [("LINE", (x0, y0), (x0 + s, y0)), ("LINE", (x0 + s, y0), (x0 + s, y0 + s)),   # noqa: E731
                            ("LINE", (x0 + s, y0 + s), (x0, y0 + s)), ("LINE", (x0, y0 + s), (x0, y0))]
    f = tmp_path / "y.dxf"
    assert laser_dxf.write_prims(sq(0, 0, 50) + sq(10, 10, 5) + sq(100, 0, 20), str(f)) == 0
    pl = list(ezdxf.readfile(str(f)).modelspace().query("LWPOLYLINE"))
    assert len(pl) == 3 and all(p.closed for p in pl)


def test_a_corner_reached_through_a_collapsed_diagonal_run_keeps_its_vertex(tmp_path):
    """The pieces between the right side and the top cut the corner diagonally (100, 49.2) -> (100.5, 50): the corner
    point (100, 49.2) and the run's far end stay in the contour, so the outline is not slanted."""
    n = 100
    run = [("LINE", (100.0 + 0.5 * k / n, 49.2 + 0.8 * k / n), (100.0 + 0.5 * (k + 1) / n, 49.2 + 0.8 * (k + 1) / n)) for k in range(n)]
    prims = [("LINE", (0.0, 0.0), (100.0, 0.0)), ("LINE", (100.0, 0.0), (100.0, 49.2))] + run + \
            [("LINE", (100.5, 50.0), (0.0, 50.0)), ("LINE", (0.0, 50.0), (0.0, 0.0))]
    f = tmp_path / "c.dxf"
    assert laser_dxf.write_prims(prims, str(f)) == 0
    pl, = list(ezdxf.readfile(str(f)).modelspace().query("LWPOLYLINE"))
    pts = [tuple(v[:2]) for v in pl.get_points()]
    assert any(abs(x - 100.0) < 0.01 and abs(y - 49.2) < 0.01 for x, y in pts), pts
    assert any(abs(x - 100.5) < 0.01 and abs(y - 50.0) < 0.01 for x, y in pts), pts


def test_a_small_contour_made_only_of_tiny_pieces_is_reported_not_dropped(tmp_path):
    """A Ø0.8 hole drawn as 300 pieces collapses into one node: the outline still closes, and the lost hole is counted."""
    import math
    hole = [("LINE", (50 + 0.4 * math.cos(2 * math.pi * k / 300), 25 + 0.4 * math.sin(2 * math.pi * k / 300)),
             (50 + 0.4 * math.cos(2 * math.pi * (k + 1) / 300), 25 + 0.4 * math.sin(2 * math.pi * (k + 1) / 300))) for k in range(300)]
    outline = [("LINE", (0.0, 0.0), (100.0, 0.0)), ("LINE", (100.0, 0.0), (100.0, 50.0)),
               ("LINE", (100.0, 50.0), (0.0, 50.0)), ("LINE", (0.0, 50.0), (0.0, 0.0))]
    assert laser_dxf.write_prims(outline + hole, str(tmp_path / "h.dxf")) == 1


def test_a_short_spline_edge_is_cut_into_pieces_no_shorter_than_the_tolerance():
    """shop_drawing._edges cuts a curve by its length (0.05 mm pieces at least), not always into 200: a 0.8 mm lofted bend
    end no longer arrives as pieces below the joining tolerance."""
    bd = pytest.importorskip("build123d")
    import shop_drawing
    e = bd.Edge.make_spline([bd.Vector(0, 0, 0), bd.Vector(0.4, 0.05, 0), bd.Vector(0.8, 0, 0)])
    msp = ezdxf.new("R2010").modelspace()
    shop_drawing._edges(msp, [e], "C")
    pts = [tuple(v[:2]) for pl in msp.query("LWPOLYLINE") for v in pl.get_points()]
    import math
    gaps = [math.dist(a, b) for a, b in zip(pts, pts[1:])]
    assert gaps and min(gaps) >= laser_dxf.TOL, (len(pts), min(gaps) if gaps else None)
