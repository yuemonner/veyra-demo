# 90 Second Demo Script

## Opening

Open `/cinematic`.

> Six robots. One model update. Ninety seconds.
>
> This is what Physical AI looks like before fleet scale: repeated real-world runs, constant change and fragmented context.

## 1. Signal

Show the six robot run:

- 14:02:11 policy v0.9 test rollout started
- 14:04:37 all six robots updated
- 14:11:08 first known grip pose drift signal
- 14:18:42 two affected robots detected
- 14:26:03 engineer note recorded

Say:

> Same model. Same task. Different behavior.

## 2. Where Else

Show the scope view:

- two robots show the pattern
- one more robot shares the same calibration and gripper firmware
- three robots stay stable under other combinations

Say:

> The first useful question is scope. Where else is the same pattern appearing, and which exposed run deserves attention now?

## 3. Compare

Show the decision-time comparison:

- Policy v0.9 ran on all six robots
- Camera calibration C and gripper firmware 7.3 co-occur across the affected runs
- R06 shares the same combination and appeared stable at decision time
- Low-light bin was ambient in the decision-time comparison

Say:

> This narrows the investigation while keeping root cause open.

## 4. Decision

Generate the Decision Package.

Show:

- evidence available
- open questions
- team action
- follow-up
- decision state saved at 14:27

Say:

> The team pauses v0.9 on robots with calibration C and gripper firmware 7.3. Veyra preserves the evidence, action scope and open questions around that decision.

## 5. Late Evidence

Inject delayed evidence.

Show:

- 14:09 event_time: R06 post-run telemetry shows drift
- 14:27 decision state saved with two affected robots
- 14:31 known_at: delayed evidence becomes knowable
- 14:31:04 ingested_at: evidence reaches Veyra

Say:

> Veyra updates what we know now, not what the team knew then.

## 6. Outcome

Show the follow-up result:

- R03 recovered after rollback
- R05 recovered after rollback
- R06 later confirmed affected
- no recurrence on reverted robots during the follow-up window

Say:

> Veyra links the action to what actually happened.

## 7. Similar Previous Case

Move twelve days forward.

Show:

- matched context
- previous action
- previous outcome
- missing evidence from last time
- what differs this time

Say:

> The investigation starts with what worked before. Every operational decision makes the next one smarter.

## Close

End on:

> Today: six robots in a lab. Tomorrow: cranes, robot cells and autonomous machines in production.
>
> Veyra becomes the operational intelligence system for Physical AI.
