#!/usr/bin/env node
// Run a virtual device headless: its firmware builds + build/model/device.json, scripted presses / turns, a PNG of a display.
//   node device_run.mjs --device build/model/device.json --fw build/fw --ms 3000
//        [--do "press btn_play 120 @500; hold btn_play 1000 @1000; turn enc 3 @2500"] [--shot tft out.png]
// Prints the firmware's serial lines (one per line, as the board printed them); exit 0. press = hold = down, then up after <ms> (150);
// turn <id> <n> (1); down / up <id>. A step that does not parse exits 64 before anything runs.
import { readFileSync, writeFileSync } from 'node:fs';
import * as vc from '../runtime/index.mjs';
import { nodeFirmwareLoader, pngOf } from '../runtime/node.mjs';

const a = process.argv.slice(2), get = (k, d) => { const i = a.indexOf(k); return i < 0 ? d : a[i + 1]; };
if (!get('--device') || !get('--fw')) { console.error('usage: device_run.mjs --device <device.json> --fw <dir> [--ms N] [--do "..."] [--shot <id> <png>]'); process.exit(64); }
const device = JSON.parse(readFileSync(get('--device'), 'utf8'));
const m = await vc.buildDevice({ vc, loadFirmware: nodeFirmwareLoader(get('--fw')), device });
m.sim.on('serial', (e) => console.log(e.line));
m.sim.on('crash', (e) => { console.error(`crash ${e.board} at ${(e.t / 1e6).toFixed(3)} s: ${e.error && e.error.stack || e.error}`); process.exitCode = 1; });
for (const step of (get('--do', '') || '').split(';').map((s) => s.trim()).filter(Boolean)) {
  const w = step.split(/\s+/), at = w.find((x) => x.startsWith('@')), [verb, id, ...rest] = w.filter((x) => x !== at);
  const t = at ? +at.slice(1) * 1000 : 0, arg = rest.length ? +rest[0] : { press: 150, hold: 150, turn: 1 }[verb];
  const run = { press: () => m.press(id, arg), hold: () => m.press(id, arg), down: () => m.down(id), up: () => m.up(id), turn: () => m.turn(id, arg) }[verb];
  const known = (device.elements || []).some((e) => e.id === id);
  if (!run || !known || !Number.isFinite(t) || t < 0 || rest.length > 1 || (arg !== undefined && !Number.isFinite(arg))) {
    console.error(`bad step: ${step} (press|hold <id> [ms] [@t ms]; turn <id> [n] [@t]; down|up <id> [@t]; elements: ${(device.elements || []).map((e) => e.id).join(', ')})`);
    process.exit(64);
  }
  m.sim.at(t, run);
}
const ms = +get('--ms', '1000'), si = a.indexOf('--shot');
const shotOk = si < 0 || (a[si + 2] && (device.elements || []).some((e) => e.id === a[si + 1] && m.chips[e.id] && m.chips[e.id].fb));
if (!Number.isFinite(ms) || ms <= 0 || !shotOk) {
  console.error(!shotOk ? `bad --shot: a display element id and a PNG path (displays: ${m.displays().map((d) => d.id).join(', ') || 'none'})` : `bad --ms: ${get('--ms')}`);
  process.exit(64);
}
m.sim.run(ms * 1000);
if (si >= 0) writeFileSync(a[si + 2], pngOf(m.display(a[si + 1])));
