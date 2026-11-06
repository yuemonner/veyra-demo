"""Blind investigation replay: evidence freeze, leakage audit, step judge, AI verifier and harness."""
import json
import os
import shutil
import subprocess
import sys
from datetime import date

import pytest

from app.agent import step_judge as J
from app.agent import timeline as T

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.join(HERE, "..", "research", "investigation_benchmark")
CASE = os.path.join(BENCH, "cases", "shuttle-synthetic")
sys.path.insert(0, BENCH)
import run as H  # noqa: E402


def _lines():
    return open(os.path.join(CASE, "log.txt"), encoding="utf-8").read().split("\n")


def _space():
    return json.load(open(os.path.join(CASE, "hypotheses.json"), encoding="utf-8"))


def test_freeze_keeps_only_rows_before_cutoff_and_maps_lines():
    lines = _lines()
    fl, orig, audit = T.freeze(lines, date(2026, 10, 20))
    rows = [l for l in fl if T.ROW_TS.match(l)]
    assert rows and all(l[:10] < "2026-10-20" for l in rows)
    assert all(lines[o - 1] == l or l.startswith("# ----") for l, o in zip(fl, orig))
    assert T.leak_check(fl, date(2026, 10, 20), T.sealed_phrases(json.load(open(os.path.join(CASE, "truth.sealed.json"))))) == []
    assert audit["dropped_rows"] > 0


@pytest.mark.parametrize("inject", ["2026-10-25T07:00:00.000 MCU_FR DC_V=50", "# FR inverter replaced on 2026-10-21", "# problem came back"])
def test_freeze_removes_injected_leaks(inject):
    lines = _lines() + [inject] if inject[0] == "2" else [inject] + _lines()
    fl, _, _ = T.freeze(lines, date(2026, 10, 20))
    assert inject not in fl
    assert T.leak_check(lines, date(2026, 10, 20))


def test_sealed_phrase_that_survives_the_freeze_is_flagged():
    phrases = T.sealed_phrases({"main_cause_text": "discoloured terminal", "location": "FR HV connector"})
    lines = ["# operator note: look at the terminal"] + _lines()
    fl, _, _ = T.freeze(lines, date(2026, 10, 20))
    assert any(l.startswith("# operator note") for l in fl)
    assert any(x["kind"] == "sealed phrase" for x in T.leak_check(fl, date(2026, 10, 20), phrases))


def test_result_known_on_reads_later_dates_in_decided_by():
    s = {"when": "2026-10-21", "decided_by": "10 月 28 日又急停了一次"}
    assert H.result_known_on(s) == date(2026, 10, 28)
    assert H.result_known_on({"when": "2026-10-21", "decided_by": "ok"}) == date(2026, 10, 21)


def test_prior_steps_hide_results_not_yet_known():
    case = H.load_case(CASE)
    pri = {p["id"]: p for p in H.prior_steps(case, date(2026, 10, 22))}
    assert pri["s2"]["result"] == "pending" and pri["s2"]["decided_by"] is None
    assert "s3" not in pri


def _state(cutoff):
    fl, orig, _ = T.freeze(_lines(), cutoff)
    return J.State(fl, orig, _space(), cutoff).to_json()


def test_state_never_eliminates_only_weakens_and_cites():
    st = _state(date(2026, 10, 20))
    assert "contact" in st["open"]
    assert st["hypotheses"]["hsa_control"]["status"] == "not_identifiable"
    for h in st["hypotheses"].values():
        assert h["status"] in ("supported", "weakened", "no_support", "not_identifiable")
        if h["status"] in ("weakened", "no_support"):
            assert h["facts"]
    for f in st["facts"]:
        if f["tier"] == "confirmed":
            assert f["lines"] or f["line_ranges"]


def test_stale_facts_are_downgraded_for_the_step():
    st = _state(date(2026, 10, 21))
    step = {"id": "s2", "check": "更换 FR 电机逆变器（MCU_FR）", "part": "FR 逆变器", "kind": "swap", "result": "not_the_issue", "when": "2026-10-21", "cost": 1800}
    j = J.judge_step(step, st, _space(), [], {})
    assert j["targets"] == ["inverter"]
    assert all(k["tier_for_now"] != "confirmed" for k in j["known_before"] if k["fact"] and k["stale_days"])
    assert j["data_before"]["inverter"]["tier"] != "confirmed"
    assert j["hindsight"]["verified_avoidable"] is False
    assert j["knowledge_candidate"]["status"] == "unapproved"


def test_reading_codes_tests_no_hypothesis():
    st = _state(date(2026, 10, 20))
    j = J.judge_step({"id": "s1", "check": "读取遥控器故障码和 FR 逆变器的 DTC 历史", "kind": "check", "result": "inconclusive", "when": "2026-10-20"}, st, _space(), [], {})
    assert j["targets"] == [] and j["information_gathering"]


def test_ai_interpretation_is_held_to_the_facts():
    from app.agent.llm import ScriptedModel
    st = _state(date(2026, 10, 21))
    step = {"id": "s2", "check": "更换 FR 电机逆变器（MCU_FR）", "kind": "swap", "result": "not_the_issue", "when": "2026-10-21"}
    j = J.judge_step(step, st, _space(), [], {})
    bad = {"type": "tool_use", "id": "a", "name": "submit_step_judgment",
           "input": {"claims": [{"text": "The connector was obviously the cause", "fact_ids": [], "tier": "confirmed"}], "eliminates": [{"hypothesis": "battery", "fact_ids": ["f1"]}]}}
    good = {"type": "tool_use", "id": "b", "name": "submit_step_judgment",
            "input": {"claims": [{"text": "FR's drop per amp was about three times the others", "fact_ids": ["f1"], "tier": "inferred"}]}}
    out = J.ai_interpret(ScriptedModel([{"content": [bad]}, {"content": [good]}]), j, st)
    assert out["status"] == "accepted" and len(out["rejected"]) == 1
    assert any("cites no fact" in p for p in out["rejected"][0]) and any("eliminates 'battery'" in p for p in out["rejected"][0])
    # f1 is 12 days old at this step, so even citing it, 'confirmed' is too strong
    assert J.verify_ai({"claims": [{"text": "x", "fact_ids": ["f1"], "tier": "confirmed"}]}, j, st)


def test_blind_runner_refuses_a_pack_with_extra_files(tmp_path):
    case = H.load_case(CASE)
    fp = H.freeze_point(case, date(2026, 10, 20), [], str(tmp_path))
    shutil.copy(os.path.join(CASE, "truth.sealed.json"), fp["pack"])
    with pytest.raises(RuntimeError):
        H.run_veyra(fp["pack"])


def test_leaked_case_voids_every_point(tmp_path):
    d = tmp_path / "leaky"
    shutil.copytree(CASE, d)
    log = (d / "log.txt").read_text(encoding="utf-8")
    (d / "log.txt").write_text("# depot note: inspect the terminal first\n" + log, encoding="utf-8")
    case = H.load_case(str(d))
    phrases = T.sealed_phrases(json.load(open(d / "truth.sealed.json")))
    fp = H.freeze_point(case, date(2026, 10, 20), phrases, str(tmp_path / "w"))
    assert fp["leaks"] and fp["leaks"][0]["kind"] == "sealed phrase"


def test_synthetic_case_end_to_end():
    r = H.run_case(CASE)
    assert r["void_points"] == []
    assert all(t["pass"] for t in r["leak_selftest"]) and all(t["pass"] for t in r["verifier_selftest"])
    sc = r["score"]
    assert sc["unsupported"] == []
    assert sc["avoidable"]["verified"] == []                      # nothing verified: every relevant fact was stale
    assert [e["hypothesis"] for e in sc["wrong_eliminations"]] == ["battery"]
    assert all(m["main_open"] for m in sc["M4"])
    assert any(w["hypothesis"] == "hsa_control" for w in sc["worse_than_engineers"] if "hypothesis" in w)
