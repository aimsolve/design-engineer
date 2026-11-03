"""Bought screw of {machine} (framework template): one file, one variant per (size, length, std); qty in the
purchase list = the number of placements. Dock it with its joint `head`: origin on the bearing face under the head (the top of a
countersunk head, flush with the surface), Z from the shaft to the head. A hole joint of a part sits on the surface where the screw
goes in, Z out of the surface: `part["shape"].joints["hole_1"].connect_to(screw["shape"].joints["head"])` puts the shaft into the hole.
A washer under the head: dock the washer on the hole, the screw on the washer's `top`. Simplified shape (no thread, no socket)."""
from build123d import Align, Cylinder, Location, Pos, RegularPolygon, RigidJoint, extrude, Cone
from geom import P

HEADS = {   # std -> size -> (head width d_k or across flats s, head height k); length is from under the head (with the head for csk)
    "DIN 912": {"M2": (3.8, 2), "M2.5": (4.5, 2.5), "M3": (5.5, 3), "M4": (7, 4), "M5": (8.5, 5), "M6": (10, 6), "M8": (13, 8),
                "M10": (16, 10), "M12": (18, 12)},
    "ISO 7380": {"M3": (5.7, 1.65), "M4": (7.6, 2.2), "M5": (9.5, 2.75), "M6": (10.5, 3.3), "M8": (14, 4.4), "M10": (17.5, 5.5)},
    "ISO 10642": {"M2": (4, 1.2), "M2.5": (5, 1.5), "M3": (6.72, 1.86), "M4": (8.96, 2.48), "M5": (11.2, 3.1), "M6": (13.44, 3.72),
                  "M8": (17.92, 4.96), "M10": (22.4, 6.2)},
    "DIN 7985": {"M2": (4, 1.6), "M2.5": (5, 2), "M3": (6, 2.4), "M4": (8, 3.1), "M5": (10, 3.8), "M6": (12, 4.6), "M8": (16, 6)},
    "DIN 7984": {"M2": (3.8, 1.6), "M2.5": (4.5, 2), "M3": (5.5, 2), "M4": (7, 2.8), "M5": (8.5, 3.5), "M6": (10, 4), "M8": (13, 5)},
    "DIN 933": {"M3": (5.5, 2), "M4": (7, 2.8), "M5": (8, 3.5), "M6": (10, 4), "M8": (13, 5.3), "M10": (16, 6.4), "M12": (18, 7.5)},
}
# purchase-list names; scaffold.py rewrites NAMES and WORD in the project's language (text_i18n.FASTENERS); unit "pcs"
# is shown in it by derive.py
NAMES = {
    "DIN 912": "Socket head cap screw",
    "ISO 7380": "Button head screw",
    "ISO 10642": "Countersunk socket screw",
    "DIN 7985": "Pan head Phillips screw",
    "DIN 7984": "Low head socket cap screw",
    "DIN 933": "Hex head bolt",
}
WORD = "Screw"
BOTTOM = (Align.CENTER, Align.CENTER, Align.MIN)
TOP = (Align.CENTER, Align.CENTER, Align.MAX)


def build(size="M5", length=12, std="DIN 912"):
    """size "M5" (a fine thread "M8x1" uses the M8 head), length in mm, std one of HEADS."""
    head = HEADS[std][size.split("x")[0]]
    d = float(size[1:].split("x")[0])
    if std == "ISO 10642":                      # countersunk: the head is inside the length, its top on the surface
        assert length > head[1], f"{size}x{length} {std}: the length must exceed the countersunk head height {head[1]}"
        shape = Cone(d / 2, head[0] / 2, head[1], align=TOP) + Pos(0, 0, -head[1]) * Cylinder(d / 2, length - head[1], align=TOP)
    else:
        top = extrude(RegularPolygon(head[0] / 2 / 0.8660254, 6), head[1]) if std == "DIN 933" else Cylinder(head[0] / 2, head[1], align=BOTTOM)
        shape = top + Cylinder(d / 2, length, align=TOP)
    RigidJoint("head", shape, Location())
    spec = f"{size}x{length:g} {std}"
    return P(f"screw_{spec.replace(' ', '_').replace('.', '_')}", shape, "#9da3a8", process="bought", kind="fasteners",
             code=spec, desc=f"{NAMES[std]} {spec}", title=f"{WORD} {spec}", unit="pcs")
