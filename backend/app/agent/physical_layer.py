"""The physical engineering layer inside Veyra's main AI investigator.

One investigator, two kinds of evidence:
  * the evidence sheet / original log (recording_tools, LogTools): what the data shows, with line-level quotes, held by
    the quote-and-number verifier;
  * the Physical Check Engine (app.physchecks): deterministic, versioned physical checks, case facts, the hypothesis
    assessment for the machine's declared class and the next decisive check, read from the SAME uploaded recording
    through a signal profile.

When a case came from an uploaded recording that a signal profile can read, the investigator gets the physical tools
below, and its report is also held to the physical assessment (hypotheses.constrain_claims): a statement that names the
protective response as the cause, a weak cell as the sole cause, contradicts Check A, excludes a hypothesis without a
verified outcome, or uses HV-vehicle concepts on a machine the vehicle family does not apply to, is returned to the
model to fix, and kept with its verdict in the result. No profile -> no physical tools; nothing changes.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

LAYER_VERSION = "physical-layer-v2"

PREFIX = "physical_"
SPECS: List[Dict[str, Any]] = [
    {"name": "physical_checks", "description": "Deterministic physical checks on the original recording (charge accounting, protection sequence, restart eligibility): outcome PASS / VIOLATION / INDETERMINATE / NOT_APPLICABLE and a one-line summary each, plus the machine class the signal profile declares.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "physical_check_detail", "description": "One physical check in full: findings with their source rows (file, row), calculations with formulas, reasons, assumptions.",
     "input_schema": {"type": "object", "properties": {"check_id": {"type": "string"}}, "required": ["check_id"]}},
    {"name": "physical_facts", "description": "Case facts derived from the physical checks, each with its rows and stated assumptions.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "physical_hypotheses", "description": "The deterministic hypothesis assessment for this machine: family_scope (whether the battery-electric-vehicle family applies), hypotheses with roles (symptom, protective response, upstream cause, contributing factor), statuses and supporting / contradicting / missing evidence. Your report is held to it.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "physical_next_checks", "description": "Next decisive checks: which open hypotheses each separates, what each predicts, and the safety gate (work that needs qualified personnel is a hand-off).",
     "input_schema": {"type": "object", "properties": {"requester_qualification": {"type": "string", "enum": ["none", "trained_technician", "hv_qualified"]}}}},
    {"name": "physical_system_audit", "description": "System-level audit of the original recording: symptom vs record (drive requests, speed, emergency stop), the control sequence around the protective shutdown (BMS, VCU, DC/DC, contactors, current, with sample ages), inferred alarm thresholds, signal independence (relays, constant or saturated fields, post-contactor voltage, current when open), load sag, lowest vs highest cell aligned by charge (timing vs size), restart and HV re-authorisation.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "physical_plausibility", "description": "When the SOC error arose (before the record or inside it), what each explanation would physically require, and record evidence per physical hypothesis (what the record supports or weakens), what each would require, and a labelled order to CHECK FIRST (leading / leading_if_flat_ocv / possible) whose basis is record evidence, this customer's verified outcomes, or reference knowledge on a confirmed chemistry. Leading is a ranked proposal, never a confirmed cause; a prior is never evidence.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "physical_samples", "description": "Up to 60 evenly spaced samples of one signal role (as named by the signal profile) in one window, each with its source row.",
     "input_schema": {"type": "object", "properties": {"window": {"type": "string"}, "role": {"type": "string"}, "t0": {"type": "number"}, "t1": {"type": "number"}},
                      "required": ["window", "role", "t0", "t1"]}},
]
MEMORY_SPECS: List[Dict[str, Any]] = [
    {"name": "physical_earlier_knowledge", "description": "Engineering Memory: engineer-verified results from EARLIER cases that share a finding with this recording, whether each applies here (same machine class, same check versions) and why, who verified it, and the check that settled it. Context only; never evidence about this recording: a cause verified elsewhere stays open here until verified here.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "physical_scenario_results", "description": "Engineering Memory: approved reusable scenarios run on this recording (outcome, applicability) and their latest results on the other stored recordings.",
     "input_schema": {"type": "object", "properties": {}}},
]
NAMES = {s["name"] for s in SPECS} | {s["name"] for s in MEMORY_SPECS}

PROMPT = """
PHYSICAL CHECKS. This case's recording is also read by Veyra's Physical Check Engine through a signal profile
(physical_* tools). They are deterministic and computed from the original rows; the time axis is the profile's own
(seconds from the recording's first message, per window), which can differ from the evidence sheet's.
- Start with physical_checks and physical_hypotheses. family_scope says whether the battery-electric-vehicle family
  (HV pack, BMS, contactors) applies to this machine; when it does not, do not propose HV, BMS, contactor, DC-DC or
  restart-policy causes or work.
- A protective response (contactors opening, a protection level rising) is how a symptom happened, not why.
- Give every hypothesis a physical_ref: the physical_hypotheses id it is (A, B, ... ) or none. It is checked BY ID: a
  protective response or symptom given role main_cause, a factor the assessment marks insufficient_alone given as the
  leading main cause, or ruled_out without a verified outcome, is returned to you to fix. Your headline and
  explanation are also read for HV-vehicle concepts on other machines and for a protection named as the cause.
- When physical_earlier_knowledge is offered, read it: an engineer-verified result from another case can tell you which
  check settled the same finding before (do that check first), never that the cause is confirmed here.
- Safety is checked deterministically after you: physical_next_checks lists safety_holds and the checks' templates with
  hv_state. Give next_action a template_id (a physical_next_checks id, or none for offline / document / data work). An
  action that needs HV closed while a hold is active, an unplanned template, a hazardous action with no template, or a
  step the template does not cover is BLOCKED and replaced by the plan's safe alternative.
- Investigate the machine as a system before arguing a cause: physical_system_audit first (does the record show the
  reported symptom; what BMS, VCU, DC/DC and contactors did around the shutdown; which signals are relays, constant or
  cannot show what their name says; how far load sag goes; whether the weakest cell sets the TIMING or the SIZE; how HV
  was re-authorised after restarts). Inferred thresholds are inferences; a relay is never a second measurement.
- Do not pick the explanation that sounds most plausible. Say which explanations the measurements support, which remain
  indistinguishable, and what evidence would separate them. Then order the indistinguishable ones ONLY with
  physical_plausibility: when the error arose (before the record means the history since the last calibration anchor
  decides), the record evidence for each, and its labelled order to check first. When it names a leading explanation,
  give it status leading with its basis_kind (evidence or prior) and the decisive check, stated as not confirmed. A
  prior (customer's verified outcomes, reference knowledge) is never evidence and never a reason to rule another
  explanation out; reference knowledge is never presented as a fact of this case. A check that abstains (INDETERMINATE) names what would resolve
  it (resolve_by); use that. Agreement between a signal and its own inputs (e.g. SOC counted from the same current) is
  not evidence (the check's independence field says so).
- Declare next_action.steps, each {kind, text}. Reading or analysing data, requesting documents, asking people, drafting
  and observing without contact (read_data, request_document, ask_person, write_document, observe) run without approval;
  prefer them when they can separate the causes. Physical steps (measure, handle, energise, charge_discharge, operate,
  remote_command, procedure) are delivered only under the named procedure, and an engineer confirms them before anyone
  acts. The gate reads each step's text as well; a hazardous step declared as no-touch is treated as hazardous.
- List every safety hold and every check finding of severity violation or review in safety_findings, by ref (hold id or
  check_id:finding_kind). What you leave out is inserted by Veyra and marked as such.
"""

STEP_LABEL = {"physical_checks": "Ran the physical checks", "physical_check_detail": "Opened physical check {check_id}",
              "physical_facts": "Read the physical facts", "physical_hypotheses": "Read the hypothesis assessment",
              "physical_next_checks": "Planned the next decisive check", "physical_system_audit": "Audited the machine as a system",
              "physical_plausibility": "Ordered the explanations by what each would require", "physical_samples": "Sampled {role} in {window}, {t0}–{t1} s",
              "physical_earlier_knowledge": "Read verified results from earlier cases", "physical_scenario_results": "Read the reusable scenarios' results"}


def summary(name: str, value: Any) -> str:
    if name == "physical_checks":
        return "; ".join(f"{k}: {v['outcome']}" for k, v in (value.get("checks") or {}).items())
    if name == "physical_hypotheses":
        sc = value.get("family_scope") or {}
        return (f"{len(value.get('hypotheses') or [])} hypotheses" if sc.get("applies") else "vehicle family does not apply: " + "; ".join(sc.get("reasons") or []))[:200]
    if name == "physical_next_checks":
        rec = value.get("recommended") or []
        return f"first: {rec[0]['id']}" if rec else (value.get("note") or "nothing to recommend")[:200]
    if name == "physical_check_detail":
        return f"{value.get('check_id')}: {value.get('outcome')}, {len(value.get('findings') or [])} finding(s)"
    if name == "physical_samples":
        return f"{value.get('count')} sample(s) of {value.get('role')}"
    return ""


def source_recording(case_id: str) -> Optional[str]:
    """The uploaded recording a case was built from (recordings_api writes case_ids into recording.json)."""
    from app.physchecks import ingested
    for meta in ingested.list_recordings():
        if case_id in (meta.get("case_ids") or []):
            return meta.get("id")
    return None


class PhysicalLayer:
    def __init__(self, recording_id: str, rec, profile, tools):
        self.recording_id, self.rec, self.profile, self.tools = recording_id, rec, profile, tools

    def specs(self) -> List[Dict[str, Any]]:
        return SPECS + (MEMORY_SPECS if self.tools.context is not None else [])

    def describe(self) -> Dict[str, Any]:
        a = self.tools.assessment
        return {"version": LAYER_VERSION, "recording_id": self.recording_id, "profile": self.profile.id,
                "machine_class": a["family_scope"].get("machine_class"), "family_applies": a["family_scope"]["applies"],
                "checks": self.tools.checks_overview(), "summary": a.get("summary") or a.get("note")}

    def call(self, name: str, args: Dict[str, Any]) -> Any:
        if name == "physical_checks":
            return {"machine": self.tools.assessment.get("machine"), "checks": self.tools.checks_overview()}
        if name == "physical_check_detail":
            return self.tools.check_detail(**args)
        if name == "physical_facts":
            return self.tools.facts_()
        if name == "physical_hypotheses":
            return self.tools.hypotheses_()
        if name == "physical_next_checks":
            return self.tools.next_checks(**args)
        if name == "physical_samples":
            return self.tools.samples(**args)
        if name == "physical_system_audit":
            return self.tools.system_audit()
        if name == "physical_plausibility":
            return self.tools.plausibility()
        if name == "physical_earlier_knowledge":
            return self.tools.earlier_knowledge()
        if name == "physical_scenario_results":
            return self.tools.scenario_results()
        raise ValueError(f"Unknown tool {name}")

    def constrain(self, report: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Hold the report to the physical assessment.
        Hypotheses are checked BY REFERENCE (physical_ref -> the assessment's hypothesis: its role and status), not by
        words. Only the free-text headline and explanation still go through the text guard (constrain_claims)."""
        from app.physchecks import hypotheses
        A = self.tools.assessment
        out = [dict(c, kind="text") for c in hypotheses.constrain_claims(
            [c for c in ({"text": report.get("headline") or "", "source": "headline"},
                         {"text": report.get("explanation") or "", "source": "explanation"}) if c["text"].strip()], A, self.tools.checks)]
        return out + [structured(h, A) for h in report.get("hypotheses") or []]
CAUSE_ROLES = ("main_cause",)


def structured(h: Dict[str, Any], A: Dict[str, Any]) -> Dict[str, Any]:
    """One report hypothesis against the physical assessment, by id. Verdict accepted / rejected with reasons."""
    ref = (h.get("physical_ref") or "").strip()
    src = f"hypothesis {h.get('id')}"
    text = f"{h.get('area', '')}: {h.get('claim', '')}"
    base = {"source": src, "text": text, "kind": "structured", "physical_ref": ref or None}
    scope = A.get("family_scope") or {}
    phys = {x["id"]: x for x in A.get("hypotheses") or []}
    if not ref:
        return {**base, "verdict": "rejected", "reasons": ["physical_ref is missing: name the physical_hypotheses id this hypothesis is, or none"]}
    if ref.lower() == "none":
        if scope.get("applies") is False:
            from app.physchecks import hypotheses
            m = hypotheses.HV_VEHICLE_TERMS.search(text)
            if m:
                return {**base, "verdict": "rejected", "reasons": [f"uses '{m.group(0)}', a battery-electric-vehicle concept, on a machine that family does not apply to: "
                                                                     + "; ".join(scope.get("reasons") or [])]}
        return {**base, "verdict": "accepted", "reasons": ["not one of the physical hypotheses; held to the evidence sheet only"]}
    if scope.get("applies") is False:
        return {**base, "verdict": "rejected", "reasons": [f"physical_ref {ref!r}: no physical hypothesis family applies to this machine (" + "; ".join(scope.get("reasons") or []) + ")"]}
    x = phys.get(ref)
    if x is None:                           # a mechanism id (e.g. C2) is held to its parent hypothesis
        x = next((p for p in phys.values() if any(m.get("id") == ref for m in p.get("mechanisms") or [])), None)
    if x is None:
        return {**base, "verdict": "rejected", "reasons": [f"physical_ref {ref!r} is not in the assessment (ids: {', '.join(sorted(phys))})"]}
    reasons: List[str] = []
    status, role = (h.get("status") or ""), (h.get("role") or "")
    if role in CAUSE_ROLES and x["role"] in ("protective_response", "observed_symptom"):
        reasons.append(f"{x['label']} is the {x['role'].replace('_', ' ')} in the assessment: it is how the symptom happened, not its cause")
    if role in CAUSE_ROLES and status == "leading" and x["status"] == "insufficient_alone":
        reasons.append(f"{x['label']} cannot be the main cause on its own: {x['status_reason']}")
    if status == "ruled_out" and x["status"] != "excluded":
        reasons.append(f"{x['label']} is '{x['status']}' in the assessment; only a verified outcome or direct physical evidence excludes it")
    weak = [e for e in (x.get("plausibility") or {}).get("record_evidence", []) if e.get("direction") == "weakens"]
    if status == "leading" and role in CAUSE_ROLES and weak:
        reasons.append(f"{x['label']}: the record weakens it ({weak[0]['claim'][:160]}); it cannot be the leading main cause")
    if status == "leading" and x["status"] == "excluded":
        reasons.append(f"{x['label']} is excluded in the assessment by a verified outcome")
    return {**base, "verdict": "rejected" if reasons else "accepted", "reasons": reasons or [f"consistent with {x['label']} ({x['status']})"],
            "assessment_status": x["status"], "assessment_role": x["role"]}


def problems(constrained: List[Dict[str, Any]]) -> List[str]:
    return [f"{c['source']}: \"{c['text'][:160]}\" is held to the physical checks and rejected: " + "; ".join(c["reasons"])
            for c in constrained if c["verdict"] == "rejected"]


def for_case(store, case_id: str, context: Optional[Dict[str, Any]] = None) -> Optional[PhysicalLayer]:
    """The physical layer for a case, or None when no uploaded recording / no compatible signal profile. `context`
    ({"knowledge": ..., "scenario_results": ...} from the engineering ledger) adds the Engineering Memory tools."""
    try:
        rid = source_recording(case_id)
        if rid is None:
            return None
        from app.physchecks import agent_bridge, ingested
        if ingested.chosen(ingested.recording_meta(rid)) is None:
            return None
        rec, prof, _meta = ingested.load(rid)
        return PhysicalLayer(rid, rec, prof, agent_bridge.PhysicalCheckTools(rec, prof, context=context))
    except (KeyError, ValueError, OSError, ImportError):     # no uploads folder / no API on this instance / unreadable file
        return None


def link_case(recording_folder: Path, case_id: str) -> None:
    """Record that a case was built from this uploaded recording (in its recording.json)."""
    p = Path(recording_folder) / "recording.json"
    meta = json.loads(p.read_text(encoding="utf-8"))
    ids = meta.setdefault("case_ids", [])
    if case_id not in ids:
        ids.append(case_id)
    p.write_text(json.dumps(meta))
