from datetime import datetime
from typing import Optional
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db import Base


class Asset(Base):
    __tablename__ = "assets"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    asset_type: Mapped[str] = mapped_column(String, nullable=False, default="AMR")
    family: Mapped[str] = mapped_column(String, nullable=False, default="AMR-X")
    site: Mapped[str] = mapped_column(String, nullable=False, default="Warehouse A")
    environment: Mapped[str] = mapped_column(String, nullable=False, default="warehouse")


class Evidence(Base):
    __tablename__ = "evidence"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    asset_id: Mapped[str] = mapped_column(String, ForeignKey("assets.id"), index=True)
    event_type: Mapped[str] = mapped_column(String, index=True)
    event_time: Mapped[datetime] = mapped_column(DateTime, index=True)
    known_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    source: Mapped[str] = mapped_column(String, index=True)
    evidence_class: Mapped[str] = mapped_column(String, index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    superseded_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    asset: Mapped[Asset] = relationship()


class Investigation(Base):
    __tablename__ = "investigations"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    asset_id: Mapped[str] = mapped_column(String, ForeignKey("assets.id"), index=True)
    trigger_id: Mapped[str] = mapped_column(String, ForeignKey("evidence.id"))
    title: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DecisionPackage(Base):
    __tablename__ = "decision_packages"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    investigation_id: Mapped[str] = mapped_column(String, ForeignKey("investigations.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    package: Mapped[dict] = mapped_column(JSON)
    sealed: Mapped[bool] = mapped_column(Boolean, default=False)
    sealed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    digest: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    signature: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    public_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class Decision(Base):
    __tablename__ = "decisions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    investigation_id: Mapped[str] = mapped_column(String, ForeignKey("investigations.id"), index=True)
    decision: Mapped[str] = mapped_column(String)
    owner: Mapped[str] = mapped_column(String)
    rationale: Mapped[str] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(DateTime)
    package_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("decision_packages.id"), nullable=True)


class EvidenceSnapshot(Base):
    __tablename__ = "evidence_snapshots"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    investigation_id: Mapped[str] = mapped_column(String, ForeignKey("investigations.id"), index=True)
    decision_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("decisions.id"), nullable=True, index=True)
    snapshot_time: Mapped[datetime] = mapped_column(DateTime, index=True)
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    known_state: Mapped[dict] = mapped_column(JSON, default=dict)
    unknowns: Mapped[list] = mapped_column(JSON, default=list)
    observability_state: Mapped[dict] = mapped_column(JSON, default=dict)
    sealed_digest: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class DecisionOption(Base):
    __tablename__ = "decision_options"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    investigation_id: Mapped[str] = mapped_column(String, ForeignKey("investigations.id"), index=True)
    decision_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("decisions.id"), nullable=True, index=True)
    option_type: Mapped[str] = mapped_column(String, index=True)
    label: Mapped[str] = mapped_column(String)
    expected_cost: Mapped[dict] = mapped_column(JSON, default=dict)
    expected_risk: Mapped[dict] = mapped_column(JSON, default=dict)
    historical_support: Mapped[dict] = mapped_column(JSON, default=dict)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)


class ObservabilityState(Base):
    __tablename__ = "observability_states"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    investigation_id: Mapped[str] = mapped_column(String, ForeignKey("investigations.id"), index=True)
    decision_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("decisions.id"), nullable=True, index=True)
    assessed_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String, default="partial")
    connected_sources: Mapped[list] = mapped_column(JSON, default=list)
    freshness: Mapped[dict] = mapped_column(JSON, default=dict)
    completeness: Mapped[dict] = mapped_column(JSON, default=dict)
    delayed_sources: Mapped[list] = mapped_column(JSON, default=list)
    missing_windows: Mapped[list] = mapped_column(JSON, default=list)


class ExecutedAction(Base):
    __tablename__ = "executed_actions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    investigation_id: Mapped[str] = mapped_column(String, ForeignKey("investigations.id"), index=True)
    decision_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("decisions.id"), nullable=True, index=True)
    action_type: Mapped[str] = mapped_column(String, index=True)
    owner: Mapped[str] = mapped_column(String)
    executed_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    scope: Mapped[dict] = mapped_column(JSON, default=dict)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class Outcome(Base):
    __tablename__ = "outcomes"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    investigation_id: Mapped[str] = mapped_column(String, ForeignKey("investigations.id"), index=True)
    outcome: Mapped[str] = mapped_column(String)
    recorded_at: Mapped[datetime] = mapped_column(DateTime)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class CostImpact(Base):
    __tablename__ = "cost_impacts"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    investigation_id: Mapped[str] = mapped_column(String, ForeignKey("investigations.id"), index=True)
    outcome_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("outcomes.id"), nullable=True, index=True)
    recovery_time_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    downtime_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    field_visit: Mapped[bool] = mapped_column(Boolean, default=False)
    engineering_hours: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rollout_delay_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    customer_escalation: Mapped[bool] = mapped_column(Boolean, default=False)
    estimated_cost: Mapped[dict] = mapped_column(JSON, default=dict)


class LateEvidence(Base):
    __tablename__ = "late_evidence"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    investigation_id: Mapped[str] = mapped_column(String, ForeignKey("investigations.id"), index=True)
    evidence_id: Mapped[str] = mapped_column(String, ForeignKey("evidence.id"), index=True)
    decision_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("decisions.id"), nullable=True, index=True)
    materiality: Mapped[str] = mapped_column(String, default="review_required")
    review_status: Mapped[str] = mapped_column(String, default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ReviewFlag(Base):
    __tablename__ = "review_flags"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    investigation_id: Mapped[str] = mapped_column(String, ForeignKey("investigations.id"), index=True)
    decision_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("decisions.id"), nullable=True, index=True)
    flag_type: Mapped[str] = mapped_column(String, index=True)
    severity: Mapped[str] = mapped_column(String, default="review")
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PrecedentComparison(Base):
    __tablename__ = "precedent_comparisons"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    investigation_id: Mapped[str] = mapped_column(String, ForeignKey("investigations.id"), index=True)
    query: Mapped[dict] = mapped_column(JSON, default=dict)
    similar_state: Mapped[dict] = mapped_column(JSON, default=dict)
    response_history: Mapped[dict] = mapped_column(JSON, default=dict)
    outcome_comparison: Mapped[dict] = mapped_column(JSON, default=dict)
    cost_comparison: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
