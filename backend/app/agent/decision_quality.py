"""Decision quality of investigation checks: how much a check narrows the explanations without dropping a true cause.

Roles come from the sealed truth: initiating (the fault that started the chain), contributing (conditions without which
it would not have happened), consequence (happened because of the chain, not a cause), not involved.

For every check, the engineers' actual steps and Veyra's recommended checks alike:
  bits         uncertainty removed, log2(open before) - log2(open after), over a uniform belief on the open set
  expected     for checks with predicted outcomes: the expected bits over the open hypotheses that have a prediction
               (hypotheses without one stay open in every outcome)
  unsafe       the check removed an initiating or contributing cause: its bits came from dropping a true cause
  consequence  the check treated a consequence as the cause (targeted it and reported it as the fault)
Bits are never converted into hours or money.
"""
from __future__ import annotations

from math import log2
from typing import Any, Dict, List, Optional

TRUE_ROLES = ("initiating", "contributing")


def _bits(n_before: int, n_after: int) -> float:
    if n_before <= 0 or n_after <= 0:
        return 0.0
    return round(log2(n_before) - log2(n_after), 3)


def role(roles: Dict[str, str], h: str) -> str:
    return roles.get(h, "not_involved")


def engineer_steps(steps: List[Dict[str, Any]], targets_of, all_ids: List[str], roles: Dict[str, str]) -> List[Dict[str, Any]]:
    """The engineers' own open set: every hypothesis in the space minus what their reported results eliminated."""
    open_ = list(all_ids)
    out = []
    for s in steps:
        tg = targets_of(s)
        before = list(open_)
        removed: List[str] = []
        if s["result"] == "not_the_issue":
            removed = [t for t in tg if t in open_]
        elif s["result"] == "fault_found" and tg:
            # a fault found narrows the initiating cause to the target; other open hypotheses are not shown wrong by it
            removed = []
        open_ = [h for h in open_ if h not in removed]
        after_n = 1 if s["result"] == "fault_found" and tg else len(open_)
        wrong = [h for h in removed if role(roles, h) in TRUE_ROLES]
        cons = [t for t in tg if role(roles, t) == "consequence" and s["result"] in ("fault_found", "partly")]
        out.append({"side": "engineers", "step": s["id"], "check": s["check"], "targets": tg, "result": s["result"],
                    "open_before": len(before), "open_after": after_n, "bits": _bits(len(before), after_n) if tg else 0.0,
                    "removed": removed, "unsafe": bool(wrong), "removed_true_causes": [{"hypothesis": h, "role": role(roles, h)} for h in wrong],
                    "found": [{"hypothesis": t, "role": role(roles, t)} for t in tg if s["result"] in ("fault_found", "partly")],
                    "consequence_as_cause": cons, "minutes": s.get("minutes"), "cost": s.get("cost"), "information_gathering": not tg})
    return out


def veyra_check(state: Dict[str, Any], roles: Dict[str, str], initiating: str, load_only: bool = False,
                rest_checks=("segment_r", "meter_v")) -> Optional[Dict[str, Any]]:
    rec = (state.get("plan") or {}).get("recommended")
    if not rec:
        return None
    pred = rec.get("predicts") or {}
    open_ = list(state["open"])
    have = [h for h in open_ if h in pred]
    none = [h for h in open_ if h not in pred]
    classes: Dict[Any, List[str]] = {}
    for h in have:
        classes.setdefault(pred[h], []).append(h)
    # expected bits, uniform over the open hypotheses that have a prediction
    exp = round(sum((len(g) / len(have)) * _bits(len(open_), len(g) + len(none)) for g in classes.values()), 3) if have else None
    out = {"side": "veyra", "check": rec["id"], "what": rec.get("what"), "cost_tier": rec.get("cost_tier"), "open_before": len(open_),
           "expected_bits": exp, "max_bits": _bits(len(open_), 1), "unpredicted_open": none}
    if initiating in pred:
        cls = pred[initiating]
        if load_only and rec["id"] in rest_checks:
            keep = [h for h in open_ if h != initiating]           # reads normal at rest: the initiating cause looks excluded
        else:
            keep = [h for h in open_ if pred.get(h, cls) == cls]
        removed = [h for h in open_ if h not in keep]
        wrong = [h for h in removed if role(roles, h) in TRUE_ROLES]
        out.update({"simulated": True, "open_after": len(keep), "bits": _bits(len(open_), len(keep)), "removed": removed,
                    "unsafe": bool(wrong), "removed_true_causes": [{"hypothesis": h, "role": role(roles, h)} for h in wrong]})
    else:
        out.update({"simulated": False, "why": "no predicted outcome for the initiating cause"})
    return out


def state_roles(state: Dict[str, Any], roles: Dict[str, str]) -> Dict[str, Any]:
    """Where each true cause and each consequence stands in Veyra's state."""
    by = {}
    for h, r in roles.items():
        if r == "not_involved":
            continue
        by[h] = {"role": r, "status": (state["hypotheses"].get(h) or {}).get("status")}
    supported_consequences = [h for h, v in by.items() if v["role"] == "consequence" and v["status"] == "supported"]
    return {"by_hypothesis": by, "true_causes_open": [h for h, v in by.items() if v["role"] in TRUE_ROLES and v["status"] == "supported"],
            "true_causes_weakened": [h for h, v in by.items() if v["role"] in TRUE_ROLES and v["status"] == "weakened"],
            "true_causes_not_identifiable": [h for h, v in by.items() if v["role"] in TRUE_ROLES and v["status"] == "not_identifiable"],
            "consequences_held_as_causes": supported_consequences}
