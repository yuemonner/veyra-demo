"""Release acceptance for the hosted demo: an upload is readable only with the access code, the size limit holds, and
a visitor without the code still sees the demo cases."""
import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

LOG = Path(__file__).resolve().parents[2] / "fixtures/real/public/rplidar-no-scans/log.txt"
CODE = {"x-veyra-access": "access-code-123"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VEYRA_ENABLE_RUNTIME", "true")
    monkeypatch.setenv("VEYRA_ENGINE_DB", str(tmp_path / "engine.db"))
    monkeypatch.setenv("VEYRA_ACCESS_CODE", "access-code-123")
    monkeypatch.setenv("VEYRA_PUBLIC_DEMO", "true")
    monkeypatch.setenv("VEYRA_ALLOW_RECORDINGS", "true")
    monkeypatch.setenv("VEYRA_INVESTIGATOR", "rules")
    monkeypatch.delenv("VEYRA_ENGINE_SEED", raising=False)
    main = importlib.import_module("app.main")
    from app import engine_seed
    engine_seed.DEMO_CASE_IDS.clear()
    with TestClient(main.app) as c:
        yield c
    engine_seed.DEMO_CASE_IDS.clear()


def test_an_upload_is_readable_only_with_the_access_code(client):
    from app import engine_seed
    up = client.post("/engine/recordings?filename=lidar.log", content=LOG.read_bytes(), headers=CODE)
    assert up.status_code == 200
    rid = up.json()["id"]
    assert client.get(f"/engine/recordings/{rid}").status_code == 404
    job = client.post(f"/engine/recordings/{rid}/debrief", headers=CODE, json={"mapping": {}, "machine_id": "LIDAR-1", "title": "No scans", "allow_model": False}).json()
    assert client.get(f"/engine/jobs/{job['job_id']}").status_code == 404
    import time
    for _ in range(100):
        state = client.get(f"/engine/jobs/{job['job_id']}", headers=CODE).json()
        if state["state"] != "running":
            break
        time.sleep(0.1)
    cid = state["result"]["case_id"]
    assert client.get(f"/engine/cases/{cid}").status_code == 404
    assert client.get(f"/engine/cases/{cid}/investigation").status_code == 404
    assert cid not in [c["id"] for c in client.get("/engine/cases").json()]
    assert client.get(f"/engine/cases/{cid}", headers=CODE).status_code == 200
    assert cid in [c["id"] for c in client.get("/engine/cases", headers=CODE).json()]
    inv = client.get(f"/engine/cases/{cid}/investigation", headers=CODE).json()["investigation"]["result"]
    assert inv["status"] == "cause_unknown" and inv["headline"].startswith("First error at line 32")
    # a demo case stays open to every visitor
    engine_seed.DEMO_CASE_IDS.add(cid)
    assert client.get(f"/engine/cases/{cid}").status_code == 200


def test_a_wrong_code_reads_nothing_new_and_writes_nothing(client):
    assert client.post("/engine/recordings?filename=x.log", content=b"ERROR x", headers={"x-veyra-access": "nope"}).status_code == 401
    assert client.get("/engine/cases/case-doesnotexist", headers={"x-veyra-access": "nope"}).status_code == 404


def test_the_size_limit_holds(client, monkeypatch):
    from app import recordings_api
    monkeypatch.setattr(recordings_api, "MAX_MB", 1)
    r = client.post("/engine/recordings?filename=big.log", content=b"INFO ok\n" * (1024 * 1024 // 8 + 10), headers=CODE)
    assert r.status_code == 413 and "1 MB" in r.json()["detail"]
    r = client.post("/engine/recordings?filename=evil.exe", content=b"x", headers=CODE)
    assert r.status_code == 422
