"""Next decisive check: which measurement or document separates the hypotheses that are still open.

Each template states, per hypothesis, the observation it PREDICTS; a check discriminates a pair of open hypotheses when
their predictions differ. Templates also state what would confirm or weaken each hypothesis, what is required, the
preconditions, effort (ordinal; cost only when known), and SAFETY:

  hv_exposure       the work touches or is near high voltage
  qualification     none < trained_technician < hv_qualified

Each template states the high-voltage state it needs (hv_state: none = no contact with the machine, open = HV isolated,
closed = HV energised). The action safety gate (app.agent.action_gate) uses it with the safety holds.

Safety is a HARD constraint, not a penalty: a check that needs a qualification the requester does not have is never
recommended to that requester. It is listed separately as a hand-off ("requires HV-qualified personnel, under the
customer's procedures"). Safety holds derived from the checks (e.g. cells discharged below the original trip level after
repeated restarts) come before any ranking.

Ranking (documented lexicographic rule, no probabilities):
  0. when the assessment names a LEADING explanation (plausibility layer), a no-contact check that can confirm or weaken
     it comes first: the cheapest way to test the ranked proposal
  1. number of open-hypothesis pairs the check separates (more first)
  2. effort: low < medium < high
  3. no HV exposure before HV exposure
  4. template id (stable)

The planner also states plainly when the RECORDED telemetry cannot separate the open hypotheses: under each of them the
recorded signals look the same, so another recording of the same signals will not help.
"""
from __future__ import annotations

from itertools import combinations
from typing import Any, Dict, List, Optional

PLANNER_VERSION = "next-decisive-check-v2"
QUAL = {"none": 0, "trained_technician": 1, "hv_qualified": 2}
EFFORT = {"low": 0, "medium": 1, "high": 2}

TEMPLATES: List[Dict[str, Any]] = [
    {"id": "REC-TELEMETRY", "hv_state": "none", "title": "Another recording of the same signals (SOC, pack current, cell min/max, states)", "kind": "telemetry",
     "predictions": {"A": "SOC follows measured charge / stated capacity, then an abrupt correction near the under-voltage alarm",
                     "B": "SOC follows measured charge / stated capacity, then an abrupt correction near the under-voltage alarm",
                     "C": "SOC follows measured charge / stated capacity, then an abrupt correction near the under-voltage alarm"},
     "requires": ["the same recorded signals"], "preconditions": [], "safety": {"hv_exposure": False, "qualification": "none"},
     "effort": "low", "who": "Veyra (automatic)", "pseudo": True},
    {"id": "DOC-BMS-HISTORY", "hv_state": "none", "title": "BMS charge and calibration history since the last full charge", "kind": "document",
     "predictions": {"A": "no full-charge calibration for a long time (beyond the customer's recommended interval, if one exists), long standby or sleep "
                          "periods with currents near zero, or a charge that ended early yet set SOC high",
                     "B": "recent full charges with calibration, and still cutoff after a small fraction of the stated capacity",
                     "C": "BMS-counted discharge throughput clearly below the energy the charger delivered"},
     "confirms": {"A": "the last calibration point is old or wrong, and SOC drift since then matches the shortfall",
                  "B": "full calibrations are recent and each full charge accepts far less than the stated capacity",
                  "C": "charge in (charger) and counted charge out disagree by much more than sensor accuracy"},
     "weakens": {"A": "SOC was calibrated at a full charge shortly before", "B": "full charges accept close to the stated capacity",
                 "C": "charger energy and BMS throughput agree"},
     "requires": ["BMS event/history export", "charger log for the same period"], "preconditions": ["read-only export; no change to the vehicle"],
     "safety": {"hv_exposure": False, "qualification": "none"}, "effort": "low", "who": "Customer BMS / software engineer"},
    {"id": "DOC-SOC-ALGORITHM", "hv_state": "none", "title": "SOC algorithm: when OCV, full-charge and low-end corrections trigger", "kind": "document",
     "predictions": {"A": "corrections only at full charge and at the under-voltage alarm (nothing in between)",
                     "B": "regular voltage-based corrections exist (a pure counting drift would have been caught)",
                     "C": "regular voltage-based corrections exist (a pure counting drift would have been caught)"},
     "confirms": {"A": "the algorithm has no correction between full charge and the under-voltage alarm"},
     "weakens": {"A": "regular OCV/low-end corrections exist and should have fired at rest"},
     "requires": ["BMS supplier documentation"], "preconditions": [], "safety": {"hv_exposure": False, "qualification": "none"},
     "effort": "low", "who": "Customer BMS engineer / supplier"},
    {"id": "DOC-LOAD-INVENTORY", "hv_state": "none", "title": "Standstill load list and where each load connects relative to the current sensor", "kind": "document",
     "predictions": {"A": "every load is downstream of the current sensor", "B": "every load is downstream of the current sensor",
                     "C": "at least one load draws from the pack upstream of the sensor"},
     "confirms": {"C": "a load bypasses the sensor and its draw is comparable to the shortfall"}, "weakens": {"C": "all loads pass the sensor"},
     "requires": ["electrical schematic", "load list at standstill/READY"], "preconditions": [],
     "safety": {"hv_exposure": False, "qualification": "none"}, "effort": "low", "who": "Customer electrical engineer"},
    {"id": "MEAS-CELL-REST", "covers": ["measure", "handle", "procedure", "observe"], "hv_state": "open", "title": "Rest voltage of every cell, read through the BMS diagnostic tool", "kind": "measurement",
     "predictions": {"D": "the persistently lowest cell (or a few) well below the others at rest", "A": "cells close together", "B": "cells close together",
                     "C": "cells close together"},
     "confirms": {"D": "a persistent, large gap between the weakest cells and the rest at rest"},
     "weakens": {"D": "all cells within a small spread at rest"},
     "requires": ["BMS diagnostic tool on the low-voltage diagnostic connector"],
     "preconditions": ["vehicle at rest, HV open", "no opening of the pack or HV connectors"],
     "safety": {"hv_exposure": False, "qualification": "trained_technician"}, "effort": "low", "who": "Trained service technician",
     "also_safety": "assesses possible over-discharge before any further HV closure or charging"},
    {"id": "MEAS-INDEPENDENT-CURRENT", "covers": ["measure", "handle", "energise", "procedure", "observe"], "hv_state": "closed", "title": "Independent DC current measurement against the BMS reading at a steady load", "kind": "measurement",
     "predictions": {"A": "meter and BMS agree within sensor accuracy", "B": "meter and BMS agree within sensor accuracy",
                     "C": "the meter reads clearly more than the BMS, or sees a load the BMS does not"},
     "confirms": {"C": "a difference far beyond the sensor's stated accuracy"}, "weakens": {"C": "agreement within accuracy at representative loads"},
     "requires": ["calibrated DC clamp meter rated for the pack voltage", "steady load (e.g. READY at standstill)"],
     "preconditions": ["customer HV procedure", "cells above the manufacturer's recovery limit (see MEAS-CELL-REST)"],
     "safety": {"hv_exposure": True, "qualification": "hv_qualified"}, "effort": "medium", "who": "HV-qualified engineer"},
    {"id": "TEST-CAPACITY", "covers": ["measure", "handle", "energise", "charge_discharge", "procedure", "observe"], "hv_state": "closed", "title": "Controlled capacity test: full charge, then constant-current discharge with an independent meter and all cell voltages", "kind": "test",
     "predictions": {"A": "measured capacity close to the stated value; the BMS SOC drifts away from it during the test",
                     "B": "measured capacity far below the stated value",
                     "C": "the meter's Ah differs from the BMS-counted Ah",
                     "D": "the persistently lowest cell reaches the cutoff clearly before the others"},
     "confirms": {"A": "capacity near stated and SOC error reproduces", "B": "capacity far below stated", "C": "meter and BMS Ah disagree beyond accuracy",
                  "D": "one cell limits the discharge while others retain charge"},
     "weakens": {"A": "SOC tracks the measured capacity throughout", "B": "capacity near stated", "C": "meter and BMS Ah agree", "D": "all cells reach cutoff together"},
     "requires": ["controlled charger and load", "independent calibrated current meter", "all-cell voltage logging"],
     "preconditions": ["only if no existing independently metered record answers the question (DATA-METERED-CYCLE first): cycling a "
                       "possibly faulty pack is itself a risk",
                       "battery manufacturer's procedure", "every cell above the recovery limit; otherwise the manufacturer's recovery procedure first",
                       "customer HV procedure and safety supervision"],
     "safety": {"hv_exposure": True, "qualification": "hv_qualified"}, "effort": "high", "who": "HV-qualified test engineers"},
    # From an external EE advisor's first-principles proposal (2026-11-16): ask for existing, independently metered records before any new
    # cycling of a possibly faulty pack; read the relaxation after the shutdown; get the OCV curve before reading SOC from voltage.
    {"id": "DATA-METERED-CYCLE", "hv_state": "none", "title": "An existing, independently metered charge and discharge record with known endpoints (no new cycling of a possibly faulty pack)",
     "kind": "data_request",
     "predictions": {"A": "metered Ah agrees with the BMS-counted Ah; the BMS SOC at the endpoints disagrees with the metered state",
                     "B": "metered usable capacity far below the stated value",
                     "C": "the meter's Ah differs from the BMS-counted Ah",
                     "D": "one cell reaches the voltage limits first (with initial balance, temperature and balancing activity accounted for)"},
     "confirms": {"B": "metered capacity far below stated", "C": "meter and BMS Ah disagree beyond accuracy", "D": "one cell limits while others retain charge"},
     "weakens": {"B": "metered capacity near stated", "C": "meter and BMS Ah agree", "D": "all cells reach the limits together"},
     "requires": ["a charger, test-bench or service record with its own calibrated meter", "endpoints (full / empty) and temperature",
                  "balancing activity during the record"],
     "preconditions": ["an older record may not reflect the pack's condition at the incident; state its date"],
     "safety": {"hv_exposure": False, "qualification": "none"}, "effort": "low", "who": "Customer service / test engineering",
     "provenance": {"kind": "expert_proposal", "source": "external EE advisor (2026-11-16)", "status": "not yet validated on field cases"}},
    {"id": "DATA-POST-SHUTDOWN", "hv_state": "none", "title": "Cell voltages and temperature after the shutdown at near-zero current, for as long as the BMS logged",
     "kind": "data_request",
     "predictions": {"D": "the lowest cell recovers like the others but rests lower (imbalance or capacity), or with a larger instant step (resistance / connection)",
                     "E": "the low cell's reading steps, or does not follow a physical relaxation"},
     "requires": ["BMS log after the HV opening (often recorded while the BMS stays awake)", "cell temperature"],
     "preconditions": ["BMS-reported voltages alone cannot exclude a sensing error"],
     "safety": {"hv_exposure": False, "qualification": "none"}, "effort": "low", "who": "Customer data engineer",
     "provenance": {"kind": "expert_proposal", "source": "external EE advisor (2026-11-16)", "status": "not yet validated on field cases"}},
    {"id": "DOC-OCV-CURVE", "hv_state": "none", "title": "The chemistry's OCV-SOC curve at temperature (with hysteresis), and the voltage and current sensor accuracy",
     "kind": "document",
     "predictions": {"A": "the rested voltage gives an SOC that disagrees with the reported SOC by a roughly constant amount",
                     "B": "the rested-voltage SOC changes more between rest points than counted charge / stated capacity predicts",
                     "C": "the disagreement grows with time rather than with charge throughput (a current offset)"},
     "requires": ["cell supplier's OCV table for this chemistry", "sensor datasheets"], "preconditions": ["flat regions (e.g. LFP plateaus) do not establish SOC"],
     "enables": ["ocv_consistency", "relaxation (SOC from the relaxed voltage)"],
     "safety": {"hv_exposure": False, "qualification": "none"}, "effort": "low", "who": "Customer battery engineer / cell supplier",
     "provenance": {"kind": "expert_proposal", "source": "external EE advisor (2026-11-16)", "status": "not yet validated on field cases"}},
    {"id": "DATA-EARLIER-RECORDS", "hv_state": "none", "title": "Records before the earliest window, and the real time between windows", "kind": "data_request",
     "predictions": {"A": "SOC and cell voltage begin to diverge after a specific event (e.g. an incomplete charge marked full)",
                     "B": "cell voltage falls faster than SOC from the last full charge on, with no single starting event"},
     "requires": ["earlier BMS/VCU recordings", "wall-clock times of the windows"], "preconditions": [],
     "safety": {"hv_exposure": False, "qualification": "none"}, "effort": "low", "who": "Customer data engineer"},
    {"id": "INSPECT-HARNESS", "covers": ["handle", "measure", "procedure", "observe"], "hv_state": "open", "title": "Inspect the sense harness and connectors of the persistently lowest cell, and the current-sensor connector", "kind": "inspection",
     "predictions": {"E": "damage, corrosion or a loose sense lead", "C": "a current-sensor connector or wiring fault",
                     "A": "nothing found", "B": "nothing found", "D": "nothing found"},
     "requires": ["pack access"], "preconditions": ["customer HV procedure", "pack de-energised and opened by qualified staff"],
     "safety": {"hv_exposure": True, "qualification": "hv_qualified"}, "effort": "high", "who": "HV-qualified technician"},
    {"id": "DOC-THRESHOLDS", "hv_state": "none", "title": "Cell chemistry, series count, under-voltage thresholds and delays per level, restart-permission logic", "kind": "document",
     "predictions": {}, "enables": ["a verified protection specification for Check B", "a verified restart policy for Check C"],
     "requires": ["BMS supplier documentation"], "preconditions": [], "safety": {"hv_exposure": False, "qualification": "none"},
     "effort": "low", "who": "Customer BMS engineer / supplier"},
]


def _pairs(t: Dict[str, Any], open_ids: List[str]) -> List[List[str]]:
    p = t.get("predictions") or {}
    return [[a, b] for a, b in combinations(sorted(open_ids), 2) if a in p and b in p and p[a] != p[b]]


def plan(assessment: Dict[str, Any], checks: Dict[str, Dict[str, Any]], *, requester_qualification: str = "none",
         done: Optional[List[str]] = None, prior: Optional[Dict[str, List[Dict[str, Any]]]] = None) -> Dict[str, Any]:
    """`prior` (knowledge.prior_for_planner): template id -> applicable verified results elsewhere that this check settled.
    Such a check goes first among the checks the requester may do; nothing else about the ranking or the gates changes."""
    if requester_qualification not in QUAL:
        raise ValueError(f"requester_qualification must be one of {list(QUAL)}")
    hyps = assessment.get("hypotheses") or []
    scope = assessment.get("family_scope") or {}
    if scope.get("applies") is False:
        return {"version": PLANNER_VERSION, "recommended": [], "handoff": [], "safety_holds": [], "telemetry_verdict": None,
                "machine_class": scope.get("machine_class"),
                "note": "No next-check templates exist for this machine: the battery-electric-vehicle templates (HV, BMS, contactors) do not apply ("
                        + "; ".join(scope.get("reasons") or []) + ")."}
    if scope.get("applies") is not True:
        raise ValueError("assessment has no family_scope: build it with hypotheses.assess(..., machine=hypotheses.machine_of(profile))")
    if not hyps:
        return {"version": PLANNER_VERSION, "recommended": [], "handoff": [], "safety_holds": [], "telemetry_verdict": None,
                "note": "no hypotheses to separate"}
    open_ids = [h["id"] for h in hyps if h["role"] in ("upstream_cause", "contributing_factor") and h["status"] in ("open", "insufficient_alone")]
    labels = {h["id"]: h["label"] for h in hyps}
    done = set(done or [])
    rq = QUAL[requester_qualification]

    holds: List[Dict[str, Any]] = []
    rc = checks.get("restart_eligibility") or {}
    deep = next((f for f in rc.get("findings") or [] if f["kind"] == "deeper_discharge_than_original_trip"), None)
    if deep:
        holds.append({"id": "HOLD-HV-RECLOSURE", "title": "Do not close HV or charge again until every cell's rest voltage has been assessed",
                      "why": deep["statement"], "basis": "check:restart_eligibility",
                      "action": "MEAS-CELL-REST by a trained technician; if any cell is below the manufacturer's recovery limit, follow the "
                                "manufacturer's procedure with HV-qualified staff before any charge or HV closure",
                      "type": "safety precaution, not a finding of fault",
                      "blocks_hv_state": ["closed"], "release_by": ["MEAS-CELL-REST"], "finding": "restart_eligibility:deeper_discharge_than_original_trip",
                      "provenance": {"kind": "engineering_rule", "source": f"Veyra planner {PLANNER_VERSION}",
                                     "status": "encoded by Veyra; not a customer specification and not yet reviewed by the customer"}})

    rows: List[Dict[str, Any]] = []
    for t in TEMPLATES:
        if t["id"] in done:
            continue
        pairs = _pairs(t, open_ids)
        informs = [h for h in open_ids if h in (t.get("predictions") or {})]
        need = QUAL[t["safety"]["qualification"]]
        rows.append({"id": t["id"], "title": t["title"], "kind": t["kind"], "hv_state": t.get("hv_state"), "distinguishes": pairs,
                     "distinguishes_labels": [f"{labels.get(a, a)} vs {labels.get(b, b)}" for a, b in pairs],
                     "informs": informs, "predictions": {h: t["predictions"][h] for h in informs},
                     "confirms": {h: v for h, v in (t.get("confirms") or {}).items() if h in open_ids},
                     "weakens": {h: v for h, v in (t.get("weakens") or {}).items() if h in open_ids},
                     "requires": t.get("requires", []), "preconditions": t.get("preconditions", []), "safety": t["safety"],
                     "effort": t["effort"], "cost": t.get("cost"), "who": t["who"], "enables": t.get("enables", []),
                     "also_safety": t.get("also_safety"), "pseudo": t.get("pseudo", False), "provenance": t.get("provenance"),
                     "allowed_for_requester": need <= rq,
                     "rank_key": [-len(pairs), EFFORT[t["effort"]], int(t["safety"]["hv_exposure"]), t["id"]]})
    tel = next((r for r in rows if r["id"] == "REC-TELEMETRY"), None)
    telemetry_verdict = None
    if tel is not None:
        upstream_open = [h for h in open_ids if (next(x for x in hyps if x["id"] == h)["role"] == "upstream_cause")]
        if not tel["distinguishes"] and len(upstream_open) >= 2:
            telemetry_verdict = ("The recorded telemetry cannot distinguish " + ", ".join(labels[h] for h in upstream_open) +
                                 ": under each of them the recorded signals look the same (SOC following measured charge, then an abrupt correction). "
                                 "Separating them needs documents, an independent measurement or a controlled test, not another recording of these signals.")
    real = [r for r in rows if not r["pseudo"]]
    prior = prior or {}
    for r in real:
        if r["id"] in prior:
            r["prior"] = prior[r["id"]]
    lead = (assessment.get("leading") or assessment.get("conditional_leading") or {}).get("hypothesis")
    for r in real:
        r["tests_leading"] = bool(lead and r.get("hv_state") == "none" and (lead in r["confirms"] or lead in r["weakens"]))
    real.sort(key=lambda r: (0 if r.get("prior") else 1, 0 if r["tests_leading"] else 1, r["rank_key"]))
    recommended = [r for r in real if r["allowed_for_requester"] and (r["distinguishes"] or r["enables"] or r.get("also_safety"))]
    handoff = [dict(r, handoff=f"Requires {r['safety']['qualification'].replace('_', ' ')} personnel under the customer's procedures; "
                               "not to be carried out by the requester.") for r in real if not r["allowed_for_requester"] and (r["distinguishes"] or r["enables"])]
    for n, r in enumerate(recommended, 1):
        r["rank"] = n
    return {"version": PLANNER_VERSION, "requester_qualification": requester_qualification, "open_hypotheses": open_ids,
            "safety_holds": holds, "telemetry_verdict": telemetry_verdict, "recommended": recommended, "handoff": handoff,
            "leading": assessment.get("leading"), "conditional_leading": assessment.get("conditional_leading"),
            "ranking_rule": ("a check that settled the same finding in an applicable, engineer-verified earlier case first > " if prior else "")
                            + ("a no-contact check that tests the leading explanation > " if lead else "")
                            + "pairs separated (more first) > effort (low first) > no HV exposure first > id; safety qualification is a hard gate",
            "not_recommended": [r["id"] for r in real if not r["distinguishes"] and not r["enables"] and not r.get("also_safety")]}
