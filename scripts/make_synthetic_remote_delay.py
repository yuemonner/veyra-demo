"""Generate SYNTHETIC remote-control response-delay fixtures (deterministic, no randomness).

These files are invented for testing ingestion and analysis mechanics. They are not BuilderX data,
not derived from any customer record, and must not be presented as field evidence.

Scenario (machine EX1, 2026-03-04 09:00–09:15 UTC by the console clock):
- Console log (CSV, console_clock): RTT and loss of the probe path console -> vehicle-router, and
  command transmissions. RTT and loss rise between 09:06:00 and 09:07:30.
- Vehicle log (JSON Lines, vehicle_controller_clock): command receipt, controller output, vehicle
  feedback, application heartbeats (with a gap), a controller restart and a fault code.
- The vehicle clock runs 0.8 s ahead of the console clock. This offset is NOT declared in the
  profiles, so any cross-clock latency must be reported as unavailable rather than computed.

- Vehicle input/response (CSV, vehicle_controller_clock, 10 Hz): boom command as received by the
  controller (%) and boom rate (deg/s). Healthy behaviour: delay 150 ms, gain 0.4 deg/s per %.
  It is unchanged during the network incident; `io_rows(slowdown_after=...)` builds a variant whose
  vehicle-side delay rises to 450 ms, for tests only.

Usage: python scripts/make_synthetic_remote_delay.py [output_dir]
"""
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

START = datetime(2026, 3, 4, 9, 0, 0, tzinfo=timezone.utc)
INCIDENT = (START + timedelta(minutes=6), START + timedelta(minutes=7, seconds=30))
VEHICLE_CLOCK_OFFSET_S = 0.8  # undeclared on purpose
HEARTBEAT_GAP = (START + timedelta(minutes=6, seconds=20), START + timedelta(minutes=6, seconds=50))


def in_incident(t):
    return INCIDENT[0] <= t < INCIDENT[1]


def iso(t):
    return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def rtt_ms(i, t):
    base = 45 + (i * 7) % 16  # 45–60 ms
    return 380 + (i * 37) % 141 if in_incident(t) else base  # 380–520 ms during the incident


def loss_ratio(i, t):
    return round(0.08 + ((i * 13) % 8) / 100, 3) if in_incident(t) else round(((i * 3) % 4) / 1000, 3)


def build():
    console = []
    for i in range(90):  # every 10 s
        t = START + timedelta(seconds=10 * i)
        console.append({"time": iso(t), "machine": "EX1", "metric": "lte_rtt", "reading": rtt_ms(i, t), "units": "ms", "path": "console->vehicle-router", "cmd_id": ""})
        console.append({"time": iso(t), "machine": "EX1", "metric": "lte_loss", "reading": loss_ratio(i, t), "units": "ratio", "path": "console->vehicle-router", "cmd_id": ""})

    vehicle = []
    for k in range(30):  # one command every 30 s
        sent = START + timedelta(seconds=30 * k + 5)
        command_id = f"C{1000 + k}"
        console.append({"time": iso(sent), "machine": "EX1", "metric": "cmd_tx", "reading": "", "units": "", "path": "operator-console", "cmd_id": command_id})
        transit_ms = (420 + (k * 29) % 90) if in_incident(sent) else (22 + (k * 5) % 9)
        received = sent + timedelta(milliseconds=transit_ms)
        output = received + timedelta(milliseconds=15 + (k * 3) % 10)
        feedback = output + timedelta(milliseconds=120 + (k * 11) % 40)
        for name, moment in (("cmd_rx", received), ("cmd_out", output), ("fb_boom", feedback)):
            vehicle_clock = moment + timedelta(seconds=VEHICLE_CLOCK_OFFSET_S)
            vehicle.append({"ts": int(vehicle_clock.timestamp() * 1000), "unit_id": "EX-01", "name": name, "cmd": command_id})

    for j in range(180):  # heartbeat every 5 s, missing during the gap
        t = START + timedelta(seconds=5 * j)
        if HEARTBEAT_GAP[0] <= t < HEARTBEAT_GAP[1]:
            continue
        vehicle.append({"ts": int((t + timedelta(seconds=VEHICLE_CLOCK_OFFSET_S)).timestamp() * 1000), "unit_id": "EX-01", "name": "hb"})

    fault_time = START + timedelta(minutes=6, seconds=21) + timedelta(seconds=VEHICLE_CLOCK_OFFSET_S)
    vehicle.append({"ts": int(fault_time.timestamp() * 1000), "unit_id": "EX-01", "name": "dtc", "code": "SYN-LINK-017", "desc": "Synthetic: link supervision timeout", "src": "remote_kit"})
    restart = START + timedelta(minutes=6, seconds=52) + timedelta(seconds=VEHICLE_CLOCK_OFFSET_S)
    vehicle.append({"ts": int(restart.timestamp() * 1000), "unit_id": "EX-01", "name": "ctrl_restart", "evt": "watchdog_restart"})

    console.sort(key=lambda r: (r["time"], r["metric"]))
    vehicle.sort(key=lambda r: (r["ts"], r["name"]))
    return console, vehicle


IO_RATE_HZ = 10
IO_GAIN = 0.4       # deg/s per % command
IO_DELAY_S = 0.15
IO_SLOW_DELAY_S = 0.45
IO_TAU_S = 0.1


def _joystick(t):
    value = 60 * math.sin(2 * math.pi * t / 14.0) + 25 * math.sin(2 * math.pi * t / 5.3)
    return max(-100.0, min(100.0, value))


def io_rows(slowdown_after=None, minutes=15):
    """Vehicle-clock samples of boom command input and boom rate. slowdown_after: seconds after START when delay rises."""
    dt = 1.0 / IO_RATE_HZ
    rows = []
    response = 0.0
    for k in range(int(minutes * 60 * IO_RATE_HZ)):
        t = k * dt
        delay = IO_SLOW_DELAY_S if slowdown_after is not None and t >= slowdown_after else IO_DELAY_S
        target = IO_GAIN * _joystick(t - delay)
        response += (target - response) * (dt / (IO_TAU_S + dt))  # first-order lag
        ripple = 0.3 * math.sin(2 * math.pi * t / 0.9)
        ts = int(round((START.timestamp() + t + VEHICLE_CLOCK_OFFSET_S) * 1000))
        rows.append({"ts": ts, "unit": "EX-01", "name": "rx_cmd_boom", "val": round(_joystick(t), 3), "uom": "%", "ch": "boom"})
        rows.append({"ts": ts, "unit": "EX-01", "name": "boom_rate", "val": round(response + ripple, 3), "uom": "deg/s", "ch": "boom"})
    return rows


def io_csv_text(rows):
    header = ["ts", "unit", "name", "val", "uom", "ch"]
    return "\n".join([",".join(header)] + [",".join(str(r[h]) for h in header) for r in rows]) + "\n"


IO_PROFILE = {
    "profile_id": "synthetic-vehicle-io-csv", "version": 1, "format": "csv", "synthetic": True,
    "columns": {"timestamp": "ts", "asset": "unit", "signal": "name", "value": "val", "unit": "uom", "scope": "ch"},
    "signal_map": {"rx_cmd_boom": "vehicle.command_input", "boom_rate": "vehicle.actuator_response"},
    "asset_aliases": {"EX-01": "EX1"}, "clock_domain": "vehicle_controller_clock", "timestamp_format": "epoch", "epoch_unit": "ms",
    "source_metadata": {"oem": "SYNTHETIC-OEM", "model": "SYN-EX20", "kit_version": "synthetic-kit-2.1", "control_version": "synthetic-ctl-4.3",
                        "firmware_version": "synthetic-vcu-1.8", "vehicle_interface": "synthetic-CAN-bridge-1.0"},
    "notes": "SYNTHETIC 10 Hz input/response fixture. Vehicle clock offset intentionally undeclared.",
}

CONSOLE_PROFILE = {
    "profile_id": "synthetic-console-csv", "version": 1, "format": "csv", "synthetic": True,
    "columns": {"timestamp": "time", "asset": "machine", "signal": "metric", "value": "reading", "unit": "units", "scope": "path", "command_id": "cmd_id"},
    "signal_map": {"lte_rtt": "network.rtt", "lte_loss": "network.packet_loss", "cmd_tx": "command.sent"},
    "clock_domain": "console_clock", "timestamp_format": "iso8601",
    "source_metadata": {"oem": "SYNTHETIC-OEM", "model": "SYN-EX20", "kit_version": "synthetic-kit-2.1", "control_version": "synthetic-ctl-4.3",
                        "network_path": "LTE private APN (synthetic)", "measurement_endpoints": "console -> vehicle-router ICMP probe (synthetic)"},
    "notes": "SYNTHETIC fixture for mechanics testing. Not customer data.",
}

VEHICLE_PROFILE = {
    "profile_id": "synthetic-vehicle-jsonl", "version": 1, "format": "jsonl", "synthetic": True,
    "columns": {"timestamp": "ts", "asset": "unit_id", "signal": "name", "command_id": "cmd", "code": "code", "code_description": "desc", "code_source": "src", "event_value": "evt"},
    "signal_map": {"cmd_rx": "command.received", "cmd_out": "command.output", "fb_boom": "vehicle.feedback", "hb": "app.heartbeat",
                   "ctrl_restart": "controller.reset", "dtc": "fault.code"},
    "asset_aliases": {"EX-01": "EX1"}, "fault_source_domains": {"remote_kit": "control_suite"},
    "clock_domain": "vehicle_controller_clock", "timestamp_format": "epoch", "epoch_unit": "ms",
    "source_metadata": {"oem": "SYNTHETIC-OEM", "model": "SYN-EX20", "kit_version": "synthetic-kit-2.1", "control_version": "synthetic-ctl-4.3", "vehicle_interface": "synthetic-CAN-bridge-1.0"},
    "notes": "SYNTHETIC fixture. Vehicle clock offset is intentionally undeclared.",
}


def write(out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    console, vehicle = build()
    header = ["time", "machine", "metric", "reading", "units", "path", "cmd_id"]
    # CSV has no comment syntax; the synthetic label lives in the filename, the profile and README.md.
    lines = [",".join(header)]
    for row in console:
        lines.append(",".join(str(row[h]) for h in header))
    (out_dir / "synthetic_ex1_console.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out_dir / "synthetic_ex1_vehicle.jsonl").write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in vehicle), encoding="utf-8")
    (out_dir / "synthetic_console_profile.json").write_text(json.dumps(CONSOLE_PROFILE, indent=2) + "\n", encoding="utf-8")
    (out_dir / "synthetic_vehicle_profile.json").write_text(json.dumps(VEHICLE_PROFILE, indent=2) + "\n", encoding="utf-8")
    (out_dir / "synthetic_ex1_vehicle_io.csv").write_text(io_csv_text(io_rows()), encoding="utf-8")
    (out_dir / "synthetic_vehicle_io_profile.json").write_text(json.dumps(IO_PROFILE, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "fixtures" / "synthetic" / "remote_delay"
    write(target)
    print(f"Wrote SYNTHETIC fixtures to {target}")
