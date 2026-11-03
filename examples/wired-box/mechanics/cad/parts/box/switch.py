"""Part `switch`: micro limit switch on the carriage, its two lugs on top. Own coordinates: bottom centre at the origin."""
import params as P_
from build123d import Align, Box, Location, RigidJoint
from geom import P


def build():
    H, h = P_.SWITCH_H_MM, P_.SWITCH_LUG_MM / 2
    shape = Box(P_.SWITCH_L_MM, P_.SWITCH_W_MM, H, align=(Align.CENTER, Align.CENTER, Align.MIN))
    RigidJoint("base", shape, Location())
    return P("Limit switch", shape, "#1c1c1c", process="bought", kind="electrical", code="KW11-3Z", title="Limit switch",
             desc="Micro switch KW11, NO contact", unit="pcs", ports={"C": "COM", "NO": "NO"},
             ports_at={"C": (-h, 0, H), "NO": (h, 0, H)})
