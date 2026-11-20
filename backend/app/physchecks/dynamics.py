"""Signal dynamics: domain-neutral, read-only computations of how signals change over time and relative to each other.

Generalised from the engineering operations in an independent engineering assessment of CASE A (2026-11-17):
rates of change on actual timestamps over rolling windows, the rate of change of the spread between comparable signals,
reading those against load, operating state and the protective event, and separating a precursor from the trigger.
Nothing here knows a battery, a vehicle or a case. Domain meaning (which signals are comparable components, which signal
is the load, which state change is a protective event) comes from a domain library (app/physchecks/domains/*.json) or
from the caller.

Every operation:
  * reads signal ROLES through the recording's profile (valid samples only: ranges, placeholders and maps apply);
  * works on actual timestamps, never on sample counts; windows are time spans;
  * never fits, differentiates or aligns across a gap longer than the profile's max_gap_s, a time reversal or overlap
    (two source segments interleaved), a change of source segment, or a declared state change (segments);
  * estimates the measurement floor from the data (quantisation step, noise, update interval of held values) and reports
    what a rate or a difference must exceed to be distinguishable from it;
  * corrects standard errors for serial correlation (lag-1 autocorrelation of the residuals: n_eff = n (1 - rho) / (1 + rho));
  * uses one significance constant, Z = 2 (two-sided, about 95 %), stated in its assumptions; no machine-specific threshold;
  * returns quantities labelled by kind (observation / inferred_parameter / conditional), its assumptions, its inputs and
    the source rows (file, 1-based line) behind the key numbers, like app.physchecks.compute.

Operations (registered in app.physchecks.compute.OPS):
  quality      sampling interval and jitter, update interval of held values, quantisation step, noise, gaps, overlaps, segments
  rate         rate of change over time with an adaptive (or given) time window and its standard error; acceleration
  changepoint  piecewise-linear fit with change points chosen by an information criterion; each change with a time interval
  departure    the signal's relationship to its drivers fitted on a baseline span, then where, when and how strongly the signal
               departs from it (onset with an interval, earliest persistent detection, lead time before an event)
  divergence   a group of comparable signals: common mode and spread, the spread's departure from its baseline relationship
               to load and common mode, change of load coupling, unexplained jumps, identity of the extreme component,
               timing-skew bound, and whether the divergence precedes an event
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

from app.physchecks import compute as C

DYN_VERSION = "dynamics-v3"
Z = 2.0                 # the only significance constant: two-sided, about 95 %
OUTLIER_Z = 5.0         # a residual this many baseline standard deviations from the fit is an outlier (Gaussian p ~ 6e-7)
MIN_FIT = 8             # fewest independent samples for a local fit
MAX_POINTS = 4000       # longer series are averaged in time bins of at least one update interval before a global fit
TRACK_ITEMS = 40

Signal = Union[str, Dict[str, Any]]


class DynError(C.ComputeError):
    pass


def _r(x: Any, nd: int = 6) -> Any:
    if x is None:
        return None
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    try:
        f = float(x)
    except (TypeError, ValueError):
        return x
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, nd)


# ---- signals ---------------------------------------------------------------------------------------------------------
class Track:
    """A signal on its own timestamps: one role, or an expression over roles aligned to the slowest member."""

    def __init__(self, t: np.ndarray, v: np.ndarray, *, name: str, unit: str, members: List[C.Series], idx: List[np.ndarray],
                 seg: np.ndarray, age_spread: Optional[np.ndarray], resolution: Optional[float], kinds: List[str]):
        self.t, self.v, self.name, self.unit, self.members, self.idx, self.seg = t, v, name, unit, members, idx, seg
        self.age_spread = age_spread       # per sample: max - min age of the member samples combined (alignment skew), s
        self.resolution = resolution
        self.kinds = kinds

    def __len__(self) -> int:
        return len(self.t)

    def refs(self, i: int) -> List[Dict[str, Any]]:
        return [m.ref(int(ix[i])) for m, ix in zip(self.members, self.idx)]

    def inputs(self) -> List[Dict[str, Any]]:
        return [m.input() for m in self.members]


def _seg_ids(s: C.Series) -> np.ndarray:
    col = s.ctx.rec.column(s.window, s.rs.field)
    segs = np.asarray(col.seg, dtype=np.int64) if len(col.seg) == len(col.t) and len(col.t) else np.zeros(len(col.t), dtype=np.int64)
    return segs[np.asarray(s.idx, dtype=np.int64)] if len(s.idx) else np.zeros(0, dtype=np.int64)


def _role_track(ctx: C.Context, role: str, window: str, t0: Optional[float], t1: Optional[float]) -> Track:
    s = ctx.series(role, window, t0, t1)
    if len(s) == 0:
        raise DynError(f"no valid samples of {role} in {window} {t0}..{t1}")
    kind = ctx.profile.role(role).get("kind", "measurement")
    return Track(np.asarray(s.t, float), np.asarray(s.v, float), name=role, unit=s.unit, members=[s], idx=[np.arange(len(s))],
                 seg=_seg_ids(s), age_spread=None, resolution=s.rs.resolution, kinds=[kind])


EXPR = ("diff", "spread", "mean", "median")


def track(ctx: C.Context, sig: Signal, window: str, t0: Optional[float] = None, t1: Optional[float] = None) -> Track:
    """A role name, or {"diff": [a, b]} (a - b), {"spread": [roles]} (max - min), {"mean": [roles]}. Members are aligned to
    the timestamps of the member with the longest median sampling interval, each at its latest sample no older than its
    max_age_s; a timestamp at which any member has no fresh sample is dropped."""
    if isinstance(sig, str):
        return _role_track(ctx, sig, window, t0, t1)
    if not isinstance(sig, dict) or len(sig) != 1 or next(iter(sig)) not in EXPR:
        raise DynError(f"a signal is a role name or one of {{'diff': [a, b]}}, {{'spread': [roles]}}, {{'mean': [roles]}}; got {sig!r}")
    op, roles = next(iter(sig.items()))
    roles = list(roles or [])
    if (op == "diff" and len(roles) != 2) or len(roles) < 2:
        raise DynError(f"{op} needs {'exactly two' if op == 'diff' else 'at least two'} roles")
    ss = [ctx.series(r, window, t0, t1) for r in roles]
    if any(len(s) == 0 for s in ss):
        raise DynError(f"a member of {sig} has no valid samples in {window} {t0}..{t1}")
    units = {s.unit for s in ss}
    if len(units) != 1:
        raise DynError(f"members of {op} have different units {sorted(units)}: not comparable")
    ref = max(range(len(ss)), key=lambda k: float(np.median(np.diff(ss[k].t))) if len(ss[k]) > 1 else 0.0)
    tr = np.asarray(ss[ref].t, float)
    vals, idxs, ages = [], [], []
    keep = np.ones(len(tr), bool)
    for s in ss:
        ts = np.asarray(s.t, float)
        j = np.searchsorted(ts, tr, side="right") - 1
        ok = j >= 0
        jj = np.where(ok, j, 0)
        age = tr - ts[jj]
        ok &= age <= s.rs.max_age_s
        keep &= ok
        vals.append(np.asarray(s.v, float)[jj])
        idxs.append(jj)
        ages.append(age)
    V = np.vstack(vals)[:, keep]
    A = np.vstack(ages)[:, keep]
    if op == "diff":
        v = V[0] - V[1]
    elif op == "spread":
        v = V.max(axis=0) - V.min(axis=0)
    elif op == "median":
        v = np.median(V, axis=0)
    else:
        v = V.mean(axis=0)
    res = [s.rs.resolution for s in ss]
    resolution = None if any(r is None for r in res) else (max(res) if op == "mean" else max(res))
    segs = np.vstack([_seg_ids(s)[i] for s, i in zip(ss, idxs)])[:, keep]
    seg = segs[0] * 0
    for k in range(segs.shape[0]):           # a change of source segment in any member is a boundary
        seg = seg * 1000 + segs[k]
    kinds = [ctx.profile.role(r).get("kind", "measurement") for r in roles]
    return Track(tr[keep], v, name=f"{op}({', '.join(roles)})", unit=next(iter(units)), members=ss, idx=[i[keep] for i in idxs],
                 seg=seg, age_spread=A.max(axis=0) - A.min(axis=0), resolution=resolution, kinds=kinds)


# ---- measurement floor and segmentation -----------------------------------------------------------------------------
def floor(tr: Track) -> Dict[str, Any]:
    """Sampling and measurement floor of a track, estimated from the data (the profile's resolution when it states one)."""
    t, v = tr.t, tr.v
    out: Dict[str, Any] = {"n": int(len(t))}
    if len(t) < 3:
        return {**out, "dt_median": None, "update_interval": None, "resolution": tr.resolution, "noise_sd": None, "sd_eff": tr.resolution}
    dt = np.diff(t)
    pos = dt[dt > 0]
    dtm = float(np.median(pos)) if len(pos) else None
    changes = np.flatnonzero(np.diff(v) != 0)
    upd = float(np.median(np.diff(t[changes + 1]))) if len(changes) > 2 else (float(t[-1] - t[0]) if len(t) > 1 else None)
    q, q_src = tr.resolution, "profile"
    if q is None:
        dv = np.abs(np.diff(v))
        dv = dv[dv > 1e-12]
        if len(dv):
            q0 = float(np.min(dv))
            frac = np.abs(dv / q0 - np.round(dv / q0))
            q, q_src = (q0, "inferred: smallest step, most steps are its multiples") if np.mean(frac < 0.05) > 0.9 else (None, "not quantised")
        else:
            q_src = "constant"
    d2 = np.diff(v, 2)
    mad = float(np.median(np.abs(d2 - np.median(d2)))) if len(d2) else 0.0
    sd = 1.4826 * mad / math.sqrt(6.0)
    sd_eff = math.sqrt(sd ** 2 + ((q or 0.0) ** 2) / 12.0)
    out.update(dt_median=_r(dtm), dt_p10=_r(np.percentile(pos, 10)) if len(pos) else None, dt_p90=_r(np.percentile(pos, 90)) if len(pos) else None,
               jitter=_r((np.percentile(pos, 90) - np.percentile(pos, 10)) / dtm) if len(pos) and dtm else None,
               update_interval=_r(upd), changed_fraction=_r(len(changes) / max(1, len(v) - 1)), resolution=_r(q), resolution_source=q_src,
               noise_sd=_r(sd), sd_eff=_r(sd_eff), non_increasing_steps=int(np.sum(dt <= 0)))
    return out


def segments(tr: Track, max_gap: float, breaks: Sequence[float] = ()) -> List[Tuple[int, int]]:
    """[i0, i1) index ranges with no gap > max_gap, no time reversal or repeat, no change of source segment and no break time inside."""
    n = len(tr)
    if n == 0:
        return []
    cut = np.zeros(n, bool)
    dt = np.diff(tr.t)
    cut[1:] |= (dt > max_gap) | (dt <= 0)
    cut[1:] |= tr.seg[1:] != tr.seg[:-1]
    for b in breaks:
        k = int(np.searchsorted(tr.t, b, side="left"))
        if 0 < k < n:
            cut[k] = True
    starts = [0] + list(np.flatnonzero(cut))
    starts = sorted(set(int(s) for s in starts))
    ends = starts[1:] + [n]
    return [(a, b) for a, b in zip(starts, ends) if b > a]


def _rho(res: np.ndarray) -> float:
    if len(res) < 4 or float(np.var(res)) <= 0:
        return 0.0
    r = float(np.corrcoef(res[:-1], res[1:])[0, 1])
    return 0.0 if math.isnan(r) else min(0.95, max(0.0, r))


def _infl(rho: float) -> float:
    return math.sqrt((1 + rho) / (1 - rho))


def _ols(X: np.ndarray, y: np.ndarray, sd_floor: float = 0.0) -> Dict[str, Any]:
    n, p = X.shape
    if n <= p:
        raise DynError("not enough samples for the fit")
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    res = y - X @ beta
    rho = _rho(res)
    s2 = max(float(res @ res) / (n - p), sd_floor ** 2)
    try:
        XtXi = np.linalg.inv(X.T @ X)
    except np.linalg.LinAlgError:
        XtXi = np.linalg.pinv(X.T @ X)
    cov = s2 * XtXi * (1 + rho) / (1 - rho)
    return {"beta": beta, "se": np.sqrt(np.maximum(np.diag(cov), 0)), "cov": cov, "res": res, "s": math.sqrt(s2), "rho": rho, "n": n,
            "n_eff": n * (1 - rho) / (1 + rho), "sse": float(res @ res)}


def _bin(t: np.ndarray, v: np.ndarray, width: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Average in time bins of the given width (held values sampled faster than they update are not independent)."""
    if width <= 0 or len(t) < 2:
        return t, v, np.arange(len(t))
    k = np.floor((t - t[0]) / width).astype(np.int64)
    edges = np.flatnonzero(np.diff(k)) + 1
    st = np.concatenate([[0], edges])
    tb = np.add.reduceat(t, st) / np.diff(np.concatenate([st, [len(t)]]))
    vb = np.add.reduceat(v, st) / np.diff(np.concatenate([st, [len(t)]]))
    return tb, vb, st


# ---- rate ------------------------------------------------------------------------------------------------------------
def _local_slopes(t: np.ndarray, v: np.ndarray, centers: np.ndarray, span: float, causal: bool, sd: float, rho: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """OLS slope over [c - span, c] (causal) or [c - span/2, c + span/2], O(1) per window from prefix sums."""
    tt = t - t[0]
    P = [np.concatenate([[0.0], np.cumsum(x)]) for x in (np.ones_like(tt), tt, v, tt * tt, tt * v, v * v)]
    lo_t = centers - (span if causal else span / 2)
    hi_t = centers if causal else centers + span / 2
    a = np.searchsorted(t, lo_t, side="left")
    b = np.searchsorted(t, hi_t, side="right")
    n, St, Sv, Stt, Stv, Svv = (p[b] - p[a] for p in P)
    den = n * Stt - St * St
    with np.errstate(divide="ignore", invalid="ignore"):
        slope = (n * Stv - St * Sv) / den
        icpt = (Sv - slope * St) / n
        sse = Svv - 2 * icpt * Sv - 2 * slope * Stv + icpt * icpt * n + 2 * icpt * slope * St + slope * slope * Stt
        s2 = np.maximum(sse / np.maximum(n - 2, 1), sd * sd)
        se = np.sqrt(s2 * n / den) * _infl(rho)
    bad = (n < MIN_FIT) | (den <= 0)
    slope[bad] = np.nan
    se[bad] = np.nan
    return slope, se, n


def rate_track(tr: Track, max_gap: float, span_s: Optional[float] = None, causal: bool = True, breaks: Sequence[float] = ()) -> Dict[str, Any]:
    """Rate of change along the track. Without span_s, the window adapts per point (intersection of confidence intervals):
    the longest window, from a ladder doubling from the shortest usable one, whose slope interval still overlaps those of
    every shorter window, i.e. as long as the signal is still straight within its noise, and no longer."""
    fl = floor(tr)
    sd = float(fl.get("sd_eff") or 0.0)
    upd = float(fl.get("update_interval") or fl.get("dt_median") or 0.0)
    dtm = float(fl.get("dt_median") or 0.0)
    smin = max(MIN_FIT * max(upd, dtm), 1e-9)
    T, R, S, SP, SEG = [], [], [], [], []
    for a, b in segments(tr, max_gap, breaks):
        t, v = tr.t[a:b], tr.v[a:b]
        if len(t) < MIN_FIT or t[-1] - t[0] < smin:
            continue
        dur = float(t[-1] - t[0])
        step = max(smin / 2, dur / 2000)
        centers = np.arange(t[0] + (smin if causal else smin / 2), t[-1] + 1e-9, step)
        if len(centers) == 0:
            continue
        # serial correlation of the noise at the shortest window: residuals of a coarse local line
        rho = 0.0
        if len(t) > 3 * MIN_FIT:
            sl, _, _ = _local_slopes(t, v, t, smin, causal, sd, 0.0)
            ok = ~np.isnan(sl)
            if ok.sum() > 3:
                d = np.diff(v)
                rho = _rho(d - np.median(d)) if upd <= dtm * 1.5 else 0.0
        if span_s:
            sl, se, n = _local_slopes(t, v, centers, float(span_s), causal, sd, rho)
            sp = np.full(len(centers), float(span_s))
        else:
            spans = [smin * 2 ** k for k in range(0, 12) if smin * 2 ** k <= max(smin, dur)]
            lo = np.full(len(centers), -np.inf)
            hi = np.full(len(centers), np.inf)
            sl = np.full(len(centers), np.nan)
            se = np.full(len(centers), np.nan)
            sp = np.full(len(centers), np.nan)
            alive = np.ones(len(centers), bool)
            for w in spans:
                s_, e_, _ = _local_slopes(t, v, centers, w, causal, sd, rho)
                ok = ~np.isnan(s_)
                lo2 = np.maximum(lo, s_ - Z * e_)
                hi2 = np.minimum(hi, s_ + Z * e_)
                still = alive & ok & (lo2 <= hi2)
                sl[still], se[still], sp[still] = s_[still], e_[still], w
                lo[still], hi[still] = lo2[still], hi2[still]
                alive &= still | ~ok
                alive &= ok | np.isnan(sl)
        good = ~np.isnan(sl)
        T.append(centers[good]); R.append(sl[good]); S.append(se[good]); SP.append(sp[good]); SEG.append(np.full(int(good.sum()), a))
    if not T:
        raise DynError(f"{tr.name}: no segment long enough for a rate (needs {MIN_FIT} samples over at least {_r(smin, 3)} s without a gap)")
    return {"t": np.concatenate(T), "rate": np.concatenate(R), "se": np.concatenate(S), "span": np.concatenate(SP),
            "seg_start": np.concatenate(SEG), "floor": fl, "min_span": smin}


def _nearest(tr: Track, t: float) -> int:
    """The latest sample at or before t (never a later one: a reference must not point past what was read)."""
    return int(min(len(tr) - 1, max(0, np.searchsorted(tr.t, t, side="right") - 1)))


def _pick(arr: Dict[str, np.ndarray], n: int = TRACK_ITEMS) -> List[Dict[str, Any]]:
    k = len(arr["t"])
    sel = np.unique(np.linspace(0, k - 1, min(n, k)).astype(int)) if k else []
    return [{"t": _r(arr["t"][i], 3), "rate": _r(arr["rate"][i], 9), "se": _r(arr["se"][i], 9), "span_s": _r(arr["span"][i], 3)} for i in sel]


def _windows(ctx: C.Context, window: str) -> None:
    if window not in ctx.rec.windows:
        raise DynError(f"unknown window {window!r}; windows: {', '.join(ctx.rec.windows)}")


def _breaks(ctx: C.Context, window: str, segment_by: Optional[List[str]], t0: Optional[float], t1: Optional[float]) -> List[float]:
    out: List[float] = []
    for r in segment_by or []:
        s = ctx.series(r, window, t0, t1)
        out += [s.t[i] for i in range(1, len(s)) if s.v[i] != s.v[i - 1]]
    return sorted(out)


def _out(op: str, q: Dict[str, Any], kinds: Dict[str, str], inputs: List[Dict[str, Any]], refs: List[Dict[str, Any]], assumptions: List[str],
         notes: Optional[List[str]] = None, **extra: Any) -> Dict[str, Any]:
    r = C._out(op, q, kinds, inputs, refs, [f"significance: Z = {Z} (two-sided, about 95 %); standard errors inflated for lag-1 serial correlation"] + assumptions,
               notes, **extra)
    r["version"] = DYN_VERSION
    return r


def _estimate_note(tr: Track) -> List[str]:
    if "estimate" in tr.kinds:
        return [f"{tr.name} is an ESTIMATE the machine computes: its rate is the rate of the reported estimate (a correction appears as a "
                "step), not the rate of the physical quantity"]
    return []


def rate(ctx: C.Context, signal: Signal, window: str, t0: Optional[float] = None, t1: Optional[float] = None, span_s: Optional[float] = None,
         causal: bool = True, segment_by: Optional[List[str]] = None) -> Dict[str, Any]:
    """Rate of change (unit per second) on actual timestamps. Window: span_s seconds if given, else adaptive per point.
    causal: the window ends at the point (only past data, as a monitor would see it); else centred."""
    _windows(ctx, window)
    tr = track(ctx, signal, window, t0, t1)
    rt = rate_track(tr, ctx.profile.max_gap_s, span_s, causal, _breaks(ctx, window, segment_by, t0, t1))
    r, se, t = rt["rate"], rt["se"], rt["t"]
    imin, imax = int(np.nanargmin(r)), int(np.nanargmax(r))
    fl = rt["floor"]
    q = fl.get("resolution") or 0.0
    span_med = float(np.median(rt["span"]))
    first, last = slice(0, max(1, len(r) // 10)), slice(len(r) - max(1, len(r) // 10), len(r))
    early, late = float(np.median(r[first])), float(np.median(r[last]))
    q_out = {"unit": f"{tr.unit}/s", "points": int(len(r)), "window_s_median": _r(span_med, 3), "window": "adaptive" if not span_s else f"{span_s} s",
             "causal": causal, "rate_min": _r(r[imin], 9), "rate_min_t": _r(t[imin], 3), "rate_min_se": _r(se[imin], 9),
             "rate_max": _r(r[imax], 9), "rate_max_t": _r(t[imax], 3), "rate_max_se": _r(se[imax], 9),
             "rate_first_tenth_median": _r(early, 9), "rate_last_tenth_median": _r(late, 9),
             "acceleration": _r((late - early) / max(1e-9, float(t[last][0] - t[first][0])), 12) if len(r) > 1 else None,
             "resolution_floor_rate": _r(q / span_med, 9) if q and span_med else None,
             "floor": fl}
    kinds = {k: "observation" for k in q_out}
    kinds["floor"] = "inferred_parameter"
    refs = []
    for i in (imin, imax):
        k = _nearest(tr, float(t[i]))
        refs += tr.refs(k)
        j = _nearest(tr, float(t[i] - (rt["span"][i] if causal else rt["span"][i] / 2)))
        refs += tr.refs(j)
    notes = _estimate_note(tr)
    if q and abs(q_out["rate_min"] or 0) < 2 * q / span_med and abs(q_out["rate_max"] or 0) < 2 * q / span_med:
        notes.append("every rate is within two quantisation steps per window: indistinguishable from the resolution of the recording")
    return _out("rate", q_out, kinds, tr.inputs(), refs,
                [f"windows never cross a gap > {ctx.profile.max_gap_s} s, a time reversal, a source-segment change"
                 + (f" or a change of {', '.join(segment_by)}" if segment_by else ""),
                 "adaptive window: the longest window (doubling from MIN_FIT update intervals) whose slope interval overlaps every shorter one"
                 if not span_s else f"fixed window {span_s} s on actual timestamps"],
                notes, items=_pick(rt))


# ---- change points --------------------------------------------------------------------------------------------------
def _hinge_fit(t: np.ndarray, y: np.ndarray, taus: Sequence[float]) -> Tuple[float, np.ndarray]:
    cols = [np.ones_like(t), t - t[0]] + [np.maximum(t - tau, 0.0) for tau in taus]
    X = np.vstack(cols).T
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    res = y - X @ beta
    return float(res @ res), beta


def _prep(tr: Track, t0: Optional[float], t1: Optional[float], max_gap: float) -> Tuple[np.ndarray, np.ndarray, Dict[str, Any], float]:
    fl = floor(tr)
    upd = float(fl.get("update_interval") or fl.get("dt_median") or 0.0)
    m = np.ones(len(tr), bool)
    if t0 is not None:
        m &= tr.t >= t0
    if t1 is not None:
        m &= tr.t <= t1
    t, v = tr.t[m], tr.v[m]
    width = max(upd, (t[-1] - t[0]) / MAX_POINTS if len(t) > 1 else 0.0)
    tb, vb, _ = _bin(t, v, width)
    return tb, vb, fl, width


def changepoint_fit(t: np.ndarray, y: np.ndarray, max_breaks: int = 2, sd_floor: float = 0.0, grid: int = 160) -> Dict[str, Any]:
    """Continuous piecewise-linear fit (a line plus hinges). Breaks are added greedily while the information criterion
    BIC = n_eff ln(SSE / n) + p ln(n_eff) improves; n_eff corrects for serial correlation. Each break's interval: the
    break times whose SSE is within the 95 % profile-likelihood bound (n_eff ln(SSE / SSE_min) <= 3.84)."""
    n = len(t)
    if n < 3 * MIN_FIT:
        raise DynError("too few samples for a change-point fit")
    lo, hi = np.percentile(t, 5), np.percentile(t, 95)
    cands = np.linspace(lo, hi, grid)
    sse0, b0 = _hinge_fit(t, y, [])
    rho = _rho(y - np.vstack([np.ones_like(t), t - t[0]]).T @ b0)

    def bic(sse: float, p: int, ne: float) -> float:
        return ne * math.log(max(sse, n * sd_floor ** 2 / 12 + 1e-30) / n) + p * math.log(max(ne, 2))
    taus: List[float] = []
    cur_sse = sse0
    base_ne = n * (1 - rho) / (1 + rho)
    cur_bic = bic(sse0, 2, base_ne)
    profile: List[np.ndarray] = []
    for _ in range(max_breaks):
        sses = np.array([_hinge_fit(t, y, taus + [c])[0] if all(abs(c - x) > (hi - lo) / grid * 2 for x in taus) else np.inf for c in cands])
        k = int(np.argmin(sses))
        if not np.isfinite(sses[k]):
            break
        trial = taus + [float(cands[k])]
        _, bt = _hinge_fit(t, y, trial)
        cols = np.vstack([np.ones_like(t), t - t[0]] + [np.maximum(t - x, 0.0) for x in trial]).T
        r2 = _rho(y - cols @ bt)
        ne = n * (1 - r2) / (1 + r2)
        b = bic(float(sses[k]), 2 + len(trial), ne)
        if b >= cur_bic:
            break
        taus, cur_sse, cur_bic = trial, float(sses[k]), b
        profile.append(sses)
    sse, beta = _hinge_fit(t, y, taus)
    cols = np.vstack([np.ones_like(t), t - t[0]] + [np.maximum(t - x, 0.0) for x in taus]).T
    fit = _ols(cols, y, sd_floor)
    order = np.argsort(taus)
    breaks = []
    slope = float(fit["beta"][1])
    for j in order:
        ci = [None, None]
        prof = profile[j] if j < len(profile) else None
        if prof is not None:
            ok = cands[np.isfinite(prof) & (fit["n_eff"] * np.log(np.maximum(prof, 1e-30) / max(prof.min(), 1e-30)) <= 3.84)]
            stepg = float(cands[1] - cands[0]) if len(cands) > 1 else 0.0       # the grid's own resolution widens the interval
            ci = [float(ok.min()) - stepg, float(ok.max()) + stepg] if len(ok) else ci
        dslope, dse = float(fit["beta"][2 + j]), float(fit["se"][2 + j])
        breaks.append({"t": taus[j], "t_interval": ci, "slope_before": slope, "slope_after": slope + dslope, "slope_change": dslope,
                       "slope_change_se": dse, "slope_change_z": dslope / dse if dse > 0 else None})
        slope += dslope
    return {"breaks": breaks, "slope_initial": float(fit["beta"][1]), "slope_initial_se": float(fit["se"][1]), "n": n, "n_eff": fit["n_eff"],
            "rho": fit["rho"], "residual_sd": fit["s"], "bic": cur_bic}


def changepoint(ctx: C.Context, signal: Signal, window: str, t0: Optional[float] = None, t1: Optional[float] = None, max_breaks: int = 2) -> Dict[str, Any]:
    """Where the signal's rate of change changes: a continuous piecewise-linear fit with up to max_breaks change points,
    each kept only if it improves the information criterion (no threshold). Reports each change point with its 95 %
    interval and the rate before and after (acceleration = rate after - rate before)."""
    _windows(ctx, window)
    tr = track(ctx, signal, window, t0, t1)
    segs = segments(tr, ctx.profile.max_gap_s)
    use = [(a, b) for a, b in segs if b - a >= MIN_FIT]
    if not use:
        raise DynError(f"{tr.name}: no segment with {MIN_FIT} samples")
    ix = np.concatenate([np.arange(a, b) for a, b in use])
    dropped = len(tr) - len(ix)
    sub = Track(tr.t[ix], tr.v[ix], name=tr.name, unit=tr.unit, members=tr.members, idx=[i[ix] for i in tr.idx], seg=tr.seg[ix],
                age_spread=None if tr.age_spread is None else tr.age_spread[ix], resolution=tr.resolution, kinds=tr.kinds)
    t, y, fl, width = _prep(sub, None, None, ctx.profile.max_gap_s)
    cp = changepoint_fit(t, y, int(max_breaks), float(fl.get("sd_eff") or 0.0))
    q = {"unit_rate": f"{tr.unit}/s", "span": [_r(t[0], 3), _r(t[-1], 3)], "breaks": [{k: _r(v, 9) if not isinstance(v, list) else [_r(x, 3) for x in v]
                                                                                    for k, v in br.items()} for br in cp["breaks"]],
         "break_count": len(cp["breaks"]), "rate_initial": _r(cp["slope_initial"], 9), "rate_final": _r(cp["breaks"][-1]["slope_after"] if cp["breaks"] else cp["slope_initial"], 9),
         "n": cp["n"], "n_eff": _r(cp["n_eff"], 1), "residual_sd": _r(cp["residual_sd"], 9), "bin_width_s": _r(width, 4)}
    kinds = {k: "observation" for k in q}
    kinds["breaks"] = "inferred_parameter"
    refs = sub.refs(0) + sub.refs(len(sub) - 1) + [r for br in cp["breaks"] for r in sub.refs(_nearest(sub, br["t"]))]
    notes = _estimate_note(tr)
    if dropped:
        notes.append(f"{dropped} samples in segments shorter than {MIN_FIT} samples (overlapping sources, isolated samples) were left out of the fit")
    if len(use) > 1:
        notes.append(f"the fit spans {len(use)} segments separated by gaps or source changes (a global trend model; no derivative is taken across them)")
    return _out("changepoint", q, kinds, tr.inputs(), refs,
                [f"samples averaged in bins of {_r(width, 4)} s (at least one update interval: held values are not independent samples)",
                 "change points kept only while the serial-correlation-corrected BIC improves; 95 % profile-likelihood interval per change point"],
                notes)


# ---- baseline relationship and departure ----------------------------------------------------------------------------
def _aligned(ctx: C.Context, y: Track, preds: List[Signal], window: str) -> Tuple[np.ndarray, List[str], np.ndarray]:
    """Predictors at y's timestamps (latest fresh sample). The predictor "time" is the timestamp itself (a steady trend)."""
    cols, names = [], []
    keep = np.ones(len(y), bool)
    for p in preds:
        if p == "time":
            cols.append(y.t - y.t[0])
            names.append("time")
            continue
        pt = track(ctx, p, window)
        j = np.searchsorted(pt.t, y.t, side="right") - 1
        ok = j >= 0
        jj = np.where(ok, j, 0)
        max_age = max(m.rs.max_age_s for m in pt.members)
        ok &= (y.t - pt.t[jj]) <= max_age
        keep &= ok
        cols.append(pt.v[jj])
        names.append(pt.name)
    return (np.vstack(cols).T if cols else np.zeros((len(y), 0))), names, keep


def _event_time(ctx: C.Context, window: str, ev: Optional[Dict[str, Any]], after: Optional[float]) -> Optional[Dict[str, Any]]:
    """An event: {role, kind}: kind "first_change" (first change of a state after `after`), "reaches_max" (first time the
    role reaches its highest value in the window), "becomes" with value, or "crosses" with op and value."""
    if not ev:
        return None
    s = ctx.series(ev["role"], window)
    if len(s) == 0:
        return None
    kind = ev.get("kind", "first_change")
    t, v = np.asarray(s.t), np.asarray(s.v)
    lo = -np.inf if after is None else after
    idx = None
    if kind == "first_change":
        ch = np.flatnonzero((np.diff(v) != 0) & (t[1:] > lo)) + 1
        idx = int(ch[0]) if len(ch) else None
    elif kind == "reaches_max":
        hits = np.flatnonzero((v == v.max()) & (t > lo))
        idx = int(hits[0]) if len(hits) and (hits[0] > 0 and v[hits[0] - 1] != v.max()) else None
    elif kind == "becomes":
        hits = np.flatnonzero((v == float(ev["value"])) & (t > lo))
        hits = [h for h in hits if h > 0 and v[h - 1] != float(ev["value"])]
        idx = int(hits[0]) if hits else None
    elif kind == "crosses":
        fn = C._OPS[ev.get("op", "<")]
        hits = [i for i in range(len(v)) if t[i] > lo and fn(v[i], float(ev["value"]))]
        idx = hits[0] if hits else None
    else:
        raise DynError("event kind must be first_change, reaches_max, becomes or crosses")
    if idx is None:
        return None
    prev = float(t[idx - 1]) if idx > 0 else None
    return {"t": float(t[idx]), "t_previous_sample": prev, "role": ev["role"], "kind": kind, "value": float(v[idx]),
            "ref": s.ref(idx)}


def departure_fit(y: np.ndarray, X: np.ndarray, t: np.ndarray, base: np.ndarray, ana: np.ndarray, sd_floor: float,
                  block_s: Optional[float] = None) -> Dict[str, Any]:
    """Fit y ~ 1 + X on the baseline mask, predict on the analysis mask AFTER the baseline ends (monitoring starts there).
    Residual = y - prediction. Block means of the residual with their standard error (parameter uncertainty + noise with
    serial correlation) give z per block."""
    Xb = np.column_stack([np.ones(int(base.sum())), X[base]]) if X.shape[1] else np.ones((int(base.sum()), 1))
    fit = _ols(Xb, y[base], sd_floor)
    Xa = np.column_stack([np.ones(int(ana.sum())), X[ana]]) if X.shape[1] else np.ones((int(ana.sum()), 1))
    after = t[ana] > float(t[base][-1])          # monitoring starts where the baseline ends
    if after.sum() < 2:
        raise DynError("the analysis span has no samples after the baseline")
    Xa = Xa[after]
    pred = Xa @ fit["beta"]
    res = y[ana][after] - pred
    ta = t[ana][after]
    span_b = float(t[base][-1] - t[base][0])
    span_a = float(ta[-1] - ta[0]) if len(ta) > 1 else 0.0
    L = block_s or max(span_b / 4, span_a / 20, 1e-9)
    k = np.floor((ta - ta[0]) / L).astype(int)
    blocks = []
    infl2 = (1 + fit["rho"]) / (1 - fit["rho"])
    for b in np.unique(k):
        m = k == b
        if m.sum() < 2:
            continue
        xbar = Xa[m].mean(axis=0)
        var_par = float(xbar @ fit["cov"] @ xbar)
        var_noise = fit["s"] ** 2 * infl2 / m.sum()
        se = math.sqrt(var_par + var_noise)
        mean = float(res[m].mean())
        blocks.append({"t0": float(ta[m][0]), "t1": float(ta[m][-1]), "mean": mean, "se": se, "z": mean / se if se > 0 else 0.0, "n": int(m.sum())})
    # earliest persistent detection: first block from which every later block departs in the same direction (|z| >= Z)
    first_sig = None
    if blocks:
        sign = 1 if blocks[-1]["mean"] >= 0 else -1
        for i in range(len(blocks)):
            if all(sign * b_["z"] >= Z for b_ in blocks[i:]):
                first_sig = blocks[i]
                break
    # onset: a hinge from zero in the residual, if it beats "no departure" on BIC
    onset = None
    if len(ta) >= 3 * MIN_FIT:
        rr = res
        ne_null = len(rr) * (1 - fit["rho"]) / (1 + fit["rho"])
        sse_null = float(rr @ rr)
        cands = np.linspace(ta[0], ta[-1], 160)[:-8]
        sses = []
        for c in cands:
            h = np.maximum(ta - c, 0.0)
            hh = float(h @ h)
            sses.append(sse_null - (float(h @ rr) ** 2) / hh if hh > 0 else np.inf)
        sses = np.array(sses)
        j = int(np.argmin(sses))
        nn = len(rr)
        floor2 = nn * sd_floor ** 2 / 12 + 1e-30
        bic_null = ne_null * math.log(max(sse_null, floor2) / nn)
        bic_h = ne_null * math.log(max(float(sses[j]), floor2) / nn) + 2 * math.log(max(ne_null, 2))
        if bic_h < bic_null:
            ok = cands[np.isfinite(sses) & (ne_null * np.log(np.maximum(sses, 1e-30) / max(sses[j], 1e-30)) <= 3.84)]
            h = np.maximum(ta - cands[j], 0.0)
            stepg = float(cands[1] - cands[0]) if len(cands) > 1 else 0.0
            onset = {"t": float(cands[j]), "t_interval": [float(ok.min()) - stepg, float(ok.max()) + stepg] if len(ok) else None,
                     "rate_after": float(h @ rr) / float(h @ h)}
    last = blocks[-1] if blocks else None
    # jumps: changes of the residual between consecutive samples beyond OUTLIER_Z robust sd of the baseline's changes
    rb = fit["res"]
    db = np.diff(rb)
    sd_d = max(1.4826 * float(np.median(np.abs(db - np.median(db)))) if len(db) else 0.0, sd_floor * math.sqrt(2) / math.sqrt(12) * 2, 1e-15)
    jb = int(np.sum(np.abs(db) > OUTLIER_Z * sd_d))
    ja = int(np.sum(np.abs(np.diff(res)) > OUTLIER_Z * sd_d))
    tb_span = max(span_b, 1e-9)
    lam = (jb + 0.5) / tb_span
    exp_a = lam * max(span_a, 1e-9)
    jumps = {"baseline": jb, "analysis": ja, "expected_in_analysis": exp_a, "excess_z": (ja - exp_a) / math.sqrt(exp_a + 1.0), "step_sd": sd_d}
    return {"fit": fit, "blocks": blocks, "last": last, "first_significant": first_sig, "onset": onset, "block_s": L, "res": res, "t": ta, "jumps": jumps}


def _norm_span(sp: Optional[Dict[str, Any]], dflt: Tuple[float, float]) -> Tuple[float, float]:
    if not sp:
        return dflt
    return (float(sp.get("t0", dflt[0])), float(sp.get("t1", dflt[1])))


def _ordering(first_sig: Optional[Dict[str, Any]], onset: Optional[Dict[str, Any]], event: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not event:
        return {"ordering": "no_event"}
    te = event["t"]
    unc = te - (event.get("t_previous_sample") or te)
    if first_sig is None:
        return {"ordering": "no_departure_before_event", "event_t": te}
    tdet = first_sig["t1"]
    o = "precedes" if tdet < te - unc else "coincides" if tdet <= te + unc else "follows"
    out = {"ordering": o, "event_t": te, "event_time_uncertainty_s": unc, "detected_by_t": tdet, "lead_s": te - tdet}
    if onset:
        out["onset_t"], out["onset_lead_s"] = onset["t"], te - onset["t"]
    return out


def departure(ctx: C.Context, signal: Signal, window: str, predictors: Optional[List[Signal]] = None, baseline: Optional[Dict[str, Any]] = None,
              analysis: Optional[Dict[str, Any]] = None, event: Optional[Dict[str, Any]] = None, baseline_fraction: float = 1 / 3) -> Dict[str, Any]:
    """Does the signal leave the relationship it had with its drivers (predictors, e.g. load) on a baseline span, and when?
    Fits signal ~ 1 + predictors on the baseline (default: the first baseline_fraction of the analysis span before the
    event), predicts it over the analysis span, and reports the residual's departure: last-block z, the onset (a hinge
    from zero, kept only if it beats "no departure" on BIC) with its interval, the earliest block from which every later
    block departs (causal: what a monitor could have seen), and the ordering against an event. Precedence is not causation."""
    _windows(ctx, window)
    preds = list(predictors or [])
    y = track(ctx, signal, window)
    X, names, keep = _aligned(ctx, y, preds, window)
    t, v = y.t[keep], y.v[keep]
    X = X[keep]
    if len(t) < 3 * MIN_FIT:
        raise DynError(f"too few aligned samples of {y.name} and its predictors")
    ev = _event_time(ctx, window, event, None) if event else None
    a0, a1 = _norm_span(analysis, (float(t[0]), float(ev["t"]) if ev else float(t[-1])))
    b0, b1 = _norm_span(baseline, (a0, a0 + (a1 - a0) * float(baseline_fraction)))
    fl = floor(y)
    upd = float(fl.get("update_interval") or fl.get("dt_median") or 0.0)
    width = max(upd, (a1 - a0) / MAX_POINTS)
    # bin y and X together on y's time base
    m = (t >= min(a0, b0)) & (t <= max(a1, b1))
    t, v, X = t[m], v[m], X[m]
    tb, vb, st = _bin(t, v, width)
    cnt = np.diff(np.concatenate([st, [len(t)]]))
    Xb = np.vstack([np.add.reduceat(X[:, j], st) / cnt for j in range(X.shape[1])]).T if X.shape[1] else np.zeros((len(tb), 0))
    base = (tb >= b0) & (tb <= b1)
    ana = (tb >= a0) & (tb <= a1)
    if base.sum() < 2 * MIN_FIT or ana.sum() < 2 * MIN_FIT:
        raise DynError(f"baseline {b0}..{b1} or analysis {a0}..{a1} has too few samples")
    notes = _estimate_note(y)
    pv = []
    for j, nm in enumerate(names):
        sd_b, sd_a = float(np.std(Xb[base, j])), float(np.std(Xb[ana, j]))
        pv.append({"predictor": nm, "baseline_sd": _r(sd_b), "analysis_sd": _r(sd_a)})
        if sd_b <= 1e-12:
            notes.append(f"{nm} does not vary in the baseline: its coefficient is not identifiable and it was dropped")
    use = [j for j in range(len(names)) if float(np.std(Xb[base, j])) > 1e-12]
    if len(use) < len(names):
        Xb, names = Xb[:, use], [names[j] for j in use]
    sd_floor = float(fl.get("sd_eff") or 0.0)
    d = departure_fit(vb, Xb, tb, base, ana, sd_floor)
    # jumps on the RAW samples (bins would average a short glitch away): residual against the baseline relation
    Xr = X[:, use] if len(use) < X.shape[1] else X
    rr = v - (np.column_stack([np.ones(len(t)), Xr]) @ d["fit"]["beta"] if Xr.shape[1] else d["fit"]["beta"][0])
    chg = np.flatnonzero(np.diff(v) != 0) + 1                       # only where the reading updated (held values are not changes)
    dres = np.diff(rr)[chg - 1] if len(chg) else np.zeros(0)
    tch = t[chg] if len(chg) else np.zeros(0)
    okdt = (np.diff(t)[chg - 1] <= ctx.profile.max_gap_s) if len(chg) else np.zeros(0, bool)
    qres = float(fl.get("resolution") or 0.0)
    # A jump the driver explains is not a sensing signature: a change of y within one update interval of a driver step
    # (|dx| beyond OUTLIER_Z x the driver's own robust step noise) is excluded. A component whose load coupling grows
    # makes its residual jump AT load steps; a measurement fault makes it jump without one (dynamics-v2).
    near_step = np.zeros(len(tch), bool)
    tol = max(upd, float(np.median(np.diff(t))) if len(t) > 1 else 0.0)
    for j in range(Xr.shape[1] if Xr.ndim == 2 else 0):
        dx = np.diff(Xr[:, j])
        mad = 1.4826 * float(np.median(np.abs(dx - np.median(dx)))) if len(dx) else 0.0
        nz = np.abs(dx[dx != 0])
        res_x = float(nz.min()) if len(nz) else 0.0
        st = t[1:][np.abs(dx) > OUTLIER_Z * max(mad, res_x, 1e-12)]
        if len(st) and len(tch):
            k_ = np.searchsorted(st, tch)
            dl = np.abs(tch - st[np.clip(k_ - 1, 0, len(st) - 1)])
            dr = np.abs(st[np.clip(k_, 0, len(st) - 1)] - tch)
            near_step |= np.minimum(dl, dr) <= tol
    okdt = okdt & ~near_step
    inb, ina = (tch >= b0) & (tch <= b1) & okdt, (tch > b1) & (tch <= a1) & okdt
    db = dres[inb]
    sd_d = max(1.4826 * float(np.median(np.abs(db - np.median(db)))) if len(db) else 0.0, qres, 1e-15)
    jb, ja = int(np.sum(np.abs(db) > OUTLIER_Z * sd_d)), int(np.sum(np.abs(dres[ina]) > OUTLIER_Z * sd_d))
    lam = (jb + 0.5) / max(b1 - b0, 1e-9)
    exp_a = lam * max(a1 - b1, 1e-9)
    d["jumps"] = {"baseline": jb, "analysis": ja, "expected_in_analysis": exp_a, "excess_z": (ja - exp_a) / math.sqrt(exp_a + 1.0), "step_sd": sd_d}
    fit, last = d["fit"], d["last"]
    order = _ordering(d["first_significant"], d["onset"], ev)
    # extrapolation: predictors outside the range seen in the baseline
    extrap = []
    for j, nm in enumerate(names):
        lo, hi = float(Xb[base, j].min()), float(Xb[base, j].max())
        out_frac = float(np.mean((Xb[ana, j] < lo) | (Xb[ana, j] > hi)))
        if out_frac > 0.2:
            extrap.append({"predictor": nm, "fraction_outside_baseline_range": _r(out_frac, 3)})
    if extrap:
        notes.append("the analysis span takes predictors outside the range seen in the baseline: the departure partly reflects extrapolation of "
                     "the baseline relationship (a curved but healthy relationship can look like a departure)")
    jm = d["jumps"]
    q = {"signal": y.name, "unit": y.unit, "baseline": [_r(b0, 3), _r(b1, 3)], "analysis": [_r(a0, 3), _r(a1, 3)],
         "predictors": names, "coefficients": {nm: {"value": _r(fit["beta"][1 + j], 9), "se": _r(fit["se"][1 + j], 9)} for j, nm in enumerate(names)},
         "baseline_residual_sd": _r(fit["s"], 9), "baseline_rho": _r(fit["rho"], 3),
         "departure_last": _r(last["mean"], 9) if last else None, "departure_last_se": _r(last["se"], 9) if last else None,
         "departure_z": _r(last["z"], 3) if last else None, "departure_abs_z": _r(abs(last["z"]), 3) if last else None,
         "onset_t": _r(d["onset"]["t"], 3) if d["onset"] else None, "onset_interval": [_r(x, 3) for x in (d["onset"] or {}).get("t_interval") or []] or None,
         "first_significant_t": _r(d["first_significant"]["t1"], 3) if d["first_significant"] else None,
         "jumps_baseline": jm["baseline"], "jumps_analysis": jm["analysis"], "jump_excess_z": _r(jm["excess_z"], 3),
         "jump_rule": f"a jump: at an update of the reading, the residual changes by more than {OUTLIER_Z} x max(robust sd of its baseline changes, resolution)",
         "event": {k: _r(v2, 3) if not isinstance(v2, (dict, str)) else v2 for k, v2 in (ev or {}).items() if k != "ref"} or None,
         **{k: _r(v2, 3) if not isinstance(v2, str) else v2 for k, v2 in order.items()},
         "precedes_event": 1 if order.get("ordering") == "precedes" else 0 if ev else None,
         "predictor_variation": pv, "block_s": _r(d["block_s"], 3)}
    kinds = {k: "observation" for k in q}
    for k in ("coefficients", "baseline_residual_sd", "baseline_rho"):
        kinds[k] = "inferred_parameter"
    for k in ("departure_last", "departure_last_se", "departure_z", "departure_abs_z", "onset_t", "onset_interval", "first_significant_t", "lead_s",
              "onset_lead_s", "precedes_event", "ordering", "detected_by_t"):
        if k in kinds:
            kinds[k] = "conditional"
    refs = []
    for tt in (b0, b1, a1):
        refs += y.refs(_nearest(y, tt))
    if d["onset"]:
        refs += y.refs(_nearest(y, d["onset"]["t"]))
    if ev:
        refs.append(ev["ref"])
    ass = [f"baseline {_r(b0, 3)}..{_r(b1, 3)} s is taken as the machine's reference behaviour (the departure is relative to it; a fault "
           "already present in the baseline is part of the reference)",
           f"relationship fitted on the baseline: {y.name} ~ 1 + {' + '.join(names) or '(constant)'}; linear in each predictor",
           f"samples averaged in bins of {_r(width, 4)} s; predictors at their latest fresh sample",
           f"blocks of {_r(d['block_s'], 3)} s for the departure z; earliest persistent detection = first block after which every block departs "
           f"(|z| >= {Z}) in the final direction"]
    if ev:
        ass.append(f"event: {event}")
        notes.append("a departure that precedes an event is a candidate precursor, not evidence that it caused the event")
    return _out("departure", q, kinds, y.inputs(), refs, ass, notes,
                items=[{k: _r(v2, 9 if k in ("mean", "se") else 3) for k, v2 in b_.items()} for b_ in d["blocks"]][:TRACK_ITEMS])


# ---- divergence of comparable signals -------------------------------------------------------------------------------
def _step_coupling(t: np.ndarray, y: np.ndarray, x: np.ndarray, x_floor: float, max_dt: float, settle: float) -> Dict[str, Any]:
    """dy/dx at the driver's steps: x changes by more than OUTLIER_Z x its noise floor between consecutive samples; y is
    read just before the step and `settle` seconds after it (at least one update interval of y and the members' sample-age
    skew, so a held or skewed reading is not taken for no response). Immune to slow trends."""
    dt, dx = np.diff(t), np.diff(x)
    st = np.flatnonzero((dt > 0) & (dt <= max_dt) & (np.abs(dx) > OUTLIER_Z * max(x_floor, 1e-12)))
    r = []
    for i in st:
        j = int(np.searchsorted(t, t[i + 1] + settle, side="left"))
        if j >= len(t) or t[j] - t[i + 1] > settle + max_dt:
            continue
        k = int(np.searchsorted(t, t[j], side="left"))
        if np.any(np.abs(np.diff(x[i + 1:k + 1])) > OUTLIER_Z * max(x_floor, 1e-12)):
            continue                                   # another step before the response was read
        r.append((y[j] - y[i]) / (x[i + 1] - x[i]))
    if len(r) < 5:
        return {"steps": int(len(r)), "median": None, "se": None}
    ra = np.asarray(r)
    med = float(np.median(ra))
    mad = 1.4826 * float(np.median(np.abs(ra - med)))
    return {"steps": int(len(ra)), "median": med, "se": 1.2533 * max(mad, 1e-15) / math.sqrt(len(ra))}


# ---- dynamics-v3: materiality as evidence with uncertainty; load-coupling growth; intermittent transients --------------
MATERIAL = 0.5     # "at least half of the change it belongs to" (dynamics-v2's design choice), now judged on an interval
IMMATERIAL = 1 / 3 # dismissing needs more: the factor itself is uncertain, and a false exclusion removes a true cause


def materiality(est: List[Tuple[Optional[float], Optional[float]]]) -> Dict[str, Any]:
    """A share judged against MATERIAL with its uncertainty: every (estimate, standard error) pair (one per baseline choice)
    gives an interval estimate +- Z se. MATERIAL when every interval lies at or above 1/2 (what a flag needs); IMMATERIAL when
    every one lies below 1/3 (what a dismissal needs: the factor 1/2 is a design choice, so a share just under it is not
    evidence against a cause); otherwise undetermined (neither counted nor dismissed: evidence, not an exclusion law)."""
    ok = [(e, se or 0.0) for e, se in est if e is not None]
    if not ok:
        return {"verdict": None, "interval": None}
    lo = min(e - Z * se for e, se in ok)
    hi = max(e + Z * se for e, se in ok)
    v = "material" if lo >= MATERIAL else "immaterial" if hi < IMMATERIAL else "undetermined"
    return {"verdict": v, "interval": [_r(lo, 4), _r(hi, 4)], "estimates": [_r(e, 4) for e, _ in ok]}


def _noise_sd(v: np.ndarray) -> float:
    d = np.diff(v)
    nz = d[d != 0]
    if len(nz) < 3:
        return 0.0
    return max(1.4826 * float(np.median(np.abs(nz - np.median(nz)))) / math.sqrt(2), float(np.min(np.abs(nz))) / math.sqrt(12))


def _median_change(tr: "Track", b0: float, b1: float, l0: float, l1: float) -> Tuple[Optional[float], Optional[float]]:
    """median(late) - median(baseline) and its standard error (white-noise sd of the reading; n = updates, not held samples)."""
    mb, ml = (tr.t >= b0) & (tr.t <= b1), (tr.t >= l0) & (tr.t <= l1)
    if not mb.any() or not ml.any():
        return None, None
    vb, vl = tr.v[mb], tr.v[ml]
    nb = 1 + int(np.sum(np.diff(vb) != 0))
    nl = 1 + int(np.sum(np.diff(vl) != 0))
    sd = _noise_sd(tr.v[(tr.t >= b0) & (tr.t <= l1)])
    return float(np.median(vl) - np.median(vb)), 1.2533 * sd * math.sqrt(1 / nb + 1 / nl)


def _ratio_se(a: float, sa: float, b: float, sb: float) -> float:
    """se of a / b by the delta method."""
    return math.sqrt((sa / b) ** 2 + (a * sb / b ** 2) ** 2) if b else float("inf")


def _driver_steps(t: np.ndarray, x: np.ndarray, x_floor: float, max_dt: float) -> np.ndarray:
    dt, dx = np.diff(t), np.diff(x)
    return np.flatnonzero((dt > 0) & (dt <= max_dt) & (np.abs(dx) > OUTLIER_Z * max(x_floor, 1e-12)))


def coupling_growth(ctx: C.Context, lim: str, peers: List[str], driver: str, window: str, t0: float, t1: float,
                    limit_side: Optional[str]) -> Dict[str, Any]:
    """Does ONE member's response per unit of the driver grow over the span, relative to its peers? At every driver step,
    the limiting member's change minus its peers' (median) change, regressed on the driver's change with a term that grows
    linearly over the span: dy = c0 dx + c1 (s) dx, s = 0..1. c1 is the growth of the member's extra coupling (oriented
    toward the limit side), compared with the group's own coupling (the peers' change per unit driver). A resistance- or
    torque-constant-like loss grows c1; a load-independent loss, a group-wide change and a constant timing skew do not.
    material: the growth is at least MATERIAL of the group's own coupling, judged on its interval (rel +- Z se)."""
    L = track(ctx, lim, window, t0, t1)
    P = track(ctx, {"median": peers} if len(peers) > 1 else peers[0], window, t0, t1)
    X = track(ctx, driver, window, t0, t1)
    j = np.searchsorted(P.t, L.t, side="right") - 1
    jx = np.searchsorted(X.t, L.t, side="right") - 1
    ok = (j >= 0) & (jx >= 0)
    t, yl, yp, x = L.t[ok], L.v[ok], P.v[j[ok]], X.v[jx[ok]]
    sgn = -1.0 if limit_side == "low" else 1.0
    y = sgn * (yl - yp)
    xfl = floor(X)
    xf = float(xfl.get("sd_eff") or 0.0)
    mdt = 3 * float(xfl.get("update_interval") or xfl.get("dt_median") or ctx.profile.max_gap_s)
    settle = 2 * float(floor(L).get("update_interval") or (np.median(np.diff(L.t)) if len(L) > 1 else 0.0))
    rows = []
    for i in _driver_steps(t, x, xf, mdt):
        k = int(np.searchsorted(t, t[i + 1] + settle, side="left"))
        if k >= len(t) or t[k] - t[i + 1] > settle + mdt:
            continue
        if np.any(np.abs(np.diff(x[i + 1:k + 1])) > OUTLIER_Z * max(xf, 1e-12)):
            continue                                   # another step before the response was read
        rows.append((t[i + 1], x[i + 1] - x[i], y[k] - y[i], yp[k] - yp[i]))
    out: Dict[str, Any] = {"steps": len(rows), "rule": "at each driver step (|dx| > OUTLIER_Z x its noise floor), the member's change minus its "
                           "peers' change, read one settling interval later; regression dy = c0 dx + c1 s dx over the span (s = 0..1)"}
    if len(rows) < 8:
        out["note"] = f"not identifiable: {len(rows)} usable driver steps (8 are needed)"
        return out
    a = np.asarray(rows)
    ts, dx, dy, dp = a.T
    s01 = (ts - ts.min()) / max(ts.max() - ts.min(), 1e-9)
    A = np.column_stack([dx, dx * s01])
    beta, *_ = np.linalg.lstsq(A, dy, rcond=None)
    res = dy - A @ beta
    sd = 1.4826 * float(np.median(np.abs(res - np.median(res)))) or float(np.std(res)) or 1e-15
    cov = sd ** 2 * np.linalg.pinv(A.T @ A)
    se1 = math.sqrt(max(cov[1, 1], 1e-300))
    common = float(np.linalg.lstsq(dx[:, None], dp, rcond=None)[0][0])
    z = float(beta[1] / se1)
    rel = float(beta[1] / abs(common)) if abs(common) > 1e-15 else None
    rel_se = float(se1 / abs(common)) if rel is not None else None
    mat = materiality([(rel, rel_se)]) if rel is not None else {"verdict": None, "interval": None}
    out.update({"z": _r(z, 3), "growth": _r(float(beta[1]), 9), "growth_se": _r(se1, 9), "baseline_extra_coupling": _r(float(beta[0]), 9),
                "group_coupling": _r(common, 9), "relative_growth": _r(rel, 4), "relative_growth_se": _r(rel_se, 4), "materiality": mat,
                "t_span": [_r(float(ts.min()), 3), _r(float(ts.max()), 3)]})
    return out


def transients(ctx: C.Context, members: List[str], driver: Optional[str], window: str, t0: Optional[float], t1: Optional[float]) -> Dict[str, Any]:
    """Intermittent measurement faults: a reading that jumps and returns (a jump of > OUTLIER_Z x its own noise followed,
    within max(20 updates, 10 s), by an opposite jump of 0.5..2x its size), with no driver step at either edge (a step
    is shifted by the member's measured response lag). Counted per member and compared with its PEERS, so a fault present
    from the start (no clean baseline) still shows: a physical loss moves a component smoothly or with the load; a
    measurement fault moves one reading and back while its peers, under the same driver, do not."""
    steps = np.zeros(0)
    if driver:
        X = track(ctx, driver, window, t0, t1)
        xfl = floor(X)
        st = _driver_steps(X.t, X.v, float(xfl.get("sd_eff") or 0.0), 3 * float(xfl.get("update_interval") or xfl.get("dt_median") or ctx.profile.max_gap_s))
        steps = X.t[st + 1]
    per = []
    for m in members:
        s = ctx.series(m, window, t0, t1)
        t, v = np.asarray(s.t, float), np.asarray(s.v, float)
        if len(t) < 20:
            per.append({"member": m, "count": None})
            continue
        upd = float(np.median(np.diff(t)))
        d = np.diff(v)
        nz = d[d != 0]
        if len(nz) < 10:
            per.append({"member": m, "count": 0, "jumps": 0})
            continue
        sd = max(1.4826 * float(np.median(np.abs(nz - np.median(nz)))), float(np.min(np.abs(nz))))
        big = np.flatnonzero(np.abs(d) > OUTLIER_Z * sd)
        lag = 0.0
        if len(steps) and len(big):                    # the member's response lag to driver steps (timing skew)
            bt = t[big + 1]
            k = np.searchsorted(bt, steps - upd)
            ok = k < len(bt)
            dl = bt[k[ok]] - steps[ok]
            dl = dl[dl < 5.0]
            lag = float(np.percentile(dl, 90)) if len(dl) >= 3 else 0.0
            tb = t[big + 1]
            ss = steps + lag / 2
            kk = np.searchsorted(ss, tb)
            dd = np.minimum(np.abs(tb - ss[np.clip(kk - 1, 0, len(ss) - 1)]), np.abs(ss[np.clip(kk, 0, len(ss) - 1)] - tb))
            big = big[dd > max(upd, lag / 2 + upd)]
        max_ret = max(20 * upd, 10.0)
        n, used, first = 0, set(), []
        for a_ in big:
            if a_ in used:
                continue
            for b_ in big[big > a_]:
                if t[b_ + 1] - t[a_ + 1] > max_ret:
                    break
                if np.sign(d[b_]) != np.sign(d[a_]) and 0.5 <= abs(d[b_]) / abs(d[a_]) <= 2:
                    n += 1
                    used |= {a_, b_}
                    first.append((a_ + 1, b_ + 1))
                    break
        per.append({"member": m, "count": n, "jumps": int(len(big)), "noise_sd": _r(sd, 9), "lag_s": _r(lag, 3),
                    "refs": [r for i, j_ in first[:2] for r in (s.ref(i), s.ref(j_)) if hasattr(s, "ref")]})
    counts = [(p_["count"], p_["member"]) for p_ in per if p_["count"] is not None]
    if len(counts) < 2:
        return {"per_member": per, "excess": None, "member": None}
    counts.sort(reverse=True)
    return {"per_member": per, "member": counts[0][1], "excess": int(counts[0][0] - counts[1][0]),
            "rule": f"jump > {OUTLIER_Z} x the member's noise, returning within max(20 updates, 10 s) by 0.5..2x, no driver step at either edge; "
                    "excess = the most-affected member's count minus the next member's"}


def event_jump(ctx: C.Context, member: str, driver: Optional[str], window: str, t_event: float, limit_side: Optional[str]) -> Dict[str, Any]:
    """Did the member at the limit reach it through a JUMP (> OUTLIER_Z x its own noise, toward the limit side, no driver
    step at that moment, shifted by the member's response lag) in the last max(20 updates, 10 s) before the event, with no
    return before the event? A physical loss approaches a limit gradually or at a load step; a measurement fault can put a
    reading past it in one update (and the protective action then ends the record before the reading returns)."""
    s = ctx.series(member, window, None, t_event)
    t, v = np.asarray(s.t, float), np.asarray(s.v, float)
    if len(t) < 20:
        return {"member": member, "value": None, "note": "too few samples"}
    upd = float(np.median(np.diff(t)))
    d = np.diff(v)
    nz = d[d != 0]
    if len(nz) < 10:
        return {"member": member, "value": 0}
    sd = max(1.4826 * float(np.median(np.abs(nz - np.median(nz)))), float(np.min(np.abs(nz))))
    win = max(20 * upd, 10.0)
    sgn = -1.0 if limit_side == "low" else 1.0
    idx = np.flatnonzero((t[1:] >= t_event - win) & (sgn * d > OUTLIER_Z * sd))
    steps = np.zeros(0)
    if driver:
        X = track(ctx, driver, window, None, t_event)
        xfl = floor(X)
        st = _driver_steps(X.t, X.v, float(xfl.get("sd_eff") or 0.0), 3 * float(xfl.get("update_interval") or xfl.get("dt_median") or ctx.profile.max_gap_s))
        steps = X.t[st + 1]
    tol = upd + 2 * upd
    hits = []
    for i in idx:
        tj = t[i + 1]
        if len(steps) and np.min(np.abs(steps - tj)) <= tol:        # a driver step at that moment explains it
            continue
        after = d[i + 1:]
        if np.any(-sgn * after > 0.5 * sgn * d[i]):                                  # it returned before the event
            continue
        hits.append(i)
    return {"member": member, "value": 1 if hits else 0, "window_s": _r(win, 3), "noise_sd": _r(sd, 9),
            "jump": _r(float(d[hits[-1]]), 9) if hits else None, "t": _r(float(t[hits[-1] + 1]), 3) if hits else None,
            "refs": [s.ref(hits[-1]), s.ref(hits[-1] + 1)] if hits and hasattr(s, "ref") else []}


def divergence(ctx: C.Context, roles: List[str], window: str, t0: Optional[float] = None, t1: Optional[float] = None,
               driver_role: Optional[str] = None, index_roles: Optional[List[str]] = None, baseline: Optional[Dict[str, Any]] = None,
               event: Optional[Dict[str, Any]] = None, limit_side: Optional[str] = None, baseline_fraction: float = 1 / 3,
               response: Optional[str] = None) -> Dict[str, Any]:
    """A group of comparable signals: two extremes of one group (e.g. its lowest and highest member) or individual components.
    Separates what the group does TOGETHER from what one member does APART, and when:
      * spread (max - min) and its departure from its own baseline trend and load relationship (diff_departure_z), onset,
        earliest persistent detection, ordering and lead before an event;
      * the LIMITING member (the one nearest the limit side, else the one departing most) and the FAR member (for two
        extremes the other one; for components the group median): each one's departure from its baseline trend. A group-wide
        excursion moves the far member too (far_departure_abs_z); a member-specific one does not;
      * the limiting member's excursion since the baseline split into the part the far member shares (common) and the rest
        (differential): common_share + differential_share = 1;
      * load coupling of the spread at the driver's steps, baseline vs late (load_coupling_change_z; a resistance-like
        effect), identifiable only if the driver steps in both spans;
      * unexplained jumps of the spread against its baseline relationship (jump_excess_z: a sensing-like signature);
      * identity of the extreme component over the span (index roles), so a derivative is read as one component's;
      * timing-skew bound: spread the members' different sample ages can manufacture at the common rate;
      * the same departure under other baseline choices (baseline_sensitivity). Precedence is not causation."""
    _windows(ctx, window)
    roles = list(roles or [])
    if len(roles) < 2:
        raise DynError("divergence needs at least two comparable roles")
    if limit_side not in (None, "low", "high"):
        raise DynError("limit_side must be low, high or omitted")
    sp = track(ctx, {"spread": roles}, window, t0, t1)
    common_sig: Signal = {"mean": roles} if len(roles) == 2 else {"median": roles}
    cm = track(ctx, common_sig, window, t0, t1)
    ev = _event_time(ctx, window, event, None) if event else None
    span0 = float(sp.t[0]) if t0 is None else float(t0)
    span1 = (min(float(sp.t[-1]), ev["t"]) if ev else float(sp.t[-1])) if t1 is None else float(t1)
    ana = {"t0": span0, "t1": span1}
    preds: List[Signal] = ([driver_role] if driver_role else []) + ["time"]
    dep = departure(ctx, {"spread": roles}, window, preds, baseline, ana, event, baseline_fraction)
    dq = dep["quantities"]
    b0, b1 = dq["baseline"]
    # members: who is limiting, who is far
    meds = {r: float(np.median(np.asarray(ctx.series(r, window, b0, b1).v or [0.0]))) for r in roles}
    mdep = {}
    for r in roles:
        try:
            mdep[r] = departure(ctx, r, window, preds, {"t0": b0, "t1": b1}, ana)["quantities"]
        except DynError:
            continue
    if limit_side and len(roles) == 2:
        lim = min(roles, key=lambda r: meds[r]) if limit_side == "low" else max(roles, key=lambda r: meds[r])
    elif limit_side:
        lim = min(mdep, key=lambda r: mdep[r]["departure_last"] or 0) if limit_side == "low" else max(mdep, key=lambda r: mdep[r]["departure_last"] or 0)
    else:
        lim = max(mdep, key=lambda r: abs(mdep[r]["departure_z"] or 0)) if mdep else roles[0]
    if len(roles) == 2:
        far = [r for r in roles if r != lim][0]
        far_q = mdep.get(far)
    else:
        far = f"median({', '.join(roles)})"
        try:
            far_q = departure(ctx, {"median": roles}, window, preds, {"t0": b0, "t1": b1}, ana)["quantities"]
        except DynError:
            far_q = None
    # excursion decomposition (limiting member vs far member, baseline median -> last analysis block)
    def change(sig: Signal) -> Optional[float]:
        tr = track(ctx, sig, window, b0, span1)
        base_v = tr.v[(tr.t >= b0) & (tr.t <= b1)]
        L = dep["quantities"]["block_s"] or (span1 - b0) / 20
        late_v = tr.v[(tr.t >= span1 - L) & (tr.t <= span1)]
        return float(np.median(late_v) - np.median(base_v)) if len(base_v) and len(late_v) else None
    d_lim = change(lim)
    d_far = change(far if len(roles) == 2 else {"median": roles})
    common_share = None
    if d_lim is not None and d_far is not None and abs(d_lim) > 0:
        common_share = max(0.0, min(1.0, d_far / d_lim))
    # materiality (dynamics-v2): statistical significance is not physical relevance. A long record makes a small bend of
    # a healthy group's trend "significant". A departure is MATERIAL when it is more than half of the change it is part of:
    #   own_departure_z   the spread's departure, counted only when more than half of the limiting member's excursion is its
    #                     own (differential_share >= 1/2); otherwise the group's motion amplified, reported as 0
    #   far_departure_share  |far member's departure from its baseline trend| / |its change since the baseline|
    #   far_material_departure_z  far_departure_abs_z counted only when far_departure_share >= 1/2, else 0
    diff_share = None if common_share is None else 1 - common_share
    own_dep_z = None if dq.get("departure_z") is None or diff_share is None else (dq["departure_z"] if diff_share >= 0.5 else 0.0)
    far_dl = (far_q or {}).get("departure_last")
    far_dep_share = (min(1.0, abs(far_dl) / abs(d_far)) if far_dl is not None and d_far not in (None, 0.0) else None)
    far_mat_z = None if (far_q or {}).get("departure_abs_z") is None or far_dep_share is None else (
        far_q["departure_abs_z"] if far_dep_share >= 0.5 else 0.0)
    # rates
    rs = rate_track(sp, ctx.profile.max_gap_s)
    rc = rate_track(cm, ctx.profile.max_gap_s)

    def med(rt: Dict[str, Any], a: float, b: float) -> Optional[float]:
        m = (rt["t"] >= a) & (rt["t"] <= b)
        return float(np.median(rt["rate"][m])) if m.any() else None
    late0 = max(b1, span1 - (b1 - b0) / 2)
    sr_b, sr_l, cr_b, cr_l = med(rs, b0, b1), med(rs, late0, span1), med(rc, b0, b1), med(rc, late0, span1)
    inspan = (rs["t"] >= span0) & (rs["t"] <= span1)
    imx = int(np.nanargmax(np.where(inspan, rs["rate"], -np.inf)))
    # load coupling at the driver's steps
    load = None
    if driver_role:
        X, _, keep = _aligned(ctx, sp, [driver_role], window)
        tt, yy, xx = sp.t[keep], sp.v[keep], X[keep, 0]
        dfl = floor(track(ctx, driver_role, window, b0, span1))
        xfloor = float(dfl.get("sd_eff") or 0.0)
        mdt = 3 * float(dfl.get("update_interval") or dfl.get("dt_median") or ctx.profile.max_gap_s)
        yfl = floor(sp)
        settle = float(yfl.get("update_interval") or yfl.get("dt_median") or 0.0) + (float(np.percentile(sp.age_spread, 95)) if sp.age_spread is not None and len(sp) else 0.0)
        cb = _step_coupling(tt[(tt >= b0) & (tt <= b1)], yy[(tt >= b0) & (tt <= b1)], xx[(tt >= b0) & (tt <= b1)], xfloor, mdt, settle)
        cl = _step_coupling(tt[(tt >= late0) & (tt <= span1)], yy[(tt >= late0) & (tt <= span1)], xx[(tt >= late0) & (tt <= span1)], xfloor, mdt, settle)
        z = None
        if cb["median"] is not None and cl["median"] is not None:
            z = (cl["median"] - cb["median"]) / math.sqrt(cb["se"] ** 2 + cl["se"] ** 2)
        load = {"baseline_steps": cb["steps"], "late_steps": cl["steps"], "baseline_ratio": cb["median"], "late_ratio": cl["median"], "z": z,
                "step_rule": f"driver steps: |d{driver_role}| > {OUTLIER_Z} x its noise floor between consecutive samples at most {_r(mdt, 3)} s apart"}
    # identity of the extreme components
    ident = []
    for r in index_roles or []:
        s = ctx.series(r, window, span0, span1)
        vals = list(s.v)
        if vals:
            mode = max(set(vals), key=vals.count)
            ident.append({"role": r, "changes": sum(1 for a_, b_ in zip(vals, vals[1:]) if a_ != b_), "most_frequent": _r(mode, 0),
                          "fraction_most_frequent": _r(vals.count(mode) / len(vals), 4), "distinct": len(set(vals))})
    # timing skew
    skew = None
    if sp.age_spread is not None and len(rc["rate"]):
        msk = (sp.t >= span0) & (sp.t <= span1)
        age = float(np.percentile(sp.age_spread[msk], 95)) if msk.any() else 0.0
        cmr = float(np.percentile(np.abs(rc["rate"]), 95))
        skew = {"member_age_difference_p95_s": age, "common_rate_p95": cmr, "spread_bound": age * cmr}
    # baseline sensitivity
    sens = []
    for f in (0.25, 0.5):
        if abs(f - baseline_fraction) < 1e-9 or baseline:
            continue
        try:
            q2 = departure(ctx, {"spread": roles}, window, preds, None, ana, event, f)["quantities"]
            sens.append({"baseline_fraction": f, "baseline": q2["baseline"], "departure_z": q2["departure_z"], "first_significant_t": q2["first_significant_t"]})
        except DynError:
            continue
    zs = [dq.get("departure_z")] + [x["departure_z"] for x in sens]
    robust = None if any(z is None for z in zs) else (all(z >= Z for z in zs) or all(z <= -Z for z in zs) or all(abs(z) < Z for z in zs))
    # ---- dynamics-v3 --------------------------------------------------------------------------------------------------
    late_L = dq["block_s"] or (span1 - b0) / 20
    lim_tr = track(ctx, lim, window, b0, span1)
    far_tr = track(ctx, far if len(roles) == 2 else {"median": roles}, window, b0, span1)
    est = []
    for bb in [(b0, b1)] + [tuple(x["baseline"]) for x in sens]:
        dl_, sl_ = _median_change(lim_tr, bb[0], bb[1], span1 - late_L, span1)
        df_, sf_ = _median_change(far_tr, bb[0], bb[1], span1 - late_L, span1)
        if dl_ is None or df_ is None or abs(dl_) <= 1e-15:
            continue
        sh = 1 - max(0.0, min(1.0, df_ / dl_))
        est.append((sh, _ratio_se(df_, sf_, dl_, sl_)))
    own_mat = materiality(est)
    own_mat_z = None if dq.get("departure_z") is None or own_mat["verdict"] in (None, "undetermined") else (
        dq["departure_z"] if own_mat["verdict"] == "material" else 0.0)
    # the far member's change in its relation to the driver: a STATIC response (current follows the present command) has
    # no trend of its own, so its baseline relation excludes time and a group-wide ramp present from the start still shows;
    # an INTEGRATING response (a cell discharging) keeps time in the relation (its decline is the healthy reference)
    far_rel = None
    lim_rel = None
    if response == "static" and driver_role:
        # a static response (it follows the present driver) has no trend of its own: each member's relation to the driver is
        # fitted on the baseline WITHOUT a time term and its mean residual read over the whole late span
        def rel_change(sig: Signal) -> Optional[Dict[str, Any]]:
            tr_ = track(ctx, sig, window, b0, span1)
            Xf, _, kf = _aligned(ctx, tr_, [driver_role], window)
            tf, yf, xf_ = tr_.t[kf], tr_.v[kf], Xf[kf, 0]
            mb_, ml_ = (tf >= b0) & (tf <= b1), (tf >= late0) & (tf <= span1)
            if mb_.sum() <= 10 or ml_.sum() <= 10:
                return None
            fit = _ols(np.column_stack([np.ones(int(mb_.sum())), xf_[mb_]]), yf[mb_])
            rl = yf[ml_] - (fit["beta"][0] + fit["beta"][1] * xf_[ml_])
            rho_l = _rho(rl - rl.mean())
            v = np.array([1.0, float(xf_[ml_].mean())])
            se_l = math.sqrt(fit["s"] ** 2 / max(1.0, len(rl) * (1 - rho_l) / (1 + rho_l)) + float(v @ fit["cov"] @ v))
            return {"z": _r(abs(float(rl.mean())) / max(se_l, 1e-15), 3), "departure": float(rl.mean()), "se": se_l, "predictors": [driver_role],
                    "span": [_r(late0, 3), _r(span1, 3)],
                    "reference": "baseline relation to the driver, without a time trend, read over the late span (static response)"}
        try:
            far_rel = rel_change(far if len(roles) == 2 else {"median": roles})
            lim_rel = rel_change(lim)
        except DynError:
            far_rel = None
    if far_rel is None and far_q:
        far_rel = {"z": far_q.get("departure_abs_z"), "departure": far_q.get("departure_last"), "se": far_q.get("departure_last_se"),
                   "predictors": preds, "reference": "baseline trend and relation to the driver"}
    far_mat = {"verdict": None, "interval": None}
    far_mat_change_z = None
    if far_rel and far_rel.get("z") is not None:
        dF, sF = _median_change(far_tr, b0, b1, span1 - late_L, span1)
        if response == "static" and far_rel["predictors"] == [driver_role]:
            # static: the far member's relation change as a share of the change it belongs to, the limiting member's relation
            # change at the same driver. If the limiting member's relation did not change, the group did not move: immaterial.
            far_rel["materiality_reference"] = "the limiting member's relation change at the same driver (static response)"
            if not lim_rel or (lim_rel.get("z") or 0) < Z:
                dF, sF = None, None
                far_mat = {"verdict": "immaterial", "interval": None, "note": "the limiting member's relation to the driver did not change "
                           "measurably: the group did not move"}
                far_mat_change_z = 0.0
            else:
                dF, sF = abs(lim_rel["departure"]), lim_rel["se"]
                far_rel["limiting_relation"] = lim_rel
        if dF not in (None, 0.0) and far_rel.get("departure") is not None:
            shf = min(1.0, abs(far_rel["departure"]) / abs(dF))
            far_mat = materiality([(shf, _ratio_se(abs(far_rel["departure"]), far_rel.get("se") or 0.0, abs(dF), sF or 0.0))])
            far_mat_change_z = None if far_mat["verdict"] in (None, "undetermined") else (far_rel["z"] if far_mat["verdict"] == "material" else 0.0)
    cg = None
    if driver_role:
        # two extremes: the limiting one against the other; individual components: every member against its peers, the
        # strongest growth reported (with or without an event, the deteriorating member need not be the one nearest the limit)
        cands = [lim] if len(roles) == 2 else list(roles)
        tried = []
        for m in cands:
            try:
                c_ = coupling_growth(ctx, m, [r for r in roles if r != m], driver_role, window, b0, span1, limit_side)
            except DynError as ex:
                c_ = {"note": str(ex)}
            c_["member"] = m
            tried.append(c_)
        withz = [c_ for c_ in tried if c_.get("z") is not None]
        cg = max(withz, key=lambda c_: c_["z"]) if withz else tried[0]
        if len(tried) > 1:
            cg["members_tested"] = len(tried)
            cg["note_multiplicity"] = (f"the strongest of {len(tried)} members; each was tested against its peers, so the largest z is "
                                       "partly selected by chance (materiality is judged on its interval)")
    cg_mat_z = None
    if cg and cg.get("z") is not None and (cg.get("materiality") or {}).get("verdict") in ("material", "immaterial"):
        cg_mat_z = cg["z"] if cg["materiality"]["verdict"] == "material" else 0.0
    tri = transients(ctx, roles, driver_role, window, span0, span1)
    ej = None
    if ev:
        at_lim = lim
        if limit_side in ("low", "high") and len(roles) > 2:
            lv = {r: ctx.series(r, window, None, ev["t"]).v for r in roles}
            lv = {r: x[-1] for r, x in lv.items() if len(x)}
            if lv:
                at_lim = (min if limit_side == "low" else max)(lv, key=lv.get)
        try:
            ej = event_jump(ctx, at_lim, driver_role, window, ev["t"], limit_side)
        except DynError as ex:
            ej = {"member": at_lim, "value": None, "note": str(ex)}
    notes = [n for n in dep.get("notes") or [] if "extrapolation" not in n]
    if own_mat["verdict"] == "undetermined":
        notes.append("materiality undetermined: under the baseline choices and the noise, the limiting member's own share of its excursion "
                     f"lies on both sides of {MATERIAL} ({own_mat['interval']}); the departure is neither counted nor dismissed")
    growth = dq.get("departure_last") or 0.0
    skew_q = (1 if abs(growth) <= skew["spread_bound"] else 0) if skew else None
    if skew_q:
        notes.append("QUALIFIED: the spread's departure is within what the members' different sample ages can manufacture at the common rate "
                     "(timing skew): it is not evidence that a component diverged")
    if ident and any(i["changes"] > 0 for i in ident):
        notes.append("the extreme component changes identity in the span: the spread and its rate belong to different components at different "
                     "times; read a derivative as one component's only between identity changes")
    if robust is False:
        notes.append("the departure verdict changes with the choice of baseline span: report it as baseline-dependent")
    if load and load["z"] is None:
        notes.append(f"load coupling not identifiable: the driver stepped {load['baseline_steps']} time(s) in the baseline and {load['late_steps']} "
                     "in the late span (at least 5 in each are needed); a resistance-like effect can be neither shown nor excluded on this data")
    q = {"roles": roles, "unit": sp.unit, "span": [_r(span0, 3), _r(span1, 3)], "baseline": dq["baseline"],
         "limiting_member": lim, "far_member": far,
         "spread_baseline_median": _r(float(np.median(sp.v[(sp.t >= b0) & (sp.t <= b1)])), 9),
         "spread_late_median": _r(float(np.median(sp.v[(sp.t >= late0) & (sp.t <= span1)])), 9),
         "spread_rate_baseline": _r(sr_b, 9), "spread_rate_late": _r(sr_l, 9), "spread_rate_max": _r(rs["rate"][imx], 9),
         "spread_rate_max_t": _r(rs["t"][imx], 3), "spread_rate_max_se": _r(rs["se"][imx], 9),
         "common_rate_baseline": _r(cr_b, 9), "common_rate_late": _r(cr_l, 9),
         "diff_departure_z": dq.get("departure_z"), "diff_departure_last": dq.get("departure_last"),
         "onset_t": dq.get("onset_t"), "onset_interval": dq.get("onset_interval"), "first_significant_t": dq.get("first_significant_t"),
         "ordering": dq.get("ordering"), "lead_s": dq.get("lead_s"), "onset_lead_s": dq.get("onset_lead_s"), "precedes_event": dq.get("precedes_event"),
         "event": dq.get("event"),
         "limiting_departure_z": (mdep.get(lim) or {}).get("departure_z"), "far_departure_z": (far_q or {}).get("departure_z"),
         "far_departure_abs_z": (far_q or {}).get("departure_abs_z"),
         "own_departure_z": _r(own_dep_z, 3), "far_departure_share": _r(far_dep_share, 4), "far_material_departure_z": _r(far_mat_z, 3),
         "limiting_change": _r(d_lim, 9), "far_change": _r(d_far, 9), "common_share": _r(common_share, 4),
         "differential_share": _r(None if common_share is None else 1 - common_share, 4),
         "load_coupling_change_z": _r(load["z"], 3) if load and load["z"] is not None else None,
         "load_coupling": {k: _r(v2, 9) if not isinstance(v2, str) else v2 for k, v2 in load.items()} if load else None,
         "jump_excess_z": dq.get("jump_excess_z"), "jumps_baseline": dq.get("jumps_baseline"), "jumps_analysis": dq.get("jumps_analysis"),
         "identity": ident or None, "identity_stable": (1 if all(i["changes"] == 0 for i in ident) else 0) if ident else None,
         "timing_skew": {k: _r(v2, 9) for k, v2 in skew.items()} if skew else None, "skew_qualified": skew_q,
         "baseline_sensitivity": sens or None, "baseline_robust": None if robust is None else int(robust),
         "coefficients": dq.get("coefficients"), "predictor_variation": dq.get("predictor_variation"),
         # dynamics-v3
         "own_materiality": own_mat, "own_material_departure_z": _r(own_mat_z, 3),
         "far_relation": far_rel, "far_materiality": far_mat, "far_material_change_z": _r(far_mat_change_z, 3), "response": response,
         "coupling_growth": cg, "coupling_growth_z": (cg or {}).get("z"), "coupling_growth_relative": (cg or {}).get("relative_growth"),
         "coupling_growth_material_z": _r(cg_mat_z, 3),
         "transients": tri, "transient_excess": tri.get("excess"), "transient_member": tri.get("member"),
         "event_jump": ej, "event_on_jump": (ej or {}).get("value"),
         "sensing_signatures": (None if tri.get("excess") is None and (ej or {}).get("value") is None else
                                int((tri.get("excess") or 0) >= 2) + int((ej or {}).get("value") or 0))}
    kinds = {k: "observation" for k in q}
    for k in ("diff_departure_z", "diff_departure_last", "onset_t", "onset_interval", "first_significant_t", "ordering", "lead_s", "onset_lead_s",
              "precedes_event", "limiting_departure_z", "far_departure_z", "far_departure_abs_z", "load_coupling_change_z", "load_coupling",
              "jump_excess_z", "baseline_sensitivity", "baseline_robust", "common_share", "differential_share", "own_departure_z",
              "far_departure_share", "far_material_departure_z", "own_materiality", "own_material_departure_z", "far_relation",
              "far_materiality", "far_material_change_z", "coupling_growth", "coupling_growth_z", "coupling_growth_relative",
              "coupling_growth_material_z"):
        kinds[k] = "conditional"
    kinds["coefficients"] = "inferred_parameter"
    refs = dep["evidence"] + sp.refs(_nearest(sp, float(rs["t"][imx])))
    ass = list(dep["assumptions"]) + [
        "spread = max - min of the members at each aligned time; common = their mean (two extremes) or median (components)",
        "limiting member: " + (f"the one on the {limit_side} side" if limit_side else "the one departing most from its baseline trend"),
        "common_share = far member's change / limiting member's change since the baseline (clipped to 0..1)"]
    return _out("divergence", q, kinds, sp.inputs() + ([ctx.series(driver_role, window).input()] if driver_role else []), refs, ass, notes,
                items=dep.get("items"))


# ---- recorded protection reason ------------------------------------------------------------------------------------
def recorded_reason(ctx: C.Context, roles: List[str], window: str, event: Dict[str, Any], pattern: str,
                    before_s: float = 60.0, after_s: float = 10.0) -> Dict[str, Any]:
    """What the machine itself recorded as the reason, around a protective event: the labels a reason state (a fault or
    protection code with a decoded meaning) takes from before_s before the event to after_s after it, and whether any of
    them says that a member of the group reached its own limit (`pattern`, from the domain library: meaning, not a
    threshold). None when no bound reason role carries labels: then the recording cannot say."""
    _windows(ctx, window)
    ev = _event_time(ctx, window, event, None)
    if not ev:
        raise DynError(f"event {event} not found in {window}")
    rx = re.compile(pattern, re.I)
    seen, refs, used = [], [], []
    for r in roles:
        if not ctx.profile.has_role(r) or not ctx.rec.has(window, ctx.profile.role(r)["field"]):
            continue
        enum = ctx.profile.role(r).get("enum") or {}
        if not enum:
            continue
        s = ctx.series(r, window, ev["t"] - before_s, ev["t"] + after_s)
        used.append(r)
        for i, v in enumerate(s.v):
            k = str(int(v)) if float(v).is_integer() else str(v)
            lab = enum.get(k, k)
            if lab not in [x["label"] for x in seen]:
                seen.append({"role": r, "label": lab, "t": _r(float(s.t[i]), 3), "member_limit": bool(rx.search(lab))})
                refs.append(s.ref(i))
    hit = [x for x in seen if x["member_limit"]]
    q = {"event": {k: _r(v, 3) if not isinstance(v, (dict, str)) else v for k, v in ev.items() if k != "ref"}, "reason_roles": used,
         "labels": seen, "member_limit_recorded": (1 if hit else 0) if used else None, "pattern": pattern,
         "span": [_r(ev["t"] - before_s, 3), _r(ev["t"] + after_s, 3)]}
    kinds = {k: "observation" for k in q}
    kinds["member_limit_recorded"] = "reported_value"        # the machine's own record, read as it is; not verified here
    notes = [] if used else ["no reason role with decoded labels is bound for this group: the recording cannot say why the protection acted"]
    return _out("recorded_reason", q, kinds, [ctx.series(r, window).input() for r in used], refs + [ev["ref"]],
                ["a label is the decoded meaning the profile gives the raw code (as documented by the machine's maker)",
                 f"read from {before_s} s before to {after_s} s after the event"], notes)


# ---- quality ---------------------------------------------------------------------------------------------------------
def quality(ctx: C.Context, signal: Signal, window: str, t0: Optional[float] = None, t1: Optional[float] = None) -> Dict[str, Any]:
    """Sampling and measurement floor: median interval and jitter, update interval of held values, quantisation step (stated or
    inferred), noise, gaps, time reversals or repeats (overlapping sources), source-segment changes, and the resulting segments."""
    _windows(ctx, window)
    tr = track(ctx, signal, window, t0, t1)
    fl = floor(tr)
    segs = segments(tr, ctx.profile.max_gap_s)
    dt = np.diff(tr.t)
    gaps = dt[dt > ctx.profile.max_gap_s]
    q = {**fl, "unit": tr.unit, "gaps": int(len(gaps)), "gap_total_s": _r(float(gaps.sum()), 3), "largest_gap_s": _r(float(gaps.max()), 3) if len(gaps) else 0.0,
         "source_segment_changes": int(np.sum(tr.seg[1:] != tr.seg[:-1])) if len(tr) > 1 else 0, "segments": len(segs),
         "segment_spans": [[_r(tr.t[a], 3), _r(tr.t[b - 1], 3)] for a, b in segs][:TRACK_ITEMS],
         "alignment_age_p95_s": _r(float(np.percentile(tr.age_spread, 95)), 4) if tr.age_spread is not None and len(tr) else None}
    kinds = {k: "observation" for k in q}
    for k in ("resolution", "noise_sd", "sd_eff", "update_interval"):
        kinds[k] = "inferred_parameter"
    refs = tr.refs(0) + tr.refs(len(tr) - 1)
    if len(gaps):
        k = int(np.argmax(dt))
        refs += tr.refs(k) + tr.refs(k + 1)
    notes = _estimate_note(tr)
    if fl.get("update_interval") and fl.get("dt_median") and fl["update_interval"] > 1.5 * fl["dt_median"]:
        notes.append("values are held between updates (update interval longer than the sampling interval): consecutive samples are not "
                     "independent, and a rate over fewer than a few update intervals is a step artefact")
    if q["non_increasing_steps"]:
        notes.append("timestamps repeat or go backwards: overlapping sources or segments; a derivative across them is meaningless")
    return _out("quality", q, kinds, tr.inputs(), refs, ["noise from the median absolute second difference (robust to trends and steps)"], notes)


OPS = {"quality": quality, "rate": rate, "changepoint": changepoint, "departure": departure, "divergence": divergence,
       "recorded_reason": recorded_reason}

_SIG = "signal = a role, or {diff:[a,b]} | {spread:[roles]} | {mean:[roles]} | {median:[roles]}"
OP_DOC = {
    "quality": f"quality{{signal, window, t0?, t1?}}: sampling interval and jitter, update interval of held values, quantisation step, noise, gaps, overlaps, segments ({_SIG}).",
    "rate": f"rate{{signal, window, t0?, t1?, span_s?, causal=true, segment_by?:[state roles]}}: rate of change per second on actual timestamps; adaptive window unless span_s; min/max rate with time and standard error, early vs late rate, resolution floor ({_SIG}).",
    "changepoint": f"changepoint{{signal, window, t0?, t1?, max_breaks=2}}: where the rate of change changes (acceleration onset), each change point with a 95 % interval and the rate before/after; kept only if the information criterion improves ({_SIG}).",
    "departure": f"departure{{signal, window, predictors?:[signals], baseline?:{{t0,t1}}, analysis?:{{t0,t1}}, event?:{{role, kind: first_change|reaches_max|becomes|crosses, value?, op?}}}}: fits signal ~ predictors on a baseline span, then when and how strongly the signal departs from that relationship: departure_z, onset with interval, first_significant_t (earliest persistent detection), ordering vs the event (precedes/coincides/follows) and lead_s ({_SIG}).",
    "recorded_reason": "recorded_reason{roles:[reason/fault-code state roles with decoded labels], window, event, pattern (regex on labels meaning a member reached its own limit), before_s=60, after_s=10}: the labels the machine recorded around a protective event and whether one says the group's own limit protection acted (member_limit_recorded: 1/0, None if no labelled reason role).",
    "divergence": "divergence{roles:[comparable roles: two extremes of a group (lowest, highest) or individual components], window, t0?, t1?, driver_role?, index_roles?, baseline?, event?, limit_side?: low|high}: what the group does together vs what one member does apart: spread departure from its baseline trend and load relation (diff_departure_z, onset_t, first_significant_t, lead_s, ordering vs the event), limiting vs far member departures (far_departure_abs_z: a group-wide excursion), common_share/differential_share of the limiting member's excursion, load_coupling_change_z at load steps, jump_excess_z, identity of the extreme component, timing-skew bound, baseline sensitivity; response?: static|integrating. v3: own_material_departure_z and far_material_change_z (materiality judged on an interval; None = undetermined), coupling_growth (one member's response per unit driver growing relative to its peers; coupling_growth_material_z), transients (readings that jump and return without a driver step, per member vs peers; transient_excess).",
}
