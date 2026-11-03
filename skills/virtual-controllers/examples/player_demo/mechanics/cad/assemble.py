"""Rebuild entry of player_demo: python <fw>/skills/machine-project/scripts/cad_incremental.py --model assemble.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from parts import player

items = player.build()
print(f"{len(items)} placed parts")
