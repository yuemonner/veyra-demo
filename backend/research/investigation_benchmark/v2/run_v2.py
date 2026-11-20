"""Blind investigation replay, protocol v2 (PROTOCOL_v2.md): telemetry + artifacts, causal roles, decision quality.

    python research/investigation_benchmark/v2/run_v2.py [case_dir ...]

Reuses the v1 harness for packaging, freezing logs and v1 scoring (unchanged), and adds artifact freezing and audit,
a telemetry-only vs telemetry+artifacts comparison, decision-quality metrics and M1-v2 (post hoc).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
V1 = os.path.dirname(HERE)
BACKEND = os.path.abspath(os.path.join(V1, "..", ".."))
sys.path.insert(0, BACKEND)


def _load_v1():
    """The v1 harness, unchanged, loaded by path (another module named 'run' may already be imported)."""
    import importlib.util
    if "bench_v1_run" in sys.modules:
        return sys.modules["bench_v1_run"]
    spec = importlib.util.spec_from_file_location("bench_v1_run", os.path.join(V1, "run.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bench_v1_run"] = mod
    spec.loader.exec_module(mod)
    return mod


H = _load_v1()
from app.agent import decision_quality as DQ  # noqa: E402
from app.agent import evidence as EV  # noqa: E402
from app.agent import step_judge as J  # noqa: E402
from app.agent import timeline as T  # noqa: E402

VERSION = "blind-replay-v2"


def load_case(d: str) -> Dict[str, Any]:
    case = H.load_case(d)
    p = os.path.join(d, "artifacts.json")
    case["artifacts"] = json.load(open(p, encoding="utf-8"))["artifacts"] if os.path.exists(p) else []
    return case


def run_veyra(pack: str) -> Dict[str, Any]:
    r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "veyra_blind_v2.py"), pack, BACKEND], capture_output=True, text=True, timeout=300)
    if r.returncode:
        raise RuntimeError(f"Veyra run failed at {pack}: {r.stderr[-2000:]}")
    rd = lambda n: json.load(open(os.path.join(pack, n), encoding="utf-8"))  # noqa: E731
    return {"full": rd("state.json"), "telemetry": rd("state_telemetry.json")}


def withheld_rules(space: Dict[str, Any], cutoff: date) -> List[Dict[str, Any]]:
    out = []
    for h in space["hypotheses"]:
        for k, r in ((h.get("maps") or {}).get("artifacts") or {}).items():
            if isinstance(r, dict) and r.get("available_from") and date.fromisoformat(r["available_from"]) >= cutoff:
                out.append({"hypothesis": h["id"], "rule": k, "available_from": r["available_from"]})
    return out


def artifact_selftest(case: Dict[str, Any], phrases: List[str]) -> List[Dict[str, Any]]:
    cutoff = H.decision_points(case)[0]
    later = cutoff.replace(day=min(cutoff.day + 3, 28)).isoformat()
    base = [a for a in case["artifacts"] if date.fromisoformat(a["available_from"][:10]) < cutoff]
    tests = [
        ("artifact available after the cutoff", {"id": "x1", "kind": "controller_config", "component": "VCU", "available_from": later, "params": {}}),
        ("artifact text names a later date", {"id": "x2", "kind": "software_version", "component": "BMS", "version": "1.9.3", "available_from": "2026-01-01",
                                              "note": f"rolled out fleet-wide on {later}"}),
        ("intervention result before it was known", {"id": "x3", "kind": "intervention", "when": "2026-01-05", "available_from": "2026-01-05",
                                                     "action": "replaced FR inverter", "part": "MCU_FR", "result": "not_the_issue", "result_known": later}),
        ("undated note with an outcome word", {"id": "x4", "kind": "note", "available_from": "2026-01-01", "text": "problem came back after the swap"}),
        ("sealed phrase in an available artifact", {"id": "x5", "kind": "note", "available_from": "2026-01-01", "text": f"check the {phrases[0]}" if phrases else "x"}),
    ]
    out = []
    for name, art in tests:
        arts = base + [art]
        frozen, audit = EV.freeze(arts, cutoff)
        f = next((a for a in frozen if a["id"] == art["id"]), None)
        neutralised = f is None or (art["kind"] == "intervention" and f.get("result") == "pending")
        unfrozen_flag = bool(EV.leak_check(arts, cutoff, phrases))
        pack_flag = bool(EV.leak_check(frozen, cutoff, phrases))
        out.append({"test": name, "neutralised_by_freeze": neutralised, "audit_flags_unfrozen": unfrozen_flag, "audit_flags_pack": pack_flag,
                    "pass": neutralised or pack_flag})
    bad = [{"id": "y", "kind": "software_version", "component": "VCU", "version": "9"}]
    out.append({"test": "artifact without available_from is rejected", "pass": any(i["severity"] == "error" for i in EV.validate(bad))})
    return out


def m1_v2(points: List[Dict[str, Any]], truth: Dict[str, Any], roles: Dict[str, str], key: str, t0: str) -> Any:
    init = truth["main_cause"]
    for i, p in enumerate(points):
        st = p[key]
        if (st["hypotheses"].get(init) or {}).get("status") != "supported":
            continue
        c = DQ.veyra_check(st, roles, init, truth.get("load_only", False))
        if not c or not c.get("simulated") or c["unsafe"]:
            continue
        left = [h for h in st["open"] if h not in c["removed"]]
        reqs = {r["hypothesis"] for r in st["plan"].get("data_requests") or []}
        if all(DQ.role(roles, h) in DQ.TRUE_ROLES or (h in c["unpredicted_open"] and h in reqs) for h in left):
            return {"point": i, "cutoff": p["cutoff"], "days": (date.fromisoformat(p["cutoff"]) - date.fromisoformat(t0)).days}
    return None


def run_case(d: str) -> Dict[str, Any]:
    case = load_case(d)
    issues = H.validate(case) + EV.validate(case["artifacts"])
    if any(i["severity"] == "error" for i in issues):
        return {"case": case["id"], "issues": issues, "status": "invalid"}
    phrases = T.sealed_phrases(json.load(open(os.path.join(d, "truth.sealed.json"), encoding="utf-8")))
    work = tempfile.mkdtemp(prefix="blind2-")
    points = []
    try:
        for cutoff in H.decision_points(case):
            fp = H.freeze_point(case, cutoff, phrases, work)
            arts, aaudit = EV.freeze(case["artifacts"], cutoff)
            aleaks = EV.leak_check(arts, cutoff, phrases)
            json.dump({"artifacts": arts}, open(os.path.join(fp["pack"], "artifacts.json"), "w", encoding="utf-8"), ensure_ascii=False)
            leaks = fp["leaks"] + aleaks
            if leaks:
                points.append({"cutoff": cutoff.isoformat(), "void": True, "leaks": leaks})
                continue
            states = run_veyra(fp["pack"])
            judgments = [J.judge_step(s, states["full"], case["space"], fp["priors"], case["report"]) for s in case["steps"] if s["when"] == cutoff.isoformat()]
            points.append({"cutoff": cutoff.isoformat(), "void": False, "rows": fp["rows"], "artifact_audit": aaudit, "leaks": [],
                           "withheld_rules": withheld_rules(case["space"], cutoff),
                           "prior_visible": [{"id": p["id"], "result": p["result"]} for p in fp["priors"]],
                           "state": states["full"], "state_telemetry": states["telemetry"], "judgments": judgments})
    finally:
        shutil.rmtree(work, ignore_errors=True)
    # ---- the sealed truth is read only from here on
    truth = json.load(open(os.path.join(d, "truth.sealed.json"), encoding="utf-8"))
    roles = truth.get("roles") or {truth["main_cause"]: "initiating", **{c: "contributing" for c in truth.get("contributing") or []}}
    valid = [p for p in points if not p["void"]]
    t0 = valid[0]["cutoff"]
    scores = {}
    for key in ("state_telemetry", "state"):
        pts = [dict(p, state=p[key]) for p in valid]
        for p in pts:
            p["simulation"] = H.simulate(p["state"], truth)
            p["next_check"] = H.next_check_value(p["state"], truth)
            p["judgments"] = [J.judge_step(s, p["state"], case["space"], H.prior_steps(case, date.fromisoformat(p["cutoff"])), case["report"])
                              for s in case["steps"] if s["when"] == p["cutoff"]]
        sc = H.score(case, pts, truth)
        # v2: an artifact fact is cited by its artifact id (v1's check only knew log lines)
        art_ids = {(p["cutoff"], f["id"]) for p in pts for f in p["state"]["facts"] if f.get("artifact")}
        sc["unsupported"] = [u for u in sc["unsupported"] if (u.get("cutoff"), u.get("fact")) not in art_ids]
        sc["M1_v2_post_hoc"] = {"veyra": m1_v2(valid, truth, roles, key, t0), "engineers": sc["M1"]["engineers"]}
        sc["DQ"] = {"veyra_checks": [dict(cutoff=p["cutoff"], **(DQ.veyra_check(p[key], roles, truth["main_cause"], truth.get("load_only", False)) or {"check": None}))
                                     for p in valid],
                    "roles": [dict(cutoff=p["cutoff"], **DQ.state_roles(p[key], roles)) for p in valid]}
        scores["telemetry_only" if key == "state_telemetry" else "telemetry_and_artifacts"] = sc
    eng = DQ.engineer_steps(case["steps"], lambda s: J.targets(s, case["space"]), [h["id"] for h in case["space"]["hypotheses"]], roles)
    return {"case": case["id"], "protocol": "PROTOCOL_v2.md", "version": VERSION, "synthetic": bool(case["report"].get("synthetic")),
            "issues": issues, "truth": {k: truth.get(k) for k in ("main_cause", "contributing", "roles", "confidence", "load_only")},
            "points": points, "void_points": [p["cutoff"] for p in points if p["void"]],
            "scores": scores, "DQ_engineers": eng,
            "leak_selftest": H.leak_selftest(case, phrases), "artifact_leak_selftest": artifact_selftest(case, phrases)}


if __name__ == "__main__":
    dirs = sys.argv[1:] or [os.path.join(HERE, "cases", c) for c in sorted(os.listdir(os.path.join(HERE, "cases"))) if os.path.isdir(os.path.join(HERE, "cases", c))]
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    for d in dirs:
        r = run_case(os.path.abspath(d))
        path = os.path.join(HERE, "results", f"{r['case']}.json")
        json.dump(r, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
        print(path)
