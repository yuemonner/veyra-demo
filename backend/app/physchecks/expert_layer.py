"""The expert layer: system-level audit + ordinal plausibility, attached to the deterministic assessment.

One entry point used everywhere an assessment is built (agent bridge, investigation, case replay), so the AI investigator,
the stored record and the replay see the same thing:

  audit         app.physchecks.system_audit: symptom vs record, control sequence, inferred thresholds, signal
                independence, load sag, charge alignment, restart authorisation.
  plausibility  app.physchecks.plausibility: when the SOC error arose, what each explanation would require, and an
                record evidence per explanation and a labelled order to check first (prior, never evidence).

The assessment's statuses are NOT changed (only verified outcomes confirm or exclude). Each upstream hypothesis gets a
`plausibility` field, the assessment gets `leading` (or None), and the summary says which explanation leads, on what
basis, and the check that decides it. Customer-confirmed rules come from a private file (VEYRA_CUSTOMER_RULES, or the
case directory's customer_rules), never from the repository.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from app.physchecks import plausibility as P, system_audit

LAYER_VERSION = "expert-layer-v1"


def rules_from_environment() -> Dict[str, Any]:
    p = os.environ.get("VEYRA_CUSTOMER_RULES")
    return P.load_rules(Path(p)) if p else {}


def _capacity(profile, checks) -> Optional[float]:
    cap = ((checks.get("charge_accounting") or {}).get("parameters_used") or {}).get("capacity_ah")
    if cap:
        return float(cap)
    v, _ = profile.parameter("capacity_ah")
    return float(v) if v else None


def enrich(rec, profile, checks: Dict[str, Dict[str, Any]], facts: Dict[str, Dict[str, Any]], assessment: Dict[str, Any], *,
           rules: Optional[Dict[str, Any]] = None, reported_symptom: Optional[str] = None) -> Dict[str, Any]:
    """Compute the audit and the plausibility grades; attach them to `assessment` (in place) and return both."""
    rules = rules if rules is not None else rules_from_environment()
    audit = system_audit.audit(rec, profile, checks, facts, reported_symptom=reported_symptom, rules=rules)
    est = None
    if profile.has_role("soc"):
        est = profile.role("soc").get("estimator") or "coulomb_counting"
    applies = (assessment.get("family_scope") or {}).get("applies") and assessment.get("hypotheses")
    plaus = P.grade(facts, checks, audit, capacity_ah=_capacity(profile, checks), profile_conditions=getattr(profile, "conditions", {}) or {},
                    estimator=est, rules=rules) if applies else {"version": P.PLAUSIBILITY_VERSION, "grades": {}, "leading": None,
                                                                 "note": "no hypothesis family applies; nothing to order"}
    for h in assessment.get("hypotheses") or []:
        g = (plaus.get("grades") or {}).get(h["id"])
        if g:
            h["plausibility"] = g
    assessment["leading"] = plaus.get("leading")
    assessment["conditional_leading"] = plaus.get("conditional_leading")
    assessment["onset"] = plaus.get("onset")
    assessment["expert_layer"] = LAYER_VERSION
    if plaus.get("leading"):
        lead = plaus["leading"]
        assessment["summary"] = (lead["statement"] + " " + (assessment.get("summary") or "")).strip()
    elif plaus.get("conditional_leading"):
        assessment["summary"] = (plaus["conditional_leading"]["statement"] + " " + (assessment.get("summary") or "")).strip()
    elif applies and plaus.get("grades"):
        g = plaus["grades"]
        pass
    if rules:
        assessment["customer_rules"] = {"customer": rules.get("customer"), "items": rules.get("items")}
    return {"audit": audit, "plausibility": plaus}
