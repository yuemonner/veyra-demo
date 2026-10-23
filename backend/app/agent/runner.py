"""Investigation agent: an LLM drives the read-only tools and returns a cited report.

Boundaries enforced here, not left to the prompt:
- Data egress: a non-synthetic case is only sent to a model provider after a person records a
  `model_authorization` for that provider in the case ledger.
- Read-only: the agent's tools cannot write; its only output is one machine-generated `agent_report`.
- Citations: every observation, hypothesis and proposed check must cite tool calls made in this run;
  a report that does not is returned to the model to fix, and stored as uncited if it still fails.
- No confirmed root cause: the report may rank hypotheses but never confirm one.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Dict, List, Optional

from app.agent import tools as toolset
from app.agent.llm import ModelError

AGENT_VERSION = "veyra-agent-v3.2"  # v3.2: observation vs attribution, "leading" needs comparative evidence, reads Veyra's plan. v3.1: short reports (length limits checked). v3: queries any signal and case, runs methods and sandboxed Python; answers follow-up questions with a premise check
# v2: numbers in claims must appear in the cited tool output; no interpretation inside observations
MAX_TURNS = 20
MAX_REPORT_ATTEMPTS = 3
_CONFIRMED = re.compile(r"\b(root cause (is|was) (confirmed|identified)|confirmed root cause|definitely caused)\b", re.IGNORECASE)

SYSTEM_PROMPT = """You are Veyra's field investigation agent. A deployed machine behaved unexpectedly; engineers need to know what changed and what to check next before anyone travels.

Work only from tool results. Call case_overview first, then the tools that matter for this case. case_overview names the machine type and what each domain key means on this machine (e.g. control_suite = the flight controller on an aircraft); write for that machine, in its terms, and never borrow terms from another machine type. Compare the symptom window with the reference. Look for brief events near any operator intervention. Treat clocks carefully: different clock domains may be offset.

Rules:
- Every observation, hypothesis and proposed check must cite the ids of the tool calls whose results support it (the tool_use ids you received).
- Every number you write must appear in the output of a tool call you cite for that item. Quote source rows (import#row) where a tool gives them.
- Observations state only what tool results show. Put any interpretation (may, likely, suggests, consistent with, an artifact of) in a hypothesis.
- Check whether the machine followed its commands (command_tracking) and which outputs froze (actuator_outputs) before ranking hypotheses.
- A finding the engine suppressed as normal for this machine is not evidence of a fault.
- Never state a confirmed root cause. Rank hypotheses as leading, open or weakened, and say what would confirm or rule each out.
- Keep what failed apart from why. "LF_KFE stopped following its commands" is an observation; "its encoder is faulty" is an attribution. Name a hypothesis at the level the data can separate (e.g. "a fault at the LF_KFE joint (drive, encoder, wiring or mechanics)"), never one component out of several the data doesn't distinguish.
- "leading" needs comparative evidence: cite, in each other open hypothesis's `against`, a tool result that weighs against it. Without that, mark them all "open".
- Before proposing checks, call next_action_plan. If your first check differs from its first, say why in that check's `why`.
- Name what is unmeasured or missing and why it matters.
- Propose only read-only checks or checks a person must approve; you cannot act on the machine.
- Write for a busy field engineer: short, plain sentences. No filler, no repeating the question.
- Keep it short: summary 2 sentences; at most 5 observations and 3 hypotheses, one sentence each (under 25 words); at most 4 proposed checks, the action in under 15 words; at most 3 questions; uncertainty 1-2 sentences.
- Times as HH:MM:SS. Leave source rows and rule ids out of the text: the citations carry them.
- When the precomputed findings don't answer something, compute it: signal_catalog and query_signal read any recorded signal (other cases too, via find_cases); run_method runs Veyra's methods; run_python runs your own analysis. Never state a number you did not get from a tool.
- Timing: say which signal moved first only when onset_order (or your own computation that accounts for clock offset and sampling) says so. Different clocks without a declared offset cannot be ordered: say "cannot tell" and what would settle it.
- Units: use the unit each tool reports. Convert only between known pairs and say so.
Finish by calling submit_report exactly once."""

QUESTION_PROMPT = """An engineer asked a follow-up question about this case:
"{question}"

First test the question's premise against the data: does what it assumes actually show in the readings? If the data contradicts it, say so plainly in premise (status not_supported) and answer what the data does show; do not explain a fault the data does not show. If the data cannot settle it, use cannot_tell and say what would.
Compute what you need with the tools. Then call submit_report with premise and answer filled in, plus the usual fields (they may be short). Never state a confirmed root cause.
Style: answer.text is at most 2 short sentences, the direct answer first ("Yes.", "No.", "Can't tell."), then the one number or reason that matters. premise.explanation is one short sentence. Plain words an operator understands; signal names only when needed; times as HH:MM:SS."""


class AgentError(ValueError):
    pass


def egress_allowed(store, case_id: str, provider: str) -> Optional[Dict[str, Any]]:
    case = store.case(case_id)
    if case["synthetic"]:
        return {"basis": "synthetic case"}
    grants = [e for e in store.ledger(case_id) if e["kind"] == "model_authorization" and e["payload"]["provider"] == provider]
    return {"basis": "model_authorization", "entry_id": grants[-1]["id"], "scope": grants[-1]["payload"]["scope"]} if grants else None


_NUM = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?")
_HEDGE = re.compile(r"\b(may|might|could|likely|probably|possibly|suggests?|appears?|seems?|consistent with|fits|presumably|artifact|artefact)\b", re.IGNORECASE)


def _number_in(n: str, text: str) -> bool:
    if n in text:
        return True
    x = float(n)
    return any(abs(x - float(m)) <= max(0.051, abs(float(m)) * 0.006) for m in _NUM.findall(text))


def _question_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    if spec["name"] != "submit_report":
        return spec
    schema = json.loads(json.dumps(spec["input_schema"]))
    cited = {"type": "array", "items": {"type": "string"}}
    schema["properties"]["premise"] = {"type": "object", "required": ["status", "explanation", "evidence"], "properties": {
        "status": {"type": "string", "enum": ["supported", "not_supported", "cannot_tell"]}, "explanation": {"type": "string"}, "evidence": cited}}
    schema["properties"]["answer"] = {"type": "object", "required": ["text", "evidence"], "properties": {"text": {"type": "string"}, "evidence": cited}}
    schema["required"] = schema["required"] + ["premise", "answer"]
    return {**spec, "description": "Finish. Answer the engineer's question: premise check first, then the answer, each citing tool calls.", "input_schema": schema}


_COMPONENT = re.compile(r"\b(drives?|encoders?|cabl\w*|wiring|motors?|gearbox\w*|harness\w*|connectors?|firmware|sensors?|bearings?)\b", re.IGNORECASE)


def _check_report(report: Dict[str, Any], valid_ids: set, outputs: Optional[Dict[str, str]] = None, question: Optional[str] = None) -> List[str]:
    problems = []
    if question:
        for key, field in (("premise", "explanation"), ("answer", "text")):
            item = report.get(key) or {}
            if not (item.get(field) or "").strip():
                problems.append(f"{key} is missing: answer the engineer's question")
                continue
            cited = item.get("evidence") or []
            if not cited:
                problems.append(f"{key} cites no tool call")
            elif any(c not in valid_ids for c in cited):
                problems.append(f"{key} cites unknown tool call ids")
            elif outputs is not None:
                blob = " ".join(outputs.get(c, "") for c in cited)
                missing = [n for n in _NUM.findall(item[field]) if ("." in n or len(n.lstrip("-")) >= 3) and not _number_in(n, blob)]
                if missing:
                    problems.append(f"{key} states numbers not found in the tool calls it cites: {', '.join(missing[:5])}")
            if _CONFIRMED.search(item.get(field) or ""):
                problems.append(f"{key}: do not state a confirmed root cause")
        if (report.get("premise") or {}).get("status") not in (None, "supported", "not_supported", "cannot_tell"):
            problems.append("premise.status must be supported, not_supported or cannot_tell")
    for section, key in (("observations", "evidence"), ("hypotheses", "support"), ("proposed_checks", "evidence")):
        for index, item in enumerate(report.get(section) or []):
            cited = item.get(key) or []
            if not cited:
                problems.append(f"{section}[{index}] cites no tool call")
            unknown = [c for c in cited + (item.get("against") or []) if c not in valid_ids]
            if unknown:
                problems.append(f"{section}[{index}] cites unknown tool call ids: {', '.join(unknown[:4])}")
            if outputs is not None and section in ("observations", "hypotheses") and not unknown:
                text = item.get("text") or item.get("statement") or ""
                blob = " ".join(outputs.get(c, "") for c in cited + (item.get("against") or []))
                missing = [n for n in _NUM.findall(text) if ("." in n or len(n.lstrip("-")) >= 3) and not _number_in(n, blob)]
                if missing:
                    problems.append(f"{section}[{index}] states numbers not found in the tool calls it cites: {', '.join(missing[:5])}")
    for index, item in enumerate(report.get("observations") or []):
        hedge = _HEDGE.search(item.get("text") or "")
        if hedge:
            problems.append(f"observations[{index}] contains an interpretation ('{hedge.group(0)}'); state only what the data shows and move the interpretation to a hypothesis")
    hyps = report.get("hypotheses") or []
    leading = [h for h in hyps if h.get("status") == "leading"]
    if len(leading) > 1:
        problems.append("only one hypothesis can be leading")
    if leading:
        unopposed = [i for i, h in enumerate(hyps) if h.get("status") == "open" and not h.get("against")]
        if unopposed:
            problems.append(f"a hypothesis is 'leading' but hypotheses[{unopposed[0]}] has no evidence against it; cite that evidence in its 'against' or mark the leading one 'open'")
        named = {m.group(1).lower()[:5] for m in _COMPONENT.finditer(leading[0].get("statement") or "")}
        if len(named) >= 2:
            problems.append("the leading hypothesis lists several components the data doesn't separate; name the part (e.g. the joint) or mark it 'open'")
    limits = (("observations", 6), ("hypotheses", 4), ("proposed_checks", 5), ("questions_for_engineers", 4))
    for key, most in limits:
        if len(report.get(key) or []) > most:
            problems.append(f"{key}: keep at most {most - 1}; drop the least useful")
    if len((report.get("summary") or "").split()) > 70:
        problems.append("summary is too long: 2 short sentences")
    texts = [report.get("summary", "")] + [h.get("statement", "") for h in report.get("hypotheses") or []]
    if any(_CONFIRMED.search(t or "") for t in texts):
        problems.append("Do not state a confirmed root cause; rank hypotheses instead")
    if not (report.get("summary") or "").strip():
        problems.append("summary is empty")
    return problems


def run_agent(store, case_id: str, *, model, actor: str = "Veyra agent", max_turns: int = MAX_TURNS, question: Optional[str] = None) -> Dict[str, Any]:
    grant = egress_allowed(store, case_id, model.provider)
    if grant is None:
        raise AgentError(f"This case holds private data. Record a model_authorization for '{model.provider}' before sending it to the model.")
    run = store.latest_run(case_id)
    if run is None:
        run = store.investigate(case_id, actor=actor)
    question = (question or "").strip()[:600] or None
    box = toolset.Toolbox(store, case_id, may_share=lambda cid: cid == case_id or egress_allowed(store, cid, model.provider) is not None)
    if box.run["id"] != run["id"]:
        box.run = run
    first = QUESTION_PROMPT.format(question=question) + f"\n\nCase {case_id}. Start with case_overview." if question else f"Investigate case {case_id}. Start with case_overview."
    messages: List[Dict[str, Any]] = [{"role": "user", "content": first}]
    tools = toolset.TOOL_SPECS if not question else [_question_spec(t) for t in toolset.TOOL_SPECS]
    calls: List[Dict[str, Any]] = []
    texts: Dict[str, str] = {}
    usage = {"input_tokens": 0, "output_tokens": 0}
    report, problems, attempts = None, [], 0
    started = time.perf_counter()
    for turn in range(max_turns):
        try:
            response = model.create(system=SYSTEM_PROMPT, messages=messages, tools=tools)
        except ModelError as error:
            raise AgentError(str(error)) from None
        for key in usage:
            usage[key] += int((response.get("usage") or {}).get(key, 0))
        content = response.get("content") or []
        messages.append({"role": "assistant", "content": content})
        uses = [b for b in content if b.get("type") == "tool_use"]
        if not uses:
            if report is None:
                messages.append({"role": "user", "content": "Finish by calling submit_report with cited evidence."})
                continue
            break
        results = []
        for use in uses:
            name, args, use_id = use["name"], use.get("input") or {}, use["id"]
            if name == "submit_report":
                attempts += 1
                problems = _check_report(args, {c["id"] for c in calls}, texts, question=question)
                report = args
                text = ("Report accepted." if not problems else "Fix these and call submit_report again: " + "; ".join(problems))
                results.append({"type": "tool_result", "tool_use_id": use_id, "content": text, "is_error": bool(problems)})
                continue
            try:
                result = box.call(name, args)
                text, error = toolset.serialize(result), False
            except (ValueError, TypeError, KeyError) as exc:
                text, error = f"Tool error: {exc}", True
            texts[use_id] = text
            calls.append({"id": use_id, "tool": name, "input": args, "result_sha256": hashlib.sha256(text.encode()).hexdigest(),
                          "error": error, "result_preview": text[:4000 if name in toolset.COMPUTE_TOOLS else 600]})
            results.append({"type": "tool_result", "tool_use_id": use_id, "content": text, "is_error": error})
        messages.append({"role": "user", "content": results})
        if report is not None and (not problems or attempts >= MAX_REPORT_ATTEMPTS):
            break
    if report is None:
        raise AgentError("The agent did not submit a report within its turn limit")
    from app.engine import next_action as na
    rows = na.compare(run, na.assess(run), na.site_results(store.ledger(case_id), run["id"]))
    plan_first = rows[0]["check_id"] if rows else None
    first_check = next((c.get("check_id") for c in report.get("proposed_checks") or [] if c.get("check_id")), None)
    payload = {
        "agent": AGENT_VERSION, "provider": model.provider, "model": model.model, "run_id": run["id"], "run_hash": run["run_hash"],
        "question": question, "report": report, "citations_valid": not problems, "citation_problems": problems,
        "plan_first": plan_first, "read_plan": any(c["tool"] == "next_action_plan" for c in calls), "first_check_differs": bool(plan_first and first_check and first_check != plan_first),
        "computations": box.computations, "cases_used": sorted(box.cases_used),
        "tool_calls": calls, "turns": len([m for m in messages if m["role"] == "assistant"]), "usage": usage,
        "duration_ms": round((time.perf_counter() - started) * 1000), "egress": {**grant, "raw_samples_shared": box.samples_shared},
        "boundary": "AI-generated investigation notes from read-only tools. Hypotheses are not confirmed causes; a person decides what to check or do.",
    }
    return store.append_trusted(case_id, "agent_report", payload, actor=actor, source=f"veyra://agent/{model.provider}/{model.model}")
