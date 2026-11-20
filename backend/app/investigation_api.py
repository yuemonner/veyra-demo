"""Local-only experimental investigation jobs; persistent receipts, no automatic device actions."""
import hashlib
import json
import os
import re
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.agent import investigation_loop as loop
from app.agent.llm import from_environment
from app.engine_api import get_store
from app.runtime_engine import enabled
from app.store.sqlite_store import StoreError


def local_lab():
    if os.getenv("VEYRA_INVESTIGATION_LAB") != "true" or os.getenv("RENDER") or os.getenv("VERCEL"):
        raise HTTPException(404, "Experimental investigation is enabled only in the local preview")


router = APIRouter(prefix="/engine/cases/{case_id}/investigation-lab", tags=["Local investigation experiment"],
                   dependencies=[Depends(enabled), Depends(local_lab)])
_lock = threading.Lock()
_running = set()


def folder(store, cid):
    try:
        store.case(cid)
    except StoreError:
        raise HTTPException(404, "Case not found") from None
    return store.path.parent / "investigation-lab" / hashlib.sha256(cid.encode()).hexdigest()[:24]


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2, allow_nan=False))
    os.replace(temp, path)


@router.get("")
def list_investigations(case_id: str, store=Depends(get_store)):
    base = folder(store, case_id)
    model = from_environment()
    runs = []
    for p in sorted(base.glob("inv-*/result.json"), reverse=True):
        result = json.loads(p.read_text())
        key = str(p.parent.resolve())
        with _lock:
            active = key in _running
        if result["state"] == "running" and not active:
            result.update(state="interrupted", error="Local worker stopped; no completion is claimed. Start a new frozen run.")
        runs.append(result)
    runs.sort(key=lambda r: r["created_at"], reverse=True)
    return {"enabled": True, "provider": model.provider, "model": model.model,
            "configured": model.available() and os.getenv("VEYRA_INVESTIGATION_AGENT_BLOCKED") != "true",
            "runs": runs[:8], "boundary": "Local experiment. Real model execution and independent evaluation are separate."}


@router.post("", status_code=202)
def start_investigation(case_id: str, store=Depends(get_store)):
    from app.private import private
    if private() and os.getenv("VEYRA_PRIVATE_ALLOW_AGENT") != "true":
        raise HTTPException(403, "Agent disabled for this private preview")
    base = folder(store, case_id)
    with _lock:
        if any(str(base.resolve()) in item for item in _running):
            raise HTTPException(409, "An investigation for this case is already running")
        model = from_environment()
        try:
            session = loop.prepare(store, case_id, model)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        run_dir = base / session.result["id"]
        session.save = lambda result: atomic(run_dir / "result.json", result)
        atomic(run_dir / "evidence.json", session.snapshot)
        session.persist()
        if session.result["state"] == "running":
            key = str(run_dir.resolve())
            _running.add(key)
            def work():
                try:
                    session.run(model)
                except Exception as error:
                    session.result.update(state="failed", error=type(error).__name__ + ": " + str(error)[:1200])
                    session.persist()
                finally:
                    with _lock:
                        _running.discard(key)
            threading.Thread(target=work, daemon=True).start()
    return session.result


@router.post("/tool-validation")
def validate_local_tools(case_id: str, store=Depends(get_store)):
    base = folder(store, case_id)
    try:
        session = loop.Session(loop.freeze(store, case_id), provider="local-tools", model="none", mode="operator_tool_validation")
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    run_dir = base / session.result["id"]
    session.save = lambda result: atomic(run_dir / "result.json", result)
    atomic(run_dir / "evidence.json", session.snapshot)
    return loop.validate_tools(session)


@router.get("/{investigation_id}")
def export_investigation(case_id: str, investigation_id: str, store=Depends(get_store)):
    base = folder(store, case_id)
    if not re.fullmatch(r"inv-[0-9a-f]{16}", investigation_id):
        raise HTTPException(404, "Investigation not found")
    path = base / investigation_id / "result.json"
    if not path.exists():
        raise HTTPException(404, "Investigation not found")
    return json.loads(path.read_text())
