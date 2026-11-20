# expert-methods-eval-v1: results (2026-11-19)

- **Code under test:** tag `expert-methods-eval-v1-frozen` (399b7c7), run from a worktree at the tag.
- **Set:** sealed set (`SEALED.md`), 60 SYNTHETIC cases, 12 per cause, in two conditions.
- **Engine only:** no model was called.
- **Raw per-case outputs:** `~/Veyra-private/expert-methods-eval-v1/runs/`.
- **Scores:** `RESULTS.json`.

## Preregistered criteria

| Criterion | Result |
|---|---|
| P1 no harm: record evidence never weakens the true cause | **pass**: 0 / 120 case-conditions |
| P2 evidence leads are right (>= 90 %, >= 3 leads) | **pass**: 12 / 12 (6 per condition, all `sensor_bypass` via load transitions) |
| P3 no firm prior lead without a confirmed chemistry (condition U) | **pass**: 0 prior leads; 54 conditional leads |
| P4 no planning regression (first check separates the truth) | **pass**: v3 60 / 60, v4 60 / 60 in each condition (no gain either: v3 already met it) |

## Reported without a criterion

**Prior leads in condition D (chemistry declared).** 36 leads, 15 correct:

| Cause | Prior leads | Correct |
|---|---|---|
| `soc_drift` | 7 / 12 | 7 |
| `drift_weak_cell` | 8 / 12 | 8 |
| `capacity_fade` | 9 / 12 | 0 |
| `current_scale` | 7 / 12 | 0 |
| `sensor_bypass` (records too short to show load switchings) | 5 / 12 | 0 |

- **Every prior lead was A, and A was led only on a flat curve.** It was right on every A case and wrong on every
  non-A case it was given for.
- **That is what a base-rate prior does on a uniform mix.** The prior is worth only as much as the base rate behind it.
- **Where the field base rate is drift-dominated, the prior is useful.** Whether a fleet's base rate is drift-dominated has to come from the customer's own verified outcomes. It is labelled "prior, not evidence", and the separating check (history since the
  last full charge) is always first.
- **This set cannot measure field accuracy.**

**Other measures.**
- **Bypass detection:**
  - 6 / 12 records show the load not seen by the current sensor;
  - 6 / 12 abstain: the record is too short for three switchings;
  - 0 are wrong.
- **Weak cell (`drift_weak_cell`):** D is graded "contributing, timing only" in 12 / 12 and never leads.

## What this does and does not show

**Shows:**
- The expert layer adds correct record-evidence leads.
- It does no harm to the true cause.
- It refuses firm leads without a confirmed chemistry.

**Does not show:**
- That the AI agent uses these tools well. An agent-level evaluation needs model runs.
- Field accuracy of the prior. That needs field cases, starting with the customer's own verified outcomes, which are now
  the first prior basis.
