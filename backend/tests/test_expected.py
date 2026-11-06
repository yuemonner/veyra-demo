"""Expected vs observed behaviour (app/physics/expected.py) on the SYNTHETIC shuttle log: three launches on the same
ramp, the second ending in an emergency stop. Development checks; the spec is an example, not PIX's."""
import json
import re
from pathlib import Path

import pytest

from app.agent import investigator as inv
from app.agent.llm import ScriptedModel
from app.physics import expected as X
from app.store.sqlite_store import CaseStore
from app.workflows import public_samples

ROOT = Path(__file__).resolve().parents[2]
LOG = (ROOT / "fixtures/synthetic/demo/shuttle-hill-start-stop/log.txt").read_text().split("\n")


def _ep(r, i):
    return {x["id"]: x for x in r["snapshots"][i]["results"]}


def test_first_divergence_and_the_chain_of_the_stop():
    r = X.analyse(LOG)
    s = r["snapshots"][1]
    assert s["first_divergence"]["id"] == "cmd_within_limit" and s["first_divergence"]["layer"] == "controller"
    f = s["first_divergence"]["first"]
    assert f["value"] == 93.0 and f["ref"] == 80.2 and LOG[f["line"] - 1].strip() == f["quote"]
    kinds = [(c["kind"], c["id"]) for c in s["chain"]]
    assert kinds.index(("divergence", "bus_above_uv")) < kinds.index(("protection", "uv_trip")) < kinds.index(("symptom", "symptom"))
    assert _ep(r, 1)["uv_trip"]["status"] == "designed_response" and _ep(r, 1)["fault_stop"]["status"] == "designed_response"
    assert _ep(r, 1)["torque_follows"]["status"] == "met"          # torque cut by the protection is not a divergence
    assert s["context"]["VCU software"]["value"] == "2.4.1" and s["context"]["Coldest cell"]["value"] == 2.0


def test_contrast_across_episodes():
    r = X.analyse(LOG)
    c = {x["id"]: x for x in r["contrast"]}
    assert c["cmd_within_limit"]["violated_in"] == [2] and c["cmd_within_limit"]["met_in"] == [1, 3]
    assert r["snapshots"][2]["first_divergence"] is None


def test_what_the_data_cannot_determine_is_stated():
    lay = X.analyse(LOG)["layers"]
    blind = lay["physical"]["cannot_determine"]
    assert [b["id"] for b in blind] == ["epb_caliper_released"] and "caliper position" in blind[0]["needs"]
    assert {x["id"] for x in lay["controller"]["confirmed"]} >= {"vcu_drive", "epb_release_cmd", "uv_trip", "fault_stop"}
    assert len(lay["environment"]["observed"]) == 3


def test_a_missing_signal_becomes_not_observable_not_a_guess():
    r = X.analyse([re.sub(r" Speed_mps=[\d.]+", "", l) for l in LOG])
    st = _ep(r, 1)["vehicle_moves"]
    assert st["status"] == "not_observable" and "Veh.Speed_mps" in st["needs"]


def test_logs_without_a_matching_spec_are_not_analysed():
    micro = (ROOT / "fixtures/real/public/microros-stm32-session-drops/log.txt").read_text().split("\n")
    assert X.analyse(micro) is None
    assert X.analyse([re.sub(r" Tq_Act=[\d.-]+", "", l) for l in LOG]) is None


def test_agent_view_and_the_verifier():
    v = X.agent_view(X.analyse(LOG))
    assert len(json.dumps(v)) < 24000 and "cannot be determined" in v["how_to_use"]
    out = {"x1": json.dumps(v)}
    bare = {"checked": [], "unobservable": []}
    p = " | ".join(inv.check_expected(bare, out))
    assert "cite it" in p and "cannot determine" in p
    good = {"checked": [{"check": "expected", "result": "cmd_within_limit first", "verdict": "supports", "calls": ["x1"]}],
            "unobservable": [{"fact": "Whether the EPB calipers opened", "how_to_observe": "log EPB caliper position"}]}
    assert inv.check_expected(good, out) == []


@pytest.fixture()
def store(tmp_path):
    s = CaseStore(tmp_path / "w.db")
    yield s
    s.close()


def test_the_agent_can_call_it(store):
    cid = public_samples.run_sample(store, "shuttle-hill-start-stop")["case_id"]
    store.append(cid, "model_authorization", {"provider": "anthropic", "scope": "test", "authorized_by": "t", "evidence": "test"}, actor="t")
    tools = inv.LogTools(store, cid)
    assert any(t["name"] == "expected_behavior" for t in inv.tool_specs(tools))
    m = ScriptedModel([{"content": [{"type": "tool_use", "id": "x1", "name": "expected_behavior", "input": {}}]},
                       {"content": [{"type": "tool_use", "id": "s1", "name": "submit_report", "input": {"headline": "h"}}]}])
    m.provider = "anthropic"
    try:
        r = inv.investigate(store, cid, model=m)
    except Exception:  # noqa: BLE001  the scripted report is incomplete on purpose; only the tool step matters here
        r = None
    sent = m.calls[1]["messages"][-1]["content"][0]["content"]
    assert '"first_divergence"' in sent and "cmd_within_limit" in sent
    if r:
        assert r["steps"][0]["tool"] == "expected_behavior" and "first divergence" in r["steps"][0]["summary"]


def test_without_a_model_the_first_divergence_still_appears(store):
    cid = public_samples.run_sample(store, "shuttle-hill-start-stop")["case_id"]
    r = inv.investigate(store, cid, model=None)
    assert any(c["check"].startswith("Expected vs observed, episode 2") for c in r["result"]["checked"])
