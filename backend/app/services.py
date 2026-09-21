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
    DecisionRecord,
    Evidence,
    EvidenceSnapshot,
    ExecutedAction,
    Investigation,
    LateEvidence,
    ObservabilityState,
    Outcome,
    OutcomeAttribution,
    PrecedentComparison,
    ReviewFlag,
)

BASE = datetime(2026, 9, 3, 14, 0, 0)
DEMO_DECISION_TIME = BASE + timedelta(minutes=27)
DEMO_SEAL_TIME = BASE + timedelta(minutes=31)
TIMESTAMP_AUTHORITY = "Veyra demo timestamp authority"
STATE_KEYS = [
    "application_version",
    "firmware_version",
    "configuration",
    "map_version",
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
        OutcomeAttribution,
        Outcome,
        DecisionRecord,
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
            id=f"EX{i:02d}",
            name=f"EX{i:02d}",
            asset_type="Remote-operated excavator",
            family="Heavy equipment robotics",
            site="Quarry North" if i <= 6 else "Port Yard East",
            environment="deployed industrial site",
        )
        for i in range(1, 13)
    ]
    db.add_all(assets)
    affected_ids = {"EX03", "EX05", "EX08"}
    for a in assets:
        asset_num = int(a.id[2:])
        localization_profile = "localization-L4" if asset_num in {2, 3, 5, 8, 11} else "localization-L3"
        lidar_firmware = "lidar-fw-5.3" if asset_num in {2, 3, 4, 5, 8, 9, 11} else "lidar-fw-5.2"
        map_version = "map-M19" if asset_num in {2, 3, 4, 5, 6, 8, 9, 11} else "map-M18"
        zone_profile = "loading-zone-B" if asset_num in {3, 5, 8, 11} else "standard-route"
        connection_type = "private-5g" if asset_num in {1, 3, 5, 8, 10, 11} else "site-wifi"
        db.add(
            evidence(
                a.id,
                "state_snapshot",
                2,
                0,
                "robot_runtime",
                "OBSERVED",
                {
                    "application_version": "autonomy-2.6",
                    "firmware_version": "lidar-fw-5.2",
                    "configuration": "localization-L3",
                    "map_version": "map-M18",
                    "network_profile": zone_profile,
                    "connection_type": connection_type,
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
                "release_manifest",
                "OBSERVED",
                {"application_version": "autonomy-2.7", "previous_application_version": "autonomy-2.6", "deployment_id": "autonomy-release-27", "rollout_size": 12},
                f"{a.id}-autonomy-27",
            )
        )
    for aid in ["EX02", "EX03", "EX04", "EX05", "EX06", "EX08", "EX09", "EX11"]:
        asset_num = int(aid[2:])
        localization_profile = "localization-L4" if asset_num in {2, 3, 5, 8, 11} else "localization-L3"
        lidar_firmware = "lidar-fw-5.3" if asset_num in {2, 3, 4, 5, 8, 9, 11} else "lidar-fw-5.2"
        map_version = "map-M19"
        zone_profile = "loading-zone-B" if asset_num in {3, 5, 8, 11} else "standard-route"
        db.add(
            evidence(
                aid,
                "configuration_change",
                6,
                1,
                "robot_config_registry",
                "OBSERVED",
                {"configuration": localization_profile, "previous_configuration": "localization-L3", "firmware_version": lidar_firmware, "map_version": map_version, "network_profile": zone_profile},
                f"{aid}-localization-map-update",
            )
        )
    for index, aid in enumerate(sorted(affected_ids)):
        db.add(
            evidence(
                aid,
                "telemetry_anomaly",
                11 + (index * 7),
                1,
                "mission_runtime",
                "OBSERVED",
                {"signal": "safe_stop_near_loading_zone", "process_health": "safe_stop", "severity": "review", "safe_stop_count": 1 + index, "zone": "loading-zone-B"},
                f"{aid}-safe-stop",
            )
        )
    db.add(
        evidence(
            "EX03",
            "human_discovery",
            31,
            0,
            "operator_review",
            "HUMAN_ASSERTED",
            {"observation": "Operator review opened after three machines entered safe-stop near loading zone B after autonomy release 2.7", "reported_by": "fleet_operator"},
            "EX03-operator-review-1431",
        )
    )
    inv = Investigation(id="inv-120-robots-bad-rollout", asset_id="EX03", trigger_id="EX03-safe-stop", title="Safe-stop after autonomy release")
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
            option_type="remote_fix",
            label="Remote recovery on EX03, EX05 and EX08, hold technician dispatch",
            expected_cost={"engineering_hours": 1.2, "field_visit": False, "rollout_delay_minutes": 43},
            expected_risk={"risk": "medium", "reason": "EX11 remains exposed but has no known issue at decision time"},
            historical_support={"status": "none_yet", "summary": "No prior outcome in this workspace"},
        ),
        DecisionOption(
            id=f"opt-{uuid.uuid4().hex[:10]}",
            investigation_id=investigation_id,
            option_type="monitor",
            label="Continue monitoring without intervention",
            expected_cost={"engineering_hours": 0.5, "field_visit": False, "rollout_delay_minutes": 0},
            expected_risk={"risk": "high", "reason": "Affected machines may keep entering safe-stop during production missions"},
            historical_support={"status": "weak", "summary": "No recovery evidence yet"},
        ),
        DecisionOption(
            id=f"opt-{uuid.uuid4().hex[:10]}",
            investigation_id=investigation_id,
            option_type="dispatch",
            label="Dispatch field engineer",
            expected_cost={"engineering_hours": 0.5, "field_visit": True, "estimated_cost_usd": 1200},
            expected_risk={"risk": "low", "reason": "Fast onsite inspection, but current evidence does not yet justify the visit"},
            historical_support={"status": "not_supported", "summary": "No hardware fault confirmed"},
        ),
        DecisionOption(
            id=f"opt-{uuid.uuid4().hex[:10]}",
            investigation_id=investigation_id,
            option_type="pause_rollout",
            label="Pause autonomy 2.7 rollout for exposed machines",
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
            connected_sources=["release_manifest", "robot_config_registry", "mission_runtime", "operator_review"],
            freshness={
                "release_manifest": "fresh",
                "robot_config_registry": "fresh",
                "mission_runtime": "partial",
                "operator_review": "fresh",
            },
            completeness={
                "EX03": "complete",
                "EX05": "complete",
                "EX08": "complete",
                "EX11": "no known issue at decision time",
            },
            delayed_sources=["edge_buffer"],
            missing_windows=["operator intent at safe-stop", "exact planner fallback reason", "EX11 runtime buffer not known at 14:27"],
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
            if payload.get("map_version") is not None:
                state["map_version"] = payload.get("map_version")
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
    for contextual_gap in ["operator intent at safe-stop", "exact planner fallback reason", "local perception trace near loading zone B", "operator workaround rationale"]:
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
        ("Autonomy release 2.7", "application_version", "autonomy-2.7"),
        ("Localization profile L4", "configuration", "localization-L4"),
        ("LiDAR firmware 5.3", "firmware_version", "lidar-fw-5.3"),
        ("Map M19", "map_version", "map-M19"),
        ("Loading zone B", "network_profile", "loading-zone-B"),
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
            "Autonomy release 2.7 ran on all twelve machines, so the release alone does not explain the split.",
            "Localization profile L4 and loading zone B exposure co-occur across the affected machines.",
            "EX11 shares the same exposure without a known issue at decision time.",
            "LiDAR firmware 5.3 and map M19 also appear in healthy machines, so they are not sufficient on their own.",
            "This narrows the investigation; it does not establish cause.",
        ],
        "potentially_exposed": [
            {"asset_id": "EX11", "reason": "Shares localization profile L4, LiDAR firmware 5.3 and loading zone B exposure without a known issue at decision time."}
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


def demo_hypotheses() -> list[dict[str, Any]]:
    return [
        {
            "id": "h-localization-zone",
            "hypothesis": "Localization profile L4 with loading zone B exposure is contributing to safe-stop events.",
            "confidence": 0.58,
            "status": "supported_by_current_evidence",
            "supporting_evidence": ["EX03, EX05 and EX08 affected", "EX11 shares exposure", "localization profile and zone exposure co-occur across affected machines"],
            "counter_evidence": ["Autonomy 2.7 is deployed across all twelve machines", "LiDAR firmware 5.3 and map M19 also appear in healthy machines"],
        },
        {
            "id": "h-perception-map",
            "hypothesis": "Perception confidence or map context near loading zone B is contributing to planner fallback.",
            "confidence": 0.42,
            "status": "plausible_but_unresolved",
            "supporting_evidence": ["Affected machines share loading zone B exposure", "Exact planner fallback reason is missing"],
            "counter_evidence": ["No local perception trace has been retrieved yet"],
        },
        {
            "id": "h-release-wide",
            "hypothesis": "Autonomy release 2.7 caused a fleet-wide safe-stop issue.",
            "confidence": 0.18,
            "status": "weak_support",
            "supporting_evidence": ["Issue appeared after autonomy 2.7 rollout"],
            "counter_evidence": ["Nine updated machines have no known signal at decision time"],
        },
    ]


def build_package(db: Session, investigation_id: str) -> DecisionPackage:
    seed_decision_primitives(db, investigation_id)
    rec = reconstruct(db, investigation_id)
    comp = compare(db, investigation_id)
    unknowns = rec["evidence_gaps"]
    options = list(db.scalars(select(DecisionOption).where(DecisionOption.investigation_id == investigation_id).order_by(DecisionOption.option_type)))
    observability = db.scalar(select(ObservabilityState).where(ObservabilityState.investigation_id == investigation_id).order_by(ObservabilityState.assessed_at.desc()).limit(1))
    substantiation_checks = [
        {"name": "affected population scoped", "status": "supported", "evidence": f"{len(comp['affected_assets'])} affected, {len(comp['unaffected_assets'])} healthy comparison machines"},
        {"name": "exposed-but-stable systems identified", "status": "supported", "evidence": ", ".join(item["asset_id"] for item in comp["potentially_exposed"]) or "none"},
        {"name": "decision owner assigned", "status": "missing", "evidence": "no named human owner until decision is recorded"},
        {"name": "unknowns explicitly preserved", "status": "supported", "evidence": "; ".join(unknowns[:2])},
        {"name": "inference boundary checked", "status": "warning", "evidence": "localization/zone exposure is plausible, not established cause"},
        {"name": "outcome follow-up required", "status": "missing", "evidence": "no outcome record yet"},
    ]
    package = {
        "trigger": rec["trigger"],
        "last_known_healthy": rec["last_known_healthy"],
        "recent_changes": rec["changes_since_healthy"],
        "first_abnormal_evidence": rec["first_abnormal_evidence"],
        "machine_state": rec["current_state"],
        "peer_comparison": comp,
        "supporting_evidence": ["release manifest", "robot configuration record", "mission runtime telemetry", "operator review"],
        "missing_evidence": rec["evidence_gaps"],
        "what_was_unknown": [
            "Whether EX11 would later show the same safe-stop behavior",
            "Whether loading zone B map or perception context contributed",
            "Whether remote recovery would restore service",
            "Whether localization profile L4 was causal or only correlated",
        ],
        "hypotheses": demo_hypotheses(),
        "primary_hypothesis": demo_hypotheses()[0],
        "current_interpretation": comp["interpretation"],
        "human_decision": None,
        "planned_action": None,
        "actual_action": None,
        "options_considered": [serialize_option(option) for option in options],
        "observability_state": serialize_observability(observability),
        "approval_policy": {
            "name": "Remote equipment rollout review",
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
        "outcome_validation": {
            "status": "pending",
            "method": "Track return-to-service, recurrence and field dispatch outcome after action",
            "causal_attribution": "not_established",
        },
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
    options_for_record = []
    for option in db.scalars(select(DecisionOption).where(DecisionOption.investigation_id == decision.investigation_id)):
        option.selected = option.option_type in {"remote_fix", "pause_rollout"}
        option.decision_id = decision.id
        options_for_record.append(serialize_option(option))
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
            action_type="remote_fix_and_hold_rollout",
            owner=decision.owner,
            executed_at=DEMO_DECISION_TIME + timedelta(minutes=4),
            scope={"remote_recovery": ["EX03", "EX05", "EX08"], "watch": ["EX11"], "held_rollout": ["localization-L4", "loading-zone-B"]},
            payload={"operations_support": "informed", "field_dispatch": "held", "remote_fix": "remote recovery and autonomy service restart"},
        )
    )
    snapshot = db.scalar(select(EvidenceSnapshot).where(EvidenceSnapshot.investigation_id == decision.investigation_id).order_by(EvidenceSnapshot.snapshot_time.desc()).limit(1))
    chosen = [item for item in options_for_record if item["selected"]]
    record_body = {
        "decision_id": decision.id,
        "decision_time": iso(DEMO_DECISION_TIME),
        "owner": decision.owner,
        "chosen_option": chosen,
        "options_considered": options_for_record,
        "evidence_snapshot": {
            "id": snapshot.id if snapshot else None,
            "known_state": snapshot.known_state if snapshot else {},
            "unknowns": snapshot.unknowns if snapshot else [],
            "what_was_unknown": snapshot.unknowns if snapshot else [],
            "hypotheses": demo_hypotheses(),
            "primary_hypothesis": demo_hypotheses()[0],
        },
        "observability_state": serialize_observability(observability),
        "planned_action": {
            "action_type": "remote_fix_and_hold_rollout",
            "label": "Remote recovery on affected machines, hold field dispatch and monitor EX11",
            "scope": {"remote_recovery": ["EX03", "EX05", "EX08"], "watch": ["EX11"], "held_rollout": ["localization-L4", "loading-zone-B"]},
        },
    }
    record_digest = hashlib.sha256(json.dumps(record_body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if not db.scalar(select(DecisionRecord.id).where(DecisionRecord.decision_id == decision.id).limit(1)):
        db.add(
            DecisionRecord(
                id=f"drec-{uuid.uuid4().hex[:10]}",
                investigation_id=decision.investigation_id,
                decision_id=decision.id,
                package_id=decision.package_id,
                decision_time=DEMO_DECISION_TIME,
                owner=decision.owner,
                chosen_option=chosen[0] if chosen else {},
                options_considered=options_for_record,
                evidence_snapshot=record_body["evidence_snapshot"],
                observability_state=record_body["observability_state"] or {},
                immutable=True,
                digest=record_digest,
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
    package["planned_action"] = {
        "action_type": "remote_fix_and_hold_rollout",
        "label": "Remote recovery on affected machines, hold field dispatch and monitor EX11",
        "owner": decision.owner,
        "planned_at": iso(DEMO_DECISION_TIME),
        "scope": {"remote_recovery": ["EX03", "EX05", "EX08"], "watch": ["EX11"], "held_rollout": ["localization-L4", "loading-zone-B"]},
    }
    package["actual_action"] = {
        "status": "recorded",
        "action_type": "remote_fix_and_hold_rollout",
        "executed_at": iso(DEMO_DECISION_TIME + timedelta(minutes=4)),
        "scope": {"remote_recovery": ["EX03", "EX05", "EX08"], "watch": ["EX11"], "field_dispatch": "held"},
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
    records = list(db.scalars(select(DecisionRecord).where(DecisionRecord.investigation_id == investigation_id).order_by(DecisionRecord.decision_time.desc())))
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
        "decision_records": [
            {
                "id": item.id,
                "decision_id": item.decision_id,
                "package_id": item.package_id,
                "decision_time": iso(item.decision_time),
                "owner": item.owner,
                "chosen_option": item.chosen_option,
                "options_considered": item.options_considered,
                "evidence_snapshot": item.evidence_snapshot,
                "observability_state": item.observability_state,
                "hypotheses": item.evidence_snapshot.get("hypotheses", []) if isinstance(item.evidence_snapshot, dict) else [],
                "planned_action": {
                    "action_type": "remote_fix_and_hold_rollout",
                    "label": "Remote recovery on affected machines, hold field dispatch and monitor EX11",
                    "scope": {"remote_recovery": ["EX03", "EX05", "EX08"], "watch": ["EX11"], "held_rollout": ["localization-L4", "loading-zone-B"]},
                },
                "immutable": item.immutable,
                "digest": item.digest,
            }
            for item in records
        ],
    }


def seal_body(dp: DecisionPackage) -> dict[str, Any]:
    package = dict(dp.package or {})
    package.pop("_seal", None)
    return {
        "version": dp.version,
        "trusted_timestamp": iso(DEMO_SEAL_TIME),
        "timestamp_authority": TIMESTAMP_AUTHORITY,
        "package": package,
    }


def seal_package(db: Session, package_id: str) -> DecisionPackage:
    dp = db.get(DecisionPackage, package_id)
    if not dp:
        raise ValueError("package not found")
    if dp.sealed:
        return dp
    sealed_at = DEMO_SEAL_TIME
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
        "trusted_timestamp": iso(DEMO_SEAL_TIME),
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
        "trusted_timestamp": iso(DEMO_SEAL_TIME),
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
        "EX11",
        "telemetry_anomaly",
        9,
        22,
        "delayed_edge_buffer",
        "OBSERVED",
        {
            "signal": "safe_stop_near_loading_zone",
            "process_health": "safe_stop",
            "severity": "review",
            "safe_stop_count": 1,
            "zone": "loading-zone-B",
            "source_detail": "delayed edge buffer upload",
            "note": "EX11 runtime buffer was uploaded at 14:31; safe-stop behavior was present at 14:09:11.",
        },
        "EX11-delayed-safe-stop",
        ingested_delay_seconds=4,
    )
    if not db.get(Evidence, ev.id):
        db.add(ev)
        db.commit()
    investigation_id = "inv-120-robots-bad-rollout"
    latest_decision = db.scalar(select(Decision).where(Decision.investigation_id == investigation_id).order_by(Decision.decided_at.desc()).limit(1))
    latest_record = db.scalar(select(DecisionRecord).where(DecisionRecord.investigation_id == investigation_id).order_by(DecisionRecord.decision_time.desc()).limit(1))
    if not db.get(LateEvidence, "late-EX11-delayed-safe-stop"):
        db.add(
            LateEvidence(
                id="late-EX11-delayed-safe-stop",
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
                reason="EX11 evidence had event_time before the sealed decision but known_at after the decision.",
                status="open",
                created_at=ev.ingested_at,
            )
        )
        db.commit()
    return {
        "late_evidence": serialize_evidence(ev),
        "comparison": compare(db, investigation_id),
        "review_required": True,
        "decision_record_unchanged": latest_record.digest if latest_record else None,
    }


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
            "engineering_hours_saved": payload.get("engineering_hours_saved", 2),
            "rollout_risk_reduced": True,
        },
    )
    db.add(impact)
    attribution = OutcomeAttribution(
        id=f"attr-{uuid.uuid4().hex[:10]}",
        investigation_id=outcome.investigation_id,
        outcome_id=outcome.id,
        action_type="remote_fix_and_hold_rollout",
        attribution_level=payload.get("attribution_level", "observed"),
        rationale=payload.get("attribution_rationale", "Return-to-service was observed after remote recovery. Remote recovery is not treated as proven causal."),
        evidence={
            "recovery_observed_after_action": True,
            "causal_proof": False,
            "validation_status": payload.get("validation_status", "observed_recovery_not_causal_proof"),
            "validated_signals": payload.get("validated_signals", ["safe-stop cleared", "machines returned to service", "no field visit required"]),
            "hypothesis_result": payload.get("hypothesis_result", {
                "h-localization-zone": "partially_supported",
                "h-perception-map": "still_unresolved",
                "h-release-wide": "weakened",
            }),
            "recurrence_window": payload.get("recurrence_window", "EX03 and EX08 stable; EX05 entered safe-stop again six hours later; EX11 later showed same pattern"),
        },
    )
    db.add(attribution)
    return impact


def precedent_comparison(db: Session, investigation_id: str) -> dict[str, Any]:
    comp = compare(db, investigation_id)
    outcomes = list(db.scalars(select(Outcome).order_by(Outcome.recorded_at.desc())))
    cost_rows = list(db.scalars(select(CostImpact).where(CostImpact.investigation_id == investigation_id)))
    attributions = list(db.scalars(select(OutcomeAttribution).where(OutcomeAttribution.investigation_id == investigation_id)))
    response_history = {
        "remote_fix": {
            "cases": len([o for o in outcomes if "remote" in (o.outcome or "").lower() or "restart" in json.dumps(o.payload or {}).lower()]),
            "observed_outcome": "returned to service without a field visit" if outcomes else "outcome pending",
            "attribution": attributions[0].attribution_level if attributions else "not_observed",
        },
        "monitor": {"cases": 0, "observed_outcome": "not yet observed"},
        "dispatch": {"cases": 0, "observed_outcome": "not supported by current evidence"},
        "pause_rollout": {"cases": len(outcomes), "observed_outcome": "rollout contained while review continued" if outcomes else "outcome pending"},
    }
    result = {
        "investigation_id": investigation_id,
        "query": {
            "question": "In similar conditions, what responses were tried and what happened?",
            "state": {
                "application": "autonomy-2.7",
                "configuration": "localization-L4",
                "firmware": "lidar-fw-5.3",
                "map": "map-M19",
                "zone": "loading-zone-B",
                "signal": "safe_stop_near_loading_zone",
            },
        },
        "similar_state": {
            "affected": comp["affected_assets"],
            "unaffected": comp["unaffected_assets"],
            "potentially_exposed": comp["potentially_exposed"],
        },
        "response_history": response_history,
        "outcome_comparison": [
            {"action": "remote_fix", "cases": response_history["remote_fix"]["cases"], "outcome": response_history["remote_fix"]["observed_outcome"], "attribution": response_history["remote_fix"]["attribution"]},
            {"action": "monitor", "cases": response_history["monitor"]["cases"], "outcome": response_history["monitor"]["observed_outcome"], "attribution": "not_observed"},
            {"action": "dispatch", "cases": response_history["dispatch"]["cases"], "outcome": response_history["dispatch"]["observed_outcome"], "attribution": "not_supported"},
            {"action": "pause_rollout", "cases": response_history["pause_rollout"]["cases"], "outcome": response_history["pause_rollout"]["observed_outcome"], "attribution": "observed"},
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
        "outcome_validation": {
            "status": attributions[0].evidence.get("validation_status") if attributions else "pending",
            "validated_signals": attributions[0].evidence.get("validated_signals") if attributions else [],
            "hypothesis_result": attributions[0].evidence.get("hypothesis_result") if attributions else {},
            "causal_attribution": "not_established",
        },
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
                "match_reason": "same post-deployment safe-stop pattern, localization profile and loading-zone exposure",
                "outcome": o.outcome,
                "recorded_at": iso(o.recorded_at),
            }
            for o in outcomes[:5]
        ],
        "precedent_comparison": precedent,
    }
