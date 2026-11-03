// Floating windows of the viewer (app panel, sim board card, real-board windows): drag by the header, fold to a compact header,
// folded windows line up along the bottom-right edge. Position and fold are remembered per project (localStorage "win.<project>").
// The owner of a window keeps its own fold toggle (a class on the element); this module watches that class and only places the window.
const PID = location.pathname.split("/")[1] || "local";
const KEY = "win." + PID;
const GAP = 8, DOCK_W = 236;
const narrow = () => window.innerWidth <= 700;
const load = () => { try { return JSON.parse(localStorage.getItem(KEY) || "{}") || {}; } catch { return {}; } };
const save = (o) => { try { localStorage.setItem(KEY, JSON.stringify(o)); } catch {} };
const PROPS = ["left", "top", "right", "bottom", "width", "height", "max-height", "z-index"];
const wins = [];
let state = load(), zTop = 20;

const imp = (el, p, v) => el.style.setProperty(p, v, "important");
const clear = (el) => PROPS.forEach((p) => el.style.removeProperty(p));
const visible = (w) => !w.el.hidden && w.el.isConnected && getComputedStyle(w.el).display !== "none";
const folded = (w) => w.el.classList.contains(w.cls);
const dockBottom = () => (document.body.classList.contains("has-sim") ? 60 : 16);

function clamp(x, y, el) {
  const r = el.getBoundingClientRect();
  return [Math.max(GAP, Math.min(x, innerWidth - Math.min(r.width, innerWidth) - GAP)), Math.max(GAP, Math.min(y, innerHeight - 40))];
}

function layout() {
  document.documentElement.classList.toggle("wm-on", !narrow());
  if (narrow()) { wins.forEach((w) => { clear(w.el); w.el.classList.remove("wDocked", "wFree"); }); return; }
  const appW0 = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--app-w")) || 0;
  const right0 = innerWidth - 16 - appW0;   // left of an open app panel in its default place
  let right = right0, base = innerHeight - dockBottom(), dRowH = 0;
  const row = wins.filter((w) => visible(w) && folded(w)).sort((a, b) => a.order - b.order);
  for (const w of row) {                       // folded: a compact header in the bottom row, right to left
    if (!w.el.classList.contains("wDocked")) imp(w.el, "z-index", "40");   // the bottom row stays above open windows
    w.el.classList.add("wDocked"); w.el.classList.remove("wFree");
    imp(w.el, "width", DOCK_W + "px"); imp(w.el, "height", "auto"); imp(w.el, "right", "auto"); imp(w.el, "bottom", "auto"); imp(w.el, "max-height", "none");
    const h = w.el.offsetHeight;
    if (right - DOCK_W < 392 && right < right0) { right = right0; base -= dRowH + GAP; dRowH = 0; }   // no room: one more row above
    right -= DOCK_W; imp(w.el, "left", right + "px"); imp(w.el, "top", base - h + "px"); right -= GAP; dRowH = Math.max(dRowH, h);
  }
  // never-moved «flow» windows (real boards): a row right of the panel along the bottom, wrapping upwards
  const appW = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--app-w")) || 0;
  let fx = 392, fy = innerHeight - (document.body.classList.contains("has-sim") ? 64 : 16), rowH = 0;
  for (const w of wins) {
    if (!w.flow || !visible(w) || folded(w) || w.dragging || (state[w.id] && state[w.id].x != null)) continue;
    w.el.classList.remove("wDocked", "wFree");
    ["left", "top", "right", "bottom", "width", "height", "max-height"].forEach((p) => w.el.style.removeProperty(p));
    const ww = w.el.offsetWidth, hh = w.el.offsetHeight;
    if (!w.autoFolded && 392 + ww > innerWidth - 16 - appW) { w.autoFolded = true; w.el.classList.add(w.cls); continue; }   // no room beside the panels: fold into the bottom row
    if (fx + ww > innerWidth - 16 - appW && fx > 392) { fx = 392; fy -= rowH + GAP; rowH = 0; }
    imp(w.el, "left", fx + "px"); imp(w.el, "top", fy - hh + "px"); fx += ww + GAP; rowH = Math.max(rowH, hh);
  }
  for (const w of wins) {
    if (w.flow && !(state[w.id] && state[w.id].x != null)) continue;
    if (!visible(w) || folded(w) || w.dragging) continue;
    if (w.el.classList.contains("wDocked")) { w.el.classList.remove("wDocked"); imp(w.el, "z-index", String(++zTop)); }
    const s = state[w.id];
    if (!s || s.x == null) { clear(w.el); w.el.classList.remove("wFree"); continue; }   // never moved: the default place from the CSS
    w.el.classList.add("wFree");
    ["width", "height", "max-height"].forEach((p) => w.el.style.removeProperty(p));
    imp(w.el, "right", "auto"); imp(w.el, "bottom", "auto");
    const [x, y] = clamp(s.x, s.y, w.el); imp(w.el, "left", x + "px"); imp(w.el, "top", y + "px");
    imp(w.el, "max-height", innerHeight - y - GAP + "px");
    if (s.h) imp(w.el, "height", Math.min(s.h, innerHeight - y - GAP) + "px");
  }
  const app = wins.find((w) => w.id === "app");
  document.documentElement.classList.toggle("wm-appfree", !!(app && !folded(app) && state.app && state.app.x != null));
}

let queued = false;
const relayout = () => { if (queued) return; queued = true; requestAnimationFrame(() => { queued = false; layout(); }); };

export function register(el, { id, head, cls = "collapsed", keepHeight = false, flow = false } = {}) {
  if (!el || wins.some((w) => w.el === el)) return;
  const w = { el, id, head: head || el.firstElementChild, cls, keepHeight, flow, order: wins.length };
  wins.push(w);
  const s = state[id];
  if (s && typeof s.c === "boolean" && folded(w) !== s.c && w.head) w.head.click();   // the remembered fold, through the owner's own toggle
  new MutationObserver(() => {
    const c = folded(w);
    if ((state[id] || {}).c !== c) { state[id] = { ...(state[id] || {}), c }; save(state); }
    relayout();
  }).observe(el, { attributes: true, attributeFilter: ["class", "hidden"] });
  // drag by the header (a click without movement stays the owner's fold toggle)
  let drag = null, moved = false;
  w.head.addEventListener("pointerdown", (e) => {
    imp(el, "z-index", String(++zTop));
    if (narrow() || e.button !== 0 || folded(w) || e.target.closest("button, select, input, a")) return;
    const r = el.getBoundingClientRect();
    e.preventDefault();   // no text selection while dragging
    drag = { dx: e.clientX - r.left, dy: e.clientY - r.top, x0: e.clientX, y0: e.clientY, h: r.height }; moved = false;
  });
  window.addEventListener("pointermove", (e) => {
    if (!drag) return;
    if (!moved && Math.hypot(e.clientX - drag.x0, e.clientY - drag.y0) < 5) return;
    if (!moved) { moved = true; w.dragging = true; el.classList.add("wFree", "wDrag"); if (w.keepHeight) imp(el, "height", drag.h + "px"); imp(el, "right", "auto"); imp(el, "bottom", "auto"); }
    const [x, y] = clamp(e.clientX - drag.dx, e.clientY - drag.dy, el);
    imp(el, "left", x + "px"); imp(el, "top", y + "px");
  });
  window.addEventListener("pointerup", () => {
    if (!drag) return;
    if (moved) {
      const r = el.getBoundingClientRect();
      state[id] = { ...(state[id] || {}), x: Math.round(r.left), y: Math.round(r.top), ...(w.keepHeight ? { h: Math.round(r.height) } : {}) }; save(state);
      el.classList.remove("wDrag"); w.dragging = false;
      const eat = (ev) => { ev.stopPropagation(); ev.preventDefault(); };   // the drag is not a fold
      el.addEventListener("click", eat, { capture: true, once: true }); setTimeout(() => el.removeEventListener("click", eat, { capture: true }), 60);
      relayout();
    }
    drag = null;
  });
  // double click on the header: back to the default place
  w.head.addEventListener("dblclick", (e) => { if (e.target.closest("button, select, input, a")) return; state[id] = { c: folded(w) }; save(state); relayout(); });
  relayout();
}
export function forget(el) { const i = wins.findIndex((w) => w.el === el); if (i >= 0) wins.splice(i, 1); relayout(); }
window.addEventListener("resize", relayout);
window.__win = { register, forget, relayout, state: () => state };
