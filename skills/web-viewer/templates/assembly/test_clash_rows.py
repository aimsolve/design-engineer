#!/usr/bin/env python3
"""Real-browser check of the red plaque «⚠ parts overlap: N» (initClearance, i18n clash.head) on a phone and a desktop.

The bug it guards: on a 390 px phone the opened plaque collapsed to about half the width, every row wrapped to 4-5 lines and a scrolled
list showed a row's tail «93.9 mm³ · t 0.7857 s» without the names; names fell back to placement ids when clearance.json arrived before
parts.json.

A site built with THIS build.py from an importer output (--in, else $VIEWER_TEST_MODEL, else the demo machine examples/nema-bracket,
built once in a temp dir), six distinct parts renamed in its parts.json (drawing null, so the card title is exactly the name), served
like the stack (viewer_stack_server.py, /<id>/). The test then writes public clearance.json (six pairs) and names it in config.json, as
build.py does after a code-model check. The page is in its default language (ru); the unit texts come from i18n.mjs.

  env -u VIRTUAL_ENV uv run --isolated --no-project --with playwright python test_clash_rows.py \
      [--in <importer output>] [--swiftshader] [--shots <dir>]
(first run: `... --with playwright playwright install chromium`)

Contract under test (from the spec, not from the code). The plaque is #clashMark, its head #clashHead (click opens), the rows are the
children of #clashList.
  [6a] every row starts with both parts' names (the card titles from parts.json) on their own line; the volume/time is on a separate
       line below (and that line is not split over two visual lines);
  [6b] the names are there even when clearance.json is answered before parts.json has loaded (parts.json held back 3 s);
  [6c] on a 390×800 viewport the opened plaque is at least 85 % of the viewport width.
LOOK at the PNGs. Exit code 1 on any failed check.
"""
import argparse, copy, json, shutil, sys, tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from viewer_stack_server import build, link_input, model_dir, serve_stack, ui_texts  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--in", dest="src", default=None, help="importer output dir (default: $VIEWER_TEST_MODEL, else the demo machine)")
ap.add_argument("--swiftshader", action="store_true")
ap.add_argument("--shots", default=None)
a = ap.parse_args()
GPU_ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] if a.swiftshader else ["--use-angle=metal", "--enable-gpu", "--ignore-gpu-blocklist"]
NOROUTER = ["--flash", "none", "--kit", "none", "--feedback", "none"]
shots = Path(a.shots) if a.shots else Path(tempfile.mkdtemp(prefix="clash_rows_shots_")); shots.mkdir(parents=True, exist_ok=True); print("screenshots:", shots)
PROJ = "clash-demo"
NAMES = ["Pusher · axle — M5 DIN 912", "Pusher · housing", "Frame · left upright", "Belt drive · pulley", "Top cover",
         "Sensor bracket"]     # long and short names, with the separators real part names carry
MM3 = ui_texts("ru")["clash"]["mm3"]
rows, ok_all = [], True


def check(name, ok, detail=""):
    global ok_all
    ok_all &= bool(ok)
    rows.append((name, "PASS" if ok else "FAIL", detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)
    return ok


src = model_dir(a.src)
cat = json.loads((src / "parts.json").read_text(encoding="utf-8"))
mine = copy.deepcopy(cat)
ids = [p["id"] for p in mine["parts"] if not p.get("part_id")][:len(NAMES)]     # distinct parts (an instance's card is its part's)
if len(ids) < len(NAMES):
    sys.exit(f"the model needs {len(NAMES)} distinct parts, has {len(ids)}")
NAME = dict(zip(ids, NAMES))
for p in mine["parts"]:
    if p["id"] in NAME:
        p.update(name=NAME[p["id"]], drawing=None)
PAIRS = [(ids[0], ids[1]), (ids[2], ids[3]), (ids[4], ids[5]), (ids[0], ids[3]), (ids[1], ids[4]), (ids[2], ids[5])]
CLEARANCE = {"clashes": [{"a": x, "b": y, "groups": ["push"], "now": False, "volume_mm3": 93.9, "t_s": 0.7857, "at_mm": [12.5, 40.0, 7.25]}
                         for x, y in PAIRS], "unmeasured": [], "unmeasured_n": 0, "cycle": True}

tmp = Path(tempfile.mkdtemp(prefix="clash_rows_"))
projects = tmp / "projects"; projects.mkdir()
r = build(link_input(src, tmp / "in", mine), projects / PROJ, *NOROUTER)
if r.returncode:
    sys.exit("build.py failed:\n" + r.stderr[-2000:])
site = projects / PROJ
cfg = json.loads((site / "config.json").read_text(encoding="utf-8"))
cfg["clearance"] = "clearance.json"
(site / "config.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
(site / "clearance.json").write_text(json.dumps(CLEARANCE, ensure_ascii=False), encoding="utf-8")
srv, BASE = serve_stack(projects)

# For each row of #clashList: its text lines, and where (client rect tops) the first name and the volume sit.
ROWS_JS = r"""
([names, mm3]) => {
  const list = document.getElementById('clashList');
  if (!list) return null;
  const rangeOf = (el, needle) => {   // a Range over the first occurrence of needle in el's text, across text nodes
    const tw = document.createTreeWalker(el, NodeFilter.SHOW_TEXT); const nodes = []; let all = '';
    for (let n; (n = tw.nextNode());) { nodes.push([n, all.length]); all += n.data; }
    const i = all.indexOf(needle); if (i < 0) return null;
    const at = (k) => { for (let j = nodes.length - 1; j >= 0; j--) if (nodes[j][1] <= k) return [nodes[j][0], k - nodes[j][1]]; };
    const r = document.createRange(); const s = at(i), e = at(i + needle.length - 1);
    r.setStart(s[0], s[1]); r.setEnd(e[0], e[1] + 1); return r;
  };
  return [...list.children].map((row) => {
    const text = row.innerText || '';
    const lines = text.split('\n').map((s) => s.trim()).filter(Boolean);
    const first = names.find((n) => text.includes(n));
    const rn = first ? rangeOf(row, first) : null;
    const vl = lines.find((l) => l.includes(mm3)) || '';
    const rv = vl ? rangeOf(row, vl) : null;
    const tops = rv ? [...new Set([...rv.getClientRects()].filter((q) => q.width > 0).map((q) => Math.round(q.top)))] : [];
    return { text, lines, nameTop: rn ? rn.getBoundingClientRect().top : null, nameBottom: rn ? rn.getBoundingClientRect().bottom : null,
             volTop: rv ? rv.getBoundingClientRect().top : null, volLineTops: tops };
  });
}
"""


def open_plaque(page):
    page.wait_for_selector("#clashHead", timeout=120000)
    page.click("#clashHead")
    page.wait_for_timeout(300)


def check_rows(page, tag):
    got = page.evaluate(ROWS_JS, [NAMES, MM3])
    if not check(f"[6a] {tag}: the plaque lists {len(PAIRS)} rows", got is not None and len(got) == len(PAIRS), f"{got and len(got)} rows"):
        return
    for (x, y), row in zip(PAIRS, got):
        na, nb = NAME[x], NAME[y]
        first = row["lines"][0] if row["lines"] else ""
        check(f"[6a] {tag}: row {na} × {nb} starts with both names", na in first and nb in first, json.dumps(row["lines"], ensure_ascii=False))
        check(f"[6a] {tag}: its first line holds no volume/time", MM3 not in first and "0.7857" not in first, first)
        check(f"[6a] {tag}: volume/time on a separate line below the names",
              any(MM3 in l and "0.7857" in l for l in row["lines"][1:]) and row["volTop"] is not None and row["nameBottom"] is not None
              and row["volTop"] >= row["nameBottom"] - 1, json.dumps({k: row[k] for k in ("lines", "nameBottom", "volTop")}, ensure_ascii=False))
        check(f"[6a] {tag}: the volume/time line is one visual line", len(row["volLineTops"]) == 1, str(row["volLineTops"]))
        check(f"[6a] {tag}: no placement id instead of a name", x not in first and y not in first, first)


try:
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=GPU_ARGS)

        # ---- [6a][6c] phone 390×800
        ctx = b.new_context(viewport={"width": 390, "height": 800}, device_scale_factor=2)
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(f"{BASE}/{PROJ}/?app=0", wait_until="load")
        open_plaque(page)
        w = page.evaluate("(() => { const m = document.getElementById('clashMark'); return m ? m.getBoundingClientRect().width : 0; })()")
        check("[6c] phone: the opened plaque is at least 85 % of 390 px", w >= 0.85 * 390, f"{w:.0f} px")
        check_rows(page, "phone")
        page.screenshot(path=str(shots / "phone_open.png"))
        page.evaluate("(() => { const l = document.getElementById('clashList'); if (l) l.scrollTop = l.scrollHeight; })()")
        page.wait_for_timeout(200)
        page.screenshot(path=str(shots / "phone_scrolled.png"))
        check("[6] phone: no page errors", not errs, "; ".join(errs[:3]))
        ctx.close()

        # ---- [6a] desktop
        ctx = b.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.goto(f"{BASE}/{PROJ}/?app=0", wait_until="load")
        open_plaque(page)
        check_rows(page, "desktop")
        page.screenshot(path=str(shots / "desktop_open.png"))
        ctx.close()

        # ---- [6b] clearance.json answered before parts.json: parts.json held back 3 s
        ctx = b.new_context(viewport={"width": 390, "height": 800}, device_scale_factor=2)
        page = ctx.new_page()
        held, seen = [], []
        page.route(f"**/{PROJ}/parts.json*", lambda route: held.append(route))
        page.on("request", lambda req: seen.append(req.url.split("?")[0].rsplit("/", 1)[-1]))
        page.goto(f"{BASE}/{PROJ}/?app=0", wait_until="domcontentloaded")
        for _ in range(60):   # until the page asked for clearance.json while parts.json is held
            if "clearance.json" in seen and held:
                break
            page.wait_for_timeout(100)
        check("[6b] clearance.json was fetched while parts.json was still pending", "clearance.json" in seen and bool(held), str(seen[-8:]))
        page.wait_for_timeout(3000)
        for route in held:
            route.continue_()
        page.unroute(f"**/{PROJ}/parts.json*")
        page.wait_for_function("window.__viewer && (window.__viewer.ready || window.__viewer.error)", timeout=120000)
        open_plaque(page)
        page.wait_for_timeout(500)
        check_rows(page, "parts.json late")
        page.screenshot(path=str(shots / "parts_late.png"))
        ctx.close()
        b.close()
finally:
    srv.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)

print("\n| check | result | detail |\n|---|---|---|")
for x in rows:
    print("| " + " | ".join(str(c).replace("|", "/") for c in x) + " |")
print(f"\n{sum(x[1] == 'FAIL' for x in rows)} failing, {sum(x[1] == 'PASS' for x in rows)} passing")
sys.exit(0 if ok_all else 1)
