"""Rebuild entry of {machine}: calls every node (parts/<node>/__init__.py build()); the framework stores one STEP per part variant
(parts/<node>/<part>.py, in the part's own coordinates) and where the nodes placed it, in build/model.
Built by the router after every turn that changed the model (kit.json `site` = sh sim/site.sh: rebuild -> viewer -> public/)
or only the model, from mechanics/cad:       python <fw>/skills/machine-project/scripts/cad_incremental.py --model assemble.py
The cache decides what runs: a part re-runs only when a param it read, its code (and the project modules it imports) or a file it
opened changed; moving a part in its node re-places it without rebuilding it.
Nothing is exported here. Whole-machine STEP, laser/bent drawings, print STL, bought rows and the viewer are derived from
build/model by derive.py (kit.json scopes); `aas_sync.py .` copies params and parts into the AAS.
Add a node: create the folder parts/<node>/ (__init__.py with build() docking its parts, one <part>.py per part). With several
nodes, parts/__init__.py build() is the machine: it builds the nodes, docks each node's list onto the others by joints
(connect_to on one part of it, then place(list)) and returns them all; call only that here (items = parts.build())."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from parts import example

items = example.build()
print(f"{len(items)} placed parts")
