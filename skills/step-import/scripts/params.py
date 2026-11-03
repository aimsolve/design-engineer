"""Parametric extractor for parts.json: name (+ catalogue knowledge + STL geometry) -> type + typed params.

Per part it sets:
  type              "pulley" | "belt" | "fastener" | "bearing" | ... | None
  params            {english_snake_case: value}, SI (unit in the key: _mm, _w, _a, _v)
  params_source     {key: "name" | "geometry" | "catalogue" | "derived"}
  params_check      {key: "geometry:agree"}  name/catalogue value confirmed by the mesh
  params_conflicts  {key: {"name"|"catalogue": v, "geometry": g}}  both kept, params holds the name/catalogue value

Pure python (re). Geometry (numpy + parts/<id>.stl) is optional: without numpy or the STL the geometry step is skipped.
Edit RULES (ordered, first match wins), then `python3 step_import.py --reclassify <out>`.
"""
import math, os, re, struct

import import_i18n as I   # the words of part names in other languages, the look-alike letters

try:
    import numpy as np
except ImportError:
    np = None


def norm(s):
    """Look-alike letters of other scripts -> Latin, applied to a COPY of the name used for value parsing (keywords match
    the original too)."""
    return I.latinise(s)


# ---------------------------------------------------------------- geometry helpers
def dims(part):
    lo, hi = part["bbox_mm"]
    return [round(b - a, 3) for a, b in zip(lo, hi)]


def load_stl(out, part):
    f = (part.get("files") or {}).get("stl")
    path = os.path.join(out, f) if f else None
    if np is None or not path or not os.path.exists(path):
        return None
    with open(path, "rb") as fh:
        fh.seek(80); n = struct.unpack("<I", fh.read(4))[0]
        rec = np.frombuffer(fh.read(n * 50), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    return np.unique(rec["v"].reshape(-1, 3).astype(np.float64).round(4), axis=0)


def teeth_from_stl(V):
    """Count teeth of a toothed pulley: axis = shortest bbox side; among the axial levels that carry vertices, take the one
    with the most outer-ring vertices that is not a plain circle (the tooth outline; flange/hub levels are circles), FFT of r(theta)."""
    if V is None or len(V) < 100: return None
    ext = V.max(0) - V.min(0); ax = int(np.argmin(ext)); o = [i for i in range(3) if i != ax]
    c = (V.max(0) + V.min(0)) / 2
    x, y, a = V[:, o[0]] - c[o[0]], V[:, o[1]] - c[o[1]], V[:, ax]
    r, th = np.hypot(x, y), np.arctan2(y, x)
    best = None
    for lv in np.unique(np.round(a, 2)):
        m = np.abs(a - lv) < 0.02
        if m.sum() < 40: continue
        rr, tt = r[m], th[m]
        sel = rr > 0.7 * rr.max()
        if sel.sum() < 40: continue
        rr, tt = rr[sel], tt[sel]
        score = (rr.max() - rr.min()) / rr.max()
        if score < 0.01: continue
        if best is None or len(rr) > best[0]: best = (len(rr), rr, tt)   # the tooth outline is the densest ring
    if best is None: return None
    _, rr, tt = best
    i = np.argsort(tt); grid = np.linspace(-np.pi, np.pi, 1440, endpoint=False)
    sig = np.interp(grid, tt[i], rr[i], period=2 * np.pi)
    F = np.abs(np.fft.rfft(sig - sig.mean()))[1:200]
    return int(np.argmax(F) + 1)


# ---------------------------------------------------------------- param builder
class P:
    def __init__(s): s.v, s.s, s.ok, s.bad = {}, {}, {}, {}
    def set(s, k, v, src):
        if v is not None: s.v[k], s.s[k] = v, src
    def check(s, k, geom, tol=1.0, rel=0.0):
        """Compare the already set value with a geometry-measured one."""
        if geom is None or k not in s.v: return
        if abs(s.v[k] - geom) <= max(tol, rel * abs(s.v[k])): s.ok[k] = "geometry:agree"
        else: s.bad[k] = {("catalogue" if s.s[k] == "catalogue" else "name"): s.v[k], "geometry": round(float(geom), 2)}


def _num(m, i):
    return float(m.group(i)) if "." in m.group(i) else int(m.group(i))


THREAD = re.compile(r"\bM(\d+(?:\.\d+)?)\b")
THREAD_LEN = re.compile(r"\bM(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)")
STDREF = re.compile(r"(?<![A-Za-z])(DIN EN|DIN|ISO)\s*-?\s*(\d{3,5})", re.I)


# ---------------------------------------------------------------- per-type handlers: (n=norm name, o=lower orig, part, V, out) -> P
def h_pulley(n, o, part, V, out):
    p = P()
    m = re.search(r"HTD\s*(\d)M", n, re.I)
    if m: p.set("profile", f"HTD{m.group(1)}M", "name"); p.set("pitch_mm", int(m.group(1)), "name")
    elif (m := re.search(r"\bM(\d)_T\d+", n)): p.set("profile", f"M{m.group(1)}", "name"); p.set("pitch_mm", int(m.group(1)), "name")
    if (m := re.search(r"(?:\b|_)(\d+)T(?![A-Za-z0-9])|_T(\d+)(?!\d)", n)): p.set("teeth", int(m.group(1) or m.group(2)), "name")
    if (m := re.search(r"(?:\b|_)(\d+)W(?![A-Za-z0-9])|_W(\d+)(?!\d)", n)): p.set("belt_width_mm", int(m.group(1) or m.group(2)), "name")
    g = teeth_from_stl(V)
    if g is not None:
        if "teeth" in p.v:
            if p.v["teeth"] == g: p.ok["teeth"] = "geometry:agree"
            else: p.bad["teeth"] = {"name": p.v["teeth"], "geometry": g}
        else: p.set("teeth", g, "geometry")
    if "teeth" in p.v and "pitch_mm" in p.v:
        p.set("pitch_diameter_mm", round(p.v["teeth"] * p.v["pitch_mm"] / math.pi, 2), "derived")
    p.set("max_diameter_mm", round(max(dims(part)), 1), "geometry")
    return p


def h_belt(n, o, part, V, out):
    p = P()
    if (m := re.search(r"(\d+)\s*M(\d)\b", n)):
        p.set("width_mm", int(m.group(1)), "name"); p.set("pitch_mm", int(m.group(2)), "name")
        p.set("profile", f"HTD{m.group(2)}M", "catalogue")   # RU "10M5" = 10 mm wide, 5 mm pitch; the HTD profile is an assumption
        p.check("width_mm", min(dims(part)), tol=0.5)
    return p


def h_conveyor(n, o, part, V, out):
    p = P(); p.set("width_mm", round(min(dims(part)), 1), "geometry")   # closed loop: thinnest bbox side = belt width (assumed)
    return p


BEARING_CAT = {   # designation -> (d, D, B) mm, datasheet knowledge
    "6000": (10, 26, 8), "608": (8, 22, 7), "61901": (12, 24, 6), "61800": (10, 19, 5), "6902": (15, 28, 7)}


def h_bearing(n, o, part, V, out):
    p = P(); d3 = None
    m = re.search(r"(\d+)x(\d+)x(\d+)[_\-\s]*(6\d{2,4}|608)[_\-\s]*(2RS|RS|ZZ|2Z)?", n, re.I)
    if m:
        d3 = tuple(int(m.group(i)) for i in (1, 2, 3)); des, seal = m.group(4), (m.group(5) or "").upper()
    else:
        m = re.search(r"\bSS\s*(6\d{3})\s*(2RS|RS|ZZ)?", n, re.I)
        if not m: return _bearing_other(n, part, p)
        des, seal = m.group(1), (m.group(2) or "").upper()
    p.set("designation", des + (f"-{seal}" if seal else ""), "name")
    if seal: p.set("seal", seal, "name")
    comp = re.search(r"inner ring|outer ring|seal$", n, re.I)
    if comp: p.set("component", comp.group(0).lower().replace(" ", "_"), "name")
    cat = BEARING_CAT.get(des)
    if d3:
        p.set("d_mm", d3[0], "name"); p.set("D_mm", d3[1], "name"); p.set("B_mm", d3[2], "name")
        if cat and cat != d3: p.bad["dimensions"] = {"name": d3, "catalogue": cat}
        elif cat: p.ok["dimensions"] = "catalogue:agree"
    elif cat:
        for k, v in zip(("d_mm", "D_mm", "B_mm"), cat): p.set(k, v, "catalogue")
    dm = sorted(dims(part)); c = comp.group(0).lower() if comp else ""
    if not comp or c == "outer ring": p.check("B_mm", dm[0], tol=0.4); p.check("D_mm", dm[2], tol=0.6)
    elif c == "inner ring": p.check("B_mm", dm[0], tol=0.4)
    return p


def _bearing_other(n, part, p):
    if re.search(r"KFL\s*0?01", n, re.I):
        p.set("designation", "KFL001", "name"); p.set("housing", "flange_rhombic_2_bolt", "catalogue"); p.set("bore_d_mm", 12, "catalogue")
    elif re.search(r"angular contact", n, re.I): p.set("bearing_type", "angular_contact_ball", "name")
    return p


def h_fastener(n, o, part, V, out):
    p = P(); nl = n.lower()
    forms = [(r"socket thin pilot cap screw", "thin_socket_head_screw", "name"), (r"socket head cap screw", "socket_head_cap_screw", "name"),
             (r"brugola", "socket_head_cap_screw", "catalogue"), (r"hex screw", "hex_head_screw", "name"), (r"hex nut", "hex_nut", "name"),
             (r"screw|" + I.words("screw"), "screw", "name")]
    for rx, f, src in forms:
        if re.search(rx, nl) or re.search(rx, o): p.set("fastener_type", f, src); break
    if (m := STDREF.search(n)): p.set("standard", f"{m.group(1).upper()} {m.group(2)}", "name")
    if (m := THREAD_LEN.search(n)):
        p.set("thread", f"M{m.group(1)}", "name"); p.set("length_mm", _num(m, 2), "name")
    elif (m := THREAD.search(n)): p.set("thread", f"M{m.group(1)}", "name")
    if "thread" in p.v: p.set("nominal_diameter_mm", float(p.v["thread"][1:]) if "." in p.v["thread"] else int(p.v["thread"][1:]), "derived")
    if (m := re.search(r"---\s*(\d\.\d)N?\b", n)): p.set("strength_class", m.group(1), "name")
    if re.search(r"INOX 304", n, re.I): p.set("material_grade", "AISI 304", "name")
    if "length_mm" in p.v and "screw" in p.v.get("fastener_type", ""):
        L, dn = p.v["length_mm"], p.v.get("nominal_diameter_mm", 6); tot = max(dims(part))
        p.set("overall_length_mm", round(tot, 1), "geometry")          # incl. head
        if L <= tot + 0.3 and tot <= L + 1.3 * dn: p.ok["length_mm"] = "geometry:agree"
        else: p.bad["length_mm"] = {"name": L, "geometry": round(tot, 1)}
    return p


def h_threaded(n, o, part, V, out):   # rivet_nut / stud / foot
    p = P()
    if (m := THREAD_LEN.search(n)): p.set("thread", f"M{m.group(1)}", "name"); p.set("length_mm", _num(m, 2), "name")
    elif (m := THREAD.search(n)): p.set("thread", f"M{m.group(1)}", "name")
    if re.search(I.words("press_in"), o): p.set("installation", "press_in", "name")
    if "length_mm" in p.v and re.search(I.words("stud"), o):
        tot = max(dims(part)); p.set("overall_length_mm", round(tot, 1), "geometry"); p.check("length_mm", tot, tol=1, rel=0.1)
    return p


NEMA_FACE = {"17": 42.3, "23": 56.4, "34": 86.4}


def h_stepper(n, o, part, V, out):
    p = P()
    m = re.search(r"NEMA\s*(\d\d)", n, re.I) or re.search(r"(?:Stepper|Steper)\s*(?:Body|rotor)?\s*(\d\d)\b", n, re.I)
    if m and m.group(1) in NEMA_FACE:
        p.set("frame", f"NEMA{m.group(1)}", "name"); p.set("frame_size_mm", NEMA_FACE[m.group(1)], "catalogue")
    if (m := re.search(r"([A-Z]*\d{2}STH(\d{2,3})-(\d{2})(\d{2})A)", n)):
        p.set("model", m.group(1), "name"); p.set("body_length_mm", int(m.group(2)), "name")
        p.set("rated_current_a", int(m.group(3)) / 10, "catalogue")   # vendor code convention 28.. = 2.8 A
    if (m := re.search(r"Stepper (Body|rotor)", n, re.I)): p.set("component", m.group(1).lower(), "name")
    dm = dims(part)
    if "frame_size_mm" in p.v and p.v.get("component") != "rotor": p.check("frame_size_mm", min(dm), tol=1.0)
    if "body_length_mm" in p.v:
        p.check("body_length_mm", min(dm, key=lambda v: abs(v - p.v["body_length_mm"])), tol=1.5)
    return p


def h_driver(n, o, part, V, out):
    p = P()
    if (m := re.search(r"\b(DM\d{3}[A-Z]?|TB\d{4})\b", n)): p.set("model", m.group(1), "name")
    if p.v.get("model", "").startswith("DM"): p.set("maker", "Leadshine", "catalogue")
    if (m := re.search(r"\b(Sink|Cover|PCB|sheetmetal)\b", n, re.I)): p.set("component", m.group(1).lower(), "name")
    return p


def h_psu(n, o, part, V, out):
    p = P()
    if (m := re.search(r"\b(NES|LRS)-(\d+)", n)):
        p.set("model", f"{m.group(1)}-{m.group(2)}", "name"); p.set("power_w", int(m.group(2)), "name"); p.set("maker", "Mean Well", "catalogue")
    return p


def h_dcdc(n, o, part, V, out):
    p = P()
    if re.search(r"ARCELI", n, re.I): p.set("maker", "ARCELI", "name")
    if (m := re.search(r"([\d.]+)-([\d.]+)V to ([\d.]+)-([\d.]+)V", n)):
        for k, i in (("input_v_min", 1), ("input_v_max", 2), ("output_v_min", 3), ("output_v_max", 4)): p.set(k, float(m.group(i)), "name")
    return p


def h_mcu(n, o, part, V, out):
    p = P()
    if re.search(r"esp ?32", n, re.I): p.set("model", "ESP32", "name")
    if re.search(r"arduino nano", n, re.I): p.set("model", "Arduino Nano", "name")
    if re.search(r"usb-c", n, re.I): p.set("usb_connector", "USB-C", "name")
    if re.search(r"shield|adapter", n, re.I): p.set("form", "shield" if "shield" in n.lower() else "terminal_adapter", "name")
    return p


def h_cylinder(n, o, part, V, out):
    p = P(); nn = n.replace("_", " ")
    if (m := re.search(r"\b(MAL\d+[A-Z]?)\s+(\d+)(?:x(\d+))?", nn)):
        p.set("model", m.group(1), "name"); p.set("bore_mm", int(m.group(2)), "name")
        if m.group(3): p.set("stroke_mm", int(m.group(3)), "name")
        comp = [("rear_cap", r"ARKA KAPAK"), ("piston", r"P.?STON"), ("front_cap", r"KAPAK"), ("tube", r"BORU"), ("rod", r"\bM.?L\b")]
    elif (m := re.search(r"\bTN(\d+)X(\d+)", nn, re.I)):
        p.set("model", f"TN{m.group(1)}X{m.group(2)}", "name"); p.set("bore_mm", int(m.group(1)), "name"); p.set("stroke_mm", int(m.group(2)), "name")
        p.set("style", "twin_rod", "catalogue")
        comp = [("body", r"BODY"), ("rod", r"ROD")]
    else: return p
    for c, rx in comp:
        if re.search(rx, nn, re.I): p.set("component", c, "name"); break
    return p


def h_valve(n, o, part, V, out):
    p = P()
    if (m := re.search(r"\b(4V2\d{2}-\d{2}(?:-[A-Z])?)", n)):
        p.set("model", m.group(1), "name"); p.set("maker", "AirTAC", "catalogue")
        if (c := re.search(r"-(fati|hougai|qiangai|xianquan)$", n)):
            p.set("component", {"fati": "body", "hougai": "back_cover", "qiangai": "cover", "xianquan": "coil"}[c.group(1)], "catalogue")
    return p


def h_frl(n, o, part, V, out):
    p = P()
    if (m := re.search(r"\b(TM0000\d\d)", n)): p.set("model", m.group(1), "name")
    if (m := re.search(r"G(\d)-(\d)", n)): p.set("thread", f"G{m.group(1)}/{m.group(2)}", "name")
    if (m := re.search(r"(corpo|vaso|cupola|pomello|ghiera|filtro|staffa)", n, re.I)):
        p.set("component", {"corpo": "body", "vaso": "bowl", "cupola": "bonnet", "pomello": "knob", "ghiera": "ring_nut",
                            "filtro": "sinter_filter", "staffa": "bracket"}[m.group(1).lower()], "catalogue")
    return p


def h_gauge(n, o, part, V, out):
    p = P()
    if (m := re.search(r"KPM\s*(\d+)", n)):
        p.set("model", f"KPM {m.group(1)}", "name"); p.set("dial_diameter_mm", int(m.group(1)), "name"); p.check("dial_diameter_mm", max(dims(part)), tol=1.0)
    return p


def h_pfit(n, o, part, V, out):
    p = P()
    if re.search(I.words("tee"), o): p.set("fitting_type", "tee", "name")
    if re.search(I.words("flow_control"), o): p.set("fitting_type", "flow_control", "name")
    if (m := re.search(r"\bT (\d+) MM|\b0?(\d) NBPT", n)): p.set("tube_od_mm", int(m.group(1) or m.group(2)), "name")
    return p


def h_vacgen(n, o, part, V, out):
    p = P()
    if (m := re.search(r"cv(\d+)hsm", n, re.I)): p.set("model", f"CV{m.group(1)}HSM", "name")
    return p


def h_channel(n, o, part, V, out):
    p = P()
    if (m := re.search(r"Cable channel (\d+)x(\d+)_(\d+)", n, re.I)):
        p.set("width_mm", int(m.group(1)), "name"); p.set("height_mm", int(m.group(2)), "name"); p.set("length_mm", int(m.group(3)), "name")
        d = sorted(dims(part)); p.check("length_mm", d[2], tol=1); p.check("height_mm", d[1], tol=1.5); p.check("width_mm", d[0], tol=1, rel=0.1)
    return p


def h_rail(n, o, part, V, out):
    p = P()
    if (m := re.search(r"_(\d+)$", n)): p.set("length_mm", int(m.group(1)), "name")
    d = sorted(dims(part)); p.set("width_mm", round(d[1], 1), "geometry"); p.set("height_mm", round(d[0], 1), "geometry")
    if p.v.get("width_mm") == 35 and p.v.get("height_mm") == 7.5: p.set("profile", "TS35x7.5", "catalogue")
    if "length_mm" in p.v: p.check("length_mm", d[2], tol=1)
    return p


def h_connector(n, o, part, V, out):
    p = P(); nn = n.replace("_", " ")
    if re.search(r"plug 200mil", nn, re.I): p.set("pitch_mm", 5.08, "catalogue")
    elif (m := re.search(r"(\d+)[._ ](\d+)\s*MM", nn, re.I)): p.set("pitch_mm", float(f"{m.group(1)}.{m.group(2)}"), "name")
    elif (m := re.search(r"(\d+)\s*MM", nn, re.I)): p.set("pitch_mm", float(m.group(1)), "name")
    if (m := re.search(r"(\d+)\s*PIN|\bN(\d)\b", nn, re.I)): p.set("pins", int(m.group(1) or m.group(2)), "name")
    if re.search(r"\bmale\b", nn, re.I): p.set("gender", "male", "name")
    if re.search(r"closed", nn, re.I): p.set("housing", "closed", "name")
    return p


def h_dip(n, o, part, V, out):
    p = P(); nn = n.replace("_", " ")
    if (m := re.search(r"(\d+)\s*PIN", nn, re.I)): p.set("pins", int(m.group(1)), "name")
    if (m := re.search(r"(\d+)\s*position", nn, re.I)): p.set("positions", int(m.group(1)), "name")
    return p


def h_led(n, o, part, V, out):
    p = P()
    cols = [c.lower() for c in re.findall(r"GREEN|RED|YELLOW|BLUE|WHITE", n, re.I)]
    if cols: p.set("colors", cols, "name")
    return p


def h_gland(n, o, part, V, out):
    p = P()
    if (m := re.search(r"PG(\d+)", n)): p.set("thread", f"PG{m.group(1)}", "name")
    return p


def h_mosfet(n, o, part, V, out):
    p = P()
    if (m := re.search(r"IRF\d+\w*", n)): p.set("model", m.group(0), "name")
    return p


def h_none(n, o, part, V, out):
    return P()


# ordered: first regex that matches the lower-cased original / normalised name wins
RULES = [
    ("pulley",            rf"\b{I.words('pulley')}\b|\bpulley\b",                    h_pulley),
    ("belt",              rf"\b{I.words('belt')}\b",                               h_belt),
    ("conveyor_belt",     rf"\b{I.words('conveyor_belt')}\b",                      h_conveyor),
    ("foot",              rf"\b{I.words('foot')}\b",                               h_threaded),
    ("stud",              rf"\b{I.words('stud')}\b",                               h_threaded),
    ("rivet_nut",         I.words("rivet_nut"),                                    h_threaded),
    ("fastener",          rf"socket (head|thin)|hex (screw|nut)|brugola|^{I.words('screw')}\b|\bscrew\b", h_fastener),
    ("bearing",           r"\d+x\d+x\d+[_\- ]*(6\d{2,4}|608)|\bss 6\d{3}|angular contact ball bearing", h_bearing),
    ("bearing_unit",      r"\bkfl0?01",                                             h_bearing),
    ("stepper_motor",     r"nema|stepper|steper",                                   h_stepper),
    ("stepper_driver",    r"\bdm\d{3}|tb6600|stepdriver|driverpcb",                 h_driver),
    ("power_supply",      r"\b(nes|lrs)-\d+",                                       h_psu),
    ("dc_dc_converter",   r"dc-dc",                                                 h_dcdc),
    ("mosfet_module",     r"\birf\d+",                                              h_mosfet),
    ("mcu_board",         r"esp ?32|arduino",                                       h_mcu),
    ("pneumatic_cylinder", r"\bmal\d{4}|\btn\d+x\d+",                               h_cylinder),
    ("valve",             r"\b4v2\d{2}-",                                           h_valve),
    ("air_preparation_unit", r"\btm0000\d\d|serie tm",                              h_frl),
    ("pressure_gauge",    r"manometr",                                              h_gauge),
    ("pneumatic_fitting", I.words("pneumatic_fitting"),                            h_pfit),
    ("vacuum_generator",  r"generator_cv",                                          h_vacgen),
    ("suction_cup",       I.words("suction_cup"),                                  h_none),
    ("cable_channel",     r"cable channel \d+x\d+",                                 h_channel),
    ("din_rail",          rf"\b{I.words('din_rail')}",                              h_rail),
    ("connector",         r"connector_|^plug 200mil|\bplug \d",                     h_connector),
    ("terminal_block",    r"terminal block|" + I.words("terminal_block"),          h_none),
    ("dip_switch",        r"dip.?switch",                                           h_dip),
    ("indicator_led",     r"\bled\b|" + I.words("indicator_led"),                  h_led),
    ("cable_gland",       r"^pe_pg\d+",                                             h_gland),
    ("pcb",               r"^pcb$",                                                 h_none),
    ("heatsink",          rf"^{I.words('heatsink')}$",                               h_none),
    ("adc_board",         rf"\b{I.words('adc_board')}\b",                          h_none),
    ("encoder",           r"encoder",                                               h_none),
]
RULES = [(t, re.compile(rx, re.I), h) for t, rx, h in RULES]


def _project_code(name):
    """How long the maker's project code is that a name starts with (`ABC-1 ` before the drawing number: upper-case
    letters, a dash, one digit), 0 when it has none."""
    m = re.match(r"([^\W\d_]{2,6})-\d(?:[ _]+|$)", name)
    return m.end() if m and m.group(1).isupper() else 0


def extract_params(part, out, geometry=True):
    """Fill type / params / params_source / params_check / params_conflicts on a parts.json entry (in place)."""
    part["type"] = None
    for k in ("params", "params_source", "params_check", "params_conflicts"): part.pop(k, None)
    if part.get("drawing") and not re.search(rf"\b{I.words('pulley')}\b", part["name"] or "", re.I):
        return part        # own drawing = made part, its geometry is its own CAD; only pulleys are decoded
    k = _project_code(part["name"] or "")
    n = norm(part["name"])[k:].strip()
    if part.get("drawing"): n = re.sub(r"^\d\d(\.\d\d)+ ", "", n)
    o = (part["name"] or "").lower()[k:].strip()
    if part.get("drawing"): o = re.sub(r"^\d\d(\.\d\d)+ ", "", o)
    for t, rx, h in RULES:
        if rx.search(o) or rx.search(n.lower()) or rx.search(n.lower().replace("_", " ")):
            V = load_stl(out, part) if (t == "pulley" and geometry) else None
            p = h(n, o, part, V, out)
            part["type"] = t
            if p.v: part["params"], part["params_source"] = p.v, p.s
            if p.ok: part["params_check"] = p.ok
            if p.bad: part["params_conflicts"] = p.bad
            return part
    return part


def apply_all(parts, out, geometry=True):
    for p in parts: extract_params(p, out, geometry=geometry)


def report(parts):
    import collections
    typed = collections.OrderedDict(); untyped = []
    for p in parts:
        if p.get("type"): typed.setdefault(p["type"], []).append(p)
        elif not p.get("drawing"): untyped.append(p)
    made = sum(1 for p in parts if p.get("drawing") and not p.get("type"))
    print(f"params: {sum(len(v) for v in typed.values())} typed, {sum(1 for p in parts if p.get('params'))} with params, "
          f"{len(untyped)} untyped non-made, {made} made (drawing) parts not decoded")
    print(f"  {'type':22s}{'parts':>6s}{'w/params':>9s}{'conflicts':>10s}")
    for t, ps in sorted(typed.items(), key=lambda kv: -len(kv[1])):
        print(f"  {t:22s}{len(ps):6d}{sum(1 for p in ps if p.get('params')):9d}{sum(1 for p in ps if p.get('params_conflicts')):10d}")
    print("pulleys (teeth: name vs geometry):")
    for p in parts:
        if p.get("type") == "pulley":
            pa, pc = p.get("params", {}), p.get("params_conflicts", {})
            st = "agree" if p.get("params_check", {}).get("teeth") else f"CONFLICT {pc['teeth']}" if "teeth" in pc else "geometry n/a"
            print(f"  {p['id']:16s} teeth={pa.get('teeth')} ({p.get('params_source', {}).get('teeth')}) {st}  {p['name']}")
    print("conflicts:")
    for p in parts:
        if p.get("params_conflicts"): print(f"  {p['id'][:40]}: {p['params_conflicts']}")
    print("untyped non-made:", [f"{p['kind'][:3]}:{p['name']}" for p in untyped])
