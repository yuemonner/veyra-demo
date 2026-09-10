# 90 Second Demo Script

## Opening

> Everyone here is building better robots. I want to show what happens the morning after you deploy them.

Open `/cinematic`:

> A deployment just went out to 120 robots. 37 are starting to behave differently.

## 1. Live Failure

Show the fleet map and event sequence:

- 14:02: healthy state snapshot
- 14:04: v2.4 deployed to all 120
- 14:11: first abnormal navigation signal
- 14:18: 37 affected robots detected
- 14:26: customer ticket arrives

Say:

> Veyra saw the pattern 7 minutes before the customer ticket. The question is not just what log line looks strange. The question is what the affected robots have in common.

## 2. Compare

Click `Compare affected vs healthy`.

Say:

> Software changed everywhere. The failure did not.

Show:

- Same change: 120
- Same signal: 37
- No signal: 83
- Network Profile C is the clean separator

Say:

> This narrows the investigation. It does not claim root cause.

## 3. Decision Package

Click `Generate Decision Package`.

Say:

> Instead of asking the team to open deployment history, telemetry, config state, customer tickets and peer machines, Veyra assembles one package around the decision.

Seal it:

> This preserves what was known when the team chose an action.

## 4. Delayed Evidence

Click `Inject delayed evidence`.

Say:

> New evidence can change what we believe now. It cannot rewrite what the team knew when it made the decision.

## 5. Operational Memory

Record the outcome.

Say:

> Forty-seven days later, a similar context appears. Veyra can surface the prior action and outcome.

Close:

> The first incident took 42 minutes to understand. The second took 42 seconds. Every machine decision should make the next one better.
