# Blind investigation replay: results

Scored under `PROTOCOL.md` (frozen 2026-11-06, before the first run). Raw output: `results/<case>.json`.
Reproduce: `python research/investigation_benchmark/run.py`.

## Run 1: integration on the synthetic shuttle case

**This case is synthetic.** The log was generated, and the walkthrough, hypothesis space and truth were written by the
Veyra team. It shows the harness works end to end. It is not evidence that Veyra decides better than engineers.

### Setup

| | |
|---|---|
| Decision points | 5: 10-20 (report, s1), 10-21 (s2), 10-22 (s3), 10-23 (s4), 10-29 (s5) |
| Data Veyra had | 10-20 to 10-28: only the 10-09 snapshot (1,091 rows, 11–14 days old). 10-29: both snapshots (3,277 rows). |
| Isolation | Veyra ran in a separate `python -I` process. It saw only the frozen pack, and it refuses a pack that holds any other file. |
| Leakage audit | 0 of 5 points voided. All 4 injected-leak tests caught. |
| AI verifier | All 6 scripted tests behaved as expected. |

The injected-leak tests:
- a later row
- a later date in the header
- an outcome word in the header
- a sealed phrase that survives the freeze (the audit flags it, which voids the point)

The verifier tests:
- rejects a claim with no citation
- rejects a citation to a fact that does not exist
- rejects a claim above its evidence tier
- rejects eliminating a supported hypothesis
- accepts a valid claim
- accepts eliminating a hypothesis the tools weakened

Problems found in the package itself:
- s2's result ("it stopped again on 28 Oct") only became known on 10-28. The harness hides it from s3 and s4.
- s3's stated reason ("the inverter swap didn't fix it") assumes that result 6 days early. The walkthrough's dates are
  inconsistent. For a real case this would go back to the engineers.

### Metrics

**M1. Time to first useful hypothesis**

| | Result |
|---|---|
| Engineers | s5, day 9 (10-29), first step to target the FR connector |
| Veyra | **not met at any decision point** |

Veyra fails M1 because the frozen rule needs at most 3 open hypotheses. Veyra had 4 (contact, conductor, inverter,
battery), or 5 by 10-29 (plus HSA). It also needs the next check to separate the main cause from all of them. The
resistance check separates contact from cable and inverter, but cannot touch battery or HSA. Battery and HSA are true
contributing factors, so the rule penalises keeping true factors open. That is how the rule was frozen, and it is
reported as written.

**M2. Eliminations**

| | Eliminated | Right? |
|---|---|---|
| Engineers | inverter (s2, part swap) | ✓ not involved |
| Engineers | battery (s3, SOC and cell voltages normal) | **✗ contributing factor** |
| Veyra | FR voltage sensor (weakened, inferred) | ✓ not involved |

**M3. Checks**

- **Engineers (recorded):** 5 steps to the cause (4 tested a hypothesis), 515 min, €1,860 in parts.
- **Veyra (simulated, not converted to time or money):** 1 check, a four-wire segment resistance at the depot (cost tier
  1). It eliminates cable and inverter and leaves contact plus battery (plus HSA at 10-29). It never narrows to the
  main cause alone.
- Not simulated: data requests for HSA (a driven launch with HSA on and off, tier 2) and mechanical drag (tier 1).

**M4. Truth retained**

- The main cause stayed open at all 5 points.
- Battery was supported at all 5.
- HSA was not identifiable until 10-29: HSA was never active in the 10-09 data.

**M5. Next-check value**

The recommended check at every point was the segment resistance.
- It keeps the main cause and eliminates 2 wrong hypotheses.
- Expected open after it: 2.0, or 3.0 at 10-29.
- It does not mislead: the truth's fault is not load-only. If it were, the at-rest check would read normal, and the
  recommendation already carries a measurement under load as the follow-up.

### Avoidable work

**Verified avoidable: none, 0 min, €0.**
- No step's target was contradicted by a confirmed fact recorded within a day of the step. The only data before 10-29
  was 11 to 14 days old.
- The hypothesis space has no engineer-approved exclusion rules.

**Estimated, with no hours or money claimed:**
- s2, the inverter swap (recorded 240 min and €1,800). A cheaper check that separates inverter from contact and cable
  existed: the segment resistance, cost tier 1. This is at the inferred tier, because the data was 12 days old.

**Correction to the earlier debug replay (v1).** Using full-log hindsight, v1 labels s3 (the battery check)
"avoidable". The blind judge does not.
- At s3, the data Veyra had pointed toward battery: pack current crossed the BMS limit.
- Battery is in fact a contributing factor.
- v1's label is wrong, and the replay shown in the product today still says it.

### Wrong eliminations

- Engineers: battery at s3. This is a reported result, but the SOC and cell-voltage check at the depot cannot show
  cold-pack power limiting.
- Veyra: none.

### Unsupported conclusions

- None found by the audit:
  - every confirmed fact cites lines
  - no "weakened" was presented as confirmed
  - nothing was marked verified without a confirmed fact
- AI claims rejected: only in the scripted tests. No live model was run in this integration.

### Where Veyra did worse than the engineers

1. **HSA (contributing):** at s4 the engineers found it by reviewing the VCU 2.4.1 configuration. Veyra could not
   identify it from the data until 10-29: HSA was never active in the 10-09 snapshot. Veyra does not read
   configuration or release notes. The OTA note was in the log header, but no tool used it.
2. **M1 not met at any point,** while the engineers reached it at day 9.
3. **Veyra's path does not narrow to the main cause alone.** Contact and battery stay open after its check.

### Where Veyra did better

These are reported alongside the above, never netted against them.

1. **Battery.** At s3 the engineers eliminated battery, a true contributing factor, while Veyra's data still supported
   it. But the support was thin:
   - one launch crossed the BMS discharge limit by 1.1% (151.7 A against 150 A)
   - the data was 13 days old, so the support is inferred, not confirmed
   - Veyra may have been right for a weak reason
2. **The connector.** From day 0, Veyra had the true connector cause open and its separating check recommended. The
   engineers first targeted it on day 9. This is not a win under M1, for the reasons above.

### What this run does and does not show

- **It shows:**
  - the freeze, the audit, the isolation, the step judge, the verifier and the scorer work end to end
  - with stale data, the honest output is "estimated, not verified"
- **It does not show** that Veyra decides better than engineers. The case is synthetic, written by the people who built
  Veyra, and the hypothesis space and truth were not engineer-approved. That needs the real PIX cases, packaged as in
  `cases/README.md`.
