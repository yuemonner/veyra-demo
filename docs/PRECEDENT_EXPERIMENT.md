# Does one closed case help the next one? Precedent experiment on real ALFA flights

Run 2026-10-21. Script: `scripts/validation/precedent_experiment_alfa.py`. Results: `docs/results/alfa_precedent_results.json`.
Rules under test: `precedent-rules-v1` (similarity), `next-check-policy-v4` (ranking), case closure v2. The rules were written
on the synthetic EX1 story and were **not tuned on these flights**.

## The question

When an engineer has closed an earlier case, does Veyra, on a new case that looks similar:

1. find the similar earlier case,
2. show which check settled it,
3. say what is the same and what is different, and
4. **not** apply the earlier cause to the new case?

## Data and the one assumption

CMU AirLab ALFA (Keipour, Mousaei, Scherer, IJRR 2021, doi:10.1184/R1/12707963; CC0 per README): 47 real flights of one
Carbon Z aircraft, 36 with a fault injected by the researchers (engine, aileron, elevator, rudder), 11 without.

ALFA has no engineers and no inspections. **The dataset's fault label stands in for the engineer's conclusion** when a case
is closed (`cause_confirmed`, basis "inspection", note "ALFA label stands in for an inspection"). Every closure says so.
The check that "settled" each case is the same for every closure (on-site actuator inspection), so point 2 is tested
only as a mechanism here, not as evidence that the right check is learned.

## Protocol

- **A. Blind runs.** Every flight becomes a case; its first 20 s are declared known-good; all reference windows are declared
  before any investigation (the same protocol as `scripts/alfa_benchmark.py`). Every case is investigated once, blind.
- **B. Leave-one-out.** For each flight, every *other* fault flight is a closed case. Is the top precedent the same kind of fault?
- **C. Replay in the order flown** (the main result). The 36 fault flights are taken in date order. Each is investigated
  through the real engine path (store, ledger, check ranking) with only the cases closed so far, then closed. This is how
  experience builds up on a fleet. For every flight, its hypotheses, triage and root cause are compared with its blind run in A.

## Results

### Replay in the order flown (C)

| | Flights |
|---|---|
| Fault flights with at least one earlier closed case | 35 |
| A similar earlier case was shown | 22 |
| Top earlier case had the **same kind of fault** | **19 of 22** |
| Top earlier case had a **different kind of fault** | 3 of 22 |
| Nothing shown | 13 (11: the engine flagged nothing in that flight; 2: it flagged something no earlier closed case had) |
| Hypotheses, triage and root cause identical to the blind run | **36 of 36** |
| Root cause still "unconfirmed" after the precedent was shown | 36 of 36 |
| Check that settled the earlier case moved to the top of Next checks | 22 of 22 |

### Leave-one-out (B)

Top-1 same kind on 21 of the 25 fault flights where something was shown (84 %); chance from the fault mix is 46 %.
All 8 aileron, 2 elevator and 4 rudder flights got a same-kind precedent among those shown; engine failures did worst
(9 of 23), mostly because the engine flags nothing on 11 of them. On the 11 flights without a fault, 1 was shown a
precedent: `2018-09-11-15-05-11_2_no_failure`, the one flight the ALFA benchmark also flags.

### Against the four points

1. **Finds the similar earlier case: yes, when the engine has flagged something.** 19 of 22 top precedents were the same
   kind of fault. Example: the first right-aileron flight (2018-09-11 17:55:30 #1) was shown the earlier left+right aileron
   flight (14:52:54) at similarity 1.0: both have the two aileron outputs mirroring each other.
2. **Shows which check settled it: yes, as a mechanism.** That check moved to the top of Next checks with the reason
   ("Settled a similar earlier case: …"). Because every closure in this experiment names the same check, this says nothing
   yet about whether Veyra surfaces the *right* check. That needs real closures.
3. **Says what is the same and what differs: yes, but often there is nothing to tell apart.** 16 of the 22 matches rest on
   a single finding, and for 18 of 22 the top precedent has no difference in what the engine flagged. The page now says so:
   "Nothing the engine flagged tells these two cases apart … the earlier cause is not evidence for this one."
4. **Does not apply the earlier cause: yes, 36 of 36.** The precedent is shown next to the case; the case's own
   hypotheses, owner and severity, and its "unconfirmed" root cause, are unchanged.

### Where it went wrong, and why point 4 matters

The 3 wrong-kind top precedents:

| New flight (truth) | Top earlier case shown | Similarity | Shared finding |
|---|---|---|---|
| 2018-09-11 14:41:51 elevator | engine failure 2018-07-30 16:39:00 #2 | 1.0 | pitch stopped following its command |
| 2018-09-11 15:06:34 #1 rudder | engine failure 2018-09-11 11:56:30 | 0.667 | roll stopped following its command (the rudder output stuck is listed as "only in this case") |
| 2018-09-11 17:27:13 #2 both ailerons | engine failure 2018-07-30 17:36:35 | 1.0 | roll stopped following its command |

An engine failure and an elevator failure both make the aircraft stop following its pitch command. From what the engine
flags, the two cases are identical. Had the earlier cause been applied, the elevator case would have been sent to
propulsion. It was not: the case's own evidence still decided, and the shared-finding note says the match is not evidence.

## What changed because of this experiment

- **Ties.** With one-finding signatures many earlier cases tie at 1.0, and the first version broke ties by internal id, so
  the three shown were arbitrary (they changed between runs). Ties now go to the same machine, then the strongest evidence
  behind the earlier conclusion, then the most recently closed; the number not shown is reported (`not_shown`).
- **Protocol bug found and fixed.** The first replay declared reference windows while investigating, so the machine
  baseline grew during the run and one flight's own result changed (a new finding appeared). That was the experiment, not
  the precedents: with all windows declared first, 36 of 36 are unchanged. Earlier result files are kept on the Mac
  (`datasets/alfa/veyra/precedents/*_v1…v3*.json`).
- **The page** says when a match rests on one finding and nothing tells the cases apart.

## Limits

- Labels stand in for engineers' conclusions; the settling check is the same for all. Point 2 needs real closures.
- One aircraft, 36 faults, four fault kinds, faults injected in software. Not a fleet.
- Similarity uses only what the engine flagged. Where the engine flags nothing (11 engine failures) there is nothing to
  match, and where it flags one thing, different causes look the same.
- Open question for the ranking policy: the earlier case's check (an on-site inspection) now ranks above the free
  automatic checks. That may be right when the earlier case was settled that way, or it may cost a trip. To be decided
  with an engineer.
