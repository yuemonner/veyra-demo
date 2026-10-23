"""Decision Check: facts, decision-relevant gaps and explicit claims, kept apart. SYNTHETIC excavator case.

Network and controller both show evidence; hydraulics show no delay where measured. Checks that split network vs
controller: controller logs, router logs.
"""
import pytest

from app.workflows import excavator_demo, decision_check, next_action
from app.ledger.model import LedgerError
from app.store.sqlite_store import CaseStore, StoreError


@pytest.fixture
def case(tmp_path):
    store = CaseStore(tmp_path / "engine.db")
    yield store, excavator_demo.seed(store)
    store.close()


def _site(store, cid, check_id, outcome):
    run = store.latest_run(cid)
    store.append(cid, "check_review", {"run_id": run["id"], "check_id": check_id, "verdict": "accepted", "reason": "r"}, actor="Lead", source="t")
    store.append(cid, "check_execution", {"run_id": run["id"], "check_id": check_id, "status": "performed", "outcome": outcome, "executed_at": "2026-03-04T10:40:00Z",
                                          "performer": "Site", "observations": "o", "evidence_note": "ticket"}, actor="Site", source="t")


def _check(store, cid, **proposal):
    return decision_check.check(store, cid, {"urgent": False, "relies_on": [], **proposal}, actor="Engineer")["check"]["payload"]


def test_nothing_stated_still_finds_the_check_that_could_change_the_dispatch(case):
    store, cid = case
    out = _check(store, cid, action="dispatch", purpose="repair")
    assert out["claims"] == []                                              # nothing attributed to the engineer
    assert out["coverage"]["status"] == "partial" and out["alert"] == "notice"
    assert {g["check_id"] for g in out["gaps"]} >= {"CHK-CONTROLLER-LOGS"}
    assert out["next_check"]["check_id"] == "CHK-CONTROLLER-LOGS"
    assert any("Network" in f["text"] for f in out["facts"])               # facts shown, never as "you assume"


def test_safety_dispatch_without_a_root_cause_is_not_opposed(case):
    store, cid = case
    out = _check(store, cid, action="dispatch", purpose="safety_inspection", urgent=True)
    assert out["alert"] in ("quiet", "notice") and out["alert"] != "warning"
    assert out["gaps"] == [] and out["coverage"]["status"] == "none_needed" and out["claims"] == []


def test_engineer_right_is_quiet_once_the_deciding_checks_are_done(case):
    store, cid = case
    _site(store, cid, "CHK-CONTROLLER-LOGS", "external_trigger")
    _site(store, cid, "CHK-NETWORK-LOGS", "network_event")
    out = _check(store, cid, action="remote_fix", purpose="repair", target_part="network",
                 hypothesis={"part": "network", "provenance": "engineer_stated"})
    assert out["claims"][0]["verdict"] == "supported"
    assert out["coverage"]["status"] == "complete" and out["alert"] == "quiet"


def test_engineer_wrong_claim_is_contradicted_only_by_applicable_evidence(case):
    store, cid = case
    _site(store, cid, "CHK-CONTROLLER-LOGS", "external_trigger")          # the restart followed a lost link
    out = _check(store, cid, action="remote_fix", purpose="repair", target_part="control_suite",
                 hypothesis={"part": "control_suite", "provenance": "engineer_stated"})
    assert out["claims"][0]["verdict"] == "contradicted" and out["alert"] == "warning"
    assert "ledger" in out["claims"][0]["source"]


def test_nothing_found_is_never_a_contradiction(case):
    store, cid = case
    out = _check(store, cid, action="dispatch", purpose="repair", target_part="vehicle_body",
                 hypothesis={"part": "vehicle_body", "provenance": "engineer_stated"})
    assert out["claims"][0]["verdict"] == "insufficient"                    # no delay where measured != no fault
    assert out["alert"] == "warning" and "points to" in out["headline"]     # a fact about the evidence, not a verdict on the engineer


def test_relying_on_a_normal_measurement_to_rule_a_part_out_states_the_limit(case):
    store, cid = case
    run = store.latest_run(cid)
    seg = next(o for o in run["domains"]["vehicle_body"]["observations"] if o["rule"] == "R-SEGMENT")
    out = _check(store, cid, action="dispatch", purpose="repair", target_part="network",
                 relies_on=[{"domain": "vehicle_body", "rule": seg["rule"], "text": seg["text"], "use": "rules_out", "part": "vehicle_body", "provenance": "engineer_stated"}])
    claim = out["claims"][0]
    assert claim["verdict"] == "insufficient" and "does not show" in claim["why"]


def test_stale_data_is_not_assessed_never_a_pass(case):
    store, cid = case
    store.assert_context({"machine_id": "EX1", "key": "kit_version", "value": "synthetic-kit-9.9", "status": "engineer_confirmed", "actor": "t",
                          "source": "t", "effective_from": "2026-03-04T12:00:00Z", "evidence": ["e"]})
    out = _check(store, cid, action="dispatch", purpose="repair")
    assert out["alert"] == "not_assessed" and out["coverage"]["status"] == "not_assessed" and out["headline"].startswith("Not checked")


def test_failed_check_is_recorded_as_not_assessed(case, monkeypatch):
    store, cid = case
    monkeypatch.setattr(decision_check.rules, "facts", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out = _check(store, cid, action="dispatch", purpose="repair")
    assert out["alert"] == "not_assessed" and "failed" in out["headline"]


def test_prefill_provenance_is_kept_and_commit_links_proposal_and_check(case):
    store, cid = case
    out = decision_check.check(store, cid, {"action": "dispatch", "purpose": "diagnose", "urgent": False, "relies_on": [],
                                            "hypothesis": {"part": "network", "provenance": "system_suggested_confirmed"}}, actor="Engineer")
    assert out["proposal"]["payload"]["hypothesis"]["provenance"] == "system_suggested_confirmed"
    assert out["check"]["payload"]["claims"][0]["provenance"] == "system_suggested_confirmed"
    run = store.latest_run(cid)
    d = store.append(cid, "decision", {"run_id": run["id"], "options_considered": ["Dispatch network technician", "Remote fix first"],
                                       "chosen_option": "Remote fix first", "rationale": "Check found a remote check that could change it",
                                       "proposal_id": out["proposal"]["id"], "check_id": out["check"]["id"], "changed_from_proposal": True},
                     actor="Engineer", source="t")
    assert d["payload"]["changed_from_proposal"] is True and d["payload"]["check_alert"] == out["check"]["payload"]["alert"]
    assert d["payload"]["proceed_reason"] is None                           # optional, never forced


def test_checks_cannot_be_forged_and_parts_are_validated(case):
    store, cid = case
    run = store.latest_run(cid)
    with pytest.raises((LedgerError, StoreError), match="only be generated"):
        store.append(cid, "decision_check", {"run_id": run["id"], "alert": "quiet"}, actor="x", source="t")
    with pytest.raises((LedgerError, StoreError), match="target_part"):
        decision_check.check(store, cid, {"action": "dispatch", "purpose": "repair", "target_part": "gearbox"}, actor="x")


def test_suggestion_is_labelled_as_veyras(case):
    store, cid = case
    st = next_action.make_plan(store, cid)
    _site(store, cid, "CHK-CONTROLLER-LOGS", "external_trigger")
    next_action.make_report(store, cid)
    s = decision_check.suggest(store, cid)
    assert s["suggested_hypothesis"] == "network" and s["suggested_basis"] == "Next action report"
    assert st["step"] == 2


def test_dispatch_with_no_part_named_is_told_the_evidence_points_to_a_remote_fix(case):
    store, cid = case
    _site(store, cid, "CHK-CONTROLLER-LOGS", "external_trigger")
    out = _check(store, cid, action="dispatch", purpose="repair")
    assert out["claims"] == [] and out["alert"] in ("notice", "warning")
    assert "can be changed remotely" in out["headline"]
    safety = _check(store, cid, action="dispatch", purpose="safety_inspection")
    assert safety["alert"] != "warning" and not any(f.get("points_elsewhere") for f in safety["facts"])
