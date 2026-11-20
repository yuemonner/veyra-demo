"""Investigate and Anticipate with signal dynamics: the reusable link between the dynamics engine and causal inquiry.

Given a recording and its profile, this module
  1. finds GROUPS of comparable signals the profile carries, using domain libraries (app/physchecks/domains/*.json):
     which roles are comparable components (or the extremes of a group), which role is the load or command that drives
     them, which state changes are protective events, which side the limit is on;
  2. SCREENS each group (Anticipate): rates of the limiting and far members, the spread's departure from its baseline
     trend and load relation, when it became observable and how far before each event, the limiting member's excursion
     split into what the whole group shared and what was its own, identity of the extreme component, timing skew,
     baseline sensitivity;
  3. states the competing MECHANISM CLASSES for the group (Investigate), the same five in every domain, worded by the
     domain library:
        common     the whole group moved (a group-wide cause; can act together with a component-level one)
        component  one member moved apart and reached the limit first, through one of three mechanisms:
                   drift (load-independent: capacity, imbalance, friction, wear), load (follows the load: resistance,
                   torque constant), sensing (a measurement fault on that member)
        external   the event was initiated outside the group: the group did not depart before it
     and derives, from the physics of each class, which computed quantity each predicts differently;
  4. GENERATES the discriminating tests (registered before anything is computed, so none is post-hoc) for the inquiry
     ledger, and picks the next check: the external record that separates the most pairs still unresolved.

Nothing here knows a machine or a case; the domain libraries hold meaning, never thresholds or answers.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.physchecks import compute as C, dynamics as D

DOMAIN_DIR = Path(__file__).resolve().parent / "domains"
ANTICIPATE_VERSION = "anticipate-v3"
CLASSES = ("common", "component", "external")
MECHANISMS = ("drift", "load", "sensing")
EFFECT = {"common": "common_mode", "drift": "component_drift", "load": "component_load", "sensing": "component_sensing"}


def domains() -> List[Dict[str, Any]]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(DOMAIN_DIR.glob("*.json"))]


def _present(ctx: C.Context, role: str, window: str) -> bool:
    p = ctx.profile
    return p.has_role(role) and ctx.rec.has(window, p.role(role)["field"]) and len(p.series(ctx.rec, window, role).samples) > 0


def groups(ctx: C.Context) -> List[Dict[str, Any]]:
    """Every domain group whose members (>= 2) the profile binds, per window that carries them."""
    out = []
    roles = sorted(ctx.profile.roles)
    for dom in domains():
        for g in dom.get("groups") or []:
            spec = g.get("members") or {}
            names = list(spec.get("roles") or [])
            if spec.get("pattern"):
                rx = re.compile(spec["pattern"])
                names += [r for r in roles if rx.match(r) and r not in names]
            for w in ctx.rec.windows:
                mem = [r for r in names if _present(ctx, r, w)]
                if len(mem) < 2:
                    continue
                drv = next((r for r in g.get("driver_roles") or [] if _present(ctx, r, w)), None)
                idx = [r for r in g.get("index_roles") or [] if _present(ctx, r, w)]
                evs = [e for e in g.get("events") or [] if _present(ctx, e["role"], w)]
                out.append({"domain": dom["id"], "group": g["id"], "title": g.get("title", g["id"]), "window": w, "members": mem, "driver": drv,
                            "index_roles": idx, "limit_side": g.get("limit_side"), "events": evs, "library": dom, "response": g.get("response"),
                            "reason_roles": [r for r in g.get("reason_roles") or [] if ctx.profile.has_role(r)], "member_limit_reason": g.get("member_limit_reason")})
    return out


def _args(g: Dict[str, Any], event: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    a: Dict[str, Any] = {"roles": g["members"], "window": g["window"]}
    if g["driver"]:
        a["driver_role"] = g["driver"]
    if g["index_roles"]:
        a["index_roles"] = g["index_roles"]
    if g["limit_side"]:
        a["limit_side"] = g["limit_side"]
    if g.get("response"):
        a["response"] = g["response"]
    if event:
        a["event"] = {k: v for k, v in event.items() if k in ("role", "kind", "value", "op")}
    return a


def _first_event(ctx: C.Context, g: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for e in g["events"]:
        if D._event_time(ctx, g["window"], e, None):
            return e
    return None


def screen_group(ctx: C.Context, g: Dict[str, Any]) -> Dict[str, Any]:
    """The Anticipate screen of one group in one window: against every event found, and with no event (whole window)."""
    found = [(e, D._event_time(ctx, g["window"], e, None)) for e in g["events"]]
    found = [(e, t) for e, t in found if t]
    runs = []
    for e, t in found or [(None, None)]:
        try:
            r = C.run(ctx, "divergence", _args(g, e))
        except C.ComputeError as ex:
            runs.append({"event": e and e["name"], "error": str(ex)})
            continue
        q = r["quantities"]
        lim, far = q["limiting_member"], q["far_member"]
        rates = {}
        for m in [lim] + ([far] if far in g["members"] else []):
            try:
                rq = C.run(ctx, "rate", {"signal": m, "window": g["window"], "t1": (t or {}).get("t")})["quantities"]
                rates[m] = {k: rq[k] for k in ("rate_min", "rate_min_t", "rate_min_se", "rate_max", "rate_max_t", "rate_first_tenth_median",
                                               "rate_last_tenth_median", "window_s_median", "unit")}
            except C.ComputeError:
                pass
        departs = q.get("diff_departure_z") is not None and abs(q["diff_departure_z"]) >= D.Z
        material = (q.get("differential_share") or 0.0) >= 0.5        # anticipate-v2: rule W2 of dynamics-eval-v1
        warning = bool(departs and material and q.get("baseline_robust") != 0 and not q.get("skew_qualified"))
        runs.append({"event": e and e["name"], "event_t": (t or {}).get("t"), "divergence": q, "notes": r["notes"], "rates": rates,
                     "evidence": r["evidence"][:8], "assumptions": r["assumptions"],
                     "warning": {"raised": warning, "rule": f"the spread departs from its baseline trend and load relation (|z| >= {D.Z}), with the "
                                 "same verdict under other baseline spans and beyond the timing-skew bound, AND the departure is material: at least "
                                 "half of the limiting member's excursion since the baseline is its own (differential_share >= 1/2)",
                                 "first_observable_t": q.get("first_significant_t") if warning else None,
                                 "lead_s": q.get("lead_s") if warning and e else None}})
    return {"domain": g["domain"], "group": g["group"], "title": g["title"], "window": g["window"], "members": g["members"], "driver": g["driver"],
            "index_roles": g["index_roles"], "events_found": [{"name": e["name"], "t": t["t"]} for e, t in found], "runs": runs}


def screen(ctx: C.Context) -> Dict[str, Any]:
    gs = groups(ctx)
    return {"version": ANTICIPATE_VERSION, "dynamics_version": D.DYN_VERSION, "groups": [screen_group(ctx, g) for g in gs],
            "note": "Departures and precedence are candidate precursors under the stated baseline; they are not evidence of cause. A warning rule "
                    "with a validated false-alarm rate needs many machines' records."}


# ---- Investigate: mechanism classes, generated tests, next check ------------------------------------------------------
def focus(ctx: C.Context) -> Optional[Dict[str, Any]]:
    """The group and window an investigation tests: the first group/window in which a protective event is found, else none."""
    for g in groups(ctx):
        e = _first_event(ctx, g)
        if e:
            return {**g, "event": e}
    return None


def hypotheses(g: Dict[str, Any], prefix: str) -> List[Dict[str, Any]]:
    lib = g["library"]["classes"]
    rel = g["library"].get("relates_to_assessment") or {}
    fam = f"dynamics:{g['group']}"
    out = []

    def eff(kind: Optional[str]) -> Dict[str, str]:
        return {m: kind for m in g["members"]} if kind else {}
    out.append({"id": f"{prefix}1", "cls": "common", "label": lib["common"]["label"], "statement": lib["common"]["statement"], "family": fam,
                "effects": eff(EFFECT["common"]), "coexists_with": [f"{prefix}2"], "relates_to": rel.get("common")})
    out.append({"id": f"{prefix}2", "cls": "component", "label": lib["component"]["label"], "statement": lib["component"]["statement"], "family": fam,
                "mechanisms": [{"id": f"{prefix}2{c}", "cls": m, "label": lib[m]["label"], "statement": lib[m]["statement"], "effects": eff(EFFECT[m]),
                                "relates_to": rel.get(m)} for c, m in zip("abc", MECHANISMS)],
                "coexists_with": [f"{prefix}1"], "relates_to": rel.get("component")})
    out.append({"id": f"{prefix}3", "cls": "external", "label": lib["external"]["label"], "statement": lib["external"]["statement"], "family": fam,
                "effects": {}, "relates_to": rel.get("external")})
    return out


def generated_tests(g: Dict[str, Any], prefix: str) -> List[Dict[str, Any]]:
    """The discriminating tests that follow from the classes' physics, on one divergence computation (four quantities)."""
    test = {"op": "divergence", "args": _args(g, g.get("event"))}
    z = D.Z
    P = prefix
    return [
        {"question": "Did one member move apart from the group before the event, beyond its own earlier trend and load relation, by a material share?",
         "test": test, "quantity": "own_material_departure_z",
         "predictions": [{"hypothesis": f"{P}2a", "expect": {"gt": z}, "why": "a load-independent loss in one member pulls it away from the rest at any load, "
                          "and most of that member's excursion is its own (a group-wide bend only amplifies existing small differences)"},
                         {"hypothesis": f"{P}3", "expect": {"between": [-z, z]}, "why": "initiated outside the group: no member departs before the event"},
                         {"hypothesis": f"{P}1", "expect": {"between": [-z, z]}, "why": "a group-wide cause alone moves the members together: the limiting "
                          "member's excursion is mostly shared, so its own departure is not material (acting together with a one-member cause, it can be)"}],
         "pair": [["drift", "external"], ["drift", "common"]],
         "abstains": "when the member's own share lies on both sides of one half under the baseline choices and the noise (no result)"},
        {"question": "Did the far member (the whole group) change its relation to the driver before the event, by a material share of its change?",
         "test": test, "quantity": "far_material_change_z",
         "predictions": [{"hypothesis": f"{P}1", "expect": {"gt": z}, "why": "a group-wide cause moves even the member farthest from the limit away from its "
                          "earlier relation to the driver (for a static response, even when the rise had begun before the baseline ended)"},
                         {"hypothesis": f"{P}3", "expect": {"lt": z}, "why": "initiated outside the group: the group keeps its earlier relation"}],
         # no prediction for the one-member mechanisms: the group may move as a whole meanwhile (a pack still discharges)
         "pair": [["common", "external"]],
         "abstains": "when the far member's material share is undetermined (no result)"},
        {"question": "Did the limiting member's response per unit of the driver grow over the span, relative to its peers, by a material part of the group's own response?",
         "test": test, "quantity": "coupling_growth_material_z",
         "predictions": [{"hypothesis": f"{P}2b", "expect": {"gt": z}, "why": "a growing resistance-like loss (or lost torque constant) makes the member's extra "
                          "response per unit of load grow"},
                         {"hypothesis": f"{P}2a", "expect": {"between": [-z, z]}, "why": "a load-independent loss leaves the response per unit of load unchanged"},
                         {"hypothesis": f"{P}1", "expect": {"between": [-z, z]}, "why": "a group-wide cause changes every member's response alike: no member's grows "
                          "relative to its peers"},
                         {"hypothesis": f"{P}3", "expect": {"between": [-z, z]}, "why": "initiated outside the group: no member's response changes"}],
         "pair": [["drift", "load"], ["load", "common"], ["load", "external"]],
         "abstains": "when fewer than 8 driver steps are usable or the growth's materiality is undetermined (no result)"},
        {"question": "Does one member's reading show a measurement-fault signature: jumps that return without a driver step (more than its "
                     "peers), or the limit reached by one unreturned jump without a driver step?",
         "test": test, "quantity": "sensing_signatures",
         "predictions": [{"hypothesis": f"{P}2c", "expect": {"gt": 0.5}, "why": "an intermittent sensing fault moves one reading and back while its peers, "
                          "under the same driver, do not; or puts it past the limit in one update and the protective action ends the record before "
                          "it returns. A short record may show only one of the two"},
                         {"hypothesis": f"{P}2a", "expect": {"lt": 0.5}, "why": "a physical loss changes the member's value smoothly"},
                         {"hypothesis": f"{P}2b", "expect": {"lt": 0.5}, "why": "a physical resistance follows the load; jumps at driver steps are excluded"},
                         {"hypothesis": f"{P}1", "expect": {"lt": 0.5}, "why": "a group-wide cause moves every member smoothly and alike"},
                         {"hypothesis": f"{P}3", "expect": {"lt": 0.5}, "why": "initiated outside the group: no member's reading misbehaves"}],
         "pair": [["sensing", "drift"], ["sensing", "load"], ["sensing", "common"], ["sensing", "external"]]},
    ] + ([{"question": "Did the machine itself record that a member of this group reached its own limit at the event?",
           "test": {"op": "recorded_reason", "args": {"roles": g["reason_roles"], "window": g["window"], "event": _args(g, g.get("event")).get("event"),
                                                      "pattern": g["member_limit_reason"]}},
           "quantity": "member_limit_recorded",
           "predictions": [{"hypothesis": f"{P}3", "expect": {"lt": 0.5}, "why": "a stop initiated outside the group is not recorded as the group's own limit protection"},
                           {"hypothesis": f"{P}1", "expect": {"gt": 0.5}, "why": "a group-wide excursion reaches the members' limit, which the protection records"},
                           {"hypothesis": f"{P}2a", "expect": {"gt": 0.5}, "why": "one member reached its limit: the protection records it"},
                           {"hypothesis": f"{P}2b", "expect": {"gt": 0.5}, "why": "one member reached its limit: the protection records it"},
                           {"hypothesis": f"{P}2c", "expect": {"gt": 0.5}, "why": "a false reading past the limit trips the same protection, which records it"}],
           "pair": [["external", "common"], ["external", "component"]]}] if g.get("reason_roles") and g.get("member_limit_reason") and g.get("event") else [])


def _fmt(x: Any, unit: str = "") -> str:
    if x is None:
        return "n/a"
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    return f"{v:.4g}{(' ' + unit) if unit else ''}"


def findings(ctx: C.Context, g: Dict[str, Any], q: Dict[str, Any], reason: Optional[Dict[str, Any]], nxt: Optional[Dict[str, Any]]) -> List[str]:
    """Veyra's own plain statements of what the group did, generated from the computed quantities only (no model, no
    case knowledge): the limiting member and its rate, the spread's growth, how early it departed, what share was the
    member's own, whether it followed the load, the sensing signatures, what the machine recorded, and the next check."""
    out: List[str] = []
    unit = q.get("unit") or ""
    lim, far = q.get("limiting_member"), q.get("far_member")
    ev = (g.get("event") or {}).get("name") or "the event"
    t_ev = (q.get("event") or {}).get("t")
    try:
        rq = C.run(ctx, "rate", {"signal": lim, "window": g["window"], "t1": t_ev})["quantities"]
        out.append(f"Limiting member {lim}: its rate of change went from {_fmt(rq.get('rate_first_tenth_median'))} to "
                   f"{_fmt(rq.get('rate_last_tenth_median'))} {unit}/s (first vs last tenth of the span); steepest {_fmt(rq.get('rate_min'))} {unit}/s "
                   f"at t={_fmt(rq.get('rate_min_t'))} s, {_fmt((t_ev or 0) - (rq.get('rate_min_t') or 0)) if t_ev and rq.get('rate_min_t') is not None else 'n/a'} s before {ev}.")
    except Exception:                                                    # noqa: BLE001 - a missing rate is reported as missing
        out.append(f"Limiting member {lim}: its rate could not be computed.")
    out.append(f"Spread ({unit}) between the members grew at {_fmt(q.get('spread_rate_baseline'))} {unit}/s in the baseline and "
               f"{_fmt(q.get('spread_rate_late'))} {unit}/s late; steepest {_fmt(q.get('spread_rate_max'))} {unit}/s at t={_fmt(q.get('spread_rate_max_t'))} s. "
               f"The group's common level moved at {_fmt(q.get('common_rate_baseline'))} then {_fmt(q.get('common_rate_late'))} {unit}/s.")
    if q.get("first_significant_t") is not None:
        out.append(f"The spread departed from its baseline trend and load relation (|z| {_fmt(q.get('diff_departure_z'))}) from t={_fmt(q.get('first_significant_t'))} s, "
                   f"{_fmt(q.get('lead_s'))} s before {ev} (ordering: {q.get('ordering')}). A departure that precedes an event is a candidate precursor, not a cause.")
    om, fm = q.get("own_materiality") or {}, q.get("far_materiality") or {}
    out.append(f"Of {lim}'s excursion since the baseline, {_fmt(q.get('differential_share'))} was its own and the rest shared with the group "
               f"(cell- or component-level vs group-level; share interval {om.get('interval')}: {om.get('verdict')}); the far member {far} changed its "
               f"relation by a share {fm.get('estimates')} ({fm.get('verdict')}). Both a group-wide and a one-member cause may act together.")
    cg = q.get("coupling_growth") or {}
    if cg.get("z") is None:
        out.append(f"Load relation: {cg.get('note') or 'not identifiable'}; whether the member's deterioration follows the load (resistance-like) "
                   "or not (capacity- or imbalance-like) cannot be separated on this data.")
    else:
        out.append(f"Load relation: over {cg.get('steps')} driver steps, the member's extra response per unit of the driver grew by "
                   f"{_fmt(cg.get('relative_growth'))} of the group's own response (z {_fmt(cg.get('z'))}; {(cg.get('materiality') or {}).get('verdict')}).")
    out.append(f"Sensing signatures: {q.get('transient_excess')} more jump-and-return transient(s) on {q.get('transient_member')} than on its peers; "
               f"limit reached by one unreturned jump: {q.get('event_on_jump')}.")
    if reason is not None:
        labs = [x["label"] for x in reason.get("labels") or []]
        out.append(f"Recorded reason around {ev}: {labs or 'none'}; the group's own limit protection recorded: {reason.get('member_limit_recorded')}.")
    if nxt:
        out.append(f"Next check: {nxt.get('record')} (separates {', '.join(' vs '.join(p) for p in nxt.get('addresses') or [])}).")
    return out


def next_check(g: Dict[str, Any], unresolved: List[Tuple[str, str]]) -> Optional[Dict[str, Any]]:
    """The external record that separates the most class pairs still unresolved (pairs named by class: common, component,
    drift, load, sensing, external). A component pair is addressed by a check on any of its mechanisms."""
    want = {frozenset(p) for p in unresolved}
    best, score = None, 0
    for ch in g["library"].get("external_checks") or []:
        sep = {frozenset(p) for p in ch.get("separates") or []}
        hit = [sorted(p) for p in want if p in sep or (("component" in p) and any(frozenset({x, *(p - {"component"})}) in sep for x in MECHANISMS))]
        if len(hit) > score:
            best, score = {**ch, "addresses": hit}, len(hit)
    return best
