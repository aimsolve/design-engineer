---
name: sim
description: Any engineering sim that decides a design (plain numpy 1-D models, MuJoCo 3.x, FEA, cycle collisions) — simplest model first, dt/2 convergence, every force in the path listed, sensitivity sweep to find the deciding input, controls that can fail, honest README with trust verdicts; MuJoCo specifics for paper / thin sheet / small parts; strength of a part (gmsh + CalculiX: stress, deflection, safety factor, a picture for the client) whenever a chat asks whether a part will hold, bend, how thick it must be, or about strength, or a load-bearing bracket / plate / lever / printed holder is about to be made; collisions and clearances of every moving part over the whole machine cycle (python-fcl) after any change to motion or to parts near a moving path. The machine itself in MuJoCo or ROS: its MJCF / URDF comes out of the model (export_robot.py), never written a second time by hand. Use before writing any sim, when a result looks too good or blows up, and before telling anyone what a sim showed.
---

# Sims for machine design

Where they run: on the platform every tool below is in the worker image (MuJoCo, gmsh + CalculiX, python-fcl,
build123d), so a sim is a plain `job run --expect long "..."`; a long job gets 6 GB / 4 cores on a big worker, 3 GB /
2 cores on a small one. Locally the same commands run in the framework's environment (gmsh and CalculiX installed).
A job's OpenMP / BLAS thread count is set to its cores: don't set `OMP_NUM_THREADS` yourself. Several MuJoCo variants = several jobs, not threads in one.

## Any sim — checks before a number leaves the building

1. **Simplest model that can kill the idea first.** A plain 1-D numpy model of the belt/train settled what a full
   MuJoCo run muddled. Go 3-D only for what 1-D can't answer.
2. **Convergence**: halve the time step (and the mesh pitch) and rerun before quoting a number. A "±0.2 mm" claim
   moved from +1.42 to −0.30 mm at dt/2 — it was solver noise.
3. **List every contact and force element in the path**, mark the ones the model omits. A 6.2 % failure rate fell to
   0.09 % once two omitted parts (corner shoes, leaves) were modelled.
4. **Sensitivity sweep** over the uncertain inputs; name the deciding one. If it is an unmeasured μ, bound it from
   reference values and name the assumption; don't report the sim's guess as fact. A hand test at the client only as
   the exception (`engineer-loop` gate order).
5. **Controls that can fail** (see `engineer-loop`): a control that passes whatever the variant proves nothing.
6. Report what is an INPUT (tension, μ, glue) vs what is computed.

# Strength of a part (FEA: gmsh + CalculiX)

When: a part that carries load (bracket, motor plate, lever, printed holder, shaft support) before it goes to the laser or
the printer, whenever the client asks whether it will hold, whether it will bend, how thick it must be, and when choosing thickness or
material. One solid per run, linear static only: no contact, bolt preload, plasticity, buckling, fatigue, vibration;
say which of those could matter. Script: `F=${DE_ROOT:-/opt/design-engineer}/skills/sim/scripts/fea_static.py` (worker main python).

1. **The part alone as STEP** (`export_step(part, "build/fea/<name>.step")`, or `build/parts/<name>.step`). A sheet part
   modelled with sharp bends gets its real inner radius on the FEA copy (`part.fillet(t, [inner_edge])`): a sharp inner
   corner is a stress singularity and never converges.
2. `job run "python $F build/fea/<name>.step --list"` — faces with type, area, centre, normal / diameter.
3. **Spec** `sim/fea/<name>.json` (source, committed; the docstring of the script has every key):
   ```json
   {"step": "build/fea/bracket.step", "out": "build/fea/bracket", "title": "Motor bracket", "material": "S235",
    "h": 1.5, "refine": 1.5, "up": "y",
    "fix": [{"holes": 4.5}, {"plane": "-y", "clip": [[-30.5, 20, -88.5], [-23.5, 30, -81.5]]}],
    "loads": [{"on": {"holes": 3.4}, "torque": 560, "axis": [1, 0, 0], "label": "0.56 N·m"},
              {"on": {"holes": 3.4}, "force": [0, -4.5, 21.7], "label": "motor"}]}
   ```
   Fix where it is really held: bolt holes + the clamped patch under each head (`clip` box), not a whole face (that is
   a weld). Every load from the model with its source: motor torque from the datasheet in params, cylinder force at
   the REGULATED pressure, mass × g × a dynamic factor, crank force T / R. A force that acts off the picked faces
   (crank plane, motor centre of mass) = the force + its moment r × F as a `torque` entry. ×2 on nominal for impact /
   stepper stall unless you know better, and say so. Materials: S235, S355, AISI304, AL5754, AL6061T6, PETG, PLA, PA-CF,
   POM (printed = the weak layer direction), or `{"name", "E", "nu", "yield"}`.
4. `job run --expect long "python $F --spec sim/fea/<name>.json"` -> `build/fea/<name>/fea.png` (two views, colour = von Mises,
   deformation exaggerated, supports grey, forces as arrows) + `result.json`. 100k nodes ~1.5 min. A long job gets
   3 GB on a small worker (6 GB on a big one): keep the fine mesh <= ~100k nodes (3 mm sheet:
   h 1.5 -> 1.0); `"solver": "ITERATIVE CHOLESKY"` uses less memory but ran 4× slower.
5. **Read it before quoting**: equilibrium error ~0 N (the loads went in); `change on refine` u < 5 % and stress away
   from supports < 10 % = converged. The peak at the supports and in sharp re-entrant corners (laser-cut notches) does
   not converge: quote the stress away from supports, name the corner peak separately. Hand check of the main section
   (σ = M / W, beam deflection) within ~30 %, else the fix or the load is wrong. Safety = yield / stress: steel >= 2,
   printed >= 3 (creep, layer bond).
6. To the client, in his language: the picture + one sentence ("I checked the bracket against the motor force with a ×2
   margin: stress up to 69 MPa at a yield of 235, safety 3.4, deflection 0.09 mm"), plus what was not counted. No solver
   words. The picture's texts follow the spec's `"lang"`, else `.engineer/locale.json`, else Russian.

Validated in the worker: steel cantilever 100×10×10, 100 N: deflection 0.1911 mm vs 0.1905 formula, root stress 56.9
vs 57.0 MPa.

# Collisions over the whole cycle (python-fcl)

When: after a change to a moving group, its travel or schedule, or a part near a moving path; before a concept or a
drawing set of a machine with motion goes out; when the viewer animation shows parts passing through each other.
Input = what the viewer already has: the parts at the HOME pose + `motion.json` with a `cycle` block (`web-viewer`
«Cycle motion»; `node_groups` maps parts to groups). A code model gives the parts as `build/model/parts.json`:

```bash
job run --expect long "python ${DE_ROOT:-/opt/design-engineer}/skills/sim/scripts/cycle_clearance.py --model build/model/parts.json \
  --motion build/twin/motion.json --groups build/twin/motion_groups.json --clear 0.5 \
  --expect '*rail_block*~*rail*' --skip '*link_rod*' '*rod_end*' --out build/clearance"
```
A machine without parts.json: `--step build/cad/<machine>.step` instead of `--model` (the whole STEP is tessellated
again whenever it changes; unchanged pairs still come from the cache).
- Each moving part vs every part of another group, sampled so nothing moves > 0.5 mm between samples. Lines: `HIT`
  (meshes intersect, with the time window), `CLOSE` (< `--clear`), `SLIDE` (touching the whole cycle: a rail block on
  its rail), `ok`; `clearance.json` has every pair. Exit 1 on an unexpected HIT / CLOSE.
- Groups move rigidly (translate / rotate about a pivot). Crank links, rod ends and pins drawn as `static` in
  `node_groups` really swing: they give false HITs. Give them a rotary group or `--skip` them and check the link
  swing separately; never wave a HIT away by eye. The product drawn in the path (bars, a label stack) gives
  expected HITs with what pushes or picks it: `--expect "pusher~cassette_item_*"`.
- With `--model` a part takes its group from `node_groups` / `--groups`, else as the viewer moves it (`derive.py`
  `motion_groups`): its `motion_group` field, else the group of the part it is docked to, up the joints. The screws of
  a slider move with it (without that, a real machine showed 304 false HITs instead of 66). A part with neither a
  STEP nor stored triangles is listed as not checked.
- **Incremental**: `--model` tessellates only parts whose key changed (`<out>/meshes/`, on the PC a folder per project
  in the machine's cache), and a pair is measured
  again only when one of its two parts, their placement or their groups' motion changed (`<out>/pairs.json`); the
  run prints `measured N, cached M`. A real machine (60 moving parts, 6 groups, 437 pairs): first run 3.3 min
  (2.8 of it tessellating 251 parts), nothing changed 1 s, one moving part edited 5 s (26 pairs). Run it after every
  change to motion: it is cheap. Samples a pair cannot hit at are skipped (the exact motion of one part against the
  other, never closer than 2 mm): another machine, 479 pairs, 9.7 -> 3.5 min, the same verdicts and least distances.
- **Overlaps are not yours to check here any more**: every model build runs the same sweep with exact overlap
  volumes and the contacts taken from the joints (machine-project `clearance.py`), and reports a clash as a build defect
  (a red mark on the page and the board; publishing goes on). This tool is for
  what the build does not judge: how close parts come (`CLOSE`, `--clear`), a STEP-only machine (`--step`), what-ifs.
- Show a HIT to people as a viewer frame at that time (`#t=`), not as a table.
- Not seen: a part wholly inside another without surfaces crossing, flexible parts (belts, hoses, paper), a part
  that changes group.

# The machine itself in MuJoCo / ROS: MJCF and URDF out of the model

**Never write a second model of the machine.** The model in code already states which parts move together and how, so
the simulator's model comes out of it:

```bash
job run "S=${DE_ROOT:-/opt/design-engineer}/skills/machine-project/scripts; python $S/export_robot.py \
  --model build/model/parts.json --out build/robot --name <machine> \
  --mechanism mechanics/kinematics/mechanism.py --groups build/motion_groups.json"
```

-> `build/robot/<machine>.xml` (MJCF), `<machine>.urdf` and `build/robot/meshes/*.stl`. A link is a part or a rigid
group of parts, a joint is a declared freedom with its axis, anchor and travel, and every link's mass, centre of mass
and inertia come from the parts' own solids at the density of what they are made of (a part's `card.density` in
g/cm³, else a catalogue by `material`, else 7.85 **and said so, naming the part**). Read the log: it prints a line per
link with its mass, and a line for every default it had to take.

Two inputs, whichever the machine has:
- **docked parts**: the dockings in `parts.json` are the tree — a `rigid` docking welds, any other is a joint.
  This is all a machine without a loop (and any robot arm) needs; `--mechanism` is then read for the loops alone.
- **a stated mechanism**: `mechanics/kinematics/mechanism.py` adds two names for this — `LINKS = {<group>:
  <joint>}` (which group of parts is which link; a group in no entry is welded to the base) and `DRIVES`
  (`{"joint", "kind": "cylinder"|"motor", "bore_mm", "rod_mm", "pressure_bar"}` or `force_n`/`torque_nm`). Each
  `meet()` of the loop becomes an MJCF `<equality><connect>`; **URDF breaks the loop** and the log says which and
  where, because URDF is one tree with one root. A cylindrical or ball joint is refused for URDF, naming it.
- **One drive per machine** for now; a second is refused rather than half written (a machine with six drives cannot be exported yet).

What to know before trusting a run of it:
- **Only the driven joint gets a range.** The travel of every other joint of a loop is what the loop makes of it, not
  an end stop the machine has; they are reported instead. A machine whose real stop is a cylinder cushion
  has no cushion in the file: driven at its own cycle time the lever overshoots its travel by ~10° and the drive
  saturates. Drive it an order of magnitude slower to check the mechanism, and model the cushion before believing a
  fast run.
- **Contacts are off** (`contype=0`): MuJoCo collides the convex hull of a mesh, which fills every bore. `--collide`
  turns them on with the two links of every joint excluded; clearances over the cycle stay the python-fcl check above.
- **MJCF `ref` is the start**, so MuJoCo's zero is the pose the CAD is drawn in. URDF has no starting value at all.
- A hinge drive's `ctrlrange` is in **radians** even under `<compiler angle="degree">` (the compiler converts a
  joint's `ref` and `range` and not an actuator's `ctrlrange`; checked on mujoco 3.17).
- The worker image has MuJoCo 3.14 (headless, `MUJOCO_GL=osmesa`): export and run in the same `job run --expect long`.
  `tests/test_export_robot.py` passes on 3.14 as on 3.17.
- **Check it against the model's own solve**, not by eye: drive the exported model slowly over the stroke and compare
  every joint with `mechanism.poses()` at the same drive position; `tests/test_export_robot.py`
  (`test_the_exported_loop_follows_the_model_s_own_solve_when_it_is_driven`) does exactly that on a four-bar and is
  the pattern to copy. One measurement from a real pneumatic lever machine, for scale: 0.13° of lever and 0.11 mm of rod over the whole cycle, and
  the export showed its swinging cup frame weighs 12.2 kg where that project's `physics/cyl_force.py` assumes ~3.5 kg,
  so its SC50 needs 6.23 bar on the pull side to come back up from the drop. Those numbers are that machine's, not
  this skill's: re-measure on your own.

# MuJoCo specifics

## Before anything

- **Sim before live test**: an idea a simple sim can kill is killed by a sim, not by the client's printer.
- **Confirm parameters in the group before any run > ~15 min** (picture + product orientation, dims, material, what
  moves, variants, time). Hours of runs were once thrown away: the product was upside down.
- Log the run in the task list (name, why, %, ETA).

## Setup checklist

1. **Geometry from the live CAD, never retyped.** For the machine itself that is `export_robot.py` above — bodies,
   joints and masses and all. For whatever the machine handles (product, web, sheet): exec the params file at run
   time, and export meshes from the CAD (STL + CoACD convex pieces). Record the CAD mtime you exported from; add an assert that fails when meshes and
   params disagree. Sim geometry goes stale fast — the first project ended with every sim on an old geometry.
2. **Orientation**: check the product's orientation in the first frame, every run.
3. **Collision meshes are convex hulls.** A concave part as one mesh silently fills its hollow: decompose it
   (CoACD) or build it from analytic pieces.
4. **Flex (sheet) mesh**: uniform pitch (mixed 0.5/1.0 mm blew up, 40-70 % stretch in 10 ms); uniform diagonals
   (checkerboard locks the sheet ~20× too stiff).
5. **Bending calibration is mesh-dependent**: MuJoCo `young` = factor × E, factor was 2.5-3.45 for different
   pitches. Recalibrate (cantilever vs beam theory) after every mesh change.
6. **Contacts**: `cone="elliptic"`, `impratio` 10..50. Pyramidal at impratio 1 creeps under unsaturated load (all
   such runs were discarded). `noslip` is not allowed with flex. Pair friction = element-wise max unless `priority`
   is set; one μ, no static/kinetic split.
7. **Light body on a heavy mesh**: a 5 g part resting on 1e-7 kg flex nodes sinks and tilts → drive it kinematically.
8. **Control run**: the solver has a slip floor (~0.1 mm per stroke even at μ = 1). Always run a μ = 1 control and
   read only the excess.
9. Run variants in parallel; a loaded machine made 4 s of machine time take 70 min.

Env: `env -u VIRTUAL_ENV uv run --quiet --isolated --no-project --with mujoco==3.14.0 --with numpy --with imageio
--with imageio-ffmpeg --with pillow --with matplotlib --with scipy python …`

## What it cannot do — design around it

- **Flex cannot hold a pressed crease** (no 2D plasticity). A curved rest shape flips to its mirror or springs flat.
  Anything past ~60° of fold is not physical. Options: rigid panels on hinge joints with `frictionloss` ≈ plastic
  moment (1-3 mN·m for 90-100 g/m² paper); staged recompiles with the current shape as rest; LS-DYNA `*MAT_PAPER` /
  Abaqus for the real thing.
- Bending force is linear in positions, so a rest shape x0 can be emulated with a constant force +K·x0.
- **Friction coefficients are inputs.** Literature: paper-steel 0.27-0.31, paper-UHMW ~0.12-0.2, paper-foil
  0.25-0.3. A result that depends on the ORDER of two μ values is the input inequality coming back out — say so and
  ask for a hand measurement (incline test).
- Hoop tension of a glued sleeve, glue, plastic set: not computed; if they are inputs, say they are inputs.

## Output

- Video + stills, product position in every caption; `summary.json`.
- README opens with **ANSWER**, then numbers, then **Caveats, honestly**: what is not modelled, which geometry
  version, which μ.
- A trust verdict per claim (high / medium / low / none) → the wiki's findings page, with "never simulated" listed.
- Roast the conclusion (fresh critic) before it goes to the group.
- To the client: "the calculation showed…" in his language, with a picture, no solver words.
