"""Known-label synthetic acceptance for scope discovery, receipts and executable checks."""
import json
from pathlib import Path

import pytest

from app import discovery
from app.engine import checks, entry
from app.ingest.profile import parse_profile
from app.store.sqlite_store import CaseStore, StoreError
from app.workflows import autopilot


def make_store(tmp_path, event="WARNING: controller watchdog", event_times=(20,), measurement=True):
    store = CaseStore(tmp_path / "entry.db")
    case = store.create_case(machine_id="SYN", title="Synthetic unknown scope", actor="Test", synthetic=True)
    profile = parse_profile({"profile_id": "synthetic-entry", "version": 1, "format": "jsonl", "synthetic": True,
        "columns": {k: k for k in ("timestamp", "asset", "signal", "value", "unit", "event_value")},
        "signal_map": {"rtt": "network.rtt", "event": "controller.event"},
        "clock_domain": "test-clock", "timestamp_format": "epoch", "epoch_unit": "s"})
    data = [{"timestamp": 1700000000 + t / 10, "asset": "SYN", "signal": "rtt", "value": 40, "unit": "ms"}
            for t in range(1000)] if measurement else []
    data += [{"timestamp": 1700000000 + t, "asset": "SYN", "signal": "event", "event_value": event} for t in event_times]
    store.commit_import(case["id"], "\n".join(json.dumps(r) for r in data).encode(), "synthetic.jsonl", profile, actor="Test")
    return store, case["id"]


def test_unknown_scope_discovers_and_executes_all_candidates_without_declaring_normal(tmp_path):
    store, cid = make_store(tmp_path, event_times=(20, 70))
    try:
        run = store.investigate(cid, actor="Test")
        assert run["entry"]["status"] == "candidate_windows"
        assert len(run["entry"]["candidates"]) == 2
        assert len(run["candidate_investigations"]) == 1
        assert store.case(cid)["symptom_start"] is None  # does not rewrite an engineer's report
        for item in [run] + run["candidate_investigations"]:
            assert item["symptom_window"] and item["reference"]["status"] == "missing"
            assert item["diagnostic_execution"]["completed"] > 0
            assert item["root_cause"] == "unconfirmed"
            assert all(r["input_hashes"] == run["input_hashes"] and r["run_id"] == item["id"]
                       for r in item["diagnostic_execution"]["analyses"])
        candidate = run["entry"]["candidates"][0]
        assert candidate["support"][0]["source_rows"][0]["import_id"] in run["inputs"]
        assert candidate["input_hashes"] == run["input_hashes"]
        assert next(c for c in run["checks"] if c["check_id"] == "CHK-SOURCE-ROWS")["availability"]["state"] == "ready_automatic"
        store.append(cid, "check_review", {"run_id": run["id"], "check_id": "CHK-SOURCE-ROWS", "verdict": "accepted", "reason": "Read-only test"}, actor="Test")
        executed = store.execute_automatic(cid, "CHK-SOURCE-ROWS", actor="Test")
        assert executed["result"]["count"] > 0
        assert store.verify(cid)["ledger_chain"]["valid"]
    finally:
        store.close()


@pytest.mark.parametrize("event,measurement", [("INFO: logging started", True), ("No fault", True), ("WARNING: watchdog", False)])
def test_no_credible_scope_requests_minimum_information_and_never_claims_success(tmp_path, event, measurement):
    store, cid = make_store(tmp_path, event=event, measurement=measurement)
    try:
        run = store.investigate(cid, actor="Test")
        assert run["symptom_window"] is None
        assert run["entry"]["status"] == "insufficient_evidence"
        assert run["entry"]["missing_information"]
        assert run["diagnostic_execution"]["completed"] == 0
        assert run["diagnostic_execution"]["status"] == "insufficient_evidence"
        check = next(c for c in run["checks"] if c["check_id"] == "CHK-SOURCE-ROWS")
        assert check["availability"]["state"] == "needs_data" and not checks.eligible(check)
        assert autopilot.advance(store, cid)["stage"] == "needs_information"
        with pytest.raises(StoreError, match="window"):
            store.execute_automatic(cid, "CHK-SOURCE-ROWS", actor="Test")
        assert not any(e["kind"] == "check_execution" for e in store.ledger(cid))
    finally:
        store.close()


@pytest.mark.parametrize("value,unit,clock", [(float("nan"), "ms", "a"), (float("inf"), "ms", "a"), (True, "ms", "a"), (500, "s", "a"), (500, "ms", None)])
def test_invalid_samples_do_not_define_an_incident(value, unit, clock):
    rows = [{"t": i, "kind": "measurement", "signal": "network.rtt", "value": value, "unit": unit, "clock_domain": clock} for i in range(30)]
    assert not entry.discover([{"id": "i", "sha256": "s", "rows": rows}])["candidates"]


def test_single_outlier_is_not_a_credible_window():
    rows = [{"t": i, "kind": "measurement", "signal": "network.rtt", "value": 500 if i == 20 else 40,
             "unit": "ms", "clock_domain": "a"} for i in range(40)]
    assert not entry.discover([{"id": "i", "sha256": "s", "rows": rows}])["candidates"]


class Recording:
    px4 = False
    format = "synthetic-observer"
    clock = "a"
    start_s = 0
    end_s = 40
    topics = {"/state": {"type": "mavros_msgs/State", "count": 40}}

    def read(self, names, t0=None, t1=None):
        for i in range(40):
            yield "/state", i, {"armed": i > 20}

    def fields(self, name, msg):
        return [("armed", "number")]

    def get(self, name, msg, field):
        return msg.get(field)

    def close(self):
        pass


def test_available_probe_is_not_executed_without_incident(monkeypatch):
    monkeypatch.setattr(discovery, "open_source", lambda path: Recording())
    out = discovery.discover(Path("synthetic"), [])
    assert out["inventory"]["eligible_for_probes"] == ["/state"]
    assert out["inventory"]["metadata_inspected"] == ["/state"]
    assert out["inventory"]["read_by_probes"] == []
    assert out["execution"]["status"] == "not_executed"


def test_failed_probe_has_failure_receipt_and_no_false_completed_analysis(monkeypatch):
    monkeypatch.setattr(discovery, "open_source", lambda path: Recording())
    def fail(*args):
        raise ValueError("Synthetic decoder failed")
    monkeypatch.setattr(discovery, "probe", fail)
    out = discovery.discover(Path("synthetic"), [{"id": "one", "t": 20}])
    assert out["execution"]["status"] == "failed"
    assert out["execution"]["probes"][0]["status"] == "failed"
    assert out["inventory"]["read_by_probes"] == []
    assert "Synthetic decoder failed" in out["incidents"][0]["probe_error"]


def test_successful_probe_reports_only_observed_reads(monkeypatch):
    monkeypatch.setattr(discovery, "open_source", lambda path: Recording())
    out = discovery.discover(Path("synthetic"), [{"id": "one", "t": 20}])
    assert out["execution"]["status"] == "completed"
    assert out["inventory"]["read_by_probes"] == ["/state"]
    assert out["execution"]["probes"][0]["reads"][0]["samples_read"]["/state"] == 40


def test_legacy_ready_check_still_validates_scope():
    check = {"check_id": "CHK-SOURCE-ROWS", "availability": {"state": "ready_automatic"}, "eligibility": {"eligible": True}}
    assert checks.execution_blocker(check, {"symptom_window": None})


def test_candidate_clock_domains_are_never_silently_joined():
    rows = [{"t": i, "kind": "measurement", "signal": "network.rtt", "value": 40, "unit": "ms", "clock_domain": "a"} for i in range(40)]
    rows.append({"t": 20, "kind": "event", "signal": "controller.event", "event_value": "WARNING: watchdog", "clock_domain": "b"})
    result = entry.discover([{"id": "i", "sha256": "s", "rows": rows}])
    assert result["status"] == "insufficient_evidence" and result["rejected_anchors"]


def test_failed_diagnostic_is_not_a_success_receipt(tmp_path, monkeypatch):
    from app.engine import investigate as engine
    store, cid = make_store(tmp_path)
    def fail(*args, **kwargs):
        raise ValueError("Synthetic diagnostic failure")
    monkeypatch.setattr(engine.outputs, "detect", fail)
    try:
        run = store.investigate(cid, actor="Test")
        receipt = next(r for r in run["diagnostic_execution"]["analyses"] if r["tool"] == "actuator_output_health")
        assert receipt["status"] == "failed" and "Synthetic diagnostic failure" in receipt["reason"]
        assert run["diagnostic_execution"]["status"] == "partial"
    finally:
        store.close()


def test_new_snapshot_and_run_never_inherit_old_candidate_receipts(tmp_path):
    store, cid = make_store(tmp_path)
    try:
        first = store.investigate(cid, actor="Test")
        second = store.investigate(cid, actor="Test")
        assert first["id"] != second["id"]
        assert first["input_hashes"] == second["input_hashes"]
        assert all(r["run_id"] == second["id"] for r in second["diagnostic_execution"]["analyses"])
        assert store.runs(cid)[0]["run_hash"] == first["run_hash"]
        assert store.verify(cid)["runs_intact"]
    finally:
        store.close()
