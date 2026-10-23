"""Upload a robot recording (ROS 1 bag, ROS 2 bag as .zip, MCAP) -> review the suggested mapping -> debrief.

Recordings are large, so they are streamed to disk (not sent as base64 JSON) and the debrief runs as a background
job the page polls. Available on a private instance, or locally with VEYRA_ALLOW_RECORDINGS=true; never on the
open hosted demo.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from app.ingest import bags, catalog
from app.private import actor_for, private


def recordings_enabled(request: Request):
    if os.getenv("VEYRA_ENABLE_RUNTIME") != "true":
        raise HTTPException(404, "Local runtime is not enabled")
    if not (private() or os.getenv("VEYRA_ALLOW_RECORDINGS") == "true"):
        raise HTTPException(404, "Recording upload is available on a private instance")


router = APIRouter(prefix="/engine", tags=["Recordings"], dependencies=[Depends(recordings_enabled)])
MAX_MB = int(os.getenv("VEYRA_MAX_RECORDING_MB", "4096"))
SUFFIXES = (".bag", ".mcap", ".zip")
_jobs: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()


def _folder(request: Request) -> Path:
    from app.engine_api import store_path
    folder = store_path(request).parent / "recordings"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _recording_dir(request: Request, rid: str) -> Path:
    if not re.fullmatch(r"rec-[0-9a-f]{16}", rid):
        raise HTTPException(404, "Recording not found")
    folder = _folder(request) / rid
    if not folder.is_dir():
        raise HTTPException(404, "Recording not found")
    return folder


@router.post("/recordings")
async def upload(request: Request, filename: str = Query(min_length=1, max_length=160)):
    name = Path(filename).name
    if name.startswith(".") or not name.lower().endswith(SUFFIXES):
        raise HTTPException(422, "Upload a ROS 1 bag (.bag), an MCAP file (.mcap) or a ROS 2 bag folder zipped (.zip)")
    rid = "rec-" + secrets.token_hex(8)
    folder = _folder(request) / rid
    folder.mkdir()
    target = folder / re.sub(r"[^A-Za-z0-9._-]", "_", name)
    size, limit = 0, MAX_MB * 1024 * 1024
    try:
        with open(target, "wb") as handle:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"Recordings up to {MAX_MB} MB on this instance")
                handle.write(chunk)
        if size == 0:
            raise HTTPException(422, "The upload was empty")
        inspection = await run_in_threadpool(bags.inspect, target)
    except bags.RecordingError as error:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(422, str(error)) from None
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    meta = {"id": rid, "filename": name, "stored_as": target.name, "bytes": size, "uploaded_at": time.time(),
            "uploaded_by": actor_for(request, "Engineer (upload)"), "inspection": inspection, "suggested": bags.suggest(inspection)}
    (folder / "recording.json").write_text(json.dumps(meta))
    return meta


@router.get("/recordings/{rid}")
def recording(rid: str, request: Request):
    return json.loads((_recording_dir(request, rid) / "recording.json").read_text())


class ReportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stated_reason: str = Field(min_length=1, max_length=2000)
    operator: Optional[str] = Field(default=None, max_length=150)
    intervention_type: Optional[str] = None
    symptom: Optional[str] = None
    affected_function: Optional[str] = Field(default=None, max_length=100)
    at_s: Optional[float] = None


class DebriefIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mapping: Dict[str, Any]
    machine_id: str = Field(min_length=1, max_length=150)
    title: str = Field(min_length=1, max_length=200)
    machine_class: Optional[str] = None
    actor: str = Field(default="Engineer (upload)", min_length=1, max_length=150)
    problem_from_s: Optional[float] = None
    problem_to_s: Optional[float] = None
    normal_from_s: Optional[float] = None
    normal_to_s: Optional[float] = None
    normal_basis: Optional[str] = Field(default=None, max_length=1000)
    report: Optional[ReportIn] = None
    synthetic: bool = False


@router.post("/recordings/{rid}/debrief")
def start_debrief(rid: str, body: DebriefIn, request: Request):
    from app.engine_api import store_path
    folder = _recording_dir(request, rid)
    meta = json.loads((folder / "recording.json").read_text())
    try:
        mapping = bags.validate_mapping(body.mapping, [s["id"] for s in catalog.describe()["signals"]], [t["topic"] for t in meta["inspection"]["topics"]])
    except bags.RecordingError as error:
        raise HTTPException(422, str(error)) from None
    if body.machine_class:
        from app.machines import profiles
        if body.machine_class not in profiles.load_all():
            raise HTTPException(422, f"Unknown machine type '{body.machine_class}'")
    job = {"id": "job-" + secrets.token_hex(8), "recording": rid, "state": "running", "step": "Starting", "started_at": time.time(),
           "owner": (getattr(request.state, "user", None) or {}).get("workspace_id")}
    with _lock:
        _jobs[job["id"]] = job
    args = dict(path=folder / meta["stored_as"], mapping=mapping, machine_id=body.machine_id.strip(), title=body.title.strip(),
                actor=actor_for(request, body.actor), machine_class=body.machine_class, problem_from_s=body.problem_from_s, problem_to_s=body.problem_to_s,
                normal_from_s=body.normal_from_s, normal_to_s=body.normal_to_s, normal_basis=body.normal_basis,
                report=body.report.model_dump() if body.report else None, source_name=meta["filename"], synthetic=body.synthetic)
    threading.Thread(target=_run_job, args=(job, store_path(request), args), daemon=True).start()
    return {"job_id": job["id"]}


def _run_job(job: Dict[str, Any], db: Path, args: Dict[str, Any]) -> None:
    from app.workflows.debrief import debrief_recording
    from app.store.sqlite_store import CaseStore
    store = CaseStore(db)
    try:
        result = debrief_recording(store, progress=lambda step: job.update(step=step), **args)
        from app.engine_api import _autopilot
        job.update(step="Preparing the next step")
        _autopilot(store, result["case_id"])
        job.update(state="done", step="Done", result=result, finished_at=time.time())
    except (ValueError, bags.RecordingError) as error:
        job.update(state="failed", error=str(error), finished_at=time.time())
    except Exception as error:  # an unexpected failure is reported, never swallowed
        traceback.print_exc()
        job.update(state="failed", error=f"Unexpected error: {error}", finished_at=time.time())
    finally:
        store.close()
        from app.memory import release
        release()


@router.get("/jobs/{job_id}")
def job_status(job_id: str, request: Request):
    job = _jobs.get(job_id)
    owner = (getattr(request.state, "user", None) or {}).get("workspace_id") if private() else None
    if not job or (private() and job.get("owner") != owner):
        raise HTTPException(404, "Job not found")
    return {k: v for k, v in job.items() if k != "owner"}
