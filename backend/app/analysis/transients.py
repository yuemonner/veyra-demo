"""Brief-event (transient) detector: every sample, not a plot.

A transient here is a run of at most MAX_EVENT_SAMPLES consecutive samples whose value departs from
the local median of its neighbours by more than K robust units. Robust units are 1.4826 × the median
absolute residual over the reference samples (declared known-good window when given, else samples
outside the symptom window, else all samples). Longer departures, and departures that do not return to the
surrounding level (edges of a level change), are counted but not reported as brief events; they are left
to screening and the reference comparison.

Only signals sampled at MIN_RATE_HZ or faster are scanned; slower signals cannot show sub-second events.
v2: a stream that mostly repeats its previous value (sample-and-hold logging) is judged on its distinct
updates, so its rate is the real sensor rate and no event is shorter than one real update. Angles that
wrap at ±180° are unwrapped first, so a turn through 180° is not an event.
Each event reports how wide it would be on a plot of the whole recording, because events far below
one pixel are the ones people miss in RViz/Foxglove-style views.
An event is an observation on one measured signal. It is never a cause.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.analysis import series as S
from app.ingest.catalog import COMMANDED_SIGNALS as COMMANDED

TRANSIENT_RULES_VERSION = "transient-rules-v3"  # v3: commanded values are not scanned; v2: sample-and-hold streams judged on distinct updates; wrapping angles unwrapped
K_ROBUST = 8.0
NEIGHBOURS = 2            # local median over ±2 neighbours (second pass skips flagged samples)
MAX_EVENT_SAMPLES = 4     # longer departures are sustained, not brief
MIN_RATE_HZ = 5.0
MIN_SAMPLES = 50
MIN_REFERENCE_SAMPLES = 50
PLOT_WIDTH_PX = 1600      # nominal plot width used for the visibility estimate
CONTEXT_SAMPLES = 25
SIDE_SAMPLES = 5           # samples either side used to confirm the signal returns to its level      # samples kept either side of an event for display
MAX_EVENTS_PER_SIGNAL = 20


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


def _inside(t: float, window: Optional[Tuple[float, float]]) -> bool:
    return window is not None and window[0] <= t <= window[1]


def detect(rows: Iterable[Dict[str, Any]], window: Optional[Tuple[float, float]] = None,
           reference: Optional[Tuple[float, float]] = None) -> Dict[str, Any]:
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for row in rows:
        if row.get("kind") == "measurement" and isinstance(row.get("value"), (int, float)):
            groups.setdefault((row["signal"], row.get("scope"), row.get("unit"), row.get("clock_domain")), []).append(row)

    per_signal: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for (signal, scope, unit, clock), items in sorted(groups.items(), key=lambda kv: tuple(str(k) for k in kv[0])):
        items.sort(key=lambda r: r["t"])
        label = {"signal": signal, "scope": scope, "unit": unit, "clock_domain": clock}
        if signal in COMMANDED:
            skipped.append({**label, "reason": "Commanded value: a step is a command, not a brief event"})
            continue
        repeat_share = S.repeated_share([r["value"] for r in items])
        held = repeat_share >= S.REPEAT_SHARE_HOLD
        if held:  # sample-and-hold stream: keep only the rows where the value actually changed
            items = [r for k, r in enumerate(items) if k == 0 or r["value"] != items[k - 1]["value"]]
        n = len(items)
        label["repeated_sample_share"] = round(repeat_share, 3)
        if n < MIN_SAMPLES:
            skipped.append({**label, "reason": f"{n} samples; at least {MIN_SAMPLES} needed"})
            continue
        intervals = [b["t"] - a["t"] for a, b in zip(items, items[1:]) if b["t"] > a["t"]]
        if not intervals:
            skipped.append({**label, "reason": "No time progression between samples"})
            continue
        interval = _median(intervals)
        rate = 1.0 / interval if interval > 0 else 0.0
        if rate < MIN_RATE_HZ:
            why = (f"Only {rate:.2g} real updates per second ({repeat_share:.0%} of rows repeat the previous value)" if held
                   else f"Sampled at {rate:.2g} Hz")
            skipped.append({**label, "reason": f"{why}; brief events need at least {MIN_RATE_HZ:g} Hz"})
            continue

        values = [r["value"] for r in items]
        wrapped = S.wraps(values, unit)
        if wrapped:
            values = S.unwrap(values)
            label["unwrapped_angle"] = True
        residuals = []
        for i in range(n):
            if i < NEIGHBOURS or i >= n - NEIGHBOURS:
                residuals.append(0.0)  # edge samples lack neighbours on one side; not evaluated
                continue
            neighbours = values[i - NEIGHBOURS:i] + values[i + 1:i + NEIGHBOURS + 1]
            residuals.append(values[i] - _median(neighbours))

        evaluated = range(NEIGHBOURS, n - NEIGHBOURS)
        if reference and sum(1 for r in items if _inside(r["t"], reference)) >= MIN_REFERENCE_SAMPLES:
            ref_idx = [i for i in evaluated if _inside(items[i]["t"], reference)]
            scale_source = "declared reference window"
        elif window and sum(1 for r in items if not _inside(r["t"], window)) >= MIN_REFERENCE_SAMPLES:
            ref_idx = [i for i in evaluated if not _inside(items[i]["t"], window)]
            scale_source = "samples outside the symptom window"
        else:
            ref_idx = list(evaluated)
            scale_source = "all samples (no reference available)"
        scale = 1.4826 * _median([abs(residuals[i]) for i in ref_idx])
        floor = 1e-9 + 1e-6 * max(abs(v) for v in values)
        scale = max(scale, floor)

        first = [abs(residuals[i]) > K_ROBUST * scale for i in range(n)]
        # Second pass: re-evaluate samples near a first-pass flag against the nearest unflagged
        # neighbours, so a multi-sample event does not drag its normal neighbours in with it.
        flagged = list(first)
        reach = NEIGHBOURS + MAX_EVENT_SAMPLES
        near = {k for i in range(n) if first[i] for k in range(max(NEIGHBOURS, i - NEIGHBOURS), min(n - NEIGHBOURS, i + NEIGHBOURS + 1))}
        for i in sorted(near):
            left = [values[k] for k in range(i - 1, max(-1, i - reach - 1), -1) if not first[k]][:NEIGHBOURS]
            right = [values[k] for k in range(i + 1, min(n, i + reach + 1)) if not first[k]][:NEIGHBOURS]
            if len(left) < NEIGHBOURS or len(right) < NEIGHBOURS:
                flagged[i] = False
                continue
            residuals[i] = values[i] - _median(left + right)
            flagged[i] = abs(residuals[i]) > K_ROBUST * scale
        events: List[Dict[str, Any]] = []
        sustained = 0
        level_changes = 0
        i = 0
        span = items[-1]["t"] - items[0]["t"]
        while i < n:
            if not flagged[i]:
                i += 1
                continue
            j = i
            while j + 1 < n and flagged[j + 1] and items[j + 1]["t"] - items[j]["t"] <= 1.5 * interval:
                j += 1
            count = j - i + 1
            peak_index = max(range(i, j + 1), key=lambda k: abs(residuals[k]))
            before = values[max(0, i - SIDE_SAMPLES):i]
            after = values[j + 1:j + 1 + SIDE_SAMPLES]
            # A brief event departs from the level on BOTH sides in the same direction; otherwise it is an edge of a level change.
            returns = bool(before and after) and all(
                (values[peak_index] - _median(side)) * residuals[peak_index] > 0 and abs(values[peak_index] - _median(side)) > K_ROBUST * scale
                for side in (before, after))
            if count > MAX_EVENT_SAMPLES:
                sustained += 1
            elif not returns:
                level_changes += 1
            elif len(events) < MAX_EVENTS_PER_SIGNAL:
                level = values[peak_index] - residuals[peak_index]
                duration_s = (items[j + 1]["t"] - items[i]["t"]) if j + 1 < n else count * interval
                lo, hi = max(0, i - CONTEXT_SAMPLES), min(n, j + CONTEXT_SAMPLES + 1)
                events.append({
                    "start": _iso(items[i]["t"]), "end": _iso(items[j]["t"]), "t_start": items[i]["t"], "t_end": items[j]["t"],
                    "samples": count, "duration_ms": round(duration_s * 1000, 1),
                    "peak_value": items[peak_index]["value"], "local_level": round(level, 6),
                    "departure_robust_units": round(abs(residuals[peak_index]) / scale, 1),
                    "direction": "up" if residuals[peak_index] > 0 else "down",
                    "in_symptom_window": _inside(items[i]["t"], window),
                    "plot_width_px": round(duration_s / span * PLOT_WIDTH_PX, 3) if span > 0 else None,
                    "rows": [{"import_id": items[k].get("import_id"), "row": items[k].get("row")} for k in range(i, j + 1)],
                    "context": [{"t": items[k]["t"], "v": values[k]} for k in range(lo, hi)],
                })
            i = j + 1
        per_signal.append({**label, "domain": items[0].get("domain"), "rate_hz": round(rate, 3), "samples": n, "robust_unit": scale, "scale_source": scale_source,
                           "events": events, "sustained_departures": sustained, "level_changes": level_changes,
                           "imports": sorted({r.get("import_id") for r in items if r.get("import_id")})})

    all_events = [e for s in per_signal for e in s["events"]]
    return {
        "rules_version": TRANSIENT_RULES_VERSION,
        "rule": f"|value − median of ±{NEIGHBOURS} neighbours| > {K_ROBUST:g} robust units, for at most {MAX_EVENT_SAMPLES} consecutive samples (first and last {NEIGHBOURS} samples not evaluated); "
                f"signals sampled at ≥{MIN_RATE_HZ:g} Hz with ≥{MIN_SAMPLES} samples",
        "plot_width_px_basis": f"Width on a {PLOT_WIDTH_PX} px plot of the whole recording for that signal",
        "signals": per_signal, "skipped": skipped,
        "events_total": len(all_events), "events_in_window": sum(1 for e in all_events if e["in_symptom_window"]),
        "boundary": "Brief departures on measured signals. Each is an observation, not a cause; sensor glitches and real events look alike here.",
    }
