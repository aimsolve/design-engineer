"""Bought washer of {machine} (framework template): one variant per (size, std); qty = the number of placements.
Joints: `seat` on the face that lies on the part (origin there, the washer along +Z) and `top` on the other face (Z on, away from the
part), where the screw head or the nut docks."""
from build123d import Align, Cylinder, Location, RigidJoint
from geom import P

WASHERS = {   # std -> size -> (hole d1, outer d2, thickness h)
    "DIN 125": {"M2": (2.2, 5, 0.3), "M2.5": (2.7, 6, 0.5), "M3": (3.2, 7, 0.5), "M4": (4.3, 9, 0.8), "M5": (5.3, 10, 1),
                "M6": (6.4, 12, 1.6), "M8": (8.4, 16, 1.6), "M10": (10.5, 20, 2), "M12": (13, 24, 2.5)},
    "DIN 433": {"M2": (2.2, 4.5, 0.3), "M2.5": (2.7, 5, 0.5), "M3": (3.2, 6, 0.5), "M4": (4.3, 8, 0.5), "M5": (5.3, 9, 1),
                "M6": (6.4, 11, 1.6)},
    "DIN 9021": {"M3": (3.2, 9, 0.8), "M4": (4.3, 12, 1), "M5": (5.3, 15, 1.2), "M6": (6.4, 18, 1.6), "M8": (8.4, 24, 2),
                 "M10": (10.5, 30, 2.5)},
}
BOTTOM = (Align.CENTER, Align.CENTER, Align.MIN)
# purchase-list names; scaffold.py rewrites WORD in the project's language (text_i18n.FASTENERS); unit "pcs"
# is shown in it by derive.py
WORD = "Washer"


def build(size="M5", std="DIN 125"):
    d1, d2, h = WASHERS[std][size.split("x")[0]]
    shape = Cylinder(d2 / 2, h, align=BOTTOM) - Cylinder(d1 / 2, 3 * h, align=BOTTOM).moved(Location((0, 0, -h)))
    RigidJoint("seat", shape, Location())
    RigidJoint("top", shape, Location((0, 0, h)))
    spec = f"{size} {std}"
    return P(f"washer_{spec.replace(' ', '_').replace('.', '_')}", shape, "#b8bcc0", process="bought", kind="fasteners",
             code=spec, desc=f"{WORD} {spec}", title=f"{WORD} {spec}", unit="pcs")
