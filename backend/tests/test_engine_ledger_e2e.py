"""Investigation Engine v2 + Intervention Ledger + store + ENGINEER CLI, end to end on SYNTHETIC fixtures."""
import json
import sqlite3
from pathlib import Path

import pytest

from app.workflows.cli import main
from app.ingest.profile import parse_profile
from app.ledger.model import LedgerError, verify_chain
from app.store.sqlite_store import CaseStore, StoreError

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "synthetic" / "remote_delay"
FILES = (("synthetic_ex1_console.csv", "synthetic_console_profile.json"), ("synthetic_ex1_vehicle.jsonl", "synthetic_vehicle_profile.json"),
         ("synthetic_ex1_vehicle_io.csv", "synthetic_vehicle_io_profile.json"))
IDENTITY = ("oem", "model", "kit_version", "control_version", "firmware_version", "vehicle_interface")


def profile(name):
    return parse_profile(json.loads((FIXTURES / name).read_text()))


@pytest.fixture
def store(tmp_path):
    s = CaseStore(tmp_path / "veyra.db")
    yield s
    s.close()


def new_case(store, files=FILES):
    case = store.create_case(machine_id="EX1", title="Synthetic delay", actor="Engineer A", synthetic=True, symptom_start="2026-03-04T09:06:00Z",
                             symptom_end="2026-03-04T09:07:30Z", symptom_source="Synthetic operator report")
    for data, prof in files:
        store.commit_import(case["id"], (FIXTURES / data).read_bytes(), data, profile(prof), actor="Engineer A")
    return case["id"]


def confirm_identity(store):
    for key in IDENTITY:
        value = store.context("EX1")["attributes"][key]["value"]
        store.assert_context({"machine_id": "EX1", "key": key, "value": value, "status": "engineer_confirmed", "source": "Synthetic plate photo",
                              "actor": "Engineer A", "effective_from": "2026-03-01T00:00:00Z", "evidence": ["photo-1"]})


def test_engine_localizes_synthetic_delay_without_claiming_a_cause(store):
    cid = new_case(store)
    first = store.investigate(cid, actor="Engineer A")
    assert first["findings"]["input_response"]["channels"][0]["status"] == "not_comparable"
    assert first["root_cause"] == "unconfirmed" and first["model"]["external_calls"] == 0
    confirm_identity(store)
    run = store.investigate(cid, actor="Engineer A")
    domains = {k: v["status"] for k, v in run["domains"].items()}
    assert domains == {"network": "delay_observed", "control_suite": "anomaly_observed",
                       "vehicle_interface": "no_delay_observed_in_measured_part", "vehicle_body": "no_delay_observed_in_measured_part"}
    transport = run["findings"]["command_chain"]["segments"]["command.sent->command.received"]
    assert transport["status"] == "delay_change_observed_assuming_stable_offset" and 380 < transport["relative_change_ms"] < 520
    assert transport["absolute_latency"]["status"] == "unavailable" and "constant" in transport["assumption"]
    assert run["findings"]["input_response"]["channels"][0]["status"] == "no_change_detected"
    order = [c["check_id"] for c in run["checks"]]
    assert order[:2] == ["CHK-SOURCE-ROWS", "CHK-CLOCK-OFFSET"] and order[-1] == "CHK-RESTORE"
    assert run["dispatch"]["status"] == "not_ready" and run["dispatch"]["parts"]["status"] == "not_determined"
    assert all(step["status"] == "succeeded" for step in run["trace"])
    restore = store.investigate(cid, actor="Engineer A", goal="restore_operations")
    assert restore["checks"][0]["check_id"] == "CHK-RESTORE"


def test_bounded_clock_offset_gives_absolute_latency_with_uncertainty(store):
    cid = new_case(store)
    store.assert_context({"machine_id": "EX1", "key": "clock_offset_bound_ms", "value": "1000", "status": "engineer_confirmed", "source": "Synthetic NTP report",
                          "actor": "Engineer A", "effective_from": "2026-03-01T00:00:00Z", "evidence": ["ntp-1"]})
    transport = store.investigate(cid, actor="Engineer A")["findings"]["command_chain"]["segments"]["command.sent->command.received"]
    assert transport["absolute_latency"]["status"] == "computed_with_clock_uncertainty" and transport["absolute_latency"]["uncertainty_ms"] == pytest.approx(1000)


def test_ledger_rules_staleness_and_authorization(store):
    cid = new_case(store, FILES[:2])
    run = store.investigate(cid, actor="Engineer A")
    with pytest.raises(LedgerError, match="Accept or edit"):
        store.append(cid, "check_execution", {"run_id": run["id"], "check_id": "CHK-SOURCE-ROWS", "status": "automatic_read_only", "tool_result": {}}, actor="Engineer A")
    with pytest.raises(StoreError, match="not an automatic"):
        store.execute_automatic(cid, "CHK-PROBE-PATH", actor="Engineer A")
    with pytest.raises(StoreError, match="cannot run now: needs_data"):  # no input/response channel imported yet
        store.execute_automatic(cid, "CHK-IO-BASELINE", actor="Engineer A")
    store.append(cid, "check_review", {"run_id": run["id"], "check_id": "CHK-SOURCE-ROWS", "verdict": "edited", "reason": "narrow", "edited_procedure": "Only RTT rows"}, actor="Engineer A")
    with pytest.raises(LedgerError, match="Only an accepted, unedited check"):
        store.execute_automatic(cid, "CHK-SOURCE-ROWS", actor="Engineer A")
    store.append(cid, "check_review", {"run_id": run["id"], "check_id": "CHK-SOURCE-ROWS", "verdict": "accepted", "reason": "coverage first"}, actor="Engineer A")
    assert store.execute_automatic(cid, "CHK-SOURCE-ROWS", actor="Engineer A")["result"]["count"] > 0
    store.append(cid, "check_review", {"run_id": run["id"], "check_id": "CHK-PROBE-PATH", "verdict": "accepted", "reason": "ok"}, actor="Engineer A")
    with pytest.raises(LedgerError, match="needs evidence"):
        store.append(cid, "check_execution", {"run_id": run["id"], "check_id": "CHK-PROBE-PATH", "status": "performed", "executed_at": "2026-03-04T10:00:00Z",
                                              "performer": "Net eng", "observations": "Probe uses the command VLAN"}, actor="Engineer A")
    # New evidence makes the run stale for reviews, executions and decisions.
    store.commit_import(cid, (FIXTURES / FILES[2][0]).read_bytes(), FILES[2][0], profile(FILES[2][1]), actor="Engineer A")
    with pytest.raises(LedgerError, match="stale \\(evidence changed"):
        store.append(cid, "decision", {"run_id": run["id"], "options_considered": ["a"], "chosen_option": "a", "rationale": "r"}, actor="Engineer A")
    run2 = store.investigate(cid, actor="Engineer A")
    store.assert_context({"machine_id": "EX1", "key": "site", "value": "Pit 3", "status": "reported", "source": "call", "actor": "Engineer A", "effective_from": "2026-03-01T00:00:00Z"})
    with pytest.raises(LedgerError, match="machine context changed"):
        store.append(cid, "decision", {"run_id": run2["id"], "options_considered": ["a"], "chosen_option": "a", "rationale": "r"}, actor="Engineer A")
    store.investigate(cid, actor="Engineer A")
    with pytest.raises(LedgerError, match="older investigation run"):
        store.append(cid, "check_review", {"run_id": run2["id"], "check_id": "CHK-SOURCE-ROWS", "verdict": "accepted", "reason": "x"}, actor="Engineer A")


def test_decision_action_outcome_chain_and_episode_export(store):
    cid = new_case(store, FILES[:2])
    run = store.investigate(cid, actor="Engineer A")
    with pytest.raises(LedgerError, match="Linked decision"):
        store.append(cid, "action", {"decision_id": "nope", "action_type": "remote_restart", "description": "x", "status": "performed",
                                     "performed_at": "2026-03-04T10:00:00Z", "performer": "p"}, actor="Engineer A")
    with pytest.raises(LedgerError, match="must be one of options_considered"):
        store.append(cid, "decision", {"run_id": run["id"], "options_considered": ["a", "b"], "chosen_option": "c", "rationale": "r"}, actor="Engineer A")
    decision = store.append(cid, "decision", {"run_id": run["id"], "options_considered": ["Remote fix", "Dispatch"], "chosen_option": "Remote fix", "rationale": "Network first"}, actor="Engineer A")
    action = store.append(cid, "action", {"decision_id": decision["id"], "action_type": "network_change", "description": "APN change", "status": "performed",
                                          "performed_at": "2026-03-04T11:00:00Z", "performer": "Net eng"}, actor="Engineer A")
    with pytest.raises(LedgerError, match="cannot start before the action"):
        store.append(cid, "outcome", {"action_id": action["id"], "result": "recovered", "observed_from": "2026-03-04T10:00:00Z", "observed_to": "2026-03-04T12:00:00Z",
                                      "verification": "unverified", "observations": "ok"}, actor="Engineer A")
    imports = store.imports(cid, include_rows=False)
    with pytest.raises(LedgerError, match="has records inside the observation window"):
        store.append(cid, "outcome", {"action_id": action["id"], "result": "recovered", "observed_from": "2026-03-04T11:00:00Z", "observed_to": "2026-03-04T12:00:00Z",
                                      "verification": "evidence_attached", "evidence": [imports[0]["id"]], "observations": "ok"}, actor="Engineer A")
    outcome = store.append(cid, "outcome", {"action_id": action["id"], "result": "recovered", "observed_from": "2026-03-04T11:00:00Z", "observed_to": "2026-03-04T12:00:00Z",
                                            "verification": "unverified", "observations": "Operator reports normal response"}, actor="Engineer A")
    with pytest.raises(LedgerError, match="different person"):
        store.append(cid, "outcome_review", {"outcome_id": outcome["id"], "verdict": "accepted", "reason": "fine"}, actor="engineer a")
    store.append(cid, "outcome_review", {"outcome_id": outcome["id"], "verdict": "accepted", "reason": "fine"}, actor="Reviewer B")
    store.append(cid, "recurrence_check", {"outcome_id": outcome["id"], "window_from": "2026-03-04T12:00:00Z", "window_to": "2026-03-11T12:00:00Z", "recurred": "no"}, actor="Engineer A")

    episodes = store.episodes(cid)
    assert len(episodes) == 1
    episode = episodes[0]
    assert episode["decision_time"]["belief"] and episode["decision_time"]["candidates"] and episode["synthetic"] is True
    assert episode["hindsight"]["outcomes"][0]["payload"]["result"] == "recovered"
    assert "recovered" not in json.dumps(episode["decision_time"])  # no hindsight leaks into decision-time data
    assert episode["human"]["decisions"][0]["payload"]["chosen_option"] == "Remote fix"
    assert store.verify(cid)["ledger_chain"]["valid"]


def test_append_only_is_enforced_by_the_database_and_tampering_is_detected(store, tmp_path):
    cid = new_case(store, FILES[:1])
    store.investigate(cid, actor="Engineer A")
    for statement in ("UPDATE ledger SET kind = 'x'", "DELETE FROM ledger", "DELETE FROM imports", "UPDATE context_assertions SET machine_id = 'EX2'", "DELETE FROM runs"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            store.db.execute(statement)
    entries = store.ledger(cid)
    tampered = [dict(e) for e in entries]
    tampered[0] = {**tampered[0], "actor": "Someone else"}
    result = verify_chain(tampered)
    assert not result["valid"] and result["broken_at"] == 0


def test_synthetic_and_real_data_never_mix(store):
    real = store.create_case(machine_id="EX1", title="Real", actor="Engineer A", synthetic=False)
    with pytest.raises(StoreError, match="Synthetic profiles only go into synthetic cases"):
        store.commit_import(real["id"], (FIXTURES / FILES[0][0]).read_bytes(), FILES[0][0], profile(FILES[0][1]), actor="Engineer A")
    cid = new_case(store, FILES[:1])
    with pytest.raises(StoreError, match="duplicate_upload"):
        store.commit_import(cid, (FIXTURES / FILES[0][0]).read_bytes(), FILES[0][0], profile(FILES[0][1]), actor="Engineer A")


def test_cli_full_loop_as_an_fde(tmp_path, capsys):
    ws = ["--workspace", str(tmp_path / "ws")]
    assert main(ws + ["--json", "case", "create", "--machine", "EX1", "--title", "CLI case", "--actor", "Engineer A", "--synthetic",
                      "--symptom-start", "2026-03-04T09:06:00Z", "--symptom-end", "2026-03-04T09:07:30Z", "--symptom-source", "Synthetic call"]) == 0
    cid = json.loads(capsys.readouterr().out)["id"]
    for data, prof in FILES:
        assert main(ws + ["import", cid, str(FIXTURES / data), "--profile", str(FIXTURES / prof), "--actor", "Engineer A", "--preview"]) == 0
        assert main(ws + ["import", cid, str(FIXTURES / data), "--profile", str(FIXTURES / prof), "--actor", "Engineer A"]) == 0
    for key in IDENTITY:
        capsys.readouterr()
        assert main(ws + ["--json", "context", "show", "EX1"]) == 0
        value = json.loads(capsys.readouterr().out)["attributes"][key]["value"]
        assert main(ws + ["context", "set", "EX1", "--key", key, "--value", value, "--status", "engineer_confirmed", "--source", "Synthetic plate photo",
                          "--actor", "Engineer A", "--effective-from", "2026-03-01T00:00:00Z", "--evidence", "photo-1"]) == 0
    assert main(ws + ["investigate", cid, "--actor", "Engineer A"]) == 0
    out = capsys.readouterr().out
    assert "network            delay_observed" in out and "CHK-CLOCK-OFFSET" in out
    assert main(ws + ["review", cid, "CHK-SOURCE-ROWS", "--verdict", "accepted", "--reason", "coverage", "--actor", "Engineer A"]) == 0
    assert main(ws + ["execute", cid, "CHK-SOURCE-ROWS", "--actor", "Engineer A"]) == 0
    assert main(ws + ["record", cid, "decision", "--actor", "Engineer A", "--source", "call", "--add", "options_considered=Remote fix",
                      "--add", "options_considered=Dispatch", "--set", "chosen_option=Remote fix", "--set", "rationale=Network first"]) == 0
    capsys.readouterr()
    assert main(ws + ["record", cid, "action", "--actor", "Engineer A", "--source", "call", "--set", "decision_id=missing", "--set", "action_type=other",
                      "--set", "description=x", "--set", "status=performed"]) == 2
    assert "Linked decision" in capsys.readouterr().err
    report = tmp_path / "report.html"
    assert main(ws + ["report", cid, "--out", str(report)]) == 0
    html = report.read_text()
    assert "SYNTHETIC DATA" in html and "delay observed" in html and "Hash chain: <b class='ok'>valid" in html and "<script" not in html
    assert main(ws + ["ledger", cid, "--verify"]) == 0
    assert main(ws + ["ledger", cid, "--export", str(tmp_path / "e.jsonl")]) == 0
    assert (tmp_path / "e.jsonl").read_text().count("veyra-episode-v3") == 1


def test_cli_demo_runs_end_to_end(tmp_path, capsys):
    assert main(["--workspace", str(tmp_path / "ws"), "demo", "--out-dir", str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    assert "Ledger chain valid: True" in out and list((tmp_path / "out").glob("*.html"))


def test_report_escapes_hostile_source_text(store, tmp_path):
    hostile = (FIXTURES / FILES[0][0]).read_text().replace("console->vehicle-router", "<script>alert(1)</script>", 3)
    case = store.create_case(machine_id="EX1", title="<img src=x onerror=alert(1)>", actor="Engineer A", synthetic=True,
                             symptom_start="2026-03-04T09:06:00Z", symptom_end="2026-03-04T09:07:30Z", symptom_source="x")
    store.commit_import(case["id"], hostile.encode(), "h.csv", profile(FILES[0][1]), actor="Engineer A")
    store.investigate(case["id"], actor="Engineer A")
    from app.workflows.report import render
    html = render(store, case["id"])
    assert "<script>alert(1)</script>" not in html and "<img src=x" not in html
