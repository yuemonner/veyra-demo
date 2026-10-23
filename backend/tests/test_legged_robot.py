"""Second machine type: a walking robot (ANYmal D-like). Joint and body-velocity tracking pairs, legged profile."""
import math

from app.analysis import tracking
from app.ingest import catalog
from app.machines import profiles


def _joint(t, cmd, meas, joint="LF_KFE"):
    return [{"signal": "joint.position_command", "scope": joint, "t": t, "value": cmd, "domain": "control_suite"},
            {"signal": "joint.position", "scope": joint, "t": t, "value": meas, "domain": "vehicle_interface"}]


def _gait(i):
    return 60 + 30 * math.sin(i * 0.6)  # a knee target swinging with the gait


def test_joint_that_stops_following_is_found_and_attributed_to_its_drive():
    lag = lambda i: 25 + 8 * math.sin(i * 1.3)  # a compliant leg always lags its target by a lot
    rows = [r for i in range(1200) for r in _joint(i * 0.05, _gait(i), _gait(i) - lag(i) - (40 if i >= 900 else 0))]
    out = tracking.detect(rows, (40, 60), (0, 20))
    assert out["pairs"] == ["joint_position"]
    first = out["first"]
    assert first and first["axis"] == "LF_KFE" and first["domain"] == "vehicle_interface"
    assert 45.0 <= first["t_start"] <= 45.3 and "p95" in first["text"] and "normally" in first["text"]


def test_normal_gait_lag_is_not_a_departure():
    rows = [r for i in range(1200) for r in _joint(i * 0.05, _gait(i), _gait(i) - 25 - 8 * math.sin(i * 1.3))]
    assert tracking.detect(rows, (40, 60), (0, 20))["findings"] == []


def test_robot_that_stops_moving_while_commanded_departs_on_body_velocity():
    rows = []
    for i in range(1200):
        t = i * 0.05
        rows += [{"signal": "control.velocity_setpoint", "scope": "x", "t": t, "value": 1.0},
                 {"signal": "vehicle.velocity", "scope": "x", "t": t, "value": (0.92 + 0.03 * math.sin(i)) if t < 45 else 0.05 + 0.01 * math.sin(i), "domain": "vehicle_body"}]
    first = tracking.detect(rows, (40, 60), (0, 20))["first"]
    assert first["axis"] == "body velocity x" and first["domain"] == "vehicle_body" and "m/s" in first["text"]


def test_attitude_pair_unchanged():
    assert tracking.PAIRS[0]["command"] == tracking.COMMAND == "control.attitude_setpoint"
    assert tracking.PAIRS[0]["ratio"] == tracking.RATIO and tracking.PAIRS[0]["min_error"] == tracking.MIN_ERROR_DEG


def test_legged_profile_names_the_robot_parts():
    p = profiles.load_all()["legged_robot"]
    assert p["domains"]["vehicle_interface"]["label"] == "Joint drives (actuators)"
    assert p["checks"]["CHK-PROBE-PATH"]["applicable"] is False
    for signal in p["signal_roles"]:
        assert signal in catalog.CATALOG
    assert {"joint.position_command", "control.velocity_setpoint", "control.yaw_rate_setpoint"} <= catalog.COMMANDED_SIGNALS


def test_slower_command_than_the_reference_borrows_the_nearest_command_size():
    rows = []
    for i in range(1200):
        t = i * 0.05
        cmd = 1.0 if t < 30 else 0.5                      # reference walk only at 1 m/s, then the operator slows down
        rows += [{"signal": "control.velocity_setpoint", "scope": "x", "t": t, "value": cmd},
                 {"signal": "vehicle.velocity", "scope": "x", "t": t, "value": cmd - 0.08 + 0.03 * math.sin(i), "domain": "vehicle_body"}]
    out = tracking.detect(rows, (40, 60), (0, 20))
    axis = out["axes"][0]
    assert out["findings"] == [] and axis["borrowed_bins"] == {"0-0.3": ">=0.7", "0.3-0.7": ">=0.7"} and axis["unscored_bins"] == []


def _event(t, joint, value, signal="controller.event"):
    return {"signal": signal, "scope": joint, "t": t, "event_value": f"{joint} {value}", "domain": "control_suite"}


def test_whole_robot_mode_change_is_routine_but_one_drive_alone_is_not():
    from app.analysis import screening
    rule = profiles.load_all()["legged_robot"]["routine_events"][0]
    joints = ["LF_HAA", "LF_HFE", "LF_KFE", "RF_HAA", "RF_HFE", "RF_KFE", "LH_HAA", "LH_HFE", "LH_KFE", "RH_HAA", "RH_HFE", "RH_KFE"]
    rows = [_event(100 + k * 0.01, j, "command mode 13 -> 1") for k, j in enumerate(joints)] + [_event(200, "LF_KFE", "command mode 13 -> 1")]
    out = screening.events(rows, (90, 210), routine=[rule])
    routine = [e for e in out["events"] if e["routine"]]
    assert len(routine) == 12 and not [e for e in out["events"] if e["t"] == 200][0]["routine"]


def test_event_seen_in_known_good_runs_is_routine():
    from app.analysis import screening
    out = screening.events([_event(5, "x", "AUTO (armed)")], (0, 10), normal_values={("controller.event", "x AUTO (armed)")})
    assert out["events"][0]["routine"] == "also recorded in the machine's known-good runs"
