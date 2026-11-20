# dynamics-eval-v1: does signal dynamics make Veyra better at anticipating and investigating faults? (preregistration)

Written and committed before any evaluation run. Code frozen at tag `dynamics-eval-v1-frozen`. `evaluate.py` refuses to
run when `backend/` or `scripts/` differ from the tag. Recorded runs are never overwritten or edited. A run lost to an
infrastructure failure (usage limit) is re-run with `--resume`; the failed record is kept as `*.failed-<k>.json`.

## What is evaluated

The founder asked (2026-11-17 22:30) for the engineering skills in an independent engineering assessment of CASE A
to be extracted, generalised and built into Veyra. Those skills are:
- rates on actual timestamps;
- divergence between comparable signals;
- precursors before a protective event;
- the trigger kept separate from the cause.

They are built as:
- the **dynamics engine**: `app/physchecks/dynamics.py`, with the ops quality, rate, changepoint, departure and divergence;
- the **anticipate screen** and the **mechanism classes with generated discriminating tests**: `app/physchecks/anticipate.py`
  with domain libraries `app/physchecks/domains/*.json`;
- **inquiry-v3**, in `app/agent/inquiry.py`. Veyra registers the generated tests before they run. A tie rule in the fault
  algebra refuses a separation that rests on signals neither cause acts on.

The old agent is commit `73fd22e`, the head of `case-a-engineering`. Its backend is identical to tag
`inquiry-strategies-v1-frozen`, with the same coverage strategy and the same tools minus everything above.

## Data

- **Sealed SYNTHETIC set.** `evaluate.py seal` draws it after the freeze (`os.urandom`) with
  `scripts/make_synthetic_dynamics.py`. The key stays outside the repository; only its SHA-256 is committed before
  the first run.
  - **Two categories with one engine:** battery (a series string, lowest/highest cell, pack current, fault level,
    contactor) and robot (n actuators under one command, fault state, protective stop). Domain meaning comes only from
    the two domain libraries.
  - **Classes:** component faults (drift, load, sensing); events without a component fault (common: the whole pack runs
    empty or payload loads every actuator; external: a stop initiated outside the group); controls without an event
    (healthy_constant, healthy_varying, noise, timing).
- **Engine set:** 6 seeds x 9 classes x 2 categories = 108 cases, deterministic (no model).
- **Agent set:** 1 seed x 5 event classes x 2 categories = 10 cases, each investigated by the old and the new agent.
- **CASE A:** a development regression only, never proof of generalisation. Both agents run once each, under the
  recorded customer permission, from the container. The data stays in /tmp/claude-0/private and ~/Veyra-private, never in the
  repository.

Development seeds used before the freeze (3, 4, 5, 7, 11, 12, 13) are not reused: sealed seeds are drawn from os.urandom
in [0, 1e9).

## Measures (fixed now)

### Anticipate (engine)

These run on the first event found (or the whole window when there is none) of the first group.
- **W1 warning:** the spread departs from its baseline trend and load relation (|z| >= 2), with the same verdict under
  baseline fractions 1/4, 1/3 and 1/2, and beyond the timing-skew bound.
- **W2:** W1, and differential_share >= 0.5, meaning more than half of the limiting member's excursion is its own. W2
  is a second, preregistered variant. If W2 does better, it still needs a confirmation set before it can become the
  rule.
- **Detection** counts the component-fault cases (drift, load, sensing).
- **False alarms** count every other case: healthy controls, the noise and timing confounders, common and external.
  Lead time is the median, for W1 detections.
- **AUC** of diff_departure_z and of differential_share, component faults vs the rest. This is threshold-free.

### Investigate (engine)

These cover event cases, using the generated tests only.
- **Top set:** leaves (L1, L2a, L2b, L2c, L3) with at least one consistent test and no outright contradiction; else
  those with no contradiction.
- **Truth ids:** drift L2a, load L2b, sensing L2c, common L1, external L3.
- **Reported:**
  - truth in top set;
  - mean set size;
  - bits = log2(5/|top|) when correct;
  - single-answer accuracy (calibration of the cases where Veyra names one class);
  - truth contradicted;
  - whether the next check targets an unresolved pair containing the truth. This is a proxy: no follow-up record is
    simulated.

### Agents, matched

Both agents use:
- the same model backend (Claude Code CLI, claude-sonnet-5-5);
- the same recording, profile and coverage strategy;
- the same turn budget (each code's INQUIRY_MAX_TURNS = 30, 6 fix attempts).

The class of each hypothesis in the agent's top tier comes from:
- the ledger id: L-ids directly; assessment ids A, B → common, D → drift, E → sensing, F → external;
- else the keyword table in `evaluate.py` (KEYWORDS, fixed now).

The top tier is ranking tier 1, else the leading or open main causes. Reported:
- truth in top tier;
- top-tier size;
- false certainty (one class, wrong);
- whether the next check targets the truth's class;
- the dynamics ops the agent itself ran;
- rejected claims;
- model and tool calls.

### CASE A targets (development regression)

Each is scored from the agent's record by the regexes in `case_a_targets.py`, fixed before the CASE A runs:
- T1: identifies the accelerating lowest-cell voltage decline;
- T2: quantifies the cell-voltage spread and its growth rate;
- T3: relates them to current or operating state;
- T4: establishes how early the trend became observable before protection;
- T5: distinguishes cell-level limitation from pack-level SOC/capacity, and says whether they coexist;
- T6: proposes a more informative next check.

## Limits stated now

- **Small and synthetic.** All unseen cases are synthetic, from a generator written by the same team as the detector.
  Real solved cases with outcomes are the test that matters. The agent set is small: 10 cases per agent shows
  direction, not effect size.
- **No validated warning rule.** The warning rules have no validated false-alarm rate on real machines.
- **Proxy for next-check value.** No follow-up record is simulated.
- **Nothing measures Alloy.**
