"""Canonical case state: one versioned answer to "where does this case stand now", built from the append-only ledger.

Everything the case page summarises (the headline and status, how many explanations stand, the next action, the latest
discussion round) is read from this one object, so the parts of the page cannot disagree. The latest assessment wins:
an investigation round or a discussion round, whichever was recorded last. Earlier conclusions are kept, in order,
marked as superseded and by what; they are never edited and never shown as current.

Also here, because they need the same timeline:
  provenance   what the current status of each explanation rests on (data, an engineer's report, or reasoning), every
               engineer statement labelled as reported, verified outcomes, and statements that claim more than that
  safety       the current next action classified by hazard (experiment_safety), with the review it needs
  impact       for each engineer input, whether it changed the next recommended action, what changed and why, and
               which competing explanations remain
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional

from app.agent import experiment_safety as S
from app.agent import investigator as inv
from app.agent import provenance as prov

VERSION = "case-state-v1"
STANDING = ("leading", "open")


def _assessments(ledger: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    n_inv = n_disc = 0
    for e in ledger:
        p = e["payload"]
        if e["kind"] != "agent_report":
            continue
        if p.get("kind") == "autonomous_investigation":
            n_inv += 1
            r = inv.reviewed_result(json.loads(json.dumps(p.get("result") or {})))
            out.append({"source": "investigation", "n": n_inv, "entry": e["id"], "at": e["recorded_at"], "recorded": bool(p.get("recorded")),
                        "mode": p.get("mode"), "headline": r.get("headline"), "status": r.get("status"), "hypotheses": r.get("hypotheses") or [],
                        "next_action": r.get("next_action"), "parts": [], "message": None, "message_kind": None})
        elif p.get("kind") == "discussion_round" and p.get("result"):
            n_disc += 1
            r = p["result"]
            out.append({"source": "discussion", "n": n_disc, "entry": e["id"], "at": e["recorded_at"], "recorded": bool(p.get("recorded")),
                        "mode": "agent", "headline": r.get("reply"), "status": None, "hypotheses": inv.reviewed_hypotheses(r.get("hypotheses"))[0],
                        "next_action": r.get("next_check"), "parts": r.get("parts") or [], "message": p.get("message"), "message_kind": p.get("message_kind"),
                        "still_unknown": r.get("still_unknown") or []})
    return out


def _status(a: Dict[str, Any]) -> str:
    """The cause status of an assessment. An investigation states its own; a discussion round does not, so it is
    'cause_probable' only when exactly one explanation is leading as the main cause and every other is weakened or ruled
    out, and 'cause_unknown' otherwise."""
    if a["source"] == "investigation":
        return a.get("status") or "cause_unknown"
    hs = a["hypotheses"]
    lead = [h for h in hs if h.get("status") == "leading"]
    rest_open = [h for h in hs if h.get("status") == "open"]
    return "cause_probable" if len(lead) == 1 and lead[0].get("role") in (None, "main_cause") and not rest_open else "cause_unknown"


def _counts(hs: List[Dict[str, Any]]) -> Dict[str, int]:
    c = {k: sum(1 for h in hs if h.get("status") == k) for k in ("leading", "open", "weakened", "ruled_out")}
    # still possible = not ruled out (a weakened explanation is not excluded), as the fault-space panel counts it
    return {"total": len(hs), **c, "possible": len(hs) - c["ruled_out"], "standing": c["leading"] + c["open"]}


def _norm(t: Optional[str]) -> str:
    return re.sub(r"[\s,，。.;；:：()（）]+", " ", (t or "").lower()).strip()


def _similarity(a: Optional[str], b: Optional[str]) -> float:
    """Overlap of the words (latin) and characters (CJK) of two actions, order ignored: 1 for the same text reworded or
    reordered, 0 for nothing in common."""
    def toks(t: Optional[str]) -> set:
        t = _norm(t)
        return set(re.findall(r"[a-z0-9_]+", t)) | set(re.findall(r"[一-鿿]", t))
    x, y = toks(a), toks(b)
    if not x or not y:
        return 0.0 if (x or y) else 1.0
    return round(len(x & y) / len(x | y), 2)


def _targets(next_action: Optional[Dict[str, Any]]) -> List[str]:
    out = set()
    for o in (next_action or {}).get("outcomes") or []:
        out |= set(o.get("rules_out") or []) | set(o.get("weakens") or [])
    return sorted(out)


def _inputs(ledger: List[Dict[str, Any]], assessments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every engineer input, in order: results they recorded (engineer_finding) and what they brought to a discussion
    round (a result they measured, or a guess or question)."""
    out = []
    for e in ledger:
        if e["kind"] == "engineer_finding":
            p = e["payload"]
            if p.get("observations") and any(a["source"] == "discussion" and a.get("message") == p.get("observations") for a in assessments):
                continue    # a live discussion result writes its finding too; count it once, with its round
            out.append({"kind": "observation", "source": "engineer_finding", "entry": e["id"], "at": e["recorded_at"],
                        "text": f"{p.get('action')}: {p.get('outcome_selected') or p.get('result')}" + (f". {p['observations']}" if p.get("observations") else ""),
                        "provenance": "engineer_reported"})
    for a in assessments:
        if a["source"] == "discussion" and a.get("message"):
            out.append({"kind": "observation" if a["message_kind"] == "result" else "claim", "source": "discussion", "entry": a["entry"], "round": a["n"],
                        "at": a["at"], "text": a["message"], "provenance": "engineer_reported", "answered_by": a["entry"]})
    return sorted(out, key=lambda x: x["at"])


def impact(inputs: List[Dict[str, Any]], assessments: List[Dict[str, Any]], hv_system: bool) -> List[Dict[str, Any]]:
    """For each engineer input: the assessment just before it and the one it produced, and whether the next recommended
    action changed, what changed, why, and which competing explanations remain."""
    out = []
    for x in inputs:
        if x.get("answered_by"):
            after = next((a for a in assessments if a["entry"] == x["answered_by"]), None)
            before = None
            for a in assessments:
                if a["entry"] == x["answered_by"]:
                    break
                before = a
        else:
            before = next((a for a in reversed(assessments) if a["at"] <= x["at"]), None)
            after = next((a for a in assessments if a["at"] > x["at"]), None)
        if after is None:
            out.append({**x, "verdict": "not_yet_assessed", "summary": "No assessment has been made since this input."})
            continue
        b_hyp = {h["id"]: h for h in (before or {}).get("hypotheses") or []}
        moved, new = [], []
        for h in after["hypotheses"]:
            o = b_hyp.get(h["id"])
            if o is None:
                new.append({"id": h["id"], "area": h.get("area"), "status": h.get("status")})
            elif o.get("status") != h.get("status") or (o.get("area") != h.get("area")):
                moved.append({"id": h["id"], "area": h.get("area"), "from": o.get("status"), "to": h.get("status"),
                              "renamed_from": o.get("area") if o.get("area") != h.get("area") else None, "because": h.get("because")})
        dropped = [{"id": i, "area": o.get("area")} for i, o in b_hyp.items() if i not in {h["id"] for h in after["hypotheses"]}]
        na_b, na_a = (before or {}).get("next_action") or {}, after.get("next_action") or {}
        t_b, t_a = _targets(na_b), _targets(na_a)
        sim = _similarity(na_b.get("action"), na_a.get("action"))
        # the same check reworded is not a new action: very similar text, or similar text testing the same explanations
        changed = not (sim >= 0.75 or (sim >= 0.45 and t_b == t_a))
        sb = S.classify(na_b, hv_system=hv_system)["worst"] if na_b else None
        sa = S.classify(na_a, hv_system=hv_system)["worst"] if na_a else None
        parts = [{"claim": p.get("claim"), "verdict": p.get("verdict"), "provenance": prov.of_kind(p.get("kind"))} for p in after.get("parts") or []]
        why = []
        if moved:
            why.append("; ".join(f"{m['area']}: {m['from']} → {m['to']}" + (f" ({m['because']})" if m.get("because") else "") for m in moved))
        if new:
            why.append("new: " + ", ".join(f"{n['area']} ({n['status']})" for n in new))
        reported_basis = [p for p in parts if p["provenance"] == "engineer_reported" and p["verdict"] in ("holds", "partly")]
        status_moved = [m for m in moved if m["from"] != m["to"]]
        if not changed and not status_moved and not new:
            verdict = "no_change"
        elif not changed:
            verdict = "explanations_moved_action_unchanged"
        elif status_moved or new:
            verdict = "action_changed_with_evidence"
        else:
            verdict = "action_changed_without_evidence"   # a new check with no explanation moved: it answers the claim, not new evidence
        out.append({**x, "before": {"source": (before or {}).get("source"), "n": (before or {}).get("n"), "next_action": na_b.get("action"), "targets": t_b, "hazard": sb},
                    "after": {"source": after["source"], "n": after["n"], "next_action": na_a.get("action"), "targets": t_a, "hazard": sa},
                    "verdict": verdict, "genuine_change": verdict == "action_changed_with_evidence", "action_changed": changed, "action_similarity": sim, "targets_changed": t_b != t_a, "hazard_changed": sb != sa,
                    "moved": moved, "new": new, "dropped": dropped,
                    "why": why or ["the assessment after it kept every explanation where it was"],
                    "rests_on_engineer_report": [p["claim"] for p in reported_basis],
                    "remaining": [{"id": h["id"], "area": h.get("area"), "status": h.get("status"), "role": h.get("role")}
                                  for h in after["hypotheses"] if h.get("status") in STANDING]})
    return out


def _basis(assessments: List[Dict[str, Any]], inputs: List[Dict[str, Any]]) -> Dict[str, str]:
    """What the current status of each explanation rests on, tracked through the assessments: the status inherits its basis
    while it is unchanged; when it changes right after an engineer observation, it rests on that report; otherwise on the
    lines it cites (telemetry) or on Veyra's reasoning."""
    basis: Dict[str, str] = {}
    last: Dict[str, str] = {}
    prev_at = ""
    for a in assessments:
        obs_between = [x for x in inputs if x["kind"] == "observation" and (prev_at < x["at"] <= a["at"] or x.get("answered_by") == a["entry"])]
        for h in a["hypotheses"]:
            if last.get(h["id"]) == h.get("status") and h["id"] in basis:
                continue
            if obs_between and last.get(h["id"]) is not None:
                basis[h["id"]] = "engineer_reported"
            elif h.get("lines") or h.get("basis") == "direct":
                basis[h["id"]] = "telemetry_observation"
            else:
                basis[h["id"]] = "model_inference"
            last[h["id"]] = h.get("status")
        prev_at = a["at"]
    return basis


def build(store, case_id: str, *, lines: Optional[List[str]] = None, walkthrough: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    ledger = store.ledger(case_id)
    case = store.case(case_id)
    if lines is None:
        try:
            lines = inv.LogTools(store, case_id).raw or []
        except Exception:  # noqa: BLE001
            lines = []
    if walkthrough is None:
        try:
            from app.agent import replay
            walkthrough = replay.walkthrough_of(store, case_id)
        except Exception:  # noqa: BLE001
            walkthrough = None
    hv_v = S.system_voltage(lines or [])
    hv_system = S.has_hv(hv_v, f"{case.get('title', '')} {case.get('description', '') or ''}")
    assessments = _assessments(ledger)
    inputs = _inputs(ledger, assessments)
    verified = prov.verified_outcomes(ledger)
    relevant = [e for e in ledger if e["kind"] in ("agent_report", "engineer_finding", "case_closure", "reproduction_attempt", "debug_walkthrough", "outcome")]
    digest = hashlib.sha256(json.dumps([e["id"] for e in relevant]).encode()).hexdigest()[:10]
    version = {"n": len(relevant), "id": f"v{len(relevant)}-{digest}", "as_of": relevant[-1]["recorded_at"] if relevant else None,
               "last_entry": relevant[-1]["id"] if relevant else None}
    cur = assessments[-1] if assessments else None
    basis = _basis(assessments, inputs)
    closure = next((e for e in reversed(ledger) if e["kind"] == "case_closure"), None)
    current = None
    if cur:
        hs = cur["hypotheses"]
        na = cur.get("next_action")
        current = {
            "from": {"source": cur["source"], "n": cur["n"], "entry": cur["entry"], "at": cur["at"], "recorded": cur["recorded"],
                     "of": sum(1 for a in assessments if a["source"] == cur["source"])},
            "status": "cause_verified" if any(v["kind"] == "fix_verified_by_data" for v in verified) else _status(cur),
            "headline": cur.get("headline"),
            "hypotheses": [{**h, "status_basis": basis.get(h["id"], "model_inference"),
                            **prov.hypothesis_standing(h, basis=basis.get(h["id"], "model_inference"), verified=verified)} for h in hs],
            "counts": _counts(hs),
            "next_action": ({**na, "safety": S.classify(na, hv_system=hv_system)} if na else None),
            "still_unknown": cur.get("still_unknown") or [],
        }
    history = []
    for a in assessments[:-1]:
        nxt = next(b for b in assessments if b["at"] >= a["at"] and b["entry"] != a["entry"] and assessments.index(b) > assessments.index(a))
        history.append({"source": a["source"], "n": a["n"], "entry": a["entry"], "at": a["at"], "recorded": a["recorded"], "headline": a.get("headline"),
                        "status": _status(a), "leading": [h.get("area") for h in a["hypotheses"] if h.get("status") == "leading"],
                        "counts": _counts(a["hypotheses"]), "next_action": (a.get("next_action") or {}).get("action"),
                        "superseded_by": {"source": nxt["source"], "n": nxt["n"], "entry": nxt["entry"]}})
    disc = [a for a in assessments if a["source"] == "discussion"]
    claims = prov.engineer_claims(ledger, walkthrough)
    verified_any = bool(verified)
    flags = []
    for c in claims:
        f = prov.audit_text(c["text"], provenance=c["provenance"], verified_any=verified_any)
        if f and c.get("asserts_cause"):
            flags.append({**f, "source": c["source"]})
        elif c.get("asserts_cause") and c["source"] in ("walkthrough_final_cause", "case_closure") and not verified_any and c.get("text"):
            flags.append({"text": c["text"][:300], "provenance": "engineer_reported", "source": c["source"],
                          "issue": "an engineer's conclusion about the cause; show it as reported, not as the established cause (no verified outcome)"})
    if current:
        f = prov.audit_text(current.get("headline"), provenance="model_inference", verified_any=verified_any)
        if f:
            flags.append({**f, "source": f"{cur['source']} {cur['n']} headline"})
    return {
        "version": VERSION, "case_id": case_id, "state_version": version, "synthetic": bool(case.get("synthetic")),
        "current": current,
        "latest_discussion": ({"n": disc[-1]["n"], "of": len(disc), "at": disc[-1]["at"], "recorded": disc[-1]["recorded"],
                               "message_kind": disc[-1]["message_kind"], "message": disc[-1]["message"], "reply": disc[-1]["headline"],
                               "is_current": cur is disc[-1]} if disc else None),
        "history": history,
        "engineer_inputs": impact(inputs, assessments, hv_system),
        "provenance": {"version": prov.VERSION, "engineer_claims": claims, "verified_outcomes": verified, "flags": flags,
                       "closure": ({"engineer_status": closure["payload"].get("investigation_status"), "method": closure["payload"].get("confirmed_by"),
                                    "verified": any(v["kind"] == "fix_verified_by_data" for v in verified)} if closure else None)},
        "safety": {"version": S.VERSION, "high_voltage_system": hv_system, "system_voltage_v": hv_v,
                   "rule": f"High voltage: above {S.HV_LIMIT_V_DC:g} V DC (ISO 6469-3 voltage class B)"},
        "boundary": "One state for the whole case page, rebuilt from the append-only record. Earlier conclusions stay in the history; none is current but the latest.",
    }
