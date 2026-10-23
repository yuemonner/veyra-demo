# Real-data benchmark: CMU AirLab ALFA (blind)

**Data.** 47 real autonomous flights of a fixed-wing UAV (Carbon Z T-28, Pixhawk/ArduPlane, ROS/MAVROS logs) with
faults injected in flight: 23 engine failures and control-surface failures (ailerons, elevator, rudder), plus normal
flights. Keipour, Mousaei, Scherer, *ALFA: A Dataset for UAV Fault and Anomaly Detection*, IJRR 2021,
[doi:10.1184/R1/12707963](https://doi.org/10.1184/R1/12707963). License: CC0 per the dataset README (CC BY 4.0 on
the repository page). Cite the paper with any result. The data is not stored in this repository.

**Why this dataset.** It is the closest public match to remote-operated and autonomous machines with *real* failures
and a recorded ground truth: a machine that stops following its commands, logged the way a fleet platform logs it.

**Protocol.** `scripts/alfa_benchmark.py`
1. Each flight's topics are converted into one canonical Veyra import. The ground-truth topics (`failure_status-*`)
   are excluded, so the engine runs blind.
2. A case is opened (real data), the first 20 s are declared the known-good reference, the unchanged engine runs.
3. Only then is the ground truth read. A detection counts if it lands between 1 s before and 10 s after the true
   onset and names the right part (engine, aileron, elevator, rudder). Normal flights score a false alarm if anything fires.

**Check under test.** Actuator-output health (`backend/app/analysis/outputs.py`): an output that normally moves
freezes, or splits from its mirrored twin. The same check applies to valve drives on an excavator or a crane hoist.

## Results

| | v1 — fixed before any scored run | v2 — written after seeing v1 results |
|---|---|---|
| Fault flights detected in window (of 36) | 26 | 23 |
| …with the right part named | 26 / 26 | 23 / 23 |
| Missed | 0 | 12 |
| Fired too early or too late | 10 | 1 |
| False alarms on normal flights (of 11) | 5 | 1 |
| Median / max delay after true onset | 0.42 s / 0.77 s | 0.31 s / 6.7 s* |
| Held-out flights only (33 faults, 11 normal) | 23 detected, 10 early/late, 5 false alarms | 21 detected, 11 missed, 1 late, 1 false alarm |

v1 was written after looking at three development flights. All of its errors had one cause: in cruise the autopilot
holds the throttle at one value for 2–4 s, which v1 read as a freeze. v2 counts a freeze only outside the output's
reference range. That removes most false alarms but misses engine failures where the throttle was already at idle
in the reference window, and both elevator failures. *v2's 6.7 s maximum is a development flight where one aileron
froze at neutral, inside its normal range: v2 no longer flags that freeze and only catches it 6.7 s later, when the
two aileron outputs split. Because v2 was designed after seeing all 47 results, its numbers are post-hoc
and must be confirmed on new flights (ALFA's raw recordings hold further fault scenarios).

## Engine v2.2, validated (output rules v3 + command tracking), after the strict case validation

The validation of one case (`scripts/validation/validate_alfa_case.py`) showed false findings: a throttle
held at its normal maximum reported as frozen, sample-and-hold attitude frames reported as brief events,
and yaw compared without handling the ±180° wrap. This update fixes them and adds a machine baseline: the
aircraft's normal flights are loaded as **known-good runs**, and each flight is compared only with the
other flights (never with itself, never with data from the same time period). Written after seeing all
results above, so post-hoc like v2.

| Held-out flights (33 faults, 11 normal) | v2 | v2.2 validated |
|---|---|---|
| Faults found by output rules, in window | 21 | 18 |
| …plus "stopped following its commands" (tracking), in window | — | 21 in total |
| Rudder / aileron / elevator / engine found (either check) | 3 / 6 / 0 / 13 of 3 / 7 / 2 / 22 | 3 / 7 / 2 / 10 |
| Fired too early or too late | 1 | 0 (output rules); 3 tracking departures on engine flights fall outside the window |
| Normal flights with any finding | 1 | 1 (output rules); tracking 0 of 11 |
| Brief events reported inside a symptom window (all 47 flights) | not counted | 0 |
| Median / max delay, output rules | 0.31 s / 0.77 s | 0.28 s / 0.69 s |

What changed, honestly:
- **Control surfaces:** every rudder, aileron and elevator failure in the held-out set is now found. The
  two elevator failures the output rules never caught are caught by tracking (1.5 s and 9.4 s after onset).
- **Engine failures found: 13 → 10.** Three output detections were lost: in those flights the throttle sat at idle (1100 µs) for 7.5–11.3 s. Three
  normal flight starts show the same aircraft idling for 7–12 s. On this signal alone an engine cut and a
  deliberate glide look the same, so the earlier "detections" were not distinguishable evidence. Engine
  failures need an energy signal (commanded climb vs measured altitude/airspeed), which is not built yet.
- **The remaining false alarm** (throttle held 1700 µs for 8.8 s in a normal flight) stays: only one other
  normal flight reaches 1700 µs, for 0.95 s, so ten normal flights are too few to call 8.8 s normal.
- Tracking fires on 0 of 11 normal flights and 11 of 36 fault flights; on 3 engine flights it fires 22 s early
  or 11–21 s late, so it is a confirmation signal, not a timer.

## Read this before quoting a number

- ALFA injects faults in the autopilot firmware, so the recorded servo outputs carry the injected command. On a
  machine with a mechanical fault, the equivalent signal is an actuator position or feedback sensor, which a pilot
  has to provide. The physical effect (measured vs commanded attitude) is reported per flight as "motion confirms".
- 47 flights from one airframe is a small sample; none of this is a validated detection rate for another machine.
- Veyra's job in a case is to point engineers at the right part fast and show the evidence, not to replace a fault
  detection system. The engine still reports every result as an observation with a stated rule, never a root cause.

Reproduce: download the dataset, extract `processed/`, then
`python scripts/alfa_benchmark.py <processed> <output>` (standard library only; about two minutes).
Open one flight as a field case: `python scripts/alfa_load_case.py <processed> <flight folder>`.
