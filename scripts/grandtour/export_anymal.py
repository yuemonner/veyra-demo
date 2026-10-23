"""Export one GrandTour (ETH RSL) ANYmal D mission to a flat JSONL telemetry file, the way a robot operator's
logging stack would hand it over. Runs where the Zarr files are (needs `zarr` + numpy); everything after this
step (import profile, case, investigation) is ordinary Veyra.

What is exported (source-side names; the import profile maps them to Veyra's catalog):
- joint_cmd_pos / joint_pos / joint_torque per joint (scope = joint name), decimated 400 Hz -> 20 Hz;
- drive_status: a joint drive's status word, CHANGES ONLY (the value at recording start is not an event);
- drive_mode: a joint's command mode, changes only;
- twist_cmd (operator twist command, x/y m/s, yaw rad/s) at its own rate; base_twist (leg odometry) at 20 Hz;
- body_attitude roll/pitch/yaw from the state estimator, 20 Hz.
Also writes <out>.inventory.json: array shapes, rates, and full-rate statistics (so decimation hides nothing
that is counted).

Data: GrandTour dataset, ETH Zurich Robotic Systems Lab, https://huggingface.co/datasets/leggedrobotics/grand_tour_dataset
(MIT licence per the dataset card). No fault labels: this export is for portability checks, not accuracy.

Usage: python export_anymal.py <mission folder> <out.jsonl>
Opens arrays read-only (zarr.open_array(mode="r")); never calls zarr.load.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import zarr

JOINTS = ["LF_HAA", "LF_HFE", "LF_KFE", "RF_HAA", "RF_HFE", "RF_KFE", "LH_HAA", "LH_HFE", "LH_KFE", "RH_HAA", "RH_HFE", "RH_KFE"]
ASSET = "ANYmal-D-GrandTour"
DECIMATE_HZ = 20.0


def arr(mission: Path, group: str, name: str) -> np.ndarray:
    return np.asarray(zarr.open_array(str(mission / group / name), mode="r")[:])


def every(ts: np.ndarray, hz: float) -> np.ndarray:
    """Indices that keep one sample per 1/hz seconds (first sample at or after each tick)."""
    ticks = np.arange(ts[0], ts[-1], 1.0 / hz)
    idx = np.unique(np.searchsorted(ts, ticks))
    return idx[idx < len(ts)]


def stats(v: np.ndarray) -> dict:
    a = np.abs(v[np.isfinite(v)])
    if not len(a):
        return {}
    return {"p50": float(np.percentile(a, 50)), "p95": float(np.percentile(a, 95)), "p99": float(np.percentile(a, 99)), "max": float(a.max())}


def euler_xyzw(q: np.ndarray):
    x, y, z, w = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
    roll = np.degrees(np.arctan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y)))
    pitch = np.degrees(np.arcsin(np.clip(2 * (w * y - z * x), -1, 1)))
    yaw = np.degrees(np.arctan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)))
    return roll, pitch, yaw


def main(mission_dir: str, out_path: str) -> int:
    mission = Path(mission_dir)
    rows, inv = [], {"mission": mission.name, "groups": {}}

    def add(t, signal, value, unit, scope=None):
        rows.append({"t": int(round(float(t) * 1e9)), "asset": ASSET, "signal": signal, "value": value, "unit": unit, "scope": scope})

    # Joint actuators
    g = "anymal_state_actuator"
    ts = arr(mission, g, "timestamp")
    keep = every(ts, DECIMATE_HZ)
    inv["groups"][g] = {"samples": int(len(ts)), "start": float(ts[0]), "end": float(ts[-1]),
                        "rate_hz": round(float((len(ts) - 1) / (ts[-1] - ts[0])), 1), "joints": {}}
    for j, name in enumerate(JOINTS):
        p = f"{j:02d}_"
        cmd, pos, tau = arr(mission, g, p + "command_position"), arr(mission, g, p + "state_joint_position"), arr(mission, g, p + "state_joint_torque")
        status, mode = arr(mission, g, p + "state_statusword"), arr(mission, g, p + "command_mode")
        err = np.degrees(cmd - pos)
        worst = int(np.argmax(np.abs(err)))
        status_vals, status_counts = np.unique(status, return_counts=True)
        mode_vals, mode_counts = np.unique(mode, return_counts=True)
        inv["groups"][g]["joints"][name] = {
            "cmd_minus_pos_deg": stats(err), "worst_error_at": float(ts[worst]), "worst_error_deg": float(err[worst]),
            "torque_nm": stats(tau), "cmd_pos_range_deg": [float(np.degrees(cmd.min())), float(np.degrees(cmd.max()))],
            "statusword": {f"0x{int(v):08x}": int(c) for v, c in zip(status_vals, status_counts)},
            "command_mode": {int(v): int(c) for v, c in zip(mode_vals, mode_counts)},
            "statusword_changes": int(np.count_nonzero(np.diff(status.astype(np.int64)))),
        }
        for i in keep:
            add(ts[i], "joint_cmd_pos", float(cmd[i]), "rad", name)
            add(ts[i], "joint_pos", float(pos[i]), "rad", name)
            add(ts[i], "joint_torque", float(tau[i]), "Nm", name)
        for i in np.flatnonzero(np.diff(status.astype(np.int64))) + 1:
            add(ts[i], "drive_status", f"{name} status 0x{int(status[i - 1]):08x} -> 0x{int(status[i]):08x}", None, name)
        for i in np.flatnonzero(np.diff(mode.astype(np.int64))) + 1:
            add(ts[i], "drive_mode", f"{name} command mode {int(mode[i - 1])} -> {int(mode[i])}", None, name)

    # Operator twist command
    g = "anymal_command_twist"
    ts, lin, ang = arr(mission, g, "timestamp"), arr(mission, g, "linear"), arr(mission, g, "angular")
    inv["groups"][g] = {"samples": int(len(ts)), "start": float(ts[0]), "end": float(ts[-1]),
                        "rate_hz": round(float((len(ts) - 1) / max(ts[-1] - ts[0], 1e-9)), 1),
                        "linear_x": stats(lin[:, 0]), "linear_y": stats(lin[:, 1]), "angular_z": stats(ang[:, 2]),
                        "largest_gap_s": float(np.max(np.diff(ts))) if len(ts) > 1 else None}
    for i in range(len(ts)):
        add(ts[i], "twist_cmd", float(lin[i, 0]), "m/s", "x")
        add(ts[i], "twist_cmd", float(lin[i, 1]), "m/s", "y")
        add(ts[i], "twist_cmd_yaw", float(ang[i, 2]), "rad/s", "yaw")

    # Leg odometry twist
    g = "anymal_state_odometry"
    ts, lin, ang = arr(mission, g, "timestamp"), arr(mission, g, "twist_lin"), arr(mission, g, "twist_ang")
    inv["groups"][g] = {"samples": int(len(ts)), "start": float(ts[0]), "end": float(ts[-1]),
                        "rate_hz": round(float((len(ts) - 1) / (ts[-1] - ts[0])), 1),
                        "twist_x": stats(lin[:, 0]), "twist_y": stats(lin[:, 1]), "yaw_rate": stats(ang[:, 2])}
    for i in every(ts, DECIMATE_HZ):
        add(ts[i], "base_twist", float(lin[i, 0]), "m/s", "x")
        add(ts[i], "base_twist", float(lin[i, 1]), "m/s", "y")
        add(ts[i], "base_twist_yaw", float(ang[i, 2]), "rad/s", "yaw")

    # Body attitude from the state estimator
    g = "anymal_state_state_estimator"
    ts, q = arr(mission, g, "timestamp"), arr(mission, g, "pose_orien")
    roll, pitch, yaw = euler_xyzw(q)
    contacts = {f: arr(mission, g, f"{f}_contact") for f in ("LF_FOOT", "RF_FOOT", "LH_FOOT", "RH_FOOT")}
    inv["groups"][g] = {"samples": int(len(ts)), "start": float(ts[0]), "end": float(ts[-1]),
                        "rate_hz": round(float((len(ts) - 1) / (ts[-1] - ts[0])), 1), "first_quaternion": [float(x) for x in q[0]],
                        "roll_deg": stats(roll), "pitch_deg": stats(pitch),
                        "contact_values": {f: {int(v): int(c) for v, c in zip(*np.unique(a, return_counts=True))} for f, a in contacts.items()}}
    for i in every(ts, DECIMATE_HZ):
        add(ts[i], "body_attitude", float(roll[i]), "deg", "roll")
        add(ts[i], "body_attitude", float(pitch[i]), "deg", "pitch")
        add(ts[i], "body_attitude", float(yaw[i]), "deg", "yaw")

    rows = [r for r in rows if not (isinstance(r["value"], float) and not math.isfinite(r["value"]))]
    rows.sort(key=lambda r: (r["t"], r["signal"], r["scope"] or ""))
    out = Path(out_path)
    out.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows))
    inv["rows_written"] = len(rows)
    inv["rows_by_signal"] = {}
    for r in rows:
        inv["rows_by_signal"][r["signal"]] = inv["rows_by_signal"].get(r["signal"], 0) + 1
    Path(str(out) + ".inventory.json").write_text(json.dumps(inv, indent=1))
    print(f"wrote {len(rows)} rows to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
