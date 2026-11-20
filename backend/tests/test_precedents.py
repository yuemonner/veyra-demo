"""Similar earlier cases: found, compared, their decisive check put first, their cause never applied."""
from app.engine import precedents as P


def sig(*items):
    return [{"domain": d, "rule": r, "signal": s, "scope": sc} for d, r, s, sc in items]


AILERON = sig(("vehicle_interface", "R-OUTPUT-STUCK", "actuator.output", "aileron_rc5"), ("vehicle_body", "R-TRACKING", "vehicle.attitude", "roll"))
RUDDER = sig(("vehicle_interface", "R-OUTPUT-STUCK", "actuator.output", "rudder_rc4"), ("vehicle_body", "R-TRACKING", "vehicle.attitude", "yaw"))
ENGINE = sig(("vehicle_body", "R-REFERENCE", "vehicle.airspeed", None))


def closed(case_id, s, cause, check, machine="M1", profile="fixed_wing_uav", synthetic=False):
    return {"case_id": case_id, "title": case_id, "machine_id": machine, "profile_id": profile, "synthetic": synthetic, "signature": s,
            "closure": {"investigation_status": "cause_confirmed", "cause_domain": "vehicle_interface", "cause": cause, "evidence_strength": "direct",
                        "resolution_status": "resolved", "decisive_check": check}}


def test_most_similar_closed_case_first_with_same_and_different():
    now = sig(("vehicle_interface", "R-OUTPUT-STUCK", "actuator.output", "aileron_rc5"), ("vehicle_body", "R-TRACKING", "vehicle.attitude", "roll"),
              ("vehicle_body", "R-BRIEF", "vehicle.attitude", "pitch"))
    out = P.match(now, [closed("rudder", RUDDER, "Rudder servo", "CHK-X"), closed("aileron", AILERON, "Aileron linkage", "CHK-ONSITE-ACTUATOR"),
                        closed("engine", ENGINE, "Motor", "CHK-Y")], machine_id="M1", profile_id="fixed_wing_uav", synthetic=False)
    first = out["matches"][0]
    assert first["case_id"] == "aileron" and first["similarity"] > out["matches"][1]["similarity"]
    assert any("pitch" in x for x in first["only_now"]) and not first["only_then"]
    assert "engine" not in [m["case_id"] for m in out["matches"]]
    assert list(out["decisive_checks"])[0] == "CHK-ONSITE-ACTUATOR"
    assert "not applied" in out["boundary"]


def test_other_machine_type_and_synthetic_never_match():
    out = P.match(AILERON, [closed("x", AILERON, "c", "k", machine="M2", profile="legged_robot"), closed("y", AILERON, "c", "k", synthetic=True)],
                  machine_id="M1", profile_id="fixed_wing_uav", synthetic=False)
    assert out["matches"] == []
    same_type = P.match(AILERON, [closed("z", AILERON, "c", "k", machine="M2")], machine_id="M1", profile_id="fixed_wing_uav", synthetic=False)
    assert same_type["matches"][0]["same_machine"] is False


def test_same_reported_symptom_is_shown_with_differences_and_its_check_not_moved_up():
    late = [{"symptom": "late_response", "function": "boom"}]
    earlier = {**closed("net", sig(("network", "R-SEGMENT-RELATIVE", None, None)), "LTE handover storm", "CHK-NETWORK-LOGS"), "reported": late}
    now = sig(("vehicle_body", "R-IO", None, None))
    out = P.match(now, [earlier], machine_id="M1", profile_id="fixed_wing_uav", synthetic=False, reported_now=late,
                  status_now={"network": "no_delay_observed_in_measured_part", "vehicle_body": "delay_observed"}, names={"network": "Network"})
    m = out["matches"][0]
    assert m["match_basis"] == "reported symptom only" and m["evidence_points_elsewhere"] is True
    assert m["earlier_parts_now"] == [{"part": "Network", "status_now": "no_delay_observed_in_measured_part"}]
    assert m["check_not_moved_up"] and out["decisive_checks"] == {}
    other = P.match(now, [earlier], machine_id="M1", profile_id="fixed_wing_uav", synthetic=False,
                    reported_now=[{"symptom": "stopped_unexpectedly", "function": "boom"}])
    assert other["matches"] == []
    elsewhere = P.match(now, [earlier], machine_id="M1", profile_id="fixed_wing_uav", synthetic=False,
                        reported_now=[{"symptom": "late_response", "function": "bucket"}])
    assert elsewhere["matches"] == []


def test_reported_symptoms_come_from_intervention_reports():
    entries = [{"kind": "intervention_report", "payload": {"symptom": "late_response", "affected_function": "boom"}},
               {"kind": "intervention_report", "payload": {"symptom": None}}, {"kind": "triage", "payload": {}}]
    assert P.reported(entries) == [{"symptom": "late_response", "function": "boom"}]
