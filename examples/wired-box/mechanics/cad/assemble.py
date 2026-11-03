"""Rebuild entry of wired-box: calls every node; the framework stores one STEP per part variant and where the node placed it,
in build/model (cad_incremental.py --model assemble.py; the router / `de build` runs it through sim/site.sh)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from parts import box

items = box.build()
print(f"{len(items)} placed parts")
