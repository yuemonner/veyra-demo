"""Text logs: console output, serial logs, rosout / syslog exports, PX4 / ArduPilot / Crazyflie consoles, journald text.

Most field incidents arrive as text, not telemetry: a console capture after a reboot, a syslog export, a ticket with a
pasted log. This reads any such file without a per-robot parser:

- TIME: per line, the first recognised timestamp (ISO 8601, ROS 1 / ROS 2 '[1536693598.70]' epoch, syslog 'Mar 18
  21:43:36', kernel / uptime '[  123.456]'). Lines without one inherit the last time seen. A file with no timestamps at
  all keeps its LINE ORDER only: rows are placed 1 ms apart from an anchor and the clock is named 'line_order', so no
  duration or rate is ever read from them.
- LEVEL: ERROR / WARNING / INFO / DEBUG / FATAL from the usual spellings ('[ WARN]', 'W/', 'ERR:', '[WARNING]' suffix...).
- SOURCE: the subsystem prefix ('SYS:', '[commander]', 'kernel:', '[node]') and any source location ('uart_syslink.c:373').
- KIND: what the line reports, from a fixed vocabulary (KINDS below): boot banner, watchdog reset, assert, hard fault,
  stack overflow, out of memory, brown-out / low voltage, timeout, link loss, dropped data, failsafe.
- TEMPLATE: the message with numbers and hex replaced ('Dropped 92 events (seq=101)' -> 'Dropped # events (seq=#)'),
  so the same message is counted as one.

Rows go into Veyra's canonical import like any other source: a boot / reset line becomes controller.reset, an assert or
fault becomes fault.code (value = the template, scope = the source location), anything else controller.event.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

VERSION = "text-log-v1"
MAX_LINES = 200_000
TEXT_SUFFIXES = (".log", ".txt", ".out", ".console")
LINE_ORDER_STEP_NS = 1_000_000

MONTHS = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}
_ISO = re.compile(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:[.,](\d{1,9}))?(Z|[+-]\d{2}:?\d{2})?")
_EPOCH = re.compile(r"\[\s*(1[0-9]{9}(?:\.\d{1,9})?)\s*\]")               # ROS 1 / ROS 2 wall time in brackets
_SYSLOG = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?", re.I)
_UPTIME = re.compile(r"^\s*\[\s*(\d{1,7}\.\d{3,9})\s*\]")                  # kernel / uptime seconds at line start
_LEVEL = [
    ("FATAL", re.compile(r"\b(FATAL|PANIC|EMERG(ENCY)?|CRIT(ICAL)?)\b|^\s*F/", re.I)),
    ("ERROR", re.compile(r"\b(ERROR|ERR)\b|\[\s*ERROR\s*\]|^\s*E/", re.I)),
    ("WARNING", re.compile(r"\b(WARN(ING)?)\b|\[\s*WARN(ING)?\s*\]|^\s*W/", re.I)),
    ("DEBUG", re.compile(r"\b(DEBUG|DBG|TRACE)\b|^\s*D/", re.I)),
    ("INFO", re.compile(r"\b(INFO|NOTICE)\b|^\s*I/", re.I)),
]
_LOCATION = re.compile(r"((?:[\w.-]+/)*[\w.-]+\.(?:c|cc|cpp|cxx|h|hpp|py|rs|go|java|ts|js)):(\d+)")
_SUBSYSTEM = re.compile(r"^\s*(?:\[[^\]]*\]\s*)*(?:\[\s*([A-Za-z_][\w./-]{1,40})\s*\]|([A-Z][A-Z0-9_]{1,15}):\s)")
_TEMPLATE_NUM = re.compile(r"0x[0-9a-fA-F]+|\b\d+(?:\.\d+)?\b")

# What a line reports. Order matters: the first match wins.
KINDS: List[Tuple[str, re.Pattern]] = [
    ("watchdog_reset", re.compile(r"watch\s*dog|\bIWDG\b|\bWWDG\b|\bWDT\b", re.I)),
    ("stack_overflow", re.compile(r"stack\s*(over\s*flow|smash|exhaust)|stackoverflow", re.I)),
    ("hard_fault", re.compile(r"hard\s*fault|bus\s*fault|usage\s*fault|mem\s*manage|segmentation fault|SIGSEGV|SIGABRT|kernel panic|core dumped", re.I)),
    ("assert", re.compile(r"assert(ion)?\s*(failed|failure)|\bASSERT\b", re.I)),
    ("out_of_memory", re.compile(r"out of memory|\bOOM\b|malloc fail|allocation fail|heap (exhausted|full)", re.I)),
    ("brownout", re.compile(r"brown-?\s*out|under-?voltage|low (battery|voltage)|voltage (drop|low)|battery (critical|low)", re.I)),
    ("boot", re.compile(r"is up and running|\bbooting\b|\bboot(ed|ing)? (complete|up|done)\b|starting up|system start|firmware (version|build)|\bbuild\s*\d|bootloader|reset reason|reboot(ing|ed)?\b|power[- ]on", re.I)),
    ("link_loss", re.compile(r"(connection|link|radio|gcs|ground station|heartbeat|telemetry)[^.]{0,30}\b(lost|loss|timeout|timed out|dropped|disconnect)", re.I)),
    ("failsafe", re.compile(r"fail-?safe|emergency stop|\bkill(ed)?\b|e-?stop", re.I)),
    ("timeout", re.compile(r"time(d)?\s*out|deadline (missed|exceeded)", re.I)),
    ("dropped_data", re.compile(r"\bdropped\b|overrun|checksum|crc (error|fail)|queue (full|overflow)|buffer (full|overflow)|overflow", re.I)),
]
RESET_KINDS = {"watchdog_reset", "boot"}
FAULT_KINDS = {"assert", "hard_fault", "stack_overflow", "out_of_memory"}


@dataclass
class Line:
    n: int                      # 1-based line number in the file
    text: str
    t_ns: Optional[int]         # parsed time (ns since epoch), or None
    level: Optional[str]
    kind: Optional[str]
    subsystem: Optional[str]
    location: Optional[str]
    template: str


@dataclass
class TextLog:
    lines: List[Line]
    clock: str                  # 'iso' | 'epoch' | 'syslog' | 'uptime' | 'line_order' | mixed names
    timestamped_share: float
    anchor_ns: int
    truncated: bool = False
    year_assumed: Optional[int] = None
    notes: List[str] = field(default_factory=list)


def looks_like_text(path: Path) -> bool:
    try:
        head = Path(path).read_bytes()[:4096]
    except OSError:
        return False
    if not head or b"\0" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        try:
            head.decode("latin-1")
        except UnicodeDecodeError:
            return False
    printable = sum(1 for b in head if b in (9, 10, 13) or 32 <= b < 127 or b >= 128)
    return printable / len(head) > 0.95 and b"\n" in head


def _time(text: str, year: int) -> Tuple[Optional[int], Optional[str]]:
    m = _ISO.search(text)
    if m:
        date, hh, mm, ss, frac, tz = m.groups()
        dt = datetime.strptime(f"{date} {hh}:{mm}:{ss}", "%Y-%m-%d %H:%M:%S")
        if tz and tz != "Z":
            sign = 1 if tz[0] == "+" else -1
            tz = tz.replace(":", "")
            offset = sign * (int(tz[1:3]) * 3600 + int(tz[3:5]) * 60)
            ts = dt.replace(tzinfo=timezone.utc).timestamp() - offset
        else:
            ts = dt.replace(tzinfo=timezone.utc).timestamp()
        return int(ts * 1e9) + (int((frac or "0").ljust(9, "0")[:9])), "iso"
    m = _EPOCH.search(text)
    if m:
        return int(float(m.group(1)) * 1e9), "epoch"
    m = _SYSLOG.search(text)
    if m:
        mon, day, hh, mm, ss, frac = m.groups()
        dt = datetime(year, MONTHS[mon.lower()], int(day), int(hh), int(mm), int(ss), tzinfo=timezone.utc)
        return int(dt.timestamp() * 1e9) + int((frac or "0").ljust(9, "0")[:9]), "syslog"
    m = _UPTIME.search(text)
    if m:
        return int(float(m.group(1)) * 1e9), "uptime"
    return None, None


def _level(text: str) -> Optional[str]:
    for name, rx in _LEVEL:
        if rx.search(text):
            return name
    return None


def _kind(text: str) -> Optional[str]:
    for name, rx in KINDS:
        if rx.search(text):
            return name
    return None


def template(text: str) -> str:
    body = _ISO.sub("", text)
    body = _EPOCH.sub("", body)
    body = _SYSLOG.sub("", body)
    body = _UPTIME.sub("", body)
    body = _TEMPLATE_NUM.sub("#", body)
    return re.sub(r"\s+", " ", body).strip()[:200]


def read(path: Path, *, anchor_ns: Optional[int] = None, year: Optional[int] = None) -> TextLog:
    """Parse a text log. anchor_ns places line-order logs (no timestamps) in time; default: the file's modified time."""
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    all_lines = text.splitlines()
    truncated = len(all_lines) > MAX_LINES
    all_lines = all_lines[:MAX_LINES]
    year = year or datetime.now(timezone.utc).year
    parsed, clocks = [], {}
    for i, line in enumerate(all_lines, 1):
        if not line.strip():
            continue
        t, clock = _time(line, year)
        if clock:
            clocks[clock] = clocks.get(clock, 0) + 1
        loc = _LOCATION.search(line)
        sub = _SUBSYSTEM.match(line)
        subsystem = (sub.group(1) or sub.group(2)) if sub else None
        if subsystem and re.fullmatch(r"\s*[\d.]+\s*|INFO|WARN|WARNING|ERROR|DEBUG|FATAL|ERR", subsystem, re.I):
            subsystem = None
        parsed.append(Line(n=i, text=line.rstrip()[:500], t_ns=t, level=_level(line), kind=_kind(line), subsystem=subsystem,
                           location=f"{loc.group(1).split('/')[-1]}:{loc.group(2)}" if loc else None, template=template(line)))
    stamped = sum(1 for p in parsed if p.t_ns is not None)
    share = stamped / len(parsed) if parsed else 0.0
    if anchor_ns is None:
        anchor_ns = int(Path(path).stat().st_mtime * 1e9)
    notes = []
    if share < 0.5:
        clock = "line_order"
        notes.append("No usable timestamps: lines are kept in file order only (placed 1 ms apart); no duration or rate is read from them.")
        for k, p in enumerate(parsed):
            p.t_ns = anchor_ns + k * LINE_ORDER_STEP_NS
    else:
        clock = max(clocks, key=clocks.get)
        last = None
        for p in parsed:  # lines without a time inherit the last one seen (continuation lines, stack traces)
            if p.t_ns is None:
                p.t_ns = last
            last = p.t_ns if p.t_ns is not None else last
        first = next((p.t_ns for p in parsed if p.t_ns is not None), anchor_ns)
        for p in parsed:
            if p.t_ns is None:
                p.t_ns = first
        if clock == "uptime":
            notes.append("Times are seconds since boot, not wall-clock time; they restart at each boot.")
            offset = anchor_ns - parsed[0].t_ns if parsed else 0
            for p in parsed:
                p.t_ns += offset
        if clock == "syslog":
            notes.append(f"Syslog timestamps carry no year; {year} is assumed.")
        if len(clocks) > 1:
            notes.append("More than one timestamp format in the file: " + ", ".join(sorted(clocks)))
    if truncated:
        notes.append(f"Only the first {MAX_LINES:,} lines were read.")
    return TextLog(lines=parsed, clock=clock, timestamped_share=round(share, 3), anchor_ns=anchor_ns, truncated=truncated,
                   year_assumed=year if clock == "syslog" else None, notes=notes)


def rows(log: TextLog, asset: str) -> List[Dict[str, Any]]:
    """Canonical rows: resets -> controller.reset, faults -> fault.code, other lines -> controller.event."""
    out: List[Dict[str, Any]] = []
    for p in log.lines:
        if p.kind in RESET_KINDS:
            signal, event = "controller.reset", p.template
        elif p.kind in FAULT_KINDS:
            signal, event = "fault.code", None
        else:
            signal, event = "controller.event", p.template
        row = {"t": p.t_ns, "asset": asset, "signal": signal, "unit": None, "scope": p.location or p.subsystem or "log",
               "event": f"{p.level}: {event}" if (event and p.level) else event}
        if signal == "fault.code":
            row["value"] = p.template
        out.append(row)
    return out


def mapping(clock: str) -> Dict[str, Any]:
    return {"mapping_id": VERSION, "version": VERSION, "max_hz": 1000.0, "clock_domain": f"text_log_{clock}",
            "topics": [{"topic": "log", "entries": [{"signal": s} for s in ("controller.reset", "fault.code", "controller.event")]}]}
