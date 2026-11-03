#!/usr/bin/env python3
"""Publish a PlatformIO firmware build for flashing from the web viewer (Web Serial, see skills/web-viewer: build.py --flash).

  fw_publish.py <firmware_dir> --card CARD --out <dir> [--env ENV] [--version TEXT] [--boot-app0 FILE] [--no-build]

  <firmware_dir>  electronics/firmware/<card>/ (has platformio.ini); ENV defaults to the first [env:*]
  --out           the published folder: <out>/<card>/{manifest.json, *.bin|hex|uf2} and <out>/index.json (created, or MERGED: the entry of this card is replaced)
                  Use <project>/build/fw_publish: build.py --flash copies it into every rebuilt site (the site dir itself is wiped on each build).
  --boot-app0     Arduino-ESP32 boot_app0.bin; default = the one in the PlatformIO framework-arduinoespressif32 package
  --no-build      skip `pio run -e ENV` (the build is already in .pio/build/ENV)

Contract: work_sessions/DesignEngineer/2026-10-20/flashing/CONTRACT.md ("Published firmware"). Nothing is written unless every file is found.
"""
import argparse
import configparser
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

# PlatformIO board id -> chip. Exact ids first, then prefixes; anything else is asked from `pio boards` (field "mcu").
EXACT = {
    "uno": "atmega328p", "nanoatmega328": "atmega328p", "nanoatmega328new": "atmega328p", "miniatmega328": "atmega328p",
    "diecimilaatmega328": "atmega328p", "pro16MHzatmega328": "atmega328p", "pro8MHzatmega328": "atmega328p",
    "megaatmega2560": "atmega2560", "leonardo": "atmega32u4", "micro": "atmega32u4",
    "pico": "rp2040", "rpipico": "rp2040", "rpipicow": "rp2040", "rpipico2": "rp2350", "rpipico2w": "rp2350",
}
ESP_PREFIXES = [("esp32-c3", "esp32c3"), ("esp32c3", "esp32c3"), ("esp32-s3", "esp32s3"), ("esp32s3", "esp32s3"), ("esp32-s2", "esp32s2"),
                ("esp32s2", "esp32s2"), ("esp32-c6", "esp32c6"), ("esp32c6", "esp32c6"), ("esp32-h2", "esp32h2"), ("esp32h2", "esp32h2"),
                ("esp32", "esp32"), ("nodemcu-32", "esp32"), ("lolin32", "esp32"), ("az-delivery-devkit", "esp32")]
KNOWN_MCU = {"esp32", "esp32c3", "esp32s2", "esp32s3", "esp32c6", "esp32h2", "atmega328p", "atmega2560", "atmega32u4", "rp2040", "rp2350"}
# USB classes a chip can show up as (CONTRACT.md, "USB class from VID:PID")
USB = {"esp-usb": ["esp32c3", "esp32s3", "esp32c6", "esp32h2"], "usb-uart": ["esp32", "esp32s2", "atmega328p", "atmega2560"],
       "arduino": ["atmega328p", "atmega2560", "atmega32u4"], "rp2": ["rp2040", "rp2350"]}


def fail(msg):
    sys.exit(f"fw_publish: {msg}")


def pio_cmd():
    for c in (shutil.which("pio"), shutil.which("platformio"), os.path.expanduser("~/.platformio/penv/bin/pio")):
        if c and os.path.exists(c):
            return c
    return None


def chip_for_board(board_id):
    b = board_id.strip()
    if b in EXACT:
        return EXACT[b]
    for pre, chip in ESP_PREFIXES:
        if b.startswith(pre):
            return chip
    pio = pio_cmd()
    if pio:
        try:
            r = subprocess.run([pio, "boards", "--json-output", b], capture_output=True, text=True, timeout=60)
            for item in json.loads(r.stdout or "[]"):
                if item.get("id") == b:
                    mcu = re.sub(r"[^a-z0-9]", "", str(item.get("mcu", "")).lower())
                    if mcu in KNOWN_MCU:
                        return mcu
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    fail(f"don't know the chip of PlatformIO board '{b}': add it to EXACT/ESP_PREFIXES in fw_publish.py")


def usb_classes(chip):
    return sorted(c for c, chips in USB.items() if chip in chips)


def read_ini(path):
    cp = configparser.ConfigParser(interpolation=None, strict=False, inline_comment_prefixes=(";",))
    cp.optionxform = str
    with open(path, encoding="utf-8") as f:
        cp.read_file(f)
    return cp


def pick_env(cp, want):
    envs = [s[4:] for s in cp.sections() if s.startswith("env:")]
    if not envs:
        fail("platformio.ini has no [env:*] section")
    if want and want not in envs:
        fail(f"no [env:{want}] in platformio.ini (have: {', '.join(envs)})")
    return want or envs[0]


def ini_get(cp, env, key):
    for sec in (f"env:{env}", "env"):
        if cp.has_option(sec, key):
            return cp.get(sec, key).strip()
    return None


def strip_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def monitor_speed(fw_dir, cp, env):
    """Serial.begin(N) in the sketch wins over platformio.ini monitor_speed (the sketch is what runs), then 115200."""
    for root, _, files in os.walk(os.path.join(fw_dir, "src")):
        for name in sorted(files):
            if name.endswith((".cpp", ".ino", ".c", ".h", ".hpp")):
                with open(os.path.join(root, name), encoding="utf-8", errors="replace") as f:
                    m = re.search(r"\bSerial\.begin\s*\(\s*(\d+)", strip_comments(f.read()))
                if m:
                    return int(m.group(1))
    v = ini_get(cp, env, "monitor_speed")
    return int(v) if v and v.isdigit() else 115200


def find_boot_app0(given):
    if given:
        if not os.path.isfile(given):
            fail(f"--boot-app0 file not found: {given}")
        return given
    core = os.environ.get("PLATFORMIO_CORE_DIR") or os.path.expanduser("~/.platformio")
    hits = glob.glob(os.path.join(core, "packages", "framework-arduinoespressif32*", "tools", "partitions", "boot_app0.bin"))
    if not hits:
        fail("boot_app0.bin not found in the PlatformIO framework-arduinoespressif32 package: build once with pio, or pass --boot-app0")
    return max(hits, key=os.path.getmtime)


def default_version(fw_dir):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    try:
        r = subprocess.run(["git", "-C", fw_dir, "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return f"{r.stdout.strip()} {stamp}"
    except (OSError, subprocess.SubprocessError):
        pass
    return stamp


def plan_files(chip, build_dir, boot_app0):
    """-> (kind, [(manifest entry, source path)]). Raises via fail() naming the first missing file."""
    if chip.startswith("esp32"):
        boot = "0x1000" if chip in ("esp32", "esp32s2") else "0x0"
        items = [({"offset": boot, "url": "bootloader.bin"}, os.path.join(build_dir, "bootloader.bin")),
                 ({"offset": "0x8000", "url": "partitions.bin"}, os.path.join(build_dir, "partitions.bin")),
                 ({"offset": "0xe000", "url": "boot_app0.bin"}, boot_app0 or find_boot_app0(None)),
                 ({"offset": "0x10000", "url": "firmware.bin"}, os.path.join(build_dir, "firmware.bin"))]
        kind = "esp32"
    elif chip.startswith("rp2"):
        items, kind = [({"url": "firmware.uf2"}, os.path.join(build_dir, "firmware.uf2"))], "uf2"
    else:
        items, kind = [({"url": "firmware.hex"}, os.path.join(build_dir, "firmware.hex"))], "avr"
    for _, src in items:
        if not os.path.isfile(src):
            fail(f"build output missing: {src} (run without --no-build, or check the env)")
    return kind, items


def merge_index(out, entry):
    path = os.path.join(out, "index.json")
    idx = {"boards": []}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            try:
                idx = json.load(f)
            except ValueError:
                fail(f"{path} is not valid JSON")
        if isinstance(idx, list):   # the simulator's firmware list lives in the same file in a built site; keep it under "sim"
            idx = {"sim": idx, "boards": []}
    idx["boards"] = [b for b in idx.get("boards", []) if b.get("card") != entry["card"]] + [entry]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.chmod(path, 0o644)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("firmware_dir")
    ap.add_argument("--card", required=True, help="board id of the viewer's board card = the electronics/firmware/<card> folder")
    ap.add_argument("--out", required=True)
    ap.add_argument("--env")
    ap.add_argument("--version")
    ap.add_argument("--boot-app0")
    ap.add_argument("--no-build", action="store_true")
    a = ap.parse_args(argv)

    fw_dir = os.path.abspath(a.firmware_dir)
    ini = os.path.join(fw_dir, "platformio.ini")
    if not os.path.isfile(ini):
        fail(f"no platformio.ini in {fw_dir}")
    cp = read_ini(ini)
    env = pick_env(cp, a.env)
    board = ini_get(cp, env, "board")
    if not board:
        fail(f"[env:{env}] has no 'board'")
    chip = chip_for_board(board)

    if not a.no_build:
        pio = pio_cmd()
        if not pio:
            fail("pio (PlatformIO) not found; install it or build yourself and pass --no-build")
        if subprocess.run([pio, "run", "-e", env], cwd=fw_dir).returncode != 0:
            fail(f"pio run -e {env} failed")

    build_dir = os.path.join(fw_dir, ".pio", "build", env)
    boot_app0 = find_boot_app0(a.boot_app0) if chip.startswith("esp32") else None
    kind, items = plan_files(chip, build_dir, boot_app0)
    version = a.version or default_version(fw_dir)
    manifest = {"kind": kind, "chip": chip}
    if kind != "uf2":
        manifest["monitor_speed"] = monitor_speed(fw_dir, cp, env)
    manifest["version"] = version
    manifest["files"] = [m for m, _ in items]

    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix=".fwpub_", dir=out)
    try:
        for m, src in items:
            shutil.copyfile(src, os.path.join(tmp, m["url"]))
        with open(os.path.join(tmp, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=1)
            f.write("\n")
        os.chmod(tmp, 0o755)   # mkdtemp makes 0700; nginx must be able to read it
        for name in os.listdir(tmp):
            os.chmod(os.path.join(tmp, name), 0o644)
        dst = os.path.join(out, a.card)
        shutil.rmtree(dst, ignore_errors=True)
        os.rename(tmp, dst)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    merge_index(out, {"card": a.card, "chip": chip, "usb": usb_classes(chip), "manifest": f"{a.card}/manifest.json",
                      "version": version, "built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    print(f"published {a.card}: {chip}, {kind}, {len(items)} file(s), monitor_speed={manifest.get('monitor_speed', '-')}, version '{version}' -> {out}")


if __name__ == "__main__":
    main()
