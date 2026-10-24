"""Evidence discovery (app/discovery) and the signal-chain layer (app/engine/mechanisms.py). SYNTHETIC test data only."""
import struct
import sys
from pathlib import Path

import pytest

from app.discovery import mavlink, roles
from app.engine import mechanisms, questions
from app.machines import profiles

CHAIN = profiles.load_all()["fixed_wing_uav"]["signal_chain"]
T0 = 1_700_000_000.0


def _fact(kind, point, t, text, **kw):
    return {"kind": kind, "point": point, "t_rel_s": t, "near_onset": t is not None and abs(t) <= 5, "text": text, "stream": "/s", **kw}


def _disc(facts, roles_present=("controller_mode", "command_link", "operator_input", "actuator_command", "body_motion"), **inc):
    return {"version": "evidence-discovery-v1", "source": {"name": "flight.bag", "sha256": "ab" * 32},
            "inventory": {"streams": [], "excluded": [{"stream": "/failure_status/rudder", "reason": "label"}], "not_understood": [],
                          "read_by_probes": ["/mavros/state"], "roles_present": list(roles_present)},
            "incidents": [{"incident": {"t": T0, "channel": "rudder_rc4"}, "facts": facts, "controller_mode": [], "texts_in_recording": 0, **inc}],
            "gaps": []}


def _run(stuck=True, tracking_dt=0.74):
    out = {"findings": {"actuator_outputs": {"findings": []}, "tracking": {"findings": []},
                        "coverage": [{"signal": "actuator.output", "count": 10}, {"signal": "vehicle.attitude", "count": 10}]}}
    if stuck:
        out["findings"]["actuator_outputs"]["findings"].append({"rule": "STUCK", "channel": "rudder_rc4", "t_start": T0, "held_value": 1900.0,
                                                                "end": "2023-11-14T22:13:37Z", "unit": "us", "reference_range": [1479.0, 1516.0],
                                                                "text": "rudder_rc4 output held 1900"})
    if tracking_dt is not None:
        out["findings"]["tracking"]["findings"].append({"axis": "roll", "t_start": T0 + tracking_dt, "text": "roll stopped following its commands"})
    return out


# ---------- discovery building blocks
def test_label_streams_are_never_evidence():
    for topic in ("/failure_status/rudder", "/failure_status", "/ground_truth/pose", "/groundtruth", "/labels", "/annotations/fault"):
        assert roles.is_label(topic), topic
    for topic in ("/mavros/state", "failure_detector_status", "/diagnostics", "/joint_states"):
        assert not roles.is_label(topic), topic


def test_roles_come_from_standard_types_and_unknown_is_said():
    assert roles.role_for("/mavros/rc/in", "mavros_msgs/msg/RCIn")[0] == "operator_input"
    assert roles.role_for("/mavros/rc/out", "mavros_msgs/RCOut")[0] == "actuator_command"
    assert roles.role_for("/joint_command", "sensor_msgs/msg/JointState")[0] == "actuator_command"
    assert roles.role_for("/joint_states", "sensor_msgs/msg/JointState")[0] == "actuator_feedback"
    assert roles.role_for("vehicle_status", None, px4=True)[0] == "controller_mode"
    role, why = roles.role_for("/mavctrl/rpy", "geometry_msgs/msg/Vector3")
    assert role is None and "generic type" in why


def test_mavlink_param_value_statustext_and_heartbeat_decode():
    raw = struct.pack("<fHH", 3.0, 900, 17) + b"DISABLE_RUDDER".ljust(16, b"\0") + bytes([2])
    words = [int.from_bytes(raw[i:i + 8].ljust(8, b"\0"), "little") for i in range(0, len(raw), 8)]
    p = mavlink.decode(22, mavlink.payload(words, len(raw), 22))
    assert p["name"] == "DISABLE_RUDDER" and p["value"] == 3.0 and p["index"] == 17
    text = bytes([4]) + b"Throttle failsafe on"
    w = [int.from_bytes(text[i:i + 8].ljust(8, b"\0"), "little") for i in range(0, len(text), 8)]
    assert mavlink.decode(253, mavlink.payload(w, len(text), 253)) == {"severity": "WARNING", "text": "Throttle failsafe on"}
    hb = struct.pack("<IBBBB", 15, 1, 3, 128 | 1, 5)
    assert mavlink.decode(0, mavlink.payload([int.from_bytes(hb, "little")], len(hb), 0))["system_status"] == "CRITICAL"


# ---------- signal-chain layer
def test_held_output_with_controller_record_before_it_points_at_the_controller():
    disc = _disc([_fact("parameter_reported", "controller", -0.43, "parameter DISABLE_RUDDER = 3 reported", parameter="DISABLE_RUDDER",
                        occurrences_in_recording=1, chance_near_onset=0.01),
                  _fact("operator_steady", "operator", None, "No operator input moved within 5 s of the onset")])
    a = mechanisms.assess(_run(), CHAIN, disc)
    ev = a["events"][0]
    st = {m["id"]: m["status"] for m in ev["mechanisms"]}
    assert ev["lead"]["point"] == "output_command"
    assert st["controller_behaviour"] == "supported" and st["operator_command"] == "unlikely"
    assert all(st[k] == "unlikely" for k in ("output_path", "physical_response", "sensor_error"))  # motion departed AFTER the output
    roll = next(m for m in ev["members"] if m["key"] == "TRACKING:roll")
    assert roll["relation"] == "consequence" and "not independent evidence" in roll["note"]
    ctl = next(m for m in ev["mechanisms"] if m["id"] == "controller_behaviour")
    assert "DISABLE_RUDDER" in ctl["needs"] and "confirmed" not in ev["summary"].lower()


def test_records_after_the_onset_and_routine_records_never_support_a_cause():
    disc = _disc([_fact("health_change", "controller", 2.5, "lost attitude stabilisation", occurrences_in_recording=1, chance_near_onset=0.05),
                  _fact("state_change", "controller", -1.0, "land detector flag 0 -> 1", occurrences_in_recording=80, chance_near_onset=0.9),
                  _fact("departure", "operator", 1.6, "switch 1101 -> 1926", role="operator_input")])
    ev = mechanisms.assess(_run(), CHAIN, disc)["events"][0]
    st = {m["id"]: m for m in ev["mechanisms"]}
    assert st["controller_behaviour"]["status"] == "possible"
    kinds = {e["kind"] for e in st["controller_behaviour"]["evidence"]}
    assert {"after_onset", "routine"} <= kinds
    assert st["operator_command"]["status"] == "unlikely"


def test_without_discovery_the_order_still_argues_but_nothing_is_supported():
    ev = mechanisms.assess(_run(), CHAIN, None)["events"][0]
    st = {m["id"]: m["status"] for m in ev["mechanisms"]}
    assert "supported" not in st.values() and st["output_path"] == "unlikely" and st["controller_behaviour"] == "cannot_tell"


def test_motion_first_keeps_downstream_mechanisms_open():
    run = _run(stuck=True, tracking_dt=None)
    run["findings"]["tracking"]["findings"].append({"axis": "roll", "t_start": T0 - 1.5, "text": "roll stopped following its commands"})
    ev = mechanisms.assess(run, CHAIN, None)["events"][0]
    st = {m["id"]: m["status"] for m in ev["mechanisms"]}
    assert ev["lead"]["point"] == "motion" and st["physical_response"] != "unlikely" and st["sensor_error"] != "unlikely"


def test_sensor_error_uses_independent_measurements():
    run = {"findings": {"transients": {"signals": [{"signal": "vehicle.altitude", "scope": "baro", "events": [{"t_start": T0, "in_symptom_window": True}]}]}}}
    rows = []
    for k in range(-300, 50):
        t = T0 + k * 0.1
        rows.append({"signal": "vehicle.altitude", "scope": "baro", "t": t, "value": 100.0 + (8.0 if k >= 0 else 0.0) + 0.01 * (k % 3), "unit": "m"})
        rows.append({"signal": "vehicle.altitude", "scope": "gps", "t": t, "value": 300.0 + 0.01 * (k % 3), "unit": "m"})
    ev = mechanisms.assess(run, CHAIN, None, rows)["events"][0]
    sensor = next(m for m in ev["mechanisms"] if m["id"] == "sensor_error")
    assert sensor["status"] == "supported" and "only this measurement moved" in sensor["evidence"][0]["text"]
    together = [dict(r, value=r["value"] + (8.0 if r["scope"] == "gps" and r["t"] >= T0 else 0.0)) for r in rows]
    sensor2 = next(m for m in mechanisms.assess(run, CHAIN, None, together)["events"][0]["mechanisms"] if m["id"] == "sensor_error")
    assert sensor2["status"] in ("possible", "unlikely") and "motion was real" in sensor2["evidence"][0]["text"]


def test_profiles_validate_the_signal_chain(tmp_path, monkeypatch):
    bad = {"id": "x", "domains": {r: {"label": r, "owner": r} for r in profiles.ROLES},
           "signal_chain": {"points": [{"id": "a", "label": "A"}], "mechanisms": [{"id": "m", "label": "M", "acts_on": "nowhere"}]}}
    with pytest.raises(profiles.ProfileError):
        profiles._validate(bad, "x.json")
    for p in profiles.load_all().values():
        assert p["signal_chain"]["points"] and p["signal_chain"]["mechanisms"]


# ---------- questions use the discovery record
def test_questions_answer_from_the_whole_recording_and_ask_the_specific_next_question():
    disc = _disc([_fact("parameter_reported", "controller", -0.43, "parameter DISABLE_RUDDER = 3 reported", parameter="DISABLE_RUDDER",
                        occurrences_in_recording=1, chance_near_onset=0.01),
                  _fact("operator_vs_output", "operator", None, "channel 4: operator input 1509–1509 vs output 1496–1900")],
                 controller_mode=[{"stream": "/mavros/state", "field": "mode", "at_onset": "GUIDED", "changes_in_recording": 0, "changes_in_window": 0,
                                   "sample_interval_s": 1.0}])
    run = _run()
    out = questions.answer(run, [], [], discovery=disc, causal=mechanisms.assess(run, CHAIN, disc))
    by = {q["id"].split(":")[0]: q for q in out["questions"]}
    assert by["commanded"]["status"] == "answered" and "mode GUIDED" in by["commanded"]["answer"] and "DISABLE_RUDDER" in by["commanded"]["answer"]
    assert by["config"]["status"] == "not_answerable" and out["next"]["question"].startswith("What does DISABLE_RUDDER do")
    assert "whole original recording" in out["next"]["why_a_person"]
    assert out["discovery"]["excluded"][0]["stream"] == "/failure_status/rudder" and out["causal"]["applicable"]
    assert "unlikely" in (by["physical"].get("order_note") or "").lower() or by["physical"]["order_note"]


def test_evidence_discovery_records_are_machine_generated_only(tmp_path):
    from app.ledger.model import LedgerError
    from app.store.sqlite_store import CaseStore
    store = CaseStore(tmp_path / "d.db")
    c = store.create_case(machine_id="M", title="t", actor="a", synthetic=True)
    with pytest.raises((LedgerError, Exception)):
        store.append(c["id"], "evidence_discovery", {"incidents": []}, actor="someone")
    store.close()


# ---------- the upload path runs discovery on the real file
def test_upload_runs_discovery_on_the_whole_recording(tmp_path):
    pytest.importorskip("rosbags")
    pytest.importorskip("mcap")
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import make_synthetic_recordings as gen
    from app.ingest import bags
    from app.store.sqlite_store import CaseStore
    from app.workflows.debrief import debrief_recording
    path = gen.write_all(tmp_path / "rec")["ros1"]
    store = CaseStore(tmp_path / "e.db")
    out = debrief_recording(store, path, bags.suggest(bags.inspect(path)), machine_id="QD1", title="Left front leg dragging (SYNTHETIC)", actor="Engineer",
                            machine_class="legged_robot", problem_from_s=55, problem_to_s=119, normal_from_s=5, normal_to_s=50, synthetic=True)
    assert "entry_id" in out["discovery"], out["discovery"]
    disc = store.discovery(out["case_id"])
    inv = disc["inventory"]
    assert {s["stream"]: s["role"] for s in inv["streams"]}["/joint_command"] == "actuator_command"
    assert disc["incidents"] and disc["source"]["sha256"] == bags.sha256_file(path)
    q = store.questions(out["case_id"])
    assert q["causal"]["applicable"] and q["causal"]["chain"][0]["id"] == "operator"
    assert store.verify(out["case_id"])["ledger_chain"]["valid"]
    store.close()
