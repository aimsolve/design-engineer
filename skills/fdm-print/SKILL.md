---
name: fdm-print
description: Printable parts and FDM test pieces the client prints himself — test pieces only as the exception (a failure criterion and a control first, tracked as a follow-up), build from the live CAD, files sent at once with a render + weight/time line as a complete kit, printer rules (inner allowance, walls, orientation, glue keys, fit as printed — no sanding), mesh checks into checks.json, and the mandatory "what this does NOT test" list. Use before designing or sending any printable piece (STL/3MF/STEP for printing).
---

# FDM test pieces for the client

A piece answers one physical question (does the flap stay up after a hand press? does the label slide?). One folder
per piece: `print/<piece>/` with `PIECE.md`, the build script, STL/STEP, previews, `checks.json`.

## Before designing: can it fail?

Write in `PIECE.md` first: the question, **what result means "no"**, and the control that can fail. Roasts caught
pieces that would pass whatever the answer was (a strip that covered the ledges, a finger holding the whole fold).
A test piece at the client is the exception: only what a catalog, our calculation or a cheap sim couldn't answer
(`engineer-loop` gate order). Roast the test plan. When it is sent, it is a follow-up with a due time that we chase.

## Ask the client once, record in the project

Printer (bed size, bed-slinger / CoreXY), material, nozzle, layer, how he mounts pieces, glue, time/weight budget.
Parts must fit **as printed**: no sanding, shimming or re-printing to fit (client labour = defect) — use the inner
allowance plus adjustable features (slots, clamps) where a fit matters; a calibration piece only if he offers. First
project, for reference: PETG, 0.4 / 0.2, 1.27 g/cm³, ~0.15 per side inner allowance (assumed, not measured), Ø4.5
holes for M4, 256 mm bed, glue keys drawn at zero clearance because he used thin glue (contact faces sanded to
P1000 on test pieces only).

## Flow — file and picture together, as a kit

1. **Build from the live CAD.** Import the working faces from the part modules / params; change only non-contact
   material (back-side thickening, shells, ears). Prove the working face is identical: sample the B-rep, report max
   deviation (target 0.000 mm).
2. **Preview**: renders of the piece as it sits in the machine (iso, front along travel, top) with a ghost product,
   plus a table: size, print bounding box, volume, weight (material density, 3 perimeters, 15 % gyroid), print time
   for a bed-slinger and a CoreXY. Say "no slicer was available" if the time is volume-based.
3. **Send at once:** the link + the front view + the table in one message. The file is never made or attached by you: the
   router keeps every print part's STL current, `tg link <part> --download` (a whole kit: `tg link --kit print --download`). Preview-and-wait only if he asked for
   it or the part is expensive (metal, milling).
4. **A complete kit by default** for a node or machine: the router's print kit (`tg link --kit print --download`) with every part, plus a README in the client's language (print /
   do not print / replaces vN, fasteners: what goes to what with which screw), an assembled and an exploded picture — not one part at a time.
   Withdraw the older zip when a revision supersedes it; log each rev and the client's quote in `PIECE.md`.

## Geometry rules

- Min wall 3 perimeters on non-working sides.
- **Inner allowance per side** on channels/pockets (printed inner contours come out small) — the measured value for
  this client. State both the drawn and the expected printed clearance, and what it means if his printer holds size.
- **Orientation**: working face never faces down (stand a long guide on its end, no supports, brim for tall narrow
  pieces). List every down-facing area > 45° with its size.
- Keep the machine's tapers and edge radii exactly.
- Mounting the way the client asked.
- **Fillet after subtracting the pocket, never the cutter.**
- Glue keys per the client's answers above; no sanding allowance on machine parts (fit as printed).

## Checks before sending (checks.json)

- Watertight, one body (`trimesh`: `body_count == 1`).
- Working face vs CAD: max deviation; no material 0.05 mm in front of it.
- Moving panels / flaps at their extreme angles vs added material: 0 hits, closest distance.
- Product envelope (smallest / nominal / biggest of the batch) along the whole piece: 0 hits, clearance per wall.
- Widths re-measured down the pocket on the mesh (must not grow the wrong way).
- Fits the bed (say which size).

## Say what it does NOT test

Every `PIECE.md` ends with that list (other flaps, glue, drive, sprung parts, steel-vs-PETG friction, layer steps).
The client reads the result through it.

## Budget

A whole-path rig grows fast (first project: 11 pieces, 1.7 kg, 16.5 h vs a target of ≤ 8 h / ≤ 500 g). Ask for
the time/weight budget up front and print the minimum that answers the question.
