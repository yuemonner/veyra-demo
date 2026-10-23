"""Explicit states, derived only from the case ledger, with the transitions the ledger accepts.

Three lifecycles, each a small table instead of rules scattered across the validators:

1. A part of the machine (evidence lifecycle). Each stage needs a different kind of record; nothing skips a stage:
     observed            the engine looked and saw nothing out of normal
     anomaly             a finding (an observation that departs from the reference); a fact about data, not a cause
     hypothesis          a part kept open, or supported by a person's verdict, as a possible explanation
     cause_probable      an engineer's closure names it, backed by evidence in the case or judgement
     cause_confirmed     an engineer's closure names it AND links a confirming record (inspection, reproduction,
                         or a fix that resolved it), see CONFIRMING_RECORD
   The engine never writes cause_*; only a case closure does (ledger kind case_closure).

2. A check (what Veyra recommends, a person approves, the site does):
     proposed -> approved | rejected -> sent -> answered | not_performed
   `approved` covers accepted and edited. A request can be sent only when approved; a result recorded only when
   approved (sending is optional: a check done in-house needs no request). Once answered, the check is closed for
   this run: no re-review, no new request, no second result.

3. A decision (Veyra may recommend, a person decides, someone acts, the result is checked):
     recommended -> decided -> acted -> outcome_recorded -> verified | verification_rejected
   An action links the decision it carries out; an outcome needs a performed (or explicitly simulated) action and
   cannot start before it; verification needs a second person (enforced in the ledger). Decisions are often written
   down after the fact, so a decision's record time is not compared with the action's time.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

STATES_VERSION = "case-states-v1"

PART_STAGES = ("observed", "anomaly", "hypothesis", "cause_probable", "cause_confirmed")
CHECK_STATES = ("proposed", "approved", "rejected", "sent", "answered", "not_performed")
DECISION_STATES = ("recommended", "decided", "acted", "outcome_recorded", "verified", "verification_rejected")

# Which check states each ledger kind may act on (the transition table for lifecycle 2).
CHECK_TRANSITIONS = {
    "check_review": ("proposed", "approved", "rejected"),          # a review can be revised until the check is answered
    "request_sent": ("approved", "sent"),                          # a reminder / second channel is a new 'sent'
    "check_execution": ("approved", "sent", "not_performed"),      # a result once approved; retry after 'not performed'
}

# What a closure must link to call a cause confirmed, per confirmation method (lifecycle 1).
CONFIRMING_RECORD = {
    "inspection": ("check_execution", lambda p: p.get("status") == "performed"),
    "reproduction": ("reproduction_attempt", lambda p: p.get("result") in ("reproduced", "partially_reproduced")),
    "fix_resolved_it": ("outcome", lambda p: p.get("result") == "recovered"),  # simulated only in a synthetic case (below)
}
CONFIRMING_WORDS = {"inspection": "a performed check (the inspection)", "reproduction": "a reproduction attempt that reproduced it",
                    "fix_resolved_it": "an outcome where the machine recovered after the fix (simulated only in a synthetic case)"}


class StateError(ValueError):
    pass


def check_state(entries: Sequence[Dict[str, Any]], run_id: str, check_id: str) -> str:
    """State of one check in one run, replayed from the ledger in order."""
    state = "proposed"
    for e in entries:
        p = e.get("payload") or {}
        if p.get("run_id") != run_id:
            continue
        if e["kind"] == "check_review" and p.get("check_id") == check_id:
            state = "rejected" if p.get("verdict") == "rejected" else "approved"
        elif e["kind"] == "request_sent" and check_id in (p.get("check_ids") or []):
            state = "sent"
        elif e["kind"] == "check_execution" and p.get("check_id") == check_id:
            state = "not_performed" if p.get("status") == "not_performed" else "answered"
    return state


def require_check_transition(kind: str, entries: Sequence[Dict[str, Any]], run_id: str, check_id: str) -> str:
    state = check_state(entries, run_id, check_id)
    allowed = CHECK_TRANSITIONS[kind]
    if state not in allowed:
        what = {"check_review": "review it again", "request_sent": "record a request for it", "check_execution": "record a result"}[kind]
        why = {"answered": "it already has a result in this run", "rejected": "it was rejected",
               "proposed": "it has not been approved", "not_performed": "it was recorded as not performed"}.get(state, f"it is {state}")
        raise StateError(f"Cannot {what} for {check_id}: {why} (check state '{state}')")
    return state


def decision_state(entries: Sequence[Dict[str, Any]], decision_id: str) -> str:
    state = "decided"
    actions = {e["id"] for e in entries if e["kind"] == "action" and e["payload"].get("decision_id") == decision_id
               and e["payload"].get("status") in ("performed", "simulated")}
    if actions:
        state = "acted"
    outcomes = [e for e in entries if e["kind"] == "outcome" and e["payload"].get("action_id") in actions]
    if outcomes:
        state = "outcome_recorded"
        reviews = [e for e in entries if e["kind"] == "outcome_review" and e["payload"].get("outcome_id") in {o["id"] for o in outcomes}]
        if reviews:
            state = "verified" if reviews[-1]["payload"].get("verdict") == "accepted" else "verification_rejected"
    return state


def require_confirming_record(method: Optional[str], cause_domain: Optional[str], evidence_ids: Sequence[str],
                              entries: Sequence[Dict[str, Any]], evidence_note: Optional[str] = None, synthetic: bool = False) -> None:
    """'Cause confirmed' rests either on a record outside Veyra described in evidence_note (a work order, a photo), or on
    records of this case. When it rests on records of this case, at least one must actually confirm: a check recorded as
    not performed, a reproduction that failed, a simulated or failed fix, or an inspection of another part does not."""
    if method not in CONFIRMING_RECORD or not evidence_ids or evidence_note:
        return
    kind, test = CONFIRMING_RECORD[method]
    by_id = {e["id"]: e for e in entries}
    for i in evidence_ids:
        e = by_id.get(i)
        if not e or e["kind"] != kind or not test(e["payload"]):
            continue
        if kind == "outcome" and e["payload"].get("simulated") and not synthetic:
            continue  # a simulated fix confirms nothing about a real machine
        if kind == "check_execution" and cause_domain:
            domains = ((e["payload"].get("proposed") or {}).get("domains")
                       or next((r["payload"]["proposed"].get("domains") for r in entries if r["id"] == e["payload"].get("review_id")), None))
            if domains and cause_domain not in domains:
                continue  # an inspection of another part does not confirm this one
        return
    raise StateError(f"None of the linked records confirms it: 'confirmed by {method.replace('_', ' ')}' needs {CONFIRMING_WORDS[method]}"
                     + (" that examined this part" if kind == "check_execution" and cause_domain else "")
                     + "; link that record, describe the record outside Veyra in evidence_note, or close it as 'probable'")


def lifecycle(run: Optional[Dict[str, Any]], entries: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Every part's stage, every check's state in the latest run, every decision's state."""
    parts: Dict[str, Any] = {}
    if run:
        for domain, a in (run.get("domains") or {}).items():
            flagged = [o for o in a.get("observations", []) if o.get("kind") in ("anomaly", "delay")]
            parts[domain] = {"stage": "anomaly" if flagged else "observed", "findings": len(flagged)}
        for h in run.get("hypotheses") or []:
            d = h.get("domain")
            # A hypothesis is a part the board SUPPORTS (a measured delay or a person's verdict), not merely a finding.
            if d and (h.get("column") == "supported" or (h.get("human") or {}).get("status") == "supported"):
                parts.setdefault(d, {"findings": 0})["stage"] = "hypothesis"
        closure = next((e for e in reversed(entries) if e["kind"] == "case_closure"), None)
        if closure and closure["payload"].get("cause_domain"):
            d = closure["payload"]["cause_domain"]
            stage = "cause_confirmed" if closure["payload"].get("investigation_status") == "cause_confirmed" else \
                    "cause_probable" if closure["payload"].get("investigation_status") == "cause_probable" else None
            if stage:
                parts.setdefault(d, {"findings": 0})["stage"] = stage
                parts[d]["closure_id"] = closure["id"]
    checks = {}
    if run:
        for c in run.get("checks") or []:
            st = check_state(entries, run["id"], c["check_id"])
            if st != "proposed" or c.get("rank", 99) <= 5:
                checks[c["check_id"]] = st
    decisions = [{"decision_id": e["id"], "chosen_option": e["payload"].get("chosen_option"), "state": decision_state(entries, e["id"])}
                 for e in entries if e["kind"] == "decision"]
    recommended = any(e["kind"] == "next_action" and e["payload"].get("stage") == "report" and (not run or e["payload"].get("run_id") == run["id"])
                      for e in entries)
    return {"version": STATES_VERSION, "parts": parts, "checks": checks, "decisions": decisions,
            "recommendation": "recommended" if recommended and not decisions else None,
            "stages": {"part": list(PART_STAGES), "check": list(CHECK_STATES), "decision": list(DECISION_STATES)}}
