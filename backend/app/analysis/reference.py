"""Reference comparison: how the suspect window departs from a known-good one, signal by signal.

Engineers get reliable answers when they can say "this run is known good, that one is not, find the
difference". This module resolves which reference a run uses and compares every measured signal
between the reference window and the symptom window with a stated rule. Without a declared
reference the comparison is labelled as resting on an assumption.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.analysis import series as S

REFERENCE_RULES_VERSION = "reference-rules-v2"  # v2: angles that wrap at ±180° are compared as offsets from the reference direction
MIN_SAMPLES = 5
DEFAULT_GUARD_S = 30.0
GRADES = {"explicit": "compared_to_declared_known_good", "declared": "compared_to_declared_known_good",
          "assumed_default": "compared_to_assumed_reference", "missing": "no_reference"}


def _q(ordered: Sequence[float], p: float) -> float:
    position = (len(ordered) - 1) * p
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _stats(values: List[float]) -> Dict[str, Any]:
    ordered = sorted(values)
    if not ordered:
        return {"count": 0}
    return {"count": len(ordered), "p05": _q(ordered, 0.05), "median": _q(ordered, 0.5), "p95": _q(ordered, 0.95), "max": ordered[-1], "min": ordered[0]}


def resolve(ledger: Sequence[Dict[str, Any]], window: Optional[Tuple[float, float]], explicit: Optional[Tuple[float, float]],
            first_t: Optional[float], to_t, to_iso) -> Dict[str, Any]:
    declared = [e for e in ledger if e["kind"] == "reference_declared"]
    if explicit:
        ref = {"status": "explicit", "window": explicit, "basis": "Baseline window given for this run", "by": None, "entry_id": None}
    elif declared:
        last = declared[-1]
        ref = {"status": "declared", "window": (to_t(last["payload"]["window_from"]), to_t(last["payload"]["window_to"])),
               "basis": last["payload"]["basis"], "source": last["payload"].get("source"), "by": last["actor"], "at": last["recorded_at"], "entry_id": last["id"]}
    elif window and first_t is not None and window[0] - DEFAULT_GUARD_S - first_t >= 60:
        ref = {"status": "assumed_default", "window": (first_t, window[0] - DEFAULT_GUARD_S),
               "basis": f"Assumed healthy: data before the symptom window (minus {DEFAULT_GUARD_S:g} s). Nobody has declared it known-good.", "by": None, "entry_id": None}
    else:
        ref = {"status": "missing", "window": None, "basis": "No reference available", "by": None, "entry_id": None}
    ref["evidence_grade"] = GRADES[ref["status"]]
    ref["window_iso"] = [to_iso(ref["window"][0]), to_iso(ref["window"][1])] if ref["window"] else None
    ref["rules_version"] = REFERENCE_RULES_VERSION
    return ref


def compare(rows: Iterable[Dict[str, Any]], reference: Optional[Tuple[float, float]], window: Optional[Tuple[float, float]]) -> Dict[str, Any]:
    if not reference or not window:
        return {"status": "unavailable", "reason": "Needs both a reference window and a symptom window", "signals": []}
    groups: Dict[tuple, Dict[str, List[float]]] = {}
    for row in rows:
        if row.get("kind") != "measurement" or not isinstance(row.get("value"), (int, float)):
            continue
        key = (row["signal"], row.get("scope"), row.get("unit"), row.get("clock_domain"), row.get("domain"))
        bucket = groups.setdefault(key, {"ref": [], "inc": []})
        if reference[0] <= row["t"] <= reference[1]:
            bucket["ref"].append(row["value"])
        elif window[0] <= row["t"] <= window[1]:
            bucket["inc"].append(row["value"])
    signals = []
    for (signal, scope, unit, clock, domain), bucket in sorted(groups.items(), key=lambda kv: tuple(str(k) for k in kv[0])):
        both = bucket["ref"] + bucket["inc"]
        circular = S.is_angle(unit) and len(both) > 1 and max(both) - min(both) > S.WRAP_SPAN_DEG
        if circular:
            # Express every value as its signed offset from the reference direction, so -179 and +179 are 2° apart.
            centre = S.circular_mean(bucket["ref"]) if bucket["ref"] else 0.0
            ref, inc = _stats([S.angle_diff(v, centre) for v in bucket["ref"]]), _stats([S.angle_diff(v, centre) for v in bucket["inc"]])
        else:
            ref, inc = _stats(bucket["ref"]), _stats(bucket["inc"])
        if ref["count"] < MIN_SAMPLES or inc["count"] < MIN_SAMPLES:
            status = "insufficient_samples"
        elif inc["median"] > ref["p95"]:
            status = "higher_than_reference"
        elif inc["median"] < ref["p05"]:
            status = "lower_than_reference"
        else:
            status = "within_reference_range"
        entry = {"signal": signal, "scope": scope, "unit": unit, "clock_domain": clock, "domain": domain, "reference": ref, "incident": inc,
                 "median_change": (inc["median"] - ref["median"]) if status != "insufficient_samples" else None, "status": status}
        if circular:
            entry["circular"] = {"reference_direction_deg": round(centre, 2),
                                 "note": "Wrapping angle: statistics are offsets in degrees from the reference direction, not raw angles."}
        signals.append(entry)
    return {"status": "computed", "rule": f"incident median above reference p95 or below reference p05; ≥{MIN_SAMPLES} samples in each window",
            "signals": signals, "departing": sum(1 for s in signals if s["status"] in ("higher_than_reference", "lower_than_reference")),
            "clock_note": "Windows are applied in each record's own clock; an undeclared offset between clocks shifts window edges."}
