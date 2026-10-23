"""Case Memory Test on SYNTHETIC excavator EX2: two related incidents.

Incident 1: the operator reports the boom reacting late. The engineer finds the cause (the LTE link: commands arrive
late, heartbeat gap, link fault code), acts (cell pinning), records the outcome and closes the case.
Incident 2, a week later, same machine, same complaint. One variant per test:
  - "repeat":    the same cause again (control: incident 1 should match and nothing important should differ);
  - "changed":   ONE KEY CONDITION CHANGED: the link is healthy, the boom itself responds late (vehicle-side delay
                 150 -> 450 ms in the same window);
  - "unrelated": the "changed" data, but the operator reports a different symptom (negative control: no shared
                 findings and no shared complaint, so incident 1 should NOT be shown).
For incident 2 the test asks: does Veyra find incident 1; does it say what differs; does it keep incident 1's cause
OUT of incident 2's own conclusions (hypotheses, triage, root cause identical to a run made before incident 1 was closed)?

Data: fixtures/synthetic/remote_delay/synthetic_ex2_* from scripts/make_synthetic_remote_delay.py. SYNTHETIC; not
field evidence.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from app.ingest.profile import parse_profile

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "synthetic" / "remote_delay"
MACHINE = "EX2"
FIRST = datetime(2026, 4, 8, 9, 0, 0, tzinfo=timezone.utc)
SECOND = datetime(2026, 4, 15, 9, 0, 0, tzinfo=timezone.utc)
ENGINEER, TECH = "Field engineer (synthetic)", "Network engineer (synthetic)"
COMPLAINT = "Boom reacts late to the joystick"
PROFILES = (("console.csv", "synthetic_console_profile.json"), ("vehicle.jsonl", "synthetic_vehicle_profile.json"),
            ("vehicle_io.csv", "synthetic_vehicle_io_profile.json"))
VARIANTS = ("repeat", "changed", "unrelated")


def _iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


def _ensure_context(store) -> None:
    attrs = store.context(MACHINE).get("attributes") or {}
    if (attrs.get("machine_class") or {}).get("value") != "remote_excavator":
        store.assert_context({"machine_id": MACHINE, "key": "machine_class", "value": "remote_excavator", "status": "engineer_confirmed", "actor": ENGINEER,
                              "source": "Synthetic: machine plate photo", "effective_from": "2026-01-01T00:00:00Z", "evidence": ["synthetic-photo-002"]})


def _confirm_reported_context(store) -> None:
    attrs = store.context(MACHINE)["attributes"]
    for key in ("oem", "model", "kit_version", "control_version", "firmware_version", "vehicle_interface"):
        if key in attrs and attrs[key].get("status") != "engineer_confirmed":
            store.assert_context({"machine_id": MACHINE, "key": key, "value": attrs[key]["value"], "status": "engineer_confirmed", "actor": ENGINEER,
                                  "source": "Synthetic: kit inventory photo", "effective_from": "2026-03-01T00:00:00Z", "evidence": ["synthetic-photo-002"]})


def open_case(store, session: str, start: datetime, title: str, symptom: str = "late_response", complaint: str = COMPLAINT) -> str:
    _ensure_context(store)
    cid = store.create_case(machine_id=MACHINE, title=title, actor=ENGINEER, synthetic=True,
                            symptom_start=_iso(start + timedelta(minutes=6)), symptom_end=_iso(start + timedelta(minutes=7, seconds=30)),
                            symptom_source=f"Synthetic operator report: '{complaint}'")["id"]
    store.append(cid, "intervention_report", {"occurred_at": _iso(start + timedelta(minutes=6, seconds=40)), "intervention_type": "pause",
                                              "operator": "Operator C (synthetic)", "stated_reason": complaint, "symptom": symptom, "affected_function": "boom",
                                              "reported_via": "Radio to site supervisor (synthetic)"}, actor=ENGINEER, source="synthetic site report")
    for suffix, profile in PROFILES:
        name = f"synthetic_ex2_{session}_{suffix}"
        store.commit_import(cid, (FIXTURES / name).read_bytes(), name, parse_profile(json.loads((FIXTURES / profile).read_text())), actor=ENGINEER)
    store.append(cid, "reference_declared", {"window_from": _iso(start), "window_to": _iso(start + timedelta(minutes=5, seconds=30)),
                                             "basis": "Normal digging before the complaint (synthetic)", "reference_source": "Shift note (synthetic)"},
                 actor=ENGINEER, source="synthetic")
    _confirm_reported_context(store)
    return cid


def resolve_first(store, cid: str) -> Dict[str, Any]:
    """The engineer's work on incident 1: check, decision, action, outcome, closure (all synthetic, action simulated)."""
    run = store.investigate(cid, actor=ENGINEER)
    store.append(cid, "check_review", {"run_id": run["id"], "check_id": "CHK-NETWORK-LOGS", "verdict": "accepted",
                                       "reason": "Command timing points before vehicle receipt"}, actor=ENGINEER, source="synthetic")
    logs = store.append(cid, "check_execution", {"run_id": run["id"], "check_id": "CHK-NETWORK-LOGS", "status": "performed",
                                                 "executed_at": _iso(FIRST + timedelta(hours=1, minutes=40)), "performer": TECH,
                                                 "observations": "Synthetic: cell handover storm 09:05:50-09:07:40 on the machine's LTE cell",
                                                 "evidence_note": "Synthetic gateway log excerpt, ticket SYN-61"}, actor=ENGINEER, source="synthetic ticket")
    decision = store.append(cid, "decision", {"run_id": run["id"], "options_considered": ["Pin the machine to a fixed LTE cell and monitor", "Dispatch hydraulic technician"],
                                              "chosen_option": "Pin the machine to a fixed LTE cell and monitor",
                                              "rationale": "Commands arrive late before vehicle receipt; handover storm in the window; boom response itself unchanged"},
                            actor=ENGINEER, source="synthetic")
    action = store.append(cid, "action", {"decision_id": decision["id"], "action_type": "network_change", "description": "Synthetic: cell pinning applied on the vehicle router",
                                          "status": "simulated", "performed_at": _iso(FIRST + timedelta(hours=3)), "performer": TECH}, actor=ENGINEER, source="synthetic")
    outcome = store.append(cid, "outcome", {"action_id": action["id"], "result": "recovered", "observed_from": _iso(FIRST + timedelta(hours=3)),
                                            "observed_to": _iso(FIRST + timedelta(days=2)), "verification": "unverified",
                                            "observations": "Synthetic: no late response reported for two days"}, actor=ENGINEER, source="synthetic")
    store.append(cid, "case_closure", {"run_id": run["id"], "investigation_status": "cause_confirmed", "cause_domain": "network",
                                       "cause": "Synthetic: LTE cell handover storm; commands queued before reaching the machine",
                                       "confirmed_by": "fix_resolved_it", "evidence_entries": [logs["id"], outcome["id"]],
                                       "resolution_status": "resolved", "fix": "Machine pinned to the site's main LTE cell", "fix_held": "held",
                                       "decisive_check": "CHK-NETWORK-LOGS", "hours_to_cause": 2,
                                       "would_have_saved_time": "Synthetic: compare command timing before vs during the window first"}, actor=TECH, source="synthetic")
    return run


def own_conclusions(run: Dict[str, Any]) -> Dict[str, Any]:
    return {"hypotheses": [(h["domain"], h["engine_status"], h["column"]) for h in run["hypotheses"]],
            "triage": {k: run["triage"]["proposed"][k] for k in ("severity", "owner")}, "root_cause": run["root_cause"]}


def _flagged(run: Dict[str, Any]) -> List:
    return sorted({(d, o["rule"]) for d, a in run["domains"].items() for o in a["observations"] if o["kind"] in ("anomaly", "delay")})


def scenario(store, variant: str) -> Dict[str, Any]:
    first = open_case(store, "incident1", FIRST, "EX2 boom reacts late (SYNTHETIC, incident 1)")
    unrelated = variant == "unrelated"
    second = open_case(store, "incident2_repeat" if variant == "repeat" else "incident2_changed", SECOND, f"EX2 incident 2 (SYNTHETIC, {variant})",
                       symptom="stopped_unexpectedly" if unrelated else "late_response",
                       complaint="Boom stopped by itself mid-swing" if unrelated else COMPLAINT)
    before = store.investigate(second, actor=ENGINEER)          # incident 2 before anyone has closed incident 1
    first_run = resolve_first(store, first)
    after = store.investigate(second, actor=ENGINEER)
    m = after["precedents"]["matches"]
    top = m[0] if m else None
    keys = ("match_basis", "similarity", "same_reported_symptom", "parts_then", "parts_now", "evidence_points_elsewhere", "earlier_parts_now",
            "same", "only_now", "only_then", "check_not_moved_up")
    r = {"variant": variant, "incident_1_flags": _flagged(first_run), "incident_2_flags": _flagged(after),
         "found_incident_1": bool(top and top["case_id"] == first),
         "precedent": None if not top else {k: top[k] for k in keys if k in top} | {
             "earlier_cause": top["closure"]["cause"], "status": top["closure"]["investigation_status"],
             "evidence_strength": top["closure"]["evidence_strength"], "resolution": top["closure"]["resolution_status"]},
         "next_checks_before": [c["check_id"] for c in before["checks"][:4]],
         "next_checks_after": [(c["check_id"], c["trigger"]) for c in after["checks"][:4]],
         "own_conclusions": own_conclusions(after), "own_conclusions_unchanged": own_conclusions(before) == own_conclusions(after)}
    r["criteria"] = criteria(variant, r)
    r["passed"] = all(r["criteria"].values())
    return r


def criteria(variant: str, r: Dict[str, Any]) -> Dict[str, bool]:
    """Pass/fail criteria, fixed before the run. Each must hold."""
    p = r["precedent"] or {}
    top_after = [c[0] for c in r["next_checks_after"]]
    if variant == "unrelated":
        return {"incident 1 not shown (different complaint, no shared findings)": not r["found_incident_1"],
                "own conclusions unchanged": r["own_conclusions_unchanged"]}
    common = {"1. finds incident 1": r["found_incident_1"],
              "4. incident 1's cause not applied (hypotheses, triage, root cause unchanged)": r["own_conclusions_unchanged"],
              "root cause still unconfirmed": r["own_conclusions"]["root_cause"] == "unconfirmed"}
    if variant == "repeat":
        return {**common, "matched on findings and the reported symptom": p.get("match_basis") == "findings and reported symptom",
                "3. nothing differs": not p.get("only_now") and not p.get("only_then"),
                "2. the check that settled incident 1 is first in Next checks": top_after[:1] == ["CHK-NETWORK-LOGS"],
                "owner proposed from this case's evidence: network": r["own_conclusions"]["triage"]["owner"]["domains"] == ["network"]}
    return {**common, "matched on the reported symptom only": p.get("match_basis") == "reported symptom only",
            "3. says the evidence points elsewhere": p.get("evidence_points_elsewhere") is True,
            "3. says where incident 1 found its problem is quiet now": any(x["part"] == "Network" and x["status_now"] == "no_delay_observed_in_measured_part"
                                                                         for x in p.get("earlier_parts_now", [])),
            "3. lists what is only in this case": any("Machine body" in x for x in p.get("only_now", [])),
            "2. incident 1's check shown but NOT moved up": bool(p.get("check_not_moved_up")) and "CHK-NETWORK-LOGS" not in top_after,
            "Next checks identical to before incident 1 was closed": top_after == r["next_checks_before"],
            "owner proposed from this case's evidence: machine body": r["own_conclusions"]["triage"]["owner"]["domains"] == ["vehicle_body"]}


def build_case_memory_demo(store) -> Dict[str, Any]:
    """Hosted demo: incident 1 closed by the engineer, incident 2 ("changed": same complaint, link healthy) investigated."""
    first = open_case(store, "incident1", FIRST, "EX2 boom reacts late (SYNTHETIC, case memory test: incident 1)")
    resolve_first(store, first)
    second = open_case(store, "incident2_changed", SECOND, "EX2 boom reacts late again, a week later (SYNTHETIC, case memory test: incident 2)")
    store.investigate(second, actor=ENGINEER)
    return {"incident_1": first, "incident_2": second, "synthetic": True}
