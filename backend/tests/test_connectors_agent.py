"""Connectors, automatic pipeline and the investigation agent — on SYNTHETIC data and local test servers."""
import json
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.agent.llm import ScriptedModel
from app.agent.runner import AgentError, run_agent
from app.automation import demo, pipeline
from app.connectors import base
from app.connectors.files import S3Connector
from app.connectors.robotlogs import RobotLogConnector, read_bag, write_bag
from app.workflows.demo import FILES, FIXTURES
from app.ingest.profile import parse_profile
from app.store.sqlite_store import CaseStore

NOW = datetime(2026, 4, 1, tzinfo=timezone.utc)


class _Server:
    def __init__(self, handler):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def __exit__(self, *exc):
        self.httpd.shutdown()


class FleetHandler(BaseHTTPRequestHandler):
    seen_auth = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        parts = parsed.path.strip("/").split("/")  # mock-fleet/v1/machines/EX1/<stream>
        query = dict(urllib.parse.parse_qsl(parsed.query))
        FleetHandler.seen_auth.append(self.headers.get("Authorization"))
        try:
            records = demo.mock_records(parts[-1], parts[-2], query["from"], query["to"])
            body, code = json.dumps({"records": records}).encode(), 200
        except ValueError:
            body, code = b"{}", 404
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def store(tmp_path):
    s = CaseStore(tmp_path / "engine.db", clock=lambda: NOW)
    yield s
    s.close()


def _event_case(store, config):
    payload = demo.demo_event_payload()
    raw = json.dumps(payload).encode()
    ts = str(int(time.time()))
    sig = pipeline.sign("secret", ts, raw)
    pipeline.verify_signature("secret", ts, sig, raw)
    event = pipeline.normalize_event(demo.DEMO_SOURCE, payload, config["webhooks"]["sources"][demo.DEMO_SOURCE])
    return event, pipeline.handle_event(store, event, config=config)


def test_webhook_signature_and_mapping():
    raw = b'{"a":1}'
    ts = str(int(time.time()))
    with pytest.raises(pipeline.EventError, match="does not match"):
        pipeline.verify_signature("secret", ts, pipeline.sign("other", ts, raw), raw)
    with pytest.raises(pipeline.EventError, match="too old"):
        pipeline.verify_signature("secret", str(int(time.time()) - 3600), pipeline.sign("secret", str(int(time.time()) - 3600), raw), raw)
    cfg = demo.demo_config("http://127.0.0.1:1")["webhooks"]["sources"][demo.DEMO_SOURCE]
    event = pipeline.normalize_event(demo.DEMO_SOURCE, demo.demo_event_payload(), cfg)
    assert event["type"] == "takeover" and event["machine_id"] == "EX1" and event["stated_reason"].startswith("Boom kept moving")
    with pytest.raises(pipeline.EventError, match="not mapped"):
        pipeline.normalize_event(demo.DEMO_SOURCE, {**demo.demo_event_payload(), "kind": "UNKNOWN"}, cfg)


def test_event_opens_case_pulls_all_connectors_and_investigates(store):
    with _Server(FleetHandler) as url:
        config = demo.demo_config(url)
        event, result = _event_case(store, config)
        assert result["status"] == "opened" and result["imported_files"] == 4, result
        cid = result["case_id"]
        kinds = [e["kind"] for e in store.ledger(cid)]
        assert kinds[:3] == ["support_milestone", "trigger_event", "intervention_report"]
        acquisitions = {e["payload"]["connector_id"]: e["payload"] for e in store.ledger(cid) if e["kind"] == "acquisition"}
        assert set(acquisitions) == {"fleet-api", "export-folder", "robot-logs"}
        assert acquisitions["fleet-api"]["imported"] == 2 and acquisitions["robot-logs"]["items"][0]["notes"][0].startswith("Converted from MCAP")
        run = store.latest_run(cid)
        assert run["domains"]["network"]["status"] == "delay_observed"
        assert run["findings"]["transients"]["events_in_window"] >= 1
        assert run["interventions"][0]["operator"] == "Operator A (synthetic)"
        # The same event again is not a second case.
        again = pipeline.handle_event(store, event, config=config)
        assert again == {"case_id": cid, "status": "already_handled", "event_id": event["event_id"]}
        # Re-acquiring is safe: identical files are recognised as duplicates.
        first = acquisitions["fleet-api"]["window"]
        window = base.Window(datetime.fromisoformat(first[0].replace("Z", "+00:00")), datetime.fromisoformat(first[1].replace("Z", "+00:00")))
        records = pipeline.acquire(store, cid, base.build(config), actor="Engineer", window=window)
        assert all(i["status"] == "duplicate" for r in records for i in r["payload"]["items"])
        assert store.verify(cid)["ledger_chain"]["valid"]


def test_http_connector_refuses_unlisted_hosts_and_plain_http():
    with pytest.raises(base.ConnectorError, match="allowed_hosts"):
        base.http_get("https://evil.example.com/x", allowed_hosts=["fleet.example.com"])
    with pytest.raises(base.ConnectorError, match="Plain HTTP"):
        base.http_get("http://fleet.example.com/x", allowed_hosts=["fleet.example.com"])
    with pytest.raises(base.ConnectorError, match="looks like a secret"):
        base.validate_config({"connectors": [{"id": "a", "kind": "rest_api", "api_token": "abc"}]})


def test_s3_connector_signs_lists_and_reads(tmp_path, monkeypatch):
    content = (FIXTURES / "synthetic_ex1_hydraulics.csv").read_bytes()

    class S3Handler(BaseHTTPRequestHandler):
        auth = []

        def log_message(self, *a):
            pass

        def do_GET(self):
            S3Handler.auth.append(self.headers.get("Authorization", ""))
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path == "/bucket/":
                body = (b'<?xml version="1.0"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                        b"<IsTruncated>false</IsTruncated><Contents><Key>exports/EX1/synthetic_ex1_hydraulics.csv</Key><Size>1</Size></Contents>"
                        b"<Contents><Key>exports/EX1/other.bin</Key><Size>1</Size></Contents></ListBucketResult>")
            elif parsed.path == "/bucket/exports/EX1/synthetic_ex1_hydraulics.csv":
                body = content
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

    monkeypatch.setenv("S3_KEY", "AKIDEXAMPLE")
    monkeypatch.setenv("S3_SECRET", "secret")
    with _Server(S3Handler) as url:
        connector = S3Connector({"id": "s3", "kind": "s3", "synthetic": True, "bucket": "bucket", "region": "eu-central-1", "endpoint": url,
                                 "allowed_hosts": ["127.0.0.1"], "prefix": "exports/{machine}/", "access_key_env": "S3_KEY", "secret_key_env": "S3_SECRET",
                                 "patterns": [{"glob": "*_hydraulics.csv", "profile": json.loads((FIXTURES / "synthetic_hydraulics_profile.json").read_text())}]})
        batches = connector.fetch("EX1", base.Window(NOW, NOW))
    assert [b.filename for b in batches] == ["synthetic_ex1_hydraulics.csv"] and batches[0].content == content
    assert all(a.startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/") for a in S3Handler.auth)


def test_rosbag_connector_converts_jointstate_by_name(tmp_path):
    import struct
    definition = ("Header header\nstring[] name\nfloat64[] position\nfloat64[] velocity\nfloat64[] effort\n"
                  "================================================================================\nMSG: std_msgs/Header\nuint32 seq\ntime stamp\nstring frame_id\n")

    def joint_state(seq, t, velocity):
        out = struct.pack("<I", seq) + struct.pack("<II", int(t), int(round((t - int(t)) * 1e9))) + struct.pack("<I", 0)
        out += struct.pack("<I", 2) + b"".join(struct.pack("<I", len(n)) + n for n in (b"boom_joint", b"arm_joint"))
        for values in ([0.0, 0.0], [velocity, 9.9], [0.0, 0.0]):
            out += struct.pack("<I", 2) + struct.pack("<2d", *values)
        return out

    t0 = datetime(2026, 3, 4, 9, 6, tzinfo=timezone.utc).timestamp()
    bag = write_bag([{"conn": 0, "topic": "/joint_states", "type": "sensor_msgs/JointState", "definition": definition}],
                    [(0, t0 + i * 0.05, joint_state(i, t0 + i * 0.05, 0.01 * i)) for i in range(200)], compression="bz2")
    (tmp_path / "run1.bag").write_bytes(bag)
    assert len(list(read_bag(bag))) == 200
    connector = RobotLogConnector({"id": "bags", "kind": "robot_logs", "path": str(tmp_path), "globs": ["*.bag"], "clock_domain": "robot_clock",
                                   "time_source": "header", "signals": [{"topic": "/joint_states", "field": "velocity[boom_joint]",
                                                                         "signal": "vehicle.actuator_response", "unit": "rad/s", "scope": "boom"}]})
    batches = connector.fetch("EX1", base.Window(datetime.fromtimestamp(t0, timezone.utc), datetime.fromtimestamp(t0 + 4.99, timezone.utc)))
    rows = [json.loads(line) for line in batches[0].content.decode().splitlines()]
    assert len(rows) == 100 and rows[10]["value"] == pytest.approx(0.1) and rows[0]["unit"] == "rad/s"
    parse_profile(batches[0].profile)  # the generated profile is a valid import profile


def _scripted_agent_responses():
    return [
        {"content": [{"type": "tool_use", "id": "t1", "name": "case_overview", "input": {}}], "usage": {"input_tokens": 10, "output_tokens": 5}},
        {"content": [{"type": "tool_use", "id": "t2", "name": "compare_to_reference", "input": {}},
                     {"type": "tool_use", "id": "t3", "name": "brief_events", "input": {}},
                     {"type": "tool_use", "id": "t4", "name": "signal_stats", "input": {"signal": "network.rtt", "window": "symptom"}}]},
        {"content": [{"type": "tool_use", "id": "r1", "name": "submit_report", "input": {
            "summary": "The root cause is confirmed as the network.", "observations": [{"text": "RTT higher than reference", "evidence": ["t2"]}],
            "hypotheses": [{"statement": "Network delay", "status": "leading", "support": ["t2", "t4"]}],
            "proposed_checks": [{"action": "Export router logs", "why": "Confirm link events", "evidence": []}],
            "questions_for_engineers": ["Was the LTE cell changed?"], "uncertainty": "Clock offset unknown"}}]},
        {"content": [{"type": "tool_use", "id": "r2", "name": "submit_report", "input": {
            "summary": "Commands reached the machine late while the boom responded normally; network delay leads.",
            "observations": [{"text": "RTT 447 ms vs 52 ms in the reference window", "evidence": ["t2", "t4"]},
                             {"text": "Pressure spike 40 ms after the takeover", "evidence": ["t3"]}],
            "hypotheses": [{"statement": "Network delay before vehicle receipt", "domain": "network", "status": "leading", "support": ["t2", "t4"]},
                           {"statement": "Hydraulic fault", "domain": "vehicle_body", "status": "weakened", "support": ["t3"], "against": ["t2"]}],
            "proposed_checks": [{"check_id": "CHK-NETWORK-LOGS", "action": "Export router logs for the window", "why": "Link events would explain the delay", "evidence": ["t2"]}],
            "questions_for_engineers": ["Was the LTE cell changed?"], "uncertainty": "Console–vehicle clock offset is unknown"}}]},
    ]


def test_agent_cites_tools_rejects_root_cause_and_uncited_claims(store):
    case = store.create_case(machine_id="EX1", title="t", actor="Engineer", synthetic=True, symptom_start="2026-03-04T09:06:00Z",
                             symptom_end="2026-03-04T09:07:30Z", symptom_source="synthetic")
    for data, profile in FILES + (("synthetic_ex1_hydraulics.csv", "synthetic_hydraulics_profile.json"),):
        store.commit_import(case["id"], (FIXTURES / data).read_bytes(), data, parse_profile(json.loads((FIXTURES / profile).read_text())), actor="Engineer")
    store.investigate(case["id"], actor="Engineer")
    model = ScriptedModel(_scripted_agent_responses())
    entry = run_agent(store, case["id"], model=model)
    payload = entry["payload"]
    assert entry["kind"] == "agent_report" and payload["citations_valid"] is True
    assert [c["tool"] for c in payload["tool_calls"]] == ["case_overview", "compare_to_reference", "brief_events", "signal_stats"]
    first_feedback = json.dumps(model.calls[3]["messages"][-1])
    assert "confirmed root cause" in first_feedback and "cites no tool call" in first_feedback
    assert payload["report"]["hypotheses"][0]["status"] == "leading" and payload["egress"]["basis"] == "synthetic case"
    with pytest.raises(Exception, match="only be generated"):
        store.append(case["id"], "agent_report", {"run_id": "x"}, actor="someone")


def test_agent_refuses_real_private_data_without_authorization(store):
    case = store.create_case(machine_id="M7", title="real", actor="Engineer", synthetic=False)
    with pytest.raises(AgentError, match="model_authorization"):
        run_agent(store, case["id"], model=ScriptedModel([]))
    with pytest.raises(Exception, match="provider"):
        store.append(case["id"], "model_authorization", {"provider": "scripted-test", "scope": "x", "authorized_by": "y", "evidence": "z"}, actor="Engineer")


FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, os, sys
argv, prompt = sys.argv[1:], sys.stdin.read()
assert "-p" in argv and "--disallowedTools" in argv and "Bash" in argv[argv.index("--disallowedTools") + 1]
assert "ANTHROPIC_API_KEY" not in os.environ, "subscription sign-in must be used, not an API key"
assert "submit_report" in argv[argv.index("--system-prompt") + 1]
if "[result of cc_1]" not in prompt:
    reply = {"note": "Start with the overview.", "tool_calls": [{"name": "case_overview", "input": {}}]}
else:
    reply = {"note": "Report.", "tool_calls": [{"name": "submit_report", "input": {
        "summary": "Outputs held while the reference moved; the leading area is the vehicle body.",
        "observations": [{"text": "Overview read", "evidence": ["cc_1"]}],
        "hypotheses": [{"statement": "Actuator drive", "status": "leading", "support": ["cc_1"]}],
        "proposed_checks": [{"action": "Inspect the actuator feedback", "why": "Separates command from mechanics", "evidence": ["cc_1"]}],
        "questions_for_engineers": [], "uncertainty": "Single flight"}}]}
print(json.dumps({"type": "result", "is_error": False, "result": "```json\n" + json.dumps(reply) + "\n```",
                  "usage": {"input_tokens": 100, "output_tokens": 20}}))
'''


def test_claude_code_backend_runs_agent_through_local_cli(store, tmp_path, monkeypatch):
    from app.agent.llm import ClaudeCodeModel, from_environment
    fake = tmp_path / "claude"
    fake.write_text(FAKE_CLAUDE)
    fake.chmod(0o755)
    monkeypatch.setenv("VEYRA_CLAUDE_BIN", str(fake))
    monkeypatch.setenv("VEYRA_AGENT_BACKEND", "claude-code")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-reach-the-cli")
    model = from_environment()
    assert isinstance(model, ClaudeCodeModel) and model.available() and model.provider == "anthropic"
    case = store.create_case(machine_id="EX1", title="t", actor="Engineer", synthetic=True, symptom_start="2026-03-04T09:06:00Z",
                             symptom_end="2026-03-04T09:07:30Z", symptom_source="synthetic")
    for data, profile in FILES:
        store.commit_import(case["id"], (FIXTURES / data).read_bytes(), data, parse_profile(json.loads((FIXTURES / profile).read_text())), actor="Engineer")
    store.investigate(case["id"], actor="Engineer")
    payload = run_agent(store, case["id"], model=model)["payload"]
    assert payload["citations_valid"] and payload["model"] == "claude-code:sonnet"
    assert [c["tool"] for c in payload["tool_calls"]] == ["case_overview"] and payload["usage"]["output_tokens"] == 40


def test_claude_code_backend_reports_missing_cli(monkeypatch, tmp_path):
    from app.agent.llm import ClaudeCodeModel, ModelError
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("VEYRA_CLAUDE_BIN", raising=False)
    model = ClaudeCodeModel()
    if model.binary():  # a system-wide install in /usr/local or /opt/homebrew
        pytest.skip("claude installed system-wide on this machine")
    with pytest.raises(ModelError, match="not installed"):
        model.create(system="s", messages=[{"role": "user", "content": "x"}], tools=[])
