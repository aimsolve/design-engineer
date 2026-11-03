---
name: emc-debug
description: Noise / interference / drift troubleshooting on a client's electronics ("readings jump when the motors are on", "interference", "the weight drifts") — measure first, then a hypothesis tree (source → coupling path → victim), every test with its failure criterion stated up front, one change at a time, cheapest fix first. Ships the ADS1220 + stepper drivers playbook (the 7-state log matrix, what to ask the client for) and a log-matrix CSV template + analysis script (noise RMS, p-p, drift per state, spectra, grams). Use on any complaint about noise, drift, jumps or EMI, before proposing any fix.
---

# EMC / noise debugging

## Rules

1. **Measure first.** The first reply to a noise complaint is a measurement request, not a fix list. A fix that
   "helps" without a before/after log on the same states is an anecdote.
2. **Cheapest fix first**: firmware setting → driver setting → wiring/cable → filter parts → ground topology →
   board respin. Don't ask for a new board while a register can still be wrong.
3. **Every test states its failure criterion before it runs** (`engineer-loop`): "if RMS in state C is within 1.5×
   state A, the supply is NOT the path". A test whose every outcome confirms the favourite theory proves nothing.
4. **One change at a time**, re-log the same states, same cable, same load. Two changes at once = no information.
5. Compare against the **datasheet noise floor** (noise table at the same data rate and gain, cited via
   `datasheets`). Within ~2× of it = nothing to fix on the board; the fix is data rate / averaging.
6. **Drift ≠ jumps.** Slow exponential over minutes = thermal (or reference). Steps = supply/reference/ground state
   change. Random fat noise = coupling/aliasing. Spikes = bad reads (firmware) or bursts.

## Hypothesis tree (write it into the project wiki, strike branches as tests kill them)

Source (driver chopper 20-60 kHz, step edges, switching regulator, MCU/radio, mains) →
path: **conducted supply** · **common ground impedance** · **capacitive** (cable next to motor wires) ·
**inductive** (loops) · **aliasing** (near multiples of the ΔΣ modulator rate) · **ground loop via logger/PC** ·
**mechanical vibration** · **thermal** → victim (reference, AVDD, inputs, cable, sensor).
Each path has one test that isolates it — the matrix below is built that way.

## Scenarios

| Case | Playbook |
|---|---|
| ADS1220 (or any ΔΣ bridge ADC) + load cell, readings change when stepper drivers are powered, even idle | [playbooks/ads1220_steppers.md](playbooks/ads1220_steppers.md) — **first scenario**; ends with a real doser case (what was found, in order, and what to build in when designing a weigher) |

New scenario → new playbook file with the same shape: facts with citations, ranked causes with tell-tales,
the measurement matrix, what to ask, fixes cheapest first.

## Logs → numbers

```bash
S=${DE_ROOT:-/opt/design-engineer}/skills/emc-debug
cp $S/templates/ads1220_log_matrix.csv logs/     # the client (or his firmware) fills it: t_s,state,code[,temp_c]
python $S/scripts/adc_log_stats.py logs/matrix.csv --vref 5 --gain 128 --mvv 2 --capacity-g 1000 \
       --ds-noise-uv 0.09 -o logs/out           # on the platform: via `job run`
python $S/scripts/adc_log_stats.py --demo -o /tmp/demo   # synthetic matrix to see the output shape
```

Out: `stats.csv` + `stats.md` (per state: n, mean, RMS noise after detrend, p-p, drift µV/min, in µV / codes /
grams; ratio to state A and to the datasheet floor; the contrasts the matrix is designed for) and `matrix.png`
(time series + spectrum per state) — look at the PNG before quoting anything. Mark `--ds-noise-uv` with its citation.

## Asking the client

Client-facing request in the client's language: [templates/i18n/measure_request.ru.md](templates/i18n/measure_request.ru.md),
[templates/i18n/measure_request.en.md](templates/i18n/measure_request.en.md) — photos, supply scheme,
driver model and standstill current settings, the log matrix, scope captures if he has a scope. Send it in pieces:
one ask per message, photos first (they are cheap and often settle the wiring question).
