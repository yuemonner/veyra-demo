"""Expert layer (system audit + record evidence + labelled prior order) on SYNTHETIC recordings only."""
import copy
import importlib.util
import json
import tempfile
import zipfile
from pathlib import Path

import pytest

from app.agent import physical_layer
from app.physchecks import base, expert_layer, facts as facts_mod, hypotheses, planner, plausibility, synthetic, system_audit
from app.physchecks.profile import Profile
from app.physchecks.recording import Recording, load_decoded_csv

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("heldout_gen", ROOT / "scripts" / "make_synthetic_heldout.py")
G = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(G)
PJ = json.loads((ROOT / "backend/app/physchecks/profiles/synthetic-bev-heldout.json").read_text())
for _r in PJ["roles"].values():
    _r["field"] = _r["field"].split(":", 1)[-1]


def heldout(cause: str, seed: int, *, shape=None, rules=None):
    p = G.draw(cause, seed)
    p["label"] = "test"
    d = Path(tempfile.mkdtemp())
    G.write(p, d / "pkg.zip")
    zipfile.ZipFile(d / "pkg.zip").extractall(d)
    pj = copy.deepcopy(PJ)
    if shape:
        pj.setdefault("conditions", {})["ocv_shape"] = shape
    prof = Profile(pj)
    rec = load_decoded_csv(f"t-{seed}", {"drain": sorted(d.glob("drain_h*.csv"))}, fields=sorted({s["field"] for s in prof.roles.values()}), synthetic=True)
    checks = base.run_all(rec, prof)
    F = facts_mod.derive(rec, prof, checks)
    A = hypotheses.assess(checks, F, [], machine=hypotheses.machine_of(prof))
    ex = expert_layer.enrich(rec, prof, checks, F, A, rules=rules or {})
    return p, A, ex, checks


def _seed(cause, want_curve=None, want=lambda p: True):
    for s in range(1, 400):
        p = G.draw(cause, s)
        if (want_curve is None or p["curve"] == want_curve) and want(p):
            return s
    raise AssertionError("no seed")


def test_unknown_chemistry_gives_only_a_conditional_lead():
    _, A, ex, _ = heldout("soc_drift", _seed("soc_drift"))
    assert A["leading"] is None
    assert A["conditional_leading"]["hypothesis"] == "A"
    assert ex["plausibility"]["grades"]["A"]["grade"] == "leading_if_flat_ocv"


def test_confirmed_flat_chemistry_gives_a_labelled_prior_lead_and_the_separating_check_first():
    _, A, ex, checks = heldout("soc_drift", _seed("soc_drift"), shape="flat")
    lead = A["leading"]
    assert lead["hypothesis"] == "A" and lead["basis_kind"] == "prior"
    assert "not evidence from this record" in lead["statement"]
    plan = planner.plan(A, checks)
    first = plan["recommended"][0]
    assert first["id"] == "DOC-BMS-HISTORY" and first["tests_leading"]
    # the separating check distinguishes the leading explanation from capacity and current
    assert {"A", "B"} <= set(sum(first["distinguishes"], []))


def test_sloped_chemistry_gives_no_prior_lead():
    _, A, ex, _ = heldout("soc_drift", _seed("soc_drift"), shape="sloped")
    assert A["leading"] is None and A["conditional_leading"] is None
    assert ex["plausibility"]["grades"]["A"]["grade"] == "possible"


def test_a_load_the_sensor_does_not_see_is_record_evidence_and_beats_the_prior():
    found = None
    for s in range(1, 60):                      # a record long enough to show several load switchings
        p = G.draw("sensor_bypass", s)
        if p["dcdc_on_s"] + p["dcdc_off_s"] > 700:
            continue
        _, A, ex, _ = heldout("sensor_bypass", s, shape="flat")
        if "load_transitions" in ex["audit"]:
            found = (A, ex)
            break
    assert found, "no bypass record with load switchings"
    A, ex = found
    assert ex["audit"]["load_transitions"]["value"]["verdict"] == "not_seen"
    assert A["leading"]["hypothesis"] == "C" and A["leading"]["basis_kind"] == "evidence"


def test_requirement_is_never_used_to_rule_out_capacity_loss():
    _, A, ex, _ = heldout("capacity_fade", _seed("capacity_fade"), shape="sloped")
    b = ex["plausibility"]["grades"]["B"]
    assert b["grade"] == "possible"
    assert not any(e.get("direction") == "weakens" for e in b["record_evidence"])


def test_customer_verified_outcomes_set_the_prior():
    rules = {"verified_outcomes": [{"id": "X1", "hypothesis": "B", "signature": {"onset": "predates_record"}}]}
    _, A, ex, _ = heldout("capacity_fade", _seed("capacity_fade"), shape="flat", rules=rules)
    assert A["leading"]["hypothesis"] == "B"
    assert "verified outcomes" in A["leading"]["basis"]


def test_load_rules_reads_the_customer_file(tmp_path):
    f = tmp_path / "rules.json"
    f.write_text(json.dumps({"schema": plausibility.RULES_SCHEMA, "customer": "SYN", "rules": [
        {"id": "R1", "kind": "fault_level_semantics", "statement": "level 2 permits HV", "data": {"levels": {"2": {"permits_hv": True}, "3": {"permits_hv": False}}}},
        {"id": "R2", "kind": "chemistry", "statement": "LFP", "data": {"chemistry": "LFP", "ocv_shape": "flat"}},
        {"id": "R3", "kind": "maintenance_interval", "statement": "full charge every 14 days", "data": {"full_charge_interval_days": 14}},
        {"id": "R4", "kind": "verified_outcome", "statement": "drift", "data": {"hypothesis": "A", "signature": {"onset": "predates_record"}}}]}))
    r = plausibility.load_rules(f)
    assert r["fault_levels"]["2"]["permits_hv"] is True and r["ocv_shape"] == "flat" and r["full_charge_interval_days"] == 14
    assert r["verified_outcomes"][0]["hypothesis"] == "A"
    f.write_text(json.dumps({"schema": "other"}))
    with pytest.raises(ValueError):
        plausibility.load_rules(f)


def test_reference_knowledge_entries_state_their_conditions():
    K = plausibility.reference_knowledge()
    assert {"RK-SOC-COUNTING-DRIFT", "RK-FLAT-OCV", "RK-FADE-END-OF-LIFE", "RK-CURRENT-SENSOR-ACCURACY", "RK-WEAK-CELL-TIMING"} <= set(K)
    for e in K.values():
        assert e["source_kind"] == "general_engineering_knowledge" and "caveat" in e and "applies_when" in e


# ---------------------------------------------------------------- system audit on the scripted SYNTHETIC incident
def _incident():
    rec, prof = synthetic.incident(), synthetic.profile()
    checks = base.run_all(rec, prof)
    F = facts_mod.derive(rec, prof, checks)
    return rec, prof, checks, F


def test_control_sequence_and_restart_authorisation_on_the_incident():
    rec, prof, checks, F = _incident()
    a = system_audit.audit(rec, prof, checks, F)
    assert "error" not in a.get("control_sequence", {})
    seq = a["control_sequence"]
    assert seq["value"]["events"] and seq["kind"] == "observed"
    ra = a["restart_authorisation"]
    kinds = {f["id"] for f in ra["findings"]}
    assert "protection_not_latched" in kinds
    assert next(f for f in ra["findings"] if f["id"] == "protection_not_latched")["flag"] == "question"


def test_restart_semantics_from_customer_rules():
    rec, prof, checks, F = _incident()
    rules = {"fault_levels": {"2": {"permits_hv": True}, "3": {"permits_hv": False, "latched": True}}}
    ra = system_audit.audit(rec, prof, checks, F, rules=rules)["restart_authorisation"]
    nl = next(f for f in ra["findings"] if f["id"] == "protection_not_latched")
    assert nl["flag"] == "deviation"


def test_inferred_thresholds_are_labelled_inferences():
    rec, prof, checks, F = _incident()
    th = system_audit.inferred_thresholds(rec, prof)
    assert th is None or (th["kind"] == "inferred" and th["limits"])


def _manual():
    """A tiny SYNTHETIC record: a relay of the current, a post-contactor field that copies pack voltage, a saturated field."""
    rec = Recording("syn-manual", synthetic=True)
    for k in range(200):
        t = k * 0.5
        closed = 1 if t < 60 else 0
        i = 7.0 if closed else 0.0
        rec.add("w", "i", t, i, row=k + 1)
        rec.add("w", "i_vcu", t + 0.1, i, row=k + 1)
        rec.add("w", "v", t, 300.0 - 0.01 * k, row=k + 1)
        rec.add("w", "v_post", t, 300.0 - 0.01 * k, row=k + 1)
        rec.add("w", "kp", t, closed, row=k + 1)
        rec.add("w", "kn", t, closed, row=k + 1)
        rec.add("w", "ins", t, 255, row=k + 1)
        rec.add("w", "spd", t, 0.0, row=k + 1)
        rec.add("w", "estop", t, 2, row=k + 1)
        rec.add("w", "drv", t, 0, row=k + 1)
    rec.finalize()
    prof = Profile({"schema": "veyra-signal-profile-v1", "id": "syn-manual", "synthetic": True, "max_sample_age_s": 1.0, "max_gap_s": 1.0,
                    "roles": {"pack_current": {"field": "i", "unit": "A", "sign": "discharge_positive", "resolution": 0.1},
                              "vcu_pack_current": {"field": "i_vcu", "unit": "A", "sign": "discharge_positive", "same_source_as": "pack_current"},
                              "pack_voltage": {"field": "v", "unit": "V"}, "post_contactor_voltage": {"field": "v_post", "unit": "V"},
                              "main_positive_closed": {"field": "kp", "unit": "", "kind": "state"},
                              "main_negative_closed": {"field": "kn", "unit": "", "kind": "state"},
                              "insulation_pos": {"field": "ins", "unit": "", "saturation": 255},
                              "speed": {"field": "spd", "unit": "m/s"}, "estop_state": {"field": "estop", "unit": "", "kind": "state"},
                              "drive_enabled": {"field": "drv", "unit": "", "kind": "state"}}})
    return rec, prof


def test_signal_independence_flags_relays_copies_and_saturation():
    rec, prof = _manual()
    si = system_audit.signal_independence(rec, prof)
    ids = {i["id"] for i in si["items"]}
    assert "relay:vcu_pack_current" in ids and "constant:insulation_pos" in ids and "current_when_open" in ids
    post = next(i for i in si["items"] if i["id"] == "post_contactor_field")
    assert post["flag"] == "safety" and "live" in post["statement"]
    assert "constant:speed" not in ids            # a stationary machine's zero speed IS information


def test_symptom_vs_record_checks_the_report():
    rec, prof = _manual()
    s = system_audit.symptom_vs_record(rec, prof, reported="could not drive after the alarm")
    assert s["value"]["drive_requested"] is False and s["value"]["reconciles_with_report"] is False
    assert "emergency stop was active" in s["statement"]


def test_structured_guard_rejects_a_leading_cause_the_record_weakens():
    A = {"family_scope": {"applies": True}, "hypotheses": [
        {"id": "C", "label": "Current", "role": "upstream_cause", "status": "open",
         "plausibility": {"record_evidence": [{"claim": "loads pass the sensor", "direction": "weakens"}]}}]}
    v = physical_layer.structured({"id": "h1", "physical_ref": "C", "role": "main_cause", "status": "leading", "claim": "x"}, A)
    assert v["verdict"] == "rejected"
