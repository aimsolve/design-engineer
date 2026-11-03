"""The purchase list: `derive.py bought` (the purchase list, manifest key `bought` = list of rows).

Done-when map:
  DW1  A row merged from parts in different nodes has `node` = the distinct non-empty node names, sorted, joined ", ".
  DW2  `buy: "length"` bought parts with the same (code, name) -> ONE row, unit metres (text_i18n "unit.m"),
       qty = max(1, ceil(total_mm * buy_margin / 1000)), buy_margin default 1.2, total_mm = sum over every placement of every such
       part of the part's longest bbox side; name = text_i18n "bought.by_length" of the part name and total_mm. No per-piece rows.
  DW3  `cut: {stock, note, label, total, code?, class?, all_total?}` on any part -> per stock: one row per piece length L (longest
       bbox side rounded to whole mm), then a total row in metres; stocks sharing `all_total` add one grand-total row. A made part
       with `cut` keeps its other scopes; a bought part with `cut` makes only the cut rows.
  DW4  Screws counted from coaxial round holes stacked across >= 2 placed parts, enabled by a row-only bought part carrying
       `screws_from_holes` (templates with {n}, {L}); plus `screws` / `screws_unpaired` extras. One row per (n, L).
  DW5  Deterministic; a second run with no model change reports `computed 0`.

The model (build/model/parts.json + STEPs in each part's OWN coordinates + placements, 4x4 row-major) is hand-written here, like
test_instances.py / test_print_scope.py, so these tests depend only on derive.py.
"""
import json
import math
import shutil
from pathlib import Path

import pytest

import helpers as H
import text_i18n as TX

# the texts a Russian project's purchase list carries (no .engineer/locale.json in these projects: the default language)
M, PCS, PCS_CUT = TX.t("unit.m"), TX.t("unit.pcs"), TX.t("unit.pcs_cut")


def by_length(name, mm):
    return TX.t("bought.by_length", name=name, mm=mm)


def piece(stock, length, note, labels):
    return TX.t("bought.cut_piece", stock=stock, length=length, note=note, labels=labels)


def total(name, count):
    return TX.t("bought.cut_total", total=name, count=count)


# ---------------------------------------------------------------- placement matrices (4x4 row-major, flattened to 16)
def T(x, y, z):
    return [1, 0, 0, x, 0, 1, 0, y, 0, 0, 1, z, 0, 0, 0, 1]


def RX90(x, y, z):          # rotate +90 deg about X (own z -> machine -y), then translate
    return [1, 0, 0, x, 0, 0, -1, y, 0, 1, 0, z, 0, 0, 0, 1]


def write_model(root, specs):
    """specs: [(pid, process, build123d shape or None, extra fields, [loc, ...] or None)].
    shape None -> a row-only part (no STEP), like a node's bought row. With placements, qty = number of placements
    (unless `extra` sets qty), placement names pid, pid_2, pid_3 ..."""
    root = Path(root)
    (root / "mechanics" / "cad" / "parts").mkdir(parents=True, exist_ok=True)
    (root / "mechanics" / "cad" / "params.py").write_text("X = 1\n")
    (root / "build/model/step").mkdir(parents=True, exist_ok=True)
    parts = []
    for i, (pid, proc, shape, extra, locs) in enumerate(specs):
        extra = dict(extra)
        if shape is None:
            p = {"id": pid, "name": pid, "node": "boom", "func": "boom.build", "process": proc,
                 "key": f"{i:02d}" + "e" * 62}
            p.update(extra)
        else:
            qty = extra.pop("qty", None)
            p = H._write_part(root, pid, proc, shape, extra)
            if locs is not None:
                p["placements"] = [{"name": pid if k == 0 else f"{pid}_{k + 1}", "loc": loc} for k, loc in enumerate(locs)]
                p["qty"] = len(locs)
            if qty is not None:
                p["qty"] = qty
        parts.append(p)
    H._write_parts_json(root, parts)
    return root


def rows_named(d, pred):
    return [r for r in d.manifest["bought"] if pred(r)]


def one_row(d, pred, what):
    got = rows_named(d, pred)
    assert len(got) == 1, f"{what}: expected exactly one row, got {len(got)}:\n" + json.dumps(d.manifest["bought"], ensure_ascii=False, indent=1)
    return got[0]


def dump(d):
    return json.dumps(d.manifest["bought"], ensure_ascii=False, indent=1)


def _box(x, y, z):
    from build123d import Box
    return Box(x, y, z)


# ================================================================ DW1: merged rows keep every node
@pytest.fixture(scope="module")
def nodes_run(tmp_path_factory):
    root = write_model(tmp_path_factory.mktemp("nodes") / "proj", [
        ("bolt_legs", "bought", _box(2, 2, 2), {"name": "Bolt", "code": "B5", "node": "legs", "qty": 2}, None),
        ("bolt_frame", "bought", _box(2, 2, 2), {"name": "Bolt", "code": "B5", "node": "frame", "qty": 3}, None),
        ("bolt_frame2", "bought", None, {"name": "Bolt", "code": "B5", "node": "frame", "qty": 1}, None),
        ("bolt_nonode", "bought", None, {"name": "Bolt", "code": "B5", "node": "", "qty": 1}, None),
        ("nut", "bought", _box(2, 2, 1), {"name": "Nut", "code": "N5", "node": "frame", "qty": 4}, None),
    ])
    return root, H.run_derive(root, "bought")


def test_merged_row_lists_every_distinct_node_sorted(nodes_run):
    """DW1: Bolt from legs, frame, frame and a part without a node -> node "frame, legs", qty summed."""
    root, d = nodes_run
    d.ok()
    row = one_row(d, lambda r: r["code"] == "B5", "Bolt")
    assert row["qty"] == 7, dump(d)
    assert row["node"] == "frame, legs", f"node of the merged Bolt row: {row['node']!r}"


def test_unmerged_row_keeps_its_single_node(nodes_run):
    """DW1 regression guard: a row from one node still says just that node."""
    root, d = nodes_run
    d.ok()
    assert one_row(d, lambda r: r["code"] == "N5", "Nut")["node"] == "frame"


# ================================================================ DW2: stock sold by length
@pytest.fixture(scope="module")
def length_run(tmp_path_factory):
    root = write_model(tmp_path_factory.mktemp("length") / "proj", [
        # 2 x 500 + 1 x 300 = 1300 mm of the same profile; default margin 1.2 -> 1560 mm -> 2 m
        ("rail_a", "bought", _box(500, 20, 20), {"name": "Profile 20x20", "code": "AL2020", "buy": "length", "node": "frame"},
         [T(0, 0, 0), T(0, 100, 0)]),
        ("rail_b", "bought", _box(20, 300, 20), {"name": "Profile 20x20", "code": "AL2020", "buy": "length", "node": "legs"},
         [T(0, 200, 0)]),
        # margin 1.0, 1001 mm -> ceil(1.001) = 2 m
        ("rod", "bought", _box(10, 10, 1001), {"name": "Rod 10", "code": "ROD10", "buy": "length", "buy_margin": 1.0},
         [T(100, 0, 0)]),
        # tiny piece: 10 mm * 1.2 = 0.012 m -> at least 1
        ("stub", "bought", _box(10, 8, 6), {"name": "Stub 10", "code": "STUB", "buy": "length"}, [T(200, 0, 0)]),
        # a per-piece bought part stays as before
        ("nut", "bought", _box(2, 2, 1), {"name": "Nut", "code": "N5", "qty": 4}, None),
    ])
    return root, H.run_derive(root, "bought")


def test_length_parts_with_one_code_make_one_metre_row(length_run):
    """DW2: rail_a (2 placements x 500) + rail_b (300) -> one row, 1300 mm, ceil(1300*1.2/1000) = 2 m."""
    root, d = length_run
    d.ok()
    rows = rows_named(d, lambda r: r["code"] == "AL2020")
    assert len(rows) == 1, "no per-piece rows for a length part:\n" + dump(d)
    row = rows[0]
    assert row["unit"] == M and row["qty"] == 2, dump(d)
    assert row["name"] == by_length("Profile 20x20", 1300), row["name"]


def test_length_row_uses_buy_margin(length_run):
    """DW2: buy_margin 1.0 on 1001 mm -> 2 m (rounded up, never down)."""
    root, d = length_run
    d.ok()
    row = one_row(d, lambda r: r["code"] == "ROD10", "Rod 10")
    assert row["qty"] == 2 and row["unit"] == M, dump(d)
    assert row["name"] == by_length("Rod 10", 1001), row["name"]


def test_length_row_is_at_least_one_metre(length_run):
    root, d = length_run
    d.ok()
    row = one_row(d, lambda r: r["code"] == "STUB", "Stub 10")
    assert row["qty"] == 1 and row["unit"] == M, dump(d)
    assert row["name"] == by_length("Stub 10", 10), row["name"]


def test_per_piece_bought_rows_are_unchanged_next_to_length_rows(length_run):
    root, d = length_run
    d.ok()
    row = one_row(d, lambda r: r["code"] == "N5", "Nut")
    assert row["qty"] == 4 and row["unit"] == PCS, dump(d)
    assert len(d.manifest["bought"]) == 4, dump(d)


# ================================================================ DW3: cut list
PIPE = {"stock": "Pipe 20x20", "note": "ends 90°", "total": "Pipe 20x20 total", "code": "T2020", "class": "steel",
        "all_total": "Profile overall"}
ANGLE = {"stock": "Angle 25", "note": "no chamfer", "total": "Angle 25 total", "code": "U25", "class": "steel",
         "all_total": "Profile overall"}
FLAT = {"stock": "Strap 4x30", "note": "deburr", "label": "bar", "total": "Strap total", "code": "P430", "class": "alu"}


def _cut_specs(with_cut=True):
    specs = [
        # made part, 250 mm, two pieces, node frame
        ("post", "mill", _box(20, 20, 250), {"node": "frame", "cut": dict(PIPE, label="post")}, [T(0, 0, 0), T(100, 0, 0)]),
        # bought part with cut, 250.2 mm -> L 250, merges with post's pieces; node legs; makes no row of its own
        ("beam", "bought", _box(20, 250.2, 20), {"node": "legs", "code": "OWN-BEAM", "desc": "Beam own row",
                                                  "cut": dict(PIPE, label="beam")}, [T(0, 0, 300)]),
        # same stock, other length
        ("brace", "laser", _box(120, 20, 20), {"node": "frame", "cut": dict(PIPE, label="brace")}, [T(0, 0, 600)]),
        # second stock sharing the all_total, 400 mm
        ("angle", "mill", _box(400, 25, 25), {"node": "frame", "cut": dict(ANGLE, label="angle")}, [T(0, 0, 900)]),
        # third stock, no all_total
        ("strap", "mill", _box(30, 4, 80), {"node": "top", "cut": FLAT}, [T(0, 0, 1200), T(50, 0, 1200), T(90, 0, 1200)]),
    ]
    if not with_cut:
        specs = [(pid, proc, shape, {k: v for k, v in extra.items() if k != "cut"}, locs) for pid, proc, shape, extra, locs in specs]
    return specs


@pytest.fixture(scope="module")
def cut_run(tmp_path_factory):
    root = write_model(tmp_path_factory.mktemp("cut") / "proj", _cut_specs())
    return root, H.run_derive(root, "bought")


def test_cut_pieces_of_equal_length_merge_across_parts(cut_run):
    """DW3: post x2 (250, frame) + beam x1 (250.2 -> 250, legs) -> one row of 3 pieces, labels sorted, both nodes."""
    root, d = cut_run
    d.ok()
    row = one_row(d, lambda r: r["name"] == piece("Pipe 20x20", 250, "ends 90°", "beam; post; post"), "pipe 250")
    assert row["qty"] == 3 and row["unit"] == PCS_CUT, dump(d)
    assert row["kind"] == "profile" and row["class"] == "steel" and row["code"] == "T2020", row
    assert row["node"] == "frame, legs", row["node"]


def test_cut_piece_row_per_length(cut_run):
    root, d = cut_run
    d.ok()
    row = one_row(d, lambda r: r["name"] == piece("Pipe 20x20", 120, "ends 90°", "brace"), "pipe 120")
    assert row["qty"] == 1 and row["unit"] == PCS_CUT and row["node"] == "frame", row
    strap = one_row(d, lambda r: r["name"] == piece("Strap 4x30", 80, "deburr", "bar; bar; bar"), "strap 80")
    assert strap["qty"] == 3 and strap["class"] == "alu" and strap["code"] == "P430" and strap["node"] == "top", strap


def test_cut_stock_total_row_in_metres(cut_run):
    """DW3: pipe pieces 250+250+250+120 = 870 mm in 4 pieces -> 0.87 m."""
    root, d = cut_run
    d.ok()
    row = one_row(d, lambda r: r["name"] == total("Pipe 20x20 total", 4), "pipe total")
    assert row["qty"] == pytest.approx(0.87, abs=1e-9) and row["unit"] == M, row
    assert row["node"] == "" and row["code"] == "T2020" and row["class"] == "steel" and row["kind"] == "profile", row
    flat = one_row(d, lambda r: r["name"] == total("Strap total", 3), "strap total")
    assert flat["qty"] == pytest.approx(0.24, abs=1e-9), flat


def test_stocks_sharing_all_total_get_one_grand_total(cut_run):
    """DW3: pipe 0.87 m + angle 0.4 m share "Profile overall" -> 1.27 m; the strap (no all_total) is not in it."""
    root, d = cut_run
    d.ok()
    row = one_row(d, lambda r: r["name"] == "Profile overall", "all_total")
    assert row["qty"] == pytest.approx(1.27, abs=1e-9) and row["unit"] == M, row
    assert row["code"] == "" and row["node"] == "", row


def test_cut_list_has_exactly_the_expected_rows(cut_run):
    """DW3: the bought part with `cut` makes no row of its own; made parts never make bought rows."""
    root, d = cut_run
    d.ok()
    names = sorted(r["name"] for r in d.manifest["bought"])
    assert names == sorted([
        piece("Pipe 20x20", 250, "ends 90°", "beam; post; post"),
        piece("Pipe 20x20", 120, "ends 90°", "brace"),
        total("Pipe 20x20 total", 4),
        piece("Angle 25", 400, "no chamfer", "angle"),
        total("Angle 25 total", 1),
        piece("Strap 4x30", 80, "deburr", "bar; bar; bar"),
        total("Strap total", 3),
        "Profile overall",
    ]), dump(d)
    assert not rows_named(d, lambda r: r["code"] == "OWN-BEAM"), dump(d)


def test_cut_rows_are_ordered_pieces_then_total_per_stock(cut_run):
    """DW3: for each stock, its piece rows come before its total row."""
    root, d = cut_run
    d.ok()
    names = [r["name"] for r in d.manifest["bought"]]
    idx = {n: i for i, n in enumerate(names)}
    pipe_total = idx[total("Pipe 20x20 total", 4)]
    assert idx[piece("Pipe 20x20", 250, "ends 90°", "beam; post; post")] < pipe_total
    assert idx[piece("Pipe 20x20", 120, "ends 90°", "brace")] < pipe_total
    assert idx[piece("Angle 25", 400, "no chamfer", "angle")] < idx[total("Angle 25 total", 1)]


def test_made_part_with_cut_keeps_its_step_scope(cut_run, tmp_path):
    """DW3 regression guard: `cut` adds bought rows only; the step scope lists the same parts with or without `cut`."""
    root, _ = cut_run
    with_cut = {e["id"] for e in H.run_derive(root, "step").ok().manifest["parts"]}
    plain = write_model(tmp_path / "plain", _cut_specs(with_cut=False))
    without = {e["id"] for e in H.run_derive(plain, "step").ok().manifest["parts"]}
    assert "post" in without and with_cut == without, (with_cut, without)


# ================================================================ DW4: screws counted from holes
SCREW_TPL = {"name": "Screw M{n}x{L} DIN 912", "code": "DIN912-M{n}x{L}", "supplier": "Bolt.pl", "class": "fastener",
             "kind": "screw", "unit": "pcs", "margin": 1.1}


def _drilled(x, y, z, holes):
    """Box x*y*z centred on the origin with through holes along Z: holes = [(hole x, diameter)]."""
    from build123d import Box, Cylinder, Pos
    s = Box(x, y, z)
    for hx, dia in holes:
        s = s - Pos(hx, 0, 0) * Cylinder(dia / 2, z + 10)
    return s


def _screw_specs(with_template=True):
    plate = _drilled(40, 20, 4, [(-10, 2.0), (10, 3.4)])        # z -2..2, M2-size hole at x-10, M3 clearance at x+10
    block = _drilled(40, 20, 10, [(-10, 2.0)])                 # z -5..5
    specs = [
        ("plate", "mill", plate, {"node": "frame"}, [
            T(0, 0, 0), T(0, 0, 4),                    # stack along Z, span 8: M2x6 + M3x6
            RX90(100, 0, 0), RX90(100, 4, 0),          # the same pair turned so the holes run along Y: M2x6 + M3x6
            T(200, 0, 0),                              # over block@200 at x190 (span 14: M2x12); x210 hole alone: no screw
            T(300, 0, 0), T(300, 0, 4.8),              # 0.8 mm gap still stacks, span 8.8: M2x8 + M3x8
            T(400, 0, 0), T(400, 0, 5.5),              # 1.5 mm gap: no stack, no screw
            T(500, 0, 0),                              # under `cover` (holes: false): no screw
            T(600, 0, 0),                              # under a product part: no screw
            T(700, 0, 0),                              # under `guard` whose holes_skip_with matches "plate": no screw
        ]),
        ("block", "mill", block, {"node": "frame", "screws_unpaired": [[3, 16, 2]]}, [
            T(200, 0, -7),                             # z -12..-2 under plate@200: in a counted stack
            T(1100, 0, 0),                             # alone: unpaired -> 2 x M3x16
            T(1200, 0, 0),                             # under thin_d34: M2 by the d2 hole, length from d<3.0 holes only
        ]),
        ("thin_d34", "mill", _drilled(40, 20, 2, [(-10, 3.4)]), {}, [T(1200, 0, 6)]),   # z 5..7 on block@1200
        ("cover", "mill", plate, {"holes": False}, [T(500, 0, 4)]),
        ("prod", "mill", plate, {"product": True}, [T(600, 0, 4)]),
        ("guard", "mill", plate, {"holes_skip_with": "^plate$"}, [T(700, 0, 4)]),
        ("big", "mill", _drilled(40, 20, 4, [(-10, 4.0)]), {}, [T(800, 0, 0), T(800, 0, 4)]),     # all d >= 3.7: no screw
        ("tiny", "mill", _drilled(40, 20, 4, [(-10, 1.0)]), {}, [T(900, 0, 0), T(900, 0, 4)]),    # r 0.5 < 0.7: not a hole
        ("thick", "mill", _drilled(40, 20, 25, [(-10, 2.0)]), {}, [T(1000, 0, 0), T(1000, 0, 25)]),  # span 50 > 45: no screw
        ("bracket", "print", _box(10, 10, 10), {"screws": [[2, 20, 4]]}, [T(0, 300, 0), T(50, 300, 0)]),   # 8 x M2x20
        ("nut", "bought", _box(2, 2, 1), {"name": "Nut", "code": "N5", "qty": 4}, None),
    ]
    if with_template:
        specs.append(("screw_tpl", "bought", None, {"name": "Screws by holes", "code": "TPL", "screws_from_holes": SCREW_TPL}, None))
    return specs


# (n, L) -> raw count; qty = ceil(count * 1.1)
EXPECTED_SCREWS = {(2, 6): 2, (3, 6): 2, (2, 12): 1, (2, 8): 2, (3, 8): 1, (2, 20): 8, (3, 16): 2}


@pytest.fixture(scope="module")
def screws_model(tmp_path_factory):
    return write_model(tmp_path_factory.mktemp("screws") / "proj", _screw_specs())


@pytest.fixture(scope="module")
def screws_run(screws_model):
    return screws_model, H.run_derive(screws_model, "bought")


def screw_rows(d):
    return {r["code"]: r for r in d.manifest["bought"] if r.get("code", "").startswith("DIN912-")}


def test_screw_rows_are_exactly_the_expected_sizes_and_counts(screws_run):
    """DW4: stacks (Z and Y axes, gap <= 1 mm), the holes:false / product / holes_skip_with / d>=3.7 / small-radius / span>45
    exclusions, `screws` per placement and `screws_unpaired` on the unpaired placement only, margin 1.1 rounded up."""
    root, d = screws_run
    d.ok()
    got = {code: r["qty"] for code, r in screw_rows(d).items()}
    want = {f"DIN912-M{n}x{L}": math.ceil(c * 1.1) for (n, L), c in EXPECTED_SCREWS.items()}
    assert got == want, f"screw rows by code -> qty\n got: {got}\nwant: {want}\n" + dump(d)


@pytest.mark.parametrize("n,L,why", [
    (2, 6, "two coaxial plate holes d2.0 along Z and along Y, span 8 -> M2x6, 2 stacks"),
    (3, 6, "the d3.4 holes of the same pairs -> M3x6"),
    (2, 12, "plate over block, span 14 -> M2x12"),
    (3, 8, "0.8 mm gap stacks, span 8.8 -> M3x8"),
])
def test_one_stack_kind(screws_run, n, L, why):
    root, d = screws_run
    d.ok()
    code = f"DIN912-M{n}x{L}"
    assert code in screw_rows(d), f"{why}: no row {code}\n" + dump(d)
    assert screw_rows(d)[code]["qty"] == math.ceil(EXPECTED_SCREWS[(n, L)] * 1.1), screw_rows(d)[code]


def test_m2_length_spans_only_the_small_holes(screws_run):
    """DW4: block (10 mm, d2.0) under thin_d34 (2 mm, d3.4): M2 by the smallest hole, span over d<3.0 holes = 10 -> M2x8
    (counted with the gap stack: 2), not M2x10 from the 12 mm span of all holes."""
    root, d = screws_run
    d.ok()
    rows = screw_rows(d)
    assert "DIN912-M2x10" not in rows, dump(d)
    assert rows.get("DIN912-M2x8", {}).get("qty") == math.ceil(2 * 1.1), dump(d)


def test_extra_screws_per_placement(screws_run):
    """DW4: bracket `screws: [[2, 20, 4]]` placed twice -> 8 -> ceil(8.8) = 9."""
    root, d = screws_run
    d.ok()
    assert screw_rows(d).get("DIN912-M2x20", {}).get("qty") == 9, dump(d)


def test_unpaired_screws_only_for_placements_outside_counted_stacks(screws_run):
    """DW4: block has 3 placements, only block@1100 is in no counted stack -> 2 x M3x16 -> ceil(2.2) = 3."""
    root, d = screws_run
    d.ok()
    assert screw_rows(d).get("DIN912-M3x16", {}).get("qty") == 3, dump(d)


def test_screw_row_fields_come_from_the_template(screws_run):
    root, d = screws_run
    d.ok()
    row = screw_rows(d).get("DIN912-M2x6")
    assert row is not None, dump(d)
    assert row["name"] == "Screw M2x6 DIN 912" and row["unit"] == PCS and row["supplier"] == "Bolt.pl", row
    assert row["class"] == "fastener" and row["kind"] == "screw" and row["node"] == "", row


def test_template_part_makes_no_row_of_its_own(screws_run):
    root, d = screws_run
    d.ok()
    assert not rows_named(d, lambda r: r["code"] == "TPL" or r["name"] == "Screws by holes"), dump(d)
    assert one_row(d, lambda r: r["code"] == "N5", "Nut")["qty"] == 4


def test_no_template_no_screws_from_holes(tmp_path):
    """DW4: without a `screws_from_holes` part nothing is counted from holes (nor from `screws` extras)."""
    root = write_model(tmp_path / "proj", _screw_specs(with_template=False))
    d = H.run_derive(root, "bought").ok()
    assert screw_rows(d) == {}, dump(d)
    assert [r["code"] for r in d.manifest["bought"]] == ["N5"], dump(d)


# ================================================================ DW5: deterministic, cached
def test_second_run_reuses_everything_and_gives_the_same_rows(screws_model, tmp_path):
    root = Path(shutil.copytree(screws_model, tmp_path / "proj"))
    first = H.run_derive(root, "bought").ok()
    assert screw_rows(first), "screws must be counted for this check to mean anything\n" + dump(first)
    second = H.run_derive(root, "bought").ok()
    assert second.computed == 0, second.log[-1500:]
    assert second.manifest["bought"] == first.manifest["bought"]


def test_same_model_in_two_places_gives_identical_rows(screws_run, tmp_path):
    """DW5: nothing depends on paths, timestamps or set order."""
    root, d = screws_run
    d.ok()
    assert screw_rows(d), "screws must be counted for this check to mean anything\n" + dump(d)
    other = write_model(tmp_path / "again", _screw_specs())
    d2 = H.run_derive(other, "bought").ok()
    assert d2.manifest["bought"] == d.manifest["bought"]


def test_cut_and_length_rows_are_stable_on_rerun(cut_run, length_run):
    for root, d in (cut_run, length_run):
        d.ok()
        assert any(r["unit"] == M for r in d.manifest["bought"]), "metre rows must exist for this check to mean anything\n" + dump(d)
        again = H.run_derive(root, "bought").ok()
        assert again.computed == 0, again.log[-1500:]
        assert again.manifest["bought"] == d.manifest["bought"]


def test_row_only_length_part_counts_its_buy_length(tmp_path):
    """A stock-by-length row that is not drawn (hoses not shown) gives its length as `buy_length` (mm) and merges with the drawn
    pieces of the same (code, name): 971 (row) + 50 (drawn) = 1021 mm -> ceil(1021 * 1.2 / 1000) = 2 m."""
    root = write_model(tmp_path / "proj", [
        ("hose_row", "bought", None, {"name": "Hose 6", "code": "", "buy": "length", "buy_length": 971.0, "node": ""}, None),
        ("hose_dip", "bought", _box(50, 6, 6), {"name": "Hose 6", "code": "", "buy": "length", "node": "core"}, [T(0, 0, 0)]),
    ])
    d = H.run_derive(root, "bought")
    d.ok()
    row = one_row(d, lambda r: r["name"].startswith("Hose 6"), "Hose 6")
    assert row["name"] == by_length("Hose 6", 1021) and row["qty"] == 2 and row["unit"] == M, dump(d)
    assert row["code"] == "" and row["node"] == "core", dump(d)


def test_row_only_length_part_counts_its_qty(tmp_path):
    """A row-only stock-by-length part with qty 3 is three pieces of buy_length: 3 x 300 mm = 900 mm -> ceil(900 * 1.2 / 1000) = 2 m."""
    root = write_model(tmp_path / "proj", [
        ("cable", "bought", None, {"name": "Cable", "code": "C1", "buy": "length", "buy_length": 300.0, "qty": 3}, None)])
    d = H.run_derive(root, "bought")
    d.ok()
    row = one_row(d, lambda r: r["code"] == "C1", "Cable")
    assert row["name"] == by_length("Cable", 900) and row["qty"] == 2, dump(d)


def test_cut_piece_without_a_shape_fails_loudly(tmp_path):
    """A cut list piece takes its length from its shape: a row-only part with `cut` would vanish, so derive.py bought refuses it."""
    root = write_model(tmp_path / "proj", [
        ("beam", "bought", None, {"name": "Beam", "cut": {"stock": "Profile", "note": "", "label": "beam", "total": "total"}}, None)])
    d = H.run_derive(root, "bought")
    assert d.rc != 0 and "beam" in d.log, d.log[-400:]
