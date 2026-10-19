# Veyra FDE guide — Investigation Engine v2

For forward deployed engineers running investigations on site or remotely. Everything runs locally with
Python 3.9+ and **no other dependencies**. Data stays in one workspace file; nothing is sent anywhere and no
model provider is called.

```sh
cd veyra-v0-demo
python3 scripts/veyra.py --help                    # all commands
python3 scripts/veyra.py demo                     # full loop on SYNTHETIC data, writes an HTML report
export VEYRA_WORKSPACE=~/veyra-cases/builderx      # optional; default ./veyra-workspace
```

## 1. What the engine does (and does not)

| It does | It does not |
| --- | --- |
| Validates exports row by row against an explicit import profile | Guess what a column or signal means |
| Keeps original files byte-for-byte with SHA-256, re-verified before every run | Edit or delete anything (the database refuses UPDATE/DELETE) |
| Resolves machine context as of the incident, using only what was known then | Treat reported context as confirmed |
| Computes screening crossings, heartbeat gaps, events, command-chain stage latency and (when data allows) an input–response baseline | Name a root cause, give probabilities or estimate savings |
| Marks fault domains as delay observed / anomaly observed / no delay in measured part / not observed | Close a domain it has no measurements for |
| Ranks next checks with written ordinal rules and shows why | Authorize dispatch, actuation or any edited procedure |
| Records reviews, executions, decisions, actions, outcomes, reviews and recurrence in a hash-chained ledger | Treat a decision as an action, or an action as a verified outcome |

## 2. Onboarding a new customer export (once per format)

1. Get a small authorized sample and its field dictionary.
2. Write an import profile (JSON). Start from `fixtures/synthetic/remote_delay/synthetic_*_profile.json`.
   - `columns`: which column is timestamp, asset, signal, value, unit, scope (probe path or channel), command_id, code…
   - `signal_map`: customer signal name → canonical signal (`python3 scripts/veyra.py context schema` lists context
     attributes; canonical signals are in `backend/app/ingest/catalog.py`). Unmapped signals are kept for statistics only.
   - `clock_domain`: name the clock that produced the timestamps (console, vehicle controller, router…). One per file.
   - `max_clock_skew_s`: only if documented; otherwise leave it out (unknown).
   - `source_metadata`: OEM, model, kit, software, interface… recorded as **reported** context.
3. Dry-run it — nothing is stored:
   ```sh
   python3 scripts/validate_export.py sample.csv profile.json --asset EX1
   ```
   Fix the profile until rejected rows are explained. Exit code 0 accepted, 1 needs review, 2 rejected/unsupported.

## 3. The investigation loop

```sh
V="python3 scripts/veyra.py"
$V case create --machine EX1 --title "EX1 late boom response" --actor "Your Name" \
   --symptom-start 2026-03-04T09:06:00Z --symptom-end 2026-03-04T09:07:30Z --symptom-source "Operator call via site supervisor, ticket 123"
$V import CASE console.csv --profile console_profile.json --actor "Your Name" --preview   # check first
$V import CASE console.csv --profile console_profile.json --actor "Your Name"
$V context show EX1                                   # what is reported vs confirmed vs unknown
$V context set EX1 --key firmware_version --value 1.8 --status engineer_confirmed \
   --source "VCU about screen photo" --evidence photo-17 --effective-from 2026-03-01T00:00:00Z --actor "Your Name"
$V investigate CASE --actor "Your Name"               # immutable run; prints domains, checks, dispatch readiness
$V checks CASE                                        # full reasons, who/tools, availability
$V review CASE CHK-SOURCE-ROWS --verdict accepted --reason "coverage first" --actor "Your Name"
$V execute CASE CHK-SOURCE-ROWS --actor "Your Name"   # only accepted automatic read-only checks
$V review CASE CHK-NETWORK-LOGS --verdict edited --reason "limit load" --edited-procedure "Gateway log 09:00–09:15 only" --actor "Your Name"
$V record CASE check_execution --actor "Your Name" --source "ticket 123" --set check_id=CHK-NETWORK-LOGS \
   --set status=performed --set executed_at=2026-03-04T10:40:00Z --set performer="Network engineer" \
   --set observations="Handover storm 09:05:50–09:07:40" --set evidence_note="Gateway log attached to ticket 123"
$V record CASE decision --actor "Your Name" --source "call with customer" --add options_considered="Pin LTE cell" \
   --add options_considered="Dispatch technician" --set chosen_option="Pin LTE cell" --set rationale="Delay before vehicle receipt"
$V record CASE action --actor "Your Name" --source "ticket 123" --set decision_id=LEDGER_ID --set action_type=network_change \
   --set description="Cell pinning on vehicle router" --set status=performed --set performed_at=2026-03-04T11:00:00Z --set performer="Network engineer"
$V record CASE outcome --actor "Your Name" --source "operator call" --set action_id=LEDGER_ID --set result=recovered \
   --set observed_from=2026-03-04T11:00:00Z --set observed_to=2026-03-04T13:00:00Z --set verification=unverified --set observations="Normal response"
$V record CASE outcome_review --actor "Second Person" --source "review" --set outcome_id=LEDGER_ID --set verdict=accepted --set reason="Matches gateway log"
$V record CASE recurrence_check --actor "Your Name" --source "fleet check" --set outcome_id=LEDGER_ID \
   --set window_from=2026-03-04T13:00:00Z --set window_to=2026-03-11T13:00:00Z --set recurred=no
$V report CASE --out reports/EX1.html                 # self-contained HTML to share
$V ledger CASE --verify                               # hash chain + original files + runs
$V ledger CASE --export episodes.jsonl                # decision-time vs hindsight separated
```

Rules the tool enforces (it refuses with a reason otherwise):
- New evidence or new context makes the last run **stale**: investigate again before reviewing, executing or deciding.
- Edited procedures are manual only; automatic execution needs an accepted, unedited, available check.
- A performed check needs evidence (import IDs) or an evidence note. Actions need a decision; outcomes need a performed
  (or explicitly simulated) action, a window after the action, and evidence covering the window to be "evidence_attached".
- Outcome reviews need a different person. Synthetic data only goes into synthetic cases.

## 4. Reading the results

- **Fault domains** — `delay_observed`: measured delay in that segment for matched records. `anomaly_observed`:
  something abnormal recorded there, link to the symptom not established. `no_delay_observed_in_measured_part`: the
  measured part was normal; unmeasured parts stay possible. `not_observed`: no records. Root cause is always unconfirmed.
- **Command chain** — needs command IDs at several stages. Across two clocks, absolute latency needs a confirmed
  `clock_offset_bound_ms`; without it the engine reports only the *change* between in-window and out-of-window
  commands, stated as "assuming a stable clock offset". Verify that with CHK-CLOCK-OFFSET.
- **Input–Response Baseline** (optional) — runs only when a channel has `vehicle.command_input` and
  `vehicle.actuator_response` on one clock, with enough excitation and coverage. Comparison needs the machine context
  (OEM, model, kit, control software, firmware, interface) **confirmed** and unchanged between windows. "No change
  detected" applies to that channel only.
- **Next checks** — the "Ranking basis" column lists the exact rule values. Cost and duration are unknown unless recorded.
- **Dispatch readiness** — "ready for engineer decision" never means approved. Parts are "not determined" until a
  component fault is confirmed.

## 5. Where the data goes later

The workspace file holds the reusable assets: machine-scoped context (carries across cases), original files with
hashes, immutable runs with versions of every rule used, and the ledger. `ledger --export` produces
`veyra-episode-v1` records for evaluation; hindsight (outcomes, recurrence) is kept apart from decision-time data.
The same store is served by the web API (`/engine/*`, local runtime only) so the workspace UI can adopt it.
