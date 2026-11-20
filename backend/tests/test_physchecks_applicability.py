"""Machine applicability and signal semantics: a battery that is not a battery-electric vehicle's HV pack (a PX4 drone,
a profile that states no class) never gets HV / BMS / contactor hypotheses or checks, and an SOC that is not known to
count charge never yields a charge-accounting VIOLATION. All recordings here are SYNTHETIC (app.physchecks.synthetic)."""
import copy

import pytest

from app.physchecks import agent_bridge, base, facts, hypotheses, planner, synthetic as S
from app.physchecks.base import INDETERMINATE, VIOLATION
from app.physchecks.profile import Profile

A = base.get("charge_accounting")


def drone_profile(estimator="unknown"):
    """The synthetic profile re-declared as a PX4-like drone: no HV contactors, SOC estimator as given."""
    p = copy.deepcopy(S.PROFILE)
    p.update(id="synthetic-drone", machine_class="px4_vehicle", conditions={})
    p["roles"]["soc"] = dict(p["roles"]["soc"], estimator=estimator)
    for r in ("bms_fault_level", "bms_state", "main_positive_closed", "main_negative_closed", "precharge_closed", "power_state", "dcdc_state"):
        p["roles"].pop(r)
    return Profile(p)


def assess(rec, prof):
    checks = base.run_all(rec, prof)
    return checks, hypotheses.assess(checks, facts.derive(rec, prof, checks), [], machine=hypotheses.machine_of(prof))


# ---------------------------------------------------------------- signal semantics (Check A)
def test_unknown_estimator_without_counting_evidence_is_not_a_violation():
    # 300 s of counting at 6 A moves SOC by 0.5 pp: too little to show the estimator counts charge
    r = A.run(S.abrupt_correction(), drone_profile("unknown")).as_dict()
    assert r["outcome"] == INDETERMINATE
    assert "does not say how SOC is estimated" in r["reasons"][0]
    assert "unexplained_soc_correction" not in [f["kind"] for f in r["findings"]]
    f = next(f for f in r["findings"] if f["kind"] == "soc_change_not_from_charge")
    assert f["severity"] == "review" and f["values"]["unexplained_pp"]          # the observation and its size are kept
    assert r["parameters_used"]["soc_estimator"] == "unknown" and r["parameters_used"]["counting_shown_in_recording"] is False


def test_voltage_based_estimator_is_never_a_violation():
    r = A.run(S.abrupt_correction(), drone_profile("voltage_based")).as_dict()
    assert r["outcome"] == INDETERMINATE and "voltage_based" in r["reasons"][0]


def test_unknown_estimator_shown_counting_in_the_recording_keeps_the_violation():
    # 20 min of counting at 40 A (13.3 pp) before a 30 pp correction: the recording itself shows a counting estimator
    b = S.Builder("syn-long-then-correction")
    S.discharge(b, "w1", 0, 1500, current=40.0, cap_ah=100.0, soc0=80.0,
                soc_bias=lambda t: -30.0 * min(1.0, max(0.0, (t - 1300) / 30)))
    r = A.run(b.done(), drone_profile("unknown")).as_dict()
    assert r["outcome"] == VIOLATION and r["parameters_used"]["counting_shown_in_recording"] is True
    assert any("the estimator counts charge there" in a for a in r["assumptions"])


def test_declared_coulomb_counting_keeps_the_existing_behaviour():
    assert A.run(S.abrupt_correction(), S.profile()).as_dict()["outcome"] == VIOLATION


# ---------------------------------------------------------------- machine applicability (hypotheses, planner, agent)
def test_px4_machine_gets_no_hv_family_and_no_recommendations():
    rec = S.abrupt_correction()
    checks, Ass = assess(rec, drone_profile("coulomb_counting"))       # even with a real violation
    assert checks["charge_accounting"]["outcome"] == VIOLATION
    assert Ass["hypotheses"] == [] and Ass["family"] is None
    assert Ass["family_scope"]["applies"] is False and Ass["family_scope"]["machine_class"] == "px4_vehicle"
    P = planner.plan(Ass, checks, requester_qualification="hv_qualified")
    assert P["recommended"] == [] and P["handoff"] == [] and P["safety_holds"] == []
    assert "do not apply" in P["note"]


def test_profile_without_machine_class_gets_no_hv_family():
    p = copy.deepcopy(S.PROFILE)
    p["machine_class"] = None
    checks, Ass = assess(S.abrupt_correction(), Profile(p))
    assert Ass["hypotheses"] == [] and "states no machine class" in Ass["family_scope"]["reasons"][0]


def test_declared_vehicle_still_gets_the_family():
    rec = S.incident()
    checks, Ass = assess(rec, S.profile())
    assert Ass["family_scope"]["applies"] is True and {"A", "B", "C"} <= {h["id"] for h in Ass["hypotheses"]}
    assert planner.plan(Ass, checks)["recommended"]


def test_planner_refuses_an_assessment_without_scope():
    rec = S.incident()
    checks, Ass = assess(rec, S.profile())
    Ass.pop("family_scope")
    with pytest.raises(ValueError):
        planner.plan(Ass, checks)


def test_hv_vehicle_statements_from_a_model_are_rejected_on_a_drone():
    rec = S.abrupt_correction()
    checks, Ass = assess(rec, drone_profile("unknown"))
    out = hypotheses.constrain_claims([
        {"text": "Have an HV-qualified technician check the main contactors and the BMS restart policy.", "source": "agent"},
        {"text": "The reported remaining charge fell faster than the measured current explains; the estimator type is unknown.", "source": "agent"},
    ], Ass, checks)
    assert out[0]["verdict"] == "rejected" and "does not apply" in out[0]["reasons"][0]
    assert out[1]["verdict"] == "accepted"


def test_agent_prompt_names_the_machine_class():
    class Recorder:
        provider, model = "scripted-test", "rec"

        def __init__(self):
            self.messages = None

        def available(self):
            return True

        def create(self, *, system, messages, tools):
            self.messages = self.messages or [dict(m) for m in messages]
            assert "family_scope" in system
            return {"content": [{"type": "tool_use", "id": "s", "name": "submit_report", "input": {"hypotheses": [], "summary": "Nothing applies."}}]}

    m = Recorder()
    out = agent_bridge.investigate(S.abrupt_correction(), drone_profile(), model=m, allow_external_model=True)
    assert out["mode"] == "agent" and "px4_vehicle" in m.messages[0]["content"]
    assert out["deterministic_assessment"]["family_scope"]["applies"] is False
