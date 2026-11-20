# Autonomous Investigation Entry v0

Review date: 2026-10-25. Local implementation and regression acceptance; no push or deployment.

## Scope and architecture

The existing Field Case, read-only tools, human approvals and decision/outcome records remain in place. This change adds
an honest investigation entry point and an optional OpenAI model adapter. It adds no UI redesign, model training,
Operational Learning, neuroscience methods or case-memory features.

`engine/entry.py` reuses canonical evidence, the existing measurement screening limits and heartbeat-gap method, and
reported alert/reset/fault semantics. Without a supplied symptom window it can return up to three candidate scopes.
Candidates carry source import/row references, input hashes, units, clock domain, coverage requirements, method and
limitations. Later candidates beyond the execution budget are explicitly deferred. Each returned candidate gets a real
investigation: the primary is the parent run; other candidate runs are embedded with their own IDs, findings and execution
receipts. Human case timestamps are not overwritten. Only the primary run's checks are offered by the current workflow.

No PX4 issue ID, topic name, fault label or case timestamp is in the entry algorithm. No new change-point detector is
claimed. A single threshold outlier cannot define a candidate. Informational logging notices are not fault evidence.
Candidates require valid canonical measurement units and at least three distinct surrounding measurement times on each
side in the same clock domain; this is limited coverage validation, not proof of continuous coverage or sensor health.
Other clocks are not silently aligned. Context remains unverified until confirmed by engineering evidence.

A candidate is an exploratory scope, not an incident declaration or causal conclusion. An earlier segment is never
automatically declared healthy because a candidate was found. With no credible candidate, `entry.status` and
`diagnostic_execution.status` are `insufficient_evidence`; the next-action workflow asks for an approximate affected time
range or timestamped observable symptom and its source. An optional request for valid local measurements specifies what
is needed if the recording does not cover that time.

## Execution integrity and check eligibility

- `available_streams`, `metadata_inspected` and `eligible_for_probes` are separate from `read_by_probes`.
- Actual probe receipts record incident, requested streams/windows, samples read, completion/skip/failure and reason.
  Directly decoded ULog text/parameter arrays count only when their probe runs. Inventory alone yields no probe reads.
- Discovery records carry run ID, run hash, source hash and input hashes. Existing run isolation continues to apply.
- Each diagnostic calculation has an execution receipt with current run ID, input hashes and result digest. A successful
  function return can still be a skipped diagnostic (`no_baseline`, missing command stages, insufficient records, etc.).
  No completed diagnostic means `insufficient_evidence`; mixed successes/failures are `partial`. None means root cause
  confirmed.
- Window-dependent checks cannot be ready without a credible scope; source-window inspection additionally requires
  records in that window. Execution revalidates scope for stored/legacy readiness too. Authorization and site approval
  are still prerequisites, not automatically granted capabilities.
- The Agent has a new read-only `investigation_entry` tool exposing candidate scope, execution receipts, discovery and
  remaining unknowns. Its recorded tool calls now preserve full outputs as well as previews and digests.

## OpenAI integration

`OpenAIModel` implements the same provider interface as the existing Claude backends, using the
[Responses API function-calling loop](https://developers.openai.com/api/docs/guides/function-calling). Original output
items, including reasoning items, are replayed alongside `function_call_output` receipts between turns. The adapter
requests `store=false`; it does not replace provider data policies. Existing authorization, tool allowlists, citation
validation and unconfirmed-root-cause boundaries remain in force. Responses that are incomplete or carry malformed
function arguments fail closed. No scripted fallback is used.

Server environment configuration (never expose these as frontend variables):

```dotenv
VEYRA_AGENT_BACKEND=openai
VEYRA_OPENAI_MODEL=<an available Responses tool-calling model ID>
OPENAI_API_KEY=<configure privately on the server>
```

No model ID is silently chosen. Load these variables into the backend process's environment and restart it. A real case
also needs a recorded model authorization for provider `openai`; an Anthropic authorization does not authorize OpenAI.
The authorization record contains scope and permission evidence, never the API key.

**Real OpenAI Agent acceptance: Blocked.** The user elected to finish without supplying credentials/model configuration.
Provider protocol tests use mocked HTTP and are not autonomous reasoning validation. No Claude login or scripted model
was substituted.

## Synthetic validation before PX4 rerun

Seventeen synthetic entry tests passed before the real-log regression: credible and noncredible missing-window cases,
multiple candidates, ordinary notices, single outliers, invalid/missing samples, incompatible units, different clocks,
metadata-only probes, failed and completed probes, failed diagnostics, absent check prerequisites, current-run receipt
isolation, and source integrity. Five OpenAI adapter tests cover multi-turn tool/reasoning replay, missing configuration,
separate data authorization, incomplete responses and malformed arguments. These are deterministic transport tests.
The focused discovery, engine/ledger, next-action, autopilot, HTTP API and external-review run passed 101 tests; the final
entry suite passed all 17 cases. The broader backend run excluding the environment-dependent `test_compute.py` module
finished with **363 passed**. Local HTTP/S3 integration tests ran with loopback network permission and passed. No failing
test was rewritten to force a positive PX4 diagnosis.

The compute module was attempted separately and stopped at its first test because the nested macOS `sandbox-exec`
process cannot initialize in this execution environment (`sandbox_apply: Operation not permitted`). That capability is
**not validated here**; its isolation was not disabled to get passing tests. No Agent Python computation was claimed.

## PX4 #25892 regression result

This is the previously examined [public incident](https://github.com/PX4/PX4-Autopilot/issues/25892), **not a fresh blind
benchmark**. The original failed acceptance database, reports, tool calls and seal remain intact. Twenty sealed baseline
artifact hashes were verified before the rerun. The same original ULog was used, with no supplied incident time, baseline,
diagnosis, manual mapping or machine class. Ninety-five product files were frozen for the rerun and verified afterwards.

Original source SHA-256:
`a16f16ca4a6efb38863b02adcefa237d91b97d59fbab996d61f0173b82397799`.

| Item | Original failed acceptance | Entry v0 regression |
|---|---|---|
| Candidate window | None | One exploratory window, `2025-11-02 02:23:35.007858–02:23:46.057792 UTC` |
| Supporting evidence | No incident selected | Eight source-linked warning records: attitude failure, failsafe, kill switch, termination and yaw-estimate warning; may be reactions |
| Actual discovery probes | 0; 27 streams misleadingly listed as read | 2 completed probes; 25 streams actually read; 27 eligible |
| Canonical records | 3,786 | 3,786; same input mapping |
| Diagnostic calculations | Unscoped; success labels obscured missing prerequisites | Event-window inspection and brief-event scan completed; seven other diagnostics explicitly skipped |
| Source-window check | Raised `No symptom window recorded` | Executed read-only; 1,824 in-window rows counted, digest and receipt stored |
| Follow-on check | Workflow crashed | Drive-log retrieval drafted; `needs_authorization`, awaiting approval; nothing sent or executed on equipment |
| Unknown streams | 55 | 55, still explicit |
| Root cause | Unconfirmed | Unconfirmed; all four fault-domain hypotheses remain open |
| Real Agent | Claude login blocked | OpenAI credentials/model absent; Blocked, zero model calls |

The two completed **diagnostics** are not the same as the two completed **discovery probes**. Receipts preserve both
levels. Missing reference and command/response data prevent the baseline and latency analyses. No healthy baseline was
manufactured. Root cause, actuator hardware failure, sensor fault, and which event initiated the failure remain unsupported.
The late alert-based scope is useful for investigation but is not validated as the fault's true onset.

Examples of still-uninterpreted topics: `action_request`, `airspeed_wind`, `control_allocator_status`,
`estimator_attitude`, `estimator_baro_bias`, including multi-instance streams. The existing adapter still does not
investigate every original field. In particular this change does not establish the reported vertical-control mechanism.

Integrity checks passed: imports intact, run hashes intact, valid append-only ledger chain. The observer recorded 57
top-level/nested tool calls, with outputs/errors and digests. The final state is a draft awaiting human approval, not a
completed diagnosis, dispatch, intervention or outcome verification.

## Reproduce and inspect

```bash
PYTHONPATH=backend python scripts/validation/autonomous_entry_acceptance.py recording.ulg new-output-directory
```

The command refuses to overwrite an existing output directory. It creates a fresh store, fingerprints source and product
code, runs existing upload/discovery/investigation/next-action entry points, exports receipts and seals outputs. It has no
incident-time or normal-baseline argument. `--agent --allow-public-model` is a separate explicit authorization to use a
configured model on that public recording; without configuration it records Blocked.

Detailed observed regression artifacts are local under:
local outputs directory `autonomous-entry-v0-px4-regression-2026-10-24/` (not in the repository).
The earlier failure is under `outputs/autonomous-acceptance-2026-10-24/`. Neither the raw recordings nor case databases
are added to Git. The standalone CLI was also smoke-tested against the same original log.

This verifies a trustworthy bounded entry path on synthetic cases and one exposed regression case. It does not establish
general-purpose discovery accuracy, autonomous Agent investigation, causal identification or cost-optimal decision making.
