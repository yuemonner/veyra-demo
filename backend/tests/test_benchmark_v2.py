"""Blind replay protocol v2: artifact evidence under the leakage rules, causal roles, decision quality; v1 preserved."""
import json
import os
import sys
from datetime import date

from app.agent import decision_quality as DQ
from app.agent import evidence as EV

HERE = os.path.dirname(os.path.abspath(__file__))
B = os.path.join(HERE, "..", "research", "investigation_benchmark")
CASE2 = os.path.join(B, "v2", "cases", "shuttle-synthetic-v2")
def _load(name, path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


R2 = _load("bench_v2_run", os.path.join(B, "v2", "run_v2.py"))


def _arts():
    return json.load(open(os.path.join(CASE2, "artifacts.json")))["artifacts"]


def test_artifacts_enter_only_once_available_and_results_only_once_known():
    kept, audit = EV.freeze(_arts(), date(2026, 10, 22))
    ids = {a["id"] for a in kept}
    assert "cfg-vcu-241" in ids and "iv-mcu-fr" in ids
    assert "iv-fr-conn" not in ids and "bulletin-conn-b" not in ids
    mcu = next(a for a in kept if a["id"] == "iv-mcu-fr")
    assert mcu["result"] == "pending" and mcu["decided_by"] is None          # the recurrence was only known on 10-28
    kept29, _ = EV.freeze(_arts(), date(2026, 10, 29))
    assert next(a for a in kept29 if a["id"] == "iv-mcu-fr")["result"] == "not_the_issue"
    assert EV.leak_check(kept, date(2026, 10, 22)) == []


def test_artifact_without_availability_is_invalid():
    assert any(i["severity"] == "error" for i in EV.validate([{"id": "a", "kind": "software_version"}]))


def test_rules_in_the_space_are_dated_too():
    space = json.load(open(os.path.join(CASE2, "hypotheses.json")))
    facts = EV.assess(EV.freeze(_arts(), date(2026, 10, 29))[0], space, date(2026, 10, 29), date(2026, 10, 20))
    assert "contact" not in facts                                            # the supplier bulletin rule is from 11-02
    assert any(f["effect"] == "supports" and f["tier"] == "confirmed" for f in facts["hsa_control"])


def test_artifacts_never_eliminate():
    state = {"facts": [], "hypotheses": {"x": {"status": "supported", "facts": [], "why": ""}}, "open": ["x"], "weakened": [], "plan": {}}
    out = EV.merge(state, {"x": [{"text": "t", "tier": "inferred", "as_of": "2026-01-01", "artifact": "a", "effect": "argues_against"}]})
    assert out["hypotheses"]["x"]["status"] == "supported"
    out = EV.merge(state, {"x": [{"text": "t", "tier": "confirmed", "as_of": "2026-01-01", "artifact": "a", "effect": "argues_against"}]})
    assert out["hypotheses"]["x"]["status"] == "weakened"                    # weakened, never eliminated


def test_decision_quality_flags_dropping_a_true_cause():
    roles = {"a": "initiating", "b": "contributing", "c": "not_involved"}
    steps = [{"id": "s1", "check": "c", "result": "not_the_issue"}, {"id": "s2", "check": "b", "result": "not_the_issue"}, {"id": "s3", "check": "a", "result": "fault_found"}]
    out = DQ.engineer_steps(steps, lambda s: [s["check"]], ["a", "b", "c"], roles)
    assert [o["unsafe"] for o in out] == [False, True, False]
    assert out[0]["bits"] == round(1.585 - 1.0, 3) and out[2]["open_after"] == 1


def test_v2_run_end_to_end():
    r = R2.run_case(CASE2)
    assert r["void_points"] == []
    assert all(t["pass"] for t in r["leak_selftest"]) and all(t["pass"] for t in r["artifact_leak_selftest"])
    full, tele = r["scores"]["telemetry_and_artifacts"], r["scores"]["telemetry_only"]
    assert full["unsupported"] == [] and tele["unsupported"] == []
    assert all(not c.get("unsafe") for c in full["DQ"]["veyra_checks"])
    assert any(e["unsafe"] for e in r["DQ_engineers"])                      # the battery elimination
    assert full["M1"]["veyra"] is None                                       # the frozen v1 M1 still fails
    for p in r["points"]:
        assert all(w["rule"] == "hardware" for w in p["withheld_rules"])


def test_v1_results_are_preserved():
    H = sys.modules["bench_v1_run"]
    committed = json.load(open(os.path.join(B, "results", "shuttle-synthetic.json")))["score"]
    fresh = H.run_case(os.path.join(B, "cases", "shuttle-synthetic"))["score"]
    for k in ("M1", "M2", "M3", "M4", "M5", "avoidable", "wrong_eliminations", "unsupported", "worse_than_engineers", "better_than_engineers"):
        assert fresh[k] == committed[k], k
