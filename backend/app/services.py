import base64
import hashlib
import json
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Asset, Decision, DecisionPackage, Evidence, Investigation, Outcome

BASE = datetime(2026, 9, 3, 14, 0, 0)
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
    for model in [Outcome, Decision, DecisionPackage, Investigation, Evidence, Asset]:
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
    return {"assets": len(assets), "affected": len(affected_ids), "investigation_id": inv.id}


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
            "Low-light conditions and end-effector family are plausible but imperfect separators.",
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
    rec = reconstruct(db, investigation_id)
    comp = compare(db, investigation_id)
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
        "outcome": None,
    }
    dp = DecisionPackage(id=f"pkg-{uuid.uuid4().hex[:10]}", investigation_id=investigation_id, version=1, package=package)
    db.add(dp)
    db.commit()
    db.refresh(dp)
    return dp


def seal_package(db: Session, package_id: str) -> DecisionPackage:
    dp = db.get(DecisionPackage, package_id)
    if not dp:
        raise ValueError("package not found")
    if dp.sealed:
        return dp
    sealed_at = datetime.utcnow()
    body = {"version": dp.version, "sealed_at": iso(sealed_at), "package": dp.package}
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
    db.commit()
    db.refresh(dp)
    return dp


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
        {"signal": "grip_pose_drift", "process_health": "degraded", "severity": "review", "offset_mm": 9, "note": "event_time before engineer note; known later"},
        "R06-delayed-grip-drift",
        ingested_delay_seconds=4,
    )
    if not db.get(Evidence, ev.id):
        db.add(ev)
        db.commit()
    return {"late_evidence": serialize_evidence(ev), "comparison": compare(db, "inv-120-robots-bad-rollout")}


def similar_memory(db: Session, investigation_id: str) -> dict[str, Any]:
    outcomes = list(db.scalars(select(Outcome).order_by(Outcome.recorded_at.desc())))
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
    }
