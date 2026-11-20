# End-to-end acceptance v1 (written before the run)

Purpose: show that one upload goes raw recording -> automatic scan -> debrief -> Veyra's MAIN investigator (evidence
sheet + physical layer, real model) -> reusable scenario (two engineers) -> engineer-verified outcome -> reused in a later
investigation, and that a drone log never gets battery-electric-vehicle (HV / BMS / contactor) output.

Data: SYNTHETIC tables from `scripts/make_synthetic_bev_tables.py` (R1 correction seed 1, R2 healthy seed 2, R3
correction seed 3) and one public PX4 ULog from UAV-SEAD (R4, `2021-10-06--_11_54_32.ulg`). No customer data.
Model: Claude Code CLI (`VEYRA_AGENT_BACKEND=claude-code`, alias `sonnet`); model id recorded per run. The engineer steps
(scenario author A, reviewer B, performed check, observation, verification) are scripted engineer actions, labelled.

Sequence: R1 upload -> scan (no scenario yet) -> debrief -> investigation. Engineer A creates a scenario from R1's
Check A finding, Engineer B approves (back-fill on stored recordings). Engineers open R1 in the outcome ledger, record
DOC-BMS-HISTORY performed, an observation supporting A, verify A. R2 upload -> scan -> debrief -> investigation. R3 upload
-> scan -> debrief -> investigation. R4 upload -> scan -> debrief -> investigation (public data: agent allowed).

Pass criteria (each reported, pass or fail):
- C1 each upload is read by the expected profile (R1-R3 `synthetic-bev-tables`, R4 `px4-battery-status`).
- C2 R1 and R3: investigator = main, mode = agent, Check A VIOLATION, final report has no rejected structured claim,
  report status is not `no_failure_found`.
- C3 R2: the approved scenario's result on R2 is PASS (back-fill or on arrival); R3: the scenario runs on arrival and
  returns VIOLATION.
- C4 R3: the arrival investigation lists R1's verified A as earlier knowledge with applicability `applies`, the
  deterministic next check is DOC-BMS-HISTORY, and A is not confirmed in R3.
- C5 R4: family_scope.applies is false, Check A is not VIOLATION, the final headline and explanation contain no HV-vehicle
  term, no rejected claim remains in the final report.
- C6 every agent run: mode agent, at least one tool call, report accepted by the verifier (no dropped quotes left).
- Also recorded, no threshold: steps, model calls, seconds, verifier fix rounds, tokens.

Outputs: `results/*.json` (one per investigation, as stored) and `RESULTS.md`. Nothing is edited after the run; a rerun
is a new numbered run.

## Runs (appended after each run; nothing above changes)

* Run 1 (2026-11-16): C1-C5 PASS, C6 FAIL. R4's agent did not run: the harness set the physical-check permission
  (VEYRA_PRIVATE_ALLOW_AGENT) but not the engine case's own `model_authorization` record, which the main investigator also
  requires for non-synthetic data. That is the two-layer permission working; the harness was incomplete. Also: the summary
  read the model id from the wrong key ("none"). C5 passed without an agent report on R4 (vacuous for the text part).
  Outputs kept unchanged in `results-run1/` and `RESULTS-run1.md`.
* Run 2: the harness records the public-data permission on R4's engine case and reads `model_id`. No product change.
