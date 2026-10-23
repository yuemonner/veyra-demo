"""Analysis methods the agent can run, with their rules stated in every result.

Built-in methods are deterministic Veyra code, versioned, run in the API process (no generated code involved):
  onset          when a signal first left its normal band, and the band used
  onset_order    which of two signals departed first, or "cannot tell" when clocks or sampling don't allow it
  lag            delay between two signals by cross-correlation, with the clock caveat
  compare_runs   one signal's statistics across several cases, converting units only between known pairs

Team methods are Python an engineer saved (for example the team's own heading-error script). They run in the
sandbox like any agent-written computation, and their version is the fingerprint of their code.
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.compute.data import CaseData, iso

METHODS_VERSION = "veyra-methods-1"
CONVERSIONS = {("rad", "deg"): 180 / math.pi, ("deg", "rad"): math.pi / 180, ("s", "ms"): 1000.0, ("ms", "s"): 0.001,
               ("m/s", "km/h"): 3.6, ("km/h", "m/s"): 1 / 3.6, ("rad/s", "deg/s"): 180 / math.pi, ("deg/s", "rad/s"): math.pi / 180}

BUILTIN = {
    "onset": {"description": "When a signal first left its normal band (median ± k × robust spread of a normal period) for n samples in a row. Give a_minus to use a − a_minus instead (e.g. measured position − commanded position = tracking error).",
              "inputs": ["a", "a_minus (optional)"], "params": {"k": "band width in robust standard deviations (default 4)", "n": "samples in a row (default 3)",
                                          "normal": "'reference' (the case's declared normal window, default), 'first_fraction' or a [start, end] ISO pair",
                                          "after": "ignore departures before this ISO time (optional)"}},
    "onset_order": {"description": "Which of two signals departed first. Answers 'cannot tell' when the gap is within the clock and sampling uncertainty, or when the clocks have no declared relation.",
                    "inputs": ["a", "b", "a_minus (optional)", "b_minus (optional)"], "params": {"k": "as onset", "n": "as onset", "normal": "as onset", "after": "as onset"}},
    "lag": {"description": "Delay between two numeric signals by normalised cross-correlation on a common time grid, within ±max_lag_s.",
            "inputs": ["a", "b"], "params": {"max_lag_s": "largest delay to consider (default 2)"}},
    "compare_runs": {"description": "One signal's count, median, p95, min and max in the same kind of window across several cases. Units are converted only between known pairs (deg/rad, ms/s, m/s/km/h); otherwise the case is left out and said so.",
                     "inputs": ["one series per case"], "params": {}},
}


# ---------- helpers ----------
def _numeric(s: Dict[str, Any]) -> Tuple[List[float], List[float]]:
    if not s.get("numeric"):
        raise ValueError(f"{s['signal']} is not numeric in case {s['case_id']}")
    return [float(t) for t in s["t"]], [float(v) for v in s["v"]]


def _period(ts: List[float], near: Optional[float] = None) -> float:
    gaps = [b - a for a, b in zip(ts, ts[1:]) if b > a]
    if not gaps:
        return 0.0
    if near is not None:
        index = min(range(len(ts)), key=lambda i: abs(ts[i] - near))
        local = [b - a for a, b in zip(ts[max(0, index - 5):index + 6], ts[max(0, index - 5) + 1:index + 6]) if b > a]
        if local:
            return max(local)
    return statistics.median(gaps)


def _pct(values: List[float], q: float) -> float:
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def difference(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """a − b on a's timestamps (nearest earlier reading of b). Same case, clock and unit only: tracking error and similar."""
    if a["case_id"] != b["case_id"] or a["clock"] != b["clock"]:
        raise ValueError(f"Cannot subtract {b['signal']} from {a['signal']}: different case or clock ({a['clock']} vs {b['clock']})")
    if a["unit"] != b["unit"]:
        raise ValueError(f"Cannot subtract {b['signal']} ({b['unit']}) from {a['signal']} ({a['unit']}): different units")
    ta, va = _numeric(a)
    tb, vb = _numeric(b)
    out_t, out_v, out_s, j = [], [], [], 0
    gap = 2 * max(_period(ta), _period(tb))
    for i, t in enumerate(ta):
        while j + 1 < len(tb) and tb[j + 1] <= t:
            j += 1
        if tb[j] <= t and t - tb[j] <= gap:
            out_t.append(t)
            out_v.append(va[i] - vb[j])
            out_s.append(a["sources"][i])
    if len(out_t) < 10:
        raise ValueError(f"{a['signal']} and {b['signal']} have too few readings close enough in time to subtract")
    return {**a, "signal": f"{a['signal']} − {b['signal']}", "t": out_t, "v": out_v, "sources": out_s, "numeric": True}


def _normal_span(data: CaseData, s: Dict[str, Any], normal: Any) -> Tuple[float, float, str]:
    ts = s["t"]
    if normal in (None, "reference"):
        try:
            lo, hi = data.window(s["case_id"], "reference")
            return lo, hi, "the case's declared normal (reference) window"
        except ValueError:
            normal = "first_fraction"
    if normal == "first_fraction":
        return ts[0], ts[0] + (ts[-1] - ts[0]) * 0.2, "ASSUMED normal: the first 20% of the queried readings (no declared normal window)"
    if isinstance(normal, (list, tuple)) and len(normal) == 2:
        from app.compute.data import parse_t
        return parse_t(normal[0]), parse_t(normal[1]), "the normal period given in the request"
    raise ValueError("normal must be 'reference', 'first_fraction' or [start, end]")


def onset(data: CaseData, s: Dict[str, Any], *, k: float = 4.0, n: int = 3, normal: Any = "reference", after: Optional[str] = None) -> Dict[str, Any]:
    ts, vs = _numeric(s)
    lo, hi, basis = _normal_span(data, s, normal)
    base = [v for t, v in zip(ts, vs) if lo <= t <= hi]
    if len(base) < 5:
        raise ValueError(f"Only {len(base)} readings of {s['signal']} in the normal period; need at least 5")
    centre = statistics.median(base)
    spread = statistics.median([abs(v - centre) for v in base]) * 1.4826
    floor = max(abs(centre) * 1e-3, 1e-9)
    width = k * max(spread, floor)
    from app.compute.data import parse_t
    start_after = max(hi, parse_t(after)) if after else hi
    run = 0
    for i, (t, v) in enumerate(zip(ts, vs)):
        if t <= start_after:
            continue
        run = run + 1 if abs(v - centre) > width else 0
        if run >= n:
            first = i - n + 1
            return {"departed": True, "onset": iso(ts[first]), "onset_t": ts[first], "value": vs[first], "unit": s["unit"],
                    "source": s["sources"][first], "band": [centre - width, centre + width], "normal_median": centre,
                    "normal_basis": basis, "normal_window": [iso(lo), iso(hi)], "rule": f"|value − median| > {k:g} × robust spread for {n} readings in a row",
                    "sample_period_s": _period(ts, ts[first])}
    return {"departed": False, "unit": s["unit"], "band": [centre - width, centre + width], "normal_basis": basis, "normal_window": [iso(lo), iso(hi)],
            "rule": f"|value − median| > {k:g} × robust spread for {n} readings in a row", "searched_from": iso(start_after), "searched_to": iso(ts[-1])}


def _clock_uncertainty(a: Dict[str, Any], b: Dict[str, Any]) -> Tuple[Optional[float], str]:
    if a["case_id"] == b["case_id"] and a["clock"] == b["clock"]:
        return 0.0, f"same clock ({a['clock']})"
    sa, sb = a.get("clock_max_skew_s"), b.get("clock_max_skew_s")
    if sa is None or sb is None:
        unknown = [c for c, x in ((a["clock"], sa), (b["clock"], sb)) if x is None]
        return None, f"different clocks ({a['clock']} vs {b['clock']}); no declared maximum offset for {', '.join(unknown)}"
    return float(sa) + float(sb), f"different clocks ({a['clock']} vs {b['clock']}); declared maximum offsets {sa:g} s + {sb:g} s"


def onset_order(data: CaseData, a: Dict[str, Any], b: Dict[str, Any], **kw) -> Dict[str, Any]:
    oa, ob = onset(data, a, **kw), onset(data, b, **kw)
    clock_u, clock_note = _clock_uncertainty(a, b)
    out = {"a": {"signal": a["signal"], "scope": a.get("scope"), **{k: v for k, v in oa.items() if k != "onset_t"}},
           "b": {"signal": b["signal"], "scope": b.get("scope"), **{k: v for k, v in ob.items() if k != "onset_t"}}, "clocks": clock_note}
    if not (oa["departed"] and ob["departed"]):
        missing = [x["signal"] for x, o in ((a, oa), (b, ob)) if not o["departed"]]
        return {**out, "verdict": "cannot_tell", "why": f"{', '.join(missing)} did not leave its normal band, so there is no order to compare"}
    gap = ob["onset_t"] - oa["onset_t"]
    sampling = oa["sample_period_s"] + ob["sample_period_s"]
    out["gap_s"] = round(gap, 6)
    out["sampling_uncertainty_s"] = round(sampling, 6)
    if clock_u is None:
        return {**out, "verdict": "cannot_tell", "why": "The two clocks have no declared relation, so their timestamps cannot be ordered. Declare the maximum offset (machine context clock_offset_bound_ms or the import profile) to compare them."}
    total = clock_u + sampling
    out["total_uncertainty_s"] = round(total, 6)
    if abs(gap) <= total:
        return {**out, "verdict": "cannot_tell", "why": f"The gap ({gap:+.3f} s) is within the timing uncertainty (±{total:.3f} s: clocks {clock_u:g} s + sampling {sampling:.3f} s)."}
    first, second = (a, b) if gap > 0 else (b, a)
    return {**out, "verdict": "a_first" if gap > 0 else "b_first",
            "why": f"{first['signal']} left its band {abs(gap):.3f} s before {second['signal']}, more than the timing uncertainty (±{total:.3f} s)."}


def lag(data: CaseData, a: Dict[str, Any], b: Dict[str, Any], *, max_lag_s: float = 2.0) -> Dict[str, Any]:
    ta, va = _numeric(a)
    tb, vb = _numeric(b)
    lo, hi = max(ta[0], tb[0]), min(ta[-1], tb[-1])
    if hi - lo <= 2 * max_lag_s:
        raise ValueError("The two signals overlap for too short a time for this max_lag_s")
    dt = max(_period(ta), _period(tb), (hi - lo) / 4000)
    grid = [lo + i * dt for i in range(int((hi - lo) / dt))]

    def resample(ts, vs):
        out, j = [], 0
        for g in grid:
            while j + 1 < len(ts) and ts[j + 1] <= g:
                j += 1
            out.append(vs[j])
        m = statistics.fmean(out)
        sd = statistics.pstdev(out) or 1.0
        return [(x - m) / sd for x in out]

    xa, xb = resample(ta, va), resample(tb, vb)
    steps = int(max_lag_s / dt)
    best = (0, -2.0)
    for k in range(-steps, steps + 1):
        pairs = [(xa[i], xb[i + k]) for i in range(max(0, -k), min(len(xa), len(xb) - k))]
        if len(pairs) < 20:
            continue
        c = sum(p * q for p, q in pairs) / len(pairs)
        if c > best[1]:
            best = (k, c)
    clock_u, clock_note = _clock_uncertainty(a, b)
    lag_s = best[0] * dt
    out = {"lag_s": round(lag_s, 6), "meaning": "positive: b follows a by this much", "correlation": round(best[1], 3), "grid_step_s": round(dt, 6),
           "overlap": [iso(lo), iso(hi)], "clocks": clock_note}
    if clock_u is None:
        out["caveat"] = "Clocks have no declared relation: the lag mixes the real delay with an unknown clock offset."
    elif abs(lag_s) <= clock_u + dt:
        out["caveat"] = f"The lag is within the timing uncertainty (±{clock_u + dt:.3f} s); it does not establish an order."
    if best[1] < 0.3:
        out["caveat"] = (out.get("caveat", "") + " Correlation is weak (< 0.3): the signals may not be related.").strip()
    return out


def compare_runs(data: CaseData, series: List[Dict[str, Any]]) -> Dict[str, Any]:
    target = series[0]["unit"]
    rows, skipped = [], []
    for s in series:
        try:
            _, vs = _numeric(s)
        except ValueError as error:
            skipped.append({"case_id": s["case_id"], "why": str(error)})
            continue
        factor, note = 1.0, None
        if s["unit"] != target:
            factor = CONVERSIONS.get((s["unit"], target))
            if factor is None:
                skipped.append({"case_id": s["case_id"], "why": f"unit {s['unit']} cannot be converted to {target}"})
                continue
            note = f"converted from {s['unit']} × {factor:.6g}"
        vs = [v * factor for v in vs]
        rows.append({"case_id": s["case_id"], "window": s["window"], "count": len(vs), "median": statistics.median(vs), "p95": _pct(vs, 0.95),
                     "min": min(vs), "max": max(vs), "unit": target, "conversion": note, "first_source": s["sources"][0], "last_source": s["sources"][-1]})
    return {"signal": series[0]["signal"], "unit": target, "cases": rows, "left_out": skipped}


# ---------- team methods (saved Python) ----------
class Library:
    """Team methods for one workspace, stored next to its case database. Append-only versions."""

    def __init__(self, store_path):
        self.path = Path(str(store_path)).with_suffix(".methods.json")

    def _load(self) -> List[Dict[str, Any]]:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return []

    def all(self) -> List[Dict[str, Any]]:
        latest: Dict[str, Dict[str, Any]] = {}
        for m in self._load():
            latest[m["name"]] = m
        return list(latest.values())

    def get(self, name: str) -> Dict[str, Any]:
        for m in self.all():
            if m["name"] == name:
                return m
        raise ValueError(f"No team method called '{name}'")

    def save(self, *, name: str, description: str, code: str, inputs: List[str], author: str) -> Dict[str, Any]:
        name = name.strip()
        if not name or len(name) > 60 or not all(c.isalnum() or c in "-_ " for c in name):
            raise ValueError("Name: letters, digits, spaces, - or _, up to 60 characters")
        if name in BUILTIN:
            raise ValueError(f"'{name}' is a built-in method")
        if "result" not in code:
            raise ValueError("The code must set `result`")
        items = self._load()
        entry = {"name": name, "description": description.strip()[:400], "code": code, "inputs": [i.strip() for i in inputs if i.strip()][:8] or ["a"],
                 "author": author, "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "version": hashlib.sha256(code.encode()).hexdigest()[:12], "previous_versions": sum(1 for m in items if m["name"] == name)}
        items.append(entry)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, indent=1))
        tmp.replace(self.path)
        return entry
