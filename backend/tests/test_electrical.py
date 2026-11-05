"""Electrical relations (app/physics/electrical.py, v2) on the SYNTHETIC shuttle log, whose generator sets each branch's
resistance (FL 8, FR 26, RL 9, RR 8 mΩ) and splits current equally. These are development checks: the blind validation
on an independent generator is research/electrical_validation (see PREREGISTRATION.md and RESULTS.md)."""
import json
import re
from pathlib import Path

import pytest

from app.agent import investigator as inv
from app.agent.llm import ScriptedModel
from app.physics import electrical as E
from app.store.sqlite_store import CaseStore
from app.workflows import public_samples

ROOT = Path(__file__).resolve().parents[2]
LOG = (ROOT / "fixtures/synthetic/demo/shuttle-hill-start-stop/log.txt").read_text().split("\n")


def test_measured_model_and_unobservable_are_kept_apart():
    r = E.analyse(LOG)
    b = next(f for f in r["findings"] if f["relation"] == "B_FR")
    m = next(f for f in r["findings"] if f["relation"] == "R_FR")
    assert b["tier"] == "measured" and "per amp of pack current" in b["text"] and "mΩ" not in b["text"]
    assert m["tier"] == "model" and any("not recorded" in a for a in m["assumptions"])
    assert "Each branch's DC current" in r["identifiability"]["unobservable"]
    lo, hi = r["model"]["branches"]["FR"]["r_interval_ohm"]
    assert lo < 0.026 < hi and hi - lo > 0.005          # model uncertainty, not just ±1 mΩ of noise
    assert 0.45 < m["values"]["s_star_pct"] / 100 < 0.56   # a normal branch would need about half the traction current
    sens = r["model"]["sensitivity"]["FR"]
    assert [x["r_ohm"] for x in sens] == sorted((x["r_ohm"] for x in sens), reverse=True)


def test_candidates_stay_open_and_torque_is_not_used_to_rule_out_current():
    r = E.analyse(LOG)
    a = r["assessments"][0]
    assert a["wheel"] == "FR" and set(a["open"]) == {"contact", "conductor", "inverter_draw"}
    st = {c["id"]: c["status"] for c in a["candidates"]}
    assert st["uneven_load"] == "weakened" and st["sensor_gain"] == "weakened"
    assert "ruled_out" not in json.dumps(r)
    assert "does not show similar DC current" in r["torque_note"]["text"]
    assert a["recommended"] == "segment_r" and a["then"]["do"] == "segment_dv_loaded"
    seg = next(m for m in a["measurements"] if m["id"] == "segment_r")
    assert seg["splits"] and "read normal at rest" in seg["blind_spot"]


def test_battery_comparison_names_what_else_differs():
    p = next(f for f in E.analyse(LOG)["findings"] if f["relation"] == "P")
    assert p["kind"] == "observation" and "soc" in p["values"]["confounds"] and "cannot attribute" in p["text"]


def test_a_voltage_sensor_offset_shows_at_rest_and_is_not_mistaken_for_resistance():
    def shift(line):
        return re.sub(r"DC_V=([\d.]+)", lambda m: f"DC_V={float(m.group(1)) - 1.0:.2f}", line) if " MCU_RL " in line else line
    r = E.analyse([shift(l) for l in LOG])
    assert "O_RL" in r["relations_broken"] and r["relations"]["branch"]["flagged"] == ["FR"]


@pytest.mark.parametrize("drop,expect", [(r"Pack_I=[\d.]+ ", "not_identifiable"), (r" DC_V=[\d.]+", "not_identifiable")])
def test_missing_signals_give_no_estimate(drop, expect):
    r = E.analyse([re.sub(drop, " ", l) for l in LOG])
    if r is None:
        return
    assert r["relations"]["branch"]["status"] in (expect, "not_applicable")
    assert not r["model"]["branches"] and not r["assessments"]
    assert not any(f["relation"].startswith(("B_", "R_")) for f in r["findings"])


def test_a_creeping_vehicle_is_not_identifiable():
    def creep(line):
        return re.sub(r"Pack_I=([\d.]+)", lambda m: f"Pack_I={min(float(m.group(1)), 12.0):.1f}", line)
    r = E.analyse([creep(l) for l in LOG])
    assert r["relations"]["branch"]["status"] == "not_identifiable" and r["relations"]["pack"]["status"] == "not_identifiable"
    assert not r["model"]["branches"] and not any(f["relation"] == "P" for f in r["findings"])


def test_only_logs_with_electrical_signals_are_analysed():
    micro = (ROOT / "fixtures/real/public/microros-stm32-session-drops/log.txt").read_text().split("\n")
    assert E.analyse(micro) is None and E.detect(LOG) is not None


def test_the_agent_view_fits_a_tool_result():
    v = E.agent_view(E.analyse(LOG))
    assert len(json.dumps(v)) < 24000 and "never call a model-derived resistance measured" in v["how_to_use"]


# ---------------------------------------------------------------- the investigation loop
GOOD = {"headline": "FR's branch drop does not add up", "status": "cause_unknown", "confidence": "medium", "explanation": "",
        "evidence": [], "checked": [{"check": "Electrical relations", "result": "B_FR (measured): FR's drop per amp is 3.2× the others'", "verdict": "supports", "calls": ["e1"]}],
        "hypotheses": [{"id": "h1", "area": "FR connector contact", "claim": "FR connector contact resistance", "status": "open", "because": "B_FR", "role": "main_cause"},
                       {"id": "h2", "area": "FR cable or fuse", "claim": "FR cable or fuse resistance", "status": "open", "because": "B_FR", "role": "main_cause"},
                       {"id": "h3", "area": "FR inverter draw", "claim": "FR inverter draws more DC current", "status": "open", "because": "B_FR", "role": "main_cause"}],
        "next_action": {"action": "Four-wire resistance of each FR segment, HV isolated, before reseating", "why": "separates contact, cable and inverter draw",
                        "how": ["isolate HV"], "where": "depot", "outcomes": [{"label": "connector high", "means": "contact", "keeps": ["h1"]}]},
        "unknowns": ["branch current"], "events": []}


def test_physics_check_returns_unsupported_claims():
    out = {"e1": json.dumps(E.agent_view(E.analyse(LOG)))}
    assert inv.check_physics(json.loads(json.dumps(GOOD)), out) == []
    bad = json.loads(json.dumps(GOOD))
    bad["checked"][0]["calls"] = []
    bad["headline"] = "FR branch resistance measured at 26 mΩ"
    bad["confidence"] = "high"
    bad["hypotheses"][0]["status"] = "leading"
    bad["hypotheses"][2].update(status="ruled_out", because="FR torque matches the other wheels")
    bad["next_action"]["action"] = "Replace the FR inverter"
    p = " | ".join(inv.check_physics(bad, out))
    for needle in ("cite it", "model-derived", "confidence cannot be high", "is ruled out", "similar torque", "lowest-cost discriminating"):
        assert needle in p, needle


@pytest.fixture()
def store(tmp_path):
    s = CaseStore(tmp_path / "w.db")
    yield s
    s.close()


def test_the_agent_can_call_the_electrical_tool_and_must_use_it(store):
    out = public_samples.run_sample(store, "shuttle-hill-start-stop")
    cid = out["case_id"]
    store.append(cid, "model_authorization", {"provider": "anthropic", "scope": "test", "authorized_by": "t", "evidence": "test"}, actor="t")
    tools = inv.LogTools(store, cid)
    assert any(t["name"] == "electrical" for t in inv.tool_specs(tools))
    bad = json.loads(json.dumps(GOOD))
    bad["hypotheses"][2].update(status="ruled_out", because="torque is equal", basis="direct", excluded_by="data")
    m = ScriptedModel([
        {"content": [{"type": "tool_use", "id": "e1", "name": "electrical", "input": {}}]},
        {"content": [{"type": "tool_use", "id": "s1", "name": "submit_report", "input": bad}]},
        {"content": [{"type": "tool_use", "id": "s2", "name": "submit_report", "input": GOOD}]},
    ])
    m.provider = "anthropic"
    r = inv.investigate(store, cid, model=m)
    assert r["mode"] == "agent" and r["steps"][0]["tool"] == "electrical" and "error" not in r["steps"][0]["summary"]
    assert "mechanisms the data cannot separate" in r["steps"][0]["summary"]
    returned = m.calls[2]["messages"][-1]["content"][0]["content"]
    assert "similar torque" in returned
    assert [h["status"] for h in r["result"]["hypotheses"]] == ["open", "open", "open"]


def test_without_a_model_the_electrical_findings_still_appear(store):
    out = public_samples.run_sample(store, "shuttle-hill-start-stop")
    r = inv.investigate(store, out["case_id"], model=None)
    checks = [c["check"] for c in r["result"]["checked"]]
    assert any("B_FR (measured)" in c for c in checks) and any("R_FR (model-derived)" in c for c in checks)
