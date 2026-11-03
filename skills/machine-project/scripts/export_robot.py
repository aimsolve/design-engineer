#!/usr/bin/env python3
"""The machine's own model written out as a robot description: URDF for ROS, MJCF for MuJoCo.

  python3 export_robot.py --model build/model/parts.json --out build/robot --name my_machine \
      [--mechanism mechanics/kinematics/mechanism.py] [--groups build/motion_groups.json] \
      [--format both] [--collide]

Nobody writes a second model of the machine. A link is a part or a rigid group of parts, a joint
is a declared freedom with its axis, its anchor and its travel, and a link's mass, centre of mass
and inertia come out of the parts' own solids at the density of what they are made of. The one
thing a simulator needs that a static arrangement never says - which parts move together - the
model already says twice over, and this reads whichever of the two a machine has:

  * **the docking graph**: `build/model/parts.json` carries, per placement, the joint it was
    docked by (`{to, at, by, type, value}`). A `rigid` docking welds the child into its parent's
    link - there are no fixed joints here, as there are none in PartCAD, because what is welded
    together is ONE body - and a `revolute` / `linear` docking is a joint between two links. This
    is the path a machine converted to #16 takes, and the only path a robot arm needs. A
    `--mechanism` beside it is read for what a tree cannot hold - the loops and the drive - and a
    joint it states that no link of the machine answers for is refused by name.
  * **the mechanism**: `mechanics/kinematics/mechanism.py` states the closed loops and which
    joint is driven. A loop is what a docking graph cannot hold (a part is placed once, so the part
    at the other end of the loop cannot also hold it), and it is what URDF cannot hold either. Here
    the links are the mechanism's own joints, and what moves with each comes from two names the
    mechanism adds for this: `LINKS = {<group>: <joint>}`, which group of parts is which link, and
    `DRIVES`, what drives the loop. A group is a part's `motion_group` field, or an entry of
    `build/motion_groups.json` (`--groups`) for a machine whose groups its own code works out - the
    same file `skills/sim/scripts/cycle_clearance.py` reads. A group in no `LINKS` entry is welded
    to the base, and said so.

What each format is told:

  revolute        URDF `revolute` (`continuous` where both bounds are open), MJCF `hinge`
  prismatic       URDF `prismatic`, MJCF `slide`
  cylindrical     MJCF: a hinge and a slide in one body. URDF: **refused** naming the joint
  ball            MJCF `ball`. URDF: **refused**
  a closed loop   MJCF `<equality><connect>` between the two links at the point that stays
                  together. URDF: the chain is **broken** and this says which loop and where,
                  because URDF is one tree with one root and has no spelling for a loop
  a drive         MJCF `<actuator><position>` on the driven joint, its `ctrlrange` the joint's own
                  travel and its `forcerange` what the cylinder or the motor can push. URDF has no
                  actuators: the force becomes the joint's `<limit effort>` and is reported

Nothing is invented. A link whose parts have no solid is written with no inertia, which is what URDF
means by it; MJCF is told `inertiafromgeom="false"`, because MuJoCo would otherwise weigh the convex
hull of such a link's meshes at the density of water, and a link that MOVES and weighs nothing is
refused by name rather than written. A density nobody stated is the catalogue's, and the default is
reported naming every part that took it; a joint a format has no type for is refused rather than
written as a chain of links of a mass nobody measured (which is what PartCAD's `dummyMass` was, and
why they removed it).

Frames. Every link frame is the machine frame translated to the link's own anchor - the pivot of
its joint, or where its body sits at the pose the CAD is drawn in - so no link is rotated against
another and `<axis>` is the freedom's direction in machine coordinates verbatim. Meshes are written
in millimetres and referenced with `scale="0.001 0.001 0.001"`, which is how both formats say "these
coordinates are millimetres"; a part used more than once is written once. MJCF states the start
value as the joint's `ref`, so MuJoCo's zero is the pose the model is drawn in; URDF has no starting
value at all, and that is reported rather than lost.

Contacts are OFF by default (`contype="0" conaffinity="0"`): MuJoCo collides the convex hull of a
mesh, which fills every bore and makes a carriage sit on its rail, and clearances over the cycle are
`skills/sim/scripts/cycle_clearance.py`'s job (python-fcl, the real meshes). `--collide` turns them
on, with a `<contact><exclude>` for the two links of every joint.
"""
import argparse
import collections
import importlib.util
import json
import math
import os
import re
import sys
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import words_i18n as W  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

#: millimetres per metre: both formats are SI, the model is millimetres, and this is the only place
#: the two meet (plus the mesh `scale`, which is its reciprocal)
MM_PER_M = 1000.0

#: g/cm^3 by material, for a part that states no `card.density`. Matched as a lower-case substring
#: of the part's `material`, longest key first, so "stainless sheet" beats "steel".
DENSITY = W.density()   # words_i18n.DENSITY, every language

#: what a part of unknown material is weighed at, and reported as
DEFAULT_DENSITY = 7.85

#: what a printed part of unknown material is weighed at: PLA, which is what the printers run
PRINTED_DENSITY = 1.24

#: the joint kinds the model states and what each format makes of them
REVOLUTE, PRISMATIC, CYLINDRICAL, BALL = "revolute", "prismatic", "cylindrical", "ball"
FROM_MODEL = {"revolute": REVOLUTE, "linear": PRISMATIC, "cylindrical": CYLINDRICAL, "ball": BALL}

#: what URDF has a type for. Everything else is refused naming the joint (see the module docstring)
URDF_KINDS = (REVOLUTE, PRISMATIC)

#: `<limit effort velocity>` where nothing states them: URDF requires both, and these limit nothing
#: a passive mechanism does while staying finite, as PartCAD's own defaults do
DEFAULT_EFFORT_N = 1000.0
DEFAULT_VELOCITY = 10.0

#: how far a bound URDF cannot leave open is written. URDF's `lower`/`upper` default to nought and
#: are required, so "open on one side" has no spelling but a bound too far away to reach
OPEN_BOUND = 1.0e6

#: a position actuator reaches its whole force at this error - metres for a move, radians for a turn
#: (0.2 mm, or 0.011 deg). Its whole effect is the steady-state error of the drive, force / kp: 0.04 mm
#: of rod at a 1072 N pneumatic drive. Tighter than this and the damping a `dampratio` of one
#: asks for slows the stroke down more than the error is worth.
SERVO_ERROR = 0.0002


class Refused(Exception):
    """What the model says cannot be written in the format asked for, and why."""


# --------------------------------------------------------------------------------------- the model

class Link:
    """One rigid body: its parts, where its frame sits, and the joint it hangs off."""

    def __init__(self, name, anchor=(0.0, 0.0, 0.0), parent=None):
        self.name, self.anchor, self.parent = name, tuple(float(v) for v in anchor), parent
        self.places = []                 # (part dict, placement dict)
        self.joint = None
        self.physics = None              # {"mass", "com", "tensor"} in machine coordinates, or None


class Joint:
    """One declared freedom: what it is, where its line is, and how far it goes.

    `axis` and `anchor` are in machine coordinates (every link frame is the machine frame
    translated, so they need no re-expressing); `lower`/`upper`/`start` are degrees for a turn and
    millimetres for a move, as the model states them, and None where a bound is open.
    """

    def __init__(self, name, kind, parent, child, axis, anchor, lower=None, upper=None, start=0.0,
                 label="", second=None):
        self.name, self.kind, self.parent, self.child = name, kind, parent, child
        self.axis, self.anchor = _unit(axis), tuple(float(v) for v in anchor)
        self.lower, self.upper, self.start = lower, upper, float(start)
        self.label = label or name       # how the model spells it, for a refusal to name
        #: a cylindrical pair's second freedom, (lower, upper, start) of the MOVE along the same line
        self.second = second or (None, None, 0.0)

    @property
    def turning(self):
        return self.kind in (REVOLUTE, CYLINDRICAL, BALL)


class Closure:
    """Two points of two links that stay together: a loop the tree cannot hold."""

    def __init__(self, a, b, point):
        self.a, self.b, self.point = a, b, tuple(float(v) for v in point)


class Drive:
    """What drives a joint: the force it can apply and the travel it is commanded over."""

    def __init__(self, joint, kind, force, lower, upper, note="", stated=""):
        self.joint, self.kind, self.force = joint, kind, float(force)
        self.lower, self.upper, self.note = float(lower), float(upper), note
        self.stated = stated             # which key the model gave the force under, for its unit


class Robot:
    def __init__(self, name):
        self.name = name
        self.links = collections.OrderedDict()
        self.joints = []
        self.closures = []
        self.drives = []
        self.notes = []                  # every default and every freedom a format cannot state

    def say(self, line):
        self.notes.append(line)

    def link(self, name, anchor=(0.0, 0.0, 0.0), parent=None):
        if name not in self.links:
            self.links[name] = Link(name, anchor, parent)
        return self.links[name]

    def order(self):
        """The links parents before children, so a nested format can be written in one pass."""
        out, seen = [], set()
        waiting = list(self.links.values())
        while waiting:
            ready = [lk for lk in waiting if lk.parent is None or lk.parent in seen]
            if not ready:
                sys.exit("[export_robot] links %s hold each other up; one of them has to hang off the base"
                         % ", ".join(sorted(lk.name for lk in waiting)))
            for lk in ready:
                out.append(lk)
                seen.add(lk.name)
            waiting = [lk for lk in waiting if lk.name not in seen]
        return out


# ------------------------------------------------------------------------------- reading the model

def _root_holds(root, places, model):
    """The project root is three directories up from the model; say so when it is not.

    A `step` is stated relative to the project root, so a `--model` anywhere else makes every part
    file resolve to nothing - and the first thing to say so would otherwise be an OCCT import error
    about a path nobody chose.
    """
    for part, _ in places:
        if part.get("step"):
            if not os.path.exists(os.path.join(root, part["step"])):
                sys.exit("[export_robot] %s says part %r is at %r, and there is no such file under %s. "
                         "A model is read at <project>/build/model/parts.json, and the part STEPs are "
                         "stated relative to that <project>"
                         % (model, part["id"], part["step"], root))
            return


def _read_json(path, what):
    """A file this script is handed, with the script's own message when it is not there."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.loads(fh.read())
    except OSError as problem:
        sys.exit("[export_robot] %s (%s): %s" % (what, path, problem.strerror or problem))
    except ValueError as problem:
        sys.exit("[export_robot] %s (%s) is not JSON: %s" % (what, path, problem))


def read_model(path):
    """`build/model/parts.json` as (part, placement) pairs, in the order the nodes returned them."""
    data = _read_json(path, "the built model")
    places = []
    for part in data.get("parts") or []:
        for placement in part.get("placements") or []:
            places.append((part, placement))
    if not places:
        sys.exit("[export_robot] %s holds no placed part; build the model first "
                 "(cad_incremental.py --model assemble.py)" % path)
    return places


def links_from_dockings(robot, places):
    """The links and joints of a machine whose parts are docked onto each other.

    A `rigid` docking welds the child into its parent's link; any other docking is a joint. A
    placement nothing docks is welded to the world: the machine is bolted down, and that is the
    base link. Returns False when the model states no docking at all.
    """
    edges = {pl["name"]: pl["joint"] for _, pl in places if pl.get("joint")}
    if not edges:
        return False
    by_name = {pl["name"]: (part, pl) for part, pl in places}
    head = {}                            # placement -> the placement whose link it is in

    def find(name):
        seen = []
        while True:
            edge = edges.get(name)
            if edge is None or edge["type"] != "rigid":
                for step in seen:
                    head[step] = name
                return name
            if name in seen:
                sys.exit("[export_robot] placements %s are welded in a circle" % ", ".join(seen))
            seen.append(name)
            name = edge["to"]

    for _, pl in places:
        head[pl["name"]] = find(pl["name"])

    base = robot.link("base")
    for _, pl in places:                 # the link of every head first, so a child finds its parent
        name = pl["name"]
        if head[name] != name or name not in edges:
            continue
        edge = edges[name]
        parent_part, parent_pl = by_name[edge["to"]]
        kind = FROM_MODEL.get(edge["type"])
        if kind is None:
            sys.exit("[export_robot] placement %r is docked by a %r joint, which is none of %s"
                     % (name, edge["type"], ", ".join(sorted(FROM_MODEL))))
        axis, anchor = _docked_axis(parent_part, parent_pl, edge, name)
        # The parent link is the head of the parent placement's rigid group - unless that head is
        # itself docked by nothing, in which case the group is welded to the world and is the base.
        above = head[edge["to"]]
        lk = robot.link(name, anchor=anchor, parent=above if above in edges else "base")
        spec = (parent_part.get("joints") or {}).get(edge["at"]) or {}
        # A cylindrical pair is two freedoms on one line, and the model states each separately:
        # `angular_range` is the turn's and `range` the move's, the docked value being (position,
        # angle). Every other kind states its one travel as `range`.
        turning = _range_of(spec, "angular_range" if kind == CYLINDRICAL else "range")
        second = _range_of(spec, "range") + (_start_of(edge, kind, 0),) if kind == CYLINDRICAL else None
        lo, hi = turning
        lk.joint = Joint(name, kind, lk.parent, name, axis, anchor, lo, hi, _start_of(edge, kind),
                         label="%s %r of %r <- %r" % (kind, edge["at"], edge["to"], edge["by"]),
                         second=second)
        robot.joints.append(lk.joint)
    for part, pl in places:
        owner = head[pl["name"]]
        (robot.links[owner] if owner in robot.links else base).places.append((part, pl))
    return True


def _docked_axis(parent_part, parent_pl, edge, child):
    """The docked joint's line, in machine coordinates: the parent's joint moved by its placement."""
    spec = (parent_part.get("joints") or {}).get(edge["at"])
    if spec is None:
        sys.exit("[export_robot] placement %r is docked to %r joint %r, and that part states no such "
                 "joint" % (child, edge["to"], edge["at"]))
    rot, off = _matrix(parent_pl["loc"])
    if spec.get("axis"):
        origin, direction = spec["axis"]["origin"], spec["axis"]["direction"]
        return _apply_rot(rot, direction), _apply(rot, off, origin)
    own, point = _matrix(spec["loc"])    # a ball joint: its own frame, Z out of the part
    return _apply_rot(rot, [own[0][2], own[1][2], own[2][2]]), _apply(rot, off, point)


def _range_of(spec, key="range"):
    """One travel the model states, as (lower, upper), each None where that bound is open.

    build123d's own open bound is an infinity, and a ball states three pairs rather than one: both
    come back as no bound, so nothing is invented from a number that is not a travel.
    """
    rng = spec.get(key)
    if not rng or len(rng) != 2 or any(isinstance(v, (list, tuple)) for v in rng):
        return None, None
    lo, hi = float(rng[0]), float(rng[1])
    return (None if not math.isfinite(lo) else lo), (None if not math.isfinite(hi) else hi)


def _start_of(edge, kind, index=None):
    """What the docking set on the moving joint: a cylindrical pair states (position, angle)."""
    value = edge.get("value")
    if isinstance(value, (list, tuple)):
        want = (1 if kind == CYLINDRICAL else 0) if index is None else index
        if len(value) <= want:
            raise Refused("a %s docking states the value %r, and a cylindrical pair's is (position, "
                          "angle): the model and this export disagree about what it is" % (kind, value))
        return float(value[want])
    return float(value or 0.0)


def load_mechanism(path):
    """The machine's `mechanism.py`, with the framework's own `linkage.py` ahead of the machine's."""
    was = list(sys.path)
    sys.path.insert(0, os.path.dirname(os.path.abspath(path)))
    sys.path.insert(0, HERE)
    try:
        spec = importlib.util.spec_from_file_location("machine_mechanism", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = was
    for name in ("mechanism", "poses"):
        if not hasattr(module, name):
            sys.exit("[export_robot] %s states no %r; see the contract in cycle_from_mechanism.py" % (path, name))
    return module


def links_from_mechanism(robot, places, module, group_of):
    """The links and joints of a machine whose kinematics is stated as a mechanism.

    Every joint of the loop is a link, placed where the solved start pose puts it; the parts of each
    come from `--groups` (placement -> viewer group) through the mechanism's `LINKS` (group -> joint).
    A group in no `LINKS` entry is welded to the base, and said so - which is also how a group whose
    own freedom the loop does not state (a drive of its own, on its own timing) names the link it
    rides on: `LINKS` puts it on that joint, and its own stroke is not written.
    """
    mech = module.mechanism()
    solved = _solve(module)
    if len(solved) < 2:
        sys.exit("[export_robot] poses() gave %d frames; a travel needs at least two" % len(solved))
    poses = mech.poses(solved[0])
    lo = {name: min(frame[name] for frame in solved) for name in solved[0]}
    hi = {name: max(frame[name] for frame in solved) for name in solved[0]}

    links = dict(getattr(module, "LINKS", None) or {})
    if not links:
        sys.exit("[export_robot] %s states no LINKS: which joint of the mechanism each group of parts "
                 "is, e.g. LINKS = {'lever': 'lever', 'frame': 'bearing'}. The mechanism says how the "
                 "machine moves and LINKS says what moves with it" % module.__file__)
    for group, joint in sorted(links.items()):
        if joint not in mech.joints:
            sys.exit("[export_robot] LINKS puts group %r on joint %r, which is no joint of the mechanism "
                     "(there is: %s)" % (group, joint, ", ".join(sorted(mech.joints))))
    for group in sorted(set(group_of.values()) - set(links)):
        robot.say("group %r is in no LINKS entry: its parts are welded to the base, where a part nothing "
                  "moves belongs - name it in LINKS if it moves" % group)

    base = robot.link("base")
    for name, joint in mech.joints.items():
        parent = "base" if joint.parent == "ground" else joint.parent
        robot.link(name, anchor=_flat(poses[name]), parent=parent)
    # Every joint carries the travel the SOLVE gives it over the drive's own stroke. Only the driven
    # joint's is an end stop the machine really has (the cylinder bottoming, the motor's turn); the
    # rest is what the loop makes of it, and is written all the same, because it is also what keeps an
    # engine inside the assembly the machine is built in. A point-to-point equality holds both
    # assemblies of a loop, and a hard enough push - a step of the whole drive force - snaps a
    # four-bar through its fold into the mirror one, where every frame after it is a perfectly good
    # answer to the wrong mechanism. That is the failure `linkage.py` refuses to hand back, and
    # out here the travel is what refuses it: checked on a pneumatic four-bar machine, which flips without
    # these ranges and does not with them. A joint pressed against one is a sim outside what the
    # model states, and the log says which travel each range came from.
    driven = {entry.get("joint") for entry in (getattr(module, "DRIVES", None) or ())}
    for name, joint in mech.joints.items():
        lk = robot.links[name]
        if joint.kind == "turn":
            axis = (0.0, -1.0, 0.0)      # the solver measures from +X toward +Z, which is a turn about -Y
        else:
            angle = math.radians(poses[joint.parent][2])
            axis = (math.cos(angle), 0.0, math.sin(angle))
        lk.joint = Joint(name, REVOLUTE if joint.kind == "turn" else PRISMATIC, lk.parent, name,
                         axis, lk.anchor, lo[name], hi[name], solved[0][name])
        robot.joints.append(lk.joint)
        robot.say("joint %r: %.3f..%.3f %s, %s" % (name, lo[name], hi[name],
                  "deg" if joint.kind == "turn" else "mm",
                  "the drive's own travel" if name in driven else
                  "what the loop makes of the drive's travel - not a stop the machine has"))

    for part, pl in places:
        target = links.get(group_of.get(pl["name"]))
        (robot.links[target] if target in robot.links else base).places.append((part, pl))

    loops_and_drives(robot, module, {name: name for name in mech.joints}, mech, solved)
    return True


def loops_and_drives(robot, module, link_of, mech=None, solved=None, compare=False):
    """The mechanism's closures and its drive, onto links the caller has already made.

    `link_of` maps a joint of the mechanism to the link that moves with it, which is the one thing
    the two inputs spell differently: links made FROM the mechanism are its joints, and links made
    from the docking graph are named after the placement that heads them. A joint the mechanism
    states and no link answers for is refused by name - the loop is the whole point of reading this
    file, and a machine written without its declared loop is a machine nobody built.

    `compare` is for the one case where the same machine is stated TWICE - the dockings built the
    tree and the mechanism says what the tree cannot hold. Then the two have to be the same machine,
    and nothing else here would notice if they were not: a closure point comes from the mechanism's
    solve and the link it is written against comes from the dockings, so a mechanism whose `at` is
    somewhere else writes an equality onto a point of neither part, with the loop perfectly closed
    around it and nothing to say so (MuJoCo derives the second body's anchor from the first at
    `qpos0`, so the residual is nought either way). See `_same_machine`.
    """
    mech = mech or module.mechanism()
    solved = list(solved if solved is not None else _solve(module))
    poses = mech.poses(solved[0])
    travel = {name: (min(f[name] for f in solved), max(f[name] for f in solved)) for name in solved[0]}
    if compare:
        _same_machine(robot, mech, poses, link_of, getattr(module, "__file__", "the mechanism"))

    def link(side, what):
        if side == "ground":
            return "base"
        name = link_of.get(side)
        if name is None or name not in robot.links:
            raise Refused(
                "%s %r of the mechanism, and no link of this machine moves with it (there is: %s). "
                "Name it in LINKS, or dock the part it is" % (what, side, ", ".join(robot.links)))
        return name

    for meet in mech.meets:
        if meet.distance is not None:
            raise Refused(
                "the loop is closed by a distance of %.3f mm between %r and %r, which is a link with no "
                "body of its own: no body, no equality. Declare it as a body of the mechanism "
                "(turn(...) hung on one end) and meet its far end" % (meet.distance, meet.a[0], meet.b[0]))
        a, b = meet.a[0], meet.b[0]
        first, second = link(a, "the loop meets"), link(b, "the loop meets")
        point = _point(poses[a] if a != "ground" else (0.0, 0.0, 0.0), meet.a[1], meet.a[2])
        robot.closures.append(Closure(first, second, (point[0], 0.0, point[1])))

    drives = list(getattr(module, "DRIVES", None) or ())
    if len(drives) > 1:
        raise Refused(
            "%d drives are declared (%s). One drive per machine is written for now, and writing some of "
            "them would be a model of a machine nobody has: say which one drives this export, or wait "
            "for several" % (len(drives), ", ".join(str(d.get("joint")) for d in drives)))
    for entry in drives:
        joint = entry.get("joint")
        if joint not in travel:
            raise Refused("DRIVES names joint %r, which is no joint of the mechanism (there is: %s)"
                          % (joint, ", ".join(sorted(mech.joints))))
        robot.drives.append(Drive(link(joint, "DRIVES drives"), entry.get("kind") or "motor",
                                  _drive_force(entry, robot), travel[joint][0], travel[joint][1],
                                  entry.get("note") or "", _stated_force(entry)))


#: how far apart two statements of one pin may be, in millimetres, before they are two machines
AGREE_MM = 0.01


def _same_machine(robot, mech, poses, link_of, path):
    """The mechanism and the dockings have to describe one machine; what cannot be compared is left.

    Only the geometry is compared, because only the geometry means the same thing on both sides: a
    turn's pivot is a point, a slide's line is a line. The VALUES do not - build123d measures an
    angle from a joint's own `angle_reference` and the mechanism measures it from +X - so a start
    that differs is not a disagreement and is not read as one.
    """
    for name, joint in mech.joints.items():
        link = robot.links.get(link_of.get(name))
        if link is None or link.joint is None:
            continue
        here, axis, anchor = _flat(poses[name]), link.joint.axis, link.joint.anchor
        if joint.kind == "turn":
            gap = math.sqrt(sum((here[i] - anchor[i]) ** 2 for i in range(3)))
            if gap > AGREE_MM:
                raise Refused(
                    "%s turns %r about (%.3f, %.3f) of the side view, and the docking that places it "
                    "puts its axis at (%.3f, %.3f, %.3f) - %.3f mm apart. These are two machines, and "
                    "an equality written between them would sit on a point of neither part. Make the "
                    "two statements agree" % (path, name, here[0], here[2], anchor[0], anchor[1],
                                              anchor[2], gap))
            if abs(abs(axis[1]) - 1.0) > 1e-6:
                raise Refused(
                    "%s states %r as a turn of its side view, which is a turn about Y, and the docking "
                    "that places it turns about (%s). Make the two statements agree"
                    % (path, name, ", ".join(_num(v) for v in axis)))
            continue
        angle = math.radians(poses[joint.parent][2])
        along = (math.cos(angle), 0.0, math.sin(angle))
        if abs(abs(sum(along[i] * axis[i] for i in range(3))) - 1.0) > 1e-6:
            raise Refused(
                "%s slides %r along (%s) and the docking that places it slides along (%s). Make the "
                "two statements agree"
                % (path, name, ", ".join(_num(v) for v in along), ", ".join(_num(v) for v in axis)))
        off = [here[i] - anchor[i] for i in range(3)]
        along_axis = sum(off[i] * axis[i] for i in range(3))
        gap = math.sqrt(max(0.0, sum(v * v for v in off) - along_axis * along_axis))
        if gap > AGREE_MM:
            raise Refused(
                "%s slides %r through (%.3f, %.3f) of the side view, which is %.3f mm off the line the "
                "docking slides it along. These are two machines. Make the two statements agree"
                % (path, name, here[0], here[2], gap))


def _solve(module):
    """`poses()` of a machine's mechanism, with what the solver refuses said as this script says it."""
    import linkage
    try:
        return list(module.poses())
    except (linkage.WillNotClose, linkage.MirrorBranch, linkage.NotSquare, linkage.BadDeclaration) as no:
        raise Refused("%s cannot be solved: %s" % (getattr(module, "__file__", "the mechanism"), no))


def _force_fits(robot, drive, joint):
    """What a drive states has to be the quantity the joint takes: a torque turns, a force moves."""
    turns = {"force_n": False, "bore_mm": False, "torque_nm": True}.get(drive.stated)
    if turns is not None and turns != joint.turning:
        raise Refused(
            "the drive of %r states %s and that joint %s, so the number would go into a slot that "
            "means the other quantity (N.m for a turn, N for a move). State %s instead"
            % (joint.name, drive.stated, "turns" if joint.turning else "moves",
               "torque_nm" if joint.turning else "force_n, or bore_mm with pressure_bar"))


def _stated_force(entry):
    """Which key a drive states its force under, so the unit can be held against the joint's kind."""
    for key in ("force_n", "torque_nm", "bore_mm"):
        if entry.get(key):
            return key
    return ""


def _drive_force(entry, robot):
    """What the drive can push: a cylinder's bore and pressure, or a stated force or torque."""
    for key in ("force_n", "torque_nm"):
        if entry.get(key):
            return float(entry[key])
    bore, pressure = entry.get("bore_mm"), entry.get("pressure_bar")
    if bore and pressure:
        rod = float(entry.get("rod_mm") or 0.0)
        # The pulling side is the annulus the rod leaves of the bore, so it is the weaker of the two
        # and the one a stroke has to be sized by: what pushes out will always push harder.
        pull = math.pi / 4.0 * (float(bore) ** 2 - rod ** 2)
        force = pull * float(pressure) * 0.1                     # bar -> N/mm^2
        robot.say("drive %r: %.0f N from bore %g mm (rod %g) at %g bar, the pulling side"
                  % (entry.get("joint"), force, float(bore), rod, float(pressure)))
        return force
    robot.say("drive %r states no force: %g N is written, which limits nothing it does"
              % (entry.get("joint"), DEFAULT_EFFORT_N))
    return DEFAULT_EFFORT_N


# --------------------------------------------------------------------------------- mass and inertia

def density_of(part):
    """(g/cm^3, where it came from) for one part: what it states, the catalogue, then the default.

    `None` is "nothing stated" and a stated nought is a part that weighs nothing, which is a thing a
    part may be (a drawn void, a placeholder) and not a missing value.
    """
    stated = (part.get("card") or {}).get("density")
    if stated is not None:
        return float(stated), "stated"
    # Whole words, longest key first: a plain substring match finds `pla` inside `plate` and weighs a
    # steel plate as printed plastic, while the longest-first order is what makes `stainless` beat
    # `steel`. A key of five letters or more also matches a word that starts with it, which is what
    # catches `alumin`ium (and the word stems of other languages) without listing every ending.
    words = [w for w in re.split(r"[^0-9a-z\u0400-\u04ff]+", (part.get("material") or "").lower()) if w]
    for key in sorted(DENSITY, key=len, reverse=True):
        spelled = key.split()
        if any(words[i:i + len(spelled)] == spelled for i in range(len(words))) or \
                (len(key) >= 5 and any(w.startswith(key) for w in words)):
            return DENSITY[key], "catalogue %s (%s)" % (_num(DENSITY[key]), key)
    if part.get("process") == "print":
        return PRINTED_DENSITY, "printed %s (no material stated)" % _num(PRINTED_DENSITY)
    return DEFAULT_DENSITY, "default"


def weigh(robot, places, root):
    """Each part's solid measured once, and every link's mass, centre and inertia from them.

    The solid is measured at a density of one - volume, centroid and the inertia tensor about the
    centroid, which is length^5 - and the density scales both, because a homogeneous solid balances
    at its centroid whatever it is made of. A part with no solid adds nothing, exactly as it adds
    nothing to an integral.
    """
    import build123d as B                                       # only this half needs a CAD kernel
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    measured, shapes, sources, boneless = {}, {}, {}, []
    for part, _ in places:
        key = part.get("key")
        if not key or key in measured:
            continue
        step = part.get("step")
        if not step:
            # A stored mesh (vendor triangles, no B-rep) is drawn and not weighed: there is no solid
            # to integrate, and a mesh that is not closed has no volume to speak of. It still goes
            # into the file, because a part the model has and the description has not is a machine
            # nobody built.
            measured[key] = None
            boneless.append(part["id"])
            if part.get("mesh"):
                shapes[key] = _read_json(os.path.join(root, part["mesh"]),
                                         "the stored mesh of part %r" % part["id"])
            continue
        shape = B.import_step(os.path.join(root, step))
        shapes[key] = shape
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape.wrapped, props)
        centre = props.CentreOfMass()
        # OCCT's MatrixOfInertia() is already about the centroid, whatever point the GProp_GProps was
        # built at (measured: a 10 mm cube 1000 mm from the origin gives Iyy = 16666.667 either way),
        # and it already holds the tensor form - the products negated - which is the convention URDF,
        # SDFormat and MJCF all state. So one integration is the whole of it.
        matrix = props.MatrixOfInertia()
        measured[key] = {
            "volume": props.Mass(),
            "centroid": (centre.X(), centre.Y(), centre.Z()),
            "unit": [[matrix.Value(r, c) for c in (1, 2, 3)] for r in (1, 2, 3)],
        }

    for link in robot.links.values():
        pieces = []
        for part, pl in link.places:
            solid = measured.get(part.get("key"))
            if solid is None:
                continue
            density, source = density_of(part)
            sources.setdefault(source, []).append(part["id"])
            rot, off = _matrix(pl["loc"])
            # kg from mm^3 at g/cm^3, and kg*m^2 from mm^5 at g/cm^3
            mass = solid["volume"] * density * 1.0e-6
            tensor = _rotated([[v * density * 1.0e-12 for v in row] for row in solid["unit"]], rot)
            pieces.append((mass, _apply(rot, off, solid["centroid"]), tensor))
        link.physics = _combined(pieces)

    for source in sorted(sources):
        named = sorted(set(sources[source]))
        robot.say("density %s: %s%s"
                  % (source if source != "default" else
                     "DEFAULT %s g/cm3, because the part states no material the catalogue holds"
                     % _num(DEFAULT_DENSITY),
                     ", ".join(named[:12]), "" if len(named) <= 12 else " and %d more" % (len(named) - 12)))
    if boneless:
        robot.say("%d part(s) have no solid in the model and add no mass (a stored mesh is drawn and not "
                  "weighed; a row with no geometry is not drawn either): %s"
                  % (len(boneless), ", ".join(sorted(set(boneless)))))
    for link in robot.links.values():
        if link.physics is None and link.places:
            robot.say("link %r is written with no inertia, and MJCF is told not to make one up "
                      "(inertiafromgeom=false): nothing in it could be weighed" % link.name)
    return shapes


def _combined(pieces):
    """Masses, centres and tensors of parts held rigidly together, about their common centre."""
    pieces = [p for p in pieces if p[0] > 0.0]
    if not pieces:
        return None
    total = sum(mass for mass, _, _ in pieces)
    centre = [sum(mass * com[i] for mass, com, _ in pieces) / total for i in range(3)]
    tensor = [[0.0] * 3 for _ in range(3)]
    for mass, com, own in pieces:
        offset = [(com[i] - centre[i]) / MM_PER_M for i in range(3)]
        square = sum(v * v for v in offset)
        for r in range(3):
            for c in range(3):
                tensor[r][c] += own[r][c] + mass * ((square if r == c else 0.0) - offset[r] * offset[c])
    return {"mass": total, "com": tuple(centre), "tensor": tensor}


# ------------------------------------------------------------------------------------------ writers

def write_meshes(shapes, out):
    """One binary STL per part, in millimetres. MuJoCo reads binary STL only."""
    import build123d as B
    directory = os.path.join(out, "meshes")
    os.makedirs(directory, exist_ok=True)
    written = []
    for key, shape in shapes.items():
        path = os.path.join(directory, key + ".stl")
        if not os.path.exists(path):     # the key is a hash of what made the part: a whole file that
            tmp = "%s.tmp-%d" % (path, os.getpid())        # is there is the right one
            if isinstance(shape, dict):
                _write_stl(shape, tmp)
            else:
                B.export_stl(shape, tmp, ascii_format=False)
            os.replace(tmp, path)
        written.append(path)
    return written


def _write_stl(mesh, path):
    """A stored mesh part's triangles as a binary STL: {"v": [x, y, z, ...], "f": [i, j, k, ...]}."""
    import struct
    v = [float(x) for x in mesh["v"]]
    f = [int(i) for i in mesh["f"]]
    if len(v) % 3 or len(f) % 3:
        sys.exit("[export_robot] %s holds %d coordinates and %d indices; both are triples"
                 % (path, len(v), len(f)))
    with open(path, "wb") as fh:
        fh.write(b"export_robot".ljust(80, b"\0"))
        fh.write(struct.pack("<I", len(f) // 3))
        for t in range(0, len(f), 3):
            points = [v[3 * f[t + k]:3 * f[t + k] + 3] for k in range(3)]
            a = [points[1][i] - points[0][i] for i in range(3)]
            b = [points[2][i] - points[0][i] for i in range(3)]
            cross = (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
            length = math.sqrt(sum(v * v for v in cross))
            # A degenerate triangle has no normal; STL allows (0, 0, 0) and every reader works the
            # normals out from the winding anyway, so it is written as it is rather than refused.
            fh.write(struct.pack("<3f", *((v / length for v in cross) if length else (0.0, 0.0, 0.0))))
            for point in points:
                fh.write(struct.pack("<3f", *point))
            fh.write(struct.pack("<H", 0))


def write_urdf(robot, path, collide=False):
    """A link per rigid body and a joint per pair, with every closed loop broken and said so.

    A URDF has no starting value, so the choice is between writing the machine where the CAD draws it
    and letting a joint's value mean what the model says it means. This writes the second: every
    joint's zero is the model's zero, so driving the URDF to the values the model states puts every
    part where parts.json puts it - which is what reads a URDF (ROS's tools, a robot's drivers), since
    they take a joint's zero to be where its hardware reads zero. The price is that the URDF's own
    default pose, every joint at nought, is not the pose the CAD is drawn in, and that is reported.
    """
    check_urdf(robot)
    for closure in robot.closures:
        robot.say("URDF: the loop that holds %r and %r together at (%.1f, %.1f, %.1f) mm is BROKEN - "
                  "URDF is one tree with one root and cannot state a loop. The chain is written, the "
                  "closure is not: read this URDF for the kinematics, the MJCF for the mechanism"
                  % (closure.a, closure.b, *closure.point))
    effort = {d.joint: d.force for d in robot.drives}
    took_defaults = [j.name for j in robot.joints
                     if j.name not in effort and _urdf_type(j) != "continuous"]
    if took_defaults:
        robot.say("URDF: <limit> is required and the model states no force or speed for %s: effort %s "
                  "(N.m for a turn, N for a move) and velocity %s (rad/s, m/s) are written, which limit "
                  "nothing a passive mechanism does"
                  % (", ".join(took_defaults), _num(DEFAULT_EFFORT_N), _num(DEFAULT_VELOCITY)))
    robot.say("URDF: every joint's zero is the MODEL's zero, so the URDF driven to the model's own values "
              "stands where the CAD does - and its own default pose, every joint at nought, does not. "
              "URDF has no starting value to carry them: %s"
              % ", ".join("%s=%g" % (j.name, j.start) for j in robot.joints))

    root = ET.Element("robot", {"name": robot.name})
    for note in robot.notes:             # whoever opens this file months from now reads what stderr said
        if note.startswith("URDF: "):
            root.append(ET.Comment(" " + note[len("URDF: "):] + " "))
    for link in robot.order():
        element = ET.SubElement(root, "link", {"name": link.name})
        if link.physics:
            inertial = ET.SubElement(element, "inertial")
            ET.SubElement(inertial, "origin", {"xyz": _xyz(link.physics["com"], link.anchor), "rpy": "0 0 0"})
            ET.SubElement(inertial, "mass", {"value": _num(link.physics["mass"])})
            ET.SubElement(inertial, "inertia", _inertia(link.physics["tensor"]))
        for part, pl in link.places:
            if not _drawn(part):
                continue
            rot, off = _matrix(pl["loc"])
            origin = {"xyz": _xyz(off, link.anchor), "rpy": _rpy(rot)}
            for tag in ("visual",) + (("collision",) if collide else ()):
                node = ET.SubElement(element, tag, {"name": pl["name"]})
                ET.SubElement(node, "origin", dict(origin))
                geometry = ET.SubElement(node, "geometry")
                ET.SubElement(geometry, "mesh", {"filename": "meshes/%s.stl" % part["key"],
                                                 "scale": "%s %s %s" % (("0.001",) * 3)})
    for joint in robot.joints:
        element = ET.SubElement(root, "joint", {"name": joint.name,
                                                "type": _urdf_type(joint)})
        # The child link's frame when the joint is at ZERO, while every part inside it is written at
        # the pose the CAD draws: so the joint origin undoes the start - a turn of -start about the
        # axis, or a slide of -start along it - and the joint at `start` is the CAD pose again.
        anchor = _xyz(joint.anchor, robot.links[joint.parent].anchor)
        if joint.kind == PRISMATIC:
            back = [joint.anchor[i] - joint.axis[i] * joint.start for i in range(3)]
            ET.SubElement(element, "origin", {"xyz": _xyz(back, robot.links[joint.parent].anchor),
                                              "rpy": "0 0 0"})
        else:
            ET.SubElement(element, "origin", {"xyz": anchor,
                                              "rpy": _rpy(_about(joint.axis, -joint.start))})
        ET.SubElement(element, "parent", {"link": joint.parent})
        ET.SubElement(element, "child", {"link": joint.child})
        ET.SubElement(element, "axis", {"xyz": "%s %s %s" % tuple(_num(v) for v in joint.axis)})
        if _urdf_type(joint) != "continuous":
            scale = math.radians(1.0) if joint.turning else 1.0 / MM_PER_M
            lower = (joint.lower if joint.lower is not None else -OPEN_BOUND) * scale
            upper = (joint.upper if joint.upper is not None else OPEN_BOUND) * scale
            ET.SubElement(element, "limit", {"lower": _num(lower), "upper": _num(upper),
                                             "effort": _num(effort.get(joint.name, DEFAULT_EFFORT_N)),
                                             "velocity": _num(DEFAULT_VELOCITY)})
            if joint.lower is None or joint.upper is None:
                robot.say("URDF: joint %r is open on one side, which URDF's <limit> cannot leave open: "
                          "written %g %s away"
                          % (joint.name, OPEN_BOUND, "deg" if joint.turning else "mm"))
    _write(root, path, '<?xml version="1.0"?>')
    return path


def driven_joint(robot, drive):
    """The one joint a drive turns, or a refusal: an actuator drives one freedom and guesses none.

    A cylindrical pair goes out as two MJCF joints under two names, and which of them a motor really
    drives is not something the declaration says - so it is not something this picks.
    """
    joint = next((j for j in robot.joints if j.name == drive.joint), None)
    if joint is None:
        raise Refused("the drive is on %r, and no joint of this machine is written under that name "
                      "(there is: %s)" % (drive.joint, ", ".join(j.name for j in robot.joints)))
    if joint.kind not in (REVOLUTE, PRISMATIC):
        raise Refused("the drive is on %r, which is %s - two freedoms, written as two MJCF joints "
                      "(%s.turn and %s.move). An actuator drives ONE of them: declare the freedom the "
                      "motor really drives" % (drive.joint, joint.kind, drive.joint, drive.joint))
    return joint


def check_mjcf(robot):
    """What MuJoCo would have to invent, before a single file is written.

    A link that moves and weighs nothing is not a body: with `inertiafromgeom` off MuJoCo refuses it
    ("mass and inertia of moving bodies must be larger than mjMINVAL") and with it on MuJoCo makes
    the mass up. Neither is an answer, so this says which parts have no solid and what would fix it.
    """
    for link in robot.links.values():
        if link.joint is None or link.physics is not None:
            continue
        parts = sorted({part["id"] for part, _ in link.places}) or ["(no part at all)"]
        raise Refused(
            "link %r moves on joint %r and nothing in it can be weighed (%s). A moving body needs a "
            "mass: give those parts a solid, or state one in `card.density` with a measured mass. "
            "MuJoCo would otherwise weigh the convex hull of their meshes at the density of water"
            % (link.name, link.joint.name, ", ".join(parts)))


def check_urdf(robot):
    """What URDF has no type for, before a single file is written: a refusal writes nothing."""
    for joint in robot.joints:
        if joint.kind not in URDF_KINDS:
            raise Refused(
                "joint %r (%s) is %s, and URDF has no type for it. Simulate the machine in MuJoCo, "
                "which writes every type; or model the body this joint stands for and join it by "
                "single-axis joints. A chain of links of a mass nobody measured is not written"
                % (joint.name, joint.label, joint.kind))


def _mjcf_joint(robot, element, joint, anchor):
    """The MJCF joints of one declared freedom, in the order MuJoCo applies them.

    A body may hold several joints and applies them in the order they are written, so a cylindrical
    pair is a hinge and a slide in the one body - which is why MJCF needs no link nobody built. The
    start value is the joint's `ref`: MuJoCo's zero is then the pose the CAD is drawn in, and a
    reading of the joint is the value the model gives it. A ball has no `ref`, so it is written where
    it starts and MuJoCo's identity IS that start, which is reported.
    """
    pos = _xyz(joint.anchor, anchor)
    axis = "%s %s %s" % tuple(_num(v) for v in joint.axis)
    if joint.kind == BALL:
        ET.SubElement(element, "joint", {"name": joint.name, "type": "ball", "pos": pos})
        robot.say("MJCF: joint %r is a ball and has no 'ref': it is written where the model stands, so "
                  "MuJoCo's identity is that pose, not the model's zero" % joint.name)
        robot.say("MJCF: joint %r states a limit per turn (%s) and an MJCF ball is bounded by ONE angle "
                  "from its zero: no range is written rather than the wrong cone. Declare the three "
                  "turns as separate joints to bound them"
                  % (joint.name, joint.label))
        return
    if joint.kind == CYLINDRICAL:
        # A turn and a move on one line, uncoupled: MuJoCo applies a body's joints in the order they
        # are written, which is why MJCF needs no link nobody built here. The model states both
        # travels (cad_incremental._joints_json: `angular_range` is the turn's and `range` the
        # move's) and the docking states both values, so each of the two gets its own.
        pairs = [("hinge", joint.start, joint.lower, joint.upper),
                 ("slide", joint.second[2], joint.second[0], joint.second[1])]
        robot.say("MJCF: joint %r is cylindrical: a hinge and a slide in one body, each with its own "
                  "travel and its own start" % joint.name)
    elif joint.kind == PRISMATIC:
        pairs = [("slide", joint.start, joint.lower, joint.upper)]
    else:
        pairs = [("hinge", joint.start, joint.lower, joint.upper)]
    for kind, start, lower, upper in pairs:
        scale = 1.0 if kind == "hinge" else 1.0 / MM_PER_M      # degrees as written; a move in metres
        name = joint.name if len(pairs) == 1 else "%s.%s" % (joint.name, "turn" if kind == "hinge" else "move")
        attributes = {"name": name, "type": kind, "pos": pos, "axis": axis, "ref": _num(start * scale)}
        if lower is not None or upper is not None:
            low = (lower if lower is not None else -OPEN_BOUND) * scale
            high = (upper if upper is not None else OPEN_BOUND) * scale
            attributes["range"] = "%s %s" % (_num(low), _num(high))
            if lower is None or upper is None:
                robot.say("MJCF: joint %r is open on one side, and an MJCF range has two ends: written "
                          "%g %s away" % (name, OPEN_BOUND, "deg" if kind == "hinge" else "mm"))
        ET.SubElement(element, "joint", attributes)


def _urdf_type(joint):
    if joint.kind == PRISMATIC:
        return "prismatic"
    return "continuous" if joint.lower is None and joint.upper is None else "revolute"


def write_mjcf(robot, path, collide=False):
    """The bodies nested as the joints hang them, every loop held by an `<equality><connect>`."""
    root = ET.Element("mujoco", {"model": robot.name})
    # `inertiafromgeom` defaults to "auto", which means MuJoCo weighs a body with no <inertial> by
    # the CONVEX HULL of its meshes at its own default density - water's, 1000 kg/m3. Measured on
    # mujoco 3.17: a 200 x 200 x 300 mm vendor cover with no solid came out at 12 kg. That is the
    # dummy mass PartCAD removed, in another spelling, so it is turned off: a body with no inertial
    # then weighs nothing, and a MOVING one is refused by name before MuJoCo says it less clearly.
    ET.SubElement(root, "compiler", {"angle": "degree", "meshdir": "meshes", "autolimits": "true",
                                     "inertiafromgeom": "false"})
    # elliptic friction and noslip: MuJoCo's default soft contacts let a body creep under a steady
    # tangential load, which answers a friction claim with the solver (PartCAD, sim-mujoco #8).
    ET.SubElement(root, "option", {"timestep": "0.0005", "integrator": "implicitfast",
                                   "cone": "elliptic", "impratio": "10", "noslip_iterations": "3"})
    default = ET.SubElement(root, "default")
    ET.SubElement(default, "geom", {"contype": "1" if collide else "0",
                                    "conaffinity": "1" if collide else "0"})
    # An equality is held like a contact - softly - so a loop written at MuJoCo's defaults sags.
    ET.SubElement(default, "equality", {"solref": "0.002 1", "solimp": "0.95 0.99 0.001"})
    assets = ET.SubElement(root, "asset")
    # A mesh is named after the part and read from the file its cache key names, so the file is
    # written once however often the part is placed and the model still reads as the model.
    for name, key in sorted({part["id"]: part["key"] for link in robot.links.values()
                             for part, _ in link.places if _drawn(part)}.items()):
        ET.SubElement(assets, "mesh", {"name": name, "file": key + ".stl",
                                       "scale": "%s %s %s" % (("0.001",) * 3)})
    world = ET.SubElement(root, "worldbody")
    ET.SubElement(world, "light", {"pos": "0 0 4", "dir": "0 0 -1", "directional": "true"})

    elements = {}
    for link in robot.order():
        if link.parent is None:
            # Welded to the world: the machine is bolted down, and in MJCF that is a body with no
            # joint - its geoms could as well be the worldbody's, but a body keeps its name readable.
            element = ET.SubElement(world, "body", {"name": link.name, "pos": _xyz(link.anchor, (0, 0, 0))})
        else:
            element = ET.SubElement(elements[link.parent], "body",
                                    {"name": link.name,
                                     "pos": _xyz(link.anchor, robot.links[link.parent].anchor)})
        elements[link.name] = element
        if link.physics:
            ET.SubElement(element, "inertial", {"pos": _xyz(link.physics["com"], link.anchor),
                                                "mass": _num(link.physics["mass"]),
                                                "fullinertia": _fullinertia(link.physics["tensor"])})
        if link.joint is not None:
            _mjcf_joint(robot, element, link.joint, link.anchor)
        for part, pl in link.places:
            if not _drawn(part):
                continue
            rot, off = _matrix(pl["loc"])
            attributes = {"name": pl["name"], "type": "mesh", "mesh": part["id"],
                          "pos": _xyz(off, link.anchor), "quat": _quat_text(rot)}
            if part.get("color"):
                attributes["rgba"] = _rgba(part["color"])
            ET.SubElement(element, "geom", attributes)

    if robot.closures:
        equality = ET.SubElement(root, "equality")
        for index, closure in enumerate(robot.closures, 1):
            ET.SubElement(equality, "connect",
                          {"name": "loop%d" % index, "body1": closure.a, "body2": closure.b,
                           "anchor": _xyz(closure.point, robot.links[closure.a].anchor)})
    if robot.drives:
        actuator = ET.SubElement(root, "actuator")
        for drive in robot.drives:
            joint = driven_joint(robot, drive)
            # <compiler angle="degree"> converts a joint's ref and range and does NOT convert an
            # actuator's ctrlrange, so a hinge's ctrlrange goes out in radians whatever the compiler
            # is told. Checked against mujoco 3.17: range="0 90" compiled to 1.5708 rad, ctrlrange
            # to 90. A move's is metres either way.
            scale = math.radians(1.0) if joint.turning else 1.0 / MM_PER_M
            low, high = sorted((drive.lower * scale, drive.upper * scale))
            ET.SubElement(actuator, "position",
                          {"name": drive.joint, "joint": drive.joint,
                           "ctrlrange": "%s %s" % (_num(low), _num(high)),
                           "kp": _num(drive.force / SERVO_ERROR), "dampratio": "1",
                           "forcerange": "%s %s" % (_num(-drive.force), _num(drive.force))})
            robot.say("MJCF: drive %r is a position servo over %s (%s), %.0f %s available%s; "
                      "a cylinder at a steady pressure is a force, and this is what makes the stroke "
                      "repeatable in a check" % (drive.joint, "%g..%g" % (low, high),
                                                 "rad" if joint.turning else "m", drive.force,
                                                 "N.m" if joint.turning else "N",
                                                 " - " + drive.note if drive.note else ""))
    if collide:
        contact = ET.SubElement(root, "contact")
        for joint in robot.joints:
            ET.SubElement(contact, "exclude", {"body1": joint.parent, "body2": joint.child})
        for closure in robot.closures:
            ET.SubElement(contact, "exclude", {"body1": closure.a, "body2": closure.b})
    else:
        robot.say("MJCF: contacts are off (contype=0). MuJoCo collides the convex hull of a mesh, "
                  "which fills every bore; clearances over the cycle are cycle_clearance.py's. "
                  "--collide turns them on, with the two links of every joint excluded")
    _write(root, path, '<?xml version="1.0"?>')
    return path


# ------------------------------------------------------------------------------------------- numbers

def _drawn(part):
    """Whether the model has geometry for this part: a solid, or a stored mesh. A row has neither."""
    return bool(part.get("step") or part.get("mesh"))


def _unit(vector):
    length = math.sqrt(sum(float(v) * float(v) for v in vector))
    if length == 0.0:
        raise Refused("a joint of this model states an axis of no length (0, 0, 0); every freedom turns "
                      "about or moves along a direction, and a Z axis is not one to invent for it")
    return tuple(float(v) / length for v in vector)


def _matrix(loc):
    """A row-major 4x4 placement as (3x3 rotation, translation)."""
    values = [float(v) for v in loc]
    rot = [[values[0], values[1], values[2]], [values[4], values[5], values[6]],
           [values[8], values[9], values[10]]]
    return rot, (values[3], values[7], values[11])


def _apply_rot(rot, vector):
    return tuple(sum(rot[r][c] * float(vector[c]) for c in range(3)) for r in range(3))


def _apply(rot, off, point):
    moved = _apply_rot(rot, point)
    return tuple(moved[i] + off[i] for i in range(3))


def _about(axis, degrees):
    """The rotation matrix of `degrees` about a unit `axis` (Rodrigues)."""
    angle = math.radians(float(degrees))
    cos, sin = math.cos(angle), math.sin(angle)
    x, y, z = axis
    return [[cos + x * x * (1 - cos), x * y * (1 - cos) - z * sin, x * z * (1 - cos) + y * sin],
            [y * x * (1 - cos) + z * sin, cos + y * y * (1 - cos), y * z * (1 - cos) - x * sin],
            [z * x * (1 - cos) - y * sin, z * y * (1 - cos) + x * sin, cos + z * z * (1 - cos)]]


def _rotated(tensor, rot):
    """R T R^T."""
    first = [[sum(rot[r][k] * tensor[k][c] for k in range(3)) for c in range(3)] for r in range(3)]
    return [[sum(first[r][k] * rot[c][k] for k in range(3)) for c in range(3)] for r in range(3)]


def _flat(pose):
    """A planar pose (x, z, angle) as a machine point: the mechanism is the CAD's side view."""
    return (pose[0], 0.0, pose[1])


def _point(pose, local, turned=0.0):
    """A point of a planar body, in machine (x, z) - linkage.py's own arithmetic."""
    angle = math.radians(pose[2] + turned)
    cos, sin = math.cos(angle), math.sin(angle)
    return (pose[0] + cos * local[0] - sin * local[1], pose[1] + sin * local[0] + cos * local[1])


def _num(value):
    """A number written so that reading it back gives the same float.

    Rounded to a readable number of digits a radian is no longer the degree it came from, and a
    description of a machine is read by a solver, not by eye: `repr` is the shortest string that
    round-trips, which is what PartCAD writes too.
    """
    value = float(value)
    return "0" if value == 0.0 else repr(value)


def _xyz(point, origin):
    return " ".join(_num((point[i] - origin[i]) / MM_PER_M) for i in range(3))


def _inertia(tensor):
    return {"ixx": _num(tensor[0][0]), "ixy": _num(tensor[0][1]), "ixz": _num(tensor[0][2]),
            "iyy": _num(tensor[1][1]), "iyz": _num(tensor[1][2]), "izz": _num(tensor[2][2])}


def _fullinertia(tensor):
    """MJCF orders `fullinertia` as ixx iyy izz ixy ixz iyz."""
    return " ".join(_num(v) for v in (tensor[0][0], tensor[1][1], tensor[2][2],
                                      tensor[0][1], tensor[0][2], tensor[1][2]))


def _quat(rot):
    """The (w, x, y, z) of a rotation matrix, by the branch that stays away from a zero divisor."""
    trace = rot[0][0] + rot[1][1] + rot[2][2]
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        return ((0.25 * s), (rot[2][1] - rot[1][2]) / s, (rot[0][2] - rot[2][0]) / s,
                (rot[1][0] - rot[0][1]) / s)
    biggest = max(range(3), key=lambda i: rot[i][i])
    a, b = (biggest + 1) % 3, (biggest + 2) % 3
    s = math.sqrt(1.0 + rot[biggest][biggest] - rot[a][a] - rot[b][b]) * 2.0
    out = [(rot[b][a] - rot[a][b]) / s, 0.0, 0.0, 0.0]
    out[1 + biggest], out[1 + a], out[1 + b] = 0.25 * s, (rot[a][biggest] + rot[biggest][a]) / s, \
        (rot[b][biggest] + rot[biggest][b]) / s
    return tuple(out)


def _quat_text(rot):
    return " ".join(_num(v) for v in _quat(rot))


def _rpy(rot):
    """URDF's fixed-axis roll-pitch-yaw (radians): the matrix is Rz(yaw) Ry(pitch) Rx(roll).

    At the poles pitch = +-90 the roll and the yaw are not separable; the roll is put at zero and
    the whole turn carried by the yaw, which is the choice tf2 and urdfdom make.
    """
    sin_pitch = max(-1.0, min(1.0, -rot[2][0]))
    pitch = math.asin(sin_pitch)
    if abs(sin_pitch) > 1.0 - 1e-12:
        # At the pole only (yaw -+ roll) is determined. The first row is then
        # [0, sin(roll - yaw), cos(roll - yaw)] at pitch +90 and its negative at -90, so with the
        # roll pinned at nought the yaw is read off it. A part lying along one axis - a rod turned
        # 90 deg about Y - is exactly this case, so it is not a corner nobody reaches.
        yaw = math.atan2(-rot[0][1], rot[0][2] if sin_pitch > 0.0 else -rot[0][2])
        return "%s %s %s" % (_num(0.0), _num(pitch), _num(yaw))
    roll = math.atan2(rot[2][1], rot[2][2])
    yaw = math.atan2(rot[1][0], rot[0][0])
    return "%s %s %s" % (_num(roll), _num(pitch), _num(yaw))


def _rgba(colour):
    text = str(colour).lstrip("#")
    if len(text) != 6:
        return "0.7 0.7 0.7 1"
    return " ".join(_num(int(text[i:i + 2], 16) / 255.0) for i in (0, 2, 4)) + " 1"


def _write(root, path, header):
    """Written beside and replaced, as cad_incremental writes parts.json: a short write is no export."""
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    _indent(root)
    body = ET.tostring(root, encoding="unicode")
    tmp = "%s.tmp-%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(header + "\n" + body + "\n")
    os.replace(tmp, path)


def _indent(element, level=0):
    pad = "\n" + "  " * level
    if len(element):
        element.text = element.text or pad + "  "
        for child in element:
            _indent(child, level + 1)
            child.tail = pad + "  "
        element[-1].tail = pad
    if level and not (element.tail or "").strip():
        element.tail = pad


# ---------------------------------------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True, help="build/model/parts.json of the built machine")
    ap.add_argument("--out", required=True, help="directory for the description and its meshes")
    ap.add_argument("--name", required=True, help="the robot's name in both files")
    ap.add_argument("--mechanism", help="mechanics/kinematics/mechanism.py: the closed loops and the drive")
    ap.add_argument("--groups", help="build/motion_groups.json: placement -> viewer group")
    ap.add_argument("--format", choices=("urdf", "mjcf", "both"), default="both")
    ap.add_argument("--collide", action="store_true", help="write contacts (off by default; see the docstring)")
    args = ap.parse_args(argv)

    # `step` is stated relative to the project root ("build/model/step/<key>.step"), and the model
    # itself lives at "<root>/build/model/parts.json": three directories up is that root.
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(args.model))))
    places = read_model(args.model)
    _root_holds(root, places, args.model)
    robot = Robot(args.name)

    try:
        if links_from_dockings(robot, places):
            if args.mechanism:
                # The tree is the dockings', and the mechanism is read for what a tree cannot hold: a
                # mechanism joint is then the link whose head placement is named after it, since that
                # is what a converted machine's own two declarations have in common.
                module = load_mechanism(args.mechanism)
                loops_and_drives(robot, module, {name: name for name in module.mechanism().joints},
                                 compare=True)
                robot.say("the tree comes from the model's own dockings; %s was read for the "
                          "loops and the drive, and gave %d loop(s) and %d drive(s)"
                          % (args.mechanism, len(robot.closures), len(robot.drives)))
        elif args.mechanism:
            # The part's own `motion_group` first (SKILL.md), then the file a machine's own code
            # writes, which is the later statement of the two and wins where both name a placement.
            group_of = {pl["name"]: part["motion_group"] for part, pl in places if part.get("motion_group")}
            if args.groups:
                group_of.update(_read_json(args.groups, "the group map"))
            if not group_of:
                sys.exit("[export_robot] no part states a `motion_group` and no --groups was given, so "
                         "nothing says which parts move with which joint of the mechanism")
            links_from_mechanism(robot, places, load_mechanism(args.mechanism), group_of)
        else:
            # Nothing in the model states a freedom, so the machine is one rigid body - which is what
            # a format writes for a shape with no joints, and not an error. It is said, though: a
            # machine whose parts were meant to move and are not docked comes out like this.
            base = robot.link("base")
            for part, pl in places:
                base.places.append((part, pl))
            robot.say("%s states no docking and no --mechanism was given: nothing in the "
                      "model says a part moves, so the machine is written as ONE link. Dock the parts, "
                      "or state the mechanism, if it has freedoms" % args.model)

        for drive in robot.drives:   # before a single file: a refusal writes nothing
            _force_fits(robot, drive, driven_joint(robot, drive))
        if args.format in ("urdf", "both"):
            check_urdf(robot)
        shapes = weigh(robot, places, root)
        if args.format in ("mjcf", "both"):
            check_mjcf(robot)            # needs the masses, and still before a single file
        os.makedirs(args.out, exist_ok=True)
        write_meshes(shapes, args.out)
        written = []
        if args.format in ("mjcf", "both"):
            written.append(write_mjcf(robot, os.path.join(args.out, args.name + ".xml"), args.collide))
        if args.format in ("urdf", "both"):
            written.append(write_urdf(robot, os.path.join(args.out, args.name + ".urdf"), args.collide))
    except Refused as refusal:
        sys.exit("[export_robot] refused: %s" % refusal)

    for link in robot.order():
        mass = "%.3f kg" % link.physics["mass"] if link.physics else "no mass"
        print("[export_robot] link %-14s %-12s %3d part(s)%s"
              % (link.name, mass, len(link.places),
                 "" if link.joint is None else "  %s %s" % (link.joint.kind, link.joint.name)),
              file=sys.stderr)
    for note in robot.notes:
        print("[export_robot] %s" % note, file=sys.stderr)
    print("[export_robot] %d links, %d joints, %d loop(s), %d drive(s) -> %s"
          % (len(robot.links), len(robot.joints), len(robot.closures), len(robot.drives),
             ", ".join(written)), file=sys.stderr)
    return robot


if __name__ == "__main__":
    main()
