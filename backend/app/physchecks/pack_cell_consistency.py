"""Check D: pack voltage against the cell voltages (a relationship every series pack must obey).

With every cell recorded, the cells sum to the pack voltage. With only the lowest and highest cell recorded (the usual
BMS summary), the weaker bound still holds: N x min cell <= pack voltage <= N x max cell, i.e. the average cell
(pack / N) lies between the lowest and the highest cell. A persistent breach means a voltage measurement (pack or cell)
is wrong, or the two do not cover the same cells.

Provenance rule: if the pack voltage is computed from the cell voltages (profile derived_from), agreement is not evidence
and the check abstains. Relationship and rule: external EE advisor's proposal (2026-11-16); see cellphys.PROVENANCE.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.physchecks import base, cellphys
from app.physchecks.base import (INDETERMINATE, NOT_APPLICABLE, PASS, VIOLATION, Calculation, CheckResult, CheckSpec, Finding,
                                 InputSpec, ParamSpec, fmt)
from app.physchecks.profile import Profile
from app.physchecks.recording import Recording

VERSION = "pack-cell-consistency-v1"

SPEC = CheckSpec(
    id="pack_cell_consistency",
    version=VERSION,
    title="Pack voltage against cell voltages",
    purpose="Does the average cell (pack voltage / series count) lie between the lowest and highest recorded cell?",
    inputs=[InputSpec("pack_voltage", "V", "Pack voltage (must be measured independently of the cell voltages)"),
            InputSpec("min_cell_voltage", "V", "Lowest cell voltage"),
            InputSpec("max_cell_voltage", "V", "Highest cell voltage"),
            InputSpec("pack_current", "A", "Pack current, to skip pairs taken across a current step", required=False)],
    parameters=[ParamSpec("series_count", "-", "Cells in series", True, source_rule="the pack specification (customer spec)"),
                ParamSpec("pack_voltage_accuracy_v", "V", "Pack voltage sensor accuracy", False, None,
                          source_rule="sensor spec; if absent 0.5 % of the reading (an assumption)"),
                ParamSpec("cell_voltage_accuracy_v", "V", "Cell voltage sensor accuracy", False, None,
                          source_rule="sensor spec; if absent 5 mV (an assumption)"),
                ParamSpec("max_current_change_a", "A", "Pairs across a larger current change are skipped (sampling skew)", False, 5.0),
                ParamSpec("min_persist_samples", "-", "Consecutive breaches before a breach is reported", False, 5)],
    applies_when=["pack voltage and lowest/highest cell voltage are recorded", "the series count is stated"],
    alignment={"pairing": "each pack-voltage sample with the latest cell (and current) samples no older than the roles' max_age_s"},
    limitations=["With only the lowest and highest cell this is a bound, not the sum: a sensing error that keeps the average "
                 "cell between them is not seen",
                 "If the pack voltage is computed from the cells, agreement is by construction (the check abstains)",
                 "Parallel groups count as one cell; a mismatch can also mean the pack sensor and the cells span different "
                 "electrical boundaries (e.g. across a fuse or contactor)"],
)


class PackCellConsistency(base.Check):
    spec = SPEC

    def run(self, rec: Recording, profile: Profile, params: Optional[Dict[str, Any]] = None) -> CheckResult:
        params = dict(params or {})
        res = CheckResult(SPEC.id, VERSION, rec.id, rec.synthetic, NOT_APPLICABLE, "")
        res.provenance = dict(cellphys.PROVENANCE)
        res.input_digest = base.input_digest(rec, profile, SPEC.id, VERSION, params)
        missing = self.missing_roles(profile)
        if missing:
            res.reasons.append(f"the profile binds no signal for {', '.join(missing)}")
            res.summary = "Not applicable: pack voltage or lowest/highest cell voltage is not recorded."
            return res
        res.inputs_used = {r: profile.describe(r) for r in ("pack_voltage", "min_cell_voltage", "max_cell_voltage", "pack_current") if profile.has_role(r)}
        closure = profile.inputs_closure("pack_voltage")
        if "cell_voltages" in closure or "min_cell_voltage" in closure or "max_cell_voltage" in closure:
            res.outcome = INDETERMINATE
            res.independence.append({"signals": ["pack_voltage", "cell voltages"], "independent": False,
                                     "because": "the profile declares the pack voltage derived from the cell voltages",
                                     "means": "agreement between them is by construction and is not evidence"})
            res.reasons.append("the pack voltage is computed from the cell voltages, so comparing them shows nothing")
            res.resolve_by.append({"measurement": "an independently measured pack voltage (e.g. the inverter's or charger's DC-link "
                                                  "voltage, or a meter on the pack terminals)", "template": None})
            res.summary = "Indeterminate: the pack voltage is not independent of the cell voltages."
            return res
        n, n_src = cellphys.param(params, profile, "series_count")
        try:
            n = int(n) if n is not None else None
        except (TypeError, ValueError):
            n = None
        if not n or n < 1:
            res.outcome = INDETERMINATE
            res.reasons.append("the series count is not stated, so the average cell cannot be formed")
            res.resolve_by.append({"measurement": "the number of cells in series, from the pack specification", "template": "DOC-THRESHOLDS"})
            res.summary = "Indeterminate: the series count is not stated."
            return res
        pv_acc, pv_src = cellphys.param(params, profile, "pack_voltage_accuracy_v")
        cv_acc, cv_src = cellphys.param(params, profile, "cell_voltage_accuracy_v", 0.005)
        di_max = float(params.get("max_current_change_a", 5.0))
        persist = int(params.get("min_persist_samples", 5))
        res.parameters_used.update({"series_count": n, "series_count_source": n_src, "cell_voltage_accuracy_v": cv_acc,
                                    "cell_voltage_accuracy_source": cv_src, "pack_voltage_accuracy_source": pv_src if pv_acc is not None else
                                    "default 0.5 % of the reading (an assumption, not a specification)"})
        if cv_src.startswith("default"):
            res.assumptions.append("cell voltage accuracy 5 mV (default, not a sensor specification)")
        if pv_acc is None:
            res.assumptions.append("pack voltage accuracy 0.5 % of the reading (default, not a sensor specification)")
        breaches_all: List[Dict[str, Any]] = []
        pairs = 0
        for w in rec.windows:
            others = ["min_cell_voltage", "max_cell_voltage"] + (["pack_current"] if profile.has_role("pack_current") else [])
            al = cellphys.Aligned(rec, profile, w, "pack_voltage", others, {"pack_voltage": "V", "min_cell_voltage": "V", "max_cell_voltage": "V", "pack_current": "A"})
            rows = al.rows
            prev_i = None
            flags, devs, use = [], [], []
            for r in rows:
                i = r.get("pack_current")
                skip = prev_i is not None and i is not None and abs(i - prev_i) > di_max
                prev_i = i
                if skip:
                    continue
                tol_pack = (float(pv_acc) if pv_acc is not None else 0.005 * abs(r["pack_voltage"])) / n + float(cv_acc)
                avg = r["pack_voltage"] / n
                dev = (r["min_cell_voltage"] - avg) if avg < r["min_cell_voltage"] else (avg - r["max_cell_voltage"]) if avg > r["max_cell_voltage"] else 0.0
                flags.append(dev > tol_pack)
                devs.append(dev if avg > r["max_cell_voltage"] else -dev)
                use.append(r)
            pairs += len(use)
            for a, b in cellphys.runs(flags):
                if b - a < persist:
                    continue
                k = max(range(a, b), key=lambda j: abs(devs[j]))
                r = use[k]
                breaches_all.append({"window": w, "t_start": fmt(use[a]["t"]), "t_end": fmt(use[b - 1]["t"]), "samples": b - a,
                                     "worst_avg_cell_minus_bound_v": fmt(devs[k], 4), "pack_v": fmt(r["pack_voltage"], 3),
                                     "avg_cell_v": fmt(r["pack_voltage"] / n, 4), "min_cell_v": fmt(r["min_cell_voltage"], 4),
                                     "max_cell_v": fmt(r["max_cell_voltage"], 4),
                                     "evidence": [rec.ref(w, profile.role(x)["field"], r["_ref"][x]) for x in ("pack_voltage", "min_cell_voltage", "max_cell_voltage")]})
            res.windows[w] = {"pairs": len(use), "breach_runs": sum(1 for x in breaches_all if x["window"] == w), "excluded": al.excluded()}
        res.calculations.append(Calculation("average_cell", "pack_voltage / series_count, compared with [min_cell - tol, max_cell + tol]",
                                            {"series_count": n}, None, "V", "pack_voltage, min/max cell voltage"))
        if pairs == 0:
            res.outcome = INDETERMINATE
            res.reasons.append("no pack-voltage sample pairs with fresh lowest/highest cell samples")
            res.resolve_by.append({"measurement": "pack and cell voltages logged at a common rate (or with timestamps)", "template": "REC-TELEMETRY"})
            res.summary = "Indeterminate: no usable pack/cell pairs."
            return res
        for b in breaches_all:
            above = b["worst_avg_cell_minus_bound_v"] > 0
            res.findings.append(Finding("pack_cell_mismatch",
                                        f"{b['window']} t={b['t_start']}..{b['t_end']} s ({b['samples']} samples): the average cell "
                                        f"({b['avg_cell_v']} V = pack {b['pack_v']} V / {n}) lies {'above the highest' if above else 'below the lowest'} "
                                        f"cell ({b['max_cell_v'] if above else b['min_cell_v']} V) by {abs(b['worst_avg_cell_minus_bound_v'])} V beyond tolerance.",
                                        b.pop("evidence"), b, "review"))
        if breaches_all:
            res.outcome = VIOLATION
            res.summary = (f"{len(breaches_all)} persistent stretch(es) where pack voltage / {n} lies outside the recorded cell range: a pack or "
                           "cell voltage measurement is wrong, or they span different cells. Voltage sensing cannot be excluded.")
            res.resolve_by.append({"measurement": "an independent voltage reference on the pack terminals and on the suspect cells", "template": None})
        else:
            res.outcome = PASS
            res.summary = (f"The average cell (pack / {n}) stays within the recorded lowest/highest cell over {pairs} pairs: no sign of a pack/cell "
                           "voltage sensing disagreement (a bound check; a sensing error that keeps the average inside the range is not seen).")
        return res


CHECK = base.register(PackCellConsistency())
