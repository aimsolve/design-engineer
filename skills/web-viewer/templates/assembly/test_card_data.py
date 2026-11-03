#!/usr/bin/env python3
"""Part card driven by parts.json data in the shared viewer: optional part fields instead of a per-project patched page.

Optional part fields `thickness` (text, e.g. "2 mm") and `density` (g/cm³): a «sheet thickness» row right before the module row, mass =
volume × the part's density, no mass on parts without a density (bought), no «from the name» note. A parts.json without them renders as
before: mass = volume × 7.85 with the estimate note, the note on the material, no thickness row.

Two sites built with THIS build.py from the same importer output (only parts.json differs), served like the stack (viewer_stack_server.py,
/<id>/). The model: --in, else $VIEWER_TEST_MODEL, else the demo machine (examples/nema-bracket, built once in a temp dir). Three distinct
parts of it play the sheet part (density + thickness), a made part (density only) and a bought part (neither). The page is in its
default language (ru); every text the checks expect comes from i18n.mjs.
  env -u VIRTUAL_ENV uv run --isolated --no-project --with playwright python test_card_data.py [--in <importer output>] [--swiftshader]
Exit code 1 on any failed check.
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
a = ap.parse_args()
GPU_ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] if a.swiftshader else ["--use-angle=metal", "--enable-gpu", "--ignore-gpu-blocklist"]
NOROUTER = ["--flash", "none", "--kit", "none", "--feedback", "none"]
T = ui_texts("ru")
rows, ok_all = [], True


def check(name, ok, detail=""):
    global ok_all
    ok_all &= bool(ok)
    rows.append((name, "PASS" if ok else "FAIL", detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)


def card(page, pid):
    page.evaluate(f"window.__viewer.select({json.dumps(pid)})")
    page.wait_for_timeout(150)
    return page.evaluate("[...document.querySelectorAll('#infoP dt')].map(dt => [dt.textContent, dt.nextElementSibling ? dt.nextElementSibling.textContent : ''])")


def row(r, label):
    return next((v for k, v in r if k == label), None)


def index_of(r, label):
    return next((i for i, (k, _) in enumerate(r) if k == label), -1)


def num(v, d):        # the page's number format (index.html `num`)
    x = round(v * 10 ** d) / 10 ** d
    return (str(int(x)) if x == int(x) else repr(x)).replace(".", T["decimal"])


def mass_text(kg):    # index.html `massText`
    return f"{num(kg, 2)} {T['kg']}" if kg >= 1 else f"{num(kg * 1000, 1)} {T['g']}"


src = model_dir(a.src)
cat = json.loads((src / "parts.json").read_text(encoding="utf-8"))
distinct = sorted((p for p in cat["parts"] if not p.get("part_id") and p.get("volume_mm3")), key=lambda p: -p["volume_mm3"])
if len(distinct) < 3:
    sys.exit(f"the model needs 3 distinct parts with a volume, has {len(distinct)}")
SHEET, MADE, BOUGHT = (distinct[i] for i in (0, 1, len(distinct) - 1))
RICH_MATERIAL = "Aluminium 5052, sheet"
rich = copy.deepcopy(cat)                     # a parts.json whose materials come from the model
for p in rich["parts"]:
    if p["id"] == SHEET["id"]:
        p.update(material=RICH_MATERIAL, density=2.7, thickness="2 mm")
    elif p["id"] == MADE["id"]:
        p.update(material="Stainless steel", density=7.9)
tmp = Path(tempfile.mkdtemp(prefix="card_data_"))
projects = tmp / "projects"; projects.mkdir()
for pid, catalogue in (("demo-rich", rich), ("demo-plain", cat)):
    r = build(link_input(src, tmp / f"in-{pid}", catalogue), projects / pid, *NOROUTER)
    check(f"{pid}: build.py succeeds", r.returncode == 0, r.stderr.strip()[-200:])
srv, base = serve_stack(projects)
try:
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=GPU_ARGS)
        page = b.new_page(viewport={"width": 1440, "height": 900})
        errs = []
        page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errs.append(str(e)))

        # ---- parts.json with thickness / density
        page.goto(f"{base}/demo-rich/?app=0", wait_until="load")
        page.wait_for_function("window.__viewer && (window.__viewer.ready || window.__viewer.error)", timeout=180000)
        r = card(page, SHEET["id"])
        it, im = index_of(r, T["thickness"]), index_of(r, T["module"])
        check("thickness: a part with `thickness` has a thickness row", it >= 0, json.dumps(r, ensure_ascii=False))
        check("thickness: the row shows the part's value", row(r, T["thickness"]) == "2 mm", str(row(r, T["thickness"])))
        check("thickness: the row is right before the module row", it >= 0 and it == im - 1, f"thickness at {it}, module at {im}")
        want = mass_text(SHEET["volume_mm3"] * 2.7e-6)
        check(f"density: mass = volume × the part's density ({want})", want in (row(r, T["mass"]) or ""), str(row(r, T["mass"])))
        check("density: material from the model shows no «from the name» note",
              row(r, T["material"]) == RICH_MATERIAL, str(row(r, T["material"])))
        r = card(page, MADE["id"])
        check("density: a made part with density and no thickness has no thickness row", index_of(r, T["thickness"]) < 0 and index_of(r, T["module"]) >= 0,
              json.dumps(r, ensure_ascii=False))
        want = mass_text(MADE["volume_mm3"] * 7.9e-6)
        check(f"density: its mass uses its own density ({want})", want in (row(r, T["mass"]) or ""), str(row(r, T["mass"])))
        check("density: its material shows no «from the name» note", T["matNote"] not in (row(r, T["material"]) or T["matNote"]), str(row(r, T["material"])))
        r = card(page, BOUGHT["id"])
        check("density: with densities in the catalogue a bought part (no density) shows no mass", index_of(r, T["mass"]) < 0, json.dumps(r, ensure_ascii=False))
        check("density: the bought part's card still has its other rows", index_of(r, T["module"]) >= 0 and index_of(r, T["size"]) >= 0,
              json.dumps(r, ensure_ascii=False))
        check("density: the bought part shows no «from the name» note", T["matNote"] not in json.dumps(r, ensure_ascii=False), json.dumps(r, ensure_ascii=False))
        check("density: the bought part has no volume row", index_of(r, T["volume"]) < 0, json.dumps(r, ensure_ascii=False))

        # ---- parts.json without these fields: as before
        page.goto(f"{base}/demo-plain/?app=0", wait_until="load")
        page.wait_for_function("window.__viewer && (window.__viewer.ready || window.__viewer.error)", timeout=180000)
        for p in (SHEET, MADE, BOUGHT):
            got = card(page, p["id"])
            labels = [k for k, _ in got]
            want_mass = T["massNote"].replace("{m}", mass_text(p["volume_mm3"] * 7.85e-6))
            want_mat = T["materials"].get(p.get("material"), p.get("material")) + T["matNote"] if p.get("material") else None
            check(f"default: card of {p['id']}: no thickness row, mass = volume × 7.85 with the estimate note, the note on the material",
                  T["thickness"] not in labels and row(got, T["mass"]) == want_mass and labels[-2:] == [T["volume"], T["mass"]]
                  and (want_mat is None or row(got, T["material"]) == want_mat),
                  json.dumps(got, ensure_ascii=False)[:300])
        check("console has no errors", not errs, "; ".join(errs[:3]))
        b.close()
finally:
    srv.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)

print("\n| check | result | detail |\n|---|---|---|")
for x in rows:
    print("| " + " | ".join(str(c).replace("|", "/") for c in x) + " |")
print(f"\n{sum(x[1] == 'FAIL' for x in rows)} failing, {sum(x[1] == 'PASS' for x in rows)} passing")
sys.exit(0 if ok_all else 1)
