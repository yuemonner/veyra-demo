"""Write SYNTHETIC robot recordings in real robot formats, for testing Veyra's recording import (deterministic).

A small four-legged robot walks for 120 s. From 60 s its left front knee (LF_KFE) stops following its command: the
measured angle sags up to 25 deg past the target and the drive reports an ERROR in /diagnostics.
Topics: /joint_states (sensor_msgs/JointState, 100 Hz), /joint_command (JointState targets, 100 Hz),
/cmd_vel (geometry_msgs/Twist, 10 Hz), /odom (nav_msgs/Odometry, 50 Hz), /diagnostics (DiagnosticArray, 1 Hz).

Formats: ROS 1 bag (.bag), ROS 2 bag folder (sqlite3) zipped (.zip), MCAP with ROS 2 messages (.mcap).
SYNTHETIC: not field evidence and not private data.

Usage: python scripts/make_synthetic_recordings.py <out_dir>
"""
from __future__ import annotations

import math
import shutil
import sys
import zipfile
from pathlib import Path

START_NS = 1_780_000_000_000_000_000  # 2026-05-28 (synthetic)
DURATION_S = 120.0
FAULT_AT_S = 60.0
JOINTS = ["LF_HFE", "LF_KFE", "RF_HFE", "RF_KFE"]


def target(j: int, t: float) -> float:
    return 0.6 * math.sin(2 * math.pi * t / 1.2 + j) + (0.9 if j % 2 else 0.4)


def measured(j: int, t: float) -> float:
    lag = target(j, t - 0.03) - 0.02
    if JOINTS[j] == "LF_KFE" and t >= FAULT_AT_S:
        lag -= math.radians(25.0) * min(1.0, (t - FAULT_AT_S) / 0.5)
    return lag


def _schedule():
    """(t_s, topic) in time order."""
    events = []
    for topic, hz in (("/joint_states", 100), ("/joint_command", 100), ("/cmd_vel", 10), ("/odom", 50), ("/diagnostics", 1)):
        n = int(DURATION_S * hz)
        events += [(k / hz, topic) for k in range(n)]
    events.sort()
    return events


def _messages(ts, ros: int):
    """Build message objects with a rosbags typestore. ros = 1 or 2."""
    from rosbags.typesys import Stores, get_typestore
    store = get_typestore(Stores.ROS1_NOETIC if ros == 1 else Stores.ROS2_HUMBLE)
    T = store.types
    import numpy as np

    def stamp(t):
        ns = START_NS + int(t * 1e9)
        Time = T["builtin_interfaces/msg/Time"]
        return Time(sec=ns // 1_000_000_000, nanosec=ns % 1_000_000_000)

    def header(t, frame="base"):
        H = T["std_msgs/msg/Header"]
        return H(seq=0, stamp=stamp(t), frame_id=frame) if ros == 1 else H(stamp=stamp(t), frame_id=frame)

    def make(topic, t):
        if topic in ("/joint_states", "/joint_command"):
            f = measured if topic == "/joint_states" else target
            pos = np.array([f(j, t) for j in range(len(JOINTS))], dtype=np.float64)
            eff = np.array([(4.0 if JOINTS[j] != "LF_KFE" or t < FAULT_AT_S else 1.2) + 0.3 * math.sin(t * 3 + j) for j in range(len(JOINTS))], dtype=np.float64)
            return T["sensor_msgs/msg/JointState"](header=header(t), name=list(JOINTS), position=pos, velocity=np.zeros(len(JOINTS)), effort=eff)
        if topic == "/cmd_vel":
            V3 = T["geometry_msgs/msg/Vector3"]
            return T["geometry_msgs/msg/Twist"](linear=V3(x=0.8, y=0.0, z=0.0), angular=V3(x=0.0, y=0.0, z=0.1 * math.sin(t / 7)))
        if topic == "/odom":
            V3, P, Q = T["geometry_msgs/msg/Vector3"], T["geometry_msgs/msg/Point"], T["geometry_msgs/msg/Quaternion"]
            yaw = 0.02 * t
            q = Q(x=0.0, y=0.0, z=math.sin(yaw / 2), w=math.cos(yaw / 2))
            pose = T["geometry_msgs/msg/PoseWithCovariance"](pose=T["geometry_msgs/msg/Pose"](position=P(x=0.8 * t, y=0.0, z=0.5), orientation=q), covariance=np.zeros(36))
            speed = 0.78 if t < FAULT_AT_S else 0.55
            twist = T["geometry_msgs/msg/TwistWithCovariance"](twist=T["geometry_msgs/msg/Twist"](linear=V3(x=speed, y=0.0, z=0.0), angular=V3(x=0.0, y=0.0, z=0.1 * math.sin(t / 7))), covariance=np.zeros(36))
            return T["nav_msgs/msg/Odometry"](header=header(t, "odom"), child_frame_id="base", pose=pose, twist=twist)
        if topic == "/diagnostics":
            S, KV = T["diagnostic_msgs/msg/DiagnosticStatus"], T["diagnostic_msgs/msg/KeyValue"]
            bad = t >= FAULT_AT_S + 1
            level = 2 if bad else 0
            statuses = [S(level=(level if j == "LF_KFE" else 0) if ros == 1 else np.uint8(level if j == "LF_KFE" else 0), name=f"drive/{j}",
                          message=("position tracking error" if bad and j == "LF_KFE" else "OK"), hardware_id=j, values=[KV(key="temp", value="41")]) for j in JOINTS]
            return T["diagnostic_msgs/msg/DiagnosticArray"](header=header(t), status=statuses)
        raise KeyError(topic)
    return store, make


TYPES = {"/joint_states": "sensor_msgs/msg/JointState", "/joint_command": "sensor_msgs/msg/JointState", "/cmd_vel": "geometry_msgs/msg/Twist",
         "/odom": "nav_msgs/msg/Odometry", "/diagnostics": "diagnostic_msgs/msg/DiagnosticArray"}


def write_ros1(path: Path) -> Path:
    from rosbags.rosbag1 import Writer
    store, make = _messages(None, 1)
    path.unlink(missing_ok=True)
    with Writer(path) as writer:
        conns = {topic: writer.add_connection(topic, TYPES[topic], typestore=store) for topic in TYPES}
        for t, topic in _schedule():
            writer.write(conns[topic], START_NS + int(t * 1e9), store.serialize_ros1(make(topic, t), TYPES[topic]))
    return path


def write_ros2_zip(path: Path) -> Path:
    from rosbags.rosbag2 import Writer
    store, make = _messages(None, 2)
    folder = path.with_suffix("")
    shutil.rmtree(folder, ignore_errors=True)
    with Writer(folder, version=8) as writer:
        conns = {topic: writer.add_connection(topic, TYPES[topic], typestore=store) for topic in TYPES}
        for t, topic in _schedule():
            writer.write(conns[topic], START_NS + int(t * 1e9), store.serialize_cdr(make(topic, t), TYPES[topic]))
    path.unlink(missing_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(folder.rglob("*")):
            zf.write(f, Path(folder.name) / f.relative_to(folder))
    shutil.rmtree(folder)
    return path


def write_mcap(path: Path) -> Path:
    """Plain MCAP file with ROS 2 (CDR) messages, as `ros2 bag record -s mcap` or Foxglove produce."""
    from mcap.writer import Writer
    from rosbags.typesys.msg import get_types_from_msg  # noqa: F401  (ensures rosbags is present)
    store, make = _messages(None, 2)
    path.unlink(missing_ok=True)
    with open(path, "wb") as handle:
        writer = Writer(handle)
        writer.start(profile="ros2", library="veyra synthetic")
        channels = {}
        for topic, msgtype in TYPES.items():
            text, _ = store.generate_msgdef(msgtype, ros_version=2)
            schema = writer.register_schema(name=msgtype, encoding="ros2msg", data=text.encode())
            channels[topic] = writer.register_channel(topic=topic, message_encoding="cdr", schema_id=schema)
        for seq, (t, topic) in enumerate(_schedule()):
            ns = START_NS + int(t * 1e9)
            writer.add_message(channel_id=channels[topic], log_time=ns, publish_time=ns, sequence=seq,
                               data=bytes(store.serialize_cdr(make(topic, t), TYPES[topic])))
        writer.finish()
    return path


def write_all(out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    return {"ros1": write_ros1(out / "synthetic_quadruped.bag"), "ros2": write_ros2_zip(out / "synthetic_quadruped_ros2.zip"),
            "mcap": write_mcap(out / "synthetic_quadruped.mcap")}


if __name__ == "__main__":
    for k, v in write_all(Path(sys.argv[1] if len(sys.argv) > 1 else ".")).items():
        print(k, v, v.stat().st_size)
