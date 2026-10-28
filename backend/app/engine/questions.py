"""Questions the case's own data can answer, answered before Veyra asks anyone for more evidence.

A finding says what a rule saw ("rudder_rc4 output held 1900 for 17 s"). An engineer's next questions are concrete:
Was that value inside what the channel normally outputs (an end stop or limit)? Did other outputs change at the same
moment? What happened first? Was it commanded by a mode change, failsafe or controller event? Did the actuator
physically move there? This module answers each from the records already in the case (and known-good runs of the same
machine), or says exactly which record would answer it and why a person has to get it.

Facts only: no cause is named. Read-only and deterministic; computed when asked, so it also works for stored runs.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from statistics import median
from typing import Any, Dict, List, Optional

QUESTIONS_VERSION = "data-questions-v2"
EVENT_SIGNALS = ("controller.event", "controller.reset", "fault.code")
FEEDBACK_SIGNALS = ("vehicle.actuator_response", "actuator.status")
CHANGE_MIN = 20.0       # output units (PWM µs): smaller median shifts around the onset are not reported
AROUND_S = 3.0          # seconds compared before / after the onset
EVENT_MARGIN_S = 10.0


def _t(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _clock(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%H:%M:%S.%f")[:-4]


def _unit(u: Optional[str]) -> str:
    return " µs" if u == "us" else f" {u}" if u else ""


def _discovered(disc: Optional[Dict[str, Any]], t0: float) -> Optional[Dict[str, Any]]:
    """The evidence-discovery result for the incident at t0 (within 1 s), if the original recording was read."""
    for inc in (disc or {}).get("incidents") or []:
        it = (inc.get("incident") or {}).get("t")
        if it is not None and abs(float(it) - t0) <= 1.0 and not inc.get("outside_recording"):
            return inc
    return None


def _from_discovery(disc: Dict[str, Any], inc: Dict[str, Any], ch: str) -> Dict[str, Any]:
    """The 'commanded?' question, answered from the whole original recording (evidence discovery), not only the imported signals."""
    parts = []
    shown = lambda v: str(v).lower() if isinstance(v, bool) else str(v)  # noqa: E731
    modes = [m for m in inc.get("controller_mode") or [] if m["field"] not in ("connected", "guided", "system_status") and m.get("at_onset") is not None]
    steady = [m for m in modes if not m["changes_in_window"]]
    if steady:
        every = max((m.get("sample_interval_s") or 0) for m in steady)
        parts.append(" and ".join(f"{m['field']} {shown(m['at_onset'])}" for m in steady) + " at the onset, unchanged in the 60 s around it"
                     + (f" (state sampled about every {every:.1f} s)" if every else ""))
    for m in modes:
        if m["changes_in_window"]:
            parts.append(f"{m['field']} {shown(m['at_onset'])} at the onset, {m['changes_in_window']} change(s) in the 60 s around it")
    facts = inc.get("facts") or []
    cause_like = [f for f in facts if f["point"] == "controller" and f.get("near_onset") and f["t_rel_s"] is not None and f["t_rel_s"] <= 1.0
                  and (f.get("chance_near_onset") or 0) < 0.2]
    for f in cause_like[:4]:
        parts.append(f"{f['text']} at {f['t_rel_s']:+.2f} s")
    for f in facts:
        if f["kind"] in ("operator_vs_output", "operator_steady"):
            parts.append(f["text"])
    if not inc.get("texts_in_recording"):
        parts.append("no controller text messages anywhere in the recording")
    src = disc.get("source") or {}
    return {"id": f"commanded:{ch}", "question": f"Was the change on {ch} commanded by the operator, or by the controller itself?", "status": "answered",
            "answer": f"From the whole original recording ({src.get('name')}), read by Veyra: " + "; ".join(parts) + ".",
            "source": {"recording": src.get("name"), "sha256": src.get("sha256"), "streams_read": len((disc.get("inventory") or {}).get("read_by_probes") or []),
                       "excluded": [e["stream"] for e in (disc.get("inventory") or {}).get("excluded") or []]},
            "facts": {"controller_records_before_onset": [f["text"] for f in cause_like]}}


def _msg(text: str) -> str:
    """A log line without its time stamp and level prefix."""
    text = re.sub(r"^\s*(\[[^\]]*\]\s*){1,3}:?\s*", "", text)
    text = re.sub(r"^\s*\w{3}\s+\d{1,2}\s+[\d:.]+\s+\S+\s+[\w./-]+(\[\d+\])?:\s*", "", text)  # syslog: 'Oct 27 14:05:42 host prog[pid]: '
    text = re.sub(r"^(kernel:\s*)?(WARNING|WARN|ERROR|INFO|NOTICE|DEBUG|FATAL):\s*", "", text)
    return text.strip().rstrip(".")


def _from_log(log: Dict[str, Any], reports: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Questions a text log answers (app/discovery/logs.py), and the checks it cannot answer."""
    out: List[Dict[str, Any]] = []
    restarts = log.get("restarts") or []
    inc = log.get("incident")
    if not restarts:
        out.append({"id": "log:restarts", "question": "Did the machine restart?", "status": "answered",
                    "answer": f"No restart or reset appears in the {log['lines']} lines of the log."
                    + (f" The first failure is at line {inc['line']}: {_msg(inc['text'])}." if inc else "")})
    if inc and inc.get("first_signs"):
        signs = inc["first_signs"]
        out.append({"id": "log:first_signs", "question": f"What warned before the failure at line {inc['line']}?", "status": "answered",
                    "answer": "Warnings before the failure, in order: " + "; ".join(f"{f['component']} from line {f['first_line']} ({_msg(f['text'])[:80]}"
                                                       + (f", {len(f['lines'])} lines" if len(f["lines"]) > 1 else "") + ")" for f in signs[:5])
                    + (f". The first sign came {inc['lead_s']:g} s before the failure" if inc.get("lead_s") is not None else "")
                    + ". Earlier is a lead, not a cause.",
                    "facts": {"first_signs": [{k: f[k] for k in ("component", "first_line", "kinds", "lines")} for f in signs]}})
    for t in (inc or {}).get("threads") or []:
        later = "; ".join(f"line {x['line']}: {_msg(x['text'])[:90]}" for x in t["later"][:3])
        out.append({"id": f"log:thread:{t['component']}", "question": f"What happened to {t['component']}?", "status": "answered",
                    "answer": f"{t['component']} warned before the failure (line{'s' if len(t['before']) > 1 else ''} {', '.join(map(str, t['before'][:4]))}), then {later}. "
                              + ("The same component warning right before the failure and failing later makes it the first place to look; the order alone does not prove it caused the failure."
                               if t.get("proximate", True) else
                               f"It failed only after line {inc['line']}, and other components warned closer to the failure ({', '.join(x for x in inc.get('proximate', [])[:2])}): "
                               "more likely a consequence or a separate fault than the trigger. Worth its own check, not the first one.")})
    for r in restarts[-2:]:
        where = f"line {r['line']}"
        if r["causes"]:
            ans = "; ".join(f"{c['label'].split(':')[0]} (line{'s' if len(c['lines']) > 1 else ''} {', '.join(map(str, c['lines'][:3]))}"
                            + (f", at {', '.join(c['locations'])}" if c["locations"] else "") + ")" for c in r["causes"])
            ans = f"The log states it: {ans}." + (f" {r['note']}" if r.get("note") else "")
        else:
            ans = "The log does not say why: no watchdog, assert, fault or low-voltage message near it."
        out.append({"id": f"log:cause:{r['line']}", "question": f"Why did it restart at {where}?", "status": "answered", "answer": ans,
                    "facts": {"primary_cause": r["primary_cause"], "lines": r["boot_lines"]}})
        before = [b for b in r["before"] if not re.fullmatch(r"\s*(\w{1,16}:)?\s*[-=_*#.~ ]*", b["text"])]  # skip separator lines
        if before:
            out.append({"id": f"log:before:{r['line']}", "question": f"What did the log show just before the restart at {where}?", "status": "answered",
                        "answer": " / ".join(f"line {b['line']}: {b['text'][:90]}" for b in before[-3:]) + "."})
    for a in log.get("absences") or []:
        out.append({"id": f"log:absence:{a['what'][:30]}", "question": a["what"].replace("No ", "Was there any ", 1).rstrip(".") + "?",
                    "status": "answered", "answer": f"{a['what']}. {a['caveat']}"})
    from app.discovery.logs import already_done
    for c in (log.get("checks") or [])[:5]:
        done = already_done(c, reports or [])
        if c.get("answered"):
            out.append({"id": f"log:check:{c['cause']}:{c['check'][:20]}", "question": c["question"], "status": "answered", "answer": c["answered"]})
        elif done:
            out.append({"id": f"log:check:{c['cause']}:{c['check'][:20]}", "question": c["question"], "status": "reported_done",
                        "answer": f"Reported as already examined: “{done.rstrip('.')}”.", "check": c["check"]})
        else:
            out.append({"id": f"log:check:{c['cause']}:{c['check'][:20]}", "question": c["question"], "status": "not_answerable",
                        "needs": f"{c['check']}. {c['why']}".rstrip("."), "check_id": None, "for": c["for"]})
    return out


def answer(run: Dict[str, Any], rows: List[Dict[str, Any]], baseline_runs: List[Dict[str, Any]], closure: Optional[Dict[str, Any]] = None,
           discovery: Optional[Dict[str, Any]] = None, causal: Optional[Dict[str, Any]] = None, lessons: Optional[Dict[str, Any]] = None,
           reports: Optional[List[str]] = None) -> Dict[str, Any]:
    outputs = [f for f in ((run.get("findings") or {}).get("actuator_outputs") or {}).get("findings", []) if f.get("rule") == "STUCK"]
    tracking = ((run.get("findings") or {}).get("tracking") or {}).get("findings", [])
    signals = {r.get("signal") for r in rows}
    by_channel: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        if r.get("signal") == "actuator.output" and isinstance(r.get("value"), (int, float)):
            by_channel.setdefault(r.get("scope") or "unnamed", []).append(r)
    for items in by_channel.values():
        items.sort(key=lambda r: r["t"])
    questions: List[Dict[str, Any]] = []

    for f in outputs:
        ch, v, t0, t1, unit = f["channel"], f["held_value"], f["t_start"], _t(f["end"]), f.get("unit")
        # 1. Output limits: the held value against this flight's normal period and every known-good run of the machine.
        kg = [r["value"] for run_ in baseline_runs for r in run_["rows"] if r.get("signal") == "actuator.output" and (r.get("scope") or "unnamed") == ch
              and isinstance(r.get("value"), (int, float))]
        ref = f.get("reference_range")
        parts = []
        if ref:
            parts.append(f"outside this flight's normal period ({ref[0]:g}–{ref[1]:g}{_unit(unit)})" if not ref[0] <= v <= ref[1]
                         else f"inside this flight's normal period ({ref[0]:g}–{ref[1]:g}{_unit(unit)})")
        if kg:
            lo, hi = min(kg), max(kg)
            where = ("above anything" if v > hi else "below anything" if v < lo else "equal to the highest value" if v == hi
                     else "equal to the lowest value" if v == lo else "within what")
            parts.append(f"{where} {ch} output in {len(baseline_runs)} known-good run{'s' if len(baseline_runs) != 1 else ''} of this machine ({lo:g}–{hi:g}{_unit(unit)})")
        questions.append({"id": f"range:{ch}", "question": f"Was {v:g}{_unit(unit)} within what {ch} normally outputs?",
                          "status": "answered" if parts else "not_answerable",
                          "answer": (f"{v:g}{_unit(unit)} is " + "; ".join(parts) + ".") if parts else None,
                          "needs": None if parts else "A normal period or known-good runs of this machine with this channel",
                          "facts": {"held_value": v, "reference_range": ref, "known_good_range": [min(kg), max(kg)] if kg else None, "known_good_runs": len(baseline_runs)}})
        # 2. Other outputs around the onset (medians just before and just after).
        changed, steady = [], []
        for other, items in sorted(by_channel.items()):
            if other == ch:
                continue
            before = [r["value"] for r in items if t0 - AROUND_S <= r["t"] < t0]
            after = [r["value"] for r in items if t0 < r["t"] <= t0 + AROUND_S]
            if len(before) < 2 or len(after) < 2:
                continue
            a, b = median(before), median(after)
            (changed if abs(b - a) >= CHANGE_MIN else steady).append((other, a, b))
        if changed or steady:
            text = ("; ".join(f"{o} {a:g}→{b:g}{_unit(unit)}" for o, a, b in changed) + " in the 3 s around the onset" if changed
                    else "No other output moved by more than " + f"{CHANGE_MIN:g}{_unit(unit)} in the 3 s around the onset")
            if changed and steady:
                text += f"; unchanged: {', '.join(o for o, _, _ in steady)}"
            questions.append({"id": f"others:{ch}", "question": f"Did other outputs change when {ch} started holding?", "status": "answered",
                              "answer": text + ".", "facts": {"changed": [list(x) for x in changed], "unchanged": [x[0] for x in steady]}})
        # 3. Order of events.
        if tracking:
            first = min(tracking, key=lambda x: x["t_start"])
            dt = first["t_start"] - t0
            questions.append({"id": f"order:{ch}", "question": "Which came first: the held output or the tracking departure?", "status": "answered",
                              "answer": (f"{ch} started holding at {_clock(t0)}; {first['axis']} stopped following its commands {abs(dt):.2f} s "
                                         f"{'later' if dt >= 0 else 'earlier'} and the hold lasted {t1 - t0:.1f} s."),
                              "facts": {"output_onset": t0, "departure_onset": first["t_start"], "seconds_between": round(dt, 3)}})
        # 4. Commanded? Mode changes, failsafes, controller events or fault codes around the onset.
        inc = _discovered(discovery, t0)
        if inc:  # the original recording was read around this onset: answer from it before asking anyone
            q = _from_discovery(discovery, inc, ch)
            questions.append(q)
            params = [f for f in inc.get("facts") or [] if f["kind"] == "parameter_reported" and f.get("near_onset") and f["t_rel_s"] <= 1.0]
            if params:
                names = ", ".join(sorted({f["parameter"] for f in params}))
                questions.append({"id": f"config:{ch}", "question": f"What does {names} do in this firmware, and who or what set it {abs(params[0]['t_rel_s']):.2f} s before the onset?",
                                  "status": "not_answerable",
                                  "needs": (f"The firmware documentation for {names}, and the ground-station log or the flight-controller's own log for that moment. "
                                            "The recording shows the controller reporting the value; it does not show who sent it"),
                                  "check_id": "CHK-CONTROLLER-LOGS"})
            elif not q["facts"]["controller_records_before_onset"]:
                questions.append({"id": f"autopilot:{ch}", "question": f"Did the controller itself log a failsafe, an error or an output limit that explains {v:g}{_unit(unit)}?",
                                  "status": "not_answerable",
                                  "needs": "The controller's own on-board log (error, event and failsafe records) and its output configuration: neither is in this recording",
                                  "check_id": "CHK-CONTROLLER-LOGS"})
        ev = [] if inc else [r for r in rows if r.get("signal") in EVENT_SIGNALS and t0 - EVENT_MARGIN_S <= r["t"] <= t1 + EVENT_MARGIN_S]
        if inc:
            pass
        elif ev:
            questions.append({"id": f"commanded:{ch}", "question": f"Did a mode change, failsafe or controller event come with the hold on {ch}?", "status": "answered",
                              "answer": "; ".join(f"{_clock(r['t'])} {r.get('code') or r.get('event_value') or r['signal']}" for r in ev[:6]) + ".",
                              "facts": {"events": len(ev)}})
        else:
            present = any(s in signals for s in EVENT_SIGNALS)
            questions.append({"id": f"commanded:{ch}", "question": f"Was the hold on {ch} commanded by a mode change, failsafe or other controller event?",
                              "status": "answered" if present else "not_answerable",
                              "answer": f"No controller event, reset or fault code within {EVENT_MARGIN_S:g} s of the hold." if present else None,
                              "needs": None if present else "The flight controller's own log (mode, failsafe, event and parameter records): none are in this case",
                              "check_id": None if present else "CHK-CONTROLLER-LOGS"})
        # 5. Did the actuator physically get there?
        fb = [r for r in rows if r.get("signal") in FEEDBACK_SIGNALS and (r.get("scope") or "") == ch]
        ev_c = next((e for e in (causal or {}).get("events") or [] if e["lead"]["scope"] == ch), None)
        path = next((m for m in (ev_c or {}).get("mechanisms") or [] if m["id"] == "output_path"), None)
        order_note = (path["evidence"][0]["text"] if path and path["status"] == "unlikely" and path.get("evidence") else None)
        questions.append({"id": f"physical:{ch}", "question": f"Did the {ch.split('_')[0]} actuator physically move to that position?",
                          "status": "answered" if fb else "not_answerable",
                          "answer": f"{len(fb)} feedback records for {ch} exist in the window; see Evidence." if fb else None,
                          "needs": None if fb else "Actuator position feedback (not in this case) or a check of the actuator and its linkage",
                          "check_id": None if fb else "CHK-ONSITE-ACTUATOR", "order_note": order_note})

    if tracking and not outputs:
        for d in tracking[:2]:
            ev = [r for r in rows if r.get("signal") in EVENT_SIGNALS and abs(r["t"] - d["t_start"]) <= EVENT_MARGIN_S]
            present = any(s in signals for s in EVENT_SIGNALS)
            questions.append({"id": f"commanded:{d['axis']}", "question": f"Did a mode change, failsafe or controller event come with {d['axis']} departing?",
                              "status": "answered" if present else "not_answerable",
                              "answer": ("; ".join(f"{_clock(r['t'])} {r.get('code') or r.get('event_value') or r['signal']}" for r in ev[:6]) + "." if ev
                                         else f"No controller event, reset or fault code within {EVENT_MARGIN_S:g} s.") if present else None,
                              "needs": None if present else "The controller's own mode and event log: none are in this case",
                              "check_id": None if present else "CHK-CONTROLLER-LOGS"})

    log = (discovery or {}).get("log")
    if log:
        questions += _from_log(log, reports)
    if not questions:
        return {"version": QUESTIONS_VERSION, "applicable": False, "questions": [], "causal": causal, "discovery": _discovery_summary(discovery),
                "lessons": lessons}
    established = [q["answer"] for q in questions if q["status"] in ("answered", "reported_done") and q.get("answer")]
    open_q = [q for q in questions if q["status"] == "not_answerable"]
    # Remote retrieval before travel: a log someone can send comes before a physical check.
    order = {"CHK-CONTROLLER-LOGS": 0, None: 1, "CHK-ONSITE-ACTUATOR": 2}
    nxt = min(open_q, key=lambda q: order.get(q.get("check_id"), 1)) if open_q else None
    status = (f"Cause {closure['investigation_status'].replace('cause_', '').replace('_', ' ')}" if closure and closure.get("investigation_status")
              else "Further investigation needed · No cause confirmed")
    # The Brief leads with these facts only when the data answers enough (today: output-hold findings); otherwise the
    # questions are still shown in Next action, and the Brief keeps its finding-led summary.
    return {"version": QUESTIONS_VERSION, "applicable": True, "causal": causal, "discovery": _discovery_summary(discovery), "lessons": lessons,
            "log": log, "brief": (bool(outputs) or bool(log)) and len(established) >= 2, "questions": questions, "established": established,
            "cannot_establish": [{"question": q["question"], "needs": q["needs"], "check_id": q.get("check_id")} for q in open_q],
            "next": None if not nxt else {"question": nxt["question"], "needs": nxt["needs"], "check_id": nxt.get("check_id"),
                                          "why_a_person": (f"Veyra read all {log['lines']} lines of the log; the answer is not in them. It needs the machine itself " + ("(a debug build, added logging or a debugger on the board)." if log.get("restarts") else
                                                              "(the data named under Needs: monitoring, diagnostics, a recording of the topics, or added logging).") if log else
                                                           f"Veyra read the whole original recording ({len((discovery.get('inventory') or {}).get('read_by_probes') or [])} streams: "
                                                           "controller mode and status, controller messages and configuration, operator inputs, outputs, battery); "
                                                           "this record is not in it. Only someone with the machine, its storage or the ground station can provide it." if discovery else
                                                           "That record is not in this case. Veyra cannot reach the machine; someone with access has to retrieve it, or inspect the part.")},
            "decision_status": status,
            "boundary": "Answered only from records in this case and known-good runs of the same machine. Facts, not a cause."}


def _discovery_summary(disc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not disc:
        return None
    inv = disc.get("inventory") or {}
    roles: Dict[str, int] = {}
    for st in inv.get("streams") or []:
        roles[st["role"]] = roles.get(st["role"], 0) + 1
    return {"recording": (disc.get("source") or {}).get("name"), "sha256": (disc.get("source") or {}).get("sha256"), "version": disc.get("version"),
            "streams": len(inv.get("streams") or []), "roles": roles, "read_by_probes": len(inv.get("read_by_probes") or []),
            "not_understood": [{"stream": x["stream"], "type": x["type"], "why": x["why"]} for x in inv.get("not_understood") or []][:20],
            "excluded": [{"stream": x["stream"], "reason": x["reason"]} for x in inv.get("excluded") or []],
            "gaps": disc.get("gaps") or [], "rules": disc.get("rules")}
