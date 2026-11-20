"""Ordering the explanations the record cannot separate, without pretending to probabilities.

The deterministic assessment (hypotheses.py) keeps A (SOC estimate), B (capacity) and C (current) "open" when the recorded
signals look the same under each. Experienced engineers still order them, and they do it in a checkable way: they ask
WHEN the error arose, and WHAT EACH EXPLANATION WOULD PHYSICALLY REQUIRE. This module encodes that:

  onset          was the SOC overstatement already present at the first record (accumulated before it, since the
                 estimator's last calibration anchor) or did it arise inside the record?
  requirements   what B needs (the usable capacity it implies, anchored at a full charge) and what C needs (how many
                 times the measured current the real discharge must have been), each compared with reference knowledge.
  record evidence + a labelled order to check first (see below).

Two things are kept apart (dev finding, 2026-11-19: "what B would require" is the same number as the SOC overstatement,
so it is not evidence against B; on capacity-fade cases it pushed away from the true cause):
  record evidence  only this raises or weakens a cause: the onset, the late correction, load transitions against the
                   measured current (a load not seen by the sensor supports C2), current when open, the rested-voltage
                   pattern when the OCV curve exists.
  prior order      which explanation to CHECK FIRST when the record cannot separate them, in this order of basis:
                   (1) a single cause the record supports; (2) this customer's engineer-verified outcomes with the same
                   signature (the fleet's own base rate); (3) reference knowledge, only on a CONFIRMED flat-OCV chemistry
                   with a coulomb-counted SOC, an onset before the record and a late correction (A). Unknown chemistry ->
                   a conditional lead. The prior is labelled, never evidence, and never used to reject a hypothesis.
A "leading" explanation is a ranked proposal with its basis, never a confirmed cause: only an engineer-verified outcome
confirms. The check that can confirm or weaken it at the lowest risk is named with it.

Customer-confirmed rules (schema veyra-customer-rules-v1) are loaded from the customer's private directory, never stored
in the repository; they are a separate evidence tier with their own provenance.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

PLAUSIBILITY_VERSION = "plausibility-v2"
RULES_SCHEMA = "veyra-customer-rules-v1"
GRADES = ("leading", "leading_if_flat_ocv", "possible")
B_EXTREME_FRACTION = 0.60
C_EXTREME_FACTOR = 1.5
ONSET_MIN_PP = 15.0

_KN_PATH = Path(__file__).parent / "domains" / "reference_knowledge.json"


def reference_knowledge() -> Dict[str, Dict[str, Any]]:
    data = json.loads(_KN_PATH.read_text(encoding="utf-8"))
    return {e["id"]: e for e in data["entries"]}


def load_rules(path: Optional[Path]) -> Dict[str, Any]:
    """Customer-confirmed rules from a private file. Returns {} when absent. Shape after loading:
    {"customer", "scope", "fault_levels": {"2": {"permits_hv": true}, ...}, "chemistry", "ocv_shape",
     "full_charge_interval_days", "items": [rule dicts with provenance]}"""
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    if data.get("schema") != RULES_SCHEMA:
        raise ValueError(f"customer rules must use schema {RULES_SCHEMA!r}")
    out: Dict[str, Any] = {"customer": data.get("customer"), "scope": data.get("scope"), "fault_levels": {}, "items": []}
    for r in data.get("rules") or []:
        out["items"].append({k: r.get(k) for k in ("id", "kind", "statement", "confirmed_by", "date")})
        k, d = r.get("kind"), r.get("data") or {}
        if k == "fault_level_semantics":
            out["fault_levels"].update({str(lv): v for lv, v in (d.get("levels") or {}).items()})
        elif k == "chemistry":
            out["chemistry"] = d.get("chemistry")
            if d.get("ocv_shape"):
                out["ocv_shape"] = d["ocv_shape"]
        elif k == "maintenance_interval":
            out["full_charge_interval_days"] = d.get("full_charge_interval_days")
        elif k == "verified_outcome":
            out.setdefault("verified_outcomes", []).append({"id": r.get("id"), "hypothesis": d.get("hypothesis"), "mechanisms": d.get("mechanisms"),
                                                            "signature": d.get("signature") or {}, "statement": r.get("statement")})
    return out


FLAT_CHEMISTRIES = ("lfp", "lifepo4", "磷酸铁锂", "lithium iron phosphate", "lto")


def ocv_shape(profile_conditions: Dict[str, Any], rules: Dict[str, Any]) -> Dict[str, Any]:
    """flat | sloped | unknown, and where that came from (customer rule > profile declaration > unknown)."""
    if rules.get("ocv_shape"):
        return {"shape": rules["ocv_shape"], "basis": "customer_rule"}
    chem = str(rules.get("chemistry") or "").lower()
    if chem:
        return {"shape": "flat" if any(c in chem for c in FLAT_CHEMISTRIES) else "unknown", "basis": "customer_rule", "chemistry": rules.get("chemistry")}
    pc = profile_conditions or {}
    if pc.get("ocv_shape") in ("flat", "sloped"):
        return {"shape": pc["ocv_shape"], "basis": "profile"}
    chem = str(pc.get("chemistry") or "").lower()
    if chem and not any(w in chem for w in ("not stated", "unconfirmed", "inferred", "unknown")):
        return {"shape": "flat" if any(c in chem for c in FLAT_CHEMISTRIES) else "unknown", "basis": "profile", "chemistry": pc.get("chemistry")}
    return {"shape": "unknown", "basis": "none"}


def grade(facts: Dict[str, Dict[str, Any]], checks: Dict[str, Dict[str, Any]], audit: Optional[Dict[str, Any]],
          *, capacity_ah: Optional[float], profile_conditions: Optional[Dict[str, Any]] = None,
          estimator: Optional[str] = None, rules: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    rules = rules or {}
    K = reference_knowledge()
    eng = (facts.get("energy_shortfall") or {}).get("value") or {}
    corr = (facts.get("soc_correction") or {}).get("value") or {}
    ca = checks.get("charge_accounting") or {}
    ratios = [f["values"].get("ratio_reported_to_expected") for f in ca.get("findings") or []
              if f.get("kind") == "soc_tracks_measured_charge" and f["values"].get("ratio_reported_to_expected")]
    shape = ocv_shape(profile_conditions or {}, rules)
    out: Dict[str, Any] = {"version": PLAUSIBILITY_VERSION, "ocv_shape": shape, "grades": {}, "onset": None, "requirements": {}, "leading": None}
    if not eng or not capacity_ah:
        out["note"] = "No energy shortfall to grade (needs the first recorded SOC, the charge delivered to cutoff and the stated capacity)."
        return out

    over = float(eng.get("overstatement_pp") or 0.0)
    counted_ok = all(0.8 <= float(r) <= 1.25 for r in ratios) if ratios else None
    corr_pp = abs(float(corr.get("unexplained_pp") or 0.0)) if corr else 0.0
    pre = over >= ONSET_MIN_PP and counted_ok is not False
    out["onset"] = {
        "predates_record": pre, "overstatement_at_first_record_pp": over, "counting_consistent_in_record": counted_ok,
        "tracking_ratios": ratios[:6], "late_correction_pp": corr_pp or None,
        "statement": (f"The SOC already overstated the charge actually available by about {over:.0f} pp at the first record"
                      + (", and inside the record it fell only by the charge counted (ratio " + ", ".join(f"{r:g}" for r in ratios[:4]) + ")" if counted_ok else "")
                      + ": the error accumulated BEFORE the record, since the estimator's last calibration anchor. The decisive evidence is the history "
                        "since that anchor, not another recording of the same signals.") if pre else
                     (f"The overstatement at the first record is about {over:.0f} pp" + ("; inside the record the SOC did not follow the counted charge" if counted_ok is False else "")
                      + ": when the error arose cannot be placed before the record."),
        "basis": ["fact:energy_shortfall", "check:charge_accounting"]}

    soc_first = float(eng["soc_first_pct"])
    delivered = float(eng["delivered_ah"])
    cap = float(capacity_ah)
    c_req_anchored = (1 - soc_first / 100) * cap + delivered       # B with the SOC calibrated at a full charge
    frac = c_req_anchored / cap
    factor = eng.get("current_underread_factor")
    out["requirements"] = {
        "B": {"implied_capacity_ah_if_anchored_at_full": round(c_req_anchored, 1), "fraction_of_stated": round(frac, 3),
              "statement": f"B alone needs a usable capacity of about {c_req_anchored:.0f} Ah ({100 * frac:.0f} % of the stated {cap:g} Ah), "
                           f"assuming the SOC was last calibrated at a full charge; any other anchor needs less."},
        "C": {"underread_factor": factor,
              "statement": None if not factor else f"C alone needs the real discharge to be about {factor:g}x the measured current during operation."}}

    # ---- 1. record evidence (only this can raise or weaken a cause) -------------------------------------------------
    G: Dict[str, Dict[str, Any]] = {h: {"record_evidence": [], "requirement": None, "prior": None} for h in ("A", "B", "C")}
    G["B"]["requirement"] = out["requirements"]["B"]["statement"] + (" That is beyond the common end-of-life range (reference knowledge), or a capacity "
                                                                      "parameter that does not match the pack." if frac < B_EXTREME_FRACTION else "")
    G["C"]["requirement"] = out["requirements"]["C"]["statement"]
    if pre:
        G["A"]["record_evidence"].append({"claim": out["onset"]["statement"], "basis": "computed:onset", "direction": "consistent"})
    if corr_pp >= 10:
        G["A"]["record_evidence"].append({"claim": f"A {corr_pp:.0f} pp correction moved the SOC with almost no counted charge: the estimate was wrong, "
                                                   "the charge was not lost in seconds.", "basis": "fact:soc_correction", "direction": "consistent"})
    lt = ((audit or {}).get("load_transitions") or {})
    verdict = lt.get("value", {}).get("verdict")
    if verdict == "not_seen":
        G["C"]["record_evidence"].append({"claim": lt["statement"], "basis": "computed:load_transitions", "direction": "supports"})
    elif verdict == "seen":
        G["C"]["record_evidence"].append({"claim": lt["statement"] + " (weakens a bypassing load, C2; a scale error, C1, stays open)",
                                          "basis": "computed:load_transitions", "direction": "weakens_mechanism"})
    sig = ((audit or {}).get("signal_independence") or {}).get("items") or []
    cz = next((i for i in sig if i["id"] == "current_when_open" and i["value"].get("exact_zero") == i["value"].get("frames")), None)
    if cz:
        G["C"]["record_evidence"].append({"claim": cz["statement"] + " (bounds an offset, not a scale error)", "basis": "computed:signal_independence",
                                          "direction": "weakens_mechanism"})
    oc = ((checks.get("ocv_consistency") or {}).get("windows") or {}).get("pattern") or {}
    pat = oc.get("pattern")
    if pat:                                          # the rested-voltage SOC pattern, when the OCV curve is available
        target = {"constant_offset": "A", "growth_with_throughput": "B", "growth_with_time": "C"}.get(pat)
        if target:
            G[target]["record_evidence"].append({"claim": f"Rested-voltage SOC against reported SOC: {pat.replace('_', ' ')}", "basis": "check:ocv_consistency",
                                                 "direction": "supports"})
    supported = [h for h in ("A", "B", "C") if any(e["direction"] == "supports" for e in G[h]["record_evidence"])]

    # ---- 2. prior order (labelled: never evidence, never used to reject) ----------------------------------------------
    verified = [v for v in (rules.get("verified_outcomes") or []) if _signature_matches(v.get("signature") or {}, pre, corr_pp, shape)]
    prior_lead, prior_basis = None, None
    if len(supported) == 1:
        prior_lead, prior_basis = supported[0], "record evidence"
    elif verified:
        counts: Dict[str, int] = {}
        for v in verified:
            counts[v["hypothesis"]] = counts.get(v["hypothesis"], 0) + 1
        prior_lead = max(counts, key=counts.get)
        prior_basis = (f"this customer's verified outcomes: {counts[prior_lead]} of {len(verified)} earlier incident(s) with the same signature "
                       f"({', '.join(v.get('id', '?') for v in verified[:3])}) were verified as {prior_lead}")
    elif pre and (corr_pp >= 10 or over >= 30) and shape["shape"] == "flat" and (estimator or "coulomb_counting") == "coulomb_counting":
        prior_lead, prior_basis = "A", ("reference knowledge, not this record: on a flat-OCV chemistry a coulomb-counted SOC drifts between anchors and is "
                                        "corrected late in one step (RK-SOC-COUNTING-DRIFT, RK-FLAT-OCV); unnoticed fade of this size in service is unusual "
                                        "(RK-FADE-END-OF-LIFE)")
    conditional = (prior_lead is None and pre and (corr_pp >= 10 or over >= 30) and shape["shape"] == "unknown" and not supported)
    for h in ("A", "B", "C"):
        G[h]["grade"] = "leading" if h == prior_lead else ("leading_if_flat_ocv" if (conditional and h == "A") else "possible")
        G[h]["reasons"] = G[h]["record_evidence"] + ([{"claim": G[h]["requirement"], "basis": "computed:requirement"}] if G[h]["requirement"] else [])
    G["A"]["mechanisms"] = [{"id": "A1", "statement": "no calibration anchor (full charge or long rest) for a long time: counting error accumulated"},
                            {"id": "A2", "statement": "small standby or sleep currents below the sensor's resolution went uncounted between anchors"},
                            {"id": "A3", "statement": "a wrong initialisation or reset set the SOC high"}]
    if prior_lead:
        G[prior_lead]["prior"] = prior_basis
        if prior_basis.startswith("reference"):
            G[prior_lead]["reasons"] += [{"claim": K[k]["statement"], "basis": f"reference:{k}"} for k in ("RK-SOC-COUNTING-DRIFT", "RK-FLAT-OCV")]
    if shape["shape"] == "flat" and shape["basis"] == "customer_rule":
        G["A"]["reasons"].append({"claim": f"Customer-confirmed chemistry: {shape.get('chemistry') or 'flat OCV'}.", "basis": "customer_rule:chemistry"})
    if rules.get("full_charge_interval_days"):
        G["A"]["reasons"].append({"claim": f"Customer-confirmed rule: these packs need a full charge at least every {rules['full_charge_interval_days']} days "
                                           "for SOC calibration.", "basis": "customer_rule:maintenance_interval"})
    al = (audit or {}).get("charge_alignment") or {}
    if al.get("value", {}).get("timing_only"):
        G["D"] = {"grade": "contributing_timing_only", "reasons": [{"claim": al["statement"], "basis": "computed:charge_alignment"},
                                                                {"claim": K["RK-WEAK-CELL-TIMING"]["statement"], "basis": "reference:RK-WEAK-CELL-TIMING"}]}
    out["grades"] = G
    if prior_lead:
        names = {"A": "the SOC estimate drifted between calibration anchors" + (" and was corrected only when the voltage left the plateau" if shape["shape"] == "flat" else ""),
                 "B": "the usable capacity is far below the stated parameter", "C": "the current sensor misses part of the discharge"}
        decisive = {"A": "DOC-BMS-HISTORY", "B": "DATA-METERED-CYCLE", "C": "DOC-LOAD-INVENTORY" if verdict == "not_seen" else "MEAS-INDEPENDENT-CURRENT"}[prior_lead]
        kind = "record evidence" if prior_basis == "record evidence" else "an ordering prior, not evidence from this record"
        out["leading"] = {"hypothesis": prior_lead, "basis": prior_basis, "basis_kind": "evidence" if prior_basis == "record evidence" else "prior",
                          "statement": f"Leading explanation (not confirmed; {kind}): {names[prior_lead]}. Basis: {prior_basis}. "
                                       f"It is checked first, by {decisive}.", "decisive_check": decisive}
    if conditional:
        out["conditional_leading"] = {"hypothesis": "A", "condition": "the chemistry has a flat OCV plateau (e.g. LFP)",
                                      "statement": "If the pack's chemistry has a flat OCV plateau (for example LFP), the SOC estimate drifting between calibration "
                                                   "anchors is the explanation to check first (reference knowledge, not this record). Confirm the chemistry and "
                                                   "read the history since the last full charge.", "decisive_check": "DOC-BMS-HISTORY"}
    return out


def _signature_matches(sig: Dict[str, Any], pre: bool, corr_pp: float, shape: Dict[str, Any]) -> bool:
    """Whether an earlier verified outcome's recorded signature matches this case (only the fields it states)."""
    if sig.get("onset") == "predates_record" and not pre:
        return False
    if sig.get("late_correction") and corr_pp < 10:
        return False
    if sig.get("ocv_shape") and shape.get("shape") not in (sig["ocv_shape"], "unknown"):
        return False
    return True
