"""Decision Check on a stored case: record the proposal, check it, store the check. Rules: app/engine/decision_check.py.

proposed (decision_proposal, by a person) -> checked (decision_check, by the engine) -> committed or changed
(decision, linking both) -> outcome (outcome / case_closure, later). Each is its own ledger record.
"""
from __future__ import annotations

from typing import Any, Dict

from app.engine import decision_check as rules
from app.engine import next_action as na

ACTOR = "Veyra decision check"


def _run_current(store, case_id: str) -> bool:
    ledger = store.ledger(case_id)
    runs = [e for e in ledger if e["kind"] == "investigation_run"]
    if not runs:
        return False
    state = store._state(store.case(case_id))
    return runs[-1]["payload"]["input_hashes"] == state["input_hashes"] and runs[-1]["payload"]["context_hash_current"] == state["context_hash_current"]


def suggest(store, case_id: str) -> Dict[str, Any]:
    """Prefill for the proposal form. Everything here is marked as Veyra's suggestion until the engineer keeps or changes it."""
    run = store.latest_run(case_id)
    if run is None:
        return {"available": False, "reason": "Investigate the case first."}
    assessment = na.assess(run)
    ledger = store.ledger(case_id)
    report = next((e["payload"] for e in reversed(ledger) if e["kind"] == "next_action" and e["payload"].get("run_id") == run["id"]
                   and e["payload"].get("stage") == "report"), None)
    lead = None
    if report and report.get("leading"):
        lead = next((d for d in run["domains"] if na._label(run, d) == report["leading"]), None)
    elif len(assessment["supported"]) == 1:
        lead = assessment["supported"][0]["part"]
    findings = [{"domain": d, "rule": o.get("rule"), "text": o["text"], "kind": o["kind"]}
                for d, a in run["domains"].items() for o in a["observations"] if o.get("rule")]
    return {"available": True, "run_id": run["id"], "actions": list(rules.ACTIONS), "purposes": list(rules.PURPOSES),
            "parts": [{"part": d, "label": na._label(run, d)} for d in run["domains"]],
            "suggested_hypothesis": lead, "suggested_basis": ("Next action report" if report and report.get("leading") else "only part with evidence") if lead else None,
            "findings": findings}


def check(store, case_id: str, proposal: Dict[str, Any], *, actor: str) -> Dict[str, Any]:
    run = store.latest_run(case_id)
    if run is None:
        raise ValueError("Investigate the case first")
    entry = store.append(case_id, "decision_proposal", {**proposal, "run_id": run["id"]}, actor=actor, source="decision check")
    try:
        current = _run_current(store, case_id)
        result = rules.check(run, entry["payload"], store.ledger(case_id), run_current=current)
    except Exception as error:  # a failed check must be recorded as not assessed, never as a pass
        result = rules.check(run, entry["payload"], [], unavailable=f"The check failed: {type(error).__name__}")
    stored = store.append_trusted(case_id, "decision_check", {**result, "proposal_id": entry["id"], "run_id": run["id"]},
                                  actor=ACTOR, source=f"veyra://decision-check/{rules.POLICY}")
    return {"proposal": entry, "check": stored}
