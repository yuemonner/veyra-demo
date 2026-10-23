# Case Memory Test: two related incidents, one key condition changed

Run 2026-10-21. SYNTHETIC data only (no new dataset, no new agent). Code: `backend/app/workflows/case_memory.py`;
runner: `scripts/validation/case_memory_test.py`; pytest: `backend/tests/test_case_memory.py`;
results: `docs/results/case_memory_results.json`.

## The test

Machine EX2, a synthetic remote excavator built with the same generator as the EX1 demo story
(`scripts/make_synthetic_remote_delay.py`, deterministic).

**Incident 1 (8 April).** The operator pauses and reports "Boom reacts late to the joystick" (symptom: late response,
function: boom). The engineer:
1. runs the network log check: a handover storm on the machine's LTE cell;
2. decides to pin the machine to the main cell;
3. records the action (simulated) and the outcome (recovered, no complaint for two days);
4. closes the case: cause confirmed (the fix resolved it), cause in the network, machine resolved, settled by
   CHK-NETWORK-LOGS.

**Incident 2 (15 April), same machine, same complaint**, in three versions:

| Variant | What is different from incident 1 | What Veyra should do |
|---|---|---|
| repeat | Nothing: same link problem | Find incident 1, nothing differs, put its network check first |
| **changed** | **One key condition: the link is healthy; the boom itself responds late (vehicle-side delay 150 → 450 ms in the same window)** | Find incident 1 because the complaint is the same; say the evidence now points elsewhere; do not push the network check; propose the machine-body team from this case's own evidence |
| unrelated | Same data as "changed", but the operator reports a different symptom (boom stopped by itself) | Do not show incident 1 |

For each variant, incident 2 is investigated once **before** incident 1 is closed and once **after**. Its own
conclusions (hypotheses, proposed owner and severity, root cause) must be identical in both runs. If they are, Veyra
did not apply incident 1's cause.

The pass/fail criteria are written in `case_memory.criteria()` and were fixed before the run.

## First run: it failed where it mattered

With the precedent rules from the ALFA experiment (`precedent-rules-v1`), similarity used only what the engine
flagged. Results:

- **repeat:** passed.
- **unrelated:** passed.
- **changed:** **did not find incident 1 at all.** Incident 1 flagged the network and the remote-control software;
  incident 2 flagged only the machine body. Nothing was shared, so nothing was shown.

That is the case that matters most. An engineer would remember "same complaint as last week", and that is when a
wrong copy of the old cause is most likely. Matching on findings alone cannot see it.

## What changed (precedent-rules-v2)

- **The operator's report is structured.** An intervention report can carry `symptom` (late_response, no_response,
  unexpected_motion, erratic_motion, stopped_unexpectedly, other) and `affected_function` (e.g. boom). The operator's
  own words stay in `stated_reason`.
- **Two ways to match.** An earlier closed case is shown when its findings are similar (as before), **or** when an
  operator reported the same symptom on the same function on the same machine (or machine type).
- **A symptom-only match is labelled "Same complaint, different evidence", and it shows:**
  - where the earlier case found its problem, and what this case shows there now (e.g. "Network: nothing found in
    the measured part");
  - which parts this case's findings are in;
  - the earlier case's settling check, which is **not** moved up in Next checks: moving it up would carry the old
    cause over.
- **Findings are written in words.** "Machine body & hydraulics: response to its input changed (R-IO)", not only
  rule codes.

## Second run: all three pass

```
repeat     PASS
   ok  1. finds incident 1
   ok  4. incident 1's cause not applied (hypotheses, triage, root cause unchanged)
   ok  root cause still unconfirmed
   ok  matched on findings and the reported symptom
   ok  3. nothing differs
   ok  2. the check that settled incident 1 is first in Next checks
   ok  owner proposed from this case's evidence: network
changed    PASS
   ok  1. finds incident 1
   ok  4. incident 1's cause not applied (hypotheses, triage, root cause unchanged)
   ok  root cause still unconfirmed
   ok  matched on the reported symptom only
   ok  3. says the evidence points elsewhere
   ok  3. says where incident 1 found its problem is quiet now
   ok  3. lists what is only in this case
   ok  2. incident 1's check shown but NOT moved up
   ok  Next checks identical to before incident 1 was closed
   ok  owner proposed from this case's evidence: machine body
unrelated  PASS
   ok  incident 1 not shown (different complaint, no shared findings)
   ok  own conclusions unchanged
```

On the hosted demo, the EX2 pair is seeded: incident 1 closed, incident 2 ("changed") investigated. Open incident 2;
the Brief shows incident 1 under "Similar earlier cases".

## Limits

- **Synthetic, and built to be clean.** The changed condition is a clear, single change, and both incidents come from
  the same generator. Real incidents will not separate this neatly.
- **The symptom must be recorded.** Matching on the complaint only works if someone records the structured symptom.
  There is no intake form for it on the page yet (it goes in through the ledger API). Free text is not matched.
- **The repeat variant passes partly by construction.** Its data is the same generator run a week later, so "nothing
  differs" is expected.
- **No real engineer has used it yet.** Whether "Same complaint, different evidence" stops someone from copying the
  old fix is a question for a real deployment, not for this test.
