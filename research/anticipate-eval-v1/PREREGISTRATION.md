# anticipate-eval-v1: can Veyra warn before a protective event using only past data? (preregistration)

Written and committed before any run. The code is frozen at tag `anticipate-eval-v1-frozen`, branch
`anticipate-surface`. `evaluate.py` refuses any other code. The set is drawn after the freeze with os.urandom, and the
key stays outside the repository; its SHA-256 is committed before the first run.

## What is evaluated

The founder asked on 2026-11-18 for two complementary capabilities:
- **Investigate:** after a fault, reconstruct the causal chain.
- **Anticipate:** before a fault, find a gradually deteriorating physical relationship and recommend an evidence-based
  check.

Anticipate is `app/physchecks/watch.py`:
- it is deterministic and uses no model;
- it runs on every ingested recording (`ingested.auto_watch`) and from the command line (`scripts/veyra_watch.py`);
- it reads only data up to `as_of`.

The watch reuses dynamics-v2:
- the divergence of comparable components;
- materiality (at least half of the limiting member's excursion is its own);
- growth only, since a converging spread is not a deterioration;
- the robustness and timing-skew qualifications.

It reports the signatures that lean to a mechanism (load coupling growth, jumps without a load step, a smooth own
departure) and the domain library's check that separates the mechanisms still open.

One limitation of v1 investigations' lead times is addressed here. The "earliest persistent detection" in the engine
uses later blocks, so it is retrospective. This evaluation measures what a monitor would have said at time T from data
up to T only.

## Set

- **Generator:** `scripts/make_synthetic_dynamics.py` (SYNTHETIC; battery and robot).
- **Cases:** 8 seeds x 9 classes x 2 categories = 144 cases.
- **Seeds:** fresh. They are not the dynamics-eval-v1 or -v2 sealed seeds, and not development seeds 2001–2004, which
  shaped dynamics-v2 and the watch.

## Measures (fixed now)

For each case, the watch runs on the first comparable group at as_of fractions 0.5, 0.75, 0.9 and 1.0:
- for cases with a protective event, the fractions are of the time from the start to the event that Veyra finds in the
  data. At 1.0 it reads just before the event; the event itself is never read;
- for cases without one, they are of the record's span.

Two rules, both reported:
- **M (the watch's rule):** status `material_departure`.
- **S (significance only, like v1's W1):** status `material_departure` or `significant_not_material`.

Reported for each fraction, for all, battery and robot:
- detection on component faults (drift, load, sensing), with misses by class;
- false alarms on every other case (healthy_constant, healthy_varying, noise, timing, common, external), by class;
- for rule M:
  - how often a lean was given;
  - single-lean accuracy;
  - lean contains the truth;
  - the recommended check addresses a pair containing the true mechanism;
- the earliest fraction at which each component fault was flagged by M.

No threshold is tuned on this set. Whatever the result, it is reported as it comes.

## Limits stated now

- **Synthetic generator.** The set comes from the same team's generator that shaped the design, so it confirms behaviour
  on unseen seeds of that world, not on real machines.
- **No validated false-alarm rate.** A rate on real fleets needs many machines' records.
- **The materiality factor (1/2) is a design choice**, not fitted.
- **A moving baseline.** The baseline is the first third of what the watch has seen, so a fault present from the start
  is part of the reference.
- **The lean is not a diagnosis.** It is what the signatures point to; the recommended check is what would confirm it.
