"""ForEnt benchmark: does Veyra's legged-robot set-up find real, labelled entrapments of a Unitree Go2?

Data: ForEnt (forest entrapments of a Unitree Go2), doi:10.5281/zenodo.18824718, CC BY 4.0. The public release
holds 2 of the paper's 11 sequences: 18 labelled entrapments (start/end, type, severity).

Protocol (fixed before scoring; labels are read only by the scorer):
1. Convert each sequence to the same canonical signals as the ANYmal check, with the SAME machine profile
   (legged_robot): joint position/torque (lowstate), joint targets (lowcmd), body velocity and turn rate (robot
   odometry), attitude (body IMU), the operator's joystick as the commanded velocity, sport-mode changes as events.
2. The joystick is a stick deflection, not a speed. Its gain to m/s (and rad/s) is fitted by least squares on the
   KNOWN-GOOD walk only (data mapping, stated in the output).
3. Known-good walk: the first 60 s after the operator first moves the stick in seq_1. It is used as the machine
   baseline for both sequences (one robot). The scorer checks that no label overlaps it.
4. Blind scan: Veyra's tracking rules (tracking-rules-v2, unchanged) run continuously after the known-good walk. Each
   departure is an alarm; alarms on any axis within MERGE_S of each other form one alarm episode.
5. Scoring: a label is FOUND if an episode starts in [start - 2 s, end + 2 s]. An episode that matches no label is a
   FALSE ALARM. Latency = episode start - label start.

Usage: python forent_benchmark.py <ForEnt processed dir> <extra dir from extract_bags.py> <out dir>
"""
from __future__ import annotations

import bisect
import csv
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.analysis import tracking  # noqa: E402
from app.ingest.pipeline import ingest  # noqa: E402
from app.ingest.profile import parse_profile  # noqa: E402

MACHINE = "Unitree-Go2-ForEnt"
JOINTS = ["FR_hip", "FR_thigh", "FR_calf", "FL_hip", "FL_thigh", "FL_calf", "RR_hip", "RR_thigh", "RR_calf", "RL_hip", "RL_thigh", "RL_calf"]
HZ = 20.0
KNOWN_GOOD_S = 60.0
STICK_ACTIVE = 0.2
MERGE_S = 10.0
MATCH_SLACK_S = 2.0
PROFILE = {
    "profile_id": "forent-unitree-go2", "version": 1, "format": "jsonl", "synthetic": False,
    "columns": {"timestamp": "t", "asset": "asset", "signal": "signal", "value": "value", "unit": "unit", "scope": "scope"},
    "signal_map": {"joint_cmd_pos": "joint.position_command", "joint_pos": "joint.position", "joint_torque": "joint.torque",
                   "twist_cmd": "control.velocity_setpoint", "twist_cmd_yaw": "control.yaw_rate_setpoint",
                   "base_twist": "vehicle.velocity", "base_twist_yaw": "vehicle.yaw_rate", "body_attitude": "vehicle.attitude",
                   "sport_mode": "controller.event"},
    "clock_domain": "go2_bag_clock", "timestamp_format": "epoch", "epoch_unit": "ns",
    "notes": "ForEnt (CC BY 4.0, doi:10.5281/zenodo.18824718). Converted by scripts/forent/forent_benchmark.py.",
}


def read(path):
    with open(path, newline="") as f:
        yield from csv.DictReader(f)


def decimate(rows, hz=HZ):
    last = None
    for r in rows:
        t = int(r["timestamp"])
        if last is None or t - last >= 1e9 / hz:
            last = t
            yield t, r


def raw_rows(processed: Path, extra: Path):
    """Source-side rows, plus the joystick (stick deflection) kept separately until its gain is fitted."""
    out, stick = [], []
    add = lambda t, s, v, u, sc=None: out.append({"t": t, "asset": MACHINE, "signal": s, "value": v, "unit": u, "scope": sc})  # noqa: E731
    for t, r in decimate(read(processed / "joint_states.csv")):
        for i, j in enumerate(JOINTS):
            add(t, "joint_pos", float(r[f"{i}_q"]), "rad", j)
            add(t, "joint_torque", float(r[f"{i}_tau_est"]), "Nm", j)
    if (extra / "lowcmd.csv").exists():
        for t, r in decimate(read(extra / "lowcmd.csv")):
            for i, j in enumerate(JOINTS):
                if float(r[f"{i}_kp"]) > 0:  # a position target is only applied with position gain
                    add(t, "joint_cmd_pos", float(r[f"{i}_q"]), "rad", j)
    for t, r in decimate(read(processed / "odometry.csv")):
        add(t, "base_twist", float(r["twist_twist_linear_x"]), "m/s", "x")
        add(t, "base_twist", float(r["twist_twist_linear_y"]), "m/s", "y")
        add(t, "base_twist_yaw", float(r["twist_twist_angular_z"]), "rad/s", "yaw")
    for t, r in decimate(read(processed / "imu_body.csv")):
        for k, axis in enumerate(("roll", "pitch", "yaw")):
            add(t, "body_attitude", math.degrees(float(r[f"rpy_{k}"])), "deg", axis)
    if (extra / "wirelesscontroller.csv").exists():
        for r in read(extra / "wirelesscontroller.csv"):
            stick.append((int(r["timestamp"]), float(r["ly"]), float(r["lx"]), float(r["rx"])))
    if (extra / "sportmodestate.csv").exists():
        last = None
        for r in read(extra / "sportmodestate.csv"):
            state = (r.get("mode"), r.get("gait_type"))
            if last is not None and state != last:
                add(int(r["timestamp"]), "sport_mode", f"sport mode {last[0]} -> {state[0]}, gait {last[1]} -> {state[1]}", None)
            last = state
    return out, stick


def fit_gain(stick, rows, window, axis_index, signal, scope):
    """Least-squares gain (through zero) from stick deflection to measured speed, on the known-good walk only."""
    meas = sorted((r["t"], r["value"]) for r in rows if r["signal"] == signal and r["scope"] == scope and window[0] <= r["t"] / 1e9 <= window[1])
    mt = [t for t, _ in meas]
    num = den = 0.0
    for s in stick:
        if not window[0] <= s[0] / 1e9 <= window[1]:
            continue
        k = bisect.bisect_left(mt, s[0])
        if k < len(meas) and abs(mt[k] - s[0]) < 0.1e9:
            x = s[axis_index]
            num += x * meas[k][1]
            den += x * x
    return num / den if den else 0.0


def first_walk(stick):
    for t, ly, lx, rx in stick:
        if max(abs(ly), abs(lx), abs(rx)) >= STICK_ACTIVE:
            return t / 1e9
    return None


def episodes(alarms):
    out = []
    for a in sorted(alarms, key=lambda a: a["t"]):
        if out and a["t"] - out[-1]["end"] <= MERGE_S:
            out[-1]["end"] = a["t"]
            out[-1]["axes"].add(a["axis"])
        else:
            out.append({"start": a["t"], "end": a["t"], "axes": {a["axis"]}, "text": a["text"]})
    return out


def scan(rows, baseline, start):
    """Run the tracking rules continuously: after each departure, restart MERGE_S later."""
    wanted = {p[k] for p in tracking.PAIRS for k in ("command", "measured")}
    rows = [r for r in rows if r["signal"] in wanted]
    baseline = {**baseline, "rows": [r for r in baseline["rows"] if r["signal"] in wanted]}
    alarms, cursor = [], start
    end = max(r["t"] for r in rows)
    while cursor < end:
        out = tracking.detect([r for r in rows if r["t"] > cursor], None, None, [baseline])
        if not out.get("findings"):
            break
        for f in out["findings"]:
            if f["t_start"] < cursor + MERGE_S:
                alarms.append({"t": f["t_start"], "axis": f["axis"], "text": f["text"]})
        cursor = out["findings"][0]["t_start"] + MERGE_S
    return alarms


def main(processed_dir: str, extra_dir: str, out_dir: str) -> int:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    profile = parse_profile(PROFILE)
    seqs = {}
    for seq in ("seq_1", "seq_2"):
        raw, stick = raw_rows(Path(processed_dir) / seq, Path(extra_dir) / seq)
        seqs[seq] = {"raw": raw, "stick": stick, "t0": min(r["t"] for r in raw) / 1e9}
    walk = first_walk(seqs["seq_1"]["stick"])
    good = (walk, walk + KNOWN_GOOD_S)
    raw1 = seqs["seq_1"]["raw"]
    gains = {"x": fit_gain(seqs["seq_1"]["stick"], raw1, good, 1, "base_twist", "x"),
             "y": fit_gain(seqs["seq_1"]["stick"], raw1, good, 2, "base_twist", "y"),
             "yaw": fit_gain(seqs["seq_1"]["stick"], raw1, good, 3, "base_twist_yaw", "yaw")}
    report = {"dataset": "ForEnt (CC BY 4.0), public sequences 1-2", "machine_profile": "legged_robot", "rules": tracking.TRACKING_RULES_VERSION,
              "known_good_walk_s": [round(good[0] - seqs["seq_1"]["t0"], 1), round(good[1] - seqs["seq_1"]["t0"], 1)],
              "stick_gain": {k: round(v, 3) for k, v in gains.items()}, "sequences": {}}
    canon = {}
    for seq, d in seqs.items():
        rows = list(d["raw"])
        for t, ly, lx, rx in d["stick"]:
            rows.append({"t": t, "asset": MACHINE, "signal": "twist_cmd", "value": ly * gains["x"], "unit": "m/s", "scope": "x"})
            rows.append({"t": t, "asset": MACHINE, "signal": "twist_cmd", "value": lx * gains["y"], "unit": "m/s", "scope": "y"})
            rows.append({"t": t, "asset": MACHINE, "signal": "twist_cmd_yaw", "value": rx * gains["yaw"], "unit": "rad/s", "scope": "yaw"})
        rows.sort(key=lambda r: (r["t"], r["signal"], r["scope"] or ""))
        content = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows).encode()
        (out / f"forent_{seq}.jsonl").write_bytes(content)
        canon[seq] = []
        for k in range(0, len(rows), 100_000):  # files under the 25 MB import limit, as a logging stack would split them
            part = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows[k:k + 100_000]).encode()
            result = ingest(part, f"forent_{seq}_{k // 100_000}.jsonl", profile, MACHINE)
            if not result.rows:
                raise SystemExit(f"{seq} part {k // 100_000} not imported: {result.status} {result.file_errors}")
            canon[seq] += result.rows
    baseline = {"case_id": "known-good-walk-seq_1", "rows": [r for r in canon["seq_1"] if good[0] <= r["t"] <= good[1]]}
    found_total = labels_total = false_total = 0
    for seq, rows in canon.items():
        t0 = seqs[seq]["t0"]
        start = good[1] if seq == "seq_1" else min(r["t"] for r in rows)
        alarms = scan(rows, baseline, start)
        eps = episodes(alarms)
        labels = [{"start": float(l["robot_start_s"]), "end": float(l["robot_end_s"]), "type": l["type"], "severity": int(l["severity"])}
                  for l in read(Path(processed_dir) / seq / "label.csv")]   # read only now, after the blind scan
        assert not any(l["start"] < good[1] - t0 and l["end"] > good[0] - t0 for l in labels) or seq != "seq_1", "a label overlaps the known-good walk"
        for e in eps:
            e["start_s"], e["end_s"] = round(e["start"] - t0, 2), round(e["end"] - t0, 2)
            e["matches"] = [i for i, l in enumerate(labels) if l["start"] - MATCH_SLACK_S <= e["start_s"] <= l["end"] + MATCH_SLACK_S]
        for i, l in enumerate(labels):
            hits = [e for e in eps if i in e["matches"]]
            l["found"] = bool(hits)
            l["latency_s"] = round(hits[0]["start_s"] - l["start"], 2) if hits else None
            l["axes"] = sorted(hits[0]["axes"]) if hits else []
        false = [e for e in eps if not e["matches"]]
        scored_from = round(start - t0, 1)
        report["sequences"][seq] = {"duration_s": round(max(r["t"] for r in rows) - min(r["t"] for r in rows), 1), "scored_from_s": scored_from,
                                    "labels": labels, "found": sum(l["found"] for l in labels), "false_alarm_episodes": len(false),
                                    "episodes": [{k: (sorted(v) if isinstance(v, set) else v) for k, v in e.items() if k not in ("start", "end")} for e in eps]}
        found_total += sum(l["found"] for l in labels)
        labels_total += len(labels)
        false_total += len(false)
    report["summary"] = {"labels": labels_total, "found": found_total, "false_alarm_episodes": false_total}
    (out / "forent_benchmark.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report["summary"]), json.dumps(report["stick_gain"]), report["known_good_walk_s"])
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
