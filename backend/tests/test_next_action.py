"""Next action: investigate -> one or two discriminating checks (approved, done by the site) -> dispatch readiness. SYNTHETIC."""
import pytest

from app.engine import next_action as rules
from app.workflows import excavator_demo, next_action
from app.ledger.model import LedgerError
from app.store.sqlite_store import CaseStore, StoreError


@pytest.fixture
def case(tmp_path):
    store = CaseStore(tmp_path / "engine.db")
    yield store, excavator_demo.seed(store)
    store.close()


def _approve_and_report(store, cid, run_id, check_id, outcome, observations):
    store.append(cid, "check_review", {"run_id": run_id, "check_id": check_id, "verdict": "accepted", "reason": "Splits network vs controller"},
                 actor="Service lead", source="test")
    store.append(cid, "check_execution", {"run_id": run_id, "check_id": check_id, "status": "performed", "outcome": outcome,
                                          "executed_at": "2026-03-04T10:40:00Z", "performer": "Site supervisor", "observations": observations,
                                          "evidence_note": "Synthetic log export, ticket SYN-43"}, actor="Site supervisor", source="test")


def test_step1_names_what_is_supported_and_what_cannot_be_told_apart(case):
    store, cid = case
    st = next_action.state(store, cid)
    assert st["step"] == 1
    assert {s["part"] for s in st["assessment"]["supported"]} == {"network", "control_suite"}
    assert [c["part"] for c in st["assessment"]["cannot_tell_apart"]] == ["network", "control_suite"]
    assert {x["part"] for x in st["assessment"]["less_likely"]} == {"vehicle_interface", "vehicle_body"}


def test_step2_proposes_at_most_two_checks_that_split_the_candidates(case):
    store, cid = case
    st = next_action.make_plan(store, cid)
    assert st["step"] == 2 and [c["check_id"] for c in st["checks"]] == ["CHK-CONTROLLER-LOGS", "CHK-PROBE-PATH"]
    first = st["checks"][0]
    assert first["informs"] == ["Remote-control software", "Network"] and len(first["could_change"]) == 2   # "nothing recorded" weakens both: no change
    assert any("Network more likely" in o["meaning"] for o in first["outcomes"]) and any("Remote-control software more likely" in o["meaning"] for o in first["outcomes"])
    plan = [e for e in store.ledger(cid) if e["kind"] == "next_action" and e["payload"]["stage"] == "plan"][-1]["payload"]
    assert [r["check_id"] for r in plan["comparison"]][:3] == ["CHK-CONTROLLER-LOGS", "CHK-PROBE-PATH", "CHK-NETWORK-LOGS"]
    assert all(r["ranking_basis"] and r["settles_note"] for r in plan["comparison"])
    assert all(c["approval"] is None and c["result"] is None for c in st["checks"])
    assert next_action.make_plan(store, cid)["plan_entry"] == st["plan_entry"]  # idempotent per run


def test_site_result_needs_approval_and_a_named_outcome(case):
    store, cid = case
    st = next_action.make_plan(store, cid)
    with pytest.raises((LedgerError, StoreError), match="Accept or edit"):
        store.append(cid, "check_execution", {"run_id": st["run_id"], "check_id": "CHK-CONTROLLER-LOGS", "status": "performed", "outcome": "external_trigger",
                                              "executed_at": "2026-03-04T10:40:00Z", "performer": "x", "observations": "y", "evidence_note": "z"}, actor="x", source="t")
    store.append(cid, "check_review", {"run_id": st["run_id"], "check_id": "CHK-CONTROLLER-LOGS", "verdict": "accepted", "reason": "r"}, actor="Lead", source="t")
    with pytest.raises((LedgerError, StoreError), match="outcome"):
        store.append(cid, "check_execution", {"run_id": st["run_id"], "check_id": "CHK-CONTROLLER-LOGS", "status": "performed", "outcome": "it_was_the_network",
                                              "executed_at": "2026-03-04T10:40:00Z", "performer": "x", "observations": "y", "evidence_note": "z"}, actor="x", source="t")
    with pytest.raises(ValueError, match="site result"):
        next_action.make_report(store, cid)


def test_step3_link_cause_means_fix_remotely_and_no_truck(case):
    store, cid = case
    st = next_action.make_plan(store, cid)
    _approve_and_report(store, cid, st["run_id"], "CHK-CONTROLLER-LOGS", "external_trigger", "Restart logged 'link supervision timeout' after the link dropped")
    report = next_action.make_report(store, cid)["report"]
    assert report["status"] == "fix_remotely_first" and report["leading"] == "Network"
    cannot = [c["text"] for c in report["cannot_do_yet"]]
    assert "Send a technician to the machine" in cannot and "Name a failed part" in cannot
    assert any("ticket" not in w["text"] and "Site result for CHK-CONTROLLER-LOGS" in w["text"] for w in report["why"])
    assert report["checks_pending"] == ["CHK-PROBE-PATH"] and "dispatch approval" in report["boundary"]
    assert [e["kind"] for e in store.ledger(cid)][-1] == "next_action"


def test_step3_controller_cause_and_conflicting_results(case):
    store, cid = case
    st = next_action.make_plan(store, cid)
    _approve_and_report(store, cid, st["run_id"], "CHK-CONTROLLER-LOGS", "application_fault", "Stack overflow logged before the watchdog restart")
    report = next_action.make_report(store, cid)["report"]
    assert report["status"] == "fix_remotely_first" and report["leading"] == "Remote-control software"
    _approve_and_report(store, cid, st["run_id"], "CHK-NETWORK-LOGS", "network_event", "Handover storm on the machine's cell")
    report = next_action.make_report(store, cid)["report"]
    assert report["status"] == "hold_for_evidence" and report["leading"] is None  # results disagree: no decision forced


def test_nothing_to_split_means_no_plan():
    run = {"id": "r", "domains": {"network": {"status": "delay_observed", "observations": []}, "control_suite": {"status": "no_delay_observed_in_measured_part", "observations": []}},
           "hypotheses": [], "checks": []}
    a = rules.assess(run)
    assert a["cannot_tell_apart"] == [] and rules.plan(run, a) == []


def test_machine_generated_next_action_cannot_be_forged_through_the_human_ledger(case):
    store, cid = case
    run = store.latest_run(cid)
    with pytest.raises((LedgerError, StoreError), match="only be generated"):
        store.append(cid, "next_action", {"stage": "report", "run_id": run["id"], "status": "dispatch_candidate"}, actor="someone", source="t")


def test_screen_and_engine_agree_on_the_plan_rules():
    from pathlib import Path
    page = (Path(__file__).resolve().parents[2] / "frontend" / "app" / "field-case" / "page.tsx").read_text()
    assert f'const PLAN_POLICY = "{rules.POLICY}";' in page
