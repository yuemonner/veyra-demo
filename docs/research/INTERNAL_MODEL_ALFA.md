# Research: internal-model residuals on ALFA (pre-registered, one shot)

Status: research only; the product is unchanged. Script: `scripts/research/alfa_internal_model.py`. The design is in
the script's docstring and was committed (6541c40, 2026-10-23 13:18) before the first scored run. Data: CMU AirLab
ALFA, local copy, not in the repository.

## Idea

Instead of comparing a window with an earlier window, ask: *given what the autopilot is trying to do, is it doing
what it normally does?* A linear model of the autopilot's output law (throttle, elevator, ailerons, rudder from the
controller's own errors and targets) is learned **only from the normal flights**. A fault shows up as outputs the
model can't explain. In control engineering this is model-based fault detection; in neuroscience, the internal-model
literature (e.g. McNamee & Wolpert 2019). Fault labels are used only for scoring.

Honest false-alarm test: each normal flight is scored with a model and thresholds fitted on the *other* normal flights.

## Result (held-out flights: 33 with faults, 11 normal; same scoring as `docs/ALFA_BENCHMARK.md`)

| | Engine v2.2 (output rules; + tracking) | Internal model |
|---|---|---|
| Detected in window, right part | 18 (21 with tracking) | 20 (21 in window, 1 wrong part) |
| Engine failures (22) | 10 | **15** |
| Aileron (7) / elevator (2) / rudder (3) | 6–7 / 0–2 / 3 | **3 / 1 / 2** |
| Fired too early or too late | 0 | **8** (7 early, all throttle, on engine flights) |
| False alarms on normal flights (11) | 1 | 1 |
| Median delay | 0.28 s | 1.2 s |

Flight by flight (held-out faults, counting tracking for v2.2): both catch 14, only the internal model 6 (all engine
failures), only v2.2 7 (5 aileron, 1 elevator, 1 engine), neither 6. Either one would cover 27 of 33, but that
combination is post-hoc and ignores the internal model's 7 early alarms.

## What it means

- **It partially closes a known gap**: engine failures, where an idle throttle looks like a normal glide on the output
  alone. The internal model sees that the throttle went idle *while the controller wanted thrust*: 15 of 22 vs 10;
  7 engine failures are still not caught in time. What it shows is an inconsistency between control intent and output,
  not by itself a mechanical engine fault.
- **It is not a replacement.** Worse on control surfaces (outputs that freeze are caught faster and more reliably by
  the existing freeze/split rules), slower, and 7 early alarms.
- **The early alarms come from too little normal data.** First guess: the normal flights don't include the
  emergency-trajectory manoeuvres of the `with_emr_traj` flights. The follow-up below did not confirm that; the
  likelier cause is day-to-day differences in normal throttle. Either way, a residual is only meaningful where the
  model has been validated.

## Audit of the evaluation (v1)

- **The 47 flights**: 3 development flights (one engine, one rudder, one aileron failure) are excluded from scoring, as in
  `docs/ALFA_BENCHMARK.md`; 44 are scored: 33 with an injected fault and 11 without. Of those 11, 10 are labelled
  `no_failure` and 1 is labelled `no_ground_truth` (no fault record, which is not the same as a verified normal flight).
- **Training and scoring are separated by flight.** The 10 `no_failure` flights are the only training data. Each of them
  is scored with a model and thresholds fitted on the other 9; fault flights and the `no_ground_truth` flight are scored
  with the model fitted on all 10. No time-window splitting.
- **Thresholds and preprocessing used no fault flight.** The features were chosen from how an ArduPlane autopilot
  works, not by searching. But the designer (Claude) already knew from earlier benchmark runs on these same flights
  that engine failures were the weak spot, so the choice of a throttle model was not blind to the dataset.
- **False-alarm evidence is thin**: after the 20 s reference window, the 11 normal flights give only 535 s (9 minutes)
  of scorable flight; four of them give under 15 s each. One false alarm (the same flight on which the existing
  checks also false-alarm).
- **Alarms before the fault**: yes, the 7 "early" alarms on engine flights fire 7 to 111 s before the injected failure.
  All 7 are July flights; the July normal flights give 68 s of scorable data.

Per flight, engine failures (held-out, 22): both detectors 9; **only the internal model 6**
(07-18-15-53-31_2, 09-11-14-22-07_1, 10-18-11-03-57, 10-18-11-04-00, 10-18-11-04-08_1, 10-18-11-04-08_2);
only the existing checks 1 (07-30-17-20-01, where the internal model alarmed 10 s early); neither 6.
Four of the six extra detections come from one session on 2018-10-18 (consecutive flights, same day), so they are
not six independent confirmations.

Control surfaces (12): the existing checks (output rules plus tracking) catch all 12; the internal model 7 in window,
2 late, 1 wrong part, 2 missed. It adds no surface detection the existing checks miss.

## Follow-up: "know when the model doesn't know" (v2, v3)

| Held-out (33 faults, 11 normal) | v1 | v2: abstain outside the inputs seen in normal flights | v3: abstain only on unusual *commands* (exploratory) |
|---|---|---|---|
| Right part, in window | 20 | 15 | 21 |
| Engine / aileron / elevator / rudder | 15 / 2 / 1 / 2 | 11 / 2 / 0 / 2 | 15 / 2 / 2 / 2 |
| Too early or late | 8 | 9 | 8 |
| False alarms (normal flights) | 1 | 3 | 1 |

- **v2 (pre-registered) made it worse.** A fault itself pushes the controller's errors outside anything seen in normal
  flight, so v2 abstained exactly when a fault happened; masking the worst normal stretches also lowered a threshold.
- **v3 (written after seeing v2, so post-hoc) is about level with v1**: one more elevator failure, same early alarms.
  The early throttle alarms are not explained by unusual pitch commands. The likelier cause: normal throttle differs by
  flying day (median 0.31–0.44 in July, 0.53–0.57 in September), and the July normal flights are few and short.
- **Stopped here.** Each further change would be tuned on flights already seen.

## Fresh data: what exists

ALFA's raw recordings (downloaded, CC0) add 8 sessions not in the processed set; 7 were examined (the eighth is a
4.6 GB recording, not extracted). Only one
contains an injected fault during autonomous flight (2018-07-30-16-28-00, engine); the other autonomous stretches are
shorter than the 20 s reference window. One labelled flight is not a test. RflyMAD's 497 real flights are the right
next source, but its download page could not be read from here and no licence is stated.

## Next

1. Use the internal model as an *additional, labelled* finding for one question it answers well: "the throttle went
   to idle while the autopilot wanted thrust" (engine failures), not as a general detector.
2. Get RflyMAD real flights (or customer normal flights) with enough normal data per machine and per flying day,
   pre-register, and test once.

Reference: Keipour, Mousaei, Scherer, *ALFA: A Dataset for UAV Fault and Anomaly Detection*, IJRR 2021,
doi:10.1184/R1/12707963.
