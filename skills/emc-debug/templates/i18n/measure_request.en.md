# Measurement request to the client (ADC noise from stepper drivers)

Send it in pieces, one item per message, starting with the photos. Fill in your own: the client's name and form of
address, the driver model. Each message is one request + why (one sentence).

---

**1. Photos**, to see where interference can get into the wires.
"Please send photos of all the wiring: power supply, controller board, drivers, motor cables and the load-cell cable,
so that it is visible where they run next to each other. And the ADC board from both sides."

**2. Supply scheme.**
"How is it powered: what feeds the load-cell bridge (excitation), what is the ADC reference (REFP/REFN) connected to,
and what feeds the ADC itself (AVDD)? Where do the grounds of the ADC, the controller and the drivers meet?
If there is a schematic, please send the schematic file, a PDF and a netlist export."

**3. Drivers.**
"Which driver model (TMC2209, A4988, DRV8825…)? What holding current is set while the motor stands still
(for TMC: IHOLD/IRUN and TPOWERDOWN; for A4988/DRV8825: Vref)? Is the driver enabled (EN) or disabled during
weighing? What is the motor supply voltage?"

**4. Firmware.**
"Please send the part of the code that configures and reads the ADS1220, and the values of its four registers read
back from the chip while it runs."

**5. A log of the readings (the main one).**
"I need a log of the raw ADC codes, 60 seconds in each state, no load on the sensor, cable untouched.
Format: a table of time, state, code (I will attach the template). States:
- A: drivers unpowered
- B: drivers powered but disabled (EN)
- C: drivers enabled, motors standing still (holding)
- D: as C, but the motors disconnected
- E: ADC inputs shorted internally (MUX=1110), once in A and once in C
- F: a resistor / jumper on the connector instead of the sensor, in A and in C
- G: as C, but the laptop on battery (no charger) or through a USB isolator
If all of it is hard, let us start with A, B, C."

**6. Oscilloscope (if there is one).**
"If you have an oscilloscope: the ADC supply (AVDD) and the bridge excitation right at the ADC pins, AC-coupled,
with a short ground spring on the probe instead of the long croc clip, in states A and C. And the voltage between the
ADC ground and the driver ground. A CSV export or a screen with an FFT is best."

**7. Temperature.**
"After 10 minutes in state C: are the driver, the motor and the sensor warm? (by finger or an IR thermometer)"
