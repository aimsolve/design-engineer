// A virtual device running REAL firmware: the test's own sketches in tests/fixtures/ are built to wasm
// with build/build.mjs and run headless, through the CLI (scripts/device_run.mjs) and the API (runtime/device.mjs buildDevice + VM).
// Written from the spec before the implementation exists. Needs emscripten (tests/helpers.mjs finds it; skips with the reason
// otherwise). Never on a shared server. First run builds 4 sketches (minutes); later runs reuse the builds while shim/ + build.mjs + the
// sketch are unchanged.
//
//   node --test --test-timeout=1200000 agent/skills/virtual-controllers/tests/device_fw.test.mjs
//
// Contract under test:
//   node scripts/device_run.mjs --device <device.json> --fw <fw dir> --ms <run ms> [--do "press <id> <ms> @<t ms>; hold <id> <ms> @<t>;
//        turn <id> <n> @<t>"] [--shot <element> <out.png>]   prints the firmware's serial lines, exit 0
//   runtime/device.mjs buildDevice({vc, loadFirmware, device, firmware}) -> machine {sim, elements, down(id), up(id), press(id, ms),
//        turn(id, n), display(id), pinTable(board), ...}, usable by VM as machineModule {META: deviceMeta(device), buildMachine}
//   shim: Arduino SPI.h (SPIClass, SPISettings, setFrequency, writeBytes, global SPI), Wire.requestFrom/available/read,
//        ESP-IDF esp_lcd over SPI (spi_bus_initialize, esp_lcd_new_panel_io_spi + on_color_trans_done, esp_lcd_new_panel_st7789,
//        esp_lcd_panel_* ops, esp_lcd_panel_io_tx_param / tx_color asynchronous like DMA)
// Test names carry the "done when" they serve: [2] a held button stays pressed, [3] button / encoder model, [4] display model,
// [5] firmware APIs, [6] headless run (press / hold / turn by id, PNG, serial).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { emEnv, buildFixture, deviceRun, grab, decodePng, colour, FIXTURES } from './helpers.mjs';

const EM = emEnv();
const opt = EM.skip ? { skip: EM.skip } : {};
const tmp = () => mkdtempSync(join(tmpdir(), 'vc-device-'));
const json = (f) => JSON.parse(readFileSync(join(FIXTURES, f), 'utf8'));
const okRun = (r) => assert.equal(r.status, 0, `device_run.mjs exit ${r.status}\n${r.stderr.slice(-2000)}\n${r.stdout.slice(-1000)}`);

// ---------------------------------------------------------------- buttons + encoder through the CLI

const BTN_SCRIPT = 'press btn_play 120 @500; hold btn_play 1000 @1000; press btn_clean 100 @2200; press btn_hi 100 @2400; '
  + 'turn enc 3 @2600; turn enc -2 @2900; press enc 100 @3200';
let btnRun = null;
const buttons = () => btnRun || (btnRun = deviceRun({ device: 'buttons.device.json', fw: buildFixture('buttons'), ms: 3600, doScript: BTN_SCRIPT }));

test('[6] device_run: the firmware boots and its serial lines are printed, exit 0', opt, () => {
  const r = buttons();
  okRun(r);
  assert.ok(grab(r.lines, /\bREADY\b/).length === 1, r.stdout.slice(0, 500));
});

test('[3] firmware sees contact bounce: each press and release of btn_play is several level changes (odd), ending LOW / HIGH', opt, () => {
  const r = buttons(); okRun(r);
  const e = grab(r.lines, /EDGES 27 (\d+) LEVEL (\d)/).map((m) => [+m[1], +m[2]]);
  assert.deepEqual(e.map((x) => x[1]), [0, 1, 0, 1], `two presses = press, release, press, release: ${JSON.stringify(e)}`);
  for (const [n] of e) { assert.ok(n >= 3, `bounce: several edges, got ${n}`); assert.equal(n % 2, 1, `odd count ${n}`); }
});

test('[3] bounce_ms 0: exactly one edge on press and one on release', opt, () => {
  const r = buttons(); okRun(r);
  const e = grab(r.lines, /EDGES 14 (\d+) LEVEL (\d)/).map((m) => `${m[1]}/${m[2]}`);
  assert.deepEqual(e, ['1/0', '1/1']);
});

test('[3] active level from the wiring: a button to 3V3 reads HIGH pressed, the pinMode default (pull-down: LOW) released', opt, () => {
  const r = buttons(); okRun(r);
  const e = grab(r.lines, /EDGES 26 (\d+) LEVEL (\d)/).map((m) => +m[2]);
  assert.deepEqual(e, [1, 0]);
});

test('[2][3] a press lasts as long as it is held; a long press is a long hold (the firmware measures it)', opt, () => {
  const r = buttons(); okRun(r);
  const p = grab(r.lines, /PRESS 27 (\d+)/).map((m) => +m[1]);
  assert.equal(p.length, 2, `two presses of btn_play: ${JSON.stringify(p)}`);
  assert.ok(p[0] >= 105 && p[0] <= 135, `press 120 ms measured ${p[0]}`);
  assert.ok(p[1] >= 980 && p[1] <= 1030, `hold 1000 ms measured ${p[1]}`);
});

test('[3] encoder: turn 3 then turn -2 -> the firmware counts +3, then +1 (quadrature, detent 4, CW/CCW)', opt, () => {
  const r = buttons(); okRun(r);
  assert.deepEqual(grab(r.lines, /\bENC (-?\d+)/).map((m) => +m[1]), [3, 1]);
});

test('[3] encoder push (SW) works like a button: press enc = SW LOW, then released', opt, () => {
  const r = buttons(); okRun(r);
  assert.deepEqual(grab(r.lines, /EDGES 25 (\d+) LEVEL (\d)/).map((m) => +m[2]), [0, 1]);
});

// ---------------------------------------------------------------- ST7789 over Arduino SPI

let st = null;
const st7789 = () => st || (st = (() => {
  const d = tmp(), fw = buildFixture('st7789_spi');
  const a = deviceRun({ device: 'st7789.device.json', fw, ms: 1300, shot: ['tft', join(d, 'on.png')] });
  const b = deviceRun({ device: 'st7789.device.json', fw, ms: 2300, shot: ['tft', join(d, 'blk.png')] });
  return { a, b, on: join(d, 'on.png'), blk: join(d, 'blk.png'), fw };
})());

test('[5] SPI.h: the sketch compiles unchanged and runs (SPIClass, SPISettings, writeBytes, setFrequency)', opt, () => {
  const { a } = st7789(); okRun(a);
  assert.ok(grab(a.lines, /\bDRAWN\b/).length === 1, a.stdout.slice(-800));
});

test('[4] honest frame time: a 240x320 RGB565 frame costs >= 30.72 ms at 40 MHz and >= 122.88 ms at 10 MHz', opt, () => {
  const { a } = st7789(); okRun(a);
  const f40 = +(grab(a.lines, /FULL_US (\d+)/)[0] || [])[1], f10 = +(grab(a.lines, /FULL10_US (\d+)/)[0] || [])[1];
  assert.ok(f40 >= 30720 && f40 <= 46080, `40 MHz frame ${f40} us (bytes*8/clock = 30720)`);
  assert.ok(f10 >= 122880 && f10 <= 184320, `10 MHz frame ${f10} us (= 122880)`);
});

test('[4][6] PNG of the display: blue screen, the red partial window exactly where it was written; CS HIGH bytes ignored', opt, () => {
  const { a, on } = st7789(); okRun(a);
  const img = decodePng(readFileSync(on));
  assert.deepEqual([img.w, img.h], [240, 320]);
  for (const [x, y] of [[100, 200], [119, 209], [110, 205]]) assert.ok(colour.red(img.px(x, y)), `red at (${x},${y}): ${img.px(x, y)}`);
  for (const [x, y] of [[5, 5], [99, 205], [120, 205], [110, 199], [110, 210], [239, 319]])
    assert.ok(colour.blue(img.px(x, y)), `blue at (${x},${y}) (a green here = the CS-HIGH write reached the display): ${img.px(x, y)}`);
});

test('[4] backlight: BLK wired to GPIO4 going LOW makes the picture dark', opt, () => {
  const { b, blk } = st7789(); okRun(b);
  assert.ok(grab(b.lines, /\bBLK_OFF\b/).length === 1, b.stdout.slice(-500));
  const img = decodePng(readFileSync(blk));
  for (const [x, y] of [[5, 5], [110, 205], [200, 300]]) assert.ok(colour.dark(img.px(x, y)), `dark at (${x},${y}): ${img.px(x, y)}`);
});

test('[4] bytes reach the display only on the SPI pins the firmware configured (SDA wired to GPIO13, sketch uses MOSI 23)', opt, () => {
  const d = tmp();
  const r = deviceRun({ device: 'st7789_wrongpins.device.json', fw: buildFixture('st7789_spi'), ms: 1300, shot: ['tft', join(d, 'x.png')] });
  okRun(r);
  const img = decodePng(readFileSync(join(d, 'x.png')));
  for (const [x, y] of [[5, 5], [110, 205]]) assert.ok(colour.dark(img.px(x, y)), `nothing shown at (${x},${y}): ${img.px(x, y)}`);
});

test('[4] a display whose CS is not wired takes every byte on its bus (the CS-HIGH green fill shows)', opt, () => {
  const d = tmp();
  const r = deviceRun({ device: 'st7789_nocs.device.json', fw: buildFixture('st7789_spi'), ms: 1300, shot: ['tft', join(d, 'x.png')] });
  okRun(r);
  const img = decodePng(readFileSync(join(d, 'x.png')));
  assert.ok(colour.green(img.px(5, 5)), `(5,5): ${img.px(5, 5)}`);
});

// ---------------------------------------------------------------- ESP-IDF esp_lcd

let lcd = null;
const espLcd = () => lcd || (lcd = (() => {
  const d = tmp();
  const r = deviceRun({ device: 'esp_lcd.device.json', fw: buildFixture('esp_lcd_frames'), ms: 1500, shot: ['tft', join(d, 'lcd.png')] });
  return { r, png: join(d, 'lcd.png') };
})());
const num = (lines, re) => { const m = grab(lines, re)[0]; return m ? +m[m.length - 1] : NaN; };

test('[5] esp_lcd: an IDF-style sketch (spi_bus_initialize, panel_io_spi, new_panel_st7789, panel ops) compiles and draws', opt, () => {
  const { r } = espLcd(); okRun(r);
  assert.ok(grab(r.lines, /\bDRAWN\b/).length === 1, r.stdout.slice(-800));
});

test('[5] esp_lcd: draw_bitmap returns at once (DMA-like); on_color_trans_done fires after bytes*8/pclk of machine time', opt, () => {
  const { r } = espLcd(); okRun(r);
  const call = num(r.lines, /CALL_US (\d+)/), done = num(r.lines, /DONE_US (\d+)/);
  assert.ok(call < 1000, `draw_bitmap of 19200 bytes returned after ${call} us`);
  assert.ok(done >= 3840 && done <= 6000, `callback ${done} us after the call (19200*8/40 MHz = 3840)`);
  assert.equal(num(r.lines, /\bCB (\d+)/), 19, 'one on_color_trans_done per tx_color (1 + 8 + 8 bands, 1 raw tx_color, 1 square)');
});

test('[4][5] esp_lcd: a full 240x320 frame of 8 bands takes >= 30.72 ms at 40 MHz (honest frame rate)', opt, () => {
  const { r } = espLcd(); okRun(r);
  for (const k of [1, 2]) {
    const f = num(r.lines, new RegExp(`FRAME_US ${k} (\\d+)`));
    assert.ok(f >= 30720 && f <= 46080, `frame ${k}: ${f} us`);
  }
});

test('[4][5] esp_lcd: the picture — green frame, raw tx_param/tx_color white square, mirror(x) puts the blue square at x 230..239', opt, () => {
  const { r, png } = espLcd(); okRun(r);
  const img = decodePng(readFileSync(png));
  assert.ok(colour.green(img.px(5, 5)), `(5,5) ${img.px(5, 5)}`);
  assert.ok(colour.green(img.px(225, 5)), `(225,5) ${img.px(225, 5)}`);
  assert.ok(colour.white(img.px(101, 101)), `(101,101) ${img.px(101, 101)}`);
  assert.ok(colour.green(img.px(104, 101)), `(104,101) ${img.px(104, 101)}`);
  assert.ok(colour.blue(img.px(235, 5)), `mirrored square at (235,5): ${img.px(235, 5)}`);
});

// ---------------------------------------------------------------- SSD1306 over Wire + requestFrom

test('[4][5] ssd1306 over Wire: page 0 of the left half lit; requestFrom answered by the display, 0 bytes from an empty address', opt, () => {
  const d = tmp();
  const r = deviceRun({ device: 'ssd1306.device.json', fw: buildFixture('ssd1306_wire'), ms: 600, shot: ['oled', join(d, 'o.png')] });
  okRun(r);
  assert.deepEqual(grab(r.lines, /RQ 3C (\d+) (\d+)/).map((m) => [+m[1], +m[2]]), [[1, 1]], r.stdout.slice(-600));
  assert.deepEqual(grab(r.lines, /RQ 50 (\d+) (\d+)/).map((m) => [+m[1], +m[2]]), [[0, 0]]);
  assert.equal(num(r.lines, /NACK 50 (\d+)/), 2);
  const img = decodePng(readFileSync(join(d, 'o.png')));
  assert.deepEqual([img.w, img.h], [128, 64]);
  assert.ok(colour.lit(img.px(10, 3)), `(10,3) ${img.px(10, 3)}`);
  assert.ok(colour.dark(img.px(100, 3)), `(100,3) ${img.px(100, 3)}`);
  assert.ok(colour.dark(img.px(10, 20)), `(10,20) ${img.px(10, 20)}`);
});

// ---------------------------------------------------------------- the API: buildDevice, VM, display(id), pngOf

async function api() {
  const vc = await import('../runtime/index.mjs');
  const { buildDevice, deviceMeta } = await import('../runtime/device.mjs');
  const { nodeFirmwareLoader, pngOf } = await import('../runtime/node.mjs');
  return { vc, buildDevice, deviceMeta, nodeFirmwareLoader, pngOf };
}

test('[6] buildDevice: down / up by element id drive the wired pin; press(id, ms) and turn(id, n) reach the firmware', opt, async () => {
  const { vc, buildDevice, nodeFirmwareLoader } = await api();
  const device = json('buttons.device.json');
  const m = await buildDevice({ vc, loadFirmware: nodeFirmwareLoader(buildFixture('buttons')), device, firmware: {} });
  const lines = []; m.sim.on('serial', (e) => lines.push(e.line));
  assert.deepEqual([...m.elements].map((e) => e.id ?? e).sort(), ['btn_clean', 'btn_hi', 'btn_play', 'enc']);
  m.sim.run(300e3);
  const lvl = () => m.sim.board('mcu').level(27);
  assert.equal(lvl(), 1, 'released: INPUT_PULLUP reads 1');
  m.down('btn_play'); m.sim.run(m.sim.t + 200e3);
  assert.equal(lvl(), 0, 'held: LOW');
  m.sim.run(m.sim.t + 800e3);
  assert.equal(lvl(), 0, 'still held 1 s later');
  m.up('btn_play'); m.sim.run(m.sim.t + 200e3);
  assert.equal(lvl(), 1);
  m.press('btn_play', 150); m.sim.run(m.sim.t + 400e3);
  m.turn('enc', 2); m.sim.run(m.sim.t + 400e3);
  const presses = grab(lines, /PRESS 27 (\d+)/).map((x) => +x[1]);
  assert.equal(presses.length, 2, JSON.stringify(lines));
  assert.ok(presses[0] >= 980 && presses[0] <= 1030, `held ~1000 ms: ${presses[0]}`);
  assert.ok(presses[1] >= 135 && presses[1] <= 165, `press 150: ${presses[1]}`);
  assert.deepEqual(grab(lines, /\bENC (-?\d+)/).map((x) => +x[1]), [2]);
});

test('[6] the device is a machine the VM runs (META = deviceMeta, buildMachine = buildDevice): serial lands in vm.logs', opt, async () => {
  const { vc, buildDevice, deviceMeta, nodeFirmwareLoader } = await api();
  const device = json('buttons.device.json');
  const vm = new vc.VM({ vc, loadFirmware: nodeFirmwareLoader(buildFixture('buttons')),
    machineModule: { META: deviceMeta(device), buildMachine: (o) => buildDevice({ ...o, device }) } });
  await vm.init();
  vm.step(300);
  assert.ok(vm.logs.mcu.some((l) => /READY/.test(l.line)), JSON.stringify(vm.logs));
  vm.m.press('btn_play', 100); vm.step(400);
  assert.ok(vm.logs.mcu.some((l) => /PRESS 27/.test(l.line)), JSON.stringify(vm.logs.mcu.slice(-5)));
  assert.ok(Array.isArray(vm.m.pinTable('mcu')), 'pinTable(board) for the board card');
});

test('[6] display(id) + pngOf: the agent gets the picture of a running device', opt, async () => {
  const { vc, buildDevice, nodeFirmwareLoader, pngOf } = await api();
  const m = await buildDevice({ vc, loadFirmware: nodeFirmwareLoader(buildFixture('st7789_spi')), device: json('st7789.device.json'), firmware: {} });
  m.sim.run(1300e3);
  const d = m.display('tft');
  assert.equal(d.on, true);
  assert.ok(d.frames > 0, `frames ${d.frames}`);
  assert.equal(d.fb.length, 240 * 320 * 4);
  const img = decodePng(pngOf(d));
  assert.ok(colour.red(img.px(110, 205)) && colour.blue(img.px(5, 5)), `${img.px(110, 205)} / ${img.px(5, 5)}`);
});

