"""Node `example` of {machine}: docks its parts together. One file per part in this folder (parts/example/<part>.py, build() -> one
part in its own coordinates, with joints); shared code of the node goes to parts/example/_<name>.py, a sub-node to a sub-folder.
Every part stands where a joint puts it: `a["shape"].joints["x"].connect_to(b["shape"].joints["y"])` moves b, then place(b) without
a Location. Only the machine's root part gets a Location. Screws, nuts, washers: parts/fasteners/ (dock them into hole joints).
Moving a joint re-places what is docked to it without rebuilding its geometry."""
from build123d import Pos
from geom import place
from . import block


def build():
    """-> the node's placed parts. A sub-node's list: dock one of its parts, then place(sub) moves the whole sub-assembly."""
    base, upper = block.build(), block.build()        # one build() per copy: each copy has its own shape and joints
    base["shape"].joints["top"].connect_to(upper["shape"].joints["bottom"])
    return [place(base, Pos(0, 0, 0)), place(upper)]
