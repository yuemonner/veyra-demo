"""Evidence acquisition: before Veyra asks a person anything it must have searched the data for it, and what the data
already answers is listed with the line that answers it (so nobody has to ask). Checked by verify(), no model involved."""
from app.agent import investigator as inv

RAW = ["2026-10-28T07:42:00.000 VCU_Status State=READY VCU_SW=2.4.1",
       "2026-10-28T07:42:00.000 BMS_Status Pack_V=72.37 Pack_I=4.0 SOC=38 DCL_A=150 T_cell_min=2",
       "2026-10-28T07:42:02.450 MCU_FR_Event ERROR DTC 0x2A11 DC bus under-voltage (54.3 V < 55.0 V for >= 50 ms)"]
OUT = {"c1": "matches 1 line 2 SOC=38 T_cell_min=2", "c2": "pattern connector|terminal matches 0"}
HYPS = [{"id": "h1", "area": "VCU", "claim": "x", "status": "leading", "because": "y"},
        {"id": "h2", "area": "FR harness", "claim": "x", "status": "open", "because": "y"}]


def _report(**extra):
    return {"headline": "x", "status": "cause_unknown", "confidence": "low", "evidence": [], "checked": [], "events": [],
            "unknowns": [], "hypotheses": HYPS,
            "next_action": {"action": "a", "why": "b", "how": [], "where": "on_site", "conditions": "cell temperature below 5 °C, SOC about 38",
                            "baseline": "the afternoon launch", "record": ["Pack_I", "MCU_FR.DC_V"], "needs_approval": True,
                            "outcomes": [{"label": "l", "means": "m", "keeps": ["h1"], "rules_out": []},
                                         {"label": "l2", "means": "m2", "keeps": ["h2"], "rules_out": []}]}, **extra}


def test_a_question_for_a_person_needs_a_search_of_the_data_first():
    q = {"question": "Was the FR terminal re-torqued after the swap?", "ask": "field_engineer", "why_needed": "splits h1 from h2",
         "not_in_data": "no signal reports terminal torque", "if_answers": [{"answer": "yes", "means": "m", "keeps": ["h1"]}, {"answer": "no", "means": "m", "keeps": ["h2"]}]}
    v = inv.verify(_report(field_questions=[{**q, "searched_calls": []}]), RAW, OUT)
    assert v["cleaned"]["field_questions"] == [] and any("before asking a person" in p for p in v["problems"])
    v = inv.verify(_report(field_questions=[{**q, "searched_calls": ["c2"]}]), RAW, OUT)
    assert len(v["cleaned"]["field_questions"]) == 1 and not any("field_questions" in p for p in v["problems"])
    v = inv.verify(_report(field_questions=[{**q, "searched_calls": ["c2"], "if_answers": [{"answer": "yes", "means": "m", "keeps": ["h9"]}, {"answer": "no", "means": "m"}]}]), RAW, OUT)
    assert any("unknown hypothesis" in p for p in v["problems"])


def test_what_the_data_already_answers_must_cite_the_line_that_answers_it():
    good = {"question": "What was the state of charge?", "answer": "SOC 38, cell temperature 2 °C", "line": 2, "quote": "SOC=38 DCL_A=150", "calls": ["c1"]}
    v = inv.verify(_report(already_answered=[good]), RAW, OUT)
    assert v["cleaned"]["already_answered"] == [good]
    wrong_quote = {**good, "quote": "SOC=61"}
    wrong_number = {**good, "answer": "SOC 61"}
    v = inv.verify(_report(already_answered=[wrong_quote, wrong_number]), RAW, OUT)
    assert v["cleaned"]["already_answered"] == [] and len([p for p in v["problems"] if "already_answered" in p]) == 2


def test_a_retest_carries_conditions_baseline_signals_and_approval():
    v = inv.verify(_report(), RAW, OUT)
    na = v["cleaned"]["next_action"]
    assert na["conditions"] and na["baseline"] and na["record"] == ["Pack_I", "MCU_FR.DC_V"] and na["needs_approval"] is True
    spec = inv.SUBMIT_SPEC["input_schema"]["properties"]["next_action"]["properties"]
    assert {"conditions", "baseline", "record", "needs_approval"} <= set(spec)
