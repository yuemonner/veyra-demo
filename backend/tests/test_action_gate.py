"""Action safety gate (Safety & Experiment Design discipline): proposed physical actions are checked deterministically and
BLOCKED when unsafe; safety findings the model leaves out are inserted. Reproduces CASE A's two known failures on SYNTHETIC
restart cycles (re-closures discharging below the original trip level). The model is scripted."""
import copy

import pytest

from app.agent import action_gate as G
from app.agent import investigator as inv
from app.agent import physical_layer as PL
from app.agent.llm import ScriptedModel
from app.physchecks import agent_bridge, synthetic as S
from app.physchecks.profile import Profile
from app.store.sqlite_store import CaseStore
from tests.test_recording_investigation import _csv, _profile  # noqa: F401

# the action the CASE A rerun v1 proposed, paraphrased (a live current comparison with HV closed)
CASE_A_LIKE = {"action": "Compare an independent clamp meter with BMS pack current at standstill", "why": "separates current from capacity",
               "how": ["With HV closed and the vehicle at standstill, clamp a calibrated DC meter on the pack cable", "Read the BMS current at the same moment"],
               "where": "on_site", "needs_approval": True, "template_id": "MEAS-INDEPENDENT-CURRENT",
               "outcomes": [{"label": "agree", "means": "not C", "keeps": ["h1"], "rules_out": []}, {"label": "differ", "means": "C", "keeps": ["h2"], "rules_out": []}]}


def tools(rec=None, profile=None):
    return agent_bridge.PhysicalCheckTools(rec or S.restart_cycles(), profile or S.profile())


def test_case_a_like_action_is_blocked_by_the_hold():
    t = tools()
    v = G.evaluate_action(CASE_A_LIKE, t.next_checks(), hv_system=True)
    assert v["verdict"] == "blocked" and [r["rule"] for r in v["rules"]] == ["G1"]
    assert v["rules"][0]["provenance"]["kind"] == "engineering_rule"


def test_same_action_without_template_is_also_unvalidated():
    na = dict(CASE_A_LIKE, template_id="none")
    rules = {r["rule"] for r in G.evaluate_action(na, tools().next_checks(), hv_system=True)["rules"]}
    assert rules == {"G1", "G3"}


@pytest.mark.parametrize("words", ["Put the vehicle in READY and log the current", "Charge the pack to full and record cell voltages",
                                   "Close the contactors and read the bus voltage", "上高压后读取电流"])
def test_energising_words_are_blocked_under_the_hold_whatever_the_template(words):
    na = {"action": words, "how": [], "template_id": "DOC-BMS-HISTORY", "needs_approval": True}
    assert "G1" in {r["rule"] for r in G.evaluate_action(na, tools().next_checks(), hv_system=True)["rules"]}


def test_the_holds_release_check_and_documents_pass():
    plan = tools().next_checks()
    rest = {"action": "Read every cell's rest voltage through the BMS diagnostic tool", "how": ["vehicle at rest, HV open"],
            "template_id": "MEAS-CELL-REST", "needs_approval": True}
    assert G.evaluate_action(rest, plan, hv_system=True)["verdict"] == "pass"
    doc = {"action": "Export the BMS charge and calibration history", "how": ["read-only export"], "template_id": "DOC-BMS-HISTORY"}
    assert G.evaluate_action(doc, plan, hv_system=True)["verdict"] == "pass"


def test_unknown_template_is_blocked():
    na = {"action": "Run the special test", "template_id": "MY-OWN-TEST"}
    assert "G2" in [r["rule"] for r in G.evaluate_action(na, tools().next_checks(), hv_system=True)["rules"]]


def test_without_a_hold_a_live_measurement_is_a_reviewed_handoff_not_a_block():
    plan = tools(S.abrupt_correction()).next_checks()
    assert not plan["safety_holds"]
    assert G.evaluate_action(CASE_A_LIKE, plan, hv_system=True)["verdict"] == "pass"
    v = G.evaluate_action(dict(CASE_A_LIKE, needs_approval=False), plan, hv_system=True)
    assert v["verdict"] == "pass" and v["outcome"] == "confirm" and v["requires_confirmation"]   # v6: the gate itself requires the engineer


def test_enforce_replaces_the_blocked_action_and_inserts_missing_findings():
    t = tools()
    report = {"headline": "x", "next_action": copy.deepcopy(CASE_A_LIKE)}
    out = G.enforce(report, t, hv_system=True)
    r, g = out["report"], out["gate"]
    assert g["action_verdict"] == "blocked" and g["blocked_actions"][0]["action"]["template_id"] == "MEAS-INDEPENDENT-CURRENT"
    assert r["next_action"]["template_id"] == "MEAS-CELL-REST" and r["next_action"]["replaced_by_gate"] is True
    assert r["next_action"]["provenance"]["kind"] == "engineering_rule"
    assert "HOLD-HV-RECLOSURE" in g["inserted_findings"] and "restart_eligibility:deeper_discharge_than_original_trip" in g["inserted_findings"]
    assert all(x.get("inserted_by") == G.GATE_VERSION for x in r["safety_findings"])
    assert report["next_action"]["template_id"] == "MEAS-INDEPENDENT-CURRENT"     # the model's report is not edited in place


def test_a_compliant_report_passes_unchanged():
    t = tools()
    refs = [m["ref"] for m in G.material_refs(t)]
    report = {"next_action": {"action": "Export the BMS charge and calibration history", "how": [], "template_id": "DOC-BMS-HISTORY"},
              "safety_findings": [{"ref": r, "statement": "noted"} for r in refs]}
    out = G.enforce(report, t, hv_system=True)
    assert out["gate"]["passed_unchanged"] and out["report"]["next_action"]["provenance"]["kind"] == "expert_proposal"


def test_drone_custom_actions_low_risk_pass_hazardous_blocked():
    p = copy.deepcopy(S.PROFILE)
    p.update(id="synthetic-drone", machine_class="px4_vehicle")
    plan = tools(S.abrupt_correction(), Profile(p)).next_checks()
    look = {"action": "Inspect the battery connector and lead visually", "how": ["photograph both ends"], "template_id": "none"}
    assert G.evaluate_action(look, plan, hv_system=False)["verdict"] == "pass"
    fly = {"action": "Fly the drone again to reproduce the drop", "how": ["same route, same load"], "template_id": "none", "needs_approval": True}
    v = G.evaluate_action(fly, plan, hv_system=False)
    assert v["verdict"] == "blocked" and "G3" in {r["rule"] for r in v["rules"]}


# ---------------------------------------------------------------- in the investigator, with a scripted model
@pytest.fixture()
def case(tmp_path, monkeypatch):
    store = CaseStore(tmp_path / "w.db")
    c = store.create_case(machine_id="UAV-T", title="SYNTHETIC case", actor="t", synthetic=True)
    store.commit_import(c["id"], _csv(), "uav.csv", _profile(), actor="t", review_reason="test")
    store.investigate(c["id"], actor="t")
    rec = S.restart_cycles()
    layer = PL.PhysicalLayer("rec-00000000000000ef", rec, S.profile(), agent_bridge.PhysicalCheckTools(rec, S.profile()))
    monkeypatch.setattr(PL, "for_case", lambda store, case_id, context=None: layer)
    yield store, c["id"], layer
    store.close()


def _report(tools_, next_action, safety_findings=None):
    n = next(i for i, l in enumerate(tools_.raw, 1) if l.startswith("SERIES"))
    r = {"headline": "Protection tripped repeatedly after re-closures", "status": "cause_unknown", "confidence": "low",
         "explanation": "The upstream cause is open.", "checked": [], "unknowns": ["x"], "events": [],
         "evidence": [{"line": n, "quote": tools_.raw[n - 1][:40], "shows": "the series", "calls": ["p1"]}],
         "next_action": next_action,
         "hypotheses": [{"id": "h1", "area": "SOC estimation", "claim": "drift", "status": "open", "role": "main_cause", "because": "x", "physical_ref": "A"},
                        {"id": "h2", "area": "Capacity", "claim": "low", "status": "open", "role": "main_cause", "because": "x", "physical_ref": "B"}]}
    if safety_findings is not None:
        r["safety_findings"] = safety_findings
    return r


class _Model(ScriptedModel):
    provider = "anthropic"


def test_model_that_never_fixes_is_overridden_by_the_gate(case, monkeypatch):
    store, cid, layer = case
    monkeypatch.setenv("VEYRA_INQUIRY", "off")      # the scripted model never tests; the inquiry loop is tested in test_inquiry.py
    t = inv.tools_for(store, cid)
    bad = _report(t, copy.deepcopy(CASE_A_LIKE))
    model = _Model([{"content": [{"type": "tool_use", "id": "p1", "name": "physical_next_checks", "input": {}}]}] +
                   [{"content": [{"type": "tool_use", "id": f"s{i}", "name": "submit_report", "input": bad}]} for i in range(inv.MAX_FIX_ATTEMPTS + 1)])
    r = inv.investigate(store, cid, model=model)
    assert "blocked by G1" in model.calls[2]["messages"][-1]["content"][0]["content"]      # the model was told first
    na = r["result"]["next_action"]
    assert na["template_id"] == "MEAS-CELL-REST" and na["replaced_by_gate"]
    assert r["safety_gate"]["action_verdict"] == "blocked" and "HOLD-HV-RECLOSURE" in r["safety_gate"]["inserted_findings"]
    assert r["reasoning"]["safety"]["rules"] == ["G1"]


def test_model_that_fixes_passes_unchanged(case, monkeypatch):
    monkeypatch.setenv("VEYRA_INQUIRY", "off")      # this test scripts the gate fix only; the inquiry loop is tested in test_inquiry
    store, cid, layer = case
    t = inv.tools_for(store, cid)
    refs = [m["ref"] for m in G.material_refs(layer.tools)]
    good = _report(t, {"action": "Read every cell's rest voltage through the BMS diagnostic tool", "why": "required by the hold", "how": ["HV open"],
                       "where": "on_site", "template_id": "MEAS-CELL-REST", "needs_approval": True,
                       "steps": [{"kind": "procedure", "text": "Read every cell's rest voltage through the BMS diagnostic tool, HV open"}],
                       "outcomes": [{"label": "ok", "means": "release", "keeps": ["h1"], "rules_out": []}, {"label": "low", "means": "recover", "keeps": ["h2"], "rules_out": []}]},
                   [{"ref": x, "statement": "noted"} for x in refs])
    model = _Model([{"content": [{"type": "tool_use", "id": "p1", "name": "physical_next_checks", "input": {}}]},
                    {"content": [{"type": "tool_use", "id": "s1", "name": "submit_report", "input": good}]}])
    r = inv.investigate(store, cid, model=model)
    assert r["safety_gate"]["passed_unchanged"] and r["result"]["next_action"]["template_id"] == "MEAS-CELL-REST"
    assert not r["result"]["next_action"].get("replaced_by_gate")


# ---------------------------------------------------------------- found by acceptance v2 run 3: the replacement failed its own gate
def test_the_replacement_passes_the_gate_it_replaces_for():
    plan = tools().next_checks()
    rep = G.replacement(plan, hv_system=True)
    assert G.evaluate_action(rep, plan, hv_system=True)["verdict"] == "pass"


@pytest.mark.parametrize("seed_tools", [lambda: tools(), lambda: tools(S.restart_cycles(reset_in_gap=False)), lambda: tools(S.abrupt_correction()), lambda: tools(S.incident())])
@pytest.mark.parametrize("na", [CASE_A_LIKE, dict(CASE_A_LIKE, template_id="none"), {"action": "Fly it again", "how": [], "template_id": "none"},
                                {"action": "Charge the pack and close HV", "how": [], "template_id": "TEST-CAPACITY"}, None])
def test_whatever_is_proposed_the_delivered_action_passes(seed_tools, na):
    t = seed_tools()
    out = G.enforce({"next_action": copy.deepcopy(na)} if na else {}, t, hv_system=True)
    assert out["gate"]["final_action_verdict"] == "pass"
    assert G.evaluate_action(out["report"]["next_action"], t.next_checks(), hv_system=True)["verdict"] == "pass"


@pytest.mark.parametrize("text,energising", [
    ("Keep HV open; do not close the contactors", False), ("Read rest voltages before any charge or HV closure", False),
    ("With HV closed, clamp the meter", True), ("Read the codes, then close the contactors", True),
    ("Never charge the pack until cells are assessed", False), ("在上高压之前读取静置电压", False), ("上高压后读取电流", True)])
def test_negated_clauses_do_not_count_as_energising(text, energising):
    assert G.energising_text(text) is energising


# ---------------------------------------------------------------- over-blocking found in acceptance v2 run 3 (actions the model proposed there)
RUN3_OFFLINE = [
    {"action": "Export BMS charge/calibration history and charger log (offline document review)", "where": "remote", "template_id": "none", "needs_approval": True,
     "how": ["Export the BMS event/history log since the last full charge", "Export the charger log for the same period",
             "Compare the last full-charge calibration with charger energy in vs BMS-counted throughput"],
     "conditions": "Read-only export; vehicle stays at rest with HV open; no charging and no HV closure."},
    {"action": "Export BMS charge and calibration history and the charger log.", "where": "remote", "template_id": "none", "needs_approval": True,
     "how": ["Export the BMS event and history log since the last full charge.", "Get the charger log for the same period."],
     "conditions": "Read-only data export. Do not close HV or charge the vehicle. Cover the period since the last full charge."},
    {"action": "Analyse the saved ULG file offline for the commander and RC topics", "where": "remote", "template_id": "none", "needs_approval": False,
     "how": ["Open the stored .ulg on a desktop and plot manual_control_setpoint and the kill switch field in the last 10 s before the output step",
             "Plot vehicle_land_detected and actuator_armed for the same window"]},
]


@pytest.mark.parametrize("na", RUN3_OFFLINE)
def test_remote_data_work_is_not_blocked_even_under_a_hold(na):
    assert G.evaluate_action(na, tools().next_checks(), hv_system=True)["verdict"] == "pass"


@pytest.mark.parametrize("na", [
    {"action": "Connect the meter to the pack terminals", "where": "remote", "how": [], "template_id": "none"},
    {"action": "Remotely close the contactors and log current", "where": "remote", "how": [], "template_id": "none"},
    {"action": "Export logs, then power up the vehicle and read the BMS", "where": "remote", "how": [], "template_id": "none"}])
def test_remote_label_does_not_hide_contact_or_energising(na):
    assert G.evaluate_action(na, tools().next_checks(), hv_system=True)["verdict"] == "blocked"


def test_the_replacement_names_the_separating_check_that_follows_the_hold():
    plan = tools().next_checks()
    rep = G.replacement(plan, hv_system=True)
    assert rep["template_id"] == "MEAS-CELL-REST" and "after the hold is released" in rep["why"]
    assert G.evaluate_action(rep, plan, hv_system=True)["verdict"] == "pass"
