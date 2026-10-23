"""Profile-based industrial import for the local runtime (additive; legacy mapping import unchanged).

Flow: preview (validate only, nothing stored) -> commit (immutable source + ingestion report).
Rows that need review can only be committed with a named reviewer reason. Unsupported, rejected
and duplicate uploads are never stored as evidence.
"""
import base64
import binascii
import hashlib
import json
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.ingest import catalog
from app.ingest.pipeline import NEEDS_REVIEW, Limits, ingest
from app.ingest.profile import ProfileError, parse_profile
from app.models import RuntimeSource
from app.runtime_diagnostics import snapshot
from app.runtime_engine import case_required, enabled, record, source_info, sources, uid

router = APIRouter(prefix="/runtime", tags=["Industrial import"], dependencies=[Depends(enabled)])

# The local runtime request cap (runtime_engine.enabled) is 5.1 MB, so a file is at most ~3.8 MB as base64.
LIMITS = Limits(max_bytes=5_000_000, max_rows=50_000)
# Legacy analytics thresholds are keyed by these names; everything else stays statistics-only.
LEGACY_SIGNAL_NAMES = {"network.rtt": "rtt", "network.packet_loss": "packet_loss"}
PREVIEW_ROWS = 50


class ImportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: str = Field(min_length=1, max_length=150)
    filename: str = Field(min_length=1, max_length=160)
    content_base64: str = Field(min_length=1, max_length=6_900_000)
    profile: dict
    role: Literal["observation", "baseline"] = "observation"
    source_uri: str = Field(min_length=1, max_length=2000)
    review_reason: Optional[str] = Field(default=None, max_length=2000)


def _decode_payload(body: ImportIn) -> bytes:
    try:
        return base64.b64decode(body.content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "content_base64 is not valid base64") from None


def _safe_filename(name: str) -> str:
    if name.startswith(".") or any(ch in name for ch in ("/", "\\", "\x00")) or ".." in name:
        raise HTTPException(422, "Use a plain filename without paths")
    return name


def _prepare(db: Session, cid: str, body: ImportIn):
    case = case_required(db, cid)
    if case.payload.get("adapter") != "industrial":
        raise HTTPException(422, "Profile imports are for industrial cases")
    if case.payload.get("status") == "closed":
        raise HTTPException(409, "Reopen the case before adding evidence")
    _safe_filename(body.filename)
    try:
        profile = parse_profile(body.profile)
    except ProfileError as error:
        raise HTTPException(422, f"Import profile: {error}") from None
    raw = _decode_payload(body)
    existing = {s.sha256: s.id for s in sources(db, cid)}
    result = ingest(raw, body.filename, profile, case.payload["asset_id"], now=datetime.now(timezone.utc),
                    existing_hashes=existing, limits=LIMITS)
    return case, profile, raw, result


def _legacy_samples(rows):
    """Numeric rows in the shape the existing runtime analytics expect; nothing else enters numeric tools."""
    samples = []
    for row in rows:
        if not isinstance(row.get("value"), (int, float)):
            continue
        canonical = row["signal"]
        samples.append({
            "t": row["t"], "asset": row["asset"],
            "signal": LEGACY_SIGNAL_NAMES.get(canonical, canonical or "unmapped:" + row["source_signal"]),
            "value": row["value"], "unit": row["unit"] or "unknown", "original_unit": row["original_unit"],
            "original_value": row["original_value"], "row": row["row"], "line": row.get("line"),
            "scope": row["scope"] or "unspecified", "event_type": "", "version": "",
            "canonical_signal": canonical, "clock_domain": row["clock_domain"], "flags": row["flags"],
        })
    return samples


@router.get("/catalog/signals")
def signal_catalog():
    return catalog.describe()


@router.post("/cases/{cid}/imports/preview")
def preview_import(cid: str, body: ImportIn, db: Session = Depends(get_db)):
    _, _, _, result = _prepare(db, cid, body)
    report = result.to_dict(include_rows=False)
    report["preview_rows"] = [{k: v for k, v in r.items() if k != "raw"} for r in result.rows[:PREVIEW_ROWS]]
    report["stored"] = False
    return report


@router.post("/cases/{cid}/imports")
def commit_import(cid: str, body: ImportIn, db: Session = Depends(get_db)):
    case, profile, raw, result = _prepare(db, cid, body)
    report = result.to_dict(include_rows=False)
    if not result.committable:
        raise HTTPException(409 if result.status == "duplicate_upload" else 422, {"message": f"Import not stored: {result.status}", "report": report})
    if result.status == NEEDS_REVIEW and not (body.review_reason or "").strip():
        raise HTTPException(422, {"message": "Some rows were rejected. Record a review reason to store the accepted rows.", "report": report})
    if len(sources(db, cid)) >= 12:
        raise HTTPException(422, "Local case limit: 12 sources")
    if result.profile.get("synthetic") != bool(case.payload.get("synthetic")):
        raise HTTPException(422, "Synthetic profiles can only be imported into synthetic cases, and real data only into real cases")

    content = raw.decode("utf-8")  # strict UTF-8 already validated; BOM kept so content.encode() == original bytes
    samples = _legacy_samples(result.rows)
    events = [{k: v for k, v in r.items() if k != "raw"} for r in result.rows if not isinstance(r.get("value"), (int, float))]
    if not samples and not events:
        raise HTTPException(422, "No storable rows")
    times = [r["t"] for r in result.rows]
    source = RuntimeSource(id=uid("source"), case_id=cid, sha256=hashlib.sha256(raw).hexdigest(), content=content, samples=samples, payload={})
    source.payload = {
        "name": body.filename, "format": profile.format, "role": body.role, "frame": "not_applicable",
        "source_uri": body.source_uri, "actor": body.actor, "mapping": {}, "context": {}, "synthetic": profile.synthetic,
        "machine_context_snapshot": snapshot(case),
        "normalized_sha256": hashlib.sha256(json.dumps(samples, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest(),
        "sample_count": len(samples), "event_count": len(events), "events": events,
        "time_start": min(times), "time_end": max(times), "warnings": result.warnings, "bytes": len(raw),
        "role_boundary": "source_observation_unreviewed",
        "ingestion": report,
        "review": {"actor": body.actor, "reason": body.review_reason} if result.status == NEEDS_REVIEW else None,
    }
    db.add(source)
    db.commit()
    event = record(db, cid, "import", {"actor": body.actor, "source": source.id, "summary": f"Imported {body.filename}: {result.status}",
                                        "import_status": result.status, "profile": result.profile, "sha256": source.sha256,
                                        "rejected_row_count": result.rejected_row_count, "review_reason": body.review_reason})
    return {"source": source_info(source), "report": report, "event_id": event["id"], "stored": True}
