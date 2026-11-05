# Electrical inference: blind validation, preregistered

Written 2026-11-05, before the generator or the v2 engine was run on any case.

## What is tested

`app/physics/electrical.py` (v2) on synthetic CAN logs from an independent generator (`generate.py`), which shares no
code with the engine or with the demo-case generator (`scripts/demo/make_pix_shuttle_case.py`). It uses richer physics
than the engine assumes: constant-power wheel loads (DC current set by torque, speed and each inverter's losses), a
battery with ohmic and polarisation resistance (R0, R1, tau) that depend on temperature and state of charge, auxiliary
load, per-sensor offset, gain, noise and quantisation, and per-ECU sampling phase (timing skew).

Ground truth is written to a separate directory. The engine runs in a separate process that is given only the log
file. Scoring reads the truth after the engine has finished.

Limitation: the engine and the generator were written by the same author (Claude), so "blind" means the engine cannot
see the parameters, not that its assumptions were made independently of the generator's. Real validation needs vehicle data.

## Scenarios (each with randomised parameters)

| id | what is true |
|---|---|
| healthy_even | No fault. Branch resistance 6–10 mΩ, healthy inverter loss spread ±5%, even torque |
| healthy_uneven | No fault. One wheel carries 10–30% more torque than the others (uneven load) |
| connector | One branch has 4–25 mΩ extra contact resistance |
| connector_intermittent | One branch's contact adds 8–25 mΩ only under load (reads normal at rest) |
| conductor | One branch's cable or fuse has 6–25 mΩ extra |
| inverter_loss | One inverter/motor draws 1.5–2.2× the DC current at the same torque; branch resistance normal |
| sensor_offset | One inverter reports DC voltage with a 0.4–1.5 V offset |
| sensor_gain | One inverter reports DC voltage with a 2–5% gain error |
| cold_battery | Snapshots at −5 to 25 °C; half also differ in state of charge by ≥ 15 points |
| combined | Connector on one wheel and inverter loss on another, or connector plus offset plus a cold battery |
| missing_* | The same logs with pack current, all but one inverter voltage, torque, or temperature removed |
| low_load | The vehicle only creeps (pack current under 20 A) |

Splits: dev seeds 0–19 per scenario (used to set engine thresholds); held-out seeds 1000–1029 per scenario (run once,
after the engine is frozen, and reported as they come out).

## Pass criteria (held-out)

1. Fabrication: no resistance or ratio estimate is reported for a relation marked not identifiable or not applicable. Required: 0 cases.
2. False positives: a branch anomaly flagged in healthy_even, healthy_uneven, sensor_offset or cold_battery. Required: ≤ 5% of cases.
3. Missed faults: connector or conductor with ≥ 8 mΩ extra, and inverter_loss, not flagged. Required: ≤ 10%. Smaller connector faults (4–8 mΩ) are reported, with no criterion.
4. Attribution: when a branch anomaly is flagged, the true mechanism is among the candidates the engine keeps open. Required: ≥ 95%. The engine never calls a branch resistance measured or identified.
5. Numerical accuracy: measured drop per amp of pack current (k) within 10% of the true value (median). The true branch resistance lies inside the engine's plausible-share interval in ≥ 90% of connector/conductor cases.
6. Battery: when compared snapshots differ in state of charge by ≥ 10 points, the confound is flagged in 100% of cases; the apparent resistance lies between R0 and R0 + R1 (±15%) in ≥ 90% of snapshots.
7. Sensor: offsets ≥ 0.5 V are flagged in ≥ 90% of cases.
8. Next measurement: for flagged branch anomalies, the recommended measurement, simulated on the true parameters, keeps the true mechanism and eliminates at least one wrong candidate in ≥ 90% of cases. Cases where its result would mislead (e.g. an intermittent contact reading normal at rest) are counted and reported.

A failed criterion is reported as failed. Thresholds are not changed after the held-out run.
