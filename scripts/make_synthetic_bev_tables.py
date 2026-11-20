"""SYNTHETIC decoded-table packages (a .zip of per-frame CSVs + manifest) for a battery-electric vehicle, read by the
built-in profile `synthetic-bev-tables`. Generated from stated rules; never any customer's data.

  python scripts/make_synthetic_bev_tables.py --variant correction|healthy [--seed N] --out SYNTHETIC-x.zip

restarts: like correction without the SOC correction, then HV closed again 2-5 times after the trip (seeded), each time
discharging below the original trip level and tripping again. correction: constant discharge; the estimator counts charge, then pulls SOC down by `drop` points in 30 s (no current
change) while cells sag; the fault level rises 1 -> 2 -> 3 and both contactors open. healthy: counting only, no faults.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import random
import zipfile
from pathlib import Path

COLS = ["t_rel_s", "can_id", "i_pack_a", "soc_pct", "cap_ah", "v_pack_v", "v_cell_min_v", "v_cell_max_v", "idx_min", "fault_level", "k_pos", "k_neg"]


def rows(variant: str, seed: int):
    rnd = random.Random(seed)
    cap = 100.0
    cur = 6.0 + rnd.random() * 4
    soc0 = 55.0 + rnd.random() * 10
    drop = 30.0 + rnd.random() * 10
    t_corr = 1500.0 + rnd.random() * 300
    out_ref, out_fault = [], []
    q, t = 0.0, 0.0
    weak = rnd.randint(1, 96)
    while t <= 2400:
        bias = -drop * min(1.0, max(0.0, (t - t_corr) / 30)) if variant == "correction" else 0.0
        depth_on = variant in ("correction", "restarts")
        soc = soc0 - q / (cap * 3600) * 100 + bias
        depth = min(1.2, max(0.0, (t - 600) / 1500)) if depth_on else 0.0
        vmin = 3.20 - 0.70 * depth ** 1.5
        vmax = vmin + 0.01 + 0.25 * depth
        level = 0 if variant == "healthy" else (3 if vmin < 2.5 else 2 if vmin < 2.7 else 1 if vmin < 2.9 else 0)
        closed = 0 if level >= 3 else 1
        current = cur if closed else 0.0
        row = {"t_rel_s": f"{t:.1f}", "can_id": "0x2A0", "i_pack_a": f"{current:.2f}", "soc_pct": f"{round(round(max(0.0, soc) / 0.4) * 0.4, 1)}",
               "cap_ah": f"{cap:g}", "v_pack_v": f"{96 * (vmin + vmax) / 2:.1f}", "v_cell_min_v": f"{vmin:.3f}", "v_cell_max_v": f"{vmax:.3f}",
               "idx_min": str(weak), "fault_level": str(level), "k_pos": str(closed), "k_neg": str(closed)}
        (out_ref if t < 1200 else out_fault).append(row)
        q += current * 0.5
        t += 0.5
    files = {"reference_synbms.csv": out_ref, "fault_synbms.csv": out_fault}
    if variant == "restarts":
        files["repeat_synbms.csv"] = _restarts(rnd, cap, cur, weak, start=2400.0 + 600 + rnd.random() * 300)
    return files


def _restarts(rnd, cap, cur, weak, *, start):
    """After the protective trip: HV closed again `n` times; each time the cells fall below the original trip level and the
    protection trips again (fault level resets to 0 while open, between closures)."""
    rows, t = [], start
    n = rnd.randint(2, 5)
    soc = 1.2
    for _ in range(n):
        for _k in range(20):                      # open, fault reset
            rows.append(_row(t, 0.0, soc, cap, 2.62, 2.80, weak, 0, 0)); t += 0.5
        v = 2.60
        while v > 2.42:                           # closed, discharging below the original trip level (2.5 V)
            lvl = 3 if v < 2.48 else 2
            rows.append(_row(t, cur, soc, cap, v, 2.78, weak, lvl, 1)); t += 0.5
            v -= 0.004 + rnd.random() * 0.002
        for _k in range(6):                       # trip: open
            rows.append(_row(t, 0.0, soc, cap, 2.60, 2.80, weak, 3, 0)); t += 0.5
        t += 20.0 + rnd.random() * 40
    return rows


def _row(t, current, soc, cap, vmin, vmax, weak, level, closed):
    return {"t_rel_s": f"{t:.1f}", "can_id": "0x2A0", "i_pack_a": f"{current:.2f}", "soc_pct": f"{soc:.1f}", "cap_ah": f"{cap:g}",
            "v_pack_v": f"{96 * (vmin + vmax) / 2:.1f}", "v_cell_min_v": f"{vmin:.3f}", "v_cell_max_v": f"{vmax:.3f}", "idx_min": str(weak),
            "fault_level": str(level), "k_pos": str(closed), "k_neg": str(closed)}


def write(variant: str, out: Path, seed: int = 1) -> Path:
    files = {}
    for name, rs in rows(variant, seed).items():
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=COLS)
        w.writeheader()
        w.writerows(rs)
        files[name] = buf.getvalue().encode()
    man = io.StringIO()
    mw = csv.writer(man)
    mw.writerow(["file", "sha256"])
    for n, b in sorted(files.items()):
        mw.writerow([n, hashlib.sha256(b).hexdigest()])
    files["manifest.csv"] = man.getvalue().encode()
    out = Path(out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for n, b in sorted(files.items()):
            zf.writestr(n, b)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=["correction", "healthy", "restarts"], required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    print(write(a.variant, Path(a.out), a.seed))
