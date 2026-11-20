# Discovery reliability follow-up — 2026-10-24

Continues Claude's `ca5ef45` implementation. This change strengthens evidence discovery and signal-chain assessment;
it does not add next-check selection, redesign the workspace, or deploy the application.

## Changes

- **Missing input is not steady input.** Numeric probes now require baseline samples, observations on both sides of the
  onset, coverage of the local window edges, and no local gap larger than three typical sample intervals. Unreadable or
  insufficiently observed fields are returned under `unobserved`. Operator coverage failures produce an
  `operator_unobserved` fact and a `cannot_tell` assessment, rather than evidence against operator involvement.
  Properly sampled steady inputs and actual departures still produce their respective facts.
- **Discovery belongs to its investigation run.** `CaseStore.discovery` defaults to the latest run and selects only a
  discovery record with that run ID. Historical records remain in the append-only ledger and can be retrieved with an
  explicit `run_id`. A rerun without fresh discovery falls back to the available investigation data; it does not silently
  inherit the preceding run's source inventory or incident facts. Automatic rediscovery on every rerun is not added here.
- **Sensor comparisons need compatible observations.** Comparisons reject missing or mixed units, differing units,
  nonfinite values, and comparison scopes without sufficient incident-window coverage. No unit conversion is guessed.
  When no eligible comparison remains, sensor error is `cannot_tell`.

Versions: `evidence-discovery-v1.1`, `signal-chain-v1.3`. Numeric coverage requirements are included in the discovery
record's `rules`; previous ledger entries are not rewritten.

## Validation

Twelve added regression cases cover absent input, input ending before the incident, local dropouts, short observations,
valid steady/changing input, incompatible/missing/mixed units, nonfinite readings, missing post-onset sensor data, and
run-specific discovery history. Tests use synthetic inputs and temporary databases, never the working case database.

The focused discovery, external-review, engine/ledger end-to-end, and ledger-integrity suite passed: **50 tests**.
The discovery suite alone passed **24 tests**, including the twelve new cases.

The broader backend run, excluding `test_compute.py` after its sandbox initialization failure, finished with **339 passed
and 2 failed**. Both failures are connector integration tests whose temporary HTTP servers cannot bind a local port in
this execution environment (`PermissionError: Operation not permitted`), before connector requests run. The compute
module separately fails because macOS rejects `sandbox-exec` initialization. These are incomplete environment-dependent
checks, not reported as passing tests.

The four existing PX4 #26910 problem logs were rerun through the actual product upload path
(`scripts/research/discovery_px4.py`), using temporary stores. All four completed, with 45 classified streams per log and
18 discovered incident windows in total. Results are local at `/tmp/veyra-discovery-review-px4.json`; recordings and local
case databases are not added to Git.

This is a **regression check on previously used logs**, not a fresh blind evaluation or a diagnosis accuracy score.
The historical ALFA predictions and metrics in `DISCOVERY_CAUSAL_EVAL.md` are unchanged; they have not been rerun or
claimed for these versions.

## Remaining boundaries

- Distinct sensor scopes and matching units do not establish statistical independence. Shared estimator inputs and
  common-mode failures still require machine-context provenance.
- Recorded order alone does not establish causation. The existing chain, grouping lag and timing allowance remain
  assumptions to validate against the machine's feedback paths and clock uncertainty.
- A first parameter report is not proof that the parameter changed then; a rare controller message near an onset is
  contextual evidence, not confirmation of the mechanism. The existing controller-support rule still needs this
  distinction strengthened before broader causal claims.
- No new sealed ALFA or customer dataset has been evaluated in this change. Fresh holdout data and a frozen protocol are
  needed before making new generalization claims.
- In this Codex execution environment, macOS `sandbox-exec` cannot initialize and socket-binding integration tests
  cannot open local listeners. These restrictions are reported as test limitations; protections are not disabled to
  obtain a green test run.
