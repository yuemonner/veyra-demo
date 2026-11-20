# End-to-end acceptance v2: results

Protocol: PROTOCOL.md (committed before this run). Engineer steps scripted and labelled. Model ids reported by the CLI: claude-sonnet-5-5.

| Criterion | Result |
|---|---|
| C1 | PASS |
| C2 | PASS |
| C3 | PASS |
| C4 | PASS |
| C5 | PASS |
| C6 | PASS |
| S1 | FAIL |
| S2 | PASS |
| S3 | PASS |

| Run | Profile | Investigator | Mode | Check A | Report status | Tool calls | Model calls | s | Rejected claims (final) | Headline |
|---|---|---|---|---|---|---|---|---|---|---|
| R1 | synthetic-bev-tables | main | agent | VIOLATION | cause_unknown | 11 | 3 | 46.5 | 0 | Under-voltage trip after SOC jumped 38 points unexplained by counted charge; upstream cause not separable from |
| R2 | synthetic-bev-tables | main | agent | PASS | no_failure_found | 9 | 3 | 25.8 | 0 | No failure shown: SOC declines steadily with measured charge; the t=200 'shift' is a ramp artifact. |
| R3 | synthetic-bev-tables | main | agent | VIOLATION | cause_unknown | 5 | 5 | 92.6 | 0 | Under-voltage protection opened HV after a pack-wide energy shortfall; upstream cause unresolved among SOC, ca |
| P1 | px4-battery-status | main | agent | INDETERMINATE | cause_unknown | 10 | 5 | 97.0 | 0 | Motor outputs dropped to minimum at 90.755 s, before the logged kill-switch event; who commanded it is not rec |
| I1 | synthetic-bev-tables | main | agent | PASS | cause_unknown | 10 | 5 | 95.3 | 0 | BMS under-voltage protection opened HV at 2100.5 s after only ~4.6 Ah; SOC, capacity or current error unresolv |
| I2 | synthetic-bev-tables | main | agent | PASS | cause_unknown | 10 | 5 | 120.4 | 0 | Whole pack fell at constant 7.9 A until BMS under-voltage tripped HV; HV was then reclosed five times. |
| I3 | synthetic-bev-tables | main | agent | PASS | cause_unknown | 13 | 5 | 77.2 | 0 | Pack fell from 62% SOC to under-voltage trip after only ~4 Ah; upstream cause not separable in this data. |
| P2 | px4-battery-status | main | agent | INDETERMINATE | cause_unknown | 9 | 5 | 86.3 | 0 | Auto-land ended with all motor outputs cut to minimum, 0.8 s before the logged manual kill-switch. |

| Run | Hold | Gate blocked the model's action | Rules | Findings inserted by the gate | Model compliant | Final action passes |
|---|---|---|---|---|---|---|
| R1 | False | False |  | 0 | True | pass |
| R2 | False | False |  | 0 | True | pass |
| R3 | False | False |  | 0 | True | pass |
| P1 | False | True | G3,G4 | 0 | False | pass |
| I1 | True | True | G3 | 0 | False | blocked |
| I2 | True | True | G1,G3 | 0 | False | blocked |
| I3 | True | False |  | 0 | True | pass |
| P2 | False | False |  | 0 | True | pass |

Scenario scn-e7154fccb2cf back-fill after approval: {"scenarios": ["scn-e7154fccb2cf"], "recordings": ["rec-0000000000000000"], "results": [{"scenario_id": "scn-e7154fccb2cf", "scenario_version": 1, "scenario_title": "SOC correction not explained by measured charge (from R1)", "scenario_status": "approved", "reviewed": true, "recording": "rec-0000000

R2 scenario results: ['PASS']; R3: ['VIOLATION']

R3 earlier knowledge used: [{'applicability': 'applies', 'case_id': 'rec-0000000000000000', 'hypothesis_id': 'A', 'result': 'confirmed'}]
