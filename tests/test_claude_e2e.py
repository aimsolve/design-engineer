"""Done-when 4 (opt-in, DE_E2E_CLAUDE=1): in a fresh `de init` folder, plain Claude Code with the plugin, asked in Russian for a
NEMA 17 bracket on a 2020 profile, produces a part that builds, passes check and shows in the served page. ~20 min, real subscription."""
import os
import re
import shutil

import pytest

from conftest import AGENT, de, init_project, parts_json, run, serve, stop

pytestmark = [pytest.mark.slow, pytest.mark.e2e,
              pytest.mark.skipif(os.environ.get("DE_E2E_CLAUDE") != "1", reason="opt-in: DE_E2E_CLAUDE=1 runs a real `claude -p` (~20 min)")]
PROMPT = "design a NEMA 17 motor bracket for a 2020 aluminium profile"
BRACKET = re.compile(r"bracket|mount", re.I)


def test_claude_designs_a_bracket_that_builds_checks_and_shows(tmp_path):
    claude = shutil.which("claude")
    assert claude, "no `claude` on PATH"
    ref, r0 = init_project(tmp_path, "init-example")
    assert r0.rc == 0, r0.why("de init (reference)")
    assert de("build", ref, cwd=ref, timeout=1200).rc == 0
    n_example = len(parts_json(ref))

    d, ri = init_project(tmp_path, "nema-bracket")
    assert ri.rc == 0, ri.why("de init")
    env = {k: v for k, v in os.environ.items() if k not in ("ENGINEER_API", "DE_ROOT", "CLAUDE_PROJECT_DIR")}
    r = run([claude, "-p", "--plugin-dir", AGENT, "--dangerously-skip-permissions", PROMPT], cwd=d, env=env, timeout=20 * 60)
    (tmp_path / "claude.log").write_text(r.text)
    assert r.rc == 0, r.why("claude -p")

    rb = de("build", d, cwd=d, timeout=1200)
    assert rb.rc == 0, rb.why("de build after Claude")
    parts = parts_json(d)
    assert len(parts) > n_example, f"{len(parts)} parts, the init example has {n_example}"
    named = [p for p in parts if BRACKET.search(f"{p.get('id', '')} {p.get('name', '')}")]
    assert named, f"no part named as a bracket: {[(p.get('id'), p.get('name')) for p in parts]}"
    rc = de("check", d, cwd=d, timeout=900)
    assert rc.rc == 0, rc.why("de check after Claude")

    try:
        rs, port, slug = serve(d)
        assert rs.rc == 0 and port, rs.why("de serve")
        sp = pytest.importorskip("playwright.sync_api")
        with sp.sync_playwright() as p:
            b = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
            pg = b.new_page(viewport={"width": 1280, "height": 800})
            pg.goto(f"http://127.0.0.1:{port}/{slug}/", wait_until="load")
            pg.wait_for_function("window.__viewer && (window.__viewer.ready || window.__viewer.error)", timeout=180000)
            assert not pg.evaluate("window.__viewer.error")
            pg.wait_for_timeout(1500)
            shown = pg.evaluate("""() => { const v = window.__viewer, s = new Set();
              for (let y = 40; y < innerHeight - 40; y += 12) for (let x = 40; x < innerWidth - 40; x += 12) { const h = v.pick(x, y); if (h && h.part) s.add(h.part); }
              return [...s]; }""")
            b.close()
        ids = {p["id"] for p in named}
        assert shown, "the served page shows no part"
        on_screen = [s for s in shown if any(s == i or s.startswith(i + "_") for i in ids) or BRACKET.search(s)]
        assert on_screen, f"the bracket {ids} is not on screen (shown: {shown})"
    finally:
        stop(d)
