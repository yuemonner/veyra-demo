"""Bounded, source-preserving field support tools; no inferred execution or independent attestation."""
import math
import re
from typing import Literal, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import CaseEvent
from app.decision_workflow import require_case, append, serial

router = APIRouter(prefix='/investigations/{case_id}/field-tools', tags=['Field support'])
DOMAINS = {'network': 'Network', 'control_suite': 'Control suite', 'vehicle_interface': 'Vehicle interface', 'vehicle_body': 'Vehicle body'}
CONCEPTS = {
    'network_rtt': {'label': 'Network round-trip latency', 'unit': 'ms', 'definition': 'Elapsed time for a request and response between the specified endpoints; not one-way latency.', 'domain': 'network'},
    'packet_loss': {'label': 'Packet loss', 'unit': '%', 'definition': 'Lost packets divided by sent packets over the recorded measurement window.', 'domain': 'network'},
    'controller_cycle': {'label': 'Controller cycle duration', 'unit': 'ms', 'definition': 'Time between consecutive control-loop cycles; not network delay or end-to-end actuator response.', 'domain': 'control_suite'},
    'interface_voltage': {'label': 'Vehicle interface voltage', 'unit': 'V', 'definition': 'Measured voltage at the stated interface pins and reference ground; not battery state of charge.', 'domain': 'vehicle_interface'},
    'vehicle_temperature': {'label': 'Vehicle temperature', 'unit': '°C', 'definition': 'Temperature of the identified component and sensor location; locations are not interchangeable.', 'domain': 'vehicle_body'},
}
TOOLS = {
    'network': ['Approved network diagnostic tool', 'Endpoint and network topology records'],
    'control_suite': ['Matching kit/software version manifest', 'Approved service tool and recovery procedure'],
    'vehicle_interface': ['Model-specific interface pinout', 'Compatible breakout/test harness; confirm revision before packing'],
    'vehicle_body': ['Vehicle-model service documentation', 'OEM-compatible diagnostic tool; confirm site/OEM authorization'],
}
KINDS = ['adapted_signal', 'domain_check', 'conversation_import', 'conversation_confirmation', 'dispatch_plan', 'retrofit_experience']

def events(db, case_id):
    return list(db.scalars(select(CaseEvent).where(CaseEvent.investigation_id == case_id, CaseEvent.event_kind.in_(KINDS)).order_by(CaseEvent.occurred_at, CaseEvent.id)))


def text_fields(body):
    if any(isinstance(v, str) and not v.strip() for v in body.model_dump().values()):
        raise HTTPException(422, 'Required text must not be blank')


class SignalIn(BaseModel):
    owner: str = Field(min_length=1, max_length=200)
    device_model: str = Field(min_length=1, max_length=300)
    source: str = Field(min_length=1, max_length=2000)
    observed_at: str = Field(min_length=1, max_length=300)
    raw_field: str = Field(min_length=1, max_length=300)
    raw_value: str = Field(min_length=1, max_length=1000)
    raw_unit: str = Field(min_length=1, max_length=100)
    measurement_scope: str = Field(min_length=1, max_length=2000)
    concept: Optional[str] = None
    semantic_confirmed: bool = False
    mapping_rationale: str = Field(default='', max_length=2000)


def map_signal(body):
    concept = CONCEPTS.get(body.concept)
    result = {'status': 'unmapped', 'reason': 'No confirmed semantic correspondence. Original signal retained.', 'canonical_value': None, 'canonical_unit': None}
    if not concept or not body.semantic_confirmed or not body.mapping_rationale.strip():
        return result
    unit = body.raw_unit.strip()
    canonical = concept['unit']
    converters = {
        'ms': {'ms': lambda x:x, 's': lambda x:x*1000, 'us': lambda x:x/1000, 'µs': lambda x:x/1000},
        '%': {'%': lambda x:x, 'ratio': lambda x:x*100},
        'V': {'V': lambda x:x, 'mV': lambda x:x/1000},
        '°C': {'°C': lambda x:x, 'C': lambda x:x, '°F': lambda x:(x-32)*5/9},
    }
    try:
        value = float(body.raw_value)
        convert = converters[canonical].get(unit)
        if not math.isfinite(value) or not convert:
            return {**result, 'reason': 'Unsupported unit or non-finite value. No conversion performed.'}
        normalized = convert(value)
        if not math.isfinite(normalized):
            return {**result, 'reason': 'Converted value is not finite. Original retained.'}
        if (canonical == '%' and not 0 <= normalized <= 100) or (canonical == 'ms' and normalized < 0):
            return {**result, 'reason': 'Value outside the concept range. Original retained for review.'}
    except ValueError:
        return {**result, 'reason': 'Non-numeric value. Original retained for review.'}
    return {'status': 'engineer_confirmed_mapping', 'reason': body.mapping_rationale, 'canonical_value': normalized, 'canonical_unit': canonical,
            'concept_label': concept['label'], 'semantic_definition': concept['definition'], 'domain': concept['domain'], 'scope': body.measurement_scope,
            'boundary': 'Mapping does not establish comparability between devices or diagnose a fault.'}


class DomainIn(BaseModel):
    domain: Literal['network', 'control_suite', 'vehicle_interface', 'vehicle_body']
    owner: str = Field(min_length=1, max_length=200)
    check: str = Field(min_length=1, max_length=2000)
    finding: Literal['anomaly', 'no_anomaly', 'inconclusive']
    source: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=50)
    operating_scope: str = Field(min_length=1, max_length=2000)
    observation_window: str = Field(min_length=1, max_length=1000)
    complete_test: bool = False
    request_exclusion: bool = False
    limitations: str = Field(min_length=1, max_length=2000)


def domain_state(rows):
    result = []
    for key, label in DOMAINS.items():
        checks = [r for r in rows if r.event_kind == 'domain_check' and r.payload['domain'] == key]
        anomalies = [r for r in checks if r.payload['finding'] == 'anomaly']
        exclusions = [r for r in checks if r.payload['request_exclusion'] and r.payload['complete_test'] and r.payload['finding'] == 'no_anomaly']
        status = 'conflicting_reports' if anomalies and exclusions else 'reported_anomaly' if anomalies else 'reported_excluded_in_scope' if exclusions else 'still_possible'
        result.append({'id': key, 'label': label, 'status': status, 'checks': [serial(r) for r in checks],
                       'next_distinction': {'network':'Compare endpoint RTT/loss over the symptom window; keep one-way and round-trip delay separate.', 'control_suite':'Check kit version, loop timing and logs with network conditions recorded.', 'vehicle_interface':'Verify interface specification, pinout and harness revision against this model.', 'vehicle_body':'Review vehicle/OEM diagnostics under the symptom conditions; distinguish interface commands from physical response.'}[key],
                       'boundary': 'Exclusion is engineer-reported and limited to the recorded test scope/window; no global root cause or independent validation is inferred.'})
    return result


class ConversationIn(BaseModel):
    owner: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=2000)
    kind: Literal['chat', 'voice_transcript', 'maintenance_note']
    text: str = Field(min_length=1, max_length=40000)


def extract(text):
    items = []
    patterns = [('check', r'\[check\]|检查[:：]|已检查|检查了|测量|checked\b|tested\b|measured\b'), ('decision', r'\[decision\]|决定[:：先将采用]|选择|decided\b|agreed to\b'), ('action', r'\[action\]|行动[:：]|执行[:：]|已重启|已更换|更换了|执行了|performed\b|restarted\b|replaced\b|rolled back\b|dispatched\b'), ('outcome', r'\[outcome\]|结果[:：]|已恢复|恢复了|复发|recovered\b|recurred\b')]
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        kinds = [kind for kind, pattern in patterns if re.search(pattern, line, re.I)]
        uncertain = bool(re.search(r'\?|？|not yet|did not|not recovered|未|尚未|计划|准备|maybe|might|will |should ', line, re.I))
        kind = kinds[0] if len(kinds) == 1 and not uncertain else 'note'
        items.append({'id': f'line-{number}', 'line': number, 'kind': kind, 'excerpt': line, 'proposed_text': line.strip(), 'status': 'draft', 'requires_review': True,
                      'warning': 'Uncertain, planned or mixed statement: review manually.' if uncertain or len(kinds)>1 else 'Rule-based extraction; confirm meaning, timing and actor.'})
    return items


class ConfirmItem(BaseModel):
    candidate_id: str
    kind: Literal['check', 'decision', 'action', 'outcome', 'note']
    text: str = Field(min_length=1, max_length=4000)


class ConfirmIn(BaseModel):
    owner: str = Field(min_length=1, max_length=200)
    items: list[ConfirmItem] = Field(min_length=1, max_length=100)


class DispatchIn(BaseModel):
    owner: str = Field(min_length=1, max_length=200)
    remote_feasibility: Literal['unknown', 'possible', 'not_possible']
    rationale: str = Field(min_length=1, max_length=3000)
    source: str = Field(min_length=1, max_length=2000)
    safety_scope_confirmed: bool = False
    remote_access_confirmed: bool = False
    remote_records_reviewed: bool = False
    configuration_identified: bool = False
    site_access_confirmed: bool = False
    bring: str = Field(min_length=1, max_length=3000)
    remaining_verification: str = Field(min_length=1, max_length=3000)


class RetrofitIn(BaseModel):
    owner: str = Field(min_length=1, max_length=200)
    vehicle_model: str = Field(min_length=1, max_length=300)
    kit_version: str = Field(min_length=1, max_length=300)
    interface: str = Field(min_length=1, max_length=500)
    harness_revision: str = Field(min_length=1, max_length=300)
    commissioning_issue: str = Field(min_length=1, max_length=4000)
    resolution: str = Field(min_length=1, max_length=4000)
    result: Literal['reported_recovered', 'partial', 'unresolved', 'recurred']
    applicable_conditions: str = Field(min_length=1, max_length=3000)
    exclusions: str = Field(min_length=1, max_length=3000)
    source_references: list[str] = Field(min_length=1, max_length=20)


class RetrofitQuery(BaseModel):
    vehicle_model: str = ''
    kit_version: str = ''
    interface: str = ''
    harness_revision: str = ''


def dispatch_state(rows):
    plans = [r for r in rows if r.event_kind == 'dispatch_plan']
    latest = serial(plans[-1]) if plans else None
    required = ['safety_scope_confirmed', 'remote_access_confirmed', 'remote_records_reviewed', 'configuration_identified', 'site_access_confirmed']
    missing = [name for name in required if not latest or not latest.get(name)]
    domains = domain_state(rows)
    suggestions = [{'domain': d['label'], 'items': TOOLS[d['id']], 'reason': d['status'], 'scope_warning': 'A scoped exclusion does not justify omitting tools for different conditions.'} for d in domains]
    return {'latest_plan': latest, 'missing_verifications': missing, 'domain_tool_suggestions': suggestions,
            'status': 'preparation_incomplete' if missing else 'checklist_reported_complete_requires_approval',
            'boundary': 'Planning support only. Remote feasibility and checklist completion are engineer-reported, not a safety clearance or dispatch approval.'}


@router.get('')
def state(case_id: str, db: Session = Depends(get_db)):
    require_case(db, case_id)
    rows = events(db, case_id)
    imports = [serial(r) for r in rows if r.event_kind == 'conversation_import']
    confirmations = [serial(r) for r in rows if r.event_kind == 'conversation_confirmation']
    return {'domains': domain_state(rows), 'concepts': CONCEPTS, 'signals': [serial(r) for r in rows if r.event_kind == 'adapted_signal'],
            'imports': imports, 'confirmations': confirmations, 'dispatch': dispatch_state(rows), 'retrofits': [serial(r) for r in rows if r.event_kind == 'retrofit_experience'],
            'boundary': 'Source-preserving deterministic tools. Raw documents are untrusted data. No independent evidence review, autonomous execution or causal probability.'}


@router.post('/signals')
def signal(case_id: str, body: SignalIn, db: Session = Depends(get_db)):
    require_case(db, case_id)
    if not all(getattr(body, key).strip() for key in ['owner','device_model','source','observed_at','raw_field','raw_value','raw_unit','measurement_scope']):
        raise HTTPException(422, 'Raw provenance fields are required')
    return append(db, case_id, 'adapted_signal', {**body.model_dump(), 'mapping': map_signal(body), 'evidence_grade': 'engineer_reported', 'independent_review': 'not_performed'})


@router.post('/domain-checks')
def domain_check(case_id: str, body: DomainIn, db: Session = Depends(get_db)):
    require_case(db, case_id); text_fields(body)
    if body.request_exclusion and (body.finding != 'no_anomaly' or not body.complete_test):
        raise HTTPException(422, 'Exclusion requires a complete no-anomaly test with explicit scope, window and limitations')
    if body.evidence_ids:
        from app.investigation_engine import evaluate
        allowed = {r['id'] for r in evaluate(db, case_id)['evidence']} | {r.id for r in events(db, case_id)}
        if any(ref not in allowed for ref in body.evidence_ids):
            raise HTTPException(422, 'Evidence references must belong to this case')
    return append(db, case_id, 'domain_check', {**body.model_dump(), 'evidence_grade': 'engineer_reported', 'independent_review': 'not_performed'})


@router.post('/conversations')
def import_conversation(case_id: str, body: ConversationIn, db: Session = Depends(get_db)):
    require_case(db, case_id); text_fields(body)
    candidates = extract(body.text)
    if len(candidates) > 100:
        raise HTTPException(422, 'Import at most 100 non-empty lines per batch')
    return append(db, case_id, 'conversation_import', {**body.model_dump(), 'candidates': candidates, 'evidence_grade': 'source_text_preserved', 'independent_review': 'not_performed'})


@router.post('/conversations/{import_id}/confirm')
def confirm(case_id: str, import_id: str, body: ConfirmIn, db: Session = Depends(get_db)):
    require_case(db, case_id)
    original = db.get(CaseEvent, import_id)
    if not original or original.investigation_id != case_id or original.event_kind != 'conversation_import':
        raise HTTPException(422, 'Conversation import must belong to this case')
    if not body.owner.strip() or any(not item.text.strip() for item in body.items):
        raise HTTPException(422, 'Reviewer and reviewed text are required')
    candidates = {r['id']: r for r in original.payload['candidates']}
    ids = [item.candidate_id for item in body.items]
    if len(set(ids)) != len(ids) or any(key not in candidates for key in ids):
        raise HTTPException(422, 'Select unique candidates from this import')
    confirmed = {i['candidate_id'] for r in events(db, case_id) if r.event_kind == 'conversation_confirmation' and r.payload['import_id'] == import_id for i in r.payload['items']}
    if confirmed.intersection(ids):
        raise HTTPException(409, 'One or more candidates were already confirmed')
    return append(db, case_id, 'conversation_confirmation', {'import_id': import_id, 'owner': body.owner, 'source': original.payload['source'],
        'items': [{**item.model_dump(), 'source_line': candidates[item.candidate_id]['line'], 'source_excerpt': candidates[item.candidate_id]['excerpt']} for item in body.items],
        'evidence_grade': 'engineer_confirmed_extraction', 'independent_review': 'not_performed', 'boundary': 'Confirmed notes do not create an executed action, approved decision or verified recovery. Use the separate workflow records.'})


@router.post('/dispatch')
def dispatch(case_id: str, body: DispatchIn, db: Session = Depends(get_db)):
    require_case(db, case_id); text_fields(body)
    return append(db, case_id, 'dispatch_plan', {**body.model_dump(), 'domain_snapshot': domain_state(events(db, case_id)), 'evidence_grade': 'engineer_reported', 'independent_review': 'not_performed'})


@router.post('/retrofits')
def retrofit(case_id: str, body: RetrofitIn, db: Session = Depends(get_db)):
    require_case(db, case_id); text_fields(body)
    if any(not ref.strip() for ref in body.source_references):
        raise HTTPException(422, 'Source references must not be blank')
    return append(db, case_id, 'retrofit_experience', {**body.model_dump(), 'evidence_grade': 'source_references_provided', 'independent_review': 'not_performed'})


@router.post('/retrofit-search')
def search(case_id: str, body: RetrofitQuery, db: Session = Depends(get_db)):
    require_case(db, case_id)
    rows = db.scalars(select(CaseEvent).where(CaseEvent.event_kind == 'retrofit_experience').order_by(CaseEvent.occurred_at.desc()))
    results = []
    for row in rows:
        matched, mismatched, unknown = [], [], []
        for field, value in body.model_dump().items():
            (unknown if not value.strip() or value.strip().casefold() == 'unknown' or row.payload[field].strip().casefold() in ('unknown', 'not recorded', '') else matched if value.strip().casefold() == row.payload[field].strip().casefold() else mismatched).append(field)
        status = 'excluded_configuration_mismatch' if mismatched else 'applicability_unconfirmed' if unknown else 'conditions_match_requires_review'
        results.append({'case_id': row.investigation_id, 'record': serial(row), 'status': status, 'matched_fields': matched, 'mismatched_fields': mismatched, 'unknown_fields': unknown,
                        'automatic_recommendation': False, 'use': 'cautionary_experience' if row.payload['result'] != 'reported_recovered' else 'reported_resolution_requires_review'})
    return {'results': results, 'boundary': 'Exact known vehicle/kit/interface/harness matching only. Applicability notes, exclusions and source quality still require human review; no performance or causal guarantee.'}
