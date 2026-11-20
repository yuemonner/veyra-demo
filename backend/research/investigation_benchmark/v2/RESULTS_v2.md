# Blind investigation replay: results under protocol v2

Scored under `PROTOCOL_v2.md`. v1's results (`../RESULTS.md`) stand as reported, and a test checks that re-running v1
reproduces its scores. Raw output: `results/shuttle-synthetic-v2.json`.

## v2 run 1: integration on the synthetic shuttle case with synthetic artifacts

**This case is synthetic, and so are its artifacts.** The Veyra team wrote all of them:
- the VCU 2.4.1 OTA record and its calibration file
- the build records
- the service records
- a supplier bulletin dated after the case closed

Nothing here measures whether Veyra helps on real cases.

### Leakage

| Check | Result |
|---|---|
| Decision points voided | 0 of 5 |
| Log injected-leak tests | 4 of 4 caught |
| Artifact injected-leak tests | 6 of 6 caught |

The artifact tests cover:
- an artifact available after the cutoff
- an artifact whose text names a later date
- an intervention result shown before it was known
- an undated note with an outcome word
- a sealed phrase in an available artifact
- an artifact with no availability date

At every decision point, these were correctly kept out:
- the record of the connector repair (available 10-29, so excluded even at the 10-29 cutoff)
- the supplier bulletin (11-02)
- the swap's result, until 10-28

The hypothesis space's rule linking connector revision B to contact faults comes from that 11-02 bulletin. It was
withheld at all five decision points.

Two changes were made during this run, both recorded in the protocol:
- **Sealed-phrase check on artifacts.** The first attempt voided every point, because the connector's build record
  names the component "connector". Sealed phrases are now looked for in free text only.
- **Artifact citations.** v1's line-citation check flagged 5 confirmed configuration facts as "confirmed without cited
  lines". In v2 an artifact id counts as a citation, and these 5 flags were removed.

### What the artifacts changed (telemetry only → telemetry plus artifacts)

**HSA, a contributing factor:**
- Telemetry only: not identifiable until 10-29.
- With artifacts: supported from day 0. The released 2.4.1 calibration sets the HSA feed-forward limit at 95 N·m,
  above the cold BMS limit of 80 N·m, a comparison the space states. This is the same information the engineers found
  on day 3 (s4) by reviewing the configuration. In v1, Veyra missing it was the first "worse than the engineers" item;
  with the artifact it is gone.

**Recommended check:** the four-wire segment resistance (depot, cost tier 1) in both runs.
- Expected bits: 1.0 telemetry only, 0.74 with artifacts. HSA is now open, and the check cannot address it.
- In both runs it is safe: it never removes a true cause.

Unchanged by the artifacts:
- **Consequence:** the FR undervoltage protection (a consequence) is never held as a cause. Its trip matched the spec
  wherever it could be checked.
- **Frozen v1 M1:** still not met at any point. 4 or 5 hypotheses stay open, more than the 3 the rule allows.
- **M1-v2 (post hoc, written after seeing v1):** day 0 in both runs, against day 9 for the engineers. M1-v2 forgives
  keeping true contributing causes open. Because it was defined after seeing run 1, treat it with caution; the frozen
  M1 result above is the one of record.

### Decision quality (DQ)

Bits use a uniform belief over each side's open set. They are not converted into time or money.

| Check | Side | Bits | Removes a true cause? |
|---|---|---|---|
| s1, read codes | engineers | 0 | (gathers information) |
| s2, inverter swap | engineers | 0.19 | no |
| s3, battery SOC and cells | engineers | 0.22 | **yes: the battery, a contributing factor (unsafe)** |
| s4, HSA configuration | engineers | 0 | no (finds HSA partly responsible) |
| s5, FR connector | engineers | 2.58 | no (fault found) |
| segment resistance, every point | Veyra (simulated) | 0.74–1.0 of a possible 2.0–2.3 | no |

- **Unsafe reductions:** engineers 1 (s3), Veyra 0.
- **Consequence taken as the cause:** none on either side.

### Other v1 metrics on v2 evidence

These are the same as in v1:
- **Verified avoidable:** 0 min, €0.
- **Estimated:** s2 (inverter swap) only.
- **Wrong eliminations:** engineers 1 (battery), Veyra 0.
- **Unsupported conclusions:** none.

Still worse than the engineers:
- frozen M1 never met
- Veyra's simulated path never narrows to the initiating cause alone (contact, battery and HSA all stay open)

### AI step judge with a live model

See `../AI_JUDGE.md`. On v2 states (with artifacts) a live model was accepted on 2 of 4 steps, rejected on 2; on v1 states, 1 of 4. The AI judge is not yet working; the reasons, including false rejections caused by the verifier, are listed there.

### What this does not show

That artifacts help on real cases. Here, the artifact that mattered (the calibration file) and the rule relating it to
HSA were both written by the people who built Veyra. On real cases it depends on whether such a file exists, when it
was available, and whether the engineers state the rule before Veyra runs.
