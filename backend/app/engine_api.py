"""HTTP API for Investigation Engine v2 (same core and store as the FDE CLI).

Local runtime only (same guard as /runtime). The store is a separate append-only SQLite file
(VEYRA_ENGINE_DB, default .local-runtime/engine.db) until the PostgreSQL migration (M2).
"""
import base64
import binascii
import os
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from app.context import model as context_model
from app.context.model import ContextError
from app.engine import checks as check_policy
from app.fde import report as report_html
from app.ingest import catalog
from app.ingest.profile import ProfileError, parse_profile
from app.ledger.model import LedgerError
from app.runtime_engine import enabled
from app.store.sqlite_store import CaseStore, StoreError

router = APIRouter(prefix="/engine", tags=["Investigation Engine v2"], dependencies=[Depends(enabled)])
DEFAULT_DB = Path(__file__).resolve().parents[2] / ".local-runtime" / "engine.db"


def get_store():
    store = CaseStore(Path(os.getenv("VEYRA_ENGINE_DB") or DEFAULT_DB))
    try:
        yield store
    finally:
        store.close()


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except StoreError as error:
        raise HTTPException(404 if "not found" in str(error) else 422, str(error)) from None
    except (LedgerError, ContextError, ProfileError, ValueError) as error:
        raise HTTPException(422, str(error)) from None


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CaseIn(Strict):
    machine_id: str = Field(min_length=1, max_length=150)
    title: str = Field(min_length=1, max_length=200)
    actor: str = Field(min_length=1, max_length=150)
    synthetic: bool = False
    symptom_start: Optional[str] = None
    symptom_end: Optional[str] = None
    symptom_source: Optional[str] = Field(default=None, max_length=2000)


class ImportIn(Strict):
    actor: str = Field(min_length=1, max_length=150)
    filename: str = Field(min_length=1, max_length=160)
    content_base64: str = Field(min_length=1, max_length=6_900_000)
    profile: Dict[str, Any]
    review_reason: Optional[str] = Field(default=None, max_length=2000)


class ContextIn(Strict):
    key: str
    value: Optional[str] = None
    status: Literal["reported", "engineer_confirmed", "unknown", "retracted"] = "reported"
    source: str = Field(min_length=1, max_length=2000)
    actor: str = Field(min_length=1, max_length=150)
    effective_from: str
    evidence: List[str] = Field(default_factory=list, max_length=20)
    note: str = Field(default="", max_length=2000)


class RunIn(Strict):
    actor: str = Field(min_length=1, max_length=150)
    goal: Literal["reduce_uncertainty", "restore_operations"] = "reduce_uncertainty"
    baseline_start: Optional[str] = None
    baseline_end: Optional[str] = None


class LedgerIn(Strict):
    kind: Literal["check_review", "check_execution", "decision", "dispatch_preparation", "action", "outcome", "outcome_review", "recurrence_check", "support_milestone"]
    actor: str = Field(min_length=1, max_length=150)
    source: str = Field(min_length=1, max_length=2000)
    payload: Dict[str, Any]


class ExecuteIn(Strict):
    actor: str = Field(min_length=1, max_length=150)


def _bytes(body: ImportIn) -> bytes:
    if body.filename.startswith(".") or any(ch in body.filename for ch in ("/", "\\", "\x00")):
        raise HTTPException(422, "Use a plain filename without paths")
    try:
        return base64.b64decode(body.content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "content_base64 is not valid base64") from None


@router.get("/schema")
def schema():
    return {"catalog": catalog.describe(), "context": context_model.describe(),
            "checks": [{k: v for k, v in c.items()} for c in check_policy.CATALOG], "check_policy": check_policy.POLICY_VERSION}


@router.post("/cases")
def create_case(body: CaseIn, store: CaseStore = Depends(get_store)):
    return _guard(store.create_case, **body.model_dump())


@router.get("/cases")
def list_cases(store: CaseStore = Depends(get_store)):
    return store.cases()


@router.get("/cases/{case_id}")
def get_case(case_id: str, store: CaseStore = Depends(get_store)):
    case = _guard(store.case, case_id)
    return {"case": case, "imports": store.imports(case_id, include_rows=False), "latest_run": store.latest_run(case_id),
            "runs": [{"id": r["id"], "created_at": r["created_at"], "run_hash": r["run_hash"]} for r in store.runs(case_id)],
            "ledger": store.ledger(case_id), "context": store.context(case["machine_id"])}


@router.post("/cases/{case_id}/imports/preview")
def preview_import(case_id: str, body: ImportIn, store: CaseStore = Depends(get_store)):
    profile = _guard(parse_profile, body.profile)
    result = _guard(store.preview_import, case_id, _bytes(body), body.filename, profile)
    return {**result.to_dict(include_rows=False), "preview_rows": [{k: v for k, v in r.items() if k != "raw"} for r in result.rows[:50]], "stored": False}


@router.post("/cases/{case_id}/imports")
def commit_import(case_id: str, body: ImportIn, store: CaseStore = Depends(get_store)):
    profile = _guard(parse_profile, body.profile)
    record, result = _guard(store.commit_import, case_id, _bytes(body), body.filename, profile, actor=body.actor, review_reason=body.review_reason)
    return {"import": record, "report": result.to_dict(include_rows=False), "stored": True}


@router.post("/machines/{machine_id}/context")
def assert_context(machine_id: str, body: ContextIn, store: CaseStore = Depends(get_store)):
    raw = {**body.model_dump(), "machine_id": machine_id}
    if raw["value"] is None:
        raw.pop("value")
    return _guard(store.assert_context, raw)


@router.get("/machines/{machine_id}/context")
def machine_context(machine_id: str, at: Optional[str] = None, known_at: Optional[str] = None, store: CaseStore = Depends(get_store)):
    return _guard(store.context, machine_id, at=at, known_at=known_at)


@router.post("/cases/{case_id}/runs")
def run(case_id: str, body: RunIn, store: CaseStore = Depends(get_store)):
    baseline = (body.baseline_start, body.baseline_end) if body.baseline_start or body.baseline_end else None
    if baseline and not all(baseline):
        raise HTTPException(422, "Give both baseline_start and baseline_end")
    return _guard(store.investigate, case_id, actor=body.actor, goal=body.goal, baseline_window=baseline)


@router.post("/cases/{case_id}/checks/{check_id}/execute")
def execute(case_id: str, check_id: str, body: ExecuteIn, store: CaseStore = Depends(get_store)):
    return _guard(store.execute_automatic, case_id, check_id, actor=body.actor)


@router.post("/cases/{case_id}/ledger")
def append(case_id: str, body: LedgerIn, store: CaseStore = Depends(get_store)):
    if body.kind == "check_execution" and body.payload.get("status") == "automatic_read_only":
        raise HTTPException(422, "Automatic execution results can only be generated by the engine execution endpoint")
    return _guard(store.append, case_id, body.kind, body.payload, actor=body.actor, source=body.source)


@router.get("/cases/{case_id}/ledger")
def ledger(case_id: str, store: CaseStore = Depends(get_store)):
    _guard(store.case, case_id)
    return {"entries": store.ledger(case_id), "verification": store.verify(case_id)}


@router.get("/cases/{case_id}/episodes")
def episodes(case_id: str, store: CaseStore = Depends(get_store)):
    return _guard(store.episodes, case_id)


@router.get("/cases/{case_id}/report", response_class=HTMLResponse)
def report(case_id: str, store: CaseStore = Depends(get_store)):
    return HTMLResponse(_guard(report_html.render, store, case_id))
