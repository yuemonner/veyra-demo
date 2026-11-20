"""Anticipate: watch a recording for a deteriorating physical relationship BEFORE any fault, and say what to check.

Investigate starts from an event and reconstructs its causes. Anticipate runs on every recording, needs no event and no
model, and asks: is any group of comparable components (cells of a string, actuators under one command, ...) drifting
apart, and if so, which kind of mechanism do the data lean to, and which single check would tell them apart?

It reuses the dynamics engine (`divergence`) and the domain libraries of `anticipate.py`; it only ever reads data up to
`as_of`. A monitor at time T cannot see the future, so nothing after `as_of` enters any number here. Every statement is
a candidate, not a diagnosis:
  * a material departure is a candidate precursor, not a cause, and has no validated false-alarm rate on real machines;
  * the mechanism leanings are signatures the engine measured, each with its own number and rows;
  * the recommended check is the external record the domain library says separates the mechanisms still open.

Status per group:
  material_departure   the spread left its baseline trend and load relation (|z| >= Z, robust to the baseline choice,
                       beyond the timing-skew bound) AND at least half of the limiting member's excursion is its own
  significant_not_material  statistically significant but mostly the group's shared motion (e.g. a healthy group's curved
                       trend amplifying small differences): reported, no recommendation
  no_departure         the spread did not grow beyond the measurement and baseline uncertainty
  converging           the members came closer together than their trend predicts (e.g. balancing): not a deterioration
  not_robust           significant growth under one baseline choice but not under others: no conclusion
  within_timing_skew   the growth is within what the members' different sample ages could manufacture
  not_assessable       too little data, no comparable members, or the computation refused (reason given)
Only GROWTH of the spread (members moving apart) is a deterioration; the sign matters.

watch-v2 (dynamics-v3) adds two signatures that the spread's share cannot see, each a flag of its own, and judges every
materiality on an interval (evidence with uncertainty, not an exclusion law):
  coupling_growth          one member's response per unit of the driver grows relative to its peers, by at least half of
                           the group's own response (interval at or above 1/2): a resistance- or torque-constant-like loss
                           that a varying load hides from the spread's share
  intermittent_transients  one member's reading jumps and returns, without a driver step, at least twice more often than
                           any peer's: an intermittent measurement fault, present from the start or not
Flags: material_departure, coupling_growth, intermittent_transients. "possible" (not raised): a significant signature
whose materiality interval straddles 1/2. A flag is a candidate precursor, never a diagnosis.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.physchecks import anticipate as ANT, compute as C, dynamics as D

WATCH_VERSION = "watch-v2"


def _signatures(q: Dict[str, Any]) -> List[Dict[str, Any]]:
    """What the measured quantities say about the mechanism classes (each a measured number with its reading)."""
    z = D.Z
    out = []
    lc = q.get("load_coupling_change_z")
    if lc is not None:
        out.append({"quantity": "load_coupling_change_z", "value": lc, "leans_to": None,      # v2 statistic, kept for comparison; v3's coupling_growth leans
                    "reading": "the member's extra response per unit of the driver (load or command) grew (resistance-like)" if lc >= z else
                    "the response per unit of the driver did not grow measurably (not resistance-like so far)"})
    else:
        out.append({"quantity": "load_coupling_change_z", "value": None, "leans_to": None,
                    "reading": "not identifiable: the driver did not step often enough in both spans"})
    jz = q.get("jump_excess_z")
    if jz is not None:
        out.append({"quantity": "jump_excess_z", "value": jz, "leans_to": None,      # v2 statistic, kept for comparison; v3's transient_excess leans
                    "reading": "the spread jumps without a load step more often than in the baseline (measurement-like)" if jz >= z else
                    "no excess of jumps without a load step"})
    fm = q.get("far_material_departure_z")
    if fm is not None:
        # context only: a material departure is by definition mostly the limiting member's own, so a group-wide motion can
        # at most act together with it (it is never the lean here; Investigate tests common vs component at an event)
        out.append({"quantity": "far_material_departure_z", "value": fm, "leans_to": None,
                    "reading": "the member farthest from the limit also left its trend: a group-wide cause may act together with this one"
                    if fm >= z else "the far member kept its trend"})
    od = q.get("own_departure_z")
    od = q.get("own_material_departure_z")
    cgm = q.get("coupling_growth_material_z")
    tx = q.get("transient_excess")
    if od is not None and od >= z and (cgm is None or cgm < z) and (tx is None or tx < 2):
        out.append({"quantity": "own_departure_z", "value": od, "leans_to": "drift",
                    "reading": "one member moves apart smoothly with no growth in load coupling and no glitches (capacity-, imbalance- "
                               "or friction-like); this lean rests on the other signatures being absent"})
    return out


def watch_group(ctx: C.Context, g: Dict[str, Any], as_of: Optional[float] = None) -> Dict[str, Any]:
    a = ANT._args(g, None)
    if as_of is not None:
        a["t1"] = float(as_of)
    head = {"domain": g["domain"], "group": g["group"], "title": g["title"], "window": g["window"], "members": g["members"],
            "driver": g["driver"], "as_of": as_of}
    try:
        r = C.run(ctx, "divergence", a)
    except (C.ComputeError, D.DynError) as ex:
        return head | {"status": "not_assessable", "reason": str(ex)}
    q = r["quantities"]
    dz = q.get("diff_departure_z")
    material = (q.get("differential_share") or 0.0) >= 0.5
    reason = None
    if dz is None:
        status, reason = "not_assessable", "no departure statistic could be computed"
    elif dz <= -D.Z:
        status, reason = "converging", "the members came closer together than their baseline trend predicts (not a deterioration)"
    elif dz < D.Z:
        status = "no_departure"
    elif q.get("baseline_robust") == 0:
        status, reason = "not_robust", "the departure depends on which span is taken as the baseline"
    elif q.get("skew_qualified"):
        status, reason = "within_timing_skew", "the spread change is within what the members' different sample ages can manufacture"
    else:
        status = "material_departure" if material else "significant_not_material"
        if not material:
            reason = "statistically significant, but most of the limiting member's excursion is shared with the group"
    # dynamics-v3: materiality judged on an interval; two more routes
    om = (q.get("own_materiality") or {}).get("verdict")
    if status in ("material_departure", "significant_not_material") and om:
        status = {"material": "material_departure", "immaterial": "significant_not_material", "undetermined": "possible_departure"}[om]
        reason = None if om == "material" else reason if om == "immaterial" else (
            f"significant, but the member's own share of its excursion lies on both sides of one half {q['own_materiality']['interval']}")
    flags, possible = [], []
    if status == "material_departure":
        flags.append("material_departure")
    elif status == "possible_departure":
        possible.append("material_departure")
    cg = q.get("coupling_growth") or {}
    cgv = (cg.get("materiality") or {}).get("verdict")
    if cg.get("z") is not None and cg["z"] >= D.Z:
        if cgv == "material":
            flags.append("coupling_growth")
        elif cgv == "undetermined":
            possible.append("coupling_growth")
    tx = q.get("transient_excess")
    if tx is not None and tx >= 2:
        flags.append("intermittent_transients")
    elif tx == 1:
        possible.append("intermittent_transients")
    sig = _signatures(q) if flags else []
    if "coupling_growth" in flags:
        sig.append({"quantity": "coupling_growth", "value": cg.get("relative_growth"), "leans_to": "load",
                    "reading": f"the limiting member's response per unit of the driver grew by {cg.get('relative_growth')} of the group's own response "
                               f"(interval {cg['materiality']['interval']}, z {cg.get('z')}) relative to its peers"})
    if "intermittent_transients" in flags:
        sig.append({"quantity": "transient_excess", "value": tx, "leans_to": "sensing",
                    "reading": f"{q.get('transient_member')} jumps and returns without a driver step {tx} more time(s) than any peer"})
    leans = sorted({s["leans_to"] for s in sig if s["leans_to"]})
    lib = g["library"]["classes"]
    rec = None
    if flags:
        open_mech = [m for m in ANT.MECHANISMS if not leans or m in leans] or list(ANT.MECHANISMS)
        pairs = [(a_, b_) for i, a_ in enumerate(open_mech) for b_ in open_mech[i + 1:]]
        if not leans:
            pairs.append(("common", "component"))
        if len(leans) == 1 and leans[0] in ANT.MECHANISMS:
            # one lean: the check that would confirm it against its nearest rivals
            pairs = [(leans[0], m) for m in ANT.MECHANISMS if m != leans[0]]
        rec = ANT.next_check(g, pairs)
    lim = q.get("limiting_member")
    # is the limiting extreme the same physical component throughout? (its own index role, when the group has one per member)
    ident = {i["role"]: i for i in q.get("identity") or []}
    lim_ident = None
    if lim in g["members"] and len(g["index_roles"]) == len(g["members"]):
        lim_ident = ident.get(g["index_roles"][g["members"].index(lim)])
    return head | {
        "status": status, "reason": reason, "flags": flags, "possible": possible,
        "limiting_member": lim,
        "identity_stable": None if lim_ident is None else int(lim_ident["changes"] == 0),
        "limiting_component": None if lim_ident is None else {"index_role": lim_ident["role"], "index": lim_ident.get("most_frequent"),
                                                             "changes": lim_ident["changes"], "fraction": lim_ident.get("fraction_most_frequent")},
        "first_observable_t": q.get("first_significant_t") if "material_departure" in flags else None,
        "numbers": {k: q.get(k) for k in ("diff_departure_z", "differential_share", "own_departure_z", "far_material_departure_z",
                                          "load_coupling_change_z", "jump_excess_z", "baseline_robust", "skew_qualified", "spread_rate_last",
                                          "limiting_change", "far_change", "own_material_departure_z", "coupling_growth_z",
                                          "coupling_growth_relative", "coupling_growth_material_z", "transient_excess", "transient_member") if k in q},
        "materiality": {"own": q.get("own_materiality"), "coupling": (q.get("coupling_growth") or {}).get("materiality")},
        "signatures": sig,
        "leans_to": [{"class": c, "label": lib.get(c, {}).get("label", c)} for c in leans],
        "recommended_check": ({"id": rec["id"], "record": rec["record"], "separates": rec.get("addresses")} if rec else None),
        "evidence": r["evidence"][:8], "assumptions": r["assumptions"],
        "caveats": ["a material departure is a candidate precursor, not a cause; it has no validated false-alarm rate on real machines",
                    "nothing after as_of was read" if as_of is not None else "the whole recording was read (retrospective)"],
    }


def watch(ctx: C.Context, as_of: Optional[float] = None) -> Dict[str, Any]:
    """Every comparable group the profile binds, watched up to as_of (default: the end of each window)."""
    gs = ANT.groups(ctx)
    groups = [watch_group(ctx, g, as_of) for g in gs]
    flagged = [x for x in groups if x.get("flags")]
    return {"version": WATCH_VERSION, "dynamics_version": D.DYN_VERSION, "as_of": as_of, "groups": groups,
            "summary": ("no comparable groups in this recording" if not groups else
                        f"{len(flagged)} of {len(groups)} group(s) flagged ({', '.join(sorted({f for x in flagged for f in x['flags']}))})" if flagged else
                        f"none of {len(groups)} group(s) flagged"),
            "note": "Anticipate reads only data up to as_of; departures are candidate precursors under the stated baseline, not causes."}
