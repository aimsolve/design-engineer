# player_demo: the example device of virtual-controllers

A pocket player: ESP32-DevKitC, a 2.0" ST7789 240x320 IPS display, three tact buttons, an EC11 encoder with a push, in a printed case.
Everything about the virtual device comes from the model:

- `mechanics/cad/parts/player/*.py`: each part's `ports` and `sim` field (what it is in the simulation; the display's `glass`).
- `mechanics/cad/connections.py`: `WIRES` (the same wires the electric scheme draws) and `SIM` (keys and labels of each placement).
- `electronics/firmware/player/`: one PlatformIO project for the chip AND the page: LVGL 9.2 + ESP-IDF `esp_lcd` (ST7789 over SPI at
  40 MHz, double buffer, DMA-like flush), buttons polled with a 15 ms debounce, the encoder decoded in interrupts.

```
cd mechanics/cad && python <skills>/machine-project/scripts/cad_incremental.py --model assemble.py   # -> build/model/{parts,device}.json
(cd electronics/firmware/player && pio pkg install -e player && pio run -e player)                    # the chip (same source)
SKILLS=<skills> sh sim/build_fw.sh                                                                      # the page: build/fw/player.{mjs,wasm}
node <skills>/virtual-controllers/scripts/device_run.mjs --device build/model/device.json --fw build/fw --ms 3000 \
     --do "press btn_play 120 @1000; turn enc 2 @2000" --shot lcd screen.png                            # headless + a PNG of the screen
SKILLS=<skills> PYTHON=<python with build123d> sh sim/site.sh                                           # the model page in build/site
```

Serial: `BOOT`, `READY`, `BTN <name> DOWN` / `UP <ms>`, `RAW <name> <edges>` (contact bounce seen by the interrupt), `LONG <name>`
(held 0.8 s), `ENC <+n> <pos>`, `FPS <n>`. Keys on the page: A / Space / D, the arrows and Enter for the encoder.
