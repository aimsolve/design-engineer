"""A closed mechanism stated as joints plus what is closed, solved frame by frame.

A cylinder driving a crank through a rod, a four-bar linkage, a slider on a swinging
lever: a loop the assembly tree cannot hold, because a part placed once cannot also be
held by the part at the other end of the loop. The machine states two things - the joints
the loop is made of, and which pairs of points on them stay together - and names one
joint as the one that is driven. The rest are found by `scipy.optimize.least_squares`,
each frame starting from the one before it, so the loop stays in the assembly it started
in; a frame that would come back as the mirror image is an error, not a frame.

What this is for: one answer, in one place, instead of the same trigonometry written out
in the model, in the cycle and in whatever sized the links - three copies that drift
apart silently, because nothing compares them.

Planar: the CAD side view, X across and Z up, turns about Y, millimetres and degrees.
A pose is (x, z, angle). Nothing here imports build123d or OCP, so it runs in a plain
python with numpy and scipy.
"""
import math

import numpy as np
from scipy.optimize import least_squares

GROUND = "ground"

#: how close a loop has to come to closed, in millimetres, to count as solved
CLOSED_MM = 1e-7
#: a frame whose Jacobian has fallen this far below the stiffest one of the whole sweep is a dead point
DEAD = 1e-4
#: how many times the frame's own motion a deviation from the predicted frame may be
#: before it is a jump to the other assembly rather than the loop's own curvature
JUMP = 8.0
#: a predicted step this many times the driver's own is the Jacobian going slack, not a prediction
WILD = 1e3


class WillNotClose(Exception):
    """The loop cannot be closed at this value of the driving joint."""


class MirrorBranch(Exception):
    """The loop went through a dead point and would come back as its mirror image."""


class NotSquare(Exception):
    """As many unknown joint values as equations is what makes a mechanism; this is not."""


class BadDeclaration(Exception):
    """The loop as written cannot mean anything: a body that does not exist, or a name used twice."""


def _turned(pose, extra=0.0):
    """The cosine and sine of a pose's angle, `extra` degrees further round."""
    radians = math.radians(pose[2] + extra)
    return math.cos(radians), math.sin(radians)


def _compose(pose, local):
    c, s = _turned(pose)
    return (pose[0] + c * local[0] - s * local[1], pose[1] + s * local[0] + c * local[1], pose[2] + local[2])


def _point(pose, local, turned=0.0):
    c, s = _turned(pose, turned)
    return (pose[0] + c * local[0] - s * local[1], pose[1] + s * local[0] + c * local[1])


class _Joint:
    def __init__(self, name, kind, parent, at, offset, start):
        self.name, self.kind, self.parent = name, kind, parent
        self.at, self.offset, self.start = at, offset, start

    def local(self, value):
        if self.kind == "turn":
            return (self.at[0], self.at[1], value)
        return (value, self.offset, 0.0)


class _Meet:
    def __init__(self, a, b, distance):
        self.a, self.b, self.distance = a, b, distance

    @property
    def equations(self):
        return 1 if self.distance is not None else 2

    def residual(self, poses):
        (na, la, ta), (nb, lb, tb) = self.a, self.b
        pa, pb = _point(poses[na], la, ta), _point(poses[nb], lb, tb)
        dx, dz = pa[0] - pb[0], pa[1] - pb[1]
        if self.distance is None:
            return [dx, dz]
        return [math.hypot(dx, dz) - self.distance]


def _side(entry):
    """('body', (x, z)) or ('body', (x, z), turned) -> the three of them."""
    name, local = entry[0], tuple(entry[1])
    return name, local, float(entry[2]) if len(entry) > 2 else 0.0


class Mechanism:
    def __init__(self):
        self.joints = {}
        self.meets = []
        self._values = None

    # --- what the agent writes ---------------------------------------------------------

    def turn(self, name, on, at, start=0.0):
        """`name` turns on `on` about the point `at` of it; `start` is where it begins."""
        self._fresh(name)
        self.joints[name] = _Joint(name, "turn", on, tuple(at), 0.0, float(start))

    def slide(self, name, on, offset=0.0, start=0.0):
        """`name` slides along `on`'s own X, `offset` across it; `start` is where it begins."""
        self._fresh(name)
        self.joints[name] = _Joint(name, "slide", on, (0.0, 0.0), float(offset), float(start))

    def _fresh(self, name):
        """A second joint of the same name would quietly replace the first and lose it."""
        if name in self.joints:
            raise BadDeclaration("'%s' is declared twice; every joint of a loop needs its own name" % name)
        if name == GROUND:
            raise BadDeclaration("'%s' is what everything is placed against; a joint cannot be called that" % GROUND)

    def meet(self, a, b, distance=None):
        """Two points that stay together - or `distance` apart, which is a link with no body.

        Each side is `(body, (x, z))` in that body's own frame, or `(body, (x, z), turned)`
        when the point sits on an arm `turned` degrees off the body's X axis.
        """
        self.meets.append(_Meet(_side(a), _side(b), None if distance is None else float(distance)))

    # --- what it answers ----------------------------------------------------------------

    def poses(self, values):
        poses, pending = {GROUND: (0.0, 0.0, 0.0)}, dict(self.joints)
        while pending:
            placed = [n for n, j in pending.items() if j.parent in poses]
            if not placed:
                self._blame_the_parents(pending)
            for name in placed:
                joint = pending.pop(name)
                poses[name] = _compose(poses[joint.parent], joint.local(values[name]))
        return poses

    def _blame_the_parents(self, pending):
        """Nothing left can be placed: either a body does not exist, or they hold each other up."""
        known = set(self.joints) | {GROUND}
        missing = sorted({j.parent for j in pending.values() if j.parent not in known})
        if missing:
            raise BadDeclaration(
                "'%s' is placed on '%s', and there is no such body (there is: %s)"
                % (sorted(n for n, j in pending.items() if j.parent in missing)[0],
                   missing[0], ", ".join(sorted(known)))
            )
        raise BadDeclaration(
            "these joints hold each other up and nothing holds any of them: %s; one of a loop's "
            "joints is placed on '%s'" % (", ".join(sorted(pending)), GROUND)
        )

    def _residual(self, values):
        poses = self.poses(values)
        out = []
        for meet in self.meets:
            out += meet.residual(poses)
        return out

    def _unknowns(self, driver):
        return [n for n in self.joints if n != driver]

    def _check_square(self, driver):
        if driver not in self.joints:
            raise BadDeclaration(
                "there is no joint called '%s' to drive (there is: %s)"
                % (driver, ", ".join(sorted(self.joints)) or "none")
            )
        unknowns, equations = self._unknowns(driver), sum(m.equations for m in self.meets)
        if len(unknowns) != equations:
            raise NotSquare(
                "%d joint values are unknown (%s) and %d equations close the loops: "
                "state one closure per freedom, or drive another joint"
                % (len(unknowns), ", ".join(sorted(unknowns)), equations)
            )
        return unknowns

    def _solve_frame(self, driver, driven_value, start, unknowns):
        def residual(vector):
            values = dict(zip(unknowns, vector))
            values[driver] = driven_value
            return self._residual(values)

        answer = least_squares(residual, [float(start[n]) for n in unknowns], xtol=1e-14, ftol=1e-14, gtol=1e-14)
        values = dict(zip(unknowns, answer.x))
        values[driver] = driven_value
        gap = float(np.max(np.abs(answer.fun))) if len(answer.fun) else 0.0
        if gap > CLOSED_MM and answer.status == 0:
            raise WillNotClose(
                "at %s = %.4f the solver gave up with the loop %.4f mm open: it ran out of steps rather "
                "than finding the sizes cannot reach" % (driver, driven_value, gap)
            )
        if gap > CLOSED_MM:
            raise WillNotClose(
                "at %s = %.4f the loop stays %.4f mm open: the sizes cannot reach" % (driver, driven_value, gap)
            )
        return values, self._jacobian(driver, driven_value, values, unknowns)

    def _jacobian(self, driver, driven_value, values, unknowns):
        step = 1e-6
        columns = []
        for name in unknowns:
            moved = dict(values)
            moved[name] = values[name] + step
            ahead = np.array(self._residual(moved))
            moved[name] = values[name] - step
            behind = np.array(self._residual(moved))
            columns.append((ahead - behind) / (2 * step))
        return np.array(columns).T

    def assemble(self, driver):
        """Close the loops at the declared starting values, holding `driver` where it is.

        Which joint is held decides nothing about the answer, only which value is taken as
        given; it is named rather than defaulted so that it is not whichever joint happens
        to have been written first.
        """
        unknowns = self._check_square(driver)
        start = {n: j.start for n, j in self.joints.items()}
        values, _ = self._solve_frame(driver, start[driver], start, unknowns)
        self._values = values
        return dict(values)

    def sweep(self, driver, start, end, steps):
        """The driving joint from `start` to `end` in `steps`: one solved frame each.

        Every frame begins at the one before it, so the loop stays in the assembly it was
        in. What that cannot do by itself is notice that it did not: past a dead point the
        loop comes back as its mirror image and every frame after it is a perfectly good
        answer to the wrong mechanism. So the whole sweep is solved first and then read as
        a whole - a frame whose Jacobian has gone slack against the stiffest of the sweep,
        a sign that turns over, a frame that landed far from where the one before it was
        heading - because none of those can be judged from one frame alone. The sweep that
        starts at a dead point is exactly the case a running maximum cannot see.
        """
        if steps < 1:
            raise ValueError("a sweep of %s needs at least one step, not %r" % (driver, steps))
        unknowns = self._check_square(driver)
        guess = dict((n, j.start) for n, j in self.joints.items())
        if self._values is not None:
            guess.update(self._values)
        guess[driver] = start          # only so that `guess` is a whole pose; the driver is never solved for

        solved, before = [], None
        for index in range(steps + 1):
            value = start + (end - start) * index / steps
            predicted = None
            if before is not None:
                predicted = self._predict(before, driver, value - before["value"], unknowns)
                if predicted is not None:
                    for name, guessed in zip(unknowns, predicted):
                        guess[name] = guessed
            try:
                values, jacobian = self._solve_frame(driver, value, guess, unknowns)
            except WillNotClose:
                # Leaving a dead point the solve has no slope to follow - both assemblies are as
                # near - and stalls where it is, which looks like a loop the sizes cannot reach.
                # It is the fold, and saying so sends the author to the drive, not to the links.
                if before is not None and _slack(before["jacobian"]) < DEAD:
                    raise MirrorBranch(self._folded(driver, before["value"])) from None
                raise
            solved.append({"value": value, "values": values, "jacobian": jacobian, "predicted": predicted})
            guess = dict(values)
            before = solved[-1]

        self._check_one_branch(driver, solved, unknowns)
        return [dict(frame["values"]) for frame in solved]

    @staticmethod
    def _folded(driver, value):
        return (
            "at %s = %.4f the loop folds flat: from there it can assemble either way, so no cycle "
            "through this point is the mechanism's own. If '%s' is not the joint the motor turns, drive "
            "that one instead: at a dead centre a slider does not decide which way its crank goes on, "
            "the crank does" % (driver, value, driver)
        )

    def _check_one_branch(self, driver, solved, unknowns):
        """Read the solved sweep as a whole: is every frame the same assembly as the first?"""
        determinants = [float(np.linalg.det(frame["jacobian"])) for frame in solved]
        stiffest = max(abs(d) for d in determinants)
        for frame, determinant in zip(solved, determinants):
            if abs(determinant) < DEAD * stiffest:
                raise MirrorBranch(self._folded(driver, frame["value"]))
        for was, now, before, after in zip(solved, solved[1:], determinants, determinants[1:]):
            if (before > 0) != (after > 0):
                raise MirrorBranch(
                    "between %s = %.4f and %.4f the loop turned itself inside out: it came back "
                    "as the mirror assembly" % (driver, was["value"], now["value"])
                )

        # How far a frame may land from where the one before it was heading. The predicted
        # step is first order, so at a turning point of the output it is nought while the
        # loop is still moving; what is ordinary there is the motion of the frames around
        # it. The first comparison has nothing before it to measure against, and the two
        # tests above already cover a flip there, so it is left to them.
        travelled = 0.0
        for was, now in zip(solved, solved[1:]):
            start_values = np.array([was["values"][n] for n in unknowns])
            moved = np.array([now["values"][n] for n in unknowns]) - start_values
            if now["predicted"] is not None and travelled:
                step = np.array(now["predicted"]) - start_values
                ordinary = max(float(np.linalg.norm(step)), travelled)
                adrift = float(np.linalg.norm(moved - step))
                if adrift > JUMP * ordinary:
                    raise MirrorBranch(
                        "between %s = %.4f and %.4f the loop landed %.3f away from where it was "
                        "heading, %.1f times its own motion: it jumped to the mirror assembly"
                        % (driver, was["value"], now["value"], adrift, adrift / ordinary)
                    )
            travelled = float(np.linalg.norm(moved))

    def _predict(self, before, driver, step, unknowns):
        """Where the frame before this one says the unknowns are heading, linearly."""
        if step == 0.0:
            return None
        ahead = dict(before["values"])
        ahead[driver] = before["values"][driver] + 1e-6
        behind = dict(before["values"])
        behind[driver] = before["values"][driver] - 1e-6
        slope = (np.array(self._residual(ahead)) - np.array(self._residual(behind))) / 2e-6
        try:
            change = np.linalg.solve(before["jacobian"], -slope * step)
        except np.linalg.LinAlgError:
            return None
        # Near a fold the Jacobian is nearly singular and the step it predicts runs off; a guess
        # that wild sends the solve somewhere unrelated, and the fold then reads as a loop whose
        # sizes cannot reach. No prediction is better than that one: the warm start still holds.
        if float(np.linalg.norm(change)) > WILD * abs(step):
            return None
        return [before["values"][n] + d for n, d in zip(unknowns, change)]


def _slack(jacobian):
    """How near to folded one frame is, on its own: |det| over the product of the column lengths.

    That ratio is 1 when the loop's freedoms pull at right angles and 0 when two of them have
    become one (Hadamard's inequality), and it does not care whether a column is in degrees or
    millimetres - which is what a single frame, with no stiffer frame to compare against, needs.
    """
    lengths = np.linalg.norm(jacobian, axis=0)
    if not np.all(lengths):
        return 0.0
    return abs(float(np.linalg.det(jacobian))) / float(np.prod(lengths))
