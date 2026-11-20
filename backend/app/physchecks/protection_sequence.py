"""Check B: protection sequence against an explicitly provided expected-behaviour specification.

What the data alone can say (always reported, as FACTS): the fault-level escalations, and after each escalation which
protection-relevant states changed next (HV permission, power state, DC-DC, main contactors, the level the VCU
received), with latencies and the rows that show them. An observed order is not a causal proof and not a judgement.

What needs a specification: whether that sequence was CORRECT. A spec (schema veyra-protection-spec-v1) states a
trigger, the steps that must follow (condition + allowed latency window), and optionally a required order:

  {"schema": "veyra-protection-spec-v1", "id": "...", "version": "...", "verified": true, "source": "<document, revision>",
   "trigger": {"role": "bms_fault_level", "op": ">=", "value": 3},
   "horizon_s": 30,
   "steps": [{"id": "contactors_open", "description": "...", "within_s": [0, 10],
              "condition": {"all": [{"role": "main_positive_closed", "op": "==", "value": 0},
                                    {"role": "main_negative_closed", "op": "==", "value": 0}]}}],
   "order": ["...", "contactors_open"]}

Outcomes: no protection escalation in the record -> NOT_APPLICABLE. No spec -> INDETERMINATE (facts only). A spec with
verified != true -> INDETERMINATE: conformance to the DRAFT is shown, never asserted as correct or incorrect. A
verified spec -> PASS when every step happened in its window and order, VIOLATION when one did not (with rows), and
INDETERMINATE when gaps or stale samples leave a step undecidable.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.physchecks import base
from app.physchecks.base import (INDETERMINATE, NOT_APPLICABLE, PASS, VIOLATION, CheckResult, CheckSpec, Finding,
                                 InputSpec, ParamSpec, fmt)
from app.physchecks.events import OPS, Timeline, holds, roles_in, transitions
from app.physchecks.profile import Profile
from app.physchecks.recording import Recording

VERSION = "protection-sequence-v1"
SPEC_SCHEMA = "veyra-protection-spec-v1"
FOLLOW_ROLES = ["bms_state", "power_state", "dcdc_state", "main_positive_closed", "main_negative_closed",
                "precharge_closed", "vcu_bms_fault_level", "vehicle_fault_level", "battery_code"]

SPEC = CheckSpec(
    id="protection_sequence",
    version=VERSION,
    title="Protection sequence",
    purpose="After a protection escalation, did fault level, HV permission, power state, DC-DC and contactors follow the specified sequence?",
    inputs=[InputSpec("bms_fault_level", "", "BMS fault level, normalised (raw codes mapped in the profile)")]
    + [InputSpec(r, "", f"{r.replace('_', ' ')} (state)", required=False) for r in FOLLOW_ROLES],
    parameters=[ParamSpec("spec", "-", f"Expected-behaviour specification ({SPEC_SCHEMA})", required=False, default=None,
                          source_rule="the customer's protection design document; only verified: true supports PASS/VIOLATION"),
                ParamSpec("horizon_s", "s", "How long after each escalation to follow the states when no spec is given", False, 30.0)],
    applies_when=["the profile binds a fault-level signal", "at least one fault-level escalation is recorded"],
    alignment={"state pairing": "latest sample at or before the instant, no older than each role's max_age_s",
               "changes across gaps": "reported with both bounding samples, time not known"},
    limitations=["Contactor states are feedback signals, not a physical contact measurement",
                 "Without the customer's specification the check reports order and latency only, never correctness"],
)


def _escalations(rec: Recording, profile: Profile) -> List[Any]:
    return [t for t in transitions(rec, profile, "bms_fault_level") if not t.initial and t.old is not None and t.new > t.old]


class ProtectionSequence(base.Check):
    spec = SPEC

    def run(self, rec: Recording, profile: Profile, params: Optional[Dict[str, Any]] = None) -> CheckResult:
        params = dict(params or {})
        res = CheckResult(SPEC.id, VERSION, rec.id, rec.synthetic, NOT_APPLICABLE, "")
        res.input_digest = base.input_digest(rec, profile, SPEC.id, VERSION, params)
        if not profile.has_role("bms_fault_level"):
            res.reasons.append("the profile binds no fault-level signal")
            res.summary = "Not applicable: no fault-level signal."
            return res
        res.inputs_used = {r: profile.describe(r) for r in ["bms_fault_level"] + FOLLOW_ROLES if profile.has_role(r)}
        esc = _escalations(rec, profile)
        if not esc:
            res.reasons.append("no fault-level escalation is recorded")
            res.summary = "Not applicable: no protection escalation in the recording."
            return res
        horizon = float(params.get("horizon_s", 30.0))
        res.windows["escalations"] = {"items": [e.as_dict(profile) for e in esc]}
        # facts: escalation chain and what followed the highest escalation(s)
        top = max(e.new for e in esc)
        followed: List[Dict[str, Any]] = []
        for e in [x for x in esc if x.new == top]:
            after = []
            for role in FOLLOW_ROLES:
                for tr in transitions(rec, profile, role, [e.window]):
                    if not tr.initial and e.t - 1e-9 <= tr.t <= e.t + horizon:
                        d = tr.as_dict(profile)
                        d["latency_s"] = round(tr.t - e.t, 3)
                        after.append(d)
            after.sort(key=lambda d: d["t"])
            followed.append({"trigger": e.as_dict(profile), "followed_by": after})
            names = ", ".join(f"{d['role']} -> {d.get('to_name') or d['to']} (+{d['latency_s']} s{'' if d['time_known'] else ', time not known: gap'})" for d in after)
            res.findings.append(Finding(
                "observed_sequence",
                f"{e.window}: fault level {e.old:g} -> {e.new:g} at t={e.t:.3f} s, then: {names or 'no followed state changed within ' + str(horizon) + ' s'}. "
                "Observed order only; it is not evidence of which event caused which.",
                [e.ref] + [], {"trigger_t": round(e.t, 3), "followed_by": after}))
        res.windows["observed"] = {"top_level": top, "sequences": followed}
        chain = " -> ".join(f"{e.new:g} at {e.t:.3f} s ({e.window})" for e in esc)
        res.findings.insert(0, Finding("escalation_chain", f"Fault level escalations: {chain}.", [e.ref for e in esc[:12]],
                                       {"count": len(esc)}))

        spec = params.get("spec")
        if not spec:
            res.outcome = INDETERMINATE
            res.reasons.append("no expected-behaviour specification was provided: the sequence is reported as facts, not judged")
            res.summary = (f"Indeterminate: {len(esc)} escalation(s) recorded (highest level {top:g}); observed sequence reported, "
                           "no specification to judge it against.")
            return res
        if spec.get("schema") != SPEC_SCHEMA:
            res.outcome = INDETERMINATE
            res.reasons.append(f"the specification is not a {SPEC_SCHEMA} document")
            res.summary = "Indeterminate: unreadable specification."
            return res
        verified = spec.get("verified") is True
        res.parameters_used = {"spec_id": spec.get("id"), "spec_version": spec.get("version"), "spec_verified": verified,
                               "spec_source": spec.get("source")}
        trig = spec["trigger"]
        triggers = [t for t in transitions(rec, profile, trig["role"]) if not t.initial and OPS[trig.get("op", "==")](t.new, trig["value"])
                    and (t.old is None or not OPS[trig.get("op", "==")](t.old, trig["value"]))]
        if not triggers:
            res.outcome = NOT_APPLICABLE
            res.reasons.append(f"the specification's trigger ({trig}) never occurs in the recording")
            res.summary = "Not applicable: the specified trigger does not occur."
            return res
        tl = {r: Timeline(rec, profile, r) for st in spec["steps"] for r in roles_in(st["condition"])}
        verdicts: List[str] = []
        evaluations: List[Dict[str, Any]] = []
        for t in triggers:
            ev = {"trigger_t": round(t.t, 3), "window": t.window, "steps": []}
            step_times: Dict[str, Optional[float]] = {}
            for st in spec["steps"]:
                ev["steps"].append(self._step(rec, profile, tl, t, st, float(spec.get("horizon_s", horizon))))
                # only a step whose time is actually known can take part in an order judgement
                step_times[st["id"]] = ev["steps"][-1].get("t") if ev["steps"][-1]["status"] in ("met", "not_met") else None
            order = spec.get("order") or []
            known = [step_times.get(s) for s in order]
            if order and all(x is not None for x in known):
                ev["order_ok"] = all(a <= b + 1e-9 for a, b in zip(known, known[1:]))
            else:
                ev["order_ok"] = None if order else True
            states = [s["status"] for s in ev["steps"]]
            if "not_met" in states or ev["order_ok"] is False:
                ev["verdict"] = "deviates"
            elif "undecidable" in states or ev["order_ok"] is None:
                ev["verdict"] = "undecidable"
            else:
                ev["verdict"] = "conforms"
            verdicts.append(ev["verdict"])
            evaluations.append(ev)
            for s in ev["steps"]:
                if s["status"] != "met":
                    res.findings.append(Finding("spec_step_" + s["status"], f"{t.window} trigger at {t.t:.3f} s: step '{s['id']}' {s['detail']}",
                                                [], {k: v for k, v in s.items() if k != "evidence"},
                                                "violation" if (verified and s["status"] == "not_met") else "review"))
        res.windows["spec_evaluation"] = {"triggers": evaluations}
        if "undecidable" in verdicts and "deviates" not in verdicts:
            res.outcome = INDETERMINATE
            res.reasons.append("gaps or stale samples leave at least one specified step undecidable")
        elif not verified:
            res.outcome = INDETERMINATE
            res.reasons.append(f"the specification is not verified (source: {spec.get('source') or 'not stated'}): "
                               f"the sequence {'deviates from' if 'deviates' in verdicts else 'conforms to'} the DRAFT, which is not a judgement")
        else:
            res.outcome = VIOLATION if "deviates" in verdicts else PASS
        res.summary = {PASS: f"Sequence conforms to verified spec {spec.get('id')} for {len(triggers)} trigger(s).",
                       VIOLATION: f"Sequence deviates from verified spec {spec.get('id')} (see findings).",
                       INDETERMINATE: "Indeterminate: " + "; ".join(res.reasons)}[res.outcome]
        return res

    def _step(self, rec, profile, tl, trig, st, horizon):
        lo, hi = st.get("within_s", [0, horizon])
        roles = roles_in(st["condition"])
        missing = [r for r in roles if not tl[r].available()]
        if missing:
            return {"id": st["id"], "status": "undecidable", "detail": f"no samples of {missing}", "t": None}
        cands = [trig.t]
        for r in roles:
            cands += [x.t for x in transitions(rec, profile, r, [trig.window]) if not x.initial and trig.t <= x.t <= trig.t + max(hi, horizon)]
        unknown_time = [x for r in roles for x in transitions(rec, profile, r, [trig.window])
                        if not x.initial and not x.time_known and trig.t <= x.t <= trig.t + hi]
        for c in sorted(set(cands)):
            vals, refs = {}, []
            for r in roles:
                got = tl[r].at(trig.window, c)
                vals[r] = None if got is None else got[0]
                if got:
                    refs.append(got[2].as_dict())
            h = holds(st["condition"], vals)
            if h:
                lat = c - trig.t
                if unknown_time:
                    return {"id": st["id"], "status": "undecidable", "t": round(c, 3), "latency_s": round(lat, 3),
                            "detail": "a state involved changed inside a recording gap; its time is not known", "evidence": refs}
                ok = lo - 1e-9 <= lat <= hi + 1e-9
                return {"id": st["id"], "status": "met" if ok else "not_met", "t": round(c, 3), "latency_s": round(lat, 3),
                        "allowed_s": [lo, hi], "evidence": refs,
                        "detail": f"held after {lat:.3f} s" + ("" if ok else f", outside the allowed {lo}–{hi} s")}
        covered = all(tl[r].covered(trig.window, trig.t, trig.t + hi) for r in roles)
        if not covered:
            return {"id": st["id"], "status": "undecidable", "t": None, "detail": f"the record does not cover {hi} s after the trigger without gaps"}
        return {"id": st["id"], "status": "not_met", "t": None, "allowed_s": [lo, hi], "detail": f"did not hold within {hi} s of the trigger"}


CHECK = base.register(ProtectionSequence())
