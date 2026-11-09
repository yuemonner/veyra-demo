"""Citable evidence tools over a structured recording (ROS bag, MCAP, PX4 ULog, canonical CSV).

The AI investigator does not read a whole recording. Veyra's parser first turns the recorded signals into an EVIDENCE
SHEET: numbered lines, computed deterministically from the imported rows, that the model can quote exactly like log lines:

  SERIES    one line per signal: unit, samples, time span, range, typical interval
  EVENT     mode changes, failsafes, error codes, in time order
  GAP       stretches where a signal stopped arriving
  CONSTANT  a signal that never changed (or held one value for most of the recording)
  CHANGE    the largest step changes of each signal, measured against that signal's own usual variation
  TRACKING  a setpoint against the measured value (attitude, rate, speed...): typical error, worst error, when it diverged
  ANOMALY   the candidates worth checking first, ranked, each pointing at the lines above

Then the model tests its hypotheses with tools over the same rows: a time window, the changes of one signal, the
relation between two signals (correlation, lag, difference), and the order in which signals left their baseline.
Every number those tools return comes from the recorded rows; the verifier holds the model to them as it does for logs.
Same rows in, same sheet out: the sheet is reproducible from the case's imported data.
"""
from __future__ import annotations

import bisect
import hashlib
import math
import re
import statistics
from typing import Any, Dict, List, Optional, Tuple

SHEET_VERSION = "veyra-recording-sheet-v1"
MAX_SERIES = 80
MAX_EVENTS = 150
MAX_CHANGES_PER_SERIES = 3
CHANGE_MIN_Z = 6.0

Series = Dict[str, Any]


def _r(x: Optional[float], nd: int = 3) -> Optional[float]:
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    v = round(float(x), nd)
    return 0.0 if v == 0 else v


def _fmt(x: Optional[float]) -> str:
    v = _r(x)
    return "n/a" if v is None else (f"{v:.3f}".rstrip("0").rstrip(".") if v != int(v) else str(int(v)))


def _key(row: Dict[str, Any]) -> str:
    return row["signal"] + (f"[{row['scope']}]" if row.get("scope") else "")


def _mad(xs: List[float]) -> float:
    if not xs:
        return 0.0
    m = statistics.median(xs)
    return statistics.median([abs(x - m) for x in xs])


def _unwrap_if_heading(s: Series) -> bool:
    """A heading in degrees that wraps at +-180 (or 0/360) would read as huge steps. Unwrap it so a crossing of the wrap
    is not a change; the sheet says the series is unwrapped."""
    if (s["unit"] or "").lower() not in ("deg", "degree", "degrees") or not re.search(r"yaw|heading|course", s["key"], re.I):
        return False
    v = s["v"]
    if len(v) < 3 or max(v) - min(v) < 300:
        return False
    out, off = [v[0]], 0.0
    for a, b in zip(v, v[1:]):
        d = b - a
        if d > 180:
            off -= 360
        elif d < -180:
            off += 360
        out.append(b + off)
    if out == v:
        return False
    s["v"] = out
    return True


class Recording:
    """The case's imported rows, grouped into time series (relative seconds from the first row)."""

    def __init__(self, rows: List[Dict[str, Any]]):
        rows = [r for r in rows if r.get("t") is not None]
        rows.sort(key=lambda r: (r["t"], _key(r)))
        self.t0 = rows[0]["t"] if rows else 0.0
        self.start_utc = rows[0].get("time_utc") if rows else None
        self.clock = rows[0].get("clock_domain") if rows else None
        self.n_rows = len(rows)
        self.series: Dict[str, Series] = {}
        self.events: List[Dict[str, Any]] = []
        for r in rows:
            t = round(r["t"] - self.t0, 6)
            if isinstance(r.get("value"), (int, float)) and not isinstance(r.get("value"), bool) and math.isfinite(r["value"]):
                s = self.series.setdefault(_key(r), {"key": _key(r), "unit": r.get("unit") or "", "t": [], "v": []})
                s["t"].append(t)
                s["v"].append(float(r["value"]))
            if r.get("event_value") not in (None, "") or r.get("code") not in (None, ""):
                self.events.append({"t": t, "key": _key(r), "event": r.get("event_value"), "code": r.get("code"),
                                    "desc": r.get("code_description")})
        self.span = round((rows[-1]["t"] - self.t0), 6) if rows else 0.0
        keep = sorted(self.series.values(), key=lambda s: -len(s["t"]))[:MAX_SERIES]
        self.series = {s["key"]: s for s in sorted(keep, key=lambda s: s["key"])}
        for s in self.series.values():
            s["unwrapped"] = _unwrap_if_heading(s)
            dts = [b - a for a, b in zip(s["t"], s["t"][1:]) if b > a]
            s["dt"] = statistics.median(dts) if dts else None
            s["spread"] = _mad([b - a for a, b in zip(s["v"], s["v"][1:])]) or _mad(s["v"])

    def find(self, name: str) -> Series:
        if name in self.series:
            return self.series[name]
        low = name.lower()
        hits = [k for k in self.series if k.lower() == low] or [k for k in self.series if low in k.lower()]
        if len(hits) == 1:
            return self.series[hits[0]]
        raise ValueError(f"No single series matches {name!r}" + (f"; candidates: {hits[:8]}" if hits else f"; series: {list(self.series)[:30]}"))

    @staticmethod
    def window(s: Series, start: Optional[float], end: Optional[float]) -> Tuple[List[float], List[float]]:
        a = 0 if start is None else bisect.bisect_left(s["t"], start)
        b = len(s["t"]) if end is None else bisect.bisect_right(s["t"], end)
        return s["t"][a:b], s["v"][a:b]


# ------------------------------------------------------------------ deterministic analyses (sheet and tools share them)
def change_points(s: Series, start: Optional[float] = None, end: Optional[float] = None, top: int = MAX_CHANGES_PER_SERIES,
                  min_z: float = CHANGE_MIN_Z) -> List[Dict[str, Any]]:
    """Largest level steps: median of the w samples after minus the w samples before, in units of the signal's usual
    sample-to-sample variation (MAD of differences). Non-overlapping, strongest first."""
    t, v = Recording.window(s, start, end)
    n = len(v)
    if n < 12:
        return []
    w = max(3, min(25, n // 40))
    spread = s["spread"] or _mad(v) or 0.0
    if spread == 0:
        lo, hi = min(v), max(v)
        spread = (hi - lo) / 1000 if hi > lo else 0.0
    if spread == 0:
        return []
    cands = []
    for i in range(w, n - w + 1):
        before, after = statistics.median(v[i - w:i]), statistics.median(v[i:i + w])
        z = abs(after - before) / spread
        if z >= min_z:
            cands.append((z, i, before, after))
    out: List[Dict[str, Any]] = []
    taken: List[int] = []
    for z, i, before, after in sorted(cands, key=lambda c: (-c[0], c[1])):
        if any(abs(i - j) < 2 * w for j in taken):
            continue
        taken.append(i)
        out.append({"series": s["key"], "t": _r(t[i]), "before": _r(before), "after": _r(after), "step": _r(after - before),
                    "z": _r(z, 1), "unit": s["unit"]})
        if len(out) >= top:
            break
    return sorted(out, key=lambda c: c["t"])


def gaps(s: Series, min_s: float = 0.5) -> List[Dict[str, Any]]:
    if not s.get("dt"):
        return []
    lim = max(min_s, 5 * s["dt"])
    return [{"series": s["key"], "from": _r(a), "to": _r(b), "seconds": _r(b - a), "usual": _r(s["dt"])}
            for a, b in zip(s["t"], s["t"][1:]) if b - a > lim]


def longest_constant(s: Series) -> Optional[Dict[str, Any]]:
    v, t = s["v"], s["t"]
    if len(v) < 10:
        return None
    best, i = (0, 0, 0), 0
    for j in range(1, len(v) + 1):
        if j == len(v) or v[j] != v[i]:
            if j - i > best[0]:
                best = (j - i, i, j - 1)
            i = j
    n, a, b = best
    span = t[-1] - t[0]
    if n == len(v):
        return {"series": s["key"], "value": _r(v[0]), "whole": True, "from": _r(t[0]), "to": _r(t[-1]), "samples": n}
    if span > 0 and (t[b] - t[a]) >= 0.3 * span and n >= 20:
        return {"series": s["key"], "value": _r(v[a]), "whole": False, "from": _r(t[a]), "to": _r(t[b]), "samples": n,
                "seconds": _r(t[b] - t[a])}
    return None


def _align(a: Series, b: Series, start: Optional[float], end: Optional[float], lag: float = 0.0) -> Tuple[List[float], List[float], List[float]]:
    """b's value nearest to each of a's sample times (shifted by lag), when within 2 of b's usual intervals."""
    ta, va = Recording.window(a, start, end)
    tol = 2 * (b.get("dt") or 0.1)
    out_t, out_a, out_b = [], [], []
    for t, x in zip(ta, va):
        k = bisect.bisect_left(b["t"], t + lag)
        best = None
        for j in (k - 1, k):
            if 0 <= j < len(b["t"]) and abs(b["t"][j] - (t + lag)) <= tol and (best is None or abs(b["t"][j] - (t + lag)) < abs(b["t"][best] - (t + lag))):
                best = j
        if best is not None:
            out_t.append(t)
            out_a.append(x)
            out_b.append(b["v"][best])
    return out_t, out_a, out_b


def _corr(x: List[float], y: List[float]) -> Optional[float]:
    if len(x) < 5:
        return None
    mx, my = statistics.fmean(x), statistics.fmean(y)
    sx = math.sqrt(sum((a - mx) ** 2 for a in x))
    sy = math.sqrt(sum((b - my) ** 2 for b in y))
    if sx == 0 or sy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy)


def tracking(sp: Series, meas: Series, start: Optional[float] = None, end: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Setpoint against measured value: typical and worst absolute error, and the windows where the error stayed above
    three times its typical size (and above 1% of the setpoint's range) for at least 3 samples."""
    t, a, b = _align(sp, meas, start, end)
    if len(t) < 10:
        return None
    angular = (meas["unit"] or "").lower() in ("deg", "degree", "degrees")
    err = [abs(((x - y + 180) % 360) - 180) if angular else abs(x - y) for x, y in zip(a, b)]
    typical = statistics.median(err)
    rng = (max(a) - min(a)) or 1.0
    lim = max(3 * typical, 0.01 * rng)
    i_max = max(range(len(err)), key=lambda i: err[i])
    windows, i = [], 0
    while i < len(err):
        if err[i] > lim:
            j = i
            while j + 1 < len(err) and err[j + 1] > lim:
                j += 1
            if j - i + 1 >= 3:
                windows.append({"from": _r(t[i]), "to": _r(t[j]), "peak_error": _r(max(err[i:j + 1]))})
            i = j + 1
        else:
            i += 1
    # the typical error per stretch of the recording: a fault that degrades control shows as a sustained rise
    span = t[-1] - t[0]
    width = max(5.0, span / 10) if span > 0 else 0.0
    trend = []
    if width:
        k, n_w = 0, int(math.ceil(span / width))
        for w in range(n_w):
            lo, hi = t[0] + w * width, t[0] + (w + 1) * width
            seg = []
            while k < len(t) and t[k] < hi + (1e-9 if w == n_w - 1 else 0):
                seg.append(err[k])
                k += 1
            if len(seg) >= 5:
                trend.append({"from": _r(lo), "to": _r(min(hi, t[-1])), "typical_error": _r(statistics.median(seg))})
    rise = None
    run: List[Dict[str, Any]] = []
    for w in trend:
        if w["typical_error"] > 2 * typical and w["typical_error"] > 0.01 * rng:
            run.append(w)
            if len(run) >= 2 and (rise is None or len(run) > len(rise)):
                rise = list(run)
        else:
            run = []
    comparable = typical <= 0.25 * rng
    return {"setpoint": sp["key"], "measured": meas["key"], "unit": meas["unit"], "samples": len(t), "typical_error": _r(typical),
            "worst_error": _r(err[i_max]), "worst_at": _r(t[i_max]), "limit": _r(lim), "diverged": windows[:6], "comparable": comparable,
            "by_window": trend, "sustained_rise": ({"from": rise[0]["from"], "to": rise[-1]["to"], "typical_error": max(w["typical_error"] for w in rise)}
                                                   if rise else None)}


def tracking_pairs(rec: Recording) -> List[Tuple[Series, Series]]:
    """control.<x>_setpoint[scope] against the measured series of the same quantity and scope."""
    pairs = []
    for k, s in rec.series.items():
        m = re.match(r"^control\.(.+?)_setpoint(\[.*\])?$", k)
        if not m:
            continue
        base, scope = m.group(1), m.group(2) or ""
        base_root = base.replace("_rate", "")
        for cand in (f"vehicle.{base}{scope}", f"vehicle.{base_root}{scope}", f"vehicle.angular_rate{scope}" if base.endswith("_rate") else ""):
            if cand and cand in rec.series:
                pairs.append((s, rec.series[cand]))
                break
    return pairs


def departures(rec: Recording, start: float, end: float, keys: Optional[List[str]] = None, baseline_s: float = 5.0, k: float = 5.0) -> List[Dict[str, Any]]:
    """Order in which signals left their baseline: for each series, the first sample in [start, end] that differs from
    the median of the baseline_s before start by more than k times the baseline's usual variation."""
    out = []
    for key in keys or list(rec.series):
        s = rec.find(key) if keys else rec.series[key]
        _bt, bv = Recording.window(s, start - baseline_s, start)
        if len(bv) < 5:
            continue
        base = statistics.median(bv)
        spread = _mad(bv) or s["spread"] or 0.0
        if spread == 0:
            spread = abs(base) * 0.01 or 1e-9
        t, v = Recording.window(s, start, end)
        for ti, vi in zip(t, v):
            if abs(vi - base) > k * spread:
                out.append({"series": s["key"], "left_baseline_at": _r(ti), "baseline": _r(base), "value": _r(vi), "unit": s["unit"]})
                break
    return sorted(out, key=lambda d: (d["left_baseline_at"], d["series"]))


# ------------------------------------------------------------------ the evidence sheet
def build_sheet(rec: Recording) -> Dict[str, Any]:
    lines: List[str] = []
    refs: Dict[str, int] = {}

    def add(text: str, ref: Optional[str] = None) -> int:
        lines.append(text)
        if ref:
            refs[ref] = len(lines)
        return len(lines)

    add(f"RECORDING {rec.n_rows} rows, {len(rec.series)} series, span {_fmt(rec.span)} s from t=0 at {rec.start_utc or 'unknown'} "
        f"(clock {rec.clock or 'unknown'}). Times below are seconds from t=0.")
    for s in rec.series.values():
        v = s["v"]
        add(f"SERIES {s['key']} unit={s['unit'] or '-'} samples={len(v)} t={_fmt(s['t'][0])}..{_fmt(s['t'][-1])} s "
            f"min={_fmt(min(v))} max={_fmt(max(v))} median={_fmt(statistics.median(v))} usual_interval={_fmt(s['dt'])} s"
            + (" (unwrapped across +-180, so a wrap is not a step)" if s.get("unwrapped") else ""), "series:" + s["key"])
    for e in rec.events[:MAX_EVENTS]:
        add(f"EVENT t={_fmt(e['t'])} s {e['key']} {e['event'] if e['event'] not in (None, '') else ''}"
            f"{(' code=' + str(e['code'])) if e['code'] not in (None, '') else ''}{(' (' + str(e['desc']) + ')') if e.get('desc') else ''}".replace("  ", " "))
    if len(rec.events) > MAX_EVENTS:
        add(f"EVENT ... {len(rec.events) - MAX_EVENTS} more events not listed (use the events tool)")
    # (score, t, text, line). Score = how unusual (log scale) x what kind: a measured state not following its setpoint
    # matters more than a gap, which matters more than a step in a measured state, which matters more than a step in a
    # command (commands change by design: take-off, mode changes).
    anomalies: List[Tuple[float, float, str, int]] = []
    for s in rec.series.values():
        for g in gaps(s)[:3]:
            n = add(f"GAP {s['key']} no samples from t={_fmt(g['from'])} to {_fmt(g['to'])} s ({_fmt(g['seconds'])} s; usual interval {_fmt(g['usual'])} s)")
            anomalies.append((2.0 * math.log10(1 + g["seconds"] / max(g["usual"], 1e-3)), g["from"], f"{s['key']} stopped arriving for {_fmt(g['seconds'])} s", n))
        c = longest_constant(s)
        if c and c["whole"] and len(rec.series) > 1:
            add(f"CONSTANT {s['key']} = {_fmt(c['value'])} for the whole recording ({c['samples']} samples)")
        elif c:
            add(f"CONSTANT {s['key']} held {_fmt(c['value'])} from t={_fmt(c['from'])} to {_fmt(c['to'])} s ({c['samples']} samples)")
    groups: Dict[Tuple[str, float], List[Tuple[Dict[str, Any], int]]] = {}
    for s in rec.series.values():
        for c in change_points(s):
            n = add(f"CHANGE {s['key']} at t={_fmt(c['t'])} s: {_fmt(c['before'])} -> {_fmt(c['after'])} {s['unit']} "
                    f"(step {_fmt(c['step'])}, {_fmt(c['z'])}x its usual variation)".replace("  ", " "))
            # channels of one signal that step together are one candidate (all motors spinning up is one event)
            groups.setdefault((s["key"].split("[")[0], round(c["t"] * 2) / 2, c["step"] > 0), []).append((c, n))
    for (base, _t, up), items in groups.items():
        kind = 1.0 if base.startswith(("control.", "actuator.", "command.")) else 1.5
        z = max(c["z"] for c, _n in items)
        c0, n0 = min(items, key=lambda x: x[0]["t"])
        what = (f"{len(items)} channels of {base} stepped {'up' if up else 'down'} together" if len(items) > 1
                else f"{c0['series']} stepped {_fmt(c0['before'])} -> {_fmt(c0['after'])} {c0['unit']}".strip())
        anomalies.append((kind * math.log10(1 + z), c0["t"], what, n0))
    for sp, meas in tracking_pairs(rec):
        tr = tracking(sp, meas)
        if not tr:
            continue
        if not tr["comparable"]:
            add(f"TRACKING {sp['key']} vs {meas['key']}: not comparable (typical |error| {_fmt(tr['typical_error'])} {tr['unit']} is a quarter of the "
                f"setpoint's range or more: the two are probably in different frames or conventions)".replace("  ", " "))
            continue
        n = add(f"TRACKING {sp['key']} vs {meas['key']}: typical |error| {_fmt(tr['typical_error'])} {tr['unit']}, worst {_fmt(tr['worst_error'])} "
                f"at t={_fmt(tr['worst_at'])} s; {len(tr['diverged'])} window(s) above {_fmt(tr['limit'])}".replace("  ", " "), f"tracking:{meas['key']}")
        add(f"TRACKING {meas['key']} typical |error| by stretch: " + ", ".join(f"{_fmt(w['from'])}-{_fmt(w['to'])} s {_fmt(w['typical_error'])}" for w in tr["by_window"])
            + f" {tr['unit']}".rstrip())
        if tr["sustained_rise"]:
            r_ = tr["sustained_rise"]
            m = add(f"TRACKING {meas['key']} followed its setpoint worse from t={_fmt(r_['from'])} to {_fmt(r_['to'])} s: typical |error| up to "
                    f"{_fmt(r_['typical_error'])} {tr['unit']} against {_fmt(tr['typical_error'])} over the whole recording".replace("  ", " "))
            anomalies.append((3.0 * math.log10(1 + 4 * r_["typical_error"] / max(tr["typical_error"], 1e-6)), r_["from"],
                              f"{meas['key']} followed {sp['key']} worse for a sustained stretch (typical |error| {_fmt(r_['typical_error'])} vs {_fmt(tr['typical_error'])})", m))
        for w in tr["diverged"][:3]:
            m = add(f"TRACKING {meas['key']} diverged from its setpoint from t={_fmt(w['from'])} to {_fmt(w['to'])} s (peak |error| {_fmt(w['peak_error'])} {tr['unit']})".replace("  ", " "))
            ratio = w["peak_error"] / max(tr["typical_error"], 1e-6)
            anomalies.append((3.0 * math.log10(1 + ratio), w["from"], f"{meas['key']} did not follow {sp['key']} (peak |error| {_fmt(w['peak_error'])})", m))
    ranked = sorted(anomalies, key=lambda a: (-a[0], a[1]))[:12]
    for i, (score, t, text, line) in enumerate(sorted(ranked, key=lambda a: a[1]), 1):
        add(f"ANOMALY a{i} t={_fmt(t)} s: {text} (see line {line}; strength {_fmt(score)})", f"anomaly:a{i}")
    if not anomalies:
        add("ANOMALY none: no gap, step change or tracking divergence stood out in these signals")
    sheet_sha = hashlib.sha256("\n".join(lines).encode()).hexdigest()
    return {"version": SHEET_VERSION, "lines": lines, "refs": refs, "sha256": sheet_sha}


# ------------------------------------------------------------------ the tools the investigator offers for a recording
class RecordingTools:
    """Same contract as LogTools (raw / parsed / available / overview / read_lines / search / measure), with the evidence
    sheet as the citable 'lines', plus window / changes / relation / sequence / events / anomalies over the rows."""

    kind = "recording"

    def __init__(self, store, case_id: str):
        self.store, self.case_id = store, case_id
        rows: List[Dict[str, Any]] = []
        for imp in store.imports(case_id, include_rows=True):
            if str(imp.get("clock_domain") or "").startswith("text_log"):
                continue
            rows += imp.get("rows") or []
        self.rec = Recording(rows)
        self.sheet = build_sheet(self.rec) if self.rec.series or self.rec.events else {"lines": [], "refs": {}, "sha256": None, "version": SHEET_VERSION}
        self.raw: List[str] = self.sheet["lines"]
        self.parsed: Dict[int, Any] = {}
        self.clock = "line_order"
        self.sha = self.sheet["sha256"]

    def available(self) -> bool:
        return bool(self.rec.series)

    def electrical_applies(self) -> bool:
        return False

    def expected_applies(self) -> bool:
        return False

    def _ln(self, n: int) -> Dict[str, Any]:
        return {"line": n, "text": self.raw[n - 1]}

    def overview(self) -> Dict[str, Any]:
        an = [self._ln(n) for k, n in sorted(self.sheet["refs"].items(), key=lambda kv: kv[1]) if k.startswith("anomaly:")]
        return {"kind": "recording evidence sheet", "sheet_lines": len(self.raw), "header": self._ln(1),
                "series": [{"series": k[7:], "line": n} for k, n in self.sheet["refs"].items() if k.startswith("series:")],
                "events": sum(1 for x in self.raw if x.startswith("EVENT ")),
                "anomaly_candidates": an,
                "tracking": [self._ln(n) for k, n in self.sheet["refs"].items() if k.startswith("tracking:")],
                "note": ("The sheet lines are computed from the recorded rows; quote them exactly with their line numbers. "
                         "Use window, changes, relation, sequence and events to test each hypothesis, and cite their call ids for every number.")}

    def read_lines(self, start: int, end: Optional[int] = None) -> Dict[str, Any]:
        start = max(1, int(start))
        end = min(len(self.raw), int(end or start + 40), start + 199)
        return {"lines": [self._ln(n) for n in range(start, end + 1)], "total_lines": len(self.raw)}

    def search(self, pattern: str, context: int = 0, max_hits: int = 60) -> Dict[str, Any]:
        try:
            rx = re.compile(pattern, re.I)
        except re.error:
            rx = re.compile(re.escape(pattern), re.I)
        hits = [n for n, text in enumerate(self.raw, 1) if rx.search(text)]
        return {"pattern": pattern, "matches": len(hits), "lines": [self._ln(n) for n in hits[:max_hits]], "truncated": len(hits) > max_hits}

    def window(self, series: List[str], start_s: float, end_s: float, samples: int = 30) -> Dict[str, Any]:
        """Each series inside [start_s, end_s]: count, min, max, median, first and last value, the change against the rest
        of the recording, and up to `samples` evenly spaced samples [t, value]."""
        start_s, end_s = float(start_s), float(end_s)
        if end_s <= start_s:
            raise ValueError("end_s must be after start_s")
        out = []
        for name in (series or [])[:8]:
            s = self.rec.find(name)
            t, v = Recording.window(s, start_s, end_s)
            if not v:
                out.append({"series": s["key"], "samples": 0, "note": "no samples in this window"})
                continue
            rest = [x for tt, x in zip(s["t"], s["v"]) if tt < start_s or tt > end_s]
            step = max(1, len(v) // max(1, min(int(samples), 60)))
            out.append({"series": s["key"], "unit": s["unit"], "samples": len(v), "min": _r(min(v)), "max": _r(max(v)),
                        "median": _r(statistics.median(v)), "first": [_r(t[0]), _r(v[0])], "last": [_r(t[-1]), _r(v[-1])],
                        "median_outside_window": _r(statistics.median(rest)) if rest else None,
                        "values": [[_r(t[i]), _r(v[i])] for i in range(0, len(v), step)][:60]})
        return {"window_s": [_r(start_s), _r(end_s)], "series": out}

    def changes(self, series: str, start_s: Optional[float] = None, end_s: Optional[float] = None, top: int = 5) -> Dict[str, Any]:
        s = self.rec.find(series)
        cps = change_points(s, start_s, end_s, top=min(int(top or 5), 10), min_z=4.0)
        return {"series": s["key"], "unit": s["unit"], "usual_variation": _r(s["spread"]), "changes": cps,
                "note": "z = step size in units of the signal's usual sample-to-sample variation (median absolute difference)."}

    def relation(self, a: str, b: str, start_s: Optional[float] = None, end_s: Optional[float] = None, max_lag_s: float = 2.0) -> Dict[str, Any]:
        """How b follows a: correlation at zero lag, the lag (b later than a, in seconds) with the strongest correlation
        within ±max_lag_s, and when the units match the absolute difference (typical, worst and when)."""
        sa, sb = self.rec.find(a), self.rec.find(b)
        t, x, y = _align(sa, sb, start_s, end_s)
        if len(t) < 10:
            return {"a": sa["key"], "b": sb["key"], "samples": len(t), "note": "too few overlapping samples"}
        res: Dict[str, Any] = {"a": sa["key"], "b": sb["key"], "samples": len(t), "window_s": [_r(t[0]), _r(t[-1])], "correlation": _r(_corr(x, y))}
        step = max(sb.get("dt") or 0.05, 0.01)
        best = (abs(res["correlation"] or 0), 0.0, res["correlation"])
        n_lags = int(min(max_lag_s, 5.0) / step)
        for k in range(-n_lags, n_lags + 1, max(1, n_lags // 40)):
            lag = k * step
            _t, xx, yy = _align(sa, sb, start_s, end_s, lag)
            c = _corr(xx, yy)
            if c is not None and abs(c) > best[0] + 1e-9:
                best = (abs(c), lag, c)
        res["best_lag_s"], res["correlation_at_best_lag"] = _r(best[1]), _r(best[2])
        if (sa["unit"] or "") == (sb["unit"] or ""):
            d = [abs(p - q) for p, q in zip(x, y)]
            i = max(range(len(d)), key=lambda j: d[j])
            res["difference"] = {"unit": sa["unit"], "typical": _r(statistics.median(d)), "worst": _r(d[i]), "worst_at": _r(t[i])}
        return res

    def sequence(self, start_s: float, end_s: float, series: Optional[List[str]] = None, baseline_s: float = 5.0) -> Dict[str, Any]:
        """Which signals left their baseline first inside [start_s, end_s], in time order, with the events in the window."""
        start_s, end_s = float(start_s), float(end_s)
        dep = departures(self.rec, start_s, end_s, series, baseline_s)
        ev = [e for e in self.rec.events if start_s <= e["t"] <= end_s][:40]
        return {"window_s": [_r(start_s), _r(end_s)], "baseline_s": _r(baseline_s), "left_baseline_in_order": dep[:30],
                "events": [{"t": _r(e["t"]), "series": e["key"], "event": e["event"], "code": e["code"]} for e in ev]}

    def events(self, start_s: Optional[float] = None, end_s: Optional[float] = None) -> Dict[str, Any]:
        ev = [e for e in self.rec.events if (start_s is None or e["t"] >= float(start_s)) and (end_s is None or e["t"] <= float(end_s))]
        return {"count": len(ev), "events": [{"t": _r(e["t"]), "series": e["key"], "event": e["event"], "code": e["code"], "desc": e.get("desc")} for e in ev[:80]]}

    def anomalies(self) -> Dict[str, Any]:
        return {"candidates": [self._ln(n) for k, n in sorted(self.sheet["refs"].items(), key=lambda kv: kv[1]) if k.startswith("anomaly:")],
                "note": "Ranked by strength, listed in time order. A candidate is where to look, not a cause."}


RECORDING_TOOL_SPECS = [
    {"name": "overview", "description": "The recording's evidence sheet at a glance: the series with their sheet lines, how many events, the tracking checks and the ranked anomaly candidates (each pointing at the sheet line with the detail).",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "read_lines", "description": "Evidence sheet lines start..end (max 200), with their line numbers. Quote these exactly as evidence.",
     "input_schema": {"type": "object", "properties": {"start": {"type": "integer"}, "end": {"type": "integer"}}, "required": ["start"]}},
    {"name": "search", "description": "Evidence sheet lines matching a regular expression (case-insensitive), e.g. a series name, 'CHANGE', 'GAP', 'EVENT'.",
     "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]}},
    {"name": "window", "description": "Time window: for each series (up to 8, by name as in the sheet), count, min, max, median, first/last value, the median outside the window, and evenly spaced samples [t, value].",
     "input_schema": {"type": "object", "properties": {"series": {"type": "array", "items": {"type": "string"}}, "start_s": {"type": "number"}, "end_s": {"type": "number"}, "samples": {"type": "integer"}}, "required": ["series", "start_s", "end_s"]}},
    {"name": "changes", "description": "Signal changes: the largest level steps of one series (optionally inside a window), each with time, level before and after, and its size against the signal's usual variation.",
     "input_schema": {"type": "object", "properties": {"series": {"type": "string"}, "start_s": {"type": "number"}, "end_s": {"type": "number"}, "top": {"type": "integer"}}, "required": ["series"]}},
    {"name": "relation", "description": "Cross-signal relation: how series b follows series a (correlation, the lag with the strongest correlation, and the absolute difference when the units match), optionally inside a window. Use it for command vs response, setpoint vs measured, cause vs effect.",
     "input_schema": {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "string"}, "start_s": {"type": "number"}, "end_s": {"type": "number"}, "max_lag_s": {"type": "number"}}, "required": ["a", "b"]}},
    {"name": "sequence", "description": "Event sequence: inside [start_s, end_s], which signals left their baseline (the baseline_s before start_s) first, in time order, with the events in the window. Use it to separate the earliest sign from its effects.",
     "input_schema": {"type": "object", "properties": {"start_s": {"type": "number"}, "end_s": {"type": "number"}, "series": {"type": "array", "items": {"type": "string"}}, "baseline_s": {"type": "number"}}, "required": ["start_s", "end_s"]}},
    {"name": "events", "description": "Mode changes, failsafes and codes in time order, optionally inside a window.",
     "input_schema": {"type": "object", "properties": {"start_s": {"type": "number"}, "end_s": {"type": "number"}}}},
    {"name": "anomalies", "description": "The ranked anomaly candidates (gaps, step changes, tracking divergence), each pointing at its sheet line. Where to look, not causes.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "lessons", "description": "Earlier closed cases with a similar signature, and whether each applies here (applies / check_first / not_applicable).",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "case_context", "description": "The machine, what the reporter observed, and results the engineer already reported for earlier next actions.",
     "input_schema": {"type": "object", "properties": {}}},
]

RECORDING_TOOLS = {"overview", "read_lines", "search", "window", "changes", "relation", "sequence", "events", "anomalies"}

RECORDING_PROMPT = """
This case is a robot RECORDING (signals over time), not a text log. Veyra's parser has turned it into an EVIDENCE SHEET:
numbered lines computed from the recorded rows (SERIES, EVENT, GAP, CONSTANT, CHANGE, TRACKING, ANOMALY). Wherever the
rules above say "log lines", read "sheet lines": quote them exactly with their line numbers. Times are seconds from t=0.
Work the same way: map the fault space, then TEST each hypothesis with window, changes, relation, sequence and events,
and cite their call ids for every number. Use sequence to separate the earliest sign from its effects, and relation for
command against response. An ANOMALY line is a candidate, never a cause. A signal is what the machine's controller or
estimator recorded, not the physical part itself: say where each piece of evidence was observed (a commanded value, an
estimate, a sensor reading), and do not rule an area out with evidence that never observed it."""

STEP_LABEL = {"window": "Looked at {series} from {start_s} to {end_s} s", "changes": "Found the changes in {series}",
              "relation": "Related {a} and {b}", "sequence": "Ordered what moved first, {start_s}–{end_s} s", "events": "Listed the events",
              "anomalies": "Listed the anomaly candidates", "overview": "Read the evidence sheet", "read_lines": "Read sheet lines {start}–{end}",
              "search": "Searched the sheet for “{pattern}”"}


def summary(name: str, value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    if name == "overview":
        return f"{len(value.get('series') or [])} series, {value.get('events', 0)} events, {len(value.get('anomaly_candidates') or [])} anomaly candidates"
    if name == "window":
        return "; ".join(f"{s['series']}: {s.get('min')}–{s.get('max')} {s.get('unit', '')}".strip() for s in value.get("series") or [] if s.get("samples"))[:160]
    if name == "changes":
        return f"{len(value.get('changes') or [])} step change(s) in {value.get('series')}"
    if name == "relation":
        return f"correlation {value.get('correlation')}, strongest at lag {value.get('best_lag_s')} s"
    if name == "sequence":
        d = value.get("left_baseline_in_order") or []
        return ("first to move: " + ", ".join(f"{x['series']} at {x['left_baseline_at']} s" for x in d[:3])) if d else "nothing left its baseline"
    if name == "events":
        return f"{value.get('count', 0)} event(s)"
    if name == "anomalies":
        return f"{len(value.get('candidates') or [])} candidate(s)"
    if name == "search":
        return f"{value.get('matches', 0)} matching line(s)"
    if name == "read_lines":
        return f"{len(value.get('lines') or [])} lines"
    return ""
