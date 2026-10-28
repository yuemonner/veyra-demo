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
from typing import Any, Callable, Dict, List, Optional

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
   line(s) or result that decided it.
3. Separate cause from consequence: a crash, a restart, a watchdog, an e-stop or a timeout is often the effect of
   something earlier. Say which lines are the earliest sign and which are effects.
4. Only what the data cannot answer becomes the next action for a person: design it as an experiment that splits the
   hypotheses still standing. One concrete action a field engineer can do (a command to run, a value to read, a part to
   inspect, a config to check). For each possible result: which hypotheses it keeps, which it rules out, and what Veyra
   would do next. Never propose an action you could have done yourself with the tools.
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
- An event the engineer marked as normal for this machine (case_context.marked_normal) is routine here.
- In all text a person reads, name hypotheses by their area ("Network path"), never by id; ids go only in keeps / rules_out.
- Fill reuse_when: the conditions under which your explanation, once confirmed, would apply to another machine.
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
                "outcomes": {"type": "array", "minItems": 2, "maxItems": 3, "items": {"type": "object", "properties": {
                    "label": {"type": "string", "description": "What the engineer would find, under 8 words"},
                    "means": {"type": "string", "description": "What that would mean, under 20 words"},
                    "keeps": {"type": "array", "items": {"type": "string"}, "description": "Hypothesis ids still standing after this result"},
                    "rules_out": {"type": "array", "items": {"type": "string"}, "description": "Hypothesis ids this result rules out"},
                    "then": {"type": "string", "description": "What Veyra would do next after this result, under 12 words"}},
                    "required": ["label", "means", "keeps", "rules_out"]}}},
                "required": ["action", "why", "how", "where", "outcomes"]},
            "unknowns": {"type": "array", "maxItems": 3, "items": {"type": "string"}},
            "hypotheses": {"type": "array", "minItems": 2, "maxItems": 6, "items": {"type": "object", "properties": {
                "id": {"type": "string", "description": "h1, h2... keep ids from earlier rounds"},
                "area": {"type": "string", "description": "Where the fault would be, 2-5 words"},
                "claim": {"type": "string", "description": "The hypothesis, under 20 words"},
                "status": {"type": "string", "enum": ["leading", "open", "weakened", "ruled_out"]},
                "because": {"type": "string", "description": "What decided this status, under 25 words"},
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

STEP_LABEL = {"overview": "Read the whole log", "read_lines": "Read lines {start}–{end}", "search": "Searched for “{pattern}”",
              "measure": "Measured “{pattern}”", "lessons": "Compared with earlier cases", "case_context": "Read the report and earlier results"}


def _case_context(store, case_id: str) -> Dict[str, Any]:
    case = store.case(case_id)
    led = store.ledger(case_id)
    reports = [e["payload"].get("stated_reason") for e in led if e["kind"] == "intervention_report"]
    findings = [{"recorded_at": e["recorded_at"], "by": e["actor"], **{k: e["payload"].get(k) for k in ("action", "result", "observations")}}
                for e in led if e["kind"] == "engineer_finding"]
    prev = next((e["payload"]["result"].get("hypotheses") for e in reversed(led) if e["kind"] == "agent_report"
                 and e["payload"].get("kind") == "autonomous_investigation" and e["payload"].get("mode") == "agent"), None)
    notes = {}
    for e in store.ledger(case_id) + _machine_notes(store, case["machine_id"], case_id):
        if e["kind"] == "event_note":
            notes[e["payload"]["pattern"]] = e["payload"]
    return {"machine": case["machine_id"], "title": case["title"], "reported": reports, "engineer_results": findings,
            "previous_hypotheses": prev, "marked_normal": [n for n in notes.values() if n["verdict"] == "normal"]}


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
    ids = {h.get("id") for h in cleaned.get("hypotheses") or []}
    for h in cleaned.get("hypotheses") or []:
        bad = [n for n in h.get("lines") or [] if not isinstance(n, int) or not 1 <= n <= len(raw)]
        if bad:
            problems.append(f"hypothesis {h.get('id')}: line(s) {bad} do not exist")
    for i, o in enumerate(((cleaned.get("next_action") or {}).get("outcomes")) or []):
        unknown = [x for x in (o.get("keeps") or []) + (o.get("rules_out") or []) if x not in ids]
        if unknown:
            problems.append(f"next_action.outcomes[{i}]: unknown hypothesis id(s) {unknown}")
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
    return {"problems": problems, "cleaned": cleaned, "dropped": dropped}


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
def investigate(store, case_id: str, *, model=None, actor: str = "Veyra investigator", on_step: Callable[[Dict[str, Any]], None] = lambda s: None,
                max_turns: int = MAX_TURNS) -> Dict[str, Any]:
    from app.agent.runner import egress_allowed
    started = time.time()
    run = store.latest_run(case_id)
    if run is None:
        raise ValueError("Investigate the case first")
    tools = LogTools(store, case_id)
    steps: List[Dict[str, Any]] = []
    mode, usage, report, dropped = "deterministic", {"input_tokens": 0, "output_tokens": 0}, None, []
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
        messages: List[Dict[str, Any]] = [{"role": "user", "content": f"Investigate case {case_id}. Start with overview and case_context."}]
        attempts = 0
        try:
            for _ in range(max_turns):
                try:
                    reply = model.create(system=SYSTEM_PROMPT, messages=messages, tools=TOOL_SPECS + [SUBMIT_SPEC])
                except Exception:  # noqa: BLE001  one retry: a transient CLI / network failure is not a verdict
                    time.sleep(2)
                    reply = model.create(system=SYSTEM_PROMPT, messages=messages, tools=TOOL_SPECS + [SUBMIT_SPEC])
                for k in usage:
                    usage[k] += int((reply.get("usage") or {}).get(k) or 0)
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
              "steps": steps, "result": report, "dropped": dropped, "usage": usage, "seconds": round(time.time() - started, 1),
              "boundary": "Read-only checks over the original data. No cause is confirmed until an engineer's result confirms it."}
    store.append_trusted(case_id, "agent_report", result, actor=actor, source=INVESTIGATOR_VERSION)
    return result


def latest(store, case_id: str) -> Optional[Dict[str, Any]]:
    for e in reversed(store.ledger(case_id)):
        if e["kind"] == "agent_report" and e["payload"].get("kind") == "autonomous_investigation":
            return {**e["payload"], "entry_id": e["id"], "recorded_at": e["recorded_at"]}
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
            hyps = r.get("hypotheses")
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
            chain.append({"at": e["recorded_at"], "type": "closure", "by": e["actor"], "title": "Conclusion confirmed",
                          "detail": p.get("cause") or "Closed without a cause", "recovered": p.get("resolution_status"), "status": p.get("investigation_status")})
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
            "boundary": "Written by Veyra from the case's append-only record as the investigation happened."}


def review_to_closure(store, case_id: str, *, conclusion: str, recovered: str, correction: Optional[str] = None,
                      fix: Optional[str] = None) -> Dict[str, Any]:
    """Review & confirm: the engineer answers two questions; Veyra writes the closure from what the case already holds.
    conclusion: correct | partly | wrong; recovered: yes | not_yet | unknown."""
    d = draft_lesson(store, case_id)
    run = store.latest_run(case_id) or {}
    domains = run.get("domains") or {}
    domain = next((k for k, v in domains.items() if v.get("status") == "anomaly_observed"), next(iter(domains), None))
    cause = d["cause"] if conclusion == "correct" else (correction or "").strip()
    status = "cause_unknown" if conclusion == "wrong" and not cause else "cause_probable"
    fix_text = (fix or "").strip() or None
    confirmed = conclusion == "correct" and recovered == "yes" and fix_text
    payload = {"investigation_status": "cause_confirmed" if confirmed else status,
               "evidence_note": (d["evidence_note"] + ("; engineer: the machine recovered after the fix" if recovered == "yes" else ""))[:1000] or None,
               "resolution_status": {"yes": "resolved", "not_yet": "not_yet_repaired", "unknown": "not_yet_repaired"}[recovered],
               "decisive_check": (d.get("decisive_check") or "")[:200] or None, "applies_when": d.get("applies_when") or None, "fix": fix_text}
    if payload["investigation_status"] != "cause_unknown":
        payload.update(cause=cause[:1000], cause_domain=domain, confirmed_by="fix_resolved_it" if confirmed else "engineer_judgement")
    if fix_text:
        payload["fix_held"] = "held" if recovered == "yes" else "not_yet_known"
    return payload
