# inquiry-eval-v1: preregistration (written and committed before any run)

**Question.** Can Veyra's main investigator, with the Predict → Test → Update loop (`inquiry-v1`), do on its own the kind of
causal investigation the team did by hand on 2026-11-17 (an internal post-run analysis)? The goal is not to
reproduce the CASE A answer.

**Frozen.** Tag `inquiry-eval-v1-frozen`, on the commit that adds this file. It freezes the code (`backend/`, `scripts/`)
and the scoring (`criteria.py`, `heldout.py`, `case_a.py`). Both harnesses refuse to run from any other commit or with
changes in those paths. Model: claude-sonnet-5-5 through the Claude Code CLI (`VEYRA_AGENT_BACKEND=claude-code`), the same
as v4. The deterministic layer, action gate v6.3 and the physical-action rules are unchanged by inquiry-v1.

## What the agent is given (leakage rule)

- Only Veyra's standard path: the uploaded recording, its signal profile, the system prompt, and the tools.
- It is NOT given:
  - the post-run analysis or its script (`~/Veyra-private/analysis`);
  - the sealed prediction;
  - the demo page;
  - the v4 records;
  - any team conclusion.
- L2 below checks that the system prompt and the tool descriptions contain none of the post-run phrases listed in
  `criteria.FORBIDDEN`.
- The deterministic hypothesis-B text keeps its single-anchor framing ("alone it requires usable capacity of about X Ah").
  It was **deliberately not fixed**, so that P5 measures whether the agent corrects it itself.

## Developer interventions made before the freeze, informed by CASE A (to be reported as such, never as autonomous)

1. The computation set was designed from the post-run audit's list of what was missing:
   - `topology`, `integrate`, `step_response`, `unexplained_changes`, `transitions`, `crosscheck`;
   - `capacity` with declared SOC anchors.
   All are generic (no machine constants), but their existence comes from developer work on CASE A. In particular, a
   `capacity` op that asks for an anchor makes P5 easier than a blank computation tool would.
2. The inquiry prompt says, in general terms, that a number in a deterministic statement may rest on an unstated
   assumption (an anchor or a reference point), and asks the agent to compute such a number under the alternatives.
3. Fixes made on CASE A before this freeze:
   - 905489d: three cell-physics output defects;
   - a `transitions` before/after reference fix and a clearer `topology` refusal. These came from smoke checks on the
     generator and on CASE A; the CASE A smoke check printed whether each op ran, never a value.

**Autonomous** means: which hypotheses to test, which computations, windows, times and arguments to use, what each
hypothesis predicts, how to read the results, the ranking, and what the report says.

## CASE A (development regression, NOT a blind validation): 3 runs

`case_a.py --run 1..3`, customer permission as recorded for v4 plus the founder's instruction of 2026-11-17. A criterion
passes only if it passes in all three runs; the per-run table is reported either way.

- **Regression** (unchanged from v4): D1–D5, K1–K7, S1, S2, S5.
- **L1 loop.** At least one counted, discriminating prediction test. The ledger's hash chain verifies, and every test
  comes after its prediction.
- **L2 no leakage.** As above.
- **P1 topology.** A topology computation inferred a series count. This is the relationship that unlocks the cell checks.
- **P2 charge to cutoff.** Charge was counted (integrate or capacity) over `pack_current` in a window that contains a
  protective contactor opening.
- **P3 current–voltage step relation.** A counted test of `step_response` with x = `pack_current`, in which C made a
  prediction.
- **P4 bypass load.** A counted test of `unexplained_changes` or `transitions` involving `pack_current`, in which C made a
  prediction.
- **P5 capacity framing.**
  - P5a: a `capacity` computation with a full-charge (`counter_since_anchor`) anchor and at least one other anchor.
  - P5b: the final report says that capacity depends on the anchor, full charge or calibration.
  - P5 = P5a and P5b.
- **R1.** A and B are not separated by a test whose value is an observation. They may be separated only conditionally,
  on a stated anchor.
- **N1.** The next action, data requests or questions name a metered or charger charge record, or a charge/calibration
  history.

Recorded without a threshold:
- the ranking;
- each hypothesis's standing;
- the ops used;
- post-hoc tests;
- records by kind;
- whether C ended contradicted;
- the inferred series count;
- tool and model call counts, and time.

## Held-out SYNTHETIC incidents (unseen, solved by construction): 1 run of 6 cases

**Procedure.**
1. After the tag, `heldout.py seal` draws 6 cases from os.urandom: each of the 5 causes once, plus one random repeat.
   The causes are soc_drift, capacity_fade, current_scale, sensor_bypass and drift_weak_cell (see
   `scripts/make_synthetic_heldout.py`).
2. The key is written to `~/Veyra-private/inquiry-eval-v1/heldout-KEY.json`, and only its SHA-256 is printed.
3. The SHA-256 is recorded in a commit before `run`. The developer does not open the key before scoring.

**Profile.** As with a real customer profile, it declares no series count, chemistry or OCV curve.

**Per-case criteria.**
- **H1.** As L1.
- **H2.** Topology: correct, wrong or not run.
- **H3. Truth not demoted.**
  - Truth ids: soc_drift → A, capacity_fade → B, current_scale and sensor_bypass → C, drift_weak_cell → A (+D).
  - No false cause is ranked above the true one.
  - No counted, observation-valued test contradicts the true one.
- **H4. No false certainty.** For soc_drift, capacity_fade and drift_weak_cell, A and B are not separated by an
  observation-valued test, because by construction the record cannot separate them.
- **H5. Discovery where the data can show it.**
  - sensor_bypass: C is in the top tier.
  - A/B causes: C ended contradicted by a counted test.
  - current_scale: recorded only (a scale error leaves only a plausible-looking resistance).

**Bars.** H1 and H3 in 6/6. H4 in every applicable case. H5 in at least 2/3 of applicable cases. H2 is never "wrong".

## Not claimed whatever the result

- CASE A is development data: passing there shows the loop works on the case it was developed on, not that it generalises.
- The held-out cases are synthetic and come from a model written by the same developer. They test generalisation across
  causes, parameters and chemistry curves, not across real machines.
- Real validation needs the customer's other solved faults, with predictions sealed before the answer is revealed. This is still
  to do.
