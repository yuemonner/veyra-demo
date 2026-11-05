"""Independent generator of synthetic chassis CAN logs for blind validation of app/physics/electrical.py.

Shares no code with the engine or with scripts/demo/make_pix_shuttle_case.py, and uses physics the engine does not
assume: constant-power wheel loads (DC current from torque, speed and each inverter's and motor's losses), a battery
with ohmic and polarisation resistance depending on temperature and state of charge, auxiliary load, an inverter
DC-link filter, per-ECU sampling phase (values in a frame are up to 60 ms old), per-sensor offset, gain, noise and
quantisation, inverter heating. Every case is reproducible from (scenario, seed).

generate(scenario, seed) -> (log_lines, truth). The truth goes to a separate directory; the engine never sees it.
SYNTHETIC: not from a real vehicle.
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple

WHEELS = ["FL", "FR", "RL", "RR"]
DT = 0.01          # simulation step, s
FRAME = 0.1        # CAN frame period, s
R_WHEEL = 0.3      # m

SCENARIOS = ["healthy_even", "healthy_uneven", "connector", "connector_intermittent", "conductor", "inverter_loss",
             "sensor_offset", "sensor_gain", "cold_battery", "combined",
             "missing_current", "missing_wheel_v", "missing_torque", "missing_temp", "low_load"]
HEALTHY = {"healthy_even", "healthy_uneven", "sensor_offset", "cold_battery"}


def _ocv_cell(soc: float) -> float:
    return 3.35 + 0.72 * soc + 0.08 * math.tanh((soc - 0.12) * 12) - 0.05 * (1 - soc) ** 4


def _r0_factor(t_c: float, soc: float) -> float:
    return math.exp(3300 * (1 / (t_c + 273.15) - 1 / 298.15)) * (1 + 0.8 * max(0.0, 0.3 - soc) / 0.3)


def sample(scenario: str, seed: int) -> Dict[str, Any]:
    """Draw a case's parameters. Everything the engine is scored against is here."""
    rng = random.Random(f"{scenario}:{seed}")
    p: Dict[str, Any] = {"scenario": scenario, "seed": seed, "faults": []}
    p["cells"] = rng.choice([20, 22, 24, 26])
    p["r0_25"] = rng.uniform(0.012, 0.030)          # pack ohmic resistance at 25 °C, mid SOC
    p["r1_ratio"] = rng.uniform(0.4, 0.9)
    p["tau"] = rng.uniform(1.2, 4.0)
    p["aux_a"] = rng.uniform(1.5, 4.5)
    p["mass"] = rng.uniform(1400, 2600)
    p["slope"] = rng.uniform(0.04, 0.10)
    w = {}
    for x in WHEELS:
        w[x] = {"cable": rng.uniform(0.0055, 0.0085), "contact": rng.uniform(0.0005, 0.0015), "contact_load_extra": 0.0,
                "c_cu": rng.uniform(0.40, 0.55) * rng.uniform(0.95, 1.05), "p0": rng.uniform(20, 50), "tq_factor": rng.uniform(0.97, 1.03),
                "v_off": rng.gauss(0, 0.03), "v_gain": 1 + rng.gauss(0, 0.0015), "filt": rng.uniform(0.005, 0.03), "phase": rng.uniform(0, 0.06)}
    p["wheels"] = w
    p["bms_phase"] = rng.uniform(0, 0.03)
    p["i_gain"] = 1 + rng.gauss(0, 0.006)
    p["i_off"] = rng.gauss(0, 0.3)
    base_t = rng.uniform(5, 25)
    base_soc = rng.uniform(0.35, 0.85)
    snaps = []
    for j in range(rng.choice([2, 3])):
        snaps.append({"t_c": base_t + rng.uniform(-2, 2), "soc": min(0.95, max(0.15, base_soc + rng.uniform(-0.05, 0.05))),
                      "tq_hold": rng.uniform(55, 95), "ramp": rng.uniform(0.3, 0.8), "hold": rng.uniform(1.0, 6.0)})
    p["snapshots"] = snaps
    fw = rng.choice(WHEELS)
    if scenario == "healthy_uneven":
        up = rng.uniform(1.10, 1.30)
        for x in WHEELS:
            w[x]["tq_factor"] *= up if x == fw else (4 - up) / 3
        p["uneven_wheel"] = fw
    elif scenario == "connector":
        size = rng.uniform(0.004, 0.025)
        w[fw]["contact"] += size
        p["faults"].append({"type": "contact", "wheel": fw, "size_ohm": size})
    elif scenario == "connector_intermittent":
        size = rng.uniform(0.008, 0.025)
        w[fw]["contact_load_extra"] = size
        p["faults"].append({"type": "contact", "wheel": fw, "size_ohm": size, "load_only": True})
    elif scenario == "conductor":
        size = rng.uniform(0.006, 0.025)
        w[fw]["cable"] += size
        p["faults"].append({"type": "conductor", "wheel": fw, "size_ohm": size})
    elif scenario == "inverter_loss":
        m = rng.uniform(1.5, 2.2)
        w[fw]["c_cu"] *= m
        w[fw]["p0"] *= m
        p["faults"].append({"type": "inverter_draw", "wheel": fw, "loss_mult": m})
    elif scenario == "sensor_offset":
        off = rng.choice([-1, 1]) * rng.uniform(0.4, 1.5)
        w[fw]["v_off"] += off
        p["faults"].append({"type": "sensor_offset", "wheel": fw, "offset_v": off})
    elif scenario == "sensor_gain":
        g = rng.choice([-1, 1]) * rng.uniform(0.02, 0.05)
        w[fw]["v_gain"] += g
        p["faults"].append({"type": "sensor_gain", "wheel": fw, "gain_err": g})
        for s in snaps:  # spread rest voltage so gain is separable from offset in some cases
            s["soc"] = rng.uniform(0.25, 0.9)
    elif scenario == "cold_battery":
        temps = sorted(rng.uniform(-5, 25) for _ in snaps)
        confound = seed % 2 == 0
        for s, t in zip(snaps, temps):
            s["t_c"] = t
        if confound:
            snaps[0]["soc"] = rng.uniform(0.2, 0.4)
            snaps[-1]["soc"] = snaps[0]["soc"] + rng.uniform(0.15, 0.45)
        p["confound_soc"] = confound
    elif scenario == "combined":
        if seed % 2 == 0:
            a, b = rng.sample(WHEELS, 2)
            size = rng.uniform(0.010, 0.025)
            m = rng.uniform(1.6, 2.2)
            w[a]["contact"] += size
            w[b]["c_cu"] *= m
            w[b]["p0"] *= m
            p["faults"] += [{"type": "contact", "wheel": a, "size_ohm": size}, {"type": "inverter_draw", "wheel": b, "loss_mult": m}]
        else:
            a, b = rng.sample(WHEELS, 2)
            size = rng.uniform(0.010, 0.025)
            off = rng.choice([-1, 1]) * rng.uniform(0.6, 1.5)
            w[a]["contact"] += size
            w[b]["v_off"] += off
            p["faults"] += [{"type": "contact", "wheel": a, "size_ohm": size}, {"type": "sensor_offset", "wheel": b, "offset_v": off}]
            for s in snaps:
                s["t_c"] = rng.uniform(-5, 5)
    elif scenario == "low_load":
        for s in snaps:
            s["tq_hold"] = rng.uniform(2, 5)
    if scenario.startswith("missing_"):
        size = rng.uniform(0.010, 0.025)
        w[fw]["contact"] += size
        p["faults"].append({"type": "contact", "wheel": fw, "size_ohm": size})
        p["missing"] = scenario.replace("missing_", "")
    return p


def _simulate_snapshot(p: Dict[str, Any], s: Dict[str, Any], rng: random.Random) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Return per-frame sampled values (with sensor effects) and the truth over the snapshot."""
    W = p["wheels"]
    cells = p["cells"]
    r0 = p["r0_25"] * _r0_factor(s["t_c"], s["soc"])
    r1 = r0 * p["r1_ratio"]
    tau = p["tau"] * (1 + max(0.0, 15 - s["t_c"]) / 30)
    ocv = cells * _ocv_cell(s["soc"])
    pre, post = 3.0, 2.0
    total = pre + s["ramp"] + s["hold"] + s["ramp"] + post
    steps = int(round(total / DT))
    v_rc = 0.0
    speed = 0.0
    i_w = {x: 0.0 for x in WHEELS}
    filt = {x: ocv for x in WHEELS}
    t_inv = {x: s["t_c"] + 3 for x in WHEELS}
    hist: List[Dict[str, Any]] = []
    g = 9.81
    for k in range(steps + 1):
        t = k * DT
        if t < pre:
            cmd = 0.0
        elif t < pre + s["ramp"]:
            cmd = s["tq_hold"] * (t - pre) / s["ramp"]
        elif t < pre + s["ramp"] + s["hold"]:
            cmd = s["tq_hold"]
        elif t < pre + 2 * s["ramp"] + s["hold"]:
            cmd = s["tq_hold"] * (1 - (t - pre - s["ramp"] - s["hold"]) / s["ramp"])
        else:
            cmd = 0.0
        tq = {x: cmd * W[x]["tq_factor"] for x in WHEELS}
        force = sum(tq.values()) / R_WHEEL - p["mass"] * g * p["slope"] - 0.012 * p["mass"] * g
        speed = max(0.0, speed + (force / p["mass"]) * DT) if cmd > 0 else max(0.0, speed - 0.5 * DT)
        omega = speed / R_WHEEL
        pdc, loss = {}, {}
        for x in WHEELS:
            loss[x] = W[x]["c_cu"] * tq[x] ** 2 + W[x]["p0"] * (1 if cmd > 0 else 0.3) + 4.0 * omega
            pdc[x] = tq[x] * omega + loss[x]
        for _ in range(6):
            i_pack = sum(i_w.values()) + p["aux_a"]
            v_pack = ocv - r0 * i_pack - v_rc
            v_w = {}
            for x in WHEELS:
                rb = W[x]["cable"] + W[x]["contact"] + W[x]["contact_load_extra"] * min(1.0, max(0.0, (i_w[x] - 15) / 20))
                v_w[x] = v_pack - rb * i_w[x]
                i_w[x] = pdc[x] / max(20.0, v_w[x])
        i_pack = sum(i_w.values()) + p["aux_a"]
        v_rc += (i_pack * r1 - v_rc) * DT / tau
        v_pack = ocv - r0 * i_pack - v_rc
        rb_now = {}
        for x in WHEELS:
            rb_now[x] = W[x]["cable"] + W[x]["contact"] + W[x]["contact_load_extra"] * min(1.0, max(0.0, (i_w[x] - 15) / 20))
            v_w[x] = v_pack - rb_now[x] * i_w[x]
            filt[x] += (v_w[x] - filt[x]) * DT / W[x]["filt"]
            t_inv[x] += (0.4 * loss[x] / 1800 - (t_inv[x] - s["t_c"] - 3) / 600) * DT
        hist.append({"t": t, "i_pack": i_pack, "v_pack": v_pack, "i_w": dict(i_w), "v_w": dict(v_w), "v_filt": dict(filt), "tq": tq, "omega": omega,
                     "t_inv": dict(t_inv), "rb": rb_now, "cmd": cmd})

    def at(tt: float) -> Dict[str, Any]:
        return hist[max(0, min(len(hist) - 1, int(round(tt / DT))))]

    frames = []
    nframes = int(total / FRAME)
    for f in range(nframes):
        tf = f * FRAME
        b = at(tf - p["bms_phase"])
        row = {"t": tf, "Pack_V": b["v_pack"] + rng.gauss(0, 0.03), "Pack_I": b["i_pack"] * p["i_gain"] + p["i_off"] + rng.gauss(0, 0.4),
               "T_cell_min": s["t_c"], "T_cell_max": s["t_c"] + 2, "SOC": s["soc"] * 100, "cmd": at(tf)["cmd"], "wheels": {}}
        for x in WHEELS:
            m = at(tf - W[x]["phase"])
            row["wheels"][x] = {"DC_V": m["v_filt"][x] * W[x]["v_gain"] + W[x]["v_off"] + rng.gauss(0, 0.035),
                                "Tq_Act": m["tq"][x] + rng.gauss(0, 0.3), "Spd_rpm": m["omega"] * 60 / (2 * math.pi), "T_inv": m["t_inv"][x]}
        frames.append(row)
    # truth on the frame grid, noise-free and synchronous
    grid = [at(f * FRAME) for f in range(nframes)]
    loaded = [h for h in grid if h["i_pack"] >= 20]
    truth: Dict[str, Any] = {"t_c": s["t_c"], "soc": s["soc"], "r0_ohm": r0, "r1_ohm": r1, "tau_s": tau, "ocv": ocv,
                             "pulse_s": s["ramp"] * 2 + s["hold"], "peak_a": max(h["i_pack"] for h in grid)}
    if len(loaded) >= 3:
        def slope(xs, ys):
            mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
            sxx = sum((a - mx) ** 2 for a in xs)
            return sum((a - mx) * (c - my) for a, c in zip(xs, ys)) / sxx if sxx else None
        truth["k"] = {x: slope([h["i_pack"] for h in grid], [h["v_pack"] - h["v_w"][x] for h in grid]) for x in WHEELS}
        truth["share"] = {x: slope([h["i_pack"] for h in grid], [h["i_w"][x] for h in grid]) for x in WHEELS}
        truth["r_loaded"] = {x: sum(h["rb"][x] for h in loaded) / len(loaded) for x in WHEELS}
    return frames, truth


def generate(scenario: str, seed: int) -> Tuple[List[str], Dict[str, Any]]:
    p = sample(scenario, seed)
    rng = random.Random(f"noise:{scenario}:{seed}")
    lines = ["# CAN log export, chassis bus, decoded. SYNTHETIC VALIDATION DATA (generator electrical_validation/generate.py), not from a real vehicle.",
             f"# Vehicle VAL-{seed:04d}. Four wheel motors."]
    start = datetime(2026, 9, 1, 7, 0, 0) + timedelta(days=seed % 40)
    truth_snaps = []
    missing = p.get("missing")
    for j, s in enumerate(p["snapshots"]):
        frames, tr = _simulate_snapshot(p, s, rng)
        truth_snaps.append(tr)
        t0 = start + timedelta(hours=3 * j)
        lines.append(f"# ---- snapshot {j + 1} of {len(p['snapshots'])}: launch {j + 1}")
        for fr in frames:
            ts = (t0 + timedelta(seconds=fr["t"])).isoformat(timespec="milliseconds")
            parts = [f"Pack_V={fr['Pack_V']:.2f}"]
            if missing != "current":
                parts.append(f"Pack_I={fr['Pack_I']:.1f}")
            parts.append(f"SOC={fr['SOC']:.0f}")
            if missing != "temp":
                parts += [f"T_cell_min={fr['T_cell_min']:.0f}", f"T_cell_max={fr['T_cell_max']:.0f}"]
            lines.append(f"{ts} BMS_Status    " + " ".join(parts))
            lines.append(f"{ts} VCU_TqDebug   TqCmd_Out={fr['cmd']:.1f} TqLim_BMS=120.0")
            for x in WHEELS:
                m = fr["wheels"][x]
                parts = []
                if not (missing == "wheel_v" and x != "FL"):
                    parts.append(f"DC_V={m['DC_V']:.2f}")
                if missing != "torque":
                    parts.append(f"Tq_Act={m['Tq_Act']:.1f}")
                parts += [f"Spd_rpm={m['Spd_rpm']:.0f}", f"T_inv={m['T_inv']:.0f}"]
                lines.append(f"{ts} MCU_{x}        " + " ".join(parts))
    W = p["wheels"]
    truth = {"scenario": scenario, "seed": seed, "faults": p["faults"], "missing": missing,
             "confound_soc": p.get("confound_soc"), "uneven_wheel": p.get("uneven_wheel"),
             "wheels": {x: {"cable_ohm": W[x]["cable"], "contact_static_ohm": W[x]["contact"], "contact_load_extra_ohm": W[x]["contact_load_extra"],
                            "loss_coeff": W[x]["c_cu"], "v_off": W[x]["v_off"], "v_gain": W[x]["v_gain"], "tq_factor": W[x]["tq_factor"]} for x in WHEELS},
             "snapshots": truth_snaps}
    return lines, truth
