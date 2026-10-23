"""Five defects found by an external reviewer (2026-10-23), each with the reviewer's acceptance condition.

1. Analysis sets must not depend on the display cap of the command list (501 commands all inside the window -> 0 outside).
2. "The batch did not slow down" is not "no delay": one slow command among normal ones must not read as no delay.
3. A person's "reopened" stays open; the engine cannot put the part back to "supported" over it.
4. Ruling out one measured scope does not rule out the whole part; new evidence is not covered by an old exclusion.
5. A delay change measured across unsynchronised clocks stays conditional: it can call for a clock check, never "supported".
"""
import pytest

from app.analysis import command_chain
from app.engine import investigate as engine
from app.engine import triage

C = "vehicle_clock"
SKEW = {C: None}


def chain_rows(latencies_ms, t0, step=1.0, prefix="cmd"):
    """Commands on one clock: received -> output (+20 ms) -> feedback (+latency)."""
    rows = []
    for i, lat in enumerate(latencies_ms):
        t, cid = t0 + i * step, f"{prefix}-{i:05d}"
        rows += [{"signal": "command.received", "t": t, "clock_domain": C, "command_id": cid, "row": 3 * i},
                 {"signal": "command.output", "t": t + 0.02, "clock_domain": C, "command_id": cid, "row": 3 * i + 1},
                 {"signal": "vehicle.feedback", "t": t + 0.02 + lat / 1000, "clock_domain": C, "command_id": cid, "row": 3 * i + 2}]
    return rows


# ---- 1 ----
@pytest.mark.parametrize("cap", [50, 500, 5000])
def test_1_analysis_does_not_depend_on_the_display_cap(monkeypatch, cap):
    monkeypatch.setattr(command_chain, "MAX_COMMANDS_LISTED", cap)
    rows = chain_rows([100.0] * 501, t0=1000.0, step=0.5)          # 501 commands, all inside the window
    out = engine._command_chain(rows, (999.0, 2000.0), SKEW)
    assert out["inside_commands"] == 501 and out["outside_commands"] == 0


# ---- 2 ----
def _vehicle_segment(inside_ms, outside_ms):
    rows = chain_rows(outside_ms, t0=0.0, prefix="before") + chain_rows(inside_ms, t0=1000.0, prefix="during")
    return engine._command_chain(rows, (999.0, 1100.0), SKEW)["segments"]["command.output->vehicle.feedback"]


def test_2_one_slow_command_is_not_no_delay():
    seg = _vehicle_segment([10.0, 10.0, 1000.0], [10.0 + (i % 3) for i in range(30)])
    assert seg["status"] != "no_delay_observed_in_segment"
    assert seg["status"] == "slow_commands_in_window" and seg["slow_commands"] == 1 and seg["max_inside_ms"] == pytest.approx(1000.0)
    domains, _ = engine._domains({"measurements": [], "heartbeats": [], "events": {}, "command_chain": {"segments": {"command.output->vehicle.feedback": seg}},
                                  "input_response": {}}, {"attributes": {}})
    assert domains["vehicle_interface"]["status"] != "no_delay_observed_in_measured_part"
    board = triage.hypothesis_board(domains, [])
    routed = triage.propose(domains, board, [], [])["proposed"]["route_away"]
    assert "vehicle_interface" not in [r["domain"] for r in routed]


def test_2b_every_command_within_normal_is_a_real_negative():
    seg = _vehicle_segment([10.0, 11.0, 12.0], [10.0 + (i % 3) for i in range(30)])
    assert seg["status"] == "no_delay_observed_in_segment" and seg["max_inside_ms"] <= seg["outside_p95_ms"] + 20


# ---- 3 ----
def verdict(domain, status, run_id="run-1", **extra):
    return {"id": f"led-{domain}-{status}", "kind": "hypothesis_status", "actor": "Engineer", "recorded_at": "2026-10-23T10:00:00+00:00",
            "payload": {"run_id": run_id, "domain": domain, "status": status, "basis": "new counter-evidence", "scope": extra.pop("scope", None),
                        "evidence_entries": [], "finding_rules": extra.pop("finding_rules", []), **extra}}


def domains_with(**obs):
    out = {d: {"status": "not_observed", "observations": []} for d in engine.DOMAINS}
    for d, items in obs.items():
        kinds = {o["kind"] for o in items}
        out[d] = {"status": "delay_observed" if "delay" in kinds else "anomaly_observed", "observations": items}
    return out


def test_3_reopened_stays_open_over_an_engine_delay():
    domains = domains_with(network=[{"rule": "R-SEGMENT", "kind": "delay", "key": {"signal": "command.sent->command.received"}, "text": "delay"}])
    board = {b["domain"]: b for b in triage.hypothesis_board(domains, [verdict("network", "reopened")])}
    assert board["network"]["column"] == "open" and board["network"]["engine_status"] == "delay_observed"
    assert "delay" in (board["network"]["engine_hint"] or "")


# ---- 4 ----
DELAY = {"rule": "R-SEGMENT", "kind": "delay", "key": {"signal": "command.output->vehicle.feedback"}, "text": "boom output->feedback delay"}
STUCK = {"rule": "R-OUTPUT-STUCK", "kind": "anomaly", "key": {"signal": "actuator.output", "scope": "boom"}, "text": "boom output held"}


def test_4_ruling_out_one_mechanism_keeps_the_others():
    domains = domains_with(vehicle_interface=[DELAY, STUCK])
    ledger = [verdict("vehicle_interface", "ruled_out", scope="boom, three matched commands, output->feedback delay",
                      excluded_observations=[triage.observation_key(DELAY)])]
    b = {x["domain"]: x for x in triage.hypothesis_board(domains, ledger)}["vehicle_interface"]
    assert b["column"] != "ruled_out" and b["partly_ruled_out"]["remaining"] == [triage.observation_key(STUCK)]
    proposed = triage.propose(domains, list({x["domain"]: x for x in triage.hypothesis_board(domains, ledger)}.values()), [], ledger)["proposed"]
    assert "vehicle_interface" in proposed["owner"]["domains"]


def test_4b_exclusion_covering_everything_rules_out_until_new_evidence():
    domains = domains_with(vehicle_interface=[DELAY])
    ledger = [verdict("vehicle_interface", "ruled_out", excluded_observations=[triage.observation_key(DELAY)], input_hashes={"imp-1": "a"})]
    same = {x["domain"]: x for x in triage.hypothesis_board(domains, ledger, input_hashes={"imp-1": "a"})}["vehicle_interface"]
    assert same["column"] == "ruled_out"
    more = {x["domain"]: x for x in triage.hypothesis_board(domains, ledger, input_hashes={"imp-1": "a", "imp-2": "b"})}["vehicle_interface"]
    assert more["column"] != "ruled_out" and "new data" in more["human"]["note"]


# ---- 5 ----
def test_5_cross_clock_delay_change_stays_conditional():
    seg = {"domain": "network", "status": "delay_change_observed_assuming_stable_offset", "relative_change_ms": 440.0, "commands_inside": 5,
           "absolute_latency": {"status": "unavailable"}}
    domains, _ = engine._domains({"measurements": [], "heartbeats": [], "events": {}, "command_chain": {"segments": {"command.sent->command.received": seg}},
                                  "input_response": {}}, {"attributes": {}})
    o = domains["network"]["observations"][0]
    assert (o["measurement"], o["value_ms"], o["clock_stability"], o["attribution_eligible"]) == ("relative_delay_change", 440.0, "unverified", False)
    assert domains["network"]["status"] != "delay_observed"
    board = {b["domain"]: b for b in triage.hypothesis_board(domains, [])}
    assert board["network"]["column"] != "supported"


# ---- second review (2026-10-23 evening): facts before requests ----
def test_6_questions_answer_from_records_and_name_no_cause():
    from app.engine import questions
    t0 = 1000.0
    run = {"findings": {"actuator_outputs": {"findings": [{"rule": "STUCK", "channel": "rudder_rc4", "held_value": 1900.0, "t_start": t0,
                                                            "end": "1970-01-01T00:17:00Z", "unit": "us", "reference_range": [1479.0, 1516.0]}]},
                        "tracking": {"findings": [{"axis": "roll", "t_start": t0 + 0.74}]}}}
    rows = [{"signal": "actuator.output", "scope": ch, "t": t0 + dt, "value": v}
            for ch, before, after in (("aileron_rc5", 1496, 1325), ("elevator_rc2", 1548, 1546)) for dt, v in ((-2, before), (-1, before), (1, after), (2, after))]
    kg = [{"rows": [{"signal": "actuator.output", "scope": "rudder_rc4", "value": v} for v in (1355, 1615)]}]
    out = questions.answer(run, rows, kg)
    by = {q["id"].split(":")[0]: q for q in out["questions"]}
    assert "above anything rudder_rc4 output in 1 known-good run" in by["range"]["answer"]
    assert "aileron_rc5 1496→1325" in by["others"]["answer"] and "unchanged: elevator_rc2" in by["others"]["answer"]
    assert "0.74 s later" in by["order"]["answer"]
    assert by["commanded"]["status"] == "not_answerable" and by["commanded"]["check_id"] == "CHK-CONTROLLER-LOGS"
    assert by["physical"]["status"] == "not_answerable"
    assert out["next"]["check_id"] == "CHK-CONTROLLER-LOGS"          # a log someone can send comes before travel
    assert out["decision_status"] == "Further investigation needed · No cause confirmed"
    text = " ".join(out["established"]).lower()
    assert not any(w in text for w in ("servo path", "controller output", "most likely", "cause"))


def test_7_source_coverage_answers_before_asking_and_names_the_missing_record():
    from app.engine import questions
    t0 = 1536693598.701565
    run = {"findings": {"actuator_outputs": {"findings": [{"rule": "STUCK", "channel": "rudder_rc4", "held_value": 1900.0, "t_start": t0,
                                                            "end": "2018-09-11T19:20:15.697Z", "unit": "us", "reference_range": [1479.0, 1516.0]}]},
                        "tracking": {"findings": []}}}
    cov = {"source_flight": "2018-09-11-15-06-34_2", "onset_epoch_s": t0, "recorded_span_s": [-52.1, 16.9], "mode_sample_interval_s": 1.0,
           "mode_changes": [{"t_s": -52.09, "mode": "GUIDED", "armed": True}], "pilot_rudder_input_us": {"min": 1509, "max": 1509},
           "rudder_output_us": {"min": 1900, "max": 1900}, "switch_changes": [], "diagnostic_warnings": {}, "statustext_messages": 0,
           "files": {}, "not_in_recording": ["x"], "excluded": "failure_status-*"}
    out = questions.answer(run, [], [], source_coverage=cov)
    by = {q["id"].split(":")[0]: q for q in out["questions"]}
    assert by["commanded"]["status"] == "answered" and "not a stick input" in by["commanded"]["answer"] and "no mode change" in by["commanded"]["answer"]
    assert by["autopilot"]["status"] == "not_answerable" and "SERVO4_MAX" in by["autopilot"]["needs"]
    assert out["next"]["question"].startswith("Did the autopilot itself log") and "SD card" in out["next"]["why_a_person"]
    # without coverage the old question stands, and nothing claims the recording was read
    plain_out = questions.answer(run, [], [])
    assert {q["id"].split(":")[0] for q in plain_out["questions"]} >= {"commanded"} and "SD card" not in plain_out["next"]["why_a_person"]


def test_8_read_caches_are_shared_across_connections_and_invalidated_by_new_records(tmp_path):
    """Each request opens its own connection; cached answers must be reused across them and refreshed by any new record."""
    import json as _json
    from app.ingest.profile import parse_profile
    from app.store import sqlite_store
    from app.store.sqlite_store import CaseStore
    prof = parse_profile({"profile_id": "t", "version": 1, "format": "jsonl", "synthetic": False,
                          "columns": {"timestamp": "t", "asset": "asset", "signal": "signal", "value": "value", "unit": "unit", "scope": "scope"},
                          "signal_map": {"rc_out": "actuator.output"}, "clock_domain": "c", "timestamp_format": "epoch", "epoch_unit": "ns"})
    db = tmp_path / "c.db"
    s1 = CaseStore(db)
    c = s1.create_case(machine_id="M1", title="t", actor="a", synthetic=False)
    rows = "".join(_json.dumps({"t": int((1_700_000_000 + i) * 1e9), "asset": "M1", "signal": "rc_out", "value": 1500 + i, "unit": "us", "scope": "x"}) + "\n" for i in range(50))
    s1.commit_import(c["id"], rows.encode(), "a.jsonl", prof, actor="a")
    s1.investigate(c["id"], actor="a")
    calls = []
    real = s1._questions.__func__
    sqlite_store.CaseStore._questions = lambda self, cid: (calls.append(cid), real(self, cid))[1]
    try:
        first = s1.questions(c["id"]); s1.close()
        s2 = CaseStore(db)
        assert s2.questions(c["id"]) == first and len(calls) == 1          # a new connection reuses the answer
        series = s2.series(c["id"], "actuator.output", "x", 1_700_000_000, 1_700_000_049)
        assert series["samples"] == 50 and series["points"][0][1] == 1500.0
        s2.append(c["id"], "triage", {"severity": "low", "owner": "Team", "reason": "r"}, actor="a")
        s2.questions(c["id"])
        assert len(calls) == 2                                              # a new record invalidates it
        s2.close()
    finally:
        sqlite_store.CaseStore._questions = real
