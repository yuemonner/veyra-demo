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
    assert data["first_abnormal_evidence"]["id"] == "R03-module-unhealthy"
    assert data["human_discovery"]["id"] == "R03-engineer-note-1426"
    assert data["detection_latency"] == "0h15m"
    assert data["current_state"]["application_version"] == "app-0.36"
    assert data["current_state"]["configuration"] == "device-profile-C17"
    assert data["current_state"]["health"] == "degraded"


def test_comparison_groups_affected_and_unaffected_peers():
    investigation_id = reset()
    response = client.get(f"/investigations/{investigation_id}/comparison")
    assert response.status_code == 200
    data = response.json()
    assert data["same_change"] == 7
    assert data["same_signal"] == 2
    assert data["no_signal"] == 5
    assert "R03" in data["affected_assets"]
    assert "R06" in data["unaffected_assets"]
    assert data["potentially_exposed"][0]["asset_id"] == "R06"
    table = {row["context"]: row for row in data["table"]}
    assert table["Application 0.36"] == {"context": "Application 0.36", "affected": "2/2", "unaffected": "5/5"}
    assert table["Device profile C17"] == {"context": "Device profile C17", "affected": "2/2", "unaffected": "1/5"}
    assert table["Module firmware 4.9"] == {"context": "Module firmware 4.9", "affected": "2/2", "unaffected": "1/5"}
    assert table["Site network profile N7"] == {"context": "Site network profile N7", "affected": "2/2", "unaffected": "1/5"}
    assert table["Cellular-only"] == {"context": "Cellular-only", "affected": "2/2", "unaffected": "2/5"}


def test_decision_package_can_be_sealed_and_late_evidence_does_not_mutate_it():
    investigation_id = reset()
    package = client.post(f"/investigations/{investigation_id}/decision-package").json()
    assert package["package"]["decision_substantiation"]["status"] == "incomplete"
    assert package["package"]["decision_substantiation"]["question"] == "Are we allowed and justified to take the operational action yet?"
    assert package["package"]["observability_state"]["status"] == "partial"
    assert len(package["package"]["options_considered"]) == 4
    assert package["package"]["what_was_unknown"]
    assert package["package"]["primary_hypothesis"]["confidence"] > 0
    assert package["package"]["outcome_validation"]["status"] == "pending"
    client.post(
        f"/investigations/{investigation_id}/decision",
        json={
            "decision": "Remote restart affected machines and hold field dispatch",
            "owner": "Robotics Engineering",
            "rationale": "Affected machines share module firmware 4.9, device profile C17 and site network profile N7.",
            "package_id": package["id"],
        },
    )
    sealed = client.post(f"/decision-packages/{package['id']}/seal").json()
    assert sealed["sealed"] is True
    assert sealed["package"]["human_decision"]["owner"] == "Robotics Engineering"
    assert sealed["package"]["decision_substantiation"]["status"] == "actionable_with_open_follow_up"
    assert sealed["package"]["planned_action"]["action_type"] == "remote_fix_and_hold_rollout"
    assert sealed["package"]["actual_action"]["scope"]["field_dispatch"] == "held"
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
    assert late["review_required"] is True
    assert late["late_evidence"]["event_time"] == "2026-09-03T14:09:00Z"
    assert late["late_evidence"]["known_at"] == "2026-09-03T14:31:00Z"
    assert late["late_evidence"]["ingested_at"] == "2026-09-03T14:31:04Z"
    sealed_again = client.post(f"/decision-packages/{package['id']}/seal").json()
    assert sealed_again["digest"] == digest


def test_decision_context_tracks_options_observability_actions_and_late_review_flags():
    investigation_id = reset()
    package = client.post(f"/investigations/{investigation_id}/decision-package").json()
    client.post(
        f"/investigations/{investigation_id}/decision",
        json={
            "decision": "Remote restart affected machines and hold field dispatch",
            "owner": "Robotics Engineering",
            "rationale": "Affected machines share module firmware 4.9, device profile C17 and site network profile N7.",
            "package_id": package["id"],
        },
    )
    context = client.get(f"/investigations/{investigation_id}/decision-context").json()
    assert context["observability_state"]["status"] == "partial"
    assert any(option["selected"] for option in context["options_considered"])
    assert context["executed_actions"][0]["action_type"] == "remote_fix_and_hold_rollout"
    assert context["evidence_snapshots"]
    assert context["decision_records"][0]["immutable"] is True
    assert context["decision_records"][0]["chosen_option"]["option_type"] in {"remote_fix", "pause_rollout"}
    assert context["decision_records"][0]["evidence_snapshot"]["what_was_unknown"]
    assert context["decision_records"][0]["evidence_snapshot"]["primary_hypothesis"]["confidence"] > 0
    decision_record_digest = context["decision_records"][0]["digest"]
    client.post("/demo/late-evidence")
    context_after_late = client.get(f"/investigations/{investigation_id}/decision-context").json()
    assert context_after_late["review_flags"][0]["flag_type"] == "late_decision_relevant_evidence"
    assert context_after_late["decision_records"][0]["digest"] == decision_record_digest


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
    response = client.post(
        f"/investigations/{investigation_id}/outcome",
        json={
            "outcome": "Remote restart restored connectivity; field visit avoided; rollout held pending review",
            "payload": {"recovery_minutes": 18, "field_visit": False, "engineering_hours_saved": 3},
        },
    )
    assert response.status_code == 200
    memory = client.get(f"/memory/similar?investigation_id={investigation_id}").json()
    assert memory["similar_cases"][0]["outcome"] == "Remote restart restored connectivity; field visit avoided; rollout held pending review"
    assert memory["precedent_comparison"]["evidence_strength"] == "precedent, not causal proof"
    precedent = client.get(f"/precedents/compare?investigation_id={investigation_id}").json()
    assert precedent["response_history"]["remote_fix"]["cases"] >= 1
    assert precedent["response_history"]["remote_fix"]["attribution"] == "observed"
    assert precedent["outcome_validation"]["status"] == "observed_recovery_not_causal_proof"
    assert precedent["outcome_validation"]["causal_attribution"] == "not_established"
    assert precedent["cost_comparison"][0]["field_visit"] is False
    remote_fix_row = [row for row in precedent["outcome_comparison"] if row["action"] == "remote_fix"][0]
    assert remote_fix_row["attribution"] == "observed"
