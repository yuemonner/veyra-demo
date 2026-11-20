# Public demo release: P0 checklist (2026-10-23)

Goal: an engineer who has never seen Veyra opens the link and, within ten minutes, understands what it found, why it
recommends the next step, and can't find a factual or consistency error. Research is paused (`docs/research/`).
Deployed 2026-10-23 (approved): API on Render and web on Vercel from commit `ed2c0fb` (main, the Render branch and the
review branch all point to it), then a wording-only web fix (see the end).

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
| 12 | **Public write access** | **pass (live)** | Production accepted anonymous writes: the access code was never set on Render, so the gate stayed open (an empty request got 422 instead of 401). Now a hosted, non-private server refuses every write without a code (`RENDER` set, or `VEYRA_PUBLIC_DEMO=true`), so it no longer depends on dashboard settings. Live check after deploy: POST, PUT, PATCH, DELETE on cases, ledger, runs, the demo builder and the older API routes all answer 403 `read_only_demo`; a guessed code too; webhooks refuse (not configured); 19 cases before and after. |
| 13 | Frontend/backend parity, smoke test | pass (live) | Web and API from `ed2c0fb`; the new `/states` and `/series` endpoints answer on the live API. Live: ALFA rudder and roll plots render; each finding sentence once in Evidence; Brief and Next action agree; all 9 visitor cases have a valid record chain; real-data cases stop at "anomaly", synthetic stories reach a cause only through their closure; outcomes show "recorded", not "verified". |
| 14 | Brief "Do next" vs Next action | pass (live) | When no remote check can change which part is more likely, the Brief now says "Next step: on site, or a decision" (Next action's own reading) and offers the remaining check as optional context. The conclusion is unchanged. |
| 15 | Public reads | pass (live) | The hosted API holds only public research data (CMU AirLab ALFA, ETH GrandTour ANYmal) and synthetic cases; no private instance data is on it. Do not invite real uploads to this server. |

Tests (local, on the Mac, release code `ed2c0fb`): backend **321 passed** (27 new in `tests/test_release_guards.py`, 1 new public-demo gate test); frontend type check clean. Looked at locally: the ALFA case shows the rudder plot (held at 1900 µs against its normal range) and the roll plot (command vs measured), each finding sentence once in Evidence.
