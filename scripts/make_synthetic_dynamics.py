"""SYNTHETIC incidents and controls for the signal-dynamics evaluation, in TWO machine categories, generated from first
principles with ONE sealed ground truth each. Never customer data; nothing here is tuned to CASE A.

BATTERY (a series string; the BMS reports pack current, pack voltage, a coulomb-counted SOC, the lowest and highest cell
voltage with their indices, a fault level and the main contactor). Every cell: V = OCV(SOC) - I R0 - V_rc (one RC pair),
with natural cell-to-cell mismatch in capacity, initial SOC and resistance; per-channel sensing noise; 1 mV and 0.1 A
quantisation; values held between updates. The BMS raises fault level 1/2/3 when the lowest reading stays below the
curve's 8/3/1 % voltage for its persistence time and opens the contactor after level 3.
ROBOT (n actuators driven by one command; the drive controller reports each actuator's current, the command, a fault
state and a protective stop). Actuator k: I_k = f_k + b_k * command + noise, natural mismatch in f_k and b_k; the
controller faults when any current stays above its limit for its persistence time, then stops.

Classes (truth sealed by research/dynamics-eval-v1; the agent never sees it):
  fault, component (one member):   drift       battery: one cell with lower capacity / SOC     robot: one actuator's friction rises
                                   load        battery: one cell's resistance grows           robot: one actuator's torque constant falls
                                   sensing     battery: one cell channel glitches             robot: one current channel glitches
  event without a component fault: common      battery: the whole pack runs empty             robot: payload / terrain loads every actuator
                                   external    battery: an outside fault opens the contactor  robot: an operator / supervisor stop
  controls without an event:       healthy_constant, healthy_varying, noise (5-15x sensing noise),
                                   timing (members reported in separate messages with skew and jitter, while the load steps)
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import random
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.physchecks.synthetic_cells import SYN_OCV, ocv  # noqa: E402

FAULT = ("drift", "load", "sensing")
EVENT_BENIGN = ("common", "external")
CONTROLS = ("healthy_constant", "healthy_varying", "noise", "timing")
CLASSES = FAULT + EVENT_BENIGN + CONTROLS
DOMAINS = ("battery", "robot")
BAT_COLS = ["t_rel_s", "can_id", "i_pack_a", "v_pack_v", "soc_pct", "v_cell_min_v", "v_cell_max_v", "idx_min", "idx_max", "fault_level", "k_pos"]
ROB_N = 6
ROB_COLS = ["t_rel_s", "can_id", "cmd"] + [f"i{k}" for k in range(1, ROB_N + 1)] + ["fault", "stop"]


def draw(domain: str, cls: str, seed: int) -> Dict[str, Any]:
    rnd = random.Random(seed)
    if domain == "battery":
        n = rnd.choice([12, 16, 24, 32, 48, 96])
        cap = float(rnd.choice([20, 40, 60, 105, 150]))
        p = {"domain": domain, "cls": cls, "seed": seed, "n": n, "cap_ah": cap, "curve": rnd.choice(["sloped", "flat"]),
             "sig_cap": round(rnd.uniform(0.004, 0.012), 4), "sig_soc": round(rnd.uniform(0.2, 0.8), 3), "sig_r": round(rnd.uniform(0.02, 0.06), 3),
             "r0": round(rnd.uniform(0.04, 0.12) / cap, 6), "r1": round(rnd.uniform(0.02, 0.06) / cap, 6), "tau": round(rnd.uniform(20, 90), 1),
             "base_c": round(rnd.uniform(0.15, 0.4), 3), "step_c": round(rnd.uniform(0.1, 0.4), 3), "step_period": rnd.randint(40, 160),
             "dt": rnd.choice([0.2, 0.5, 1.0]), "update_every": rnd.choice([1, 2, 3]), "sense_sd": round(rnd.uniform(0.0003, 0.0010), 5),
             "persist_s": rnd.choice([2.0, 3.0, 5.0]), "weak": rnd.randrange(n), "skew_s": 0.0}
        p["steps"] = cls not in ("healthy_constant",)
        if cls in ("drift", "load", "sensing", "common"):
            p["soc0"] = round(rnd.uniform(25, 40), 1)
        elif cls == "external":
            p["soc0"] = round(rnd.uniform(60, 85), 1)
        else:
            p["soc0"] = round(rnd.uniform(50, 85), 1)
            p["duration_s"] = rnd.randint(1800, 3600)
        if cls == "drift":
            p["weak_cap"] = round(rnd.uniform(0.55, 0.8), 3)
            p["weak_soc"] = round(rnd.uniform(-6, 0), 2)
        if cls == "load":
            p["weak_r0"] = round(rnd.uniform(1.5, 3.0), 2)
            p["weak_r_growth"] = round(rnd.uniform(1.0, 3.0), 2)      # extra multiple of r0 gained per 1000 s
        if cls == "sensing":
            p["glitch_v"] = round(rnd.uniform(0.15, 0.6), 3)
            p["glitch_rate0"], p["glitch_rate1"] = 0.0005, round(rnd.uniform(0.005, 0.03), 4)
        if cls == "external":
            p["ext_at"] = rnd.randint(1500, 2600)
        if cls == "noise":
            p["sense_sd"] = round(p["sense_sd"] * rnd.uniform(5, 12), 5)
        if cls == "timing":
            p["skew_s"] = round(rnd.uniform(0.5, 1.5), 2)
            p["step_c"] = round(rnd.uniform(0.3, 0.5), 3)
        return p
    p = {"domain": domain, "cls": cls, "seed": seed, "n": rnd.choice([4, 6]), "dt": rnd.choice([0.05, 0.1, 0.2]),
         "f": round(rnd.uniform(0.5, 2.0), 3), "b": round(rnd.uniform(4.0, 10.0), 3), "sig_f": round(rnd.uniform(0.03, 0.08), 3),
         "sig_b": round(rnd.uniform(0.02, 0.05), 3), "noise": round(rnd.uniform(0.01, 0.03), 4), "q": rnd.choice([0.01, 0.05, 0.1]),
         "cmd_lo": round(rnd.uniform(0.1, 0.3), 3), "cmd_hi": round(rnd.uniform(0.6, 0.9), 3), "hold_s": (rnd.randint(4, 10), rnd.randint(12, 40)),
         "limit_x": round(rnd.uniform(1.4, 1.7), 3), "persist_s": rnd.choice([0.3, 0.5, 1.0]), "weak": 0, "skew_s": 0.0,
         "duration_s": rnd.randint(1500, 3000), "steps": cls != "healthy_constant"}
    p["weak"] = rnd.randrange(p["n"])
    if cls == "drift":
        p["wear_per_1000s"] = round(rnd.uniform(0.25, 0.6), 3)     # friction current gained, as a fraction of f + b
    if cls == "load":
        p["kt_loss_per_1000s"] = round(rnd.uniform(0.4, 0.8), 3)  # extra fraction of b per 1000 s
    if cls == "sensing":
        p["glitch_x"] = round(rnd.uniform(0.6, 1.2), 3)
        p["glitch_rate0"], p["glitch_rate1"] = 0.0005, round(rnd.uniform(0.005, 0.03), 4)
    if cls == "common":
        p["payload_per_1000s"] = round(rnd.uniform(0.35, 0.6), 3)
    if cls == "external":
        p["ext_at"] = rnd.randint(900, 1400)
    if cls == "noise":
        p["noise"] = round(p["noise"] * rnd.uniform(4, 8), 4)
    if cls == "timing":
        p["skew_s"] = round(rnd.uniform(0.2, 0.6), 2)
        p["hold_s"] = (2, 6)
    return p


# ---- battery ---------------------------------------------------------------------------------------------------------
def _battery(p: Dict[str, Any]) -> Dict[str, List[Dict[str, str]]]:
    rnd = random.Random(p["seed"] * 7919 + 3)
    curve = SYN_OCV[p["curve"]]
    n, C = p["n"], p["cap_ah"]
    cap = [C * (1 + rnd.gauss(0, p["sig_cap"])) for _ in range(n)]
    soc = [p["soc0"] + rnd.gauss(0, p["sig_soc"]) for _ in range(n)]
    r0 = [p["r0"] * (1 + rnd.gauss(0, p["sig_r"])) for _ in range(n)]
    w = p["weak"]
    if p["cls"] == "drift":
        cap[w] *= p["weak_cap"]
        soc[w] += p["weak_soc"]
    if p["cls"] == "load":
        r0[w] *= p["weak_r0"]
    vrc = [0.0] * n
    th = {1: ocv(curve, 8), 2: ocv(curve, 3), 3: ocv(curve, 1)}
    below = {1: 0.0, 2: 0.0, 3: 0.0}
    level, closed, open_at, t = 0, 1, None, 0.0
    soc_bms = sum(soc) / n
    dt = p["dt"]
    rows: List[Dict[str, str]] = []
    held: Dict[str, str] = {}
    k = 0
    glitch_until, glitch_v = -1.0, 0.0
    rest_after = None
    max_t = p.get("duration_s") or 6 * 3600
    while t < max_t:
        stepped = p["steps"] and (int(t // p["step_period"]) % 2 == 1)
        i_true = (p["base_c"] + (p["step_c"] if stepped else 0.0)) * C if closed else 0.0
        if p["cls"] == "load":
            r0[w] = p["r0"] * (p["weak_r0"] + p["weak_r_growth"] * t / 1000.0)
        for j in range(n):
            soc[j] -= i_true * dt / (cap[j] * 3600) * 100
            vrc[j] += (i_true * p["r1"] - vrc[j]) * min(1.0, dt / p["tau"])
        v = [ocv(curve, max(soc[j], -5)) - i_true * r0[j] - vrc[j] for j in range(n)]
        sensed = [x + rnd.gauss(0, p["sense_sd"]) for x in v]
        if p["cls"] == "sensing" and closed:
            rate = p["glitch_rate0"] + (p["glitch_rate1"] - p["glitch_rate0"]) * min(1.0, t / 3000.0)
            if t > glitch_until and rnd.random() < rate * dt:
                glitch_until, glitch_v = t + rnd.uniform(0.5, 2.0) + (p["persist_s"] if rnd.random() < min(1.0, t / 4000.0) else 0.0), p["glitch_v"] * rnd.uniform(0.5, 1.0)
            if t <= glitch_until:
                sensed[w] -= glitch_v
        sensed = [round(x, 3) for x in sensed]
        vmin, vmax = min(sensed), max(sensed)
        imin, imax = sensed.index(vmin) + 1, sensed.index(vmax) + 1
        soc_bms -= (i_true + rnd.gauss(0, 0.05)) * dt / (C * 3600) * 100
        if closed:
            for L in (1, 2, 3):
                below[L] = below[L] + dt if vmin < th[L] else 0.0
                if below[L] >= p["persist_s"]:
                    level = max(level, L)
            if p["cls"] == "external" and t >= p["ext_at"]:
                level = 3
            if level == 3 and open_at is None:
                open_at = t + 1.0
            if open_at is not None and t >= open_at:
                closed, rest_after = 0, t
        upd = (k % p["update_every"]) == 0
        if upd or not held:
            held = {"v_cell_min_v": f"{vmin:.3f}", "v_cell_max_v": f"{vmax:.3f}", "idx_min": str(imin), "idx_max": str(imax)}
        base = {"i_pack_a": f"{round(i_true + rnd.gauss(0, 0.05), 1):.1f}", "v_pack_v": f"{sum(v):.1f}", "soc_pct": f"{max(0.0, soc_bms):.1f}",
                "fault_level": str(level), "k_pos": str(closed)}
        if p["skew_s"] > 0:
            rows.append({"t_rel_s": f"{t:.3f}", "can_id": "0x101", **base, "v_cell_min_v": held["v_cell_min_v"], "idx_min": held["idx_min"]})
            tj = t + p["skew_s"] + rnd.uniform(-0.2, 0.2) * p["skew_s"]
            rows.append({"t_rel_s": f"{tj:.3f}", "can_id": "0x102", "v_cell_max_v": held["v_cell_max_v"], "idx_max": held["idx_max"]})
        else:
            rows.append({"t_rel_s": f"{t:.3f}", "can_id": "0x101", **base, **held})
        if rest_after is not None and t - rest_after >= 120:
            break
        t = round(t + dt, 6)
        k += 1
    rows.sort(key=lambda r: float(r["t_rel_s"]))
    return {"run_hbat.csv": rows}


# ---- robot -----------------------------------------------------------------------------------------------------------
def _robot(p: Dict[str, Any]) -> Dict[str, List[Dict[str, str]]]:
    rnd = random.Random(p["seed"] * 104729 + 5)
    n, dt = p["n"], p["dt"]
    f = [p["f"] * (1 + rnd.gauss(0, p["sig_f"])) for _ in range(n)]
    b = [p["b"] * (1 + rnd.gauss(0, p["sig_b"])) for _ in range(n)]
    limit = (p["f"] + p["b"] * p["cmd_hi"]) * p["limit_x"]
    w = p["weak"]
    cmd, next_change, t, k = p["cmd_lo"], 0.0, 0.0, 0
    fault, stop, over = 0, 0, 0.0
    rows: List[Dict[str, str]] = []
    stop_at = None
    glitch_until, glitch_a = -1.0, 0.0
    lat: List[float] = [0.0] * n
    while t < p["duration_s"]:
        if p["steps"] and t >= next_change and not stop:
            cmd = round(rnd.uniform(p["cmd_lo"], p["cmd_hi"]), 3)
            next_change = t + rnd.uniform(*p["hold_s"])
        if not p["steps"]:
            cmd = (p["cmd_lo"] + p["cmd_hi"]) / 2
        c = 0.0 if stop else cmd
        mult = 1.0 + (p.get("payload_per_1000s", 0.0) * t / 1000.0 if p["cls"] == "common" else 0.0)
        cur = []
        for j in range(n):
            fj, bj = f[j] * mult, b[j] * mult
            if p["cls"] == "drift" and j == w:
                fj += p["wear_per_1000s"] * (p["f"] + p["b"]) * (t / 1000.0) ** 1.5
            if p["cls"] == "load" and j == w:
                bj *= 1 + p["kt_loss_per_1000s"] * t / 1000.0
            i = 0.0 if stop else fj + bj * c + rnd.gauss(0, p["noise"] * p["b"])
            cur.append(i)
        if p["cls"] == "sensing" and not stop:
            rate = p["glitch_rate0"] + (p["glitch_rate1"] - p["glitch_rate0"]) * min(1.0, t / 2000.0)
            if t > glitch_until and rnd.random() < rate * dt:
                glitch_until = t + rnd.uniform(0.1, 0.4) + (p["persist_s"] if rnd.random() < min(1.0, t / 2500.0) else 0.0)
                glitch_a = p["glitch_x"] * limit * rnd.uniform(0.6, 1.0)
            if t <= glitch_until:
                cur[w] += glitch_a
        q = p["q"]
        cur = [round(x / q) * q for x in cur]
        if not stop:
            over = over + dt if max(cur) > limit else 0.0
            if over >= p["persist_s"]:
                fault, stop_at = 2, t
            if p["cls"] == "external" and t >= p["ext_at"]:
                stop_at = t
            if stop_at is not None:
                stop = 1
        base = {"cmd": f"{c:.3f}", "fault": str(fault), "stop": str(stop)}
        if p["skew_s"] > 0:
            rows.append({"t_rel_s": f"{t:.3f}", "can_id": "0x201", **base, **{f"i{j + 1}": f"{cur[j]:.3f}" for j in range(0, n, 2)}})
            tj = t + p["skew_s"] + rnd.uniform(-0.2, 0.2) * p["skew_s"]
            rows.append({"t_rel_s": f"{tj:.3f}", "can_id": "0x202", **{f"i{j + 1}": f"{cur[j]:.3f}" for j in range(1, n, 2)}})
        else:
            rows.append({"t_rel_s": f"{t:.3f}", "can_id": "0x201", **base, **{f"i{j + 1}": f"{cur[j]:.3f}" for j in range(n)}})
        if stop_at is not None and t - stop_at >= 30:
            break
        t = round(t + dt, 6)
        k += 1
    rows.sort(key=lambda r: float(r["t_rel_s"]))
    return {"run_hdrv.csv": rows}


def simulate(p: Dict[str, Any]) -> Dict[str, List[Dict[str, str]]]:
    return _battery(p) if p["domain"] == "battery" else _robot(p)


def write(p: Dict[str, Any], out: Path) -> Path:
    files = {}
    for name, rs in simulate(p).items():
        buf = io.StringIO()
        wr = csv.DictWriter(buf, fieldnames=BAT_COLS if name.endswith("hbat.csv") else ROB_COLS)
        wr.writeheader()
        wr.writerows(rs)
        files[name] = buf.getvalue().encode()
    man = io.StringIO()
    mw = csv.writer(man)
    mw.writerow(["file", "sha256"])
    for k, bts in sorted(files.items()):
        mw.writerow([k, hashlib.sha256(bts).hexdigest()])
    files["manifest.csv"] = man.getvalue().encode()
    out = Path(out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for k, bts in sorted(files.items()):
            zf.writestr(k, bts)
    return out


PROFILE_DIR = Path(__file__).resolve().parents[1] / "backend" / "app" / "physchecks" / "profiles"


def profile(domain: str) -> Dict[str, Any]:
    return json.loads((PROFILE_DIR / f"synthetic-dyn-{domain}.json").read_text(encoding="utf-8"))


def to_recording(p: Dict[str, Any], rid: str = "syn"):
    """The generated rows as a Recording + Profile, without a file (for the deterministic engine evaluation)."""
    from app.physchecks.profile import Profile
    from app.physchecks.recording import Recording
    rec = Recording(rid, synthetic=True, label=f"SYNTHETIC {p['domain']}")
    grp = "bat" if p["domain"] == "battery" else "drv"
    for name, rows in simulate(p).items():
        for line, r in enumerate(rows, start=2):
            t = float(r["t_rel_s"])
            for c, v in r.items():
                if c in ("t_rel_s", "can_id") or v in ("", None):
                    continue
                rec.add("run", f"{grp}:{c}", t, float(v), row=line, source=r["can_id"], file=name)
    return rec.finalize(), Profile(profile(p["domain"]))


def truth(p: Dict[str, Any]) -> Dict[str, Any]:
    cls = p["cls"]
    return {"domain": p["domain"], "cls": cls, "component_fault": cls in FAULT, "event_expected": cls in FAULT + EVENT_BENIGN,
            "mechanism": cls if cls in FAULT else None, "weak_member": p.get("weak")}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("domain", choices=DOMAINS)
    ap.add_argument("cls", choices=CLASSES)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    print(write(draw(a.domain, a.cls, a.seed), Path(a.out)))
