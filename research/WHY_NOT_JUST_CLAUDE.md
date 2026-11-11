# "Why can't an OEM just give these logs to Claude?" — claims, tests, results

Rule for this file: a claim counts only with a test that could have failed, a stored result, and a script someone else
can re-run. Data: ALFA (CMU AirLab, CC0, doi:10.1184/R1/12707963), 47 real flights. Same model in every arm (Claude Code
CLI, alias sonnet). Last updated 2026-11-11.

## What we do NOT claim

**Veyra is not smarter than Claude at finding these faults.** Preregistered result (`alfa-ai-blind-v2/results.md`):
the same model alone, given the signals as a 1 Hz table, found 36/36 faults; Veyra v2 found 32/36; Veyra v1 12/36.
The model alone was also 3x faster. Any pitch built on "better reasoning" fails an independent retest today.

## Claims

| # | Claim | Test | Status | Result |
|---|---|---|---|---|
| 1 | Evidence a reader can check mechanically | Every quoted evidence item must be verbatim on the line it cites (`alfa-ai-blind-v2/quote_check.py`, no model calls) | Done, post-hoc | Veyra 213/213 items; model alone 13/180 (3 on the cited line). 47/47 model-alone reports carry at least one quote that is not in the data as quoted |
| 2 | No false alarms on healthy machines | Status `cause_probable` on the 10 no-failure flights | Done, preregistered, small n | Veyra 0/10; model alone 2/10. Says "no fault" plainly: Veyra 8/10, model alone 4/10 |
| 3 | A run can be reproduced by a third party without a model | `scripts/demo/verify_demo.py`: input hash, evidence sheet byte-identical, every tool call re-executed, every quote and number re-checked | Done on 2 flights | 4/4 checks pass (flight-30 pack) |
| 4 | Same case, same answer | 10 flights x 5 repeats per arm: agreement on fault / no fault, component, onset within 5 s | NOT RUN | — |
| 5 | The raw file, not our table | Arm "Claude Code + Python + the raw .bag" (labels stripped), no Veyra parser: found rate, wrong-signal errors, time, cost | NOT RUN | — |
| 6 | Faults that are not visible in a table (the physical gap) | Faults that need a physical model (e.g. resistance from voltage drop and current), Veyra with its physics checks vs model alone | NOT RUN, no data yet | — |
| 7 | The next check is the right one | Engineers rate the proposed next check blind (arm hidden); or replay closed cases and compare with the check that settled them | NOT RUN, needs closed real cases | — |
| 8 | Later cases get faster with the fleet's own history | Replay a fleet's cases in order, memory on vs off | NOT RUN on real data | — |

## Caveats a reviewer will raise (and they are right)

- Claim 1 measures *checkable*, not *true*. Most model-alone quotes are abbreviations of real rows ("49,...,1.5e+03"),
  not inventions. Veyra's number comes from a verifier that rejects a report and asks again; the model alone got one try
  and no verifier. That is the point of the claim (the system enforces it), but it is not evidence the model lies.
  Follow-up to preregister: are the *numbers* in each report true to the data?
- Claim 2 rests on 10 flights, all with prior exposure. 0/10 vs 2/10 is not statistically distinguishable.
- The "model alone" arm is not "give the logs to Claude": it was fed Veyra's own parsing, topic mapping and label guard.
  A raw ALFA bag is binary, 2–18 MB, ~35 topics, and contains the answer in `/failure_status/*`. Claim 5 tests the real question.
- ALFA faults are easy (a throttle at zero shows in any table). Nothing here tests the hard case in claim 6.
- One aircraft, four fault types, one model family. No claim beyond that.

## What an independent party needs

The repository at the tagged commit, the public ALFA download, a Claude Code login. `alfa-ai-blind-v1/` and
`alfa-ai-blind-v2/` hold the preregistrations (committed before the runs), runners, scorers, every per-flight record
with its input sha256, and the infrastructure failures kept aside.
