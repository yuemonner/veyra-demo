"""Robot log connector: ROS 1 bags and MCAP recordings, standard library only.

Supported:
  ROS 1 bag v2.0: uncompressed and bz2 chunks; messages decoded from their embedded definitions
                  (primitives, strings, time/duration, fixed and variable arrays, nested types).
  MCAP:           uncompressed chunks or unchunked files; message encodings `json` and `ros1`
                  (schema encoding `ros1msg`). zstd/lz4 chunks and ROS 2 CDR are reported as
                  unsupported; decompress first (`mcap decompress`) or export to JSON.

Configured topic fields are converted to canonical rows and imported through an explicit,
generated profile. The original log's SHA-256 is kept in the import notes; the converted JSON Lines
file is what the store hashes and keeps.

Configuration:
  {"kind": "robot_logs", "path": "/logs", "globs": ["*.bag", "*.mcap"], "clock_domain": "robot_clock",
   "time_source": "log_time" | "header", "max_clock_skew_s": null,
   "signals": [{"topic": "/joint_states", "field": "velocity[boom_joint]", "signal": "vehicle.actuator_response",
                "unit": "rad/s", "scope": "boom"},
               {"topic": "/heartbeat", "signal": "app.heartbeat"}],
   "source_metadata": {...}, "synthetic": false}
Field paths: dotted (`twist.linear.x`), array index (`position[2]`), or by name for messages that carry a
`name` array alongside value arrays (sensor_msgs/JointState: `velocity[boom_joint]`).
"""
from __future__ import annotations

import bz2
import fnmatch
import hashlib
import io
import json
import re
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from app.connectors.base import Batch, Connector, ConnectorError, Window
from app.ingest import catalog

BAG_MAGIC = b"#ROSBAG V2.0\n"
MCAP_MAGIC = b"\x89MCAP0\r\n"
MAX_LOG_BYTES = 2_000_000_000
MAX_ROWS = 190_000

# ---------- ROS 1 message definitions ----------
_PRIMITIVES = {
    "bool": ("<B", 1), "int8": ("<b", 1), "byte": ("<b", 1), "uint8": ("<B", 1), "char": ("<B", 1),
    "int16": ("<h", 2), "uint16": ("<H", 2), "int32": ("<i", 4), "uint32": ("<I", 4),
    "int64": ("<q", 8), "uint64": ("<Q", 8), "float32": ("<f", 4), "float64": ("<d", 8),
}
_FIELD = re.compile(r"^\s*([A-Za-z0-9_/]+)(\[(\d*)\])?\s+([A-Za-z_][A-Za-z0-9_]*)\s*(=.*)?$")


def parse_definitions(root_type: str, text: str) -> Dict[str, List[Tuple[str, Optional[int], str]]]:
    """Return {type: [(field_type, array_len or -1 for variable or None, name)]} for the root and nested types."""
    sections = re.split(r"^=+\s*$", text, flags=re.MULTILINE)
    types: Dict[str, List[Tuple[str, Optional[int], str]]] = {}
    for index, section in enumerate(sections):
        lines = section.strip("\n").splitlines()
        name = root_type
        if index > 0:
            header = next((l for l in lines if l.startswith("MSG:")), None)
            if header is None:
                continue
            name = header.split(":", 1)[1].strip()
            lines = [l for l in lines if not l.startswith("MSG:")]
        package = name.split("/")[0] if "/" in name else ""
        fields = []
        for raw in lines:
            line = raw.split("#", 1)[0].rstrip()
            if not line.strip():
                continue
            match = _FIELD.match(line)
            if not match:
                raise ConnectorError(f"Cannot parse message definition line: {raw.strip()[:80]}")
            ftype, is_array, size, fname, constant = match.groups()
            if constant:
                continue  # constants carry no bytes
            if ftype == "Header":
                ftype = "std_msgs/Header"
            elif ftype not in _PRIMITIVES and ftype not in ("string", "time", "duration") and "/" not in ftype:
                ftype = f"{package}/{ftype}" if package else ftype
            length = None if not is_array else (int(size) if size else -1)
            fields.append((ftype, length, fname))
        types[name] = fields
    return types


class _Reader:
    def __init__(self, data: bytes):
        self.data, self.pos = data, 0

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise ConnectorError("Message shorter than its definition")
        chunk = self.data[self.pos:self.pos + n]
        self.pos += n
        return chunk

    def unpack(self, fmt: str, size: int):
        return struct.unpack(fmt, self.take(size))[0]


def _decode_value(reader: _Reader, ftype: str, types: Dict[str, Any]) -> Any:
    if ftype in _PRIMITIVES:
        fmt, size = _PRIMITIVES[ftype]
        value = reader.unpack(fmt, size)
        return bool(value) if ftype == "bool" else value
    if ftype == "string":
        return reader.take(reader.unpack("<I", 4)).decode("utf-8", errors="replace")
    if ftype in ("time", "duration"):
        secs, nsecs = struct.unpack("<II" if ftype == "time" else "<ii", reader.take(8))
        return secs + nsecs / 1e9
    if ftype not in types:
        raise ConnectorError(f"Message definition for {ftype} is missing")
    return {name: _decode_field(reader, sub, length, types) for sub, length, name in types[ftype]}


def _decode_field(reader: _Reader, ftype: str, length: Optional[int], types: Dict[str, Any]) -> Any:
    if length is None:
        return _decode_value(reader, ftype, types)
    count = reader.unpack("<I", 4) if length == -1 else length
    if ftype in ("uint8", "char") and count > 64:
        return list(reader.take(count))
    return [_decode_value(reader, ftype, types) for _ in range(count)]


def decode_ros1(data: bytes, root_type: str, types: Dict[str, Any]) -> Dict[str, Any]:
    reader = _Reader(data)
    return {name: _decode_field(reader, ftype, length, types) for ftype, length, name in types[root_type]}


def field_value(message: Any, path: str) -> Any:
    """Resolve `a.b`, `a[3]` and `velocity[joint_name]` (by the message's `name` array)."""
    current = message
    for part in re.findall(r"[^.\[\]]+|\[[^\]]*\]", path):
        if part.startswith("["):
            key = part[1:-1]
            if not isinstance(current, list):
                return None
            if key.isdigit():
                index = int(key)
            else:
                names = message.get("name") if isinstance(message, dict) else None
                if not isinstance(names, list) or key not in names:
                    return None
                index = names.index(key)
            if index >= len(current):
                return None
            current = current[index]
        else:
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
    return current


# ---------- containers ----------
def _bag_header(blob: bytes) -> Dict[str, bytes]:
    fields, pos = {}, 0
    while pos < len(blob):
        (length,) = struct.unpack_from("<I", blob, pos)
        pos += 4
        field = blob[pos:pos + length]
        pos += length
        name, _, value = field.partition(b"=")
        fields[name.decode()] = value
    return fields


def _bag_records(stream: io.BytesIO) -> Iterator[Tuple[Dict[str, bytes], bytes]]:
    while True:
        raw = stream.read(4)
        if len(raw) < 4:
            return
        (header_len,) = struct.unpack("<I", raw)
        header = _bag_header(stream.read(header_len))
        (data_len,) = struct.unpack("<I", stream.read(4))
        yield header, stream.read(data_len)


def read_bag(content: bytes) -> Iterator[Tuple[str, float, Any]]:
    """Yield (topic, log_time_seconds, decoded message) for every message in a ROS 1 bag."""
    if not content.startswith(BAG_MAGIC):
        raise ConnectorError("Not a ROS 1 bag v2.0")
    connections: Dict[int, Dict[str, Any]] = {}

    def handle(records: Iterator[Tuple[Dict[str, bytes], bytes]]) -> Iterator[Tuple[str, float, Any]]:
        for header, data in records:
            op = header.get("op", b"\x00")[0]
            if op == 0x05:  # chunk
                compression = header.get("compression", b"none").decode()
                if compression == "bz2":
                    data = bz2.decompress(data)
                elif compression != "none":
                    raise ConnectorError(f"Bag chunk compression '{compression}' is not supported; re-save with `rosbag compress --bz2` or decompress first")
                yield from handle(_bag_records(io.BytesIO(data)))
            elif op == 0x07:  # connection
                (conn,) = struct.unpack("<I", header["conn"])
                info = _bag_header(data)
                msg_type = info["type"].decode()
                connections[conn] = {"topic": header["topic"].decode(), "type": msg_type,
                                     "types": parse_definitions(msg_type, info.get("message_definition", b"").decode())}
            elif op == 0x02:  # message data
                (conn,) = struct.unpack("<I", header["conn"])
                secs, nsecs = struct.unpack("<II", header["time"])
                c = connections.get(conn)
                if c is None:
                    continue
                yield c["topic"], secs + nsecs / 1e9, decode_ros1(data, c["type"], c["types"])

    stream = io.BytesIO(content)
    stream.seek(len(BAG_MAGIC))
    yield from handle(_bag_records(stream))


def _mcap_str(buf: bytes, pos: int) -> Tuple[str, int]:
    (length,) = struct.unpack_from("<I", buf, pos)
    return buf[pos + 4:pos + 4 + length].decode("utf-8"), pos + 4 + length


def read_mcap(content: bytes, unsupported: Dict[str, int]) -> Iterator[Tuple[str, float, Any]]:
    """Yield (topic, log_time_seconds, decoded message). Unsupported encodings are counted, not guessed."""
    if not content.startswith(MCAP_MAGIC):
        raise ConnectorError("Not an MCAP file")
    schemas: Dict[int, Dict[str, Any]] = {}
    channels: Dict[int, Dict[str, Any]] = {}

    def records(buf: bytes, pos: int, end: int) -> Iterator[Tuple[str, float, Any]]:
        while pos + 9 <= end:
            op = buf[pos]
            (length,) = struct.unpack_from("<Q", buf, pos + 1)
            body = buf[pos + 9:pos + 9 + length]
            pos += 9 + length
            if op == 0x02:  # footer
                return
            if op == 0x03:  # schema
                (sid,) = struct.unpack_from("<H", body, 0)
                name, p = _mcap_str(body, 2)
                encoding, p = _mcap_str(body, p)
                (dlen,) = struct.unpack_from("<I", body, p)
                schemas[sid] = {"name": name, "encoding": encoding, "data": body[p + 4:p + 4 + dlen]}
            elif op == 0x04:  # channel
                cid, sid = struct.unpack_from("<HH", body, 0)
                topic, p = _mcap_str(body, 4)
                encoding, p = _mcap_str(body, p)
                channels[cid] = {"topic": topic, "encoding": encoding, "schema_id": sid}
            elif op == 0x05:  # message
                cid, _seq, log_time, _pub = struct.unpack_from("<HIQQ", body, 0)
                payload = body[22:]
                channel = channels.get(cid)
                if channel is None:
                    continue
                message = _mcap_decode(channel, schemas.get(channel["schema_id"]), payload, unsupported)
                if message is not None:
                    yield channel["topic"], log_time / 1e9, message
            elif op == 0x06:  # chunk
                _s, _e, _usize, _crc = struct.unpack_from("<QQQI", body, 0)
                compression, p = _mcap_str(body, 28)
                (rlen,) = struct.unpack_from("<Q", body, p)
                inner = body[p + 8:p + 8 + rlen]
                if compression:
                    raise ConnectorError(f"MCAP chunk compression '{compression}' is not supported here; run `mcap decompress` first")
                yield from records(inner, 0, len(inner))

    yield from records(content, len(MCAP_MAGIC), len(content) - len(MCAP_MAGIC))


def _mcap_decode(channel: Dict[str, Any], schema: Optional[Dict[str, Any]], payload: bytes, unsupported: Dict[str, int]) -> Any:
    encoding = channel["encoding"]
    if encoding == "json":
        return json.loads(payload)
    if encoding == "ros1" and schema and schema["encoding"] == "ros1msg":
        if "_types" not in schema:
            schema["_types"] = parse_definitions(schema["name"], schema["data"].decode())
        return decode_ros1(payload, schema["name"], schema["_types"])
    unsupported[encoding] = unsupported.get(encoding, 0) + 1
    return None


# ---------- connector ----------
def _key(index: int, topic: str, field: Optional[str]) -> str:
    text = re.sub(r"[^A-Za-z0-9._:-]+", "_", f"{topic}.{field or 'event'}").strip("_.")
    return f"m{index}.{text}"[:120]


class RobotLogConnector(Connector):
    kind = "robot_logs"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        for key in ("path", "clock_domain", "signals"):
            if not config.get(key):
                raise ConnectorError(f"{self.id}: '{key}' is required")
        self.root = Path(config["path"]).expanduser().resolve()
        self.globs = config.get("globs") or ["*.bag", "*.mcap"]
        self.time_source = config.get("time_source", "log_time")
        if self.time_source not in ("log_time", "header"):
            raise ConnectorError(f"{self.id}: time_source must be log_time or header")
        self.mappings = []
        for index, item in enumerate(config["signals"]):
            spec = catalog.get(item.get("signal", ""))
            if spec.kind == catalog.MEASUREMENT and (not item.get("field") or not item.get("unit")):
                raise ConnectorError(f"{self.id}: {item['signal']} on {item.get('topic')} needs 'field' and 'unit'")
            self.mappings.append({**item, "key": _key(index, item["topic"], item.get("field")), "kind": spec.kind})

    def describe(self) -> Dict[str, Any]:
        return {**super().describe(), "path": str(self.root), "globs": self.globs, "topics": sorted({m["topic"] for m in self.mappings}),
                "clock_domain": self.config["clock_domain"], "time_source": self.time_source}

    def profile(self) -> Dict[str, Any]:
        return {"profile_id": f"connector-{self.id}"[:120], "version": 1, "format": "jsonl", "synthetic": self.synthetic,
                "columns": {"timestamp": "t", "asset": "asset", "signal": "signal", "value": "value", "unit": "unit", "scope": "scope"},
                "signal_map": {m["key"]: m["signal"] for m in self.mappings}, "clock_domain": self.config["clock_domain"],
                "timestamp_format": "iso8601", "max_clock_skew_s": self.config.get("max_clock_skew_s"),
                "source_metadata": self.config.get("source_metadata", {}),
                "notes": f"Generated by robot-log connector {self.id}; time source: {self.time_source}"}

    def convert(self, content: bytes, machine_id: str, window: Optional[Window]) -> Tuple[bytes, Dict[str, Any]]:
        unsupported: Dict[str, int] = {}
        if content.startswith(BAG_MAGIC):
            messages, fmt = read_bag(content), "ROS 1 bag"
        elif content.startswith(MCAP_MAGIC):
            messages, fmt = read_mcap(content, unsupported), "MCAP"
        else:
            raise ConnectorError("Not a ROS 1 bag or MCAP file")
        by_topic: Dict[str, List[Dict[str, Any]]] = {}
        for m in self.mappings:
            by_topic.setdefault(m["topic"], []).append(m)
        lo, hi = window.epoch() if window else (float("-inf"), float("inf"))
        rows, seen, missing = [], 0, 0
        for topic, log_time, message in messages:
            maps = by_topic.get(topic)
            if not maps:
                continue
            seen += 1
            t = log_time
            if self.time_source == "header":
                stamp = field_value(message, "header.stamp")
                if not isinstance(stamp, (int, float)):
                    missing += 1
                    continue
                t = stamp
            if not lo <= t <= hi:
                continue
            iso = datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
            for m in maps:
                row = {"t": iso, "asset": machine_id, "signal": m["key"], "scope": m.get("scope")}
                if m["kind"] == catalog.MEASUREMENT:
                    value = field_value(message, m["field"])
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        missing += 1
                        continue
                    row.update(value=value, unit=m["unit"])
                rows.append(row)
                if len(rows) >= MAX_ROWS:
                    raise ConnectorError(f"More than {MAX_ROWS} rows in the window; narrow the window or the signal list")
        rows.sort(key=lambda r: (r["t"], r["signal"]))
        body = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows).encode()
        return body, {"format": fmt, "messages_on_mapped_topics": seen, "rows": len(rows), "values_missing": missing, "unsupported_encodings": unsupported}

    def fetch(self, machine_id: str, window: Window) -> List[Batch]:
        if not self.root.is_dir():
            raise ConnectorError(f"{self.id}: folder {self.root} does not exist")
        batches = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or path.is_symlink() or self.root not in path.resolve().parents:
                continue
            if not any(fnmatch.fnmatchcase(path.name, g.replace("{machine}", machine_id)) for g in self.globs):
                continue
            if path.stat().st_size > MAX_LOG_BYTES:
                raise ConnectorError(f"{path.name} is larger than {MAX_LOG_BYTES} bytes")
            content = path.read_bytes()
            body, stats = self.convert(content, machine_id, window)
            if not stats["rows"]:
                continue
            note = (f"Converted from {stats['format']} {path.name} (sha256 {hashlib.sha256(content).hexdigest()}): "
                    f"{stats['rows']} rows in window from {stats['messages_on_mapped_topics']} messages on mapped topics")
            notes = [note]
            if stats["unsupported_encodings"]:
                notes.append("Skipped messages with unsupported encodings: " + ", ".join(f"{k} ×{v}" for k, v in stats["unsupported_encodings"].items()))
            if stats["values_missing"]:
                notes.append(f"{stats['values_missing']} mapped values were missing or non-numeric and were not imported")
            batches.append(Batch(filename=f"{path.stem}_{machine_id}.converted.jsonl", content=body, profile=self.profile(),
                                 source=f"robot_log:{path.relative_to(self.root)}", notes=notes))
        return batches


# ---------- minimal writers (tests, synthetic fixtures) ----------
def _bag_field(name: str, value: bytes) -> bytes:
    field = name.encode() + b"=" + value
    return struct.pack("<I", len(field)) + field


def _bag_record(fields: List[Tuple[str, bytes]], data: bytes) -> bytes:
    header = b"".join(_bag_field(n, v) for n, v in fields)
    return struct.pack("<I", len(header)) + header + struct.pack("<I", len(data)) + data


def write_bag(connections: List[Dict[str, Any]], messages: List[Tuple[int, float, bytes]], compression: str = "none") -> bytes:
    """connections: [{conn, topic, type, definition}]; messages: [(conn, time_s, serialized)]."""
    inner = b""
    for c in connections:
        meta = b"".join(_bag_field(n, v) for n, v in (("topic", c["topic"].encode()), ("type", c["type"].encode()),
                                                       ("md5sum", b"*"), ("message_definition", c["definition"].encode())))
        inner += _bag_record([("op", b"\x07"), ("conn", struct.pack("<I", c["conn"])), ("topic", c["topic"].encode())], meta)
    for conn, t, data in messages:
        secs = int(t)
        inner += _bag_record([("op", b"\x02"), ("conn", struct.pack("<I", conn)), ("time", struct.pack("<II", secs, int(round((t - secs) * 1e9))))], data)
    payload = bz2.compress(inner) if compression == "bz2" else inner
    chunk = _bag_record([("op", b"\x05"), ("compression", compression.encode()), ("size", struct.pack("<I", len(inner)))], payload)
    header = _bag_record([("op", b"\x03"), ("index_pos", struct.pack("<Q", 0)), ("conn_count", struct.pack("<I", len(connections))),
                          ("chunk_count", struct.pack("<I", 1))], b" " * 64)
    return BAG_MAGIC + header + chunk


def _mstr(text: str) -> bytes:
    raw = text.encode()
    return struct.pack("<I", len(raw)) + raw


def write_mcap_json(channels: List[Dict[str, Any]], messages: List[Tuple[int, float, Dict[str, Any]]]) -> bytes:
    """Unchunked MCAP with JSON-encoded messages. channels: [{id, topic}]; messages: [(channel_id, time_s, dict)]."""
    def record(op: int, body: bytes) -> bytes:
        return bytes([op]) + struct.pack("<Q", len(body)) + body
    out = MCAP_MAGIC + record(0x01, _mstr("") + _mstr("veyra-test"))
    out += record(0x03, struct.pack("<H", 1) + _mstr("json-object") + _mstr("jsonschema") + struct.pack("<I", 2) + b"{}")
    for c in channels:
        out += record(0x04, struct.pack("<HH", c["id"], 1) + _mstr(c["topic"]) + _mstr("json") + struct.pack("<I", 0))
    for seq, (cid, t, message) in enumerate(messages):
        ns = int(round(t * 1e9))
        out += record(0x05, struct.pack("<HIQQ", cid, seq, ns, ns) + json.dumps(message).encode())
    out += record(0x02, struct.pack("<QQI", 0, 0, 0))
    return out + MCAP_MAGIC
