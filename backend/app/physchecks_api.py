"""Local engineering API: physical checks, guided replay, scenarios and outcomes.

LOCAL ONLY. Every route answers 404 unless VEYRA_ENGINEERING_LOCAL=true is set AND the request comes from the loopback
interface. The hosted demo never sets it, so confidential cases cannot be reached from a deployment. Private case
directories are read from VEYRA_PRIVATE_CASES_DIR (default ~/Veyra-private); the SYNTHETIC demo case is built in memory.

Writes (scenario create/review, outcome records) need the header x-veyra-actor naming the engineer; the actor kind is
always "engineer" here (agents never write through this API).
"""
from __future__ import annotations

import os
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.physchecks import agent_bridge, base, case_replay, casedir, ingested, investigation as INV, knowledge, outcomes as O, scenarios as SC, synthetic as S
from app.physchecks.store import Ledger

router = APIRouter(prefix="/engineering", tags=["engineering"])
SYNTHETIC_ID = "SYNTHETIC-UV-INCIDENT"
_lock = threading.Lock()
_ledger: Optional[Ledger] = None


def enabled() -> bool:
    return os.environ.get("VEYRA_ENGINEERING_LOCAL") == "true"


def _guard(request: Request) -> None:
    host = request.client.host if request.client else ""
    if not enabled() or host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        raise HTTPException(404, "Not found")


def ledger() -> Ledger:
    global _ledger
    with _lock:
        if _ledger is None:
            _ledger = Ledger()
        return _ledger


def reset_for_tests(path: Optional[Path] = None) -> None:
    global _ledger
    with _lock:
        _ledger = Ledger(path) if path else None
    _case_dir.cache_clear()


def _synthetic_meta() -> Dict[str, Any]:
    return {"case_id": SYNTHETIC_ID, "title": "SYNTHETIC stationary under-voltage incident (generated)", "synthetic": True, "confidential": False,
            "owner": "Veyra test fixture", "historical_reports": [], "historical_claims": [],
            "excluded_files": {}}


def _case(case_id: str):
    if ingested.is_recording_id(case_id):        # an uploaded recording, read through its signal profile (cached by the adapter)
        try:
            rec, prof, meta = ingested.load(case_id)
        except KeyError:
            raise HTTPException(404, f"no recording {case_id}")
        except ValueError as e:
            raise HTTPException(409, str(e))
        return rec, prof, ingested.case_meta(case_id, rec, prof, meta), {}, ingested.integrity(rec, prof)
    return _case_dir(case_id)


@lru_cache(maxsize=4)
def _case_dir(case_id: str):
    if case_id == SYNTHETIC_ID:
        return S.incident(SYNTHETIC_ID.lower()), S.profile(), _synthetic_meta(), {}, None
    for p in casedir.discover():
        cd = casedir.load(p)
        if cd.case_id == case_id:
            return cd.recording, cd.profile, cd.meta, cd.check_parameters(), cd.manifest_check
    raise HTTPException(404, f"no case {case_id}")


def _targets() -> List[Any]:
    """Recordings to preview a scenario on: other SYNTHETIC vehicles (labelled as such)."""
    return [(S.abrupt_correction("synthetic-vehicle-2", drop_pp=22.0), S.profile()), (S.healthy("synthetic-vehicle-3"), S.profile()),
            (S.protection_trip("synthetic-vehicle-4"), S.profile())]


@router.get("/cases")
def list_cases(request: Request) -> Dict[str, Any]:
    _guard(request)
    rows = [{"id": SYNTHETIC_ID, "title": _synthetic_meta()["title"], "synthetic": True, "confidential": False}]
    for p in casedir.discover():
        try:
            import json
            m = json.loads((p / "case.json").read_text(encoding="utf-8"))
            rows.append({"id": m["case_id"], "title": m.get("title"), "synthetic": bool(m.get("synthetic")), "confidential": bool(m.get("confidential"))})
        except Exception as e:  # a broken case directory is listed, not hidden
            rows.append({"id": p.name, "title": f"unreadable case directory: {e}", "synthetic": False, "confidential": True, "error": True})
    for m in ingested.list_recordings():
        c = ingested.chosen(m)
        if c is not None:
            rows.append({"id": m["id"], "title": f"{m.get('filename')} ({c['title']})", "synthetic": c["synthetic"], "confidential": not c["synthetic"],
                         "recording": True})
    return {"cases": rows, "private_root": "configured" if casedir.discover() else "none found"}


@router.get("/recordings")
def list_ingested(request: Request) -> Dict[str, Any]:
    """Recordings uploaded through Veyra's recording ingestion, with which signal profile can read each one, the
    automatic scan that ran when it arrived, and the latest stored result of every scenario on it."""
    _guard(request)
    rows = []
    for m in ingested.list_recordings():
        verdicts = ingested.matches(m)
        auto = ingested.latest_scan(ledger(), m["id"])
        rows.append({"id": m["id"], "filename": m.get("filename"), "format": (m.get("inspection") or {}).get("format"), "bytes": m.get("bytes"),
                     "uploaded_at": m.get("uploaded_at"), "uploaded_by": m.get("uploaded_by"),
                     "duration_s": (m.get("inspection") or {}).get("duration_s"),
                     "profile": next((v for v in verdicts if v["status"] == "compatible"), None), "profiles": verdicts,
                     "auto_scan": None if auto is None else auto["payload"] | {"at": auto["created_at"]},
                     "results": ingested.results_for(ledger(), m["id"]),
                     "investigation": _inv_row(m["id"])})
    return {"recordings": rows, "folder": "configured", "auto_scan": ingested._engineering_on(),
            "upload": os.environ.get("VEYRA_ENABLE_RUNTIME") == "true" and os.environ.get("VEYRA_ALLOW_RECORDINGS") == "true",
            "profiles": [{"id": d["id"], "title": d.get("title"), "origin": o, "synthetic": bool(d.get("synthetic")), "machine_class": d.get("machine_class"),
                          "formats": d["recording"].get("formats")} for o, d in ingested.recording_profiles()]}


def _inv_row(rid: str) -> Optional[Dict[str, Any]]:
    st = INV.status(ledger(), rid)
    last = st["latest"]
    if last is None:
        return {"running": bool(st["running"]), "mode": None}
    a = last.get("agent") or {}
    claims = a.get("constrained_claims") or []
    return {"running": bool(st["running"]), "mode": a.get("mode"), "reason": a.get("reason"), "model": a.get("model"), "at": last["at"],
            "trigger": last.get("trigger"), "steps": len(a.get("trace") or []),
            "verdicts": {v: sum(1 for c in claims if c.get("verdict") == v) for v in ("accepted", "downgraded", "flagged", "rejected")},
            "next_check": (last.get("deterministic") or {}).get("next_check"), "knowledge_used": len(last.get("knowledge_used") or [])}


@router.post("/recordings/{rid}/scan")
def rescan_recording(rid: str, request: Request) -> Dict[str, Any]:
    """Run the approved scenarios on one stored recording again (same code as the automatic scan on upload)."""
    _guard(request)
    try:
        return ingested.auto_scan(ledger(), rid, actor="veyra-scanner (requested)")
    except KeyError:
        raise HTTPException(404, f"no recording {rid}")


@router.get("/cases/{case_id}/replay")
def replay(case_id: str, request: Request, qualification: str = "none") -> Dict[str, Any]:
    _guard(request)
    rec, prof, meta, params, man = _case(case_id)
    try:
        last = INV.latest(ledger(), case_id)
        agent_run = None if last is None else (last["payload"].get("agent") or {}) | {"investigation_id": last["id"], "at": last["created_at"],
                                                                                       "trigger": last["payload"].get("trigger")}
        return case_replay.build(rec, prof, meta, params=params, ledger=ledger(), qualification=qualification, agent_run=agent_run,
                                 comparison_targets=_targets() + ingested.targets(exclude=case_id), manifest_check=man)
    except ValueError as e:
        raise HTTPException(400, str(e))


def _loader(case_id: str):
    def load():
        rec, prof, meta, params, _ = _case(case_id)
        return rec, prof, meta, params
    return load


def _open_for(case_id: str, actor: str) -> Dict[str, Any]:
    rec, prof, meta, params, _ = _case(case_id)
    checks = base.run_all(rec, prof, params)
    return O.open_case(ledger(), case_id, title=meta.get("title") or case_id, signature=O.signature_of(checks), actor=actor,
                       confidential=bool(meta.get("confidential")), context=knowledge.context_of(checks, prof, rec.synthetic))


@router.get("/cases/{case_id}/investigation")
def investigation_status(case_id: str, request: Request) -> Dict[str, Any]:
    """The latest stored investigation of this case (deterministic part, knowledge used, scenario results, AI investigator
    run or why it did not run) and whether one is running now."""
    _guard(request)
    rec, *_ = _case(case_id)
    st = INV.status(ledger(), case_id)
    st["agent_allowed"] = INV.allowed(rec.synthetic)
    st["agent_rule"] = ("SYNTHETIC: the AI investigator may run." if rec.synthetic else
                        "Not SYNTHETIC (treated as confidential, even if public): the AI investigator runs only after the data owner agreed and the operator set VEYRA_PRIVATE_ALLOW_AGENT=true"
                        + (" (set)." if agent_bridge.agent_allowed_by_operator() else " (not set)."))
    return st


@router.post("/cases/{case_id}/investigate")
def investigate_now(case_id: str, request: Request, x_veyra_actor: Optional[str] = Header(None)) -> Dict[str, Any]:
    _guard(request)
    actor = _actor(x_veyra_actor)
    _case(case_id)
    started = INV.start(ledger(), case_id, _loader(case_id), trigger="requested", actor=f"veyra-investigator (requested by {actor})")
    return {"started": started, "note": None if started else "an investigation of this case is already running"}


class AdoptIn(BaseModel):
    investigation_id: str
    claim_index: int


@router.post("/cases/{case_id}/investigation/adopt")
def adopt_claim(case_id: str, body: AdoptIn, request: Request, x_veyra_actor: Optional[str] = Header(None)) -> Dict[str, Any]:
    """An engineer takes one AI-investigator statement into the outcome ledger as a hypothesis proposal (origin 'agent')."""
    _guard(request)
    actor = _actor(x_veyra_actor)
    try:
        rec = INV.adopt(ledger(), case_id, investigation_id=body.investigation_id, claim_index=body.claim_index, actor=actor,
                        case_open=lambda: _open_for(case_id, actor))
    except O.OutcomeError as e:
        raise HTTPException(400, str(e))
    return {"record": rec, "state": O.state(ledger(), case_id)}


@router.get("/cases/{case_id}/series")
def series(case_id: str, request: Request, window: str, t0: float, t1: float, role: Optional[str] = None, field: Optional[str] = None,
           limit: int = 400) -> Dict[str, Any]:
    """Raw samples of one role (or of the role bound to `field`) with their source file and row, for drill-down."""
    _guard(request)
    rec, prof, *_ = _case(case_id)
    if role is None and field is not None:
        role = next((r for r, spec in prof.roles.items() if spec.get("field") == field), None)
    if not role or not prof.has_role(role):
        raise HTTPException(400, f"no signal role for {role or field}")
    s = prof.series(rec, window, role)
    pts = [x for x in s.samples if t0 <= x.t <= t1][: max(1, min(limit, 2000))]
    return {"case": case_id, "window": window, "role": role, "unit": s.unit, "excluded": s.excluded,
            "samples": [rec.ref(window, s.field, x.index).as_dict() | {"value": x.value} for x in pts]}


@router.get("/checks")
def checks_catalog(request: Request) -> Dict[str, Any]:
    _guard(request)
    return {"engine": base.ENGINE_VERSION, "checks": [c.spec.as_dict() for c in base.all_checks()]}


class ScenarioIn(BaseModel):
    case_id: str
    title: str = Field(min_length=3, max_length=200)
    finding_kind: str = "unexplained_soc_correction"
    check_id: str = "charge_accounting"
    applicability: Optional[Dict[str, Any]] = None
    params: Optional[Dict[str, Any]] = None


class ReviewIn(BaseModel):
    decision: str
    notes: str = ""


def _actor(x_veyra_actor: Optional[str]) -> str:
    if not x_veyra_actor or not x_veyra_actor.strip():
        raise HTTPException(400, "name the engineer in the x-veyra-actor header")
    return x_veyra_actor.strip()[:80]


@router.post("/scenarios")
def create_scenario(body: ScenarioIn, request: Request, x_veyra_actor: Optional[str] = Header(None)) -> Dict[str, Any]:
    _guard(request)
    rec, prof, meta, params, _ = _case(body.case_id)
    result = base.get(body.check_id).run(rec, prof, params.get(body.check_id)).as_dict()
    try:
        return SC.create_from_finding(ledger(), case_id=body.case_id, check_result=result, finding_kind=body.finding_kind, title=body.title,
                                      author=_actor(x_veyra_actor), applicability=body.applicability, params=body.params)
    except SC.ScenarioError as e:
        raise HTTPException(400, str(e))


@router.post("/scenarios/{sid}/review")
def review_scenario(sid: str, body: ReviewIn, request: Request, x_veyra_actor: Optional[str] = Header(None)) -> Dict[str, Any]:
    _guard(request)
    try:
        out = SC.review(ledger(), sid, reviewer=_actor(x_veyra_actor), reviewer_kind="engineer", decision=body.decision, notes=body.notes)
    except SC.ScenarioError as e:
        raise HTTPException(400, str(e))
    if out["status"] == "approved":
        # like a scenario in Alloy checks past missions: run it now on every stored recording a profile can read
        threading.Thread(target=lambda: ingested.backfill(ledger(), sid), daemon=True, name=f"backfill-{sid}").start()
        out = dict(out, backfill="started on stored recordings")
    return out


@router.get("/scenarios")
def list_scenarios(request: Request) -> Dict[str, Any]:
    _guard(request)
    return {"scenarios": SC.list_all(ledger())}


@router.post("/scan")
def scan(request: Request) -> Dict[str, Any]:
    """Run every approved scenario over the SYNTHETIC comparison vehicles and every local case."""
    _guard(request)
    targets = list(_targets())
    for p in casedir.discover():
        cd = casedir.load(p)
        targets.append((cd.recording, cd.profile))
    targets += ingested.targets()
    out = SC.scan(ledger(), targets, actor="veyra-scanner")
    return {k: v for k, v in out.items()}


class OutcomeIn(BaseModel):
    kind: str
    data: Dict[str, Any]


@router.post("/cases/{case_id}/outcomes")
def record_outcome(case_id: str, body: OutcomeIn, request: Request, x_veyra_actor: Optional[str] = Header(None)) -> Dict[str, Any]:
    _guard(request)
    actor = _actor(x_veyra_actor)
    fns = {"open": O.open_case, "propose": O.propose, "review": O.review, "performed": O.performed, "observe": O.observe,
           "verify": O.verify, "correct": O.correct, "close": O.close}
    if body.kind not in fns:
        raise HTTPException(400, f"kind must be one of {sorted(fns)}")
    data = dict(body.data)
    data["actor"] = actor
    if body.kind == "open":
        # the case signature (what retrieval matches future cases on) comes from this run's checks, not from the client
        rec, prof, meta, params, _ = _case(case_id)
        checks = base.run_all(rec, prof, params)
        data.setdefault("signature", O.signature_of(checks))
        data.setdefault("context", knowledge.context_of(checks, prof, rec.synthetic))
        data.setdefault("confidential", bool(meta.get("confidential")))
        data.setdefault("title", meta.get("title") or case_id)
    if body.kind != "open":
        data["actor_kind"] = "engineer"
    if body.kind == "propose":
        data.setdefault("origin", "engineer")
    try:
        rec = fns[body.kind](ledger(), case_id, **data)
    except (O.OutcomeError, TypeError) as e:
        raise HTTPException(400, str(e))
    return {"record": rec, "state": O.state(ledger(), case_id)}


@router.get("/cases/{case_id}/outcomes")
def outcome_state(case_id: str, request: Request) -> Dict[str, Any]:
    _guard(request)
    return O.state(ledger(), case_id)
