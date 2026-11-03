---
name: web-viewer
description: Reusable three.js web viewer for a CAD machine or product — one self-contained HTML page (no server, works on a phone) built from meshes.json + params.json + one project hook file (motion pose(t), live hoses/springs, card texts). Part cards, hide parts, orbit/pan/zoom with double-click/double-tap pivot, phone layout, URL hash #t/cam/hide for reviewers, headless frames (shoot.py), a node smoke test (harness.js) and a real-input controls test. Also has an assembly mode for imported CAD (GLB + parts.json from step-import) as a static site with tree, kind colours, part cards and STEP/STL downloads. Use when a project needs its CAD shown to a client or critic in a browser, when adding an animation or a part card, when shooting review frames, or when a viewer bug shows up (z-fighting, blank frames, parts floating, phone unusable).
---

# Web viewer for a CAD model (three.js, one HTML file)

Template: `templates/viewer/` (copy it into the project, e.g. `viewer/`); `templates/viewer/project.js` is the hook skeleton with
every key commented. The rules below are the lessons of the first projects, condensed.

## Pipeline

```
cad/params.py ─► parts/<node>.py build() → [{name, shape, color, group, title?, desc?}]  (world coords, HOME pose)
                        │                 optional module-level VIEWER dict (viewer-only numbers)
                 cad/assemble.py ─► out/meshes.json  [{name:"node/part", group, color, pos, idx, title?, desc?, opacity?}]
                                ─► out/params.json  every UPPER_CASE constant + all VIEWER dicts  (= `P` in the page)
                        │
      viewer/build.py  (page.tpl.html + three.min.js + meshes.json + params.json + project.js) ─► model.html (one file)
```

```bash
python viewer/build.py --meshes cad/out/meshes.json --params cad/out/params.json --project viewer/project.js --out model.html
node viewer/harness.js model.html                  # smoke test: construction, full cycle, input, hash, pose(t) jumps
env -u VIRTUAL_ENV uv run --quiet --isolated --no-project --with playwright python viewer/shoot.py model.html out.png --t 1.0 [--cam ...] [--hide a,b] [--mobile]
env -u VIRTUAL_ENV uv run --quiet --isolated --no-project --with playwright python viewer/test_controls.py model.html --shots frames/
```

build.py prints page size (budget 5 MB), the heaviest meshes, and numeric literals in project.js (each one is a second
copy of a CAD number). The page logs parts without card text; harness.js prints them.

## Plugging a project in: the hook file

One file, `project.js`, defines `function viewerProject(V)` and returns a config (all keys optional; skeleton with every
key commented: `templates/viewer/project.js`):

| Key | What |
|---|---|
| `title`, `subtitle`, `axesText` | panel text (subtitle/axes may be HTML) |
| `views` | `[{name, yaw, pitch, dist, target:[x,y,z]}]`; first = home and "Reset view"; none = fit the whole model |
| `axes` | `{at, size, minDist}` axis gizmo; tags hidden in close-ups |
| `near`, `zoom`, `pitchRange`, `edges`, `noPick` | camera near plane, dist limits, pitch limits, edge lines, never-pick name substrings |
| `cycle`, `ops` | cycle seconds (0 = static model, no play controls) and `[{t0, t1, name, d}]` operation windows |
| `groupText` | `{group: "what it does, stroke"}` for the card's motion row |
| `parts` | `[[substring, title, desc]]` fallback card text; **prefer `title`/`desc` on the part record** (meshes.json) |
| `lang` | UI language of the page: a table of `page_i18n.js` (`ru`, `en`; default `ru`); `?lang=` in the URL wins |
| `labels` | override any UI string (the `L` dict: all UI text in `page_i18n.js`, one table per language) |
| `material(m, geo)` | custom material for a mesh record (e.g. two-tone paper), `null` = default |
| `setup()` | build procedural objects, hide CAD meshes you redraw live |
| `pose(t, cycles)` | set every group and procedural object from `t` alone |
| `meta(t, cycles)` | status line |

`V` hands the hook: `THREE, P, scene, groups, byName, pickables, G(group), find(sub), findAll(sub), mat, box, at,
k/ease/there` (ramps), `mm/mm2/num` (decimal comma), `springLen, setSpring(mesh, f, fixedEnd)`,
`rotateAbout(mesh, axis, angle, pivot?)`, `tube(name, color, r, parent?)` → `update(points)` for hoses and cables,
`addTag(text, where, opts)`, `L`.

The template itself holds no product number and no project name. Groups come from `meshes.json` (`group` per mesh,
`P.GROUP_PARENT` nests riders); a group moves by `G(name).position` only.

## Controls (desktop and phone)

| Do | Desktop | Phone |
|---|---|---|
| orbit | drag | one finger |
| pan | right-drag or Shift-drag | two-finger drag |
| zoom | wheel | pinch |
| part card | click | tap (card opens as a collapsed strip at the bottom) |
| new orbit pivot | double-click a part: smooth re-centre on the hit point | double-tap a part |
| back to home view | "Reset view" | same |
| hide a part | card: "Hide part" / "Hide all like this (n)" / "Rotate around this point"; panel: "Show all" | same |

Panel and card collapse to a strip (header tap); on screens ≤ 700 px both start collapsed, the card is a bottom sheet
≤ 45 % of the height, the op list shows only the current op's description. A one-line hint of the controls sits in the
panel. Double-click never hides (it did in the first project and fired by accident on touch). The double-tap's first
tap also selects the part.

URL hash, read at load and on `hashchange`: `#t=1.45&cam=yaw,pitch,dist,tx,ty,tz&hide=node/a,node/b`. `t` freezes
the cycle. `window.__viewer` exposes `state()`, `pick(x, y)`, `coverage()`, `setTime(t)` for scripts.

## Rules

- **Every text the page itself shows is in `templates/assembly/i18n.mjs`**, under the same key in `ru`, `en` and
  `pl`; never a literal in `index.html` or a module. The page language: `?lang=` > the visitor's pick for this project
  (`eng.lang.<pid>`) > the project's `public/locale.json` > ru. Model data (part and node names) is not translated.

**Numbers and data**
- One source of numbers: the hook reads `P` only. Viewer-only numbers go in a `VIEWER` dict in the part module, never
  typed in project.js (build.py lists the literals it finds).
- Card text lives on the CAD part record (`title`, `desc`), not in substring lists in the viewer; a renamed part must
  not silently lose its card.
- Per-node assembles write per-node files; only a full assemble writes the files build.py reads.
- Do not export meshes the viewer hides and redraws (hoses); budget triangles (springs as simple proxies).

**Rendering**
- Never two coplanar surfaces. Log depth buffer is on, so `polygonOffset` does nothing: use real gaps.
- A visual gap must exceed the tessellation tolerance of BOTH meshes everywhere along the motion (0.2 mm chord
  tolerance needs ≥ 0.3 mm), or re-export the neighbour finer.
- Anything resting on a deformed surface uses the same offset function as that surface.
- Procedural product heights derive from one variable; outer layer ≥ inner + gap at every station.
- Mirroring a coordinate flips triangle winding: reverse the index order.
- A CAD mesh handed over to an animated proxy must look identical at the swap frame.
- Thin light parts on light steel need a contrasting colour and an outline. Owners want parts gone, not ghosted.

**Motion**
- `pose(t)` is a pure function of time. Driven parts take their position from the driver (with lost motion), never
  from their own timer.
- Every motion group has a named actuator and guide in the CAD; a group moving with nothing driving it is a bug.
- Motions are continuous segments sharing endpoints; no `boolean * value` terms. harness.js flags group jumps.
- Flexible parts (hoses, cables, belts) are rebuilt each frame from endpoints with one shared length param; slack ≥ 0
  at both stroke ends.
- Springs drawn at the stop get seated on the nominal product and compressed; overtravel goes into a visible spring,
  never into interpenetration. Tipping pivots about the real contact edge.
- Moving surfaces need marks spaced at the cycle pitch. Thin sheets are one connected grid from the first version.

**Frames and review**
- LOOK at every PNG before claiming anything. shoot.py prints coverage and exits 1 on a near-blank frame (camera
  inside a solid); hide occluders with `--hide`.
- Every frame is a fresh page load (shoot.py) — state never leaks between frames.
- Pixels cannot prove sub-mm clearances: check those numerically; frames are for "looks right".
- Test UX on a phone viewport (`--mobile`, `test_controls.py`) before shipping; real touch, not just a narrow window.

## Traps

- Software GL (swiftshader) renders a phone frame at DPR 2 in ~200 ms; a Playwright CDP touch call waits for a frame,
  so scripted double taps can land > 400 ms apart. The page uses the event timestamp; test_controls.py runs the phone
  at DPR 1.
- Raycasting hits invisible meshes in three.js; the template filters by visibility up the parent chain (the first
  project's page let clicks land on the hidden CAD hose).
- `three.min.js` is three.js r150 (global build, deprecated after r160 but fine inlined; MIT, `LICENSE-three` and `VERSION.txt` next to it); keep it, the page must stay one file.
- A project's hook may create meshes lazily in `pose()`; the page calls `pose(0)` before reading `&hide=`.

## Assembly mode (imported CAD)

For a machine that came from a STEP file (`step-import` output: `model.glb` + `parts.json` + `parts/`), not from our own
build123d code. On the engineer stack this pipeline is the project's kit.json `site` command (e.g. `sim/site.sh`): the router
runs it after every turn that changed the model; an agent's job running it is refused. Template: `templates/assembly/`. It is a **static folder served over HTTP**, not one file: 12 MB of GLB does
not fit the 5 MB one-file rule. The one-file template above is unchanged.

```
step-import out/  (model.glb, parts.json, parts/<id>.step|stl)
      │  python3 templates/assembly/build.py --in <out> --out <out>/site [--title "..."] [--up y|z]
      ▼
site/  config.json  model.glb  parts.json  parts/  [edges.*  motion.json  world.js  sim/  fw/]      (project data only)
      │  on the stack: copied to public/; the page is served at /<id>/
      │  locally: build.py ... --standalone, then python3 -m http.server 8765 -d <out>/site
      ▼
env -u VIRTUAL_ENV uv run --isolated --no-project --with playwright python templates/assembly/test_site.py [--url http://127.0.0.1:8765/] --shots <dir> [--part <id>] [--module <module>]
```

`test_site.py` checks a served site (`--url`) or builds and serves its own; the other assembly tests build and serve their own: `test_view_tools.py`
(views, grid, ruler), `test_kit_downloads.py`, `test_apk_button.py`, `test_panel_downloads_fit.py`, `test_panel_layout.py`,
`test_part_notes.py`, `test_sim.py`, `test_shared_viewer_build.py` (unittest), `test_shared_viewer_e2e.py`. They leave 300 MB+ sites in
the system temp dir if they are killed: check `/var/folders/**/T/*site*` after a run that did not finish.
The browser tests run on the demo machine `examples/nema-bracket` by default (built once with `de build` into a cached temp dir);
`VIEWER_TEST_MODEL=<importer output dir>` (or `--in`) points them at a richer model, `VIEWER_TEST_SIM=<machine sim dir>` (or `--sim`) at
its sim. A test that needs what the demo has not (a sim with boards, drawings, an operator app) prints `SKIP: <reason>` and exits 0.
UI texts the tests expect come from i18n.mjs (`viewer_stack_server.ui_texts(lang)`), never literals.

**One viewer for all projects.** The viewer code is never copied into a project. The stack's nginx serves this
template's `index.html`, modules, `vendor/` and the sim runtime (`skills/virtual-controllers/runtime/` at `/_viewer/vc/`) at `/_viewer/`
straight from `vendor/design-engineer`, and answers `/<id>/` with that `index.html` when the project's public/ has `config.json` and no
`index.html` of its own. So `scripts/sync_framework.sh` updates the viewer on every project page at once, without a rebuild; a public/
with an old `index.html` keeps its old page until it is deleted. The page imports everything from `/_viewer/<name>.js` (nginx answers
`<name>.js` or `<name>.mjs`, both as JavaScript) and reads everything else from `config.json`: `title`, `sim` / `edges` / `cycle` (the layers
the build found), and `v` = build stamp, appended as `?v=` to every data URL (model, parts.json, edges, motion, world) so a browser never
shows the model of an older build; `config.json` itself is fetched `no-store`. `--standalone` adds `index.html` + `<out>/_viewer/` (a
copy laid out the same way, `.mjs` renamed to `.js`) for a plain static server at the root. Tests serve sites through
`templates/assembly/viewer_stack_server.py` (the nginx rules in Python). A project's own look or behaviour goes into the data
(`parts.json` fields, `config.json`, its `--world` module, `sim/`), never into a patched copy of the page.

Same as one-file mode: orbit/pan/zoom, double-click/tap = new pivot (never hides), part card as bottom sheet on phones,
every UI string in one table per language (`i18n.mjs`), `#cam=` in the URL, `window.__viewer`. Different:

- Input is data, not code: no hook file, no `pose(t)`; motion only from a machine's `motion.json` (the machine layer below). Card text comes from `parts.json` (name, drawing, kind,
  module, instances, bbox, volume; mass = volume x 7.85 g/cm3, labelled as an estimate). Optional part fields: `density` (g/cm3,
  material from the model: mass = volume x density, no "from the name" note; once any part has one, a part without it is bought and shows no mass) and
  `thickness` (text, a "Sheet thickness" row before the module row). Downloads are plain links to `parts/`.
- Colour by `material` (parts.json `material` from step-import; default) or `kind`, toggle in the panel (remembered in localStorage,
  `__viewer.setColorMode('kind'|'material')`); legend and tree dots follow. One shared PBR material per class (palette `MAT_STYLE` in
  index.html: orange = printed plastic so custom parts pop, glass translucent). A parts.json without `material` falls back to kind.
  Shading: smooth normals with a 30 deg crease (`toCreasedNormals` at load, once per distinct geometry), hemisphere + 2 directional + a small
  PMREM studio environment. Home view fits the corners of every mesh box (tight, ~70-75 % of the width). Same-part siblings collapse
  in the tree to one row "name x N" with one eye. Selection is magenta (in nobody's palette), all instances light, clicked one strong.
  Selected part: all instances light orange, the clicked one strong. Assemblies are framed, not tinted.
- **CAD look** (`view_fx.js`, panel row "View:", three plain toggles remembered in localStorage `assembly.view`):
  "Edges" = baked feature edges (`step-import/scripts/bake_edges.py`, loaded AFTER the model in 8 ms batches, one `LineSegments` child per mesh instance sharing one
  geometry per part, so they follow motion) + screen-space silhouette outlines; "Gap shadows" = screen-space AO (12 taps from the depth texture, normals from depth
  derivatives, depth-aware blur; off by default on phones, switches itself off when frames stay > 33 ms; a manual click stops the auto-off);
  "High contrast" = white page, 2 px black contours, stronger AO, more saturated colours and jitter, 18 px UI. Pipeline: scene -> MSAA half-float target with a depth
  texture -> AO pass -> composite (AO + outlines + line dilation + sRGB). Baked lines write only alpha=0.5 (rgb blend = keep), the composite turns that marker into a line of
  the wanted thickness, so WebGL's 1 px limit does not matter. Fills have polygonOffset(1,1) so lines win the depth test. Per-part material = class colour with a
  hash-of-id jitter (lightness/hue/saturation); parts named as a guard, glass or plexi (`NAME_WORDS.glass` in i18n.mjs, any language) are transparent (and never steal a click from the part behind);
  sheet-like steel (thickness estimate < 4.5 mm) gets a procedural galvanised spangle, box-projected in the part's own frame (no UVs needed). `__viewer.view.{get,set,spangle,auto}`;
  `__viewer.bench(n)` goes through the whole pipeline and syncs with readPixels. M1 Pro, 1440x900: ~4.5 ms/frame all on (dpr 1), ~10-13 ms at dpr 2; texture costs nothing measurable.
  Cards say material and mass come from the NAME / volume (STEP AP203 has no material data).
- Left panel is the GLB node hierarchy (module level open by default) with an eye per row, search by name or drawing number,
  "Show all". Assembly card has "Show only this" (isolate).
- **Standard views, grid and ruler** (the View section of the panel; what an engineer used to a CAD viewer misses). Seven buttons
  Front / Back / Left / Right / Top / Bottom / Isometric fly the camera to that direction and refit it to the whole
  machine (camera: front = +Z, right = +X, top = +Y of the viewer's frame); the one the camera is at is marked. Deliberately a
  list of named buttons, not a view cube: the words need no guessing, one click is enough, and it works on a phone. "Grid" puts a
  measuring grid on three panes around the machine (floor + the two walls on the far side, which swap as you orbit and go away above
  ~70° of elevation, where they would only funnel the view), with a step of 1…1000 mm or "auto" (~20 cells along the machine) and the
  machine's overall size next to it. On/off and step are remembered in localStorage under one key for every project (like View and
  Colour), so a step chosen on one machine is the starting point on the next. The grid is drawn in a fragment shader, NOT as line geometry — lines of its own would
  be taken for feature edges by the CAD look (`view_fx`: alpha below 1 marks a line pixel) and come out thick and black. "Ruler":
  two clicks on the model give the distance in mm (plus horizontal and vertical parts); points snap to the nearest corner of the
  triangle under the cursor (12 px), so edges and holes give their real size; the line and its label are drawn over the canvas in
  SVG, so the machine never hides them. While the ruler is on, a click measures instead of selecting; Escape switches it off (unless
  a comment is being typed). With "less motion" set in the OS (`prefers-reduced-motion`) every camera flight — view buttons, "Reset
  view", tree clicks — puts the camera there at once instead of animating.
- Hash: `#cam=yaw,pitch,dist,tx,ty,tz&hide=<part ids>&hn=<node idx>&sel=<part id>&seln=<node idx>&up=y|z`. The page rewrites
  it (replaceState) as the view changes, so the address bar is a shareable link. `hide` hides every instance of a part id;
  `hn`/`seln` are GLB node indexes (single instances, assemblies).
- `window.__viewer`: `ready`, `state()`, `select(partId|nodeIdx, frame)`, `pick(x,y)`, `partScreen(id)`, `nodeByDrawing(d)`,
  `isolate(n)`, `showAll()`, `setCam({...})`, `box(n)`, `stats`, and `sim` when built with `--sim` (below).
  Views / grid / ruler (`setView` moves the CAMERA; the CAD look is `view.set`, a different thing):
  `setView("front"|"back"|"left"|"right"|"top"|"bottom"|"iso")`, `setGrid(on, step)` with `grid` =
  `{on, step, size}`, `setMeasure(on)`, `measureAt(clientX, clientY)`, `clearMeasure()` with `measure` = `{on, points, distance}`.
- Renders on demand (only when the view changed); 914 meshes = about 860 draw calls, 2.5 M triangles: 16.5 ms/frame (vsync
  capped) on an M1 Pro, GPU Chrome. Adaptive DPR drops to 1 if frames are slow.

### Machine layer (`build.py --sim <machine sim dir>`)

Real firmware (virtual-controllers wasm builds) running in the page, paced to the wall clock. Without `--sim` there is no section and no
network request for it. The machine's `sim/site.json` names the browser files: `{"entry":"machine.mjs","files":["machine.mjs","world.mjs"],"extra":["../mechanics/kinematics/motion.json"],"fw":"../build/fw"}`.
Output: `site/sim/{config.json, <files>}`, `site/fw/{*.js,*.wasm,index.json}`; the sim UI, CAD motion, app panel and runtime come from `/_viewer/`.
The machine's modules are shipped as **`.js`** (build.py renames `.mjs` and rewrites the references; a static host serves a project's `.mjs` as application/octet-stream
and browsers refuse such module scripts), and their `./vc/...` imports point at `/_viewer/vc/` (one runtime instance per page).

- **UI (no invented machine UI: the machine is operated in its own app, see "App panel"):** panel section "Boards" (board list, collapsed by default on phones) and a thin **simulation bar**
  (fixed strip at the bottom of the 3D area; `L.sim`): machine clock mm:ss.s, speed x0.25 / x1 / x5 / x20 / max, Pause/Resume, Reset, and one button per physical input of the machine
  (`META.inputs: [{id, label, hint, board, pin, active}]` in the machine's `machine.mjs`, e.g. a single Start push button on one GPIO). A press drives the pin for 300 ms of machine time
  and resumes a paused sim. The achieved-speed readout shows only when it says something ("×0.7 — falling behind", "max ≈×80", "paused"). No Start/Stop/target-weight field/state line: those are the app's job.
- **Board card** (list click, or a 3D click on a part mapped by `META.boards[].cad`): MCU, firmware selector (builds of that MCU from `fw/index.json`;
  switching rebuilds the board and resets the sim), pin table with live levels, Serial (last 200 lines, auto-scroll, weight rows hideable), I2C of that board, "Show in 3D".
- **Pacing:** each animation frame `vm.tick()` advances virtual time by wall dt x speed in slices, stops at 8 ms (12 ms at max) of CPU, debt over 250 ms is dropped.
  The wasm is fetched once and instantiated from bytes (`wasmBinary`): the server's `.wasm` Content-Type does not matter.
- **API for the CAD binding / scripts:** `__viewer.sim = {state(), actuators(), start(g), stop(), setSpeed(x|'max'), pause(), resume(), reset(), step(ms), setTarget(g), press(inputId), inputs(), setFirmware(board, name), openBoard(id), app, vm}`
  (`start/stop/setTarget` = machine-level scripting through `machine.start()` etc., not buttons; tests use them).
  `actuators()` -> `{id: {kind:'stepper'|'binary', board, label, steps, pos, vel, on}}`: stepper `steps` = total STEP edges, `pos` = signed steps, `vel` = steps/s (>= 50 ms window); binary uses `on`. Read it every frame (`api.tickers` runs before render).
  `api.tickers` (per-frame callbacks) and `api.selectHooks` are the extension points; a page that animates must call `invalidate()` itself (render is on demand).
- **CAD motion (`motion.mjs`, served at `/_viewer/motion.js`):** when the machine's `site.json` lists `motion.json` (in `extra`, any path relative to sim/; shipped flat under its basename), the CAD moves from `actuators()`. Schema and
  conventions: the machine repo's `mechanics/kinematics/MOTION.md` (`kind` rotary|linear|belt|binary, `nodes` as `name[k]/name[k]/...` paths, `axis`, `pivot` in GLB mm, `per_step`, `rest_deg`, `dir_sign`,
  `start_steps`, `range_steps`, `binary_travel`, `parent`, `carries`, `also`, `dir_ignored`). Mechanisms missing from the CAD: top-level `clones: [{id, actuator, from:[node paths], offset_mm, label, note}]` — the viewer clones those nodes (shared geometry) at the offset as a translucent amber ghost, an actuator addresses the clone by path (same-name index after the existing siblings), the card shows `note`. The only per-machine wiring is `site.json` -> `"motion": {"file", "id_map": {motion id: actuators() id}, "labels": [{"node", "source": "world.cells.0", "unit": "g"}]}`
  (id_map only for ids that differ; labels = optional floating HTML readouts above a node, `source` = property path on the machine object `vm.m`). build.py copies it to `sim/config.json`.
  What it does: resolves paths once (unresolved ones -> `console.warn` + a warning line in the panel, never a crash), stores each node's home matrix and applies the motion ON TOP of it
  (`local' = inv(Wp) M Wp local`, so int16 dequant matrices survive); rotary = about axis through pivot by `p*per_step - rest_deg`, `p = start_steps + pos*dir_sign` clamped to `range_steps`;
  linear = `p*per_step` mm or `binary_travel` lerped over 150 ms; belt = movement marks: flat see-through blue chevrons pointing along the top-run travel (InstancedMesh in the model frame, pitch `mark_pitch_mm` or `"marks": <mm>`, default 150; per belt `"marks": false` = none — the old dark/light slats were read as pushers by a client) that run with `p*per_step`
  plus the `also` rollers, `carries` slide along the belt and WRAP at its ends (a tray jumps back to the other end: looks odd if the belt is full); `parent` composed only for nodes not already under the parent's nodes;
  binary tips get a cyan emissive tint. Nothing moves before an actuator's first STEP edge; Reset (`vm.resets` changes) restores every home matrix. Cost about 0.08 ms/frame.
  Debug API: `__viewer.sim.motion = {bound, unresolved, drives, probe() /*world matrices of every bound node*/, homeProbe, marks()}`; the page exposes `__viewer.{model, camera, invalidate, nodes}` for it.
  Trap: when a rest pose is guessed, LOOK at the frames (`shoot`-style stepping with `sim.pause(); sim.step(ms)`): the head's `rest_deg` from a geometric guess was 89 deg off, visible at once.
  Also, in a drive's `also`: `kind: "coupler"` (four-bar rod: `nodes` = rods, `a`/`b` = crank paths,
  `a_pt`/`b_pt` = rest pins in mm; pin b is solved on crank b's circle and crank b follows) and `kind: "stretch"` (spring: end `a_pt` fixed, end `b_pt` rides node b);
  linear `offset_mm` (the CAD draws the item away from its LOW pose; applied at load and after Reset); vacuum `grip` (`reach_mm`, `belt`, `stack`: picks on the rising
  edge, releases onto the belt); `stops: "absorb"` (an open-loop stepper driven into a hard stop loses the extra steps). Debug: `sim.motion.grips()`, `deltas()`, `advance(ms)`.
- `test_sim.py`: pacing accuracy at x1, max-speed cycle, frame time, simulation bar + physical input, board card, firmware switch, lag readout, phone layout, app panel + bridge (own page load, `?app=1`), screenshots. Machine-specific expectations (boards, inputs, the app) come from the model it is pointed at; without one it skips. Pacing tests load `?app=0`.

### Cycle motion (code-CAD machines, no simulator)

A build123d machine describes motion as groups + a schedule, not steppers. `motion.json` gets a `cycle` block:
`{period_s, loop, groups: {name: {kind: linear|rotary, axis: "x|y|z"|[x,y,z], travel_mm | travel_deg + pivot_mm, parent?, segments: [[t0,t1,f0,f1],...]}}}`.
frac is linear inside a segment, holds between segments, and before the first segment holds the last one's end (the cycle wraps). `parent` = rides on the parent (M = M_parent * M_own).
The machine's product (`build.py --world <module.mjs> [--world-data <json>] [--world-label "Show the bars, the labels and the glue"]`, with `--motion`): what the machine works on (products, packaging, glue) is not CAD, so a project module draws it: it exports `mount({THREE, root, data, period, frac, node, api}) -> {update(t)}`, `root` is in mm of the CAD frame, `node(name)` finds a GLB node (move CAD nodes on top of their home matrix, never toggle `.visible`: the viewer's hide state owns it), `update(t)` runs on every cycle time change. A checkbox in the panel under View, labelled in the client's words with what it shows (never a bare noun like "Product"; without `--world-label` the viewer's own "Show the product" in the page language), shows/hides it; the objects are never picked.
Optional `node_groups: {instance name: group | "static"}` overrides the per-part `motion_group` per GLB node (matched on the name before
" - ", three-sanitised): a code-CAD export reuses one shape (hub, rod end, rail block) in several groups, which one part id cannot express.

```
python3 skills/web-viewer/scripts/cycle_from_params.py --params <cad>/params.py --out motion.json --bom build/cad/bom.json --groups-out motion_groups.json
python3 skills/machine-project/scripts/cycle_from_mechanism.py --mechanism mechanics/kinematics/mechanism.py --out motion.json   # instead, for a machine with a closed loop: the cycle comes out of the solve
   (params.py: GROUPS {group: None|(axis, travel)}, GROUP_PARENT, MOTION, CYCLE_T; optional GROUP_ROTARY {group: pivot_mm}; imported in a subprocess)
step_import.py model.step --out build --groups <module map> --motion-groups motion_groups.json    # parts.json gets `motion_group`
build.py --in build --out site --motion motion.json                                               # config.json `cycle`, motion.json
```
On a code model (cwd = the project, `build/model/parts.json` + `nodes.json`) `build.py` first checks that no two parts share space, moving ones over the `--motion` cycle with the import's `motion_group`s (machine-project `clearance.py`); the result goes into the site as `clearance.json` (config.json `clearance`) and never stops the build: a clash is a red mark at the top of the page ("Parts overlap: N", the list jumps to the moment and selects the part) and the router tells the agent. A client STEP or a hand-made import is not checked.
`build.py --link "Label=url"` (repeatable) adds link buttons under "Reset view" (config.json `links`), e.g. a project's own process-animation page. Never a PDF: machine schemes are buttons of "Download files", drawn from the model (machine-project «Machine schemes»); `check.py` fails a PDF link.
Page: a cycle bar (Play/Pause, x0.25/x1/x2, scrub over one period, time readout); `#t=<s>` opens paused at t; the hash carries `t` only while paused.
API: `__viewer.cycle = {state(), setTime(t), play(), pause(), frac(g,t)}`. Axes/pivots are in the GLB frame (up=y, same as the CAD). Not covered: procedural
extras (folding packaging, a stack of products, sprung punches), a part that changes group. `test_site.py` runs the cycle checks when the site has a cycle.

### Notes on the model (`build.py --feedback URL`, `feedback.mjs` -> `feedback.js`)

A pencil button on the 3D ("Draw a note") opens a drawing toolbar (select, pencil, line, arrow, rectangle, oval, text, ×). The cycle / sim pauses
when the first stroke starts (not when a tool is picked): that moment is the note's, and the cycle bar hides while the note has shapes. A
shape is drawn on a 2D canvas over the WebGL one, then lives in the 3D scene (stage 2 below). Finished shape -> a popup at the bottom ("What is wrong here?",
Send / Cancel). Send POSTs JSON to config.json `feedback` (absolute or relative):
`{comment, image: "data:image/jpeg;base64,..." (the view on screen at Send, with the 3D shapes in it; <= 1600 px), images?: [the earlier views'
pictures, each as it was after its last shape], view: {url, hash, cycle_t, parts, shapes, views, camera}}` — all pictures together under 700 KB
of base64 (quality first, then pixels), the host nginx caps bodies at 1 MB; the router sends 2+ pictures as one album`;
2xx -> "Sent ✓" and the drawing clears, otherwise the server's `error` text. Without `--feedback` there is no button and `feedback.js` is not shipped.
Each shape is cast into the model when it is finished, while the camera is still the frame's (`projectShapes` in feedback.mjs):
`shapes: [{type, text?, closed?, px, pts: [{px, role, part, n, at, normal} | {px, role, miss}], inside?}]` — an arrow's `tail`/`tip`, a line's
`end`/`along`, an area's (rect, oval, a closed pencil loop) `centre`/`edge` plus `inside` = part id -> grid samples inside it ("" = empty space),
an open stroke's `stroke` samples, a text's `anchor`; `at` mm and `normal` in the model's assembly frame (the CAD's axes, as parts.json bbox_mm).
`camera: {w, h, fov, near, far, position, target, up, view, proj}` (same frame; 4x4 row-major). `parts` = the ids the shapes hit. No `selected`:
the last clicked part is not what was drawn. The router turns this into the agents' `drawing` text and `build/notes/<msg id>.json`, from which
`scripts/understood.py <note id> [--parts ...] [--points ...] [--at X,Y,Z:label]` renders the same view with the understood parts in magenta
and numbered markers ("understood like this"; a worker job, headless Chromium + SwiftShader). Hooks: `__viewer.pick(x, y)` -> `{part, n, point, normal}`,
`__viewer.toAsm(v, dir)`, `__viewer.camInfo()`.
Stage 2: a finished shape also gets `path` (its 3D polyline, assembly mm; on the surface, a sample over empty space continues the wall of
the nearest sample that hit) and becomes a 3D object in the scene (red tubes ~3 px wide whatever the zoom, an arrowhead cone, text as a
sprite; depth-tested, so a part in front hides it). The camera is no longer locked: with the select tool the model turns and the note can
be drawn on from another side; `views` lists the cameras it was drawn from, each shape's `view` indexes them, `camera` is the last, the
JPEG is the view at send. The plane tool (`data-tool="plane"`) takes a click on a face and puts the next shapes on that face's plane, also
past the part's edge (a translucent patch shows it; press again to go back): shape `plane`, key points `plane: true`, `off: true` past the
edge. understood.py draws the shapes in 3D in `--view N` and the agent's `--path "X,Y,Z;…:label"` sketch in green. Hooks: `__viewer.fromAsm`,
`__viewer.rayAt(x, y)`, `__viewer.THREE`, `__viewer.scene`.
Stage 3, ready-made marks (`data-tool` pin, move, face, dim): a pin is one click (shape `pin`, key point `pin`, `num` 1, 2, … in the order
placed; a red ball with its number); "from here to there" a drag (`move`, key points `from`/`to`, a dashed arrow); "this face" one click (`face`,
key point `face`, plus `face: {part, normal, centre, area, size, plane}` from `faceRegion` = the triangles of that part sharing edges with
the hit one and lying in its plane, tinted in 3D); a dimension a drag (`dim`, `from`/`to`, `length` mm, a line with "N.N mm" in the page language). The router's
breakdown names each (pin N; move with the shift (dx, dy, dz); this face with its plane "x = …", size and area; dimension with ΔX/ΔY/ΔZ) and
understood.py draws them. Hooks: `__viewer.meshTris(n)` (a mesh's triangles in assembly mm), `__viewer.pick()` also returns `face` (the
triangle index; the note keeps it as `face.tri`, so understood.py lights the same face).
Debug API: `__viewer.feedback = {open(), close(), tool(name), state()}`.
The part card also has a box "What should change in this part?" (or "…in this assembly?" for a node picked in the tree): it POSTs the same endpoint with
`view: {part, node, part_name, selected, parts: [part]}` and a frame with the selection highlighted. Both boxes have a mic: the recording goes to
the sibling `/stt/<project>` (the router's Whisper) and the text lands in the box for editing; nothing is sent until Send.

### File downloads (`build.py --kit auto`, `kit.mjs` -> `kit.js`)

Panel buttons "For 3D printing", "For laser cutting and bending", "Purchased parts (Excel)", "Model (STEP)", "Assembly drawings" (zip, one PDF per node, `derive.py assembly`); the part card "Download the part files", the node card
"Download the assembly archive". Everything is built from the CURRENT model when pressed: the router fingerprints the project's model sources; if they changed
since the last build it runs the project's export as a worker job (the button shows "The model is being updated, file in ~N s", the page downloads by itself when done; a link `#sel=<part>&dl=part` / `#dl=<scope>` from `tg link` starts it by itself), else
it packs from the last export at once. Buttons appear only when the project has `kit.json` (contract: skill `machine-project`, "Download kits").

### Flashing boards from the page (`build.py`, ON by default, `--flash DIR|none`; `flash.mjs` + `hw.mjs` -> `flash.js`, `hw.js`)

Every build gets it (`--flash none` turns it off): with nothing published, any board can still be connected and the bot can work with it.
The panel section "Boards on the desk" has "Connect a board" (Web Serial `requestPort`); each connected board gets its own window over the 3D (`.hwWin`):
name, chip, "Connected · <version>", one primary "Flash", "Reset the board", and "Log" that unfolds the serial log with a "Monitor" switch (hidden by default).
A flash by the bot shows "The bot is flashing…", the result "Flashed · hh:mm". A sim board row whose real board is connected has "Flash to the board".

**Windows and panel (`win.mjs`, always shipped):** the app panel, the sim board card and the board windows drag by their header and fold to a header in a
bottom-right row; position and fold are kept in localStorage `win.<project>`, double-click a header = back to the default place. Panel sections
(View / Simulation / Boards on the desk / Download files / Parts) fold on their heading, kept per project (`assembly.sec.<project>.<section>`); Parts takes the rest
of the panel height and scrolls inside. Tests drive boards with a fake `navigator.serial` + `window.__hwSessionFor(port)` (test hook): `test_panel_layout.py`.
Plug-in matching is by USB VID:PID class (`usbClass`, `matchCards`: one compatible card = automatic, several = a select, remembered in localStorage); the exact chip is checked at flash time.
The page also talks to the router (`/hw/<project>/event` POST, `/hw/<project>/cmd` long-poll, only while a board is connected: each poll holds one of the browser's 6 HTTP/1.1 connections to the host. Serial grants are per origin, so a plugged-in board makes every viewer tab of the site poll; only HTTP/2 on the host nginx removes that limit) so the agent's `hw flash|log|reset` run on the open viewer; with no router behind the page (a local static server) it just stops calling.
Firmware: `fw/index.json` (`{"boards":[...]}`) + `fw/<card>/manifest.json`, made by `skills/firmware-sim/scripts/fw_publish.py`. Missing index -> cards say "firmware not published". Web Serial absent -> one line "Flashing works in Chrome or Edge on a computer".
**Surviving rebuilds:** `build.py` wipes `--out`, so the published files live in `<in>/fw_publish/` (or `--flash DIR`) and are copied into `<site>/fw/` on every build. With `--sim`, `fw/index.json` is the simulator's firmware list (an array): it is kept under `"sim"` in the merged `{sim, boards}` (sim_ui reads both forms).
`flash.mjs` is per port: `new FlashSession(grantedPort)` / `sessionFor(port)`; the module-level functions are one default session that asks with `requestPort`. Tests: `node --test hw.test.mjs flash.test.mjs flash_esp.test.mjs`; `test_hw_idle_poll.py` (real Chromium, ~1 min, no site build: a tab without a board holds no poll, with a board it gets commands).

### Device (a device from the model, `device_ui.mjs`)

`sim/site.json` = `{"device": "../build/model/device.json", "fw": "../build/fw"}` (no `entry`, no machine.mjs): build.py copies it to
`site/sim/device.json`, sim_ui builds the machine with the framework's `deviceMeta` / `buildDevice` and mounts `device_ui.mjs`:
- window "Device" (win.mjs id `device`, right side): every display enlarged (pixelated canvas), a button per button element (its
  `label`, its key), ◀ / push / ▶ per encoder. The simulation bar shows no input buttons for a device.
- the same canvas is a texture on the display part in 3D: a plane over `params.glass` (centre / normal / up / size in the part's own mm),
  placed with the element's `loc` from device.json under the mesh's parent (the viewer's meshes are quantized: a mesh's own frame is not
  the part's). Without `glass`: the +Z face of the part's box.
- mouse held on a button's 3D part = held button (the press is not an orbit); the wheel over an encoder part turns it (not a zoom); keys:
  `params.key` held = held, `params.keys` [ccw, cw] one click per keydown, `params.push_key`. Not while typing in a field.
- only the dirty rectangle of a display is uploaded each frame; render stays on demand.
- `__viewer.sim.device = {elements(), down(id), up(id), press(id, ms), turn(id, n), screen(id) -> {w, h, on, frames, pixels}, shot(id) -> PNG data URL}`;
  `__viewer.sim.press(id)` presses a device button for 300 ms. Test: `test_device.py` (real Chromium).

### App panel (`site.json` -> `"app"`, `app_panel.mjs`)

The machine's own operator app (a web build) in an iframe next to the 3D, wired to a virtual board. Generic; enabled per machine:
`"app": {"path": "app/", "title": "...", "board": "master", "transport": "ble-bridge", "open": "desktop"|"always"|"lazy", "latency_ms": 40, "source": {...}}`.

- **External app (`"url"`):** `"app": {"url": "/ws/<workspace>/app/", "board": "master", "transport": "ble-bridge", ...}` iframes a build that lives elsewhere (a workspace's shared build, one for all its projects' viewers) instead of copying `path`/`source`; `build.py` copies nothing and the `ble-bridge` is unchanged. The build is made by the router's release job (`tg app-release`), not by `build.py`.
- **Placement:** docked right on desktop (420 px, `--app-w` moves the part/board cards left of it: a board card opens over the 3D, never over the app), bottom sheet on a phone (62 vh, folds to a strip; opening a board card folds it).
  `open`: `desktop` (default) = open on desktop, folded on a phone; `always`; `lazy`. `?app=0` never loads it, `?app=1` opens it. The choice is remembered (localStorage `assembly.appD|appM`).
- **Lazy loading:** the iframe is created when the panel first opens; the default desktop open starts it only after the model and the sim are up (never blocks them). A loading state with elapsed seconds shows until the app says hello.
- **`ble-bridge` transport** (postMessage JSON, wire format = the app's docs/SIM_BRIDGE.md): app->page `hello|scan|connect|write|disconnect` with `src: "<tag>-app"`, page->app `devices|connected|notify|disconnected` with `src: "<tag>-sim"`; the page takes `<tag>` from the app's first message (`app.bridge_tag` in site.json fixes it). `write` goes to the named board's RX characteristic
  (`Board.bleWrite`), every TX notification of that board (`VM.onBle`, survives Reset) goes back as `notify`. The device name and characteristics are **the firmware's** (virtual-controllers BLE stub reports `Board.ble = {name, service, chars}`), nothing is hard-coded.
  Every `hello`/`scan` is answered with a fresh `devices`; `event.source` is checked; once the board has advertised and the machine says `ready` the page connects by itself (not after the operator unpaired it in the app); a Reset / firmware switch drops the link and it re-connects when the new firmware is up.
  **Latency:** both directions are delayed by `latency_ms` (default 40) of MACHINE time, like a BLE connection interval. Not cosmetic: a firmware that answers `4` with `STATE:4` and clears its command register 50 ms later loses the `1` of an app reacting instantly (zero-latency bridge), and the machine never starts.
- **Build (`build.py`):** copies `source.repo/source.build` to `site/<path>` and rewrites `<base href>` to `./` (a Flutter build bakes `--base-href` in; `./` works under any prefix). `source` = `{name, repo (path relative to sim/), remote, branch, commit, version, build, build_cmd}` is the machine's link to the app repo:
  the build FAILS with the fix when the app build is missing, older than the commit, or the repo is checked out at another commit. The same record feeds the AAS (SoftwareNameplate, see step-import/AAS.md).
- API: `__viewer.sim.app = {open(), close(), state() -> {open, loaded, hello, connected, device, userDetached}, log (last 300 bridge messages), iframe()}`.


Traps

- **Up axis.** The importer stamps the GLB "Z-up (as in the STEP)". For many machine STEPs that is wrong: legs stand along
  Y, feet at their ends, the long axis is the conveyor line. Rendered Z-up the machine lies on its side. `build.py --up`
  defaults to `y` (glTF native, no rotation); `--up z` or `#up=z` rotates -90 deg about X. Always LOOK at the first render.
- The GLB is metres and has no normals: the page scales x1000 (world = mm) and computes creased normals at load (not `computeVertexNormals`: that smooths every edge).
- GLTFLoader mangles node names (spaces to `_`); the real name is `userData.name`. Part id is `userData.part` (from `extras`).
- Raycasting ignores `visible`: pick collects `traverseVisible` meshes only. Brute force over 2.6 M triangles is fine for a click.
- Software GL (headless default) does about 1 fps on this model and makes frame times meaningless; the test launches Chrome
  with `--use-angle=metal` (Mac) for fps, `--swiftshader` for boxes without a GPU (fps not judged). Scanning the screen with
  `pick()` while the camera is still flying gives wrong click points: wait for `state().flying === false`.
- `parts/` is most of the site (300+ MB of STEP+STL for a 250-part machine); the first page load there is 13.5 MB (model + vendor).
  Serve with gzip/brotli in front if the VPS allows: GLB and STL compress well, and the page shows real download progress
  only if the server sends `Content-Length`.
- `python` may not exist on the Mac, use `python3`.

### The shared app and its release (projects in a workspace)

Rules for a project whose router registry puts it in a workspace (its `app-src/` is a checkout of the shared app repo, branch `bot/twin`):
- After EVERY app change: go through `10c-dev:coding` (spec; tests and review sized to the diff, its §1), **bump `version:` in `app-src/pubspec.yaml`** (a version that was already released is refused), write a concrete test scenario (numbered steps in the viewer and what must be seen after each), then call `tg app-release --scenario "<text>"`. It builds the web version (the shared `/ws/<id>/app/` your viewer iframes) and a beta APK, and posts the scenario and the link in the chat.
- Do not push, merge, upload or tag anything yourself: the router does it after the test passes.
- If `turn.md` says another chat is editing the app right now, `app-src/` is read-only this turn: do not change the app.
- When the owner asks you to test it yourself: run the scenario in the web viewer with Playwright, step by step, take a screenshot of each check, send them with `tg file`, then report `tg app-test pass "<notes>"` or `tg app-test fail "<what differs>"`. Pass releases to production; fail releases nothing.
- A release that passed puts the APK on the project page of every workspace project (`public/apk/`, button "Download the app (APK)"; the router does it, never copy APKs into `public/` yourself). A workspace with an external server also uploads it there; one without (`release-config host=page`) only publishes it on the page.
