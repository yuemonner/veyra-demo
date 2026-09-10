import os
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.gettempdir()}/veyra_test.db"

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def reset():
    response = client.post("/demo/reset")
    assert response.status_code == 200
    return response.json()["investigation_id"]


def test_reconstruction_calculates_lkg_first_abnormal_and_latency():
    investigation_id = reset()
    response = client.post(f"/investigations/{investigation_id}/reconstruct")
    assert response.status_code == 200
    data = response.json()
    assert data["last_known_healthy"]["id"] == "AMR-001-healthy-1402"
    assert data["first_abnormal_evidence"]["id"] == "AMR-001-nav-drift"
    assert data["human_discovery"]["id"] == "AMR-001-ticket-1426"
    assert data["detection_latency"] == "0h15m"
    assert data["current_state"]["application_version"] == "2.4"
    assert data["current_state"]["network_profile"] == "C"
    assert data["current_state"]["health"] == "degraded"


def test_comparison_groups_affected_and_unaffected_peers():
    investigation_id = reset()
    response = client.get(f"/investigations/{investigation_id}/comparison")
    assert response.status_code == 200
    data = response.json()
    assert data["same_change"] == 120
    assert data["same_signal"] == 37
    assert data["no_signal"] == 83
    assert "AMR-001" in data["affected_assets"]
    assert "AMR-038" in data["unaffected_assets"]
    assert data["potentially_exposed"][0]["asset_id"] == "AMR-038"
    table = {row["context"]: row for row in data["table"]}
    assert table["Application v2.4"] == {"context": "Application v2.4", "affected": "37/37", "unaffected": "83/83"}
    assert table["Warehouse-east config"] == {"context": "Warehouse-east config", "affected": "29/37", "unaffected": "31/83"}
    assert table["Motor firmware 7.2"] == {"context": "Motor firmware 7.2", "affected": "37/37", "unaffected": "21/83"}
    assert table["Private 5G"] == {"context": "Private 5G", "affected": "25/37", "unaffected": "55/83"}
    assert table["Network Profile C"] == {"context": "Network Profile C", "affected": "37/37", "unaffected": "21/83"}


def test_decision_package_can_be_sealed_and_late_evidence_does_not_mutate_it():
    investigation_id = reset()
    package = client.post(f"/investigations/{investigation_id}/decision-package").json()
    sealed = client.post(f"/decision-packages/{package['id']}/seal").json()
    assert sealed["sealed"] is True
    digest = sealed["digest"]
    assert len(digest) == 64
    assert sealed["signature"]
    late = client.post("/demo/late-evidence").json()
    assert late["comparison"]["same_signal"] == 38
    assert late["late_evidence"]["event_time"] == "2026-09-03T14:09:00Z"
    assert late["late_evidence"]["known_at"] == "2026-09-03T14:31:00Z"
    assert late["late_evidence"]["ingested_at"] == "2026-09-03T14:31:04Z"
    sealed_again = client.post(f"/decision-packages/{package['id']}/seal").json()
    assert sealed_again["digest"] == digest


def test_runtime_event_only_needs_event_time():
    reset()
    response = client.post(
        "/events",
        json={
            "asset_id": "AMR-001",
            "event_type": "heartbeat",
            "event_time": "2026-09-03T14:40:00",
            "source": "edge_agent",
            "evidence_class": "OBSERVED",
            "payload": {"process_health": "healthy"},
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["event_time"] == "2026-09-03T14:40:00Z"
    assert data["known_at"]
    assert data["ingested_at"]


def test_outcome_becomes_operational_memory():
    investigation_id = reset()
    response = client.post(f"/investigations/{investigation_id}/outcome", json={"outcome": "Network profile N7 held pending site review"})
    assert response.status_code == 200
    memory = client.get(f"/memory/similar?investigation_id={investigation_id}").json()
    assert memory["similar_cases"][0]["outcome"] == "Network profile N7 held pending site review"
