"""Debug replay (app/agent/replay.py): an engineers' walkthrough replayed against the data. Uses the SYNTHETIC shuttle
case and its example walkthrough (not a real service history)."""
import importlib
import json
from pathlib import Path

import pytest

from app.agent import investigator as inv
from app.agent import replay as R
from app.ledger.model import LedgerError
from app.store.sqlite_store import CaseStore
from app.workflows import public_samples

ROOT = Path(__file__).resolve().parents[2]
CASE = ROOT / "fixtures/synthetic/demo/shuttle-hill-start-stop"
LOG = (CASE / "log.txt").read_text().split("\n")
WALK = json.loads((CASE / "walkthrough.json").read_text())["walkthrough"]


def _replay():
    w = json.loads(json.dumps(WALK))
    for i, s in enumerate(w["steps"]):
        s["id"] = f"s{i + 1}"
    return R.replay(LOG, w)


def test_the_inverter_swap_is_replayed_before_and_after():
    s = {x["id"]: x for x in _replay()["steps"]}
    swap = s["s2"]
    assert swap["before"]["verdict"] == "not_separable" and "could not tell" in swap["before"]["text"]
    assert swap["cheaper"]["measurement"] == "segment_r" and swap["cheaper"]["cost_tier"] == 1
    assert swap["after"]["verdict"] == "unchanged" and swap["after"]["values"]["after_ratio"] > 2.5
    # a cheaper discriminating check existed, but the data before it was 12 days old: an estimate, not a verified saving
    assert swap["hindsight"] == "potentially_avoidable" and swap["data_age_days"] == 12
    assert swap["snapshots_before"] == 1 and swap["snapshots_after"] == 2


def test_checks_the_log_could_answer_are_found_with_lines():
    s = {x["id"]: x for x in _replay()["steps"]}
    assert s["s1"]["before"]["verdict"] == "answerable" and "DTC" in s["s1"]["signals"]          # reading codes is not an inverter question
    assert s["s3"]["evidence"] and "SOC" in s["s3"]["signals"]
    assert s["s4"]["signals"][0] == "HSA"
    for e in s["s3"]["evidence"]:
        assert LOG[e["line"] - 1].strip().startswith(e["quote"][:40])


def test_regression_battery_check_is_not_called_avoidable():
    """v1 called s3 (battery SOC and cells) avoidable because the signals were in the log. The battery was a contributing
    factor: the data before it pointed toward the battery, and a later step (s4) found the BMS limit involved."""
    s3 = {x["id"]: x for x in _replay()["steps"]}["s3"]
    assert s3["hindsight"] == "insufficient_evidence"
    assert any("later step (s4)" in w for w in s3["hindsight_why"])
    assert any("pointed toward" in w for w in s3["hindsight_why"])


def test_summary_counts_time_and_money_only_for_verified_avoidable():
    m = _replay()["summary"]
    assert (m["steps"], m["not_the_issue"], m["verified_avoidable"], m["potentially_avoidable"], m["insufficient_evidence"]) == (5, 2, 0, 1, 1)
    assert m["minutes_verified_avoidable"] == 0 and m["cost_verified_avoidable"] == 0
    assert not any(k in m for k in ("avoidable", "cheaper_first", "minutes_avoidable", "cost_avoidable"))
    assert m["signal_additions"][0]["what"].startswith("per-branch DC current") and m["signal_additions"][0]["steps"] == ["s2", "s5"]


def test_without_matching_signals_a_step_says_so():
    micro = (ROOT / "fixtures/real/public/microros-stm32-session-drops/log.txt").read_text().split("\n")
    r = R.replay(micro, {"steps": [{"id": "s1", "check": "Re-crimped the UART connector on the STM32 board", "result": "not_the_issue"},
                                   {"id": "s2", "check": "Checked the agent's session timeout setting", "result": "not_the_issue"},
                                   {"id": "s3", "check": "Asked the supplier for the gearbox lubricant spec", "result": "inconclusive"}]})
    v = [x["before"]["verdict"] for x in r["steps"]]
    assert v[0] == "physical_only" and v[2] == "not_in_data" and r["steps"][2]["needs"]
    assert not r["electrical"]


def test_the_ledger_validates_a_walkthrough(tmp_path):
    store = CaseStore(tmp_path / "w.db")
    try:
        cid = public_samples.run_sample(store, "shuttle-hill-start-stop")["case_id"]
        with pytest.raises(LedgerError):
            store.append(cid, "debug_walkthrough", {"steps": []}, actor="e")
        with pytest.raises(LedgerError):
            store.append(cid, "debug_walkthrough", {"steps": [{"check": "x", "result": "maybe"}]}, actor="e")
        store.append(cid, "debug_walkthrough", WALK, actor="Engineer", source="test")
        rc = R.replay_case(store, cid)
        assert rc["walkthrough"]["verification_grade"] == "engineer_reported_not_independently_reviewed"
        assert rc["replay"]["summary"]["potentially_avoidable"] == 1 and rc["replay"]["summary"]["verified_avoidable"] == 0
        tried = inv._case_context(store, cid)["engineers_already_tried"]
        assert tried[1]["data_after"] == "unchanged" and tried[1]["result"] == "not_the_issue"
        f = R.fleet(store)
        assert f["cases"] == 1 and f["systems"][0]["architecture"] == "modular" and f["systems"][0]["share_verified_avoidable"] == 0.0
    finally:
        store.close()


def test_pasted_text_becomes_draft_steps():
    p = R.parse_text("- 2026-10-21 swapped FR inverter, €1800, 4h -- fault came back\n- 2026年10月29日 FR高压接插件发黑，重新压接，2小时")
    a, b = p["steps"]
    assert (a["kind"], a["result"], a["when"], a["minutes"], a["cost"]) == ("swap", "not_the_issue", "2026-10-21", 240, 1800)
    assert (b["kind"], b["result"], b["when"], b["minutes"]) == ("repair", "fault_found", "2026-10-29", 120)
    assert not b["check"].startswith("日")


def test_api_round_trip(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setenv("VEYRA_ENABLE_RUNTIME", "true")
    monkeypatch.setenv("VEYRA_ENGINE_DB", str(tmp_path / "engine.db"))
    monkeypatch.setenv("VEYRA_PUBLIC_DEMO", "true")
    monkeypatch.setenv("VEYRA_ALLOW_RECORDINGS", "true")
    monkeypatch.delenv("VEYRA_ACCESS_CODE", raising=False)
    monkeypatch.delenv("VEYRA_ENGINE_SEED", raising=False)
    main = importlib.import_module("app.main")
    with TestClient(main.app) as c:
        assert c.post("/engine/walkthrough/parse", json={"text": "- replaced the FR inverter, came back"}).status_code == 200  # open on a read-only demo
        assert c.post("/engine/cases/x/walkthrough", json={"steps": [{"check": "x", "result": "not_the_issue"}]}).status_code == 403
    store = CaseStore(tmp_path / "engine.db")
    try:
        public_samples.seed_showcase(store)
        cid = next(x["id"] for x in store.cases() if "接驳车" in x["title"] or "shuttle" in x["title"].lower())
    finally:
        store.close()
    monkeypatch.setenv("VEYRA_PUBLIC_DEMO", "false")
    with TestClient(main.app) as c:
        got = c.get(f"/engine/cases/{cid}/walkthrough").json()
        assert got["walkthrough"]["source"] == "example" and got["replay"]["summary"]["steps"] == 5
        assert c.get("/engine/feasibility").json()["cases"] == 2   # shuttle (example) and micro-ROS (public thread)
        bad = c.post(f"/engine/cases/{cid}/walkthrough", json={"steps": [{"check": "x", "result": "not_the_issue", "when": "21 Oct"}]})
        assert bad.status_code == 422
        ok = c.post(f"/engine/cases/{cid}/walkthrough", json={"system": "HV", "steps": [{"check": "Replaced the FR inverter", "kind": "swap", "result": "not_the_issue", "when": "2026-10-21", "minutes": 240}]})
        assert ok.status_code == 200 and ok.json()["replay"]["steps"][0]["after"]["verdict"] == "unchanged"


def test_fresh_no_anomaly_is_verified_and_stale_is_not():
    st = {"before": {"verdict": "no_anomaly"}, "data_age_days": 1, "points_toward": []}
    step = {"check": "swap FR inverter", "result": "not_the_issue"}
    assert R._hindsight(st, step, [])[0] == "verified_avoidable"
    assert R._hindsight(dict(st, data_age_days=5), step, [])[0] == "potentially_avoidable"
    later = [{"id": "s9", "check": "FR inverter firmware", "result": "partly"}]
    assert R._hindsight(st, step, later)[0] == "insufficient_evidence"


def test_live_replay_is_consistent_with_the_blind_benchmark():
    """Same synthetic case through the benchmark (frozen protocol v1) and the live replay: the replay never claims more."""
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location("bench_v1_run", ROOT / "backend/research/investigation_benchmark/run.py")
    H = sys.modules.get("bench_v1_run") or importlib.util.module_from_spec(spec)
    if "bench_v1_run" not in sys.modules:
        sys.modules["bench_v1_run"] = H
        spec.loader.exec_module(H)
    b = H.run_case(str(ROOT / "backend/research/investigation_benchmark/cases/shuttle-synthetic"))
    truth_causes = {b["truth"]["main_cause"], *b["truth"]["contributing"]}
    rp = {x["id"]: x for x in _replay()["steps"]}
    verified_b = {v["step"] for v in b["score"]["avoidable"]["verified"]}
    assert {i for i, x in rp.items() if x["hindsight"] == "verified_avoidable"} <= verified_b
    case = H.load_case(str(ROOT / "backend/research/investigation_benchmark/cases/shuttle-synthetic"))
    for st in case["steps"]:
        if set(H.J.targets(st, case["space"])) & truth_causes:
            assert rp[st["id"]]["hindsight"] not in ("verified_avoidable", "potentially_avoidable"), st["id"]
    est_b = {e["step"] for e in b["score"]["avoidable"]["estimated"]}
    assert est_b <= {i for i, x in rp.items() if x["hindsight"] == "potentially_avoidable"}
