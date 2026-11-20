"""Evidence-backed investigation scope. Candidate windows are not confirmed incidents.

Uses existing event semantics, threshold screening and heartbeat-gap methods. No source-specific
topics, labels, clocks or learned thresholds. Uninterpreted measurements never seed a window.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Dict, List

from app.analysis import screening
from app.ingest import catalog

VERSION = "investigation-entry-v0"
PADDING_S = 5.0
MERGE_S = 5.0
MAX_WINDOWS = 3
_ALERT = re.compile(r"\b(WARNING|ERROR|CRITICAL|EMERGENCY|watchdog|hardfault|panic)\b|failsafe.*(?:activated|off\s*->\s*on)", re.I)
_NEGATED = re.compile(r"\b(no (?:fault|error)|failsafe (?:deactivated|off)|(?:fault|failsafe) (?:cleared|false))\b", re.I)


def _finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def discover(imports: List[Dict[str, Any]]) -> Dict[str, Any]:
    snapshot = {i["id"]: i["sha256"] for i in imports}
    rows = [r for i in imports for r in i["rows"] if _finite(r.get("t")) and r.get("clock_domain")]
    usable = [r for r in rows if r.get("kind") == "measurement" and _finite(r.get("value"))
              and r.get("signal") in catalog.CATALOG and r.get("unit") == catalog.CATALOG[r["signal"]].unit]
    anchors, skipped = [], []

    def add(t, end, clock, signal, method, refs, explanation):
        anchors.append({"t": t, "end": end, "clock_domain": clock, "signal": signal,
                        "method": method, "source_rows": refs, "explanation": explanation})

    for r in rows:
        sig = r.get("signal")
        text = str(r.get("event_value") or r.get("code") or "")
        if r.get("kind") != "event" or sig not in screening.EVENT_SIGNALS:
            continue
        if (sig == "controller.reset" or
                (sig == "fault.code" and text.strip().lower() not in ("", "0", "none", "ok")) or
                (_ALERT.search(text) and not _NEGATED.search(text))):
            add(r["t"], r["t"], r["clock_domain"], sig, "reported_alert", [{"import_id": r.get("import_id"), "row": r.get("row")}],
                "Source reports an alert/reset/fault: " + text + ". This may be a reaction, not the initiating fault.")

    for m in screening.measurements(usable, None):
        for crossing in m["crossings"]:
            if crossing["count"] >= 3:  # an isolated statistical outlier does not define an incident
                add(crossing["start"], crossing["end"], m["clock_domain"], m["signal"], "existing_threshold_screening",
                    crossing["rows"], f"{crossing['count']} samples cross {m['threshold']}; illustrative screening, not an OEM limit.")
    for gap in screening.heartbeat_gaps(rows, None):
        for g in gap.get("gaps", []):
            refs = [{"import_id": r.get("import_id"), "row": r.get("row")} for r in rows
                    if r.get("signal") == "app.heartbeat" and r["clock_domain"] == gap["clock_domain"] and r["t"] in (g["start"], g["end"])]
            add(g["start"], g["end"], gap["clock_domain"], "app.heartbeat", "existing_heartbeat_gap", refs,
                gap["rule"] + "; a gap is a loss of observation, not proof of a machine failure.")

    candidates = []
    for anchor in sorted(anchors, key=lambda a: (a["clock_domain"], a["t"])):
        clock = anchor["clock_domain"]
        measurements = [r for r in usable if r["clock_domain"] == clock]
        before = [r for r in measurements if anchor["t"] - PADDING_S <= r["t"] < anchor["t"]]
        after = [r for r in measurements if anchor["end"] <= r["t"] <= anchor["end"] + PADDING_S]
        if len({r["t"] for r in before}) < 3 or len({r["t"] for r in after}) < 3:
            skipped.append({"anchor": anchor, "reason": "Fewer than three distinct measurement times before/after the candidate in the same clock"})
            continue
        span = [min(r["t"] for r in before), max(r["t"] for r in after)]
        if candidates and candidates[-1]["clock_domain"] == clock and span[0] <= candidates[-1]["window"][1] + MERGE_S:
            candidates[-1]["window"][1] = max(candidates[-1]["window"][1], span[1])
            candidates[-1]["support"].append(anchor)
        else:
            candidates.append({"window": span, "clock_domain": clock, "anchor_s": anchor["t"], "support": [anchor]})
    candidates.sort(key=lambda c: (c["window"][0], c["clock_domain"]))
    for c in candidates:
        c["id"] = "candidate-" + hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()[:12]
        c["input_hashes"] = snapshot
        c["limitations"] = ["Exploratory window, not a confirmed incident or causal conclusion.",
                             "Operating context and sensor independence remain unverified; no healthy baseline assumed.",
                             "Other clock domains cannot be ordered against this candidate without alignment."]
        c["coverage"] = {"clock_domain": c["clock_domain"], "before_after_min_distinct_samples": 3,
                         "units": sorted({r["unit"] for r in usable if r["clock_domain"] == c["clock_domain"]})}
    return {"version": VERSION, "status": "candidate_windows" if candidates else "insufficient_evidence",
            "candidates": candidates[:MAX_WINDOWS], "deferred_candidates": candidates[MAX_WINDOWS:], "rejected_anchors": skipped,
            "input_hashes": snapshot, "methods": ["reported_alert", "existing_threshold_screening", "existing_heartbeat_gap"],
            "not_executed": ["Baseline-dependent command-response deviation requires a declared healthy reference.",
                             "No new change-point model or interpretation of unknown fields is assumed."],
            "missing_information": [] if candidates else ["Provide the approximate affected time range, or one timestamped observable symptom and its source.",
                    "If the recording lacks valid same-clock measurements around that time, provide those records and units."],
            "selection": "Earliest evidence-backed candidate first; not an estimate of fault probability.",
            "boundary": "Candidate discovery is scope selection, not diagnosis or proof of abnormal operation."}


def execution(findings: Dict[str, Any], window, trace, run_id, input_hashes):
    """Separate successful function returns from completed diagnostic calculations."""
    tools = {"screen_measurements": "measurements", "heartbeat_gaps": "heartbeats", "events_near_window": "events",
             "command_chain_latency": "command_chain", "input_response_baseline": "input_response",
             "compare_to_reference": "reference_comparison", "actuator_output_health": "actuator_outputs",
             "command_tracking": "tracking", "scan_brief_events": "transients"}
    receipts = []
    for step in trace:
        key = tools.get(step["tool"])
        if not key:
            continue
        value = findings.get(key)
        status, reason = "completed", None
        if step["status"] == "failed" or value is None:
            status, reason = "failed", step.get("error", "No result")
        elif window is None:
            status, reason = "skipped", "No credible incident window; metadata/screening is not an incident investigation"
        elif isinstance(value, dict):
            if value.get("status") in ("unavailable", "not_applicable", "no_baseline"):
                status, reason = "skipped", value.get("reason", value["status"])
            elif key == "tracking" and not any(a.get("status") not in ("no_baseline", "not_applicable", "insufficient_data") for a in value.get("axes", [])):
                status, reason = "skipped", "No tracking axis had an eligible baseline"
            elif key == "transients" and not value.get("signals"):
                status, reason = "skipped", "No sufficiently sampled interpretable signal"
            elif key == "events" and not value.get("events"):
                status, reason = "skipped", "No event records in the candidate scope"
        elif not value:
            status, reason = "skipped", "No applicable records"
        elif key == "measurements" and not any(m.get("threshold") and m.get("in_window", {}).get("count") for m in value):
            status, reason = "skipped", "Statistics inspected, but no applicable diagnostic threshold on a measured signal"
        elif key == "heartbeats" and not any(m.get("status") == "computed" for m in value):
            status, reason = "skipped", "Heartbeat coverage is insufficient"
        step["analysis_status"], step["analysis_reason"] = status, reason
        receipts.append({"tool": step["tool"], "status": status, "reason": reason, "run_id": run_id,
                         "input_hashes": input_hashes, "result_sha256": hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()})
    completed = sum(r["status"] == "completed" for r in receipts)
    failed = sum(r["status"] == "failed" for r in receipts)
    return {"status": "partial" if completed and failed else "analysis_completed" if completed else "insufficient_evidence",
            "completed": completed, "failed": failed,
            "analyses": receipts, "boundary": "Completed analyses do not confirm a fault or root cause."}
