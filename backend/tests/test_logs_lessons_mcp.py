"""Text logs (app/ingest/textlog.py, app/discovery/logs.py), lessons (app/engine/lessons.py) and the MCP server.
SYNTHETIC data only (fixtures/synthetic/crazyflie are reconstructions, labelled as such)."""
import json
from pathlib import Path

import pytest

from app import mcp_server
from app.discovery import logs
from app.ingest import textlog
from app.store.sqlite_store import CaseStore
from app.workflows import crazyflie_demo as cf

CF = Path(__file__).resolve().parents[2] / "fixtures" / "synthetic" / "crazyflie"


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return p


# ---------- reading any text log
def test_timestamps_levels_kinds_and_locations(tmp_path):
    p = _write(tmp_path, "mixed.log", "\n".join([
        "2026-03-18T21:43:36.709Z [ERROR] [motor_driver] overcurrent on joint 3",
        "2026-03-18T21:43:37.100Z [ WARN] [nav] Connection to ground station lost",
        "2026-03-18T21:43:38.000Z kernel: Out of memory: Killed process 1234 (planner)",
        "2026-03-18T21:43:39.000Z Assertion failed at src/drivers/uart.c:88",
    ]))
    log = textlog.read(p)
    assert log.clock == "iso" and log.timestamped_share == 1.0
    kinds = [x.kind for x in log.lines]
    assert kinds == [None, "link_loss", "out_of_memory", "assert"]
    assert log.lines[0].level == "ERROR" and log.lines[1].level == "WARNING" and log.lines[3].location == "uart.c:88"
    assert log.lines[1].t_ns - log.lines[0].t_ns == 391_000_000
    ros = textlog.read(_write(tmp_path, "ros.log", "[ INFO] [1536693598.701565]: started\n[ERROR] [1536693599.000000]: Dropped 12 events (seq=101)\n"))
    assert ros.clock == "epoch" and ros.lines[1].template == "[ERROR] : Dropped # events (seq=#)"


def test_a_log_without_timestamps_keeps_order_only(tmp_path):
    log = textlog.read(CF / "cf_B_inflight_restart.log", anchor_ns=1_600_000_000_000_000_000)
    assert log.clock == "line_order" and any("order only" in n for n in log.notes)
    rows = textlog.rows(log, "CF")
    assert {r["signal"] for r in rows} >= {"controller.reset", "controller.event"}
    assert all(b["t"] > a["t"] for a, b in zip(rows, rows[1:]))


def test_restart_causes_and_the_watchdog_after_a_fault_is_a_consequence():
    a = logs.characterize(textlog.read(CF / "cf_A_hlcommander_watchdog.log"))
    assert len(a["restarts"]) == 1 and a["restarts"][0]["primary_cause"] == "assert"
    assert "consequence" in a["restarts"][0]["note"] and "at:queue.c:1429" in a["signature_tokens"]
    b = logs.characterize(textlog.read(CF / "cf_B_inflight_restart.log"))
    assert [r["primary_cause"] for r in b["restarts"]] == ["watchdog"]          # the first, ordinary boot is not a restart
    assert "reset:unknown" not in b["signature_tokens"]
    assert any("stack-overflow" in x["what"] for x in b["absences"])
    assert b["checks"][0]["topic"] == "debugger"


def test_a_check_reported_as_done_is_not_asked_again():
    check = {"topic": "stack", "check": "Log per-task stack high-water marks"}
    assert logs.already_done(check, ["Restarted. Stack overflow already ruled out by checking high-water marks."])
    assert not logs.already_done(check, ["Restarted in flight."])


# ---------- upload -> investigation -> lessons
@pytest.fixture()
def demo(tmp_path):
    store = CaseStore(tmp_path / "cf.db")
    out = cf.run_sample(store, "crazyflie-restart")
    yield store, out["case_id"]
    store.close()


def test_uploaded_log_is_investigated_and_the_lesson_is_checked_not_copied(demo):
    store, cid = demo
    found = store.discovery(cid)
    assert found["source"]["format"] == "text_log" and found["log"]["restarts"][0]["primary_cause"] == "watchdog"
    q = store.questions(cid)
    assert q["applicable"] and q["brief"]
    by_status = {}
    for x in q["questions"]:
        by_status.setdefault(x["status"], []).append(x)
    assert any("Watchdog reset" in x["answer"] for x in by_status["answered"])
    assert by_status["reported_done"] and "Stack overflow already ruled out" in by_status["reported_done"][0]["answer"]
    assert q["next"]["question"].startswith("Where was each task waiting") and "lines of the log" in q["next"]["why_a_person"]
    lesson = q["lessons"]["lessons"][0]
    assert lesson["verdict"] == "check_first" and "at:queue.c:1429" in lesson["missing_here"]
    assert lesson["status"] == "verified" and lesson["done_here"] and "counterexample" in lesson["reuse"]
    run = store.latest_run(cid)
    assert "stack overflow" not in json.dumps(run["hypotheses"]).lower()  # the lesson never edits this case's own conclusions
    assert store.verify(cid)["ledger_chain"]["valid"]


def test_the_same_signature_again_applies(tmp_path):
    store = CaseStore(tmp_path / "again.db")
    cf.ensure_lesson(store)
    from app.workflows.debrief import debrief_recording
    out = debrief_recording(store, CF / "cf_A_hlcommander_watchdog.log", {}, machine_id="CF-LAB-09", title="Same failure on another drone (SYNTHETIC)",
                            actor="t", synthetic=True, report={"stated_reason": "Fell after takeoff", "symptom": "stopped_unexpectedly",
                                                               "affected_function": "flight"})
    lesson = store.questions(out["case_id"])["lessons"]["lessons"][0]
    assert lesson["verdict"] == "applies" and "fix held there" in lesson["reuse"]
    store.close()


# ---------- MCP server and agent tools
def test_mcp_server_lists_and_calls_read_only_tools(demo, monkeypatch):
    store, cid = demo
    monkeypatch.setenv("VEYRA_ENGINE_DB", str(store.path))
    init = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
    assert init["result"]["serverInfo"]["name"] == "veyra-evidence-engine"
    assert mcp_server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    names = {t["name"] for t in mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]}
    assert {"list_cases", "recording_discovery", "log_lines", "signal_chain", "lessons"} <= names and "submit_report" not in names
    res = mcp_server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "log_lines", "arguments": {"case_id": cid, "pattern": "watchdog"}}})
    body = json.loads(res["result"]["content"][0]["text"])
    assert not res["result"]["isError"] and body["matches"] == 1 and body["lines"][0]["time"] is None   # no invented times on a line-order log
    real = store.create_case(machine_id="R1", title="real", actor="a", synthetic=False)
    refused = mcp_server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "case_overview", "arguments": {"case_id": real["id"]}}})
    assert refused["result"]["isError"] and "model_authorization" in refused["result"]["content"][0]["text"]
    assert mcp_server.handle({"jsonrpc": "2.0", "id": 5, "method": "nope"})["error"]["code"] == -32601
