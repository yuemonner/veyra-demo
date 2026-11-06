# Packaging a real case (for the 5–10 PIX cases)

One folder per closed case. Pick cases before seeing what Veyra says about them, and include cases where the engineers
found the cause fast: we need the cases where Veyra could do worse.

## Files

| file | who writes it | what |
|---|---|---|
| `log*.txt` | export, unedited | decoded logs with a timestamp on every row. Keep everything, including later logs. The harness freezes them. |
| `files.json` (optional) | engineer | `{"log2.txt": "2026-10-25"}`: the date a file could first be retrieved, if later than its rows (for example a log pulled from the vehicle only at a depot visit). |
| `report.json` | engineer | `{"machine", "report_date", "symptom"}`: the first report, in the words used then. |
| `walkthrough.json` | engineer interview | `{"currency", "steps": [...]}`, one entry per action, in order. |
| `hypotheses.json` | engineer, before Veyra runs | every explanation the team considered or should have considered: `id`, `name`, `keywords` (words a step would use for it), `maps` (how Veyra's tools relate to it, filled in with us), `observe` (how to check it if no signal shows it, with a cost tier). |
| `truth.sealed.json` | engineer, sealed | `main_cause` (an id), `contributing` (ids), `main_cause_text`, `location`, `established_by`, `established_at`, `confidence`, `load_only`. |

Fields for each step in the walkthrough:
- **`check`**: what was done.
- **`part`**: the part or system.
- **`kind`**: one of check, swap, repair or config.
- **`why`**: what they expected and why. Record this in their words at the time, not in hindsight.
- **`when`**: the date.
- **`minutes`**: the time spent.
- **`cost`**: the cost of parts.
- **`result`**: one of not_the_issue, partly, fault_found or inconclusive.
- **`decided_by`**: what settled the result.
- **`result_known`**: the date the result became known, if later than `when`. For example, a swap that only proved
  useless when the fault came back.

## Rules

- The hypothesis space and the truth are approved by the case's engineers before Veyra is run on the case. After that
  they are frozen.
- Don't write the cause into `report.json`, a step's `why`, or any log comment dated before it was found. The audit
  will void the decision point if it finds a sealed phrase.
- Unknown fields stay empty. "Not recorded" is a result, not something to guess.
- `excluded_when_relation_holds: true` may be added to a hypothesis only if an engineer agrees the data relation rules
  it out. Without it, nothing counts as verified avoidable.
- For each step, also ask: "What would have changed your mind?" Store the answer in the knowledge candidate when the
  engineers approve it. Veyra does not learn from these yet.

Run with `python research/investigation_benchmark/run.py cases/<folder>`, then copy the summary into `RESULTS.md`
under a new run heading. Never re-run with changed rules on a case that has already been scored.
