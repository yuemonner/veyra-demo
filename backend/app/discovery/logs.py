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

LOGS_VERSION = "log-characterization-v2"
BLOCK_GAP_LINES = 4         # boot / reset lines within this many lines of each other are one boot
AFTER_LINES = 8             # lines after a boot that may still carry its reset report
BEFORE_LINES = 8            # lines shown before a restart
PROXIMATE_LINES = 3         # the last notable lines before a failure: what was going wrong right then
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
# What failed when the machine did NOT restart: a stop, an aborted task, a process that died. Each kind has the checks
# that separate its possible causes. The log names the symptom; these checks name what would explain it.
INCIDENT_KINDS = ("failsafe", "task_failed", "process_crash", "out_of_memory", "hard_fault", "assert", "stack_overflow")
KIND_CHECKS: Dict[str, List[Dict[str, Any]]] = {
    "dropped_data": [{"question": "Was the consumer too slow, or did the producer burst?", "topic": "load",
                      "check": "Record per-process CPU and the queue depth / callback duration of the component that dropped data, up to the incident",
                      "why": "A full queue means the reader fell behind; CPU and callback time say whether it was starved or blocked."}],
    "overrun": [{"question": "Was the processor overloaded when the loop missed its rate?", "topic": "load",
                 "check": "Record per-process CPU and memory, and the loop's own execution time, over the span where it missed its rate",
                 "why": "A control loop that misses its rate is usually starved by another process, or blocked waiting for an input."}],
    "tracking_error": [{"question": "Was the axis blocked, or did its drive stop getting commands?", "topic": "axis",
                        "check": "Record commanded vs actual position and motor current of that axis, and the drive's fieldbus error counters, over the seconds before the error",
                        "why": "High current with no motion points to a mechanical block or collision; commands that stop or jump point to communication or the controller."}],
    "fieldbus": [{"question": "Did the drive lose communication before the error?", "topic": "fieldbus",
                  "check": "Read the fieldbus diagnostics for that device (EtherCAT working-counter / lost-frame counters, CAN error counters) and check its cable and connector",
                  "why": "Missed frames make a drive hold or jump its position, which a controller then sees as a following error."}],
    "timeout": [{"question": "Did the sensor stop sending, or did the software stop receiving?", "topic": "rate",
                 "check": "Compare the publish rate on the driver side with what the subscriber received (header stamps of the topic in a recording, or the driver's own counters)",
                 "why": "Separates a sensor / cable / driver fault from a starved or blocked receiver."}],
    "out_of_memory": [{"question": "Did the killed process grow steadily (a leak) or jump at once?", "topic": "memory",
                       "check": "Record the process's memory (RSS) over time up to the kill, and the system's free memory",
                       "why": "A steady climb points to a leak or an unbounded buffer; a jump to one large allocation or a burst of input."}],
    "process_crash": [{"question": "Was the process killed from outside or did it crash in its own code?", "topic": "core dump",
                       "check": "Read the exit signal (-9 = killed, usually by the kernel's out-of-memory killer; -11 = segfault) and the core dump or backtrace if one exists",
                       "why": "A kill points at memory pressure; a segfault or abort points at the process's own code."}],
    "task_failed": [{"question": "What did the component that gave up see before it gave up?", "topic": "inputs",
                     "check": "Record the inputs of the component that failed (sensor topics it subscribes to, with their stamps) over the same span",
                     "why": "A planner or controller usually fails because an input stopped or went stale, not on its own."}],
    "failsafe": [{"question": "Which condition triggered the stop?", "topic": "safety",
                  "check": "Read the safety node's trigger condition and the input it watched at the stop (its own log line names it when it prints a reason)",
                  "why": "The stop is a protection reacting to something; the trigger names what."}],
    "link_loss": [{"question": "Was the link lost at the radio or at the software?", "topic": "link",
                   "check": "Record link quality (RSSI, packet loss) and the ground-station side log over the same span",
                   "why": "Separates range / interference from a process on either side that stopped sending."}],
}
PRIMARY_ORDER = ("assert", "hard_fault", "stack_overflow", "out_of_memory", "brownout", "watchdog")
KIND_TO_CAUSE = {"assert": "assert", "hard_fault": "hard_fault", "stack_overflow": "stack_overflow", "out_of_memory": "out_of_memory",
                 "brownout": "brownout", "watchdog_reset": "watchdog"}


# Topic of a check -> words that show an engineer already did it, when they appear with 'ruled out' / 'checked' language.
DONE_TOPICS = {"stack": r"stack", "debugger": r"debugger|gdb|j-?link|halt(ed)? the", "voltage": r"voltage|battery|brown-?out|supply",
               "heap": r"heap|memory", "assert": r"assert", "fault": r"fault regist|cfsr|hfsr", "reset cause": r"reset[- ]cause|rcc_csr",
               "console": r"console", "load": r"cpu|load|callback", "rate": r"rate|\bhz\b|publish", "memory": r"memory|rss|leak",
               "core dump": r"core ?dump|backtrace|segfault", "axis": r"current|axis|collision|mechanical", "fieldbus": r"ethercat|fieldbus|\bcan\b|cable|connector", "inputs": r"input|sensor|topic", "safety": r"safety|e-?stop", "link": r"rssi|link|radio"}
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


def _actor(p: Line) -> Optional[str]:
    """The component a line is about: the process a kill / crash line names, else the subsystem that printed it."""
    return p.target or p.subsystem


def _incident(lines: List[Line]) -> Optional[Dict[str, Any]]:
    """When the machine did not restart: the first line where something failed, what came before it (first sign per
    component, in order) and what came after (consequences, recovery). Order, not cause: an earlier warning is a lead."""
    first = next((i for i, p in enumerate(lines) if p.kind in INCIDENT_KINDS), None)
    if first is None:
        return None
    inc = lines[first]
    before = [p for p in lines[:first] if p.level in NOTABLE_LEVELS or p.kind]
    first_signs: Dict[str, Dict[str, Any]] = {}
    for p in before:
        a = _actor(p) or "unnamed"
        f = first_signs.setdefault(a, {"component": a, "first_line": p.n, "t_ns": p.t_ns, "text": p.text, "level": p.level, "kinds": [], "lines": []})
        f["lines"].append(p.n)
        if p.kind and p.kind not in f["kinds"]:
            f["kinds"].append(p.kind)
    proximate: List[str] = []  # components of the last notable lines before the failure, closest first
    for p in reversed(before):
        a = _actor(p) or "unnamed"
        if a not in proximate:
            proximate.append(a)
        if len([q for q in before if q.n >= p.n]) >= PROXIMATE_LINES:
            break
    for a, f in first_signs.items():
        f["last_line"] = f["lines"][-1]
        f["proximate"] = a in proximate
    after = [{"line": p.n, "t_ns": p.t_ns, "level": p.level, "kind": p.kind, "component": _actor(p), "text": p.text}
             for p in lines[first + 1:] if p.level in NOTABLE_LEVELS or p.kind]
    # One component across the whole log: its warnings before, and what later happened to it (killed, died, respawned).
    threads = []
    for a, f in first_signs.items():
        later = [x for x in after if x["component"] == a and x["kind"] in ("out_of_memory", "process_crash", "hard_fault", "recovered")]
        if later:
            threads.append({"component": a, "before": f["lines"], "proximate": a in proximate, "later": [{"line": x["line"], "kind": x["kind"], "text": x["text"]} for x in later]})
    lead_s = None
    ordered = sorted(first_signs.values(), key=lambda f: f["first_line"])
    if ordered and ordered[0]["t_ns"] is not None and inc.t_ns is not None:
        lead_s = round((inc.t_ns - ordered[0]["t_ns"]) / 1e9, 3)
    return {"line": inc.n, "t_ns": inc.t_ns, "kind": inc.kind, "component": _actor(inc), "text": inc.text,
            "first_signs": ordered, "lead_s": lead_s, "proximate": proximate,
            "just_before": [{"line": p.n, "component": _actor(p), "level": p.level, "kind": p.kind, "text": p.text} for p in before[-PROXIMATE_LINES:]], "after": after[:12], "threads": threads,
            "rule": "Earliest line where something failed (stop, aborted task, process died, out of memory, fault). Components are listed by their first warning before it."}


def characterize(log: TextLog, *, source_name: Optional[str] = None) -> Dict[str, Any]:
    lines = log.lines
    restarts = _restarts(lines)
    incident = None if restarts else _incident(lines)
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
    if restarts and not kinds.get("stack_overflow"):
        absences.append({"what": "No stack-overflow message anywhere in the log",
                         "caveat": "Only meaningful if the firmware's stack-overflow check is enabled and prints; otherwise an overflow is silent."})
    if restarts and not kinds.get("brownout"):
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
    if incident:
        tokens = set(tokens)
        tokens |= {f"incident:{incident['kind']}"} | {f"fault:{k}" for k in kinds if k in FAULT_KINDS}
        tokens |= {f"at:{t['component']}:{x['kind']}" for t in incident["threads"] for x in t["later"] if x["kind"] != "recovered"}
        tokens |= {f"sign:{f['component']}:{k}" for f in incident["first_signs"] for k in f["kinds"]}
        tokens = sorted(tokens)
    checks, seen = [], set()
    for r in restarts[-3:]:
        for c in ([x["cause"] for x in r["causes"]] or ["unknown"]):
            for chk in RESET_CAUSES[c]["checks"]:
                if chk["check"] not in seen:
                    seen.add(chk["check"])
                    checks.append({**chk, "for": RESET_CAUSES[c]["label"], "cause": c})
    if incident:
        wanted = [k for f in incident["first_signs"] for k in f["kinds"]] + [incident["kind"]] + [x["kind"] for x in incident["after"] if x["kind"]]
        for k in wanted:
            for chk in KIND_CHECKS.get(k) or RESET_CAUSES.get(KIND_TO_CAUSE.get(k, ""), {}).get("checks", []):
                if chk["check"] not in seen:
                    seen.add(chk["check"])
                    checks.append({**chk, "for": k.replace("_", " "), "cause": k})
        # Order: what was going wrong right before the failure (closest component first, and what later happened to it),
        # then the failure itself, then earlier and later signs. Order is a lead, not a cause.
        rank: Dict[str, int] = {}
        for i, a in enumerate(incident["proximate"]):
            f = next(x for x in incident["first_signs"] if x["component"] == a)
            for t in incident["threads"]:
                if t["component"] == a:
                    for x in t["later"]:
                        rank.setdefault(x["kind"], i * 10)
            for k in f["kinds"]:
                rank.setdefault(k, i * 10 + 1)
        rank.setdefault(incident["kind"], 50)
        checks.sort(key=lambda c: rank.get(c["cause"], 90))
        # Some checks the log itself already answers: a process that died with signal 9 right after the kernel's
        # out-of-memory killer named it was killed for memory, not crashed in its own code.
        import re
        oom = {p.target: p for p in lines if p.kind == "out_of_memory" and p.target}
        for p in lines:
            if p.kind == "process_crash" and p.target in oom and re.search(r"exit code -9\b|signal 9\b|SIGKILL", p.text):
                for c in checks:
                    if c["cause"] == "process_crash" and "answered" not in c:
                        c["answered"] = (f"Killed from outside: line {oom[p.target].n} shows the kernel's out-of-memory killer ending {p.target}, "
                                         f"and line {p.n} shows it exited with signal 9.")
        for p in lines:
            if p.kind == "process_crash" and re.search(r"segfault|segmentation fault|SIGSEGV|/SEGV|SIGABRT|/ABRT|terminate called|core dumped|code=dumped", p.text, re.I):
                for c in checks:
                    if c["cause"] == "process_crash" and "answered" not in c:
                        c["answered"] = (f"Crashed in its own code: line {p.n} shows {p.target or 'the process'} ending on a "
                                         f"{'segmentation fault' if re.search(r'seg', p.text, re.I) else 'abort'}. A core dump or backtrace would name the function.")
        for p in lines:
            m = re.search(r"(?:stop|fail-?safe)\w*\s*(?:engaged|triggered|activated)?\s*[:(]\s*([^)]{3,120})|(?:because|due to|reason[:=])\s*(.{3,120})", p.text, re.I) if p.kind == "failsafe" else None
            if m:
                reason = (m.group(1) or m.group(2)).strip().rstrip(".)")
                for c in checks:
                    if c["cause"] == "failsafe" and "answered" not in c:
                        c["answered"] = (f"The stop states its trigger at line {p.n}: “{reason}”. That is what the protection saw; "
                                         "the other checks are about why it saw it.")
                break
    summary = []
    if incident:
        summary.append(f"No restart. The first failure is at line {incident['line']}: {incident['text'][:100]}")
        if incident["first_signs"]:
            f0 = incident["first_signs"][0]
            summary.append(f"First warning before it: {f0['component']} at line {f0['first_line']}"
                           + (f", {incident['lead_s']:g} s earlier" if incident["lead_s"] is not None else ""))
        if incident["just_before"]:
            summary.append("Right before it: " + "; ".join(f"line {x['line']} {x['component'] or ''}".rstrip() for x in incident["just_before"]))
        for t in incident["threads"]:
            summary.append(("" if t["proximate"] else "Also: ") + f"{t['component']} warned before the failure (line{'s' if len(t['before']) > 1 else ''} {', '.join(map(str, t['before'][:4]))}) and later: "
                           + "; ".join(f"{k.replace('_', ' ')} (line {min(x['line'] for x in t['later'] if x['kind'] == k)})"
                                     for k in dict.fromkeys(x["kind"] for x in t["later"])))
    if restarts:
        last = restarts[-1]
        summary.append(f"{len(restarts)} restart{'s' if len(restarts) != 1 else ''} in the log; the last at line {last['line']}: "
                       + (RESET_CAUSES[last["primary_cause"]]["label"].split(":")[0].lower() + " stated by the log"
                          if last["causes"] else "no cause stated"))
    faults: Dict[str, int] = {}
    for sg in signatures:
        if sg["kind"] in FAULT_KINDS:
            key = sg["kind"].replace("_", " ") + (" at " + sg["location"] if sg["location"] else "")
            faults[key] = faults.get(key, 0) + sg["count"]
    if faults:
        summary.append("Faults reported: " + "; ".join(f"{k} ×{n}" for k, n in list(faults.items())[:4]))
    return {"version": LOGS_VERSION, "source": source_name, "lines": len(lines), "clock": log.clock, "timestamped_share": log.timestamped_share,
            "notes": log.notes, "levels": levels, "kinds": kinds, "subsystems": dict(sorted(subsystems.items(), key=lambda kv: -kv[1])[:12]),
            "restarts": restarts, "incident": incident, "signatures": signatures[:30], "absences": absences, "gaps": gaps, "checks": checks,
            "signature_tokens": tokens, "summary": summary,
            "boundary": "Read from the log text only. A message is what the software reported; it is not a measurement and not a cause."}
