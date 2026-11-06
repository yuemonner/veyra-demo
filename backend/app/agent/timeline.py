"""Freeze the evidence timeline at a decision point, and audit the frozen pack for leakage.

To judge an investigation decision fairly, only what was knowable at that moment may be used: log rows recorded before
the cutoff; header notes that neither carry a later date nor speak of outcomes (a part replaced, a problem returning, a
cause found); snapshot labels cleaned the same way. Every kept line keeps its original line number, so a citation in the
frozen pack points back to the original file. The audit then looks for anything that slipped through: later rows,
later dates, outcome words, or phrases from the sealed answer.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROW_TS = re.compile(r"^(\d{4}-\d{2}-\d{2})T\d{2}:\d{2}:\d{2}")
ANY_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
OUTCOME = re.compile(r"replac|return|fixed|repair|found|root cause|resolved|came back|again|re-?crimp|re-?torque|"
                     r"更换|换了|修复|修好|查出|原因是|又出现|再次|复发|重新压接|紧固", re.I)
SNAPSHOT_REF = re.compile(r"snapshot|片段", re.I)
PAREN = re.compile(r"\(([^()]*)\)|（([^（）]*)）")


def _dates(text: str) -> List[date]:
    out = []
    for y, m, d in ANY_DATE.findall(text):
        try:
            out.append(date(int(y), int(m), int(d)))
        except ValueError:
            continue
    return out


def _bad(text: str, cutoff: date) -> Optional[str]:
    if any(d >= cutoff for d in _dates(text)):
        return "date on or after the cutoff"
    if OUTCOME.search(text):
        return "outcome word"
    return None


def freeze(lines: Sequence[str], cutoff: date, available_from: Optional[date] = None) -> Tuple[List[str], List[int], Dict[str, Any]]:
    """Return (frozen lines, original line number of each, audit of what was removed and why)."""
    out: List[str] = []
    orig: List[int] = []
    dropped_header: List[Dict[str, Any]] = []
    kept_rows = dropped_rows = 0
    pending_label: Optional[Tuple[int, str]] = None
    if available_from is not None and available_from >= cutoff:
        return [], [], {"cutoff": cutoff.isoformat(), "kept_rows": 0, "dropped_rows": None, "note": "this file was not available yet"}
    for i, raw in enumerate(lines, 1):
        m = ROW_TS.match(raw)
        if m:
            if date.fromisoformat(m.group(1)) < cutoff:
                if pending_label is not None:
                    out.append(pending_label[1])
                    orig.append(pending_label[0])
                    pending_label = None
                out.append(raw)
                orig.append(i)
                kept_rows += 1
            else:
                dropped_rows += 1
                pending_label = None
            continue
        if raw.startswith("# ----"):
            label = raw
            for g in PAREN.finditer(raw):
                inner = g.group(1) or g.group(2) or ""
                if _bad(inner, cutoff):
                    label = label.replace(g.group(0), "").rstrip()
            label = re.sub(r"snapshot \d+ of \d+:\s*", "", label)
            pending_label = (i, label)       # written only if a row of this snapshot is kept
            continue
        if raw.startswith("#"):
            why = _bad(raw, cutoff) or ("refers to other snapshots" if SNAPSHOT_REF.search(raw) else None)
            if why:
                dropped_header.append({"line": i, "reason": why})
                continue
            out.append(raw)
            orig.append(i)
            continue
        if raw.strip():
            # a continuation line (no timestamp): keep it only next to a kept row
            if orig and orig[-1] == i - 1:
                out.append(raw)
                orig.append(i)
    return out, orig, {"cutoff": cutoff.isoformat(), "kept_rows": kept_rows, "dropped_rows": dropped_rows, "dropped_header": dropped_header}


def sealed_phrases(truth: Dict[str, Any]) -> List[str]:
    """Distinctive words of the sealed answer: latin words of 5+ letters and CJK runs of 2+ characters."""
    text = " ".join(str(truth.get(k) or "") for k in ("main_cause_text", "location"))
    words = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z\-]{4,}", text)}
    cjk = set(re.findall(r"[一-鿿]{2,}", text))
    common = {"which", "there", "their", "about", "because", "under", "below", "above", "engineer", "engineers", "reported",
              "contact", "resistance", "voltage", "battery", "torque"}  # words that also name signals or normal context
    return sorted((words - common) | cjk)


def leak_check(frozen: Sequence[str], cutoff: date, phrases: Sequence[str] = (), allowed_dates_before: bool = True) -> List[Dict[str, Any]]:
    """Everything in a frozen pack that should not be there."""
    leaks = []
    for n, raw in enumerate(frozen, 1):
        m = ROW_TS.match(raw)
        if m and date.fromisoformat(m.group(1)) >= cutoff:
            leaks.append({"line": n, "kind": "row after cutoff", "text": raw[:120]})
        if raw.startswith("#"):
            why = _bad(raw, cutoff)
            if why:
                leaks.append({"line": n, "kind": f"comment: {why}", "text": raw[:120]})
        low = raw.lower()
        for p in phrases:
            if p.lower() in low:
                leaks.append({"line": n, "kind": "sealed phrase", "phrase": p, "text": raw[:120]})
    return leaks


def parse_date(s: str) -> date:
    return datetime.fromisoformat(s[:10]).date()
