"""Expected behaviour against observed behaviour, layer by layer, and the first point where they part.

Instead of matching error codes, ask what the machine should have done given what it was asked and what it was told:
the operator's or autonomy's intent, the controller's decisions, the physical response of motors, power and brakes,
under the conditions of the moment. Each expectation is a small, checkable rule over the decoded signals (a value
holds, a signal follows a command, a response arrives in time, a protection fires when it should). The engine
reports, per recorded episode:

  - each expectation as met, violated (first time, value, line), not triggered, or NOT OBSERVABLE (the signal it needs
    is not recorded, with what would be needed): "the controller reported the brake released, but whether the
    calipers opened cannot be determined: needs caliper position";
  - the first divergence: the earliest violated expectation, with its line;
  - the chain: divergences, designed protections that fired, and the symptom, in time order;
  - a contrast across episodes: which expectations break only in the episode with the fault.

Expectations come from a declared spec (app/physics/specs/*.json): constraints engineers state, or that are read from
the log header and conventions, marked as such until engineers confirm them. A learned counterpart (a model of normal
behaviour fitted on healthy runs) is research on the ALFA data (docs/research/INTERNAL_MODEL_ALFA.md).

The design borrows two ideas from recent research, adapted to diagnosis rather than control: a reference trajectory to
compare execution against (Brito, ..., McNamee, "World Models as Reference Trajectories for Rapid Motor Adaptation",
NeurIPS 2025), and modelling the controller, body and environment as one closed loop rather than isolated signals
(McNamee et al., "Integrative neurocybernetic modeling in the era of large-scale neuroscience", 2026). Deterministic;
no model call; nothing here confirms a cause.
"""
from __future__ import annotations

import bisect
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.physics.electrical import parse

VERSION = "expected-behavior-v1"
SPEC_DIR = Path(__file__).resolve().parent / "specs"
MAX_AGE_S = 0.35   # a CAN value is held until the next frame; older than this it is stale
OPS = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
       "==": lambda a, b: a == b, "!=": lambda a, b: a != b}


def load_specs() -> List[Dict[str, Any]]:
    return [json.loads(p.read_text()) for p in sorted(SPEC_DIR.glob("*.json"))]


class _Sig:
    """One recorded field of one message: zero-order hold between frames, as CAN values are."""

    def __init__(self, pts: List[Tuple[float, Any, int]]):
        pts.sort(key=lambda p: p[0])
        self.t = [p[0] for p in pts]
        self.v = [p[1] for p in pts]
        self.n = [p[2] for p in pts]

    def at(self, t: float) -> Optional[Tuple[Any, int]]:
        i = bisect.bisect_right(self.t, t + 1e-6) - 1
        if i < 0 or t - self.t[i] > MAX_AGE_S:
            return None
        return self.v[i], self.n[i]


class _Snap:
    def __init__(self, snap: Dict[str, Any], lines: List[str]):
        self.label, self.first_line = snap["label"], snap["first_line"]
        self.lines = lines
        self.t0 = snap["rows"][0][0] if snap["rows"] else 0.0
        self.t_end = snap["rows"][-1][0] if snap["rows"] else 0.0
        series: Dict[Tuple[str, str], List[Tuple[float, Any, int]]] = {}
        self.events: List[Tuple[float, int, str]] = []
        for t, msg, vals, n in snap["rows"]:
            for k, v in vals.items():
                series.setdefault((msg, k), []).append((t, v, n))
            if msg.endswith("_Event") or " ERROR " in lines[n - 1] or " WARN " in lines[n - 1]:
                self.events.append((t, n, lines[n - 1].strip()))
        self.sigs = {k: _Sig(v) for k, v in series.items()}

    def resolve(self, ref: str) -> List[Tuple[str, _Sig]]:
        msg, field = ref.split(".", 1)
        pat = re.compile("^" + re.escape(msg).replace(r"\*", ".*") + "$")
        return [(m, s) for (m, f), s in sorted(self.sigs.items()) if f == field and pat.match(m)]

    def value(self, ref: str, t: float) -> Optional[Tuple[Any, int]]:
        r = self.resolve(ref)
        return r[0][1].at(t) if r else None


def _num(x: Any) -> Any:
    return x if isinstance(x, (int, float)) else x


def _cond(sn: _Snap, c: Dict[str, Any], sig: Optional[_Sig], t: float) -> Optional[Tuple[bool, Any, Any, int]]:
    """Evaluate one condition at time t: (holds, value, reference, line) or None when a value is missing."""
    got = sig.at(t) if sig is not None else sn.value(c["signal"], t)
    if got is None:
        return None
    v, n = got
    if "ref" in c:
        rr = sn.value(c["ref"], t)
        if rr is None:
            return None
        r = rr[0]
    else:
        r = c["value"]
    try:
        if isinstance(v, (int, float)) and isinstance(r, (int, float)):
            tol = c.get("tol", 0.0)
            rr_ = r + tol if c["op"] in ("<=", "<") else r - tol if c["op"] in (">=", ">") else r
            return OPS[c["op"]](v, rr_), v, r, n
        return OPS[c["op"]](str(v), str(r)), v, r, n
    except TypeError:
        return None


def _window(sn: _Snap, spec: Dict[str, Any]) -> Optional[Tuple[float, float, int, Optional[Dict[str, Any]]]]:
    w = spec["window"]
    times = sorted({t for c in w["start"] for _, s in sn.resolve(c["signal"]) for t in s.t})
    start = None
    for t in times:
        for c in w["start"]:
            r = _cond(sn, c, None, t)
            if r and r[0]:
                start = (t, r[3])
                break
        if start:
            break
    if not start:
        return None
    end, symptom = sn.t_end, None
    ends = sorted({t for c in w["end"] for _, s in sn.resolve(c["signal"]) for t in s.t if t > start[0]})
    for t in ends:
        hit = next((r for c in w["end"] for r in [_cond(sn, c, None, t)] if r and r[0]), None)
        if hit:
            end = t
            symptom = {"t": t, "line": hit[3], "quote": sn.lines[hit[3] - 1].strip()[:200], "text": (spec.get("symptom") or {}).get("text", "symptom")}
            break
    return start[0], end, start[1], symptom


def _check(sn: _Snap, e: Dict[str, Any], ws: float, we: float) -> Dict[str, Any]:
    kind = e["kind"]
    out: Dict[str, Any] = {"id": e["id"], "layer": e["layer"], "kind": kind, "text": e["text"]}
    if kind == "trigger":
        return {**out, "status": "met"}

    def missing(*refs: str) -> Optional[str]:
        return next((r for r in refs if r and not sn.resolve(r)), None)

    if kind in ("holds", "follows", "responds"):
        gone = missing(e["signal"], e.get("ref"))
        if gone:
            return {**out, "status": "not_observable", "missing": gone, "needs": e.get("needs") or f"{gone} on CAN"}
    if kind == "holds":
        first, count, who = None, 0, set()
        for m, s in sn.resolve(e["signal"]):
            for t in s.t:
                if ws <= t <= we:
                    r = _cond(sn, e, s, t)
                    if r and not r[0]:
                        count += 1
                        who.add(m)
                        if first is None or t < first["t"]:
                            first = {"t": t, "value": r[1], "ref": r[2], "line": r[3], "signal": f"{m}.{e['signal'].split('.', 1)[1]}"}
        if first:
            return {**out, "status": "violated", "first": first, "count": count, "where": sorted(who)}
        return {**out, "status": "met"}
    if kind == "follows":
        first, count, who = None, 0, set()
        lag = e.get("lag_s", 0.0)
        for m, s in sn.resolve(e["signal"]):
            for t, v, n in zip(s.t, s.v, s.n):
                if not (ws + lag <= t <= we) or not isinstance(v, (int, float)):
                    continue
                refs = [x for x in (sn.value(e["ref"], t), sn.value(e["ref"], t - lag)) if x and isinstance(x[0], (int, float))]
                if not refs:
                    continue
                tol = lambda r: max(e.get("tol_abs", 0.0), e.get("tol_rel", 0.0) * abs(r))  # noqa: E731
                if all(abs(v - r[0]) > tol(r[0]) for r in refs):
                    count += 1
                    who.add(m)
                    if first is None or t < first["t"]:
                        first = {"t": t, "value": v, "ref": refs[0][0], "line": n, "signal": f"{m}.{e['signal'].split('.', 1)[1]}"}
        if first:
            return {**out, "status": "violated", "first": first, "count": count, "where": sorted(who)}
        return {**out, "status": "met"}
    if kind == "responds":
        deadline = ws + e["within_s"]
        late = []
        for m, s in sn.resolve(e["signal"]):
            hit = next(((t, n) for t, n in zip(s.t, s.n) if ws <= t <= min(deadline, we) and (_cond(sn, e, s, t) or (False,))[0]), None)
            if hit is None:
                late.append(m)
        if not late:
            return {**out, "status": "met"}
        if we < deadline:
            return {**out, "status": "not_reached", "where": late, "note": "the episode ended before the deadline"}
        first_line = next((n for _, s in sn.resolve(e["signal"]) for t, n in zip(s.t, s.n) if t >= deadline), None)
        return {**out, "status": "violated", "first": {"t": deadline, "line": first_line, "signal": e["signal"]}, "where": late}
    if kind == "reaction":
        trig_t, trig_line = None, None
        if "trigger" in e:
            gone = missing(e["trigger"]["signal"])
            if gone:
                return {**out, "status": "not_observable", "missing": gone, "needs": e.get("needs") or f"{gone} on CAN"}
            for m, s in sn.resolve(e["trigger"]["signal"]):
                run_start = None
                for t, n in zip(s.t, s.n):
                    r = _cond(sn, e["trigger"], s, t)
                    if r and r[0]:
                        run_start = run_start if run_start is not None else (t, n)
                        if t - run_start[0] >= e.get("for_s", 0.0) - 1e-6 and (trig_t is None or t < trig_t):
                            trig_t, trig_line = t, n
                            break
                    else:
                        run_start = None
        else:
            hit = next(((t, n) for t, n, txt in sn.events if re.search(e["trigger_event"], txt)), None)
            if hit:
                trig_t, trig_line = hit
        if trig_t is None:
            return {**out, "status": "not_triggered"}
        resp = next(((t, n, txt) for t, n, txt in sn.events if trig_t - 1e-6 <= t <= trig_t + e["within_s"] and re.search(e["response_event"], txt)), None)
        if resp:
            return {**out, "status": "designed_response", "trigger": {"t": trig_t, "line": trig_line}, "first": {"t": resp[0], "line": resp[1]}}
        return {**out, "status": "violated", "trigger": {"t": trig_t, "line": trig_line}, "first": {"t": trig_t + e["within_s"], "line": trig_line},
                "note": "the protection did not respond in time"}
    return {**out, "status": "unknown_kind"}


def analyse(lines: List[str], spec: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    snaps = parse(lines)
    if not snaps:
        return None
    if spec is None:
        spec = pick_spec(snaps, lines)
        if spec is None:
            return None
    out_snaps = []
    for s in snaps:
        sn = _Snap(s, lines)
        w = _window(sn, spec)
        if not w:
            out_snaps.append({"label": s["label"], "first_line": s["first_line"], "window": None})
            continue
        ws, we, wline, symptom = w
        # protections first: once one fires the machine is in a protective state, and normal-operation expectations stop
        reacts = {e["id"]: _check(sn, e, ws, we) for e in spec["expectations"] if e["kind"] == "reaction"}
        fired = [r["trigger"]["t"] for r in reacts.values() if r["status"] == "designed_response"]
        we_normal = min([we] + [t + 1e-6 for t in fired])
        res = [reacts[e["id"]] if e["id"] in reacts else _check(sn, e, ws, we_normal) for e in spec["expectations"]]
        for r in res:
            for k in ("first", "trigger"):
                if r.get(k) and r[k].get("t") is not None:
                    r[k]["rel_s"] = round(r[k]["t"] - ws, 2)
                    if r[k].get("line"):
                        r[k]["quote"] = lines[r[k]["line"] - 1].strip()[:200]
        viol = sorted([r for r in res if r["status"] == "violated"], key=lambda r: r["first"]["t"])
        chain = [{"t_rel": r["first"]["rel_s"], "kind": "divergence", "id": r["id"], "layer": r["layer"], "text": r["text"], "line": r["first"].get("line")}
                 for r in viol]
        chain += [{"t_rel": r["first"]["rel_s"], "kind": "protection", "id": r["id"], "layer": r["layer"], "text": r["text"], "line": r["first"]["line"]}
                  for r in res if r["status"] == "designed_response"]
        if symptom:
            chain.append({"t_rel": round(symptom["t"] - ws, 2), "kind": "symptom", "id": "symptom", "layer": "controller", "text": symptom["text"], "line": symptom["line"]})
        chain.sort(key=lambda c: (c["t_rel"], {"divergence": 0, "protection": 1, "symptom": 2}[c["kind"]]))
        ctx = {}
        for c in spec.get("context") or []:
            v = sn.value(c["signal"], ws)
            if v is not None:
                ctx[c["label"]] = {"value": v[0], "unit": c.get("unit", ""), "line": v[1]}
        out_snaps.append({"label": s["label"], "first_line": s["first_line"],
                          "window": {"start_line": wline, "duration_s": round(we - ws, 2), "ended_by_symptom": bool(symptom)},
                          "results": res, "first_divergence": ({k: viol[0][k] for k in ("id", "layer", "text")} | {"first": viol[0]["first"]}) if viol else None,
                          "chain": chain, "symptom": symptom, "context": ctx})
    used = [s for s in out_snaps if s["window"]]
    if not used:
        return {"version": VERSION, "applies": True, "spec": _spec_meta(spec), "snapshots": out_snaps, "status": "not_identifiable",
                "reason": "no episode in this log where motion was requested"}
    contrast = []
    for e in spec["expectations"]:
        st = [next(r["status"] for r in s["results"] if r["id"] == e["id"]) for s in used]
        if "violated" in st and "met" in st:
            contrast.append({"id": e["id"], "text": e["text"], "violated_in": [i + 1 for i, x in enumerate(st) if x == "violated"],
                             "met_in": [i + 1 for i, x in enumerate(st) if x == "met"]})
    return {"version": VERSION, "applies": True, "status": "ok", "spec": _spec_meta(spec), "snapshots": out_snaps, "contrast": contrast,
            "layers": _layers(spec, used),
            "boundary": "Expectations are declared rules, marked with where they came from; a divergence shows where behaviour left "
                        "the expected path, not which part failed. What is not recorded is stated, not guessed."}


def _spec_meta(spec: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": spec["id"], "name": spec["name"], "source": spec["source"], "layers": spec["layers"]}


def _layers(spec: Dict[str, Any], used: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per layer, over all episodes: what the data confirms, where it diverged, and what it cannot determine."""
    out = {}
    for layer in spec["layers"]:
        confirmed, diverged, blind = [], [], []
        for e in spec["expectations"]:
            if e["layer"] != layer:
                continue
            st = [next(r for r in s["results"] if r["id"] == e["id"]) for s in used]
            if all(r["status"] == "not_observable" for r in st):
                blind.append({"id": e["id"], "text": e["text"], "needs": st[0].get("needs")})
            elif any(r["status"] == "violated" for r in st):
                diverged.append({"id": e["id"], "text": e["text"], "episodes": [i + 1 for i, r in enumerate(st) if r["status"] == "violated"]})
            elif any(r["status"] in ("met", "designed_response") for r in st):
                confirmed.append({"id": e["id"], "text": e["text"]})
        out[layer] = {"confirmed": confirmed, "diverged": diverged, "cannot_determine": blind}
    if "environment" in out:
        out["environment"]["observed"] = [{"episode": i + 1, "context": s["context"]} for i, s in enumerate(used)]
    return out


def pick_spec(snaps: List[Dict[str, Any]], lines: List[str]) -> Optional[Dict[str, Any]]:
    """The spec whose required signals are all recorded."""
    have = {(m, k) for s in snaps for _, m, v, _ in s["rows"] for k in v}
    for spec in load_specs():
        ok = True
        for ref in spec.get("applies_if_signals") or []:
            msg, field = ref.split(".", 1)
            pat = re.compile("^" + re.escape(msg).replace(r"\*", ".*") + "$")
            if not any(f == field and pat.match(m) for m, f in have):
                ok = False
                break
        if ok:
            return spec
    return None


def agent_view(r: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not r:
        return {"applies": False, "note": "No behaviour spec matches this log's signals."}
    eps = []
    for i, s in enumerate(r["snapshots"]):
        if not s.get("window"):
            eps.append({"episode": i + 1, "label": s["label"], "note": "no motion requested"})
            continue
        eps.append({"episode": i + 1, "label": s["label"], "first_divergence": s["first_divergence"],
                    "chain": s["chain"], "context": {k: v["value"] for k, v in s["context"].items()},
                    "not_reached": [x["id"] for x in s["results"] if x["status"] == "not_reached"]})
    return {"version": r["version"], "spec": r["spec"], "episodes": eps, "contrast": r.get("contrast"), "layers": r.get("layers"),
            "how_to_use": ("Cite the first divergence by its expectation id and line. Use the layers: say what the data confirms, "
                           "and for each 'cannot_determine' item say it cannot be determined and what is needed; never assume it. "
                           "A 'protection' in the chain is the machine behaving as designed, not a fault. The expectations come from "
                           "the spec's stated source, not from the machine's engineers unless it says so.")}
