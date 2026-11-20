"""Investigation of one case or uploaded recording, run and STORED: the step between "a recording arrived" and "an
engineer looks at it".

run() does, in order:
  1. the deterministic physical checks, facts, hypothesis assessment (with this case's own verified outcomes);
  2. retrieval of earlier engineer-verified knowledge (knowledge.for_case) and of scenario results on this recording
     and on the other stored recordings (cross-run);
  3. the AI investigator (agent_bridge) with those as tools, IF it may run: the recording is SYNTHETIC, or the operator
     has set VEYRA_PRIVATE_ALLOW_AGENT=true after the data owner agreed. Otherwise the record says "blocked"; when no
     model is configured it says "unavailable". Nothing is invented in either case;
  4. every agent statement passes hypotheses.constrain_claims; the record keeps the verdicts.
The result is appended to the engineering ledger as kind "investigation" on subject "investigation:<id>". An agent statement
becomes a tracked hypothesis only when an engineer adopts it (adopt()), and a cause only when an engineer verifies it.
"""
from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from app.physchecks import expert_layer, agent_bridge, base, facts as facts_mod, hypotheses, knowledge, outcomes as O, planner
from app.physchecks.store import Ledger

KIND = "investigation"
VERSION = "veyra-investigation-v1"
MODEL_FACTORY: Optional[Callable[[], Any]] = None          # tests inject a scripted model here
_running: Dict[str, str] = {}
_lock = threading.Lock()


def _subject(case_id: str) -> str:
    return f"investigation:{case_id}"


def scenario_context(led: Ledger, rec_id: str) -> Dict[str, Any]:
    """Latest stored result per (scenario, recording): this recording's, and the same scenarios on other recordings."""
    latest: Dict[tuple, Dict[str, Any]] = {}
    for r in led.records(kinds=["scan_result"]):
        p = r["payload"]
        latest[(p.get("scenario_id"), p.get("recording"))] = p | {"at": r["created_at"]}
    keep = ("scenario_id", "scenario_version", "scenario_title", "recording", "outcome", "applicability", "summary", "matched_original", "trigger", "synthetic")
    here = [{k: p.get(k) for k in keep} for (sid, rid), p in latest.items() if rid == rec_id]
    ids = {x["scenario_id"] for x in here}
    others = [{k: p.get(k) for k in keep} for (sid, rid), p in latest.items() if rid != rec_id and (not ids or sid in ids)]
    return {"here": here, "other_recordings": others[:50]}


def allowed(synthetic: bool) -> bool:
    return synthetic or agent_bridge.agent_allowed_by_operator()


def run(led: Ledger, case_id: str, rec, prof, meta: Dict[str, Any], params=None, *, trigger: str, actor: str = "veyra-investigator",
        model=None) -> Dict[str, Any]:
    started = datetime.now(timezone.utc).isoformat()
    checks = base.run_all(rec, prof, params)
    F = facts_mod.derive(rec, prof, checks)
    verified = O.verified_for(led, case_id)
    A = hypotheses.assess(checks, F, verified, machine=hypotheses.machine_of(prof))
    expert_layer.enrich(rec, prof, checks, F, A, reported_symptom=(meta or {}).get("reported_symptom"))
    K = knowledge.for_case(led, case_id, checks, A, prof, rec.synthetic)
    plan = planner.plan(A, checks, prior=knowledge.prior_for_planner(K))
    ctx = {"knowledge": K, "scenario_results": scenario_context(led, rec.id)}
    may_run = allowed(rec.synthetic)
    if not may_run:
        model = None            # the permission rule is enforced here, whatever model is configured (including test models)
    elif model is None and MODEL_FACTORY is not None:
        model = MODEL_FACTORY()
    linked = linked_case(rec.id)
    if linked is not None and os.environ.get("VEYRA_UNIFIED_INVESTIGATOR", "true") != "false":
        out = _main_investigator(linked, model, may_run, ctx)
    else:
        out = agent_bridge.investigate(rec, prof, params, model=model, allow_external_model=may_run, context=ctx, verified=verified)
    top = (plan.get("recommended") or [None])[0]
    payload = {
        "version": VERSION, "case_id": case_id, "trigger": trigger, "synthetic": rec.synthetic, "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(), "recording_digest": rec.digest(),
        "deterministic": {
            "checks": {cid: {"outcome": r["outcome"], "summary": r["summary"]} for cid, r in checks.items()},
            "hypotheses": [{"id": h["id"], "label": h["label"], "role": h["role"], "status": h["status"]} for h in A["hypotheses"]],
            "summary": A.get("summary"),
            "next_check": None if top is None else {"id": top["id"], "title": top.get("title"), "prior": top.get("prior")},
        },
        "knowledge_used": [{k: i[k] for k in ("case_id", "hypothesis_id", "result", "applicability")} for i in K["items"]],
        "scenario_results": ctx["scenario_results"],
        "agent": {k: out.get(k) for k in ("mode", "reason", "model", "provider", "trace", "agent_report", "constrained_claims", "accepted_as_is", "note",
                                          "version", "investigator", "engine_case_id", "safety_gate")},
    }
    return led.append(KIND, _subject(case_id), payload, actor=actor, actor_kind="system")


def linked_case(rid: str) -> Optional[str]:
    """The engine case built from this uploaded recording (debrief), if any: the main investigator then runs on it, with
    the evidence sheet AND the physical layer (app.agent.physical_layer)."""
    from app.physchecks import ingested
    if not ingested.is_recording_id(rid):
        return None
    try:
        ids = ingested.recording_meta(rid).get("case_ids") or []
    except KeyError:
        return None
    return ids[-1] if ids else None


def engine_store_path():
    from app.physchecks import ingested
    return ingested.recordings_root().parent / "engine.db" if os.getenv("VEYRA_ENGINE_DB") is None else os.getenv("VEYRA_ENGINE_DB")


def _inquiry_hint() -> str:
    from app.agent.inquiry import strategy
    return {"free": " Then test the competing causes with the inquiry tools before you conclude.",
            "coverage": " Then call inquiry_plan: for every unresolved causal distinction, establish whether this recording can discriminate it and run "
                        "the highest-value test before you conclude.",
            "infogain": " Then call inquiry_plan and run the tests with the highest expected_bits, until no available test can split the causes still tied."}[strategy()]


def _main_investigator(case_id: str, model, may_run: bool, ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Veyra's main AI investigator on the linked engine case; its record is mapped into this ledger's agent fields."""
    from app.agent import investigator as INV
    from app.store.sqlite_store import CaseStore
    store = CaseStore(engine_store_path())
    try:
        if store.latest_run(case_id) is None:
            store.investigate(case_id, actor="veyra-investigator")
        if may_run and model is None:
            from app.agent.llm import from_environment
            model = from_environment()
        if not may_run or not model.available():
            # no model may run: the main investigator's rule-based reading still runs (nothing leaves this machine), and the
            # action gate still applies, so the safety findings and a safe next check exist without any AI
            r = INV.investigate(store, case_id, model=None, physical_context=ctx)
            g = r.get("safety_gate") or {}
            return {"mode": "blocked" if not may_run else "unavailable", "investigator": "main", "engine_case_id": case_id,
                    "reason": ("confidential case: the AI investigator would send case data to an external model. It runs only when the data owner "
                               "has agreed and the operator sets VEYRA_PRIVATE_ALLOW_AGENT=true." if not may_run else "no model is configured (no API key / CLI).")
                              + " Veyra's rule-based reading and the action safety gate ran instead.",
                    "agent_report": {"summary": (r.get("result") or {}).get("headline"),
                                     "next_check": ((r.get("result") or {}).get("next_action") or {}).get("action")},
                    "safety_gate": {k: g.get(k) for k in ("action_verdict", "outcome", "requires_confirmation", "inserted_findings", "blocked_actions")},
                    "version": INV.INVESTIGATOR_VERSION}
        r = INV.investigate(store, case_id, model=model, physical_context=ctx, first_message=(
            f"Investigate case {case_id}. Start with overview, case_context, physical_checks and physical_hypotheses. "
            "Earlier verified engineering knowledge for this recording: " + str(len((ctx.get("knowledge") or {}).get("items") or [])) + " item(s); "
            "read it with physical_earlier_knowledge."
            + ("" if os.environ.get("VEYRA_INQUIRY", "on").strip().lower() in ("off", "0", "false") else _inquiry_hint())))
        res = r.get("result") or {}
        ph = r.get("physical") or {}
        return {"mode": r["mode"] if r["mode"] == "agent" else "agent_failed", "investigator": "main", "engine_case_id": case_id,
                "model": r.get("model"), "provider": getattr(model, "provider", None), "reason": r.get("fallback_reason"),
                "trace": [{"step": i, "tool": st["tool"], "input": st["input"], "error": str(st.get("summary", "")).startswith("error:")}
                          for i, st in enumerate(r.get("steps") or [])],
                "agent_report": {"summary": res.get("headline"), "explanation": res.get("explanation"),
                                 "hypotheses": [{"id": h.get("physical_ref") or h.get("id"), "status": h.get("status"), "statement": f"{h.get('area')}: {h.get('claim')}"}
                                                for h in res.get("hypotheses") or []],
                                 "next_check": (res.get("next_action") or {}).get("action")},
                "constrained_claims": ph.get("constrained_claims") or [],
                "accepted_as_is": all(c.get("verdict") == "accepted" for c in ph.get("constrained_claims") or []),
                "note": "Veyra's main investigator (evidence sheet + physical layer), held to the quote/number verifier and to the physical "
                        "assessment by reference. Statements are proposals until an engineer verifies them.",
                "version": INV.INVESTIGATOR_VERSION}
    finally:
        store.close()


def latest(led: Ledger, case_id: str) -> Optional[Dict[str, Any]]:
    rows = led.records(subject=_subject(case_id), kinds=[KIND])
    return rows[-1] if rows else None


def status(led: Ledger, case_id: str) -> Dict[str, Any]:
    last = latest(led, case_id)
    return {"running": _running.get(case_id), "latest": None if last is None else last["payload"] | {"id": last["id"], "at": last["created_at"]},
            "agent_allowed": None}


def start(led: Ledger, case_id: str, load: Callable[[], tuple], *, trigger: str, actor: str = "veyra-investigator") -> bool:
    """Run in the background; False if one is already running for this case."""
    with _lock:
        if case_id in _running:
            return False
        _running[case_id] = datetime.now(timezone.utc).isoformat()

    def go():
        try:
            rec, prof, meta, params = load()
            run(led, case_id, rec, prof, meta, params, trigger=trigger, actor=actor)
        except Exception as error:  # recorded with the case, so the page can show it
            try:
                led.append(KIND, _subject(case_id), {"version": VERSION, "case_id": case_id, "trigger": trigger,
                                                     "agent": {"mode": "failed", "reason": f"{type(error).__name__}: {error}"}},
                           actor=actor, actor_kind="system")
            except Exception:
                pass
        finally:
            with _lock:
                _running.pop(case_id, None)
    threading.Thread(target=go, daemon=True, name=f"investigate-{case_id}").start()
    return True


def adopt(led: Ledger, case_id: str, *, investigation_id: str, claim_index: int, actor: str, case_open: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    """An engineer takes one agent statement into the outcome ledger as a hypothesis proposal (origin 'agent').
    The text is the stored agent text, not something the client sends; rejected statements cannot be adopted."""
    r = led.get(investigation_id)
    if not r or r["kind"] != KIND or r["subject"] != _subject(case_id):
        raise O.OutcomeError("no such investigation for this case")
    claims = (r["payload"].get("agent") or {}).get("constrained_claims") or []
    if not 0 <= claim_index < len(claims):
        raise O.OutcomeError("no such agent statement")
    c = claims[claim_index]
    if c.get("verdict") == "rejected":
        raise O.OutcomeError("this agent statement was rejected by the physical checks: " + "; ".join(c.get("reasons") or []))
    hid = c.get("hypothesis") or (c.get("hypotheses") or [None])[0]
    if not hid:
        raise O.OutcomeError("this agent statement names no hypothesis; record it as an engineer note instead")
    if not O.state(led, case_id).get("signature"):
        case_open()
    statement = f"{c.get('text')} [AI investigator {r['payload']['agent'].get('model')}, investigation {investigation_id}; verdict {c.get('verdict')}]"
    return O.propose(led, case_id, hypothesis_id=hid, statement=statement, origin="agent", actor=actor, actor_kind="engineer")
