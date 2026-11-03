---
name: virtual-controllers
description: Run a machine's or a device's REAL Arduino/ESP32 firmware (unchanged .ino or PlatformIO src/main.cpp, with its PlatformIO libraries such as LVGL) as WebAssembly on a fake board, several boards at once in one shared virtual clock — pins wired between boards in JS, I2C routed by address, BLE/Serial/EEPROM/Servo/Timer stubs, a fake HX711, step/dir recorders; and a virtual DEVICE derived from the model (build/model/device.json): buttons with contact bounce, encoders, SPI/I2C displays (ST7789/ST7735/ILI9341/SSD1306, Arduino SPI or ESP-IDF esp_lcd) drawn on the model page. Node-first (headless scenarios, PNG of a screen), the same code in the viewer. Use when a client's machine has several controllers and you want to see the whole thing behave (command sequences, handshakes, stepper motion) before or without hardware; NOT for analog/EMC (emc-debug) or register-level/cycle-exact questions (firmware-sim, simavr/QEMU).
---

# Virtual controllers

Board-generic layer. A machine's own folder (e.g. `<machine>/sim/`) supplies: which sketches, the nets between boards, the world (sensors/physics), what to record.

## Pieces

| Path | What |
|---|---|
| `shim/` | Arduino API for wasm: `vc_core.{h,cpp}` (millis/micros/delay, GPIO, analogRead, attachInterrupt, Serial incl. printf, String, timed services), `Wire.h` (master+slave, `requestFrom` answered by a chip model), `SPI.h` (SPIClass, ESP32 default pins per bus), `idf_vc.cpp` + `idf/` (ESP-IDF stand-ins: gpio, `spi_bus_initialize`, `vTaskDelay`, `esp_lcd_new_panel_io_spi`), `EEPROM.h` (RAM, erased = 0xFF), `BLEDevice.h` (+`BLEServer/Utils/2902`), `ESP32Servo.h`, `GyverTimers.h` (Timer1/2 as a pin-toggle clock), `SoftwareSerial.h` (one-wire UART bus), `WiFi.h` (+`WiFiAP/WiFiClient.h`: softAP + page server), `FastLED.h` (strip colours as a hook), `vc_ext.h` (`Stream` base, UART mail type), `vc_lib.js` (the JS imports), `vc_main.cpp` |
| `lib/` | Vendored real libraries, unchanged, pinned, with licences: `GyverStepper` 2.7.3, `HX711` (RobTillaart 0.6.5 — the one that has `HX711_CHANNEL_A_GAIN_64`; bogde/HX711 does NOT compile this firmware), `GyverBus` 2.6.1 (GBUS over a UART; add `--libsrc GyverBus/GyverBus.cpp`) |
| `lib/esp_lcd/` | ESP-IDF v5.3.2 `esp_lcd` panel drivers, unchanged (Apache-2.0): `esp_lcd_new_panel_st7789` and the panel ops; the SPI panel IO is the shim's |
| `build/build.mjs` | `.ino` -> `sketch.cpp` (adds `#include <Arduino.h>` and the prototypes the Arduino IDE would generate), or a `.cpp` as is -> `em++` (a local emsdk when `em++` is on PATH, else docker `emscripten/emsdk:3.1.74`), ASYNCIFY -> `<name>.mjs` + `.wasm`. `--libdeps .pio/libdeps/<env>` compiles every PlatformIO library the firmware uses (C with `emcc`, objects cached in `<out>/build/obj/` by source + headers), `--include <dir>` adds e.g. the folder of `lv_conf.h` |
| `runtime/chips.mjs` | chip models of a device: `Button`, `Encoder`, `MipiDisplay` (st7789 / st7735 / ili9341), `Ssd1306` |
| `runtime/device.mjs` | `deviceMeta(device)` + `buildDevice({vc, loadFirmware, device})`: a device.json as a machine the `VM` runs (no machine.mjs) |
| `scripts/device_run.mjs` | headless run of a device: `--device build/model/device.json --fw build/fw --ms 3000 --do "press btn_play 120 @500; turn enc 2 @1500" --shot lcd out.png` |
| `examples/player_demo/` | the example device: ESP32 + ST7789 + 3 buttons + EC11, firmware LVGL 9.2 + esp_lcd (`sim/build_fw.sh`, `sim/site.sh`) |
| `runtime/sim.mjs` | `Sim` (scheduler, nets, I2C bus, timers) and `Board` (one wasm instance) |
| `runtime/devices.mjs` | `StepperRecorder` (`live()` = steps incl. a running timer clock), `PinLogger`, `FakeHx711`, `uartBus(sim, members?)` (one-wire SoftwareSerial bus) |
| `runtime/vm.mjs` | `VM`: a built machine paced to the wall clock (speed x0.25..max, pause, reset, per-frame CPU budget, serial/I2C/BLE logs) + `browserFirmwareLoader`. Browser-ready, also runs in node |
| `runtime/node.mjs` | node-only `nodeFirmwareLoader(dir)`. Everything else in `runtime/` is isomorphic (no Buffer, no node:*) |

## How it works (read before changing anything)

- **One wasm instance per board.** It has its own virtual clock (µs). ASYNCIFY suspends it inside `vc_js_yield`; `Sim.run()` always resumes the board that wakes earliest. Everything is synchronous JS (Asyncify's `wakeUp()` returns when the board yields again).
- **When a board yields:** `delay()` / `delayMicroseconds(>=quantum)` (sleep until the target time), or when it has burned a *quantum* of virtual CPU time (default 50 µs; grows up to `quantumMaxUs` while the board does nothing but read). Boards therefore stay within ~one quantum of each other. Pin events are stamped with the writer's exact local time regardless of quantum.
- **Cost model:** every shim call costs a few virtual µs (`VC_COST_*` in `vc_core.h`, AVR vs ESP32) and every `loop()` pass at least `VC_COST_LOOP` — otherwise an idle `loop()` never advances time. Serial models the UART (baud, TX ring buffer): a sketch that prints at 9600 baud in a tight loop is slowed exactly as on hardware.
- **Interrupts and callbacks** (`attachInterrupt`, `Wire.onReceive`, BLE `onWrite`) run at the board's next sync point, not preemptively; a sleeping board is woken early ("poked") by an edge on an IRQ pin, an I2C message or a BLE write. A BLE callback that calls `delay()` yields like the main task.
- **Nets:** `sim.net(name, [[board, pin], ...])`. Value = what the OUTPUT member last wrote; undriven inputs read the pinMode default (INPUT_PULLUP=1, INPUT=0). `analogRead` on a wired pin returns 0/1 x full scale; on other pins it asks `sim.analogProvider(board, pin, t)` (the world). `board.drive(pin, v)` forces an input (buttons, sensors).
- **I2C:** master `endTransmission()` -> `sim.i2cTx` -> slave mailbox by address (slave registers itself in `Wire.begin(addr)`); NACK (2) if nobody has the address.
- **BLE:** `board.bleConnect()`, `board.bleWrite(charUuid, "WEIGHT:500")`, TX notifications = `sim.on('ble-tx')` (`VM.onBle(fn)` = same, survives Reset); notifications while disconnected are dropped like on hardware. The stub reports what the firmware advertises: `board.ble = {name, service, chars: {uuid: props}}`, with `board.bleRx()` / `board.bleTx()` = first writable / notify characteristic (this is how the web-viewer app panel bridges an operator app without knowing UUIDs). No MTU, no connection-interval latency: the bridge adds that (web-viewer SKILL.md).
- **HX711:** the real library bit-bangs DT/SCK; `FakeHx711` answers on the pins (ready = DOUT low, 24 bits MSB first, 25-27 pulses = gain, SCK high >= 60 µs = power-down + 400 ms settle, free-running at `rateHz`). It verifies pin numbers and gain, and reproduces the 10 SPS pacing.
- **SoftwareSerial bus:** every written byte is a hook `uart.tx` (`a` = byte, `b` = TX pin, `t` = end of its stop bit; TX blocks 10 bit-times like the real bit-banged one). `uartBus(sim)` delivers it to all the other boards as UART mail (type 5) into a 64-byte RX ring. One `SoftwareSerial` per sketch; no collisions, no echo.
- **Wi-Fi page (ESP32 softAP + `WiFiServer`):** the HTTP link rides the same BLE bridge: the operator page writes the request text to characteristic `http-rx`, what the sketch prints comes back as notifications on `http-tx`, `client.stop()` sends `\x04`. The SSID is `board.ble.name`, so the web-viewer app panel shows the board's own page unchanged. A sketch with both `WiFi.h` and `BLEDevice.h` does not compile (`#error`): they share the bridge.
- **FastLED:** `show()` = hook `led:<pin>:<brightness>:RRGGBB,...` (colours as set, before brightness); the machine maps it to indicators. One strip per sketch.
- **Events** (`sim.on`): `pin`, `clock` (hardware-timer toggle), `serial` (per line), `i2c`, `ble-tx`, `hook` (servo, ...), `crash`, `stderr`.

## A device from the model

A device (a player, a controller with a screen and buttons) needs no machine.mjs: its parts say what they are and the wiring says where
they go, as in the electric scheme (skill machine-project «Virtual device»). The model build writes `build/model/device.json`;
`runtime/device.mjs` turns it into boards + chips; the viewer shows it (web-viewer «Device» panel); `scripts/device_run.mjs` runs it headless.

- **Button:** pressed = its pin forced to the rail its other contact is wired to (GND -> LOW, 3V3 -> HIGH); released = not driven
  (the firmware's pinMode default). Contact bounce on by default (3-7 edges within `bounce_ms`, default 3, deterministic per id and
  time; `bounce_ms: 0` = one edge). Long press = a long hold: the firmware measures it. The page never presses shorter than 60 ms.
- **Encoder:** quadrature A/B to C, `detent` transitions per click (4), clockwise = A leads, one transition every 1.5 ms; SW/SW2 = a button.
- **Display (st7789 / st7735 / ili9341):** MIPI-DCS over SPI. Bytes reach it only on the SCK/MOSI the firmware set up (`SPI.begin`,
  `spi_bus_initialize`) and while its CS is LOW (CS not wired = always); DC low = command. SWRESET, SLPIN/OUT, DISPON/OFF, CASET/RASET
  (partial windows, wrap inside), RAMWR/RAMWRC, MADCTL (MX/MY/MV, BGR), COLMOD 565 / 666, INVON/OFF; controller RAM with `x_off/y_off`
  for smaller panels; `ips: true` = an IPS panel (true colours with INVON, most ST7789 modules). RST low = reset; BLK driven LOW = dark.
  `fb` = what the glass shows; a dirty rectangle tells the page what to upload.
- **SSD1306:** I2C at `addr` (0x3C) on the Wire pins the firmware uses (defaults 21/22); control byte 0x00 / 0x40 / Co; horizontal,
  vertical and page addressing; on/off, invert, contrast, segment remap, COM scan; `requestFrom` returns its status byte.
- **Honest frame rate:** an SPI transfer costs bytes x 8 / clock of the board's time: 240x320 RGB565 at 40 MHz = 30.7 ms (<= 32 fps).
  `esp_lcd` colour transfers are asynchronous like DMA: `draw_bitmap` returns at once; when the bus time has passed the display gets
  the bytes the buffer holds THEN (refilling it before the callback tears, as on the chip) and `on_color_trans_done` runs (a timed
  service: like an interrupt, at the next sync point, waking a sleeping board on time); the bus is shared and `tx_param` waits for
  queued colours, as in the IDF, also from a BLE / Wire / HTTP handler.
- **No preemption:** an interrupt or a transfer-done callback runs only when the firmware makes a call (any shim call, delay, millis).
  A bare spin on a flag set by an interrupt (`while (flag);`) hangs the board (and freezes the page). LVGL waits for a flush exactly
  like that; a build with LVGL among its libdeps links `shim/vc_lvgl.c`, which gives every LVGL display without its own flush wait one
  that sleeps until the queued transfers are done: stock LVGL 9 firmware runs unchanged (LVGL 8 is not covered: give its driver a
  `wait_cb` that calls something). A spin of the firmware's own still hangs. A `delay()` inside a BLE / Wire / HTTP handler is fine.
- Memory: 16 MB per ESP32 board (LVGL), 8 MB per AVR board; ESP32 builds link the IDF stand-ins and esp_lcd, AVR builds do not.

## Build and run

```
node build/build.mjs --name master --board esp32 --sketch x.ino --out build/fw --libsrc HX711/HX711.cpp
node build/build.mjs --name slave  --board avr   --sketch y.ino --out build/fw
```

then in JS: `const sim = new Sim(); await sim.addBoard('master', (await import('build/fw/master.mjs')).default, 'esp32'); sim.net(...); sim.at(t_us, fn); sim.run(60e6);` — a full machine (three boards, 12 recorded steppers, fake load cells, stub world) has the same shape in its own `sim/{machine,run}.mjs`.

## In the browser (web-viewer «Machine» layer)

`runtime/*.mjs` (except `node.mjs`) and the `.mjs`+`.wasm` builds run unchanged in a page: emscripten is built with `ENVIRONMENT=web,node`. The machine's
`sim/machine.mjs` must be isomorphic too: no `node:*` imports, the runtime is injected (`buildMachine({vc, loadFirmware, firmware})`, `vc` = the runtime index) and it exports
`META` (`boards: [{id, name, mcu, fw, about, cad:[{part, instance?}]}]`, `target`) and returns `{sim, status, start(), stop(), setTarget(g), sample(), actuators(), pinTable(board)}`;
optional `i2cMeaning(byte)`. node (`sim/run.mjs`) loads the same file with `nodeFirmwareLoader`. `build/build.mjs` writes `<out>/index.json` (`name, mcu, source, built, wasm`) for the site's firmware selector.
Site wiring and page UI: `web-viewer/SKILL.md` -> «Machine» layer. Measured (M1 Pro, Chrome): x1 accurate to 0.1 %, max about x80-100 at 12 ms/frame with frames still 16.7 ms.

## Limits (say them in the report)

- Evidence about the CODE and its inter-board protocol/timing at ~quantum resolution — not about the electronics, not cycle-exact. AVR `int` is 32-bit here (16-bit on a real Nano); a sketch relying on 16-bit overflow behaves differently.
- Sensors and mechanics come from the machine's `world` (a stub until it is real physics); wiring is taken from the firmware, not verified against the schematic.
- One thread per board: no FreeRTOS tasks, no concurrent BLE task; callbacks run at sync points.
- Wi-Fi: softAP + page server only (no STA, no TCP timing, one request at a time).
- Not shimmed: SPI reads (MISO reads 0xFF), raw `spi_device_*` transactions, real Servo output, `analogWrite`/PWM (a PWM backlight is
  on/off), hardware timers other than the toggle clock, sleep modes, watchdog, FreeRTOS tasks, SD, I2S sound (planned).
- Display libraries that write ESP32 SPI registers directly (TFT_eSPI, LovyanGFX) do not run: use LVGL + esp_lcd, or Arduino SPI (Adafruit).
- Add a missing Arduino call to `shim/`, never edit the firmware.
