---
name: machine-project
description: Use before creating any file in a machine project (where does it go?), when a project is new or has no aas/ (run scaffold), when the model, exports (print / laser / web), git or the AAS are touched, and at the end of every turn that changed files (run check.py; `check.py --kb` lints the wiki). The project standard: the model is the source of truth, one folder per layer (aas/ mechanics/ electronics/ physics/ sim/ docs/ wiki/ raw/ build/), source vs generated, the model-first loop, what goes to git. Scripts: scaffold.py, aas_sync.py, check.py, kb_lint.py, fit_checks.py (fits: screw in its hole, docked pairs, bought = drawn), meshpart.py (smooth housings as mesh parts), clearance.py (the build's own overlap check: no two parts share space, over the cycle), harness.py (wires in 3D: port locations, routes through waypoints, bundles, cut lengths, the harness check), cad_incremental.py (`--model`: per-part incremental build123d build), derive.py (per-part exports: step / laser / print / bought / viewer), model_from_steps.py (model of a client-CAD project), part_library.py (the part library: `roots` / `sync` / `check`). Also use when the client asks for an app (phone / Android / operator / control app): there is exactly one way to make one, see «The machine's app».
---

# Machine project standard

Hard rules for every machine project. The model is the source of truth, fully parametric, in standard formats and
convertible both ways; everything else is derived from it or explains it. Nobody asks the client a number that is in the model.

Scripts: `S=${DE_ROOT:-/opt/design-engineer}/skills/machine-project/scripts` (`DE_ROOT` = the plugin root: `de` sets it in local mode; the platform mounts the plugin at /opt/design-engineer).

## Start

```bash
python3 $S/scaffold.py . --name <machine> --title "<human name>" [--cad build123d|step]   # idempotent; build123d: params.py, parts/example/ (`__init__.py` places, `block.py` one part), geom.py (`P()`, `place()`), assemble.py, kit.json (derive.py scopes), sim/site.sh
python3 $S/check.py .                                                                    # PASS/FAIL per rule (+ KB lint as WARN)
python3 $S/check.py . --kb                                                               # only the wiki lint; exit 1 on a KB FAIL
```
On a new project the platform has already run scaffold (no `--cad`); run it again with `--cad build123d` when you start modelling in code.
A STEP the client sends is ported onto code (below, «A client's STEP»); `--cad step` is only for a legacy project not ported yet. A project without `aas/` is not on the standard: scaffold it, then move files to their layer.

## A client's STEP: ported onto code, never the model

A STEP (or SolidWorks model) the client sends is ported onto the code model, never imported as it is (skill `step-migration`):
step_import.py cuts it, step_to_project.py writes one folder per sub-assembly and one file per part with every part where his
CAD has it, the parts it leaves on his geometry are drawn by hand one by one, migration_check.py proves the client will
not notice, and check.py checks the project. A node, a sub-assembly for an existing project, is ported into it as one new node
(`step_to_project.py … --node <parent node folder>`). Recognition takes 1–2 h, so it never runs inside a turn:
`job run --needs heavy --expect long --timeout 14400 "python ${DE_ROOT:-/opt/design-engineer}/skills/step-import/scripts/step_to_project.py build/client_step/<file> . --library build/clients/<tg id>"`.
The client hears one line: the model is being ported and when it will be ready.

Bought parts of a port go to the client's library /clients/<tg id>/library, never to the framework catalogue (every client
sees that one): step_to_project.py stages them with `--library build/clients/<tg id>`, step-import's merge_library.py
merges them into /clients/<tg id> (never a plain copy), part_library.py sync brings them into mechanics/cad/library/.
A job never gets `inbox/`: copy the STEP to build/client_step/ first.

## Layers: one folder each, one format each

| Folder | Standard | Source | Generated (goes to `build/`) |
|---|---|---|---|
| `aas/` | machine index and every number: `<machine>.aas.json`, Asset Administration Shell IEC 63278 (`step-import/AAS.md`) | the JSON | `build/<machine>.aasx` package |
| `mechanics/cad/` | build123d code with `params.py` as the numeric source (`cad-build123d`); a client's STEP is ported into it (legacy projects not ported yet keep a STEP) | code | STL, 3MF, GLB, renders |
| `mechanics/kinematics/` | which model nodes each actuator moves; `mechanism.py` where a closed loop is stated | source + derived from the AAS (`motion.json`) | - |
| `mechanics/<part>/` | part designs, when a part needs its own drawing/notes | files | - |
| `electronics/schematic/` | schematic; KiCad is the target format (`.kicad_sch`); PDF/EasyEDA only as received | schematic | netlist, ERC output |
| `electronics/firmware/<board>/` | ONE PlatformIO project per board (`platformio.ini`); a client's sketches stay unchanged inside | code | `.pio/`, wasm, binaries |
| `electronics/interfaces/` | protocols (BLE, serial): `.md` + machine-readable `.json`; also in the AAS as AssetInterfacesDescription (IDTA 02017) | files | - |
| `physics/` | physics-engine scenes, MuJoCo MJCF | MJCF | sim logs |
| `sim/` | glue between layers only (harness, site config); no model data | scripts | `build/site/` |
| `viewer/` | optional, machine-specific viewer/presentation layer (page templates `*.tpl.html`, viewer scripts); templates are source, built pages go to `build/` | scripts, templates | `build/` pages |
| `docs/` | documents for people | Markdown | PDF |
| `wiki/` | decisions and non-numeric facts only (`project-wiki`); `index.md` is generated by check.py | Markdown | `wiki/index.md` |
| `raw/` | immutable sources: chat exports, transcripts, critic dumps (never inside `wiki/`) | files | - |
| `build/` | everything generated; git-ignored; recreate on demand | - | all of it |

- A layer folder is created by whoever adds its first file, with a `README.md` (what is source, what the format is). No empty placeholder trees.
- Screenshots and previews never sit next to sources: `build/` or `outbox/` (sent to the chat), `public/` (served page).
- Runtime dirs `.engineer/ .home/ jobs/ inbox/ outbox/ public/` belong to the platform, not to the model.

## Loop: model first

1. **Change the model** in the layer that owns the number (params.py / CAD / AAS / firmware / interface file).
2. **Regenerate.** Local mode (`.engineer/mode` = `local`, `local.md`): you run `de build` (= kit.json `site`), then `de check`; the Stop hook runs both again at the end of your turn and sends you back on a failure. A file for the user: its path in `build/` or the local viewer link.
   Platform: **not you.** After your turn the router runs kit.json `site` (build123d: `sh sim/site.sh`: the cache re-runs only the nodes whose params or code changed, re-meshes only changed parts, rewrites the viewer in `public/`, syncs the AAS), then remakes the download files; your next turn starts with the platform's notice that model vN is ready, or that it did not build, with the log. Jobs that run derive.py, site / twin scripts or kit.json commands are refused. A file for the client: `tg link`.
3. **Verify**: read the result (numbers, render, min distances; `engineer-loop`), `python3 $S/check.py .`.
4. **Record the decision** in `wiki/` with its why and the client quote. The wiki names the model field (`WIDTH_MM`, `parts: <id>`), never the number.

If the wiki and the model disagree, the model wins and the wiki line is fixed. A number the client gave goes into the model
(`params.py` with a comment naming the wiki decision), not only into a decision page. Before asking the client anything, look it up in the model.

### Code model: one file per part, a folder per node -> one STEP per part -> `derive.py` (build123d)

The model on disk is `build/model/`: `parts.json` + one STEP per part (named by a key = hash of the inputs that made it).
Nothing else is a source of truth and nothing is exported by hand.
- **Layout**: a node is a folder `mechanics/cad/parts/<node>/`; a sub-node is a sub-folder (`parts/tower/carriage/` = node
  `tower/carriage`, a group inside `tower` in the STEP, the group `tower/carriage` in the viewer). In it:
  - `<part>.py` = ONE part: a plain `build(**args)` that builds it **in its own coordinates** (at the origin) and returns
    `geom.P(name, shape, color, process=..., **fields)`. Identical parts are one file placed several times; a size that differs is an
    argument (`build(length=600)`): each distinct set of arguments is a variant with its own name, STEP and cache entry
    (defaults applied, `600` == `600.0`). Find a part by its file name; edit only that file.
  - `__init__.py` = the node: `build()` docks its parts and returns them placed, plus rows without a shape (`P("pipe", None, ...,
    qty=3)`). It never builds geometry and never edits a placed copy (a changed shape or field of a placed copy is a hard error).
- **Joints: every part stands where a joint puts it.** A part file declares stock build123d joints on its shape, in its own
  coordinates: `RigidJoint("bottom", shape, Location(...))`, `RevoluteJoint("hinge", shape, axis=Axis(...), angular_range=(0, 90))`,
  `LinearJoint("carriage", shape, axis=..., linear_range=(-60, 60))`, `CylindricalJoint`, `BallJoint`. The node docks with the stock
  API and places without a Location: `base["shape"].joints["rail"].connect_to(rail["shape"].joints["bottom"])`, then
  `place(rail)`; a moving joint takes its value there (`connect_to(..., position=30)` / `angle=90`; outside the range = a build
  error naming the parts). Rules: the joint frames coincide (no flip): a hole / face joint sits on the surface, Z out of the part;
  the docking part's joint Z points back at it (the screw's `head`: Z from the shaft to the head). Dock first, then place(); a part
  that moves after place() is a build error. One `build()` call per copy (`s = screw.build(...)` per hole): each copy has its
  own shape and joints. One docking places a part: a joint used for a second connect_to forgets the first (that part then fails
  check.py). A sub-node returns its list in its own coordinates; the parent docks one part of it and `place(sub)` moves all of it.
  Only the machine's root part is placed with a Location (`place(base, Pos(0, 0, 0))`); several top nodes: `parts/__init__.py`
  `build()` is the machine (builds the nodes, docks them onto each other, returns them all) and assemble.py calls only it.
  `build/model/parts.json` keeps the graph: per part `joints` {label: {type, loc, axis, range}} (own coordinates), per placement
  `joint` {to: <placement>, at: <its joint>, by: <own joint>, type, value}. check.py FAILs a placement without one (but the root).
  So a size change moves everything docked to the changed part, and the assembly drawings, motion and URDF read this graph.
  Converting a project: copy the framework's templates/geom.py place / _moves / _same over the project's place() first (the old one
  needs a Location). A docking the parts no longer show (the parent moved after it) fails the build naming both.
- **Fasteners are parts**: `parts/fasteners/` (from the framework templates `screw.py` `build(size, length, std)`, `nut.py`
  `build(size, std)`, `washer.py` `build(size, std)`): bought, kind fasteners, code `M5x12 DIN 912`; dock each into its hole joint
  (washer `seat` on the hole, screw `head` on the washer's `top`; a nut's `seat` on the far side). qty = placements. Never count
  screws in text or in a row: every screw that holds two parts is placed.
- **A bought part is the product its row orders.** A real loss: a bracket was drawn from one maker's 3D (a hole for M5)
  while the purchase list ordered another product, picked by name. The client bought that one, whose maker drawing gives a
  4.6 mm hole for M4 — the M5 does not go in. So, on every bought part with a 3D:
  - `src` = where the 3D came from: the vendor file (`src="vendor/<maker>-<article>.step"`, e.g.
    `"vendor/acme-A184.step"` — the stem after the maker is read as the article, so the order matters) or, better,
    `src={"file": ..., "code": "A184", "link": <the product page>, "maker": "Acme"}`. check.py **FAILs** when the row
    names several articles and its product link orders one that is not the drawn one (the row of that loss listed both
    articles and linked the other one), and when a part that records `src` offers an analogue in its purchase text: `src` says
    the machine's fits ARE this maker's geometry, so "or an analogue" (in any language) contradicts it — order this product, or drop `src` and draw the
    part from its sizes. It **WARNs**, never FAILs, when the row's link names no article at all (a marketplace slug of words),
    when it links a search page instead of a product, and when `src` has no `code` and the one read out of the file name is
    not in the row either: none of those prove the wrong thing is being bought.
  - `verified` = the check against the maker's own document, made BEFORE the part goes into the list: the size under the
    fastener and the fit to what it touches (profile, slot, thread). `verified={"against": "soberizavod STEP V222LX + A184",
    "checked": ["hole slot 7 for M5", "key 1.0 high in slot 6"], "when": "2026-10-26"}`. A fastener / profile / bearing without
    it is a WARN, and its purchase row carries `verified: false`, which the manager reads: it answers clients out of those
    rows, so the row itself says whether anybody ever checked the product in it. Asking the client what the maker's drawing
    says is not the way: read the drawing or the STEP.
  - `fit: false` on a part keeps it out of the fit rules (a joint that is a datum, not a mating face) — say why in a comment.
  - `TITLE = "..."` in a node's `__init__.py` (a model ported from a client's CAD, step-import `step_to_project.py`): the
    viewer shows that node as a group of that name per copy, nested by the nodes' imports and joints, so a sub-assembly
    the client reuses under two parents (its folder is under the first) shows under each of them.
  - `_<name>.py` = shared code of the node (`from . import _common`, or `from parts.frame import _common` from another node);
    in a node folder only a module's `build()` is a cached build (helpers may be named anything). `assemble.py` only calls nodes:
    a `place()` there is not in the model and fails the build.
  - A part may call another part's `build()` to read a size; only what a node places is in the model.
- **Closed loops: state what is closed and what drives it, never the trigonometry** (`scripts/linkage.py`). A part is placed
  once, by one docking, so a loop a docking cannot hold - a cylinder driving a crank through a rod, a four-bar, a slider on a
  swinging lever - is stated instead, in `mechanics/kinematics/mechanism.py` (copy `templates/mechanism.py`): the joints of the loop (`turn(name, on, at, start)`,
  `slide(name, on, offset, start)`), which pairs of points stay together (`meet((body, point), (body, point)[, distance=...])`,
  a `distance` being a link with no body of its own) and which joint is driven (`sweep(joint, from, to, steps)`). The rest is
  `scipy.optimize.least_squares`, each frame starting at the one before it. Rules: as many unknown joint values as equations
  (anything else is refused naming the spare joint); a loop the sizes cannot close is refused with the gap in millimetres. The
  whole sweep is read as one before any of it is returned, so a loop that folds flat at any frame, or that turns over between two
  of them, is refused naming where rather than handed back as the mirror assembly - what that cannot see is a flip that happens
  and undoes itself between two frames you asked for, so a cycle too coarse to show the motion is still yours to notice. Driving
  it the other way round (the stroke a swing needs) is the same declaration read backwards, so nothing is bisected by hand.
  DRIVE THE JOINT THE MOTOR TURNS: a crank swung or turned by a stepper is driven by the crank, never by its slider. At a dead
  centre the slider's position does not say which way the crank goes on, so a slider driven through one is refused (a machine
  with six crank drives was exactly like that: exact when driven by the crank, refused when driven by the slider).
  NEVER write the loop out as `atan2`/`asin` in the model and again in the cycle: those are two answers that drift apart in
  silence. One declaration, every reader of it.
- **The viewer cycle is generated from that mechanism**: `mechanism.py` also states `PERIOD_S`, `RUNS` (when the drive
  runs over one period, and which way), `FRAMES`, `poses()` and `groups(poses)` (which viewer group follows which joint value,
  about which pivot, riding on what), and `scripts/cycle_from_mechanism.py --mechanism mechanics/kinematics/mechanism.py --out
  build/motion.json` writes the `cycle` block. Travels, per-frame fractions and segments are arithmetic on the solve, so the
  animation cannot disagree with the model. A link between two moving parts (a crank's connecting rod) is declared as a body
  of its own hung on the crank (`turn("rod", on="crank", at=<crank pin>)` and `meet` its far end with the slider - not as a
  `distance=`, which has no body to show) and is a `rotary` group riding on the crank (`parent`), turning about the crank pin:
  exact in the viewer, so no per-frame track is needed for it. **The same declaration is what the machine's MJCF and
  URDF come out of** (`scripts/export_robot.py`, skill `sim`): `LINKS` says which group of parts is which link and
  `DRIVES` what drives the loop, each `meet` becomes an MJCF `<equality connect>`, and URDF gets the chain with the loop
  broken and said so - nobody writes a second model of the machine for a simulator. Covered so far: ONE
  driven joint per machine, written as the `cycle` block. A machine with several drives on their own timings
  or with stepper `actuators` in motion.json uses `linkage.py` for its poses and keeps its present path to the
  viewer until the generator takes several drives. A machine whose groups move independently on a fixed schedule has no loop
  and keeps `web-viewer/scripts/cycle_from_params.py` (`GROUPS` / `MOTION` in `params.py`) instead.
- **qty is the number of placements.** `qty=` on a placed part is a hard error; a row without a shape gives `qty`, and a bought
  part may give `qty` = units per placement (a pipe drawn once, bought as 5 × 1 m: total = qty × placements).
  Placement names (the viewer's ids) are the part's name, then `<name>_2`, `_3`…, or `name=` in `place()`; unique in the model.
- **Rebuild** (you do not call this yourself, `sim/site.sh` and `derive.py` do): from `mechanics/cad`,
  `python $S/cad_incremental.py --model assemble.py`. `assemble.py` only imports every node and calls its `build()`.
  Last log lines: `nodes rebuilt N, reused M | parts K` and `rebuilt: frame.foot.build, ...`. A part re-runs only when a param value
  it read, its own file or a project module it imports, or a file it opened changed; moving a part in its node re-places it and
  rebuilds nothing. Otherwise its part is read back from its STEP.
- **Where numbers live**. Every number and every value derived from one is in `params.py`; a part does `import params as P`
  and reads `P.NAME`. A node that has shared or derived numbers of its own puts them in `parts/<node>/params.py` and the parts of
  that node read them with `from . import params as P`; a name that file does not define comes from the machine's `params.py`
  and is keyed there. Both are params modules: their values are tracked read by read, so a part that did not read a changed name
  is not rebuilt, and editing a formula that changes no value rebuilds nothing.
  A value taken out of an ordinary project module (`from ._layout import RAIL_Y`) is keyed by its VALUE, like a param: the part
  rebuilds when `RAIL_Y` comes out different, not when some other number `_layout.py` read changed. A function taken is keyed by
  its code and the values of the module-level names it uses; a project class by its methods and class attributes; a Location,
  Vector, Axis or Plane by its numbers. An object (an instance, a shape, an array) is not keyed by its state: it brings every
  number read by the imports that could have built or changed it, so keep layout numbers as numbers, tuples, strings, lists and
  dicts, and an object out of a layout module. The module's FILE is hashed, so any edit to it rebuilds every part that uses it -
  which is why shared numbers belong in a params module and helper code in a `_` module.
  A part reused from the cache stays unread until its geometry is touched: `isinstance(sh, Solid)` and `sh.__class__` answer
  without reading it, but `type(sh) is Solid` is False until then - dispatch on isinstance().
  **Never `from params import *`**: the values it copies in cannot all carry a mark back to their parameter, so one changed
  number rebuilds parts that never looked at it (check.py warns and names the file). The one exception is a node's own
  `params.py`: start it with `from params import *` and add the node's own numbers below. Its values are keyed by value, so
  that costs nothing, and the machine's names then also resolve in a plain script run (outside `--model`).
  Import project modules at the top of a file, not inside `build()`: a module imported only inside `build()` is not there when
  the next run checks the part, so the part is keyed by everything that module read and by its file, as before #18.
- **Part fields**: `process` ∈ print | laser | bent | mill | turn | other | bought, plus JSON fields: `material`, `thickness` (laser/bent),
  `finish`, `code` (the cipher shown in the kit name and on the drawing), `weld`, `unit`; a bought part adds `desc`, `supplier`, `link`,
  `kind` (fasteners|electrical|motors|pneumatic|bearings|profile|other), `class`. The id is the name as the viewer spells it (`_` for
  non-word characters) and must be unique: two variants or files with one name are a hard error naming both. A bought part with no
  3D model (pipe by the metre) is `shape=None`: a row of the purchase list only. Anything else without a shape, or a non-JSON field, is a
  hard error naming the part. An argument without a JSON form (a Location) works but is rebuilt on every run (a warning names it).
- A node is a folder: any `parts/<x>.py` that is not a `_` helper fails check.py, and one with `build()` / `build_*()` also stops
  the build. Every `inputs` list of
  `kit.json` (top level and each scope) must hold `mechanics/cad/parts/**/*.py`, or an edit of a part file does not refresh the
  downloads (check.py fails it).
- A row inside a sub-node placed N times counts N × its `qty`. Every node `assemble.py` calls is a root of the model, with or without
  arguments; a check script calling a node (e.g. with a pose) never adds to or removes from the model.
- **Contract for parts and nodes**: geometry depends only on `params`, the arguments, the module code, files it opens and `import_step`; no env vars, clock, random. A module's import changes nothing but its own names. A module whose code sets an attribute or item anywhere (a library class patched, another module's object set up, `self.x = ...` in a method), or uses setattr / exec / globals(), keeps every number it read in every part that uses it, and so does every module importing it; a change made by calling a library function is not seen at all. Outside
  the contract = stale parts: delete `build/model` to start clean.
- **Legacy client-CAD machines, not ported yet** (no node code; the client's per-part STEPs + a parts list; a new one is ported instead): `kit.json` gets `"model": "steps"` (optional `"model_parts"`,
  `"model_steps"`, default `build/parts.json` and `build/parts`); `derive.py` runs `model_from_steps.py ROOT PARTS_JSON STEP_DIR`, which writes
  `build/model/`: bought kinds -> `bought` (code = article), printed -> `print`, a flat steel sheet <= 6 mm -> `laser` (thickness from the solid),
  other made parts -> `other`. `bent`, `weld`, a different material/thickness/qty/code come from `mechanics/export/parts_meta.json`
  `{"<id>": {"process": "bent", "thickness": 3, "weld": "A"}}`. Its fields replace the generated ones, `name` and `node` too
  (two parts named alike get separate drawings by renaming one there). **Vendor assemblies** (a motor or driver delivered as several STEP
  pieces): the leader row carries `qty` (how many assemblies); every other piece says `"counted_in": "<leader id>"` and derive bought lists
  no row and adds no qty for it (model_from_steps rejects a `counted_in` that is not another bought part). Print parts: `derive.py print`
  lays the STL on its largest planar face; `"print_rot": [rx, ry, rz]` or `["X"|"Y"|"Z", deg]` (degrees) in the meta or on the part
  replaces that choice. The STL is the part as it stands at its **first placement** (print_rot turns it in machine axes after that).
- **Optional part fields** the scopes read (a project without them gets today's output):
  - `print_with: "<print part id>"`: printed as one piece with that part (fused at their first placements); no file of its own.
  - `mesh: {"v": [[x, y, z], ...], "f": [[i, j, k], ...]}` instead of `shape` (not to be confused with the solid mesh parts of #78,
    `meshpart.Mesh` as the shape, below): a view-only part drawn as triangles (vendor units with no
    B-rep), in machine coordinates where it stands; the viewer shows it, bought counts it, step / print / laser make no file of it.
  - bought: `desc`, `code` (`""` stays empty), `unit`, `supplier`, `link`, `kind`, `class`, `note`; merged rows list every node.
    `buy: "length"` = stock sold by length: one row per (code, name) in metres, the copies' longest sides (a row's `buy_length` mm x qty)
    x `buy_margin` (1.2), rounded up. `cut: {stock, note, label, total, code?, class?, all_total?}` on any part: a piece of a cut list
    (one row per length, a total per stock, one per `all_total`); a bought part with `cut` makes only those rows.
  - M2 / M3 screws from the holes: a row-only bought part with `screws_from_holes: {name, code, supplier, class, kind, unit, margin}`
    ({n}, {L} in name / code) turns it on: coaxial round holes of two placed parts = one screw; `holes: false` leaves a part out,
    `holes_skip_with: "<regex>"` drops a stack with a matching part (a block over its rail), `screws: [[n, L, count]]` per placement
    adds screws the holes cannot pair, `screws_unpaired` adds them only when the copy is in no counted stack.
  - viewer: `card: {name, material, kind, density, thickness}` over the guessed catalogue values (the name keeps a copy's suffix; the glb
    node keeps `name`), `motion_group` (the cycle moves it with that group), `in_viewer: false` (not on the model page).
- Any other script run as `python $S/cad_incremental.py script.py [args]` is a plain `python script.py args` (no caching; `--no-cache`
  is accepted and ignored). Only `--model assemble.py` builds the model; a plain run of `assemble.py` is refused.

### Smooth housings: mesh parts

A housing held in the hand (a player, a remote, a grip) is a smooth shell: blends, a thumb dimple, a waist, a wedge. build123d
cannot make those reliably (OCC fillets and the wall offset fail on them, as a trial showed). Such a part is a **mesh part**:
its skin is a signed distance field, every fit feature is an exact build123d solid, and `meshpart.py` (in `$S`, importable from a
part file) combines them; the part file returns `meshpart.Mesh` instead of a B-rep. Everything else is as for any part.

```python
# mechanics/cad/parts/body/shell.py            params.py: W, L, H, R (edge radius), WALL, SPLIT (front shell above it)
import math
import params as P_
import meshpart as mp                      # never `as M`: build123d's `*` defines M (a unit)
from build123d import Align, Box, Cylinder, Location, Pos, RigidJoint
from geom import P

UP = (Align.CENTER, Align.CENTER, Align.MIN)


def slab(w, l, h, r):
    """Signed distance (mm, negative inside) of a soft-edged slab. Numbers come in as arguments: the field is called a
    million times, and a params read inside it would be tracked by the model build every time (minutes instead of seconds)."""
    def f(x, y, z):
        q = [abs(x) - w / 2 + r, abs(y) - l / 2 + r, abs(z - h / 2) - h / 2 + r]
        return math.hypot(*[max(c, 0) for c in q]) + min(max(q), 0) - r
    return f


def build():
    skin = slab(P_.W, P_.L, P_.H, P_.R)
    box = (-P_.W / 2 - 1, -P_.L / 2 - 1, -1, P_.W / 2 + 1, P_.L / 2 + 1, P_.H + 1)
    outer = mp.sdf(skin, box, edge=0.5)
    inner = mp.sdf(skin, box, edge=0.5, level=-P_.WALL)                 # the same field 2 mm in: a uniform wall
    above = mp.solid(Pos(0, 0, P_.SPLIT) * Box(P_.W + 10, P_.L + 10, P_.H, align=UP))
    boss = mp.solid(Pos(22, 40, P_.SPLIT) * Cylinder(2.5, P_.H, align=UP)) ^ outer    # exact solids, clipped to the skin
    cuts = (mp.solid(Pos(0, 17, 0) * Box(32, 42, 3 * P_.H)) +                         # screen window
            mp.solid(Pos(22, 40, P_.SPLIT) * Cylinder(0.85, 6, align=UP)))           # M2 thread-forming hole, 6 deep
    shell = mp.Mesh(((outer - inner) + boss) ^ above - cuts)
    RigidJoint("boss_1", shell, Location((22, 40, P_.SPLIT), (180, 0, 0)))   # on the boss face, Z out of it: a screw docks here
    return P("shell", shell, "#d9d4ca", process="print", material="PETG")
```

- `mp.sdf(f, bounds, edge, level)` meshes {f < level} (f negative inside, `bounds` with a mm of margin; edge 0.5 ≈ 5 s for a
  60 × 100 × 15 shell, cached like any part). Pass the numbers into `f` (a closure, as `slab` above): `f` must not read
  `params` or a project module itself. Blends are SDF arithmetic: a smooth max/min (`k` mm) gives a dimple or a grip
  that flows into the surface, where OCC leaves a crater with a fillet ring or fails.
- `mp.solid(shape, tol=0.01)` = an exact build123d solid as a mesh: screen windows, button holes, bosses, screw holes, PCB
  ledges. Their sizes survive to 0.01 mm (a 32 × 42 window measures 32.00 × 42.00 on the printed STL); the fit numbers live
  in params.py and the build123d code as before. Combine with `+` `-` `^` (manifold3d: exact mesh booleans, never fail).
- `mp.Mesh(body)` must be a closed solid (the build refuses another, naming the part). Joints, docking, `place()`, several
  placements, the cache: as for a STEP part. parts.json: `mesh` (its triangles, own coordinates) + `mesh_solid: true`, no `step`.
- What it gets: **print** STL (first placement, largest flat face down or `print_rot`; no STEP file next to it; no
  `print_with`), the **viewer** and its download, **assembly drawings** (outline and creases, hidden where a part is in front),
  **clearance** and **fit checks** (a screw in its boss: the hole and the depth read off cross sections of the mesh, the
  same rules and verdicts as for a STEP part). Not: the model STEP (`derive.py step` leaves it out and says so in
  `not_in_step`), laser / bent (the build refuses a mesh part with that process), the URDF / MJCF mass, `screws_from_holes`
  (dock its screws).
- Use it for the part a person sees and holds; keep brackets, plates, frames in build123d (they keep their STEP).

### Part library: a part designed once, used in every project it belongs to

Before you draw a purchased part or a unit you have built before, look in the library: `python3 $S/part_library.py roots`
says which levels this project sees, and the parts in use are files under `mechanics/cad/library/<group>/`.
A library part is a part file exactly as above (`build(**args)` -> `geom.P(...)`, joints of #16).

**A node may not build a library part itself** (a node only places parts, as above), so a library part enters the model
through a part file of the project — one file per library part used, which is also where this machine's numbers are
passed in:

```python
# mechanics/cad/parts/clamp/cylinder.py   <- the part file: this machine's cylinder is that library part
import params as P_
from library.pneumatic import airtac_sc


def build():
    return airtac_sc.build(bore=50, stroke=P_.CLAMP_STROKE_MM)
```

```python
# mechanics/cad/parts/clamp/__init__.py   <- the node docks and places it like any other part
from geom import place
from . import cylinder, jaw
...
    c = cylinder.build()
    base["shape"].joints["cyl_seat"].connect_to(c["shape"].joints["rear"])
    c["shape"].joints["rod"].connect_to(jaw_part["shape"].joints["base"], position=0)
    return [place(c), ...]
```

The part keeps the library's name, ports and purchase row; the file in the project is where it stands in this machine.
The same library part placed by several nodes is still ONE part file, imported by each of them the way `parts/fasteners/`
is (`from parts.bought import bearing`); two part files returning the same library part are two producers of one id,
which the build refuses. Another variant (another stroke, other `mounts`) is another call, and the library gives it its
own id.

- **Three levels, by whom a part belongs to.** The framework's catalogue (`${DE_ROOT:-/opt/design-engineer}/catalog/library`) holds
  purchased parts as they are sold, and every project sees it. `/clients/<client id>/library/` holds what was designed for
  that client — his typical units and his reworked parts — and only his projects see it, the same way only his projects
  see his `rules.md`. `/workspace/library/` is the same for one workspace. A part designed for a client never goes into
  the catalogue, and the levels a project may see are the folders mounted into its container, not a rule you follow.
- **Where you edit a part decides who gets it.** A part this client will want in his next machine too goes to
  `/clients/<id>/library/<group>/<part>.py`; something true only for this machine stays in `parts/<node>/`. A purchased
  part that any client could buy, as the maker sells it, belongs in the framework catalogue: say so to the owner (its
  files are not writable from here) and keep it in the client's library until he adds it.
- **The project's copy is not the source.** The platform mirrors the groups you import into `mechanics/cad/library/`
  (at the start of every turn, and after it before check.py and the model build read the files) and records each
  file's level and hash in `mechanics/cad/library/.sources.json`. An edit of the copy stops the sync — you get a
  message naming the file and its source, and `check.py` fails («library») — so make the change in the source; to
  drop an edit of the copy, delete the copied file and the next sync brings the source back. After adding a part to the library you may run `python3 $S/part_library.py sync .`
  yourself to see it in this project at once; the platform runs the same command.
- **The import decides what is copied.** A group nobody imports is not in the project; dropping the import drops it on
  the next sync. A library file's own imports count too (a reworked part reading the catalogue part's sizes brings that
  group along). Only static imports are seen — a name built at run time and handed to importlib is not. A group that is
  in no library this project sees is a sync error naming the group — that is what another client's part looks like
  from here, and it is not something to work around. `mechanics/cad/library/` is the library's folder name: a project's
  own code never goes there (sync refuses to touch an unstamped one).
- **A library edit reaches another project at the start of that project's next turn**, when its own copy is synced: a
  client's other machines are not rebuilt behind their backs, and each project commits the library version it was
  built with. Whose library a project sees is the router's own record of its clients, not `.engineer/roles.json` alone:
  a client written into that file by hand is mounted only after the owner confirms him (`router.py client`).
- **Nothing shadows anything.** The same `library.<group>.<part>` at two levels is a sync error naming both files. A
  part reworked for a client gets its own name and may import the catalogue part to read its sizes.
- A catalogue part carries the maker's STEP when the catalogue has one and a dimensioned envelope with
  `standin_reason` when it does not; its ports and its purchase row are the same either way, so a project does not
  change when the STEP arrives. How to write one: `catalog/README.md` in the framework.

## The machine's app (the client asks for an app)

Any request for an app — "an app", "for my phone", "for Android" (in whatever language the client writes), a control or
operator app — is built ONE way: a Flutter app in its own repo, edited by you in `app-src/` (branch `bot/twin`), built by the router as a
web version (shown next to the 3D in the viewer, BLE simulated) and as an APK on a build worker, released after the test; the
client then downloads the APK from the project page (its download-app button). Why one way: the client installs a real
app on his phone, every app is tested and released the same way, and the platform's maintainer can maintain all of them.

- No `app-src/` yet: run `tg app-new <name>` (`<name>` = short Latin slug of the machine, starting with a letter, e.g. `sorter`). The router creates
  the repo, the workspace and `app-src/` with a Flutter skeleton (package `<name>` with `-` as `_`) in a few minutes and tells you in a platform
  message; until then `tg app-new` answers «creating». Never create an app anywhere else.
- Never offer or build a web page, a PWA, a Web Bluetooth / Web Serial page, an HTML file in `public/` or an app served by the
  ESP32 itself as «the app», not even «for now» or «as a quick version»: a browser-only page does not install and is not
  released through the test.
- Until `app-src/` exists: firmware, the BLE protocol (a wiki page both sides follow) and the app spec (screens, data as JSON).
- Rules for the code, the version bump and the release: skill `web-viewer`, «The shared app and its release».
- Dates: an app needs a Flutter build plus an APK build plus the client's test. Never promise it within hours; say "tomorrow" or
  later and send the APK link when the release passed.

## Git

The platform commits and pushes the folder to the project's private repo after every turn; you never commit. What gets committed:
sources only (model, code, wiki, docs). Not committed: `build/` and every generated type (STL/3MF/DXF/GLB/mp4/zip/preview PNG/rendered HTML),
runtime dirs, secrets, files over 20 MB. `check.py` fails on any of these. Create only files you want in the repo.

**History is yours to read, never to restore.** In `/work` plain git reads the project's history (one commit per turn, newest
first): `git log --oneline -- mechanics/cad/params.py`, `git log -S TANK_IN_W_MM`, `git diff HEAD~5 -- mechanics/`,
`git show <rev>:mechanics/cad/params.py`. It is read-only: commit, checkout, reset, stash fail, and that is on purpose. An
older project `CLAUDE.md` saying «do not run git» means commits; reading history is fine. `app-src/` is the app's own repo:
its history is not here (`git log -- app-src/...` is empty).
- Use it to answer «what changed», «when did we change X», «what was it before» and to find the old numbers and the old
  design a client asks back ("put it back the way it was", "as it was yesterday").
- **Never bring an old version back as files** (`git show <rev>:f > f`, copying a whole old part or `params.py`). Old
  commits may predate the current framework, the part standard, the fit checks; restoring them silently drops what was
  added since. Read the old version, then build the requested shape again in the current model by today's rules, and
  say in the chat what you took from which date.
- History replaces hand copies: no `_v14/`, `*_v2.py`, `old/`, `params_backup.py` in the project, and no copies in
  `build/old/` either (`build/` is cleaned).

## Exports on demand (from the model, into `build/`)

| For | Format | How |
|---|---|---|
| 3D print | STL / 3MF | `derive.py print` via kit.json, built by the router (one STL per `process: print` part, from its STEP); the client gets it by `tg link <part> --download` / `tg link --kit print --download`, with a render and the numbers (`fdm-print`) |
| laser / bending | DXF + 2-sheet PDF | `derive.py laser` (shop-drawings: CypCut DXF, Latin kit names with the `workshop` drawing profile, drawing from `thickness`, `material`, `code`, `weld` of the part) |
| web / client view | glTF/GLB + page | ALWAYS the standard `web-viewer` assembly page (step-import of the machine STEP with named parts -> `build.py`, `--motion`/`--world` to extend it) at the root of `public/`: the ONLY model of the machine; never a hand-made HTML/three.js page of the machine, not even a linked «animation» page; missing features go into the viewer |
| operator / phone app | Flutter app + APK | ALWAYS the platform's app path (see «The machine's app»): `tg app-new`, then code in `app-src/`; never a web page, PWA, Web Bluetooth page or HTML in `public/` as «the app» |
| CAD exchange | STEP | `derive.py step` (all part STEPs in one assembly, a group per node) |
| assembly drawings | PDF per node, zip | `derive.py assembly` (shop-drawings `assembly_drawing.py`): exploded along the dockings, balloons, table «No., part, code, qty» (in the project's language), big nodes in steps; the router offers it to every kit.json that runs derive.py, no scope to declare |

Never hand-edit an export: change the model and re-export.

### Download kits: `kit.json` + the export manifest (the client downloads files himself from the model page)
Files for the client are never sent from an old export folder. The model page has download buttons (skill `web-viewer`); the router builds
them from the current model. The project only provides the export, by this contract:

- `kit.json` at the project root (plus `"site": "<command: incremental model build + the viewer published to public/>"`, e.g. `sh sim/site.sh`:
  the router runs it after every turn that changed the files `inputs` match, numbers the result model vN and then remakes the kits;
  it must write `public/config.json` (web-viewer `build.py` does); `site_timeout` (s, default 1800)):
  `{"export": "<shell command; runs as a worker job, cwd = project root>", "inputs": ["<glob of every file the parts come from>", ...],
  "manifest": "build/kit/manifest.json", "timeout": 3600}`. `inputs` = params, part sources, vendor STEP, drawing scripts: anything whose change
  changes a file. The router hashes them; a change = a rebuild on the next click. Do not list the export's own output.
- **Per-scope exports (do this: one full export makes the purchase list wait for every laser drawing).** `"scopes"` gives each button
  its own command, inputs and freshness: `bought` (the Excel), `step` (the model STEP), `print`, `laser` (laser + bent). Each entry:
  `{"export", "inputs" (default: the top-level ones), "manifest" (default `build/kit/<scope>.json`), "timeout", "part", "node"}`.
  A scope's export builds ONLY that scope and writes its own manifest piece (same schema; the router takes from it only its scope:
  print rows / laser+bent rows / `bought` + `bought_xlsx` / `model_step`, plus `other` rows not listed yet) and merges the pieces. A change to a file only one
  scope's `inputs` match leaves the other buttons fresh, so list per scope exactly what it reads: `bought` = the BOM sources, not
  the drawing scripts. `part` / `node` (print, laser): a command with `{part}` / `{node}` (the viewer's id / node name, shell-quoted
  in; write `{part}`, `{node}`, `{manifest}` bare, never inside quotes) that builds that one part / node and writes its piece to
  `{manifest}`; without them a part download builds its whole scope.
  Scopes you do not declare are built by the top-level `export`; with every scope declared, drop it. Scopes run as separate jobs,
  possibly at the same time on different machines (the model cache is safe to share). A code model declares every scope as a
  `derive.py` call (`check.py` fails any other command); `bought` reads rows only, no geometry. A scope whose `export` uses `--where FIELD=VALUE` must pass the same `--where` in its `part` command, or a per-part download builds/checks a part outside the scope's subset. Example (this is what `scaffold.py` writes):
  ```json
  {"inputs": ["mechanics/cad/*.py", "mechanics/cad/parts/**/*.py", "mechanics/cad/vendor/*"], "timeout": 3600,
   "scopes": {
    "bought": {"export": "S=${DE_ROOT:-/opt/design-engineer}/skills/machine-project/scripts; python $S/derive.py bought build/kit/bought.json", "timeout": 300},
    "step":   {"export": "S=...; python $S/derive.py step build/kit/step.json"},
    "print":  {"export": "S=...; python $S/derive.py print build/kit/print.json",
               "part": "S=...; python $S/derive.py print {manifest} {part}"},
    "laser":  {"export": "S=...; python $S/derive.py laser build/kit/laser.json",
               "part": "S=...; python $S/derive.py laser {manifest} {part}"}}}
  ```
  `derive.py SCOPE MANIFEST [PART_ID]` (cwd = project root) first brings the model up to date, writes the manifest piece below, reuses every
  result whose inputs did not change and prints `[derive] <scope>: parts K, reused R, computed C`. A scope `viewer` (used by `sim/site.sh`)
  builds `model.glb` from per-part meshes.
- The export writes ALL manufacturing files fresh (print STL in print orientation, laser/bent DXF + PDF drawing + STEP per part, the
  purchase list `.xlsx`, the model STEP) and then `manifest.json`:
  `{"parts": [{"id": "<the viewer's part id>", "name": "<the part's name>", "node": "<the viewer's node name>", "process": "print|laser|bent|bought|other",
  "qty": 2, "thickness": 2.0, "material": "PETG", "files": ["build/kit/...stl", ...]}], "bought_xlsx": "build/kit/bought.xlsx", "model_step": "build/kit/model.step"}`.
  `id` = the viewer's part id (`__viewer.parts()`), `node` = the assembly node's name as in the model STEP (`__viewer.nodes[n].userData.name`):
  they must match exactly, or the card buttons do not appear.
  One process per kit: a laser/bending row is metal only, a print row is plastic only (PRINCIPLES §4).
- Bought parts: list them in the manifest as `"bought": [{"name", "code", "qty", "unit", "supplier", "link", "kind", "class", "node"}]`, one row
  per BOM position. The router generates the Excel from it (one sheet per kind, named in the project's language: fasteners, electrical,
  motors and drives, pneumatics, bearings and guides, profile cutting, other) by `kind` ∈ fasteners | electrical | motors |
  pneumatic | bearings | profile | other, taken from the model's BOM (PRINCIPLES §3); words in `class`/`name` are only a fallback for an
  unmarked row. Do not build the Excel yourself.
- Derived results live in `build/derived/<key>/`, keyed by the part STEP, the deriver's code and the part fields it uses; a rebuild of one node recomputes
  only the files of its parts. The manifest still lists every file. Never write kit files from a project script.
- Exit 0 only when every file was written and checked. A failed export is reported to you by the router (`kit_export_failed`) and the page
  says the files could not be made: fix it.
- When the client asks for files in the chat, answer with the page link and which button to press; send files yourself only if he insists.

### Machine schemes: a projection of the model (the electric and pneumatic scheme buttons)
A scheme is not a drawing of yours: the framework draws it from the model, the same way the drawings come from it, and the router
builds it after every model change into two buttons of the page's download panel. The model holds three things:
- **Elements** = the parts whose `kind` is `electrical`, `motors` or `pneumatic`, one per placement, under the same id as in the viewer
  and the purchase list. A part of those kinds that is not on a scheme (cable duct, DIN rail, rod end, shock absorber) says
  `scheme=False` in its part file.
- **Ports** of a part, in its part file: `P(..., ports={"+V": "+24 V", "-V": "0 V", "PUL+": "STEP", "DIR+": "DIR"})`
  (name -> a short label, may be ""); a valve has both its coil (`"coil+"`, `"coil-"`) and its air ports (`"P"`, `"A"`, `"B"`).
  A board of our own (KiCad) has its connector pins as ports (`"J3.2"`).
- **Connections**, once, in `mechanics/cad/connections.py` (plain Python, no build123d; it may `import params` and derive the list
  from params, e.g. one STEP wire per driver from the pin map):
  ```python
  WIRES = [{"from": "psu.+V", "to": "drv_press.+V", "net": "+24 V", "spec": "1.0 mm²"}, ...]   # electric scheme
  HOSES = [{"from": "frl.OUT", "to": "valve_vac.P", "spec": "PU 6x4"}, ...]                   # pneumatic scheme
  ```
  An end is `"<placement>.<port>"`. Never retype a number that is a model field: compute it from params.
- The sheets are drawings, laid out by the framework (ELK): the electric scheme in symbols (motor, coil, button, breaker,
  battery) and blocks with their terminals, every wire a line with its net's name; a supply net on 3+ terminals ("+24 V", "0 V",
  "GND") is a supply / ground mark at each terminal, a net between two controllers a named flag. The pneumatic one in ISO 1219
  symbols (filter unit, 2/2, 3/2 and 5/2 valves with solenoid and spring, ejector, suction cup, cylinder, tank, fitting, tee,
  silencer), hoses H1... Then the table of all connections. The model page shows the drawing (the button opens it, a PDF download
  in it). One look in every project; you never draw or lay out a sheet.
- The symbol comes from the part's names and ports. When it guesses wrong, say it in the part file: `symbol="valve_5_2"` (one of
  box, motor, coil, button, button_nc, breaker, battery, source, frl, filter, regulator, valve_2_2, valve_3_2, valve_5_2, ejector,
  cup, cylinder, tank, silencer, fitting, tee). Give a part a human `title` (or `card.name`): that is its name on the drawing.
- `check.py` (rule «schemes») checks both ways after every model build: every end is a port of a model part, and every element
  is on a scheme. Fix the model (ports, `scheme=False`, a wire), never the check.
- The same WIRES, with port locations and waypoints, are the wires in 3D (below, «Wires in 3D»): the electric scheme's PDF
  then ends with the harness drawing and the cut lengths.
- You never run `schemes.py`, publish a PDF or link one from the page (`build.py --link`, config.json `links`): `check.py` fails it.

### Virtual device: a projection of the model (the web-viewer's device panel)
A device you can click on its page, running the firmware that goes to the chip, is not drawn by you either: the model describes it.
- A part says what it is in the simulation, in its part file: `P(..., ports={...}, sim={"model": "esp32", "fw": "player"})` = an MCU
  board running the firmware build `player` (its ports `GPIO<n>` / `IO<n>` are pins, `GND` / `3V3` / `5V` / `VCC` rails;
  `{"model": "avr"}` = a Nano / Uno, ports `D<n>` and `A<n>` = pin 14 + n);
  `sim={"model": "button"}` (two contacts), `"encoder"` (ports A, B, C, optional SW + SW2, `detent`), `"st7789" | "st7735" | "ili9341"`
  (ports SCL SDA DC CS RST BLK VCC GND; `w`, `h`, `x_off`, `y_off`, `ips`, `glass`: centre / normal / up / size of the active area in
  the part's own mm), `"ssd1306"` (SCL SDA VCC GND, `w`, `h`, `addr`). `bounce_ms` on a button or on an encoder's push contact (its
  A/B channels do not bounce). Two MCUs: an element wired to both says `board`.
- The wiring is the one of the electric scheme: `WIRES` in `mechanics/cad/connections.py`; a net named like "GND" / "0 V" / "+3.3 V" is
  a rail. Settings of one placement that are not part properties (keyboard key, label) go to `SIM = {"btn_play": {"key": "Space",
  "label": "Play"}, "enc": {"keys": ["ArrowLeft", "ArrowRight"], "push_key": "Enter"}}` in the same file.
- The model build (`cad_incremental.py --model`) writes `build/model/device.json` (`scripts/device.py`); `check.py` rule «device» fails
  on a sim part not wired to its board, an unknown model, an MCU without `fw`, a net on two pins of one board or on GND and 3V3 at once,
  a `SIM` key that names no sim placement. With a problem there is no device.json at all (a page never runs an older wiring). Fix the
  model, never the check.
- The firmware lives in `electronics/firmware/<board>/` (PlatformIO). Its wasm build for the page goes to `build/fw` (virtual-controllers
  build.mjs `--libdeps .pio/libdeps/<env>`, on the Mac / PC, never on the VPS) and the site gets `sim/site.json`
  `{"device": "../build/model/device.json", "fw": "../build/fw"}` (`build.py --sim sim`). Example: virtual-controllers `examples/player_demo`.

### Wires in 3D: routed inside the machine, from the same model (`scripts/harness.py`)
The scheme says what connects to what; inside a small, crowded enclosure that is not enough: how each wire runs, how long it
is cut, whether the bundle fits the gap it passes and bends without breaking, and whether a wire to a moving part is pulled
tight. All of that is computed from the model after every change, never measured or drawn by hand. Route every wire of a
machine that has an enclosure, a board and wires to its parts.
- **Port locations**, in the part file next to its `ports`: `ports_at={"J1.1": ((x, y, z), (dx, dy, dz)), "VCC": (x, y, z)}` =
  where the wire leaves the part, in the part's own coordinates (mm), and the direction it leaves in (default +Z): the pin of a
  header, the terminal of a motor, the lug of a switch. Compute them from the same numbers the shape uses (pin pitch,
  connector position), never retype them. A wire is routed when both of its end ports have a location.
- **Routes** through waypoints you write in `mechanics/cad/connections.py`, as you place parts:
  ```python
  WAYPOINTS = {"slot": (0.0, P_.SLOT_Y_MM, P_.SLOT_Z_MM),             # machine frame: a point in a wall's cutout, along a wall
               "car": {"at": (-15, 0, 30), "on": "carriage"}}         # on a placement, its own coordinates: moves with it
  WIRES = [{"from": "board.J1.5", "to": "switch.NO", "net": "LIMIT", "spec": "0.14 mm²", "via": ["hdr", "slot", "car"],
            "loop": 30}, ...]                                         # loop = mm of service loop for a wire to a moving part
  HARNESS = {"slack": 0.1, "ends_mm": 10, "bend": 4}                  # optional: the defaults
  ```
  The wire leaves each port straight (a lead of `bend` x its diameter, at least 5 mm), then runs through its `via` in order.
  Wires that pass the same two waypoints one after the other share that segment: a **bundle**, its diameter from theirs
  (`od` mm on a wire, else from its spec: mm², AWG, "4x0.25 mm²" = a cable). Put a waypoint in every opening a bundle passes
  (its centre), at every clip, and where it turns; place points off a wall by the bundle's radius. Name placements in the
  node (`place(p, name="board")`) so the ends read `board.J1.1`. A per-wire `bend` (10 for a wire that flexes) overrides;
  `stock` (the purchase-list name of the wire, default "Wire <spec>") and `code` (its article) go to the cut list.
- **What it gives**: each wire's route (corners rounded at the bend radius), its length, and its **cut length** =
  (route + loop) x (1 + slack) + `ends_mm` per end, rounded up to 5 mm. The purchase list (bought scope) has a cut list of
  wires per spec: one row per cut length naming its wires, a total in metres. The electric scheme PDF gets the **harness
  drawing**: the routes flat with the connectors, pins and wire ids, segment lengths and bundle diameters, then the table
  of wires with route, loop and cut length. The model page shows every wire as a tube coloured by its net (switch
  «Wires» in View); a wire to a moving part follows the cycle.
- **Checked after every build** (the viewer build runs it next to the part-overlap check, with the cycle it publishes;
  `check.py` rule «harness» fails on any of them, a failing turn comes back to you like a clash):
  `clash` = a bundle (its full diameter) goes through a part: add a waypoint around it; `gap` = it does not fit through
  where a waypoint puts it (a slot, a hole, a clip smaller than the bundle): widen the opening or route a part of the bundle
  elsewhere; `bend` = no room for the bend radius (bend x the bundle's diameter) at a waypoint, at rest or anywhere in the
  cycle: move the waypoints apart, soften the turn; `stretch` = over the motion cycle the route gets longer than the rest
  route + `loop`: give the `loop` it names, or route the wire along the moving part; `route` = a reference that does not
  resolve (a `via` name not in WAYPOINTS, `ports_at` of a port the part does not have, `on` a placement that is not
  there). The wire leaves its own part at its port, so the lead is not measured against that part; everything else is.
  Fix the model (waypoints, ports, openings), never the check. `python3 $S/harness.py . [--motion build/motion.json]` runs
  it alone; the report is `build/harness/report.json`. Example: framework `examples/wired-box` (board, motor, jack, a
  carriage with a limit switch, wires through a divider's slot).
- Not yet: automatic routing (shortest path through the free volume, along walls and ducts) - you place the waypoints.

## AAS: how it is built and checked

- **Skeleton** (scaffold): Nameplate, TechnicalData, empty PartCatalog, HierarchicalStructures (EntryNode only). Valid against aas-test-engines from minute zero.
- **build123d machine**: `params.py` (UPPER_CASE numbers, unit = name suffix) and `build/model/parts.json` (written by the rebuild) go into the AAS with
  `aas_sync.py` (TechnicalData.ModelParameters with `source` + `unit` qualifiers; PartCatalog + BoM by node: a part under every node that places it, BulkCount = its placements there; flat when nothing has placements; SameAs). One way: never edit those in the AAS by hand.
- **STEP machine**: `step_import.py` then `aas_build.py build --machine .` (`step-import/AAS.md`); it rebuilds PartCatalog, HierarchicalStructures, Models3D from the STEP,
  carries Nameplate/Kinematics/... over, writes `build/<machine>.aasx`. It needs `aas/seed/machine.facts.json` (and measured kinematics) the first time. Also code-CAD parts can be
  exported to STEP and imported this way when the machine grows into a big assembly.
- **Interfaces** (BLE etc.): `electronics/interfaces/*.json` listed in the facts, built as AssetInterfacesDescription.
- **Check**: `check.py` = schema (aas-test-engines) + structure; when `build/<machine>.aasx` exists also `aas_check.py` (round trip, derived files). Needs `uv` (runs Python 3.12 itself).

## What check.py verifies

The router runs `check.py --no-validate --json` itself after every turn that changed project files; a FAIL comes back to you as a
system item saying the standard is broken (with the fails): fix it first, before client tasks (it is repeated when the FAIL set changes, and every few turns).

Layout (README, .gitignore, aas/; WARN: layer folders without README, no wiki/index.md, unknown top-level folders) / no generated file type would be committed /
no committed file over 20 MB / no screenshots outside build/ outbox/ / AAS present, sound, valid (+ round trip) / firmware folders are PlatformIO projects /
wiki lines with a number+unit and no `model_field` in backticks (WARN, listed; client quotes in `wiki/client/` are exempt).
Model gates (`cad_gates.py`, FAIL, each with its fix): **CAD layout** = `mechanics/cad/params.py` + node folders `mechanics/cad/parts/<node>/`, each
with a no-argument `build()` / `build_*()` returning the parts (`parts/_<name>.py` = helpers; any other `parts/<x>.py` FAILs); **kit.json** present and valid (contract above);
**model parts** (code model = `assemble.py` + `parts/` AND `kit.json` already calls `derive.py` or `build/model/parts.json` exists; an old-template project is not judged until it migrates) = every part dict literal in `parts/*.py` carries a `process`, ids are unique across nodes, and a built `build/model/parts.json` has unique ids and a valid process; **part files** (node folders) = a node's `__init__.py` returns no part with a shape (it places part files), a part file's `build()` returns one part, no `qty` on a placed part;
**library** (only when the project imports a library group or has a mirror) = `mechanics/cad/library/` matches its `.sources.json` (a library part is edited in the library, not in the project's copy), holds every group the project imports, and is listed in every `kit.json` `inputs` (`"mechanics/cad/library/**/*"`);
**kit via derive.py** (code model) = every kit.json command runs `derive.py` (directly or through a project shell script); **bought kind** = every `bought`
row of the export manifest has `kind`; **single-process kits** = known `process`, print rows plastic (STL/3MF), laser/bent rows metal
(no STL, no rubber/silicone/plastic). A missing manifest is a WARN (built on the first download). Which gates apply:
build123d (params.py or build123d code in mechanics/cad) = all; **legacy STEP-import, not ported yet** (the designer's STEP directly in
mechanics/cad/, no params.py; a new STEP is ported instead) = kit.json + manifest gates, no build123d layout;
no model yet = none.
Model fits (`fit_checks.py`, #47 / #45; `python3 $S/fit_checks.py .` runs them alone). Static, from `build/model/parts.json`:
**bought = drawn** = a bought part's `src` and the product its row orders are one product, no analogues (FAIL); a part with no
`src`, or a row that links a search instead of a product, is a WARN. **bought part verified** = every bought fastener / profile /
bearing records its `verified` check against the maker's drawing or STEP (WARN). **bought fastener pairs** = every thread size in
the list has both halves — a nut with no screw, or a screw with no nut, T-nut or rivet nut, is a WARN (the thread may be cut in a
part of the model, and then the drawing has to say so). Geometric, from the part STEPs along the joint graph: **fastener fit** =
for every screw docked into a hole joint, the hole against the screw (a clearance hole needs d + 0.1, a tapped one the tap drill
d − pitch; anything between the two is a FAIL — that is M5 in the T01's 4.6), and its length against the depth the model draws
(a screw standing deeper than the drawn void bottoms out: FAIL when that void is a drawn hole, WARN when the void simply is not
drawn). The seat is the narrowest hole the shaft can reach from the joint's face, so a counterbore or a countersink over it does
not hide the hole that matters. WARN, never FAIL: no hole on the axis at all, a hole the shaft cannot reach, material right at
the head's bearing face, a hole drawn at exactly the screw's size (draw the clearance you mean), a thread engaged under 0.8 d, a
hole over 1.6 mm wider than the screw, a size whose pitch is not on record, and anything OCC could not measure. **docked parts
clash** = a docked pair does not overlap (FAIL over 2 mm³), except the thread of a screw in a nut, a T-nut or a tapped hole,
which `fastener fit` has instead; a pair docked with a gap over 0.5 mm is a WARN. What a male fastener is comes off the part's
own fields — `kind` fasteners plus a thread size it spells — not off a word list, so dowel pins and "threaded rod" are checked too. The lateral
play of a key or a boss inside a slot — the other half of that bracket loss — is NOT checked (that needs a clearance measure): state such a fit
with an assert on the maker's section, the way `params.py` does. The geometric rules need build123d and the built model: a full
`check.py .` computes them (per pair, cached in `build/fit/` together with one bounding box per part, the report in
`build/model/fit.json`) while the router's `--no-validate` run reports what the last full run recorded, and WARNs when that
report is of an older model **or was written by other fit rules** — a corrected tolerance takes effect on the next full run,
it is not reported from the old report. A fastener's cached verdict is keyed on every part along its screw's axis, not just on
the pair, so editing the part a screw bottoms out on does not leave the old verdict in place.
**Wires in 3D** (`harness.py`, rule «harness», read from the last build's `build/harness/report.json`; a report of another
model is a WARN): every routed bundle clear of the parts and through its gaps, no bend under `bend` x its diameter, no wire
stretched over the cycle; see «Wires in 3D».
**Overlaps: the model build checks them itself** (`clearance.py`, #19; you never run it). Every site build ends with the
viewer build (web-viewer `build.py`), and on a code model that build first checks that no two parts share space: fixed parts
where the model stands, moving ones over the whole cycle it is about to publish, grouped as the page moves them. A pair
overlapping by more than 2 mm³ (an exact boolean; touching faces are 0) is a **defect of the build**. It does not stop
publishing: the model page and the pinned board show a red mark, and the model-ready notice comes with a build-defect
line and the pairs - fix them before anything else in the model. The build log has lines like `CLASH
body_cabinet [static] x holed_lever_1 [lever]: 15.7 mm3 at t 0.5726 s of the cycle (overlapping 0.5125..0.6612,
2.2388..2.3875 s), at (1.2, 627.5, 1029.7) mm` - what your change made first, then the pairs that clashed before it; all of
them are in `build/clearance/report.json`. In a project with a GitHub repo the router keeps the pairs in ONE issue
about part overlaps (label `clashes`): its body is rewritten after every build, it closes itself at 0 and reopens when
pairs come back; its executor works them, other lanes fix only a pair their own change made. **A machine already built in
hardware** (router flag, `MACHINE ASSEMBLED` in your prompt), or a model ported from the client's CAD (`CLIENT CAD`), changes only with the client's OK: sort each pair into a drawing
error or true to the hardware, ask the client in plain words, change after his yes. A pair the client confirmed as meant
goes to `wiki/client/clash_accepted.json` (`[{"a", "b", "why", "confirmed_by", "date"}]`, placement names as in the report):
it is no longer counted, on the page or in the issue. That file is the client's word only; otherwise there is no list
of pairs that may touch: **a contact the machine means comes from a joint.** A pair docked to each other is `docked parts
clash`'s (above); a screw's overlap that lies inside its shank along the axis of the joint it is docked by is its thread or its
passage through a part behind its host (`fastener fit` judges the hole and the depth). So dock a part to what it sits in, and
dock a screw by a joint whose -Z is its axis (`templates/screw.py`): a vendor knob docked by a datum joint threads into its nut
as a clash. A `product` part (what the machine handles) is not checked: it is drawn at one instant of its travel, and
neither is a client's STEP model (`model_from_steps.py`): its overlaps are the client's data. Links a world module moves by pose tracks (world data `CRANK_TRACKS` = {n, period, nodes: {placement: 12*n floats, row-major 3x4 CAD mm}}, e.g. crank links) move so in the check too: a tracked part takes its track instead of its group and is measured at the track's samples, where its pose is true; between samples it is not measured, so a fast link needs a dense track (e.g. 96 over 2.13 s). On one machine with 35 tracked parts this took 10 of its 12 clash-issue pairs away (rod ends on their slider pins, a link rod) and added none; the one left was a real one. Only changed pairs are measured (cached in `build/clearance/overlaps.json`): a pneumatic machine of 902 pairs, 70 s
from cold, 4 s with nothing changed, 13 s after lengthening the two levers (24 pairs). Not seen: two docked parts that swing into each other, and a closed loop's closing
pin, which no docking states — the parts carry it twice (`cyl_trunnion` / `cyl_body_cb`, 1349 mm³), so it is a clash today.
`--json` = read-only run for the router (one JSON object on stdout, wiki/index.md untouched). "Would be committed" is computed
with git even when the platform keeps the git dir outside the project. Exit 1 on any FAIL.
KB lint (`kb_lint.py`, rules in `project-wiki`): FAIL-level — page > 12 KB / 150 lines, table row > 300 chars, register row > 200 chars or
register > 40 KB, `wiki/client/rules.md` > 6 KB / 40 lines, `/clients/*/rules.md` > 6 KB, CLAUDE.md > 8 KB or «Current state» > 5 lines,
handover > 8 KB, raw files in wiki/; WARN-level — page older than the latest D pointing at it, orphan, no outgoing links, no frontmatter,
superseded row not collapsed, feedback without a rule/gate/fix, R row repeating a C row or a principle. A normal run prints all of them
as WARN (exit code unchanged, FAIL-level ones tagged `[FAIL with --kb]`); `--kb` fails on them. Every run rewrites `wiki/index.md` from the pages' frontmatter. Fix the project, not the checker, unless the checker is wrong: then fix it and say so.
