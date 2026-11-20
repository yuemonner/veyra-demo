"""Check F: voltage relaxation after a current interruption (e.g. the protective shutdown).

At an interruption the immediate voltage recovery divided by the current step estimates ohmic resistance; the slower
recovery that follows is polarization. Compared between the lowest cell and the average cell (pack / N):
  * higher ohmic resistance in the lowest cell -> resistance or connection
  * similar resistance but a lower voltage once relaxed -> lower SOC in that cell (imbalance) or lower capacity; one rest
    does not separate the two
The relaxed voltage constrains SOC only with the chemistry's OCV curve at temperature, and not in a flat region of that
curve (e.g. LFP plateaus, with hysteresis). BMS-reported voltages alone cannot exclude a sensing error. External EE
advisor's proposal (2026-11-16).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.physchecks import base, cellphys
from app.physchecks.base import (INDETERMINATE, NOT_APPLICABLE, PASS, VIOLATION, Calculation, CheckResult, CheckSpec, Finding,
                                 InputSpec, ParamSpec, fmt)
from app.physchecks.profile import Profile
from app.physchecks.recording import Recording

VERSION = "relaxation-v1"

SPEC = CheckSpec(
    id="relaxation",
    version=VERSION,
    title="Relaxation after a current interruption",
    purpose="At each current interruption: ohmic resistance (instant recovery / current step) and polarization (slow recovery) of the "
            "lowest cell against the average cell, and the relaxed voltage against the OCV curve when one is given.",
    inputs=[InputSpec("pack_current", "A", "Pack current", sign_convention="converted to discharge-positive"),
            InputSpec("min_cell_voltage", "V", "Lowest cell voltage"),
            InputSpec("pack_voltage", "V", "Pack voltage (for the average cell)", required=False),
            InputSpec("cell_temperature", "degC", "Cell temperature (OCV depends on it); also read from max_temp / min_temp / pack_temperature "
                      "(cellphys.TEMPERATURE_ROLES)", required=False)],
    parameters=[ParamSpec("series_count", "-", "Cells in series", False, source_rule="the pack specification; without it only the lowest cell is described"),
                ParamSpec("ocv_curve", "-", "Chemistry OCV curve {soc_pct, v, temperature_c, verified}", False, None, source_rule="customer / cell supplier"),
                ParamSpec("load_min_a", "A", "Current before the step at least", False, 5.0),
                ParamSpec("rest_max_a", "A", "Current counts as interrupted at most", False, 1.0),
                ParamSpec("step_max_s", "s", "Longest step from load to rest", False, 3.0),
                ParamSpec("instant_s", "s", "Window after the step for the instant recovery", False, 3.0),
                ParamSpec("min_relax_s", "s", "Shortest rest to judge polarization", False, 300.0),
                ParamSpec("resistance_ratio_flag", "-", "Lowest-cell / average-cell ohmic resistance above this is reported", False, 1.5),
                ParamSpec("flat_slope_v_per_pp", "V/pp", "OCV slope below this: voltage does not establish SOC", False, 0.002),
                ParamSpec("relaxed_deficit_flag_v", "V", "Lowest cell relaxed this much below the average is reported (fit on healthy packs)", False, 0.03),
                ParamSpec("cell_voltage_accuracy_v", "V", "Cell voltage resolution / accuracy: an instant step within 2x this is not resolved", False, None,
                          source_rule="sensor spec or the role's resolution; else 5 mV (an assumption)")],
    applies_when=["pack current and lowest cell voltage are recorded", "the current drops from load to near zero at least once"],
    alignment={"pairing": "voltage samples are taken at or after the step, never interpolated"},
    limitations=["One interruption gives one resistance estimate; temperature changes it",
                 "Relaxed voltage -> SOC needs the chemistry's OCV curve and temperature and fails in flat regions / with hysteresis",
                 "BMS-reported voltages: excluding a sensing error needs an independent voltage reference"],
)


def _first_after(series, t0: float, t1: float):
    for s in series.samples:
        if t0 <= s.t <= t1:
            return s
        if s.t > t1:
            break
    return None


def _last_before(series, t: float, max_age: float):
    best = None
    for s in series.samples:
        if s.t <= t:
            best = s
        else:
            break
    return best if best is not None and t - best.t <= max_age else None


class Relaxation(base.Check):
    spec = SPEC

    def run(self, rec: Recording, profile: Profile, params: Optional[Dict[str, Any]] = None) -> CheckResult:
        params = dict(params or {})
        res = CheckResult(SPEC.id, VERSION, rec.id, rec.synthetic, NOT_APPLICABLE, "")
        res.provenance = dict(cellphys.PROVENANCE)
        res.input_digest = base.input_digest(rec, profile, SPEC.id, VERSION, params)
        missing = self.missing_roles(profile)
        if missing:
            res.reasons.append(f"the profile binds no signal for {', '.join(missing)}")
            res.summary = "Not applicable: pack current or lowest cell voltage is not recorded."
            return res
        P = {k: float(params.get(k, d)) for k, d in (("load_min_a", 5.0), ("rest_max_a", 1.0), ("step_max_s", 3.0), ("instant_s", 3.0),
                                                     ("min_relax_s", 300.0), ("resistance_ratio_flag", 1.5), ("flat_slope_v_per_pp", 0.002),
                                                     ("relaxed_deficit_flag_v", 0.03))}
        n, n_src = cellphys.param(params, profile, "series_count")
        try:
            n = int(n) if n is not None else None
        except (TypeError, ValueError):
            n = None
        curve, curve_src = cellphys.param(params, profile, "ocv_curve")
        res.parameters_used.update({**P, "series_count": n, "ocv_curve_source": curve_src})
        troles = cellphys.temperature_roles(profile)
        res.inputs_used = {r: profile.describe(r) for r in ("pack_current", "min_cell_voltage", "pack_voltage", *troles) if profile.has_role(r)}
        res_v, res_src = cellphys.param(params, profile, "cell_voltage_accuracy_v")
        if res_v is None and profile.role("min_cell_voltage").get("resolution"):
            res_v, res_src = profile.role("min_cell_voltage")["resolution"], "profile: min_cell_voltage resolution"
        if res_v is None:
            res_v, res_src = 0.005, "default 5 mV (an assumption, not a sensor specification)"
            res.assumptions.append("cell voltage resolution 5 mV (default, not a sensor specification): an instant step within 10 mV is not resolved")
        res_v = float(res_v)
        res.parameters_used.update({"cell_voltage_resolution_v": res_v, "cell_voltage_resolution_source": res_src, "temperature_roles": troles})
        sign = 1.0 if profile.role("pack_current").get("sign", "discharge_positive") == "discharge_positive" else -1.0
        events: List[Dict[str, Any]] = []
        for w in rec.windows:
            cur = profile.series(rec, w, "pack_current", "A")
            vmin = profile.series(rec, w, "min_cell_voltage", "V")
            vp = profile.series(rec, w, "pack_voltage", "V") if (profile.has_role("pack_voltage") and n) else None
            tcs = {r: profile.series(rec, w, r, "degC") for r in troles}
            S = cur.samples
            k = 0
            while k < len(S) - 1:
                a = S[k]
                if sign * a.value < P["load_min_a"]:
                    k += 1
                    continue
                j = k + 1
                while j < len(S) and S[j].t - a.t <= P["step_max_s"] and abs(S[j].value) > P["rest_max_a"]:
                    j += 1
                if j >= len(S) or S[j].t - a.t > P["step_max_s"] or abs(S[j].value) > P["rest_max_a"]:
                    k += 1
                    continue
                t_step, i0 = S[j].t, sign * a.value
                e = j
                while e + 1 < len(S) and abs(S[e + 1].value) <= P["rest_max_a"] and S[e + 1].t - S[e].t <= max(profile.max_gap_s, 5.0):
                    e += 1
                rest_s = S[e].t - t_step
                ev: Dict[str, Any] = {"window": w, "t_step": fmt(t_step), "current_step_a": fmt(i0, 2), "rest_s": fmt(rest_s, 1)}
                for name, ser, scale in (("lowest_cell", vmin, 1.0), ("average_cell", vp, 1.0 / n if n else None)):
                    if ser is None or scale is None:
                        continue
                    before = _last_before(ser, a.t, ser.max_age_s + P["step_max_s"])
                    inst = _first_after(ser, t_step, t_step + P["instant_s"])
                    end = _last_before(ser, S[e].t, ser.max_age_s)
                    if not (before and inst and end):
                        continue
                    dv_inst = (inst.value - before.value) * scale
                    floor = 2 * res_v                    # per-cell resolution, also used (conservatively) for the average cell
                    resolved = dv_inst > floor
                    r0 = dv_inst / i0 if resolved else None
                    slow = (end.value - inst.value) * scale
                    tau = None
                    if rest_s >= P["min_relax_s"] and abs(slow) > 0:
                        target = inst.value + 0.632 * (end.value - inst.value)
                        hit = next((s for s in ser.samples if t_step <= s.t <= S[e].t and (s.value - target) * (end.value - inst.value) >= 0), None)
                        tau = fmt(hit.t - t_step, 1) if hit else None
                    ev[name] = {"v_load": fmt(before.value * scale, 4), "v_instant": fmt(inst.value * scale, 4), "v_relaxed": fmt(end.value * scale, 4),
                                "r_ohmic_ohm": fmt(r0, 5) if resolved else None,
                                "r_ohmic_note": None if resolved else (f"below resolution: the instant change {dv_inst * 1000:+.1f} mV at {i0:.1f} A is within "
                                                                       f"+-{floor * 1000:.0f} mV; R0 is less than about {floor / i0 * 1000:.1f} mohm"),
                                "instant_change_v": fmt(dv_inst, 4),
                                "polarization_v": fmt(slow, 4) if rest_s >= P["min_relax_s"] else None, "tau_63_s": tau,
                                "evidence": [rec.ref(w, ser.field, x.index) for x in (before, inst, end)]}
                for r, tser in tcs.items():
                    tt = _last_before(tser, t_step, 60.0)
                    ev.setdefault("temperature_c", {})[r] = fmt(tt.value, 1) if tt else None
                events.append(ev)
                k = e + 1
        if not events:
            res.reasons.append("no current interruption (load to near zero) is recorded")
            res.summary = "Not applicable: the current never drops from load to near zero."
            return res
        flagged = []
        for ev in events:
            lo, av = ev.get("lowest_cell"), ev.get("average_cell")
            ev_ref = [x for part in (lo, av) if part for x in part.pop("evidence")]
            notes = []
            ratio = None
            if lo and av and lo["r_ohmic_ohm"] is not None and av["r_ohmic_ohm"] is not None and av["r_ohmic_ohm"] > 0:
                ratio = lo["r_ohmic_ohm"] / av["r_ohmic_ohm"]   # only between resolved resistances
                ev["resistance_ratio"] = fmt(ratio, 2)
            if ratio is not None and ratio > P["resistance_ratio_flag"]:
                notes.append(f"the lowest cell's ohmic resistance is {ratio:.2f} x the average cell's: resistance or connection")
            elif lo and av and ev["rest_s"] >= P["min_relax_s"] and av["v_relaxed"] - lo["v_relaxed"] > P["relaxed_deficit_flag_v"]:
                rtxt = f"similar resistance (x{ratio:.2f})" if ratio is not None else "resistance not resolvable at this step"
                notes.append(f"{rtxt}, but the lowest cell relaxes {av['v_relaxed'] - lo['v_relaxed']:.3f} V lower: lower SOC "
                             "in that cell (imbalance), lower capacity, or a voltage sensing offset; one rest from BMS-reported voltages does "
                             "not separate them")
            if ev["rest_s"] < P["min_relax_s"]:
                ev["polarization"] = f"rest of {ev['rest_s']} s is shorter than {P['min_relax_s']:g} s: polarization not judged"
            for msg in notes:
                flagged.append(ev)
                res.findings.append(Finding("relaxation_deficit", f"{ev['window']} interruption at t={ev['t_step']} s ({ev['current_step_a']} A): {msg}.",
                                            ev_ref, {k: v for k, v in ev.items()}, "review"))
            if not notes:
                def r0txt(part):
                    return f"R0 {part['r_ohmic_ohm']} ohm" if part.get("r_ohmic_ohm") is not None else f"R0 {part['r_ohmic_note']}"
                desc = []
                if lo:
                    desc.append(f"lowest cell {lo['v_load']} V under load -> {lo['v_relaxed']} V after {ev['rest_s']} s at rest, {r0txt(lo)}" +
                                (f", slow recovery {lo['polarization_v']:+} V (63 % after {lo['tau_63_s']} s)" if lo.get("polarization_v") is not None else ""))
                if av:
                    desc.append(f"average cell {r0txt(av)}")
                if ev.get("polarization"):
                    desc.append(ev["polarization"])
                res.findings.append(Finding("relaxation", f"{ev['window']} interruption at t={ev['t_step']} s ({ev['current_step_a']} A): " + "; ".join(desc) + ".",
                                            ev_ref, {k: v for k, v in ev.items()}, "info"))
        res.windows["interruptions"] = {"items": events}
        res.calculations.append(Calculation("ohmic_resistance", "R0 = (V_instant_after - V_before) / I_before, per cell (pack / N for the average)",
                                            {"interruptions": len(events)}, None, "ohm", "lowest cell, pack voltage, pack current"))
        # OCV: the relaxed voltage -> SOC only with a curve, at temperature, outside flat regions
        bad_curve = cellphys.valid_curve(curve)
        long_rests = [ev for ev in events if ev["rest_s"] >= P["min_relax_s"] and ev.get("average_cell")]
        if bad_curve:
            res.resolve_by.append({"measurement": "the chemistry's OCV-SOC curve at temperature (with hysteresis), to read SOC from the relaxed voltage",
                                   "template": "DOC-OCV-CURVE"})
        else:
            for ev in long_rests:
                soc, slope = cellphys.ocv_inverse(curve, ev["average_cell"]["v_relaxed"])
                soc_lo, _ = cellphys.ocv_inverse(curve, ev["lowest_cell"]["v_relaxed"]) if ev.get("lowest_cell") else (None, None)
                if slope is None or slope < P["flat_slope_v_per_pp"]:
                    ev["ocv_soc"] = "flat OCV region (or outside the curve): the relaxed voltage does not establish SOC"
                else:
                    ev["ocv_soc"] = {"average_cell_pct": fmt(soc, 1), "lowest_cell_pct": fmt(soc_lo, 1), "curve_slope_v_per_pp": fmt(slope, 4),
                                     "verified_curve": bool(curve.get("verified"))}
        if not troles:
            res.assumptions.append("no temperature recorded (cell, pack or min/max): resistance and OCV depend on it")
        elif "cell_temperature" not in troles:
            res.assumptions.append(f"temperature taken from {', '.join(troles)} (not a per-cell temperature)")
        if not (profile.has_role("pack_voltage") and n):
            res.reasons.append("without pack voltage and series count, the lowest cell cannot be compared with the average cell")
            res.resolve_by.append({"measurement": "pack voltage and the series count", "template": "DOC-THRESHOLDS"})
        if any(ev["rest_s"] < P["min_relax_s"] for ev in events):
            res.resolve_by.append({"measurement": f"cell voltages and temperature logged at near-zero current for at least {P['min_relax_s']:g} s after the "
                                                  "interruption", "template": "DATA-POST-SHUTDOWN"})
        res.resolve_by.append({"measurement": "an independent voltage reference, to exclude a sensing error in the BMS-reported voltages", "template": None})
        if flagged:
            res.outcome = VIOLATION
            res.summary = f"{len(events)} interruption(s); the lowest cell departs from the average: " + "; ".join(
                f.statement.split(': ', 1)[1] for f in res.findings if f.kind == "relaxation_deficit")[:400]
        elif any(ev.get("average_cell") for ev in events):
            res.outcome = PASS
            res.summary = f"{len(events)} interruption(s): the lowest cell recovers like the average cell (ohmic and relaxed voltage)."
        else:
            res.outcome = INDETERMINATE
            res.summary = f"{len(events)} interruption(s) described for the lowest cell only; no average cell to compare with."
        return res


CHECK = base.register(Relaxation())
