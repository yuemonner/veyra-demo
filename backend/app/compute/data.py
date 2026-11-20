"""Read-only access to stored readings for the agent's queries and computations.

Nothing here writes. A query names a case (the current one, or another case in the same workspace), a signal, an
optional scope and a window; the answer is the readings with their unit, clock and source row, plus a fingerprint
of exactly what was returned so a computation can be traced back to its inputs.
"""
from __future__ import annotations

import hashlib
import json
import statistics
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

MAX_SERIES_POINTS = 50_000   # per series handed to a computation
MAX_SERIES = 8               # series per computation


def iso(t: Optional[float]) -> Optional[str]:
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z") if t is not None else None


def parse_t(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def signal_name(row: Dict[str, Any]) -> str:
    return row.get("signal") or ("unmapped:" + str(row.get("source_signal")))


class CaseData:
    """Readings of the cases in one store. Cached per case for the life of one agent run."""

    def __init__(self, store):
        self.store = store
        self._rows: Dict[str, List[Dict[str, Any]]] = {}
        self._runs: Dict[str, Optional[Dict[str, Any]]] = {}
        self._imports: Dict[str, List[Dict[str, Any]]] = {}

    # ---- basics ----
    def run(self, case_id: str) -> Optional[Dict[str, Any]]:
        if case_id not in self._runs:
            self.store.case(case_id)  # raises if the case does not exist
            self._runs[case_id] = self.store.latest_run(case_id)
        return self._runs[case_id]

    def imports(self, case_id: str) -> List[Dict[str, Any]]:
        if case_id not in self._imports:
            self._imports[case_id] = self.store.imports(case_id)
        return self._imports[case_id]

    def rows(self, case_id: str) -> List[Dict[str, Any]]:
        if case_id not in self._rows:
            run = self.run(case_id)
            used = set(run["inputs"]) if run else None
            self._rows[case_id] = sorted((r for i in self.imports(case_id) if used is None or i["id"] in used for r in i["rows"]), key=lambda r: r["t"])
        return self._rows[case_id]

    def clock_bounds(self, case_id: str) -> Dict[str, Optional[float]]:
        """clock domain -> declared max skew (s) against true time, None when nobody declared one."""
        out: Dict[str, Optional[float]] = {}
        for i in self.imports(case_id):
            known = out.get(i["clock_domain"], "unset")
            skew = i.get("max_clock_skew_s")
            out[i["clock_domain"]] = skew if known == "unset" else (None if known is None or skew is None else max(known, skew))
        return out

    def window(self, case_id: str, which: Optional[str], start: Optional[str] = None, end: Optional[str] = None) -> Optional[Tuple[float, float]]:
        if which in (None, "all"):
            return None
        run = self.run(case_id)
        if which == "symptom":
            if not run or not run.get("symptom_window"):
                raise ValueError(f"Case {case_id} has no symptom window")
            return parse_t(run["symptom_window"][0]), parse_t(run["symptom_window"][1])
        if which == "reference":
            ref = (run or {}).get("reference") or {}
            if not ref.get("window_iso"):
                raise ValueError(f"Case {case_id} has no reference window")
            return parse_t(ref["window_iso"][0]), parse_t(ref["window_iso"][1])
        if which == "custom":
            if not start or not end:
                raise ValueError("A custom window needs start and end (ISO 8601, UTC)")
            lo, hi = parse_t(start), parse_t(end)
            if hi <= lo:
                raise ValueError("The window must end after it starts")
            return lo, hi
        raise ValueError("window must be 'all', 'symptom', 'reference' or 'custom'")

    # ---- catalogue and search ----
    def catalogue(self, case_id: str) -> List[Dict[str, Any]]:
        groups: Dict[Tuple[str, Any, Any, Any], List[Dict[str, Any]]] = {}
        for r in self.rows(case_id):
            groups.setdefault((signal_name(r), r.get("scope"), r.get("unit"), r.get("clock_domain")), []).append(r)
        bounds = self.clock_bounds(case_id)
        out = []
        for (sig, scope, unit, clock), items in sorted(groups.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
            ts = [r["t"] for r in items]
            gaps = [b - a for a, b in zip(ts, ts[1:]) if b > a]
            originals = sorted({str(r.get("original_unit")) for r in items if r.get("original_unit") and r.get("original_unit") != unit})
            out.append({"signal": sig, "scope": scope, "unit": unit, "converted_from": originals or None, "clock": clock,
                        "clock_max_skew_s": bounds.get(clock), "numeric": any(isinstance(r.get("value"), (int, float)) for r in items[:50]),
                        "count": len(items), "start": iso(ts[0]), "end": iso(ts[-1]),
                        "median_rate_hz": round(1 / statistics.median(gaps), 3) if gaps else None})
        return out

    def cases(self, *, machine_id: Optional[str] = None, machine_class: Optional[str] = None, signal: Optional[str] = None,
              exclude: Optional[str] = None, limit: int = 30) -> List[Dict[str, Any]]:
        out = []
        for c in reversed(self.store.cases()):
            if c["id"] == exclude or (machine_id and c["machine_id"] != machine_id):
                continue
            klass = ((self.store.context(c["machine_id"]).get("attributes") or {}).get("machine_class") or {}).get("value")
            if machine_class and klass != machine_class:
                continue
            signals = sorted({signal_name(r) for r in self.rows(c["id"])}) if signal or len(out) < limit else []
            if signal and signal not in signals:
                continue
            ledger = self.store.ledger(c["id"])
            closure = next((e["payload"] for e in reversed(ledger) if e["kind"] == "case_closure"), None)
            run = self.run(c["id"])
            out.append({"case_id": c["id"], "machine_id": c["machine_id"], "machine_class": klass, "title": c["title"], "synthetic": bool(c["synthetic"]),
                        "known_good_run": c.get("fault_type") == "known_good_run", "created_at": c["created_at"],
                        "symptom_window": (run or {}).get("symptom_window"),
                        "closure": {k: closure.get(k) for k in ("investigation_status", "cause_domain", "cause", "resolution_status")} if closure else None,
                        "signals": signals[:40]})
            if len(out) >= limit:
                break
        return out

    # ---- series ----
    def series(self, case_id: str, signal: str, *, scope: Optional[str] = None, window: Optional[str] = "all",
               start: Optional[str] = None, end: Optional[str] = None, max_points: int = MAX_SERIES_POINTS) -> Dict[str, Any]:
        span = self.window(case_id, window, start, end)
        picked = [r for r in self.rows(case_id) if signal_name(r) == signal and (scope is None or r.get("scope") == scope)
                  and (span is None or span[0] <= r["t"] <= span[1])]
        if not picked:
            raise ValueError(f"No readings of {signal}{' (' + scope + ')' if scope else ''} in case {case_id} for that window")
        scopes = sorted({str(r.get("scope")) for r in picked})
        if scope is None and len(scopes) > 1:
            raise ValueError(f"{signal} has several scopes in case {case_id}: {', '.join(scopes[:12])}. Name one.")
        units = sorted({str(r.get("unit")) for r in picked})
        clocks = sorted({str(r.get("clock_domain")) for r in picked})
        if len(units) > 1 or len(clocks) > 1:
            raise ValueError(f"{signal} mixes units {units} or clocks {clocks}; query each separately")
        thinned_every = max(1, -(-len(picked) // max_points))
        kept = picked[::thinned_every]
        numeric = all(isinstance(r.get("value"), (int, float)) for r in kept)
        values = [r.get("value") if numeric else (r.get("value") if r.get("value") is not None else r.get("event_value") or r.get("code")) for r in kept]
        digest = hashlib.sha256(json.dumps([[r.get("import_id"), r.get("row"), r["t"], r.get("value")] for r in kept], separators=(",", ":"), default=str).encode()).hexdigest()
        bound = self.clock_bounds(case_id).get(clocks[0])
        return {"case_id": case_id, "signal": signal, "scope": picked[0].get("scope"), "unit": units[0], "clock": clocks[0], "clock_max_skew_s": bound,
                "numeric": numeric, "window": [iso(span[0]), iso(span[1])] if span else "all",
                "t": [r["t"] for r in kept], "v": values, "sources": [f"{r.get('import_id')}#row{r.get('row')}" for r in kept],
                "provenance": {"case_id": case_id, "signal": signal, "scope": picked[0].get("scope"), "unit": units[0], "clock": clocks[0],
                               "window": [iso(span[0]), iso(span[1])] if span else "all", "rows": len(picked), "used": len(kept), "thinned_every": thinned_every,
                               "first_source": f"{kept[0].get('import_id')}#row{kept[0].get('row')}", "last_source": f"{kept[-1].get('import_id')}#row{kept[-1].get('row')}",
                               "rows_sha256": digest}}
