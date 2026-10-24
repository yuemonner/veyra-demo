"""Decoding the few MAVLink messages that say what the flight controller itself did, from a raw MAVLink stream
recorded by mavros (mavros_msgs/Mavlink: msgid, len, payload64 = the payload as little-endian uint64 words).

Message layouts are the published MAVLink common message set (https://mavlink.io/en/messages/common.html), fields in
wire order (largest type first). MAVLink 2 trims trailing zero bytes, so payloads are zero-padded to full length.
  0   HEARTBEAT     custom_mode u32, type u8, autopilot u8, base_mode u8, system_status u8
  1   SYS_STATUS    sensors present / enabled / health bitmasks (u32 x3)
  22  PARAM_VALUE   param_value f32, param_count u16, param_index u16, param_id char[16], param_type u8
                    (sent by the controller when a parameter is set, or when one is requested)
  77  COMMAND_ACK   command u16, result u8
  253 STATUSTEXT    severity u8, text char[50]
"""
from __future__ import annotations

import struct
from typing import Any, Dict, List, Optional

SIZES = {0: 9, 1: 31, 22: 25, 77: 3, 253: 51}
MAV_STATE = {0: "UNINIT", 1: "BOOT", 2: "CALIBRATING", 3: "STANDBY", 4: "ACTIVE", 5: "CRITICAL", 6: "EMERGENCY", 7: "POWEROFF",
             8: "FLIGHT_TERMINATION"}
SEVERITY = {0: "EMERGENCY", 1: "ALERT", 2: "CRITICAL", 3: "ERROR", 4: "WARNING", 5: "NOTICE", 6: "INFO", 7: "DEBUG"}
SENSOR_BITS = ["3D gyro", "3D accelerometer", "3D magnetometer", "absolute pressure", "differential pressure (airspeed)", "GPS",
               "optical flow", "vision position", "laser position", "external ground truth", "angular rate control",
               "attitude stabilisation", "yaw position", "altitude control", "XY position control", "motor outputs", "RC receiver",
               "gyro 2", "accelerometer 2", "magnetometer 2", "geofence", "AHRS", "terrain", "reverse motor", "logging", "battery",
               "proximity", "satcom", "pre-arm check", "obstacle avoidance", "propulsion"]
# ArduPilot reports these statistics counters as parameters every few tens of seconds: routine, never a configuration change.
ROUTINE_PARAM_PREFIXES = ("STAT_",)


def payload(words: List[int], length: int, msgid: int) -> bytes:
    raw = b"".join(struct.pack("<Q", int(w) & 0xFFFFFFFFFFFFFFFF) for w in words)[:max(0, int(length))]
    return raw.ljust(SIZES.get(msgid, len(raw)), b"\0")


def decode(msgid: int, data: bytes) -> Optional[Dict[str, Any]]:
    try:
        if msgid == 0:
            custom, typ, autopilot, base, status = struct.unpack_from("<IBBBB", data, 0)
            return {"custom_mode": custom, "type": typ, "autopilot": autopilot, "armed": bool(base & 128),
                    "system_status": MAV_STATE.get(status, str(status))}
        if msgid == 1:
            present, enabled, health = struct.unpack_from("<III", data, 0)
            return {"present": present, "enabled": enabled, "health": health}
        if msgid == 22:
            value, count, index = struct.unpack_from("<fHH", data, 0)
            name = data[8:24].split(b"\0")[0].decode(errors="replace")
            return {"name": name, "value": round(value, 6), "index": index, "count": count, "type": data[24]}
        if msgid == 77:
            command, result = struct.unpack_from("<HB", data, 0)
            return {"command": command, "result": result}
        if msgid == 253:
            return {"severity": SEVERITY.get(data[0], str(data[0])), "text": data[1:51].split(b"\0")[0].decode(errors="replace")}
    except struct.error:
        return None
    return None


def bits(mask: int) -> List[str]:
    return [SENSOR_BITS[i] if i < len(SENSOR_BITS) else f"bit {i}" for i in range(32) if mask >> i & 1]
