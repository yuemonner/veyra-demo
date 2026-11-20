"""SYNTHETIC series-pack model for testing the cell-physics checks. Every recording built here is labelled synthetic.

Each cell: V = OCV(SOC_cell) - I * R0 - V_rc, with one RC pair (dV_rc/dt = (I * R1 - V_rc) / tau). The OCV curves below are
INVENTED, smooth and monotonic (one sloped, one with a flat plateau); they are not any real chemistry and must never be
used as one. Faults are injected explicitly (current sensor offset / scale, capacity fade, imbalance, extra resistance,
a cell-voltage sensing step, an estimator correction at rest), so the checks' LOGIC can be tested. This does not show that
the same signatures identify real degradation (external EE advisor's caveat, 2026-11-16).
"""
from __future__ import annotations

import bisect
import copy
import math
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.physchecks.profile import Profile
from app.physchecks.recording import Recording

# INVENTED curves (SOC %, V per cell). "sloped" rises steadily; "flat" has a plateau between 30 and 80 % (1 mV / %).
SYN_OCV = {
    "sloped": {"soc_pct": [0, 5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100],
               "v": [3.00, 3.30, 3.42, 3.55, 3.62, 3.68, 3.74, 3.81, 3.89, 3.97, 4.06, 4.11, 4.18]},
    "flat": {"soc_pct": [0, 5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100],
             "v": [2.80, 3.05, 3.15, 3.22, 3.260, 3.270, 3.280, 3.290, 3.300, 3.310, 3.33, 3.36, 3.45]},
}


def ocv(curve: Dict[str, List[float]], soc: float) -> float:
    xs, ys = curve["soc_pct"], curve["v"]
    s = min(max(soc, xs[0]), xs[-1])
    i = max(1, bisect.bisect_left(xs, s))
    x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
    return y0 + (y1 - y0) * (s - x0) / (x1 - x0)


CELLS_PROFILE: Dict[str, Any] = {
    "schema": "veyra-signal-profile-v1", "id": "synthetic-bev-cells", "version": "1", "synthetic": True,
    "source": "SYNTHETIC series-pack model (generated, not a real vehicle)",
    "machine_class": "battery_electric_vehicle", "conditions": {"has_hv_contactors": True},
    "max_sample_age_s": 1.5, "max_gap_s": 3.0,
    "roles": {
        "pack_current": {"field": "i_pack", "unit": "A", "sign": "discharge_positive", "kind": "measurement", "valid": [-1000, 1000], "max_age_s": 1.5},
        "soc": {"field": "soc", "unit": "%", "kind": "estimate", "estimator": "coulomb_counting", "valid": [0, 100], "resolution": 0.1},
        "pack_voltage": {"field": "v_pack", "unit": "V", "kind": "measurement", "valid": [0, 1000], "max_age_s": 1.5},
        "min_cell_voltage": {"field": "v_cell_min", "unit": "V", "kind": "measurement", "valid": [1.0, 5.0], "max_age_s": 1.5},
        "max_cell_voltage": {"field": "v_cell_max", "unit": "V", "kind": "measurement", "valid": [1.0, 5.0], "max_age_s": 1.5},
        "min_cell_index": {"field": "idx_min", "unit": "", "kind": "state", "valid": [1, 400]},
        "cell_temperature": {"field": "t_cell", "unit": "degC", "kind": "measurement", "valid": [-40, 90]},
    },
    "parameters": {"series_count": {"value": 12, "source": "SYNTHETIC model"}, "capacity_ah": {"value": 50.0, "source": "SYNTHETIC model"}},
}


def cells_profile(*, derived_pack_voltage: bool = False, curve: Optional[str] = None, **params) -> Profile:
    p = copy.deepcopy(CELLS_PROFILE)
    if derived_pack_voltage:
        p["roles"]["pack_voltage"]["derived_from"] = ["cell_voltages"]
    if curve:
        p["parameters"]["ocv_curve"] = {"value": dict(SYN_OCV[curve], temperature_c=25.0, chemistry=f"SYNTHETIC {curve}", verified=True),
                                        "source": "SYNTHETIC model (invented curve)"}
    for k, v in params.items():
        p["parameters"][k] = {"value": v, "source": "test"}
    return Profile(p)


@dataclass
class Pack:
    n: int = 12
    capacity_ah: float = 50.0                      # true capacity of every cell unless overridden
    soc0: float = 70.0
    r0: float = 0.002                              # ohm per cell
    r1: float = 0.0015
    tau: float = 60.0
    curve: str = "sloped"
    cell_capacity: Dict[int, float] = field(default_factory=dict)      # cell index -> true Ah (capacity fade)
    cell_soc0: Dict[int, float] = field(default_factory=dict)          # cell index -> initial SOC (imbalance)
    cell_r0: Dict[int, float] = field(default_factory=dict)            # cell index -> R0 (extra resistance / connection)
    current_offset_a: float = 0.0                  # sensor reads I + offset
    current_scale: float = 1.0                     # sensor reads I * scale
    rated_capacity_ah: Optional[float] = None      # what the BMS counts against (default: capacity_ah)
    sense_step: Optional[Tuple[int, float, float]] = None   # (cell, t, volts): the cell's sensed voltage steps by `volts` at t
    noise_v: float = 0.0008
    noise_i: float = 0.05
    ocv_correction_after_rest_s: Optional[float] = None     # BMS resets SOC to the OCV-implied value after this much rest
    pack_voltage_independent: bool = True
    seed: int = 1


def simulate(pack: Pack, segments: List[Tuple[float, float]], *, rid: str = "syn-cells", dt: float = 1.0) -> Recording:
    """segments: [(duration_s, true current A, discharge positive)]. Returns a SYNTHETIC recording (one window, w1)."""
    rng = random.Random(pack.seed)
    curve = SYN_OCV[pack.curve]
    cap = [pack.cell_capacity.get(i, pack.capacity_ah) for i in range(pack.n)]
    soc = [pack.cell_soc0.get(i, pack.soc0) for i in range(pack.n)]
    r0 = [pack.cell_r0.get(i, pack.r0) for i in range(pack.n)]
    vrc = [0.0] * pack.n
    rated = pack.rated_capacity_ah or pack.capacity_ah
    soc_bms = sum(soc) / pack.n
    rec = Recording(rid, synthetic=True, label=f"SYNTHETIC {rid}", provenance={"generator": "app.physchecks.synthetic_cells"})
    t, row, rest = 0.0, 1, 0.0
    for dur, cur in segments:
        steps = int(round(dur / dt))
        for _ in range(steps):
            for i in range(pack.n):
                soc[i] -= cur * dt / (cap[i] * 3600) * 100
                vrc[i] += dt * (cur * pack.r1 - vrc[i]) / pack.tau
            meas_i = cur * pack.current_scale + pack.current_offset_a + rng.gauss(0, pack.noise_i)
            soc_bms -= meas_i * dt / (rated * 3600) * 100
            rest = rest + dt if abs(cur) < 0.5 else 0.0
            v = [ocv(curve, soc[i]) - cur * r0[i] - vrc[i] for i in range(pack.n)]
            if pack.ocv_correction_after_rest_s and rest >= pack.ocv_correction_after_rest_s and abs(rest - pack.ocv_correction_after_rest_s) < dt / 2:
                mean_v = sum(v) / pack.n                # the estimator jumps to the SOC its (true) curve gives at rest
                lo, hi = 0.0, 100.0
                for _k in range(40):
                    mid = (lo + hi) / 2
                    lo, hi = (mid, hi) if ocv(curve, mid) < mean_v else (lo, mid)
                soc_bms = (lo + hi) / 2
            sensed = [x + rng.gauss(0, pack.noise_v) for x in v]
            if pack.sense_step and t >= pack.sense_step[1]:
                sensed[pack.sense_step[0]] += pack.sense_step[2]
            vp = (sum(v) + rng.gauss(0, pack.noise_v * pack.n ** 0.5)) if pack.pack_voltage_independent else sum(sensed)
            lo_i = min(range(pack.n), key=lambda k: sensed[k])
            row += 1
            for k, val in (("i_pack", meas_i), ("soc", max(0.0, min(100.0, round(soc_bms, 1)))), ("v_pack", vp)):
                rec.add("w1", k, round(t, 3), val, row=row, segment="S1", source="BMS_STATUS", file="synthetic_cells.csv")
            row += 1
            for k, val in (("v_cell_min", sensed[lo_i]), ("v_cell_max", max(sensed)), ("idx_min", lo_i + 1), ("t_cell", 25.0)):
                rec.add("w1", k, round(t + 0.01, 3), val, row=row, segment="S1", source="BMS_CELLS", file="synthetic_cells.csv")
            t += dt
    return rec.finalize()


def drive_then_rest(pack: Pack, *, rid: str = "syn-cells", varied: bool = True, rest_s: float = 1800.0,
                    drive_s: float = 1800.0, base_a: float = 40.0) -> Recording:
    """A discharge (varied current: 10..90 A blocks; or constant `base_a`), then a rest (current interrupted, as at a shutdown)."""
    segs: List[Tuple[float, float]] = [(120.0, 0.0)]
    if varied:
        levels = [20.0, 80.0, 40.0, 90.0, 10.0, 60.0, 30.0, 70.0]
        k = 0
        while sum(d for d, _ in segs) < drive_s:
            segs.append((60.0, levels[k % len(levels)]))
            k += 1
    else:
        segs.append((drive_s, base_a))
    segs.append((rest_s, 0.0))
    return simulate(pack, segs, rid=rid)
