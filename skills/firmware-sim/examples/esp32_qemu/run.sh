#!/usr/bin/env bash
# Build with PlatformIO, merge a 4 MB flash image, boot it in Espressif QEMU, UART -> uart.log.
# Stops as soon as DONE appears in the log, or after $SECS s wall time.
# Run in the fw image: job run --needs fw "cd fw/esp32_qemu && ./run.sh"
set -euo pipefail
cd "$(dirname "$0")"
SECS=${SECS:-40}
ENV=esp32dev
pio run -e $ENV
B=.pio/build/$ENV
P=${PLATFORMIO_CORE_DIR:-$HOME/.platformio}/packages
BOOT_APP0=$(find "$P"/framework-arduinoespressif32/tools/partitions -name boot_app0.bin | head -1)
python "$P"/tool-esptoolpy/esptool.py --chip esp32 merge_bin --fill-flash-size 4MB -o $B/flash.bin \
  0x1000 $B/bootloader.bin 0x8000 $B/partitions.bin 0xe000 "$BOOT_APP0" 0x10000 $B/firmware.bin
rm -f uart.log
timeout "$SECS" qemu-system-xtensa -machine esp32 -m 4M -display none -monitor none -serial file:uart.log \
  -drive file=$B/flash.bin,if=mtd,format=raw &
QPID=$!
for _ in $(seq $((SECS * 2))); do grep -q DONE uart.log 2>/dev/null && break; sleep 0.5; done
kill $QPID 2>/dev/null || true; wait $QPID 2>/dev/null || true
echo "--- uart.log (tail) ---"; tail -n 25 uart.log
grep -q "DONE" uart.log && echo "PASS: firmware reached DONE" || { echo "FAIL: no DONE in uart.log"; exit 1; }
