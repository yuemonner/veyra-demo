# anticipate-eval-v1: results (scored 2026-11-18)

Frozen code `anticipate-eval-v1-frozen` (e56bfbe). 144 sealed SYNTHETIC cases, key SHA-256 8cea8e1a…, 0 errors.
The watch reads only data up to each as_of; at 1.0 it reads up to just before the protective event.
Full scores: `RESULTS.json` (this folder; copied from /tmp/claude-0/anteval/runs). Per-case records: ~/Veyra-private/anticipate-eval-v1/.

## Component faults flagged (48 cases: 16 drift, 16 load, 16 sensing) and false alarms (96 cases)

False alarms are counted over healthy_constant, healthy_varying, noise, timing, pack- or payload-wide (common) and
external stops.

| as_of (fraction of time to the event) | rule M (the watch): flagged | M: false alarms | rule S (significance only, like v1): flagged | S: false alarms |
|---|---|---|---|---|
| 0.5  | 12 / 48 | **0 / 96** | 25 / 48 | 10 / 96 |
| 0.75 | 17 / 48 | **0 / 96** | 23 / 48 | 15 / 96 |
| 0.9  | 19 / 48 | **0 / 96** | 25 / 48 | 19 / 96 |
| just before the event | 20 / 48 | **0 / 96** | 31 / 48 | 19 / 96 |

Rule S's false alarms are all in the battery: mostly the whole pack running empty (common, up to 8 of 8), plus healthy,
noise, timing and external cases. Robot false alarms are 0 under both rules.

### By mechanism, rule M

- **drift:** 9 of 16 flagged halfway to the event, 13 at 0.75, 15 at 0.9, 16 of 16 just before it.
- **load:** at most 4 of 16 flagged. A growing resistance or falling torque constant shows as larger load coupling, but
  the spread's own share stays below half while the load keeps changing.
- **sensing:** 0 of 16 at every time. Intermittent glitches do not make the spread grow. They are what Investigate's jump
  test is for (at an event), not what the watch's growth rule sees.

### Earliest flag, rule M

Of the 25 component faults ever flagged, 12 were flagged by halfway to the event, 8 more by 0.75, 4 by 0.9 and 1 just
before it. The other 23 were never flagged.

### When the watch flags

- **Mechanism lean:** when it gave a single lean, it was correct 11 / 12 at 0.5, 15 / 17 at 0.75, 16 / 19 at 0.9 and
  20 / 20 just before the event. Every lean given was a single one.
- **Recommended check:** it addressed the true mechanism in 100 % of flagged cases at every time.

## What this says

- **Materiality is what makes the watch usable.** Significance alone flags up to 19 of 96 non-faults. Material growth
  flags none, in either category, at any time.
- **It warns early for gradual, load-independent deterioration.** For cell capacity or imbalance, and for actuator
  friction or wear, it flagged 9 / 16 halfway to the protective event and 16 / 16 just before it. It also names the
  mechanism class and the check that confirms it.
- **It is weak or blind for the rest:**
  - load-proportional deterioration (resistance, torque constant): at most 4 / 16;
  - intermittent sensing faults: 0 / 16.

  These need different signatures, such as growth of the load coupling itself as a watch rule and a glitch rate. That
  is the next version, to be preregistered and confirmed on a fresh set.
- **Limits.**
  - All synthetic, from the generator that shaped the design.
  - No validated false-alarm rate on real machines.
  - The 1/2 materiality factor is a design choice.
