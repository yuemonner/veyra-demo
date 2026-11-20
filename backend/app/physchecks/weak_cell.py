"""Check E: what the lowest cell's deficit follows (weak-cell attribution).

The deficit d = average cell (pack / N) - lowest cell is fitted as d = a + R x I + k x (100 - SOC):
  * R significantly > 0: the deficit scales with current -> extra resistance or a connection (to first order a cell is
    OCV - I x R; the lowest cell has more R than the average)
  * k significantly > 0: the deficit grows as SOC falls -> reduced capacity or imbalance (which of the two depends on chemistry
    and prior charge history; this check does not separate them)
  * steps in d with no matching step in current or SOC -> a sensing candidate
If current barely varied, or varied together with SOC, the terms overlap and the check says the data cannot rank them
(it abstains on that question and names the record that would separate them). External EE advisor's proposal (2026-11-16).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.physchecks import base, cellphys
from app.physchecks.base import (INDETERMINATE, NOT_APPLICABLE, PASS, VIOLATION, Calculation, CheckResult, CheckSpec, Finding,
                                 InputSpec, ParamSpec, fmt)
from app.physchecks.profile import Profile
from app.physchecks.recording import Recording

VERSION = "weak-cell-attribution-v1"

SPEC = CheckSpec(
    id="weak_cell_attribution",
    version=VERSION,
    title="Weak-cell attribution",
    purpose="Does the lowest cell's deficit scale with current (resistance / connection), grow at low SOC (capacity or imbalance), "
            "or jump on its own (sensing) - and can the data tell these apart?",
    inputs=[InputSpec("pack_voltage", "V", "Pack voltage"), InputSpec("min_cell_voltage", "V", "Lowest cell voltage"),
            InputSpec("pack_current", "A", "Pack current", sign_convention="converted to discharge-positive"),
            InputSpec("soc", "%", "Reported SOC (an estimate)", required=False),
            InputSpec("min_cell_index", "", "Which cell is lowest", required=False)],
    parameters=[ParamSpec("series_count", "-", "Cells in series", True, source_rule="the pack specification"),
                ParamSpec("cell_voltage_accuracy_v", "V", "Cell voltage sensor accuracy", False, None, source_rule="sensor spec; else 5 mV (an assumption)"),
                ParamSpec("max_collinearity", "-", "Above this |correlation| between current and SOC their effects are not separated", False, 0.9),
                ParamSpec("jump_v", "V", "A deficit step at least this large with no current/SOC step is a sensing candidate", False, 0.02),
                ParamSpec("min_pairs", "-", "Fewest pairs for a fit", False, 60),
                ParamSpec("max_current_change_a", "A", "Pairs across a larger current change are skipped (sampling skew)", False, 5.0),
                ParamSpec("min_current_span_a", "A", "Current must vary at least this much to judge a resistance effect", False, 10.0),
                ParamSpec("min_soc_span_pp", "pp", "SOC must vary at least this much to judge a capacity / imbalance effect", False, 5.0)],
    applies_when=["pack voltage, lowest cell voltage and pack current are recorded", "the series count is stated"],
    alignment={"pairing": "pack-voltage samples with the latest lowest-cell, current and SOC samples within max_age_s"},
    limitations=["The average cell is pack / N: a pack-voltage sensing error shifts every deficit (see pack_cell_consistency)",
                 "If the lowest cell changes identity, the deficit belongs to whichever cell is lowest at each moment",
                 "Capacity loss and imbalance both make the deficit grow at low SOC; separating them needs a record of which "
                 "cell reaches the voltage limits first, with initial balance, temperature and balancing accounted for",
                 "BMS-reported voltages: excluding a sensing error needs an independent voltage reference"],
)


class WeakCellAttribution(base.Check):
    spec = SPEC

    def run(self, rec: Recording, profile: Profile, params: Optional[Dict[str, Any]] = None) -> CheckResult:
        params = dict(params or {})
        res = CheckResult(SPEC.id, VERSION, rec.id, rec.synthetic, NOT_APPLICABLE, "")
        res.provenance = dict(cellphys.PROVENANCE)
        res.input_digest = base.input_digest(rec, profile, SPEC.id, VERSION, params)
        missing = self.missing_roles(profile)
        if missing:
            res.reasons.append(f"the profile binds no signal for {', '.join(missing)}")
            res.summary = "Not applicable: pack voltage, lowest cell voltage or pack current is not recorded."
            return res
        n, n_src = cellphys.param(params, profile, "series_count")
        try:
            n = int(n) if n is not None else None
        except (TypeError, ValueError):
            n = None
        if not n:
            res.outcome = INDETERMINATE
            res.reasons.append("the series count is not stated, so the average cell cannot be formed")
            res.resolve_by.append({"measurement": "the number of cells in series, from the pack specification", "template": "DOC-THRESHOLDS"})
            res.summary = "Indeterminate: the series count is not stated."
            return res
        acc, acc_src = cellphys.param(params, profile, "cell_voltage_accuracy_v", 0.005)
        acc = float(acc)
        if acc_src.startswith("default"):
            res.assumptions.append("cell voltage accuracy 5 mV (default, not a sensor specification)")
        maxcol = float(params.get("max_collinearity", 0.9))
        jump_v = float(params.get("jump_v", 0.02))
        res.inputs_used = {r: profile.describe(r) for r in ("pack_voltage", "min_cell_voltage", "pack_current", "soc", "min_cell_index") if profile.has_role(r)}
        res.parameters_used.update({"series_count": n, "series_count_source": n_src, "cell_voltage_accuracy_v": acc})
        sign = 1.0 if profile.role("pack_current").get("sign", "discharge_positive") == "discharge_positive" else -1.0
        rows: List[Dict[str, Any]] = []
        idx_seen = set()
        for w in rec.windows:
            others = ["min_cell_voltage", "pack_current"] + (["soc"] if profile.has_role("soc") else [])
            al = cellphys.Aligned(rec, profile, w, "pack_voltage", others, {"pack_voltage": "V", "min_cell_voltage": "V", "pack_current": "A", "soc": "%"})
            for r in al.rows:
                r["w"] = w
                r["I"] = sign * r["pack_current"]
                r["d"] = r["pack_voltage"] / n - r["min_cell_voltage"]
                rows.append(r)
            if profile.has_role("min_cell_index"):
                idx_seen |= {s.value for s in profile.series(rec, w, "min_cell_index").samples}
        if len(rows) < int(params.get("min_pairs", 60)):
            res.outcome = INDETERMINATE
            res.reasons.append(f"only {len(rows)} usable pairs")
            res.resolve_by.append({"measurement": "a longer record of pack voltage, lowest cell voltage and current", "template": "REC-TELEMETRY"})
            res.summary = "Indeterminate: too few pack/cell/current pairs."
            return res
        if len(idx_seen) > 1:
            res.assumptions.append(f"the lowest cell changed identity ({len(idx_seen)} different cells were lowest): the deficit belongs to "
                                   "whichever cell is lowest at each moment")
        # skip pairs taken across a current change (pack and cell messages are sampled at different instants)
        di_max = float(params.get("max_current_change_a", 5.0))
        keep = [r for k, r in enumerate(rows) if k == 0 or rows[k - 1]["w"] != r["w"] or abs(r["I"] - rows[k - 1]["I"]) <= di_max]
        rows = keep
        # sensing candidates: persistent steps in the deficit AFTER removing what current and SOC explain (two passes), so a step
        # that coincides with a current change is still seen; it must hold for 3 windows after it (a transient decays, a step stays)
        base_x = {"current_a": [r["I"] for r in rows]}
        if all("soc" in r for r in rows):
            base_x["soc_depth_pp"] = [100.0 - r["soc"] for r in rows]
        pre = cellphys.ols([r["d"] for r in rows], base_x)
        resid = [r["d"] - (pre["intercept"] + sum(pre["coef"][k] * base_x[k][i] for k in base_x)) for i, r in enumerate(rows)] if pre else [r["d"] for r in rows]
        med = lambda xs: sorted(xs)[len(xs) // 2]  # noqa: E731
        jumps, k, h = [], 8, 8
        while k + 3 * h <= len(rows):
            if len({rows[i]["w"] for i in range(k - h, k + 3 * h)}) == 1:
                b0 = med(resid[k - h:k])
                a1, a2, a3 = med(resid[k:k + h]), med(resid[k + h:k + 2 * h]), med(resid[k + 2 * h:k + 3 * h])
                dd = a1 - b0
                held = all(abs(x - b0) >= 0.7 * abs(dd) and (x - b0) * dd > 0 for x in (a2, a3))
                ds = abs(rows[k + h].get("soc", 0) - rows[k - h].get("soc", 0))
                if abs(dd) >= jump_v and held and ds < 2.0:
                    jumps.append({"window": rows[k]["w"], "t": fmt(rows[k]["t"]), "deficit_step_v": fmt(dd, 4), "k": k})
                    k += 3 * h
                    continue
            k += 1
        xs = {"current_a": [r["I"] for r in rows]}
        if all("soc" in r for r in rows):
            xs["soc_depth_pp"] = [100.0 - r["soc"] for r in rows]
        for j, jp in enumerate(jumps):                 # each sensing step is fitted as its own offset
            xs[f"step_{j}"] = [1.0 if (i >= jp["k"]) else 0.0 for i in range(len(rows))]
        for jp in jumps:
            jp.pop("k")
        fit = cellphys.ols([r["d"] for r in rows], xs)
        res.windows["fit"] = {k: v for k, v in (fit or {}).items()}
        res.windows["sensing_candidates"] = {"items": jumps[:20], "count": len(jumps)}
        res.calculations.append(Calculation("deficit_fit", "d = pack/N - min_cell = a + R*I + k*(100 - SOC) (least squares)",
                                            {"pairs": len(rows), "series_count": n}, fit and {"R_ohm": fmt(fit['coef'].get('current_a'), 5),
                                                                                              "k_v_per_pp": fmt(fit['coef'].get('soc_depth_pp'), 5)},
                                            "ohm, V/pp", "pack voltage, lowest cell, pack current, reported SOC"))
        if fit is None:
            res.outcome = INDETERMINATE
            res.reasons.append("the fit could not be formed")
            res.summary = "Indeterminate: the deficit fit could not be formed."
            return res
        supports, indisting = [], []
        noise = max(acc, fit["resid_sd"])
        col = abs(fit["corr"].get("current_a~soc_depth_pp", 0.0)) if "soc_depth_pp" in xs else 0.0
        if col != col:                               # NaN when a regressor is constant
            col = 1.0
        terms = {"current_a": ("resistance_or_connection", "the deficit scales with current"),
                 "soc_depth_pp": ("capacity_or_imbalance", "the deficit grows as SOC falls")}
        separable = col <= maxcol
        spans = {"current_a": float(params.get("min_current_span_a", 10.0)), "soc_depth_pp": float(params.get("min_soc_span_pp", 5.0))}
        absent = []
        for k, (label, meaning) in terms.items():
            if k not in fit["coef"]:
                continue
            c, se, rng = fit["coef"][k], fit["se"][k], fit["range"][k]
            effect = c * rng
            if k == "soc_depth_pp" and fit["range"].get("current_a", 0) < spans["current_a"]:
                indisting.append({"explanation": label, "because": "current hardly varied: a deficit that grows at low SOC can also be resistance rising "
                                                                   "at low SOC; varied current at similar SOC is needed to separate them"})
            elif rng < spans[k]:
                indisting.append({"explanation": label, "because": f"{k.split('_')[0]} varied over only {fmt(rng, 2)} "
                                                                    f"({'A' if k == 'current_a' else 'pp'}), less than {spans[k]:g}: its effect cannot be seen"})
            elif not separable:
                indisting.append({"explanation": label, "because": f"current and SOC moved together (|correlation| {fmt(col, 3)} > {maxcol}): their effects are not separated"})
            elif c > 3 * se and effect > 3 * noise:
                supports.append({"explanation": label, "meaning": meaning, "coefficient": fmt(c, 5), "se": fmt(se, 5), "effect_over_range_v": fmt(effect, 4)})
            else:
                absent.append({"explanation": label, "because": f"{k.split('_')[0]} varied over {fmt(rng, 2)} with no significant effect "
                                                               f"({fmt(effect, 4)} V over the range)"})
        res.windows["fit"]["absent"] = absent
        if jumps:
            supports.append({"explanation": "sensing", "meaning": f"{len(jumps)} step(s) in the deficit with no current or SOC step", "examples": jumps[:3]})
        res.windows["attribution"] = {"supported": supports, "not_separable": indisting,
                                      "mean_deficit_v": fmt(sum(r["d"] for r in rows) / len(rows), 4)}
        for s_ in supports:
            res.findings.append(Finding(f"deficit_{s_['explanation']}", f"The lowest cell's deficit supports {s_['explanation'].replace('_', ' ')}: {s_['meaning']}"
                                        + (f" (R = {s_['coefficient']} ohm +- {s_['se']})" if s_["explanation"] == "resistance_or_connection" else ""),
                                        [], s_, "review"))
        for x in indisting:
            res.findings.append(Finding("deficit_not_separable", f"{x['explanation'].replace('_', ' ')}: not decidable from this record - {x['because']}.", [], x, "info"))
        if any(s_["explanation"] == "capacity_or_imbalance" for s_ in supports) or any(x["explanation"] == "capacity_or_imbalance" for x in indisting):
            res.resolve_by.append({"measurement": "an existing independently metered charge/discharge record showing which cell reaches the voltage "
                                                  "limits first (with initial balance, temperature and balancing activity)", "template": "DATA-METERED-CYCLE"})
        if indisting:
            res.resolve_by.append({"measurement": "a record where current varies at a similar SOC (or a current interruption, see relaxation)",
                                   "template": "DATA-POST-SHUTDOWN"})
        if supports:
            res.outcome = VIOLATION
            res.summary = ("The lowest cell's deficit supports: " + ", ".join(s_["explanation"].replace("_", " ") for s_ in supports) +
                           (". Not separable here: " + ", ".join(x["explanation"].replace("_", " ") for x in indisting) if indisting else "") + ".")
        elif indisting:
            res.outcome = INDETERMINATE
            res.reasons += [f"{x['explanation']}: {x['because']}" for x in indisting]
            res.summary = "Indeterminate: " + "; ".join(f"{x['explanation'].replace('_', ' ')} - {x['because']}" for x in indisting)
        else:
            res.outcome = PASS
            res.summary = (f"The lowest cell tracks the average cell: current and SOC varied enough and neither changes the deficit, no sensing steps "
                           f"(mean deficit {res.windows['attribution']['mean_deficit_v']} V).")
        return res


CHECK = base.register(WeakCellAttribution())
