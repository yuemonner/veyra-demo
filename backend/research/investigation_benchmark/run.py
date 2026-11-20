"""Blind investigation replay: harness and scorer. Scoring rules are in PROTOCOL.md (frozen before the first run).

    python research/investigation_benchmark/run.py [case_dir ...]      (default: every folder in cases/)

For each case: validate the package; for each decision point freeze the evidence, audit the frozen pack for leaks, run
Veyra in an isolated process on the pack alone, judge each engineer step from that state; only then read the sealed
truth and score M1–M5, eliminations, verified and estimated avoidable work, leakage, unsupported conclusions and the
places where Veyra did worse than the engineers. Writes results/<case>.json and RESULTS.md.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, BACKEND)

from app.agent import step_judge as J  # noqa: E402
from app.agent import timeline as T  # noqa: E402

RESULTS = ["not_the_issue", "partly", "fault_found", "inconclusive"]
ZH_DATE = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日")
REST_CHECKS = {"segment_r", "meter_v"}  # measured at rest: a contact that opens only under load reads normal


# ---------------------------------------------------------------- package
def load_case(d: str) -> Dict[str, Any]:
    j = lambda n: json.load(open(os.path.join(d, n), encoding="utf-8"))  # noqa: E731
    logs = sorted(f for f in os.listdir(d) if f.startswith("log") and f.endswith(".txt"))
    w = j("walkthrough.json")
    w = w.get("walkthrough", w)
    case = {"id": os.path.basename(d.rstrip("/")), "dir": d, "report": j("report.json"), "space": j("hypotheses.json"),
            "steps": [dict(s, id=f"s{i + 1}") for i, s in enumerate(w["steps"])], "walkthrough": w,
            "logs": {f: open(os.path.join(d, f), encoding="utf-8").read().split("\n") for f in logs},
            "available_from": (j("files.json") if os.path.exists(os.path.join(d, "files.json")) else {})}
    return case


def result_known_on(step: Dict[str, Any]) -> date:
    """When the step's result became known: an explicit result_known date, else the latest date its decided_by names."""
    when = date.fromisoformat(step["when"])
    if step.get("result_known"):
        return date.fromisoformat(step["result_known"])
    text = step.get("decided_by") or ""
    ds = T._dates(text) + [date(when.year, int(m), int(dd)) for m, dd in ZH_DATE.findall(text)]
    return max([when] + ds)


def validate(case: Dict[str, Any]) -> List[Dict[str, Any]]:
    issues = []
    ids = [h["id"] for h in case["space"]["hypotheses"]]
    if len(ids) != len(set(ids)):
        issues.append({"severity": "error", "issue": "duplicate hypothesis ids"})
    if not case["logs"]:
        issues.append({"severity": "error", "issue": "no log*.txt"})
    for s in case["steps"]:
        if s.get("result") not in RESULTS:
            issues.append({"severity": "error", "step": s["id"], "issue": f"result {s.get('result')!r} not one of {RESULTS}"})
        if not s.get("when"):
            issues.append({"severity": "error", "step": s["id"], "issue": "no date"})
            continue
        k = result_known_on(s)
        if k > date.fromisoformat(s["when"]):
            issues.append({"severity": "note", "step": s["id"], "issue": f"result became known on {k} (from decided_by), after the step on {s['when']}; "
                                                                             "it is hidden from decision points before then"})
    # a later step's stated reason relies on a result not yet known
    for s in case["steps"]:
        for p in case["steps"]:
            if p is s or not s.get("when") or not p.get("when"):
                continue
            if date.fromisoformat(p["when"]) < date.fromisoformat(s["when"]) < result_known_on(p) and p["result"] == "not_the_issue":
                issues.append({"severity": "warning", "step": s["id"],
                               "issue": f"taken on {s['when']}, before {p['id']}'s result was known ({result_known_on(p)}); if its reason relies on "
                                        f"{p['id']} having failed, the walkthrough's dates are inconsistent. Ask the engineers."})
    if case["report"].get("synthetic"):
        issues.append({"severity": "note", "issue": "synthetic case: tests the harness, not Veyra"})
    return issues


def decision_points(case: Dict[str, Any]) -> List[date]:
    ds = {date.fromisoformat(case["report"]["report_date"])} | {date.fromisoformat(s["when"]) for s in case["steps"] if s.get("when")}
    return sorted(ds)


def prior_steps(case: Dict[str, Any], cutoff: date) -> List[Dict[str, Any]]:
    out = []
    for s in case["steps"]:
        if date.fromisoformat(s["when"]) >= cutoff:
            continue
        known = result_known_on(s) < cutoff
        out.append({"id": s["id"], "check": s["check"], "part": s.get("part"), "kind": s.get("kind"), "when": s["when"],
                    "why": s.get("why"), "result": s["result"] if known else "pending",
                    "decided_by": s.get("decided_by") if known else None,
                    "targets": J.targets(s, case["space"])})
    return out


# ---------------------------------------------------------------- one decision point
def freeze_point(case: Dict[str, Any], cutoff: date, phrases: List[str], workdir: str) -> Dict[str, Any]:
    lines, orig, audits = [], [], []
    for f, raw in case["logs"].items():
        af = case["available_from"].get(f)
        fl, fo, a = T.freeze(raw, cutoff, date.fromisoformat(af) if af else None)
        lines += fl
        orig += fo
        audits.append(dict(a, file=f))
    priors = prior_steps(case, cutoff)
    leaks = T.leak_check(lines, cutoff, phrases)
    for p in priors:   # the engineers' earlier steps go into the pack too: audit their text the same way
        txt = " ".join(str(p.get(k) or "") for k in ("check", "part", "why", "decided_by"))
        for d in T._dates(txt):
            if d >= cutoff:
                leaks.append({"kind": "prior step names a later date", "step": p["id"]})
        for ph in phrases:
            if ph.lower() in txt.lower():
                leaks.append({"kind": "sealed phrase in prior step", "step": p["id"], "phrase": ph})
    pack = os.path.join(workdir, cutoff.isoformat())
    os.makedirs(pack)
    open(os.path.join(pack, "log.txt"), "w", encoding="utf-8").write("\n".join(lines))
    json.dump(case["report"], open(os.path.join(pack, "report.json"), "w", encoding="utf-8"), ensure_ascii=False)
    json.dump(case["space"], open(os.path.join(pack, "hypotheses.json"), "w", encoding="utf-8"), ensure_ascii=False)
    json.dump({"steps": priors, "orig_lines": orig}, open(os.path.join(pack, "prior_steps.json"), "w", encoding="utf-8"), ensure_ascii=False)
    open(os.path.join(pack, "cutoff.txt"), "w").write(cutoff.isoformat())
    return {"pack": pack, "audit": audits, "leaks": leaks, "priors": priors, "rows": sum(a.get("kept_rows") or 0 for a in audits)}


def run_veyra(pack: str) -> Dict[str, Any]:
    """Veyra in a separate isolated interpreter that is given the pack directory only."""
    r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "veyra_blind.py"), pack, BACKEND], capture_output=True, text=True, timeout=300)
    if r.returncode:
        raise RuntimeError(f"Veyra run failed at {pack}: {r.stderr[-2000:]}")
    return json.load(open(os.path.join(pack, "state.json"), encoding="utf-8"))


# ---------------------------------------------------------------- scoring (reads the truth)
def simulate(state: Dict[str, Any], truth: Dict[str, Any]) -> Dict[str, Any]:
    """Veyra's recommended checks, with outcomes simulated from the truth via each check's own predicted classes."""
    main = truth["main_cause"]
    open_ = list(state["open"])
    rec = state["plan"].get("recommended")
    meas = {m["id"]: m for m in state["plan"].get("measurements") or []}
    checks, unsimulated, seen = [], [], set()
    while rec and rec["id"] not in seen:
        seen.add(rec["id"])
        pred = meas.get(rec["id"], {}).get("predicts") or rec.get("predicts") or {}
        if main not in pred:
            unsimulated.append({"check": rec["id"], "why": "no predicted outcome for the main cause"})
            break
        cls = pred[main]
        misleading = bool(truth.get("load_only") and rec["id"] in REST_CHECKS)
        keep = [h for h in open_ if pred.get(h, cls) == cls] if not misleading else [h for h in open_ if h != main]
        checks.append({"check": rec["id"], "cost_tier": rec["cost_tier"], "eliminates": [h for h in open_ if h not in keep],
                       "remaining": keep, "misleading": misleading})
        open_ = keep
        predicted_left = [h for h in open_ if h in pred]
        if len(predicted_left) <= 1:
            break
        nxt = [m for m in meas.values() if m["id"] not in seen and m["strength"] == "strong"
               and len({m["predicts"].get(h) for h in predicted_left if h in m["predicts"]}) > 1]
        rec = min(nxt, key=lambda m: m["cost_tier"]) if nxt else None
    for d in state["plan"].get("data_requests") or []:
        unsimulated.append({"check": d.get("what"), "hypothesis": d["hypothesis"], "cost_tier": d.get("cost_tier"),
                            "why": "data request for a hypothesis the data cannot show; outcome not predicted"})
    return {"checks": checks, "remaining": open_, "single_candidate": open_ == [main], "main_kept": main in open_, "unsimulated": unsimulated}


def next_check_value(state: Dict[str, Any], truth: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    rec = state["plan"].get("recommended")
    if not rec:
        return None
    pred, main, open_ = rec.get("predicts") or {}, truth["main_cause"], state["open"]
    if main not in pred:
        return {"check": rec["id"], "simulated": False, "why": "no predicted outcome for the main cause"}
    cls = pred[main]
    keep = [h for h in open_ if pred.get(h, cls) == cls]
    exp = [len([g for g in open_ if pred.get(g, pred.get(h)) == pred.get(h)]) for h in open_ if h in pred]
    misleading = bool(truth.get("load_only") and rec["id"] in REST_CHECKS)
    return {"check": rec["id"], "what": rec["what"], "cost_tier": rec["cost_tier"], "simulated": True,
            "keeps_main": main in keep and not misleading, "eliminates_wrong": [h for h in open_ if h not in keep],
            "expected_remaining": round(sum(exp) / len(exp), 2) if exp else None, "unpredicted_open": [h for h in open_ if h not in pred],
            "misleading": misleading, "then": rec.get("then")}


def score(case: Dict[str, Any], points: List[Dict[str, Any]], truth: Dict[str, Any]) -> Dict[str, Any]:
    main, contrib = truth["main_cause"], set(truth.get("contributing") or [])
    t0 = points[0]["cutoff"]
    days = lambda d: (date.fromisoformat(d) - date.fromisoformat(t0)).days  # noqa: E731
    space = {h["id"]: h for h in case["space"]["hypotheses"]}
    loc = (truth.get("location") or "").split()[0] if truth.get("location") else ""

    # M1
    m1v = None
    for i, p in enumerate(points):
        st, v = p["state"], p["next_check"]
        sep = v and v.get("simulated") and v["keeps_main"] and not v["unpredicted_open"] and len([h for h in st["open"] if h != main]) == len(v["eliminates_wrong"])
        if main in st["open"] and len(st["open"]) <= 3 and sep:
            m1v = {"point": i, "cutoff": p["cutoff"], "days": days(p["cutoff"])}
            break
    m1e = None
    for s in case["steps"]:
        text = f"{s['check']} {s.get('part') or ''} {s.get('why') or ''}"
        if main in J.targets(s, case["space"]) and (not loc or loc in text):
            m1e = {"step": s["id"], "when": s["when"], "days": days(s["when"])}
            break
    m1_notes = []
    if m1v is None:
        for p in points:
            st = p["state"]
            why = []
            if main not in st["open"]:
                why.append("main cause not open")
            if len(st["open"]) > 3:
                why.append(f"{len(st['open'])} hypotheses open ({', '.join(st['open'])})")
            v = p["next_check"] or {}
            if v.get("unpredicted_open"):
                why.append(f"the recommended check cannot separate {', '.join(v['unpredicted_open'])}")
            m1_notes.append({"cutoff": p["cutoff"], "why_not": why})

    # M2
    elim_e = [{"step": s["id"], "hypothesis": t, "correct": t != main and t not in contrib, "truth_role": "main" if t == main else ("contributing" if t in contrib else "not involved"),
               "evidence_tier": "reported", "decided_by": s.get("decided_by")}
              for s in case["steps"] if s["result"] == "not_the_issue" for t in J.targets(s, case["space"])]
    first_weak: Dict[str, str] = {}
    for p in points:
        for h in p["state"]["weakened"]:
            first_weak.setdefault(h, p["cutoff"])
    elim_v = [{"hypothesis": h, "from": c, "correct": h != main and h not in contrib, "truth_role": "main" if h == main else ("contributing" if h in contrib else "not involved"),
               "evidence_tier": "inferred (tool: weakened)"} for h, c in first_weak.items()]

    # M3
    found = next((s for s in case["steps"] if s["result"] == "fault_found" and main in J.targets(s, case["space"])), None)
    upto = case["steps"][: case["steps"].index(found) + 1] if found else case["steps"]
    m3e = {"steps": len(upto), "hypothesis_tests": sum(1 for s in upto if J.targets(s, case["space"])),
           "minutes": sum(s.get("minutes") or 0 for s in upto), "parts_cost": sum(s.get("cost") or 0 for s in upto),
           "currency": case["walkthrough"].get("currency"), "found_by": found["id"] if found else None, "tier": "reported (engineers' records)"}
    m3v = [{"cutoff": p["cutoff"], **p["simulation"]} for p in points]

    # M4, M5
    m4 = [{"cutoff": p["cutoff"], "main_open": main in p["state"]["open"],
           "contributing": {c: p["state"]["hypotheses"].get(c, {}).get("status") for c in sorted(contrib)}} for p in points]
    m5 = [{"cutoff": p["cutoff"], **(p["next_check"] or {"check": None})} for p in points]

    # avoidable work
    judged = [j for p in points for j in p["judgments"]]
    verified = [j for j in judged if j["hindsight"]["verified_avoidable"]]
    s_by = {s["id"]: s for s in case["steps"]}
    estimated = [{"step": j["id"], "check": j["check"], "items": j["hindsight"]["estimated"],
                  "recorded_minutes": s_by[j["id"]].get("minutes"), "recorded_cost": s_by[j["id"]].get("cost"),
                  "note": "estimate only: no time or money is claimed"} for j in judged if j["hindsight"]["estimated"]]

    # unsupported conclusions
    unsupported = []
    for p in points:
        for f in p["state"]["facts"]:
            if f["tier"] == "confirmed" and not f["lines"] and not f.get("line_ranges"):
                unsupported.append({"cutoff": p["cutoff"], "fact": f["id"], "issue": "confirmed without cited lines", "text": f["text"][:100]})
        for h, s in p["state"]["hypotheses"].items():
            if s["status"] in ("weakened", "no_support") and not s["facts"]:
                unsupported.append({"cutoff": p["cutoff"], "hypothesis": h, "issue": f"{s['status']} without facts"})
    for j in judged:
        for t, v in j["data_before"].items():
            if v["data_before"] == "argued_against" and v["tier"] == "confirmed":
                unsupported.append({"step": j["id"], "hypothesis": t, "issue": "weakened claimed as confirmed"})
        if j["hindsight"]["verified_avoidable"] and not any(v["tier"] == "confirmed" for v in j["data_before"].values()):
            unsupported.append({"step": j["id"], "issue": "verified avoidable without a confirmed fact"})

    # worse than the engineers
    worse = []
    for s in case["steps"]:
        for t in J.targets(s, case["space"]):
            if s["result"] in ("partly", "fault_found") and (t in contrib or t == main):
                p = next(p for p in points if p["cutoff"] == s["when"])
                st = p["state"]["hypotheses"].get(t, {}).get("status")
                if st != "supported":
                    worse.append({"kind": "engineers found a cause Veyra could not identify then", "step": s["id"], "hypothesis": t, "role": "main" if t == main else "contributing",
                                  "veyra_status": st, "why": p["state"]["hypotheses"].get(t, {}).get("why")})
    if m1v is None and m1e is not None:
        worse.append({"kind": "no useful hypothesis by the frozen M1 rule at any decision point, while the engineers reached one", "engineers": m1e})
    elif m1v and m1e and m1v["days"] > m1e["days"]:
        worse.append({"kind": "useful hypothesis later than the engineers", "veyra": m1v, "engineers": m1e})
    for e in elim_v:
        if not e["correct"]:
            worse.append({"kind": "incorrect elimination by Veyra", **e})
    for p in points:
        sim = p["simulation"]
        if not sim["single_candidate"] and m3e["found_by"]:
            worse.append({"kind": "Veyra's simulated path does not narrow to the main cause alone", "cutoff": p["cutoff"], "remaining": sim["remaining"]})
            break
    for p in points:
        if p["simulation"]["checks"] and len(p["simulation"]["checks"]) > m3e["hypothesis_tests"]:
            worse.append({"kind": "more checks than the engineers", "cutoff": p["cutoff"]})

    # where Veyra did better (same rules, reported alongside, never netted against the list above)
    better = []
    for e in elim_e:
        if not e["correct"]:
            s = s_by[e["step"]]
            p = next(p for p in points if p["cutoff"] == s["when"])
            st = p["state"]["hypotheses"].get(e["hypothesis"], {})
            if st.get("status") == "supported":
                tiers = [f["tier"] for f in p["state"]["facts"] if f["id"] in st["facts"]]
                better.append({"kind": "engineers eliminated a true factor that Veyra's data still supported", "step": e["step"], "hypothesis": e["hypothesis"],
                               "veyra_why": st.get("why"), "facts": [f["text"] for f in p["state"]["facts"] if f["id"] in st["facts"]],
                               "as_of": [f["as_of"] for f in p["state"]["facts"] if f["id"] in st["facts"]], "tiers": tiers})
    if m1e and m1v and m1v["days"] < m1e["days"]:
        better.append({"kind": "useful hypothesis earlier than the engineers", "veyra": m1v, "engineers": m1e})
    first_main = next((p for p in points if main in p["state"]["open"]), None)
    if first_main and m1e and days(first_main["cutoff"]) < m1e["days"]:
        better.append({"kind": "main cause open and the discriminating check recommended before the engineers targeted it (not the M1 rule)",
                       "veyra_from": first_main["cutoff"], "check": (first_main["next_check"] or {}).get("check"), "engineers": m1e})

    return {"M1": {"veyra": m1v, "engineers": m1e, "veyra_not_met": m1_notes}, "M2": {"engineers": elim_e, "veyra": elim_v},
            "M3": {"engineers": m3e, "veyra": m3v}, "M4": m4, "M5": m5,
            "avoidable": {"verified": [{"step": j["id"], "minutes": s_by[j["id"]].get("minutes"), "cost": s_by[j["id"]].get("cost")} for j in verified],
                          "estimated": estimated},
            "wrong_eliminations": [dict(e, side="engineers") for e in elim_e if not e["correct"]] + [dict(e, side="Veyra") for e in elim_v if not e["correct"]],
            "unsupported": unsupported, "worse_than_engineers": worse, "better_than_engineers": better}


# ---------------------------------------------------------------- injected-leak test of the audit
def leak_selftest(case: Dict[str, Any], phrases: List[str]) -> List[Dict[str, Any]]:
    raw = next(iter(case["logs"].values()))
    cutoff = decision_points(case)[0]
    row = next(l for l in raw if T.ROW_TS.match(l))
    later = cutoff.replace(day=min(cutoff.day + 3, 28)).isoformat()
    tests = [
        ("row after the cutoff", raw + [later + row[10:]]),
        ("header with a later date", ["# service visit " + later] + raw),
        ("header with an outcome word", ["# FR inverter replaced, problem came back"] + raw),
        ("sealed phrase in a kept header", [f"# note: check the {phrases[0]}"] + raw if phrases else raw),
    ]
    out = []
    for name, lines in tests:
        injected = [l for l in lines if l not in set(raw)]
        fl, _, _ = T.freeze(lines, cutoff)
        survives = any(l in fl for l in injected)
        flagged_unfrozen = bool(T.leak_check(lines, cutoff, phrases))
        flagged_pack = bool(T.leak_check(fl, cutoff, phrases))
        # pass: the leak never reaches Veyra unnoticed (the freeze removes it, or the audit flags the pack and voids the point)
        out.append({"test": name, "removed_by_freeze": not survives, "audit_flags_unfrozen": flagged_unfrozen,
                    "audit_flags_pack": flagged_pack, "pass": flagged_unfrozen and (not survives or flagged_pack)})
    return out


def verifier_selftest(state: Dict[str, Any], judgment: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The AI interpretation verifier against scripted claims, valid and invalid."""
    fid = state["facts"][0]["id"] if state["facts"] else "f1"
    inf = next((f["id"] for f in state["facts"] if f["tier"] == "inferred"), fid)
    ok_h = next((h for h, s in state["hypotheses"].items() if s["status"] == "weakened"), None)
    sup = next(iter(state["open"]), "x")
    cases = [
        ("valid, cited, within tier", {"claims": [{"text": "x", "fact_ids": [inf], "tier": "inferred"}]}, True),
        ("no citation", {"claims": [{"text": "the cause is the connector", "fact_ids": [], "tier": "inferred"}]}, False),
        ("cites a fact that does not exist", {"claims": [{"text": "x", "fact_ids": ["f999"], "tier": "inferred"}]}, False),
        ("stronger than its evidence", {"claims": [{"text": "x", "fact_ids": [inf], "tier": "confirmed"}]}, False),
        ("eliminates a supported hypothesis", {"claims": [], "eliminates": [{"hypothesis": sup, "fact_ids": [fid]}]}, False),
    ]
    if ok_h:
        cases.append(("eliminates a hypothesis the tools weakened", {"claims": [], "eliminates": [{"hypothesis": ok_h, "fact_ids": [fid]}]}, True))
    out = []
    for name, payload, should_pass in cases:
        problems = J.verify_ai(payload, judgment, state)
        out.append({"test": name, "expected": "accept" if should_pass else "reject", "got": "accept" if not problems else "reject",
                    "pass": (not problems) == should_pass, "problems": problems})
    return out


# ---------------------------------------------------------------- run
def run_case(d: str) -> Dict[str, Any]:
    case = load_case(d)
    issues = validate(case)
    if any(i["severity"] == "error" for i in issues):
        return {"case": case["id"], "issues": issues, "status": "invalid"}
    phrases = T.sealed_phrases(json.load(open(os.path.join(d, "truth.sealed.json"), encoding="utf-8")))  # for the audit only
    work = tempfile.mkdtemp(prefix="blind-")
    points = []
    try:
        for cutoff in decision_points(case):
            fp = freeze_point(case, cutoff, phrases, work)
            if fp["leaks"]:
                points.append({"cutoff": cutoff.isoformat(), "void": True, "leaks": fp["leaks"]})
                continue
            state = run_veyra(fp["pack"])
            prior = fp["priors"]
            judgments = [J.judge_step(s, state, case["space"], prior, case["report"]) for s in case["steps"] if s["when"] == cutoff.isoformat()]
            points.append({"cutoff": cutoff.isoformat(), "void": False, "rows": fp["rows"], "audit": fp["audit"], "leaks": [],
                           "prior_visible": [{"id": p["id"], "result": p["result"]} for p in prior], "state": state, "judgments": judgments})
    finally:
        shutil.rmtree(work, ignore_errors=True)
    # ---- the sealed truth is read only from here on
    truth = json.load(open(os.path.join(d, "truth.sealed.json"), encoding="utf-8"))
    valid = [p for p in points if not p["void"]]
    for p in valid:
        p["simulation"] = simulate(p["state"], truth)
        p["next_check"] = next_check_value(p["state"], truth)
    sc = score(case, valid, truth)
    try:   # what the earlier debug replay (v1, full-log hindsight) said about the same steps, for comparison
        from app.agent import replay as R
        old = R.replay(next(iter(case["logs"].values())), dict(case["walkthrough"], steps=case["steps"]))
        sc["replay_v1"] = [{"step": s["id"], "check": s["check"], "v1_hindsight": o.get("hindsight")} for s, o in zip(case["steps"], old["steps"])]
    except Exception as e:  # noqa: BLE001
        sc["replay_v1"] = {"error": str(e)}
    first = valid[0]
    j0 = next((j for p in valid for j in p["judgments"] if j["targets"]), None)
    return {"case": case["id"], "synthetic": bool(case["report"].get("synthetic")), "protocol": "PROTOCOL.md (frozen 2026-11-06)",
            "judge": J.VERSION, "issues": issues, "truth": {k: truth.get(k) for k in ("main_cause", "contributing", "confidence", "established_at", "load_only")},
            "points": points, "score": sc, "void_points": [p["cutoff"] for p in points if p["void"]],
            "leak_selftest": leak_selftest(case, phrases), "verifier_selftest": verifier_selftest(first["state"], j0) if j0 else []}


if __name__ == "__main__":
    dirs = sys.argv[1:] or [os.path.join(HERE, "cases", c) for c in sorted(os.listdir(os.path.join(HERE, "cases"))) if not c.startswith("_") and os.path.isdir(os.path.join(HERE, "cases", c))]
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    for d in dirs:
        r = run_case(os.path.abspath(d))
        path = os.path.join(HERE, "results", f"{r['case']}.json")
        json.dump(r, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
        print(path)
