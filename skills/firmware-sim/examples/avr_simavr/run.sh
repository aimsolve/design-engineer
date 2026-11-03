#!/usr/bin/env bash
# Build with PlatformIO, run in simavr (ATmega328P @ 16 MHz) for $SECS s wall time; simavr prints UART0 to stdout.
# Run in the fw image: job run --needs fw "cd fw/avr_simavr && ./run.sh"
set -euo pipefail
cd "$(dirname "$0")"
SECS=${SECS:-10}
pio run -e uno
timeout "$SECS" simavr -m atmega328p -f 16000000 .pio/build/uno/firmware.elf > uart.log 2>&1 || true
echo "--- uart.log (tail) ---"; tail -n 25 uart.log
grep -q "DONE" uart.log && echo "PASS: firmware reached DONE" || { echo "FAIL: no DONE in uart.log"; exit 1; }
