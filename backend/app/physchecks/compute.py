"""Read-only computations over a decoded recording, for the investigator's Predict -> Test -> Update loop.

Each operation reads signal ROLES through the recording's signal profile (never raw column names), uses only valid
samples (the profile's ranges, placeholders and maps apply), never interpolates or carries a value across a gap longer
than the profile allows, and returns:

  * the computed quantities, each labelled by kind:
      observation         read or computed directly from recorded rows
      reported_value      a value the machine itself reports (e.g. a capacity parameter on the bus): not verified
      inferred_parameter  a parameter of the machine inferred from the data, with its uncertainty
      conditional         holds only under the assumptions listed with it
      verified_fact       a parameter the profile marks as a customer specification or a verified result
  * `assumptions`: every assumption the result depends on (declared by the caller or by the operation);
  * `inputs`: the roles, fields and files read, with sample counts and exclusions;
  * `evidence`: file and 1-based line of the rows behind the key numbers.

Nothing here writes to the recording, the profile or any store. No machine-specific constants: thresholds are arguments
with stated defaults, and every default used is listed under `assumptions`.
"""
from __future__ import annotations

import bisect
import math
import statistics
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.physchecks.profile import Profile, RoleSeries
from app.physchecks.recording import Recording

COMPUTE_VERSION = "inquiry-compute-v1"
MAX_EVIDENCE = 12
MAX_ITEMS = 40
VERIFIED_SOURCES = ("customer_spec", "customer specification", "verified")


class ComputeError(ValueError):
    pass


def _r(x: Optional[float], nd: int = 4) -> Optional[float]:
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    return round(float(x), nd)


class Series:
    """Valid samples of one role in one window and time range, in a canonical unit; currents discharge-positive."""

    def __init__(self, ctx: "Context", role: str, window: str, t0: Optional[float] = None, t1: Optional[float] = None):
        p = ctx.profile
        if not p.has_role(role):
            raise ComputeError(f"unknown role {role!r}; roles: {', '.join(sorted(p.roles))}")
        if window not in ctx.rec.windows:
            raise ComputeError(f"unknown window {window!r}; windows: {', '.join(ctx.rec.windows)}")
        spec = p.role(role)
        unit = {"current": "A", "voltage": "V", "fraction": "%", "charge": "Ah"}.get(_dimension(spec.get("unit", "")))
        rs: RoleSeries = p.series(ctx.rec, window, role, unit)
        sign = -1.0 if spec.get("sign") == "charge_positive" else 1.0
        lo = -math.inf if t0 is None else float(t0)
        hi = math.inf if t1 is None else float(t1)
        pts = [s for s in rs.samples if lo <= s.t <= hi]
        self.ctx, self.role, self.window, self.rs = ctx, role, window, rs
        self.t = [s.t for s in pts]
        self.v = [s.value * sign for s in pts]
        self.idx = [s.index for s in pts]
        self.unit = rs.unit
        self.sign_note = "discharge positive" if spec.get("sign") else None
        if spec.get("sign") == "charge_positive":
            self.sign_note = "discharge positive (the recorded sign was charge positive and is inverted here)"

    def __len__(self) -> int:
        return len(self.t)

    def ref(self, i: int) -> Dict[str, Any]:
        r = self.ctx.rec.ref(self.window, self.rs.field, self.idx[i]).as_dict()
        r["role"] = self.role
        return r

    def at(self, t: float, max_age: Optional[float] = None) -> Optional[int]:
        """Index of the latest sample at or before t, no older than max_age (default: the role's max_age_s)."""
        i = bisect.bisect_right(self.t, t) - 1
        if i < 0:
            return None
        if t - self.t[i] > (self.rs.max_age_s if max_age is None else max_age):
            return None
        return i

    def input(self) -> Dict[str, Any]:
        files = sorted({self.ctx.rec.ref(self.window, self.rs.field, i).file for i in self.idx[:1] + self.idx[-1:]} - {""})
        return {"role": self.role, "window": self.window, "field": self.rs.field, "unit": self.unit, "files": files,
                "samples": len(self), "excluded": self.rs.excluded, "sign": self.sign_note,
                "provenance": self.ctx.profile.provenance(self.role)}


def _dimension(unit: str) -> str:
    from app.physchecks.profile import _UNIT
    return (_UNIT.get(unit) or ("none", 1.0))[0]


class Context:
    def __init__(self, rec: Recording, profile: Profile):
        self.rec, self.profile = rec, profile

    def series(self, role: str, window: str, t0: Optional[float] = None, t1: Optional[float] = None) -> Series:
        return Series(self, role, window, t0, t1)


# ---- filters ---------------------------------------------------------------------------------------------------------
_OPS = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
        "==": lambda a, b: abs(a - b) < 1e-9, "!=": lambda a, b: abs(a - b) >= 1e-9}


def _where(ctx: Context, s: Series, where: Optional[Sequence[Dict[str, Any]]]) -> Tuple[List[int], List[str]]:
    """Indices of s whose time satisfies every condition (another role's latest fresh sample compared with a value)."""
    if not where:
        return list(range(len(s))), []
    conds = []
    for w in where:
        if w.get("op") not in _OPS:
            raise ComputeError(f"where.op must be one of {sorted(_OPS)}")
        conds.append((ctx.series(w["role"], s.window), _OPS[w["op"]], float(w["value"]), w))
    keep = []
    for i, t in enumerate(s.t):
        ok = True
        for cs, fn, val, _ in conds:
            j = cs.at(t)
            if j is None or not fn(cs.v[j], val):
                ok = False
                break
        if ok:
            keep.append(i)
    return keep, [f"only samples where {w['role']} {w['op']} {w['value']} (latest fresh sample)" for *_, w in conds]


def _describe(s: Series, keep: List[int]) -> Dict[str, Any]:
    if not keep:
        return {"n": 0}
    vals = sorted(s.v[i] for i in keep)
    imin = min(keep, key=lambda i: s.v[i])
    imax = max(keep, key=lambda i: s.v[i])

    def q(p: float) -> float:
        return vals[min(len(vals) - 1, max(0, int(round(p * (len(vals) - 1)))))]
    return {"n": len(keep), "unit": s.unit, "t_first": _r(s.t[keep[0]], 3), "t_last": _r(s.t[keep[-1]], 3),
            "first": _r(s.v[keep[0]]), "last": _r(s.v[keep[-1]]), "min": _r(vals[0]), "max": _r(vals[-1]),
            "mean": _r(statistics.fmean(vals)), "median": _r(statistics.median(vals)), "p5": _r(q(0.05)), "p95": _r(q(0.95)),
            "_refs": [s.ref(keep[0]), s.ref(keep[-1]), s.ref(imin), s.ref(imax)]}


def _out(op: str, quantities: Dict[str, Any], kinds: Dict[str, str], inputs: List[Dict[str, Any]], evidence: List[Dict[str, Any]],
         assumptions: List[str], notes: Optional[List[str]] = None, **extra: Any) -> Dict[str, Any]:
    seen, ev = set(), []
    for e in evidence:
        k = (e.get("file"), e.get("row"), e.get("field"))
        if k not in seen:
            seen.add(k)
            ev.append(e)
    return {"op": op, "version": COMPUTE_VERSION, "quantities": quantities, "kinds": kinds, "assumptions": assumptions,
            "inputs": inputs, "evidence": ev[:MAX_EVIDENCE], "notes": notes or [], **extra}


# ---- operations ------------------------------------------------------------------------------------------------------
def signals(ctx: Context) -> Dict[str, Any]:
    """What can be computed: windows and spans, roles (with provenance and sample counts per window), parameters."""
    p, rec = ctx.profile, ctx.rec
    windows = []
    for w in rec.windows:
        sp = rec.span(w)
        windows.append({"window": w, "t0": _r(sp[0], 3) if sp else None, "t1": _r(sp[1], 3) if sp else None,
                        "roles": {r: len(p.series(rec, w, r).samples) for r in sorted(p.roles) if rec.has(w, p.role(r)["field"])}})
    roles = {r: {"field": s["field"], "unit": s.get("unit", ""), "kind": s.get("kind", "measurement"), "sign": s.get("sign"),
                 "enum": s.get("enum"), "derived_from": p.derived_from(r), "estimator": s.get("estimator")} for r, s in sorted(p.roles.items())}
    params = {k: {"value": v.get("value"), "source": v.get("source", "profile"),
                  "kind": "verified_fact" if any(x in str(v.get("source", "")).lower() for x in VERIFIED_SOURCES) else "reported_value"}
              for k, v in p.parameters.items()}
    return {"version": COMPUTE_VERSION, "profile": p.id, "machine_class": p.data.get("machine_class"), "conditions": p.conditions,
            "max_gap_s": p.max_gap_s, "windows": windows, "roles": roles, "parameters": params,
            "ops": sorted(OPS), "note": "Times are the profile's per-window seconds. Currents are reported discharge positive."}


def stats(ctx: Context, role: str, window: str, t0: Optional[float] = None, t1: Optional[float] = None,
          where: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    s = ctx.series(role, window, t0, t1)
    keep, conds = _where(ctx, s, where)
    d = _describe(s, keep)
    refs = d.pop("_refs", [])
    return _out("stats", d, {k: "observation" for k in d}, [s.input()], refs, conds)


def compare_windows(ctx: Context, role: str, a: Dict[str, Any], b: Dict[str, Any], where: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    out, inputs, refs, conds = {}, [], [], []
    for name, w in (("a", a), ("b", b)):
        s = ctx.series(role, w["window"], w.get("t0"), w.get("t1"))
        keep, c = _where(ctx, s, where)
        d = _describe(s, keep)
        refs += d.pop("_refs", [])
        out[name] = {"window": w["window"], "t0": w.get("t0"), "t1": w.get("t1"), **d}
        inputs.append(s.input())
        conds = c
    diff = {}
    for k in ("median", "mean", "min", "max", "p5", "p95"):
        if out["a"].get(k) is not None and out["b"].get(k) is not None:
            diff[f"{k}_b_minus_a"] = _r(out["b"][k] - out["a"][k])
    q = {**out, **diff}
    return _out("compare_windows", q, {k: "observation" for k in q}, inputs, refs, conds)


def _integrate(s: Series, keep: List[int], max_gap: float) -> Dict[str, Any]:
    total = dis = chg = covered = gaps = 0.0
    biggest: Tuple[float, int] = (0.0, -1)
    for a, b in zip(keep, keep[1:]):
        dt = s.t[b] - s.t[a]
        if dt <= 0:
            continue
        if dt > max_gap:
            gaps += dt
            if dt > biggest[0]:
                biggest = (dt, a)
            continue
        area = 0.5 * (s.v[a] + s.v[b]) * dt
        total += area
        dis += max(area, 0.0)
        chg += min(area, 0.0)
        covered += dt
    return {"net": total, "pos": dis, "neg": chg, "covered_s": covered, "gap_s": gaps, "biggest_gap": biggest}


def integrate(ctx: Context, role: str, window: str, t0: Optional[float] = None, t1: Optional[float] = None,
              max_gap_s: Optional[float] = None, where: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Trapezoid integral of a role over time. For a current: charge in Ah, discharge positive. Gaps longer than
    max_gap_s (default: the profile's max_gap_s) are not bridged; their total duration is reported with the bound they
    put on the result at the observed current range."""
    s = ctx.series(role, window, t0, t1)
    keep, conds = _where(ctx, s, where)
    if len(keep) < 2:
        raise ComputeError(f"fewer than 2 valid samples of {role} in {window} {t0}..{t1}")
    gap = ctx.profile.max_gap_s if max_gap_s is None else float(max_gap_s)
    g = _integrate(s, keep, gap)
    is_current = s.unit == "A"
    scale = 1 / 3600.0 if is_current else 1.0
    unit = "Ah" if is_current else f"{s.unit}*s"
    vals = [s.v[i] for i in keep]
    span = s.t[keep[-1]] - s.t[keep[0]]
    q = {"net": _r(g["net"] * scale), "positive_part": _r(g["pos"] * scale), "negative_part": _r(g["neg"] * scale), "unit": unit,
         "t_first": _r(s.t[keep[0]], 3), "t_last": _r(s.t[keep[-1]], 3), "span_s": _r(span, 3), "covered_s": _r(g["covered_s"], 3),
         "gap_s": _r(g["gap_s"], 3), "coverage": _r(g["covered_s"] / span if span > 0 else None, 4),
         "mean_while_covered": _r(g["net"] / g["covered_s"] if g["covered_s"] > 0 else None),
         "gap_bound": [_r(min(vals) * g["gap_s"] * scale), _r(max(vals) * g["gap_s"] * scale)]}
    refs = [s.ref(keep[0]), s.ref(keep[-1])]
    if g["biggest_gap"][1] >= 0:
        refs.append(s.ref(g["biggest_gap"][1]))
    kinds = {k: "observation" for k in q}
    kinds["gap_bound"] = "conditional"
    ass = [f"intervals longer than {gap} s are not integrated (no value is carried across a gap)"] + conds
    if is_current:
        ass.append("the recorded current is the whole current through the pack (a load upstream of the sensor is not counted)")
    return _out("integrate", q, kinds, [s.input()], refs, ass,
                ["gap_bound: what the gaps could add at the lowest and highest value observed in the range"] if g["gap_s"] else [])


def _steps(x: Series, min_dx: float, max_dt: float) -> List[Tuple[int, int]]:
    out = []
    for a in range(1, len(x)):
        dt = x.t[a] - x.t[a - 1]
        if 0 < dt <= max_dt and abs(x.v[a] - x.v[a - 1]) >= min_dx:
            out.append((a - 1, a))
    return out


def step_response(ctx: Context, x_role: str, y_role: str, window: str, t0: Optional[float] = None, t1: Optional[float] = None,
                  min_dx: float = 5.0, settle_s: float = 0.0, max_dt_s: Optional[float] = None) -> Dict[str, Any]:
    """Every step of x (|dx| >= min_dx between consecutive samples) against the change of y over the same interval:
    ratio = -dy/dx (for pack voltage against discharge current: the apparent source resistance, ohm). y is taken as its
    latest fresh sample at or before the step's start, and at or before the step's end + settle_s."""
    x, y = ctx.series(x_role, window, t0, t1), ctx.series(y_role, window, t0, t1 if t1 is None else t1 + settle_s)
    max_dt = ctx.profile.max_gap_s if max_dt_s is None else float(max_dt_s)
    items, refs = [], []
    for a, b in _steps(x, float(min_dx), max_dt):
        ya, yb = y.at(x.t[a]), y.at(x.t[b] + settle_s, max_age=y.rs.max_age_s + settle_s)
        if ya is None or yb is None or y.t[yb] <= x.t[a]:      # y not re-sampled after the step started: no response seen
            continue
        dx, dy = x.v[b] - x.v[a], y.v[yb] - y.v[ya]
        items.append({"t": _r(x.t[b], 3), "dx": _r(dx), "dy": _r(dy), "ratio": _r(-dy / dx, 6),
                      "rows": {x_role: [x.ref(a)["row"], x.ref(b)["row"]], y_role: [y.ref(ya)["row"], y.ref(yb)["row"]]}})
        if len(refs) < MAX_EVIDENCE:
            refs += [x.ref(b), y.ref(yb)]
    ratios = [i["ratio"] for i in items if i["ratio"] is not None]
    med = statistics.median(ratios) if ratios else None
    mad = statistics.median(abs(r - med) for r in ratios) if ratios else None
    q = {"steps": len(items), "median_ratio": _r(med, 6), "mad_ratio": _r(mad, 6), "min_ratio": _r(min(ratios), 6) if ratios else None,
         "max_ratio": _r(max(ratios), 6) if ratios else None,
         "positive_fraction": _r(sum(r > 0 for r in ratios) / len(ratios), 4) if ratios else None,
         "ratio_unit": f"{y.unit}/{x.unit}"}
    kinds = {k: "observation" for k in q}
    return _out("step_response", q, kinds, [x.input(), y.input()], refs,
                [f"a step is |d{x_role}| >= {min_dx} {x.unit} between consecutive samples at most {max_dt} s apart",
                 f"{y_role} is read at the step start and {settle_s} s after the step end (latest fresh sample)"],
                items=items[:MAX_ITEMS], items_total=len(items))


def unexplained_changes(ctx: Context, y_role: str, x_role: str, window: str, t0: Optional[float] = None, t1: Optional[float] = None,
                        min_dy: float = 1.0, max_dx: float = 1.0, max_dt_s: Optional[float] = None) -> Dict[str, Any]:
    """Steps of y (|dy| >= min_dy between consecutive samples) during which x changed by less than max_dx: a change in y
    that the measured x does not account for (e.g. pack voltage moving with no change in measured current)."""
    x, y = ctx.series(x_role, window, t0, t1), ctx.series(y_role, window, t0, t1)
    max_dt = ctx.profile.max_gap_s if max_dt_s is None else float(max_dt_s)
    items, refs, explained, checked = [], [], 0, 0
    for a, b in _steps(y, float(min_dy), max_dt):
        xa, xb = x.at(y.t[a]), x.at(y.t[b])
        if xa is None or xb is None:
            continue
        checked += 1
        dx = x.v[xb] - x.v[xa]
        if abs(dx) < max_dx:
            items.append({"t": _r(y.t[b], 3), "dy": _r(y.v[b] - y.v[a]), "dx": _r(dx),
                          "rows": {y_role: [y.ref(a)["row"], y.ref(b)["row"]], x_role: [x.ref(xa)["row"], x.ref(xb)["row"]]}})
            if len(refs) < MAX_EVIDENCE:
                refs += [y.ref(b), x.ref(xb)]
        else:
            explained += 1
    q = {"y_steps_checked": checked, "unexplained": len(items), "explained_by_x": explained}
    return _out("unexplained_changes", q, {k: "observation" for k in q}, [y.input(), x.input()], refs,
                [f"a y step is |d{y_role}| >= {min_dy} between consecutive samples at most {max_dt} s apart; unexplained when |d{x_role}| < {max_dx}"],
                items=items[:MAX_ITEMS])


def transitions(ctx: Context, state_role: str, window: str, roles: List[str], t0: Optional[float] = None, t1: Optional[float] = None,
                pre_s: float = 0.0, post_s: float = 1.0, where: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """At every change of a state role: each listed role's value before the change (its latest fresh sample at the
    previous state sample, the last one known to carry the old state, minus pre_s) and post_s after the new state was
    reported. The change happened somewhere between those two state samples."""
    st = ctx.series(state_role, window, t0, t1)
    keep, conds = _where(ctx, st, where)
    others = {r: ctx.series(r, window) for r in roles}
    enum = ctx.profile.role(state_role).get("enum") or {}

    def name(v: float) -> str:
        k = str(int(v)) if float(v).is_integer() else str(v)
        return enum.get(k, k)
    items, refs = [], []
    keepset = set(keep)
    for i in range(1, len(st)):
        if i not in keepset or st.v[i] == st.v[i - 1]:
            continue
        t, t_prev = st.t[i], st.t[i - 1]
        row = {"t": _r(t, 3), "from": name(st.v[i - 1]), "to": name(st.v[i]), "state_row": st.ref(i)["row"], "changes": {}}
        for r, s in others.items():
            a = s.at(t_prev - pre_s, max_age=s.rs.max_age_s + pre_s)
            b = s.at(t + post_s, max_age=s.rs.max_age_s + post_s)
            row["changes"][r] = None if a is None or b is None else {"before": _r(s.v[a]), "after": _r(s.v[b]), "delta": _r(s.v[b] - s.v[a]),
                                                                    "rows": [s.ref(a)["row"], s.ref(b)["row"]]}
            if b is not None and len(refs) < MAX_EVIDENCE:
                refs.append(s.ref(b))
        items.append(row)
        if len(refs) < MAX_EVIDENCE:
            refs.append(st.ref(i))
    by_pair: Dict[str, int] = {}
    for it in items:
        k = f"{it['from']} -> {it['to']}"
        by_pair[k] = by_pair.get(k, 0) + 1
    q = {"transitions": len(items), "by_kind": by_pair}
    return _out("transitions", q, {k: "observation" for k in q}, [st.input()] + [s.input() for s in others.values()], refs,
                [f"before: the latest fresh sample at the previous state sample minus {pre_s} s; after: {post_s} s after the new state was reported"] + conds,
                items=items[:MAX_ITEMS])


def crosscheck(ctx: Context, role_a: str, role_b: str, window: str, t0: Optional[float] = None, t1: Optional[float] = None,
               where: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Two signals said to measure related quantities, paired on role_a's timestamps: difference statistics, how often
    they are identical, correlation, and whether the profile declares them independent (agreement between a signal and
    its own inputs is not evidence)."""
    a, b = ctx.series(role_a, window, t0, t1), ctx.series(role_b, window)
    keep, conds = _where(ctx, a, where)
    pairs, refs = [], []
    for i in keep:
        j = b.at(a.t[i])
        if j is not None:
            pairs.append((a.v[i], b.v[j], i, j))
    if not pairs:
        raise ComputeError(f"no fresh pairs of {role_a} and {role_b} in {window}")
    d = [p[1] - p[0] for p in pairs]
    res = b.rs.resolution or a.rs.resolution or 1e-9
    corr = None
    if len(pairs) > 2:
        xa, xb = [p[0] for p in pairs], [p[1] for p in pairs]
        try:
            corr = statistics.correlation(xa, xb)
        except statistics.StatisticsError:
            corr = None
    worst = max(pairs, key=lambda p: abs(p[1] - p[0]))
    refs = [a.ref(worst[2]), b.ref(worst[3]), a.ref(pairs[0][2]), b.ref(pairs[0][3])]
    indep = ctx.profile.independence(role_a, role_b)
    q = {"pairs": len(pairs), "median_b_minus_a": _r(statistics.median(d)), "max_abs_diff": _r(max(abs(x) for x in d)),
         "identical_fraction": _r(sum(abs(x) <= res for x in d) / len(d), 4), "correlation": _r(corr, 4),
         "declared_independent": indep}
    kinds = {k: "observation" for k in q}
    kinds["declared_independent"] = "reported_value"
    notes = []
    if indep is False:
        notes.append("the profile declares one signal derived from the other or both from a shared input: their agreement is not evidence")
    elif indep is None:
        notes.append("an estimate's inputs are not declared: whether the two share an input is unknown")
    if q["identical_fraction"] and q["identical_fraction"] > 0.99:
        notes.append("the two signals are identical in almost every pair: one is probably a copy of the other, whatever their names say")
    return _out("crosscheck", q, kinds, [a.input(), b.input()], refs, [f"pairs use {role_b}'s latest fresh sample at {role_a}'s time"] + conds, notes)


def topology(ctx: Context, pack_role: str, cell_roles: List[str], window: str, t0: Optional[float] = None, t1: Optional[float] = None,
             max_cell_spread_v: float = 0.05, min_support: float = 0.9, where: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Infer the series cell count from pack voltage against cell voltages. With one cell role (a mean), the ratio is
    pack / mean. With several (e.g. min and max), only samples whose cell spread is <= max_cell_spread_v are used and the
    count is bracketed by pack/max .. pack/min. An integer is inferred only when it is consistent with >= min_support of
    the usable samples and no other integer is; otherwise the inference is ambiguous."""
    pack = ctx.series(pack_role, window, t0, t1)
    cells = [ctx.series(r, window) for r in cell_roles]
    if not cells:
        raise ComputeError("give at least one cell voltage role")
    keep, conds = _where(ctx, pack, where)
    brackets, refs, skipped_spread, stale = [], [], 0, 0
    for i in keep:
        vals = []
        for c in cells:
            j = c.at(pack.t[i])
            if j is None:
                break
            vals.append((c.v[j], c, j))
        if len(vals) != len(cells):
            stale += 1
            continue
        cv = [v for v, *_ in vals]
        if max(cv) - min(cv) > max_cell_spread_v:
            skipped_spread += 1
            continue
        if min(cv) <= 0:
            continue
        brackets.append((pack.v[i] / max(cv), pack.v[i] / min(cv), i, vals))
    if not brackets:
        raise ComputeError(f"no usable sample: of {len(keep)} {pack_role} samples, {stale} had no fresh sample of every cell role and "
                           f"{skipped_spread} had a cell spread above max_cell_spread_v={max_cell_spread_v} V")
    mids = [(lo + hi) / 2 for lo, hi, *_ in brackets]
    lo_n, hi_n = math.floor(min(lo for lo, *_ in brackets)), math.ceil(max(hi for _, hi, *_ in brackets))
    support = {}
    for n in range(max(1, lo_n), hi_n + 1):
        support[n] = sum(lo - 0.5 <= n <= hi + 0.5 for lo, hi, *_ in brackets) / len(brackets)
    ranked = sorted(support.items(), key=lambda kv: -kv[1])
    best = ranked[0]
    rivals = [n for n, s in ranked[1:] if s >= min_support]
    inferred = best[0] if best[1] >= min_support and not rivals else None
    med = statistics.median(mids)
    for lo, hi, i, vals in (brackets[0], brackets[len(brackets) // 2], brackets[-1]):
        refs.append(pack.ref(i))
        refs += [c.ref(j) for _, c, j in vals]
    declared, src = ctx.profile.parameter("series_count")
    indep = [ctx.profile.independence(pack_role, r) for r in cell_roles]
    if "cell_voltages" in ctx.profile.inputs_closure(pack_role):      # the named quantity the cell roles are read from
        indep = [False]
    q = {"usable_samples": len(brackets), "skipped_for_spread": skipped_spread, "median_ratio": _r(med, 3),
         "ratio_p5_p95": [_r(sorted(mids)[int(0.05 * (len(mids) - 1))], 3), _r(sorted(mids)[int(0.95 * (len(mids) - 1))], 3)],
         "candidates": [{"series_count": n, "support": _r(s, 4)} for n, s in ranked[:4]],
         "inferred_series_count": inferred, "declared_series_count": declared,
         "declared_agrees": None if declared is None or inferred is None else int(declared) == inferred,
         "pack_independent_of_cells": None if None in indep else all(indep)}
    kinds = {k: "observation" for k in q}
    kinds.update(inferred_series_count="inferred_parameter", candidates="inferred_parameter", declared_series_count="reported_value",
                 declared_agrees="observation", pack_independent_of_cells="reported_value")
    if declared is not None and any(x in str(src).lower() for x in VERIFIED_SOURCES):
        kinds["declared_series_count"] = "verified_fact"
    notes = []
    if inferred is None:
        notes.append("ambiguous: no single integer is consistent with enough samples; do not use a series count from this result")
    if q["pack_independent_of_cells"] is not True:
        notes.append("provenance: the profile does not declare pack voltage independent of the cell voltages. If the BMS computes the pack "
                     "voltage as the sum of its cells, the ratio is its own arithmetic: it gives the count of cells the BMS sums, "
                     "which equals the series count only if every cell is summed")
    return _out("topology", q, kinds, [pack.input()] + [c.input() for c in cells], refs,
                [f"samples with cell spread > {max_cell_spread_v} V are not used",
                 "with several cell roles the per-cell mean is bracketed by their min and max (never assumed to be their midpoint)",
                 f"an integer is inferred only if consistent with >= {min_support} of usable samples and unique"] + conds, notes)


ANCHORS = ("displayed_soc_correct", "displayed_start_true_end", "counter_since_anchor")


def capacity(ctx: Context, current_role: str, soc_role: str, window: str, t0: float, t1: float, anchors: List[Dict[str, Any]],
             rated_capacity_ah: Optional[float] = None, max_gap_s: Optional[float] = None) -> Dict[str, Any]:
    """Usable capacity implied by the charge counted between t0 and t1, under each declared SOC anchor. Every result is
    CONDITIONAL on its anchor; the anchors are what the caller must state, and comparing them shows how much the
    conclusion depends on that choice.

    anchors (each a dict with "kind"):
      displayed_soc_correct      the displayed SOC is correct at t0 and t1:   C = Q / (soc_t0 - soc_t1)
      displayed_start_true_end   displayed SOC correct at t0, true SOC at t1 = end_true_soc_pct (e.g. empty at a protective
                                 cutoff, itself an assumption):              C = Q / (soc_t0 - end)
      counter_since_anchor       the SOC counter was set to anchor_true_soc_pct at an earlier anchor (e.g. a full charge) and
                                 counted correctly against rated capacity R since then; true SOC at t1 = end_true_soc_pct:
                                 charge before t0 = (anchor - soc_t0)/100 * R;   C = (that + Q) / (anchor - end) * 100
    """
    if not anchors:
        raise ComputeError(f"declare at least one anchor; kinds: {', '.join(ANCHORS)}")
    cur, soc = ctx.series(current_role, window, t0, t1), ctx.series(soc_role, window, t0, t1)
    if len(soc) < 2:
        raise ComputeError(f"fewer than 2 valid {soc_role} samples in {window} {t0}..{t1}")
    gap = ctx.profile.max_gap_s if max_gap_s is None else float(max_gap_s)
    g = _integrate(cur, list(range(len(cur))), gap)
    Q = g["net"] / 3600.0
    s0, s1 = soc.v[0], soc.v[-1]
    R, R_src = rated_capacity_ah, "given by the caller (an assumption unless it is a verified specification)"
    if R is None:
        v, src = ctx.profile.parameter("capacity_ah")
        if v is not None:
            R, R_src = float(v), f"profile parameter ({src})"
    if R is None and ctx.profile.has_role("rated_capacity"):
        rc = ctx.series("rated_capacity", window)
        if len(rc):
            R, R_src = statistics.median(rc.v), "reported by the machine on the bus (rated_capacity role): not verified"
    results, ass = [], [f"counted charge Q is the integral of {current_role} from {t0} to {t1} s; intervals longer than {gap} s are not bridged",
                        "the measured current is the whole pack current (no load upstream of the sensor)"]
    for an in anchors:
        k = an.get("kind")
        if k not in ANCHORS:
            raise ComputeError(f"anchor kind must be one of {ANCHORS}")
        end = an.get("end_true_soc_pct")
        r: Dict[str, Any] = {"anchor": an, "kind": "conditional"}
        if k == "displayed_soc_correct":
            den = s0 - s1
            r["assumes"] = [f"displayed {soc_role} is correct at {t0} s and at {t1} s"]
            r["implied_capacity_ah"] = _r(Q / den * 100, 3) if abs(den) > 1e-9 else None
        elif k == "displayed_start_true_end":
            if end is None:
                raise ComputeError("displayed_start_true_end needs end_true_soc_pct")
            den = s0 - float(end)
            r["assumes"] = [f"displayed {soc_role} is correct at {t0} s", f"true SOC at {t1} s is {end} %"]
            r["implied_capacity_ah"] = _r(Q / den * 100, 3) if abs(den) > 1e-9 else None
        else:
            anc = an.get("anchor_true_soc_pct")
            if anc is None or end is None:
                raise ComputeError("counter_since_anchor needs anchor_true_soc_pct and end_true_soc_pct")
            if R is None:
                raise ComputeError("counter_since_anchor needs the rated capacity the counter divides by (rated_capacity_ah, a capacity_ah parameter or a rated_capacity role)")
            before = (float(anc) - s0) / 100.0 * R
            den = float(anc) - float(end)
            r["assumes"] = [f"the counter was set to {anc} % at an earlier anchor and counted correctly since, against {_r(R, 3)} Ah ({R_src})",
                            f"true SOC at {t1} s is {end} %"]
            r["charge_before_t0_ah"] = _r(before, 3)
            r["implied_capacity_ah"] = _r((before + Q) / den * 100, 3) if abs(den) > 1e-9 else None
        if R and r.get("implied_capacity_ah") is not None:
            r["fraction_of_rated"] = _r(r["implied_capacity_ah"] / R, 4)
        results.append(r)
    q = {"counted_ah": _r(Q, 4), "soc_t0_pct": _r(s0, 3), "soc_t1_pct": _r(s1, 3), "rated_capacity_ah": _r(R, 3), "rated_capacity_source": R_src if R else None,
         "gap_s": _r(g["gap_s"], 3), "anchors": results}
    kinds = {"counted_ah": "observation", "soc_t0_pct": "reported_value", "soc_t1_pct": "reported_value", "gap_s": "observation",
             "rated_capacity_ah": "verified_fact" if any(x in R_src.lower() for x in VERIFIED_SOURCES) else "reported_value",
             "rated_capacity_source": "observation", "anchors": "conditional"}
    refs = [cur.ref(0), cur.ref(len(cur) - 1), soc.ref(0), soc.ref(len(soc) - 1)]
    spread = [x["implied_capacity_ah"] for x in results if x.get("implied_capacity_ah") is not None]
    notes = []
    if len(spread) > 1 and min(spread) > 0 and max(spread) / min(spread) > 1.2:
        notes.append(f"the implied capacity depends strongly on the anchor ({min(spread)}..{max(spread)} Ah): a conclusion about capacity "
                     "that does not state its anchor is not supported")
    return _out("capacity", q, kinds, [cur.input(), soc.input()], refs, ass, notes)


OPS = {"stats": stats, "compare_windows": compare_windows, "integrate": integrate, "step_response": step_response,
       "unexplained_changes": unexplained_changes, "transitions": transitions, "crosscheck": crosscheck, "topology": topology,
       "capacity": capacity}

OP_DOC = {
    "stats": "stats{role, window, t0?, t1?, where?}: n, first/last, min/max, mean, median, p5/p95, with rows.",
    "compare_windows": "compare_windows{role, a:{window,t0?,t1?}, b:{...}, where?}: the same statistics in two windows or ranges and their differences.",
    "integrate": "integrate{role, window, t0?, t1?, max_gap_s?, where?}: time integral (a current gives Ah, discharge positive); gaps are not bridged and are reported with their bound.",
    "step_response": "step_response{x_role, y_role, window, t0?, t1?, min_dx=5, settle_s=0, max_dt_s?}: each step of x against the change of y: ratio -dy/dx per step (voltage vs current: apparent resistance), median, spread, sign consistency.",
    "unexplained_changes": "unexplained_changes{y_role, x_role, window, t0?, t1?, min_dy=1, max_dx=1}: steps of y with no matching change in x (e.g. voltage moving without measured current).",
    "transitions": "transitions{state_role, window, roles:[...], t0?, t1?, pre_s=0, post_s=1, where?}: at each change of a state, each role before (at the last sample of the old state) and after.",
    "crosscheck": "crosscheck{role_a, role_b, window, t0?, t1?, where?}: paired difference, identical fraction, correlation, declared independence.",
    "topology": "topology{pack_role, cell_roles:[...], window, t0?, t1?, max_cell_spread_v=0.05, min_support=0.9, where?}: series cell count inferred from pack vs cell voltages, with support, ambiguity and provenance.",
    "capacity": "capacity{current_role, soc_role, window, t0, t1, anchors:[{kind, end_true_soc_pct?, anchor_true_soc_pct?}], rated_capacity_ah?}: usable capacity implied by counted charge under each declared SOC anchor (kinds: displayed_soc_correct, displayed_start_true_end, counter_since_anchor). Conditional results only.",
}
WHERE_DOC = "where: [{role, op (<, <=, >, >=, ==, !=), value}] keeps only samples where that role's latest fresh value satisfies it."


def run(ctx: Context, op: str, args: Dict[str, Any]) -> Dict[str, Any]:
    fn = OPS.get(op)
    if fn is None:
        raise ComputeError(f"unknown op {op!r}; ops: {', '.join(sorted(OPS))}")
    try:
        return fn(ctx, **(args or {}))
    except TypeError as e:
        raise ComputeError(f"{op}: {e}. Signature: {OP_DOC[op]}") from None


def _register_dynamics() -> None:
    """Signal-dynamics operations (app.physchecks.dynamics) share this registry, so the investigator discovers them with the rest."""
    from app.physchecks import dynamics as D
    OPS.update(D.OPS)
    OP_DOC.update(D.OP_DOC)


_register_dynamics()
