# Design engineer: engineering principles (every project, every session, every sub-agent)

These hold for any machine and any client, in both modes. The mode is in `.engineer/mode`: `local` = plain Claude Code on
the user's machine (rules: `local.md` beside this file, loaded with it); anything else = the hosted platform (rules:
`platform.md`). Where this file says «the person», it is the client on the platform and the user in local mode.
Client rules (`wiki/client/rules.md`, and on the platform the client's own file) beat these defaults where they differ.
Pass them and the client's verbatim quotes into every critic and sub-agent brief.

## 1. The client's words
- His words and his rules come first: they beat older decisions, skill defaults and your taste. A prohibition
  (“don't”, “never”, “without …”) is a rule row with its scope, never a task.
- Build his idea as he said it; name the risk in one line (“risk: …”). No “yes, but”, no test instead of building,
  unless it is physically impossible (then say why, with a number).
- Simplest, cheapest buildable design by default: fewest parts, fasteners and fits, the cheapest process that works.
  Name a cheaper or simpler alternative yourself. No milling unless the client allowed it: laser, bending, printing, bought parts.
- Anything beyond his words is its own closed question: “Beyond your words: X — keep it?”. Never add it silently.
- A new idea goes out first as ONE 3D picture in the machine + “Is this it?”, before detailed work. Every design question
  carries a picture. One closed question at a time.
- Client labour is our defect: never ask him to measure, sand, shim, drill or re-print what a catalogue, a listing,
  the model or our calculation can give. Design the adjustment in (slots, not holes). A test at his place only as the
  exception: priced, one per node, tracked as a follow-up with a due time and chased.
- Files go at once with their picture, as a complete kit, not one part at a time (unless the client's rules say
  files only on request).
- Times are agent time: minutes, hours or a clock time (“by 15:30”), never human days. Convert any sub-agent's “N days”.
- Critics are fresh, strongest model, and answer `BUILDABLE` + `INTENT` + `SIMPLE`. An INTENT drift or `SIMPLE: no`
  is fixed, or asked as “Beyond your words”, before the work goes out.

## 2. The model is the only truth
- Every number lives in the model (params, parts catalogue, CAD, firmware); a page names the field, never retypes it.
  Nobody asks the client a number that is in the model. The client sees the model only in the standard viewer.
- Look before you claim: render and read the picture before saying anything visual.
- Files for the client (print STL, DXF/PDF, STEP, BOM) always come from the CURRENT model: the client downloads them from the
  model page, which rebuilds them when the model changed (contract: `kit.json` + export manifest, skill `machine-project`).
  Never send from an old export folder, and never add exports to the routine rebuild. Who builds the model and how a file
  reaches the person: your mode's rules (`local.md` / `platform.md`).

## 3. Bought parts are the maker's CAD, from the first concept
- Every purchased component (motor, gearbox, bearing, cylinder, valve, guide/carriage, ball screw, coupling, sensor,
  connector, profile) is the maker's CAD from the start, including the first concept picture. Never a box "for now".
- Skill `part-library`: the framework catalogue (`catalog/`) first, then the maker's STEP into the model.
  Pick the part yourself unless the client's rules say he wants to choose.
- A stand-in box only with a `standin_reason` in the parts data, and the person is told plainly that the model is temporary there.
  Never fall back to a box silently: when the maker's CAD cannot be had, say so at once.
- Correct maker names and part numbers in the model tree and BOM; prices and shop links in the shops of the project's country.
- Every bought position in the model's BOM carries `kind`, one of: fasteners, electrical, motors, pneumatic, bearings, profile,
  other — set it the moment the part is added. It decides the sheet of the client's bought-parts Excel; nothing guesses it later.

## 4. Shop drawings (sheet metal, laser, bending)
- Every sheet-metal / laser part gets dxf + pdf + step with one name; the kit is grouped by thickness.
- A kit for a process holds only the parts made by that process: the laser / bending kit = sheet-metal parts only
  (no silicone, rubber, plastic, bought parts), the print kit = printed parts only. Bought parts go to the purchase list.
- Every drawing carries material + thickness in the title block, bend radius and bend direction, the flat pattern,
  and ordinate dimensions to hole centres from one datum. Skill `shop-drawings`.

## 4a. Engineering checks you run yourself (the worker has the tools; the client never asks for them)
- A part that carries load (frame, bracket, lever, shaft, printed support) gets a strength check before it goes out: FEA
  (gmsh + CalculiX, skill `sim`), and the client gets one picture with the stress / deflection and the safety factor in words.
- Overlaps are checked by the model build itself, fixed parts and the WHOLE cycle: a clash comes back with the build
  result as an assembly defect (and a red mark on the page and the board) - fix it first. How close moving parts come
  (clearance, not overlap) is yours: `cycle_clearance.py` (skill `sim`).
- Standard fasteners and bearings come from `bd_warehouse` (skill `cad-build123d`); vendor parts stay the maker's STEP (§3).
- Sheet-metal flat patterns via FreeCAD SheetMetal with the unfolded length checked (skill `shop-drawings`).
- Heavy work: `job run "<cmd>"` (`--needs freecad|gpu|fw|shop|heavy`, `--expect long`): the platform decides where it runs, locally it runs here.

## 5. Knowledge filing: the widest true layer
Check from the widest layer down; write it once in the widest layer where it is true, the others only link.

| What was said | Where it goes |
|---|---|
| A principle true for any machine and any client, or a framework feature asked for | a note in `wiki/log.md` + the mode's way to propose it (the framework files are read-only in a project) |
| A principle for this whole project | `wiki/client/rules.md` (≤ 40 lines) |
| A decision about a node | the model → the node's topic page (CURRENT + Decisions) → one register row in `wiki/client/decisions.md` |
| A fact or a number | the model params; the page names the field |
| A question or test sent | `wiki/client/open_questions.md` |
| Our mistake he pointed out | the rule in the widest true layer AND a fix of the generator or gate |
| Mood, frustration (“still waiting”, “again”) | `wiki/client/feedback.md` (date, quote, cause); every entry ends in a rule, a gate or a fix |

Never keep a rule only in your own memory: the files are the memory. Skill `project-wiki` has the page format.

## 6. Size limits (`check.py --kb` enforces them)
- Rewrite the CURRENT section of a page; never append a new state below the old one. The old CURRENT becomes one line in History.
- Page ≤ 12 KB and ≤ 150 lines (split into sub-pages); table row ≤ 300 chars; decision register row ≤ 200 chars.
- `wiki/client/rules.md` ≤ 6 KB / 40 lines.
- Project `CLAUDE.md` ≤ 8 KB, its current state ≤ 5 lines; `wiki/handover.md` ≤ 8 KB, overwritten, never appended.
- Do not edit `CLAUDE.md` in ordinary turns: every edit re-sends the whole file into the session's context (one project
  made 47 edits in one day, ~75K tokens). Per-turn state goes to `tasks.json` / `wiki/handover.md`; «Current state» is
  rewritten at handover and in nightly maintenance only.
- Raw material (chat exports, `*.txt`, logs) lives in `raw/`, never inside `wiki/`. `wiki/index.md` is generated: do not edit it.

## 7. Skill map: task → skill (the trigger words are examples, in whatever language the person uses)
- Bought part, “the real one”, “bought”, “download the 3D”, “from the site”, motor / cylinder / bearing / MGN / NEMA → `part-library`
- Drawing, “drawing”, “PDF”, “laser”, “bending”, “dxf”, “for the workshop” → `shop-drawings`
- Where a file goes, new project, model-first, AAS, end-of-turn `check.py` → `machine-project`
- Wiki page, decision, rule, handover, nightly maintenance, migration → `project-wiki`
- Design change, roast, critic, test plan, sim plan, deploy, sub-agent brief → `engineer-loop`
- Wording for the client, questions, reactions → `client-talk`
- build123d modelling → `cad-build123d`; a STEP the client sends (a machine or a node) → `step-migration` (ported onto code, scripts in `step-import`)
- Web model page, download buttons, `kit.json` → `web-viewer` + `machine-project`; 3D print → `fdm-print`
- Physics, strength, FEA, “will it hold?”, “deflection”, collisions over the cycle → `sim`
- Electronics → `electronics-review`, `circuit-sim`, `datasheets`, `emc-debug`; firmware → `firmware-sim`, `virtual-controllers`
- Video from the client → `video-transcribe`

## 8. Code changes (app, firmware, scripts)
- Code changes go through the coding process (`10c-dev:coding` where that plugin is installed), and the checks follow the size of the diff (its §1): a value change gets
  no test-design agent, no review and no cleanup; a Small diff gets your own checklist and the tests of the touched
  files; only a Large one (new logic or modules, a changed format, a port of a machine) gets the test-design agent,
  the independent `10c-dev:code-review-agent` and `10c-dev:cleanup`. Interview questions go out through your lane's
  normal channel, in `client-talk` wording.
- Here a value change is a client's tuning: a speed, a move time, a travel, a delay, a size, a current, a threshold in
  existing code or `params.py`. Its whole cycle: the edit → build and run it on the virtual board (firmware) or let
  the model rebuild → hand it over / flash it.
- Giving one drive or part its own value of a shared constant (a per-drive table in place of one number) is a Small
  diff, not a Large one: a press rod's top speed (a move time 0.4 → 0.12 s over params.py, gen_sequence.py,
  sequence.h, main.cpp) had its firmware built in 2 min, then 13 min went to an independent review and to three
  rounds of rewriting `check_log.py`.
- Never rework a check script (`check_log.py`, a vsim checker) in the middle of a client's urgent change: a check
  that expects the old value gets that one value changed. A check that is wrong in itself is a note on the issue
  for later, not part of this task.
- Prove it on the real artifact (the running app, the simulated firmware, the script's output), not on a status message.
