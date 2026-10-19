"""Deterministic screening over normalized rows: threshold crossings, heartbeat gaps, events in a window.

Every threshold carries its provenance. A crossing is an observation on a named measurement path,
never a diagnosis.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

SCREENING_RULES_VERSION = "screening-rules-v1"
# Illustrative pilot screening rules. Replace with customer-agreed values per machine/profile.
THRESHOLDS = {
    "network.rtt": {"above": 200.0, "unit": "ms", "provenance": "Illustrative pilot screening rule; not an OEM or BuilderX limit"},
    "network.packet_loss": {"above": 5.0, "unit": "%", "provenance": "Illustrative pilot screening rule; not an OEM or BuilderX limit"},
    "network.jitter": {"above": 50.0, "unit": "ms", "provenance": "Illustrative pilot screening rule; not an OEM or BuilderX limit"},
    "controller.cycle_time": {"above": 50.0, "unit": "ms", "provenance": "Illustrative pilot screening rule; not an OEM or BuilderX limit"},
}
HEARTBEAT_GAP_FACTOR = 3.0  # gap > factor × median interval
EVENT_SIGNALS = ("controller.reset", "controller.event", "fault.code", "network.link_event")


def _stats(values: Sequence[float]) -> Dict[str, Any]:
    ordered = sorted(values)
    n = len(ordered)
    if not n:
        return {"count": 0}
    def q(p: float) -> float:
        position = (n - 1) * p
        lower = int(position)
        upper = min(lower + 1, n - 1)
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return {"count": n, "min": ordered[0], "median": q(0.5), "p95": q(0.95), "max": ordered[-1], "mean": sum(ordered) / n}


def in_window(t: float, window: Optional[Sequence[float]]) -> bool:
    return window is None or window[0] <= t <= window[1]


def measurements(rows: Iterable[Dict[str, Any]], window: Optional[Sequence[float]], overrides: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Per (signal, scope, unit, clock) statistics, in and out of the window, with threshold crossings."""
    thresholds = {**THRESHOLDS, **(overrides or {})}
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for row in rows:
        if row.get("kind") == "measurement" and isinstance(row.get("value"), (int, float)):
            groups.setdefault((row["signal"], row.get("scope"), row.get("unit"), row.get("clock_domain")), []).append(row)
    findings = []
    for (signal, scope, unit, clock), items in sorted(groups.items(), key=lambda kv: tuple(str(k) for k in kv[0])):
        items.sort(key=lambda r: r["t"])
        inside = [r for r in items if in_window(r["t"], window)]
        outside = [r for r in items if not in_window(r["t"], window)] if window else []
        rule = thresholds.get(signal)
        crossings = []
        if rule and rule["unit"] == unit:
            current = None
            for row in items:
                if row["value"] > rule["above"]:
                    if current is None or row["t"] - current["end"] > 30:
                        current = {"start": row["t"], "end": row["t"], "count": 0, "peak": row["value"], "rows": []}
                        crossings.append(current)
                    current["end"] = row["t"]
                    current["count"] += 1
                    current["peak"] = max(current["peak"], row["value"])
                    if len(current["rows"]) < 20:
                        current["rows"].append({"import_id": row.get("import_id"), "row": row.get("row")})
                else:
                    current = None
        findings.append({
            "kind": "measurement_screening", "signal": signal, "scope": scope, "unit": unit, "clock_domain": clock,
            "domain": items[0].get("domain"), "in_window": _stats([r["value"] for r in inside]),
            "outside_window": _stats([r["value"] for r in outside]) if window else None,
            "threshold": {**rule, "rules_version": SCREENING_RULES_VERSION} if rule and rule["unit"] == unit else None,
            "crossings": crossings, "crossing_in_window": any(c["start"] <= window[1] and c["end"] >= window[0] for c in crossings) if window else bool(crossings),
            "imports": sorted({r.get("import_id") for r in items if r.get("import_id")}),
            "observation": ("Values above the screening rule on this measured path." if crossings else "No screening-rule crossing on this measured path.")
                           + (" The path is named by the source; whether it is the command path needs confirmation." if signal.startswith("network.") else ""),
        })
    return findings


def heartbeat_gaps(rows: Iterable[Dict[str, Any]], window: Optional[Sequence[float]]) -> List[Dict[str, Any]]:
    by_clock: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        if row.get("signal") == "app.heartbeat":
            by_clock.setdefault(row.get("clock_domain"), []).append(row)
    out = []
    for clock, items in sorted(by_clock.items(), key=lambda kv: str(kv[0])):
        times = sorted(r["t"] for r in items)
        intervals = [b - a for a, b in zip(times, times[1:]) if b > a]
        if len(intervals) < 3:
            out.append({"kind": "heartbeat_gaps", "clock_domain": clock, "status": "unavailable", "reason": "Fewer than four heartbeats"})
            continue
        median = sorted(intervals)[len(intervals) // 2]
        limit = HEARTBEAT_GAP_FACTOR * median
        gaps = [{"start": a, "end": b, "duration_s": b - a} for a, b in zip(times, times[1:]) if b - a > limit]
        out.append({"kind": "heartbeat_gaps", "clock_domain": clock, "status": "computed", "median_interval_s": median,
                    "rule": f"gap > {HEARTBEAT_GAP_FACTOR:g} × median interval ({limit:.2f} s)", "gaps": gaps,
                    "gap_in_window": any(g["start"] <= window[1] and g["end"] >= window[0] for g in gaps) if window else bool(gaps),
                    "observation": "Heartbeat gaps show when the application did not report liveness; they do not show why."})
    return out


def events(rows: Iterable[Dict[str, Any]], window: Optional[Sequence[float]], margin_s: float = 60.0) -> Dict[str, Any]:
    """Controller resets/events, fault codes and link events within the window (± margin), listed verbatim."""
    selected = []
    for row in rows:
        if row.get("signal") in EVENT_SIGNALS:
            near = window is None or window[0] - margin_s <= row["t"] <= window[1] + margin_s
            if near:
                selected.append({"t": row["t"], "time_utc": row.get("time_utc"), "signal": row["signal"], "event_value": row.get("event_value"),
                                 "code": row.get("code"), "code_description": row.get("code_description"), "code_source": row.get("code_source"),
                                 "domain": row.get("domain"), "clock_domain": row.get("clock_domain"), "import_id": row.get("import_id"), "row": row.get("row")})
    selected.sort(key=lambda e: e["t"])
    return {"kind": "events_near_window", "margin_s": margin_s, "events": selected,
            "observation": "Events are listed as recorded. Co-occurrence with the symptom is not causation; clocks may differ."}


def coverage(rows: Iterable[Dict[str, Any]], window: Optional[Sequence[float]]) -> List[Dict[str, Any]]:
    by_signal: Dict[tuple, List[float]] = {}
    for row in rows:
        by_signal.setdefault((row.get("signal") or "unmapped:" + str(row.get("source_signal")), row.get("clock_domain")), []).append(row["t"])
    out = []
    for (signal, clock), times in sorted(by_signal.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        times.sort()
        covers = bool(window) and times[0] <= window[0] and times[-1] >= window[1]
        out.append({"signal": signal, "clock_domain": clock, "count": len(times), "start": times[0], "end": times[-1],
                    "rows_in_window": sum(1 for t in times if in_window(t, window)), "spans_window": covers if window else None})
    return out
