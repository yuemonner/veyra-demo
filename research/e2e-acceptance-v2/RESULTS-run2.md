# End-to-end acceptance v2: results

Protocol: PROTOCOL.md (committed before this run). Engineer steps scripted and labelled. Model ids reported by the CLI: none.

| Criterion | Result |
|---|---|
| C1 | PASS |
| C2 | FAIL |
| C3 | PASS |
| C4 | PASS |
| C5 | PASS |
| C6 | FAIL |
| S1 | PASS |
| S2 | PASS |
| S3 | PASS |

| Run | Profile | Investigator | Mode | Check A | Report status | Tool calls | Model calls | s | Rejected claims (final) | Headline |
|---|---|---|---|---|---|---|---|---|---|---|
| R1 | synthetic-bev-tables | main | agent_failed | VIOLATION | cause_unknown | 0 | 0 | 6.4 | 0 | Strongest anomaly candidate at t=680 s: battery.pack_voltage[pack] changed level for good (307.7 -> 281.9 V) |
| R2 | synthetic-bev-tables | main | agent_failed | PASS | cause_unknown | 0 | 0 | 6.3 | 0 | Strongest anomaly candidate at t=200 s: battery.soc[bms] changed level for good (64.4 -> 60.8 %) |
| R3 | synthetic-bev-tables | main | agent_failed | VIOLATION | cause_unknown | 0 | 0 | 6.4 | 0 | Strongest anomaly candidate at t=680 s: battery.pack_voltage[pack] changed level for good (307.7 -> 281.9 V) |
| P1 | px4-battery-status | main | agent_failed | INDETERMINATE | cause_unknown | 0 | 0 | 6.1 | 0 | Strongest anomaly candidate at t=78.049 s: vehicle.attitude[yaw] followed control.attitude_setpoint[yaw] worse |
| I1 | synthetic-bev-tables | main | agent_failed | PASS | cause_unknown | 0 | 0 | 6.4 | 0 | Strongest anomaly candidate at t=628 s: battery.cell_voltage[min] changed level for good (3.2 -> 2.841 V) |
| I2 | synthetic-bev-tables | main | agent_failed | PASS | cause_unknown | 0 | 0 | 6.4 | 0 | Strongest anomaly candidate at t=665.5 s: battery.pack_voltage[pack] changed level for good (307.7 -> 278.6 V) |
| I3 | synthetic-bev-tables | main | agent_failed | PASS | cause_unknown | 0 | 0 | 6.6 | 0 | Strongest anomaly candidate at t=619 s: battery.cell_voltage[min] changed level for good (3.2 -> 2.844 V) |
| P2 | px4-battery-status | main | agent_failed | INDETERMINATE | cause_unknown | 0 | 0 | 5.9 | 0 | Strongest anomaly candidate at t=7.405 s: vehicle.altitude[estimate] changed level for good (0.11 -> 3.166 m) |

| Run | Hold | Gate blocked the model's action | Rules | Findings inserted by the gate | Model compliant | Final action passes |
|---|---|---|---|---|---|---|
| R1 | False | False |  | 1 | False | pass |
| R2 | False | False |  | 0 | True | pass |
| R3 | False | False |  | 1 | False | pass |
| P1 | False | False |  | 1 | False | pass |
| I1 | True | False |  | 5 | False | pass |
| I2 | True | False |  | 5 | False | pass |
| I3 | True | False |  | 5 | False | pass |
| P2 | False | False |  | 1 | False | pass |

Scenario scn-523f7937daf8 back-fill after approval: {"scenarios": ["scn-523f7937daf8"], "recordings": ["rec-0000000000000000"], "results": [{"scenario_id": "scn-523f7937daf8", "scenario_version": 1, "scenario_title": "SOC correction not explained by measured charge (from R1)", "scenario_status": "approved", "reviewed": true, "recording": "rec-0000000

R2 scenario results: ['PASS']; R3: ['VIOLATION']

R3 earlier knowledge used: [{'applicability': 'applies', 'case_id': 'rec-0000000000000000', 'hypothesis_id': 'A', 'result': 'confirmed'}]
