# Playbook: ADS1220 + load cell, readings drift/jump when stepper drivers are powered (motors idle)

Source: `design-engineer/reviews/ELECTRONICS_RESEARCH.md` §3 (2026-09-26). SBAS501D pages are PDF pages, checked 2026-09-26 with ds_grep. Re-cite every number from the
fetched datasheet (`datasheets`) before it goes to a client; section numbers are SBAS501D.

## Facts that frame it

- ΔΣ ADC, modulator at **256 kHz** (normal mode). Interference near n·256 kHz is not attenuated by the digital
  filter and aliases into band. Datasheet recommends a first-order RC at 1-10× the data rate. [SBAS501D §9.1.2 p.48]
- Chopper frequency: TMC2209 StealthChop PWM 23.4 / **35.1 (default)** / 46.9 / 58.5 kHz; SpreadCycle ~16-30 kHz;
  DRV8825 ~30 kHz. Edges are ns → broadband; e.g. 7 × 35.1 ≈ 246 kHz sits near 256 kHz. [TMC2209 ds §6; DRV8825 ds]
- **"Idle" is not quiet**: an enabled driver keeps chopping to hold current (IHOLD). TMC2209 can reduce it
  (IHOLD, TPOWERDOWN, IHOLDDELAY) or freewheel. [TMC2209 ds §1.7, IHOLD_IRUN]
- Ratiometric only if excitation = reference (REFP/REFN on the excitation, VREF=01/10, or VREF=11 AVDD). Datasheet
  example `3Eh 04h 98h 00h`: 5 V, 3 mV/V, gain 128, 20 SPS. [§9.2.3 p.62]
- Noise floor at gain 128, inputs shorted: **0.09 µVrms @ 20 SPS**, 0.26 @ 175, 0.70 @ 1000 SPS. 50+60 Hz rejection
  only at 20 SPS. [Table 7-1 p.16, §6.5 p.7]
- Layout: solid ground plane; split AGND/DGND not necessary; C0G input caps. [§9.4.1 p.65]

## Causes, ranked for "changes when drivers powered, motors idle"

| # | Cause | Tell-tale | Cheapest fix |
|---|---|---|---|
| R1 | Not ratiometric (excitation from 5 V, internal 2.048 V ref) | **step** when drivers turn on, ∝ load on cell, follows rail voltage | wire REFP/REFN to excitation or VREF=AVDD — firmware + maybe one wire |
| R2 | Chopper ripple on AVDD/excitation (shared 5 V / buck) | σ grows B→C; ripple at chopper f on scope; internal short (E) also worse | own LDO for AVDD+excitation, bigger caps, star feed |
| R3 | Common ground impedance (motor return through ADC ground) | offset + noise ∝ hold current; worse with long thin ground | driver ground straight to PSU star, ADC+bridge one local ground |
| R4 | Coupling into the cell cable (capacitive/radiated, aliasing, RF rectification) | depends on cable routing; better with motors unplugged (D) | shielded twisted pair, shield at ADC end only, reroute; diff+CM RC (C0G); ferrite/CM choke on motor leads |
| R5 | Ground loop through logger (USB-PC, earthed scope/PSU, frame) | changes on battery laptop / USB removed (G) | log isolated / on battery; decide earth point |
| R6 | Thermal (hold current heats driver, motor, cell) | **slow** drift over minutes after power-on | reduce IHOLD/TPOWERDOWN or disable drivers when weighing; continuous tare |
| R7 | Mechanical vibration from holding/StealthChop | narrow peaks in the reading's spectrum; same with the cable isolated | disable/reduce hold, SpreadCycle vs StealthChop, damping |
| R8 | Firmware/config (rate too high, BCS on, no DRDY wait, wrong SPI mode, sign bug, UART traffic during conversion) | outliers, codes out of range, noise far from Table 7-1 | fix config/read path — check `electronics-review/checklists/firmware_adc.md` |
| R9 | Layout (no plane, driver traces under ADC) | everything else fixed, noise stays | next board revision |
| R10 | SPI link corrupted (MCU far from the ADC, long unshielded wires next to motor cables) | readback of config registers ≠ written; codes jump ×1.61 (= 3.3/2.048: ADC reset to the internal ref); channel swaps, DRDY timeouts; all far more often with drivers on | MCU next to the ADC (SPI ≤ 10 cm); firmware reads the config back every conversion and rewrites it |
| R11 | Frame floats (no PE, unearthed power strip): chopper dv/dt through winding–housing capacitance swings the frame the cells sit on | huge **step** (tens to hundreds of g) when drivers switch off; earthing the frame alone makes it WORSE while the weigher floats | frame, motor bodies, PSU FG → PE **and** weigher ground bonded to the frame at one point near the cells |
| R12 | Ground loop through another controller (Arduino on its own PSU, I2C + common GND, driver PUL−/DIR− on its ground) | step ∝ that controller connected; gone when the link is isolated | isolated link: opto UART (PC817) or I2C isolator (ADuM1250 / ISO154x); USB isolator for the logging laptop |
| R13 | Energized motor mounted ON the weighed part (flap/bucket motor on the hopper) | noise only on the cell carrying that motor; drops when its driver is de-energized | insulate the motor body from the weighed part (gasket + bushings), cable away from the cell cable |
| R14 | Vibration of feed motors through the frame (mechanical, not electrical) | noise only while running, decays seconds after stop; vanishes with the hopper lifted off the cell | coarse → fine (half speed) feed, settle 1–2 s, median; dampers; avoid resonant speeds |

## The 7-state log matrix (60 s raw codes each, same cable, no load on the cell)

| State | Drivers | ADC input | Isolates | Failure criterion (write before running) |
|---|---|---|---|---|
| A | unpowered | cell | baseline | — (reference for all ratios) |
| B | powered, EN disabled | cell | supply load/ripple without chopping | B/A RMS ≤ 1.5 and mean shift < 1 LSB-equivalent g → supply-on alone is not the path |
| C | powered + enabled (holding) | cell | chopping | C/B RMS ≤ 1.5 and no step → chopping is not the cause (look elsewhere) |
| D | as C, motors unplugged | cell | cable/motor coupling | D ≈ C → motor wires/cable coupling is not the path |
| E | internal short MUX=1110b, in A and in C | internal | board/supply vs outside | E@C ≈ E@A → board and AVDD are clean; the problem enters through the inputs |
| F | cell replaced at the connector by resistor divider/short, in A and C | dummy | cell + cable vs board | F@C ≈ F@A while C ≫ A → cell/cable path |
| G | as C, laptop on battery / USB isolated | cell | ground loop | G ≈ C → the logging ground loop is not it |

Slow drift over minutes in any state → R6 (log temperature too). A step at B or C that scales with load on the cell
(repeat C with a known weight) → R1.

## Ask the client (in this order; the client-facing text in `../templates/i18n/measure_request.<lang>.md`)

1. **Photos**: whole wiring (PSU, controller, driver modules, motor cables, load-cell cable route), ADC board both sides.
2. **Supply scheme**: what feeds excitation, what REFP/REFN connect to, what feeds AVDD, where grounds meet.
3. **Driver model and standstill current settings**: TMC mode, IHOLD/IRUN, TPOWERDOWN; A4988/DRV8825 Vref and EN
   usage; motor supply voltage.
4. **Firmware**: ADS1220 init + read code, register values read back at runtime.
5. **The log matrix** (template CSV) — if his firmware can't dump raw codes with a state tag, that is fix #0.
6. **Scope, if he has one**: AVDD and excitation at the ADC pins, AC-coupled, short ground spring, states A and C;
   voltage between ADC ground and driver ground; CSV export or FFT screenshot.
7. Temperature of driver, motor, cell after 10 min in C (finger or IR thermometer).
8. **Earthing and the weighed part**: is the frame on PE, is the socket earthed, how long is the MCU–ADC wire, which other
   controllers share a ground with the weigher, and is any motor mounted on the hopper/platform the cell carries (does it
   have to stay energized?). Each "yes" on the doser below was a week of tests.

## Fixes, cheapest first (one at a time, re-log A/C after each)

1. Firmware: ratiometric reference, 20 SPS (or lower) if the process allows, BCS=0, DRDY-driven reads.
2. Standstill: lower IHOLD / TPOWERDOWN, or disable drivers during the weighing window.
3. Separate LDO + filtering for AVDD and excitation (sim the filter: `circuit-sim` supply_ripple — a ferrite+cap LC can
   resonate at the chopper frequency).
4. Cable: shield, twist, routing; input RC (`circuit-sim` rc_antialias).
5. Ground topology.
6. Layout respin.

## Real case: a hopper doser (weigher next to steppers)

ESP32 + ADS1220 module + 2 load cells under hoppers, DM556 feed drivers, an Arduino Nano driving more motors, bucket
steppers mounted on the hoppers; weighing while the feed runs; dose 125–500 g, wanted ±2–5 g. About 40 logs over two
weeks. What was found, in the order found (σ = single read, ch1/ch2):

| # | Found | Proof (before → after) | Fix |
|---|---|---|---|
| 1 | R10, 80 cm SPI wires | drivers on: config lost / resets 8× more often than off → 0 faults in 772 reads | ESP32 moved next to the ADC; config readback + rewrite in firmware |
| 2 | Module ties AVDD = DVDD on the board | DVDD 3.3 V + AVDD 5 V read 0; continuity AVDD–DVDD | one supply for the module (measure continuity before any split: a split would short 5 V into 3V3) |
| 3 | R5, logging laptop on mains | zero step at drivers-off +126/+98 g (laptop on mains), −95/−50 (on battery), +4 g (power bank) | power bank, later USB isolator |
| 4 | R11, frame not earthed, unearthed power strip | frame → PE alone, weigher floating: **worse**, step −536/−304 g; + weigher AGND → frame near cells: +5/−14 g | frame, motor bodies, PSU FG → PE; one bond weigher ground → frame |
| 5 | R12, Nano on own PSU, I2C + common GND | step +142/+99 g → 0.0/+0.4 g | PC817 opto UART (600 baud, framed commands, 10 k + 10 nF on RX against EMI) + USB isolator |
| 6 | R6, warm-up | drift +19 g/min at start → +2.5 after 5 min, < 1 g per dose after 15 min | warm-up, tare every dose |
| 7 | Input RC (1 k + 100 nF) | σ 4.7/4.0 → 5.5/3.4 g: no gain (the noise was not differential pickup) | kept, not a fix |
| 8 | R13, bucket motor on hopper 1, must stay energized (flap does not hold without current) | RUN σ ch1 7.8–9.9 → 5.4 g with a silicone gasket under the motor (4.7 with its driver off) | insulate all bucket motors from the hoppers |
| 9 | R14, feed-motor vibration | both hoppers lifted off: σ 0.72–0.85 g in every state (electronics clean); one motor at a time: M1 5.7, M3 2.3, M2 1.05 g; all at half speed 4.1 → 1.85 g, median-of-5 0.75/0.95 g | coarse full speed → fine half speed → stop → 1–2 s → median 0.5 s; dampers on M1/M3 |

Discriminating tests that settled it (add them to the matrix when the cell sits under moving machinery): **hopper lifted
off the cell** (electrical vs mechanical), **one motor at a time**, **the suspect motor's driver de-energized**, **ABA**
(bond on / off / on) when one change looked like it reversed an earlier result, **cable swap** between channels.

Traps that cost days: two variables changed in one test (charger + Arduino → "worst ever", nothing learned); a log
labelled A that was C; a bond wire that had come loose during a test (the client said so a day later); a channel reading
negative full scale = open bridge contact, not mechanics; a wrong conclusion from a hopper that was not resting on its cell.

**If you are designing, not debugging** (a weigher next to steppers from the start), build in what the doser learned:
frame + motor bodies + PSU FG on PE; a weigh island (MCU + ADC + bridges) with one bond to the frame near the cells; every
link out of the island isolated (opto UART / I2C isolator, USB isolator for a laptop); MCU next to the ADC with config
readback in firmware; no energized motor on the weighed part without an insulating mount; coarse/fine feed, settle, median,
tare per dose.

Closest real-world analogue: Klipper load-cell probes (ADS1220 next to steppers on 3D printers) —
https://www.klipper3d.org/Load_Cell.html ("huge amounts of noise onto the 5V bus", bigger caps than the datasheet).
