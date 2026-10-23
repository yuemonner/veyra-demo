"""Load a real ANYmal D mission (GrandTour, ETH RSL) into a Veyra engine store as two cases.

Cross-machine check: the same investigation engine that ran on the ALFA fixed-wing aircraft, on a walking
robot. Nothing here is engine code: an import profile (signal names and units), the machine's context
(machine_class = legged_robot) and two cases.

Case A, REAL, no reported fault: the whole mission, with steady walking at the commanded 1 m/s
(80-140 s after start) declared as the known-good reference. Expected answer: nothing departs. Any finding is
a false alarm and is reported as such.
Case B, SYNTHETIC FAULT ON REAL DATA: the same mission with a fault injected into ONE joint drive (LF_KFE, left
front knee) from 200 s: the measured knee angle sags up to 25 deg further past its target (in the direction it already lags) within 0.5 s and the drive's
measured torque drops to 30 %. Only those two joint signals are changed; the body's real motion is NOT
re-simulated, so body velocity and attitude still show the healthy walk. The case is flagged synthetic.

GrandTour has no fault labels, so this is a portability check (does the engine name the right part of THIS
machine, with checks for THIS machine), not an accuracy benchmark.

Data mapping knowledge (not engine code): an ANYdrive applies a position target only in command mode 13
(joint position/velocity/torque with PID gains). In mode 1 (freeze) the target is not applied, so target rows
exported while a drive was in mode 1 are dropped before import.

Usage: python anymal_load_cases.py <export .jsonl from export_anymal.py> <engine.db> [--hosted]
  --hosted: the lighter copy shipped with the hosted demo (see HOSTED)
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.ingest.profile import parse_profile  # noqa: E402
from app.store.sqlite_store import CaseStore  # noqa: E402

MACHINE = "ANYmal-D-GrandTour"
APPLIED_MODE = 13
REFERENCE_S = (80.0, 140.0)
FAULT = {"joint": "LF_KFE", "at_s": 200.0, "sag_deg": 25.0, "ramp_s": 0.5, "torque_factor": 0.3}
SOURCE = "GrandTour dataset, ETH Zurich Robotic Systems Lab (MIT licence per dataset card), mission 2024-10-01-11-29-55"

PROFILE = {
    "profile_id": "grandtour-anymal-d", "version": 1, "format": "jsonl", "synthetic": False,
    "columns": {"timestamp": "t", "asset": "asset", "signal": "signal", "value": "value", "unit": "unit", "scope": "scope"},
    "signal_map": {"joint_cmd_pos": "joint.position_command", "joint_pos": "joint.position", "joint_torque": "joint.torque",
                   "drive_status": "actuator.status", "drive_mode": "controller.event",
                   "twist_cmd": "control.velocity_setpoint", "twist_cmd_yaw": "control.yaw_rate_setpoint",
                   "base_twist": "vehicle.velocity", "base_twist_yaw": "vehicle.yaw_rate", "body_attitude": "vehicle.attitude"},
    "clock_domain": "anymal_onboard_clock", "timestamp_format": "epoch", "epoch_unit": "ns",
    "notes": f"{SOURCE}. Exported by scripts/grandtour/export_anymal.py (joints decimated 400 -> 20 Hz).",
}


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def applied_only(rows):
    """Drop joint targets exported while that joint's drive was not applying them (mode != 13)."""
    changes = {}
    for r in rows:
        if r["signal"] == "drive_mode":
            new = int(r["value"].rsplit("-> ", 1)[1])
            old = int(r["value"].split("mode ", 1)[1].split(" ->", 1)[0])
            changes.setdefault(r["scope"], []).append((r["t"], old, new))
    out, dropped = [], 0
    for r in rows:
        if r["signal"] == "joint_cmd_pos":
            ch = changes.get(r["scope"], [])
            mode = ch[0][1] if ch else APPLIED_MODE
            for t, _, new in ch:
                if t <= r["t"]:
                    mode = new
            if mode != APPLIED_MODE:
                dropped += 1
                continue
        out.append(r)
    return out, dropped


def inject(rows, t0):
    """The knee gives way under load: it flexes FURTHER past its target, in the direction it already lags
    (taken from the data before the fault, so the injected error adds to the normal lag, not cancels it)."""
    at = t0 + FAULT["at_s"]
    j = FAULT["joint"]
    cmd = {r["t"]: r["value"] for r in rows if r["signal"] == "joint_cmd_pos" and r["scope"] == j and r["t"] / 1e9 < at}
    lags = sorted(r["value"] - cmd[r["t"]] for r in rows if r["signal"] == "joint_pos" and r["scope"] == j and r["t"] in cmd)
    direction = 1.0 if lags[len(lags) // 2] >= 0 else -1.0
    out = []
    for r in rows:
        t = r["t"] / 1e9
        if r["scope"] == j and t >= at and r["signal"] in ("joint_pos", "joint_torque"):
            r = dict(r)
            if r["signal"] == "joint_pos":
                r["value"] = r["value"] + direction * math.radians(FAULT["sag_deg"]) * min(1.0, (t - at) / FAULT["ramp_s"])
            else:
                r["value"] = r["value"] * FAULT["torque_factor"]
        out.append(r)
    return out


def encode(rows) -> bytes:
    return "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows).encode()


def files(rows, stem):
    """Three log files, as a robot's logging stack splits them (and each under the 25 MB import limit)."""
    part = lambda r: ("front_legs" if r["scope"][:2] in ("LF", "RF") else "hind_legs") if r["signal"].startswith(("joint_", "drive_")) else "body"
    out = {}
    for r in rows:
        out.setdefault(part(r), []).append(r)
    return [(f"{stem}_{name}.jsonl", encode(items)) for name, items in sorted(out.items())]


def commit(store, cid, rows, stem, profile, reason):
    for name, content in files(rows, stem):
        store.commit_import(cid, content, name, profile, actor="GrandTour loader", review_reason=reason)


HOSTED = {"span_s": (60.0, 310.0), "hz": 10.0, "drop": ("joint_torque",)}


def lighter(rows, t0):
    """The hosted demo's copy: 60-310 s of the mission (reference walk, the injected fault, the robot lying down),
    measurements thinned to 10 Hz per signal, joint torque left out. Events are all kept."""
    keep, last = [], {}
    lo, hi = t0 + HOSTED["span_s"][0], t0 + HOSTED["span_s"][1]
    for r in rows:
        t = r["t"] / 1e9
        if not lo <= t <= hi or r["signal"] in HOSTED["drop"]:
            continue
        if isinstance(r["value"], (int, float)):
            key = (r["signal"], r["scope"])
            if key in last and t - last[key] < 1.0 / HOSTED["hz"] - 1e-6:
                continue
            last[key] = t
        keep.append(r)
    return keep


def main(export: str, db: str, hosted: bool = False) -> int:
    raw = [json.loads(line) for line in open(export)]
    rows, dropped = applied_only(raw)
    t0 = min(r["t"] for r in rows) / 1e9
    t_end = max(r["t"] for r in rows) / 1e9
    note = ""
    if hosted:
        t_end = t0 + HOSTED["span_s"][1]
        note = " Hosted demo copy: 60-310 s of the mission, measurements at 10 Hz, joint torque left out."
    profile = parse_profile(PROFILE)
    store = CaseStore(Path(db))
    store.assert_context({"machine_id": MACHINE, "key": "machine_class", "value": "legged_robot", "status": "reported", "actor": "GrandTour loader",
                          "source": "GrandTour dataset card (ANYbotics ANYmal D quadruped)", "effective_from": "2024-01-01T00:00:00Z"})
    store.assert_context({"machine_id": MACHINE, "key": "model", "value": "ANYmal D", "status": "reported", "actor": "GrandTour loader",
                          "source": "GrandTour dataset card", "effective_from": "2024-01-01T00:00:00Z"})
    reference = {"window_from": iso(t0 + REFERENCE_S[0]), "window_to": iso(t0 + REFERENCE_S[1]),
                 "basis": "Steady walking at the commanded 1 m/s, 80-140 s into a mission with no reported fault", "reference_source": "GrandTour loader"}
    ids = {}
    # Case A: real mission, no reported fault
    a = store.create_case(machine_id=MACHINE, title="ANYmal D mission 2024-10-01 11:29 (real data, no reported fault) — false-alarm check",
                          actor="GrandTour loader", synthetic=False, symptom_start=iso(t0 + REFERENCE_S[1]), symptom_end=iso(t_end),
                          symptom_source=f"No fault reported. Cross-machine check: everything after the reference walk is searched. {SOURCE}.{note}")
    commit(store, a["id"], lighter(rows, t0) if hosted else rows, "anymal_2024-10-01-11-29-55", profile,
           f"Public dataset import; {dropped} joint targets dropped (drive not applying them, mode != 13)")
    store.append(a["id"], "reference_declared", reference, actor="GrandTour loader")
    ids["real_no_fault"] = a["id"]
    # Case B: injected fault in one joint drive
    b = store.create_case(machine_id=MACHINE, title="ANYmal D: left front knee drive fault — SYNTHETIC fault injected into a real mission",
                          actor="GrandTour loader", synthetic=True, symptom_start=iso(t0 + FAULT["at_s"] - 10), symptom_end=iso(t0 + FAULT["at_s"] + 30),
                          symptom_source=(f"SYNTHETIC: from {FAULT['at_s']:g} s the measured {FAULT['joint']} angle sags up to {FAULT['sag_deg']:g}° past its target "
                                          f"and its torque drops to {FAULT['torque_factor'] * 100:g} %. Body motion not re-simulated. Underlying data: {SOURCE}.{note}"))
    injected = parse_profile({**PROFILE, "profile_id": "grandtour-anymal-d-injected-fault", "synthetic": True,
                              "notes": PROFILE["notes"] + " SYNTHETIC: a joint fault injected by scripts/grandtour/anymal_load_cases.py."})
    commit(store, b["id"], lighter(inject(rows, t0), t0) if hosted else inject(rows, t0), "anymal_2024-10-01-11-29-55_INJECTED_LF_KFE", injected,
           "Real mission with a labelled synthetic joint fault injected by scripts/grandtour/anymal_load_cases.py")
    store.append(b["id"], "reference_declared", reference, actor="GrandTour loader")
    ids["synthetic_fault"] = b["id"]
    results = {}
    for name, cid in ids.items():
        t = datetime.now()
        run = store.investigate(cid, actor="GrandTour loader")
        results[name] = {"case_id": cid, "run_id": run["id"], "seconds": round((datetime.now() - t).total_seconds(), 1)}
    store.close()
    print(json.dumps({"dropped_targets": dropped, "rows": len(rows), "t0": t0, **results}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], "--hosted" in sys.argv[3:]))
