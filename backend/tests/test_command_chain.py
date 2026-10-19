"""Command-chain stage latency on SYNTHETIC fixtures with two clocks and an undeclared offset."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.analysis.command_chain import (
    INCONSISTENT, STAGE_MISSING, UNAVAILABLE_CROSS_CLOCK, AMBIGUOUS, stage_latencies,
)
from app.ingest.pipeline import ingest
from app.ingest.profile import parse_profile

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "synthetic" / "remote_delay"
NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)


def rows():
    out = []
    for data, profile in (("synthetic_ex1_console.csv", "synthetic_console_profile.json"), ("synthetic_ex1_vehicle.jsonl", "synthetic_vehicle_profile.json")):
        result = ingest((FIXTURES / data).read_bytes(), data, parse_profile(json.loads((FIXTURES / profile).read_text())), "EX1", now=NOW)
        out += [{**r, "import_id": data} for r in result.rows]
    return out


def test_cross_clock_transport_latency_is_unavailable_without_declared_skew():
    result = stage_latencies(rows(), {"console_clock": None, "vehicle_controller_clock": None})
    network = result["segments"]["command.sent->command.received"]
    assert result["command_count"] == 30
    assert network["status_counts"] == {UNAVAILABLE_CROSS_CLOCK: 30} and network["availability"] == "unavailable"
    controller = result["segments"]["command.received->command.output"]
    assert controller["computed_count"] == 30 and 15 <= controller["median_ms"] <= 25
    vehicle = result["segments"]["command.output->vehicle.feedback"]
    assert vehicle["computed_count"] == 30 and 120 <= vehicle["min_ms"] and vehicle["max_ms"] < 161
    segment = result["commands"][0]["segments"]["command.received->command.output"]
    assert segment["from_import"] == "synthetic_ex1_vehicle.jsonl" and segment["from_row"] is not None


def test_declared_skew_yields_values_with_explicit_uncertainty():
    result = stage_latencies(rows(), {"console_clock": 1.0, "vehicle_controller_clock": 1.0})
    network = result["segments"]["command.sent->command.received"]
    assert network["uncertain_count"] == 30 and network["uncertainty_ms"] == pytest.approx(2000.0)
    # The undeclared 0.8 s offset dominates these values: uncertainty larger than the effect is reported, not hidden.
    assert network["uncertain_median_ms"] > 800 and "median_ms" not in network


def test_incident_window_keeps_vehicle_side_stages_comparable():
    start = datetime(2026, 3, 4, 9, 6, tzinfo=timezone.utc).timestamp()
    end = datetime(2026, 3, 4, 9, 7, 30, tzinfo=timezone.utc).timestamp()
    during = stage_latencies(rows(), {"console_clock": None, "vehicle_controller_clock": None}, window=(start, end, "console_clock"))
    assert during["command_count"] == 3
    assert during["segments"]["command.received->command.output"]["max_ms"] < 30


def test_missing_duplicate_and_inconsistent_stages():
    base = {"clock_domain": "c", "command_id": "X"}
    data = [
        {**base, "signal": "command.received", "t": 10.0, "row": 1}, {**base, "signal": "command.output", "t": 9.9, "row": 2},
        {**base, "command_id": "Y", "signal": "command.output", "t": 5.0, "row": 3}, {**base, "command_id": "Y", "signal": "vehicle.feedback", "t": 5.1, "row": 4},
        {**base, "command_id": "Y", "signal": "vehicle.feedback", "t": 5.2, "row": 5},
    ]
    result = stage_latencies(data, {"c": None})
    x, y = result["commands"]
    assert x["segments"]["command.received->command.output"]["status"] == INCONSISTENT
    assert x["segments"]["command.sent->command.received"]["status"] == STAGE_MISSING
    assert y["segments"]["command.output->vehicle.feedback"]["status"] == AMBIGUOUS
    assert "cause" in result["boundary"].lower() and "Not a cause" in result["boundary"]
