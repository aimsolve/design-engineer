"""Part `tnut` of node `bracket`: M5 slide-in T-nut for a 6 mm slot (2020 B-type) — a simplified envelope that fits the
catalogue profile's plain groove. Own coordinates: top face (under the slot lip) at Z = 0, body along -Z, joint Z up."""
from build123d import Align, Box, Cylinder, Location, RigidJoint
from geom import P


def build():
    shape = Box(10.0, 5.4, 3.5, align=(Align.CENTER, Align.CENTER, Align.MAX)) - Cylinder(2.5, 10.0)
    RigidJoint("seat", shape, Location())
    return P("tnut_M5_slot6", shape, "#9da3a8", process="bought", kind="fasteners", code="T-nut M5 slot 6",
             desc="T-nut M5 for slot 6 (2020 profile)", title="T-nut M5 slot 6", unit="pcs",
             standin_reason="simplified T-nut (slot 6 envelope); to be replaced by the maker's STEP")
