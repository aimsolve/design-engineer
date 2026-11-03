"""Part `motor`: NEMA 14 stepper envelope, its 4-pin connector on the back face. Own coordinates: the back face at X = 0,
the shaft along +X, the body's bottom at Z = 0."""
import params as P_
from build123d import Align, Box, Cylinder, Location, Pos, RigidJoint, Rot
from geom import P

PINS = ["A+", "A-", "B+", "B-"]


def build():
    s, L = P_.MOTOR_SQ_MM, P_.MOTOR_LEN_MM
    shape = Box(L, s, s, align=(Align.MIN, Align.CENTER, Align.MIN))
    shape += Pos(L, 0, s / 2) * Rot(0, 90, 0) * Cylinder(2.5, 20, align=(Align.CENTER, Align.CENTER, Align.MIN))
    cz = s - 8
    shape += Pos(-5, 0, cz) * Box(5, 12, 7, align=(Align.MIN, Align.CENTER, Align.CENTER))
    ports_at = {p: ((-5, -3.75 + 2.5 * i, cz), (-1, 0, 0)) for i, p in enumerate(PINS)}
    RigidJoint("base", shape, Location())
    return P("NEMA 14 stepper", shape, "#3a3f44", process="bought", kind="motors", code="14HS11", title="Motor",
             desc="Stepper motor NEMA 14, 35x35x28 mm", unit="pcs", ports={p: p for p in PINS}, ports_at=ports_at)
