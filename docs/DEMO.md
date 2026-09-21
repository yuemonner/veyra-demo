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

> One deployment. Twelve machines. Three enter safe-stop.
>
> See what changed. Decide what to do. Learn what happened after. Reuse it next time.

## 1. Signal

Show:

- 12 remote-operated machines
- autonomy release 2.7 deployed to all machines
- EX03, EX05 and EX08 enter safe-stop
- 9 comparable machines remain healthy at decision time

Say:

> Three machines entered safe-stop after the same deployment.

## 2. What Changed

Show the reconstructed context:

- autonomy stack 2.6 to 2.7
- localization profile L3 to L4
- LiDAR firmware 5.2 to 5.3
- map version M18 to M19
- safe-stop near loading zone B
- operator review at 14:31
- hardware fault unconfirmed

Say:

> Veyra reconstructs the operational context around the case: machine state, autonomy release, localization changes and operator review.

## 3. Where Else

Show:

- 12 machines share the same autonomy release
- 5 share localization profile L4
- 4 share loading zone B exposure
- 3 are known affected at decision time
- EX11 shares the exposure and has no known issue at 14:27

Say:

> The question becomes scope. Where else does this behavior appear, and which exposed machines should the team watch?

## 4. Decision State

Show:

- Known at 14:27: EX03, EX05 and EX08 affected, same autonomy release, localization profile L4, loading zone B exposure, hardware fault unconfirmed
- Unknown at 14:27: whether EX11 will show the same issue, whether remote recovery will restore service
- event_time / known_at / ingested_at

Say:

> Veyra preserves what the team knew when the decision was made.

## 5. Team Action

Show:

- remote recovery on EX03, EX05 and EX08
- hold the exposed rollout group
- monitor EX11
- notify operations support
- hold field dispatch
- owner: Robotics Engineering

Say:

> The investigation becomes an operational record.

## 6. Outcome

Show:

- EX03, EX05 and EX08 returned to service after remote recovery
- EX05 entered safe-stop again six hours later
- field visit avoided
- rollout held for 43 minutes
- delayed EX11 runtime buffer arrives
- current affected population updates to 4

Say:

> New evidence updates the case without rewriting the original decision.

## 7. Twelve Days Later

Show:

- similar case happened two weeks ago
- remote recovery returned affected machines to service
- field dispatch was avoided
- one additional machine appeared later
- suggested next step: check localization and loading zone exposure before dispatching a technician

Say:

> The company no longer starts from zero when a similar machine problem appears again.

## Close

> Every operational case should make the next one smarter.
>
> Operational intelligence for Physical AI.
