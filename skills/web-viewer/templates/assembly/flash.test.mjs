import test from "node:test";
import assert from "node:assert/strict";
import { parseHex, stkLoadAddress, stkProgPage, stkPages, friendlyError } from "./flash.mjs";
import { DICT } from "./i18n.mjs";

// 4 bytes at 0x0000, 2 bytes at 0x0006, EOF. Checksums computed per Intel HEX.
const HEX = ":0400000001020304F2\n:02000600AABB93\n:00000001FF\n";
test("parseHex fills gaps with 0xFF", () => {
  const { start, data } = parseHex(HEX);
  assert.equal(start, 0); assert.deepEqual([...data], [1, 2, 3, 4, 0xff, 0xff, 0xaa, 0xbb]);
});
test("parseHex rejects bad checksum", () => assert.throws(() => parseHex(":0400000001020304F3\n"), /checksum/));
test("parseHex handles extended linear address", () => {
  const { start } = parseHex(":020000040001F9\n:0100000055AA\n:00000001FF\n"); assert.equal(start, 0x10000);
});
test("STK500 load address is a word address, little endian", () => assert.deepEqual(stkLoadAddress(0x0180), [0x55, 0xc0, 0x00, 0x20]));
test("STK500 prog page framing", () => {
  const f = stkProgPage([1, 2, 3]); assert.deepEqual(f, [0x64, 0, 3, 0x46, 1, 2, 3, 0x20]);
});
test("pages are padded to 128 with 0xFF", () => {
  const p = stkPages(new Uint8Array(130).fill(7), 0); assert.equal(p.length, 2);
  assert.equal(p[1].addr, 128); assert.equal(p[1].bytes.length, 128); assert.equal(p[1].bytes[2], 0xff);
});
test("friendly errors in the default language (ru)", () => {
  Object.defineProperty(globalThis, "navigator", { value: { serial: {} }, configurable: true });
  assert.equal(friendlyError(new Error("Failed to open serial port.")), DICT.ru.flash.busy);
  assert.match(friendlyError(new Error("Failed to connect with the device")), /BOOT/);
  Object.defineProperty(globalThis, "navigator", { value: {}, configurable: true }); assert.equal(friendlyError(new Error("x")), DICT.ru.flash.noSerial);
});
