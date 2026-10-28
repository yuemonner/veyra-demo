"""Intervention Ledger: the append-only record of belief -> check -> decision -> action -> outcome.

Each entry is typed, validated against the case history, and hash-chained per case so tampering is
detectable. Human decisions, actual actions, observations and verification stay separate records.
The episode export separates decision-time information from hindsight so the ledger can later
serve as a leakage-resistant evaluation or training set without rebuilding anything.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from app.engine import states as case_states

LEDGER_SCHEMA_VERSION = "intervention-ledger-v1"  # v1 entry format; kinds added additively in engine v2.1
EPISODE_SCHEMA_VERSION = "veyra-episode-v3"  # v2 adds trigger, hypothesis statuses, intervention reviews, reproduction attempts; v3 case closures (hindsight)
GENESIS = "GENESIS"

ACTION_TYPES = ("remote_reconfiguration", "remote_restart", "software_update", "network_change", "operating_change",
                "onsite_inspection", "component_replacement", "calibration", "other")
DOMAINS = ("network", "control_suite", "vehicle_interface", "vehicle_body")
SEVERITIES = ("critical", "high", "medium", "low")
INTERVENTION_TYPES = ("takeover", "emergency_stop", "pause", "remote_disconnect", "other")
# What the operator noticed, in a small fixed vocabulary, so later cases with the same complaint can be found even when
# the evidence points somewhere else. Optional; the operator's own words stay in stated_reason.
SYMPTOMS = ("late_response", "no_response", "unexpected_motion", "erratic_motion", "stopped_unexpectedly", "other")
REPRODUCTION_ENVIRONMENTS = ("log_replay", "simulation", "bench", "field")
CONFIRMATION_METHODS = ("inspection", "reproduction", "fix_resolved_it", "evidence_in_case", "engineer_judgement")
CONFIRMING_METHODS = ("inspection", "reproduction", "fix_resolved_it")
# How much a method shows on its own. A fix that resolved it is strong but indirect (it could coincide with something else).
EVIDENCE_STRENGTH = {"inspection": "direct", "reproduction": "direct", "fix_resolved_it": "indirect", "evidence_in_case": "circumstantial",
                     "engineer_judgement": "judgement"}
INVESTIGATION_STATUSES = ("cause_confirmed", "cause_probable", "cause_unknown", "not_reproduced")
RESOLUTION_STATUSES = ("resolved", "temporary_workaround", "not_yet_repaired", "not_resolved", "machine_retired", "handed_over")
FIX_HELD = ("held", "partly", "not_held", "not_yet_known")
MILESTONES = ("issue_reported", "triage_started", "first_evidence_backed_check", "dispatch_requested", "engineer_arrived",
              "service_reported_restored", "case_closed")


class LedgerError(ValueError):
    pass


def _text(payload: Dict[str, Any], key: str, required: bool = True, limit: int = 4000) -> Optional[str]:
    value = payload.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise LedgerError(f"'{key}' is required")
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise LedgerError(f"'{key}' must be text up to {limit} characters")
    return value.strip()


def _choice(payload: Dict[str, Any], key: str, options: Sequence[str], required: bool = True) -> Optional[str]:
    value = payload.get(key)
    if value is None and not required:
        return None
    if value not in options:
        raise LedgerError(f"'{key}' must be one of: {', '.join(options)}")
    return value


def _time(payload: Dict[str, Any], key: str, now: datetime, required: bool = True) -> Optional[str]:
    value = payload.get(key)
    if value is None:
        if required:
            raise LedgerError(f"'{key}' (ISO 8601 with timezone) is required")
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise LedgerError(f"'{key}' is not an ISO 8601 timestamp") from None
    if moment.tzinfo is None:
        raise LedgerError(f"'{key}' needs a timezone offset")
    if moment > now:
        raise LedgerError(f"'{key}' cannot be in the future")
    return moment.isoformat()


def _ids(payload: Dict[str, Any], key: str, allowed: set, label: str) -> List[str]:
    values = payload.get(key) or []
    if not isinstance(values, list) or len(values) > 50 or not all(isinstance(v, str) for v in values):
        raise LedgerError(f"'{key}' must be a list of identifiers")
    unknown = [v for v in values if v not in allowed]
    if unknown:
        raise LedgerError(f"{label} not in this case: {', '.join(unknown[:5])}")
    return values


def _find(entries: Sequence[Dict[str, Any]], entry_id: Optional[str], kind: str) -> Dict[str, Any]:
    for entry in entries:
        if entry["id"] == entry_id and entry["kind"] == kind:
            return entry
    raise LedgerError(f"Linked {kind} '{entry_id}' not found in this case")


def _latest_run(entries: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    runs = [e for e in entries if e["kind"] == "investigation_run"]
    return runs[-1] if runs else None


def _run_entry(entries: Sequence[Dict[str, Any]], run_id: Optional[str]) -> Dict[str, Any]:
    for entry in entries:
        if entry["kind"] == "investigation_run" and entry["payload"].get("run_id") == run_id:
            return entry
    raise LedgerError(f"Investigation run '{run_id}' not found in this case")


def _require_current_run(entries, run_id, state) -> Dict[str, Any]:
    run = _run_entry(entries, run_id)
    latest = _latest_run(entries)
    if latest is None or latest["id"] != run["id"]:
        raise LedgerError("This refers to an older investigation run. Use the latest run.")
    stale = []
    if run["payload"]["input_hashes"] != state.get("input_hashes"):
        stale.append("evidence changed")
    if run["payload"]["context_hash_current"] != state.get("context_hash_current"):
        stale.append("machine context changed")
    if stale:
        raise LedgerError("Run is stale (" + ", ".join(stale) + "). Investigate again before reviewing, executing or deciding.")
    return run


REQUEST_CHANNELS = ("email", "phone", "chat", "ticket", "in_person", "other")


def _exclusion(payload: Dict[str, Any], belief: Dict[str, Any], rules: List[str], run: Dict[str, Any]) -> Dict[str, Any]:
    """What a 'ruled out' verdict covers: named findings (rule|signal|scope) of this run, on these files. Default: the
    findings of the named rules, else every finding of the part at the time. Never the part as such."""
    flagged = belief.get("flagged_keys")
    named = payload.get("excludes")
    if named is not None:
        if not isinstance(named, list) or not all(isinstance(k, str) and k.count("|") == 2 for k in named):
            raise LedgerError("'excludes' must list findings as rule|signal|scope")
        if flagged is not None and any(k not in flagged for k in named):
            raise LedgerError("'excludes' names a finding this run did not flag for this part")
        excluded = named
    elif flagged is not None:
        excluded = [k for k in flagged if not rules or k.split("|")[0] in rules]
    else:  # runs recorded before flagged keys existed
        excluded = [f"{r}|*|*" for r in rules] or ["*"]
    return {"excluded_observations": excluded, "input_hashes": run["payload"].get("input_hashes")}


def _transition(kind: str, entries: Sequence[Dict[str, Any]], run_id: str, check_id: str) -> None:
    try:
        case_states.require_check_transition(kind, entries, run_id, check_id)
    except case_states.StateError as exc:
        raise LedgerError(str(exc)) from None


def validate(kind: str, payload: Dict[str, Any], actor: str, entries: Sequence[Dict[str, Any]], state: Dict[str, Any], now: datetime, *, trusted_automatic: bool = False) -> Dict[str, Any]:
    """Validate a new entry against case history. Returns the normalized payload and links."""
    if not isinstance(payload, dict):
        raise LedgerError("payload must be an object")
    if not isinstance(actor, str) or not actor.strip():
        raise LedgerError("actor is required")
    imports = set(state.get("import_ids", []))
    links: List[str] = []
    out: Dict[str, Any] = {}

    if kind == "investigation_run":
        for key in ("run_id", "run_hash", "input_hashes", "context_hash_current", "belief", "candidates", "goal", "policy_versions"):
            if key not in payload:
                raise LedgerError(f"investigation_run requires '{key}'")
        return {"payload": payload, "links": []}

    if kind == "check_review":
        run = _require_current_run(entries, payload.get("run_id"), state)
        check_id = _text(payload, "check_id")
        _transition(kind, entries, run["payload"]["run_id"], check_id)
        candidates = {c["check_id"]: c for c in run["payload"]["candidates"]}
        if check_id not in candidates:
            raise LedgerError(f"'{check_id}' is not a check proposed by this run")
        verdict = _choice(payload, "verdict", ("accepted", "rejected", "edited"))
        out = {"run_id": run["payload"]["run_id"], "check_id": check_id, "verdict": verdict, "reason": _text(payload, "reason"),
               "proposed": candidates[check_id]}
        if verdict == "edited":
            out["edited_procedure"] = _text(payload, "edited_procedure")
            out["authorization"] = "manual_only_edited_procedures_are_never_auto_executed"
        links = [run["id"]]

    elif kind == "check_execution":
        run = _require_current_run(entries, payload.get("run_id"), state)
        check_id = _text(payload, "check_id")
        if any(e["kind"] == "check_review" and e["payload"]["run_id"] == run["payload"]["run_id"] and e["payload"]["check_id"] == check_id for e in entries):
            _transition(kind, entries, run["payload"]["run_id"], check_id)
        reviews = [e for e in entries if e["kind"] == "check_review" and e["payload"]["run_id"] == run["payload"]["run_id"] and e["payload"]["check_id"] == check_id]
        if not reviews or reviews[-1]["payload"]["verdict"] == "rejected":
            raise LedgerError("Accept or edit this check (with a reason) before recording its execution")
        review = reviews[-1]
        status = _choice(payload, "status", ("performed", "not_performed", "inconclusive", "automatic_read_only"))
        if status == "automatic_read_only" and review["payload"]["verdict"] != "accepted":
            raise LedgerError("Only an accepted, unedited check can run automatically")
        if status == "automatic_read_only" and not trusted_automatic:
            raise LedgerError("Automatic execution results can only be generated by the engine")
        out = {"run_id": run["payload"]["run_id"], "check_id": check_id, "status": status, "review_id": review["id"],
               "procedure": review["payload"].get("edited_procedure") or review["payload"]["proposed"].get("question")}
        if status in ("performed", "inconclusive"):
            out["executed_at"] = _time(payload, "executed_at", now)
            out["performer"] = _text(payload, "performer")
            out["observations"] = _text(payload, "observations")
            out["evidence"] = _ids(payload, "evidence", imports, "Evidence imports")
            if status == "performed" and not out["evidence"] and not payload.get("evidence_note"):
                raise LedgerError("A performed check needs evidence (import IDs) or an evidence_note explaining where the record is")
            out["evidence_note"] = _text(payload, "evidence_note", required=False)
            if payload.get("outcome") is not None:
                from app.engine.next_action import ALIASES, SPLITS
                allowed = tuple(SPLITS.get(check_id, {})) + tuple(ALIASES.get(check_id, {})) or tuple((review["payload"]["proposed"].get("expected") or {}))
                if not allowed:
                    raise LedgerError(f"{check_id} has no named outcomes; describe the result in observations")
                out["outcome"] = _choice(payload, "outcome", allowed)
        elif status == "not_performed":
            out["reason"] = _text(payload, "reason")
        else:
            out["tool_result"] = payload.get("tool_result")
            if not isinstance(out["tool_result"], dict):
                raise LedgerError("Automatic execution must carry its tool result")
        links = [run["id"], review["id"]]

    elif kind == "decision":
        run = _require_current_run(entries, payload.get("run_id"), state)
        options = payload.get("options_considered")
        if not isinstance(options, list) or not options or not all(isinstance(o, str) and o.strip() for o in options):
            raise LedgerError("options_considered must list the options the engineer weighed")
        chosen = _text(payload, "chosen_option")
        if chosen not in options:
            raise LedgerError("chosen_option must be one of options_considered")
        out = {"run_id": run["payload"]["run_id"], "options_considered": options, "chosen_option": chosen, "rationale": _text(payload, "rationale"),
               "belief_at_decision": run["payload"]["belief"], "dispatch_readiness_at_decision": run["payload"].get("dispatch_status"),
               "boundary": "Human decision. Not an executed action and not a verified result."}
        links = [run["id"]]
        if payload.get("proposal_id"):
            proposal = _find(entries, payload["proposal_id"], "decision_proposal")
            checked = _find(entries, payload.get("check_id"), "decision_check") if payload.get("check_id") else None
            if checked and checked["payload"].get("proposal_id") != proposal["id"]:
                raise LedgerError("check_id belongs to a different proposal")
            out.update({"proposal_id": proposal["id"], "check_id": checked["id"] if checked else None,
                        "check_alert": checked["payload"].get("alert") if checked else "not_checked",
                        "changed_from_proposal": bool(payload.get("changed_from_proposal")),
                        "proceed_reason": _text(payload, "proceed_reason", required=False, limit=600)})
            links += [proposal["id"]] + ([checked["id"]] if checked else [])
        elif payload.get("veyra_check_id"):
            # Accepting Veyra's own recommendation: it links the self-check that verified it (an internal record).
            checked = _find(entries, payload["veyra_check_id"], "next_action")
            if checked["payload"].get("stage") != "self_check" or not checked["payload"].get("subject", {}).get("recommendation"):
                raise LedgerError("veyra_check_id must point to Veyra's checked recommendation")
            out.update({"veyra_check_id": checked["id"], "check_alert": checked["payload"].get("alert"),
                        "changed_from_proposal": bool(payload.get("changed_from_proposal")),
                        "proceed_reason": _text(payload, "proceed_reason", required=False, limit=600)})
            links.append(checked["id"])

    elif kind == "request_sent":
        # A person states that an approved request actually went out. Approving a draft is not sending it.
        run = _require_current_run(entries, payload.get("run_id"), state)
        ids = payload.get("check_ids")
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
            raise LedgerError("check_ids must list the checks the request asks for")
        reviews = []
        for check_id in ids:
            found = [e for e in entries if e["kind"] == "check_review" and e["payload"]["run_id"] == run["payload"]["run_id"] and e["payload"]["check_id"] == check_id]
            if not found or found[-1]["payload"]["verdict"] == "rejected":
                raise LedgerError(f"Approve {check_id} before recording that the request was sent")
            _transition(kind, entries, run["payload"]["run_id"], check_id)
            reviews.append(found[-1]["id"])
        out = {"run_id": run["payload"]["run_id"], "check_ids": ids, "channel": _choice(payload, "channel", REQUEST_CHANNELS),
               "sent_to": _text(payload, "sent_to", limit=200), "sent_at": _time(payload, "sent_at", now),
               "reference": _text(payload, "reference", required=False, limit=300),
               "boundary": "Stated by the person who sent it. Veyra did not send it and has not seen a reply."}
        links = [run["id"]] + reviews

    elif kind == "decision_proposal":
        # What the engineer proposes to do, before it is committed. Only explicit statements are stored as claims,
        # each with its provenance: engineer_stated, or system_suggested_confirmed (a Veyra prefill the engineer kept).
        from app.engine.decision_check import ACTIONS, AGENT_ACTIONS, AGENT_PROVENANCE, PURPOSES, PROVENANCE
        by_agent = bool(trusted_automatic)  # only the engine's own path can file a proposal as Veyra's
        actions, provenance = (AGENT_ACTIONS, PROVENANCE + AGENT_PROVENANCE) if by_agent else (ACTIONS, PROVENANCE)
        run = _run_entry(entries, payload.get("run_id"))
        parts = {b["domain"] for b in run["payload"].get("belief") or []} or {"network", "control_suite", "vehicle_interface", "vehicle_body"}
        def part(value, key):
            if value is None:
                return None
            if value not in parts:
                raise LedgerError(f"'{key}' must be one of: {', '.join(sorted(parts))}")
            return value
        out = {"run_id": run["payload"]["run_id"], "proposed_by": "veyra" if by_agent else "engineer",
               "action": _choice(payload, "action", actions), "purpose": _choice(payload, "purpose", PURPOSES),
               "check_id": _text(payload, "check_id", required=False, limit=80), "recommendation": bool(payload.get("recommendation")) and by_agent,
               "urgent": bool(payload.get("urgent")), "target_part": part(payload.get("target_part"), "target_part"),
               "note": _text(payload, "note", required=False, limit=600)}
        h = payload.get("hypothesis")
        if h:
            if not isinstance(h, dict):
                raise LedgerError("hypothesis must be an object")
            out["hypothesis"] = {"part": part(h.get("part"), "hypothesis.part"), "provenance": _choice(h, "provenance", provenance),
                                 "text": _text(h, "text", required=False, limit=300)}
        relies = payload.get("relies_on") or []
        if not isinstance(relies, list) or len(relies) > 6:
            raise LedgerError("relies_on must be a list of at most 6 findings")
        out["relies_on"] = [{"domain": part(r.get("domain"), "relies_on.domain"), "rule": _text(r, "rule", limit=80), "text": _text(r, "text", limit=600),
                             "use": _choice(r, "use", ("supports", "rules_out")), "part": part(r.get("part"), "relies_on.part"),
                             "provenance": _choice(r, "provenance", provenance)} for r in relies]
        out["boundary"] = "A proposal, not a decision. Only the hypothesis and findings listed here are the engineer's statements."
        links = [run["id"]]

    elif kind == "dispatch_preparation":
        run = _require_current_run(entries, payload.get("run_id"), state)
        out = {"run_id": run["payload"]["run_id"], "remote_resolution": _choice(payload, "remote_resolution", ("unknown", "possible", "not_possible", "reported_resolved")),
               "prepared": [str(p)[:300] for p in payload.get("prepared", [])][:30],
               "blockers": [str(b)[:300] for b in payload.get("blockers", [])][:30],
               "note": _text(payload, "note", required=False),
               "boundary": "Engineer-reported preparation. Not dispatch approval and not a confirmed failed part."}
        links = [run["id"]]

    elif kind == "action":
        decision = _find(entries, payload.get("decision_id"), "decision")
        status = _choice(payload, "status", ("performed", "simulated", "not_performed"))
        out = {"decision_id": decision["id"], "action_type": _choice(payload, "action_type", ACTION_TYPES),
               "description": _text(payload, "description"), "status": status}
        if status in ("performed", "simulated"):
            out["performed_at"] = _time(payload, "performed_at", now)
            out["performer"] = _text(payload, "performer")
        else:
            out["reason"] = _text(payload, "reason")
        links = [decision["id"]]

    elif kind == "outcome":
        action = _find(entries, payload.get("action_id"), "action")
        if action["payload"]["status"] == "not_performed":
            raise LedgerError("An outcome must follow an action that was performed (or explicitly simulated)")
        result = _choice(payload, "result", ("recovered", "partially_recovered", "not_recovered", "unknown"))
        start = _time(payload, "observed_from", now)
        end = _time(payload, "observed_to", now)
        if datetime.fromisoformat(end) <= datetime.fromisoformat(start):
            raise LedgerError("observed_to must be after observed_from")
        if action["payload"].get("performed_at") and datetime.fromisoformat(start) < datetime.fromisoformat(action["payload"]["performed_at"]):
            raise LedgerError("The observation window cannot start before the action")
        verification = _choice(payload, "verification", ("unverified", "evidence_attached"))
        evidence = _ids(payload, "evidence", imports, "Evidence imports")
        if verification == "evidence_attached" and not evidence:
            raise LedgerError("evidence_attached needs at least one import covering the observation window")
        sample_times = state.get("import_sample_times", {})
        start_t, end_t = datetime.fromisoformat(start).timestamp(), datetime.fromisoformat(end).timestamp()
        # A file's bounding span can hide an entirely unsampled observation window.
        if verification == "evidence_attached" and not any(
                start_t <= timestamp <= end_t for import_id in evidence for timestamp in sample_times.get(import_id, [])):
            raise LedgerError("None of the attached imports has records inside the observation window")
        out = {"action_id": action["id"], "result": result, "observed_from": start, "observed_to": end, "verification": verification,
               "evidence": evidence, "observations": _text(payload, "observations"), "simulated": action["payload"]["status"] == "simulated",
               "verification_grade": "engineer_reported_not_independently_reviewed"}
        links = [action["id"]]

    elif kind == "outcome_review":
        outcome = _find(entries, payload.get("outcome_id"), "outcome")
        if outcome["actor"].strip().casefold() == actor.strip().casefold():
            raise LedgerError("An outcome review needs a different person from the one who recorded the outcome")
        out = {"outcome_id": outcome["id"], "verdict": _choice(payload, "verdict", ("accepted", "rejected")), "reason": _text(payload, "reason"),
               "boundary": "Second-person review; identities are not authenticated in the local tool"}
        links = [outcome["id"]]

    elif kind == "recurrence_check":
        outcome = _find(entries, payload.get("outcome_id"), "outcome")
        start = _time(payload, "window_from", now)
        end = _time(payload, "window_to", now)
        if datetime.fromisoformat(end) <= datetime.fromisoformat(start):
            raise LedgerError("window_to must be after window_from")
        out = {"outcome_id": outcome["id"], "window_from": start, "window_to": end,
               "recurred": _choice(payload, "recurred", ("yes", "no", "unknown")), "evidence": _ids(payload, "evidence", imports, "Evidence imports"),
               "note": _text(payload, "note", required=False)}
        links = [outcome["id"]]

    elif kind == "intervention_report":
        # A person stopped, paused or took over the machine. Opens or enriches a case; never a verdict.
        out = {"occurred_at": _time(payload, "occurred_at", now), "intervention_type": _choice(payload, "intervention_type", INTERVENTION_TYPES),
               "operator": _text(payload, "operator", limit=200), "operator_role": _text(payload, "operator_role", required=False, limit=200),
               "stated_reason": _text(payload, "stated_reason"), "reported_via": _text(payload, "reported_via", required=False, limit=300),
               "symptom": _choice(payload, "symptom", SYMPTOMS, required=False),
               "affected_function": (_text(payload, "affected_function", required=False, limit=100) or "").strip().lower() or None,
               "boundary": "The operator's stated reason at the time. Not established as correct or incorrect."}

    elif kind == "intervention_review":
        intervention = _find(entries, payload.get("intervention_id"), "intervention_report")
        run = _require_current_run(entries, payload.get("run_id"), state)
        out = {"intervention_id": intervention["id"], "run_id": run["payload"]["run_id"],
               "verdict": _choice(payload, "verdict", ("necessary", "unnecessary", "unclear")), "reason": _text(payload, "reason"),
               "boundary": "Engineer judgement on the recorded evidence. 'Unnecessary' interventions are kept as cases worth recreating."}
        links = [intervention["id"], run["id"]]

    elif kind == "reference_declared":
        # The known-good run the case is compared against. Without it, comparisons rest on an assumption.
        start = _time(payload, "window_from", now)
        end = _time(payload, "window_to", now)
        start_t, end_t = datetime.fromisoformat(start).timestamp(), datetime.fromisoformat(end).timestamp()
        if end_t <= start_t:
            raise LedgerError("window_to must be after window_from")
        symptom = state.get("symptom_window")
        if symptom and start_t < symptom[1] and end_t > symptom[0]:
            raise LedgerError("The reference window overlaps the symptom window; a known-good reference must lie outside it")
        covered = [i for i, times in state.get("import_sample_times", {}).items() if any(start_t <= t <= end_t for t in times)]
        if not covered:
            raise LedgerError("No imported records fall inside the reference window")
        out = {"window_from": start, "window_to": end, "basis": _text(payload, "basis"),
               "source": _text(payload, "reference_source", required=False, limit=300), "covered_by_imports": sorted(covered),
               "boundary": "Declared known-good by a person. The engine compares against it; it does not prove it was healthy."}

    elif kind == "triage":
        run_id = payload.get("run_id")
        if run_id is not None:
            _run_entry(entries, run_id)
        previous = [e for e in entries if e["kind"] == "triage"]
        owner = _text(payload, "owner", limit=200)
        prior_owner = previous[-1]["payload"]["owner"] if previous else None
        out = {"severity": _choice(payload, "severity", SEVERITIES), "owner": owner, "reason": _text(payload, "reason"),
               "run_id": run_id, "previous_owner": prior_owner, "handoff": prior_owner is not None and prior_owner.casefold() != owner.casefold(),
               "boundary": "Human triage. Severity and ownership can change as evidence arrives; every change stays in the record."}

    elif kind == "hypothesis_status":
        run = _require_current_run(entries, payload.get("run_id"), state)
        domain = _choice(payload, "domain", DOMAINS)
        status = _choice(payload, "status", ("ruled_out", "weakened", "supported", "reopened"))
        allowed = {e["id"] for e in entries if e["kind"] in ("check_execution", "reproduction_attempt")}
        evidence_entries = _ids(payload, "evidence_entries", allowed, "Check executions or reproduction attempts")
        belief = {b["domain"]: b for b in run["payload"]["belief"]}
        rules = payload.get("finding_rules") or []
        if not isinstance(rules, list) or not all(isinstance(r, str) for r in rules):
            raise LedgerError("'finding_rules' must be a list of rule identifiers")
        unknown = [r for r in rules if r not in belief.get(domain, {}).get("observation_rules", [])]
        if unknown:
            raise LedgerError(f"Rules not observed for {domain} in this run: {', '.join(unknown)}")
        if status in ("ruled_out", "weakened", "supported") and not evidence_entries and not rules:
            raise LedgerError(f"'{status}' needs evidence: check executions, reproduction attempts or findings from this run")
        out = {"run_id": run["payload"]["run_id"], "domain": domain, "status": status, "basis": _text(payload, "basis"),
               "scope": _text(payload, "scope", required=status in ("ruled_out", "weakened"), limit=1000),
               "evidence_entries": evidence_entries, "finding_rules": rules, "engine_status_at_review": belief.get(domain, {}).get("status"),
               **(_exclusion(payload, belief.get(domain, {}), rules, run) if status == "ruled_out" else {}),
               "boundary": "A person's judgement about one domain, limited to the stated scope. Ruled out here means: not supported by what was examined."}
        links = [run["id"], *evidence_entries]

    elif kind == "reproduction_attempt":
        run = _require_current_run(entries, payload.get("run_id"), state)
        out = {"run_id": run["payload"]["run_id"], "environment": _choice(payload, "environment", REPRODUCTION_ENVIRONMENTS),
               "platform": _text(payload, "platform", limit=300), "setup": _text(payload, "setup"),
               "result": _choice(payload, "result", ("reproduced", "partially_reproduced", "not_reproduced", "inconclusive")),
               "observations": _text(payload, "observations"), "performed_at": _time(payload, "performed_at", now),
               "evidence": _ids(payload, "evidence", imports, "Evidence imports"), "evidence_note": _text(payload, "evidence_note", required=False),
               "boundary": "Reproducing a symptom supports a mechanism; it does not prove it was the cause in the field."}
        links = [run["id"]]

    elif kind in ("acquisition", "agent_report", "trigger_event", "next_action", "decision_check", "evidence_discovery"):
        # Machine-generated records: only the engine's own pipeline may write them.
        if not trusted_automatic:
            raise LedgerError(f"'{kind}' records can only be generated by Veyra's connectors and agent")
        out = dict(payload)
        if kind in ("agent_report", "next_action", "evidence_discovery"):
            run = _require_current_run(entries, payload.get("run_id"), state)
            links = [run["id"]]
        if kind == "decision_check":
            links = [_run_entry(entries, payload.get("run_id"))["id"], _find(entries, payload.get("proposal_id"), "decision_proposal")["id"]]

    elif kind == "model_authorization":
        # Written permission to send this case's (non-synthetic) data to a named model provider.
        out = {"provider": _choice(payload, "provider", ("anthropic", "openai_compatible", "local")), "model": _text(payload, "model", required=False, limit=200),
               "scope": _text(payload, "scope"), "authorized_by": _text(payload, "authorized_by", limit=300), "evidence": _text(payload, "evidence"),
               "boundary": "Covers only the provider and scope stated. Recorded by a person; not verified by Veyra."}

    elif kind == "event_note":
        # One click on an event Veyra classified: "normal for this machine" (or "relevant after all"). Applies at once,
        # by the person who knows the machine; the latest note on a pattern wins. Read by the investigator next time.
        lines = payload.get("lines") or []
        if not isinstance(lines, list) or not all(isinstance(n, int) for n in lines):
            raise LedgerError("lines must be a list of line numbers")
        out = {"pattern": _text(payload, "pattern", limit=400), "verdict": _choice(payload, "verdict", ("normal", "relevant")),
               "scope": _choice(payload, "scope", ("case", "machine")), "lines": lines[:50], "note": _text(payload, "note", required=False, limit=500)}

    elif kind == "engineer_finding":
        # What an engineer found when doing the next action Veyra proposed (or any check of their own). It feeds the
        # next investigation step; it is the engineer's report, not an independently verified measurement.
        out = {"action": _text(payload, "action", limit=500), "result": _choice(payload, "result", ("found", "not_found", "cannot_do", "other")),
               "observations": _text(payload, "observations", required=payload.get("result") in ("found", "other"), limit=2000),
               "investigation_entry": _text(payload, "investigation_entry", required=False, limit=80),
               "verification_grade": "engineer_reported_not_independently_reviewed"}

    elif kind == "case_closure":
        # The engineer's conclusion: the unit of engineering experience later cases reuse. What is known about the
        # CAUSE (investigation status, with the strength of the evidence) is kept apart from what happened to the
        # MACHINE (resolution status): a case can close with the cause unknown, or with the cause found and not yet repaired.
        run_id = payload.get("run_id")
        run = _run_entry(entries, run_id) if run_id is not None else _latest_run(entries)
        evidence_kinds = ("check_execution", "reproduction_attempt", "outcome", "hypothesis_status", "outcome_review")
        allowed = {e["id"] for e in entries if e["kind"] in evidence_kinds}
        hours = payload.get("hours_to_cause")
        if hours is not None and (not isinstance(hours, (int, float)) or isinstance(hours, bool) or not 0 <= hours <= 24 * 365):
            raise LedgerError("hours_to_cause must be a number of hours (0 to 8760)")
        previous = [e for e in entries if e["kind"] == "case_closure"]
        status = _choice(payload, "investigation_status", INVESTIGATION_STATUSES)
        has_cause = status in ("cause_confirmed", "cause_probable")
        method = _choice(payload, "confirmed_by", CONFIRMATION_METHODS, required=has_cause)
        evidence_entries = _ids(payload, "evidence_entries", allowed, "Check executions, reproductions, outcomes or reviews")
        evidence_note = _text(payload, "evidence_note", required=False, limit=1000)
        if method in ("inspection", "reproduction", "fix_resolved_it") and not evidence_entries and not evidence_note:
            raise LedgerError(f"'{method}' needs the record that shows it: link an entry of this case or describe it in evidence_note")
        if status == "cause_confirmed" and method not in CONFIRMING_METHODS:
            raise LedgerError("'Confirmed' needs inspection, reproduction or a fix that resolved it; "
                              "evidence in the case or an engineer's judgement supports 'probable'")
        if status == "cause_confirmed":
            try:
                case_states.require_confirming_record(method, payload.get("cause_domain"), evidence_entries, entries, evidence_note,
                                                     synthetic=bool(state.get("synthetic")))
            except case_states.StateError as exc:
                raise LedgerError(str(exc)) from None
        resolution = _choice(payload, "resolution_status", RESOLUTION_STATUSES)
        fix = _text(payload, "fix", required=False, limit=1000)
        out = {"run_id": run["payload"]["run_id"] if run else None, "investigation_status": status,
               "cause_domain": _choice(payload, "cause_domain", DOMAINS, required=has_cause), "cause": _text(payload, "cause", required=has_cause, limit=1000),
               "confirmed_by": method, "evidence_strength": EVIDENCE_STRENGTH.get(method) if method else None,
               "evidence_entries": evidence_entries, "evidence_note": evidence_note,
               "resolution_status": resolution, "fix": fix,
               "fix_held": _choice(payload, "fix_held", FIX_HELD, required=False) or ("not_yet_known" if fix else None),
               "handed_over_to": _text(payload, "handed_over_to", required=resolution == "handed_over", limit=300),
               "decisive_check": _text(payload, "decisive_check", required=False, limit=200),
               "applies_when": _text(payload, "applies_when", required=False, limit=1000),
               "hours_to_cause": hours, "would_have_saved_time": _text(payload, "would_have_saved_time", required=False, limit=2000),
               "engine_status_at_closure": {b["domain"]: b["status"] for b in run["payload"]["belief"]} if run else None,
               "supersedes": previous[-1]["id"] if previous else None,
               "boundary": "The engineer's conclusion, with the stated method and evidence strength. 'Probable' is not proof. "
                           "A later conclusion can supersede it; both stay on record."}
        links = ([run["id"]] if run else []) + evidence_entries + ([previous[-1]["id"]] if previous else [])

    elif kind == "normal_proposal":
        # "This is normal for this machine": a person proposes that an observation is expected behaviour.
        # It changes nothing until a second person approves it (normal_review).
        run = _require_current_run(entries, payload.get("run_id"), state)
        key = payload.get("observation_key")
        if not isinstance(key, dict) or not isinstance(key.get("rule"), str):
            raise LedgerError("observation_key must name the finding's rule (and its signal and scope)")
        observed = {r for b in run["payload"]["belief"] for r in b.get("observation_rules", [])}
        if key["rule"] not in observed:
            raise LedgerError(f"Rule {key['rule']} was not observed in this run")
        clean = {k: str(key[k])[:300] for k in ("rule", "signal", "scope", "value") if key.get(k) is not None}
        scope = _choice(payload, "scope", ("machine", "machine_type"))
        out = {"run_id": run["payload"]["run_id"], "observation_key": clean, "observation_text": _text(payload, "observation_text", limit=1000),
               "scope": scope, "machine_class": _text(payload, "machine_class", required=scope == "machine_type", limit=100),
               "reason": _text(payload, "reason", limit=1000),
               "boundary": "Proposal only. It applies to later investigations once a different person approves it."}
        links = [run["id"]]

    elif kind == "normal_review":
        proposal = _find(entries, payload.get("proposal_id"), "normal_proposal")
        if proposal["actor"].strip().casefold() == actor.strip().casefold():
            raise LedgerError("A different person must review a 'normal for this machine' proposal")
        out = {"proposal_id": proposal["id"], "verdict": _choice(payload, "verdict", ("approved", "rejected")), "reason": _text(payload, "reason"),
               "boundary": "Second-person review; identities are not authenticated in the local tool"}
        links = [proposal["id"]]

    elif kind == "support_milestone":
        out = {"milestone": _choice(payload, "milestone", MILESTONES), "observed_at": _time(payload, "observed_at", now),
               "note": _text(payload, "note", required=False)}

    else:
        raise LedgerError(f"Unknown ledger entry kind '{kind}'")
    return {"payload": out, "links": links}


def entry_hash(entry: Dict[str, Any]) -> str:
    body = {k: entry[k] for k in ("schema", "id", "case_id", "kind", "actor", "source", "recorded_at", "payload", "links", "prev_hash")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def build_entry(*, entry_id: str, case_id: str, kind: str, actor: str, source: str, recorded_at: datetime, payload: Dict[str, Any],
                links: List[str], prev_hash: str) -> Dict[str, Any]:
    entry = {"schema": LEDGER_SCHEMA_VERSION, "id": entry_id, "case_id": case_id, "kind": kind, "actor": actor.strip(),
             "source": source.strip() if source else "", "recorded_at": recorded_at.isoformat(), "payload": payload, "links": links, "prev_hash": prev_hash}
    entry["hash"] = entry_hash(entry)
    return entry


def verify_chain(entries: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    previous = GENESIS
    for index, entry in enumerate(entries):
        if entry["prev_hash"] != previous:
            return {"valid": False, "broken_at": index, "entry_id": entry["id"], "reason": "previous-hash link mismatch"}
        if entry_hash(entry) != entry["hash"]:
            return {"valid": False, "broken_at": index, "entry_id": entry["id"], "reason": "entry content does not match its hash"}
        previous = entry["hash"]
    return {"valid": True, "entries": len(entries), "head": previous}


def episodes(case: Dict[str, Any], entries: Sequence[Dict[str, Any]], runs: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One episode per investigation run, with decision-time information separated from hindsight."""
    out = []
    run_entries = [e for e in entries if e["kind"] == "investigation_run"]
    for position, run_entry in enumerate(run_entries):
        next_time = run_entries[position + 1]["recorded_at"] if position + 1 < len(run_entries) else None
        run_id = run_entry["payload"]["run_id"]
        related = [e for e in entries if e["payload"].get("run_id") == run_id]
        decisions = [e for e in related if e["kind"] == "decision"]
        decision_ids = {d["id"] for d in decisions}
        actions = [e for e in entries if e["kind"] == "action" and e["payload"]["decision_id"] in decision_ids]
        action_ids = {a["id"] for a in actions}
        outcomes = [e for e in entries if e["kind"] == "outcome" and e["payload"]["action_id"] in action_ids]
        outcome_ids = {o["id"] for o in outcomes}
        run = runs.get(run_id, {})
        out.append({
            "schema": EPISODE_SCHEMA_VERSION, "case_id": case["id"], "machine_id": case["machine_id"], "fault_type": case.get("fault_type"),
            "synthetic": bool(case.get("synthetic")),
            "decision_time": {
                "run_id": run_id, "run_hash": run_entry["payload"]["run_hash"], "recorded_at": run_entry["recorded_at"],
                "context_snapshot": run.get("context", {}).get("incident"), "input_hashes": run_entry["payload"]["input_hashes"],
                "policy_versions": run_entry["payload"]["policy_versions"], "goal": run_entry["payload"]["goal"],
                "belief": run_entry["payload"]["belief"], "candidates": run_entry["payload"]["candidates"],
                "findings_digest": run.get("findings_digest"),
            },
            "trigger": {"intervention_reports": [_slim(e) for e in entries if e["kind"] == "intervention_report" and e["recorded_at"] <= run_entry["recorded_at"]],
                        "reference_declarations": [_slim(e) for e in entries if e["kind"] == "reference_declared" and e["recorded_at"] <= run_entry["recorded_at"]],
                        "triage_before_run": [_slim(e) for e in entries if e["kind"] == "triage" and e["recorded_at"] <= run_entry["recorded_at"]]},
            "human": {"reviews": [_slim(e) for e in related if e["kind"] == "check_review"],
                      "hypothesis_statuses": [_slim(e) for e in related if e["kind"] == "hypothesis_status"],
                      "intervention_reviews": [_slim(e) for e in related if e["kind"] == "intervention_review"],
                      "decisions": [_slim(e) for e in decisions],
                      "proposals": [_slim(e) for e in related if e["kind"] == "decision_proposal" and e["payload"].get("proposed_by") != "veyra"],
                      "dispatch_preparation": [_slim(e) for e in related if e["kind"] == "dispatch_preparation"]},
            "agent_internal": {"note": "Veyra's own attempts and self-checks. Audit only; not engineering decisions.",
                               "count": len([e for e in related if e["kind"] in ("next_action", "decision_check", "agent_report")
                                             or (e["kind"] == "decision_proposal" and e["payload"].get("proposed_by") == "veyra")])},
            "requests_sent": [_slim(e) for e in related if e["kind"] == "request_sent"],
            "interventions": {"check_executions": [_slim(e) for e in related if e["kind"] == "check_execution"],
                              "reproduction_attempts": [_slim(e) for e in related if e["kind"] == "reproduction_attempt"],
                              "actions": [_slim(e) for e in actions]},
            "hindsight": {"note": "Never use as decision-time features.",
                          "outcomes": [_slim(e) for e in outcomes],
                          "outcome_reviews": [_slim(e) for e in entries if e["kind"] == "outcome_review" and e["payload"]["outcome_id"] in outcome_ids],
                          "recurrence_checks": [_slim(e) for e in entries if e["kind"] == "recurrence_check" and e["payload"]["outcome_id"] in outcome_ids],
                          "case_closures": [_slim(e) for e in entries if e["kind"] == "case_closure" and e["recorded_at"] >= run_entry["recorded_at"]],
                          "superseded_by_run_at": next_time},
        })
    return out


def _slim(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": entry["id"], "kind": entry["kind"], "actor": entry["actor"], "recorded_at": entry["recorded_at"], "payload": entry["payload"], "hash": entry["hash"]}
