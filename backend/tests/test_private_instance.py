"""Private instance: sign-in, one database per workspace, records attributed to the signed-in person, share links,
recording upload -> debrief. Test accounts and passwords are generated here (test values only)."""
import secrets
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("VEYRA_PRIVATE", "true")
    monkeypatch.setenv("VEYRA_ENABLE_RUNTIME", "true")
    monkeypatch.setenv("VEYRA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("VEYRA_PRIVATE_ALLOW_AGENT", raising=False)
    from app import private
    from app.accounts import Accounts
    from app.engine_api import router as engine_router
    from app.recordings_api import router as recordings_router
    private._accounts = None
    app = FastAPI()
    app.include_router(private.auth)
    app.include_router(private.shares)
    app.include_router(recordings_router)
    app.include_router(engine_router)
    app.middleware("http")(private.gate)
    accounts = Accounts()
    accounts.add_workspace("Acme Robotics")
    accounts.add_workspace("Other Co")
    pw = {k: secrets.token_urlsafe(14) for k in ("a", "a2", "b")}
    accounts.add_user(workspace_id="acme-robotics", email="ana@acme.test", name="Ana Field", password=pw["a"])
    accounts.add_user(workspace_id="acme-robotics", email="ben@acme.test", name="Ben Review", password=pw["a2"])
    accounts.add_user(workspace_id="other-co", email="olga@other.test", name="Olga", password=pw["b"])
    accounts.close()
    yield app, pw
    private._accounts = None


def client(app, email=None, password=None):
    c = TestClient(app)
    c.headers.update({"x-veyra-request": "1"})
    if email:
        r = c.post("/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
    return c


def test_engine_needs_sign_in(env):
    app, _ = env
    r = TestClient(app).get("/engine/cases")
    assert r.status_code == 401 and r.json()["code"] == "login_required"


def test_wrong_password_and_lockout(env):
    app, pw = env
    c = TestClient(app)
    for _ in range(5):
        assert c.post("/auth/login", json={"email": "ana@acme.test", "password": "wrong-password"}).status_code == 401
    r = c.post("/auth/login", json={"email": "ana@acme.test", "password": pw["a"]})
    assert r.status_code == 401 and "15 minutes" in r.json()["detail"]


def test_records_carry_the_signed_in_person_and_workspaces_are_separate(env):
    app, pw = env
    ana = client(app, "ana@acme.test", pw["a"])
    case = ana.post("/engine/cases", json={"machine_id": "R1", "title": "Arm stalls", "actor": "Someone else", "synthetic": False}).json()
    entries = ana.get(f"/engine/cases/{case['id']}/ledger").json()["entries"]
    assert entries[0]["actor"] == "Ana Field"
    assert ana.get("/auth/me").json()["user"]["workspace_name"] == "Acme Robotics"
    ben = client(app, "ben@acme.test", pw["a2"])
    assert [c["id"] for c in ben.get("/engine/cases").json()] == [case["id"]]
    olga = client(app, "olga@other.test", pw["b"])
    assert olga.get("/engine/cases").json() == []
    assert olga.get(f"/engine/cases/{case['id']}").status_code == 404


def test_writes_need_the_request_header_and_risky_endpoints_are_off(env):
    app, pw = env
    c = client(app, "ana@acme.test", pw["a"])
    bare = TestClient(app)
    bare.cookies = c.cookies
    assert bare.post("/engine/cases", json={"machine_id": "R1", "title": "x", "actor": "a"}).status_code == 403
    assert c.post("/engine/demo/field-case", json={}).status_code == 404
    case = c.post("/engine/cases", json={"machine_id": "R1", "title": "Arm stalls", "actor": "a"}).json()
    r = c.post(f"/engine/cases/{case['id']}/agent", json={})
    assert r.status_code == 403 and "external model" in r.json()["detail"]


def test_share_link_is_read_only_expires_and_can_be_revoked(env):
    app, pw = env
    from app.workflows.case_memory import build_case_memory_demo  # any case with a run
    from app.private import accounts
    from app.store.sqlite_store import CaseStore
    from app.accounts import workspace_db
    store = CaseStore(workspace_db("acme-robotics"))
    ids = build_case_memory_demo(store)
    store.close()
    ana = client(app, "ana@acme.test", pw["a"])
    share = ana.post(f"/engine/cases/{ids['incident_2']}/shares", json={"days": 7}).json()
    public = TestClient(app)
    page = public.get(share["path"])
    assert page.status_code == 200 and "Shared read-only by Ana Field" in page.text and page.headers["cache-control"] == "no-store"
    assert public.post(share["path"]).status_code in (404, 405)
    olga = client(app, "olga@other.test", pw["b"])
    assert olga.delete(f"/engine/shares/{share['id']}").status_code == 404
    assert ana.delete(f"/engine/shares/{share['id']}").status_code == 200
    assert public.get(share["path"]).status_code == 404
    assert public.get("/share/not-a-real-token").status_code == 404


def test_upload_recording_then_debrief(env, tmp_path):
    pytest.importorskip("rosbags")
    pytest.importorskip("mcap")
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import make_synthetic_recordings as gen
    path = gen.write_mcap(tmp_path / "walk.mcap")
    app, pw = env
    ana = client(app, "ana@acme.test", pw["a"])
    up = ana.post("/engine/recordings?filename=walk.mcap", content=path.read_bytes(), headers={"content-type": "application/octet-stream"})
    assert up.status_code == 200, up.text
    meta = up.json()
    assert meta["inspection"]["format"] == "mcap" and meta["suggested"]["topics"]
    job = ana.post(f"/engine/recordings/{meta['id']}/debrief", json={
        "mapping": meta["suggested"], "machine_id": "QD1", "title": "Left front leg dragging (SYNTHETIC)", "machine_class": "legged_robot",
        "problem_from_s": 55, "problem_to_s": 119, "normal_from_s": 5, "normal_to_s": 50,
        "report": {"stated_reason": "Robot started limping", "symptom": "erratic_motion", "affected_function": "left front leg", "at_s": 62}}).json()
    for _ in range(240):
        status = ana.get(f"/engine/jobs/{job['job_id']}").json()
        if status["state"] != "running":
            break
        time.sleep(0.5)
    assert status["state"] == "done", status
    detail = ana.get(f"/engine/cases/{status['result']['case_id']}").json()
    assert detail["case"]["title"].startswith("Left front leg") and detail["ledger"][0]["actor"] == "Ana Field"
    olga = client(app, "olga@other.test", pw["b"])
    assert olga.get(f"/engine/jobs/{job['job_id']}").status_code == 404
    assert olga.get(f"/engine/recordings/{meta['id']}").status_code == 404
    assert ana.post("/engine/recordings?filename=notes.txt", content=b"x").status_code == 422
