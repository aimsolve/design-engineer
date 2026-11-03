---
name: circuit-sim
description: Small circuit sims that settle one electronics question — ngspice in batch (runner script → Bode / transient PNG + the numbers), templates for a bridge/ADC input RC anti-alias filter and for stepper-chopper ripple through a supply filter (ferrite+cap vs RC), spicelib for sweeps, schemdraw for pictures to the client. Same honesty rules as `sim`: simplest model that can kill the idea, inputs vs outputs, controls that can fail, trust verdicts. Use before proposing a filter / supply / grounding change to a client, or when someone quotes a corner frequency or attenuation.
---

# Circuit sims

Inherits **all of `sim`**: simplest model first, every omitted element listed, sensitivity over the uncertain
inputs, a control that can fail, README with ANSWER → numbers → caveats, trust verdict per claim, roast before it
goes out, "the calculation showed…" to the client (in his language).

## When a sim earns its place

- An input RC: corner, attenuation at the chopper frequency and at the ΔΣ modulator rate (ADS1220: 256 kHz),
  CM → differential conversion through cap mismatch.
- A supply filter between a motor bus and AVDD/excitation: ripple at the chopper frequency, **resonance peaking**
  of bead/inductor + ceramic cap, DC drop of an RC.
- IR drop / shared ground impedance: motor current × wire resistance vs the µV you are measuring.
- Not for: whether the client's cable picks up noise (that's a measurement, `emc-debug`), radiated EMI, layout.
  A two-line hand formula beats a sim when it answers — write the formula next to the result anyway as the control.

## Run it

```bash
S=${DE_ROOT:-/opt/design-engineer}/skills/circuit-sim      # on the platform: ngspice is in the worker → `job run "…"`
python $S/scripts/spice_run.py $S/templates/rc_antialias.cir --ac "dec 50 1 10meg" \
    --probe "v(outp,outn)" --ref "v(srcp,srcn)" --at 20,35.1k,256k -o sim/rc/diff
python $S/scripts/spice_run.py $S/templates/supply_ripple.cir --ac "dec 50 100 10meg" \
    --probe "v(avdd_lc),v(avdd_rc)" --ref "v(bus)" --at 35.1k -o sim/ripple/ac
python $S/scripts/spice_run.py $S/templates/supply_ripple.cir --tran "0.2u 4m" --skip 1.5m \
    --probe "v(avdd_lc),v(avdd_rc)" -o sim/ripple/tran --param Rrc=22
```

Out: `<o>.png` (Bode magnitude + phase, or transient), `.json` (−3 dB corner, peak, dB at each `--at`, or p-p /
RMS after `--skip`), `.csv`, `.log` (ngspice output — read it when a number looks odd). `--param k=v` overrides a
`.param`; sweep by looping it. **Look at the PNG before quoting.**

Template results with their placeholder values (worker, 2026-09-26): RC corner 643 Hz (hand formula: 643 Hz),
−35 dB at 35.1 kHz, −52 dB at 256 kHz; bead+10 µF **peaks +17.6 dB at 33 kHz** — 100 mV p-p bus ripple becomes
640 mV p-p on AVDD — while 10 Ω + 10 µF gives 4.5 mV p-p and a 30 mV drop. That peaking is the classic trap.

Control that can fail: the RC corner from the sim must match 1/(2π·R·C) by hand (differential, R = all series resistance per side incl. the source: 1/(2π·2R·(Cdiff +
Ccm/2))); LC resonance must sit at 1/(2π√(LC)). If they disagree, the netlist is wrong, not physics.

## Models and inputs

- Every value is an INPUT with a source: the client's schematic (cite sheet/refdes) or a datasheet (`datasheets`).
  Placeholders in the templates are marked — never quote a result built on a placeholder.
- A ferrite bead: use the impedance curve from its datasheet at the frequency of interest (at 20-60 kHz it is a
  small inductor, not a resistor). Capacitors: ESR, and the DC-bias derating of X7R/X5R ceramics (a "10 µF" 0805
  at 5 V may be 3-4 µF — sweep it).
- Vendor models (LDOs, op-amps) only from the manufacturer site, logged in the project's `datasheets/`. LTspice/PSpice
  syntax: put `set ngbehavior=ltpsa` in `.spiceinit` next to the netlist. Encrypted models don't run in ngspice.
- Batch / parameter sweeps in Python: `spicelib` (SimRunner + NGspiceSimulator, then `RawRead(raw, dialect="ngspice")` — without the dialect it fails) in the worker, or just loop
  the runner with `--param`. PySpice is not installed (broken with Debian's ngspice 39).

## Pictures for the client

`schemdraw` (worker) draws a clean fragment of the proposed change — a PNG with refdes and values, no solver words.
Send it with one sentence and the plot, in his language: "Filter R3/C5: the 35 kHz interference is attenuated about 50 times (calculated)."
