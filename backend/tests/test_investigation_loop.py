"""Protocol/synthetic checks only. A scripted caller never validates real Agent autonomy."""
import copy

import pytest

from app.agent import investigation_loop as lab
from app.agent.llm import ModelError
from app.agent.runner import egress_allowed
from app.store.sqlite_store import CaseStore
from app.ingest.profile import parse_profile


def snapshot():
    rows = []
    for i in range(160):
        for sig, val in [("network.rtt", 40 if i < 85 else 460), ("control.cycle_time", 12)]:
            rows.append({"signal": sig, "value": val, "unit": "ms", "scope": None, "kind": "measurement",
                         "t": 1700000000 + i * .1, "clock_domain": "device", "import_id": "raw", "row": len(rows) + 1})
    return {"case_id": "test", "synthetic": True, "source_hashes": {"raw": "ab" * 32}, "excluded_label_rows": 0,
            "imports": [{"id": "raw", "sha256": "ab" * 32, "clock_domain": "device", "max_clock_skew_s": None, "rows": rows}]}


def session():
    return lab.Session(snapshot(), provider="scripted-test", model="protocol-only", mode="protocol_test")


def revision(**kwargs):
    return {"hypotheses": [{"id": "network", "statement": "Delay along the observed network path", "missing": "Endpoints and command response", **kwargs},
                            {"id": "control", "statement": "Local controller processing delay", "missing": "Task queue and vehicle feedback"}],
            "reason": "Test competing explanations, no confirmed cause"}


def proposal():
    return {"method": "onset", "inputs": {"a": {"signal": "network.rtt"}}, "params": {"normal": "first_fraction"},
            "distinguishes": ["network", "control"], "why": "Test whether the measured network path changed before requesting other evidence",
            "possible_outcomes": ["RTT departure supports investigating this path", "No departure leaves controller and unmeasured paths open"]}


def finish(disposition="insufficient_evidence"):
    return {"disposition": disposition, "summary": "The initiating fault cannot be determined.", "reason_to_stop": "Missing physical response",
            "minimum_missing_information": ["Timestamped vehicle feedback on an aligned clock"], "evidence": []}


def call(s, name, args=None):
    return s.execute(f"c{len(s.result['calls']) + 1}", name, args or {})


def test_real_calculation_and_evidence_revision_without_engine_run():
    s = session()
    assert s.data.run("test") is None
    c = call(s, "inventory")
    assert c["status"] == "completed" and s.result["completed_analyses"] == 0
    assert c["output"]["precomputed_findings_supplied"] is False
    call(s, "revise_hypotheses", revision())
    p = call(s, "propose_calculation", proposal())
    c = call(s, "run_calculation", {"plan_id": p["id"]})
    assert c["status"] == "completed" and c["output"]["result"]["departed"]
    assert c["output"]["provenance"]["inputs"][0]["rows_sha256"]
    assert "ASSUMED" in c["output"]["result"]["normal_basis"]
    r = call(s, "revise_hypotheses", revision(status="leading", support=[c["id"]]))
    assert r["output"]["previous"][0]["status"] == "open"
    final = finish("candidate_explanations"); final["evidence"] = [c["id"]]
    assert call(s, "finish", final)["status"] == "completed"
    assert s.result["state"] == "finished" and not s.result["agent_autonomy_validated"]


def test_missing_prerequisites_do_not_become_analysis_or_success():
    s = session()
    assert call(s, "run_calculation", {"plan_id": "not-existing"})["status"] == "failed"
    assert call(s, "finish", finish("candidate_explanations"))["status"] == "failed"
    assert s.result["completed_analyses"] == 0
    assert call(s, "finish", finish())["status"] == "completed"


def test_proposal_requires_competition_explicit_reference_and_real_series():
    s = session()
    assert call(s, "propose_calculation", proposal())["status"] == "failed"
    call(s, "revise_hypotheses", revision())
    p = proposal(); p["params"] = {}
    assert call(s, "propose_calculation", p)["status"] == "failed"
    p = proposal(); p["inputs"]["a"]["signal"] = "unavailable"
    assert call(s, "propose_calculation", p)["status"] == "failed"


def test_metadata_and_failed_calls_cannot_support_leading_hypothesis():
    s = session()
    c = call(s, "inventory")
    assert call(s, "revise_hypotheses", revision(status="leading", support=[c["id"]]))["status"] == "failed"
    assert call(s, "revise_hypotheses", revision(support=["another-run"]))["status"] == "failed"


def test_result_must_be_read_into_revision_before_candidate_report():
    s = session()
    call(s, "revise_hypotheses", revision())
    p = call(s, "propose_calculation", proposal())
    call(s, "run_calculation", {"plan_id": p["id"]})
    assert call(s, "finish", finish("candidate_explanations"))["status"] == "failed"
    assert call(s, "run_calculation", {"plan_id": p["id"]})["status"] == "failed"


def test_snapshot_mutation_and_foreign_case_access_fail_closed():
    s = session()
    with pytest.raises(ValueError, match="Outside"):
        s.data.series("foreign", "network.rtt")
    s.snapshot["imports"][0]["rows"][0]["value"] = 999
    assert call(s, "inventory")["status"] == "failed" and not s.result["integrity"]


def test_code_change_invalidates_run(monkeypatch):
    s = session()
    monkeypatch.setattr(lab, "code_manifest", lambda: {"changed": "yes"})
    assert call(s, "inventory")["status"] == "failed"


def test_failed_sandbox_is_not_counted_as_completed(monkeypatch):
    s = session()
    call(s, "revise_hypotheses", revision())
    p = proposal(); p.update(method="python", code="result = 1")
    pc = call(s, "propose_calculation", p)
    monkeypatch.setattr(lab.sandbox, "run", lambda *a, **kw: {"ok": False, "error": "OS sandbox unavailable"})
    assert call(s, "run_calculation", {"plan_id": pc["id"]})["status"] == "failed"
    assert s.result["completed_analyses"] == 0


def test_normal_data_yields_no_departure_and_clock_mismatch_cannot_order():
    s = session()
    call(s, "revise_hypotheses", revision())
    p = proposal(); p["inputs"]["a"]["signal"] = "control.cycle_time"
    pc = call(s, "propose_calculation", p)
    out = call(s, "run_calculation", {"plan_id": pc["id"]})["output"]
    assert out["result"]["departed"] is False
    a = s.data.series("test", "network.rtt")
    b = copy.deepcopy(a); b["clock"] = "unrelated"
    result = lab.methods.onset_order(s.data, a, b, normal="first_fraction")
    assert result["verdict"] == "cannot_tell"


def test_no_model_and_no_egress_authorization_are_blocked(tmp_path):
    store = CaseStore(tmp_path / "evidence.db")
    cid = store.create_case(machine_id="M", title="Recording", actor="Test", synthetic=False)["id"]
    profile = parse_profile({"profile_id": "test", "version": 1, "format": "jsonl", "clock_domain": "device",
        "columns": {k: k for k in ("timestamp", "asset", "signal", "value", "unit")}, "signal_map": {"rtt": "network.rtt"},
        "timestamp_format": "epoch", "epoch_unit": "s"})
    import json
    body = "\n".join(json.dumps({"timestamp": 1700000000 + i, "asset": "M", "signal": "rtt", "value": 40, "unit": "ms"}) for i in range(20))
    store.commit_import(cid, body.encode(), "raw.jsonl", profile, actor="Test")
    class Model:
        provider = "openai"; model = "test"
        def available(self): return False
    s = lab.prepare(store, cid, Model())
    assert s.result["state"] == "blocked" and s.result["model_calls"] == 0
    assert store.latest_run(cid) is None and egress_allowed(store, cid, "openai") is None
    Model.available = lambda self: True
    assert lab.prepare(store, cid, Model()).result["state"] == "blocked"
    store.close()


def test_model_failure_and_budget_do_not_claim_success():
    s = session()
    class Broken:
        def create(self, **kwargs): raise ModelError("Authentication unavailable")
    result = s.run(Broken())
    assert result["state"] == "failed" and result["model_calls"] == 1 and not result["agent_autonomy_validated"]
    class NoTools:
        def create(self, **kwargs): return {"content": []}
    s = session(); result = s.run(NoTools())
    assert result["state"] == "incomplete" and result["model_calls"] == lab.MAX_TURNS


def test_operator_tool_validation_is_real_calculation_with_zero_model_calls():
    s = session(); s.result["mode"] = "operator_tool_validation"
    result = lab.validate_tools(s)
    assert result["state"] == "finished" and result["completed_analyses"] == 1
    assert result["model_calls"] == 0 and not result["agent_autonomy_validated"]
    assert result["report"]["disposition"] == "insufficient_evidence"


def test_http_persists_blocked_run_and_does_not_accept_foreign_case(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.engine_api import get_store
    from app.investigation_api import router
    from test_autonomous_entry import make_store
    monkeypatch.setenv("VEYRA_ENABLE_RUNTIME", "true")
    monkeypatch.setenv("VEYRA_INVESTIGATION_LAB", "true")
    monkeypatch.setenv("VEYRA_INVESTIGATION_AGENT_BLOCKED", "true")
    monkeypatch.setenv("VEYRA_PRIVATE", "false")
    monkeypatch.delenv("RENDER", raising=False); monkeypatch.delenv("VERCEL", raising=False)
    store, cid = make_store(tmp_path)
    app = FastAPI(); app.include_router(router); app.dependency_overrides[get_store] = lambda: store
    try:
        client = TestClient(app)
        base = f"/engine/cases/{cid}/investigation-lab"
        r = client.post(base)
        assert r.status_code == 202 and r.json()["state"] == "blocked"
        assert client.get(base).json()["runs"][0]["id"] == r.json()["id"]
        assert client.get(base + "/" + r.json()["id"]).status_code == 200
        assert client.get("/engine/cases/foreign/investigation-lab").status_code == 404
        assert store.latest_run(cid) is None
    finally:
        store.close()
