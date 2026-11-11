# ALFA evaluation v2 — Veyra v2 against the frozen v1 baseline and against the same model alone (preregistered)

Written and committed before any v2 or model-alone run. Follows `../alfa-ai-blind-v1` (baseline, frozen at e9bc6ce).

## What changed since v1 (all post-hoc, all disclosed)

Commit 30f459b, `backend/app/agent/recording_tools.py` and `backend/app/ingest/bags.py`: attitude taken from the
commanded/measured pair's own convention; tracking error with the steady lag removed; SHIFT lines; held-to-end and frozen
outputs; ROLE lines; start-up candidates labelled and half-weighted; routine steps down-weighted; heading-like angles not
level-tested; a `compare` tool; prompt guidance on how a fault shows in a recording.

**How blind this is.** Thresholds and logic were designed and checked on the 26 development flights' data only
(`../alfa-ai-blind-v1/split.tsv`). But the developer read the v1 *results* of every flight, validation included, when
writing the v1 post-hoc review, and the list of failure modes came from all of them. So the 21 validation flights are
"not used for design or tuning", not "never seen". No flight outside ALFA has been evaluated; a claim beyond this
aircraft and these four fault types is not supported by this evaluation.

## Arms (same 47 flights, same neutral aliases, same request, same model alias)

1. **Veyra v1**: the stored v1 results, re-scored with the measure below (not re-run).
2. **Veyra v2**: the investigator at 30f459b (run refuses to start if the evaluated files differ).
3. **Model alone**: the same model (Claude Code CLI, alias `sonnet`), one call, no Veyra evidence sheet, tools or verifier.
   It receives the same mapped signals as a table sampled at 1 Hz (every series Veyra extracted, 3 significant figures),
   the logged events, the same request, and the same report schema (`submit_report`). Prompt in `run2.py`. This is "what a
   capable general model does with the same data in its context", not a tuned competitor.

## Measure (v1's rubric flaws fixed; applied identically to all three arms)

From the report only. A **statement** is one sentence of the headline, the explanation or a *leading* hypothesis
(area + claim); or one evidence line with its note; or one listed event. Onset and failed components come from
`/failure_status/*`, read after each run.

- **FOUND** = status is not `no_failure_found` AND at least one statement contains BOTH
  - a time within [onset − 3 s, onset + 15 s] (a number written `t=N`, `t≈N`, `N s`, `at N`, `from N`, `after N`, `by N`), and
  - the failed part, its axis, or its output channel:
    aileron: aileron | roll | ch[0], ch[4], ch[5];  elevator: elevator | pitch | ch[1];  rudder: rudder | yaw | ch[3];
    engine: engine | motor | propulsion | thrust | throttle | ch[2].
    (Channel map: ArduPlane convention, confirmed by the recordings' ROLE lines; written `ch[3]`, `ch3`, `ch 3`, `channel 3`.)
  This is the headline number: the report says, in one place, when and where.
- **FOUND-strict** = the same with the part's own word or channel only (no axis word).

Disclosure: a first draft of this measure (time and name anywhere in the report, not in one statement) was applied to the
stored v1 results before this file was committed and gave 20/36; it was tightened to the one-statement rule because it
passed reports that mention the axis in passing. No v2 or model-alone run existed when this was fixed.
- Healthy flights: **false alarm** = status `cause_probable`; **clean** = status `no_failure_found`.
- Per arm: FOUND / FOUND-strict on development, validation and all faulty flights; false alarms and clean on healthy
  flights (all development); tool calls, seconds, tokens.

Infrastructure failures (no model reply, usage limit) are rerun up to two times, then reported and excluded with their count.
The unlabelled flight is run and reported, not scored.

## What would count as "better than the model alone"

Veyra v2 FOUND on validation flights higher than the model alone by more than the exact binomial noise allows is the
claim we would make; with 21 validation flights only large differences are distinguishable, and the result is reported
with its counts whatever it is. Nothing is tuned after these runs; if v2 is not better, that is the result.
