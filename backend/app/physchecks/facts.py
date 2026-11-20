"""Case facts: deterministic quantities the hypothesis layer reasons with, each with the rows that show it.

Facts are observations or arithmetic over observations. When a fact needs an assumption (e.g. charge drawn in an
unrecorded interval estimated from SOC counts), the assumption is stated next to the number and the fact is marked
`estimated`. Two quantities that are easy to conflate are kept apart:

  soc_correction        the abrupt re-statement of SOC by the estimator (Check A episode): pp of SOC that moved with
                        almost no measured charge. It measures how wrong the estimate WAS, not energy leaving the pack.
  energy_shortfall      the charge actually delivered from the first recorded SOC to the protective cutoff, against
                        what that SOC and the stated capacity promised. It spans windows and includes an estimate for
                        unrecorded time.
"""
from __future__ import annotations

import bisect
import statistics
from typing import Any, Dict, List, Optional

from app.physchecks.events import Timeline, transitions
from app.physchecks.profile import Profile
from app.physchecks.recording import Recording


def _fact(fid: str, statement: str, value: Any = None, refs: Optional[List[Dict[str, Any]]] = None, *, estimated: bool = False,
          assumptions: Optional[List[str]] = None, unit: str = "") -> Dict[str, Any]:
    return {"id": fid, "statement": statement, "value": value, "unit": unit, "evidence": refs or [], "estimated": estimated,
            "assumptions": assumptions or []}


def derive(rec: Recording, profile: Profile, checks: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    F: Dict[str, Dict[str, Any]] = {}
    windows = rec.windows

    # stationary
    if profile.has_role("speed"):
        vals = []
        for w in windows:
            s = profile.series(rec, w, "speed")
            vals += [(abs(x.value), w, x) for x in s.samples]
        if vals:
            mx = max(vals, key=lambda v: v[0])
            F["stationary"] = _fact("stationary", f"Recorded speed never exceeded {mx[0]:.2f} m/s in any window: the machine was stationary while recorded.",
                                    mx[0] <= 0.05, [rec.ref(mx[1], profile.role("speed")["field"], mx[2].index).as_dict()], unit="m/s")
    # discharge current range while recorded
    if profile.has_role("pack_current"):
        sgn = 1.0 if profile.role("pack_current").get("sign") == "discharge_positive" else -1.0
        cur = [sgn * x.value for w in windows for x in profile.series(rec, w, "pack_current", "A").samples]
        on = [c for c in cur if abs(c) > 0.5]
        if on:
            q = statistics.quantiles(on, n=20)
            F["load"] = _fact("load", f"While current flowed, discharge current was {q[0]:.1f}–{q[-1]:.1f} A (5th–95th percentile), max {max(on):.1f} A.",
                              {"p5": round(q[0], 2), "p95": round(q[-1], 2), "max": round(max(on), 2)}, unit="A")
    # first protective shutdown (from Check C)
    rc = checks.get("restart_eligibility") or {}
    sds = ((rc.get("windows") or {}).get("protective_shutdowns") or {}).get("items") or []
    tmin, tmax = Timeline(rec, profile, "min_cell_voltage"), Timeline(rec, profile, "max_cell_voltage")
    if sds:
        sd = sds[0]
        w, t = sd["window"], sd["trigger_t"]
        lo = tmin.at(w, t, max_age=1.0) if tmin.available() else None
        hi = tmax.at(w, t, max_age=1.0) if tmax.available() else None
        if lo and hi:
            F["cells_at_trip"] = _fact("cells_at_trip", f"At the protective trip ({w}, t={t} s) the lowest cell read {lo[0]:.3f} V and the highest {hi[0]:.3f} V "
                                       f"(spread {1000 * (hi[0] - lo[0]):.0f} mV).", {"min_v": lo[0], "max_v": hi[0], "spread_mv": round(1000 * (hi[0] - lo[0]))},
                                       [lo[2].as_dict(), hi[2].as_dict()], unit="V")
        # first record of cells
        w0 = windows[0]
        span0 = rec.span(w0)
        if span0 and tmin.available() and tmax.available():
            first_lo = _first_fresh(tmin, w0, span0[0])
            first_hi = _first_fresh(tmax, w0, span0[0])
            if first_lo and first_hi:
                F["cells_first_record"] = _fact("cells_first_record", f"At the first record ({w0}, t={first_lo[3]:.1f} s) cells read {first_lo[0]:.3f}–{first_hi[0]:.3f} V "
                                                f"(spread {1000 * (first_hi[0] - first_lo[0]):.0f} mV).",
                                                {"min_v": first_lo[0], "max_v": first_hi[0], "spread_mv": round(1000 * (first_hi[0] - first_lo[0]))},
                                                [first_lo[2].as_dict(), first_hi[2].as_dict()], unit="V")
                if "cells_at_trip" in F:
                    d_max = first_hi[0] - F["cells_at_trip"]["value"]["max_v"]
                    spread_trip = F["cells_at_trip"]["value"]["max_v"] - F["cells_at_trip"]["value"]["min_v"]
                    F["pack_wide_decline"] = _fact(
                        "pack_wide_decline",
                        f"The HIGHEST cell fell {1000 * d_max:.0f} mV from the first record to the trip, more than the {1000 * spread_trip:.0f} mV spread between "
                        "highest and lowest cell at the trip: the whole pack was depleted, not one cell." if d_max >= spread_trip else
                        f"The highest cell fell {1000 * d_max:.0f} mV from the first record to the trip, less than the {1000 * spread_trip:.0f} mV spread at the trip: "
                        "the depletion is concentrated in the lowest cell(s).",
                        {"highest_cell_decline_mv": round(1000 * d_max), "spread_at_trip_mv": round(1000 * spread_trip), "pack_wide": d_max >= spread_trip},
                        F["cells_first_record"]["evidence"] + F["cells_at_trip"]["evidence"])
        # lowest cell index stability in the trip window
        if profile.has_role("min_cell_index"):
            idx = profile.series(rec, w, "min_cell_index")
            vals = [x.value for x in idx.samples if x.t <= t]
            if vals:
                mode = statistics.mode(vals)
                share = vals.count(mode) / len(vals)
                F["lowest_cell_index"] = _fact("lowest_cell_index", f"In {w} up to the trip, the lowest cell was index {mode:g} in {100 * share:.1f}% of {len(vals)} samples.",
                                               {"index": mode, "share": round(share, 4)}, [rec.ref(w, idx.field, idx.samples[0].index).as_dict()])
        # rest rebound after the trip
        span = rec.span(w)
        if span and tmin.available() and tmax.available():
            end_lo, end_hi = tmin.at(w, span[1], max_age=2.0), tmax.at(w, span[1], max_age=2.0)
            if end_lo and end_hi and "cells_at_trip" in F:
                F["rest_rebound"] = _fact("rest_rebound", f"With HV open, by the end of {w} (t={span[1]:.0f} s) cells recovered to {end_lo[0]:.3f}–{end_hi[0]:.3f} V "
                                          f"(lowest +{1000 * (end_lo[0] - F['cells_at_trip']['value']['min_v']):.0f} mV).",
                                          {"min_v": end_lo[0], "max_v": end_hi[0]}, [end_lo[2].as_dict(), end_hi[2].as_dict()], unit="V")
        # smoothness of the lowest cell during discharge (sensing faults jump)
        if tmin.available():
            s, _ = tmin.by_window[w]
            pts = [x for x in s.samples if x.t <= t]
            steps = [abs(b.value - a.value) for a, b in zip(pts, pts[1:]) if b.t - a.t <= profile.max_gap_s]
            if steps:
                F["lowest_cell_smooth"] = _fact("lowest_cell_smooth", f"Before the trip, consecutive lowest-cell samples never differed by more than {1000 * max(steps):.0f} mV.",
                                                {"max_step_mv": round(1000 * max(steps))}, unit="mV")
    # VCU's copy of the current: agreement and independence
    if profile.has_role("vcu_pack_current") and profile.has_role("pack_current"):
        a = Timeline(rec, profile, "pack_current")
        diffs = []
        for w in windows:
            s = profile.series(rec, w, "vcu_pack_current", "A")
            for x in s.samples[::10]:
                got = a.at(w, x.t)
                if got:
                    diffs.append(abs(got[0] - x.value))
        if diffs:
            indep = profile.independent("pack_current", "vcu_pack_current")
            F["current_cross_check"] = _fact("current_cross_check", f"The VCU's current agrees with the BMS current (median |difference| {statistics.median(diffs):.2f} A over {len(diffs)} pairs). "
                                             + ("They are independent measurements." if indep else "The VCU receives it from the BMS: the same sensor, so this is NOT an independent check of the sensor."),
                                             {"median_abs_diff_a": round(statistics.median(diffs), 3), "independent": indep}, unit="A")
    # energy shortfall (distinct from the SOC correction)
    ca = checks.get("charge_accounting") or {}
    cap = (ca.get("parameters_used") or {}).get("capacity_ah")
    if cap and sds and ca.get("windows"):
        F.update(_energy(rec, profile, ca, cap, sds[0], windows))
    # the correction itself
    eps = [e for w in (ca.get("windows") or {}).values() for e in w.get("episodes") or []]
    if eps:
        big = max(eps, key=lambda e: abs(e["unexplained_pp"]))
        F["soc_correction"] = _fact("soc_correction", f"Largest SOC correction: {big['soc_before_pct']} % -> {big['soc_after_pct']} % in {big['duration_s']} s ({big['window']}, "
                                    f"t={big['t_start']}–{big['t_end']} s); measured charge {big['measured_charge_ah']} Ah explains {big['explained_pp']} pp; "
                                    f"{abs(big['unexplained_pp'])} pp unexplained.", big, unit="pp")
        ctx = _correction_context(rec, profile, eps)
        if ctx:
            F["correction_context"] = ctx
    return F


def _correction_context(rec: Recording, profile: Profile, eps: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """For each SOC correction: the recording quality inside it and the fault escalation just before it (context only).

    Answers 'is this a sampling, timing or segment artifact?' from the rows, and states which escalation preceded the
    first changed SOC sample. Order in time is context, not proof that the escalation caused the correction."""
    if not profile.has_role("soc"):
        return None
    esc = [t for t in transitions(rec, profile, "bms_fault_level") if not t.initial and t.old is not None and t.new > t.old and t.time_known]
    items, refs = [], []
    for ep in eps:
        w, t0, t1 = ep["window"], ep["t_start"], ep["t_end"]
        s = profile.series(rec, w, "soc")
        inside = [x for x in s.samples if t0 <= x.t <= t1]
        if len(inside) < 2:
            continue
        gaps = [b.t - a.t for a, b in zip(inside, inside[1:])]
        segs = {rec.segment_of(w, s.field, x.index) for x in inside}
        steps = [b for a, b in zip(inside, inside[1:]) if b.value != a.value]
        first = steps[0] if steps else inside[-1]
        intervals = [b.t - a.t for a, b in zip(steps, steps[1:])]
        cur_age = None
        if profile.has_role("pack_current"):
            ct = [x.t for x in profile.series(rec, w, "pack_current").samples if t0 - 5 <= x.t <= t1]
            if ct:
                ages = []
                for x in inside:
                    k = bisect.bisect_right(ct, x.t) - 1
                    ages.append(x.t - ct[k] if k >= 0 else float("inf"))
                cur_age = max(ages)
        prior = [e for e in esc if e.window == w and e.t <= first.t and first.t - e.t <= 30.0]
        p = prior[-1] if prior else None
        item = {"window": w, "t_start": t0, "t_end": t1, "first_changed_t": round(first.t, 3), "soc_samples": len(inside),
                "max_soc_interval_s": round(max(gaps), 3), "soc_max_age_s": s.max_age_s, "segments": sorted(segs),
                "steps": len(steps), "median_step_interval_s": round(statistics.median(intervals), 3) if intervals else None,
                "max_current_age_s": None if cur_age is None else round(cur_age, 3),
                "sampling_clean": max(gaps) <= s.max_age_s and len(segs) == 1 and (cur_age is None or cur_age <= profile.max_age("pack_current")),
                "preceding_escalation": None if p is None else {"t": round(p.t, 3), "from": p.old, "to": p.new, "delay_s": round(first.t - p.t, 3)}}
        items.append(item)
        refs.append(rec.ref(w, s.field, first.index).as_dict())
        if p is not None:
            refs.append(p.ref.as_dict())
    if not items:
        return None
    parts = []
    for it in items:
        if it["sampling_clean"]:
            q = f"SOC sampled at least every {it['max_soc_interval_s']} s with no gap, one segment"
            if it["max_current_age_s"] is not None:
                q += f", current paired within {it['max_current_age_s']} s"
        else:
            q = f"recording quality limits this interval (max SOC interval {it['max_soc_interval_s']} s, segments {', '.join(it['segments'])})"
        e = it["preceding_escalation"]
        pre = (f"first changed sample t={it['first_changed_t']} s, {e['delay_s']} s after BMS fault level {e['from']:g} -> {e['to']:g} (t={e['t']} s)"
               if e else f"first changed sample t={it['first_changed_t']} s, no fault escalation in the 30 s before")
        cad = f"; {it['steps']} steps about every {it['median_step_interval_s']} s" if it["median_step_interval_s"] else ""
        parts.append(f"{it['window']} t={it['t_start']}–{it['t_end']} s: {pre}{cad}; {q}.")
    delays = [it["preceding_escalation"]["delay_s"] for it in items if it["preceding_escalation"]]
    common = ""
    if len(items) > 1 and len(delays) == len(items) and max(delays) - min(delays) <= 0.2 and all(it["sampling_clean"] for it in items):
        common = (f" {'Both' if len(items) == 2 else 'All ' + str(len(items))} corrections start about {statistics.median(delays):.1f} s after a "
                  f"fault-level escalation and step at the same cadence: the same pattern each time, recorded cleanly (not a sampling, timing or "
                  f"segment artifact) and consistent with the estimator re-stating SOC when the fault level rises. The timing is context; it does "
                  f"not say which upstream cause made the SOC wrong.")
    return _fact("correction_context", "Context of each SOC correction: " + " ".join(parts) + common, items, refs)


def integrate_ah(rec: Recording, profile: Profile, w: str, until: Optional[float] = None) -> float:
    """Discharge-positive trapezoid integral of the pack current in one window (Ah), split at gaps > max_gap_s."""
    s = profile.series(rec, w, "pack_current", "A")
    sgn = 1.0 if profile.role("pack_current").get("sign") == "discharge_positive" else -1.0
    q = 0.0
    pts = [x for x in s.samples if until is None or x.t <= until]
    for a, b in zip(pts, pts[1:]):
        dt = b.t - a.t
        if dt <= profile.max_gap_s:
            q += 0.5 * sgn * (a.value + b.value) * dt
    return q / 3600.0


def _first_fresh(tl: Timeline, w: str, t0: float):
    s, ts = tl.by_window[w]
    for x in s.samples[:50]:
        return x.value, 0.0, tl.rec.ref(w, s.field, x.index), x.t
    return None


def _energy(rec, profile, ca, cap, sd, windows) -> Dict[str, Dict[str, Any]]:
    """Charge delivered from the first recorded SOC to the protective cutoff, against what SOC x capacity promised."""
    out: Dict[str, Dict[str, Any]] = {}
    trip_w, trip_t = sd["window"], sd["t"]
    idx = windows.index(trip_w)
    used = windows[: idx + 1]
    soc_first = None
    measured = 0.0
    parts = []
    soc_ends: Dict[str, Any] = {}
    for w in used:
        s = profile.series(rec, w, "soc", "%")
        if not s.samples:
            continue
        if soc_first is None:
            soc_first = (s.samples[0].value, w, s.samples[0])
        # measured charge up to the trip in the trip window, whole window otherwise (never across gaps)
        q = integrate_ah(rec, profile, w, None if w != trip_w else trip_t)
        measured += q
        parts.append({"window": w, "measured_ah": round(q, 4)})
        soc_ends[w] = (s.samples[0].value, s.samples[-1].value)
    # unrecorded intervals between consecutive windows: SOC-count estimate (labelled)
    est = 0.0
    est_parts = []
    for a, b in zip(used, used[1:]):
        if a in soc_ends and b in soc_ends:
            d_pp = soc_ends[a][1] - soc_ends[b][0]
            e = max(0.0, d_pp) / 100 * cap
            est += e
            est_parts.append({"between": [a, b], "soc_drop_pp": round(d_pp, 2), "estimated_ah": round(e, 3)})
    if soc_first is None:
        return out
    promised = soc_first[0] / 100 * cap
    delivered = measured + est
    implied = delivered / (soc_first[0] / 100) if soc_first[0] > 0 else None
    ratio = (promised / delivered) if delivered > 0 else None
    out["energy_shortfall"] = _fact(
        "energy_shortfall",
        f"From the first recorded SOC ({soc_first[0]:.1f} % in {soc_first[1]}) to the protective cutoff, about {delivered:.1f} Ah was delivered "
        f"({measured:.2f} Ah measured in recorded windows + {est:.2f} Ah estimated for unrecorded time from SOC counts), against "
        f"{promised:.1f} Ah that {soc_first[0]:.1f} % of {cap:g} Ah promised. Either the SOC was overstated by about "
        f"{soc_first[0] - 100 * delivered / cap:.0f} pp, or usable capacity was about {implied:.0f} Ah, or the current was under-measured "
        f"by about {ratio:.1f}x, or a combination.",
        {"soc_first_pct": soc_first[0], "measured_ah": round(measured, 3), "unrecorded_estimated_ah": round(est, 3), "delivered_ah": round(delivered, 3),
         "promised_ah": round(promised, 2), "overstatement_pp": round(soc_first[0] - 100 * delivered / cap, 1),
         "implied_capacity_ah": None if implied is None else round(implied, 1), "current_underread_factor": None if ratio is None else round(ratio, 2),
         "parts": parts, "unrecorded": est_parts},
        [rec.ref(soc_first[1], profile.role("soc")["field"], soc_first[2].index).as_dict()], estimated=bool(est_parts),
        assumptions=["charge in unrecorded intervals is estimated from the SOC drop between windows (SOC equals coulomb counting where checked)"] if est_parts else [],
        unit="Ah")
    return out
