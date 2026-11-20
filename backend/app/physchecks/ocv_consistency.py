"""Check G: SOC implied by the rested voltage (through the chemistry's OCV curve) against the reported SOC and the counted charge.

At rest points (near-zero current long enough to relax) the average cell voltage gives an OCV-implied SOC. The error
e = reported SOC - OCV SOC across rest points, and the counted charge between them, follow different patterns:
  * a constant e:                         wrong starting SOC, OCV-model error or a voltage bias
  * e growing with charge throughput:     wrong capacity, or a current-sensor scale error
  * e growing with time (not throughput): current-sensor offset
These can remain indistinguishable without independent measurements (e.g. a steady discharge makes throughput and time
move together). Between two rest points, counted Ah / OCV-SOC change gives an implied capacity; capacity and current scale
are not separated by it. Needs a curve; only a VERIFIED curve supports a VIOLATION. External EE advisor's proposal (2026-11-16).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.physchecks import base, cellphys
from app.physchecks.base import (INDETERMINATE, NOT_APPLICABLE, PASS, VIOLATION, Calculation, CheckResult, CheckSpec, Finding,
                                 InputSpec, ParamSpec, fmt)
from app.physchecks.profile import Profile
from app.physchecks.recording import Recording

VERSION = "ocv-consistency-v1"

SPEC = CheckSpec(
    id="ocv_consistency",
    version=VERSION,
    title="Rested-voltage SOC against reported SOC and counted charge",
    purpose="At rest points, does the SOC implied by the OCV curve agree with the reported SOC, and how does any disagreement grow?",
    inputs=[InputSpec("pack_current", "A", "Pack current", sign_convention="converted to discharge-positive"),
            InputSpec("soc", "%", "Reported SOC (an estimate)"),
            InputSpec("pack_voltage", "V", "Pack voltage (average cell = pack / N)", required=False),
            InputSpec("min_cell_voltage", "V", "Lowest cell", required=False), InputSpec("max_cell_voltage", "V", "Highest cell", required=False)],
    parameters=[ParamSpec("ocv_curve", "-", "Chemistry OCV curve {soc_pct, v, temperature_c, verified}", True, source_rule="customer / cell supplier"),
                ParamSpec("series_count", "-", "Cells in series", False, source_rule="the pack specification"),
                ParamSpec("capacity_ah", "Ah", "Capacity the SOC is counted against", False, None),
                ParamSpec("rest_max_a", "A", "Current counts as rest at most", False, 1.0),
                ParamSpec("min_rest_s", "s", "Shortest rest before the voltage is read as OCV", False, 1200.0,
                          source_rule="default 20 min (an assumption; chemistry-dependent)"),
                ParamSpec("flat_slope_v_per_pp", "V/pp", "OCV slope below this: voltage does not establish SOC", False, 0.002),
                ParamSpec("tolerance_pp", "pp", "SOC disagreement tolerated", False, 5.0)],
    applies_when=["current and reported SOC are recorded", "an OCV curve is given", "at least one long enough rest"],
    alignment={"rest point": "the last voltage sample of a rest at least min_rest_s long"},
    limitations=["Needs the chemistry's OCV curve at temperature; hysteresis is not modelled",
                 "Flat OCV regions (e.g. LFP plateaus) do not establish SOC",
                 "Capacity error and current scale error give the same pattern unless current is measured independently",
                 "A short rest overstates the deficit (the cell is still relaxing)"],
)


class OcvConsistency(base.Check):
    spec = SPEC

    def run(self, rec: Recording, profile: Profile, params: Optional[Dict[str, Any]] = None) -> CheckResult:
        params = dict(params or {})
        res = CheckResult(SPEC.id, VERSION, rec.id, rec.synthetic, NOT_APPLICABLE, "")
        res.provenance = dict(cellphys.PROVENANCE)
        res.input_digest = base.input_digest(rec, profile, SPEC.id, VERSION, params)
        missing = self.missing_roles(profile)
        if missing:
            res.reasons.append(f"the profile binds no signal for {', '.join(missing)}")
            res.summary = "Not applicable: pack current or reported SOC is not recorded."
            return res
        curve, curve_src = cellphys.param(params, profile, "ocv_curve")
        bad = cellphys.valid_curve(curve)
        if bad:
            res.outcome = INDETERMINATE
            res.reasons.append(bad)
            res.resolve_by.append({"measurement": "the chemistry's OCV-SOC curve at temperature (with hysteresis), from the cell supplier", "template": "DOC-OCV-CURVE"})
            res.summary = "Indeterminate: no OCV curve, so the rested voltage cannot be turned into SOC."
            return res
        P = {k: float(params.get(k, d)) for k, d in (("rest_max_a", 1.0), ("min_rest_s", 1200.0), ("flat_slope_v_per_pp", 0.002), ("tolerance_pp", 5.0))}
        n, _ = cellphys.param(params, profile, "series_count")
        n = int(n) if n else None
        verified = bool(curve.get("verified"))
        res.parameters_used.update({**P, "series_count": n, "ocv_curve_source": curve_src, "ocv_curve_verified": verified,
                                    "ocv_curve_temperature_c": curve.get("temperature_c")})
        if "min_rest_s" not in params:
            res.assumptions.append(f"a rest of {P['min_rest_s']:g} s is taken as relaxed (default; chemistry-dependent)")
        if n and profile.has_role("pack_voltage"):
            vrole, scale, vdesc = "pack_voltage", 1.0 / n, "pack / N"
        elif profile.has_role("min_cell_voltage") and profile.has_role("max_cell_voltage"):
            vrole, scale, vdesc = None, None, "(lowest + highest) / 2"
            res.assumptions.append("the average cell is approximated by (lowest + highest cell) / 2")
        else:
            res.outcome = INDETERMINATE
            res.reasons.append("no pack voltage with series count, nor lowest/highest cell voltage")
            res.resolve_by.append({"measurement": "pack voltage and the series count", "template": "DOC-THRESHOLDS"})
            res.summary = "Indeterminate: no cell voltage to read against the curve."
            return res
        sign = 1.0 if profile.role("pack_current").get("sign", "discharge_positive") == "discharge_positive" else -1.0
        points: List[Dict[str, Any]] = []
        for w in rec.windows:
            cur = profile.series(rec, w, "pack_current", "A").samples
            soc = profile.series(rec, w, "soc", "%")
            if vrole:
                vs = profile.series(rec, w, vrole, "V")
                volt = lambda t: next((s.value * scale for s in reversed(vs.samples) if s.t <= t), None)  # noqa: E731
            else:
                lo, hi = profile.series(rec, w, "min_cell_voltage", "V"), profile.series(rec, w, "max_cell_voltage", "V")
                def volt(t, lo=lo, hi=hi):
                    a = next((s.value for s in reversed(lo.samples) if s.t <= t), None)
                    b = next((s.value for s in reversed(hi.samples) if s.t <= t), None)
                    return None if a is None or b is None else (a + b) / 2
            q, last_t, last_i, rest_start, rest_q, rest_last = 0.0, None, None, None, 0.0, None
            for s in cur:
                if last_t is not None and s.t - last_t <= profile.max_gap_s:
                    q += sign * (s.value + last_i) / 2 * (s.t - last_t) / 3600.0
                last_t, last_i = s.t, s.value
                if abs(s.value) <= P["rest_max_a"]:
                    if rest_start is None:
                        rest_start, rest_q = s.t, q
                    rest_last = s.t
                else:
                    if rest_start is not None:
                        self._point(points, w, rest_start, rest_last, volt, soc, rest_q, P)
                    rest_start = None
            if rest_start is not None:
                self._point(points, w, rest_start, rest_last, volt, soc, rest_q, P)
        if not points:
            res.outcome = INDETERMINATE
            res.reasons.append(f"no rest of at least {P['min_rest_s']:g} s at |I| <= {P['rest_max_a']:g} A")
            res.resolve_by.append({"measurement": f"cell voltages logged during a rest of at least {P['min_rest_s']:g} s", "template": "DATA-POST-SHUTDOWN"})
            res.summary = "Indeterminate: no long enough rest to read the OCV."
            return res
        usable = []
        for p in points:
            ocv_soc, slope = cellphys.ocv_inverse(curve, p["v_cell"])
            p["curve_slope_v_per_pp"] = fmt(slope, 4)
            if slope is None or slope < P["flat_slope_v_per_pp"]:
                p["ocv_soc_pct"] = None
                p["note"] = "flat OCV region or outside the curve: the voltage does not establish SOC here"
                continue
            p["ocv_soc_pct"] = fmt(ocv_soc, 1)
            p["error_pp"] = fmt(p["soc_reported_pct"] - ocv_soc, 2)
            usable.append(p)
        res.windows["rest_points"] = {"items": points, "voltage": vdesc}
        if not usable:
            res.outcome = INDETERMINATE
            res.reasons.append("every rest point lies in a flat region of the OCV curve (or outside it)")
            res.resolve_by.append({"measurement": "a rest at a SOC where the OCV curve is not flat, or an independent charge measurement", "template": "DATA-METERED-CYCLE"})
            res.summary = "Indeterminate: the rested voltages fall where the OCV curve is flat."
            return res
        errs = [p["error_pp"] for p in usable]
        worst = max(usable, key=lambda p: abs(p["error_pp"]))
        pattern, notes = None, []
        if len(usable) >= 3:
            fit = cellphys.ols(errs, {"throughput_ah": [p["counted_ah"] for p in usable], "time_s": [p["t"] for p in usable]})
            if fit is None or abs(fit["corr"].get("throughput_ah~time_s", 1.0)) > 0.95:
                pattern = "not separable"
                notes.append("throughput and time moved together between rest points: a capacity / current-scale error and a current offset "
                             "cannot be told apart from this record")
            else:
                grow_q = abs(fit["coef"]["throughput_ah"]) > 3 * fit["se"]["throughput_ah"]
                grow_t = abs(fit["coef"]["time_s"]) > 3 * fit["se"]["time_s"]
                pattern = ("grows with throughput: capacity or current scale error" if grow_q and not grow_t else
                           "grows with time: current offset" if grow_t and not grow_q else
                           "grows with both throughput and time" if grow_q and grow_t else "constant: starting SOC, OCV model or voltage bias")
        elif len(usable) == 2:
            a, b = usable
            d_ocv = b["ocv_soc_pct"] - a["ocv_soc_pct"]
            dq = b["counted_ah"] - a["counted_ah"]
            if abs(d_ocv) >= 5:
                cap_impl = -dq / (d_ocv / 100.0)
                cap, _ = cellphys.param(params, profile, "capacity_ah")
                notes.append(f"between the two rest points {fmt(dq, 2)} Ah was counted while the OCV SOC moved {fmt(d_ocv, 1)} pp: implied capacity "
                             f"{fmt(cap_impl, 1)} Ah" + (f" against {cap} Ah stated (capacity or current scale; not separated)" if cap else ""))
            pattern = "two points: growth pattern not established"
        else:
            pattern = "one point: growth pattern not established"
        res.windows["pattern"] = {"pattern": pattern, "notes": notes}
        res.calculations.append(Calculation("ocv_soc_error", "e = reported SOC - OCV_curve^-1(rested average cell voltage)",
                                            {"rest_points": len(points), "usable": len(usable)}, errs, "pp", f"{vdesc}, reported SOC, OCV curve ({curve_src})"))
        off = abs(worst["error_pp"]) > P["tolerance_pp"]
        if off:
            res.findings.append(Finding("ocv_soc_disagreement",
                                        f"At rest ({worst['window']} t={worst['t']} s) the reported SOC is {worst['soc_reported_pct']} % while the OCV curve "
                                        f"gives {worst['ocv_soc_pct']} % ({worst['error_pp']:+} pp). Pattern: {pattern}.", [], worst,
                                        "review" if verified else "info"))
            res.resolve_by += [{"measurement": "independent current measurement against the BMS current", "template": "MEAS-INDEPENDENT-CURRENT"},
                               {"measurement": "an existing independently metered charge/discharge record", "template": "DATA-METERED-CYCLE"}]
        for x in notes:
            res.findings.append(Finding("ocv_pattern_note", x, [], {}, "info"))
        if off and verified:
            res.outcome = VIOLATION
            res.summary = f"Reported SOC disagrees with the rested-voltage SOC by up to {abs(worst['error_pp'])} pp; pattern: {pattern}."
        elif off:
            res.outcome = INDETERMINATE
            res.reasons.append("the OCV curve is not verified: a disagreement is reported but not called a violation")
            res.summary = f"Indeterminate (unverified curve): reported SOC differs from the rested-voltage SOC by up to {abs(worst['error_pp'])} pp; pattern: {pattern}."
        else:
            res.outcome = PASS
            res.summary = f"At {len(usable)} rest point(s) the reported SOC agrees with the rested-voltage SOC within {P['tolerance_pp']:g} pp."
        return res

    @staticmethod
    def _point(points, w, t0, t1, volt, soc, q, P):
        if t1 - t0 < P["min_rest_s"]:
            return
        v = volt(t1)
        s = next((x.value for x in reversed(soc.samples) if x.t <= t1), None)
        if v is None or s is None:
            return
        points.append({"window": w, "t": fmt(t1), "rest_s": fmt(t1 - t0, 1), "v_cell": fmt(v, 4), "soc_reported_pct": fmt(s, 1), "counted_ah": fmt(q, 3)})


CHECK = base.register(OcvConsistency())
