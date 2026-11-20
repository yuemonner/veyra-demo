"""Guided engineering replay: one case told in five sections, every claim tied to its basis.

  1 Establish the facts                  what was recorded, data integrity, the timeline
  2 Find the physical contradiction      Check A: SOC against measured charge; the energy shortfall kept apart
  3 Evaluate candidate causes            hypotheses with supporting, contradicting and missing evidence
  4 Explain the next decisive checks     what separates the open causes, safety first
  5 Turn the finding into a scenario     a reusable check, its review state, and its runs on other recordings

Each claim says what it rests on:
  evidence     a recorded value, with file + row
  analysis     a deterministic calculation over recorded values (formula and inputs shown)
  historical   an earlier report on this case: shown as history, never re-labelled as a result of this run
  assumption   stated, not measured

The pack also carries the full trace (every check result, digests, versions, manifest verification) and the AI
investigator's status for THIS run: not run, blocked (no owner permission), unavailable, or its constrained output.
Nothing is shown as an agent result unless an agent actually ran.
"""
from __future__ import annotations

import bisect
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.physchecks import expert_layer, base, facts as facts_mod, hypotheses, knowledge, outcomes as O, planner, scenarios as SC
from app.physchecks.profile import Profile
from app.physchecks.recording import Recording

REPLAY_VERSION = "guided-replay-v1"
CHART_ROLES = ["soc", "pack_current", "min_cell_voltage", "max_cell_voltage"]


def _claim(text: str, basis: str, refs: Optional[List[Dict[str, Any]]] = None, **extra) -> Dict[str, Any]:
    assert basis in ("evidence", "analysis", "historical", "assumption")
    return {"text": text, "basis": basis, "refs": (refs or [])[:6], **extra}


def _downsample(rec: Recording, profile: Profile, w: str, role: str, n: int = 450, keep: Optional[List[float]] = None) -> Dict[str, Any]:
    s = profile.series(rec, w, role)
    pts = s.samples
    if not pts:
        return {"role": role, "points": []}
    step = max(1, len(pts) // n)
    idx = set(range(0, len(pts), step)) | {len(pts) - 1}
    ts = [p.t for p in pts]
    for t in keep or []:
        i = bisect.bisect_left(ts, t)
        idx |= {max(0, i - 1), min(len(pts) - 1, i)}
    # keep every value change for slow state-like series (SOC) so steps are not hidden
    if role == "soc":
        idx |= {i for i in range(1, len(pts)) if pts[i].value != pts[i - 1].value}
    gap = profile.max_gap_s
    out = []
    for i in sorted(idx):
        if out and pts[i].t - out[-1]["t"] > max(gap * step * 1.5, gap * 3):
            out.append({"t": round(pts[i].t, 3), "v": None})        # break the line at gaps: nothing is drawn across them
        out.append({"t": round(pts[i].t, 3), "v": round(pts[i].value, 4)})
    return {"role": role, "field": s.field, "unit": s.unit, "file": rec.ref(w, s.field, pts[0].index).file, "points": out}


def _predicted_soc(rec: Recording, profile: Profile, w: str, cap: float, t_anchor: float, t_end: float) -> Dict[str, Any]:
    """Analysis series: SOC predicted from the anchor sample by measured charge at the stated capacity (no gap bridging)."""
    soc = profile.series(rec, w, "soc", "%").samples
    cur = profile.series(rec, w, "pack_current", "A").samples
    sgn = 1.0 if profile.role("pack_current").get("sign") == "discharge_positive" else -1.0
    a = next((s for s in soc if s.t >= t_anchor), None)
    if a is None:
        return {"points": []}
    pts, q, prev = [], 0.0, None
    stride = max(1, len(cur) // 450)
    for k, c in enumerate(cur):
        if c.t < a.t or c.t > t_end:
            continue
        if prev is not None:
            dt = c.t - prev.t
            if dt > profile.max_gap_s:
                pts.append({"t": round(c.t, 3), "v": None})
                break                                   # do not carry the prediction across a gap
            q += 0.5 * sgn * (c.value + prev.value) * dt
        if k % stride == 0:
            pts.append({"t": round(c.t, 3), "v": round(a.value - q / (cap * 3600) * 100, 3)})
        prev = c
    return {"role": "soc_predicted_by_measured_charge", "unit": "%", "anchor_t": round(a.t, 3), "anchor_soc": a.value, "points": pts,
            "basis": "analysis", "formula": "SOC(t) = SOC(anchor) - integral(I dt) / C x 100, trapezoid, stopped at the first gap"}


def build(rec: Recording, profile: Profile, meta: Dict[str, Any], *, params: Optional[Dict[str, Dict[str, Any]]] = None,
          ledger=None, qualification: str = "none", agent_run: Optional[Dict[str, Any]] = None,
          comparison_targets: Optional[List[Any]] = None, manifest_check: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    checks = base.run_all(rec, profile, params)
    F = facts_mod.derive(rec, profile, checks)
    verified = O.verified_for(ledger, meta["case_id"]) if ledger is not None else []
    A = hypotheses.assess(checks, F, verified, machine=hypotheses.machine_of(profile))
    expert_layer.enrich(rec, profile, checks, F, A, reported_symptom=(meta or {}).get("reported_symptom"))
    K = knowledge.for_case(ledger, meta["case_id"], checks, A, profile, rec.synthetic)
    prior = knowledge.prior_for_planner(K)
    plans = {q: planner.plan(A, checks, requester_qualification=q, prior=prior) for q in dict.fromkeys([qualification, "none", "hv_qualified"])}
    ca, ps, rc = checks["charge_accounting"], checks["protection_sequence"], checks["restart_eligibility"]

    # ---- 1 facts
    s1: List[Dict[str, Any]] = []
    for fid in ("stationary", "load", "cells_first_record", "cells_at_trip", "lowest_cell_index", "rest_rebound", "current_cross_check"):
        f = F.get(fid)
        if f:
            s1.append(_claim(f["statement"], "evidence" if not f["estimated"] else "analysis", f["evidence"], fact=fid))
    timeline = []
    for e in (ps.get("windows") or {}).get("escalations", {}).get("items", []):
        timeline.append({"t": e["t"], "window": e["window"], "what": f"BMS fault level {e['from']:g} -> {e['to']:g}", "refs": e["evidence"][:1], "basis": "evidence"})
    for sd in (rc.get("windows") or {}).get("protective_shutdowns", {}).get("items", []):
        timeline.append({"t": sd["t"], "window": sd["window"], "what": "HV contactors open (protective)", "refs": sd["evidence"][:1], "basis": "evidence"})
    for r in (rc.get("windows") or {}).get("reclosures", {}).get("items", []):
        timeline.append({"t": r["t"], "window": r["window"], "what": f"HV closed again (#{r['n']}); {r['reset']}", "refs": r["evidence"][:1], "basis": "evidence"})
    for w in (ca.get("windows") or {}).values():
        for ep in w.get("episodes") or []:
            timeline.append({"t": ep["t_start"], "window": ep["window"], "what": f"SOC {ep['soc_before_pct']} % -> {ep['soc_after_pct']} % in {ep['duration_s']} s "
                                                                                f"with {ep['measured_charge_ah']} Ah measured", "basis": "analysis", "refs": []})
    order = {w: i for i, w in enumerate(rec.windows)}
    timeline.sort(key=lambda x: (order.get(x["window"], 0), x["t"]))
    integrity = {"manifest": manifest_check, "windows": [{"window": w, "span": rec.span(w)} for w in rec.windows],
                 "excluded_files": meta.get("excluded_files") or {}, "loader": rec.provenance.get("loader"), "recording_digest": rec.digest()}

    # ---- 2 contradiction
    s2: List[Dict[str, Any]] = []
    for f in ca["findings"]:
        if f["kind"] in ("unexplained_soc_correction", "declared_recalibration", "soc_change_across_gap", "polarity_suspect"):
            s2.append(_claim(f["statement"], "analysis", f["evidence"], finding=f["kind"], values=f["values"]))
    if F.get("correction_context"):
        c = F["correction_context"]
        s2.append(_claim(c["statement"], "evidence", c["evidence"], fact="correction_context", values=c["value"]))
    for f in [x for x in ca["findings"] if x["kind"] == "soc_tracks_measured_charge"][:4]:
        s2.append(_claim(f["statement"], "analysis", f["evidence"], finding=f["kind"]))
    if F.get("energy_shortfall"):
        e = F["energy_shortfall"]
        s2.append(_claim(e["statement"], "analysis", e["evidence"], fact="energy_shortfall", assumptions=e["assumptions"], estimated=e["estimated"]))
    charts = []
    cap = (ca.get("parameters_used") or {}).get("capacity_ah")
    for w in rec.windows:
        eps = (ca["windows"].get(w) or {}).get("episodes") or []
        keep = [x for ep in eps for x in (ep["t_start"], ep["t_end"])]
        panel = {"window": w, "series": [_downsample(rec, profile, w, r, keep=keep) for r in CHART_ROLES if profile.has_role(r)],
                 "episodes": [{"from": ep["t_start"], "to": ep["t_end"], "label": f"{abs(ep['unexplained_pp'])} pp unexplained"} for ep in eps],
                 "markers": [{"t": x["t"], "label": x["what"]} for x in timeline if x["window"] == w and x["basis"] == "evidence"][:14]}
        spans = (ca["windows"].get(w) or {}).get("tracking_spans") or []
        if cap and spans and eps:
            # anchor at the continuous span that runs into the first correction, so the prediction is visible across it
            before = [sp for sp in spans if sp["t_start"] < eps[0]["t_start"]]
            anchor = before[-1]["t_start"] if before else spans[0]["t_start"]
            panel["predicted"] = _predicted_soc(rec, profile, w, cap, anchor, eps[-1]["t_end"] + 30)
        charts.append(panel)
    calcs = [c for c in ca["calculations"]][:6]

    # ---- 3 causes
    s3 = A

    # ---- 5 scenario
    finding_kind = next((f["kind"] for f in ca["findings"] if f["kind"] == "unexplained_soc_correction"), None)
    scen = {"proposal": None, "registered": [], "preview_runs": []}
    if finding_kind:
        scen["proposal"] = {"title": "Abrupt SOC correction not explained by measured charge", "check_id": "charge_accounting",
                            "check_version": ca["check_version"], "from_finding": finding_kind,
                            "applicability": {"required_roles": ["pack_current", "soc"], "machine_class": profile.data.get("machine_class")},
                            "params": {}, "status": "proposal (not yet created or reviewed)"}
    reg = [s for s in SC.list_all(ledger) if s["created_from"]["case_id"] == meta["case_id"]] if ledger is not None else []
    scen["registered"] = [{k: s[k] for k in ("id", "title", "status", "version", "author", "reviews", "history")} for s in reg]
    for s in reg:
        for trec, tprof in comparison_targets or []:
            scen["preview_runs"].append(SC.run(s, trec, tprof, preview=s["status"] != "approved"))
    if not reg and finding_kind and comparison_targets:
        tmp = {"id": "proposal", "version": 0, "title": scen["proposal"]["title"], "status": "draft", "check_id": "charge_accounting",
               "check_version": ca["check_version"], "params": {}, "applicability": scen["proposal"]["applicability"],
               "expect_outcome_on_match": ca["outcome"]}
        scen["preview_runs"] = [SC.run(tmp, trec, tprof, preview=True) for trec, tprof in comparison_targets]
    outcome_state = O.state(ledger, meta["case_id"]) if ledger is not None else None

    # ---- historical reports and reconciliation
    history = {"reports": meta.get("historical_reports") or [], "reconciliation": _reconcile(meta.get("historical_claims") or [], F, checks)}

    return {
        "version": REPLAY_VERSION, "built_at": datetime.now(timezone.utc).isoformat(),
        "case": {"id": meta["case_id"], "title": meta.get("title"), "synthetic": rec.synthetic, "confidential": bool(meta.get("confidential")),
                 "owner": meta.get("owner"), "windows": rec.windows, "profile": profile.id},
        "banner": ("SYNTHETIC case: generated data for demonstrating the workflow. Not any customer's investigation." if rec.synthetic else
                   "CONFIDENTIAL customer case. Local playback only: do not publish, upload or share outside the authorised team."),
        "sections": [
            {"n": 1, "id": "facts", "title": "Establish the facts", "claims": s1, "timeline": timeline, "integrity": integrity},
            {"n": 2, "id": "contradiction", "title": "Find the physical contradiction", "claims": s2, "charts": charts, "calculations": calcs,
             "quantities": A.get("quantities"), "check_outcome": ca["outcome"], "check_summary": ca["summary"]},
            {"n": 3, "id": "causes", "title": "Evaluate candidate causes and counterevidence", "assessment": s3, "knowledge": K},
            {"n": 4, "id": "next", "title": "Explain the next decisive checks", "plans": plans, "default_qualification": qualification},
            {"n": 5, "id": "scenario", "title": "Turn the finding into a reusable engineering scenario", "scenario": scen, "outcomes": outcome_state},
        ],
        "history": history,
        "agent": agent_run or {"mode": "not_run", "reason": "No AI investigator run is part of this replay. Everything above is deterministic."},
        "trace": {"checks": checks, "facts": F, "engine": base.ENGINE_VERSION, "specs": [c.spec.as_dict() for c in base.all_checks()],
                  "params": params or {}, "assumptions": sorted({a for r in checks.values() for a in r["assumptions"]})},
    }


def _get(path: str, F: Dict[str, Any], checks: Dict[str, Any]) -> Any:
    root, _, rest = path.partition(":")
    cur: Any = F if root == "fact" else checks if root == "check" else None
    for part in rest.split("."):
        if cur is None:
            return None
        if part == "#len":
            return len(cur) if isinstance(cur, (list, dict)) else None
        if part.startswith("kind=") and isinstance(cur, list):
            cur = next((x for x in cur if isinstance(x, dict) and x.get("kind") == part[5:]), None)
            continue
        if part.startswith("abs") and part == "abs":
            return abs(cur) if isinstance(cur, (int, float)) else None
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            cur = cur.get(part) if isinstance(cur, dict) else None
    return cur


def _reconcile(claims: List[Dict[str, Any]], F: Dict[str, Any], checks: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for c in claims:
        got = _get(c["compare"], F, checks) if c.get("compare") else None
        row = {"id": c["id"], "historical": c["text"], "historical_value": c.get("value"), "unit": c.get("unit", ""), "recomputed": got,
               "source": c.get("compare")}
        if got is None or c.get("value") is None:
            row["status"] = "not recomputed"
        else:
            try:
                row["difference"] = round(float(got) - float(c["value"]), 6)
                row["tolerance"] = c.get("tolerance", 0)
                row["status"] = "agrees" if abs(float(got) - float(c["value"])) <= float(c.get("tolerance", 0)) else "differs"
            except (TypeError, ValueError):
                row["status"] = "agrees" if str(got) == str(c["value"]) else "differs"
        out.append(row)
    return out
