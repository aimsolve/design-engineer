---
name: shop-drawings
description: Shop drawings for sheet-metal parts (laser cut, laser + bending) to the client's drawing standard: sheet 1 = flat pattern with a bend label along each bend line (direction, bend angle, angle between the flanges, inner R) and «**» theoretical dims + views (overall, flange, thickness «t *», inner R) + isometric; sheet 2 = every hole and cut-out from corner 0,0 + hole table; weld seams on welded parts; a laser-clean DXF the shop's laser software reads (AutoCAD 2000, closed contours on layer 0, no text); ONE name for .dxf + .pdf + .step; every text in the project's language. A generator makes it all and a checker FAILs a set that breaks any of it. Use whenever a chat asks for a drawing, drawings, a PDF, bending, laser, dxf, a flat pattern, files for the shop or for cutting (in any language), or for files for the laser / bending shop, before sending ANY metal part to manufacture.
---

# Shop drawings for sheet metal

Where it comes from: a client's engineers had to teach this per project (thickness, bend radius, hole centres; then a sample
drawing to follow for every bent part; then a DXF their laser software could not read), and the next project drew it wrong
again. All of it is now in the generator: the agent does not lay out a drawing by hand and does not write its own exporter. Fix
the generator, never a project copy of it.

## The standard (every metal part, every time; the generator does it, the checker enforces it)

| On the set | Where |
|---|---|
| Flat pattern: bend lines dash-dot, a label along each line (UP / DOWN, bend N° (angle between flanges M°), R N), chain dims between bend lines + overall, marked «**» (theoretical) | sheet 1, top left |
| Views of the finished part: overall sizes; the profile along the bend lines with flange sizes, thickness «t *» dimensioned on the view and the inner R leader | sheet 1, right |
| Isometric with visible edges | sheet 1, bottom left |
| Notes: * reference, ** theoretical, general tolerances (GOST 30893.1 / ISO 2768-1), quantity, "hole positions on sheet 2", welds | sheet 1 |
| Every hole / cut-out / notch vertex from the lower-left corner 0,0 (= DXF 0,0), numbered holes, table X / Y / Ø, summary per Ø | sheet 2 |
| Weld seams (mark + leg) on both views + the note, for a part with `"weld"` | sheet 1 |
| Title block: the part's name, material + thickness, scale, sheet N of 2, quantity, drawing code | both sheets |
| Readable scale: A1 (A0 when it doubles the scale), small parts up to 5:1 | both sheets |
| DXF: AutoCAD 2000 (AC1015), mm, ASCII, every contour ONE closed LWPOLYLINE on layer 0, holes as CIRCLE, no text / bends / other layers | `<name>.dxf` |
| One name: `<name>.dxf` + `<name>.pdf` + `<name>.step` (finished 3D part) | kit folder `<t> mm <material>` |

Every text a person reads (sheets, folder words, `spisok.csv` header, README) is in the project's language: `.engineer/locale.json`
`lang` in the directory the scripts run from, or `--lang`; none = Russian. The texts live in `scripts/drawings_i18n.py`; add a
language there, never in the drawing code. No internal codes, decision numbers or review talk on the sheets.
The layout above is the default for every project: hole positions by coordinates from 0,0 with a table (GOST 2.307), the
AutoCAD 2000 DXF. A shop that wants its files named its own way is a project setting, the drawing profile: `.engineer/drawings.json`
`{"profile": "workshop"}` makes a hosting platform name the laser kit's files in Latin, `<code>_<t>mm<Oc|Nr|Al|St>_<qty>pc_<NameInLatin>`
(give a part a `"code"`, its drawing code, in the model). Without a profile every file keeps the part's name.

## How to produce it

1. Export each finished metal part as a STEP (folded state, sharp or R corners, home pose does not matter) or point at
   the build123d function that builds it.
2. Write `parts.json` next to the build outputs (a machine project gets this from `derive.py laser`, fields from the model's part dict):
   ```json
   [{"name": "Exit bracket", "step": "out/exit_bracket.step", "material": "AISI 304", "qty": 2, "code": "EX_01_00_001"},
    {"name": "Box cap", "py": "parts/box.py:build_cap", "material": "DC01 galvanised", "t": 1, "R": 1, "weld": true,
     "taps": [2.5], "dim_d": [4.5], "finish": "powder coat RAL 7024", "note": ["..."]}]
   ```
   `t` is auto-detected (most common opposite-face distance) — give it when the part has thicker bosses. `R` defaults
   to `t`, `K` to 0.33 steel / stainless, 0.40 aluminium. `taps` = hole diameters that are tap drills (the table says the thread).
   `weld` = true (corner seams found on the model are marked) or a text saying where and how to weld. `dim_d` = the only
   hole diameters that get positions on sheet 2 (screw holes); without it a Ø with more than 30 holes counts as a perforation:
   drawn and counted, positions in the DXF. `group` overrides the folder material word.
3. Heavy: on the platform run it as a worker job, never in the project container:
   `job run --expect long "bash ${DE_ROOT:-/opt/design-engineer}/skills/shop-drawings/scripts/drawings_env.sh shop_drawing.py --parts cad/parts.json --out build/kit --png"`
   Locally: `skills/shop-drawings/scripts/drawings_env.sh shop_drawing.py ...`. One part:
   `drawings_env.sh shop_drawing.py --step out/x.step --material S235JR --out build/kit`.
   `drawings_env.sh` keeps its own venv apart from the project environment.
4. Read the per-part lines: `ok / WARN / FAIL name folder bends holes blank sheet scale unfold...`. FAIL = the flat
   pattern does not refold into the model (volume, placement, hole count), the part is not constant-thickness sheet, or the
   laser DXF has an open contour: fix the model, do not ship. WARN = a sharp model (R leader at the corner) or a weld with no
   seam found — look at the `--png` pages.
5. Run the checker on the kit and send nothing until it passes:
   `drawings_env.sh check_drawings.py build/kit` -> `CHECK: n ok, 0 fail`.
5b. Every bent part also goes through the second, independent unfold (next section). Both must agree before the kit ships.
6. Never send the zip yourself: a machine project builds the laser kit from the model with this generator
   (`derive.py laser`, kit.json). On the platform the client gets it by link, `tg link --kit laser --download` (one part:
   `tg link <part> --download`), with one sentence in the chat: what is in it, how many parts, which thicknesses.

## What the generator does

- `shop_drawing.py`: loads the part, `unfold.py` makes the flat pattern (legs = slabs of thickness t, 90° bends, setback
  R + t, BA = π/2 (R + K t), refold check vs the model; a flat part whose leg test fails falls back to its largest face),
  then writes the three files and `spisok.csv` / `README.txt` / zip.
- `sheet_pdf.py`: the two sheets above. Views are drawn in the part's own frame (its biggest flat face in XY), so a STEP
  placed at an angle in the assembly still gives straight views. The profile looks along the bend lines (inner-R cylinders
  decide): under the main view, or right of it.
- `laser_dxf.py`: the laser DXF (the dialect CypCut-style software reads, pinned by `tests/test_laser_dxf_dialect.py`);
  `python laser_dxf.py IN.dxf OUT.dxf` converts any DXF's cut layers to it.
- `drawings_i18n.py`: every text on the sheets and in the kit, per language, and the words the checker recognises.

## Second unfold: FreeCAD SheetMetal (every bent part)

The generator's `unfold.py` is one opinion about the blank length; SheetMetal (FreeCAD 0.20 + SheetMetal 0.5.10 in the
worker) is a second one, any bend angle. Run it on EACH bent part, comparing with the kit DXF of the same part:
`job run "/usr/bin/python3 ${DE_ROOT:-/opt/design-engineer}/skills/shop-drawings/scripts/sm_unfold.py --step out/x.step --out build/sm --compare 'build/kit/3 mm steel/x.dxf'"`
(`--k 0.40` aluminium, `--R mm` for a sharp model whose real inner radius is not t, `--face N` to pick the base face.
Local test: FreeCAD lives only in the worker, `docker cp` the script + STEP into `engineer-worker-1` and run the same line there.)
It prints t and R, then one line per check: (a) flat area x t vs the folded volume, (b) blank = sum of straight legs +
sum angle x (R + K t), (c) blank size and round holes vs the kit DXF. Exit 0 = all ok; writes `x.flat.dxf` (OUTLINE /
HOLES / BEND, same 0,0 rule) + `x.flat.json`.
- Both ok: ship the kit as is. Any FAIL: do not ship; the hand formula (b) says which of the two is wrong.
- Bend that `unfold.py` cannot do (not 90°) or a generator FAIL, with SheetMetal ok: `x.flat.dxf` is the cut file —
  `python laser_dxf.py x.flat.dxf <kit>/x.dxf` (never copy it raw: its layers are not what the laser software reads); the PDF then lacks
  the flat-pattern sheet and bend tags, say so in the chat and in `note`.
- SheetMetal FAILs on a part the generator passes (notch cut through the bend, hole inside R + t of a bend): the
  generator stands only if the hand formula (b) done by hand agrees; say that the second check did not run.

## The checker (scripts/check_drawings.py)

FAILs a part when any of: the three files with one name; the DXF not laser-clean (`laser_dxf.check`: AC1015, ASCII, layer 0,
closed LWPOLYLINE + CIRCLE only); real text in the PDF; a material grade; thickness (and = the kit folder's `<t> mm`); for
bent parts the inner bend radius, the direction (up / down), a flat-pattern caption and the «**» flat dims on sheet 1; the
blank's overall size; with holes: a datum, the hole sheet (table X, mm / Y, mm after sheet 1) and the X and Y of every round
hole centre from the DXF's 0,0 (±0.06 mm; a Ø labelled as a perforation in the summary is exempt). It recognises those words in
every language of `drawings_i18n.RECOGNISE` and works on any set: a PDF from KOMPAS / SolidWorks passes if its text is real and
holds those values; text as curves fails as unverifiable. Its reasons come in the project's language (`--lang` overrides).

## Traps

- Model bends sharp is fine (unfold puts the R in); a model with real R must use the same R as the drawing.
- Mirrored parts are separate names (bent the other way) — give each its own entry.
- Holes cut after bending (across a bend zone, tapped after bending) are not in the flat cut: say so in `note`
  (a string = one note, a list = one note per item) and check the STEP.
- Off-axis holes (axis not along X/Y/Z) get no callout in the views; they are still in the flat pattern + table.
- SheetMetal sets K from the ANSI table only when `KFACTORSTANDARD = "ansi"` (unset = DIN, which halves K: a blank about
  0.26 t short per 90° bend); it also reads `FreeCADGui.Selection.getSelection()[0]`, headless that is a stub with the part in it (done).
- A sharp model has no cylinders for SheetMetal: the script fillets the bend edges (R inner, R + t outer) first, so R = t
  is an assumption, same as the generator's. The FreeCAD DXF can be mirrored against the kit's (the other side of the sheet).
- Milled / turned parts are not this skill yet: draw them with the same title-block and datum rules by hand.
- Bent parts with tabs or extra features cut through a leg fail the unfold ("not constant-thickness sheet"): unfold the sharp
  build123d model (`"py"`), not the finished STEP with rounded bends and add-ons.

## Assembly drawings per node (`scripts/assembly_drawing.py`)

The client's rule: an assembly drawing for every node, like flat-pack furniture instructions (exploded view, parts and fasteners
numbered, a parts table), never one for the whole machine. A machine project gets it from `derive.py assembly` (on the platform
the model page's assembly-drawings button builds it, also when kit.json does not declare the scope); never draw one by hand.
- One PDF per node (the `node` of each placement in build/model/parts.json). A fastener (`kind: "fasteners"`) goes to the node of
  the part it is docked to, so a screw placed by a folder node lands on the drawing of the part it holds.
- Exploded isometric: each part is pulled out along the axis of the joint it is docked at (from parts.json `joints` of the part it
  docks to), away from that part, by its own size (a fastener by 1.3 x its length); a chain opens like a fan. The view is the
  isometric corner that shows the gaps best. Dashed line = where the part goes in. Hidden-line removal over all parts at once.
- Table «No., Part, Code, Qty» (in the project's language); a balloon per row; fasteners last, merged by `code` and title, size
  from the code («M5×12 DIN 912», «×8»; a Russian project writes the thread prefix in Cyrillic, as its documents do). Give bought
  parts and fasteners a `code`; the title comes from `title`, else `name`.
- More than 28 rows: the joint tree is cut into steps. The biggest sub-assembly (3+ distinct parts) gets its own sheet
  «Step N. Assembly "…"» and is one row on the later sheets; identical ones are drawn once («assemble N pcs»).
  A sheet still too big (a node without dockings) spreads its balloons over several sheets.
- Mesh-only parts (an imported cabinet) have no STEP and are not drawn: such a node gets no drawing.
