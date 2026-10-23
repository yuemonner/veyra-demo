# Public incident benchmark, case 1: PX4 #26910 (baseline, unmodified rules)

Local only. Logs and outputs live in `.local-private/benchmark/` (git-ignored) and are not redistributed.
Runner: `scripts/benchmark/px4_26910.py`. Reader: `backend/app/ingest/ulog.py` (new, tests in `test_ulog.py`).

## Why not PX4 #21828 (the first choice)

The issue and its log link exist, but the log can no longer be downloaded: `logs.px4.io` answers *AccessDenied*.
PX4 Flight Review keeps logs for 12 months (policy announced October 2024); the July 2023 log is gone. We did not
substitute or reconstruct it.

## The case used instead

[PX4-Autopilot #26910](https://github.com/PX4/PX4-Autopilot/issues/26910), opened 2026-03-30, closed automatically for
inactivity on 2026-08-13. VOXL2 multicopter, PX4 1.17 beta, Mission mode. Height estimate unstable: sudden drops or
crashes, or climbs without pilot input. Eight real logs downloaded from `review.px4.io` (4 affected flights linked by
the reporter, 4 flights after the reporter changed parameters), SHA-256 recorded by the download script (`backups/fetch-px4b.txt` beside the project, not in the repo).

| Package | Contents | Given to Veyra |
|---|---|---|
| Evidence | the 4 affected flight logs; the symptom as first reported, *without* "disabling the barometer appeared to resolve it" | yes |
| After-change flights | 4 logs flown after the parameter change | only for a separate false-alarm check, never as a baseline |
| Sealed | the issue thread: maintainers flag non-default parameters (`MPC_Z_P` 5.0 vs default 1.0 and above its max, `MPC_Z_VEL_P_ACC` 8 vs 4, `EKF2_RNG_DELAY` 50 vs 5, …); the reporter's later flights after resetting them had no height problem | no |

Truth quality: **probable, not independently confirmed.** One maintainer saw no major tracking issue and suggested
estimator drift; the parameter explanation is supported by the reporter's later flights, then the issue went quiet.

## Ingestion gap (reported before the adapter was written)

Veyra refused PX4 ULog (`.ulg` was in the "known, unsupported" list). Added a reader for the published format,
standard library only, with a fixed documented topic map (attitude and setpoint, yaw rate and setpoint, height from
the estimator / barometer / GPS, ground speed, actuator outputs, collective thrust, mode and arming changes, log messages).
These VOXL logs have no GPS UTC time; the reader uses the logger's own file-name time and labels the clock
`logger_file_name`. One after-change log has no wall-clock time at all; Veyra's import refuses 1970 dates and we did not
invent one, so it was skipped.

## Rule fixed before the run

Takeoff = "Takeoff detected"; end = "Kill engaged" or "Landing detected"; assumed-normal = first 10 s after takeoff
(labelled assumed); problem = the rest. Machine type `fixed_wing_uav`: Veyra has no multicopter profile (known mismatch).

## What Veyra produced (unchanged rules)

| Question | Result |
|---|---|
| Anomalies correctly identified | **Not the height problem.** No rule compares the estimator's height with the barometer or GPS. Findings were brief spikes, attitude "departures" in the last seconds before the kill switch (the crash or the kill itself), and actuator "holds". |
| Unsupported claims | (1) Every flight was marked "Flight controller: anomaly observed" because any controller event near the window counts, including "Executing Mission" and "Opened log file". (2) "ch1 output held 4477" etc.: VOXL actuator outputs are not PWM microseconds; our mapping labelled them `us` and the hold rule fired on unused channels. (3) "roll stopped following its commands … 0.4× normal": the sentence contradicts itself (the summary statistic covers the whole window, the departure a 2 s stretch). |
| Hypotheses left open | Flight controller, actuation and airframe on every flight; nothing separated them. |
| Next proposed check | Retrieve the flight-controller log, and an input–response baseline. **The flight-controller log is the evidence already loaded.** "Confirm the airframe, firmware and parameter set" was ranked 2nd among all checks, but the plan never chose it because its outcomes are not in the comparison table. |
| GPS / estimator path before seeing the answer | No. |
| Not determinable from these logs | Whether the parameters caused the drops (needs a controlled reflight); what the reporter's barometer-off flights looked like (not linked). |
| False-alarm check (3 usable after-change flights) | **Same findings, same request on the good flights.** Veyra did not tell affected from unaffected flights. |

## What the evidence contained that Veyra did not use (evaluator's look, after the run; not a tuned rule)

- **Configuration.** The ULog stores every parameter and, for the 128 that differ from the firmware default, the
  default itself. 8 of them are height-control or height-estimator settings; maintainers named 6 of those. A read-only
  "what differs from default, filtered to the affected function" listing would have pointed at the answer from the log alone.
- **Redundant measurements.** Estimator height vs GPS height disagreed by up to 0.7–5.8 m on affected flights and
  0.8–1.5 m on the after-change flights; estimator vs barometer drift 3.1–13.5 m vs 2.3–3.3 m. Two to three of four
  affected flights stand out. Four vs four flights, chosen after knowing the answer: a lead, not a validated rule.

## Fixes this case motivates (not applied; the baseline above stays the record)

1. Count an event as evidence only when it is a fault, failsafe or warning, not routine mission or logger messages.
2. Read actuator units from the source; don't assume PWM.
3. Describe a tracking departure with the departure stretch, not the whole window.
4. Never ask the site for a log that is already in the case; offer the reading of it instead.
5. New read-only checks: parameters vs firmware defaults (filtered to the affected function); agreement between
   redundant measurements of one quantity.
6. A multicopter machine profile.

After fixes, re-run this case and the false-alarm check, then try a second public incident not seen during the fixes.
