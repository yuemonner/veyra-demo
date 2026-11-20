"""Machine profiles: the machine type, not code, names domains and teams and words the checks."""
import json

import pytest

from app.workflows.demo import FILES, FIXTURES
from app.ingest.profile import parse_profile
from app.machines import profiles
from app.store.sqlite_store import CaseStore


def _case(store, machine="EX9"):
    case = store.create_case(machine_id=machine, title="t", actor="Engineer", synthetic=True, symptom_start="2026-03-04T09:06:00Z",
                             symptom_end="2026-03-04T09:07:30Z", symptom_source="synthetic test")
    for data, profile in FILES:
        raw = json.loads((FIXTURES / profile).read_text())
        raw["asset_aliases"] = {**(raw.get("asset_aliases") or {}), "EX1": machine, "EX-01": machine}
        store.commit_import(case["id"], (FIXTURES / data).read_bytes(), data, parse_profile(raw), actor="Engineer")
    return case["id"]


def _set_class(store, machine, value):
    store.assert_context({"machine_id": machine, "key": "machine_class", "value": value, "status": "reported", "actor": "Engineer",
                          "source": "test", "effective_from": "2026-01-01T00:00:00Z"})


def test_builtin_profiles_are_valid_and_cover_every_role():
    loaded = profiles.load_all()
    assert {"generic", "remote_excavator", "fixed_wing_uav", "agricultural_robot"} <= set(loaded)
    assert all(set(p["domains"]) == set(profiles.ROLES) for p in loaded.values())


def test_machine_class_decides_names_owners_and_checks(tmp_path):
    store = CaseStore(tmp_path / "e.db")
    generic = store.investigate(_case(store, "EX9"), actor="Engineer")
    assert generic["machine_profile"]["id"] == "generic" and "generic profile used" in generic["machine_profile"]["basis"]
    _set_class(store, "UAV1", "fixed_wing_uav")
    uav = store.investigate(_case(store, "UAV1"), actor="Engineer")
    assert uav["machine_profile"]["domains"]["control_suite"]["label"] == "Flight controller & autopilot"
    assert "Hydraulic" not in json.dumps(uav["checks"]) and "hydraulics" not in json.dumps(uav["machine_profile"])
    onsite = next(c for c in uav["checks"] if c["check_id"] == "CHK-ONSITE-ACTUATOR")
    assert onsite["question"] == "Inspect the servo, linkage and control surface on the airframe"
    assert next(c for c in uav["checks"] if c["check_id"] == "CHK-PROBE-PATH")["availability"]["state"] == "not_applicable"
    owners = {b["owner"] for b in uav["hypotheses"]}
    assert "Flight software" in owners and "Machine & hydraulics service" not in owners
    store.close()


def test_a_new_machine_type_is_configuration_only(tmp_path, monkeypatch):
    folder = tmp_path / "profiles"
    folder.mkdir()
    (folder / "orchard_robot.json").write_text(json.dumps({
        "id": "orchard_robot", "name": "Orchard harvesting robot",
        "domains": {"network": {"label": "Orchard Wi-Fi", "owner": "IT"}, "control_suite": {"label": "Picking autonomy", "owner": "Autonomy team"},
                    "vehicle_interface": {"label": "Arm & gripper drives", "owner": "Mechatronics"}, "vehicle_body": {"label": "Arm, gripper & chassis", "owner": "Service"}},
        "checks": {"CHK-ONSITE-ACTUATOR": {"question": "Inspect the arm joints and gripper", "who": "Service technician"}}}))
    monkeypatch.setenv("VEYRA_MACHINE_PROFILES_DIR", str(folder))
    store = CaseStore(tmp_path / "e.db")
    _set_class(store, "OR1", "orchard_robot")
    run = store.investigate(_case(store, "OR1"), actor="Engineer")
    assert run["machine_profile"]["name"] == "Orchard harvesting robot"
    assert next(c for c in run["checks"] if c["check_id"] == "CHK-ONSITE-ACTUATOR")["question"] == "Inspect the arm joints and gripper"
    assert {b["owner"] for b in run["hypotheses"]} == {"IT", "Autonomy team", "Mechatronics", "Service"}
    store.close()


def test_controller_log_check_matches_the_evidence():
    from app.engine import checks
    base = next(c for c in checks.CATALOG if c["id"] == "CHK-CONTROLLER-LOGS")
    with_events = checks._for_case(base, {"control_events": True}, None)
    assert "heartbeat gap" in with_events["question"]
    without = checks._for_case(base, {"control_events": False}, None)
    assert "heartbeat gap" not in without["question"] and "No controller events" in without["why"]
    uav = checks._for_case(base, {"control_events": False}, profiles.load_all()["fixed_wing_uav"])
    assert uav["question"].startswith("Retrieve the flight-controller log") and "failsafe" in uav["why"]


def test_invalid_profile_is_refused(tmp_path, monkeypatch):
    folder = tmp_path / "bad"
    folder.mkdir()
    (folder / "bad.json").write_text(json.dumps({"id": "bad", "domains": {"network": {"label": "x", "owner": "y"}}}))
    monkeypatch.setenv("VEYRA_MACHINE_PROFILES_DIR", str(folder))
    with pytest.raises(profiles.ProfileError, match="exactly these roles"):
        profiles.load_all()


def test_profile_decides_which_context_must_be_confirmed(tmp_path):
    store = CaseStore(tmp_path / "e.db")
    _set_class(store, "UAV2", "fixed_wing_uav")
    run = store.investigate(_case(store, "UAV2"), actor="Engineer")
    check = next(c for c in run["checks"] if c["check_id"] == "CHK-CONFIRM-CONTEXT")
    assert "kit_version" not in (check["trigger"] or "") and "vehicle_interface" not in (check["trigger"] or "")
    assert check["question"].startswith("Confirm the airframe model")
    store.close()


def test_check_reasons_use_the_machines_own_names(tmp_path):
    store = CaseStore(tmp_path / "e.db")
    _set_class(store, "UAV3", "fixed_wing_uav")
    run = store.investigate(_case(store, "UAV3"), actor="Engineer")
    text = json.dumps([c.get("trigger") for c in run["checks"]])
    assert "vehicle interface" not in text and "vehicle body" not in text and "control suite" not in text
    store.close()
