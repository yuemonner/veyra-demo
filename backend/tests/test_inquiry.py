"""Predict -> Test -> Update (app.agent.inquiry) and its read-only computations (app.physchecks.compute).

All data SYNTHETIC: a series-pack model (app.physchecks.synthetic_cells) generates each competing cause from first
principles (capacity fade, a current sensor that under-reads, a load bypassing the sensor), so the tests check that the
computations separate them without any case-specific constant. The model in the end-to-end test is scripted."""
import copy

import pytest

from app.agent import inquiry as IQ
from app.agent import investigator as inv
from app.agent import physical_layer as PL
from app.agent.llm import ScriptedModel
from app.physchecks import agent_bridge, compute as CM, synthetic as S, synthetic_cells as SC
from app.physchecks.profile import Profile
from app.physchecks.recording import Recording

SEGMENTS = [(120, 5.0), (60, 40.0), (60, 10.0), (60, 60.0), (60, 20.0), (120, 5.0)]


def _ctx(pack=None, profile=None, segments=SEGMENTS):
    pack = pack or SC.Pack()
    return CM.Context(SC.simulate(pack, segments), profile or SC.cells_profile())


# ---------------------------------------------------------------- computations
def test_integrate_counts_charge_and_reports_gaps():
    ctx = _ctx(segments=[(3600, 10.0)])
    r = CM.integrate(ctx, "pack_current", "w1")
    assert r["quantities"]["net"] == pytest.approx(10.0, rel=0.01) and r["quantities"]["unit"] == "Ah"
    assert r["evidence"] and all(e["file"] == "synthetic_cells.csv" and e["row"] > 1 for e in r["evidence"])
    assert any("upstream of the sensor" in a for a in r["assumptions"])
    gapped = CM.integrate(ctx, "pack_current", "w1", max_gap_s=0.5)          # 1 s sampling: every interval is a gap
    assert gapped["quantities"]["covered_s"] == 0 and gapped["quantities"]["gap_bound"][1] > 0


def test_step_response_gives_the_source_resistance_and_exposes_a_current_scale_error():
    true = CM.step_response(_ctx(), "pack_current", "pack_voltage", "w1", min_dx=5)
    under = CM.step_response(_ctx(SC.Pack(current_scale=0.5)), "pack_current", "pack_voltage", "w1", min_dx=2.5)
    r_true, r_under = true["quantities"]["median_ratio"], under["quantities"]["median_ratio"]
    assert true["quantities"]["steps"] >= 4 and true["quantities"]["positive_fraction"] == 1.0
    assert r_true == pytest.approx(12 * 0.002, rel=0.25)                   # n * r0 of the model
    assert r_under == pytest.approx(2 * r_true, rel=0.2)                   # a sensor reading half the current doubles it
    assert all(set(i["rows"]) == {"pack_current", "pack_voltage"} for i in true["items"])


def test_unexplained_changes_finds_a_load_the_sensor_does_not_see():
    b = S.Builder("syn-bypass", dt=1.0)
    for t in range(0, 200):
        hidden = 15.0 if 100 <= t < 150 else 0.0                           # a load upstream of the sensor
        b.frame("w1", float(t), "BMS", i_pack=5.0, v_pack=round(400 - 0.1 * (5.0 + hidden), 3), dcdc=1 if 100 <= t < 150 else 0)
    p = copy.deepcopy(S.PROFILE)
    p["roles"]["pack_voltage"] = {"field": "v_pack", "unit": "V", "kind": "measurement", "valid": [0, 1000], "max_age_s": 1.5}
    p["roles"]["pack_current"]["max_age_s"] = 1.5
    p["roles"]["dcdc_state"]["max_age_s"] = 1.5
    p["max_gap_s"] = 2.0
    ctx = CM.Context(b.done(), Profile(p))
    u = CM.unexplained_changes(ctx, "pack_voltage", "pack_current", "w1", min_dy=1.0, max_dx=1.0)
    assert u["quantities"]["unexplained"] == 2 and u["quantities"]["explained_by_x"] == 0
    tr = CM.transitions(ctx, "dcdc_state", "w1", ["pack_current", "pack_voltage"])
    assert tr["quantities"]["transitions"] == 2
    first = tr["items"][0]
    assert first["from"] == "off" and first["to"] == "working"
    assert first["changes"]["pack_current"]["delta"] == 0 and first["changes"]["pack_voltage"]["delta"] == pytest.approx(-1.5)


def test_topology_infers_the_series_count_and_flags_a_wrong_declaration_and_provenance():
    r = CM.topology(_ctx(SC.Pack(n=16), SC.cells_profile()), "pack_voltage", ["min_cell_voltage", "max_cell_voltage"], "w1")
    q = r["quantities"]
    assert q["inferred_series_count"] == 16 and q["declared_series_count"] == 12 and q["declared_agrees"] is False
    assert r["kinds"]["inferred_series_count"] == "inferred_parameter" and q["pack_independent_of_cells"] is True
    derived = CM.topology(_ctx(SC.Pack(pack_voltage_independent=False), SC.cells_profile(derived_pack_voltage=True)),
                          "pack_voltage", ["min_cell_voltage", "max_cell_voltage"], "w1")
    assert derived["quantities"]["pack_independent_of_cells"] is False and any("own arithmetic" in n for n in derived["notes"])
    with pytest.raises(CM.ComputeError):                                  # no usable sample: refused, never guessed
        CM.topology(_ctx(SC.Pack(n=16)), "pack_voltage", ["min_cell_voltage", "max_cell_voltage"], "w1", max_cell_spread_v=0.0)


def test_capacity_is_conditional_on_the_declared_anchor():
    # the BMS counts against 50 Ah; the true capacity is 25 Ah; SOC was set to 100 at a full charge before the record
    pack = SC.Pack(capacity_ah=25.0, rated_capacity_ah=50.0, soc0=100.0, noise_i=0.0)
    rec = SC.simulate(pack, [(7200, 10.0), (1800, 10.0)])   # 20 Ah before the record, 5 Ah in it: true SOC 0 at the end
    ctx = CM.Context(rec, SC.cells_profile())
    t0, t1 = 7200.0, 8999.0                       # only the last part is "the record"
    r = CM.capacity(ctx, "pack_current", "soc", "w1", t0, t1, anchors=[
        {"kind": "displayed_soc_correct"},
        {"kind": "displayed_start_true_end", "end_true_soc_pct": 0},
        {"kind": "counter_since_anchor", "anchor_true_soc_pct": 100, "end_true_soc_pct": 0}])
    q = r["quantities"]
    assert q["counted_ah"] == pytest.approx(5.0, rel=0.02) and q["rated_capacity_ah"] == 50.0
    caps = [a["implied_capacity_ah"] for a in q["anchors"]]
    assert q["soc_t0_pct"] == pytest.approx(60.0, abs=0.2)
    assert caps[0] == pytest.approx(50.0, rel=0.05)          # the counter is self-consistent: it hides fade
    assert caps[1] == pytest.approx(5.0 / 0.6, rel=0.05)      # SOC 60 % taken as true at t0
    assert caps[2] == pytest.approx(25.0, rel=0.05)           # the true capacity, recovered only under the full-charge anchor
    assert all(a["kind"] == "conditional" and a["assumes"] for a in q["anchors"])
    assert r["notes"] and "depends strongly on the anchor" in r["notes"][0]


def test_crosscheck_flags_a_copied_signal():
    b = S.Builder("syn-copy", dt=1.0)
    for t in range(50):
        b.frame("w1", float(t), "BMS", i_pack=10.0 + (t % 7), i_vcu=10.0 + (t % 7))
    p = copy.deepcopy(S.PROFILE)
    p["roles"]["vcu_pack_current"] = {"field": "i_vcu", "unit": "A", "sign": "discharge_positive", "kind": "measurement", "same_source_as": "pack_current"}
    ctx = CM.Context(b.done(), Profile(p))
    r = CM.crosscheck(ctx, "pack_current", "vcu_pack_current", "w1")
    assert r["quantities"]["identical_fraction"] == 1.0 and r["quantities"]["declared_independent"] is False
    assert any("not evidence" in n for n in r["notes"]) and any("copy" in n for n in r["notes"])


def test_unknown_role_or_op_is_refused():
    ctx = _ctx()
    with pytest.raises(CM.ComputeError):
        CM.run(ctx, "stats", {"role": "nope", "window": "w1"})
    with pytest.raises(CM.ComputeError):
        CM.run(ctx, "exec", {})
    with pytest.raises(CM.ComputeError):
        CM.run(ctx, "stats", {"role": "soc", "window": "w1", "bogus": 1})


# ---------------------------------------------------------------- ledger
def _inquiry(pack=None):
    rec = SC.simulate(pack or SC.Pack(current_scale=0.5), SEGMENTS)
    a = {"hypotheses": [{"id": "A", "label": "SOC", "statement": "s", "role": "upstream_cause", "status": "open"},
                        {"id": "B", "label": "Capacity", "statement": "s", "role": "upstream_cause", "status": "open"},
                        {"id": "C", "label": "Current", "statement": "s", "role": "upstream_cause", "status": "open"},
                        {"id": "P1", "label": "Protection", "statement": "s", "role": "protective_response", "status": "observed"}]}
    return IQ.Inquiry(rec, SC.cells_profile(), a)


STEP_TEST = {"op": "step_response", "args": {"x_role": "pack_current", "y_role": "pack_voltage", "window": "w1", "min_dx": 2.5}}


def _predict(q, test=STEP_TEST):
    return q.call("inquiry_predict", {"question": "is the current scale right?", "test": test, "quantity": "median_ratio", "predictions": [
        {"hypothesis": "C", "expect": {"gt": 0.036}, "why": "an under-reading sensor inflates dV/dI"},
        {"hypothesis": "A", "expect": {"between": [0.012, 0.036]}, "why": "true current: dV/dI is the cells' resistance"},
        {"hypothesis": "B", "expect": {"between": [0.012, 0.036]}, "why": "fade does not change the sensor"}]}, "call-1")


def test_seeding_uses_causes_not_the_protective_response():
    assert set(_inquiry().hypotheses) == {"A", "B", "C"}


def test_predict_then_test_updates_deterministically_and_chains_hashes():
    q = _inquiry()
    p = _predict(q)
    assert p["prediction_id"] == "P1" and p["post_hoc"] is False
    t = q.call("inquiry_test", {"prediction_id": "P1"}, "call-2")
    assert t["update"]["outcomes"] == {"C": "consistent", "A": "contradicted", "B": "contradicted"} and t["update"]["discriminating"]
    assert sorted(map(tuple, t["update"]["separated"])) == [("C", "A"), ("C", "B")]
    assert t["computation"]["evidence"]
    ex = q.export()
    assert [e["kind"] for e in ex["entries"]][-3:] == ["prediction", "test", "update"]
    assert all(ex["entries"][i]["prev"] == ex["entries"][i - 1]["hash"] for i in range(1, len(ex["entries"])))
    assert ex["summary"] == {"tests_executed": 1, "counted": 1, "post_hoc": 0, "discriminating": 1, "explorations": 0,
                             "distinctions": 3, "separated": 2, "declared": 0, "open": 1}       # A vs B: same prediction, still open
    with pytest.raises(ValueError):
        q.call("inquiry_test", {"prediction_id": "P1"})            # one test per prediction


def test_a_prediction_after_exploring_the_same_computation_is_post_hoc_and_does_not_count():
    q = _inquiry()
    q.call("inquiry_compute", STEP_TEST)
    p = _predict(q)
    assert p["post_hoc"] is True
    t = q.call("inquiry_test", {"prediction_id": p["prediction_id"]})
    assert t["update"]["counts"] is False and t["update"]["discriminating"] is False
    assert any("no prediction test was executed" in x for x in q.problems({"hypotheses": []}))


def test_non_discriminating_or_one_sided_predictions_are_refused():
    q = _inquiry()
    same = {"between": [0, 1]}
    with pytest.raises(ValueError, match="would not discriminate"):
        q.call("inquiry_predict", {"question": "x", "test": STEP_TEST, "quantity": "median_ratio",
                                   "predictions": [{"hypothesis": "A", "expect": same, "why": ""}, {"hypothesis": "B", "expect": same, "why": ""}]})
    with pytest.raises(ValueError, match="at least two"):
        q.call("inquiry_predict", {"question": "x", "test": STEP_TEST, "quantity": "median_ratio", "predictions": [{"hypothesis": "A", "expect": same, "why": ""}]})
    with pytest.raises(ValueError, match="unknown hypothesis"):
        q.call("inquiry_predict", {"question": "x", "test": STEP_TEST, "quantity": "median_ratio",
                                   "predictions": [{"hypothesis": "Z", "expect": same, "why": ""}, {"hypothesis": "A", "expect": {"gt": 2}, "why": ""}]})


def _rank(q, report, outputs=None):
    """Problems other than the distinction-coverage rule (tested separately)."""
    return [x for x in q.problems(report, outputs) if "distinction" not in x and "declared not discriminable" not in x]


def test_ranking_needs_an_executed_separating_test_and_ties_stay_together():
    q = _inquiry()
    assert any("no prediction test" in x for x in q.problems({"ranking": []}))
    _predict(q)
    q.call("inquiry_test", {"prediction_id": "P1"})
    ok = {"ranking": [{"hypotheses": ["C"], "tests": ["T1"], "basis": "x"}, {"hypotheses": ["A", "B"], "tests": ["T1"], "basis": "y", "coexist": True}]}
    assert _rank(q, ok) == []
    unseparated = {"ranking": [{"hypotheses": ["C"], "tests": ["T1"], "basis": "x"}, {"hypotheses": ["A"], "tests": ["T1"], "basis": "y"},
                               {"hypotheses": ["B"], "tests": [], "basis": "z"}]}
    assert any("A is ranked above B" in x for x in q.problems(unseparated))
    inverted = {"ranking": [{"hypotheses": ["A"], "tests": ["T1"], "basis": "x"}, {"hypotheses": ["C"], "tests": ["T1"], "basis": "y"}]}
    assert any("A is ranked above C" in x for x in q.problems(inverted))
    assert any("unknown inquiry test" in x for x in q.problems({"ranking": [{"hypotheses": ["C"], "tests": ["T9"], "basis": ""}]}))


def test_ranking_basis_numbers_must_come_from_the_cited_tests():
    q = _inquiry()
    _predict(q)
    t = q.call("inquiry_test", {"prediction_id": "P1"}, "c-test")
    import json
    out = {"c-test": json.dumps(t)}
    good = {"ranking": [{"hypotheses": ["C"], "tests": ["T1"], "basis": f"median ratio {t['value']}"}, {"hypotheses": ["A", "B"], "tests": ["T1"], "basis": "y"}]}
    bad = {"ranking": [{"hypotheses": ["C"], "tests": ["T1"], "basis": "median ratio 0.123456"}, {"hypotheses": ["A", "B"], "tests": ["T1"], "basis": "y"}]}
    assert _rank(q, good, out) == []
    assert any("not in the cited tests" in x for x in q.problems(bad, out))


def test_leading_needs_separation_and_cannot_be_contradicted():
    q = _inquiry()
    _predict(q)
    q.call("inquiry_test", {"prediction_id": "P1"})
    h = lambda ref, st: {"id": ref.lower(), "physical_ref": ref, "role": "main_cause", "status": st}  # noqa: E731
    assert _rank(q, {"hypotheses": [h("C", "leading"), h("A", "open"), h("B", "open")]}) == []
    assert any("contradicted it" in x for x in q.problems({"hypotheses": [h("A", "leading"), h("C", "open")]}))
    assert any("no executed test separated them" in x for x in q.problems({"hypotheses": [h("A", "leading"), h("B", "open")]}))


def test_records_are_labelled_by_kind():
    q = _inquiry(SC.Pack(n=16))
    q.call("inquiry_compute", {"op": "topology", "args": {"pack_role": "pack_voltage", "cell_roles": ["min_cell_voltage", "max_cell_voltage"], "window": "w1"}})
    q.call("inquiry_predict", {"question": "a", "test": {"op": "capacity", "args": {"current_role": "pack_current", "soc_role": "soc", "window": "w1", "t0": 0, "t1": 400,
                                                                               "anchors": [{"kind": "displayed_soc_correct"}]}},
                               "quantity": "anchors.0.implied_capacity_ah", "assumptions": ["the counter was not reset in the record"],
                               "predictions": [{"hypothesis": "A", "expect": {"gt": 40}, "why": ""}, {"hypothesis": "B", "expect": {"lt": 40}, "why": ""}]})
    t = q.call("inquiry_test", {"prediction_id": "P1"})
    assert t["value_kind"] == "conditional"
    kinds = q.view()["records_by_kind"]
    assert kinds.get("inferred_parameter") and kinds.get("assumption") and kinds.get("conditional") and kinds.get("reported_value")


# ---------------------------------------------------------------- inside the investigator
class _Model(ScriptedModel):
    provider = "anthropic"


@pytest.fixture()
def case(tmp_path):
    from tests.test_recording_investigation import _csv, _profile
    from app.store.sqlite_store import CaseStore
    store = CaseStore(tmp_path / "w.db")
    c = store.create_case(machine_id="UAV-T", title="SYNTHETIC case", actor="t", synthetic=True)
    store.commit_import(c["id"], _csv(), "uav.csv", _profile(), actor="t", review_reason="test")
    store.investigate(c["id"], actor="t")
    yield store, c["id"]
    store.close()


def _layer():
    rec = S.incident()
    prof = S.profile()
    return PL.PhysicalLayer("rec-00000000000000ab", rec, prof, agent_bridge.PhysicalCheckTools(rec, prof))


def test_investigator_offers_the_loop_requires_a_test_and_records_the_ledger(case, monkeypatch):
    monkeypatch.setenv("VEYRA_DYNAMICS", "off")        # this scripted run exercises the inquiry-v2 loop alone (dynamics: test_dynamics.py)
    from tests.test_physical_layer import _report
    store, cid = case
    layer = _layer()
    monkeypatch.setattr(PL, "for_case", lambda store, case_id, context=None: layer)
    tools = inv.tools_for(store, cid)
    window = layer.rec.windows[0]
    hyps = [{"id": "h1", "area": "SOC estimation", "claim": "SOC estimation drift", "status": "open", "role": "main_cause", "because": "x", "physical_ref": "A"},
            {"id": "h2", "area": "Capacity", "claim": "capacity fade", "status": "open", "role": "main_cause", "because": "x", "physical_ref": "B"}]
    first = _report("SOC fell without charge to account for it", "Upstream cause open.", hyps, tools, layer)
    test = {"op": "integrate", "args": {"role": "pack_current", "window": window}}
    sp = layer.rec.span(window)
    explore = [{"op": "capacity", "args": {"current_role": "pack_current", "soc_role": "soc", "window": window, "t0": sp[0], "t1": sp[1],
                                           "anchors": [{"kind": "displayed_soc_correct"}]}},
               {"op": "step_response", "args": {"x_role": "pack_current", "y_role": "min_cell_voltage", "window": window}},
               {"op": "transitions", "args": {"state_role": "dcdc_state", "window": window, "roles": ["pack_current"]}},
               {"op": "unexplained_changes", "args": {"y_role": "min_cell_voltage", "x_role": "pack_current", "window": window, "min_dy": 0.05}},
               {"op": "compare_windows", "args": {"role": "pack_current", "a": {"window": layer.rec.windows[0]}, "b": {"window": layer.rec.windows[-1]}}}]
    declare = [{"pair": pair, "reason": "no computed quantity differs", "looked_at": ["X1", "X2"], "would_separate": "a metered charge record"}
               for pair in (["A", "B"], ["B", "C1"], ["A", "C2"], ["B", "C2"])]
    tie = {**first, "ranking": [{"hypotheses": ["A", "B", "C"], "tests": ["T1"], "basis": "not separated", "coexist": True}]}
    model = _Model([
        {"content": [{"type": "tool_use", "id": "p1", "name": "physical_checks", "input": {}},
                     {"type": "tool_use", "id": "q1", "name": "inquiry_plan", "input": {}}]},
        {"content": [{"type": "tool_use", "id": "s1", "name": "submit_report", "input": first}]},
        {"content": [{"type": "tool_use", "id": "q2", "name": "inquiry_predict", "input": {
            "question": "how much charge left the pack", "test": test, "quantity": "net",
            "predictions": [{"hypothesis": "A", "expect": {"gt": 0}, "why": "a"}, {"hypothesis": "C1", "expect": {"lt": 0}, "why": "b"}]}}]},
        {"content": [{"type": "tool_use", "id": "q3", "name": "inquiry_test", "input": {"prediction_id": "P1"}}]},
        {"content": [{"type": "tool_use", "id": "s2", "name": "submit_report", "input": {**first, "ranking": [
            {"hypotheses": ["A"], "tests": ["T1"], "basis": "x"}, {"hypotheses": ["B", "C"], "tests": ["T1"], "basis": "y"}]}}]},
        {"content": [{"type": "tool_use", "id": "d1", "name": "inquiry_distinguishable", "input": {**declare[0], "looked_at": ["T1"]}}]},
        {"content": [{"type": "tool_use", "id": f"x{i}", "name": "inquiry_compute", "input": x} for i, x in enumerate(explore)]},
        {"content": [{"type": "tool_use", "id": "s3", "name": "submit_report", "input": tie}]},
        {"content": [{"type": "tool_use", "id": f"d{i + 2}", "name": "inquiry_distinguishable", "input": d} for i, d in enumerate(declare)]},
        {"content": [{"type": "tool_use", "id": "s4", "name": "submit_report", "input": tie}]},
    ])
    r = inv.investigate(store, cid, model=model)
    assert r["mode"] == "agent"
    assert IQ.NAMES <= set(model.calls[0]["tools"])
    assert "no prediction test was executed" in model.calls[2]["messages"][-1]["content"][0]["content"]
    second_reject = model.calls[5]["messages"][-1]["content"][0]["content"]
    assert "A is ranked above C" in second_reject and "distinction" in second_reject
    early_declaration = model.calls[6]["messages"][-1]["content"][0]["content"]
    assert "not run yet" in early_declaration                    # declared before looking: recorded, but it does not close yet
    third_reject = model.calls[8]["messages"][-1]["content"][0]["content"]
    assert "B vs C2 is unresolved" in third_reject
    iq = r["inquiry"]
    assert iq["outstanding_at_acceptance"] == []
    assert iq["summary"]["separated"] == 1 and iq["summary"]["declared"] == 4 and iq["summary"]["open"] == 0
    assert {"C1", "C2"} <= {h["id"] for h in iq["hypotheses"]}
    assert all(i["run"] for i in iq["instruments"])
    assert r["reasoning"]["inquiry"]["discriminating"] == 1
    assert r["safety_gate"] is not None                          # the independent gate still ran


def test_the_loop_can_be_switched_off(case, monkeypatch):
    store, cid = case
    layer = _layer()
    monkeypatch.setattr(PL, "for_case", lambda store, case_id, context=None: layer)
    monkeypatch.setenv("VEYRA_INQUIRY", "off")
    model = _Model([{"content": [{"type": "tool_use", "id": "p1", "name": "physical_checks", "input": {}}]}])
    inv.investigate(store, cid, model=model, max_turns=1)
    assert not (IQ.NAMES & set(model.calls[0]["tools"]))


def test_heldout_generator_trips_for_every_cause_and_declares_no_pack_parameters():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("heldout_gen", Path(__file__).resolve().parents[2] / "scripts" / "make_synthetic_heldout.py")
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    for cause in g.CAUSES:
        s = g.summary(g.draw(cause, 7))
        assert s["tripped"] and s["math_ok"], cause
    prof = Profile.load(Path(CM.__file__).parent / "profiles" / "synthetic-bev-heldout.json")
    assert prof.synthetic and prof.parameters == {} and prof.role("soc")["estimator"] == "coulomb_counting"


def test_a_distinction_closes_only_by_a_separating_test_or_a_declaration_after_every_applicable_computation(monkeypatch):
    monkeypatch.setenv("VEYRA_DYNAMICS", "off")
    q = _inquiry()
    ops = [i["op"] for i in q.instruments()]
    assert ops == ["integrate", "capacity", "step_response", "transitions", "unexplained_changes", "topology"]   # from roles; one window
    _predict(q)                                               # C vs A and C vs B differ; A vs B predicted the same
    plan = q.plan()
    assert plan["registered_not_tested"][0]["addresses_open_distinctions"] == [["A", "C"], ["B", "C"]]
    q.call("inquiry_test", {"prediction_id": "P1"})
    assert q.plan()["open"] == [["A", "B"]]
    q.call("inquiry_distinguishable", {"pair": ["A", "B"], "reason": "same current, same sensor", "looked_at": ["T1"], "would_separate": "a metered full charge"})
    assert any("never run" in x for x in q.problems({}))      # declared before looking with every applicable computation
    w = "w1"
    for op, args in [("integrate", {"role": "pack_current", "window": w}),
                     ("capacity", {"current_role": "pack_current", "soc_role": "soc", "window": w, "t0": 0, "t1": 400, "anchors": [{"kind": "displayed_soc_correct"}]}),
                     ("transitions", {"state_role": "min_cell_index", "window": w, "roles": ["pack_current"]}),
                     ("unexplained_changes", {"y_role": "pack_voltage", "x_role": "pack_current", "window": w}),
                     ("topology", {"pack_role": "pack_voltage", "cell_roles": ["min_cell_voltage", "max_cell_voltage"], "window": w})]:
        q.call("inquiry_compute", {"op": op, "args": args})
    assert q.problems({}) == [] and q.view()["summary"]["open"] == 0
    with pytest.raises(ValueError, match="already separated"):
        q.call("inquiry_distinguishable", {"pair": ["A", "C"], "reason": "x", "looked_at": ["T1"], "would_separate": "y"})


def test_a_test_of_one_mechanism_does_not_demote_the_other():
    rec = SC.simulate(SC.Pack(), SEGMENTS)
    a = {"hypotheses": [{"id": "A", "label": "SOC", "statement": "s", "role": "upstream_cause", "status": "open"},
                        {"id": "C", "label": "Current", "statement": "s", "role": "upstream_cause", "status": "open",
                         "mechanisms": [{"id": "C1", "statement": "scale"}, {"id": "C2", "statement": "bypass"}]}]}
    q = IQ.Inquiry(rec, SC.cells_profile(), a)
    assert q.competing() == ["A", "C1", "C2"]
    with pytest.raises(ValueError, match="predict for each mechanism"):
        q.call("inquiry_predict", {"question": "x", "test": STEP_TEST, "quantity": "median_ratio",
                                   "predictions": [{"hypothesis": "C", "expect": {"gt": 0.036}, "why": ""}, {"hypothesis": "A", "expect": {"lt": 0.036}, "why": ""}]})
    q.call("inquiry_predict", {"question": "scale?", "test": STEP_TEST, "quantity": "median_ratio",
                               "predictions": [{"hypothesis": "C1", "expect": {"gt": 0.036}, "why": ""}, {"hypothesis": "A", "expect": {"lt": 0.036}, "why": ""}]})
    q.call("inquiry_test", {"prediction_id": "P1"})
    assert q.standing("C1")["standing"] == "contradicted" and q.standing("C")["standing"] == "untested"
    assert q.separated("A", "C") == []                         # C2 untouched: C is not below A
    assert q.separated("A", "C1") == ["T1"]
    assert any("A is ranked above C" in x for x in q.problems({"ranking": [{"hypotheses": ["A"], "tests": ["T1"], "basis": ""},
                                                                           {"hypotheses": ["C"], "tests": ["T1"], "basis": ""}]}))
    assert [d["pair"] for d in q.distinctions()] == [["A", "C1"], ["A", "C2"]]   # mechanisms of one cause are not rivals


def test_a_prose_reply_from_the_cli_gets_the_corrective_retry():
    from app.agent import llm
    with pytest.raises(llm._BadJSON):
        llm.ClaudeCodeModel._extract_json("I will now compute the step response for each window.")


# ---------------------------------------------------------------- strategies (same tools, different policy)
def test_expected_bits_measures_how_much_a_test_would_split_the_tied_causes():
    q = _inquiry()
    p = q.call("inquiry_predict", {"question": "q", "test": STEP_TEST, "quantity": "median_ratio", "predictions": [
        {"hypothesis": "C", "expect": {"gt": 0.036}, "why": "x"}, {"hypothesis": "A", "expect": {"between": [0.012, 0.036]}, "why": "x"},
        {"hypothesis": "B", "expect": {"between": [0.012, 0.036]}, "why": "x"}]})
    import math
    assert p["expected_bits"] == round(math.log2(3) - (2 / 3) * 1.0, 3)          # {C} | {A, B}
    three = q.call("inquiry_predict", {"question": "q", "test": {"op": "integrate", "args": {"role": "pack_current", "window": "w1"}}, "quantity": "net",
                                       "predictions": [{"hypothesis": h, "expect": {"between": [lo, lo + 1]}, "why": "x"} for h, lo in (("A", 0), ("B", 5), ("C", 10))]})
    assert three["expected_bits"] == round(math.log2(3), 3)                       # all three apart
    assert q.plan()["registered_not_tested"][0]["prediction_id"] == three["prediction_id"]


def test_free_needs_one_test_coverage_needs_every_pair_infogain_needs_every_tie_tested_or_declared(monkeypatch):
    def run(strategy):
        monkeypatch.setenv("VEYRA_INQUIRY_STRATEGY", strategy)
        q = _inquiry()
        _predict(q)
        q.call("inquiry_test", {"prediction_id": "P1"})          # separates C from A and B; A vs B still tied
        before = q.problems({})
        q.call("inquiry_distinguishable", {"pair": ["A", "B"], "reason": "same current, same sensor", "looked_at": ["T1"], "would_separate": "a metered full charge"})
        return before, q.problems({})
    free = run("free")
    assert free == ([], [])
    cov = run("coverage")
    assert any("A vs B is unresolved" in x for x in cov[0]) and any("never run" in x for x in cov[1])     # must run every applicable computation
    ig = run("infogain")
    assert any("A vs B is still tied" in x for x in ig[0]) and ig[1] == []                                # the agent decides it cannot be split
    monkeypatch.setenv("VEYRA_INQUIRY_STRATEGY", "infogain")
    assert "expected_bits" in IQ.prompt() and "every applicable computation" not in IQ.prompt()


# ---------------------------------------------------------------- measurement-fault algebra
def _inquiry_with_effects():
    rec = SC.simulate(SC.Pack(), SEGMENTS)
    a = {"hypotheses": [{"id": "A", "label": "SOC", "statement": "s", "role": "upstream_cause", "status": "open", "effects": {"soc": "estimate_high"}},
                        {"id": "B", "label": "Capacity", "statement": "s", "role": "upstream_cause", "status": "open", "effects": {"rated_capacity": "capacity_low"}},
                        {"id": "C", "label": "Current", "statement": "s", "role": "upstream_cause", "status": "open",
                         "mechanisms": [{"id": "C1", "statement": "scale", "effects": {"pack_current": "scale_low"}},
                                        {"id": "C2", "statement": "bypass", "effects": {"pack_current": "unseen_load"}}]}]}
    return IQ.Inquiry(rec, SC.cells_profile(), a)


def test_a_prediction_that_gets_the_direction_of_a_measurement_fault_wrong_is_refused():
    q = _inquiry_with_effects()
    wrong = {"question": "scale?", "test": STEP_TEST, "quantity": "median_ratio", "predictions": [
        {"hypothesis": "A", "expect": {"between": [0.03, 0.2]}, "why": "cells"},
        {"hypothesis": "C1", "expect": {"lt": 0.01}, "why": "an under-read current makes the ratio tiny"}]}       # backwards
    with pytest.raises(ValueError, match="HIGHER"):
        q.call("inquiry_predict", wrong)
    manufactured = {"question": "bypass?", "test": STEP_TEST, "quantity": "median_ratio", "predictions": [
        {"hypothesis": "A", "expect": {"between": [0.03, 0.2]}, "why": "cells"}, {"hypothesis": "C2", "expect": {"gt": 0.5}, "why": "x"}]}
    with pytest.raises(ValueError, match="cannot change"):
        q.call("inquiry_predict", manufactured)


def test_a_separation_resting_on_an_assumed_baseline_weakens_but_does_not_rank():
    q = _inquiry_with_effects()
    p = q.call("inquiry_predict", {"question": "scale?", "test": STEP_TEST, "quantity": "median_ratio", "predictions": [
        {"hypothesis": "A", "expect": {"gt": 0.5}, "why": "assumed cell resistance"},          # deliberately wrong absolute guess
        {"hypothesis": "C1", "expect": {"gt": 1.0}, "why": "true ratio / k"}]})
    assert p["prediction_id"] and q.predictions["P1"]["conditional_pairs"] == [["A", "C1"]]
    t = q.call("inquiry_test", {"prediction_id": "P1"})
    assert t["update"]["outcomes"] == {"A": "contradicted_if_assumed", "C1": "contradicted_if_assumed"}
    assert t["update"]["separated"] == [] and q.standing("C1")["standing"] == "untested"


# ---------------------------------------------------------------- inquiry-v4: every executed test updates the assessment
def test_an_exploration_of_a_registered_prediction_is_its_test_with_registration_kept():
    q = _inquiry()
    p = _predict(q)
    h = q.predictions[p["prediction_id"]]["hash"]
    x = q.call("inquiry_compute", STEP_TEST)
    assert x["registered_predictions_tested"] == ["T1"]
    t = q.tests["T1"]
    assert t["via_exploration"] == x["compute_id"] and t["registered_at"] == h and t["update"]["counts"] is True
    a = {r["id"]: r for r in q.assessment_after_tests()["hypotheses"]}
    assert a["C"]["after_tests"] == "supported" and a["A"]["after_tests"] == "weakened" and a["B"]["after_tests"] == "weakened"
    assert a["C"]["provenance"][0] == {"test": "T1", "prediction": "P1", "registered_at": h, "via_exploration": "X1", "source": "agent"}


def test_a_post_hoc_test_updates_the_assessment_but_never_separates():
    q = _inquiry()
    q.call("inquiry_compute", STEP_TEST)
    p = _predict(q)
    q.call("inquiry_test", {"prediction_id": p["prediction_id"]})
    a = {r["id"]: r for r in q.assessment_after_tests()["hypotheses"]}
    assert a["A"]["after_tests"] == "weakened_post_hoc" and a["C"]["after_tests"] == "consistent_post_hoc"
    assert a["C"]["above"] == [] and q.separated("C", "A") == []


def test_an_undetermined_materiality_declares_every_unresolved_pair_without_crashing():
    # dynamics-v3 regression: a test returning no result (undetermined materiality) declares its pairs per hypothesis id
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import make_synthetic_dynamics as G
    from app.agent import inquiry as IQ
    for cls, seed in (("drift", 6001), ("external", 6001), ("common", 5002)):
        rec, prof = G.to_recording(G.draw("battery", cls, seed))
        iq = IQ.Inquiry(rec, prof, {"hypotheses": []})
        assert iq.dyn, cls
        for d in iq.declarations.values():
            assert len(d["pair"]) == 2 and d["pair"][0] != d["pair"][1]
