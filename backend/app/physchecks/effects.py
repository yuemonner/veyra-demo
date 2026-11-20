"""Measurement-fault algebra: what a hypothesised fault does to a computed quantity, decided by physics, not by the model.

A hypothesis may state its EFFECTS on signal roles, e.g. {"pack_current": "scale_low"} (the sensor reads k x the true
value, 0 < k < 1) or {"pack_current": "unseen_load"} (a load draws upstream of the sensor). For each computation and
quantity this module knows how such an effect moves the measured value relative to the same machine without the fault:

  higher / lower   the quantity is pushed in that direction (e.g. dV/dI per measured amp rises when the current reads low)
  same             the effect cannot change this quantity (a test on it cannot separate the fault from no fault)

and whether the no-fault value itself is known: an absolute baseline that depends on an unstated machine parameter (a
pack's internal resistance) cannot be predicted from the recording, so a separation that rests on it is CONDITIONAL on
the assumed magnitude: it weakens, it does not place a cause below another.

Used by the inquiry ledger when a prediction is registered (a prediction that contradicts the algebra is returned to the
model with the reason) and when a test is updated (conditional contradictions are recorded as such). Nothing here knows
a vehicle or a case: only roles, operations and the direction of a measurement error.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

EFFECT_KINDS = {
    "scale_low": "the signal reads a fraction k (0 < k < 1) of the true value",
    "scale_high": "the signal reads k > 1 times the true value",
    "unseen_load": "a load draws upstream of this current sensor, so the sensor never sees it",
    "estimate_high": "an estimate (e.g. a state of charge) reads above the truth",
    "capacity_low": "the true capacity is below the stated or reported parameter",
    # comparable components (app.physchecks.dynamics / anticipate): what a mechanism class does to a group of them
    "common_mode": "the whole group of comparable members moves together beyond its earlier trend (a group-wide cause)",
    "component_drift": "one member moves apart from the group independently of load (capacity, imbalance, friction, wear)",
    "component_load": "one member's extra deviation follows the load and grows (resistance, torque constant)",
    "component_sensing": "one member's reading is a measurement fault: it jumps unexplained by load or the group",
}

# (op, quantity, argument naming the role, effect) -> (direction, baseline needs a parameter in this list, or None)
RULES: Dict[Tuple[str, str, str, str], Tuple[str, Optional[List[str]]]] = {
    ("step_response", "median_ratio", "x_role", "scale_low"): ("higher", ["internal_resistance_ohm", "cell_resistance_ohm"]),
    ("step_response", "median_ratio", "x_role", "scale_high"): ("lower", ["internal_resistance_ohm", "cell_resistance_ohm"]),
    ("step_response", "median_ratio", "x_role", "unseen_load"): ("same", None),
    ("step_response", "median_ratio", "x_role", "estimate_high"): ("same", None),
    ("integrate", "net", "role", "scale_low"): ("lower", None),
    ("integrate", "net", "role", "scale_high"): ("higher", None),
    ("integrate", "net", "role", "unseen_load"): ("lower", None),
    ("unexplained_changes", "unexplained", "x_role", "unseen_load"): ("higher", None),
    ("unexplained_changes", "unexplained", "x_role", "scale_low"): ("same", None),
    ("unexplained_changes", "unexplained", "x_role", "scale_high"): ("same", None),
    ("divergence", "diff_departure_z", "roles", "component_drift"): ("higher", None),
    ("divergence", "far_departure_abs_z", "roles", "common_mode"): ("higher", None),
    ("divergence", "own_departure_z", "roles", "component_drift"): ("higher", None),
    ("divergence", "far_material_departure_z", "roles", "common_mode"): ("higher", None),
    ("divergence", "load_coupling_change_z", "roles", "component_load"): ("higher", None),
    ("divergence", "jump_excess_z", "roles", "component_sensing"): ("higher", None),
}
SAME_TOL = 0.0


def _iv(expect: Dict[str, Any]) -> Tuple[float, float]:
    from app.agent.inquiry import _interval
    return _interval(expect)


def effect_on(effects: Dict[str, str], op: str, quantity: str, args: Dict[str, Any]) -> Optional[Tuple[str, str, Optional[List[str]]]]:
    """(effect, direction, baseline_params) of a hypothesis's effects on this computation's quantity, or None if unknown."""
    q = quantity.split(".")[-1]
    for (rop, rq, arg, eff), (direction, params) in RULES.items():
        v = args.get(arg)
        names = v if isinstance(v, list) else [v or ""]
        if rop == op and rq == q and any(effects.get(str(n)) == eff for n in names):
            return eff, direction, params
    return None


def roles_in(args: Any) -> set:
    """Every role an op's arguments name: *role keys, role lists, signal expressions, predictors, events."""
    out: set = set()
    if isinstance(args, dict):
        for k, v in args.items():
            if isinstance(v, str) and (k.endswith("role") or k in ("signal", "role")):
                out.add(v)
            elif isinstance(v, (list, dict)):
                out |= roles_in(v)
    elif isinstance(args, list):
        for v in args:
            if isinstance(v, str):
                out.add(v)
            else:
                out |= roles_in(v)
    return out


def audit(op: str, args: Dict[str, Any], quantity: str, predictions: List[Dict[str, Any]], effects_of: Dict[str, Dict[str, str]],
          stated_params: List[str]) -> Dict[str, Any]:
    """Check registered predictions against the algebra. A hypothesis WITH an effect on this quantity is compared with each
    hypothesis that has NO effect on any role this computation reads (the no-fault reference for it).
      errors       the prediction moves the quantity the wrong way, or separates a fault the algebra says cannot move it
      conditional  pairs whose separation rests on an absolute baseline the recording cannot give (an unstated parameter)
    """
    roles_read = roles_in(args or {})
    ex = {p["hypothesis"]: _iv(p["expect"]) for p in predictions}
    eff = {h: effect_on(effects_of.get(h) or {}, op, quantity, args) for h in ex}
    refs = [h for h in ex if not (set(effects_of.get(h) or {}) & roles_read)]
    errors, conditional, notes = [], [], []
    for h, e in eff.items():
        if e is None:
            continue
        kind, direction, params = e
        for r in refs:
            if r == h:
                continue
            (hlo, hhi), (rlo, rhi) = ex[h], ex[r]
            if direction == "higher" and hhi <= rlo + SAME_TOL and not math.isinf(rlo):
                errors.append(f"{h} ({EFFECT_KINDS[kind]} on {args.get('x_role') or args.get('role')}) makes {quantity} HIGHER than under {r}, "
                              f"but you predict it below {r}'s region")
            elif direction == "lower" and hlo >= rhi - SAME_TOL and not math.isinf(rhi):
                errors.append(f"{h} ({EFFECT_KINDS[kind]}) makes {quantity} LOWER than under {r}, but you predict it above {r}'s region")
            elif direction == "same" and (hhi < rlo or hlo > rhi):
                errors.append(f"{h} ({EFFECT_KINDS[kind]}) cannot change {quantity}: predicting a different value from {r} would manufacture a "
                              "separation; this computation cannot test it")
            if direction in ("higher", "lower") and params and not (set(params) & set(stated_params)):
                conditional.append(sorted([h, r]))
                notes.append(f"{h} vs {r}: {quantity}'s no-fault value depends on an unstated parameter ({' or '.join(params)}); "
                             "a separation rests on the magnitude you assumed for it")
    # two causes that each declare effects, none of them on any signal this computation reads, cannot be told apart by it
    declared = [h for h in ex if effects_of.get(h)]
    for i, h in enumerate(declared):
        for r in declared[i + 1:]:
            if (set(effects_of[h]) | set(effects_of[r])) & roles_read:
                continue
            (hlo, hhi), (rlo, rhi) = ex[h], ex[r]
            if (hhi <= rlo or hlo >= rhi) and ex[h] != ex[r]:
                errors.append(f"neither {h} ({', '.join(sorted(effects_of[h]))}) nor {r} ({', '.join(sorted(effects_of[r]))}) acts on any signal this "
                              f"computation reads: predicting different values for them would manufacture a separation")
    return {"errors": errors, "conditional_pairs": conditional, "notes": notes,
            "rule": "measurement-fault algebra (app.physchecks.effects): direction of each fault's effect on the computed quantity"}
