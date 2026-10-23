"""SYNTHETIC stand-in for a the operator's systems, so the automatic pipeline can be shown end to end.

- A mock fleet API (`/mock-fleet/...`) serves the synthetic EX1 console and vehicle logs as JSON,
  filtered by time, the way a remote-operation platform's telemetry API would.
- An export folder holds the synthetic hydraulics CSV.
- A robot-log folder holds an MCAP recording of boom command and response, generated from the
  synthetic fixtures on first use.
- A webhook source maps a fleet platform's alert payload onto Veyra events.
Nothing here is private data or a real integration.
"""
from __future__ import annotations

import csv
import io
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.connectors.robotlogs import write_mcap_json

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "fixtures" / "synthetic" / "remote_delay"
RUNTIME = ROOT / ".local-runtime"
DEMO_SECRET = "synthetic-demo-webhook-secret"  # demo only; real sources use a secret from the environment
DEMO_SOURCE = "fleet-alerts-demo"


def _profile(name: str, **override: Any) -> Dict[str, Any]:
    data = json.loads((FIXTURES / name).read_text())
    data.update(override)
    return data


def _t(value: str) -> float:
    if value.isdigit():
        return int(value) / 1000.0
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def mock_records(stream: str, machine: str, start: str, end: str) -> List[Dict[str, Any]]:
    if machine != "EX1":
        return []
    lo, hi = _t(start), _t(end)
    if stream == "console":
        rows = list(csv.DictReader(io.StringIO((FIXTURES / "synthetic_ex1_console.csv").read_text())))
        return [r for r in rows if lo <= _t(r["time"]) <= hi]
    if stream == "vehicle":
        rows = [json.loads(line) for line in (FIXTURES / "synthetic_ex1_vehicle.jsonl").read_text().splitlines() if line.strip()]
        return [r for r in rows if lo <= r["ts"] / 1000.0 <= hi]
    raise ValueError(f"Unknown stream {stream}")


def robot_log_folder() -> Path:
    """Write the synthetic MCAP once: boom command and response from the 10 Hz fixture (vehicle clock)."""
    folder = RUNTIME / "demo-robot-logs"
    target = folder / "synthetic_ex1_boom.mcap"
    if not target.exists():
        folder.mkdir(parents=True, exist_ok=True)
        rows = list(csv.DictReader(io.StringIO((FIXTURES / "synthetic_ex1_vehicle_io.csv").read_text())))
        messages = [(1 if r["name"] == "rx_cmd_boom" else 2, int(r["ts"]) / 1000.0, {"value": float(r["val"])}) for r in rows]
        target.write_bytes(write_mcap_json([{"id": 1, "topic": "/boom/command"}, {"id": 2, "topic": "/boom/rate"}], messages))
    return folder


def demo_config(api_base: Optional[str] = None) -> Dict[str, Any]:
    base = (api_base or os.environ.get("VEYRA_API_BASE") or "http://127.0.0.1:8019").rstrip("/")
    io_meta = _profile("synthetic_vehicle_io_profile.json")["source_metadata"]
    return {
        "schema": "veyra-connectors-v1", "synthetic_demo": True,
        "connectors": [
            {"id": "fleet-api", "kind": "rest_api", "synthetic": True, "machines": ["EX1"], "base_url": f"{base}/mock-fleet",
             "allowed_hosts": ["127.0.0.1", "localhost"],
             "streams": [
                 {"name": "console", "path": "/v1/machines/{machine}/console?from={start}&to={end}", "records_key": "records",
                  "profile": _profile("synthetic_console_profile.json", format="json")},
                 {"name": "vehicle", "path": "/v1/machines/{machine}/vehicle?from={start_ms}&to={end_ms}", "records_key": "records",
                  "profile": _profile("synthetic_vehicle_profile.json", format="json")}]},
            {"id": "export-folder", "kind": "folder", "synthetic": True, "machines": ["EX1"], "path": str(FIXTURES),
             "patterns": [{"glob": "synthetic_ex1_hydraulics.csv", "profile": _profile("synthetic_hydraulics_profile.json")}]},
            {"id": "robot-logs", "kind": "robot_logs", "synthetic": True, "machines": ["EX1"], "path": str(robot_log_folder()),
             "globs": ["*.mcap"], "clock_domain": "vehicle_controller_clock", "time_source": "log_time", "source_metadata": io_meta,
             "signals": [{"topic": "/boom/command", "field": "value", "signal": "vehicle.command_input", "unit": "%", "scope": "boom"},
                         {"topic": "/boom/rate", "field": "value", "signal": "vehicle.actuator_response", "unit": "deg/s", "scope": "boom"}]},
        ],
        "webhooks": {"sources": {DEMO_SOURCE: {
            "synthetic": True, "machine_aliases": {"EX-01": "EX1"},
            "fields": {"event_id": "id", "machine_id": "asset.id", "occurred_at": "time", "type": "kind", "title": "summary",
                       "description": "details", "operator": "operator.name", "stated_reason": "operator.reason"},
            "type_map": {"OPERATOR_TAKEOVER": "takeover", "ALARM": "alert", "TICKET": "ticket"}}}},
        "automation": {"symptom_before_s": 90, "symptom_after_s": 30, "acquire_before_s": 600, "acquire_after_s": 300},
    }


def demo_event_payload() -> Dict[str, Any]:
    """What the (synthetic) fleet platform would post when the operator pressed takeover."""
    return {"id": f"SYN-{uuid.uuid4().hex[:10]}", "kind": "OPERATOR_TAKEOVER", "time": "2026-03-04T09:06:58Z",
            "asset": {"id": "EX-01", "site": "Synthetic quarry"}, "summary": "Operator takeover: boom kept moving after release",
            "details": "Takeover button pressed on the remote console (synthetic event)",
            "operator": {"name": "Operator A (synthetic)", "reason": "Boom kept moving after I released the joystick, so I took over and stopped it"},
            "generated_at": datetime.now(timezone.utc).isoformat()}
