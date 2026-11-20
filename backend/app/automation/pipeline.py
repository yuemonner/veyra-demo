"""Automatic field-case pipeline: event -> case -> evidence from connectors -> investigation -> agent.

Every step goes through the validated store: connector files are committed as ordinary imports,
the trigger and each acquisition are machine-generated ledger records, an operator takeover becomes
an intervention report with the reason the source system gave, and the investigation run is the same
deterministic engine. Nothing here touches a machine.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from app.connectors import base as connectors
from app.ingest.profile import ProfileError, parse_profile
from app.store.sqlite_store import CaseStore, StoreError

EVENT_TYPES = ("alert", "ticket", "takeover", "emergency_stop", "pause", "remote_disconnect")
INTERVENTION_TYPES = {"takeover": "takeover", "emergency_stop": "emergency_stop", "pause": "pause", "remote_disconnect": "remote_disconnect"}
DEFAULTS = {"symptom_before_s": 90, "symptom_after_s": 30, "acquire_before_s": 600, "acquire_after_s": 300}
SIGNATURE_TOLERANCE_S = 300


class EventError(ValueError):
    pass


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: Any) -> datetime:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise EventError(f"occurred_at '{value}' is not ISO 8601") from None
    if moment.tzinfo is None:
        raise EventError("occurred_at needs a timezone")
    return moment


# ---------- webhook ----------
def sign(secret: str, timestamp: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def verify_signature(secret: str, timestamp: Optional[str], signature: Optional[str], body: bytes, now: Optional[float] = None) -> None:
    if not timestamp or not signature:
        raise EventError("Missing X-Veyra-Timestamp or X-Veyra-Signature")
    try:
        age = abs((now or time.time()) - float(timestamp))
    except ValueError:
        raise EventError("X-Veyra-Timestamp must be Unix seconds") from None
    if age > SIGNATURE_TOLERANCE_S:
        raise EventError("Webhook timestamp is too old or in the future")
    if not hmac.compare_digest(sign(secret, timestamp, body), signature):
        raise EventError("Webhook signature does not match")


def _dig(data: Any, dotted: Optional[str]) -> Any:
    if not dotted:
        return None
    for part in dotted.split("."):
        if not isinstance(data, dict):
            return None
        data = data.get(part)
    return data


def normalize_event(source: str, payload: Dict[str, Any], source_config: Dict[str, Any]) -> Dict[str, Any]:
    """Map a source system's payload onto Veyra's event fields using the configured paths."""
    fields = source_config.get("fields") or {}
    get = lambda key: _dig(payload, fields.get(key, key))  # noqa: E731
    raw_type = get("type")
    event_type = (source_config.get("type_map") or {}).get(str(raw_type), raw_type)
    if event_type not in EVENT_TYPES:
        raise EventError(f"Event type '{raw_type}' is not mapped to one of {', '.join(EVENT_TYPES)}")
    event = {"source": source, "event_id": str(get("event_id") or ""), "type": event_type, "machine_id": str(get("machine_id") or ""),
             "occurred_at": _iso(_parse_time(get("occurred_at"))), "title": str(get("title") or event_type)[:200],
             "description": str(get("description") or "")[:2000], "operator": get("operator"), "stated_reason": get("stated_reason"),
             "synthetic": bool(source_config.get("synthetic", False))}
    if not event["event_id"] or not event["machine_id"]:
        raise EventError("Event needs an id and a machine")
    machine_aliases = source_config.get("machine_aliases") or {}
    event["machine_id"] = machine_aliases.get(event["machine_id"], event["machine_id"])
    return event


# ---------- acquisition ----------
def acquire(store: CaseStore, case_id: str, connector_list: List[connectors.Connector], *, actor: str,
            window: Optional[connectors.Window] = None, trigger: Optional[str] = None) -> List[Dict[str, Any]]:
    case = store.case(case_id)
    if window is None:
        if case["symptom_start"] is None:
            raise StoreError("Give a time window: this case has no symptom window")
        start = datetime.fromtimestamp(case["symptom_start"], timezone.utc) - timedelta(seconds=DEFAULTS["acquire_before_s"])
        end = datetime.fromtimestamp(case["symptom_end"], timezone.utc) + timedelta(seconds=DEFAULTS["acquire_after_s"])
        window = connectors.Window(start, end)
    records = []
    for connector in connector_list:
        if not connector.serves(case["machine_id"]):
            continue
        items: List[Dict[str, Any]] = []
        error = None
        if connector.synthetic != bool(case["synthetic"]):
            error = "Connector and case disagree on synthetic data; nothing fetched"
        else:
            try:
                batches = connector.fetch(case["machine_id"], window)
            except connectors.ConnectorError as exc:
                error, batches = str(exc), []
            for batch in batches:
                item = {"filename": batch.filename, "source": batch.source, "sha256": hashlib.sha256(batch.content).hexdigest(),
                        "bytes": len(batch.content), "notes": batch.notes}
                try:
                    profile = parse_profile(batch.profile)
                    record, result = store.commit_import(case_id, batch.content, batch.filename, profile, actor=actor,
                                                         review_reason=f"Automatic import by connector {connector.id}; rejected rows are listed in the import report")
                    item.update(status="imported", import_id=record["id"], rows=len(result.rows), rejected_rows=result.rejected_row_count)
                except ProfileError as exc:
                    item.update(status="profile_error", detail=str(exc))
                except StoreError as exc:
                    item.update(status="duplicate" if "duplicate" in str(exc).lower() else "not_imported", detail=str(exc)[:500])
                items.append(item)
        payload = {"connector_id": connector.id, "connector_kind": connector.kind, "window": list(window.iso()), "trigger": trigger,
                   "items": items, "error": error, "imported": sum(1 for i in items if i["status"] == "imported"),
                   "boundary": "Read-only fetch. Files were committed through the same validated import path as a manual upload."}
        records.append(store.append_trusted(case_id, "acquisition", payload, actor=actor, source=f"veyra://connector/{connector.id}"))
    return records


# ---------- events ----------
def find_case_for_event(store: CaseStore, event: Dict[str, Any]) -> Optional[str]:
    for case in store.cases():
        if case["machine_id"] != event["machine_id"]:
            continue
        for entry in store.ledger(case["id"]):
            if entry["kind"] == "trigger_event" and entry["payload"]["source"] == event["source"] and entry["payload"]["event_id"] == event["event_id"]:
                return case["id"]
    return None


def handle_event(store: CaseStore, event: Dict[str, Any], *, config: Dict[str, Any], actor: Optional[str] = None,
                 agent: Optional[Callable[[str], Dict[str, Any]]] = None, raw_digest: Optional[str] = None) -> Dict[str, Any]:
    """Open (or find) the case for an event, pull evidence, investigate, and optionally run the agent."""
    actor = actor or f"connector:{event['source']}"
    existing = find_case_for_event(store, event)
    if existing:
        return {"case_id": existing, "status": "already_handled", "event_id": event["event_id"]}
    settings = {**DEFAULTS, **(config.get("automation") or {})}
    at = _parse_time(event["occurred_at"])
    case = store.create_case(machine_id=event["machine_id"], title=f"{event['machine_id']}: {event['title']}"[:200], actor=actor,
                             synthetic=event["synthetic"],
                             symptom_start=_iso(at - timedelta(seconds=settings["symptom_before_s"])),
                             symptom_end=_iso(at + timedelta(seconds=settings["symptom_after_s"])),
                             symptom_source=f"{event['source']} {event['type']} event {event['event_id']}: {event['title']}")
    cid = case["id"]
    store.append_trusted(cid, "trigger_event", {**{k: event[k] for k in ("source", "event_id", "type", "machine_id", "occurred_at", "title", "description")},
                                                "raw_sha256": raw_digest, "boundary": "As reported by the source system."},
                         actor=actor, source=f"veyra://webhook/{event['source']}")
    if event["type"] in INTERVENTION_TYPES:
        store.append(cid, "intervention_report", {"occurred_at": event["occurred_at"], "intervention_type": INTERVENTION_TYPES[event["type"]],
                                                  "operator": str(event.get("operator") or "Unknown operator"),
                                                  "stated_reason": str(event.get("stated_reason") or event.get("description") or event["title"]),
                                                  "reported_via": f"{event['source']} event {event['event_id']}"}, actor=actor, source=f"veyra://webhook/{event['source']}")
    window = connectors.Window(at - timedelta(seconds=settings["acquire_before_s"]), at + timedelta(seconds=settings["acquire_after_s"]))
    acquisitions = acquire(store, cid, connectors.build(config), actor=actor, window=window, trigger=f"{event['source']}:{event['event_id']}")
    imported = sum(a["payload"]["imported"] for a in acquisitions)
    out = {"case_id": cid, "status": "opened", "event_id": event["event_id"], "acquisitions": [a["id"] for a in acquisitions], "imported_files": imported}
    if imported:
        run = store.investigate(cid, actor=actor)
        out["run_id"] = run["id"]
        if agent is not None:
            try:
                report = agent(cid)
                out["agent_report"] = report.get("id")
            except Exception as exc:  # the case stays usable without the agent
                out["agent_error"] = str(exc)[:500]
    else:
        out["status"] = "opened_without_evidence"
    return out
