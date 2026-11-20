"""System-level audit: the machine as a system, not only the battery.

An engineering method encoded from an independent system-level fault assessment (2026-11-18; attribution in the private
records, not here). Before any cause is argued, it reconstructs what the WHOLE machine did and how far each recorded signal
can be trusted:

  symptom_vs_record     the reported symptom checked against the recorded vehicle state (drive requests, speed, gear,
                        emergency stop). "Could not drive" and "was stationary and never asked to drive" are different.
  control_sequence      the escalation, controller states (BMS state, power state, DC/DC), contactor feedback and current
                        around the protective shutdown, joined on the latest sample at or before each instant, with sample
                        ages, never across a logging gap. Includes whether current kept flowing after a load was switched off.
  inferred_thresholds   each fault-level escalation against the monitored signal: the round threshold it most likely uses
                        and the delay after the first crossing. Always labelled INFERRED: the specification decides.
  signal_independence   which signals are relays of others (agreement is not a second measurement), which fields cannot
                        measure what their name says (e.g. a post-contactor voltage equal to pack voltage with contactors
                        open), whether current reads exactly zero when open (an offset bound, unless clamped), and which
                        fields are constant or saturated (no information).
  load_sag              the apparent pack resistance at large current steps, and the most voltage load sag can explain at the
                        recorded current against the recorded voltage decline.
  charge_alignment      the lowest and highest cell aligned by CHARGE removed: a constant offset means the weakest cell sets
                        the TIMING of the cutoff; its size against the SOC discrepancy says whether it can explain the SIZE.
  restart_authorisation for every HV closure after a protective shutdown: fault level and controller state at the closure,
                        whether an escalation was already reported, the weakest cell against the voltage at which the first
                        escalation fired. Interpreted with customer-confirmed fault-level semantics when provided; without
                        them it is a question for the manufacturer, never a violation.

Every item: a statement, values, evidence references, and whether it is observed, computed or inferred.
Domain-neutral roles; anything battery-specific is skipped when the profile does not bind the role.
"""
from __future__ import annotations

import bisect
import math
import statistics
from typing import Any, Dict, List, Optional, Tuple

from app.physchecks.events import Timeline, transitions
from app.physchecks.profile import Profile
from app.physchecks.recording import Recording

AUDIT_VERSION = "system-audit-v1"
KIND_OBSERVED, KIND_COMPUTED, KIND_INFERRED = "observed", "computed", "inferred"


def _item(iid: str, statement: str, kind: str, value: Any = None, evidence: Optional[List[Dict[str, Any]]] = None,
          *, limits: Optional[List[str]] = None, flag: Optional[str] = None) -> Dict[str, Any]:
    d = {"id": iid, "statement": statement, "kind": kind, "value": value, "evidence": (evidence or [])[:6], "limits": limits or []}
    if flag:
        d["flag"] = flag
    return d


def _name(profile: Profile, role: str, v: Optional[float]) -> str:
    if v is None:
        return "?"
    n = profile.state_name(role, v)
    return f"{n} ({v:g})" if n else f"{v:g}"


def _samples(rec: Recording, profile: Profile, role: str, unit: Optional[str] = None) -> List[Tuple[str, Any]]:
    if not profile.has_role(role):
        return []
    out = []
    for w in rec.windows:
        s = profile.series(rec, w, role, unit)
        out += [(w, x) for x in s.samples]
    return out


def _sign(profile: Profile) -> float:
    return 1.0 if profile.role("pack_current").get("sign") == "discharge_positive" else -1.0


# ------------------------------------------------------------------ symptom against record
VEHICLE_STATE_ROLES = ("speed", "throttle", "drive_enabled", "gear", "estop_state")    # constant zero here IS information (stationary)
NEUTRAL_NAMES = ("n", "neutral", "空挡", "p", "park")


def symptom_vs_record(rec: Recording, profile: Profile, reported: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Was the machine asked to move, and did it move? Uses drive_enabled, throttle, speed, gear, estop_state when bound."""
    have = [r for r in ("drive_enabled", "throttle", "speed", "gear", "estop_state") if profile.has_role(r)]
    if not have:
        return None
    v: Dict[str, Any] = {}
    ev: List[Dict[str, Any]] = []
    if profile.has_role("drive_enabled"):
        s = _samples(rec, profile, "drive_enabled")
        on = [(w, x) for w, x in s if x.value != 0]
        v["drive_enabled_frames"], v["drive_enabled_on"] = len(s), len(on)
        if on:
            ev.append(rec.ref(on[0][0], profile.role("drive_enabled")["field"], on[0][1].index).as_dict())
    if profile.has_role("throttle"):
        s = _samples(rec, profile, "throttle")
        v["throttle_frames"], v["throttle_nonzero"] = len(s), sum(1 for _, x in s if abs(x.value) > 1e-9)
    if profile.has_role("speed"):
        s = _samples(rec, profile, "speed", "m/s")
        v["speed_frames"], v["moving_frames"] = len(s), sum(1 for _, x in s if abs(x.value) > 0.05)
        v["max_speed_mps"] = round(max((abs(x.value) for _, x in s), default=0.0), 3)
    if profile.has_role("gear"):
        s = _samples(rec, profile, "gear")
        names = [str(profile.state_name("gear", x.value) or "").strip().lower() for _, x in s]
        v["gear_frames"], v["gear_not_neutral"] = len(s), sum(1 for n in names if n and n not in NEUTRAL_NAMES)
    if profile.has_role("estop_state"):
        by_w: Dict[str, List[int]] = {}
        for w, x in _samples(rec, profile, "estop_state"):
            by_w.setdefault(w, []).append(int(x.value != 0))
        v["estop_active_share_by_window"] = {w: round(sum(a) / len(a), 3) for w, a in by_w.items() if a}
    requested = (v.get("drive_enabled_on", 0) > 0) or (v.get("throttle_nonzero", 0) > 0) or (v.get("gear_not_neutral", 0) > 0)
    moved = v.get("moving_frames", 0) > 0
    est = v.get("estop_active_share_by_window") or {}
    parts = []
    if not requested and not moved:
        parts.append("No drive request and no movement in any recorded frame" +
                     (f" (drive enable {v.get('drive_enabled_frames', 0):,} frames, throttle, speed and gear all at rest)" if "drive_enabled_frames" in v else "") + ".")
    else:
        parts.append(f"Drive requests recorded: {requested}; movement recorded: {moved}.")
    if est:
        always = [w for w, sh in est.items() if sh >= 0.999]
        if always:
            parts.append("The emergency stop was active throughout " + ", ".join(always) + ".")
    verdict = None
    if reported:
        r = reported.lower()
        if any(k in r for k in ("drive", "move", "行驶", "开不", "无法")) and not requested and not moved:
            verdict = ("The record does not show the reported symptom as stated: no drive request was refused, because none was made. "
                       "The question becomes why a stationary machine reached a protective state, not why it would not drive.")
            parts.append(verdict)
    return _item("symptom_vs_record", " ".join(parts), KIND_OBSERVED, {**v, "drive_requested": requested, "moved": moved, "reported": reported,
                                                                       "reconciles_with_report": None if not reported else verdict is None}, ev)


# ------------------------------------------------------------------ control sequence around the protective shutdown
SEQUENCE_ROLES = ("bms_fault_level", "vcu_bms_fault_level", "bms_state", "power_state", "dcdc_state", "main_positive_closed",
                  "main_negative_closed", "battery_code")
OFF_NAMES = ("off", "关闭", "disabled", "0")


def control_sequence(rec: Recording, profile: Profile, checks: Dict[str, Dict[str, Any]], *, before_s: float = 15.0, after_s: float = 15.0) -> Optional[Dict[str, Any]]:
    sds = (((checks.get("restart_eligibility") or {}).get("windows") or {}).get("protective_shutdowns") or {}).get("items") or []
    if not sds:
        return None
    sd = sds[0]
    w, t_open, t_trig = sd["window"], sd["t"], sd["trigger_t"]
    t0, t1 = t_trig - before_s, t_open + after_s
    rows: List[Dict[str, Any]] = []
    for role in SEQUENCE_ROLES:
        if not profile.has_role(role):
            continue
        for tr in transitions(rec, profile, role, [w]):
            if tr.initial or not (t0 <= tr.t <= t1):
                continue
            rows.append({"t": round(tr.t, 3), "role": role, "from": _name(profile, role, tr.old), "to": _name(profile, role, tr.new),
                         "time_known": tr.time_known, "evidence": [tr.ref.as_dict()]})
    rows.sort(key=lambda r: (r["t"], r["role"]))
    cur = Timeline(rec, profile, "pack_current") if profile.has_role("pack_current") else None
    sgn = _sign(profile) if cur else 1.0
    findings: List[Dict[str, Any]] = []
    vals: Dict[str, Any] = {"trigger_t": t_trig, "open_t": t_open, "events": rows}
    if cur and cur.available():
        c_trig = cur.at(w, t_trig - 1e-6, max_age=max(cur.max_age, 1.0))
        vals["current_before_trigger_a"] = None if c_trig is None else round(sgn * c_trig[0], 2)
        # a load switched off while current kept flowing
        for r in rows:
            if r["role"] == "dcdc_state" and str(r["to"]).split(" ")[0].lower() in OFF_NAMES:
                c_after = cur.at(w, r["t"] + 0.5, max_age=max(cur.max_age, 1.0))
                if c_after is not None and sgn * c_after[0] > 1.0 and r["t"] < t_open:
                    vals["current_after_dcdc_off_a"] = round(sgn * c_after[0], 2)
                    findings.append(_item("load_not_only_dcdc",
                                          f"The DC/DC reported off at t={r['t']} s, yet {sgn * c_after[0]:.1f} A still flowed until the contactors opened: "
                                          "the continuous load is not only the DC/DC.", KIND_OBSERVED, round(sgn * c_after[0], 2),
                                          r["evidence"] + [c_after[2].as_dict()]))
                break
        z = None
        s = profile.series(rec, w, "pack_current", "A")
        for x in s.samples:
            if x.t > t_open and abs(x.value) < 1e-9:
                z = x
                break
        if z is not None:
            vals["current_zero_t"] = round(z.t, 3)
    lines = [f"t={r['t']} s  {r['role']}: {r['from']} -> {r['to']}" + ("" if r["time_known"] else " (inside a logging gap)") for r in rows]
    st = (f"Around the protective shutdown in {w} (escalation t={t_trig} s, contactors open t={t_open} s): " + "; ".join(lines[:14])
          + (" ..." if len(lines) > 14 else "") + ". Order is consistent with a protective response; order is not proof of cause.")
    out = _item("control_sequence", st, KIND_OBSERVED, vals, [e for r in rows[:6] for e in r["evidence"]],
                limits=["controller states are the controllers' reports, not physical measurements",
                        "values from different messages are joined on the latest sample at or before each instant"])
    out["findings"] = findings
    return out


# ------------------------------------------------------------------ thresholds inferred from crossings
def inferred_thresholds(rec: Recording, profile: Profile, *, signal: str = "min_cell_voltage", grid: float = 0.05,
                        direction: str = "falling") -> Optional[Dict[str, Any]]:
    """For each time-known escalation of the fault level: the monitored signal's value then, the round threshold on `grid`
    it most likely uses (the next grid value on the far side), and the delay since the signal first crossed it."""
    if not (profile.has_role("bms_fault_level") and profile.has_role(signal)):
        return None
    tl = Timeline(rec, profile, signal)
    if not tl.available():
        return None
    rows = []
    seen_levels = set()
    for tr in transitions(rec, profile, "bms_fault_level"):
        if tr.initial or tr.old is None or tr.new <= tr.old or not tr.time_known or tr.new in seen_levels:
            continue
        seen_levels.add(tr.new)
        at = tl.at(tr.window, tr.t, max_age=max(tl.max_age, 1.0))
        if at is None:
            continue
        v = at[0]
        thr = (math.ceil(round(v / grid, 6)) * grid) if direction == "falling" else (math.floor(round(v / grid, 6)) * grid)
        s, ts = tl.by_window[tr.window]
        i1 = bisect.bisect_right(ts, tr.t)
        first = None
        for x in s.samples[:i1]:
            if (x.value <= thr + 1e-9) if direction == "falling" else (x.value >= thr - 1e-9):
                first = x
                break
        if first is None:
            continue
        rows.append({"level": tr.new, "t": round(tr.t, 3), "signal_value": round(v, 4), "inferred_threshold": round(thr, 3),
                     "first_crossing_t": round(first.t, 3), "delay_s": round(tr.t - first.t, 2),
                     "evidence": [tr.ref.as_dict(), rec.ref(tr.window, s.field, first.index).as_dict()]})
    if not rows:
        return None
    st = "Inferred from the record (the specification decides): " + "; ".join(
        f"level {r['level']:g} at t={r['t']} s with {signal} {r['signal_value']} -> threshold about {r['inferred_threshold']} "
        f"(first crossed {r['delay_s']} s earlier)" for r in rows) + "."
    return _item("inferred_thresholds", st, KIND_INFERRED, {"signal": signal, "grid": grid, "rows": rows},
                 [e for r in rows for e in r["evidence"]][:6],
                 limits=[f"assumes round thresholds on a {grid:g} grid and a fixed delay; hysteresis and filtering are not visible"])


# ------------------------------------------------------------------ signal independence
def _paired_diffs(rec: Recording, profile: Profile, a: str, b: str, *, max_age: float = 0.5) -> List[float]:
    tb = Timeline(rec, profile, b)
    out = []
    for w in rec.windows:
        for x in profile.series(rec, w, a).samples:
            y = tb.at(w, x.t, max_age=max_age)
            if y is not None:
                out.append(abs(x.value - y[0]))
    return out


def signal_independence(rec: Recording, profile: Profile) -> Optional[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    # relays declared in the profile
    for role, spec in profile.roles.items():
        src = spec.get("same_source_as")
        if src and profile.has_role(src):
            try:
                d = _paired_diffs(rec, profile, role, src)
            except Exception:
                d = []
            if d:
                d.sort()
                med, p99 = d[len(d) // 2], d[min(len(d) - 1, int(0.99 * len(d)))]
                items.append(_item(f"relay:{role}", f"{role} is a relay of {src} (declared; |difference| median {med:.3g}, 99th percentile {p99:.3g} over "
                                   f"{len(d):,} paired samples, the rest is transmission lag at changes): agreement between them is not a second measurement.",
                                   KIND_COMPUTED, {"role": role, "source": src, "median_abs_diff": round(med, 4), "p99_abs_diff": round(p99, 4),
                                                   "max_abs_diff": round(d[-1], 4), "pairs": len(d)}))
    # contactor state and the post-contactor voltage / current while open
    if profile.has_role("main_positive_closed") and profile.has_role("main_negative_closed"):
        pos, neg = Timeline(rec, profile, "main_positive_closed"), Timeline(rec, profile, "main_negative_closed")

        def is_open(w, t):
            a, b = pos.at(w, t, max_age=2.0), neg.at(w, t, max_age=2.0)
            return a is not None and b is not None and a[0] == 0 and b[0] == 0

        if profile.has_role("pack_current"):
            n = z = 0
            for w in rec.windows:
                for x in profile.series(rec, w, "pack_current", "A").samples:
                    if is_open(w, x.t):
                        n += 1
                        z += abs(x.value) < 1e-9
            if n >= 20:
                res = profile.role("pack_current").get("resolution")
                if z == n:
                    items.append(_item("current_when_open", f"In all {n:,} current samples with both contactors reported open, current reads exactly 0: "
                                       f"a current-sensor offset is below the resolution{f' ({res} A)' if res else ''}, unless the BMS clamps the reading when open.",
                                       KIND_COMPUTED, {"frames": n, "exact_zero": z, "offset_bound_a": res},
                                       limits=["a clamp to zero when open would make this uninformative: ask the supplier"]))
                else:
                    items.append(_item("current_when_open", f"With both contactors reported open, {n - z:,} of {n:,} current samples are not zero: "
                                       "either a sensor offset or current flowing despite the reported open state.", KIND_COMPUTED,
                                       {"frames": n, "exact_zero": z}, flag="review"))
        if profile.has_role("post_contactor_voltage") and profile.has_role("pack_voltage"):
            pv = Timeline(rec, profile, "pack_voltage")
            n = eq = n_open = eq_open = 0
            for w in rec.windows:
                for x in profile.series(rec, w, "post_contactor_voltage", "V").samples:
                    y = pv.at(w, x.t, max_age=1.0)
                    if y is None:
                        continue
                    same = abs(x.value - y[0]) <= 0.051
                    n += 1
                    eq += same
                    if is_open(w, x.t):
                        n_open += 1
                        eq_open += same
            if n >= 20:
                if n_open and eq_open == n_open:
                    items.append(_item("post_contactor_field", f"The post-contactor voltage equals pack voltage in {eq:,} of {n:,} samples, including all "
                                       f"{n_open:,} with the contactors reported open: this field does not measure behind the contactors and cannot "
                                       "confirm their state. Treat the HV bus as live until measured.", KIND_COMPUTED,
                                       {"frames": n, "equal": eq, "open_frames": n_open, "equal_open": eq_open}, flag="safety"))
    # constant (or saturated) fields: no information
    for role, spec in profile.roles.items():
        if spec.get("kind") in ("state", "command", "estimate") or role.endswith("_closed") or role.endswith("_index") or role in VEHICLE_STATE_ROLES:
            continue
        vals = []
        for w in rec.windows:
            vals += [x.value for x in profile.series(rec, w, role).samples]
        if len(vals) >= 50 and max(vals) == min(vals) and not (spec.get("kind") == "reported_parameter" and vals[0] != 0):
            sat = spec.get("saturation")
            items.append(_item(f"constant:{role}", f"{role} reads {vals[0]:g} in all {len(vals):,} samples" +
                               (" (its saturation value)" if sat is not None and float(sat) == vals[0] else "") +
                               ": it carries no information in this record and is not evidence either way.", KIND_COMPUTED,
                               {"role": role, "value": vals[0], "samples": len(vals)}))
    if not items:
        return None
    return {"id": "signal_independence", "items": items,
            "statement": " ".join(i["statement"] for i in items[:5])}


# ------------------------------------------------------------------ load sag
def load_sag(rec: Recording, profile: Profile, checks: Dict[str, Dict[str, Any]], facts: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not (profile.has_role("pack_current") and profile.has_role("pack_voltage")):
        return None
    sds = (((checks.get("restart_eligibility") or {}).get("windows") or {}).get("protective_shutdowns") or {}).get("items") or []
    if not sds:
        return None
    sd = sds[0]
    w, t = sd["window"], sd["t"]
    cur, pv = Timeline(rec, profile, "pack_current"), Timeline(rec, profile, "pack_voltage")
    i0 = cur.at(w, t - 1e-3, max_age=2.0)
    v_pre = pv.at(w, t - 1e-3, max_age=2.0)
    v_post = pv.at(w, t + 0.6, max_age=1.0)
    if not (i0 and v_pre and v_post) or abs(i0[0]) < 1.0:
        return None
    sgn = _sign(profile)
    di = abs(i0[0])
    dv = v_post[0] - v_pre[0]
    r_app = dv / di
    res = profile.role("pack_voltage").get("resolution")
    first_v = None
    for w0 in rec.windows:
        s = profile.series(rec, w0, "pack_voltage", "V")
        if s.samples:
            first_v = s.samples[0]
            break
    decline = None if first_v is None else first_v.value - v_pre[0]
    load = (facts.get("load") or {}).get("value") or {}
    i_typ = load.get("p95") or di
    sag = abs(r_app) * i_typ
    st = (f"At the opening ({w}, t={t} s) current fell {di:.1f} A -> 0 and pack voltage rose {dv:.2f} V: apparent resistance "
          f"{1000 * abs(r_app):.0f} mΩ (includes polarisation{f'; {res} V resolution alone is {1000 * float(res) / di:.0f} mΩ' if res else ''}). "
          f"At up to {i_typ:.1f} A that is at most {sag:.2f} V of load sag" +
          (f", against a {decline:.1f} V decline from the first record: load sag does not explain the decline." if decline and decline > 3 * sag else "."))
    return _item("load_sag", st, KIND_COMPUTED, {"delta_i_a": round(sgn * di, 2), "delta_v": round(dv, 3), "r_apparent_mohm": round(1000 * abs(r_app), 1),
                                                 "max_sag_v": round(sag, 3), "decline_v": None if decline is None else round(decline, 2),
                                                 "explains_decline": None if decline is None else decline <= 3 * sag},
                 [v_pre[2].as_dict(), v_post[2].as_dict(), i0[2].as_dict()])


# ------------------------------------------------------------------ does a switched load pass the current sensor?
ON_NAMES = ("working", "on", "工作", "1")


def load_transitions(rec: Recording, profile: Profile, *, state_role: str = "dcdc_state", half_s: float = 3.0) -> Optional[Dict[str, Any]]:
    """At each on/off transition of a switched load (with the contactors closed): the pack-voltage step against the measured
    current step. A voltage step with no current step means the load is not seen by the current sensor (it draws upstream
    of it); a current step that matches the voltage step means it passes the sensor."""
    if not all(profile.has_role(r) for r in (state_role, "pack_current", "pack_voltage")):
        return None
    pos = Timeline(rec, profile, "main_positive_closed") if profile.has_role("main_positive_closed") else None
    rows = []
    for w in rec.windows:
        I = profile.series(rec, w, "pack_current", "A")
        V = profile.series(rec, w, "pack_voltage", "V")
        ti, tv = [x.t for x in I.samples], [x.t for x in V.samples]

        def med(samples, ts, a, b):
            i, j = bisect.bisect_left(ts, a), bisect.bisect_right(ts, b)
            vals = [samples[k].value for k in range(i, j)]
            return statistics.median(vals) if len(vals) >= 3 else None

        for tr in transitions(rec, profile, state_role, [w]):
            if tr.initial or not tr.time_known or tr.old is None:
                continue
            if pos is not None:
                c = pos.at(w, tr.t, max_age=2.0)
                if not c or c[0] != 1:
                    continue
            on = str(profile.state_name(state_role, tr.new) or tr.new).lower() in ON_NAMES
            # the state report can lag the switching: "before" ends at the last sample still showing the old state
            tb = tr.t_prev if tr.t_prev is not None else tr.t - 0.2
            i0, i1 = med(I.samples, ti, tb - half_s, tb), med(I.samples, ti, tr.t + 0.5, tr.t + half_s)
            v0, v1 = med(V.samples, tv, tb - half_s, tb), med(V.samples, tv, tr.t + 0.5, tr.t + half_s)
            if None in (i0, i1, v0, v1):
                continue
            rows.append({"window": w, "t": round(tr.t, 2), "to_on": on, "di_a": round(i1 - i0, 2), "dv_v": round(v1 - v0, 3), "evidence": [tr.ref.as_dict()]})
        if len(rows) >= 60:
            break
    if len(rows) < 3:
        return None
    sgn = _sign(profile)
    unseen = [r for r in rows if abs(r["dv_v"]) >= 0.15 and abs(r["di_a"]) < 0.3]
    seen = [r for r in rows if abs(r["di_a"]) >= 0.5 and (r["dv_v"] * sgn * r["di_a"] < 0)]      # more discharge -> lower voltage
    informative = [r for r in rows if abs(r["dv_v"]) >= 0.15 or abs(r["di_a"]) >= 0.5]
    if len(unseen) >= max(3, 0.6 * len(informative)):
        verdict, st = "not_seen", (f"At {len(unseen)} of {len(rows)} {state_role} transitions the pack voltage stepped (median {statistics.median(abs(r['dv_v']) for r in unseen):.2f} V) "
                                   "with no step in the measured current: this load is not seen by the current sensor (it draws upstream of it), so counted "
                                   "charge misses it.")
    elif len(seen) >= max(3, 0.6 * len(informative)):
        verdict, st = "seen", (f"At {len(seen)} of {len(rows)} {state_role} transitions the measured current stepped (median {statistics.median(abs(r['di_a']) for r in seen):.1f} A) "
                               "with the matching voltage step: this load passes the current sensor.")
    else:
        verdict, st = "unclear", f"{state_role} transitions ({len(rows)}) do not show a consistent relation between voltage and current steps."
    return _item("load_transitions", st, KIND_COMPUTED, {"state_role": state_role, "transitions": len(rows), "not_seen": len(unseen), "seen": len(seen),
                                                         "verdict": verdict, "rows": [{k: v for k, v in r.items() if k != 'evidence'} for r in rows[:12]]},
                 [e for r in (unseen or seen or rows)[:3] for e in r["evidence"]], flag="evidence" if verdict == "not_seen" else None)


# ------------------------------------------------------------------ extreme cells aligned by charge
def _cum_charge(rec: Recording, profile: Profile, w: str) -> Tuple[List[float], List[float]]:
    s = profile.series(rec, w, "pack_current", "A")
    sgn = _sign(profile)
    ts, qs, q = [], [], 0.0
    prev = None
    for x in s.samples:
        if prev is not None and x.t - prev.t <= profile.max_gap_s:
            q += 0.5 * sgn * (prev.value + x.value) * (x.t - prev.t) / 3600.0
        ts.append(x.t)
        qs.append(q)
        prev = x
    return ts, qs


def _q_at(ts: List[float], qs: List[float], t: float) -> Optional[float]:
    i = bisect.bisect_right(ts, t) - 1
    return None if i < 0 else qs[i]


def charge_alignment(rec: Recording, profile: Profile, checks: Dict[str, Dict[str, Any]], facts: Dict[str, Dict[str, Any]],
                     *, step_v: float = 0.05) -> Optional[Dict[str, Any]]:
    """In the trip window: at each voltage on a grid, the charge removed when the lowest cell (persistent index) reached it
    against when the highest cell did. Constant offset -> the weakest cell sets timing; compared with the SOC discrepancy."""
    need = ("pack_current", "min_cell_voltage", "max_cell_voltage")
    if not all(profile.has_role(r) for r in need):
        return None
    sds = (((checks.get("restart_eligibility") or {}).get("windows") or {}).get("protective_shutdowns") or {}).get("items") or []
    if not sds:
        return None
    w, t_trip = sds[0]["window"], sds[0]["trigger_t"]
    ts, qs = _cum_charge(rec, profile, w)
    if len(ts) < 10:
        return None
    lo = [x for x in profile.series(rec, w, "min_cell_voltage", "V").samples if x.t <= t_trip]
    hi = [x for x in profile.series(rec, w, "max_cell_voltage", "V").samples if x.t <= t_trip]
    if profile.has_role("min_cell_index"):
        idx = Timeline(rec, profile, "min_cell_index")
        vals = [idx.at(w, x.t, max_age=2.0) for x in lo]
        modes = [v[0] for v in vals if v]
        if modes:
            m = statistics.mode(modes)
            lo = [x for x, v in zip(lo, vals) if v and v[0] == m]
    if profile.has_role("max_cell_index"):
        idx = Timeline(rec, profile, "max_cell_index")
        vals = [idx.at(w, x.t, max_age=2.0) for x in hi]
        modes = [v[0] for v in vals if v]
        if modes:
            m = statistics.mode(modes)
            hi = [x for x, v in zip(hi, vals) if v and v[0] == m]
    if len(lo) < 20 or len(hi) < 20:
        return None

    def crossing(samples, v):
        for a, b in zip(samples, samples[1:]):
            if a.value > v >= b.value:
                return b
        return None

    v_top = min(lo[0].value, hi[0].value)
    v_bot = max(min(x.value for x in lo), min(x.value for x in hi))
    rows = []
    v = math.floor(v_top / step_v) * step_v
    while v > v_bot:
        a, b = crossing(lo, v), crossing(hi, v)
        if a and b:
            qa, qb = _q_at(ts, qs, a.t), _q_at(ts, qs, b.t)
            if qa is not None and qb is not None:
                rows.append({"v": round(v, 3), "t_lowest": round(a.t, 1), "t_highest": round(b.t, 1), "offset_ah": round(qb - qa, 3),
                             "evidence": [rec.ref(w, profile.role("min_cell_voltage")["field"], a.index).as_dict(),
                                          rec.ref(w, profile.role("max_cell_voltage")["field"], b.index).as_dict()]})
        v -= step_v
    if len(rows) < 2:
        return None
    offs = [r["offset_ah"] for r in rows]
    span_q = (_q_at(ts, qs, max(r["t_lowest"] for r in rows)) or 0) - (_q_at(ts, qs, min(r["t_lowest"] for r in rows)) or 0)
    med_off = statistics.median(offs)
    constant = (max(offs) - min(offs)) <= max(0.1 * abs(med_off), 0.05)
    t_a, t_b = min(r["t_lowest"] for r in rows), max(r["t_highest"] for r in rows)
    q_a, q_b = _q_at(ts, qs, t_a), _q_at(ts, qs, t_b)
    i_typ = (q_b - q_a) * 3600.0 / (t_b - t_a) if (q_a is not None and q_b is not None and t_b > t_a) else None
    minutes = None if not i_typ else round(med_off / i_typ * 60, 1)
    eng = (facts.get("energy_shortfall") or {}).get("value") or {}
    cap = ((checks.get("charge_accounting") or {}).get("parameters_used") or {}).get("capacity_ah")
    if not cap:
        cap, _ = profile.parameter("capacity_ah")
    discrepancy_ah = None
    if eng and cap:
        discrepancy_ah = round(eng["overstatement_pp"] / 100 * float(cap), 2)
    share = None if not discrepancy_ah or discrepancy_ah <= 0 else round(med_off / discrepancy_ah, 3)
    timing_only = share is not None and share < 0.2
    st = (f"Aligned by charge removed in {w}, the persistently lowest cell reached " + ", ".join(f"{r['v']:.2f}" for r in rows[:6]) +
          f" V {min(offs):.2f}–{max(offs):.2f} Ah before the highest cell" + (" (a constant offset)" if constant else " (the offset changes)") +
          (f", about {minutes} min at the {i_typ:.1f} A that flowed meanwhile" if minutes is not None else "") + ".")
    if discrepancy_ah:
        st += (f" Against an SOC discrepancy of about {discrepancy_ah} Ah, the weakest cell accounts for about {100 * share:.0f}% of it: "
               + ("it sets WHEN the usable charge ended, not the SIZE of the discrepancy." if timing_only else "it can explain a material part of the size."))
    st += f" The offset is observed over about {span_q:.1f} Ah of discharge; a capacity difference smaller than that resolution cannot be told from imbalance."
    return _item("charge_alignment", st, KIND_COMPUTED, {"rows": [{k: v for k, v in r.items() if k != 'evidence'} for r in rows], "offset_ah_median": round(med_off, 3),
                                                         "offset_range_ah": [min(offs), max(offs)], "constant": constant, "minutes_at_typical_load": minutes,
                                                         "discrepancy_ah": discrepancy_ah, "share_of_discrepancy": share, "timing_only": timing_only,
                                                         "observed_over_ah": round(span_q, 2)},
                 [e for r in rows[:3] for e in r["evidence"]],
                 limits=["assumes the cells share the curve shape (checked only through the constant offset)", "charge from the BMS current sensor"])


# ------------------------------------------------------------------ restart and HV re-authorisation
def restart_authorisation(rec: Recording, profile: Profile, checks: Dict[str, Dict[str, Any]],
                          rules: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    re_ = checks.get("restart_eligibility") or {}
    recl = ((re_.get("windows") or {}).get("reclosures") or {}).get("items") or []
    if not recl:
        return None
    lvl = Timeline(rec, profile, "bms_fault_level") if profile.has_role("bms_fault_level") else None
    st_ = Timeline(rec, profile, "bms_state") if profile.has_role("bms_state") else None
    # the cell voltage when each escalation level first appeared (any window, time-known)
    first_v: Dict[float, float] = {}
    mc = Timeline(rec, profile, "min_cell_voltage") if profile.has_role("min_cell_voltage") else None
    if lvl and mc:
        for tr in transitions(rec, profile, "bms_fault_level"):
            if tr.initial or tr.old is None or tr.new <= tr.old or tr.new in first_v:
                continue
            at = mc.at(tr.window, tr.t, max_age=max(mc.max_age, 1.0))
            if at:
                first_v[tr.new] = at[0]
    sem = ((rules or {}).get("fault_levels") or {})
    rows = []
    for r in recl:
        w, t = r["window"], r["t"]
        lv = lvl.at(w, t, max_age=5.0) if lvl else None
        sv = st_.at(w, t, max_age=5.0) if st_ else None
        prior_esc = []
        if lvl:
            for tr in transitions(rec, profile, "bms_fault_level", [w]):
                if not tr.initial and tr.old is not None and tr.new > tr.old and t - 30 <= tr.t <= t:
                    prior_esc.append({"to": tr.new, "t": round(tr.t, 3), "before_close_s": round(t - tr.t, 2)})
        level_at_close = None if lv is None else lv[0]
        rows.append({"n": r["n"], "window": w, "t": t, "fault_level_at_close": level_at_close,
                     "bms_state_at_close": None if sv is None else _name(profile, "bms_state", sv[0]),
                     "escalations_before_close": prior_esc, "min_cell_v_at_close": r.get("min_cell_v_at_close"),
                     "ended_by_protective_shutdown": r.get("ended_by_protective_shutdown"), "lowest_cell_v_while_on": r.get("lowest_cell_v_while_on")})
    findings = []
    lvl2_v = first_v.get(2.0)
    below = [x for x in rows if lvl2_v is not None and x["min_cell_v_at_close"] is not None and x["min_cell_v_at_close"] < lvl2_v]
    with_esc = [x for x in rows if x["escalations_before_close"] or (x["fault_level_at_close"] or 0) > 0]
    allows = {k: bool((v or {}).get("permits_hv")) for k, v in sem.items()}
    latched = {k: (v or {}).get("latched") for k, v in sem.items()}
    block = sorted(float(k) for k, v in sem.items() if (v or {}).get("permits_hv") is False)
    if block and first_v.get(block[0]) is not None and rows:
        bv = first_v[block[0]]
        vs = [x["min_cell_v_at_close"] for x in rows if x["min_cell_v_at_close"] is not None]
        if vs:
            findings.append(_item("authorised_relative_to_blocking_level",
                                  f"Customer-confirmed: level {block[0]:g} blocks HV. Every closure was made with the weakest cell at {min(vs):.3f}–{max(vs):.3f} V, "
                                  f"{'above' if min(vs) > bv else 'at or below'} the {bv:.3f} V at which level {block[0]:g} fired: the rest recovery lifted the cell "
                                  "back over the blocking threshold before each closure.", KIND_COMPUTED, {"blocking_level": block[0], "blocking_v": bv}))
    if below and not block:
        findings.append(_item("authorised_below_escalation_voltage",
                              f"{len(below)} of {len(rows)} HV closures after the protective shutdown were made with the weakest cell at "
                              f"{min(x['min_cell_v_at_close'] for x in below):.3f}–{max(x['min_cell_v_at_close'] for x in below):.3f} V, below the "
                              f"{lvl2_v:.3f} V at which level 2 first appeared.", KIND_COMPUTED,
                              {"closures": [x["n"] for x in below], "level2_first_v": lvl2_v}, flag="review"))
    if with_esc:
        esc_levels = sorted({e["to"] for x in with_esc for e in x["escalations_before_close"]} | {x["fault_level_at_close"] for x in with_esc if x["fault_level_at_close"]})
        permitted = [l for l in esc_levels if allows.get(str(int(l)))]
        txt = (f"{len(with_esc)} closure(s) completed with an escalation already reported (level(s) {', '.join(f'{l:g}' for l in esc_levels)}).")
        if permitted and len(permitted) == len(esc_levels):
            txt += " Customer-confirmed rule: these levels permit HV, so this is by design."
            flag = None
        else:
            txt += " Whether these levels should block HV belongs to the manufacturer's fault-level specification."
            flag = "question"
        findings.append(_item("closed_with_escalation_reported", txt, KIND_COMPUTED, {"closures": [x["n"] for x in with_esc], "levels": esc_levels}, flag=flag))
    trips = [x for x in rows if x["ended_by_protective_shutdown"] is not None]
    if trips:
        txt = (f"The top protective level did not persist across the power cycle: after each reset the system authorised HV again, and "
               f"{len(trips)} of {len(rows)} closures ended in another protective shutdown.")
        top = max(first_v) if first_v else None
        lt = latched.get(str(int(top))) if top is not None else None
        if lt is True:
            txt += f" Customer-confirmed rule: level {top:g} is meant to latch, so this deviates from the design."
            flag = "deviation"
        elif lt is False:
            txt += f" Customer-confirmed rule: level {top:g} is not meant to latch; whether automatic re-authorisation at this cell voltage is acceptable remains a design question."
            flag = "question"
        else:
            txt += " Whether it should latch is a question for the manufacturer's reset specification."
            flag = "question"
        findings.append(_item("protection_not_latched", txt, KIND_COMPUTED, {"cycles": len(trips)}, flag=flag))
    return {"id": "restart_authorisation", "rows": rows, "findings": findings, "level_first_cell_v": {f"{k:g}": round(v, 4) for k, v in first_v.items()},
            "rules_used": bool(sem), "statement": " ".join(f["statement"] for f in findings)}


# ------------------------------------------------------------------ all of it
def audit(rec: Recording, profile: Profile, checks: Dict[str, Dict[str, Any]], facts: Dict[str, Dict[str, Any]],
          *, reported_symptom: Optional[str] = None, rules: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"version": AUDIT_VERSION}
    for key, fn in (("symptom_vs_record", lambda: symptom_vs_record(rec, profile, reported_symptom)),
                    ("control_sequence", lambda: control_sequence(rec, profile, checks)),
                    ("inferred_thresholds", lambda: inferred_thresholds(rec, profile)),
                    ("signal_independence", lambda: signal_independence(rec, profile)),
                    ("load_sag", lambda: load_sag(rec, profile, checks, facts)),
                    ("load_transitions", lambda: load_transitions(rec, profile)),
                    ("charge_alignment", lambda: charge_alignment(rec, profile, checks, facts)),
                    ("restart_authorisation", lambda: restart_authorisation(rec, profile, checks, rules))):
        try:
            v = fn()
        except Exception as e:  # an audit section never breaks the investigation; the failure is recorded
            v = {"id": key, "error": f"{type(e).__name__}: {e}"}
        if v is not None:
            out[key] = v
    return out
