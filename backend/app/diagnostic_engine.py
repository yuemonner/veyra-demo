"""Evidence-to-Diagnostic-Check Engine: explicit rules, gaps and constraints, no causal probabilities."""
import csv
import io
import json
import math
from typing import Literal, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import Asset, CaseEvent
from app.decision_workflow import require_case, append, serial
from app.field_capabilities import events as field_events, domain_state

router = APIRouter(prefix='/investigations/{case_id}/engineering', tags=['Engineering context and diagnostics'])
KINDS = ['machine_context_recorded', 'engineering_evidence_imported', 'engineering_change_recorded', 'diagnostic_assessment_recorded']
MATCH_FIELDS = ['oem', 'model', 'retrofit_version', 'environment', 'vehicle_interface', 'harness_revision']
UNKNOWN = ('', 'unknown', 'not recorded', '未知')


def rows(db, case_id):
    return list(db.scalars(select(CaseEvent).where(CaseEvent.investigation_id == case_id, CaseEvent.event_kind.in_(KINDS)).order_by(CaseEvent.occurred_at, CaseEvent.id)))


def machine_context(db, case_id, asset_id=None, cutoff=None):
    case = require_case(db, case_id)
    candidates = [r for r in rows(db, case_id) if r.event_kind == 'machine_context_recorded' and r.payload['asset_id'] == (asset_id or case.asset_id) and (cutoff is None or r.occurred_at <= cutoff)]
    return serial(candidates[-1]) if candidates else None


class ContextIn(BaseModel):
    owner: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=2000)
    asset_id: str
    oem: str = Field(min_length=1, max_length=300)
    model: str = Field(min_length=1, max_length=300)
    retrofit_version: str = Field(min_length=1, max_length=300)
    environment: str = Field(min_length=1, max_length=1000)
    vehicle_interface: str = Field(min_length=1, max_length=500)
    harness_revision: str = Field(min_length=1, max_length=300)
    control_software: str = Field(min_length=1, max_length=300)
    network_configuration: str = Field(min_length=1, max_length=1000)
    location: str = Field(min_length=1, max_length=1000)
    customer_impact: str = Field(min_length=1, max_length=3000)
    remote_status: Literal['unknown', 'accessible', 'unavailable'] = 'unknown'
    remote_diagnostic_status: Literal['unknown', 'not_started', 'in_progress', 'awaiting_data', 'completed', 'blocked'] = 'unknown'
    dispatch_risk: str = Field(min_length=1, max_length=2000)
    exposure_scope: str = Field(min_length=1, max_length=2000)
    observation_window: str = Field(min_length=1, max_length=1000)
    window_complete: bool = False
    signal_state: Literal['unknown', 'stop_reported', 'healthy_observed'] = 'unknown'


class EvidenceIn(BaseModel):
    owner: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=2000)
    asset_id: str
    evidence_type: Literal['controller_log', 'network_metrics', 'fault_codes', 'maintenance_report', 'chat_transcript']
    format: Literal['text', 'csv', 'json']
    content: str = Field(min_length=1, max_length=40000)


def finite_number(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('Non-finite numbers are not valid evidence JSON')
    return number


def parse_evidence(body):
    if body.format == 'csv':
        reader = csv.DictReader(io.StringIO(body.content))
        headers = reader.fieldnames or []
        if not headers or any(not str(h).strip() for h in headers) or len(headers) != len(set(headers)) or len(headers)>60:
            raise HTTPException(422, 'CSV requires unique non-empty headers (at most 60 columns)')
        parsed = list(reader)
        if any(None in row for row in parsed):
            raise HTTPException(422, 'CSV row has extra cells; correct the source before import')
    elif body.format == 'json':
        try:
            parsed = json.loads(body.content, parse_float=finite_number, parse_constant=finite_number)
        except (ValueError, RecursionError):
            raise HTTPException(422, 'Invalid/non-finite JSON; original source was not imported')
        if isinstance(parsed, dict):
            parsed = [parsed]
        if not isinstance(parsed, list) or not all(isinstance(row, dict) for row in parsed):
            raise HTTPException(422, 'JSON must be an object or an array of objects')
    else:
        parsed = [{'line': i, 'original_text': line} for i,line in enumerate(body.content.splitlines(),1) if line.strip()]
    if not parsed or len(parsed)>100:
        raise HTTPException(422, 'Import 1–100 records per batch')
    return parsed


class ChangeIn(BaseModel):
    owner: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=2000)
    asset_id: str
    change_type: Literal['retrofit_version', 'control_software', 'vehicle_interface', 'harness_revision', 'network_configuration']
    before: str = Field(min_length=1, max_length=2000)
    after: str = Field(min_length=1, max_length=2000)
    effective_time: str = Field(min_length=1, max_length=300)
    status: Literal['planned', 'reported_applied']
    constraints: str = Field(min_length=1, max_length=2000)


class AssessmentIn(BaseModel):
    owner: str = Field(min_length=1, max_length=200)
    fault_domain: Literal['unknown', 'network', 'control_suite', 'vehicle_interface', 'vehicle_body']
    domain_status: Literal['unresolved', 'suspected', 'engineer_reported_isolated']
    rationale: str = Field(min_length=1, max_length=4000)
    root_cause_status: Literal['unresolved', 'engineer_reported_confirmed'] = 'unresolved'
    root_cause: Optional[str] = Field(default=None, max_length=3000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=50)
    source: str = Field(min_length=1, max_length=2000)
    limitations: str = Field(min_length=1, max_length=3000)


def validate_text(body):
    if any(isinstance(value,str) and not value.strip() for value in body.model_dump().values()):
        raise HTTPException(422, 'Text must be non-empty; record unknown values explicitly')


def asset_required(db, asset_id):
    if not db.get(Asset, asset_id):
        raise HTTPException(422, 'Select a recorded machine')


def compare_fleet(db, case_id, target):
    contexts = {}
    for row in rows(db, case_id):
        if row.event_kind == 'machine_context_recorded':
            contexts[row.payload['asset_id']] = serial(row)
    base = contexts.get(target)
    result = []
    for asset_id, context in contexts.items():
        if asset_id == target:
            continue
        matched, mismatch, missing = [], [], []
        for key in MATCH_FIELDS:
            left = (base or {}).get(key, 'unknown').strip().casefold()
            right = context.get(key, 'unknown').strip().casefold()
            (missing if left in UNKNOWN or right in UNKNOWN else matched if left == right else mismatch).append(key)
        exposure_match = bool(base and base['exposure_scope'].strip().casefold() not in UNKNOWN and base['exposure_scope'].strip().casefold() == context['exposure_scope'].strip().casefold())
        complete = bool(base and base['window_complete'] and context['window_complete'] and base['observation_window'].strip().casefold() not in UNKNOWN and context['observation_window'].strip().casefold() not in UNKNOWN)
        healthy = context['signal_state'] == 'healthy_observed'
        valid = bool(base and not mismatch and not missing and exposure_match and complete and healthy and base['signal_state']=='stop_reported')
        result.append({'asset_id':asset_id, 'context':context, 'matched':matched, 'mismatched':mismatch, 'missing':missing, 'exposure_match':exposure_match,
                       'complete_windows':complete, 'valid_reported_control':valid, 'reason':'Requires known matching machine context, matched reported exposure, complete windows, an affected target and a reported healthy comparator.'})
    return {'target_asset_id':target, 'target_context':base, 'machines':result, 'valid_control_count':sum(r['valid_reported_control'] for r in result),
            'comparison_status':'reported_comparable_controls_available' if any(r['valid_reported_control'] for r in result) else 'no_valid_control_do_not_force_comparison',
            'boundary':'Engineering reports, not independent telemetry attestation. An unknown/mismatched model, kit, environment or operating exposure blocks a valid comparison.'}


CATALOG = [
    {'id':'check-network-window','domain':'network','label':'Review endpoint RTT and loss in the symptom window','concepts':['network_rtt','packet_loss'],'evidence_types':['network_metrics'], 'separates':['network','control_suite'], 'remote_mode':'read_only_if_access_confirmed','personnel':'Remote network / controls engineer','risk':'low for existing read-only records; access authorization required','approval_required':False,'expected':'An aligned network anomaly keeps network implicated; clean complete endpoint records only constrain the measured path and window.'},
    {'id':'check-control-cycle','domain':'control_suite','label':'Align controller logs, cycle time and command timing','concepts':['controller_cycle'],'evidence_types':['controller_log'], 'separates':['control_suite','vehicle_interface'], 'remote_mode':'read_only_if_access_confirmed','personnel':'Control kit engineer','risk':'low for logs review; changing software/state requires separate approval','approval_required':False,'expected':'Controller timing anomalies versus clean outputs help separate control-suite processing from downstream interface behavior; do not infer actuator health.'},
    {'id':'check-interface-revision','domain':'vehicle_interface','label':'Verify interface specification, harness revision and observed interface signals','concepts':['interface_voltage'],'evidence_types':['fault_codes','maintenance_report'], 'separates':['vehicle_interface','vehicle_body'], 'remote_mode':'records_remote_physical_verification_may_require_onsite','personnel':'Vehicle interface engineer / authorized field technician','risk':'physical inspection requires OEM/site procedure, safe access and authorization','approval_required':True,'expected':'Matching interface command/feedback and model-specific harness checks can distinguish interface faults from vehicle response; unknown pinouts prevent a safe physical test.'},
    {'id':'check-vehicle-oem','domain':'vehicle_body','label':'Review OEM fault codes and model-specific service findings','concepts':['vehicle_temperature'],'evidence_types':['fault_codes','maintenance_report'], 'separates':['vehicle_body','vehicle_interface'], 'remote_mode':'records_remote_onsite_requirements_unknown','personnel':'Authorized OEM / vehicle service engineer','risk':'vehicle inspection or operation needs approved procedures and site authorization','approval_required':True,'expected':'OEM-supported findings may narrow the physical subsystem. A fault code alone is not a confirmed failed part or permission to replace it.'},
]


def evaluate_checks(db, case_id):
    case = require_case(db, case_id)
    saved = rows(db, case_id)
    field = field_events(db, case_id)
    domains = domain_state(field)
    profile = machine_context(db, case_id)
    imports = [r for r in saved if r.event_kind=='engineering_evidence_imported' and r.payload['asset_id']==case.asset_id]
    mapped = [r for r in field if r.event_kind=='adapted_signal' and r.payload['mapping']['status']=='engineer_confirmed_mapping']
    assessment_rows = [r for r in saved if r.event_kind=='diagnostic_assessment_recorded']
    assessment = serial(assessment_rows[-1]) if assessment_rows else {'fault_domain':'unknown','domain_status':'unresolved','root_cause_status':'unresolved','root_cause':None}
    checks = []
    for template in CATALOG:
        state = next(d for d in domains if d['id']==template['domain'])
        # A model name alone cannot establish that a signal is from this asset.
        signal_refs = [r.id for r in mapped if r.payload.get('asset_id') == case.asset_id and r.payload['concept'] in template['concepts']]
        raw_refs = [r.id for r in imports if r.payload['evidence_type'] in template['evidence_types']]
        gaps = []
        if not raw_refs: gaps.append('No source import for: '+', '.join(template['evidence_types']))
        if not signal_refs: gaps.append('No asset-linked confirmed semantic mapping for: '+', '.join(template['concepts']))
        if not profile:
            gaps.append('Machine model, kit, interface and operating context not recorded')
        else:
            missing_context = [key for key in MATCH_FIELDS if profile.get(key, 'unknown').strip().casefold() in UNKNOWN]
            if missing_context: gaps.append('Unknown machine context: '+', '.join(missing_context))
            if not profile['window_complete'] or profile['exposure_scope'].strip().casefold() in UNKNOWN:
                gaps.append('Complete symptom-window and operating exposure are not established')
        if raw_refs: gaps.append('Imported source timing and quality still require engineer review')
        remote = 'access_not_confirmed' if not profile or profile['remote_status']!='accessible' else template['remote_mode']
        priority = 'review_conflict_first' if state['status']=='conflicting_reports' else 'investigate_reported_anomaly' if state['status']=='reported_anomaly' else 'scope_review_before_repeating' if state['status']=='reported_excluded_in_scope' else 'verify_reported_domain' if assessment['fault_domain']==template['domain'] and assessment['domain_status'] in ('suspected','engineer_reported_isolated') else 'close_evidence_gap'
        checks.append({**template, 'rule_id':'domain-evidence-gap-v1:'+template['domain'], 'priority':priority, 'available_evidence_ids':raw_refs+signal_refs+[r['id'] for r in state['checks']],
                       'missing_evidence':gaps, 'remote_feasibility':remote, 'domain_status':state['status'], 'reason':f"Domain status: {state['status']}. This check distinguishes {' vs '.join(template['separates'])}; {len(gaps)} source/context gaps remain.",
                       'boundary':'Diagnostic priority is not operational action priority; no root-cause probability is estimated.'})
    order = {'review_conflict_first':0, 'investigate_reported_anomaly':1,'close_evidence_gap':3,'verify_reported_domain':2,'scope_review_before_repeating':4}
    checks.sort(key=lambda c:(order[c['priority']],c['id']))
    return {'method':'Evidence-to-Diagnostic-Check Engine · explicit rules v1', 'machine_context':profile, 'assessment':assessment, 'checks':checks,
            'boundary':'Fault domain and root cause are separate human assessments. Rules explain available inputs, missing evidence, differentiating checks, personnel, remote constraints and risk.'}


@router.get('')
def state(case_id: str, db: Session = Depends(get_db)):
    case = require_case(db, case_id)
    saved = rows(db, case_id)
    return {'asset_id':case.asset_id, 'assets':[{'id':a.id,'name':a.name} for a in db.scalars(select(Asset).order_by(Asset.id))],
            'context':machine_context(db,case_id), 'contexts':[serial(r) for r in saved if r.event_kind=='machine_context_recorded'],
            'evidence_imports':[serial(r) for r in saved if r.event_kind=='engineering_evidence_imported'], 'changes':[serial(r) for r in saved if r.event_kind=='engineering_change_recorded'],
            'fleet':compare_fleet(db,case_id,case.asset_id), 'diagnostics':evaluate_checks(db,case_id)}


@router.post('/context')
def save_context(case_id: str, body: ContextIn, db: Session = Depends(get_db)):
    require_case(db,case_id);asset_required(db,body.asset_id);validate_text(body)
    if body.source.strip().casefold() in UNKNOWN:
        raise HTTPException(422, 'Machine context requires an actual source reference')
    if body.signal_state=='healthy_observed' and (not body.window_complete or body.exposure_scope.strip().casefold() in UNKNOWN or body.observation_window.strip().casefold() in UNKNOWN):
        raise HTTPException(422,'Reported healthy comparison requires a complete observation window and explicit exposure scope')
    return append(db,case_id,'machine_context_recorded',{**body.model_dump(),'evidence_grade':'engineer_reported','independent_review':'not_performed'})


@router.post('/evidence')
def save_evidence(case_id: str, body: EvidenceIn, db: Session = Depends(get_db)):
    require_case(db,case_id);asset_required(db,body.asset_id);validate_text(body)
    parsed = parse_evidence(body)
    return append(db,case_id,'engineering_evidence_imported',{**body.model_dump(),'records':parsed,'data_quality':'source_preserved_unreviewed','semantic_mapping':'not_inferred','independent_review':'not_performed'})


@router.post('/changes')
def save_change(case_id: str, body: ChangeIn, db: Session = Depends(get_db)):
    require_case(db,case_id);asset_required(db,body.asset_id);validate_text(body)
    return append(db,case_id,'engineering_change_recorded',{**body.model_dump(),'evidence_grade':'engineer_reported','context_automatically_updated':False})


@router.post('/assessments')
def save_assessment(case_id: str, body: AssessmentIn, db: Session = Depends(get_db)):
    require_case(db,case_id);validate_text(body)
    from app.investigation_engine import evaluate
    allowed = {e['id'] for e in evaluate(db,case_id)['evidence']}
    if any(ref not in allowed for ref in body.evidence_ids):
        raise HTTPException(422,'Evidence references must belong to the current case view')
    if body.fault_domain=='unknown' and body.domain_status!='unresolved':
        raise HTTPException(422,'An unknown domain cannot be isolated or suspected')
    if body.domain_status=='engineer_reported_isolated' and not body.evidence_ids:
        raise HTTPException(422,'Scoped fault-domain isolation requires evidence references')
    if body.root_cause_status=='engineer_reported_confirmed' and (not body.root_cause or not body.evidence_ids):
        raise HTTPException(422,'A reported root cause needs a separate explanation and source evidence')
    return append(db,case_id,'diagnostic_assessment_recorded',{**body.model_dump(),'evidence_grade':'engineer_reported','independent_review':'not_performed'})
