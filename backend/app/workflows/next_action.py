"""The next-action flow on a stored case: state, plan, report. Rules live in app/engine/next_action.py.

Every step is a ledger record:
  plan      trusted `next_action` (stage "plan"): the assessment and the 1-2 proposed checks
  approve   `check_review` (accepted / edited, with a reason) by a person
  result    `check_execution` (performed, with outcome, observations, who, where the record is) from the site
  report    trusted `next_action` (stage "report"): the dispatch readiness report
"""
from __future__ import annotations

from app.engine import checks as check_policy

from typing import Any, Dict, List, Optional

from app.engine import next_action as rules

ACTOR = "Veyra next-action"


def _for_run(ledger: List[Dict[str, Any]], run_id: str, stage: str) -> Optional[Dict[str, Any]]:
    found = [e for e in ledger if e["kind"] == "next_action" and e["payload"].get("run_id") == run_id and e["payload"].get("stage") == stage]
    return found[-1] if found else None


def state(store, case_id: str) -> Dict[str, Any]:
    run = store.latest_run(case_id)
    if run is None:
        return {"step": 0, "note": "Investigate the case first."}
    ledger = store.ledger(case_id)
    plan_entry = _for_run(ledger, run["id"], "plan")
    report_entry = _for_run(ledger, run["id"], "report")
    assessment = plan_entry["payload"]["assessment"] if plan_entry else rules.assess(run)
    checks = plan_entry["payload"]["checks"] if plan_entry else []
    for check in checks:
        reviews = [e for e in ledger if e["kind"] == "check_review" and e["payload"]["run_id"] == run["id"] and e["payload"]["check_id"] == check["check_id"]]
        runs = [e for e in ledger if e["kind"] == "check_execution" and e["payload"]["run_id"] == run["id"] and e["payload"]["check_id"] == check["check_id"]]
        check["approval"] = ({"verdict": reviews[-1]["payload"]["verdict"], "by": reviews[-1]["actor"], "reason": reviews[-1]["payload"]["reason"],
                              "at": reviews[-1]["recorded_at"]} if reviews else None)
        check["result"] = ({**{k: runs[-1]["payload"].get(k) for k in ("status", "outcome", "observations", "performer", "executed_at", "evidence_note")},
                            "entry_id": runs[-1]["id"]} if runs else None)
    earlier = [{"check_id": cid, **{k: r.get(k) for k in ("outcome", "observations", "performer", "evidence_note")}, "entry_id": r["entry_id"]}
               for cid, r in rules.site_results(ledger, run["id"]).items() if not r["this_run"]]
    step = 3 if report_entry else 2 if plan_entry else 1
    return {"step": step, "run_id": run["id"], "policy": rules.POLICY, "assessment": assessment, "checks": checks,
            "plan_entry": plan_entry["id"] if plan_entry else None, "report": report_entry["payload"] if report_entry else None,
            "report_entry": report_entry["id"] if report_entry else None,
            "ready_to_decide": bool(checks) and all(c.get("result") for c in checks), "earlier_results": earlier}


def nothing_to_ask(run: Dict[str, Any], assessment: Dict[str, Any], checks: List[Dict[str, Any]], earlier: Dict[str, Any]) -> Optional[str]:
    """Why no site check is proposed, or None when there is one (or earlier site results to build on)."""
    if checks or any(not r["this_run"] for r in earlier.values()):
        return None
    info = [c["question"] for c in run.get("checks") or [] if check_policy.eligible(c) and c.get("mode") != "automatic_read_only"
            and c["availability"]["state"] != "onsite_required" and c["objective"] == "reduce_uncertainty"][:3]
    return ("No remote check's result would change which of " + (" and ".join(x["label"] for x in assessment["cannot_tell_apart"]) or "the open explanations")
            + " is more likely. " + (f"Checks like '{info[0]}' add context but don't move that balance. " if info else "")
            + "The next step is on site, or a decision.")


def make_plan(store, case_id: str) -> Dict[str, Any]:
    run = store.latest_run(case_id)
    if run is None:
        raise ValueError("Investigate the case first")
    existing = _for_run(store.ledger(case_id), run["id"], "plan")
    if existing:
        return state(store, case_id)
    assessment = rules.assess(run)
    earlier = rules.site_results(store.ledger(case_id), run["id"])
    checks = rules.plan(run, assessment, results=earlier)
    comparison = rules.compare(run, assessment, earlier)
    reason = nothing_to_ask(run, assessment, checks, earlier)
    if reason:
        raise ValueError(reason)
    store.append_trusted(case_id, "next_action", {"stage": "plan", "run_id": run["id"], "policy": rules.POLICY, "assessment": assessment, "checks": checks,
                                                  "comparison": comparison},
                         actor=ACTOR, source=f"veyra://next-action/{rules.POLICY}")
    return state(store, case_id)


def make_report(store, case_id: str) -> Dict[str, Any]:
    current = state(store, case_id)
    if current["step"] < 2:
        raise ValueError("Propose the next checks first")
    run = store.latest_run(case_id)
    ledger = store.ledger(case_id)
    results = [{**r, "check_id": cid} for cid, r in rules.site_results(ledger, run["id"]).items()]
    if not any(r.get("status") == "performed" for r in results):
        raise ValueError("Record at least one site result before deciding")
    report = rules.decide(run, current["assessment"], current["checks"], results)
    store.append_trusted(case_id, "next_action", {"stage": "report", **report, "plan_entry": current["plan_entry"]},
                         actor=ACTOR, source=f"veyra://next-action/{rules.POLICY}")
    return state(store, case_id)
