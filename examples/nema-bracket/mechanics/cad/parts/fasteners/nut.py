"""Bought nut of nema-bracket (framework template): one variant per (size, std); qty = the number of placements.
Joint `seat`: origin on the bearing face, the nut body along +Z. Dock it on a part's joint where the screw comes out, Z out of that
surface (or on a washer's `top`). Simplified shape (no thread)."""
from build123d import Cylinder, Location, RegularPolygon, RigidJoint, extrude
from geom import P

NUTS = {   # std -> size -> (across flats s, height m)
    "DIN 934": {"M2": (4, 1.6), "M2.5": (5, 2), "M3": (5.5, 2.4), "M4": (7, 3.2), "M5": (8, 4), "M6": (10, 5), "M8": (13, 6.5),
                "M10": (16, 8), "M12": (18, 10)},
    "DIN 985": {"M3": (5.5, 4), "M4": (7, 5), "M5": (8, 5), "M6": (10, 6), "M8": (13, 8), "M10": (16, 10), "M12": (18, 12)},
    "ISO 4032": {"M3": (5.5, 2.4), "M4": (7, 3.2), "M5": (8, 4.7), "M6": (10, 5.2), "M8": (13, 6.8), "M10": (16, 8.4),
                 "M12": (18, 10.8), "M16": (24, 14.8)},
    "ISO 4034": {"M5": (8, 5.6), "M6": (10, 6.4), "M8": (13, 7.9), "M10": (16, 9.5), "M12": (18, 12.2), "M16": (24, 15.9)},
}
# purchase-list names; scaffold.py rewrites NAMES and WORD in the project's language (text_i18n.FASTENERS); unit "pcs"
# is shown in it by derive.py
NAMES = {
    "DIN 934": "Hex nut",
    "DIN 985": "Self-locking nut",
    "ISO 4032": "Hex nut",
    "ISO 4034": "Hex nut (product grade C)",
}
WORD = "Nut"


def build(size="M5", std="DIN 934"):
    """size "M5" or a fine thread "M8x1" (the M8 nut body)."""
    s, m = NUTS[std][size.split("x")[0]]
    d = float(size[1:].split("x")[0])
    shape = extrude(RegularPolygon(s / 2 / 0.8660254, 6), m) - Cylinder(d / 2, 3 * m)
    RigidJoint("seat", shape, Location())
    spec = f"{size} {std}"
    return P(f"nut_{spec.replace(' ', '_').replace('.', '_')}", shape, "#9da3a8", process="bought", kind="fasteners",
             code=spec, desc=f"{NAMES[std]} {spec}", title=f"{WORD} {spec}", unit="pcs")
