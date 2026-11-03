// Download kits of the assembly viewer: files for manufacturing, always from the current model.
// Loaded by index.html only when config.json has `kit` (build.py --kit auto); the router decides the rest:
//
//   GET /kit/<project>/status[?scope=<scope>] -> {enabled, fresh, building, failed, since, est_secs, parts: [ids with files], nodes: [names],
//        scopes: [print|laser|bought|step|assembly|electric|pneumatic], off: {scope: "none" (the model has none) | "setup" (not exported here)}}; with ?scope the state is that scope's own (each scope builds and ages on its own)
//   GET /kit/<project>/<scope> -> 200 the file | 202 {building, since} (the model changed: an export runs) | 4xx/5xx {error}
//        scope = print | laser | bought | step | assembly | electric | pneumatic | node/<name> | part/<id>
//
// Panel: all seven buttons always (assembly = "Assembly drawings", a zip of one PDF per node; electric / pneumatic = the machine's
// wiring and pneumatic diagrams, drawn by the router's schemes.py from the model's ports and
// mechanics/cad/connections.py: the button opens the drawing over the page, GET /kit/<project>/svg/<kind> (an <img>: zoom with the
// wheel or two fingers, drag to move), with "Download PDF" = GET /kit/<project>/<kind>); one the router does not offer (or every
// one, enabled: false) is greyed out, its reason on hover. Part card: "Download the part files"; node card: "Download the assembly archive".
// A 202 turns the pressed button into "The model is being updated, file in ~N s"; the page polls the status and downloads by itself when the
// build is fresh (never the old file). A link from the bot (`tg link`): #sel=<part>&dl=part downloads that part, #dl=<scope> a kit.
// "Open in Bambu Studio" (after "For 3D printing", and on a print part's card; not on phones): GET /kit/<project>/3mf/print | 3mf/part/<id> waits
// like a download, then answers {url, name}, a signed 3MF link that Bambu Studio fetches itself (it takes only .3mf, without our cookie);
// the page hands it over as bambustudioopen://<url&name=…> on a Mac, bambustudio://open?file=<…> elsewhere (Bambu Studio's own schemes).

const POLL_MS = 4000;

export function mountKit({ api, L, cfg }) {
  const K = L.kit;
  const base = cfg.kit === "auto" ? "/kit/" + location.pathname.split("/")[1] : String(cfg.kit).replace(/\/$/, "");
  const bambu = !/iPhone|iPad|iPod|Android/i.test(navigator.userAgent) && !(/Mac/.test(navigator.platform) && navigator.maxTouchPoints > 1);
  const css = document.createElement("style");
  css.textContent = `.kitSt{flex-basis:100%;font-size:12px;color:#c92a2a} .kitSt.note{color:var(--muted)} #kitRow button[data-scope^="3mf/"]::after{content:"↗" / ""} #kitCard{margin:6px 0}
#kitRow button:disabled,#kitCard button:disabled{opacity:.6;cursor:default}
#kitRow button[aria-disabled=true],#kitRow button[aria-disabled=true]:hover{opacity:.45;cursor:not-allowed;background:none;border-color:var(--hair)}
.kitProg{flex-basis:100%;height:4px;border-radius:2px;background:var(--line);overflow:hidden;margin-top:2px}
.kitProg i{display:block;height:100%;width:0;background:var(--acc);transition:width 1s linear}
.kitProg.unk i{width:30%;animation:kitSlide 1.6s ease-in-out infinite} @keyframes kitSlide{0%{margin-left:-30%}100%{margin-left:100%}}
.kitHint{flex-basis:100%;font-size:12px;color:var(--muted)}
#kitScheme{position:fixed;inset:0;z-index:70;display:flex;flex-direction:column;background:#fff;color:#111}
#kitScheme .ksBar{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:8px 12px;border-bottom:1px solid var(--line,#ddd)}
#kitScheme .ksBar b{font-size:15px;margin-right:auto} #kitScheme .ksHint{font-size:12px;color:var(--muted)}
#kitScheme .ksView{flex:1;position:relative;overflow:hidden;background:#fff;touch-action:none;cursor:grab}
#kitScheme .ksView img{position:absolute;left:0;top:0;transform-origin:0 0;max-width:none;user-select:none;-webkit-user-drag:none}
#kitToast{position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:60;max-width:min(560px,calc(100vw - 32px));padding:12px 16px;
border-radius:8px;background:var(--panel,#fff);color:var(--ink,#111);box-shadow:0 2px 12px rgba(0,0,0,.25);font-size:14px;line-height:1.4}
#kitToast b{display:block;margin-bottom:4px} #kitToast .kitProg{margin-top:8px}`;
  document.head.appendChild(css);
  let st = null, waiting = null, timer = null, toast = null;   // toast: the page was opened by a download link from the bot

  const row = document.createElement("div"); row.className = "row"; row.id = "kitRow"; row.hidden = true;
  const secDl = document.getElementById("secDl"), dlBody = secDl && secDl.querySelector(".sb");
  if (dlBody) dlBody.appendChild(row); else { const anchor = document.getElementById("linkRow") || document.getElementById("colRow"); if (anchor) anchor.after(row); }
  const card = document.createElement("div"); card.className = "row"; card.id = "kitCard"; card.hidden = true;
  const cbtn = document.createElement("button"); card.appendChild(cbtn);
  const cbam = document.createElement("button"); cbam.textContent = K.bambu; cbam.hidden = true; card.appendChild(cbam);
  const cst = document.createElement("div"); cst.className = "sub kitSt"; cst.hidden = true; card.appendChild(cst);
  const rowDl = document.getElementById("rowDl"), infoBody = document.querySelector("#info .body");
  if (rowDl) rowDl.after(card); else if (infoBody) infoBody.prepend(card);

  const rst = document.createElement("div"); rst.className = "kitSt"; rst.hidden = true;
  let shownScopes = "";
  function panel() {                       // rebuilt whenever the router's scopes change (a new process after a rebuild)
    const all = Object.keys(K.scopes);
    const scopes = !st.enabled ? [] : st.scopes || all;     // an older router without `scopes`: all of them, a missing one answers 404
    const off = (s) => scopes.includes(s) ? "" : !st.enabled ? K.off.project : (st.off || {})[s] === "none" ? K.off[s] : K.off.setup;
    const noStl = Array.isArray(st.print_parts) && !st.print_parts.length;   // exported, and no print part has an STL
    const key = JSON.stringify([...all.map(off), noStl]);
    if (key === shownScopes) return;
    shownScopes = key;
    row.innerHTML = "";
    const lbl = document.createElement("span"); lbl.className = "sub"; lbl.textContent = K.title; row.appendChild(lbl);
    for (const s of all) {
      const b = document.createElement("button"); b.textContent = K.scopes[s]; b.dataset.scope = s;
      const why = off(s);
      if (why) { b.setAttribute("aria-disabled", "true"); b.title = why; }   // not `disabled`: the hover still shows the reason
      else b.addEventListener("click", () => get(/^(electric|pneumatic)$/.test(s) ? "svg/" + s : s, b, rst));
      row.appendChild(b);
      if (s === "print" && bambu) {
        const o = document.createElement("button"); o.textContent = K.bambu; o.dataset.scope = "3mf/print";
        const owhy = why || (noStl ? K.bambuNone : "");
        if (owhy) { o.setAttribute("aria-disabled", "true"); o.title = owhy; }
        else o.addEventListener("click", () => get("3mf/print", o, rst));
        row.appendChild(o);
      }
    }
    row.appendChild(rst);
    row.hidden = false;
    if (secDl) secDl.hidden = false;
    lock();
  }
  const allButtons = () => [...row.querySelectorAll("button:not([aria-disabled])"), ...(card.hidden ? [] : [cbtn, ...(cbam.hidden ? [] : [cbam])])];
  function lock() {                        // one wait at a time: the others are greyed out while a button waits
    for (const b of allButtons()) if (!waiting || b !== waiting.btn) b.disabled = !!waiting || b.dataset.busy === "1";
  }
  function cardSync() {
    const s = api.selInfo && api.selInfo();
    let scope = null;
    const pid = s && (s.kit || s.part);   // a repeat placement of a part file's part downloads that part (catalogue part_id)
    if (pid && st && (st.parts || []).includes(pid)) scope = "part/" + pid;
    else if (s && !s.part && s.key && st && (st.nodes || []).includes(s.key)) scope = "node/" + s.key;
    card.hidden = !scope;
    if (!scope) return;
    if (cbtn.dataset.scope !== scope) {
      cbtn.dataset.scope = scope; cst.hidden = true;
      if (waiting && (waiting.btn === cbtn || waiting.btn === cbam)) stopWait();
    }
    cbam.hidden = !bambu || !scope.startsWith("part/") || !(st.print_parts || []).includes(scope.slice(5));
    cbam.dataset.scope = "3mf/" + scope;
    if (!(waiting && waiting.btn === cbtn) && cbtn.dataset.busy !== "1") cbtn.textContent = scope.startsWith("part/") ? K.part : K.node;
    lock();
  }
  cbtn.addEventListener("click", () => get(cbtn.dataset.scope, cbtn, cst));
  cbam.addEventListener("click", () => get(cbam.dataset.scope, cbam, cst));
  api.selectHooks.push(cardSync);

  const mmss = (s) => Math.floor(s / 60) + ":" + String(Math.floor(s % 60)).padStart(2, "0");
  const left = (s) => s < 90 ? Math.max(5, Math.round(s / 5) * 5) + " " + K.sec : Math.round(s / 60) + " " + K.min;
  function stopWait() {
    if (!waiting) return;
    clearInterval(timer); timer = null;
    const w = waiting; waiting = null;
    w.btn.textContent = w.label; w.btn.title = "";
    w.prog.remove(); w.hint.remove();
    lock();
  }
  // the wait: "In the queue, ahead of you: N" while the worker is busy, then "Building… m:ss of ~N min" with a bar from the last build's duration
  function show(w) {
    const t0 = (w.started || w.since) * 1000, el = Math.max(0, (Date.now() - t0) / 1000);
    if (w.pos > 0) {
      w.btn.textContent = K.queued + " " + w.pos; w.prog.className = "kitProg unk";
      if (toast && toast.scope === w.scope) say(K.linkWait + K.queued + " " + w.pos + ".", null);
      return;
    }
    w.btn.textContent = K.building + (w.est ? ", " + K.soon + left(Math.max(0, w.est - el)) : "… " + mmss(el));   // no estimate yet: the time it runs
    if (toast && toast.scope === w.scope) say(K.linkWait + (w.est ? K.linkIn + left(Math.max(0, w.est - el)) + "." : K.linkSoon), w.est ? Math.min(95, (el / w.est) * 100) : null);
    w.prog.className = w.est ? "kitProg" : "kitProg unk";
    if (w.est) w.prog.firstChild.style.width = Math.min(95, (el / w.est) * 100) + "%";
  }
  function track(w, j) {
    if (j.since) w.since = j.since;
    w.started = j.started || null; w.pos = j.queue_pos || 0; w.est = j.est_secs || null;
  }
  function fail(out, text) { stopWait(); out.textContent = text; out.classList.remove("note"); out.hidden = false; }
  function openBambu(j) {               // Bambu Studio downloads the link itself; it asks whether to open a file from this site
    const target = encodeURIComponent(j.url + "&name=" + j.name);
    const href = (/Mac/.test(navigator.platform || navigator.userAgent) ? "bambustudioopen://" : "bambustudio://open?file=") + target;
    location.href = href;
  }

  async function get(scope, btn, out, label = btn.textContent) {
    if (waiting) {                       // the waiting button pressed again = stop waiting (the build itself goes on on the server)
      if (waiting.btn === btn) { const o = waiting.out; stopWait(); o.textContent = K.cancelled; o.hidden = false; }
      return;
    }
    if (btn.dataset.busy === "1") return;
    out.hidden = true; out.classList.remove("note");
    btn.dataset.busy = "1"; btn.disabled = true;
    let r;
    try { r = await fetch(base + "/" + scope.split("/").map(encodeURIComponent).join("/"), { credentials: "same-origin" }); }
    catch (e) { r = null; }
    if (r && r.status === 200 && scope.startsWith("3mf/")) {
      const j = await r.json().catch(() => null);
      btn.textContent = label; btn.dataset.busy = ""; btn.disabled = false; lock();
      if (!j || !j.url) return fail(out, K.failed);
      openBambu(j);
      out.textContent = K.bambuHint; out.classList.add("note"); out.hidden = false;
      return;
    }
    if (r && r.status === 200 && scope.startsWith("svg/")) {    // a scheme: shown over the page, not saved
      const blob = await r.blob().catch(() => null);
      btn.textContent = label; btn.dataset.busy = ""; btn.disabled = false; lock();
      if (!blob) return fail(out, K.failed);
      return openScheme(scope.slice(4), blob);
    }
    if (r && r.status === 200) {
      btn.textContent = K.downloading;
      try { await save(r); if (toast && toast.scope === scope) say(K.linkDone, 100, 6000); }
      catch (e) { out.textContent = K.failed; out.hidden = false; if (toast && toast.scope === scope) say(K.failed, null, 10000); }
      btn.textContent = label; btn.dataset.busy = ""; btn.disabled = false; return lock();
    }
    btn.dataset.busy = "";
    if (r && r.status === 202) {
      const j = await r.json().catch(() => ({}));
      const prog = document.createElement("div"); prog.className = "kitProg"; prog.appendChild(document.createElement("i"));
      const hint = document.createElement("div"); hint.className = "kitHint"; hint.textContent = K.hint;
      btn.parentNode.insertBefore(hint, out); btn.parentNode.insertBefore(prog, hint);
      waiting = { scope, btn, out, label, n: 0, misses: 0, prog, hint, since: Date.now() / 1000 };
      track(waiting, j);
      btn.disabled = false; btn.title = K.cancelHint; lock();
      show(waiting);
      timer = setInterval(async () => {
        if (!waiting) return;
        show(waiting);
        if (waiting.n++ % (POLL_MS / 1000)) return;
        const s = await status(waiting.scope);
        if (!waiting) return;
        const w = waiting;
        if (!s) { if (++w.misses >= 3) fail(out, K.failed); return; }
        w.misses = 0;
        if (s.building) track(w, s);
        if (s.fresh || s.failed || !s.building) {   // ready / failed (the click shows the server's words) / stale again: ask once more
          stopWait();
          get(w.scope, w.btn, w.out, w.label);
        }
      }, 1000);
      return;
    }
    const j = r ? await r.json().catch(() => null) : null;
    btn.textContent = label; btn.disabled = false; lock();
    fail(out, (j && j.error) || K.failed);
    if (toast && toast.scope === scope) say((j && j.error) || K.failed, null, 10000);
  }
  async function save(r) {
    const cd = r.headers.get("Content-Disposition") || "";
    const m = /filename\*=UTF-8''([^;]+)/i.exec(cd) || /filename="([^"]+)"/i.exec(cd);
    const name = m ? decodeURIComponent(m[1]) : "files.zip";
    const url = URL.createObjectURL(await r.blob());
    const a = document.createElement("a"); a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }
  // the scheme drawing over the page: an <img> (never inline SVG), moved and zoomed by a CSS transform
  let scheme = null;
  function closeScheme() {
    if (!scheme) return;
    if (waiting && scheme.el.contains(waiting.btn)) stopWait();   // its "Download PDF" was waiting: never a wait without a button
    scheme.el.remove(); URL.revokeObjectURL(scheme.url); document.removeEventListener("keydown", scheme.key, true);
    const back = scheme.from; scheme = null;
    if (back && back.isConnected) back.focus();
  }
  function openScheme(kind, blob) {
    closeScheme();
    const url = URL.createObjectURL(new Blob([blob], { type: "image/svg+xml" }));
    const from = document.activeElement;
    const el = document.createElement("div"); el.id = "kitScheme"; el.setAttribute("role", "dialog"); el.setAttribute("aria-modal", "true");
    el.setAttribute("aria-label", K.scopes[kind]);
    const bar = document.createElement("div"); bar.className = "ksBar";
    const t = document.createElement("b"); t.textContent = K.scopes[kind];
    const hint = document.createElement("span"); hint.className = "ksHint"; hint.textContent = K.zoom;
    const pdf = document.createElement("button"); pdf.textContent = K.pdf;
    const close = document.createElement("button"); close.textContent = "✕ " + K.close; close.setAttribute("aria-label", K.close);
    const note = document.createElement("div"); note.className = "kitSt"; note.hidden = true;
    bar.append(t, hint, pdf, close, note);
    const view = document.createElement("div"); view.className = "ksView";
    const img = document.createElement("img"); img.alt = K.scopes[kind]; img.draggable = false; img.src = url;
    view.appendChild(img); el.append(bar, view); document.body.appendChild(el);
    let x = 0, y = 0, k = 1, fit = 1;
    const put = () => { img.style.transform = `translate(${x}px,${y}px) scale(${k})`; };
    const zoomAt = (px, py, f) => {
      const k2 = Math.min(Math.max(k * f, fit * 0.5), fit * 40);
      x = px - (px - x) * (k2 / k); y = py - (py - y) * (k2 / k); k = k2; put();
    };
    img.addEventListener("load", () => {       // the first sheet across the screen
      const w = img.naturalWidth || 1000, r = view.getBoundingClientRect();
      fit = k = r.width / w; x = 0; y = 0; put();
    });
    view.addEventListener("wheel", (e) => {
      e.preventDefault();
      const r = view.getBoundingClientRect();
      zoomAt(e.clientX - r.left, e.clientY - r.top, Math.exp(-e.deltaY * 0.0015));
    }, { passive: false });
    view.addEventListener("dblclick", (e) => { const r = view.getBoundingClientRect(); zoomAt(e.clientX - r.left, e.clientY - r.top, 2); });
    const pts = new Map();
    let pinch = null;
    view.addEventListener("pointerdown", (e) => { view.setPointerCapture(e.pointerId); pts.set(e.pointerId, [e.clientX, e.clientY]); pinch = null; });
    view.addEventListener("pointermove", (e) => {
      if (!pts.has(e.pointerId)) return;
      const [px, py] = pts.get(e.pointerId);
      if (pts.size === 1) { x += e.clientX - px; y += e.clientY - py; put(); }
      pts.set(e.pointerId, [e.clientX, e.clientY]);
      if (pts.size === 2) {
        const [[ax, ay], [bx, by]] = [...pts.values()], d = Math.hypot(ax - bx, ay - by), r = view.getBoundingClientRect();
        if (pinch) zoomAt((ax + bx) / 2 - r.left, (ay + by) / 2 - r.top, d / pinch);
        pinch = d;
      }
    });
    const up = (e) => { pts.delete(e.pointerId); pinch = null; };
    view.addEventListener("pointerup", up); view.addEventListener("pointercancel", up);
    const key = (e) => {                  // the page's own keys (Escape = measure off) stay quiet under the drawing
      if (e.key === "Escape") { e.stopPropagation(); closeScheme(); return; }
      if (e.key === "Tab") { e.preventDefault(); (document.activeElement === pdf ? close : pdf).focus(); return; }
      if (!el.contains(e.target)) e.stopPropagation();
    };
    document.addEventListener("keydown", key, true);
    close.addEventListener("click", closeScheme);
    pdf.addEventListener("click", () => get(kind, pdf, note));
    scheme = { el, url, key, from };
    close.focus();
  }

  async function status(scope) {
    try {
      const r = await fetch(base + "/status" + (scope ? "?scope=" + encodeURIComponent(scope) : ""), { credentials: "same-origin" });
      if (!r.ok) return null;
      const s = await r.json();
      if (s) { st = s; panel(); cardSync(); }
      return s;
    } catch (e) { return null; }
  }

  // the notice of a link download: what is being fetched, that it starts by itself and when (a bare "updating" says nothing)
  function say(text, pct, closeIn) {
    if (!toast) return;
    toast.el.querySelector("span").textContent = text;
    const bar = toast.el.querySelector(".kitProg");
    bar.className = pct == null ? "kitProg unk" : "kitProg"; if (pct != null) bar.firstChild.style.width = pct + "%";
    bar.hidden = !!closeIn;
    if (closeIn) { const t = toast; toast = null; setTimeout(() => t.el.remove(), closeIn); }
  }
  function notice(scope, name) {
    const el = document.createElement("div"); el.id = "kitToast"; el.setAttribute("role", "status");
    const b = document.createElement("b"); b.textContent = name;
    const bar = document.createElement("div"); bar.className = "kitProg unk"; bar.appendChild(document.createElement("i"));
    el.append(b, document.createElement("span"), bar); document.body.appendChild(el);
    const mb = document.getElementById("modelBanner"); if (mb) mb.hidden = true;   // one notice, not two
    toast = { scope, el };
    say(K.linkStart, null);
  }
  // a link from the bot: start the download it names once, then drop dl from the hash (a reload must not download again)
  async function fromLink() {
    const q = {};
    (api.hash0 || location.hash || "").slice(1).split("&").forEach((kv) => { const i = kv.indexOf("="); if (i > 0) q[kv.slice(0, i)] = decodeURIComponent(kv.slice(i + 1)); });
    api.hash0 = "";
    if (!q.dl) return;
    try { history.replaceState(null, "", location.hash.replace(/&?dl=[^&]*/, "").replace(/^#&/, "#") || location.pathname); } catch (e) { /* sandbox */ }
    if (q.dl === "part") {
      notice("part/" + q.sel, K.linkPart + " " + q.sel);    // at once: the model may take a while to load
      while (!api.ready && !api.error) await new Promise((r) => setTimeout(r, 100));
      cardSync();
      if (card.hidden || !cbtn.dataset.scope) return say(K.failed, null, 10000);
      toast.scope = cbtn.dataset.scope;
      toast.el.querySelector("b").textContent = K.linkPart + " " + ((api.selInfo() || {}).name || q.sel);
      return get(cbtn.dataset.scope, cbtn, cst);
    }
    if (!K.scopes[q.dl]) return;
    const b = row.querySelector(`button[data-scope="${q.dl}"]:not([aria-disabled])`);
    if (b) { notice(q.dl, K.linkKit + " «" + K.scopes[q.dl] + "»"); get(q.dl, b, rst); }
  }

  api.kit = { status, state: () => ({ st, waiting: waiting && waiting.scope }) };
  return status().then(() => { fromLink(); return api.kit; });
}
