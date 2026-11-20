"""Structured engineering hypotheses, constrained by deterministic checks and facts.

Each hypothesis has a ROLE that keeps symptom, protection and cause apart:
  observed_symptom      what the operator saw (HV lost, cannot drive)
  protective_response   a designed protection acting (contactors opening after an under-voltage escalation)
  upstream_cause        a candidate for why the protection had to act
  contributing_factor   something that can make it worse or earlier but cannot explain all facts on its own

and a STATUS from a fixed vocabulary (no numeric probabilities):
  observed            directly recorded (symptom / protective response)
  open                consistent with all evidence; the evidence does not separate it from the other open ones
  insufficient_alone  may contribute, but a stated fact contradicts it being the whole explanation
  weakened            evidence points against it; nothing in the data excludes it directly
  excluded            only by a VERIFIED outcome or direct physical evidence (never by an LLM, never by sequence)
  confirmed           only by a VERIFIED outcome (engineer-verified test/repair result)

Each carries supporting, contradicting and missing evidence; each item names its basis: a check result, a fact, a
verified outcome, or an assumption. Event ORDER is used only as "consistent with", never as causal proof.

`constrain_claims` holds model-generated claims to this assessment: it downgrades unverified root-cause assertions,
rejects exclusions the evidence does not support, flags a protective response named as the cause, a weak cell named
as the sole cause, a claim that measured charge explains a correction Check A found unexplained, and numeric
probabilities.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

ASSESSMENT_VERSION = "hypothesis-assessment-v1"
STATUSES = ("observed", "open", "insufficient_alone", "weakened", "excluded", "confirmed")
ROLES = ("observed_symptom", "protective_response", "upstream_cause", "contributing_factor")

# The hypothesis family below (and the planner's templates) describe a battery-ELECTRIC VEHICLE with an HV pack, a BMS and
# main contactors. It applies only to a machine whose signal profile DECLARES such a class; any other machine (a PX4
# drone, a quadruped, a profile that states no class) gets no family, no HV / BMS / contactor recommendations, and a
# statement of why. The check results themselves stay available.
FAMILY_ID = "battery_energy_shortfall"
FAMILY_MACHINE_CLASSES = ("battery_electric_vehicle",)
# words that only make sense for an HV vehicle pack; a model statement using them on another machine is rejected
HV_VEHICLE_TERMS = re.compile(r"\b(contactors?|precharge|pre-charge|high[- ]voltage|hv[- ]?(qualified|work|procedure|closure|loss|lost|pack|bus|interlock)?|"
                              r"bms|battery management system|dc-?dc|vcu|restart polic\w*|insulation)\b", re.I)


def machine_of(profile) -> Dict[str, Any]:
    """What the signal profile declares about the machine (class and conditions). Nothing is inferred from the data."""
    d = getattr(profile, "data", None) or {}
    return {"machine_class": d.get("machine_class"), "conditions": dict(d.get("conditions") or {}), "profile": getattr(profile, "id", None)}


def family_scope(machine: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Whether the battery-electric-vehicle hypothesis family applies to this machine, with the reasons."""
    mc = (machine or {}).get("machine_class")
    cond = (machine or {}).get("conditions") or {}
    if not mc:
        return {"family": FAMILY_ID, "applies": False, "machine_class": None,
                "reasons": ["the signal profile states no machine class; the battery-electric-vehicle incident family (HV pack, BMS, contactors) "
                            "is used only for a declared " + " / ".join(FAMILY_MACHINE_CLASSES)]}
    if mc not in FAMILY_MACHINE_CLASSES:
        return {"family": FAMILY_ID, "applies": False, "machine_class": mc,
                "reasons": [f"machine class {mc!r} is not a battery-electric vehicle; HV, BMS, contactor and restart-policy hypotheses and checks do not apply"]}
    if cond.get("has_hv_contactors") is False:
        return {"family": FAMILY_ID, "applies": False, "machine_class": mc, "reasons": ["the profile states the machine has no HV contactors"]}
    return {"family": FAMILY_ID, "applies": True, "machine_class": mc, "reasons": [f"declared machine class {mc!r}"]}


@dataclass
class Item:
    claim: str
    basis: str                       # "check:<id>", "fact:<id>", "outcome:<id>", "assumption"
    evidence: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {"claim": self.claim, "basis": self.basis, "evidence": self.evidence[:6]}


@dataclass
class Hypothesis:
    id: str
    label: str
    statement: str
    role: str
    status: str
    supporting: List[Item] = field(default_factory=list)
    contradicting: List[Item] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    status_reason: str = ""
    mechanisms: List[Dict[str, Any]] = field(default_factory=list)   # distinct ways it can act: each is tested on its own
    effects: Dict[str, str] = field(default_factory=dict)            # role -> measurement effect (app.physchecks.effects)

    def as_dict(self) -> Dict[str, Any]:
        d = {"id": self.id, "label": self.label, "statement": self.statement, "role": self.role, "status": self.status,
             "status_reason": self.status_reason, "supporting": [i.as_dict() for i in self.supporting],
             "contradicting": [i.as_dict() for i in self.contradicting], "missing": self.missing}
        if self.mechanisms:
            d["mechanisms"] = self.mechanisms
        if self.effects:
            d["effects"] = self.effects
        return d


def _ev(f: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return (f or {}).get("evidence") or []


def assess(checks: Dict[str, Dict[str, Any]], facts: Dict[str, Dict[str, Any]],
           outcomes: Optional[List[Dict[str, Any]]] = None, *, machine: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Build the hypothesis set for a battery energy-shortfall incident from check results and facts.
    `outcomes` are VERIFIED outcomes for this case (from outcomes.verified_for); only they can confirm or exclude.
    `machine` (machine_of(profile)) is required: the family applies only to a declared battery-electric vehicle."""
    scope = family_scope(machine)
    out = _assess(checks, facts, outcomes) if scope["applies"] else {
        "version": ASSESSMENT_VERSION, "family": None, "hypotheses": [], "quantities": {},
        "note": "The battery-electric-vehicle incident family does not apply to this machine: " + "; ".join(scope["reasons"]) +
                ". The physical check results stand on their own; no HV, BMS or contactor hypothesis or check is proposed."}
    out["machine"] = machine
    out["family_scope"] = scope
    return out


def _cell_physics(checks: Dict[str, Dict[str, Any]], H: Dict[str, "Hypothesis"]) -> None:
    """Evidence from the cell-physics checks (an external EE advisor's proposal, not yet field-validated): added as supporting /
    contradicting / missing items with their basis. They never change a status on their own."""
    def ev(f):
        return [e for e in (f or {}).get("evidence") or []][:3]
    pc, wc, rx, oc = (checks.get(k) or {} for k in ("pack_cell_consistency", "weak_cell_attribution", "relaxation", "ocv_consistency"))
    e, d = H.get("E"), H.get("D")
    if e is not None:
        if pc.get("outcome") == "VIOLATION":
            f = next((x for x in pc.get("findings") or [] if x["kind"] == "pack_cell_mismatch"), None)
            e.supporting.append(Item(pc["summary"], "check:pack_cell_consistency", ev(f)))
        elif pc.get("outcome") == "PASS":
            e.contradicting.append(Item("Pack voltage / series count stays within the recorded lowest and highest cell (a bound check: a sensing error that "
                                        "keeps the average inside the range is not seen).", "check:pack_cell_consistency"))
        sup = ((wc.get("windows") or {}).get("attribution") or {}).get("supported") or []
        if any(s_["explanation"] == "sensing" for s_ in sup):
            e.supporting.append(Item(next(s_["meaning"] for s_ in sup if s_["explanation"] == "sensing") + " (the lowest cell's deficit)", "check:weak_cell_attribution"))
        e.missing.append("An independent voltage reference on the suspect cells: BMS-reported voltages alone cannot exclude a sensing error")
    if d is not None:
        att = (wc.get("windows") or {}).get("attribution") or {}
        for s_ in att.get("supported") or []:
            if s_["explanation"] in ("resistance_or_connection", "capacity_or_imbalance"):
                d.supporting.append(Item(f"The lowest cell's deficit {s_['meaning']} ({s_['explanation'].replace('_', ' ')})", "check:weak_cell_attribution"))
        for x in att.get("not_separable") or []:
            d.missing.append(f"{x['explanation'].replace('_', ' ')}: not decidable from this record ({x['because']})")
        for f in rx.get("findings") or []:
            if f["kind"] == "relaxation_deficit":
                d.supporting.append(Item(f["statement"], "check:relaxation", ev(f)))
        d.missing.append("Whether the weak cell is degraded or only out of balance: which cell reaches the voltage limits first in an existing, "
                         "independently metered charge/discharge record (initial balance, temperature and balancing accounted for)")
    pat = ((oc.get("windows") or {}).get("pattern") or {}).get("pattern")
    if oc.get("outcome") in ("VIOLATION", "INDETERMINATE") and pat:
        for hid in ("A", "B", "C"):
            if hid in H:
                H[hid].supporting.append(Item(f"Rested-voltage SOC against reported SOC: {oc['summary']}", "check:ocv_consistency"))
    elif oc.get("outcome") == "INDETERMINATE":
        for hid in ("A", "B", "C"):
            if hid in H:
                H[hid].missing.append("The chemistry's OCV curve at temperature, to read SOC from the rested voltage (constant offset, growth with throughput "
                                      "and growth with time point to different causes)")
    ind = (checks.get("charge_accounting") or {}).get("independence") or []
    if ind and "A" in H:
        if any(x.get("independent") is False for x in ind):
            H["A"].missing.append("SOC following the measured charge is not independent evidence (SOC is counted from that current): it shows the estimator "
                                  "counted, not that the current or the capacity is right")
        else:
            H["A"].missing.append("How the BMS computes SOC: until it is stated, SOC following the measured charge cannot count as evidence that the "
                                  "current or the capacity is right")


def _assess(checks: Dict[str, Dict[str, Any]], facts: Dict[str, Dict[str, Any]],
            outcomes: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    ca, ps, re_ = checks.get("charge_accounting") or {}, checks.get("protection_sequence") or {}, checks.get("restart_eligibility") or {}
    H: List[Hypothesis] = []
    sds = ((re_.get("windows") or {}).get("protective_shutdowns") or {}).get("items") or []
    applies = bool(sds) or ca.get("outcome") == "VIOLATION"
    if not applies:
        return {"version": ASSESSMENT_VERSION, "family": None, "hypotheses": [], "quantities": {},
                "note": "No protective shutdown and no unexplained SOC correction: the battery energy-shortfall hypothesis family does not apply."}

    if sds:
        sd = sds[0]
        H.append(Hypothesis("S1", "HV lost", f"High voltage was lost ({sd['window']}, t={sd['t']} s): the machine could not drive.",
                            "observed_symptom", "observed", [Item("Both main contactors report open", "check:restart_eligibility", sd["evidence"][:1])],
                            status_reason="directly recorded"))
        seq = next((f for f in ps.get("findings") or [] if f["kind"] == "observed_sequence"), None)
        H.append(Hypothesis("P1", "Under-voltage protection", "The contactors opened as the BMS's protective response to a cell under-voltage escalation.",
                            "protective_response", "observed",
                            [Item(seq["statement"] if seq else f"HV opened {round(sd['t'] - sd['trigger_t'], 3)} s after the fault level rose to {sd['trigger_level']:g}",
                                  "check:protection_sequence" if seq else "check:restart_eligibility", sd["evidence"][1:3])],
                            missing=["The customer's protection specification (thresholds, delays, required order) to judge whether the sequence was correct"],
                            status_reason="the escalation preceded the opening; that order fits a protective action. Order is not proof of cause; "
                                          "the protection explains HOW HV was lost, not WHY the cells were that low."))

    corr = facts.get("soc_correction")
    eng = facts.get("energy_shortfall")
    track = [f for f in ca.get("findings") or [] if f["kind"] == "soc_tracks_measured_charge"]
    ratios = [f["values"].get("ratio_reported_to_expected") for f in track if f["values"].get("ratio_reported_to_expected")]
    common_support: List[Item] = []
    if eng:
        common_support.append(Item(eng["statement"], "fact:energy_shortfall", _ev(eng)))

    # A: SOC estimation / calibration
    a = Hypothesis("A", "SOC estimation / calibration error",
                   "The SOC estimate had drifted high (coulomb counting without effective correction) and was only corrected at the under-voltage alarm.",
                   "upstream_cause", "open", list(common_support), effects={"soc": "estimate_high"})
    if ratios:
        a.supporting.append(Item(f"Between corrections SOC followed measured charge / stated capacity (ratio {', '.join(str(r) for r in ratios[:4])}): "
                                 "the estimator was counting charge, with no visible voltage-based correction", "check:charge_accounting",
                                 [e for f in track[:2] for e in f["evidence"]]))
    if corr:
        a.supporting.append(Item(corr["statement"] + " The estimator re-stated SOC without the charge to account for it.", "check:charge_accounting", _ev(corr)))
    a.missing += ["SOC algorithm: when OCV, full-charge and low-end corrections trigger", "Charge and calibration history since the last full charge"]
    H.append(a)

    # B: usable capacity far below the stated parameter
    b = Hypothesis("B", "Usable capacity far below the stated parameter",
                   "The pack's usable capacity is far below the capacity the SOC is computed against (fade, or a wrong parameter).",
                   "upstream_cause", "open", list(common_support), effects={"rated_capacity": "capacity_low"})
    if eng:
        v = eng["value"]
        b.contradicting.append(Item(f"Alone it requires usable capacity of about {v['implied_capacity_ah']} Ah against the stated "
                                    f"{round(v['promised_ah'] / (v['soc_first_pct'] / 100))} Ah: a very large shortfall, with no capacity test either way",
                                    "fact:energy_shortfall", _ev(eng)))
    b.missing += ["An existing, independently metered charge/discharge record (before any controlled capacity test: cycling a possibly faulty "
                  "pack is itself a risk)", "Pack build record: the capacity parameter's origin"]
    H.append(b)

    # C: current under-measured / load bypassing the sensor
    c = Hypothesis("C", "Current under-measured or load bypassing the sensor",
                   "The BMS current sensor under-reads, or some load draws from the pack upstream of it, so counted charge is below real discharge.",
                   "upstream_cause", "open", list(common_support),
                   mechanisms=[{"id": "C1", "statement": "the current sensor under-reads (scale or offset error)", "effects": {"pack_current": "scale_low"}},
                               {"id": "C2", "statement": "a load draws from the pack upstream of the sensor, so the sensor never sees it",
                                "effects": {"pack_current": "unseen_load"}}])
    xc = facts.get("current_cross_check")
    if xc:
        c.contradicting.append(Item(xc["statement"], "fact:current_cross_check", _ev(xc)) if xc["value"]["independent"] else
                               Item(xc["statement"] + " It neither supports nor weakens C.", "fact:current_cross_check", _ev(xc)))
    if eng and eng["value"].get("current_underread_factor"):
        f = eng["value"]["current_underread_factor"]
        load = facts.get("load")
        c.contradicting.append(Item(f"Alone it requires the real discharge to be about {f}x the measured current"
                                    + (f" (measured mostly {load['value']['p5']}–{load['value']['p95']} A)" if load else "") + ": a very large error",
                                    "fact:energy_shortfall", _ev(eng)))
    c.missing += ["Independent current measurement against the BMS reading", "Standstill load list: which loads bypass the current sensor"]
    H.append(c)

    # D: weak cell / imbalance, as a contributor
    d = Hypothesis("D", "Weak cell / imbalance", "One cell (or a few) is weaker or poorly balanced and reaches the cutoff first.",
                   "contributing_factor", "open")
    li = facts.get("lowest_cell_index")
    if li:
        d.supporting.append(Item(li["statement"], "fact:lowest_cell_index", _ev(li)))
    cf, ct = facts.get("cells_first_record"), facts.get("cells_at_trip")
    if cf and ct:
        d.supporting.append(Item(f"Cell spread grew from {cf['value']['spread_mv']} mV at the first record to {ct['value']['spread_mv']} mV at the trip",
                                 "fact:cells_at_trip", _ev(cf) + _ev(ct)))
    pw = facts.get("pack_wide_decline")
    if pw and pw["value"]["pack_wide"]:
        d.contradicting.append(Item(pw["statement"], "fact:pack_wide_decline", _ev(pw)))
        if corr or eng:
            d.contradicting.append(Item("A weak cell makes the cutoff come early, but it does not change how the BMS counts pack charge: it cannot explain "
                                        "SOC following measured charge and then being corrected by tens of points, nor the pack-level energy shortfall.",
                                        "check:charge_accounting", _ev(corr) or _ev(eng)))
        d.status = "insufficient_alone"
        d.status_reason = ("the highest cell was also depleted (pack-wide decline larger than the cell spread), and the SOC accounting discrepancy is "
                           "pack-level: a weak cell can be an additional contributor, not the whole explanation")
    d.missing += ["Rest voltage of every cell (not only highest/lowest)", "Capacity test with all cell voltages logged"]
    H.append(d)

    # E: cell-voltage sensing / harness fault
    e = Hypothesis("E", "Cell-voltage sensing or harness fault", "The low cell reading is a sensing error (sense lead, connector, measurement channel).",
                   "upstream_cause", "open")
    sm, rb = facts.get("lowest_cell_smooth"), facts.get("rest_rebound")
    if sm:
        e.contradicting.append(Item(sm["statement"] + " A sense-lead fault typically shows jumps.", "fact:lowest_cell_smooth", _ev(sm)))
    if pw and pw["value"]["pack_wide"]:
        e.contradicting.append(Item("The highest cell fell too, by more than the cell spread; a sensing fault on the lowest cell's channel would not move it.", "fact:pack_wide_decline", _ev(pw)))
    if rb:
        e.contradicting.append(Item(rb["statement"] + " Recovery at rest follows a real cell.", "fact:rest_rebound", _ev(rb)))
    if e.contradicting:
        e.status = "weakened"
        e.status_reason = "smooth decline, pack-wide fall and physical rest recovery point against a sensing fault; no direct inspection excludes it"
    e.missing += ["Inspection of the cell sense harness and connectors (qualified personnel)"]
    H.append(e)

    # F: contactor / VCU / insulation event first
    f_ = Hypothesis("F", "Contactor, VCU or insulation event first", "A contactor, VCU or insulation fault came first and the battery alarms followed.",
                    "upstream_cause", "open")
    if sds:
        f_.contradicting.append(Item(f"The contactors opened {round(sds[0]['t'] - sds[0]['trigger_t'], 3)} s AFTER the fault level rose to {sds[0]['trigger_level']:g}, "
                                     "and the under-voltage alarms started much earlier: the recorded order does not fit this one (order is not proof).",
                                     "check:restart_eligibility", sds[0]["evidence"][:2]))
        f_.status = "weakened"
        f_.status_reason = "the recorded order runs from cell under-voltage to contactor opening, not the reverse"
    f_.missing += ["Insulation readings in a usable range (the recorded field may be saturated)", "Contactor command signals (only feedback is recorded)"]
    H.append(f_)

    _cell_physics(checks, {h.id: h for h in H})

    # verified outcomes override
    for o in outcomes or []:
        for h in H:
            if h.id == o.get("hypothesis_id") and o.get("verified"):
                if o["result"] == "confirmed":
                    h.status, h.status_reason = "confirmed", f"verified outcome {o['id']}: {o['summary']}"
                elif o["result"] == "excluded":
                    h.status, h.status_reason = "excluded", f"verified outcome {o['id']}: {o['summary']}"
                (h.supporting if o["result"] == "confirmed" else h.contradicting).append(Item(o["summary"], f"outcome:{o['id']}"))

    open_up = [h.id for h in H if h.role == "upstream_cause" and h.status == "open"]
    for h in H:
        if h.status == "open" and not h.status_reason:
            others = [x for x in open_up if x != h.id]
            h.status_reason = ("consistent with all recorded evidence; the recorded data does not separate it from " + ", ".join(others)) if others else \
                              "consistent with all recorded evidence"
    quantities = {}
    if corr:
        quantities["soc_correction_pp"] = {"value": abs(corr["value"]["unexplained_pp"]), "meaning": "abrupt SOC re-statement by the estimator, not explained by measured charge",
                                           "basis": "check:charge_accounting"}
    if eng:
        quantities["energy_shortfall_overstatement_pp"] = {"value": eng["value"]["overstatement_pp"], "meaning": "how far the first recorded SOC overstated the charge actually delivered to cutoff",
                                                           "basis": "fact:energy_shortfall", "estimated": eng["estimated"]}
    if len(quantities) == 2:
        quantities["note"] = ("These are different quantities: the correction is one abrupt estimator step inside the fault window; the shortfall spans windows and "
                              "includes an estimate for unrecorded time. Do not add or substitute one for the other.")
    return {"version": ASSESSMENT_VERSION, "family": "battery_energy_shortfall", "hypotheses": [h.as_dict() for h in H],
            "open_upstream": open_up, "quantities": quantities,
            "summary": _summary(H)}


def _summary(H: List[Hypothesis]) -> str:
    by = {h.id: h for h in H}
    open_up = [h.label for h in H if h.role == "upstream_cause" and h.status == "open"]
    parts = []
    if "P1" in by:
        parts.append("HV loss was the BMS under-voltage protection acting (protective response, not the origin).")
    if open_up:
        parts.append("Open upstream explanations: " + "; ".join(open_up) + ". The recorded data does not separate them.")
    d = by.get("D")
    if d and d.status == "insufficient_alone":
        parts.append("A weak cell cannot explain the whole incident on its own: " + d.status_reason + ".")
    conf = [h.label for h in H if h.status == "confirmed"]
    parts.append(("Confirmed by verified outcome: " + "; ".join(conf) + ".") if conf else "No root cause is confirmed.")
    return " ".join(parts)


# ---------------------------------------------------------------- holding model claims to the assessment
KEYWORDS = {
    "A": ("soc estimat", "calibrat", "coulomb", "soc drift", "estimator", "soc error", "ocv"),
    "B": ("capacity", "fade", "degrad", "aged", "ageing", "aging"),
    "C": ("current sensor", "current measurement", "shunt", "bypass", "unmetered", "parasitic", "hall sensor", "under-read"),
    "D": ("weak cell", "lowest cell", "imbalance", "balanc", "single cell", "one cell"),
    "E": ("sense lead", "sensing", "harness", "voltage tap", "measurement channel"),
    "F": ("insulation", "contactor fault", "vcu fault", "relay fault", "welded"),
    # the protective mechanism named as an AGENT of the outcome (the HV loss itself is the effect, not a keyword here)
    "P1": ("contactor", "protection", "under-voltage protection", "undervoltage protection"),
}
CAUSE_WORDS = ("root cause", "caused", "the cause", "is due to", "because of", "responsible for", "confirmed", "definitely", "proven")
PROB = re.compile(r"(\d{1,3}\s?%\s*(probab|likel|chance|confiden|certain))|(probability\s*(of|=|:)?\s*0?\.\d+)|((probab|likel|confiden)\w*\s*(of|at|is|=|:)?\s*\d{1,3}\s?%)", re.I)
CHARGE_EXPLAINS = re.compile(r"(explained|accounted for|consistent with|matches)\s+(by|with)?\s*(the\s+)?(measured\s+)?(current|charge|discharge)", re.I)


# A cause word inside a negation ("no root cause is confirmed", "not the origin") or inside a report of ANOTHER case
# ("A was confirmed by engineer B in an earlier case") is not a causal claim about this recording.
NEGATED = re.compile(r"\b(no|not|never|cannot|can't|isn't|without|neither|nor|unconfirmed|unverified)\b[^.;:]{0,40}?(root cause|cause|confirm|origin|proven)"
                     r"|(root cause|the cause)[^.;:]{0,30}?\b(not|unconfirmed|unverified|remains open|is open|pending)\b"
                     r"|needs? an? engineer[- ]verified|until (it is |they are )?verified", re.I)
ELSEWHERE = re.compile(r"\b(earlier|previous|prior|another|other)\b[^.;:]{0,60}\b(case|incident|recording|vehicle)\b|\bconfirmed (by|in)\b", re.I)


def causal_sentences(text: str) -> List[str]:
    """The sentences of `text` that assert a cause about this recording."""
    return [x for x in re.split(r"(?<=[.;!?])\s+", text)
            if any(w in x.lower() for w in CAUSE_WORDS) and not NEGATED.search(x) and not ELSEWHERE.search(x)]


def _guess(text: str) -> List[str]:
    t = text.lower()
    out = [h for h, kws in KEYWORDS.items() if any(k in t for k in kws)]
    if "D" not in out and re.search(r"\bcell\s*#?\d+\b", t):     # a numbered cell ("cell 20", "cell #7")
        out.append("D")
    return out


def constrain_claims(claims: List[Dict[str, Any]], assessment: Dict[str, Any], checks: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Each claim: {text, hypothesis?, asserted_status?, source}. Returns each with a verdict and reasons."""
    hyp = {h["id"]: h for h in assessment.get("hypotheses") or []}
    scope = assessment.get("family_scope") or {}
    out = []
    for cl in claims:
        text = cl.get("text") or ""
        if scope.get("applies") is False and HV_VEHICLE_TERMS.search(text):
            term = HV_VEHICLE_TERMS.search(text).group(0)
            out.append({**cl, "hypotheses": [], "verdict": "rejected", "constrained_status": None,
                        "reasons": [f"uses '{term}', a battery-electric-vehicle concept, but that family does not apply here: " + "; ".join(scope.get("reasons") or [])]})
            continue
        ids = [cl["hypothesis"]] if cl.get("hypothesis") else _guess(text)
        asserted = (cl.get("asserted_status") or "").lower()
        sents = causal_sentences(text)
        causal = asserted in ("root_cause", "confirmed", "cause") or bool(sents)
        # which hypotheses the causal assertion is about: the stated one, else those named in the causal sentences only
        causal_ids = set(ids) if (cl.get("hypothesis") or asserted in ("root_cause", "confirmed", "cause")) else set(_guess(" ".join(sents)))
        verdict, reasons, status = "accepted", [], None
        if PROB.search(text):
            verdict = "flagged"
            reasons.append("states a numeric probability; the evidence supports ordinal statuses only")
        if CHARGE_EXPLAINS.search(text) and "soc" in text.lower() and (checks.get("charge_accounting") or {}).get("outcome") == "VIOLATION" \
                and not re.search(r"\bnot\b|\bcannot\b|\bunexplained\b", text.lower()):
            verdict = "rejected"
            reasons.append("contradicts Check A: " + checks["charge_accounting"]["summary"])
        for hid in ids:
            h = hyp.get(hid)
            if not h:
                continue
            if causal and hid not in causal_ids:
                pass
            elif causal and h["role"] == "protective_response":
                verdict = "rejected"
                reasons.append("names the protective response as the cause: the contactors opening is HOW HV was lost, not WHY the cells were depleted")
            elif causal and h["status"] == "insufficient_alone" and not re.search(r"contribut|additional|partly|secondary", text.lower()):
                verdict = "rejected"
                reasons.append(f"claims {h['label']} as the cause, but: {h['status_reason']}")
            elif causal and h["status"] != "confirmed":
                if verdict == "accepted":
                    verdict = "downgraded"
                status = "proposed"
                reasons.append(f"{h['label']} is '{h['status']}' in the deterministic assessment; a cause needs an engineer-verified outcome. Recorded as a proposal.")
            if asserted == "excluded" and h["status"] not in ("excluded",):
                verdict = "rejected"
                reasons.append(f"claims {h['label']} is excluded; no verified outcome or direct evidence excludes it (status '{h['status']}')")
        out.append({**cl, "hypotheses": ids, "verdict": verdict, "reasons": reasons, "constrained_status": status or (hyp[ids[0]]["status"] if ids and ids[0] in hyp else None)})
    return out
