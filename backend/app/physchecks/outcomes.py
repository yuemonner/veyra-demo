"""Verified outcomes: Case -> Hypothesis -> Recommended check -> Performed check -> Observation -> Engineer verification
-> Outcome, as append-only records in the engineering ledger.

Stages a hypothesis can be in (derived from its records, never set directly):
  proposed_diagnosis            stated by the deterministic assessment, an agent or an engineer; nothing tested yet
  engineer_reviewed_hypothesis  an engineer reviewed it and accepted it for testing (or rejected it)
  observed_test_result          a performed check produced an observation that bears on it
  verified_root_cause           an ENGINEER verified it as confirmed, from at least one observation, with no unresolved
                                conflicting observation
  excluded                      an ENGINEER verified it as excluded, on the same conditions
  unresolved                    the case was closed without a verified cause

Rules:
  * an agent can propose hypotheses and record nothing else in the chain; it can never review or verify;
  * a verification must cite observations that exist, belong to the case and bear on the hypothesis;
  * if current observations both support and contradict a hypothesis, it cannot be confirmed or excluded until an
    engineer corrects (supersedes) one of them with a reason;
  * a correction supersedes a record and keeps both; the stage is recomputed from what is current;
  * retrieval for future investigations returns VERIFIED outcomes as trusted knowledge, and lists the rest separately
    as unverified (never promoted silently into a rule).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.physchecks.store import Ledger

KINDS = ("case_opened", "hypothesis", "hypothesis_review", "recommended_check", "performed_check", "observation", "verification",
         "case_closure", "correction")
STAGES = ("proposed_diagnosis", "engineer_reviewed_hypothesis", "observed_test_result", "verified_root_cause", "excluded", "unresolved")


class OutcomeError(ValueError):
    pass


def _subject(case_id: str) -> str:
    return f"case:{case_id}"


def _need_engineer(kind: str) -> None:
    if kind != "engineer":
        raise OutcomeError("only an engineer can record this; an agent's output stays a proposal")


def open_case(led: Ledger, case_id: str, *, title: str, signature: List[str], actor: str, confidential: bool,
              context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """`context` (knowledge.context_of): machine class, profile, check versions and whether the data is synthetic, so a
    later case can decide whether this case's verified results apply to it."""
    return led.append("case_opened", _subject(case_id), {"case_id": case_id, "title": title, "signature": sorted(set(signature)),
                                                          "confidential": confidential, "context": context or {}}, actor=actor, actor_kind="engineer")


def propose(led: Ledger, case_id: str, *, hypothesis_id: str, statement: str, origin: str, actor: str, actor_kind: str) -> Dict[str, Any]:
    if origin not in ("deterministic", "agent", "engineer"):
        raise OutcomeError("origin must be deterministic, agent or engineer")
    return led.append("hypothesis", _subject(case_id), {"hypothesis_id": hypothesis_id, "statement": statement, "origin": origin},
                      actor=actor, actor_kind=actor_kind)


def review(led: Ledger, case_id: str, *, hypothesis_id: str, decision: str, notes: str, actor: str, actor_kind: str) -> Dict[str, Any]:
    _need_engineer(actor_kind)
    if decision not in ("accept_for_testing", "reject", "needs_more_evidence"):
        raise OutcomeError("decision must be accept_for_testing, reject or needs_more_evidence")
    _hyp(led, case_id, hypothesis_id)
    return led.append("hypothesis_review", _subject(case_id), {"hypothesis_id": hypothesis_id, "decision": decision, "notes": notes},
                      actor=actor, actor_kind=actor_kind)


def recommend(led: Ledger, case_id: str, *, template_id: str, hypotheses: List[str], plan_version: str, actor: str = "veyra-planner") -> Dict[str, Any]:
    return led.append("recommended_check", _subject(case_id), {"template_id": template_id, "hypotheses": hypotheses, "plan_version": plan_version},
                      actor=actor, actor_kind="system")


def performed(led: Ledger, case_id: str, *, template_id: str, method: str, performed_by: str, qualification: str, actor: str,
              actor_kind: str, recommended_id: Optional[str] = None) -> Dict[str, Any]:
    _need_engineer(actor_kind)
    return led.append("performed_check", _subject(case_id), {"template_id": template_id, "method": method, "performed_by": performed_by,
                                                              "qualification": qualification, "recommended_id": recommended_id},
                      actor=actor, actor_kind=actor_kind)


def observe(led: Ledger, case_id: str, *, performed_id: str, summary: str, measurements: Dict[str, Any], supports: List[str],
            contradicts: List[str], actor: str, actor_kind: str, attachments: Optional[List[str]] = None) -> Dict[str, Any]:
    _need_engineer(actor_kind)
    p = led.get(performed_id)
    if not p or p["kind"] != "performed_check" or p["subject"] != _subject(case_id):
        raise OutcomeError("an observation must come from a performed check of this case")
    if set(supports) & set(contradicts):
        raise OutcomeError("one observation cannot both support and contradict the same hypothesis")
    return led.append("observation", _subject(case_id), {"performed_id": performed_id, "summary": summary, "measurements": measurements,
                                                         "supports": supports, "contradicts": contradicts, "attachments": attachments or []},
                      actor=actor, actor_kind=actor_kind)


def verify(led: Ledger, case_id: str, *, hypothesis_id: str, result: str, observation_ids: List[str], basis: str, actor: str,
           actor_kind: str) -> Dict[str, Any]:
    _need_engineer(actor_kind)
    if result not in ("confirmed", "excluded", "not_settled"):
        raise OutcomeError("result must be confirmed, excluded or not_settled")
    if not basis.strip():
        raise OutcomeError("a verification states its basis")
    _hyp(led, case_id, hypothesis_id)
    current = {r["id"]: r for r in led.current(subject=_subject(case_id), kinds=["observation"])}
    if result != "not_settled":
        if not observation_ids:
            raise OutcomeError("confirming or excluding needs at least one observation")
        for oid in observation_ids:
            o = current.get(oid)
            if o is None:
                raise OutcomeError(f"observation {oid} is not a current observation of this case (unknown or superseded)")
            side = "supports" if result == "confirmed" else "contradicts"
            if hypothesis_id not in o["payload"][side]:
                raise OutcomeError(f"observation {oid} does not {side[:-1]} {hypothesis_id}")
        conflict = conflicts(led, case_id).get(hypothesis_id)
        if conflict:
            raise OutcomeError(f"conflicting observations for {hypothesis_id} ({conflict}); correct one of them before verifying")
    return led.append("verification", _subject(case_id), {"hypothesis_id": hypothesis_id, "result": result, "observation_ids": observation_ids,
                                                          "basis": basis}, actor=actor, actor_kind=actor_kind)


def correct(led: Ledger, case_id: str, *, record_id: str, reason: str, replacement: Optional[Dict[str, Any]], actor: str,
            actor_kind: str) -> Dict[str, Any]:
    """Supersede a record (an observation found wrong, a verification revised). The replacement, if any, is a new record of
    the same kind; without one the record is withdrawn."""
    _need_engineer(actor_kind)
    if not reason.strip():
        raise OutcomeError("a correction states its reason")
    old = led.get(record_id)
    if not old or old["subject"] != _subject(case_id):
        raise OutcomeError("can only correct a record of this case")
    if replacement is None:
        return led.append("correction", _subject(case_id), {"reason": reason, "withdrawn": record_id, "kind": old["kind"]},
                          actor=actor, actor_kind=actor_kind, supersedes=record_id)
    return led.append(old["kind"], _subject(case_id), {**replacement, "correction_reason": reason}, actor=actor, actor_kind=actor_kind,
                      supersedes=record_id)


def close(led: Ledger, case_id: str, *, summary: str, actor: str, actor_kind: str) -> Dict[str, Any]:
    _need_engineer(actor_kind)
    return led.append("case_closure", _subject(case_id), {"summary": summary}, actor=actor, actor_kind=actor_kind)


def _hyp(led: Ledger, case_id: str, hid: str) -> Dict[str, Any]:
    h = [r for r in led.current(subject=_subject(case_id), kinds=["hypothesis"]) if r["payload"]["hypothesis_id"] == hid]
    if not h:
        raise OutcomeError(f"hypothesis {hid} was never proposed in case {case_id}")
    return h[-1]


def conflicts(led: Ledger, case_id: str) -> Dict[str, str]:
    obs = led.current(subject=_subject(case_id), kinds=["observation"])
    out: Dict[str, str] = {}
    for hid in {h for o in obs for h in o["payload"]["supports"] + o["payload"]["contradicts"]}:
        sup = [o["id"] for o in obs if hid in o["payload"]["supports"]]
        con = [o["id"] for o in obs if hid in o["payload"]["contradicts"]]
        if sup and con:
            out[hid] = f"supported by {', '.join(sup)}; contradicted by {', '.join(con)}"
    return out


def state(led: Ledger, case_id: str) -> Dict[str, Any]:
    subj = _subject(case_id)
    cur = led.current(subject=subj)
    opened = next((r for r in cur if r["kind"] == "case_opened"), None)
    hyps: Dict[str, Dict[str, Any]] = {}
    for r in cur:
        p = r["payload"]
        if r["kind"] == "hypothesis":
            hyps.setdefault(p["hypothesis_id"], {"hypothesis_id": p["hypothesis_id"], "statements": [], "reviews": [], "observations": [],
                                                 "verification": None})
            hyps[p["hypothesis_id"]]["statements"].append({"statement": p["statement"], "origin": p["origin"], "by": r["actor"], "id": r["id"]})
        elif r["kind"] == "hypothesis_review" and p["hypothesis_id"] in hyps:
            hyps[p["hypothesis_id"]]["reviews"].append({"decision": p["decision"], "by": r["actor"], "notes": p["notes"], "id": r["id"]})
        elif r["kind"] == "observation":
            for hid in p["supports"] + p["contradicts"]:
                if hid in hyps:
                    hyps[hid]["observations"].append({"id": r["id"], "summary": p["summary"], "bears": "supports" if hid in p["supports"] else "contradicts",
                                                      "by": r["actor"]})
        elif r["kind"] == "verification" and p["hypothesis_id"] in hyps:
            hyps[p["hypothesis_id"]]["verification"] = {"result": p["result"], "basis": p["basis"], "by": r["actor"], "id": r["id"],
                                                        "observation_ids": p["observation_ids"], "at": r["created_at"]}
    conf = conflicts(led, case_id)
    closed = next((r for r in reversed(cur) if r["kind"] == "case_closure"), None)
    for hid, h in hyps.items():
        v = h["verification"]
        if v and v["result"] == "confirmed":
            h["stage"] = "verified_root_cause"
        elif v and v["result"] == "excluded":
            h["stage"] = "excluded"
        elif h["observations"]:
            h["stage"] = "observed_test_result"
        elif h["reviews"]:
            h["stage"] = "engineer_reviewed_hypothesis"
        else:
            h["stage"] = "proposed_diagnosis"
        h["conflict"] = conf.get(hid)
        h["agent_only"] = all(s["origin"] == "agent" for s in h["statements"]) and not h["reviews"]
    confirmed = [h for h in hyps.values() if h["stage"] == "verified_root_cause"]
    case_stage = "verified_root_cause" if confirmed else ("unresolved" if closed else "open")
    cur_ids = {r["id"] for r in cur}
    history = [{"id": r["id"], "kind": r["kind"], "by": r["actor"], "actor_kind": r["actor_kind"], "at": r["created_at"],
                "supersedes": r["supersedes"], "current": r["id"] in cur_ids, "payload": r["payload"]} for r in led.records(subject=subj)]
    return {"case_id": case_id, "title": (opened or {}).get("payload", {}).get("title"), "signature": (opened or {}).get("payload", {}).get("signature", []),
            "case_stage": case_stage, "hypotheses": hyps, "conflicts": conf, "closure": closed["payload"] if closed else None, "history": history}


def verified_for(led: Ledger, case_id: str) -> List[Dict[str, Any]]:
    """The case's engineer-verified results, in the shape hypotheses.assess(outcomes=...) takes."""
    st = state(led, case_id)
    out = []
    for hid, h in st["hypotheses"].items():
        v = h["verification"]
        if v and v["result"] in ("confirmed", "excluded"):
            out.append({"id": v["id"], "hypothesis_id": hid, "result": v["result"], "verified": True, "summary": v["basis"], "by": v["by"]})
    return out


def knowledge(led: Ledger, signature: List[str], *, exclude_case: Optional[str] = None) -> Dict[str, Any]:
    """Earlier cases with an overlapping signature: VERIFIED outcomes as trusted knowledge, the rest listed as unverified."""
    sig = set(signature)
    trusted, unverified = [], []
    for opened in led.records(kinds=["case_opened"]):
        cid = opened["payload"]["case_id"]
        if cid == exclude_case:
            continue
        overlap = sig & set(opened["payload"]["signature"])
        if not overlap:
            continue
        st = state(led, cid)
        for hid, h in st["hypotheses"].items():
            row = {"case_id": cid, "case_title": st["title"], "hypothesis_id": hid, "stage": h["stage"], "shared_signature": sorted(overlap),
                   "statement": h["statements"][-1]["statement"] if h["statements"] else ""}
            if h["stage"] in ("verified_root_cause", "excluded"):
                row["verification"] = h["verification"]
                trusted.append(row)
            else:
                row["note"] = "not verified: shown for context, not used as a rule"
                unverified.append(row)
    return {"trusted": trusted, "unverified": unverified}


def signature_of(checks: Dict[str, Dict[str, Any]]) -> List[str]:
    """A case signature from deterministic check findings (kinds only, no values)."""
    sig = set()
    for cid, r in checks.items():
        sig.add(f"{cid}:{r['outcome']}")
        for f in r.get("findings") or []:
            if f["severity"] in ("violation", "review"):
                sig.add(f"{cid}:{f['kind']}")
    return sorted(sig)
