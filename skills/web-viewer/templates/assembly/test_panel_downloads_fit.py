#!/usr/bin/env python3
"""Real-browser check: with every left-panel section open, the download buttons all fit on screen (bug: #secDl squeezed to ~46 px,
only the first download button visible, the other three hidden inside a tiny scrolling box). Sibling of test_panel_layout.py, same style.

Builds a fresh site with THIS template (build.py --sim <sim> --flash), serves it under /<project>/ like the stack does, and fakes the
router: GET /kit/<project>/status answers {enabled: true, scopes: [print, laser, bought, step]}, /hw/** answers 404. No real boards are
connected. It needs a machine with a sim (boards): --sim, else $VIEWER_TEST_SIM, and its importer output: --in, else $VIEWER_TEST_MODEL;
the demo machine has no sim, so without them the test skips saying so.

  env -u VIRTUAL_ENV uv run --isolated --no-project --with playwright python test_panel_downloads_fit.py \
      [--in <importer output>] [--sim <machine sim dir>] [--build <build.py to test>] [--swiftshader] [--shots <dir>]
(first run: `... --with playwright playwright install chromium`)

UI the test relies on (the spec): #panel with foldable sections headed (i18n.mjs) sec.view, sim.machine, sec.hw, sec.dl, sec.parts;
the downloads section is #secDl with buttons named exactly kit.scopes.print / laser / bought / step; the parts tree is #tree; the
simulation section is #vmSec (rows #vmBoards .brd); the panel's scrolling body is `#panel .body`, class `tight` only when the open
sections do not fit. The page is in its default language (ru); every text comes from i18n.mjs. Narrow phone board windows are covered
by test_panel_layout.py [6] (run it too).
Check names start with the done-when number: [1] every download button fully visible, [3] the simulation section (#vmSec) open shows
heading + first row, folded collapses to <= 40 px with no inline min-height, [4] phone 390x844: #vmSec / #secDl not squeezed, [2] the
tree gives way (the panel may scroll as a whole where the open sections cannot fit, the tree stays reachable).
LOOK at every PNG. Exit code 1 on any failed check.
"""
import argparse, functools, json, re, subprocess, sys, tempfile, threading, urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from viewer_stack_server import model_dir, serve_stack, sim_dir, ui_texts  # noqa: E402
ap = argparse.ArgumentParser()
ap.add_argument("--in", dest="src", default=None, help="importer output dir (model.glb, parts.json, parts/, fw_publish); default: $VIEWER_TEST_MODEL")
ap.add_argument("--sim", default=None, help="machine sim dir with site.json; default: $VIEWER_TEST_SIM")
ap.add_argument("--build", default=str(HERE / "build.py"), help="build.py to test (default: this template)")
ap.add_argument("--project", default="demo-machine", help="URL segment the site is served under (/kit/<project>/...)")
ap.add_argument("--swiftshader", action="store_true")
ap.add_argument("--shots", default=None)
a = ap.parse_args()
a.sim = str(sim_dir(a.sim, needs="a machine sim dir with boards (site.json), for the simulation and boards sections"))
a.src = str(model_dir(a.src, needs="the importer output of the machine whose sim the test runs"))
shots = Path(a.shots) if a.shots else Path(tempfile.mkdtemp(prefix="dlfit_shots_")); shots.mkdir(parents=True, exist_ok=True); print("screenshots:", shots)
GPU = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] if a.swiftshader else ["--use-angle=metal", "--enable-gpu", "--ignore-gpu-blocklist"]
rows, ok_all = [], True

T = ui_texts("ru")
SECTIONS = [T["sec"]["view"], T["sim"]["machine"], T["sec"]["hw"], T["sec"]["dl"], T["sec"]["parts"]]
VIEW_SEC, SIM_SEC, HW_SEC, DL_SEC, PARTS_SEC = SECTIONS
DL_BUTTONS = [T["kit"]["scopes"][k] for k in ("print", "laser", "bought", "step")]
VIEWPORTS = [(1500, 950), (1280, 800)]
TREE_MIN_FALLBACK = 100   # px; used when #tree declares no min-height of its own
NO_PANEL_SCROLL = set()   # revised spec: with sim + boards + a 3-line legend a machine page can be taller than a 950 px screen too,
                          # so the panel may scroll as a whole at both sizes as long as the tree is reachable

# #tree's visible height = its rect cut by every clipping ancestor and the viewport; `chain` = boxes between #tree and #panel that clip it,
# `scrollable` = how many of those can scroll (overflow auto/scroll) when their content overflows, i.e. clipping that the user can scroll past.
TREE_JS = r"""() => { const t = document.getElementById('tree'), p = document.getElementById('panel'); if (!t) return {visH: 0, chain: [], scrollable: 0};
  const r = t.getBoundingClientRect(); let top = Math.max(r.top, 0), bot = Math.min(r.bottom, innerHeight); const chain = []; let scrollable = 0, inPanel = true;
  for (let e = t.parentElement; e && e !== document.documentElement; e = e.parentElement) {
    const s = getComputedStyle(e);
    if (s.overflowY !== 'visible') { const q = e.getBoundingClientRect(); top = Math.max(top, q.top); bot = Math.min(bot, q.bottom);
      if (inPanel && e.scrollHeight > e.clientHeight + 1) { chain.push((e.id ? '#' + e.id : e.tagName.toLowerCase()) + ' ' + s.overflowY + ' ' + e.scrollHeight + '>' + e.clientHeight + ' top=' + Math.round(e.scrollTop));
        if (/(auto|scroll)/.test(s.overflowY)) scrollable++; } }
    if (e === p) inPanel = false; }
  return {visH: Math.round(Math.max(0, bot - top)), treeTop: Math.round(r.top), treeBottom: Math.round(r.bottom), chain, scrollable}; }"""


def check(name, ok, detail=""):
    global ok_all
    ok_all &= bool(ok)
    rows.append((name, "PASS" if ok else "FAIL", detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)
    return ok


# ---------------------------------------------------------------- site: build with the template under test, serve under /<project>/
root = Path(tempfile.mkdtemp(prefix="dlfit_site_"))
import atexit, shutil
atexit.register(shutil.rmtree, root, True)   # the built site is ~365 MB: never leave it behind
bld = subprocess.run([sys.executable, a.build, "--in", a.src, "--out", str(root / a.project), "--sim", a.sim, "--flash"], capture_output=True, text=True)
if bld.returncode:
    sys.exit("build.py failed:\n" + (bld.stderr or bld.stdout)[-3000:])
srv, _ = serve_stack(root)   # the stack's rules: /_viewer/ = this template, /<id>/ = the site with the shared index.html
URL = f"http://127.0.0.1:{srv.server_port}/{a.project}/?app=0"
print("site:", root / a.project, "served at", URL)


def install_routes(ctx, kit_calls):
    def kit(route):
        path = urllib.parse.urlparse(route.request.url).path
        kit_calls.append(path)
        if path.rstrip("/").endswith("/status"):
            body = {"enabled": True, "fresh": True, "building": False, "since": None, "parts": [], "nodes": [], "scopes": ["print", "laser", "bought", "step"]}
            return route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
        route.fulfill(status=404, content_type="application/json", body='{"error": "no file in this test"}')
    ctx.route(re.compile(r"^https?://[^/]+/kit/.*"), kit)
    ctx.route(re.compile(r"^https?://[^/]+/hw/.*"), lambda r: r.fulfill(status=404, content_type="application/json", body='{"error": "no router"}'))


# ---------------------------------------------------------------- panel sections (same lookup as test_panel_layout.py)
FIND_HEAD_JS = r"""([label, mark]) => {
  const panel = document.getElementById('panel'); if (!panel) return null;
  const re = new RegExp('^\\s*' + label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '(?!\\p{L})', 'u');
  const own = el => Array.from(el.childNodes).filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim();
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden'; };
  for (const el of panel.querySelectorAll('*')) {
    if (!vis(el)) continue;
    if (re.test(own(el)) || (el.children.length === 0 && re.test(el.textContent))) {
      document.querySelectorAll('[data-test-head="' + mark + '"]').forEach(x => x.removeAttribute('data-test-head'));
      const h = el.closest('button, summary, [role=button], h1, h2, h3, h4, h5, h6, [class*=head], [class*=Head]') || el;
      h.setAttribute('data-test-head', mark); return true;
    }
  }
  return null; }"""

BODY = {
    VIEW_SEC: "#bColMat",
    SIM_SEC: "#vmBoards",
    HW_SEC: f"#panel :is(button, [role=button], a):has-text({json.dumps(T['hw']['connect'], ensure_ascii=False)})",
    DL_SEC: f"#panel :is(button, [role=button], a):has-text({json.dumps(DL_BUTTONS[0], ensure_ascii=False)})",
    PARTS_SEC: "#tree",
}


def head(page, label):
    mark = "h" + str(SECTIONS.index(label))
    return page.locator(f'[data-test-head="{mark}"]') if page.evaluate(FIND_HEAD_JS, [label, mark]) else None


def body_open(page, label):
    """Open = the section's body is rendered (display/visibility), regardless of whether it is clipped: clipping is what we measure."""
    loc = page.locator(BODY[label]).first
    return loc.count() > 0 and loc.evaluate("e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== 'hidden'; }")


def open_section(page, label):
    h = head(page, label)
    if not h:
        return False
    if not body_open(page, label):
        h.click(); page.wait_for_timeout(350)
    return body_open(page, label)


EXPAND_TREE_JS = r"""() => {   // unfold every collapsed node of the parts tree (the parts tree expanded, as in the bug report)
  const t = document.getElementById('tree'); if (!t) return 0; let n = 0;
  for (let pass = 0; pass < 6; pass++) {
    const closed = Array.from(t.querySelectorAll('[aria-expanded="false"], details:not([open]) > summary'));
    if (!closed.length) break;
    closed.forEach(e => { if (e.tagName === 'SUMMARY') e.parentElement.open = true; else e.click(); n++; });
  }
  return n; }"""

# Visible part of an element: its rect cut by every clipping ancestor (overflow != visible) up to the viewport.
GEOM_JS = r"""(labels) => {
  const R = r => ({x: Math.round(r.left), y: Math.round(r.top), r: Math.round(r.right), b: Math.round(r.bottom), h: Math.round(r.bottom - r.top)});
  const clipOf = (el) => { let c = {left: 0, top: 0, right: innerWidth, bottom: innerHeight}, chain = [];
    for (let e = el.parentElement; e && e !== document.documentElement; e = e.parentElement) {
      const s = getComputedStyle(e);
      if (s.overflowX !== 'visible' || s.overflowY !== 'visible') { const r = e.getBoundingClientRect();
        c = {left: Math.max(c.left, r.left), top: Math.max(c.top, r.top), right: Math.min(c.right, r.right), bottom: Math.min(c.bottom, r.bottom)};
        chain.push((e.id ? '#' + e.id : e.tagName.toLowerCase() + (e.className && typeof e.className === 'string' ? '.' + e.className.trim().split(/\s+/).join('.') : '')) + ' ' + Math.round(r.height) + 'px'); } }
    return {c, chain}; };
  const panel = document.getElementById('panel'), sec = document.getElementById('secDl'), tree = document.getElementById('tree');
  const pr = panel && panel.getBoundingClientRect();
  const btns = {};
  for (const l of labels) {
    const b = Array.from((panel || document).querySelectorAll('button, [role=button], a')).find(x => x.textContent.trim() === l);
    if (!b) { btns[l] = null; continue; }
    const r = b.getBoundingClientRect(), {c, chain} = clipOf(b);
    const inside = r.width > 0 && r.height > 0 && r.left >= c.left - 1 && r.right <= c.right + 1 && r.top >= c.top - 1 && r.bottom <= c.bottom + 1;
    const inPanel = !!pr && r.top >= pr.top - 1 && r.bottom <= Math.min(pr.bottom, innerHeight) + 1;
    const inSec = !!sec && (() => { const s = sec.getBoundingClientRect(); return r.top >= s.top - 1 && r.bottom <= s.bottom + 1; })();
    btns[l] = {rect: R(r), clip: R(c), clippers: chain, fully: inside && inPanel && inSec};
  }
  const scrolling = (root) => { const out = []; if (root) for (const e of [root, ...root.querySelectorAll('*')]) {
    const oy = getComputedStyle(e).overflowY;
    if (/(auto|scroll)/.test(oy) && e.scrollHeight > e.clientHeight + 1) out.push((e.id ? '#' + e.id : e.tagName.toLowerCase() + '.' + String(e.className).trim().split(/\s+/)[0]) + ' ' + e.scrollHeight + '>' + e.clientHeight);
  } return out; };
  const panelScrolls = [];   // #panel itself or a box between #panel and #tree scrolling = the panel scrolls as a whole
  if (panel && tree) for (let e = tree.parentElement; e; e = e.parentElement) { const oy = getComputedStyle(e).overflowY;
    if (/(auto|scroll)/.test(oy) && e.scrollHeight > e.clientHeight + 1) panelScrolls.push((e.id ? '#' + e.id : e.tagName.toLowerCase() + '.' + String(e.className).trim().split(/\s+/)[0]) + ' ' + e.scrollHeight + '>' + e.clientHeight); if (e === panel) break; }
  const ts = tree && getComputedStyle(tree), tr = tree && tree.getBoundingClientRect();
  return {
    panel: pr && {...R(pr), scroll: panel.scrollHeight, client: panel.clientHeight},
    secDl: sec && {...R(sec.getBoundingClientRect()), scroll: sec.scrollHeight, client: sec.clientHeight},
    btns, dlScrolls: scrolling(sec), panelScrolls,
    tree: tree && {...R(tr), visH: Math.round(Math.max(0, Math.min(tr.bottom, pr ? pr.bottom : innerHeight, innerHeight) - Math.max(tr.top, pr ? pr.top : 0))),
                   overflowY: ts.overflowY, minH: parseFloat(ts.minHeight) || 0, scroll: tree.scrollHeight, client: tree.clientHeight},
  }; }"""


def open_page(p, w, h, mobile=False):
    b = p.chromium.launch(args=GPU)
    ctx = b.new_context(viewport={"width": w, "height": h}, device_scale_factor=1, is_mobile=mobile, has_touch=mobile)
    kit_calls = []
    install_routes(ctx, kit_calls)
    ctx.set_default_timeout(8000); ctx.set_default_navigation_timeout(120000)
    page = ctx.new_page()
    page.goto(URL, wait_until="load")
    page.wait_for_function("window.__viewer && (window.__viewer.ready || window.__viewer.error)", timeout=120000)
    try:
        page.wait_for_function("window.__viewer.sim || window.__viewer.simError", timeout=60000)
    except Exception:  # noqa
        pass
    try:
        page.wait_for_selector("#secDl", state="attached", timeout=15000)
    except Exception:  # noqa
        pass
    page.wait_for_timeout(800)
    return b, page, kit_calls


def fold_section(page, label):
    h = head(page, label)
    if not h:
        return False
    if body_open(page, label):
        h.click(); page.wait_for_timeout(350)
    return not body_open(page, label)


# Is `sel` (inside `within`) fully visible: its rect inside every clipping ancestor, the viewport and the `within` box?
FULLY_JS = r"""([el, within]) => {
  const R = r => ({x: Math.round(r.left), y: Math.round(r.top), r: Math.round(r.right), b: Math.round(r.bottom), h: Math.round(r.bottom - r.top)});
  if (!el) return {fully: false, why: 'missing'};
  const r = el.getBoundingClientRect(); let c = {left: 0, top: 0, right: innerWidth, bottom: innerHeight}; const chain = [];
  for (let e = el.parentElement; e && e !== document.documentElement; e = e.parentElement) { const s = getComputedStyle(e);
    if (s.overflowX !== 'visible' || s.overflowY !== 'visible') { const q = e.getBoundingClientRect();
      c = {left: Math.max(c.left, q.left), top: Math.max(c.top, q.top), right: Math.min(c.right, q.right), bottom: Math.min(c.bottom, q.bottom)};
      chain.push((e.id ? '#' + e.id : e.tagName.toLowerCase() + '.' + String(e.className).trim().split(/\s+/)[0]) + ' ' + Math.round(q.height) + 'px'); } }
  const w = within && within.getBoundingClientRect();
  const fully = r.width > 0 && r.height > 0 && r.left >= c.left - 1 && r.right <= c.right + 1 && r.top >= c.top - 1 && r.bottom <= c.bottom + 1
    && (!w || (r.top >= w.top - 1 && r.bottom <= w.bottom + 1));
  return {fully, rect: R(r), visible: R(c), within: w && R(w), clippedBy: chain}; }"""

# Boxes in `root` (itself included) whose content is cut: overflow not visible and scrollHeight > clientHeight.
SQUEEZED_JS = r"""(root) => { const out = []; if (!root) return ['missing'];
  for (const e of [root, ...root.querySelectorAll('*')]) { const s = getComputedStyle(e);
    if (s.overflowY !== 'visible' && e.scrollHeight > e.clientHeight + 1 && e.clientHeight > 0)
      out.push((e.id ? '#' + e.id : e.tagName.toLowerCase() + '.' + String(e.className).trim().split(/\s+/)[0]) + ' ' + s.overflowY + ' ' + e.scrollHeight + '>' + e.clientHeight); }
  return out; }"""


def fully(page, sel, within):
    return page.evaluate(f"""() => ({FULLY_JS})([document.querySelector({json.dumps(sel)}), document.querySelector({json.dumps(within)})])""")


def sim_head_sel(page):
    """Mark the simulation section's heading; its selector."""
    head(page, SIM_SEC)
    return '[data-test-head="h1"]'


def check_sim_open(page, tag):
    """[3] an open simulation section keeps its heading and its first board row fully visible inside #vmSec."""
    hs = sim_head_sel(page)
    for name, sel in ((f"heading «{SIM_SEC}»", hs), ("first board row (#vmBoards .brd)", "#vmBoards .brd")):
        r = fully(page, sel, "#vmSec")
        check(f"[3] {tag}: open #vmSec shows its {name} fully, inside #vmSec", r["fully"], json.dumps(r, ensure_ascii=False))


def check_sim_folded(page, tag):
    """[3] folded simulation section: no leftover inline min-height, collapses to a heading line (<= 40 px)."""
    f = fold_section(page, SIM_SEC)
    st = page.evaluate("() => { const e = document.getElementById('vmSec'); return e && {minHeight: e.style.minHeight, h: Math.round(e.getBoundingClientRect().height)}; }")
    check(f"[3] {tag}: folded #vmSec has style.minHeight \"\" and height <= 40 px", f and bool(st) and st["minHeight"] == "" and st["h"] <= 40, json.dumps(st))


def body_state(page):
    return page.evaluate("""() => { const e = document.querySelector('#panel .body'); return e && {cls: e.className, tight: e.classList.contains('tight'),
      scroll: e.scrollHeight, client: e.clientHeight, overflowY: getComputedStyle(e).overflowY}; }""")


def run_viewport(p, w, h):
    tag = f"{w}x{h}"
    b, page, kit_calls = open_page(p, w, h)

    opened = {s: open_section(page, s) for s in SECTIONS}
    check(f"[1] {tag}: site built with --sim --flash has all five sections, all open", all(opened.values()),
          json.dumps(opened, ensure_ascii=False) + f"; kit requests {len(kit_calls)}")
    n = page.evaluate(EXPAND_TREE_JS)
    page.wait_for_timeout(500)
    page.evaluate("() => document.querySelectorAll('#panel, #panel *').forEach(e => { if (e.id !== 'tree') e.scrollTop = 0; })")
    page.screenshot(path=str(shots / f"dlfit_{tag}.png"))
    g = page.evaluate(GEOM_JS, DL_BUTTONS)
    print(f"  {tag}: tree nodes unfolded {n}; geometry {json.dumps({k: g[k] for k in ('panel', 'secDl', 'tree', 'dlScrolls', 'panelScrolls')}, ensure_ascii=False)}")

    # [1] every download button fully visible, no scrolling needed
    for l in DL_BUTTONS:
        bi = g["btns"].get(l)
        check(f"[1] {tag}: «{l}» fully visible inside #secDl and #panel (no scrolling)", bool(bi and bi["fully"]),
              json.dumps(bi and {"button": bi["rect"], "visible area": bi["clip"], "clipped by": bi["clippers"]}, ensure_ascii=False) + f"; #secDl {g['secDl']}")
    sd = g["secDl"]
    check(f"[1] {tag}: nothing inside #secDl needs scrolling", bool(sd) and not g["dlScrolls"], json.dumps({"#secDl": sd, "scrolling boxes": g["dlScrolls"]}))

    # [2] the tree gives way. Either (a) the panel does not scroll and #tree shows >= its min height, or (b) — only where the open
    # sections cannot fit (not at 1500x950) — the panel scrolls as a whole and, scrolled down to the tree, #tree shows >= its min height.
    # Both: #tree keeps scrolling inside itself; the panel never clips the tree with no way to reach it.
    t = g["tree"]
    need = max(t["minH"], TREE_MIN_FALLBACK) if t else TREE_MIN_FALLBACK
    pn = g["panel"]
    top = page.evaluate(TREE_JS)
    panel_scrolls = bool(pn) and (pn["scroll"] > pn["client"] + 1 or bool(g["panelScrolls"]))
    a_ok = not panel_scrolls and top["visH"] >= need - 1
    after = None
    if not a_ok and panel_scrolls:
        page.evaluate("""() => { const p = document.getElementById('panel'), t = document.getElementById('tree');   // scroll the panel down to the tree
          for (let e = t.parentElement; e; e = e.parentElement) { if (/(auto|scroll)/.test(getComputedStyle(e).overflowY)) e.scrollTop = e.scrollHeight; if (e === p) break; } }""")
        page.wait_for_timeout(200)
        after = page.evaluate(TREE_JS)
        page.screenshot(path=str(shots / f"dlfit_{tag}_panel_scrolled.png"))
    b_ok = after is not None and after["visH"] >= need - 1 and after["scrollable"] == len(after["chain"]) > 0
    may_scroll = (w, h) not in NO_PANEL_SCROLL
    if not may_scroll:
        check(f"[2] {tag}: the panel does not scroll as a whole (#panel and the boxes between it and #tree)", not panel_scrolls,
              json.dumps({"panel": pn and [pn["scroll"], pn["client"]], "scrolling": g["panelScrolls"]}))
    check(f"[2] {tag}: #tree shows at least its min height ({need:.0f} px)" + ("" if not may_scroll else ", in place or after scrolling the panel to it"),
          a_ok or (may_scroll and b_ok),
          json.dumps({"panel scrolls": panel_scrolls, "at panel top": top, "after scrolling panel to tree": after}, ensure_ascii=False))
    check(f"[2] {tag}: #tree scrolls inside itself", bool(t) and t["overflowY"] in ("auto", "scroll") and t["scroll"] > t["client"],
          json.dumps(t and {"overflowY": t["overflowY"], "scroll": [t["scroll"], t["client"]]}))

    # [3] the simulation section open: heading + first row visible (panel back at the top); then folded: collapses cleanly
    page.evaluate("() => document.querySelectorAll('#panel, #panel *').forEach(e => { if (e.id !== 'tree') e.scrollTop = 0; })")
    check_sim_open(page, tag)
    check_sim_folded(page, tag)
    page.screenshot(path=str(shots / f"dlfit_{tag}_sim_folded.png"))
    b.close()


def run_roomy(p, w, h, fold):
    """[2] negative: when everything fits, `#panel .body` is not «tight» and does not scroll."""
    tag = f"{w}x{h}" + (" with " + " and ".join(f"«{f}»" for f in fold) + " folded" if fold else " all open")
    b, page, _ = open_page(p, w, h)
    for s_ in SECTIONS:
        open_section(page, s_)
    for f in fold:
        fold_section(page, f)
    page.wait_for_timeout(400)
    page.screenshot(path=str(shots / f"dlfit_roomy_{w}x{h}_{len(fold)}.png"))
    st = body_state(page)
    check(f"[2] {tag}: #panel .body has no class «tight» and does not scroll", bool(st) and not st["tight"] and st["scroll"] <= st["client"] + 1, json.dumps(st))
    b.close()


def run_phone(p):
    """[4] phone 390x844: #vmSec and #secDl as tall as their content, nothing in #secDl scrolls."""
    tag = "390x844"
    b, page, _ = open_page(p, 390, 844, mobile=True)
    if page.evaluate("() => document.getElementById('panel').classList.contains('collapsed')"):   # on a phone the panel starts folded to its title: unfold it (#pFold)
        page.click("#pFold"); page.wait_for_timeout(400)
    check(f"[4] {tag}: the panel unfolds (#pFold)", not page.evaluate("() => document.getElementById('panel').classList.contains('collapsed')"))
    opened = {s_: open_section(page, s_) for s_ in SECTIONS}
    page.wait_for_timeout(400)
    page.screenshot(path=str(shots / "dlfit_phone.png"))
    check(f"[4] {tag}: all five sections present and open", all(opened.values()), json.dumps(opened, ensure_ascii=False))
    for sec in ("#vmSec", "#secDl"):
        sq = page.evaluate(f"() => ({SQUEEZED_JS})(document.querySelector({json.dumps(sec)}))")
        hh = page.evaluate(f"() => {{ const e = document.querySelector({json.dumps(sec)}); return e && [e.scrollHeight, e.clientHeight]; }}")
        check(f"[4] {tag}: {sec} as tall as its content (no box inside it cut or scrolling)", bool(hh) and hh[0] <= hh[1] + 1 and not sq,
              json.dumps({"scroll/client": hh, "squeezed": sq}))
    b.close()


with sync_playwright() as p:
    for (w, h) in VIEWPORTS:
        try:
            run_viewport(p, w, h)
        except Exception as e:  # noqa
            check(f"scenario {w}x{h} ran to the end", False, f"{type(e).__name__}: {str(e)[:300]}")
    for name, fn in (("roomy 1500x950 folded", lambda: run_roomy(p, 1500, 950, [SIM_SEC, HW_SEC])),
                     ("roomy 1500x1300", lambda: run_roomy(p, 1500, 1300, [])), ("phone 390x844", lambda: run_phone(p))):
        try:
            fn()
        except Exception as e:  # noqa
            check(f"scenario {name} ran to the end", False, f"{type(e).__name__}: {str(e)[:300]}")
srv.shutdown()
print("\n| check | result | detail |\n|---|---|---|")
for r in rows:
    print("| " + " | ".join(str(x).replace("|", "/") for x in r) + " |")
sys.exit(0 if ok_all else 1)
