# Checklist: firmware of an ADC driver (generic, ADS1220 appendix)

Ask for the actual code AND a dump of the registers read back from the chip at runtime — the code often differs
from what is in the chip (write failed, wrong SPI mode, reset after brown-out).

## Generic
- [ ] SPI mode, clock speed and CS handling match the datasheet timing; CS held low for the whole transaction.
- [ ] Every config write is **read back and compared** (RREG); mismatch → log + retry, not silently continue.
- [ ] Data-ready: waits for DRDY (pin interrupt or polling) — never reads on a timer that can run ahead of the ADC.
- [ ] Sign extension of 24-bit two's complement to int32 done right (`if (v & 0x800000) v |= 0xFF000000`).
- [ ] Data rate vs noise: rate chosen from the datasheet noise table for the needed resolution, not "max speed".
- [ ] Digital filter / mains rejection setting matches the rate (see appendix: ADS1220 rejects 50+60 Hz only at 20 SPS).
- [ ] Reference / gain / input mux in the registers match the wiring (electronics-review step 6).
- [ ] What else happens during a conversion: stepper driver UART/SPI traffic, step pulses, PWM, Wi-Fi/BLE bursts,
      display refresh. Anything that toggles near the ADC while it converts is a suspect.
- [ ] ISR priorities: a long ISR (step generation) delays the DRDY handler → missed or doubled samples.
- [ ] Averaging, median, tare and calibration logic: which filter, how many samples, what happens to an outlier;
      tare done in the same motor state as the weighing.
- [ ] Raw codes can be logged (CSV over serial with a state tag) — needed for `emc-debug`. If not, add it first.

## Syncing reads with motor state
- [ ] Weighing window defined: drivers disabled (EN) or at reduced hold current, motion finished, settle time elapsed.
- [ ] After a config/mux change or START, discard conversions until settled (ΔΣ digital filter; ADS1220 single-cycle
      settling in single-shot mode — continuous mode after a step still gives one partial sample).
- [ ] Each sample tagged with the driver state (off / enabled-holding / stepping) so logs can be split by state.
- [ ] Driver standstill settings known (TMC IHOLD/IRUN, TPOWERDOWN, StealthChop vs SpreadCycle; A4988/DRV8825: Vref,
      EN usage) — idle but enabled drivers keep chopping.

## Appendix: ADS1220 (SBAS501D — confirm page numbers with ds_grep before citing)

Commands: RESET `06h`, START/SYNC `08h`, POWERDOWN `02h`, RDATA `10h`, RREG `20h | (reg<<2) | (n-1)`,
WREG `40h | (reg<<2) | (n-1)`. **SPI mode 1** (CPOL=0, CPHA=1). Data: 24-bit two's complement, MSB first.

| Reg | Bits | Check |
|---|---|---|
| 0 | MUX[7:4] GAIN[3:1] PGA_BYPASS[0] | MUX matches the bridge pins; `1110` = inputs shorted to mid-supply (internal short test for emc-debug); gain 128 typical for load cells; bypass only allowed at low gains |
| 1 | DR[7:5] MODE[4:3] CM[2] TS[1] BCS[0] | normal mode DR `000`=20 SPS … `110`=1000 SPS; CM=1 continuous; TS=0 (temp sensor off); **BCS=0** (burn-out sources off) |
| 2 | VREF[7:6] 50/60[5:4] PSW[3] IDAC[2:0] | VREF `00` internal 2.048 V (**not ratiometric**), `01` REFP0/REFN0, `10` AIN0/REFP1–AIN3/REFN1, `11` AVDD–AVSS; 50/60 `01` = both, valid only at 20 SPS normal mode; PSW per wiring; IDAC off for a bridge |
| 3 | I1MUX I2MUX DRDYM | IDAC routing off for a bridge; DRDYM if DOUT also signals ready |

Datasheet bridge example (§9.2.3 p.62): `3Eh 04h 98h 00h` = AIN1–AIN2, gain 128, 20 SPS continuous, reference on
REFP1/REFN1, 50+60 Hz rejection, low-side switch closed. Compare the client's values with this line by line.

LSB = 2·VREF / (gain · 2^24). Baseline noise at gain 128, inputs shorted: Table 7-1 p.16 (0.09 µVrms at 20 SPS per the
2026-09 research — re-cite before quoting). Internal reference drift is in §6.5 p.7.
