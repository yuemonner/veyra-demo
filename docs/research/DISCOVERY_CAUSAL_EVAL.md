# Evidence discovery and the signal-chain layer: blind evaluation

Status: **pre-registered** (this section was written before the evaluation script was run on any flight other than the
development flight). Results are appended below the line, unedited above it.

## What is being tested

Two product mechanisms, both general (no ALFA-specific code):

1. **Evidence discovery** (`backend/app/discovery/`): given any ROS bag / MCAP / PX4 ULog and an incident onset, list every
   stream, give each a role from its standard message type, exclude label streams, and read the controller's mode,
   failsafe, configuration and message records, the operator's inputs, and output / feedback / power departures around
   the onset.
2. **Signal-chain layer** (`backend/app/engine/mechanisms.py` + `signal_chain` in each machine profile): place each anomaly
   at its measurement point, treat later downstream anomalies as consequences, and weigh six mechanisms (operator input,
   link, controller behaviour, output path, physical response, sensor error) as supported / possible / unlikely /
   cannot tell.

## Development data (disclosed)

- Built and debugged on ONE flight: `carbonZ_2018-09-11-15-06-34_2_rudder_right_failure` (the hosted demo case).
- Before the code existed, the MAVLink stream of two other flights (one elevator, one engine) was opened to count
  PARAM_VALUE messages (9 and 7). No other flight was opened. No threshold was tuned on any flight.

## Protocol (frozen)

- Data: the 47 processed ALFA sequences (bags), CMU AirLab, CC0. Label topics (`/failure_status/*`) are excluded by the
  product's label guard; the script additionally asserts that no excluded stream reaches a probe.
- Incidents: Veyra's own stored blind detections (`alfa_results_v3.json`, engine output rules v3): the first output
  finding of each flight (rule, channel, onset) and the first tracking departure. No incident is taken from labels.
  Onsets are converted to the recording clock with the same start the benchmark used (earliest record of the imported
  topics).
- Phase 1 (blind): run discovery and the signal-chain layer on every flight with an incident, write all predictions to a
  file and record its SHA-256. The script reads no truth field in phase 1 (the truth fields are stripped on load).
- Phase 2 (scoring): read the labels afterwards.

Ground truth for mechanism: in ALFA every fault is produced by the flight controller's firmware when a parameter
(DISABLE_ENGINE / ELEVATOR / RUDDER / AILERON...) is set from the ground station, active in autonomous mode only (Keipour
et al., IJRR 2021). So for every true fault the correct mechanism is **controller behaviour**; output path, physical
response and sensor error are wrong.

Metrics, on incidents whose flight has a true fault and whose Veyra onset is within the benchmark's detection window:

- M1: controller behaviour is `supported`.
- M2: no other mechanism is `supported`.
- M3: output path, physical response and sensor error are all `unlikely` (excluded by the recorded order).
- M4: operator (pilot stick) is not `supported`.
- Ablation: the same with the controller's configuration records removed (no PARAM_VALUE), to show what the order and
  inputs alone establish. In ALFA the parameter that injects the fault is echoed in-band; this makes M1 easy and is
  reported as such.
- On the false alarm and on normal flights: what the layer says (ideally nothing `supported`).
- Discovery generality: the same discovery code on the four PX4 logs of public issue #26910 (a different autopilot,
  format and vehicle type): roles found, streams not understood, controller records found. Descriptive, no labels.

Honest limits stated in advance: ALFA has one true mechanism class, so this cannot show the layer separating, say, a
servo fault from a controller fault on real data; it can show that the layer does not blame the hardware when the
recorded order says the controller output changed first, and that discovery finds controller-side records generically.

---

## Results (appended after the run)

Files: `docs/research/results/alfa_discovery_eval_v1*.json` (scores, per-flight statuses) and `*.predictions.json`
(phase-1 output; its SHA-256 is the value recorded before labels were read).

### v1, the pre-registered blind run (predictions sha256 `bf638d0abee21a18a45638017a9fdf32e9107bf078b9db6452397c537bfc90a1`)

20 incidents on fault flights detected inside the window (12 engine, 8 aileron/rudder/elevator combinations):

| Metric | With discovery | Without the controller's configuration records (ablation) |
|---|---|---|
| M1 controller behaviour `supported` | **19 / 20** | 0 / 20 (all 20 `possible`) |
| M2 no other mechanism `supported` | 20 / 20 | 20 / 20 |
| M3 output path, physical response, sensor error all `unlikely` | **20 / 20** | 20 / 20 |
| M4 pilot stick not `supported` | 20 / 20 | 20 / 20 |

- In 19 of 20, discovery found the controller reporting a `DISABLE_*` parameter 0.08 to 0.91 s before the onset (first
  report of that parameter in the recording). The 20th (2018-09-11-17-55-30_2, left aileron) has no such record within
  the 30 s window: controller stays `possible`, nothing is wrongly blamed.
- The ablation shows what the recorded ORDER alone establishes: in every case the output command changed before the
  aircraft's motion, so a servo, airframe or sensor fault is `unlikely` (it would move the aircraft first and the
  controller would react), and the pilot's inputs were steady. Without the in-band parameter echo, the controller is the
  only mechanism left open, but it is `possible`, not `supported`.
- Excluded label streams: `/failure_status/*` in every flight (26 of 26 with an incident). The script asserts no
  excluded stream reached a probe.
- Not understood (listed, not guessed): `/mavctrl/path_dev`, `/mavctrl/rpy` (generic Vector3 on non-standard topics),
  `/emergency_responder/traj_file`, `/mavros/mission/reached`.
- Gaps reported on every flight: no command-link quality records, no controller text messages (0 STATUSTEXT).

**Two defects found in v1 on the incidents outside the scored set** (6 flights: 5 missed by the output rules, where the
incident was a later tracking departure, and 1 false alarm):

1. A record AFTER the onset was allowed to support a cause. False alarm 2018-09-11-15-05-11_2 (no fault): pilot stick
   input was `supported` from a transmitter switch moved 1.63 s after the onset (a reaction, the safety pilot).
2. "Outputs held their level until the motion departed" made the controller `unlikely`. A controller that FREEZES an
   output also leaves it at its level. On elevator flight 2018-09-11-14-41-51 this hid a `DISABLE_ELEVATOR` record
   1.55 s before the onset (controller marked `unlikely`, wrongly).

### v1.1 (fix after seeing v1; NOT blind; predictions sha256 `408501f6…`)

- A record supports a cause only if it is at most 1 s after the onset (state topics are sampled at ~1 Hz); later records
  are listed as "after the onset (a reaction or consequence)".
- A steady output no longer makes upstream mechanisms `unlikely` (`possible`, with the frozen-output caveat).
- Scored metrics unchanged (19/20/20/20). The false alarm now has nothing `supported` (pilot input `unlikely`,
  controller `possible`); the elevator flight now has the controller `supported` by the parameter record.

### v1.2 (fix after the PX4 run below; NOT blind; predictions sha256 `ee6b38a7…`)

- A controller record counts only if a record like it would land in the 6 s before the onset by chance less than 20 % of
  the time (Poisson rate from how often that same field / message / parameter changes in the whole recording). Reason:
  on the PX4 logs, land-detector and estimator flags change many times per flight and were being counted.
- Sensor error now uses independent measurements of the same quantity (profile `redundant_signals`, e.g. altitude from
  barometer, GNSS and estimator): `supported` when only one moved, `unlikely` when the others moved too and departed
  first, otherwise `possible`. Never `unlikely` just because the motion was real: a wrong reading can make the controller
  move the machine.
- ALFA scored metrics unchanged.

### Discovery on a different autopilot and format: PX4 issue #26910 (4 public ULogs, VOXL2 multicopter)

Same discovery code, through the product upload path. Descriptive only (no labels; sealed issue replies not read).

- 45 streams given a role in each log (2 actuator command, 1 actuator feedback (ESC), 1 power, 3 operator input,
  4 controller mode, 8 controller status, 7 setpoints, 2 text, 1 configuration, 14 motion); 38 to 44 not understood
  (estimator internals such as `estimator_aid_src_*`, `ekf2_timestamps`) and listed as such.
- Controller-side records found near events, e.g. "Connection to ground station lost" 3.9 s before a ground-speed
  departure, "Pilot took over using sticks" 2.5 s before an altitude departure.
- The altitude events were tested for sensor error against the other altitude measurements: none showed one sensor
  moving alone; in one, the barometer moved 15 m against GNSS 5 m (reported, not yet turned into a rule).
- The generic chain was used (no multicopter profile), and the logs are not redistributed (`.local-private/`).

### What this does and does not show

- It shows a general mechanism finding controller-side evidence that was not imported (19/20, from the raw MAVLink
  stream) and a causal layer that does not blame hardware when the recorded order says the controller output changed
  first (20/20, also without the parameter records).
- In ALFA the injected fault announces itself in-band (`DISABLE_*` echo). On a real incident the equivalent would be a
  mode change, failsafe, configuration change or controller message, which the same probes read; how often real
  recordings carry such a record is not measured here.
- ALFA has one true mechanism class. Separating a servo fault from a controller fault on real data, and a sensor fault
  from real motion, needs data with those faults; none was available with a licence that allows this use.
