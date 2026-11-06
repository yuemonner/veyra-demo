"""Claim provenance: where each statement on a case comes from, and how far it may go.

Four kinds, kept apart everywhere a claim is shown:
  telemetry_observation  read directly from a cited line of the machine's own data (the quote is checked against the
                         file), or computed from such lines by a deterministic tool
  engineer_reported      what an engineer says they measured, saw, did or concluded; recorded as said, not checked
  model_inference        reasoning by Veyra (the AI or a deterministic rule) beyond what a line shows
  verified_outcome       an outcome established independently of anyone's say-so: a record linked to data, made after
                         an intervention or a deliberate reproduction (see `verified_outcomes`)

The rule this module enforces: an engineer's report, or an observation that is merely consistent with an explanation,
never becomes verified physical causation. A hypothesis is a verified cause only when a verified outcome names it.
Short of that its standing says what it rests on: data, an engineer's report, or reasoning.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

VERSION = "provenance-v1"
KINDS = ("telemetry_observation", "engineer_reported", "model_inference", "verified_outcome")
LABEL = {
    "telemetry_observation": ("Observed in the data", "数据里看到的"),
    "engineer_reported": ("Engineer reported (not checked against data)", "工程师报告的（未用数据核实）"),
    "model_inference": ("Veyra's inference", "Veyra 推断的"),
    "verified_outcome": ("Verified outcome (independent record with data)", "已验证的结果（有数据的独立记录）"),
}

# Words that state a cause as established. Shown with a provenance label unless a verified outcome backs them.
CAUSAL = re.compile(r"\broot cause\b|\bthe cause (is|was)\b|\bcaused by\b|\bconfirmed\b|\bverified\b|\bwas the (cause|problem|fault)\b|"
                    r"\bfixed (it|the)\b|\bresolved\b|根本原因|原因是|查出来的原因|查出的原因|确认|已验证|修好|已修复|就是.{0,10}(原因|问题)", re.I)


def of_kind(kind: Optional[str]) -> str:
    """Map the tags the investigator and the discussion already write (observed / inferred / reported)."""
    return {"observed": "telemetry_observation", "inferred": "model_inference", "reported": "engineer_reported"}.get(kind or "", "model_inference")


def _has_data(e: Dict[str, Any]) -> bool:
    p = e["payload"]
    return bool(p.get("evidence") or p.get("evidence_imports") or p.get("imports"))


def verified_outcomes(ledger: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Outcomes that stand without anyone's say-so. Two kinds qualify:
      fix_verified_by_data       a closure whose fix held, linked to a record that carries data imports recorded after the
                                 fix (an outcome, a check execution or a reproduction attempt with evidence imports)
      mechanism_reproduced       a reproduction attempt recorded as reproduced, with evidence imports (data), in a stated
                                 environment; it supports the mechanism, not that it was the cause in the field
    A closure 'confirmed by inspection' with only a note, a fix that 'held' by the engineer's word, or a reproduction with
    no data attached is engineer_reported, not verified."""
    by_id = {e["id"]: e for e in ledger}
    out = []
    for e in ledger:
        p = e["payload"]
        if e["kind"] == "reproduction_attempt" and p.get("result") == "reproduced" and _has_data(e):
            out.append({"kind": "mechanism_reproduced", "entry": e["id"], "at": e["recorded_at"], "environment": p.get("environment"),
                        "hypotheses": p.get("hypotheses") or [], "domain": p.get("domain"),
                        "scope": "Supports the mechanism under the stated conditions; not proof it was the cause in the field."})
        if e["kind"] == "case_closure" and p.get("fix_held") == "held":
            linked = [by_id[i] for i in p.get("evidence_entries") or [] if i in by_id]
            data = [x for x in linked if x["kind"] in ("outcome", "check_execution", "reproduction_attempt") and _has_data(x)]
            if data:
                out.append({"kind": "fix_verified_by_data", "entry": e["id"], "at": e["recorded_at"], "linked": [x["id"] for x in data],
                            "domain": p.get("cause_domain"), "cause": p.get("cause"), "hypotheses": p.get("hypotheses") or [],
                            "scope": "The fix removed the symptom in data recorded after it (the linked record carries that data)."})
    return out


def engineer_claims(ledger: List[Dict[str, Any]], walkthrough: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Every statement an engineer made on the case, labelled engineer_reported, with whether it asserts a cause."""
    out = []
    for e in ledger:
        p = e["payload"]
        if e["kind"] == "engineer_finding":
            text = f"{p.get('action')}: {p.get('outcome_selected') or p.get('result')}" + (f". {p['observations']}" if p.get("observations") else "")
            out.append({"source": "engineer_finding", "entry": e["id"], "at": e["recorded_at"], "text": text, "provenance": "engineer_reported",
                        "asserts_cause": bool(CAUSAL.search(text))})
        elif e["kind"] == "case_closure":
            text = p.get("cause") or ""
            method = p.get("confirmed_by")
            out.append({"source": "case_closure", "entry": e["id"], "at": e["recorded_at"], "text": text, "provenance": "engineer_reported",
                        "asserts_cause": bool(text), "engineer_status": p.get("investigation_status"), "method": method,
                        "note": ("The engineer recorded the cause as confirmed" + (f" ({method.replace('_', ' ')})" if method else "")
                                 + "; Veyra counts it as verified only with a linked record carrying data.") if p.get("investigation_status") == "cause_confirmed" else None})
        elif e["kind"] == "reproduction_attempt":
            out.append({"source": "reproduction_attempt", "entry": e["id"], "at": e["recorded_at"],
                        "text": f"{p.get('environment')}: {p.get('setup')} -> {p.get('result')}: {p.get('observations')}",
                        "provenance": "verified_outcome" if (p.get("result") == "reproduced" and _has_data(e)) else "engineer_reported",
                        "asserts_cause": False})
    if walkthrough:
        for i, s in enumerate(walkthrough.get("steps") or []):
            text = f"{s.get('check')}: {s.get('result')}" + (f" ({s['decided_by']})" if s.get("decided_by") else "")
            out.append({"source": "walkthrough", "step": s.get("id") or f"s{i + 1}", "at": s.get("when"), "text": text, "provenance": "engineer_reported",
                        "asserts_cause": s.get("result") == "fault_found"})
        if walkthrough.get("final_cause"):
            out.append({"source": "walkthrough_final_cause", "text": walkthrough["final_cause"], "provenance": "engineer_reported", "asserts_cause": True})
    return out


def hypothesis_standing(h: Dict[str, Any], *, basis: str, verified: List[Dict[str, Any]]) -> Dict[str, Any]:
    """How far a hypothesis may be called a cause. basis: what its current status rests on (telemetry_observation,
    engineer_reported or model_inference). Only a verified outcome naming it makes it a verified cause."""
    names = {h.get("id"), h.get("area")}
    v = [x for x in verified if names & set(x.get("hypotheses") or []) and x["kind"] == "fix_verified_by_data"]
    m = [x for x in verified if names & set(x.get("hypotheses") or []) and x["kind"] == "mechanism_reproduced"]
    if v:
        return {"standing": "verified_cause", "provenance": "verified_outcome", "by": [x["entry"] for x in v]}
    if m:
        return {"standing": "mechanism_reproduced", "provenance": "verified_outcome", "by": [x["entry"] for x in m],
                "note": "Reproduced with data; not yet shown to be the cause in the field."}
    if h.get("status") not in ("leading", "open"):
        return {"standing": h.get("status"), "provenance": basis}
    return {"standing": "leading_not_verified" if h.get("status") == "leading" else "open", "provenance": basis,
            "note": {"engineer_reported": "Its status rests on an engineer's report that the data does not show.",
                     "telemetry_observation": "Consistent with the data; consistency is not proof of cause.",
                     "model_inference": "Veyra's inference from the data; not proof of cause."}[basis]}


def audit_text(text: Optional[str], *, provenance: str, verified_any: bool) -> Optional[Dict[str, Any]]:
    """A statement that asserts a cause, shown as more than its provenance allows. Returns a flag, or None."""
    if not text or not CAUSAL.search(text) or verified_any or provenance == "verified_outcome":
        return None
    return {"text": text[:300], "provenance": provenance,
            "issue": "states a cause as established; no verified outcome backs it, so it must be shown as " + LABEL[provenance][0].lower()}
