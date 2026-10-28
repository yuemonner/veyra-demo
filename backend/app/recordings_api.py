"""Upload a robot recording (ROS 1 bag, ROS 2 bag as .zip, MCAP, PX4 ULog) or a text log (console, syslog, rosout)
-> review the suggested mapping (recordings only; a text log needs none) -> debrief.

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

from app.ingest import bags, catalog, textlog, ulog
from app.private import actor_for, private


def recordings_enabled(request: Request):
    if os.getenv("VEYRA_ENABLE_RUNTIME") != "true":
        raise HTTPException(404, "Local runtime is not enabled")
    if not (private() or os.getenv("VEYRA_ALLOW_RECORDINGS") == "true"):
        raise HTTPException(404, "Recording upload is available on a private instance")


router = APIRouter(prefix="/engine", tags=["Recordings"], dependencies=[Depends(recordings_enabled)])
MAX_MB = int(os.getenv("VEYRA_MAX_RECORDING_MB", "4096"))
SUFFIXES = (".bag", ".mcap", ".zip", ".ulg") + textlog.TEXT_SUFFIXES
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
        raise HTTPException(422, "Upload a ROS 1 bag (.bag), an MCAP file (.mcap), a ROS 2 bag folder zipped (.zip), a PX4 log (.ulg) or a text log (.log, .txt)")
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
        inspection = await run_in_threadpool(_inspect, target)
    except (bags.RecordingError, ValueError) as error:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(422, str(error)) from None
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    meta = {"id": rid, "filename": name, "stored_as": target.name, "bytes": size, "uploaded_at": time.time(),
            "uploaded_by": actor_for(request, "Engineer (upload)"), "inspection": inspection,
            "suggested": bags.suggest(inspection) if inspection["format"] in bags.FORMATS else None}
    (folder / "recording.json").write_text(json.dumps(meta))
    return meta


def _inspect(path: Path) -> Dict[str, Any]:
    """What the file is, before anything is imported. Text logs and PX4 logs need no topic mapping."""
    from app.workflows.debrief import is_text_log
    if ulog.is_ulog(path):
        log = ulog.read(path, set())
        topics = sorted({k[0] for k in log["counts"]})
        span = ((log["last_us"] or 0) - (log["first_us"] or 0)) / 1e6
        return {"format": "px4_ulog", "duration_s": span, "topics": [{"topic": t, "type": "px4", "count": n, "rate_hz": None}
                                                                    for (t, _m), n in sorted(log["counts"].items())], "message_count": sum(log["counts"].values()),
                "start_ns": None}
    if is_text_log(path):
        if not textlog.looks_like_text(path):
            raise ValueError("This file is not a readable text log (expected lines of text)")
        from app.discovery import logs
        parsed = textlog.read(path)
        found = logs.characterize(parsed, source_name=path.name)
        return {"format": "text_log", "lines": found["lines"], "clock": found["clock"], "notes": found["notes"], "summary": found["summary"],
                "restarts": len(found["restarts"]), "kinds": found["kinds"], "duration_s": None, "topics": [], "message_count": found["lines"]}
    return bags.inspect(path)


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
    allow_model: bool = False   # the uploader allows Veyra's AI investigator to read this data (sent to the configured model provider)


@router.post("/recordings/{rid}/debrief")
def start_debrief(rid: str, body: DebriefIn, request: Request):
    from app.engine_api import store_path
    folder = _recording_dir(request, rid)
    meta = json.loads((folder / "recording.json").read_text())
    if meta["inspection"]["format"] in bags.FORMATS:
        try:
            mapping = bags.validate_mapping(body.mapping, [s["id"] for s in catalog.describe()["signals"]], [t["topic"] for t in meta["inspection"]["topics"]])
        except bags.RecordingError as error:
            raise HTTPException(422, str(error)) from None
    else:
        mapping = {}  # PX4 logs and text logs have a fixed, documented reading
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
    threading.Thread(target=_run_job, args=(job, store_path(request), args, body.allow_model, actor_for(request, body.actor)), daemon=True).start()
    return {"job_id": job["id"]}


def _run_job(job: Dict[str, Any], db: Path, args: Dict[str, Any], allow_model: bool = False, actor: str = "Engineer (upload)") -> None:
    from app.workflows.debrief import debrief_recording
    from app.store.sqlite_store import CaseStore
    store = CaseStore(db)
    try:
        result = debrief_recording(store, progress=lambda step: job.update(step=step), **args)
        job.update(case_id=result["case_id"])
        _investigate_into(job, store, result["case_id"], allow_model=allow_model, actor=actor)
        from app.engine_api import _autopilot
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


@router.post("/access-check")
def access_check():
    """Nothing to do: reaching here means the access-code gate (hosted demo) let this browser write."""
    return {"ok": True}


@router.get("/samples")
def list_samples():
    from app.workflows import public_samples
    return public_samples.listing()


class SampleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allow_model: bool = False


@router.post("/samples/{name}/run")
def sample_log(name: str, request: Request, body: Optional[SampleIn] = None):
    """Run a REAL public failure log (fixtures/real/public) through the ordinary upload path and the investigator."""
    from app.engine_api import store_path
    from app.workflows import public_samples
    if name not in public_samples.SAMPLES:
        raise HTTPException(404, "Unknown sample")
    job = _new_job(request)
    threading.Thread(target=_run_sample, args=(job, store_path(request), name, bool(body and body.allow_model), actor_for(request, "Engineer (sample)")),
                     daemon=True).start()
    return {"job_id": job["id"]}


def _new_job(request: Request, **extra) -> Dict[str, Any]:
    job = {"id": "job-" + secrets.token_hex(8), "recording": None, "state": "running", "step": "Starting", "started_at": time.time(), "steps": [],
           "owner": (getattr(request.state, "user", None) or {}).get("workspace_id"), **extra}
    with _lock:
        _jobs[job["id"]] = job
    return job


def _run_sample(job: Dict[str, Any], db: Path, name: str, allow_model: bool, actor: str) -> None:
    from app.store.sqlite_store import CaseStore
    from app.workflows import public_samples
    store = CaseStore(db)
    try:
        result = public_samples.run_sample(store, name, progress=lambda step: job.update(step=step))
        job.update(case_id=result["case_id"])
        _investigate_into(job, store, result["case_id"], allow_model=allow_model, actor=actor)
        job.update(state="done", step="Done", result=result, finished_at=time.time())
    except Exception as error:  # noqa: BLE001
        traceback.print_exc()
        job.update(state="failed", error=f"{type(error).__name__}: {error}", finished_at=time.time())
    finally:
        store.close()


# ---------------------------------------------------------------- autonomous investigation
def _investigator_model():
    if os.getenv("VEYRA_INVESTIGATOR", "agent").strip().lower() in ("rules", "off", "false"):
        return None
    from app.agent.llm import from_environment
    return from_environment()


def _investigate_into(job: Dict[str, Any], store, case_id: str, *, allow_model: bool, actor: str) -> Dict[str, Any]:
    """Run the investigator for a case, streaming each check it runs into the job (the page shows them live)."""
    from app.agent import investigator
    from app.agent.runner import egress_allowed
    model = _investigator_model()
    if model is not None and allow_model and model.available() and not egress_allowed(store, case_id, model.provider):
        store.append(case_id, "model_authorization", {"provider": model.provider, "model": getattr(model, "model", None),
                                                      "scope": "Veyra's investigator may read this case's data to investigate it",
                                                      "authorized_by": actor, "evidence": "Allowed by the uploader in the upload form"},
                     actor=actor, source="upload form")
    job.update(step="Veyra is investigating" if model is not None and model.available() else "Reading the log")
    out = investigator.investigate(store, case_id, model=model, on_step=lambda st: job["steps"].append(st))
    job.update(investigation={"mode": out["mode"], "fallback_reason": out["fallback_reason"], "seconds": out["seconds"]})
    return out


class InvestigateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allow_model: bool = False


@router.post("/cases/{case_id}/investigate")
def start_investigation(case_id: str, request: Request, body: Optional[InvestigateIn] = None):
    from app.engine_api import store_path
    job = _new_job(request, case_id=case_id)
    threading.Thread(target=_run_investigation, args=(job, store_path(request), case_id, bool(body and body.allow_model), actor_for(request, "Engineer")),
                     daemon=True).start()
    return {"job_id": job["id"]}


def _run_investigation(job, db, case_id, allow_model, actor):
    from app.store.sqlite_store import CaseStore
    store = CaseStore(db)
    try:
        _investigate_into(job, store, case_id, allow_model=allow_model, actor=actor)
        job.update(state="done", step="Done", result={"case_id": case_id}, finished_at=time.time())
    except Exception as error:  # noqa: BLE001
        traceback.print_exc()
        job.update(state="failed", error=f"{type(error).__name__}: {error}", finished_at=time.time())
    finally:
        store.close()


@router.get("/cases/{case_id}/investigation")
def get_investigation(case_id: str, request: Request):
    from app.agent import investigator
    from app.agent.runner import egress_allowed
    from app.engine_api import store_path
    from app.store.sqlite_store import CaseStore
    store = CaseStore(store_path(request))
    try:
        model = _investigator_model()
        findings = [{"id": e["id"], "recorded_at": e["recorded_at"], "by": e["actor"], **e["payload"]} for e in store.ledger(case_id) if e["kind"] == "engineer_finding"]
        recorded = [{**e["payload"], "entry_id": e["id"]} for e in store.ledger(case_id)
                    if e["kind"] == "agent_report" and e["payload"].get("kind") == "autonomous_investigation" and e["payload"].get("recorded")]
        playback = {"investigations": recorded, "findings": findings} if recorded else None  # a recorded walkthrough, played step by step
        notes = {}
        for e in store.ledger(case_id):
            if e["kind"] == "event_note":
                notes[e["payload"]["pattern"]] = {**e["payload"], "by": e["actor"], "at": e["recorded_at"]}
        rounds = [e["payload"] for e in store.ledger(case_id) if e["kind"] == "agent_report"
                  and e["payload"].get("kind") == "autonomous_investigation" and e["payload"].get("mode") == "agent"]
        previous = (rounds[-2].get("result") or {}).get("hypotheses") if len(rounds) > 1 else None
        return {"investigation": investigator.latest(store, case_id), "findings": findings, "playback": playback, "notes": list(notes.values()),
                "previous_hypotheses": previous,
                "agent": {"configured": bool(model and model.available()), "model": getattr(model, "model", None) if model else None,
                          "allowed": bool(model and egress_allowed(store, case_id, model.provider))},
                "closed": any(e["kind"] == "case_closure" for e in store.ledger(case_id))}
    finally:
        store.close()


class FindingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(min_length=1, max_length=500)
    result: str = Field(pattern="^(found|not_found|cannot_do|other)$")
    observations: Optional[str] = Field(default=None, max_length=2000)
    investigation_entry: Optional[str] = Field(default=None, max_length=80)
    reinvestigate: bool = True


@router.post("/cases/{case_id}/finding")
def record_finding(case_id: str, body: FindingIn, request: Request):
    """The engineer's result for the next action. Veyra re-investigates with it (unless asked not to)."""
    from app.engine_api import store_path
    from app.ledger.model import LedgerError
    from app.store.sqlite_store import CaseStore
    store = CaseStore(store_path(request))
    try:
        entry = store.append(case_id, "engineer_finding", body.model_dump(exclude={"reinvestigate"}, exclude_none=True),
                             actor=actor_for(request, "Engineer"), source="case page")
    except LedgerError as error:
        raise HTTPException(422, str(error)) from None
    finally:
        store.close()
    if not body.reinvestigate:
        return {"entry_id": entry["id"]}
    job = _new_job(request, case_id=case_id)
    threading.Thread(target=_run_investigation, args=(job, store_path(request), case_id, False, actor_for(request, "Engineer")), daemon=True).start()
    return {"entry_id": entry["id"], "job_id": job["id"]}


@router.get("/cases/{case_id}/lesson-draft")
def lesson_draft(case_id: str, request: Request):
    from app.agent import investigator
    from app.engine_api import store_path
    from app.store.sqlite_store import CaseStore
    store = CaseStore(store_path(request))
    try:
        run = store.latest_run(case_id) or {}
        labels = (run.get("machine_profile") or {}).get("domains") or {}
        domains = [{"id": k, "label": (labels.get(k) or {}).get("label", k), "status": v.get("status")} for k, v in (run.get("domains") or {}).items()]
        return {"draft": investigator.draft_lesson(store, case_id), "domains": domains}
    finally:
        store.close()


@router.get("/jobs/{job_id}")
def job_status(job_id: str, request: Request):
    job = _jobs.get(job_id)
    owner = (getattr(request.state, "user", None) or {}).get("workspace_id") if private() else None
    if not job or (private() and job.get("owner") != owner):
        raise HTTPException(404, "Job not found")
    return {k: v for k, v in job.items() if k != "owner"}

class EventNoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pattern: str = Field(min_length=1, max_length=400)
    verdict: str = Field(pattern="^(normal|relevant)$")
    scope: str = Field(default="machine", pattern="^(case|machine)$")
    lines: list = Field(default_factory=list, max_length=50)
    note: Optional[str] = Field(default=None, max_length=500)


@router.post("/cases/{case_id}/event-note")
def event_note(case_id: str, body: EventNoteIn, request: Request):
    """One click: an event Veyra classified is normal for this machine (or relevant after all). No form, no second approver."""
    from app.engine_api import store_path
    from app.ledger.model import LedgerError
    from app.store.sqlite_store import CaseStore
    store = CaseStore(store_path(request))
    try:
        return store.append(case_id, "event_note", body.model_dump(exclude_none=True), actor=actor_for(request, "Engineer"), source="case page")
    except LedgerError as error:
        raise HTTPException(422, str(error)) from None
    finally:
        store.close()


@router.get("/cases/{case_id}/record")
def case_record(case_id: str, request: Request):
    from app.agent import investigator
    from app.engine_api import store_path
    from app.store.sqlite_store import CaseStore
    store = CaseStore(store_path(request))
    try:
        return investigator.record(store, case_id)
    finally:
        store.close()


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conclusion: str = Field(pattern="^(correct|partly|wrong)$")
    recovered: str = Field(pattern="^(yes|not_yet|unknown)$")
    correction: Optional[str] = Field(default=None, max_length=1000)
    fix: Optional[str] = Field(default=None, max_length=1000)


@router.post("/cases/{case_id}/review")
def review_and_confirm(case_id: str, body: ReviewIn, request: Request):
    """Close by answering two questions; Veyra writes the closure (and the lesson) from the case."""
    from app.agent import investigator
    from app.engine_api import store_path
    from app.ledger.model import LedgerError
    from app.store.sqlite_store import CaseStore
    if body.conclusion != "correct" and not (body.correction or "").strip() and body.conclusion == "partly":
        raise HTTPException(422, "Say what is wrong in the conclusion")
    store = CaseStore(store_path(request))
    try:
        payload = investigator.review_to_closure(store, case_id, conclusion=body.conclusion, recovered=body.recovered,
                                                 correction=body.correction, fix=body.fix)
        return store.append(case_id, "case_closure", payload, actor=actor_for(request, "Engineer"), source="review and confirm")
    except LedgerError as error:
        raise HTTPException(422, str(error)) from None
    finally:
        store.close()


@router.get("/cases/{case_id}/log")
def case_log(case_id: str, request: Request):
    """The original log lines (real numbers, real values) for the case page."""
    from app.agent.investigator import LogTools
    from app.engine_api import store_path
    from app.store.sqlite_store import CaseStore
    store = CaseStore(store_path(request))
    try:
        tools = LogTools(store, case_id)
        return {"lines": tools.raw[:20000], "total": len(tools.raw), "available": tools.available()}
    finally:
        store.close()
