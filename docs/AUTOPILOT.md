# Autopilot: Veyra does the work, people make the calls

Code: `backend/app/workflows/autopilot.py`. Self-check: `verify_remote_check` in `backend/app/engine/decision_check.py`.
Screen: the top of the **Next action** tab. Tests: `backend/tests/test_autopilot.py`. On when `VEYRA_AUTOPILOT=true`
(the local preview sets it).

## The loop

| Step | Who | Record | State shown |
|---|---|---|---|
| Investigate the upload | Veyra | `investigation_run` | |
| Compare the candidate checks, pick up to two | Veyra | `next_action` (plan, with `comparison`) | |
| Check its own proposal against the evidence | Veyra | `next_action` (self_check, internal) | |
| Draft one request for the picked checks, "done" defined first | Veyra | `next_action` (prepared) | **Draft** |
| Approve the draft | **Person** | `check_review` per check | **Approved, not sent** |
| Send it (email, phone, ticket…) and say so | **Person** | `request_sent` (channel, to, when) | **Sent** |
| Record each answer from the site | **Person** | `check_execution` | **Answered** |
| Re-assess and recommend | Veyra | `next_action` (report) | |
| Check its own recommendation | Veyra | `next_action` (self_check, internal) | |
| Record the decision | **Person** | `decision` (`veyra_check_id` links the self-check it accepted) | |

Approving a draft sends nothing. Veyra never sends messages and cannot write `request_sent`; only a person can.

`advance()` runs after an investigation, an approval, a "sent" record or a site result. It is idempotent and holds a
per-case lock, so two calls at once write each record once.

## Internal attempts vs decisions

Self-checks are Veyra's own attempts. They are `next_action` records with `stage: self_check` and `internal: true`,
fingerprinted by what they were checked against (run, subject, site results, approvals): the same inputs never write
a second record, and a failed self-check isn't retried until the evidence changes. They are never stored as
`decision_proposal`, which is reserved for proposals by people. The Record tab folds them away; the episode export
counts them under `agent_internal` and keeps human proposals and decisions separate. Cases recorded before this
change keep their older `decision_proposal` + `decision_check` pairs (read as legacy self-checks).

## When Veyra asks for nothing

- No problem was reported and the only finding is sub-second events: Veyra says so and drafts nothing.
- No remote check's result would change which explanation is more likely: Veyra says the next step is on site or a decision.

## Permission tiers

- **Veyra does on its own:** read authorized records, compute, compare, write its own records, draft requests.
- **A person approves:** sending anything outside, asking the site to act, decisions, dispatch, purchases.
- **Never by Veyra:** machine control, dangerous tests, confirming a repair, closing a case.

## Self-check (Veyra checking its own proposal)

The checker re-derives every premise from the run and the case record, not from the plan's text:

1. It informs explanations that are still open, from the evidence now. The claim lists what each answer would do
   ("if X: A more likely; if Y: A less likely") and says "No single result settles it alone" unless one answer
   supports exactly one open explanation and weakens all the others.
2. It hasn't been done.
3. Some result of it could change whether someone travels (each result simulated).

All three supported → the check goes into the draft. If the checker fails, the result is "not checked" and nothing
is drafted.

## What the draft may say

Only what the recording shows, each line with its time and source (tracking finding, or import and row). Routine
events (e.g. every joint changing mode as the robot stands up) are left out. If the recording has no controller,
reset or link events, the draft says that, rather than naming an event. Each question lists the possible answers
plus "Something else", none presumed to have happened.

Veyra's proposals and claims are labelled `proposed_by: veyra` / `agent_proposed`. Only the engine can write them;
a person's proposal through the ledger API is always `engineer`.

## "Done" is defined up front

The prepared request fixes what counts as an answer (one of the named results, plus where the record is) and when a
recommendation is due. Veyra re-assesses only when a result meets that.

## Limits

- Veyra drafts the request; a person sends it. No messaging integration.
- The plan and checks are rule-based (`next-action-v2`, `decision-check-v1`). The outcome effects in `SPLITS` and the
  ranking order are written assumptions, not validated against outcomes. The LLM agent is not in this loop yet;
  it is the natural next step for drafting and for questions the rules don't cover.
- Measured on synthetic cases only. The real test: end-to-end success on real incidents, number of times a person
  had to step in, and how often Veyra's flags were wrong.
