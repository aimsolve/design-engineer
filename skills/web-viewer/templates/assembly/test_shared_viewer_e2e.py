#!/usr/bin/env python3
"""Real-browser check of «one viewer code for all projects, the project gives only data».

Builds sites from importer output with THIS build.py (default = shared mode) and serves them the way the stack will (viewer_stack_server.py:
/_viewer/ from the framework with .js -> .mjs fallback, /<id>/ from the site dir with the shared index.html when the project has none),
then the same page from a --standalone build served by plain `python -m http.server`.
The model: --in, else $VIEWER_TEST_MODEL, else the demo machine (examples/nema-bracket, built once in a temp dir). The machine section
(sim + CAD motion on the shared runtime) needs a sim dir: --sim, else $VIEWER_TEST_SIM; without one it is skipped (the demo has no sim).
The part clicked in 3D: --part, else the biggest distinct part with a STEP and an STL that a click can reach on screen.

  env -u VIRTUAL_ENV uv run --isolated --no-project --with playwright python test_shared_viewer_e2e.py \
      [--in <importer output>] [--sim <machine sim dir>] [--part <part id>] [--swiftshader]
(first run: `... --with playwright playwright install chromium`)

Done-when it serves:  a) the page of a project: same picture, click on a part opens its card, download buttons work, no console errors
                      b) the page's viewer code comes from /_viewer/, not from the project's public/
                      c) a template change is visible on the page without rebuilding the project
                      d) cache-busting stamp on data URLs, the machine's sim/motion on the shared runtime
Exit code 1 on any failed check.
"""
import argparse, json, os, shutil, sys, tempfile, urllib.parse, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from viewer_stack_server import (REPO, RUNTIME_REL, SIM_ENV, TEMPLATE_REL, build, link_input, model_dir, non_background_share,  # noqa: E402
                                 parts_of, serve_plain, serve_stack)

ap = argparse.ArgumentParser()
ap.add_argument("--in", dest="src", default=None, help="importer output dir (model.glb, parts.json, parts/, edges.*); default: $VIEWER_TEST_MODEL, else the demo machine")
ap.add_argument("--sim", default=None, help="machine sim dir with site.json (its 'app' is dropped); default: $VIEWER_TEST_SIM, else that section is skipped")
ap.add_argument("--part", default=None, help="a part id to click in 3D (default: the biggest distinct part with a STEP and an STL that is on screen)")
ap.add_argument("--swiftshader", action="store_true")
a = ap.parse_args()
GPU_ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] if a.swiftshader else ["--use-angle=metal", "--enable-gpu", "--ignore-gpu-blocklist"]
NOROUTER = ["--flash", "none", "--kit", "none", "--feedback", "none"]   # no router behind these test servers
rows, ok_all = [], True


def check(name, ok, detail=""):
    global ok_all
    ok_all &= bool(ok)
    rows.append((name, "PASS" if ok else "FAIL", detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)
    return ok


def built(tag, r):
    return check(f"{tag}: build.py succeeds", r.returncode == 0, (r.stderr or r.stdout).strip().splitlines()[-1] if r.returncode else "")


def get(url):
    try:
        with urllib.request.urlopen(url) as r:
            return r.status, len(r.read())
    except Exception as e:  # noqa
        return getattr(e, "code", 0), 0


def open_page(b, url):
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
    page = ctx.new_page()
    errs, reqs = [], []
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.on("request", lambda r: reqs.append(r.url))
    page.goto(url, wait_until="load")
    page.wait_for_function("window.__viewer && (window.__viewer.ready || window.__viewer.error)", timeout=180000)
    return ctx, page, errs, reqs


def paths(reqs):
    return [urllib.parse.urlsplit(u).path for u in reqs]


def render_share(page):
    """Share of the 3D canvas that is not background, with every overlay (panel, cards, bars) hidden."""
    page.evaluate("document.querySelectorAll('body > *:not(#stage)').forEach(e => { e.dataset.vis = e.style.visibility; e.style.visibility = 'hidden'; })")
    page.wait_for_timeout(300)
    share = non_background_share(page.screenshot())
    page.evaluate("document.querySelectorAll('body > *:not(#stage)').forEach(e => { e.style.visibility = e.dataset.vis || ''; })")
    return share


def click_part(page, part):
    """Real mouse click on a visible pixel of `part`; returns the viewer state after it (or None if the part is not on screen)."""
    page.evaluate(f"window.__viewer.select({json.dumps(part)}, true)")
    page.wait_for_timeout(500)
    page.wait_for_function("!window.__viewer.state().flying", timeout=30000)
    page.evaluate("window.__viewer.select(null)")
    page.wait_for_timeout(200)
    hit = page.evaluate("""(id) => { const v = window.__viewer;
      for (let y = 20; y < innerHeight - 20; y += 12) for (let x = 20; x < innerWidth - 20; x += 12) { const r = v.pick(x, y); if (!r || r.part !== id) continue;
        const e = document.elementFromPoint(x, y); if (e && e.tagName === 'CANVAS') return [x, y]; }   // not under the panel or a card
      return null; }""", part)
    if not hit:
        return None
    page.mouse.click(*hit)
    page.wait_for_timeout(500)
    return page.evaluate("window.__viewer.state()")


def basic_page(b, tag, url, conf_title):
    """a) of the list above on one page: loads, title from config.json, 3D drawn, click -> card, card downloads 200, no console errors."""
    ctx, page, errs, reqs = open_page(b, url)
    err = page.evaluate("window.__viewer.error")
    check(f"{tag}: model loads", not err, str(err or ""))
    check(f"{tag}: tab title = config.json title", page.title() == conf_title, f"{page.title()!r} vs {conf_title!r}")
    check(f"{tag}: panel title = config.json title", page.inner_text("#pTitle").strip() == conf_title, page.inner_text("#pTitle")[:80])
    page.wait_for_timeout(1500)
    share = render_share(page)
    check(f"{tag}: 3D canvas is not blank", share > 0.02, f"{share:.1%} of the canvas differs from the background")
    st = None
    for part in ([a.part] if a.part else CANDIDATES):
        st = click_part(page, part)
        if st and st["selected"] and st["selected"]["part"] == part:
            a.part = part      # the next page clicks the same part
            break
    check(f"{tag}: click on a part opens its card", st and st["infoOpen"] and st["selected"] and st["selected"]["part"] == a.part,
          f"{a.part}: " + json.dumps(st and st["selected"], ensure_ascii=False))
    if st and st["infoOpen"]:
        for sel in ("#dlStep", "#dlStl"):
            href = page.get_attribute(sel, "href")
            full = urllib.parse.urljoin(page.url, href or "")
            s = get(full)
            check(f"{tag}: card download {sel} answers 200", href and s[0] == 200 and s[1] > 0, f"{full} -> {s}")
    page.wait_for_timeout(500)
    check(f"{tag}: console has no errors", not errs, "; ".join(errs[:3]))
    return ctx, page, errs, reqs


tmp = Path(tempfile.mkdtemp(prefix="shared_viewer_e2e_"))
fw = tmp / "framework"                                    # a copy of the framework the server serves /_viewer/ from (edited in section c)
shutil.copytree(REPO / TEMPLATE_REL, fw / TEMPLATE_REL, ignore=shutil.ignore_patterns("__pycache__"))
shutil.copytree(REPO / RUNTIME_REL, fw / RUNTIME_REL)
projects = tmp / "projects"
projects.mkdir()
src = model_dir(a.src)
if a.part:
    CANDIDATES = [a.part]
else:                       # tried in this order until one is reachable by a click (a big part may sit inside a housing)
    CANDIDATES = [p["id"] for p in sorted((p for p in parts_of(src) if not p.get("part_id") and (p.get("files") or {}).get("step")
                                           and (p.get("files") or {}).get("stl")), key=lambda p: -(p.get("volume_mm3") or 0))][:8]
    if not CANDIDATES:
        sys.exit("the model has no distinct part with a STEP and an STL to click (pass --part)")
title = json.loads((src / "parts.json").read_text(encoding="utf-8"))["assembly"]["root"]
srv, base = serve_stack(projects, fw)
procs = []
try:
    with sync_playwright() as p:
        b = p.chromium.launch(args=GPU_ARGS)

        # ---------------------------------------------------------------- a) + b): the stack layout, shared-mode build
        out = projects / "demo-proj"
        built("stack", build(src, out, *NOROUTER))
        url = f"{base}/demo-proj/?app=0"
        ctx, page, errs, reqs = basic_page(b, "stack", url, title)
        pp = paths(reqs)
        local_code = [x for x in pp if x.startswith("/demo-proj/") and (x.endswith((".js", ".mjs")) or "/vendor/" in x)]
        check("stack: no viewer code is loaded from the project's folder", not local_code, ", ".join(local_code[:5]))
        check("stack: three.js and the viewer modules come from /_viewer/", any(x.startswith("/_viewer/vendor/") for x in pp) and any(x.startswith("/_viewer/") and x.endswith((".js", ".mjs")) and "/vendor/" not in x for x in pp),
              ", ".join(x for x in pp if x.startswith("/_viewer/"))[:200])
        if page.evaluate("!!(window.__viewer.edges)"):
            page.wait_for_function("window.__viewer.edges.loaded || window.__viewer.edges.error", timeout=120000)
        check("stack: baked edges load (config.json says there are edges)", page.evaluate("!!(window.__viewer.edges && window.__viewer.edges.loaded)"), json.dumps(page.evaluate("window.__viewer.edges || null")))
        ctx.close()
        # the title comes from config.json at run time, not from a value baked into the page
        conf = json.loads((out / "config.json").read_text(encoding="utf-8"))
        conf["title"] = "Title from config.json · Ø"
        (out / "config.json").write_text(json.dumps(conf, ensure_ascii=False), encoding="utf-8")
        ctx, page, errs, reqs = open_page(b, url)
        check("stack: title edited in config.json shows without a rebuild", page.title() == conf["title"] and page.inner_text("#pTitle").strip() == conf["title"],
              f"tab {page.title()!r}, panel {page.inner_text('#pTitle')[:60]!r}")
        ctx.close()

        # ---------------------------------------------------------------- c) a template change reaches the page without rebuilding the project
        idx = fw / TEMPLATE_REL / "index.html"
        idx.write_text(idx.read_text(encoding="utf-8").replace("</head>", '<meta name="tpl-probe" content="v2"></head>', 1), encoding="utf-8")
        win = fw / TEMPLATE_REL / "win.mjs"
        win.write_text(win.read_text(encoding="utf-8") + '\nwindow.__tplProbe = "v2";\n', encoding="utf-8")
        ctx, page, errs, reqs = open_page(b, url)
        check("template change: edited index.html is served on the project page", page.evaluate("document.querySelector('meta[name=tpl-probe]')?.content") == "v2")
        check("template change: edited viewer module runs on the project page", page.evaluate("window.__tplProbe") == "v2")
        ctx.close()

        # ---------------------------------------------------------------- d) cache-busting stamp on data URLs, new on every build
        def data_queries(reqs):
            q = {}
            for u in reqs:
                sp = urllib.parse.urlsplit(u)
                name = sp.path.rsplit("/", 1)[-1]
                if sp.path.startswith("/demo-proj/") and name in ("model.glb", "parts.json", "edges.json", "edges.bin"):
                    q[name] = sp.query
            return q
        ctx, page, errs, reqs = open_page(b, url)
        q1 = data_queries(reqs)
        ctx.close()
        check("stamp: model.glb, parts.json, edges carry a cache-busting query", set(q1) >= {"model.glb", "parts.json"} and all(q1.values()), json.dumps(q1))
        built("stamp rebuild", build(src, out, *NOROUTER))
        ctx, page, errs, reqs = open_page(b, url)
        q2 = data_queries(reqs)
        ctx.close()
        check("stamp: a rebuild changes the query of every data URL", q1 and all(q2.get(k) and q2[k] != v for k, v in q1.items()), f"{json.dumps(q1)} -> {json.dumps(q2)}")

        # ---------------------------------------------------------------- d) the machine: sim + motion on the shared runtime
        sim_given = a.sim or os.environ.get(SIM_ENV)
        sim_src = Path(sim_given).expanduser() if sim_given else None
        if sim_src is None:
            print(f"SKIP machine section (sim + CAD motion): no sim dir (pass --sim or set ${SIM_ENV})", flush=True)
        elif (sim_src / "site.json").is_file():
            sroot = tmp / "simsrc"                                       # same relative layout as the machine repo (site.json paths point up)
            shutil.copytree(sim_src, sroot / "sim", ignore=shutil.ignore_patterns("node_modules", "framework_patch"))
            site = json.loads((sroot / "sim" / "site.json").read_text(encoding="utf-8"))
            site.pop("app", None)                                        # the operator app build is another repo; not what this test is about
            (sroot / "sim" / "site.json").write_text(json.dumps(site, ensure_ascii=False), encoding="utf-8")
            for x in site.get("extra", []) + [site.get("fw", "../build/fw")]:
                s, d = (sim_src / x).resolve(), (sroot / "sim" / x).resolve()
                if s.exists() and not d.exists():
                    d.parent.mkdir(parents=True, exist_ok=True)
                    (shutil.copytree if s.is_dir() else shutil.copyfile)(s, d)
            sout = projects / "demo-sim"
            if built("machine", build(link_input(src, tmp / "in_sim"), sout, "--sim", sroot / "sim", *NOROUTER)):
                ctx, page, errs, reqs = open_page(b, f"{base}/demo-sim/?app=0")
                page.wait_for_function("window.__viewer.sim || window.__viewer.simError", timeout=60000)
                check("machine: sim mounts", page.evaluate("!!window.__viewer.sim && !window.__viewer.simError"), str(page.evaluate("window.__viewer.simError")))
                page.wait_for_function("window.__viewer.sim.motion || window.__viewer.motionError", timeout=60000)
                check("machine: CAD motion binds", page.evaluate("!window.__viewer.motionError && window.__viewer.sim.motion.bound > 0"), str(page.evaluate("window.__viewer.motionError || window.__viewer.sim.motion.bound")))
                pp = paths(reqs)
                local_rt = [x for x in pp if x.startswith(("/demo-sim/sim/vc/", "/demo-sim/sim/ui.")) or x in ("/demo-sim/sim/motion.js", "/demo-sim/sim/app.js")]   # sim/motion.json is data
                check("machine: sim runtime, sim UI and motion come from /_viewer/, not the project", not local_rt and any(x.startswith("/_viewer/vc/") for x in pp), ", ".join(local_rt[:5]))
                page.wait_for_timeout(1000)
                check("machine: console has no errors", not errs, "; ".join(errs[:3]))
                ctx.close()
        else:
            check("machine: sim dir has a site.json", False, f"no {sim_src}/site.json (pass --sim)")

        # ---------------------------------------------------------------- --standalone: plain static server at the root
        alone = tmp / "alone"
        if built("standalone", build(src, alone, "--standalone", *NOROUTER)):
            pr, sbase = serve_plain(alone)
            procs.append(pr)
            ctx, page, errs, reqs = basic_page(b, "standalone", f"{sbase}/?app=0", title)
            ctx.close()
        b.close()
finally:
    srv.shutdown()
    for pr in procs:
        pr.terminate(); pr.wait(5)
    shutil.rmtree(tmp, ignore_errors=True)

print("\n| check | result | detail |\n|---|---|---|")
for r in rows:
    print("| " + " | ".join(str(x).replace("|", "/") for x in r) + " |")
print(f"\n{sum(r[1] == 'FAIL' for r in rows)} failing, {sum(r[1] == 'PASS' for r in rows)} passing")
sys.exit(0 if ok_all else 1)
