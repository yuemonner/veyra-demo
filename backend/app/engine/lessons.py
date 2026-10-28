"""Operational learning: what earlier, closed cases teach about this one, and whether it applies here.

A LESSON is a closed case seen as reusable experience:
  failure signature  what the evidence showed (anomalies at their signal-chain points, the mechanism the records
                     supported, the reset cause and fault locations a log stated, the reported symptom)
  operating context  model, firmware, control software... at the incident (machine context)
  cause + status     the engineer's conclusion (confirmed / probable / unknown) and how it was established
  intervention       what was done (fix) and whether it held
  decisive check     the check that settled it, reusable as the first check next time
  applies when       conditions the engineer stated (free text, optional)
  counterexamples    other closed cases with a similar signature and a DIFFERENT cause
  status             verified (cause confirmed and the fix held), candidate (probable, or fix not yet known),
                     no cause (closed without one: still tells what was ruled out)

Reuse is two steps, both deterministic:
1. RETRIEVE: weighted overlap of signature tokens. Specific tokens (a fault location, an anomaly on a named channel, a
   supported mechanism, a stated reset cause) weigh 3; general ones (a symptom, a message template) weigh 1.
2. APPLICABILITY: each of the lesson's specific tokens is present or absent here, and the recorded context is the same,
   differs or unverified. Verdicts: 'applies' (every specific token present, context not different), 'check_first' (some
   shared, some missing, or context differs / unverified: reuse its decisive check, not its fix), 'not_applicable'
   (only general tokens shared: listed so nobody re-uses its fix by mistake).
Nothing here edits the case's own conclusions: a lesson is shown beside them.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from app.engine import precedents

LESSONS_VERSION = "lessons-v1"
SPECIFIC = ("at:", "fault:", "reset:", "anomaly:", "mechanism:")
MIN_SCORE = 0.15


def _weight(token: str) -> int:
    return 3 if token.startswith(SPECIFIC) else 1


def _weighted_overlap(a: Set[str], b: Set[str]) -> float:
    union = a | b
    if not union:
        return 0.0
    return round(sum(_weight(t) for t in a & b) / sum(_weight(t) for t in union), 3)


def case_signature(store, case_id: str) -> Dict[str, Any]:
    """Signature tokens of a case from its latest run, its evidence-discovery record and its reported symptom."""
    from app.engine import mechanisms
    from app.workflows.discovery import profile_for_run
    run = store.latest_run(case_id)
    tokens: Set[str] = set()
    found = store.discovery(case_id)
    if run is not None:
        chain = profile_for_run(run).get("signal_chain")
        for a in mechanisms.anomalies(run, chain or {"points": []}):
            if a["rule"] in ("STUCK", "MIRROR", "TRACKING", "THRESHOLD"):
                tokens.add(f"anomaly:{a['rule']}:{a['scope'] or a['signal']}")
        if chain:
            assessed = mechanisms.assess(run, chain, found)
            for ev in assessed.get("events") or []:
                tokens |= {f"mechanism:{m['id']}" for m in ev["mechanisms"] if m["status"] == "supported"}
    if found and found.get("log"):
        tokens |= set(found["log"].get("signature_tokens") or [])
    for e in store.ledger(case_id):
        if e["kind"] == "intervention_report":
            if e["payload"].get("symptom"):
                tokens.add(f"symptom:{e['payload']['symptom']}")
            if e["payload"].get("affected_function"):
                tokens.add(f"function:{str(e['payload']['affected_function']).strip().lower()}")
    return {"tokens": sorted(tokens), "context": (run or {}).get("context", {}).get("incident"), "machine_id": store.case(case_id)["machine_id"]}


def _closure(store, case_id: str) -> Optional[Dict[str, Any]]:
    return next((e for e in reversed(store.ledger(case_id)) if e["kind"] == "case_closure"), None)


def lesson_for(store, case: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    entry = _closure(store, case["id"])
    if entry is None:
        return None
    c = entry["payload"]
    sig = case_signature(store, case["id"])
    decisive = c.get("decisive_check")
    if decisive:  # a check id from the case's run -> its question
        run = store.latest_run(case["id"]) or {}
        decisive = next((k.get("question") for k in run.get("checks") or [] if k.get("check_id") == decisive), decisive)
    status = ("verified" if c.get("investigation_status") == "cause_confirmed" and c.get("fix_held") == "held"
              else "no_cause" if c.get("investigation_status") in ("cause_unknown", "not_reproduced") else "candidate")
    return {"case_id": case["id"], "title": case.get("title"), "machine_id": case.get("machine_id"), "synthetic": bool(case.get("synthetic")),
            "closed_at": entry["recorded_at"], "closed_by": entry["actor"], "signature": sig["tokens"], "context": sig["context"],
            "cause": c.get("cause"), "cause_domain": c.get("cause_domain"), "investigation_status": c.get("investigation_status"),
            "confirmed_by": c.get("confirmed_by"), "fix": c.get("fix"), "fix_held": c.get("fix_held"), "decisive_check": decisive,
            "applies_when": c.get("applies_when"), "evidence_note": c.get("evidence_note"), "status": status}


def all_lessons(store, *, exclude: Optional[str] = None) -> List[Dict[str, Any]]:
    out = []
    for case in store.cases():
        if case["id"] == exclude or case.get("fault_type") == "known_good_run":
            continue
        lesson = lesson_for(store, case)
        if lesson:
            out.append(lesson)
    return out


def _same_cause(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    if not a.get("cause") or not b.get("cause"):
        return False
    return a.get("cause_domain") == b.get("cause_domain") and a["cause"].strip().casefold() == b["cause"].strip().casefold()


def _check_topic(text: Optional[str]) -> Optional[str]:
    import re
    from app.discovery.logs import DONE_TOPICS
    for topic, rx in DONE_TOPICS.items():
        if text and re.search(rx, text, re.I):
            return topic
    return None


def match(store, case_id: str) -> Dict[str, Any]:
    from app.discovery.logs import already_done
    sig = case_signature(store, case_id)
    reports = [e["payload"].get("stated_reason") or "" for e in store.ledger(case_id) if e["kind"] == "intervention_report"]
    now = set(sig["tokens"])
    lessons = all_lessons(store, exclude=case_id)
    shown = []
    for lesson in lessons:
        then = set(lesson["signature"])
        score = _weighted_overlap(now, then)
        specific = sorted(t for t in then if t.startswith(SPECIFIC))
        shared_specific = [t for t in specific if t in now]
        if score < MIN_SCORE and not shared_specific:
            continue
        missing = [t for t in specific if t not in now]
        ctx = precedents.compare_context(sig["context"], lesson["context"])
        if specific and not missing and ctx["status"] != "differs":
            verdict = "applies"
        elif shared_specific:
            verdict = "check_first"
        else:
            verdict = "not_applicable"
        counter = [{"case_id": o["case_id"], "title": o["title"], "cause": o.get("cause"), "status": o["status"]}
                   for o in lessons if o["case_id"] != lesson["case_id"] and o.get("cause") and not _same_cause(o, lesson)
                   and _weighted_overlap(set(o["signature"]), then) >= 0.2
                   and any(t.startswith(SPECIFIC) for t in set(o["signature"]) & then)]
        done_here = already_done({"topic": _check_topic(lesson.get("decisive_check"))}, reports) if lesson.get("decisive_check") else None
        if done_here and verdict != "applies":
            reuse = (f"The check that settled it there ({lesson['decisive_check']}) is reported as already done here: “{done_here.rstrip('.')}”. "
                     "If it found nothing, this case is a counterexample to that lesson: close it with its own cause.")
        elif verdict == "applies" and lesson["status"] == "verified" and lesson.get("fix"):
            reuse = f"Its fix held there: {lesson['fix']}. Confirm the cause here before repeating it."
        elif lesson.get("decisive_check"):
            reuse = f"Run the check that settled it there: {lesson['decisive_check']}."
        else:
            reuse = None
        shown.append({**{k: lesson[k] for k in ("case_id", "title", "machine_id", "synthetic", "cause", "cause_domain", "investigation_status",
                                                "fix", "fix_held", "decisive_check", "applies_when", "status", "closed_at")},
                      "score": score, "verdict": verdict, "shared": sorted(now & then), "missing_here": missing,
                      "only_here": sorted(t for t in now - then if t.startswith(SPECIFIC)), "context": ctx, "counterexamples": counter, "reuse": reuse,
                      "done_here": done_here, "explain": {"shared": [describe(t) for t in sorted(now & then)], "missing_here": [describe(t) for t in missing]}})
    rank = {"applies": 0, "check_first": 1, "not_applicable": 2}
    shown.sort(key=lambda m: (rank[m["verdict"]], -m["score"]))
    return {"version": LESSONS_VERSION, "signature": sig["tokens"], "lessons": shown[:5], "lessons_in_workspace": len(lessons),
            "rule": "Specific signature tokens weigh 3, general 1; 'applies' needs every specific token of the lesson here and no context difference.",
            "boundary": "Earlier cases are leads with their conditions, not answers. A lesson never changes this case's own findings."}


def describe(token: str) -> str:
    kind, _, rest = token.partition(":")
    return {"at": f"fault at {rest}", "fault": f"{rest.replace('_', ' ')}{' reset' if rest == 'watchdog' else ''} reported", "reset": f"restart cause: {rest.replace('_', ' ')}",
            "anomaly": f"{rest.split(':', 1)[0].lower()} on {rest.split(':', 1)[-1]}", "mechanism": f"evidence pointed to {rest.replace('_', ' ')}",
            "symptom": f"reported symptom: {rest.replace('_', ' ')}", "function": f"affected: {rest}", "msg": f"message “{rest[:70]}”"}.get(kind, token)
