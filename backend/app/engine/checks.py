"""Next-best-check catalog and ordinal ranking for remote-control response delay.

Ranking is a documented lexicographic rule, not a probability or a calibrated information gain:
  1. matches the selected objective (reduce uncertainty / restore operations)
  2. not already answered in this run or performed earlier
  3. triggered by a current observation or gap (vs. generic)
  4. availability: automatic read-only < remote ready < needs context < needs data/authorization < on-site
  5. more open fault domains it can inform
  6. lower operational disruption
Each ranked check carries the rule values that placed it, so an engineer can disagree precisely.
"""
from __future__ import annotations

from typing import Any, Dict, List

POLICY_VERSION = "next-check-policy-v1"
AVAILABILITY_ORDER = {"ready_automatic": 0, "ready_remote": 1, "needs_context": 2, "needs_data": 3, "needs_authorization": 3, "onsite_required": 4}
DISRUPTION_ORDER = {"none": 0, "low": 1, "high": 2}
OPEN_STATUSES = ("not_observed", "anomaly_observed", "delay_observed")

CATALOG: List[Dict[str, Any]] = [
    {"id": "CHK-SOURCE-ROWS", "question": "Inspect the original records inside the symptom window", "objective": "reduce_uncertainty",
     "mode": "automatic_read_only", "tool": "window_rows", "domains": ["network", "control_suite", "vehicle_interface", "vehicle_body"],
     "disruption": "none", "who": "Support engineer", "tools": ["Veyra source viewer"],
     "why": "Confirms what was actually recorded and where coverage is missing before anything is attributed.",
     "expected": {"coverage_ok": "Records exist for the symptom window", "coverage_gap": "Gaps limit what can be concluded"}},
    {"id": "CHK-CMD-CHAIN", "question": "Compare command stage latencies inside vs outside the symptom window", "objective": "reduce_uncertainty",
     "mode": "automatic_read_only", "tool": "command_chain", "domains": ["network", "control_suite", "vehicle_interface", "vehicle_body"],
     "disruption": "none", "who": "Support engineer", "tools": ["Command-ID correlation"], "requires_signals": ["command.received", "command.output"],
     "why": "Shows in which segment of the same commands time elapsed; segments without records stay open.",
     "expected": {"network": "Delay before receipt", "control_suite": "Delay between receipt and output", "vehicle": "Delay between output and feedback"}},
    {"id": "CHK-IO-BASELINE", "question": "Compare vehicle-side input→response behaviour with a baseline window", "objective": "reduce_uncertainty",
     "mode": "automatic_read_only", "tool": "input_response", "domains": ["vehicle_interface", "vehicle_body"], "disruption": "none",
     "who": "Controls engineer", "tools": ["Input–Response Baseline"], "requires_signals": ["vehicle.command_input", "vehicle.actuator_response"],
     "requires_context": ["oem", "model", "kit_version", "control_version", "firmware_version", "vehicle_interface"],
     "why": "If the machine responds to received commands as it did before, a vehicle-side slowdown is less likely for that channel.",
     "expected": {"unchanged": "Vehicle-side response comparable to baseline", "changed": "Delay or gain changed on the vehicle side"}},
    {"id": "CHK-CLOCK-OFFSET", "question": "Establish the console–vehicle clock offset (sync logs or a shared event)", "objective": "reduce_uncertainty",
     "mode": "remote_manual", "domains": ["network", "control_suite"], "disruption": "none", "who": "Support engineer",
     "tools": ["NTP/PTP/GPS sync logs", "A timestamped event seen by both clocks"],
     "why": "Without a bounded offset, absolute delay between console send and vehicle receipt is unknown, and any relative change rests on the offset staying stable.",
     "expected": {"bounded": "Transport-segment latency becomes measurable", "unbounded": "Network transport stays unmeasured"}},
    {"id": "CHK-PROBE-PATH", "question": "Confirm the RTT probe measures the actual command path", "objective": "reduce_uncertainty",
     "mode": "remote_manual", "domains": ["network"], "disruption": "none", "who": "Network engineer", "tools": ["Probe configuration", "Routing table"],
     "why": "A probe on a different path can neither confirm nor exclude command-path delay.",
     "expected": {"same_path": "Probe anomalies describe the command path", "different_path": "Probe results do not apply to commands"}},
    {"id": "CHK-NETWORK-LOGS", "question": "Export router/base-station logs for the symptom window", "objective": "reduce_uncertainty",
     "mode": "remote_manual", "domains": ["network"], "disruption": "low", "who": "Network engineer", "tools": ["Router log export"],
     "requires_authorization": True,
     "why": "Link drops, handovers or congestion on the command path would show here.",
     "expected": {"network_event": "Link events align with the symptom", "no_event": "Network stays possible if logs are incomplete"}},
    {"id": "CHK-CONTROLLER-LOGS", "question": "Retrieve controller application logs around the heartbeat gap, resets and fault codes",
     "objective": "reduce_uncertainty", "mode": "remote_manual", "domains": ["control_suite"], "disruption": "low", "who": "Controls engineer",
     "tools": ["Controller log export"], "requires_authorization": True,
     "why": "Explains what the application was doing when it stopped reporting or restarted.",
     "expected": {"application_fault": "Errors precede the gap or restart", "external_trigger": "Restart follows a link-supervision event"}},
    {"id": "CHK-CONFIRM-CONTEXT", "question": "Confirm the machine context needed for comparisons", "objective": "reduce_uncertainty",
     "mode": "remote_manual", "domains": ["network", "control_suite", "vehicle_interface", "vehicle_body"], "disruption": "none",
     "who": "Field engineer with the customer", "tools": ["Machine records", "Kit and software inventory"],
     "why": "Baseline and precedent comparisons are blocked until the relevant attributes are confirmed.",
     "expected": {"confirmed": "Comparisons become available", "changed": "A recorded change may explain behaviour differences"}},
    {"id": "CHK-ONSITE-ACTUATOR", "question": "Inspect actuator and hydraulic response on site", "objective": "reduce_uncertainty",
     "mode": "onsite", "domains": ["vehicle_interface", "vehicle_body"], "disruption": "high", "who": "Field service technician",
     "tools": ["Hydraulic gauges", "OEM-approved service tooling (confirm compatibility)"],
     "why": "Direct inspection when vehicle-side evidence points there or remote evidence is exhausted.",
     "expected": {"fault_found": "Physical finding recorded with evidence", "nothing_found": "Vehicle side less likely for the inspected channel"}},
    {"id": "CHK-RESTORE", "question": "Agree an approved service-restoration option with the site owner", "objective": "restore_operations",
     "mode": "coordination", "domains": [], "disruption": "low", "who": "Site owner + authorized controls engineer", "tools": ["Approved service procedure"],
     "why": "Restoring operation and reducing uncertainty are different goals; this does not diagnose anything.",
     "expected": {"approved_option": "A human selects an approved intervention", "blocked": "Keep the machine state unchanged and escalate"}},
]


def _availability(check: Dict[str, Any], facts: Dict[str, Any]) -> Dict[str, Any]:
    signals = facts["signals_present"]
    missing_signals = [s for s in check.get("requires_signals", []) if s not in signals]
    if check["mode"] == "automatic_read_only":
        if check["id"] in facts["answered_in_run"]:
            return {"state": "answered_in_run", "detail": facts["answered_in_run"][check["id"]]}
        if missing_signals:
            return {"state": "needs_data", "detail": "Missing records: " + ", ".join(missing_signals)}
        if check["id"] == "CHK-IO-BASELINE" and facts.get("io_needs_context"):
            return {"state": "needs_context", "detail": "Context not confirmed comparable between windows: " + ", ".join(facts["io_needs_context"])}
        return {"state": "ready_automatic", "detail": "Runs read-only on imported records"}
    if check["mode"] == "onsite":
        return {"state": "onsite_required", "detail": "Requires travel and site approval"}
    if check.get("requires_authorization") and facts["remote_access"] != "read_only_authorized":
        return {"state": "needs_authorization", "detail": f"Remote access is '{facts['remote_access'] or 'not recorded'}'; read-only authorization must be confirmed"}
    return {"state": "ready_remote", "detail": "Can be done remotely by a person"}


def _triggered(check_id: str, facts: Dict[str, Any]) -> str:
    """Return the observation that makes this check relevant now, or '' if it is generic."""
    if check_id == "CHK-CLOCK-OFFSET" and facts["transport_cross_clock"]:
        return "Transport delay is measured only relative to an assumed-stable offset between unsynchronized clocks; bound and check the offset"
    if check_id == "CHK-PROBE-PATH" and facts["network_crossing"] and not facts["endpoints_confirmed"]:
        return "Probe-path threshold crossing while measurement endpoints are not confirmed"
    if check_id == "CHK-NETWORK-LOGS" and facts["network_crossing"]:
        return "Network probe crossing inside the symptom window"
    if check_id == "CHK-CONTROLLER-LOGS" and facts["control_events"]:
        return "Heartbeat gap, reset or fault code near the symptom window"
    if check_id == "CHK-CONFIRM-CONTEXT" and facts["context_gaps"]:
        return "Unconfirmed context blocks comparisons: " + ", ".join(facts["context_gaps"][:6])
    if check_id == "CHK-ONSITE-ACTUATOR" and facts["vehicle_delay"]:
        return "Delay or change observed on the vehicle side"
    if check_id == "CHK-IO-BASELINE" and facts.get("io_needs_context"):
        return "Input–response models exist but context is unconfirmed: " + ", ".join(facts["io_needs_context"][:6])
    if check_id == "CHK-SOURCE-ROWS" and facts["rows_in_window"]:
        return "Records exist inside the symptom window"
    if check_id in ("CHK-CMD-CHAIN", "CHK-IO-BASELINE") and check_id in facts["answered_in_run"]:
        return "Computed in this run"
    return ""


def rank(domains: Dict[str, Dict[str, Any]], facts: Dict[str, Any], goal: str) -> List[Dict[str, Any]]:
    ranked = []
    for check in CATALOG:
        availability = _availability(check, facts)
        trigger = _triggered(check["id"], facts)
        performed = facts["previously_performed"].get(check["id"])
        open_domains = [d for d in check["domains"] if domains.get(d, {}).get("status") in OPEN_STATUSES]
        key = (
            0 if check["objective"] == goal else 1,
            1 if availability["state"] == "answered_in_run" or performed else 0,
            0 if trigger else 1,
            AVAILABILITY_ORDER.get(availability["state"], 5),
            -len(open_domains),
            DISRUPTION_ORDER[check["disruption"]],
        )
        ranked.append({**{k: v for k, v in check.items() if k not in ("requires_signals",)},
                       "check_id": check["id"], "availability": availability, "trigger": trigger or None,
                       "informs_open_domains": open_domains, "previously_performed": performed,
                       "rank_key": {"objective_match": key[0] == 0, "already_answered": key[1] == 1, "triggered": key[2] == 0,
                                    "availability": availability["state"], "open_domains": len(open_domains), "disruption": check["disruption"]},
                       "_key": key, "policy_version": POLICY_VERSION, "cost": "unknown", "duration": "unknown"})
    ranked.sort(key=lambda c: (c["_key"], c["check_id"]))
    for position, check in enumerate(ranked, 1):
        check["rank"] = position
        check["rank_explanation"] = _explain(check)
        del check["_key"]
    return ranked


def _explain(check: Dict[str, Any]) -> str:
    k = check["rank_key"]
    parts = ["matches objective" if k["objective_match"] else "different objective",
             "already answered/performed" if k["already_answered"] else "not yet answered",
             "triggered: " + check["trigger"] if k["triggered"] else "generic (no current trigger)",
             "availability: " + k["availability"].replace("_", " "),
             f"informs {k['open_domains']} open domain(s)", "disruption: " + k["disruption"]]
    return "; ".join(parts)
