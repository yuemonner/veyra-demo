"""Read-only tools the investigation agent may call. Each one runs on the case's stored evidence and
latest engine run; none can write to the ledger, change machine context or touch a machine.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.analysis import screening
from app.compute import methods as compute_methods
from app.compute import sandbox
from app.compute.data import CaseData, MAX_SERIES

MAX_RESULT_CHARS = 14_000
MAX_SAMPLES = 120


def _iso(t: Optional[float]) -> Optional[str]:
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z") if t is not None else None


def _t(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


SERIES_PROPS = {"case_id": {"type": "string", "description": "Default: this case"}, "signal": {"type": "string"}, "scope": {"type": "string"},
                "window": {"type": "string", "enum": ["all", "symptom", "reference", "custom"], "description": "Default: all"},
                "start": {"type": "string"}, "end": {"type": "string"}}
SERIES_SCHEMA = {"type": "object", "properties": SERIES_PROPS, "required": ["signal"], "additionalProperties": False}
COMPUTE_TOOLS = {"query_signal", "run_method", "run_python", "signal_catalog", "find_cases"}

TOOL_SPECS: List[Dict[str, Any]] = [
    {"name": "case_overview", "description": "Case, machine, symptom window, operator interventions, the reference used, per-domain evidence status and missing information. Call this first.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "list_signals", "description": "Every recorded signal with clock, sample count, time span and samples inside the symptom window.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "signal_stats", "description": "Statistics (count, min, median, p95, max) of one canonical signal in a window: 'symptom', 'reference' or 'custom' with start/end ISO times.",
     "input_schema": {"type": "object", "properties": {"signal": {"type": "string"}, "scope": {"type": "string"}, "window": {"type": "string", "enum": ["symptom", "reference", "custom"]},
                                                       "start": {"type": "string"}, "end": {"type": "string"}}, "required": ["signal", "window"], "additionalProperties": False}},
    {"name": "signal_samples", "description": f"Up to {MAX_SAMPLES} raw samples (evenly thinned) of one signal between start and end, each with its source row.",
     "input_schema": {"type": "object", "properties": {"signal": {"type": "string"}, "scope": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"}},
                      "required": ["signal", "start", "end"], "additionalProperties": False}},
    {"name": "compare_to_reference", "description": "Per-signal comparison of the symptom window against the reference window, with the rule used.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "brief_events", "description": "Sub-second events found by scanning every high-rate sample, with exact times and source rows.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "actuator_outputs", "description": "Actuator outputs that froze or split from their twin, with source rows; holds suppressed because the machine's known-good runs show them as normal.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "command_tracking", "description": "Did the machine follow its commands? Tracking error (commanded vs measured) per axis and command size, against known-good runs or the reference, with the time it departed and source rows.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "command_chain", "description": "Command stage latencies inside vs outside the symptom window, per segment, with clock assumptions.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "input_response", "description": "Vehicle-side input→response comparison against the baseline window, per channel.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "events_near_window", "description": "Controller resets, fault codes, link events and heartbeat gaps near the symptom window, as recorded.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "next_checks", "description": "Engine-ranked next checks with the rule values that placed them.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "next_action_plan", "description": "Veyra's rule-based plan: which explanations are supported, open or less likely (with why), and every candidate check compared on whether it could change the decision, what it informs, access and disruption. Read it before proposing checks; if you rank differently, say why.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "machine_history", "description": "Earlier cases on the same machine and recurrence notes.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "find_cases", "description": "Other cases in this workspace (same machine, same machine type, or that recorded a signal), with their symptom windows and how they were closed. Use before comparing runs.",
     "input_schema": {"type": "object", "properties": {"machine_id": {"type": "string"}, "machine_class": {"type": "string"}, "signal": {"type": "string"}}, "additionalProperties": False}},
    {"name": "signal_catalog", "description": "Every signal recorded in a case (this one by default): unit (and the unit it was converted from), clock, declared maximum clock offset, rate, count and time span.",
     "input_schema": {"type": "object", "properties": {"case_id": {"type": "string"}}, "additionalProperties": False}},
    {"name": "query_signal", "description": f"Readings of one signal in any case of this workspace and window, with statistics and up to {MAX_SAMPLES} evenly thinned samples, each with its source row.",
     "input_schema": {"type": "object", "properties": {**SERIES_PROPS}, "required": ["signal"], "additionalProperties": False}},
    {"name": "list_methods", "description": "Built-in analysis methods (onset, onset_order, lag, compare_runs) and the team's saved methods, with what each needs.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "run_method", "description": "Run a built-in or team method on named input series. Built-ins state their rule and uncertainty; onset_order answers 'cannot_tell' when clocks or sampling don't allow an order. compare_runs takes one input per case.",
     "input_schema": {"type": "object", "required": ["method", "inputs"], "additionalProperties": False, "properties": {
         "method": {"type": "string"}, "inputs": {"type": "object", "description": "name -> series (onset: a [+ a_minus]; onset_order: a, b [+ a_minus, b_minus]; lag: a and b; compare_runs: any names)", "additionalProperties": SERIES_SCHEMA},
         "params": {"type": "object"}}}},
    {"name": "run_python", "description": "Run new Python analysis on named input series in an isolated process (no network, no file access, time and memory limits). In the code: series[name].t (UTC seconds), .v (values; numpy arrays when numeric), .unit, .clock, .sources; np (numpy), pd (pandas, if installed), math, statistics, params. Set `result` to a JSON-able value. Up to 8 series. State the assumptions (alignment, units, thresholds) in result.",
     "input_schema": {"type": "object", "required": ["code", "inputs"], "additionalProperties": False, "properties": {
         "code": {"type": "string"}, "inputs": {"type": "object", "additionalProperties": SERIES_SCHEMA}, "params": {"type": "object"}}}},
    {"name": "submit_report", "description": "Finish. Every observation, hypothesis and proposed check must cite the ids of tool calls whose results support it.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["summary", "observations", "hypotheses", "proposed_checks", "questions_for_engineers", "uncertainty"],
                      "properties": {
                          "summary": {"type": "string", "description": "3–5 plain sentences for the engineer on call. No confirmed root cause."},
                          "observations": {"type": "array", "items": {"type": "object", "required": ["text", "evidence"], "properties": {"text": {"type": "string"}, "evidence": {"type": "array", "items": {"type": "string"}}}}},
                          "hypotheses": {"type": "array", "items": {"type": "object", "required": ["statement", "status", "support"], "properties": {
                              "statement": {"type": "string"}, "domain": {"type": "string", "enum": ["network", "control_suite", "vehicle_interface", "vehicle_body", "other"]},
                              "status": {"type": "string", "enum": ["leading", "open", "weakened"]},
                              "support": {"type": "array", "items": {"type": "string"}}, "against": {"type": "array", "items": {"type": "string"}}}}},
                          "proposed_checks": {"type": "array", "items": {"type": "object", "required": ["action", "why", "evidence"], "properties": {
                              "check_id": {"type": "string"}, "action": {"type": "string"}, "why": {"type": "string"}, "evidence": {"type": "array", "items": {"type": "string"}}}}},
                          "questions_for_engineers": {"type": "array", "items": {"type": "string"}},
                          "uncertainty": {"type": "string", "description": "What is unknown or unmeasured and why it matters."}}}},
]


class Toolbox:
    def __init__(self, store, case_id: str, *, may_share: Optional[Callable[[str], bool]] = None):
        self.store = store
        self.case = store.case(case_id)
        self.run = store.latest_run(case_id)
        self._rows: Optional[List[Dict[str, Any]]] = None
        self.samples_shared = 0
        self.data = CaseData(store)
        self.may_share = may_share or (lambda cid: cid == case_id)
        self.cases_used = {case_id}
        self.computations: List[Dict[str, Any]] = []

    def rows(self) -> List[Dict[str, Any]]:
        if self._rows is None:
            self._rows = [r for i in self.store.imports(self.case["id"]) if i["id"] in self.run["inputs"] for r in i["rows"]]
        return self._rows

    def _window(self, which: str, start: Optional[str], end: Optional[str]) -> Tuple[float, float]:
        if which == "symptom":
            if not self.run["symptom_window"]:
                raise ValueError("This case has no symptom window")
            return _t(self.run["symptom_window"][0]), _t(self.run["symptom_window"][1])
        if which == "reference":
            ref = self.run.get("reference") or {}
            if not ref.get("window_iso"):
                raise ValueError("No reference window is available")
            return _t(ref["window_iso"][0]), _t(ref["window_iso"][1])
        if not start or not end:
            raise ValueError("A custom window needs start and end")
        return _t(start), _t(end)

    def call(self, name: str, args: Dict[str, Any]) -> Any:
        handler: Optional[Callable[..., Any]] = getattr(self, "tool_" + name, None)
        if handler is None:
            raise ValueError(f"Unknown tool {name}")
        return handler(**args)

    # ---- tools ----
    def tool_case_overview(self) -> Dict[str, Any]:
        run = self.run
        profile = run.get("machine_profile") or {}
        return {"case_id": self.case["id"], "title": self.case["title"], "machine_id": self.case["machine_id"], "synthetic": bool(self.case["synthetic"]),
                "machine_type": {"name": profile.get("name"), "basis": profile.get("basis"),
                                 "domains": {r: f"{d['label']} (owner: {d['owner']})" for r, d in (profile.get("domains") or {}).items()}},
                "symptom_window": run["symptom_window"], "symptom_source": self.case["symptom_source"],
                "interventions": [{k: i.get(k) for k in ("intervention_type", "occurred_at", "operator", "stated_reason", "review")} for i in run.get("interventions", [])],
                "reference": {k: (run.get("reference") or {}).get(k) for k in ("status", "window_iso", "basis", "evidence_grade")},
                "domains": {d: {"status": a["status"], "observations": [o["text"] for o in a["observations"]][:8]} for d, a in run["domains"].items()},
                "human_verdicts": [{"domain": b["domain"], "verdict": (b.get("human") or {}).get("status"), "scope": (b.get("human") or {}).get("scope")} for b in run.get("hypotheses", []) if b.get("human")],
                "missing_information": run["missing_information"][:12], "root_cause": run["root_cause"]}

    def tool_list_signals(self) -> List[Dict[str, Any]]:
        return [{"signal": c["signal"], "clock": c["clock_domain"], "count": c["count"], "start": _iso(c["start"]), "end": _iso(c["end"]),
                 "rows_in_symptom_window": c["rows_in_window"]} for c in self.run["findings"]["coverage"] or []]

    def tool_signal_stats(self, signal: str, window: str, scope: Optional[str] = None, start: Optional[str] = None, end: Optional[str] = None) -> List[Dict[str, Any]]:
        lo, hi = self._window(window, start, end)
        groups: Dict[Tuple[Any, Any, Any], List[float]] = {}
        for r in self.rows():
            if r.get("signal") == signal and (scope is None or r.get("scope") == scope) and isinstance(r.get("value"), (int, float)) and lo <= r["t"] <= hi:
                groups.setdefault((r.get("scope"), r.get("clock_domain"), r.get("unit")), []).append(r["value"])
        if not groups:
            return [{"signal": signal, "window": [_iso(lo), _iso(hi)], "count": 0, "note": "No numeric samples in this window"}]
        return [{"signal": signal, "scope": s, "clock": c, "unit": u, "window": [_iso(lo), _iso(hi)], **screening._stats(v)} for (s, c, u), v in groups.items()]

    def tool_signal_samples(self, signal: str, start: str, end: str, scope: Optional[str] = None) -> Dict[str, Any]:
        lo, hi = _t(start), _t(end)
        picked = [r for r in self.rows() if r.get("signal") == signal and (scope is None or r.get("scope") == scope) and lo <= r["t"] <= hi]
        picked.sort(key=lambda r: r["t"])
        step = max(1, len(picked) // MAX_SAMPLES)
        thinned = picked[::step][:MAX_SAMPLES]
        self.samples_shared += len(thinned)
        return {"signal": signal, "total_in_range": len(picked), "returned": len(thinned), "thinned_every": step,
                "samples": [{"t": r["time_utc"], "value": r.get("value"), "event": r.get("event_value") or r.get("code"), "unit": r.get("unit"),
                             "scope": r.get("scope"), "clock": r.get("clock_domain"), "source": f"{r.get('import_id')}#row{r.get('row')}"} for r in thinned]}

    def tool_compare_to_reference(self) -> Any:
        return {"reference": self.run.get("reference", {}).get("window_iso"), **(self.run["findings"].get("reference_comparison") or {})}

    def tool_brief_events(self) -> Any:
        tr = self.run["findings"].get("transients") or {}
        return {"rule": tr.get("rule"), "events_total": tr.get("events_total"), "skipped": tr.get("skipped"),
                "signals": [{"signal": s["signal"], "scope": s["scope"], "clock": s["clock_domain"], "rate_hz": s["rate_hz"],
                             "events": [{k: e[k] for k in ("start", "samples", "duration_ms", "peak_value", "local_level", "direction", "in_symptom_window", "plot_width_px", "rows")} for e in s["events"]]}
                            for s in tr.get("signals", [])]}

    def tool_actuator_outputs(self) -> Any:
        out = self.run["findings"].get("actuator_outputs") or {}
        return {k: out.get(k) for k in ("status", "rules_version", "rules", "reference", "channels", "findings", "machine_baseline", "boundary", "reason")}

    def tool_command_tracking(self) -> Any:
        return self.run["findings"].get("tracking") or {"status": "not_computed", "reason": "This run predates command tracking; run the investigation again"}

    def tool_command_chain(self) -> Any:
        chain = self.run["findings"].get("command_chain") or {}
        return {k: chain.get(k) for k in ("status", "segments", "window_note", "boundary", "declared_skew_s")}

    def tool_input_response(self) -> Any:
        return self.run["findings"].get("input_response")

    def tool_events_near_window(self) -> Any:
        return {"events": (self.run["findings"].get("events") or {}).get("events", []), "heartbeats": self.run["findings"].get("heartbeats")}

    def tool_next_checks(self) -> Any:
        return [{"check_id": c["check_id"], "rank": c["rank"], "question": c["question"], "trigger": c["trigger"], "availability": c["availability"]["state"],
                 "explanation": c["rank_explanation"]} for c in self.run["checks"]]

    def tool_next_action_plan(self) -> Any:
        from app.engine import next_action as na
        assessment = na.assess(self.run)
        results = na.site_results(self.store.ledger(self.case["id"]), self.run["id"])
        rows = na.compare(self.run, assessment, results)
        return {"policy": na.POLICY, "assessment": {k: assessment[k] for k in ("supported", "cannot_tell_apart", "why_not_separable", "less_likely", "ruled_out")},
                "checks_compared": [{k: r[k] for k in ("position", "check_id", "question", "access", "disruption", "informs", "could_change", "could_change_travel",
                                                        "settles_note", "note", "ranking_basis", "tied_with")} for r in rows],
                "first": rows[0]["check_id"] if rows else None,
                "basis": "Written rules, not validated against outcomes. Disagree when the data gives a reason."}

    def tool_machine_history(self) -> Any:
        return self.run.get("machine_history")


    # ---- compute layer: any case in this workspace, new analysis ----
    def _case(self, case_id: Optional[str]) -> str:
        cid = case_id or self.case["id"]
        if cid != self.case["id"]:
            self.store.case(cid)
            if not self.may_share(cid):
                raise ValueError(f"Case {cid} holds private data that has not been authorised for this model; ask an engineer to authorise it or compare without it")
        self.cases_used.add(cid)
        return cid

    def _series(self, spec: Dict[str, Any], max_points: int = 50_000) -> Dict[str, Any]:
        cid = self._case(spec.get("case_id"))
        return self.data.series(cid, spec["signal"], scope=spec.get("scope"), window=spec.get("window") or "all",
                                start=spec.get("start"), end=spec.get("end"), max_points=max_points)

    def tool_find_cases(self, machine_id: Optional[str] = None, machine_class: Optional[str] = None, signal: Optional[str] = None) -> Any:
        found = self.data.cases(machine_id=machine_id, machine_class=machine_class, signal=signal, exclude=self.case["id"])
        for c in found:
            c["data_available_to_model"] = self.may_share(c["case_id"])
        return {"cases": found, "note": "Only this workspace's cases. Cases whose data is not authorised for the model can be listed but not queried."}

    def tool_signal_catalog(self, case_id: Optional[str] = None) -> Any:
        cid = self._case(case_id)
        return {"case_id": cid, "signals": self.data.catalogue(cid)}

    def tool_query_signal(self, signal: str, case_id: Optional[str] = None, scope: Optional[str] = None, window: str = "all",
                          start: Optional[str] = None, end: Optional[str] = None) -> Any:
        s = self._series({"case_id": case_id, "signal": signal, "scope": scope, "window": window, "start": start, "end": end})
        n = len(s["t"])
        step = max(1, -(-n // MAX_SAMPLES))
        picked = list(range(0, n, step))[:MAX_SAMPLES]
        self.samples_shared += len(picked)
        out = {k: s[k] for k in ("case_id", "signal", "scope", "unit", "clock", "clock_max_skew_s", "window")}
        if s["numeric"]:
            out["stats"] = screening._stats([float(v) for v in s["v"]])
        out["samples"] = [{"t": compute_methods.iso(s["t"][i]), "value": s["v"][i], "source": s["sources"][i]} for i in picked]
        out["provenance"] = {**s["provenance"], "samples_returned": len(picked), "thinned_every_for_samples": step}
        return out

    def tool_list_methods(self) -> Any:
        team = compute_methods.Library(self.store.path).all() if getattr(self.store, "path", None) else []
        return {"built_in": {k: {**v, "version": compute_methods.METHODS_VERSION} for k, v in compute_methods.BUILTIN.items()},
                "team": [{k: m[k] for k in ("name", "description", "inputs", "author", "version", "saved_at")} for m in team],
                "sandbox": sandbox.isolation()["label"]}

    def _record(self, kind: str, name: str, out: Dict[str, Any], code: Optional[str] = None) -> Dict[str, Any]:
        self.computations.append({"kind": kind, "name": name, "code": code, "ok": out.get("ok", True), "provenance": out.get("provenance"),
                                  "result_preview": json.dumps(out.get("result", out), default=str)[:1500]})
        return out

    def tool_run_method(self, method: str, inputs: Dict[str, Dict[str, Any]], params: Optional[Dict[str, Any]] = None) -> Any:
        params = dict(params or {})
        if not inputs or len(inputs) > MAX_SERIES:
            raise ValueError(f"Give 1 to {MAX_SERIES} input series")
        series = {name: self._series(spec) for name, spec in inputs.items()}
        started = time.perf_counter()
        if method in ("onset", "onset_order"):
            kw = {k: params[k] for k in ("k", "n", "normal", "after") if k in params}
            for name in [n for n in series if n.endswith("_minus")]:
                base = name[:-len("_minus")]
                if base not in series:
                    raise ValueError(f"{name} needs an input called {base}")
                series[base] = compute_methods.difference(series[base], series.pop(name))
            if method == "onset":
                result = compute_methods.onset(self.data, series.get("a") or next(iter(series.values())), **kw)
                result.pop("onset_t", None)
            else:
                if not {"a", "b"} <= set(series):
                    raise ValueError("onset_order needs inputs a and b")
                result = compute_methods.onset_order(self.data, series["a"], series["b"], **kw)
        elif method == "lag":
            if not {"a", "b"} <= set(series):
                raise ValueError("lag needs inputs a and b")
            result = compute_methods.lag(self.data, series["a"], series["b"], **({"max_lag_s": float(params["max_lag_s"])} if "max_lag_s" in params else {}))
        elif method == "compare_runs":
            result = compute_methods.compare_runs(self.data, list(series.values()))
        else:
            team = compute_methods.Library(self.store.path).get(method)
            out = sandbox.run(team["code"], series, params=params, protected=self._protected())
            out["provenance"]["method"] = {"name": team["name"], "version": team["version"], "author": team["author"]}
            return self._record("team_method", method, out, team["code"])
        out = {"ok": True, "result": result, "provenance": {"method": {"name": method, "version": compute_methods.METHODS_VERSION},
                                                             "inputs": [s["provenance"] for s in series.values()], "params": params,
                                                             "duration_ms": round((time.perf_counter() - started) * 1000)}}
        return self._record("method", method, out)

    def _protected(self) -> List[str]:
        import os
        base = os.path.dirname(os.path.realpath(str(getattr(self.store, "path", "") or ".")))
        return [base] + ([os.environ["VEYRA_DATA_DIR"]] if os.environ.get("VEYRA_DATA_DIR") else [])

    def tool_run_python(self, code: str, inputs: Dict[str, Dict[str, Any]], params: Optional[Dict[str, Any]] = None) -> Any:
        if not inputs or len(inputs) > MAX_SERIES:
            raise ValueError(f"Give 1 to {MAX_SERIES} input series")
        series = {name: self._series(spec) for name, spec in inputs.items()}
        out = sandbox.run(code, series, params=params, protected=self._protected())
        return self._record("python", "run_python", out, code)


def serialize(result: Any) -> str:
    text = json.dumps(result, default=str, separators=(",", ":"))
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + '..."[truncated; narrow the request]"'
    return text
