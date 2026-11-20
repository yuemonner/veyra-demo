"""Audit of the QD-1 case (SYNTHETIC walking robot, left front knee stops following its commands).

Each test pins one statement Veyra makes to the source rows, so a rule written for another machine (the excavator's
'watchdog restart') can't leak into this one.
"""
import re
import sys
from pathlib import Path

import pytest

pytest.importorskip("rosbags")
pytest.importorskip("mcap")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import make_synthetic_recordings as gen  # noqa: E402
from app.agent.runner import _check_report  # noqa: E402
from app.engine import decision_check as dc  # noqa: E402
from app.engine import next_action as na  # noqa: E402
from app.workflows import autopilot  # noqa: E402
from app.workflows.debrief import debrief_recording  # noqa: E402
from app.ingest import bags  # noqa: E402
from app.store.sqlite_store import CaseStore  # noqa: E402

EVENT_WORDS = re.compile(r"\b(restart\w*|reset\w*|reboot\w*|lost link|link loss|watchdog)\b", re.IGNORECASE)


@pytest.fixture(scope="module")
def qd1(tmp_path_factory):
    path = gen.write_all(tmp_path_factory.mktemp("rec"))["mcap"]
    store = CaseStore(tmp_path_factory.mktemp("db") / "e.db")
    out = debrief_recording(store, path, bags.suggest(bags.inspect(path)), machine_id="QD1", title="Left front leg dragging (SYNTHETIC)", actor="Engineer",
                            machine_class="legged_robot", problem_from_s=55, problem_to_s=119, normal_from_s=5, normal_to_s=50,
                            report={"stated_reason": "Robot started limping", "symptom": "erratic_motion", "affected_function": "left front leg", "at_s": 62})
    yield store, out["case_id"]
    store.close()


def _rows(store, cid):
    return [r for i in store.imports(cid) for r in i["rows"]]


def test_issue1_no_event_is_named_that_the_rows_do_not_contain(qd1):
    store, cid = qd1
    rows = _rows(store, cid)
    assert not any(r["signal"] in ("controller.reset", "network.link_event", "app.heartbeat", "fault.code") for r in rows)   # the source data
    v = autopilot.advance(store, cid)
    text = v["request"]["body"] + " ".join(o["label"] for c in v["checks"] for o in c["outcomes"])
    gap = "The recording has no controller, reset or link events, so we don't know whether any occurred."
    assert gap in text and not EVENT_WORDS.search(text.replace(gap, "")), text                             # absence stated; nothing presumed
    assert "LF_KFE" in v["request"]["body"] and "before or after the LF_KFE tracking departure" in v["request"]["body"]
    for fact in v["request"]["observed"]:                                                                  # every fact has a source
        assert fact["source"] and fact["time"]


def test_issue2_a_leading_hypothesis_needs_comparative_evidence_and_one_part():
    base = {"summary": "s", "observations": [], "proposed_checks": [], "questions_for_engineers": [], "uncertainty": "u"}
    vague = {**base, "hypotheses": [
        {"statement": "The LF_KFE drive, its encoder or its cabling has a fault", "domain": "vehicle_interface", "status": "leading", "support": ["t1"], "against": []},
        {"statement": "Mechanical binding", "domain": "vehicle_body", "status": "open", "support": ["t1"], "against": ["t1"]},
        {"statement": "The controller sent bad commands", "domain": "control_suite", "status": "open", "support": ["t1"]}]}
    problems = _check_report(vague, {"t1"})
    assert any("several components" in p for p in problems) and any("no evidence against it" in p for p in problems)
    fixed = {**base, "hypotheses": [
        {"statement": "A fault at the LF_KFE joint", "domain": "vehicle_interface", "status": "leading", "support": ["t1"]},
        {"statement": "The controller sent bad commands", "domain": "control_suite", "status": "open", "support": ["t1"], "against": ["t1"]}]}
    assert _check_report(fixed, {"t1"}) == []


def test_issue3_the_plan_compares_checks_and_never_claims_one_answer_settles_it(qd1):
    store, cid = qd1
    run = store.latest_run(cid)
    a = na.assess(run)
    assert [x["part"] for x in a["less_likely"] if "other joints tracked normally" in x["why"]] == ["network"]   # comparative evidence
    assert {c["part"] for c in a["cannot_tell_apart"]} == {"vehicle_interface", "control_suite"}
    rows = {r["check_id"]: r for r in na.compare(run, a)}
    assert {"CHK-CONTROLLER-LOGS", "CHK-DRIVE-LOGS"} <= set(rows)
    for r in rows.values():
        assert r["settles_note"] == "No single result settles it alone" and r["ranking_basis"]
    assert "drive's status messages" in rows["CHK-DRIVE-LOGS"]["note"]                                     # what the recording already has
    picked = [c["check_id"] for c in na.plan(run, a)]
    assert set(picked) == {"CHK-CONTROLLER-LOGS", "CHK-DRIVE-LOGS"}                                       # both, in one request
    claim = dc.verify_remote_check(run, {"action": "remote_check", "check_id": "CHK-CONTROLLER-LOGS"}, store.ledger(cid))["claims"][0]
    assert "separates" not in claim["claim"] and "No single result settles it alone" in claim["why"]


def test_issue5_self_checks_are_internal_and_written_once(qd1):
    store, cid = qd1
    for _ in range(3):
        autopilot.advance(store, cid)
    kinds = [e["kind"] for e in store.ledger(cid)]
    assert "decision_proposal" not in kinds and "decision_check" not in kinds and "decision" not in kinds
    selfs = [e for e in store.ledger(cid) if e["kind"] == "next_action" and e["payload"].get("stage") == "self_check"]
    assert all(e["payload"]["internal"] for e in selfs) and len({e["payload"]["fingerprint"] for e in selfs}) == len(selfs)
