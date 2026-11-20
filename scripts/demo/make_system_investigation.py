"""Public demo data: Veyra's system-level investigation and physical checks on a SYNTHETIC battery incident.

    python scripts/demo/make_system_investigation.py            # writes frontend/public/demo/system-investigation.json
    python scripts/demo/make_system_investigation.py --check    # exits 1 if the committed file is out of date

Everything is computed by the deterministic engine (app.physchecks: checks, facts, hypotheses, expert layer, planner) from
app.physchecks.synthetic.incident(), a first-principles synthetic recording. No customer data is read. The fault-level rules
below are declared for the synthetic machine, the way a manufacturer would declare its own.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
OUT = ROOT / "frontend" / "public" / "demo" / "system-investigation.json"

RULES = {
    "customer": "synthetic manufacturer",
    "fault_levels": {"1": {"permits_hv": True}, "2": {"permits_hv": True}, "3": {"permits_hv": False, "latched": True}},
    "items": ["Fault level 1 and 2 permit high voltage; level 3 is the protective shutdown and should stay latched"],
}
REPORTED = "The shuttle lost drive power and would not move."
SERIES = {"v_cell_min": "Lowest cell (V)", "v_cell_max": "Highest cell (V)", "soc": "Displayed SOC (%)", "i_pack": "Pack current (A)",
          "fault_raw": "BMS fault level"}


def _ev(items, n=3):
    return [{k: e.get(k) for k in ("window", "file", "row", "t", "source", "field", "value")} for e in (items or [])[:n]]


def _finding(f):
    return {"id": f.get("id"), "statement": f.get("statement"), "kind": f.get("kind"), "flag": f.get("flag"),
            "evidence": _ev(f.get("evidence")), "limits": f.get("limits") or []}


def _series(rec, window, step_s=2.0):
    out = {}
    for field, label in SERIES.items():
        if not rec.has(window, field):
            continue
        col = rec.column(window, field)
        pts, last = [], None
        for t, v in zip(col.t, col.v):
            if last is None or t - last >= step_s:
                pts.append([round(t, 2), round(v, 4)])
                last = t
        out[field] = {"label": label, "points": pts}
    return out


def build() -> dict:
    from app.physchecks import base, expert_layer, facts as facts_mod, hypotheses, planner, synthetic
    rec, prof = synthetic.incident(), synthetic.profile()
    checks = base.run_all(rec, prof)
    F = facts_mod.derive(rec, prof, checks)
    A = hypotheses.assess(checks, F, [], machine=hypotheses.machine_of(prof))
    ex = expert_layer.enrich(rec, prof, checks, F, A, rules=RULES, reported_symptom=REPORTED)
    plan = planner.plan(A, checks)
    au = ex["audit"]

    cs = au.get("control_sequence") or {}
    th = au.get("inferred_thresholds") or {}
    si = au.get("signal_independence") or {}
    ca = au.get("charge_alignment") or {}
    ra = au.get("restart_authorisation") or {}
    sv = au.get("symptom_vs_record") or {}
    return {
        "schema": "veyra-demo-system-investigation-v1",
        "synthetic": True,
        "case": {
            "title": "Parked shuttle loses high voltage after an undervoltage trip, then is restarted",
            "reported": REPORTED,
            "machine": "Battery-electric shuttle (synthetic), 100 Ah pack, BMS and VCU logs",
            "recording": rec.recording_id if hasattr(rec, "recording_id") else "syn-incident",
            "windows": {w: list(rec.span(w)) for w in ("early", "fault", "repeat") if rec.span(w)},
            "rules": RULES["items"],
            "engine": {"audit": au.get("version"), "expert_layer": A.get("expert_layer")},
        },
        "system": {
            "symptom": {"statement": sv.get("statement"), "value": sv.get("value")},
            "sequence": {"statement": cs.get("statement"), "events": [{k: e.get(k) for k in ("t", "role", "from", "to")} | {"evidence": _ev(e.get("evidence"), 1)}
                                                                       for e in (cs.get("value") or {}).get("events", [])],
                         "findings": [_finding(f) for f in cs.get("findings") or []], "limits": cs.get("limits") or []},
            "signals": {"statement": si.get("statement"), "items": [_finding(i) for i in si.get("items") or []]},
            "thresholds": {"statement": th.get("statement"), "rows": [{k: r.get(k) for k in ("level", "t", "signal_value", "inferred_threshold", "first_crossing_t", "delay_s")}
                                                                       for r in (th.get("value") or {}).get("rows", [])],
                           "limits": th.get("limits") or []},
            "restarts": {"statement": ra.get("statement"), "rows": ra.get("rows") or [], "findings": [_finding(f) for f in ra.get("findings") or []],
                         "level_first_cell_v": ra.get("level_first_cell_v")},
        },
        "physics": {
            "checks": [{"id": k, "outcome": v.get("outcome"), "summary": v.get("summary"),
                        "calculations": [{c: x.get(c) for c in ("name", "formula", "value", "unit")} for x in (v.get("calculations") or [])[:4]]}
                       for k, v in checks.items()],
            "alignment": {"statement": ca.get("statement"), "value": ca.get("value"), "evidence": _ev(ca.get("evidence")), "limits": ca.get("limits") or []},
            "onset": A.get("onset"),
            "quantities": A.get("quantities"),
        },
        "explanations": {
            "hypotheses": [{"id": h["id"], "label": h["label"], "statement": h.get("statement"), "role": h["role"], "status": h["status"],
                            "status_reason": h.get("status_reason"), "grade": (h.get("plausibility") or {}).get("grade"),
                            "record_evidence": [{k: e.get(k) for k in ("claim", "direction")} for e in (h.get("plausibility") or {}).get("record_evidence", [])],
                            "missing": h.get("missing") or []}
                           for h in A.get("hypotheses") or []],
            "leading": A.get("leading"),
            "conditional_leading": A.get("conditional_leading"),
        },
        "next": {
            "safety_holds": plan.get("safety_holds") or [],
            "recommended": [{k: r.get(k) for k in ("id", "title", "kind", "hv_state", "distinguishes_labels", "predictions")} for r in (plan.get("recommended") or [])[:4]],
        },
        "series": {"window": "fault", "trigger_t": (cs.get("value") or {}).get("trigger_t"), "open_t": (cs.get("value") or {}).get("open_t"),
                   "lines": _series(rec, "fault")},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    body = json.dumps(build(), indent=1, ensure_ascii=False, default=str, sort_keys=True) + "\n"
    if a.check:
        ok = OUT.exists() and OUT.read_text() == body
        print("up to date" if ok else f"{OUT} is out of date: run scripts/demo/make_system_investigation.py")
        sys.exit(0 if ok else 1)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(body)
    print("wrote", OUT, len(body), "bytes")


if __name__ == "__main__":
    main()
