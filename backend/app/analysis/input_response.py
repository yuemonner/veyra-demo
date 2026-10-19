"""Input–Response Baseline (optional module): how a channel's measured response follows its input.

Model (delay–gain v1): response(t) ≈ gain · input(t − delay) + offset, fitted on a uniform grid
by scanning delays and choosing the highest correlation. It is a first step toward per-machine
system identification and runs only when the data supports it:

- input and response on the same channel and the same clock domain,
- enough overlapping, gap-free samples,
- sufficient input excitation (a constant input identifies nothing),
- a minimum correlation at the best delay (otherwise "no stable relation identified").

Comparisons between a baseline window and an incident window are explicit about thresholds and
about context comparability. A change in the model is an observation, not a cause.
"""
from __future__ import annotations

import math
from bisect import bisect_left
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

MODULE_VERSION = "input-response-delay-gain-v1"
INPUT_SIGNAL = "vehicle.command_input"
RESPONSE_SIGNAL = "vehicle.actuator_response"

DEFAULTS = {
    "min_points": 100,             # grid points with both series available
    "min_input_std": 5.0,          # % of full scale; below this the input does not excite the channel
    "min_input_reversals": 4,      # crossings of the input mean; guards against a single ramp
    "max_delay_s": 2.0,
    "min_correlation": 0.6,
    "max_gap_factor": 3.0,         # grid points inside larger source gaps are invalid
    # Comparison screening rules (pilot defaults, not OEM specifications)
    "delay_change_min_s": 0.05,
    "gain_ratio_band": (0.8, 1.25),
}
THRESHOLD_PROVENANCE = "Pilot screening defaults in input_response.DEFAULTS; not OEM specifications. Review per machine."


def _series(rows: Iterable[Dict[str, Any]], signal: str, channel: str, clock: Optional[str], start: float, end: float) -> List[Tuple[float, float]]:
    out = [(r["t"], float(r["value"])) for r in rows
           if r.get("signal") == signal and r.get("scope") == channel and isinstance(r.get("value"), (int, float))
           and (clock is None or r.get("clock_domain") == clock) and start <= r["t"] <= end]
    out.sort()
    return out


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def _interpolate(series: List[Tuple[float, float]], times: List[float], max_gap: float) -> List[Optional[float]]:
    xs = [t for t, _ in series]
    out: List[Optional[float]] = []
    for t in times:
        i = bisect_left(xs, t)
        if i < len(xs) and xs[i] == t:
            out.append(series[i][1])
            continue
        if i == 0 or i == len(xs):
            out.append(None)
            continue
        (t0, v0), (t1, v1) = series[i - 1], series[i]
        if t1 - t0 > max_gap:
            out.append(None)
            continue
        out.append(v0 + (v1 - v0) * (t - t0) / (t1 - t0))
    return out


def _correlation(xs: List[float], ys: List[float]) -> Tuple[float, float, float]:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx <= 0 or syy <= 0:
        return 0.0, 0.0, my
    gain = sxy / sxx
    return sxy / math.sqrt(sxx * syy), gain, my - gain * mx


def identify(rows: Iterable[Dict[str, Any]], channel: str, start: float, end: float, *, clock: Optional[str] = None,
             settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Identify the delay–gain model of one channel in [start, end]. Never raises for weak data."""
    cfg = {**DEFAULTS, **(settings or {})}
    rows = list(rows)
    window = {"start": start, "end": end, "channel": channel}
    clocks_in = {r.get("clock_domain") for r in rows if r.get("signal") == INPUT_SIGNAL and r.get("scope") == channel}
    clocks_out = {r.get("clock_domain") for r in rows if r.get("signal") == RESPONSE_SIGNAL and r.get("scope") == channel}

    def unavailable(reason: str, **extra) -> Dict[str, Any]:
        return {"module": MODULE_VERSION, "status": "unavailable", "reason": reason, "window": window, **extra}

    if not clocks_in or not clocks_out:
        return unavailable("Channel needs both vehicle.command_input and vehicle.actuator_response records.")
    if clock is None:
        shared = clocks_in & clocks_out
        if len(shared) != 1:
            return unavailable("Input and response must come from one shared clock domain; cross-clock identification is not attempted.",
                               input_clocks=sorted(c for c in clocks_in if c), response_clocks=sorted(c for c in clocks_out if c))
        clock = next(iter(shared))
    window["clock_domain"] = clock
    x = _series(rows, INPUT_SIGNAL, channel, clock, start, end)
    y = _series(rows, RESPONSE_SIGNAL, channel, clock, start, end)
    if len(x) < 3 or len(y) < 3:
        return unavailable("Too few input or response samples in this window.", input_samples=len(x), response_samples=len(y))

    dt = _median([b[0] - a[0] for a, b in zip(x, x[1:]) if b[0] > a[0]] or [0.0])
    if not 0.001 <= dt <= 1.0:
        return unavailable("Input sample interval outside 1 ms–1 s; resampling would be unreliable.", sample_interval_s=dt)
    lo, hi = max(x[0][0], y[0][0]), min(x[-1][0], y[-1][0])
    steps = int((hi - lo) / dt)
    max_lag = int(round(cfg["max_delay_s"] / dt))
    if steps - max_lag < cfg["min_points"]:
        return unavailable("Not enough overlapping coverage for identification.", overlap_s=max(0.0, hi - lo), sample_interval_s=dt)
    grid = [lo + i * dt for i in range(steps + 1)]
    xi = _interpolate(x, grid, cfg["max_gap_factor"] * dt)
    yi = _interpolate(y, grid, cfg["max_gap_factor"] * dt)

    valid_x = [v for v in xi if v is not None]
    mean_x = sum(valid_x) / len(valid_x)
    std_x = math.sqrt(sum((v - mean_x) ** 2 for v in valid_x) / len(valid_x))
    reversals = sum(1 for a, b in zip(valid_x, valid_x[1:]) if (a - mean_x) * (b - mean_x) < 0)
    excitation = {"input_std": std_x, "input_mean_crossings": reversals, "min_input_std": cfg["min_input_std"], "min_input_reversals": cfg["min_input_reversals"]}
    if std_x < cfg["min_input_std"] or reversals < cfg["min_input_reversals"]:
        return unavailable("Insufficient input excitation: the operator input does not vary enough to identify the response.", excitation=excitation)

    best = None
    scan = []
    for lag in range(0, max_lag + 1):
        pairs = [(xi[i - lag], yi[i]) for i in range(lag, len(grid)) if xi[i - lag] is not None and yi[i] is not None]
        if len(pairs) < cfg["min_points"]:
            continue
        corr, gain, offset = _correlation([p[0] for p in pairs], [p[1] for p in pairs])
        scan.append((lag * dt, corr))
        if best is None or corr > best[1]:
            best = (lag, corr, gain, offset, len(pairs))
    if best is None:
        return unavailable("Not enough valid paired samples at any delay.", excitation=excitation)
    lag, corr, gain, offset, n = best
    model = {"delay_s": lag * dt, "gain": gain, "offset": offset, "correlation": corr, "r_squared": corr * corr,
             "delay_resolution_s": dt, "paired_points": n, "units": {"input": "%", "response": "deg/s", "gain": "deg/s per %"}}
    at_boundary = lag == max_lag
    if corr < cfg["min_correlation"]:
        return {"module": MODULE_VERSION, "status": "no_stable_relation", "window": window, "excitation": excitation,
                "best_candidate": model, "reason": f"Best correlation {corr:.2f} is below {cfg['min_correlation']}; no input–response model is claimed."}
    return {"module": MODULE_VERSION, "status": "identified", "window": window, "excitation": excitation, "model": model,
            "delay_at_search_limit": at_boundary,
            "correlation_by_delay": [{"delay_s": d, "correlation": c} for d, c in scan[:: max(1, len(scan) // 40)]],
            "boundary": "Empirical delay–gain fit on this window only. Not a physical model and not a cause."}


def compare(baseline: Dict[str, Any], incident: Dict[str, Any], *, context_comparability: Optional[Dict[str, Any]] = None,
            settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Compare an incident-window model with a baseline-window model on the same channel."""
    cfg = {**DEFAULTS, **(settings or {})}
    result: Dict[str, Any] = {"module": MODULE_VERSION, "baseline": baseline, "incident": incident, "threshold_provenance": THRESHOLD_PROVENANCE,
                              "context_comparability": context_comparability}
    if baseline.get("status") != "identified" or incident.get("status") != "identified":
        result.update(status="unavailable", reason="Both windows need an identified model before comparing.")
        return result
    if context_comparability is not None and not context_comparability.get("comparable"):
        result.update(status="not_comparable", reason="Machine context is not confirmed comparable between the two windows; the models are shown but not compared.")
        return result
    b, i = baseline["model"], incident["model"]
    delay_change = i["delay_s"] - b["delay_s"]
    resolution = max(b["delay_resolution_s"], i["delay_resolution_s"])
    delay_rule = max(cfg["delay_change_min_s"], 2 * resolution)
    ratio = i["gain"] / b["gain"] if b["gain"] else None
    low, high = cfg["gain_ratio_band"]
    observations = []
    if abs(delay_change) > delay_rule:
        observations.append({"rule": "delay_change", "observation": "response_delay_increased" if delay_change > 0 else "response_delay_decreased",
                             "detail": f"Delay {b['delay_s']*1000:.0f} ms → {i['delay_s']*1000:.0f} ms (rule: |change| > {delay_rule*1000:.0f} ms)"})
    if ratio is not None and not low <= ratio <= high:
        observations.append({"rule": "gain_ratio", "observation": "response_gain_changed",
                             "detail": f"Gain ratio {ratio:.2f} outside {low}–{high}"})
    result.update(status="changed" if observations else "no_change_detected", delay_change_s=delay_change, gain_ratio=ratio,
                  rules={"delay_change_threshold_s": delay_rule, "gain_ratio_band": [low, high]}, observations=observations,
                  boundary="Compares empirical fits in two windows. No change detected means within these rules for this channel only; other channels and domains remain open.")
    return result


def channels(rows: Iterable[Dict[str, Any]]) -> List[str]:
    inputs = {r.get("scope") for r in rows if r.get("signal") == INPUT_SIGNAL}
    outputs = {r.get("scope") for r in rows if r.get("signal") == RESPONSE_SIGNAL}
    return sorted(c for c in inputs & outputs if c)
