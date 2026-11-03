"""Part `board`: the driver board (stepper driver + MCU) on four standoffs, an 8-pin header J1 near its +X edge.
Own coordinates: the standoffs' feet at Z = 0, centred."""
import params as P_
from build123d import Align, Box, Cylinder, Location, Pos, RigidJoint
from geom import P

MIN = (Align.CENTER, Align.CENTER, Align.MIN)
PINS = ["A+", "A-", "B+", "B-", "LIMIT", "GND", "VIN", "GND"]


def build():
    L, W, s = P_.BOARD_L_MM, P_.BOARD_W_MM, P_.STANDOFF_MM
    top = s + P_.BOARD_T_MM
    shape = Pos(0, 0, s) * Box(L, W, P_.BOARD_T_MM, align=MIN)
    for dx in (-L / 2 + 4, L / 2 - 4):
        for dy in (-W / 2 + 4, W / 2 - 4):
            shape += Pos(dx, dy, 0) * Cylinder(2.5, s, align=MIN)
    hx = P_.HEADER_X_MM - P_.BOARD_X_MM
    shape += Pos(hx, 0, top) * Box(P_.PITCH_MM, P_.PITCH_MM * len(PINS), P_.HEADER_H_MM, align=MIN)
    y0 = -P_.PITCH_MM * (len(PINS) - 1) / 2
    ports = {f"J1.{i + 1}": n for i, n in enumerate(PINS)}
    ports_at = {f"J1.{i + 1}": ((hx, y0 + i * P_.PITCH_MM, top + P_.HEADER_H_MM), (0, 0, 1)) for i in range(len(PINS))}
    RigidJoint("base", shape, Location())
    return P("Driver board", shape, "#2b8a3e", process="bought", kind="electrical", code="DRV-1", title="Driver board",
             desc="Stepper driver board with MCU, 8-pin header J1", unit="pcs", ports=ports, ports_at=ports_at)
