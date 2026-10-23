"""Decision-aware value-of-information planner. RESEARCH ONLY: not imported by the product.

Question it answers: what should the engineer do next, and is more investigation worth delaying the action?

  1. Feasibility first: actions that break a safety or authorization constraint are removed before any costs are
     compared (they are listed with the reason, never scored).
  2. Every number is an explicit range (assumptions.json), sampled under a named distribution. The planner reports
     how the recommendation behaves across those samples; it never reports a single "probability that X is best".
  3. Options = act now (each feasible action) | gather one piece of evidence, then act | gather two together, then act.
     Each option's loss = its own cost + delay + the expected loss of the best action afterwards, under the sample.
  4. Robustness: per option, how often it is the best and how often its regret (loss minus the sample's best) stays
     within a tolerance. "Never bad" = within tolerance in at least 90% of samples.
  5. Stop rule: when acting now is never bad and no evidence option is clearly better (best in most samples with a
     median gain above the tolerance), the answer is "enough evidence to act".
"""
from __future__ import annotations

import itertools
import math
import random
import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

Range = Tuple[float, float]


def draw(rng: random.Random, r: Range, dist: str) -> float:
    lo, hi = r
    if hi <= lo:
        return lo
    if dist == "uniform":
        return rng.uniform(lo, hi)
    if dist == "triangular":
        return rng.triangular(lo, hi, (lo + hi) / 2)
    if dist == "low":  # mass near the low end
        return rng.triangular(lo, hi, lo)
    if dist == "high":
        return rng.triangular(lo, hi, hi)
    raise ValueError(dist)


@dataclass
class Check:
    id: str
    label: str
    cost: Range                       # money-equivalent cost of asking and doing it
    delay: Range                      # downtime-equivalent cost of waiting for the answer
    outcomes: Dict[str, Dict[str, Range]]  # outcome -> {hypothesis: P(outcome | hypothesis) weight range}; normalised per hypothesis
    needs_authorization: bool = False


@dataclass
class Scenario:
    case: str
    hypotheses: Dict[str, Range]      # prior weight ranges (normalised per sample)
    actions: Dict[str, Dict[str, Range]]  # action -> {hypothesis: loss range}
    checks: List[Check]
    excluded: Dict[str, str] = field(default_factory=dict)  # action or check -> why it is not allowed
    notes: List[str] = field(default_factory=list)


def _sample(sc: Scenario, rng: random.Random, dist: str, shrink: float = 0.0):
    w = {h: draw(rng, r, dist) for h, r in sc.hypotheses.items()}
    z = sum(w.values()) or 1.0
    prior = {h: v / z for h, v in w.items()}
    loss = {a: {h: draw(rng, r, dist) for h, r in row.items()} for a, row in sc.actions.items()}
    checks = []
    for c in sc.checks:
        lik = {}
        for h in sc.hypotheses:
            raw = {o: draw(rng, row.get(h, (0.2, 0.8)), dist) for o, row in c.outcomes.items()}
            s = sum(raw.values()) or 1.0
            p = {o: v / s for o, v in raw.items()}
            if shrink:  # pessimism about evidence: pull every answer toward "uninformative"
                k = len(p)
                p = {o: (1 - shrink) * v + shrink / k for o, v in p.items()}
            lik[h] = p
        checks.append((c, draw(rng, c.cost, dist) + draw(rng, c.delay, dist), lik))
    return prior, loss, checks


def _best_act(post: Dict[str, float], loss) -> Tuple[str, float]:
    return min(((a, sum(post[h] * row[h] for h in post)) for a, row in loss.items()), key=lambda x: x[1])


def _with_evidence(prior, loss, picked) -> float:
    """Expected loss of: get all picked checks' answers, then take the best action given them."""
    total = 0.0
    for answer in itertools.product(*[list(c[2][next(iter(prior))].keys()) for c in picked]):
        joint = {h: prior[h] * math.prod(c[2][h][o] for c, o in zip(picked, answer)) for h in prior}
        py = sum(joint.values())
        if py <= 0:
            continue
        post = {h: v / py for h, v in joint.items()}
        total += py * _best_act(post, loss)[1]
    return total + sum(c[1] for c in picked)


def plan(sc: Scenario, n: int = 4000, dist: str = "uniform", seed: int = 1, shrink: float = 0.0, tolerance: float = 150.0,
         pairs: bool = True) -> Dict:
    rng = random.Random(seed)
    names: List[str] = [f"act: {a}" for a in sc.actions] + [f"check: {c.id}" for c in sc.checks]
    if pairs:
        names += [f"check: {a.id} + {b.id}" for a, b in itertools.combinations(sc.checks, 2)]
    regrets = {k: [] for k in names}
    gains = {k: [] for k in names}
    wins = {k: 0 for k in names}
    act_name = {}
    for _ in range(n):
        prior, loss, checks = _sample(sc, rng, dist, shrink)
        a_best, a_loss = _best_act(prior, loss)
        values = {f"act: {a}": sum(prior[h] * row[h] for h in prior) for a, row in loss.items()}
        for c in checks:
            values[f"check: {c[0].id}"] = _with_evidence(prior, loss, [c])
        if pairs:
            for x, y in itertools.combinations(checks, 2):
                values[f"check: {x[0].id} + {y[0].id}"] = _with_evidence(prior, loss, [x, y])
        best = min(values.values())
        top = min(values, key=values.get)
        wins[top] += 1
        for k, v in values.items():
            regrets[k].append(v - best)
            gains[k].append(a_loss - v)  # positive: better than acting now on the best current action
        act_name[a_best] = act_name.get(a_best, 0) + 1
    rows = []
    for k in names:
        r = sorted(regrets[k])
        g = sorted(gains[k])
        rows.append({"option": k, "best_in": round(wins[k] / n, 3), "never_bad": round(sum(x <= tolerance for x in r) / n, 3),
                     "regret_median": round(r[n // 2]), "regret_p90": round(r[int(0.9 * (n - 1))]),
                     "gain_vs_acting_now_median": round(g[n // 2])})
    rows.sort(key=lambda x: (-x["never_bad"], x["regret_median"]))
    act_rows = [x for x in rows if x["option"].startswith("act: ")]
    check_rows = [x for x in rows if x["option"].startswith("check: ")]
    best_act_row = max(act_rows, key=lambda x: (x["never_bad"], -x["regret_median"]))
    best_check = max(check_rows, key=lambda x: (x["best_in"], x["gain_vs_acting_now_median"])) if check_rows else None
    clearly_better = bool(best_check and best_check["best_in"] >= 0.5 and best_check["gain_vs_acting_now_median"] > tolerance)
    if best_act_row["never_bad"] >= 0.9 and not clearly_better:
        decision = {"kind": "act", "option": best_act_row["option"], "say": f"Enough evidence to act: {best_act_row['option'][5:]}."}
    elif clearly_better:
        decision = {"kind": "gather", "option": best_check["option"], "say": f"Worth getting first: {best_check['option'][7:]}."}
    else:
        robust = [x for x in rows if x["never_bad"] >= 0.9]
        decision = ({"kind": "act" if robust[0]["option"].startswith("act") else "gather", "option": robust[0]["option"],
                     "say": f"No option is clearly best; this one is never far from best: {robust[0]['option']}."} if robust else
                    {"kind": "toss_up", "option": rows[0]["option"],
                     "say": "No option is robust under these assumptions. Narrow the assumptions (costs, likelihoods) before deciding."})
    return {"case": sc.case, "distribution": dist, "samples": n, "shrink": shrink, "tolerance": tolerance, "decision": decision,
            "options": rows, "excluded": sc.excluded, "most_common_act_now": max(act_name, key=act_name.get), "notes": sc.notes}


def sensitivity(sc: Scenario, n: int = 3000, tolerance: float = 150.0) -> Dict:
    """Same scenario under different sampling distributions and with evidence made less informative."""
    runs = {}
    for dist in ("uniform", "triangular", "low", "high"):
        runs[dist] = plan(sc, n=n, dist=dist, tolerance=tolerance)["decision"]["option"]
    for shrink in (0.3, 0.6):
        runs[f"uniform, evidence {int(shrink * 100)}% less informative"] = plan(sc, n=n, shrink=shrink, tolerance=tolerance)["decision"]["option"]
    stable = len(set(runs.values())) == 1
    return {"case": sc.case, "decisions": runs, "stable": stable}
