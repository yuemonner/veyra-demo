"""Electrical relations over a decoded CAN log: what is measured, what a model derives from it, and what no signal shows.

A fault that never trips an alarm can still break a physical relation. This module reads a battery-electric chassis's
recorded pack voltage, pack current and inverter DC voltages, and keeps three tiers apart in everything it reports:

  measured       quantities read straight from the recorded signals: the voltage difference between the pack and each
                 inverter input, and how fast that difference grows per amp of PACK current (k, V/A). No physical model.
  model-derived  quantities that need an assumption to compute. A branch's resistance is k divided by the branch's
                 share of the pack current; that share is not recorded, so a resistance is always given as a function
                 of the assumed share, with the range of shares the recorded torques make plausible.
  unobservable   what no recorded signal shows: each branch's DC current, where along a branch extra resistance sits,
                 each inverter's efficiency, a contact's condition.

Relations checked:
1. Branch drop. Pack_V - DC_V(w) = R_w * I_w + offset_w, with I_w = s_w * Pack_I (marginal share s_w unknown). The data
   identifies k_w = R_w * s_w only. A wheel is flagged when no plausible current split makes its resistance match the
   others': with every other branch at the same resistance, equality needs wheel w to carry s* = k_w / (k_w + (n-1) k_o)
   of the traction current; it is flagged when s* (taken conservatively, at two standard errors) is above the share the
   recorded torques allow. A flag means "this branch's resistance is high OR its DC current is above the healthy range",
   never which.
2. Pack drop. Pack_V = V0 - R * Pack_I over a launch is an APPARENT resistance (ohmic plus polarisation over the pulse);
   a ~100 ms step value is closer to ohmic. Timing skew between voltage and current, rest-voltage relaxation and
   hysteresis are checked; comparisons across snapshots name what else differs (state of charge, current, pulse length).
3. Sensor at rest. With no current, every inverter should read the pack voltage; a wheel that does not reads with an
   offset or a gain error (separable only when the rest voltage varied).
4. Command limit. TqCmd_Out <= TqLim_BMS, read directly.

For a flagged branch, each candidate mechanism is marked consistent or weakened (never ruled out: this module sees no
direct exclusion), and each extra measurement is listed with the result each candidate predicts, its cost, its strength
and its blind spots. The lowest-cost measurement that would split the candidates still open is recommended.

Methods: least squares with standard errors and a dI/dt term for timing skew; structural isolability (fault signature
analysis, Frisk, Krysander et al., Fault Diagnosis Toolbox, MIT) extended with predicted outcomes per measurement;
resistance from real-world driving data with temperature recorded alongside (battery connection diagnosis literature).
Deterministic; no model call. Where the data lacks the signals a relation needs, the relation says not applicable or
not identifiable, and no number is produced for it.
"""
from __future__ import annotations

import bisect
import math
import re
from typing import Any, Dict, List, Optional, Tuple

VERSION = "electrical-relations-v2"
ROW = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s+(?:(INFO|WARN|ERROR|DEBUG)\s+)?(\w+)\s+(.*)$")
KV = re.compile(r"([A-Za-z][\w]*)=([^\s()]+)")

LOADED_A = 20.0          # pack current above which a sample counts as loaded
REST_A = 6.0             # pack current below which the drive is at rest
MIN_POINTS = 8           # loaded samples needed for a fit
MIN_SPAN_A = 30.0        # current range needed for a fit
PAIR_GAP_S = 0.15        # interpolate between samples at most this far apart
SHARE_SPREAD = 0.15      # healthy inverter/motor loss spread on top of the torque-derived share (torque recorded; dev split p98)
SHARE_SPREAD_NO_TQ = 0.30  # share band around 1/n when no torque is recorded
I_CAL = 0.02             # pack current sensor accuracy assumed
HEALTHY_SPREAD_OHM = 0.003  # at the most favourable plausible split, a branch this far above the others is anomalous (set on the dev split)
OFFSET_V = 0.4           # at-rest disagreement with the other inverters that counts as a sensor offset
DIDT_T = 3.0             # t-value at which the dI/dt (timing) term is kept (pack fit)
LAGS_S = [x / 100 for x in range(-12, 13)]  # inverter-vs-BMS sampling lags searched
LOADDEP_V = 0.12         # load dependence: loaded line below the rest reading by this much more than the other wheels (dev split)
STEP_A = 15.0            # a current change this large between consecutive samples counts as a step


# ---------------------------------------------------------------- parsing and series
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


class _S:
    """A sampled signal: times, values, line numbers; linear interpolation between neighbouring samples."""

    def __init__(self, pts: List[Tuple[float, float, int]]):
        pts = sorted(pts)
        self.t = [p[0] for p in pts]
        self.v = [p[1] for p in pts]
        self.n = [p[2] for p in pts]

    def __len__(self) -> int:
        return len(self.t)

    def at(self, t: float) -> Optional[Tuple[float, int]]:
        i = bisect.bisect_left(self.t, t)
        if i < len(self.t) and abs(self.t[i] - t) < 1e-6:
            return self.v[i], self.n[i]
        if i == 0 or i >= len(self.t):
            return None
        t0, t1 = self.t[i - 1], self.t[i]
        if t1 - t0 > PAIR_GAP_S:
            return None
        a = (t - t0) / (t1 - t0)
        return self.v[i - 1] + a * (self.v[i] - self.v[i - 1]), self.n[i if a >= 0.5 else i - 1]


def _series(snap: Dict[str, Any], msg: str, key: str) -> _S:
    return _S([(t, v[key], n) for t, m, v, n in snap["rows"] if m == msg and isinstance(v.get(key), float)])


def _deriv(s: _S) -> List[float]:
    out = []
    for i in range(len(s)):
        a, b = max(0, i - 1), min(len(s) - 1, i + 1)
        dt = s.t[b] - s.t[a]
        out.append((s.v[b] - s.v[a]) / dt if dt > 0 and dt <= 2 * PAIR_GAP_S + 1e-9 else 0.0)
    return out


# ---------------------------------------------------------------- least squares
def _inv(a: List[List[float]]) -> Optional[List[List[float]]]:
    n = len(a)
    m = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(a)]
    scale = max(abs(a[i][i]) for i in range(n)) or 1.0
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[p][c]) < 1e-12 * scale:
            return None
        m[c], m[p] = m[p], m[c]
        piv = m[c][c]
        m[c] = [x / piv for x in m[c]]
        for r in range(n):
            if r != c and m[r][c]:
                f = m[r][c]
                m[r] = [x - f * y for x, y in zip(m[r], m[c])]
    return [row[n:] for row in m]


def _ols(X: List[List[float]], y: List[float]) -> Optional[Dict[str, Any]]:
    n, p = len(y), len(X[0]) if X else 0
    if n < p + 3:
        return None
    xtx = [[sum(X[r][i] * X[r][j] for r in range(n)) for j in range(p)] for i in range(p)]
    xty = [sum(X[r][i] * y[r] for r in range(n)) for i in range(p)]
    inv = _inv(xtx)
    if inv is None:
        return None
    beta = [sum(inv[i][j] * xty[j] for j in range(p)) for i in range(p)]
    res = [y[r] - sum(beta[i] * X[r][i] for i in range(p)) for r in range(n)]
    s2 = sum(e * e for e in res) / (n - p)
    return {"beta": beta, "se": [math.sqrt(max(0.0, s2 * inv[i][i])) for i in range(p)], "resid": res, "sd": math.sqrt(s2), "n": n}


def _t(f: Dict[str, Any], i: int) -> float:
    return abs(f["beta"][i]) / f["se"][i] if f["se"][i] > 0 else (math.inf if f["beta"][i] else 0.0)


def _r(x: Optional[float], d: int = 4) -> Optional[float]:
    return None if x is None else round(x, d)


def _mean(xs: List[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def _median(xs: List[float]) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


# ---------------------------------------------------------------- signals
def detect(lines: List[str]) -> Optional[Dict[str, Any]]:
    """The signals this module can use, found by name. None when the log has none of pack voltage, pack current or an
    inverter DC voltage (the module does not apply)."""
    snaps = parse(lines)
    if not snaps:
        return None
    keys = {(m, k) for s in snaps for _, m, v, _ in s["rows"] for k, x in v.items() if isinstance(x, float)}
    pack = next(((m, "Pack_V") for m, k in sorted(keys) if k == "Pack_V"), None)
    cur = next(((m, "Pack_I") for m, k in sorted(keys) if k == "Pack_I"), None)
    wheels = sorted({m for m, k in keys if k == "DC_V" and m.startswith("MCU_")})
    if not (pack or cur or wheels):
        return None

    def per_wheel(key: str) -> bool:
        return bool(wheels) and all((w, key) in keys for w in wheels)
    temp = next(((m, k) for m, k in sorted(keys) if k in ("T_cell_min", "T_cell")), None)
    tmax = next(((m, k) for m, k in sorted(keys) if k == "T_cell_max"), None)
    soc = next(((m, k) for m, k in sorted(keys) if k == "SOC"), None)
    cmd = next(((m, k) for m, k in sorted(keys) if k == "TqCmd_Out"), None)
    lim = next(((m, k) for m, k in sorted(keys) if k == "TqLim_BMS"), None)
    return {"pack_v": pack, "pack_i": cur, "wheels": wheels, "temp": temp, "temp_max": tmax, "soc": soc, "cmd": cmd, "lim": lim,
            "torque": per_wheel("Tq_Act"), "t_inv": per_wheel("T_inv")}


# ---------------------------------------------------------------- one snapshot
def analyse_snapshot(snap: Dict[str, Any], sig: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"label": snap["label"], "first_line": snap["first_line"], "branches": {}, "pack": None, "limit": None,
                           "torque": None, "t_inv": None}
    for key, name in (("temp", "cell_temp_c"), ("temp_max", "cell_temp_max_c"), ("soc", "soc")):
        if sig.get(key):
            s = _series(snap, *sig[key])
            out[name] = s.v[0] if len(s) else None
    pi = _series(snap, *sig["pack_i"]) if sig["pack_i"] else _S([])
    pv = _series(snap, *sig["pack_v"]) if sig["pack_v"] else _S([])
    loaded_t = [t for t, i in zip(pi.t, pi.v) if i >= LOADED_A]
    dt = _median([b - a for a, b in zip(pi.t, pi.t[1:])]) or 0.1
    out["load"] = {"peak_a": _r(max(pi.v), 1) if len(pi) else None, "n_loaded": len(loaded_t),
                   "pulse_s": _r(len(loaded_t) * dt, 2), "span_a": _r(max(pi.v) - min(pi.v), 1) if len(pi) else None}
    didt = _deriv(pi)

    def enough(n_loaded: int, span: float) -> Optional[str]:
        if n_loaded < MIN_POINTS or span < MIN_SPAN_A:
            return f"not enough load: {n_loaded} samples above {LOADED_A:g} A and a current range of {span:.0f} A (needs {MIN_POINTS} and {MIN_SPAN_A:g} A)"
        return None

    # branch drop per wheel
    for w in sig["wheels"]:
        name = w.replace("MCU_", "")
        if not sig["pack_v"] or not sig["pack_i"]:
            out["branches"][name] = {"status": "not_identifiable", "reason": "needs pack voltage and pack current" + ("" if sig["pack_i"] else " (no pack current is recorded)")}
            continue
        dv = _series(snap, w, "DC_V")

        def rows_at(lag: float) -> List[Tuple[float, float, int]]:
            out_ = []
            for t, i in zip(pi.t, pi.v):
                p, q = pv.at(t), dv.at(t + lag)
                if p and q:
                    out_.append((i, p[0] - q[0], q[1]))
            return out_
        rows0 = rows_at(0.0)
        nl = sum(1 for r in rows0 if r[0] >= LOADED_A)
        span = (max(r[0] for r in rows0) - min(r[0] for r in rows0)) if rows0 else 0.0
        rest = [r[1] for r in rows0 if r[0] <= REST_A]
        b: Dict[str, Any] = {"n_rest": len(rest), "rest_dv_v": _r(_median(rest), 3) if len(rest) >= 3 else None}
        why = enough(nl, span)
        if why:
            out["branches"][name] = {"status": "not_identifiable", "reason": why, **b}
            continue
        f0 = _ols([[1.0, r[0]] for r in rows0], [r[1] for r in rows0])
        if not f0:
            out["branches"][name] = {"status": "not_identifiable", "reason": "the fit is degenerate (current does not vary)", **b}
            continue
        best = (f0["sd"], 0.0, f0, rows0)
        for lag in LAGS_S:
            rr = rows_at(lag)
            if len(rr) < MIN_POINTS:
                continue
            f = _ols([[1.0, r[0]] for r in rr], [r[1] for r in rr])
            if f and f["sd"] < best[0] - 1e-9:
                best = (f["sd"], lag, f, rr)
        _, lag, f, rows = best
        k, k_se = f["beta"][1], f["se"][1]
        peak = max(rows, key=lambda r: r[0])
        imax = peak[0]
        # load dependence: the loaded-only line, extrapolated to the rest current, should land on the rest reading
        fl = _ols([[1.0, r[0]] for r in rows if r[0] >= LOADED_A], [r[1] for r in rows if r[0] >= LOADED_A])
        i_rest = _median([r[0] for r in rows if r[0] <= REST_A])
        rest_al = _median([r[1] for r in rows if r[0] <= REST_A])
        gap = (rest_al - (fl["beta"][0] + fl["beta"][1] * i_rest)) if fl and i_rest is not None and rest_al is not None else None
        b.update({"status": "ok", "k_v_per_a": _r(k, 6), "k_se": _r(k_se, 6), "k_unaligned": _r(f0["beta"][1], 6),
                  "lag_ms": round(lag * 1000), "k_timing_sens": _r(abs(k - f0["beta"][1]), 6),
                  "intercept_v": _r(f["beta"][0], 3), "resid_sd_v": _r(f["sd"], 3), "n": f["n"], "n_loaded": nl,
                  "dv_at_peak_v": _r(peak[1], 3), "peak_a": _r(imax, 1), "load_gap_v": _r(gap, 3),
                  "lines": [r[2] for r in rows if r[0] >= LOADED_A][:3] + [peak[2]]})
        out["branches"][name] = b

    # pack drop
    if sig["pack_v"] and sig["pack_i"]:
        rows = []
        for t, i, n, d in zip(pi.t, pi.v, pi.n, didt):
            p = pv.at(t)
            if p:
                rows.append((t, i, p[0], d, p[1]))
        if loaded_t:
            lo_t, hi_t = loaded_t[0] - 3.0, loaded_t[-1] + 1.0
            rows = [r for r in rows if lo_t <= r[0] <= hi_t]
        nl = sum(1 for r in rows if r[1] >= LOADED_A)
        span = (max(r[1] for r in rows) - min(r[1] for r in rows)) if rows else 0.0
        why = enough(nl, span)
        if why:
            out["pack"] = {"status": "not_identifiable", "reason": why}
        else:
            fs = _ols([[1.0, r[1]] for r in rows], [r[2] for r in rows])
            fd = _ols([[1.0, r[1], r[3]] for r in rows], [r[2] for r in rows])
            if not fs:
                out["pack"] = {"status": "not_identifiable", "reason": "the fit is degenerate"}
            else:
                use_dyn = bool(fd) and _t(fd, 2) >= DIDT_T
                f = fd if use_dyn else fs
                r_app = -f["beta"][1]
                skew_ms = (fd["beta"][2] / fd["beta"][1] * 1000) if use_dyn and fd["beta"][1] else None
                steps = [-(b2[2] - a2[2]) / (b2[1] - a2[1]) for a2, b2 in zip(rows, rows[1:])
                         if b2[0] - a2[0] <= PAIR_GAP_S + 1e-9 and abs(b2[1] - a2[1]) >= STEP_A]
                rise = [e for r, e in zip(rows, fs["resid"]) if r[3] > 50]
                fall = [e for r, e in zip(rows, fs["resid"]) if r[3] < -50]
                pre = [r for r in rows if r[1] <= REST_A and loaded_t and r[0] < loaded_t[0]]
                drift = None
                if len(pre) >= 3:
                    fr = _ols([[1.0, r[0] - pre[0][0]] for r in pre], [r[2] for r in pre])
                    drift = fr["beta"][1] if fr else None
                hyst = (_mean(rise) - _mean(fall)) if len(rise) >= 2 and len(fall) >= 2 else None
                r_step = _median(steps) if len(steps) >= 3 else None
                dyn_flag = (hyst is not None and abs(hyst) > 0.15) or (r_step is not None and r_app > 0 and abs(r_step - r_app) / r_app > 0.25)
                out["pack"] = {"status": "ok", "apparent_r_ohm": _r(r_app, 5), "apparent_se_ohm": _r(f["se"][1], 5),
                               "r_static_fit_ohm": _r(-fs["beta"][1], 5), "timing_term_t": _r(_t(fd, 2), 1) if fd else None,
                               "timing_used": use_dyn, "implied_skew_ms": _r(skew_ms, 0),
                               "step_r_ohm": _r(r_step, 5), "n_steps": len(steps), "hysteresis_v": _r(hyst, 3),
                               "dynamic": bool(dyn_flag), "v0": _r(f["beta"][0], 2),
                               "v_rest": _r(_mean([r[2] for r in pre]), 2) if pre else None,
                               "rest_drift_v_per_s": _r(drift, 3), "n": f["n"],
                               "peak_a": _r(max(r[1] for r in rows), 1), "min_v": _r(min(r[2] for r in rows), 2),
                               "line": max(rows, key=lambda r: r[1])[4]}
    elif not sig["pack_i"]:
        out["pack"] = {"status": "not_identifiable", "reason": "no pack current is recorded"}
    else:
        out["pack"] = {"status": "not_identifiable", "reason": "no pack voltage is recorded"}

    # per-wheel torque under load, and inverter temperature
    if sig["torque"]:
        tq = {}
        for w in sig["wheels"]:
            vals = [abs(v["Tq_Act"]) for t, m, v, _ in snap["rows"] if m == w and isinstance(v.get("Tq_Act"), float)
                    and (not len(pi) or ((pi.at(t) or (0.0,))[0] >= LOADED_A))]
            if len(vals) >= 3:
                tq[w.replace("MCU_", "")] = {"mean": _mean(vals), "mean_sq": _mean([x * x for x in vals]), "n": len(vals)}
        out["torque"] = tq or None
    if sig["t_inv"]:
        ti = {}
        for w in sig["wheels"]:
            vals = [v["T_inv"] for _, m, v, _ in snap["rows"] if m == w and isinstance(v.get("T_inv"), float)]
            if vals:
                ti[w.replace("MCU_", "")] = {"start": vals[0], "max": max(vals), "rise": max(vals) - vals[0]}
        out["t_inv"] = ti or None

    if sig["cmd"] and sig["lim"]:
        cs, ls = _series(snap, *sig["cmd"]), _series(snap, *sig["lim"])
        over = [(c - l[0], n, t) for t, c, n in zip(cs.t, cs.v, cs.n) for l in [ls.at(t)] if l]
        worst = max(over, default=None)
        t0 = loaded_t[0] if loaded_t else (cs.t[0] if len(cs) else None)
        first = next((o for o in over if o[0] > 0.5), None)
        out["limit"] = {"max_over": _r(worst[0], 1) if worst else None, "line": worst[1] if worst else None,
                        "first_over_s": _r(first[2] - t0, 2) if first and t0 is not None else None, "first_line": first[1] if first else None}
    return out


# ---------------------------------------------------------------- shares and branch decisions
def share_band(snap: Dict[str, Any], w: str, wheels: List[str]) -> Tuple[float, float, str]:
    """The range of wheel w's (marginal) share of traction current that healthy operation allows in this snapshot.
    With torque recorded: between its torque share (power dominated by mechanical output) and its torque-squared share
    (dominated by copper loss at low speed), widened by the healthy loss spread. Without torque: 1/n widened more."""
    n = len(wheels)
    tq = snap.get("torque") or {}
    if all(x in tq and tq[x]["mean"] > 0 for x in wheels):
        lin = tq[w]["mean"] / sum(tq[x]["mean"] for x in wheels)
        sq = tq[w]["mean_sq"] / sum(tq[x]["mean_sq"] for x in wheels)
        return min(lin, sq) * (1 - SHARE_SPREAD), max(lin, sq) * (1 + SHARE_SPREAD), "torque"
    return (1 - SHARE_SPREAD_NO_TQ) / n, (1 + SHARE_SPREAD_NO_TQ) / n, "no_torque"


def _unc(b: Dict[str, Any]) -> float:
    """Half-width of the k interval: timing sensitivity (aligned vs unaligned fit) plus two standard errors."""
    return (b.get("k_timing_sens") or 0.0) + 2 * (b.get("k_se") or 0.0)


def _branch_decisions(snaps: List[Dict[str, Any]], wheels: List[str], untrusted: Optional[List[str]] = None) -> Tuple[List[str], Dict[str, List[Dict[str, Any]]]]:
    """Iteratively flag the branch that no plausible current split explains, excluding flagged ones (and wheels whose
    voltage sensor is flagged) from the baseline."""
    untrusted = list(untrusted or [])
    n = len(wheels)
    flagged: List[str] = []
    detail: Dict[str, List[Dict[str, Any]]] = {}
    while True:
        cand = []
        for w in wheels:
            if w in flagged:
                continue
            rows = []
            for j, s in enumerate(snaps):
                b = s["branches"].get(w) or {}
                others = [x for x in wheels if x != w and x not in flagged and x not in untrusted and (s["branches"].get(x) or {}).get("status") == "ok"]
                if b.get("status") != "ok" or not others:
                    continue
                k = b["k_v_per_a"]
                ko = _mean([s["branches"][x]["k_v_per_a"] for x in others])
                u_o = _mean([_unc(s["branches"][x]) for x in others]) / math.sqrt(len(others))
                kw_lo, ko_hi = k - _unc(b), ko + u_o
                s_star = k / (k + (n - 1) * ko) if k > 0 and ko > 0 else None
                s_cons = kw_lo / (kw_lo + (n - 1) * ko_hi) if kw_lo > 0 and ko_hi > 0 else 0.0
                lo, hi, basis = share_band(s, w, wheels)
                excess = n * (k - ko)
                # most favourable plausible split for w: it carries the top of its band, the others share the rest
                excess_min = kw_lo / hi - ko_hi * (n - 1) / (1 - hi)
                anom = excess_min > HEALTHY_SPREAD_OHM
                rows.append({"snapshot": j, "k": k, "k_others": ko, "s_star": s_star, "s_star_cons": s_cons, "band": (lo, hi), "basis": basis,
                             "excess_equal_share_ohm": excess, "excess_min_ohm": excess_min, "anomalous": anom})
            m = len(rows)
            hits = sum(1 for r in rows if r["anomalous"])
            detail[w] = rows
            if m and hits >= max(1, math.ceil(m / 2)):
                cand.append((sum(r["excess_min_ohm"] for r in rows) / m, w))
        if not cand:
            return flagged, detail
        flagged.append(max(cand)[1])


def _agg_k(snaps: List[Dict[str, Any]], w: str) -> Optional[Tuple[float, float]]:
    """Inverse-variance mean of k over snapshots, and its half-width (at least the snapshots' scatter)."""
    pts = [(s["branches"][w]["k_v_per_a"], _unc(s["branches"][w])) for s in snaps if (s["branches"].get(w) or {}).get("status") == "ok"]
    if not pts:
        return None
    ws = [1 / max(u, 1e-6) ** 2 for _, u in pts]
    k = sum(x * a for (x, _), a in zip(pts, ws)) / sum(ws)
    u = 1 / math.sqrt(sum(ws))
    if len(pts) > 1:
        sd = math.sqrt(sum((x - k) ** 2 for x, _ in pts) / (len(pts) - 1))
        u = max(u, 2 * sd / math.sqrt(len(pts)))
    return k, u


# ---------------------------------------------------------------- mechanisms and measurements
MECHANISMS = {
    "contact": "{w} connector or terminal contact resistance",
    "conductor": "{w} cable, busbar or fuse resistance",
    "inverter_draw": "{w} inverter or motor drawing more DC current (losses), branch resistance normal",
    "uneven_load": "{w} carrying more load than the other wheels (healthy)",
    "sensor_gain": "{w} DC voltage sensor gain error",
}

# id, cost (0 data already logged or a log pull, 1 parked vehicle at the depot, 2 instrumented driven launch), what,
# predicted outcome class per mechanism, strength, blind spot.
MEASUREMENTS = [
    {"id": "t_inv_trend", "cost": 0, "approval": False,
     "what": "Pull a full shift of logs and compare {w}'s inverter temperature with the other inverters at the same torque",
     "predicts": {"inverter_draw": "hotter", "contact": "similar", "conductor": "similar", "uneven_load": "similar", "sensor_gain": "similar"},
     "strength": "weak", "blind": "Cooling, thermal mass and 1 °C resolution can hide the difference: a hotter inverter supports, a similar one only weakens."},
    {"id": "meter_v", "cost": 1, "approval": True,
     "what": "Reference voltmeter at the {w} inverter DC input, key on, compared with the DC_V it reports on CAN",
     "predicts": {"sensor_gain": "mismatch", "contact": "match", "conductor": "match", "inverter_draw": "match", "uneven_load": "match"},
     "strength": "strong", "blind": "Shows the sensor error at one voltage; a gain error needs two voltages (e.g. before and after a short load)."},
    {"id": "segment_r", "cost": 1, "approval": True,
     "what": "With high voltage isolated, and before anyone unplugs or reseats it: four-wire resistance of each {w} segment (pack-side connector, cable, fuse, inverter-side connector), and the same on one healthy wheel",
     "predicts": {"contact": "connector_high", "conductor": "cable_high", "inverter_draw": "normal", "uneven_load": "normal", "sensor_gain": "normal"},
     "strength": "strong", "blind": "A contact that only opens under load, heat or vibration can read normal at rest; a normal reading weakens a contact fault, it does not exclude it."},
    {"id": "branch_i", "cost": 2, "approval": True,
     "what": "HV-rated DC clamp on the {w} inverter input and on one healthy wheel during the same ramp launch",
     "predicts": {"contact": "in_band", "conductor": "in_band", "sensor_gain": "in_band", "uneven_load": "torque_share", "inverter_draw": "high"},
     "strength": "strong", "blind": "Separates current from resistance, not connector from cable."},
    {"id": "segment_dv_loaded", "cost": 2, "approval": True,
     "what": "Voltage across each {w} segment during a ramp launch, and the same on one healthy wheel",
     "predicts": {"contact": "connector_high", "conductor": "cable_high", "inverter_draw": "all_scaled", "uneven_load": "all_scaled", "sensor_gain": "normal"},
     "strength": "strong", "blind": "Live high-voltage measurement on a driven vehicle: insulated probes, an engineer's approval."},
]
COST = {0: "data already logged (a log pull)", 1: "parked vehicle at the depot", 2: "instrumented driven launch"}


def _assess(w: str, wheels: List[str], snaps: List[Dict[str, Any]], detail: List[Dict[str, Any]], pack_r: Optional[float],
            rest_dev: Optional[float], v_rest: Optional[float], load_dependent: bool) -> Dict[str, Any]:
    """Candidates for a flagged branch, each consistent or weakened (with why), and the measurement plan."""
    rows = [r for r in detail if r["anomalous"]] or detail
    s_star = _median([r["s_star"] for r in rows if r["s_star"]]) or 0.0
    lo = _median([r["band"][0] for r in rows])
    hi = _median([r["band"][1] for r in rows])
    basis = rows[0]["basis"] if rows else "no_torque"
    dk = _median([r["k"] - r["k_others"] for r in rows]) or 0.0
    cands = []
    for cid in ("contact", "conductor", "inverter_draw", "uneven_load", "sensor_gain"):
        c = {"id": cid, "name": MECHANISMS[cid].format(w=w), "status": "consistent", "why": ""}
        if cid in ("contact", "conductor"):
            c["why"] = f"Extra resistance in the branch gives this drop at a normal current share ({lo:.0%}–{hi:.0%})."
        elif cid == "inverter_draw":
            c["why"] = (f"With a normal branch resistance, {w} would carry about {s_star:.0%} of the traction current, above the "
                        f"{lo:.0%}–{hi:.0%} healthy operation allows. Recorded torque does not show DC current or losses, so this stays open.")
        elif cid == "uneven_load":
            if basis == "torque":
                c["status"] = "weakened"
                c["why"] = (f"The recorded torques put {w}'s share at {lo:.0%}–{hi:.0%}; matching the other branches would need "
                            f"{s_star:.0%}. Weakened, not excluded: the share band assumes a loss model.")
            else:
                c["why"] = "No per-wheel torque is recorded, so an uneven load cannot be checked."
        elif cid == "sensor_gain":
            if pack_r and pack_r > 0 and rest_dev is not None and v_rest:
                g1 = dk / pack_r
                implied = -g1 * v_rest
                c["values"] = {"gain_error_pct": round(g1 * 100, 1), "implied_rest_v": round(implied, 2), "measured_rest_v": round(rest_dev, 2)}
                if abs(implied - rest_dev) > max(0.3, 0.5 * abs(implied)):
                    c["status"] = "weakened"
                    c["why"] = (f"A gain error large enough for this slope ({g1:+.1%}) would also show about {implied:+.2f} V at rest; "
                                f"{w} reads {rest_dev:+.2f} V from the others at rest. Weakened, not excluded: an offset could partly cancel it.")
                else:
                    c["why"] = f"A {g1:+.1%} gain error would explain the slope and matches the {rest_dev:+.2f} V seen at rest."
            else:
                c["why"] = "The at-rest reading or the pack resistance is not available, so a gain error cannot be checked."
        cands.append(c)
    open_ids = [c["id"] for c in cands if c["status"] == "consistent"]
    plan = []
    for m in MEASUREMENTS:
        strength = m["strength"]
        note = m["blind"]
        if m["id"] == "segment_r" and load_dependent:
            strength, note = "weak", ("Extrapolated to zero current, this branch's loaded drop lands below its at-rest reading, as a contact "
                                      "that opens under load would make it; a resistance check at rest may read normal. " + m["blind"])
        if m["id"] == "branch_i" and s_star - hi < 0.06:
            strength, note = "weak", "The share needed to explain the drop is too close to the healthy range for a clamp meter to resolve."
        groups: Dict[str, List[str]] = {}
        for cid in open_ids:
            groups.setdefault(m["predicts"][cid], []).append(cid)
        expected = {cid: _expect(m["id"], m["predicts"][cid], w, s_star, lo, hi, dk, wheels) for cid in open_ids}
        plan.append({"id": m["id"], "what": m["what"].format(w=w), "cost": m["cost"], "cost_label": COST[m["cost"]], "needs_approval": m["approval"],
                     "strength": strength, "blind_spot": note, "expected": expected,
                     "splits": [sorted(v) for v in groups.values()], "discriminates": len(groups) >= 2})
    useful = [p for p in plan if p["discriminates"] and p["strength"] == "strong"]
    rec = min(useful, key=lambda p: (p["cost"], -len(p["splits"]))) if useful else None
    then = None
    if rec and rec["id"] == "segment_r" and "contact" in open_ids:
        then = {"if": "every segment reads normal", "do": "segment_dv_loaded",
                "why": "a contact that opens only under load reads normal at rest; measure under load before treating the contact as weakened"}
    return {"wheel": w, "s_star": _r(s_star, 3), "band": [_r(lo, 3), _r(hi, 3)], "band_basis": basis, "candidates": cands, "open": open_ids,
            "measurements": plan, "recommended": rec["id"] if rec else None, "then": then,
            "free_checks": [p["id"] for p in plan if p["cost"] == 0 and p["discriminates"]]}


def _expect(mid: str, cls: str, w: str, s_star: float, lo: float, hi: float, dk: float, wheels: List[str]) -> str:
    n = len(wheels)
    ex = n * dk * 1000
    return {
        ("t_inv_trend", "hotter"): f"{w} inverter warmer than the others at the same torque",
        ("t_inv_trend", "similar"): "no consistent temperature difference",
        ("meter_v", "mismatch"): "meter and CAN differ, by an amount that grows with voltage",
        ("meter_v", "match"): "meter and CAN agree within about 0.1 V",
        ("segment_r", "connector_high"): f"a connector segment about {ex:.0f} mΩ above the healthy wheel's",
        ("segment_r", "cable_high"): f"the cable or fuse about {ex:.0f} mΩ above the healthy wheel's",
        ("segment_r", "normal"): "every segment matches the healthy wheel",
        ("branch_i", "in_band"): f"{w} carries {lo:.0%}–{hi:.0%} of traction current, like the healthy wheel",
        ("branch_i", "high"): f"{w} carries about {s_star:.0%} of traction current, {s_star * n:.1f}× an equal share",
        ("branch_i", "torque_share"): f"{w}'s current follows its torque share",
        ("segment_dv_loaded", "connector_high"): "the connector segment's drop is high, the cable's is normal",
        ("segment_dv_loaded", "cable_high"): "the cable or fuse drop is high, the connectors' are normal",
        ("segment_dv_loaded", "all_scaled"): f"every {w} segment drops more by the same factor (about {s_star * n:.1f}×)",
        ("segment_dv_loaded", "normal"): "every segment matches the healthy wheel",
    }.get((mid, cls), cls)


# ---------------------------------------------------------------- the whole log
def analyse(lines: List[str]) -> Optional[Dict[str, Any]]:
    sig = detect(lines)
    if not sig:
        return None
    parsed = parse(lines)
    snaps = [analyse_snapshot(s, sig) for s in parsed]
    wheels = [w.replace("MCU_", "") for w in sig["wheels"]]
    n = len(wheels)
    findings: List[Dict[str, Any]] = []
    relations: Dict[str, Dict[str, Any]] = {}
    model: Dict[str, Any] = {"branches": {}, "sensitivity": {}}
    assessments: List[Dict[str, Any]] = []

    # ---- branch relation
    ok_snaps = [s for s in snaps if sum(1 for b in s["branches"].values() if b.get("status") == "ok") >= 2]
    if n < 2:
        relations["branch"] = {"status": "not_applicable", "reason": f"needs at least two inverter DC voltages ({n} recorded)"}
    elif not sig["pack_v"] or not sig["pack_i"]:
        relations["branch"] = {"status": "not_identifiable", "reason": "needs pack voltage and pack current" + ("" if sig["pack_i"] else "; no pack current is recorded, so a voltage difference cannot be related to current")}
    elif not ok_snaps:
        reasons = sorted({b.get("reason") for s in snaps for b in s["branches"].values() if b.get("reason")})
        relations["branch"] = {"status": "not_identifiable", "reason": reasons[0] if reasons else "no snapshot has enough load"}
    else:
        # sensor at rest
        devs: Dict[str, List[Tuple[float, Optional[float]]]] = {w: [] for w in wheels}
        for s in snaps:
            rd = {w: (s["branches"].get(w) or {}).get("rest_dv_v") for w in wheels}
            vr = (s.get("pack") or {}).get("v_rest") if isinstance(s.get("pack"), dict) else None
            for w in wheels:
                others = [rd[x] for x in wheels if x != w and rd[x] is not None]
                if rd[w] is not None and others:
                    devs[w].append((rd[w] - _median(others), vr))
        off_flag = []
        for w in wheels:
            d = devs[w]
            if d and sum(1 for x, _ in d if abs(x) > OFFSET_V) >= max(1, math.ceil(len(d) / 2)):
                off_flag.append(w)
                vs = [(v, x) for x, v in d if v is not None]
                gain_note = None
                if len(vs) >= 2 and max(v for v, _ in vs) - min(v for v, _ in vs) >= 2.0:
                    fg = _ols([[1.0, v] for v, _ in vs], [x for _, x in vs]) if len(vs) >= 5 else None
                    gain_note = "gain_or_offset" if not fg else ("gain" if _t(fg, 1) >= 3 else "offset")
                findings.append({"relation": f"O_{w}", "tier": "measured", "kind": "anomaly",
                                 "values": {"wheel": w, "v": round(_median([x for x, _ in d]), 2), "snapshots": sum(1 for x, _ in d if abs(x) > OFFSET_V), "of": len(d),
                                            "separable": gain_note},
                                 "text": f"At rest, {w} reads {_median([x for x, _ in d]):+.2f} V from the other inverters (pack minus inverter), in "
                                         f"{sum(1 for x, _ in d if abs(x) > OFFSET_V)} of {len(d)} snapshots. A sensor offset or gain error; "
                                         + ("which one is not identifiable from this data (the rest voltage did not vary by 2 V)." if gain_note is None else
                                            "the rest voltage varied enough to compare the two." if gain_note == "gain_or_offset" else f"the pattern looks like {'a gain error' if gain_note == 'gain' else 'an offset'}."),
                                 "assumptions": ["At rest no current flows in the branches, so every inverter should read the pack voltage."]})
        relations["sensor"] = {"status": "ok" if any(devs.values()) else "not_identifiable",
                               **({} if any(devs.values()) else {"reason": "no snapshot has the drive at rest"}), "flagged": off_flag}
        flagged, detail = _branch_decisions(ok_snaps, wheels, off_flag)
        relations["branch"] = {"status": "ok", "snapshots_used": len(ok_snaps), "of": len(snaps), "flagged": flagged}
        aggs = {w: _agg_k(ok_snaps, w) for w in wheels}
        healthy = [w for w in wheels if w not in flagged and w not in off_flag and aggs[w]] or [w for w in wheels if w not in flagged and aggs[w]]
        for w in wheels:
            if not aggs[w]:
                continue
            k, u = aggs[w]
            bands = [share_band(s, w, wheels) for s in ok_snaps if (s["branches"].get(w) or {}).get("status") == "ok"]
            lo, hi = _median([b[0] for b in bands]), _median([b[1] for b in bands])
            # another flagged wheel may draw more than its share, leaving less for this one
            for f_ in flagged:
                if f_ != w:
                    sf = _median([r["s_star"] for r in detail.get(f_) or [] if r["anomalous"] and r["s_star"]])
                    if sf and sf > 1 / n:
                        lo *= (1 - sf) / (1 - 1 / n)
            r_lo = max(0.0, (k - u) / hi * (1 - I_CAL))
            r_hi = (k + u) / lo * (1 + I_CAL)
            model["branches"][w] = {"k_v_per_a": _r(k, 6), "k_halfwidth": _r(u, 6), "r_equal_share_ohm": _r(n * k, 5), "r_equal_share_halfwidth_ohm": _r(n * u, 5),
                                    "share_band": [_r(lo, 3), _r(hi, 3)], "band_basis": bands[0][2] if bands else None,
                                    "r_interval_ohm": [_r(r_lo, 5), _r(r_hi, 5)]}
        k_o = _mean([aggs[x][0] for x in healthy]) if healthy else None
        for w in flagged:
            k = aggs[w][0]
            grid = sorted({0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.5, 0.6} | {round(1 / n, 3)})
            band = model["branches"][w]["share_band"]
            model["sensitivity"][w] = [{"share": s_, "r_ohm": _r(k / s_, 5), "others_r_ohm": _r(k_o * (n - 1) / (1 - s_), 5) if k_o else None,
                                        "in_band": band[0] <= s_ <= band[1]} for s_ in grid]
            rows = detail.get(w) or []
            hit = [r for r in rows if r["anomalous"]]
            s_star = _median([r["s_star"] for r in hit if r["s_star"]])
            findings.append({"relation": f"B_{w}", "tier": "measured", "kind": "anomaly",
                             "values": {"wheel": w, "k_mv_per_a": round(k * 1000, 2), "others_mv_per_a": round(k_o * 1000, 2) if k_o else None,
                                        "ratio": round(k / k_o, 2) if k_o else None, "snapshots": len(hit), "of": len(rows)},
                             "text": (f"{w}'s voltage drop grows {k * 1000:.2f} mV per amp of pack current against {k_o * 1000:.2f} for the other "
                                      f"inverters ({k / k_o:.1f}×), in {len(hit)} of {len(rows)} snapshots.") if k_o else f"{w}'s drop per amp stands out.",
                             "assumptions": ["Pack and inverter voltages are sampled close enough in time; a timing term is fitted where it matters."]})
            mb = model["branches"][w]
            findings.append({"relation": f"R_{w}", "tier": "model", "kind": "anomaly",
                             "values": {"wheel": w, "r_equal_mohm": round(mb["r_equal_share_ohm"] * 1000, 1),
                                        "others_equal_mohm": round(n * k_o * 1000, 1) if k_o else None,
                                        "r_lo_mohm": round(mb["r_interval_ohm"][0] * 1000, 1), "r_hi_mohm": round(mb["r_interval_ohm"][1] * 1000, 1),
                                        "band_lo_pct": round(mb["share_band"][0] * 100), "band_hi_pct": round(mb["share_band"][1] * 100),
                                        "s_star_pct": round((s_star or 0) * 100), "basis": mb["band_basis"]},
                             "text": (f"If {w} carries an equal share of current, its branch resistance is {mb['r_equal_share_ohm'] * 1000:.1f} mΩ against "
                                      f"{n * k_o * 1000:.1f} mΩ for the others; over the {mb['share_band'][0]:.0%}–{mb['share_band'][1]:.0%} share "
                                      f"{'the recorded torques allow' if mb['band_basis'] == 'torque' else 'assumed without torque data'}, "
                                      f"{mb['r_interval_ohm'][0] * 1000:.1f}–{mb['r_interval_ohm'][1] * 1000:.1f} mΩ. Its resistance would match the others' only if it carried "
                                      f"about {(s_star or 0):.0%} of the traction current.") if k_o else "",
                             "assumptions": [f"Branch current is not recorded; resistance = drop per amp ÷ the branch's share of pack current.",
                                             "The other branches have equal resistance to one another.",
                                             f"Pack current sensor within ±{I_CAL:.0%}."]})
        # assessments for flagged branches
        pack_rs = [s["pack"]["apparent_r_ohm"] for s in snaps if isinstance(s.get("pack"), dict) and s["pack"].get("status") == "ok"]
        v_rests = [s["pack"]["v_rest"] for s in snaps if isinstance(s.get("pack"), dict) and s["pack"].get("v_rest")]
        for w in flagged:
            rest_dev = _median([x for x, _ in devs.get(w) or []])
            gaps = []
            for s_ in ok_snaps:
                bw = s_["branches"].get(w) or {}
                og = [(s_["branches"].get(x) or {}).get("load_gap_v") for x in wheels if x != w and x not in flagged]
                og = [g for g in og if g is not None]
                if bw.get("load_gap_v") is not None and og:
                    gaps.append(bw["load_gap_v"] - _median(og))
            load_dep = bool(gaps) and sum(1 for g in gaps if g > LOADDEP_V) >= max(1, math.ceil(len(gaps) / 2))
            a = _assess(w, wheels, ok_snaps, detail.get(w) or [], _median(pack_rs), rest_dev, _median(v_rests), load_dep)
            a["load_dependent"] = load_dep
            # weak evidence: inverter heating
            diffs = []
            for s in ok_snaps:
                ti = s.get("t_inv") or {}
                if w in ti and any(x in ti for x in wheels if x != w):
                    diffs.append(ti[w]["rise"] - _mean([ti[x]["rise"] for x in wheels if x != w and x in ti]))
            if diffs:
                a["t_inv"] = {"rise_diff_c": round(_median(diffs), 1), "snapshots": len(diffs),
                              "reading": "supports" if _median(diffs) >= 2 else "no_difference",
                              "note": (f"{w}'s inverter warmed {_median(diffs):+.1f} °C relative to the others during the launches. "
                                       + ("That supports extra losses in it (weak evidence)." if _median(diffs) >= 2 else
                                          "No extra heating is seen, which only weakly argues against extra losses: launches are short and the resolution is 1 °C."))}
            assessments.append(a)
        # torque: what it does and does not show
        tq_note = None
        if sig["torque"] and flagged:
            means = {w: _mean([s["torque"][w]["mean"] for s in ok_snaps if s.get("torque") and w in s["torque"]]) for w in wheels}
            w = flagged[0]
            others = [means[x] for x in wheels if x != w and means.get(x)]
            if means.get(w) and others:
                diff = (means[w] - _mean(others)) / _mean(others) * 100
                tq_note = {"wheel": w, "nm": round(means[w], 1), "others_nm": round(_mean(others), 1), "diff_pct": round(diff),
                           "text": (f"{w}'s torque under load averages {means[w]:.1f} N.m against {_mean(others):.1f} for the others ({diff:+.0f}%). "
                                    "Similar torque means similar mechanical load; it does not show similar DC current, because DC current also "
                                    "depends on each inverter's and motor's losses, which are not recorded.")}

    # ---- pack relation
    packs = [s for s in snaps if isinstance(s.get("pack"), dict) and s["pack"].get("status") == "ok"]
    if not sig["pack_v"] or not sig["pack_i"]:
        relations["pack"] = {"status": "not_identifiable", "reason": "needs pack voltage and pack current"}
    elif not packs:
        reasons = [s["pack"].get("reason") for s in snaps if isinstance(s.get("pack"), dict) and s["pack"].get("reason")]
        relations["pack"] = {"status": "not_identifiable", "reason": reasons[0] if reasons else "no snapshot has enough load"}
    else:
        relations["pack"] = {"status": "ok", "snapshots_used": len(packs), "of": len(snaps)}
        temps = [s for s in packs if s.get("cell_temp_c") is not None]
        if len(temps) >= 2:
            lo, hi = min(temps, key=lambda s: s["cell_temp_c"]), max(temps, key=lambda s: s["cell_temp_c"])
            if hi["cell_temp_c"] - lo["cell_temp_c"] >= 5:
                conf = []
                if lo.get("soc") is not None and hi.get("soc") is not None and abs(lo["soc"] - hi["soc"]) >= 10:
                    conf.append({"id": "soc", "text": f"state of charge also differs ({lo['soc']:.0f}% vs {hi['soc']:.0f}%)"})
                if lo["pack"]["peak_a"] and hi["pack"]["peak_a"] and max(lo["pack"]["peak_a"], hi["pack"]["peak_a"]) / max(1.0, min(lo["pack"]["peak_a"], hi["pack"]["peak_a"])) > 1.3:
                    conf.append({"id": "current", "text": f"peak current differs ({lo['pack']['peak_a']:.0f} A vs {hi['pack']['peak_a']:.0f} A)"})
                pl, ph = lo["load"]["pulse_s"] or 0, hi["load"]["pulse_s"] or 0
                if min(pl, ph) > 0 and max(pl, ph) / min(pl, ph) > 1.5:
                    conf.append({"id": "pulse", "text": f"launch length differs ({pl:.1f} s vs {ph:.1f} s), which changes the polarisation included"})
                if lo["pack"]["dynamic"] or hi["pack"]["dynamic"]:
                    conf.append({"id": "dynamic", "text": "the voltage responds with a delay (polarisation), so these are apparent values over each launch"})
                ra, rb = lo["pack"]["apparent_r_ohm"], hi["pack"]["apparent_r_ohm"]
                findings.append({"relation": "P", "tier": "measured", "kind": "observation",
                                 "values": {"cold_mohm": round(ra * 1000), "cold_c": lo["cell_temp_c"], "warm_mohm": round(rb * 1000), "warm_c": hi["cell_temp_c"],
                                            "cold_soc": lo.get("soc"), "warm_soc": hi.get("soc"), "confounds": [c["id"] for c in conf]},
                                 "text": (f"Apparent pack resistance over the launch: {ra * 1000:.0f} mΩ at {lo['cell_temp_c']:g} °C against {rb * 1000:.0f} mΩ at "
                                          f"{hi['cell_temp_c']:g} °C. " + (("Other conditions differ too: " + "; ".join(c["text"] for c in conf) +
                                          ". This data cannot attribute the difference to temperature alone.") if conf else
                                          "State of charge, current and launch length are similar, so temperature is the main difference.")),
                                 "assumptions": ["Apparent resistance includes ohmic and polarisation parts over each launch; it is not a cell's ohmic resistance.",
                                                 "T_cell_min is the coldest cell, not the average."]})
                relations["pack"]["confounds"] = conf
    # ---- limit relation
    if sig["cmd"] and sig["lim"]:
        over = [s for s in snaps if s["limit"] and (s["limit"]["max_over"] or 0) > 0.5]
        relations["limit"] = {"status": "ok"}
        if over:
            s = over[0]
            findings.append({"relation": "L", "tier": "measured", "kind": "anomaly",
                             "values": {"over": s["limit"]["max_over"], "line": s["limit"]["line"], "snapshot": s["label"]},
                             "text": f"Torque command above the BMS limit by up to {s['limit']['max_over']} N.m in '{s['label']}' (line {s['limit']['line']}).",
                             "assumptions": ["Both signals are the VCU's own values on CAN."]})
    else:
        relations["limit"] = {"status": "not_applicable", "reason": "no torque command and BMS limit pair is recorded"}

    flagged = (relations.get("branch") or {}).get("flagged") or []
    return {"version": VERSION, "applies": True, "wheel": flagged[0] if flagged else None, "wheels": wheels,
            "signals": {k: (list(v) if isinstance(v, tuple) else v) for k, v in sig.items()},
            "relations": relations, "snapshots": snaps, "findings": findings,
            "relations_broken": sorted(f["relation"] for f in findings if f["kind"] == "anomaly" and f["tier"] == "measured"),
            "model": model, "assessments": assessments, "torque_note": tq_note if relations.get("branch", {}).get("status") == "ok" else None,
            "identifiability": identifiability(sig),
            "settings": {"share_spread": SHARE_SPREAD, "share_spread_no_torque": SHARE_SPREAD_NO_TQ, "pack_current_accuracy": I_CAL,
                         "healthy_spread_ohm": HEALTHY_SPREAD_OHM, "offset_v": OFFSET_V},
            "boundary": "Computed from the recorded values. A flag shows where the physics does not add up, not which part has failed; "
                        "no mechanism is ruled out here, only weakened."}


def identifiability(sig: Dict[str, Any]) -> Dict[str, List[str]]:
    measured, derived, unobs = [], [], []
    if sig["pack_v"] and sig["wheels"]:
        measured.append("Voltage difference between the pack and each inverter input, at rest and under load")
    if sig["pack_v"] and sig["pack_i"] and sig["wheels"]:
        measured.append("How fast that difference grows per amp of pack current, for each inverter")
        derived.append("Branch resistance: needs each branch's share of the current, which is not recorded; given over the plausible range of shares")
    if sig["pack_v"] and sig["pack_i"]:
        measured.append("Pack voltage drop against pack current over each launch (an apparent resistance)")
        derived.append("Pack ohmic resistance: needs a fast current step; at the logged sample rate the step value still includes some polarisation")
    if sig["torque"]:
        measured.append("Each wheel's torque, as its inverter reports it")
    if sig["t_inv"]:
        measured.append("Each inverter's temperature, as it reports it")
    unobs += ["Each branch's DC current", "Where along a branch extra resistance sits (connector, cable, fuse, inverter input)",
              "Each inverter's and motor's efficiency", "A contact's condition (seating, torque, corrosion)"]
    if not sig["torque"] and sig["wheels"]:
        unobs.append("Each wheel's load (no per-wheel torque is recorded)")
    return {"measured": measured, "model_derived": derived, "unobservable": unobs}


# ---------------------------------------------------------------- what the investigating agent is given
MEAS_WORDS = {"segment_r": r"four[- ]wire|4[- ]wire|segment resistance|四线|分段电阻|段电阻",
              "segment_dv_loaded": r"(voltage|drop) across (each )?(the )?(connector|segment)|segment drop|分段压降|接插件两侧|跨接",
              "branch_i": r"clamp|branch current|DC current into|电流钳|支路电流|输入电流",
              "meter_v": r"voltmeter|reference meter|万用表|电压表|标准表",
              "t_inv_trend": r"inverter temperature|T_inv|逆变器温度"}
CAND_WORDS = {"contact": r"connector|terminal|contact|crimp|接插件|端子|接触|压接",
              "conductor": r"cable|busbar|fuse|harness|conductor|线缆|母排|保险丝|线束|导线",
              "inverter_draw": r"inverter|motor loss|efficien|draws? more|逆变器|电机损耗|效率",
              "sensor_gain": r"sensor|gain|calibrat|传感器|增益|标定"}


def agent_view(r: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """A compact copy for the investigating agent: tiers, findings with assumptions, candidates and the measurement plan."""
    if not r:
        return {"applies": False, "note": "No pack voltage, pack current or inverter DC voltage in this log."}
    out = {"version": r["version"], "relations": r["relations"], "identifiability": r["identifiability"],
           "findings": [{k: f[k] for k in ("relation", "tier", "kind", "text", "assumptions")} for f in r["findings"]],
           "torque_note": (r.get("torque_note") or {}).get("text"), "boundary": r["boundary"],
           "how_to_use": ("Cite a finding by its relation id and tier. 'measured' is read from the signals; 'model' depends on the "
                          "assumptions listed with it: never call a model-derived resistance measured or identified. The candidates "
                          "marked consistent cannot be told apart by this data: keep them open (none is ruled out here). Use the "
                          "recommended measurement, with what each candidate predicts, as the next action unless a cheaper check "
                          "separates them.")}
    for w, rows in (r.get("model") or {}).get("sensitivity", {}).items():
        out.setdefault("resistance_vs_share", {})[w] = [f"{x['share']:.0%} share: {x['r_ohm'] * 1000:.1f} mΩ (others {x['others_r_ohm'] * 1000:.1f})"
                                                        for x in rows if x.get("others_r_ohm") is not None]
    out["assessments"] = [{"wheel": a["wheel"], "load_dependent": a.get("load_dependent"),
                           "candidates": [{k: c[k] for k in ("id", "name", "status", "why")} for c in a["candidates"]],
                           "recommended": a["recommended"], "then": a.get("then"), "t_inv": (a.get("t_inv") or {}).get("note"),
                           "measurements": [{k: m[k] for k in ("id", "what", "cost_label", "needs_approval", "strength", "blind_spot", "expected")}
                                            for m in a["measurements"]]} for a in r.get("assessments") or []]
    return out
