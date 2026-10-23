# Cross-machine check: from a fixed-wing UAV (ALFA) to a walking robot (ANYmal D)

Started 2026-10-21 09:32 (Lisbon). First complete run of both cases at 09:55. **Elapsed: about 23 minutes.**
About 4 of those minutes were spent downloading and converting the data. One engineer plus Claude did the work,
and nobody on the team had worked with ANYmal or its data format before.

This is a check of portability, not of accuracy. The GrandTour dataset has no fault labels. Case B uses a
synthetic fault, labelled as such, injected into one joint of a real mission. One injected fault is not a
detection rate.

## Data

- **Dataset:** GrandTour, ETH Zurich Robotic Systems Lab, ANYmal D, mission 2024-10-01-11-29-55 (349 s).
  MIT licence per the dataset card. Four topics were downloaded (315 MB of Zarr files): joint actuators
  (400 Hz), operator twist command, leg odometry, and the state estimator.
- **Export** (`scripts/grandtour/export_anymal.py`, run where the Zarr files are): 309,090 rows.
  - Joints are decimated from 400 Hz to 20 Hz.
  - Drive status words and command modes are exported as changes only.
  - Full-rate statistics are written to an inventory file, so nothing that the decimation hides is counted.
- **Load** (`scripts/grandtour/anymal_load_cases.py`): sets the import profile and the machine context
  (`machine_class = legged_robot`) and builds the two cases. Each case is three files: front legs, hind legs,
  body. Each file is under the 25 MB import limit.

## What changed, by kind

| Kind | Files | Lines (+/−) |
|---|---|---|
| Configuration: machine profile | `machines/profiles/legged_robot.json` | +87 |
| Data mapping: export and import profile (data owner side) | `scripts/grandtour/export_anymal.py`, `scripts/grandtour/anymal_load_cases.py` | +314 |
| **Core engine code** | `analysis/tracking.py` (128/83), `ingest/catalog.py` (14/2), `engine/investigate.py` (5/3), `engine/checks.py` (3/1), `analysis/screening.py` (1/1) | **+151 / −90** |
| UI | `frontend/app/field-case/page.tsx` | +5 / −4 |
| Tests | `tests/test_legged_robot.py` (5 new), `tests/test_runtime_imports.py` | +55 / −1 |

What the core changes were, and why configuration could not do them:

1. **Signal catalog v5** (additive). Eight new signals:
   - joint position command, joint position and joint torque;
   - drive status;
   - commanded and measured body velocity;
   - commanded and measured turn rate.

   The catalog had no notion of a joint. The three commanded signals are marked as commands, so the engine
   never reads "the command differed" as a fault.
2. **Command tracking v2.** Version 1 compared aircraft attitude only. Version 2 works from a table of
   commanded/measured pairs: attitude, joint position, body velocity and turn rate. The attitude rule is
   unchanged, and its constants and outputs are identical. The ALFA tests still pass.

   Joints are judged against the 95th-percentile normal error, not the median. On ANYmal the knees lag their
   targets by about 31° on average while walking (median, reference walk). The compliant position control is
   the reason, and that lag is normal.
3. **Routing.** A tracking departure is now filed under the role of the signal that failed to follow: a joint
   lands under joint drives, and body attitude still lands under the body. Before, it always went to the body.
4. **Next Checks.** A tracking departure on the machine side now triggers the on-site actuator check, worded by
   the profile: inspect the named joint drive, then the leg.
5. **Screening.** Drive status changes count as events. The heading (yaw) is not reported as "higher than
   reference", because heading is the mission, not a fault.

**No change** was needed in the ledger, the store, ingest, the triage rules, the agent or the hosting.

## Two things only real data showed

- **Commands that are not applied.** An ANYmal drive applies a position target only in command mode 13. In mode
  1 (freeze, robot lying down) the target and the knee angle differ by about 150°. Taken naively, every joint
  would "fail to follow". The loader drops the 11,496 target rows recorded in mode 1. This is data-mapping
  knowledge, not an engine change.
- **Lag direction.** The first injection moved the knee *against* its normal lag. That made the error smaller,
  and tracking (which uses the error's size) correctly said nothing. The comparison with the reference window
  still showed the knee angle shifted. The injection now adds to the normal lag. A drive fault that reduces the
  error, though, would be missed by tracking. Signed error per joint is a future rule.

## Results

**Case B: synthetic fault, left front knee (LF_KFE), injected at 200 s.** The knee sags up to 25° further past
its target, and its torque drops to 30 %.
- Tracking found a departure on **LF_KFE only**, starting 2.6 s after the injection. The rolling median error
  was 1.1× the 95th-percentile normal error. The other 11 joints, body velocity and turn rate tracked normally.
- The evidence is filed under **Joint drives (actuators)**. The triage owner is a joint review: Actuators & drives,
  and Mechanics & field operations. Communication and the locomotion controller have no evidence.
- The Next Checks are in this robot's terms. The on-site check reads "Inspect the joint drive named in the
  evidence: drive error register, temperature, encoder and cabling, then the leg it moves". Its trigger is
  "Stopped following its commands: LF_KFE". The probe-path check (network RTT) is switched off for this machine.
- The torque drop to 30 % was **not** flagged. The median torque moved from 14 to 8 N·m, which is still inside the
  reference p05–p95 range. Body motion was not re-simulated, so body signals cannot show the fault's
  consequences.

**Case A: the real mission, no reported fault.** Everything after the reference walk was searched.
- Tracking: **no departure** on any of the 12 joints, body velocity or turn rate. This is the correct answer.
- **False alarms the engine still raises, as recorded:**
  - The robot sitting down at the end of the mission produces 60 mode-change events and 60 drive status
    changes. The rule that marks events near the symptom window as anomalies reports them under the locomotion
    controller and the joint drives. Routine events need either a known-good baseline or a "routine sequence"
    list in the profile.
  - "Body velocity (x) lower than reference": 0.67 vs 0.91 m/s. The operator commanded a slower walk, and
    tracking shows the robot followed. The reference-comparison rule does not yet discount a measured value
    whose command changed.
  - Brief events on pitch, roll and some joint angles: steps and terrain on a walking robot.

## Limits

- One mission, with no other mission as a known-good run. The baseline is the declared reference walk
  (80–140 s) inside the same mission.
- The tracking rule was set before it was run on this data, and one injected fault was tested. This is not a
  detection rate. GrandTour has no fault labels. For accuracy, a labelled set such as ForEnt (Unitree Go2
  entrapments) is the next step.
- The investigation agent was not run on these cases. The deterministic engine was.

## Follow-up: labelled data (ForEnt, Unitree Go2)

The same profile and rules were run unchanged on a second legged robot with real, labelled entrapments. The robot
was found in 3 of 18 entrapments, with 0 false alarms. Short (3-6 s) entrapments are below the sustained-departure
rule's resolution. See `docs/FORENT_BENCHMARK.md`.
