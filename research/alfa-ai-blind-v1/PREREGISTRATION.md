# ALFA blind evaluation of the AI investigator on recordings — v1 (preregistered)

Written and committed **before any scored run**. Nothing below changes after the first run; anything added later is
marked post-hoc with its date.

## What is evaluated

The AI investigator on structured recordings (P1), exactly as at commit **e9bc6ce**:
`backend/app/agent/recording_tools.py` (evidence sheet, tools, anomaly ranking, thresholds), `backend/app/agent/investigator.py`
(prompts, verifier, MAX_TURNS 14), `backend/app/ingest/bags.py` (`suggest`: the mapping a user gets on upload). The run
script refuses to start if any of these files differs from e9bc6ce.

Model: Claude Code CLI (`VEYRA_AGENT_BACKEND=claude-code`, alias `sonnet`). The provider-side model behind an alias is not
under our control and cannot be frozen; the CLI version and the model id each call reports are recorded per case.

## Data

CMU AirLab ALFA, processed flights (Keipour, Mousaei, Scherer, IJRR 2021, doi:10.1184/R1/12707963, CC0): all 47 `.bag`
files. Each flight is uploaded under a neutral alias (`flight-NN.bag`, machine `UAV-NN`, neutral title and request: *"Check
this flight: did a control fault occur, when, and where on the aircraft?"*), so neither the file name nor the title can
carry the label. The mapping is Veyra's suggested mapping; label topics (`/failure_status/*`) are never mapped (label guard).

Labels are read from `/failure_status/{aileron,rudder,elevator,engines}` **after** each investigation, by the scorer only.
Onset = first message with a non-zero value on any label topic, in the evidence sheet's seconds. Fault window =
[onset, end of recording]. Failed components = the label topics that become non-zero.

`carbonZ_2018-07-18-12-10-11_no_ground_truth` has no label: it is run and reported, not scored.

### Prior exposure (disclosed)

No ALFA flight is untouched by this project. All 47 were scored earlier by the rule-engine benchmark
(`scripts/alfa_benchmark.py`, whose results the developers saw), and the 10 no-failure flights plus
`2018-09-11-15-06-34_2_rudder_right_failure` are in the hosted demo seed. The AI evidence tools were developed while looking
at one ALFA flight, `2018-10-05-14-37-22_3_left_aileron_failure` (and one PX4 log outside ALFA). Reported as such.

## Measures (per flight, from the stored outputs only)

Axis of each component, used to decide whether a sheet line or a tool call concerns the failed part:

| Component | Signals (sheet names) | Words that name it in the result |
|---|---|---|
| aileron | `vehicle.attitude[roll]`, `control.attitude_setpoint[roll]` | aileron, roll |
| rudder | `vehicle.attitude[yaw]`, `control.attitude_setpoint[yaw]`, `vehicle.yaw_rate` | rudder, yaw |
| elevator | `vehicle.attitude[pitch]`, `control.attitude_setpoint[pitch]` | elevator, pitch |
| engine | `propulsion.throttle`, `vehicle.altitude`, `vehicle.velocity`, `vehicle.airspeed` | engine, motor, propulsion, thrust, throttle, power |

A sheet line **concerns the fault** when it names a signal of a failed component and a time `t=` (or `from t=`) at or after
onset − 1 s.

Faulty flights:
- **G · evidence generated**: the evidence sheet has at least one line (CHANGE, TRACKING, GAP, CONSTANT or EVENT) that concerns the fault.
- **R · anomaly ranked**: at least one ANOMALY line concerns the fault.
- **T · tool selection / window coverage**: coverage = the share of the fault window covered by the union of the time
  ranges of the agent's window, changes, relation, sequence and events calls (a call without start/end covers the whole
  recording). T passes when at least one such call names a signal of a failed component (or names no series: sequence /
  events over all signals) and overlaps the fault window by at least 1 s.
- **Evidence recall**: the result's evidence cites at least one sheet line that concerns the fault.
- **D · diagnosis**: the headline, explanation, or a *leading* hypothesis's area/claim contains a word that names a failed
  component, and status is not `no_failure_found`. Reported twice: **D-strict** with the component word only (aileron,
  rudder, elevator, engine|motor|propulsion|thrust), **D-axis** also accepting the axis word (roll, yaw, pitch, throttle, power).
- **Failure stage** for a flight that fails D-axis: evidence generation if not G; else tool selection if not T (noting
  whether R also failed); else reasoning.

Healthy flights (no_failure):
- **FP-strict**: status `cause_probable`.
- **FP-flag**: a *leading* hypothesis whose area or claim contains any component word above (aileron, rudder, elevator,
  engine, motor, propulsion, thrust, servo, actuator, sensor). Reported as a flag rate, not a judgement.

Every flight: tool calls, model calls, input/output/cache tokens, cost when reported, wall time, verifier drops.

## Infrastructure failures

A run that falls back to rules (`mode != agent`: CLI error, usage limit, timeout) is an infrastructure failure, not a
result. It is rerun up to two times; what still fails is reported as such and excluded from the rates, with its count.

## After the baseline: split for any change

Development set (changes may be designed on it): every flight with prior exposure above, plus flights whose
`sha256(folder name)` begins with a hex digit 0-7. Validation set (no change may be designed on it): the rest. The split
is computed by `split.py` and committed with this file (`split.tsv`: 26 development, 21 validation). All ten healthy
flights fall in development (they have prior exposure), so false positives can be measured only there; a fresh healthy
set is needed to validate them. A change is then re-run on both; validation results are reported
separately and every change is listed as post-hoc.

## Outputs

`results/` (one JSON per flight: alias, sha256, mapping, sheet + sha, steps with inputs and outputs, result, usage, labels,
scores), `baseline.json`, `baseline.md`. Nothing in `results/` is edited after the run.
