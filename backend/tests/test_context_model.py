"""Machine Context Model: bitemporal resolution, provenance, conflicts and purpose-specific comparability."""
from datetime import datetime, timezone

import pytest

from app.context import model as ctx

T0 = datetime(2026, 3, 1, tzinfo=timezone.utc)


def make(i, key, value, status="reported", effective="2026-03-01T00:00:00Z", recorded=None, source="Kit inventory", actor="FDE", evidence=None):
    recorded_at = recorded or datetime(2026, 3, 2, tzinfo=timezone.utc)
    raw = {"machine_id": "EX1", "key": key, "status": status, "source": source, "actor": actor, "effective_from": effective, "evidence": evidence or []}
    if value is not None:
        raw["value"] = value
    return ctx.validate_assertion(raw, assertion_id=f"a{i}", recorded_at=recorded_at)


def ts(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def test_unknown_is_explicit_and_unrecorded_is_distinct():
    snap = ctx.resolve([make(1, "kit_version", None, status="unknown")], "EX1", effective_at=ts("2026-03-05T00:00:00Z"))
    assert snap["attributes"]["kit_version"]["status"] == "unknown" and snap["attributes"]["kit_version"]["value"] is None
    assert snap["attributes"]["model"]["status"] == "not_recorded"


def test_effective_time_and_known_time_allow_exact_replay():
    old = make(1, "control_version", "4.3", status="engineer_confirmed", effective="2026-03-01T00:00:00Z", recorded=datetime(2026, 3, 1, 1, tzinfo=timezone.utc))
    update = make(2, "control_version", "4.4", status="engineer_confirmed", effective="2026-03-10T00:00:00Z", recorded=datetime(2026, 3, 10, 1, tzinfo=timezone.utc))
    # Incident on 03-04 sees 4.3 even after the update is known.
    assert ctx.confirmed(ctx.resolve([old, update], "EX1", effective_at=ts("2026-03-04T09:00:00Z")), "control_version") == "4.3"
    assert ctx.confirmed(ctx.resolve([old, update], "EX1", effective_at=ts("2026-03-12T00:00:00Z")), "control_version") == "4.4"
    # A correction recorded later about the past does not change what was known earlier.
    correction = make(3, "control_version", "4.3.1", status="engineer_confirmed", effective="2026-03-01T00:00:00Z", recorded=datetime(2026, 3, 20, tzinfo=timezone.utc))
    before = ctx.resolve([old, update, correction], "EX1", effective_at=ts("2026-03-04T00:00:00Z"), known_at=ts("2026-03-15T00:00:00Z"))
    after = ctx.resolve([old, update, correction], "EX1", effective_at=ts("2026-03-04T00:00:00Z"), known_at=ts("2026-03-21T00:00:00Z"))
    assert before["attributes"]["control_version"]["value"] == "4.3"
    assert after["attributes"]["control_version"]["conflicting_values"] == ["4.3"]  # same effective time, different values: surfaced
    assert ctx.confirmed(after, "control_version") is None  # conflicting values are never treated as confirmed


def test_confirmation_carries_over_consistent_later_reports_but_not_changes():
    confirmed = make(1, "kit_version", "2.1", status="engineer_confirmed", effective="2026-03-01T00:00:00Z", evidence=["photo"])
    same = make(2, "kit_version", "2.1", effective="2026-03-04T09:00:00Z", source="import profile x v1", recorded=datetime(2026, 3, 5, tzinfo=timezone.utc))
    snap = ctx.resolve([confirmed, same], "EX1", effective_at=ts("2026-03-06T00:00:00Z"))
    assert ctx.confirmed(snap, "kit_version") == "2.1" and snap["attributes"]["kit_version"]["confirmation_assertion_id"] == "a1"
    changed = make(3, "kit_version", "2.2", effective="2026-03-05T00:00:00Z", recorded=datetime(2026, 3, 6, tzinfo=timezone.utc))
    snap = ctx.resolve([confirmed, same, changed], "EX1", effective_at=ts("2026-03-06T00:00:00Z"))
    assert snap["attributes"]["kit_version"]["status"] == "reported" and ctx.confirmed(snap, "kit_version") is None


def test_retraction_returns_attribute_to_unknown():
    snap = ctx.resolve([make(1, "site", "Pit 3"), make(2, "site", None, status="retracted", effective="2026-03-02T00:00:00Z", recorded=datetime(2026, 3, 3, tzinfo=timezone.utc))],
                       "EX1", effective_at=ts("2026-03-04T00:00:00Z"))
    assert snap["attributes"]["site"]["status"] == "unknown" and snap["attributes"]["site"]["retracted"]


def test_snapshot_hash_changes_only_with_knowledge():
    items = [make(1, "oem", "X")]
    a = ctx.resolve(items, "EX1", effective_at=ts("2026-03-04T00:00:00Z"), known_at=ts("2026-03-04T00:00:00Z"))
    b = ctx.resolve(items, "EX1", effective_at=ts("2026-03-05T00:00:00Z"), known_at=ts("2026-03-06T00:00:00Z"))
    c = ctx.resolve(items + [make(2, "model", "Y")], "EX1", effective_at=ts("2026-03-05T00:00:00Z"))
    assert a["hash"] == b["hash"] != c["hash"]


def test_purpose_specific_comparability_requires_confirmed_matches():
    keys = ctx.COMPARABILITY_RULES["input_response.baseline"]["must_match_confirmed"]
    base = [make(i, k, "v", status="engineer_confirmed", evidence=["e"]) for i, k in enumerate(keys)]
    snap = ctx.resolve(base, "EX1", effective_at=ts("2026-03-04T00:00:00Z"))
    assert ctx.compare(snap, snap, "input_response.baseline")["comparable"]
    software = base + [make(99, "control_version", "w", status="engineer_confirmed", effective="2026-03-03T00:00:00Z", recorded=datetime(2026, 3, 3, 1, tzinfo=timezone.utc), evidence=["e"])]
    later = ctx.resolve(software, "EX1", effective_at=ts("2026-03-04T00:00:00Z"))
    result = ctx.compare(snap, later, "input_response.baseline")
    assert not result["comparable"] and result["mismatched"][0]["key"] == "control_version"
    reported = ctx.resolve([make(i, k, "v") for i, k in enumerate(keys)], "EX1", effective_at=ts("2026-03-04T00:00:00Z"))
    assert set(ctx.compare(reported, reported, "input_response.baseline")["missing_confirmation"]) == set(keys)
    assert ctx.changes_between(snap, later)[0]["key"] == "control_version"


@pytest.mark.parametrize("raw,message", [
    ({"key": "colour", "value": "red"}, "not a context attribute"),
    ({"key": "remote_access", "value": "full_control"}, "must be one of"),
    ({"key": "site", "value": "x", "effective_from": "2026-03-01T00:00:00"}, "timezone"),
    ({"key": "site", "value": "x", "effective_from": "2030-01-01T00:00:00Z"}, "later than the time it is recorded"),
    ({"key": "site", "value": "x", "status": "engineer_confirmed", "source": "import profile p v1"}, "cannot be marked engineer_confirmed"),
    ({"key": "clock_offset_bound_ms", "value": "about one second"}, "number of milliseconds"),
    ({"key": "site", "value": "x", "guess": True}, "Unknown assertion fields"),
])
def test_assertion_validation(raw, message):
    body = {"machine_id": "EX1", "status": "reported", "source": "doc", "actor": "FDE", "effective_from": "2026-03-01T00:00:00Z", **raw}
    with pytest.raises(ctx.ContextError, match=message):
        ctx.validate_assertion(body, assertion_id="x", recorded_at=datetime(2026, 3, 2, tzinfo=timezone.utc))
