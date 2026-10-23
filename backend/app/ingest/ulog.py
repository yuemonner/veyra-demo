"""PX4 ULog reader (https://docs.px4.io/main/en/dev_log/ulog_file_format.html), standard library only.

Reads the file's own message formats, then extracts a fixed, documented set of PX4 topics into Veyra's canonical
rows, the same shape bags.extract produces. Nothing is inferred beyond unit conversion:
  vehicle_attitude.q                -> vehicle.attitude (roll/pitch/yaw, deg)
  vehicle_attitude_setpoint.q_d     -> control.attitude_setpoint (roll/pitch/yaw, deg)
  vehicle_angular_velocity.xyz[2]   -> vehicle.yaw_rate (deg/s)
  vehicle_rates_setpoint.yaw        -> control.yaw_rate_setpoint (deg/s)
  vehicle_local_position.z          -> vehicle.altitude scope "estimate" (m, up = -z, relative to the EKF origin)
  vehicle_air_data.baro_alt_meter   -> vehicle.altitude scope "baro" (m)
  vehicle_gps_position / sensor_gps -> vehicle.altitude scope "gps" (m, MSL)
  vehicle_local_position.vx, vy     -> vehicle.ground_speed (m/s)
  actuator_outputs.output[i]        -> actuator.output scope "ch{i+1}" (raw output value; us on PWM outputs)
  vehicle_thrust_setpoint.xyz[2]    -> propulsion.throttle (% of full collective thrust)
  vehicle_status nav_state, arming_state, failsafe changes -> controller.event
  logged text messages (all but debug)   -> controller.event (scope "log")
Time: microseconds since boot, placed on UTC with the first GPS fix's time_utc_usec when there is one; otherwise with
the wall-clock time in the logger's own file name (clock "logger_file_name", companion clock, 1 s resolution); otherwise
left on the boot clock (clock "px4_boot", not wall-clock time).
"""
from __future__ import annotations

import math
import struct
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.ingest.bags import Extraction, quat_to_euler_deg

MAGIC = b"ULog\x01\x12\x35"
VERSION = "px4-ulog-v1"
TYPES = {"int8_t": "b", "uint8_t": "B", "int16_t": "h", "uint16_t": "H", "int32_t": "i", "uint32_t": "I",
         "int64_t": "q", "uint64_t": "Q", "float": "f", "double": "d", "bool": "?", "char": "c"}
SIZES = {k: struct.calcsize("<" + v) for k, v in TYPES.items()}
NAV_STATE = {0: "MANUAL", 1: "ALTCTL", 2: "POSCTL", 3: "AUTO_MISSION", 4: "AUTO_LOITER", 5: "AUTO_RTL", 10: "ACRO", 12: "DESCEND",
             13: "TERMINATION", 14: "OFFBOARD", 15: "STAB", 17: "AUTO_TAKEOFF", 18: "AUTO_LAND", 19: "AUTO_FOLLOW_TARGET",
             20: "AUTO_PRECLAND", 21: "ORBIT", 22: "AUTO_VTOL_TAKEOFF"}
ARMING = {1: "DISARMED", 2: "ARMED"}
LEVELS = {ord("0"): "EMERGENCY", ord("1"): "ALERT", ord("2"): "CRITICAL", ord("3"): "ERROR", ord("4"): "WARNING",
          ord("5"): "NOTICE", ord("6"): "INFO", ord("7"): "DEBUG"}


class ULogError(ValueError):
    pass


def is_ulog(path: Path) -> bool:
    with open(path, "rb") as f:
        return f.read(7) == MAGIC


class _Layout:
    """Byte offset and decoder of every leaf field of a message format, nested types flattened."""

    def __init__(self, formats: Dict[str, List[Tuple[str, str, int]]], name: str):
        self.fields: Dict[str, Tuple[int, str, int]] = {}  # path -> (offset, struct code, count)
        self.size = self._walk(formats, name, "", 0)

    def _walk(self, formats, name, prefix, offset) -> int:
        start = offset
        for ftype, fname, count in formats[name]:
            n = max(count, 1)
            if ftype in TYPES:
                if not fname.startswith("_padding"):
                    self.fields[prefix + fname] = (offset, TYPES[ftype], count)
                offset += SIZES[ftype] * n
            elif ftype in formats:
                for i in range(n):
                    offset += self._walk(formats, ftype, f"{prefix}{fname}{f'[{i}]' if count else ''}.", offset)
            else:
                raise ULogError(f"Unknown field type {ftype} in {name}")
        return offset - start

    def get(self, data: bytes, path: str) -> Any:
        offset, code, count = self.fields[path]
        if count:
            values = struct.unpack_from(f"<{count}{code}", data, offset)
            return bytes(values).split(b"\0")[0].decode(errors="replace") if code == "c" else list(values)
        return struct.unpack_from("<" + code, data, offset)[0]


def _parse_format(text: str) -> Tuple[str, List[Tuple[str, str, int]]]:
    name, _, body = text.partition(":")
    fields = []
    for item in filter(None, body.split(";")):
        ftype, fname = item.strip().split(" ", 1)
        count = 0
        if "[" in ftype:
            ftype, _, rest = ftype.partition("[")
            count = int(rest.rstrip("]"))
        fields.append((ftype, fname, count))
    return name, fields


def read(path: Path, topics: Optional[set] = None) -> Dict[str, Any]:
    """{info, params, messages: [(t_us, level, text)], dropouts, data: {(topic, multi_id): [(t_us, bytes)]}, layouts}"""
    data_bytes = Path(path).read_bytes()
    if data_bytes[:7] != MAGIC:
        raise ULogError("Not a ULog file")
    start_us = struct.unpack_from("<Q", data_bytes, 8)[0]
    pos, end = 16, len(data_bytes)
    formats: Dict[str, List[Tuple[str, str, int]]] = {}
    subs: Dict[int, Tuple[str, int]] = {}
    info: Dict[str, Any] = {}
    params: Dict[str, Any] = {}
    messages: List[Tuple[int, str, str]] = []
    dropouts: List[int] = []
    out: Dict[Tuple[str, int], List[Tuple[int, bytes]]] = {}
    layouts: Dict[str, _Layout] = {}

    def value_of(key: str, raw: bytes) -> Any:
        ktype, _, kname = key.partition(" ")
        base, _, cnt = ktype.partition("[")
        if base == "char":
            return kname, raw.split(b"\0")[0].decode(errors="replace")
        if base in TYPES:
            n = int(cnt.rstrip("]")) if cnt else 1
            vals = struct.unpack_from(f"<{n}{TYPES[base]}", raw, 0)
            return kname, vals[0] if n == 1 else list(vals)
        return kname, raw

    while pos + 3 <= end:
        size, mtype = struct.unpack_from("<HB", data_bytes, pos)
        body = data_bytes[pos + 3: pos + 3 + size]
        pos += 3 + size
        if len(body) < size:
            break  # truncated final message
        if mtype == ord("D"):
            msg_id = struct.unpack_from("<H", body, 0)[0]
            sub = subs.get(msg_id)
            if sub and (topics is None or sub[0] in topics):
                out.setdefault(sub, []).append((struct.unpack_from("<Q", body, 2)[0], body[2:]))
        elif mtype == ord("F"):
            name, fields = _parse_format(body.decode(errors="replace"))
            formats[name] = fields
        elif mtype == ord("A"):
            multi_id, msg_id = struct.unpack_from("<BH", body, 0)
            subs[msg_id] = (body[3:].decode(errors="replace"), multi_id)
        elif mtype in (ord("I"), ord("P")):
            klen = body[0]
            k, v = value_of(body[1:1 + klen].decode(errors="replace"), body[1 + klen:])
            (info if mtype == ord("I") else params)[k] = v
        elif mtype == ord("L"):
            level, t = struct.unpack_from("<BQ", body, 0)
            messages.append((t, LEVELS.get(level, str(level)), body[9:].decode(errors="replace")))
        elif mtype == ord("C"):
            level, _tag, t = struct.unpack_from("<BHQ", body, 0)
            messages.append((t, LEVELS.get(level, str(level)), body[11:].decode(errors="replace")))
        elif mtype == ord("O"):
            dropouts.append(struct.unpack_from("<H", body, 0)[0])
    for (topic, _multi) in out:
        if topic in formats and topic not in layouts:
            layouts[topic] = _Layout(formats, topic)
    return {"start_us": start_us, "info": info, "params": params, "messages": messages, "dropouts": dropouts, "data": out, "layouts": layouts}


TOPICS = {"vehicle_attitude", "vehicle_attitude_setpoint", "vehicle_angular_velocity", "vehicle_rates_setpoint", "vehicle_local_position",
          "vehicle_air_data", "vehicle_gps_position", "sensor_gps", "actuator_outputs", "vehicle_thrust_setpoint", "vehicle_status"}
SIGNALS = ["vehicle.attitude", "control.attitude_setpoint", "vehicle.yaw_rate", "control.yaw_rate_setpoint", "vehicle.altitude",
           "vehicle.ground_speed", "actuator.output", "propulsion.throttle", "controller.event"]


def mapping(max_hz: float = 10.0, clock_domain: str = "px4_clock") -> Dict[str, Any]:
    """The fixed mapping, in the shape bags.import_profile expects (for the import record)."""
    return {"mapping_id": VERSION, "version": VERSION, "max_hz": max_hz, "clock_domain": clock_domain,
            "topics": [{"topic": "ulog", "entries": [{"signal": s} for s in SIGNALS]}]}


def extract(path: Path, *, asset: str, max_hz: float = 10.0, progress: Optional[Callable[[int], None]] = None) -> Tuple[Extraction, Dict[str, Any]]:
    log = read(path, TOPICS)
    data, lay = log["data"], log["layouts"]
    period = 1e6 / max_hz
    # UTC offset from the first GPS fix
    offset_us, clock = None, "px4_boot"
    for topic in ("vehicle_gps_position", "sensor_gps"):
        for (name, multi), samples in data.items():
            if name != topic or offset_us is not None or "time_utc_usec" not in lay[name].fields:
                continue
            for t, raw in samples:
                utc = lay[name].get(raw, "time_utc_usec")
                if utc and utc > 1_000_000_000_000_000:  # after 2001
                    offset_us, clock = utc - t, "gps_utc"
                    break
    if offset_us is None:
        # No GPS time: use the wall-clock time in the logger's own file name ("Opened full log file: .../2026-03-20/16_45_05.ulg"),
        # which the companion computer's clock wrote when logging started. Labelled as such; seconds resolution.
        import re
        from datetime import datetime, timezone
        for t, _level, text in log["messages"]:
            m = re.search(r"(\d{4}-\d{2}-\d{2})/(\d{2})_(\d{2})_(\d{2})\.ulg", text)
            if "Opened" in text and m:
                wall = datetime.strptime(f"{m.group(1)} {m.group(2)}:{m.group(3)}:{m.group(4)}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                offset_us, clock = int(wall.timestamp() * 1e6) - t, "logger_file_name"
                break
    if offset_us is None:
        offset_us = 0
    rows: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {}
    skipped: Dict[str, int] = {}

    def add(t_us, signal, value, unit, scope, event=None):
        rows.append({"t": int((t_us + offset_us) * 1000), "asset": asset, "signal": signal, "value": value, "unit": unit,
                     "scope": scope, "event": event})
        counts[signal] = counts.get(signal, 0) + 1

    def series(topic, multi_wanted=0):
        last = None
        for (name, multi), samples in data.items():
            if name != topic or multi != multi_wanted:
                continue
            for t, raw in samples:
                if last is not None and t - last < period:
                    continue
                last = t
                yield t, raw

    def has(topic, field):
        return topic in lay and field in lay[topic].fields

    for topic, field, signal in (("vehicle_attitude", "q", "vehicle.attitude"), ("vehicle_attitude_setpoint", "q_d", "control.attitude_setpoint")):
        if has(topic, field):
            for t, raw in series(topic):
                w, x, y, z = lay[topic].get(raw, field)  # PX4 quaternions are (w, x, y, z)
                if any(map(math.isnan, (w, x, y, z))) or abs(w * w + x * x + y * y + z * z - 1) > 0.1:
                    skipped[signal] = skipped.get(signal, 0) + 1
                    continue
                for scope, v in zip(("roll", "pitch", "yaw"), quat_to_euler_deg(x, y, z, w)):
                    add(t, signal, round(v, 4), "deg", scope)
    if has("vehicle_angular_velocity", "xyz"):
        for t, raw in series("vehicle_angular_velocity"):
            add(t, "vehicle.yaw_rate", round(math.degrees(lay["vehicle_angular_velocity"].get(raw, "xyz")[2]), 4), "deg/s", "yaw")
    if has("vehicle_rates_setpoint", "yaw"):
        for t, raw in series("vehicle_rates_setpoint"):
            v = lay["vehicle_rates_setpoint"].get(raw, "yaw")
            if not math.isnan(v):
                add(t, "control.yaw_rate_setpoint", round(math.degrees(v), 4), "deg/s", "yaw")
    if has("vehicle_local_position", "z"):
        L = lay["vehicle_local_position"]
        for t, raw in series("vehicle_local_position"):
            z = L.get(raw, "z")
            if not math.isnan(z):
                add(t, "vehicle.altitude", round(-z, 3), "m", "estimate")
            if "vx" in L.fields:
                vx, vy = L.get(raw, "vx"), L.get(raw, "vy")
                if not (math.isnan(vx) or math.isnan(vy)):
                    add(t, "vehicle.ground_speed", round(math.hypot(vx, vy), 3), "m/s", None)
    if has("vehicle_air_data", "baro_alt_meter"):
        for t, raw in series("vehicle_air_data"):
            add(t, "vehicle.altitude", round(lay["vehicle_air_data"].get(raw, "baro_alt_meter"), 3), "m", "baro")
    gps = next((tp for tp in ("vehicle_gps_position", "sensor_gps") if tp in lay), None)
    if gps:
        field, scale = ("altitude_msl_m", 1.0) if has(gps, "altitude_msl_m") else ("alt", 1e-3) if has(gps, "alt") else (None, None)
        if field:
            for t, raw in series(gps):
                v = lay[gps].get(raw, field) * scale
                if v:
                    add(t, "vehicle.altitude", round(v, 3), "m", "gps")
    if has("actuator_outputs", "output"):
        A = lay["actuator_outputs"]
        for t, raw in series("actuator_outputs"):
            outs = A.get(raw, "output")
            n = A.get(raw, "noutputs") if "noutputs" in A.fields else len(outs)
            for i, v in enumerate(outs[:n]):
                if not math.isnan(v):
                    add(t, "actuator.output", round(v, 3), "us", f"ch{i + 1}")
    if has("vehicle_thrust_setpoint", "xyz"):
        for t, raw in series("vehicle_thrust_setpoint"):
            z = lay["vehicle_thrust_setpoint"].get(raw, "xyz")[2]
            if not math.isnan(z):
                add(t, "propulsion.throttle", round(-z * 100, 2), "%", None)
    if "vehicle_status" in lay:
        S, last = lay["vehicle_status"], {}
        for (name, multi), samples in data.items():
            if name != "vehicle_status":
                continue
            for t, raw in samples:
                for field, names in (("nav_state", NAV_STATE), ("arming_state", ARMING), ("failsafe", None)):
                    if field not in S.fields:
                        continue
                    v = S.get(raw, field)
                    text = (names or {}).get(v, str(v)) if names is not None else ("on" if v else "off")
                    if last.get(field) is not None and last[field] != text:
                        add(t, "controller.event", None, None, "vehicle_status", f"{field} {last[field]} -> {text}")
                    last[field] = text
    for t, level, text in log["messages"]:
        if level != "DEBUG":
            add(t, "controller.event", None, None, "log", f"{level}: {text[:200]}")
    if not rows:
        raise ULogError("None of the supported PX4 topics are in this log")
    rows.sort(key=lambda r: (r["t"], r["signal"], r["scope"] or ""))
    meta = {"clock": clock, "offset_us": offset_us, "info": {k: v for k, v in log["info"].items() if isinstance(v, (str, int, float))},
            "dropouts": len(log["dropouts"]), "dropout_ms": sum(log["dropouts"]), "topics_found": sorted({k[0] for k in data}),
            "params": log["params"]}
    return Extraction(rows=rows, counts=counts, skipped=skipped, start_ns=rows[0]["t"], end_ns=rows[-1]["t"]), meta
