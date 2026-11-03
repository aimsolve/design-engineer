// Shared helpers of the virtual-device tests: find emscripten, build a fixture sketch with build/build.mjs (cached
// by the hash of the shim + build script + sketch), run scripts/device_run.mjs, decode a PNG. Test-only, never shipped.
//
// Emscripten: em++ on PATH, or the local emsdk at $EMSDK / ~/.cache/emsdk (emsdk_env.sh is sourced). When one is found, a `docker`
// that fails is put first on PATH so build.mjs takes the local em++ (Docker Desktop can hang). No em++ but a docker that answers
// `docker info` within 20 s: build.mjs uses docker. Neither: every wasm test skips with the reason. Never on a shared server.
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync, chmodSync } from 'node:fs';
import { homedir, hostname, tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { inflateSync } from 'node:zlib';

export const HERE = dirname(fileURLToPath(import.meta.url));
export const SKILL = resolve(HERE, '..');
export const FIXTURES = join(HERE, 'fixtures');
export const BUILD = join(SKILL, 'build', 'build.mjs');
export const DEVICE_RUN = join(SKILL, 'scripts', 'device_run.mjs');

// A shared server that must not run emscripten builds: DE_NO_HEAVY_BUILD=1, or a VPS-looking host name.
function onVps() {
  return process.env.DE_NO_HEAVY_BUILD === '1' || /vps|ovh/i.test(hostname());
}

function envOf(script) {
  const r = spawnSync('bash', ['-c', `source "${script}" >/dev/null 2>&1; env -0`], { encoding: 'latin1', timeout: 60000 });
  if (r.status !== 0) return null;
  const env = {};
  for (const kv of r.stdout.split('\0')) { const i = kv.indexOf('='); if (i > 0) env[kv.slice(0, i)] = kv.slice(i + 1); }
  return env;
}

const works = (cmd, args, env, timeout = 60000) => {
  const r = spawnSync(cmd, args, { env, stdio: 'ignore', timeout });
  return r.status === 0;
};

let cached = null;
// -> { env } for spawning build.mjs, or { skip: "<reason>" }
export function emEnv() {
  if (cached) return cached;
  if (onVps()) return (cached = { skip: 'wasm builds never run on a shared server (heavy builds go to a workstation)' });
  let env = { ...process.env };
  if (!works('em++', ['--version'], env)) {
    const root = process.env.EMSDK || join(homedir(), '.cache', 'emsdk');
    const script = join(root, 'emsdk_env.sh');
    const e = existsSync(script) ? envOf(script) : null;
    if (e) env = { ...process.env, ...e };
  }
  if (!env.EMSDK_PYTHON && env.EMSDK) {
    const pyDir = join(env.EMSDK, 'python');
    const v = existsSync(pyDir) ? readdirSync(pyDir).sort().pop() : null;
    if (v && existsSync(join(pyDir, v, 'bin', 'python3'))) env.EMSDK_PYTHON = join(pyDir, v, 'bin', 'python3');
  }
  if (works('em++', ['--version'], env)) {
    const nodock = join(tmpdir(), 'vc-test-nodocker');
    mkdirSync(nodock, { recursive: true });
    writeFileSync(join(nodock, 'docker'), '#!/bin/sh\nexit 1\n'); chmodSync(join(nodock, 'docker'), 0o755);
    env.PATH = `${nodock}:${env.PATH}`;
    return (cached = { env, how: 'local em++' });
  }
  if (works('docker', ['info'], process.env, 20000)) return (cached = { env: { ...process.env }, how: 'docker' });
  return (cached = { skip: 'no emscripten: no em++ on PATH, no emsdk at $EMSDK or ~/.cache/emsdk, and docker does not answer' });
}

function sourceHash(sketch) {
  const h = createHash('sha256');
  h.update(readFileSync(BUILD));
  const shim = join(SKILL, 'shim');
  const walk = (d) => { for (const f of readdirSync(d, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
    const p = join(d, f.name); if (f.isDirectory()) walk(p); else { h.update(p.slice(shim.length)); h.update(readFileSync(p)); } } };
  walk(shim);
  h.update(readFileSync(sketch));
  return h.digest('hex').slice(0, 16);
}

const built = new Map();
// Build tests/fixtures/<name>.ino for esp32 -> the directory holding <name>.mjs + <name>.wasm (the --fw dir of device_run).
// A failed build throws the compiler's error lines (once per run; later calls rethrow the same error).
export function buildFixture(name) {
  if (built.has(name)) { const v = built.get(name); if (v instanceof Error) throw v; return v; }
  const em = emEnv();
  if (em.skip) throw new Error(em.skip);
  const sketch = join(FIXTURES, `${name}.ino`);
  const out = join(tmpdir(), 'vc-device-fixtures', `${name}-${sourceHash(sketch)}`);
  if (!existsSync(join(out, `${name}.wasm`))) {
    mkdirSync(out, { recursive: true });
    const r = spawnSync(process.execPath, [BUILD, '--name', name, '--board', 'esp32', '--sketch', sketch, '--out', out],
      { env: em.env, encoding: 'utf8', timeout: 15 * 60 * 1000 });
    if (r.status !== 0) {
      const all = `${r.stderr || ''}\n${r.stdout || ''}`;
      const errs = all.split('\n').filter((l) => /error:|undefined symbol/.test(l) && !/^\s*em\+\+: error: '/.test(l)).slice(0, 25).join('\n');
      const e = new Error(`build.mjs ${name} failed (${r.status}):\n${errs || all.slice(-3000)}`);
      built.set(name, e);
      throw e;
    }
  }
  built.set(name, out);
  return out;
}

// node scripts/device_run.mjs ... -> {status, stdout, stderr, lines}
export function deviceRun({ device, fw, ms, doScript = null, shot = null }) {
  const args = [DEVICE_RUN, '--device', join(FIXTURES, device), '--fw', fw, '--ms', String(ms)];
  if (doScript) args.push('--do', doScript);
  if (shot) args.push('--shot', shot[0], shot[1]);
  const r = spawnSync(process.execPath, args, { encoding: 'utf8', timeout: 10 * 60 * 1000 });
  return { status: r.status, stdout: r.stdout || '', stderr: r.stderr || '', lines: (r.stdout || '').split('\n') };
}

// the firmware's lines (device_run may prefix a time / board): every line matching re, as match arrays
export const grab = (lines, re) => lines.map((l) => l.match(re)).filter(Boolean);

export function decodePng(bytes) {
  const buf = Buffer.from(bytes);
  if (buf.subarray(0, 8).toString('hex') !== '89504e470d0a1a0a') throw new Error('not a PNG');
  let pos = 8, w = 0, h = 0, ch = 0; const idat = [];
  while (pos < buf.length) {
    const n = buf.readUInt32BE(pos), typ = buf.toString('latin1', pos + 4, pos + 8), body = buf.subarray(pos + 8, pos + 8 + n);
    if (typ === 'IHDR') {
      w = body.readUInt32BE(0); h = body.readUInt32BE(4);
      if (body[8] !== 8) throw new Error('PNG: 8-bit only'); ch = { 0: 1, 2: 3, 6: 4 }[body[9]];
    } else if (typ === 'IDAT') idat.push(body);
    pos += 12 + n;
  }
  const raw = inflateSync(Buffer.concat(idat)), stride = w * ch, out = Buffer.alloc(h * stride);
  for (let y = 0; y < h; y++) {
    const f = raw[y * (stride + 1)], line = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1));
    for (let i = 0; i < stride; i++) {
      const a = i >= ch ? out[y * stride + i - ch] : 0, b = y ? out[(y - 1) * stride + i] : 0, c = i >= ch && y ? out[(y - 1) * stride + i - ch] : 0;
      let v = line[i];
      if (f === 1) v += a; else if (f === 2) v += b; else if (f === 3) v += (a + b) >> 1;
      else if (f === 4) { const p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c); v += pa <= pb && pa <= pc ? a : pb <= pc ? b : c; }
      out[y * stride + i] = v & 255;
    }
  }
  const px = (x, y) => { const o = (y * w + x) * ch; return ch === 1 ? [out[o], out[o], out[o]] : [out[o], out[o + 1], out[o + 2]]; };
  return { w, h, px };
}

export const colour = {
  red: ([r, g, b]) => r >= 200 && g <= 50 && b <= 50,
  green: ([r, g, b]) => r <= 50 && g >= 200 && b <= 50,
  blue: ([r, g, b]) => r <= 50 && g <= 50 && b >= 200,
  white: ([r, g, b]) => r >= 200 && g >= 200 && b >= 200,
  lit: (p) => Math.max(...p) > 100,
  dark: (p) => Math.max(...p) <= 40,
};
