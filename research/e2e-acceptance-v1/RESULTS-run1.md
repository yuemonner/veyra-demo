# End-to-end acceptance v1: results

Protocol: PROTOCOL.md (committed before this run). Engineer steps scripted and labelled. Model ids reported by the CLI: none.

| Criterion | Result |
|---|---|
| C1 | PASS |
| C2 | PASS |
| C3 | PASS |
| C4 | PASS |
| C5 | PASS |
| C6 | FAIL |

| Run | Profile | Investigator | Mode | Check A | Report status | Tool calls | Model calls | s | Rejected claims (final) | Headline |
|---|---|---|---|---|---|---|---|---|---|---|
| R1 | synthetic-bev-tables | main | agent | VIOLATION | cause_unknown | 10 | 3 | 29.9 | 0 | Pack depleted after ~3.8 Ah delivered while SOC read 60%; BMS under-voltage protection opened contactors. |
| R2 | synthetic-bev-tables | main | agent | PASS | no_failure_found | 9 | 3 | 27.9 | 0 | No fault shown: SOC falls steadily in line with measured current; the t=200 'shift' is not a real jump. |
| R3 | synthetic-bev-tables | main | agent | VIOLATION | cause_unknown | 10 | 3 | 30.5 | 0 | Cell under-voltage protection opened HV after SOC stayed high, then jumped 56.8 to 24.0 % unexplained by charg |
| R4 | px4-battery-status | main | agent_failed | INDETERMINATE | cause_unknown | 0 | 0 | 1.3 | 0 | Strongest anomaly candidate at t=78.049 s: vehicle.attitude[yaw] followed control.attitude_setpoint[yaw] worse |

Scenario scn-6e2d1f97caf2 back-fill after approval: {"scenarios": ["scn-6e2d1f97caf2"], "recordings": ["rec-0000000000000000"], "results": [{"scenario_id": "scn-6e2d1f97caf2", "scenario_version": 1, "scenario_title": "SOC correction not explained by measured charge (from R1)", "scenario_status": "approved", "reviewed": true, "recording": "rec-0000000

R2 scenario results: ['PASS']; R3: ['VIOLATION']

R3 earlier knowledge used: [{'applicability': 'applies', 'case_id': 'rec-0000000000000000', 'hypothesis_id': 'A', 'result': 'confirmed'}]
