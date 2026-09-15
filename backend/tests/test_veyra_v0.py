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
    assert data["last_known_healthy"]["id"] == "R03-healthy-run"
    assert data["first_abnormal_evidence"]["id"] == "R03-grip-drift"
    assert data["human_discovery"]["id"] == "R03-engineer-note-1426"
    assert data["detection_latency"] == "0h15m"
    assert data["current_state"]["application_version"] == "policy-v0.9"
    assert data["current_state"]["configuration"] == "camera-cal-C"
    assert data["current_state"]["health"] == "degraded"


def test_comparison_groups_affected_and_unaffected_peers():
    investigation_id = reset()
    response = client.get(f"/investigations/{investigation_id}/comparison")
    assert response.status_code == 200
    data = response.json()
    assert data["same_change"] == 6
    assert data["same_signal"] == 2
    assert data["no_signal"] == 4
    assert "R03" in data["affected_assets"]
    assert "R06" in data["unaffected_assets"]
    assert data["potentially_exposed"][0]["asset_id"] == "R06"
    table = {row["context"]: row for row in data["table"]}
    assert table["Policy v0.9"] == {"context": "Policy v0.9", "affected": "2/2", "unaffected": "4/4"}
    assert table["Camera calibration C"] == {"context": "Camera calibration C", "affected": "2/2", "unaffected": "1/4"}
    assert table["Gripper firmware 7.3"] == {"context": "Gripper firmware 7.3", "affected": "2/2", "unaffected": "1/4"}
    assert table["Low-light test cell"] == {"context": "Low-light test cell", "affected": "1/2", "unaffected": "2/4"}
    assert table["End-effector G2"] == {"context": "End-effector G2", "affected": "2/2", "unaffected": "2/4"}


def test_decision_package_can_be_sealed_and_late_evidence_does_not_mutate_it():
    investigation_id = reset()
    package = client.post(f"/investigations/{investigation_id}/decision-package").json()
    assert package["package"]["decision_substantiation"]["status"] == "incomplete"
    assert package["package"]["decision_substantiation"]["question"] == "Are we allowed and justified to take the operational action yet?"
    client.post(
        f"/investigations/{investigation_id}/decision",
        json={
            "decision": "Pause v0.9 on robots with calibration C and gripper firmware 7.3",
            "owner": "Robotics Engineering",
            "rationale": "Affected runs share calibration C and gripper firmware 7.3.",
            "package_id": package["id"],
        },
    )
    sealed = client.post(f"/decision-packages/{package['id']}/seal").json()
    assert sealed["sealed"] is True
    assert sealed["package"]["human_decision"]["owner"] == "Robotics Engineering"
    assert sealed["package"]["decision_substantiation"]["status"] == "actionable_with_open_follow_up"
    assert sealed["package"]["_seal"]["trusted_timestamp"] == "2026-09-03T14:27:00Z"
    assert sealed["package"]["_seal"]["timestamp_authority"] == "Veyra demo timestamp authority"
    digest = sealed["digest"]
    assert len(digest) == 64
    assert sealed["signature"]
    verified = client.get(f"/decision-packages/{package['id']}/verify").json()
    assert verified["valid"] is True
    assert verified["digest_matches"] is True
    assert verified["signature_valid"] is True
    assert verified["verification_mode"] == "standalone"
    late = client.post("/demo/late-evidence").json()
    assert late["comparison"]["same_signal"] == 3
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
            "asset_id": "R03",
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
    response = client.post(f"/investigations/{investigation_id}/outcome", json={"outcome": "Calibration C held pending targeted low-light runs"})
    assert response.status_code == 200
    memory = client.get(f"/memory/similar?investigation_id={investigation_id}").json()
    assert memory["similar_cases"][0]["outcome"] == "Calibration C held pending targeted low-light runs"
