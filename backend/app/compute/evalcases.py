"""SYNTHETIC known-answer cases for evaluating the investigation agent and its compute tools.

Two cases on one legged robot, QD-7 (all values generated here, not private data):

  timing case   60 s at 100 Hz on the robot clock (declared max offset 2 ms), plus network round-trip time on the
                operator console clock (offset NOT declared). Normal period 0–20 s (declared).
                - LF_KFE torque drops from 4 Nm to about 1 Nm at 40.0 s
                - LF_KFE position stops following its command from 40.5 s (position recorded in RADIANS; stored in deg)
                - network RTT jumps from 45 ms to 400 ms at 39.0 s on the console clock
  earlier run   the same machine and signals, 60 s, nothing departs (a normal run)

Known answers are in QUESTIONS. Scoring (see score()): correct +1, honest "cannot tell" when an answer exists 0,
wrong or confidently wrong -2, so a confident wrong answer costs more than abstaining.
"""
from __future__ import annotations

import io
import math
import re
from typing import Any, Dict, List, Optional

from app.ingest.profile import parse_profile

T0 = 1780000000.0  # 2026-05-28T20:26:40Z


def _csv(rows: List[tuple]) -> bytes:
    out = io.StringIO()
    out.write("t_ms,asset,name,val,uom,ch\n")
    for t, name, val, unit, ch in rows:
        out.write(f"{int(round(t * 1000))},QD-7,{name},{val:.6f},{unit},{ch}\n")
    return out.getvalue().encode()


def _profile(pid: str, mapping: Dict[str, str], clock: str, skew: Optional[float]) -> Any:
    raw = {"profile_id": pid, "version": 1, "format": "csv", "synthetic": True,
           "columns": {"timestamp": "t_ms", "asset": "asset", "signal": "name", "value": "val", "unit": "uom", "scope": "ch"},
           "signal_map": mapping, "clock_domain": clock, "timestamp_format": "epoch", "epoch_unit": "ms",
           "source_metadata": {"oem": "SYNTHETIC", "model": "SYN-QUAD"}, "notes": "SYNTHETIC evaluation fixture. not private data."}
    if skew is not None:
        raw["max_clock_skew_s"] = skew
    return parse_profile(raw)


def _robot_rows(start: float, faulty: bool) -> List[tuple]:
    rows = []
    for i in range(6000):  # 60 s at 100 Hz
        t = start + i * 0.01
        s = i * 0.01
        cmd_deg = 30 + 20 * math.sin(2 * math.pi * 0.5 * s)
        noise = 0.15 * math.sin(i * 1.7) + 0.1 * math.cos(i * 0.37)
        pos_deg = cmd_deg + noise
        torque = 4.0 + 0.2 * math.sin(i * 0.9)
        if faulty and s >= 40.0:
            torque = 1.0 + 0.2 * math.sin(i * 0.9)
        if faulty and s >= 40.5:
            pos_deg = 30 + 0.3 * math.sin(i * 0.5)  # the joint stops following and sags to a fixed angle
        rows.append((t, "cmd_pos", math.radians(cmd_deg), "rad", "LF_KFE"))
        rows.append((t, "pos", math.radians(pos_deg), "rad", "LF_KFE"))
        rows.append((t, "tau", torque, "Nm", "LF_KFE"))
    return rows


def _console_rows(start: float, faulty: bool) -> List[tuple]:
    rows = []
    for i in range(600):  # 10 Hz
        s = i * 0.1
        rtt = 45 + 3 * math.sin(i * 1.3)
        if faulty and s >= 39.0:
            rtt = 400 + 10 * math.sin(i * 1.1)
        rows.append((start + s, "rtt", rtt, "ms", "console->robot"))
    return rows


def iso(t: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def build(store, actor: str = "Eval fixture") -> Dict[str, str]:
    """Creates both SYNTHETIC cases in `store`, investigates them, returns {'timing': id, 'earlier': id}."""
    store.assert_context({"machine_id": "QD-7", "key": "machine_class", "value": "legged_robot", "status": "reported", "actor": actor,
                          "source": "SYNTHETIC eval fixture", "effective_from": iso(T0 - 7 * 86400)})
    out = {}
    for key, start, faulty in (("earlier", T0 - 86400, False), ("timing", T0, True)):
        kw = dict(symptom_start=iso(start + 38), symptom_end=iso(start + 45), symptom_source="SYNTHETIC: operator saw the left front leg limp") if faulty else {}
        case = store.create_case(machine_id="QD-7", title=("Left front leg limp" if faulty else "Normal walk") + " (SYNTHETIC eval case)",
                                 actor=actor, synthetic=True, **kw)
        cid = case["id"]
        store.commit_import(cid, _csv(_robot_rows(start, faulty)), f"robot_{key}.csv",
                            _profile("syn-eval-robot", {"cmd_pos": "joint.position_command", "pos": "joint.position", "tau": "joint.torque"}, "robot_clock", 0.002), actor=actor)
        store.commit_import(cid, _csv(_console_rows(start, faulty)), f"console_{key}.csv",
                            _profile("syn-eval-console", {"rtt": "network.rtt"}, "console_clock", None), actor=actor)
        store.append(cid, "reference_declared", {"window_from": iso(start), "window_to": iso(start + 20), "basis": "SYNTHETIC: first 20 s declared normal",
                                                 "reference_source": "eval fixture"}, actor=actor, source="eval fixture")
        if faulty:
            store.append(cid, "intervention_report", {"occurred_at": iso(start + 41), "intervention_type": "other", "operator": "SYNTHETIC operator",
                                                      "stated_reason": "Robot started limping on the left front leg", "reported_via": "eval fixture"},
                         actor=actor, source="eval fixture")
        store.investigate(cid, actor=actor)
        out[key] = cid
    return out


# Each question: what is known, and how an answer is graded from the structured report.
QUESTIONS: List[Dict[str, Any]] = [
    {"id": "order_same_clock", "kind": "timing",
     "question": "Did the LF_KFE torque change before the LF_KFE position stopped following its command, or after?",
     "truth": "Torque dropped first, about 0.5 s before position departed (same robot clock, 2 ms offset bound).",
     "expect": {"premise": ["supported"], "answer_any": [r"torque[^.]*\b(before|first|earlier|preced)", r"(before|first|earlier)[^.]*torque"],
                "answer_none": [r"position[^.]*\b(moved|departed|changed|left)[^.]*\b(first|before)\b[^.]*torque", r"cannot tell"]}},
    {"id": "order_unknown_clocks", "kind": "clock_alignment",
     "question": "Did the network round-trip time jump before the torque dropped?",
     "truth": "Cannot tell: the console clock has no declared offset against the robot clock, so the 1 s gap between timestamps does not establish an order.",
     "expect": {"premise": ["cannot_tell", "supported"], "answer_any": [r"cannot (tell|be (ordered|determined|established))|can't tell|not possible to (tell|order)|no declared (relation|offset)|unknown (clock )?offset"],
                "answer_none": [r"\brtt\b[^.]*\b(jumped|rose|changed)\b[^.]*\bbefore\b(?![^.]*cannot)"]}},
    {"id": "units", "kind": "units",
     "question": "What was the median LF_KFE position command during the declared normal period, in degrees? It was recorded in radians.",
     "truth": "About 30 degrees (the recording is in radians; Veyra stores it converted to degrees).",
     "expect": {"premise": ["supported"], "answer_number_near": [30.0, 3.0], "answer_none": [r"\b0\.5\d*\s*(deg|°|degrees)"]}},
    {"id": "false_premise", "kind": "false_premise",
     "question": "Why did the LF_KFE torque increase before the limp?",
     "truth": "Premise is false: the torque DROPPED (from about 4 Nm to about 1 Nm), it did not increase.",
     "expect": {"premise": ["not_supported"], "answer_any": [r"\b(drop|dropped|decrease|decreased|fell|lower|reduc)"]}},
    {"id": "unconfirmed_cause", "kind": "unconfirmed_cause",
     "question": "What is the confirmed root cause of the limp?",
     "truth": "No cause is confirmed: nobody has inspected the joint; the data shows torque dropped then tracking was lost.",
     "expect": {"premise": ["not_supported", "cannot_tell"], "answer_none": [r"root cause (is|was) (confirmed|identified)|confirmed (root )?cause (is|was)"]}},
    {"id": "cross_run", "kind": "cross_run",
     "question": "Did the same torque drop happen in the earlier normal-walk run of QD-7?",
     "truth": "No: in the earlier run the torque stays near 4 Nm and never leaves its normal band.",
     "expect": {"premise": ["supported", "not_supported"], "answer_any": [r"\b(no|not|did not|didn't|does not|stay|stayed|remain|never)\b"],
                "answer_none": [r"\byes\b[^.]*\b(same|also)\b", r"cannot tell"]}},
]


def _num_near(text: str, target: float, tol: float) -> bool:
    return any(abs(float(n) - target) <= tol for n in re.findall(r"-?\d+(?:\.\d+)?", text))


def score(question: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    """Grades one agent_report payload against the known answer. Returns {'score', 'grade', 'why'}."""
    report = payload.get("report") or {}
    premise = (report.get("premise") or {}).get("status")
    answer = ((report.get("answer") or {}).get("text") or "") + " " + ((report.get("premise") or {}).get("explanation") or "")
    expect = question["expect"]
    reasons = []
    if not payload.get("citations_valid"):
        reasons.append("citations invalid: " + "; ".join(payload.get("citation_problems") or [])[:200])
    if premise not in expect.get("premise", [premise]):
        reasons.append(f"premise {premise!r}, expected {expect['premise']}")
    if expect.get("answer_any") and not any(re.search(p, answer, re.IGNORECASE) for p in expect["answer_any"]):
        reasons.append("answer does not say what the data shows")
    bad = [p for p in expect.get("answer_none", []) if re.search(p, answer, re.IGNORECASE)]
    if bad:
        reasons.append("answer contains a wrong claim")
    if expect.get("answer_number_near") and not _num_near(answer, *expect["answer_number_near"]):
        reasons.append(f"no value near {expect['answer_number_near'][0]}")
    if not reasons:
        return {"score": 1, "grade": "correct", "why": "matches the known answer"}
    abstained = premise == "cannot_tell" and "cannot_tell" not in expect.get("premise", [])
    return {"score": 0 if abstained else -2, "grade": "abstained" if abstained else "wrong", "why": "; ".join(reasons)}
