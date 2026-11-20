"""Generate a SYNTHETIC battery-electric-vehicle recording as a real ROS file, for demonstrating ingestion, automatic
scenario scans and knowledge reuse when no further authorised customer data is available.

  python scripts/make_synthetic_bev_recording.py --variant correction --format ros1 --out /tmp/syn-bev-2.bag
  python scripts/make_synthetic_bev_recording.py --variant healthy    --format ros2 --out /tmp/syn-bev-3.zip

Topics (all under /synthetic_bev, so only the SYNTHETIC profile `synthetic-bev-ros` binds them and every result is
labelled SYNTHETIC):
  /synthetic_bev/battery            sensor_msgs/BatteryState, 10 Hz (current negative while discharging, percentage 0-1)
  /synthetic_bev/bms_fault_level    std_msgs/UInt8, 2 Hz
  /synthetic_bev/contactors_closed  std_msgs/UInt8, 2 Hz
  /synthetic_bev/cell_voltage_min   std_msgs/Float32, 2 Hz

Variants (shapes from stated rules; the numbers are not any customer's):
  correction  constant 9 A; at t=400 s the fault level rises 1 -> 2 and 1.0 s later the estimator pulls SOC down 25 pp
              over 30 s with the current unchanged; level 3 at t=700 s, contactors open 3 s later
  healthy     constant 9 A, SOC follows the measured charge, no faults
--format ros1 writes a ROS 1 bag; --format ros2 writes a ROS 2 bag (MCAP storage) and zips the folder, which is how
Veyra's upload takes ROS 2 bags.
"""
from __future__ import annotations

import argparse
import shutil
import tempfile
import zipfile
from pathlib import Path

import numpy as np
from rosbags.typesys import Stores, get_typestore

CAP_AH, CURRENT_A, SOC0, DURATION_S = 100.0, 9.0, 0.62, 900.0


def samples(variant: str):
    """Yield (t_s, topic, kind, values) in time order."""
    q = 0.0
    for k in range(int(DURATION_S * 10) + 1):
        t = k / 10.0
        corr = variant == "correction"
        level = 0 if not corr else (1 if t < 400 else (2 if t < 700 else 3))
        closed = not (corr and t >= 703.0)
        cur = CURRENT_A if closed else 0.0
        bias = 0.0 if not corr or t < 401.0 else -0.25 * min(1.0, (t - 401.0) / 30.0)
        soc = max(0.0, round(round((SOC0 - q / (CAP_AH * 3600) + bias) / 0.004) * 0.004, 4))
        cell = (3.10 - 0.0007 * t) if closed else 3.0
        yield t, "/synthetic_bev/battery", "battery", {"current": -cur, "percentage": soc, "capacity": CAP_AH, "voltage": 96 * cell}
        if k % 5 == 0:
            yield t, "/synthetic_bev/bms_fault_level", "u8", level
            yield t, "/synthetic_bev/contactors_closed", "u8", int(closed)
            yield t, "/synthetic_bev/cell_voltage_min", "f32", round(cell, 4)
        q += cur * 0.1


def _defaults(ts, msgtype: str):
    """A message with every field at a neutral default (works for the ROS 1 and ROS 2 definitions)."""
    cls = ts.types[msgtype]
    vals = {}
    for name, f in cls.__dataclass_fields__.items():
        if name == "__msgtype__":
            continue
        tp = str(f.type)
        if "Header" in tp:
            vals[name] = None
        elif "ndarray" in tp or "np." in tp:
            vals[name] = np.array([], dtype=np.float32)
        elif tp in ("str", "<class 'str'>"):
            vals[name] = ""
        elif tp in ("bool", "<class 'bool'>"):
            vals[name] = True
        elif tp in ("float", "<class 'float'>"):
            vals[name] = float("nan")
        elif "list" in tp:
            vals[name] = []
        else:
            vals[name] = 0
    return cls, vals


def message(ts, ros: int, kind: str, value, t: float):
    if kind == "u8":
        return "std_msgs/msg/UInt8", ts.types["std_msgs/msg/UInt8"](data=int(value))
    if kind == "f32":
        return "std_msgs/msg/Float32", ts.types["std_msgs/msg/Float32"](data=float(value))
    mt = "sensor_msgs/msg/BatteryState"
    cls, vals = _defaults(ts, mt)
    stamp = ts.types["builtin_interfaces/msg/Time"](sec=int(t), nanosec=int(round((t % 1) * 1e9)))
    hdr = ts.types["std_msgs/msg/Header"]
    vals["header"] = hdr(seq=0, stamp=stamp, frame_id="synthetic_bev") if ros == 1 else hdr(stamp=stamp, frame_id="synthetic_bev")
    vals.update(value)
    vals["design_capacity"] = CAP_AH
    vals["serial_number"] = "SYNTHETIC"
    vals["location"] = "SYNTHETIC demo pack"
    return mt, cls(**vals)


def write(variant: str, fmt: str, out: Path) -> Path:
    t0_ns = 1_700_000_000_000_000_000
    if fmt == "ros1":
        from rosbags.rosbag1 import Writer
        ts = get_typestore(Stores.ROS1_NOETIC)
        out.unlink(missing_ok=True)
        with Writer(out) as w:
            conns = {}
            for t, topic, kind, value in samples(variant):
                mt, msg = message(ts, 1, kind, value, t)
                if topic not in conns:
                    conns[topic] = w.add_connection(topic, mt, typestore=ts)
                w.write(conns[topic], t0_ns + int(t * 1e9), ts.serialize_ros1(msg, mt))
        return out
    from rosbags.rosbag2 import StoragePlugin, Writer
    ts = get_typestore(Stores.ROS2_HUMBLE)
    tmp = Path(tempfile.mkdtemp(prefix="syn-bev-"))
    folder = tmp / out.stem
    with Writer(folder, version=8, storage_plugin=StoragePlugin.MCAP) as w:
        conns = {}
        for t, topic, kind, value in samples(variant):
            mt, msg = message(ts, 2, kind, value, t)
            if topic not in conns:
                conns[topic] = w.add_connection(topic, mt, typestore=ts)
            w.write(conns[topic], t0_ns + int(t * 1e9), ts.serialize_cdr(msg, mt))
    out.unlink(missing_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(folder.rglob("*")):
            z.write(p, p.relative_to(tmp))
    shutil.rmtree(tmp, ignore_errors=True)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--variant", choices=["correction", "healthy"], required=True)
    ap.add_argument("--format", choices=["ros1", "ros2"], default="ros1")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    print(write(a.variant, a.format, a.out))
