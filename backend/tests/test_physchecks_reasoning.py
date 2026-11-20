"""Hypothesis assessment, next decisive check, and the AI investigator held to the checks. SYNTHETIC fixtures only."""
import pytest

from app.agent.llm import ScriptedModel
from app.physchecks import agent_bridge, base, facts, hypotheses, planner, synthetic as S
from app.physchecks.recording import Recording


@pytest.fixture(scope="module")
def case():
    rec, prof = S.incident(), S.profile()
    checks = base.run_all(rec, prof)
    F = facts.derive(rec, prof, checks)
    return rec, prof, checks, F, hypotheses.assess(checks, F, machine=hypotheses.machine_of(prof))


def H(assessment, hid):
    return next(h for h in assessment["hypotheses"] if h["id"] == hid)


def test_symptom_protection_and_causes_are_separate_roles(case):
    *_, A = case
    assert H(A, "S1")["role"] == "observed_symptom" and H(A, "S1")["status"] == "observed"
    p1 = H(A, "P1")
    assert p1["role"] == "protective_response" and "not proof" in p1["status_reason"].lower()
    assert {h["id"] for h in A["hypotheses"] if h["role"] == "upstream_cause"} == {"A", "B", "C", "E", "F"}


def test_open_explanations_are_not_ranked_with_probabilities(case):
    *_, A = case
    assert A["open_upstream"] == ["A", "B", "C"]
    for h in A["hypotheses"]:
        assert h["status"] in hypotheses.STATUSES
        assert "%" not in h["status_reason"]
    assert "No root cause is confirmed" in A["summary"]


def test_weak_cell_alone_is_insufficient_with_reasons(case):
    *_, F, A = case
    d = H(A, "D")
    assert d["status"] == "insufficient_alone" and d["role"] == "contributing_factor"
    assert F["pack_wide_decline"]["value"]["pack_wide"] is True
    bases = [c["basis"] for c in d["contradicting"]]
    assert "fact:pack_wide_decline" in bases and "check:charge_accounting" in bases
    assert d["supporting"]  # it is still a real contributor candidate


def test_weak_cell_becomes_plausible_when_only_one_cell_sags():
    """Counter-case: if only the lowest cell falls (highest stays high), D is NOT marked insufficient by the pack-wide rule."""
    b = S.Builder("onecell")
    for k in range(5000):          # 0 .. 500 s; the lowest cell reaches 2.52 V at 400 s
        t = round(k * 0.1, 3)
        lo = 3.2 - 0.0017 * t if t < 400 else 2.52
        lvl = 0 if lo > 2.9 else (2 if lo > 2.52 else 3)
        closed = 0 if t > 400 + 3.0 else 1
        b.frame("w", t, "BMS_STATUS", i_pack=5.0 if closed else 0.0, soc=60.0, cap_ah=100.0, fault_raw=lvl, bms_state=5 if lvl == 3 else 0)
        b.frame("w", round(t + 0.02, 3), "BMS_CELLS", v_cell_min=round(lo, 4), v_cell_max=3.2, idx_min=3, k_pos=closed, k_neg=closed)
    rec, prof = b.done(), S.profile()
    checks = base.run_all(rec, prof)
    F = facts.derive(rec, prof, checks)
    assert F["pack_wide_decline"]["value"]["pack_wide"] is False
    assert H(hypotheses.assess(checks, F, machine=hypotheses.machine_of(prof)), "D")["status"] == "open"


def test_correction_and_energy_shortfall_are_distinct_quantities(case):
    *_, A = case
    q = A["quantities"]
    assert q["soc_correction_pp"]["basis"] == "check:charge_accounting"
    assert q["energy_shortfall_overstatement_pp"]["basis"] == "fact:energy_shortfall"
    assert q["energy_shortfall_overstatement_pp"]["estimated"] is True
    assert "different quantities" in q["note"]


def test_counterevidence_for_sensing_and_contactor_first_weakens_but_never_excludes(case):
    *_, A = case
    for hid in ("E", "F"):
        h = H(A, hid)
        assert h["status"] == "weakened" and h["contradicting"]


def test_verified_outcome_is_the_only_way_to_confirm(case):
    _, prof, checks, F, _ = case
    unverified = [{"id": "o1", "hypothesis_id": "B", "result": "confirmed", "verified": False, "summary": "x"}]
    assert H(hypotheses.assess(checks, F, unverified, machine=hypotheses.machine_of(prof)), "B")["status"] == "open"
    verified = [{"id": "o2", "hypothesis_id": "B", "result": "confirmed", "verified": True, "summary": "capacity test 15 Ah"}]
    A2 = hypotheses.assess(checks, F, verified, machine=hypotheses.machine_of(prof))
    assert H(A2, "B")["status"] == "confirmed" and "Confirmed by verified outcome" in A2["summary"]


def test_family_not_applicable_on_healthy_data():
    rec, prof = S.healthy(), S.profile()
    checks = base.run_all(rec, prof)
    A = hypotheses.assess(checks, facts.derive(rec, prof, checks), machine=hypotheses.machine_of(prof))
    assert A["hypotheses"] == [] and A["family"] is None


# ---------------------------------------------------------------- claims held to the assessment
@pytest.mark.parametrize("text,hyp,status,verdict", [
    ("Weak cell 7 is the root cause.", None, None, "rejected"),
    ("The under-voltage protection caused the incident.", None, None, "rejected"),
    ("SOC estimation drift is the root cause.", None, None, "downgraded"),
    ("The SOC drop is explained by the measured current.", None, None, "rejected"),
    ("The SOC correction is not explained by the measured current.", None, None, "accepted"),
    ("Capacity loss is excluded.", "B", "excluded", "rejected"),
    ("Cell imbalance is a possible additional contributor.", None, None, "accepted"),
    ("SOC drift, 85% likely.", None, None, "flagged"),
    # negations and reports of another case are not causal claims about this recording (seen in a real model run)
    ("HV loss was the BMS under-voltage protection acting (protective response), not the origin. No root cause is confirmed.", None, None, "accepted"),
    ("SOC estimation drift is open here; in an earlier case A was confirmed by Engineer B, which only orders the next check.", "A", "open", "accepted"),
    ("The under-voltage protection is not the root cause.", None, None, "accepted"),
    ("The protection acted. Weak cell 7 is the root cause.", None, None, "rejected"),
])
def test_constrain_claims(case, text, hyp, status, verdict):
    *_, checks, _, A = case
    r = hypotheses.constrain_claims([{"text": text, "hypothesis": hyp, "asserted_status": status}], A, checks)[0]
    assert r["verdict"] == verdict, r["reasons"]


# ---------------------------------------------------------------- next decisive check
def test_telemetry_cannot_distinguish_open_causes(case):
    *_, checks, _, A = case
    P = planner.plan(A, checks)
    assert P["telemetry_verdict"] and "cannot distinguish" in P["telemetry_verdict"]
    assert "REC-TELEMETRY" not in [r["id"] for r in P["recommended"]]


def test_plan_records_discrimination_predictions_and_criteria(case):
    *_, checks, _, A = case
    P = planner.plan(A, checks)
    top = P["recommended"][0]
    # an existing independently metered record separates the most causes without cycling the pack (EE advisor, 2026-11-16)
    assert top["id"] == "DATA-METERED-CYCLE" and top["rank"] == 1 and top["provenance"]["kind"] == "expert_proposal"
    assert {("A", "B"), ("A", "C"), ("B", "C")} <= set(map(tuple, top["distinguishes"]))
    assert {"A", "B", "C"} <= set(top["predictions"]) and top["confirms"] and top["weakens"]
    doc = next(r for r in P["recommended"] if r["id"] == "DOC-BMS-HISTORY")
    assert sorted(map(tuple, doc["distinguishes"])) == [("A", "B"), ("A", "C"), ("B", "C")]
    assert top["requires"] and top["safety"]["qualification"] == "none" and top["effort"] == "low"


def test_hv_procedures_are_never_recommended_to_unqualified(case):
    *_, checks, _, A = case
    P = planner.plan(A, checks, requester_qualification="none")
    rec_ids = [r["id"] for r in P["recommended"]]
    for hv in ("TEST-CAPACITY", "MEAS-INDEPENDENT-CURRENT", "INSPECT-HARNESS"):
        assert hv not in rec_ids
        assert hv in [r["id"] for r in P["handoff"]]
    assert all(not r["safety"]["hv_exposure"] for r in P["recommended"])
    assert all("not to be carried out by the requester" in r["handoff"] for r in P["handoff"])


def test_qualified_requester_gets_the_existing_metered_record_before_cycling_the_pack(case):
    *_, checks, _, A = case
    P = planner.plan(A, checks, requester_qualification="hv_qualified")
    ids = [r["id"] for r in P["recommended"]]
    assert ids[0] == "DATA-METERED-CYCLE" and ids.index("DATA-METERED-CYCLE") < ids.index("TEST-CAPACITY")
    cap = next(r for r in P["recommended"] if r["id"] == "TEST-CAPACITY")
    assert any("recovery" in p for p in cap["preconditions"]) and any("possibly faulty pack" in p for p in cap["preconditions"])


def test_safety_hold_precedes_ranking(case):
    *_, checks, _, A = case
    P = planner.plan(A, checks)
    assert P["safety_holds"] and P["safety_holds"][0]["id"] == "HOLD-HV-RECLOSURE"
    assert "not a finding of fault" in P["safety_holds"][0]["type"]


def test_done_checks_drop_out(case):
    *_, checks, _, A = case
    P = planner.plan(A, checks, done=["DOC-BMS-HISTORY"])
    assert "DOC-BMS-HISTORY" not in [r["id"] for r in P["recommended"]]


# ---------------------------------------------------------------- the AI investigator, held to the checks
def _tool(i, name, **inp):
    return {"type": "tool_use", "id": f"t{i}", "name": name, "input": inp}


def test_agent_uses_checks_and_its_claims_are_constrained(case):
    rec, prof, *_ = case
    model = ScriptedModel([
        {"content": [_tool(1, "checks_overview"), _tool(2, "hypotheses")]},
        {"content": [_tool(3, "samples", window="fault", role="soc", t0=590, t1=650), _tool(4, "next_checks")]},
        {"content": [_tool(5, "submit_report", summary="Pack under-voltage protection after an SOC estimate that was far too high.",
                           hypotheses=[{"id": "D", "status": "root_cause", "statement": "Weak cell 7 is the root cause."},
                                       {"id": "A", "status": "open", "statement": "SOC estimation drift remains open."},
                                       {"id": "B", "status": "excluded", "statement": "Capacity loss is excluded."}],
                           next_check="DOC-BMS-HISTORY")]},
    ])
    out = agent_bridge.investigate(rec, prof, model=model)
    assert out["mode"] == "agent" and out["synthetic"] is True
    tools_used = [t["tool"] for t in out["trace"]]
    assert tools_used[:2] == ["checks_overview", "hypotheses"] and "samples" in tools_used
    v = {c["hypothesis"]: c["verdict"] for c in out["constrained_claims"] if c.get("hypothesis")}
    assert v == {"D": "rejected", "A": "accepted", "B": "rejected"}
    assert out["accepted_as_is"] is False
    assert out["deterministic_assessment"]["open_upstream"] == ["A", "B", "C"]   # unchanged by the agent


def test_agent_blocked_on_confidential_data_without_permission():
    rec = S.incident()
    rec.synthetic = False
    out = agent_bridge.investigate(rec, S.profile(), model=None, allow_external_model=False)
    assert out["mode"] == "blocked" and "VEYRA_PRIVATE_ALLOW_AGENT" in out["reason"]


def test_agent_unavailable_is_reported_not_fabricated():
    class NoModel:
        model = "none"

        def available(self):
            return False
    out = agent_bridge.investigate(S.incident(), S.profile(), model=NoModel())
    assert out["mode"] == "unavailable" and "agent_report" not in out


def test_agent_tool_errors_are_returned_to_the_model():
    model = ScriptedModel([{"content": [_tool(1, "check_detail", check_id="nope")]},
                           {"content": [_tool(2, "submit_report", summary="Open.", hypotheses=[])]}])
    out = agent_bridge.investigate(S.incident(), S.profile(), model=model)
    assert out["trace"][0]["error"] is True and out["mode"] == "agent"


def test_correction_context_reports_sampling_and_preceding_escalation():
    """Each SOC correction states its sampling quality and the escalation just before it (context, not cause)."""
    b = S.Builder("ctx")
    for k in range(3000):                    # 0 .. 300 s, 6 A; level 1 -> 2 at 150 s; SOC ramps down 20 pp from 151 s
        t = round(k * 0.1, 3)
        bias = 0.0 if t < 151 else -20.0 * min(1.0, (t - 151) / 20.0)
        soc = round(round((60.0 - 6.0 * t / 3600 / 100 * 100 + bias) / 0.4) * 0.4, 3)
        b.frame("w", t, "BMS_STATUS", i_pack=6.0, soc=soc, cap_ah=100.0, fault_raw=1 if t < 150 else 2, bms_state=0)
    rec, prof = b.done(), S.profile()
    F = facts.derive(rec, prof, base.run_all(rec, prof))
    ctx = F["correction_context"]["value"][0]
    assert ctx["sampling_clean"] and ctx["segments"] and ctx["max_soc_interval_s"] <= 0.11
    assert ctx["preceding_escalation"]["from"] == 1 and ctx["preceding_escalation"]["to"] == 2
    assert 1.0 <= ctx["preceding_escalation"]["delay_s"] <= 2.5
    assert "not say which upstream cause" not in F["correction_context"]["statement"]   # one correction: no shared-pattern claim
