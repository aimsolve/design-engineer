"""Board flashing through the web viewer: the router side (no Telegram, no docker, so it is testable on its own).

A viewer tab (browser, Web Serial) posts its ports, serial log lines and command results to /hw/<pid>/event and
long-polls /hw/<pid>/cmd for work. The agent CLI `hw` queues commands and reads state through /api/hw/*.
State lives in memory, per project; the log of each card is also appended to <project dir>/hw/<card>.log.
"""
import itertools
import re
import threading
import time
from collections import deque
from pathlib import Path

ONLINE_S = 45          # a viewer is online if a ports event arrived within this many seconds
POLL_S = 25            # viewer long-poll
WAIT_MAX_S = 110       # agent `cmd/<id>/wait`
LOG_LINES = 2000       # ring per card
LOG_FILE_MAX = 512_000  # bytes; a larger file is rewritten with its newest lines, at most LOG_LINES and half of LOG_FILE_MAX
OPS = ("flash", "reset", "monitor", "identify")
MAX_PORTS = 32         # ports per viewer, cards per project, logs per kind (card / port)
MAX_VIEWERS = 8        # per project; the least recently seen goes first

_cv = threading.Condition()
_fl = threading.Lock()          # log-file I/O only; never taken while holding _cv
_seq = itertools.count(int(time.time() * 1000))   # command ids, global; seeded from time so a router restart never reuses ids a viewer already saw
_tick = itertools.count(1)      # tie-break for "most recent" when two events share a timestamp
_proj = {}                      # pid -> {viewers: {vid: {t, n, ports}}, cmds: {id: cmd}, logs: {card: deque}, flash: {card: dict}, cards: {card: class}}


def _p(pid):
    return _proj.setdefault(pid, {"viewers": {}, "cmds": {}, "logs": {}, "flash": {}, "cards": {}})


def _safe(card):
    return re.sub(r"[^\w.-]", "_", str(card))[:60] or "_"


def _online(st, now):
    return [(v["t"], v["n"], vid, v) for vid, v in st["viewers"].items() if now - v["t"] <= ONLINE_S]


def _pref(v):
    """A tab that holds the port (or can open it) or is visible beats a hidden one with a busy port."""
    return 1 if (v.get("held") or v.get("visible")) else 0


def _order(st, now):
    return sorted(_online(st, now), key=lambda x: (_pref(x[3]), x[0], x[1]))


def _connected(st, now):
    """card -> port, from the online viewers' ports lists (the preferred, then newest viewer wins)."""
    out = {}
    for _, _, _, v in _order(st, now):
        for pt in v["ports"]:
            if pt.get("card"):
                out[pt["card"]] = pt
    return out


def _free_ports(st, now):
    """key -> port, for granted ports with no card (same preference)."""
    out = {}
    for _, _, _, v in _order(st, now):
        for pt in v["ports"]:
            if pt.get("key") and not pt.get("card"):
                out[pt["key"]] = pt
    return out


def _target(st, now, card=None, key=None):
    best = None
    for t, n, vid, v in _order(st, now):
        if any((pt.get("card") == card) if card else (pt.get("key") == key) for pt in v["ports"]):
            best = vid
    return best


def _append_file(pdir, card, lines):
    try:
        with _fl:
            d = Path(pdir) / "hw"
            d.mkdir(parents=True, exist_ok=True)
            f = d / f"{_safe(card)}.log"
            with f.open("a", encoding="utf-8") as fh:
                fh.write("".join(l + "\n" for l in lines))
            if f.stat().st_size > LOG_FILE_MAX:
                keep, size = [], 0
                for l in reversed(f.read_text(encoding="utf-8", errors="replace").splitlines()[-LOG_LINES:]):
                    size += len(l.encode("utf-8")) + 1
                    if size > LOG_FILE_MAX // 2:
                        if not keep:   # one huge line: keep its end rather than an empty file
                            keep.append(l.encode("utf-8")[-(LOG_FILE_MAX // 2 - 1):].decode("utf-8", "ignore"))
                        break
                    keep.append(l)
                f.write_text("".join(l + "\n" for l in reversed(keep)), encoding="utf-8")
    except OSError:
        pass


def _port_key(x):
    k = x.get("key") or (f"{x.get('vid') or ''}:{x.get('pid') or ''}" + (f":{x['serial']}" if x.get("serial") else ""))
    return str(k)[:80] if k.strip(":") else None


def event(pid, b, now, pdir):
    """A viewer's event. Returns (http code, body)."""
    vid = str(b.get("viewer") or "")[:80]
    if not vid:
        return 400, {"error": "viewer id"}
    kind = b.get("type")
    to_file = None
    with _cv:
        st = _p(pid)
        if kind == "ports":
            ports = []
            for x in (b.get("ports") or [])[:MAX_PORTS]:
                if isinstance(x, dict):
                    pt = {k: (str(x[k])[:80] if x.get(k) is not None else None) for k in ("card", "class", "vid", "pid", "serial")}
                    pt["key"] = _port_key(x)
                    ports.append(pt)
            for k in [k for k, v in st["viewers"].items() if now - v["t"] > ONLINE_S]:
                del st["viewers"][k]
            if vid not in st["viewers"] and len(st["viewers"]) >= MAX_VIEWERS:
                del st["viewers"][min(st["viewers"], key=lambda k: (st["viewers"][k]["t"], st["viewers"][k]["n"]))]
            st["viewers"][vid] = {"t": now, "n": next(_tick), "ports": ports, "held": b.get("held") is True, "visible": b.get("visible") is True}
            conn = _connected(st, now)
            for pt in ports:
                c = pt["card"]
                if c and c not in st["cards"] and len(st["cards"]) >= MAX_PORTS:   # full: make room by forgetting a card that is not connected now
                    old = next((k for k in st["cards"] if k not in conn), None)
                    if old is not None:
                        del st["cards"][old]
                if c and (c in st["cards"] or len(st["cards"]) < MAX_PORTS):
                    st["cards"][c] = pt["class"]
            _cv.notify_all()
            return 200, {"ok": True}
        if kind == "log":
            port_key = str(b.get("port") or "")[:80]
            card = str(b.get("card") or "")[:60]
            name = ("port:" + port_key) if port_key and not card else card
            lines = [str(l)[:500] for l in (b.get("lines") or [])[:500]]
            if not name:
                return 400, {"error": "card"}
            if name not in st["logs"] and sum(1 for k in st["logs"] if k.startswith("port:") == name.startswith("port:")) >= MAX_PORTS:
                return 400, {"error": "too many logs"}
            st["logs"].setdefault(name, deque(maxlen=LOG_LINES)).extend(lines)
            to_file = (name, lines)
        elif kind == "result":
            c = st["cmds"].get(b.get("cmd"))
            if not c:
                return 404, {"error": "no such command"}
            c["result"] = {"ok": bool(b.get("ok")), "message": str(b.get("message") or "")[:500], "chip": str(b.get("chip") or "")[:40]}
            if c["op"] == "flash":
                st["flash"][c["card"] or "port:" + str(c["port"])] = dict(c["result"], at=now)
            _cv.notify_all()
            return 200, {"ok": True}
        else:
            return 400, {"error": "unknown event type"}
    _append_file(pdir, *to_file)   # outside the lock: disk must not stall the long-polls
    return 200, {"ok": True}


def poll(pid, vid, after, gone=lambda: False):
    """Viewer long-poll: commands with id > after addressed to this viewer, or [] after POLL_S."""
    end = time.monotonic() + POLL_S
    with _cv:
        while True:
            v = _p(pid)["viewers"].get(vid)   # a waiting poll is a sign of life too: a hidden tab's 20 s heartbeat timer gets throttled past ONLINE_S
            if v:
                v["t"] = max(v["t"], time.time())
            mine = sorted((c for c in _p(pid)["cmds"].values() if c["viewer"] == vid and c["id"] > after), key=lambda c: c["id"])
            left = end - time.monotonic()
            if mine or left <= 0 or gone():
                return {"cmds": [{k: c[k] for k in ("id", "op", "card", "port", "fw") if k == "id" or k == "op" or c[k]} for c in mine]}
            _cv.wait(min(left, 1.0))


def queue(pid, op, card, now, port=None, fw=None):
    card, port, fw = (str(x) if x else None for x in (card, port, fw))
    if op not in OPS or not (card or port):
        return 400, {"error": "op is flash|reset|monitor|identify, card or port is required"}
    with _cv:
        st = _p(pid)
        vid = _target(st, now, card=card, key=port)
        if not vid:
            return 409, {"error": "no viewer online with this board connected", "online": bool(_online(st, now))}
        cid = next(_seq)
        st["cmds"][cid] = {"id": cid, "op": op, "card": card, "port": port, "fw": fw,
                           "viewer": vid, "result": None, "t": now}
        for old in sorted(st["cmds"])[:-200]:
            del st["cmds"][old]
        _cv.notify_all()
        return 200, {"id": cid}


def wait(pid, cid, gone=lambda: False, limit=WAIT_MAX_S):
    end = time.monotonic() + limit
    with _cv:
        while True:
            c = _p(pid)["cmds"].get(cid)
            if not c:
                return 404, {"error": "no such command"}
            left = end - time.monotonic()
            if c["result"]:
                return 200, dict(c["result"], id=cid)
            if left <= 0 or gone():
                return 200, {"id": cid, "ok": None, "pending": True, "message": "no result yet"}
            _cv.wait(min(left, 1.0))


def log(pid, card, tail):
    """card is a card name, or "port:<key>" for a port that has no card."""
    with _cv:
        lines = list(_p(pid)["logs"].get(card, ()))
    return {"card": card, "lines": lines[-tail:] if tail > 0 else []}


def status(pid, now):
    with _cv:
        st = _p(pid)
        conn = _connected(st, now)
        names = {n for n in set(st["cards"]) | set(st["logs"]) | set(st["flash"]) | set(conn) if not n.startswith("port:")}
        names = sorted(sorted(names, key=lambda n: (n not in conn, n not in st["cards"], n))[:MAX_PORTS])   # at most MAX_PORTS: connected first
        cards = []
        for n in names:
            lg = st["logs"].get(n)
            cards.append({"card": n, "connected": n in conn, "class": (conn.get(n) or {}).get("class") or st["cards"].get(n),
                          "last_flash": st["flash"].get(n), "last_line": lg[-1] if lg else ""})
        ports = []
        for k, pt in sorted(_free_ports(st, now).items()):
            lg = st["logs"].get("port:" + k)
            ports.append({"key": k, "class": pt.get("class"), "vid": pt.get("vid"), "pid": pt.get("pid"), "serial": pt.get("serial"),
                          "last_flash": st["flash"].get("port:" + k), "last_line": lg[-1] if lg else ""})
        return {"online": bool(_online(st, now)), "cards": cards, "ports": ports}
