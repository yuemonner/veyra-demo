"""Check C: restart eligibility after a protective HV shutdown.

Read from the data, across all windows in time order:
  HV closed      both main contactors report closed (feedback signals, not a contact measurement)
  protective     an HV opening that follows a fault-level escalation within `protective_window_s`
  shutdown
  reset          the fault level returning to 0. A reset whose transition falls in a gap or between windows is marked
                 "not observed" (the record shows before and after, not the moment or its cause)
  reclosure      HV closing again after a protective shutdown

For each reclosure: the shutdown it follows, the reset that preceded it (observed or not), the lowest-cell voltage at
the closure (latest fresh sample), what happened next (escalation, next protective shutdown), and the lowest cell
voltage while HV was on, compared with the lowest-cell voltage at the original protective trip.

Flags for ENGINEERING REVIEW (never called a safety violation without the customer's restart-permission logic):
  reclosed_after_protective_shutdown, repeated_trip_cycles, deeper_discharge_than_original_trip, reset_not_observed.

Outcome: no HV closure/opening or no protective shutdown -> NOT_APPLICABLE. Protective shutdown(s) and no reclosure ->
PASS (a factual statement, independent of policy). Reclosures without a VERIFIED restart policy -> INDETERMINATE with the
flags. With a verified policy (schema veyra-restart-policy-v1: max_reclosures_after_protective_shutdown,
min_cell_voltage_to_close_v) -> PASS or VIOLATION with rows.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.physchecks import base
from app.physchecks.base import (INDETERMINATE, NOT_APPLICABLE, PASS, VIOLATION, CheckResult, CheckSpec, Finding,
                                 InputSpec, ParamSpec, fmt)
from app.physchecks.events import Timeline, Transition, transitions
from app.physchecks.profile import Profile
from app.physchecks.recording import Recording

VERSION = "restart-eligibility-v1"
POLICY_SCHEMA = "veyra-restart-policy-v1"

SPEC = CheckSpec(
    id="restart_eligibility",
    version=VERSION,
    title="Restart eligibility",
    purpose="After a protective HV shutdown, when and in what condition was HV closed again, and does anything warrant engineering review?",
    inputs=[InputSpec("main_positive_closed", "", "Main positive contactor feedback"),
            InputSpec("main_negative_closed", "", "Main negative contactor feedback"),
            InputSpec("bms_fault_level", "", "BMS fault level, normalised"),
            InputSpec("min_cell_voltage", "V", "Lowest cell voltage", required=False),
            InputSpec("precharge_closed", "", "Precharge contactor feedback", required=False)],
    parameters=[ParamSpec("policy", "-", f"Customer restart-permission policy ({POLICY_SCHEMA})", False, None,
                          source_rule="the customer's restart logic and parameters; only verified: true supports VIOLATION"),
                ParamSpec("protective_window_s", "s", "An HV opening this soon after an escalation counts as protective", False, 30.0),
                ParamSpec("min_protective_level", "", "Lowest escalated level that makes an opening protective", False, 2)],
    applies_when=["main contactor feedback and a fault level are recorded", "a protective HV shutdown occurs"],
    alignment={"cell voltage at closure": "latest sample at or before the closure, no older than the role's max_age_s",
               "across windows": "windows are ordered on the shared time axis; time between windows is unrecorded"},
    limitations=["Without the restart-permission logic, a reclosure cannot be called permitted or forbidden",
                 "Contactor feedback is not a physical contact measurement",
                 "Why a fault reset happened is not recorded"],
)


def _hv_events(rec: Recording, profile: Profile) -> List[Dict[str, Any]]:
    """HV closed/open events from the two main-contactor feedbacks (both closed = HV closed)."""
    ev: List[Dict[str, Any]] = []
    for w in rec.windows:
        trs = sorted(transitions(rec, profile, "main_positive_closed", [w]) + transitions(rec, profile, "main_negative_closed", [w]),
                     key=lambda x: (x.t, x.role))
        state = {"main_positive_closed": None, "main_negative_closed": None}
        closed: Optional[bool] = None
        for tr in trs:
            state[tr.role] = tr.new
            if None in state.values():
                continue
            now = state["main_positive_closed"] == 1 and state["main_negative_closed"] == 1
            if closed is None:
                ev.append({"kind": "initial_closed" if now else "initial_open", "window": w, "t": tr.t, "ref": tr.ref, "time_known": True, "initial": True})
            elif now != closed:
                ev.append({"kind": "close" if now else "open", "window": w, "t": tr.t, "ref": tr.ref, "time_known": tr.time_known, "initial": False})
            closed = now
    return ev


class RestartEligibility(base.Check):
    spec = SPEC

    def run(self, rec: Recording, profile: Profile, params: Optional[Dict[str, Any]] = None) -> CheckResult:
        params = dict(params or {})
        res = CheckResult(SPEC.id, VERSION, rec.id, rec.synthetic, NOT_APPLICABLE, "")
        res.input_digest = base.input_digest(rec, profile, SPEC.id, VERSION, params)
        missing = self.missing_roles(profile)
        if missing:
            res.reasons.append(f"the profile binds no signal for {', '.join(missing)}")
            res.summary = "Not applicable: no contactor feedback / fault level."
            return res
        res.inputs_used = {r: profile.describe(r) for r in ("main_positive_closed", "main_negative_closed", "bms_fault_level", "min_cell_voltage") if profile.has_role(r)}
        pw = float(params.get("protective_window_s", 30.0))
        min_level = float(params.get("min_protective_level", 2))
        hv = _hv_events(rec, profile)
        if not any(e["kind"] in ("close", "open") for e in hv):
            res.reasons.append("no HV closing or opening is recorded")
            res.summary = "Not applicable: HV state never changes in the recording."
            return res
        faults = [t for t in transitions(rec, profile, "bms_fault_level")]
        esc = [t for t in faults if not t.initial and t.old is not None and t.new > t.old]
        resets = [t for t in faults if not t.initial and t.old is not None and t.old > 0 and t.new == 0]
        cell = Timeline(rec, profile, "min_cell_voltage")
        order = {w: i for i, w in enumerate(rec.windows)}

        def before(a_w: str, a_t: float, b_w: str, b_t: float) -> bool:
            return (order[a_w], a_t) < (order[b_w], b_t)

        shutdowns: List[Dict[str, Any]] = []
        for e in hv:
            if e["kind"] != "open":
                continue
            prior = [x for x in esc if x.window == e["window"] and e["t"] - pw <= x.t <= e["t"] and x.new >= min_level]
            if prior:
                trig = max(prior, key=lambda x: (x.new, x.t))
                cv = cell.at(trig.window, trig.t, max_age=max(cell.max_age, 1.0)) if cell.available() else None
                shutdowns.append({"window": e["window"], "t": e["t"], "ref": e["ref"], "trigger": trig,
                                  "cell_at_trigger": cv})
        res.windows["hv_events"] = {"items": [{k: (v.as_dict() if k == "ref" else v) for k, v in e.items()} for e in hv]}
        if not shutdowns:
            res.reasons.append(f"no HV opening follows a fault escalation to level >= {min_level:g} within {pw:g} s")
            res.summary = "Not applicable: no protective HV shutdown in the recording."
            return res
        first = shutdowns[0]
        closes = [e for e in hv if e["kind"] == "close" and before(first["window"], first["t"], e["window"], e["t"])]
        sd_items = []
        for s in shutdowns:
            cv = s["cell_at_trigger"]
            sd_items.append({"window": s["window"], "t": round(s["t"], 3), "trigger_level": s["trigger"].new,
                             "trigger_t": round(s["trigger"].t, 3), "min_cell_v_at_trigger": None if cv is None else cv[0],
                             "evidence": [s["ref"].as_dict(), s["trigger"].ref.as_dict()] + ([cv[2].as_dict()] if cv else [])})
            res.findings.append(Finding("protective_shutdown",
                                        f"{s['window']}: HV opened at t={s['t']:.3f} s, {s['t'] - s['trigger'].t:.3f} s after the fault level rose to "
                                        f"{s['trigger'].new:g} (t={s['trigger'].t:.3f} s)" + (f"; lowest cell then {cv[0]:.3f} V" if cv else "") + ".",
                                        [s["ref"], s["trigger"].ref] + ([cv[2]] if cv else []), sd_items[-1]))
        res.windows["protective_shutdowns"] = {"items": sd_items}
        if not closes:
            res.outcome = PASS
            res.summary = f"{len(shutdowns)} protective shutdown(s); HV was not closed again afterwards in the record."
            return res

        first_cell = first["cell_at_trigger"][0] if first["cell_at_trigger"] else None
        recl: List[Dict[str, Any]] = []
        for n, c in enumerate(closes, 1):
            prev_sd = [s for s in shutdowns if before(s["window"], s["t"], c["window"], c["t"])][-1]
            rs = [r for r in resets if before(prev_sd["window"], prev_sd["t"], r.window, r.t) and before(r.window, r.t, c["window"], c["t"])]
            reset_obs = bool(rs) and rs[-1].time_known and rs[-1].window == c["window"]
            if not rs:
                reset_note = "no reset recorded between the shutdown and this closure"
            elif not reset_obs:
                reset_note = f"fault level returned to 0 inside an unrecorded interval (between t={rs[-1].t_prev} and {rs[-1].t:.3f} s)" if rs[-1].t_prev is not None else "the reset happened before the window started"
            else:
                reset_note = f"fault level returned to 0 at t={rs[-1].t:.3f} s"
            # was the fault level already 0 when the window opened?
            init_lvl = next((t for t in faults if t.initial and t.window == c["window"]), None)
            if not rs and init_lvl is not None and init_lvl.new == 0:
                reset_note = f"the window opens with fault level 0 (t={init_lvl.t:.3f} s): the reset happened in unrecorded time"
            cv = cell.at(c["window"], c["t"], max_age=max(cell.max_age, 1.0)) if cell.available() else None
            nxt = [e for e in hv if e["kind"] == "open" and before(c["window"], c["t"], e["window"], e["t"])]
            hv_on_end = nxt[0] if nxt else None
            end_t = hv_on_end["t"] if hv_on_end and hv_on_end["window"] == c["window"] else (rec.span(c["window"]) or (c["t"], c["t"]))[1]
            low = cell.extreme(c["window"], c["t"], end_t, min) if cell.available() else None
            nxt_sd = next((s for s in shutdowns if hv_on_end and s["t"] == hv_on_end["t"] and s["window"] == hv_on_end["window"]), None)
            esc_after = [x for x in esc if x.window == c["window"] and c["t"] <= x.t <= end_t]
            item = {"n": n, "window": c["window"], "t": round(c["t"], 3), "after_shutdown_t": round(prev_sd["t"], 3),
                    "after_shutdown_window": prev_sd["window"], "reset": reset_note, "reset_observed": reset_obs,
                    "min_cell_v_at_close": None if cv is None else cv[0],
                    "escalations_while_on": [{"to": x.new, "t": round(x.t, 3), "after_s": round(x.t - c["t"], 3)} for x in esc_after],
                    "ended_by_protective_shutdown": None if nxt_sd is None else round(nxt_sd["t"], 3),
                    "hv_on_until": None if hv_on_end is None or hv_on_end["window"] != c["window"] else round(hv_on_end["t"], 3),
                    "lowest_cell_v_while_on": None if low is None else low[0],
                    "evidence": [c["ref"].as_dict()] + ([cv[2].as_dict()] if cv else []) + ([low[1].as_dict()] if low else [])}
            recl.append(item)
        res.windows["reclosures"] = {"items": recl}

        flags: List[Finding] = []
        refs_close = [c["ref"] for c in closes]
        flags.append(Finding("reclosed_after_protective_shutdown",
                             f"HV was closed {len(closes)} time(s) after the protective shutdown at t={first['t']:.3f} s ({first['window']}): "
                             + ", ".join(f"{r['t']} s" for r in recl) + ". Whether this was permitted depends on the restart logic, which is not provided.",
                             refs_close, {"count": len(closes)}, "review"))
        cycles = [r for r in recl if r["ended_by_protective_shutdown"] is not None]
        if len(cycles) >= 2:
            flags.append(Finding("repeated_trip_cycles", f"{len(cycles)} closures each ended in another protective shutdown: "
                                 + ", ".join(f"closed {r['t']} s -> tripped {r['ended_by_protective_shutdown']} s" for r in cycles) + ".",
                                 refs_close, {"cycles": len(cycles)}, "review"))
        lows = [r for r in recl if r["lowest_cell_v_while_on"] is not None]
        if first_cell is not None and lows:
            worst = min(lows, key=lambda r: r["lowest_cell_v_while_on"])
            if worst["lowest_cell_v_while_on"] < first_cell:
                flags.append(Finding("deeper_discharge_than_original_trip",
                                     f"While HV was on again, the lowest cell reached {worst['lowest_cell_v_while_on']:.3f} V (closure {worst['n']} at "
                                     f"{worst['t']} s), below the {first_cell:.3f} V at which the original protective trip fired. Over-discharge "
                                     "risk for the affected cells is an engineering question, not a conclusion of this check.",
                                     [], {"lowest_v": worst["lowest_cell_v_while_on"], "original_trip_v": first_cell}, "review"))
        unobs = [r for r in recl if not r["reset_observed"]]
        if unobs:
            flags.append(Finding("reset_not_observed", f"{len(unobs)} closure(s) followed a fault reset that the record does not show happening "
                                 "(it fell in a gap or before the window); the reason for the reset is not recorded.", [],
                                 {"closures": [r["n"] for r in unobs]}, "review"))
        res.findings += flags

        policy = params.get("policy")
        if not policy or policy.get("schema") != POLICY_SCHEMA:
            res.outcome = INDETERMINATE
            res.reasons.append("the customer's restart-permission logic and parameters are not provided: the closures are flagged for "
                               "engineering review, not judged")
            res.summary = (f"Indeterminate: {len(closes)} HV reclosure(s) after a protective shutdown, {len(flags)} review flag(s); "
                           "no restart policy to judge them against.")
            return res
        verified = policy.get("verified") is True
        res.parameters_used = {"policy_id": policy.get("id"), "policy_verified": verified, "policy_source": policy.get("source")}
        breaches: List[str] = []
        mx = policy.get("max_reclosures_after_protective_shutdown")
        if mx is not None and len(closes) > int(mx):
            breaches.append(f"{len(closes)} reclosures > allowed {mx}")
        vmin = policy.get("min_cell_voltage_to_close_v")
        if vmin is not None:
            for r in recl:
                if r["min_cell_v_at_close"] is None:
                    breaches.append(f"closure {r['n']}: lowest cell at closure not known (no fresh sample)")
                elif r["min_cell_v_at_close"] < float(vmin):
                    breaches.append(f"closure {r['n']} at {r['t']} s with lowest cell {r['min_cell_v_at_close']:.3f} V < {vmin} V")
        unknown = [b for b in breaches if "not known" in b]
        real = [b for b in breaches if "not known" not in b]
        if not verified:
            res.outcome = INDETERMINATE
            res.reasons.append(f"the restart policy is not verified: compared against the DRAFT ({'; '.join(breaches) or 'no difference'}), not judged")
        elif real:
            res.outcome = VIOLATION
            res.findings.append(Finding("restart_policy_breach", "; ".join(real), refs_close, {"breaches": real}, "violation"))
        elif unknown:
            res.outcome = INDETERMINATE
            res.reasons += unknown
        else:
            res.outcome = PASS
        res.summary = {PASS: "Reclosures are within the verified restart policy.",
                       VIOLATION: "Reclosures breach the verified restart policy: " + "; ".join(real),
                       INDETERMINATE: "Indeterminate: " + "; ".join(res.reasons)}[res.outcome]
        return res


CHECK = base.register(RestartEligibility())
