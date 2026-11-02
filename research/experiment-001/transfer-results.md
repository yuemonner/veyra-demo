# Experiment 001 transfer test results

Preregistered in PREREGISTRATION.md. Fingerprint `38a5950e96b95e75`. Every run is included; abstentions are runs that ended without a 95% answer.

## Robot A (development robot), known faults: 300 cases

| Method | Correct | Test cost | Confidently wrong | Abstained |
|---|---|---|---|---|
| Fixed checklist | 90.0% [86.3, 93.3] | 6.00 [5.67, 6.36] | 0.7% [0.0, 1.7] | 9.3% [6.0, 12.7] |
| Random order | 83.0% [78.7, 87.3] | 7.70 [7.21, 8.16] | 0.0% [0.0, 0.0] | 17.0% [13.0, 21.3] |
| Classical active diagnosis | 92.7% [89.7, 95.3] | 5.97 [5.58, 6.35] | 0.0% [0.0, 0.0] | 7.3% [4.7, 10.3] |
| Fixed checklist + detector | 87.7% [83.7, 91.3] | 6.00 [5.67, 6.36] | 3.0% [1.3, 5.0] | 9.3% [6.0, 12.7] |
| Random order + detector | 82.3% [78.0, 86.7] | 7.72 [7.23, 8.19] | 0.7% [0.0, 1.7] | 17.0% [13.0, 21.3] |
| Classical active + detector | 92.7% [89.7, 95.3] | 5.98 [5.59, 6.36] | 0.0% [0.0, 0.0] | 7.3% [4.7, 10.3] |

Paired differences on the same cases (first minus second), 95% bootstrap CI:

| Comparison | Correct | Test cost | Confidently wrong | Abstained |
|---|---|---|---|---|
| Classical active diagnosis vs Fixed checklist | +2.7 pts [0.7, 5.0] | -0.03 [-0.33, 0.29] | -0.7 pts [-1.7, 0.0] | -2.0 pts [-4.3, 0.3] |
| Classical active diagnosis vs Random order | +9.7 pts [5.7, 13.7] | -1.73 [-2.12, -1.33] | +0.0 pts [0.0, 0.0] | -9.7 pts [-13.7, -6.0] |
| Classical active + detector vs Fixed checklist + detector | +5.0 pts [2.3, 8.0] | -0.02 [-0.32, 0.30] | -3.0 pts [-5.0, -1.3] | -2.0 pts [-4.3, 0.3] |
| Classical active + detector vs Classical active diagnosis | +0.0 pts [0.0, 0.0] | +0.01 [0.00, 0.03] | +0.0 pts [0.0, 0.0] | +0.0 pts [0.0, 0.0] |
| Fixed checklist + detector vs Fixed checklist | -2.3 pts [-4.0, -0.7] | +0.00 [0.00, 0.00] | +2.3 pts [0.7, 4.0] | +0.0 pts [0.0, 0.0] |

## Robot A (development robot), unknown faults: 100 cases

| Method | Correct | Test cost | Confidently wrong | Abstained |
|---|---|---|---|---|
| Fixed checklist | 0.0% [0.0, 0.0] | 4.11 [4.00, 4.26] | 100.0% [100.0, 100.0] | 0.0% [0.0, 0.0] |
| Random order | 0.0% [0.0, 0.0] | 3.64 [3.25, 4.06] | 100.0% [100.0, 100.0] | 0.0% [0.0, 0.0] |
| Classical active diagnosis | 0.0% [0.0, 0.0] | 3.14 [2.82, 3.51] | 100.0% [100.0, 100.0] | 0.0% [0.0, 0.0] |
| Fixed checklist + detector | 100.0% [100.0, 100.0] | 4.14 [4.03, 4.30] | 0.0% [0.0, 0.0] | 0.0% [0.0, 0.0] |
| Random order + detector | 97.0% [93.0, 100.0] | 3.63 [3.26, 4.04] | 3.0% [0.0, 7.0] | 0.0% [0.0, 0.0] |
| Classical active + detector | 97.0% [93.0, 100.0] | 3.13 [2.82, 3.50] | 3.0% [0.0, 7.0] | 0.0% [0.0, 0.0] |

Paired differences on the same cases (first minus second), 95% bootstrap CI:

| Comparison | Correct | Test cost | Confidently wrong | Abstained |
|---|---|---|---|---|
| Classical active diagnosis vs Fixed checklist | +0.0 pts [0.0, 0.0] | -0.97 [-1.27, -0.64] | +0.0 pts [0.0, 0.0] | +0.0 pts [0.0, 0.0] |
| Classical active diagnosis vs Random order | +0.0 pts [0.0, 0.0] | -0.50 [-0.89, -0.11] | +0.0 pts [0.0, 0.0] | +0.0 pts [0.0, 0.0] |
| Classical active + detector vs Fixed checklist + detector | -3.0 pts [-7.0, 0.0] | -1.01 [-1.30, -0.69] | +3.0 pts [0.0, 7.0] | +0.0 pts [0.0, 0.0] |
| Classical active + detector vs Classical active diagnosis | +97.0 pts [93.0, 100.0] | -0.01 [-0.03, 0.00] | -97.0 pts [-100.0, -93.0] | +0.0 pts [0.0, 0.0] |
| Fixed checklist + detector vs Fixed checklist | +100.0 pts [100.0, 100.0] | +0.03 [0.00, 0.09] | -100.0 pts [-100.0, -100.0] | +0.0 pts [0.0, 0.0] |

## Robot B (held out), known faults: 300 cases

| Method | Correct | Test cost | Confidently wrong | Abstained |
|---|---|---|---|---|
| Fixed checklist | 39.0% [33.7, 45.0] | 4.24 [4.15, 4.34] | 61.0% [55.3, 66.3] | 0.0% [0.0, 0.0] |
| Random order | 28.3% [23.3, 33.7] | 3.60 [3.39, 3.83] | 71.7% [66.3, 76.7] | 0.0% [0.0, 0.0] |
| Classical active diagnosis | 29.3% [24.3, 34.7] | 3.15 [3.01, 3.28] | 70.7% [65.3, 75.7] | 0.0% [0.0, 0.0] |
| Fixed checklist + detector | 34.0% [28.7, 39.7] | 4.31 [4.21, 4.44] | 66.0% [60.7, 71.0] | 0.0% [0.0, 0.0] |
| Random order + detector | 26.3% [21.7, 31.3] | 3.65 [3.44, 3.88] | 73.7% [68.7, 78.3] | 0.0% [0.0, 0.0] |
| Classical active + detector | 28.0% [23.0, 33.3] | 3.16 [3.02, 3.31] | 72.0% [66.7, 77.0] | 0.0% [0.0, 0.0] |

Paired differences on the same cases (first minus second), 95% bootstrap CI:

| Comparison | Correct | Test cost | Confidently wrong | Abstained |
|---|---|---|---|---|
| Classical active diagnosis vs Fixed checklist | -9.7 pts [-15.0, -4.3] | -1.10 [-1.26, -0.93] | +9.7 pts [4.3, 14.7] | +0.0 pts [0.0, 0.0] |
| Classical active diagnosis vs Random order | +1.0 pts [-3.7, 5.3] | -0.45 [-0.69, -0.21] | -1.0 pts [-5.3, 3.7] | +0.0 pts [0.0, 0.0] |
| Classical active + detector vs Fixed checklist + detector | -6.0 pts [-10.7, -1.3] | -1.15 [-1.33, -0.97] | +6.0 pts [1.3, 11.0] | +0.0 pts [0.0, 0.0] |
| Classical active + detector vs Classical active diagnosis | -1.3 pts [-2.7, -0.3] | +0.02 [0.00, 0.04] | +1.3 pts [0.3, 2.7] | +0.0 pts [0.0, 0.0] |
| Fixed checklist + detector vs Fixed checklist | -5.0 pts [-7.7, -2.7] | +0.07 [0.02, 0.13] | +5.0 pts [2.7, 7.7] | +0.0 pts [0.0, 0.0] |

## Robot B (held out), unknown faults: 100 cases

| Method | Correct | Test cost | Confidently wrong | Abstained |
|---|---|---|---|---|
| Fixed checklist | 0.0% [0.0, 0.0] | 4.03 [4.00, 4.09] | 100.0% [100.0, 100.0] | 0.0% [0.0, 0.0] |
| Random order | 0.0% [0.0, 0.0] | 2.86 [2.68, 3.06] | 100.0% [100.0, 100.0] | 0.0% [0.0, 0.0] |
| Classical active diagnosis | 0.0% [0.0, 0.0] | 2.48 [2.35, 2.62] | 100.0% [100.0, 100.0] | 0.0% [0.0, 0.0] |
| Fixed checklist + detector | 91.0% [85.0, 96.0] | 4.03 [4.00, 4.09] | 9.0% [4.0, 15.0] | 0.0% [0.0, 0.0] |
| Random order + detector | 89.0% [82.0, 95.0] | 2.88 [2.69, 3.08] | 11.0% [6.0, 18.0] | 0.0% [0.0, 0.0] |
| Classical active + detector | 89.0% [83.0, 95.0] | 2.49 [2.36, 2.62] | 11.0% [5.0, 17.0] | 0.0% [0.0, 0.0] |

Paired differences on the same cases (first minus second), 95% bootstrap CI:

| Comparison | Correct | Test cost | Confidently wrong | Abstained |
|---|---|---|---|---|
| Classical active diagnosis vs Fixed checklist | +0.0 pts [0.0, 0.0] | -1.55 [-1.69, -1.41] | +0.0 pts [0.0, 0.0] | +0.0 pts [0.0, 0.0] |
| Classical active diagnosis vs Random order | +0.0 pts [0.0, 0.0] | -0.38 [-0.60, -0.14] | +0.0 pts [0.0, 0.0] | +0.0 pts [0.0, 0.0] |
| Classical active + detector vs Fixed checklist + detector | -2.0 pts [-7.0, 3.0] | -1.54 [-1.68, -1.39] | +2.0 pts [-3.0, 7.0] | +0.0 pts [0.0, 0.0] |
| Classical active + detector vs Classical active diagnosis | +89.0 pts [83.0, 95.0] | +0.01 [-0.03, 0.06] | -89.0 pts [-95.0, -83.0] | +0.0 pts [0.0, 0.0] |
| Fixed checklist + detector vs Fixed checklist | +91.0 pts [85.0, 96.0] | +0.00 [0.00, 0.00] | -91.0 pts [-96.0, -85.0] | +0.0 pts [0.0, 0.0] |

## Against the preregistered criteria

| Claim | Criterion (PREREGISTRATION.md section 4) | Robot B result | Verdict |
|---|---|---|---|
| Adaptive selection survives transfer | C1 accuracy CI above 0, or cost CI below 0 with accuracy CI lower end above -5 pts; confidently-wrong not higher | Accuracy -9.7 pts [-15.0, -4.3]; cost -1.10 [-1.26, -0.93]; confidently wrong +9.7 pts [4.3, 14.7] | **Not established. It got worse:** cheaper, but less accurate and more often confidently wrong |
| Detector and library survive transfer | C3 accuracy CI above 0 and confidently-wrong CI below 0 on unknown faults; known-fault accuracy lower end above -5 pts | Unknown: +89.0 pts [83.0, 95.0], confidently wrong -89.0 pts [-95.0, -83.0]; known: -1.3 pts [-2.7, -0.3] | **Established** |

On robot A (not held out), adaptive selection was slightly more accurate than the tuned checklist at the same cost:
+2.7 pts [0.7, 5.0].

## What failed on robot B, and why (descriptive; nothing was changed)

- **Every method was confidently wrong on 61-72% of known-fault cases on robot B, and none abstained.**
- Almost every failure was diagnosed as **"control delay"**: robot B's slower motors (0.14 s lag, not 0.08) and higher
  nominal latency, which the model was not told about, look exactly like a delay fault. The misfit detector rarely fired
  (10 of 300 cases), because a wrong fault explained the mismatch well.
- Adaptive selection was hurt most: it picks the cheapest informative test (the gentle run), "delay" passes 95% at once,
  and it stops. The checklist starts with the top-speed run, which happens to catch weak motors (30/30 vs 0/30).

## What survives

1. **Detecting that no listed cause explains the data, and widening the search, prevents confident misdiagnosis of
   faults that were not on the list**, on both robots, whichever way tests are chosen. This comes from the detector and
   library, not from the choice of experiments, and not from anything specific to the prototype.
2. **Information-gain selection gives a small accuracy gain on the robot it was developed on**, but not on a new robot.

## What does not survive

- "Fewer or better experiments on an unfamiliar machine": not supported. On an unfamiliar robot the dominant problem is
  that the machine's own normal behaviour is unknown, and unmodelled differences masquerade as a fault. Choosing tests
  more cleverly made that worse.

## Implication for the research program (a new question, not a fix)

Diagnosing faults on an unfamiliar machine first requires learning that machine's normal behaviour (program 002,
active system identification), and a detector that can recognise "this robot is different" rather than only "this
fault is not on my list". Any follow-up is a new preregistered experiment with new seeds; these results stand as they are.
