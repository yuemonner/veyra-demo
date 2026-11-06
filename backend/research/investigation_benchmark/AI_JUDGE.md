# AI step judge: first live-model run under the evidence verifier

Run 2026-11-06 on the owner's Mac, through the signed-in Claude Code (`claude-code:sonnet`, served by
`claude-sonnet-5-5`). Raw output:
- `results/ai_judge_shuttle-synthetic.json` (frozen v1 states)
- `v2/results/ai_judge_shuttle-synthetic-v2.json` (v2 states, with artifacts)

## Setup

- **What the model judges:** each engineer step that tests a hypothesis (s2–s5). The model is given only the facts
  Veyra had at that decision point, plus the step and its reported result. The sealed truth is never sent.
- **Attempts:** at most 2 per step. A rejected attempt is returned to the model with the verifier's reasons.
- **Extra flag:** numbers in a claim that appear in none of the cited facts. This flags without rejecting.

**Verdict: the AI step judge does not work yet.** The verifier does its job, but most steps end rejected, and some of
the rejections are the verifier's fault, not the model's.

## Results

| Run | Steps accepted | First try | Claims accepted | Unmatched numbers |
|---|---|---|---|---|
| v1 states (telemetry) | 1 of 4 (s5) | 1 | 7 | 0 |
| v2 states (+ artifacts) | 2 of 4 (s4, s5) | 0 | 13 | 0 |

## Why steps were rejected

1. **The model overstated the tier: the most common reason, and caught correctly.**
   - The model labelled claims "confirmed" that rest on data 12–14 days old. Under the protocol that data is
     downgraded to "inferred".
   - It repeated this after being told, on 4 steps.
   - This is the verifier working as intended.
2. **The engineers' own reports cannot be cited: a design gap on our side.**
   - What the engineers reported (for example "SOC and cell voltages normal", "the stop recurred on 10-28") reaches
     the model as context, not as facts with ids.
   - So an accurate claim about a report could only be uncited, and the verifier rejected it. This happened on 3 steps.
   - These are false rejections.
3. **An elimination the protocol allows was rejected: the verifier is too strict.**
   - At s5 (v2) the model eliminated the inverter, citing nothing. The engineers had reported it not the issue at s2.
   - The protocol allows an elimination by an engineer's reported result, but the verifier only accepts one for the
     current step's own targets.
   - This is a false rejection, although the model also should have cited the report.
4. **One elimination was rejected correctly.** At s3 (v2) the model wrote a description in place of a hypothesis id.

## What the verifier did not catch, in accepted claims

- **"Confirmed" on an interpretation.**
  - At s5 (v1) the model wrote "the temperature dependence of pack resistance cannot be attributed to temperature
    alone, because state of charge and launch length also differ" and labelled it confirmed.
  - It cites a confirmed fact, so the tier check passes. But the sentence is the model's own inference, and the cited
    fact does not mention state of charge.
  - The verifier checks citations and tiers, not whether a sentence follows from what it cites.
- **Reported results used as facts.** The accepted claims about the engineers' findings are honest about being
  "reported", but they cite telemetry facts (f1, f2) for content that came from the step's reported result. The
  citation is decorative.

No accepted claim invented a number, and no accepted output eliminated a hypothesis.

## Changes needed before claiming the AI judge works

These are not made yet, and they belong in a new verifier version, not in this run:
- Give each engineer report (earlier steps' results and this step's result) a fact id with tier "reported".
- Accept eliminations backed by an earlier reported result when that fact is cited.
- Add a support check: a claim marked "confirmed" may only restate its cited fact, with numbers and entities taken
  from it. An interpretation is at most "inferred".
- Re-run, and have a human grader blind to the verifier's verdicts read every accepted claim against its cited
  facts. Report how many accepted claims the human finds unsupported.
