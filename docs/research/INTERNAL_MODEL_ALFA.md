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

- **It fixes the known weakness**: engine failures, where an idle throttle looks like a normal glide on the output
  alone. The internal model sees that the throttle went idle *while the controller wanted thrust*: 15 of 22 vs 10.
- **It is not a replacement.** Worse on control surfaces (outputs that freeze are caught faster and more reliably by
  the existing freeze/split rules), slower, and 7 early alarms.
- **The early alarms have one cause:** the normal flights are short and don't include the emergency-trajectory
  manoeuvres flown in the `with_emr_traj` flights. The model is asked about situations it never saw and reports them
  as faults. A residual is only meaningful where the model has been validated.

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

ALFA's raw recordings (downloaded, CC0) add 8 sessions not in the processed set. Of the 7 readable ones, only one
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
