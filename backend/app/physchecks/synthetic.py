"""SYNTHETIC battery recordings for tests and the offline demo. Every recording built here is labelled synthetic.

These are generated from simple, stated rules (coulomb counting, a scripted correction, a scripted protection
sequence). They exercise the checks' logic; they are not, and must never be presented as, any customer's data.
Message names are neutral ("BMS_STATUS", "BMS_CELLS", "VCU_STATE"), not a real protocol.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from app.physchecks.profile import Profile
from app.physchecks.recording import Recording

PROFILE: Dict[str, Any] = {
    "schema": "veyra-signal-profile-v1", "id": "synthetic-bev", "version": "1", "synthetic": True,
    "source": "SYNTHETIC test profile (generated, not a real vehicle)",
    "machine_class": "battery_electric_vehicle", "conditions": {"has_hv_contactors": True},
    "max_sample_age_s": 0.5, "max_gap_s": 1.0,
    "roles": {
        "pack_current": {"field": "i_pack", "unit": "A", "sign": "discharge_positive", "kind": "measurement", "valid": [-1000, 1000], "max_age_s": 0.5},
        "soc": {"field": "soc", "unit": "%", "kind": "estimate", "estimator": "coulomb_counting", "valid": [0, 100], "resolution": 0.4},
        "rated_capacity": {"field": "cap_ah", "unit": "Ah", "kind": "reported_parameter", "valid": [1, 1000]},
        "min_cell_voltage": {"field": "v_cell_min", "unit": "V", "kind": "measurement", "valid": [1.0, 5.0], "placeholders": [0, 65.535]},
        "max_cell_voltage": {"field": "v_cell_max", "unit": "V", "kind": "measurement", "valid": [1.0, 5.0], "placeholders": [0, 65.535]},
        "min_cell_index": {"field": "idx_min", "unit": "", "kind": "state", "valid": [1, 400]},
        "bms_fault_level": {"field": "fault_raw", "unit": "", "kind": "state", "map": {"0": 0, "1": 1, "2": 2, "3": 3, "4": 4}},
        "bms_state": {"field": "bms_state", "unit": "", "kind": "state", "enum": {"0": "HV on", "3": "precharging", "4": "HV allowed", "5": "HV not allowed"}},
        "main_positive_closed": {"field": "k_pos", "unit": "", "kind": "state"},
        "main_negative_closed": {"field": "k_neg", "unit": "", "kind": "state"},
        "precharge_closed": {"field": "k_pre", "unit": "", "kind": "state"},
        "power_state": {"field": "pwr", "unit": "", "kind": "state", "enum": {"3": "ACC", "4": "READY", "0": "power-down"}},
        "dcdc_state": {"field": "dcdc", "unit": "", "kind": "state", "enum": {"0": "off", "1": "working"}},
        "speed": {"field": "v_mps", "unit": "m/s", "kind": "measurement", "valid": [-50, 50]},
        "soc_recal_flag": {"field": "soc_recal", "unit": "", "kind": "state"},
    },
    "parameters": {},
}


def profile(**overrides) -> Profile:
    p = copy.deepcopy(PROFILE)
    for k, v in overrides.items():
        p[k] = v
    return Profile(p)


class Builder:
    """Script a recording step by step at a fixed message rate."""

    def __init__(self, rid: str = "synthetic", dt: float = 0.1):
        self.rec = Recording(rid, synthetic=True, label=f"SYNTHETIC {rid}", provenance={"generator": "app.physchecks.synthetic"})
        self.dt = dt
        self.row = 1

    def frame(self, window: str, t: float, msg: str, **fields: Optional[float]) -> None:
        self.row += 1
        for k, v in fields.items():
            if v is not None:
                self.rec.add(window, k, t, v, row=self.row, segment="S1", source=msg, file=f"synthetic_{window}.csv")

    def done(self) -> Recording:
        return self.rec.finalize()


def discharge(b: Builder, window: str, t0: float, t1: float, *, current: float, cap_ah: float = 100.0, soc0: float = 50.0,
              res: float = 0.4, sign: float = 1.0, soc_bias=None, extra: Optional[Dict[str, Any]] = None,
              skip: Optional[List[Tuple[float, float]]] = None, cell0: float = 3.2, cell_slope: float = 0.0) -> float:
    """Coulomb-counted SOC (quantised to `res`) under a constant discharge current; returns the SOC at t1.
    `soc_bias(t)` adds a scripted estimator change (pp). `skip` drops frames inside the given intervals (gaps)."""
    q = 0.0
    t = t0
    n = 0
    while t <= t1 + 1e-9:
        if not any(a <= t < b for a, b in skip or []):
            soc_true = soc0 - q / (cap_ah * 3600) * 100
            soc = soc_true + (soc_bias(t) if soc_bias else 0.0)
            soc_q = max(0.0, round(round(soc / res) * res, 3))
            b.frame(window, round(t, 3), "BMS_STATUS", i_pack=sign * current, soc=soc_q, cap_ah=cap_ah,
                    fault_raw=(extra or {}).get("fault_raw", 0))
            b.frame(window, round(t + 0.02, 3), "BMS_CELLS", v_cell_min=round(cell0 - cell_slope * (t - t0), 4),
                    v_cell_max=round(cell0 + 0.01 - cell_slope * (t - t0), 4), idx_min=20)
        q += current * b.dt
        t = t0 + (n := n + 1) * b.dt
    return soc0 - q / (cap_ah * 3600) * 100


def healthy(rid: str = "syn-healthy") -> Recording:
    b = Builder(rid)
    discharge(b, "w1", 0, 600, current=40.0, cap_ah=100.0, soc0=80.0)
    return b.done()


def abrupt_correction(rid: str = "syn-correction", *, at: float = 300.0, drop_pp: float = 30.0, over_s: float = 30.0,
                      recal_flag: bool = False) -> Recording:
    """Constant 6 A discharge; between `at` and `at+over_s` the estimator pulls SOC down by `drop_pp` (no current change)."""
    b = Builder(rid)

    def bias(t):
        return -drop_pp * min(1.0, max(0.0, (t - at) / over_s))
    discharge(b, "w1", 0, 600, current=6.0, cap_ah=100.0, soc0=52.0, soc_bias=bias)
    if recal_flag:
        for k in range(0, 31):
            b.frame("w1", at + k, "BMS_DIAG", soc_recal=1 if k < over_s else 0)
    return b.done()


def protection_trip(rid: str = "syn-trip", *, contactor_delay_s: float = 3.0, dcdc_delay_s: float = 2.0,
                    gap: Optional[Tuple[float, float]] = None) -> Recording:
    """Level 1 -> 2 -> 3 escalation then HV-not-allowed, READY->ACC, DC-DC off and both contactors open."""
    b = Builder(rid)
    T3 = 100.0
    t = 0.0
    while t <= 200.0 + 1e-9:
        tt = round(t, 3)
        if gap and gap[0] <= tt < gap[1]:
            t += 0.1
            continue
        lvl = 0 if tt < 20 else (1 if tt < 60 else (2 if tt < T3 else 3))
        state = 5 if tt >= T3 else 0
        closed = 0 if tt >= T3 + contactor_delay_s else 1
        b.frame("w1", tt, "BMS_STATUS", fault_raw=lvl, bms_state=state, i_pack=7.0 if closed else 0.0, soc=40.0, cap_ah=100.0)
        b.frame("w1", round(tt + 0.01, 3), "BMS_RELAYS", k_pos=closed, k_neg=closed, k_pre=0,
                v_cell_min=round(2.9 - 0.004 * tt, 4) if closed else 2.6, v_cell_max=3.0)
        b.frame("w1", round(tt + 0.03, 3), "VCU_STATE", pwr=3 if tt >= T3 + 0.2 else 4, dcdc=0 if tt >= T3 + dcdc_delay_s else 1, v_mps=0.0)
        t += 0.1
    return b.done()


def incident(rid: str = "syn-incident", *, cap_ah: float = 100.0) -> Recording:
    """A complete SYNTHETIC stationary under-voltage incident across three windows on one time axis:
    'early'  : no alarm, SOC follows coulomb counting at 8 A
    'fault'  : cells sag pack-wide (the lowest cell faster), level 1 -> 2, then the estimator pulls SOC down 35 pp in
               40 s with the current unchanged, level 3 -> HV not allowed, READY->ACC, DC-DC off, contactors open +3 s,
               then rest recovery
    'repeat' : two reclosures after the fault reset in unrecorded time, each discharging below the original trip level
    Shapes are scripted from stated rules; the numbers are not any customer's."""
    b = Builder(rid)
    I = 8.0

    def cells(t):                                # piecewise pack-wide sag: 2.95 V at 0 s, 2.75 V at 600 s, 2.50 V near 800 s
        if t < 0:
            lo = 3.15 - 0.20 * (t + 3000) / 3000
        elif t < 600:
            lo = 2.95 - 0.20 * t / 600
        else:
            lo = 2.75 - 0.25 * (t - 600) / 200
        spread = 0.012 + max(0.0, t + 3000) / 3800 * 0.29   # the lowest cell (index 7) drifts away from the rest
        return lo, lo + spread

    def level(lo):
        return 0 if lo > 2.95 else (1 if lo > 2.75 else (2 if lo > 2.50 else 3))
    q = 0.0
    soc0 = 50.0
    t, trip, opened = -2400.0, None, None
    while t <= 1200.0 + 1e-9:
        w = "early" if t < -900 else ("fault" if t >= -500 else None)
        if w is None:                            # unrecorded interval: charge still flows
            q += I * 0.1
            t = round(t + 0.1, 3)
            continue
        hv = opened is None or t < opened
        cur = I if hv else 0.0
        lo, hi = cells(t) if hv else (2.70, 2.90)
        lvl = level(lo) if hv else 3
        if trip is None and lvl == 3:
            trip, opened = t, round(t + 3.0, 3)
        bias = 0.0 if t < 500 else -30.0 * min(1.0, (t - 500) / 25.0)
        soc = max(0.0, round(round((soc0 - q / (cap_ah * 3600) * 100 + bias) / 0.4) * 0.4, 3))
        b.frame(w, t, "BMS_STATUS", i_pack=cur, soc=soc, cap_ah=cap_ah, fault_raw=lvl, bms_state=5 if trip is not None else 0)
        b.frame(w, round(t + 0.02, 3), "BMS_CELLS", v_cell_min=round(lo, 4), v_cell_max=round(hi, 4), idx_min=7,
                k_pos=1 if hv else 0, k_neg=1 if hv else 0, k_pre=0)
        b.frame(w, round(t + 0.04, 3), "VCU_STATE", pwr=3 if trip is not None and t >= trip + 0.2 else 4,
                dcdc=0 if trip is not None and t >= trip + 2.0 else 1, v_mps=0.0)
        q += cur * 0.1
        t = round(t + 0.1, 3)
    # repeat window: fault already reset, two closures that trip again below the original trip level
    t = 2200.0
    for cyc in range(2):
        for k in range(10):
            b.frame("repeat", round(t, 3), "BMS_STATUS", i_pack=0.0, soc=1.2, cap_ah=cap_ah, fault_raw=0, bms_state=4)
            b.frame("repeat", round(t + 0.02, 3), "BMS_CELLS", v_cell_min=2.70, v_cell_max=2.90, idx_min=7, k_pos=0, k_neg=0, k_pre=0)
            t += 0.1
        lo = 2.68
        trip_r = None
        while True:
            lvl = 2 if lo > 2.50 else 3
            if trip_r is None and lvl == 3:
                trip_r = t
            hv = trip_r is None or t < trip_r + 3.0
            b.frame("repeat", round(t, 3), "BMS_STATUS", i_pack=I if hv else 0.0, soc=1.2, cap_ah=cap_ah, fault_raw=lvl, bms_state=5 if trip_r else 0)
            b.frame("repeat", round(t + 0.02, 3), "BMS_CELLS", v_cell_min=round(lo, 4), v_cell_max=2.80, idx_min=7, k_pos=1 if hv else 0, k_neg=1 if hv else 0, k_pre=0)
            lo -= 0.003 if hv else 0.0
            t += 0.1
            if trip_r is not None and t > trip_r + 8:
                break
        t += 60.0                                # gap: the fault resets in unrecorded time
    return b.done()


def restart_cycles(rid: str = "syn-restarts", *, cycles: int = 3, reset_in_gap: bool = True) -> Recording:
    """A protective trip, then `cycles` reclosures, each discharging below the original trip voltage and tripping again.
    Resets happen inside recording gaps when reset_in_gap is True."""
    b = Builder(rid)
    t = 0.0
    v = 2.60

    def emit(win, tt, lvl, closed, cell, state=0):
        b.frame(win, round(tt, 3), "BMS_STATUS", fault_raw=lvl, bms_state=state, i_pack=7.0 if closed else 0.0, soc=1.2, cap_ah=100.0)
        b.frame(win, round(tt + 0.01, 3), "BMS_RELAYS", k_pos=closed, k_neg=closed, k_pre=0, v_cell_min=round(cell, 4), v_cell_max=2.8)
    # window A: first trip at t=50 (level 3), contactors open at 53
    while t <= 100:
        lvl = 2 if t < 50 else 3
        closed = 1 if t < 53.0 else 0
        emit("a", t, lvl, closed, 2.64 - 0.0028 * min(t, 50) if closed else 2.70, 5 if t >= 50 else 0)
        t += 0.1
    # window B: starts later with fault already reset (reset unobserved)
    t = 200.0
    for c in range(cycles):
        if reset_in_gap:          # the fault already reads 0 when the record resumes: the reset itself is not recorded
            for k in range(10):
                emit("b", t, 0, 0, 2.70)
                t += 0.1
        else:                     # the reset is recorded: level 3 -> 0 while open
            for k in range(20):
                emit("b", t, 3 if k < 10 else 0, 0, 2.70)
                t += 0.1
        # closed, discharging below the original trip level, then trip
        cell = 2.68
        while True:
            lvl = 2 if cell > 2.53 else 3
            emit("b", t, lvl, 1, cell)
            cell -= 0.004
            t += 0.1
            if cell < 2.46:
                break
        trip = t
        while t < trip + 3.0:
            emit("b", t, 3, 1, cell, 5)
            t += 0.1
        while t < trip + 6:
            emit("b", t, 3, 0, 2.7, 5)
            t += 0.1
        t += 30.0 if reset_in_gap else 0.0   # unrecorded interval in which the fault resets
    return b.done()
