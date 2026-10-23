# Public demo release: P0 checklist (2026-10-23)

Goal: an engineer who has never seen Veyra opens the link and, within ten minutes, understands what it found, why it
recommends the next step, and can't find a factual or consistency error. Research is paused (`docs/research/`).
Nothing in this checklist is deployed yet; deployment needs approval.

Legend: **pass** verified by a test or by looking; **fixed** was broken, fixed locally, needs deploy; **open** known, not fixed.

| # | Area | Status | Evidence |
|---|---|---|---|
| 1 | Case isolation | pass | Baselines only from the same machine, never the same recording, synthetic and real never mixed (existing tests). New: machine history lists only *earlier* incidents (test A3). |
| 2 | Time (`as_of`) | fixed | Cross-case data had no time limit: a replay could use closures, baselines and normals recorded later. `investigate(as_of=…)` now limits every input (tests A1–A4). |
| 3 | Precedents vs context | fixed | Similar cases matched on findings only. Now firmware, software, kit, harness, interface and workload are compared; a settling check is promoted only when they match or neither case records them (tests B1–B2). |
| 4 | Check eligibility | fixed | Eight places decided "is this check still open" with different rules. One `eligibility()` now; a check that would fetch evidence already in the case (a flight-controller log) is "already in this case" (tests C1–C4). Checks that cannot run (missing data/context) are no longer listed as next checks. |
| 5 | Observation → cause | pass + fixed | The engine never writes a cause (test D1). A confirmed cause needs inspection, reproduction or a fix that worked (existing). New: records linked as confirmation must actually confirm (test D2). |
| 6 | Decision states | fixed | Check and decision states derived from the ledger with explicit tables (`app/engine/states.py`); a check cannot be re-reviewed, re-requested or answered twice after its result (tests D3–D4). `GET /engine/cases/{id}/states`. |
| 7 | Consistency Brief / Evidence / Next action | fixed | The rudder finding appeared 4 times. Now: the headline in the Brief, the full sentence once under All findings (with its plot); Outputs, Tracking and Next action refer to it. |
| 8 | Time-series evidence | fixed | No plots. Each output-freeze and tracking finding now shows the recorded samples (held value vs normal range; command vs measured), from the case's own data (`GET .../series`, peaks kept when thinned). |
| 9 | Units and provenance (demo cases) | pass | ALFA outputs are PWM µs (ArduPlane RC out); ANYmal and QD-1 checked earlier. |
| 10 | Units for uploaded PX4 logs | open | VOXL actuator outputs are not PWM µs (PX4 benchmark fix 2). Not reachable on the public demo (uploads are off); fix before uploads open. |
| 11 | Visitor text | fixed | Raw keys (`firmware_version`) and a stray ")" in tracking sentences; case titles cut mid-word in the picker. |
| 12 | **Public write access** | **fixed (blocker)** | Production accepted anonymous writes: the access code was never set on Render, so the gate stayed open (verified with an empty request: 422 instead of 401). The gate now fails closed: `VEYRA_PUBLIC_DEMO=true` without a code refuses every write (test added). Needs deploy. |
| 13 | Frontend/backend parity, smoke test | open | Production still runs the previous version. After approval: deploy, then smoke-test the four views of each demo case and one refused write. |

Tests: backend 319 passed on the Mac before items 11–12 (27 new in `tests/test_release_guards.py`); type check clean.
