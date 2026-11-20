# dynamics-eval-v3: matched before/after evaluation of dynamics-v3 (preregistration)

**When written.** Written and committed before any v3 run. The code is frozen at tag `dynamics-eval-v3-frozen` (branch
`dynamics-v3`); the harness (`evaluate.py`) refuses to run from any other code. The set is drawn after the tag with
os.urandom. The key's SHA-256 is committed in `SEALED.md` before the first run. The key is kept outside the repository.

**Founder's request (2026-11-18).** Prove that v3 improves the complete autonomous investigation, not only the
deterministic engine.

## What changed: dynamics-v3, watch-v2, anticipate-v3, inquiry-v4

1. **Materiality is evidence with uncertainty, not an exclusion law.** Every share is judged on an interval, estimate
   ± Z·se, across the baseline choices.
   - MATERIAL (counted, or a flag raised) when the whole interval is at or above 1/2.
   - IMMATERIAL (dismissed) only when the whole interval is below 1/3.
   - Otherwise UNDETERMINED: the test returns no result, and the watch reports "possible" without flagging.
   - Reason for the asymmetry: the 1/2 factor is a design choice, and a false exclusion removes a true cause.
2. **Load-coupling growth** (`coupling_growth`). At every driver step, one member's change minus its peers' change is
   regressed on the driver change, with a term that grows over the span.
   - The growth is compared with the group's own coupling.
   - It is material when its interval is at or above 1/2.
   - It becomes a watch flag of its own, and generated test T3.
3. **Intermittent measurement faults.**
   - **Jump-and-return transients** with no driver step, counted per member against its peers. The driver steps are
     shifted by each member's measured response lag, which handles timing skew.
   - **Limit reached by one unreturned jump** with no driver step (`event_on_jump`).
   - The transients become a watch flag. Both signatures feed generated test T4 (`sensing_signatures`).
4. **Static response.** A domain library may declare that a group's members follow the present driver (`response`:
   static or integrating).
   - For a static group, the far member's relation to the driver is fitted without a time term and read over the late
     span.
   - Its materiality is its share of the limiting member's relation change at the same driver.
   - This fixes the robot `common` regression.
5. **Recorded protection reason.** This is a new op, `recorded_reason`, plus generated test T5. Where the profile binds
   a decoded reason or fault-code state, the domain library says which labels mean that the group's own limit
   protection acted. That contradicts an external initiation. There is no result when no such role is bound.
6. **inquiry-v4: every executed test updates the assessment.**
   - An exploration whose computation equals a registered, untested prediction is that prediction's test. Its
     registration and hash are kept, and the test is linked to the exploration.
   - Post-hoc tests update the assessment, labelled post-hoc, and never separate.
   - `assessment_after_tests` (prior status → weakened / supported / consistent / untested, with provenance) is in
     every investigation record, including the deterministic fallback.
   - A class pair counts as unresolved only if no discriminating test separated it.
7. **Dynamics findings.** Veyra writes plain statements from the computed quantities: the limiting member's rate, spread
   growth, lead before the event, own vs shared share, the load relation, sensing signatures, the recorded reason and
   the next check. They are given to the agent and kept in the record. The prompt asks the agent to report them and to
   reconcile them with the other causes.

Unchanged: the model (claude-sonnet-5-5 via Claude Code), the coverage strategy, the turn budget, the Safety Gate and
the physical-action rules.

## Developed on, and developer interventions (reported as such, never as autonomous findings)

**Development data:**
- unsealed synthetic seeds 5001–5004 and 6001–6004 (all 18 classes, both domains);
- v2's aggregate engine results;
- the deterministic (no-model) v3 engine on CASE A.

**Informed by CASE A** (development regression, not blind):
- the asymmetric materiality band (the cloud session's lesson: the lowest cell's share was just under 1/2);
- the recorded-reason test (CASE A records a protection code);
- the unresolved-pair fix (CASE A's next check pointed at a pair another test had resolved);
- the prompt line asking the agent to report the group's dynamics (CASE A's targets).

**Domain knowledge added by the developer:**
- `response` (static or integrating) per domain group;
- reason roles and label patterns per domain.

## Sets: one fresh sealed key

- **Engine:** 6 seeds × 9 classes × 2 domains = 108 cases, deterministic, no model.
  - Run by the **v2 code**: tag `anticipate-eval-v1-frozen`, which is dynamics-v2 plus watch-v1, the code the cloud
    evaluated.
  - Run by the **v3 code** on the same cases.
  - The watch is read at as_of = 0.5, 0.75, 0.9 and 1.0 of the time to the event (for controls, of the record).
- **Agent:** 1 seed × 5 event classes × 2 domains = 10 cases.
  - **v2 agent:** branch `signal-dynamics-v2`, backend equal to `dynamics-eval-v2-frozen`.
  - **v3 agent:** this tag.
  - Both use the same model, profiles, strategy and turn budget.
  - Runs are interleaved per case, so a usage-limit stop affects both alike.
- **CASE A:** the v3 agent runs 2 times, under the customer's recorded permission. Both runs are compared with the two
  independent v2 runs that already exist (cloud and Mac). This is a development regression and never evidence of
  generalisation.

## Measures (all fixed now)

**Engine.**
- Watch: component faults flagged, by mechanism, at each as_of.
- False alarms: flags on the 72 non-component cases (healthy, noise, timing, common, external) at each as_of.
- Lead: median earliest-flag time before the event.
- Investigate:
  - truth in the top set (survived, else alive);
  - false exclusions: the truth contradicted;
  - false certainty: a single answer that is wrong;
  - mean top-set size;
  - the next check addresses an unresolved pair containing the truth.

**Agent.**
- Truth in the agent's top tier.
- False certainty: one class named, and it is wrong.
- Mean top-tier size.
- The next check addresses the truth.
- The truth's `assessment_after_tests` label (v3 only).
- Latency: median seconds.
- Cost: USD and model calls.

**CASE A.**
- The six preregistered targets of dynamics-eval-v1 (`case_a_targets.py`, unchanged).
- The `assessment_after_tests` labels of L1 (group-wide), L2 (one cell) and L3 (external).
- The next-check ids.
- Seconds and cost.

## Decision rules (fixed now)

**v3 is CONFIRMED as an engine improvement only if, on the fresh set, against the v2 code on the same cases:**
- (a) false alarms ≤ v2's at every as_of;
- (b) at as_of 1.0, the load and sensing faults flagged are more than v2's, and drift faults flagged ≥ v2's;
- (c) false exclusions (truth contradicted) are fewer than v2's;
- (d) truth in the top set ≥ v2's;
- (e) false certainty ≤ v2's.

**Agent comparison.** Reported as measured. With 10 cases it shows direction only.

**Founder's question on CASE A.** "Can Veyra now autonomously discover the physical deterioration the independent assessment found,
reconcile it with the SOC accounting anomaly, and make a better next engineering decision than the frozen Agent?"

YES only if BOTH v3 runs meet all of:
- **(i) discovery:** T1 (min-cell acceleration), T2 (spread growth rate) and T4 (how early before protection);
- **(ii) reconciliation:**
  - T5 (cell vs pack level);
  - L2 not weakened, and L3 not standing above L2;
- **(iii) better next decision:** T6, plus a next check whose ids include a record that separates the pair the record
  leaves unresolved.

Otherwise the answer is no. The report shows, per target, where the reasoning failed and what evidence was missing.
Both v2 CASE A runs are scored the same way, for the before/after.

## Limits stated now

- **Synthetic.** Everything except CASE A comes from the generator the design was developed on (fresh seeds only).
- **CASE A is development data.** Several v3 changes were informed by it (listed above).
- **No validated real-world false-alarm rate.**
- **Design factors.** The materiality factors 1/2 and 1/3 are design choices, not fitted.
- **No Alloy comparison.** Nothing measures Alloy.
