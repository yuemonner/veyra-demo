"""Triage, ownership routing, hypothesis board and same-machine recurrence (pure, documented rules).

Engineers said the expensive part of field debugging is often not the analysis but deciding how
severe a case is and whose problem it is, and teams handing it back and forth. This module turns
the run's domain evidence into a *proposed* severity and owner, keeps every human triage change, and
counts handoffs. Nothing here is a root cause; proposals are ordinal rules a person can overrule.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

TRIAGE_RULES_VERSION = "triage-rules-v1"
RECURRENCE_RULES_VERSION = "recurrence-rules-v1"

OWNERS = {
    "network": "Network & connectivity",
    "control_suite": "Remote-control software",
    "vehicle_interface": "Vehicle integration",
    "vehicle_body": "Machine & hydraulics service",
}
DOMAIN_NAMES = {"network": "Network", "control_suite": "Remote-control software", "vehicle_interface": "Vehicle interface", "vehicle_body": "Machine body & hydraulics"}
_STRENGTH = {"delay_observed": 3, "anomaly_observed": 2, "not_observed": 1, "no_delay_observed_in_measured_part": 0}
SAFETY_INTERVENTIONS = ("takeover", "emergency_stop")
HANDOFF_WARNING = 2


def hypothesis_board(domains: Dict[str, Dict[str, Any]], ledger: Sequence[Dict[str, Any]], owners: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """Engine status per domain next to the latest human verdict (ruled out / weakened / supported / reopened)."""
    latest: Dict[str, Dict[str, Any]] = {}
    for entry in ledger:
        if entry["kind"] == "hypothesis_status":
            latest[entry["payload"]["domain"]] = entry
    board = []
    for domain, assessment in domains.items():
        human = latest.get(domain)
        human_status = human["payload"]["status"] if human else None
        if human_status == "ruled_out":
            column = "ruled_out"
        elif human_status == "supported" or (human_status != "weakened" and assessment["status"] == "delay_observed"):
            column = "supported"
        else:
            column = "open"
        board.append({
            "domain": domain, "owner": (owners or OWNERS).get(domain), "engine_status": assessment["status"], "column": column,
            "human": None if not human else {"status": human_status, "basis": human["payload"]["basis"], "scope": human["payload"].get("scope"),
                                             "by": human["actor"], "at": human["recorded_at"], "entry_id": human["id"],
                                             "evidence_entries": human["payload"]["evidence_entries"], "finding_rules": human["payload"]["finding_rules"]},
            "engine_hint": ("Measured part showed no delay; a person may rule this out for the measured scope only"
                            if assessment["status"] == "no_delay_observed_in_measured_part" and human_status != "ruled_out" else None),
            "evidence_count": len(assessment["observations"]),
        })
    return board


def propose(domains: Dict[str, Dict[str, Any]], board: List[Dict[str, Any]], interventions: List[Dict[str, Any]],
            ledger: Sequence[Dict[str, Any]], owners: Optional[Dict[str, str]] = None, names: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    OWNERS = owners or globals()["OWNERS"]  # the machine profile names the teams
    NAMES = names or globals()["DOMAIN_NAMES"]
    ruled_out = {b["domain"] for b in board if b["column"] == "ruled_out"}
    safety = [i for i in interventions if i["intervention_type"] in SAFETY_INTERVENTIONS]
    if safety:
        severity = {"level": "high", "rule": "T1",
                    "basis": f"Operator {safety[0]['intervention_type'].replace('_', ' ')} recorded; treat as safety-relevant until the intervention is reviewed"}
    elif any(d["status"] in ("delay_observed", "anomaly_observed") for d in domains.values()):
        severity = {"level": "medium", "rule": "T2", "basis": "Delay or anomaly observed in at least one domain; no safety intervention recorded"}
    else:
        severity = {"level": "low", "rule": "T3", "basis": "No delay or anomaly observed in the imported records"}

    candidates = sorted(((d, a) for d, a in domains.items() if d not in ruled_out),
                        key=lambda kv: (-_STRENGTH.get(kv[1]["status"], 0), kv[0]))
    top = candidates[0] if candidates else None
    if top and _STRENGTH.get(top[1]["status"], 0) >= 2:
        tied = [d for d, a in candidates if _STRENGTH.get(a["status"], 0) == _STRENGTH.get(top[1]["status"], 0)]
        owner = {"team": OWNERS[top[0]] if len(tied) == 1 else "Joint review: " + ", ".join(OWNERS[d] for d in tied),
                 "domains": tied, "rule": "O1" if len(tied) == 1 else "O2",
                 "basis": "Strongest measured evidence: " + ", ".join(f"{NAMES.get(d, d)}: {domains[d]['status'].replace('_', ' ')}" for d in tied)}
    else:
        owner = {"team": "Unassigned (joint review)", "domains": [], "rule": "O3", "basis": "No domain has measured delay or anomalies yet"}
    also_open = [{"domain": d, "team": OWNERS[d], "status": a["status"]} for d, a in candidates
                 if d not in owner["domains"] and a["status"] != "no_delay_observed_in_measured_part"]
    route_away = [{"domain": d, "team": OWNERS[d], "why": "ruled out by a person (scoped)" if d in ruled_out else "no delay in the measured part"}
                  for d, a in domains.items() if d in ruled_out or a["status"] == "no_delay_observed_in_measured_part"]

    history = [{"severity": e["payload"]["severity"], "owner": e["payload"]["owner"], "reason": e["payload"]["reason"], "by": e["actor"],
                "at": e["recorded_at"], "handoff": e["payload"]["handoff"], "entry_id": e["id"]} for e in ledger if e["kind"] == "triage"]
    handoffs = sum(1 for h in history if h["handoff"])
    confirmed = history[-1] if history else None
    return {
        "rules_version": TRIAGE_RULES_VERSION,
        "proposed": {"severity": severity, "owner": owner, "also_open": also_open, "route_away": route_away},
        "confirmed": confirmed, "status": "confirmed_by_person" if confirmed else "proposed_only",
        "differs_from_proposal": bool(confirmed and (confirmed["severity"] != severity["level"] or confirmed["owner"] != owner["team"])),
        "history": history, "handoffs": handoffs,
        "handoff_warning": (f"Ownership changed {handoffs} times. Review jointly with every team named, using this record, instead of another handoff."
                            if handoffs >= HANDOFF_WARNING else None),
        "rules": {"T1": "Safety intervention (takeover / emergency stop) recorded -> high", "T2": "Delay or anomaly observed -> medium", "T3": "Otherwise -> low",
                  "O1": "Single domain with the strongest measured evidence -> its team", "O2": "Tie -> joint review", "O3": "No measured evidence -> unassigned"},
        "boundary": "Proposed triage from ordinal rules. A person confirms severity and owner; nothing is routed automatically.",
    }


def recurrence(history: List[Dict[str, Any]], domains: Dict[str, Dict[str, Any]], operators_now: Sequence[str],
               owners: Optional[Dict[str, str]] = None, names: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Prior cases on the same machine. Same domain with a different operator points to the machine or site, not technique (heuristic)."""
    OWNERS = owners or globals()["OWNERS"]
    DOMAIN_NAMES = names or globals()["DOMAIN_NAMES"]
    observed_now = [d for d, a in domains.items() if a["status"] in ("delay_observed", "anomaly_observed")]
    now = {o.casefold() for o in operators_now}
    notes = []
    for domain in observed_now:
        matches = [h for h in history if domain in h.get("observed_domains", [])]
        if not matches:
            continue
        other_operators = sorted({o for h in matches for o in h.get("operators", []) if o.casefold() not in now})
        same_operator = any(o.casefold() in now for h in matches for o in h.get("operators", []))
        notes.append({
            "domain": domain, "team": OWNERS.get(domain), "prior_cases": [h["case_id"] for h in matches],
            "different_operators": other_operators, "same_operator_seen_before": same_operator, "rule": "R-RECURRENCE",
            "text": (f"{DOMAIN_NAMES.get(domain, domain)}: also observed in {len(matches)} earlier case(s) on this machine"
                     + (f" with a different operator ({', '.join(other_operators)})" if other_operators and now else "")
                     + ". A machine- or site-side cause becomes more plausible than operator technique (heuristic, not proof)."
                     if other_operators and now else
                     f"{DOMAIN_NAMES.get(domain, domain)}: also observed in {len(matches)} earlier case(s) on this machine. Operators unknown or the same; no inference about technique."),
        })
    return {"rules_version": RECURRENCE_RULES_VERSION, "prior_cases": history, "notes": notes,
            "boundary": "Earlier cases on the same machine, as recorded. Recurrence is a prompt to look, not a diagnosis."}


def intervention_evidence(interventions: List[Dict[str, Any]], findings: Dict[str, Any], to_t, margin_s: float = 5.0) -> List[Dict[str, Any]]:
    """For each reported intervention: brief events and controller events recorded close to it (by timestamp, per clock)."""
    out = []
    events = (findings.get("events") or {}).get("events", [])
    for report in interventions:
        at = to_t(report["occurred_at"])
        nearby = []
        for signal in (findings.get("transients") or {}).get("signals", []):
            for event in signal["events"]:
                delta = event["t_start"] - at
                if abs(delta) <= margin_s:
                    nearby.append({"kind": "brief_event", "signal": signal["signal"], "scope": signal["scope"], "clock_domain": signal["clock_domain"],
                                   "at": event["start"], "offset_ms": round(delta * 1000), "detail": f"{event['samples']} sample(s), {event['direction']}, peak {event['peak_value']} {signal['unit']}"})
        for event in events:
            delta = event["t"] - at if "t" in event else None
            if delta is not None and abs(delta) <= 6 * margin_s:
                nearby.append({"kind": "recorded_event", "signal": event["signal"], "clock_domain": event.get("clock_domain"), "at": event.get("time_utc"),
                               "offset_ms": round(delta * 1000), "detail": event.get("code") or event.get("event_value") or event["signal"]})
        nearby.sort(key=lambda n: abs(n["offset_ms"]))
        out.append({**report, "evidence_nearby": nearby,
                    "clock_note": "Offsets compare the intervention time (as reported) with record timestamps in their own clock; an undeclared clock offset shifts them."})
    return out
