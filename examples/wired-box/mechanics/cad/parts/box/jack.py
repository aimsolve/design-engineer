"""Part `jack`: panel DC jack 5.5/2.1, its solder lugs inside. Own coordinates: the panel face at Z = 0, the body along +Z."""
import params as P_
from build123d import Align, Cylinder, Location, RigidJoint
from geom import P


def build():
    L, h = P_.JACK_LEN_MM, P_.JACK_LUG_MM / 2
    shape = Cylinder(P_.JACK_D_MM / 2, L, align=(Align.CENTER, Align.CENTER, Align.MIN))
    RigidJoint("base", shape, Location())
    return P("DC jack 5.5/2.1", shape, "#222222", process="bought", kind="electrical", code="DC-022", title="Power jack",
             desc="Panel DC jack 5.5x2.1 mm", unit="pcs", ports={"+": "+12 V", "-": "0 V"},
             ports_at={"+": (0, -h, L), "-": (0, h, L)})
