#!/usr/bin/env python3
"""Assembly-mode site builder: template + importer output -> a static folder to serve over HTTP.

  python build.py --in <step-import out dir> --out <site dir> [--title "..."] [--sim <machine sim dir>] [--flash DIR|none] [--standalone]

One viewer for every project: the viewer code (index.html, the modules here, vendor/, the sim runtime) is served by the stack at /_viewer/
straight from the framework, and /<project>/ without its own index.html gets this index.html. So --out gets ONLY the project's data: model.glb, parts.json,
parts/, edges.json/.bin (if present in --in), config.json (title, up, which layers exist, build stamp `v`), motion.json, the --world module, sim/ and fw/.
--standalone also writes index.html and a copy of the viewer code under <out>/_viewer/: serve <out> at the server root (python3 -m http.server -d <out>) for local runs.
--sim <dir> adds the machine layer (virtual controllers, see skills/virtual-controllers): <dir>/site.json names the browser-side files:
  {"entry": "machine.mjs", "files": ["machine.mjs", "world.mjs"], "fw": "../build/fw", "extra": ["../mechanics/kinematics/motion.json"]}   (paths relative to <dir>; a file lands in site/sim/ under its basename)
  or, for a device derived from the model (no machine.mjs): {"device": "../build/model/device.json", "fw": "../build/fw"} (copied to sim/device.json)
-> site/sim/{config.json, ui.mjs, <files>, vc/<framework runtime>}, site/fw/{*.mjs,*.wasm,index.json}. Without --sim the page has no machine section.
site.json may carry an "app" entry = the machine's own operator app in a docked panel (app_panel.mjs, BLE bridge to the named board):
  "app": {"path": "app/", "title": "...", "board": "master", "transport": "ble-bridge", "open": "desktop",
          "source": {"repo": "../../app-repo", "remote": "<git url>", "branch": "...", "commit": "<full sha>", "build": "build/web", "build_cmd": "..."}}
The app's web build (source.repo/source.build) is copied to <site>/<path>. The build FAILS (with the fix) when it is missing, older than the recorded commit,
or its repo checkout is not at that commit; <base href> in its index.html is rewritten to "./" so it works under any URL prefix. Without "source"
use "build": "<dir relative to sim/>" (no linkage checks).
No CDN at runtime.
"""
import argparse, json, re, shutil, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else p.stat().st_size


def mb(n: int) -> str:
    return f"{n / 1e6:.1f} MB"


RUNTIME = HERE.parents[2] / "virtual-controllers" / "runtime"
BROWSER_RUNTIME = ["index.mjs", "sim.mjs", "devices.mjs", "vm.mjs", "chips.mjs", "device.mjs", "device_i18n.mjs"]   # node.mjs is node-only, never shipped
VIEWER_CODE = ["view_fx.js", "win.mjs", "feedback.mjs", "kit.mjs", "flash.mjs", "hw.mjs", "motion.mjs", "sim_ui.mjs", "app_panel.mjs", "device_ui.mjs", "wires.mjs", "i18n.mjs"]   # what /_viewer/ serves besides index.html, vendor/, vc/


def make_writable(root: Path) -> None:
    """Files copied from the framework mount (/opt/design-engineer, :ro) must not stay read-only: the next build has to delete them."""
    for p in [root, *root.rglob("*")]:
        if not p.is_symlink():
            p.chmod(0o755 if p.is_dir() else 0o644)


def copy_tree(src: Path, dst: Path, **kw) -> None:
    shutil.copytree(src, dst, copy_function=shutil.copyfile, **kw)   # copyfile = contents only, no source mode bits
    make_writable(dst)


def copy_file(src: Path, dst: Path) -> None:
    shutil.copyfile(src, dst)
    dst.chmod(0o644)


def remove_tree(path: Path) -> None:
    """rmtree that also removes what an earlier build left read-only (a file or dir without write permission)."""
    if path.exists():
        make_writable(path)
        shutil.rmtree(path)


def put_js(src: Path, dst: Path, vc: str = "") -> None:
    """Copy a module to the site as .js with '.mjs' references rewritten: most static hosts serve .mjs as application/octet-stream,
    and browsers refuse to run a module script with a non-JavaScript MIME type. The repo keeps .mjs so node can import the same files.
    vc = the file extension of the shared sim runtime: a machine module's './vc/x.mjs' import goes to /_viewer/vc/ (one runtime instance per page,
    the one sim_ui imports: .mjs as the stack serves the framework, .js in a --standalone copy)."""
    dst = dst.with_suffix(".js") if dst.suffix == ".mjs" else dst
    text = src.read_text(encoding="utf-8").replace(".mjs", ".js")
    if vc:
        text = re.sub(r"""(['"])\./vc/([\w./-]+?)\.js(['"])""", lambda m: f"{m[1]}/_viewer/vc/{m[2]}{vc}{m[3]}", text)
    dst.write_text(text, encoding="utf-8")


def put_viewer(out: Path, sim: bool) -> None:
    """--standalone: index.html + the viewer code under <out>/_viewer/, laid out as the stack serves it (modules as .js; the sim runtime only with --sim)."""
    copy_file(HERE / "index.html", out / "index.html")
    # The page probes apk/latest.json (the machine's released Android app). A plain static server answers 404 and the browser logs it
    # as an error; the stack's nginx answers 204 instead, and a local copy never gets a real APK, so an empty one does here.
    (out / "apk").mkdir(exist_ok=True)
    (out / "apk" / "latest.json").write_text("{}", encoding="utf-8")
    if not (out / "locale.json").exists():   # the same for the page language: {} = the Russian default
        (out / "locale.json").write_text("{}", encoding="utf-8")
    v = out / "_viewer"
    copy_tree(HERE / "vendor", v / "vendor")
    for f in VIEWER_CODE:
        put_js(HERE / f, v / f)
    if not sim:
        return
    (v / "vc").mkdir()
    for f in BROWSER_RUNTIME:
        put_js(RUNTIME / f, v / "vc" / f)


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def add_app(sd: Path, app: dict, out: Path) -> dict:
    """Copy the machine app's web build to <site>/<app.path> after checking it belongs to the commit recorded in site.json."""
    if app.get("url"):   # an external app (a workspace's shared build, e.g. /ws/<id>/app/): nothing to copy, the panel iframes the URL
        if not app.get("board"):
            sys.exit("--sim: site.json app.board is required")
        conf = {k: v for k, v in app.items() if k not in ("build", "source")}
        conf["path"] = app["url"]
        print(f"app: external {app['url']}")
        return conf
    for k in ("path", "board"):
        if not app.get(k):
            sys.exit(f"--sim: site.json app.{k} is required")
    src = app.get("source")
    if src:
        repo = (sd / src["repo"]).resolve()
        build = repo / src.get("build", "build/web")
        want = src["commit"]
        how = src.get("build_cmd", "the app's web build")
        if not (repo / ".git").exists():
            sys.exit(f"--sim: app repo {repo} (site.json app.source.repo) is not a git checkout")
        if not (build / "index.html").is_file():
            sys.exit(f"--sim: app build missing: {build / 'index.html'}\n  build it in {repo}:  {how}")
        ct = git(repo, "show", "-s", "--format=%ct", want)
        if not ct:
            sys.exit(f"--sim: recorded app commit {want} is not in {repo}: fetch it, or update site.json app.source.commit")
        built = max(f.stat().st_mtime for f in build.iterdir() if f.is_file())   # the build tool rewrites index.html / bootstrap on every run, even when compiled output is up to date
        if built < int(ct):
            sys.exit(f"--sim: app build {build} is OLDER than the recorded commit {want[:10]} (built {built:.0f}, commit {ct})\n  rebuild it in {repo}:  {how}")
        head = git(repo, "rev-parse", "HEAD")
        if head and head != want:
            sys.exit(f"--sim: app repo {repo} is at {head[:10]} but site.json records {want[:10]}: check out the recorded commit and rebuild, or update the record")
        dirty = git(repo, "status", "--porcelain", "--untracked-files=no")
        if dirty:
            print(f"warning: app repo {repo} has uncommitted changes; the build may not match commit {want[:10]}", file=sys.stderr)
    else:
        build = (sd / app["build"]).resolve()
        if not (build / "index.html").is_file():
            sys.exit(f"--sim: app build missing: {build / 'index.html'}")
    dst = out / app["path"]
    copy_tree(build, dst, ignore=shutil.ignore_patterns(".last_build_id", ".DS_Store"))
    idx = dst / "index.html"
    h = idx.read_text(encoding="utf-8")
    h2 = re.sub(r'<base href="[^"]*">', '<base href="./">', h)   # Flutter bakes --base-href in; "./" makes the app work under any prefix (local server, VPS)
    if h2 != h:
        idx.write_text(h2, encoding="utf-8")
    conf = {k: v for k, v in app.items() if k != "build"}
    conf["size_mb"] = round(size(dst) / 1e6)
    print(f"app: {app['path']} {mb(size(dst))} from {build}" + (f" @ {src['commit'][:10]}" if src else ""))
    return conf


def add_sim(sd: Path, out: Path, standalone: bool) -> None:
    site = sd / "site.json"
    if not site.is_file():
        sys.exit(f"--sim: missing {site} (needs entry/files/fw)")
    sc = json.loads(site.read_text(encoding="utf-8"))
    (out / "sim").mkdir(parents=True)
    if sc.get("device"):   # a device from the model (machine-project device.py): the framework's device runtime is the machine
        dev = sd / sc["device"]
        if not dev.is_file():
            sys.exit(f"--sim: {dev} (site.json device) missing - build the model (cad_incremental --model writes it) or run machine-project/scripts/device.py")
        copy_file(dev, out / "sim" / "device.json")
    elif not sc.get("entry"):
        sys.exit(f"--sim: {site} needs 'entry' (a machine.mjs) or 'device' (build/model/device.json)")
    listed = list(sc.get("files", [])) + [x for x in sc.get("extra", []) if (sd / x).is_file()]
    names = [Path(f).name for f in listed]   # site.json paths may point outside <dir> (e.g. ../mechanics/kinematics/motion.json); they land flat in site/sim/
    for f in listed:
        if not (sd / f).is_file():
            sys.exit(f"--sim: {sd / f} listed in site.json but missing")
        put_js(sd / f, out / "sim" / Path(f).name, vc=".js" if standalone else ".mjs")
    fw = (sd / sc.get("fw", "../build/fw")).resolve()
    (out / "fw").mkdir()
    n = 0
    for f in sorted(fw.iterdir()):
        if f.is_file() and f.suffix in (".mjs", ".wasm", ".json"):
            (put_js if f.suffix == ".mjs" else copy_file)(f, out / "fw" / f.name); n += 1
    if not (out / "fw" / "index.json").is_file():
        sys.exit(f"--sim: {fw}/index.json missing - rebuild the firmware (build.mjs writes it)")
    conf = {"entry": sc["entry"].replace(".mjs", ".js"), "files": [x.replace(".mjs", ".js") for x in names], "title": sc.get("title")} if not sc.get("device") \
        else {"device": "device.json", "files": [x.replace(".mjs", ".js") for x in names], "title": sc.get("title")}
    mo = sc.get("motion")   # {"file": "motion.json", "id_map": {motion id: sim actuators() id}, "labels": [...]}; default: motion.json among the extras, ids equal
    if mo is None and "motion.json" in names:
        mo = {}
    if mo is not None:
        mo.setdefault("file", "motion.json")
        if not (out / "sim" / mo["file"]).is_file():
            sys.exit(f"--sim: motion file {mo['file']} must be listed in site.json files/extra")
        conf["motion"] = mo
    if sc.get("app"):
        conf["app"] = add_app(sd, sc["app"], out)
    (out / "sim" / "config.json").write_text(json.dumps(conf, ensure_ascii=False), encoding="utf-8")
    print(f"sim: {'device.json, ' if sc.get('device') else ''}{len(names)} machine files, {n} firmware files (runtime: /_viewer/vc/)")


def add_flash(out: Path, pub: Path) -> None:
    """Overlay the published real-board firmware (fw_publish.py output) onto <site>/fw/. The site dir is wiped on every build, so the published files live OUTSIDE it
    (default <in>/fw_publish) and are copied in here. With --sim, fw/index.json already is the simulator's list (an array): it moves under "sim" in the merged {sim, boards}."""
    idx = pub / "index.json"
    if not idx.is_file():
        print(f"flash: nothing published in {pub} (cards say 'firmware not published'); publish with skills/firmware-sim/scripts/fw_publish.py --out {pub}")
        return
    dst = out / "fw"
    dst.mkdir(exist_ok=True)
    for item in sorted(pub.iterdir()):
        if item.name == "index.json":
            continue
        if item.is_dir():
            copy_tree(item, dst / item.name)
        else:
            copy_file(item, dst / item.name)
    # the web server (nginx) must read these whatever the source modes were (fw_publish dirs can be 0700)
    for p in [dst, *dst.rglob("*")]:
        p.chmod(0o755 if p.is_dir() else 0o644)
    merged = json.loads(idx.read_text(encoding="utf-8"))
    cur = dst / "index.json"
    if cur.is_file():
        sim = json.loads(cur.read_text(encoding="utf-8"))
        if isinstance(sim, list):
            merged = {**merged, "sim": sim}
    cur.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
    cur.chmod(0o644)
    print(f"flash: {len(merged.get('boards', []))} published board(s) from {pub}")


def clearance_report(cat, cyc, tracks=None):
    """The parts of a code model (cwd = the project, build/model from cad_incremental.py --model) that share space -
    moving ones over the cycle this build publishes, grouped as the page moves them - or None for anything else: a client's
    STEP (model_from_steps.py writes the same files, every part `func` "steps") or a hand-made import has overlaps that are
    the client's data. It never stops the build: the page shows a red mark from <out>/clearance.json
    and the router tells the agent from there."""
    root = Path.cwd()
    model = root / "build/model/parts.json"
    if not (model.is_file() and (root / "build/model/nodes.json").is_file()):
        return None
    parts = json.loads(model.read_text(encoding="utf-8")).get("parts") or []
    if not parts or all(p.get("func") == "steps" for p in parts):
        return None
    sys.path.insert(0, str(HERE.parents[2] / "machine-project" / "scripts"))
    try:
        import clearance
        groups = {str(p["id"]): p["motion_group"] for p in cat["parts"] if p.get("id") and p.get("motion_group")}
        rep = clearance.run(root, (cyc or {}).get("cycle"), groups, tracks)
    except Exception as e:   # noqa: BLE001 - the check must never stop publishing; the router says it did not run
        why = f"{type(e).__name__}: {e}"[:300]
        print(f"WARN  clearance check failed, not run: {why}", file=sys.stderr, flush=True)
        return {"clashes": [], "unmeasured": [], "cycle": bool(cyc), "error": why}
    print("\n".join(clearance.lines(rep)), flush=True)
    return rep


def harness_report(cat, cyc):
    """The wires routed inside a code model (machine-project harness.py: ports with locations, routes through waypoints) as
    the page draws them, checked against the parts - moving ones over the cycle this build publishes - or None when the
    model routes no wire. Like the overlap check it never stops the build: check.py rule «harness» reads its report."""
    root = Path.cwd()
    if not (root / "build/model/parts.json").is_file():
        return None
    sys.path.insert(0, str(HERE.parents[2] / "machine-project" / "scripts"))
    try:
        import harness
        groups = {str(p["id"]): p["motion_group"] for p in cat["parts"] if p.get("id") and p.get("motion_group")}
        rep = harness.run(root, (cyc or {}).get("cycle"), groups)
    except Exception as e:   # noqa: BLE001 - never stops publishing; check.py and the router say it did not run
        try:
            located = any(isinstance(p, dict) and p.get("ports_at")
                          for p in json.loads((root / "build/model/parts.json").read_text(encoding="utf-8")).get("parts") or [])
        except (OSError, ValueError, AttributeError):
            located = False
        if not located:   # a model that locates no port routes no wire: nothing of wires on its page (rule «schemes» reports connections.py)
            return None
        why = f"{type(e).__name__}: {e}"[:300]
        print(f"WARN  wires (harness) not checked: {why}", file=sys.stderr, flush=True)
        f = root / "build/harness/report.json"   # never yesterday's verdict on today's model: the error is the verdict
        f.parent.mkdir(parents=True, exist_ok=True)
        try:
            inputs = harness.inputs(root)
        except Exception:   # noqa: BLE001 - harness.py itself did not load
            inputs = None
        f.write_text(json.dumps({"inputs": inputs, "error": why, "wires": [], "problems": []}), encoding="utf-8")
        return {"wires": [], "problems": [f"the wires check did not run: {why}"]}
    if rep is None:
        return None
    print("\n".join(harness.lines(rep)), flush=True)
    return harness.site(rep)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True, help="importer output dir (model.glb, parts.json, parts/)")
    ap.add_argument("--out", required=True, help="site dir to (re)create")
    ap.add_argument("--up", choices=["y", "z"], default="y", help="which model axis is UP for the machine. default y = glTF native, no rotation. The importer's GLB says Z but "
                    "that is only the STEP header; LOOK at the render (legs stand, feet on the floor). Viewers can override with #up=z")
    ap.add_argument("--sim", help="machine sim dir with site.json (adds the virtual-machine layer)")
    ap.add_argument("--motion", help="motion.json with a `cycle` block (skills/web-viewer/scripts/cycle_from_params.py): adds the cycle bar (play/scrub) without a simulator; "
                    "parts need `motion_group` in parts.json (step-import --motion-groups)")
    ap.add_argument("--world", metavar="MJS", help="with --motion: the project's surroundings module (products, packaging, glue...), a button on the cycle bar "
                    "(--world-label: a checkbox in the panel); exports mount({THREE, root, data, period, frac, node, api}) -> {update(t)}, root is in mm of the CAD frame")
    ap.add_argument("--world-label", help="the --world checkbox in the panel: what it shows, in the client's words and language (e.g. \"Show the bars, the labels and the glue\"); "
                    "default: the viewer's own text in the page language (i18n.mjs motion.world)")
    ap.add_argument("--world-data", metavar="JSON", help="data for --world (e.g. the project's params), passed as `data`")
    ap.add_argument("--title", help="page title (default: assembly root name from parts.json)")
    ap.add_argument("--kit", metavar="URL", default="auto", help="download kits (panel + card buttons): GET <URL>/status and <URL>/<scope>. "
                    "Default auto = the router's kit route for the first path segment (buttons appear only if the project has kit.json); --kit none turns it off")
    ap.add_argument("--feedback", metavar="URL", default="auto", help="endpoint for \"Draw a note\" (the page POSTs {comment, image (JPEG data URL), view} as JSON). "
                    "Default auto = /feedback/<first path segment> (the engineer stack: /<project id>/ pages, the router posts the note to that project's chat); "
                    "--feedback none turns it off")
    ap.add_argument("--flash", nargs="?", const="auto", default="auto", metavar="DIR", help="flash real boards from the page over Web Serial (ON by default; --flash none turns it off): \"Connect a board\", a window per connected board, "
                    "the agent's `hw` commands through the router). DIR = firmware published by skills/firmware-sim/scripts/fw_publish.py; default <in>/fw_publish, copied into <site>/fw/ on every build "
                    "so a rebuild never loses it. Nothing published = any board can still be connected and the bot can work with it")
    ap.add_argument("--standalone", action="store_true", help="also write index.html and the viewer code (<out>/_viewer/) for a plain static server at the root; "
                    "the stack serves both itself, so a project's public/ never gets them")
    ap.add_argument("--link", action="append", default=[], metavar="LABEL=URL", help="a link button in the panel (repeatable), e.g. a process animation page")
    a = ap.parse_args()
    if a.flash == "none":
        a.flash = None
    src, out = Path(a.src).expanduser().resolve(), Path(a.out).expanduser().resolve()
    for need in ("model.glb", "parts.json"):
        if not (src / need).is_file():
            sys.exit(f"missing {src / need}")
    cat = json.loads((src / "parts.json").read_text(encoding="utf-8"))
    title = a.title or cat.get("assembly", {}).get("root") or "CAD"
    cyc = None
    if a.motion:
        cyc = json.loads(Path(a.motion).expanduser().read_text(encoding="utf-8"))
        if "cycle" not in cyc:
            sys.exit(f"--motion: {a.motion} has no `cycle` block")
        n_mg = sum(1 for p in cat["parts"] if p.get("motion_group") in cyc["cycle"]["groups"])
        if not n_mg:
            print("warning: no part of parts.json has a motion_group listed in cycle.groups (run step-import with --motion-groups)", file=sys.stderr)
    tracks = None   # links the project's world module moves by pose tracks (world data CRANK_TRACKS) move so in the check too
    if a.world_data and cyc:
        try:
            wd = json.loads(Path(a.world_data).expanduser().read_text(encoding="utf-8"))
            tracks = wd.get("CRANK_TRACKS") if isinstance(wd, dict) else None
        except (OSError, ValueError) as e:
            print(f"WARN  clearance: world data not read, links checked without their pose tracks: {e}", file=sys.stderr)
            tracks = None
    clr = clearance_report(cat, cyc, tracks)
    wires = harness_report(cat, cyc)
    if out.exists():
        remove_tree(out)
    out.mkdir(parents=True)
    has_edges = (src / "edges.json").is_file() and (src / "edges.bin").is_file()   # step-import/scripts/bake_edges.py; without it the page just has no contour lines
    if cyc:
        (out / "motion.json").write_text(json.dumps(cyc, ensure_ascii=False), encoding="utf-8")
    world = None
    if a.world:
        if not a.motion:
            sys.exit("--world needs --motion (it runs on the cycle clock)")
        put_js(Path(a.world).expanduser(), out / "world.js")
        world = {"module": "world.js", **({"label": a.world_label} if a.world_label else {})}
        if a.world_data:
            shutil.copyfile(Path(a.world_data).expanduser(), out / "world_data.json"); world["data"] = "world_data.json"
    links = [{"label": l.split("=", 1)[0], "url": l.split("=", 1)[1]} for l in a.link if "=" in l]
    if clr is not None:
        (out / "clearance.json").write_text(json.dumps({"clashes": clr["clashes"], "unmeasured": clr["unmeasured"][:20],
                                                        "unmeasured_n": len(clr["unmeasured"]), "cycle": clr["cycle"],
                                                        **({"error": clr["error"]} if clr.get("error") else {})},
                                                       ensure_ascii=False), encoding="utf-8")
    if wires is not None:
        (out / "wires.json").write_text(json.dumps(wires, ensure_ascii=False), encoding="utf-8")
    layers = {**({"sim": "sim/config.json"} if a.sim else {}), **({"wires": "wires.json"} if wires is not None else {}), **({"edges": "edges.json"} if has_edges else {}), **({"cycle": "motion.json"} if cyc else {}),
              **({"clearance": "clearance.json"} if clr is not None else {})}
    stamp = f"{time.time_ns() // 1000:x}"   # cache-busts every data URL of this build (in-app browsers cache hard: a client once kept seeing a stale model)
    (out / "config.json").write_text(json.dumps({"up": a.up, "title": title, "v": stamp, **layers, **({"links": links} if links else {}), **({"world": world} if world else {}), **({"feedback": a.feedback} if a.feedback != "none" else {}), **({"kit": a.kit} if a.kit != "none" else {}), **({"flash": True} if a.flash else {})}, ensure_ascii=False), encoding="utf-8")
    if a.standalone:
        put_viewer(out, bool(a.sim))
    if has_edges:
        copy_file(src / "edges.json", out / "edges.json"); copy_file(src / "edges.bin", out / "edges.bin")
    else:
        print("note: no edges.json/edges.bin - run skills/step-import/scripts/bake_edges.py <out> for CAD-style contour lines", file=sys.stderr)
    copy_file(src / "model.glb", out / "model.glb")
    copy_file(src / "parts.json", out / "parts.json")
    if (src / "parts").is_dir():
        copy_tree(src / "parts", out / "parts")
    else:
        print("warning: no parts/ dir - download buttons will 404", file=sys.stderr)
    if a.sim:
        add_sim(Path(a.sim).expanduser().resolve(), out, a.standalone)
    if a.flash:
        add_flash(out, src / "fw_publish" if a.flash == "auto" else Path(a.flash).expanduser().resolve())
        conf = json.loads((out / "config.json").read_text(encoding="utf-8"))   # no fw/index.json -> the page skips that fetch (no 404 in the console)
        conf["flash"] = {"fw": (out / "fw" / "index.json").is_file()}
        (out / "config.json").write_text(json.dumps(conf, ensure_ascii=False), encoding="utf-8")
    missing = [f for p in cat["parts"] for f in p.get("files", {}).values() if not (out / f).is_file()]
    if missing:
        print(f"warning: {len(missing)} catalogue files missing, e.g. {missing[:3]}", file=sys.stderr)
    print(f"site: {out}")
    for name in ("index.html", "_viewer", "model.glb", "edges.bin", "parts.json", "parts", "sim", "fw"):
        if (out / name).exists():
            print(f"  {name:12s} {mb(size(out / name))}")
    print(f"total {mb(size(out))}  (initial page load: model + parts.json = {mb(size(out / 'model.glb') + size(out / 'parts.json'))} + the shared viewer code)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
