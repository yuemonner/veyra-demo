"""What each stream of an unfamiliar recording IS, from its message type (or, for PX4 logs, its topic name).

A role says what part of the machine's signal chain a stream measures, so an investigation can ask the right question of
it. Roles come only from standard, published message definitions (ROS common_msgs, mavros_msgs, PX4 uORB topic names);
a stream whose type is generic (Float64, Vector3) or unknown and whose topic name is not a standard one is listed as NOT
UNDERSTOOD rather than guessed.

Roles and the signal-chain point they measure (points are defined per machine type in the machine profile):
  operator_input        the pilot's / operator's own inputs (sticks, joystick)                    -> operator
  command_link          a raw telemetry/command protocol stream (decoded where the protocol is known) -> controller (and operator)
  controller_mode       the controller's mode, arming and failsafe state                          -> controller
  controller_setpoint   what the controller asks for (attitude, position, velocity targets)       -> controller
  controller_parameters configuration values reported by the controller                          -> controller
  controller_text       the controller's own text / log messages                                  -> controller
  controller_status     the controller's health and detector flags, diagnostics                   -> controller
  actuator_command      the output commands the controller sends (PWM, joint commands)            -> output_command
  actuator_feedback     what the actuator actually did (servo position, joint position/effort, ESC) -> actuator
  power                 battery voltage and current (an indirect view of what motors draw)          -> actuator
  body_motion           measured motion and position of the machine (IMU, odometry, GNSS, air data) -> motion
  sensor_housekeeping   sensor temperature, time references                                       -> (none)

LABEL streams are excluded before anything else: topics that carry a dataset's answer key (fault-injection status, ground
truth, annotations) must never become evidence. The guard is by topic name and is deliberately broad.
"""
from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

ROLES = ("operator_input", "command_link", "controller_mode", "controller_setpoint", "controller_parameters", "controller_text",
         "controller_status", "actuator_command", "actuator_feedback", "power", "body_motion", "sensor_housekeeping")
DEFAULT_POINT = {"operator_input": "operator", "command_link": "controller", "controller_mode": "controller", "controller_setpoint": "controller",
                 "controller_parameters": "controller", "controller_text": "controller", "controller_status": "controller",
                 "actuator_command": "output_command", "actuator_feedback": "actuator", "power": "actuator", "body_motion": "motion",
                 "sensor_housekeeping": None}

# Topics that carry a dataset's answer key. Matched against each path segment of the topic name.
LABEL_GUARD = re.compile(r"(^|[/_.-])(failure_status|fault_status|fault_injection|injected_fault|ground_?truth|gt|labels?|annotations?)($|[/_.-])", re.I)

_CMD = re.compile(r"(cmd|command|target|desired|setpoint|reference|goal)", re.I)

# Standard ROS / mavros message types (short form 'package/Type').
ROS_TYPES: Dict[str, str] = {
    "mavros_msgs/RCIn": "operator_input",
    "sensor_msgs/Joy": "operator_input",
    "mavros_msgs/ManualControl": "operator_input",
    "mavros_msgs/Mavlink": "command_link",
    "mavros_msgs/State": "controller_mode",
    "mavros_msgs/ExtendedState": "controller_mode",
    "mavros_msgs/StatusText": "controller_text",
    "rosgraph_msgs/Log": "controller_text",
    "rcl_interfaces/Log": "controller_text",
    "diagnostic_msgs/DiagnosticArray": "controller_status",
    "mavros_msgs/NavErrors": "controller_status",
    "mavros_msgs/Param": "controller_parameters",
    "mavros_msgs/ParamValue": "controller_parameters",
    "mavros_msgs/PositionTarget": "controller_setpoint",
    "mavros_msgs/GlobalPositionTarget": "controller_setpoint",
    "mavros_msgs/AttitudeTarget": "controller_setpoint",
    "mavros_msgs/NavDataPair": "controller_setpoint",     # commanded and measured value of one axis
    "mavros_msgs/NavVector3": "controller_setpoint",
    "mavros_msgs/RCOut": "actuator_command",
    "mavros_msgs/ActuatorControl": "actuator_command",
    "mavros_msgs/ESCStatus": "actuator_feedback",
    "mavros_msgs/ESCTelemetry": "actuator_feedback",
    "sensor_msgs/BatteryState": "power",
    "sensor_msgs/Imu": "body_motion",
    "nav_msgs/Odometry": "body_motion",
    "geometry_msgs/PoseStamped": "body_motion",
    "geometry_msgs/PoseWithCovarianceStamped": "body_motion",
    "sensor_msgs/NavSatFix": "body_motion",
    "sensor_msgs/FluidPressure": "body_motion",
    "sensor_msgs/MagneticField": "body_motion",
    "mavros_msgs/VFR_HUD": "body_motion",
    "mavros_msgs/Altitude": "body_motion",
    "sensor_msgs/Temperature": "sensor_housekeeping",
    "sensor_msgs/TimeReference": "sensor_housekeeping",
}
# Types whose role depends on whether the topic is a command topic.
COMMAND_OR_MEASURED = {"sensor_msgs/JointState": ("actuator_command", "actuator_feedback"),
                       "geometry_msgs/Twist": ("controller_setpoint", "body_motion"),
                       "geometry_msgs/TwistStamped": ("controller_setpoint", "body_motion")}
# Generic containers: understood only from a standard topic name.
GENERIC_TYPES = {"std_msgs/Float64", "std_msgs/Float32", "std_msgs/Int8", "std_msgs/Int32", "std_msgs/Bool", "std_msgs/String",
                 "geometry_msgs/Vector3", "geometry_msgs/Vector3Stamped", "std_msgs/Float64MultiArray"}
MAVROS_TOPIC_NAMES = {"global_position/rel_alt": "body_motion", "global_position/compass_hdg": "body_motion"}

# PX4 uORB topic names (https://docs.px4.io/main/en/msg_docs/).
PX4_TOPICS: Dict[str, str] = {
    "input_rc": "operator_input", "manual_control_setpoint": "operator_input", "manual_control_switches": "operator_input",
    "vehicle_command": "command_link", "vehicle_command_ack": "command_link",
    "vehicle_status": "controller_mode", "vehicle_control_mode": "controller_mode", "commander_state": "controller_mode",
    "vehicle_land_detected": "controller_mode", "failsafe_flags": "controller_status",
    "vehicle_attitude_setpoint": "controller_setpoint", "vehicle_rates_setpoint": "controller_setpoint",
    "trajectory_setpoint": "controller_setpoint", "position_setpoint_triplet": "controller_setpoint",
    "vehicle_local_position_setpoint": "controller_setpoint", "vehicle_thrust_setpoint": "controller_setpoint",
    "vehicle_torque_setpoint": "controller_setpoint", "tecs_status": "controller_status",
    "failure_detector_status": "controller_status", "estimator_status": "controller_status", "estimator_status_flags": "controller_status",
    "estimator_innovation_test_ratios": "controller_status", "sensors_status_imu": "controller_status", "vehicle_imu_status": "controller_status",
    "cpuload": "controller_status", "health_report": "controller_status", "event": "controller_text",
    "actuator_outputs": "actuator_command", "actuator_motors": "actuator_command", "actuator_servos": "actuator_command",
    "actuator_controls_0": "actuator_command",
    "esc_status": "actuator_feedback", "actuator_armed": "controller_mode",
    "battery_status": "power",
    "vehicle_attitude": "body_motion", "vehicle_angular_velocity": "body_motion", "vehicle_local_position": "body_motion",
    "vehicle_global_position": "body_motion", "sensor_combined": "body_motion", "vehicle_air_data": "body_motion",
    "sensor_gps": "body_motion", "vehicle_gps_position": "body_motion", "distance_sensor": "body_motion", "airspeed": "body_motion",
    "airspeed_validated": "body_motion", "vehicle_odometry": "body_motion", "vehicle_visual_odometry": "body_motion",
    "vehicle_acceleration": "body_motion", "sensor_accel": "body_motion", "sensor_gyro": "body_motion", "sensor_baro": "body_motion",
    "sensor_mag": "body_motion", "vehicle_magnetometer": "body_motion", "vehicle_angular_acceleration": "body_motion",
}


def short_type(msgtype: str) -> str:
    return (msgtype or "").replace("/msg/", "/")


def is_label(topic: str) -> bool:
    return bool(LABEL_GUARD.search(topic or ""))


def role_for(topic: str, msgtype: Optional[str], *, px4: bool = False) -> Tuple[Optional[str], str]:
    """(role or None, why). None means not understood."""
    if px4:
        role = PX4_TOPICS.get(topic)
        return (role, f"PX4 uORB topic '{topic}'") if role else (None, f"PX4 topic '{topic}' is not one Veyra has a role for")
    kind = short_type(msgtype or "")
    if kind in ROS_TYPES:
        return ROS_TYPES[kind], f"standard message type {kind}"
    if kind in COMMAND_OR_MEASURED:
        cmd, measured = COMMAND_OR_MEASURED[kind]
        commanded = bool(_CMD.search(topic))
        return (cmd if commanded else measured), f"standard message type {kind} on a {'command' if commanded else 'measurement'} topic"
    if kind in GENERIC_TYPES:
        for suffix, role in MAVROS_TOPIC_NAMES.items():
            if topic.rstrip("/").endswith(suffix):
                return role, f"generic type {kind} on the standard mavros topic '{suffix}'"
        return None, f"generic type {kind}: what it measures is not stated by the type, and '{topic}' is not a standard topic name"
    return None, f"message type {kind or '(none)'} is not one Veyra has a role for"
