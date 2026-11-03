"""Issue #113 «Wires in 3D», stage 1: the PURE part of the new `harness.py` (no meshes, no model build, no subprocess).

Written before the module existed, from the spec only. Every test imports `harness` inside its body (through `routes()` /
`bundle_d()` below), so a missing module is a FAILING test and not a collection error.

ASSUMED INTERFACE (the whole surface these tests touch; object here if it does not fit):
  harness.routes(parts, conns, cycle=None) -> (wires, problems)
      parts    = the `parts` list of build/model/parts.json (part records: id, name, placements [{name, loc: 16 floats,
                 row-major 4x4}], ports {name: label}, ports_at {port: [x, y, z] | [[x, y, z], [dx, dy, dz]]}, motion_group?)
      conns    = {"WIRES": [...], "WAYPOINTS": {...}, "HARNESS": {...}} as connections.py defines them (HARNESS may be missing)
      cycle    = the `cycle` block of a motion.json (period_s, groups {g: {kind, axis, travel_mm, segments, parts}}, node_groups),
                 or None: then nothing moves
      wires    = list of dicts, at least {"id", "length_mm", "cut_mm", "od_mm", "path"}; path = [[x, y, z], ...] in the
                 machine frame from the `from` port to the `to` port. A wire that is not routed (an end without ports_at) has
                 no path (absent / None / []) or is left out.
      problems = list of {"rule": "route" | "bend" | "clash" | "stretch", "text": str}; "route" = a bad reference.
  harness.bundle_d(ods) -> the diameter (mm) of a bundle of wires with these outer diameters.
  Units are mm everywhere. Wire ids are "W1", "W2", ... by position in WIRES, routed or not.
  Cut length = (route length + loop) * (1 + slack) + 2 * ends_mm, never shorter than the route.
  HARNESS defaults, when connections.py has none: slack 0.1, ends_mm 10, bend 4 (fixed flexible PVC wire).

Geometry rules the expected numbers rely on, all from the spec:
  - a port point is in its part's own coordinates, moved by the placement's loc; the direction is turned by it too and
    defaults to (0, 0, 1) when only the point is given;
  - a waypoint {"at", "on": placement} is in that placement's own coordinates; a bare (x, y, z) is in the machine frame;
  - the wire leaves each port straight along the port's direction, then runs through its waypoints in order;
  - where waypoints and ports are collinear there is no bend, so the route length is exactly the polyline length (that is
    why the length tests use straight routes: any corner smoothing cannot change them);
  - a bundle = the wires passing the same two consecutive waypoints; its diameter is never below its thickest wire and
    larger for 2+ wires; its cross-section holds every wire's, so bundle_d(ods) >= sqrt(sum(od^2));
  - a bend needs a radius of at least bend x the bundle diameter: a 90 deg turn needs that radius of straight run on both
    sides of the corner, so two 90 deg turns closer than that radius have no room.

Not here (need meshes, see test_harness_e2e.py): rule 1 (through a part), rule 2 (gap), the CLI, check.py, the viewer,
the purchase list, the drawing.

    <venv with pytest>/bin/python -m pytest agent/skills/machine-project/scripts/tests/test_harness.py -q
"""
import math

import pytest

ROUTE, BEND, STRETCH = "route", "bend", "stretch"
RULES = {"route", "bend", "clash", "stretch"}
NO_ALLOW = {"slack": 0.0, "ends_mm": 0.0, "bend": 6}      # cut length == route length (+ loop)


# ---------------------------------------------------------------- the module under test
def routes(parts, conns, cycle=None):
    import harness                       # in the body on purpose: a missing module must FAIL the test, not error collection
    out = harness.routes(parts, conns, cycle=cycle)
    assert isinstance(out, tuple) and len(out) == 2, f"routes() returns (wires, problems), got {out!r}"
    wires, problems = out
    assert isinstance(wires, list) and isinstance(problems, list), out
    for p in problems:
        assert isinstance(p, dict) and p.get("rule") in RULES and isinstance(p.get("text"), str) and p["text"], \
            f"every problem is {{rule: route|bend|clash|stretch, text}}: {p!r}"
    for w in wires:
        assert isinstance(w, dict) and isinstance(w.get("id"), str), f"every wire carries its id: {w!r}"
        if w.get("path"):
            for k in ("length_mm", "cut_mm", "od_mm"):
                assert isinstance(w.get(k), (int, float)), f"a routed wire carries {k}: {w!r}"
    return wires, problems


def bundle_d(ods):
    import harness
    return float(harness.bundle_d(list(ods)))


def routed(wires):
    return {w["id"]: w for w in wires if w.get("path")}


def wire(wires, wid):
    got = routed(wires)
    assert wid in got, f"{wid} must be routed; routed: {sorted(got)}; all: {wires!r}"
    return got[wid]


def of_rule(problems, rule):
    return [p for p in problems if p["rule"] == rule]


# ---------------------------------------------------------------- hand-made model records
def T(x, y, z):
    return [1, 0, 0, x, 0, 1, 0, y, 0, 0, 1, z, 0, 0, 0, 1]


def RX90(x, y, z):               # +90 deg about X (own +Z -> machine -Y), then translate
    return [1, 0, 0, x, 0, 0, -1, y, 0, 1, 0, z, 0, 0, 0, 1]


def part(pid, loc=None, ports_at=None, ports=None, placements=None, **extra):
    """A parts.json record. ports default to the names in ports_at."""
    rec = {"id": pid, "name": pid, "node": "m", "func": "m.build", "process": "bought", "kind": "electrical",
           "key": "0" * 64, "qty": 1, "ports": ports if ports is not None else {k: "" for k in (ports_at or {})},
           "placements": placements or [{"name": pid, "node": "m", "loc": loc or T(0, 0, 0)}]}
    if ports_at is not None:
        rec["ports_at"] = ports_at
    rec.update(extra)
    return rec


def conns(wires, waypoints=None, harness=None):
    c = {"WIRES": wires, "WAYPOINTS": waypoints or {}}
    if harness is not None:
        c["HARNESS"] = harness
    return c


def straight_model():
    """src.P at world (0,0,0) leaving +X, dst.Q at world (200,0,0) leaving -X, both through non-trivial placements;
    waypoint `mid` on placement `carrier` at world (100,0,0). Route length exactly 200 when every transform is applied."""
    return [
        part("src", T(10, 20, 30), {"P": [[-10, -20, -30], [1, 0, 0]]}),
        part("dst", T(200, 5, 0), {"Q": [[0, -5, 0], [-1, 0, 0]]}),
        part("carrier", T(90, -30, 0), process="mill", kind=None, ports={}),
    ]


MID = {"mid": {"at": (10, 30, 0), "on": "carrier"}}


def one_wire(**kw):
    return [dict({"from": "src.P", "to": "dst.Q", "net": "SIG", "spec": "0.5 mm²", "via": ["mid"]}, **kw)]


def dist(a, b):
    return math.dist([float(v) for v in a], [float(v) for v in b])


def unit(v):
    n = math.sqrt(sum(float(x) ** 2 for x in v))
    assert n > 1e-9, f"zero-length segment: {v}"
    return [float(x) / n for x in v]


def near_path(path, pt, tol=1e-3):
    """Distance from pt to the polyline."""
    best = math.inf
    for a, b in zip(path, path[1:]):
        a, b = [float(v) for v in a], [float(v) for v in b]
        ab = [b[i] - a[i] for i in range(3)]
        L2 = sum(x * x for x in ab)
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, sum((pt[i] - a[i]) * ab[i] for i in range(3)) / L2))
        best = min(best, dist(pt, [a[i] + t * ab[i] for i in range(3)]))
    return best


# ================================================================ the route: where it starts, where it goes, how long it is
def test_r1_a_straight_route_runs_from_port_to_port_through_its_waypoint_with_every_transform_applied():
    wires, problems = routes(straight_model(), conns(one_wire(), MID, NO_ALLOW))
    assert problems == [], f"a straight, well-referenced wire has no problem: {problems}"
    w = wire(wires, "W1")
    assert dist(w["path"][0], (0, 0, 0)) < 1e-3, \
        f"the path starts at src.P in the machine frame: own (-10,-20,-30) moved by the placement (10,20,30) is (0,0,0), got {w['path'][0]}"
    assert dist(w["path"][-1], (200, 0, 0)) < 1e-3, f"the path ends at dst.Q = (200,0,0), got {w['path'][-1]}"
    assert near_path(w["path"], (100, 0, 0)) < 1e-3, \
        f"the path passes the waypoint `mid`: own (10,30,0) on carrier at (90,-30,0) is (100,0,0); path {w['path']}"
    assert w["length_mm"] == pytest.approx(200.0, abs=0.01), f"port to port in a straight line is 200 mm: {w}"


def test_r2_the_route_leaves_and_enters_each_port_along_its_direction():
    """src.P leaves +X, dst.Q leaves +Z, mid is off both axes: the first and the last straight pieces show the directions."""
    parts = [part("src", T(0, 0, 0), {"P": [[0, 0, 0], [1, 0, 0]]}),
             part("dst", T(0, 0, 0), {"Q": [[100, 100, 0], [0, 0, 1]]})]
    wires, problems = routes(parts, conns(one_wire(), {"mid": (60, 40, 60)}, NO_ALLOW))
    w = wire(wires, "W1")
    p = w["path"]
    first = unit([p[1][i] - p[0][i] for i in range(3)])
    last = unit([p[-2][i] - p[-1][i] for i in range(3)])
    assert first == pytest.approx([1, 0, 0], abs=1e-3), f"the wire leaves src.P straight along +X: first piece {p[:2]}"
    assert last == pytest.approx([0, 0, 1], abs=1e-3), f"the wire leaves dst.Q straight along +Z: last piece {p[-2:]}"


def test_r3_a_port_given_as_a_point_only_leaves_along_plus_z():
    parts = [part("src", T(0, 0, 0), {"P": [0, 0, 0]}),
             part("dst", T(0, 0, 0), {"Q": [[100, 0, 0], [-1, 0, 0]]})]
    wires, _ = routes(parts, conns(one_wire(), {"mid": (50, 30, 40)}, NO_ALLOW))
    p = wire(wires, "W1")["path"]
    assert unit([p[1][i] - p[0][i] for i in range(3)]) == pytest.approx([0, 0, 1], abs=1e-3), \
        f"no direction given: the wire leaves along (0, 0, 1); first piece {p[:2]}"


def test_r4_the_port_direction_turns_with_the_placement():
    """Default +Z on a part turned +90 deg about X points along machine -Y."""
    parts = [part("src", RX90(0, 0, 0), {"P": [0, 0, 0]}),
             part("dst", T(0, 0, 0), {"Q": [[100, 0, 0], [-1, 0, 0]]})]
    wires, _ = routes(parts, conns(one_wire(), {"mid": (50, -40, 30)}, NO_ALLOW))
    p = wire(wires, "W1")["path"]
    assert unit([p[1][i] - p[0][i] for i in range(3)]) == pytest.approx([0, -1, 0], abs=1e-3), \
        f"own +Z turned 90 deg about X is machine -Y: the wire leaves that way; first piece {p[:2]}"


def test_r5_a_waypoint_on_a_placement_with_several_copies_uses_that_copy():
    """Placement names, not part ids: `carrier_2` is the second copy of carrier, 100 mm further along +X."""
    parts = straight_model()
    parts[2]["placements"] = [{"name": "carrier", "node": "m", "loc": T(-100, -30, 0)},
                              {"name": "carrier_2", "node": "m", "loc": T(90, -30, 0)}]
    wires, problems = routes(parts, conns(one_wire(), {"mid": {"at": (10, 30, 0), "on": "carrier_2"}}, NO_ALLOW))
    assert problems == [], problems
    w = wire(wires, "W1")
    assert near_path(w["path"], (100, 0, 0)) < 1e-3 and w["length_mm"] == pytest.approx(200.0, abs=0.01), \
        f"the waypoint rides on carrier_2 at (90,-30,0), so the route is the straight 200 mm: {w}"


# ================================================================ cut length
def test_c1_with_no_allowances_the_cut_length_is_the_route():
    wires, _ = routes(straight_model(), conns(one_wire(), MID, NO_ALLOW))
    w = wire(wires, "W1")
    assert w["cut_mm"] == pytest.approx(200.0, abs=0.5), f"slack 0, ends 0, no loop: cut = route = 200: {w}"


def test_c2_the_ends_allowance_is_added_once_per_end():
    wires, _ = routes(straight_model(), conns(one_wire(), MID, {"slack": 0.0, "ends_mm": 15, "bend": 6}))
    assert wire(wires, "W1")["cut_mm"] == pytest.approx(230.0, abs=0.5), "200 + 2 x 15 per end = 230"


def test_c3_the_slack_is_a_fraction_of_the_route():
    wires, _ = routes(straight_model(), conns(one_wire(), MID, {"slack": 0.1, "ends_mm": 0, "bend": 6}))
    assert 220.0 - 0.5 <= wire(wires, "W1")["cut_mm"] < 225.0, "200 x (1 + 0.1) = 220 (rounded up to 5 mm at most)"


def test_c4_the_service_loop_is_added_to_the_route():
    wires, _ = routes(straight_model(), conns(one_wire(loop=25), MID, NO_ALLOW))
    w = wire(wires, "W1")
    assert w["cut_mm"] == pytest.approx(225.0, abs=0.5), f"200 + loop 25 = 225: {w}"
    assert w["length_mm"] == pytest.approx(200.0, abs=0.01), f"the loop is cut, not routed: the route stays 200: {w}"


def test_c5_slack_applies_to_route_plus_loop_then_the_ends():
    wires, _ = routes(straight_model(), conns(one_wire(loop=50), MID, {"slack": 0.2, "ends_mm": 10, "bend": 6}))
    assert wire(wires, "W1")["cut_mm"] == pytest.approx((200 + 50) * 1.2 + 20, abs=0.5), "(200 + 50) x 1.2 + 2 x 10 = 320"


def test_c6_without_HARNESS_the_spec_defaults_apply():
    wires, _ = routes(straight_model(), conns(one_wire(), MID))
    assert 240.0 - 0.5 <= wire(wires, "W1")["cut_mm"] < 245.0, \
        "defaults slack 0.1, ends 10 mm: 200 x 1.1 + 2 x 10 = 240 (rounded up to 5 mm at most)"


def test_c7_the_cut_length_is_never_shorter_than_the_route():
    wires, _ = routes(straight_model(), conns(one_wire(), MID, {"slack": -0.5, "ends_mm": 0, "bend": 6}))
    w = wire(wires, "W1")
    assert w["cut_mm"] >= w["length_mm"] - 1e-6 and w["cut_mm"] >= 200 - 1e-6, \
        f"a negative slack must not cut the wire shorter than its route: {w}"


# ================================================================ outer diameter and bundles
def test_d1_an_explicit_od_is_the_wire_diameter():
    wires, _ = routes(straight_model(), conns(one_wire(od=3.2), MID, NO_ALLOW))
    assert wire(wires, "W1")["od_mm"] == pytest.approx(3.2), "`od` given: it is the wire's outer diameter"


def _od(spec):
    wires, problems = routes(straight_model(), conns(one_wire(spec=spec), MID, NO_ALLOW))
    assert not of_rule(problems, ROUTE), f"spec {spec!r} is a valid cross-section: {problems}"
    return float(wire(wires, "W1")["od_mm"])


def test_d2_the_od_follows_the_cross_section_in_mm2():
    small, big = _od("0.5 mm²"), _od("1.5 mm²")
    assert small > math.sqrt(4 * 0.5 / math.pi), f"0.5 mm² has a 0.80 mm conductor; insulated it is thicker: {small}"
    assert big > small, f"1.5 mm² is a thicker wire than 0.5 mm²: {big} vs {small}"
    assert small < 5 and big < 6, f"a hook-up wire is a few mm at most: {small}, {big}"


def test_d3_the_od_follows_an_awg_size():
    awg24, awg18 = _od("24 AWG"), _od("18 AWG")
    assert awg24 > 0.511, f"24 AWG has a 0.51 mm conductor; insulated it is thicker: {awg24}"
    assert awg18 > awg24, f"18 AWG is thicker than 24 AWG: {awg18} vs {awg24}"
    assert _od("AWG24") == _od("AWG 24") == awg24, "the gauge may come after the word too"


def test_d4_bundle_of_one_wire_is_that_wire():
    assert bundle_d([2.0]) == pytest.approx(2.0)


@pytest.mark.parametrize("ods", [[2.0, 2.0], [1.0, 3.0], [1.0] * 3, [1.0] * 9, [0.8, 1.2, 2.5, 1.6]])
def test_d5_a_bundle_of_two_or_more_is_thicker_than_its_thickest_wire_and_holds_their_cross_sections(ods):
    d = bundle_d(ods)
    assert d > max(ods), f"a bundle of {ods} is thicker than its thickest wire: {d}"
    assert d >= math.sqrt(sum(x * x for x in ods)) - 1e-9, \
        f"the bundle's cross-section holds every wire's: d >= sqrt(sum od^2) = {math.sqrt(sum(x * x for x in ods)):.3f}, got {d}"


def test_d6_a_bundle_never_gets_thinner_when_a_wire_is_added():
    """Not strictly thicker: 7 equal wires pack in the same circle as 6 (one in the middle)."""
    ds = [bundle_d([1.0] * n) for n in range(1, 10)]
    assert all(b >= a - 1e-9 for a, b in zip(ds, ds[1:])), f"adding a wire never makes the bundle thinner: {ds}"
    assert ds[-1] > ds[1], f"nine wires make a thicker bundle than two: {ds}"


# ================================================================ rule 3: bend room
def zigzag(n, s, own_k2=False):
    """n wires a.Pi -> b.Qi, each leaving +X from (-400, 0, z_i), turning 90 deg at k1 (100,0,0) towards +Y, 90 deg again
    at k2 (100, s, 0) back to +X, into b.Qi at (600, s, z_i). z_i = 1.2 i: well apart from each other, nearly no turn
    before k1. own_k2: each wire through its own k2_i at the same place (no two wires share a pair of waypoints)."""
    ap = {f"P{i}": [[-400, 0, 1.2 * i], [1, 0, 0]] for i in range(n)}
    bp = {f"Q{i}": [[600, s, 1.2 * i], [-1, 0, 0]] for i in range(n)}
    parts = [part("a", T(0, 0, 0), ap), part("b", T(0, 0, 0), bp)]
    wps = {"k1": (100, 0, 0), "k2": (100, s, 0)}
    if own_k2:
        wps.update({f"k2_{i}": (100, s, 1.2 * i) for i in range(n)})
    ws = [{"from": f"a.P{i}", "to": f"b.Q{i}", "net": f"N{i}", "spec": "0.5 mm²", "od": 1.0,
           "via": ["k1", f"k2_{i}" if own_k2 else "k2"]} for i in range(n)]
    return parts, conns(ws, wps, {"slack": 0.1, "ends_mm": 10, "bend": 6})


def test_b1_two_right_angle_turns_3_mm_apart_have_no_room_to_bend():
    """od 1, bend 6: radius 6 mm, so each 90 deg turn needs 6 mm of straight run next to it; 3 mm is not enough."""
    parts, c = zigzag(1, 3.0)
    _, problems = routes(parts, c)
    bad = of_rule(problems, BEND)
    assert bad, f"a 90 deg turn at k1 and another 3 mm later at k2 leave no room for a 6 mm radius: {problems}"
    assert any("W1" in p["text"] and ("k1" in p["text"] or "k2" in p["text"]) for p in bad), \
        f"the bend problem names the wire (W1) and the waypoint (k1 or k2): {bad}"


def test_b2_the_same_turns_60_mm_apart_bend_fine():
    parts, c = zigzag(1, 60.0)
    wires, problems = routes(parts, c)
    assert problems == [], f"60 mm between the turns is room for two 6 mm radii: {problems}"
    assert wire(wires, "W1")["length_mm"] == pytest.approx(500 + 60 + 500, rel=0.02), \
        f"the route is about 500 + 60 + 500 mm (a little less where the corners are rounded): {wires}"


def test_b3_the_bend_radius_is_judged_on_the_bundle_diameter():
    """15 mm between the turns: room for one 1 mm wire (radius 6, needs at most 12), not for a bundle of nine of them
    sharing k1 -> k2 (diameter >= 3, radius >= 18, more than 15 on its own)."""
    _, alone = routes(*zigzag(1, 15.0))
    assert not of_rule(alone, BEND), f"one 1 mm wire turns twice in 15 mm with a 6 mm radius: {alone}"
    _, nine = routes(*zigzag(9, 15.0))
    bad = of_rule(nine, BEND)
    assert bad, f"nine wires share k1 -> k2: a bundle >= 3 mm thick needs an 18 mm radius, 15 mm is too tight: {nine}"
    named = {f"W{i + 1}" for i in range(9) if any(f"W{i + 1}" in p["text"] for p in bad)}
    assert named, f"the bend problem names the bundle's wires: {bad}"


def test_b4_wires_that_share_only_one_waypoint_are_not_a_bundle():
    """The same nine wires through k1 but each through its own k2_i: no two wires pass the same two consecutive waypoints,
    so each is judged alone (radius 6) and 15 mm is enough."""
    _, problems = routes(*zigzag(9, 15.0, own_k2=True))
    assert not of_rule(problems, BEND), f"no shared segment, no bundle: each 1 mm wire bends fine in 15 mm: {problems}"


def test_b5_a_larger_bend_factor_fails_where_the_default_passes():
    parts, c = zigzag(1, 20.0)
    _, ok = routes(parts, c)
    assert not of_rule(ok, BEND), f"bend 6 x 1 mm: 20 mm is room for two 6 mm radii: {ok}"
    c["HARNESS"]["bend"] = 25
    _, tight = routes(parts, c)
    assert of_rule(tight, BEND), f"bend 25 x 1 mm = 25 mm radius: a 90 deg turn needs 25 mm next to it, 20 mm is too short: {tight}"


# ================================================================ ids and what is routed
def test_i1_ids_follow_WIRES_order_and_count_the_unrouted_ones():
    parts = straight_model()
    parts[0]["ports"]["NC"] = ""                         # a port with no location: its wire is not routed
    parts[1]["ports"]["NC2"] = ""
    ws = [{"from": "src.NC", "to": "dst.NC2", "net": "X", "spec": "0.5 mm²"}] + one_wire()
    wires, problems = routes(parts, conns(ws, MID, NO_ALLOW))
    got = routed(wires)
    assert set(got) == {"W2"}, f"only the second wire has ports_at at both ends, and it is W2 (WIRES order): {sorted(got)}"
    assert got["W2"]["length_mm"] == pytest.approx(200.0, abs=0.01), got["W2"]
    assert not problems, f"a wire without port locations is simply not routed, not a problem: {problems}"


def test_i2_a_wire_with_one_located_end_is_not_routed():
    parts = straight_model()
    parts[1]["ports"]["NC2"] = ""
    ws = [{"from": "src.P", "to": "dst.NC2", "net": "X", "spec": "0.5 mm²", "via": ["mid"]}]
    wires, problems = routes(parts, conns(ws, MID, NO_ALLOW))
    assert routed(wires) == {}, f"dst.NC2 has no location: W1 is not routed: {wires}"


def test_i3_a_model_without_any_ports_at_gives_nothing_and_no_problem():
    parts = [part("src", T(0, 0, 0), ports={"P": ""}), part("dst", T(200, 0, 0), ports={"Q": ""})]
    ws = [{"from": "src.P", "to": "dst.Q", "net": "SIG", "spec": "0.5 mm²"}]
    wires, problems = routes(parts, conns(ws))
    assert routed(wires) == {} and problems == [], f"no ports_at anywhere: nothing is routed, nothing is wrong: {wires}, {problems}"


def test_i4_no_wires_at_all_is_fine():
    wires, problems = routes(straight_model(), {"WIRES": []})
    assert routed(wires) == {} and problems == [], (wires, problems)


# ================================================================ bad references
def test_e1_an_unknown_waypoint_in_via_is_a_problem_naming_it_and_the_wire():
    _, problems = routes(straight_model(), conns(one_wire(via=["mid", "nowhere"]), MID, NO_ALLOW))
    bad = of_rule(problems, ROUTE)
    assert any("nowhere" in p["text"] and "W1" in p["text"] for p in bad), \
        f"`via` names a waypoint WAYPOINTS does not have: a route problem naming `nowhere` and W1: {problems}"


def test_e2_ports_at_naming_a_port_the_part_does_not_have_is_a_problem():
    parts = straight_model()
    parts[0]["ports_at"]["GHOST"] = [1, 2, 3]            # not in its `ports`
    _, problems = routes(parts, conns(one_wire(), MID, NO_ALLOW))
    bad = of_rule(problems, ROUTE)
    assert any("GHOST" in p["text"] and "src" in p["text"] for p in bad), \
        f"ports_at names port GHOST that part src does not have: a route problem naming both: {problems}"


def test_e3_a_waypoint_on_a_placement_that_does_not_exist_is_a_problem():
    _, problems = routes(straight_model(), conns(one_wire(), {"mid": {"at": (10, 30, 0), "on": "trolley"}}, NO_ALLOW))
    bad = of_rule(problems, ROUTE)
    assert any("mid" in p["text"] and "trolley" in p["text"] for p in bad), \
        f"waypoint `mid` sits on placement `trolley`, which the model has not: a route problem naming both: {problems}"


# ================================================================ rule 4: stretched over the cycle (pure: the cycle moves the points)
CYCLE = {"period_s": 1.0,
         "groups": {"slide": {"kind": "linear", "axis": "x", "travel_mm": 100.0, "segments": [[0, 1, 0, 1]],
                              "parts": ["sensor"]}},
         "node_groups": {"sensor": "slide"}}


def moving_port_model():
    """board.OUT at (0,0,0) leaving +X; fixed waypoint `corner` (100,0,0); sensor.IN at (200,0,0) leaving -X on a sensor that
    moves +X 100 mm over the cycle: the route is 200 mm at rest and 300 mm at the end of the travel."""
    return [part("board", T(0, 0, 0), {"OUT": [[0, 0, 0], [1, 0, 0]]}),
            part("sensor", T(200, 0, 0), {"IN": [[0, 0, 0], [-1, 0, 0]]}, motion_group="slide")]


def sensor_wire(**kw):
    return [dict({"from": "board.OUT", "to": "sensor.IN", "net": "SIG", "spec": "0.5 mm²", "via": ["corner"]}, **kw)]


def test_s1_a_wire_to_a_moving_port_without_enough_loop_is_stretched():
    wires, problems = routes(moving_port_model(), conns(sensor_wire(loop=50), {"corner": (100, 0, 0)}, NO_ALLOW), cycle=CYCLE)
    bad = of_rule(problems, STRETCH)
    assert bad, f"at the end of the travel the wire needs 100 mm more than at rest; a 50 mm loop is not enough: {problems}"
    assert any("W1" in p["text"] for p in bad), f"the stretch problem names the wire: {bad}"
    assert wire(wires, "W1")["length_mm"] == pytest.approx(200.0, abs=0.01), "length_mm is the route at rest"


def test_s2_enough_loop_fixes_the_stretch():
    wires, problems = routes(moving_port_model(), conns(sensor_wire(loop=120), {"corner": (100, 0, 0)}, NO_ALLOW), cycle=CYCLE)
    assert not of_rule(problems, STRETCH), f"a 120 mm loop covers the 100 mm the travel adds: {problems}"
    assert wire(wires, "W1")["cut_mm"] == pytest.approx(320.0, abs=0.5), "the loop is in the cut length: 200 + 120"


def test_s3_without_a_cycle_nothing_moves_so_nothing_stretches():
    _, problems = routes(moving_port_model(), conns(sensor_wire(), {"corner": (100, 0, 0)}, NO_ALLOW), cycle=None)
    assert not of_rule(problems, STRETCH), f"no cycle: the sensor stays where the model stands: {problems}"


def test_s4_a_waypoint_riding_on_a_moving_placement_stretches_the_wire_too():
    """Both ports fixed, (0,0,0) +X and (200,0,0) -X; the clip waypoint sits on a carriage at (100,0,0) that moves +Y 50 mm:
    the route grows from 200 to 2 x sqrt(100^2 + 50^2) = 223.6 mm."""
    cyc = {"period_s": 1.0,
           "groups": {"lift": {"kind": "linear", "axis": "y", "travel_mm": 50.0, "segments": [[0, 1, 0, 1]],
                               "parts": ["carriage"]}},
           "node_groups": {"carriage": "lift"}}
    parts = [part("a", T(0, 0, 0), {"P": [[0, 0, 0], [1, 0, 0]]}),
             part("b", T(0, 0, 0), {"Q": [[200, 0, 0], [-1, 0, 0]]}),
             part("carriage", T(80, -10, 0), ports={}, process="other", kind=None, motion_group="lift")]
    wps = {"clip": {"at": (20, 10, 0), "on": "carriage"}}
    base = {"from": "a.P", "to": "b.Q", "net": "SIG", "spec": "0.5 mm²", "via": ["clip"]}
    _, short = routes(parts, conns([dict(base, loop=10)], wps, NO_ALLOW), cycle=cyc)
    assert any("W1" in p["text"] for p in of_rule(short, STRETCH)), \
        f"the clip moves 50 mm away: the wire needs 23.6 mm more, a 10 mm loop is not enough: {short}"
    _, ok = routes(parts, conns([dict(base, loop=30)], wps, NO_ALLOW), cycle=cyc)
    assert not of_rule(ok, STRETCH), f"a 30 mm loop covers the 23.6 mm: {ok}"


def test_s5_a_wire_between_fixed_parts_never_stretches_whatever_moves_elsewhere():
    parts = straight_model() + [part("sensor", T(500, 0, 0), {"IN": [0, 0, 0]}, motion_group="slide")]
    _, problems = routes(parts, conns(one_wire(), MID, NO_ALLOW), cycle=CYCLE)
    assert not of_rule(problems, STRETCH), f"src, dst and carrier do not move: W1 cannot stretch: {problems}"
