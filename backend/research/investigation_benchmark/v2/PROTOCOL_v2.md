# Blind investigation replay: protocol v2

Written 2026-11-06, **after** run 1 under protocol v1. v1 (`../PROTOCOL.md`), its harness (`../run.py`) and its results
(`../RESULTS.md`, `../results/`) are preserved unchanged. Re-running v1 must reproduce its scores, and a regression test
checks this. Nothing in v2 changes how a v1 result was scored.

Because v2 was written after seeing a v1 result, anything in it that would have changed that result is marked
**post hoc** and is reported next to the v1 metric, never instead of it.

## What v2 adds

1. **Evidence beyond telemetry:** software versions, controller configuration, hardware revisions, intervention
   history and notes. Each is an artifact with an availability timestamp.
2. **Causal roles** in the sealed truth: initiating, contributing, consequence and not involved.
3. **Decision-quality metrics:** how much each check narrows the explanations without dropping a true cause.

## Artifacts (`artifacts.json`)

Each artifact has:
- `id`
- `kind`: software_version, controller_config, hardware_revision, intervention or note
- `available_from`: required. The moment the people investigating could first have read it. A package with any
  artifact missing this field is invalid.
- `valid_from`: optional. When the state took effect.
- kind-specific fields:
  - **software_version:** component, version
  - **controller_config:** component, version, params
  - **hardware_revision:** component, revision
  - **intervention:** when, action, part, result, result_known, decided_by
  - **note:** text

### Same leakage rules as logs

- **Availability:** an artifact enters a decision point only if `available_from` is before the cutoff (the start of
  that day).
- **Edited after the fact:** an artifact whose text names a date on or after the cutoff is dropped, because it was
  edited afterwards.
- **Undated notes:** a note containing an outcome word is dropped.
- **Intervention results:** a result is shown only from `result_known`, or from `when` if there is none. Before that it
  is "pending", and `decided_by` is hidden.
- **Audit:** every frozen artifact set is audited for:
  - an artifact not yet available
  - a later date
  - an intervention result shown before it was known
  - a sealed phrase

  Any of these voids the decision point.
- **Injected-leak tests** cover each rule.

### Rules in the hypothesis space have dates too

A hypothesis space written after a case closes can carry later knowledge back into it, such as a supplier bulletin
or a recall list. So any artifact rule in `hypotheses.json` may carry its own `available_from`, and it is applied only
at cutoffs after that date. Rules that relate an artifact to a hypothesis:
- **config:** compares two released parameters with a stated operator
- **version:** the hypothesis needs a version or later
- **hardware:** the installed revision is in an affected list
- **work_on:** a recent intervention on the component

The tools apply only these rules. They never invent one.

### How artifact facts count

- A **confirmed** artifact fact that **supports** a hypothesis makes a not-identifiable or no-support hypothesis
  supported. Example: a released calibration value above a released limit.
- A **confirmed** fact that **argues against** a hypothesis weakens it, unless confirmed telemetry supports it.
- **Inferred** facts are attached as context and never change a status. These are a version window, an affected
  revision without an engineer-approved rule, and work done on a component shortly before.
- Nothing is eliminated by an artifact.

## Causal roles (`truth.sealed.json` → `roles`)

| role | meaning |
|---|---|
| **initiating** | the fault that started the chain |
| **contributing** | a condition without which the failure would not have happened |
| **consequence** | happened because of the chain, not a cause (for example a protection that tripped as designed) |
| **not involved** | |

The engineers approve the roles together with the truth.

## Metrics

### v1 metrics, unchanged

M1–M5 and avoidable work are computed exactly as in v1, on the v2 evidence. They are reported both for telemetry only
and for telemetry plus artifacts, so the effect of the artifacts is visible.

### Decision quality (new)

- **DQ1. Bits per check.**
  - Uncertainty removed, `log2(open before) − log2(open after)`, with a uniform belief over the open hypotheses.
  - *Engineers:* their open set is the whole space minus their own reported eliminations. A step's realised bits come
    from its reported result: "not the issue" removes its targets; "fault found" narrows to the target.
  - *Veyra:* expected bits of the recommended check, from its predicted outcome classes (hypotheses without a
    prediction stay open in every outcome), and realised bits with the outcome simulated from the truth.
  - Bits are never converted to hours or money.
- **DQ2. Unsafe reduction.** Any check, by either side, that removes an initiating or contributing cause. Its bits are
  reported as unsafe.
- **DQ3. Consequence taken as the cause.** A step that targets a consequence and reports it as the fault or as partly
  responsible. A Veyra state that holds a consequence as a supported cause.
- **DQ4. Role coverage per decision point.** Which initiating and contributing causes are open, weakened or not
  identifiable in Veyra's state.
- **M1-v2 (post hoc; reported next to M1, never replacing it).**
  - *Veyra:* the first decision point where the initiating cause is supported, Veyra's recommended check is safe (DQ2),
    and after its simulated outcome every hypothesis left open is a true cause, or has no prediction and a listed data
    request.
  - *Engineers:* as in M1.
  - It exists because v1's M1 counts true contributing causes held open as a failure, and that was seen in run 1.

### Always reported

Everything listed in v1, plus:
- artifact leakage
- artifact rules that were withheld because they postdate the cutoff
- the telemetry-only versus artifacts comparison

## AI step judge

The verifier rules are as in v1. The AI judge counts as working only when a live model has been run under the
verifier on the benchmark's own judgments. Results are reported per claim: accepted, rejected and why, and the number
of attempts needed. The scripted tests only show that the verifier works.

## Runs

- **v2 run 1:** integration on `cases/shuttle-synthetic-v2`. This is the synthetic case with synthetic artifacts, all
  written by the Veyra team. It tests the harness, not Veyra.
- **Then:** the PIX cases with real artifacts. The `available_from` dates come from the source systems (OTA server,
  calibration release, build records, service system), not from memory.

## Changes after the first attempt

- **2026-11-06, first v2 attempt.** Every decision point was voided because the build record of the FR connector
  names the component "FR HV connector", and "connector" is a sealed phrase.
  - From then on, sealed phrases are looked for in an artifact's free text (text, note, action, decided_by, source),
    not in its identity fields (component, part, revision, version).
  - Reason: an inventory record naming a component is not the answer, just as the hypothesis names in the space are
    not.
  - Risk accepted: a leak written into an identity field would not be caught. Package reviewers check identity fields
    by hand.
  - No result had been scored when this changed.
- **2026-11-06, first scored v2 run.** v1's "confirmed without cited lines" check knew only log lines, so it flagged
  confirmed configuration facts.
  - From then on, a fact derived from an artifact cites the artifact's id, and that counts as a citation. This is the
    meaning the artifact section above intended.
  - The flags this change removed are listed in RESULTS_v2.md.
- **Same run.** The leak audit did not flag an outcome word in an undated note. The freeze already removed such notes.
  The audit now flags them too.
