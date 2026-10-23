"""Source coverage for the ALFA demo case: what the ORIGINAL recording holds beyond what Veyra imported.

Usage: python scripts/seed/alfa_source_coverage.py <ALFA processed flight folder> <out.json>
e.g. .../processed/carbonZ_2018-09-11-15-06-34_2_rudder_right_failure fixtures/seed/alfa_source_coverage.json

The demo case imported attitude, setpoints, outputs, airspeed and altitude only. Before Veyra asks anyone for "the
flight-controller log", this reads the topics of the same recording that were not imported: flight mode and arming
(mavros/state), the pilot's transmitter inputs (mavros/rc/in), autopilot diagnostics, and the MAVLink stream (for
STATUSTEXT, message 253). Facts only, each with its source file and SHA-256.

Deliberately NOT used: failure_status-* topics. They are the dataset's fault-injection label (the answer key).
"""
import csv
import hashlib
import json
import sys
from pathlib import Path

ONSET = 1536693598.701565      # rudder_rc4 hold start in the demo case (autopilot ROS clock, epoch s)
HOLD_END = 1536693615.697
CHANNEL = 3                    # RC channel 4 (rudder) is index 3 in mavros rc/in and rc/out


def main(folder: Path, out: Path) -> None:
    name = folder.name
    f = lambda topic: folder / f"{name}-{topic}.csv"
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    t = lambda r: int(r["%time"]) / 1e9 - ONSET
    rows = lambda topic: list(csv.DictReader(open(f(topic))))

    state = rows("mavros-state")
    changes, prev = [], None
    for r in state:
        cur = (r["field.mode"], r["field.armed"])
        if cur != prev:
            changes.append({"t_s": round(t(r), 2), "mode": cur[0], "armed": cur[1] == "1"})
            prev = cur
    rc_in, rc_out = rows("mavros-rc-in"), rows("mavros-rc-out")
    hold = lambda rs: [int(r[f"field.channels{CHANNEL}"]) for r in rs if 0 <= t(r) <= HOLD_END - ONSET]
    switches = []
    for ch in range(4, 8):
        prev = None
        for r in rc_in:
            v = r[f"field.channels{ch}"]
            if v != prev and prev is not None:
                switches.append({"rc_input": ch + 1, "t_s": round(t(r), 2), "from": int(prev), "to": int(v)})
            prev = v
    diag = rows("diagnostics")
    warnings = {}
    for r in diag:
        for i in range(16):
            lvl = r.get(f"field.status{i}.level")
            if lvl not in (None, "", "0"):
                key = f"{r[f'field.status{i}.name']}: {r[f'field.status{i}.message']}"
                w = warnings.setdefault(key, {"first_t_s": t(r), "last_t_s": t(r), "samples": 0})
                w["last_t_s"], w["samples"] = t(r), w["samples"] + 1
    mav = rows("mavlink-from")
    statustext = sum(1 for r in mav if r["field.msgid"] == "253")
    used = ["mavros-state", "mavros-rc-in", "mavros-rc-out", "diagnostics", "mavlink-from"]
    result = {
        "version": "alfa-source-coverage-v1", "source_flight": name.split("_rudder")[0].replace("carbonZ_", ""),
        "dataset": "CMU AirLab ALFA (CC0), doi:10.1184/R1/12707963", "onset_epoch_s": ONSET, "hold_end_epoch_s": HOLD_END,
        "files": {u: {"sha256": sha(f(u)), "rows": sum(1 for _ in open(f(u))) - 1} for u in used},
        "topics_in_recording": sorted(p.name.replace(name + "-", "").replace(".csv", "") for p in folder.glob("*.csv")),
        "recorded_span_s": [round(t(state[0]), 1), round(t(state[-1]), 1)],
        "mode_sample_interval_s": round((t(state[-1]) - t(state[0])) / max(1, len(state) - 1), 2),
        "mode_changes": changes,
        "pilot_rudder_input_us": {"min": min(hold(rc_in)), "max": max(hold(rc_in))},
        "rudder_output_us": {"min": min(hold(rc_out)), "max": max(hold(rc_out))},
        "switch_changes": switches,
        "diagnostic_warnings": {k: {"first_t_s": round(v["first_t_s"], 1), "last_t_s": round(v["last_t_s"], 1), "samples": v["samples"]} for k, v in warnings.items()},
        "statustext_messages": statustext, "mavlink_messages": len(mav),
        "not_in_recording": ["Autopilot on-board log (ArduPilot dataflash): ERR and EV events, failsafe records, MODE at full rate",
                             "Servo parameters: SERVO4_MIN / SERVO4_MAX / SERVO4_TRIM / SERVO4_REVERSED"],
        "excluded": "failure_status-* topics: the dataset's fault-injection label, the answer key; never used as evidence.",
    }
    out.write_text(json.dumps(result, indent=1))
    print(f"wrote {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
