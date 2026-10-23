"""Hosted demo settings: the access-code gate on writes, and the first-boot seed that never touches existing cases."""
import gzip
import importlib
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VEYRA_ENABLE_RUNTIME", "true")
    monkeypatch.setenv("VEYRA_ENGINE_DB", str(tmp_path / "engine.db"))
    monkeypatch.setenv("VEYRA_ACCESS_CODE", "access-code-123")
    monkeypatch.delenv("VEYRA_ENGINE_SEED", raising=False)
    main = importlib.import_module("app.main")
    with TestClient(main.app) as c:
        yield c


def test_reads_are_open_and_writes_need_the_code(client):
    assert client.get("/engine/cases").status_code == 200
    denied = client.post("/engine/demo/field-case", json={})
    assert denied.status_code == 401 and denied.json()["code"] == "access_required"
    wrong = client.post("/engine/demo/field-case", json={}, headers={"x-veyra-access": "nope"})
    assert wrong.status_code == 401
    ok = client.post("/engine/demo/field-case", json={}, headers={"x-veyra-access": "access-code-123"})
    assert ok.status_code == 200 and ok.json()["synthetic"] is True


def test_cors_origins_setting():
    from app.main import cors_origins
    assert cors_origins("https://a.vercel.app/, https://b.vercel.app") == ["https://a.vercel.app", "https://b.vercel.app"]
    assert cors_origins("") == ["*"]


def test_signed_webhooks_skip_the_access_gate(client):
    # The webhook keeps its own HMAC check: without a signature it is refused by that, not by the access gate.
    response = client.post("/engine/webhooks/unknown-source", content=b"{}")
    assert response.status_code != 401 or response.json().get("code") != "access_required"


def test_seed_loads_real_case_verbatim_and_story_only_into_an_empty_store(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "scripts"))
    import export_case_seed
    from app import engine_seed
    from app.workflows.demo import build_field_case_story
    from app.store.sqlite_store import CaseStore

    source = CaseStore(tmp_path / "source.db")
    story = build_field_case_story(source)
    source.close()
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    assert export_case_seed.main(str(tmp_path / "source.db"), str(seed_dir / "case.sqlite.gz"), [story["case_id"]]) == 0
    monkeypatch.setattr(engine_seed, "SEED_DIR", seed_dir)

    target = CaseStore(tmp_path / "hosted.db")
    result = engine_seed.seed_if_empty(target)
    assert result["seeded"] and result["real_cases"] == [story["case_id"]]
    assert target.verify(story["case_id"])["ledger_chain"]["valid"]
    count = target.db.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
    assert engine_seed.seed_if_empty(target)["seeded"] is False  # second boot: untouched
    assert target.db.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == count
    target.close()


PUBLIC_SEED_MACHINES = {"ALFA-CarbonZ": "CMU AirLab ALFA (CC0)", "ANYmal-D-GrandTour": "ETH RSL GrandTour (MIT)"}


def test_shipped_seed_is_only_public_research_data():
    from app import engine_seed
    files = sorted(engine_seed.SEED_DIR.glob("*.sqlite.gz"))
    assert files, "fixtures/seed holds the real-data demo cases"
    import sqlite3
    import tempfile
    for f in files:
        with tempfile.NamedTemporaryFile(suffix=".sqlite") as plain:
            with gzip.open(f, "rb") as packed:
                shutil.copyfileobj(packed, plain)
            plain.flush()
            rows = sqlite3.connect(plain.name).execute("SELECT machine_id, synthetic, title FROM cases").fetchall()
        assert rows and all(m in PUBLIC_SEED_MACHINES for m, s, t in rows), "only public research datasets ship with the demo"
        # A case built with an injected fault is flagged synthetic and says so in its title.
        assert all((s == 1) == ("SYNTHETIC" in t) for m, s, t in rows)


def test_hosted_site_and_its_previews_are_accepted_others_refused(monkeypatch):
    from app.runtime_engine import origin_allowed
    monkeypatch.setenv("VEYRA_CORS_ORIGINS", "https://frontend-livid-seven-41.vercel.app")
    monkeypatch.setenv("VEYRA_CORS_ORIGIN_REGEX", r"https://frontend-[a-z0-9]+-silkenwind\.vercel\.app")
    assert origin_allowed("http://127.0.0.1:3019")
    assert origin_allowed("https://frontend-livid-seven-41.vercel.app")
    assert origin_allowed("https://frontend-5gu85zf7q-silkenwind.vercel.app")
    assert not origin_allowed("https://frontend-5gu85zf7q-silkenwind.vercel.app.evil.example")
    assert not origin_allowed("https://evil.example")


def test_read_cache_never_hides_tampering_from_writes(tmp_path):
    from app.workflows.demo import build_field_case_story
    from app.store.sqlite_store import CaseStore, StoreError
    store = CaseStore(tmp_path / "e.db")
    case_id = build_field_case_story(store)["case_id"]
    assert store.verify(case_id)["imports_intact"]  # read path: computed once, then cached
    store.db.execute("DROP TRIGGER imports_no_update")
    store.db.execute("UPDATE imports SET content = CAST(X'00' || content AS BLOB) WHERE case_id = ?", (case_id,))
    store.db.commit()
    assert not any(i["integrity_ok"] for i in store.imports(case_id, include_rows=True))  # full check sees it
    assert not store.verify(case_id)["imports_intact"]  # size changed, so the cached entry no longer applies
    with pytest.raises(StoreError, match="integrity"):
        store.investigate(case_id, actor="Engineer")
    store.close()


def test_hosted_page_origin_may_send_credentials(monkeypatch):
    """The page fetches with credentials: 'include'; without this header every browser call fails (CORS)."""
    from fastapi.testclient import TestClient
    from app.main import app
    response = TestClient(app).options("/engine/cases", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"})
    assert response.headers.get("access-control-allow-credentials") == "true"
