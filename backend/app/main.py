import os
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db, init_db
from app.models import Asset, Decision, DecisionPackage, Evidence, Investigation, Outcome
from app.services import attach_human_decision, build_package, compare, compare_runs, create_field_case, decision_context, diagnostic_checks, evidence_lineage, generate_recording_report, get_field_case, hypotheses_state, ingest_recording, inject_late_evidence, investigate_imported_data, list_runs, precedent_comparison, reconstruct, record_check_observation, record_cost_impact, replay_case, reset_demo, save_imported_investigation, seal_package, search_recorded_events, serialize_evidence, serialize_field_case, similar_memory, verify_package

from app.decision_workflow import router as workflow_router

app = FastAPI(title="Veyra Field Decision Support", version="1.5.0")
app.include_router(workflow_router)
from app.field_capabilities import router as field_router
app.include_router(field_router)
from app.diagnostic_engine import router as diagnostic_router
app.include_router(diagnostic_router)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class EvidenceIn(BaseModel):
    id: Optional[str] = None
    asset_id: str
    event_type: str
    event_time: datetime
    known_at: Optional[datetime] = None
    ingested_at: Optional[datetime] = None
    source: str
    source_id: str = ""
    source_type: str = ""
    original_reference: str = ""
    linkage_method: str = "explicit_reference"
    verification_state: str = "verified"
    evidence_class: str
    payload: dict = {}
    confidence: float = 1.0
    provenance: dict = {}
    supporting_references: list = []
    unresolved_assumptions: list = []


class InvestigationIn(BaseModel):
    asset_id: str
    trigger_id: str
    title: str


class FieldCaseIn(BaseModel):
    title: str
    asset_id: str = "unknown"
    trigger_payload: dict = {}


class DecisionIn(BaseModel):
    decision: str
    owner: str = "Engineering"
    rationale: str = ""
    package_id: Optional[str] = None


class OutcomeIn(BaseModel):
    outcome: str
    payload: dict = {}


class CheckObservationIn(BaseModel):
    observation: dict = {}


class RecordingInvestigationIn(BaseModel):
    question: str
    run_ids: list[str] = []
    field_case_id: Optional[str] = None


class SaveRecordingInvestigationIn(BaseModel):
    title: str
    question: str
    run_ids: list[str]
    annotations: list = []
    field_case_id: Optional[str] = None


@app.on_event("startup")
def startup():
    init_db()
    if os.getenv("VEYRA_AUTO_SEED", "").lower() in {"1", "true", "yes"}:
        db = next(get_db())
        try:
            has_investigation = db.scalar(select(Investigation.id).limit(1))
            if not has_investigation:
                reset_demo(db)
        finally:
            db.close()


@app.get("/")
def root():
    return {
        "service": "veyra-demo-api",
        "status": "ok",
        "docs": "/docs",
        "demo_investigation": "/investigations/inv-120-robots-bad-rollout",
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/field-cases")
def create_case(body: FieldCaseIn, db: Session = Depends(get_db)):
    return create_field_case(db, body.title, body.asset_id, body.trigger_payload)


@app.get("/field-cases/{field_case_id}")
def read_field_case(field_case_id: str, db: Session = Depends(get_db)):
    try:
        return serialize_field_case(db, get_field_case(db, field_case_id))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/field-cases/{field_case_id}/evidence-files")
async def upload_field_case_evidence(field_case_id: str, file: UploadFile = File(...), description: str = Form(""), db: Session = Depends(get_db)):
    content = await file.read()
    if not content:
        raise HTTPException(400, "uploaded file is empty")
    try:
        return ingest_recording(db, file.filename or "recording", content, description, field_case_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/field-cases/{field_case_id}/evidence/search")
def field_case_evidence_search(
    field_case_id: str,
    asset_id: Optional[str] = None,
    event_type: Optional[str] = None,
    software_version: Optional[str] = None,
    query: Optional[str] = None,
    db: Session = Depends(get_db),
):
    return search_recorded_events(db, asset_id=asset_id, event_type=event_type, software_version=software_version, query=query, field_case_id=field_case_id)


@app.get("/field-cases/{field_case_id}/compare")
def field_case_compare(field_case_id: str, run_id: list[str] = Query(default=[]), db: Session = Depends(get_db)):
    return compare_runs(db, run_id or None, field_case_id)


@app.post("/field-cases/{field_case_id}/investigate")
def field_case_investigate(field_case_id: str, body: RecordingInvestigationIn, db: Session = Depends(get_db)):
    return investigate_imported_data(db, body.question, body.run_ids or None, field_case_id)


@app.post("/field-cases/{field_case_id}/report")
def field_case_report(field_case_id: str, body: RecordingInvestigationIn, db: Session = Depends(get_db)):
    return generate_recording_report(db, body.question, body.run_ids or None, field_case_id)


@app.post("/field-cases/{field_case_id}/saved-investigations")
def create_field_case_saved_investigation(field_case_id: str, body: SaveRecordingInvestigationIn, db: Session = Depends(get_db)):
    return save_imported_investigation(db, body.title, body.question, body.run_ids, body.annotations, field_case_id)


@app.post("/recordings/upload")
async def upload_recording(file: UploadFile = File(...), description: str = Form(""), field_case_id: Optional[str] = Form(None), db: Session = Depends(get_db)):
    content = await file.read()
    if not content:
        raise HTTPException(400, "uploaded file is empty")
    return ingest_recording(db, file.filename or "recording", content, description, field_case_id)


@app.get("/runs")
def runs(db: Session = Depends(get_db)):
    return {"runs": list_runs(db)}


@app.get("/recorded-events/search")
def recorded_events_search(
    asset_id: Optional[str] = None,
    event_type: Optional[str] = None,
    software_version: Optional[str] = None,
    query: Optional[str] = None,
    db: Session = Depends(get_db),
):
    return search_recorded_events(db, asset_id=asset_id, event_type=event_type, software_version=software_version, query=query)


@app.get("/runs/compare")
def runs_compare(run_id: list[str] = Query(default=[]), db: Session = Depends(get_db)):
    if len(run_id) < 2:
        raise HTTPException(400, "provide at least two run_id query parameters")
    return compare_runs(db, run_id)


@app.post("/recordings/investigate")
def recordings_investigate(body: RecordingInvestigationIn, db: Session = Depends(get_db)):
    return investigate_imported_data(db, body.question, body.run_ids or None, body.field_case_id)


@app.post("/recordings/report")
def recordings_report(body: RecordingInvestigationIn, db: Session = Depends(get_db)):
    return generate_recording_report(db, body.question, body.run_ids or None, body.field_case_id)


@app.post("/saved-investigations")
def create_saved_investigation(body: SaveRecordingInvestigationIn, db: Session = Depends(get_db)):
    return save_imported_investigation(db, body.title, body.question, body.run_ids, body.annotations, body.field_case_id)


@app.post("/demo/reset")
def demo_reset(db: Session = Depends(get_db)):
    return reset_demo(db)


@app.post("/demo/late-evidence")
def demo_late_evidence(db: Session = Depends(get_db)):
    return inject_late_evidence(db)


@app.post("/events")
def create_event(body: EvidenceIn, db: Session = Depends(get_db)):
    received_at = datetime.utcnow()
    ev = Evidence(
        id=body.id or f"ev-{uuid4().hex[:10]}",
        asset_id=body.asset_id,
        event_type=body.event_type,
        event_time=body.event_time,
        known_at=body.known_at or received_at,
        ingested_at=body.ingested_at or received_at,
        source=body.source,
        source_id=body.source_id or f"{body.source}:{body.asset_id}",
        source_type=body.source_type or body.source,
        original_reference=body.original_reference or f"{body.source}:{body.asset_id}:{body.event_type}",
        linkage_method=body.linkage_method,
        verification_state=body.verification_state,
        evidence_class=body.evidence_class,
        payload=body.payload,
        confidence=body.confidence,
        provenance=body.provenance,
        supporting_references=body.supporting_references,
        unresolved_assumptions=body.unresolved_assumptions,
    )
    db.add(ev)
    db.commit()
    return serialize_evidence(ev)


@app.get("/assets")
def assets(db: Session = Depends(get_db)):
    return list(db.scalars(select(Asset).order_by(Asset.id)))


@app.get("/assets/{asset_id}")
def asset(asset_id: str, db: Session = Depends(get_db)):
    row = db.get(Asset, asset_id)
    if not row:
        raise HTTPException(404, "asset not found")
    return row


@app.post("/investigations")
def create_investigation(body: InvestigationIn, db: Session = Depends(get_db)):
    inv = Investigation(id=f"inv-{uuid4().hex[:10]}", asset_id=body.asset_id, trigger_id=body.trigger_id, title=body.title)
    db.add(inv)
    db.commit()
    return inv


@app.get("/investigations")
def investigations(db: Session = Depends(get_db)):
    return list(db.scalars(select(Investigation).order_by(Investigation.created_at.desc())))


@app.get("/investigations/{investigation_id}")
def investigation(investigation_id: str, db: Session = Depends(get_db)):
    inv = db.get(Investigation, investigation_id)
    if not inv:
        raise HTTPException(404, "investigation not found")
    return inv


@app.post("/investigations/{investigation_id}/reconstruct")
def reconstruct_investigation(investigation_id: str, db: Session = Depends(get_db)):
    return reconstruct(db, investigation_id)


@app.get("/investigations/{investigation_id}/comparison")
def compare_investigation(investigation_id: str, db: Session = Depends(get_db)):
    return compare(db, investigation_id)


@app.post("/investigations/{investigation_id}/decision-package")
def decision_package(investigation_id: str, db: Session = Depends(get_db)):
    return build_package(db, investigation_id)


@app.get("/investigations/{investigation_id}/decision-context")
def investigation_decision_context(investigation_id: str, db: Session = Depends(get_db)):
    return decision_context(db, investigation_id)


@app.get("/investigations/{investigation_id}/evidence-lineage")
def investigation_evidence_lineage(investigation_id: str, db: Session = Depends(get_db)):
    return evidence_lineage(db, investigation_id)


@app.get("/investigations/{investigation_id}/hypotheses")
def investigation_hypotheses(investigation_id: str, db: Session = Depends(get_db)):
    return hypotheses_state(db, investigation_id)


@app.get("/investigations/{investigation_id}/diagnostic-checks")
def investigation_diagnostic_checks(investigation_id: str, db: Session = Depends(get_db)):
    return diagnostic_checks(db, investigation_id)


@app.post("/investigations/{investigation_id}/diagnostic-checks/{check_id}/observation")
def investigation_record_check_observation(investigation_id: str, check_id: str, body: CheckObservationIn, db: Session = Depends(get_db)):
    try:
        return record_check_observation(db, investigation_id, check_id, body.observation)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))


@app.get("/investigations/{investigation_id}/replay")
def investigation_replay(investigation_id: str, db: Session = Depends(get_db)):
    return replay_case(db, investigation_id)


@app.post("/decision-packages/{package_id}/seal")
def seal_decision_package(package_id: str, db: Session = Depends(get_db)):
    return seal_package(db, package_id)


@app.get("/decision-packages/{package_id}/verify")
def verify_decision_package(package_id: str, db: Session = Depends(get_db)):
    return verify_package(db, package_id)


@app.post("/investigations/{investigation_id}/decision")
def record_decision(investigation_id: str, body: DecisionIn, db: Session = Depends(get_db)):
    decision = Decision(id=f"dec-{uuid4().hex[:10]}", investigation_id=investigation_id, decision=body.decision, owner=body.owner, rationale=body.rationale, decided_at=datetime.utcnow(), package_id=body.package_id)
    db.add(decision)
    attach_human_decision(db, decision)
    db.commit()
    return decision


@app.post("/investigations/{investigation_id}/outcome")
def record_outcome(investigation_id: str, body: OutcomeIn, db: Session = Depends(get_db)):
    outcome = Outcome(id=f"out-{uuid4().hex[:10]}", investigation_id=investigation_id, outcome=body.outcome, recorded_at=datetime.utcnow(), payload=body.payload)
    db.add(outcome)
    record_cost_impact(db, outcome)
    db.commit()
    return outcome


@app.get("/memory/similar")
def memory_similar(investigation_id: str, db: Session = Depends(get_db)):
    return similar_memory(db, investigation_id)


@app.get("/precedents/compare")
def precedents_compare(investigation_id: str, db: Session = Depends(get_db)):
    return precedent_comparison(db, investigation_id)


@app.get("/investigations/{investigation_id}/analysis")
def investigation_analysis(investigation_id: str, known_at: Optional[datetime] = None, db: Session = Depends(get_db)):
    from app.services import seed_field_case_reasoning
    from app.investigation_engine import evaluate
    if not db.get(Investigation, investigation_id):
        raise HTTPException(status_code=404, detail="investigation not found")
    seed_field_case_reasoning(db, investigation_id)
    return evaluate(db, investigation_id, (known_at.astimezone(timezone.utc).replace(tzinfo=None) if known_at.tzinfo else known_at) if known_at else None)


@app.post("/investigations/{investigation_id}/analysis/precedent")
def save_conditional_precedent(investigation_id: str, body: dict, db: Session = Depends(get_db)):
    from app.investigation_engine import evaluate
    from app.models import CaseEvent
    if not db.get(Investigation, investigation_id):
        raise HTTPException(status_code=404, detail="investigation not found")
    if not all(isinstance(body.get(k), str) and body[k].strip() for k in ("owner", "outcome")):
        raise HTTPException(status_code=422, detail="owner and observed outcome are required")
    conditions = body.get("applicable_conditions")
    if not isinstance(conditions, list) or not conditions or not all(isinstance(c, str) and c.strip() for c in conditions):
        raise HTTPException(status_code=422, detail="explicit applicable conditions are required")
    analysis = evaluate(db, investigation_id)
    events = analysis["diagnostic_events"]
    if not events:
        raise HTTPException(status_code=422, detail="diagnostic evidence is required")
    verification = body.get("intervention_verification", {})
    if not isinstance(verification, dict):
        raise HTTPException(status_code=422, detail="intervention_verification must be an object")
    for flag in ("only_variable_changed", "recovery_verified", "alternatives_excluded"):
        if flag in verification and type(verification[flag]) is not bool and verification[flag] is not None:
            raise HTTPException(status_code=422, detail=f"{flag} must be boolean or unknown")
    now = datetime.utcnow()
    decision = Decision(id=f"dec-{uuid4().hex}", investigation_id=investigation_id, decision="Record conditional precedent", owner=body["owner"], rationale=body["outcome"], decided_at=now)
    outcome = Outcome(id=f"out-{uuid4().hex}", investigation_id=investigation_id, outcome=body["outcome"], recorded_at=now, payload={"decision_id": decision.id, "applicable_conditions": conditions, "diagnostic_evidence_ids": [e["id"] for e in events], "validation_status": "human_reported_requires_independent_verification", "causal_attribution": "not_established", "intervention_verification": verification})
    db.add_all([decision, outcome, CaseEvent(id=f"precedent-{uuid4().hex}", investigation_id=investigation_id, event_kind="conditional_precedent_recorded", occurred_at=now, payload={"decision_id": decision.id, "outcome_id": outcome.id, "analysis": analysis})])
    db.commit()
    return {"decision_id": decision.id, "outcome_id": outcome.id, "automatic_recommendation": False}


@app.post("/demo/investigation-sessions")
def new_investigation_demo_session(db: Session = Depends(get_db)):
    from app.investigation_engine import create_demo_session
    try:
        return create_demo_session(db)
    except ValueError as error:
        raise HTTPException(status_code=503, detail=str(error))


@app.post("/investigations/{investigation_id}/hypotheses/{hypothesis_id}/challenge")
def challenge_hypothesis(investigation_id: str, hypothesis_id: str, body: dict, db: Session = Depends(get_db)):
    from app.models import Hypothesis, CaseEvent
    from app.investigation_engine import evaluate
    hypothesis = db.get(Hypothesis, hypothesis_id)
    if not hypothesis or hypothesis.investigation_id != investigation_id:
        raise HTTPException(status_code=404, detail="hypothesis not found")
    if not all(isinstance(body.get(k), str) and body[k].strip() for k in ('owner', 'reason')):
        raise HTTPException(status_code=422, detail="owner and reason are required")
    references = body.get('evidence_ids', [])
    if not isinstance(references, list) or not all(isinstance(ref, str) for ref in references):
        raise HTTPException(status_code=422, detail="evidence_ids must be a list")
    state = evaluate(db, investigation_id)
    permitted = {e['id'] for e in state['evidence']} | {e['id'] for h in state['hypotheses'] for key in ('supporting_evidence', 'contradicting_evidence', 'non_discriminating_evidence', 'context_evidence') for e in h[key]}
    if any(ref not in permitted for ref in references):
        raise HTTPException(status_code=422, detail="Evidence references must exist in this investigation")
    db.add(CaseEvent(id=f'challenge-{uuid4().hex}', investigation_id=investigation_id, event_kind='hypothesis_challenged', occurred_at=datetime.utcnow(), payload={'hypothesis_id': hypothesis_id, 'owner': body['owner'], 'reason': body['reason'], 'evidence_ids': references}))
    db.commit()
    return evaluate(db, investigation_id)


@app.post('/demo/remote-support-sessions')
def new_remote_support_session(db: Session = Depends(get_db)):
    from app.investigation_engine import create_remote_session
    return create_remote_session(db)
