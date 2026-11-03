# nema-bracket

Machine `nema-bracket`. The model is the source of truth; every other file is derived from it or explains it
(standard: `design-engineer` skill `machine-project`).

## Layers

Each layer has one folder and one industry format. A folder appears when its layer gets its first file, and gets a README.

| Folder | Layer | Source or generated | Format |
|---|---|---|---|
| `aas/` | Machine index and numbers (`nema-bracket.aas.json`) | source | Asset Administration Shell, IEC 63278 |
| `mechanics/` | CAD, kinematics, part designs | source | build123d code, numbers in `mechanics/cad/params.py` |
| `electronics/` | schematic, firmware (one PlatformIO project per board), interfaces | source | KiCad target, PlatformIO, AAS AssetInterfacesDescription |
| `physics/` | physics-engine scenes | source | MuJoCo MJCF |
| `sim/` | glue between the layers only | source | scripts |
| `docs/` | documents for people | source | Markdown |
| `wiki/` | decisions and non-numeric facts (`index.md` first) | source | Markdown |
| `build/` | everything generated: STL/3MF, DXF, glTF, renders, packages | generated, git-ignored | any |

Numbers live in the model (named fields, e.g. `mechanics/cad/params.py`), never in `wiki/` or here.
Screenshots and previews go to `build/` or `outbox/`, never next to sources.

## Build

Code model (build123d): numbers in `mechanics/cad/params.py`, one folder `mechanics/cad/parts/<node>/` per node: one file per part `<part>.py` with `build(**args)` returning one part in its own coordinates, `geom.P(name, shape, color, process=..., material=..., thickness=..., code=..., weld=...)`, with build123d joints, and `__init__.py` whose `build()` docks them (`a["shape"].joints["x"].connect_to(b["shape"].joints["y"])`, then `geom.place(b)`; qty = number of placements); screws/nuts/washers from `parts/fasteners/`. Rebuild + viewer: the router runs kit.json `site` (`sh sim/site.sh`) after every turn that changed the model (the cache re-runs only the parts whose params or code changed; one STEP per part in `build/model/`). Download kits (STEP, laser/bent drawings, print STL, purchase list) are `derive.py` scopes declared in `kit.json`: nothing is exported by hand. `python3 <plugin>/skills/machine-project/scripts/aas_sync.py .` copies params and parts into the AAS (site.sh runs it).
