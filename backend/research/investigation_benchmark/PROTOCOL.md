# Blind investigation replay: benchmark protocol

Frozen 2026-11-06, before the harness was run on any case. Changes after a run are listed at the end with the reason,
and never change how an earlier result is scored.

## Question

With the same information the engineers had at each moment, would Veyra have made better investigation decisions?
The aim is not to make Veyra look good in hindsight.

## Case package (one folder per case)

| file | content | visible to Veyra |
|---|---|---|
| `log*.txt` | decoded logs; every row timestamped | only rows recorded before the cutoff |
| `report.json` | the first report: symptom, machine, report date | yes, from the report date |
| `walkthrough.json` | the engineers' steps: what, why, when, time, parts cost, result, what decided it | only steps before the cutoff, and only as the engineers reported them then |
| `hypotheses.json` | the hypothesis space, written or approved by the case's engineers: id, description, keywords, how tools map to it, how to observe it, cost tier | yes |
| `truth.sealed.json` | main cause, contributing factors, how and when established, confidence | never; read only by the scorer |

## Decision points and the evidence freeze

- **Decision points:** the report date (t0), then the date of each engineer step.
- **Cutoff:** at each point, the cutoff is the start of that day.
  - Veyra receives log rows recorded strictly before the cutoff, the report, the hypothesis space, and the engineers'
    earlier steps as reported.
  - Assumption: logs recorded before a cutoff were retrievable at the time. A package can override this per file with
    `available_from`.
- **Header and labels:** a comment line or snapshot label is removed if any of these hold:
  - it carries a date on or after the cutoff
  - it contains an outcome word (replaced, returned, fixed, repaired, found, root cause, resolved, and their Chinese
    equivalents)
  - it refers to other snapshots
- **Isolation:** Veyra runs in a separate process that is given only the frozen pack. The scorer reads
  `truth.sealed.json` after Veyra has finished.
- **Leakage audit, on every frozen pack.** It fails if any of these is found:
  - a row timestamped on or after the cutoff
  - a comment with a later date
  - an outcome word
  - any distinctive phrase from the sealed truth (words of 5 or more letters, or 2 or more CJK characters, from the
    main cause and its location)

  A failed audit voids that decision point. The audit is itself tested by injecting leaks.

## Evidence tiers (applies to every statement, Veyra's and the judge's)

- **confirmed:** read directly from a cited line, or computed by a deterministic tool from cited lines. It is valid as
  of the time those lines were recorded.
- **inferred:** follows from confirmed facts plus a stated assumption, including any state carried forward more than
  one day from when it was recorded.
- **reported:** an engineer's statement, not checked against data.
- **not identifiable:** no available signal can show it.

Rules:
- A hypothesis may be marked eliminated only by a confirmed fact that contradicts it, or by an engineer's reported
  result (tier: reported).
- A deterministic tool's "weakened" counts as weakened, not eliminated.
- The AI may interpret, but each of its claims must cite fact ids from the deterministic tools or the frozen lines. Its
  tier can be no higher than the lowest tier it cites. A claim with no citation is rejected.

## Metrics (per case)

- **M1. Time to first useful hypothesis.**
  - *Veyra:* the first decision point at which the true main cause is among at most three supported, open hypotheses,
    and the next recommended check would separate it from the others.
  - *Engineers:* the first step whose stated target or reason names the true main cause's mechanism and location.
  - Reported as a decision-point index and in calendar days from the report.
- **M2. Eliminations.**
  - Every hypothesis each side treats as eliminated (engineers: result "not the issue"; Veyra: tool "weakened"),
    scored against the truth's main cause and contributing factors.
  - Incorrect eliminations are listed one by one.
- **M3. Checks.**
  - *Engineers (actual, verified):* steps until the main cause was found, recorded minutes and recorded parts cost.
  - *Veyra (hypothetical, estimated):* the number of recommended checks until a single main-cause candidate remains,
    and their cost tiers.
    - Each check's outcome is simulated from the truth using the check's own predicted outcome per hypothesis, so it
      assumes the check works as designed.
    - Checks with no predicted outcomes are listed but not simulated.
  - Veyra's path is never converted into hours or money.
- **M4. Truth retained.** At every decision point, is the main cause still among Veyra's open hypotheses, and which
  contributing factors are open, weakened or not identifiable?
- **M5. Next-check value.** At every decision point, Veyra's recommended check:
  - does its simulated outcome keep the main cause and eliminate at least one wrong open hypothesis?
  - how many open hypotheses are expected to remain (uniform over the open set)?
  - would the outcome mislead, by contradicting the main cause's predicted class?

## Avoidable work: verified vs estimated

- **Verified avoidable:** an engineer step whose result was "not the issue", where data available before it held a
  confirmed fact that its target was not the cause, recorded within one day of the step. It is counted with the
  engineers' recorded minutes and cost.
- **Estimated (not savings):** steps where a cheaper discriminating check existed, or where the data argued against the
  target at the inferred tier. These are listed, with no hours or money attached.

## Always reported

- Wrong eliminations, by either side.
- Evidence leakage: audit failures, and the result of the injected-leak tests.
- Unsupported conclusions:
  - statements above their evidence tier
  - eliminations without the required evidence
  - AI claims rejected by the verifier
- Where Veyra performs worse than the engineers:
  - a contributing factor the engineers found that Veyra could not identify from the data
  - more checks in Veyra's path
  - a decision point where Veyra had no useful hypothesis while the engineers did
  - any incorrect elimination by Veyra

## Runs

1. Integration on the synthetic shuttle case (`cases/shuttle-synthetic`). The data is generated, so this shows the
   harness works, not that Veyra works.
2. Then 5–10 real PIX cases packaged as above. The hypothesis space and the truth are approved by PIX engineers before
   Veyra is run.

## Changes after the first run

- After run 1 (synthetic), a "where Veyra did better" list was added to the report. It is reported alongside the
  "worse" list and never offsets it. No metric definition, threshold or tier rule changed.
- Implementation detail made explicit (it does not change the rules): a prior step's result is shown to Veyra only from
  the date it became known (`result_known`, or the latest date named in `decided_by`).
