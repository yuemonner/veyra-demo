# Unfamiliar-log end-to-end acceptance — 2026-10-24

**Verdict: the full autonomous-investigation chain did not pass.** The frozen product imported and classified a new
recording, but did not discover an incident window or produce a usable next-check plan. The separately attempted real
LLM Agent was blocked by its configured Claude Code authentication. No diagnosis rules, thresholds, mappings, product
prompts, or machine profiles were added for the recording. No deployment was performed.

## Input and frozen conditions

- Public incident: [PX4/PX4-Autopilot #25892](https://github.com/PX4/PX4-Autopilot/issues/25892).
- [Original ULog](https://logs.px4.io/download?log=2e6046ed-5282-48cd-831e-4978e197b453), 2,361,924 bytes;
  SHA-256 `a16f16ca4a6efb38863b02adcefa237d91b97d59fbab996d61f0173b82397799`.
- No identifier match in existing product code, scripts, documentation or fixtures; not one of the previous ALFA or
  PX4 #26910 runs. This establishes no known local adaptation, not an assertion about all historical model training data.
- Frozen base commit: `ca5ef453e2f42ac60d6b375898c48a27be100d6e`, plus the uncommitted reliability fixes already present.
  A manifest hashed 94 product Python/profile files before source selection and verified them again after execution.
  Protocol SHA-256: `d4486d4ac6e847bd8de3fd737a4cf98f45cba16f224981e1a8867b175e42ff41`.
- Fresh local database with no seeded cases, historical answers, declared normal run or known-good baseline. No supplied
  onset, symptom window, field mapping, root-cause hint or machine class. The existing PX4 adapter selected the generic
  machine profile. Human setup consisted of fetching the public bytes, invoking the existing upload entry point and
  recording authorization for public-data model use. This was a harness-invoked product test, not automatic data fetching.
- Issue titles were visible to the evaluator during source selection; no issue narrative, comments or diagnosis were
  given to Veyra. The selected issue's initial narrative was inspected only after the outputs were sealed. This is a
  single-case frozen capability check, not a statistically independent blind diagnosis benchmark.

## Requested chain versus observed execution

The product's actual order was fingerprint → fixed PX4 extraction → import → investigation → discovery inventory →
questions → autopilot → Agent attempt. Inventory is currently after the first investigation, rather than an
inventory-led investigation loop.

| Layer | Actual result | Assessment |
|---|---|---|
| Ingest | Accepted the original ULog through `debrief_recording`, with an empty user mapping. Stored one import with 3,786 canonical rows and intact source/run/ledger integrity. | Pass for this supported format. |
| Inventory | 108 entries: 53 assigned standard roles, 55 explicitly not understood, no label streams in this input. | Pass for topic inventory; does not establish field-level understanding. |
| Interpret | Existing fixed adapter emitted eight canonical signal types: attitude, attitude setpoint, yaw rate, altitude, ground speed, actuator output, collective thrust and controller events. | Partial. Not arbitrary field interpretation. |
| Discover | Discovery ledger record was written, but `incidents=[]`; no incident probes or per-incident source reads ran. | Failed to autonomously identify an analysis window. |
| Investigate | Run and domain board were produced; all four hypotheses stayed open, root cause unconfirmed. Reference-dependent analyses were unavailable. Signal-chain assessment: `applicable=false`, “No anomalies in this investigation run.” | Partial execution, no incident-specific mechanism separation. |
| Next Check | Ranked `CHK-SOURCE-ROWS` first as `ready_automatic`; actual autopilot execution raised `ValueError: No symptom window recorded`. | Failed: no valid executable next check or recovery request for missing scope. |
| Real Agent | Existing `claude-code:sonnet` backend was invoked, without a scripted substitute. First model turn failed: `Not logged in · Please run /login`. Zero Agent tool calls completed. User elected to finish with this marked blocked. | Environment-blocked; autonomous model reasoning not assessed. |

## What actually ran, and what did not

Eight observed top-level calls, with full outputs or errors and SHA-256-linked artifacts:

1. `ulog.extract` — succeeded; used the existing standard mapping.
2. `CaseStore.commit_import` — succeeded.
3. `CaseStore.investigate` — succeeded as a function call; returned 22 internal trace entries.
4. `discovery.discover` — returned a discovery record with zero incidents.
5. `discovery.inventory` — succeeded inside discovery.
6. `CaseStore.questions` — returned no applicable incident questions or causal assessment.
7. `autopilot.advance` — failed at the automatically selected source-window check.
8. `ClaudeCodeModel.create` — failed at authentication, before an Agent tool request.

The internal trace labels all 22 invoked steps `succeeded`. Their actual outputs are more limited:

- Reference comparison: unavailable; both reference and symptom window are needed.
- Actuator-output health: unavailable; requires a reference window.
- Tracking: outer status `computed`, but roll, pitch and yaw each report `no_baseline`; no tracking findings.
- Command-chain latency: unavailable; no command-stage records.
- Input–response baseline: not applicable; required command/response pairs are absent from canonical evidence.
- Per-incident discovery probes, new Python analysis, Agent hypotheses, manual/physical checks, intervention and outcome
  verification: not executed. No failed check was recorded as successfully executed.

### Audit mismatch: “read” does not mean read

`discovery.inventory.read_by_probes` lists **27 streams**, and the questions view exposes that count. The observer saw
**zero `probe` calls and zero source-read iterators used by incident probes**, because no incident was selected. The
ULog reader did scan the file for inventory/canonical extraction; this is not the same as running the claimed incident
probes. The current field is populated from eligible role names, not successful probe receipts. Treating it as evidence
read would overstate the work performed.

### Next-check rationale versus prerequisites

The first check's stated trigger was that records exist inside the symptom window. Its ranking explanation cites
objective match, availability, four open domains and no disruption. But `run.symptom_window` is null. The system stored
an automatic `check_review` while preparing the check, then execution failed; there is no successful `check_execution`.
This is a prerequisite-validation failure, not evidence that the requested source-window analysis ran.

### Hypotheses actually formed

Only the generic Network / Control software / Controls & actuation / Field service domain board was produced, all open.
Control software was marked `anomaly_observed` from 17 controller events. These include both warning/failsafe messages
and ordinary logging/takeoff notices; event presence is not a separated causal explanation. No incident-specific
controller-versus-actuator-versus-sensor mechanism assessment or cited Agent report was formed.

## Post-seal comparison with the reported incident

The issue author describes an ascent command whose measured vertical velocity did not track the desired velocity and
asks whether the vertical-control integral explains it. That is a report and hypothesis, not a verified root cause.

After sealing, evaluator-only schema inspection found `vehicle_local_position.vz`, `trajectory_setpoint.velocity`,
`vehicle_local_position_setpoint.vz` and thrust fields in the original recording. Veyra's canonical adapter did not expose
the desired/measured vertical-velocity pair, so the autonomous run did not compare the two signals central to the report.
No new mapper or analysis was added. The post-seal schema audit is separate from the autonomous evidence receipts and
is not credited as a product discovery.

## Crazyflie case suitability

The [Bitcraze thread](https://forum.bitcraze.io/viewtopic.php?t=2535) and its
[second page](https://forum.bitcraze.io/viewtopic.php?t=2535&start=10) describe watchdog resets, stack-sizing experiments
and later debugger observations around a UART/DMA wait. The final posts still ask why transfer cleanup did not occur.
I found prose, code references and short console excerpts in the thread, not a complete downloadable timestamped
telemetry recording. It is useful as a source-linked engineering-conversation case with unresolved causes, but does
not replace the original-log acceptance check. It was not imported as invented telemetry or credited as a diagnosis pass.

## What should change after this frozen assessment

1. Add a general missing-scope path: discover and label candidate incident windows from existing events/signals, or ask
   for the missing window explicitly. Never mark a window-dependent check ready with no window.
2. Separate eligible streams, parsed streams, executed probes, sufficient evidence and skipped analyses in receipts.
   Function return success must not be presented as completed diagnostic analysis.
3. Make supported signal roles available for actual field-level querying and investigation, including cases without a
   predeclared normal baseline. Expose discovery and signal-chain evidence to the Agent; its current tool list has no
   direct discovery/causal-results tool.
4. Restore the configured model login, then assess real Agent tool use on a fresh holdout. This recording is now an
   exposed acceptance case; a later improved run on it is a regression check, not a new blind result.

These are findings and proposed general fixes. Product code remained frozen for this assessment.

## Artifacts and reproducibility

Local artifact directory:
local outputs directory `autonomous-acceptance-2026-10-24/` (not in the repository)

- `protocol.json`, `selection.json`, `run_acceptance.py`: predeclared conditions, source and observer.
- `tool_calls.json`, `call-0001.json` … `call-0008.json`: invocation inputs, complete outputs/errors, durations and hashes.
- `upload.json`, `imports.json`, `run.json`, `discovery.json`, `questions.json`: actual product outputs.
- `ledger.json`, `integrity.json`: preserved automatic records and verification results.
- `summary.json`, `seal.json`: status and sealed result hashes, recorded before reference review.
- `post_seal_schema_audit.json`: evaluator-only schema inspection, not autonomous findings.
- `observer-attempt1/`: first observer attempt, which stopped because its serializer used the wrong signature for
  `ImportProfile.to_dict`. Only observer serialization was corrected; this is not attributed to the product. The second
  attempt used a fresh store. No failed attempt was hidden and no model answer was substituted.

The artifact directory contains public-source data only; no working customer databases or model credentials are
included. The raw recording and temporary case databases are kept locally, outside the repository.
