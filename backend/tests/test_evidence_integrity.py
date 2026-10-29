"""Evidence integrity before deployment, on REAL public logs: the micro-ROS walkthrough (STM32H7, UDP agent log) and the
ArduPlane SITL crash (a different log format). Four conditions:
1. an area is ruled out only on direct exclusion; anything else is weakened, also for outputs recorded earlier;
2. "normal" is case-local by default; a machine baseline needs evidence, conditions and a confirmation, and later
   investigations see it as context, never as a reason to hide the event;
3. closing keeps engineer acceptance, the symptom after the fix, root-cause verification and machine restoration apart,
   and a click never makes a verified lesson;
4. an engineer's result is passed on item by item, with Veyra's own description of an outcome kept apart from what was
   observed."""
import json

import pytest

from app.agent import investigator as inv
from app.agent.llm import ScriptedModel
from app.engine import lessons
from app.ledger.model import LedgerError
from app.store.sqlite_store import CaseStore
from app.workflows import public_samples


@pytest.fixture()
def store(tmp_path):
    s = CaseStore(tmp_path / "w.db")
    yield s
    s.close()


def _microros(store):
    return public_samples.seed_showcase(store)[0]


def _ardupilot(store, machine=None):
    cid = public_samples.run_sample(store, "ardupilot-sitl-startup-crash")["case_id"]
    if machine:
        store.db.execute("UPDATE cases SET machine_id=? WHERE id=?", (machine, cid))
        store.db.commit()
    return cid


def _authorise(store, cid):
    store.append(cid, "model_authorization", {"provider": "anthropic", "scope": "test", "authorized_by": "t", "evidence": "test"}, actor="t")


def _scripted(responses):
    m = ScriptedModel(responses)
    m.provider = "anthropic"
    return m


# ---------------------------------------------------------------- 1. fault-space elimination
def test_recorded_walkthrough_does_not_rule_out_the_network_path_from_a_host_capture(store):
    cid = _microros(store)
    stored = [e for e in store.ledger(cid) if e["kind"] == "agent_report"][-1]["payload"]["result"]
    assert {h["area"]: h["status"] for h in stored["hypotheses"]}["Network path"] == "ruled_out"     # the record itself is unchanged
    shown = inv.latest(store, cid)["result"]
    status = {h["area"]: h["status"] for h in shown["hypotheses"]}
    assert status["Network path"] == "weakened" and status["Agent / host side"] == "weakened" and status["Board firmware / transport"] == "leading"
    assert "ruled_out" not in status.values()
    net = next(h for h in shown["hypotheses"] if h["area"] == "Network path")
    assert "no direct exclusion" in net["integrity"]
    assert all(not o["rules_out"] and o["weakens"] for o in shown["next_action"]["outcomes"])     # branches weaken, not exclude
    assert shown["integrity"]["version"] == inv.INTEGRITY_VERSION
    record = inv.record(store, cid)
    assert not any(c.get("changes") and c["changes"]["ruled_out"] for c in record["chain"])


def _report(hyp_status, basis, raw_first_line):
    return {"headline": "SITL crashed at startup", "status": "cause_unknown", "confidence": "low", "explanation": "",
            "evidence": [{"line": 1, "quote": raw_first_line[:30], "shows": "crash", "calls": ["a1"]}],
            "checked": [{"check": "Read the log", "result": "see tool", "verdict": "inconclusive", "calls": ["a1"]}],
            "hypotheses": [{"id": "h1", "area": "Parameter config", "claim": "x", "status": "leading", "because": "x", "lines": [1]},
                           {"id": "h2", "area": "Build / host environment", "claim": "x", "status": hyp_status, "because": "x",
                            **({"basis": basis} if basis else {})}],
            "events": [],
            "next_action": {"action": "Bisect the parameters", "why": "x", "how": ["x"], "where": "lab",
                            "outcomes": [{"label": "Crash follows a parameter", "means": "x", "keeps": ["h1"], "rules_out": [], "weakens": ["h2"]},
                                         {"label": "No parameter matters", "means": "x", "keeps": ["h2"], "rules_out": ["h1"], "weakens": []}]},
            "unknowns": ["x"]}


def test_a_new_run_must_name_direct_exclusion_or_the_area_stays_weakened(store):
    cid = _ardupilot(store)
    _authorise(store, cid)
    raw = inv.LogTools(store, cid).raw
    indirect = _report("ruled_out", "indirect", raw[0])
    model = _scripted([{"content": [{"type": "tool_use", "id": "a1", "name": "overview", "input": {}}]},
                       {"content": [{"type": "tool_use", "id": "a2", "name": "submit_report", "input": indirect}]},
                       {"content": [{"type": "tool_use", "id": "a3", "name": "submit_report", "input": indirect}]},
                       {"content": [{"type": "tool_use", "id": "a4", "name": "submit_report", "input": indirect}]}])
    r = inv.investigate(store, cid, model=model)
    assert "without direct exclusion" in model.calls[2]["messages"][-1]["content"][0]["content"]
    h2 = next(h for h in r["result"]["hypotheses"] if h["id"] == "h2")
    assert h2["status"] == "weakened" and h2["integrity"]                         # stored demoted, with why
    assert r["result"]["next_action"]["outcomes"][1]["rules_out"] == ["h1"]      # a declared direct branch exclusion is kept
    direct = _report("ruled_out", "direct", raw[0])
    direct["hypotheses"][1]["observed_at"] = "a clean rebuild on a second host"
    direct["hypotheses"][1]["excluded_by"] = "data"
    model2 = _scripted([{"content": [{"type": "tool_use", "id": "b1", "name": "overview", "input": {}}]},
                        {"content": [{"type": "tool_use", "id": "b2", "name": "submit_report", "input": {**direct, "evidence": [{**direct["evidence"][0], "calls": ["b1"]}],
                                                                                                         "checked": [{**direct["checked"][0], "calls": ["b1"]}]}}]}])
    r2 = inv.investigate(store, cid, model=model2)
    assert next(h for h in r2["result"]["hypotheses"] if h["id"] == "h2")["status"] == "ruled_out"


def test_an_engineer_report_alone_never_rules_an_area_out(store):
    cid = _ardupilot(store)
    _authorise(store, cid)
    raw = inv.LogTools(store, cid).raw
    rep = _report("ruled_out", "direct", raw[0])
    rep["hypotheses"][1].update(observed_at="engineer's rebuild", excluded_by="engineer_report")
    model = _scripted([{"content": [{"type": "tool_use", "id": "a1", "name": "overview", "input": {}}]}] +
                      [{"content": [{"type": "tool_use", "id": f"a{i}", "name": "submit_report", "input": rep}]} for i in range(2, 5)])
    r = inv.investigate(store, cid, model=model)
    assert "engineer's report" in model.calls[2]["messages"][-1]["content"][0]["content"]
    h2 = next(h for h in r["result"]["hypotheses"] if h["id"] == "h2")
    assert h2["status"] == "weakened" and "not verified" in h2["integrity"]


# ---------------------------------------------------------------- 2. machine-specific normal labels
def test_normal_is_case_local_by_default_and_a_baseline_needs_evidence_scope_and_confirmation(store):
    a = _ardupilot(store, machine="SITL-1")
    b = _ardupilot(store, machine="SITL-1")
    e = store.append(a, "event_note", {"pattern": "ERROR: segmentation fault - aborting", "verdict": "normal"}, actor="Ana")
    assert e["payload"]["scope"] == "case"
    ctx_b = inv._case_context(store, b)
    assert ctx_b["dismissed_in_this_case"] == [] and ctx_b["machine_baselines"] == []      # nothing leaks to the next case
    assert inv._case_context(store, a)["dismissed_in_this_case"] == ["ERROR: segmentation fault - aborting"]
    for bad in ({"scope": "machine"}, {"scope": "machine", "evidence": "x", "applies_when": "y"},
                {"scope": "machine", "confirmed": True, "applies_when": "y"}, {"scope": "machine", "confirmed": True, "evidence": "x"}):
        with pytest.raises(LedgerError):
            store.append(a, "event_note", {"pattern": "Init ArduPlane", "verdict": "normal", **bad}, actor="Ana")
    store.append(a, "event_note", {"pattern": "Init ArduPlane", "verdict": "normal", "scope": "machine", "confirmed": True,
                                   "evidence": "Printed at every start in 20 healthy runs", "applies_when": "SITL builds of ArduPlane 4.6"}, actor="Ana")
    ctx_b = inv._case_context(store, b)
    assert ctx_b["dismissed_in_this_case"] == []
    assert ctx_b["machine_baselines"] == [{"pattern": "Init ArduPlane", "evidence": "Printed at every start in 20 healthy runs",
                                           "applies_when": "SITL builds of ArduPlane 4.6"}]
    assert "never a reason to hide an event" in inv.SYSTEM_PROMPT and "marked_normal" not in inv.SYSTEM_PROMPT


def test_microros_dismissal_stays_in_its_case(store):
    cid = _microros(store)
    store.append(cid, "event_note", {"pattern": "Agent start with port 457879 wrapped to 64663", "verdict": "normal"}, actor="Ana")
    other = public_samples.run_sample(store, "microros-stm32-session-drops")["case_id"]
    assert inv._case_context(store, other)["dismissed_in_this_case"] == []


# ---------------------------------------------------------------- 3. closure semantics
@pytest.mark.parametrize("sample", ["microros", "ardupilot"])
def test_yes_yes_yes_is_acceptance_and_observation_not_a_verified_cause(store, sample):
    cid = _microros(store) if sample == "microros" else _ardupilot(store)
    if sample == "microros":   # accepts the investigator's conclusion as it stands
        p = inv.review_to_closure(store, cid, conclusion="correct", symptom_after_fix="gone", machine_restored="yes",
                                  fix="Clean the D-cache over the Tx buffer before DMA")
    else:                      # no AI conclusion on this case: the engineer states one
        p = inv.review_to_closure(store, cid, conclusion="partly", correction="Tiltrotor motors used before QuadPlane setup finished",
                                  symptom_after_fix="gone", machine_restored="yes", fix="Set Q_TILT_TYPE before arming")
    e = store.append(cid, "case_closure", p, actor="Ana")["payload"]
    assert e["investigation_status"] == "cause_probable" and e["confirmed_by"] == "engineer_judgement"
    assert e["root_cause_verified"] is False and e["engineer_acceptance"] == ("accepted" if sample == "microros" else "partly")
    assert e["symptom_after_fix"] == "gone"
    assert e["resolution_status"] == "resolved" and e["fix_held"] == "not_yet_known"
    facts = inv.closure_facts(e)
    assert facts["root_cause"] == "Not verified: probable" and facts["machine"] == "Back in service" and "observation" in facts["symptom_after_fix"]
    lesson = lessons.lesson_for(store, store.case(cid))
    assert lesson["status"] == "candidate"                                      # never 'verified' from a click
    assert inv.record(store, cid)["chain"][-1]["facts"]["root_cause"] == "Not verified: probable"


def test_rejected_conclusion_without_a_correction_closes_without_a_cause(store):
    cid = _ardupilot(store)
    p = inv.review_to_closure(store, cid, conclusion="wrong", symptom_after_fix="no_fix", machine_restored="no")
    e = store.append(cid, "case_closure", p, actor="Ana")["payload"]
    assert e["investigation_status"] == "cause_unknown" and e["engineer_acceptance"] == "rejected" and e["resolution_status"] == "not_yet_repaired"


# ---------------------------------------------------------------- 4. observation provenance
def test_the_walkthrough_shows_every_item_passed_to_round_two(store):
    cid = _microros(store)
    finding = next(e["payload"] for e in store.ledger(cid) if e["kind"] == "engineer_finding")
    items = inv.finding_provenance(finding)
    kinds = [i["kind"] for i in items]
    assert kinds[0] == "selected" and items[0]["text"] == "Wire shows 36 zero bytes"
    assert "veyra_description" in kinds                                         # Veyra's own words, labelled as such
    observed = [i["text"] for i in items if i["kind"] == "observed"]
    assert "Tx buffer at sendto has the right XRCE bytes" in observed           # the CPU-visible buffer, stated as inspected
    assert any("tcpdump on the agent host" in t for t in observed) and "D-cache is enabled" in observed
    # everything round two received is in these items: the stored text is selected (description). observations
    import re
    norm = lambda t: re.sub(r"[.;]\s*", " ", t).split()  # noqa: E731
    rebuilt = f"{items[0]['text']} ({items[1]['text']}). " + "; ".join(observed)
    assert norm(rebuilt) == norm(finding["observations"])


def test_a_live_result_keeps_the_selected_outcome_apart_from_observations(store):
    cid = _ardupilot(store)
    store.append(cid, "engineer_finding", {"action": "Bisect the parameters", "outcome_selected": "Crash follows a parameter",
                                           "result": "found", "observations": "Q_TILT_TYPE=2 alone reproduces the crash"}, actor="Ana")
    f = next(e["payload"] for e in store.ledger(cid) if e["kind"] == "engineer_finding")
    assert [i["kind"] for i in inv.finding_provenance(f)] == ["selected", "observed"]
    ctx = inv._case_context(store, cid)["engineer_results"][0]
    assert ctx["outcome_selected"] == "Crash follows a parameter" and ctx["observations"] == "Q_TILT_TYPE=2 alone reproduces the crash"
    assert "Only the\n  observations are evidence" in inv.SYSTEM_PROMPT or "Only the observations are evidence" in inv.SYSTEM_PROMPT.replace("\n  ", " ")


def test_api_review_and_note_defaults(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    monkeypatch.setenv("VEYRA_ENABLE_RUNTIME", "true")
    monkeypatch.setenv("VEYRA_ALLOW_RECORDINGS", "true")
    monkeypatch.setenv("VEYRA_ENGINE_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("VEYRA_INVESTIGATOR", "rules")
    monkeypatch.delenv("VEYRA_PRIVATE", raising=False)
    from app.recordings_api import router as recordings_router
    from app.engine_api import router as engine_router
    st = CaseStore(tmp_path / "api.db")
    cid = public_samples.seed_showcase(st)[0]
    st.close()
    app = FastAPI()
    app.include_router(recordings_router)
    app.include_router(engine_router)
    c = TestClient(app)
    c.headers.update({"x-veyra-request": "1"})
    got = c.get(f"/engine/cases/{cid}/investigation").json()
    assert all(h["status"] != "ruled_out" for i in got["playback"]["investigations"] for h in i["result"]["hypotheses"])
    assert got["playback"]["findings"][0]["provenance"][1]["kind"] == "veyra_description"
    n = c.post(f"/engine/cases/{cid}/event-note", json={"pattern": "x", "verdict": "normal"}).json()
    assert n["payload"]["scope"] == "case"
    assert c.post(f"/engine/cases/{cid}/event-note", json={"pattern": "x", "verdict": "normal", "scope": "machine"}).status_code == 422
    r = c.post(f"/engine/cases/{cid}/review", json={"conclusion": "correct", "symptom_after_fix": "gone", "machine_restored": "yes", "fix": "clean D-cache"})
    assert r.status_code == 200, r.text
    assert r.json()["payload"]["investigation_status"] == "cause_probable" and r.json()["payload"]["root_cause_verified"] is False
