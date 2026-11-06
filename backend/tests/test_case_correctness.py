"""Regression tests for three correctness gaps on the case page, on the synthetic shuttle demo (Chinese) and the real
public micro-ROS case (English):

1. claim provenance: an engineer's report or a consistent observation never becomes a verified cause;
2. one canonical, versioned case state: summary, counts, status and latest discussion agree; history is superseded;
3. safety-aware planning: hazardous checks are marked, need a qualified engineer's review, and safer checks come first;
and whether each engineer input changed the next action, what changed, and what remains.
"""
import importlib.util
import json
import os
import sys

import pytest

from app.agent import case_state as CS
from app.agent import experiment_safety as S
from app.agent import investigator as inv
from app.agent import provenance as prov
from app.store.sqlite_store import CaseStore
from app.workflows import public_samples

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SHUTTLE = "园区接驳车坡道起步急停，遥控器报 FR 电机故障（码 7）"


@pytest.fixture(scope="module")
def showcase(tmp_path_factory):
    store = CaseStore(tmp_path_factory.mktemp("cc") / "c.db")
    ids = public_samples.seed_showcase(store)
    by = {store.case(i)["title"]: i for i in ids}
    yield store, by
    store.close()


def _id(by, prefix):
    return next(v for k, v in by.items() if k.startswith(prefix))


# ---------------------------------------------------------------- 1. provenance
def test_walkthrough_conclusion_is_reported_not_established(showcase):
    store, by = showcase
    s = CS.build(store, by[SHUTTLE])
    final = [c for c in s["provenance"]["engineer_claims"] if c["source"] == "walkthrough_final_cause"]
    assert final and final[0]["provenance"] == "engineer_reported"
    assert any(f["source"] == "walkthrough_final_cause" for f in s["provenance"]["flags"])
    assert s["provenance"]["verified_outcomes"] == [] and s["current"]["status"] != "cause_verified"


def test_a_leading_explanation_resting_on_an_engineer_report_says_so(showcase):
    store, by = showcase
    hs = {h["id"]: h for h in CS.build(store, by[SHUTTLE])["current"]["hypotheses"]}
    # h3 (FR terminal) became leading in round 4, on the engineer's bench measurement, which the log cannot show
    assert hs["h3"]["status"] == "leading" and hs["h3"]["status_basis"] == "engineer_reported"
    assert hs["h3"]["standing"] == "leading_not_verified"
    assert all(h["standing"] not in ("verified_cause", "mechanism_reproduced") for h in hs.values())


def test_engineer_confirmation_without_data_is_not_verified(showcase, tmp_path):
    store = CaseStore(tmp_path / "v.db")
    try:
        cid = public_samples.run_sample(store, "shuttle-hill-start-stop")["case_id"]
        store.append(cid, "reproduction_attempt", {"run_id": store.latest_run(cid)["id"], "environment": "field", "platform": "SHUTTLE-03",
                                                   "setup": "cold launch, HSA on", "result": "reproduced", "observations": "code 7 again",
                                                   "performed_at": "2026-11-05T07:40:00+00:00"}, actor="Engineer", source="test")
        store.append(cid, "case_closure", {"investigation_status": "cause_confirmed", "cause_domain": "vehicle_body", "cause": "FR terminal discoloured",
                                           "confirmed_by": "inspection", "evidence_note": "Technician saw a discoloured terminal",
                                           "resolution_status": "resolved", "fix": "Re-crimped", "fix_held": "held"}, actor="Engineer", source="test")
        lad = inv.verification_ladder(store, cid)
        assert lad["level"] == "reproduced"                                     # not 'verified'
        assert lad["rungs"][1]["reached"]["provenance"] == "engineer_reported"   # no data attached to the reproduction
        assert lad["engineer_reported_confirmation"]["method"] == "inspection"
        s = CS.build(store, cid)
        assert s["provenance"]["verified_outcomes"] == [] and s["provenance"]["closure"]["verified"] is False
        assert any(f["source"] == "case_closure" for f in s["provenance"]["flags"])
    finally:
        store.close()


def test_verified_outcomes_need_records_with_data():
    led = [{"id": "r1", "kind": "reproduction_attempt", "recorded_at": "1", "payload": {"result": "reproduced", "evidence": [], "environment": "bench"}},
           {"id": "r2", "kind": "reproduction_attempt", "recorded_at": "2", "payload": {"result": "reproduced", "evidence": ["imp-1"], "environment": "bench"}},
           {"id": "o1", "kind": "outcome", "recorded_at": "3", "payload": {"evidence": ["imp-2"]}},
           {"id": "c1", "kind": "case_closure", "recorded_at": "4", "payload": {"fix_held": "held", "evidence_entries": []}},
           {"id": "c2", "kind": "case_closure", "recorded_at": "5", "payload": {"fix_held": "held", "evidence_entries": ["o1"]}}]
    v = prov.verified_outcomes(led)
    assert [(x["kind"], x["entry"]) for x in v] == [("mechanism_reproduced", "r2"), ("fix_verified_by_data", "c2")]
    h = {"id": "h1", "area": "x", "status": "leading"}
    assert prov.hypothesis_standing(h, basis="telemetry_observation", verified=[])["standing"] == "leading_not_verified"
    assert prov.hypothesis_standing(h, basis="engineer_reported", verified=[{**v[0], "hypotheses": ["h1"]}])["standing"] == "mechanism_reproduced"


# ---------------------------------------------------------------- 2. canonical state
def test_one_state_for_summary_counts_and_latest_discussion(showcase):
    store, by = showcase
    from app.agent import discussion
    cid = by[SHUTTLE]
    s = CS.build(store, cid)
    cur = s["current"]
    assert cur["from"]["source"] == "discussion" and cur["from"]["n"] == 5 and s["latest_discussion"]["is_current"]
    ids_now = [(h["id"], h["status"]) for h in discussion.current_hypotheses(store, cid)]
    assert [(h["id"], h["status"]) for h in cur["hypotheses"]] == ids_now        # the discussion panel and the summary agree
    n = cur["counts"]
    assert n["total"] == len(cur["hypotheses"]) and n["standing"] == n["leading"] + n["open"] == 3
    # 'still possible' counts as the fault-space panel does: everything not ruled out (weakened is not excluded)
    assert n["possible"] == len([h for h in cur["hypotheses"] if h["status"] != "ruled_out"]) == 5
    assert cur["next_action"]["action"] == discussion.rounds_of(store, cid)[-1]["result"]["next_check"]["action"]
    # the investigation round is history, superseded, never current
    assert s["history"][0]["source"] == "investigation" and s["history"][0]["superseded_by"]["source"] == "discussion"
    assert all(h["entry"] != cur["from"]["entry"] for h in s["history"])


def test_state_version_moves_with_the_record(tmp_path):
    store = CaseStore(tmp_path / "s.db")
    try:
        cid = public_samples.seed_showcase(store)[0]
        a = CS.build(store, cid)
        store.append(cid, "engineer_finding", {"action": "Checked the cable", "result": "not_found", "observations": "nothing loose"}, actor="Engineer", source="test")
        b = CS.build(store, cid)
        assert b["state_version"]["n"] == a["state_version"]["n"] + 1 and b["state_version"]["id"] != a["state_version"]["id"]
        assert b["engineer_inputs"][-1]["verdict"] == "not_yet_assessed"     # recorded, not yet answered by an assessment
        assert b["current"] == a["current"]                                   # an engineer input alone does not change the findings
    finally:
        store.close()


def test_micro_ros_probable_conclusion_is_history_once_superseded(showcase):
    store, by = showcase
    s = CS.build(store, _id(by, "micro-ROS"))
    inv2 = [h for h in s["history"] if h["source"] == "investigation" and h["n"] == 2][0]
    assert inv2["status"] == "cause_probable" and inv2["superseded_by"]["source"] == "discussion"
    assert s["current"]["from"]["source"] == "discussion" and s["current"]["status"] == "cause_unknown"   # h6 is open again


# ---------------------------------------------------------------- 3. safety
def test_every_shuttle_field_check_is_hazardous_and_needs_review(showcase):
    store, by = showcase
    from app.agent import discussion
    for r in discussion.rounds_of(store, by[SHUTTLE]):
        sf = S.classify(r["result"]["next_check"], hv_system=True)
        assert sf["hazardous"] and sf["requires_review"] and sf["approved"] is False and sf["review_by"]
        assert {"machine_operation", "fault_reproduction"} <= set(sf["classes"])
    first = S.classify(discussion.rounds_of(store, by[SHUTTLE])[0]["result"]["next_check"], hv_system=True)
    assert "live_hv" in first["classes"]                                     # a multimeter on the inverter terminals during a launch


def test_micro_ros_bench_checks_are_not_flagged(showcase):
    store, by = showcase
    s = CS.build(store, _id(by, "micro-ROS"))
    assert s["safety"]["high_voltage_system"] is False
    assert not s["current"]["next_action"]["safety"]["hazardous"]
    assert all(x.get("after", {}).get("hazard") in (None, "low_risk_physical", "bench", "offline") for x in s["engineer_inputs"])


def test_high_voltage_threshold_and_plan_order():
    assert S.has_hv(74.4) and not S.has_hv(48.0) and not S.has_hv(None, "STM32 board on USB")
    sys.path.insert(0, ROOT + "/backend")
    from datetime import date
    from app.agent import timeline as T
    from app.physics import electrical as E
    lines = open(os.path.join(ROOT, "backend/research/investigation_benchmark/cases/shuttle-synthetic/log.txt"), encoding="utf-8").read().split("\n")
    r = S.annotate_electrical(E.analyse(T.freeze(lines, date(2026, 10, 20))[0]), hv_system=True)
    a = r["assessments"][0]
    m = {x["id"]: x["safety"] for x in a["measurements"]}
    assert m["t_inv_trend"]["worst"] == "offline" and m["segment_r"]["worst"] == "hv_deenergised" and m["segment_r"]["requires_review"]
    assert a["safety_plan"]["recommended"] == "t_inv_trend" and a["safety_plan"]["then"] == "segment_r" and a["safety_plan"]["then_requires_review"]
    assert a["recommended"] == "segment_r"                                   # the electrical module's own (validated) choice is unchanged


def test_the_verifier_rejects_a_hazardous_check_without_approval():
    raw = ["2026-10-28T07:42:00.000 BMS_Status Pack_V=74.4 Pack_I=4.0"]
    bad = {"next_action": {"action": "Cold ramp launch with HSA on and off, record the bus", "where": "on_site", "needs_approval": False}}
    assert inv.check_safety(bad, raw)
    assert not inv.check_safety({"next_action": {**bad["next_action"], "needs_approval": True}}, raw)


# ---------------------------------------------------------------- 4. does each engineer input change the next action
def test_shuttle_inputs_changed_the_action_only_when_the_evidence_moved(showcase):
    store, by = showcase
    x = {i.get("round"): i for i in CS.build(store, by[SHUTTLE])["engineer_inputs"]}
    assert x[4]["kind"] == "observation" and x[4]["verdict"] == "action_changed_with_evidence" and x[4]["genuine_change"]
    assert any(m["id"] == "h3" and m["from"] == "open" and m["to"] == "leading" for m in x[4]["moved"]) and x[4]["rests_on_engineer_report"]
    assert x[3]["verdict"] == "explanations_moved_action_unchanged" and x[3]["action_similarity"] >= 0.75   # same check, reworded
    assert x[5]["verdict"] == "action_changed_without_evidence" and not x[5]["genuine_change"]
    assert {r["id"] for r in x[5]["remaining"]} == {"h1", "h2", "h3"}


def test_micro_ros_inputs(showcase):
    store, by = showcase
    ins = CS.build(store, _id(by, "micro-ROS"))["engineer_inputs"]
    finding = [i for i in ins if i["source"] == "engineer_finding"][0]
    assert finding["verdict"] == "action_changed_with_evidence"
    assert {(m["id"], m["from"], m["to"]) for m in finding["moved"]} == {("h2", "open", "leading"), ("h1", "leading", "weakened")}
    switch = [i for i in ins if i.get("round") == 3][0]
    assert switch["kind"] == "claim" and switch["verdict"] == "action_changed_without_evidence"


def test_benchmark_observation_impact_changes_once():
    spec = importlib.util.spec_from_file_location("bench_obs_impact", os.path.join(ROOT, "backend/research/investigation_benchmark/v2/observation_impact.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    p = os.path.join(ROOT, "backend/research/investigation_benchmark/v2/results/shuttle-synthetic-v2.json")
    o = m.run(p, os.path.join(ROOT, "backend/research/investigation_benchmark/v2/cases/shuttle-synthetic-v2"))
    changed = [x for x in o["points"] if x["action_changed"]]
    assert [x["cutoff"] for x in changed] == ["2026-10-29"]
    assert changed[0]["plan_without_them"]["recommended"] == "t_inv_trend" and changed[0]["plan_with_them"]["recommended"] == "segment_r"
    assert changed[0]["plan_with_them"]["requires_review"]


# ---------------------------------------------------------------- the synthetic demonstration is preserved
def test_demo_recordings_are_unchanged_and_still_seed(showcase):
    store, by = showcase
    from app.agent import discussion
    assert len(discussion.rounds_of(store, by[SHUTTLE])) == 5 and len(discussion.rounds_of(store, _id(by, "micro-ROS"))) == 4
    d = json.load(open(os.path.join(ROOT, "fixtures/synthetic/demo/shuttle-hill-start-stop/recorded_discussion.v4.json"), encoding="utf-8"))
    assert "safety" not in json.dumps(d)                                      # safety is computed when read, never written into recordings


def test_state_api(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    monkeypatch.setenv("VEYRA_ENABLE_RUNTIME", "true")
    monkeypatch.setenv("VEYRA_ENGINE_DB", str(tmp_path / "engine.db"))
    monkeypatch.setenv("VEYRA_PUBLIC_DEMO", "false")
    monkeypatch.setenv("VEYRA_ALLOW_RECORDINGS", "true")
    monkeypatch.delenv("VEYRA_ACCESS_CODE", raising=False)
    monkeypatch.delenv("VEYRA_ENGINE_SEED", raising=False)
    main = importlib.import_module("app.main")
    store = CaseStore(tmp_path / "engine.db")
    try:
        public_samples.seed_showcase(store)
        cid = next(x["id"] for x in store.cases() if "接驳车" in x["title"])
    finally:
        store.close()
    with TestClient(main.app) as c:
        st = c.get(f"/engine/cases/{cid}/state").json()
        assert st["current"]["from"]["source"] == "discussion" and st["current"]["next_action"]["safety"]["requires_review"]
        d = c.get(f"/engine/cases/{cid}/discussion").json()
        assert d["rounds"][-1]["result"]["next_check"]["safety"]["hazardous"]
        i = c.get(f"/engine/cases/{cid}/investigation").json()
        assert i["playback"]["investigations"][-1]["result"]["next_action"]["safety"]["hazardous"]
        el = c.get(f"/engine/cases/{cid}/electrical").json()
        assert el["assessments"][0]["safety_plan"]["then"] == "segment_r"
