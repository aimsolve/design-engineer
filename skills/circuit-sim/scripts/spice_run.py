#!/usr/bin/env python3
"""Run a SPICE netlist in ngspice (batch) and plot a Bode or transient PNG + print the numbers that matter.

  python spice_run.py ../templates/rc_antialias.cir --ac "dec 50 1 10meg" --probe "v(outp,outn)" \
         --ref "v(inp,inn)" --at 35.1k,256k -o out/rc
  python spice_run.py ../templates/supply_ripple.cir --tran "0.2u 3m" --probe "v(avdd_lc),v(avdd_rc)" \
         --skip 1m -o out/ripple
  python spice_run.py ../templates/supply_ripple.cir --ac "dec 50 100 10meg" --probe "v(avdd_lc),v(avdd_rc)" \
         --ref "v(bus)" --at 35.1k -o out/ripple_ac

Any .control block in the netlist is removed; this script adds its own. --param name=value overrides a .param
(repeatable) — use it for sweeps instead of editing the file. Needs ngspice on PATH, numpy, matplotlib.
Outputs: <out>.png, <out>.csv, <out>.json (numbers), <out>.log (ngspice output — read it on any doubt).
"""
import argparse, json, re, subprocess, sys, tempfile
from pathlib import Path

import numpy as np


def si(s):
    m = re.fullmatch(r"\s*([-+0-9.eE]+)\s*(meg|[fpnumkgt]?)\w*\s*", s, re.I)
    if not m:
        raise ValueError(s)
    mult = {"": 1, "f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3, "k": 1e3, "meg": 1e6, "g": 1e9, "t": 1e12}
    return float(m[1]) * mult[m[2].lower()]


def run(netlist, analysis, vectors, params, workdir):
    text = Path(netlist).read_text()
    text = re.sub(r"(?ims)^\s*\.control.*?^\s*\.endc\s*$", "", text)
    text = re.sub(r"(?im)^\s*\.end\s*$", "", text)
    for p in params:
        k, v = p.split("=", 1)
        text, n = re.subn(rf"(?im)^(\s*\.param\s+{re.escape(k)}\s*=\s*)\S+", rf"\g<1>{v}", text)
        if not n:
            text += f"\n.param {k}={v}\n"
    data = Path(workdir) / "data.txt"
    ctl = (f"\n.control\nset wr_singlescale\nset wr_vecnames\noption numdgt=12\n{analysis}\n"
           f"wrdata {data} {' '.join(vectors)}\nquit\n.endc\n.end\n")
    cir = Path(workdir) / "run.cir"
    cir.write_text(text + ctl)
    p = subprocess.run(["ngspice", "-b", str(cir)], capture_output=True, text=True, timeout=600)
    if not data.exists():
        sys.exit(f"ngspice wrote no data (exit {p.returncode}):\n{p.stdout[-3000:]}\n{p.stderr[-3000:]}")
    lines = data.read_text().split("\n")
    head = lines[0].split()
    arr = np.loadtxt(lines[1:], ndmin=2)
    return head, arr, p.stdout + p.stderr


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("netlist")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--ac", help='ngspice ac args, e.g. "dec 50 1 10meg"')
    g.add_argument("--tran", help='ngspice tran args, e.g. "0.2u 3m"')
    ap.add_argument("--probe", required=True, help='comma list of node expressions, e.g. "v(out),v(a,b)"')
    ap.add_argument("--ref", help="AC: reference vector, gain = probe/ref (default: absolute)")
    ap.add_argument("--at", default="", help="AC: frequencies to report attenuation at, e.g. 35.1k,256k")
    ap.add_argument("--skip", default="0", help="TRAN: ignore the first T seconds (start-up) for the stats")
    ap.add_argument("--param", action="append", default=[], help="override .param, e.g. --param R=2k")
    ap.add_argument("-o", "--out", required=True, help="output path prefix")
    ap.add_argument("--title", default="")
    a = ap.parse_args()

    probes = [s.strip() for s in re.split(r",(?![^()]*\))", a.probe) if s.strip()]
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    res = {"netlist": str(a.netlist), "params": a.param}
    with tempfile.TemporaryDirectory() as td:
        if a.ac:
            vecs = [f"real({p})" for p in probes] + [f"imag({p})" for p in probes]
            if a.ref:
                vecs += [f"real({a.ref})", f"imag({a.ref})"]
            head, arr, log = run(a.netlist, f"ac {a.ac}", vecs, a.param, td)
            f = arr[:, 0]
            n = len(probes)
            ref = arr[:, 2 * n + 1] + 1j * arr[:, 2 * n + 2] if a.ref else 1
            fig, (am, ph) = plt.subplots(2, 1, figsize=(9, 7), sharex=True)
            cols = {"freq_hz": f}
            for i, p in enumerate(probes):
                h = (arr[:, 1 + i] + 1j * arr[:, 1 + n + i]) / ref
                db = 20 * np.log10(np.abs(h) + 1e-300)
                deg = np.degrees(np.unwrap(np.angle(h)))
                cols[f"{p}_db"], cols[f"{p}_deg"] = db, deg
                d0 = db[0]
                below = np.where(db <= d0 - 3.0103)[0]
                fc = float(np.interp(d0 - 3.0103, [db[below[0]], db[below[0] - 1]], [f[below[0]], f[below[0] - 1]])) \
                    if below.size and below[0] > 0 else None
                r = {"gain_at_fmin_db": round(float(d0), 3), "f_minus3db_hz": fc and round(fc, 2),
                     "peak_db": round(float(db.max()), 2), "f_peak_hz": round(float(f[db.argmax()]), 1)}
                for fs in filter(None, a.at.split(",")):
                    r[f"db_at_{fs}"] = round(float(np.interp(np.log10(si(fs)), np.log10(f), db)), 2)
                res[p] = r
                am.semilogx(f, db, label=p)
                ph.semilogx(f, deg, label=p)
                if fc:
                    am.axvline(fc, ls=":", lw=0.8, color=am.lines[-1].get_color())
            for fs in filter(None, a.at.split(",")):
                am.axvline(si(fs), color="grey", lw=0.6)
                am.text(si(fs), am.get_ylim()[0], f" {fs}Hz", rotation=90, fontsize=8, va="bottom", color="grey")
            am.set(ylabel=f"gain, dB{' re ' + a.ref if a.ref else ''}", title=a.title or f"{Path(a.netlist).name} AC")
            ph.set(xlabel="Hz", ylabel="phase, °")
            for ax in (am, ph):
                ax.grid(True, which="both", alpha=0.3)
                ax.legend(fontsize=8)
        else:
            head, arr, log = run(a.netlist, f"tran {a.tran}", probes, a.param, td)
            t = arr[:, 0]
            keep = t >= si(a.skip)
            fig, ax = plt.subplots(figsize=(10, 5))
            cols = {"time_s": t}
            for i, p in enumerate(probes):
                y = arr[:, 1 + i]
                cols[p] = y
                yk = y[keep]
                res[p] = {"mean": float(yk.mean()), "pp": float(np.ptp(yk)), "rms_ac": float(yk.std()),
                          "min": float(yk.min()), "max": float(yk.max())}
                ax.plot(t * 1e3, y, lw=0.8, label=f"{p}  p-p {np.ptp(yk) * 1e3:.2f} mV")
            ax.axvspan(0, si(a.skip) * 1e3, color="grey", alpha=0.12, label="start-up (not in stats)")
            ax.set(xlabel="ms", ylabel="V", title=a.title or f"{Path(a.netlist).name} transient")
            ax.grid(alpha=0.3)
            ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out.with_suffix(".png"), dpi=110)
    np.savetxt(out.with_suffix(".csv"), np.column_stack(list(cols.values())), delimiter=",",
               header=",".join(cols), comments="")
    out.with_suffix(".log").write_text(log)
    out.with_suffix(".json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))
    print(f"-> {out}.png .csv .json .log")


if __name__ == "__main__":
    main()
