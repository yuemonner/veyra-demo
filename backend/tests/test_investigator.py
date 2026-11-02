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


def test_hosted_showcase_replays_the_recorded_investigation_labelled(store):
    ids = public_samples.seed_showcase(store)
    assert len(ids) == 1 and public_samples.seed_showcase(store) == []            # once
    inv_ = inv.latest(store, ids[0])
    assert inv_["mode"] == "agent" and inv_["recorded"]["note"].startswith("Recorded") and inv_["result"]["hypotheses"]
    assert [e["kind"] for e in store.ledger(ids[0]) if e["kind"] in ("agent_report", "engineer_finding")] == ["agent_report", "engineer_finding", "agent_report"]
    assert store.verify(ids[0])["ledger_chain"]["valid"]


def test_every_model_call_records_tokens_cost_latency_and_model(store):
    out = public_samples.run_sample(store, "rplidar-no-scans")
    cid = out["case_id"]
    store.append(cid, "model_authorization", {"provider": "anthropic", "scope": "test", "authorized_by": "t", "evidence": "test"}, actor="t")
    raw = inv.LogTools(store, cid).raw
    n = next(i for i, l in enumerate(raw, 1) if l.strip())
    report = {"headline": "No scan", "status": "cause_unknown", "confidence": "low", "explanation": "",
              "evidence": [{"line": n, "quote": raw[n - 1][:40], "shows": "first line", "calls": ["a1"]}], "checked": [],
              "next_action": {"action": "Read the health status", "why": "splits device from driver", "how": [], "where": "remote",
                              "outcomes": [{"label": "Error", "means": "device"}, {"label": "Healthy", "means": "driver"}]}, "unknowns": []}
    u1 = {"input_tokens": 1200, "cache_read_input_tokens": 30000, "cache_creation_input_tokens": 5000, "output_tokens": 800}
    u2 = {"input_tokens": 900, "cache_read_input_tokens": 36000, "cache_creation_input_tokens": 0, "output_tokens": 1500}
    model = _scripted([
        {"content": [{"type": "tool_use", "id": "a1", "name": "overview", "input": {}}], "usage": u1, "model_id": "claude-sonnet-x", "cost_usd": 0.031, "api_ms": 4100},
        {"content": [{"type": "tool_use", "id": "a2", "name": "submit_report", "input": report}], "usage": u2, "model_id": "claude-sonnet-x", "cost_usd": 0.027, "api_ms": 6200},
    ])
    r = inv.investigate(store, cid, model=model)
    calls = r["model_calls"]
    assert [c["call"] for c in calls] == [1, 2] and all(c["model_id"] == "claude-sonnet-x" for c in calls)
    assert calls[0]["cache_creation_input_tokens"] == 5000 and calls[1]["output_tokens"] == 1500
    assert calls[0]["requested"] == ["overview"] and calls[1]["requested"] == ["submit_report"]
    assert all(isinstance(c["ms"], int) and c["ms"] >= 0 for c in calls) and calls[1]["api_ms"] == 6200
    u = r["usage"]
    assert u["input_tokens"] == 1200 + 30000 + 900 + 36000 and u["output_tokens"] == 2300     # same meaning as earlier records
    assert u["uncached_input_tokens"] == 2100 and u["cache_read_input_tokens"] == 66000 and u["cache_creation_input_tokens"] == 5000
    assert u["cost_usd"] == 0.058 and u["model_calls"] == 2 and u["model_ids"] == ["claude-sonnet-x"]
    assert inv.latest(store, cid)["model_calls"] == calls                                            # stored in the case history


def test_cost_is_left_unknown_when_any_step_did_not_report_it(store):
    calls = [inv.model_call_record(1, {"usage": {"input_tokens": 10}, "cost_usd": 0.01}, 5, "m"),
             inv.model_call_record(2, {"usage": {"input_tokens": 10}}, 5, "m")]
    assert inv.usage_totals(calls)["cost_usd"] is None and calls[1]["model_id"] == "m"
