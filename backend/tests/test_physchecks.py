"""Physical Check Engine: charge accounting (A), protection sequence (B), restart eligibility (C).

All recordings here are SYNTHETIC (app.physchecks.synthetic); they test the logic, not any customer's data.
"""
import copy

import pytest

from app.physchecks import base, synthetic as S
from app.physchecks.base import INDETERMINATE, NOT_APPLICABLE, PASS, VIOLATION
from app.physchecks.profile import Profile, ProfileError
from app.physchecks.recording import Recording

A = base.get("charge_accounting")
B = base.get("protection_sequence")
C = base.get("restart_eligibility")


def kinds(r):
    return [f["kind"] for f in r["findings"]]


# ---------------------------------------------------------------- Check A: charge accounting
def test_normal_charge_integration_passes_and_tracks():
    r = A.run(S.healthy(), S.profile()).as_dict()
    assert r["outcome"] == PASS and r["synthetic"] is True
    w = r["windows"]["w1"]
    assert w["measured_discharge_ah"] == pytest.approx(40 * 600 / 3600, rel=1e-3)
    span = w["tracking_spans"][0]
    # SOC is quantised to 0.4 pp, so the ratio carries +/- 2 x 0.4 / |expected change|; the check reports that bound
    assert span["tracks"] and span["ratio_reported_to_expected"] == pytest.approx(1.0, abs=span["ratio_uncertainty"])
    assert span["ratio_uncertainty"] == pytest.approx(0.8 / (40 * 600 / 3600), rel=0.01)
    assert "soc_tracks_measured_charge" in kinds(r)


def test_abrupt_soc_correction_is_a_violation_with_numbers_and_rows():
    r = A.run(S.abrupt_correction(drop_pp=30.0, over_s=30.0), S.profile()).as_dict()
    assert r["outcome"] == VIOLATION
    ep = r["windows"]["w1"]["episodes"][0]
    assert ep["soc_before_pct"] - ep["soc_after_pct"] == pytest.approx(30.0, abs=0.9)
    assert ep["measured_charge_ah"] == pytest.approx(6.0 * ep["duration_s"] / 3600, rel=0.02)
    assert abs(ep["unexplained_pp"]) == pytest.approx(30.0 - ep["explained_pp"], abs=0.9)
    assert 295 <= ep["t_start"] <= 301 and 329 <= ep["t_end"] <= 331
    assert ep["current_needed_to_explain_a"] > 1000          # far beyond the 6 A measured
    f = next(f for f in r["findings"] if f["kind"] == "unexplained_soc_correction")
    assert f["evidence"] and all(e["row"] > 0 and e["field"] == "soc" for e in f["evidence"])
    assert any(c["formula"].startswith("unexplained") for c in r["calculations"])


def test_small_quantisation_steps_are_not_corrections():
    r = A.run(S.abrupt_correction(drop_pp=1.0, over_s=30.0), S.profile()).as_dict()
    assert r["outcome"] == PASS


def test_gap_is_not_integrated_across_and_soc_change_inside_it_is_not_assessable():
    b = S.Builder("gap")

    def bias(t):
        return -10.0 if t >= 205 else 0.0          # the estimator moves while the record is missing
    S.discharge(b, "w1", 0, 400, current=6.0, cap_ah=100.0, soc0=52.0, soc_bias=bias, skip=[(200, 215)])
    r = A.run(b.done(), S.profile()).as_dict()
    w = r["windows"]["w1"]
    assert w["current_gap_count"] == 1 and w["current_gaps"][0]["gap_s"] > 10
    assert w["episodes"] == []                           # nothing invented inside the gap
    assert w["soc_changes_across_gaps"][0]["change_pp"] == pytest.approx(-10.0, abs=0.9)
    assert r["outcome"] == INDETERMINATE
    assert w["measured_discharge_ah"] == pytest.approx(6.0 * (400 - 15.1) / 3600, rel=0.01)


def test_stale_current_is_not_paired_with_soc():
    b = S.Builder("stale")
    for k in range(3000):                               # SOC keeps arriving, current stops after 60 s
        t = round(k * 0.1, 3)
        b.frame("w1", t, "BMS_SOC", soc=50.0, cap_ah=100.0)
        if t < 60:
            b.frame("w1", round(t + 0.05, 3), "BMS_I", i_pack=5.0)
    r = A.run(b.done(), S.profile()).as_dict()
    dq = r["windows"]["w1"]["data_quality"]
    assert dq["soc_samples_without_fresh_current"] >= 2390
    assert r["outcome"] in (PASS, INDETERMINATE) and not r["windows"]["w1"]["episodes"]


def test_cross_message_timing_within_and_beyond_max_age():
    def build(offset):
        b = S.Builder("xmsg")
        for k in range(6000):
            t = round(k * 0.1, 3)
            b.frame("w1", t, "BMS_I", i_pack=36.0)
            b.frame("w1", round(t + offset, 3), "BMS_SOC", soc=round(round((60 - 36 * t / 3600) / 0.4) * 0.4, 3), cap_ah=100.0)
        return b.done()
    ok = A.run(build(0.05), S.profile()).as_dict()
    assert ok["outcome"] == PASS and ok["windows"]["w1"]["data_quality"]["soc_samples_without_fresh_current"] == 0
    p = copy.deepcopy(S.PROFILE)
    p["roles"]["pack_current"]["max_age_s"] = 0.04          # stricter than the 0.05 s offset
    stale = A.run(build(0.05), Profile(p)).as_dict()
    assert stale["outcome"] == INDETERMINATE
    assert "share a continuous current record" in " ".join(stale["reasons"])


@pytest.mark.parametrize("cap", [0, -5, float("nan"), "abc", 1e9])
def test_invalid_capacity_is_indeterminate(cap):
    r = A.run(S.healthy(), S.profile(), {"capacity_ah": cap}).as_dict()
    assert r["outcome"] == INDETERMINATE and r["reasons"]


def test_missing_capacity_is_indeterminate():
    p = copy.deepcopy(S.PROFILE)
    del p["roles"]["rated_capacity"]
    r = A.run(S.healthy(), Profile(p)).as_dict()
    assert r["outcome"] == INDETERMINATE and "no capacity" in r["reasons"][0]


def test_reported_capacity_is_labelled_a_parameter():
    r = A.run(S.healthy(), S.profile()).as_dict()
    assert "reported parameter" in r["parameters_used"]["capacity_source"]
    assert any("assumed to be defined against" in a for a in r["assumptions"])


def test_reversed_current_polarity_is_detected_not_reported_as_drift():
    b = S.Builder("rev")
    S.discharge(b, "w1", 0, 900, current=40.0, cap_ah=100.0, soc0=80.0, sign=-1.0)
    r = A.run(b.done(), S.profile()).as_dict()
    assert r["outcome"] == INDETERMINATE
    assert "polarity_suspect" in kinds(r)


def test_declared_charge_positive_convention_is_honoured():
    b = S.Builder("cp")
    S.discharge(b, "w1", 0, 900, current=40.0, cap_ah=100.0, soc0=80.0, sign=-1.0)
    p = copy.deepcopy(S.PROFILE)
    p["roles"]["pack_current"]["sign"] = "charge_positive"
    assert A.run(b.done(), Profile(p)).as_dict()["outcome"] == PASS


def test_profile_requires_a_sign_convention_for_measured_current():
    p = copy.deepcopy(S.PROFILE)
    del p["roles"]["pack_current"]["sign"]
    with pytest.raises(ProfileError):
        Profile(p)


def test_verified_recalibration_marker_explains_the_correction_but_keeps_its_size():
    rec = S.abrupt_correction(recal_flag=True)
    m = {"role": "soc_recal_flag", "op": "==", "value": 1, "description": "BMS low-end OCV recalibration", "verified": True}
    r = A.run(rec, S.profile(), {"recalibration_markers": [m]}).as_dict()
    assert r["outcome"] == PASS
    f = next(f for f in r["findings"] if f["kind"] == "declared_recalibration")
    assert abs(f["values"]["unexplained_pp"]) > 25 and "estimation error" in f["statement"]


def test_unverified_recalibration_rule_does_not_explain_anything():
    rec = S.abrupt_correction(recal_flag=True)
    m = {"role": "soc_recal_flag", "op": "==", "value": 1, "description": "suspected recalibration", "verified": False}
    r = A.run(rec, S.profile(), {"recalibration_markers": [m]}).as_dict()
    assert r["outcome"] == VIOLATION
    assert "UNVERIFIED" in next(f for f in r["findings"] if f["kind"] == "unexplained_soc_correction")["statement"]


def test_placeholders_are_excluded_and_counted():
    b = S.Builder("ph")
    S.discharge(b, "w1", 0, 300, current=20.0, cap_ah=100.0, soc0=70.0)
    b.frame("w1", 0.05, "BMS_STATUS", soc=255.0)        # out of range
    r = A.run(b.done(), S.profile()).as_dict()
    assert r["windows"]["w1"]["data_quality"]["soc_excluded"] == {"outside the declared valid range": 1}


def test_not_applicable_without_soc_binding():
    p = copy.deepcopy(S.PROFILE)
    del p["roles"]["soc"]
    r = A.run(S.healthy(), Profile(p)).as_dict()
    assert r["outcome"] == NOT_APPLICABLE


def test_deterministic_same_inputs_same_result():
    r1 = A.run(S.abrupt_correction(), S.profile()).as_dict()
    r2 = A.run(S.abrupt_correction(), S.profile()).as_dict()
    assert r1 == r2 and len(r1["input_digest"]) == 64
    r3 = A.run(S.abrupt_correction(), S.profile(), {"detect_window_s": 20}).as_dict()
    assert r3["input_digest"] != r1["input_digest"]


# ---------------------------------------------------------------- Check B: protection sequence
SPEC = {"schema": "veyra-protection-spec-v1", "id": "syn-uv-spec", "version": "1", "verified": True, "source": "SYNTHETIC test spec",
        "trigger": {"role": "bms_fault_level", "op": ">=", "value": 3}, "horizon_s": 30,
        "steps": [{"id": "hv_not_allowed", "within_s": [0, 1], "condition": {"role": "bms_state", "op": "==", "value": 5}},
                  {"id": "dcdc_off", "within_s": [0, 10], "condition": {"role": "dcdc_state", "op": "==", "value": 0}},
                  {"id": "contactors_open", "within_s": [0, 10],
                   "condition": {"all": [{"role": "main_positive_closed", "op": "==", "value": 0}, {"role": "main_negative_closed", "op": "==", "value": 0}]}}],
        "order": ["hv_not_allowed", "dcdc_off", "contactors_open"]}


def test_protection_sequence_without_spec_reports_facts_only():
    r = B.run(S.protection_trip(), S.profile()).as_dict()
    assert r["outcome"] == INDETERMINATE
    seq = r["windows"]["observed"]["sequences"][0]["followed_by"]
    open_ = next(d for d in seq if d["role"] == "main_positive_closed")
    assert open_["latency_s"] == pytest.approx(3.0, abs=0.06)
    assert "not evidence of which event caused which" in next(f for f in r["findings"] if f["kind"] == "observed_sequence")["statement"]


def test_protection_sequence_conforms_to_verified_spec():
    assert B.run(S.protection_trip(), S.profile(), {"spec": SPEC}).as_dict()["outcome"] == PASS


def test_protection_sequence_violates_verified_spec_with_late_contactors():
    spec = copy.deepcopy(SPEC)
    spec["steps"][2]["within_s"] = [0, 3]
    r = B.run(S.protection_trip(), S.profile(), {"spec": spec}).as_dict()
    assert r["outcome"] == VIOLATION
    assert any(f["kind"] == "spec_step_not_met" and f["severity"] == "violation" for f in r["findings"])


def test_unverified_spec_never_yields_a_violation():
    spec = copy.deepcopy(SPEC)
    spec["steps"][2]["within_s"] = [0, 3]
    spec["verified"] = False
    r = B.run(S.protection_trip(), S.profile(), {"spec": spec}).as_dict()
    assert r["outcome"] == INDETERMINATE and "DRAFT" in r["reasons"][-1]


def test_protection_sequence_gap_makes_step_undecidable():
    r = B.run(S.protection_trip(gap=(100.5, 108.0)), S.profile(), {"spec": SPEC}).as_dict()
    assert r["outcome"] == INDETERMINATE


def test_protection_sequence_not_applicable_without_escalation():
    assert B.run(S.healthy(), S.profile()).as_dict()["outcome"] == NOT_APPLICABLE


# ---------------------------------------------------------------- Check C: restart eligibility
def test_restart_cycles_flagged_for_review_without_policy():
    r = C.run(S.restart_cycles(cycles=3), S.profile()).as_dict()
    assert r["outcome"] == INDETERMINATE
    k = kinds(r)
    for flag in ("reclosed_after_protective_shutdown", "repeated_trip_cycles", "deeper_discharge_than_original_trip", "reset_not_observed"):
        assert flag in k
    assert "not judged" in r["reasons"][0]
    assert not any(f["severity"] == "violation" for f in r["findings"])


def test_restart_reset_recorded_is_marked_observed():
    r = C.run(S.restart_cycles(cycles=1, reset_in_gap=False), S.profile()).as_dict()
    assert r["windows"]["reclosures"]["items"][0]["reset_observed"] is True


def test_protective_trip_without_reclosure_passes():
    r = C.run(S.protection_trip(), S.profile()).as_dict()
    assert r["outcome"] == PASS and "protective_shutdown" in kinds(r)


def test_restart_with_verified_policy_can_violate_and_unverified_cannot():
    pol = {"schema": "veyra-restart-policy-v1", "id": "syn-pol", "verified": True, "source": "SYNTHETIC",
           "max_reclosures_after_protective_shutdown": 1, "min_cell_voltage_to_close_v": 2.7}
    assert C.run(S.restart_cycles(cycles=3), S.profile(), {"policy": pol}).as_dict()["outcome"] == VIOLATION
    pol["verified"] = False
    assert C.run(S.restart_cycles(cycles=3), S.profile(), {"policy": pol}).as_dict()["outcome"] == INDETERMINATE


def test_restart_not_applicable_without_hv_changes():
    assert C.run(S.healthy(), S.profile()).as_dict()["outcome"] == NOT_APPLICABLE


def test_registry_and_specs_are_complete():
    ids = [c.spec.id for c in base.all_checks()]
    assert ids == ["charge_accounting", "ocv_consistency", "pack_cell_consistency", "protection_sequence", "relaxation", "restart_eligibility", "weak_cell_attribution"]
    for c in base.all_checks():
        d = c.spec.as_dict()
        assert d["inputs"] and d["alignment"] and d["limitations"] and d["version"]


def test_synthetic_flag_propagates():
    rec = Recording("x", synthetic=True)
    rec.add("w", "soc", 0, 50)
    rec.finalize()
    assert A.run(rec, S.profile()).as_dict()["synthetic"] is True
