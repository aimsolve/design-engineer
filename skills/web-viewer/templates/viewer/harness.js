// Headless smoke test of a BUILT viewer page: runs its scene script in node with WebGL/DOM stubs, then
//  - steps the cycle (play, slow off, op steps), fires tap / double tap / drag / pinch / wheel on the canvas and a hash change,
//  - samples every motion group over one cycle and reports jumps between neighbouring samples (teleports),
//  - prints parts without card text (from the page's console.warn).
// Exit 1 on any exception. Catches blank-canvas crashes, not visual errors: still LOOK at frames (shoot.py).
// Usage: node harness.js model.html [--hash "#t=1.0&hide=node/part"] [--jump 0.5]
const fs = require("fs"), vm = require("vm"), path = require("path");
const args = process.argv.slice(2);
const page = args.find((a, i) => !a.startsWith("--") && !["--hash", "--jump"].includes(args[i - 1]));
const opt = (n, d) => (args.includes(n) ? args[args.indexOf(n) + 1] : d);
if (!page) { console.error("usage: node harness.js model.html [--hash '#t=1'] [--jump 0.5]"); process.exit(2); }
const THREE = require(path.join(__dirname, "three.min.js"));
const html = fs.readFileSync(page, "utf8");
const code = html.slice(html.lastIndexOf("<script>") + 8, html.lastIndexOf("</script>"));

const W = 1200, H = 700, byId = {};
function el(id) {
  const cls = new Set();
  return { id, style: {}, textContent: "", innerHTML: "", hidden: false, checked: true, title: "", className: "",
    clientWidth: W, clientHeight: H, children: [], handlers: {},
    classList: { add: (c) => cls.add(c), remove: (c) => cls.delete(c), contains: (c) => cls.has(c),
      toggle: (c, on) => { on = on === undefined ? !cls.has(c) : on; on ? cls.add(c) : cls.delete(c); return on; } },
    addEventListener(t, fn) { (this.handlers[t] = this.handlers[t] || []).push(fn); },
    appendChild(c) { this.children.push(c); return c; },
    getBoundingClientRect: () => ({ left: 0, top: 0, width: W, height: H }), setPointerCapture() {} };
}
const document = { title: "", getElementById: (id) => (byId[id] = byId[id] || el(id)), createElement: (t) => el(t) };
const winHandlers = {};
const window = { devicePixelRatio: 2, innerWidth: W, innerHeight: H, localStorage: { getItem: () => null, setItem() {} },
  addEventListener: (t, fn) => (winHandlers[t] = winHandlers[t] || []).push(fn) };
let canvas = null, raf = [], now = 0;
THREE.WebGLRenderer = function () {
  canvas = this.domElement = el("canvas");
  this.setPixelRatio = () => {}; this.setSize = () => {}; this.render = (scene) => scene.updateMatrixWorld();
};
THREE.Clock.prototype.getDelta = () => 1 / 60;
const warns = [];
const cons = Object.assign({}, console, { warn: (...a) => warns.push(a.join(" ")) });
const location = { hash: opt("--hash", "") };
const sandbox = { THREE, document, window, location, console: cons, performance: { now: () => now }, Math, Object, Array,
  Float32Array, Uint16Array, Uint32Array, JSON, String, Number, isFinite, parseFloat, decodeURIComponent,
  requestAnimationFrame: (f) => raf.push(f) };
vm.createContext(sandbox);
try { vm.runInContext(code, sandbox); } catch (e) { console.error("SCENE THREW:", e.stack); process.exit(1); }

function spin(n) { for (let i = 0; i < n; i++) { now += 1000 / 60; const q = raf; raf = []; q.forEach((f) => f(now)); } }
const fire = (target, ev, props) => (target.handlers[ev] || []).forEach((fn) => fn(Object.assign({ target, preventDefault() {}, stopPropagation() {} }, props)));
const ptr = (type, id, x, y, extra) => fire(canvas, type, Object.assign({ type, pointerId: id, clientX: x, clientY: y, button: 0, pointerType: "touch" }, extra));
const V = window.__viewer;
const tgt = () => V.state().target.map((v) => v.toFixed(1)).join(",");
try {
  spin(5);
  console.log("built: frames", V.frames, "| cycle", V.cycle, "s | ops", byId.ops.children.length, "| state", JSON.stringify(V.state()));
  if (V.cycle) {
    fire(byId.slow, "change", { target: { checked: false } });
    spin(60 * 4);
    console.log("after ~4 s:", byId.meta.textContent);
    fire(byId.bRun, "click");
    for (let s = 0; s < 6; s++) { fire(byId.bStep, "click"); spin(120); console.log("step", s + 1, "->", byId.curOp.textContent, "|", byId.meta.textContent); }
  }
  // controls: tap centre (select), double tap (pivot), drag (orbit), right drag (pan), pinch, wheel
  const cx = W / 2, cy = H / 2;
  ptr("pointerdown", 1, cx, cy); ptr("pointerup", 1, cx, cy); spin(2);
  console.log("tap -> selected:", V.state().selected);
  now += 100; ptr("pointerdown", 1, cx, cy); ptr("pointerup", 1, cx, cy); spin(40);
  console.log("double tap -> target", tgt());
  const y0 = V.state().yaw;
  ptr("pointerdown", 1, cx, cy); ptr("pointermove", 1, cx + 80, cy); ptr("pointerup", 1, cx + 80, cy);
  console.log("drag -> yaw", y0.toFixed(3), "->", V.state().yaw.toFixed(3));
  const t0 = tgt();
  ptr("pointerdown", 1, cx, cy, { button: 2, pointerType: "mouse" }); ptr("pointermove", 1, cx + 60, cy + 30); ptr("pointerup", 1, cx + 60, cy + 30);
  console.log("right drag -> target", t0, "->", tgt());
  const d0 = V.state().dist;
  ptr("pointerdown", 1, cx - 50, cy); ptr("pointerdown", 2, cx + 50, cy);
  ptr("pointermove", 1, cx - 100, cy); ptr("pointermove", 2, cx + 100, cy); ptr("pointerup", 1, cx - 100, cy); ptr("pointerup", 2, cx + 100, cy);
  console.log("pinch -> dist", d0.toFixed(0), "->", V.state().dist.toFixed(0));
  fire(canvas, "wheel", { deltaY: 200 }); spin(2);
  location.hash = "#t=0.5&cam=-0.3,0.6,300,0,60,80"; (winHandlers.hashchange || []).forEach((f) => f()); spin(2);
  console.log("hashchange -> t", V.state().t.toFixed(2), "cam", V.state().dist, tgt());
  // teleport check: every group's world position sampled over one cycle at N and 2N steps. Fast but continuous motion
  // halves its per-sample step when N doubles; a teleport (a jump in pose(t)) does not.
  if (V.cycle) {
    const lim = parseFloat(opt("--jump", "0.5")), p = new THREE.Vector3();
    const worst = (N) => {
      const prev = {}, w = {};
      for (let i = 0; i <= N; i++) {
        const t = V.cycle * i / N; V.setTime(t);
        Object.keys(V.groups).forEach((g) => {
          V.groups[g].updateMatrixWorld(true); V.groups[g].getWorldPosition(p);
          if (prev[g]) { const d = p.distanceTo(prev[g]); if (d > (w[g] ? w[g][0] : 0)) w[g] = [d, t]; }
          prev[g] = p.clone();
        });
      }
      return w;
    };
    const a = worst(1000), b = worst(2000);
    const bad = Object.keys(b).filter((g) => b[g][0] > lim && b[g][0] > 0.75 * a[g][0]);
    console.log(bad.length ? "JUMPS (pose(t) discontinuity > " + lim + " mm): " +
      bad.map((g) => g + " " + b[g][0].toFixed(2) + " mm at t=" + b[g][1].toFixed(3)).join("; ") : "no group jumps > " + lim + " mm");
  }
  if (warns.length) console.log("page warnings:\n  " + warns.join("\n  "));
} catch (e) { console.error("FRAME THREW:", e.stack); process.exit(1); }
console.log("OK");
