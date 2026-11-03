#!/usr/bin/env bash
# Build with PlatformIO, run 2 s of virtual time in Renode (stm32f4_discovery + fake ADC), USART2 -> uart.log.
# Run in the fw image: job run --needs fw "cd fw/stm32_renode && ./run.sh"
set -euo pipefail
cd "$(dirname "$0")"
# macOS has no timeout: the same in perl, like it: SIGTERM on expiry, SIGKILL 5 s later
TIMEOUT=(timeout -k 5); command -v timeout >/dev/null || TIMEOUT=(perl -e 'my $t = shift; my $p = fork; if (!$p) { exec @ARGV or die "cannot run $ARGV[0]: $!\n" }
  $SIG{ALRM} = $SIG{TERM} = sub { kill "TERM", $p; for (1 .. 50) { exit 124 if waitpid($p, 1) == $p; select undef, undef, undef, 0.1 } kill "KILL", $p };
  alarm $t; while (waitpid($p, 0) == -1) { last unless $!{EINTR} } exit($? & 127 ? 128 + ($? & 127) : $? >> 8)')
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
"${TIMEOUT[@]}" 120 renode --disable-gui --console --plain \
  -e "\$bin=@$D/.pio/build/$ENV/firmware.elf; \$uart=@$D/uart.log; \$repl=@$D/.fake_adc.repl; include @$D/run.resc" \
  > renode.log 2>&1 || true
echo "--- uart.log (tail) ---"; tail -n 25 uart.log || { tail -n 40 renode.log; exit 1; }
grep -q "DONE" uart.log && echo "PASS: firmware reached DONE" || { echo "FAIL: no DONE"; tail -n 40 renode.log; exit 1; }
