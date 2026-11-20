# ForEnt benchmark: real, labelled entrapments of a Unitree Go2

Run on 2026-10-21, 10:05 to 11:09 (Lisbon). About 45 minutes of that was the download. **No engine code changed.**
The run reused the `legged_robot` profile written for ANYmal D and tracking rules v2 as they were. The only new code
is the converter and scorer, `scripts/forent/forent_benchmark.py`.

## Data

- **Dataset:** ForEnt, doi:10.5281/zenodo.18824718, licence CC BY 4.0. A Unitree Go2 walks in forests and gets
  caught on obstacles. Each "entrapment" is labelled with a start, an end, a type and a severity (1 to 3).
- **Public release:** only sequences 1 and 2 of the paper's 11 are on Zenodo (616 s and 399 s). That is 18 labelled
  entrapments, not the paper's 69:
  - 11 rigid swing obstructions;
  - 4 entanglements;
  - 2 suspensions (the body stuck on something);
  - 1 soft soil.

  Seventeen are severity 1 (the operator recovered it by joystick). One is severity 3 (system failure).
- **Missing from the processed CSVs:** the operator's joystick and the joint targets. Both were taken from the raw
  ROS 2 recordings (16.6 GB, downloaded one at a time and deleted after extraction).

## Protocol (fixed before scoring)

1. Signals mapped to Veyra's catalog:
   - joint position and torque come from `/lowstate`;
   - joint targets come from `/lowcmd`, kept only where kp > 0, i.e. the target was applied;
   - body velocity and turn rate come from the robot odometry;
   - attitude comes from the body IMU;
   - the commanded velocity comes from the operator's joystick.
2. The joystick reports stick deflection, not speed. Its gain to m/s was fitted on the known-good walk only:
   - forward: 0.837 m/s per full deflection;
   - lateral: -0.427;
   - yaw: -0.844 rad/s.
3. **Known-good walk:** the first 60 s after the operator first moves the stick in sequence 1 (25-85 s). No label
   overlaps it. It is the robot's baseline for both sequences.
4. **Blind scan:** the tracking rules run continuously. Labels are read only after the scan. Departures within 10 s
   of each other count as one alarm episode.
5. **Scoring:**
   - A label counts as found if an episode starts between 2 s before its start and 2 s after its end.
   - An episode that matches no label is a false alarm.

## Result

| | Count |
|---|---|
| Labelled entrapments (scored) | 18 |
| **Found** | **3** (17 %) |
| **False alarm episodes** | **0** (in 531 s of seq 1 after the known-good walk, plus all 399 s of seq 2) |

What was found:
- **Seq 1, suspension, 256-278 s (22 s long).** Body velocity x departed 18.4 s after the label start, with an error
  5.8× normal.
- **Seq 1, suspension, 509-547 s (38 s).** Found 13.4 s after onset. Five joints (front right hip, thigh and calf;
  both hind thighs) and body velocity x stopped following their commands.
- **Seq 2, entanglement, 224-228 s (4 s).** Found 0.75 s after onset, on the rear-right thigh and body velocity x.

What was missed, and why:
- **The rules are built for sustained departures.** A departure needs a 3 s rolling median and at least 2 s of
  confirmation. Most labels are swing obstructions: a leg hits a branch, and the robot or operator recovers it within
  3-6 s. Those are below this rule's time resolution by design.
- **The one severity-3 failure (seq 2, 390-393 s) was missed.** The robot fell short of the command for about 2 s,
  then the operator released the stick and the recording ends 3 s later. The sustained rule had no time to confirm,
  and with no command there is nothing left to track.

## What this says

- The cross-machine set-up carried over unchanged: same profile, same rules, a second robot from a different maker.
  It runs on real labelled data with zero false alarms.
- **Detection is low (3/18)** because Veyra's only legged-robot rule today looks for sustained departures. Brief
  entrapments need their own rule, for example:
  - short drops of body velocity while the command holds;
  - stance legs that keep pushing (foot force, joint torque) while the body does not move.

  Such a rule must be developed on one sequence and tested on the other, or on the remaining 9 sequences once the
  authors release them. Tuning on these same 18 labels would not be a measurement.
- These numbers come from 2 sequences, 18 events and one robot. They are not a general detection rate.
