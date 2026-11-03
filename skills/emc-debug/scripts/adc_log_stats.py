#!/usr/bin/env python3
"""Per-state noise / drift statistics for an ADC log matrix (emc-debug, ADS1220 playbook).

  python adc_log_stats.py matrix.csv --vref 5 --gain 128 [--exc 5] [--mvv 2 --capacity-g 1000]
         [--ds-noise-uv 0.09] -o out/
  python adc_log_stats.py --demo -o out/        # synthetic matrix, to see the output

Input CSV (template: ../templates/ads1220_log_matrix.csv): t_s,state,code[,temp_c]; '#' lines ignored.
code = raw signed 24-bit code. µV at the ADC input = code * VREF / (gain * 2^23).
grams (optional) = µV / (mV/V * excitation * 1000) * capacity.

Out: stats.csv, stats.md (per-state table + matrix contrasts), matrix.png (time series + spectrum per state).
RMS noise is after removing a linear fit (drift reported separately as µV/min). p-p is of the detrended signal.
Outliers = |x - median| > 6 * 1.4826 * MAD (bad reads or bursts; counted, not removed).
Needs numpy, scipy, matplotlib.
"""
import argparse, csv, sys
from collections import OrderedDict
from pathlib import Path

import numpy as np

CONTRASTS = [  # (numerator, denominator, what a ratio >> 1 points to)
    ("B", "A", "supply switched on (no chopping) disturbs the ADC"),
    ("C", "B", "driver chopping (holding current) is a source"),
    ("D", "C", "<1: motor wires / cable coupling is a path (D quieter than C)"),
    ("E@C", "E@A", "board / AVDD / reference carries the noise (inputs internally shorted)"),
    ("F@C", "F@A", "noise still there without the cell → board/ADC side; ≈1 while C>>A → cell + cable"),
    ("G", "C", "<1: ground loop through the logger / PC"),
]


def read_csv(path):
    rows = [r for r in csv.reader(l for l in open(path, encoding="utf-8") if l.strip() and not l.startswith("#"))]
    head = [h.strip() for h in rows[0]]
    idx = {h: i for i, h in enumerate(head)}
    for need in ("t_s", "state", "code"):
        if need not in idx:
            sys.exit(f"missing column {need}; header is {head}")
    data = OrderedDict()
    for r in rows[1:]:
        s = r[idx["state"]].strip()
        d = data.setdefault(s, {"t": [], "code": [], "temp": []})
        d["t"].append(float(r[idx["t_s"]]))
        d["code"].append(float(r[idx["code"]]))
        if "temp_c" in idx and len(r) > idx["temp_c"] and r[idx["temp_c"]].strip():
            d["temp"].append(float(r[idx["temp_c"]]))
    return {k: {kk: np.asarray(vv) for kk, vv in v.items()} for k, v in data.items()}


def demo(path, rate=20.0, dur=60.0):
    """Synthetic matrix: chopping adds noise + a step (not ratiometric), unplugging motors halves it."""
    rng = np.random.default_rng(1)
    lsb_uv = 5.0 / (128 * 2**23) * 1e6
    t = np.arange(0, dur, 1 / rate)
    spec = {"A": (0.10, 0, 0), "B": (0.12, 0.5, 0), "C": (0.60, 4.0, 0.8), "D": (0.30, 4.0, 0.8),
            "E@A": (0.09, 0, 0), "E@C": (0.11, 0, 0), "F@A": (0.10, 0, 0), "F@C": (0.13, 0, 0), "G": (0.58, 4.0, 0.8)}
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_s", "state", "code", "temp_c"])
        for s, (rms, step, drift_min) in spec.items():
            uv = 1000 + step + drift_min * t / 60 + rng.normal(0, rms, t.size)
            if s.startswith("E"):
                uv -= 1000
            for ti, u in zip(t, uv):
                w.writerow([f"{ti:.3f}", s, int(round(u / lsb_uv)), 25 + (5 if s in "CDG" else 0)])
    return path


def stats(t, uv):
    n = uv.size
    k, b = np.polyfit(t - t[0], uv, 1) if n > 2 else (0.0, uv.mean())
    res = uv - (k * (t - t[0]) + b)
    med = np.median(uv)
    mad = 1.4826 * np.median(np.abs(uv - med)) or 1e-12
    return dict(n=n, dur_s=t[-1] - t[0], rate_sps=(n - 1) / (t[-1] - t[0]) if n > 1 else 0, mean_uv=uv.mean(),
                rms_uv=res.std(ddof=1), pp_uv=np.ptp(res), drift_uv_min=k * 60,
                outliers=int(np.sum(np.abs(uv - med) > 6 * mad)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", nargs="?")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--vref", type=float, default=5.0, help="reference voltage in V (2.048 internal, or excitation)")
    ap.add_argument("--gain", type=float, default=128)
    ap.add_argument("--exc", type=float, help="bridge excitation V (default = vref)")
    ap.add_argument("--mvv", type=float, help="cell sensitivity mV/V (for grams)")
    ap.add_argument("--capacity-g", type=float, help="cell rated capacity in grams (for grams)")
    ap.add_argument("--ds-noise-uv", type=float, help="datasheet noise floor µVrms at this rate+gain (cite it!)")
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.demo:
        a.csv, a.vref, a.gain, a.mvv, a.capacity_g, a.ds_noise_uv = demo(out / "demo_matrix.csv"), 5.0, 128, 2.0, 1000, 0.09
    if not a.csv:
        ap.error("csv required (or --demo)")
    exc = a.exc or a.vref
    uv_per_code = a.vref / (a.gain * 2**23) * 1e6
    g_per_uv = a.capacity_g / (a.mvv * exc * 1000) if a.mvv and a.capacity_g else None

    data = read_csv(a.csv)
    S = OrderedDict()
    for s, d in data.items():
        uv = d["code"] * uv_per_code
        S[s] = stats(d["t"], uv)
        if d["temp"].size:
            S[s]["temp_c"] = f"{d['temp'].min():.1f}-{d['temp'].max():.1f}"

    ref = S.get("A")
    cols = ["n", "rate_sps", "mean_uv", "rms_uv", "pp_uv", "drift_uv_min", "outliers"]
    with open(out / "stats.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["state"] + cols + ["rms_codes", "rms_g", "pp_g", "shift_vs_A_uv", "shift_vs_A_g", "rms_vs_A",
                                        "rms_vs_datasheet", "temp_c"])
        for s, v in S.items():
            shift = v["mean_uv"] - ref["mean_uv"] if ref and not s.startswith("E") else None
            w.writerow([s] + [round(v[c], 4) if isinstance(v[c], float) else v[c] for c in cols] + [
                round(v["rms_uv"] / uv_per_code, 1),
                round(v["rms_uv"] * g_per_uv, 4) if g_per_uv else "", round(v["pp_uv"] * g_per_uv, 4) if g_per_uv else "",
                round(shift, 3) if shift is not None else "",
                round(shift * g_per_uv, 4) if (shift is not None and g_per_uv) else "",
                round(v["rms_uv"] / ref["rms_uv"], 2) if ref else "",
                round(v["rms_uv"] / a.ds_noise_uv, 1) if a.ds_noise_uv else "", v.get("temp_c", "")])

    md = [f"# ADC log matrix: {Path(a.csv).name}\n",
          f"VREF {a.vref} V, gain {a.gain:g}, excitation {exc} V → 1 code = {uv_per_code * 1e3:.3f} nV"
          + (f", 1 µV = {g_per_uv:.3f} g ({a.mvv} mV/V, {a.capacity_g:g} g cell)" if g_per_uv else "")
          + (f"; datasheet floor {a.ds_noise_uv} µVrms (**cite doc/rev/table**)" if a.ds_noise_uv else "") + "\n",
          "| state | n | SPS | RMS µV | p-p µV | drift µV/min | shift vs A µV | RMS/A | RMS/ds | RMS g | outliers |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for s, v in S.items():
        shift = v["mean_uv"] - ref["mean_uv"] if ref and not s.startswith("E") else None
        md.append(f"| {s} | {v['n']} | {v['rate_sps']:.1f} | {v['rms_uv']:.3f} | {v['pp_uv']:.3f} | {v['drift_uv_min']:+.3f} | "
                  f"{'' if shift is None else f'{shift:+.3f}'} | {v['rms_uv'] / ref['rms_uv']:.2f} | "
                  f"{'' if not a.ds_noise_uv else f'{v['rms_uv'] / a.ds_noise_uv:.1f}'} | "
                  f"{'' if not g_per_uv else f'{v['rms_uv'] * g_per_uv:.4f}'} | {v['outliers']} |" if ref else f"| {s} | … |")
    md += ["\n## Contrasts the matrix is built for (rule of thumb: ratio within 0.67-1.5 = no evidence for that path)\n",
           "| contrast | RMS ratio | mean shift µV | a ratio far from 1 points to |", "|---|---|---|---|"]
    for num, den, meaning in CONTRASTS:
        if num in S and den in S:
            r = S[num]["rms_uv"] / S[den]["rms_uv"]
            md.append(f"| {num}/{den} | **{r:.2f}** | {S[num]['mean_uv'] - S[den]['mean_uv']:+.3f} | {meaning} |")
        else:
            md.append(f"| {num}/{den} | missing state | | {meaning} |")
    md.append("\nA mean shift A→B/C that scales with load on the cell → not ratiometric (R1). "
              "Drift of the same sign in every powered state, tracking temp → thermal (R6). Look at matrix.png.")
    (out / "stats.md").write_text("\n".join(md) + "\n")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from scipy.signal import welch
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.5))
    off = 0.0
    for s, d in data.items():
        uv = d["code"] * uv_per_code
        y = uv - uv.mean()
        ax1.plot(d["t"] - d["t"][0], y + off, lw=0.7)
        ax1.text(d["t"][-1] - d["t"][0], off, f" {s}  {S[s]['rms_uv']:.2f} µV rms", fontsize=8, va="center")
        off += max(6 * S[s]["rms_uv"], np.ptp(y)) * 1.1
        fs = S[s]["rate_sps"]
        if len(y) > 16 and fs > 0:
            f, p = welch(y, fs=fs, nperseg=min(256, len(y)))
            ax2.loglog(f[1:], np.sqrt(p[1:]), lw=0.9, label=s)
    ax1.set(xlabel="time in state, s", ylabel="µV (mean removed, stacked)", title="Readings per state")
    ax1.set_xlim(right=ax1.get_xlim()[1] * 1.25)
    ax2.set(xlabel="Hz", ylabel="µV/√Hz", title="Spectrum of readings (aliased interference shows as peaks)")
    ax2.legend(fontsize=7)
    ax2.grid(True, which="both", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out / "matrix.png", dpi=110)
    print((out / "stats.md").read_text())
    print(f"-> {out}/stats.csv, stats.md, matrix.png")


if __name__ == "__main__":
    main()
