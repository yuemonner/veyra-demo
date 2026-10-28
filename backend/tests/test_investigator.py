"""Autonomous investigation (app/agent/investigator.py) on REAL public logs (fixtures/real/public): Veyra runs the checks,
verifies every quote and number, hands over one next action, re-investigates with the engineer's result, and drafts the
lesson. No model is called here: the agent path uses a scripted stand-in (never shown as AI output)."""
import json
import time

import pytest

from app.agent import investigator as inv
from app.agent.llm import ScriptedModel
from app.store.sqlite_store import CaseStore
from app.workflows import public_samples


@pytest.fixture()
def store(tmp_path):
    s = CaseStore(tmp_path / "w.db")
    yield s
    s.close()


def _scripted(responses):
    m = ScriptedModel(responses)
    m.provider = "anthropic"
    return m


def test_samples_are_real_and_attributed():
    names = {x["name"]: x for x in public_samples.listing()}
    assert len(names) >= 3
    for x in names.values():
        assert x["source_url"].startswith("https://") and "synthetic" not in x["title"].lower()


def test_original_lines_and_values_are_kept(store):
    out = public_samples.run_sample(store, "rplidar-no-scans")
    tools = inv.LogTools(store, out["case_id"])
    raw = (public_samples.SAMPLES["rplidar-no-scans"]["dir"] / "log.txt").read_text().splitlines()
    assert tools.raw == raw                                    # original text, original line numbers, numbers not replaced
    hit = tools.search("timeout|timed out|error", context=1)
    assert all(raw[x["line"] - 1].startswith(x["text"][:50]) for x in hit["lines"])
    m = tools.measure("rplidar_node")
    assert m["count"] == sum("rplidar_node" in l for l in raw)


def test_without_a_model_the_reading_is_labelled_rule_based(store):
    out = public_samples.run_sample(store, "ardupilot-sitl-startup-crash")
    r = inv.investigate(store, out["case_id"], model=None)
    assert r["mode"] == "deterministic" and "No model" in r["fallback_reason"]
    assert r["result"]["next_action"]["action"] and inv.latest(store, out["case_id"])["mode"] == "deterministic"


def test_agent_runs_checks_and_unverifiable_claims_are_returned_then_dropped(store):
    out = public_samples.run_sample(store, "rplidar-no-scans")
    cid = out["case_id"]
    store.append(cid, "model_authorization", {"provider": "anthropic", "scope": "test", "authorized_by": "t", "evidence": "test"}, actor="t")
    raw = inv.LogTools(store, cid).raw
    n = next(i for i, l in enumerate(raw, 1) if l.strip())
    good = {"headline": "The driver never produced a scan", "status": "cause_unknown", "confidence": "low", "explanation": "",
            "evidence": [{"line": n, "quote": raw[n - 1][:40], "shows": "first line", "calls": ["a1"]}],
            "checked": [{"check": "Searched for errors", "result": "see tool", "verdict": "inconclusive", "calls": ["a2"]}],
            "next_action": {"action": "Read the lidar's health status", "why": "separates device from driver", "how": ["ros2 service call ..."],
                            "where": "remote", "outcomes": [{"label": "Health error", "means": "device side"}, {"label": "Healthy", "means": "driver side"}]},
            "unknowns": ["x"]}
    bad = json.loads(json.dumps(good))
    bad["evidence"].append({"line": n, "quote": "this text is not in the log", "shows": "invented", "calls": ["a1"]})
    bad["checked"].append({"check": "Counted", "result": "4242 scans dropped", "verdict": "supports", "calls": ["a2"]})
    model = _scripted([
        {"content": [{"type": "tool_use", "id": "a1", "name": "overview", "input": {}}, {"type": "tool_use", "id": "a2", "name": "search", "input": {"pattern": "error|fail"}}]},
        {"content": [{"type": "tool_use", "id": "a3", "name": "submit_report", "input": bad}]},
        {"content": [{"type": "tool_use", "id": "a4", "name": "submit_report", "input": good}]},
    ])
    r = inv.investigate(store, cid, model=model)
    assert r["mode"] == "agent" and [s["tool"] for s in r["steps"]] == ["overview", "search"]
    returned = model.calls[2]["messages"][-1]["content"][0]["content"]
    assert "not in line" in returned and "4242" in returned           # Veyra sent the bad claims back
    assert r["result"]["evidence"] == good["evidence"] and not r["dropped"]


def test_no_permission_no_model_call(store):
    out = public_samples.run_sample(store, "rplidar-no-scans")
    model = _scripted([])
    r = inv.investigate(store, out["case_id"], model=model)
    assert r["mode"] == "deterministic" and "permission" in r["fallback_reason"] and model.calls == []


def test_engineer_result_feeds_the_next_step_and_the_lesson_is_drafted(store):
    out = public_samples.run_sample(store, "ardupilot-sitl-startup-crash")
    cid = out["case_id"]
    inv.investigate(store, cid, model=None)
    store.append(cid, "engineer_finding", {"action": "Run SITL under gdb", "result": "found", "observations": "crash in the tiltrotor setup code"}, actor="Ana")
    assert inv._case_context(store, cid)["engineer_results"][0]["result"] == "found"
    d = inv.draft_lesson(store, cid)
    assert d["investigation_status"] == "cause_probable" and "tiltrotor" in d["cause"] and d["decisive_check"] == "Run SITL under gdb"
    run = store.latest_run(cid)
    domain = next(iter(run["domains"]))
    store.append(cid, "case_closure", {"investigation_status": d["investigation_status"], "confirmed_by": "engineer_judgement", "cause_domain": domain,
                                       "cause": d["cause"], "evidence_note": d["evidence_note"], "resolution_status": "not_yet_repaired",
                                       "decisive_check": d["decisive_check"][:200]}, actor="Ana")
    assert store.verify(cid)["ledger_chain"]["valid"]


def test_api_upload_to_investigation_to_finding(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    monkeypatch.setenv("VEYRA_ENABLE_RUNTIME", "true")
    monkeypatch.setenv("VEYRA_ALLOW_RECORDINGS", "true")
    monkeypatch.setenv("VEYRA_ENGINE_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("VEYRA_INVESTIGATOR", "rules")
    monkeypatch.delenv("VEYRA_PRIVATE", raising=False)
    from app.engine_api import router as engine_router
    from app.recordings_api import router as recordings_router
    app = FastAPI()
    app.include_router(recordings_router)
    app.include_router(engine_router)
    c = TestClient(app)
    c.headers.update({"x-veyra-request": "1"})
    assert {x["name"] for x in c.get("/engine/samples").json()} >= {"rplidar-no-scans"}

    def wait(job_id):
        for _ in range(100):
            j = c.get(f"/engine/jobs/{job_id}").json()
            if j["state"] != "running":
                return j
            time.sleep(0.1)
        raise AssertionError("job did not finish")
    j = wait(c.post("/engine/samples/ardupilot-sitl-startup-crash/run", json={"allow_model": False}).json()["job_id"])
    assert j["state"] == "done", j
    cid = j["case_id"]
    got = c.get(f"/engine/cases/{cid}/investigation").json()
    assert got["investigation"]["mode"] == "deterministic" and got["agent"]["configured"] is False
    r = c.post(f"/engine/cases/{cid}/finding", json={"action": got["investigation"]["result"]["next_action"]["action"][:200], "result": "cannot_do"})
    assert r.status_code == 200, r.text
    assert wait(r.json()["job_id"])["state"] == "done"
    after = c.get(f"/engine/cases/{cid}/investigation").json()
    assert after["findings"][0]["result"] == "cannot_do" and after["investigation"]["entry_id"] != got["investigation"]["entry_id"]
    draft = c.get(f"/engine/cases/{cid}/lesson-draft").json()
    assert draft["draft"]["investigation_status"] in ("cause_unknown", "cause_probable") and draft["domains"]
