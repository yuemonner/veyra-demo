"""Command-chain stage latency: where did time elapse for the same command?

Pairs consecutive stages of one command_id (sent -> received -> output -> feedback). A latency is
computed only when both stages share a clock domain, or when every clock involved has a declared
maximum skew, in which case the result carries that uncertainty. Otherwise it is unavailable.

This localizes elapsed time along matched records. It does not identify a cause, and stages
with no records keep their fault domains open.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

STAGES = ("command.sent", "command.received", "command.output", "vehicle.feedback")
SEGMENTS = (
    ("command.sent", "command.received", "network"),
    ("command.received", "command.output", "control_suite"),
    ("command.output", "vehicle.feedback", "vehicle_interface_or_body"),
)
TOOL_VERSION = "command-chain-latency-v1"
MAX_COMMANDS_LISTED = 500

COMPUTED = "computed"
COMPUTED_WITH_CLOCK_UNCERTAINTY = "computed_with_clock_uncertainty"
UNAVAILABLE_CROSS_CLOCK = "unavailable_cross_clock_unknown_skew"
STAGE_MISSING = "stage_missing"
AMBIGUOUS = "ambiguous_duplicate_stage"
INCONSISTENT = "inconsistent_order_same_clock"


def _percentile(sorted_values: List[float], q: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (position - lower)


def _segment(first: List[Dict[str, Any]], second: List[Dict[str, Any]], skew: Mapping[str, Optional[float]]) -> Dict[str, Any]:
    if not first or not second:
        return {"status": STAGE_MISSING}
    if len(first) > 1 or len(second) > 1:
        return {"status": AMBIGUOUS, "records": len(first) + len(second)}
    a, b = first[0], second[0]
    evidence = {"from_row": a.get("row"), "to_row": b.get("row"), "from_clock": a["clock_domain"], "to_clock": b["clock_domain"],
                "from_import": a.get("import_id"), "to_import": b.get("import_id")}
    elapsed_ms = (b["t"] - a["t"]) * 1000.0
    if a["clock_domain"] == b["clock_domain"]:
        if elapsed_ms < 0:
            return {"status": INCONSISTENT, "latency_ms": elapsed_ms, **evidence}
        return {"status": COMPUTED, "latency_ms": elapsed_ms, "uncertainty_ms": 0.0, **evidence}
    # Different clocks: the raw timestamp difference is kept (it is NOT a latency) for relative comparisons,
    # in which a constant offset cancels.
    evidence["raw_clock_difference_ms"] = elapsed_ms
    skew_a, skew_b = skew.get(a["clock_domain"]), skew.get(b["clock_domain"])
    if skew_a is None or skew_b is None:
        return {"status": UNAVAILABLE_CROSS_CLOCK, **evidence}
    return {"status": COMPUTED_WITH_CLOCK_UNCERTAINTY, "latency_ms": elapsed_ms, "uncertainty_ms": (skew_a + skew_b) * 1000.0, **evidence}


def stage_latencies(rows: Iterable[Dict[str, Any]], declared_skew_s: Mapping[str, Optional[float]],
                    window: Optional[Tuple[float, float, str]] = None) -> Dict[str, Any]:
    """rows: normalized ingestion rows (any imports). declared_skew_s: clock_domain -> max skew in seconds or None.
    window: optional (start_t, end_t, clock_domain) selecting commands whose earliest stage in that clock falls inside."""
    by_command: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for row in rows:
        if row.get("signal") in STAGES and row.get("command_id"):
            by_command.setdefault(row["command_id"], {}).setdefault(row["signal"], []).append(row)

    commands = []
    for command_id in sorted(by_command):
        stages = by_command[command_id]
        if window is not None:
            start, end, clock = window
            in_clock = [r["t"] for records in stages.values() for r in records if r["clock_domain"] == clock]
            if not in_clock or not start <= min(in_clock) <= end:
                continue
        segments = {}
        for first, second, domain in SEGMENTS:
            segments[f"{first}->{second}"] = {"domain": domain, **_segment(stages.get(first, []), stages.get(second, []), declared_skew_s)}
        commands.append({"command_id": command_id, "stages_present": [s for s in STAGES if s in stages], "segments": segments})

    summary = {}
    for first, second, domain in SEGMENTS:
        key = f"{first}->{second}"
        results = [c["segments"][key] for c in commands]
        exact = sorted(r["latency_ms"] for r in results if r["status"] == COMPUTED)
        uncertain = [r for r in results if r["status"] == COMPUTED_WITH_CLOCK_UNCERTAINTY]
        counts: Dict[str, int] = {}
        for r in results:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        # latencies_ms / raw_ms: every value, for the per-command test; the caller drops them before storing the run.
        entry: Dict[str, Any] = {"domain": domain, "status_counts": counts, "computed_count": len(exact), "latencies_ms": exact}
        if exact:
            entry.update(median_ms=_percentile(exact, 0.5), p95_ms=_percentile(exact, 0.95), max_ms=exact[-1], min_ms=exact[0])
        if uncertain:
            values = sorted(r["latency_ms"] for r in uncertain)
            entry.update(uncertain_count=len(uncertain), uncertain_median_ms=_percentile(values, 0.5),
                         uncertainty_ms=max(r["uncertainty_ms"] for r in uncertain))
        raw = sorted(r["raw_clock_difference_ms"] for r in results if "raw_clock_difference_ms" in r)
        if raw:
            entry.update(raw_ms=raw, cross_clock_count=len(raw), cross_clock_raw_median_ms=_percentile(raw, 0.5), cross_clock_raw_p95_ms=_percentile(raw, 0.95),
                         cross_clock_note="Raw differences between clocks include an unknown offset; only changes between groups of commands are meaningful, assuming the offset is stable.")
        if not exact and not uncertain:
            entry["availability"] = "unavailable"
        summary[key] = entry

    return {
        "tool": TOOL_VERSION, "command_count": len(commands), "segments": summary,
        # command_ids is the full analysis set; `commands` is only a display list, capped. Never derive analysis from it.
        "command_ids": [c["command_id"] for c in commands],
        "commands": commands[:MAX_COMMANDS_LISTED], "truncated": len(commands) > MAX_COMMANDS_LISTED,
        "declared_skew_s": dict(declared_skew_s), "window": list(window) if window else None,
        "boundary": "Elapsed time between matched records of the same command. Not a cause. Cross-clock latency is "
                    "computed only with declared skew for every clock and carries that uncertainty; missing stages keep domains open.",
    }
