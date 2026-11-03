// Regressions of the first independent review of the virtual device (each test reproduces one finding).
//   node --test --test-timeout=1800000 agent/skills/virtual-controllers/tests/review_fixes.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { emEnv, buildFixture, deviceRun, grab, decodePng, colour, SKILL, BUILD, DEVICE_RUN } from './helpers.mjs';

const EM = emEnv();
const opt = EM.skip ? { skip: EM.skip } : {};

test('esp_lcd drawn from a Wire onReceive handler while a transfer is in flight: every handler finishes (no spin in the drain)', opt, () => {
  const fw = buildFixture('drain_master'); buildFixture('drain_slave');
  const fwDir = mkdtempSync(join(tmpdir(), 'vc-drain-'));
  for (const n of ['drain_master', 'drain_slave']) for (const ext of ['.mjs', '.wasm'])
    spawnSync('cp', [join(n === 'drain_master' ? fw : buildFixture('drain_slave'), n + ext), fwDir]);
  const png = join(fwDir, 'tft.png');
  const r = deviceRun({ device: 'drain.device.json', fw: fwDir, ms: 800, shot: ['tft', png] });
  assert.equal(r.status, 0, r.stderr.slice(-1500));
  const done = grab(r.lines, /^RX (\d+) DONE/).length;
  assert.equal(done, 10, `handlers finished: ${done}\n${r.stdout.slice(-800)}`);
  const img = decodePng(readFileSync(png));
  assert.ok(colour.green(img.px(100, 20)), `the first band shows the bytes the buffer held when its transfer ended (GREEN), got ${img.px(100, 20)}`);
});

test('review 2: a delay() inside the Wire handler, two bands queued: the handlers still finish (no spin on a due service)', opt, () => {
  const fwDir = mkdtempSync(join(tmpdir(), 'vc-drain2-'));
  for (const n of ['drain_master', 'drain_slave_wait']) for (const ext of ['.mjs', '.wasm']) spawnSync('cp', [join(buildFixture(n), n + ext), fwDir]);
  const t0 = Date.now();
  const r = deviceRun({ device: 'drain_wait.device.json', fw: fwDir, ms: 800 });
  assert.equal(r.status, 0, r.stderr.slice(-1500));
  assert.equal(grab(r.lines, /^RX (\d+) DONE/).length, 10, r.stdout.slice(-800));
  assert.ok(Date.now() - t0 < 60000);
});

test('device_run: --shot of an element that is not a display, or a bad --ms, exits 64 before running', opt, () => {
  const fw = buildFixture('buttons');
  assert.equal(deviceRun({ device: 'buttons.device.json', fw, ms: 'x' }).status, 64);
  assert.equal(deviceRun({ device: 'buttons.device.json', fw, ms: 100, shot: ['btn_play', join(tmpdir(), 'x.png')] }).status, 64);
});

test('device_run: a step without a duration takes the default; a step that does not parse exits 64 at once', opt, () => {
  const fw = buildFixture('buttons');
  const ok = deviceRun({ device: 'buttons.device.json', fw, ms: 900, doScript: 'press btn_play @300; turn enc @600' });
  assert.equal(ok.status, 0, ok.stderr);
  const p = grab(ok.lines, /PRESS 27 (\d+)/).map((m) => +m[1]);
  assert.ok(p.length === 1 && p[0] >= 130 && p[0] <= 170, `default press 150 ms: ${p}`);
  const t0 = Date.now();
  const bad = deviceRun({ device: 'buttons.device.json', fw, ms: 900, doScript: 'press btn_play abc @300' });
  assert.equal(bad.status, 64);
  assert.ok(Date.now() - t0 < 20000, 'it does not run the device first');
  assert.equal(deviceRun({ device: 'buttons.device.json', fw, ms: 900, doScript: 'press no_such 100' }).status, 64);
});

// stock LVGL 9.2 + esp_lcd, no flush wait callback in the firmware (LVGL's own wait is a bare spin): the example player
const PLAYER = join(SKILL, 'examples', 'player_demo');
const LIBDEPS = join(PLAYER, 'electronics', 'firmware', 'player', '.pio', 'libdeps', 'player');
test('stock LVGL + esp_lcd (no flush wait callback in the firmware) runs and keeps drawing', existsSync(LIBDEPS) && !EM.skip ? {} : { skip: EM.skip || `no LVGL: run \`pio pkg install -e player\` in ${PLAYER}/electronics/firmware/player` }, () => {
  const src = readFileSync(join(PLAYER, 'electronics', 'firmware', 'player', 'src', 'main.cpp'), 'utf8');
  assert.ok(!/flush_wait_cb/.test(src), 'the example firmware stays stock: no LVGL flush wait callback of its own');
  const out = mkdtempSync(join(tmpdir(), 'vc-lvgl-'));
  const b = spawnSync(process.execPath, [BUILD, '--name', 'player', '--board', 'esp32', '--sketch', join(PLAYER, 'electronics/firmware/player/src/main.cpp'),
    '--libdeps', LIBDEPS, '--include', join(PLAYER, 'electronics/firmware/player/include'), '--out', out], { env: EM.env, encoding: 'utf8', timeout: 15 * 60 * 1000 });
  assert.equal(b.status, 0, (b.stderr || '').slice(-2000));
  const dev = join(out, 'device.json');
  spawnSync('cp', [join(PLAYER, 'build', 'model', 'device.json'), dev]);
  if (!existsSync(dev)) return;
  const r = spawnSync(process.execPath, [DEVICE_RUN, '--device', dev, '--fw', out, '--ms', '2500', '--do', 'press btn_play 150 @800'], { encoding: 'utf8', timeout: 120000 });
  assert.equal(r.status, 0, r.stderr);
  const fps = grab(r.stdout.split('\n'), /^FPS (\d+)/).map((m) => +m[1]);
  assert.ok(fps.some((f) => f >= 20), `it keeps drawing (FPS lines ${fps})`);
});
