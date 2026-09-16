import base64
import hashlib
import json
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import (
    Asset,
    CostImpact,
    Decision,
    DecisionOption,
    DecisionPackage,
    Evidence,
    EvidenceSnapshot,
    ExecutedAction,
    Investigation,
    LateEvidence,
    ObservabilityState,
    Outcome,
    PrecedentComparison,
    ReviewFlag,
)

BASE = datetime(2026, 9, 3, 14, 0, 0)
DEMO_DECISION_TIME = BASE + timedelta(minutes=27)
TIMESTAMP_AUTHORITY = "Veyra demo timestamp authority"
STATE_KEYS = [
    "application_version",
    "firmware_version",
    "configuration",
    "network_profile",
    "connection_type",
    "process_health",
]


def iso(dt: datetime) -> str:
    return dt.isoformat() + "Z"


def evidence(
    asset_id: str,
    event_type: str,
    minutes: int,
    known_delay: int,
    source: str,
    evidence_class: str,
    payload: dict[str, Any],
    eid: Optional[str] = None,
    ingested_delay_seconds: int = 8,
) -> Evidence:
    event_time = BASE + timedelta(minutes=minutes)
    known_at = event_time + timedelta(minutes=known_delay)
    return Evidence(
        id=eid or f"ev-{uuid.uuid4().hex[:10]}",
        asset_id=asset_id,
        event_type=event_type,
        event_time=event_time,
        known_at=known_at,
        ingested_at=known_at + timedelta(seconds=ingested_delay_seconds),
        source=source,
        evidence_class=evidence_class,
        payload=payload,
        confidence=1.0,
        provenance={"source_ref": f"{source}:{asset_id}:{event_type}:{minutes}"},
    )


def reset_demo(db: Session) -> dict[str, Any]:
    from app.db import init_db

    init_db()
    for model in [
        PrecedentComparison,
        ReviewFlag,
        LateEvidence,
        CostImpact,
        Outcome,
        ExecutedAction,
        ObservabilityState,
        DecisionOption,
        EvidenceSnapshot,
        Decision,
        DecisionPackage,
        Investigation,
        Evidence,
        Asset,
    ]:
        db.execute(delete(model))
    assets = [
        Asset(
            id=f"R{i:02d}",
            name=f"R{i:02d}",
            asset_type="Manipulation Robot",
            family="MR-Lab",
            site="Test Cell A" if i <= 3 else "Test Cell B",
            environment="real-world test lab",
        )
        for i in range(1, 7)
    ]
    db.add_all(assets)
    affected_ids = {"R03", "R05"}
    for a in assets:
        asset_num = int(a.id[1:])
        lighting = "low-light-bin" if asset_num in {2, 3, 4} else "standard-light"
        end_effector = "gripper-G2" if asset_num in {3, 4, 5, 6} else "gripper-G1"
        db.add(
            evidence(
                a.id,
                "state_snapshot",
                2,
                0,
                "edge_agent",
                "OBSERVED",
                {
                    "application_version": "policy-v0.8",
                    "firmware_version": "gripper-fw-7.2",
                    "configuration": "camera-cal-B",
                    "network_profile": lighting,
                    "connection_type": end_effector,
                    "process_health": "healthy",
                    "health": "healthy",
                },
                f"{a.id}-healthy-run",
            )
        )
        db.add(
            evidence(
                a.id,
                "deployment",
                4,
                1,
                "policy_registry",
                "OBSERVED",
                {"application_version": "policy-v0.9", "previous_application_version": "policy-v0.8", "deployment_id": "policy-manip-09", "rollout_size": 6},
                f"{a.id}-policy-09",
            )
        )
    for aid in ["R03", "R05", "R06"]:
        db.add(
            evidence(
                aid,
                "configuration_change",
                6,
                1,
                "calibration_registry",
                "OBSERVED",
                {"configuration": "camera-cal-C", "previous_configuration": "camera-cal-B", "firmware_version": "gripper-fw-7.3"},
                f"{aid}-cal-c-fw-73",
            )
        )
    for index, aid in enumerate(sorted(affected_ids)):
        db.add(
            evidence(
                aid,
                "telemetry_anomaly",
                11 + (index % 8),
                1,
                "run_telemetry",
                "OBSERVED",
                {"signal": "grip_pose_drift", "process_health": "degraded", "severity": "review", "offset_mm": 11 + (index * 4)},
                f"{aid}-grip-drift",
            )
        )
    db.add(
        evidence(
            "R03",
            "human_discovery",
            26,
            0,
            "engineer_note",
            "HUMAN_ASSERTED",
            {"observation": "Engineer observes grip pose drifting after policy v0.9 in repeated real-world runs", "reported_by": "manipulation_engineer"},
            "R03-engineer-note-1426",
        )
    )
    inv = Investigation(id="inv-120-robots-bad-rollout", asset_id="R03", trigger_id="R03-grip-drift", title="Grip pose divergence after policy update")
    db.add(inv)
    db.commit()
    seed_decision_primitives(db, inv.id)
    return {"assets": len(assets), "affected": len(affected_ids), "investigation_id": inv.id}


def seed_decision_primitives(db: Session, investigation_id: str) -> None:
    if db.scalar(select(DecisionOption.id).where(DecisionOption.investigation_id == investigation_id).limit(1)):
        return
    options = [
        DecisionOption(
            id=f"opt-{uuid.uuid4().hex[:10]}",
            investigation_id=investigation_id,
            option_type="rollback",
            label="Roll back R03 and R05, monitor R06",
            expected_cost={"engineering_hours": 1.5, "field_visit": False, "rollout_delay_minutes": 45},
            expected_risk={"risk": "medium", "reason": "R06 remains exposed but has no known issue at decision time"},
            historical_support={"status": "none_yet", "summary": "No prior outcome in this workspace"},
        ),
        DecisionOption(
            id=f"opt-{uuid.uuid4().hex[:10]}",
            investigation_id=investigation_id,
            option_type="monitor",
            label="Continue test run under observation",
            expected_cost={"engineering_hours": 0.5, "field_visit": False, "rollout_delay_minutes": 0},
            expected_risk={"risk": "high", "reason": "Affected machines may continue degrading before cause is established"},
            historical_support={"status": "weak", "summary": "No recovery evidence yet"},
        ),
        DecisionOption(
            id=f"opt-{uuid.uuid4().hex[:10]}",
            investigation_id=investigation_id,
            option_type="dispatch",
            label="Send field technician",
            expected_cost={"engineering_hours": 0.5, "field_visit": True, "estimated_cost_usd": 1200},
            expected_risk={"risk": "low", "reason": "Fast physical inspection, but current evidence does not yet justify dispatch"},
            historical_support={"status": "not_supported", "summary": "No hardware fault confirmed"},
        ),
        DecisionOption(
            id=f"opt-{uuid.uuid4().hex[:10]}",
            investigation_id=investigation_id,
            option_type="pause_rollout",
            label="Pause rollout to exposed machines",
            expected_cost={"engineering_hours": 1.0, "field_visit": False, "rollout_delay_minutes": 60},
            expected_risk={"risk": "low", "reason": "Contains exposed group while preserving investigation time"},
            historical_support={"status": "reasonable", "summary": "Matches current evidence boundary"},
        ),
    ]
    db.add_all(options)
    db.add(
        ObservabilityState(
            id=f"obs-{uuid.uuid4().hex[:10]}",
            investigation_id=investigation_id,
            assessed_at=DEMO_DECISION_TIME,
            status="partial",
            connected_sources=["policy_registry", "calibration_registry", "run_telemetry", "engineer_note"],
            freshness={
                "policy_registry": "fresh",
                "calibration_registry": "fresh",
                "run_telemetry": "partial",
                "engineer_note": "fresh",
            },
            completeness={
                "R03": "complete",
                "R05": "complete",
                "R06": "no known issue at decision time",
            },
            delayed_sources=["edge_buffer"],
            missing_windows=["targeted low-light validation", "R06 post-run telemetry not known at 14:27"],
        )
    )
    db.commit()


def evidence_rows(db: Session, asset_id: Optional[str] = None, known_at: Optional[datetime] = None) -> list[Evidence]:
    stmt = select(Evidence).order_by(Evidence.event_time)
    if asset_id:
        stmt = stmt.where(Evidence.asset_id == asset_id)
    if known_at:
        stmt = stmt.where(Evidence.known_at <= known_at)
    return list(db.scalars(stmt))


def latest_state(db: Session, asset_id: str, at: datetime, known_at: Optional[datetime] = None) -> dict[str, Any]:
    state: dict[str, Any] = {}
    stmt = select(Evidence).where(Evidence.asset_id == asset_id, Evidence.event_time <= at).order_by(Evidence.event_time)
    if known_at:
        stmt = stmt.where(Evidence.known_at <= known_at)
    for ev in db.scalars(stmt):
        payload = ev.payload or {}
        if ev.event_type == "deployment":
            state["application_version"] = payload.get("application_version")
        if ev.event_type == "configuration_change":
            if payload.get("configuration") is not None:
                state["configuration"] = payload.get("configuration")
            if payload.get("network_profile") is not None:
                state["network_profile"] = payload.get("network_profile")
            if payload.get("firmware_version") is not None:
                state["firmware_version"] = payload.get("firmware_version")
        if ev.event_type in ["state_snapshot", "telemetry_anomaly"]:
            for key in STATE_KEYS:
                if payload.get(key) is not None:
                    state[key] = payload.get(key)
            if ev.event_type == "telemetry_anomaly":
                state["health"] = "degraded"
        if payload.get("health"):
            state["health"] = payload.get("health")
        if payload.get("signal"):
            state["last_signal"] = payload.get("signal")
    return state


def reconstruct(db: Session, investigation_id: str, knowledge_time: Optional[datetime] = None) -> dict[str, Any]:
    inv = db.get(Investigation, investigation_id)
    if not inv:
        raise ValueError("investigation not found")
    trigger = db.get(Evidence, inv.trigger_id)
    known_at = knowledge_time or BASE + timedelta(days=1)
    rows = evidence_rows(db, inv.asset_id, known_at)
    healthy = [e for e in rows if e.event_time <= trigger.event_time and (e.payload or {}).get("health") == "healthy"]
    last_healthy = healthy[-1] if healthy else None
    lookback_start = trigger.event_time - timedelta(hours=2)
    changes = [e for e in rows if lookback_start <= e.event_time <= trigger.event_time and e.event_type in ["deployment", "configuration_change"]]
    abnormalities = [e for e in rows if e.event_type == "telemetry_anomaly"]
    first_abnormal = abnormalities[0] if abnormalities else trigger
    discoveries = [e for e in rows if e.event_type == "human_discovery"]
    human_discovery = discoveries[0] if discoveries else None
    detection_latency = None
    if human_discovery and first_abnormal:
        delta = human_discovery.event_time - first_abnormal.event_time
        detection_latency = f"{delta.seconds // 3600}h{(delta.seconds % 3600) // 60:02d}m"
    current = latest_state(db, inv.asset_id, trigger.event_time, known_at)
    required = ["application_version", "configuration", "firmware_version", "process_health"]
    gaps = [f for f in required if not current.get(f)]
    if not human_discovery:
        gaps.append("human discovery context")
    for contextual_gap in ["targeted low-light validation runs for calibration C", "engineer workaround rationale"]:
        if contextual_gap not in gaps:
            gaps.append(contextual_gap)
    return {
        "investigation": {"id": inv.id, "asset_id": inv.asset_id, "title": inv.title, "status": inv.status},
        "trigger": serialize_evidence(trigger),
        "last_known_healthy": serialize_evidence(last_healthy) if last_healthy else None,
        "changes_since_healthy": [serialize_evidence(e) for e in changes],
        "first_abnormal_evidence": serialize_evidence(first_abnormal),
        "human_discovery": serialize_evidence(human_discovery) if human_discovery else None,
        "detection_latency": detection_latency,
        "current_state": current,
        "evidence_gaps": gaps,
        "timeline": [serialize_evidence(e) for e in rows],
        "known_at": iso(known_at),
    }


def compare(db: Session, investigation_id: str) -> dict[str, Any]:
    rec = reconstruct(db, investigation_id)
    trigger = db.get(Evidence, rec["trigger"]["id"])
    target_state = latest_state(db, trigger.asset_id, trigger.event_time)
    all_assets = list(db.scalars(select(Asset).order_by(Asset.id)))
    exposed, affected, unaffected = [], [], []
    for asset in all_assets:
        state = latest_state(db, asset.id, trigger.event_time)
        same_app = state.get("application_version") == target_state.get("application_version")
        if same_app:
            exposed.append(asset.id)
            anomalies = [e for e in evidence_rows(db, asset.id) if e.event_type == "telemetry_anomaly"]
            (affected if anomalies else unaffected).append(asset.id)
    table = []
    dimensions = [
        ("Policy v0.9", "application_version", "policy-v0.9"),
        ("Camera calibration C", "configuration", "camera-cal-C"),
        ("Gripper firmware 7.3", "firmware_version", "gripper-fw-7.3"),
        ("Low-light test cell", "network_profile", "low-light-bin"),
        ("End-effector G2", "connection_type", "gripper-G2"),
    ]
    for label, key, expected in dimensions:
        affected_count = sum(1 for aid in affected if latest_state(db, aid, trigger.event_time).get(key) == expected)
        unaffected_count = sum(1 for aid in unaffected if latest_state(db, aid, trigger.event_time).get(key) == expected)
        table.append({"context": label, "affected": f"{affected_count}/{len(affected)}", "unaffected": f"{unaffected_count}/{len(unaffected)}"})
    return {
        "question": "Where else is this happening?",
        "same_change": len(exposed),
        "same_signal": len(affected),
        "no_signal": len(unaffected),
        "affected_assets": affected,
        "unaffected_assets": unaffected,
        "table": table,
        "interpretation": [
            "Policy v0.9 ran on all six robots, so the model update alone does not explain the split.",
            "Camera calibration C and gripper firmware 7.3 co-occur across the affected runs.",
            "R06 shares the same combination without a known signal at decision time.",
            "Low-light bin is present in 3/6 runs, but did not differentiate affected from stable robots at decision time.",
            "This narrows the investigation; it does not establish cause.",
        ],
        "potentially_exposed": [
            {"asset_id": "R06", "reason": "Shares calibration C and gripper firmware 7.3 exposure without a matching signal yet."}
        ],
    }


def summarize_values(values: list[Any]) -> str:
    if not values:
        return "0/0"
    counts: dict[str, int] = {}
    for value in values:
        counts[str(value)] = counts.get(str(value), 0) + 1
    value, count = sorted(counts.items(), key=lambda item: item[1], reverse=True)[0]
    return f"{value} · {count}/{len(values)}"


def build_package(db: Session, investigation_id: str) -> DecisionPackage:
    seed_decision_primitives(db, investigation_id)
    rec = reconstruct(db, investigation_id)
    comp = compare(db, investigation_id)
    unknowns = rec["evidence_gaps"]
    options = list(db.scalars(select(DecisionOption).where(DecisionOption.investigation_id == investigation_id).order_by(DecisionOption.option_type)))
    observability = db.scalar(select(ObservabilityState).where(ObservabilityState.investigation_id == investigation_id).order_by(ObservabilityState.assessed_at.desc()).limit(1))
    substantiation_checks = [
        {"name": "affected population scoped", "status": "supported", "evidence": f"{len(comp['affected_assets'])} affected, {len(comp['unaffected_assets'])} stable comparison runs"},
        {"name": "exposed-but-stable systems identified", "status": "supported", "evidence": ", ".join(item["asset_id"] for item in comp["potentially_exposed"]) or "none"},
        {"name": "decision owner assigned", "status": "missing", "evidence": "no named human owner until decision is recorded"},
        {"name": "unknowns explicitly preserved", "status": "supported", "evidence": "; ".join(unknowns[:2])},
        {"name": "inference boundary checked", "status": "warning", "evidence": "calibration/firmware interaction is plausible, not established cause"},
        {"name": "outcome follow-up required", "status": "missing", "evidence": "no outcome record yet"},
    ]
    package = {
        "trigger": rec["trigger"],
        "last_known_healthy": rec["last_known_healthy"],
        "recent_changes": rec["changes_since_healthy"],
        "first_abnormal_evidence": rec["first_abnormal_evidence"],
        "machine_state": rec["current_state"],
        "peer_comparison": comp,
        "supporting_evidence": ["policy registry", "calibration record", "run telemetry", "engineer note"],
        "missing_evidence": rec["evidence_gaps"],
        "current_interpretation": comp["interpretation"],
        "human_decision": None,
        "options_considered": [serialize_option(option) for option in options],
        "observability_state": serialize_observability(observability),
        "approval_policy": {
            "name": "Physical system rollout review",
            "version": "v0.3",
            "required": ["decision owner", "evidence package", "action scope", "outcome follow-up"],
        },
        "action_scope": {
            "affected": comp["affected_assets"],
            "watch": [item["asset_id"] for item in comp["potentially_exposed"]],
            "stable_comparison": comp["unaffected_assets"],
        },
        "decision_substantiation": {
            "question": "Are we allowed and justified to take the operational action yet?",
            "status": "incomplete",
            "summary": "Decision package incomplete. Action cannot yet be fully substantiated until owner, unknowns and outcome follow-up are recorded.",
            "checks": substantiation_checks,
        },
        "outcome": None,
    }
    dp = DecisionPackage(id=f"pkg-{uuid.uuid4().hex[:10]}", investigation_id=investigation_id, version=1, package=package)
    db.add(dp)
    db.add(
        EvidenceSnapshot(
            id=f"snap-{uuid.uuid4().hex[:10]}",
            investigation_id=investigation_id,
            snapshot_time=DEMO_DECISION_TIME,
            evidence_ids=[item["id"] for item in rec["timeline"] if item["known_at"] <= iso(DEMO_DECISION_TIME)],
            known_state={
                "affected": comp["affected_assets"],
                "unaffected": comp["unaffected_assets"],
                "potentially_exposed": comp["potentially_exposed"],
            },
            unknowns=unknowns,
            observability_state=serialize_observability(observability),
        )
    )
    db.commit()
    db.refresh(dp)
    return dp


def attach_human_decision(db: Session, decision: Decision) -> None:
    for option in db.scalars(select(DecisionOption).where(DecisionOption.investigation_id == decision.investigation_id)):
        option.selected = option.option_type in {"rollback", "pause_rollout"}
        option.decision_id = decision.id
        db.add(option)
    observability = db.scalar(select(ObservabilityState).where(ObservabilityState.investigation_id == decision.investigation_id).order_by(ObservabilityState.assessed_at.desc()).limit(1))
    if observability:
        observability.decision_id = decision.id
        db.add(observability)
    db.add(
        ExecutedAction(
            id=f"act-{uuid.uuid4().hex[:10]}",
            investigation_id=decision.investigation_id,
            decision_id=decision.id,
            action_type="rollback_and_pause_rollout",
            owner=decision.owner,
            executed_at=DEMO_DECISION_TIME + timedelta(minutes=4),
            scope={"rollback": ["R03", "R05"], "watch": ["R06"], "held_rollout": ["calibration C", "gripper firmware 7.3"]},
            payload={"customer_support": "informed", "field_dispatch": "held"},
        )
    )
    if not decision.package_id:
        return
    dp = db.get(DecisionPackage, decision.package_id)
    if not dp or dp.sealed:
        return
    package = dict(dp.package or {})
    package["human_decision"] = {
        "decision": decision.decision,
        "owner": decision.owner,
        "identity": f"human:{decision.owner.lower().replace(' ', '-')}",
        "rationale": decision.rationale,
        "decided_at": iso(DEMO_DECISION_TIME),
        "signature_intent": "approved for action by named human owner",
    }
    package["outcome_follow_up"] = {
        "required": True,
        "due_after": "48h",
        "question": "Did the intervention work, and what changed afterward?",
    }
    if package.get("decision_substantiation"):
        checks = list(package["decision_substantiation"].get("checks", []))
        for check in checks:
            if check["name"] == "decision owner assigned":
                check["status"] = "supported"
                check["evidence"] = decision.owner
        package["decision_substantiation"] = {
            **package["decision_substantiation"],
            "status": "actionable_with_open_follow_up",
            "summary": "Human owner recorded. Action is supported for the scoped population, with unresolved evidence gaps and required outcome follow-up.",
            "checks": checks,
        }
    dp.package = package
    db.add(dp)


def serialize_option(option: DecisionOption) -> dict[str, Any]:
    return {
        "id": option.id,
        "option_type": option.option_type,
        "label": option.label,
        "expected_cost": option.expected_cost,
        "expected_risk": option.expected_risk,
        "historical_support": option.historical_support,
        "selected": option.selected,
    }


def serialize_observability(row: Optional[ObservabilityState]) -> Optional[dict[str, Any]]:
    if not row:
        return None
    return {
        "id": row.id,
        "assessed_at": iso(row.assessed_at),
        "status": row.status,
        "connected_sources": row.connected_sources,
        "freshness": row.freshness,
        "completeness": row.completeness,
        "delayed_sources": row.delayed_sources,
        "missing_windows": row.missing_windows,
    }


def decision_context(db: Session, investigation_id: str) -> dict[str, Any]:
    seed_decision_primitives(db, investigation_id)
    decisions = list(db.scalars(select(Decision).where(Decision.investigation_id == investigation_id).order_by(Decision.decided_at.desc())))
    options = list(db.scalars(select(DecisionOption).where(DecisionOption.investigation_id == investigation_id).order_by(DecisionOption.option_type)))
    observability = db.scalar(select(ObservabilityState).where(ObservabilityState.investigation_id == investigation_id).order_by(ObservabilityState.assessed_at.desc()).limit(1))
    actions = list(db.scalars(select(ExecutedAction).where(ExecutedAction.investigation_id == investigation_id).order_by(ExecutedAction.executed_at.desc())))
    snapshots = list(db.scalars(select(EvidenceSnapshot).where(EvidenceSnapshot.investigation_id == investigation_id).order_by(EvidenceSnapshot.snapshot_time.desc())))
    flags = list(db.scalars(select(ReviewFlag).where(ReviewFlag.investigation_id == investigation_id).order_by(ReviewFlag.created_at.desc())))
    return {
        "investigation_id": investigation_id,
        "decision_time": iso(DEMO_DECISION_TIME),
        "decisions": [
            {
                "id": item.id,
                "decision": item.decision,
                "owner": item.owner,
                "rationale": item.rationale,
                "decided_at": iso(item.decided_at),
                "package_id": item.package_id,
            }
            for item in decisions
        ],
        "options_considered": [serialize_option(option) for option in options],
        "observability_state": serialize_observability(observability),
        "executed_actions": [
            {
                "id": item.id,
                "action_type": item.action_type,
                "owner": item.owner,
                "executed_at": iso(item.executed_at),
                "scope": item.scope,
                "payload": item.payload,
            }
            for item in actions
        ],
        "evidence_snapshots": [
            {
                "id": item.id,
                "snapshot_time": iso(item.snapshot_time),
                "evidence_ids": item.evidence_ids,
                "known_state": item.known_state,
                "unknowns": item.unknowns,
                "observability_state": item.observability_state,
                "sealed_digest": item.sealed_digest,
            }
            for item in snapshots
        ],
        "review_flags": [
            {"id": item.id, "flag_type": item.flag_type, "severity": item.severity, "reason": item.reason, "status": item.status}
            for item in flags
        ],
    }


def seal_body(dp: DecisionPackage) -> dict[str, Any]:
    package = dict(dp.package or {})
    package.pop("_seal", None)
    return {
        "version": dp.version,
        "trusted_timestamp": iso(DEMO_DECISION_TIME),
        "timestamp_authority": TIMESTAMP_AUTHORITY,
        "package": package,
    }


def seal_package(db: Session, package_id: str) -> DecisionPackage:
    dp = db.get(DecisionPackage, package_id)
    if not dp:
        raise ValueError("package not found")
    if dp.sealed:
        return dp
    sealed_at = DEMO_DECISION_TIME
    body = seal_body(dp)
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(canonical).hexdigest()
    private_key = Ed25519PrivateKey.generate()
    signature = private_key.sign(canonical)
    public_key = private_key.public_key().public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    dp.sealed = True
    dp.sealed_at = sealed_at
    dp.digest = digest
    dp.signature = base64.b64encode(signature).decode()
    dp.public_key = base64.b64encode(public_key).decode()
    package = dict(dp.package or {})
    package["_seal"] = {
        "canonicalization": "json.sort_keys.compact",
        "digest_algorithm": "SHA-256",
        "signature_algorithm": "Ed25519",
        "trusted_timestamp": iso(DEMO_DECISION_TIME),
        "timestamp_authority": TIMESTAMP_AUTHORITY,
        "standalone_verification": "Use the public key, canonical body and signature to verify this package outside Veyra.",
    }
    dp.package = package
    db.commit()
    db.refresh(dp)
    return dp


def verify_package(db: Session, package_id: str) -> dict[str, Any]:
    dp = db.get(DecisionPackage, package_id)
    if not dp:
        raise ValueError("package not found")
    if not dp.sealed or not dp.digest or not dp.signature or not dp.public_key:
        return {"package_id": package_id, "sealed": bool(dp.sealed), "valid": False, "reason": "package is not sealed"}
    canonical = json.dumps(seal_body(dp), sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(canonical).hexdigest()
    digest_matches = digest == dp.digest
    signature_valid = False
    try:
        public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(dp.public_key.encode()))
        public_key.verify(base64.b64decode(dp.signature.encode()), canonical)
        signature_valid = True
    except (InvalidSignature, ValueError):
        signature_valid = False
    return {
        "package_id": package_id,
        "sealed": True,
        "valid": digest_matches and signature_valid,
        "digest_matches": digest_matches,
        "signature_valid": signature_valid,
        "digest": dp.digest,
        "public_key": dp.public_key,
        "trusted_timestamp": iso(DEMO_DECISION_TIME),
        "timestamp_authority": TIMESTAMP_AUTHORITY,
        "canonicalization": "json.sort_keys.compact",
        "verification_mode": "standalone",
    }


def serialize_evidence(ev: Optional[Evidence]) -> Optional[dict[str, Any]]:
    if not ev:
        return None
    return {
        "id": ev.id,
        "asset_id": ev.asset_id,
        "event_type": ev.event_type,
        "event_time": iso(ev.event_time),
        "known_at": iso(ev.known_at),
        "ingested_at": iso(ev.ingested_at),
        "source": ev.source,
        "evidence_class": ev.evidence_class,
        "payload": ev.payload,
        "confidence": ev.confidence,
        "provenance": ev.provenance,
    }


def inject_late_evidence(db: Session) -> dict[str, Any]:
    ev = evidence(
        "R06",
        "telemetry_anomaly",
        9,
        22,
        "delayed_edge_buffer",
        "OBSERVED",
        {
            "signal": "grip_pose_drift",
            "process_health": "degraded",
            "severity": "review",
            "offset_mm": 9,
            "source_detail": "post-run telemetry re-analysis",
            "note": "R06 post-run telemetry was re-analyzed at 14:31; grip pose drift was present at 14:09:11.",
        },
        "R06-delayed-grip-drift",
        ingested_delay_seconds=4,
    )
    if not db.get(Evidence, ev.id):
        db.add(ev)
        db.commit()
    investigation_id = "inv-120-robots-bad-rollout"
    latest_decision = db.scalar(select(Decision).where(Decision.investigation_id == investigation_id).order_by(Decision.decided_at.desc()).limit(1))
    if not db.get(LateEvidence, "late-R06-delayed-grip-drift"):
        db.add(
            LateEvidence(
                id="late-R06-delayed-grip-drift",
                investigation_id=investigation_id,
                evidence_id=ev.id,
                decision_id=latest_decision.id if latest_decision else None,
                materiality="decision_relevant",
                review_status="open",
                created_at=ev.ingested_at,
            )
        )
        db.add(
            ReviewFlag(
                id=f"flag-{uuid.uuid4().hex[:10]}",
                investigation_id=investigation_id,
                decision_id=latest_decision.id if latest_decision else None,
                flag_type="late_decision_relevant_evidence",
                severity="review",
                reason="R06 evidence had event_time before the sealed decision but known_at after the decision.",
                status="open",
                created_at=ev.ingested_at,
            )
        )
        db.commit()
    return {"late_evidence": serialize_evidence(ev), "comparison": compare(db, investigation_id), "review_required": True}


def record_cost_impact(db: Session, outcome: Outcome) -> CostImpact:
    payload = outcome.payload or {}
    impact = CostImpact(
        id=f"cost-{uuid.uuid4().hex[:10]}",
        investigation_id=outcome.investigation_id,
        outcome_id=outcome.id,
        recovery_time_minutes=payload.get("recovery_minutes", 41),
        downtime_minutes=payload.get("downtime_minutes", 41),
        field_visit=bool(payload.get("field_visit", False)),
        engineering_hours=float(payload.get("engineering_hours", 1.5)),
        rollout_delay_minutes=payload.get("rollout_delay_minutes", 60),
        customer_escalation=bool(payload.get("customer_escalation", False)),
        estimated_cost={
            "field_visit_avoided_usd": payload.get("field_visit_avoided_usd", 1200),
            "engineering_hours_saved": payload.get("engineering_hours_saved", 3),
            "rollout_risk_reduced": True,
        },
    )
    db.add(impact)
    return impact


def precedent_comparison(db: Session, investigation_id: str) -> dict[str, Any]:
    comp = compare(db, investigation_id)
    outcomes = list(db.scalars(select(Outcome).order_by(Outcome.recorded_at.desc())))
    cost_rows = list(db.scalars(select(CostImpact).where(CostImpact.investigation_id == investigation_id)))
    response_history = {
        "rollback": {
            "cases": len([o for o in outcomes if "rollback" in (o.outcome or "").lower() or "rollback" in json.dumps(o.payload or {}).lower()]),
            "observed_outcome": "affected machines recovered in the demo case" if outcomes else "no outcome recorded",
        },
        "monitor": {"cases": 0, "observed_outcome": "not yet observed"},
        "dispatch": {"cases": 0, "observed_outcome": "not supported by current evidence"},
        "pause_rollout": {"cases": len(outcomes), "observed_outcome": "rollout contained while review continued" if outcomes else "no outcome recorded"},
    }
    result = {
        "investigation_id": investigation_id,
        "query": {
            "question": "In similar conditions, what responses were tried and what happened?",
            "state": {
                "policy": "policy-v0.9",
                "configuration": "camera-cal-C",
                "firmware": "gripper-fw-7.3",
                "signal": "grip_pose_drift",
            },
        },
        "similar_state": {
            "affected": comp["affected_assets"],
            "unaffected": comp["unaffected_assets"],
            "potentially_exposed": comp["potentially_exposed"],
        },
        "response_history": response_history,
        "outcome_comparison": [
            {"action": "rollback", "status": response_history["rollback"]["observed_outcome"]},
            {"action": "monitor", "status": response_history["monitor"]["observed_outcome"]},
            {"action": "dispatch", "status": response_history["dispatch"]["observed_outcome"]},
        ],
        "cost_comparison": [
            {
                "field_visit": row.field_visit,
                "recovery_time_minutes": row.recovery_time_minutes,
                "downtime_minutes": row.downtime_minutes,
                "estimated_cost": row.estimated_cost,
            }
            for row in cost_rows
        ],
        "evidence_strength": "precedent, not causal proof",
    }
    db.add(
        PrecedentComparison(
            id=f"prec-{uuid.uuid4().hex[:10]}",
            investigation_id=investigation_id,
            query=result["query"],
            similar_state=result["similar_state"],
            response_history=result["response_history"],
            outcome_comparison={"rows": result["outcome_comparison"]},
            cost_comparison={"rows": result["cost_comparison"]},
        )
    )
    db.commit()
    return result


def similar_memory(db: Session, investigation_id: str) -> dict[str, Any]:
    outcomes = list(db.scalars(select(Outcome).order_by(Outcome.recorded_at.desc())))
    precedent = precedent_comparison(db, investigation_id) if outcomes else None
    return {
        "query_investigation_id": investigation_id,
        "similar_cases": [
            {
                "investigation_id": o.investigation_id,
                "match_reason": "same policy-update family, grip pose drift signal, calibration-specific split",
                "outcome": o.outcome,
                "recorded_at": iso(o.recorded_at),
            }
            for o in outcomes[:5]
        ],
        "precedent_comparison": precedent,
    }
