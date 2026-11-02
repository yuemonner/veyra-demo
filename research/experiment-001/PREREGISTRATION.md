# Experiment 001: preregistration of the fairness check and the transfer test

Written and committed before any held-out robot B case was run. The simulator, inference, methods, tuned settings
(checklist order, misfit threshold), hypothesis grids and test costs are frozen at this commit. If a bug is found after
the run, the original result is kept and reported next to any corrected one, as a deviation.

## 1. Fairness: equal diagnostic knowledge

Earlier, only the "Veyra" method had the misfit detector and the library of further fault types, so the unknown-fault
gap could not be attributed to its reasoning. Methods are now a 3 x 2 design:

- **Selection:** fixed checklist (best order, tuned on robot A calibration faults) | random | classical active diagnosis
  (expected information gain per unit cost).
- **Knowledge:** fault list only | fault list + misfit detector (chi-square, p < 1e-8, tuned on robot A calibration) +
  the same library (intermittent left/right command cut-out).

The Veyra prototype is exactly "classical + detector". There is no further Veyra-specific reasoning in this prototype,
so any Veyra-specific effect is zero by construction; the test is which of the two factors explains the results.

## 2. Transfer: robot A to robot B, no retuning

Defined in `robot-b.mjs`.

- **Robot B, the world:** wheel radius 0.06 m (A: 0.05), track 0.38 m (A: 0.30), motor lag 0.14 s (A: 0.08),
  nominal top wheel speed 26 rad/s (A: 30), nominal latency 0.05 s (A: 0.03), sensor noise about 1.33x A's.
- **What the methods know about B:** the spec sheet only (wheel radius, track, top speed). Motor lag, sensor noise and
  latency keep robot A's assumptions. Same hypothesis grids, costs, budget, prior (uniform over fault types), checklist
  order and misfit threshold as robot A. Nothing is tuned on B.
- **Fault mix on B (prevalence):** none 10%, grip loss 15% per side, weak motor 5% per side, encoder 10% per side,
  gyro drift 15%, delay 15%. Ranges in B's terms (weak-motor top speed 8.5-13.5 rad/s, gyro drift 0.012-0.06 rad/s,
  delay 0.1-0.3 s; others as A). Unknown-fault cases: intermittent cut-out, left or right, 50/50.
- **Seeds:** robot B known faults 200001-200300; robot B unknown fault 200301-200400. Robot A is re-run on its
  earlier test seeds (100001-100400) as the in-distribution reference; those seeds were already reported, so A is not
  held out.

## 3. Measures and analysis (transfer.mjs)

For every method: diagnostic accuracy (correct and confident), test cost, confidently wrong, abstained (no 95% answer
within budget). All runs are included. 95% bootstrap CIs (2000 resamples, fixed seeds).

Paired comparisons on the same cases, mean difference with 95% bootstrap CI, for all four measures:

- **C1, selection (primary):** classical vs fixed checklist, robot B known faults.
- **C2, selection with equal knowledge:** classical + detector vs fixed + detector, robot B known and unknown faults.
- **C3, knowledge:** classical + detector vs classical, robot B unknown faults (and known faults, for its cost).
- C4: classical vs random; C5: fixed + detector vs fixed. Same comparisons on robot A for reference.

## 4. What counts as surviving

- **Adaptive selection survives transfer** if, on robot B known faults (C1), either the accuracy difference CI is
  entirely above 0, or the cost difference CI is entirely below 0 with the accuracy difference CI's lower end above
  -5 points; and confidently-wrong is not higher (CI not entirely above 0).
- **The detector-and-library effect survives transfer** if, on robot B unknown faults (C3), the accuracy difference
  CI is entirely above 0 and confidently-wrong falls (CI entirely below 0), while on robot B known faults its
  accuracy CI's lower end stays above -5 points.
- Anything else is reported as not established. Failed and unresolved runs are listed by true fault.
