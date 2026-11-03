#!/usr/bin/env bash
# Build with PlatformIO, merge a 4 MB flash image, boot it in Espressif QEMU, UART -> uart.log.
# Stops as soon as DONE (or a panic) appears in the log, or after $SECS s wall time.
# Run in the fw image: job run --needs fw "cd fw/esp32_qemu && ./run.sh"
set -euo pipefail
cd "$(dirname "$0")"
# macOS has no timeout: the same in perl, like it: SIGTERM on expiry, SIGKILL 5 s later
TIMEOUT=(timeout -k 5); command -v timeout >/dev/null || TIMEOUT=(perl -e 'my $t = shift; my $p = fork; if (!$p) { exec @ARGV or die "cannot run $ARGV[0]: $!\n" }
  $SIG{ALRM} = $SIG{TERM} = sub { kill "TERM", $p; for (1 .. 50) { exit 124 if waitpid($p, 1) == $p; select undef, undef, undef, 0.1 } kill "KILL", $p };
  alarm $t; while (waitpid($p, 0) == -1) { last unless $!{EINTR} } exit($? & 127 ? 128 + ($? & 127) : $? >> 8)')
SECS=${SECS:-40}
ENV=esp32dev
pio run -e $ENV
B=.pio/build/$ENV
P=${PLATFORMIO_CORE_DIR:-$HOME/.platformio}/packages
BOOT_APP0=$(find "$P"/framework-arduinoespressif32/tools/partitions -name boot_app0.bin | head -1)
if [ -x "$P"/tool-esptool/esptool ]; then   # the esptool 5 binary package (espressif32 >= 7 installs it)
  "$P"/tool-esptool/esptool --chip esp32 merge-bin --pad-to-size 4MB -o $B/flash.bin \
    0x1000 $B/bootloader.bin 0x8000 $B/partitions.bin 0xe000 "$BOOT_APP0" 0x10000 $B/firmware.bin
else                                         # espressif32 6: esptool.py on PlatformIO's own Python
  pio pkg exec -p tool-esptoolpy -- esptool.py --chip esp32 merge_bin --fill-flash-size 4MB -o $B/flash.bin \
    0x1000 $B/bootloader.bin 0x8000 $B/partitions.bin 0xe000 "$BOOT_APP0" 0x10000 $B/firmware.bin
fi
qemu-system-xtensa -machine help 2>/dev/null | grep "^esp32 " >/dev/null ||   # e.g. Homebrew's qemu first on PATH: no esp32 machine
  { echo "FAIL: $(command -v qemu-system-xtensa) is not Espressif's QEMU: run install.sh, or put ~/.local/bin first on PATH"; exit 1; }
rm -f uart.log
"${TIMEOUT[@]}" "$SECS" qemu-system-xtensa -machine esp32 -m 4M -display none -monitor none -serial file:uart.log \
  -drive file=$B/flash.bin,if=mtd,format=raw &
QPID=$!
for _ in $(seq $((SECS * 2))); do grep -qE "DONE|Guru Meditation" uart.log 2>/dev/null && break; sleep 0.5; done   # a panic: no wait
kill $QPID 2>/dev/null || true; wait $QPID 2>/dev/null || true
echo "--- uart.log (tail) ---"; tail -n 25 uart.log
grep -q "DONE" uart.log && echo "PASS: firmware reached DONE" || { echo "FAIL: no DONE in uart.log"; exit 1; }
