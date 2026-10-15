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
