"""Part `carriage`: printed slider block, moves along X in the cycle. Own coordinates: its bottom centre at the origin."""
import params as P_
from build123d import Align, Box, Location, RigidJoint
from geom import P


def build():
    shape = Box(P_.CARRIAGE_L_MM, P_.CARRIAGE_W_MM, P_.CARRIAGE_H_MM, align=(Align.CENTER, Align.CENTER, Align.MIN))
    RigidJoint("base", shape, Location())
    RigidJoint("switch", shape, Location((0, 0, P_.CARRIAGE_H_MM)))
    return P("carriage", shape, "#e07b39", process="print", material="PETG", title="Carriage", motion_group="carriage")
