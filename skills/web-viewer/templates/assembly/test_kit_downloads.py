#!/usr/bin/env python3
"""Real-browser check of the download kits on an assembly site (the engineer stack's model page).

Builds fresh sites from importer output with THIS template (build.py; default --kit auto, and once with --kit none), serves them under
/<project>/ like the stack does, and intercepts the router's kit endpoints (no router, no worker):
  GET /kit/<project>/status   -> {enabled, fresh, building, since, parts: [ids with files], nodes: [node names with files]}
  GET /kit/<project>/<scope>  scope: print | laser | bought | step | node/<urlenc node> | part/<urlenc id>
                              200 file + Content-Disposition: attachment (a non-ASCII name) | 202 {building: true, since} while the export runs

The model: --in, else $VIEWER_TEST_MODEL, else the demo machine (examples/nema-bracket, built once in a temp dir). The part with files is
the biggest distinct part (or --part); the node with files is an assembly node of the tree whose parent assembly is in the tree too, and
that parent is the node without files (or --node / --other-node: a drawing number or a group node's name).
  env -u VIRTUAL_ENV uv run --isolated --no-project --with playwright python test_kit_downloads.py \
      [--in <importer output>] [--part <id>] [--node <drawing or name>] [--swiftshader] [--shots <dir>]
(first run: `... --with playwright playwright install chromium`)

UI the test relies on (from the spec): the left panel is #panel, the part / node card is #info. Buttons are <button>s (or links with
role button) named exactly as the kit scopes print / laser / bought / step in #panel, the card buttons kit.part and kit.node in #info
(texts from i18n.mjs; the page is in its default language, ru). While a build runs, the pressed button's text contains kit.building and
the time (digits: left, or elapsed without an estimate).
Done-when numbers in the check names: [1] panel buttons + the building state with auto-download, [2] card buttons, [4] no kit -> the four
panel buttons greyed out with kit.off.project on hover, no card buttons, no errors, [5] a scope the router does not offer
(`off`: {scope: "none" | "setup"}) -> its button greyed out with the reason on hover, the rest work.
Exit code 1 on any failed check.
"""
import argparse, functools, json, re, subprocess, sys, tempfile, threading, time, urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from viewer_stack_server import model_dir, parts_of, serve_stack, ui_texts  # noqa: E402
ap = argparse.ArgumentParser()
ap.add_argument("--in", dest="src", default=None, help="importer output dir (default: $VIEWER_TEST_MODEL, else the demo machine)")
ap.add_argument("--part", default=None, help="a part id that has files in the kit (default: the biggest distinct part)")
ap.add_argument("--node", default=None, help="drawing number (or group name) of an assembly node that has files in the kit (default: from the tree)")
ap.add_argument("--other-node", default=None, help="drawing number (or group name) of a node WITHOUT files in the kit (default: the node's parent)")
ap.add_argument("--build", default=str(HERE / "build.py"), help="build.py to test (default: this template)")
ap.add_argument("--swiftshader", action="store_true")
ap.add_argument("--shots", default=None)
a = ap.parse_args()
a.src = str(model_dir(a.src))
if not a.part:
    distinct = [p for p in parts_of(a.src) if not p.get("part_id")]
    a.part = max(distinct, key=lambda p: p.get("volume_mm3") or 0)["id"]
T = ui_texts("ru")
K = T["kit"]
S = K["scopes"]
shots = Path(a.shots) if a.shots else Path(tempfile.mkdtemp(prefix="kit_shots_")); shots.mkdir(parents=True, exist_ok=True); print("screenshots:", shots)
GPU_ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] if a.swiftshader else ["--use-angle=metal", "--enable-gpu", "--ignore-gpu-blocklist"]
rows, ok_all = [], True

PANEL = {   # button label -> (scope, served file name: non-ASCII on purpose, bytes, content type)
    S["print"]: ("print", "Impression 3D – pièces.zip", b"PK\x03\x04 print-zip", "application/zip"),
    S["laser"]: ("laser", "Laser und Biegen – Teile.zip", b"PK\x03\x04 laser-zip", "application/zip"),
    S["bought"]: ("bought", "Zukaufteile – Liste.xlsx", b"PK\x03\x04 bought-xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    S["step"]: ("step", "Modèle complet.step", b"ISO-10303-21; model", "application/step"),
}
PART_FILE = (f"Pièce {a.part}.zip", b"PK\x03\x04 part-zip")
NODE_FILE = ("Baugruppe – Knoten.zip", b"PK\x03\x04 node-zip")
CARD_PART, CARD_NODE = K["part"], K["node"]
BUILD_SECONDS = 5


def check(name, ok, detail=""):
    global ok_all
    ok_all &= bool(ok)
    rows.append((name, "PASS" if ok else "FAIL", detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)
    return ok


# ---------------------------------------------------------------- sites: build with this template, serve under /<project>/
root = Path(tempfile.mkdtemp(prefix="kit_site_"))
b_kit = subprocess.run([sys.executable, a.build, "--in", a.src, "--out", str(root / "demo-proj"), "--flash", "none"], capture_output=True, text=True)   # flash is on by default: its router calls would 404 here
if b_kit.returncode:
    sys.exit("build.py failed:\n" + b_kit.stderr[-2000:])
b_none = subprocess.run([sys.executable, a.build, "--in", a.src, "--out", str(root / "nokit-proj"), "--kit", "none", "--flash", "none"], capture_output=True, text=True)
srv, _ = serve_stack(root)   # the stack's rules: /_viewer/ = this template, /<id>/ = the site with the shared index.html
BASE = f"http://127.0.0.1:{srv.server_port}"


def kit_router(page, st):
    """Fake router for /kit/**. st: {enabled, parts, nodes, building: {scope: ready_at}, calls: [paths]}."""
    def handle(route):
        path = urllib.parse.unquote(urllib.parse.urlparse(route.request.url).path)
        st["calls"].append(path)
        m = re.match(r"^/kit/([^/]+)/(.+)$", path)
        if not m:
            return route.fulfill(status=404, content_type="application/json", body='{"error": "no such endpoint"}')
        scope, t = m[2], time.time()
        if scope == "status":
            building = any(t < r for r in st["building"].values())
            body = {"enabled": st["enabled"], "fresh": st["enabled"] and not building, "building": building,
                    "since": min((r - BUILD_SECONDS for r in st["building"].values() if t < r), default=None),
                    "parts": st["parts"], "nodes": st["nodes"], "scopes": st.get("scopes", ["print", "laser", "bought", "step"]), "off": st.get("off", {})}
            return route.fulfill(status=200, content_type="application/json", body=json.dumps(body, ensure_ascii=False))
        if scope in st["slow"] and scope not in st["building"]:   # stale: the first request starts the export
            st["building"][scope] = t + BUILD_SECONDS
        if scope in st["building"] and t < st["building"][scope]:
            return route.fulfill(status=202, content_type="application/json",
                                 body=json.dumps({"building": True, "since": st["building"][scope] - BUILD_SECONDS - 7}))   # began 7 s ago
        f = st["files"].get(scope)
        if not f:
            return route.fulfill(status=404, content_type="application/json", body=json.dumps({"error": "no such part"}))
        name, data, ctype = f
        route.fulfill(status=200, body=data, headers={
            "Content-Type": ctype,
            "Content-Disposition": f"attachment; filename=\"kit.bin\"; filename*=UTF-8''{urllib.parse.quote(name)}"})
    page.route("**/kit/**", handle)


def new_page(b, project, enabled=True, slow=(), scopes=None, off=None):
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1, accept_downloads=True)
    ctx.add_init_script("try { localStorage.setItem('assembly.sec.%s.dl', '0') } catch (e) {}" % project)   # the downloads section is folded by default (panel redesign): open it
    page = ctx.new_page()
    errs, downloads = [], []
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.on("download", lambda d: downloads.append(d))
    files = {scope: (name, data, ctype) for _, (scope, name, data, ctype) in PANEL.items()}
    files[f"part/{a.part}"] = (*PART_FILE, "application/zip")
    st = {"enabled": enabled, "parts": [a.part] if enabled else [], "nodes": [], "building": {}, "slow": set(slow), "calls": [], "files": files}
    if scopes is not None:
        st.update(scopes=scopes, off=off or {})
    page.__dict__["_kit"] = st
    kit_router(page, st)
    page.route("**/model.json", lambda r: r.fulfill(status=200, content_type="application/json", body='{"v": 0, "building": false}'))   # the router writes it
    page.goto(f"{BASE}/{project}/?app=0", wait_until="load")
    page.wait_for_function("window.__viewer && (window.__viewer.ready || window.__viewer.error)", timeout=120000)
    return ctx, page, errs, downloads, st


def node_name(page, drawing):
    n = page.evaluate(f"window.__viewer.nodeByDrawing({json.dumps(drawing)})")
    if not n:
        return None, None
    return n[0], page.evaluate(f"(() => {{ const o = window.__viewer.nodes[{n[0]}]; return o.userData.name || o.name; }})()")


def tree_pair(page):
    """(node, its parent): the first assembly node with a tree row whose parent assembly has a row too, as viewer indices; (None, None) if none."""
    return page.evaluate("""() => {
      const row = (o) => !!document.querySelector('#tree .tr[data-n="' + o.userData.n + '"]');
      const asm = (o) => o && !o.isMesh && o.children.length > 0;
      const o = window.__viewer.nodes.find((x) => asm(x) && row(x) && asm(x.parent) && row(x.parent));
      return o ? [o.userData.n, o.parent.userData.n] : [null, null];
    }""")


def name_of(page, n):
    return page.evaluate(f"(() => {{ const o = window.__viewer.nodes[{n}]; return o.userData.name || o.name; }})()")


def btn(page, scope, label):
    return page.locator(scope).get_by_role("button", name=label, exact=True)


OFF_SETUP_ALL = K["off"]["project"]
OFF_SETUP = K["off"]["setup"]
OFF_PRINT = K["off"]["print"]
BUILDING = K["building"]


def any_btn(page, label):
    return page.get_by_role("button", name=label, exact=True)


def wait_downloads(downloads, n, timeout, page=None):
    """Sync Playwright only dispatches events (the "download" handler) inside its own calls: wait with page.wait_for_timeout, not time.sleep."""
    t0 = time.time()
    while len(downloads) < n and time.time() - t0 < timeout:
        page.wait_for_timeout(200) if page else time.sleep(0.2)
    return len(downloads) >= n


def same_download(d, name, data):
    try:
        got = Path(d.path()).read_bytes()
    except Exception as e:  # noqa
        return False, str(e)
    return d.suggested_filename == name and got == data, f"{d.suggested_filename!r}, {len(got)} bytes"


def pick_node_in_tree(page, n):
    page.evaluate("window.__viewer.select(null)")
    page.click(f'#tree .tr[data-n="{n}"] .lbl')
    page.wait_for_timeout(500)


def run(p):
    b = p.chromium.launch(args=GPU_ARGS)
    conf = json.loads((root / "demo-proj" / "config.json").read_text(encoding="utf-8"))
    check("build.py default (--kit auto) puts `kit` into config.json", "kit" in conf, ",".join(conf))

    # ---- 1 + 2: kit enabled; LASER is stale (the router builds it), the rest is fresh
    ctx, page, errs, downloads, st = new_page(b, "demo-proj", slow={"laser"})
    nidx, oidx = tree_pair(page)
    if a.node:
        nidx, _ = node_name(page, a.node)
    if a.other_node:
        oidx, _ = node_name(page, a.other_node)
    nname = name_of(page, nidx) if nidx is not None else None
    check("model loads; test node exists", not page.evaluate("window.__viewer.error") and nidx is not None, f"node {a.node or nidx} -> {nname!r}")
    st["nodes"] = [nname] if nname else []
    st["files"][f"node/{nname}"] = (*NODE_FILE, "application/zip")
    page.reload(wait_until="load")   # status now lists the node by its real viewer name
    page.wait_for_function("window.__viewer && (window.__viewer.ready || window.__viewer.error)", timeout=120000)
    try:
        btn(page, "#panel", S["print"]).wait_for(timeout=15000)
    except Exception:  # noqa
        pass
    check("page asks /kit/<project>/status", "/kit/demo-proj/status" in st["calls"], json.dumps(st["calls"][:4], ensure_ascii=False))
    for label in PANEL:
        check(f"[1] panel has «{label}»", btn(page, "#panel", label).count() == 1 and btn(page, "#panel", label).is_visible(),
              f"{btn(page, '#panel', label).count()} found")
    page.screenshot(path=str(shots / "1_panel.png"))

    # fresh scopes: one press -> the file downloads at once
    for label, (scope, name, data, _) in PANEL.items():
        if scope == "laser" or btn(page, "#panel", label).count() != 1:
            continue
        n0 = len(downloads)
        btn(page, "#panel", label).click()
        got = wait_downloads(downloads, n0 + 1, 10, page)
        ok, det = same_download(downloads[-1], name, data) if got else (False, "no download")
        check(f"[1] «{label}» downloads /kit/demo-proj/{scope} with its name", got and ok and f"/kit/demo-proj/{scope}" in st["calls"], det)

    # stale scope: the building state with a ticking elapsed time, then the download starts by itself
    lb = btn(page, "#panel", S["laser"])
    if check("[1] stale case reached the laser button", lb.count() == 1):
        h = lb.element_handle()
        n0 = len(downloads)
        h.click()
        page.wait_for_timeout(1200)
        t1 = h.inner_text()
        page.screenshot(path=str(shots / "1_building.png"))
        check("[1] stale -> the button says it is building, with the time", BUILDING in t1 and re.search(r"\d", t1) is not None, repr(t1))
        check("[1] stale -> nothing downloaded yet", len(downloads) == n0)
        page.wait_for_timeout(2100)
        t2 = h.inner_text()
        check("[1] the time ticks", BUILDING in t2 and t2 != t1, f"{t1!r} -> {t2!r}")
        got = wait_downloads(downloads, n0 + 1, BUILD_SECONDS + 25, page)
        ok, det = same_download(downloads[-1], *PANEL[S["laser"]][1:3]) if got else (False, "no download")
        check("[1] when ready the download starts by itself (no second press)", got and ok, det)
        page.wait_for_timeout(500)
        check("[1] after the download the button is back to its laser label", BUILDING not in h.inner_text() and S["laser"] in h.inner_text(), repr(h.inner_text()))
        check("[1] exactly one laser download", len([d for d in downloads[n0:] if d.suggested_filename == PANEL[S["laser"]][1]]) == 1, str(len(downloads) - n0))

    # part card: a part with files has the button; it downloads /kit/<p>/part/<id>
    page.evaluate(f"window.__viewer.select({json.dumps(a.part)})")
    page.wait_for_timeout(600)
    check("part card is open", page.locator("#info").is_visible())
    pb = btn(page, "#info", CARD_PART)
    if check(f"[2] part card has «{CARD_PART}»", pb.count() == 1 and pb.is_visible(), f"{pb.count()} found"):
        page.screenshot(path=str(shots / "2_part_card.png"))
        n0 = len(downloads)
        pb.click()
        got = wait_downloads(downloads, n0 + 1, 10, page)
        ok, det = same_download(downloads[-1], *PART_FILE) if got else (False, "no download")
        check("[2] it downloads /kit/demo-proj/part/<id>", got and ok and f"/kit/demo-proj/part/{a.part}" in st["calls"], det)
    other = page.evaluate("(id) => { const r = window.__viewer.parts().find(([pid]) => pid !== id); return r ? r[0] : null; }", a.part)
    page.evaluate(f"window.__viewer.select({json.dumps(other)})")
    page.wait_for_timeout(500)
    check(f"[2] a part without files has no «{CARD_PART}»", page.locator("#info").is_visible() and btn(page, "#info", CARD_PART).count() == 0, other)

    # node card: a node with files has the button; it downloads /kit/<p>/node/<name>
    if nidx is not None:
        pick_node_in_tree(page, nidx)
        nb = btn(page, "#info", CARD_NODE)
        if check(f"[2] node card has «{CARD_NODE}»", page.locator("#info").is_visible() and nb.count() == 1 and nb.is_visible(), f"{nb.count()} found"):
            page.screenshot(path=str(shots / "2_node_card.png"))
            n0 = len(downloads)
            nb.click()
            got = wait_downloads(downloads, n0 + 1, 10, page)
            ok, det = same_download(downloads[-1], *NODE_FILE) if got else (False, "no download")
            check("[2] it downloads /kit/demo-proj/node/<node name>", got and ok and f"/kit/demo-proj/node/{nname}" in st["calls"], det)
        check(f"[2] node card has no «{CARD_PART}»", btn(page, "#info", CARD_PART).count() == 0)
    if oidx is not None:
        pick_node_in_tree(page, oidx)
        check(f"[2] a node without files has no «{CARD_NODE}»", page.locator("#info").is_visible() and btn(page, "#info", CARD_NODE).count() == 0, name_of(page, oidx))
    check("console has no errors (kit enabled)", not errs, "; ".join(errs[:3]))
    ctx.close()

    # ---- a stale STEP pressed by mistake -> the same button pressed again stops waiting, nothing downloads
    ctx, page, errs, downloads, st = new_page(b, "demo-proj", slow={"step"})
    sb = btn(page, "#panel", S["step"])
    try:
        sb.wait_for(timeout=15000)
    except Exception:  # noqa
        pass
    if check("[cancel] STEP button present", sb.count() == 1):
        h = sb.element_handle()
        h.click(); page.wait_for_timeout(1200)
        check("[cancel] stale STEP -> building", BUILDING in h.inner_text(), repr(h.inner_text()))
        check("[cancel] the waiting button stays clickable", h.is_enabled())
        n0 = len(downloads)
        h.click(); page.wait_for_timeout(800)
        check(f"[cancel] second press -> label back to «{S['step']}»", h.inner_text().strip() == S["step"], repr(h.inner_text()))
        check("[cancel] the page says waiting was cancelled", K["cancelled"] in page.inner_text("#panel"), page.inner_text("#panel")[-200:])
        wait_downloads(downloads, n0 + 1, BUILD_SECONDS + 5, page)
        check("[cancel] nothing downloads after cancelling", len(downloads) == n0, str(len(downloads) - n0))
    ctx.close()

    # ---- 4: the router says enabled:false -> the four panel buttons greyed out with the reason, no card buttons, no errors
    ctx, page, errs, downloads, st = new_page(b, "demo-proj", enabled=False)
    page.wait_for_timeout(2500)
    check("[4] disabled: the page still asked /status", "/kit/demo-proj/status" in st["calls"])
    for label in PANEL:
        h = any_btn(page, label)
        check(f"[4] disabled: «{label}» shown", h.count() == 1 and h.is_visible())
        if h.count():
            check(f"[4] disabled: «{label}» greyed out", h.get_attribute("aria-disabled") == "true")
            check(f"[4] disabled: «{label}» says why on hover", h.get_attribute("title") == OFF_SETUP_ALL, repr(h.get_attribute("title")))
            h.click(force=True); page.wait_for_timeout(300)
    page.evaluate(f"window.__viewer.select({json.dumps(a.part)})"); page.wait_for_timeout(500)
    check(f"[4] disabled: part card has no «{CARD_PART}»", page.locator("#info").is_visible() and any_btn(page, CARD_PART).count() == 0)
    if nidx is not None:
        pick_node_in_tree(page, nidx)
        check(f"[4] disabled: node card has no «{CARD_NODE}»", any_btn(page, CARD_NODE).count() == 0)
    check("[4] disabled: clicks send no other /kit requests", all(c.endswith("/status") for c in st["calls"]), json.dumps(st["calls"], ensure_ascii=False))
    check("[4] disabled: no building state", BUILDING not in page.inner_text("body"))
    check("[4] disabled: no downloads", not downloads)
    check("[4] disabled: console has no errors", not errs, "; ".join(errs[:3]))
    page.screenshot(path=str(shots / "4_disabled.png"))
    ctx.close()

    # ---- 5: the router does not offer print (the model has none) nor step (not set up) -> those two greyed out with the reason
    ctx, page, errs, downloads, st = new_page(b, "demo-proj", scopes=["laser", "bought"], off={"print": "none", "step": "setup"})
    page.wait_for_timeout(2500)
    for label, why in ((S["print"], OFF_PRINT), (S["step"], OFF_SETUP)):
        h = any_btn(page, label)
        check(f"[5] «{label}» shown and greyed out", h.count() == 1 and h.get_attribute("aria-disabled") == "true")
        check(f"[5] «{label}» says why on hover", h.count() == 1 and h.get_attribute("title") == why, repr(h.count() and h.get_attribute("title")))
        if h.count():
            h.click(force=True); page.wait_for_timeout(300)
    check("[5] greyed-out buttons send nothing", all(c.endswith("/status") for c in st["calls"]), json.dumps(st["calls"], ensure_ascii=False))
    for label in (S["laser"], S["bought"]):
        h = any_btn(page, label)
        check(f"[5] offered «{label}» is not greyed out", h.count() == 1 and h.get_attribute("aria-disabled") != "true" and not h.get_attribute("title"))
        n0 = len(downloads); h.click(); wait_downloads(downloads, n0 + 1, 10, page)
        check(f"[5] offered «{label}» still downloads", len(downloads) == n0 + 1)
    check("[5] console has no errors", not errs, "; ".join(errs[:3]))
    page.screenshot(path=str(shots / "5_scope_off.png"))
    ctx.close()

    # ---- 4: built with --kit none -> the page never talks to /kit at all
    if check("build.py --kit none works", b_none.returncode == 0, b_none.stderr[-300:]):
        conf_n = json.loads((root / "nokit-proj" / "config.json").read_text(encoding="utf-8"))
        check("[4] --kit none: no `kit` in config.json", "kit" not in conf_n, ",".join(conf_n))
        ctx, page, errs, downloads, st = new_page(b, "nokit-proj")
        page.wait_for_timeout(2500)
        check("[4] --kit none: no /kit requests", st["calls"] == [], json.dumps(st["calls"], ensure_ascii=False))
        check("[4] --kit none: no download buttons", all(any_btn(page, x).count() == 0 for x in PANEL))
        check("[4] --kit none: console has no errors", not errs, "; ".join(errs[:3]))
        ctx.close()
    b.close()


with sync_playwright() as p:
    run(p)
srv.shutdown()
print("\n| check | result | detail |\n|---|---|---|")
for r in rows:
    print("| " + " | ".join(str(x).replace("|", "/") for x in r) + " |")
sys.exit(0 if ok_all else 1)
