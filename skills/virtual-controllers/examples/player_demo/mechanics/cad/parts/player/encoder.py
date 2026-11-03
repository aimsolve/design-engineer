"""Part `encoder`: an EC11 rotary encoder with a push switch and a knob. A, B, C (common); SW / SW2 = the push switch."""
from build123d import Align, Box, Cylinder, Pos
from geom import P


def build():
    shape = (Box(12, 12, 7, align=(Align.CENTER, Align.CENTER, Align.MIN)) + Pos(0, 0, 7) * Cylinder(3, 3, align=(Align.CENTER, Align.CENTER, Align.MIN))
             + Pos(0, 0, 10) * Cylinder(7, 8, align=(Align.CENTER, Align.CENTER, Align.MIN)))
    return P("Encoder EC11 with push", shape, "#343a40", process="bought", kind="electrical",
             ports={"A": "", "B": "", "C": "common", "SW": "", "SW2": ""}, sim={"model": "encoder", "detent": 4})
