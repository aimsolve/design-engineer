---
name: cad-build123d
description: Parametric machine CAD in build123d with one params file as the contract — project layout (params → part modules → assemble → STEP + meshes → three.js viewer), moving groups and home pose, render / animation frame / clearance-verify patterns, purchased-part rules, and traps that cost hours (single-node assemble overwriting whole-machine outputs, fillet on the cutter, tapers that self-lock, helicoid vs flat-panel sweeps, blank frames, page bloat). Use when starting CAD for a new machine or fixture, adding a part or a moving axis, checking clearances, rendering or animating, or picking a cylinder / guide / spring.
---

# Parametric machine CAD (build123d)

## Project layout that worked

In a machine project the code lives in `mechanics/cad/` (params.py, parts/, assemble.py) and every output goes to `build/`, not `out/`;
scaffold with `machine-project` (`scaffold.py --cad build123d`), feed the AAS with its `aas_sync.py`. There a node is a folder
`parts/<node>/` with one file per part (`<part>.py`, `build()` -> one part in its own coordinates) and `__init__.py` placing them
(machine-project SKILL «Code model»). The tree below is the older single-file structure of a standalone model.

```
cad/params.py ──► cad/parts/<node>.py (build() → [{name, shape, color, group}], world coords, mm, HOME pose)
                        │
                cad/assemble.py ──► out/<machine>.step, <node>.step, meshes.json, params.json
                        │
                build.py (inlines three.min.js + meshes.json + params.json as `var P`)
                        │
                page.tpl.html ──► <machine>.html (self-contained viewer) ──► server
```

- **params.py is the contract**: axes and origin, product numbers (one hand-entered block, everything else derived),
  anchors, clearances, moving groups `GROUPS = {group: (axis, travel)}`, `GROUP_PARENT` for riders, colours. A new
  moving group not in `GROUPS` must make assemble assert.
- Write the **axes in words from the operator's position** in the params header (X his right, Y up, Z toward him…).
  The client will speak in his own frame (`client-talk`).
- Part modules: one solid per part, home pose, English comments, changelog in the docstring
  (`vNN (date, source): what`), **no product number typed in a module**.
- The viewer gets numbers only via `var P`; never a second copy in the HTML. Motion = `pose(t)` translating whole
  groups from `OPS` time windows; things a rigid group can't show (hoses, springs, belts) are redrawn live.
- Always run build123d in an isolated env: `env -u VIRTUAL_ENV uv run --isolated --no-project --with build123d python …`.

## Smooth shells: not in build123d

A housing held in the hand (blends, a thumb dimple, a waist, a wedge, a soft edge that changes radius) fails in OCC: fillets
refuse, the wall offset fails after a blend, a sphere-cut dimple comes out as a crater with a fillet ring (seen in a trial). Make that
part a mesh part: SDF skin + exact build123d fit features, `meshpart.py` (machine-project SKILL «Smooth housings: mesh parts»).
Plates, brackets, anything with a simple outline stay in build123d.

## Looking at results

| Tool | Use |
|---|---|
| Static render of the STEP (optionally a part substring + `POSE="group=frac"`) | geometry, close-ups, groups displaced by a fraction of travel |
| Headless viewer frame at cycle time T with an orbit camera (Playwright) | animation; name frames `<topic>_f<T*100>.png` |
| URL hash `#t=…&cam=yaw,pitch,dist,tx,ty,tz&hide=a,b` | same in a browser, hide occluders |
| `node harness.js` smoke test | catches a throw in scene build / `pose(t)` (blank canvas otherwise) |
| Numeric load of shapes | bounding boxes, `&` intersections, `.volume`, `.distance_to` |

**Read every PNG before claiming anything.** A camera inside a solid gives a blank frame (a critic once lost 8
frames this way).

## Clearance verify script — what it should check

1. One solid per part.
2. Product envelope (all batch-tolerance corners) swept along the whole path vs every rigid part.
3. Sprung parts at max deflection vs rigid parts.
4. Sheet/flap panels swept as **flat panels set by their leading edge** (see traps) vs everything near them.
5. Every moving group swept 0..1 of travel vs statics; list EXPECTED contacts (rod in its own body, guide rods in
   their bushes) so real ones stand out.
6. Static-static overlaps of new parts.

Points 2 and 5 over the real meshes and the whole cycle: `sim` -> «Collisions over the whole cycle» (`cycle_clearance.py`,
python-fcl). Print minimum distances, keep a reference output, **diff against it after every change**. Give it a pass/fail exit
code and no absolute paths (`Path(__file__).parents[n]`) — the first project's verify had neither.

## Purchased parts

- Model it from the maker's STEP, not a box: `part-library` (the part library, the platform's part-search module when connected, or a maker's STEP the user supplies).
- A standard catalog stroke from the maker's stroke table; say "made to order" if not.
- Rod engaged in the body over the full stroke; body envelope drawn to the catalog.
- Floating joint between rod and carriage; carriage on a rail or guide rods.
- Cushion + flow control + a reed switch on the end that matters.
- Forces at the REGULATED pressure, not 5 bar.
- Verify each code on the maker's site at order time and cite the number used; critics checking from memory are wrong
  often enough.
- When a part changes: module, viewer card, BOM page, STEP zip README — all four.

## Standard hardware: bd_warehouse (screws, nuts, washers, bearings, holes)

When: an ISO/DIN screw, nut, washer, ball bearing (608, 6001…) or the hole for one. A drawn cylinder guesses the head and
the bore; bd_warehouse builds the standard part to the table. A catalogue part with its own maker STEP (motor, guide,
pillow block, cylinder) still comes from `part-library`. In the worker's main python (installed, no `--with` needed):

```python
from build123d import *
from bd_warehouse.fastener import SocketHeadCapScrew, HexNut, PlainWasher, ClearanceHole, TapHole
from bd_warehouse.bearing import SingleRowDeepGrooveBallBearing, PressFitHole
screw = SocketHeadCapScrew("M4-0.7", length=12, fastener_type="iso4762", simple=True)  # DIN 912; origin = under the head, shank -Z
nut, washer = HexNut("M4-0.7", fastener_type="iso4032", simple=True), PlainWasher("M4", fastener_type="iso7089")
brg = SingleRowDeepGrooveBallBearing("M8-22-7", "SKT")                                   # 608: bore-OD-width; z 0..7
with BuildPart() as plate:
    Box(60, 40, 6)
    with Locations((-20, 0, 3)): ClearanceHole(screw, fit="Normal", counter_sunk=False)   # Ø4.5 through
    with Locations((20, 0, 3)):  TapHole(screw, depth=6)                                   # Ø3.3 tap drill
    with Locations((0, 0, 3)):   PressFitHole(brg, fit="Normal", depth=6)
parts = [plate.part, screw.moved(Location((-20, 0, 3))), brg.moved(Location((0, 0, -4)))]
```

- Sizes: `SocketHeadCapScrew.sizes("iso4762")`, `.types()` (`HexHeadScrew` iso4017/4014, `CounterSunkScrew` iso10642,
  `ButtonHeadScrew`, `SetScrew`, `HeatSetNut`; nuts iso4032/4035, washers iso7089/7090/7092); bearings
  `SingleRowDeepGrooveBallBearing.sizes("SKT")` (also angular-contact, cylindrical/tapered roller, capped). Also in the
  package: `flange`, `pipe`, `gear` (involute spur), `sprocket`, `shaft_key`, `retaining_ring`, `o_rings`, `open_builds`.
- **Always `simple=True`** for screws and nuts in the machine model: the real thread is 4.5× the triangles (9k -> 41k for
  one M4×12) and bloats the viewer; thread only in a close-up render.
- **`ClearanceHole` counterbores for the head by default** (`counter_sunk=True`): pass `False` for a plain hole in a sheet.
  Laser sheet holes follow `shop-drawings` (tap drills there are `taps`); the diameters match (`screw.clearance_hole_diameters`
  `{'Close': 4.3, 'Normal': 4.5, 'Loose': 4.8}`, `tap_hole_diameters` `{'Soft': 3.3, 'Hard': 3.5}` for M4).
- Place the screw from params (the same point as the hole), not from `screw.hole_locations` (a TapHole records it 1 mm off).
- BOM / viewer card: the standard name (`DIN 912 M4×12`, `608-2RS`) from `screw.info`, kind "bought".

## Traps (each cost hours)

- **Single-node assemble overwrites whole-machine outputs.** `assemble.py <node>` rewrote meshes.json, params.json and
  the machine STEP with one node; a following build shipped a third of the machine. Always run the full assemble
  before build / deploy / an unfocused render.
- **Fillet the block edge AFTER subtracting the pocket, never the cutter.** A rounded cutter made an inward lip that
  closed the pocket mouth by 2 mm; the client caught it from a picture.
- **Tapers self-lock when tan θ < μ.** Check it for every taper against the real material pair (first project:
  paper on steel μ ≈ 0.3 vs tan 10° = 0.18 → any interference wedged at ~100 N per wall; 0.05 clearance locked,
  0.15 didn't). Keep a real clearance and step every joint OUTWARD downstream.
- **A folded flap turns as one flat panel**, set by its leading edge — not as a helicoid following the guide. The
  helicoid check passed a post by 1.5 mm that the flat panel hit.
- **A swung tip whips past the swept range** (+3.6 mm past 80° on a 28 mm flap). Sweep the full range + margin.
- **Interlocks are geometry too**: if a pusher must be back before a press closes, put the reed switch in the model
  and the BOM.
- **Viewer**: log depth buffer disables `polygonOffset` → paper z-fights unless there is a real gap bigger than the
  tessellation; helical springs and fine tessellation bloat the page (keep it under a few MB).
- **Docs contradict themselves** when they grow by appending rounds; params.py wins.
- Parallel module agents: one per module, each runs only its own node; the integrator runs the full assemble.
- Generated output (`out/`, STEP/STL/3MF, meshes, frames, previews) goes to the project's `build/`, is exported on
  demand and is never committed (the platform commits sources only, after every turn); regenerate, never hand-edit.
  Parts and `params.py` live under `mechanics/cad/`, numbers in the AAS.
- The product path is reserved space: only the product and the tools acting on it live there.
- Add every new part near the product path to the verify script's list of new parts.
- Set a written minimum clearance to moving panels up front (the first project had none and argued each case).
- Viewer UI text in the client's language, code and comments in English.
- Headless frames: `python -m playwright install chromium` once per machine.
- Renderers from other plugins are often called by an absolute plugin-cache path; it breaks on a plugin update or
  another machine — resolve the path at run time.

## Scaffold

- Viewer (build.py, page.tpl.html, shoot.py, harness.js, controls test, project hook): use the `web-viewer` skill and
  copy its `templates/viewer/`.
- CAD side: copy and adapt the framework's demo project `examples/nema-bracket` (`mechanics/cad/`): `params.py`
  (header with axes in words), `assemble.py` (GROUPS assert, per-node outputs; `title`/`desc` per part for the viewer
  cards), `parts/`. A verify script: relative paths only, with an exit code.
