---
name: step-import
description: Turn a vendor/SolidWorks STEP assembly (AP203/AP214, no source CAD) into a digital-twin dataset - an instanced, hierarchical GLB in metres, a parts.json catalogue (id, name, drawing number, made/standard/purchased, module, instance count, bbox, volume) and per-part STEP + STL; and compare two exports of one machine without a CAD stack (`scripts/step_struct.py`, stdlib only, no OCP: structure, placement counts, what moved since the last export). A client's STEP is never the model: it is ported onto the code model (skill `step-migration`, `scripts/step_to_project.py`); use this skill for the scripts of that port, when the customer sends a new export and the question is what changed, when a viewer needs a click-to-catalogue mapping, or when one part of a big assembly has to be printed or re-modelled.
---

# STEP assembly -> GLB + parts catalogue

The import is `scripts/step_import.py`; `scripts/step_struct.py` reads structure only, with no OCP (below).
Feeds `web-viewer` later; per-part files feed `fdm-print` / `cad-build123d`.

## Pipeline

```
STEP -> STEPCAFControl_Reader (XCAF, names on) -> walk tree (component -> referred label)
     -> key = referred-label entry (real instancing, not a content hash)
     -> BRepMesh_IncrementalMesh once per distinct shape -> triangles + B-rep normals (mm)
     -> model.glb   one glTF mesh per distinct part, one node per instance, nested assembly nodes, metres
     -> parts.json  catalogue + assembly summary + assemblies-only tree
     -> parts/<id>.step|.stl   local coordinates, from the same triangles as the GLB
```

```bash
env -u VIRTUAL_ENV uv run --isolated --no-project --with cadquery-ocp --with trimesh --with numpy \
  python -u scripts/step_import.py in.step --out build/ [--deflection 0.5] [--angle 0.5] [--no-parts]
```

A 163 MB STEP (1103 nodes, 914 instances, 251 distinct parts): ~35 s load, ~15 s mesh, ~1 min total, peak RAM ~7 GB.

## Output

- `model.glb` Z-up like the STEP (no axis swap: bbox stays comparable to the source; rotate -90 deg about X in the viewer/Blender
  if needed). Asset extras: `units: "m"`, `source_units: "mm"`, `up: "Z"`, tessellation. Every mesh node has `extras.part` = part id.
- `parts.json` -> `{assembly: {root, nodes, instances, distinct_parts, triangles, bbox_mm, tessellation, tree}, parts: [...]}`.
  Part `id` = drawing number (four groups `01.02.03.456`, or three: `02.01.121` - makers number both ways) else slug
  of the name; duplicates get `_2`. `kind`: drawing number => made;
  DIN/ISO/nut/screw/washer/bearing/rivet words and bearing codes (`6000-2RS`, `608ZZ`, `10x26x8_6000`) => standard; rest => purchased.
  `module` = `03.MM` of own or nearest ancestor drawing.
- `material` per part (steel|printed|rubber|fastener|bearing|motor|electronics|pneumatic|glass|other) from an ordered name-regex table
  `MATERIAL_RULES` in the script (made parts are only printed = the name says printed, `import_i18n.WORDS` `printed`, else steel). Tune the table, then
  `python3 scripts/step_import.py --reclassify build/` rewrites kind + material in parts.json in seconds (no STEP, no OCP, plain python3)
  and prints the breakdown, 15 samples and the `other` names. (A 251-part machine: 148 steel, 29 electronics, 25 pneumatic, 13 fastener, 10 bearing.)
- `type` + `params` per part (`scripts/params.py`, ordered `RULES` table; look-alike letters of other scripts normalised to Latin before matching): pulley, belt,
  conveyor_belt, fastener, rivet_nut, stud, foot, bearing, bearing_unit, stepper_motor, stepper_driver, power_supply, pneumatic_cylinder, valve,
  air_preparation_unit, pneumatic_fitting, connector, cable_channel, din_rail, mcu_board ... else `type: null`. Made parts (drawing no.) are not decoded except pulleys.
  `params` = English snake_case, unit in the key (`_mm`, `_w`, `_a`, `_v`). `params_source` = per key `name|geometry|catalogue|derived`
  (catalogue = datasheet knowledge in the script, e.g. 6000-2RS = 10x26x8, NEMA23 face 56.4). `params_check` = keys confirmed by the mesh;
  `params_conflicts` = name/catalogue value AND geometry value, both kept (params holds the name one). Geometry (needs numpy + `parts/<id>.stl`):
  pulley teeth by FFT of the tooth outline, screw overall length, bearing D/B, belt width, motor face/body, channel/rail dims.
  Run `--reclassify` with numpy (`uv run --with numpy python ...`) to get the geometry checks; plain python3 rewrites params from names only and drops `params_check`.
  On a 251-part machine: 103 typed / 95 with params; all 7 pulley tooth counts confirmed by geometry. Belt loop length is not extracted.
- `parts/` with `--no-parts` is skipped and `files` is null.

## Port a client's machine onto the framework (`step_to_project.py`)

A machine the client drew in his CAD (SolidWorks) and sent as a STEP is not a separate kind of project: it is ported, once,
onto the code model (skill `machine-project` «Code model») and lives and changes only there. His STEP is the input of the
port, never the source of truth after it. Everything is a script; the agent runs it and reads the report. The agent's
side of it (who does what, what needs judgement) is the skill `step-migration`.

The path: step_import.py cuts the STEP, step_to_project.py writes the code project, the parts it leaves on his geometry
are drawn by hand one by one, migration_check.py proves the client will not notice, check.py checks the project.
A STEP of a node, a sub-assembly for an existing project, goes the same way into that project: `--node <parent node folder>`.
Recognition takes 1–2 h, so the port never runs inside a turn: it is a runner job (`job run --needs heavy --expect long --timeout 14400 "…"`)
and the executor's turns of the issue. The manager tells the client in one line that the model is being ported and when it will be ready.

```bash
python step_to_project.py "MACHINE.STEP" . [--title T] [--overrides mechanics/parts_overrides.json] [--library LIB]
# cuts the STEP into build/client_import with step_import.py first; LIB = the client's library root: /clients/<client id> where
# it is mounted, else a local folder named clients/<client id>, merged into /clients/<client id> by merge_library.py (never copied:
# python $S/merge_library.py build/clients/<client id> /clients/<client id> . reuses the same geometry, renames a clash)
python $S/../machine-project/scripts/part_library.py sync . --mount LIB     # with --library
python migration_check.py . --import build/client_import --before <copy of the old project> [--shots OLD_SITE NEW_SITE]
```

Several machines in one project (variants of a line, each its own STEP, too big to cut together): cut each with
step_import.py into its own dir, then `python import_merge.py build/client_import --root "<the page's root name>" build/line1
build/line2 [--gap 1000] [--floor-same 500] [--up y|z]` and port build/client_import as usual. The first stays where it is,
each next one stands past the previous along the floor; a part (name, drawing, triangles, volume, box) or a sub-assembly
the inputs share is one part / one node folder; OUT is written afresh and may not be an input.

- **Nodes**: one folder per distinct sub-assembly product of his tree, nested like his tree, `TITLE` = his product name
  (the viewer shows that name per copy, so his tree and the motion paths keyed on it stay as they were). Products that
  share a name but differ inside (SolidWorks configurations) are separate folders.
- **Parts**: one file per distinct part, in the first node that uses it, id = his id with `.` as `_` (the framework's id
  spelling), `title` = his name, `code` = his drawing number. Made parts are recreated by `recreate.py`: `turned`
  (profile revolved, params `d1..` / `z1..`), `sheet` (panels + bends, trimmed corner bends lofted; params `thickness`,
  `bend_radius`; process `laser` or `bent`), `prism` (slabs along an axis). Every recreation is built and compared with
  his part (`recreate.compare`: volume 0.5 %, bbox 0.05 mm, surface deviation 0.1 mm, XOR volume 1 %, same round holes),
  and only a match is kept; a part nothing matches stays on his geometry (`vendor/client/<id>.step`) with
  `standin_reason` saying why, and `migration/report.json` lists them. The numbers are in the node's `params.py`.
- **Bought parts**: a screw / nut / washer the framework templates draw (size from his name or the shaft, the variant
  whose box is within 0.6 mm or 6 % of his) becomes a part file wrapping the template where his stands, its row a real
  code (`M6x20 DIN 933`). Anything else keeps the maker's 3D his CAD carries (`vendor/client/`), kind from `--overrides`;
  with `--library build/clients/<client id>` (then merge_library.py into /clients/<client id>) it becomes a part of the client's library (his level: `library/<group>/<part>.py`
  + its STEP; never the framework's catalogue, which every client sees), synced into the project by `part_library.py sync`;
  add `mechanics/cad/library/**/*` to kit.json `inputs` yourself (the converter does not touch kit.json).
- **Placement**: in each node the largest made part is the anchor; every other part docks onto the nearest part already
  docked, the joint on that part's surface at the closest point; a template fastener docks by its own `head` / `seat`.
  Every placement ends where his stands (to 1e-6 mm in the tests; `migration_check.py` fails anything beyond 1e-3 mm).
- **Then the agent, part by part.** The script does what is unambiguous (tree, files, names and numbers, places, standard
  fasteners, the check) and recreates what its recognisers can prove; it never pretends. Every part it leaves on his
  geometry (`report.json` `totals.client_geometry`, each with its reason) is agent work: write that part's `shape()` in its
  file by hand (build123d, numbers in the node's `params.py`), drop its `vendor/client/<id>.step` and `standin_reason`,
  rebuild, and run `migration_check.py ROOT --import build/client_import --part <id>` until it passes. A part that honestly
  cannot be drawn parametrically (a casting, free-form surfaces) keeps his geometry and its `standin_reason` says why.
  What helped on one port (13 parts left): sections of his part along an axis (`recreate._flat_code` on a section
  face) give outlines with exact arcs; a toothed pulley or roller = a turned rim minus one tooth gap patterned (the gap
  closed above the land, so no sliver of the land is left); a chamfer the designer drew as a cone clipped by a flat is
  not OCC's `chamfer()`; lettering = `("spline", [...])` outlines cut after the sheet is fused (a polyline letter
  makes OCC's fuse hang). `migration_check --part` counts such a part `by hand`. A re-run of the converter keeps every file
  under parts/ and vendor/client/ the agent wrote or changed (migration/generated.json holds what the converter wrote),
  and lists them in report.json `kept`.
- **Report** (`migration_check.py`, exit 1 on a FAIL; parts added to his machine on purpose, e.g. electronics his CAD
  does not draw, are listed in `mechanics/cad/migration/added.json` `{part id or group path: why}`, copies of his removed on
  purpose in `migration/removed.json` `{part id or group path: {"copies": n, "why": ...}}`; both reported, not failed): parts, placement counts and positions, names and drawing numbers,
  geometry per part, print / laser / STEP / bought lists before and after, the viewer tree, screenshots from the standard
  views side by side. Its `report.md` is what goes to the owner.
- Slow parts (a perforated sheet with hundreds of holes) take minutes; one part's recognition runs in a child process
  and gives up after 15 min (the part stays on his geometry with that reason, and is tried again on the next run; a crash
  likewise). Results are cached in `build/step_to_project/`
  by the part's STEP and the converter's code, so a re-run recomputes only what changed. Run it on a Mac or the PC,
  never on the VPS.

## Structure and re-export diff, without OCP

`python3 scripts/step_struct.py FILE.STEP [--json OUT.json] [--diff NEWER.STEP]` (stdlib only) reads the product
structure straight out of the STEP text: product count, distinct names, usage links, every top-level product (a client
file can hold two production lines, so the largest assembly is the root and `roots` lists them all), the assembly tree
with drawing numbers, **how many times each product sits in the machine** (a sub-assembly placed twice places
everything inside it twice - the same number `step_import.py` records as instances, checked: on a 163 MB
export both say 1103 placements in all and 914 of parts), and the sha256 + header stamp (`exported <date> by
<system>`) that is the only way to tell two exports apart, since the client's file name never changes. ~8 s on a
199 MB STEP against ~1 min and ~7 GB of RAM for a full import, so this is what to run when the client sends a new
export and the question is only what moved. `--diff` reports: names in both, gone, new, kept-with-a-different-placement-count,
and renames under one drawing number (with `--json`, that report as JSON). On two real exports of one
machine, two weeks apart, it printed 316 of 324 names kept, 8 gone, 15 new, 13 placement counts changed, 1 rename - the
same -8/+15 the full import of that export recorded.

It reads names, never geometry: a part redrawn under the same name with the same placement count is invisible to it,
and so is a whole machine moved 20 mm (which that later export also did). Use it to decide whether a re-import is
worth it and what to look at, never as proof that nothing changed.

Where it and `parts.json` stop lining up - the totals agree, the rows do not, so compare totals and not row by row:

- **Totals** match: `sum(products.values())` = `assembly.nodes`, and the leaves of that sum = `assembly.instances`
  (1103 / 914 on the 163 MB export above, both tools).
- **Rows** do not. `products` is keyed by product **name** and holds assemblies as well as parts; `parts.json` is
  keyed by the XCAF label with a `_2` suffix for a repeated name, and holds leaves only. Two different products with
  one name are one row here and two there, and every assembly is a row here and none there.
- **The drawing number** comes from the product name here; `step_import.py` also looks at the instance (NAUO) name,
  so a number carried only on the instance is found there and not here.
- **Several assemblies in one file:** `step_import.py` walks only the first top-level shape (`free.Value(1)`), so its
  counts cover the one it picked, while this lists every top-level product - and the summary says so when it finds
  more than one. A definition with no parent and no children is in no machine at all: it is listed as loose and
  counted nowhere.

The tool builds nothing and publishes nothing, so it is deliberately **not** in the router's guarded-script list
(unlike `step_import.py`): an agent may run it on a worker whenever a new export lands.

Loud, never silent: a reference cycle, a product whose name cannot be resolved, a tree cut at the node budget, and a
record the parse could not read (counted against the entity starts found in the raw text) each print a WARNING.
`/* … */` comments are not stripped, so an apostrophe inside one - like any unbalanced apostrophe - makes the records
after it unreadable; that shows up as «only N of the M PRODUCT records could be read», not as a clean report.
`--json` refuses a path that is one of the files being read.

Drawing numbers of three groups were added later than four. `--reclassify` re-reads the `drawing` already stored in
parts.json and never re-derives it, so a project imported before that change needs a **full re-import** to pick the
numbers up - reclassify alone will not. That re-import also changes those parts' `id` (from a name slug to the
number, `p["drawing"] or slug(p["name"])`), and the `id` is what the viewer links, the kit manifests and the page
notes key on. To find what goes stale, diff the id lists either side of the re-import
(`python3 -c "import json;print('\n'.join(p['id'] for p in json.load(open('build/parts.json'))['parts']))"` before and
after) and grep each disappeared id in the project: `public/`, `build/kit/*.json`, `wiki/`, `tasks.json`, `aas/*.json`.

## Feature edges (CAD look), separate script

`uv run scripts/bake_edges.py build/` (numpy only, ~1 s, no OCP) reads `build/model.glb` and writes `build/edges.bin` + `edges.json`: per distinct part the
crease edges (dihedral > 30 deg, measured in real space, not in the quantised int16 space) plus boundary edges, as int16 vertices + index pairs in the
mesh's own space. A 251-part machine: 937 k distinct triangles -> 360 k edges, 4.2 MB (2.1 MB gzipped). `web-viewer/templates/assembly/build.py` copies them when
present and the page draws them (contours). Re-run after every re-import; it is not part of `step_import.py`. Silhouettes of smooth surfaces are not
crease edges: the viewer adds those in screen space.

## Traps

- **Names**: read `TDataStd_Name` on the instance label, fall back to the referred (product) label. `RWGltf_CafWriter` defaults give `NAUO123`;
  the instance label name is often `NAUOnnn` too, hence the fallback.
- **RWGltf_CafWriter does not instance** (every instance a full mesh copy, ~30 MB coarse) - that is why the GLB is written by hand.
- OCP binding: `TopoDS.Face(...)` not `Face_s`; `Bnd_Box.Get()` unusable; sequence type is `OCP.OCP.collections.Sequence_TDF_Label`;
  `tri.Node(i)`/`tri.Triangle(i).Get()` are 1-based. Run python with `-u`.
- **Normals**: `BRepLib_ToolTriangulatedShape.ComputeNormals_s` per face (negated for reversed faces), written as int8 NORMAL (KHR_mesh_quantization, required); vertices weld only if position AND quantised normal match, so edges stay crisp and cylinders smooth with no browser work. A 251-part GLB 12.5 -> 20.7 MB; `--quantize-positions` (int16 pos + dequant matrix on the instance node) -> 17.4 MB, but trimesh does not dequantise those (three/Blender do).
- Per-face triangulation duplicates boundary nodes: weld (1e-4 mm + normal) or the GLB is twice as big; use uint16 indices below 65k verts.
  Reversed faces need the winding flipped or STLs are not watertight.
- `STEPControl_Writer` prints ~10 lines per part to fd 1; the script silences it with an fd dup2.
- Same drawing number can hide different geometry (mirrored/variant parts): the id gets a `_2` suffix, so ids are not stable across STEP re-exports.
- The volume in parts.json is exact (B-rep); STL volume is the tessellated one - curved parts at 0.5 mm deflection are 1-4 % off. Use `--deflection 0.1` if the STL will be printed or measured.
- Global bbox is computed from transformed real vertices (a corner-based bbox overshoots by ~10 mm).
- Thin sub-0.5 mm features vanish or degenerate at coarse settings (glTF validator: harmless "degenerate triangle" infos).
- No colours in AP203: single grey material. `gltf-validator` has no CLI via npx; `npm i gltf-validator` in a scratch dir and call `validateBytes`.

## Machine model = AAS (source of truth)
`scripts/aas_build.py` turns build/ (parts.json, model.glb, parts/) + measured kinematics into the machine's Asset Administration Shell:
`<repo>/aas/<machine>.aas.json` (committed, canonical) and `build/<machine>.aasx` (validated by aas-test-engines). parts.json and
mechanics/kinematics/motion.json are then derived from it (`aas_export.py`, `aas_check.py` = round trip). Numbers live in the AAS, not in the wiki.
Layout, ids, standard-vs-ours, commands: [AAS.md](AAS.md); own semanticIds with units and notes: [semantics.csv](semantics.csv).

`--mesh-parts JSON`: parts that exist only as meshes (bought units imported as STL, concept boxes; no B-rep), a list of `{name, group, color, pos, idx}` in mm of the STEP frame. Each becomes a part (card, STL download, no STEP) under its group node (e.g. an electrical cabinet drawn as meshes).
