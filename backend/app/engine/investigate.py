"""Investigation Engine v2 (pure): imports + machine context + ledger -> one immutable run.

Pipeline (each step recorded in a timed tool trace):
  verify inputs -> resolve context (incident and baseline windows) -> coverage -> measurement screening
  -> heartbeat gaps -> events -> command-chain stage latency -> optional input–response baseline
  -> fault-domain assessment (explicit rules) -> ranked next checks -> dispatch readiness.

Observations and explanations stay separate. Root cause is always "unconfirmed" here; competing
domains stay open unless measured evidence speaks to them, and even then only for what was measured.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from app.analysis import command_chain, input_response, screening
from app.context import model as context_model
from app.engine import checks as check_policy
from app.ingest import catalog

ENGINE_VERSION = "investigation-engine-v2"
DOMAIN_RULES_VERSION = "domain-rules-v1"
FAULT_TYPE = "remote_response_delay"
SEGMENT_RULE = {"min_commands": 3, "min_increase_ms": 20.0}
DOMAINS = ("network", "control_suite", "vehicle_interface", "vehicle_body")


class Trace:
    def __init__(self) -> None:
        self.steps: List[Dict[str, Any]] = []

    def call(self, name: str, inputs: Dict[str, Any], fn: Callable[[], Any], refs: Sequence[str]) -> Any:
        start = time.perf_counter()
        step = {"tool": name, "inputs": inputs, "refs": list(refs), "status": "running"}
        self.steps.append(step)
        try:
            value = fn()
            step["status"] = "succeeded"
            return value
        except (ValueError, ZeroDivisionError) as error:
            step.update(status="failed", error=str(error))
            return None
        finally:
            step["duration_ms"] = round((time.perf_counter() - start) * 1000, 3)


def _iso(t: Optional[float]) -> Optional[str]:
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z") if t is not None else None


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _cross_clock_assessment(inside: Dict[str, Any], outside: Dict[str, Any]) -> Dict[str, Any]:
    n_in, n_out = inside.get("cross_clock_count", 0), outside.get("cross_clock_count", 0)
    absolute = ({"status": "computed_with_clock_uncertainty", "median_ms": inside["uncertain_median_ms"], "uncertainty_ms": inside["uncertainty_ms"]}
                if inside.get("uncertain_count") else {"status": "unavailable", "reason": "Clock offset not bounded"})
    if n_in < SEGMENT_RULE["min_commands"] or n_out < SEGMENT_RULE["min_commands"]:
        return {"status": "unavailable_cross_clock", "detail": f"Different clocks; {n_in} inside / {n_out} outside matched commands is too few for a relative comparison",
                "absolute_latency": absolute}
    change = inside["cross_clock_raw_median_ms"] - outside["cross_clock_raw_median_ms"]
    observed = inside["cross_clock_raw_median_ms"] > outside["cross_clock_raw_p95_ms"] and change > SEGMENT_RULE["min_increase_ms"]
    return {"status": "delay_change_observed_assuming_stable_offset" if observed else "no_delay_change_observed_assuming_stable_offset",
            "relative_change_ms": change, "commands_inside": n_in, "commands_outside": n_out, "absolute_latency": absolute,
            "assumption": "The offset between the two clocks stayed constant across the compared commands (no clock step or large drift).",
            "rule": f"inside raw median > outside raw p95 and change > {SEGMENT_RULE['min_increase_ms']:g} ms"}


def _segment_assessment(inside: Dict[str, Any], outside: Dict[str, Any]) -> Dict[str, Any]:
    if inside.get("cross_clock_count") and not inside.get("computed_count"):
        return _cross_clock_assessment(inside, outside)
    n_in, n_out = inside.get("computed_count", 0), outside.get("computed_count", 0)
    if n_in < SEGMENT_RULE["min_commands"] or n_out < SEGMENT_RULE["min_commands"]:
        return {"status": "insufficient_matched_commands", "detail": f"{n_in} matched commands inside, {n_out} outside (rule needs ≥{SEGMENT_RULE['min_commands']} each)"}
    increase = inside["median_ms"] - outside["median_ms"]
    rule = f"inside median > outside p95 and increase > {SEGMENT_RULE['min_increase_ms']:g} ms"
    if inside["median_ms"] > outside["p95_ms"] and increase > SEGMENT_RULE["min_increase_ms"]:
        status = "delay_observed_in_segment"
    else:
        status = "no_delay_observed_in_segment"
    return {"status": status, "inside_median_ms": inside["median_ms"], "outside_median_ms": outside["median_ms"],
            "outside_p95_ms": outside["p95_ms"], "increase_ms": increase, "commands_inside": n_in, "commands_outside": n_out, "rule": rule}


def _command_chain(rows: List[Dict[str, Any]], window: Optional[Tuple[float, float]], skew: Dict[str, Optional[float]]) -> Dict[str, Any]:
    all_result = command_chain.stage_latencies(rows, skew)
    if not all_result["command_count"]:
        return {"status": "unavailable", "reason": "No command stage records", "all": all_result}
    if window is None:
        return {"status": "computed_without_window", "all": all_result, "segments": {}}
    anchor_signal = next((s for s in command_chain.STAGES if any(r.get("signal") == s for r in rows)), None)
    anchor_clock = next(r["clock_domain"] for r in rows if r.get("signal") == anchor_signal)
    inside = command_chain.stage_latencies(rows, skew, window=(window[0], window[1], anchor_clock))
    inside_ids = {c["command_id"] for c in inside["commands"]}
    outside_rows = [r for r in rows if r.get("command_id") not in inside_ids]
    outside = command_chain.stage_latencies(outside_rows, skew)
    segments = {}
    for first, second, domain in command_chain.SEGMENTS:
        key = f"{first}->{second}"
        segments[key] = {"domain": domain, **_segment_assessment(inside["segments"][key], outside["segments"][key])}
    return {"status": "computed", "window_clock": anchor_clock, "inside_commands": inside["command_count"], "outside_commands": outside["command_count"],
            "segments": segments, "inside": inside["segments"], "outside": outside["segments"], "declared_skew_s": skew,
            "window_note": f"Symptom window applied in {anchor_clock}; edges are uncertain by any unknown offset between clocks.",
            "boundary": all_result["boundary"]}


def _input_response(rows, window, contexts_at: Callable[[float], Dict[str, Any]], baseline_window, trace_refs) -> Dict[str, Any]:
    channels = input_response.channels(rows)
    if not channels:
        return {"status": "not_applicable", "reason": "No channel has both vehicle.command_input and vehicle.actuator_response records."}
    if window is None:
        return {"status": "unavailable", "reason": "A symptom window is required to compare against a baseline."}
    results = []
    for channel in channels:
        times = [r["t"] for r in rows if r.get("scope") == channel and r.get("signal") in (input_response.INPUT_SIGNAL, input_response.RESPONSE_SIGNAL)]
        if baseline_window is not None:
            base = baseline_window
            base_source = "explicit baseline window"
        else:
            base = (min(times), window[0] - 30.0)
            base_source = "default: data before the symptom window (minus 30 s)"
        if base[1] - base[0] < 60:
            results.append({"channel": channel, "status": "unavailable", "reason": "Less than 60 s of data available for a baseline window", "baseline_window": [ _iso(base[0]), _iso(base[1])]})
            continue
        baseline_model = input_response.identify(rows, channel, base[0], base[1])
        incident_model = input_response.identify(rows, channel, window[0], window[1])
        comparability = context_model.compare(contexts_at(base[0]), contexts_at(window[0]), "input_response.baseline")
        comparison = input_response.compare(baseline_model, incident_model, context_comparability=comparability)
        results.append({"channel": channel, "baseline_window": [_iso(base[0]), _iso(base[1])], "baseline_window_source": base_source,
                        "incident_window": [_iso(window[0]), _iso(window[1])], **comparison})
    statuses = {r["status"] for r in results}
    return {"status": "computed", "module": input_response.MODULE_VERSION, "channels": results,
            "summary": sorted(statuses), "threshold_provenance": input_response.THRESHOLD_PROVENANCE}


def _domains(findings: Dict[str, Any], context_now: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    obs: Dict[str, List[Dict[str, Any]]] = {d: [] for d in DOMAINS}
    unscoped: List[Dict[str, Any]] = []
    endpoints_confirmed = context_model.confirmed(context_now, "measurement_endpoints") is not None
    for m in findings["measurements"] or []:
        if m["domain"] in obs and m["crossing_in_window"]:
            obs[m["domain"]].append({"rule": "R-SCREEN", "kind": "anomaly", "text": f"{m['signal']} above screening rule on path '{m['scope']}'"
                                     + ("" if endpoints_confirmed else " (path not confirmed as command path)")})
    for hb in findings["heartbeats"] or []:
        if hb.get("gap_in_window"):
            obs["control_suite"].append({"rule": "R-HEARTBEAT", "kind": "anomaly", "text": f"Heartbeat gap in {hb['clock_domain']} ({hb['rule']})"})
    for event in (findings["events"] or {}).get("events", []):
        domain = event["domain"] if event["domain"] in obs else ("control_suite" if event["signal"].startswith("controller.") else None)
        label = event["code"] or event["event_value"] or event["signal"]
        if domain:
            obs[domain].append({"rule": "R-EVENT", "kind": "anomaly", "text": f"{event['signal']} '{label}' near the symptom window"})
        else:
            unscoped.append({"rule": "R-EVENT-UNSCOPED", "kind": "anomaly_unscoped", "text": f"{event['signal']} '{label}' near the symptom window; source domain unknown (map it in the import profile)"})
    chain = findings["command_chain"] or {}
    for key, segment in (chain.get("segments") or {}).items():
        targets = ["vehicle_interface", "vehicle_body"] if segment["domain"] == "vehicle_interface_or_body" else [segment["domain"]]
        for d in targets:
            if segment["status"] == "delay_change_observed_assuming_stable_offset":
                obs[d].append({"rule": "R-SEGMENT-RELATIVE", "kind": "delay", "text": f"Delay in {key} rose by {segment['relative_change_ms']:.0f} ms for {segment['commands_inside']} commands inside the window, assuming a stable clock offset (absolute latency {segment['absolute_latency']['status']})"})
            elif segment["status"] == "no_delay_change_observed_assuming_stable_offset":
                obs[d].append({"rule": "R-SEGMENT-RELATIVE", "kind": "no_delay", "text": f"No delay change in {key} ({segment['relative_change_ms']:+.0f} ms), assuming a stable clock offset"})
            elif segment["status"] == "delay_observed_in_segment":
                obs[d].append({"rule": "R-SEGMENT", "kind": "delay", "text": f"Delay observed in {key}: median {segment['inside_median_ms']:.0f} ms vs {segment['outside_median_ms']:.0f} ms outside"})
            elif segment["status"] == "no_delay_observed_in_segment":
                obs[d].append({"rule": "R-SEGMENT", "kind": "no_delay", "text": f"No delay observed in {key} for {segment['commands_inside']} matched commands (median {segment['inside_median_ms']:.0f} ms vs {segment['outside_median_ms']:.0f} ms)"})
            else:
                obs[d].append({"rule": "R-SEGMENT", "kind": "unmeasured", "text": f"{key}: {segment['status'].replace('_', ' ')}"})
    io = findings["input_response"] or {}
    for channel in io.get("channels", []):
        if channel["status"] == "changed":
            for o in channel["observations"]:
                obs["vehicle_body"].append({"rule": "R-IO", "kind": "delay" if "delay" in o["observation"] else "anomaly", "text": f"{channel['channel']}: {o['detail']}"})
        elif channel["status"] == "no_change_detected":
            obs["vehicle_body"].append({"rule": "R-IO", "kind": "no_delay", "text": f"{channel['channel']}: vehicle-side input→response within baseline rules"})
        else:
            obs["vehicle_body"].append({"rule": "R-IO", "kind": "unmeasured", "text": f"{channel['channel']}: {channel.get('reason') or channel['status']}"})
    result = {}
    for d in DOMAINS:
        kinds = {o["kind"] for o in obs[d]}
        if "delay" in kinds:
            status = "delay_observed"
        elif "anomaly" in kinds:
            status = "anomaly_observed"
        elif "no_delay" in kinds:
            status = "no_delay_observed_in_measured_part"
        else:
            status = "not_observed"
        result[d] = {"status": status, "observations": obs[d], "root_cause": "unconfirmed",
                     "meaning": {"delay_observed": "Measured delay attributed to this segment for matched records",
                                 "anomaly_observed": "Anomalies recorded in this domain; relation to the symptom not established",
                                 "no_delay_observed_in_measured_part": "Measured part showed no delay; unmeasured parts remain possible",
                                 "not_observed": "No relevant records; domain remains fully open"}[status]}
    return result, unscoped


def _dispatch(context_now: Dict[str, Any], domains: Dict[str, Dict[str, Any]], ranked: List[Dict[str, Any]]) -> Dict[str, Any]:
    dispatch_keys = [k for k, d in context_model.SCHEMA.items() if d.dispatch_relevant]
    context_gaps = context_model.gaps(context_now, dispatch_keys)
    blockers = []
    for reason, keys in context_gaps.items():
        if keys:
            blockers.append({"item": "Machine context for dispatch: " + ", ".join(keys), "status": reason})
    remote_open = [c for c in ranked if c["availability"]["state"] in ("ready_automatic", "ready_remote") and not c["previously_performed"]
                   and c["objective"] == "reduce_uncertainty"]
    for check in remote_open:
        blockers.append({"item": f"Remote check not yet done: {check['check_id']} — {check['question']}", "status": "remote_check_outstanding"})
    for check in ranked:
        if check["availability"]["state"] == "needs_authorization":
            blockers.append({"item": f"Authorization needed for {check['check_id']}", "status": "authorization_required"})
    tools = [{"item": "Laptop with controller and router log access", "basis": "Needed for any remote or on-site log retrieval", "status": "candidate"}]
    if domains["network"]["status"] in ("anomaly_observed", "delay_observed"):
        tools.append({"item": "Network test equipment for the site link (confirm against kit and network path)", "basis": "Network anomalies recorded", "status": "candidate"})
    if domains["vehicle_body"]["status"] == "delay_observed" or domains["vehicle_interface"]["status"] == "delay_observed":
        oem, model = context_model.confirmed(context_now, "oem"), context_model.confirmed(context_now, "model")
        tools.append({"item": f"Hydraulic test gauges and OEM-approved service tooling for {oem or 'unconfirmed OEM'} {model or 'unconfirmed model'}",
                      "basis": "Vehicle-side delay observed", "status": "candidate"})
    return {"status": "not_ready" if blockers else "ready_for_engineer_decision", "blockers": blockers, "candidate_preparation": tools,
            "parts": {"status": "not_determined", "basis": "No component fault has been confirmed"},
            "boundary": "Preparation support only. Not dispatch approval, not a confirmed failed part, not a safety clearance."}


def investigate(*, case: Dict[str, Any], imports: List[Dict[str, Any]], assertions: List[Any], ledger: List[Dict[str, Any]],
                goal: str, actor: str, now: datetime, run_id: str, baseline_window: Optional[Tuple[float, float]] = None) -> Dict[str, Any]:
    if goal not in ("reduce_uncertainty", "restore_operations"):
        raise ValueError("goal must be reduce_uncertainty or restore_operations")
    trace = Trace()
    started = time.perf_counter()
    window = (case["symptom_start"], case["symptom_end"]) if case.get("symptom_start") is not None else None

    integrity = trace.call("verify_inputs", {"imports": len(imports)},
                           lambda: {i["id"]: i["integrity_ok"] for i in imports}, [i["id"] for i in imports])
    usable = [i for i in imports if integrity and integrity.get(i["id"])]
    if len(usable) != len(imports):
        raise ValueError("Stored import integrity check failed; investigation refused: " + ", ".join(i["id"] for i in imports if i not in usable))
    rows = [r for i in usable for r in i["rows"]]
    machine = case["machine_id"]
    known_at = now.timestamp()
    evidence_end = max((r["t"] for r in rows), default=known_at)
    incident_time = window[0] if window else evidence_end

    def contexts_at(t: float) -> Dict[str, Any]:
        return context_model.resolve(assertions, machine, effective_at=t, known_at=known_at)

    context_incident = trace.call("resolve_machine_context", {"effective_at": _iso(incident_time), "known_at": _iso(known_at)}, lambda: contexts_at(incident_time), [])
    context_current = contexts_at(known_at)

    findings: Dict[str, Any] = {}
    findings["coverage"] = trace.call("coverage", {"window": [_iso(w) for w in window] if window else None}, lambda: screening.coverage(rows, window), [i["id"] for i in usable])
    findings["measurements"] = trace.call("screen_measurements", {"rules": screening.SCREENING_RULES_VERSION}, lambda: screening.measurements(rows, window), [i["id"] for i in usable])
    findings["heartbeats"] = trace.call("heartbeat_gaps", {"factor": screening.HEARTBEAT_GAP_FACTOR}, lambda: screening.heartbeat_gaps(rows, window), [i["id"] for i in usable])
    findings["events"] = trace.call("events_near_window", {"margin_s": 60}, lambda: screening.events(rows, window), [i["id"] for i in usable])

    skew: Dict[str, Optional[float]] = {}
    bound = context_model.confirmed(context_incident, "clock_offset_bound_ms")
    for i in usable:
        skew[i["clock_domain"]] = (float(bound) / 2000.0) if bound is not None else i.get("max_clock_skew_s")
    skew_source = "confirmed context clock_offset_bound_ms (split equally)" if bound is not None else "import profiles max_clock_skew_s (None = unknown)"
    findings["command_chain"] = trace.call("command_chain_latency", {"tool": command_chain.TOOL_VERSION, "skew_source": skew_source},
                                           lambda: _command_chain(rows, window, skew), [i["id"] for i in usable])
    findings["input_response"] = trace.call("input_response_baseline", {"module": input_response.MODULE_VERSION,
                                            "baseline_window": [_iso(b) for b in baseline_window] if baseline_window else "default"},
                                            lambda: _input_response(rows, window, contexts_at, baseline_window, []), [i["id"] for i in usable])

    assessed = trace.call("assess_domains", {"rules": DOMAIN_RULES_VERSION}, lambda: _domains(findings, context_incident), [])
    domains, unscoped = assessed

    signals_present = {r.get("signal") for r in rows if r.get("signal")}
    io = findings["input_response"] or {}
    io_needs_context = sorted({k for ch in io.get("channels", []) if ch.get("status") == "not_comparable"
                               for k in (ch.get("context_comparability") or {}).get("missing_confirmation", {})})
    answered = {}
    chain = findings["command_chain"] or {}
    if chain.get("status") == "computed":
        answered["CHK-CMD-CHAIN"] = "Stage latencies computed in this run"
    if io.get("status") == "computed" and any(ch.get("status") in ("changed", "no_change_detected") for ch in io.get("channels", [])):
        answered["CHK-IO-BASELINE"] = "Input–response comparison computed in this run"
    performed = {}
    for entry in ledger:
        if entry["kind"] == "check_execution" and entry["payload"]["status"] in ("performed", "automatic_read_only"):
            performed[entry["payload"]["check_id"]] = {"entry_id": entry["id"], "recorded_at": entry["recorded_at"], "run_id": entry["payload"]["run_id"]}
    comparability_keys = set(context_model.COMPARABILITY_RULES["remote_response_delay.baseline"]["must_match_confirmed"]) | set(context_model.COMPARABILITY_RULES["input_response.baseline"]["must_match_confirmed"])
    gap_lists = context_model.gaps(context_incident, sorted(comparability_keys))
    facts = {
        "signals_present": signals_present, "answered_in_run": answered, "io_needs_context": io_needs_context,
        "remote_access": context_model.confirmed(context_current, "remote_access"), "previously_performed": performed,
        "transport_cross_clock": (chain.get("segments") or {}).get("command.sent->command.received", {}).get("status", "").endswith(("assuming_stable_offset", "cross_clock"))
                                 and (chain.get("segments") or {}).get("command.sent->command.received", {}).get("absolute_latency", {}).get("status") != "computed_with_clock_uncertainty",
        "network_crossing": any(m["crossing_in_window"] and (m["signal"] or "").startswith("network.") for m in findings["measurements"] or []),
        "endpoints_confirmed": context_model.confirmed(context_incident, "measurement_endpoints") is not None,
        "control_events": domains["control_suite"]["status"] == "anomaly_observed",
        "context_gaps": sorted(k for keys in gap_lists.values() for k in keys),
        "vehicle_delay": "delay_observed" in (domains["vehicle_body"]["status"], domains["vehicle_interface"]["status"]),
        "rows_in_window": any(c["rows_in_window"] for c in findings["coverage"] or []),
    }
    ranked = trace.call("rank_next_checks", {"policy": check_policy.POLICY_VERSION, "goal": goal}, lambda: check_policy.rank(domains, facts, goal), [])
    dispatch = trace.call("dispatch_readiness", {}, lambda: _dispatch(context_current, domains, ranked), [])

    missing = []
    for i in usable:
        missing.extend(f"{i['filename']}: {m}" for m in i.get("missing_information", []))
    if window is None:
        missing.insert(0, "No symptom window recorded for the case; in-window vs out-of-window comparisons are unavailable.")
    if gap_lists["conflicting"]:
        missing.append("Conflicting context values: " + ", ".join(gap_lists["conflicting"]))

    inputs = {i["id"]: {"filename": i["filename"], "sha256": i["sha256"], "normalized_sha256": i["normalized_sha256"], "clock_domain": i["clock_domain"],
                        "rows": len(i["rows"]), "synthetic": i.get("synthetic", False)} for i in usable}
    run = {
        "id": run_id, "case_id": case["id"], "machine_id": machine, "fault_type": case.get("fault_type", FAULT_TYPE), "created_at": now.isoformat(), "actor": actor,
        "goal": goal, "symptom_window": [_iso(w) for w in window] if window else None, "synthetic": bool(case.get("synthetic")),
        "inputs": inputs, "input_hashes": {k: v["sha256"] for k, v in inputs.items()},
        "context": {"incident": context_incident, "current_hash": context_current["hash"],
                    "changes_since_incident": context_model.changes_between(context_incident, context_current),
                    "comparability_for_baselines": context_model.compare(context_incident, context_current, "remote_response_delay.baseline")},
        "findings": findings, "findings_digest": _digest(findings), "domains": domains, "unscoped_observations": unscoped, "checks": ranked, "dispatch": dispatch,
        "missing_information": missing, "root_cause": "unconfirmed",
        "versions": {"engine": ENGINE_VERSION, "domain_rules": DOMAIN_RULES_VERSION, "check_policy": check_policy.POLICY_VERSION,
                     "screening_rules": screening.SCREENING_RULES_VERSION, "command_chain": command_chain.TOOL_VERSION,
                     "input_response": input_response.MODULE_VERSION, "context_schema": context_model.CONTEXT_SCHEMA_VERSION,
                     "comparability_rules": context_model.COMPARABILITY_RULES_VERSION, "catalog": catalog.CATALOG_VERSION},
        "model": {"status": "deterministic_no_llm", "external_calls": 0},
        "trace": trace.steps, "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        "boundary": "Observations from executed calculations on imported records. Fault domains are not root causes. "
                    "Checks are ranked by documented ordinal rules. Nothing here authorizes dispatch or actuation.",
    }
    run["run_hash"] = _digest({k: v for k, v in run.items() if k not in ("trace", "duration_ms")})
    return run


def belief_summary(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [{"domain": d, "status": v["status"], "observation_rules": sorted({o["rule"] for o in v["observations"]})} for d, v in run["domains"].items()]


def candidate_summary(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [{"check_id": c["check_id"], "rank": c["rank"], "question": c["question"], "objective": c["objective"], "mode": c["mode"],
             "availability": c["availability"]["state"], "informs_open_domains": c["informs_open_domains"], "disruption": c["disruption"],
             "trigger": c["trigger"]} for c in run["checks"]]


def window_rows(run: Dict[str, Any], imports: List[Dict[str, Any]], limit: int = 200) -> Dict[str, Any]:
    """Automatic read-only check CHK-SOURCE-ROWS: original records inside the symptom window."""
    if not run.get("symptom_window"):
        raise ValueError("No symptom window recorded")
    start = datetime.fromisoformat(run["symptom_window"][0].replace("Z", "+00:00")).timestamp()
    end = datetime.fromisoformat(run["symptom_window"][1].replace("Z", "+00:00")).timestamp()
    selected = [{"import_id": i["id"], "filename": i["filename"], "row": r["row"], "time_utc": r["time_utc"], "signal": r["signal"] or r["source_signal"],
                 "value": r["value"], "unit": r["unit"], "event_value": r["event_value"], "code": r["code"], "command_id": r["command_id"],
                 "clock_domain": r["clock_domain"], "original": r.get("raw")}
                for i in imports if i["id"] in run["inputs"] for r in i["rows"] if start <= r["t"] <= end]
    selected.sort(key=lambda r: (r["time_utc"], r["import_id"], r["row"]))
    by_signal: Dict[str, int] = {}
    for r in selected:
        by_signal[r["signal"]] = by_signal.get(r["signal"], 0) + 1
    return {"tool": "window_rows", "count": len(selected), "returned": min(limit, len(selected)), "by_signal": by_signal,
            "rows": selected[:limit], "truncated": len(selected) > limit,
            "boundary": "Original records as imported; window edges apply per clock domain without offset correction."}
