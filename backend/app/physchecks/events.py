"""State transitions over recorded state signals, honest about gaps and window starts.

A transition is a change between two consecutive valid samples of one role. If those samples are further apart than
the profile's max_gap_s, the change happened somewhere inside an unrecorded interval: it is reported with
`time_known=False` and both bounding samples, never placed at a guessed instant. The first sample of each window is an
INITIAL OBSERVATION, not a transition (the state may have been reached long before the recording started).
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from app.physchecks.profile import Profile
from app.physchecks.recording import EvidenceRef, Recording


@dataclass
class Transition:
    role: str
    window: str
    t: float                  # time of the first sample showing the new value
    t_prev: Optional[float]   # time of the last sample showing the old value (None for an initial observation)
    old: Optional[float]
    new: float
    ref: EvidenceRef
    prev_ref: Optional[EvidenceRef]
    time_known: bool
    initial: bool = False

    def as_dict(self, profile: Optional[Profile] = None) -> Dict[str, Any]:
        d = {"role": self.role, "window": self.window, "t": round(self.t, 3),
             "t_prev": None if self.t_prev is None else round(self.t_prev, 3), "from": self.old, "to": self.new,
             "time_known": self.time_known, "initial_observation": self.initial, "evidence": [self.ref.as_dict()]
             + ([self.prev_ref.as_dict()] if self.prev_ref else [])}
        if profile is not None:
            d["to_name"] = profile.state_name(self.role, self.new)
            d["from_name"] = None if self.old is None else profile.state_name(self.role, self.old)
        return d


def transitions(rec: Recording, profile: Profile, role: str, windows: Optional[List[str]] = None) -> List[Transition]:
    out: List[Transition] = []
    if not profile.has_role(role):
        return out
    gap = profile.max_gap_s
    for w in windows or rec.windows:
        s = profile.series(rec, w, role)
        prev = None
        for x in s.samples:
            if prev is None:
                out.append(Transition(role, w, x.t, None, None, x.value, rec.ref(w, s.field, x.index), None, True, True))
            elif x.value != prev.value:
                out.append(Transition(role, w, x.t, prev.t, prev.value, x.value, rec.ref(w, s.field, x.index),
                                      rec.ref(w, s.field, prev.index), (x.t - prev.t) <= gap))
            prev = x
    return out


class Timeline:
    """Sample-and-hold lookup of one role across all windows, refusing values older than max_age."""

    def __init__(self, rec: Recording, profile: Profile, role: str):
        self.rec, self.profile, self.role = rec, profile, role
        self.max_age = profile.max_age(role) if profile.has_role(role) else 0.0
        self.by_window: Dict[str, Any] = {}
        if profile.has_role(role):
            for w in rec.windows:
                s = profile.series(rec, w, role)
                self.by_window[w] = (s, [x.t for x in s.samples])

    def available(self) -> bool:
        return any(s.samples for s, _ in self.by_window.values())

    def at(self, window: str, t: float, max_age: Optional[float] = None):
        """(value, age_s, ref) of the latest sample at or before t, or None if none is fresh enough."""
        if window not in self.by_window:
            return None
        s, ts = self.by_window[window]
        i = bisect.bisect_right(ts, t) - 1
        if i < 0:
            return None
        age = t - ts[i]
        if age > (self.max_age if max_age is None else max_age):
            return None
        x = s.samples[i]
        return x.value, age, self.rec.ref(window, s.field, x.index)

    def extreme(self, window: str, t0: float, t1: float, fn: Callable = min):
        """(value, ref) of the min/max sample in [t0, t1], or None."""
        if window not in self.by_window:
            return None
        s, ts = self.by_window[window]
        a, b = bisect.bisect_left(ts, t0), bisect.bisect_right(ts, t1)
        if a >= b:
            return None
        best = fn(range(a, b), key=lambda i: s.samples[i].value)
        return s.samples[best].value, self.rec.ref(window, s.field, s.samples[best].index)

    def covered(self, window: str, t0: float, t1: float) -> bool:
        """True when samples exist over [t0, t1] with no interval longer than the profile's max gap."""
        if window not in self.by_window:
            return False
        s, ts = self.by_window[window]
        a = bisect.bisect_right(ts, t0) - 1
        b = bisect.bisect_left(ts, t1)
        if a < 0 or b >= len(ts):
            return False
        return all(ts[i + 1] - ts[i] <= self.profile.max_gap_s for i in range(a, b))


OPS = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
       "==": lambda a, b: a == b, "!=": lambda a, b: a != b, "in": lambda a, b: a in b}


def holds(cond: Dict[str, Any], values: Dict[str, Optional[float]]) -> Optional[bool]:
    """Evaluate {role, op, value} or {all: [...]} / {any: [...]}. None when a needed value is unknown."""
    if "all" in cond:
        rs = [holds(c, values) for c in cond["all"]]
        return False if any(r is False for r in rs) else (None if any(r is None for r in rs) else True)
    if "any" in cond:
        rs = [holds(c, values) for c in cond["any"]]
        return True if any(r is True for r in rs) else (None if any(r is None for r in rs) else False)
    v = values.get(cond["role"])
    if v is None:
        return None
    return bool(OPS[cond.get("op", "==")](v, cond["value"]))


def roles_in(cond: Dict[str, Any]) -> List[str]:
    if "all" in cond or "any" in cond:
        return [r for c in cond.get("all") or cond.get("any") for r in roles_in(c)]
    return [cond["role"]]
