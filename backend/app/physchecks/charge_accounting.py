"""Check A: charge accounting consistency between reported SOC and the measured current integral.

Relation: over any interval with continuous current records,
    expected SOC change (pp) = - Q_discharged / C x 100,      Q_discharged = integral of discharge current
where C is the capacity the SOC is defined against. The residual
    r(t) = [SOC(t) - SOC(t0)] - expected(t0, t)
stays within quantisation + current-sensor tolerance while SOC follows the measured charge.

What the check reports, per window:
  * CORRECTION EPISODES: places where r moves by more than the threshold within `detect_window_s`. Each is bounded by
    the last sample at the old SOC level and the first sample at the new level, and reports the reported SOC change,
    the measured charge over exactly that interval, what that charge explains, and the unexplained remainder, with the
    current that would have been needed to deliver it.
  * TRACKING SPANS: between episodes, how closely SOC followed the measured charge, and the capacity the SOC changes
    imply (a ratio of 1.0 means SOC moved exactly as measured charge divided by C).
  * POLARITY: if SOC consistently moves opposite to what the declared sign convention predicts, the convention (or the
    signal binding) is wrong. The check then says INDETERMINATE instead of reporting nonsense residuals.

Rules it follows: no resampling; integration only between current samples no further apart than `max_gap_s` (larger
gaps split the record and are listed); SOC is paired with the latest current sample no older than the role's
max_age_s; placeholder / out-of-range values are excluded and counted; a correction is "legitimate recalibration" only
when a VERIFIED recalibration marker covers it. Even then, its size is reported: it measures the estimation error that
had accumulated before the recalibration.
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Dict, List, Optional, Tuple

from app.physchecks import base
from app.physchecks.base import (INDETERMINATE, NOT_APPLICABLE, PASS, VIOLATION, Calculation, CheckResult, CheckSpec,
                                 Finding, InputSpec, ParamSpec, fmt)
from app.physchecks.profile import Profile, RoleSeries
from app.physchecks.recording import Recording

VERSION = "charge-accounting-v1"
OPS = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
       "==": lambda a, b: a == b, "!=": lambda a, b: a != b}

SPEC = CheckSpec(
    id="charge_accounting",
    version=VERSION,
    title="Charge accounting consistency",
    purpose="Is every reported SOC change explained by the charge the current sensor measured, against the stated capacity?",
    inputs=[
        InputSpec("pack_current", "A", "Pack current as measured by the BMS current sensor", sign_convention="declared in profile; converted to discharge-positive"),
        InputSpec("soc", "%", "SOC as reported by the BMS (an estimate, not a measurement)"),
        InputSpec("rated_capacity", "Ah", "Capacity field as reported in the data (a parameter, not a measured capacity)", required=False),
    ],
    parameters=[
        ParamSpec("capacity_ah", "Ah", "Capacity the SOC is defined against", required=True,
                  tolerance="must be > 0 and finite", source_rule="explicit parameter, else profile parameter, else the rated_capacity signal (labelled as a reported parameter)"),
        ParamSpec("detect_window_s", "s", "Window over which the residual is compared", False, 10.0),
        ParamSpec("min_unexplained_pp", "pp", "Smallest unexplained change reported as a correction", False, None,
                  source_rule="default max(2 pp, 5 x SOC resolution)"),
        ParamSpec("current_accuracy_a", "A", "Current sensor absolute accuracy", False, None, tolerance="if not stated, tolerance covers SOC quantisation only (stated as an assumption)"),
        ParamSpec("current_accuracy_rel", "-", "Current sensor relative accuracy (fraction of reading)", False, None),
        ParamSpec("recalibration_markers", "-", "Signals/conditions that mark a documented SOC recalibration (each with verified: true/false)", False, []),
        ParamSpec("min_tracking_s", "s", "Shortest span used to judge tracking", False, 30.0),
        ParamSpec("min_counting_evidence_s", "s", "When the profile does not declare the SOC estimator: the shortest span over which SOC must be "
                  "seen following measured charge before an unexplained change is called a violation", False, 600.0),
    ],
    applies_when=["the machine has a battery SOC and a pack current (roles soc and pack_current in the profile)"],
    alignment={"pairing": "latest current sample at or before each SOC sample, no older than the current role's max_age_s",
               "integration": "trapezoid between consecutive current samples; never across a gap larger than the profile's max_gap_s"},
    limitations=["SOC is an estimator output: a consistent residual says the estimator followed the measured charge, not that SOC was correct",
                 "A single current sensor cannot show its own bias; an independent measurement is needed for that",
                 "Charge drawn in unrecorded intervals (between windows, inside gaps) is not measured"],
)


class _Window:
    def __init__(self):
        self.out: Dict[str, Any] = {}
        self.findings: List[Finding] = []
        self.calcs: List[Calculation] = []
        self.reasons: List[str] = []
        self.outcome = PASS


def _capacity(rec: Recording, profile: Profile, params: Dict[str, Any]) -> Tuple[Optional[float], str, List[str]]:
    notes: List[str] = []
    if "capacity_ah" in params:
        return params["capacity_ah"], "explicit parameter", notes
    v, src = profile.parameter("capacity_ah")
    if v is not None:
        return v, f"profile parameter ({src})", notes
    if profile.has_role("rated_capacity"):
        vals: List[float] = []
        for w in rec.windows:
            vals += [s.value for s in profile.series(rec, w, "rated_capacity", "Ah").samples]
        if vals:
            distinct = sorted(set(vals))
            if len(distinct) > 1:
                notes.append(f"the rated_capacity field took {len(distinct)} values ({distinct[0]}–{distinct[-1]} Ah); the median is used")
            return statistics.median(vals), "rated_capacity signal (a reported parameter, not a measured capacity)", notes
    return None, "not stated", notes


def _valid_capacity(c: Any) -> Optional[str]:
    if c is None:
        return "no capacity is stated (parameter capacity_ah, profile parameter or rated_capacity signal)"
    try:
        c = float(c)
    except (TypeError, ValueError):
        return f"capacity {c!r} is not a number"
    if math.isnan(c) or math.isinf(c) or c <= 0:
        return f"capacity {c} Ah is not a usable positive value"
    if c > 1e5:
        return f"capacity {c} Ah is implausible for a battery pack"
    return None


class ChargeAccounting(base.Check):
    spec = SPEC

    def run(self, rec: Recording, profile: Profile, params: Optional[Dict[str, Any]] = None) -> CheckResult:
        params = dict(params or {})
        res = CheckResult(SPEC.id, VERSION, rec.id, rec.synthetic, NOT_APPLICABLE, "")
        res.input_digest = base.input_digest(rec, profile, SPEC.id, VERSION, params)
        missing = self.missing_roles(profile)
        if missing:
            res.reasons.append(f"the profile binds no signal for {', '.join(missing)}; this machine/recording is outside the check's scope")
            res.summary = "Not applicable: no SOC / pack current binding."
            return res
        res.inputs_used = {r: profile.describe(r) for r in ("pack_current", "soc", "rated_capacity") if profile.has_role(r)}
        cap, cap_src, cap_notes = _capacity(rec, profile, params)
        bad = _valid_capacity(cap)
        res.assumptions += cap_notes
        if bad:
            res.outcome = INDETERMINATE
            res.reasons.append(bad)
            res.summary = "Indeterminate: no valid capacity to convert measured charge into SOC points."
            return res
        cap = float(cap)
        sign = profile.role("pack_current").get("sign")
        res.parameters_used = {"capacity_ah": cap, "capacity_source": cap_src, "current_sign": sign}
        if "rated_capacity" in cap_src or "profile" in cap_src:
            res.assumptions.append(f"SOC is assumed to be defined against {cap:g} Ah ({cap_src}); a tracking ratio near 1.0 supports that the estimator uses it")
        acc_a, acc_rel = params.get("current_accuracy_a"), params.get("current_accuracy_rel")
        if acc_a is None and acc_rel is None:
            res.assumptions.append("current sensor accuracy is not stated: tolerances cover SOC quantisation only")
        outcomes: List[str] = []
        for w in rec.windows:
            win = self._window(rec, profile, w, cap, params)
            if win is None:
                continue
            res.windows[w] = {"outcome": win.outcome, **win.out, "reasons": win.reasons}
            res.findings += win.findings
            res.calculations += win.calcs
            outcomes.append(win.outcome)
            for r in win.reasons:
                res.reasons.append(f"{w}: {r}")
        res.outcome = base.combine(outcomes) if outcomes else INDETERMINATE
        if not outcomes:
            res.reasons.append("no window holds both SOC and pack current samples")
        self._estimator_gate(res, profile, params)
        self._independence(res, profile)
        res.parameters_used.update({k: params[k] for k in ("detect_window_s", "min_unexplained_pp", "current_accuracy_a", "current_accuracy_rel") if k in params})
        eps = [f for f in res.findings if f.kind == "unexplained_soc_correction"]
        rec_eps = [f for f in res.findings if f.kind == "declared_recalibration"]
        if res.outcome == VIOLATION:
            worst = max(eps, key=lambda f: abs(f.values["unexplained_pp"]))
            v = worst.values
            res.summary = (f"{len(eps)} SOC correction(s) not explained by measured charge; largest: {v['soc_before_pct']} % -> {v['soc_after_pct']} % "
                           f"in {v['duration_s']} s ({v['window']}), measured charge {v['measured_charge_ah']} Ah explains {v['explained_pp']} pp, "
                           f"{abs(v['unexplained_pp'])} pp unexplained.")
        elif res.outcome == PASS:
            res.summary = "SOC changes are explained by the measured charge within tolerance" + (f"; {len(rec_eps)} documented recalibration(s) reported separately." if rec_eps else ".")
        elif res.outcome == INDETERMINATE:
            res.summary = "Indeterminate: " + "; ".join(res.reasons[:3])
        else:
            res.summary = "Not applicable."
        res.uncertainty = {"soc_resolution_pp": next((v.get("soc_resolution_pp") for v in res.windows.values() if v.get("soc_resolution_pp") is not None), None),
                           "integration": "numeric bound = |left-rectangle - right-rectangle| / 2 per interval; sensor accuracy as stated"}
        return res

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _independence(res: CheckResult, profile: Profile) -> None:
        """Provenance rule: agreement between SOC and the current it is counted from is not evidence that either is right."""
        ind = profile.independence("soc", "pack_current")
        if ind is True:
            return
        if ind is None:
            res.independence.append({"signals": ["soc", "pack_current"], "independent": None,
                                     "because": "the profile marks SOC as an estimate but does not say how it is computed",
                                     "means": "whether SOC is counted from this same current is unknown: until that is stated, SOC following the "
                                              "measured charge cannot count as evidence that the current sensor or the capacity is right. A change "
                                              "it does not explain still shows the estimator used another input there."})
            res.resolve_by.append({"measurement": "how the BMS computes SOC (its inputs and update logic)", "template": "DOC-SOC-ALGORITHM"})
            return
        res.independence.append({"signals": ["soc", "pack_current"], "independent": False,
                                 "because": f"SOC is derived from {', '.join(profile.derived_from('soc')) or 'pack_current'}",
                                 "means": "where SOC follows the measured charge, that agreement is by construction: it does not show the "
                                          "current sensor or the capacity is right. A change it does not explain shows the estimator used "
                                          "another input there; which estimate was right is not decided by this check."})
        res.resolve_by += [{"measurement": "independent current measurement against the BMS current", "template": "MEAS-INDEPENDENT-CURRENT"},
                           {"measurement": "an existing, independently metered charge/discharge record with known endpoints", "template": "DATA-METERED-CYCLE"},
                           {"measurement": "the SOC estimator's update logic and its correction trace", "template": "DOC-SOC-ALGORITHM"}]

    @staticmethod
    def _estimator_gate(res: CheckResult, profile: Profile, params: Dict[str, Any]) -> None:
        """An SOC change the measured charge does not explain is a VIOLATION only for an estimator that is supposed to
        count charge. The profile may declare soc.estimator: coulomb_counting | voltage_based | mixed | unknown (default).
        voltage_based / mixed: such an estimator moves with voltage and load by design -> INDETERMINATE.
        unknown: the recording itself must show SOC following measured charge (a tracking span of at least
        min_counting_evidence_s that changed by at least 3 SOC steps); otherwise INDETERMINATE.
        The episodes stay listed as observations either way."""
        est = (profile.role("soc").get("estimator") or "unknown").lower()
        need_s = float(params.get("min_counting_evidence_s", 600.0))
        spans = [sp for w in res.windows.values() for sp in (w.get("tracking_spans") or [])]
        resol = {wn: (w.get("soc_resolution_pp") or 1.0) for wn, w in res.windows.items()}
        shown = [sp for sp in spans if sp.get("tracks") and sp["duration_s"] >= need_s
                 and abs(sp["expected_change_pp"]) >= 3 * resol.get(sp["window"], 1.0)]
        res.parameters_used["soc_estimator"] = est
        res.parameters_used["counting_shown_in_recording"] = bool(shown)
        if shown:
            sp = max(shown, key=lambda x: x["duration_s"])
            res.assumptions.append(f"SOC followed measured charge for {sp['duration_s']:g} s in {sp['window']} (t={sp['t_start']}..{sp['t_end']} s, "
                                   f"{sp['reported_change_pp']:+} pp reported vs {sp['expected_change_pp']:+} pp from charge): the estimator counts charge there")
        if res.outcome != VIOLATION:
            return
        if est == "coulomb_counting" or (est == "unknown" and shown):
            return
        why = (f"the profile declares the SOC estimator {est!r}: it moves with voltage and load by design, so a change the measured charge "
               "does not explain is expected and is not a fault" if est in ("voltage_based", "mixed") else
               "the profile does not say how SOC is estimated, and this recording does not show it following measured charge "
               f"(no tracking span of {need_s:g} s or more): a change the charge does not explain may be how this estimator works")
        res.outcome = INDETERMINATE
        res.reasons.insert(0, why)
        for f in res.findings:
            if f.kind == "unexplained_soc_correction":
                f.kind, f.severity = "soc_change_not_from_charge", "review"
        for w in res.windows.values():
            if w.get("outcome") == VIOLATION:
                w["outcome"] = INDETERMINATE
                w.setdefault("reasons", []).append(why)

    def _window(self, rec: Recording, profile: Profile, w: str, cap: float, params: Dict[str, Any]) -> Optional[_Window]:
        cur = profile.series(rec, w, "pack_current", "A")
        soc = profile.series(rec, w, "soc", "%")
        if not rec.has(w, cur.field) and not rec.has(w, soc.field):
            return None
        win = _Window()
        dq = {"pack_current_excluded": cur.excluded, "soc_excluded": soc.excluded,
              "pack_current_samples": len(cur.samples), "soc_samples": len(soc.samples)}
        win.out["data_quality"] = dq
        if not cur.samples or not soc.samples:
            win.outcome = INDETERMINATE
            win.reasons.append("no valid " + ("pack current" if not cur.samples else "SOC") + " samples")
            return win
        sgn = 1.0 if profile.role("pack_current").get("sign") == "discharge_positive" else -1.0
        res_pp = soc.resolution if soc.resolution is not None else _infer_resolution([s.value for s in soc.samples])
        if soc.resolution is None:
            win.out["soc_resolution_note"] = "inferred from the smallest SOC step in the data"
        win.out["soc_resolution_pp"] = res_pp
        W = float(params.get("detect_window_s", 10.0))
        min_unexp = params.get("min_unexplained_pp")
        min_unexp = float(min_unexp) if min_unexp is not None else max(2.0, 5 * res_pp)
        acc_a = float(params.get("current_accuracy_a") or 0.0)
        acc_rel = float(params.get("current_accuracy_rel") or 0.0)
        max_gap = profile.max_gap_s
        max_age = cur.max_age_s

        # 1. continuous current segments and cumulative charge (A*s, discharge-positive)
        ct = [s.t for s in cur.samples]
        ci = [sgn * s.value for s in cur.samples]
        seg_id: List[int] = []
        qcum: List[float] = []
        qbound: List[float] = []
        gaps: List[Dict[str, Any]] = []
        sid = 0
        # Source-segment changes do not split the record by themselves: overlapping source segments interleave frames of
        # the same bus, and integrating time-ordered samples does not double count. Only a time gap splits it.
        alternations = 0
        for k in range(len(ct)):
            if k == 0:
                qcum.append(0.0); qbound.append(0.0); seg_id.append(0)
                continue
            dt = ct[k] - ct[k - 1]
            if rec.segment_of(w, cur.field, cur.samples[k].index) != rec.segment_of(w, cur.field, cur.samples[k - 1].index):
                alternations += 1
            if dt > max_gap:
                sid += 1
                gaps.append({"from_t": fmt(ct[k - 1]), "to_t": fmt(ct[k]), "gap_s": fmt(dt),
                             "evidence": [rec.ref(w, cur.field, cur.samples[k - 1].index).as_dict(), rec.ref(w, cur.field, cur.samples[k].index).as_dict()]})
                qcum.append(0.0); qbound.append(0.0)
            else:
                qcum.append(qcum[-1] + 0.5 * (ci[k] + ci[k - 1]) * dt)
                qbound.append(qbound[-1] + 0.5 * abs(ci[k] - ci[k - 1]) * dt)
            seg_id.append(sid)
        win.out["current_segments"] = sid + 1
        dq["source_segment_changes"] = alternations
        if alternations > 1:
            dq["source_segment_note"] = (f"source segment changed {alternations} times between consecutive current samples: "
                                         "overlapping source segments interleave; kept as recorded, split only at time gaps")
        win.out["current_gaps"] = gaps[:20]
        win.out["current_gap_count"] = len(gaps)
        win.out["measured_discharge_ah"] = fmt(sum(_seg_totals(qcum, seg_id).values()) / 3600.0, 4)

        # 2. pair each SOC sample with the current record
        import bisect
        groups: Dict[int, List[Tuple[float, float, float, float, int]]] = {}  # seg -> (t, soc, Q As, Qbound As, soc sample idx)
        unaligned = 0
        for n, s in enumerate(soc.samples):
            j = bisect.bisect_right(ct, s.t) - 1
            if j < 0 or s.t - ct[j] > max_age:
                unaligned += 1
                continue
            q = qcum[j] + ci[j] * (s.t - ct[j])
            groups.setdefault(seg_id[j], []).append((s.t, s.value, q, qbound[j], n))
        dq["soc_samples_without_fresh_current"] = unaligned
        # SOC changes that straddle a gap in the current record: the charge drawn inside the gap is not measured
        across: List[Dict[str, Any]] = []
        keys = sorted(groups)
        for a, b in zip(keys, keys[1:]):
            last, first = groups[a][-1], groups[b][0]
            change = first[1] - last[1]
            if abs(change) >= 2 * res_pp - 1e-9:
                across.append({"window": w, "t_before": fmt(last[0]), "t_after": fmt(first[0]), "soc_before_pct": fmt(last[1], 2),
                               "soc_after_pct": fmt(first[1], 2), "change_pp": fmt(change, 2)})
                win.findings.append(Finding(
                    "soc_change_across_gap",
                    f"{w}: SOC changed {change:+.2f} pp across a gap in the current record (t={last[0]:.3f} -> {first[0]:.3f} s). "
                    "The charge drawn inside the gap is not measured, so this change cannot be assessed.",
                    [rec.ref(w, soc.field, soc.samples[last[4]].index), rec.ref(w, soc.field, soc.samples[first[4]].index)],
                    across[-1], "review"))
        win.out["soc_changes_across_gaps"] = across
        if not any(len(g) >= 2 for g in groups.values()):
            win.outcome = INDETERMINATE
            win.reasons.append(f"no two SOC samples share a continuous current record (max sample age {max_age} s, max gap {max_gap} s)")
            return win

        # 3. per continuous group: residual, correction episodes, tracking spans
        episodes: List[Dict[str, Any]] = []
        spans: List[Dict[str, Any]] = []
        k_c = 100.0 / (cap * 3600.0)                                     # pp per A*s
        for g in groups.values():
            if len(g) < 2:
                continue
            ts = [x[0] for x in g]
            ss = [x[1] for x in g]
            r = [(x[1] - g[0][1]) + (x[2] - g[0][2]) * k_c for x in g]
            thr = 2 * res_pp + 0.1 + (acc_a * W + acc_rel * 0) * k_c
            flagged: List[Tuple[int, int]] = []
            j = 0
            for k in range(len(g)):
                while j + 1 < len(g) and ts[j + 1] <= ts[k] - W:
                    j += 1
                if ts[j] <= ts[k] - W and abs(r[k] - r[j]) > thr:
                    flagged.append((k, j))
            runs: List[List[Tuple[int, int]]] = []
            for kj in flagged:
                if runs and ts[kj[0]] - ts[runs[-1][-1][0]] <= W:
                    runs[-1].append(kj)
                else:
                    runs.append([kj])
            bounds: List[Tuple[int, int]] = []
            for run in runs:
                k0, j0 = run[0]
                k1 = run[-1][0]
                s_i = max(i for i in range(j0, k0 + 1) if abs(r[i] - r[j0]) <= res_pp + 1e-9)
                e_i = min(i for i in range(s_i, k1 + 1) if abs(r[i] - r[k1]) <= res_pp + 1e-9)
                if e_i <= s_i:
                    e_i = k1
                bounds.append((s_i, e_i))
            for s_i, e_i in bounds:
                q_as = g[e_i][2] - g[s_i][2]
                q_ah = q_as / 3600.0
                expected = -q_as * k_c
                reported = ss[e_i] - ss[s_i]
                unexplained = reported - expected
                if abs(unexplained) < min_unexp:
                    continue
                dur = ts[e_i] - ts[s_i]
                tol = 2 * res_pp + (acc_a * dur + acc_rel * abs(q_as)) * k_c + (g[e_i][3] - g[s_i][3]) * k_c
                needed_a = abs(unexplained) / 100.0 * cap * 3600.0 / dur if dur > 0 else None
                episodes.append({"window": w, "t_start": fmt(ts[s_i]), "t_end": fmt(ts[e_i]), "duration_s": fmt(dur),
                                 "soc_before_pct": fmt(ss[s_i], 2), "soc_after_pct": fmt(ss[e_i], 2),
                                 "reported_change_pp": fmt(reported, 2), "measured_charge_ah": fmt(q_ah, 4),
                                 "explained_pp": fmt(-expected, 3), "expected_change_pp": fmt(expected, 3),
                                 "unexplained_pp": fmt(unexplained, 2), "tolerance_pp": fmt(tol, 3),
                                 "rate_pp_per_s": fmt(reported / dur, 3) if dur > 0 else None,
                                 "current_needed_to_explain_a": fmt(needed_a, 0),
                                 "mean_measured_current_a": fmt(q_as / dur, 2) if dur > 0 else None,
                                 "_refs": [rec.ref(w, soc.field, soc.samples[g[s_i][4]].index), rec.ref(w, soc.field, soc.samples[g[e_i][4]].index)]})
            # tracking spans between episodes
            cuts = [0] + [i for b in bounds for i in b] + [len(g) - 1]
            for a, b in zip(cuts[0::2], cuts[1::2]):
                if b <= a or ts[b] - ts[a] < float(params.get("min_tracking_s", 30.0)):
                    continue
                dev = max(abs(r[i] - r[a]) for i in range(a, b + 1))
                q_as = g[b][2] - g[a][2]
                exp_pp = -q_as * k_c
                rep_pp = ss[b] - ss[a]
                spans.append({"window": w, "t_start": fmt(ts[a]), "t_end": fmt(ts[b]), "duration_s": fmt(ts[b] - ts[a]),
                              "soc_start_pct": fmt(ss[a], 2), "soc_end_pct": fmt(ss[b], 2), "reported_change_pp": fmt(rep_pp, 2),
                              "measured_charge_ah": fmt(q_as / 3600.0, 4), "expected_change_pp": fmt(exp_pp, 3),
                              "max_deviation_pp": fmt(dev, 3), "end_residual_pp": fmt(rep_pp - exp_pp, 3),
                              "ratio_reported_to_expected": fmt(rep_pp / exp_pp, 3) if abs(exp_pp) >= 2 * res_pp else None,
                              "ratio_uncertainty": fmt(2 * res_pp / abs(exp_pp), 3) if abs(exp_pp) >= 2 * res_pp else None,
                              "_refs": [rec.ref(w, soc.field, soc.samples[g[a][4]].index), rec.ref(w, soc.field, soc.samples[g[b][4]].index)]})

        # 4. polarity: SOC moving against the declared convention over the tracking spans
        sum_rep = sum(s["reported_change_pp"] for s in spans)
        sum_exp = sum(s["expected_change_pp"] for s in spans)
        win.out["tracking_reported_change_pp"] = fmt(sum_rep, 2)
        win.out["tracking_expected_change_pp"] = fmt(sum_exp, 3)
        if abs(sum_exp) >= 3 * res_pp and sum_rep / sum_exp < -0.5:
            win.outcome = INDETERMINATE
            win.reasons.append(f"SOC moved {sum_rep:+.2f} pp while the declared sign convention ({profile.role('pack_current').get('sign')}) predicts "
                               f"{sum_exp:+.2f} pp: the current polarity or binding is likely reversed; residuals are not reported until it is confirmed")
            win.findings.append(Finding("polarity_suspect", win.reasons[-1], [x for s in spans[:2] for x in s["_refs"]],
                                        {"reported_pp": fmt(sum_rep, 2), "expected_pp": fmt(sum_exp, 3)}, "review"))
            win.out["episodes"], win.out["tracking_spans"] = [], [_clean(s) for s in spans]
            return win

        # 5. recalibration markers
        markers = params.get("recalibration_markers") or []
        for ep in episodes:
            ep["recalibration"] = None
            for m in markers:
                hit = _marker_hit(rec, profile, w, m, ep["t_start"] - W, ep["t_end"] + W)
                if hit is not None:
                    ep["recalibration"] = {"marker": m.get("description") or f"{m.get('role')} {m.get('op')} {m.get('value')}",
                                           "verified": bool(m.get("verified")), "evidence": hit.as_dict()}
                    if m.get("verified"):
                        break
        unexplained = [e for e in episodes if not (e["recalibration"] and e["recalibration"]["verified"])]
        for ep in episodes:
            refs = ep.pop("_refs")
            verified = ep["recalibration"] and ep["recalibration"]["verified"]
            kind = "declared_recalibration" if verified else "unexplained_soc_correction"
            stmt = (f"{w}: SOC went {ep['soc_before_pct']} % -> {ep['soc_after_pct']} % between t={ep['t_start']} s and t={ep['t_end']} s "
                    f"({ep['duration_s']} s). Measured charge over the same interval: {ep['measured_charge_ah']} Ah = {ep['explained_pp']} pp at "
                    f"{cap:g} Ah. Unexplained: {abs(ep['unexplained_pp'])} pp (tolerance {ep['tolerance_pp']} pp). Delivering it would need "
                    f"about {ep['current_needed_to_explain_a']:g} A for that time; the measured mean was {ep['mean_measured_current_a']} A.")
            if verified:
                stmt += (f" A verified recalibration marker covers it ({ep['recalibration']['marker']}); its size is the estimation error "
                         "accumulated before the recalibration.")
            elif ep["recalibration"]:
                stmt += f" An UNVERIFIED recalibration rule matches ({ep['recalibration']['marker']}); it is not treated as an explanation until verified."
            win.findings.append(Finding(kind, stmt, refs, {k: v for k, v in ep.items()}, "review" if verified else "violation"))
            win.calcs.append(Calculation(
                f"{w} correction at {ep['t_start']} s", "unexplained = (SOC_end - SOC_start) - ( - Q / C x 100 ),  Q = sum 1/2 (I_k + I_k+1)(t_k+1 - t_k)",
                {"SOC_start_pct": ep["soc_before_pct"], "SOC_end_pct": ep["soc_after_pct"], "Q_ah": ep["measured_charge_ah"], "C_ah": cap,
                 "t_start": ep["t_start"], "t_end": ep["t_end"]},
                ep["unexplained_pp"], "pp", f"SOC: {profile.describe('soc')}; current: {profile.describe('pack_current')}; rows cited in the finding"))
        for sp in spans:
            refs = sp.pop("_refs")
            ok = abs(sp["end_residual_pp"]) <= 2 * res_pp + (acc_a * sp["duration_s"] + acc_rel * abs(sp["measured_charge_ah"] * 3600)) * k_c + 1e-9
            sp["tracks"] = ok
            ratio = sp["ratio_reported_to_expected"]
            stmt = (f"{w}: from t={sp['t_start']} to {sp['t_end']} s SOC changed {sp['reported_change_pp']:+} pp; measured charge {sp['measured_charge_ah']} Ah "
                    f"predicts {sp['expected_change_pp']:+} pp at {cap:g} Ah (largest deviation {sp['max_deviation_pp']} pp)"
                    + (f"; ratio {ratio} -> the SOC moved as measured charge / {cap / ratio:.1f} Ah." if ratio else "."))
            win.findings.append(Finding("soc_tracks_measured_charge" if ok else "soc_drift", stmt, refs, sp, "info" if ok else "review"))
        win.out["episodes"] = episodes
        win.out["tracking_spans"] = spans
        if unexplained:
            win.outcome = VIOLATION
        elif across:
            win.outcome = INDETERMINATE
            win.reasons.append(f"{len(across)} SOC change(s) fall across gaps in the current record and cannot be assessed")
        elif not spans and not episodes:
            win.outcome = INDETERMINATE
            win.reasons.append(f"no continuous span of at least {params.get('min_tracking_s', 30.0)} s with SOC and current to judge")
        else:
            win.outcome = PASS
        return win


def _seg_totals(qcum: List[float], seg_id: List[int]) -> Dict[int, float]:
    out: Dict[int, float] = {}
    for q, s in zip(qcum, seg_id):
        out[s] = q
    return out


def _infer_resolution(vals: List[float]) -> float:
    steps = sorted({round(abs(b - a), 6) for a, b in zip(vals, vals[1:]) if abs(b - a) > 1e-9})
    return steps[0] if steps else 1.0


def _marker_hit(rec: Recording, profile: Profile, w: str, m: Dict[str, Any], t0: float, t1: float):
    role = m.get("role")
    if not role or not profile.has_role(role) or m.get("op") not in OPS:
        return None
    s = profile.series(rec, w, role)
    for x in s.samples:
        if t0 <= x.t <= t1 and OPS[m["op"]](x.value, float(m.get("value"))):
            return rec.ref(w, s.field, x.index)
    return None


def _clean(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in d.items() if not k.startswith("_")}


CHECK = base.register(ChargeAccounting())
