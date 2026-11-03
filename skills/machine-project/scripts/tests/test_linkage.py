"""Closed mechanisms and motion through a solver: linkage.py against closed forms.

Done-when map:
  DW1  a machine states what is closed and what drives it, and the solver finds the rest: the four-bar, the
       slider-crank and a real machine's slider-on-a-swinging-lever come out equal to their closed forms
  DW2  every frame starts at the one before it, so the loop stays in the assembly it started in, and a loop that
       would come back mirrored is an error naming where it folded
  DW3  a loop the sizes cannot close is an error naming the gap in millimetres, never a wrong answer
  DW4  as many unknown joint values as equations is what makes a mechanism; anything else is refused by name
  DW5  the answer does not depend on how many frames were asked for
The geometry of the lever-slider cases is a real machine's own (lever 450 mm on a shaft 1100 mm up, the green lever's
pin and the bearing offset from its slider_best.json, an SC50x100 on the rear trunnion), inlined so that this
suite needs no client repository.
"""
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from linkage import Mechanism, MirrorBranch, BadDeclaration, NotSquare, WillNotClose  # noqa: E402


# --- a four-bar ---------------------------------------------------------------------------

CRANK, COUPLER, ROCKER, SPAN = 100.0, 260.0, 180.0, 300.0


def fourbar():
    m = Mechanism()
    m.turn("crank", on="ground", at=(0.0, 0.0), start=60.0)
    m.turn("rocker", on="ground", at=(SPAN, 0.0), start=100.0)
    m.meet(("crank", (CRANK, 0.0)), ("rocker", (ROCKER, 0.0)), distance=COUPLER)
    return m


def fourbar_rocker(theta, branch=-1):
    """The rocker angle by the circle intersection; branch -1 is the one the start picks."""
    pin = (CRANK * math.cos(math.radians(theta)), CRANK * math.sin(math.radians(theta)))
    dx, dz = pin[0] - SPAN, pin[1]
    span = math.hypot(dx, dz)
    cosine = (span * span + ROCKER * ROCKER - COUPLER * COUPLER) / (2 * span * ROCKER)
    return math.degrees(math.atan2(dz, dx) + branch * math.acos(max(-1.0, min(1.0, cosine))))


def test_fourbar_matches_its_closed_form():
    for frame in fourbar().sweep("crank", 60.0, 150.0, steps=30):
        assert abs(frame["rocker"] - fourbar_rocker(frame["crank"])) < 1e-6


def test_fourbar_stays_out_of_the_mirror_assembly():
    for frame in fourbar().sweep("crank", 60.0, 150.0, steps=30):
        assert abs(frame["rocker"] - fourbar_rocker(frame["crank"], branch=+1)) > 1.0


def test_a_crank_rocker_goes_round_without_being_called_a_jump():
    """The guard must not fire where the loop is only turning around: that would be useless."""
    m = Mechanism()
    m.turn("crank", on="ground", at=(0.0, 0.0), start=0.0)
    m.turn("rocker", on="ground", at=(300.0, 0.0), start=100.0)
    m.meet(("crank", (100.0, 0.0)), ("rocker", (260.0, 0.0)), distance=280.0)
    m.assemble("crank")
    for steps in (12, 72):
        assert len(m.sweep("crank", 0.0, 360.0, steps)) == steps + 1


def test_a_parallelogram_folding_flat_is_an_error():
    """Where it folds it can assemble either way, and the hand formula answers anyway."""
    m = Mechanism()
    m.turn("crank", on="ground", at=(0.0, 0.0), start=90.0)
    m.turn("rocker", on="ground", at=(300.0, 0.0), start=90.0)
    m.meet(("crank", (100.0, 0.0)), ("rocker", (100.0, 0.0)), distance=300.0)
    m.assemble("crank")
    with pytest.raises(MirrorBranch) as caught:
        m.sweep("crank", 90.0, 270.0, steps=4)
    assert "180" in str(caught.value)


def test_a_loop_the_sizes_cannot_close_says_the_gap_in_millimetres():
    m = Mechanism()
    m.turn("crank", on="ground", at=(0.0, 0.0), start=0.0)
    m.turn("rocker", on="ground", at=(1000.0, 0.0), start=180.0)
    m.meet(("crank", (100.0, 0.0)), ("rocker", (180.0, 0.0)), distance=100.0)
    with pytest.raises(WillNotClose) as caught:
        m.sweep("crank", 0.0, 10.0, steps=2)
    assert "mm" in str(caught.value)


def test_a_freedom_nothing_closes_is_refused_by_name():
    m = Mechanism()
    m.turn("crank", on="ground", at=(0.0, 0.0), start=0.0)
    m.turn("rocker", on="ground", at=(SPAN, 0.0), start=100.0)
    m.turn("spare", on="ground", at=(0.0, 100.0), start=0.0)
    m.meet(("crank", (CRANK, 0.0)), ("rocker", (ROCKER, 0.0)), distance=COUPLER)
    with pytest.raises(NotSquare) as caught:
        m.sweep("crank", 0.0, 10.0, steps=2)
    assert "spare" in str(caught.value)


# --- the lever-slider machine's two loops --------------------------------------------------------------

ZP, ARM, PICK, SWING, DIR = 1100.0, 450.0, 72.1, 71.3, -1
PIN, OFFSET = (451.4392393973569, 332.10352748101786), 3.428336782684859      # slider_best.json
LEG, LEG_ANGLE, TRUNNION = 87.59417162345012, 53.55, 348.6592334896662   # params.py sizes these to the catalogue stroke


def lever_slider():
    """Lever on the main shaft; a bearing on its tip slides along the green lever on the pin.

    The cylinder sits on the rear trunnion, square to the middle of the swing, and its rod
    eye takes the pin of the short leg.
    """
    pin = (PIN[0], ZP + PIN[1])
    mid = PICK + DIR * (SWING / 2 - LEG_ANGLE)
    trunnion = (TRUNNION * math.cos(math.radians(mid + 90)), ZP + TRUNNION * math.sin(math.radians(mid + 90)))
    m = Mechanism()
    m.turn("lever", on="ground", at=(0.0, ZP), start=PICK)
    m.turn("green", on="ground", at=pin, start=180.0)
    m.slide("bearing", on="green", offset=OFFSET, start=300.0)
    m.turn("cyl_body", on="ground", at=trunnion, start=0.0)
    m.slide("cyl_rod", on="cyl_body", start=300.0)
    m.meet(("bearing", (0.0, 0.0)), ("lever", (ARM, 0.0)))
    m.meet(("cyl_rod", (0.0, 0.0)), ("lever", (LEG, 0.0), -DIR * LEG_ANGLE))
    return m, pin, trunnion


def green_and_cylinder(theta, pin, trunnion):
    """The two loops by hand, the way the machine writes them today."""
    tip = (ARM * math.cos(math.radians(theta)), ZP + ARM * math.sin(math.radians(theta)))
    dx, dz = tip[0] - pin[0], tip[1] - pin[1]
    reach = math.hypot(dx, dz)
    green = math.degrees(math.atan2(dz, dx) - math.asin(OFFSET / reach))
    leg = math.radians(theta - DIR * LEG_ANGLE)
    pivot = (LEG * math.cos(leg), ZP + LEG * math.sin(leg))
    return green, math.hypot(pivot[0] - trunnion[0], pivot[1] - trunnion[1])


def test_the_lever_slider_loops_match_the_hand_formulas():
    m, pin, trunnion = lever_slider()
    m.assemble("lever")
    for frame in m.sweep("lever", PICK, PICK + DIR * SWING, steps=30):
        green, length = green_and_cylinder(frame["lever"], pin, trunnion)
        assert abs((frame["green"] - green + 180.0) % 360.0 - 180.0) < 1e-6
        assert abs(frame["cyl_rod"] - length) < 1e-6


def test_driving_by_the_stroke_gives_the_swing():
    """What sizing the short leg is for: 100 mm of catalogue stroke is the whole swing.

    Today this is a bisection written out by hand in params.py; here it is the same loop
    read the other way round, which is the point of naming which joint is driven.
    """
    m, pin, trunnion = lever_slider()
    retracted = m.assemble("lever")["cyl_rod"]
    frames = m.sweep("cyl_rod", retracted, retracted + 100.0, steps=40)
    assert abs(abs(frames[-1]["lever"] - frames[0]["lever"]) - SWING) < 0.05
    for frame in frames:
        green, _ = green_and_cylinder(frame["lever"], pin, trunnion)
        assert abs((frame["green"] - green + 180.0) % 360.0 - 180.0) < 1e-6


def test_the_number_of_frames_does_not_change_the_answer():
    m, _, _ = lever_slider()
    retracted = m.assemble("lever")["cyl_rod"]
    coarse = m.sweep("cyl_rod", retracted, retracted + 100.0, steps=12)
    fine = m.sweep("cyl_rod", retracted, retracted + 100.0, steps=240)
    for name in ("lever", "green", "cyl_body", "bearing"):
        assert abs(coarse[-1][name] - fine[-1][name]) < 1e-6


def test_two_sweeps_of_one_mechanism_give_the_same_answer():
    """`sweep` must not remember the sweep before it: the generator's output rests on that."""
    m, _, _ = lever_slider()
    retracted = m.assemble("lever")["cyl_rod"]
    there = m.sweep("cyl_rod", retracted, retracted + 100.0, steps=24)
    m.sweep("cyl_rod", retracted + 100.0, retracted, steps=24)
    again = m.sweep("cyl_rod", retracted, retracted + 100.0, steps=24)
    for first, third in zip(there, again):
        for name in first:
            assert abs(first[name] - third[name]) < 1e-9, name


# --- the sweep is read as a whole, so where it starts is guarded too ---------------------

def parallelogram(start):
    m = Mechanism()
    m.turn("crank", on="ground", at=(0.0, 0.0), start=start)
    m.turn("rocker", on="ground", at=(300.0, 0.0), start=start)
    m.meet(("crank", (100.0, 0.0)), ("rocker", (100.0, 0.0)), distance=300.0)
    m.assemble("crank")
    return m


def test_a_sweep_that_begins_at_the_fold_is_an_error_too():
    """Starting at an extreme is the natural thing to write, and it is where the fold is.

    Judged frame by frame this one slips through: the first frame has nothing to be slack
    against, and every frame after it only stiffens.
    """
    with pytest.raises(MirrorBranch) as caught:
        parallelogram(180.0).sweep("crank", 180.0, 270.0, steps=4)
    assert "180" in str(caught.value)


def slider_crank(start, crank=50.0, rod=200.0):
    m = Mechanism()
    m.turn("crank", on="ground", at=(0.0, 0.0), start=start)
    m.slide("slider", on="ground", start=crank + rod - 10.0)
    m.meet(("crank", (crank, 0.0)), ("slider", (0.0, 0.0)), distance=rod)
    m.assemble("crank")
    return m


def slider_position(theta, crank=50.0, rod=200.0):
    swing = crank * math.sin(math.radians(theta))
    return crank * math.cos(math.radians(theta)) + math.sqrt(rod * rod - swing * swing)


@pytest.mark.parametrize("start", [0.0, 90.0, 180.0])
def test_a_slider_crank_started_at_dead_centre_still_goes_round(start):
    """Top and bottom dead centre are where a stroke is usually declared to start.

    The output's turning points are there, so the loop's first-order motion is nought
    while the loop itself moves: that must not read as a jump to the other assembly.
    """
    for frame in slider_crank(start).sweep("crank", start, start + 360.0, steps=72):
        assert abs(frame["slider"] - slider_position(frame["crank"])) < 1e-9


def test_a_sweep_needs_at_least_one_step():
    for steps in (0, -3):
        with pytest.raises(ValueError):
            fourbar().sweep("crank", 60.0, 150.0, steps)


def test_a_loop_that_runs_out_of_reach_part_way_says_where():
    """The start closes and the frames after it do not: a 150 coupler cannot follow the crank round.

    The other refusal test fails on the very first frame; this one has to be caught by a
    warm-started frame, which is the path a cycle actually takes.
    """
    m = Mechanism()
    m.turn("crank", on="ground", at=(0.0, 0.0), start=0.0)
    m.turn("rocker", on="ground", at=(300.0, 0.0), start=133.4)
    m.meet(("crank", (100.0, 0.0)), ("rocker", (100.0, 0.0)), distance=150.0)
    assert m.assemble("crank")                                  # the start itself closes
    with pytest.raises(WillNotClose) as caught:
        m.sweep("crank", 0.0, 90.0, steps=18)
    assert "mm" in str(caught.value) and "crank" in str(caught.value)


# --- a declaration that cannot mean anything is refused by name --------------------------

def test_a_joint_on_a_body_that_does_not_exist_names_it():
    m = Mechanism()
    m.turn("crank", on="grond", at=(0.0, 0.0), start=0.0)
    m.turn("rocker", on="ground", at=(SPAN, 0.0), start=100.0)
    m.meet(("crank", (CRANK, 0.0)), ("rocker", (ROCKER, 0.0)), distance=COUPLER)
    with pytest.raises(BadDeclaration) as caught:
        m.sweep("crank", 0.0, 10.0, steps=2)
    assert "grond" in str(caught.value) and "ground" in str(caught.value)


def test_the_same_joint_declared_twice_is_refused():
    m = Mechanism()
    m.turn("crank", on="ground", at=(0.0, 0.0), start=0.0)
    with pytest.raises(BadDeclaration) as caught:
        m.turn("crank", on="ground", at=(10.0, 0.0), start=0.0)
    assert "crank" in str(caught.value)


def test_driving_a_joint_that_was_never_declared_names_it():
    with pytest.raises(BadDeclaration) as caught:
        fourbar().sweep("crnak", 60.0, 150.0, steps=2)
    assert "crnak" in str(caught.value) and "crank" in str(caught.value)


# --- what production machines need (a machine with six crank drives) ---------------

CPUSH_R, CPUSH_L = 33.5, 100.0          # that machine's params.CPUSH_DRIVE: R = travel / 2, link 100


def crank_rod_slider():
    """A crank drive with its connecting rod as a body of its own, hung on the crank pin."""
    m = Mechanism()
    m.turn("crank", on="ground", at=(0.0, 0.0), start=0.0)
    m.turn("rod", on="crank", at=(CPUSH_R, 0.0), start=0.0)
    m.slide("slider", on="ground", start=CPUSH_R + CPUSH_L)
    m.meet(("rod", (CPUSH_L, 0.0)), ("slider", (0.0, 0.0)))
    return m


def test_a_crank_drive_driven_by_its_crank_swings_dead_centre_to_dead_centre():
    """How the stepper moves it: half a turn between the dead centres, the slider the full 2R."""
    m = crank_rod_slider()
    m.assemble("crank")
    frames = m.sweep("crank", 0.0, 180.0, steps=36)
    for frame in frames:
        assert abs(frame["slider"] - slider_position(frame["crank"], CPUSH_R, CPUSH_L)) < 1e-9
    assert abs(frames[0]["slider"] - frames[-1]["slider"] - 2 * CPUSH_R) < 1e-9


def test_driving_a_crank_drive_by_its_slider_is_refused_and_says_drive_the_crank():
    """At a dead centre the slider does not say which way the crank goes on: refused, with the way out."""
    m = crank_rod_slider()
    m.assemble("crank")
    with pytest.raises(MirrorBranch) as caught:
        m.sweep("slider", CPUSH_R + CPUSH_L, CPUSH_L - CPUSH_R, steps=36)
    assert "motor turns" in str(caught.value)


def test_a_connecting_rod_rides_on_its_crank_exactly_as_the_viewer_composes_it():
    """The viewer moves a group riding on another as M_parent * M_own, each a turn about its pivot at rest.

    The rod's own value is its turn relative to the crank and its pivot is the crank pin at rest; composed
    that way the rod's far end has to land on the slider pin in every frame - so a connecting rod needs no
    per-frame track of its own.
    """
    m = crank_rod_slider()
    m.assemble("crank")
    frames = m.sweep("crank", 0.0, 180.0, steps=36)
    drawn = m.poses(frames[0])
    pin = drawn["rod"][:2]
    end = (pin[0] + CPUSH_L * math.cos(math.radians(drawn["rod"][2])),
           pin[1] + CPUSH_L * math.sin(math.radians(drawn["rod"][2])))

    def about(centre, degrees, point):
        c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
        dx, dz = point[0] - centre[0], point[1] - centre[1]
        return (centre[0] + c * dx - s * dz, centre[1] + s * dx + c * dz)

    for frame in frames:
        own = about(pin, frame["rod"] - frames[0]["rod"], end)
        world = about((0.0, 0.0), frame["crank"] - frames[0]["crank"], own)
        assert math.hypot(world[0] - frame["slider"], world[1]) < 1e-9
