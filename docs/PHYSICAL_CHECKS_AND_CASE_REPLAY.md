# Physical checks, hypotheses, next decisive check, scenarios, outcomes and guided replay

Engineering workflow for battery-electric machines (first family: an HV loss caused by under-voltage protection):

```
decoded recording + signal profile
  -> Physical Check Engine (deterministic, versioned, evidence rows)
  -> case facts -> hypothesis assessment (roles + ordinal statuses)
  -> next decisive check (discrimination + safety gate)
  -> scenario registry (finding -> reviewed reusable check -> other recordings) + cross-run scanner
  -> verified outcome chain (engineer verification only) -> retrieval for future cases
  -> guided replay (5 sections, every claim with its basis, full trace)
```

Code: `backend/app/physchecks/`, API `backend/app/physchecks_api.py`, page `frontend/app/case-replay/`.

## Run it

Tests (SYNTHETIC fixtures only; never read private data):

```sh
PYTHONPATH=backend <python> -m pytest backend/tests/test_physchecks*.py -q
```

Local guided replay (loopback only):

```sh
VEYRA_PYTHON=<python with backend requirements> ./scripts/case_replay_local.sh
# UI  http://127.0.0.1:3058/case-replay   (?case=<id>&step=facts|contradiction|causes|next|scenario|history|trace)
# API http://127.0.0.1:8058/engineering/*  (404 unless VEYRA_ENGINEERING_LOCAL=true and the client is loopback)
```

The synthetic demo case (`SYNTHETIC-UV-INCIDENT`) is always available. Private cases are listed from
`VEYRA_PRIVATE_CASES_DIR` (default `~/Veyra-private`). The engineering ledger (scenarios, outcomes) defaults to
`~/Veyra-private/engineering.db` (`VEYRA_ENGINEERING_DB`). Neither is inside the repository.

## Adding a private case (stays outside the repository)

```
~/Veyra-private/<case>/
  case.json        schema veyra-case-dir-v1: case_id, title, synthetic:false, confidential:true, data_dir, manifest,
                   profile, windows {window: [decoded CSVs]}, check_parameters, historical_reports, historical_claims
  profile.json     schema veyra-signal-profile-v1: roles -> field, unit, sign, valid range, placeholders, map/enum,
                   resolution, max_age_s, kind, same_source_as
  raw/...          decoded per-frame CSVs (+ manifest with SHA-256; every file used is verified before reading)
```

Do not list convenience subsets that repeat frames (they would double count). `historical_claims` lets the replay
reconcile an earlier report's numbers against this run (`compare` paths such as `fact:cells_at_trip.value.min_v`,
`check:restart_eligibility.windows.reclosures.items.#len`, `...findings.kind=<kind>.values.x`).

## Check interface (`base.py`)

Each check has a spec (inputs with units and sign conventions, parameters with tolerance and source rule, applicability,
alignment rules, limitations) and returns PASS / VIOLATION / INDETERMINATE / NOT_APPLICABLE with findings (evidence
rows), calculations (formula, inputs, value), reasons, uncertainty, data-quality counts, assumptions and an input
digest. Same inputs, same result.

| Check | Decides | Never does |
|---|---|---|
| `charge_accounting` (A) | whether SOC changes are explained by integrated measured current at the stated capacity; abrupt corrections (bounded by the last sample at the old level and the first at the new), tracking spans with a quantisation-aware ratio, polarity | integrate across gaps; pair stale samples; call a correction a legitimate recalibration without a **verified** marker |
| `protection_sequence` (B) | observed escalation and what followed (facts); conformance to a spec | judge a sequence without a **verified** spec (draft spec -> INDETERMINATE); treat order as causation |
| `restart_eligibility` (C) | protective shutdowns, resets (observed or not), reclosures, review flags (repeated trips, discharge below the original trip level) | call a reclosure a safety violation without the customer's **verified** restart policy |

## Hypotheses, planner, agent

* `facts.py` keeps the abrupt SOC correction and the cross-window energy shortfall as different quantities.
* `hypotheses.py`: roles observed_symptom / protective_response / upstream_cause / contributing_factor; statuses
  observed / open / insufficient_alone / weakened / excluded / confirmed. Only a verified outcome confirms or excludes.
  A weak cell is `insufficient_alone` when the highest cell fell by more than the cell spread (pack-wide depletion).
  `constrain_claims` downgrades unverified root causes, rejects unsupported exclusions, a protective response or a weak
  cell named as the cause, claims contradicting Check A, and flags numeric probabilities.
* `planner.py`: templates state each hypothesis' predicted observation; ranking = pairs separated > effort > no HV > id.
  Qualification is a hard gate (HV work is hand-off only for non-qualified requesters); safety holds come first; it
  states when recorded telemetry cannot separate the open causes.
* `agent_bridge.py`: model tools over the deterministic results; claims constrained; refuses confidential cases unless
  the operator set `VEYRA_PRIVATE_ALLOW_AGENT=true` after the data owner agreed; `unavailable` when no model is configured.

## Scenarios and outcomes

* Scenario = versioned check configuration created from a deterministic finding; approval by a second engineer
  (agents cannot approve); revision -> re-review; runs say NOT_APPLICABLE / INDETERMINATE with the missing roles or
  mismatched configuration. `scenarios.scan` runs approved scenarios over recordings and stores results.
* Outcome chain: hypothesis -> review -> recommended -> performed -> observation -> verification -> closure, with
  corrections that supersede and keep history. Conflicting observations block confirmation until corrected.
  `outcomes.knowledge` returns verified outcomes as trusted, others as unverified context.

## Recordings: ingestion -> checks -> automatic scenario scans

Recordings uploaded through Veyra's existing recording ingestion (`POST /engine/recordings`: ROS 1 bag, ROS 2 bag as
.zip, MCAP, PX4 ULog) are read by the Physical Check Engine where the upload stored them, with Veyra's own readers
(`ingest.bags`, `ingest.ulog`), at full rate (`physchecks/ingested.py`). A signal profile with a `recording` block binds
topics by name or message type to roles; a profile is compatible only if every binding resolves to exactly one topic.

| Built-in profile | Reads | Machine class |
|---|---|---|
| `ros-battery-state` | any ROS 1/2 or MCAP recording with one `sensor_msgs/BatteryState` topic | not stated (scenarios that need one do not apply) |
| `px4-battery-status` | PX4 ULog `battery_status`; capacity from the logged `BAT_CAPACITY` (configured, not measured) | `px4_vehicle` |
| `synthetic-bev-ros` | only `/synthetic_bev/*` topics from `scripts/make_synthetic_bev_recording.py`; everything it reads is SYNTHETIC | `battery_electric_vehicle` |

Private profiles (a customer's topics, machine class, conditions) go in `<private root>/profiles/*.json` and are tried first.

* On upload (engineering workflow enabled) every approved scenario runs on the new recording; the result, or why nothing
  ran (no compatible profile, no approved scenario, read failure), is stored in the ledger (`ingest_scan`, `scan_result`).
* Approving a scenario back-fills it over the recordings already stored.
* The replay page has a **Recordings & automatic scans** view: upload, the profile chosen (and why others were not),
  the automatic scan, every scenario result with its trigger (on arrival / when approved / manual), and "Open
  investigation", which runs the full replay on that recording.

## Investigation on arrival (`physchecks/investigation.py`)

After the automatic scan, each upload a profile can read is investigated and the result is stored in the ledger
(`investigation`, subject `investigation:<id>`):

1. the deterministic checks, facts and hypothesis assessment (with this case's own verified outcomes);
2. earlier verified knowledge and scenario results on this and the other stored recordings (cross-run);
3. the AI investigator (`agent_bridge`), with tools for all of the above plus bounded raw samples. It runs only on
   SYNTHETIC data, or on real data after the data owner agreed and the operator set `VEYRA_PRIVATE_ALLOW_AGENT=true`
   (enforced in `investigation.run` whatever model is configured). Without a model the record says "unavailable";
4. every agent statement is held to the checks (`hypotheses.constrain_claims`: accepted / downgraded / flagged /
   rejected, with reasons). Negations ("not the origin", "no root cause is confirmed") and reports of another case are
   not read as causal claims.

The page shows it in step 3 (tools called, summary, each statement with its verdict) and in the Recordings view. An
engineer can **adopt** a non-rejected statement: it enters the outcome ledger as a hypothesis proposal with origin
`agent` (the case is opened on the way). It becomes a cause only through the engineer verification that already exists;
that verification is what later investigations retrieve. `POST /engineering/cases/{id}/investigate` re-runs it (the
"Run the investigation" / "Investigate" buttons). The local launch script uses the Claude Code CLI as the model
(`VEYRA_AGENT_BACKEND=claude-code`); `VEYRA_AUTO_INVESTIGATE=false` turns the automatic investigation off.

## Verified outcome -> future investigation (`physchecks/knowledge.py`)

When a case is opened in the outcome ledger it records its signature and context (machine class, profile, check
versions, synthetic). In any later investigation, an engineer-verified result from another case **applies** when it
shares a finding kind, on the same machine class, at the same check versions; **partly applies** when something is
unknown or changed, or this recording's evidence weakens it here; **does not apply** for another machine class, or when
the earlier case is SYNTHETIC and this recording is not. The replay shows it next to the candidate causes, and the
planner puts the check that settled it first (saying so). It never changes a status: a cause verified elsewhere stays
open here until verified here. Unverified hypotheses from earlier cases are listed as context only.

## Demonstration with SYNTHETIC recordings

```sh
python scripts/make_synthetic_bev_recording.py --variant healthy    --format ros2 --out /tmp/SYNTHETIC-bev-healthy.zip
python scripts/make_synthetic_bev_recording.py --variant correction --format ros1 --out /tmp/SYNTHETIC-bev-correction.bag
```

1. Recordings view: upload the healthy one (no approved scenario yet: recorded as such).
2. SYNTHETIC-UV-INCIDENT, step 5: create the scenario (engineer A), approve it (engineer B); the stored recording is
   back-filled (PASS).
3. Same step: open the case in the outcome ledger, record a performed check (DOC-BMS-HISTORY), an observation supporting
   A, and verify A.
4. Recordings view: upload the correction one; the scenario runs on arrival (VIOLATION, matches the original finding).
5. Open its investigation: step 3 shows the verified result from SYNTHETIC-UV-INCIDENT as applying, with reasons; step 4
   ranks DOC-BMS-HISTORY first because it settled that result. A stays open in the new case.

## Engineer workbench (step 5 of the replay)

The replay page writes to the local ledger through the same API, naming the engineer typed at the top of step 5
(`x-veyra-actor`; a local name, not authentication). From the page an engineer can: create a scenario from the
finding; review it as a second engineer (the author's own review is refused; an approval needs notes); run approved
scenarios on every available recording (stored scan results); open the case in the outcome ledger and track the
candidate causes; record hypothesis reviews, performed checks, observations, verifications, corrections (withdraw or
replace, the old record kept) and closure. Refusals from the rules (two-person review, conflicting observations,
verification without an observation) are shown as they come from the API.

Each SOC correction carries its context (`facts.correction_context`): SOC sample spacing and segment inside it, the
age of the paired current sample, the fault escalation just before its first changed sample and the step cadence.
That answers "is this a sampling, timing or segment artifact?" from the rows; the timing is context, not cause.

## Not done / known limits

* No customer protection spec or restart policy has been provided, so Checks B and C stay INDETERMINATE on real data.
* No live AI-investigator run on confidential data (owner permission not recorded); the bridge is tested with a
  scripted model only.
* Phone width (390 px) checked by headless-Chrome screenshots of every section: no horizontal page scroll; the step rail scrolls sideways.
* The only recordings compatible with a CASE A scenario are SYNTHETIC vehicles and the origin case itself; no second real battery-electric recording is available locally.
* Recordings are read from the local engine store's recordings folder; a multi-workspace private instance keeps a store per workspace, which this local workflow does not address.
* The automatic scan runs only where the local engineering workflow is enabled (VEYRA_ENGINEERING_LOCAL=true, loopback).
