"""Does each engineer observation genuinely change Veyra's next recommended action? (analysis on the v2 results; it adds
no metric and changes no score)

For every decision point, Veyra's safety-aware plan (app.agent.experiment_safety.plan) is computed twice from the same
frozen state: with only the engineer results known before the previous decision point, and with the results that
became known since. The difference isolates the effect of the new observations from the effect of new data. A result
"not the issue" deprioritises its target (it stays open: a report does not exclude); "partly" and "fault found" leave
the plan's open set as it is.

    python research/investigation_benchmark/v2/observation_impact.py results/shuttle-synthetic-v2.json
Writes results/observation_impact_<case>.json and prints a summary.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, BACKEND)

from app.agent import experiment_safety as S  # noqa: E402


def _v1():
    if "bench_v1_run" in sys.modules:
        return sys.modules["bench_v1_run"]
    spec = importlib.util.spec_from_file_location("bench_v1_run", os.path.join(os.path.dirname(HERE), "run.py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules["bench_v1_run"] = m
    spec.loader.exec_module(m)
    return m


def plan_at(state, deprioritised, hv_system=True):
    cands = [{"id": m["id"], "what": m["what"], "cost_tier": m["cost_tier"], "strength": m["strength"], "predicts": m.get("predicts") or {}}
             for m in (state.get("plan") or {}).get("measurements") or []]
    for r in (state.get("plan") or {}).get("data_requests") or []:
        cands.append({"id": f"observe:{r['hypothesis']}", "what": r["what"], "cost_tier": r.get("cost_tier", 1), "strength": "weak", "predicts": {}})
    p = S.plan(cands, state["open"], hv_system=hv_system, deprioritised=deprioritised)
    rec, then = p.get("recommended") or {}, p.get("then") or {}
    return {"recommended": rec.get("id"), "hazard": (rec.get("safety") or {}).get("worst"), "requires_review": bool((rec.get("safety") or {}).get("requires_review")),
            "then": then.get("id"), "then_hazard": (then.get("safety") or {}).get("worst"), "considered_open": p.get("considered_open"),
            "deprioritised": p.get("deprioritised"), "why": p.get("why")}


def run(results_path, case_dir):
    H = _v1()
    r = json.load(open(results_path, encoding="utf-8"))
    case = H.load_case(case_dir)
    pts = [p for p in r["points"] if not p.get("void")]
    out = []
    for i, p in enumerate(pts):
        cut = date.fromisoformat(p["cutoff"])
        prev = date.fromisoformat(pts[i - 1]["cutoff"]) if i else None
        known_now = [s for s in case["steps"] if date.fromisoformat(s["when"]) < cut and H.result_known_on(s) < cut]
        known_before = [s for s in known_now if prev is not None and H.result_known_on(s) < prev]
        new = [s for s in known_now if s not in known_before]
        dep = lambda ss: sorted({t for s in ss if s["result"] == "not_the_issue" for t in H.J.targets(s, case["space"])})  # noqa: E731
        before = plan_at(p["state"], dep(known_before))
        after = plan_at(p["state"], dep(known_now))
        changed = (before["recommended"], before["then"]) != (after["recommended"], after["then"])
        out.append({"cutoff": p["cutoff"], "new_engineer_results": [{"step": s["id"], "check": s["check"], "result": s["result"], "targets": H.J.targets(s, case["space"]),
                                                                    "provenance": "engineer_reported"} for s in new],
                    "plan_without_them": before, "plan_with_them": after, "action_changed": changed,
                    "why": (f"{', '.join(dep(new))} reported not the issue: it stays open but is deprioritised, so the plan now looks for a check that separates "
                            f"{', '.join(after['considered_open'] or [])}" if changed else
                            ("no new engineer result" if not new else "the new results did not change which check separates the open explanations best and most safely")),
                    "remaining_open": p["state"]["open"]})
    return {"case": r["case"], "source": os.path.relpath(results_path, HERE), "points": out,
            "summary": {"points_with_new_results": sum(1 for x in out if x["new_engineer_results"]), "changed_action": sum(1 for x in out if x["action_changed"])}}


if __name__ == "__main__":
    path = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "results", "shuttle-synthetic-v2.json"))
    res = json.load(open(path, encoding="utf-8"))
    o = run(path, os.path.join(HERE, "cases", res["case"]))
    dst = os.path.join(HERE, "results", f"observation_impact_{res['case']}.json")
    json.dump(o, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for x in o["points"]:
        print(x["cutoff"], [s["step"] + ":" + s["result"] for s in x["new_engineer_results"]], x["plan_without_them"]["recommended"], "->", x["plan_with_them"]["recommended"],
              "then", x["plan_with_them"]["then"], "| changed:", x["action_changed"], "|", x["why"][:120])
    print(dst, o["summary"])
