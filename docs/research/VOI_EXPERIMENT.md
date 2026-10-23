# Research experiment: decision-aware value of information and a stop rule

Status: **research only.** Nothing here is imported by the product; `next-action-v2` is unchanged.
Code: `backend/research/voi/` (`planner.py`, `from_case.py`, `assumptions.json`, `evaluate.py`, `sealed_truth.json`).
Tests: `backend/tests/test_voi_research.py`.

## The question

What should the engineer do next, and is more investigation worth delaying that action?

## Method (the four requirements)

1. **Real choices, constraints first.** Actions: monitor (no action), approved remote intervention, dispatch for
   diagnosis, safety escalation now; plus "get evidence X, then act" and "get X and Y together, then act" for every
   remote check the case offers. Before any cost is compared, a written safety rule removes actions (a kill switch,
   termination, crash or emergency stop in the evidence removes "monitor"; without one, "safety escalation" is excluded
   as disproportionate). Removed actions are listed with the reason, never scored.
2. **Every assumption is explicit** (`assumptions.json`, `voi-assumptions-v1`): prior weights from Veyra's own
   assessment (supported / open / less likely), loss ranges per action and kind of cause, check cost and waiting ranges
   by availability, likelihood ranges derived from what each check outcome supports or weakens. Sampled 1,500–3,000
   times under uniform, triangular, low-skewed and high-skewed distributions, and with evidence made 30% and 60% less
   informative. **"Best in X% of samples" is a property of these assumptions, not a probability about the world.**
3. **Frozen cases, truth hidden while planning.** Nine cases: QD-1, EX1, ALFA rudder, ANYmal injected knee, ANYmal real
   with no reported fault, and the four PX4 #26910 flights. Both planners answer from the case record; labels are
   opened afterwards. Scoring is **simulated** at the assumption midpoints.
4. **Learning from outcomes**: designed below, not implemented.

The planner reports, per option: how often it is best, how often its regret stays within 150 units ("never bad" if
≥ 90%), median regret, and gain over acting now. Stop rule: act when an action is never bad and no evidence option is
best in most samples with a median gain above the tolerance; otherwise gather; if nothing is robust, **abstain** and
say which assumptions need narrowing.

## Results

| Case (truth) | next-action-v2 first step | VOI planner |
|---|---|---|
| EX1 (network, synthetic) | ask for controller log + probe path | **act: approved remote intervention** (robust; correct) |
| ALFA rudder (actuation, real) | nothing to separate remotely → on site | abstain (dispatch best in 79%, never bad in 86%) |
| ANYmal real, no fault | no request (only brief events) — correct | abstain (leans to an unnecessary remote intervention) |
| ANYmal injected knee (drive) | ask for input–response baseline + controller log | abstain |
| QD-1 (drive, synthetic) | ask for drive log + controller log | abstain |
| PX4 #26910 × 4 (parameters, real) | ask for flight-controller log (already loaded) + input–response baseline | abstain |

What this shows:

- **The planner almost never chose to gather evidence**: in no case and no cost scenario was a check the robust
  first step. Under these likelihoods, the evidence Veyra's checks can return is weak (an outcome that is "neutral" to
  a cause splits 50/50), and waiting for it costs more than it saves when a cheap, reversible remote attempt exists.
- **It abstained on 8 of 9 cases.** The leading option flips with the cost assumptions: with a failed remote attempt
  twice as costly, or a wasted trip half as costly, it becomes "dispatch" on every case except EX1, whatever the true
  cause (including the PX4 parameter problem and the no-fault mission). The method is doing its job by refusing to
  pretend; the missing input is real operating costs, not more mathematics.
- **next-action-v2 asks for evidence on 7 of 9 cases**, and in simulation that delay rarely changes the action. Its
  one clear win is the no-fault case: its "only brief events" rule stops it, while the VOI planner has no notion of
  evidence quality and leans towards acting. (Simulated regrets use the same assumptions the VOI planner optimises, so
  they favour it; they are not evidence that it is better.)
- **Neither planner can find the PX4 answer**, because the decisive evidence (parameters differing from defaults)
  is in the log and no check reads it. "Approved remote intervention" without saying *which* intervention is hollow.

## Verdict

Not ready to integrate. Keep `next-action-v2`. Three things would make the planner useful, in this order:

1. **Better evidence options**, not better scoring: read-only checks over what the case already holds (parameter
   diffs, agreement between redundant measurements), which are cheap and can be decisive.
2. **Evidence quality in the prior** (the "precision" idea): weak or routine findings must move beliefs less; today
   the prior treats every "supported" part alike.
3. **Real cost ranges from an operator** for a trip, downtime and a failed remote attempt. Until then the planner
   should only say when the answer flips, and on what.

## Learning from outcomes (design, not implemented)

A case's outcome teaches the planner only through records like these, per run:

| Record | Why |
|---|---|
| Checks available, offered, approved, sent, performed, **not performed (with reason)** | Results exist only for performed checks; without the rest, the system learns only about what it already chose to do |
| Each answer with its quality (log covers the window? who read it? attached?) | A vague answer must count less |
| Action taken, by whom, and what happened next | Separates "the action worked" from "the diagnosis was right" |
| **How the cause was verified**: independent confirmation (part tested on a bench, fault reproduced), repair success only, maintainer's probable cause, reporter's belief, unknown | A successful repair is not proof of the proposed diagnosis; only independently verified cases update likelihoods |
| What the planner predicted (probabilities of each answer) before the answer came | Calibration (Brier score) can only be computed against predictions recorded in advance |

Updating rule: per check, outcome and verified cause, a Beta/Dirichlet count started from the written ranges;
cases verified only by repair success update with reduced weight or not at all; report the count behind every
learned number, and keep the assumption range when fewer than ~10 verified cases exist.
