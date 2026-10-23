"""Robot recordings in their own formats: ROS 1 bags (.bag), ROS 2 bags (a folder with metadata.yaml and .db3 or
.mcap files, uploaded as .zip) and MCAP files (.mcap, ROS 1 / ROS 2 / JSON messages).

No conversion script per robot. A recording is:
1. INSPECTED: topics, message types, counts, rates, and the numeric / text fields of a sample message;
2. MAPPED: a mapping says which topic fields are which Veyra signals. `suggest()` proposes one from standard ROS
   message types (JointState, Twist, Odometry, Imu, DiagnosticArray); a person reviews and edits it;
3. EXTRACTED to Veyra's canonical rows (decimated per signal to `max_hz`; events kept on change), then imported
   through the ordinary validated import pipeline, so units, ranges, clocks and fingerprints work as for any file.

Readers are open-source: `rosbags` (Apache-2.0) for ROS 1 / ROS 2 bags, `mcap` with `mcap-ros1-support` /
`mcap-ros2-support` (MIT) for MCAP files. They are imported lazily so the rest of Veyra runs without them.

Mapping format (JSON):
{
  "mapping_id": "anymal-d", "max_hz": 20, "clock_domain": "robot_clock",
  "topics": [
    {"topic": "/joint_states", "time": "receive" | "header",
     "entries": [
       {"kind": "series", "field": "position", "scope_from": "name", "signal": "joint.position", "unit": "rad"},
       {"kind": "series", "field": "twist.linear.x", "scope": "x", "signal": "vehicle.velocity", "unit": "m/s"},
       {"kind": "attitude", "field": "pose.pose.orientation", "signal": "vehicle.attitude"},
       {"kind": "event", "field": "mode", "scope": "controller", "signal": "controller.event"},
       {"kind": "diagnostics", "signal": "controller.event"},
       {"kind": "arrivals", "signal": "app.heartbeat", "scope": "/joint_states"}
     ]}
  ]
}
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

MAPPING_VERSION = "recording-mapping-v1"
DEFAULT_MAX_HZ = 20.0
MAX_TOPICS = 400
SAMPLE_FIELDS = 60
FORMATS = ("ros1_bag", "ros2_bag", "mcap")


class RecordingError(ValueError):
    pass


# ---------- opening recordings ----------
@dataclass
class Message:
    topic: str
    msgtype: str
    t_ns: int                 # receive / log time
    data: Any                 # decoded message (object with attributes, or dict for JSON)


@dataclass
class Recording:
    path: Path
    format: str
    topics: Dict[str, Dict[str, Any]] = field(default_factory=dict)   # topic -> {type, count}
    start_ns: Optional[int] = None
    end_ns: Optional[int] = None
    _cleanup: Optional[tempfile.TemporaryDirectory] = None

    def close(self):
        if self._cleanup:
            self._cleanup.cleanup()
            self._cleanup = None


def detect_format(path: Path) -> str:
    name = path.name.lower()
    with open(path, "rb") as handle:
        head = handle.read(16)
    if head.startswith(b"#ROSBAG V2.0"):
        return "ros1_bag"
    if head.startswith(b"\x89MCAP0\r\n"):
        return "mcap"
    if head.startswith(b"PK\x03\x04") or name.endswith(".zip"):
        return "ros2_bag"
    if name.endswith(".bag"):
        raise RecordingError("This .bag file is not a ROS 1 bag version 2.0 (older bag versions are not supported)")
    raise RecordingError("Not a recognised recording: expected a ROS 1 bag (.bag), an MCAP file (.mcap) or a ROS 2 bag folder as .zip")


def _need(module: str):
    try:
        return __import__(module, fromlist=["_"])
    except ImportError:
        raise RecordingError(f"Reading this recording needs the open-source package '{module.split('.')[0]}' "
                             "(pip install rosbags mcap mcap-ros1-support mcap-ros2-support zstandard lz4)") from None


def _safe_extract(archive: Path, into: Path) -> Path:
    """Extract a ROS 2 bag .zip safely (no absolute paths, no '..') and return the folder holding metadata.yaml."""
    with zipfile.ZipFile(archive) as zf:
        total = 0
        for info in zf.infolist():
            target = (into / info.filename).resolve()
            if not str(target).startswith(str(into.resolve()) + os.sep) and target != into.resolve():
                raise RecordingError("The .zip contains a path outside the archive")
            total += info.file_size
            if total > 50 * 1024 ** 3:
                raise RecordingError("The .zip expands to more than 50 GB")
        zf.extractall(into)
    found = sorted(into.rglob("metadata.yaml"), key=lambda p: len(p.parts))
    if not found:
        raise RecordingError("The .zip has no metadata.yaml: zip the whole ROS 2 bag folder")
    return found[0].parent


def open_recording(path: Path) -> Recording:
    path = Path(path)
    fmt = detect_format(path)
    rec = Recording(path=path, format=fmt)
    if fmt == "mcap":
        reader_mod = _need("mcap.reader")
        with open(path, "rb") as handle:
            reader = reader_mod.make_reader(handle)
            summary = reader.get_summary()
            if summary is None:
                raise RecordingError("This MCAP file has no summary section (it may be truncated); re-save it with 'mcap recover'")
            counts = (summary.statistics.channel_message_counts if summary.statistics else {}) or {}
            for cid, channel in summary.channels.items():
                schema = summary.schemas.get(channel.schema_id)
                rec.topics[channel.topic] = {"type": schema.name if schema else "(no schema)", "encoding": channel.message_encoding,
                                             "count": int(counts.get(cid, 0))}
            if summary.statistics:
                rec.start_ns, rec.end_ns = int(summary.statistics.message_start_time), int(summary.statistics.message_end_time)
        return rec
    anyreader = _need("rosbags.highlevel")
    bag_path = path
    if fmt == "ros2_bag":
        rec._cleanup = tempfile.TemporaryDirectory(prefix="veyra-ros2-")
        bag_path = _safe_extract(path, Path(rec._cleanup.name))
        rec.path = bag_path
    try:
        with anyreader.AnyReader([bag_path], default_typestore=_typestore(fmt)) as reader:
            for c in reader.connections:
                item = rec.topics.setdefault(c.topic, {"type": c.msgtype, "encoding": "ros1" if fmt == "ros1_bag" else "cdr", "count": 0})
                item["count"] += int(c.msgcount)
            rec.start_ns, rec.end_ns = int(reader.start_time), int(reader.end_time)
    except Exception as error:  # rosbags raises its own error types
        rec.close()
        raise RecordingError(f"Could not read the bag: {error}") from None
    return rec


def _typestore(fmt: str):
    typesys = _need("rosbags.typesys")
    return typesys.get_typestore(typesys.Stores.ROS1_NOETIC if fmt == "ros1_bag" else typesys.Stores.ROS2_HUMBLE)


def iter_messages(rec: Recording, topics: Sequence[str], keep: Optional[Callable[[str, int], bool]] = None) -> Iterator[Message]:
    """Messages of the given topics in time order. keep(topic, t_ns) can skip a message BEFORE it is decoded."""
    wanted = set(topics)
    if rec.format == "mcap":
        reader_mod = _need("mcap.reader")
        factories = []
        for mod, cls in (("mcap_ros2.decoder", "DecoderFactory"), ("mcap_ros1.decoder", "DecoderFactory")):
            try:
                factories.append(getattr(__import__(mod, fromlist=[cls]), cls)())
            except ImportError:
                pass
        with open(rec.path, "rb") as handle:
            reader = reader_mod.make_reader(handle, decoder_factories=factories)
            for schema, channel, message in reader.iter_messages(topics=list(wanted), log_time_order=True):
                t = int(message.log_time)
                if keep and not keep(channel.topic, t):
                    continue
                yield Message(channel.topic, schema.name if schema else "", t, _decode_mcap(reader, schema, channel, message))
        return
    anyreader = _need("rosbags.highlevel")
    with anyreader.AnyReader([rec.path], default_typestore=_typestore(rec.format)) as reader:
        conns = [c for c in reader.connections if c.topic in wanted]
        for conn, t, raw in reader.messages(connections=conns):
            if keep and not keep(conn.topic, int(t)):
                continue
            yield Message(conn.topic, conn.msgtype, int(t), reader.deserialize(raw, conn.msgtype))


_decoders: Dict[Tuple[int, int], Any] = {}


def _decode_mcap(reader, schema, channel, message):
    if channel.message_encoding == "json":
        return json.loads(message.data)
    key = (id(reader), channel.id)
    decoder = _decoders.get(key)
    if decoder is None:
        for factory in reader._decoder_factories:  # the mcap reader's own lookup, cached per channel
            decoder = factory.decoder_for(channel.message_encoding, schema)
            if decoder:
                break
        if decoder is None:
            raise RecordingError(f"No decoder for {channel.topic} ({channel.message_encoding}, schema {schema.encoding if schema else 'none'})")
        _decoders[key] = decoder
    return decoder(message.data)


# ---------- reading fields ----------
_PART = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d*)\])?$")


def _attr(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def get_field(obj: Any, path: str) -> Any:
    """Dotted path with optional list index: 'pose.pose.orientation', 'position', 'status[0].level'."""
    cur = obj
    for part in path.split("."):
        m = _PART.match(part)
        if not m:
            raise RecordingError(f"Field path '{path}' is not valid")
        cur = _attr(cur, m.group(1))
        if cur is None:
            return None
        if m.group(2) not in (None, ""):
            seq = _seq(cur)
            i = int(m.group(2))
            cur = seq[i] if seq is not None and i < len(seq) else None
    return cur


def _seq(v: Any) -> Optional[list]:
    if isinstance(v, (list, tuple)):
        return list(v)
    if hasattr(v, "tolist") and hasattr(v, "shape"):
        return v.tolist()
    return None


def _number(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        f = float(v)
        return f if math.isfinite(f) else None
    if hasattr(v, "item") and getattr(v, "shape", ()) == ():  # numpy scalar
        try:
            return _number(v.item())
        except (TypeError, ValueError):
            return None
    return None


def _fields_of(obj: Any) -> List[str]:
    if isinstance(obj, dict):
        return list(obj.keys())
    if hasattr(obj, "__dataclass_fields__"):
        return [f for f in obj.__dataclass_fields__ if not f.startswith("_")]
    slots = getattr(type(obj), "__slots__", None)
    if slots:
        return [s for s in slots if not s.startswith("_")]
    return [k for k in getattr(obj, "__dict__", {}) if not k.startswith("_")]


def describe_fields(obj: Any, prefix: str = "", depth: int = 0, out: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Numeric, text and array fields of one sample message, for choosing a mapping."""
    out = [] if out is None else out
    if depth > 6 or len(out) >= SAMPLE_FIELDS:
        return out
    for name in _fields_of(obj):
        value = _attr(obj, name)
        path = f"{prefix}{name}"
        num = _number(value)
        if num is not None:
            out.append({"path": path, "kind": "number", "sample": round(num, 6)})
        elif isinstance(value, str):
            out.append({"path": path, "kind": "text", "sample": value[:60]})
        else:
            seq = _seq(value)
            if seq is not None:
                if seq and all(_number(x) is not None for x in seq[:8]):
                    out.append({"path": path, "kind": "numbers", "length": len(seq), "sample": [round(float(_number(x)), 6) for x in seq[:6]]})
                elif seq and all(isinstance(x, str) for x in seq[:8]):
                    out.append({"path": path, "kind": "texts", "length": len(seq), "sample": seq[:6]})
                elif seq:
                    out.append({"path": path, "kind": "list", "length": len(seq)})
                    describe_fields(seq[0], f"{path}[0].", depth + 1, out)
            elif value is not None and not isinstance(value, (bytes, bytearray)):
                describe_fields(value, f"{path}.", depth + 1, out)
        if len(out) >= SAMPLE_FIELDS:
            break
    return out


# ---------- inspection and suggested mapping ----------
def inspect(path: Path) -> Dict[str, Any]:
    rec = open_recording(path)
    try:
        topics = sorted(rec.topics.items())[:MAX_TOPICS]
        first: Dict[str, Message] = {}
        if topics:
            want = {t for t, _ in topics}
            # One decoded sample per topic: stop as soon as every topic has one (or after a bounded number of messages).
            seen = 0
            for msg in iter_messages(rec, list(want), keep=lambda topic, t: topic not in first):
                first.setdefault(msg.topic, msg)
                seen += 1
                if len(first) == len(want) or seen > 20000:
                    break
        duration = (rec.end_ns - rec.start_ns) / 1e9 if rec.start_ns is not None and rec.end_ns is not None else None
        out_topics = []
        for topic, info in topics:
            sample = first.get(topic)
            rate = round(info["count"] / duration, 2) if duration and duration > 0 else None
            out_topics.append({"topic": topic, "type": info["type"], "encoding": info.get("encoding"), "count": info["count"], "rate_hz": rate,
                               "fields": describe_fields(sample.data) if sample else [],
                               "has_header_stamp": bool(sample is not None and get_field(sample.data, "header.stamp") is not None)})
        return {"format": rec.format, "start_ns": rec.start_ns, "end_ns": rec.end_ns, "duration_s": duration, "topics": out_topics,
                "message_count": sum(i["count"] for _, i in topics)}
    finally:
        rec.close()


_CMD = re.compile(r"(cmd|command|target|desired|setpoint|reference|goal)", re.I)


def _short(msgtype: str) -> str:
    """'sensor_msgs/msg/JointState' and 'sensor_msgs/JointState' -> 'sensor_msgs/JointState'."""
    return msgtype.replace("/msg/", "/")


def suggest(inspection: Dict[str, Any]) -> Dict[str, Any]:
    """A first mapping from standard ROS message types. A person reviews it; nothing unknown is guessed."""
    topics, notes = [], []
    has_odom = any(_short(t["type"]) == "nav_msgs/Odometry" for t in inspection["topics"])
    for t in inspection["topics"]:
        kind, name = _short(t["type"]), t["topic"]
        commanded = bool(_CMD.search(name))
        entries: List[Dict[str, Any]] = []
        if kind == "sensor_msgs/JointState":
            if commanded:
                entries.append({"kind": "series", "field": "position", "scope_from": "name", "signal": "joint.position_command", "unit": "rad"})
            else:
                entries.append({"kind": "series", "field": "position", "scope_from": "name", "signal": "joint.position", "unit": "rad"})
                entries.append({"kind": "series", "field": "effort", "scope_from": "name", "signal": "joint.torque", "unit": "Nm"})
        elif kind in ("geometry_msgs/Twist", "geometry_msgs/TwistStamped"):
            base = "twist." if kind.endswith("Stamped") else ""
            if commanded:
                entries += [{"kind": "series", "field": f"{base}linear.x", "scope": "x", "signal": "control.velocity_setpoint", "unit": "m/s"},
                            {"kind": "series", "field": f"{base}linear.y", "scope": "y", "signal": "control.velocity_setpoint", "unit": "m/s"},
                            {"kind": "series", "field": f"{base}angular.z", "scope": "yaw", "signal": "control.yaw_rate_setpoint", "unit": "rad/s"}]
            else:
                entries += [{"kind": "series", "field": f"{base}linear.x", "scope": "x", "signal": "vehicle.velocity", "unit": "m/s"},
                            {"kind": "series", "field": f"{base}linear.y", "scope": "y", "signal": "vehicle.velocity", "unit": "m/s"},
                            {"kind": "series", "field": f"{base}angular.z", "scope": "yaw", "signal": "vehicle.yaw_rate", "unit": "rad/s"}]
        elif kind == "nav_msgs/Odometry":
            entries += [{"kind": "series", "field": "twist.twist.linear.x", "scope": "x", "signal": "vehicle.velocity", "unit": "m/s"},
                        {"kind": "series", "field": "twist.twist.linear.y", "scope": "y", "signal": "vehicle.velocity", "unit": "m/s"},
                        {"kind": "series", "field": "twist.twist.angular.z", "scope": "yaw", "signal": "vehicle.yaw_rate", "unit": "rad/s"},
                        {"kind": "attitude", "field": "pose.pose.orientation", "signal": "vehicle.attitude"}]
        elif kind == "sensor_msgs/Imu" and not has_odom:
            entries.append({"kind": "attitude", "field": "orientation", "signal": "vehicle.attitude"})
        elif kind == "diagnostic_msgs/DiagnosticArray":
            names = " ".join(str(f.get("sample", "")) for f in t["fields"] if f["path"].endswith(".name"))
            drive = bool(re.search(r"(drive|motor|joint|actuator|servo|esc)", names, re.I))
            entries.append({"kind": "diagnostics", "signal": "actuator.status" if drive else "controller.event"})
        if entries:
            topics.append({"topic": name, "time": "receive", "entries": entries, "why": f"standard message type {kind}" + (" on a command topic" if commanded else "")})
        elif t["count"]:
            notes.append(f"{name} ({kind}) not mapped: not a standard type Veyra knows; map its fields by hand if it matters")
    return {"mapping_id": "suggested", "version": MAPPING_VERSION, "max_hz": DEFAULT_MAX_HZ, "clock_domain": "robot_clock", "topics": topics,
            "not_mapped": notes}


# ---------- validating a mapping ----------
KINDS = ("series", "attitude", "event", "diagnostics", "arrivals")


def validate_mapping(mapping: Dict[str, Any], known_signals: Iterable[str], topics: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    if not isinstance(mapping, dict) or not isinstance(mapping.get("topics"), list) or not mapping["topics"]:
        raise RecordingError("The mapping needs a non-empty 'topics' list")
    known, present = set(known_signals), set(topics) if topics is not None else None
    hz = float(mapping.get("max_hz") or DEFAULT_MAX_HZ)
    if not 0.1 <= hz <= 1000:
        raise RecordingError("max_hz must be between 0.1 and 1000")
    clean_topics = []
    for t in mapping["topics"]:
        name = t.get("topic")
        if not isinstance(name, str) or not name:
            raise RecordingError("Every mapped topic needs a 'topic' name")
        if present is not None and name not in present:
            raise RecordingError(f"Topic {name} is not in this recording")
        if t.get("time", "receive") not in ("receive", "header"):
            raise RecordingError(f"{name}: time must be 'receive' or 'header'")
        entries = []
        for e in t.get("entries") or []:
            kind = e.get("kind")
            if kind not in KINDS:
                raise RecordingError(f"{name}: entry kind must be one of {', '.join(KINDS)}")
            if e.get("signal") not in known:
                raise RecordingError(f"{name}: '{e.get('signal')}' is not a Veyra signal")
            if kind in ("series", "attitude", "event") and not isinstance(e.get("field"), str):
                raise RecordingError(f"{name}: a {kind} entry needs a 'field'")
            if e.get("field"):
                for part in e["field"].split("."):
                    if not _PART.match(part):
                        raise RecordingError(f"{name}: field path '{e['field']}' is not valid")
            entries.append({k: v for k, v in e.items() if k in ("kind", "field", "signal", "unit", "scope", "scope_from", "scopes", "scale")})
        if entries:
            clean_topics.append({"topic": name, "time": t.get("time", "receive"), "entries": entries})
    if not clean_topics:
        raise RecordingError("The mapping maps nothing")
    return {"mapping_id": str(mapping.get("mapping_id") or "custom")[:80], "version": MAPPING_VERSION, "max_hz": hz,
            "clock_domain": str(mapping.get("clock_domain") or "robot_clock")[:60], "topics": clean_topics}


# ---------- extraction ----------
def quat_to_euler_deg(x: float, y: float, z: float, w: float) -> Tuple[float, float, float]:
    roll = math.degrees(math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y)))
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x)))))
    yaw = math.degrees(math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)))
    return roll, pitch, yaw


def _stamp_ns(msg: Any) -> Optional[int]:
    stamp = get_field(msg, "header.stamp")
    if stamp is None:
        return None
    sec = _number(_attr(stamp, "sec") if _attr(stamp, "sec") is not None else _attr(stamp, "secs"))
    nsec = _attr(stamp, "nanosec")
    nsec = _number(nsec if nsec is not None else _attr(stamp, "nsec") if _attr(stamp, "nsec") is not None else _attr(stamp, "nsecs"))
    if sec is None:
        return None
    return int(sec) * 1_000_000_000 + int(nsec or 0)


DIAG_LEVEL = {0: "OK", 1: "WARN", 2: "ERROR", 3: "STALE"}


@dataclass
class Extraction:
    rows: List[Dict[str, Any]]
    counts: Dict[str, int]
    skipped: Dict[str, int]
    start_ns: Optional[int]
    end_ns: Optional[int]


def extract(path: Path, mapping: Dict[str, Any], *, asset: str, progress: Optional[Callable[[int], None]] = None) -> Extraction:
    """Canonical rows {t (ns), asset, signal, value, unit, scope, event} from a validated mapping."""
    rec = open_recording(path)
    try:
        period_ns = int(1e9 / float(mapping["max_hz"]))
        by_topic = {t["topic"]: t for t in mapping["topics"]}
        # A topic whose entries are all decimated series can skip messages before decoding them.
        decimate_topic = {name: all(e["kind"] in ("series", "attitude", "arrivals") for e in t["entries"]) and t["time"] == "receive"
                          for name, t in by_topic.items()}
        last_topic: Dict[str, int] = {}

        def keep(topic: str, t: int) -> bool:
            if not decimate_topic.get(topic):
                return True
            prev = last_topic.get(topic)
            if prev is not None and t - prev < period_ns:
                return False
            last_topic[topic] = t
            return True

        rows: List[Dict[str, Any]] = []
        counts: Dict[str, int] = {}
        skipped: Dict[str, int] = {}
        last_series: Dict[Tuple[str, str], int] = {}
        last_event: Dict[Tuple[str, str], str] = {}
        first_t = last_t = None
        n = 0

        def add(t, signal, value, unit, scope, event=None):
            rows.append({"t": t, "asset": asset, "signal": signal, "value": value, "unit": unit, "scope": scope, "event": event})
            counts[signal] = counts.get(signal, 0) + 1

        def series(t, signal, scope, value, unit, scale):
            num = _number(value)
            if num is None:
                skipped[signal] = skipped.get(signal, 0) + 1
                return
            key = (signal, scope or "")
            prev = last_series.get(key)
            if prev is not None and t - prev < period_ns:
                return
            last_series[key] = t
            add(t, signal, num * scale, unit, scope)

        for msg in iter_messages(rec, list(by_topic), keep=keep):
            n += 1
            if progress and n % 20000 == 0:
                progress(n)
            spec = by_topic[msg.topic]
            t = msg.t_ns
            if spec["time"] == "header":
                t = _stamp_ns(msg.data) or t
            first_t = t if first_t is None else min(first_t, t)
            last_t = t if last_t is None else max(last_t, t)
            for e in spec["entries"]:
                kind, signal, scale = e["kind"], e["signal"], float(e.get("scale") or 1.0)
                if kind == "series":
                    value = get_field(msg.data, e["field"])
                    seq = _seq(value)
                    if seq is None:
                        series(t, signal, e.get("scope"), value, e.get("unit"), scale)
                    else:
                        names = _seq(get_field(msg.data, e["scope_from"])) if e.get("scope_from") else e.get("scopes")
                        for i, v in enumerate(seq):
                            scope = str(names[i]) if names and i < len(names) else f"{e.get('scope') or 'index'}[{i}]"
                            series(t, signal, scope, v, e.get("unit"), scale)
                elif kind == "attitude":
                    q = get_field(msg.data, e["field"])
                    parts = [_number(_attr(q, k)) for k in ("x", "y", "z", "w")] if q is not None else [None]
                    if None in parts or abs(sum(p * p for p in parts) - 1.0) > 0.1:
                        skipped[signal] = skipped.get(signal, 0) + 1
                        continue
                    for scope, v in zip(("roll", "pitch", "yaw"), quat_to_euler_deg(*parts)):
                        series(t, signal, scope, v, "deg", 1.0)
                elif kind == "event":
                    value = get_field(msg.data, e["field"])
                    seq = _seq(value)
                    names = _seq(get_field(msg.data, e["scope_from"])) if e.get("scope_from") else None
                    items = list(enumerate(seq)) if seq is not None else [(None, value)]
                    for i, v in items:
                        scope = str(names[i]) if names and i is not None and i < len(names) else (e.get("scope") or msg.topic)
                        text = str(v) if not isinstance(v, float) else f"{v:g}"
                        key = (signal, f"{msg.topic}|{scope}")
                        if last_event.get(key) == text:
                            continue
                        previous = last_event.get(key)
                        last_event[key] = text
                        if previous is not None:  # the value at the start of the recording is a state, not an event
                            add(t, signal, None, None, scope, f"{scope} {e['field']} {previous} -> {text}")
                elif kind == "diagnostics":
                    for status in _seq(_attr(msg.data, "status")) or []:
                        name = str(_attr(status, "name") or "diagnostic")
                        level = _attr(status, "level")
                        level = int(level[0] if isinstance(level, (bytes, bytearray)) else (_number(level) or 0))
                        text = f"{DIAG_LEVEL.get(level, level)}: {str(_attr(status, 'message') or '')[:200]}"
                        key = (signal, f"diag|{name}")
                        previous = last_event.get(key)
                        if previous == text:
                            continue
                        last_event[key] = text
                        if previous is not None or level >= 1:
                            add(t, signal, None, None, name[:120], f"{name}: {text}")
                elif kind == "arrivals":
                    key = (signal, e.get("scope") or msg.topic)
                    prev = last_series.get(key)
                    if prev is None or t - prev >= period_ns:
                        last_series[key] = t
                        add(t, signal, None, None, e.get("scope") or msg.topic)
        rows.sort(key=lambda r: (r["t"], r["signal"], r["scope"] or ""))
        return Extraction(rows=rows, counts=counts, skipped=skipped, start_ns=first_t, end_ns=last_t)
    finally:
        rec.close()


# ---------- into the ordinary import pipeline ----------
def import_profile(mapping: Dict[str, Any], source_name: str, source_sha256: str) -> Dict[str, Any]:
    used = sorted({e["signal"] for t in mapping["topics"] for e in t["entries"]})
    return {"profile_id": f"recording-{mapping['mapping_id']}"[:80], "version": 1, "format": "jsonl", "synthetic": False,
            "columns": {"timestamp": "t", "asset": "asset", "signal": "signal", "value": "value", "unit": "unit", "scope": "scope", "event_value": "event"},
            "signal_map": {s: s for s in used}, "clock_domain": mapping["clock_domain"],
            "timestamp_format": "epoch", "epoch_unit": "ns", "unmapped_signals": "retain",
            "notes": f"Extracted from the recording {source_name} (sha256 {source_sha256}) with mapping {mapping['mapping_id']} "
                     f"({MAPPING_VERSION}, at most {mapping['max_hz']:g} Hz per signal)."}


def to_files(rows: List[Dict[str, Any]], stem: str, limit_bytes: int = 20_000_000) -> List[Tuple[str, bytes]]:
    """Canonical JSONL in parts under the import size limit, split by signal so each part stays coherent."""
    by_signal: Dict[str, List[str]] = {}
    for r in rows:
        by_signal.setdefault(r["signal"], []).append(json.dumps({k: v for k, v in r.items() if v is not None}, separators=(",", ":")))
    files, part, size, index = [], [], 0, 1
    for signal in sorted(by_signal):
        for line in by_signal[signal]:
            if size + len(line) + 1 > limit_bytes and part:
                files.append((f"{stem}_part{index}.jsonl", ("\n".join(part) + "\n").encode()))
                part, size, index = [], 0, index + 1
            part.append(line)
            size += len(line) + 1
    if part:
        files.append((f"{stem}_part{index}.jsonl" if index > 1 or files else f"{stem}.jsonl", ("\n".join(part) + "\n").encode()))
    return files


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
