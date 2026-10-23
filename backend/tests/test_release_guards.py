"""Release guards: four classes of regression test, one per guarantee the engine makes.

A. Time      - an investigation "as of" a time uses only what was recorded by then (no hindsight).
B. Context   - a similar earlier case on a different firmware/config/workload is shown, but its settling check is not promoted.
C. Checks    - one eligibility rule; no consumer recommends a check that was answered, performed, can't run, or would
               fetch evidence that is already in the case.
D. States    - a finding never becomes a cause by itself; checks and decisions move only along their state tables.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.engine import checks as check_policy
from app.engine import next_action as na
from app.engine import precedents
from app.engine import states as case_states
from app.ingest.profile import parse_profile
from app.ledger.model import LedgerError
from app.store.sqlite_store import CaseStore, StoreError

PROFILE = parse_profile({"profile_id": "t", "version": 1, "format": "jsonl", "synthetic": False,
                         "columns": {"timestamp": "t", "asset": "asset", "signal": "signal", "value": "value", "unit": "unit", "scope": "scope"},
                         "signal_map": {"rc_out": "actuator.output"}, "clock_domain": "c", "timestamp_format": "epoch", "epoch_unit": "ns"})
T0 = 1_700_000_000


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


class Clock:
    """A store clock the test moves forward, so 'recorded at' times are known exactly."""
    def __init__(self, t):
        self.t = datetime.fromtimestamp(t, timezone.utc)

    def __call__(self):
        return self.t

    def advance(self, hours):
        self.t += timedelta(hours=hours)
        return self.t


@pytest.fixture
def clock():
    return Clock(T0 + 86400)


@pytest.fixture
def store(tmp_path, clock):
    s = CaseStore(tmp_path / "g.db", clock=clock)
    yield s
    s.close()


def case(store, t0, machine="M1", stuck=False, investigate=True, declare=True):
    """A real-data case: one output channel; with stuck=True it freezes at 1900 inside the symptom window."""
    rows = [{"t": int((t0 + i * 0.3) * 1e9), "asset": machine, "signal": "rc_out", "unit": "us", "scope": "x",
             "value": 1900 if stuck and 12 <= i * 0.3 <= 19 else 1480 + (i * 7) % 40} for i in range(80)]
    c = store.create_case(machine_id=machine, title=f"case {t0}", actor="a", synthetic=False,
                          symptom_start=iso(t0 + 10), symptom_end=iso(t0 + 20), symptom_source="test report")
    store.commit_import(c["id"], "".join(json.dumps(r) + "\n" for r in rows).encode(), f"{t0}.jsonl", PROFILE, actor="a")
    if declare:
        store.append(c["id"], "reference_declared", {"window_from": iso(t0 + 0.5), "window_to": iso(t0 + 9), "basis": "Normal before the report",
                                                     "source": "operator"}, actor="Engineer")
    if investigate:
        store.investigate(c["id"], actor="a")
    return c["id"]


def close_probable(store, cid, cause="Linkage loose", check=None):
    return store.append(cid, "case_closure", {"investigation_status": "cause_probable", "cause_domain": "vehicle_interface", "cause": cause,
                                              "confirmed_by": "engineer_judgement", "resolution_status": "resolved",
                                              **({"decisive_check": check} if check else {})}, actor="Engineer")


# ---------------- A. Time ----------------

def test_A1_replay_does_not_see_a_closure_recorded_later(store, clock):
    old = case(store, T0, stuck=True)
    new = case(store, T0 + 5000, stuck=True)
    before_closure = clock()
    clock.advance(2)
    close_probable(store, old, check="CHK-ONSITE-ACTUATOR")
    clock.advance(1)
    live = store.investigate(new, actor="a")
    assert [m["case_id"] for m in live["precedents"]["matches"]] == [old]
    replay = store.investigate(new, actor="a", as_of=before_closure.isoformat())
    assert replay["precedents"]["matches"] == [] and replay["as_of"] == iso(before_closure.timestamp())


def test_A2_replay_does_not_use_a_baseline_declared_later(store, clock):
    good = case(store, T0, declare=False)
    incident = case(store, T0 + 5000, stuck=True)
    before = clock()
    clock.advance(1)
    store.append(good, "reference_declared", {"window_from": iso(T0 + 1), "window_to": iso(T0 + 9), "basis": "Known-good shift", "source": "operator"}, actor="Engineer")
    assert [r["case_id"] for r in store.known_good_runs(store.case(incident))] == [good]
    assert store.known_good_runs(store.case(incident), as_of=before.timestamp()) == []


def test_A3_a_later_incident_is_not_this_machines_history(store):
    early = case(store, T0)
    late = case(store, T0 + 5000)
    history = store.machine_history("M1", exclude_case=early, synthetic=False, before_incident=store.case(early)["symptom_start"])
    assert late not in [h["case_id"] for h in history]
    assert early in [h["case_id"] for h in store.machine_history("M1", exclude_case=late, synthetic=False,
                                                                 before_incident=store.case(late)["symptom_start"])]


def test_A4_as_of_must_be_past_and_after_some_evidence(store, clock):
    cid = case(store, T0)
    with pytest.raises(StoreError, match="future"):
        store.investigate(cid, actor="a", as_of=(clock() + timedelta(hours=1)).isoformat())
    with pytest.raises(StoreError, match="imported by as_of"):
        store.investigate(cid, actor="a", as_of=(clock() - timedelta(days=30)).isoformat())


# ---------------- B. Context ----------------

def snapshot(**values):
    return {"attributes": {k: ({"value": v, "status": "engineer_confirmed"} if v is not None else {"value": None, "status": "not_recorded"})
                           for k, v in values.items()}}


SIG = [{"domain": "vehicle_interface", "rule": "R-OUTPUT-STUCK", "signal": "actuator.output", "scope": "x"}]


def candidate(ctx):
    return {"case_id": "case-old", "title": "old", "machine_id": "M1", "profile_id": "generic", "synthetic": False, "signature": SIG,
            "context": ctx, "closure": {"investigation_status": "cause_probable", "decisive_check": "CHK-ONSITE-ACTUATOR", "evidence_strength": "judgement"}}


@pytest.mark.parametrize("now, then, status, promoted", [
    (snapshot(firmware_version="2.1"), snapshot(firmware_version="2.1"), "same", True),
    (snapshot(firmware_version="2.2"), snapshot(firmware_version="2.1"), "differs", False),
    (snapshot(operating_conditions="full load"), snapshot(operating_conditions="idle"), "differs", False),
    (snapshot(firmware_version="2.2"), snapshot(firmware_version=None), "unverified", False),
    (snapshot(), snapshot(), "not_recorded", True),
])
def test_B1_precedent_check_is_promoted_only_for_the_same_context(now, then, status, promoted):
    out = precedents.match(SIG, [candidate(then)], machine_id="M1", profile_id="generic", synthetic=False, context_now=now)
    m = out["matches"][0]
    assert m["context"]["status"] == status                       # always shown: a lead, with what differs
    assert ("CHK-ONSITE-ACTUATOR" in out["decisive_checks"]) is promoted
    if not promoted:
        assert "machine context" in m["check_not_moved_up"]


def test_B2_context_hash_is_value_based_and_machine_independent():
    a = precedents.context_values(snapshot(firmware_version="FW 2.1", model="X"))
    b = precedents.context_values(snapshot(firmware_version="fw 2.1", model="x"))
    assert precedents.context_hash(a) == precedents.context_hash(b) is not None
    assert precedents.context_hash(precedents.context_values(snapshot())) is None


# ---------------- C. Checks ----------------

FACTS = {"signals_present": set(), "answered_in_run": {}, "remote_access": "read_only_authorized", "previously_performed": {}}


def test_C1_asking_for_a_log_already_in_the_case_is_not_eligible():
    check = next(c for c in check_policy.CATALOG if c["id"] == "CHK-CONTROLLER-LOGS")
    a = check_policy._availability(check, {**FACTS, "sources_in_case": {"flight_controller_log": ["flight.ulg"]}})
    assert a["state"] == "already_in_case" and "flight.ulg" in a["detail"]
    assert check_policy.eligibility({"availability": a})["eligible"] is False
    assert check_policy._availability(check, {**FACTS, "sources_in_case": {"flight_controller_log": []}})["state"] == "ready_remote"


@pytest.mark.parametrize("availability, performed, eligible, prerequisite", [
    ("ready_remote", None, True, None),
    ("needs_authorization", None, True, "authorized"),
    ("onsite_required", None, True, "site approval"),
    ("answered_in_run", None, False, None),
    ("already_in_case", None, False, None),
    ("needs_data", None, False, None),
    ("needs_context", None, False, None),
    ("not_applicable", None, False, None),
    ("ready_remote", {"entry_id": "led-x"}, False, None),
])
def test_C2_one_eligibility_rule(availability, performed, eligible, prerequisite):
    e = check_policy.eligibility({"availability": {"state": availability}, "previously_performed": performed})
    assert e["eligible"] is eligible
    assert (prerequisite in " ".join(e["prerequisites"])) if prerequisite else e["prerequisites"] == []


def test_C3_next_action_never_compares_an_ineligible_check():
    split_id = next(iter(na.SPLITS))
    run = {"id": "run-1", "domains": {d: {"status": "anomaly_observed", "observations": []} for d in ("network", "control_suite")},
           "findings": {"coverage": []}, "hypotheses": [],
           "checks": [{"check_id": split_id, "question": "q", "availability": {"state": "already_in_case", "detail": "in case"}, "rank": 1}]}
    assessment = {"cannot_tell_apart": [{"part": "network", "label": "Network"}, {"part": "control_suite", "label": "Control"}],
                  "supported": [], "less_likely": [], "effects": []}
    assert na.compare(run, assessment) == []


def test_C4_a_real_run_marks_every_check_with_its_eligibility(store):
    run = store.latest_run(case(store, T0, stuck=True))
    assert run["checks"] and all({"eligible", "reason", "prerequisites"} <= set(c["eligibility"]) for c in run["checks"])
    assert all(not check_policy.eligible(c) for c in run["checks"] if c["availability"]["state"] in check_policy.NOT_ELIGIBLE_STATES)


# ---------------- D. States ----------------

def approve(store, cid, check="CHK-SOURCE-ROWS"):
    run = store.latest_run(cid)
    store.append(cid, "check_review", {"run_id": run["id"], "check_id": check, "verdict": "accepted", "reason": "useful"}, actor="Engineer")
    return run


def perform(store, cid, run, check="CHK-SOURCE-ROWS", status="performed"):
    body = {"run_id": run["id"], "check_id": check, "status": status}
    body.update({"reason": "Site closed"} if status == "not_performed" else
                {"executed_at": iso(T0 + 100), "performer": "Tech", "observations": "Looked at the rows", "evidence_note": "Work order 12"})
    return store.append(cid, "check_execution", body, actor="Engineer")


def test_D1_the_engine_never_writes_a_cause(store):
    cid = case(store, T0, stuck=True)
    run = store.latest_run(cid)
    stages = case_states.lifecycle(run, store.ledger(cid))["parts"]
    assert run["root_cause"] == "unconfirmed"
    assert not any(p["stage"].startswith("cause_") for p in stages.values())
    assert any(p["stage"] == "anomaly" for p in stages.values())       # the finding is a fact about data, nothing more
    close_probable(store, cid)
    assert case_states.lifecycle(run, store.ledger(cid))["parts"]["vehicle_interface"]["stage"] == "cause_probable"


def test_D2_confirmed_needs_a_record_that_actually_confirms(store):
    cid = case(store, T0, stuck=True)
    run = approve(store, cid)
    skipped = perform(store, cid, run, status="not_performed")
    body = {"investigation_status": "cause_confirmed", "cause_domain": "vehicle_interface", "cause": "Servo linkage loose",
            "confirmed_by": "inspection", "resolution_status": "resolved"}
    with pytest.raises(LedgerError, match="None of the linked records confirms it"):
        store.append(cid, "case_closure", {**body, "evidence_entries": [skipped["id"]]}, actor="Engineer")
    # An inspection documented outside Veyra stays possible, as before.
    assert store.append(cid, "case_closure", {**body, "evidence_note": "Technician report WO-881 with photo"}, actor="Engineer")


def test_D3_a_check_moves_only_along_its_state_table(store):
    cid = case(store, T0, stuck=True)
    run = approve(store, cid)
    assert case_states.check_state(store.ledger(cid), run["id"], "CHK-SOURCE-ROWS") == "approved"
    perform(store, cid, run, status="not_performed")
    perform(store, cid, run)                                            # retry after 'not performed' is allowed
    assert case_states.check_state(store.ledger(cid), run["id"], "CHK-SOURCE-ROWS") == "answered"
    with pytest.raises(LedgerError, match="already has a result"):
        perform(store, cid, run)
    with pytest.raises(LedgerError, match="already has a result"):
        store.append(cid, "check_review", {"run_id": run["id"], "check_id": "CHK-SOURCE-ROWS", "verdict": "rejected", "reason": "x"}, actor="Engineer")
    with pytest.raises(LedgerError, match="already has a result"):
        store.append(cid, "request_sent", {"run_id": run["id"], "check_ids": ["CHK-SOURCE-ROWS"], "channel": "email", "sent_to": "site",
                                           "sent_at": iso(T0 + 50)}, actor="Engineer")


def test_D4_decision_states_follow_the_record(store):
    cid = case(store, T0, stuck=True)
    run = store.latest_run(cid)
    d = store.append(cid, "decision", {"run_id": run["id"], "options_considered": ["dispatch", "wait"], "chosen_option": "dispatch",
                                       "rationale": "Output frozen"}, actor="Engineer")
    assert case_states.decision_state(store.ledger(cid), d["id"]) == "decided"
    a = store.append(cid, "action", {"decision_id": d["id"], "action_type": "onsite_inspection", "description": "Inspect servo",
                                     "status": "performed", "performed_at": iso(T0 + 200), "performer": "Tech"}, actor="Engineer")
    assert case_states.decision_state(store.ledger(cid), d["id"]) == "acted"
    o = store.append(cid, "outcome", {"action_id": a["id"], "result": "recovered", "observed_from": iso(T0 + 210), "observed_to": iso(T0 + 400),
                                      "verification": "unverified", "observations": "Flies normally"}, actor="Engineer")
    assert case_states.decision_state(store.ledger(cid), d["id"]) == "outcome_recorded"
    with pytest.raises(LedgerError, match="different person"):
        store.append(cid, "outcome_review", {"outcome_id": o["id"], "verdict": "accepted", "reason": "ok"}, actor="Engineer")
    store.append(cid, "outcome_review", {"outcome_id": o["id"], "verdict": "accepted", "reason": "Seen in the logs"}, actor="Reviewer")
    assert case_states.decision_state(store.ledger(cid), d["id"]) == "verified"


# ---------------- Evidence plots ----------------

def test_series_keeps_a_held_value_and_spikes_when_thinning(store):
    cid = case(store, T0, stuck=True)
    out = store.series(cid, "actuator.output", "x", T0, T0 + 30, max_points=60)
    assert out["samples"] == 80 and out["thinned"] and out["shown"] <= 60 and out["unit"] == "us"
    assert max(v for _, v in out["points"]) == 1900                  # the frozen value survives thinning
    with pytest.raises(StoreError):
        store.series(cid, "actuator.output", "x", T0 + 10, T0, max_points=60)
