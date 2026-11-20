"""Versioned canonical signal catalog for the remote-control response-delay analysis.

A canonical signal states what a value *means*. source signal name are mapped to it only
through an explicit import profile; similar names never imply equivalence. Each entry declares
its record kind, the fault domain it observes, and the only unit conversions accepted.

Fault domains are observation scopes, not root causes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

CATALOG_VERSION = "remote-delay-catalog-v7"  # v7 actuator.current (additive); v6 traction battery and high-voltage signals (additive); v2 command_input/actuator_response; v3 hydraulic_pressure; v4 actuator outputs, attitude, speeds, throttle; v5 joints, drive status, body velocity (additive)

# Record kinds
MEASUREMENT = "measurement"  # numeric value with a unit
EVENT = "event"              # something happened; optional categorical value
HEARTBEAT = "heartbeat"      # periodic liveness event; gaps are analysed later
FAULT_CODE = "fault_code"    # decoded or raw fault code string
COMMAND = "command"          # one stage of a command's journey, keyed by command_id

KINDS = (MEASUREMENT, EVENT, HEARTBEAT, FAULT_CODE, COMMAND)

# Fault domains for remote-control response delay (kept distinct; none is a root cause)
NETWORK = "network"
CONTROL_SUITE = "control_suite"
VEHICLE_INTERFACE = "vehicle_interface"
VEHICLE_BODY = "vehicle_body"
UNSCOPED = "unscoped"
DOMAINS = (NETWORK, CONTROL_SUITE, VEHICLE_INTERFACE, VEHICLE_BODY)
# Signals that record what was COMMANDED, not how the machine behaved. A command that differs from the
# reference, or steps suddenly, is the mission or the operator, not evidence of a fault by itself.
COMMANDED_SIGNALS = frozenset({"vehicle.command_input", "control.attitude_setpoint", "joint.position_command",
                               "control.velocity_setpoint", "control.yaw_rate_setpoint"})


@dataclass(frozen=True)
class SignalSpec:
    id: str
    kind: str
    domain: str
    description: str
    unit: Optional[str] = None
    # Accepted unit spellings -> multiplicative factor to the canonical unit. Exact match only.
    conversions: Dict[str, float] = field(default_factory=dict)
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    # For command stages: position in the command chain (lower = earlier)
    stage_order: Optional[int] = None

    def convert(self, value: float, unit: str) -> Tuple[float, str]:
        if self.kind != MEASUREMENT:
            raise ValueError(f"{self.id} is not a numeric measurement")
        if unit not in self.conversions:
            accepted = ", ".join(sorted(self.conversions)) or "none"
            raise ValueError(f"unit '{unit}' is not accepted for {self.id} (accepted: {accepted})")
        return value * self.conversions[unit], self.unit or unit


_MS = {"ms": 1.0, "millisecond": 1.0, "milliseconds": 1.0, "s": 1000.0, "sec": 1000.0, "seconds": 1000.0, "us": 0.001, "µs": 0.001, "microseconds": 0.001}
_PERCENT = {"%": 1.0, "percent": 1.0, "pct": 1.0, "ratio": 100.0, "fraction": 100.0}
_COMMAND_PERCENT = {"%": 1.0, "percent": 1.0, "ratio": 100.0}
_RATE = {"deg/s": 1.0, "rad/s": 57.29577951308232}
_PRESSURE = {"bar": 1.0, "kPa": 0.01, "MPa": 10.0, "psi": 0.0689475729}
_PWM = {"us": 1.0, "µs": 1.0, "pwm_us": 1.0}
_DEG = {"deg": 1.0, "degree": 1.0, "rad": 57.29577951308232}
_MPS = {"m/s": 1.0, "km/h": 1 / 3.6}
_M = {"m": 1.0, "ft": 0.3048}
_NM = {"Nm": 1.0, "N·m": 1.0, "N*m": 1.0}
_SIGNED_MPS = {"m/s": 1.0}
_VOLT = {"V": 1.0, "mV": 0.001}
_AMP = {"A": 1.0, "mA": 0.001}
_DEGC = {"degC": 1.0, "C": 1.0, "°C": 1.0}
_KBPS = {"kbit/s": 1.0, "kbps": 1.0, "bit/s": 0.001, "bps": 0.001, "Mbit/s": 1000.0, "Mbps": 1000.0}

_SPECS = (
    SignalSpec("network.rtt", MEASUREMENT, NETWORK, "Round-trip time of a named network probe path. Only describes the measured path.", "ms", _MS, minimum=0.0),
    SignalSpec("network.one_way_delay", MEASUREMENT, NETWORK, "One-way delay; requires synchronized clocks at both ends.", "ms", _MS, minimum=0.0),
    SignalSpec("network.jitter", MEASUREMENT, NETWORK, "Delay variation of a named probe path.", "ms", _MS, minimum=0.0),
    SignalSpec("network.packet_loss", MEASUREMENT, NETWORK, "Packet loss of a named probe path.", "%", _PERCENT, minimum=0.0, maximum=100.0),
    SignalSpec("network.throughput", MEASUREMENT, NETWORK, "Measured throughput of a named link.", "kbit/s", _KBPS, minimum=0.0),
    SignalSpec("network.signal_strength", MEASUREMENT, NETWORK, "Radio signal strength reported by a modem.", "dBm", {"dBm": 1.0, "dbm": 1.0}, minimum=-200.0, maximum=50.0),
    SignalSpec("network.link_event", EVENT, NETWORK, "Link state change (up, down, handover, reconnect)."),
    SignalSpec("app.heartbeat", HEARTBEAT, CONTROL_SUITE, "Application liveness heartbeat; gaps are computed, not assumed."),
    SignalSpec("app.heartbeat_interval", MEASUREMENT, CONTROL_SUITE, "Reported interval between heartbeats.", "ms", _MS, minimum=0.0),
    SignalSpec("controller.event", EVENT, CONTROL_SUITE, "Controller/application event such as mode change or watchdog."),
    SignalSpec("controller.reset", EVENT, CONTROL_SUITE, "Controller or application restart/reset."),
    SignalSpec("controller.cycle_time", MEASUREMENT, CONTROL_SUITE, "Control loop cycle time.", "ms", _MS, minimum=0.0),
    SignalSpec("fault.code", FAULT_CODE, UNSCOPED, "Fault code with its originating system; domain comes from the declared source system."),
    SignalSpec("command.sent", COMMAND, NETWORK, "Operator console sent a command.", stage_order=1),
    SignalSpec("command.received", COMMAND, NETWORK, "Vehicle-side gateway received the command.", stage_order=2),
    SignalSpec("command.output", COMMAND, CONTROL_SUITE, "Controller produced the actuator output for the command.", stage_order=3),
    SignalSpec("vehicle.feedback", COMMAND, VEHICLE_INTERFACE, "Vehicle feedback observed for the command (actuator or machine response).", stage_order=4),
    # Continuous input/response pairs on one channel (row scope = channel name, e.g. "boom").
    SignalSpec("vehicle.command_input", MEASUREMENT, CONTROL_SUITE, "Command value for a channel as received by the vehicle controller (signed, percent of full scale).", "%", _COMMAND_PERCENT, minimum=-100.0, maximum=100.0),
    SignalSpec("vehicle.hydraulic_pressure", MEASUREMENT, VEHICLE_BODY, "Measured hydraulic pressure for a channel (row scope = channel, e.g. boom cylinder).", "bar", _PRESSURE, minimum=-5.0, maximum=1000.0),
    SignalSpec("actuator.output", MEASUREMENT, VEHICLE_INTERFACE, "Controller output to one actuator channel, e.g. a servo or valve drive command (row scope = channel function).", "us", _PWM, minimum=0.0, maximum=5000.0),
    SignalSpec("control.attitude_setpoint", MEASUREMENT, CONTROL_SUITE, "Attitude the controller commanded for an axis (row scope = roll, pitch or yaw).", "deg", _DEG, minimum=-720.0, maximum=720.0),
    SignalSpec("vehicle.attitude", MEASUREMENT, VEHICLE_BODY, "Measured attitude for an axis (row scope = roll, pitch or yaw).", "deg", _DEG, minimum=-720.0, maximum=720.0),
    SignalSpec("vehicle.ground_speed", MEASUREMENT, VEHICLE_BODY, "Measured ground speed.", "m/s", _MPS, minimum=0.0, maximum=1000.0),
    SignalSpec("vehicle.airspeed", MEASUREMENT, VEHICLE_BODY, "Measured airspeed.", "m/s", _MPS, minimum=0.0, maximum=1000.0),
    SignalSpec("vehicle.altitude", MEASUREMENT, VEHICLE_BODY, "Measured altitude.", "m", _M, minimum=-1000.0, maximum=20000.0),
    SignalSpec("propulsion.throttle", MEASUREMENT, VEHICLE_BODY, "Throttle the controller applied.", "%", _PERCENT, minimum=0.0, maximum=100.0),
    SignalSpec("vehicle.actuator_response", MEASUREMENT, VEHICLE_BODY, "Measured actuator or joint rate for a channel.", "deg/s", _RATE, minimum=-10000.0, maximum=10000.0),
    # v5: articulated machines (legs, arms) and commanded body motion. Row scope = joint name or body axis.
    SignalSpec("joint.position_command", MEASUREMENT, CONTROL_SUITE, "Joint position the controller commanded (row scope = joint name). Only rows the drive was applying.", "deg", _DEG, minimum=-720.0, maximum=720.0),
    SignalSpec("joint.position", MEASUREMENT, VEHICLE_INTERFACE, "Measured joint position (row scope = joint name).", "deg", _DEG, minimum=-720.0, maximum=720.0),
    SignalSpec("joint.torque", MEASUREMENT, VEHICLE_INTERFACE, "Measured joint torque (row scope = joint name).", "Nm", _NM, minimum=-2000.0, maximum=2000.0),
    SignalSpec("actuator.status", EVENT, VEHICLE_INTERFACE, "A drive's status word or state changed (row scope = drive or joint)."),
    SignalSpec("control.velocity_setpoint", MEASUREMENT, CONTROL_SUITE, "Body velocity commanded to the machine (row scope = x or y, body frame).", "m/s", _SIGNED_MPS, minimum=-50.0, maximum=50.0),
    SignalSpec("vehicle.velocity", MEASUREMENT, VEHICLE_BODY, "Measured body velocity (row scope = x or y, body frame).", "m/s", _SIGNED_MPS, minimum=-50.0, maximum=50.0),
    SignalSpec("control.yaw_rate_setpoint", MEASUREMENT, CONTROL_SUITE, "Turn rate commanded to the machine.", "deg/s", _RATE, minimum=-3600.0, maximum=3600.0),
    SignalSpec("vehicle.yaw_rate", MEASUREMENT, VEHICLE_BODY, "Measured turn rate.", "deg/s", _RATE, minimum=-3600.0, maximum=3600.0),
    # v6: traction battery and high-voltage system, as a BMS and a vehicle controller report them. Ranges reject the
    # placeholder values a BMS sends while starting up (a 65.535 V cell, a 215 degC sensor), never a plausible reading.
    SignalSpec("battery.pack_voltage", MEASUREMENT, VEHICLE_BODY, "Battery pack terminal voltage reported by the BMS (row scope = where it is read: pack, bus after the contactors, or the receiving controller).", "V", _VOLT, minimum=0.0, maximum=1500.0),
    SignalSpec("battery.pack_current", MEASUREMENT, VEHICLE_BODY, "Battery pack current reported by the BMS; positive = discharge.", "A", _AMP, minimum=-3000.0, maximum=3000.0),
    SignalSpec("battery.soc", MEASUREMENT, VEHICLE_BODY, "State of charge as the BMS estimates it. An estimate, never a measurement of remaining energy.", "%", _PERCENT, minimum=0.0, maximum=102.0),
    SignalSpec("battery.cell_voltage", MEASUREMENT, VEHICLE_BODY, "A cell voltage the BMS reports (row scope = min or max of the pack, or a cell number).", "V", _VOLT, minimum=0.5, maximum=6.0),
    SignalSpec("battery.cell_index", MEASUREMENT, VEHICLE_BODY, "Which cell currently holds the pack's lowest or highest voltage (row scope = min or max). A label, not a quantity.", "index", {"index": 1.0}, minimum=0.0, maximum=1000.0),
    SignalSpec("battery.temperature", MEASUREMENT, VEHICLE_BODY, "A battery temperature the BMS reports (row scope = min or max of the pack, or a sensor number).", "degC", _DEGC, minimum=-50.0, maximum=150.0),
    SignalSpec("battery.current_limit", MEASUREMENT, VEHICLE_INTERFACE, "A current limit the BMS advertises to the vehicle (row scope = which limit).", "A", _AMP, minimum=0.0, maximum=6000.0),
    SignalSpec("battery.capacity_parameter", MEASUREMENT, VEHICLE_INTERFACE, "Capacity value the BMS sends as a parameter. Not a measured capacity.", "Ah", {"Ah": 1.0}, minimum=0.0, maximum=5000.0),
    SignalSpec("vehicle.low_voltage", MEASUREMENT, VEHICLE_BODY, "Low-voltage (12/24 V) supply voltage reported by the vehicle controller.", "V", _VOLT, minimum=0.0, maximum=60.0),
    SignalSpec("actuator.current", MEASUREMENT, VEHICLE_BODY, "Measured current of one actuator or motor (row scope = which actuator).", "A", _AMP, minimum=-5000.0, maximum=5000.0),
    SignalSpec("battery.state", EVENT, VEHICLE_INTERFACE, "A BMS or high-voltage state changed: contactor feedback, system state, fault level (row scope = which state). A feedback bit is not a measured contact."),
)

CATALOG: Dict[str, SignalSpec] = {spec.id: spec for spec in _SPECS}


def get(signal_id: str) -> SignalSpec:
    try:
        return CATALOG[signal_id]
    except KeyError:
        raise ValueError(f"'{signal_id}' is not a canonical signal in {CATALOG_VERSION}") from None


def describe() -> dict:
    """Serializable catalog for UIs and onboarding documentation."""
    return {
        "version": CATALOG_VERSION,
        "domains": list(DOMAINS),
        "signals": [
            {
                "id": s.id,
                "kind": s.kind,
                "domain": s.domain,
                "description": s.description,
                "unit": s.unit,
                "accepted_units": sorted(s.conversions),
                "minimum": s.minimum,
                "maximum": s.maximum,
                "stage_order": s.stage_order,
            }
            for s in _SPECS
        ],
    }
