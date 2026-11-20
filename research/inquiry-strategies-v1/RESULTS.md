# inquiry-strategies-v1: results (scored 2026-11-17 ~20:00 CET)

Frozen agent code `inquiry-strategies-v1-frozen` (a5eeb04). Five sealed SYNTHETIC cases, key SHA-256 2d4b4c96…. The key
was opened only after all 15 investigations were agent runs.

Five runs (free S5, coverage S4–S5, infogain S4–S5) first failed on the account's usage limit, before the model ran. After
the reset they were re-run with `--resume`; the failed records are kept as `*.failed-1.json`. Full records:
`~/Veyra-private/inquiry-strategies-v1/runs/` (Mac). Scores: `RESULTS.json` (this folder).

## Totals

| strategy | bits removed (of 7.92 possible) | false exclusions | H3 fail | H4 fail (false A/B certainty) | discovery (H5) | model calls | tool calls |
|---|---|---|---|---|---|---|---|
| free | 0.0 | 0 | 0 | 0 | 1 | 38 | 92 |
| coverage | 0.585 | 0 | 0 | 1 | 0 | 47 | 127 |
| infogain | 0.0 | 0 | 0 | 1 | 0 | 44 | 121 |

The preregistered rule (zero false exclusions and truth demotions, then most bits) selects **coverage**. That selection
is not meaningful, for the reasons below.

## What actually happened

1. **No strategy discriminated.** 14 of 15 investigations ended with A, B and C all in first place. Each ran one or two
   counted tests. The agent explored first (3–7 computations, which do not count), registered one test, and declared the
   rest not separable.
2. **Coverage's 0.585 bits are spurious.** In coverage S5 (truth: drift_weak_cell, A), B was ranked below A/C by a
   `capacity` test whose value is CONDITIONAL on an assumed SOC anchor. That is the capacity-framing trap; the ranking
   rule let a conditional value separate tiers.
3. **Manufactured A/B separations (H4 failures).**
   - coverage S3: `stats` median of a voltage;
   - infogain S3: `step_response`.
   
   Neither A (estimate offset) nor B (capacity) changes those quantities. The fault algebra checked a fault against a
   no-fault reference, but not two causes that both leave the quantity unchanged.
4. **The decisive test was found and then wasted.** In the bypass case (S4), coverage and infogain both ran
   `unexplained_changes` while exploring. Once explored, any prediction on it was post-hoc and could not count; neither
   registered it. Free never looked. The one case that was discoverable by construction was missed by all three.
5. **No false exclusions and no truth demotions.** The inquiry rules kept the agent from wrong certainty, except points 2
   and 3. The measurement-fault algebra turned several resistance-based contradictions into `contradicted_if_assumed`
   (S2/S4 in every strategy). That is correct: no resistance parameter is stated.

## Conclusion

The investigation POLICY (free / coverage / infogain) is not the lever. All three fail the same way. The failures are
mechanical and fixable in Veyra, not in the model:
- the decisive tests are not generated;
- exploration turns them post-hoc;
- tied causes may manufacture a separation;
- conditional values may rank.

The preregistered confirmation run of the selected strategy is NOT run. Selecting coverage on 0.585 spurious bits would
spend the confirmation set on a non-result. This deviation from the preregistration is deliberate and recorded here.
Next: inquiry-v3, below.

## inquiry-v3 (next)

1. **Veyra generates the discriminating tests.** From the fault algebra and the roles present, it registers every test on
   which two competing causes predict different DIRECTIONS (e.g. unseen_load: `unexplained_changes` > 0 vs 0), before any
   exploration. It runs them and records them.
2. **Tied causes cannot be separated** on a quantity none of them changes (refused at registration).
3. **A conditional value** (anchor- or assumption-dependent) **can weaken but never order tiers.**
4. **Exploration does not poison a test** when the test is registered by Veyra's generator before exploration, or runs on
   data the exploration did not touch.
