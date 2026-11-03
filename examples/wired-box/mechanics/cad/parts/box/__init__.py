"""Node `box` of wired-box: the enclosure, the board on its standoffs, the motor, the DC jack in the -X wall, the carriage
with its limit switch (the carriage slides along X in the cycle: motion_group "carriage")."""
from build123d import Pos
from geom import place

from . import board, carriage, enclosure, jack, motor, switch


def build():
    box = enclosure.build()
    out = [place(box, Pos(0, 0, 0), name="enclosure")]
    for name, mod in (("board", board), ("motor", motor), ("jack", jack), ("carriage", carriage)):
        p = mod.build()
        box["shape"].joints[name].connect_to(p["shape"].joints["base"])
        out.append(place(p, name=name))
        if name == "carriage":
            sw = switch.build()
            p["shape"].joints["switch"].connect_to(sw["shape"].joints["base"])
            out.append(place(sw, name="switch"))
    return out
