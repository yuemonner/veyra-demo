"""Machine Context Model: typed, machine-scoped, bitemporal, source-linked.

Context is a set of append-only *assertions* about one machine. Each says what was true
(`effective_from`), when we learned it (`recorded_at`), who said so, from which source, and how
sure we are (`status`). Resolution answers "what did we know at time K about the machine at time T",
so old investigations can be replayed exactly and later corrections never rewrite history.

Unknown is explicit. Reported values are never treated as confirmed. Conflicts are surfaced.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

CONTEXT_SCHEMA_VERSION = "machine-context-v1"
COMPARABILITY_RULES_VERSION = "comparability-rules-v1"

REPORTED = "reported"
CONFIRMED = "engineer_confirmed"
UNKNOWN = "unknown"
RETRACTED = "retracted"
NOT_RECORDED = "not_recorded"
STATUSES = (REPORTED, CONFIRMED, UNKNOWN, RETRACTED)
_STATUS_RANK = {CONFIRMED: 3, REPORTED: 2, UNKNOWN: 1, RETRACTED: 1}


@dataclass(frozen=True)
class AttributeDef:
    key: str
    category: str
    description: str
    allowed: Optional[Tuple[str, ...]] = None
    dispatch_relevant: bool = False


_DEFS = (
    AttributeDef("machine_class", "identity", "Machine type; selects the investigation profile (e.g. remote_excavator, fixed_wing_uav, agricultural_robot)", dispatch_relevant=True),
    AttributeDef("oem", "identity", "Machine manufacturer", dispatch_relevant=True),
    AttributeDef("model", "identity", "Machine model", dispatch_relevant=True),
    AttributeDef("serial_number", "identity", "Machine serial number"),
    AttributeDef("kit_version", "retrofit", "Remote-control retrofit kit version", dispatch_relevant=True),
    AttributeDef("harness_revision", "retrofit", "Wiring harness revision", dispatch_relevant=True),
    AttributeDef("control_version", "software", "Remote-control application/controller software version"),
    AttributeDef("firmware_version", "software", "Vehicle-side controller firmware version"),
    AttributeDef("vehicle_interface", "interface", "How the kit commands the machine (e.g. CAN bridge, hydraulic pilot valves)", dispatch_relevant=True),
    AttributeDef("network_path", "network", "Network between console and machine (e.g. private LTE APN, Wi-Fi mesh)"),
    AttributeDef("modem_firmware", "network", "Vehicle modem/router firmware"),
    AttributeDef("measurement_endpoints", "measurement", "What each network probe actually measures, end to end"),
    AttributeDef("clock_sync_method", "measurement", "How console and vehicle clocks are synchronized (e.g. NTP, PTP, GPS, none)"),
    AttributeDef("clock_offset_bound_ms", "measurement", "Documented maximum offset between console and vehicle clocks, in ms"),
    AttributeDef("site", "site", "Deployment site", dispatch_relevant=True),
    AttributeDef("environment", "site", "Environment (e.g. open-pit mine, port, tunnel)"),
    AttributeDef("operating_conditions", "operations", "Operating conditions relevant to the fault (load, temperature, task)"),
    AttributeDef("last_service", "maintenance", "Most recent service and what was done"),
    AttributeDef("remote_access", "access", "Authorized remote access level", allowed=("unknown", "not_authorized", "read_only_authorized"), dispatch_relevant=True),
    AttributeDef("available_tools", "access", "Diagnostic tools available at site or remotely", dispatch_relevant=True),
)
SCHEMA: Dict[str, AttributeDef] = {d.key: d for d in _DEFS}

# Purpose-specific comparability. Generic brand matches are never sufficient.
COMPARABILITY_RULES: Dict[str, Dict[str, Any]] = {
    "remote_response_delay.baseline": {
        "must_match_confirmed": ("oem", "model", "kit_version", "control_version", "vehicle_interface", "network_path", "measurement_endpoints"),
        "why": "Network and command-path measurements depend on kit, software, interface and the exact probe path.",
    },
    "remote_response_delay.precedent": {
        "must_match_confirmed": ("oem", "model", "kit_version", "control_version", "vehicle_interface", "network_path", "measurement_endpoints", "environment", "operating_conditions"),
        "why": "A prior case informs this one only if machine, retrofit, software, path and operating conditions match.",
    },
    "input_response.baseline": {
        "must_match_confirmed": ("oem", "model", "kit_version", "control_version", "firmware_version", "vehicle_interface"),
        "why": "Input–response behaviour changes with controller software, firmware and the vehicle interface.",
    },
}


class ContextError(ValueError):
    pass


@dataclass(frozen=True)
class Assertion:
    id: str
    machine_id: str
    key: str
    value: Optional[str]
    status: str
    source: str
    actor: str
    effective_from: str  # ISO 8601 with offset
    recorded_at: str     # ISO 8601 with offset, set by the store
    evidence: Tuple[str, ...] = field(default_factory=tuple)
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["evidence"] = list(self.evidence)
        return out


def parse_time(value: str, label: str) -> datetime:
    if not isinstance(value, str):
        raise ContextError(f"{label} must be an ISO 8601 timestamp with timezone")
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ContextError(f"{label} is not a valid ISO 8601 timestamp") from None
    if moment.tzinfo is None:
        raise ContextError(f"{label} needs a timezone offset")
    return moment


def _epoch(value: str) -> float:
    return parse_time(value, "timestamp").timestamp()


def validate_assertion(raw: Dict[str, Any], *, assertion_id: str, recorded_at: datetime) -> Assertion:
    """Validate one assertion before it is appended. Raises ContextError with an actionable message."""
    if recorded_at.tzinfo is None:
        raise ContextError("recorded_at must be timezone-aware")
    allowed = {"machine_id", "key", "value", "status", "source", "actor", "effective_from", "evidence", "note"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ContextError(f"Unknown assertion fields: {', '.join(unknown)}")
    key = raw.get("key")
    if key not in SCHEMA:
        raise ContextError(f"'{key}' is not a context attribute in {CONTEXT_SCHEMA_VERSION}; known: {', '.join(sorted(SCHEMA))}")
    status = raw.get("status", REPORTED)
    if status not in STATUSES:
        raise ContextError(f"status must be one of {', '.join(STATUSES)}")
    value = raw.get("value")
    if status in (UNKNOWN, RETRACTED):
        if value not in (None, ""):
            raise ContextError(f"{status} assertions carry no value")
        value = None
    else:
        if not isinstance(value, str) or not value.strip() or len(value) > 500:
            raise ContextError(f"{key}: value must be 1–500 characters")
        value = value.strip()
        definition = SCHEMA[key]
        if definition.allowed and value not in definition.allowed:
            raise ContextError(f"{key} must be one of {', '.join(definition.allowed)}")
        if key == "clock_offset_bound_ms":
            try:
                bound = float(value)
            except ValueError:
                raise ContextError("clock_offset_bound_ms must be a number of milliseconds") from None
            if not 0 <= bound <= 600000:
                raise ContextError("clock_offset_bound_ms must be between 0 and 600000")
    for label in ("machine_id", "source", "actor"):
        text = raw.get(label)
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ContextError(f"{label} is required")
    effective = parse_time(raw.get("effective_from"), "effective_from")
    if effective > recorded_at:
        raise ContextError("effective_from cannot be later than the time it is recorded")
    evidence = raw.get("evidence") or []
    if not isinstance(evidence, list) or len(evidence) > 20 or not all(isinstance(e, str) and e.strip() for e in evidence):
        raise ContextError("evidence must be a list of up to 20 identifiers")
    if status == CONFIRMED and not evidence and raw.get("source", "").startswith("import profile"):
        raise ContextError("A value reported by an import profile cannot be marked engineer_confirmed without evidence")
    note = raw.get("note", "")
    if not isinstance(note, str) or len(note) > 2000:
        raise ContextError("note must be up to 2000 characters")
    return Assertion(id=assertion_id, machine_id=raw["machine_id"].strip(), key=key, value=value, status=status,
                     source=raw["source"].strip(), actor=raw["actor"].strip(), effective_from=effective.isoformat(),
                     recorded_at=recorded_at.isoformat(), evidence=tuple(evidence), note=note)


def resolve(assertions: Iterable[Assertion], machine_id: str, *, effective_at: float, known_at: Optional[float] = None) -> Dict[str, Any]:
    """Context of `machine_id` as it applied at `effective_at`, using only assertions recorded by `known_at`."""
    usable = [a for a in assertions if a.machine_id == machine_id and _epoch(a.effective_from) <= effective_at
              and (known_at is None or _epoch(a.recorded_at) <= known_at)]
    attributes: Dict[str, Any] = {}
    for key in SCHEMA:
        candidates = [a for a in usable if a.key == key]
        if not candidates:
            attributes[key] = {"value": None, "status": NOT_RECORDED}
            continue
        latest_effective = max(_epoch(a.effective_from) for a in candidates)
        current = [a for a in candidates if _epoch(a.effective_from) == latest_effective]
        chosen = max(current, key=lambda a: (_STATUS_RANK[a.status], _epoch(a.recorded_at), a.id))
        rivals = sorted({a.value for a in current if a.status in (REPORTED, CONFIRMED) and a.value != chosen.value and a.value is not None})
        carried_from = None
        if chosen.status == REPORTED:
            # A later report of the same value does not downgrade an earlier confirmation; any different value breaks the run.
            for earlier in sorted(candidates, key=lambda a: (_epoch(a.effective_from), _epoch(a.recorded_at)), reverse=True):
                if earlier.value != chosen.value or earlier.status in (UNKNOWN, RETRACTED):
                    break
                if earlier.status == CONFIRMED:
                    carried_from = earlier
                    break
        entry = {"value": chosen.value if chosen.status in (REPORTED, CONFIRMED) else None,
                 "status": UNKNOWN if chosen.status == RETRACTED else chosen.status,
                 "source": chosen.source, "actor": chosen.actor, "effective_from": chosen.effective_from,
                 "recorded_at": chosen.recorded_at, "assertion_id": chosen.id, "evidence": list(chosen.evidence)}
        if carried_from is not None and not rivals:
            entry.update(status=CONFIRMED, confirmed_by=carried_from.actor, confirmation_source=carried_from.source,
                         confirmation_assertion_id=carried_from.id, confirmation_evidence=list(carried_from.evidence),
                         later_consistent_report=chosen.source)
        if chosen.status == RETRACTED:
            entry["retracted"] = True
        if rivals:
            entry["conflicting_values"] = rivals
        attributes[key] = entry
    snapshot = {"schema": CONTEXT_SCHEMA_VERSION, "machine_id": machine_id,
                "effective_at": datetime.fromtimestamp(effective_at, timezone.utc).isoformat(),
                "known_at": datetime.fromtimestamp(known_at, timezone.utc).isoformat() if known_at is not None else None,
                "attributes": attributes}
    snapshot["hash"] = snapshot_hash(snapshot)
    return snapshot


def snapshot_hash(snapshot: Dict[str, Any]) -> str:
    """Identity of the resolved context: machine + attributes only, so the hash changes only when knowledge changes."""
    body = {k: snapshot[k] for k in ("schema", "machine_id", "attributes")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def confirmed(snapshot: Dict[str, Any], key: str) -> Optional[str]:
    attribute = snapshot["attributes"].get(key, {})
    return attribute.get("value") if attribute.get("status") == CONFIRMED and not attribute.get("conflicting_values") else None


def gaps(snapshot: Dict[str, Any], keys: Iterable[str]) -> Dict[str, List[str]]:
    """Which keys are not confirmed, split by why."""
    out: Dict[str, List[str]] = {"not_recorded": [], "unknown": [], "reported_only": [], "conflicting": []}
    for key in keys:
        attribute = snapshot["attributes"].get(key, {"status": NOT_RECORDED})
        if attribute.get("conflicting_values"):
            out["conflicting"].append(key)
        elif attribute["status"] == NOT_RECORDED:
            out["not_recorded"].append(key)
        elif attribute["status"] == UNKNOWN:
            out["unknown"].append(key)
        elif attribute["status"] == REPORTED:
            out["reported_only"].append(key)
    return out


def compare(a: Dict[str, Any], b: Dict[str, Any], purpose: str) -> Dict[str, Any]:
    """Fault-specific comparability between two snapshots (e.g. baseline window vs incident window)."""
    if purpose not in COMPARABILITY_RULES:
        raise ContextError(f"Unknown comparability purpose '{purpose}'")
    keys = COMPARABILITY_RULES[purpose]["must_match_confirmed"]
    missing: Dict[str, List[str]] = {}
    mismatched = []
    for key in keys:
        left, right = confirmed(a, key), confirmed(b, key)
        sides = [side for side, value in (("first", left), ("second", right)) if value is None]
        if sides:
            missing[key] = sides
        elif left.strip().casefold() != right.strip().casefold():
            mismatched.append({"key": key, "first": left, "second": right})
    return {"purpose": purpose, "rules_version": COMPARABILITY_RULES_VERSION, "comparable": not missing and not mismatched,
            "required_confirmed": list(keys), "missing_confirmation": missing, "mismatched": mismatched,
            "why": COMPARABILITY_RULES[purpose]["why"]}


def changes_between(a: Dict[str, Any], b: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Attributes whose known value differs between two snapshots (any status), e.g. a software update."""
    out = []
    for key in SCHEMA:
        left, right = a["attributes"].get(key, {}), b["attributes"].get(key, {})
        if left.get("value") != right.get("value") and (left.get("value") is not None or right.get("value") is not None):
            out.append({"key": key, "first": left.get("value"), "first_status": left.get("status"),
                        "second": right.get("value"), "second_status": right.get("status")})
    return out


def describe() -> Dict[str, Any]:
    return {"schema": CONTEXT_SCHEMA_VERSION, "statuses": list(STATUSES),
            "attributes": [{"key": d.key, "category": d.category, "description": d.description, "allowed": list(d.allowed) if d.allowed else None,
                            "dispatch_relevant": d.dispatch_relevant} for d in _DEFS],
            "comparability": {k: {"must_match_confirmed": list(v["must_match_confirmed"]), "why": v["why"]} for k, v in COMPARABILITY_RULES.items()},
            "rules_version": COMPARABILITY_RULES_VERSION}
