"""Done-when 3, HTTP side: `de serve` gives the project a viewer on http://127.0.0.1 with the endpoints the page calls
(feedback -> .engineer/inbox, kit STEP download, hardware bridge), bound to loopback only."""
import base64
import io
import json
import shutil
import socket
import subprocess
import time

import pytest
from PIL import Image

from conftest import AGENT, URL_RE, copy_project, de, http, inbox, need_built, need_server, serve, stop

pytestmark = pytest.mark.slow
VIEWER = AGENT / "skills" / "web-viewer" / "templates" / "assembly"


def jpeg_data_url():
    buf = io.BytesIO()
    Image.new("RGB", (32, 24), (200, 60, 40)).save(buf, "JPEG")
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def post_json(port, path, obj=None, raw=None):
    body = raw if raw is not None else json.dumps(obj).encode()
    return http("POST", port, path, body=body, headers={"Content-Type": "application/json"})


# ---------------------------------------------------------------- the server itself
def test_serve_prints_the_loopback_url_of_the_project(served):
    server = need_server(served)
    assert server["slug"] == "nema-mount", server["result"].text
    assert URL_RE.search(server["result"].out), "the URL is not on stdout"


def test_second_serve_reuses_the_running_server(served):
    server = need_server(served)
    r, port, slug = serve(server["dir"])
    assert r.rc == 0, r.why("second de serve")
    assert (port, slug) == (server["port"], server["slug"]), f"second serve printed another URL: {r.text}"


def test_root_redirects_to_the_project_page(served):
    server = need_server(served)
    st, h, _ = http("GET", server["port"], "/")
    assert st == 302, st
    assert h.get("location", "").rstrip().endswith(f"/{server['slug']}/"), h.get("location")


def test_project_page_and_its_files(served):
    server = need_server(served)
    port, slug, d = server["port"], server["slug"], server["dir"]
    st, h, body = http("GET", port, f"/{slug}/")
    assert st == 200 and "text/html" in h.get("content-type", ""), (st, h)
    assert b"/_viewer/" in body, "the page is not the standard viewer (it loads its code from /_viewer/)"
    st, _, body = http("GET", port, f"/{slug}/config.json")
    assert st == 200 and json.loads(body) == json.loads((d / "public" / "config.json").read_text())
    st, _, body = http("GET", port, f"/{slug}/model.glb")
    assert st == 200 and body[:4] == b"glTF", st


def test_viewer_code_is_served_as_javascript(served):
    server = need_server(served)
    st, h, body = http("GET", server["port"], "/_viewer/feedback.js")
    assert st == 200, st
    assert "javascript" in h.get("content-type", ""), h.get("content-type")
    assert body == (VIEWER / "feedback.mjs").read_bytes()


def test_nothing_outside_the_project_is_served(served):
    server = need_server(served)
    st, _, _ = http("GET", server["port"], f"/{server['slug']}/../../../etc/passwd")
    assert st in (400, 403, 404), st


def test_bound_to_loopback_only(served):
    server = need_server(served)
    port = server["port"]
    lsof = shutil.which("lsof")
    if lsof:
        out = subprocess.run([lsof, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"], capture_output=True, text=True).stdout
        names = [ln.split()[-2] for ln in out.splitlines()[1:] if ln.strip()]
        assert names, f"nothing listens on {port}:\n{out}"
        assert all(n.startswith(("127.0.0.1:", "localhost:")) for n in names), f"listens on {names}"
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))              # no packet is sent: only picks the outward interface
        lan = s.getsockname()[0]
    except OSError:
        lan = None
    finally:
        s.close()
    if lan and not lan.startswith("127."):
        with pytest.raises(OSError):
            socket.create_connection((lan, port), timeout=3).close()


# ---------------------------------------------------------------- feedback -> .engineer/inbox
def test_feedback_note_lands_in_the_inbox(served):
    server = need_server(served)
    port, slug, d = server["port"], server["slug"], server["dir"]
    before = inbox(d)
    view = {"url": f"http://127.0.0.1:{port}/{slug}/", "parts": [], "camera": {"yaw": 0.5}}
    st, _, body = post_json(port, f"/feedback/{slug}", {"comment": "move the hole 2 mm (otwór)", "image": jpeg_data_url(), "view": view})
    assert st == 200, (st, body)
    new = inbox(d) - before
    notes = [d / ".engineer" / "inbox" / p for p in new if p.suffix == ".json"]
    jpgs = [d / ".engineer" / "inbox" / p for p in new if p.suffix.lower() in (".jpg", ".jpeg")]
    assert len(notes) == 1, f"new inbox files: {sorted(map(str, new))}"
    note = json.loads(notes[0].read_text(encoding="utf-8"))
    assert note.get("comment") == "move the hole 2 mm (otwór)", note
    assert note.get("view", {}).get("camera") == {"yaw": 0.5}, note
    assert "base64" not in notes[0].read_text(), "the JPEG is inlined in the note instead of a separate file"
    assert len(jpgs) == 1 and jpgs[0].read_bytes()[:2] == b"\xff\xd8", f"no JPEG next to the note: {sorted(map(str, new))}"


@pytest.mark.parametrize("raw", [b"{not json", json.dumps({"image": "data:image/jpeg;base64,AAAA", "view": {}}).encode(),
                                 json.dumps({"comment": "", "view": {}}).encode()], ids=["bad-json", "no-comment", "empty-comment"])
def test_bad_feedback_is_refused_and_writes_nothing(served, raw):
    server = need_server(served)
    before = inbox(server["dir"])
    st, _, _ = post_json(server["port"], f"/feedback/{server['slug']}", raw=raw)
    assert 400 <= st < 500, st
    assert inbox(server["dir"]) == before


def test_feedback_for_another_project_does_not_land_here(served):
    server = need_server(served)
    before = inbox(server["dir"])
    post_json(server["port"], "/feedback/some-other-project", {"comment": "x", "image": jpeg_data_url(), "view": {}})
    assert inbox(server["dir"]) == before, "a note posted for another slug was written into this project"


# ---------------------------------------------------------------- kit (STEP download)
def test_kit_status_offers_step(served):
    server = need_server(served)
    st, _, body = http("GET", server["port"], f"/kit/{server['slug']}/status")
    assert st == 200, (st, body[:300])
    s = json.loads(body)
    assert s.get("enabled") is True, s
    assert "step" in s.get("scopes", []), s


def test_kit_step_download_returns_a_step_file(served):
    server = need_server(served)
    port, slug = server["port"], server["slug"]
    deadline, seen = time.time() + 900, []
    while True:
        st, h, body = http("GET", port, f"/kit/{slug}/step", timeout=120)
        seen.append(st)
        assert st in (200, 202), f"kit step answered {st}: {body[:400]!r}"
        if st == 200:
            break
        assert time.time() < deadline, f"STEP export did not finish in 15 min (answers: {seen[-5:]})"
        time.sleep(2)
    assert body.lstrip()[:12] == b"ISO-10303-21", f"not a STEP file: {body[:80]!r}"


# ---------------------------------------------------------------- hardware bridge (the page's hw.mjs)
def test_hw_event_and_command_poll(served):
    server = need_server(served)
    port, slug = server["port"], server["slug"]
    st, _, body = post_json(port, f"/hw/{slug}/event", {"viewer": "v1", "type": "ports", "ports": []})
    assert st == 200, (st, body[:300])
    st, _, body = http("GET", port, f"/hw/{slug}/cmd?viewer=v1&after=0", timeout=40)
    assert st == 200, (st, body[:300])
    assert isinstance(json.loads(body).get("cmds"), list), body[:300]
    token = json.loads((server["dir"] / ".engineer" / "serve.json").read_text())["token"]   # the `hw` CLI's side needs it (review B1)
    st, _, body = http("GET", port, "/api/hw/status", headers={"Authorization": f"Bearer {token}"})
    assert st == 200, (st, body[:300])
    json.loads(body)


# ---------------------------------------------------------------- slug, stop
def test_slug_is_the_folder_name_lowercased(built, tmp_path):
    built_ok = need_built(built)
    d = copy_project(built_ok, tmp_path / "Nema_Mount X")
    try:
        r, port, slug = serve(d)
        assert r.rc == 0, r.why("de serve")
        assert slug == "nema-mount-x", r.text
        st, _, _ = http("GET", port, "/nema-mount-x/config.json")
        assert st == 200, st
    finally:
        stop(d)


def test_stop_stops_the_server(built, tmp_path):
    built_ok = need_built(built)
    d = copy_project(built_ok, tmp_path / "stop-me")
    r, port, _ = serve(d)
    try:
        assert r.rc == 0 and port, r.why("de serve")
        assert http("GET", port, "/stop-me/config.json")[0] == 200
        rs = de("serve", d, "--stop", cwd=d, timeout=60)
        assert rs.rc == 0, rs.why("de serve --stop")
        time.sleep(1)
        with pytest.raises(OSError):
            http("GET", port, "/", timeout=3)
    finally:
        stop(d)   # a failed assertion must not leave a server behind
