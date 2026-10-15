"""Human decision workflow. Append-only records; no machine commands are executed."""
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import CaseEvent, Investigation
from app.investigation_engine import evaluate

router = APIRouter(prefix='/investigations/{case_id}/workflow', tags=['Decision workflow'])
OPTIONS = [
    {'id': 'rollback', 'label': 'Roll back localization L4', 'goal': 'Attempt service recovery', 'tradeoff': 'Changes the machine state and may remove diagnostic evidence. Recovery is not guaranteed.', 'constraint': 'Requires engineering approval, a compatible rollback and a recovery plan.', 'time_cost': 'Not estimated; confirm with the team', 'approval_required': True},
    {'id': 'inspect', 'label': 'Inspect EX11 operating exposure', 'goal': 'Reduce uncertainty before intervention', 'tradeoff': 'Read-only records review; does not restore the three stopped machines.', 'constraint': 'Run records and a complete observation window must be available.', 'time_cost': '15 min illustrative check estimate; confirm locally', 'approval_required': False},
    {'id': 'dispatch', 'label': 'Dispatch a field engineer', 'goal': 'Enable on-site diagnosis and intervention', 'tradeoff': 'Requires travel and field capacity; findings and recovery remain uncertain.', 'constraint': 'Confirm availability, site access and authorization.', 'time_cost': 'Travel, cost and availability unknown', 'approval_required': True},
]
FIELDS = {'application_version', 'configuration', 'firmware_version', 'map_version', 'network_profile', 'site', 'actual_exposure'}

class DecisionInput(BaseModel):
    option_id: Literal['rollback', 'inspect', 'dispatch']
    owner: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, max_length=4000)
    constraints: str = Field(min_length=1, max_length=4000)
    approval: Literal['pending', 'approved', 'not_required'] = 'pending'

class ActionInput(BaseModel):
    decision_id: str
    owner: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    scope: str = Field(min_length=1, max_length=2000)
    source: str = Field(min_length=1, max_length=2000)
    executed_at: datetime
    deviation_reason: str = Field(min_length=1, max_length=2000)

class VerificationInput(BaseModel):
    action_id: str
    owner: str = Field(min_length=1, max_length=200)
    result: Literal['recovered', 'partially_recovered', 'not_recovered', 'recurred', 'unknown']
    summary: str = Field(min_length=1, max_length=4000)
    window_start: datetime
    window_end: datetime
    source: str = Field(min_length=1, max_length=2000)
    remaining_unknowns: str = Field(min_length=1, max_length=4000)
    conditions: dict[str, str]
    exclusions: str = Field(min_length=1, max_length=4000)


def utc(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def require_case(db, case_id):
    case = db.get(Investigation, case_id)
    if not case:
        raise HTTPException(404, 'Case not found')
    return case


def records(db, case_id):
    return list(db.scalars(select(CaseEvent).where(CaseEvent.investigation_id == case_id, CaseEvent.event_kind.in_(['workflow_decision', 'workflow_action', 'workflow_verification'])).order_by(CaseEvent.occurred_at, CaseEvent.id)))


def serial(row):
    return {'id': row.id, 'recorded_at': row.occurred_at.isoformat(), **row.payload}


def linked(db, case_id, record_id, kind):
    row = db.get(CaseEvent, record_id)
    if not row or row.investigation_id != case_id or row.event_kind != kind:
        raise HTTPException(422, 'Linked record must belong to this case and have the required type')
    return row


def append(db, case_id, kind, payload):
    row = CaseEvent(id=f'wf-{uuid4().hex}', investigation_id=case_id, event_kind=kind, occurred_at=datetime.utcnow(), payload=payload)
    db.add(row)
    db.commit()
    return serial(row)


def nonblank(body):
    if any(isinstance(v, str) and not v.strip() for v in body.model_dump().values()):
        raise HTTPException(422, 'Text fields must not be blank')


def context(db, case_id):
    case = require_case(db, case_id)
    analysis = evaluate(db, case_id)
    peer = next((c for c in analysis['cohorts'] if c['asset_id'] == case.asset_id), None)
    current = dict(peer['context']) if peer else {}
    # A configuration label is never an actual exposure assertion.
    current['actual_exposure'] = 'unknown'
    return analysis, current


@router.get('')
def get_workflow(case_id: str, db: Session = Depends(get_db)):
    require_case(db, case_id)
    rows = records(db, case_id)
    analysis, current = context(db, case_id)
    return {'case_id': case_id, 'options': OPTIONS,
            'decisions': [serial(r) for r in rows if r.event_kind == 'workflow_decision'],
            'actions': [serial(r) for r in rows if r.event_kind == 'workflow_action'],
            'verifications': [serial(r) for r in rows if r.event_kind == 'workflow_verification'],
            'context': {k: current.get(k, 'unknown') for k in sorted(FIELDS)},
            'affected_count': analysis['configuration_summary']['affected_count'],
            'boundary': 'Human-reported records. No commands are sent to machines; recovery is not causal proof.'}


@router.post('/decisions')
def decide(case_id: str, body: DecisionInput, db: Session = Depends(get_db)):
    nonblank(body)
    analysis, current = context(db, case_id)
    option = next(o for o in OPTIONS if o['id'] == body.option_id)
    if option['approval_required'] and body.approval == 'not_required':
        raise HTTPException(422, 'This option requires approval')
    return append(db, case_id, 'workflow_decision', {**body.model_dump(), 'selected_option': option, 'options_considered': OPTIONS,
        'evidence_snapshot': analysis, 'context_at_decision': {k: current.get(k, 'unknown') for k in sorted(FIELDS)},
        'status': 'decision_recorded_action_not_confirmed'})


@router.post('/actions')
def act(case_id: str, body: ActionInput, db: Session = Depends(get_db)):
    nonblank(body)
    decision = linked(db, case_id, body.decision_id, 'workflow_decision')
    if decision.payload['selected_option']['approval_required'] and decision.payload['approval'] != 'approved':
        raise HTTPException(422, 'Record an approved decision before reporting this action')
    executed = utc(body.executed_at)
    if executed > datetime.utcnow() or executed < decision.occurred_at:
        raise HTTPException(422, 'Execution must be after the decision and not in the future')
    return append(db, case_id, 'workflow_action', {**body.model_dump(mode='json'), 'executed_at': executed.isoformat(), 'status': 'human_reported_execution'})


@router.post('/verifications')
def verify(case_id: str, body: VerificationInput, db: Session = Depends(get_db)):
    nonblank(body)
    action = linked(db, case_id, body.action_id, 'workflow_action')
    start, end = utc(body.window_start), utc(body.window_end)
    if start < datetime.fromisoformat(action.payload['executed_at']) or end <= start or end > datetime.utcnow():
        raise HTTPException(422, 'A completed verification window must follow execution')
    previous = [r for r in records(db, case_id) if r.event_kind == 'workflow_verification' and r.payload['action_id'] == body.action_id]
    if previous and end <= max(datetime.fromisoformat(r.payload['window_end']) for r in previous):
        raise HTTPException(422, 'A verification update must extend the observation window')
    if not body.conditions or set(body.conditions) - FIELDS or any(not v.strip() or v.strip().lower() == 'unknown' for v in body.conditions.values()):
        raise HTTPException(422, 'Explicit known applicability conditions are required; use the supported context fields')
    return append(db, case_id, 'workflow_verification', {**body.model_dump(mode='json'), 'window_start': start.isoformat(), 'window_end': end.isoformat(), 'decision_id': action.payload['decision_id'], 'verification_status': 'human_reported_source_requires_review', 'causal_attribution': 'not_established'})


@router.get('/reuse')
def reuse(case_id: str, db: Session = Depends(get_db)):
    _, current = context(db, case_id)
    candidates = list(db.scalars(select(CaseEvent).where(CaseEvent.event_kind == 'workflow_verification', CaseEvent.investigation_id != case_id).order_by(CaseEvent.occurred_at.desc(), CaseEvent.id)))
    # Latest verification per action governs reuse; a recurrence supersedes recovery.
    latest = {}
    for row in candidates:
        latest.setdefault(row.payload['action_id'], row)
    matches = []
    for row in latest.values():
        v = row.payload
        matched, mismatched, unknown = [], [], []
        for key, value in v['conditions'].items():
            actual = current.get(key)
            (unknown if actual in (None, '', 'unknown') else matched if actual == value else mismatched).append(key)
        eligible = not mismatched and not unknown
        action = linked(db, row.investigation_id, v['action_id'], 'workflow_action')
        decision = linked(db, row.investigation_id, v['decision_id'], 'workflow_decision')
        matches.append({'source_case_id': row.investigation_id, 'verification': serial(row), 'action': serial(action),
            'decision': {'id': decision.id, 'owner': decision.payload['owner'], 'rationale': decision.payload['rationale'], 'selected_option': decision.payload['selected_option']},
            'status': 'candidate_for_review' if eligible else 'conditions_not_established', 'matched_fields': matched, 'mismatched_fields': mismatched, 'unknown_fields': unknown,
            'use': 'cautionary_experience' if v['result'] != 'recovered' else 'reported_recovery_requires_review', 'automatic_recommendation': False})
    return {'candidates': matches, 'boundary': 'Conditions are checked against recorded context, not configuration-inferred exposure. Exclusions and evidence sources require human review. No automatic fix recommendation.'}
