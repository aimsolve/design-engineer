"""KFL004 two-bolt oval flange bearing unit, 20 mm bore, set-screw locking — a BOUGHT part of the manufacturers'
catalogue. The KFL series is one unit per bore; this file is the 20 mm one (`build()`), so a
project that needs another bore gets its own file beside this one.

Ports, the same whichever way the geometry is made:
  `mount`   RigidJoint at the centre of the flange's back face, Z out of the unit: dock the unit onto the plate it is
            screwed to (the plate's own joint sits on its surface, Z out of it).
  `hole_1`, `hole_2`  RigidJoints on the front face at the two bolt holes, Z out of the unit: a screw's `head` docks
            here (a washer between them), so the M8 fasteners are real parts and are counted by placement.
  `shaft`   RevoluteJoint on the bore axis at the hub's mid-plane: what turns in the bearing docks here.

Catalogue dimensions (vendor data, read 26.10.2026: https://shop.slsbearings.com/sg/nis-kfl-004-oval-flanged-unit.html
— bore 20, bolt-to-bolt 71, overall length 90, 2 bolts, 0.19 kg; https://bearingsdirect.com/kfl004-mini-zinc-flange-
set-screw-block-bearing-shaft-size-20mm/ and the PTI Silver-Series listing — width 55, bolt hole Ø10 for M8, hub width
(through the bore) 23). The flange plate thickness and the hub diameter are published by nobody, so they are arguments
with the envelope's values and `standin_reason` says so: the footprint, the bolt pattern and the shaft axis are right,
the body between them is not the maker's shape.

Geometry: the maker's STEP when `VENDOR` names one and the file lies next to this module, otherwise the envelope below.
Ports and purchase row are identical either way. Adding a STEP means adding its name to `VENDOR`: that edit is what
rebuilds the parts that import this one.
"""
import os

from build123d import Align, Axis, Box, Cylinder, Location, Pos, RevoluteJoint, RigidJoint, import_step
from geom import P
from library.i18n import catalog_i18n as T

BORE_MM = 20.0        # d: the shaft
BOLTS_MM = 71.0       # bolt hole centre distance
HOLE_MM = 10.0        # bolt hole diameter (M8 screws)
LENGTH_MM = 90.0      # overall, across the two bolt ears
WIDTH_MM = 55.0       # overall, across the flange
HUB_MM = 23.0         # axial width through the bore
CODE = "KFL004"
VENDOR = {}           # code -> STEP file name beside this module, e.g. {"KFL004": "kfl004.step"}


def build(flange_mm=12.0, hub_d_mm=38.0):
    """The unit in its own coordinates: the flange's back face on z=0, the hub and the bore along +Z, so the shaft runs
    along the normal of whatever plate the flange is screwed to.
    flange_mm / hub_d_mm are the envelope's unpublished dimensions (see the module docstring)."""
    step = VENDOR.get(CODE)
    if step:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), step)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"KFL004: VENDOR names {step} but the file is not in the library next to "
                                    f"{os.path.basename(__file__)}")
        shape = import_step(path)
        standin = None
    else:
        plate = Box(LENGTH_MM, WIDTH_MM, flange_mm, align=(Align.CENTER, Align.CENTER, Align.MIN))
        for x in (-BOLTS_MM / 2, BOLTS_MM / 2):
            plate -= Pos(x, 0, 0) * Cylinder(HOLE_MM / 2, 3 * flange_mm, align=(Align.CENTER, Align.CENTER, Align.CENTER))
        shape = plate + Pos(0, 0, flange_mm) * Cylinder(hub_d_mm / 2, HUB_MM, align=(Align.CENTER, Align.CENTER, Align.MIN)) \
            - Cylinder(BORE_MM / 2, 3 * (flange_mm + HUB_MM), align=(Align.CENTER, Align.CENTER, Align.CENTER))
        standin = T.t("kfl.standin", bore=BORE_MM, bolts=BOLTS_MM, length=LENGTH_MM, width=WIDTH_MM, hub=HUB_MM)
    RigidJoint("mount", shape, Location((0, 0, 0), (1, 0, 0), 180))     # back face of the flange, Z out of the unit
    for n, x in (("hole_1", -BOLTS_MM / 2), ("hole_2", BOLTS_MM / 2)):
        RigidJoint(n, shape, Location((x, 0, flange_mm)))                # front face at the hole, Z out of the unit
    RevoluteJoint("shaft", shape, axis=Axis((0, 0, flange_mm + HUB_MM / 2), (0, 0, 1)), angular_range=(0, 360))
    fields = {"standin_reason": standin} if standin else {}
    envelope = "" if step or (flange_mm, hub_d_mm) == (12.0, 38.0) else f"_f{flange_mm:g}_h{hub_d_mm:g}"   # a variant, its own id
    return P(f"bearing_{CODE.lower()}{envelope}", shape, "#b9c0c6", process="bought", kind="bearings", code=CODE,
             desc=T.t("kfl.desc", code=CODE, bore=BORE_MM), title=T.t("kfl.title", code=CODE), unit="pcs", **fields)
