# End-to-end acceptance v1: results

Protocol: PROTOCOL.md (committed before this run). Engineer steps scripted and labelled. Model ids reported by the CLI: claude-sonnet-5-5.

| Criterion | Result |
|---|---|
| C1 | PASS |
| C2 | PASS |
| C3 | PASS |
| C4 | PASS |
| C5 | PASS |
| C6 | PASS |

| Run | Profile | Investigator | Mode | Check A | Report status | Tool calls | Model calls | s | Rejected claims (final) | Headline |
|---|---|---|---|---|---|---|---|---|---|---|
| R1 | synthetic-bev-tables | main | agent | VIOLATION | cause_unknown | 9 | 3 | 39.5 | 0 | Cells sagged from t=640 s, SOC jumped 60.8→22.8 % at t=1576 s, and BMS under-voltage protection opened HV at t |
| R2 | synthetic-bev-tables | main | agent | PASS | no_failure_found | 8 | 3 | 26.1 | 0 | No failure found: SOC declines steadily with measured charge; the 200 s 'shift' is a drift artefact. |
| R3 | synthetic-bev-tables | main | agent | VIOLATION | cause_unknown | 10 | 3 | 45.9 | 0 | BMS under-voltage protection opened HV after SOC was re-stated by 33 points with no charge to explain it. |
| R4 | px4-battery-status | main | agent | INDETERMINATE | no_failure_found | 9 | 4 | 51.1 | 0 | Flight ended by a logged manual kill-switch; no autonomous machine fault is shown in the recording. |

Scenario scn-5567c34d9d8e back-fill after approval: {"scenarios": ["scn-5567c34d9d8e"], "recordings": ["rec-0000000000000000"], "results": [{"scenario_id": "scn-5567c34d9d8e", "scenario_version": 1, "scenario_title": "SOC correction not explained by measured charge (from R1)", "scenario_status": "approved", "reviewed": true, "recording": "rec-0000000

R2 scenario results: ['PASS']; R3: ['VIOLATION']

R3 earlier knowledge used: [{'applicability': 'applies', 'case_id': 'rec-0000000000000000', 'hypothesis_id': 'A', 'result': 'confirmed'}]
