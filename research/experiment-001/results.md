# Experiment 001 results (simulation)

Fingerprint `8bef3bd7dfcc127f` · 300 known-fault cases (seeds 100001–100300), 100 unknown-fault cases (seeds 100301–100400).

## Known faults (in the hypothesis list)

| Method | Correct (95% CI) | Confidently wrong | Undetermined | Tests | Test cost (95% CI) | Widened hypotheses |
|---|---|---|---|---|---|---|
| Fixed checklist | 90.0% (86.0%–93.3%) | 0.7% | 9.3% | 1.87 | 6.00 (5.66–6.35) | 0.0% |
| Random order | 83.0% (78.7%–87.0%) | 0.0% | 17.0% | 3.23 | 7.70 (7.23–8.19) | 0.0% |
| Classical active diagnosis | 92.7% (89.3%–95.3%) | 0.0% | 7.3% | 2.15 | 5.97 (5.57–6.36) | 0.0% |
| Veyra (prototype) | 92.3% (89.0%–95.3%) | 0.7% | 7.0% | 2.18 | 6.06 (5.66–6.47) | 5.3% |

Paired, same hidden faults, test cost minus the best fixed checklist: classical -0.03 (95% CI -0.32 to 0.27), Veyra 0.06 (95% CI -0.23 to 0.34).

## Unknown fault (a motor command cutting out; not in the initial list)

| Method | Correct (95% CI) | Confidently wrong | Undetermined | Tests | Test cost (95% CI) | Widened hypotheses |
|---|---|---|---|---|---|---|
| Fixed checklist | 0.0% (0.0%–0.0%) | 100.0% | 0.0% | 1.04 | 4.11 (4.00–4.26) | 0.0% |
| Random order | 0.0% (0.0%–0.0%) | 100.0% | 0.0% | 1.57 | 3.64 (3.24–4.09) | 0.0% |
| Classical active diagnosis | 0.0% (0.0%–0.0%) | 100.0% | 0.0% | 1.21 | 3.14 (2.82–3.55) | 0.0% |
| Veyra (prototype) | 98.0% (95.0%–100.0%) | 2.0% | 0.0% | 1.17 | 2.91 (2.58–3.28) | 98.0% |
