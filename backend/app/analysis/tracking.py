"""Command tracking: does the machine still do what its controller commands?

A TRACKING PAIR is a commanded signal and the measured signal that should follow it, matched per row scope
(an axis or a joint). Pairs in tracking-rules-v2:
- attitude: control.attitude_setpoint vs vehicle.attitude (unchanged from v1; aircraft);
- joint position: joint.position_command vs joint.position (legs, arms);
- body velocity: control.velocity_setpoint vs vehicle.velocity, and turn rate: control.yaw_rate_setpoint vs
  vehicle.yaw_rate (ground robots).
The tracking error |commanded - measured| is computed on distinct updates only (repeated frames are dropped),
wrap-aware for attitude. Errors are grouped by command size, because a hard manoeuvre tracks worse than a
gentle one on a healthy machine.

Baseline, in order of preference: the machine's known-good runs (earlier cases a person declared
known-good), else the declared reference window of this case. A DEPARTURE is reported when the rolling
median (ROLL_S) of error / baseline statistic for that command size stays at or above the pair's ratio for at
least MIN_DEPARTURE_S, and the error itself is at least the pair's minimum. The baseline statistic is the
median (attitude, velocity) or the p95 (joint position: a leg under a compliant position controller always
lags its target by a large, gait-dependent amount, so the typical error is not the right yardstick).

Rules are fixed constants, versioned. A departure says the machine did not follow its commands. Why
(actuator, drive, load, terrain, wind) is not established here.
"""
from __future__ import annotations

import bisect
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.analysis import series as S

TRACKING_RULES_VERSION = "tracking-rules-v3"  # v2: tracking pairs (joints, body velocity, turn rate); v3: velocity/turn-rate command sizes the baseline never saw use the nearest size it saw. Attitude rule unchanged from v1
# The v1 attitude pair; kept as module names because other modules and docs refer to them.
COMMAND = "control.attitude_setpoint"
MEASURED = "vehicle.attitude"
BINS = {"roll": (0.0, 10.0, 30.0, 1e9), "pitch": (0.0, 5.0, 15.0, 1e9), "yaw": (0.0, 1e9)}
PAIRS = (
    {"id": "attitude", "command": COMMAND, "measured": MEASURED, "bins": BINS, "angle": True,
     "statistic": "median", "ratio": 4.0, "min_error": 5.0, "unit": "°", "label": "{scope}"},
    {"id": "joint_position", "command": "joint.position_command", "measured": "joint.position", "bins": {}, "angle": False,
     "statistic": "p95", "ratio": 1.0, "min_error": 5.0, "unit": "°", "label": "{scope}"},
    {"id": "body_velocity", "command": "control.velocity_setpoint", "measured": "vehicle.velocity", "bins": {"*": (0.0, 0.3, 0.7, 1e9)}, "angle": False,
     "statistic": "median", "ratio": 4.0, "min_error": 0.2, "unit": " m/s", "label": "body velocity {scope}", "borrow_bins": True},
    {"id": "turn_rate", "command": "control.yaw_rate_setpoint", "measured": "vehicle.yaw_rate", "bins": {"*": (0.0, 10.0, 30.0, 1e9)}, "angle": False,
     "statistic": "median", "ratio": 4.0, "min_error": 10.0, "unit": "°/s", "label": "turn rate", "borrow_bins": True},
)
PAIR_TOLERANCE_S = 0.05
ROLL_S = 3.0
RATIO = 4.0            # attitude pair (v1 constant)
MIN_DEPARTURE_S = 2.0
MIN_ERROR_DEG = 5.0    # attitude pair (v1 constant)
MIN_BIN_SAMPLES = 5


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _median(v: Sequence[float]) -> float:
    o = sorted(v)
    n = len(o)
    return o[n // 2] if n % 2 else (o[n // 2 - 1] + o[n // 2]) / 2.0


def _p95(v: Sequence[float]) -> float:
    o = sorted(v)
    return o[int(0.95 * (len(o) - 1))]


def _bin(axis: str, command: float, bins: Optional[Dict[str, Tuple[float, ...]]] = None) -> str:
    bins = BINS if bins is None else bins
    edges = bins.get(axis) or bins.get("*") or (0.0, 1e9)
    c = abs(command)
    for lo, hi in zip(edges, edges[1:]):
        if lo <= c < hi:
            return f"{lo:g}-{hi:g}" if hi < 1e9 else f">={lo:g}"
    return "other"


def _labels(axis: str, bins: Optional[Dict[str, Tuple[float, ...]]]) -> List[str]:
    edges = (BINS if bins is None else bins).get(axis) or (bins or {}).get("*") or (0.0, 1e9)
    return [f"{lo:g}-{hi:g}" if hi < 1e9 else f">={lo:g}" for lo, hi in zip(edges, edges[1:])]


def _borrow(base: Dict[str, Any], axis: str, bins: Optional[Dict[str, Tuple[float, ...]]]) -> Dict[str, str]:
    """For command sizes the baseline never saw, the nearest command size it did see (ties: the larger one,
    whose normal error is larger, so a departure is never easier to call)."""
    labels = _labels(axis, bins)
    have = [i for i, b in enumerate(labels) if b in base]
    out = {}
    for i, b in enumerate(labels):
        if b not in base and have:
            out[b] = labels[min(have, key=lambda j: (abs(j - i), -j))]
    return out


def _pair_series(rows: Iterable[Dict[str, Any]], pair: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """scope -> {"items": [(t, commanded, |error|, source row ref)], "domain": measured row domain} on distinct updates."""
    cmd: Dict[str, List[Tuple[float, float, Dict[str, Any]]]] = {}
    meas: Dict[str, List[Tuple[float, float]]] = {}
    domain: Dict[str, Any] = {}
    for r in rows:
        if not isinstance(r.get("value"), (int, float)):
            continue
        if r.get("signal") == pair["command"]:
            cmd.setdefault(r.get("scope") or "", []).append((r["t"], r["value"], {"import_id": r.get("import_id"), "row": r.get("row")}))
        elif r.get("signal") == pair["measured"]:
            meas.setdefault(r.get("scope") or "", []).append((r["t"], r["value"]))
            domain.setdefault(r.get("scope") or "", r.get("domain"))
    out: Dict[str, Dict[str, Any]] = {}
    for scope in sorted(set(cmd) & set(meas)):
        c = sorted(cmd[scope], key=lambda x: x[0])
        m = sorted(meas[scope])
        mt = [t for t, _ in m]
        series, last = [], None
        for t, cv, ref in c:
            k = bisect.bisect_left(mt, t)
            best = min((j for j in (k - 1, k) if 0 <= j < len(m)), key=lambda j: abs(mt[j] - t), default=None)
            if best is None or abs(mt[best] - t) > PAIR_TOLERANCE_S:
                continue
            mv = m[best][1]
            if (cv, mv) == last:
                continue  # repeated frame, not a new update
            last = (cv, mv)
            series.append((t, cv, abs(S.angle_diff(mv, cv)) if pair["angle"] else abs(mv - cv), ref))
        if series:
            out[scope] = {"items": series, "domain": domain.get(scope)}
    return out


def pairs(rows: Iterable[Dict[str, Any]]) -> Dict[str, List[Tuple[float, float, float, Dict[str, Any]]]]:
    """Attitude axis -> [(t, commanded, |error|, source row ref)] (the v1 pair; kept for callers and tests)."""
    return {axis: v["items"] for axis, v in _pair_series(list(rows), PAIRS[0]).items()}


def _baseline(per_axis: List[Tuple[float, float, float, Dict[str, Any]]], axis: str, bins: Optional[Dict[str, Tuple[float, ...]]] = None) -> Dict[str, Dict[str, Any]]:
    groups: Dict[str, List[float]] = {}
    for _, c, e, _ in per_axis:
        groups.setdefault(_bin(axis, c, bins), []).append(e)
    return {b: {"n": len(v), "median": round(_median(v), 2), "p95": round(_p95(v), 2)} for b, v in groups.items() if len(v) >= MIN_BIN_SAMPLES}


def _departure(scored: List[Tuple[float, float, float, Dict[str, Any]]], ratio: float, min_error: float) -> Optional[Dict[str, Any]]:
    k = None
    times = [x[0] for x in scored]
    for i, (t, _, _, _) in enumerate(scored):
        lo = bisect.bisect_left(times, t - ROLL_S)
        window_ratios = [x[1] for x in scored[lo:i + 1]]
        window_err = [x[2] for x in scored[lo:i + 1]]
        high = _median(window_ratios) >= ratio and _median(window_err) >= min_error
        if high:
            k = i if k is None else k
            if scored[i][0] - scored[k][0] >= MIN_DEPARTURE_S:
                # The rolling median confirms late; the departure began at the first sample of the
                # unbroken run of individually high errors that leads into the confirmed stretch.
                b = k
                while b > 0 and scored[b - 1][1] >= ratio and scored[b - 1][2] >= min_error:
                    b -= 1
                while b < k and not (scored[b][1] >= ratio and scored[b][2] >= min_error):
                    b += 1
                first = scored[b]
                peak = max(scored[b:i + 1], key=lambda x: x[1])
                return {"start": _iso(first[0]), "t_start": first[0], "ratio_at_start": round(first[1], 1), "peak_ratio": round(peak[1], 1),
                        "peak_error": peak[2], "rows": [x[3] for x in scored[b:b + 3]]}
        else:
            k = None
    return None


def detect(rows: Iterable[Dict[str, Any]], window: Optional[Tuple[float, float]], reference: Optional[Tuple[float, float]],
           baseline_runs: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
    rows = list(rows)
    axes, findings, present = [], [], []
    for pair in PAIRS:
        case_pairs = _pair_series(rows, pair)
        if not case_pairs:
            continue
        present.append(pair["id"])
        base_pairs: Dict[str, List] = {}
        for run in baseline_runs or []:
            for scope, v in _pair_series(run["rows"], pair).items():
                base_pairs.setdefault(scope, []).extend(v["items"])
        bins, stat = pair["bins"], pair["statistic"]
        for scope, v in case_pairs.items():
            items = v["items"]
            axis = pair["label"].format(scope=scope)
            meta = {"axis": axis, "pair": pair["id"], "scope": scope, "measured": pair["measured"], "domain": v["domain"], "unit": pair["unit"].strip()}
            if base_pairs.get(scope):
                base, source = _baseline(base_pairs[scope], scope, bins), f"{len(baseline_runs)} known-good run(s) of this machine"
            elif reference:
                base, source = _baseline([p for p in items if reference[0] <= p[0] <= reference[1]], scope, bins), "declared reference window of this case"
            else:
                axes.append({**meta, "status": "no_baseline", "reason": "No known-good runs and no declared reference window"})
                continue
            if not base:
                axes.append({**meta, "status": "no_baseline", "reason": "Baseline has too few samples per command size"})
                continue
            start = reference[1] if reference else items[0][0]
            borrowed = _borrow(base, scope, bins) if pair.get("borrow_bins") else {}
            yard = {**{b: base[f] for b, f in borrowed.items()}, **base}
            floor = 1.0 if pair["min_error"] >= 1.0 else pair["min_error"] / 4.0
            scored = [(t, e / max(yard[_bin(scope, c, bins)][stat], floor), e, ref)
                      for t, c, e, ref in items if t > start and _bin(scope, c, bins) in yard]
            unscored = sorted({_bin(scope, c, bins) for t, c, _, _ in items if t > start} - set(yard))
            departure = _departure(scored, pair["ratio"], pair["min_error"])
            if departure:
                degrees = pair["unit"].strip() == "°"
                departure["peak_error"] = round(departure["peak_error"], 1 if degrees else 2)
                if degrees:
                    departure["peak_error_deg"] = departure["peak_error"]
            in_window = [p for p in items if window and window[0] <= p[0] <= window[1]]
            entry = {**meta, "status": "departed" if departure else "tracking_within_baseline", "baseline_source": source, "baseline": base,
                     "baseline_statistic": stat, "symptom_window": _baseline(in_window, scope, bins) if in_window else {}, "departure": departure, "updates": len(items),
                     "borrowed_bins": borrowed, "unscored_bins": unscored}
            axes.append(entry)
            if departure:
                # Size of the problem: the same statistic in the symptom window vs the baseline, same command size.
                # (ratio_at_start is only the value at the moment the rule fired, so it sits near the threshold.)
                win = entry["symptom_window"]
                shared = [b for b in win if b in base and base[b][stat]]
                unit = pair["unit"]
                if shared:
                    b = max(shared, key=lambda k: win[k]["n"])
                    w, n0 = win[b][stat], base[b][stat]
                    departure["window_ratio"] = round(w / n0, 1)
                    what = (f"tracking error {'p95' if stat == 'p95' else 'median'} {w:g}{unit} during the problem vs {n0:g}{unit} normally "
                            f"({w / n0:.1f}×, same command size; baseline: {source}; peak {departure['peak_error']:g}{unit})")
                else:
                    what = f"tracking error peaked at {departure['peak_error']:g}{unit} (baseline: {source})"
                findings.append({"rule": "DEPARTURE", "axis": axis, "pair": pair["id"], "measured": pair["measured"], "domain": v["domain"],
                                 "start": departure["start"], "t_start": departure["t_start"], "baseline_source": source,
                                 "text": f"{axis} stopped following its commands from {departure['start']}: {what}"})
    if not present:
        return {"status": "not_applicable", "reason": "No commanded/measured pair is recorded (" + "; ".join(f"{p['command']} vs {p['measured']}" for p in PAIRS) + ")", "axes": []}
    findings.sort(key=lambda f: f["t_start"])
    return {"status": "computed", "rules_version": TRACKING_RULES_VERSION, "pairs": present,
            "rule": "; ".join(f"{p['id']}: rolling {ROLL_S:g} s median of error ÷ baseline {p['statistic']} (same command size) ≥ {p['ratio']:g} and error ≥ {p['min_error']:g}{p['unit']} for ≥ {MIN_DEPARTURE_S:g} s"
                              for p in PAIRS if p["id"] in present),
            "axes": axes, "findings": findings, "first": findings[0] if findings else None,
            "boundary": "Whether the machine followed its commands, as recorded. The cause of a departure is not established here."}
