"""Questions the case's own data can answer, answered before Veyra asks anyone for more evidence.

A finding says what a rule saw ("rudder_rc4 output held 1900 for 17 s"). An engineer's next questions are concrete:
Was that value inside what the channel normally outputs (an end stop or limit)? Did other outputs change at the same
moment? What happened first? Was it commanded by a mode change, failsafe or controller event? Did the actuator
physically move there? This module answers each from the records already in the case (and known-good runs of the same
machine), or says exactly which record would answer it and why a person has to get it.

Facts only: no cause is named. Read-only and deterministic; computed when asked, so it also works for stored runs.
"""
from __future__ import annotations

from datetime import datetime, timezone
from statistics import median
from typing import Any, Dict, List, Optional

QUESTIONS_VERSION = "data-questions-v1"
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


def answer(run: Dict[str, Any], rows: List[Dict[str, Any]], baseline_runs: List[Dict[str, Any]], closure: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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
        ev = [r for r in rows if r.get("signal") in EVENT_SIGNALS and t0 - EVENT_MARGIN_S <= r["t"] <= t1 + EVENT_MARGIN_S]
        if ev:
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
        questions.append({"id": f"physical:{ch}", "question": f"Did the {ch.split('_')[0]} servo physically move to that position?",
                          "status": "answered" if fb else "not_answerable",
                          "answer": f"{len(fb)} feedback records for {ch} exist in the window; see Evidence." if fb else None,
                          "needs": None if fb else "Servo position feedback (not recorded on this aircraft) or a check of the servo and linkage",
                          "check_id": None if fb else "CHK-ONSITE-ACTUATOR"})

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

    if not questions:
        return {"version": QUESTIONS_VERSION, "applicable": False, "questions": []}
    established = [q["answer"] for q in questions if q["status"] == "answered" and q.get("answer")]
    open_q = [q for q in questions if q["status"] == "not_answerable"]
    # Remote retrieval before travel: a log someone can send comes before a physical check.
    order = {"CHK-CONTROLLER-LOGS": 0, None: 1, "CHK-ONSITE-ACTUATOR": 2}
    nxt = min(open_q, key=lambda q: order.get(q.get("check_id"), 1)) if open_q else None
    status = (f"Cause {closure['investigation_status'].replace('cause_', '').replace('_', ' ')}" if closure and closure.get("investigation_status")
              else "Further investigation needed · No cause confirmed")
    # The Brief leads with these facts only when the data answers enough (today: output-hold findings); otherwise the
    # questions are still shown in Next action, and the Brief keeps its finding-led summary.
    return {"version": QUESTIONS_VERSION, "applicable": True, "brief": bool(outputs) and len(established) >= 2, "questions": questions, "established": established,
            "cannot_establish": [{"question": q["question"], "needs": q["needs"], "check_id": q.get("check_id")} for q in open_q],
            "next": None if not nxt else {"question": nxt["question"], "needs": nxt["needs"], "check_id": nxt.get("check_id"),
                                          "why_a_person": "That record is not in this case. Veyra cannot reach the machine; someone with access has to retrieve it, or inspect the part."},
            "decision_status": status,
            "boundary": "Answered only from records in this case and known-good runs of the same machine. Facts, not a cause."}
