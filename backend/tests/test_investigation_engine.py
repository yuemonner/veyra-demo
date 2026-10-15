from test_veyra_v0 import client, reset


def observation(**changes):
    return {"asset_id": "EX11", "zone": "loading-zone-B", "safe_stop": True,
            "exposure_verified": True, "telemetry_complete": True,
            "source": "runtime://EX11/zone-b/window-1", "event_time": "2026-09-03T14:32:00", **changes}


def submit(case, **changes):
    return client.post(f"/investigations/{case}/diagnostic-checks/check-ex11-zone-b/observation", json={"observation": observation(**changes)})


def hypothesis(data):
    return next(h for h in data["hypotheses"] if h["id"] == "h-l4-zone-b")


def test_unverified_peer_is_never_a_control_and_ranking_explains_utility():
    case = reset()
    data = client.get(f"/investigations/{case}/analysis").json()
    peer = next(c for c in data["cohorts"] if c["asset_id"] == "EX11")
    assert not peer["valid_control"]
    assert peer["exposure_status"] == "Exposure not verified"
    assert not any(e["id"].startswith("EX11") for e in hypothesis(data)["contradicting_evidence"])
    assert data["next_check"]["id"] == "check-ex11-zone-b"
    assert data["next_check"]["separated_pairs"]
    assert "probability" not in hypothesis(data)


def test_empty_unknown_mismatched_and_future_results_are_rejected():
    case = reset()
    path = f"/investigations/{case}/diagnostic-checks/check-ex11-zone-b/observation"
    assert client.post(path, json={"observation": {}}).status_code == 422
    for change in ({"safe_stop": None}, {"safe_stop": "false"}, {"asset_id": "EX03"}, {"event_time": "2099-01-01"}, {"source": ""}):
        assert submit(case, **change).status_code == 422


def test_incomplete_observation_preserves_uncertainty_then_verified_result_revises():
    case = reset()
    data = submit(case, safe_stop=False, exposure_verified=False).json()
    assert hypothesis(data)["status"] == "possible"
    data = submit(case, safe_stop=False).json()
    assert hypothesis(data)["status"] == "weakened"
    assert hypothesis(data)["contradicting_evidence"][-1]["source"].startswith("runtime://")
    assert next(c for c in data["cohorts"] if c["asset_id"] == "EX11")["valid_control"]
    historical = client.get(f"/investigations/{case}/analysis?known_at=2026-09-03T14:27:00").json()
    assert not historical["diagnostic_events"]
    assert historical["next_check"]["id"] == "check-ex11-zone-b"
    assert hypothesis(historical)["status"] == "possible"


def test_positive_check_is_support_not_causal_proof_and_precedent_is_conditional():
    case = reset()
    assert hypothesis(submit(case).json())["status"] == "supported_not_proven"
    path = f"/investigations/{case}/analysis/precedent"
    assert client.post(path, json={"owner": "Engineer", "outcome": "Observed recovery"}).status_code == 422
    saved = client.post(path, json={"owner": "Engineer", "outcome": "Observed recovery", "applicable_conditions": ["L4; verified Zone B exposure"]})
    assert saved.status_code == 200
    data = client.get(f"/investigations/{case}/analysis").json()
    precedent = data["conditional_precedents"][-1]
    assert precedent["conditions"]
    assert not precedent["automatic_recommendation"]
    assert precedent["verification"] == "human_reported_requires_independent_verification"


def test_conflicting_results_require_review_instead_of_overwriting_history():
    case = reset()
    submit(case)
    data = submit(case, safe_stop=False).json()
    assert hypothesis(data)["status"] == "conflicting_evidence"
    assert len(data["diagnostic_events"]) == 2
    assert not next(c for c in data["cohorts"] if c["asset_id"] == "EX11")["valid_control"]


def test_verified_different_exposure_is_not_a_control_or_a_falsification():
    case = reset()
    data = submit(case, safe_stop=False, exposure_relation='different', exposure_verified=True).json()
    peer = next(c for c in data['cohorts'] if c['asset_id'] == 'EX11')
    assert peer['exposure_status'] == 'Different conditions verified'
    assert not peer['valid_control']
    assert hypothesis(data)['status'] == 'possible'
    assert data['next_check']


def test_visitor_sessions_are_isolated_and_persist_on_reload():
    reset()
    a = client.post('/demo/investigation-sessions', json={}).json()
    b = client.post('/demo/investigation-sessions', json={}).json()
    assert a['investigation_id'] != b['investigation_id']
    check = next(c for c in a['checks'] if c['id'].endswith('check-ex11-zone-b'))
    response = client.post(f"/investigations/{a['investigation_id']}/diagnostic-checks/{check['id']}/observation", json={'observation': observation(safe_stop=False, exposure_relation='same')})
    assert response.status_code == 200
    reloaded = client.get(f"/investigations/{a['investigation_id']}/analysis").json()
    untouched = client.get(f"/investigations/{b['investigation_id']}/analysis").json()
    assert len(reloaded['diagnostic_events']) == 1
    assert not untouched['diagnostic_events']
    assert any(h['status'] == 'weakened' for h in reloaded['hypotheses'])
    assert reloaded['comparison_quality']['sample_status'] == 'insufficient_for_causal_estimation'


def test_engineer_can_challenge_but_cannot_invent_evidence_references():
    case = reset()
    endpoint = f'/investigations/{case}/hypotheses/h-l4-zone-b/challenge'
    assert client.post(endpoint, json={'owner':'Engineer','reason':'Workload not matched','evidence_ids':['invented-evidence']}).status_code == 422
    response = client.post(endpoint, json={'owner':'Engineer','reason':'Workload not matched','evidence_ids':['EX03-safe-stop']})
    assert response.status_code == 200
    h = hypothesis(response.json())
    assert h['review_status'] == 'challenged'
    assert h['challenges'][0]['reason'] == 'Workload not matched'
    assert h['status'] == 'possible'


def test_configuration_only_healthy_peers_do_not_refute_any_hypothesis():
    case = reset()
    data = client.get(f'/investigations/{case}/analysis').json()
    assert all(not h['contradicting_evidence'] for h in data['hypotheses'])


def test_configured_pair_does_not_become_verified_exposure_support():
    case = reset()
    data = client.get(f'/investigations/{case}/analysis').json()
    summary = data['configuration_summary']
    assert summary['affected_configured_l4_zone_b'] == 3
    assert summary['no_signal_configured_l4_zone_b'] == 1
    assert summary['verified_affected_exposure_count'] == 0
    assert data['comparison_quality']['valid_control_count'] == 0
    h2 = hypothesis(data)
    assert not h2['supporting_evidence']
    assert len(h2['context_evidence']) == 3
    assert all(e['matched_fields'] == {'configuration':'localization-L4','network_profile':'loading-zone-B'} for e in h2['context_evidence'])
    assert h2['evidence_strength'] == 'context_only'
    assert h2['investigation_priority'] == 'high'
    assert h2['status'] == 'possible'


def test_firmware_context_validates_payload_instead_of_trusting_localization_ids():
    from app.db import SessionLocal
    from app.models import Evidence
    case = reset()
    data = client.get(f'/investigations/{case}/analysis').json()
    h3 = next(h for h in data['hypotheses'] if h['id'] == 'h-lidar-53')
    assert not h3['supporting_evidence']
    assert len(h3['context_evidence']) == 3
    assert all(e['matched_fields'] == {'firmware_version':'lidar-fw-5.3'} for e in h3['context_evidence'])
    with SessionLocal() as db:
        for asset in ('EX03','EX05','EX08'):
            e = db.get(Evidence, f'{asset}-localization-map-update')
            e.payload = {k:v for k,v in e.payload.items() if k != 'firmware_version'}
        db.commit()
    changed = client.get(f'/investigations/{case}/analysis').json()
    h3 = next(h for h in changed['hypotheses'] if h['id'] == 'h-lidar-53')
    assert not h3['context_evidence']
    assert not h3['supporting_evidence']
    assert 'Firmware metadata required' in h3['missing_evidence']


def test_current_state_and_reference_outcome_are_explicitly_separate():
    case = reset()
    data = client.get(f'/investigations/{case}/analysis').json()
    assert not data['diagnostic_events']
    assert not data['conditional_precedents']
    assert data['historical_reference']['scope'] == 'historical_reference'
    assert data['historical_reference']['used_in_current_analysis'] is False
    assert data['historical_reference']['verification'] == 'illustrative_not_independently_verified'
    assert all(h['status'] == 'possible' for h in data['hypotheses'])


def test_ex08_event_time_and_known_time_are_not_interchangeable():
    case = reset()
    data = client.get(f'/investigations/{case}/analysis').json()
    event = next(e for e in data['timeline'] if e['id'] == 'EX08-safe-stop')
    assert event['event_time'] == '2026-09-03T14:25:00Z'
    assert event['known_at'] == '2026-09-03T14:26:00Z'
    early = client.get(f'/investigations/{case}/analysis?known_at=2026-09-03T14:25:30').json()
    assert not any(e['id'] == 'EX08-safe-stop' for e in early['timeline'])


def test_hypotheses_api_uses_same_assessment_as_analysis():
    case = reset()
    analysis = client.get(f'/investigations/{case}/analysis').json()
    hypotheses = client.get(f'/investigations/{case}/hypotheses').json()
    assert [(h['id'],h['status'],h['evidence_strength']) for h in hypotheses['hypotheses']] == [(h['id'],h['status'],h['evidence_strength']) for h in analysis['hypotheses']]
