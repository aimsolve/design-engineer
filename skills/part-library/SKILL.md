---
name: part-library
description: Use for EVERY purchased part at modelling time, including the first concept picture — and whenever the client asks for the real / bought part, to download its 3D model, to put in a motor / cylinder / bearing / MGN guide / NEMA stepper, or a box stands where a bought part belongs. Puts the maker's real CAD into the model instead of an invented box (motors, gearboxes, bearings and housings, pneumatic cylinders and valves, linear guides and carriages, ball screws, couplings, sensors, connectors): the framework's part library first, then the platform's part-search module (`parts search` / `parts get`) when one is connected, or a maker's STEP the user supplies; build it into the assembly. What to do when the search fails.
---

# Real parts from the maker's CAD

A purchased part drawn as a brick is a guess about its envelope, mounting holes and shaft. The maker's model is not.
Rule: if a catalogue part exists, the model uses its STEP **from the first concept on** — there is no "box for now,
real part later". A stand-in only for what is made to order, or with a `standin_reason` in the parts data (why no
CAD exists yet) and a plain sentence to the client that the model is temporary there.

## Where a part's STEP comes from

1. **The part library** (`machine-project` «Part library»): the framework's catalogue and this client's own library
   already hold parts with their ports and their purchase rows. Always look here first.
2. **The platform's part-search module, when one is connected** (the `parts` command is on PATH): it searches makers'
   catalogues and returns STEP files. `parts --help` lists its sources and options.
3. **Locally, without a part-search module:** the user supplies the maker's STEP (downloaded from the maker's site
   or catalogue) into `mechanics/cad/vendor/`. Ask for it in one line, naming the exact part number and the file
   format (STEP AP214). Until it arrives, a stand-in with a `standin_reason`, said plainly to the client.

## The `parts` module (when connected)

```bash
parts search "nema 17 stepper motor" --n 5     # candidates: maker, part number, formats, source
parts get <source> "<id from search>"          # STEP -> /work/parts/<source>-<name>.step
```

- Search queries in **English**, with the standard designation: `nema 17 stepper`, `608 bearing`, `MGN12 carriage`,
  `ISO 15552 cylinder 32`, `SFU1204 ball screw`, `KP08 pillow block`. Words in other languages usually find nothing.
- Short queries (1-3 words) work best; an empty result -> drop words before concluding the part is not there.
- Results can be product **families**; `get` takes the family's default row unless you name the exact part number
  (`--variant <part number>`, if the module supports it).
- Prefer a maker's model over a community upload: a community model's author's licence applies, and its dimensions
  are not guaranteed. Check that the result offers STEP.
- Downloads may be rate-limited: do not fetch "to have a look".
- If the module fails or is offline: tell the owner in one line what broke, tell the client plainly that this part is
  temporary in the model. Never fall back to a box silently.

## Flow

0. **Look in the part library first**: `python3 ${DE_ROOT:-/opt/design-engineer}/skills/machine-project/scripts/part_library.py roots`
   lists the levels, the groups in use are under `mechanics/cad/library/`. A part that comes from there needs no
   search and no download. Only when it is not there:
1. Search (module) or ask for the maker's STEP (locally). Pick the best candidate yourself: right size class, a known
   maker, STEP available, sold where the client buys.
2. Get it and build it into the model (below) straight away. Report the part number (what the client orders) with
   the render of the result, not before.
3. **Only if the client wants to choose** (his rules say so, or he asks to see options): send 3-5 candidates first,
   each as a picture with a one-line numbered caption in his language (maker, series, size, key rating, source link),
   then ask which one (or "any, your choice").

## Into the model

- Whole-machine digital twin from a STEP assembly: `step-import` (the downloaded file is a valid input).
- build123d project (`cad-build123d`): keep the file in `mechanics/cad/vendor/` and import it once:
  ```python
  from build123d import import_step, Location
  motor = import_step("vendor/maker-MOTOR-PART-NUMBER.step")
  motor.label = "motor_x"; motor.located(Location((x, y, z), (0, 0, 1), angle))
  ```
  Measure the imported envelope (`motor.bounding_box()`), the flange holes and the shaft from the STEP itself and
  drive params.py from those numbers; never retype them from the catalogue page.
- Vendor STEPs arrive in mm with arbitrary origins/axes: find the mounting face and shaft axis first, then place.
- Model tree / BOM / viewer card: the maker's name and part number (`Festo DSNU-20-50-P-A`, not `cylinder_1`), source
  (maker + part number, or the model's link + author), as `cad-build123d` requires for any purchased part.
