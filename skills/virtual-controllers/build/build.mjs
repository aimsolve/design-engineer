#!/usr/bin/env node
// Compile an unchanged Arduino .ino to wasm (emscripten in docker, or a local em++ when there is no docker; ASYNCIFY)
// on top of the virtual-controllers shim.
//   node build.mjs --name master --board esp32 --sketch path/to/x.ino|src/main.cpp --out out/fw [--libsrc HX711.cpp]
//        [--libdeps .pio/libdeps/<env>] [--include include]
// Output: <out>/<name>.mjs + <out>/<name>.wasm (+ <out>/build/<name>/sketch.cpp, the generated wrapper; <out>/build/obj/ = cached
// objects of the C libraries: the vendored esp_lcd and the --libdeps libraries, rebuilt only when a source or a header changes).
import { readFileSync, writeFileSync, mkdirSync, statSync, existsSync, readdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { resolve, dirname, join, basename } from 'node:path';
import { fileURLToPath } from 'node:url';

export const SKILL = resolve(dirname(fileURLToPath(import.meta.url)), '..');
export const EMSDK_IMAGE = 'emscripten/emsdk:3.1.74';

// Arduino IDE does two things to a .ino: prepends #include <Arduino.h>, and generates prototypes for the functions.
// We do both (regex, top-level definitions after setup() only — enough for these sketches).
export function inoToCpp(src) {
  const lines = src.split('\n');
  const setupIdx = lines.findIndex((l) => /^void\s+setup\s*\(/.test(l));
  const types = '(?:void|bool|boolean|int|float|double|long|char|byte|String|unsigned|uint\\d+_t|int\\d+_t)';
  const defRe = new RegExp(`^(${types}(?:\\s+${types})*\\s+(?:IRAM_ATTR\\s+)?\\**\\s*)([A-Za-z_]\\w*)\\s*\\(([^;{}]*)\\)\\s*(\\{)?\\s*$`);
  const protos = [];
  for (let i = Math.max(setupIdx, 0); i < lines.length; i++) {
    const m = lines[i].match(defRe);
    if (!m || m[2] === 'setup' || m[2] === 'loop') continue;
    if (!m[4]) { let j = i + 1; while (j < lines.length && !lines[j].trim()) j++; if (!/^\s*\{/.test(lines[j] || '')) continue; }
    protos.push(`${m[1].replace('IRAM_ATTR ', '')}${m[2]}(${m[3]});`);
  }
  const at = setupIdx < 0 ? 0 : setupIdx;
  lines.splice(at, 0, '// --- generated prototypes ---', ...protos, '// --- end ---');
  return `#include <Arduino.h>\n${lines.join('\n')}\n`;
}

// <out>/index.json — what the site's firmware selector lists: [{name, mcu, source, built, wasm}] (merged by name, one entry per build).
export function updateIndex(out, entry) {
  const f = join(out, 'index.json');
  const list = existsSync(f) ? JSON.parse(readFileSync(f, 'utf8')) : [];
  const i = list.findIndex((x) => x.name === entry.name);
  if (i >= 0) list[i] = entry; else list.push(entry);
  writeFileSync(f, JSON.stringify(list, null, 1) + '\n');
}

const onPath = (cmd) => { try { execFileSync(cmd, ['--version'], { stdio: 'ignore' }); return true; } catch { return false; } };
const hasDocker = () => onPath('docker');
const hasEmxx = () => onPath('em++');
const sha = (...xs) => { const h = createHash('sha256'); for (const x of xs) h.update(x); return h.digest('hex').slice(0, 20); };
const SKIP_DIRS = new Set(['examples', 'example', 'test', 'tests', 'docs', 'demos', 'env_support', 'scripts', '.git', '.pio']);
function walk(dir, exts, out = []) {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) { if (!SKIP_DIRS.has(e.name)) walk(p, exts, out); }
    else if (exts.some((x) => e.name.endsWith(x))) out.push(p);
  }
  return out.sort();
}
const treeHash = (dirs) => sha(...dirs.flatMap((d) => (existsSync(d) ? walk(d, ['.h', '.hpp', '.inc']).map((f) => f + readFileSync(f)) : [])));

// A PlatformIO libdeps folder (.pio/libdeps/<env>): every library in it is compiled like PlatformIO does — its library.json
// build.srcDir (else src/ when there is one, else the library root), examples / tests left out; its root, src/ and includeDir are on
// the include path. The libraries the firmware uses on the chip are the ones it gets here: same versions, same sources.
export function libdepsOf(dir) {
  const libs = [];
  for (const e of existsSync(dir) ? readdirSync(dir, { withFileTypes: true }) : []) {
    if (!e.isDirectory() || e.name.startsWith('.')) continue;
    const root = join(dir, e.name), lj = join(root, 'library.json');
    const build = existsSync(lj) ? (JSON.parse(readFileSync(lj, 'utf8')).build || {}) : {};
    const src = build.srcDir ? join(root, build.srcDir) : existsSync(join(root, 'src')) ? join(root, 'src') : root;
    const inc = [root, ...(existsSync(join(root, 'src')) ? [join(root, 'src')] : []), ...(build.includeDir ? [join(root, build.includeDir)] : [])];
    libs.push({ name: e.name, root, src, inc });
  }
  return libs;
}

// compile jobs [{args, out}] in parallel (cpu count): node build.mjs --jobs <file> (also inside the emsdk container)
async function runJobs(file) {
  const { spawn } = await import('node:child_process');
  const { cpus } = await import('node:os');
  const jobs = JSON.parse(readFileSync(file, 'utf8')).filter((j) => !existsSync(j.out));
  let next = 0, failed = null;
  const one = () => new Promise((ok) => {
    if (failed || next >= jobs.length) return ok();
    const j = jobs[next++];
    const p = spawn(j.cmd, j.args, { stdio: ['ignore', 'inherit', 'inherit'] });
    p.on('close', (code) => { if (code) failed = failed || `${j.cmd} ${j.args.at(-3)}: exit ${code}`; ok(one()); });
  });
  await Promise.all(Array.from({ length: Math.max(1, cpus().length) }, one));
  if (failed) { console.error(failed); process.exit(1); }
}

export function buildBoard({ name, board, sketch, out, libSrc = [], libDirs = [], extraFlags = [], libDeps = null, includes = [] }) {
  const outAbs = resolve(out);
  const bdir = join(outAbs, 'build', name), odir = join(outAbs, 'build', 'obj');
  mkdirSync(bdir, { recursive: true }); mkdirSync(odir, { recursive: true });
  const sk = resolve(sketch);
  const main = sk.endsWith('.ino') ? join(bdir, 'sketch.cpp') : sk;            // a .cpp sketch (PlatformIO src/main.cpp) is compiled as it is
  if (sk.endsWith('.ino')) writeFileSync(main, inoToCpp(readFileSync(sk, 'utf8')));
  const flag = board === 'esp32' ? '-DVC_BOARD_ESP32' : '-DVC_BOARD_AVR';
  const libs = libDeps ? libdepsOf(resolve(libDeps)) : [];
  // A local em++ when there is one (the platform's fw worker has emsdk baked in; a laptop with emsdk activated), else Docker.
  const local = hasEmxx() || !hasDocker();
  const mounts = [[SKILL, '/skill'], [outAbs, '/out'], [dirname(sk), '/sketch'], ...includes.map((d, i) => [resolve(d), `/inc${i}`]), ...(libDeps ? [[resolve(libDeps), '/libdeps']] : [])];
  const byDepth = [...mounts].sort((x, y) => y[0].length - x[0].length);   // the deepest mount wins (an out dir inside the skill folder)
  const P = (f) => { if (local) return f; for (const [h, c] of byDepth) if (f === h || f.startsWith(h + '/')) return c + f.slice(h.length); return f; };
  const incDirs = [join(SKILL, 'shim'), join(SKILL, 'shim', 'idf'), join(SKILL, 'lib', 'esp_lcd', 'include'), join(SKILL, 'lib', 'GyverStepper'),
    join(SKILL, 'lib', 'HX711'), join(SKILL, 'lib', 'GyverBus'), ...libDirs, ...includes.map((d) => resolve(d)), ...libs.flatMap((l) => l.inc), dirname(sk)];
  const inc = incDirs.flatMap((d) => ['-I', P(d)]);
  // objects of the C / C++ libraries (vendored esp_lcd on an ESP32 + libdeps, and shim/vc_lvgl.c when LVGL is one of them), cached by
  // source, flags and every header they can see (the shim, esp_lcd, --include, each library's folders, the sketch's folder)
  const esp = board === 'esp32', lvgl = libs.some((l) => l.name.toLowerCase() === 'lvgl');
  const libFiles = [...(esp ? walk(join(SKILL, 'lib', 'esp_lcd', 'src'), ['.c']) : []), ...(lvgl ? [join(SKILL, 'shim', 'vc_lvgl.c')] : []),
    ...libs.flatMap((l) => walk(l.src, ['.c', '.cpp']))];
  const hdr = treeHash([join(SKILL, 'shim'), join(SKILL, 'lib', 'esp_lcd', 'include'), ...includes.map((d) => resolve(d)),
    ...[...new Set(libs.flatMap((l) => l.inc))], dirname(sk)]);
  const cc = local ? execFileSync('em++', ['--version'], { encoding: 'utf8' }).split('\n')[0] : EMSDK_IMAGE;   // a new compiler = new objects
  const jobs = libFiles.map((f) => {
    const c = f.endsWith('.c'), o = join(odir, `${sha(f, readFileSync(f), flag, hdr, extraFlags.join(' '), inc.join(' '), cc)}.o`);
    return { out: P(o), cmd: c ? 'emcc' : 'em++', args: ['-O2', c ? '-std=gnu11' : '-std=gnu++17', '-w', flag, ...inc, ...extraFlags, '-c', P(f), '-o', P(o)], o };
  });
  const jfile = join(bdir, 'jobs.json');
  writeFileSync(jfile, JSON.stringify(jobs));
  const docker = (cmd) => ['run', '--rm', '--platform', 'linux/amd64', ...mounts.flatMap(([h, c]) => ['-v', `${h}:${c}${c === '/out' ? '' : ':ro'}`]), EMSDK_IMAGE, ...cmd];
  if (jobs.some((j) => !existsSync(j.o))) {
    const cmd = ['node', P(join(SKILL, 'build', 'build.mjs')), '--jobs', P(jfile)];
    if (local) execFileSync(process.execPath, cmd.slice(1), { stdio: 'inherit' }); else execFileSync('docker', docker(cmd), { stdio: 'inherit' });
  }
  const args = [
    '-O2', '-std=gnu++17', '-w', flag, ...inc, ...extraFlags,
    P(main), `${P(SKILL)}/shim/vc_core.cpp`, `${P(SKILL)}/shim/vc_main.cpp`, ...(esp ? [`${P(SKILL)}/shim/idf_vc.cpp`] : []),
    ...libSrc.map((f) => `${P(SKILL)}/lib/${f}`), ...jobs.map((j) => j.out),
    '--js-library', `${P(SKILL)}/shim/vc_lib.js`,
    '-sASYNCIFY', '-sASYNCIFY_IMPORTS=["vc_js_yield"]', '-sASYNCIFY_STACK_SIZE=65536',
    '-sMODULARIZE', '-sEXPORT_ES6', '-sEXPORT_NAME=createBoard', '-sENVIRONMENT=web,node',
    '-sEXPORTED_FUNCTIONS=["_main","_vc_shared_addr","_vc_layout"]',
    '-sEXPORTED_RUNTIME_METHODS=["HEAPU8"]',
    // an ESP32 firmware with LVGL needs more than the 8 MB an AVR sketch gets
    '-sINVOKE_RUN=0', `-sINITIAL_MEMORY=${esp ? 16 : 8}MB`, '-sSTACK_SIZE=512KB', '-sFILESYSTEM=0', '-sASSERTIONS=0',
    '-o', `${P(outAbs)}/${name}.mjs`,
  ];
  if (local) execFileSync('em++', args, { stdio: 'inherit' }); else execFileSync('docker', docker(['em++', ...args]), { stdio: 'inherit' });
  const wasm = statSync(join(outAbs, `${name}.wasm`)).size;
  updateIndex(outAbs, { name, mcu: board, source: basename(sketch), built: new Date().toISOString(), wasm });
  return { name, wasm };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const a = process.argv.slice(2), get = (k, d) => { const i = a.indexOf(k); return i < 0 ? d : a[i + 1]; };
  const all = (k) => a.flatMap((v, i) => (v === k ? [a[i + 1]] : []));
  if (get('--jobs')) await runJobs(get('--jobs'));
  else {
    const r = buildBoard({ name: get('--name'), board: get('--board'), sketch: get('--sketch'), out: get('--out'), libSrc: all('--libsrc'),
      libDeps: get('--libdeps', null), includes: all('--include') });
    console.log(`built ${r.name}: wasm ${r.wasm} bytes`);
  }
}
