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

LEDGER_SCHEMA_VERSION = "intervention-ledger-v1"
EPISODE_SCHEMA_VERSION = "veyra-episode-v1"
GENESIS = "GENESIS"

ACTION_TYPES = ("remote_reconfiguration", "remote_restart", "software_update", "network_change", "operating_change",
                "onsite_inspection", "component_replacement", "calibration", "other")
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


def validate(kind: str, payload: Dict[str, Any], actor: str, entries: Sequence[Dict[str, Any]], state: Dict[str, Any], now: datetime) -> Dict[str, Any]:
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
        reviews = [e for e in entries if e["kind"] == "check_review" and e["payload"]["run_id"] == run["payload"]["run_id"] and e["payload"]["check_id"] == check_id]
        if not reviews or reviews[-1]["payload"]["verdict"] == "rejected":
            raise LedgerError("Accept or edit this check (with a reason) before recording its execution")
        review = reviews[-1]
        status = _choice(payload, "status", ("performed", "not_performed", "inconclusive", "automatic_read_only"))
        if status == "automatic_read_only" and review["payload"]["verdict"] != "accepted":
            raise LedgerError("Only an accepted, unedited check can run automatically")
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
        coverage = state.get("import_spans", {})
        if verification == "evidence_attached" and not any(coverage.get(i) and coverage[i][0] <= datetime.fromisoformat(end).timestamp()
                                                           and coverage[i][1] >= datetime.fromisoformat(start).timestamp() for i in evidence):
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
            "human": {"reviews": [_slim(e) for e in related if e["kind"] == "check_review"],
                      "decisions": [_slim(e) for e in decisions],
                      "dispatch_preparation": [_slim(e) for e in related if e["kind"] == "dispatch_preparation"]},
            "interventions": {"check_executions": [_slim(e) for e in related if e["kind"] == "check_execution"],
                              "actions": [_slim(e) for e in actions]},
            "hindsight": {"note": "Never use as decision-time features.",
                          "outcomes": [_slim(e) for e in outcomes],
                          "outcome_reviews": [_slim(e) for e in entries if e["kind"] == "outcome_review" and e["payload"]["outcome_id"] in outcome_ids],
                          "recurrence_checks": [_slim(e) for e in entries if e["kind"] == "recurrence_check" and e["payload"]["outcome_id"] in outcome_ids],
                          "superseded_by_run_at": next_time},
        })
    return out


def _slim(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": entry["id"], "kind": entry["kind"], "actor": entry["actor"], "recorded_at": entry["recorded_at"], "payload": entry["payload"], "hash": entry["hash"]}
