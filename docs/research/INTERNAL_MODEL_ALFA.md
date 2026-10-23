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

## Next (needs fresh flights, because ALFA has now been seen)

1. Report "outside what the model has seen" separately from "fault-like residual" (inputs outside the normal
   flights' range → abstain, not alarm). That is the precision idea in engineering form.
2. Run the internal model *alongside* the existing rules, as a separate, labelled finding.
3. Pre-register v2 and test it on data not used here: ALFA's raw recordings with further scenarios, or the real-flight
   part of RflyMAD (licence to be checked).

Reference: Keipour, Mousaei, Scherer, *ALFA: A Dataset for UAV Fault and Anomaly Detection*, IJRR 2021,
doi:10.1184/R1/12707963.
