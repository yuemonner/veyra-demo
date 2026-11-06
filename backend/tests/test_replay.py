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
    assert swap["hindsight"] == "cheaper_first" and swap["snapshots_before"] == 1 and swap["snapshots_after"] == 2


def test_checks_the_log_could_answer_are_found_with_lines():
    s = {x["id"]: x for x in _replay()["steps"]}
    assert s["s1"]["before"]["verdict"] == "answerable" and "DTC" in s["s1"]["signals"]          # reading codes is not an inverter question
    assert s["s3"]["hindsight"] == "avoidable" and s["s3"]["evidence"] and "SOC" in s["s3"]["signals"]
    assert s["s4"]["signals"][0] == "HSA"
    for e in s["s3"]["evidence"]:
        assert LOG[e["line"] - 1].strip().startswith(e["quote"][:40])


def test_summary_counts_time_and_money_and_the_signal_to_add():
    m = _replay()["summary"]
    assert (m["steps"], m["not_the_issue"], m["avoidable"], m["cheaper_first"]) == (5, 2, 1, 1)
    assert m["minutes_avoidable"] == 285 and m["cost_avoidable"] == 1800
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
        assert rc["replay"]["summary"]["cheaper_first"] == 1
        tried = inv._case_context(store, cid)["engineers_already_tried"]
        assert tried[1]["data_after"] == "unchanged" and tried[1]["result"] == "not_the_issue"
        f = R.fleet(store)
        assert f["cases"] == 1 and f["systems"][0]["architecture"] == "modular" and f["systems"][0]["share_wasted_avoidable"] == 1.0
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
        assert c.get("/engine/feasibility").json()["cases"] == 1
        bad = c.post(f"/engine/cases/{cid}/walkthrough", json={"steps": [{"check": "x", "result": "not_the_issue", "when": "21 Oct"}]})
        assert bad.status_code == 422
        ok = c.post(f"/engine/cases/{cid}/walkthrough", json={"system": "HV", "steps": [{"check": "Replaced the FR inverter", "kind": "swap", "result": "not_the_issue", "when": "2026-10-21", "minutes": 240}]})
        assert ok.status_code == 200 and ok.json()["replay"]["steps"][0]["after"]["verdict"] == "unchanged"
