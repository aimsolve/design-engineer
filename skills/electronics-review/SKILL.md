---
name: electronics-review
description: Review a client's circuit and its firmware — schematic intake from PDF / KiCad / EasyEDA / Altium / photos (ask for a netlist export + PDF; PDF → embedded text with PyMuPDF first, vision only on high-DPI crops to cross-check), a net list you can trust, then the review checklist (power tree, grounding, motor vs analog supply separation, ratiometric bridge excitation/reference, input RC filters, decoupling, shielding, interfaces) and the firmware checklist for ADC drivers (ADS1220 registers, DRDY, digital filter, syncing reads with motor state). Output is REVIEW.md with numbered, cited findings. Use when a client sends a schematic, a board photo or ADC code, or asks "is my circuit right".
---

# Electronics review

The first electronics case: a weighing doser, TI ADS1220 on a load cell, readings drift/jump when the
stepper drivers are powered. A noise complaint goes to `emc-debug` first (measure before theory); this skill is how
to READ the design and the code. Numbers only from datasheets with a citation (`datasheets`).

## 1. Intake — ask for the export, not a photo

| Client uses | Ask for (one message, with a picture of where the menu is if needed) | Trust |
|---|---|---|
| KiCad | the project folder (`.kicad_sch` + `.kicad_pcb`) + PDF | exact (kiutils / kicad-sch-api read it; `kicad-cli` is not in the worker image yet) |
| EasyEDA Std/Pro | **File → Export → Netlist** + **Export → PDF** | exact (netlist) |
| Altium | **Design → Netlist for Project** (Protel/EDIF) + PDF | exact (netlist) |
| PDF only | ask for the netlist once; meanwhile section 2 | partial — confirm critical nets |
| Photo / hand drawing | vision + questions | low |

Also ask: firmware (ADC init + read code, the register values actually written), driver modules and their settings,
supply scheme (what feeds what), photos of the board both sides and of the whole wiring.

**Tag every finding with its evidence: `netlist` / `pdf-text` / `image` / `asked`.** Never state connectivity
from a full-page image: a VLM-only pipeline got 35 % netlist accuracy vs 96 % with text extraction (arXiv 2601.22114).

## 2. PDF schematic — text first, vision second

```bash
python ${DE_ROOT:-/opt/design-engineer}/skills/electronics-review/scripts/pdf_schematic_text.py sch.pdf -o review/sch \
    --crop U3,U5 --dpi 400        # on the platform: run it via `job run "…"` (pymupdf lives in the worker)
```

Writes `words.json` (every word + bbox), `labels.md` (refdes list, net-label-ish tokens with pages and the nearest
refdes, same label = same net), `pages/p<N>.png`, `tiles/` (~1400 px, 15 % overlap), `crops/<refdes>.png`.
A page with no text is raster → vision only, every finding `image`.

Then per crop ask vision with a fixed format (`pin → net`) and **cross-check against `labels.md`**. Anything that
disagrees, or needs following a wire across the sheet, becomes a question to the client — with the crop attached.

## 3. Review procedure (in this order)

1. **Power tree**: every rail, its source, what else hangs on it, its decoupling. Motor supply and analog supply
   drawn separately? Where do they share a regulator, a wire or a connector pin?
2. **Grounds**: where analog, digital and motor returns join; which currents flow through the ADC/bridge ground.
3. **Each IC against its datasheet application section** (pinout, required caps, reference, unused pins).
4. **Sensor and analog path**: [checklists/precision_adc.md](checklists/precision_adc.md).
5. **Interfaces**: SPI/UART levels, pull-ups, cable lengths, what is on the same connector as the sensor.
6. **Firmware vs hardware**: [checklists/firmware_adc.md](checklists/firmware_adc.md) — the register values must
   match the wiring (reference source, input pins, gain vs signal range).

## 4. Output — `review/REVIEW.md` in the project

Numbered findings, most harmful first:

```
F3  HIGH  Bridge excited from +5V, ADC uses internal 2.048 V reference → not ratiometric.
    Evidence: pdf-text p2 (REFP0/REFN0 not connected; EXC+ on net +5V); firmware reg2=0x00 (VREF=00).
    Datasheet: [SBAS501D §9.1.3 p.49]. Change: REFP0→EXC+, REFN0→EXC−, reg2 VREF=01. Test: emc-debug state C.
    Ask client: «Is the ADC reference the internal one?» (in his language, with crop)
```

A schemdraw snippet (PNG) for each proposed change the client must wire. Roast the review (`engineer-loop`)
before it goes out. Ingest findings into the project wiki.

## To the client

Use his words, in his language: interference, ground, supply, reference voltage, pickup, shield. One finding + one
question per message, with the crop or the snippet. Not "your schematic is wrong" but "here is what I see, here is
what to check".
