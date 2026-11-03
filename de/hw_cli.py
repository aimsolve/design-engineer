#!/usr/bin/env python3
"""hw — flash a board and read its serial log through the client's open web viewer (the client has the board plugged in).

  hw status                          viewer online or not; one line per card: connected, class, last flash, last log line
  hw flash CARD [--timeout 120]      flash the firmware published for CARD, wait for the result + 8 s of log, print both
  hw flash --port KEY --fw NAME      flash firmware NAME (fw/NAME/ on the site) onto any granted port, even one the model has no card for
  hw identify --port KEY             ask the chip what it is (esp32 family: esptool connect, then a reset); other classes: USB class only
  hw reset CARD | --port KEY         reset the board, then 8 s of log
  hw log CARD | --port KEY [--tail 50] [--follow S]   the serial log (--follow: keep printing new lines for S seconds)
status also lists ports with no card ("not in the model"): KEY = vid:pid[:serial] and the USB class (null/? = unknown, monitor only).
Exit codes: 0 ok, 1 the flash/reset failed, 2 no viewer online or the card is not connected: ask the client to open
the viewer (the project's model page; locally `de serve`) in Chrome or Edge, press the connect-a-board button in the panel and plug the board in.
The log is also on disk, in the project's hw/ folder (one file per card).
"""
import argparse
import os
import sys
import time

import requests

API = os.environ.get("ENGINEER_API", "http://router:8080")
H = {"Authorization": f"Bearer {os.environ.get('ENGINEER_TOKEN', '')}", "X-Eng-Lane": os.environ.get("ENG_LANE", "single")}
TAIL_S = 8
NOT_READY = ("The viewer is not open or the board is not connected: ask the client to open the project's viewer in Chrome/Edge on a computer, "
             "press the connect-a-board button in the panel and plug the board in.")
if os.environ.get("DE_VIEWER_URL"):   # local mode (`de hw`): the user's own page on this machine
    NOT_READY = (f"The viewer is not open or the board is not connected: open {os.environ['DE_VIEWER_URL']} in Chrome/Edge, "
                 "press the connect-a-board button in the panel and plug the board in.")


def call(verb, path, timeout=130, **kw):
    with requests.Session() as s:
        s.trust_env = API.startswith("http://router")   # a local `de serve` is loopback: never through a proxy
        r = s.request(verb, API + path, headers=H, timeout=timeout, **kw)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"error": r.text[:300]}


def get_log(card, tail):
    code, out = call("GET", f"/api/hw/log/{card}?tail={tail}")
    return out.get("lines", []) if code == 200 else []


def status():
    code, st = call("GET", "/api/hw/status")
    if code != 200:
        sys.exit(f"hw: {st.get('error', code)}")
    print("Viewer online" if st["online"] else "Viewer not open (no events in the last 45 s): " + NOT_READY)
    for p in st.get("ports") or []:
        lf = p.get("last_flash")
        fl = f"; flash: {'ok' if lf['ok'] else 'FAILED'} {lf['message']}" if lf else ""
        print(f"port {p['key']} ({p.get('class') or 'class unknown'}) — not in the model{fl}; log: {p.get('last_line') or '—'}")
    for c in st["cards"]:
        lf = c.get("last_flash")
        fl = f"flash: {'ok' if lf['ok'] else 'FAILED'} {lf['message']}" if lf else "never flashed"
        print(f"{c['card']}: {'connected' if c['connected'] else 'not connected'} ({c.get('class') or '?'}); {fl}; log: {c.get('last_line') or '—'}")


def run(op, card, timeout, port=None, fw=None):
    what = card or f"port {port}"
    code, st = call("GET", "/api/hw/status")
    if code == 200 and st.get("online"):
        have = (any(c["card"] == card and c["connected"] for c in st.get("cards", [])) if card
                else any(p["key"] == port for p in st.get("ports", [])) or any(c.get("port") == port for c in st.get("cards", [])))
    else:
        have = False
    if not have and not port:
        print(NOT_READY)
        return 2
    body = {"op": op, **({"card": card} if card else {}), **({"port": port} if port else {}), **({"fw": fw} if fw else {})}
    code, out = call("POST", "/api/hw/cmd", json=body)
    if code == 409:
        print(NOT_READY if card else f"Port {port} is not connected to any open viewer. " + NOT_READY)
        return 2
    if code != 200:
        print(f"hw: {out.get('error', code)}")
        return 1
    end = time.time() + timeout
    res = None
    while time.time() < end:
        _, w = call("GET", f"/api/hw/cmd/{out['id']}/wait", timeout=130)
        if "error" in w:
            print(f"hw: {w['error']}")
            return 1
        if not w.get("pending"):
            res = w
            break
    if not res:
        print(f"hw: no answer from the viewer in {timeout} s (tab closed or the board hung?)")
        return 1
    if op != "identify":
        time.sleep(TAIL_S)
    print(("OK" if res["ok"] else "FAILED") + f" ({op} {what}, {res.get('chip') or '?'}): {res['message']}")
    lines = get_log(card or f"port:{port}", 50) if op != "identify" else []
    if lines:
        print("--- board log ---")
        print("\n".join(lines))
    return 0 if res["ok"] else 1


def log(card, tail, follow):
    print("\n".join(get_log(card, tail)))
    end, seen = time.time() + follow, len(get_log(card, 2000))
    while time.time() < end:
        time.sleep(1)
        now = get_log(card, 2000)
        if len(now) > seen:
            print("\n".join(now[seen:]), flush=True)
        if len(now) < 2000:   # a full ring stops growing: follow then shows nothing new, the file on disk has the rest
            seen = len(now)
    return 0


def main():
    ap = argparse.ArgumentParser(prog="hw", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    for name, tmo in (("flash", 120), ("reset", 30), ("identify", 30), ("log", 0)):
        sp = sub.add_parser(name)
        sp.add_argument("card", nargs="?")
        sp.add_argument("--port", help="port key vid:pid[:serial] (see hw status), for boards with no card in the model")
        if name == "flash":
            sp.add_argument("--fw", help="published firmware name to flash (default: the card's)")
        if name == "log":
            sp.add_argument("--tail", type=int, default=50)
            sp.add_argument("--follow", type=int, default=0)
        else:
            sp.add_argument("--timeout", type=int, default=tmo)
    a = ap.parse_args()
    if a.cmd == "status":
        status()
        return 0
    if bool(a.card) == bool(a.port):
        ap.error(f"{a.cmd}: give a CARD or --port KEY")
    if a.cmd == "log":
        return log(a.card or f"port:{a.port}", a.tail, a.follow)
    if a.cmd == "flash" and a.port and not a.fw and not a.card:
        ap.error("flash --port needs --fw NAME")
    return run(a.cmd, a.card, a.timeout, a.port, getattr(a, "fw", None))


if __name__ == "__main__":
    sys.exit(main())
