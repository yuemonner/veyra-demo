"""The AI investigator over a physical-check case: it reasons with the deterministic checks, and is held to them.

The model gets tools that return deterministic results (never raw files): the check results, the derived facts, the
deterministic hypothesis assessment, the next-check plan, and bounded raw samples of a role (with row references).
It ends with submit_report: its hypotheses (id, status, statement) and summary. Every hypothesis statement then passes
through hypotheses.constrain_claims: unverified root causes become proposals, unsupported exclusions are rejected, a
protective response or a weak cell named as the sole cause is rejected, numeric probabilities are flagged.

Privacy: a confidential (non-synthetic) case is sent to an external model ONLY when the caller passes
allow_external_model=True, which the API sets only when the operator has set VEYRA_PRIVATE_ALLOW_AGENT=true after the
data owner agreed. Without a model, the result says so (mode "unavailable"); nothing is fabricated.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.physchecks import base, expert_layer, facts as facts_mod, hypotheses, planner
from app.physchecks.profile import Profile
from app.physchecks.recording import Recording

BRIDGE_VERSION = "physchecks-agent-bridge-v2"
MAX_STEPS = 12

SYSTEM = """You are Veyra's engineering investigator for an incident on a battery-powered machine.
You reason ONLY from the tools. They return deterministic check results, facts with row references, a deterministic
hypothesis assessment and a next-check plan. Rules:
- Keep symptom, protective response and upstream cause apart. A protection acting is not the root cause.
- Event order is not causal proof.
- Do not exclude a hypothesis without a verified outcome or direct physical evidence; do not claim a root cause is
  confirmed without an engineer-verified outcome. Use statuses: open, insufficient_alone, weakened, proposed.
- No numeric probabilities.
- The hypotheses tool states family_scope: whether the battery-electric-vehicle family (HV pack, BMS, contactors)
  applies to THIS machine. When it does not apply, never propose HV, BMS, contactor, DC-DC or restart-policy work or
  causes; report what the checks found, and what is missing to judge it.
- Work in this order, like a system engineer: (1) system_audit symptom_vs_record: does the record show the reported
  symptom? (2) control_sequence and inferred_thresholds: what the controllers did around the shutdown (inferred
  thresholds are inferences); (3) signal_independence: which signals are relays, constant or unable to show what their
  name says (never count a relay as a second measurement; treat HV as live when contactor state cannot be verified);
  (4) the estimator and the charge (facts, charge_accounting); (5) cell physics: load_sag, load_transitions and
  charge_alignment (a weak cell that sets the TIMING is not the cause of the SIZE); (6) plausibility: WHEN the error
  arose, the record evidence for each explanation, and the labelled prior order; (7) restart_authorisation; (8) safety
  holds; (9) the decisive next check.
- If the recorded data cannot separate the open hypotheses, say so. Then say which to CHECK FIRST using only the
  plausibility tool's leading entry, with its basis_kind: "evidence" (the record supports one cause) or "prior" (this
  customer's verified outcomes, or reference knowledge on a confirmed chemistry). A prior is never evidence and never a
  reason to drop another explanation. State the leading one as "leading (not confirmed)" with its basis and its
  decisive check. Never order by how plausible an explanation sounds.
- Earlier engineering knowledge (earlier_knowledge) and other recordings (scenario_results) are context. A result
  verified in ANOTHER case is not evidence about THIS recording: it may only tell you which check to do first. Say
  which earlier result you used and whether it applies.
Finish by calling submit_report once."""

TOOL_SPECS = [
    {"name": "checks_overview", "description": "Outcome and one-line summary of every physical check (charge accounting, protection sequence, restart eligibility).",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "check_detail", "description": "Full result of one check: findings with row references, calculations with formulas, reasons, assumptions.",
     "input_schema": {"type": "object", "properties": {"check_id": {"type": "string"}}, "required": ["check_id"]}},
    {"name": "facts", "description": "Derived case facts (each with its rows and stated assumptions).", "input_schema": {"type": "object", "properties": {}}},
    {"name": "hypotheses", "description": "The deterministic hypothesis assessment: roles, statuses, supporting/contradicting/missing evidence.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "next_checks", "description": "Next decisive checks: which open hypotheses each separates, predictions, safety gate.",
     "input_schema": {"type": "object", "properties": {"requester_qualification": {"type": "string", "enum": list(planner.QUAL)}}}},
    {"name": "samples", "description": "Up to 60 evenly spaced valid samples of one signal role in one window between t0 and t1, each with its source row.",
     "input_schema": {"type": "object", "properties": {"window": {"type": "string"}, "role": {"type": "string"}, "t0": {"type": "number"},
                                                       "t1": {"type": "number"}}, "required": ["window", "role", "t0", "t1"]}},
    {"name": "earlier_knowledge", "description": "Engineer-verified results from EARLIER cases that share a finding with this recording: whether each applies here and why, who verified it, the check that settled it. Context only; never evidence for this recording.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "scenario_results", "description": "Approved scenarios run on this recording (outcome, applicability), and the same scenarios' latest results on the other stored recordings (cross-run).",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "system_audit", "description": "System-level audit: symptom vs record, control sequence around the shutdown (BMS / VCU / DC-DC / contactors / current, with sample ages), inferred alarm thresholds, signal independence (relays, constant or saturated fields, post-contactor voltage, current when open), load sag, lowest vs highest cell aligned by charge (timing vs size), restart and HV re-authorisation.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "plausibility", "description": "When the SOC error arose (before the record or inside it), what each explanation would physically require, and record evidence per hypothesis (what the record supports or weakens), what each would require, and a labelled order to CHECK FIRST (leading / leading_if_flat_ocv / possible) whose basis is record evidence, this customer's verified outcomes, or reference knowledge on a confirmed chemistry. Never a probability; leading is not confirmed and a prior is not evidence.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "submit_report", "description": "Final report. hypotheses: [{id, status, statement}], summary, next_check (template id).",
     "input_schema": {"type": "object", "properties": {"hypotheses": {"type": "array", "items": {"type": "object"}}, "summary": {"type": "string"},
                                                       "next_check": {"type": "string"}}, "required": ["hypotheses", "summary"]}},
]


class PhysicalCheckTools:
    def __init__(self, rec: Recording, profile: Profile, params: Optional[Dict[str, Dict[str, Any]]] = None,
                 context: Optional[Dict[str, Any]] = None, verified: Optional[List[Dict[str, Any]]] = None,
                 rules: Optional[Dict[str, Any]] = None, reported_symptom: Optional[str] = None):
        self.rec, self.profile = rec, profile
        self.context = context                      # {"knowledge": ..., "scenario_results": ...} from investigation.context_for
        self.checks = base.run_all(rec, profile, params)
        self.facts = facts_mod.derive(rec, profile, self.checks)
        self.assessment = hypotheses.assess(self.checks, self.facts, verified or [], machine=hypotheses.machine_of(profile))
        self.expert = expert_layer.enrich(rec, profile, self.checks, self.facts, self.assessment, rules=rules, reported_symptom=reported_symptom)
        self.prior = None
        if context and context.get("knowledge"):
            from app.physchecks import knowledge
            self.prior = knowledge.prior_for_planner(context["knowledge"])

    def specs(self) -> List[Dict[str, Any]]:
        ctx = {"earlier_knowledge", "scenario_results"}
        return [t for t in TOOL_SPECS if self.context is not None or t["name"] not in ctx]

    def earlier_knowledge(self) -> Dict[str, Any]:
        k = (self.context or {}).get("knowledge") or {"items": [], "unverified": []}
        return {"rule": k.get("rule"), "items": [{x: i.get(x) for x in ("case_id", "hypothesis_id", "statement", "result", "verified_by", "basis",
                                                                        "settled_by", "applicability", "reasons", "source_synthetic")} for i in k["items"]],
                "unverified_count": len(k.get("unverified") or [])}

    def scenario_results(self) -> Dict[str, Any]:
        return (self.context or {}).get("scenario_results") or {"here": [], "other_recordings": []}

    def checks_overview(self) -> Dict[str, Any]:
        out = {}
        for cid, r in self.checks.items():
            o = {"outcome": r["outcome"], "summary": r["summary"], "version": r["check_version"]}
            if r.get("resolve_by"):
                o["resolve_by"] = [x["measurement"] + (f" ({x['template']})" if x.get("template") else "") for x in r["resolve_by"]]
            if r.get("independence"):
                o["not_independent"] = [x["means"] for x in r["independence"]]
            if (r.get("provenance") or {}).get("kind") == "expert_proposal":
                o["provenance"] = "expert proposal, not yet field-validated"
            out[cid] = o
        return out

    def check_detail(self, check_id: str) -> Dict[str, Any]:
        if check_id not in self.checks:
            raise ValueError(f"unknown check {check_id!r}; known: {sorted(self.checks)}")
        r = dict(self.checks[check_id])
        r["findings"] = r["findings"][:25]
        return r

    def facts_(self) -> Dict[str, Any]:
        return self.facts

    def hypotheses_(self) -> Dict[str, Any]:
        return self.assessment

    def system_audit(self) -> Dict[str, Any]:
        return self.expert["audit"]

    def plausibility(self) -> Dict[str, Any]:
        return self.expert["plausibility"]

    def next_checks(self, requester_qualification: str = "none") -> Dict[str, Any]:
        return planner.plan(self.assessment, self.checks, requester_qualification=requester_qualification, prior=self.prior)

    def samples(self, window: str, role: str, t0: float, t1: float) -> Dict[str, Any]:
        if not self.profile.has_role(role):
            raise ValueError(f"unknown role {role!r}")
        s = self.profile.series(self.rec, window, role)
        pts = [x for x in s.samples if t0 <= x.t <= t1]
        step = max(1, len(pts) // 60)
        return {"role": role, "window": window, "unit": s.unit, "count": len(pts),
                "samples": [{"t": round(x.t, 3), "v": x.value, "row": self.rec.ref(window, s.field, x.index).row} for x in pts[::step][:60]]}

    def call(self, name: str, args: Dict[str, Any]) -> Any:
        fn = {"facts": self.facts_, "hypotheses": self.hypotheses_}.get(name) or getattr(self, name, None)
        if fn is None or name.startswith("_") or name == "call":
            raise ValueError(f"unknown tool {name!r}")
        return fn(**args)


def _digest(x: Any) -> str:
    return hashlib.sha256(json.dumps(x, sort_keys=True, default=str).encode()).hexdigest()[:16]


def investigate(rec: Recording, profile: Profile, params: Optional[Dict[str, Dict[str, Any]]] = None, *, model=None,
                allow_external_model: bool = False, max_steps: int = MAX_STEPS, context: Optional[Dict[str, Any]] = None,
                verified: Optional[List[Dict[str, Any]]] = None, rules: Optional[Dict[str, Any]] = None,
                reported_symptom: Optional[str] = None) -> Dict[str, Any]:
    tools = PhysicalCheckTools(rec, profile, params, context=context, verified=verified, rules=rules, reported_symptom=reported_symptom)
    started = datetime.now(timezone.utc).isoformat()
    base_out = {"version": BRIDGE_VERSION, "recording": rec.id, "synthetic": rec.synthetic, "started_at": started,
                "deterministic_assessment": tools.assessment, "checks": tools.checks_overview()}
    if not rec.synthetic and not allow_external_model and getattr(model, "provider", "") != "scripted-test":
        return {**base_out, "mode": "blocked",
                "reason": "confidential case: the AI investigator would send case data to an external model. It runs only when the data owner "
                          "has agreed and the operator sets VEYRA_PRIVATE_ALLOW_AGENT=true. The deterministic checks above are unaffected."}
    if model is None:
        from app.agent.llm import from_environment
        model = from_environment()
    if not model.available():
        return {**base_out, "mode": "unavailable", "reason": "no model is configured (no API key / CLI). No agent result exists for this run."}
    messages: List[Dict[str, Any]] = [{"role": "user", "content": f"Investigate recording {rec.id} ({'SYNTHETIC' if rec.synthetic else 'confidential'}). "
                                                                  f"Machine class declared by the signal profile: {tools.assessment['family_scope'].get('machine_class') or 'not stated'}. "
                                                                  "Start with system_audit, then checks_overview, hypotheses and plausibility."
                                                                  + (" Then read earlier_knowledge and scenario_results." if context is not None else "")}]
    trace: List[Dict[str, Any]] = []
    report = None
    for step in range(max_steps):
        try:
            reply = model.create(system=SYSTEM, messages=messages, tools=tools.specs())
        except Exception as error:  # a model failure ends the run and is reported, never replaced by an invented result
            return {**base_out, "mode": "agent_failed", "model": getattr(model, "model", None), "trace": trace,
                    "reason": f"the model call failed at step {step}: {type(error).__name__}: {str(error)[:300]}"}
        content = reply.get("content") or []
        messages.append({"role": "assistant", "content": content})
        calls = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
        if not calls:
            messages.append({"role": "user", "content": "Use the tools, then call submit_report."})
            continue
        results = []
        for c in calls:
            name, args = c.get("name"), c.get("input") or {}
            if name == "submit_report":
                report = args
                results.append({"type": "tool_result", "tool_use_id": c.get("id"), "content": "Accepted."})
                trace.append({"step": step, "tool": name, "input": {"hypotheses": len(args.get("hypotheses") or [])}})
                continue
            try:
                value = tools.call(name, args)
                text, err = json.dumps(value, default=str)[:20000], False
            except Exception as e:  # tool errors go back to the model
                text, err, value = f"error: {e}", True, None
            trace.append({"step": step, "tool": name, "input": args, "error": err, "output_digest": _digest(value)})
            results.append({"type": "tool_result", "tool_use_id": c.get("id"), "content": text, **({"is_error": True} if err else {})})
        messages.append({"role": "user", "content": results})
        if report is not None:
            break
    if report is None:
        return {**base_out, "mode": "agent_incomplete", "model": getattr(model, "model", None), "trace": trace,
                "reason": f"the model did not submit a report within {max_steps} steps"}
    claims = [{"text": h.get("statement") or "", "hypothesis": h.get("id"), "asserted_status": h.get("status"), "source": "agent"}
              for h in report.get("hypotheses") or []]
    claims.append({"text": report.get("summary") or "", "source": "agent_summary"})
    constrained = hypotheses.constrain_claims(claims, tools.assessment, tools.checks)
    from app.agent import action_gate                  # the same deterministic action gate as the main investigator
    nc = (report.get("next_check") or "").strip()
    known = {t["id"]: t for t in planner.TEMPLATES}
    gate = None
    if nc:
        t = known.get(nc)
        proposed = {"action": t["title"] if t else nc, "template_id": nc if t else "none", "needs_approval": bool(t and t["safety"]["qualification"] != "none")}
        v = action_gate.evaluate_action(proposed, tools.next_checks(), hv_system=bool(tools.assessment["family_scope"].get("applies")))
        gate = {"version": action_gate.GATE_VERSION, "proposed": nc, "verdict": v["verdict"], "rules_fired": v["rules"]}
        if v["verdict"] == "blocked":
            rep = action_gate.replacement(tools.next_checks(), hv_system=True)
            gate["replacement"] = rep and rep.get("template_id")
            report = {**report, "next_check": gate["replacement"], "blocked_next_check": nc}
    return {**base_out, "mode": "agent", "action_gate": gate, "model": getattr(model, "model", None), "provider": getattr(model, "provider", None),
            "finished_at": datetime.now(timezone.utc).isoformat(), "trace": trace, "agent_report": report, "constrained_claims": constrained,
            "accepted_as_is": all(c["verdict"] == "accepted" for c in constrained),
            "note": "Agent statements are proposals. Statuses that disagree with the deterministic assessment are shown with the reason; "
                    "nothing is promoted to a trusted rule without engineer verification."}


def agent_allowed_by_operator() -> bool:
    return os.environ.get("VEYRA_PRIVATE_ALLOW_AGENT") == "true"
