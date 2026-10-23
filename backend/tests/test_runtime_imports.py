"""Profile-based import through the local runtime API. SYNTHETIC fixtures only."""
import base64
import json
from pathlib import Path

import pytest

from test_veyra_v0 import client, reset

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "synthetic" / "remote_delay"


@pytest.fixture(autouse=True)
def local_runtime(monkeypatch):
    reset()
    monkeypatch.setenv("VEYRA_ENABLE_RUNTIME", "true")


def create_case(synthetic=True):
    response = client.post("/runtime/cases", json={"title": "Remote delay (synthetic)", "adapter": "industrial", "asset_id": "EX1",
                                                   "actor": "Engineer A", "context": {}, "synthetic": synthetic})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def body(filename, profile_name, raw=None, **extra):
    raw = raw if raw is not None else (FIXTURES / filename).read_bytes()
    return {"actor": "Engineer A", "filename": filename, "content_base64": base64.b64encode(raw).decode(),
            "profile": json.loads((FIXTURES / profile_name).read_text()), "source_uri": "synthetic://remote_delay/" + filename, **extra}


CONSOLE = ("synthetic_ex1_console.csv", "synthetic_console_profile.json")
VEHICLE = ("synthetic_ex1_vehicle.jsonl", "synthetic_vehicle_profile.json")


def test_preview_stores_nothing_and_commit_feeds_the_existing_engine():
    cid = create_case()
    preview = client.post(f"/runtime/cases/{cid}/imports/preview", json=body(*CONSOLE))
    assert preview.status_code == 200, preview.text
    assert preview.json()["stored"] is False and preview.json()["accepted_row_count"] == 210
    assert client.get(f"/runtime/cases/{cid}").json()["sources"] == []

    committed = client.post(f"/runtime/cases/{cid}/imports", json=body(*CONSOLE))
    assert committed.status_code == 200, committed.text
    source = committed.json()["source"]
    assert "events" not in source and source["event_count"] == 30 and source["sample_count"] == 180
    assert source["ingestion"]["profile"]["synthetic"] is True

    run = client.post(f"/runtime/cases/{cid}/runs", json={"actor": "Engineer A"})
    assert run.status_code == 200, run.text
    rtt = next(f for f in run.json()["findings"] if f["label"].startswith("rtt"))
    assert rtt["intervals"] and rtt["metrics"]["max"] >= 380
    assert all(t["status"] == "succeeded" for t in run.json()["trace"])  # integrity of stored original bytes holds


def test_jsonl_events_are_kept_out_of_numeric_tools():
    cid = create_case()
    committed = client.post(f"/runtime/cases/{cid}/imports", json=body(*VEHICLE))
    assert committed.status_code == 200, committed.text
    assert committed.json()["source"]["sample_count"] == 0 and committed.json()["source"]["event_count"] > 200
    run = client.post(f"/runtime/cases/{cid}/runs", json={"actor": "Engineer A"})
    assert run.status_code == 200, run.text


def test_duplicate_upload_rejected_and_review_reason_required():
    cid = create_case()
    assert client.post(f"/runtime/cases/{cid}/imports", json=body(*CONSOLE)).status_code == 200
    duplicate = client.post(f"/runtime/cases/{cid}/imports", json=body(*CONSOLE))
    assert duplicate.status_code == 409 and duplicate.json()["detail"]["report"]["status"] == "duplicate_upload"

    lines = (FIXTURES / CONSOLE[0]).read_text().splitlines()
    damaged = ("\n".join(lines[:5] + ["2026-03-04T09:00:30.000Z,EX9,lte_rtt,50,ms,console->vehicle-router,"] + lines[5:20]) + "\n").encode()
    blocked = client.post(f"/runtime/cases/{cid}/imports", json=body("partial.csv", CONSOLE[1], raw=damaged))
    assert blocked.status_code == 422 and blocked.json()["detail"]["report"]["status"] == "needs_review"
    stored = client.post(f"/runtime/cases/{cid}/imports", json=body("partial.csv", CONSOLE[1], raw=damaged, review_reason="EX9 row is another machine; excluded"))
    assert stored.status_code == 200 and stored.json()["source"]["review"]["reason"].startswith("EX9")


def test_unsupported_and_synthetic_mismatch_are_not_stored():
    cid = create_case()
    unsupported = client.post(f"/runtime/cases/{cid}/imports", json=body("drive.mcap", CONSOLE[1], raw=b"\x89MCAP0\r\n"))
    assert unsupported.status_code == 422 and unsupported.json()["detail"]["report"]["review_queue"]
    real_case = create_case(synthetic=False)
    mismatch = client.post(f"/runtime/cases/{real_case}/imports", json=body(*CONSOLE))
    assert mismatch.status_code == 422 and "Synthetic profiles" in mismatch.json()["detail"]
    assert client.get(f"/runtime/cases/{cid}").json()["sources"] == []


def test_query_window_returns_the_exact_original_line():
    cid = create_case()
    source = client.post(f"/runtime/cases/{cid}/imports", json=body(*CONSOLE)).json()["source"]
    result = client.post(f"/runtime/cases/{cid}/tools", json={"tool": "query_window", "source_id": source["id"], "actor": "Engineer A", "limit": 3}).json()
    lines = (FIXTURES / CONSOLE[0]).read_text().splitlines()
    for sample in result["result"]["samples"]:
        assert sample["original_record"] == lines[sample["line"] - 1] and not sample["original_record"].startswith("time,")


def test_legacy_csv_original_record_is_not_the_header():
    cid = create_case()
    csv_text = "time,machine,metric,reading,units,endpoint\n2026-01-01T14:02:00Z,EX1,rtt,40,ms,router\n2026-01-01T14:06:00Z,EX1,rtt,470,ms,router\n"
    mapping = {"timestamp": "time", "asset": "machine", "signal": "metric", "value": "reading", "unit": "units", "scope": "endpoint", "time_unit": "iso8601"}
    added = client.post(f"/runtime/cases/{cid}/sources", json={"name": "legacy.csv", "content": csv_text, "format": "csv", "role": "observation", "frame": "not_applicable",
                                                               "source_uri": "synthetic://legacy", "actor": "Engineer A", "mapping": mapping, "synthetic": True})
    assert added.status_code == 200, added.text
    result = client.post(f"/runtime/cases/{cid}/tools", json={"tool": "query_window", "source_id": added.json()["id"], "actor": "Engineer A"}).json()
    assert [s["original_record"] for s in result["result"]["samples"]] == csv_text.splitlines()[1:]


def test_demo_reset_refuses_to_drop_runtime_records(monkeypatch):
    create_case()
    monkeypatch.delenv("VEYRA_ENABLE_RUNTIME")
    monkeypatch.delenv("VEYRA_ALLOW_DESTRUCTIVE_TEST_RESET")
    refused = client.post("/demo/reset")
    assert refused.status_code == 409 and "runtime investigation record" in refused.json()["detail"]


def test_signal_catalog_is_served():
    catalog = client.get("/runtime/catalog/signals").json()
    assert catalog["version"] == "remote-delay-catalog-v5" and any(s["id"] == "command.received" for s in catalog["signals"])
