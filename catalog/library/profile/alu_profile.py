"""Aluminium construction profile (slotted extrusion), cut to length — a BOUGHT part of the manufacturers' catalogue,
sold by the metre: `buy: "length"`, so derive's purchase list adds up the cut lengths with a
margin instead of listing one row per piece.

One variant per (size, length, mounts), each with its own id: `build(30, 400)` is a 400 mm piece of 30x30. The cross-section is simplified —
outer square, central bore, four slots as plain grooves — which is what `standin_reason` says: the envelope, the slot
positions and the end faces are right, the inner web shape is not the maker's. Sizes and slot widths follow the common
B-type series (20 → slot 6, 30 and 40 → slot 8); a project that buys another series adds its own file beside this one.

Ports:
  `end_a`, `end_b`  RigidJoints on the two end faces, Z out of the piece: a corner connector or another profile docks
                    face to face.
  `<side>_<pos>`    one RigidJoint per entry of `mounts` — a point on a slot face, Z out of that face, where a bracket
                    or a plate is bolted: `build(30, 400, mounts=(("x+", 100), ("z+", 200)))`. side is one of
                    x+ x- y+ y- (the four slot faces, the piece running along Z), pos is mm from the `end_a` face.
"""
from build123d import Align, Box, Cylinder, Location, Pos, RigidJoint, Rot
from geom import P
from library.i18n import catalog_i18n as T

# size -> (slot width, central bore diameter, groove depth)
SERIES = {20: (6.0, 4.2, 5.5), 30: (8.0, 6.8, 7.0), 40: (8.0, 6.8, 7.0)}
SIDES = {"x+": (1, 0), "x-": (-1, 0), "y+": (0, 1), "y-": (0, -1)}


def build(size=30, length=300.0, mounts=()):
    """A piece standing on its `end_a` face at the origin, running along +Z."""
    if size not in SERIES:
        raise ValueError(f"alu profile: no row for {size}x{size}; known sizes: {', '.join(f'{s:g}' for s in SERIES)}")
    slot, bore, depth = SERIES[size]
    shape = Box(size, size, length, align=(Align.CENTER, Align.CENTER, Align.MIN)) \
        - Cylinder(bore / 2, 3 * length, align=(Align.CENTER, Align.CENTER, Align.CENTER))
    for sx, sy in SIDES.values():                      # four slots, open to the outside
        shape -= Pos(sx * (size / 2 - depth / 2), sy * (size / 2 - depth / 2), length / 2) * \
            Box(slot if sx else depth, depth if sx else slot, length + 2)
    RigidJoint("end_a", shape, Location((0, 0, 0), (1, 0, 0), 180))
    RigidJoint("end_b", shape, Location((0, 0, length)))
    for side, pos in mounts:
        if side not in SIDES:
            raise ValueError(f"alu profile: mount side {side!r} is not one of {', '.join(SIDES)}")
        if not 0 <= pos <= length:
            raise ValueError(f"alu profile {size:g}x{length:g}: mount at {pos} is off the piece (0..{length})")
        sx, sy = SIDES[side]
        loc = Location((sx * size / 2, sy * size / 2, pos)) * Rot(Y=90 if sx else 0, X=-90 if sy else 0) * \
            Rot(Y=180 if sx < 0 else 0, X=180 if sy < 0 else 0)
        RigidJoint(f"{side}_{pos:g}", shape, loc)
    at = "".join(f"_{side[0]}{'p' if side[1] == '+' else 'm'}{pos:g}" for side, pos in mounts)   # other ports: its own id
    return P(f"profile_{size:g}x{size:g}_{length:g}{at}", shape, "#c9ced3", process="bought", kind="profile",
             code=T.t("profile.code", size=size), desc=T.t("profile.desc", size=size, slot=slot),
             title=T.t("profile.title", size=size), unit="m", buy="length",
             standin_reason=T.t("profile.standin", size=size, slot=slot))
