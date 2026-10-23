"""Builds a planner Scenario from what Veyra's engine produced for a case (its run and assessment). Uses no outcome,
label or report beyond what the case record holds at decision time."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from app.engine import next_action as na
from research.voi.planner import Check, Scenario

ASSUMPTIONS = json.loads((Path(__file__).parent / "assumptions.json").read_text())
SAFETY_WORDS = ("kill engaged", "flight termination", "crash", "emergency stop", "e-stop", "estop")
SAFETY_INTERVENTIONS = {"emergency_stop", "safety_stop", "safety"}


def category(h: str) -> str:
    return "none" if h == "no_fault" else "remote" if h in ASSUMPTIONS["remote_parts"] else "hands_on"


def safety_relevant(run: Dict[str, Any]) -> bool:
    texts = [o["text"].lower() for d in run["domains"].values() for o in d.get("observations", [])]
    texts += [str(e.get("event_value") or "").lower() for e in ((run.get("findings") or {}).get("events") or {}).get("events", [])]
    return any(w in t for t in texts for w in SAFETY_WORDS) or any(i.get("intervention_type") in SAFETY_INTERVENTIONS for i in run.get("interventions") or [])


def scenario(run: Dict[str, Any], label: str) -> Scenario:
    A = ASSUMPTIONS
    a = na.assess(run)
    pw = A["prior_weight"]
    hyps: Dict[str, Any] = {}
    supported = {s["part"] for s in a["supported"]}
    for c in a["cannot_tell_apart"]:
        hyps[c["part"]] = tuple(pw["supported" if c["part"] in supported else "open"])
    for s in supported:
        hyps.setdefault(s, tuple(pw["supported"]))
    for x in a["less_likely"] + a.get("effects", []):
        hyps.setdefault(x["part"], tuple(pw["less_likely"]))
    for x in a.get("ruled_out", []):
        hyps.pop(x.get("part"), None)
    reported = bool(run.get("interventions") or run.get("reported_symptoms"))
    hyps["no_fault"] = tuple(pw["no_fault_when_problem_reported" if reported else "no_fault_when_nothing_reported"])
    actions = {name: {h: tuple(row[category(h)]) for h in hyps} for name, row in A["actions"].items()}
    excluded = {}
    if safety_relevant(run):
        excluded["act: monitor, no action"] = "Safety: the evidence records a kill switch, termination, crash or emergency stop; the machine must not just keep operating."
        actions.pop("monitor, no action")
    else:
        excluded["act: safety escalation now"] = "No safety signal in the evidence: disproportionate."
        actions.pop("safety escalation now")
    L = A["likelihood"]
    checks = []
    for c in run.get("checks") or []:
        cid, state = c["check_id"], c["availability"]["state"]
        if cid not in na.SPLITS or state not in A["check_delay"] or c.get("previously_performed"):
            continue
        outcomes = {}
        for key, o in na.SPLITS[cid].items():
            row = {}
            for h in hyps:
                if h == "no_fault":
                    row[h] = tuple(L["no_fault_and_outcome_supports_something" if o["supports"] else "no_fault_and_outcome_finds_nothing"])
                elif h in o["supports"]:
                    row[h] = tuple(L["outcome_supports_h"])
                elif h in o["weakens"]:
                    row[h] = tuple(L["outcome_weakens_h"])
                else:
                    row[h] = tuple(L["outcome_neutral_to_h"])
            outcomes[key] = row
        checks.append(Check(id=cid, label=c["question"], cost=tuple(A["check_cost"]), delay=tuple(A["check_delay"][state]), outcomes=outcomes,
                            needs_authorization=state == "needs_authorization"))
    notes = [f"Hypotheses from Veyra's assessment: {', '.join(sorted(hyps))}", f"Assumptions {A['version']}"]
    return Scenario(case=label, hypotheses=hyps, actions=actions, checks=checks, excluded=excluded, notes=notes)
