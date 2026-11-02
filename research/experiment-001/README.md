# Experiment 001: diagnose an unfamiliar robot by choosing experiments (simulation)

**Question.** Can an agent identify an unknown fault in a physical system with fewer, cheaper tests than standard
practice, and does it avoid confident mistakes when the fault is not one it was told about?

Everything is in `frontend/lib/experiment-sim.ts` (no dependencies; the same code will run the browser demo).
Tests: `node --experimental-strip-types --test frontend/tests/experiment-sim.test.cjs`.

## Set-up

- **Robot.** Two-wheeled differential drive: motors with lag and a top speed, ground traction limits per wheel, wheel
  encoders and a gyro at 10 Hz, command latency. No motion capture: position and heading are measured once at the end of
  each test (centimetre error), as an engineer would in the field.
- **Faults (the initial list).** None; a wheel losing grip (left/right); a motor that can't reach speed (left/right); an
  encoder mis-scaled (left/right); gyro drift; control delay. Faults show only in the conditions that excite them: grip
  under hard acceleration, a weak motor near top speed, gyro drift most clearly when still.
- **Unknown fault (not in the list).** A motor command cutting out intermittently (a loose cable), left or right.
- **Tests and costs.** Hold still (1), gentle straight run (2), slow spin (2), hard launch (3), top-speed run (4). Budget 14.
- **Hidden faults** are drawn from continuous ranges, so they never sit exactly on the hypothesis grid.

## Inference (shared by every method)

Synthetic likelihood: for each hypothesis (fault x parameter grid point) and test, 48 seeded noisy simulations give the
expected window-averaged sensor features; sensor noise is pooled across hypotheses. Bayesian posterior over the grid,
summed to fault families. Stop when one family reaches 95%, or when the budget runs out (then: undetermined).

## Methods

| Method | Chooses the next test by |
|---|---|
| Fixed checklist | The best of all 120 orders, tuned on 200 calibration faults |
| Random order | Random (seeded) |
| Classical active diagnosis | Expected information gain about the fault family, per unit cost |
| Veyra (prototype) | Same as classical, plus a misfit check (chi-square, p < 1e-8): if no hypothesis explains the data, widen the hypothesis space with a library of further fault types |

Free choices were tuned only on calibration seeds 10001-10200 (`tune.mjs`): the checklist order, and the misfit
threshold (the loosest with at most 5% false alarms on known faults). The benchmark (`benchmark.mjs`) uses test seeds
100001-100400, never used during development or tuning.

## Result (see results.md; fingerprint printed there)

1. **Known faults: no advantage over a well-tuned checklist.** Classical active diagnosis and Veyra cost the same as the
   best fixed order (paired difference about 0, CI includes 0) with similar accuracy. They beat a random order clearly.
   The honest reading: when the fault list is known and a checklist can be tuned on hundreds of labelled faults, adaptive
   selection does not buy much here. Its advantage is not needing that tuning.
2. **Unknown fault: list-only methods are confidently wrong every time; Veyra is right 98% of the time**, because it
   notices that nothing on its list explains the data and widens its search.

## Limits

- Simulation, one robot type, one unknown-fault type. The widened hypotheses come from a pre-written library, not from an
  AI model; generating them is the research step.
- The misfit check flags about 5% of known-fault cases (a false alarm costs nothing in accuracy here, only extra hypotheses).
- Next: test transfer (tune the checklist on one robot, test on a heavier one or different fault rates), larger and
  continuous test spaces, and AI-proposed hypotheses.
