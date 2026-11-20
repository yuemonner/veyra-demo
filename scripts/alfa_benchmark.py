"""Blind benchmark on REAL public flight data: CMU AirLab ALFA (fixed-wing UAV, injected faults).

Dataset: Keipour, Mousaei, Scherer, "ALFA: A Dataset for UAV Fault and Anomaly Detection", IJRR 2021.
https://doi.org/10.1184/R1/12707963 — README states CC0; the repository page states CC BY 4.0.
Cite the paper when using results. The data is not committed to this repository.

What it does, per processed flight:
  1. Converts the flight's ROS-topic CSVs into one canonical Veyra import (JSON Lines + import profile).
     The ground-truth files (failure_status-*.csv) are NOT converted: Veyra runs blind.
  2. Creates a case (real data, synthetic=false), declares the first 20 s as the known-good reference,
     and runs the unchanged Investigation Engine.
  3. Only then reads the ground truth and scores: detection, delay, isolation, false alarms.

Rules are the engine's fixed, versioned rules. actuator-output-rules-v1 was written after looking at
three development flights (DEVELOPMENT) and before any scored run: its results are the pre-registered
ones. actuator-output-rules-v2 was written AFTER seeing every v1 result (all v1 errors came from throttle
held steady in cruise), so its scores on these 47 flights are post-hoc and need fresh flights to confirm.

Caveat that belongs with every number: ALFA injects faults in the autopilot firmware, so the recorded
servo outputs carry the injected command. On a machine with a mechanical fault, the equivalent signal
is an actuator position or feedback sensor. "Motion confirms" reports whether the aircraft's measured
attitude departed from its commanded attitude after the detected onset.

v3 protocol (investigation-engine-v2.2): before any investigation, every normal flight is also loaded as a
KNOWN-GOOD RUN of the aircraft (fault_type known_good_run, whole flight declared known-good). The
engine compares each investigated flight only with OTHER flights' known-good data: a case never sees a
recording that shares a file with it. So each normal flight is tested against the other nine.
Flight-mode changes (mavros-state) are imported as controller events.

Usage (standard library only):
  python scripts/alfa_benchmark.py <ALFA processed/ folder> <output folder>
"""
from __future__ import annotations

import csv
import glob
import json
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.ingest.profile import parse_profile  # noqa: E402
from app.store.sqlite_store import CaseStore  # noqa: E402

MACHINE = "ALFA-CarbonZ"
REFERENCE_S = 20.0
DETECTION_WINDOW = (-1.0, 10.0)       # a detection counts if it lands within this many seconds of the true onset
DEVELOPMENT = {"carbonZ_2018-09-11-15-06-34_1_rudder_right_failure", "carbonZ_2018-07-30-16-39-00_1_engine_failure",
               "carbonZ_2018-09-11-17-55-30_2_left_aileron_failure"}
# ArduPlane default output order for this airframe (RC1 unused; RC5/RC6 flaperons). Machine context, not learned.
CHANNELS = {"channels1": "elevator_rc2", "channels2": "throttle_rc3", "channels3": "rudder_rc4", "channels4": "aileron_rc5", "channels5": "aileron_rc6"}
FAULT_OF_CHANNEL = {"elevator_rc2": "elevator", "throttle_rc3": "engines", "rudder_rc4": "rudder", "aileron_rc5": "aileron", "aileron_rc6": "aileron"}
AXIS_OF_FAULT = {"elevator": "pitch", "rudder": "yaw", "aileron": "roll", "engines": None}

PROFILE = {
    "profile_id": "alfa-carbonz-processed", "version": 1, "format": "jsonl", "synthetic": False,
    "columns": {"timestamp": "t", "asset": "asset", "signal": "signal", "value": "value", "unit": "unit", "scope": "scope"},
    "signal_map": {"rc_out": "actuator.output", "attitude_cmd": "control.attitude_setpoint", "attitude_meas": "vehicle.attitude",
                   "groundspeed": "vehicle.ground_speed", "airspeed": "vehicle.airspeed", "altitude": "vehicle.altitude", "throttle": "propulsion.throttle",
                   "mode": "controller.event"},
    "clock_domain": "autopilot_ros_clock", "timestamp_format": "epoch", "epoch_unit": "ns",
    "notes": "CMU AirLab ALFA processed flight (real data; CC0 per dataset README). Converted by scripts/alfa_benchmark.py; ground-truth topics excluded.",
}


def _rows(folder: str, topic: str):
    files = glob.glob(os.path.join(folder, f"*-{topic}.csv"))
    if not files:
        return []
    with open(files[0], newline="") as handle:
        return list(csv.DictReader(handle))


def convert(folder: str) -> bytes:
    out = []
    def add(t, signal, value, unit, scope=None):
        out.append({"t": int(t), "asset": MACHINE, "signal": signal, "value": float(value), "unit": unit, "scope": scope})
    for r in _rows(folder, "mavros-rc-out"):
        for column, name in CHANNELS.items():
            add(r["%time"], "rc_out", r[f"field.{column}"], "us", name)
    for axis in ("roll", "pitch", "yaw"):
        for r in _rows(folder, f"mavros-nav_info-{axis}"):
            add(r["%time"], "attitude_cmd", r["field.commanded"], "deg", axis)
            add(r["%time"], "attitude_meas", r["field.measured"], "deg", axis)
    for r in _rows(folder, "mavros-nav_info-airspeed"):
        add(r["%time"], "airspeed", r["field.measured"], "m/s")
    for r in _rows(folder, "mavros-vfr_hud"):
        add(r["%time"], "groundspeed", r["field.groundspeed"], "m/s")
        add(r["%time"], "altitude", r["field.altitude"], "m")
        add(r["%time"], "throttle", r["field.throttle"], "ratio")
    last = None
    for r in _rows(folder, "mavros-state"):  # flight-mode and arming changes, as controller events
        state = (r.get("field.mode"), r.get("field.armed"))
        if last is not None and state != last:  # changes only; the state at the start of the recording is not an event
            out.append({"t": int(r["%time"]), "asset": MACHINE, "signal": "mode", "value": f"{state[0]} ({'armed' if state[1] == 'True' else 'disarmed'})", "unit": None, "scope": None})
            last = state
    out.sort(key=lambda r: (r["t"], r["signal"], r["scope"] or ""))
    return "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in out).encode()


def ground_truth(folder: str):
    onset, kinds = None, []
    for path in glob.glob(os.path.join(folder, "*failure_status-*.csv")):
        kinds.append(path.rsplit("failure_status-", 1)[1][:-4])
        with open(path, newline="") as handle:
            rows = list(csv.DictReader(handle))
        if rows:
            t = int(rows[0]["%time"]) / 1e9
            onset = t if onset is None else min(onset, t)
    return onset, sorted(kinds)


def motion_confirms(rows, axis, onset, reference_end):
    """Did measured attitude depart from commanded attitude in the 10 s after onset, beyond the reference p95?"""
    if axis is None:
        return None
    cmd = {r["t"]: r["value"] for r in rows if r["signal"] == "control.attitude_setpoint" and r["scope"] == axis}
    errs = []
    for r in rows:
        if r["signal"] == "vehicle.attitude" and r["scope"] == axis and r["t"] in cmd:
            e = r["value"] - cmd[r["t"]]
            if axis == "yaw":
                e = (e + 180) % 360 - 180
            errs.append((r["t"], abs(e)))
    ref = sorted(e for t, e in errs if t <= reference_end)
    post = [e for t, e in errs if onset <= t <= onset + 10]
    if len(ref) < 10 or not post:
        return None
    p95 = ref[int(0.95 * (len(ref) - 1))]
    return {"reference_p95_deg": round(p95, 2), "post_max_deg": round(max(post), 2), "departed": max(post) > p95}


def main(processed: str, out_dir: str) -> int:
    out = Path(out_dir)
    (out / "imports").mkdir(parents=True, exist_ok=True)
    db = out / "alfa-benchmark.db"
    if db.exists():
        db.unlink()
    store = CaseStore(db)
    store.assert_context({"machine_id": MACHINE, "key": "machine_class", "value": "fixed_wing_uav", "status": "reported", "actor": "ALFA benchmark",
                          "source": "CMU AirLab ALFA dataset description (Carbon Z T-28 fixed-wing UAV)", "effective_from": "2018-01-01T00:00:00Z"})
    profile = parse_profile(PROFILE)
    results = []
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")  # noqa: E731
    folders = sorted(glob.glob(os.path.join(processed, "*/")))
    # 1. Known-good runs of the aircraft: the normal flights, whole flight declared known-good.
    for folder in folders:
        name = os.path.basename(folder.rstrip("/"))
        if "no_failure" not in name:
            continue
        good = store.create_case(machine_id=MACHINE, title=f"Known-good flight {name.split('_', 1)[1][:19]}", actor="ALFA benchmark",
                                 synthetic=False, fault_type="known_good_run")
        _, rep = store.commit_import(good["id"], convert(folder), f"{name}.jsonl", profile, actor="ALFA benchmark",
                                     review_reason="Benchmark import; rejected rows listed in the import report")
        store.append(good["id"], "reference_declared", {"window_from": iso(min(r["t"] for r in rep.rows)), "window_to": iso(max(r["t"] for r in rep.rows)),
                                                         "basis": "Normal flight (dataset label: no failure injected); whole flight known-good",
                                                         "reference_source": "ALFA benchmark protocol"}, actor="ALFA benchmark")
    # 2. Every flight as a case with its first 20 s declared known-good (all declared before any investigation).
    cases = []
    for folder in folders:
        name = os.path.basename(folder.rstrip("/"))
        content = convert(folder)
        (out / "imports" / f"{name}.jsonl").write_bytes(content)
        case = store.create_case(machine_id=MACHINE, title=f"ALFA flight {name.split('_', 1)[1][:19]}", actor="ALFA benchmark", synthetic=False)
        record, report = store.commit_import(case["id"], content, f"{name}.jsonl", profile, actor="ALFA benchmark",
                                             review_reason="Benchmark import; rejected rows listed in the import report")
        start = min(r["t"] for r in report.rows)
        store.append(case["id"], "reference_declared", {"window_from": iso(start), "window_to": iso(start + REFERENCE_S),
                                                         "basis": "First 20 s of the processed autonomous sequence, before any injected fault in this benchmark design",
                                                         "reference_source": "ALFA benchmark protocol"}, actor="ALFA benchmark")
        cases.append((folder, name, case, start))
    # 3. Investigate blind, then score.
    for folder, name, case, start in cases:
        ref_end = start + REFERENCE_S
        run = store.investigate(case["id"], actor="ALFA benchmark")
        finding = (run["findings"].get("actuator_outputs") or {}).get("first")
        onset, kinds = ground_truth(folder)                     # read only after the blind run
        predicted = None
        if finding:
            channels = finding.get("channels") or [finding["channel"]]
            predicted = sorted({FAULT_OF_CHANNEL[c] for c in channels if c in FAULT_OF_CHANNEL})
        entry = {"flight": name, "split": "development" if name in DEVELOPMENT else "held_out", "case_id": case["id"],
                 "truth": {"kinds": kinds, "onset_s": round(onset - start, 2) if onset else None},
                 "veyra": {"rule": finding["rule"] if finding else None, "channel": finding["channel"] if finding else None,
                           "onset_s": round(finding["t_start"] - start, 2) if finding else None, "predicted_fault": predicted,
                           "text": finding["text"] if finding else None},
                 "known_good_runs_used": len((run.get("machine_baseline") or {}).get("known_good_runs", [])),
                 "suppressed_as_normal": [x["text"] for x in ((run["findings"].get("actuator_outputs") or {}).get("machine_baseline") or {}).get("suppressed_as_normal", [])],
                 "brief_events_in_window": (run["findings"].get("transients") or {}).get("events_in_window")}
        trk = (run["findings"].get("tracking") or {}).get("first")
        entry["tracking_departure"] = {"axis": trk["axis"], "onset_s": round(trk["t_start"] - start, 2)} if trk else None
        if onset is None:
            entry["outcome"] = "false_alarm" if finding else "correct_quiet"
        elif not finding:
            entry["outcome"] = "missed"
        else:
            delay = finding["t_start"] - onset
            entry["delay_s"] = round(delay, 2)
            if delay < DETECTION_WINDOW[0]:
                entry["outcome"] = "early_alarm"
            elif delay > DETECTION_WINDOW[1]:
                entry["outcome"] = "late"
            else:
                entry["outcome"] = "detected"
                entry["isolated"] = bool(predicted) and set(predicted) <= set(kinds) and bool(set(predicted) & set(kinds))
            imports = store.imports(case["id"])
            rows = [r for i in imports for r in i["rows"]]
            axis = AXIS_OF_FAULT.get(kinds[0]) if kinds else None
            entry["motion_confirms"] = motion_confirms(rows, axis, finding["t_start"], ref_end)
        if onset is not None and entry["tracking_departure"]:
            entry["tracking_departure"]["delay_after_true_onset_s"] = round(entry["tracking_departure"]["onset_s"] - (onset - start), 2)
        results.append(entry)
        print(f"{entry['split']:<11} {entry['outcome']:<14} {name}")
    store.close()

    def score(split):
        rs = [r for r in results if split == "all" or r["split"] == split]
        faults = [r for r in rs if r["truth"]["kinds"]]
        normal = [r for r in rs if not r["truth"]["kinds"]]
        detected = [r for r in faults if r["outcome"] == "detected"]
        delays = [r["delay_s"] for r in detected]
        return {"flights": len(rs), "fault_flights": len(faults), "normal_flights": len(normal),
                "detected": len(detected), "isolated_correctly": sum(1 for r in detected if r.get("isolated")),
                "missed": sum(1 for r in faults if r["outcome"] == "missed"), "early_or_late": sum(1 for r in faults if r["outcome"] in ("early_alarm", "late")),
                "false_alarms_on_normal_flights": sum(1 for r in normal if r["outcome"] == "false_alarm"),
                "median_delay_s": round(statistics.median(delays), 2) if delays else None, "max_delay_s": round(max(delays), 2) if delays else None,
                "tracking_departure_on_fault_flights": sum(1 for r in faults if r.get("tracking_departure")),
                "tracking_departure_on_normal_flights": sum(1 for r in normal if r.get("tracking_departure")),
                "brief_events_in_window_total": sum(r.get("brief_events_in_window") or 0 for r in rs),
                "by_fault": {k: {"flights": sum(1 for r in faults if k in r["truth"]["kinds"]),
                                 "detected": sum(1 for r in detected if k in r["truth"]["kinds"])} for k in ("engines", "aileron", "elevator", "rudder")}}
    summary = {"dataset": "CMU AirLab ALFA (processed), real flights", "license": "CC0 per dataset README; CC BY 4.0 on the repository page",
               "citation": "Keipour, Mousaei, Scherer. ALFA: A Dataset for UAV Fault and Anomaly Detection. IJRR 2021. doi:10.1184/R1/12707963",
               "engine_rules": __import__("app.analysis.outputs", fromlist=["x"]).OUTPUT_RULES_VERSION, "reference": f"first {REFERENCE_S:g} s of each flight; known-good runs = the other normal flights and other flights' first {REFERENCE_S:g} s",
               "engine": __import__("app.engine.investigate", fromlist=["x"]).ENGINE_VERSION, "detection_window_s": DETECTION_WINDOW,
               "development_flights": sorted(DEVELOPMENT), "held_out": score("held_out"), "development": score("development"), "all": score("all"),
               "caveat": "Faults are injected in autopilot firmware, so servo outputs carry the injected command; on a machine with a mechanical fault the equivalent signal is an actuator position or feedback sensor."}
    (out / "alfa_results.json").write_text(json.dumps({"summary": summary, "flights": results}, indent=1))
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
