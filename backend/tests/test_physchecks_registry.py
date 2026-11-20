"""Scenario registry, cross-run scanner and verified outcomes. SYNTHETIC fixtures; throwaway ledgers."""
import copy

import pytest

from app.physchecks import base, facts, hypotheses, outcomes as O, scenarios as SC, synthetic as S
from app.physchecks.profile import Profile
from app.physchecks.store import Ledger, LedgerError


@pytest.fixture()
def led(tmp_path):
    l = Ledger(tmp_path / "eng.db")
    yield l
    l.close()


@pytest.fixture(scope="module")
def incident():
    rec, prof = S.incident(), S.profile()
    return rec, prof, base.run_all(rec, prof)


def _scenario(led, incident, approve=True):
    rec, prof, checks = incident
    s = SC.create_from_finding(led, case_id="SYN-CASE-1", check_result=checks["charge_accounting"], finding_kind="unexplained_soc_correction",
                               title="Abrupt SOC correction not explained by measured charge", author="Engineer A",
                               applicability={"required_roles": ["pack_current", "soc"], "machine_class": "battery_electric_vehicle"})
    if approve:
        s = SC.review(led, s["id"], reviewer="Engineer B", reviewer_kind="engineer", decision="approve",
                      notes="checked thresholds and the correction window on the source rows")
    return s


# ---------------------------------------------------------------- scenarios
def test_scenario_created_from_deterministic_finding_with_provenance(led, incident):
    s = _scenario(led, incident, approve=False)
    assert s["status"] == "draft" and s["version"] == 1 and s["author"] == "Engineer A"
    cf = s["created_from"]
    assert cf["case_id"] == "SYN-CASE-1" and cf["finding_kind"] == "unexplained_soc_correction" and len(cf["check_input_digest"]) == 64
    assert cf["source_synthetic"] is True


def test_scenario_cannot_come_from_agent_or_missing_finding(led, incident):
    _, _, checks = incident
    with pytest.raises(SC.ScenarioError):
        SC.create_from_finding(led, case_id="x", check_result=checks["charge_accounting"], finding_kind="unexplained_soc_correction",
                               title="t", author="bot", author_kind="agent")
    with pytest.raises(SC.ScenarioError):
        SC.create_from_finding(led, case_id="x", check_result=checks["charge_accounting"], finding_kind="nope", title="t", author="E")
    with pytest.raises(SC.ScenarioError):
        SC.create_from_finding(led, case_id="x", check_result={"check_id": "charge_accounting", "findings": []}, finding_kind="x", title="t", author="E")


def test_two_person_review_and_agent_cannot_approve(led, incident):
    s = _scenario(led, incident, approve=False)
    with pytest.raises(SC.ScenarioError):
        SC.review(led, s["id"], reviewer="Engineer A", reviewer_kind="engineer", decision="approve", notes="ok")
    with pytest.raises(SC.ScenarioError):
        SC.review(led, s["id"], reviewer="Veyra agent", reviewer_kind="agent", decision="approve", notes="ok")
    with pytest.raises(SC.ScenarioError):
        SC.review(led, s["id"], reviewer="Engineer B", reviewer_kind="engineer", decision="approve", notes="  ")
    assert SC.review(led, s["id"], reviewer="Engineer B", reviewer_kind="engineer", decision="approve", notes="checked")["status"] == "approved"


def test_draft_does_not_run_without_preview(led, incident):
    rec, prof, _ = incident
    s = _scenario(led, incident, approve=False)
    with pytest.raises(SC.ScenarioError):
        SC.run(s, rec, prof)
    r = SC.run(s, rec, prof, preview=True)
    assert r["reviewed"] is False and r["outcome"] == "VIOLATION"


def test_revision_requires_re_review(led, incident):
    s = _scenario(led, incident)
    s2 = SC.revise(led, s["id"], author="Engineer A", change="wider detection window", params={"detect_window_s": 20})
    assert s2["version"] == 2 and s2["status"] == "draft" and len(s2["history"]) == 2
    s3 = SC.review(led, s["id"], reviewer="Engineer C", reviewer_kind="engineer", decision="approve", notes="re-checked")
    assert s3["status"] == "approved" and s3["params"] == {"detect_window_s": 20}


def test_scenario_on_another_recording_with_evidence(led, incident):
    s = _scenario(led, incident)
    other = S.abrupt_correction("syn-other-vehicle", drop_pp=25.0)
    r = SC.run(s, other, S.profile())
    assert r["outcome"] == "VIOLATION" and r["matched_original"] and r["reviewed"]
    assert r["findings"] and r["findings"][0]["evidence"][0]["recording"] == "syn-other-vehicle"
    healthy = SC.run(s, S.healthy("syn-healthy-vehicle"), S.profile())
    assert healthy["outcome"] == "PASS" and not healthy["matched_original"]


def test_scenario_unavailable_signals_and_incompatible_configuration(led, incident):
    s = _scenario(led, incident)
    p = copy.deepcopy(S.PROFILE)
    del p["roles"]["soc"]
    r = SC.run(s, S.healthy(), Profile(p))
    assert r["outcome"] == "NOT_APPLICABLE" and "soc" in r["reasons"][0]
    p2 = copy.deepcopy(S.PROFILE)
    p2["machine_class"] = "quadruped_robot"
    assert SC.run(s, S.healthy(), Profile(p2))["outcome"] == "NOT_APPLICABLE"
    b = S.Builder("nosoc")
    for k in range(100):
        b.frame("w", k * 0.1, "BMS_I", i_pack=5.0)
    r3 = SC.run(s, b.done(), S.profile())
    assert r3["outcome"] == "INDETERMINATE" and r3["applicability"] == "signals_unavailable"


def test_cross_run_scanner(led, incident):
    _scenario(led, incident)
    targets = [(S.abrupt_correction("veh-1"), S.profile()), (S.healthy("veh-2"), S.profile()), (S.protection_trip("veh-3"), S.profile())]
    out = SC.scan(led, targets)
    assert out["recordings"] == ["veh-1", "veh-2", "veh-3"]
    by = {r["recording"]: r["outcome"] for r in out["results"]}
    assert by["veh-1"] == "VIOLATION" and by["veh-2"] == "PASS"
    assert len(led.records(kinds=["scan_result"])) == 3
    assert led.verify_chain()["ok"]


# ---------------------------------------------------------------- verified outcomes
def _chain(led, incident):
    _, _, checks = incident
    sig = O.signature_of(checks)
    O.open_case(led, "SYN-CASE-1", title="SYNTHETIC under-voltage incident", signature=sig, actor="Engineer A", confidential=False)
    for hid, origin, kind in (("A", "deterministic", "system"), ("B", "deterministic", "system"), ("D", "agent", "agent")):
        O.propose(led, "SYN-CASE-1", hypothesis_id=hid, statement=f"hypothesis {hid}", origin=origin, actor="veyra" if kind != "agent" else "agent", actor_kind=kind)
    rc = O.recommend(led, "SYN-CASE-1", template_id="TEST-CAPACITY", hypotheses=["A", "B", "D"], plan_version="next-decisive-check-v1")
    pc = O.performed(led, "SYN-CASE-1", template_id="TEST-CAPACITY", method="full charge, CC discharge, independent meter", performed_by="HV team",
                     qualification="hv_qualified", actor="Engineer A", actor_kind="engineer", recommended_id=rc["id"])
    return sig, pc


def test_full_chain_to_verified_root_cause_and_retrieval(led, incident):
    sig, pc = _chain(led, incident)
    assert O.state(led, "SYN-CASE-1")["hypotheses"]["B"]["stage"] == "proposed_diagnosis"
    O.review(led, "SYN-CASE-1", hypothesis_id="B", decision="accept_for_testing", notes="test it", actor="Engineer A", actor_kind="engineer")
    assert O.state(led, "SYN-CASE-1")["hypotheses"]["B"]["stage"] == "engineer_reviewed_hypothesis"
    ob = O.observe(led, "SYN-CASE-1", performed_id=pc["id"], summary="measured usable capacity 15 Ah of 100 Ah stated",
                   measurements={"capacity_ah": 15.0}, supports=["B"], contradicts=["A"], actor="Engineer A", actor_kind="engineer")
    assert O.state(led, "SYN-CASE-1")["hypotheses"]["B"]["stage"] == "observed_test_result"
    O.verify(led, "SYN-CASE-1", hypothesis_id="B", result="confirmed", observation_ids=[ob["id"]], basis="independent capacity test",
             actor="Engineer B", actor_kind="engineer")
    st = O.state(led, "SYN-CASE-1")
    assert st["hypotheses"]["B"]["stage"] == "verified_root_cause" and st["case_stage"] == "verified_root_cause"
    vf = O.verified_for(led, "SYN-CASE-1")
    assert vf == [{"id": vf[0]["id"], "hypothesis_id": "B", "result": "confirmed", "verified": True, "summary": "independent capacity test", "by": "Engineer B"}]
    kn = O.knowledge(led, sig, exclude_case="OTHER")
    assert [k["hypothesis_id"] for k in kn["trusted"]] == ["B"]
    assert {k["hypothesis_id"] for k in kn["unverified"]} == {"A", "D"} and all("not verified" in k["note"] for k in kn["unverified"])
    # the verified outcome feeds the next assessment
    rec, prof, checks = incident
    A2 = hypotheses.assess(checks, facts.derive(rec, prof, checks), vf, machine=hypotheses.machine_of(prof))
    assert next(h for h in A2["hypotheses"] if h["id"] == "B")["status"] == "confirmed"


def test_agent_can_never_review_verify_or_observe(led, incident):
    _, pc = _chain(led, incident)
    with pytest.raises(O.OutcomeError):
        O.review(led, "SYN-CASE-1", hypothesis_id="D", decision="accept_for_testing", notes="", actor="agent", actor_kind="agent")
    with pytest.raises(O.OutcomeError):
        O.observe(led, "SYN-CASE-1", performed_id=pc["id"], summary="s", measurements={}, supports=["D"], contradicts=[], actor="agent", actor_kind="agent")
    with pytest.raises(O.OutcomeError):
        O.verify(led, "SYN-CASE-1", hypothesis_id="D", result="confirmed", observation_ids=[], basis="agent says so", actor="agent", actor_kind="agent")
    st = O.state(led, "SYN-CASE-1")
    assert st["hypotheses"]["D"]["agent_only"] is True and st["hypotheses"]["D"]["stage"] == "proposed_diagnosis"


def test_verification_needs_matching_observation(led, incident):
    _, pc = _chain(led, incident)
    with pytest.raises(O.OutcomeError):
        O.verify(led, "SYN-CASE-1", hypothesis_id="B", result="confirmed", observation_ids=[], basis="b", actor="E", actor_kind="engineer")
    ob = O.observe(led, "SYN-CASE-1", performed_id=pc["id"], summary="capacity 98 Ah", measurements={"capacity_ah": 98}, supports=["A"],
                   contradicts=["B"], actor="E", actor_kind="engineer")
    with pytest.raises(O.OutcomeError):   # this observation contradicts B, it cannot confirm it
        O.verify(led, "SYN-CASE-1", hypothesis_id="B", result="confirmed", observation_ids=[ob["id"]], basis="b", actor="E", actor_kind="engineer")
    O.verify(led, "SYN-CASE-1", hypothesis_id="B", result="excluded", observation_ids=[ob["id"]], basis="capacity near stated", actor="E", actor_kind="engineer")
    assert O.state(led, "SYN-CASE-1")["hypotheses"]["B"]["stage"] == "excluded"
    with pytest.raises(O.OutcomeError):
        O.observe(led, "SYN-CASE-1", performed_id="rec-nope", summary="s", measurements={}, supports=["A"], contradicts=[], actor="E", actor_kind="engineer")
    with pytest.raises(O.OutcomeError):
        O.verify(led, "SYN-CASE-1", hypothesis_id="Z", result="not_settled", observation_ids=[], basis="b", actor="E", actor_kind="engineer")


def test_conflicting_evidence_blocks_verification_until_corrected(led, incident):
    _, pc = _chain(led, incident)
    o1 = O.observe(led, "SYN-CASE-1", performed_id=pc["id"], summary="capacity 15 Ah", measurements={"capacity_ah": 15}, supports=["B"],
                   contradicts=[], actor="E", actor_kind="engineer")
    o2 = O.observe(led, "SYN-CASE-1", performed_id=pc["id"], summary="repeat test 96 Ah", measurements={"capacity_ah": 96}, supports=[],
                   contradicts=["B"], actor="E", actor_kind="engineer")
    assert "B" in O.conflicts(led, "SYN-CASE-1")
    with pytest.raises(O.OutcomeError):
        O.verify(led, "SYN-CASE-1", hypothesis_id="B", result="confirmed", observation_ids=[o1["id"]], basis="b", actor="E2", actor_kind="engineer")
    # not_settled is always allowed
    O.verify(led, "SYN-CASE-1", hypothesis_id="B", result="not_settled", observation_ids=[], basis="conflicting tests", actor="E2", actor_kind="engineer")
    # the first test turns out to have used a miscalibrated meter: withdraw it with a reason
    O.correct(led, "SYN-CASE-1", record_id=o1["id"], reason="meter calibration expired; result void", replacement=None, actor="E2", actor_kind="engineer")
    assert O.conflicts(led, "SYN-CASE-1") == {}
    with pytest.raises(O.OutcomeError):   # the withdrawn observation can no longer be cited
        O.verify(led, "SYN-CASE-1", hypothesis_id="B", result="confirmed", observation_ids=[o1["id"]], basis="b", actor="E2", actor_kind="engineer")
    O.verify(led, "SYN-CASE-1", hypothesis_id="B", result="excluded", observation_ids=[o2["id"]], basis="valid repeat test", actor="E2", actor_kind="engineer")
    st = O.state(led, "SYN-CASE-1")
    assert st["hypotheses"]["B"]["stage"] == "excluded"
    assert any(h["kind"] == "correction" for h in st["history"])     # history kept


def test_revised_outcome_supersedes_and_keeps_history(led, incident):
    _, pc = _chain(led, incident)
    ob = O.observe(led, "SYN-CASE-1", performed_id=pc["id"], summary="capacity 15 Ah", measurements={}, supports=["B"], contradicts=["A"],
                   actor="E", actor_kind="engineer")
    v = O.verify(led, "SYN-CASE-1", hypothesis_id="B", result="confirmed", observation_ids=[ob["id"]], basis="test", actor="E", actor_kind="engineer")
    O.correct(led, "SYN-CASE-1", record_id=v["id"], reason="supplier found the test load bypassed the meter",
              replacement={"hypothesis_id": "B", "result": "not_settled", "observation_ids": [], "basis": "test invalidated"}, actor="E3", actor_kind="engineer")
    st = O.state(led, "SYN-CASE-1")
    assert st["hypotheses"]["B"]["verification"]["result"] == "not_settled"
    assert st["hypotheses"]["B"]["stage"] == "observed_test_result" and st["case_stage"] == "open"
    assert O.verified_for(led, "SYN-CASE-1") == []
    assert len([h for h in st["history"] if h["kind"] == "verification"]) == 2


def test_closure_without_cause_is_unresolved(led, incident):
    _chain(led, incident)
    O.close(led, "SYN-CASE-1", summary="vehicle returned to service after recovery charge; cause not established", actor="E", actor_kind="engineer")
    assert O.state(led, "SYN-CASE-1")["case_stage"] == "unresolved"


def test_ledger_is_append_only_and_tamper_evident(led):
    led.append("note", "s", {"a": 1}, actor="E", actor_kind="engineer")
    led.append("note", "s", {"a": 2}, actor="E", actor_kind="engineer")
    assert led.verify_chain()["ok"]
    led.db.execute("UPDATE records SET payload='{\"a\": 3}' WHERE seq=1")
    assert led.verify_chain()["ok"] is False
    with pytest.raises(LedgerError):
        led.append("note", "s", {}, actor="", actor_kind="engineer")
    with pytest.raises(LedgerError):
        led.append("note", "s", {}, actor="E", actor_kind="robot")
