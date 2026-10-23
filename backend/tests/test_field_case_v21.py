"""Engine v2.1: reference, brief events, interventions, triage handoffs, hypothesis board, recurrence, reproduction.

All fixtures are SYNTHETIC.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.analysis import transients
from app.engine import triage as triage_rules
from app.workflows.demo import FILES, FIXTURES, build_field_case_story
from app.ingest.pipeline import ingest
from app.ingest.profile import parse_profile
from app.ledger.model import LedgerError
from app.store.sqlite_store import CaseStore

NOW = datetime(2026, 4, 1, tzinfo=timezone.utc)
WINDOW = (datetime(2026, 3, 4, 9, 6, tzinfo=timezone.utc).timestamp(), datetime(2026, 3, 4, 9, 7, 30, tzinfo=timezone.utc).timestamp())
HYD = ("synthetic_ex1_hydraulics.csv", "synthetic_hydraulics_profile.json")


def _rows(*files):
    rows = []
    for data, profile in files:
        rows += ingest((FIXTURES / data).read_bytes(), data, parse_profile(json.loads((FIXTURES / profile).read_text())), "EX1", now=NOW).rows
    return rows


@pytest.fixture
def store(tmp_path: Path):
    s = CaseStore(tmp_path / "engine.db", clock=lambda: NOW)
    yield s
    s.close()


def _case(store, files=FILES + (HYD,), actor="Engineer"):
    from app.workflows.demo import ensure_machine_class
    ensure_machine_class(store)  # EX1 is an excavator: its profile names the teams
    case = store.create_case(machine_id="EX1", title="t", actor=actor, synthetic=True, symptom_start="2026-03-04T09:06:00Z",
                             symptom_end="2026-03-04T09:07:30Z", symptom_source="synthetic test")
    for data, profile in files:
        store.commit_import(case["id"], (FIXTURES / data).read_bytes(), data, parse_profile(json.loads((FIXTURES / profile).read_text())), actor=actor)
    return case["id"]


# ---------- brief events ----------
def test_brief_events_found_exactly_and_smooth_signals_clean():
    out = transients.detect(_rows(*FILES, HYD), WINDOW)
    by_signal = {s["signal"]: s for s in out["signals"]}
    pressure = by_signal["vehicle.hydraulic_pressure"]["events"]
    assert [(e["start"], e["samples"]) for e in pressure] == [
        ("2026-03-04T09:04:13.400Z", 1), ("2026-03-04T09:06:32.200Z", 2), ("2026-03-04T09:06:58.840Z", 1)]
    assert [e["in_symptom_window"] for e in pressure] == [False, True, True]
    assert all(e["plot_width_px"] < 1 for e in pressure), "the point: none of these is visible on a whole-recording plot"
    assert pressure[2]["direction"] == "up" and pressure[1]["direction"] == "down"
    assert by_signal["vehicle.actuator_response"]["events"] == [] and "vehicle.command_input" not in by_signal
    assert {s["signal"] for s in out["skipped"]} == {"network.rtt", "network.packet_loss", "vehicle.command_input"}  # 0.1 Hz; commands are not scanned
    assert all(r["row"] for e in pressure for r in e["rows"]), "every event points at its source rows"


def test_sustained_departure_is_not_a_brief_event():
    rows = [{"kind": "measurement", "signal": "x", "scope": "s", "unit": "u", "clock_domain": "c", "t": i / 50.0,
             "value": 100.0 + (50.0 if 400 <= i < 420 else 0.0) + 0.1 * ((i * 7) % 5)} for i in range(1000)]
    out = transients.detect(rows)
    assert out["signals"][0]["events"] == []
    assert out["signals"][0]["level_changes"] >= 1, "step edges are level changes, not brief events"


# ---------- reference ----------
def test_reference_assumed_then_declared(store):
    cid = _case(store)
    run = store.investigate(cid, actor="Engineer")
    assert run["reference"]["status"] == "assumed_default" and run["reference"]["evidence_grade"] == "compared_to_assumed_reference"
    assert run["missing_information"][0].startswith("No known-good reference declared")
    declare = next(c for c in run["checks"] if c["check_id"] == "CHK-DECLARE-REFERENCE")
    assert declare["trigger"] and declare["availability"]["state"] == "ready_remote"

    with pytest.raises(LedgerError, match="overlaps the symptom window"):
        store.append(cid, "reference_declared", {"window_from": "2026-03-04T09:05:00Z", "window_to": "2026-03-04T09:06:30Z", "basis": "x"}, actor="Engineer")
    with pytest.raises(LedgerError, match="No imported records"):
        store.append(cid, "reference_declared", {"window_from": "2026-03-03T09:00:00Z", "window_to": "2026-03-03T09:05:00Z", "basis": "x"}, actor="Engineer")
    store.append(cid, "reference_declared", {"window_from": "2026-03-04T09:00:00Z", "window_to": "2026-03-04T09:05:30Z", "basis": "Operator confirmed normal digging"}, actor="Engineer")
    run = store.investigate(cid, actor="Engineer")
    assert run["reference"]["status"] == "declared" and run["reference"]["by"] == "Engineer"
    assert run["findings"]["input_response"]["channels"][0]["baseline_window"] == ["2026-03-04T09:00:00Z", "2026-03-04T09:05:30Z"]
    comparison = {s["signal"]: s["status"] for s in run["findings"]["reference_comparison"]["signals"]}
    assert comparison["network.rtt"] == "higher_than_reference" and comparison["vehicle.actuator_response"] == "within_reference_range"
    assert next(c for c in run["checks"] if c["check_id"] == "CHK-DECLARE-REFERENCE")["availability"]["state"] == "answered_in_run"
    assert any(o["rule"] == "R-BRIEF" for o in run["domains"]["vehicle_body"]["observations"])


# ---------- interventions, triage, hypotheses ----------
def test_takeover_drives_severity_blocks_dispatch_until_reviewed(store):
    cid = _case(store)
    store.append(cid, "intervention_report", {"occurred_at": "2026-03-04T09:06:58Z", "intervention_type": "takeover", "operator": "Op A",
                                              "stated_reason": "Boom kept moving"}, actor="Engineer")
    run = store.investigate(cid, actor="Engineer")
    assert run["triage"]["proposed"]["severity"]["rule"] == "T1" and run["triage"]["status"] == "proposed_only"
    assert any(b["status"] == "intervention_review_outstanding" for b in run["dispatch"]["blockers"])
    nearby = run["interventions"][0]["evidence_nearby"]
    assert nearby[0]["kind"] == "brief_event" and nearby[0]["signal"] == "vehicle.hydraulic_pressure"
    assert nearby[0]["offset_ms"] == 840, "40 ms after the takeover plus the undeclared 0.8 s vehicle clock offset"
    review = next(c for c in run["checks"] if c["check_id"] == "CHK-INTERVENTION-REVIEW")
    assert review["trigger"]

    intervention = next(e for e in store.ledger(cid) if e["kind"] == "intervention_report")
    store.append(cid, "intervention_review", {"intervention_id": intervention["id"], "run_id": run["id"], "verdict": "unnecessary",
                                              "reason": "False alarm; recreate the conditions"}, actor="Reviewer")
    run = store.investigate(cid, actor="Engineer")
    assert run["interventions"][0]["review"]["verdict"] == "unnecessary"
    assert not any(b["status"] == "intervention_review_outstanding" for b in run["dispatch"]["blockers"])
    assert next(c for c in run["checks"] if c["check_id"] == "CHK-INTERVENTION-REVIEW")["availability"]["state"] == "answered_in_run"


def test_triage_handoffs_are_counted_and_warned(store):
    cid = _case(store, files=FILES)
    for owner in ("Remote-control software", "Network & connectivity", "Remote-control software"):
        store.append(cid, "triage", {"severity": "medium", "owner": owner, "reason": "evidence moved"}, actor="Engineer")
    run = store.investigate(cid, actor="Engineer")
    assert run["triage"]["handoffs"] == 2 and run["triage"]["handoff_warning"]
    assert run["triage"]["proposed"]["owner"]["team"] == "Network & connectivity"
    assert run["triage"]["differs_from_proposal"] is True


def test_hypothesis_status_needs_evidence_and_shapes_the_board(store):
    cid = _case(store)
    run = store.investigate(cid, actor="Engineer")
    with pytest.raises(LedgerError, match="needs evidence"):
        store.append(cid, "hypothesis_status", {"run_id": run["id"], "domain": "vehicle_interface", "status": "ruled_out", "basis": "b", "scope": "s"}, actor="Engineer")
    with pytest.raises(LedgerError, match="not observed for network"):
        store.append(cid, "hypothesis_status", {"run_id": run["id"], "domain": "network", "status": "ruled_out", "basis": "b", "scope": "s",
                                                "finding_rules": ["R-IO"]}, actor="Engineer")
    with pytest.raises(LedgerError, match="'scope' is required"):
        store.append(cid, "hypothesis_status", {"run_id": run["id"], "domain": "vehicle_interface", "status": "ruled_out", "basis": "b",
                                                "finding_rules": ["R-SEGMENT"]}, actor="Engineer")
    store.append(cid, "hypothesis_status", {"run_id": run["id"], "domain": "vehicle_interface", "status": "ruled_out", "basis": "b",
                                            "scope": "matched commands", "finding_rules": ["R-SEGMENT"]}, actor="Engineer")
    run = store.investigate(cid, actor="Engineer")
    board = {b["domain"]: b for b in run["hypotheses"]}
    assert board["vehicle_interface"]["column"] == "ruled_out" and board["vehicle_interface"]["human"]["scope"] == "matched commands"
    assert board["network"]["column"] == "supported"
    assert all("vehicle_interface" not in c["informs_open_domains"] for c in run["checks"])
    assert "vehicle_interface" not in [d["domain"] for d in run["triage"]["proposed"]["also_open"]]


def test_reproduction_attempt_answers_the_check(store):
    cid = _case(store)
    run = store.investigate(cid, actor="Engineer")
    repro = next(c for c in run["checks"] if c["check_id"] == "CHK-REPRODUCE")
    assert repro["trigger"] and "network" in repro["trigger"].lower()
    store.append(cid, "reproduction_attempt", {"run_id": run["id"], "environment": "simulation", "platform": "Sim", "setup": "inject delay",
                                               "result": "reproduced", "observations": "late response", "performed_at": "2026-03-05T10:00:00Z"}, actor="Engineer")
    run = store.investigate(cid, actor="Engineer")
    assert next(c for c in run["checks"] if c["check_id"] == "CHK-REPRODUCE")["availability"]["state"] == "answered_in_run"
    episode = store.episodes(cid)[0]
    assert episode["schema"] == "veyra-episode-v3" and episode["interventions"]["reproduction_attempts"]


def test_brief_events_check_runs_read_only(store):
    cid = _case(store)
    run = store.investigate(cid, actor="Engineer")
    store.append(cid, "check_review", {"run_id": run["id"], "check_id": "CHK-BRIEF-EVENTS", "verdict": "accepted", "reason": "look"}, actor="Engineer")
    executed = store.execute_automatic(cid, "CHK-BRIEF-EVENTS", actor="Engineer")
    assert executed["result"]["count"] == 3 and executed["entry"]["payload"]["status"] == "automatic_read_only"


# ---------- recurrence ----------
def test_recurrence_uses_same_machine_and_never_mixes_synthetic_with_real():
    history = [{"case_id": "c0", "operators": ["Op B"], "observed_domains": ["network"]}]
    domains = {"network": {"status": "delay_observed"}, "vehicle_body": {"status": "not_observed"}}
    notes = triage_rules.recurrence(history, domains, ["Op A"])["notes"]
    assert notes[0]["different_operators"] == ["Op B"] and "heuristic" in notes[0]["text"]
    assert triage_rules.recurrence(history, domains, [])["notes"][0]["different_operators"] == ["Op B"]
    assert "no inference" in triage_rules.recurrence(history, domains, [])["notes"][0]["text"]


def test_field_case_story_end_to_end(store):
    out = build_field_case_story(store)
    run = store.latest_run(out["case_id"])
    verify = store.verify(out["case_id"])
    assert verify["ledger_chain"]["valid"] and verify["imports_intact"] and verify["runs_intact"]
    assert run["reference"]["status"] == "declared"
    assert run["triage"]["handoffs"] == 1 and run["triage"]["confirmed"]["owner"] == "Network & connectivity"
    assert {b["domain"]: b["column"] for b in run["hypotheses"]} == {"network": "supported", "control_suite": "open", "vehicle_interface": "ruled_out", "vehicle_body": "open"}
    assert run["machine_history"]["notes"][0]["different_operators"] == ["Operator B (synthetic)"]
    assert run["interventions"][0]["review"]["verdict"] == "necessary"
    assert store.machine_history("EX1", synthetic=False) == []
    kinds = [e["kind"] for e in store.ledger(out["case_id"])]
    for kind in ("intervention_report", "triage", "reference_declared", "hypothesis_status", "reproduction_attempt", "intervention_review", "outcome"):
        assert kind in kinds
