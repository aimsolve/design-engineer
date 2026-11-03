"""Rollout items 6, 10, 11: a flat strip is not a bent part; a sheet part far from the origin unfolds like one at the origin;
a bend of any angle (12 deg lip) unfolds, is labelled with its real angle and its blank follows the bend allowance."""
import math
import re

import pytest

from conftest import _generate, check, probe, ru

pytestmark = pytest.mark.slow

LIP = dict(material="S235JR steel", t=2, R=2)
WAY = f"(?:{ru('dir.up')}|{ru('dir.down')})"
TURN = ru("bend.turn", angle=0).split()[0]                  # the word of the bend label


def label(angle):
    """The bend label of an `angle` degree bend: direction, the bend angle, the angle left between the flanges."""
    return rf"{WAY}, {re.escape(ru('bend.turn', angle=angle))} \({re.escape(ru('bend.between', between=180 - angle))}\)"


def _gen(tmp_path_factory, synth_steps, kind, name, **kw):
    a = ["--step", synth_steps / f"{kind}.step", "--material", kw.get("material", LIP["material"]), "--t", kw.get("t", 2), "--name", name]
    if kw.get("R"):
        a += ["--R", kw["R"]]
    return _generate(tmp_path_factory.mktemp(kind) / "kit", *a)


@pytest.fixture(scope="session")
def shapes(tmp_path_factory):
    d = tmp_path_factory.mktemp("angle")
    for kind in ("flat_strip", "lip12", "lip12_far", "lip12_hole", "lip12_hole_far", "lip120"):
        probe("make", kind, d / f"{kind}.step")
    return d


@pytest.fixture(scope="session")
def strip(shapes, tmp_path_factory):
    return _gen(tmp_path_factory, shapes, "flat_strip", "Strip", t=3)


@pytest.fixture(scope="session")
def lip(shapes, tmp_path_factory):
    return _gen(tmp_path_factory, shapes, "lip12", "Lip", R=2)


@pytest.fixture(scope="session")
def lip_hole(shapes, tmp_path_factory):
    return _gen(tmp_path_factory, shapes, "lip12_hole", "Lip with hole", R=2)


@pytest.fixture(scope="session")
def lip_hole_far(shapes, tmp_path_factory):
    return _gen(tmp_path_factory, shapes, "lip12_hole_far", "Lip with hole far", R=2)


@pytest.fixture(scope="session")
def lip120(shapes, tmp_path_factory):
    return _gen(tmp_path_factory, shapes, "lip120", "Flange 120", R=2)


@pytest.fixture(scope="session")
def lip_far(shapes, tmp_path_factory):
    return _gen(tmp_path_factory, shapes, "lip12_far", "Lip far", R=2)


def test_flat_strip_is_not_bent(strip):
    rc, out = check(strip.out)
    assert rc == 0, out[-1500:]
    assert ru("flat.bent") not in strip.p1 and TURN not in strip.p1


def test_lip_label_shows_the_real_angle(lip):
    assert re.search(label(12) + r"\s*R\s*2", lip.p1), lip.p1[:800]
    assert ru("bend.turn", angle=90) not in lip.p1


def test_lip_over_90_says_bend_and_included_angle(lip120):
    """A 120 deg lip is a flange turned 120 deg that leaves 60 deg between the legs: both printed, never a bare «UP 120°»."""
    assert re.search(label(120) + r"\s*R\s*2", lip120.p1), lip120.p1[:800]
    assert not re.search(WAY + r"\s*120", lip120.p1)
    rc, out = check(lip120.out)
    assert rc == 0, out[-1500:]


def test_lip_over_90_flat_length_follows_the_bend_allowance(lip120):
    a, R, t, K = math.radians(120), 2.0, 2.0, 0.33
    expected = 90 - 2 * (R + t) * math.tan(a / 2) + a * (R + K * t)          # outer legs 60 + 30, setback (R + t) tan(a/2), BA = a (R + K t)
    ext = lip120.dxf
    L = max(ext["extmax"][0] - ext["extmin"][0], ext["extmax"][1] - ext["extmin"][1])
    assert abs(L - expected) < 0.05, (L, expected)


def test_lip_passes_the_checker_as_a_bent_part(lip):
    rc, out = check(lip.out)
    assert rc == 0, out[-1500:]


def test_lip_blank_follows_the_bend_allowance(lip):
    a, R, t, K = math.radians(12), 2.0, 2.0, 0.33
    expected = 90 - 2 * (R + t) * math.tan(a / 2) + a * (R + K * t)          # outer legs 60 + 30, setback (R + t) tan(a/2), BA = a (R + K t)
    ext = lip.dxf
    L = max(ext["extmax"][0] - ext["extmin"][0], ext["extmax"][1] - ext["extmin"][1])
    assert abs(L - expected) < 0.05, (L, expected)


def test_part_far_from_the_origin_gives_the_same_drawing(lip_hole, lip_hole_far):
    """Same part 2069 mm away: same blank, same hole position, same entities (compared in the blank's own frame), same label."""
    near, far = lip_hole.dxf, lip_hole_far.dxf
    assert far["extmax"] == pytest.approx(near["extmax"], abs=0.05)
    assert far["extmin"] == pytest.approx(near["extmin"], abs=0.05)
    assert len(near["circles"]) == 1 and len(far["circles"]) == 1, (near["circles"], far["circles"])
    assert far["circles"][0] == pytest.approx(near["circles"][0], abs=0.05)
    assert sorted((e["type"], e["layer"], e.get("n")) for e in far["entities"]) == sorted((e["type"], e["layer"], e.get("n")) for e in near["entities"])
    assert re.search(label(12), lip_hole_far.p1)


def test_bent_part_is_found_even_when_the_pdf_lost_its_flat_dims(lip, tmp_path):
    """Bent-ness comes from the generator's own bend count (spisok.csv), not from text that may be the very thing missing."""
    import shutil
    kit = tmp_path / "kit"
    shutil.copytree(lip.out, kit)
    pdf = next(kit.rglob("*.pdf"))
    r = probe("strip_flat_dims", pdf, tmp_path / "nodims.pdf")
    assert r["removed"] > 0 and r["left"] == 0, r
    shutil.copy(tmp_path / "nodims.pdf", pdf)
    rc, out = check(kit)
    assert rc == 1 and "«**»" in out, out[-1500:]


def test_flat_strip_without_spisok_still_passes(strip, tmp_path):
    import shutil
    kit = tmp_path / "kit"
    shutil.copytree(strip.out, kit)
    (kit / "spisok.csv").unlink()
    rc, out = check(kit)
    assert rc == 0, out[-1500:]


def test_one_failing_part_does_not_abort_the_batch(shapes, tmp_path):
    """A 30 mm cube is not sheet metal: it fails by name, loudly, leaves no half-made files, and the other part still gets its set."""
    import json
    from conftest import run_env
    probe("make", "cube", shapes / "cube.step")
    pj = tmp_path / "parts.json"
    pj.write_text(json.dumps([
        {"name": "Cube", "step": str(shapes / "cube.step"), "material": "S235JR steel", "t": 2},
        {"name": "Strip", "step": str(shapes / "flat_strip.step"), "material": "S235JR steel", "t": 3},
    ], ensure_ascii=False), encoding="utf-8")
    r = run_env("shop_drawing.py", "--parts", pj, "--out", tmp_path / "kit", "--no-zip")
    out = r.stdout + r.stderr
    assert r.returncode == 1 and re.search(r"(?m)^FAIL Cube", out) and "SHOP: 1 ok, 0 warn, 1 fail" in out, out[-1500:]
    names = {f.name for f in (tmp_path / "kit").rglob("*") if f.is_file()}
    assert {"Strip.dxf", "Strip.pdf", "Strip.step"} <= names
    assert not [n for n in names if n.startswith("Cube.")], names


def test_tilted_plate_far_from_the_origin(tmp_path_factory):
    d = tmp_path_factory.mktemp("tilted")
    probe("make", "far_tilted_plate", d / "far_tilted_plate.step")
    k = _gen(tmp_path_factory, d, "far_tilted_plate", "Plate", t=5)
    assert abs(max(k.dxf["extmax"][i] - k.dxf["extmin"][i] for i in (0, 1)) - 167) < 0.2
    rc, out = check(k.out)
    assert rc == 0, out[-1500:]


# ---------------------------------------------------------------- refold check on tapered legs
@pytest.fixture(scope="session")
def taper_step(tmp_path_factory):
    d = tmp_path_factory.mktemp("taper")
    probe("make", "taper_lip", d / "taper_lip.step")
    return d / "taper_lip.step"


def _refold_fails(m):
    """the generator's refold verdict (shop_drawing.flat_pattern): volume off > 2 % or < 98 % of the refolded material inside the model"""
    return m["v_err"] > 0.02 or m["inside"] < 0.98


def test_tapered_legs_refold_inside_the_model(taper_step):
    """Legs whose side edges are not parallel (a funnel lip): the correct flat refolds onto the model."""
    m = probe("refold", taper_step, 1.5, 1.5, 0.33)
    assert m["legs"] == 3 and m["bends"] == 2
    assert not _refold_fails(m), m
    assert m["inside"] > 0.999 and m["v_err"] < 0.005, m


@pytest.mark.parametrize("mode", ["missing", "mirrored", "shifted"])
def test_wrong_tapered_flat_still_fails_the_refold(taper_step, mode):
    """A flange missing from the blank, turned end for end, or moved 5 mm (a wrong bend allowance) is caught."""
    assert _refold_fails(probe("refold", taper_step, 1.5, 1.5, 0.33, mode)), mode


def test_tapered_lip_part_passes_the_checker(taper_step, tmp_path_factory):
    kit = _generate(tmp_path_factory.mktemp("taperkit") / "kit", "--step", taper_step, "--material", LIP["material"], "--t", 1.5, "--R", 1.5,
                    "--name", "Funnel")
    rc, out = check(kit.out)
    assert rc == 0, out[-1500:]
    assert re.search(WAY + ", " + re.escape(TURN), kit.p1), kit.p1[:800]
