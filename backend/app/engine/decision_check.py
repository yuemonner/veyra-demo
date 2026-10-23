"""Decision Check: before a decision is committed, what does the evidence say about it?

Three things are kept apart and never mixed:
  facts    what the records show, relevant to this action. Never attributed to the engineer.
  gaps     checks whose result could realistically change THIS action, given its purpose and urgency.
  claims   what the engineer explicitly stated or confirmed, each with its provenance. Only these get a verdict.

Two separate answers:
  coverage   were the checks this decision depends on done and readable?  complete · partial · none_needed · not_assessed
  claims     per stated claim: supported · contradicted · insufficient · not_assessed
Contradicted needs positive counter-evidence from a measurement whose limits cover the claim. "Nothing found"
is never a contradiction. Missing data, failed checks or a stale run never appear as a pass.

Advisory only: it never blocks, never says an action is optimal, never names a failed part.
"""
from __future__ import annotations

from app.engine import checks as check_policy

from typing import Any, Dict, List, Optional

from app.engine import next_action as na

POLICY = "decision-check-v1"
ACTIONS = ("dispatch", "replace_part", "remote_fix", "close_case")
AGENT_ACTIONS = ACTIONS + ("remote_check",)  # remote_check: Veyra proposes asking the site for one check
PURPOSES = ("diagnose", "repair", "safety_inspection", "restore_service")
PROVENANCE = ("engineer_stated", "system_suggested_confirmed")
AGENT_PROVENANCE = ("agent_proposed",)

# What each kind of finding can and cannot show. Reviewed by an engineer before changing.
LIMITS: Dict[str, Dict[str, str]] = {
    "R-REFERENCE": {"shows": "the median of this signal differed from the declared normal period",
                    "not": "where in the system the change started, or that other signals on the same path were normal"},
    "R-SCREEN": {"shows": "the signal crossed a fixed screening limit on the measured path",
                 "not": "that the measured path is the path the commands take"},
    "R-SEGMENT-RELATIVE": {"shows": "this stage of the same commands got slower, relative to before",
                           "not": "an absolute delay (clocks aren't synced), or what caused the slowdown"},
    "R-SEGMENT": {"shows": "no delay in this measured stage for the matched commands",
                  "not": "anything about stages or channels that were not measured"},
    "R-IO": {"shows": "the machine responded to received commands as in the baseline, on this channel",
             "not": "other channels, or faults that only appear under load"},
    "R-HEARTBEAT": {"shows": "the controller stopped reporting for a while",
                    "not": "why: a controller fault and a lost link look the same here"},
    "R-EVENT": {"shows": "this event or fault code was recorded near the problem",
                "not": "that it caused the problem"},
    "R-TRACKING": {"shows": "the joint or axis stopped following its commands",
                   "not": "whether the drive, the mechanics or the command was at fault"},
    "R-OUTPUT-STUCK": {"shows": "an output stopped moving while it normally moves",
                       "not": "whether the actuator or the signal path failed"},
    "R-BRIEF": {"shows": "a short spike or drop at that moment",
                "not": "that it mattered for the problem"},
}
EVIDENCE = na.EVIDENCE


def _label(run, d):
    return na._label(run, d) if d else "no part"


def _observation(run: Dict[str, Any], ref: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for o in run["domains"].get(ref.get("domain"), {}).get("observations", []):
        if o.get("rule") == ref.get("rule") and o.get("text") == ref.get("text"):
            return o
    return None


def _site_results(ledger: List[Dict[str, Any]], run_id: str) -> Dict[str, Dict[str, Any]]:
    return na.site_results(ledger, run_id)


def _approved(ledger, run_id) -> set:
    return {e["payload"]["check_id"] for e in ledger if e["kind"] == "check_review" and e["payload"].get("run_id") == run_id and e["payload"]["verdict"] != "rejected"}


def _scores(run: Dict[str, Any], results: Dict[str, Dict[str, Any]]) -> Dict[str, int]:
    """Balance of evidence per candidate part, same weights as Next action: evidence 1, site result +-2."""
    assessment = na.assess(run)
    candidates = [c["part"] for c in assessment["cannot_tell_apart"]] or [x["part"] for x in assessment["supported"]]
    score = {d: (1 if run["domains"][d]["status"] in EVIDENCE else 0) for d in candidates}
    for cid, r in results.items():
        o = na.outcome_of(cid, r.get("outcome"))
        if r.get("status") == "performed" and o:
            for d in o["supports"]:
                if d in score:
                    score[d] += 2
            for d in o["weakens"]:
                if d in score:
                    score[d] -= 2
    return score


def _lead(scores: Dict[str, int]) -> Optional[str]:
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    if not ranked:
        return None
    return ranked[0][0] if len(ranked) == 1 or ranked[0][1] - ranked[1][1] >= 2 else None


# ---------- facts ----------
def facts(run: Dict[str, Any], proposal: Dict[str, Any], results: Dict[str, Dict[str, Any]]) -> List[Dict[str, str]]:
    out = []
    assessment = na.assess(run)
    for s in assessment["supported"]:
        out.append({"text": f"{s['label']}: {na._evidence_lines(run, s['part'], 1)[0]}", "source": f"run {run['id']}"})
    for x in assessment["less_likely"]:
        out.append({"text": f"{x['label']}: no delay where it was measured", "source": f"run {run['id']}"})
    for cid, r in results.items():
        outcome = na.outcome_of(cid, r.get("outcome")) or {}
        if r.get("status") == "performed":
            out.append({"text": f"Site result ({cid}): {outcome.get('label') or r.get('observations')}", "source": f"ledger {r['entry_id']}"})
    target = proposal.get("target_part")
    lead = _lead(_scores(run, results))
    travel = proposal["action"] in ("dispatch", "replace_part") and proposal["purpose"] in ("repair", "diagnose") and not proposal.get("urgent")
    if travel and lead in na.REMOTE_PARTS and target != lead:
        out.append({"text": f"The evidence points to {_label(run, lead)}, which can be changed remotely", "source": f"run {run['id']}", "points_elsewhere": "yes"})
    elif target and travel:
        status = run["domains"].get(target, {}).get("status")
        others = [s["part"] for s in assessment["supported"] if s["part"] != target]
        if status not in EVIDENCE and others:
            out.append({"text": f"The recorded evidence points to {', '.join(_label(run, d) for d in others)}, not {_label(run, target)}",
                        "source": f"run {run['id']}", "points_elsewhere": "yes"})
    return out


# ---------- gaps ----------
def gaps(run: Dict[str, Any], proposal: Dict[str, Any], results: Dict[str, Dict[str, Any]], approved: set) -> List[Dict[str, Any]]:
    """Checks whose result could realistically change this action. Safety-driven or urgent visits have none:
    a missing root cause does not change the need to go."""
    action, purpose = proposal["action"], proposal["purpose"]
    if proposal.get("urgent") or purpose == "safety_inspection":
        return []
    assessment = na.assess(run)
    candidates = [c["part"] for c in assessment["cannot_tell_apart"]] or [s["part"] for s in assessment["supported"]]
    target = proposal.get("target_part")
    base = _scores(run, results)

    def lead(scores):
        """The part on top (no tie). A result 'could change' the action if it changes which part is on top."""
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        if not ranked:
            return None
        return ranked[0][0] if len(ranked) == 1 or ranked[0][1] > ranked[1][1] else None

    def basis(scores):
        """What this action rests on, under these scores. A different value means the action could change."""
        top = lead(scores)
        if action in ("dispatch", "replace_part"):
            if target:
                return ("target_leads" if top == target else "remote_leads" if top in na.REMOTE_PARTS else "unclear")
            return ("remote_leads" if top in na.REMOTE_PARTS else top or "unclear")   # no trip needed / who and what to bring
        if action == "remote_fix":
            return "target_leads" if target and top == target else "target_not_leading"
        return top or "unclear"                                                        # close_case: the conclusion

    now = basis(base)
    relevant: List[Dict[str, Any]] = []
    for check in run.get("checks") or []:
        cid = check["check_id"]
        splits = na.SPLITS.get(cid)
        if not splits or not check_policy.eligible(check) or check["availability"]["state"] == "onsite_required":
            continue
        could_change = []
        done = results.get(cid, {}).get("status") == "performed"
        for key, o in splits.items():
            if done:
                break
            trial = dict(base)
            for d in o["supports"]:
                if d in trial:
                    trial[d] += 2
            for d in o["weakens"]:
                if d in trial:
                    trial[d] -= 2
            if basis(trial) != now:
                could_change.append(o["label"])
        if done:
            could_change = ["already done"]
        if not could_change:
            continue
        r = results.get(cid)
        state = ("done" if r and r.get("status") == "performed" else "failed" if r and r.get("status") in ("not_performed", "inconclusive")
                 else "inaccessible" if check["availability"]["state"] == "needs_authorization" and cid not in approved else "not_done")
        relevant.append({"check_id": cid, "question": check["question"], "who": check.get("who"), "state": state,
                         "why": ("Done." if done else f"Could change this {action.replace('_', ' ')}: if {could_change[0].lower()}.")})
    return relevant


def prerequisites(run: Dict[str, Any], proposal: Dict[str, Any]) -> List[str]:
    if proposal["action"] != "dispatch":
        return []
    return [b["item"] for b in (run.get("dispatch") or {}).get("blockers", []) if b["status"] in ("not_recorded", "intervention_review_outstanding")][:3]


# ---------- claims ----------
def claim_verdicts(run: Dict[str, Any], proposal: Dict[str, Any], results: Dict[str, Dict[str, Any]], ledger: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    h = proposal.get("hypothesis")
    if h and h.get("part"):
        part, label = h["part"], _label(run, h["part"])
        status = run["domains"].get(part, {}).get("status")
        against = [(cid, r) for cid, r in results.items() if r.get("status") == "performed"
                   and part in (na.outcome_of(cid, r.get("outcome")) or {}).get("weakens", [])]
        ruled_out = [b for b in run.get("hypotheses") or [] if b["domain"] == part and b.get("column") == "ruled_out"]
        if against:
            cid, r = against[0]
            verdict, why = "contradicted", f"Site result ({cid}): {na.outcome_of(cid, r['outcome'])['label']}. That makes {label} less likely."
            source = f"ledger {r['entry_id']}"
        elif ruled_out:
            verdict, why, source = "contradicted", f"{label} was ruled out by {ruled_out[0]['human'].get('by')}: {ruled_out[0]['human'].get('basis')}", "case record"
        elif status in EVIDENCE:
            verdict, why, source = "supported", f"{label} shows evidence: {na._evidence_lines(run, part, 1)[0]}", f"run {run['id']}"
        elif status == "not_observed":
            verdict, why, source = "not_assessed", f"No records cover {label}; it can't be checked from this data.", f"run {run['id']}"
        else:
            verdict, why, source = "insufficient", f"No delay found where {label} was measured; that doesn't exclude faults outside the measured part.", f"run {run['id']}"
        out.append({"claim": f"{label} is involved", "kind": "hypothesis", "provenance": h.get("provenance", "engineer_stated"),
                    "verdict": verdict, "why": why, "source": source})
    for c in proposal.get("relies_on") or []:
        o = _observation(run, c)
        limit = LIMITS.get((c.get("rule") or "").split(":")[0])
        claim = f"{c.get('text', '')[:140]} {'rules out' if c.get('use') == 'rules_out' else 'supports'} {_label(run, c.get('part'))}"
        if o is None:
            out.append({"claim": claim, "kind": "finding", "provenance": c.get("provenance", "engineer_stated"), "verdict": "not_assessed",
                        "why": "This finding is not in the current investigation run.", "source": None})
            continue
        if not limit:
            verdict, why = "not_assessed", "No reviewed limit exists for this kind of finding yet."
        elif c.get("use") == "rules_out":
            verdict, why = "insufficient", f"It shows {limit['shows']}. It does not show {limit['not']}."
        else:
            same_part = c.get("part") == c.get("domain")
            verdict = "supported" if same_part and o.get("kind") in ("anomaly", "delay") else "insufficient"
            why = f"It shows {limit['shows']}." + ("" if verdict == "supported" else f" It does not show {limit['not']}.")
        out.append({"claim": claim, "kind": "finding", "provenance": c.get("provenance", "engineer_stated"), "verdict": verdict, "why": why,
                    "source": f"run {run['id']} · {c.get('rule')}"})
    return out


# ---------- Veyra checking its own proposed site check ----------
def verify_remote_check(run: Dict[str, Any], proposal: Dict[str, Any], ledger: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Re-derives every premise of the proposed check from the run and the case record, not from the plan that proposed it."""
    cid = proposal.get("check_id")
    results = _site_results(ledger, run["id"])
    approved = _approved(ledger, run["id"])
    check_row = next((c for c in run.get("checks") or [] if c["check_id"] == cid), None)
    assessment = na.assess(run)
    open_parts = [c["part"] for c in assessment["cannot_tell_apart"]]
    claims = []
    # 1. the parts it would split are really open, from the evidence now
    splits = na.SPLITS.get(cid or "", {})
    touched = {d for o in splits.values() for d in o["supports"] + o["weakens"]} & set(open_parts)
    if check_row is None or not splits:
        claims.append({"claim": f"{cid} is a check this run can use", "kind": "premise", "provenance": "agent_proposed", "verdict": "contradicted",
                       "why": "This run does not propose that check.", "source": f"run {run['id']}"})
    elif len(open_parts) < 2 or not touched:
        claims.append({"claim": "There are open explanations for it to separate", "kind": "premise", "provenance": "agent_proposed", "verdict": "contradicted",
                       "why": "The evidence no longer leaves two explanations open." if len(open_parts) < 2 else "It does not touch the explanations that are open.",
                       "source": f"run {run['id']}"})
    else:
        # What each answer would do, stated per outcome. It never claims one result separates every explanation unless
        # an outcome actually supports exactly one open part and weakens all the others.
        row = next((r for r in na.compare(run, assessment, results) if r["check_id"] == cid), None)
        effects = "; ".join(f"if {o['label'][0].lower() + o['label'][1:]}: {o['meaning']}" for o in (row or {}).get("outcomes", []))
        claims.append({"claim": "It informs " + ", ".join(_label(run, d) for d in sorted(touched)), "kind": "premise", "provenance": "agent_proposed",
                       "verdict": "supported", "why": f"{effects}. {(row or {}).get('settles_note', '')}".strip(), "source": f"run {run['id']}"})
    # 2. not already done
    if cid in results:
        claims.append({"claim": "It has not been done yet", "kind": "premise", "provenance": "agent_proposed", "verdict": "contradicted",
                       "why": f"A result is already recorded (ledger {results[cid]['entry_id']}).", "source": f"ledger {results[cid]['entry_id']}"})
    # 3. its result could change the dispatch decision (the question the site visit hinges on)
    deciding = gaps(run, {"action": "dispatch", "purpose": "repair"}, results, approved)
    mine = next((g for g in deciding if g["check_id"] == cid), None)
    claims.append({"claim": "Its result could change whether someone travels", "kind": "premise", "provenance": "agent_proposed",
                   "verdict": "supported" if mine else "insufficient",
                   "why": mine["why"] if mine else "No result of this check would change a dispatch decision on the current evidence.", "source": f"run {run['id']}"})
    permission = None
    if check_row and check_row["availability"]["state"] == "needs_authorization" and cid not in approved:
        permission = "Needs permission from the site owner before anyone runs it."
    contradicted = [c for c in claims if c["verdict"] == "contradicted"]
    weak = [c for c in claims if c["verdict"] != "supported"]
    verified = not weak
    return {"policy": POLICY, "run_id": run["id"], "run_hash": run.get("run_hash"), "check_id": cid,
            "coverage": {"status": "complete" if verified else "partial", "checks": []}, "facts": facts(run, {"action": "remote_check", "purpose": "diagnose"}, results),
            "gaps": [], "prerequisites": [permission] if permission else [], "claims": claims,
            "alert": "warning" if contradicted else ("notice" if weak else "quiet"), "verified": verified,
            "headline": ("Checked: worth asking the site." if verified else contradicted[0]["why"] if contradicted else weak[0]["why"]),
            "next_check": None, "boundary": "Veyra checked its own proposal against the evidence. A person still approves before anyone is asked."}


# ---------- the check ----------
def check(run: Optional[Dict[str, Any]], proposal: Dict[str, Any], ledger: List[Dict[str, Any]], *, run_current: bool = True,
          unavailable: Optional[str] = None) -> Dict[str, Any]:
    if run is None or not run_current or unavailable:
        reason = unavailable or ("No investigation run yet." if run is None else "The evidence or machine context changed since the last investigation. Investigate again.")
        return {"policy": POLICY, "coverage": {"status": "not_assessed", "reason": reason, "checks": []}, "facts": [], "gaps": [], "prerequisites": [],
                "claims": [], "alert": "not_assessed", "headline": f"Not checked: {reason}", "next_check": None,
                "boundary": "Advisory. Not checked does not mean fine."}
    if proposal["action"] == "remote_check":
        return verify_remote_check(run, proposal, ledger)
    results = _site_results(ledger, run["id"])
    approved = _approved(ledger, run["id"])
    f = facts(run, proposal, results)
    g = gaps(run, proposal, results, approved)
    pre = prerequisites(run, proposal)
    claims = claim_verdicts(run, proposal, results, ledger)
    open_gaps = [x for x in g if x["state"] != "done"]
    coverage = ("none_needed" if not g else "complete" if not open_gaps else "partial")
    contradicted = [c for c in claims if c["verdict"] == "contradicted"]
    weak = [c for c in claims if c["verdict"] in ("insufficient", "not_assessed")]
    elsewhere = any(x.get("points_elsewhere") for x in f)
    if contradicted:
        alert, headline = "warning", "A stated claim is contradicted by the evidence: " + contradicted[0]["why"]
    elif elsewhere and open_gaps:
        alert, headline = "warning", next(x["text"] for x in f if x.get("points_elsewhere")) + ". A remote check could change this action first."
    elif elsewhere:
        alert, headline = "notice", next(x["text"] for x in f if x.get("points_elsewhere")) + "."
    elif open_gaps:
        alert, headline = "notice", f"{len(open_gaps)} check{'s' if len(open_gaps) > 1 else ''} could still change this action."
    elif weak or pre:
        alert, headline = "notice", ("Some stated claims can't be confirmed from this data." if weak else "Some details needed for a visit are not recorded.")
    else:
        alert, headline = "quiet", ("Nothing found that would change this action." if coverage != "partial" else "")
    order = {"inaccessible": 0, "not_done": 1, "failed": 2}
    nxt = sorted(open_gaps, key=lambda x: (0 if x["check_id"] in na.YIELDS_RECORD else 1, order.get(x["state"], 3)))[:1]
    return {"policy": POLICY, "run_id": run["id"], "run_hash": run.get("run_hash"), "coverage": {"status": coverage, "checks": g},
            "facts": f, "gaps": open_gaps, "prerequisites": pre, "claims": claims, "alert": alert, "headline": headline,
            "next_check": nxt[0] if nxt else None,
            "boundary": "Advisory. It does not say the action is the best one, and it never blocks it."}
