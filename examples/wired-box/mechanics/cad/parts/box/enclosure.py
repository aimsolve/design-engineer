"""Part `enclosure`: printed open box (no lid, so the inside shows), a divider across X = 0 with a wire slot.
Own coordinates = the machine's: centred on X and Y, the floor's underside at Z = 0."""
import params as P_
from build123d import Align, Box, Location, Pos, RigidJoint
from geom import P

MIN = (Align.CENTER, Align.CENTER, Align.MIN)


def build():
    L, W, H, t = P_.INNER_L_MM, P_.INNER_W_MM, P_.INNER_H_MM, P_.WALL_MM
    shape = Box(L + 2 * t, W + 2 * t, H + t, align=MIN) - Pos(0, 0, t) * Box(L, W, H + 1, align=MIN)
    shape += Pos(0, 0, t) * Box(t, W, P_.DIVIDER_H_MM, align=MIN)
    shape -= Pos(0, P_.SLOT_Y_MM, P_.SLOT_Z_MM) * Box(4 * t, P_.SLOT_W_MM, P_.SLOT_H_MM)
    shape -= Pos(-L / 2 - t / 2, P_.JACK_Y_MM, P_.JACK_Z_MM) * Box(3 * t, 8.4, 8.4)         # the jack's hole in the -X wall
    f = P_.FLOOR_MM
    RigidJoint("board", shape, Location((P_.BOARD_X_MM, P_.BOARD_Y_MM, f)))
    RigidJoint("motor", shape, Location((P_.MOTOR_X_MM, P_.MOTOR_Y_MM, f)))
    RigidJoint("jack", shape, Location((-L / 2, P_.JACK_Y_MM, P_.JACK_Z_MM), (0, 90, 0)))   # Z into the box (+X)
    RigidJoint("carriage", shape, Location((P_.CARRIAGE_X_MM, P_.CARRIAGE_Y_MM, f)))
    return P("enclosure", shape, "#d9d4ca", process="print", material="PETG", title="Enclosure")
