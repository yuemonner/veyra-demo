"""Frozen-case comparison: next-action-v2 (the product's rules) vs the research VOI planner. RESEARCH ONLY.

Both planners see only Veyra's case record at decision time. The truth labels (sealed_truth.json) are read only after
both have answered, to score them. Scoring is SIMULATED: it uses the midpoints of the written assumptions to price
each policy's path to a final action given the true hypothesis. It says whether each policy is coherent with its
assumptions on cases whose answer is known; it is not evidence of real-world savings.

Usage (from backend/): python -m research.voi.evaluate <out.json> [px4 problem.db]
"""
from __future__ import annotations

import itertools
import json
import shutil
import sys
import tempfile
from pathlib import Path

from app.engine import next_action as na
from app.engine_seed import QD1_SEED, SEED_DIR, _copy_cases
from app.store.sqlite_store import CaseStore
from app.workflows import autopilot, excavator_demo
from research.voi import from_case
from research.voi.planner import plan, sensitivity

A = from_case.ASSUMPTIONS


def mid(r):
    return (r[0] + r[1]) / 2


def frozen_cases(px4_db=None):
    tmp = Path(tempfile.mkdtemp())
    store = CaseStore(tmp / "eval.db")
    _copy_cases(store, QD1_SEED)
    for f in sorted(SEED_DIR.glob("*.sqlite.gz")):
        _copy_cases(store, f)
    ex1 = excavator_demo.seed(store)
    cases = []
    for c in store.cases():
        t = c["title"]
        key = ("QD-1" if t.startswith("Left front leg dragging") else "EX1" if c["id"] == ex1 else "ALFA rudder" if "rudder" in t
               else "ANYmal injected knee" if "SYNTHETIC fault injected" in t else "ANYmal real, no fault" if "no reported fault" in t else None)
        if key:
            cases.append((key, store, c["id"]))
    if px4_db:
        local = tmp / "px4.db"
        shutil.copy(px4_db, local)
        px4 = CaseStore(local)
        for c in px4.cases():
            cases.append((f"PX4 #26910 · {c['title'].split()[2]}", px4, c["id"]))
    return cases


def v2_first_step(store, cid):
    v = autopilot.advance(store, cid)
    return v.get("stage"), [c["check_id"] for c in v.get("checks") or []], v.get("say")


def realized(sc, truth, first, kind):
    """Expected loss (assumption midpoints) of a policy's path given the truth.
    kind 'act': first is an action. kind 'checks': first is a list of check ids, then policy picks the action."""
    loss = {a: {h: mid(r) for h, r in row.items()} for a, row in sc.actions.items()}
    if kind == "act":
        return loss[first][truth]
    prior_w = {h: mid(r) for h, r in sc.hypotheses.items()}
    z = sum(prior_w.values())
    prior = {h: v / z for h, v in prior_w.items()}
    checks = [c for c in sc.checks if c.id in first]
    lik = {c.id: {h: _norm({o: mid(row[h]) for o, row in c.outcomes.items()}) for h in prior} for c in checks}
    total = sum(mid(c.cost) + mid(c.delay) for c in checks)
    exp = 0.0
    for answer in itertools.product(*[list(c.outcomes) for c in checks]):
        p_true = 1.0
        for c, o in zip(checks, answer):
            p_true *= lik[c.id][truth][o]
        if p_true == 0:
            continue
        joint = {h: prior[h] * _prod(lik[c.id][h][o] for c, o in zip(checks, answer)) for h in prior}
        s = sum(joint.values())
        post = {h: v / s for h, v in joint.items()}
        act = kind(post, answer, checks) if callable(kind) else min(loss, key=lambda a: sum(post[h] * loss[a][h] for h in post))
        exp += p_true * loss[act][truth]
    return total + exp


def _norm(d):
    s = sum(d.values()) or 1.0
    return {k: v / s for k, v in d.items()}


def _prod(xs):
    out = 1.0
    for x in xs:
        out *= x
    return out


def v2_policy(run, sc):
    """What the product's rules would do after the site answers: its own decide() on the simulated answers."""
    a = na.assess(run)

    def choose(post, answer, checks):
        results = [{"check_id": c.id, "status": "performed", "outcome": o, "entry_id": "sim"} for c, o in zip(checks, answer)]
        rep = na.decide(run, a, [{"check_id": c.id} for c in checks], results)
        if rep["status"] == "fix_remotely_first":
            return "approved remote intervention"
        return "dispatch for diagnosis"  # dispatch_candidate, or hold (the rules then wait for a site visit or decision)
    return choose


VARIANTS = {
    "base": {},
    "failed remote attempt costs 2x (long downtime)": {("approved remote intervention", "hands_on"): 2.0},
    "wasted trip costs half (technician nearby)": {("dispatch for diagnosis", "remote"): 0.5, ("dispatch for diagnosis", "none"): 0.5},
    "site evidence arrives 4x faster": {"delay": 0.25},
    "no remote intervention possible": {"drop": "approved remote intervention"},
}


def variant(sc, change):
    import copy
    v = copy.deepcopy(sc)
    if change.get("drop"):
        v.actions.pop(change["drop"], None)
    for key, f in change.items():
        if key == "delay":
            for c in v.checks:
                c.delay = (c.delay[0] * f, c.delay[1] * f)
        elif isinstance(key, tuple):
            act, cat = key
            if act in v.actions:
                for h in v.actions[act]:
                    if from_case.category(h) == cat:
                        lo, hi = v.actions[act][h]
                        v.actions[act][h] = (lo * f, hi * f)
    return v


def main(out, px4_db=None):
    report = []
    truth = json.loads((Path(__file__).parent / "sealed_truth.json").read_text())["cases"]
    for key, store, cid in frozen_cases(px4_db):
        run = store.latest_run(cid)
        sc = from_case.scenario(run, key)
        stage, v2_checks, say = v2_first_step(store, cid)          # the product's first step
        voi = plan(sc, n=3000, tolerance=A["tolerance"])           # the research planner's first step
        sens = sensitivity(sc, n=1500, tolerance=A["tolerance"])
        by_cost = {name: plan(variant(sc, ch), n=1500, tolerance=A["tolerance"])["decision"]["option"] for name, ch in VARIANTS.items()}
        # ---- answers recorded; now open the label ----
        t = truth["PX4 #26910" if key.startswith("PX4") else key]
        h_true = t["true"] if t["true"] in sc.hypotheses else None
        loss_mid = {a: mid(row[h_true]) for a, row in sc.actions.items()} if h_true else {}
        oracle = min(loss_mid.values()) if loss_mid else None
        if stage in ("awaiting_approval", "approved_not_sent", "awaiting_result") and v2_checks:
            v2_loss = realized(sc, h_true, v2_checks, v2_policy(run, sc)) if h_true else None
            v2_desc = f"ask the site: {', '.join(v2_checks)}"
        elif stage == "weak_evidence":
            v2_loss, v2_desc = (loss_mid.get("monitor, no action") if h_true else None), "no request (only brief events)"
        else:
            v2_loss, v2_desc = (loss_mid.get("dispatch for diagnosis") if h_true else None), f"{stage}: next step on site or a decision"
        d = voi["decision"]
        if d["option"].startswith("act: "):
            voi_loss = loss_mid.get(d["option"][5:]) if h_true else None
        else:
            ids = d["option"][7:].split(" + ")
            voi_loss = realized(sc, h_true, ids, "best") if h_true else None
        v2_unsafe = (v2_desc.startswith("no request") and "act: monitor, no action" in sc.excluded)
        report.append({"case": key, "truth": t, "hypotheses_in_scope": sorted(sc.hypotheses), "truth_in_scope": h_true is not None,
                       "excluded_by_constraints": sc.excluded,
                       "v2": {"first_step": v2_desc, "say": say, "simulated_loss": _r(v2_loss), "regret": _r(v2_loss - oracle) if v2_loss is not None else None,
                              "picked_disallowed_action": v2_unsafe},
                       "voi": {"decision": d, "abstained": d["kind"] == "toss_up",
                               # A toss-up is an abstention: its top-listed option is reported for reference only, not scored as a choice.
                               "simulated_loss": None if d["kind"] == "toss_up" else _r(voi_loss),
                               "regret": None if d["kind"] == "toss_up" or voi_loss is None else _r(voi_loss - oracle),
                               "fallback_option_regret": _r(voi_loss - oracle) if voi_loss is not None else None,
                               "options": voi["options"][:6], "sensitivity": sens, "cost_scenarios": by_cost},
                       "oracle_action": min(loss_mid, key=loss_mid.get) if loss_mid else None, "oracle_loss": _r(oracle)})
    Path(out).write_text(json.dumps({"assumptions": A["version"], "scoring": "simulated at assumption midpoints; not real outcomes",
                                     "results": report}, indent=1))
    for r in report:
        print(f"{r['case']:28s} truth={r['truth']['true']:18s} in_scope={r['truth_in_scope']!s:5s} | v2: {r['v2']['first_step'][:60]:60s} regret {r['v2']['regret']} "
              f"| VOI: {('ABSTAIN (toss-up)' if r['voi']['abstained'] else r['voi']['decision']['option'])[:55]:55s} regret {r['voi']['regret']} stable={r['voi']['sensitivity']['stable']}")
        for name, opt in r["voi"]["cost_scenarios"].items():
            print(f"      {name:48s} -> {opt}")


def _r(x):
    return None if x is None else round(x)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
