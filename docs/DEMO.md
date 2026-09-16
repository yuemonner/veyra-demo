# 90 Second Demo Script

Open `/cinematic`.

The demo is an Operational Case:

```text
Signal
  -> What changed
  -> Where else
  -> Decision state
  -> Team action
  -> Outcome
  -> Reuse next time
```

## Opening

> Six machines. One software update. Something changed.
>
> See what changed. Decide what to do. Know whether it worked. Reuse it next time.

## 1. Signal

Show:

- 6 machines
- same software release
- R03 and R05 abnormal
- R01, R02, R04 and R06 healthy

Say:

> Two machines started behaving differently after the same update.

## 2. What Changed

Show the reconstructed context:

- software release v0.8 -> v0.9
- camera calibration B -> C
- gripper firmware 7.2 -> 7.3
- grip pose drift
- engineer note at 14:26
- hardware fault unconfirmed

Say:

> Veyra reconstructs the operational context around the case: machine state, software/config changes and human observation.

## 3. Where Else

Show:

- 17 machines share the same software version across active test groups
- 5 share the same configuration profile
- 2 are currently affected
- R06 shares the exposure and appears healthy at decision time

Say:

> The question becomes scope. Where else does this pattern appear, and which exposed machines should the team watch?

## 4. Decision State

Show:

- Known at 14:27: R03 and R05 affected, same config profile, both updated today, hardware fault unconfirmed
- Unknown at 14:27: whether R06 will show the same issue, whether rollback will recover both machines
- event_time / known_at / ingested_at

Say:

> Veyra preserves what the team knew when the decision was made.

## 5. Team Action

Show:

- pause rollout to remaining machines
- roll back R03 and R05
- monitor R06
- notify customer support
- hold field dispatch
- owner: Operations Lead

Say:

> The investigation becomes an operational record.

## 6. Outcome

Show:

- R03 recovered after rollback
- R05 recovered after rollback
- field visit avoided
- follow-up window clean for 24 hours
- three days later, R06 shows the same pattern
- current affected population updates to 3

Say:

> New evidence updates the case without rewriting the original decision.

## 7. Twelve Days Later

Show:

- similar case happened 12 days ago
- rollout was paused
- rollback recovered affected machines
- field dispatch was avoided
- one additional affected machine appeared later
- suggested next step: check configuration profile before dispatching a technician

Say:

> The company is no longer solving the same problem from zero.

## Close

> Every operational case should make the next one smarter.
>
> Operational intelligence for Physical AI.
