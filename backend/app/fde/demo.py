"""Guided end-to-end demo on SYNTHETIC remote-delay fixtures (for FDE onboarding).

Every step goes through the same validated store/ledger paths an engineer uses. Actions and
outcomes in the demo are explicitly marked simulated; nothing here is field evidence.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.fde import report as report_html
from app.ingest.profile import parse_profile

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "synthetic" / "remote_delay"
FILES = (("synthetic_ex1_console.csv", "synthetic_console_profile.json"),
         ("synthetic_ex1_vehicle.jsonl", "synthetic_vehicle_profile.json"),
         ("synthetic_ex1_vehicle_io.csv", "synthetic_vehicle_io_profile.json"))
FDE, REVIEWER = "FDE (synthetic demo)", "Senior engineer (synthetic demo)"


def run_demo(store, out_dir: Path, verbose: bool = True) -> int:
    say = print if verbose else (lambda *a, **k: None)
    out_dir.mkdir(parents=True, exist_ok=True)
    case = store.create_case(machine_id="EX1", title="EX1 remote response delay (SYNTHETIC demo)", actor=FDE, synthetic=True,
                             symptom_start="2026-03-04T09:06:00Z", symptom_end="2026-03-04T09:07:30Z",
                             symptom_source="Synthetic operator report: 'boom reacts late' relayed by site supervisor")
    cid = case["id"]
    say(f"1. Case {cid} for machine EX1 with symptom window 09:06:00–09:07:30 UTC (synthetic).")
    for data, profile in FILES:
        record, result = store.commit_import(cid, (FIXTURES / data).read_bytes(), data, parse_profile(json.loads((FIXTURES / profile).read_text())), actor=FDE)
        say(f"2. Imported {data}: {record['status']}, {len(result.rows)} rows, clock {record['clock_domain']}.")

    run = store.investigate(cid, actor=FDE)
    io = run["findings"]["input_response"]["channels"][0]
    say(f"3. First run {run['id']}: boom input–response '{io['status']}' because context is only reported, not confirmed.")

    for key in ("oem", "model", "kit_version", "control_version", "firmware_version", "vehicle_interface"):
        value = store.context("EX1")["attributes"][key]["value"]
        store.assert_context({"machine_id": "EX1", "key": key, "value": value, "status": "engineer_confirmed", "actor": FDE,
                              "source": "Synthetic: machine plate and kit inventory photo", "effective_from": "2026-03-01T00:00:00Z", "evidence": ["synthetic-photo-001"]})
    store.assert_context({"machine_id": "EX1", "key": "remote_access", "value": "read_only_authorized", "status": "engineer_confirmed", "actor": FDE,
                          "source": "Synthetic: customer email granting read-only log access", "effective_from": "2026-03-04T10:30:00Z", "evidence": ["synthetic-email-7"]})
    say("4. Confirmed machine identity, kit, software, firmware and interface; read-only access authorized (synthetic evidence).")

    run = store.investigate(cid, actor=FDE)
    chain = run["findings"]["command_chain"]["segments"]
    say("5. Second run " + run["id"] + ":")
    for name, domain in run["domains"].items():
        say(f"     {name:<18} {domain['status']}")
    say(f"     transport segment: {chain['command.sent->command.received']['status']}")
    say("     top checks: " + ", ".join(f"{c['rank']}. {c['check_id']}" for c in run["checks"][:4]))

    store.append(cid, "check_review", {"run_id": run["id"], "check_id": "CHK-SOURCE-ROWS", "verdict": "accepted", "reason": "Confirm coverage before attributing delay"}, actor=FDE, source="demo")
    executed = store.execute_automatic(cid, "CHK-SOURCE-ROWS", actor=FDE)
    say(f"6. Accepted and executed CHK-SOURCE-ROWS read-only: {executed['result']['count']} records inside the window.")

    store.append(cid, "check_review", {"run_id": run["id"], "check_id": "CHK-NETWORK-LOGS", "verdict": "edited", "reason": "Limit export to the APN gateway to reduce load",
                                       "edited_procedure": "Export APN gateway session log 09:00–09:15 UTC only"}, actor=FDE, source="demo")
    store.append(cid, "check_execution", {"run_id": run["id"], "check_id": "CHK-NETWORK-LOGS", "status": "performed", "executed_at": "2026-03-04T10:40:00Z",
                                          "performer": "Synthetic network engineer", "observations": "Synthetic: cell handover storm 09:05:50–09:07:40 on the machine's LTE cell",
                                          "evidence_note": "Synthetic gateway log excerpt attached to ticket SYN-42"}, actor=FDE, source="synthetic ticket SYN-42")
    say("7. Edited CHK-NETWORK-LOGS (manual only) and recorded its result: handover storm in the window (synthetic).")

    decision = store.append(cid, "decision", {"run_id": run["id"], "options_considered": ["Pin the machine to a fixed LTE cell and monitor", "Dispatch network technician", "Dispatch hydraulic technician"],
                                              "chosen_option": "Pin the machine to a fixed LTE cell and monitor",
                                              "rationale": "Delay appears before vehicle receipt; vehicle-side segments and boom input–response unchanged; handover storm reported in the window."},
                            actor=FDE, source="demo")
    store.append(cid, "dispatch_preparation", {"run_id": run["id"], "remote_resolution": "possible", "prepared": ["Router admin access confirmed (synthetic)"],
                                               "blockers": ["Clock offset still unbounded"]}, actor=FDE, source="demo")
    action = store.append(cid, "action", {"decision_id": decision["id"], "action_type": "network_change", "description": "Synthetic: cell pinning applied on the vehicle router",
                                          "status": "simulated", "performed_at": "2026-03-04T11:00:00Z", "performer": "Synthetic network engineer"}, actor=FDE, source="demo")
    outcome = store.append(cid, "outcome", {"action_id": action["id"], "result": "recovered", "observed_from": "2026-03-04T11:00:00Z", "observed_to": "2026-03-04T13:00:00Z",
                                            "verification": "unverified", "observations": "Synthetic: operator reports normal response for two hours"}, actor=FDE, source="demo")
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
