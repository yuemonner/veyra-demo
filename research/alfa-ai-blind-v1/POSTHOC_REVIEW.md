# ALFA blind evaluation v1 — post-hoc review (NOT preregistered, NOT blind)

Written 2026-11-09 after reading every result and its label. The preregistered numbers in baseline.md stand as they are;
this file adds what they miss. It is the developer's reading, with the labels known: treat it as an analysis, not a score.

## What the preregistered measures got wrong

- **T (tool on the fault window) does not discriminate: 36/36, coverage 1.0.** A changes / relation / events call without a
  time range counts as covering the whole recording, and almost every run makes one. A targeted-call measure is below.
- **G and D miss the actuator outputs.** The rubric lists attitude, setpoint and throttle signals, not `actuator.output`
  channels, because the channel-to-surface mapping is not in the data. The agent found three rudder faults and one aileron
  fault exactly there (ch[3] is the rudder output, ch[4]/ch[5] the ailerons, ch[2] the throttle, as the flights themselves show).
- **D-axis over-counts.** It passes on a passing mention of roll / pitch (flight-34 blames pitch; flight-35 says no fault).

## Reading of each faulty flight

Found = the result reports the fault event within a few seconds of onset AND leads with or names the failed part or its
output channel. Event only = the event is reported at the right time, but dismissed or blamed on something else.

**All 36: 12 found · 5 event only · 19 missed (of 36).**  Development: 7 found · 1 event only · 7 missed (of 15).  Validation: 5 found · 4 event only · 12 missed (of 21).

| Component | Result |
|---|---|
| engine | 6 found · 3 event only · 14 missed (of 23) |
| aileron | 3 found · 1 event only · 4 missed (of 8) |
| rudder | 3 found · 1 event only · 0 missed (of 4) |
| elevator | 1 found · 0 event only · 1 missed (of 2) |

| Flight | Split | Fault | Onset s | Reading | What the result said |
|---|---|---|---|---|---|
| flight-02 | validation | engine | 116.268 | missed | no_failure_found; throttle cut at 116 s not mentioned |
| flight-03 | development | engine | 73.411 | missed | mentions a throttle cut only at start-up, not at 73 s |
| flight-04 | validation | engine | 116.625 | event found, misattributed or dismissed | throttle to 0% at 115.6 s found, judged commanded, no fault called |
| flight-06 | development | engine | 114.064 | missed | no_failure_found |
| flight-07 | validation | engine | 123.149 | missed | no fault shown |
| flight-08 | development | engine | 116.791 | missed | blames a sensor-health error at 1.5 s |
| flight-09 | validation | engine | 91.598 | missed | blames the pitch / elevator path |
| flight-11 | validation | engine | 117.172 | event found, misattributed or dismissed | throttle drop to zero found, no fault called |
| flight-12 | development | engine | 87.681 | missed | pitch sign mismatch and start-up warnings |
| flight-13 | validation | engine | 133.43 | missed | setpoint swings and estimate jumps |
| flight-14 | development | engine | 90.36 | missed | no_failure_found |
| flight-15 | validation | engine | 103.573 | missed | blames the pitch path |
| flight-17 | validation | engine | 104.854 | missed | convention / lag |
| flight-18 | validation | engine | 49.89 | missed | lag from t=0, start-up warnings |
| flight-20 | validation | elevator | 117.883 | found | pitch did not follow its setpoint, pitch actuator ch2 leading (6 s after onset) |
| flight-21 | development | aileron | 105.251 | missed | pitch polarity, dead airspeed |
| flight-22 | validation | elevator | 63.476 | missed | roll feedback delay |
| flight-24 | validation | rudder | 55.515 | event found, misattributed or dismissed | ch[3] (rudder output) jumped to 1900 us at 55.24 s, but pitch polarity leading |
| flight-25 | development | rudder | 51.974 | found | ch[3] (rudder output) stuck at 1900 us from 52.14 s, ch[3] actuator leading |
| flight-26 | development | rudder | 60.115 | found | ch[3] (rudder output) stuck at 1100 us from 60.08 s, ch[3] actuator leading |
| flight-27 | development | aileron,rudder | 116.321 | found | roll lagged from about 125 s (9 s after onset), roll actuation path leading |
| flight-28 | validation | aileron | 65.771 | found | roll degraded, aileron outputs ch4/ch5 froze at 65.92 s (onset 65.77); guidance leading |
| flight-29 | validation | aileron | 111.922 | missed | no control-loop failure |
| flight-30 | validation | aileron | 49.98 | found | aileron output ch[4] froze at 49.7 s while its twin kept moving (onset 49.98) |
| flight-32 | development | aileron | 152.199 | event found, misattributed or dismissed | pitch / airspeed at 153 s: right time, wrong component |
| flight-34 | validation | aileron | 73.379 | missed | pitch at start-up, estimate delay |
| flight-35 | development | aileron | 72.401 | missed | no attitude-control fault |
| flight-38 | validation | engine | 49.133 | missed | roll / yaw tracking |
| flight-39 | validation | engine | 100.133 | missed | heading estimate from 38 s |
| flight-40 | development | engine | 76.195 | found | throttle 45 -> 0% at 76.07 s (onset 76.20), then altitude loss; propulsion leading |
| flight-41 | development | engine | 104.246 | found | throttle to 0 at ~104 s (onset 104.25), altitude loss |
| flight-42 | development | engine | 111.165 | found | throttle to 0% and ch[2] to 1100 us (onset 111.17), descent |
| flight-43 | validation | engine | 100.36 | event found, misattributed or dismissed | throttle held at 0 from ~100 s noted; sensor health leading |
| flight-44 | validation | engine | 98.274 | found | pitch lost at 99.57 s with throttle near 0; propulsion / low authority leading |
| flight-45 | development | engine | 101.302 | found | throttle to 0 at 101.25 s (onset 101.30), altitude loss |
| flight-46 | validation | engine | 102.517 | found | throttle to 0% at 102.17 s (onset 102.52), 30 m lost |

## Healthy flights

No healthy flight was called a probable cause (FP-strict 0/10). Two of ten led with a component (flight-10, flight-23). But most
healthy results still describe start-up warnings and 'tracking errors' as findings: on this data the agent rarely says
*nothing is wrong*, it says *no fault is established* (status `no_failure_found` on 1 of 10).

## Why the misses happen (failure modes, from the 19 misses and 5 event-only)

1. **Evidence generation, frame convention (product bug).** MAVROS `nav_info/pitch` (commanded) and the odometry attitude use
   different pitch sign conventions, so the sheet reports pitch 'inverted against its setpoint' on most flights. The agent chased
   it on at least 10 flights (09, 12, 15, 17, 21, 24, 32, 34, 43, 46). This comes from the suggested mapping added in P1.
2. **Reasoning, the 1 s lag prior.** ALFA's attitude telemetry lags its setpoint by about 1 s on every flight. The agent finds
   that lag (correlation 0.97 at ~1 s) and then explains every divergence with it, without comparing before and after a time.
3. **Evidence generation, start-up transients.** Each flight starts with a 'Sensor helth' error and a low-voltage warning
   at t<2 s; the first seconds also hold the largest tracking errors. They pull the investigation to t=0 (08, 18, 34, 43).
4. **Ranking.** Anomaly candidates favour manoeuvre-sized steps; a sustained change after one time is not ranked unless
   two consecutive stretches double the error (R 20/36).
5. **Ground truth itself.** ALFA injects an engine failure by commanding the throttle to zero in the autopilot. From the data
   alone that is a commanded throttle cut; calling it 'commanded, cause unknown' (04, 11, 43) is defensible, not wrong.

## Cost (per flight, median)

11 tool calls, 5 model calls, 69 s, ~328k input tokens (almost all cache reads), ~10k output tokens. All 47 runs together:
US$14.48 as reported by the Claude Code CLI (2.1.319, model alias sonnet).

## Next (development set only, every change disclosed)

Fix the pitch convention in the MAVROS mapping; add a before/after comparison tool (a 'what changed at time T' check) and an
onset-oriented candidate; name actuator channels by what they follow (a channel that tracks the roll setpoint is a roll
output); down-weight the first seconds. Design on the 26 development flights, then re-run all 47 and report the 21
validation flights separately, with this baseline next to it.
