"""V1.1 transparent, deterministic decision support; no causal probabilities.

Configuration is context, never proof of actual operating exposure. Diagnostic
observations must explicitly attest exposure and measurement completeness.
"""
from datetime import datetime, timezone
from itertools import combinations
from uuid import uuid4

from sqlalchemy import select
from app.models import CaseEvent, DiagnosticCheck, Evidence, Hypothesis, Investigation, Outcome


def evaluate(db, investigation_id, known_at=None):
    from app.services import iso, latest_state
    inv = db.get(Investigation, investigation_id)
    if not inv:
        raise ValueError('investigation not found')
    cutoff = known_at or datetime.utcnow()
    trigger = db.get(Evidence, inv.trigger_id)
    context = latest_state(db, inv.asset_id, trigger.event_time, cutoff)
    events = list(db.scalars(select(CaseEvent).where(CaseEvent.investigation_id == investigation_id, CaseEvent.event_kind == 'diagnostic_evidence', CaseEvent.occurred_at <= cutoff).order_by(CaseEvent.occurred_at, CaseEvent.id)))
    evidence = list(db.scalars(select(Evidence).where(Evidence.known_at <= cutoff, Evidence.event_time <= cutoff, Evidence.verification_state == "verified", Evidence.superseded_by.is_(None))))
    peers = sorted({e.asset_id for e in evidence if latest_state(db, e.asset_id, trigger.event_time, cutoff).get('application_version') == context.get('application_version')})
    cohorts = []
    for asset in peers:
        state = latest_state(db, asset, trigger.event_time, cutoff)
        signals = [e for e in evidence if e.asset_id == asset and e.event_type == 'telemetry_anomaly']
        checks = [e for e in events if e.payload.get('asset_id') == asset and e.payload.get('zone') == 'loading-zone-B']
        observation = checks[-1].payload if checks else {}
        relation = observation.get('exposure_relation', 'same' if observation.get('exposure_verified') else 'unknown')
        exposed = observation.get('exposure_verified') is True and relation == 'same'
        complete = observation.get('telemetry_complete') is True
        matches = all(state.get(k) == context.get(k) and state.get(k) is not None for k in ('application_version', 'configuration', 'firmware_version', 'map_version'))
        healthy = observation.get('safe_stop') is False and complete and not any(e.payload.get("safe_stop") is True for e in checks)
        cohorts.append({'asset_id': asset, 'exposure_status': 'verified' if exposed else ('Different conditions verified' if relation == 'different' else 'Exposure not verified'), 'exposure_relation': relation, 'context': state, 'missing_fields': [k for k in ('application_version', 'configuration', 'firmware_version', 'map_version') if state.get(k) is None], 'valid_control': exposed and healthy and matches and not signals, 'signal_observed': bool(signals) or observation.get('safe_stop') is True, 'reason': 'Requires matching configuration, verified Zone B exposure and complete healthy telemetry; absence of an alert is insufficient.', 'confounders': [k for k in ('configuration', 'firmware_version', 'map_version') if state.get(k) != context.get(k)], 'evidence_ids': [e.id for e in signals] + [e.id for e in checks]})
    refs = {e.id: {'id': e.id, 'source': e.source, 'event_time': iso(e.event_time), 'known_at': iso(e.known_at), 'asset_id': e.asset_id, 'event_type': e.event_type} for e in evidence}
    refs.update({e.id: {'id': e.id, 'source': e.payload['source'], 'event_time': e.payload['event_time'], 'known_at': iso(e.occurred_at)} for e in events})
    challenges = list(db.scalars(select(CaseEvent).where(CaseEvent.investigation_id == investigation_id, CaseEvent.event_kind == 'hypothesis_challenged', CaseEvent.occurred_at <= cutoff)))
    hypotheses = []
    # A row's legacy evidence IDs are candidate links, never proof of a claim.
    # Validate the actual payload fields; configuration remains contextual.
    predicates = {
        'h-l4-zone-b': {'configuration': 'localization-L4', 'network_profile': 'loading-zone-B'},
        'h-l4-alone': {'configuration': 'localization-L4'},
        'h-lidar-53': {'firmware_version': 'lidar-fw-5.3'},
    }
    affected_assets = {c['asset_id'] for c in cohorts if c['signal_observed']}
    proposed = [c.id for c in db.scalars(select(DiagnosticCheck).where(DiagnosticCheck.investigation_id == investigation_id))]
    for row in db.scalars(select(Hypothesis).where(Hypothesis.investigation_id == investigation_id)):
        canonical_id = row.id.split('::')[-1]
        required_fields = predicates.get(canonical_id, {})
        context_records = [
            {**refs[e.id], 'matched_fields': {k: e.payload[k] for k in required_fields},
             'claim': 'Configuration metadata only; actual operating exposure is not established.'}
            for e in evidence if e.asset_id in affected_assets
            and e.event_type == 'configuration_change' and required_fields
            and all(e.payload.get(k) == v for k, v in required_fields.items())
        ]
        support, against = [], []
        status = 'possible' if context_records else 'insufficient_evidence'
        missing = ['Comparable operating exposure for the affected group and candidate controls',
                   'Complete observation windows', 'Matched workload and site conditions']
        if canonical_id == 'h-lidar-53':
            missing = ['A firmware-specific diagnostic check', 'Comparable operating conditions']
            if not context_records:
                missing.insert(0, 'Firmware metadata required')
        diagnostic_results = set()
        for event in events:
            obs = event.payload
            participant = next((c for c in cohorts if c['asset_id'] == obs.get('asset_id')), None)
            context_matches = participant is not None and all(
                participant['context'].get(k) is not None
                and participant['context'].get(k) == context.get(k)
                for k in ('application_version', 'configuration', 'firmware_version', 'map_version'))
            if not (context_matches and obs.get('exposure_verified') is True
                    and obs.get('exposure_relation', 'same') == 'same'
                    and obs.get('telemetry_complete') is True):
                continue
            if canonical_id in ('h-l4-zone-b', 'h-l4-alone'):
                diagnostic_results.add(obs['safe_stop'])
                (support if obs['safe_stop'] else against).append(refs[event.id])
                status = 'supported_not_proven' if obs['safe_stop'] else 'weakened'
                missing = ['Independent replication', 'Site and workload confounders remain unresolved']
        if len(diagnostic_results) > 1:
            status = 'conflicting_evidence'
            missing = ['Explain inconsistent diagnostic observations', 'Control workload, timing and site confounders']
        hypotheses.append({
            'id': row.id, 'label': row.label, 'status': status,
            'supporting_evidence': support, 'contradicting_evidence': against,
            'context_evidence': context_records, 'missing_evidence': missing,
            'evidence_strength': 'conflicting_observations' if len(diagnostic_results) > 1 else
                                 'diagnostic_observation' if diagnostic_results else 'context_only' if context_records else 'insufficient_evidence',
            'investigation_priority': 'high' if canonical_id == 'h-l4-zone-b' else 'normal',
            'priority_reason': 'Verify the missing exposure records first; priority is not causal confidence.' if canonical_id == 'h-l4-zone-b' else 'Keep as a competing explanation; direct diagnostic evidence is incomplete.',
            'review_status': 'challenged' if any(e.payload.get('hypothesis_id') == row.id for e in challenges) else 'open',
            'challenges': [e.payload for e in challenges if e.payload.get('hypothesis_id') == row.id],
            'non_discriminating_evidence': [refs[e.id] for e in evidence if e.event_type == 'deployment' and e.asset_id in peers],
            'possible_confounders': ['software configuration', 'operating zone', 'workload', 'site conditions'],
            'proposed_checks': proposed, 'would_change_conclusion': row.would_change_conclusion,
        })
    active = {h['id'].split('::')[-1] for h in hypotheses}
    checks = []
    for row in db.scalars(select(DiagnosticCheck).where(DiagnosticCheck.investigation_id == investigation_id)):
        outcomes = list((row.expected_observations or {}).values())
        # Count pairs of hypotheses whose predicted updates differ across outcomes.
        separated = set()
        for a, b in combinations(sorted(active), 2):
            signatures = [(o.get('belief_update', {}).get(a, 'unchanged'), o.get('belief_update', {}).get(b, 'unchanged')) for o in outcomes]
            if len(set(signatures)) > 1 and any(x != y for x, y in signatures):
                separated.add((a, b))
        minutes = max(float((row.estimated_cost or {}).get('time_minutes', 1)), 1)
        risk = {'low': 1, 'medium': 2, 'high': 4}.get((row.operational_risk or {}).get('level'), 4)
        score = len(separated) / (minutes * risk)
        check_events = [event for event in events if event.payload.get('check_id') == row.id]
        latest_observation = check_events[-1].payload if check_events else {}
        check_status = ('observed' if latest_observation.get('exposure_verified') and latest_observation.get('telemetry_complete') and latest_observation.get('exposure_relation', 'same') == 'same' else 'inconclusive') if check_events else 'recommended'
        checks.append({'id': row.id, 'question': row.question, 'status': check_status, 'score': score, 'separated_pairs': sorted(separated), 'priority': 'high' if separated and risk == 1 else 'review' if separated else 'low', 'feasible': row.check_type == 'read_only', 'cost': row.estimated_cost, 'risk': row.operational_risk, 'why': (f'First verify EX11 operating exposure: a healthy-looking machine is not a control until its conditions and telemetry window match. Estimated {minutes:g} minutes, {row.operational_risk.get("level", "unknown")} risk. This can challenge L4 + Zone B; it cannot by itself separate L4 alone from environmental interaction.' if row.check_type == 'read_only' else 'An intervention may test recovery, but changes physical state and requires approval; verify what else changed and measure a follow-up window. No calibrated information gain is claimed.'), 'required_approval': row.required_approval})
    checks.sort(key=lambda c: (-c['score'], c['id']))
    next_check = next((c for c in checks if c['status'] != 'observed' and c['score'] > 0 and c['feasible']), None)
    comparison_quality = {'valid_control_count': sum(c['valid_control'] for c in cohorts), 'sample_status': 'insufficient_for_causal_estimation', 'confounded_dimensions': ['localization configuration', 'operating zone'], 'conclusion': 'Current evidence cannot distinguish software regression from environmental interaction.', 'missing_data': ['EX11 operating exposure' ] if not any(c['asset_id'] == 'EX11' and c['exposure_relation'] != 'unknown' for c in cohorts) else ['Independent replication', 'Matched workload and observation windows']}
    configured_pair = lambda c: c['context'].get('configuration') == 'localization-L4' and c['context'].get('network_profile') == 'loading-zone-B'
    config_summary = {
        'affected_count': len(affected_assets), 'no_known_signal_count': len(cohorts) - len(affected_assets),
        'affected_configured_l4_zone_b': sum(c['signal_observed'] and configured_pair(c) for c in cohorts),
        'no_signal_configured_l4_zone_b': sum(not c['signal_observed'] and configured_pair(c) for c in cohorts),
        'verified_affected_exposure_count': sum(c['signal_observed'] and c['exposure_relation'] == 'same' for c in cohorts),
        'interpretation': 'Configuration records are not proof of comparable operating exposure.',
    }
    reference = {'scope': 'historical_reference', 'label': 'Historical reference outcome',
                 'reported_result': 'Rollback L4 → 3/3 recovered', 'source': 'Synthetic FC-021 narrative',
                 'verification': 'illustrative_not_independently_verified', 'used_in_current_analysis': False}
    timeline = [{**refs[e.id], 'label': e.event_type.replace('_', ' ')} for e in evidence
                if e.asset_id == inv.asset_id or e.event_type == 'telemetry_anomaly']
    return {'configuration_summary': config_summary, 'historical_reference': reference,
            'evidence': list(refs.values()), 'timeline': sorted(timeline, key=lambda e: (e['event_time'], e['id'])),
            'comparison_quality': comparison_quality, 'investigation_id': investigation_id, 'as_of': iso(cutoff), 'method': 'Deterministic evidence rules and diagnostic utility; causal inference not established', 'uncertainty': "We don't know yet, and here's what would help us find out.", 'cohorts': cohorts, 'hypotheses': hypotheses, 'checks': checks, 'next_check': next_check, 'diagnostic_events': [{'id': e.id, **e.payload} for e in events], 'conditional_precedents': [{'outcome_id': o.id, 'outcome': o.outcome, 'conditions': o.payload.get('applicable_conditions', []), 'verification': o.payload.get('validation_status', 'unverified'), 'verification_details': o.payload.get('intervention_verification', {}), 'automatic_recommendation': False} for o in db.scalars(select(Outcome).where(Outcome.investigation_id == investigation_id, Outcome.recorded_at <= cutoff))]}


def observe(db, investigation_id, check_id, observation):
    row = db.get(DiagnosticCheck, check_id)
    if not row or row.investigation_id != investigation_id:
        raise ValueError('check not found')
    if row.check_type != 'read_only':
        raise ValueError('Intervention requires a separately recorded human decision and action')
    relation = observation.get('exposure_relation', 'same' if observation.get('exposure_verified') else 'unknown')
    if relation not in ('unknown', 'same', 'different'):
        raise ValueError('exposure_relation must be unknown, same or different')
    if relation in ('same', 'different') and observation.get('exposure_verified') is not True:
        raise ValueError('Known exposure relation requires a verified exposure record')
    if type(observation.get('safe_stop')) is not bool:
        raise ValueError('safe_stop must be an explicit boolean; empty or inconclusive results cannot imply healthy')
    for field in ('exposure_verified', 'telemetry_complete'):
        if type(observation.get(field)) is not bool:
            raise ValueError(f'{field} must be an explicit boolean')
    for field in ('source', 'event_time', 'asset_id', 'zone'):
        if not isinstance(observation.get(field), str) or not observation[field].strip():
            raise ValueError(f'{field} is required')
    if observation['asset_id'] != row.procedure.get('asset_id') or observation['zone'] != row.procedure.get('route_segment'):
        raise ValueError('Observation does not match the diagnostic asset and route')
    observed = datetime.fromisoformat(observation['event_time'].replace('Z', '+00:00'))
    if observed.tzinfo:
        observed = observed.astimezone(timezone.utc).replace(tzinfo=None)
    if observed.replace(tzinfo=None) > datetime.utcnow():
        raise ValueError('event_time cannot be in the future')
    before = evaluate(db, investigation_id)
    event = CaseEvent(id=f'diag-{uuid4().hex}', investigation_id=investigation_id, event_kind='diagnostic_evidence', occurred_at=datetime.utcnow(), payload={**observation, "check_id": check_id, "exposure_relation": relation})
    db.add(event)
    db.flush()
    after = evaluate(db, investigation_id)
    row.actual_observation = dict(observation)
    row.belief_update = {h['id']: h['status'] for h in after['hypotheses']}
    row.status = 'observed' if relation == 'same' and observation['exposure_verified'] and observation['telemetry_complete'] else 'inconclusive'
    for item in after['hypotheses']:
        hypothesis = db.get(Hypothesis, item['id'])
        hypothesis.status = item['status']
        hypothesis.supporting_evidence = [e['id'] for e in item['supporting_evidence']]
        hypothesis.contradicting_evidence = [e['id'] for e in item['contradicting_evidence']]
        hypothesis.missing_evidence = item['missing_evidence']
        hypothesis.revision_history = [*hypothesis.revision_history, {'known_at': event.occurred_at.isoformat(), 'status': item['status'], 'reason': event.id}]
    db.add(CaseEvent(id=f'update-{uuid4().hex}', investigation_id=investigation_id, event_kind='hypothesis_reassessment', occurred_at=datetime.utcnow(), payload={'evidence_id': event.id, 'before': before['hypotheses'], 'after': after['hypotheses']}))
    db.commit()
    return evaluate(db, investigation_id)


def create_demo_session(db):
    """A visitor gets isolated diagnostic state; never reset shared case data."""
    from app.services import seed_field_case_reasoning
    template = 'inv-120-robots-bad-rollout'
    inv = db.get(Investigation, template)
    if not inv:
        raise ValueError('Synthetic reference case is not available')
    seed_field_case_reasoning(db, template)
    case_id = f'demo-session-{uuid4().hex}'
    db.add(Investigation(id=case_id, asset_id=inv.asset_id, trigger_id=inv.trigger_id, title='Synthetic investigation · EX11 exposure', status='active'))
    for h in db.scalars(select(Hypothesis).where(Hypothesis.investigation_id == template)):
        db.add(Hypothesis(id=f'{case_id}::{h.id}', investigation_id=case_id, label=h.label, status='unresolved', supporting_evidence=[], contradicting_evidence=[], missing_evidence=h.missing_evidence, would_change_conclusion=h.would_change_conclusion, revision_history=[]))
    for c in db.scalars(select(DiagnosticCheck).where(DiagnosticCheck.investigation_id == template)):
        db.add(DiagnosticCheck(id=f'{case_id}::{c.id}', investigation_id=case_id, question=c.question, procedure=c.procedure, expected_observations=c.expected_observations, estimated_cost=c.estimated_cost, operational_risk=c.operational_risk, why_informative=c.why_informative, required_approval=c.required_approval, check_type=c.check_type, rank_score=0))
    db.commit()
    return evaluate(db, case_id)
