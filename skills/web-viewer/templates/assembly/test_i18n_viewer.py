#!/usr/bin/env python3
"""Real-browser check of the viewer's languages (RU / EN / PL).

Builds a site from importer output with THIS template (build.py --flash none), serves it under /<project>/ the way the stack does
(viewer_stack_server.serve_stack), fakes the router (/kit/**, model.json, the project's locale.json, a clash report) and drives the
page through the doors a client uses: the URL, the language switcher in the panel header, a reload.

  env -u VIRTUAL_ENV uv run --isolated --no-project --with playwright python test_i18n_viewer.py \
      [--in <importer output>] [--part <part id>] [--swiftshader] [--shots <dir>] [--framework <dir>]
(first run: `... --with playwright playwright install chromium`; --framework = build and serve the viewer of another framework checkout)

The model: --in, else $VIEWER_TEST_MODEL, else the demo machine (examples/nema-bracket, built once in a temp dir); the part whose card
opens: --part, else the biggest distinct part. Russian texts the checks expect come from i18n.mjs (ui_texts("ru")).

Model data is exempt from the language rule, so the importer's parts.json is TRANSLITERATED to Latin before the build: every
Cyrillic letter left on the page then comes from the viewer itself.

What is checked:
  [V1] a language switcher with RU / EN / PL in the panel header (#pHead).
  [V2] page language order: URL ?lang= first, then the remembered choice of THIS project (localStorage "eng.lang.<pid>", pid = the
       first path segment), then the project's public/locale.json "lang", then ru. navigator.language is NOT used (a project
       without locale.json is Russian by definition, also for a Russian client with an English browser).
       <html lang> follows it. A project without locale.json gets 204 for it from the stack (like apk/latest.json): the page is ru,
       the console clean.
  [V2p] the remembered choice is per project: a pick on project A does not change project B (B still
       follows its locale.json); ?lang= on A is remembered for A only.
  [V2h] the page does not wait forever for locale.json: one that never answers leaves the page Russian, and panel and model are
       ready long before (held 30 s here).
  [V2c] a locale.json that never answers does not overwrite an eng_lang cookie the browser already has (pl stays pl), and the page
       takes that cookie's language before falling back to ru (asserted: the page is pl).
  [V2a] a browser without AbortSignal.timeout (deleted in an init script): a pl project's locale.json still makes the page pl.
  [V2s] the section headings (#secView / #secHw / #secDl / #secParts .sh) never show Russian on an en / pl page, not even before the
       script ran (with JavaScript off, and as recorded from the first DOM mutation on).
  [V3] picking another language in the switcher shows the page in it; the choice survives a reload (localStorage "eng.lang.<pid>").
  [V4] the page sets cookie eng_lang=<lang> with path=/.
  [V5] with en or pl, the UI text the viewer itself makes has no Cyrillic: panel headings, buttons, hints, the part card labels,
       the kit buttons and their building notice, the feedback drawing UI, the «model updating» banner, the clash banner head
       (text, title, aria-label, placeholder, <option>s of what is visible).
  [V6] every UI element that has a text in ru has one in en and in pl, and it is not Russian (the dictionaries have the same keys).
  [ru] ru keeps its texts (guards: the «view» and «parts» section headings of i18n.mjs).
Exit code 1 on any failed check.
"""
import argparse, atexit, json, re, shutil, sys, tempfile, urllib.parse
from pathlib import Path
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from viewer_stack_server import build, link_input, model_dir, parts_of, serve_stack, ui_texts  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--in", dest="src", default=None, help="importer output dir (model.glb, parts.json, parts/); default: $VIEWER_TEST_MODEL, else the demo machine")
ap.add_argument("--part", default=None, help="a part id to open the card of (default: the biggest distinct part)")
ap.add_argument("--build", default=str(HERE / "build.py"), help="build.py to test (default: this template)")
ap.add_argument("--framework", default=None, help="another framework checkout (the dir holding skills/, or a repo with it in agent/): its build.py, and its viewer served at /_viewer/")
ap.add_argument("--swiftshader", action="store_true")
ap.add_argument("--shots", default=None)
a = ap.parse_args()
FRAMEWORK = HERE.parents[3]   # what /_viewer/ serves (viewer_stack_server: <framework>/skills/web-viewer/templates/assembly)
if a.framework:
    FRAMEWORK = Path(a.framework).expanduser().resolve()
    if not (FRAMEWORK / "skills").is_dir() and (FRAMEWORK / "agent" / "skills").is_dir():
        FRAMEWORK = FRAMEWORK / "agent"
    a.build = str(FRAMEWORK / "skills/web-viewer/templates/assembly/build.py")
shots = Path(a.shots) if a.shots else Path(tempfile.mkdtemp(prefix="i18n_shots_")); shots.mkdir(parents=True, exist_ok=True); print("screenshots:", shots)
if not a.shots:
    atexit.register(shutil.rmtree, shots, True)   # the disk: pass --shots <dir> to keep them
GPU_ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] if a.swiftshader else ["--use-angle=metal", "--enable-gpu", "--ignore-gpu-blocklist"]
PROJ = "lang-proj"
CYR = re.compile("[\u0410-\u044f\u0401\u0451]")      # the Russian alphabet, upper and lower case
RU = ui_texts("ru")
SEC_VIEW, SEC_PARTS = RU["sec"]["view"], RU["sec"]["parts"]
rows, ok_all = [], True


def check(name, ok, detail=""):
    global ok_all
    ok_all &= bool(ok)
    rows.append((name, "PASS" if ok else "FAIL", detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)
    return ok


# ---------------------------------------------------------------- site: Latin model data, this template
LOWER = [chr(c) for c in range(0x430, 0x450)] + ["\u0451"]   # the Russian lower-case letters, then yo
TR = dict(zip(LOWER, ["a", "b", "v", "g", "d", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r",
                    "s", "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya", "e"]))


def latin(v):
    if isinstance(v, str):
        return "".join(TR.get(c, c) if c.islower() else TR.get(c.lower(), c).capitalize() for c in v)
    if isinstance(v, list):
        return [latin(x) for x in v]
    if isinstance(v, dict):
        return {k: latin(x) for k, x in v.items()}
    return v


MODEL = model_dir(a.src)
if not a.part:
    a.part = max((p for p in parts_of(MODEL) if not p.get("part_id")), key=lambda p: p.get("volume_mm3") or 0)["id"]
root = Path(tempfile.mkdtemp(prefix="i18n_site_"))
atexit.register(shutil.rmtree, root, True)   # the built site and its model copy
src = link_input(MODEL, root / "_in", parts_json=latin(json.loads((MODEL / "parts.json").read_text(encoding="utf-8"))))


def latin_glb(glb_in: Path, glb_out: Path):
    """A copy of model.glb whose JSON chunk (node / mesh names: the tree shows them for assembly rows) is transliterated too.
    The BIN chunk is copied byte for byte; the JSON chunk is re-padded with spaces to 4 bytes (glTF 2.0 GLB layout)."""
    import struct
    b = glb_in.read_bytes()
    n = struct.unpack("<I", b[12:16])[0]
    j = json.dumps(latin(json.loads(b[20:20 + n])), ensure_ascii=False).encode()
    j += b" " * (-len(j) % 4)
    rest = b[20 + n:]
    glb_out.unlink(missing_ok=True)
    glb_out.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(j) + len(rest)) + struct.pack("<I4s", len(j), b"JSON") + j + rest)


latin_glb(MODEL / "model.glb", src / "model.glb")
r = build(src, root / PROJ, "--flash", "none", build_py=Path(a.build))
if r.returncode:
    sys.exit("build.py failed:\n" + r.stderr[-2000:])
OTHER = "other-proj"   # a second project with the same site: the remembered choice is per project
(root / OTHER).symlink_to(root / PROJ, target_is_directory=True)
srv, BASE = serve_stack(root, framework=FRAMEWORK)
CONF = json.loads((root / PROJ / "config.json").read_text(encoding="utf-8"))
_glb = (root / PROJ / "model.glb").read_bytes() if (root / PROJ / "model.glb").is_file() else (src / "model.glb").read_bytes()
_gj = json.loads(_glb[20:20 + int.from_bytes(_glb[12:16], "little")])
check("site: model data is Latin (only the viewer can add Cyrillic)",
      not CYR.search(json.dumps(CONF, ensure_ascii=False)) and not CYR.search(json.dumps(_gj, ensure_ascii=False))
      and not CYR.search((root / PROJ / "parts.json").read_text(encoding="utf-8") if (root / PROJ / "parts.json").is_file() else ""),
      CONF.get("title", ""))

# ---------------------------------------------------------------- page JS helpers
UI_ROOTS = ["#panel", "#info", "#fbOpen", "#fbTop", "#fbPop", "#fbPart", "#modelBanner", "#clashHead", "#kitToast"]
COLLECT = """(roots) => {
  const vis = (el) => { if (!el.getClientRects().length) return false; const s = getComputedStyle(el); return s.visibility !== 'hidden' && s.display !== 'none'; };
  const out = [];
  for (const sel of roots) for (const r of document.querySelectorAll(sel)) {
    if (!vis(r)) continue;
    for (const el of [r, ...r.querySelectorAll('*')]) {
      if (!vis(el) && el.tagName !== 'OPTION') continue;
      const key = sel + ' ' + (el.id ? '#' + el.id : el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.split(' ')[0] : ''));
      let own = '';
      for (const n of el.childNodes) if (n.nodeType === 3) own += n.textContent;
      own = own.trim();
      if (own) out.push([key, own]);
      for (const at of ['title', 'aria-label', 'placeholder']) { const v = el.getAttribute(at); if (v && v.trim()) out.push([key + '@' + at, v.trim()]); }
    }
  }
  return out;
}"""


def ui(page, roots=None):
    return page.evaluate(COLLECT, roots or UI_ROOTS)


def cyr_items(items):
    return [(k, v) for k, v in items if CYR.search(v)]


def html_lang(page):
    return page.evaluate("document.documentElement.lang")


# ---------------------------------------------------------------- fake router
LANG_KEY = "eng.lang.{}"   # the remembered choice, per project
HOLD = "hold"               # project_lang=HOLD: locale.json never answers
SH_WATCH = """(() => {   // every text a section heading shows, from the first DOM mutation on (before any page script ran)
  window.__shSeen = [];
  const rec = () => document.querySelectorAll('#secView .sh, #secHw .sh, #secDl .sh, #secParts .sh').forEach((h) => {
    const t = (h.textContent || '').trim(); if (t) window.__shSeen.push(t); });
  new MutationObserver(rec).observe(document, { childList: true, subtree: true, characterData: true });
  document.addEventListener('DOMContentLoaded', rec);
})();"""


def new_ctx(b, locale="ru-RU", stored=None, js=True, cookie=None, no_timeout=False):
    """cookie: an eng_lang the browser already has; no_timeout: a browser without AbortSignal.timeout (older Safari / Chrome)."""
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1, locale=locale, java_script_enabled=js)
    if cookie:
        ctx.add_cookies([{"name": "eng_lang", "value": cookie, "url": BASE + "/"}])
    if no_timeout:
        ctx.add_init_script("try { delete AbortSignal.timeout; } catch (e) {} window.__noTimeout = typeof AbortSignal.timeout === 'undefined';")
    seed = "".join(f"try {{ localStorage.setItem('assembly.sec.{p}.dl', '0'); }} catch (e) {{}}" for p in (PROJ, OTHER))   # the downloads section open
    for p, lang in (stored or {}).items():   # {project: remembered language}
        seed += f"try {{ localStorage.setItem({json.dumps(LANG_KEY.format(p))}, {json.dumps(lang)}); }} catch (e) {{}}"
    ctx.add_init_script(seed)
    ctx.add_init_script(SH_WATCH)
    return ctx


def stored_langs(page):
    """Every eng.lang* key of localStorage: {key: value}."""
    return page.evaluate("(() => { const o = {}; try { for (let i = 0; i < localStorage.length; i++) { const k = localStorage.key(i);"
                         " if (k.startsWith('eng.lang')) o[k] = localStorage.getItem(k); } } catch (e) {} return o; })()")


def new_page(b, query="", locale="ru-RU", stored=None, project_lang=None, building=False, clash=False, kit_building=False,
             ctx=None, proj=PROJ, langs=None, wait=True, js=True):
    """stored: {project: lang} remembered before load (or a plain lang = for `proj`); project_lang: proj's locale.json lang
    (None = 204, HOLD = never answers); langs: {project: lang} for the other project's locale.json."""
    if isinstance(stored, str):
        stored = {proj: stored}
    ctx = ctx or new_ctx(b, locale, stored, js)
    page = ctx.new_page()
    errs = []
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errs.append(str(e)))
    held = page.__dict__.setdefault("_held", [])
    langs = dict(langs or {}, **{proj: project_lang})

    def locale_json(route):
        pr = urllib.parse.urlparse(route.request.url).path.strip("/").split("/")[0]
        lang = langs.get(pr)
        if lang == HOLD:
            held.append(route)   # never answered (aborted when the context closes)
            return
        if lang:
            return route.fulfill(status=200, content_type="application/json", body=json.dumps({"lang": lang}))
        route.fulfill(status=204, body="")   # the stack: no project language -> 204 (nginx stack.conf)

    def kit(route):
        path = urllib.parse.unquote(urllib.parse.urlparse(route.request.url).path)
        if path.endswith("/status"):
            body = {"enabled": True, "fresh": not kit_building, "building": False, "since": None, "parts": [a.part], "nodes": [],
                    "scopes": ["print", "laser", "bought", "step"], "off": {}, "model": {"building": False}}
            return route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
        if kit_building:
            return route.fulfill(status=202, content_type="application/json", body=json.dumps({"building": True, "since": 0, "est_secs": 60}))
        return route.fulfill(status=200, body=b"PK\x03\x04", headers={"Content-Type": "application/zip",
                                                                      "Content-Disposition": "attachment; filename=\"kit.zip\""})
    page.route("**/kit/**", kit)
    page.route("**/hw/**", lambda r: r.fulfill(status=404, body=""))
    page.route("**/model.json*", lambda r: r.fulfill(status=200, content_type="application/json",
                                                     body=json.dumps({"v": 0, "built": 0, "building": building})))
    page.route(re.compile(r".*/[a-z0-9-]+/locale\.json(\?.*)?$"), locale_json)
    if clash:
        conf = dict(CONF, clearance="clearance.json")
        page.route(f"**/{proj}/config.json*", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(conf)))
        rep = {"clashes": [{"a": a.part, "b": a.part, "volume_mm3": 12.5, "t_s": 1.0}]}
        page.route(f"**/{proj}/clearance.json*", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(rep)))
    page.goto(f"{BASE}/{proj}/?app=0{'&' + query if query else ''}", wait_until="load")
    if wait:
        page.wait_for_function("window.__viewer && (window.__viewer.ready || window.__viewer.error)", timeout=120000)
        page.wait_for_timeout(1500)
    return ctx, page, errs


def switcher(page):
    """The RU / EN / PL controls in the panel header: {label: element handle}."""
    hs = page.query_selector_all("#pHead *")
    out = {}
    for h in hs:
        t = (h.evaluate("(e) => e.tagName === 'OPTION' || !e.children.length ? e.textContent.trim() : ''") or "").upper()
        if t in ("RU", "EN", "PL") and t not in out:
            out[t] = h
    return out


def settled(page, want, timeout=60):
    """Wait until the page shows `want` and the viewer is ready again. A pick may reload the page by itself (the contract does not
    say how it applies the language), so evaluation errors while a navigation replaces the document are retried, not failures."""
    import time
    t = time.time() + timeout
    while time.time() < t:
        try:
            if page.evaluate("document.documentElement.lang === %s && !!window.__viewer && !!(window.__viewer.ready || window.__viewer.error)"
                             % json.dumps(want)):
                page.wait_for_timeout(1500)   # the async parts (kit, banners) as on a first load
                return True
        except Exception:  # noqa  (execution context destroyed by the page's own reload)
            pass
        page.wait_for_timeout(250)
    return False


def pick(page, label):
    h = switcher(page).get(label)
    if not h:
        return False
    def act():
        if h.evaluate("(e) => e.tagName") == "OPTION":
            sel = h.evaluate_handle("(e) => e.closest('select')")
            sel.as_element().select_option(value=h.get_attribute("value") or label.lower())
        else:
            h.click()
    try:   # the page may apply the pick by reloading itself: wait for that load, so the old document is never the one checked
        with page.expect_navigation(wait_until="load", timeout=5000):
            act()
    except Exception:  # noqa  (no navigation: the page switched in place)
        pass
    settled(page, label.lower())
    return True


def lang_cookie(ctx):
    return [c for c in ctx.cookies() if c["name"] == "eng_lang"]


def open_card(page):
    page.evaluate(f"window.__viewer.select({json.dumps(a.part)}, true)")
    page.wait_for_timeout(600)
    return page.is_visible("#info")


def open_drawing(page):
    if not page.query_selector("#fbOpen"):
        return False
    page.click("#fbOpen")
    page.wait_for_timeout(500)
    pen = page.query_selector('#fbBar button[data-tool="pen"]')
    if pen:
        pen.click()
    page.mouse.move(700, 400); page.mouse.down(); page.mouse.move(760, 440, steps=6); page.mouse.up()
    page.wait_for_timeout(600)
    return page.is_visible("#fbTop")


def click_first_kit_button(page):
    b = page.query_selector("#secDl .sb button")
    if not b:
        return False
    b.click()
    page.wait_for_timeout(1500)
    return True


# ---------------------------------------------------------------- the checks
def run(p):
    b = p.chromium.launch(args=GPU_ARGS)

    # [V1] switcher; [ru] guard
    ctx, page, errs = new_page(b)
    sw = switcher(page)
    check("[V1] panel header has RU / EN / PL", set(sw) == {"RU", "EN", "PL"}, f"found {sorted(sw)}")
    check(f"[ru] default (navigator ru-RU) is Russian: «{SEC_VIEW}» / «{SEC_PARTS}»",
          page.inner_text("#secView h3").strip() == SEC_VIEW and page.inner_text("#secParts h3").strip() == SEC_PARTS and html_lang(page) == "ru",
          f"lang={html_lang(page)!r}")
    ru_items = ui(page, ["#panel"])
    page.screenshot(path=str(shots / "ru.png"))
    ctx.close()

    # [V2] order
    cases = [  # name, new_page kwargs, expected lang
        ("?lang=en", dict(query="lang=en"), "en"),
        ("?lang=pl", dict(query="lang=pl"), "pl"),
        ("?lang=pl beats remembered en", dict(query="lang=pl", stored="en"), "pl"),
        ("?lang=ru beats remembered pl", dict(query="lang=ru", stored="pl"), "ru"),
        ("remembered en beats locale.json pl", dict(stored="en", project_lang="pl"), "en"),
        ("locale.json pl beats navigator en-US", dict(project_lang="pl", locale="en-US"), "pl"),
        ("locale.json ru beats navigator en-US", dict(project_lang="ru", locale="en-US"), "ru"),
        ("navigator pl-PL without locale.json -> ru (the browser is not asked)", dict(locale="pl-PL"), "ru"),
        ("navigator en-US without locale.json -> ru (the browser is not asked)", dict(locale="en-US"), "ru"),
        ("navigator de-DE without locale.json -> ru", dict(locale="de-DE"), "ru"),
        ("remembered pl beats locale.json en", dict(stored="pl", project_lang="en"), "pl"),
    ]
    for name, kw, want in cases:
        ctx, page, _ = new_page(b, **kw)
        got = html_lang(page)
        bad = cyr_items(ui(page, ["#panel"]))
        shown_ok = (not bad) if want != "ru" else bool(bad)
        check(f"[V2] {name}: page is {want}", got == want and shown_ok, f"<html lang={got!r}>, Cyrillic items {len(bad)}: {bad[:2]}")
        ctx.close()

    # [V2] an existing Russian project: no locale.json (204), an English browser -> Russian page, nothing in the console
    ctx, page, errs = new_page(b, locale="en-US")
    check("[V2] no locale.json (204) + navigator en-US: page is ru",
          html_lang(page) == "ru" and page.inner_text("#secView h3").strip() == SEC_VIEW, f"<html lang={html_lang(page)!r}>")
    check("[V2] no locale.json (204): console clean", not errs, "; ".join(errs[:3]))
    ctx.close()

    # [V2p] the remembered choice is per project: one browser profile, two projects
    ctx = new_ctx(b)
    _, pa, _ = new_page(b, ctx=ctx, proj=PROJ, langs={OTHER: "pl"})
    picked = pick(pa, "EN")
    check("[V2p] project A: switcher EN turns A English", picked and html_lang(pa) == "en", html_lang(pa))
    _, pb, _ = new_page(b, ctx=ctx, proj=OTHER, langs={PROJ: None}, project_lang="pl")
    check("[V2p] project B (locale.json pl) still follows its locale.json after a pick on A", html_lang(pb) == "pl", html_lang(pb))
    _, pc, _ = new_page(b, ctx=ctx, proj=OTHER, project_lang=None)
    check("[V2p] project B without locale.json stays Russian after a pick on A", html_lang(pc) == "ru", html_lang(pc))
    st = stored_langs(pc)
    check(f"[V2p] only {LANG_KEY.format(PROJ)} is stored, no global eng.lang", st == {LANG_KEY.format(PROJ): "en"}, json.dumps(st))
    ctx.close()

    ctx = new_ctx(b)
    _, pa, _ = new_page(b, ctx=ctx, proj=PROJ, query="lang=pl")
    _, pa2, _ = new_page(b, ctx=ctx, proj=PROJ)
    check("[V2p] ?lang=pl on A is remembered for A (A without ?lang= is pl)", html_lang(pa2) == "pl", html_lang(pa2))
    _, pb, _ = new_page(b, ctx=ctx, proj=OTHER, project_lang=None)
    check("[V2p] ...and for A only (B without locale.json is ru)", html_lang(pb) == "ru", html_lang(pb))
    _, pb2, _ = new_page(b, ctx=ctx, proj=OTHER, project_lang="en")
    check("[V2p] ...B with locale.json en is en", html_lang(pb2) == "en", html_lang(pb2))
    ctx.close()

    ctx, page, _ = new_page(b, stored={OTHER: "pl"}, project_lang="en")
    check("[V2p] a choice remembered for another project does not apply here (locale.json en -> en)", html_lang(page) == "en", html_lang(page))
    ctx.close()

    # [V2h] locale.json never answers: Russian, and the page does not wait for it
    import time as _t
    t0 = _t.time()
    ctx, page, _ = new_page(b, project_lang=HOLD, wait=False)
    try:
        page.wait_for_function("window.__viewer && (window.__viewer.ready || window.__viewer.error)", timeout=30000)
        ready = _t.time() - t0
    except Exception:  # noqa
        ready = None
    held = len(page.__dict__.get("_held", []))
    check("[V2h] locale.json held: the model is ready well before 30 s", ready is not None and ready < 25 and held >= 1,
          f"ready after {ready if ready is None else round(ready, 1)} s, locale.json requests held: {held}")
    sh = page.evaluate("document.querySelector('#secView .sh') && document.querySelector('#secView .sh').textContent.trim()") if ready else None
    check("[V2h] locale.json held: the page is Russian (panel filled)", ready is not None and html_lang(page) == "ru" and sh == SEC_VIEW,
          f"lang={html_lang(page) if ready else None!r}, heading={sh!r}")
    for r in page.__dict__.get("_held", []):   # end the held request ourselves: no noise when the context closes
        try:
            r.abort()
        except Exception:  # noqa
            pass
    ctx.close()

    # [V2c] an eng_lang cookie the browser already has survives a locale.json that never answers, and is the fallback before ru
    ctx = new_ctx(b, cookie="pl")
    _, page, _ = new_page(b, ctx=ctx, project_lang=HOLD)
    page.wait_for_timeout(4000)   # well past the page's own locale.json timeout
    ck = lang_cookie(ctx)
    check("[V2c] locale.json held: the existing cookie eng_lang=pl is not overwritten", [c["value"] for c in ck] == ["pl"], json.dumps(ck))
    check("[V2c] locale.json held: the page uses the cookie's pl before falling back to ru", html_lang(page) == "pl", html_lang(page))
    for r in page.__dict__.get("_held", []):
        try:
            r.abort()
        except Exception:  # noqa
            pass
    ctx.close()

    # [V2a] a browser without AbortSignal.timeout: a pl project's locale.json still makes the page pl
    ctx = new_ctx(b, no_timeout=True)
    _, page, errs = new_page(b, ctx=ctx, project_lang="pl")
    check("[V2a] harness: AbortSignal.timeout is really missing in this page", page.evaluate("window.__noTimeout === true"))
    check("[V2a] no AbortSignal.timeout: a pl project's page is pl", html_lang(page) == "pl", html_lang(page))
    check("[V2a] no AbortSignal.timeout: console clean", not errs, "; ".join(errs[:3]))
    ctx.close()

    # [V2s] section headings: never Russian on an en / pl page, not before the script ran either
    for lang in ("en", "pl"):
        ctx, page, _ = new_page(b, query=f"lang={lang}", wait=False, js=False)
        page.wait_for_timeout(500)
        raw = page.evaluate("[...document.querySelectorAll('#secView .sh, #secHw .sh, #secDl .sh, #secParts .sh')].map((h) => h.textContent.trim())")
        check(f"[V2s] {lang}, JavaScript off: no Russian in the section headings of the HTML", raw and not any(CYR.search(t) for t in raw), json.dumps(raw, ensure_ascii=False))
        ctx.close()
        ctx, page, _ = new_page(b, query=f"lang={lang}")
        seen = page.evaluate("window.__shSeen || []")
        bad = sorted({t for t in seen if CYR.search(t)})
        check(f"[V2s] {lang}: a section heading never showed Russian while the page loaded", seen and not bad, f"{len(seen)} texts seen, Russian: {bad[:4]}")
        ctx.close()

    # [V3] [V4] pick EN in the switcher, reload
    ctx, page, _ = new_page(b)
    picked = pick(page, "EN")
    bad = cyr_items(ui(page, ["#panel"]))
    check("[V3] switcher EN: the page turns English", picked and html_lang(page) == "en" and not bad, f"lang={html_lang(page)!r} {bad[:2]}")
    stored = stored_langs(page)
    check(f"[V3] the choice is remembered in localStorage {LANG_KEY.format(PROJ)} (and nowhere else)",
          stored == {LANG_KEY.format(PROJ): "en"}, json.dumps(stored))
    ck = lang_cookie(ctx)
    check("[V4] cookie eng_lang=en, path=/", any(c["value"] == "en" and c["path"] == "/" for c in ck), json.dumps(ck))
    page.reload(wait_until="load")
    page.wait_for_function("window.__viewer && (window.__viewer.ready || window.__viewer.error)", timeout=120000)
    page.wait_for_timeout(1500)
    check("[V3] after a reload the page is still English", html_lang(page) == "en" and not cyr_items(ui(page, ["#panel"])), html_lang(page))
    picked = pick(page, "PL")
    check("[V3] switcher PL: the page turns Polish", picked and html_lang(page) == "pl" and not cyr_items(ui(page, ["#panel"])), html_lang(page))
    check("[V4] cookie follows the pick: eng_lang=pl", any(c["value"] == "pl" and c["path"] == "/" for c in lang_cookie(ctx)), json.dumps(lang_cookie(ctx)))
    ctx.close()

    ctx, page, _ = new_page(b, query="lang=pl")
    check("[V4] ?lang=pl sets cookie eng_lang=pl, path=/", any(c["value"] == "pl" and c["path"] == "/" for c in lang_cookie(ctx)), json.dumps(lang_cookie(ctx)))
    ctx.close()

    # [V5] every surface in en and pl; [V6] same keys as ru
    texts = {}
    for lang in ("en", "pl"):
        ctx, page, errs = new_page(b, query=f"lang={lang}", kit_building=True)
        panel = ui(page, ["#panel"])
        check(f"[V5] {lang}: panel has no Cyrillic", panel and not cyr_items(panel), str(cyr_items(panel)[:3]))
        texts[lang] = dict(panel)
        kit_shown = page.is_visible("#secDl") and bool(page.query_selector("#secDl .sb button"))
        check(f"[V5] {lang}: kit buttons shown", kit_shown)
        if kit_shown and click_first_kit_button(page):
            items = ui(page, ["#secDl", "#kitToast", "#info"])
            check(f"[V5] {lang}: kit buttons and their building notice have no Cyrillic", not cyr_items(items), str(cyr_items(items)[:3]))
        check(f"[V5] {lang}: the part card opens", open_card(page))
        card = ui(page, ["#info"])
        check(f"[V5] {lang}: part card labels have no Cyrillic", card and not cyr_items(card), str(cyr_items(card)[:3]))
        page.screenshot(path=str(shots / f"{lang}_card.png"))
        page.evaluate("window.__viewer.select(null)")
        page.wait_for_timeout(300)
        drawn = open_drawing(page)
        fb = ui(page, ["#fbOpen", "#fbTop", "#fbPop"])
        check(f"[V5] {lang}: feedback drawing UI opens", drawn and fb)
        check(f"[V5] {lang}: feedback drawing UI has no Cyrillic", fb and not cyr_items(fb), str(cyr_items(fb)[:3]))
        page.screenshot(path=str(shots / f"{lang}_draw.png"))
        check(f"[reg] {lang}: console clean", not errs, "; ".join(errs[:3]))
        ctx.close()

        ctx, page, _ = new_page(b, query=f"lang={lang}", building=True)
        page.wait_for_selector("#modelBanner", timeout=15000) if page.query_selector("#modelBanner") is None else None
        ban = ui(page, ["#modelBanner"])
        check(f"[V5] {lang}: «model updating» banner shown without Cyrillic", ban and not cyr_items(ban), str(ban[:2]))
        ctx.close()

        ctx, page, _ = new_page(b, query=f"lang={lang}", clash=True)
        try:
            page.wait_for_selector("#clashHead", timeout=15000)
        except Exception:  # noqa
            pass
        cl = ui(page, ["#clashHead"])
        check(f"[V5] {lang}: clash banner head shown without Cyrillic", cl and not cyr_items(cl), str(cl[:2]))
        ctx.close()

    ru = dict(ru_items)
    for lang in ("en", "pl"):
        missing = [k for k in ru if CYR.search(ru[k]) and (k not in texts[lang] or CYR.search(texts[lang][k]))]
        check(f"[V6] {lang}: every panel text ru has is translated", not missing, str(missing[:5]))
    differ = [k for k in ru if k in texts["en"] and k in texts["pl"] and CYR.search(ru[k]) and texts["en"][k] == texts["pl"][k]]
    check("[V6] en and pl are different dictionaries (most Russian texts differ between them)",
          len(differ) < max(1, len([k for k in ru if CYR.search(ru[k])]) // 2), f"{len(differ)} identical: {differ[:5]}")
    b.close()


with sync_playwright() as p:
    run(p)
srv.shutdown()
print("\n| check | result | detail |\n|---|---|---|")
for r in rows:
    print("| " + " | ".join(str(x).replace("|", "/") for x in r) + " |")
sys.exit(0 if ok_all else 1)
