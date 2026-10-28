# Sealed evaluation on real public failure logs — 2026-10-28

Question: on real robot failures nobody building Veyra had seen, does Veyra investigate better than a general Claude
agent, and does it save the engineer work? No synthetic data. Protocol, hashes, raw outputs, keys and grades are kept in
the evaluation folder (outside the repository); the protocol was written and hashed before any case was chosen.

## Method
- A separate collector agent picked real logs pasted in public GitHub / forum threads and sealed the answer keys
  (cause established later in the thread, or unresolved), hashed before any system ran. Excluded: every case the team
  had touched (Crazyflie #531 / forum t=2535, PX4 #25892 / #26910, ALFA).
- Conditions, same input (log + the reporter's symptom with the diagnosis removed), same output schema:
  A = Veyra rules only (no model); B = Veyra with AI; C = plain Claude (same model, sonnet) with the raw log.
- A separate grader, blind to condition (outputs shuffled and relabelled per case), scored five criteria 0-2 against the
  keys; a script checked every cited line and quote mechanically.

## Batch 1 — 6 cases, the product as it was (B = AI using Veyra's old tools outside the product loop)
| | events | cause vs effect | next check | evidence | uncertainty | total /60 |
|---|---|---|---|---|---|---|
| A rules only | 4 | 1 | 0 | 6 | 6 | 17 |
| B AI + old Veyra tools | 12 | 9 | 10 | 12 | 12 | 55 |
| C plain Claude | 12 | 11 | 10 | 12 | 12 | 57 |

Veyra added nothing over plain Claude; rules alone gave no useful next check and crashed on one log (clock stepping
back). This led to the rebuild: an investigator that works on the original lines, runs the checks itself, verifies
every quote and number, and ends in one next action with what each result would mean (commit 91cd656 onward).

## Batch 2 — 8 holdout cases (5 established, 3 unresolved), the rebuilt product
B is the product itself: `scripts/validation/holdout_investigate.py`, the same path as an upload, live Claude Code
sign-in (sonnet), fresh database.

| | events | cause vs effect | next check | evidence | uncertainty | total /80 | actionable next step /16 |
|---|---|---|---|---|---|---|---|
| A rules only | 3 | 0 | 2 | 4 | 8 | 17 | 0 |
| B Veyra product (AI) | 14 | 13 | 14 | 16 | 15 | 72 | 16 |
| C plain Claude | 16 | 16 | 15 | 15 | 14 | 76 | 8 |

Per case (total /10): c07 V10 C9 · c08 V9 C9 · c09 V10 C10 · c10 V10 C10 · c11 V9 C10 · c12 V10 C9 · c13 V6 C10 · c14 V8 C9.

Veyra: 22-26 s per case from upload to next action, 4-5 checks run by itself, 0 claims dropped by verification,
~30-50 k input tokens per case.

## What this shows
1. Without a model Veyra is a weak rule system (17/60, 17/80). It cannot be sold as an AI investigation system in
   that mode, and the page now says so when it falls back.
2. With the model, Veyra matches plain Claude on diagnosis but does not beat it (72 vs 76; its two misses: c13 led with a
   host-network explanation the symptom already ruled out, c14 led with a bad lead the thread disproved).
3. Veyra's measured gain is in the engineer's workload: every first next step was directly actionable (16/16: a
   concrete command or value plus what each result means) vs 8/16 for plain Claude, and every quote is verified against
   the file. On the one case followed through in the UI (micro-ROS, batch 1), the engineer's recorded result led Veyra
   to the fix the thread found (clean the D-cache over the Ethernet Tx buffer).
4. The success criterion "more reliable attribution than a general Claude agent" is NOT met. "Less engineer time to a
   useful action" is supported on this small set.

## Caveats
- 14 cases; short logs (8-152 lines), where plain Claude can read everything at once. Veyra's tool-based approach
  should matter more on long or multiple logs; not yet tested.
- Grading was blind to labels but the formats differ, so a grader could infer the system.
- Several keys rest on a contributor's analysis rather than a maintainer's confirmation (noted per case); one grader
  note questions part of the c07 key. Logs were copied through a fetch tool; minor spacing differences are possible.
- Both AI conditions use the same model; memorisation of public threads cannot be excluded (one batch-1 run said it may
  have recognised a thread).

## Next
- Add what plain Claude cannot do: compare with earlier cases and their verified outcomes (lessons), read several files
  and long logs with exact counts and timing, and track whether the proposed action resolved the failure.
- Re-test on a fresh holdout with long logs and with a closed-case history, measuring time to the next action and
  whether the first action resolved the failure.
