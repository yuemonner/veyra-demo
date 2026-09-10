from datetime import datetime
from typing import Optional
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db, init_db
from app.models import Asset, Decision, DecisionPackage, Evidence, Investigation, Outcome
from app.services import build_package, compare, inject_late_evidence, reconstruct, reset_demo, seal_package, serialize_evidence, similar_memory

app = FastAPI(title="Veyra V0", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class EvidenceIn(BaseModel):
    id: Optional[str] = None
    asset_id: str
    event_type: str
    event_time: datetime
    known_at: Optional[datetime] = None
    ingested_at: Optional[datetime] = None
    source: str
    evidence_class: str
    payload: dict = {}
    confidence: float = 1.0
    provenance: dict = {}


class InvestigationIn(BaseModel):
    asset_id: str
    trigger_id: str
    title: str


class DecisionIn(BaseModel):
    decision: str
    owner: str = "Engineering"
    rationale: str = ""
    package_id: Optional[str] = None


class OutcomeIn(BaseModel):
    outcome: str
    payload: dict = {}


@app.on_event("startup")
def startup():
    init_db()


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
        evidence_class=body.evidence_class,
        payload=body.payload,
        confidence=body.confidence,
        provenance=body.provenance,
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


@app.post("/decision-packages/{package_id}/seal")
def seal_decision_package(package_id: str, db: Session = Depends(get_db)):
    return seal_package(db, package_id)


@app.post("/investigations/{investigation_id}/decision")
def record_decision(investigation_id: str, body: DecisionIn, db: Session = Depends(get_db)):
    decision = Decision(id=f"dec-{uuid4().hex[:10]}", investigation_id=investigation_id, decision=body.decision, owner=body.owner, rationale=body.rationale, decided_at=datetime.utcnow(), package_id=body.package_id)
    db.add(decision)
    db.commit()
    return decision


@app.post("/investigations/{investigation_id}/outcome")
def record_outcome(investigation_id: str, body: OutcomeIn, db: Session = Depends(get_db)):
    outcome = Outcome(id=f"out-{uuid4().hex[:10]}", investigation_id=investigation_id, outcome=body.outcome, recorded_at=datetime.utcnow(), payload=body.payload)
    db.add(outcome)
    db.commit()
    return outcome


@app.get("/memory/similar")
def memory_similar(investigation_id: str, db: Session = Depends(get_db)):
    return similar_memory(db, investigation_id)
