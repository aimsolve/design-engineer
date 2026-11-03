"""Part `button`: a 6x6 tact switch with a round cap through the case face. Contacts `1` and `2`."""
from build123d import Align, Box, Cylinder, Pos
from geom import P


def build():
    shape = Box(6, 6, 3, align=(Align.CENTER, Align.CENTER, Align.MIN)) + Pos(0, 0, 3) * Cylinder(4, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return P("Tact button 6×6", shape, "#495057", process="bought", kind="electrical", ports={"1": "", "2": ""}, sim={"model": "button"})
