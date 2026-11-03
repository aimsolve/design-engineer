# Checklist: precision ADC + bridge sensor (load cell, strain gauge, RTD)

Every item: OK / finding / unknown (→ question). Cite the ADC datasheet section for each finding. ADS1220 references
are to SBAS501D (the revision fetched 2026-09); confirm the page with `datasheets/scripts/ds_grep.py` before quoting.

## Reference and excitation
- [ ] **Ratiometric**: the bridge excitation IS the ADC reference (REFP/REFN wired to EXC+/EXC−, or AVDD as reference
      with the bridge on AVDD). If the excitation comes from a rail and the ADC uses its internal reference, every
      ripple/droop on that rail is read as weight. [SBAS501D §9.1.3 p.49, §9.2.3 p.62]
- [ ] Reference filter: small, matched to the input filter — too much reference filtering breaks ratiometry at the
      frequencies you care about. [SBAS501D §9.2.3 p.62]
- [ ] Excitation switch (ADS1220 PSW low-side switch) used as intended if power saving matters.
- [ ] Remote sense on 6-wire cells: sense lines go to REFP/REFN, not left open.

## Input path
- [ ] Differential RC + common-mode RC at the ADC pins; differential C ≥ 10× the CM caps (CM mismatch → differential
      error); **C0G/NP0** caps. Corner 1-10× the data rate for ΔΣ anti-aliasing of modulator-rate noise. [SBAS501D §9.1.2 p.48]
- [ ] Series R sized for the input current / PGA; resistors matched.
- [ ] PGA common-mode and output range respected at the chosen gain (datasheet formula, §8.3.2 p.21 in SBAS501D) —
      compute it with the real excitation, don't eyeball.
- [ ] Burn-out current sources off during precision measurement (they add offset). [SBAS501D §8.3.10 p.31]
- [ ] Unused analog inputs handled per datasheet.

## Supply
- [ ] AVDD from its own LDO or at least an RC/LC filter off a rail that motors/drivers do not load.
      **A ferrite bead does little at 20-60 kHz chopper frequencies** (it is an RF part); an LC can resonate right at
      the chopper frequency — sim it (`circuit-sim` supply_ripple template).
- [ ] Decoupling at each supply pin (value and placement per datasheet), bulk cap near the entry.
- [ ] Digital supply (DVDD) and AVDD not daisy-chained through the MCU board if the MCU board also feeds drivers.

## Ground and layout
- [ ] One solid ground plane under the ADC; split AGND/DGND "not necessary" for ADS1220. [SBAS501D §9.4.1 p.65]
- [ ] Motor/driver return current does NOT flow through the ADC/bridge ground path (star point at the PSU).
- [ ] Switching regulators, drivers, motor traces away from the analog inputs and reference.
- [ ] Shielded twisted cable to the cell; shield to AGND at the ADC end only; routed away from motor wires.
- [ ] How the logger / PC / scope ground joins (USB = ground loop candidate).

## Signal budget (write the numbers)
- Full-scale signal = sensitivity (mV/V) × excitation; gain chosen so it fits ±VREF/gain with margin.
- LSB = 2·VREF / (gain · 2^24). Needed resolution in grams → µV → compare with the datasheet noise table at the
  chosen data rate and gain (SBAS501D Table 7-1). If the datasheet noise is already above the need, no wiring fix helps.
