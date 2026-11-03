#!/usr/bin/env bash
# Build with PlatformIO, run 2 s of virtual time in Renode (stm32f4_discovery + fake ADC), USART2 -> uart.log.
# Run in the fw image: job run --needs fw "cd fw/stm32_renode && ./run.sh"
set -euo pipefail
cd "$(dirname "$0")"
ENV=disco_f407vg
pio run -e $ENV
D=$(pwd)
rm -f uart.log
cat > .fake_adc.repl <<REPL
fake_adc: Python.PythonPeripheral @ sysbus 0x50000000
    size: 0x100
    initable: true
    filename: "$D/fake_adc.py"
REPL
timeout 120 renode --disable-gui --console --plain \
  -e "\$bin=@$D/.pio/build/$ENV/firmware.elf; \$uart=@$D/uart.log; \$repl=@$D/.fake_adc.repl; include @$D/run.resc" \
  > renode.log 2>&1 || true
echo "--- uart.log (tail) ---"; tail -n 25 uart.log || { tail -n 40 renode.log; exit 1; }
grep -q "DONE" uart.log && echo "PASS: firmware reached DONE" || { echo "FAIL: no DONE"; tail -n 40 renode.log; exit 1; }
