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
