"""HTTP API for Investigation Engine v2 (same core and store as the CLI).

Local runtime only (same guard as /runtime). The store is a separate append-only SQLite file
(VEYRA_ENGINE_DB, default .local-runtime/engine.db) until the PostgreSQL migration (M2).
"""
import base64
import binascii
import os
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from app.context import model as context_model
from app.context.model import ContextError
from app.engine import checks as check_policy
from app.workflows import report as report_html
from app.ingest import catalog
from app.ingest.profile import ProfileError, parse_profile
from app.ledger.model import LedgerError
from app.private import actor_for
from app.runtime_engine import enabled
from app.store.sqlite_store import CaseStore, StoreError

router = APIRouter(prefix="/engine", tags=["Investigation Engine v2"], dependencies=[Depends(enabled)])
DEFAULT_DB = Path(__file__).resolve().parents[2] / ".local-runtime" / "engine.db"


def store_path(request: Request) -> Path:
    from app import private
    if private.private():
        return private.store_path_for(request)
    return Path(os.getenv("VEYRA_ENGINE_DB") or DEFAULT_DB)


def get_store(request: Request):
    store = CaseStore(store_path(request))
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
    as_of: Optional[str] = Field(default=None, max_length=40)  # replay with only what was known then (ISO 8601 with timezone)


class LedgerIn(Strict):
    kind: Literal["check_review", "check_execution", "decision", "dispatch_preparation", "action", "outcome", "outcome_review", "recurrence_check",
                  "support_milestone", "intervention_report", "intervention_review", "reference_declared", "triage", "hypothesis_status",
                  "reproduction_attempt", "case_closure", "normal_proposal", "normal_review", "decision_proposal", "request_sent"]
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


def _canonical_profile(fmt: str) -> Dict[str, Any]:
    columns = {"timestamp": "time", "asset": "machine", "signal": "signal", "value": "value", "unit": "unit", "scope": "scope",
               "event_value": "event", "code": "code", "command_id": "command_id"}
    return {"profile_id": f"veyra-canonical-{fmt}", "version": 1, "format": fmt, "synthetic": False, "columns": columns,
            "signal_map": {s["id"]: s["id"] for s in catalog.describe()["signals"]}, "clock_domain": "machine_clock",
            "timestamp_format": "iso8601", "unmapped_signals": "retain",
            "notes": "Veyra canonical format: one row per reading; signal names are catalog ids (e.g. network.rtt, actuator.output)."}


@router.get("/profiles")
def import_profiles():
    """Profiles an upload can use: the canonical format, plus the built-in synthetic demo profiles."""
    import json
    fixtures = Path(__file__).resolve().parents[2] / "fixtures" / "synthetic" / "remote_delay"
    out = [{"id": "veyra-canonical-csv", "label": "Veyra canonical CSV (real data)", "synthetic": False, "profile": _canonical_profile("csv"),
            "help": "Columns: time (ISO 8601 with timezone), machine, signal, value, unit, scope, event, code, command_id. Signal = a catalog id."},
           {"id": "veyra-canonical-jsonl", "label": "Veyra canonical JSON Lines (real data)", "synthetic": False, "profile": _canonical_profile("jsonl"),
            "help": "One JSON object per line with the same keys as the canonical CSV."}]
    for name, label in (("synthetic_console_profile.json", "Synthetic EX1 console log"), ("synthetic_vehicle_profile.json", "Synthetic EX1 vehicle log"),
                        ("synthetic_vehicle_io_profile.json", "Synthetic EX1 boom input/response"), ("synthetic_hydraulics_profile.json", "Synthetic EX1 hydraulics")):
        path = fixtures / name
        if path.exists():
            profile = json.loads(path.read_text())
            out.append({"id": profile["profile_id"], "label": label, "synthetic": True, "profile": profile, "help": profile.get("notes", "")})
    return {"profiles": out, "signals": [s["id"] for s in catalog.describe()["signals"]]}


@router.post("/cases")
def create_case(body: CaseIn, request: Request, store: CaseStore = Depends(get_store)):
    return _guard(store.create_case, **{**body.model_dump(), "actor": actor_for(request, body.actor)})


@router.get("/cases")
def list_cases(store: CaseStore = Depends(get_store)):
    return store.cases()


def _rerun_allowed(case_id: str) -> bool:
    from app.engine_seed import SEEDED_IDS
    return case_id not in SEEDED_IDS


@router.get("/cases/{case_id}")
def get_case(case_id: str, store: CaseStore = Depends(get_store)):
    case = _guard(store.case, case_id)
    return {"case": case, "rerun_allowed": _rerun_allowed(case_id), "imports": store.imports(case_id, include_rows=False), "latest_run": store.latest_run(case_id),
            "runs": [{"id": r["id"], "created_at": r["created_at"], "run_hash": r["run_hash"]} for r in store.runs(case_id)],
            "ledger": store.ledger(case_id), "context": store.context(case["machine_id"])}


@router.post("/cases/{case_id}/imports/preview")
def preview_import(case_id: str, body: ImportIn, store: CaseStore = Depends(get_store)):
    profile = _guard(parse_profile, body.profile)
    result = _guard(store.preview_import, case_id, _bytes(body), body.filename, profile)
    return {**result.to_dict(include_rows=False), "preview_rows": [{k: v for k, v in r.items() if k != "raw"} for r in result.rows[:50]], "stored": False}


@router.post("/cases/{case_id}/imports")
def commit_import(case_id: str, body: ImportIn, request: Request, store: CaseStore = Depends(get_store)):
    profile = _guard(parse_profile, body.profile)
    record, result = _guard(store.commit_import, case_id, _bytes(body), body.filename, profile, actor=actor_for(request, body.actor), review_reason=body.review_reason)
    return {"import": record, "report": result.to_dict(include_rows=False), "stored": True}


@router.post("/machines/{machine_id}/context")
def assert_context(machine_id: str, body: ContextIn, request: Request, store: CaseStore = Depends(get_store)):
    raw = {**body.model_dump(), "machine_id": machine_id, "actor": actor_for(request, body.actor)}
    if raw["value"] is None:
        raw.pop("value")
    return _guard(store.assert_context, raw)


@router.get("/machines/{machine_id}/context")
def machine_context(machine_id: str, at: Optional[str] = None, known_at: Optional[str] = None, store: CaseStore = Depends(get_store)):
    return _guard(store.context, machine_id, at=at, known_at=known_at)


@router.post("/cases/{case_id}/runs")
def run(case_id: str, body: RunIn, request: Request, store: CaseStore = Depends(get_store)):
    if not _rerun_allowed(case_id):
        raise HTTPException(409, "This demo case shows its recorded investigation; investigations are re-run in full deployments.")
    baseline = (body.baseline_start, body.baseline_end) if body.baseline_start or body.baseline_end else None
    if baseline and not all(baseline):
        raise HTTPException(422, "Give both baseline_start and baseline_end")
    result = _guard(store.investigate, case_id, actor=actor_for(request, body.actor), goal=body.goal, baseline_window=baseline, as_of=body.as_of)
    if body.as_of is None:  # a replay is a record of what was known then; Veyra does not act on it
        _autopilot(store, case_id)
    return result


@router.post("/cases/{case_id}/checks/{check_id}/execute")
def execute(case_id: str, check_id: str, body: ExecuteIn, request: Request, store: CaseStore = Depends(get_store)):
    return _guard(store.execute_automatic, case_id, check_id, actor=actor_for(request, body.actor))


@router.post("/cases/{case_id}/ledger")
def append(case_id: str, body: LedgerIn, request: Request, store: CaseStore = Depends(get_store)):
    if body.kind == "check_execution" and body.payload.get("status") == "automatic_read_only":
        raise HTTPException(422, "Automatic execution results can only be generated by the engine execution endpoint")
    entry = _guard(store.append, case_id, body.kind, body.payload, actor=actor_for(request, body.actor), source=body.source)
    if body.kind in ("check_review", "check_execution", "request_sent"):
        _autopilot(store, case_id)
    return entry


def autopilot_enabled() -> bool:
    import os
    return os.getenv("VEYRA_AUTOPILOT") == "true"


def _autopilot(store, case_id: str) -> None:
    """Best effort: Veyra moves the case forward after a change. A failure here never fails the person's request."""
    if not autopilot_enabled():
        return
    from app.workflows import autopilot
    try:
        autopilot.advance(store, case_id)
    except Exception:  # noqa: BLE001 - the view shows where the case stands; nothing is lost
        import logging
        logging.getLogger("veyra.autopilot").exception("autopilot advance failed for %s", case_id)
    _auto_agent(store, case_id)


AGENT_RUNS: Dict[tuple, Dict[str, Any]] = {}


def _auto_agent(store, case_id: str) -> None:
    """Opt-in (VEYRA_AUTOPILOT_AGENT=true): the AI agent writes its first report in the background after an investigation.
    Same rules as a manual run: real case data goes to a model only with a recorded model_authorization."""
    import os
    import threading
    if os.getenv("VEYRA_AUTOPILOT_AGENT") != "true":
        return
    run = store.latest_run(case_id)
    if run is None:
        return
    key = (str(store.path), case_id, run["id"])
    if key in AGENT_RUNS or any(e["kind"] == "agent_report" and e["payload"].get("run_id") == run["id"] for e in store.ledger(case_id)):
        return
    from app.agent.runner import AgentError, egress_allowed, run_agent
    model = _agent_model()
    if not model.available():
        AGENT_RUNS[key] = {"state": "off", "detail": "No model configured"}
        return
    if egress_allowed(store, case_id, model.provider) is None:
        AGENT_RUNS[key] = {"state": "needs_authorization", "detail": "This case holds real data: record a model authorization to let the agent read it."}
        return
    AGENT_RUNS[key] = {"state": "running"}

    def work(path=store.path):
        own = CaseStore(path)
        try:
            run_agent(own, case_id, model=model)
            AGENT_RUNS[key] = {"state": "done"}
        except AgentError as error:
            AGENT_RUNS[key] = {"state": "failed", "detail": str(error)[:300]}
        except Exception as error:  # noqa: BLE001
            AGENT_RUNS[key] = {"state": "failed", "detail": f"{type(error).__name__}"}
        finally:
            own.close()
    threading.Thread(target=work, daemon=True).start()


def _agent_status(store, case_id: str) -> Optional[Dict[str, Any]]:
    run = store.latest_run(case_id)
    return AGENT_RUNS.get((str(store.path), case_id, run["id"])) if run else None


@router.get("/cases/{case_id}/autopilot")
def autopilot_view(case_id: str, store: CaseStore = Depends(get_store)):
    from app.workflows import autopilot
    return {**_guard(autopilot.view, store, case_id), "enabled": autopilot_enabled(), "agent": _agent_status(store, case_id)}


@router.post("/cases/{case_id}/autopilot")
def autopilot_advance(case_id: str, store: CaseStore = Depends(get_store)):
    from app.workflows import autopilot
    out = _guard(autopilot.advance, store, case_id)
    _auto_agent(store, case_id)
    return {**out, "enabled": autopilot_enabled(), "agent": _agent_status(store, case_id)}


@router.get("/cases/{case_id}/series")
def case_series(case_id: str, signal: str, start: float, end: float, scope: Optional[str] = None, max_points: int = 400,
                store: CaseStore = Depends(get_store)):
    """One signal's recorded samples in a window, for the evidence plots. Read-only; at most 2,000 points."""
    if not 50 <= max_points <= 2000 or end - start > 6 * 3600:
        raise HTTPException(422, "max_points must be 50 to 2000 and the window at most 6 hours")
    return _guard(store.series, case_id, signal, scope, start, end, max_points)


@router.get("/cases/{case_id}/states")
def case_states_view(case_id: str, store: CaseStore = Depends(get_store)):
    """Each part's evidence stage, each check's state and each decision's state, derived from the ledger."""
    from app.engine import states as case_states
    _guard(store.case, case_id)
    return case_states.lifecycle(store.latest_run(case_id), store.ledger(case_id))


@router.get("/cases/{case_id}/ledger")
def ledger(case_id: str, store: CaseStore = Depends(get_store)):
    _guard(store.case, case_id)
    return {"entries": store.ledger(case_id), "verification": store.verify(case_id)}


@router.get("/machines/{machine_id}/history")
def machine_history(machine_id: str, synthetic: Optional[bool] = None, store: CaseStore = Depends(get_store)):
    return {"machine_id": machine_id, "cases": store.machine_history(machine_id, synthetic=synthetic)}


class DemoIn(Strict):
    actor: str = Field(default="Field engineer (synthetic demo)", min_length=1, max_length=150)


@router.post("/demo/field-case")
def demo_field_case(body: DemoIn, store: CaseStore = Depends(get_store)):
    """Load the SYNTHETIC field-case story (prior case + intervention case) into the local store."""
    from app.workflows.demo import build_field_case_story
    return _guard(build_field_case_story, store)


# ---------- connectors, triggers and the agent ----------
class AcquireIn(Strict):
    actor: str = Field(min_length=1, max_length=150)
    window_start: Optional[str] = None
    window_end: Optional[str] = None


class AgentIn(Strict):
    actor: str = Field(default="Veyra agent", min_length=1, max_length=150)
    question: Optional[str] = Field(default=None, max_length=600)


class MethodIn(Strict):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=400)
    code: str = Field(min_length=1, max_length=12_000)
    inputs: List[str] = Field(default_factory=lambda: ["a"], max_length=8)
    actor: str = Field(default="Engineer", min_length=1, max_length=150)


def _agent_model():
    from app.agent.llm import from_environment
    return from_environment()


def _connector_guard(fn, *args, **kwargs):
    from app.agent.runner import AgentError
    from app.automation.pipeline import EventError
    from app.connectors.base import ConnectorError
    try:
        return _guard(fn, *args, **kwargs)
    except (ConnectorError, EventError, AgentError) as error:
        raise HTTPException(422, str(error)) from None


@router.get("/connectors")
def connectors_status():
    from app.connectors import base
    config = _connector_guard(base.load_config)
    model = _agent_model()
    return {"config_path": config["path"], "loaded": config["loaded"], "connectors": [c.describe() for c in _connector_guard(base.build, config)],
            "webhook_sources": sorted((config.get("webhooks") or {}).get("sources", {})),
            "agent": {"provider": model.provider, "model": model.model, "configured": model.available()}}


@router.post("/cases/{case_id}/acquire")
def acquire_evidence(case_id: str, body: AcquireIn, store: CaseStore = Depends(get_store)):
    from app.automation.pipeline import acquire
    from app.connectors import base
    window = None
    if body.window_start or body.window_end:
        if not (body.window_start and body.window_end):
            raise HTTPException(422, "Give both window_start and window_end")
        from datetime import datetime
        window = base.Window(datetime.fromisoformat(body.window_start.replace("Z", "+00:00")), datetime.fromisoformat(body.window_end.replace("Z", "+00:00")))
    config = _connector_guard(base.load_config)
    return {"acquisitions": _connector_guard(acquire, store, case_id, base.build(config), actor=body.actor, window=window, trigger="manual")}


@router.post("/cases/{case_id}/agent")
def run_case_agent(case_id: str, body: AgentIn, store: CaseStore = Depends(get_store)):
    from app.agent.runner import run_agent
    return _connector_guard(run_agent, store, case_id, model=_agent_model(), actor=body.actor, question=body.question)  # the agent's own name, also in private mode


@router.get("/cases/{case_id}/next-action")
def next_action_state(case_id: str, store: CaseStore = Depends(get_store)):
    from app.workflows import next_action
    return _guard(next_action.state, store, case_id)


@router.post("/cases/{case_id}/next-action/plan")
def next_action_plan(case_id: str, store: CaseStore = Depends(get_store)):
    from app.workflows import next_action
    return _guard(next_action.make_plan, store, case_id)


@router.post("/cases/{case_id}/next-action/report")
def next_action_report(case_id: str, store: CaseStore = Depends(get_store)):
    from app.workflows import next_action
    return _guard(next_action.make_report, store, case_id)


class DecisionCheckIn(Strict):
    action: str
    purpose: str
    urgent: bool = False
    target_part: Optional[str] = None
    hypothesis: Optional[Dict[str, Any]] = None
    relies_on: List[Dict[str, Any]] = Field(default_factory=list, max_length=6)
    note: Optional[str] = Field(default=None, max_length=600)
    actor: str = Field(default="Engineer", min_length=1, max_length=150)


@router.get("/cases/{case_id}/decision-check")
def decision_check_suggest(case_id: str, store: CaseStore = Depends(get_store)):
    from app.workflows import decision_check
    return _guard(decision_check.suggest, store, case_id)


@router.post("/cases/{case_id}/decision-check")
def decision_check_run(case_id: str, body: DecisionCheckIn, request: Request, store: CaseStore = Depends(get_store)):
    from app.workflows import decision_check
    proposal = body.model_dump(exclude={"actor"})
    return _guard(decision_check.check, store, case_id, proposal, actor=actor_for(request, body.actor))


@router.get("/methods")
def list_methods(store: CaseStore = Depends(get_store)):
    from app.compute import methods, sandbox
    return {"built_in": methods.BUILTIN, "built_in_version": methods.METHODS_VERSION, "team": methods.Library(store.path).all(), "sandbox": sandbox.isolation()}


@router.post("/methods")
def save_method(body: MethodIn, request: Request, store: CaseStore = Depends(get_store)):
    from app.compute import methods
    from app.private import actor_for
    return _guard(methods.Library(store.path).save, name=body.name, description=body.description, code=body.code, inputs=body.inputs,
                  author=actor_for(request, body.actor))


def _process_event(source: str, raw: bytes, timestamp, signature, config, secret, store_path) -> dict:
    import hashlib
    import json as _json
    from app.agent.runner import run_agent
    from app.automation import pipeline
    sources = (config.get("webhooks") or {}).get("sources", {})
    if source not in sources:
        raise pipeline.EventError(f"Unknown webhook source '{source}'")
    pipeline.verify_signature(secret, timestamp, signature, raw)
    try:
        payload = _json.loads(raw)
    except ValueError:
        raise pipeline.EventError("Body is not JSON") from None
    event = pipeline.normalize_event(source, payload, sources[source])
    store = CaseStore(store_path)
    try:
        model = _agent_model()
        auto_agent = (config.get("automation") or {}).get("auto_agent", True) and model.available()
        agent = (lambda cid: run_agent(store, cid, model=model)) if auto_agent else None
        result = pipeline.handle_event(store, event, config=config, agent=agent, raw_digest=hashlib.sha256(raw).hexdigest())
        if not auto_agent:
            result["agent"] = "not run: the agent is not configured (API key, or VEYRA_AGENT_BACKEND=claude-code with Claude Code installed)"
        return result
    finally:
        store.close()


@router.post("/webhooks/{source}")
async def webhook(source: str, request: Request):
    """Signed events from alerting, ticketing or fleet systems open and investigate a case automatically."""
    import os
    from app.connectors import base
    raw = await request.body()
    config = _connector_guard(base.load_config)
    secret_env = (config.get("webhooks") or {}).get("secret_env")
    secret = os.environ.get(secret_env or "") if secret_env else None
    if not secret:
        raise HTTPException(503, "Webhooks are not configured: name a secret_env in the connector configuration and set it")
    return await run_in_threadpool(_connector_guard, _process_event, source, raw, request.headers.get("x-veyra-timestamp"),
                                   request.headers.get("x-veyra-signature"), config, secret, Path(os.getenv("VEYRA_ENGINE_DB") or DEFAULT_DB))


@router.post("/demo/live-event")
def demo_live_event():
    """SYNTHETIC: the stand-in fleet platform posts a signed takeover event; the full pipeline runs."""
    import json as _json
    import os
    import time as _time
    from app.automation import demo, pipeline
    config = demo.demo_config()
    raw = _json.dumps(demo.demo_event_payload()).encode()
    timestamp = str(int(_time.time()))
    signature = pipeline.sign(demo.DEMO_SECRET, timestamp, raw)
    from app.workflows.demo import ensure_machine_class
    store = CaseStore(Path(os.getenv("VEYRA_ENGINE_DB") or DEFAULT_DB))
    try:
        ensure_machine_class(store)  # SYNTHETIC EX1 is an excavator
    finally:
        store.close()
    return _connector_guard(_process_event, demo.DEMO_SOURCE, raw, timestamp, signature, config, demo.DEMO_SECRET,
                            Path(os.getenv("VEYRA_ENGINE_DB") or DEFAULT_DB))


mock_fleet = APIRouter(prefix="/mock-fleet", tags=["SYNTHETIC mock fleet API"], dependencies=[Depends(enabled)])


@mock_fleet.get("/v1/machines/{machine}/{stream}")
def mock_fleet_records(machine: str, stream: str, start: str = Query(alias="from"), end: str = Query(alias="to")):
    from app.automation import demo
    try:
        return {"synthetic": True, "machine": machine, "stream": stream, "records": demo.mock_records(stream, machine, start, end)}
    except ValueError as error:
        raise HTTPException(404, str(error)) from None


@router.get("/cases/{case_id}/episodes")
def episodes(case_id: str, store: CaseStore = Depends(get_store)):
    return _guard(store.episodes, case_id)


@router.get("/cases/{case_id}/report", response_class=HTMLResponse)
def report(case_id: str, store: CaseStore = Depends(get_store)):
    from app.private import private
    return HTMLResponse(_guard(report_html.render, store, case_id, authenticated=private()))


@router.get("/machine-types")
def machine_types():
    from app.machines import profiles
    return {"machine_types": [{"id": k, "name": v.get("name", k), "description": v.get("description", "")} for k, v in sorted(profiles.load_all().items())]}
