# Next action: from incident to dispatch decision

A three-step flow that runs after an investigation. Rules: `backend/app/engine/next_action.py` (policy `next-action-v2`).
Flow on a case: `backend/app/workflows/next_action.py`. Screen: the **Next action** tab on `/field-case`.

| Step | What happens | Recorded as |
|---|---|---|
| 1. Investigate | Lists the parts the logs implicate, and the ones they cannot tell apart, with the reason | computed from the latest run |
| 2. Check next | Compares every candidate check on stated criteria and proposes up to two, asked in one request. A person approves; the site does it and reports the result | `next_action` (plan), `check_review`, `check_execution` with an `outcome` |
| 3. Decide | Dispatch readiness report: what can be done now, what can't yet, and why | `next_action` (report) |

## Rules

- **Candidates**: parts with evidence (delay or anomaly) that nobody ruled out. If only one has evidence, unmeasured parts stay candidates.
- **Less likely, from comparison**: if one joint stopped following its commands while two or more others tracked
  normally, an unmeasured operator link becomes less likely (it would usually affect every joint). The controller stays
  open: it can limit one joint.
- **Comparing checks** (`compare`): each check in `SPLITS` is scored by simulating each named answer against the current
  balance of evidence. Ranked by, in order: (1) could an answer change whether someone travels (the likelier part moves
  between remotely fixable and needing hands on the machine), (2) could an answer change which explanation is more
  likely, (3) how many open explanations it informs, (4) access (Veyra / remote person / needs permission / needs data /
  on site), (5) disruption, then whether it yields a log. Checks equal on (1)–(5) are marked `tied_with`: their order
  is arbitrary and both get asked. Each row says whether one answer would settle it alone ("No single result settles it
  alone" unless one answer supports exactly one open explanation and weakens all others) and notes what the recording
  already has (e.g. the drive's status message).
  The plan picks the top two that could change something, remote only. On-site checks are never proposed here: going
  on site is the decision being prepared. The full comparison is stored with the plan.
- **Outcome wording**: each named answer describes what the site would find, never an event the case hasn't shown.
  Old names (`application_fault`, `external_trigger`) are still read as `controller_action_first`, `link_loss_first`.
- **Decision**: each candidate starts at 1 if it has evidence. A site result adds 2 to the parts it supports, takes 2
  from the parts it weakens. A lead of 2 or more decides, unless site results point to different parts (then: hold).
  - Lead is network or control software → "fix remotely first", no one travels yet.
  - Lead is the machine itself → "prepare a visit", with the candidate preparation list.
  - Otherwise → "hold", with the checks still pending.
- Always on the "can't do yet" list: naming a failed part (nothing confirmed it).

## Guarantees

- Nothing executes on the machine. Every check needs a person's approval (`check_review`) before its result can be recorded.
- A result needs who did it, what they saw and where the record is.
- `next_action` records can only be written by the engine, not through the human ledger API.
- Everything is tied to the current investigation run; new evidence or context makes it stale.

## Demo

`app/workflows/excavator_demo.py` seeds a SYNTHETIC remote-excavator case from the synthetic EX1 fixtures (no private
data). The local preview seeds it automatically.

Tests: `backend/tests/test_next_action.py`.

## Limits

- `SPLITS` covers five checks (controller logs, drive logs, network logs, probe path, input–response baseline). Other checks can
  still be done; they just aren't proposed by this flow.
- Site evidence is a note pointing to the record; attaching a file requires a new import and a new run.
- The weights (1, ±2, lead ≥ 2) are written rules, not calibrated probabilities.
