"""Shared helpers for the cell-physics checks (pack/cell consistency, weak-cell attribution, relaxation, OCV vs counted
charge). The relationships these checks test, and the rule that each check abstains on its own and names what would
resolve it, come from an external EE advisor's first-principles proposal (2026-11-16): an expert proposal, implemented by
Veyra and so far tested on SYNTHETIC injections only, not validated on field data.
"""
from __future__ import annotations

import bisect
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.physchecks.profile import Profile, RoleSeries
from app.physchecks.recording import Recording

PROVENANCE = {"kind": "expert_proposal", "source": "external EE advisor, first-principles reasoning (2026-11-16); not field experience",
              "status": "implemented by Veyra; tested on SYNTHETIC fault injections only, not validated on field data"}


# temperature roles, most specific first; a profile may record cell, pack or min/max temperatures under any of these names
TEMPERATURE_ROLES = ("cell_temperature", "max_cell_temperature", "min_cell_temperature", "max_temp", "min_temp", "pack_temperature", "temperature")


def temperature_roles(profile: Profile) -> List[str]:
    return [r for r in TEMPERATURE_ROLES if profile.has_role(r)]


def param(params: Dict[str, Any], profile: Profile, name: str, default: Any = None) -> Tuple[Any, str]:
    """(value, source): explicit parameter, else profile parameter, else the stated default (labelled as such)."""
    if name in params and params[name] is not None:
        return params[name], "explicit parameter"
    v, src = profile.parameter(name)
    if v is not None:
        return v, f"profile parameter ({src})"
    return default, "default (an assumption, not a specification)" if default is not None else "not stated"


class Aligned:
    """Samples of several roles paired on the timestamps of a base role: each other role's latest sample at or before the
    base time, no older than its max_age_s (never interpolated)."""

    def __init__(self, rec: Recording, profile: Profile, window: str, base: str, others: Sequence[str], units: Dict[str, str]):
        self.series: Dict[str, RoleSeries] = {r: profile.series(rec, window, r, units.get(r)) for r in [base, *others]}
        self.rows: List[Dict[str, Any]] = []
        idx = {r: [s.t for s in self.series[r].samples] for r in others}
        for s in self.series[base].samples:
            row: Dict[str, Any] = {"t": s.t, base: s.value, "_ref": {base: s.index}}
            ok = True
            for r in others:
                ts = idx[r]
                i = bisect.bisect_right(ts, s.t + 1e-9) - 1
                if i < 0 or s.t - ts[i] > self.series[r].max_age_s:
                    ok = False
                    break
                row[r] = self.series[r].samples[i].value
                row["_ref"][r] = self.series[r].samples[i].index
            if ok:
                self.rows.append(row)

    def excluded(self) -> Dict[str, Dict[str, int]]:
        return {r: s.excluded for r, s in self.series.items() if s.excluded}


def _solve(A: List[List[float]], b: List[float]) -> Optional[List[float]]:
    """Gauss-Jordan with partial pivoting (small systems only)."""
    n = len(A)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[p][c]) < 1e-12:
            return None
        M[c], M[p] = M[p], M[c]
        for r in range(n):
            if r != c:
                f = M[r][c] / M[c][c]
                M[r] = [x - f * y for x, y in zip(M[r], M[c])]
    return [M[i][n] / M[i][i] for i in range(n)]


def _inverse(A: List[List[float]]) -> Optional[List[List[float]]]:
    n = len(A)
    cols = []
    for j in range(n):
        e = [1.0 if i == j else 0.0 for i in range(n)]
        x = _solve(A, e)
        if x is None:
            return None
        cols.append(x)
    return [[cols[j][i] for j in range(n)] for i in range(n)]


def _corr(a: Sequence[float], b: Sequence[float]) -> float:
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return 1.0
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb)


def ols(y: Sequence[float], xs: Dict[str, Sequence[float]]) -> Optional[Dict[str, Any]]:
    """Least squares y = b0 + sum b_k x_k, with standard errors and the pairwise correlations between regressors.
    Pure Python (no numpy dependency); regressors are centred for numerical stability."""
    n = len(y)
    names = list(xs)
    if n < len(names) + 5:
        return None
    means = {k: sum(xs[k]) / n for k in names}
    my = sum(y) / n
    Xc = [[xs[k][i] - means[k] for k in names] for i in range(n)]
    yc = [v - my for v in y]
    p = len(names)
    XtX = [[sum(Xc[i][a] * Xc[i][b] for i in range(n)) for b in range(p)] for a in range(p)]
    Xty = [sum(Xc[i][a] * yc[i] for i in range(n)) for a in range(p)]
    beta = _solve(XtX, Xty) if p else []
    if beta is None:
        return None
    resid = [yc[i] - sum(beta[a] * Xc[i][a] for a in range(p)) for i in range(n)]
    dof = max(1, n - p - 1)
    s2 = sum(r * r for r in resid) / dof
    inv = _inverse(XtX) if p else []
    se = [math.sqrt(max(0.0, s2 * inv[a][a])) for a in range(p)] if inv is not None else [math.inf] * p
    corr = {f"{a}~{b}": _corr(xs[a], xs[b]) for i, a in enumerate(names) for b in names[i + 1:]}
    return {"intercept": my - sum(beta[a] * means[k] for a, k in enumerate(names)), "coef": {k: beta[a] for a, k in enumerate(names)},
            "se": {k: se[a] for a, k in enumerate(names)}, "resid_sd": math.sqrt(s2), "n": n,
            "range": {k: float(max(xs[k]) - min(xs[k])) for k in names}, "corr": corr}


def runs(flags: Sequence[bool]) -> List[Tuple[int, int]]:
    """[start, end) index ranges where flags are True."""
    out, start = [], None
    for i, f in enumerate(flags):
        if f and start is None:
            start = i
        elif not f and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(flags)))
    return out


def ocv_inverse(curve: Dict[str, Any], v: float) -> Tuple[Optional[float], Optional[float]]:
    """(SOC %, local slope V per %) on a monotonic OCV curve; (None, None) outside the curve."""
    xs, ys = list(curve["soc_pct"]), list(curve["v"])
    if not (ys[0] <= v <= ys[-1]):
        return None, None
    i = max(1, bisect.bisect_left(ys, v))
    x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
    slope = (y1 - y0) / (x1 - x0) if x1 != x0 else 0.0
    return (x0 + (v - y0) / slope if slope > 0 else (x0 + x1) / 2), slope


def valid_curve(c: Any) -> Optional[str]:
    if not isinstance(c, dict) or not c.get("soc_pct") or not c.get("v"):
        return "no OCV curve (soc_pct, v) is given"
    xs, ys = c["soc_pct"], c["v"]
    if len(xs) != len(ys) or len(xs) < 3:
        return "the OCV curve needs matching soc_pct and v lists of at least 3 points"
    if any(b <= a for a, b in zip(xs, xs[1:])) or any(b < a for a, b in zip(ys, ys[1:])):
        return "the OCV curve must be monotonic in SOC and voltage"
    return None
