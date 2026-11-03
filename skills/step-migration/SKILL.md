---
name: step-migration
description: Use whenever the client sends a STEP / a SolidWorks or other CAD model — of a whole machine ("here is the model", "sent you the STEP", "the SolidWorks assembly", in whatever language he writes; a .step/.stp/.sldasm in the chat) or of one node for a machine we already have ("a new node", "replace this node"). His CAD is never kept as the model: it is PORTED onto our code model (one file per part, nodes docked by joints) by step_to_project.py, the parts the converter cannot prove are drawn by hand one by one, and migration_check.py proves the client will not notice the difference. Long work: a runner job and issue turns, never a chat turn.
---

# A client's STEP is ported onto the code model

His STEP is the input of a port, never the project's model. After the port the machine lives only as code
(`machine-project` «Code model»), and the client cannot tell the difference: the same parts, the same names and drawing
numbers, every part where his CAD has it, the same download lists and the same views on the page. The scripts do
everything that can be decided mechanically. This page is what needs your judgement.

The framework is at `${DE_ROOT:-/opt/design-engineer}/skills`; work in the project root (`/work`).

## Who does what

- **Manager**: one line to the client in his language: the model is being ported and when it will be ready
  ("I am moving your model into our system, it will be ready tomorrow at noon"). Then `issue new --quote <message id>`;
  the attachment rides in the quote. Never anything more technical.
- **Executor**: the steps below. The path: step_import.py cuts the STEP, step_to_project.py writes the code project, the
  parts it leaves are drawn by hand one by one, migration_check.py proves the client will not notice, check.py checks the
  project. Recognition takes 1–2 h and the parts follow one by one, so it never runs inside a turn. The converter runs
  as a runner job (`job run --needs heavy --expect long`: a heavy worker, never the server the bots run on). You end the turn and the finished
  job wakes you.

## Steps

1. **Skeleton, in one job.** First `mkdir -p build/client_step && cp inbox/<file> build/client_step/`: a job never
   gets `inbox/`, the file it names elsewhere it does. For a node, add `--node <parent node folder>`. A first port writes the whole
   `mechanics/cad/parts/` and refuses files it did not write there (delete a scaffold's example parts first).
   ```
   job run --needs heavy --expect long --timeout 14400 "python ${DE_ROOT:-/opt/design-engineer}/skills/step-import/scripts/step_to_project.py build/client_step/<file>.step . --title '<his name>' --library build/clients/<client id>"
   ```
   The job cuts the STEP (step_import.py), recreates what its recognisers can prove (turned, sheet, prism), and puts
   standard screws, nuts and washers on the framework templates. It writes one folder per sub-assembly and one file per
   part, docks every part where his CAD has it, and writes `mechanics/cad/migration/report.json`.
2. **Bought parts → his library.** The job writes them to `build/clients/<client id>/library/`. Merge them into
   `/clients/<client id>/library/` (his level, the same mount as his rules.md) with the script, never by copying: another
   of his machines may have a part of that name already.
   `python ${DE_ROOT:-/opt/design-engineer}/skills/step-import/scripts/merge_library.py build/clients/<client id> /clients/<client id> .`
   (the same geometry is reused, another one gets a free name and the part files import it), then
   `python ${DE_ROOT:-/opt/design-engineer}/skills/machine-project/scripts/part_library.py sync . --mount /clients/<client id>`.
   Never into the framework catalogue: every client sees that.
3. **The rest by hand.** Read `report.json`: every made part of kind `client` still stands on his geometry
   (`vendor/client/`). Write its `shape()` in its file (build123d, numbers in the node's `params.py`), then delete its
   `standin_reason` and its `vendor/client/<id>.step`. After the router's rebuild (the "model vN is ready" message), check that part:
   `job run "python ${DE_ROOT:-/opt/design-engineer}/skills/step-import/scripts/migration_check.py . --import build/client_import --part <id>"`. Repeat until
   it passes, one part or a few per turn.
4. **Proof.** In one long job (`job run --needs heavy --expect long`):
   `migration_check.py . --import build/client_import --before <the old project's files, if any> --shots <old site> <new site>`.
   Then `check.py` in full. Its report goes to the owner as it is.
5. **Schemes.** If the old project had electric or pneumatic schemes (PDFs, `--link` buttons), give the parts their
   `ports` and write `mechanics/cad/connections.py` (`machine-project` «Machine schemes»). Then prove with a script that
   every connection of the old sheets is there.

A node STEP for an existing project: `--node` puts it into one new folder under that node and touches nothing else
(its STEP is cut into `build/node_import/<name>/`, its records go to `migration/<node>/`; check its parts with
`--import build/node_import/<name>`, the check finds the node's records and frame by itself).
`migration/<node>/placement.json` says where his CAD has the node's anchor part; dock the node's `build()` there.
Its `frame` says what that is worth: `machine` (he exported his machine with only the node shown) is his place for it and
`--part` fails the node anywhere else; `own` (the node exported on its own) is the node's own origin, so where it goes in
the machine is yours to decide (ask him if the drawings do not say), and the check reports it as not checked. A
converted parent docks its children by their place in its list, so append the node at the END of the parent's returned
list (the indexes of the others stay), or dock it from a node of your own. A part id the project already has (a shared
screw, the node it replaces) gets the next free id in the node (`<id>_2`, `his_id` in its report row); name and drawing
number stay his. Replacing a node: delete the old node's folder and its `migration/<node>/` first.

## Judgement: drawing the parts the converter left

- Read the part through sections. Run `recreate._flat_code` on a section face: it gives the outline with exact arcs.
  Rebuild it the way the designer drew it: a profile revolved, a sheet bent, a box with features. Do not trace it.
- Toothed pulleys and rollers: a turned rim minus one tooth gap, patterned. Close the gap's outline above the land, or
  slivers of the land stay behind.
- Chamfers: his D-bore chamfer is a cone clipped by the flat, not OCC's `chamfer()` on the edges. Compare both.
- Lettering: `("spline", [...])` outlines, cut after the sheet is fused. A polyline letter makes OCC's fuse hang.
- Only castings and free-form surfaces may stay on his geometry, each with its `standin_reason` saying why. A bought part
  never stays there: it comes from his library.
- Never insert a part inside a converted node's list. Its `build()` docks the children by their place in the list, so
  one more part in the middle shifts all of them. Parts his CAD does not draw (electronics the wiring needs, say) go into a folder of their
  own, docked from the root node onto his parts by name. List each one in `migration/added.json` with the reason.
- The project moved on while you ported (the agent kept working on his STEP): before the switch, merge its main into the
  port and build what changed in code, as the page shows it now; check against a fresh snapshot of the live project
  (`--before`, `--shots`). Copies of his parts the client had removed (one controller of his 8, say) are dropped in the root node,
  after all docking (the converted lists below keep every copy), and listed in `migration/removed.json`
  `{part id or group path: {"copies": n, "why": ...}}`; a group of parts added on purpose goes into `added.json` by its path.
- One drawing number on two different parts: the converter gives the second `<number>-01` (a variant, GOST 2.113). Keep it.
- A sheet part that `derive.py laser` cannot unfold stays `process="other"` (a STEP, as before), with the reason in a
  comment.
- `check.py` «docked parts clash» on his CAD as drawn: a belt round its rollers is `fit=False`. Any other overlap
  is a finding about his design: ask the owner (`issue ask`) and hide nothing.

## Pitfalls met porting a machine with a project history (scripts of ours, purchase rows, stand-ins)

- His STEP patched by our own scripts (we had added parts for him): port the patched STEP, the model he sees, then write
  our parts' `shape()` from those scripts' code. Such a part sits in the coordinates the script built it in, often the
  machine's, so its numbers are machine coordinates in the node's `params.py`.
- A project with its own purchase rows (`mechanics/export/parts_meta.json` of a legacy `"model": "steps"` kit): give them to the
  port with `--overrides` (desc, code, link, supplier, unit, class, kind, qty, counted_in; process / thickness of made
  parts). There `qty` is per placement, not the row's total. A copy of one product (`_2`, a SolidWorks mirror) merges into
  its row by name and code: keep `counted_in` only for real pieces of a vendor assembly.
- A stand-in we drew for a bought part (a spring, a belt, a spacer: no maker 3D) is a library part as code, its sizes
  passed in by the project's part file (a spring hangs between two pins, a belt runs round two pulleys).
- A recognised sheet can match his geometry and still not unfold: run `derive.py laser` after the port. A sheet whose
  own STEP does not unfold either stays `process="other"`. One his STEP unfolds gets its bends redrawn as true cylinder
  sectors with only their ends cut, not lofted. Compare the old kit's lists first: a part that had a DXF must keep it.
- A library STEP over 20 MB (a detailed board, 83 MB): keep it as `.brep` (`export_brep` / `import_brep`). It has the
  same faces, is 4–5 times smaller and loads in 0.3 s instead of 20 s.
- The viewer's node names are his product names, so the motion paths keyed on them keep working. Check every path of
  `motion.json` against the new GLB anyway, and report a path that was already dead before the port.
- His old purchase list can be wrong (4 motors listed, 6 in his CAD), and his STEP can carry an old kernel's defect (a
  spring wire with a piece missing). The model keeps the truth. The difference goes to the owner as a finding, never
  into the code.

## Pitfalls met porting a large machine (two line variants, 500 MB STEPs)

- Two machines on one page (two line variants, each its own 500 MB STEP): cut each one (5 min, 4 GB each), then
  `import_merge.py` them under the page's root name (skill `step-import`). Check its totals against the old page's
  parts.json (parts, placements, nodes, bbox) before porting.
- One of his machines inside another (each line of one machine holds the whole of another): a part of the same geometry and frame
  in another of his projects is drawn once. Copy its `shape()`, keep your own `build()` (the joints are this project's).
- Most sheets the recogniser left missed only at a bend end ("surface up to 0.5-4 mm", XOR ~0 %): the recogniser now
  fits SolidWorks' cut ends (`bend(..., ends=...)`). What remains is a jog, a relief, a hem, a chamfer running from a panel
  into the bend: start from the recogniser's own candidate and fix the region compare points at.
- A welded part (several bodies in his file) passes only as a Compound of its bodies, not fused by `sheet()`.
- Two rounds that run into each other (a pin's foot round under an edge round) are a blend OCC cannot build: the part keeps
  his geometry, its standin_reason says so.
- A perforated drum (thousands of holes): the skin, then all holes cut by one patterned tool. compare() cannot finish on
  it: prove it by its hole centres, volume and sampled exact deviation, and give those numbers.
- A converter run of hundreds of parts: its job must not die with the session that started it, and it writes nothing
  until it finishes. Its log reports docking node by node (`docking k/N`): a node that does not move for long is the one
  to look at.
