"""Done-when 5: check_drawings.py passes the generator's own output and FAILs (exit 1) a kit with: the old DXF format
(OUTLINE/HOLES/BEND layers + TEXT, R2010), an open contour, a bent-part PDF without the "**" flat dims, or a bent-part PDF
without the hole-table page. Each broken kit is a copy of a passing one with exactly one thing broken."""
import re
import shutil

import pytest

from conftest import check, probe, ru

pytestmark = pytest.mark.slow


@pytest.mark.parametrize("which", ["cabinet", "small", "weld_box", "plain_box", "disc", "perf", "up_flange"])
def test_generator_output_passes(request, which):
    kit = request.getfixturevalue(which)
    rc, out = check(kit.out)
    assert rc == 0, out[-2000:]
    m = re.search(r"CHECK: (\d+) ok, 0 fail", out)
    assert m and int(m.group(1)) >= 1, out[-2000:]


def test_generator_says_ok_for_round_and_perforated(disc, perf):
    for k in (disc, perf):
        assert re.search(r"SHOP: [1-9]\d* ok, 0 warn, 0 fail|SHOP: [1-9]\d* ok, \d+ warn, 0 fail", k.run.stdout + k.run.stderr), \
            (k.run.stdout + k.run.stderr)[-1500:]
        assert re.search(r"(?m)^ok\s", k.run.stdout + k.run.stderr), "the part line says ok"


def test_round_blank_page1_draws_the_outer_circle(disc):
    assert re.search(r"(?<![\d,.])80(?![\d,.])", disc.p1), f"overall Ø80 dimensioned on page 1: {disc.p1[:600]}"


def test_perforation_summary_on_sheet_2(perf):
    assert len(perf.dxf["circles"]) == 36
    assert ru("holes.perforation", n=36) in perf.p2, f"sheet 2 summary names it a perforation: {perf.p2[-600:]}"


def test_up_bend_along_x_labelled_up(up_flange):
    label = f"{ru('dir.up')}, {ru('bend.turn', angle=90)} ({ru('bend.between', between=90)})"
    assert re.search(re.escape(label) + r"\s*R", up_flange.p1), up_flange.p1[:600]


@pytest.fixture
def broken(cabinet, tmp_path):
    """A fresh copy of the passing cabinet kit; returns (kit dir, pdf path, dxf path) inside the copy."""
    rc, out = check(cabinet.out)
    assert rc == 0, f"precondition: the untouched kit passes\n{out[-2000:]}"
    dst = tmp_path / "kit"
    shutil.copytree(cabinet.out, dst)
    return dst, dst / cabinet.pdf_path.relative_to(cabinet.out), dst / cabinet.dxf_path.relative_to(cabinet.out)


def fails(kit):
    rc, out = check(kit)
    assert rc == 1, f"checker must FAIL (exit 1), got exit {rc}:\n{out[-2000:]}"
    assert re.search(r"[1-9]\d* fail", out), out[-2000:]


def test_old_dxf_format_fails(broken, tmp_path):
    kit, pdf, dxf = broken
    probe("old_format", dxf, tmp_path / "old.dxf")
    shutil.copy(tmp_path / "old.dxf", dxf)
    fails(kit)


def test_open_contour_fails(broken, tmp_path):
    kit, pdf, dxf = broken
    probe("open_contour", dxf, tmp_path / "open.dxf")
    shutil.copy(tmp_path / "open.dxf", dxf)
    fails(kit)


def test_pdf_without_flat_dims_fails(broken, tmp_path):
    kit, pdf, dxf = broken
    r = probe("strip_flat_dims", pdf, tmp_path / "nodims.pdf")
    assert r["removed"] > 0 and r["left"] == 0, f"setup: page 1 '**' dims removed: {r}"
    shutil.copy(tmp_path / "nodims.pdf", pdf)
    fails(kit)


def test_pdf_without_hole_table_page_fails(broken, tmp_path):
    kit, pdf, dxf = broken
    r = probe("drop_page", pdf, tmp_path / "nohole.pdf", 1)
    assert r["pages"] == 1
    shutil.copy(tmp_path / "nohole.pdf", pdf)
    fails(kit)
