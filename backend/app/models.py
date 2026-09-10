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


class Outcome(Base):
    __tablename__ = "outcomes"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    investigation_id: Mapped[str] = mapped_column(String, ForeignKey("investigations.id"), index=True)
    outcome: Mapped[str] = mapped_column(String)
    recorded_at: Mapped[datetime] = mapped_column(DateTime)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
