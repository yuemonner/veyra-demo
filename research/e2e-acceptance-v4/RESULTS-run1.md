# End-to-end acceptance v4: results

Protocol: PROTOCOL.md (committed before this run). Engineer steps scripted and labelled. Model ids reported by the CLI: claude-sonnet-5-5.

| Criterion | Result |
|---|---|
| C1 | PASS |
| C2 | PASS |
| C3 | PASS |
| C4 | PASS |
| C5 | PASS |
| C6 | PASS |
| S1 | PASS |
| S2 | PASS |
| S3 | PASS |
| S5 | PASS |
| S4 | PASS |

| Run | Profile | Investigator | Mode | Check A | Report status | Tool calls | Model calls | s | Rejected claims (final) | Headline |
|---|---|---|---|---|---|---|---|---|---|---|
| R1 | synthetic-bev-tables | main | agent | VIOLATION | cause_unknown | 13 | 4 | 68.2 | 0 | BMS under-voltage protection opened HV after SOC was abruptly restated 60.8%→22.8%; upstream cause not separab |
| R2 | synthetic-bev-tables | main | agent | PASS | no_failure_found | 12 | 4 | 33.2 | 0 | No failure shown: SOC falls steadily with constant discharge current; the t=200 s 'shift' is part of a slow ra |
| R3 | synthetic-bev-tables | main | agent | VIOLATION | cause_unknown | 12 | 4 | 70.5 | 0 | BMS under-voltage protection opened contactors; SOC was overstated by about 56 pp until an abrupt correction,  |
| P1 | px4-battery-status | main | agent | INDETERMINATE | cause_unknown | 10 | 5 | 98.5 | 0 | Yaw tracking degraded from 76.483 s; motor outputs fell to 900 us around the manual kill-switch |
| I1 | synthetic-bev-tables | main | agent | PASS | cause_unknown | 11 | 4 | 73.7 | 0 | BMS under-voltage trip at 2100.5 s after SOC overstated by about 56 points; cause of the overstatement is open |
| I2 | synthetic-bev-tables | main | agent | PASS | cause_unknown | 13 | 4 | 81.9 | 0 | Pack-wide under-voltage trip while BMS SOC read ~57%; SOC error, capacity loss or current under-read not separ |
| I3 | synthetic-bev-tables | main | agent | PASS | cause_unknown | 13 | 4 | 77.0 | 0 | Cell under-voltage trip after SOC read about 58 points too high; cause is SOC, capacity or current sensing, no |
| P2 | px4-battery-status | main | agent | INDETERMINATE | cause_unknown | 10 | 4 | 69.4 | 0 | Motor outputs dropped to minimum at t=76.1 s, before the logged kill-switch event, during an AUTO_LAND descent |

| Run | Hold | Gate blocked the model's action | Rules | Findings inserted by the gate | Model compliant | Final action passes |
|---|---|---|---|---|---|---|
| R1 | False | False |  | 0 | True | pass |
| R2 | False | False |  | 0 | True | pass |
| R3 | False | False |  | 0 | True | pass |
| P1 | False | False |  | 0 | True | pass |
| I1 | True | False |  | 0 | True | pass |
| I2 | True | False |  | 0 | True | pass |
| I3 | True | False |  | 0 | True | pass |
| P2 | False | False |  | 0 | True | pass |

Scenario scn-fd6263073bd9 back-fill after approval: {"scenarios": ["scn-fd6263073bd9"], "recordings": ["rec-0000000000000000"], "results": [{"scenario_id": "scn-fd6263073bd9", "scenario_version": 1, "scenario_title": "SOC correction not explained by measured charge (from R1)", "scenario_status": "approved", "reviewed": true, "recording": "rec-0000000

Blocked actions (full text, for review):


R2 scenario results: ['PASS']; R3: ['VIOLATION']

R3 earlier knowledge used: [{'applicability': 'applies', 'case_id': 'rec-0000000000000000', 'hypothesis_id': 'A', 'result': 'confirmed'}]
