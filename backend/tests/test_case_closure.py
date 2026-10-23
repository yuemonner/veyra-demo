"""Case closure: the engineer's confirmed conclusion, reused by later cases on the same machine."""
import json
from datetime import datetime, timezone

import pytest

from app.ingest.profile import parse_profile
from app.ledger.model import LedgerError
from app.store.sqlite_store import CaseStore

PROFILE = parse_profile({"profile_id": "t", "version": 1, "format": "jsonl", "synthetic": False,
                         "columns": {"timestamp": "t", "asset": "asset", "signal": "signal", "value": "value", "unit": "unit", "scope": "scope"},
                         "signal_map": {"rc_out": "actuator.output"}, "clock_domain": "c", "timestamp_format": "epoch", "epoch_unit": "ns"})
BASE = 1_700_000_000


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def _case(store, t0):
    content = "".join(json.dumps({"t": int((t0 + i * 0.3) * 1e9), "asset": "M1", "signal": "rc_out", "value": 1500 + i % 7, "unit": "us", "scope": "x"}) + "\n"
                      for i in range(80)).encode()
    c = store.create_case(machine_id="M1", title="t", actor="a", synthetic=False, symptom_start=iso(t0 + 10), symptom_end=iso(t0 + 20), symptom_source="test report")
    store.commit_import(c["id"], content, f"{t0}.jsonl", PROFILE, actor="a")
    store.investigate(c["id"], actor="a")
    return c["id"]


def test_closure_needs_proof_for_inspection_and_is_reused_by_later_cases(tmp_path):
    store = CaseStore(tmp_path / "e.db")
    first = _case(store, BASE)
    base = {"investigation_status": "cause_confirmed", "cause_domain": "vehicle_interface", "cause": "Servo linkage loose",
            "confirmed_by": "inspection", "resolution_status": "resolved"}
    with pytest.raises(LedgerError, match="needs the record that shows it"):
        store.append(first, "case_closure", base, actor="Engineer")
    closed = store.append(first, "case_closure", {**base, "evidence_note": "Technician report, photo in work order", "fix": "Linkage tightened",
                                                  "fix_held": "held", "decisive_check": "CHK-ONSITE-ACTUATOR", "hours_to_cause": 6,
                                                  "would_have_saved_time": "Check the linkage before the network"}, actor="Engineer")
    assert closed["payload"]["evidence_strength"] == "direct" and closed["payload"]["supersedes"] is None
    revised = store.append(first, "case_closure", {"investigation_status": "cause_probable", "cause_domain": "vehicle_interface", "cause": "Servo gear stripped",
                                                   "confirmed_by": "engineer_judgement", "resolution_status": "not_yet_repaired"}, actor="Engineer 2")
    assert revised["payload"]["supersedes"] == closed["id"] and revised["payload"]["evidence_strength"] == "judgement"
    later = _case(store, BASE + 5000)
    prior = store.machine_history("M1", exclude_case=later, synthetic=False)
    assert prior[0]["closure"]["cause"] == "Servo gear stripped" and prior[0]["closure"]["investigation_status"] == "cause_probable"
    episode = store.episodes(first)[0]
    assert [c["payload"]["cause"] for c in episode["hindsight"]["case_closures"]] == ["Servo linkage loose", "Servo gear stripped"]
    store.close()


def test_judgement_cannot_be_called_confirmed_and_cause_can_stay_unknown(tmp_path):
    store = CaseStore(tmp_path / "e.db")
    cid = _case(store, BASE)
    with pytest.raises(LedgerError, match="supports 'probable'"):
        store.append(cid, "case_closure", {"investigation_status": "cause_confirmed", "cause_domain": "network", "cause": "Router",
                                           "confirmed_by": "engineer_judgement", "resolution_status": "resolved"}, actor="Engineer")
    unknown = store.append(cid, "case_closure", {"investigation_status": "cause_unknown", "resolution_status": "temporary_workaround",
                                                 "fix": "Restarted the controller; running again"}, actor="Engineer")
    assert unknown["payload"]["cause"] is None and unknown["payload"]["fix_held"] == "not_yet_known"
    with pytest.raises(LedgerError):
        store.append(cid, "case_closure", {"investigation_status": "cause_unknown", "resolution_status": "handed_over"}, actor="Engineer")
    store.append(cid, "case_closure", {"investigation_status": "not_reproduced", "resolution_status": "handed_over", "handed_over_to": "OEM support"}, actor="Engineer")
    store.close()


def test_closure_rejects_bad_values(tmp_path):
    store = CaseStore(tmp_path / "e.db")
    cid = _case(store, BASE)
    for bad in ({"cause_domain": "wheels"}, {"hours_to_cause": -1}, {"resolution_status": "maybe"}, {"cause": ""}, {"investigation_status": "solved"}):
        payload = {"investigation_status": "cause_probable", "cause_domain": "network", "cause": "Router dropped the link",
                   "confirmed_by": "engineer_judgement", "resolution_status": "resolved", **bad}
        with pytest.raises(LedgerError):
            store.append(cid, "case_closure", payload, actor="Engineer")
    store.close()


def _anomaly_rule(store, cid):
    run = store.latest_run(cid)
    for d, a in run["domains"].items():
        for o in a["observations"]:
            if o["kind"] == "anomaly" and o.get("key"):
                return d, o
    return None, None


def test_normal_for_this_machine_needs_a_second_person_then_applies_to_later_runs(tmp_path):
    store = CaseStore(tmp_path / "e.db")
    content = "".join(json.dumps({"t": int((BASE + i * 0.3) * 1e9), "asset": "M1", "signal": "rc_out", "value": 1500 + (i % 7) + (400 if 40 <= i < 60 else 0),
                                  "unit": "us", "scope": "x"}) + "\n" for i in range(120)).encode()
    c = store.create_case(machine_id="M1", title="t", actor="a", synthetic=False, symptom_start=iso(BASE + 10), symptom_end=iso(BASE + 20), symptom_source="test")
    store.commit_import(c["id"], content, "f.jsonl", PROFILE, actor="a")
    store.append(c["id"], "reference_declared", {"window_from": iso(BASE), "window_to": iso(BASE + 9), "basis": "test"}, actor="a")
    run = store.investigate(c["id"], actor="a")
    domain, obs = _anomaly_rule(store, c["id"])
    assert obs, "the fixture produces at least one anomaly"
    proposal = store.append(c["id"], "normal_proposal", {"run_id": run["id"], "observation_key": {"rule": obs["rule"], **obs["key"]},
                                                          "observation_text": obs["text"], "scope": "machine", "reason": "Expected on this machine"}, actor="Ana")
    assert store.approved_normals() == []
    with pytest.raises(LedgerError, match="different person"):
        store.append(c["id"], "normal_review", {"proposal_id": proposal["id"], "verdict": "approved", "reason": "ok"}, actor="ana")
    store.append(c["id"], "normal_review", {"proposal_id": proposal["id"], "verdict": "approved", "reason": "Seen on every run"}, actor="Ben")
    assert [n["approved_by"] for n in store.approved_normals()] == ["Ben"]
    again = store.investigate(c["id"], actor="a")
    same = [o for o in again["domains"][domain]["observations"] if o["rule"] == obs["rule"] and o.get("key") == obs["key"]]
    assert same and same[0]["kind"] == "normal" and same[0]["normal"]["approved_by"] == "Ben"
    store.close()
