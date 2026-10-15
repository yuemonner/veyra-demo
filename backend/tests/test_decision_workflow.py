from datetime import datetime, timedelta
from test_veyra_v0 import client, reset


def fresh():
    return client.post('/demo/investigation-sessions', json={}).json()['investigation_id']


def decision(case, option='inspect', approval='not_required'):
    return client.post(f'/investigations/{case}/workflow/decisions', json={'option_id': option, 'owner': 'Delivery lead', 'rationale': 'Review records before changing state', 'constraints': 'Read-only access; no recovery claimed', 'approval': approval})


def action(case, d):
    return client.post(f'/investigations/{case}/workflow/actions', json={'decision_id': d['id'], 'owner': 'Engineer', 'description': 'Reviewed operating records', 'scope': 'EX11 run window', 'source': 'synthetic://execution/1', 'executed_at': datetime.utcnow().isoformat(), 'deviation_reason': 'Same as decision'})


def verification(case, a, result='recovered', **changes):
    return client.post(f'/investigations/{case}/workflow/verifications', json={'action_id': a['id'], 'owner': 'Reviewer', 'result': result, 'summary': 'Synthetic follow-up observation', 'window_start': a['executed_at'], 'window_end': datetime.utcnow().isoformat(), 'source': 'synthetic://verification/1', 'remaining_unknowns': 'Causal attribution unresolved', 'conditions': {'configuration': 'localization-L4'}, 'exclusions': 'Different hardware and unverified operating conditions require review', **changes})


def test_decision_freezes_evidence_and_never_fabricates_an_action():
    reset(); case = fresh()
    d = decision(case).json()
    assert len(d['options_considered']) == 3
    assert d['selected_option']['id'] == 'inspect'
    assert not d['evidence_snapshot']['diagnostic_events']
    state = client.get(f'/investigations/{case}/workflow').json()
    assert state['decisions'][0]['id'] == d['id']
    assert state['actions'] == state['verifications'] == []
    analysis = client.get(f'/investigations/{case}/analysis').json()
    check = next(c for c in analysis['checks'] if c['id'].endswith('check-ex11-zone-b'))
    r = client.post(f"/investigations/{case}/diagnostic-checks/{check['id']}/observation", json={'observation': {'asset_id': 'EX11', 'zone': 'loading-zone-B', 'safe_stop': False, 'exposure_verified': True, 'exposure_relation': 'same', 'telemetry_complete': True, 'source': 'synthetic://check/1', 'event_time': datetime.utcnow().isoformat()}})
    assert r.status_code == 200
    reread = client.get(f'/investigations/{case}/workflow').json()['decisions'][0]
    assert reread['evidence_snapshot'] == d['evidence_snapshot']


def test_approval_case_links_and_execution_times_are_enforced():
    reset(); case = fresh(); other = fresh()
    d = decision(case, 'rollback', 'pending').json()
    assert action(case, d).status_code == 422
    assert decision(case, 'rollback', 'not_required').status_code == 422
    approved = decision(case, 'rollback', 'approved').json()
    assert action(other, approved).status_code == 422
    a = action(case, approved)
    assert a.status_code == 200
    assert a.json()['status'] == 'human_reported_execution'
    invalid = a.json(); invalid['executed_at'] = '2099-01-01'
    assert client.post(f'/investigations/{case}/workflow/actions', json=invalid).status_code == 422
    assert client.get('/investigations/missing/workflow').status_code == 404


def test_verification_recurrence_governs_cross_case_reuse_and_no_self_match():
    reset(); case = fresh(); other = fresh()
    a = action(case, decision(case).json()).json()
    first = verification(case, a)
    assert first.status_code == 200
    second = verification(case, a, 'recurred')
    assert second.status_code == 200
    assert len(client.get(f'/investigations/{case}/workflow').json()['verifications']) == 2
    assert not client.get(f'/investigations/{case}/workflow/reuse').json()['candidates']
    candidates = client.get(f'/investigations/{other}/workflow/reuse').json()['candidates']
    assert len(candidates) == 1
    assert candidates[0]['use'] == 'cautionary_experience'
    assert candidates[0]['verification']['result'] == 'recurred'
    assert candidates[0]['matched_fields'] == ['configuration']
    assert candidates[0]['automatic_recommendation'] is False
    assert verification(case, a, window_end=first.json()['window_end']).status_code == 422


def test_reuse_rejects_mismatch_and_preserves_unknown_actual_exposure():
    reset(); case = fresh(); other = fresh()
    a = action(case, decision(case).json()).json()
    assert verification(case, a, conditions={'configuration': 'different-L99', 'actual_exposure': 'loading-zone-B'}).status_code == 200
    candidate = client.get(f'/investigations/{other}/workflow/reuse').json()['candidates'][0]
    assert candidate['status'] == 'conditions_not_established'
    assert candidate['mismatched_fields'] == ['configuration']
    assert candidate['unknown_fields'] == ['actual_exposure']
    assert verification(case, a, conditions={}).status_code == 422
    assert verification(case, a, conditions={'unsupported_key': 'x'}).status_code == 422
    assert verification(other, a).status_code == 422


def test_verification_requires_completed_window_and_nonblank_input():
    reset(); case = fresh()
    assert client.post(f'/investigations/{case}/workflow/decisions', json={'option_id': 'inspect', 'owner': ' ', 'rationale': 'x', 'constraints': 'x'}).status_code == 422
    a = action(case, decision(case).json()).json()
    assert verification(case, a, window_end=(datetime.utcnow()+timedelta(days=1)).isoformat()).status_code == 422
    assert verification(case, a, window_start='2000-01-01').status_code == 422
