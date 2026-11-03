#!/usr/bin/env python3
"""Tests for fw_publish.py: turn a built PlatformIO project into the files the web viewer flashes from
(<site>/fw/index.json and <site>/fw/<card>/manifest.json + binaries). Written from the spec
(work_sessions/DesignEngineer/2026-10-20/flashing/CONTRACT.md, "Published firmware") before the script exists.

  python3 skills/firmware-sim/scripts/test_fw_publish.py -v

Plain unittest + system python3. No pio, no network: every project is a temp dir with a platformio.ini, a sketch and a FAKE
.pio/build/<env>/ already in place, and the script is run with --no-build (skip `pio run`), exactly as an agent runs it otherwise.

Script interface fixed by these tests (the only part the spec leaves open):
  fw_publish.py <firmware_dir> --card CARD --out <site>/fw [--env ENV] [--version TEXT] [--boot-app0 FILE] [--no-build]
    <firmware_dir>  the folder with platformio.ini (electronics/firmware/<card>/); ENV defaults to the first [env:*]
    --out           the published folder; <out>/<card>/ gets manifest.json + the binaries, <out>/index.json is created or MERGED
                    (an entry for the same card is replaced, other cards stay)
    --boot-app0     the Arduino-ESP32 boot_app0.bin (lives in the PlatformIO framework package, not in .pio/build)
  importable too: chip_for_board(board_id) -> chip name
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "fw_publish.py")

BOARDS = {
    "esp32dev": "esp32",
    "esp32-c3-devkitm-1": "esp32c3",
    "esp32-s3-devkitc-1": "esp32s3",
    "nanoatmega328": "atmega328p",
    "uno": "atmega328p",
    "megaatmega2560": "atmega2560",
    "pico": "rp2040",
    "rpipico2w": "rp2350",
}


def need_script():
    if not os.path.exists(SCRIPT):
        raise AssertionError(f"{SCRIPT} does not exist yet")


def load_module():
    need_script()
    spec = importlib.util.spec_from_file_location("fw_publish_under_test", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Proj:
    """A throwaway firmware folder with fake build outputs."""

    def __init__(self, test, board="esp32dev", env=None, ini_speed=None, sketch="void setup(){ Serial.begin(115200); }\nvoid loop(){}\n",
                 sketch_name="main.cpp", build_files=None):
        self.tmp = tempfile.mkdtemp(prefix="fwpub_")
        test.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.env = env or board
        self.dir = os.path.join(self.tmp, "electronics", "firmware", "master")
        self.out = os.path.join(self.tmp, "site", "fw")
        os.makedirs(os.path.join(self.dir, "src"))
        ini = f"[env:{self.env}]\nplatform = x\nboard = {board}\nframework = arduino\n"
        if ini_speed:
            ini += f"monitor_speed = {ini_speed}\n"
        self.write("platformio.ini", ini)
        self.write(os.path.join("src", sketch_name), sketch)
        kind = "esp" if board.startswith("esp32") else "uf2" if board in ("pico", "rpipico2w") else "avr"
        names = build_files or {"esp": ["bootloader.bin", "partitions.bin", "firmware.bin"], "uf2": ["firmware.uf2"],
                                "avr": ["firmware.hex"]}[kind]
        for n in names:
            self.write(os.path.join(".pio", "build", self.env, n), f"BIN:{n}")
        self.boot_app0 = os.path.join(self.tmp, "boot_app0.bin")
        with open(self.boot_app0, "w") as f:
            f.write("BIN:boot_app0.bin")

    def write(self, rel, text):
        p = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)

    def publish(self, card="master", version="v18 a1b2c3d", extra=()):
        need_script()
        return subprocess.run([sys.executable, SCRIPT, self.dir, "--card", card, "--out", self.out, "--version", version,
                               "--boot-app0", self.boot_app0, "--no-build", *extra], capture_output=True, text=True, timeout=60)

    def manifest(self, card="master"):
        with open(os.path.join(self.out, card, "manifest.json")) as f:
            return json.load(f)

    def index(self):
        with open(os.path.join(self.out, "index.json")) as f:
            return json.load(f)


class ChipFromBoard(unittest.TestCase):
    def test_platformio_board_ids_map_to_chips(self):
        m = load_module()
        for board, chip in BOARDS.items():
            with self.subTest(board=board):
                self.assertEqual(m.chip_for_board(board), chip)

    def test_chip_reaches_the_manifest(self):
        for board, chip in BOARDS.items():
            with self.subTest(board=board):
                p = Proj(self, board)
                r = p.publish()
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertEqual(p.manifest()["chip"], chip)

    def test_unknown_board_fails_with_a_message_naming_it(self):
        p = Proj(self, "esp32dev")
        p.write("platformio.ini", "[env:esp32dev]\nboard = weird-board-9000\nframework = arduino\n")
        r = p.publish()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("weird-board-9000", r.stdout + r.stderr)


class ManifestFiles(unittest.TestCase):
    def files(self, board):
        p = Proj(self, board)
        r = p.publish()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return p, p.manifest()

    def test_esp32_classic_bootloader_is_at_0x1000(self):
        p, m = self.files("esp32dev")
        self.assertEqual(m["kind"], "esp32")
        self.assertEqual(m["files"], [{"offset": "0x1000", "url": "bootloader.bin"}, {"offset": "0x8000", "url": "partitions.bin"},
                                      {"offset": "0xe000", "url": "boot_app0.bin"}, {"offset": "0x10000", "url": "firmware.bin"}])

    def test_esp32c3_and_s3_bootloader_is_at_0x0(self):
        for board in ("esp32-c3-devkitm-1", "esp32-s3-devkitc-1"):
            with self.subTest(board=board):
                p, m = self.files(board)
                self.assertEqual(m["kind"], "esp32")
                self.assertEqual(m["files"][0], {"offset": "0x0", "url": "bootloader.bin"})
                self.assertEqual([f["offset"] for f in m["files"][1:]], ["0x8000", "0xe000", "0x10000"])

    def test_binaries_are_copied_next_to_the_manifest(self):
        p, m = self.files("esp32dev")
        for f in m["files"]:
            path = os.path.join(p.out, "master", f["url"])
            self.assertTrue(os.path.isfile(path), path)
            with open(path) as fh:
                self.assertEqual(fh.read(), "BIN:" + f["url"])

    def test_avr_manifest_is_one_hex_file(self):
        for board in ("uno", "nanoatmega328", "megaatmega2560"):
            with self.subTest(board=board):
                p, m = self.files(board)
                self.assertEqual(m["kind"], "avr")
                self.assertEqual(m["files"], [{"url": "firmware.hex"}])
                self.assertTrue(os.path.isfile(os.path.join(p.out, "master", "firmware.hex")))

    def test_rp2_manifest_is_one_uf2_file(self):
        for board in ("pico", "rpipico2w"):
            with self.subTest(board=board):
                p, m = self.files(board)
                self.assertEqual(m["kind"], "uf2")
                self.assertEqual(m["files"], [{"url": "firmware.uf2"}])

    def test_manifest_carries_the_version(self):
        p, m = self.files("esp32dev")
        self.assertEqual(m["version"], "v18 a1b2c3d")

    def test_missing_build_output_fails_and_names_the_file(self):
        p = Proj(self, "esp32dev", build_files=["bootloader.bin", "firmware.bin"])   # no partitions.bin
        r = p.publish()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("partitions.bin", r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(p.out, "master", "manifest.json")), "no half-published card")


class MonitorSpeed(unittest.TestCase):
    def speed(self, **kw):
        p = Proj(self, **kw)
        r = p.publish()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return p.manifest()["monitor_speed"]

    def test_platformio_ini_speed_is_used_when_sketch_agrees(self):
        self.assertEqual(self.speed(board="esp32dev", ini_speed=115200, sketch="void setup(){Serial.begin(115200);}"), 115200)

    def test_serial_begin_wins_when_it_disagrees_with_platformio_ini(self):
        self.assertEqual(self.speed(board="uno", ini_speed=9600, sketch="void setup(){ Serial.begin(57600); }\nvoid loop(){}"), 57600)

    def test_serial_begin_in_an_ino_sketch_counts(self):
        self.assertEqual(self.speed(board="uno", ini_speed=9600, sketch="void setup(){ Serial.begin( 38400 ); }", sketch_name="blink.ino"), 38400)

    def test_ini_speed_is_used_when_the_sketch_has_no_serial_begin(self):
        self.assertEqual(self.speed(board="esp32dev", ini_speed=74880, sketch="void setup(){}\nvoid loop(){}"), 74880)

    def test_commented_out_serial_begin_is_ignored(self):
        self.assertEqual(self.speed(board="esp32dev", ini_speed=230400, sketch="void setup(){\n// Serial.begin(9600);\n}"), 230400)

    def test_default_is_115200_when_nothing_says_otherwise(self):
        self.assertEqual(self.speed(board="esp32dev", sketch="void setup(){}"), 115200)


class IndexJson(unittest.TestCase):
    def test_index_lists_the_card_with_chip_usb_manifest_version_built(self):
        p = Proj(self, "esp32-c3-devkitm-1")
        self.assertEqual(p.publish().returncode, 0)
        boards = p.index()["boards"]
        self.assertEqual(len(boards), 1)
        b = boards[0]
        self.assertEqual((b["card"], b["chip"], b["manifest"], b["version"]), ("master", "esp32c3", "master/manifest.json", "v18 a1b2c3d"))
        self.assertEqual(b["usb"], ["esp-usb"])
        self.assertRegex(b["built"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_usb_classes_follow_the_compatibility_table(self):
        want = {"esp32dev": ["usb-uart"], "esp32-s3-devkitc-1": ["esp-usb"], "uno": ["arduino", "usb-uart"],
                "megaatmega2560": ["arduino", "usb-uart"], "pico": ["rp2"], "rpipico2w": ["rp2"]}
        for board, usb in want.items():
            with self.subTest(board=board):
                p = Proj(self, board)
                self.assertEqual(p.publish().returncode, 0)
                self.assertEqual(sorted(p.index()["boards"][0]["usb"]), usb)

    def test_second_card_is_added_and_republishing_a_card_replaces_its_entry(self):
        a = Proj(self, "esp32dev")
        self.assertEqual(a.publish("master", "v1").returncode, 0)
        b = Proj(self, "uno")
        b.out = a.out
        self.assertEqual(b.publish("slave", "v7").returncode, 0)
        self.assertEqual(a.publish("master", "v2").returncode, 0)
        boards = {x["card"]: x for x in a.index()["boards"]}
        self.assertEqual(sorted(boards), ["master", "slave"])
        self.assertEqual(boards["master"]["version"], "v2")
        self.assertEqual(boards["slave"]["version"], "v7")
        self.assertEqual(len(a.index()["boards"]), 2, "no duplicate entry for master")

    def test_manifest_file_matches_index_pointer(self):
        p = Proj(self, "esp32dev")
        self.assertEqual(p.publish().returncode, 0)
        self.assertTrue(os.path.isfile(os.path.join(p.out, p.index()["boards"][0]["manifest"])))


if __name__ == "__main__":
    unittest.main()
