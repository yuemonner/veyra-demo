# dynamics-eval-v2: confirmation of the corrected dynamics capability on a fresh sealed set (preregistration)

Written and committed before any v2 run. The code is frozen at tag `dynamics-eval-v2-frozen`. The harness is a copy of
dynamics-eval-v1's, unchanged except for the tag and the version labels. Its measures, keyword tables and scoring are
identical, and its rules are those of dynamics-eval-v1 (never overwrite a record; `--resume` keeps failed records).

## Why there is a v2

dynamics-eval-v1 ran its 108 engine cases on 2026-11-17. Its agent runs were stopped by a usage limit and are being
completed unchanged; their result is reported as it comes. The engine result already showed three defects in v1:
- the generated tests contradicted the true cause in 23 of 59 event cases;
- warning rule W1 raised 16 false alarms in 36 battery non-fault cases;
- preregistered variant W2 raised 0 false alarms, and its preregistration says W2 needs a confirmation set.

A post-run diagnosis found the defects below. It was done on development seeds 2001–2004, never on the sealed v1 cases
beyond their aggregate counts.

### D1: jumps the load explains were counted as glitches

When one member's resistance grows (or an actuator's torque constant falls), the spread steps at every load step. Its
residual against the baseline relation therefore jumps, which test T4 read as a sensing signature. That contradicted the
load mechanism in 6 of 6 battery and 4 of 5 robot load cases.

v2 counts a jump only where no driver step lies within one update interval. A sensing fault moves a reading without a
load change; a physical loss moves it with one.

### D2: statistical significance was taken for physical relevance

A long, dense record makes a small, healthy bend of the group's trend "significant". Two examples:
- the weakest of 96 healthy cells entering the curve's knee first;
- mid-range OCV curvature.

That raised W1 false alarms and made T1 and T2 contradict an external stop. v2 adds materiality, with one principle
everywhere: a departure counts when it is at least half of the change it belongs to.
- **warning:** W1 plus `differential_share >= 1/2`. This is exactly v1's preregistered W2, now the rule.
- **T1 (drift vs external):** reads `own_departure_z`. This is the spread departure, counted only when at least half of
  the limiting member's excursion is its own; otherwise 0.
- **T2 (common vs external):** reads `far_material_departure_z`. This is the far member's departure, counted only when it
  is at least half of that member's change since the baseline; otherwise 0.

Nothing else changed: the engine, mechanism classes, other tests, next-check rule, inquiry, prompts and Safety Gate are
the same. Development-seed result (40 event cases, 32 controls, not a confirmation):

| measure | v1 | v2 |
|---|---|---|
| false alarms | 8 | 0 |
| component faults warned | 19 / 24 | 11 / 24 |
| truth in the top set | 26 / 40 | 32 / 40 |
| truth contradicted | 15 | 8 |

The cost is fewer warnings for load and sensing faults; it is stated now.

## Sets (drawn after the freeze with os.urandom; key outside the repository, SHA-256 committed before the first run)

- **Engine:** 6 seeds x 9 classes x 2 categories = 108 cases. The design is the same as v1's, but the seeds are fresh.
  - Run by BOTH the v1 code (tag `dynamics-eval-v1-frozen`) and the v2 code on the same cases. That makes a matched
    engine before/after on unseen cases.
- **Agent:** 1 seed x 5 event classes x 2 categories = 10 cases. Run by the OLD agent (`old-agent-matched`, no dynamics)
  and the NEW v2 agent.
  - Same model backend, same coverage strategy, same turn budget.
  - Interleaved per case, so a usage-limit stop affects both alike.
- **CASE A:** the v2 agent runs once, under the customer's recorded permission, as a development regression. It is never
  evidence of generalisation, and it is scored with the same `case_a_targets.py`.

## Measures and decision (fixed now)

The measures are dynamics-eval-v1's, unchanged.

v2 is CONFIRMED as an engine improvement only if, on the fresh engine set and against the v1 code on the same cases:
- (a) it raises no more false alarms;
- (b) the truth is in the top set at least as often;
- (c) the truth is contradicted no more often.

Detection is reported separately, as a stated trade-off.

The agent comparison is reported as measured: truth in the top tier, false certainty, next check, dynamics ops used,
rejected claims and cost. With 10 cases it shows direction only.

## Limits stated now

- **Same generator.** The fresh set comes from the generator the defects were found on. It confirms the fixes
  generalise to unseen seeds of that world, not to real machines.
- **The materiality factor is a design choice.** The "at least half" factor was not fitted; it is stated, and
  alternatives are untested.
- **No agent controls.** No healthy-control cases are investigated by the agents, because an investigation starts from an
  event. Agent false alarms are therefore not measured; the engine's are.
- **Nothing measures Alloy.**
