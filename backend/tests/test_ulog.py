"""PX4 ULog reader: a small log written byte by byte from the published format, read back into canonical rows."""
import math
import struct

from app.ingest import ulog


def _msg(kind: str, body: bytes) -> bytes:
    return struct.pack("<HB", len(body), ord(kind)) + body


def _write(path, logger_line=True):
    out = bytearray(b"ULog\x01\x12\x35\x01" + struct.pack("<Q", 0))
    out += _msg("F", b"vehicle_attitude:uint64_t timestamp;float[4] q;uint8_t[4] _padding0;")
    out += _msg("F", b"vehicle_status:uint64_t timestamp;uint8_t nav_state;uint8_t arming_state;bool failsafe;uint8_t[5] _padding0;")
    out += _msg("I", bytes([len(b"char[13] ver_hw")]) + b"char[13] ver_hw" + b"MODALAI_VOXL2")
    out += _msg("P", bytes([len(b"float MPC_Z_P")]) + b"float MPC_Z_P" + struct.pack("<f", 5.0))
    out += _msg("A", struct.pack("<BH", 0, 1) + b"vehicle_attitude")
    out += _msg("A", struct.pack("<BH", 0, 2) + b"vehicle_status")
    if logger_line:
        out += _msg("L", struct.pack("<BQ", ord("6"), 1_000_000) + b"[logger] Opened full log file: /data/px4/log/2026-03-20/16_45_05.ulg")
    half = math.sqrt(0.5)
    for i in range(30):
        t = 1_000_000 + i * 100_000
        q = (1.0, 0.0, 0.0, 0.0) if i < 15 else (half, half, 0.0, 0.0)   # w, x, y, z: level, then 90 deg roll
        out += _msg("D", struct.pack("<HQ4f4x", 1, t, *q))
        out += _msg("D", struct.pack("<HQBB?5x", 2, t, 3 if i < 20 else 5, 2, False))
    out += _msg("L", struct.pack("<BQ", ord("4"), 3_500_000) + b"[commander] Failsafe activated")
    path.write_bytes(bytes(out))
    return path


def test_reads_formats_attitude_events_params_and_wall_clock(tmp_path):
    p = _write(tmp_path / "x.ulg")
    assert ulog.is_ulog(p)
    ex, meta = ulog.extract(p, asset="UAV1")
    assert meta["clock"] == "logger_file_name" and meta["params"]["MPC_Z_P"] == 5.0 and meta["info"]["ver_hw"] == "MODALAI_VOXL2"
    roll = [r["value"] for r in ex.rows if r["signal"] == "vehicle.attitude" and r["scope"] == "roll"]
    assert abs(roll[0]) < 1e-6 and abs(roll[-1] - 90.0) < 1e-3
    events = [r["event"] for r in ex.rows if r["signal"] == "controller.event"]
    assert "nav_state AUTO_MISSION -> AUTO_RTL" in events and any("Failsafe activated" in e for e in events)
    first = min(r["t"] for r in ex.rows)
    from datetime import datetime, timezone
    assert first == int(datetime(2026, 3, 20, 16, 45, 5, tzinfo=timezone.utc).timestamp()) * 1_000_000_000  # file-name time at boot 1.0 s


def test_without_any_wall_clock_the_boot_clock_is_kept_and_labelled(tmp_path):
    ex, meta = ulog.extract(_write(tmp_path / "y.ulg", logger_line=False), asset="UAV1")
    assert meta["clock"] == "px4_boot" and ex.start_ns < 10_000_000_000
