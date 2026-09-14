# 90 Second Demo Script

## Opening

> Everyone here is building better robots. I want to show what happens before fleet scale, when six real robots already start behaving differently.

Open `/cinematic`:

> Six robots ran the same manipulation policy. Two are starting to behave differently.

## 1. Live Divergence

Show the fleet map and event sequence:

- 14:02: healthy state snapshot
- 14:04: policy v0.9 rolled out to all six robots
- 14:11: first grip pose drift signal
- 14:18: two affected robots detected
- 14:26: engineer note recorded

Say:

> Veyra reconstructs the pattern before the team has to hold the whole run in its head. The question is not just what log line looks strange. The question is what the affected robots have in common.

## 2. Compare

Click `Compare affected vs healthy`.

Say:

> Same model. Same task. Different behavior.

Show:

- Same policy: 6
- Same signal: 2
- No signal: 4
- Calibration C and gripper firmware 7.3 co-occur across affected runs
- R06 shares the same combination without a known signal at decision time

Say:

> This narrows the investigation. It does not claim root cause.

## 3. Decision Package

Click `Generate Decision Package`.

Say:

> Instead of asking the team to open policy history, run telemetry, calibration state, engineer notes and peer robot runs, Veyra assembles one package around the decision.

Seal it:

> This preserves what was known when the team chose an action.

## 4. Delayed Evidence

Click `Inject delayed evidence`.

Say:

> New evidence can change what we believe now. It cannot rewrite what the team knew when it made the decision.

## 5. Operational Memory

Record the outcome.

Say:

> Twelve days later, a similar context appears. Veyra can surface the prior action and outcome.

Close:

> The first run took 42 minutes to understand. The next run took 42 seconds. Every real-world run should make the next one smarter.
