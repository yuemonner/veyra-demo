# Decision Check

Before a decision is committed, Veyra checks it against the evidence. Advisory: it never blocks, never says the
action is the best one, never names a failed part. Rules: `backend/app/engine/decision_check.py` (`decision-check-v1`).
Flow: `backend/app/workflows/decision_check.py`. Screen: **Your decision** in the Next action tab. Tests: `backend/tests/test_decision_check.py`.

## Three things kept apart

| | What | Attributed to the engineer? |
|---|---|---|
| Facts | What the records show, relevant to this action | Never |
| Gaps | Checks whose result could realistically change this action | Never |
| Claims | The part they think is at fault, findings they rely on | Only what they ticked or kept, with provenance |

Provenance: `engineer_stated`, or `system_suggested_confirmed` (a Veyra prefill the engineer kept). A kept prefill is
never recorded as the engineer's own independent judgement.

## Two answers

- **Coverage** (were the checks this decision depends on done and readable?): `complete`, `partial`, `none_needed`, `not_assessed`.
  Each check shows `done`, `not_done`, `needs permission` or `tried, no result`.
- **Claims** (per stated claim): `supported`, `contradicted`, `insufficient`, `not_assessed`.

## Rules

- **Realistically change**: each possible result of a check is simulated on the current balance of evidence
  (same weights as Next action). The check counts only if some result would change what the action rests on:
  for a dispatch, whether a remote part now leads (no trip needed) or the target part stops leading; for a remote fix,
  whether the target stops leading; for closing, whether the conclusion changes.
- **Safety or urgent**: no diagnostic gaps. A missing root cause does not change the need to go.
- **Contradicted** needs positive counter-evidence that covers the claim: a site result that makes the part less likely,
  or a person ruling it out. "No delay where measured" is `insufficient`, never `contradicted`.
- **Relying on a finding**: the reviewed limit of that kind of finding is stated (`LIMITS`): what it shows and what it does not.
  Using a normal measurement to rule a part out is at most `insufficient`.
- **Not assessed**: no run, a stale run (evidence or machine context changed) or a failed check. Shown as "Not checked",
  never as a pass.

## When it speaks

| Alert | When |
|---|---|
| warning | A stated claim is contradicted, or the evidence points to a different part and a check could still change the action |
| notice | Checks could still change it, the evidence points elsewhere, claims can't be confirmed, or visit details are missing |
| quiet | Nothing found that would change it (one line, still recorded) |
| not_assessed | Not checked, with the reason |

## Records

`decision_proposal` (person) → `decision_check` (engine only) → `decision` with `proposal_id`, `check_id`,
`check_alert`, `changed_from_proposal`, optional `proceed_reason` → later `outcome` / `case_closure`.

## Not yet

- Free-text reasons are stored, not checked (an AI reading them is a later step).
- `LIMITS` covers the engine's finding types; new types show as `not_assessed` until a limit is written and reviewed.
- "Verified decision correction" and "estimated avoided cost" are not computed yet; the records above are what they need.
