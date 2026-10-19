"""Investigation Engine v2 over HTTP (same core as the CLI). SYNTHETIC fixtures only. Needs FastAPI."""
import base64
import json
from pathlib import Path

import pytest

from test_veyra_v0 import client

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "synthetic" / "remote_delay"


@pytest.fixture(autouse=True)
def engine_env(monkeypatch, tmp_path):
    monkeypatch.setenv("VEYRA_ENABLE_RUNTIME", "true")
    monkeypatch.setenv("VEYRA_ENGINE_DB", str(tmp_path / "engine.db"))


def upload(cid, data, profile, path="imports"):
    body = {"actor": "FDE A", "filename": data, "content_base64": base64.b64encode((FIXTURES / data).read_bytes()).decode(),
            "profile": json.loads((FIXTURES / profile).read_text())}
    return client.post(f"/engine/cases/{cid}/{path}", json=body)


def test_engine_api_full_loop():
    created = client.post("/engine/cases", json={"machine_id": "EX1", "title": "API synthetic", "actor": "FDE A", "synthetic": True,
                                                 "symptom_start": "2026-03-04T09:06:00Z", "symptom_end": "2026-03-04T09:07:30Z", "symptom_source": "Synthetic"})
    assert created.status_code == 200, created.text
    cid = created.json()["id"]
    assert upload(cid, "synthetic_ex1_console.csv", "synthetic_console_profile.json", "imports/preview").json()["stored"] is False
    for data, profile in (("synthetic_ex1_console.csv", "synthetic_console_profile.json"), ("synthetic_ex1_vehicle.jsonl", "synthetic_vehicle_profile.json")):
        assert upload(cid, data, profile).status_code == 200
    run = client.post(f"/engine/cases/{cid}/runs", json={"actor": "FDE A"})
    assert run.status_code == 200, run.text
    assert run.json()["root_cause"] == "unconfirmed"
    run_id = run.json()["id"]
    review = client.post(f"/engine/cases/{cid}/ledger", json={"kind": "check_review", "actor": "FDE A", "source": "api",
                                                            "payload": {"run_id": run_id, "check_id": "CHK-SOURCE-ROWS", "verdict": "accepted", "reason": "coverage"}})
    assert review.status_code == 200, review.text
    assert client.post(f"/engine/cases/{cid}/checks/CHK-SOURCE-ROWS/execute", json={"actor": "FDE A"}).json()["result"]["count"] > 0
    stale = client.post("/engine/machines/EX1/context", json={"key": "site", "value": "Pit 3", "source": "call", "actor": "FDE A", "effective_from": "2026-03-01T00:00:00Z"})
    assert stale.status_code == 200
    decision = client.post(f"/engine/cases/{cid}/ledger", json={"kind": "decision", "actor": "FDE A", "source": "api",
                                                              "payload": {"run_id": run_id, "options_considered": ["a"], "chosen_option": "a", "rationale": "r"}})
    assert decision.status_code == 422 and "machine context changed" in decision.json()["detail"]
    ledger = client.get(f"/engine/cases/{cid}/ledger").json()
    assert ledger["verification"]["ledger_chain"]["valid"] and len(ledger["entries"]) >= 4
    report = client.get(f"/engine/cases/{cid}/report")
    assert report.status_code == 200 and "SYNTHETIC DATA" in report.text
    assert client.get("/engine/cases/case-missing").status_code == 404


def test_engine_api_disabled_without_runtime(monkeypatch):
    monkeypatch.delenv("VEYRA_ENABLE_RUNTIME")
    assert client.get("/engine/schema").status_code == 404


def test_api_rejects_client_supplied_automatic_execution_receipt():
    created = client.post('/engine/cases', json={'machine_id':'EX1','title':'Synthetic forged receipt regression',
                          'actor':'FDE A','synthetic':True,'symptom_start':'2026-03-04T09:06:00Z',
                          'symptom_end':'2026-03-04T09:07:30Z','symptom_source':'Synthetic'})
    cid = created.json()['id']
    assert upload(cid, 'synthetic_ex1_console.csv', 'synthetic_console_profile.json').status_code == 200
    run = client.post(f'/engine/cases/{cid}/runs', json={'actor':'FDE A'}).json()
    review = {'kind':'check_review','actor':'FDE A','source':'synthetic://review',
              'payload':{'run_id':run['id'],'check_id':'CHK-SOURCE-ROWS','verdict':'accepted','reason':'Review coverage'}}
    assert client.post(f'/engine/cases/{cid}/ledger', json=review).status_code == 200
    forged = {'kind':'check_execution','actor':'FDE A','source':'synthetic://forged',
              'payload':{'run_id':run['id'],'check_id':'CHK-SOURCE-ROWS','status':'automatic_read_only',
                         'tool_result':{'tool':'invented','count':999999}}}
    rejected = client.post(f'/engine/cases/{cid}/ledger', json=forged)
    assert rejected.status_code == 422 and 'execution endpoint' in rejected.json()['detail']
    genuine = client.post(f'/engine/cases/{cid}/checks/CHK-SOURCE-ROWS/execute', json={'actor':'FDE A'})
    assert genuine.status_code == 200 and genuine.json()['result']['count'] > 0
    assert genuine.json()['entry']['payload']['tool_result']['count'] == genuine.json()['result']['count']
