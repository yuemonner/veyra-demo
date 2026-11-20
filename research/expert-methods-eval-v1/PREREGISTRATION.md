# expert-methods-eval-v1: preregistration

Written and committed before the sealed set is drawn. The code under test is frozen at tag `expert-methods-eval-v1-frozen`.

## Question

Veyra now encodes system-level engineering methods (system audit, when the SOC error arose, record evidence) and a
labelled order of which explanation to check first (prior). Does this:

1. never weaken the true cause with record evidence (no harm);
2. lead correctly whenever it leads on record evidence;
3. lead on a prior only when the chemistry is confirmed, and label it as a prior;
4. keep or improve the first recommended check, judged by whether it separates the true cause from the other open
   explanations?

The engine only: no model is called. Agent-level effects are not measured here.

## Set

- 60 SYNTHETIC incidents from `scripts/make_synthetic_heldout.py` (first-principles pack model; never customer data).
- 12 per cause:
  - `soc_drift`
  - `capacity_fade`
  - `current_scale`
  - `sensor_bypass`
  - `drift_weak_cell`
- Seeds are drawn after the freeze from `os.urandom`. The key is kept outside the repository at
  `~/Veyra-private/expert-methods-eval-v1/KEY.json`, and its SHA-256 goes into `SEALED.md` before any run.
- Truth mapping:

  | Cause | Truth |
  |---|---|
  | `soc_drift`, `drift_weak_cell` | A |
  | `capacity_fade` | B |
  | `current_scale`, `sensor_bypass` | C |

**The cause mix is uniform. It is not a field base rate.** A prior that encodes field base rates is expected to be wrong
on the rarer causes here. Its accuracy on this set is reported, not judged.

## Conditions

- **U (undeclared):** the profile states no chemistry. This was CASE A at intake.
- **D (declared):** the profile states the OCV shape of the generated curve (`flat` or `sloped`), as a customer would
  state the chemistry.

## Engines

- **v3:**
  - `hypotheses.assess` + `planner.plan` without the expert layer;
  - no leading entry;
  - the planner ranking is that of v3.
- **v4:** the same, plus `expert_layer.enrich`.

## Metrics (per case and condition)

- **M1 (first check separates the truth):** the first recommended check's predictions differ between the true
  hypothesis and every other open upstream hypothesis.
- **M2 (lead):**
  - the leading hypothesis and its `basis_kind` (`evidence` or `prior`), or none;
  - whether it equals the truth.
- **M3 (harm):** any record-evidence item with direction `weakens` on the true hypothesis.
- **M4 (bypass detection):** on `sensor_bypass`, whether `load_transitions` gives `not_seen`, or abstains.
- **M5 (weak cell):** on `drift_weak_cell`, whether D is graded `contributing_timing_only` and never leads.

## Criteria (pass / fail, fixed now)

- **P1, no harm:** M3 is 0 cases in both conditions.
- **P2, evidence leads are right:** v4 leads on `basis_kind = evidence` are correct in at least 90 % of the cases where
  such a lead is given (and at least 3 such cases exist).
- **P3, no firm prior without a confirmed chemistry:** in U, v4 gives no lead with `basis_kind = prior`.
- **P4, no planning regression:** v4's M1 count is at least v3's M1 count in each condition.

## Reported without a criterion

- Prior-lead accuracy per cause in D (expected: right on A causes with a flat curve, wrong on flat B and C cases).
- Conditional leads in U.
- M4 recall and abstentions.
- M5.
