# {title}

Machine `{machine}`. The model is the source of truth; every other file is derived from it or explains it
(standard: `design-engineer` skill `machine-project`).

## Layers

Each layer has one folder and one industry format. A folder appears when its layer gets its first file, and gets a README.

| Folder | Layer | Source or generated | Format |
|---|---|---|---|
| `aas/` | Machine index and numbers (`{machine}.aas.json`) | source | Asset Administration Shell, IEC 63278 |
| `mechanics/` | CAD, kinematics, part designs | source | {cad_line} |
| `electronics/` | schematic, firmware (one PlatformIO project per board), interfaces | source | KiCad target, PlatformIO, AAS AssetInterfacesDescription |
| `physics/` | physics-engine scenes | source | MuJoCo MJCF |
| `sim/` | glue between the layers only | source | scripts |
| `docs/` | documents for people | source | Markdown |
| `wiki/` | decisions and non-numeric facts (`index.md` first) | source | Markdown |
| `build/` | everything generated: STL/3MF, DXF, glTF, renders, packages | generated, git-ignored | any |

Numbers live in the model (named fields, e.g. `mechanics/cad/params.py`), never in `wiki/` or here.
Screenshots and previews go to `build/` or `outbox/`, never next to sources.

## Build

{build_line}
