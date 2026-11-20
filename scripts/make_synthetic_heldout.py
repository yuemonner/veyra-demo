"""SYNTHETIC held-out incidents for the inquiry evaluation: a standby discharge that ends in an under-voltage protective
cutoff, generated from first principles with ONE sealed ground-truth cause (or a combination). Never customer data.

Physics (per cell, 1 s steps): OCV(SOC) from a stated curve, ohmic + one RC branch, per-cell capacity. The BMS:
  * measures current through its sensor (scale error, or loads wired upstream of the sensor are not seen);
  * counts that measured current against the RATED capacity (coulomb counting), from its own starting value;
  * corrects SOC down to 5 % over 40 s when the lowest cell falls below the curve's 8 % voltage (a low-end correction);
  * raises fault level 1/2/3 at the curve's 5/3/1 % voltages and opens both contactors 2 s after level 3;
  * then reports the pack at rest (current 0).
The VCU group reports a DC/DC converter cycling on and off, and power state.

Causes (the truth is sealed by research/inquiry-eval-v1/heldout.py; the agent never sees it):
  soc_drift       true capacity = rated; the displayed SOC started far above the true SOC (estimator drift)
  capacity_fade   true capacity well below rated; the counter was set to 100 % at a full charge and counted correctly since
  current_scale   true capacity = rated; the sensor reads a fraction of the real current; counter set at full charge
  sensor_bypass   true capacity = rated; the DC/DC load is wired upstream of the sensor; counter set at full charge
  drift_weak_cell soc_drift plus one cell with reduced capacity (reaches cutoff first)

Output: a decoded-table package (<window>_<group>.csv + manifest.csv) read by the built-in profile synthetic-bev-heldout,
which (like a real customer profile) declares no series count, no chemistry and no OCV curve.
"""
from __future__ import annotations

import csv
import hashlib
import io
import math
import random
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.physchecks.synthetic_cells import SYN_OCV, ocv  # noqa: E402

CAUSES = ("soc_drift", "capacity_fade", "current_scale", "sensor_bypass", "drift_weak_cell")
BMS_COLS = ["t_rel_s", "can_id", "i_pack_a", "soc_pct", "cap_ah", "v_pack_v", "v_cell_min_v", "v_cell_max_v", "idx_min", "fault_level", "k_pos", "k_neg"]
VCU_COLS = ["t_rel_s", "can_id", "dcdc_state", "power_state"]


def draw(cause: str, seed: int) -> Dict[str, Any]:
    """Scenario parameters for a cause, drawn from a seeded generator (wide ranges, no case-specific values)."""
    rnd = random.Random(seed)
    p: Dict[str, Any] = {"cause": cause, "seed": seed,
                         "n_series": rnd.choice([14, 24, 28, 48, 72, 88, 100, 108]),
                         "curve": rnd.choice(["sloped", "flat"]),
                         "rated_ah": float(rnd.choice([40, 60, 80, 105, 150, 200])),
                         "r0_cell": round(rnd.uniform(0.0006, 0.0025), 5), "r1_cell": round(rnd.uniform(0.0004, 0.0015), 5),
                         "tau_s": round(rnd.uniform(30, 120), 1),
                         "base_a": round(rnd.uniform(2.0, 6.0), 2), "dcdc_a": round(rnd.uniform(6.0, 18.0), 2),
                         "dcdc_on_s": rnd.randint(120, 400), "dcdc_off_s": rnd.randint(60, 300),
                         "aux_steps": [(rnd.randint(200, 3000), round(rnd.uniform(4, 15), 2), rnd.randint(20, 90)) for _ in range(rnd.randint(4, 9))],
                         "true_ah_frac": 1.0, "current_scale": 1.0, "bypass": False, "weak_cell": None,
                         "drift_pp": 0.0}
    if cause in ("soc_drift", "drift_weak_cell"):
        p["drift_pp"] = round(rnd.uniform(25, 45), 1)
        p["true_soc0"] = round(rnd.uniform(5, 14), 1)
    if cause == "drift_weak_cell":
        p["weak_cell"] = (rnd.randrange(p["n_series"]), round(rnd.uniform(0.75, 0.88), 3))
    if cause == "capacity_fade":
        p["true_ah_frac"] = round(rnd.uniform(0.38, 0.6), 3)
    if cause == "current_scale":
        p["current_scale"] = round(rnd.uniform(0.18, 0.4), 3)
    if cause == "sensor_bypass":
        p["bypass"] = True
        p["dcdc_a"] = round(rnd.uniform(14.0, 25.0), 2)
        p["dcdc_on_s"], p["dcdc_off_s"] = rnd.randint(400, 900), rnd.randint(60, 200)
    return p


def simulate(p: Dict[str, Any]) -> Dict[str, List[Dict[str, str]]]:
    rnd = random.Random(p["seed"] * 7919 + 1)
    curve = SYN_OCV[p["curve"]]
    n, rated = p["n_series"], p["rated_ah"]
    true_ah = rated * p["true_ah_frac"]
    cap = [true_ah] * n
    if p["weak_cell"]:
        cap[p["weak_cell"][0]] = true_ah * p["weak_cell"][1]
    # where the record starts
    if p["cause"] in ("soc_drift", "drift_weak_cell"):
        soc_true0 = p["true_soc0"] + rnd.uniform(4, 10)
        soc_bms = soc_true0 + p["drift_pp"]
    else:
        # a full charge, then pre_discharge_frac of the BMS-visible scale counted down before the record starts
        q_pre_true = ((0.55 + 0.25 * rnd.random()) if p["cause"] == "capacity_fade" else (0.75 + 0.2 * rnd.random())) * true_ah
        soc_true0 = 100.0 - q_pre_true / true_ah * 100
        seen = q_pre_true * (p["current_scale"] if p["cause"] == "current_scale" else 1.0)
        if p["cause"] == "sensor_bypass":
            on_frac = p["dcdc_on_s"] / (p["dcdc_on_s"] + p["dcdc_off_s"])
            seen = q_pre_true * p["base_a"] / (p["base_a"] + on_frac * p["dcdc_a"])
        soc_bms = 100.0 - seen / rated * 100
    soc = [soc_true0] * n
    if p["weak_cell"]:
        soc[p["weak_cell"][0]] = soc_true0                     # same start, smaller capacity: it falls faster
    vrc = [0.0] * n
    v_corr, v1, v2, v3 = ocv(curve, 8), ocv(curve, 5), ocv(curve, 3), ocv(curve, 1)
    bms, vcu = [], []
    t, level, closed, open_at, corr_from, corr_to, corr_t0 = 0, 0, 1, None, None, None, None
    period = p["dcdc_on_s"] + p["dcdc_off_s"]
    rest_after = None
    while t < 6 * 3600:
        dcdc = 1 if closed and (t % period) < p["dcdc_on_s"] else 0
        aux = sum(a for start, a, dur in p["aux_steps"] if closed and start <= t < start + dur)
        i_true = (p["base_a"] + dcdc * p["dcdc_a"] + aux) if closed else 0.0
        i_seen = (p["base_a"] + aux + (0 if p["bypass"] else dcdc * p["dcdc_a"])) if closed else 0.0
        i_meas = i_seen * p["current_scale"] + rnd.gauss(0, 0.03)
        for k in range(n):
            soc[k] -= i_true / (cap[k] * 3600) * 100
            vrc[k] += (i_true * p["r1_cell"] - vrc[k]) / p["tau_s"]
        v = [ocv(curve, max(soc[k], -5)) - i_true * p["r0_cell"] - vrc[k] for k in range(n)]
        sensed = [x + rnd.gauss(0, 0.0008) for x in v]
        vmin, vmax = min(sensed), max(sensed)
        imin = sensed.index(vmin) + 1
        soc_bms -= i_meas / (rated * 3600) * 100
        if corr_t0 is None and closed and vmin < v_corr:
            corr_t0, corr_from = t, soc_bms
            corr_to = min(soc_bms, 5.0)
        if corr_t0 is not None and t - corr_t0 <= 40:
            soc_bms = corr_from + (corr_to - corr_from) * (t - corr_t0) / 40
        if closed:
            level = max(level, 1 if vmin < v1 else 0, 2 if vmin < v2 else 0, 3 if vmin < v3 else 0)
            if level == 3 and open_at is None:
                open_at = t + 2
            if open_at is not None and t >= open_at:
                closed, rest_after = 0, t
        bms.append({"t_rel_s": f"{t:.1f}", "can_id": "0x18F1", "i_pack_a": f"{i_meas:.2f}", "soc_pct": f"{max(0.0, soc_bms):.1f}",
                    "cap_ah": f"{rated:g}", "v_pack_v": f"{sum(v) + rnd.gauss(0, 0.05):.2f}", "v_cell_min_v": f"{vmin:.4f}",
                    "v_cell_max_v": f"{vmax:.4f}", "idx_min": str(imin), "fault_level": str(level), "k_pos": str(closed), "k_neg": str(closed)})
        if t % 2 == 0:
            vcu.append({"t_rel_s": f"{t + 0.3:.1f}", "can_id": "0x0C10", "dcdc_state": str(dcdc), "power_state": "4" if closed else "3"})
        if rest_after is not None and t - rest_after >= 300:
            break
        t += 1
    return {"drain_hbms.csv": bms, "drain_hvcu.csv": vcu}


def write(p: Dict[str, Any], out: Path) -> Path:
    files = {}
    for name, rs in simulate(p).items():
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=BMS_COLS if name.endswith("hbms.csv") else VCU_COLS)
        w.writeheader()
        w.writerows(rs)
        files[name] = buf.getvalue().encode()
    man = io.StringIO()
    mw = csv.writer(man)
    mw.writerow(["file", "sha256"])
    for k, b in sorted(files.items()):
        mw.writerow([k, hashlib.sha256(b).hexdigest()])
    files["manifest.csv"] = man.getvalue().encode()
    out = Path(out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for k, b in sorted(files.items()):
            zf.writestr(k, b)
    return out


def summary(p: Dict[str, Any]) -> Dict[str, Any]:
    """Facts about a generated incident a reviewer can check (used only by tests and the sealed key)."""
    rows = simulate(p)["drain_hbms.csv"]
    trip = next((r for r in rows if r["k_pos"] == "0"), None)
    return {"rows": len(rows), "tripped": trip is not None, "trip_t": None if trip is None else float(trip["t_rel_s"]),
            "soc_at_trip": None if trip is None else float(trip["soc_pct"]), "math_ok": not any(math.isnan(float(r["v_pack_v"])) for r in rows)}
