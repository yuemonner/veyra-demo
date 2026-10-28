"""Autopilot: Veyra moves a case forward on its own and stops where a person is needed.

    investigate (already done on upload)
    -> propose the most useful site check                 next_action plan
    -> check its own proposal against the evidence       next_action stage "self_check" (internal; one per evidence state)
    -> draft the request for the site, with "done"
       defined before anyone acts                         next_action stage "prepared" (a draft)
    == a person approves the draft ==                     check_review   (approved, NOT sent)
    == the person sends it and says so ==                 request_sent   (channel, to whom, when; Veyra never sends)
    == the site's answer is recorded ==                   check_execution (response received)
    -> re-assess, recommend, check its own recommendation next_action report + self_check
    == a person records the decision ==                   decision (links the self_check it accepted)

Self-checks are Veyra's internal attempts. They are kept for audit, deduplicated by what they were checked against,
and never stored as decision proposals, so they can't be mistaken for engineering decisions.

advance() is idempotent: call it after any change; it only does the steps that are due. Every record is in the
ledger, Veyra's own are machine-written (they cannot be forged through the human ledger API).
Permission tiers: Veyra may read, compute, write its own records and draft requests. Sending, approving,
dispatching and closing stay with people. Nothing touches the machine.
"""
from __future__ import annotations

import hashlib
import json
import threading
from typing import Any, Dict, List, Optional

from app.engine import decision_check as dc_rules
from app.engine import next_action as na
from app.workflows import next_action as flow

ACTOR = "Veyra autopilot"
SOURCE = "veyra://autopilot"


def _latest(ledger, kind, run_id, **match):
    found = [e for e in ledger if e["kind"] == kind and e["payload"].get("run_id") == run_id and all(e["payload"].get(k) == v for k, v in match.items())]
    return found[-1] if found else None


_LOCKS: Dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _case_lock(case_id: str) -> threading.Lock:
    """One advance at a time per case: two concurrent calls used to write the same records twice."""
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(case_id, threading.Lock())


def _fingerprint(run_id: str, subject: Dict[str, Any], ledger: List[Dict[str, Any]]) -> str:
    """What a self-check depends on: the run, what is being checked, the site results and approvals. Same inputs,
    same answer, so it is written once."""
    results = sorted(r["entry_id"] for r in na.site_results(ledger, run_id).values())
    approvals = sorted(e["id"] for e in ledger if e["kind"] == "check_review" and e["payload"].get("run_id") == run_id)
    return hashlib.sha256(json.dumps([run_id, subject, results, approvals], sort_keys=True).encode()).hexdigest()[:16]


def _subject(run: Dict[str, Any], proposal: Dict[str, Any]) -> Dict[str, Any]:
    return {"run_id": run["id"], "proposed_by": "veyra", "action": proposal["action"], "purpose": proposal["purpose"],
            "check_id": proposal.get("check_id"), "recommendation": bool(proposal.get("recommendation")), "urgent": False,
            "target_part": proposal.get("target_part"), "hypothesis": proposal.get("hypothesis"), "relies_on": [], "note": None}


def _verify(store, case_id: str, run: Dict[str, Any], proposal: Dict[str, Any]) -> Dict[str, Any]:
    subject = _subject(run, proposal)
    ledger = store.ledger(case_id)
    fp = _fingerprint(run["id"], subject, ledger)
    for e in reversed(ledger):
        if e["kind"] == "next_action" and e["payload"].get("stage") == "self_check" and e["payload"].get("fingerprint") == fp:
            return e
    try:
        result = dc_rules.check(run, subject, ledger, run_current=flow_run_current(store, case_id))
    except Exception as error:  # a failed self-check is never treated as verified
        result = dc_rules.check(run, subject, [], unavailable=f"The check failed: {type(error).__name__}")
    result.setdefault("verified", result.get("alert") == "quiet" and result["coverage"]["status"] in ("complete", "none_needed"))
    return store.append_trusted(case_id, "next_action", {**result, "stage": "self_check", "run_id": run["id"], "subject": subject,
                                                         "fingerprint": fp, "internal": True}, actor=ACTOR, source=SOURCE)


def flow_run_current(store, case_id: str) -> bool:
    from app.workflows.decision_check import _run_current
    return _run_current(store, case_id)


def observed(run: Dict[str, Any]) -> List[Dict[str, str]]:
    """What the recording shows, each line with its source. Only these go into a request as facts."""
    out = []
    for f in ((run.get("findings") or {}).get("tracking") or {}).get("findings", []):
        out.append({"time": f.get("start"), "text": f["text"].replace(f" from {f.get('start')}", ""), "source": f"run {run['id']}, tracking"})
    for e in ((run.get("findings") or {}).get("events") or {}).get("events", []):
        if e.get("routine"):  # e.g. every joint switching mode together when the robot stands up: not a finding
            continue
        out.append({"time": e.get("time_utc"), "text": f"{e['signal']} recorded '{e['event_value']}'", "source": f"import {e.get('import_id')} row {e.get('row')}"})
    return sorted(out, key=lambda x: x["time"] or "")


EVENT_SIGNALS = {"controller.reset", "controller.event", "app.heartbeat", "fault.code", "network.link_event"}


def _request(run: Dict[str, Any], checks: List[Dict[str, Any]], case: Dict[str, Any]) -> Dict[str, Any]:
    """A draft. It states only what the recording shows (with times and sources), asks open questions, and lists the
    possible answers without presuming any of them happened."""
    window = run.get("symptom_window") or ["?", "?"]
    facts = observed(run)
    first = next(iter(((run.get("findings") or {}).get("tracking") or {}).get("findings", [])), None)
    anchor = f"the {first['axis']} tracking departure at {first['start']}" if first else f"the problem ({window[0]} to {window[1]})"
    recorded = {c.get("signal") for c in (run.get("findings") or {}).get("coverage") or []} & EVENT_SIGNALS
    lines = "\n".join(f"  - {f['time']}: {f['text']}" for f in facts[:4]) or "  - (no finding with a time)"
    asks = []
    for n, c in enumerate(checks, 1):
        answers = "\n".join(f"       - {o['label']}" for o in c["outcomes"])
        asks.append(f"  {n}. {c['question']}.\n     Which of these matches what you found?\n{answers}\n       - Something else (please describe)")
    body = (f"Hi,\n\nFor {case['machine_id']} ({case['title'].split(' (SYNTHETIC')[0]}).\n\n"
            f"What the recording shows:\n{lines}\n"
            + ("" if recorded else "The recording has no controller, reset or link events, so we don't know whether any occurred.\n")
            + f"\nWe want to establish what, if anything, was recorded before or after {anchor}. "
            f"For {window[0]} to {window[1]} (UTC), could you:\n\n" + "\n\n".join(asks)
            + "\n\nFor each, please also say where the log is (file name or ticket).\n\nThanks.")
    who = sorted({c.get("who") or "Site team" for c in checks})
    return {"to": " / ".join(who), "subject": f"{case['machine_id']}: log request before we decide on a visit", "body": body, "observed": facts}


READONLY_REASON = "Read-only check on the stored records. Veyra runs these itself; nothing on the machine is touched."


def auto_run_readonly(store, case_id: str, run: Dict[str, Any]) -> List[str]:
    """Runs the allow-listed read-only checks that are ready, so no one is asked to do what Veyra can do itself."""
    ledger = store.ledger(case_id)
    done = {e["payload"]["check_id"] for e in ledger if e["kind"] == "check_execution" and e["payload"].get("run_id") == run["id"]}
    ran = []
    for c in run.get("checks") or []:
        if c.get("mode") != "automatic_read_only" or c["availability"]["state"] != "ready_automatic" or c["check_id"] in done or c.get("previously_performed"):
            continue
        reviewed = any(e["kind"] == "check_review" and e["payload"].get("run_id") == run["id"] and e["payload"]["check_id"] == c["check_id"] for e in ledger)
        if not reviewed:
            store.append(case_id, "check_review", {"run_id": run["id"], "check_id": c["check_id"], "verdict": "accepted", "reason": READONLY_REASON},
                         actor=ACTOR, source=SOURCE)
        store.execute_automatic(case_id, c["check_id"], actor=ACTOR)
        ran.append(c["check_id"])
    return ran


def advance(store, case_id: str) -> Dict[str, Any]:
    with _case_lock(case_id):
        return _advance(store, case_id)


def only_brief_events(run: Dict[str, Any]) -> Optional[str]:
    """No one reported a problem and the only evidence is sub-second events: not enough to ask a site for anything."""
    if run.get("interventions") or run.get("reported_symptoms"):
        return None
    evidence = [o for d in run["domains"].values() for o in d.get("observations", []) if o.get("kind") in ("anomaly", "delay")]
    if evidence and all(o.get("rule") == "R-BRIEF" for o in evidence):
        return (f"No problem was reported, and the only finding is short: {'; '.join(o['text'] for o in evidence[:2])}. "
                "I won't ask the site for anything on that alone. Review it under Evidence.")
    return None


def text_log_only(store, case_id: str) -> Optional[str]:
    """A case whose evidence is a text log only: the generic site checks (drive logs, link logs, measured windows) do not
    fit it; its next check comes from the log itself (app/discovery/logs.py, shown in the case's questions)."""
    found = store.discovery(case_id)
    if not found or (found.get("source") or {}).get("format") != "text_log":
        return None
    q = store.questions(case_id)
    nxt = (q or {}).get("next") or {}
    return ("This case is a text log, so I won't draft a generic site request. "
            + (f"Its next check comes from the log: {nxt['question']} {nxt.get('needs', '')}".strip() if nxt.get("question") else
               "Its open checks are under What we can't establish."))


def _old_rules(ledger: List[Dict[str, Any]], run_id: str) -> Optional[Dict[str, Any]]:
    """A plan made under earlier rules stays in the record but is not acted on: its wording may presume events."""
    plan = _latest(ledger, "next_action", run_id, stage="plan")
    if plan and plan["payload"].get("policy") != na.POLICY:
        return {"stage": "old_rules", "run_id": run_id, "plan_policy": plan["payload"].get("policy"),
                "say": f"This plan and draft were made with older rules ({plan['payload'].get('policy')}) and are no longer used. "
                       "Investigate again and I'll prepare a new one."}
    return None


def _ids(prepared: Dict[str, Any]) -> List[str]:
    return prepared["payload"].get("check_ids") or [prepared["payload"]["check_id"]]


def _advance(store, case_id: str) -> Dict[str, Any]:
    run = store.latest_run(case_id)
    if run is None:
        return {"stage": "waiting_for_data", "say": "Upload data and I'll investigate it."}
    if not flow_run_current(store, case_id):
        return {"stage": "stale", "say": "The evidence changed since the last investigation. Investigate again and I'll pick it up."}
    weak = only_brief_events(run)
    if weak:
        return {"stage": "weak_evidence", "run_id": run["id"], "say": weak}
    log_only = text_log_only(store, case_id)
    if log_only:
        return {"stage": "weak_evidence", "run_id": run["id"], "say": log_only}
    old = _old_rules(store.ledger(case_id), run["id"])
    if old:
        return old
    auto_run_readonly(store, case_id, run)
    state = flow.state(store, case_id)
    if state["step"] == 1:
        try:
            state = flow.make_plan(store, case_id)
        except ValueError as error:
            return {**view(store, case_id), "stage": "nothing_to_split", "say": str(error)}
    ledger = store.ledger(case_id)
    case = store.case(case_id)
    pending = [c for c in state["checks"] if not c.get("result")]

    # Prepare one request covering the proposed checks that pass Veyra's own check. One self-check per evidence state.
    prepared = _latest(ledger, "next_action", run["id"], stage="prepared")
    pending_ids = [c["check_id"] for c in pending]
    if pending and (prepared is None or not set(_ids(prepared)) & set(pending_ids)):
        passed = []
        for c in pending:
            verdict = _verify(store, case_id, run, {"action": "remote_check", "purpose": "diagnose", "check_id": c["check_id"]})
            if verdict["payload"].get("verified"):
                passed.append((c, verdict["id"]))
        if passed:
            store.append_trusted(case_id, "next_action", {
                "stage": "prepared", "run_id": run["id"], "check_id": passed[0][0]["check_id"], "check_ids": [c["check_id"] for c, _ in passed],
                "verification": passed[0][1], "verifications": {c["check_id"]: v for c, v in passed},
                "request": _request(run, [c for c, _ in passed], case),
                "done_when": {"result": {c["check_id"]: [o["key"] for o in c["outcomes"]] for c, _ in passed},
                              "evidence": "where the record is (file or ticket)",
                              "decide_when": "one explanation leads by the set margin; if results disagree, hold"},
                "permission": "A draft. A person approves it, sends it themselves and records that it was sent. Veyra does not send messages."},
                actor=ACTOR, source=SOURCE)
        ledger = store.ledger(case_id)

    # With site results in and "done" met: re-assess, recommend, check the recommendation.
    # Results from earlier investigations of this case count too, marked as such.
    met = [cid for cid, r in na.site_results(ledger, run["id"]).items()
           if r.get("status") == "performed" and r.get("outcome") and r.get("evidence_note")]
    still_open = _latest(ledger, "next_action", run["id"], stage="prepared")
    waiting = still_open is not None and bool(set(_ids(still_open)) & set(pending_ids))
    report_entry = _latest(ledger, "next_action", run["id"], stage="report")
    if met and not waiting and (report_entry is None or set(report_entry["payload"].get("checks_used", [])) != set(met)):
        state = flow.make_report(store, case_id)
        rep = state["report"]
        lead = next((d for d in run["domains"] if na._label(run, d) == rep.get("leading")), None)
        if rep["status"] in ("fix_remotely_first", "dispatch_candidate") and lead:
            action = "remote_fix" if rep["status"] == "fix_remotely_first" else "dispatch"
            _verify(store, case_id, run, {"action": action, "purpose": "repair", "target_part": lead,
                                          "hypothesis": {"part": lead, "provenance": "agent_proposed"}, "recommendation": True})
    return view(store, case_id)


def _self_checks(ledger: List[Dict[str, Any]], run_id: str) -> Dict[str, Dict[str, Any]]:
    """Veyra's self-checks for this run, by entry id. Older cases stored them as decision_proposal + decision_check."""
    out = {e["id"]: e["payload"] for e in ledger if e["kind"] == "next_action" and e["payload"].get("stage") == "self_check" and e["payload"].get("run_id") == run_id}
    proposals = {e["id"]: e["payload"] for e in ledger if e["kind"] == "decision_proposal" and e["payload"].get("proposed_by") == "veyra"}
    for e in ledger:
        if e["kind"] == "decision_check" and e["payload"].get("run_id") == run_id and e["payload"].get("proposal_id") in proposals:
            out[e["id"]] = {**e["payload"], "subject": proposals[e["payload"]["proposal_id"]], "legacy": True}
    return out


def _sent(ledger: List[Dict[str, Any]], run_id: str, check_id: str) -> Optional[Dict[str, Any]]:
    found = [e for e in ledger if e["kind"] == "request_sent" and e["payload"].get("run_id") == run_id and check_id in e["payload"].get("check_ids", [])]
    return {**{k: found[-1]["payload"][k] for k in ("channel", "sent_to", "sent_at", "reference")}, "by": found[-1]["actor"], "entry_id": found[-1]["id"]} if found else None


def view(store, case_id: str) -> Dict[str, Any]:
    """What Veyra says to the engineer now, and the one thing it needs from them.

    Request states, each a separate record: draft prepared (awaiting_approval) -> approved, not sent (approved_not_sent)
    -> sent by a person (awaiting_result) -> response received (the result is recorded; Veyra re-assesses)."""
    run = store.latest_run(case_id)
    if run is None:
        return {"stage": "waiting_for_data", "say": "Upload data and I'll investigate it."}
    ledger = store.ledger(case_id)
    state = flow.state(store, case_id)
    weak = only_brief_events(run)
    if weak:
        return {"stage": "weak_evidence", "run_id": run["id"], "say": weak}
    log_only = text_log_only(store, case_id)
    if log_only:
        return {"stage": "weak_evidence", "run_id": run["id"], "say": log_only}
    if state["step"] == 1 and not _latest(ledger, "next_action", run["id"], stage="plan"):
        # Say where the case stands without writing anything (a visitor may only be reading).
        if not flow_run_current(store, case_id):
            return {"stage": "stale", "run_id": run["id"], "say": "The evidence changed since the last investigation. Investigate again and I'll pick it up."}
        earlier = na.site_results(ledger, run["id"])
        assessment = na.assess(run)
        reason = flow.nothing_to_ask(run, assessment, na.plan(run, assessment, results=earlier), earlier)
        if reason:
            return {"stage": "nothing_to_split", "run_id": run["id"], "say": reason}
        return {"stage": "not_started", "run_id": run["id"], "say": "I haven't prepared a next step for this investigation yet."}
    old = _old_rules(ledger, run["id"])
    if old and not state.get("report"):
        return old
    prepared = _latest(ledger, "next_action", run["id"], stage="prepared")
    report = state.get("report")
    checks = _self_checks(ledger, run["id"])
    decided = [e for e in ledger if e["kind"] == "decision" and e["payload"].get("run_id") == run["id"]]
    a = state["assessment"]
    did = list({e["payload"]["check_id"]: {"check_id": e["payload"]["check_id"], "procedure": e["payload"].get("procedure"),
                                           "count": (e["payload"].get("tool_result") or {}).get("count")}
                for e in ledger if e["kind"] == "check_execution" and e["payload"].get("run_id") == run["id"] and e["payload"]["status"] == "automatic_read_only"}.values())
    plan_entry = _latest(ledger, "next_action", run["id"], stage="plan")
    out: Dict[str, Any] = {"run_id": run["id"], "already_done": did, "internal_checks": len(checks),
                           "comparison": (plan_entry or {}).get("payload", {}).get("comparison", []),
                           "investigated": {"supported": [s["label"] for s in a["supported"]], "effects": [x["label"] for x in a.get("effects", [])],
                                            "cannot_tell_apart": [c["label"] for c in a["cannot_tell_apart"]], "why": a["why_not_separable"],
                                            "less_likely": [{"label": x["label"], "why": x["why"]} for x in a.get("less_likely", [])]}}
    if report:
        rec = next(((cid, c) for cid, c in reversed(list(checks.items()))
                    if c["subject"].get("action") in ("remote_fix", "dispatch") and c["subject"].get("recommendation", True)), None)
        out.update({"stage": "decided" if decided else "recommend", "report": report,
                    "recommendation": ({"check_entry": rec[0], "legacy": bool(rec[1].get("legacy")), "proposal_id": rec[1].get("proposal_id"),
                                        "action": rec[1]["subject"]["action"], "part": rec[1]["subject"].get("target_part"), "alert": rec[1]["alert"],
                                        "headline": rec[1]["headline"], "claims": rec[1]["claims"], "coverage": rec[1]["coverage"]["status"]} if rec else None),
                    "say": report["headline"]})
        return out
    if prepared:
        p = prepared["payload"]
        ids = _ids(prepared)
        asked = [c for c in state["checks"] if c["check_id"] in ids]
        approved = bool(asked) and all(c.get("approval") and c["approval"]["verdict"] != "rejected" for c in asked)
        sent = _sent(ledger, run["id"], ids[0])
        answered = [c for c in asked if c.get("result")]
        stage = "awaiting_result" if sent else "approved_not_sent" if approved else "awaiting_approval"
        say = {"awaiting_approval": "I've drafted a request for the site. Nothing has been sent.",
               "approved_not_sent": "The draft is approved. It has not been sent: send it yourself, then mark it as sent.",
               "awaiting_result": (f"Sent by {sent['by']} via {sent['channel'].replace('_', ' ')} to {sent['sent_to']}. "
                                   f"{len(answered)} of {len(asked)} answers recorded.") if sent else ""}[stage]
        verifications = p.get("verifications") or {ids[0]: p["verification"]}
        out.update({"stage": stage, "check": asked[0] if asked else None, "checks": asked, "sent": sent,
                    "request": p["request"], "done_when": p["done_when"],
                    "verification": {cid: {"headline": checks.get(v, {}).get("headline"), "claims": checks.get(v, {}).get("claims", []),
                                           "permission": checks.get(v, {}).get("prerequisites", [])} for cid, v in verifications.items()},
                    "say": say})
        return out
    out.update({"stage": "no_request", "say": "I couldn't find a site check that passes my own check. Review the evidence or record a decision.",
                "self_checks": list(checks.values())})
    return out
