#!/bin/sh
# The player firmware for the model page: the same src/ as the chip, built to wasm (skills/virtual-controllers build.mjs) into build/fw.
# Needs emscripten (`install.sh --extras wasm`, an activated emsdk, or Docker) and the PlatformIO libraries once:
# (cd electronics/firmware/player && pio pkg install -e player).
# Never on a small shared server: run it on a workstation (on the platform: job run --needs fw).
set -e
S=${SKILLS:-${DE_ROOT:-/opt/design-engineer}/skills}
cd electronics/firmware/player
node "$S/virtual-controllers/build/build.mjs" --name player --board esp32 --sketch src/main.cpp \
  --libdeps .pio/libdeps/player --include include --out ../../../build/fw
