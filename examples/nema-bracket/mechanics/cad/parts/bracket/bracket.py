"""Part `bracket` of node `bracket`: the printed L that holds a NEMA 17 on a 2020 profile. Base on the profile's top slot face
(two M5 slots along the profile: belt tension), motor plate standing at the profile's +Y edge, two gussets beside the motor.
Own coordinates: base bottom centre at the origin, profile along X, Z up, the motor plate's outer face at Y = PROFILE/2."""
import params as P_
from build123d import Align, Box, Cylinder, Location, Plane, Polyline, Pos, RigidJoint, Rot, SlotCenterToCenter, extrude, make_face
from geom import P

MIN = (Align.CENTER, Align.CENTER, Align.MIN)


def build():
    w, t, half = P_.PROFILE_SIZE_MM, P_.WALL_MM, P_.PROFILE_SIZE_MM / 2
    base = Box(P_.BASE_LEN_MM, w, t, align=MIN)
    plate = Pos(0, half - t / 2, 0) * Box(P_.PLATE_W_MM, t, P_.PLATE_H_MM, align=MIN)
    shape = base + plate
    for sx in (-1, 1):                                       # gussets in the YZ plane, outside the motor body
        tri = make_face(Polyline((half - t, t), (-half, t), (half - t, t + P_.GUSSET_H_MM), close=True))
        g = Plane.YZ.offset(sx * (P_.PLATE_W_MM / 2 - P_.GUSSET_T_MM / 2) - P_.GUSSET_T_MM / 2) * extrude(tri, P_.GUSSET_T_MM)
        shape += g
    for sx in (-1, 1):                                       # M5 slots along X
        shape -= Pos(sx * P_.SLOT_PITCH_MM / 2, 0, 0) * extrude(
            SlotCenterToCenter(P_.SLOT_TRAVEL_MM, P_.M5_CLEAR_D_MM), 3 * t, both=True)
    shape -= Pos(0, half, P_.MOTOR_AXIS_Z_MM) * Rot(90, 0, 0) * Cylinder(P_.PILOT_CLEAR_D_MM / 2, 4 * t)
    p = P_.MOTOR_HOLE_PITCH_MM / 2
    for dx in (-p, p):
        for dz in (-p, p):
            shape -= Pos(dx, half, P_.MOTOR_AXIS_Z_MM + dz) * Rot(90, 0, 0) * Cylinder(P_.M3_CLEAR_D_MM / 2, 4 * t)
    RigidJoint("base", shape, Location())                    # docks on the profile's mount joint (Z up, into the bracket)
    # motor: its flange joint (Z out of the flange along the shaft) onto the plate's inner face, shaft toward +Y
    RigidJoint("motor", shape, Location((0, half - t, P_.MOTOR_AXIS_Z_MM), (1, 0, 0), -90))
    for i, (dx, dz) in enumerate([(-p, -p), (p, -p), (-p, p), (p, p)], 1):   # M3 heads on the outer face, Z out (+Y)
        RigidJoint(f"m3_{i}", shape, Location((dx, half, P_.MOTOR_AXIS_Z_MM + dz), (1, 0, 0), -90))
    for i, sx in enumerate((-1, 1), 1):
        RigidJoint(f"m5_{i}", shape, Location((sx * P_.SLOT_PITCH_MM / 2, 0, t)))
        RigidJoint(f"tnut_{i}", shape, Location((sx * P_.SLOT_PITCH_MM / 2, 0, -P_.TNUT_DEPTH_MM)))
    return P("NEMA 17 bracket for a 2020 profile", shape, "#e07b39", process="print", material="PETG",
             title="NEMA 17 bracket - 2020")
