"""Part `block` of node `example` of {machine}: ONE part, built in its own coordinates (contract: skill machine-project
«Code model»). Geometry depends only on params and the arguments of build(): no env vars, clock or random, so
`cad_incremental.py --model` reuses this part while what it read holds; an edit of this file rebuilds this part only.
Identical parts = this one file, placed several times by the node; a size that differs = an argument (one variant per value).
Joints (build123d RigidJoint / RevoluteJoint / LinearJoint ...) mark where it docks and where others dock to it; the node docks
parts with connect_to, so a size change here moves everything docked to this part."""
import params as P_
from build123d import Align, Box, Location, RigidJoint
from geom import P


def build(width=P_.EXAMPLE_WIDTH_MM):
    """-> the part: one solid at the origin, mm. Replace the placeholder.
    process decides the download kit (see geom.P): print -> STL, laser / bent -> DXF + 2-sheet PDF (give thickness, material, code),
    bought -> a row of the purchase list (code, unit, kind), mill / turn / other -> STEP. qty = the number of placements."""
    shape = Box(width, width, width, align=(Align.CENTER, Align.CENTER, Align.MIN))
    RigidJoint("bottom", shape, Location())                  # where it docks onto another part (Z out of that part's face)
    RigidJoint("top", shape, Location((0, 0, width)))        # where the next part docks onto it
    return P("Example part", shape, "#9aa5b1", process="print", material="PETG")
