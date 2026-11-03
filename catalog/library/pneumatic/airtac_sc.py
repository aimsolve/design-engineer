"""Airtac SC series pneumatic cylinder, double acting, standard mounting — a BOUGHT part of the manufacturers' catalogue.
One file, one variant per (bore, stroke, extend, magnet): `build(bore=50, stroke=100)` is SC50x100, the plain
cylinder; the magnetic-piston variant (order code SC50x100S, needed for a magnetic sensor) is `magnet=True`.

Ports, the same whichever way the geometry is made:
  `rear`  RigidJoint on the rear cap face, Z out of the cylinder (what the cylinder is bolted/pinned to by its rear).
  `rod`   LinearJoint along the cylinder axis at the rod end face, range (0, stroke): the load docks here and
          `connect_to(..., position=x)` is the rod out by x mm. Rod thread E is in the purchase row, not in the shape.
A bracket (LB/FA/FB/CA/CB) and a rod-end joint are separate order codes: buy them as their own library parts.

Catalogue dimensions (SC series table, Airtac distributor compendium + airtacs.com product pages, read 26.10.2026:
https://airtacdistr.com/blogs/info/airtac-sc-series-cylinder-compendium-catelog-download,
https://www.airtacs.com/products/airtac-sc-double-acting-standard-cylinder-sc50x100):
A = overall length retracted = A0 + stroke, AC = cap face to cap face = AC0 + stroke, B = square body across flats,
D = rod diameter, E = rod end thread, P = port thread. The rod sticks A0 - AC0 out of the front cap when retracted.
Nothing else of the table is modelled: the cap threads (K at spacing KB) and the port positions are in the fields, not in
the geometry, because the drawing letters are not defined in a source we can check — `standin_reason` says so.

Geometry: the maker's STEP when `VENDOR` names one and the file lies next to this module, otherwise an envelope from the
table above. The ports and the purchase row are identical either way, so a project does not change when the STEP arrives.
Adding a STEP means adding its name to `VENDOR` below: that edit is what rebuilds the parts that import this one.
"""
import os

from build123d import Align, Axis, Box, Cylinder, LinearJoint, Location, Pos, RigidJoint, import_step
from geom import P
from library.i18n import catalog_i18n as T

# bore -> (A0, AC0, B, D, E, P): see the docstring. A0/AC0 are the stroke-free parts of A/AC.
SC = {
    32: (140.0, 93.0, 45.0, 12.0, "M10x1.25", 'G1/8"'),
    40: (142.0, 93.0, 50.0, 16.0, "M12x1.25", 'G1/4"'),
    50: (150.0, 93.0, 62.0, 20.0, "M16x1.5", 'G1/4"'),
    63: (153.0, 96.0, 75.0, 20.0, "M16x1.5", 'G3/8"'),
    80: (182.0, 107.0, 94.0, 25.0, "M20x1.5", 'G3/8"'),
}
VENDOR = {}   # code -> STEP file name beside this module, e.g. {"SC50x100": "airtac_sc50x100.step"}


def code_of(bore, stroke, magnet=False):
    """The order code. S = magnetic piston: a different, dearer part, so it is never the default."""
    return f"SC{bore:g}x{stroke:g}{'S' if magnet else ''}"


def build(bore=50, stroke=100, extend=0.0, magnet=False):
    """One cylinder standing on its rear cap face at the origin, axis +Z. extend: how far the rod is out (0..stroke) in
    this variant — the geometry of a pose, not a joint value; leave it 0 and move what is docked to `rod` instead. A
    pose other than 0 is its own part (the id carries it), because two parts of the model may not share an id."""
    if bore not in SC:
        raise ValueError(f"Airtac SC: no catalogue row for bore {bore}; known bores: {', '.join(f'{b:g}' for b in SC)}")
    if not 0 <= extend <= stroke:
        raise ValueError(f"Airtac SC{bore:g}x{stroke:g}: extend {extend} is outside the stroke (0..{stroke})")
    a0, ac0, b, d, thread, port = SC[bore]
    body_l, rod_out = ac0 + stroke, a0 - ac0
    code = code_of(bore, stroke, magnet)
    step = VENDOR.get(code)
    if step:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), step)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Airtac SC: VENDOR names {step} for {code} but the file is not in the library next to "
                                    f"{os.path.basename(__file__)}")
        shape = import_step(path)
        standin = None
    else:
        shape = Box(b, b, body_l, align=(Align.CENTER, Align.CENTER, Align.MIN)) \
            + Pos(0, 0, body_l) * Cylinder(d / 2, rod_out + extend, align=(Align.CENTER, Align.CENTER, Align.MIN))
        standin = T.t("sc.standin", body=b, length=a0, rod=d)
    top = body_l + rod_out + extend
    RigidJoint("rear", shape, Location((0, 0, 0), (1, 0, 0), 180))      # on the rear face, Z out of the cylinder
    LinearJoint("rod", shape, axis=Axis((0, 0, top), (0, 0, 1)), linear_range=(0, stroke - extend))   # what is left of it
    fields = {"standin_reason": standin} if standin else {}
    pose = f"_out{extend:g}" if extend else ""
    return P(f"cylinder_{code}{pose}", shape, "#8f9aa5", process="bought", kind="pneumatic", code=code,
             desc=T.t("sc.desc", code=code, bore=bore, stroke=stroke, thread=thread, port=port),
             title=T.t("sc.title", code=code), unit="pcs", **fields)
