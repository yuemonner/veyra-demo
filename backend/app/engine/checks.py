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

from typing import Any, Dict, List, Optional

POLICY_VERSION = "next-check-policy-v4"  # v2 adds reference, brief-event, intervention-review and reproduction checks; v3 words checks per machine profile and evidence; v4 puts the check that settled a similar closed case first
AVAILABILITY_ORDER = {"ready_automatic": 0, "ready_remote": 1, "needs_context": 2, "needs_data": 3, "needs_authorization": 3, "onsite_required": 4, "not_applicable": 6}
DISRUPTION_ORDER = {"none": 0, "low": 1, "high": 2}
OPEN_STATUSES = ("not_observed", "anomaly_observed", "delay_observed")

CATALOG: List[Dict[str, Any]] = [
    {"id": "CHK-SOURCE-ROWS", "question": "Inspect the original records inside the symptom window", "objective": "reduce_uncertainty",
     "mode": "automatic_read_only", "tool": "window_rows", "domains": ["network", "control_suite", "vehicle_interface", "vehicle_body"],
     "disruption": "none", "who": "Support engineer", "tools": ["Veyra source viewer"],
     "why": "Confirms what was actually recorded and where coverage is missing before anything is attributed.",
     "expected": {"coverage_ok": "Records exist for the symptom window", "coverage_gap": "Gaps limit what can be concluded"}},
    {"id": "CHK-DECLARE-REFERENCE", "question": "Declare a known-good run for this machine to compare against", "objective": "reduce_uncertainty",
     "mode": "remote_manual", "domains": ["network", "control_suite", "vehicle_interface", "vehicle_body"], "disruption": "none",
     "who": "Field engineer with the operator", "tools": ["Shift log", "Operator confirmation"],
     "why": "Comparisons are only as good as the reference. Without a declared known-good window they rest on assuming earlier data was healthy.",
     "expected": {"declared": "Every comparison is made against confirmed normal behaviour", "none_available": "Results stay labelled as assumption-based"}},
    {"id": "CHK-BRIEF-EVENTS", "question": "Scan every high-rate sample for brief events a plot would hide", "objective": "reduce_uncertainty",
     "mode": "automatic_read_only", "tool": "transients", "domains": ["control_suite", "vehicle_interface", "vehicle_body"], "disruption": "none",
     "who": "Support engineer", "tools": ["Brief-event scan"],
     "why": "A one-sample spike lasting tens of milliseconds is invisible on a whole-run plot but can mark where to look.",
     "expected": {"events": "Exact timestamps and source rows of brief departures", "none": "No sub-second departures on scanned signals"}},
    {"id": "CHK-INTERVENTION-REVIEW", "question": "Review the operator's intervention: stated reason against what was recorded at that moment",
     "objective": "reduce_uncertainty", "mode": "remote_manual", "domains": ["network", "control_suite", "vehicle_interface", "vehicle_body"], "disruption": "none",
     "who": "Support engineer with the operator", "tools": ["Intervention record", "Signals around the intervention"],
     "why": "On a machine meant to run under remote control, every intervention is worth explaining. A false alarm is still worth recreating.",
     "expected": {"necessary": "Recorded behaviour supports the operator's reason", "unnecessary": "Recreate the conditions that triggered a false alarm"}},
    {"id": "CHK-REPRODUCE", "question": "Try to reproduce the symptom under the recorded conditions (log replay, bench or simulation)",
     "objective": "reduce_uncertainty", "mode": "remote_manual", "domains": ["network", "control_suite", "vehicle_interface", "vehicle_body"], "disruption": "low",
     "who": "Controls engineer", "tools": ["Log replay", "Bench controller", "Simulator"],
     "why": "If the symptom can be produced on purpose, the suspected mechanism gains support and a fix can be tested before travel.",
     "expected": {"reproduced": "Mechanism supported; test the fix the same way", "not_reproduced": "Conditions or model differ from the field"}},
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
    {"id": "CHK-CLOCK-OFFSET", "question": "Establish the clock offset between the operator station and the machine (sync logs or a shared event)", "objective": "reduce_uncertainty",
     "mode": "remote_manual", "domains": ["network", "control_suite"], "disruption": "none", "who": "Support engineer",
     "tools": ["NTP/PTP/GPS sync logs", "A timestamped event seen by both clocks"],
     "why": "Without a bounded offset, absolute delay between a command being sent and the machine receiving it is unknown, and any relative change rests on the offset staying stable.",
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
    {"id": "CHK-DRIVE-LOGS", "question": "Retrieve the drive's own log for the axis named in the evidence (error register, temperature, encoder status)",
     "objective": "reduce_uncertainty", "mode": "remote_manual", "domains": ["vehicle_interface"], "disruption": "low", "who": "Drive / actuator engineer",
     "tools": ["Drive or joint controller log export"], "requires_authorization": True,
     "why": "Shows whether the drive itself recorded a hardware fault (over-temperature, encoder or power error) or only the tracking error.",
     "expected": {"drive_hw_fault": "The drive recorded a hardware fault before the departure", "drive_no_hw_fault": "Only the tracking error, no hardware fault"}},
    {"id": "CHK-CONFIRM-CONTEXT", "question": "Confirm the machine context needed for comparisons", "objective": "reduce_uncertainty",
     "mode": "remote_manual", "domains": ["network", "control_suite", "vehicle_interface", "vehicle_body"], "disruption": "none",
     "who": "Field engineer on site", "tools": ["Machine records", "Kit and software inventory"],
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
    if check["id"] in facts["answered_in_run"]:
        return {"state": "answered_in_run", "detail": facts["answered_in_run"][check["id"]]}
    if check["id"] == "CHK-INTERVENTION-REVIEW" and not facts.get("interventions_total"):
        return {"state": "not_applicable", "detail": "No operator intervention recorded for this case"}
    if check["mode"] == "automatic_read_only":
        if missing_signals:
            return {"state": "needs_data", "detail": "Missing records: " + ", ".join(missing_signals)}
        if check["id"] == "CHK-BRIEF-EVENTS" and not facts.get("brief_events_scanned"):
            return {"state": "needs_data", "detail": "No signal sampled at 5 Hz or faster"}
        if check["id"] == "CHK-IO-BASELINE" and facts.get("io_needs_context"):
            return {"state": "needs_context", "detail": "Context not confirmed comparable between windows: " + ", ".join(facts["io_needs_context"])}
        return {"state": "ready_automatic", "detail": "Runs read-only on imported records"}
    if check["mode"] == "onsite":
        return {"state": "onsite_required", "detail": "Requires travel and site approval"}
    if check.get("requires_authorization") and facts["remote_access"] != "read_only_authorized":
        return {"state": "needs_authorization", "detail": f"Remote access is '{facts['remote_access'] or 'not recorded'}'; read-only authorization must be confirmed"}
    return {"state": "ready_remote", "detail": "Can be done remotely by a person"}


def _triggered(check_id: str, facts: Dict[str, Any], names: Optional[Dict[str, str]] = None) -> str:
    """Return the observation that makes this check relevant now, or '' if it is generic."""
    if check_id == "CHK-CLOCK-OFFSET" and facts["transport_cross_clock"]:
        return "Transport delay is measured only relative to an assumed-stable offset between unsynchronized clocks; bound and check the offset"
    if check_id == "CHK-PROBE-PATH" and facts["network_crossing"] and not facts["endpoints_confirmed"]:
        return "Probe-path threshold crossing while measurement endpoints are not confirmed"
    if check_id == "CHK-NETWORK-LOGS" and facts["network_crossing"]:
        return "Network probe crossing inside the symptom window"
    if check_id == "CHK-CONTROLLER-LOGS" and facts["control_events"]:
        return "Controller events, heartbeat gaps, resets or fault codes near the symptom window"
    if check_id == "CHK-CONFIRM-CONTEXT" and facts["context_gaps"]:
        return "Unconfirmed context blocks comparisons: " + ", ".join(facts["context_gaps"][:6])
    if check_id == "CHK-DRIVE-LOGS" and facts.get("vehicle_departures"):
        return "Stopped following its commands: " + ", ".join(facts["vehicle_departures"][:4])
    if check_id == "CHK-ONSITE-ACTUATOR" and facts.get("vehicle_departures"):
        return "Stopped following its commands: " + ", ".join(facts["vehicle_departures"][:4])
    if check_id == "CHK-ONSITE-ACTUATOR" and facts["vehicle_delay"]:
        return "Delay or change observed on the vehicle side"
    if check_id == "CHK-IO-BASELINE" and facts.get("io_needs_context"):
        return "Input–response models exist but context is unconfirmed: " + ", ".join(facts["io_needs_context"][:6])
    if check_id == "CHK-DECLARE-REFERENCE" and facts.get("reference_status") not in ("declared", "explicit"):
        return "Comparisons currently use " + ("an assumed reference (data before the symptom)" if facts.get("reference_status") == "assumed_default" else "no reference")
    if check_id == "CHK-INTERVENTION-REVIEW" and facts.get("interventions_unreviewed"):
        return f"{len(facts['interventions_unreviewed'])} operator intervention(s) not yet reviewed"
    if check_id == "CHK-REPRODUCE" and facts.get("observed_domains") and not facts.get("reproduction_attempts"):
        return "Evidence points at " + ", ".join((names or {}).get(d, d.replace("_", " ")) for d in facts["observed_domains"]) + "; no reproduction attempt recorded"
    if check_id == "CHK-BRIEF-EVENTS" and facts.get("brief_events_in_window"):
        return f"{facts['brief_events_in_window']} brief event(s) inside the symptom window"
    if check_id == "CHK-SOURCE-ROWS" and facts["rows_in_window"]:
        return "Records exist inside the symptom window"
    if check_id in ("CHK-CMD-CHAIN", "CHK-IO-BASELINE") and check_id in facts["answered_in_run"]:
        return "Computed in this run"
    return ""


def _for_case(check: Dict[str, Any], facts: Dict[str, Any], profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Reword a catalog check for this machine type and this evidence (the catalog holds the defaults)."""
    check = dict(check)
    if check["id"] == "CHK-CONTROLLER-LOGS" and not facts.get("control_events"):
        check["question"] = "Retrieve controller mode and event logs, if available, around the onset"
        check["why"] = ("No controller events, resets, fault codes or heartbeats are in the imported files. A failsafe, "
                        "mode change or output override at the onset would only show in the controller's own logs.")
    if check["id"] == "CHK-DRIVE-LOGS" and facts.get("vehicle_departures"):
        check["question"] = f"Retrieve the drive log for {', '.join(facts['vehicle_departures'][:3])} (error register, temperature, encoder status) around the onset"
    override = ((profile or {}).get("checks") or {}).get(check["id"]) or {}
    for key in ("question", "why", "who", "tools"):
        if key in override:
            check[key] = override[key]
    check["applicable_to_machine"] = override.get("applicable", True)
    return check


def rank(domains: Dict[str, Dict[str, Any]], facts: Dict[str, Any], goal: str, profile: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    ranked = []
    for base in CATALOG:
        check = _for_case(base, facts, profile)
        availability = _availability(check, facts)
        if not check["applicable_to_machine"]:
            availability = {"state": "not_applicable", "detail": f"Not used for {(profile or {}).get('name', 'this machine type')}"}
        trigger = _triggered(check["id"], facts, {r: d["label"] for r, d in ((profile or {}).get("domains") or {}).items()})
        precedent = (facts.get("precedent_checks") or {}).get(check["id"]) if check["applicable_to_machine"] else None
        if precedent:  # what settled a similar earlier case goes first; it is a lead, so the reason says where it comes from
            trigger = f"Settled a similar earlier case: {precedent['title']} (similarity {precedent['similarity']:g})" + (f"; {trigger}" if trigger else "")
        performed = facts["previously_performed"].get(check["id"])
        open_domains = [d for d in check["domains"] if domains.get(d, {}).get("status") in OPEN_STATUSES]
        key = (
            0 if check["objective"] == goal else 1,
            1 if availability["state"] in ("answered_in_run", "not_applicable") or performed else 0,
            0 if precedent else 1,
            0 if trigger else 1,
            AVAILABILITY_ORDER.get(availability["state"], 5),
            -len(open_domains),
            DISRUPTION_ORDER[check["disruption"]],
        )
        ranked.append({**{k: v for k, v in check.items() if k not in ("requires_signals", "applicable_to_machine")},
                       "check_id": check["id"], "availability": availability, "trigger": trigger or None,
                       "informs_open_domains": open_domains, "previously_performed": performed,
                       "precedent": precedent,
                       "rank_key": {"objective_match": key[0] == 0, "already_answered": key[1] == 1, "settled_similar_case": key[2] == 0, "triggered": key[3] == 0,
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
             "triggered: " + check["trigger"] if k["triggered"] or k.get("settled_similar_case") else "generic (no current trigger)",
             "availability: " + k["availability"].replace("_", " "),
             f"informs {k['open_domains']} open domain(s)", "disruption: " + k["disruption"]]
    return "; ".join(parts)
