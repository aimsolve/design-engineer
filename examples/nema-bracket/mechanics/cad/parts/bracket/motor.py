"""Part `motor` of node `bracket`: NEMA 17 stepper, 17HS4401 class — a dimensioned envelope (NEMA ICS 16 flange) until the
maker's STEP is in mechanics/cad/vendor/. Own coordinates: flange face at Z = 0, shaft along +Z, body along -Z."""
import params as P_
from build123d import Align, Box, Cylinder, Location, Pos, RigidJoint, chamfer, Axis
from geom import P


def build():
    s, L = P_.MOTOR_SQ_MM, P_.MOTOR_LEN_MM
    body = Box(s, s, L, align=(Align.CENTER, Align.CENTER, Align.MAX))
    body = chamfer(body.edges().filter_by(Axis.Z), 3.0)
    p = P_.MOTOR_HOLE_PITCH_MM / 2
    for dx in (-p, p):
        for dy in (-p, p):
            body -= Pos(dx, dy, 0) * Cylinder(1.25, 9.0, align=(Align.CENTER, Align.CENTER, Align.MAX))   # M3 tapped, 4.5 deep x2
    shape = body + Cylinder(P_.MOTOR_PILOT_D_MM / 2, P_.MOTOR_PILOT_H_MM, align=(Align.CENTER, Align.CENTER, Align.MIN)) \
        + Cylinder(P_.MOTOR_SHAFT_D_MM / 2, P_.MOTOR_SHAFT_LEN_MM, align=(Align.CENTER, Align.CENTER, Align.MIN))
    RigidJoint("flange", shape, Location())   # flange face, Z along the shaft
    return P("NEMA 17 17HS4401", shape, "#3a3f44", process="bought", kind="motors", code="17HS4401",
             desc="Stepper motor NEMA 17, 42x42x40 mm, 1.7 A, 0.4 N·m, 5 mm shaft", title="NEMA 17 motor", unit="pcs",
             standin_reason="NEMA 17 envelope per NEMA ICS 16 (flange 42.3, M3 holes on 31, pilot 22, shaft 5); "
                            "to be replaced by the maker's STEP")
