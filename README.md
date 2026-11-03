# design-engineer — the digital twin of a device, as code, built by a team of AI agents in Claude Code

`design-engineer` is a Claude Code plugin that turns any folder into an engineering project for a physical device: a machine,
a fixture, a product. Not a CAD tool: one parametric model of the whole device, kept as code and checked on every change.

- **One parameters file for the whole device** (`mechanics/cad/params.py`): every size, count and choice lives there; the parts,
  the drawings, the bill of materials and the downloads are derived from it.
- **Mechanics** in [build123d](https://github.com/gumyr/build123d): one file per part, a folder per node, parts docked by joints,
  bought parts as the maker's CAD, fasteners placed one by one.
- **Electrics and pneumatics** drawn from the model's ports and connections; **firmware** that runs on a virtual board next to the
  model, and is flashed onto the real board from the browser (Web Serial).
- **Checks of the whole cycle**: parts that share space, moving parts that come too close, fits (a screw in its hole), strength
  (FEA), the project's conformance rules — after every change, before anyone sees it.
- **A 3D viewer on localhost** where the customer (or you) draws on the model — a circle, an arrow, a pin, a dimension — and the
  note goes straight to the agent, with the part and the point in millimetres it lies on. Download kits: STEP, print STL, laser DXF
  + PDF drawings, the purchase list, assembly drawings.
- **A team of agents**: in team mode you talk to a manager, and each task is worked by its own executor — a background Claude Code
  session you can watch and attach to.

## Install (macOS, Linux)

```bash
curl -fsSL https://raw.githubusercontent.com/aimsolve/design-engineer/main/install.sh | sh
```

It installs [uv](https://docs.astral.sh/uv/) if missing, the Python 3.12 environment of the core (build123d and friends, ~1 GB,
once), `de` in `~/.local/bin`, and this plugin into Claude Code. Requirements: git and
[Claude Code](https://claude.com/claude-code); no API keys beyond your Claude Code login. Optional parts:
`sh install.sh --extras sim,fea,render,electronics` (MuJoCo, gmsh FEA, Playwright renders, circuit tools).

## Use

```bash
mkdir my-machine && cd my-machine
de init            # or: de init --team   (a manager + one executor per task)
claude
```

Then describe the device, e.g. *"design a NEMA 17 motor bracket for a 2020 aluminium profile"*. Claude models it, builds it,
runs every check, and gives you the viewer link (`de serve` → `http://127.0.0.1:8765/my-machine/`). A turn that ends with a model
that does not build or fails a check is sent back to Claude to fix (Claude Code hooks).

| Command | What it does |
|---|---|
| `de init [--team] [--lang en]` | a project here: model skeleton, `wiki/`, `kit.json`, `.engineer/`, a project `CLAUDE.md` |
| `de build` | the model (`kit.json` `site`), the viewer site in `public/`, the asset administration shell (`aas/`) |
| `de check` | the conformance check: layout, model, fits, clashes, bought parts, wiki |
| `de serve` | the viewer on 127.0.0.1: notes drawn on the model → `.engineer/inbox/`, download kits, a board on the desk |
| `de kit step\|print\|laser\|bought\|assembly` | a download kit now |
| `job run "<cmd>" [--timeout S]` | heavy work (renders, sims, firmware builds) with a time limit |
| `hw status\|flash CARD\|log CARD` | the real board plugged into the computer, through the open viewer page |
| `issue new\|comment\|close\|list …`, `de exec start\|stop N` | team mode: tasks in `.engineer/issues/`, one executor session each |

An example project: `examples/nema-bracket` (copy it, `de build`, `de serve`).

## Skills

| Skill | For |
|---|---|
| `engineer-loop` | the working loop: the person's words first, simplest design, one picture before detail, critic rounds |
| `machine-project` | where every file goes; the model as the only truth; scaffold, build, checks |
| `cad-build123d` | parametric CAD with one params file as the contract |
| `part-library` | bought parts as the maker's CAD, from the first concept picture |
| `web-viewer` | the standard 3D viewer: cards, drawing notes on the model, downloads, phone layout |
| `sim` | engineering sims that decide a design: simple models first, MuJoCo, FEA, cycle collisions |
| `shop-drawings` | sheet-metal drawings (laser, bending): flat pattern, ordinate dimensions, title block |
| `fdm-print` | printable parts and test pieces: preview first, tests that can fail |
| `step-import`, `step-migration` | a STEP from another CAD ported onto the code model |
| `virtual-controllers`, `firmware-sim` | the device's real firmware on a virtual board; board simulators |
| `electronics-review`, `circuit-sim`, `datasheets`, `emc-debug` | schematic and firmware review, ngspice, cited datasheet numbers, noise debugging |
| `project-wiki` | the project's decisions and rules, filed where they are true |
| `video-transcribe` | a reference video: transcript, timestamps, frames |

The rules every session follows: `PRINCIPLES.md` (both modes) and `local.md` (this local mode); team roles in `local/roles/`.

## Modules

The core runs in any folder with nothing else. A Telegram front, GitHub issues as the task backend, a multi-project router and
remote job runners are separate modules of a hosted platform; they are not part of this repository and are off unless a platform
connects them. The core never calls a module directly: where a skill mentions one, it says what to do locally.

## Development

```bash
claude --plugin-dir ./                     # this checkout as the plugin
UV_PROJECT_ENVIRONMENT=~/.local/share/design-engineer/venv uv run --frozen --group dev pytest tests
```

Keep the Python environment outside the plugin folder (Claude Code copies a plugin folder into its cache as it is).
`DE_E2E_CLAUDE=1` adds the real Claude Code runs (~10–30 min each).

## Licence

AGPL-3.0-only (`LICENSE`). Third-party code keeps its own licence: see `NOTICE`.
