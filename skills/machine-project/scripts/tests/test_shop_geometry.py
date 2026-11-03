"""Rollout items 10-11: sheet parts far from the origin unfold; non-90 degree bends unfold; one failing sheet part does not abort the kit."""
import math
from pathlib import Path

import helpers as H
from test_derive import needs_cadgen
from test_derive_rollout3 import _model
import text_i18n as TX


def _strip(points, t, width):
    """A sharp-corner sheet strip: centre polyline in XZ (mitred offset +-t/2), extruded `width` along Y (centred)."""
    def off(d):
        lines = []
        for (x0, z0), (x1, z1) in zip(points[:-1], points[1:]):
            L = math.hypot(x1 - x0, z1 - z0)
            ux, uz = (x1 - x0) / L, (z1 - z0) / L
            lines.append(((x0 - uz * d, z0 + ux * d), (ux, uz)))
        out = [lines[0][0]]
        for (p1, u1), (p2, u2) in zip(lines[:-1], lines[1:]):
            den = u1[0] * u2[1] - u1[1] * u2[0]
            s = ((p2[0] - p1[0]) * u2[1] - (p2[1] - p1[1]) * u2[0]) / den
            out.append((p1[0] + u1[0] * s, p1[1] + u1[1] * s))
        (p, u) = lines[-1]
        out.append((p[0] + u[0] * math.hypot(points[-1][0] - points[-2][0], points[-1][1] - points[-2][1]),
                    p[1] + u[1] * math.hypot(points[-1][0] - points[-2][0], points[-1][1] - points[-2][1])))
        return out
    from build123d import Face, Pos, Vector, Wire, extrude
    poly = off(t / 2) + off(-t / 2)[::-1]
    face = Face(Wire.make_polygon([Vector(x, -width / 2, z) for x, z in poly], close=True))
    return extrude(face, amount=width, dir=(0, 1, 0))


def _bent(angle_deg, at=0.0):
    a = math.radians(angle_deg)
    from build123d import Pos
    return Pos(at, 0, 0) * _strip([(0, 0), (60, 0), (60 + 30 * math.cos(a), 30 * math.sin(a))], 2.0, 20.0)


F = {"material": "S235JR", "thickness": 2.0}


def _pdf_text(d, root, pid):
    import fitz  # noqa: F401  (only in the shop venv: read through it)


@needs_cadgen
def test_part_far_from_the_origin_unfolds_like_one_at_the_origin(tmp_path):
    root = _model(tmp_path / "p", [("near", "bent", _bent(90), dict(F, code="N1")), ("far", "bent", _bent(90, at=-2069), dict(F, code="N2"))])
    d = H.run_derive(root, "laser").ok()
    assert "error" not in H.entry(d, "far"), d.manifest
    assert len(H.entry(d, "far")["files"]) == 3


@needs_cadgen
def test_far_flat_plate_works(tmp_path):
    from build123d import Box, Pos
    root = _model(tmp_path / "p", [("tank_wall", "laser", Pos(-2069, 500, 300) * Box(1200, 2, 800), dict(F, code="W1"))])
    d = H.run_derive(root, "laser").ok()
    assert "error" not in H.entry(d, "tank_wall"), d.manifest


@needs_cadgen
def test_twelve_degree_bend_unfolds(tmp_path):
    root = _model(tmp_path / "p", [("lip", "bent", _bent(12), dict(F, code="LIP"))])
    d = H.run_derive(root, "laser").ok()
    e = H.entry(d, "lip")
    assert "error" not in e, e
    assert {Path(f).suffix for f in e["files"]} == {".dxf", ".pdf", ".step"}
    pdf = [f for f in H.file_list(e, root) if f.suffix == ".pdf"][0]
    import subprocess
    import procgroup
    txt = procgroup.run([str(H.SHOP_VENV), "-c", "import sys,pymupdf;print('\\n'.join(p.get_text() for p in pymupdf.open(sys.argv[1])))", str(pdf)],
                         capture_output=True, text=True).stdout
    assert "12°" in txt, txt[:500]


@needs_cadgen
def test_blank_length_of_a_non_90_bend_follows_the_bend_allowance(tmp_path):
    """flat = 60 + 30 along the centre line minus the sharp-corner setbacks plus BA = angle * (R + K t); R defaults to t, K to 0.33 (steel)."""
    root = _model(tmp_path / "p", [("lip", "bent", _bent(12), dict(F, code="LIP"))])
    d = H.run_derive(root, "laser").ok()
    dxf = [f for f in H.file_list(H.entry(d, "lip"), root) if f.suffix == ".dxf"][0]
    import subprocess
    import procgroup
    out = procgroup.run([str(H.SHOP_VENV), "-c", "import sys,ezdxf;from ezdxf import bbox;m=ezdxf.readfile(sys.argv[1]).modelspace();e=bbox.extents(m);print(e.size.x,e.size.y)",
                          str(dxf)], capture_output=True, text=True).stdout.split()
    a, R, t, K = math.radians(12), 2.0, 2.0, 0.33
    setback = (R + t / 2) * math.tan(a / 2)                           # centre line: mean radius R + t/2
    expected = 90 - 2 * setback + a * (R + K * t)
    assert abs(max(map(float, out)) - expected) < 0.35 or abs(float(out[0]) - expected) < 0.35, (out, expected)


@needs_cadgen
def test_one_failing_part_does_not_abort_the_kit(tmp_path):
    from build123d import Box, Pos
    root = _model(tmp_path / "p", [("ok_plate", "laser", Pos(0, 0, 0) * Box(30, 20, 2), dict(F, code="OK")),
                                   ("block", "laser", Pos(100, 0, 0) * Box(30, 30, 30), dict(F, code="BAD"))])
    d = H.run_derive(root, "laser")
    assert d.rc == 0, d.log[-1200:]
    d.ok()
    assert len(H.entry(d, "ok_plate")["files"]) == 3
    bad = H.entry(d, "block")
    assert bad.get("error")
    assert "block" in d.log and "FAILED" in d.log
    # the failed part is not silent: one plain-text note in its files (so it reaches the zip and the list), the error stays
    files = H.file_list(bad, root)
    assert [f.name for f in files] == ["block" + TX.t("error.suffix") + ".txt"], bad
    assert files[0].read_text(encoding="utf-8") == TX.t("error.text", reason=bad["error"]), files[0].read_text(encoding="utf-8")
    assert bad["error"] in files[0].read_text(encoding="utf-8")
    tail = [l for l in d.log.splitlines() if l.strip()][-3:]
    assert any("FAILED" in l and "block" in l for l in tail), tail            # the failures are the last thing the log says


def test_fail_reasons_matches_exact_names_only():
    """«Side» must not take over the reasons of «Side left» (one name a prefix of the other)."""
    import sys
    sys.path.insert(0, str(H.DERIVE.parent))
    import derive
    log = ("OK  Side                             bends 1\n"
           "FAIL Side left                        holes flat 2 vs model 3\n"
           "      refolded flat only 90.0 % inside the model\n"
           "FAIL 3 mm steel/Side left\n"
           "      no bend radius\n")
    assert derive.fail_reasons(log, "Side") == []
    assert derive.fail_reasons(log, "Side left") == ["holes flat 2 vs model 3", "refolded flat only 90.0 % inside the model", "no bend radius"]
