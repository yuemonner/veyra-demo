"""Fixes from the ALFA case validation: repeated frames, wrapping angles, normal saturation, command tracking, report checks."""
from app.agent.runner import _check_report
from app.analysis import outputs, reference, tracking, transients


def _m(signal, scope, unit, t, v):
    return {"kind": "measurement", "signal": signal, "scope": scope, "unit": unit, "clock_domain": "c", "t": t, "value": v, "domain": "vehicle_body"}


def test_sample_and_hold_stream_is_judged_on_real_updates():
    # 20 rows/s, but the value only changes every 8th row (2.5 real updates/s) with small steps.
    rows = [_m("vehicle.attitude", "pitch", "deg", i / 20.0, round((i // 8) * 0.3 + (2.0 if i // 8 == 40 else 0.0), 3)) for i in range(2000)]
    out = transients.detect(rows)
    assert out["events_total"] == 0
    skipped = out["skipped"][0]
    assert skipped["repeated_sample_share"] > 0.8 and "real updates per second" in skipped["reason"]


def test_turn_through_180_degrees_is_not_an_event():
    yaw = [((170 + 0.5 * i + 180) % 360) - 180 for i in range(400)]  # steady turn crossing +180 -> -180
    out = transients.detect([_m("vehicle.attitude", "yaw", "deg", i / 20.0, v) for i, v in enumerate(yaw)])
    assert out["events_total"] == 0 and out["signals"][0].get("unwrapped_angle") is True


def test_wrapping_angle_compared_as_offsets():
    ref = [_m("vehicle.attitude", "yaw", "deg", i, 178.0 if i % 2 else -178.0) for i in range(20)]
    inc = [_m("vehicle.attitude", "yaw", "deg", 100 + i, 179.0 if i % 2 else -179.0) for i in range(20)]
    out = reference.compare(ref + inc, (0, 19), (100, 119))
    sig = out["signals"][0]
    assert sig["status"] == "within_reference_range" and "circular" in sig


def _out(t, v, ch="throttle_rc3"):
    return {"signal": "actuator.output", "scope": ch, "unit": "us", "t": t, "value": v}


def test_hold_seen_in_known_good_runs_is_not_reported():
    ref = [_out(i * 0.33, 1200 + (i % 10) * 30) for i in range(60)]                  # moves in the reference
    hold = [_out(20 + i * 0.33, 1700.0) for i in range(12)]                             # 3.6 s at 1700
    normal = {"case_id": "case-good", "rows": [_out(i * 0.33, 1700.0) for i in range(30)]}  # 9.6 s at 1700 when healthy
    assert outputs.detect(ref + hold, (0, 19.7))["findings"], "v2 behaviour without known-good runs"
    out = outputs.detect(ref + hold, (0, 19.7), [normal])
    assert out["findings"] == [] and out["machine_baseline"]["suppressed_as_normal"][0]["longest_normal_hold_s"] >= 9
    longer = [_out(20 + i * 0.33, 1700.0) for i in range(40)]                           # 12.9 s: longer than ever seen
    assert outputs.detect(ref + longer, (0, 19.7), [normal])["findings"][0]["rule"] == "STUCK"


def _att(axis, t, cmd, meas):
    return [{"signal": "control.attitude_setpoint", "scope": axis, "t": t, "value": cmd},
            {"signal": "vehicle.attitude", "scope": axis, "t": t, "value": meas}]


def test_command_tracking_departure_found_against_known_good_runs():
    good = {"case_id": "case-good", "rows": [r for i in range(300) for r in _att("roll", i * 0.1, 20 * ((i // 50) % 2), 20 * ((i // 50) % 2) + (i % 5) * 0.4)]}
    rows = [r for i in range(400) for r in _att("roll", i * 0.1, 20 * ((i // 50) % 2),
                                                20 * ((i // 50) % 2) + ((i % 5) * 0.4 if i < 250 else 30 + i % 3))]
    out = tracking.detect(rows, (20, 40), (0, 10), [good])
    first = out["first"]
    assert first and first["axis"] == "roll" and 24.9 <= first["t_start"] <= 25.5
    calm = tracking.detect(rows[:2 * 240], (10, 23), (0, 10), [good])
    assert calm["findings"] == []


def test_report_check_rejects_unsupported_numbers_and_interpretation_in_observations():
    outputs_by_call = {"t1": '{"held":1900,"seconds":17.0}'}
    ok = {"summary": "s", "observations": [{"text": "Rudder held 1900 for 17.0 s", "evidence": ["t1"]}], "hypotheses": [], "proposed_checks": []}
    assert _check_report(ok, {"t1"}, outputs_by_call) == []
    wrong = {**ok, "observations": [{"text": "Rudder held 1950 for 17.0 s", "evidence": ["t1"]}]}
    assert any("1950" in p for p in _check_report(wrong, {"t1"}, outputs_by_call))
    hedged = {**ok, "observations": [{"text": "Rudder held 1900, likely a failsafe", "evidence": ["t1"]}]}
    assert any("interpretation" in p for p in _check_report(hedged, {"t1"}, outputs_by_call))


def test_known_good_runs_exclude_other_cases_and_the_same_time_period(tmp_path):
    import json
    from datetime import datetime, timezone
    from app.ingest.profile import parse_profile
    from app.store.sqlite_store import CaseStore
    profile = parse_profile({"profile_id": "t", "version": 1, "format": "jsonl", "synthetic": False,
                             "columns": {"timestamp": "t", "asset": "asset", "signal": "signal", "value": "value", "unit": "unit", "scope": "scope"},
                             "signal_map": {"rc_out": "actuator.output"}, "clock_domain": "c", "timestamp_format": "epoch", "epoch_unit": "ns"})
    def flight(t0, n=60, v=lambda i: 1500 + i % 7):
        return "".join(json.dumps({"t": int((t0 + i * 0.3) * 1e9), "asset": "M1", "signal": "rc_out", "value": v(i), "unit": "us", "scope": "throttle_rc3"}) + "\n" for i in range(n)).encode()
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")
    store = CaseStore(tmp_path / "e.db")
    ids = []
    base = 1_700_000_000
    for t0, content in ((base, flight(base)), (base + 5000, flight(base + 5000)), (base + 10, flight(base + 10, v=lambda i: 1100))):  # third overlaps the first in time
        c = store.create_case(machine_id="M1", title="t", actor="a", synthetic=False)
        store.commit_import(c["id"], content, f"{t0}.jsonl", profile, actor="a")
        store.append(c["id"], "reference_declared", {"window_from": iso(t0), "window_to": iso(t0 + 20), "basis": "test"}, actor="a")
        ids.append(c["id"])
    runs = store.known_good_runs(store.case(ids[0]))
    used = {r["case_id"]: r for r in runs}
    assert ids[0] not in used and ids[1] in used, "never itself; other periods are used"
    own_end = base + 59 * 0.3
    assert all(r["t"] > own_end for r in used.get(ids[2], {"rows": []})["rows"]), "data from the same time period is never a baseline"
    store.close()


def test_same_recording_is_counted_once_and_never_as_history(tmp_path):
    import json
    from datetime import datetime, timezone
    from app.ingest.profile import parse_profile
    from app.store.sqlite_store import CaseStore
    profile = parse_profile({"profile_id": "t", "version": 1, "format": "jsonl", "synthetic": False,
                             "columns": {"timestamp": "t", "asset": "asset", "signal": "signal", "value": "value", "unit": "unit", "scope": "scope"},
                             "signal_map": {"rc_out": "actuator.output"}, "clock_domain": "c", "timestamp_format": "epoch", "epoch_unit": "ns"})
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")
    def content(t0):
        return "".join(json.dumps({"t": int((t0 + i * 0.3) * 1e9), "asset": "M1", "signal": "rc_out", "value": 1500 + i % 7, "unit": "us", "scope": "x"}) + "\n" for i in range(60)).encode()
    store = CaseStore(tmp_path / "e.db")
    base = 1_700_000_000
    def make(t0, kind="remote_response_delay"):
        c = store.create_case(machine_id="M1", title="t", actor="a", synthetic=False, fault_type=kind)
        store.commit_import(c["id"], content(t0), "f.jsonl", profile, actor="a")
        store.append(c["id"], "reference_declared", {"window_from": iso(t0), "window_to": iso(t0 + 20), "basis": "test"}, actor="a")
        return c["id"]
    good_a, good_b = make(base + 1000, "known_good_run"), make(base + 1000, "known_good_run")  # the same normal recording, loaded twice
    first, again = make(base), make(base)                                                     # the same incident recording, opened twice
    assert [r["case_id"] for r in store.known_good_runs(store.case(again))] == [good_a]
    assert store.machine_history("M1", exclude_case=again, synthetic=False) == []
    store.close()
