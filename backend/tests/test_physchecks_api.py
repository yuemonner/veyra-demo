"""Local engineering API. Never reads private cases: VEYRA_PRIVATE_CASES_DIR points at an empty temporary folder."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import physchecks_api as api


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VEYRA_PRIVATE_CASES_DIR", str(tmp_path / "private"))
    monkeypatch.setenv("VEYRA_ENGINEERING_LOCAL", "true")
    api.reset_for_tests(tmp_path / "eng.db")
    app = FastAPI()
    app.include_router(api.router)
    yield TestClient(app)
    api.reset_for_tests(None)


def test_routes_are_off_unless_enabled(tmp_path, monkeypatch):
    monkeypatch.delenv("VEYRA_ENGINEERING_LOCAL", raising=False)
    app = FastAPI()
    app.include_router(api.router)
    assert TestClient(app).get("/engineering/cases").status_code == 404


def test_routes_refuse_non_loopback_clients(client):
    from types import SimpleNamespace
    from fastapi import HTTPException
    for host in ("203.0.113.9", "10.0.0.2", ""):
        with pytest.raises(HTTPException) as e:
            api._guard(SimpleNamespace(client=SimpleNamespace(host=host)))
        assert e.value.status_code == 404
    api._guard(SimpleNamespace(client=SimpleNamespace(host="127.0.0.1")))  # loopback passes


def test_main_app_mounts_the_router_but_keeps_it_off(monkeypatch):
    monkeypatch.delenv("VEYRA_ENGINEERING_LOCAL", raising=False)
    from app.main import app
    assert TestClient(app).get("/engineering/cases").status_code == 404


def test_synthetic_replay_has_five_sections_and_honest_labels(client):
    cases = client.get("/engineering/cases").json()["cases"]
    assert cases[0]["id"] == api.SYNTHETIC_ID and cases[0]["synthetic"] and len(cases) == 1
    pack = client.get(f"/engineering/cases/{api.SYNTHETIC_ID}/replay").json()
    assert [s["n"] for s in pack["sections"]] == [1, 2, 3, 4, 5]
    assert pack["case"]["synthetic"] is True and pack["banner"].startswith("SYNTHETIC")
    assert pack["agent"]["mode"] == "not_run"
    for sec in pack["sections"]:
        for c in sec.get("claims") or []:
            assert c["basis"] in ("evidence", "analysis", "historical", "assumption")
    ev = [c for c in pack["sections"][0]["claims"] if c["basis"] == "evidence"]
    assert ev and all(r["row"] > 0 for c in ev for r in c["refs"])
    s2 = pack["sections"][1]
    assert s2["check_outcome"] == "VIOLATION" and s2["charts"] and s2["calculations"]
    # the synthetic incident has an unrecorded interval inside the repeat window: the chart line must break there
    rep = next(ch for ch in s2["charts"] if ch["window"] == "repeat")
    assert any(p["v"] is None for ser in rep["series"] for p in ser["points"])
    fault = next(ch for ch in s2["charts"] if ch["window"] == "fault")
    assert fault["predicted"]["basis"] == "analysis" and fault["episodes"]
    assert pack["sections"][3]["plans"]["none"]["telemetry_verdict"]
    runs = pack["sections"][4]["scenario"]["preview_runs"]
    assert {r["recording"] for r in runs} >= {"synthetic-vehicle-2", "synthetic-vehicle-3"}
    assert all(r["recording_synthetic"] and not r["reviewed"] for r in runs)       # previews of an unreviewed proposal
    assert pack["history"]["reports"] == [] and pack["trace"]["checks"]
    json.dumps(pack)  # serialisable


def test_replay_qualification_switch(client):
    pack = client.get(f"/engineering/cases/{api.SYNTHETIC_ID}/replay?qualification=hv_qualified").json()
    assert pack["sections"][3]["plans"]["hv_qualified"]["recommended"][0]["id"] == "DATA-METERED-CYCLE"   # planner since 55aa491: an existing metered record before any capacity test
    assert client.get(f"/engineering/cases/{api.SYNTHETIC_ID}/replay?qualification=wizard").status_code == 400


def test_series_drill_down_by_field_returns_rows(client):
    r = client.get(f"/engineering/cases/{api.SYNTHETIC_ID}/series", params={"window": "fault", "field": "soc", "t0": 600, "t1": 602}).json()
    assert r["role"] == "soc" and r["samples"] and all(x["row"] > 0 and x["file"] for x in r["samples"])
    assert client.get(f"/engineering/cases/{api.SYNTHETIC_ID}/series", params={"window": "fault", "field": "nope", "t0": 0, "t1": 1}).status_code == 400


def test_unknown_case_404(client):
    assert client.get("/engineering/cases/NOPE/replay").status_code == 404


def test_scenario_review_scan_and_outcomes_through_the_api(client):
    h = {"x-veyra-actor": "Engineer A"}
    assert client.post("/engineering/scenarios", json={"case_id": api.SYNTHETIC_ID, "title": "Abrupt SOC correction"}).status_code == 400  # no actor
    s = client.post("/engineering/scenarios", json={"case_id": api.SYNTHETIC_ID, "title": "Abrupt SOC correction"}, headers=h).json()
    assert s["status"] == "draft"
    assert client.post(f"/engineering/scenarios/{s['id']}/review", json={"decision": "approve", "notes": "ok"}, headers=h).status_code == 400
    ok = client.post(f"/engineering/scenarios/{s['id']}/review", json={"decision": "approve", "notes": "checked rows"}, headers={"x-veyra-actor": "Engineer B"}).json()
    assert ok["status"] == "approved"
    scan = client.post("/engineering/scan").json()
    assert scan["counts"].get("VIOLATION", 0) >= 1 and scan["counts"].get("PASS", 0) >= 1
    pack = client.get(f"/engineering/cases/{api.SYNTHETIC_ID}/replay").json()
    assert pack["sections"][4]["scenario"]["registered"][0]["status"] == "approved"
    o = client.post(f"/engineering/cases/{api.SYNTHETIC_ID}/outcomes", json={"kind": "propose", "data": {"hypothesis_id": "A", "statement": "SOC drift"}}, headers=h).json()
    assert o["state"]["hypotheses"]["A"]["stage"] == "proposed_diagnosis"
    bad = client.post(f"/engineering/cases/{api.SYNTHETIC_ID}/outcomes", json={"kind": "verify", "data": {"hypothesis_id": "A", "result": "confirmed",
                      "observation_ids": [], "basis": "x"}}, headers=h)
    assert bad.status_code == 400


def test_outcome_chain_through_the_api_as_the_page_drives_it(client):
    """Open (signature from the server's checks) -> track -> performed -> conflicting observations block verification ->
    withdraw one (kept, superseded) -> verify; the replay then shows the hypothesis as confirmed."""
    cid, h = api.SYNTHETIC_ID, {"x-veyra-actor": "Engineer A"}
    post = lambda kind, data: client.post(f"/engineering/cases/{cid}/outcomes", json={"kind": kind, "data": data}, headers=h)
    r = post("open", {})
    assert r.status_code == 200 and r.json()["record"]["payload"]["signature"]
    assert post("propose", {"hypothesis_id": "A", "statement": "s", "origin": "deterministic"}).status_code == 200
    pid = post("performed", {"template_id": "DOC-BMS-HISTORY", "method": "m", "performed_by": "A", "qualification": "none"}).json()["record"]["id"]
    good = post("observe", {"performed_id": pid, "summary": "ok", "measurements": {}, "supports": ["A"], "contradicts": []}).json()["record"]["id"]
    bad = post("observe", {"performed_id": pid, "summary": "typo", "measurements": {}, "supports": [], "contradicts": ["A"]}).json()["record"]["id"]
    v = {"hypothesis_id": "A", "result": "confirmed", "observation_ids": [good], "basis": "b"}
    r = post("verify", v)
    assert r.status_code == 400 and "conflicting" in r.json()["detail"]
    assert post("correct", {"record_id": bad, "reason": "mis-typed", "replacement": None}).status_code == 200
    r = post("verify", v)
    assert r.status_code == 200
    hist = {x["id"]: x for x in r.json()["state"]["history"]}
    assert hist[bad]["current"] is False and hist[bad]["payload"]["summary"] == "typo"
    assert client.post(f"/engineering/cases/{cid}/outcomes", json={"kind": "verify", "data": v}).status_code == 400   # no engineer named
    rep = client.get(f"/engineering/cases/{cid}/replay").json()
    a = next(x for x in rep["sections"][2]["assessment"]["hypotheses"] if x["id"] == "A")
    assert a["status"] == "confirmed"
