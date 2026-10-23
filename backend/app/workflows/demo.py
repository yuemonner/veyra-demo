"""Guided end-to-end demo on SYNTHETIC remote-delay fixtures (for onboarding).

Every step goes through the same validated store/ledger paths an engineer uses. Actions and
outcomes in the demo are explicitly marked simulated; nothing here is field evidence.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.workflows import report as report_html
from app.ingest.profile import parse_profile

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "synthetic" / "remote_delay"
FILES = (("synthetic_ex1_console.csv", "synthetic_console_profile.json"),
         ("synthetic_ex1_vehicle.jsonl", "synthetic_vehicle_profile.json"),
         ("synthetic_ex1_vehicle_io.csv", "synthetic_vehicle_io_profile.json"))
ENGINEER, REVIEWER = "Field engineer (synthetic demo)", "Senior engineer (synthetic demo)"


def run_demo(store, out_dir: Path, verbose: bool = True) -> int:
    say = print if verbose else (lambda *a, **k: None)
    out_dir.mkdir(parents=True, exist_ok=True)
    ensure_machine_class(store)
    case = store.create_case(machine_id="EX1", title="EX1 remote response delay (SYNTHETIC demo)", actor=ENGINEER, synthetic=True,
                             symptom_start="2026-03-04T09:06:00Z", symptom_end="2026-03-04T09:07:30Z",
                             symptom_source="Synthetic operator report: 'boom reacts late' relayed by site supervisor")
    cid = case["id"]
    say(f"1. Case {cid} for machine EX1 with symptom window 09:06:00–09:07:30 UTC (synthetic).")
    for data, profile in FILES:
        record, result = store.commit_import(cid, (FIXTURES / data).read_bytes(), data, parse_profile(json.loads((FIXTURES / profile).read_text())), actor=ENGINEER)
        say(f"2. Imported {data}: {record['status']}, {len(result.rows)} rows, clock {record['clock_domain']}.")

    run = store.investigate(cid, actor=ENGINEER)
    io = run["findings"]["input_response"]["channels"][0]
    say(f"3. First run {run['id']}: boom input–response '{io['status']}' because context is only reported, not confirmed.")

    for key in ("oem", "model", "kit_version", "control_version", "firmware_version", "vehicle_interface"):
        value = store.context("EX1")["attributes"][key]["value"]
        store.assert_context({"machine_id": "EX1", "key": key, "value": value, "status": "engineer_confirmed", "actor": ENGINEER,
                              "source": "Synthetic: machine plate and kit inventory photo", "effective_from": "2026-03-01T00:00:00Z", "evidence": ["synthetic-photo-001"]})
    store.assert_context({"machine_id": "EX1", "key": "remote_access", "value": "read_only_authorized", "status": "engineer_confirmed", "actor": ENGINEER,
                          "source": "Synthetic: operator email granting read-only log access", "effective_from": "2026-03-04T10:30:00Z", "evidence": ["synthetic-email-7"]})
    say("4. Confirmed machine identity, kit, software, firmware and interface; read-only access authorized (synthetic evidence).")

    run = store.investigate(cid, actor=ENGINEER)
    chain = run["findings"]["command_chain"]["segments"]
    say("5. Second run " + run["id"] + ":")
    for name, domain in run["domains"].items():
        say(f"     {name:<18} {domain['status']}")
    say(f"     transport segment: {chain['command.sent->command.received']['status']}")
    say("     top checks: " + ", ".join(f"{c['rank']}. {c['check_id']}" for c in run["checks"][:4]))

    store.append(cid, "check_review", {"run_id": run["id"], "check_id": "CHK-SOURCE-ROWS", "verdict": "accepted", "reason": "Confirm coverage before attributing delay"}, actor=ENGINEER, source="demo")
    executed = store.execute_automatic(cid, "CHK-SOURCE-ROWS", actor=ENGINEER)
    say(f"6. Accepted and executed CHK-SOURCE-ROWS read-only: {executed['result']['count']} records inside the window.")

    store.append(cid, "check_review", {"run_id": run["id"], "check_id": "CHK-NETWORK-LOGS", "verdict": "edited", "reason": "Limit export to the APN gateway to reduce load",
                                       "edited_procedure": "Export APN gateway session log 09:00–09:15 UTC only"}, actor=ENGINEER, source="demo")
    store.append(cid, "check_execution", {"run_id": run["id"], "check_id": "CHK-NETWORK-LOGS", "status": "performed", "executed_at": "2026-03-04T10:40:00Z",
                                          "performer": "Synthetic network engineer", "observations": "Synthetic: cell handover storm 09:05:50–09:07:40 on the machine's LTE cell",
                                          "evidence_note": "Synthetic gateway log excerpt attached to ticket SYN-42"}, actor=ENGINEER, source="synthetic ticket SYN-42")
    say("7. Edited CHK-NETWORK-LOGS (manual only) and recorded its result: handover storm in the window (synthetic).")

    decision = store.append(cid, "decision", {"run_id": run["id"], "options_considered": ["Pin the machine to a fixed LTE cell and monitor", "Dispatch network technician", "Dispatch hydraulic technician"],
                                              "chosen_option": "Pin the machine to a fixed LTE cell and monitor",
                                              "rationale": "Delay appears before vehicle receipt; vehicle-side segments and boom input–response unchanged; handover storm reported in the window."},
                            actor=ENGINEER, source="demo")
    store.append(cid, "dispatch_preparation", {"run_id": run["id"], "remote_resolution": "possible", "prepared": ["Router admin access confirmed (synthetic)"],
                                               "blockers": ["Clock offset still unbounded"]}, actor=ENGINEER, source="demo")
    action = store.append(cid, "action", {"decision_id": decision["id"], "action_type": "network_change", "description": "Synthetic: cell pinning applied on the vehicle router",
                                          "status": "simulated", "performed_at": "2026-03-04T11:00:00Z", "performer": "Synthetic network engineer"}, actor=ENGINEER, source="demo")
    outcome = store.append(cid, "outcome", {"action_id": action["id"], "result": "recovered", "observed_from": "2026-03-04T11:00:00Z", "observed_to": "2026-03-04T13:00:00Z",
                                            "verification": "unverified", "observations": "Synthetic: operator reports normal response for two hours"}, actor=ENGINEER, source="demo")
    store.append(cid, "outcome_review", {"outcome_id": outcome["id"], "verdict": "accepted", "reason": "Consistent with the synthetic gateway log; still unverified telemetry"},
                 actor=REVIEWER, source="demo")
    say("8. Decision, dispatch preparation, simulated action, unverified outcome and second-person review recorded.")

    report = out_dir / f"{cid}.html"
    report.write_text(report_html.render(store, cid), encoding="utf-8")
    episodes = out_dir / f"{cid}.episodes.jsonl"
    with open(episodes, "w", encoding="utf-8") as handle:
        for episode in store.episodes(cid):
            handle.write(json.dumps(episode, default=str) + "\n")
    verify = store.verify(cid)
    say(f"9. Report: {report}\n   Episodes: {episodes}\n   Ledger chain valid: {verify['ledger_chain']['valid']}; imports intact: {verify['imports_intact']}; runs intact: {verify['runs_intact']}")
    return 0 if verify["ledger_chain"]["valid"] and verify["imports_intact"] and verify["runs_intact"] else 4


STORY_TITLE = "EX1 boom kept moving after joystick release (SYNTHETIC)"
PRIOR_TITLE = "EX1 commands felt late (SYNTHETIC, earlier session)"
OPERATOR_A, OPERATOR_B = "Operator A (synthetic)", "Operator B (synthetic)"
NETWORK_ENGINEER = "Network engineer (synthetic)"


def _import(store, cid, data, profile, actor=ENGINEER):
    return store.commit_import(cid, (FIXTURES / data).read_bytes(), data, parse_profile(json.loads((FIXTURES / profile).read_text())), actor=actor)



def ensure_machine_class(store, machine_id: str = "EX1", value: str = "remote_excavator") -> None:
    """SYNTHETIC: record what kind of machine EX1 is, once, so the excavator profile names its domains and teams."""
    current = ((store.context(machine_id).get("attributes") or {}).get("machine_class") or {}).get("value")
    if current != value:
        store.assert_context({"machine_id": machine_id, "key": "machine_class", "value": value, "status": "engineer_confirmed", "actor": ENGINEER,
                              "source": "Synthetic: machine plate photo", "effective_from": "2026-01-01T00:00:00Z", "evidence": ["synthetic-photo-001"]})

def build_field_case_story(store) -> dict:
    """SYNTHETIC field-case story for the product demo. Every record goes through the validated store and ledger.

    1. An earlier case on EX1 (Operator B paused work; network delay; router restart, partial recovery).
    2. Today's case opened by Operator A's takeover, triaged, compared against a declared known-good window,
       brief events scanned, hypotheses ruled in and out with evidence, the symptom reproduced on a bench,
       the intervention reviewed, ownership handed off once, then a decision and an unverified outcome.
    Actions and outcomes are simulated. Nothing here is field evidence.
    """
    ensure_machine_class(store)
    prior = store.create_case(machine_id="EX1", title=PRIOR_TITLE, actor=ENGINEER, synthetic=True,
                              symptom_start="2026-02-18T14:06:00Z", symptom_end="2026-02-18T14:07:30Z",
                              symptom_source="Synthetic: Operator B paused work and reported late response by radio")
    pid = prior["id"]
    for data, profile in (("synthetic_ex1_prior_console.csv", "synthetic_console_profile.json"), ("synthetic_ex1_prior_vehicle.jsonl", "synthetic_vehicle_profile.json")):
        _import(store, pid, data, profile)
    store.append(pid, "intervention_report", {"occurred_at": "2026-02-18T14:06:40Z", "intervention_type": "pause", "operator": OPERATOR_B,
                                              "stated_reason": "Commands felt late; paused to check before continuing", "reported_via": "Radio to site supervisor (synthetic)"},
                 actor=ENGINEER, source="synthetic site report")
    prior_run = store.investigate(pid, actor=ENGINEER)
    store.append(pid, "triage", {"severity": "medium", "owner": "Network & connectivity", "reason": "Delay before vehicle receipt; operator paused, no safety event",
                                 "run_id": prior_run["id"]}, actor=ENGINEER, source="demo")
    decision = store.append(pid, "decision", {"run_id": prior_run["id"], "options_considered": ["Restart the vehicle router and monitor", "Dispatch network technician"],
                                              "chosen_option": "Restart the vehicle router and monitor", "rationale": "Delay sits before vehicle receipt; cheapest reversible step first"},
                            actor=ENGINEER, source="demo")
    action = store.append(pid, "action", {"decision_id": decision["id"], "action_type": "remote_restart", "description": "Synthetic: vehicle router restarted remotely",
                                          "status": "simulated", "performed_at": "2026-02-18T15:00:00Z", "performer": NETWORK_ENGINEER}, actor=ENGINEER, source="demo")
    store.append(pid, "outcome", {"action_id": action["id"], "result": "partially_recovered", "observed_from": "2026-02-18T15:00:00Z", "observed_to": "2026-02-18T17:00:00Z",
                                  "verification": "unverified", "observations": "Synthetic: operator reports mostly normal response; occasional lag remains"}, actor=ENGINEER, source="demo")
    store.append(pid, "case_closure", {"run_id": prior_run["id"], "cause_domain": "network",
                                       "cause": "Synthetic: the vehicle router fell back to a weak cell at the north end of the site; commands queued before reaching the machine",
                                       "investigation_status": "cause_probable", "confirmed_by": "evidence_in_case",
                                       "evidence_note": "Synthetic: delay sat before vehicle receipt in the command-chain timing; router log showed the cell change",
                                       "resolution_status": "temporary_workaround",
                                       "fix": "Router restarted and locked to the site's main cell", "fix_held": "partly", "decisive_check": "CHK-CMD-CHAIN",
                                       "hours_to_cause": 3, "would_have_saved_time": "Synthetic: compare command-chain timing first; it located the delay in minutes"},
                 actor=NETWORK_ENGINEER, source="demo")

    case = store.create_case(machine_id="EX1", title=STORY_TITLE, actor=ENGINEER, synthetic=True,
                             symptom_start="2026-03-04T09:06:00Z", symptom_end="2026-03-04T09:07:30Z",
                             symptom_source="Synthetic: Operator A pressed takeover on the remote console and reported the boom kept moving")
    cid = case["id"]
    store.append(cid, "intervention_report", {"occurred_at": "2026-03-04T09:06:58Z", "intervention_type": "takeover", "operator": OPERATOR_A, "operator_role": "Remote operator",
                                              "stated_reason": "Boom kept moving after I released the joystick, so I took over and stopped it",
                                              "reported_via": "Remote console takeover button and shift note (synthetic)"}, actor=ENGINEER, source="synthetic console event")
    store.append(cid, "triage", {"severity": "high", "owner": "Remote-control software", "reason": "Machine did not follow the joystick release; looks like a control software issue at first sight"},
                 actor=ENGINEER, source="demo")
    for data, profile in FILES + (("synthetic_ex1_hydraulics.csv", "synthetic_hydraulics_profile.json"),):
        _import(store, cid, data, profile)
    store.investigate(cid, actor=ENGINEER)

    store.append(cid, "reference_declared", {"window_from": "2026-03-04T09:00:00Z", "window_to": "2026-03-04T09:05:30Z",
                                             "basis": "Normal digging before the complaint. Operator A confirmed no issues; same machine, kit and software (synthetic)",
                                             "reference_source": "Shift note and operator call (synthetic)"}, actor=ENGINEER, source="demo")
    for key in ("oem", "model", "kit_version", "control_version", "firmware_version", "vehicle_interface"):
        value = store.context("EX1")["attributes"][key]["value"]
        store.assert_context({"machine_id": "EX1", "key": key, "value": value, "status": "engineer_confirmed", "actor": ENGINEER,
                              "source": "Synthetic: machine plate and kit inventory photo", "effective_from": "2026-03-01T00:00:00Z", "evidence": ["synthetic-photo-001"]})
    store.assert_context({"machine_id": "EX1", "key": "remote_access", "value": "read_only_authorized", "status": "engineer_confirmed", "actor": ENGINEER,
                          "source": "Synthetic: operator email granting read-only log access", "effective_from": "2026-03-04T10:30:00Z", "evidence": ["synthetic-email-7"]})
    run = store.investigate(cid, actor=ENGINEER)
    store.append(cid, "triage", {"severity": "high", "owner": "Network & connectivity", "run_id": run["id"],
                                 "reason": "Against the known-good window, delay appears before vehicle receipt; vehicle-side boom response unchanged"},
                 actor=ENGINEER, source="demo")

    for check_id, reason in (("CHK-SOURCE-ROWS", "Confirm coverage before attributing anything"), ("CHK-BRIEF-EVENTS", "Find sub-second events around the takeover")):
        store.append(cid, "check_review", {"run_id": run["id"], "check_id": check_id, "verdict": "accepted", "reason": reason}, actor=ENGINEER, source="demo")
        store.execute_automatic(cid, check_id, actor=ENGINEER)
    store.append(cid, "check_review", {"run_id": run["id"], "check_id": "CHK-NETWORK-LOGS", "verdict": "edited", "reason": "Limit export to the APN gateway to reduce load",
                                       "edited_procedure": "Export APN gateway session log 09:00–09:15 UTC only"}, actor=ENGINEER, source="demo")
    logs = store.append(cid, "check_execution", {"run_id": run["id"], "check_id": "CHK-NETWORK-LOGS", "status": "performed", "executed_at": "2026-03-04T10:40:00Z",
                                                 "performer": NETWORK_ENGINEER, "observations": "Synthetic: cell handover storm 09:05:50–09:07:40 on the machine's LTE cell",
                                                 "evidence_note": "Synthetic gateway log excerpt attached to ticket SYN-42"}, actor=ENGINEER, source="synthetic ticket SYN-42")
    store.append(cid, "check_review", {"run_id": run["id"], "check_id": "CHK-REPRODUCE", "verdict": "accepted", "reason": "Test the late-command mechanism before anyone travels"},
                 actor=ENGINEER, source="demo")
    repro = store.append(cid, "reproduction_attempt", {"run_id": run["id"], "environment": "log_replay", "platform": "Bench controller with replayed command log (synthetic)",
                                                        "setup": "Replayed the 09:05–09:08 command stream through the bench controller with 450 ms delay injected on the LTE leg",
                                                        "result": "reproduced", "performed_at": "2026-03-04T11:20:00Z",
                                                        "observations": "Synthetic: boom kept moving for ~0.5 s after joystick release, matching the operator's description",
                                                        "evidence_note": "Synthetic bench recording BENCH-7"}, actor="Controls engineer (synthetic)", source="synthetic bench")
    store.append(cid, "hypothesis_status", {"run_id": run["id"], "domain": "network", "status": "supported", "evidence_entries": [logs["id"], repro["id"]],
                                            "finding_rules": ["R-SEGMENT-RELATIVE"], "basis": "Handover storm in the window, delay before vehicle receipt, symptom reproduced with injected link delay"},
                 actor=ENGINEER, source="demo")
    store.append(cid, "hypothesis_status", {"run_id": run["id"], "domain": "vehicle_body", "status": "weakened", "finding_rules": ["R-IO"],
                                            "basis": "Boom input→response matches the known-good window. The pressure spike 40 ms after the takeover fits the stop itself; the 09:06:31 dropout is still unexplained",
                                            "scope": "Boom channel only. Arm, bucket and swing were not measured"}, actor=ENGINEER, source="demo")
    store.append(cid, "hypothesis_status", {"run_id": run["id"], "domain": "vehicle_interface", "status": "ruled_out", "finding_rules": ["R-SEGMENT"],
                                            "basis": "Controller output to vehicle feedback timing unchanged for matched commands inside the window",
                                            "scope": "Commands with matched feedback records only"}, actor=ENGINEER, source="demo")
    intervention = next(e for e in store.ledger(cid) if e["kind"] == "intervention_report")
    store.append(cid, "intervention_review", {"intervention_id": intervention["id"], "run_id": run["id"], "verdict": "necessary",
                                              "reason": "Commands arriving 400+ ms late keep the boom moving after release; reproduced on the bench. Stopping was the right call"},
                 actor="Senior engineer (synthetic demo)", source="demo")

    run = store.investigate(cid, actor=ENGINEER)
    decision = store.append(cid, "decision", {"run_id": run["id"], "options_considered": ["Pin the machine to a fixed LTE cell and monitor", "Dispatch network technician", "Dispatch hydraulic technician"],
                                              "chosen_option": "Pin the machine to a fixed LTE cell and monitor",
                                              "rationale": "Delay before vehicle receipt, handover storm in the window, reproduced on the bench; vehicle side weakened for the boom channel"},
                            actor=ENGINEER, source="demo")
    store.append(cid, "dispatch_preparation", {"run_id": run["id"], "remote_resolution": "possible", "prepared": ["Router admin access confirmed (synthetic)"],
                                               "blockers": ["Clock offset still unbounded", "09:06:31 pressure dropout unexplained"]}, actor=ENGINEER, source="demo")
    action = store.append(cid, "action", {"decision_id": decision["id"], "action_type": "network_change", "description": "Synthetic: cell pinning applied on the vehicle router",
                                          "status": "simulated", "performed_at": "2026-03-04T12:00:00Z", "performer": NETWORK_ENGINEER}, actor=ENGINEER, source="demo")
    store.append(cid, "outcome", {"action_id": action["id"], "result": "recovered", "observed_from": "2026-03-04T12:00:00Z", "observed_to": "2026-03-04T14:00:00Z",
                                  "verification": "unverified", "observations": "Synthetic: Operator A reports normal response for two hours"}, actor=ENGINEER, source="demo")
    return {"case_id": cid, "prior_case_id": pid, "run_id": run["id"], "synthetic": True,
            "boundary": "SYNTHETIC demo records. not field evidence."}
