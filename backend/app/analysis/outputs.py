"""Actuator-output health: outputs that freeze, pin at a limit, or split from their mirrored twin.

Generic across machines: a hydraulic valve command, a crane hoist drive or an aircraft servo all
normally move while the machine works. This module reads the controller's actuator outputs
(`actuator.output`, row scope = the channel's function, e.g. "rudder") and reports, against the
declared reference window:

  STUCK   an output that moved during the reference holds one exact value for >= STUCK_MIN_S
          and >= STUCK_MIN_SAMPLES consecutive samples, at a value outside its reference range
          (v1 did not require "outside"; on real flights that flagged throttle held steady in cruise).
          A freeze inside the normal range is caught by MIRROR when the output has a twin.
  MIRROR  two outputs that were identical in >= MIRROR_SHARE of reference samples differ by more
          than MIRROR_TOLERANCE for >= MIRROR_MIN_SAMPLES consecutive samples

v3: when the machine has known-good runs (earlier cases a person declared known-good), a freeze is
reported only if those runs never held that value (±VALUE_TOL) for as long. A throttle resting at its
configured maximum while climbing, or at idle while descending, is normal for that machine; a hold
longer than anything seen in normal operation is not. Without known-good runs, v2 applies unchanged.

Rules are fixed constants, versioned. v2 and v3 were written after seeing benchmark results (see docs). Every finding names
the channel, the time, the held value and its source rows. An output that freezes is an observation;
whether the actuator, its drive or the controller caused it is for a person to establish.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

OUTPUT_RULES_VERSION = "actuator-output-rules-v3"  # v2: freeze only outside the reference range; v3: and longer than any hold in known-good runs
SIGNAL = "actuator.output"
ACTIVE_MIN_RANGE = 20.0      # an output "moves" if its reference range is at least this (output units)
STUCK_MIN_S = 2.0
STUCK_MIN_SAMPLES = 6
MIRROR_SHARE = 0.95
MIRROR_TOLERANCE = 10.0
MIRROR_MIN_SAMPLES = 3
REFERENCE_MIN_SAMPLES = 10
VALUE_TOL_ABS = 2.0          # output units: holds within this of the frozen value count as the same value
VALUE_TOL_REL = 0.002


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _holds(items: List[Dict[str, Any]]) -> List[Tuple[float, float]]:
    """(duration_s, value) of every run of identical consecutive values."""
    out, i = [], 0
    while i < len(items):
        j = i
        while j + 1 < len(items) and items[j + 1]["value"] == items[i]["value"]:
            j += 1
        out.append((items[j]["t"] - items[i]["t"], items[i]["value"]))
        i = j + 1
    return out


def envelope(baseline_runs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Longest hold per output channel and value across known-good runs of the same machine."""
    holds: Dict[str, List[Tuple[float, float]]] = {}
    for run in baseline_runs:
        per: Dict[str, List[Dict[str, Any]]] = {}
        for row in run["rows"]:
            if row.get("signal") == SIGNAL and isinstance(row.get("value"), (int, float)):
                per.setdefault(row.get("scope") or "unnamed", []).append(row)
        for name, items in per.items():
            items.sort(key=lambda r: r["t"])
            holds.setdefault(name, []).extend(_holds(items))
    return {"cases": [r["case_id"] for r in baseline_runs], "holds": holds}


def _normal_hold(env: Optional[Dict[str, Any]], channel: str, value: float) -> Optional[float]:
    if not env or channel not in env["holds"]:
        return None
    tol = max(VALUE_TOL_ABS, abs(value) * VALUE_TOL_REL)
    near = [d for d, v in env["holds"][channel] if abs(v - value) <= tol]
    return max(near) if near else 0.0


def detect(rows: Iterable[Dict[str, Any]], reference: Optional[Tuple[float, float]],
           baseline_runs: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
    env = envelope(baseline_runs) if baseline_runs else None
    suppressed: List[Dict[str, Any]] = []
    channels: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        if row.get("signal") == SIGNAL and isinstance(row.get("value"), (int, float)):
            channels.setdefault(row.get("scope") or "unnamed", []).append(row)
    if not channels:
        return {"status": "not_applicable", "reason": f"No {SIGNAL} records", "findings": [], "channels": []}
    if reference is None:
        return {"status": "unavailable", "reason": "Output health needs a reference window (declared or assumed)", "findings": [], "channels": []}
    for items in channels.values():
        items.sort(key=lambda r: r["t"])

    summary, findings = [], []
    for name, items in sorted(channels.items()):
        ref = [r["value"] for r in items if reference[0] <= r["t"] <= reference[1]]
        info = {"channel": name, "samples": len(items), "reference_samples": len(ref)}
        if len(ref) < REFERENCE_MIN_SAMPLES:
            summary.append({**info, "status": "insufficient_reference"})
            continue
        lo, hi = min(ref), max(ref)
        active = hi - lo >= ACTIVE_MIN_RANGE
        summary.append({**info, "status": "monitored" if active else "static_in_reference", "reference_range": [lo, hi]})
        if not active:
            continue
        after = [r for r in items if r["t"] > reference[1]]
        i = 0
        while i < len(after):
            j = i
            while j + 1 < len(after) and after[j + 1]["value"] == after[i]["value"]:
                j += 1
            run = after[i:j + 1]
            held_s = run[-1]["t"] - run[0]["t"]
            normal = _normal_hold(env, name, run[0]["value"]) if (len(run) >= STUCK_MIN_SAMPLES and held_s >= STUCK_MIN_S
                                                                    and not lo <= run[0]["value"] <= hi) else None
            if normal is not None and normal >= held_s:
                suppressed.append({"channel": name, "value": run[0]["value"], "start": _iso(run[0]["t"]), "held_s": round(held_s, 2),
                                   "longest_normal_hold_s": round(normal, 2),
                                   "text": f"{name} held {run[0]['value']:g} for {held_s:.1f} s; known-good runs of this machine held it for up to {normal:.1f} s, so not reported"})
            elif (len(run) >= STUCK_MIN_SAMPLES and held_s >= STUCK_MIN_S
                    and not lo <= run[0]["value"] <= hi):
                value = run[0]["value"]
                findings.append({"rule": "STUCK", "channel": name, "start": _iso(run[0]["t"]), "t_start": run[0]["t"],
                                 "end": _iso(run[-1]["t"]), "held_value": value, "unit": run[0].get("unit"), "samples": len(run),
                                 "outside_reference_range": not lo <= value <= hi, "reference_range": [lo, hi],
                                 "rows": [{"import_id": r.get("import_id"), "row": r.get("row")} for r in run[:3]],
                                 "longest_normal_hold_s": None if normal is None else round(normal, 2),
                                 "text": f"{name} output held {value:g} for {run[-1]['t'] - run[0]['t']:.1f} s ({len(run)} samples)"
                                         + (" — outside its reference range" if not lo <= value <= hi else "")
                                         + ("" if normal is None else (f"; known-good runs never held it longer than {normal:.1f} s" if normal else "; never held in known-good runs"))})
                break  # first freeze per channel is the onset
            i = j + 1

    names = sorted(channels)
    for a_index, a in enumerate(names):
        for b in names[a_index + 1:]:
            pairs = _pair(channels[a], channels[b])
            ref_pairs = [(t, x, y) for t, x, y in pairs if reference[0] <= t <= reference[1]]
            if len(ref_pairs) < REFERENCE_MIN_SAMPLES:
                continue
            same = sum(1 for _, x, y in ref_pairs if abs(x - y) <= MIRROR_TOLERANCE) / len(ref_pairs)
            if same < MIRROR_SHARE:
                continue
            streak: List[Tuple[float, float, float]] = []
            for t, x, y in pairs:
                if t <= reference[1]:
                    continue
                if abs(x - y) > MIRROR_TOLERANCE:
                    streak.append((t, x, y))
                    if len(streak) >= MIRROR_MIN_SAMPLES:
                        findings.append({"rule": "MIRROR", "channel": f"{a}|{b}", "channels": [a, b], "start": _iso(streak[0][0]), "t_start": streak[0][0],
                                         "values_at_start": [streak[0][1], streak[0][2]], "reference_match_share": round(same, 3),
                                         "text": f"{a} and {b} matched in {same:.0%} of reference samples, then split by {abs(streak[0][1] - streak[0][2]):g}"})
                        break
                else:
                    streak = []
    findings.sort(key=lambda f: f["t_start"])
    return {"status": "computed", "rules_version": OUTPUT_RULES_VERSION, "reference": [_iso(reference[0]), _iso(reference[1])],
            "rules": {"STUCK": f"output that moved in the reference holds one exact value outside its reference range for >= {STUCK_MIN_S:g} s and >= {STUCK_MIN_SAMPLES} samples"
                               + (", longer than any hold of that value in the machine's known-good runs" if env else ""),
                      "MIRROR": f"outputs identical (±{MIRROR_TOLERANCE:g}) in >= {MIRROR_SHARE:.0%} of reference samples differ for >= {MIRROR_MIN_SAMPLES} samples"},
            "channels": summary, "findings": findings, "first": findings[0] if findings else None,
            "machine_baseline": {"known_good_cases": env["cases"], "suppressed_as_normal": suppressed} if env else None,
            "boundary": "Output behaviour as recorded. A frozen output is an observation; the cause (actuator, drive, controller or command) is not established here."}


def _pair(a: List[Dict[str, Any]], b: List[Dict[str, Any]]) -> List[Tuple[float, float, float]]:
    """Pair samples with identical timestamps (outputs from one message), else nearest within 50 ms."""
    out, j = [], 0
    for row in a:
        while j + 1 < len(b) and abs(b[j + 1]["t"] - row["t"]) <= abs(b[j]["t"] - row["t"]):
            j += 1
        if b and abs(b[j]["t"] - row["t"]) <= 0.05:
            out.append((row["t"], row["value"], b[j]["value"]))
    return out
