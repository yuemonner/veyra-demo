"""The AI investigator on a structured recording: it does not read the recording whole. Veyra's parser builds an evidence
sheet (reproducible from the rows) and offers window / changes / relation / sequence / events / anomalies; the model
cites sheet lines and tool call ids, and the verifier holds it to them exactly as for a text log.

The recording here is SYNTHETIC (a roll setpoint the measured roll stops following at t=30 s), built in the test."""
import json
import math
from datetime import datetime, timedelta, timezone

import pytest

from app.agent import investigator as inv
from app.agent import recording_tools as rt
from app.agent.llm import ScriptedModel
from app.ingest import catalog
from app.ingest.profile import parse_profile
from app.store.sqlite_store import CaseStore


def _profile():
    columns = {"timestamp": "time", "asset": "machine", "signal": "signal", "value": "value", "unit": "unit", "scope": "scope",
               "event_value": "event", "code": "code", "command_id": "command_id"}
    return parse_profile({"profile_id": "veyra-canonical-csv", "version": 1, "format": "csv", "synthetic": True, "columns": columns,
                          "signal_map": {s["id"]: s["id"] for s in catalog.describe()["signals"]}, "clock_domain": "machine_clock",
                          "timestamp_format": "iso8601", "unmapped_signals": "retain"})


def _csv() -> bytes:
    t0 = datetime(2026, 1, 5, 10, 0, 0, tzinfo=timezone.utc)
    out = ["time,machine,signal,value,unit,scope,event,code,command_id"]
    for i in range(600):  # 60 s at 10 Hz
        t = i / 10
        sp = 20 * math.sin(t / 3)
        meas = sp + 0.5 * math.sin(7 * t) if t < 30 else sp * 0.2 + 0.5 * math.sin(7 * t)   # stops following at 30 s
        ts = (t0 + timedelta(seconds=t)).isoformat()
        out.append(f"{ts},UAV-T,control.attitude_setpoint,{sp:.4f},deg,roll,,,")
        out.append(f"{ts},UAV-T,vehicle.attitude,{meas:.4f},deg,roll,,,")
        out.append(f"{ts},UAV-T,propulsion.throttle,{50 + (5 if t > 30.2 else 0):.1f},%,,,,")
    out.append(f"{(t0 + timedelta(seconds=30.5)).isoformat()},UAV-T,controller.event,,,mode,FAILSAFE engaged,,")
    return ("\n".join(out) + "\n").encode()


@pytest.fixture()
def case(tmp_path):
    store = CaseStore(tmp_path / "w.db")
    c = store.create_case(machine_id="UAV-T", title="Roll stopped following (SYNTHETIC)", actor="t", synthetic=True)
    store.commit_import(c["id"], _csv(), "uav.csv", _profile(), actor="t", review_reason="test")
    yield store, c["id"]
    store.close()


def _run(store, cid):
    if store.latest_run(cid) is None:
        store.investigate(cid, actor="t")


class _Model(ScriptedModel):
    provider = "anthropic"

    def create(self, *, system, messages, tools):
        self.systems = getattr(self, "systems", []) + [system]
        return super().create(system=system, messages=messages, tools=tools)


def test_the_sheet_is_reproducible_and_finds_the_divergence(case):
    store, cid = case
    a, b = rt.RecordingTools(store, cid), rt.RecordingTools(store, cid)
    assert a.available() and a.sheet["sha256"] == b.sheet["sha256"] and a.raw == b.raw
    text = "\n".join(a.raw)
    assert "SERIES vehicle.attitude[roll]" in text and "EVENT t=30.5 s" in text
    rise = [l for l in a.raw if "followed its setpoint worse" in l or "diverged from its setpoint" in l]
    assert rise and any(float(x) >= 29 for l in rise for x in __import__("re").findall(r"from t=([\d.]+)", l))
    seq = a.sequence(28, 33, ["vehicle.attitude[roll]", "propulsion.throttle"], baseline_s=5)
    assert seq["left_baseline_in_order"][0]["series"] == "vehicle.attitude[roll]"     # the roll moved before the throttle
    rel = a.relation("control.attitude_setpoint[roll]", "vehicle.attitude[roll]", 0, 29)
    assert rel["correlation"] > 0.95
    assert a.relation("control.attitude_setpoint[roll]", "vehicle.attitude[roll]", 31, 60)["difference"]["typical"] > 3


def test_rules_only_on_a_recording_quotes_the_sheet(case):
    store, cid = case
    _run(store, cid)
    out = inv.deterministic(store, cid, "no model")
    raw = rt.RecordingTools(store, cid).raw
    assert out["status"] == "cause_unknown" and out["headline"].startswith("Strongest anomaly candidate")
    assert out["evidence"] and all(raw[e["line"] - 1].startswith(e["quote"][:40]) for e in out["evidence"])
    assert all(e["quote"] in raw[e["line"] - 1] for e in out["evidence"])


def test_the_agent_tests_hypotheses_with_recording_tools_and_is_held_to_them(case):
    store, cid = case
    _run(store, cid)
    tools = rt.RecordingTools(store, cid)
    n = next(i for i, l in enumerate(tools.raw, 1) if l.startswith("TRACKING vehicle.attitude[roll]"))
    rel = tools.relation("control.attitude_setpoint[roll]", "vehicle.attitude[roll]", 31, 60)
    typical = rel["difference"]["typical"]
    good = {"headline": "Roll stopped following its setpoint at about 30 s", "status": "cause_unknown", "confidence": "medium", "explanation": "",
            "evidence": [{"line": n, "quote": tools.raw[n - 1][:60], "shows": "the tracking check", "calls": ["r1"]}],
            "checked": [{"check": "Roll against setpoint after 31 s", "result": f"typical difference {typical} deg", "verdict": "supports", "calls": ["r3"]}],
            "next_action": {"action": "Inspect the roll actuator linkage", "why": "separates actuator from controller", "how": ["bench test"],
                            "where": "lab", "outcomes": [{"label": "Linkage loose", "means": "actuator", "keeps": ["h1"], "rules_out": []},
                                                         {"label": "Linkage fine", "means": "look at the controller", "keeps": ["h2"], "rules_out": []}]},
            "unknowns": ["x"], "events": [],
            "hypotheses": [{"id": "h1", "area": "Roll actuator", "claim": "the roll actuator stopped responding", "status": "leading", "because": "tracking"},
                           {"id": "h2", "area": "Controller", "claim": "the controller limited roll", "status": "open", "because": "not separated"}]}
    bad = json.loads(json.dumps(good))
    bad["checked"].append({"check": "Invented", "result": "roll error 777.7 deg", "verdict": "supports", "calls": ["r3"]})
    model = _Model([
        {"content": [{"type": "tool_use", "id": "r1", "name": "overview", "input": {}},
                     {"type": "tool_use", "id": "r2", "name": "sequence", "input": {"start_s": 28, "end_s": 33}},
                     {"type": "tool_use", "id": "r3", "name": "relation", "input": {"a": "control.attitude_setpoint[roll]", "b": "vehicle.attitude[roll]", "start_s": 31, "end_s": 60}}]},
        {"content": [{"type": "tool_use", "id": "r4", "name": "submit_report", "input": bad}]},
        {"content": [{"type": "tool_use", "id": "r5", "name": "submit_report", "input": good}]},
    ])
    r = inv.investigate(store, cid, model=model)
    assert r["mode"] == "agent" and [s["tool"] for s in r["steps"]] == ["overview", "sequence", "relation"]
    assert "777.7" in model.calls[2]["messages"][-1]["content"][0]["content"]       # the invented number went back
    assert r["result"]["evidence"] == good["evidence"]
    assert r["source"]["kind"] == "recording_sheet" and r["source"]["sha256"] == tools.sheet["sha256"] and r["source"]["sheet"] == tools.raw
    assert "RECORDING (signals over time)" in model.systems[0]
    assert set(model.calls[0]["tools"]) >= {"window", "changes", "relation", "sequence", "events", "anomalies"}
