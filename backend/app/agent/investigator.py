"""Autonomous investigation: Veyra does the investigative work and hands the engineer one next action.

The model plans; Veyra executes. Every check the agent runs is a read-only tool call over the ORIGINAL data (raw log
lines with their real line numbers and values, or the recording's signals), recorded as a step the engineer can see.
The result is one screen:
  what happened -> what Veyra already checked (and what that ruled out) -> the one next action -> what you found.

Boundaries enforced here, not left to the prompt:
- Data egress: a real case is only sent to a model provider after a model_authorization is recorded (runner.egress_allowed).
- Evidence integrity: every quoted line is compared with the original line; every number in the result must appear in
  the cited tool output or the cited lines. A result that fails is returned to the model to fix; what still fails is
  dropped and listed as dropped.
- No confirmed root cause: status is 'cause_probable' at most; confirmation comes from the engineer's result.
- Without a model, a deterministic investigation (the engine's own reading) gives the same screen, labelled as such.
"""
from __future__ import annotations

import json
import re
import statistics
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

INVESTIGATOR_VERSION = "veyra-investigator-v1"
MAX_TURNS = 14
MAX_FIX_ATTEMPTS = 2

SYSTEM_PROMPT = """You are Veyra, an autonomous field-investigation engineer for robots and drones. An engineer uploaded the
data from a failure. Your job is to DO the investigation, not to hand the engineer a reading list.

How to work:
1. Call overview first: Veyra's reading of the data (incidents, restarts, which component warned first, repeated messages).
2. Map the fault space: 3-6 competing hypotheses, each a place the fault can be (an area of the machine or software,
   e.g. "Network path", "Board firmware", "Sensor hardware", "Host / agent side"). For each, decide which check in THIS
   data would separate them, and RUN it with the tools: search for messages, read lines around events, measure counts,
   rates and the time between events, compare with earlier cases (lessons). Prefer checks that can rule an area out.
   Typically 4-10 tool calls. Report every hypothesis with its status: leading, open, weakened or ruled_out, and the
   line(s) or result that decided it. ruled_out needs DIRECT exclusion (basis "direct"): evidence that observes that
   area itself and shows it is not the fault. A measurement at one point of a path excludes only what lies beyond that
   point toward the observer, never the parts it did not observe (a capture at the receiving host does not clear the
   cable, switch or sender-side driver): use weakened (basis "indirect") and say where the evidence was observed.
   An engineer's reported result is evidence but not verified by Veyra: on its own it can weaken an area or make one
   leading, never rule one out. ruled_out also needs excluded_by "data": the exclusion is visible in the data you read.
3. Separate cause from consequence: a crash, a restart, a watchdog, an e-stop or a timeout is often the effect of
   something earlier. Say which lines are the earliest sign and which are effects.
4. Only what the data cannot answer becomes the next action for a person: design it as an experiment that splits the
   hypotheses still standing. One concrete action a field engineer can do (a command to run, a value to read, a part to
   inspect, a config to check). For each possible result: which hypotheses it keeps, which it directly rules out, which
   it only weakens, and what Veyra would do next. Never propose an action you could have done yourself with the tools.
4b. Check the data before you ask a person. A field engineer or a customer is usually asked many questions (when was it
   charged, when was the plug pulled, what was done before, which software version). Answer every one the data can
   answer yourself and list them in already_answered, with the line that answers it. Only what the data cannot show
   becomes a field_question (at most 3): a fact that changes the next step. For each, cite the tool calls where you
   looked for it (searched_calls), say why the data cannot answer it, and say how each possible answer would move the
   hypotheses. Machine data that exists elsewhere (another time window, a normal run under the same conditions, another
   vehicle, a freeze-frame) is a data_request to the engineering team, not a question for the customer. Physical facts no signal in this data can show (a connector's seating, a terminal's torque, a harness
   under tension) go in unobservable, with how a person could observe them. Stop guessing where the data ends: ask.
   The case context may list what engineers already tried (engineers_already_tried), each step replayed against the data
   (data_before: settled, answerable from signals, not separable, or not in the data; data_after: unchanged or resolved
   after the step). Do not propose repeating a step that was not the issue; when data_after is "unchanged" after a part
   was swapped, the swap did not touch the relation that does not add up: say so and use it as evidence.
   For a battery-electric machine an electrical tool may be offered. Call it; a relation that does not add up is evidence
   even when no alarm fired. Cite its findings in checked with its call id, by relation (e.g. B_FR, R_FR, P) and tier:
   a "measured" finding is read from the signals; a "model" finding holds only under the assumptions listed with it,
   and you state them (a branch resistance assumes a current share: never call it measured or identified). The
   candidates it marks consistent are what the recorded signals cannot tell apart: keep each as an open hypothesis
   (none of them is ruled out, and none leads the others on this data alone), and give each its role. Similar wheel
   torque does not show similar DC current. A pack resistance comparison names what else differs (state of charge,
   current, launch length); do not attribute it to temperature alone when it says so. The next action is the
   measurement it recommends (or a cheaper check you can show separates the same candidates), with the result each
   candidate predicts and its follow-up if the result could mislead.
   A re-test is not "run it again": the next action names the operating conditions to reproduce (temperature, state
   of charge, vehicle state, route, load), the baseline to compare with (a normal run under the same conditions, or a
   snapshot in this data), the signals to record, and whether it needs an engineer's approval (it changes the vehicle's
   state, energises high voltage, drives the vehicle, or touches safety functions).
5. Classify the log's notable events into groups (events): root_cause_candidate, anomaly, effect, or routine (start-up,
   configuration, normal traffic). Group repeated messages together. Routine events need no attention from anyone.

Rules:
- Quote log lines exactly as the tools return them, with their line numbers. Every number you write must come from a
  tool result you cite (by tool call id).
- Never claim a confirmed root cause. status is cause_probable only when the evidence points to one explanation and
  you ruled out the main alternatives; otherwise cause_unknown. Say plainly what the data cannot establish.
- An earlier case (lesson) is a lead with conditions; reuse its fix only if its verdict is 'applies'.
- Write for a busy engineer: short, plain, specific to this machine. No filler.
- If the engineer already reported results of earlier actions (case_context), build on them: do not repeat a check.
  Keep the ids of earlier hypotheses (case_context.previous_hypotheses) and update their status; add new ones only
  when the evidence opens a new area.
- An event the engineer dismissed in this case (case_context.dismissed_in_this_case) is routine in this case only.
  A machine baseline (case_context.machine_baselines) is context with conditions, never a reason to hide an event:
  check whether its conditions match this case, and still list the event.
- An engineer's result has two parts: the outcome they selected (a label you proposed) and what they observed. Only the
  observations are evidence; never treat your own description of an outcome as observed.
- In all text a person reads, name hypotheses by their area ("Network path"), never by id; ids go only in keeps / rules_out.
- Fill reuse_when: the conditions under which your explanation, once confirmed, would apply to another machine.
- Give each hypothesis that is not ruled out a role: main_cause (removing it removes the fault), trigger (a condition or
  event that lets the fault show, without being the fault), amplifier (makes it worse or decides where it shows first),
  or effect (a consequence of the fault). A failure often has one main cause and several triggers and amplifiers.
Finish by calling submit_report exactly once."""

SUBMIT_SPEC = {
    "name": "submit_report",
    "description": "Submit the investigation result (once).",
    "input_schema": {
        "type": "object",
        "properties": {
            "headline": {"type": "string", "description": "What happened, one sentence, under 20 words"},
            "status": {"type": "string", "enum": ["cause_probable", "cause_unknown", "no_failure_found"]},
            "explanation": {"type": "string", "description": "Leading explanation at the level the data can separate, under 40 words; empty if none"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "evidence": {"type": "array", "maxItems": 5, "items": {"type": "object", "properties": {
                "line": {"type": "integer"}, "quote": {"type": "string"}, "shows": {"type": "string"}, "calls": {"type": "array", "items": {"type": "string"}}},
                "required": ["line", "quote", "shows", "calls"]}},
            "checked": {"type": "array", "maxItems": 8, "description": "Checks you RAN with the tools, in order", "items": {"type": "object", "properties": {
                "check": {"type": "string"}, "result": {"type": "string"},
                "verdict": {"type": "string", "enum": ["supports", "weakens", "rules_out", "inconclusive"]},
                "about": {"type": "string", "description": "Which explanation it bears on"},
                "calls": {"type": "array", "items": {"type": "string"}}}, "required": ["check", "result", "verdict", "calls"]}},
            "effects": {"type": "array", "maxItems": 5, "description": "Lines that are consequences, not causes", "items": {"type": "object", "properties": {
                "line": {"type": "integer"}, "quote": {"type": "string"}, "why": {"type": "string"}}, "required": ["line", "quote", "why"]}},
            "next_action": {"type": "object", "properties": {
                "action": {"type": "string", "description": "Imperative, under 15 words"},
                "why": {"type": "string", "description": "What it will settle, under 30 words"},
                "how": {"type": "array", "maxItems": 4, "items": {"type": "string"}},
                "where": {"type": "string", "enum": ["remote", "on_site", "lab"]},
                "conditions": {"type": "string", "description": "Operating conditions to reproduce for the check (temperature, state of charge, vehicle state, route, load), under 30 words"},
                "baseline": {"type": "string", "description": "What to compare the result with: a normal run under the same conditions, or a snapshot or lines in this data, under 25 words"},
                "record": {"type": "array", "maxItems": 8, "items": {"type": "string"}, "description": "Signals or values to record during the check, by their names in the data"},
                "needs_approval": {"type": "boolean", "description": "True when the check changes the vehicle's state, energises high voltage, drives the vehicle or touches safety functions"},
                "outcomes": {"type": "array", "minItems": 2, "maxItems": 3, "items": {"type": "object", "properties": {
                    "label": {"type": "string", "description": "What the engineer would find, under 8 words"},
                    "means": {"type": "string", "description": "What that would mean, under 20 words"},
                    "keeps": {"type": "array", "items": {"type": "string"}, "description": "Hypothesis ids still standing after this result"},
                    "rules_out": {"type": "array", "items": {"type": "string"}, "description": "Hypothesis ids this result DIRECTLY excludes"},
                    "weakens": {"type": "array", "items": {"type": "string"}, "description": "Hypothesis ids this result makes less likely without excluding them"},
                    "then": {"type": "string", "description": "What Veyra would do next after this result, under 12 words"}},
                    "required": ["label", "means", "keeps", "rules_out"]}}},
                "required": ["action", "why", "how", "where", "outcomes"]},
            "unknowns": {"type": "array", "maxItems": 3, "items": {"type": "string"}},
            "already_answered": {"type": "array", "maxItems": 6, "description": "Questions a person would normally be asked about this failure that the data already answers, so nobody has to ask them", "items": {"type": "object", "properties": {
                "question": {"type": "string", "description": "The question as a field engineer or customer would be asked it, under 15 words"},
                "answer": {"type": "string", "description": "The answer from the data, under 25 words"},
                "line": {"type": "integer"}, "quote": {"type": "string"},
                "calls": {"type": "array", "items": {"type": "string"}}}, "required": ["question", "answer", "line", "quote", "calls"]}},
            "data_requests": {"type": "array", "maxItems": 3, "description": "Machine data that exists somewhere but is not in this case (another time window, a normal run under the same conditions, another vehicle, a controller's freeze-frame). Asked of the engineering team or their systems, never of the customer", "items": {"type": "object", "properties": {
                "what": {"type": "string", "description": "The data, under 20 words"},
                "window": {"type": "string", "description": "Time window or runs, under 20 words"},
                "conditions": {"type": "string", "description": "Operating conditions it must match, under 25 words"},
                "signals": {"type": "array", "maxItems": 10, "items": {"type": "string"}},
                "why_needed": {"type": "string", "description": "Which decision it settles, under 25 words"},
                "searched_calls": {"type": "array", "items": {"type": "string"}, "description": "Tool call ids that show it is not in this case"}},
                "required": ["what", "why_needed", "searched_calls"]}},
            "field_questions": {"type": "array", "maxItems": 3, "description": "Only facts the data cannot show and that change the next step", "items": {"type": "object", "properties": {
                "question": {"type": "string", "description": "One concrete question, under 20 words"},
                "ask": {"type": "string", "enum": ["field_engineer", "customer", "supplier"]},
                "why_needed": {"type": "string", "description": "Which decision it settles, under 25 words"},
                "not_in_data": {"type": "string", "description": "What you looked for and why the data cannot answer it, under 30 words"},
                "searched_calls": {"type": "array", "items": {"type": "string"}, "description": "Tool call ids where you looked for this in the data"},
                "if_answers": {"type": "array", "minItems": 2, "maxItems": 3, "items": {"type": "object", "properties": {
                    "answer": {"type": "string", "description": "A possible answer, under 8 words"},
                    "means": {"type": "string", "description": "What it would change, under 20 words"},
                    "keeps": {"type": "array", "items": {"type": "string"}},
                    "weakens": {"type": "array", "items": {"type": "string"}}}, "required": ["answer", "means"]}}},
                "required": ["question", "ask", "why_needed", "not_in_data", "searched_calls", "if_answers"]}},
            "unobservable": {"type": "array", "maxItems": 4, "description": "Physical facts that matter here but that no signal in this data can show", "items": {"type": "object", "properties": {
                "fact": {"type": "string", "description": "under 15 words"},
                "why_it_matters": {"type": "string", "description": "Which hypothesis depends on it, under 20 words"},
                "how_to_observe": {"type": "string", "description": "What a person would measure or inspect, under 20 words"}}, "required": ["fact", "how_to_observe"]}},
            "hypotheses": {"type": "array", "minItems": 2, "maxItems": 6, "items": {"type": "object", "properties": {
                "id": {"type": "string", "description": "h1, h2... keep ids from earlier rounds"},
                "area": {"type": "string", "description": "Where the fault would be, 2-5 words"},
                "claim": {"type": "string", "description": "The hypothesis, under 20 words"},
                "status": {"type": "string", "enum": ["leading", "open", "weakened", "ruled_out"]},
                "role": {"type": "string", "enum": ["main_cause", "trigger", "amplifier", "effect"], "description": "The part it plays if it holds: main_cause (removing it removes the fault), trigger (a condition that lets the fault show), amplifier (makes it worse or decides where it shows first), effect (a consequence)"},
                "because": {"type": "string", "description": "What decided this status, under 25 words"},
                "basis": {"type": "string", "enum": ["direct", "indirect"], "description": "direct: the evidence observes this area itself; required for ruled_out"},
                "observed_at": {"type": "string", "description": "Where the deciding evidence was observed (e.g. 'agent host NIC', 'board Tx buffer')"},
                "excluded_by": {"type": "string", "enum": ["data", "engineer_report"], "description": "For ruled_out: is the exclusion visible in the data Veyra read, or only in an engineer's report? ruled_out needs 'data'"},
                "lines": {"type": "array", "items": {"type": "integer"}}}, "required": ["id", "area", "claim", "status", "because"]}},
            "events": {"type": "array", "maxItems": 12, "description": "Notable log events grouped and classified", "items": {"type": "object", "properties": {
                "lines": {"type": "array", "items": {"type": "integer"}}, "what": {"type": "string", "description": "under 12 words"},
                "class": {"type": "string", "enum": ["root_cause_candidate", "anomaly", "effect", "routine"]},
                "why": {"type": "string", "description": "under 20 words"}}, "required": ["lines", "what", "class"]}},
            "lesson": {"type": "string", "description": "How earlier cases apply, one sentence; empty if none"},
            "reuse_when": {"type": "string", "description": "If this explanation is later confirmed: the conditions under which it would apply to another machine (hardware, firmware/software version, configuration, log signature), under 40 words"},
        },
        "required": ["headline", "status", "confidence", "evidence", "checked", "next_action", "unknowns", "hypotheses", "events"],
    },
}

_NUM = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])")
_CONFIRMED = re.compile(r"\b(root cause (is|was) (confirmed|identified)|confirmed root cause|definitely caused|is the root cause)\b", re.I)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


# ---------------------------------------------------------------- tools over the original log
class LogTools:
    """Read-only tools over the original text log (raw lines, real line numbers)."""

    def __init__(self, store, case_id: str):
        from app.ingest import rawstore, textlog
        self.store, self.case_id = store, case_id
        found = store.discovery(case_id) or {}
        self.found = found
        sha = (found.get("source") or {}).get("sha256")
        self.raw: List[str] = (rawstore.lines(store.path, sha) if sha else None) or []
        self.parsed = {}
        if self.raw:
            import tempfile
            from pathlib import Path
            with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as fh:
                fh.write("\n".join(self.raw))
            log = textlog.read(Path(fh.name))
            Path(fh.name).unlink(missing_ok=True)
            self.parsed = {p.n: p for p in log.lines}
            self.clock = log.clock

    def available(self) -> bool:
        return bool(self.raw)

    def electrical_applies(self) -> bool:
        from app.physics import electrical
        return bool(self.raw) and electrical.detect(self.raw) is not None

    def electrical_full(self) -> Dict[str, Any]:
        """Physics relations over a decoded CAN log, in full (for the case page)."""
        from app.physics import electrical
        return electrical.analyse(self.raw) or {"applies": False}

    def electrical(self) -> Dict[str, Any]:
        """What the agent is given: measured and model-derived findings with their assumptions, the mechanisms the recorded
        signals cannot separate, and the measurement plan."""
        from app.physics import electrical
        return electrical.agent_view(electrical.analyse(self.raw))

    def _line(self, n: int) -> Dict[str, Any]:
        p = self.parsed.get(n)
        out = {"line": n, "text": self.raw[n - 1][:400]}
        if p is not None and p.t_ns is not None and self.clock != "line_order":
            out["t"] = round(p.t_ns / 1e9, 6)
        return out

    def overview(self) -> Dict[str, Any]:
        log = self.found.get("log") or {}
        counts: Dict[str, Dict[str, Any]] = {}
        for p in self.parsed.values():
            if p.level in ("WARNING", "ERROR", "FATAL") or p.kind:
                c = counts.setdefault(p.template, {"count": 0, "first_line": p.n, "last_line": p.n, "level": p.level, "kind": p.kind,
                                                    "example": self.raw[p.n - 1][:300]})
                c["count"] += 1
                c["last_line"] = p.n
        repeated = sorted(counts.values(), key=lambda c: (c["first_line"]))[:40]
        inc = log.get("incident") or {}
        return {"lines": len(self.raw), "clock": getattr(self, "clock", None), "notes": log.get("notes"), "levels": log.get("levels"),
                "restarts": [{"line": r["line"], "text": self.raw[r["line"] - 1][:300], "stated_cause": r["primary_cause"],
                              "cause_lines": [self._line(n) for c in r["causes"] for n in c["lines"][:2]], "note": r.get("note")}
                             for r in log.get("restarts") or []],
                "first_failure": ({"line": inc["line"], "text": self.raw[inc["line"] - 1][:300], "kind": inc["kind"],
                                   "warned_right_before": inc.get("proximate"),
                                   "first_warning_per_component": [{"component": f["component"], "line": f["first_line"],
                                                                    "text": self.raw[f["first_line"] - 1][:200], "count": len(f["lines"])}
                                                                   for f in inc.get("first_signs") or []][:8],
                                   "same_component_later": inc.get("threads")} if inc else None),
                "warning_and_error_messages": [{k: c[k] for k in ("count", "first_line", "last_line", "level", "kind", "example")} for c in repeated],
                "note": "Line numbers are the file's own. Texts are the original lines."}

    def read_lines(self, start: int, end: Optional[int] = None) -> Dict[str, Any]:
        start = max(1, int(start))
        end = min(len(self.raw), int(end or start + 40), start + 199)
        return {"lines": [self._line(n) for n in range(start, end + 1)], "total_lines": len(self.raw)}

    def search(self, pattern: str, context: int = 0, max_hits: int = 60) -> Dict[str, Any]:
        try:
            rx = re.compile(pattern, re.I)
        except re.error:
            rx = re.compile(re.escape(pattern), re.I)
        hits = [n for n, text in enumerate(self.raw, 1) if rx.search(text)]
        out = []
        context = max(0, min(int(context or 0), 5))
        for n in hits[:max_hits]:
            item = self._line(n)
            if context:
                item["context"] = [self._line(k) for k in range(max(1, n - context), min(len(self.raw), n + context) + 1) if k != n]
            out.append(item)
        return {"pattern": pattern, "matches": len(hits), "lines": out, "truncated": len(hits) > max_hits}

    def measure(self, pattern: str, then_pattern: Optional[str] = None) -> Dict[str, Any]:
        """Count, first / last occurrence, intervals between occurrences, and (optionally) the time from each occurrence to
        the next line matching then_pattern. Times only when the log has timestamps."""
        def rx(p):
            try:
                return re.compile(p, re.I)
            except re.error:
                return re.compile(re.escape(p), re.I)
        a = rx(pattern)
        hits = [n for n, text in enumerate(self.raw, 1) if a.search(text)]
        res: Dict[str, Any] = {"pattern": pattern, "count": len(hits)}
        if not hits:
            return res
        res["first"] = self._line(hits[0])
        res["last"] = self._line(hits[-1])
        times = [(n, self.parsed[n].t_ns) for n in hits if n in self.parsed and self.parsed[n].t_ns is not None]
        timed = getattr(self, "clock", "line_order") != "line_order" and len(times) == len(hits)
        if timed and len(times) > 1:
            gaps = [(b[1] - a_[1]) / 1e9 for a_, b in zip(times, times[1:])]
            res["interval_s"] = {"min": round(min(gaps), 6), "median": round(statistics.median(gaps), 6), "max": round(max(gaps), 6)}
            span = (times[-1][1] - times[0][1]) / 1e9
            res["span_s"] = round(span, 6)
            if span > 0:
                res["rate_per_s"] = round((len(times) - 1) / span, 4)
        elif not timed:
            res["note"] = "This log has no usable timestamps for these lines: only counts and order."
        if then_pattern:
            b = rx(then_pattern)
            pairs = []
            for n in hits[:40]:
                m = next((k for k in range(n + 1, len(self.raw) + 1) if b.search(self.raw[k - 1])), None)
                if m is None:
                    pairs.append({"from_line": n, "to_line": None})
                    continue
                d = {"from_line": n, "to_line": m, "lines_between": m - n - 1}
                pa, pb = self.parsed.get(n), self.parsed.get(m)
                if timed and pa and pb and pa.t_ns is not None and pb.t_ns is not None:
                    d["seconds"] = round((pb.t_ns - pa.t_ns) / 1e9, 6)
                pairs.append(d)
            res["then"] = {"pattern": then_pattern, "pairs": pairs}
        return res


TOOL_SPECS = [
    {"name": "overview", "description": "Veyra's reading of the original log: restarts and stated causes, the first failure, which component warned first and right before it, and every warning/error message with count and first/last line.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "read_lines", "description": "Original lines start..end (max 200) with real line numbers and timestamps.",
     "input_schema": {"type": "object", "properties": {"start": {"type": "integer"}, "end": {"type": "integer"}}, "required": ["start"]}},
    {"name": "search", "description": "Lines matching a regular expression (case-insensitive), with optional context lines.",
     "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"}, "context": {"type": "integer"}}, "required": ["pattern"]}},
    {"name": "measure", "description": "Count, first/last occurrence, intervals and rate of a message; optional then_pattern gives the time and lines from each occurrence to the next matching line.",
     "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"}, "then_pattern": {"type": "string"}}, "required": ["pattern"]}},
    {"name": "lessons", "description": "Earlier closed cases with a similar signature, and whether each applies here (applies / check_first / not_applicable).",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "case_context", "description": "The machine, what the reporter observed, and results the engineer already reported for earlier next actions.",
     "input_schema": {"type": "object", "properties": {}}},
]

ELECTRICAL_SPEC = {"name": "electrical", "description": "For a decoded CAN log of a battery-electric machine: checks the physical relations (each inverter's voltage drop from the pack against pack current, the pack's voltage drop against current, inverter voltages at rest, torque command against the BMS limit). Separates what is measured from what a model derives (a branch resistance needs the branch's current share, which is not recorded, so it is given over the plausible range of shares) and from what no signal shows. For a branch that does not add up, lists the candidate mechanisms as consistent or weakened (never ruled out), and the measurements that would separate them with the result each candidate predicts, their cost and blind spots, and the lowest-cost one that discriminates. Deterministic, computed from the file.",
                   "input_schema": {"type": "object", "properties": {}}}


def tool_specs(tools: "LogTools") -> List[Dict[str, Any]]:
    return TOOL_SPECS + ([ELECTRICAL_SPEC] if tools.electrical_applies() else [])


STEP_LABEL = {"electrical": "Checked the electrical relations", "overview": "Read the whole log", "read_lines": "Read lines {start}–{end}", "search": "Searched for “{pattern}”",
              "measure": "Measured “{pattern}”", "lessons": "Compared with earlier cases", "case_context": "Read the report and earlier results"}


def _case_context(store, case_id: str) -> Dict[str, Any]:
    case = store.case(case_id)
    led = store.ledger(case_id)
    reports = [e["payload"].get("stated_reason") for e in led if e["kind"] == "intervention_report"]
    findings = [{"recorded_at": e["recorded_at"], "by": e["actor"], **{k: e["payload"].get(k) for k in ("action", "outcome_selected", "result", "observations")}}
                for e in led if e["kind"] == "engineer_finding"]
    prev = next((e["payload"]["result"].get("hypotheses") for e in reversed(led) if e["kind"] == "agent_report"
                 and e["payload"].get("kind") == "autonomous_investigation" and e["payload"].get("mode") == "agent"), None)
    here: Dict[str, Dict[str, Any]] = {}
    for e in led:
        if e["kind"] == "event_note" and e["payload"].get("scope", "case") == "case":
            here[e["payload"]["pattern"]] = e["payload"]
    baselines: Dict[str, Dict[str, Any]] = {}
    for e in [x for x in led if x["kind"] == "event_note"] + _machine_notes(store, case["machine_id"], case_id):
        if e["payload"].get("scope") == "machine":
            baselines[e["payload"]["pattern"]] = e["payload"]
    tried = None
    if any(e["kind"] == "debug_walkthrough" for e in led):
        from app.agent import replay as _rp
        rc = _rp.replay_case(store, case_id)
        if rc:
            tried = [{"step": st["check"], "when": st.get("when"), "result": st["result"], "why": st.get("why"), "decided_by": st.get("decided_by"),
                      "data_before": r["before"]["verdict"], "data_after": (r.get("after") or {}).get("verdict")}
                     for st, r in zip(rc["walkthrough"]["steps"], rc["replay"]["steps"])]
    return {"machine": case["machine_id"], "title": case["title"], "reported": reports, "engineer_results": findings,
            "engineers_already_tried": tried,
            "previous_hypotheses": reviewed_hypotheses(prev)[0] if prev else prev,
            "dismissed_in_this_case": [n["pattern"] for n in here.values() if n["verdict"] == "normal"],
            "machine_baselines": [{k: n.get(k) for k in ("pattern", "evidence", "applies_when")} for n in baselines.values() if n["verdict"] == "normal"]}


def _machine_notes(store, machine_id: str, exclude: str) -> List[Dict[str, Any]]:
    """Event notes the engineer scoped to this machine in its other cases."""
    out = []
    for c in store.cases():
        if c["machine_id"] == machine_id and c["id"] != exclude:
            out += [e for e in store.ledger(c["id"]) if e["kind"] == "event_note" and e["payload"].get("scope") == "machine"]
    return out


def _summary(name: str, result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    if name == "search":
        return f"{result.get('matches', 0)} matching line{'s' if result.get('matches') != 1 else ''}"
    if name == "measure":
        s = f"{result.get('count', 0)} occurrence{'s' if result.get('count') != 1 else ''}"
        if result.get("interval_s"):
            s += f", every {result['interval_s']['median']:g} s (median)"
        if (result.get("then") or {}).get("pairs"):
            secs = [p["seconds"] for p in result["then"]["pairs"] if p.get("seconds") is not None]
            if secs:
                s += f"; followed by “{result['then']['pattern']}” after {min(secs):g}–{max(secs):g} s"
        return s
    if name == "overview":
        ff = result.get("first_failure")
        return (f"{result.get('lines')} lines; " + (f"first failure at line {ff['line']}" if ff else
                f"{len(result.get('restarts') or [])} restart(s)" if result.get("restarts") else "no failure line recognised"))
    if name == "lessons":
        return f"{len(result.get('lessons') or [])} earlier case(s) compared"
    if name == "electrical":
        if result.get("applies") is False:
            return "not applicable to this log"
        fs = [f["relation"] for f in result.get("findings") or [] if f.get("kind") == "anomaly"]
        open_ = sum(len([c for c in a["candidates"] if c["status"] == "consistent"]) for a in result.get("assessments") or [])
        return (f"{len(fs)} relation{'s' if len(fs) != 1 else ''} not adding up" + (f": {', '.join(fs)}" if fs else "")
                + (f"; {open_} mechanisms the data cannot separate" if open_ else ""))
    if name == "read_lines":
        return f"{len(result.get('lines') or [])} lines"
    return ""


# ---------------------------------------------------------------- verification
def verify(report: Dict[str, Any], raw: List[str], outputs: Dict[str, str]) -> Dict[str, Any]:
    """Return {problems, cleaned, dropped}. Quotes must match the original line; numbers must be in cited outputs or lines."""
    problems, dropped = [], []
    cleaned = json.loads(json.dumps(report))

    def line_ok(item, where):
        n, q = item.get("line"), _norm(item.get("quote"))
        if not isinstance(n, int) or not 1 <= n <= len(raw):
            return f"{where}: line {n} does not exist (the log has {len(raw)} lines)"
        if q and q.rstrip(".…") not in _norm(raw[n - 1]):
            return f"{where}: the quote is not in line {n} (it reads: {raw[n - 1][:160]!r})"
        return None

    def numbers_ok(text, calls, lines_, where):
        pool = " ".join(outputs.get(c, "") for c in calls or []) + " " + " ".join(raw[n - 1] for n in lines_ if isinstance(n, int) and 1 <= n <= len(raw))
        bad = [x for x in _NUM.findall(text or "") if x not in pool and x.lstrip("-") not in pool and not (len(x) <= 1)]
        return f"{where}: number(s) {', '.join(bad[:4])} not found in the cited tool results or lines" if bad else None

    for key in ("evidence", "effects"):
        keep = []
        for i, item in enumerate(cleaned.get(key) or []):
            err = line_ok(item, f"{key}[{i}]")
            if not err and key == "evidence":
                unknown = [c for c in item.get("calls") or [] if c not in outputs]
                if unknown:
                    err = f"{key}[{i}]: unknown tool call id(s) {unknown}"
            (problems.append(err) or dropped.append({key: item})) if err else keep.append(item)
        cleaned[key] = keep
    keep = []
    for i, item in enumerate(cleaned.get("checked") or []):
        calls = item.get("calls") or []
        err = None
        if not calls or any(c not in outputs for c in calls):
            err = f"checked[{i}]: must cite the tool call ids that ran it (got {calls})"
        else:
            err = numbers_ok(item.get("result"), calls, [], f"checked[{i}]")
        (problems.append(err) or dropped.append({"checked": item})) if err else keep.append(item)
    cleaned["checked"] = keep
    for h in cleaned.get("hypotheses") or []:
        if h.get("status") == "ruled_out" and h.get("basis") != "direct":
            problems.append(f"hypothesis '{h.get('area')}' is ruled_out without direct exclusion: mark it weakened (basis indirect) "
                            "or name the evidence that observes that area itself (basis direct, observed_at)")
        elif h.get("status") == "ruled_out" and h.get("excluded_by") != "data":
            problems.append(f"hypothesis '{h.get('area')}' is ruled_out on an engineer's report (or without saying): an unverified "
                            "report can weaken an area, not exclude it. Mark it weakened, or show the exclusion in the data (excluded_by data)")
    ids = {h.get("id") for h in cleaned.get("hypotheses") or []}
    for h in cleaned.get("hypotheses") or []:
        bad = [n for n in h.get("lines") or [] if not isinstance(n, int) or not 1 <= n <= len(raw)]
        if bad:
            problems.append(f"hypothesis {h.get('id')}: line(s) {bad} do not exist")
    for i, o in enumerate(((cleaned.get("next_action") or {}).get("outcomes")) or []):
        unknown = [x for x in (o.get("keeps") or []) + (o.get("rules_out") or []) + (o.get("weakens") or []) if x not in ids]
        if unknown:
            problems.append(f"next_action.outcomes[{i}]: unknown hypothesis id(s) {unknown}")
    problems += check_acquisition(cleaned, raw, outputs, ids)
    problems += check_physics(cleaned, outputs)
    ev = []
    for g in cleaned.get("events") or []:
        g["lines"] = [n for n in g.get("lines") or [] if isinstance(n, int) and 1 <= n <= len(raw)]
        if g["lines"]:
            ev.append(g)
    cleaned["events"] = ev
    text = " ".join(str(cleaned.get(k) or "") for k in ("headline", "explanation"))
    if _CONFIRMED.search(text):
        problems.append("Do not state a confirmed root cause; use cause_probable and say what would confirm it")
    if cleaned.get("status") == "cause_probable" and not cleaned.get("evidence"):
        problems.append("cause_probable needs evidence lines")
    reviewed_result(cleaned)  # whatever the model still left unproven is demoted before it is stored
    return {"problems": problems, "cleaned": cleaned, "dropped": dropped}


def check_acquisition(cleaned: Dict[str, Any], raw: List[str], outputs: Dict[str, str], ids: set) -> List[str]:
    """Evidence acquisition: what the data already answers must cite the line that answers it, and a question for a person
    is allowed only after the data was searched for it (searched_calls name real tool calls). Invalid items are dropped."""
    problems: List[str] = []
    keep = []
    for i, a in enumerate(cleaned.get("already_answered") or []):
        n, q = a.get("line"), _norm(a.get("quote"))
        err = None
        if not isinstance(n, int) or not 1 <= n <= len(raw):
            err = f"already_answered[{i}]: line {n} does not exist"
        elif q and q.rstrip(".…") not in _norm(raw[n - 1]):
            err = f"already_answered[{i}]: the quote is not in line {n} (it reads: {raw[n - 1][:160]!r})"
        elif any(c not in outputs for c in a.get("calls") or []):
            err = f"already_answered[{i}]: unknown tool call id(s)"
        else:
            pool = " ".join(outputs.get(c, "") for c in a.get("calls") or []) + " " + raw[n - 1]
            bad = [x for x in _NUM.findall(a.get("answer") or "") if len(x) > 1 and x not in pool and x.lstrip("-") not in pool]
            if bad:
                err = f"already_answered[{i}]: number(s) {', '.join(bad[:4])} not in line {n} or the cited results"
        (problems.append(err) if err else keep.append(a))
    cleaned["already_answered"] = keep
    keep = []
    for i, f in enumerate(cleaned.get("field_questions") or []):
        calls = f.get("searched_calls") or []
        err = None
        if not calls or any(c not in outputs for c in calls):
            err = (f"field_questions[{i}]: before asking a person, search the data for it and cite those tool call ids in "
                   f"searched_calls (got {calls})")
        else:
            unknown = [x for o in f.get("if_answers") or [] for x in (o.get("keeps") or []) + (o.get("weakens") or []) if x not in ids]
            if unknown:
                err = f"field_questions[{i}]: unknown hypothesis id(s) {unknown}"
        (problems.append(err) if err else keep.append(f))
    cleaned["field_questions"] = keep
    keep = []
    for i, d in enumerate(cleaned.get("data_requests") or []):
        calls = d.get("searched_calls") or []
        if not calls or any(c not in outputs for c in calls):
            problems.append(f"data_requests[{i}]: cite the tool call ids that show this data is not in the case (got {calls})")
        else:
            keep.append(d)
    cleaned["data_requests"] = keep
    return problems


def _strings(obj: Any) -> List[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [x for v in obj.values() for x in _strings(v)]
    if isinstance(obj, list):
        return [x for v in obj for x in _strings(v)]
    return []


def _calls(obj: Any) -> List[str]:
    if isinstance(obj, dict):
        return list(obj.get("calls") or []) + list(obj.get("searched_calls") or []) + [x for k, v in obj.items() if k not in ("calls", "searched_calls") for x in _calls(v)]
    if isinstance(obj, list):
        return [x for v in obj for x in _calls(v)]
    return []


_MEASURED_R = re.compile(r"\b(measured|identified|confirmed)\s+(\w+\s+){0,2}resistance|resistance\s+(measured|identified|confirmed)\b|resistance\s+(of\s+\S+\s+)?(is|was|has been)\s+(measured|identified|confirmed)|"
                         r"测得的?\S{0,4}电阻|电阻(已确认|已测得|已识别|为实测)", re.I)
_TORQUE = re.compile(r"torque|扭矩", re.I)


def check_physics(cleaned: Dict[str, Any], outputs: Dict[str, str], next_key: str = "next_action") -> List[str]:
    """When the electrical tool ran and found a branch that does not add up: the report cites it, does not call a
    model-derived resistance measured, keeps the mechanisms it cannot separate open, does not use torque to dismiss a
    current difference, keeps confidence below high while they are open, and surfaces the recommended measurement."""
    from app.physics.electrical import CAND_WORDS, MEAS_WORDS
    elec = {}
    for cid, text in outputs.items():
        if '"version": "electrical-relations-v2"' in text:
            try:
                elec[cid] = json.loads(text)
            except ValueError:
                continue
    problems: List[str] = []
    for cid, r in elec.items():
        assessments = r.get("assessments") or []
        if not assessments:
            continue
        if cid not in _calls(cleaned):
            problems.append(f"the electrical check ({cid}) found a branch that does not add up: cite it (its call id) in checked or evidence")
        text = " ".join(_strings({k: v for k, v in cleaned.items() if k not in ("events",)}))
        if _MEASURED_R.search(text):
            problems.append("a branch resistance here is model-derived (it assumes the branch's share of current, which is not recorded): "
                            "say 'if the current is shared equally' or give the range; do not call it measured, identified or confirmed")
        hyps = cleaned.get("hypotheses") or []
        for a in assessments:
            open_ = [c["id"] for c in a["candidates"] if c["status"] == "consistent"]
            for h in hyps:
                ht = f"{h.get('area', '')} {h.get('claim', '')}"
                hit = [c for c in open_ if c in CAND_WORDS and re.search(CAND_WORDS[c], ht, re.I) and (a["wheel"] in ht or len(assessments) == 1)]
                if not hit:
                    continue
                if h.get("status") == "ruled_out":
                    problems.append(f"hypothesis '{h.get('area')}' is ruled out, but the electrical check shows the recorded signals cannot separate "
                                    f"{', '.join(open_)} at {a['wheel']}: keep it open or weakened, and name the measurement that would separate them")
                if "inverter_draw" in hit and h.get("status") in ("weakened", "ruled_out") and _TORQUE.search(h.get("because") or ""):
                    problems.append(f"hypothesis '{h.get('area')}': similar torque does not show similar DC current (losses are not recorded); "
                                    "do not weaken an inverter drawing more current on torque alone")
                if h.get("status") == "leading" and len(open_) >= 2 and cleaned.get("confidence") == "high":
                    problems.append(f"confidence cannot be high: the electrical check leaves {len(open_)} mechanisms at {a['wheel']} that this data cannot separate")
            rec = a.get("recommended")
            if rec and rec in MEAS_WORDS:
                where = " ".join(_strings({k: cleaned.get(k) for k in (next_key, "unobservable", "data_requests", "field_questions")}))
                if not re.search(MEAS_WORDS[rec], where, re.I):
                    m = next((x for x in a.get("measurements") or [] if x["id"] == rec), {})
                    problems.append(f"the electrical check's lowest-cost discriminating measurement is: {m.get('what', rec)}. Use it as the next action "
                                    "(with what each candidate predicts), or list it under unobservable with how to observe it")
    return problems


# ---------------------------------------------------------------- evidence integrity (also applied to stored outputs on read)
INTEGRITY_VERSION = "evidence-integrity-v2"
_NOT_DIRECT = ("Kept open: no direct exclusion was recorded for this area (the deciding evidence was observed elsewhere), "
               "so it is weakened, not ruled out.")
_REPORT_ONLY = ("Kept open: the exclusion rests on an engineer's report that Veyra has not verified in the data, so it is "
                "weakened, not ruled out.")


def reviewed_hypotheses(hyps: Optional[List[Dict[str, Any]]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """A hypothesis is ruled out only on direct exclusion (basis 'direct'). Anything else, including outputs recorded before
    the basis existed, is shown as weakened, with why. Returns (copy, notes); the stored record is never changed."""
    out, notes = [], []
    for h in hyps or []:
        h = dict(h)
        if h.get("status") == "ruled_out" and h.get("basis") != "direct":
            h["status"], h["integrity"] = "weakened", _NOT_DIRECT
            notes.append(f"{h.get('area')}: ruled out without direct exclusion, shown as weakened")
        elif h.get("status") == "ruled_out" and h.get("excluded_by") != "data":
            h["status"], h["integrity"] = "weakened", _REPORT_ONLY
            notes.append(f"{h.get('area')}: ruled out without exclusion visible in the data, shown as weakened")
        out.append(h)
    return out, notes


def reviewed_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """In place: hypotheses and the experiment's branches. A branch 'rules out' only areas whose exclusion it would observe
    directly; for outputs without that distinction (recorded before it existed) every exclusion is shown as 'weakens'."""
    hyps, notes = reviewed_hypotheses(result.get("hypotheses"))
    if result.get("hypotheses") is not None:
        result["hypotheses"] = hyps
    for o in ((result.get("next_action") or {}).get("outcomes")) or []:
        if "weakens" not in o and o.get("rules_out"):  # recorded before direct / indirect was asked for
            o["weakens"], o["rules_out"] = list(o["rules_out"]), []
            notes.append(f"result '{o.get('label')}': exclusions shown as 'weakens' (not marked direct when recorded)")
    if notes:
        result["integrity"] = {"version": INTEGRITY_VERSION, "adjustments": notes}
    return result


def reviewed_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    out = json.loads(json.dumps(payload))
    if isinstance(out.get("result"), dict):
        reviewed_result(out["result"])
    return out


def finding_provenance(payload: Dict[str, Any]) -> List[Dict[str, str]]:
    """What an engineer's result passes to the next round, item by item and labelled: the outcome they selected, Veyra's own
    description of that outcome (proposed by Veyra, not observed: present only in results recorded before the two were kept
    apart), and each observation as written."""
    items: List[Dict[str, str]] = []
    obs = (payload.get("observations") or "").strip()
    if payload.get("outcome_selected"):
        items.append({"kind": "selected", "text": payload["outcome_selected"]})
    else:
        m = re.match(r"^(.+?) \((.+?)\)\.\s*(.*)$", obs, re.S)
        if m:
            items.append({"kind": "selected", "text": m.group(1).strip()})
            items.append({"kind": "veyra_description", "text": m.group(2).strip()})
            obs = m.group(3)
    for part in re.split(r"(?<=[.;])\s+", obs):
        part = part.strip().rstrip(".;").strip()
        if part:
            items.append({"kind": "observed", "text": part})
    return items


# ---------------------------------------------------------------- deterministic investigation (no model)
def deterministic(store, case_id: str, reason: Optional[str] = None) -> Dict[str, Any]:
    """The engine's own reading in the same shape, for when no model is configured. Labelled; never presented as AI."""
    tools = LogTools(store, case_id)
    q = store.questions(case_id)
    log = (store.discovery(case_id) or {}).get("log") or {}
    inc, restarts = log.get("incident"), log.get("restarts") or []
    raw = tools.raw

    def ev(n, shows):
        return {"line": n, "quote": raw[n - 1][:200] if raw and 1 <= n <= len(raw) else "", "shows": shows, "calls": []}

    evidence, effects, checked = [], [], []
    if restarts:
        r = restarts[-1]
        headline = f"The machine restarted (line {r['line']})" + (f"; the log states {r['primary_cause'].replace('_', ' ')}" if r["causes"] else "")
        for c in r["causes"][:2]:
            evidence += [ev(n, c["label"].split(":")[0]) for n in c["lines"][:1]]
    elif inc:
        headline = f"First failure at line {inc['line']}: {_norm(raw[inc['line'] - 1])[:90] if raw else inc['text'][:90]}"
        evidence = [ev(x["line"], f"{x.get('component') or 'warning'} right before the failure") for x in (inc.get("just_before") or [])[-3:]]
        effects = [{"line": x["line"], "quote": raw[x["line"] - 1][:200] if raw else x["text"], "why": "after the first failure"} for x in (inc.get("after") or [])[:4]]
    elif not raw:  # a recording, not a text log: the engine's own established facts
        est = q.get("established") or []
        headline = (est[0] if est else (run_summary(store, case_id) or "Veyra's engine reading of this recording"))[:160]
        for a in est[1:4]:
            checked.append({"check": "Engine finding", "result": a, "verdict": "supports", "calls": []})
    else:
        headline = "No failure, restart or stop message recognised in this log"
    for x in q.get("questions") or []:
        if x.get("status") == "answered" and x["id"].startswith("log:check"):
            checked.append({"check": x["question"], "result": x["answer"], "verdict": "supports", "calls": []})
    if raw and tools.electrical_applies():
        from app.physics import electrical as _el
        for f in (_el.analyse(raw) or {}).get("findings") or []:
            checked.append({"check": f"Electrical relation {f['relation']} ({'measured' if f['tier'] == 'measured' else 'model-derived'})",
                            "result": f["text"], "verdict": "supports" if f["kind"] == "anomaly" else "inconclusive", "calls": []})
    nxt = q.get("next") or {}
    events = []
    if raw:  # the rule-based grouping: warnings and errors by message, the rest is routine
        groups: Dict[str, List[int]] = {}
        for n, p in tools.parsed.items():
            if p.level in ("WARNING", "ERROR", "FATAL") or p.kind:
                groups.setdefault(p.template, []).append(n)
        for tpl, ns in sorted(groups.items(), key=lambda kv: kv[1][0])[:12]:
            p = tools.parsed[ns[0]]
            events.append({"lines": ns[:50], "what": raw[ns[0] - 1][:90], "class": "anomaly" if p.level in ("ERROR", "FATAL") or p.kind else "routine",
                           "why": f"{len(ns)} line{'s' if len(ns) > 1 else ''} with this message"})
    return {"events": events, "hypotheses": [], "headline": headline, "status": "cause_unknown" if (restarts or inc or not raw) else "no_failure_found", "explanation": "", "confidence": "low",
            "evidence": evidence[:4], "checked": checked, "effects": effects,
            "next_action": {"action": nxt.get("question") or "Capture the full log up to the next failure", "why": nxt.get("needs") or "",
                            "how": [], "where": "remote", "outcomes": [{"label": "Found it", "means": "Record what you found"},
                                                                      {"label": "Nothing found", "means": "Record what you checked"}]},
            "unknowns": [f"{reason or 'No model configured'}: this is Veyra's rule-based reading only, without an investigation."], "lesson": ""}


def run_summary(store, case_id: str) -> Optional[str]:
    run = store.latest_run(case_id) or {}
    rc = run.get("root_cause") or {}
    return rc.get("summary") if isinstance(rc, dict) else None


# ---------------------------------------------------------------- the agent loop
def model_call_record(n: int, reply: Dict[str, Any], ms: int, fallback_model: Optional[str]) -> Dict[str, Any]:
    """One model step, as billed: tokens by kind, cost if the backend reports it, wall-clock latency and the exact model."""
    u = reply.get("usage") or {}
    requested = [b.get("name") for b in (reply.get("content") or []) if isinstance(b, dict) and b.get("type") == "tool_use"]
    return {"call": n, "model_id": reply.get("model_id") or reply.get("model") or fallback_model,
            "input_tokens": int(u.get("input_tokens") or 0), "cache_read_input_tokens": int(u.get("cache_read_input_tokens") or 0),
            "cache_creation_input_tokens": int(u.get("cache_creation_input_tokens") or 0), "output_tokens": int(u.get("output_tokens") or 0),
            "cost_usd": reply.get("cost_usd"), "cost_basis": reply.get("cost_basis"), "ms": ms, "api_ms": reply.get("api_ms"),
            "requested": requested}


def usage_totals(calls: List[Dict[str, Any]]) -> Dict[str, Any]:
    costs = [c["cost_usd"] for c in calls if isinstance(c.get("cost_usd"), (int, float))]
    return {"uncached_input_tokens": sum(c["input_tokens"] for c in calls),
            "cache_read_input_tokens": sum(c["cache_read_input_tokens"] for c in calls),
            "cache_creation_input_tokens": sum(c["cache_creation_input_tokens"] for c in calls),
            "model_calls": len(calls), "model_ms": sum(c["ms"] for c in calls),
            "cost_usd": round(sum(costs), 6) if costs and len(costs) == len(calls) else None,
            "model_ids": sorted({c["model_id"] for c in calls if c.get("model_id")})}


LANGUAGE = {"zh": ("\n\nLanguage: write every text a person reads in simplified Chinese: headline, explanation, shows, check, result, why, "
                  "claim, area, because, basis, action, how, where, label, means, then, unknowns, events, lesson, reuse_when, reply, "
                  "still_unknown, and every other free-text field. No English sentences or English hypothesis names. Use plain words an "
                  "engineer uses at work, not academic. Keep technical terms, signal and message names, units, codes and numbers in "
                  "English as they appear in the data (VCU, BMS, DCL, MCU, EPB, CAN, HSA, TqCmd_Out, DTC 0x2A11, 55.0 V). Quotes stay "
                  "exactly as in the data. Ids and enum values stay as the schema defines them.")}


def prompt_for(base: str, lang: Optional[str]) -> str:
    return base + LANGUAGE.get(lang or "", ("",))[0]


# Repeated in the first message: an instruction at the end of a long system prompt alone is easy to drop.
LANGUAGE_REMINDER = {"zh": " 最终提交里给人看的每一段文字（包括 hypotheses 的 area 和 claim、claim、why、next_action、outcomes）都用简体中文写；"
                           "技术术语、信号名、单位、代码和数字保持英文原样。"}


def language_reminder(lang: Optional[str]) -> str:
    return LANGUAGE_REMINDER.get(lang or "", "")


def investigate(store, case_id: str, *, model=None, actor: str = "Veyra investigator", on_step: Callable[[Dict[str, Any]], None] = lambda s: None,
                max_turns: int = MAX_TURNS, lang: Optional[str] = None) -> Dict[str, Any]:
    from app.agent.runner import egress_allowed
    started = time.time()
    run = store.latest_run(case_id)
    if run is None:
        raise ValueError("Investigate the case first")
    tools = LogTools(store, case_id)
    steps: List[Dict[str, Any]] = []
    mode, usage, report, dropped = "deterministic", {"input_tokens": 0, "output_tokens": 0}, None, []
    model_calls: List[Dict[str, Any]] = []
    why_deterministic = None
    if model is None or not model.available():
        why_deterministic = "No model is configured on this instance"
    elif not tools.available():
        why_deterministic = "The original log is not stored for this case (uploaded before this version), or it is not a text log"
    elif not egress_allowed(store, case_id, model.provider):
        why_deterministic = f"No permission recorded to send this case's data to {model.provider}"
    if why_deterministic is None:
        mode = "agent"
        outputs: Dict[str, str] = {}
        messages: List[Dict[str, Any]] = [{"role": "user", "content": f"Investigate case {case_id}. Start with overview and case_context.{language_reminder(lang)}"}]
        attempts = 0
        try:
            for _ in range(max_turns):
                t_call = time.time()
                try:
                    reply = model.create(system=prompt_for(SYSTEM_PROMPT, lang), messages=messages, tools=tool_specs(tools) + [SUBMIT_SPEC])
                except Exception:  # noqa: BLE001  one retry: a transient CLI / network failure is not a verdict
                    time.sleep(2)
                    reply = model.create(system=prompt_for(SYSTEM_PROMPT, lang), messages=messages, tools=tool_specs(tools) + [SUBMIT_SPEC])
                call = model_call_record(len(model_calls) + 1, reply, int((time.time() - t_call) * 1000), getattr(model, "model", None))
                model_calls.append(call)
                usage["input_tokens"] += call["input_tokens"] + call["cache_read_input_tokens"]  # comparable with earlier records
                usage["output_tokens"] += call["output_tokens"]
                content = reply.get("content") or []
                messages.append({"role": "assistant", "content": content})
                calls = [b for b in content if b.get("type") == "tool_use"]
                if not calls:
                    messages.append({"role": "user", "content": "Continue: run the checks with the tools, then call submit_report."})
                    continue
                results = []
                for call in calls:
                    name, args = call["name"], dict(call.get("input") or {})
                    if name == "submit_report":
                        attempts += 1
                        checked = verify(args, tools.raw, outputs)
                        if checked["problems"] and attempts <= MAX_FIX_ATTEMPTS:
                            results.append({"type": "tool_result", "tool_use_id": call["id"], "is_error": True,
                                            "content": "Fix these and call submit_report again: " + "; ".join(checked["problems"][:8])})
                            continue
                        report, dropped = checked["cleaned"], checked["dropped"]
                        results.append({"type": "tool_result", "tool_use_id": call["id"], "content": "Accepted."})
                        continue
                    t0 = time.time()
                    try:
                        if name == "lessons":
                            value = (store.questions(case_id).get("lessons") or {"lessons": []})
                        elif name == "case_context":
                            value = _case_context(store, case_id)
                        elif name in ("overview", "read_lines", "search", "measure"):
                            value = getattr(tools, name)(**args)
                        elif name == "electrical" and tools.electrical_applies():
                            value = tools.electrical()
                        else:
                            raise ValueError(f"Unknown tool {name}")
                        text, error = json.dumps(value, default=str)[:24000], None
                    except Exception as exc:  # noqa: BLE001  a tool error goes back to the model
                        value, text, error = None, f"{type(exc).__name__}: {exc}", True
                    outputs[call["id"]] = text
                    step = {"id": call["id"], "tool": name, "input": args, "label": STEP_LABEL.get(name, name).format(**{"start": "", "end": "", "pattern": "", **args}),
                            "summary": "error: " + text[:120] if error else _summary(name, value), "ms": int((time.time() - t0) * 1000)}
                    steps.append(step)
                    on_step(step)
                    results.append({"type": "tool_result", "tool_use_id": call["id"], "content": text, **({"is_error": True} if error else {})})
                messages.append({"role": "user", "content": results})
                if report is not None:
                    break
            if report is None:
                mode, why_deterministic = "deterministic", "The agent did not finish within its step budget"
        except Exception as exc:  # noqa: BLE001  a model failure falls back, and says so
            mode, why_deterministic = "deterministic", f"The model step failed: {exc}"[:300]
    if mode == "deterministic":
        report = deterministic(store, case_id, why_deterministic)
    result = {"kind": "autonomous_investigation", "version": INVESTIGATOR_VERSION, "run_id": run["id"], "mode": mode,
              "model": getattr(model, "model", None) if mode == "agent" else None, "fallback_reason": why_deterministic,
              "steps": steps, "result": report, "dropped": dropped, "usage": {**usage, **usage_totals(model_calls)}, "model_calls": model_calls,
              "seconds": round(time.time() - started, 1),
              "boundary": "Read-only checks over the original data. No cause is confirmed until an engineer's result confirms it."}
    store.append_trusted(case_id, "agent_report", result, actor=actor, source=INVESTIGATOR_VERSION)
    return result


def latest(store, case_id: str) -> Optional[Dict[str, Any]]:
    for e in reversed(store.ledger(case_id)):
        if e["kind"] == "agent_report" and e["payload"].get("kind") == "autonomous_investigation":
            return {**reviewed_payload(e["payload"]), "entry_id": e["id"], "recorded_at": e["recorded_at"]}
    return None


# ---------------------------------------------------------------- closing: a drafted lesson
def draft_lesson(store, case_id: str) -> Dict[str, Any]:
    """A case_closure draft from what the case already holds; the engineer confirms or edits it."""
    inv = latest(store, case_id) or {}
    res = inv.get("result") or {}
    findings = [e for e in store.ledger(case_id) if e["kind"] == "engineer_finding"]
    last = findings[-1]["payload"] if findings else None
    confirmed = bool(last and last.get("result") == "found")
    newer = bool(last and inv.get("recorded_at") and inv["recorded_at"] > findings[-1]["recorded_at"])
    cause = (res.get("explanation") if (newer or not confirmed) else None) or (last.get("observations") if confirmed else None) or res.get("explanation") or ""
    return {"investigation_status": "cause_probable" if (confirmed or res.get("status") == "cause_probable") else "cause_unknown",
            "confirmed_by": "engineer_judgement",
            "cause": cause[:600],
            "evidence_note": "; ".join([f"line {e['line']}: {e['quote'][:100]}" for e in (res.get("evidence") or [])[:3]]
                                       + ([f"engineer: {last.get('action')} -> {last.get('result')}: {last.get('observations') or ''}"] if last else []))[:900],
            "decisive_check": (last or {}).get("action") or (res.get("next_action") or {}).get("action"),
            "applies_when": (res.get("reuse_when") or "")[:1000],
            "resolution_status": "not_yet_repaired",
            "basis": "Drafted by Veyra from this case's investigation and the engineer's results. Edit anything that is wrong, then confirm."}


# ---------------------------------------------------------------- the record, written by Veyra as the work happens
def _changes(before: Optional[List[Dict[str, Any]]], after: Optional[List[Dict[str, Any]]]) -> Dict[str, List[str]]:
    """How the fault space moved between two investigation rounds (by hypothesis id)."""
    b = {h["id"]: h for h in before or []}
    out: Dict[str, List[str]] = {"ruled_out": [], "leading": [], "weakened": [], "new": []}
    for h in after or []:
        old = b.get(h["id"])
        if old is None:
            if before is not None:
                out["new"].append(h["area"])
        elif old.get("status") != h.get("status") and h.get("status") in ("ruled_out", "leading", "weakened"):
            out[h["status"]].append(h["area"])
    return out


def record(store, case_id: str) -> Dict[str, Any]:
    """The case record Veyra keeps itself: what was reported, every investigation round and how it changed the fault space,
    every action the engineer took and its result, the conclusion and whether the machine recovered. Built from the
    append-only ledger; nobody writes it by hand."""
    case = store.case(case_id)
    led = store.ledger(case_id)
    chain, prev_h, rounds, checks, actions = [], None, 0, 0, 0
    first_inv_at = None
    for e in led:
        p, k = e["payload"], e["kind"]
        if k == "intervention_report":
            chain.append({"at": e["recorded_at"], "type": "reported", "by": e["actor"], "title": "Problem reported", "detail": p.get("stated_reason")})
        elif k == "agent_report" and p.get("kind") == "autonomous_investigation" and p.get("mode") == "agent":
            rounds += 1
            checks += len(p.get("steps") or [])
            first_inv_at = first_inv_at or e["recorded_at"]
            r = p.get("result") or {}
            hyps = reviewed_hypotheses(r.get("hypotheses"))[0] if r.get("hypotheses") else r.get("hypotheses")
            lead = [h["area"] for h in hyps or [] if h.get("status") == "leading"]
            chain.append({"at": e["recorded_at"], "type": "investigation", "by": "Veyra", "title": f"Investigation round {rounds}",
                          "detail": r.get("headline"), "checks": len(p.get("steps") or []), "seconds": p.get("seconds"),
                          "leading": lead, "standing": [h["area"] for h in hyps or [] if h.get("status") in ("leading", "open")],
                          "changes": _changes(prev_h, hyps) if prev_h is not None else None,
                          "next_action": (r.get("next_action") or {}).get("action"), "status": r.get("status")})
            prev_h = hyps if hyps else prev_h
        elif k == "engineer_finding":
            actions += 1
            label = {"found": "found", "not_found": "nothing found", "cannot_do": "could not do it", "other": "other result"}[p["result"]]
            chain.append({"at": e["recorded_at"], "type": "action", "by": e["actor"], "title": p["action"], "detail": f"{label}{': ' + p['observations'] if p.get('observations') else ''}"})
        elif k == "event_note":
            chain.append({"at": e["recorded_at"], "type": "note", "by": e["actor"],
                          "title": f"Marked {'normal' if p['verdict'] == 'normal' else 'relevant'}{' for this machine' if p['scope'] == 'machine' else ''}", "detail": p["pattern"]})
        elif k == "case_closure":
            chain.append({"at": e["recorded_at"], "type": "closure", "by": e["actor"], "title": "Closed after review",
                          "detail": p.get("cause") or "Closed without a cause", "facts": closure_facts(p),
                          "recovered": p.get("resolution_status"), "status": p.get("investigation_status")})
    last = latest(store, case_id) or {}
    res = last.get("result") or {}
    created = case.get("created_at")
    t_first = None
    try:
        from datetime import datetime
        if created and first_inv_at:
            t_first = round((datetime.fromisoformat(first_inv_at) - datetime.fromisoformat(created)).total_seconds(), 1)
    except ValueError:
        pass
    closure = next((e for e in reversed(led) if e["kind"] == "case_closure"), None)
    return {"case_id": case_id, "machine_id": case["machine_id"], "title": case["title"],
            "summary": {"headline": res.get("headline"), "explanation": res.get("explanation"), "status": (closure or {}).get("payload", {}).get("investigation_status") or res.get("status"),
                        "rounds": rounds, "checks_run": checks, "engineer_actions": actions, "seconds_to_first_answer": t_first,
                        "closed": closure is not None, "recovered": (closure or {}).get("payload", {}).get("resolution_status")},
            "chain": chain, "integrity": store.verify(case_id)["ledger_chain"]["valid"],
            "metrics": case_metrics(store, case_id), "ladder": verification_ladder(store, case_id),
            "boundary": "Written by Veyra from the case's append-only record as the investigation happened."}


LADDER = [
    {"level": "probable", "label": "Probable cause", "needs": "An investigation that points to one explanation, with the main alternatives weakened or ruled out by the data."},
    {"level": "reproduced", "label": "Reproduced", "needs": "The symptom reproduced on purpose under stated conditions (bench, field or simulation), recorded by an engineer."},
    {"level": "verified", "label": "Verified", "needs": "Confirmed by inspection, by reproduction, or by data showing the fix removed the fault and it held."},
]


def verification_ladder(store, case_id: str) -> Dict[str, Any]:
    """Where the case stands on the way from a probable cause to a verified one, from the append-only record. Veyra can
    only reach 'probable' on its own; 'reproduced' and 'verified' need records an engineer writes (a reproduction attempt,
    a closure confirmed by inspection, reproduction or a fix that held)."""
    led = store.ledger(case_id)
    case = store.case(case_id)
    reached: Dict[str, Dict[str, Any]] = {}
    agent = [e for e in led if e["kind"] == "agent_report" and e["payload"].get("kind") == "autonomous_investigation" and e["payload"].get("mode") == "agent"]
    last = agent[-1] if agent else None
    if last and (last["payload"].get("result") or {}).get("status") == "cause_probable":
        reached["probable"] = {"at": last["recorded_at"], "by": "Veyra", "evidence": (last["payload"].get("result") or {}).get("headline")}
    for e in led:
        p = e["payload"]
        if e["kind"] == "reproduction_attempt" and p.get("result") == "reproduced":
            reached["reproduced"] = {"at": e["recorded_at"], "by": e["actor"], "evidence": f"{p.get('environment')}: {p.get('setup')} -> {p.get('observations')}"}
        if e["kind"] == "case_closure":
            if p.get("investigation_status") in ("cause_probable", "cause_confirmed") and "probable" not in reached:
                reached["probable"] = {"at": e["recorded_at"], "by": e["actor"], "evidence": p.get("cause")}
            if p.get("investigation_status") == "cause_confirmed" or p.get("fix_held") == "held":
                reached["verified"] = {"at": e["recorded_at"], "by": e["actor"],
                                       "evidence": p.get("evidence_note") or p.get("cause") or "", "method": p.get("confirmed_by") or ("fix held" if p.get("fix_held") == "held" else None)}
    level = next((r["level"] for r in reversed(LADDER) if r["level"] in reached), "open")
    nxt = next((r for r in LADDER if r["level"] not in reached), None)
    run = store.latest_run(case_id) or {}
    report = next((e["payload"].get("stated_reason") for e in led if e["kind"] == "intervention_report"), None)
    return {"level": level, "rungs": [{**r, "reached": reached.get(r["level"])} for r in LADDER],
            "next": nxt, "run_id": run.get("id"),
            "case": {"title": case["title"], "machine_id": case["machine_id"], "created_at": case.get("created_at"), "reported": report,
                     "synthetic": bool(case.get("synthetic"))}}


def case_metrics(store, case_id: str) -> Dict[str, Any]:
    """What this case cost and what Veyra saved, counted from the record: how fast the first answer came, how many
    checks Veyra ran, how many questions it answered from the data instead of asking someone, how many it asked."""
    from app.agent import discussion
    led = store.ledger(case_id)
    case = store.case(case_id)
    results = [e["payload"].get("result") or {} for e in led if e["kind"] == "agent_report"
               and e["payload"].get("kind") == "autonomous_investigation" and e["payload"].get("mode") == "agent"]
    steps = sum(len(e["payload"].get("steps") or []) for e in led if e["kind"] == "agent_report" and e["payload"].get("mode") == "agent")
    rounds = discussion.rounds_of(store, case_id)
    allr = results + [r.get("result") or {} for r in rounds]
    first = next((e["recorded_at"] for e in led if e["kind"] == "agent_report" and e["payload"].get("mode") == "agent"), None)
    secs = None
    try:
        from datetime import datetime
        if first and case.get("created_at"):
            secs = round((datetime.fromisoformat(first) - datetime.fromisoformat(case["created_at"])).total_seconds(), 1)
    except ValueError:
        pass
    model_s = sum(float(e["payload"].get("seconds") or 0) for e in led if e["kind"] == "agent_report" and e["payload"].get("mode") == "agent") + \
        sum(float(r.get("seconds") or 0) for r in rounds)
    return {"seconds_to_first_answer": secs, "investigation_seconds": round(model_s, 1), "checks_run": steps,
            "investigation_rounds": len(results), "discussion_rounds": len(rounds),
            "answered_from_data": sum(len(r.get("already_answered") or []) for r in allr),
            "questions_for_people": sum(len(r.get("field_questions") or []) for r in allr),
            "data_requests": sum(len(r.get("data_requests") or []) for r in allr),
            "engineer_results": sum(1 for e in led if e["kind"] == "engineer_finding"),
            "reproductions": sum(1 for e in led if e["kind"] == "reproduction_attempt"),
            "verification": verification_ladder(store, case_id)["level"],
            "note": "Counted from the case record. Engineer time outside Veyra is not measured here."}


ACCEPTANCE = {"correct": "accepted", "partly": "partly", "wrong": "rejected"}


def closure_facts(p: Dict[str, Any]) -> Dict[str, str]:
    """The four facts a closure carries, kept apart: what the engineer accepted, what they observed after the fix,
    whether the root cause was verified (inspection, reproduction or a confirming record, never a click), and whether
    the machine is back in service."""
    verified = p.get("investigation_status") == "cause_confirmed"
    return {"engineer_acceptance": {"accepted": "Engineer accepted the conclusion", "partly": "Engineer accepted it in part, with a correction",
                                    "rejected": "Engineer rejected the conclusion"}.get(p.get("engineer_acceptance") or "", "Not recorded"),
            "symptom_after_fix": {"gone": "Symptom not seen after the fix (engineer's observation)", "still_present": "Symptom still present after the fix",
                                  "unknown": "Not observed yet whether the symptom is gone", "no_fix": "No fix applied yet"}.get(p.get("symptom_after_fix") or "", "Not recorded"),
            "root_cause": (f"Verified ({p.get('confirmed_by', '').replace('_', ' ')})" if verified else
                           "Not verified: probable" if p.get("investigation_status") == "cause_probable" else "Not established"),
            "machine": {"resolved": "Back in service", "temporary_workaround": "Running on a workaround", "not_yet_repaired": "Not restored yet",
                        "not_resolved": "Not restored", "machine_retired": "Retired", "handed_over": "Handed over"}.get(p.get("resolution_status") or "", "Not recorded")}


def review_to_closure(store, case_id: str, *, conclusion: str, symptom_after_fix: str, machine_restored: str,
                      correction: Optional[str] = None, fix: Optional[str] = None) -> Dict[str, Any]:
    """Review & close. The engineer's answers are recorded as what they are: acceptance of the conclusion, an observation
    of the symptom after the fix, and whether the machine is back in service. None of them verifies the root cause: the
    closure stays 'cause_probable' (engineer judgement) whatever is clicked, so its lesson is a candidate, not verified.
    Verification needs its own record (inspection, reproduction or a fix confirmed by evidence) through the case ledger.
    conclusion: correct | partly | wrong; symptom_after_fix: gone | still_present | unknown | no_fix;
    machine_restored: yes | no | unknown."""
    d = draft_lesson(store, case_id)
    run = store.latest_run(case_id) or {}
    domains = run.get("domains") or {}
    domain = next((k for k, v in domains.items() if v.get("status") == "anomaly_observed"), next(iter(domains), None))
    cause = d["cause"] if conclusion == "correct" else (correction or "").strip()
    status = "cause_unknown" if (conclusion == "wrong" and not cause) or not cause else "cause_probable"
    fix_text = (fix or "").strip() or None
    payload = {"investigation_status": status, "engineer_acceptance": ACCEPTANCE[conclusion],
               "symptom_after_fix": symptom_after_fix if fix_text or symptom_after_fix == "no_fix" else ("no_fix" if symptom_after_fix == "unknown" else symptom_after_fix),
               "evidence_note": d["evidence_note"][:1000] or None,
               "resolution_status": {"yes": "resolved", "no": "not_yet_repaired", "unknown": "not_yet_repaired"}[machine_restored],
               "decisive_check": (d.get("decisive_check") or "")[:200] or None, "applies_when": d.get("applies_when") or None, "fix": fix_text}
    if status != "cause_unknown":
        payload.update(cause=cause[:1000], cause_domain=domain, confirmed_by="engineer_judgement")
    if fix_text:
        payload["fix_held"] = "not_yet_known"  # one observation after the fix is not a fix that held
    return payload
