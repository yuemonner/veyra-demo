"""Shared helpers for measured series: repeated (sample-and-hold) frames and angles that wrap at ±180°.

Logging pipelines often re-publish the last value at a fixed rate between real sensor updates. A
detector that treats every row as a measurement then sees "events" shorter than the sensor can
produce. And angles such as yaw jump from +180 to -180 without anything happening.
"""
from __future__ import annotations

from typing import List, Sequence, Tuple

REPEAT_SHARE_HOLD = 0.5      # at least this share of consecutive identical values = sample-and-hold stream
WRAP_SPAN_DEG = 300.0        # an angle series spanning more than this, with a jump over 180, wraps


def repeated_share(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    return sum(1 for a, b in zip(values, values[1:]) if a == b) / (len(values) - 1)


def distinct_updates(points: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Keep the first sample of each run of identical consecutive values."""
    out: List[Tuple[float, float]] = []
    for t, v in points:
        if not out or v != out[-1][1]:
            out.append((t, v))
    return out


def is_angle(unit) -> bool:
    return unit in ("deg", "degree", "degrees")


def wraps(values: Sequence[float], unit) -> bool:
    if not is_angle(unit) or len(values) < 2:
        return False
    return max(values) - min(values) > WRAP_SPAN_DEG and any(abs(b - a) > 180.0 for a, b in zip(values, values[1:]))


def unwrap(values: Sequence[float]) -> List[float]:
    """Remove ±360° jumps so a turn through ±180° reads as continuous motion."""
    out: List[float] = []
    offset = 0.0
    for i, v in enumerate(values):
        if i:
            step = v - values[i - 1]
            if step > 180.0:
                offset -= 360.0
            elif step < -180.0:
                offset += 360.0
        out.append(v + offset)
    return out


def angle_diff(a: float, b: float) -> float:
    """Signed smallest difference a − b in degrees, in (−180, 180]."""
    d = (a - b + 180.0) % 360.0 - 180.0
    return 180.0 if d == -180.0 else d


def circular_mean(values: Sequence[float]) -> float:
    import math
    s = sum(math.sin(math.radians(v)) for v in values)
    c = sum(math.cos(math.radians(v)) for v in values)
    return math.degrees(math.atan2(s, c))
