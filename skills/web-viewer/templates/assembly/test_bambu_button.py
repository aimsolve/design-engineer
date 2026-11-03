#!/usr/bin/env python3
"""Real-browser check of the «Open in Bambu Studio» button on the model page (feature A, viewer side). Written before the implementation.

Builds a site from importer output with THIS template (build.py --flash none), serves it under /<project>/ like the stack does
(viewer_stack_server.serve_stack) and fakes the router (no router, no worker):
  GET /kit/<p>/status          -> {enabled, fresh, building, since, parts, nodes, scopes, off, print_parts: [ids of print parts]}
  GET /kit/<p>/3mf/print       -> 200 {"url": <signed https link>, "name": <ascii>.3mf} | 202 {building, since} while the kit builds
  GET /kit/<p>/3mf/part/<id>   -> the same for one part
  everything else under /kit/ and /hw/ -> 404; model.json -> {"v": 0}

  env -u VIRTUAL_ENV uv run --isolated --no-project --with playwright python test_bambu_button.py \
      [--in <importer output>] [--part <id>] [--swiftshader] [--shots <dir>]
(first run: `... --with playwright playwright install chromium`)
The model: --in, else $VIEWER_TEST_MODEL, else the demo machine (examples/nema-bracket, built once in a temp dir); the print part is the
biggest distinct part with an STL (or --part).

UI the test relies on (the spec): the kit row is #kitRow, the part card's kit block is #kitCard. Buttons are <button>s named exactly
kit.bambu of the page language; the print button is kit.scopes.print (texts from i18n.mjs; the page's default language is ru).
How the hand-off to Bambu Studio is observed: Chromium emits a Playwright "request" event for a navigation to an unregistered
scheme, whichever way the page starts it (location.href / assign, window.open, a[href].click(), an iframe src; checked on
Chromium 1248), and the navigation then fails harmlessly. The test records every request whose URL starts with "bambustudio"
on the browser context (popups and iframes included) and compares it with:
  macOS (user agent + navigator.platform MacIntel):  "bambustudioopen://" + encodeURIComponent(url + "&name=" + name)
  Windows (user agent + navigator.platform Win32):   "bambustudio://open?file=" + encodeURIComponent(url + "&name=" + name)
Check names: [1] kit row button right after the print button, enabled with the print scope, greyed (aria-disabled) without it;
[2] part card button only for ids in status.print_parts; [3] click -> fetch /kit/<p>/3mf/<scope> -> the scheme link (mac / windows);
[4] 202 -> the same waiting UI as downloads (kit.building + time), then it opens by itself; [5] phones (iPhone, Android, 390x844)
show no Bambu buttons; [6] English page (?lang=en): the English kit.bambu.
Exit code 1 on any failed check.
"""
import argparse, atexit, json, re, shutil, subprocess, sys, tempfile, time, urllib.parse
from pathlib import Path
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from viewer_stack_server import model_dir, parts_of, serve_stack, ui_texts  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--in", dest="src", default=None, help="importer output dir (default: $VIEWER_TEST_MODEL, else the demo machine)")
ap.add_argument("--part", default=None, help="a part id of the model; the fake status lists it as a print part (default: the biggest distinct part with an STL)")
ap.add_argument("--build", default=str(HERE / "build.py"), help="build.py to test (default: this template)")
ap.add_argument("--swiftshader", action="store_true")
ap.add_argument("--shots", default=None)
a = ap.parse_args()
a.src = str(model_dir(a.src))
if not a.part:
    printable = [p for p in parts_of(a.src) if not p.get("part_id") and (p.get("files") or {}).get("stl")]
    if not printable:
        sys.exit("the model has no distinct part with an STL to play the print part: pass --part")
    a.part = max(printable, key=lambda p: p.get("volume_mm3") or 0)["id"]
RU, EN = ui_texts("ru")["kit"], ui_texts("en")["kit"]
shots = Path(a.shots) if a.shots else Path(tempfile.mkdtemp(prefix="bambu_shots_")); shots.mkdir(parents=True, exist_ok=True); print("screenshots:", shots)
GPU_ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] if a.swiftshader else ["--use-angle=metal", "--enable-gpu", "--ignore-gpu-blocklist"]
rows, ok_all = [], True

PROJ = "demo-proj"
BAMBU, BAMBU_EN, PRINT = RU["bambu"], EN["bambu"], RU["scopes"]["print"]
BUILDING = RU["building"]
BUILD_SECONDS = 5
LINKS = {   # scope -> what the router answers when fresh (a fake signed link; the page must pass it on untouched)
    "print": {"url": f"https://engineer.example.test/kit/{PROJ}/3mf/print?exp=1793540788&sig=Zk3x9QpL2mVb7RtY", "name": "demo-proj-print.3mf"},
    f"part/{a.part}": {"url": f"https://engineer.example.test/kit/{PROJ}/3mf/part/{urllib.parse.quote(a.part, safe='')}?exp=1793540788&sig=Pq8Wn4Hc1sLd6Ke0",
                       "name": f"part-{a.part}.3mf"},
}
UA = {
    "mac": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.6723.91 Safari/537.36", "MacIntel", "macOS"),
    "win": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.6723.91 Safari/537.36", "Win32", "Windows"),
    "iphone": ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1", "iPhone", "iOS"),
    "android": ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.6723.91 Mobile Safari/537.36", "Linux armv8l", "Android"),
}


def enc(s):
    return urllib.parse.quote(s, safe="-_.!~*'()")   # = encodeURIComponent


def expected(scope, os_):
    link = LINKS[scope]
    tail = enc(link["url"] + "&name=" + link["name"])
    return ("bambustudioopen://" if os_ == "mac" else "bambustudio://open?file=") + tail


def check(name, ok, detail=""):
    global ok_all
    ok_all &= bool(ok)
    rows.append((name, "PASS" if ok else "FAIL", detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)
    return ok


# ---------------------------------------------------------------- site
root = Path(tempfile.mkdtemp(prefix="bambu_site_"))
atexit.register(shutil.rmtree, root, True)
r = subprocess.run([sys.executable, a.build, "--in", a.src, "--out", str(root / PROJ), "--flash", "none"], capture_output=True, text=True)
if r.returncode:
    sys.exit("build.py failed:\n" + (r.stderr or r.stdout)[-2000:])
srv, BASE = serve_stack(root)


def fake_router(ctx, st):
    def kit(route):
        path = urllib.parse.unquote(urllib.parse.urlparse(route.request.url).path)
        st["calls"].append(path)
        m = re.match(r"^/kit/([^/]+)/(.+)$", path)
        scope, t = (m[2] if m else ""), time.time()
        building = any(t < x for x in st["building"].values())
        if scope == "status":
            body = {"enabled": st["enabled"], "fresh": st["enabled"] and not building, "building": building,
                    "since": min((x - BUILD_SECONDS for x in st["building"].values() if t < x), default=None),
                    "parts": [a.part], "nodes": [], "scopes": st["scopes"], "off": st["off"], "print_parts": st["print_parts"]}
            return route.fulfill(status=200, content_type="application/json", body=json.dumps(body, ensure_ascii=False))
        if scope.startswith("3mf/"):
            s = scope[4:]
            if s in st["slow"] and s not in st["building"]:
                st["building"][s] = t + BUILD_SECONDS
            if s in st["building"] and t < st["building"][s]:
                return route.fulfill(status=202, content_type="application/json",
                                     body=json.dumps({"building": True, "since": st["building"][s] - BUILD_SECONDS - 7}))
            if s in LINKS:
                return route.fulfill(status=200, content_type="application/json", body=json.dumps(LINKS[s]))
        route.fulfill(status=404, content_type="application/json", body='{"error": "no such endpoint"}')
    ctx.route(re.compile(r"^https?://[^/]+/kit/.*"), kit)
    ctx.route(re.compile(r"^https?://[^/]+/hw/.*"), lambda r: r.fulfill(status=404, content_type="application/json", body='{"error": "no router"}'))
    ctx.route("**/model.json", lambda r: r.fulfill(status=200, content_type="application/json", body='{"v": 0, "building": false}'))


def open_page(b, os_="mac", lang=None, scopes=None, off=None, enabled=True, slow=(), print_parts=None):
    ua, platform, uad = UA[os_]
    phone = os_ in ("iphone", "android")
    vw, vh = (390, 844) if phone else (1440, 900)
    ctx = b.new_context(viewport={"width": vw, "height": vh}, device_scale_factor=1, is_mobile=phone, has_touch=phone, user_agent=ua)
    ctx.add_init_script("try { localStorage.setItem('assembly.sec.%s.dl', '0') } catch (e) {}" % PROJ)   # the downloads section open
    ctx.add_init_script("""(() => { const P = %s, D = %s, M = %s;
        Object.defineProperty(Navigator.prototype, 'platform', {get: () => P, configurable: true});
        Object.defineProperty(Navigator.prototype, 'userAgentData', {configurable: true, get: () => ({platform: D, mobile: M, brands: [],
            getHighEntropyValues: async () => ({platform: D, mobile: M})})}); })();""" % (json.dumps(platform), json.dumps(uad), json.dumps(phone)))
    st = {"enabled": enabled, "scopes": scopes if scopes is not None else ["print", "laser", "bought", "step"], "off": off or {},
          "print_parts": [a.part] if print_parts is None else print_parts, "slow": set(slow), "building": {}, "calls": []}
    fake_router(ctx, st)
    nav = []
    ctx.on("request", lambda rq: nav.append(rq.url) if rq.url.startswith("bambustudio") else None)
    page = ctx.new_page()
    errs = []
    faked = lambda m: "404" in m.text and any(s in ((m.location or {}).get("url") or "") for s in ("/kit/", "/hw/"))
    bambu_fail = lambda m: "bambustudio" in m.text   # the unregistered scheme failing is expected here
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" and not faked(m) and not bambu_fail(m) else None)
    page.on("pageerror", lambda e: errs.append(str(e)))
    q = f"?app=0&lang={lang}" if lang else "?app=0"
    page.goto(f"{BASE}/{PROJ}/{q}", wait_until="load")
    page.wait_for_function("window.__viewer && (window.__viewer.ready || window.__viewer.error)", timeout=120000)
    page.wait_for_timeout(2500)
    if phone and page.evaluate("() => { const p = document.getElementById('panel'); return !!p && p.classList.contains('collapsed'); }"):
        page.click("#pFold"); page.wait_for_timeout(400)
    return ctx, page, errs, nav, st


def btn(page, scope, label):
    return page.locator(scope).get_by_role("button", name=label, exact=True)


def wait_nav(page, nav, n, timeout):
    t0 = time.time()
    while len(nav) < n and time.time() - t0 < timeout:
        page.wait_for_timeout(200)
    return len(nav) >= n


ORDER_JS = """() => { const bs = Array.from(document.querySelectorAll('#kitRow button')).map(b => (b.textContent || '').trim());
  return {texts: bs, print: bs.findIndex(t => t.includes(%s)), bambu: bs.findIndex(t => t.includes(%s))}; }""" % (json.dumps(PRINT), json.dumps(BAMBU))


def select_part(page, pid):
    page.evaluate(f"window.__viewer.select({json.dumps(pid)})")
    page.wait_for_timeout(600)


def run(p):
    b = p.chromium.launch(args=GPU_ARGS)

    # ---- [1] [2] [3] macOS desktop, print scope offered, all fresh
    ctx, page, errs, nav, st = open_page(b, "mac")
    check("model loads", not page.evaluate("window.__viewer.error"))
    kb = btn(page, "#kitRow", BAMBU)
    check(f"[1] #kitRow has «{BAMBU}»", kb.count() == 1 and kb.is_visible(), f"{kb.count()} found")
    order = page.evaluate(ORDER_JS)
    check(f"[1] it comes right after «{PRINT}»", order["print"] >= 0 and order["bambu"] == order["print"] + 1, json.dumps(order, ensure_ascii=False))
    if kb.count() == 1:
        check("[1] enabled while the print scope is offered", kb.get_attribute("aria-disabled") != "true" and kb.is_enabled())
        page.screenshot(path=str(shots / "1_kit_row.png"))
        n0 = len(nav)
        kb.click()
        got = wait_nav(page, nav, n0 + 1, 8)
        check("[3] click fetches /kit/<p>/3mf/print", f"/kit/{PROJ}/3mf/print" in st["calls"], json.dumps(st["calls"][-4:], ensure_ascii=False))
        check("[3] mac: opens bambustudioopen:// + encodeURIComponent(url + '&name=' + name)", got and nav[-1] == expected("print", "mac"),
              nav[-1] if got else "no bambustudio navigation")
        check("[3] one click, one hand-off", len(nav) - n0 == 1, str(len(nav) - n0))
    select_part(page, a.part)
    check("part card is open", page.locator("#info").is_visible())
    cb = btn(page, "#kitCard", BAMBU)
    if check(f"[2] part in print_parts: #kitCard has «{BAMBU}»", cb.count() == 1 and cb.is_visible(), f"{cb.count()} found"):
        page.screenshot(path=str(shots / "2_part_card.png"))
        n0 = len(nav)
        cb.click()
        got = wait_nav(page, nav, n0 + 1, 8)
        check("[3] part click fetches /kit/<p>/3mf/part/<id>", f"/kit/{PROJ}/3mf/part/{a.part}" in st["calls"], json.dumps(st["calls"][-4:], ensure_ascii=False))
        check("[3] mac: the part opens with its own link", got and nav[-1] == expected(f"part/{a.part}", "mac"), nav[-1] if got else "no bambustudio navigation")
    other = page.evaluate("(id) => { const r = window.__viewer.parts().find(([pid]) => pid !== id); return r ? r[0] : null; }", a.part)
    select_part(page, other)
    check(f"[2] a part not in print_parts has no «{BAMBU}» in its card", page.locator("#info").is_visible() and btn(page, "#info", BAMBU).count() == 0, str(other))
    check("[1-3] console has no errors", not errs, "; ".join(errs[:3]))
    ctx.close()

    # ---- [3] Windows
    ctx, page, errs, nav, st = open_page(b, "win")
    kb = btn(page, "#kitRow", BAMBU)
    if check(f"[3] windows: #kitRow has «{BAMBU}»", kb.count() == 1):
        kb.click()
        got = wait_nav(page, nav, 1, 8)
        check("[3] windows: opens bambustudio://open?file= + encodeURIComponent(url + '&name=' + name)", got and nav[-1] == expected("print", "win"),
              nav[-1] if got else "no bambustudio navigation")
    ctx.close()

    # ---- [4] the kit is being rebuilt: 202, the waiting UI, then it opens by itself
    ctx, page, errs, nav, st = open_page(b, "mac", slow={"print"})
    kb = btn(page, "#kitRow", BAMBU)
    if check("[4] stale case reached the Bambu button", kb.count() == 1):
        h = kb.element_handle()
        h.click()
        page.wait_for_timeout(1200)
        t1 = h.inner_text()
        page.screenshot(path=str(shots / "4_building.png"))
        check("[4] 202 -> the button says it is building, with the time", BUILDING in t1 and re.search(r"\d", t1) is not None, repr(t1))
        check("[4] nothing opened yet", nav == [], json.dumps(nav))
        got = wait_nav(page, nav, 1, BUILD_SECONDS + 25)
        check("[4] when ready it opens by itself (no second press)", got and nav[0] == expected("print", "mac"), nav[0] if got else "no bambustudio navigation")
        page.wait_for_timeout(1500)
        check("[4] exactly one hand-off", len(nav) == 1, str(len(nav)))
        check("[4] the button is back to its label", BAMBU in h.inner_text() and BUILDING not in h.inner_text(), repr(h.inner_text()))
    check("[4] console has no errors", not errs, "; ".join(errs[:3]))
    ctx.close()

    # ---- [1] no print scope -> greyed out, sends nothing
    for tag, kw in (("the model has no print parts", {"scopes": ["laser", "bought"], "off": {"print": "none", "step": "setup"}}),
                    ("kit not set up", {"enabled": False, "print_parts": []})):
        ctx, page, errs, nav, st = open_page(b, "mac", **kw)
        h = page.get_by_role("button", name=BAMBU, exact=True)
        if check(f"[1] {tag}: «{BAMBU}» shown", h.count() == 1, f"{h.count()} found"):
            check(f"[1] {tag}: greyed out (aria-disabled)", h.get_attribute("aria-disabled") == "true", repr(h.get_attribute("aria-disabled")))
            h.click(force=True); page.wait_for_timeout(800)
            check(f"[1] {tag}: a click sends no /3mf request and opens nothing", not any("/3mf/" in c for c in st["calls"]) and nav == [],
                  json.dumps([c for c in st["calls"] if "/3mf/" in c] + nav, ensure_ascii=False))
        page.screenshot(path=str(shots / f"1_off_{'none' if 'parts' in tag else 'setup'}.png"))
        ctx.close()

    # ---- [6] English
    ctx, page, errs, nav, st = open_page(b, "mac", lang="en")
    en = btn(page, "#kitRow", BAMBU_EN)
    check(f"[6] en: #kitRow has «{BAMBU_EN}»", en.count() == 1 and en.is_visible(), f"{en.count()} found")
    check(f"[6] en: no «{BAMBU}» on the page", page.get_by_role("button", name=BAMBU, exact=True).count() == 0)
    select_part(page, a.part)
    check(f"[6] en: part card has «{BAMBU_EN}»", btn(page, "#kitCard", BAMBU_EN).count() == 1)
    check("[6] en: console has no errors", not errs, "; ".join(errs[:3]))
    ctx.close()

    # ---- [5] phones: no Bambu buttons (the kit row itself stays)
    for os_ in ("iphone", "android"):
        ctx, page, errs, nav, st = open_page(b, os_)
        check(f"[5] {os_} 390x844: the kit row is there («{PRINT}»)", btn(page, "#kitRow", PRINT).count() == 1)
        check(f"[5] {os_}: no «{BAMBU}» in the kit row", page.get_by_role("button", name=BAMBU, exact=True).count() == 0)
        select_part(page, a.part)
        check(f"[5] {os_}: no «{BAMBU}» in the part card of a print part", page.get_by_role("button", name=BAMBU, exact=True).count() == 0)
        page.screenshot(path=str(shots / f"5_{os_}.png"))
        ctx.close()
    b.close()


with sync_playwright() as p:
    try:
        run(p)
    except Exception as e:  # noqa
        check("scenario ran to the end", False, f"{type(e).__name__}: {str(e)[:300]}")
srv.shutdown()
print("\n| check | result | detail |\n|---|---|---|")
for r in rows:
    print("| " + " | ".join(str(x).replace("|", "/") for x in r) + " |")
sys.exit(0 if ok_all else 1)
