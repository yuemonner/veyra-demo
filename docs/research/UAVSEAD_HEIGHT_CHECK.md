# Research: height cross-check on UAV-SEAD (pre-registered, unseen data)

Status: research only; the product is unchanged. Nothing is downloaded until the selection below is reported and approved.
This tests the **height cross-check** suggested by the PX4 #26910 case (`PUBLIC_INCIDENT_BENCHMARK.md`). It is **not** a
test of the ALFA internal model: different machine, different method. The two results must not be combined.

## Data

UAV-SEAD, Hugging Face `aykutkabaoglu/uav-flight-anomaly-dataset`, CC BY 4.0 (attribution: A. Kabaoglu, S. Sariel,
doi:10.57967/hf/7772). Real PX4 multicopters, mostly indoors with motion capture. 1,396 labelled flights; 78 carry the
label `Altitude`, with time ranges in microseconds since boot, almost all on the estimator height (`Position.Z`).

What the metadata showed before any flight data was read (labels, file list, first 128 KB of each candidate log):

- GPS is in 2 of 78 altitude flights. Indoors, the height references are motion capture / vision (76), the distance
  sensor (77) and the barometer (41). A GPS comparison is not possible here.
- The estimator's height source differs by flight (`EKF2_HGT_MODE`: barometer 39, distance sensor 14, vision 26), and
  `EKF2_RNG_AID=1` lets it also fuse the distance sensor. **A source the estimator fuses is not an independent check**,
  so references are chosen per flight from the parameters stored in the log.
- Three flight-controller boards (`sys_uuid`) account for the eligible flights; one board dominates.

## Selection (metadata only, deterministic)

Script: `scripts/research/uavsead_select.js`, run in a browser tab on huggingface.co. Records the SHA-256 of
`mapping.json`. Rules:

- Anomaly flights: only the `Altitude` label, every labelled range on `Position.Z`, readable header, estimator height
  present, at least one independent height reference, file ≤ 30 MB. Ordered by SHA-256("uavsead-select-v1|" + name);
  the first 16, at most 2 per flying day (consecutive flights of one day are not independent).
- One matched normal flight per anomaly flight: label `Normal` only, same board, same height-fusion settings, same
  independent references, same day when possible.
- 12 calibration normal flights, disjoint from the test set, same configurations: used only to set thresholds.

## The check (frozen before any flight data is read)

Per flight, airborne time only (`vehicle_land_detected.landed` false), excluding 5 s after takeoff and 2 s before landing.

- Estimator height: `h_e = −vehicle_local_position.z`.
- References, only those independent for that flight:
  - vision: `−vehicle_vision_position.z` (or the odometry / mocap topic with the same meaning);
  - distance sensor: `current_distance × cos(roll) × cos(pitch)` (attitude from `vehicle_attitude`), only while inside
    the sensor's `min_distance`–`max_distance`; assumes a flat floor (indoor flights);
  - barometer: `vehicle_air_data.baro_alt_meter`, else `sensor_baro.altitude`.
- References are resampled to the estimator's timestamps (nearest sample within 0.2 s; otherwise "no reference").
- Disagreement: `e(t) = h_e(t) − h_ref(t)`, minus its median over the preceding 30 s (removes fixed offsets between
  frames and slow barometer drift; units are metres for all three). Smoothed with a 1 s rolling median.
- Threshold per reference type: `max(floor, 1.1 × the largest smoothed |e| on any calibration flight)`; floors 0.25 m
  vision, 0.30 m distance sensor, 1.0 m barometer. Fixed after calibration, before any test flight is processed.
- Alarm: smoothed |e| above threshold for at least 1 s against any independent reference. Report which reference.
- Coverage label, never used to hide an alarm: *no independent reference* when no reference sample is valid; the
  share of airborne time with a valid reference is reported per flight.

## Scoring (fixed now)

- Anomaly flight detected: an alarm starts within a labelled range widened by 2 s before and 5 s after.
  Delay = alarm start − range start.
- Alarm outside every widened range on an anomaly flight: *unlabelled alarm*, reported separately (labels can be
  incomplete; not counted as correct or as a false alarm).
- Normal flight: any alarm is a false alarm. Reported per flight and per hour of airborne time.
- Baseline: the existing Veyra checks, unchanged, on the same flights (ULog reader as in the PX4 benchmark).
- Report every selected flight, including those that could not be processed, with the reason.

## Known limits, stated in advance

- The annotators judged "altitude" anomalies by comparing the estimate with what the vehicle physically did, likely
  using the same kind of references. Agreement with the labels is therefore partly expected; it tests whether Veyra
  finds what an expert finds, not a different ground truth.
- 16 + 16 flights from a few boards and days: a small sample; one dominant board.
- Labels are *probable* expert judgements, not injected faults.
- Indoor distance-sensor heights assume a flat floor; obstacles under the vehicle will look like disagreements.
