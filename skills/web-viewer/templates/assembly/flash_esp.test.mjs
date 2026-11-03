// ESP32 flash speed, retry and reset (bug: ESP32-D0WD-V3 on CP2102 failed at a hardcoded 460800,
// "Invalid head of packet (0x78)", and auto-reset into the bootloader did not work).
// No browser and no serial port: FlashSession.flashEspAt is stubbed, the transport and hw sessions are fakes.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as FL from "./flash.mjs";
import * as HW from "./hw.mjs";
import { DICT, LANGS } from "./i18n.mjs";

const need = (mod, name, file) => { assert.ok(mod[name], `${file} must export ${name}`); return mod[name]; };

// ---- condition 2: espBauds(baud) is the list of speeds to try, in order
test("espBauds: no speed in the manifest (undefined, 0, null, '', garbage) -> only 115200", () => {
  const espBauds = need(FL, "espBauds", "flash.mjs");
  for (const b of [undefined, 0, null, "", "fast", NaN]) assert.deepEqual(espBauds(b), [115200], `espBauds(${String(b)})`);
});
test("espBauds: 115200 -> [115200] (no duplicate retry at the same speed)", () => {
  assert.deepEqual(need(FL, "espBauds", "flash.mjs")(115200), [115200]);
});
test("espBauds: 460800 -> try 460800, then fall back to 115200", () => {
  assert.deepEqual(need(FL, "espBauds", "flash.mjs")(460800), [460800, 115200]);
});

// ---- condition 3: FlashSession.flashEsp retries once at 115200 only after a transfer error at a faster speed
// stub(results): results[i] is what the i-th flashEspAt call does: an Error (thrown) or a value (returned).
function stubbed(results) {
  const FlashSession = need(FL, "FlashSession", "flash.mjs");
  const s = new FlashSession(null);
  const calls = [];
  s.flashEspAt = async (baud, files, blobs, onProgress, expectChip) => {
    calls.push({ baud, files, blobs, onProgress, expectChip });
    const r = results[calls.length - 1];
    if (r instanceof Error) throw r;
    return r;
  };
  return { s, calls };
}
const FILES = [{ offset: "0x1000", url: "bootloader.bin" }];
const BLOBS = ["blob"];
const onProgress = () => {};

test("flashEsp: no manifest speed -> one attempt at 115200, its result returned", async () => {
  const { s, calls } = stubbed(["ESP32-D0WD-V3"]);
  const r = await s.flashEsp(FILES, BLOBS, undefined, onProgress, "esp32");
  assert.equal(r, "ESP32-D0WD-V3");
  assert.deepEqual(calls.map((c) => c.baud), [115200]);
});
test("flashEsp: success at 460800 -> no retry, its result returned", async () => {
  const { s, calls } = stubbed(["ESP32-C3"]);
  assert.equal(await s.flashEsp(FILES, BLOBS, 460800, onProgress, "esp32c3"), "ESP32-C3");
  assert.deepEqual(calls.map((c) => c.baud), [460800]);
});
test("flashEsp: 'Invalid head of packet (0x78)' at 460800 -> retried once at 115200, the 115200 result returned", async () => {
  const { s, calls } = stubbed([new Error("Invalid head of packet (0x78)"), "ESP32-D0WD-V3"]);
  const r = await s.flashEsp(FILES, BLOBS, 460800, onProgress, "esp32");
  assert.equal(r, "ESP32-D0WD-V3");
  assert.deepEqual(calls.map((c) => c.baud), [460800, 115200]);
  // the retry flashes the same thing to the same chip
  assert.equal(calls[1].files, FILES); assert.equal(calls[1].blobs, BLOBS);
  assert.equal(calls[1].onProgress, onProgress); assert.equal(calls[1].expectChip, "esp32");
});
test("flashEsp: transfer error at 460800 and again at 115200 -> the 115200 error is thrown, no third attempt", async () => {
  const { s, calls } = stubbed([new Error("Invalid head of packet (0x78)"), new Error("Timeout waiting for packet header")]);
  await assert.rejects(s.flashEsp(FILES, BLOBS, 460800, onProgress, "esp32"), /Timeout waiting for packet header/);
  assert.deepEqual(calls.map((c) => c.baud), [460800, 115200]);
});
test("flashEsp: a transfer error at 115200 (manifest speed 115200) is thrown, not retried", async () => {
  const { s, calls } = stubbed([new Error("Invalid head of packet (0x78)"), "should not be reached"]);
  await assert.rejects(s.flashEsp(FILES, BLOBS, 115200, onProgress, "esp32"), /Invalid head of packet/);
  assert.deepEqual(calls.map((c) => c.baud), [115200]);
});
test("flashEsp: a transfer error with no manifest speed is thrown, not retried", async () => {
  const { s, calls } = stubbed([new Error("Invalid head of packet (0x78)"), "should not be reached"]);
  await assert.rejects(s.flashEsp(FILES, BLOBS, undefined, onProgress, "esp32"), /Invalid head of packet/);
  assert.deepEqual(calls.map((c) => c.baud), [115200]);
});
// a wrong-chip refusal as flash.mjs throws it (fatal, in the page language): never retried at another speed, whatever the language
for (const lang of LANGS) {
  const msg = DICT[lang].flash.wrongBoard.replace("{have}", "ESP32-D0WD-V3").replace("{want}", "esp32c3");
  test(`flashEsp: wrong-chip refusal at 460800 is thrown as is, never retried (${lang}: «${msg}»)`, async () => {
    const { s, calls } = stubbed([Object.assign(new Error(msg), { fatal: true, code: "wrongBoard" }), "should not be reached"]);
    await assert.rejects(s.flashEsp(FILES, BLOBS, 460800, onProgress, "esp32"), (e) => e.message === msg);
    assert.deepEqual(calls.map((c) => c.baud), [460800]);
  });
}
test("flashEsp: no sync / connect failure at 460800 ('Failed to connect with the device') is thrown, never retried", async () => {
  const { s, calls } = stubbed([new Error("Failed to connect with the device"), "should not be reached"]);
  await assert.rejects(s.flashEsp(FILES, BLOBS, 460800, onProgress, "esp32"), /Failed to connect/);
  assert.deepEqual(calls.map((c) => c.baud), [460800]);
});

// ---- condition 4: the reset into the loader stays esptool-js's own ClassicReset (a one-call DTR/RTS reset, #81, stopped
// a CP2102 ESP32 from entering the loader at all, even with BOOT+EN; ClassicReset entered it in 0.2 s on the same board)
test("flash.mjs does not override esptool-js's reset sequence", () => {
  const src = readFileSync(new URL("./flash.mjs", import.meta.url), "utf8");
  assert.doesNotMatch(src, /resetConstructors/);
});

// ---- condition 5: a connect / no-sync failure tells the user to hold BOOT, in the default language (ru)
test("friendlyError: 'Failed to connect with the device' -> advice mentioning BOOT, in the default language", () => {
  const prev = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  Object.defineProperty(globalThis, "navigator", { value: { serial: {} }, configurable: true });
  try {
    const t = FL.friendlyError(new Error("Failed to connect with the device"));
    assert.match(t, /BOOT/);
    assert.equal(t, DICT.ru.flash.noBoot);
  } finally {
    if (prev) Object.defineProperty(globalThis, "navigator", prev); else delete globalThis.navigator;
  }
});

// ---- conditions 1 and 6: hw core passes the manifest's flash_baud to session.flashBoard as `baud`
function hwRig(manifest) {
  const flashArgs = [];
  const subs = new Set();
  const session = {
    monitoring: false,
    subscribe(fn) { subs.add(fn); return () => subs.delete(fn); },
    async openMonitor() { this.monitoring = true; },
    async closeMonitor() { this.monitoring = false; },
    async resetBoard() {},
    async identify() { return { chip: "ESP32-D0WD-V3", description: "ESP32-D0WD-V3" }; },
    async flashBoard(o = {}) { flashArgs.push(o); this.monitoring = true; return { chip: "ESP32-D0WD-V3" }; },
  };
  const port = { connected: true, getInfo: () => ({ usbVendorId: 0x10c4, usbProductId: 0xea60 }) };   // CP2102
  const core = need(HW, "createHwCore", "hw.mjs")({
    serial: { getPorts: async () => [port] },
    boards: HW.parseIndex({ boards: [{ card: "master", chip: "esp32", usb: ["usb-uart"], manifest: "master/manifest.json", version: "v1" }] }),
    getManifest: async (name) => { if (name !== "master") throw new Error("no manifest " + name); return manifest; },
    sessionFor: () => session,
    onLog: () => {}, onChange: () => {}, tailMs: 1, reopenDelays: [0],
  });
  return { core, flashArgs };
}
const ESP_MANIFEST = { kind: "esp32", chip: "esp32", monitor_speed: 115200, version: "v1", files: [{ offset: "0x1000", url: "bootloader.bin" }] };

test("hw core: manifest flash_baud 460800 reaches session.flashBoard as baud 460800", async () => {
  const { core, flashArgs } = hwRig({ ...ESP_MANIFEST, flash_baud: 460800 });
  await core.refreshPorts();
  await core.flash("master", "user");
  assert.equal(flashArgs.length, 1);
  assert.equal(flashArgs[0].baud, 460800);
});
test("hw core: manifest flash_baud 115200 reaches session.flashBoard as baud 115200", async () => {
  const { core, flashArgs } = hwRig({ ...ESP_MANIFEST, flash_baud: 115200 });
  await core.refreshPorts();
  await core.flash("master", "user");
  assert.equal(flashArgs[0].baud, 115200);
});
test("hw core: no flash_baud in the manifest -> flashBoard gets no faster speed (baud is not > 115200)", async () => {
  const { core, flashArgs } = hwRig({ ...ESP_MANIFEST });
  await core.refreshPorts();
  await core.flash("master", "user");
  assert.equal(flashArgs.length, 1);
  assert.ok(!(Number(flashArgs[0].baud) > 115200), `baud ${flashArgs[0].baud} would flash faster than 115200`);
  assert.deepEqual(FL.espBauds(flashArgs[0].baud), [115200]);
});
