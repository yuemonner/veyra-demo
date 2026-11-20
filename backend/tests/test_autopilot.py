"""Autopilot: incident -> investigation -> proposed check -> self-verification -> prepared request -> human approval ->
site result -> recommendation -> its verification -> human decision. SYNTHETIC excavator case."""
import pytest

from app.engine import decision_check as dc_rules
from app.workflows import autopilot, excavator_demo
from app.ledger.model import LedgerError
from app.store.sqlite_store import CaseStore, StoreError


@pytest.fixture
def case(tmp_path):
    store = CaseStore(tmp_path / "engine.db")
    yield store, excavator_demo.seed(store)
    store.close()


def _kinds(store, cid):
    return [e["kind"] for e in store.ledger(cid)]


def test_end_to_end_stops_only_where_a_person_is_needed(case):
    store, cid = case
    v = autopilot.advance(store, cid)
    asked = [c["check_id"] for c in v["checks"]]
    assert v["stage"] == "awaiting_approval" and asked == ["CHK-CONTROLLER-LOGS", "CHK-PROBE-PATH"]      # one request, both checks
    claims = v["verification"]["CHK-CONTROLLER-LOGS"]["claims"]
    assert all(c["verdict"] == "supported" for c in claims) and not any("separates" in c["claim"] for c in claims)
    assert "Needs permission" in v["verification"]["CHK-CONTROLLER-LOGS"]["permission"][0]
    body = v["request"]["body"]
    assert "watchdog_restart" in body and "Something else" in body and "ticket" in body                   # the restart is cited because EX1 recorded it
    assert v["done_when"]["result"]["CHK-CONTROLLER-LOGS"] == ["controller_action_first", "link_loss_first", "nothing_before"]
    assert "Nothing has been sent" in v["say"]
    n = len(store.ledger(cid))
    assert autopilot.advance(store, cid)["stage"] == "awaiting_approval" and len(store.ledger(cid)) == n   # idempotent
    reviews = [e for e in store.ledger(cid) if e["kind"] == "check_review"]
    assert [e["payload"]["check_id"] for e in reviews] == ["CHK-SOURCE-ROWS"]                               # Veyra only runs read-only checks itself
    assert "decision_proposal" not in _kinds(store, cid) and "decision_check" not in _kinds(store, cid)     # self-checks are internal records
    assert v["already_done"][0]["check_id"] == "CHK-SOURCE-ROWS" and v["already_done"][0]["count"] > 0

    run = store.latest_run(cid)
    for check_id in asked:
        store.append(cid, "check_review", {"run_id": run["id"], "check_id": check_id, "verdict": "accepted", "reason": "Draft approved"}, actor="Lead", source="t")
    v = autopilot.advance(store, cid)
    assert v["stage"] == "approved_not_sent" and "has not been sent" in v["say"] and v["sent"] is None   # approving is not sending
    store.append(cid, "request_sent", {"run_id": run["id"], "check_ids": asked, "channel": "email", "sent_to": "Site supervisor",
                                       "sent_at": "2026-03-04T10:00:00Z"}, actor="Lead", source="t")
    v = autopilot.advance(store, cid)
    assert v["stage"] == "awaiting_result" and v["sent"]["channel"] == "email" and "0 of 2 answers" in v["say"]
    store.append(cid, "check_execution", {"run_id": run["id"], "check_id": "CHK-CONTROLLER-LOGS", "status": "performed", "outcome": "link_loss_first",
                                          "executed_at": "2026-03-04T10:40:00Z", "performer": "Site", "observations": "link supervision timeout",
                                          "evidence_note": "ticket 43"}, actor="Site", source="t")
    assert autopilot.advance(store, cid)["stage"] == "awaiting_result"                                       # still waiting for the second answer
    store.append(cid, "check_execution", {"run_id": run["id"], "check_id": "CHK-PROBE-PATH", "status": "performed", "outcome": "same_path",
                                          "executed_at": "2026-03-04T10:45:00Z", "performer": "Site", "observations": "probe on the command path",
                                          "evidence_note": "ticket 43"}, actor="Site", source="t")
    v = autopilot.advance(store, cid)
    assert v["stage"] == "recommend" and v["report"]["status"] == "fix_remotely_first"
    rec = v["recommendation"]
    assert rec["action"] == "remote_fix" and rec["part"] == "network" and rec["claims"][0]["provenance"] == "agent_proposed"
    assert "decision" not in _kinds(store, cid)                                                              # the decision stays with a person
    store.append(cid, "decision", {"run_id": run["id"], "options_considered": ["Fix remotely: Network", "Send someone"], "chosen_option": "Fix remotely: Network",
                                   "rationale": "Veyra's recommendation, checked", "veyra_check_id": rec["check_entry"]}, actor="Lead", source="t")
    assert autopilot.view(store, cid)["stage"] == "decided"
    decision = [e for e in store.ledger(cid) if e["kind"] == "decision"][-1]
    assert rec["check_entry"] in decision["links"]
    episode = store.episodes(cid)[-1]
    assert episode["human"]["decisions"] and episode["requests_sent"] and episode["agent_internal"]["count"] > 0
    assert episode["human"]["proposals"] == []                                                              # Veyra's attempts are not business proposals


def test_sending_needs_an_approved_draft_and_veyra_cannot_record_it(case):
    store, cid = case
    v = autopilot.advance(store, cid)
    run = store.latest_run(cid)
    with pytest.raises((LedgerError, StoreError), match="Approve"):
        store.append(cid, "request_sent", {"run_id": run["id"], "check_ids": ["CHK-CONTROLLER-LOGS"], "channel": "email", "sent_to": "x"}, actor="L", source="t")
    with pytest.raises(StoreError):
        store.append_trusted(cid, "request_sent", {"run_id": run["id"], "check_ids": ["CHK-CONTROLLER-LOGS"]}, actor="Veyra", source="t")


def test_concurrent_advances_write_each_record_once(case):
    import threading
    store, cid = case
    errors = []
    def go():
        try:
            autopilot.advance(store, cid)
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    threads = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    led = store.ledger(cid)
    kinds = [e["kind"] for e in led]
    stages = [e["payload"].get("stage") for e in led if e["kind"] == "next_action"]
    assert not errors
    assert kinds.count("check_execution") == 1 and stages.count("plan") == 1 and stages.count("prepared") == 1
    fps = [e["payload"]["fingerprint"] for e in led if e["kind"] == "next_action" and e["payload"].get("stage") == "self_check"]
    assert len(fps) == len(set(fps))


def test_a_failed_self_check_is_not_repeated_while_the_evidence_is_unchanged(case, monkeypatch):
    store, cid = case
    monkeypatch.setattr(dc_rules, "check", lambda *a, **k: {"policy": "x", "coverage": {"status": "partial", "checks": []}, "facts": [], "gaps": [],
                                                             "prerequisites": [], "claims": [], "alert": "warning", "headline": "no", "next_check": None, "boundary": "b"})
    autopilot.advance(store, cid)
    n = len(store.ledger(cid))
    autopilot.advance(store, cid)
    autopilot.advance(store, cid)
    assert len(store.ledger(cid)) == n


def test_veyra_rejects_its_own_unsupported_proposals(case):
    store, cid = case
    run = store.latest_run(cid)
    ledger = store.ledger(cid)
    unknown = dc_rules.verify_remote_check(run, {"action": "remote_check", "check_id": "CHK-DOES-NOT-EXIST"}, ledger)
    assert not unknown["verified"] and unknown["claims"][0]["verdict"] == "contradicted"
    store.append(cid, "check_review", {"run_id": run["id"], "check_id": "CHK-CONTROLLER-LOGS", "verdict": "accepted", "reason": "r"}, actor="L", source="t")
    store.append(cid, "check_execution", {"run_id": run["id"], "check_id": "CHK-CONTROLLER-LOGS", "status": "performed", "outcome": "external_trigger",
                                          "executed_at": "2026-03-04T10:40:00Z", "performer": "S", "observations": "o", "evidence_note": "t"}, actor="S", source="t")
    done = dc_rules.verify_remote_check(run, {"action": "remote_check", "check_id": "CHK-CONTROLLER-LOGS"}, store.ledger(cid))
    assert not done["verified"] and any("already recorded" in c["why"] for c in done["claims"])
    pointless = dc_rules.verify_remote_check(run, {"action": "remote_check", "check_id": "CHK-PROBE-PATH"}, store.ledger(cid))
    assert not pointless["verified"] and any(c["verdict"] == "insufficient" for c in pointless["claims"])    # can't change the trip any more


def test_absence_of_evidence_is_not_a_contradiction_in_veyras_own_check(case):
    store, cid = case
    run = store.latest_run(cid)
    for part, expected in (("network", "supported"), ("vehicle_body", "insufficient")):
        out = dc_rules.check(run, {"action": "remote_fix", "purpose": "repair", "target_part": part, "hypothesis": {"part": part, "provenance": "agent_proposed"}},
                             store.ledger(cid))
        assert out["claims"][0]["verdict"] == expected


def test_missing_or_changed_evidence_writes_nothing(tmp_path):
    store = CaseStore(tmp_path / "e.db")
    empty = store.create_case(machine_id="EX9", title="t", actor="a", synthetic=True)
    assert autopilot.advance(store, empty["id"])["stage"] == "waiting_for_data"
    cid = excavator_demo.seed(store)
    store.assert_context({"machine_id": "EX1", "key": "kit_version", "value": "synthetic-kit-9.9", "status": "engineer_confirmed", "actor": "t",
                          "source": "t", "effective_from": "2026-03-04T12:00:00Z", "evidence": ["e"]})
    n = len(store.ledger(cid))
    assert autopilot.advance(store, cid)["stage"] == "stale" and len(store.ledger(cid)) == n
    store.close()


def test_verifier_failure_never_counts_as_verified(case, monkeypatch):
    store, cid = case
    monkeypatch.setattr(dc_rules, "check", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")) if not k.get("unavailable") else
                        {"policy": "x", "coverage": {"status": "not_assessed", "checks": []}, "facts": [], "gaps": [], "prerequisites": [], "claims": [],
                         "alert": "not_assessed", "headline": "Not checked: " + k["unavailable"], "next_check": None, "boundary": "b"})
    v = autopilot.advance(store, cid)
    assert v["stage"] == "no_request"
    assert all(not c.get("verified") for c in v["self_checks"]) and all(c["alert"] == "not_assessed" for c in v["self_checks"])
    assert not any(e["kind"] == "next_action" and e["payload"].get("stage") == "prepared" for e in store.ledger(cid))


def test_urgent_safety_dispatch_by_an_engineer_is_not_opposed(case):
    store, cid = case
    run = store.latest_run(cid)
    out = dc_rules.check(run, {"action": "dispatch", "purpose": "safety_inspection", "urgent": True}, store.ledger(cid))
    assert out["alert"] != "warning" and out["gaps"] == []


def test_a_person_cannot_file_a_proposal_as_veyras(case):
    store, cid = case
    run = store.latest_run(cid)
    with pytest.raises((LedgerError, StoreError)):
        store.append(cid, "decision_proposal", {"run_id": run["id"], "action": "remote_check", "purpose": "diagnose"}, actor="x", source="t")
    entry = store.append(cid, "decision_proposal", {"run_id": run["id"], "action": "dispatch", "purpose": "repair", "proposed_by": "veyra"}, actor="x", source="t")
    assert entry["payload"]["proposed_by"] == "engineer"
    with pytest.raises((LedgerError, StoreError)):
        store.append(cid, "decision_proposal", {"run_id": run["id"], "action": "dispatch", "purpose": "repair",
                                                "hypothesis": {"part": "network", "provenance": "agent_proposed"}}, actor="x", source="t")


def test_site_results_survive_a_new_investigation(case):
    store, cid = case
    run = store.latest_run(cid)
    store.append(cid, "check_review", {"run_id": run["id"], "check_id": "CHK-CONTROLLER-LOGS", "verdict": "accepted", "reason": "r"}, actor="L", source="t")
    store.append(cid, "check_execution", {"run_id": run["id"], "check_id": "CHK-CONTROLLER-LOGS", "status": "performed", "outcome": "external_trigger",   # old name, still read
                                          "executed_at": "2026-03-04T10:40:00Z", "performer": "S", "observations": "o", "evidence_note": "t"}, actor="S", source="t")
    store.investigate(cid, actor="Engineer")                     # re-investigated: a new run
    v = autopilot.advance(store, cid)
    assert v["stage"] == "recommend" and v["report"]["leading"] == "Network"
    assert any("from an earlier investigation" in w["source"] for w in v["report"]["why"])


def test_motion_of_the_whole_machine_is_an_effect_not_a_cause():
    from app.engine import next_action as na
    run = {"id": "r", "hypotheses": [], "checks": [], "domains": {
        "network": {"status": "not_observed", "observations": []},
        "control_suite": {"status": "not_observed", "observations": []},
        "vehicle_interface": {"status": "anomaly_observed", "observations": [{"kind": "anomaly", "rule": "R-TRACKING", "text": "LF_KFE stopped following", "key": None}]},
        "vehicle_body": {"status": "anomaly_observed", "observations": [{"kind": "anomaly", "rule": "R-REFERENCE", "text": "vehicle.velocity lower", "key": {"signal": "vehicle.velocity"}}]}}}
    a = na.assess(run)
    assert [x["part"] for x in a["effects"]] == ["vehicle_body"]
    assert [s["part"] for s in a["supported"]] == ["vehicle_interface"]
    assert {c["part"] for c in a["cannot_tell_apart"]} == {"vehicle_interface", "network", "control_suite"}   # unmeasured parts stay possible
    alone = {**run, "domains": {**run["domains"], "vehicle_interface": {"status": "no_delay_observed_in_measured_part", "observations": []}}}
    assert na.assess(alone)["effects"] == []     # with nothing else implicated, the motion change is still reported as evidence


def test_read_only_checks_run_once_even_if_asked_twice(case):
    store, cid = case
    autopilot.advance(store, cid)
    autopilot.advance(store, cid)
    run = store.latest_run(cid)
    again = store.execute_automatic(cid, "CHK-SOURCE-ROWS", actor="someone")
    assert again["already_done"] and again["result"]["count"] > 0
    runs = [e for e in store.ledger(cid) if e["kind"] == "check_execution" and e["payload"]["check_id"] == "CHK-SOURCE-ROWS" and e["payload"]["run_id"] == run["id"]]
    assert len(runs) == 1


def test_a_single_result_that_would_change_which_part_is_on_top_counts():
    from app.engine import decision_check as dc
    run = {"id": "r", "hypotheses": [], "checks": [{"check_id": "CHK-CONTROLLER-LOGS", "question": "q", "availability": {"state": "needs_authorization"}}],
           "domains": {"network": {"status": "not_observed", "observations": []}, "control_suite": {"status": "not_observed", "observations": []},
                       "vehicle_interface": {"status": "anomaly_observed", "observations": [{"kind": "anomaly", "rule": "R-TRACKING", "text": "t", "key": None}]},
                       "vehicle_body": {"status": "no_delay_observed_in_measured_part", "observations": []}}}
    out = dc.verify_remote_check(run, {"action": "remote_check", "check_id": "CHK-CONTROLLER-LOGS"}, [])
    assert out["verified"], out["claims"]


def test_no_site_request_when_nothing_was_reported_and_only_brief_events_were_found():
    run = {"interventions": [], "reported_symptoms": None, "domains": {
        "vehicle_body": {"observations": [{"rule": "R-BRIEF", "kind": "anomaly", "text": "1 brief event(s) on vehicle.attitude (pitch)"},
                                          {"rule": "R-REFERENCE-COMMANDED", "kind": "explained", "text": "the command changed, not the machine"}]}}}
    assert "won't ask the site" in autopilot.only_brief_events(run)
    assert autopilot.only_brief_events({**run, "interventions": [{"x": 1}]}) is None


def test_routine_events_are_not_presented_as_findings():
    run = {"id": "r", "findings": {"events": {"events": [
        {"time_utc": "t1", "signal": "controller.event", "event_value": "LF_HAA command mode 13 -> 1", "routine": "all 12 drives together", "import_id": "i", "row": 1},
        {"time_utc": "t2", "signal": "actuator.status", "event_value": "drive/LF_KFE: ERROR", "routine": None, "import_id": "i", "row": 2}]}}}
    assert [f["text"] for f in autopilot.observed(run)] == ["actuator.status recorded 'drive/LF_KFE: ERROR'"]


def test_a_draft_made_under_older_rules_is_not_acted_on(case):
    store, cid = case
    run = store.latest_run(cid)
    store.append_trusted(cid, "next_action", {"stage": "plan", "run_id": run["id"], "policy": "next-action-v1", "assessment": {}, "checks": []},
                         actor="Veyra next-action", source="t")
    v = autopilot.advance(store, cid)
    assert v["stage"] == "old_rules" and "Investigate again" in v["say"]
    store.investigate(cid, actor="Engineer")
    assert autopilot.advance(store, cid)["stage"] == "awaiting_approval"


def test_hosted_qd1_seed_verifies_and_drafts_without_presuming_events(tmp_path):
    from app.engine_seed import QD1_SEED, _copy_cases
    store = CaseStore(tmp_path / "h.db")
    [cid] = _copy_cases(store, QD1_SEED)
    check = store.verify(cid)
    assert check["ledger_chain"]["valid"] and check["imports_intact"] and check["runs_intact"] and store.case(cid)["synthetic"]
    v = autopilot.advance(store, cid)
    assert v["stage"] == "awaiting_approval" and {c["check_id"] for c in v["checks"]} == {"CHK-DRIVE-LOGS", "CHK-CONTROLLER-LOGS"}
    assert "restart" not in v["request"]["body"].lower()
    store.close()


def test_view_says_why_nothing_was_drafted_without_writing(tmp_path):
    store = CaseStore(tmp_path / "e.db")
    cid = excavator_demo.seed(store)
    store.assert_context({"machine_id": "EX1", "key": "kit_version", "value": "synthetic-kit-9.9", "status": "engineer_confirmed", "actor": "t",
                          "source": "t", "effective_from": "2026-03-04T12:00:00Z", "evidence": ["e"]})
    n = len(store.ledger(cid))
    assert autopilot.view(store, cid)["stage"] == "stale" and len(store.ledger(cid)) == n
    store.close()
