"""Ingestion -> automatic scenario scan -> verified knowledge reused, through the real upload endpoint and the local
engineering API. Every recording here is SYNTHETIC (generated ROS files); no private data is read."""
import importlib.util
import os
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import physchecks_api as api
from app import recordings_api
from app.agent.llm import ScriptedModel
from app.physchecks import ingested, investigation as INV

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("make_syn", ROOT / "scripts" / "make_synthetic_bev_recording.py")
make_syn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_syn)
H = {"x-veyra-actor": "Engineer A"}
H2 = {"x-veyra-actor": "Engineer B"}


def _tool(i, name, **inp):
    return {"type": "tool_use", "id": f"t{i}", "name": name, "input": inp}


def scripted():
    """A stand-in model that investigates the way the real one is asked to: checks, hypotheses, earlier knowledge,
    cross-run results, next checks, then a report with one acceptable and one unacceptable statement."""
    return ScriptedModel([
        {"content": [_tool(1, "checks_overview"), _tool(2, "hypotheses")]},
        {"content": [_tool(3, "earlier_knowledge"), _tool(4, "scenario_results")]},
        {"content": [_tool(5, "next_checks")]},
        {"content": [_tool(6, "submit_report", summary="SOC was corrected without measured charge; the recorded data does not separate A, B and C.",
                           hypotheses=[{"id": "A", "status": "open", "statement": "SOC estimation drift remains open; an earlier verified case settled the same finding with the BMS history, so that check comes first."},
                                       {"id": "F", "status": "excluded", "statement": "A contactor fault is excluded."}],
                           next_check="DOC-BMS-HISTORY")]},
    ])


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("VEYRA_PRIVATE_CASES_DIR", str(tmp_path / "private"))
    monkeypatch.setenv("VEYRA_ENGINEERING_LOCAL", "true")
    monkeypatch.setenv("VEYRA_ENABLE_RUNTIME", "true")
    monkeypatch.setenv("VEYRA_ALLOW_RECORDINGS", "true")
    monkeypatch.setenv("VEYRA_ENGINE_DB", str(tmp_path / "store" / "engine.db"))
    monkeypatch.delenv("VEYRA_PRIVATE_ALLOW_AGENT", raising=False)
    monkeypatch.setattr(INV, "MODEL_FACTORY", scripted)
    api.reset_for_tests(tmp_path / "eng.db")
    app = FastAPI()
    app.include_router(api.router)
    app.include_router(recordings_api.router)
    files = {
        "correction": make_syn.write("correction", "ros1", tmp_path / "SYNTHETIC-bev-correction.bag"),
        "healthy": make_syn.write("healthy", "ros2", tmp_path / "SYNTHETIC-bev-healthy.zip"),
    }
    yield TestClient(app), files
    api.reset_for_tests(None)


def upload(client, path: Path) -> str:
    r = client.post(f"/engine/recordings?filename={path.name}", content=path.read_bytes())
    assert r.status_code == 200, r.text
    assert r.json()["engineering_auto_scan"] == "started"
    return r.json()["id"]


def wait_scan(client, rid: str, timeout: float = 60.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        row = next((x for x in client.get("/engineering/recordings").json()["recordings"] if x["id"] == rid), None)
        if row and row["auto_scan"]:
            return row
        time.sleep(0.2)
    raise AssertionError(f"no automatic scan for {rid}")


def wait_investigation(client, case_id: str, timeout: float = 60.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = client.get(f"/engineering/cases/{case_id}/investigation").json()
        if st["latest"] and not st["running"]:
            return st
        time.sleep(0.2)
    raise AssertionError(f"no stored investigation for {case_id}")


def test_profile_matching_is_explicit_and_never_guesses(env):
    client, files = env
    rid = upload(client, files["correction"])
    row = wait_scan(client, rid)
    assert row["profile"]["profile"] == "synthetic-bev-ros" and row["profile"]["synthetic"] is True
    verdicts = {p["profile"]: p["status"] for p in row["profiles"]}
    assert verdicts["px4-battery-status"] == "incompatible"
    assert verdicts["ros-battery-state"] == "compatible"          # considered, but the more specific profile is chosen
    # the stored file is read where the recordings API put it, with row references into it
    rec, prof, _ = ingested.load(rid)
    ref = rec.ref("recording", prof.role("soc")["field"], 0).as_dict()
    assert ref["file"] == "SYNTHETIC-bev-correction.bag" and ref["source"] == "/synthetic_bev/battery" and ref["row"] == 1


def test_upload_without_approved_scenarios_records_that_nothing_ran(env):
    client, files = env
    rid = upload(client, files["healthy"])
    row = wait_scan(client, rid)
    assert row["auto_scan"]["state"] == "no_approved_scenarios" and row["auto_scan"]["profile"] == "synthetic-bev-ros"
    # it is still investigated on arrival (nothing to compare with yet, which the record shows)
    inv = wait_investigation(client, rid)["latest"]
    assert inv["agent"]["mode"] == "agent" and inv["knowledge_used"] == [] and inv["scenario_results"]["here"] == []


def test_without_a_model_the_investigation_says_so_and_invents_nothing(env, monkeypatch):
    client, files = env
    monkeypatch.setattr(INV, "MODEL_FACTORY", None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.delenv("VEYRA_AGENT_BACKEND", raising=False)
    rid = upload(client, files["healthy"])
    inv = wait_investigation(client, rid)["latest"]
    assert inv["agent"]["mode"] == "unavailable" and not inv["agent"].get("constrained_claims")
    assert inv["deterministic"]["checks"]["charge_accounting"]["outcome"] == "PASS"


def test_full_loop_ingest_scan_verify_and_reuse(env):
    client, files = env
    case = api.SYNTHETIC_ID
    # 1. a recording already stored before the scenario exists
    old = upload(client, files["healthy"])
    wait_scan(client, old)
    # 2. finding -> scenario (author A) -> approval by B -> back-fill on stored recordings
    sid = client.post("/engineering/scenarios", json={"case_id": case, "title": "Abrupt SOC correction not explained by measured charge",
                                                      "applicability": {"required_roles": ["pack_current", "soc"], "machine_class": "battery_electric_vehicle"}},
                      headers=H).json()["id"]
    r = client.post(f"/engineering/scenarios/{sid}/review", json={"decision": "approve", "notes": "rows and tolerance checked"}, headers=H2)
    assert r.status_code == 200 and r.json()["backfill"]
    t0 = time.time()
    while time.time() - t0 < 60 and not ingested.results_for(api.ledger(), old):
        time.sleep(0.2)
    back = ingested.results_for(api.ledger(), old)
    assert back and back[0]["outcome"] == "PASS" and back[0]["trigger"] == "approval-backfill"
    # 3. engineer-verified outcome on the original (SYNTHETIC) case
    post = lambda kind, data: client.post(f"/engineering/cases/{case}/outcomes", json={"kind": kind, "data": data}, headers=H)
    opened = post("open", {}).json()["record"]["payload"]
    assert opened["context"]["machine_class"] == "battery_electric_vehicle" and opened["context"]["synthetic"] is True
    post("propose", {"hypothesis_id": "A", "statement": "SOC estimate drifted high", "origin": "deterministic"})
    pid = post("performed", {"template_id": "DOC-BMS-HISTORY", "method": "SYNTHETIC: history export", "performed_by": "A", "qualification": "none"}).json()["record"]["id"]
    oid = post("observe", {"performed_id": pid, "summary": "SYNTHETIC: no full calibration for 40 days", "measurements": {}, "supports": ["A"], "contradicts": []}).json()["record"]["id"]
    assert post("verify", {"hypothesis_id": "A", "result": "confirmed", "observation_ids": [oid], "basis": "SYNTHETIC: history"}).status_code == 200
    # 4. a NEW recording arrives: the approved scenario runs automatically
    new = upload(client, files["correction"])
    row = wait_scan(client, new)
    assert row["auto_scan"]["state"] == "scanned"
    res = row["auto_scan"]["results"][0]
    assert res["scenario_id"] == sid and res["outcome"] == "VIOLATION" and res["matched_original"] is True
    assert any(x["trigger"] == "ingest" for x in row["results"])
    # 5. the new recording's investigation shows the earlier verified result, says why it applies, and uses it
    rep = client.get(f"/engineering/cases/{new}/replay").json()
    assert rep["case"]["synthetic"] is True
    k = rep["sections"][2]["knowledge"]
    item = next(i for i in k["items"] if i["case_id"] == case and i["hypothesis_id"] == "A")
    assert item["applicability"] == "applies" and item["settled_by"] == ["DOC-BMS-HISTORY"]
    assert "battery_electric_vehicle" in " ".join(item["reasons"])
    a_here = next(h for h in rep["sections"][2]["assessment"]["hypotheses"] if h["id"] == "A")
    assert a_here["status"] == "open"                              # verified elsewhere never confirms it here
    plan = rep["sections"][3]["plans"]["none"]
    assert plan["recommended"][0]["id"] == "DOC-BMS-HISTORY" and plan["recommended"][0]["prior"][0]["case_id"] == case
    assert plan["ranking_rule"].startswith("a check that settled the same finding")
    # 6. the upload also started an investigation on its own: the AI investigator used the physical checks, the earlier
    #    verified knowledge and the cross-run scenario results; its statements were held to the checks
    st = wait_investigation(client, new)
    inv = st["latest"]
    assert inv["trigger"] == "ingest" and inv["agent"]["mode"] == "agent"
    tools = [t["tool"] for t in inv["agent"]["trace"]]
    assert {"checks_overview", "hypotheses", "earlier_knowledge", "scenario_results", "next_checks"} <= set(tools)
    assert inv["knowledge_used"][0]["case_id"] == case and inv["deterministic"]["next_check"]["id"] == "DOC-BMS-HISTORY"
    assert any(x["recording"] == old and x["outcome"] == "PASS" for x in inv["scenario_results"]["other_recordings"])
    verdicts = {c.get("hypothesis"): c["verdict"] for c in inv["agent"]["constrained_claims"]}
    assert verdicts["A"] == "accepted" and verdicts["F"] == "rejected"
    rep = client.get(f"/engineering/cases/{new}/replay").json()
    assert rep["agent"]["mode"] == "agent" and rep["agent"]["investigation_id"] == inv["id"]
    # 7. the engineer adopts the agent's statement about A (opening the case on the way); the rejected one cannot be adopted
    claims = inv["agent"]["constrained_claims"]
    ia = next(i for i, c in enumerate(claims) if c.get("hypothesis") == "A")
    iff = next(i for i, c in enumerate(claims) if c.get("hypothesis") == "F")
    assert client.post(f"/engineering/cases/{new}/investigation/adopt", json={"investigation_id": inv["id"], "claim_index": iff}, headers=H).status_code == 400
    r = client.post(f"/engineering/cases/{new}/investigation/adopt", json={"investigation_id": inv["id"], "claim_index": ia}, headers=H)
    assert r.status_code == 200, r.text
    hyp = r.json()["state"]["hypotheses"]["A"]
    assert hyp["statements"][-1]["origin"] == "agent" and hyp["stage"] != "verified_root_cause"   # adopted is a proposal, not a result
    assert r.json()["state"]["signature"]


def test_synthetic_knowledge_never_applies_to_real_data():
    from app.physchecks import knowledge
    from app.physchecks.store import Ledger
    from app.physchecks import synthetic as S, base, hypotheses, facts, outcomes as O
    import tempfile
    led = Ledger(Path(tempfile.mkdtemp()) / "k.db")
    rec, prof = S.incident("syn-k"), S.profile()
    checks = base.run_all(rec, prof)
    O.open_case(led, "SYN-K", title="t", signature=O.signature_of(checks), actor="A", confidential=False,
                context=knowledge.context_of(checks, prof, True))
    O.propose(led, "SYN-K", hypothesis_id="A", statement="s", origin="engineer", actor="A", actor_kind="engineer")
    p = O.performed(led, "SYN-K", template_id="DOC-BMS-HISTORY", method="m", performed_by="A", qualification="none", actor="A", actor_kind="engineer")
    o = O.observe(led, "SYN-K", performed_id=p["id"], summary="s", measurements={}, supports=["A"], contradicts=[], actor="A", actor_kind="engineer")
    O.verify(led, "SYN-K", hypothesis_id="A", result="confirmed", observation_ids=[o["id"]], basis="b", actor="A", actor_kind="engineer")
    A = hypotheses.assess(checks, facts.derive(rec, prof, checks), [], machine=hypotheses.machine_of(prof))
    k = knowledge.for_case(led, "REAL-1", checks, A, prof, synthetic=False)       # same data, but treated as a real recording
    item = next(i for i in k["items"] if i["hypothesis_id"] == "A")
    assert item["applicability"] == "does not apply" and "SYNTHETIC" in " ".join(item["reasons"])
    assert knowledge.prior_for_planner(k) == {}


PX4 = ROOT.parent / "datasets" / "uav-sead" / "ulg" / "2021-10-06--_11_54_32.ulg"


@pytest.mark.skipif(not PX4.is_file(), reason="public PX4 log not present on this machine")
def test_public_px4_log_reads_through_the_same_path_and_bev_scenarios_do_not_apply(env):
    from app.physchecks import scenarios as SC
    client, _ = env
    rid = upload(client, PX4)
    row = wait_scan(client, rid)
    assert row["profile"]["profile"] == "px4-battery-status" and row["profile"]["synthetic"] is False
    rec, prof, _ = ingested.load(rid)
    assert prof.parameter("capacity_ah")[0] == pytest.approx(16.0)
    inv = wait_investigation(client, rid)["latest"]     # not SYNTHETIC: the AI investigator is not run without the owner's permission
    assert inv["agent"]["mode"] == "blocked" and inv["deterministic"]["checks"]
    s = {"id": "x", "version": 1, "title": "t", "status": "approved", "check_id": "charge_accounting", "check_version": "charge-accounting-v1",
         "params": {}, "applicability": {"required_roles": ["pack_current", "soc"], "machine_class": "battery_electric_vehicle"}}
    assert SC.run(s, rec, prof)["outcome"] == "NOT_APPLICABLE"


def test_ledger_survives_concurrent_reads_and_writes(tmp_path):
    """Request threads read while background scans and investigations write, on one shared connection."""
    import threading
    from app.physchecks.store import Ledger
    led, errors = Ledger(tmp_path / "c.db"), []

    def writer(n):
        try:
            for i in range(60):
                led.append("ingest_scan", f"recording:r{n}", {"i": i}, actor="t", actor_kind="system")
        except Exception as e:
            errors.append(e)

    def reader():
        try:
            for _ in range(200):
                led.records(kinds=["ingest_scan"]); led.current(subject="recording:r0")
        except Exception as e:
            errors.append(e)
    ts = [threading.Thread(target=writer, args=(n,)) for n in range(3)] + [threading.Thread(target=reader) for _ in range(4)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert not errors and len(led.records(kinds=["ingest_scan"])) == 180 and led.verify_chain()["ok"]
