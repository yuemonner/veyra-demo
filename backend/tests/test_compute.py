"""Compute layer: sandbox isolation, known-answer methods, cross-case authorisation, follow-up questions. SYNTHETIC data."""
import json

import pytest

from app.agent.llm import ScriptedModel
from app.agent.runner import run_agent
from app.agent.tools import Toolbox
from app.compute import evalcases, methods, sandbox
from app.store.sqlite_store import CaseStore

SERIES = {"a": {"t": [0, 1, 2, 3], "v": [1, 2, 3, 10], "unit": "deg", "clock": "c", "clock_max_skew_s": None, "signal": "x", "scope": None,
                "numeric": True, "case_id": "k", "window": "all", "sources": ["i#1", "i#2", "i#3", "i#4"], "provenance": {"rows": 4}}}

pytestmark = pytest.mark.skipif(not sandbox.isolation()["strong"], reason="no operating-system sandbox on this computer")


@pytest.fixture(scope="module")
def evalstore(tmp_path_factory):
    store = CaseStore(tmp_path_factory.mktemp("eval") / "engine.db")
    ids = evalcases.build(store)
    yield store, ids
    store.close()


def test_sandbox_runs_analysis_and_records_provenance():
    out = sandbox.run("result = {'max': float(max(series['a'].v)), 'unit': series['a'].unit}", SERIES)
    assert out["ok"] and out["result"] == {"max": 10.0, "unit": "deg"}
    prov = out["provenance"]
    assert len(prov["code_sha256"]) == 64 and len(prov["inputs_sha256"]) == 64 and prov["inputs"] == [{"rows": 4}] and "no network" in prov["isolation"]


@pytest.mark.parametrize("code, refused", [
    ("import socket\nresult = 1", "import socket"),
    ("import importlib\nimportlib.import_module('socket')\nresult = 1", "socket"),
    ("__import__('subprocess')\nresult = 1", "subprocess"),
    ("import os\nos.system('true')\nresult = 1", "os.system"),
    ("open('/tmp/veyra_should_not_exist', 'w').write('x')\nresult = 1", "writing outside"),
    ("result = open('/etc/passwd').read()", "reading files outside"),
    ("import os\nresult = os.listdir('/')", "outside its own folder"),
])
def test_sandbox_refuses_network_processes_and_files(code, refused):
    out = sandbox.run(code, SERIES)
    assert not out["ok"] and refused in out["error"]


def test_sandbox_limits_time_and_needs_a_result():
    slow = sandbox.run("while True:\n    pass", SERIES, limits={"wall_s": 5, "cpu_s": 2})
    assert not slow["ok"] and ("CPU" in slow["error"] or "time limit" in slow["error"])
    assert "without setting `result`" in sandbox.run("x = 1", SERIES)["error"]
    assert sandbox.run("open('own.txt', 'w').write('ok')\nresult = open('own.txt').read()", SERIES)["result"] == "ok"


def test_methods_give_the_known_answers(evalstore):
    store, ids = evalstore
    box = Toolbox(store, ids["timing"], may_share=lambda cid: True)
    order = box.call("run_method", {"method": "onset_order", "inputs": {"a": {"signal": "joint.torque"}, "b": {"signal": "joint.position"},
                                                                         "b_minus": {"signal": "joint.position_command"}}})["result"]
    assert order["verdict"] == "a_first" and abs(order["gap_s"] - 0.5) < 0.05
    unknown = box.call("run_method", {"method": "onset_order", "inputs": {"a": {"signal": "network.rtt"}, "b": {"signal": "joint.torque"}}})["result"]
    assert unknown["verdict"] == "cannot_tell" and "no declared" in unknown["clocks"]
    before = box.call("run_method", {"method": "onset", "inputs": {"a": {"signal": "joint.torque", "case_id": ids["earlier"]}}})["result"]
    assert before["departed"] is False
    normal = box.call("query_signal", {"signal": "joint.position_command", "window": "reference"})
    assert normal["unit"] == "deg" and abs(normal["stats"]["median"] - 30) < 0.5
    catalog = {c["signal"]: c for c in box.call("signal_catalog", {})["signals"]}
    assert catalog["joint.position"]["converted_from"] == ["rad"] and catalog["network.rtt"]["clock_max_skew_s"] is None
    assert len(box.computations) == 3 and all(c["provenance"]["inputs"] for c in box.computations)


def test_onset_order_respects_a_declared_clock_offset(evalstore):
    store, ids = evalstore
    data = Toolbox(store, ids["timing"]).data
    torque = data.series(ids["timing"], "joint.torque")
    rtt = {**data.series(ids["timing"], "network.rtt"), "clock_max_skew_s": 0.3}
    assert methods.onset_order(data, rtt, torque)["verdict"] == "a_first"          # 1 s apart, ±0.4 s uncertainty
    rtt_loose = {**rtt, "clock_max_skew_s": 2.0}
    assert methods.onset_order(data, rtt_loose, torque)["verdict"] == "cannot_tell"  # ±2.1 s swallows the 1 s gap


def test_units_convert_only_between_known_pairs(evalstore):
    store, ids = evalstore
    data = Toolbox(store, ids["timing"]).data
    deg = data.series(ids["timing"], "joint.position_command", window="reference")
    rad = {**deg, "case_id": "other", "unit": "rad", "v": [v * 3.141592653589793 / 180 for v in deg["v"]]}
    nm = {**deg, "case_id": "third", "unit": "Nm"}
    out = methods.compare_runs(data, [deg, rad, nm])
    assert [round(c["median"], 3) for c in out["cases"]] == [round(out["cases"][0]["median"], 3)] * 2
    assert out["cases"][1]["conversion"].startswith("converted from rad") and out["left_out"][0]["case_id"] == "third"


def test_other_cases_need_authorisation_before_the_model_sees_them(evalstore, tmp_path):
    store, ids = evalstore
    box = Toolbox(store, ids["timing"])
    with pytest.raises(ValueError, match="not been authorised"):
        box.call("query_signal", {"signal": "joint.torque", "case_id": ids["earlier"]})
    listed = box.call("find_cases", {"machine_id": "QD-7"})["cases"]
    assert [c["case_id"] for c in listed] == [ids["earlier"]] and listed[0]["data_available_to_model"] is False


def test_team_methods_are_versioned_and_run_in_the_sandbox(evalstore):
    store, ids = evalstore
    lib = methods.Library(store.path)
    saved = lib.save(name="peak abs", description="Largest absolute value", code="result = float(max(abs(x) for x in series['a'].v))", inputs=["a"], author="Test")
    assert saved["version"] and lib.get("peak abs")["previous_versions"] == 0
    box = Toolbox(store, ids["timing"])
    out = box.call("run_method", {"method": "peak abs", "inputs": {"a": {"signal": "joint.torque"}}})
    assert out["ok"] and abs(out["result"] - 4.2) < 0.05 and out["provenance"]["method"]["version"] == saved["version"]
    with pytest.raises(ValueError):
        lib.save(name="onset", description="", code="result = 1", inputs=["a"], author="Test")


def _question_responses(premise, answer_ids=("q2",)):
    return [
        {"content": [{"type": "tool_use", "id": "q1", "name": "case_overview", "input": {}}]},
        {"content": [{"type": "tool_use", "id": "q2", "name": "query_signal", "input": {"signal": "joint.torque", "window": "symptom"}}]},
        {"content": [{"type": "tool_use", "id": "s1", "name": "submit_report", "input": {
            "summary": "Torque fell in the symptom window.", "observations": [{"text": "Torque readings in the symptom window", "evidence": ["q2"]}],
            "hypotheses": [], "proposed_checks": [], "questions_for_engineers": [], "uncertainty": "Joint not inspected.", **premise}}]},
        {"content": [{"type": "tool_use", "id": "s2", "name": "submit_report", "input": {
            "summary": "Torque fell in the symptom window.", "observations": [{"text": "Torque readings in the symptom window", "evidence": ["q2"]}],
            "hypotheses": [], "proposed_checks": [], "questions_for_engineers": [], "uncertainty": "Joint not inspected.",
            "premise": {"status": "not_supported", "explanation": "Torque did not increase; it dropped.", "evidence": list(answer_ids)},
            "answer": {"text": "The torque dropped in the symptom window rather than increasing.", "evidence": list(answer_ids)}}}]},
    ]


def test_follow_up_question_needs_a_cited_premise_check_and_answer(evalstore):
    store, ids = evalstore
    model = ScriptedModel(_question_responses({}))
    entry = run_agent(store, ids["timing"], model=model, question="Why did the LF_KFE torque increase before the limp?")
    payload = entry["payload"]
    feedback = json.dumps(model.calls[3]["messages"][-1])
    assert "premise is missing" in feedback and "answer is missing" in feedback
    assert payload["citations_valid"] and payload["question"].startswith("Why did") and payload["report"]["premise"]["status"] == "not_supported"
    assert evalcases.score(evalcases.QUESTIONS[3], payload)["grade"] == "correct"
    submit = next(t for t in model.calls[0]["tools"] if t == "submit_report")
    assert submit and "run_python" in model.calls[0]["tools"]


def test_scoring_penalises_confident_wrong_answers_more_than_abstaining():
    q = evalcases.QUESTIONS[0]
    wrong = {"citations_valid": True, "report": {"premise": {"status": "supported", "explanation": ""}, "answer": {"text": "Position moved first, then torque."}}}
    abstain = {"citations_valid": True, "report": {"premise": {"status": "cannot_tell", "explanation": ""}, "answer": {"text": "Cannot tell from these clocks."}}}
    right = {"citations_valid": True, "report": {"premise": {"status": "supported", "explanation": ""}, "answer": {"text": "Torque dropped first, 0.5 s before position departed."}}}
    assert [evalcases.score(q, p)["score"] for p in (wrong, abstain, right)] == [-2, 0, 1]
