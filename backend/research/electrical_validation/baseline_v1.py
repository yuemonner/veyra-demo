"""Electrical relations over a decoded CAN log: physics residuals, parameter estimates, and what the data can tell apart.

A fault that never trips an alarm can still break a physical relation. This module checks three kinds of relation that a
battery-electric chassis obeys, estimates the hidden parameters in them from the recorded data, and then says which
physical mechanisms the recorded signals can and cannot separate.

1. Branch drop (Ohm's law per high-voltage branch): Pack_V - DC_V(wheel) = R_branch * I_branch + offset. I_branch is
   not recorded per wheel, so it is taken as Pack_I / number of wheels; that assumption is stated in every result and
   is itself one of the mechanisms the data cannot exclude (a wheel drawing more than its share).
2. Pack drop: Pack_V = V_rest - R_pack * Pack_I during a launch, with the cell temperature and state of charge it was
   measured at. A cold pack's resistance is several times a warm one's.
3. Command limit: TqCmd_Out <= TqLim_BMS. A command above the limit is a control-logic residual.

Estimates are ordinary least squares with standard errors (the parameter-estimation route used in the battery
literature, e.g. resistance from real-world driving data with temperature recorded alongside). Isolability follows
structural fault-diagnosis analysis (fault signature matrix: a fault is detectable if it affects some residual and two
faults are isolable if their signatures differ; Frisk, Krysander et al., Fault Diagnosis Toolbox, MIT), implemented here
for this small model, with candidate measurements that would separate the pairs that are not isolable.

Deterministic; no model call. Everything it reports is computed from the lines of the file, with line numbers.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Tuple

VERSION = "electrical-relations-v1"
ROW = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s+(?:(INFO|WARN|ERROR|DEBUG)\s+)?(\w+)\s+(.*)$")
KV = re.compile(r"([A-Za-z][\w]*)=([^\s()]+)")
LOADED_A = 20.0      # pack current above which a sample counts as loaded
REST_A = 6.0         # pack current below which the pack is at rest
MIN_POINTS = 6
ANOMALY_FLOOR_OHM = 0.003   # a branch must stand out by at least 3 mΩ, and by 4 standard errors


def _ts(s: str) -> float:
    from datetime import datetime
    return datetime.fromisoformat(s).timestamp()


def parse(lines: List[str]) -> List[Dict[str, Any]]:
    """Snapshots of a decoded CAN log ('# ----' starts one). Each: label, first line, rows of (t, msg, values, line)."""
    snaps: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    for i, raw in enumerate(lines, 1):
        if raw.startswith("# ----"):
            cur = {"label": raw.lstrip("#- ").strip(), "first_line": i, "rows": []}
            snaps.append(cur)
            continue
        if raw.startswith("#"):
            continue
        m = ROW.match(raw)
        if not m:
            continue
        if cur is None:
            cur = {"label": "Log", "first_line": i, "rows": []}
            snaps.append(cur)
        vals: Dict[str, Any] = {}
        for k, v in KV.findall(m.group(4)):
            try:
                vals[k] = float(v)
            except ValueError:
                vals[k] = v
        cur["rows"].append((_ts(m.group(1)), m.group(3), vals, i))
    return [s for s in snaps if s["rows"]]


def _series(snap: Dict[str, Any], msg: str, key: str) -> List[Tuple[float, float, int]]:
    return [(t, v[key], n) for t, m, v, n in snap["rows"] if m == msg and isinstance(v.get(key), float)]


def _at(series: List[Tuple[float, float, int]], t: float, tol: float = 0.026) -> Optional[Tuple[float, int]]:
    best = min(series, key=lambda p: abs(p[0] - t), default=None)
    return (best[1], best[2]) if best and abs(best[0] - t) <= tol else None


def _fit(xs: List[float], ys: List[float]) -> Optional[Dict[str, float]]:
    """y = a + b x by least squares, with standard errors."""
    n = len(xs)
    if n < MIN_POINTS:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = my - b * mx
    res = [y - (a + b * x) for x, y in zip(xs, ys)]
    s2 = sum(r * r for r in res) / max(1, n - 2)
    return {"slope": b, "intercept": a, "slope_se": math.sqrt(s2 / sxx), "intercept_se": math.sqrt(s2 * (1 / n + mx * mx / sxx)), "n": n,
            "resid_sd": math.sqrt(s2)}


def detect(lines: List[str]) -> Optional[Dict[str, Any]]:
    """The signals this module needs, found by name: pack voltage and current, per-wheel DC voltage, cell temperature,
    state of charge, and the command/limit pair. None when the log has no pack and branch voltages."""
    snaps = parse(lines)
    if not snaps:
        return None
    keys = {(m, k) for s in snaps for _, m, v, _ in s["rows"] for k in v}
    pack = next(((m, "Pack_V") for m, k in keys if k == "Pack_V"), None)
    cur = next(((m, "Pack_I") for m, k in keys if k == "Pack_I"), None)
    wheels = sorted({m for m, k in keys if k == "DC_V" and m.startswith("MCU_")})
    if not (pack and cur and len(wheels) >= 2):
        return None
    temp = next(((m, k) for m, k in keys if k in ("T_cell_min", "T_cell")), None)
    soc = next(((m, k) for m, k in keys if k == "SOC"), None)
    cmd = next(((m, k) for m, k in keys if k == "TqCmd_Out"), None)
    lim = next(((m, k) for m, k in keys if k == "TqLim_BMS"), None)
    return {"pack_v": pack, "pack_i": cur, "wheels": wheels, "temp": temp, "soc": soc, "cmd": cmd, "lim": lim}


def _r(x: Optional[float], d: int = 4) -> Optional[float]:
    return None if x is None else round(x, d)


def analyse_snapshot(snap: Dict[str, Any], sig: Dict[str, Any]) -> Dict[str, Any]:
    pv, pi = _series(snap, *sig["pack_v"]), _series(snap, *sig["pack_i"])
    nw = len(sig["wheels"])
    out: Dict[str, Any] = {"label": snap["label"], "first_line": snap["first_line"], "branches": {}, "pack": None, "limit": None}
    t0 = pi[0][0] if pi else None
    if sig["temp"]:
        ts = _series(snap, *sig["temp"])
        out["cell_temp_c"] = ts[0][1] if ts else None
    if sig["soc"]:
        ss = _series(snap, *sig["soc"])
        out["soc"] = ss[0][1] if ss else None
    # branch drop: Pack_V - DC_V = R * Pack_I / n + offset
    for w in sig["wheels"]:
        dv = _series(snap, w, "DC_V")
        xs, ys, rest, lines = [], [], [], []
        for t, i, n in pi:
            p, d = _at(pv, t), _at(dv, t)
            if not p or not d:
                continue
            drop = p[0] - d[0]
            if i >= LOADED_A:
                xs.append(i / nw); ys.append(drop); lines.append(d[1])
            elif i <= REST_A:
                rest.append(drop)
        f = _fit(xs, ys)
        out["branches"][w.replace("MCU_", "")] = {
            "r_ohm": _r(f["slope"]) if f else None, "r_se_ohm": _r(f["slope_se"], 5) if f else None, "n_loaded": len(xs),
            "offset_rest_v": _r(sum(rest) / len(rest), 3) if rest else None, "n_rest": len(rest),
            "max_drop_v": _r(max(ys), 3) if ys else None, "lines": lines[:3] + lines[-2:] if lines else []}
    # pack drop during the launch: V = V_rest - R * I
    pts = [(i, p[0]) for t, i, n in pi for p in [_at(pv, t)] if p and i >= LOADED_A]
    rest_v = [p[0] for t, i, n in pi for p in [_at(pv, t)] if p and i <= REST_A and (t0 is None or t - t0 < 5)]
    if len(pts) >= MIN_POINTS and rest_v:
        v0 = sum(rest_v) / len(rest_v)
        f = _fit([i for i, _ in pts], [v0 - v for _, v in pts])
        if f:
            out["pack"] = {"r_ohm": _r(f["slope"]), "r_se_ohm": _r(f["slope_se"], 5), "v_rest": _r(v0, 2), "n": f["n"],
                           "peak_a": _r(max(i for i, _ in pts), 1), "min_v": _r(min(v for _, v in pts), 2)}
    if sig["cmd"] and sig["lim"]:
        cs, ls = _series(snap, *sig["cmd"]), _series(snap, *sig["lim"])
        over = [(c[1] - l[0], c[2], c[0]) for c in cs for l in [_at(ls, c[0])] if l]
        worst = max(over, default=None)
        first = next((o for o in over if o[0] > 0.5), None)
        out["limit"] = {"max_over": _r(worst[0], 1) if worst else None, "line": worst[1] if worst else None,
                        "first_over_s": _r(first[2] - t0, 2) if first and t0 is not None else None, "first_line": first[1] if first else None}
    return out


# Mechanisms and the relations each one breaks (its fault signature). Relations: B = branch drop slope of that wheel,
# O = that wheel's offset at rest, S = that wheel's torque share, P = pack drop, L = command above limit.
def mechanisms(wheel: str) -> List[Dict[str, Any]]:
    return [
        {"id": "terminal", "name": f"{wheel} connector or terminal contact resistance", "signature": {f"B_{wheel}"}, "where": "between the pack and the inverter input, at a connector"},
        {"id": "cable", "name": f"{wheel} cable, busbar or fuse resistance", "signature": {f"B_{wheel}"}, "where": "between the pack and the inverter input, along the conductor"},
        {"id": "share", "name": f"{wheel} drawing more than its share of current", "signature": {f"B_{wheel}"}, "where": "inverter or motor losses; equal sharing is assumed"},
        {"id": "offset", "name": f"{wheel} DC voltage sensor offset", "signature": {f"O_{wheel}"}, "where": "the inverter's voltage measurement"},
        {"id": "gain", "name": f"{wheel} DC voltage sensor gain error", "signature": {f"O_{wheel}", f"B_{wheel}"}, "where": "the inverter's voltage measurement"},
        {"id": "pack", "name": "Pack internal resistance (cold or aged cells)", "signature": {"P"}, "where": "the battery pack; affects every branch alike"},
        {"id": "limit", "name": "Torque command not held to the BMS limit", "signature": {"L"}, "where": "VCU torque arbitration"},
    ]


# Extra measurements and the relation each adds; a measurement splits two mechanisms when exactly one of them breaks it.
MEASUREMENTS = [
    {"id": "terminal_dv", "what": "Voltage across the {w} connector (both sides of the contact) under load", "breaks": {"terminal"}},
    {"id": "branch_i", "what": "DC current into the {w} inverter during the launch (clamp meter or the inverter's own current signal)", "breaks": {"share"}},
    {"id": "meter_v", "what": "A reference voltmeter at the {w} inverter input, at rest and under load", "breaks": {"offset", "gain"}},
    {"id": "cable_r", "what": "Four-wire resistance of the {w} cable and fuse with the pack disconnected", "breaks": {"cable"}},
]


def isolability(present: set, wheel: str) -> Dict[str, Any]:
    """Which mechanisms fit the relations that broke, which of those the recorded signals cannot tell apart, and which
    extra measurement would separate each pair."""
    mech = mechanisms(wheel)
    fits = [m for m in mech if m["signature"] and m["signature"] <= present and (m["signature"] & present)]
    groups: Dict[frozenset, List[Dict[str, Any]]] = {}
    for m in fits:
        groups.setdefault(frozenset(m["signature"]), []).append(m)
    not_separable = []
    for sig_, ms in groups.items():
        if len(ms) < 2:
            continue
        splits = []
        for meas in MEASUREMENTS:
            hit = {m["id"] for m in ms} & meas["breaks"]
            if hit and hit != {m["id"] for m in ms}:
                splits.append({"id": meas["id"], "measure": meas["what"].format(w=wheel), "separates": sorted(hit)})
        not_separable.append({"relations": sorted(sig_), "mechanisms": [m["name"] for m in ms], "ids": [m["id"] for m in ms], "measurements": splits})
    detectable = [m["name"] for m in mech if m["signature"]]
    return {"fits": [{"id": m["id"], "name": m["name"], "where": m["where"]} for m in fits], "not_separable": not_separable,
            "detectable": detectable, "method": "fault signature matrix (structural isolability), with candidate measurements"}


def analyse(lines: List[str]) -> Optional[Dict[str, Any]]:
    sig = detect(lines)
    if not sig:
        return None
    snaps = [analyse_snapshot(s, sig) for s in parse(lines)]
    findings, present = [], set()
    # a branch that stands out from the others, in every snapshot where it can be estimated
    wheels = [w.replace("MCU_", "") for w in sig["wheels"]]
    per_w: Dict[str, List[Tuple[float, float]]] = {w: [] for w in wheels}
    for s in snaps:
        for w, b in s["branches"].items():
            if b["r_ohm"] is not None:
                per_w[w].append((b["r_ohm"], b["r_se_ohm"] or 0.0))
    med = sorted(sum(r for r, _ in v) / len(v) for v in per_w.values() if v)
    baseline = med[len(med) // 2 - (1 if len(med) % 2 == 0 else 0)] if med else None
    odd_wheel = None
    for w, v in per_w.items():
        if not v or baseline is None:
            continue
        r = sum(x for x, _ in v) / len(v)
        se = max(se for _, se in v)
        if r - baseline > max(ANOMALY_FLOOR_OHM, 4 * se):
            present.add(f"B_{w}")
            odd_wheel = odd_wheel or w
            findings.append({"relation": f"B_{w}", "values": {"wheel": w, "r_mohm": round(r * 1000, 1), "others_mohm": round(baseline * 1000, 1), "snapshots": len(v), "of": len(snaps)}, "text": f"{w} branch drop is {round((r - baseline) * 1000, 1)} mΩ above the other branches "
                             f"({round(r * 1000, 1)} vs {round(baseline * 1000, 1)} mΩ, assuming equal current per wheel), in {len(v)} of {len(snaps)} snapshots"})
    for s in snaps:
        for w, b in s["branches"].items():
            others = [x["offset_rest_v"] for k, x in s["branches"].items() if k != w and x["offset_rest_v"] is not None]
            if b["offset_rest_v"] is not None and others and abs(b["offset_rest_v"] - sum(others) / len(others)) > 0.3:
                present.add(f"O_{w}")
                findings.append({"relation": f"O_{w}", "values": {"wheel": w, "v": b["offset_rest_v"], "snapshot": s["label"]}, "text": f"{w} reads {b['offset_rest_v']} V away from the pack at rest in '{s['label']}'"})
    warm = [s for s in snaps if s["pack"] and s.get("cell_temp_c") is not None]
    if len(warm) >= 2:
        lo, hi = min(warm, key=lambda s: s["cell_temp_c"]), max(warm, key=lambda s: s["cell_temp_c"])
        if hi["cell_temp_c"] - lo["cell_temp_c"] >= 8 and lo["pack"]["r_ohm"] > 1.5 * hi["pack"]["r_ohm"]:
            present.add("P")
            findings.append({"relation": "P", "values": {"cold_mohm": round(lo["pack"]["r_ohm"] * 1000), "cold_c": lo["cell_temp_c"], "warm_mohm": round(hi["pack"]["r_ohm"] * 1000), "warm_c": hi["cell_temp_c"]}, "text": f"Pack resistance {round(lo['pack']['r_ohm'] * 1000)} mΩ at {lo['cell_temp_c']:g} °C vs "
                             f"{round(hi['pack']['r_ohm'] * 1000)} mΩ at {hi['cell_temp_c']:g} °C"})
    over = [s for s in snaps if s["limit"] and (s["limit"]["max_over"] or 0) > 0.5]
    if over:
        present.add("L")
        s = over[0]
        findings.append({"relation": "L", "values": {"over": s["limit"]["max_over"], "line": s["limit"]["line"], "snapshot": s["label"]}, "text": f"Torque command above the BMS limit by up to {s['limit']['max_over']} N.m in '{s['label']}' (line {s['limit']['line']})"})
    iso = isolability(present, odd_wheel) if odd_wheel else isolability(present, wheels[0])
    iso["notes"] = []
    if odd_wheel:
        # torque share: a wheel commanded more torque would draw more current; equal torque leaves only losses
        shares = {}
        for w in wheels:
            vals = [v for s in parse(lines) for _, m, row, _ in s["rows"] if m == f"MCU_{w}" for v in [row.get("Tq_Act")] if isinstance(v, float) and v > 20]
            if vals:
                shares[w] = sum(vals) / len(vals)
        if odd_wheel in shares and len(shares) > 1:
            others = [v for k, v in shares.items() if k != odd_wheel]
            diff = (shares[odd_wheel] - sum(others) / len(others)) / (sum(others) / len(others)) * 100
            iso["torque_share"] = {"wheel": odd_wheel, "nm": round(shares[odd_wheel], 1), "others_nm": round(sum(others) / len(others), 1), "diff_pct": round(diff)}
            iso["notes"].append(f"{odd_wheel}'s torque under load averages {round(shares[odd_wheel], 1)} N.m against {round(sum(others) / len(others), 1)} for the others "
                                f"({diff:+.0f}%). Drawing more than its share would then need higher losses, not more torque.")
    return {"version": VERSION, "wheel": odd_wheel, "signals": {k: (list(v) if isinstance(v, tuple) else v) for k, v in sig.items()}, "snapshots": snaps,
            "findings": findings, "relations_broken": sorted(present), "isolability": iso,
            "assumptions": [f"Branch current is Pack_I / {len(wheels)} (no per-wheel current is recorded).",
                            "Voltages are the values the controllers report on CAN, at their sample rate; fast transients are not seen."],
            "boundary": "Relations computed from the recorded values. They show where the physics does not add up, not which part has failed."}
