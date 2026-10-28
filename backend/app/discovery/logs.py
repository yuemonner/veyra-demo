"""What a text log says about an incident, before anyone reads it line by line.

Input: a parsed text log (app/ingest/textlog.py). Output, facts only, each with its line numbers:
- RESTARTS: clusters of boot / reset lines. For each, the reset cause the log itself states (watchdog, failed assert,
  hard fault, stack overflow, out of memory, brown-out) and the lines just before it. When a fault and a watchdog reset
  appear together, the fault is listed first: an assert or a fault stops the firmware and the watchdog then resets it.
- SIGNATURES: warning / error / fault messages as templates (numbers removed), how often, first and last line, and
  whether they occur just before a restart.
- ABSENCES worth stating, with their caveat (no stack-overflow message: only meaningful if the firmware prints one).
- GAPS: no timestamps (order only), no measurements (the log shows what the software reported, not what the machine did).
- CHECKS that would separate the possible causes of each restart (generic embedded / robot knowledge, RESET_CAUSES).
- A FAILURE SIGNATURE (tokens) used to find earlier lessons (app/engine/lessons.py).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.ingest.textlog import FAULT_KINDS, Line, TextLog

LOGS_VERSION = "log-characterization-v1"
BLOCK_GAP_LINES = 4         # boot / reset lines within this many lines of each other are one boot
AFTER_LINES = 8             # lines after a boot that may still carry its reset report
BEFORE_LINES = 8            # lines shown before a restart
NOTABLE_LEVELS = {"WARNING", "ERROR", "FATAL"}

RESET_CAUSES: Dict[str, Dict[str, Any]] = {
    "assert": {"label": "Failed assertion: the software stopped itself at a check that failed",
               "checks": [{"question": "Which value failed the check at the assert location?", "topic": "assert", "check": "Read the code at the assert location and capture the values it checks (debug build or added logging)",
                           "why": "An assert names the exact check that failed; the values say why it failed."}]},
    "hard_fault": {"label": "Hard fault / crash: the processor hit an invalid operation",
                   "checks": [{"question": "Which instruction faulted, and on what kind of access?", "topic": "fault", "check": "Read the fault status registers and the program counter / link register at the fault (fault handler dump or debugger)",
                               "why": "They name the faulting instruction and the kind of access (bus, memory, usage)."}]},
    "stack_overflow": {"label": "Stack overflow: a task used more stack than it has",
                       "checks": [{"question": "Which task ran out of stack?", "topic": "stack", "check": "Log per-task stack high-water marks up to the failure", "why": "Shows which task ran out and by how much."}]},
    "out_of_memory": {"label": "Out of memory: an allocation failed",
                      "checks": [{"question": "Which allocation failed?", "topic": "heap", "check": "Log free heap and the failing allocation's size and caller", "why": "Separates a leak from one large allocation."}]},
    "watchdog": {"label": "Watchdog reset: a monitored task stopped running (hang, busy wait, deadlock or starvation)",
                 "checks": [{"question": "Where was each task waiting when the watchdog fired?", "topic": "debugger", "check": "Halt the controller with a debugger while it hangs (or log the watchdog's task status) and record where each task is waiting",
                             "why": "A watchdog reset only says a task stopped; where it was waiting names the mechanism (a peripheral wait loop, a lock, a starved task)."},
                            {"question": "Was the processor overloaded, or a task's stack exhausted, before the reset?", "topic": "stack", "check": "Log CPU load and per-task stack high-water marks up to the reset",
                             "why": "Separates starvation (load near 100 %) and stack overflow from a hang."}]},
    "brownout": {"label": "Brown-out / low voltage: the supply dropped",
                 "checks": [{"question": "Did the supply dip at the reset?", "topic": "voltage", "check": "Record battery voltage and current around the reset, and the reset-cause register (brown-out flag) at boot",
                             "why": "A supply dip resets the processor without any software message."}]},
    "unknown": {"label": "Restart with no stated cause",
                "checks": [{"question": "What kind of reset was it?", "topic": "reset cause", "check": "Read and log the reset-cause register at every boot (power-on, pin, watchdog, brown-out, software)",
                            "why": "Without it a restart cannot be told apart from a power or watchdog reset."},
                           {"question": "What happened just before the restart?", "topic": "console", "check": "Capture the console continuously up to the next restart", "why": "The lines just before a restart are often the only trace."}]},
}
PRIMARY_ORDER = ("assert", "hard_fault", "stack_overflow", "out_of_memory", "brownout", "watchdog")
KIND_TO_CAUSE = {"assert": "assert", "hard_fault": "hard_fault", "stack_overflow": "stack_overflow", "out_of_memory": "out_of_memory",
                 "brownout": "brownout", "watchdog_reset": "watchdog"}


# Topic of a check -> words that show an engineer already did it, when they appear with 'ruled out' / 'checked' language.
DONE_TOPICS = {"stack": r"stack", "debugger": r"debugger|gdb|j-?link|halt(ed)? the", "voltage": r"voltage|battery|brown-?out|supply",
               "heap": r"heap|memory", "assert": r"assert", "fault": r"fault regist|cfsr|hfsr", "reset cause": r"reset[- ]cause|rcc_csr",
               "console": r"console"}
_DONE = r"(ruled out|rule out|excluded|checked|verified|not the cause|is not|was not|no )"


def already_done(check: Dict[str, Any], reports: List[str]) -> Optional[str]:
    """The sentence of an engineer's report that says this check's topic was already examined, if any."""
    import re
    rx = DONE_TOPICS.get(check.get("topic") or "")
    if not rx:
        return None
    for text in reports:
        for sentence in re.split(r"(?<=[.!?])\s+|\n", text or ""):
            if re.search(rx, sentence, re.I) and re.search(_DONE, sentence, re.I):
                return sentence.strip()[:300]
    return None


def _cite(lines: List[Line]) -> List[int]:
    return [p.n for p in lines]


def _restarts(lines: List[Line]) -> List[Dict[str, Any]]:
    idx = [i for i, p in enumerate(lines) if p.kind in ("boot", "watchdog_reset")]
    blocks: List[List[int]] = []
    for i in idx:
        if blocks and i - blocks[-1][-1] <= BLOCK_GAP_LINES:
            blocks[-1].append(i)
        else:
            blocks.append([i])
    out = []
    for b in blocks:
        start, end = b[0], min(len(lines) - 1, b[-1] + AFTER_LINES)
        inside = lines[start:end + 1]
        before = [p for p in lines[max(0, start - BEFORE_LINES):start]]
        evidence = [p for p in inside + before if p.kind in KIND_TO_CAUSE]
        causes = []
        for cause in PRIMARY_ORDER:
            hits = [p for p in evidence if KIND_TO_CAUSE.get(p.kind) == cause]
            if hits:
                causes.append({"cause": cause, "label": RESET_CAUSES[cause]["label"], "lines": _cite(hits)[:6],
                               "text": [p.text for p in hits][:3], "locations": sorted({p.location for p in hits if p.location})})
        primary = causes[0]["cause"] if causes else "unknown"
        note = None
        if len(causes) > 1 and any(c["cause"] == "watchdog" for c in causes) and primary != "watchdog":
            note = (f"The {primary.replace('_', ' ')} comes first: it stops the firmware, and the watchdog then resets it. "
                    "The watchdog message is a consequence, not a second cause.")
        if start < 3 and not causes:
            continue  # the capture starts with a normal boot: not a restart
        out.append({"line": lines[start].n, "t_ns": lines[start].t_ns, "at_file_start": start < 3,
                    "boot_lines": _cite([lines[i] for i in b])[:6], "causes": causes, "primary_cause": primary, "note": note,
                    "before": [{"line": p.n, "level": p.level, "text": p.text} for p in before if p.text.strip()][-BEFORE_LINES:]})
    return out


def characterize(log: TextLog, *, source_name: Optional[str] = None) -> Dict[str, Any]:
    lines = log.lines
    restarts = _restarts(lines)
    restart_lines = {r["line"] for r in restarts}
    near_restart = set()
    for r in restarts:
        near_restart |= {b["line"] for b in r["before"]}
    sigs: Dict[str, Dict[str, Any]] = {}
    for p in lines:
        if p.level in NOTABLE_LEVELS or p.kind in FAULT_KINDS or p.kind in ("watchdog_reset", "brownout", "link_loss", "failsafe", "timeout", "dropped_data"):
            s = sigs.setdefault(p.template, {"template": p.template, "level": p.level, "kind": p.kind, "location": p.location, "subsystem": p.subsystem,
                                             "count": 0, "first_line": p.n, "last_line": p.n, "example": p.text, "before_restart": False})
            s["count"] += 1
            s["last_line"] = p.n
            s["before_restart"] = s["before_restart"] or p.n in near_restart
    signatures = sorted(sigs.values(), key=lambda s: (-(s["kind"] in FAULT_KINDS or s["kind"] == "watchdog_reset"), -s["before_restart"], s["first_line"]))
    levels: Dict[str, int] = {}
    kinds: Dict[str, int] = {}
    subsystems: Dict[str, int] = {}
    for p in lines:
        levels[p.level or "none"] = levels.get(p.level or "none", 0) + 1
        if p.kind:
            kinds[p.kind] = kinds.get(p.kind, 0) + 1
        if p.subsystem:
            subsystems[p.subsystem] = subsystems.get(p.subsystem, 0) + 1
    absences = []
    if not kinds.get("stack_overflow"):
        absences.append({"what": "No stack-overflow message anywhere in the log",
                         "caveat": "Only meaningful if the firmware's stack-overflow check is enabled and prints; otherwise an overflow is silent."})
    if not kinds.get("brownout"):
        absences.append({"what": "No low-voltage or brown-out message",
                         "caveat": "A supply dip usually resets the processor without printing anything; only a voltage record or the reset-cause register can rule it out."})
    if restarts and not any(c for r in restarts for c in r["causes"]):
        absences.append({"what": "No reset cause is printed at any restart", "caveat": "The reset-cause register would say which reset it was."})
    gaps = []
    if log.clock == "line_order":
        gaps.append({"missing": "Timestamps", "means": "Only the order of lines is known: no duration, rate or alignment with other records can be read."})
    gaps.append({"missing": "Measurements (telemetry)", "means": "The log shows what the software reported, not what the machine did: motion, battery and actuator state are not in it."})
    primary = [r["primary_cause"] for r in restarts]
    tokens = sorted({f"reset:{c}" for c in primary}
                    | {f"fault:{c['cause']}" for r in restarts for c in r["causes"]}
                    | {f"at:{loc}" for r in restarts for c in r["causes"] for loc in c["locations"]}
                    | {f"msg:{s['template']}" for s in signatures[:8] if s["before_restart"] or s["kind"] in FAULT_KINDS})
    checks, seen = [], set()
    for r in restarts[-3:]:
        for c in ([x["cause"] for x in r["causes"]] or ["unknown"]):
            for chk in RESET_CAUSES[c]["checks"]:
                if chk["check"] not in seen:
                    seen.add(chk["check"])
                    checks.append({**chk, "for": RESET_CAUSES[c]["label"], "cause": c})
    summary = []
    if restarts:
        last = restarts[-1]
        summary.append(f"{len(restarts)} restart{'s' if len(restarts) != 1 else ''} in the log; the last at line {last['line']}: "
                       + (RESET_CAUSES[last["primary_cause"]]["label"].split(":")[0].lower() + " stated by the log"
                          if last["causes"] else "no cause stated"))
    faults = [s for s in signatures if s["kind"] in FAULT_KINDS]
    if faults:
        summary.append("Faults reported: " + "; ".join(f"{s['kind'].replace('_', ' ')}{' at ' + s['location'] if s['location'] else ''} ×{s['count']}" for s in faults[:4]))
    return {"version": LOGS_VERSION, "source": source_name, "lines": len(lines), "clock": log.clock, "timestamped_share": log.timestamped_share,
            "notes": log.notes, "levels": levels, "kinds": kinds, "subsystems": dict(sorted(subsystems.items(), key=lambda kv: -kv[1])[:12]),
            "restarts": restarts, "signatures": signatures[:30], "absences": absences, "gaps": gaps, "checks": checks,
            "signature_tokens": tokens, "summary": summary,
            "boundary": "Read from the log text only. A message is what the software reported; it is not a measurement and not a cause."}
