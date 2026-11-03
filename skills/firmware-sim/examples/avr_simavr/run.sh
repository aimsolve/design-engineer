#!/usr/bin/env bash
# Build with PlatformIO, run in simavr (ATmega328P @ 16 MHz) for $SECS s wall time; simavr prints UART0 to stdout.
# Run in the fw image: job run --needs fw "cd fw/avr_simavr && ./run.sh"
set -euo pipefail
cd "$(dirname "$0")"
# macOS has no timeout: the same in perl, like it: SIGTERM on expiry (simavr flushes its UART log only on a signal it catches), SIGKILL 5 s later
TIMEOUT=(timeout -k 5); command -v timeout >/dev/null || TIMEOUT=(perl -e 'my $t = shift; my $p = fork; if (!$p) { exec @ARGV or die "cannot run $ARGV[0]: $!\n" }
  $SIG{ALRM} = $SIG{TERM} = sub { kill "TERM", $p; for (1 .. 50) { exit 124 if waitpid($p, 1) == $p; select undef, undef, undef, 0.1 } kill "KILL", $p };
  alarm $t; while (waitpid($p, 0) == -1) { last unless $!{EINTR} } exit($? & 127 ? 128 + ($? & 127) : $? >> 8)')
SECS=${SECS:-10}
pio run -e uno
"${TIMEOUT[@]}" "$SECS" simavr -m atmega328p -f 16000000 .pio/build/uno/firmware.elf > uart.log 2>&1 || true
echo "--- uart.log (tail) ---"; tail -n 25 uart.log
grep -q "DONE" uart.log && echo "PASS: firmware reached DONE" || { echo "FAIL: no DONE in uart.log"; exit 1; }
