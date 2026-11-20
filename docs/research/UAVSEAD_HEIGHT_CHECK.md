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

### Selection result (run 2026-10-23; `backend/research/uavsead/selection_v1.json`)

`mapping.json` SHA-256 `afb1e2ab…800a`. Of 78 altitude-labelled flights, 63 are eligible; 15 excluded: 5 with several
labels, 1 label not on the height estimate, 1 file missing, 3 without the estimator height topic, 4 with no independent
reference (vision is the fused height source and the distance sensor is also fused), 1 over 30 MB.

| Set | Flights | Size | Independent references |
|---|---|---|---|
| Test, altitude anomaly | 16 (14 flying days; 27 labelled ranges, 171 s) | 120.7 MB | vision+baro 4, vision+range 4, vision 3, range+baro 3, range 2 |
| Test, matched normal | 16 (all matched) | 195.0 MB | same as their anomaly flights |
| Calibration, normal | 12 | 139.9 MB | all five configurations |
| **Total to download** | **44** | **455.6 MB** | |

11 of 16 anomaly flights come from one board; the result will be reported per board.

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

## Result (run once, 2026-10-23) — the check fails; stopped, no tuning

Download: 44 of 44 files, every size equal to the size recorded at selection; SHA-256 of each in
`datasets/uav-sead/download.txt` (local). Source: Hugging Face `aykutkabaoglu/uav-flight-anomaly-dataset`, single
upload commit `9bd0363`, licence CC BY 4.0 on the dataset card. Results: `datasets/uav-sead/results_v1.json`,
`results_v1_1.json` (local, not in the repository).

**Bug found after the first run.** v1 read the attitude quaternion under the wrong field name, so every distance-sensor
sample was discarded (coverage 0 on all flights). v1.1 fixes only that line (commit 8221828). Rule, thresholds procedure
and selection are unchanged. Both runs are reported; v1.1 is the result.

Thresholds from the 12 calibration flights: vision 3.55 m, distance sensor 0.54 m, barometer 1.0 m (the floor).

| Test (16 anomaly, 16 matched normal) | v1 (as first run) | **v1.1** |
|---|---|---|
| Anomaly flights detected | 3 / 16 | **5 / 16** |
| Labelled ranges hit | 4 / 27 | 8 / 27 |
| Normal flights with a false alarm | 2 / 16 | **3 / 16** |
| False alarms per airborne hour (0.71 h of normal flight) | 2.8 | 5.6 |
| Delay from range start (detected) | −1.0, 3.8, 3.8 s | −1.0, 1.9, 1.9, 3.8, 19.4 s |

By board (anomaly detected / normal false alarm): 3832: 3/11 and 2/11; 3833: 1/2 and 1/2; 0028: 1/3 and 0/3.
By reference: vision only 0/3; vision+range 1/4; vision+baro 2/4 with 2 false alarms on 4 normals; range 1/2;
range+baro 1/3.

**An alarm is nearly as likely on a normal flight (3/16) as on an anomaly flight (5/16).** This is not an evidence check
that can go into the product.

Why (diagnosis after the result; nothing below was used to change the run):

- **The "independent" vision reference is not independent.** On the two 2019-03-04 anomaly flights the estimator and
  motion capture agree within 3–6 cm through the labelled anomalies, although the height source is set to the barometer.
  The vision position is evidently fused for height too. The reviewer's warning ("check whether other references are
  correlated through the fusion chain") was right; reading `EKF2_HGT_MODE` alone was not enough.
- **The labels may describe what the vehicle did, not an estimation error.** If the drone really dipped and every sensor
  agrees, a consistency check is silent by design. "Altitude anomaly" in UAV-SEAD and "estimate disagrees with an
  independent sensor" are different questions.
- **One calibration flight set the vision threshold** (3.2 m on 2018-10-01/07_36_14), so vision could not alarm.
- **Indoor barometers are noisy** (propeller wash, doors): all baro false alarms are on vision+baro flights.

What holds: the check found the 2021-10-06 anomaly through both the distance sensor and the barometer, 1.9 s after the
labelled start, and the PX4 #26910 lead (estimator vs barometer) is not contradicted. But that is a few cases, not a
validated rule.

**Decision: stop here.** No re-tuning on these flights. The height cross-check stays a research note. Not integrated.
The ALFA internal-model results are separate and unaffected.
