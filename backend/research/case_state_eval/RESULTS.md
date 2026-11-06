# Case-page correctness: provenance, one case state, safety, observation impact

Run on 2026-11-06 with `python research/case_state_eval/run.py`. Raw output: `results.json`. Regression tests:
`tests/test_case_correctness.py`.

The two showcase cases:

- **Shuttle (synthetic, Chinese):** generated log, a recorded model investigation, and 5 recorded discussion rounds.
  The engineer's side of the discussion was scripted.
- **micro-ROS (real, English):** a real log from a public GitHub thread, 2 recorded investigation rounds, and 4
  recorded discussion rounds. The engineer's side was scripted from what the thread reports. This case was already
  used in evaluation batch 1, so it is not a blind test.

## 1. Claim provenance

Every explanation now records what its current status rests on:
- the data
- an engineer's report
- Veyra's inference

None is called a verified cause without a verified outcome. A verified outcome is a record that carries data: a fix
that removed the fault in data recorded after it, or a reproduction recorded with its data.

**Shuttle**
- The FR terminal became "leading" in round 4 on the engineer's bench measurement: an extra 0.9 V drop, oxidation,
  low torque. The log cannot show any of that. Its status now says it rests on the engineer's report, and its
  standing is "leading, not verified".
- The walkthrough's conclusion used to be shown as "最后查出来的原因" (the cause that was found). It is now shown as
  "工程师报告的结论（未验证）" (the engineers' reported conclusion, not verified), and it is flagged.

**micro-ROS**
- "Board Ethernet / lwIP DMA" leads because of the engineer's debugger and tcpdump result. The agent-side log cannot
  observe the wire, so its status rests on the engineer's report.
- The thread later reported the fix worked: the reporter said it was a cache coherence issue, and a second user said
  the patch worked on a Nucleo F7. Recorded as an engineer's closure ("confirmed, fix resolved it"):
  - the case stays at "probable" on the verification ladder
  - the closure is flagged as reported, not established
  - there is no post-fix log in the case
- Veyra's leading explanation matches the cause the thread later reported. That agreement is itself only reported.

**Ladder change**
- "Verified" now needs a record with data.
- A closure confirmed by an inspection note, or a fix that "held" on the engineer's word, is shown as
  engineer-reported.
- A reproduction recorded without data still reaches "reproduced", labelled "engineer reported, no data attached".

## 2. One canonical case state

`GET /engine/cases/{id}/state` returns one versioned state, built from the append-only record. The page summary, the
counts, the status, the next check and the latest discussion round all read from it.

**Before.** On the shuttle page, three conclusions were shown at once:
- The investigation header: "原因未确定" (cause not established), with HSA as the main cause and 4 of 4 explanations
  still possible.
- The discussion panel: round 5, with HSA and the FR terminal both leading.
- The replay: "最后查出来的原因" (the cause that was found), showing the engineers' conclusion.

**After.**
- The state comes from discussion round 5 of 5: cause not established, 3 of 5 explanations still possible (2
  leading, 1 open, 2 weakened).
- The discussion panel reads the same hypotheses; a test checks that they are identical.
- The investigation round is listed under "Earlier conclusions" and marked as superseded by discussion round 1.
- Its own next action is labelled as superseded.

**micro-ROS.** Investigation round 2 said "probable cause" (Ethernet DMA). That is now history, superseded by the
discussion. The current status is "cause not established" because round 4 reopened a buffer/pool difference (h6).
Previously, the page said "Round 2 of 2 · where the case stands now" under the old result.

**Versioning.** The state version moves with every relevant record. An engineer input that no assessment has answered
yet is listed as not yet assessed. It does not change the current findings.

## 3. Safety-aware experiment planning

Every proposed check gets one of these hazard classes:

| Class | Hazardous? |
|---|---|
| offline | no |
| bench | no |
| low-risk physical | no |
| de-energised high-voltage work | yes |
| machine in operation | yes |
| live high voltage | yes |
| deliberate fault reproduction | yes |

"High voltage" means more than 60 V DC, taken from the case's data. The shuttle pack reaches 74.4 V.

For a hazardous check, Veyra:
- marks that it needs review by a qualified engineer, and names who
- names a safer check where one exists
- never records approval itself

The AI's report is rejected if a hazardous check does not set `needs_approval`.

**Shuttle**
- All 5 discussion checks are hazardous: they drive the vehicle and reproduce the fault on a cold ramp.
- Round 1 also asks for a multimeter on the inverter terminals during a launch, which is live high voltage.
- The safer alternative offered: replay the logged commands through a controller model, or run the changed software
  on a hardware-in-the-loop bench.
- The electrical plan now puts the offline log check first (inverter temperature trend). The four-wire segment
  resistance comes second, after review by a high-voltage technician. The electrical module's own validated choice is
  unchanged and still shown.

**micro-ROS.** No high-voltage system. All 6 checks (Wireshark capture, debugger, buffer logging, firmware change on
the board) are low-risk and not flagged.

## 4. Does each engineer input change the next action?

| Case | Input | Result |
|---|---|---|
| Shuttle | round 4, observation (engineer report) | **Changed, with evidence.** FR terminal open → leading, cell 13 added. The new check re-tightens the terminal first; it rests on the engineer's report. |
| Shuttle | round 3, claim | **Explanations moved, action unchanged.** The same check, only reworded (similarity 1.0). |
| Shuttle | round 5, claim | **Action changed without any evidence moving.** The new check answers the engineer's question (software or parts first?), not new data. |
| micro-ROS | engineer finding (observation) | **Changed, with evidence.** Ethernet DMA open → leading; client buffers leading → weakened. |
| micro-ROS | round 3, claim ("the switch corrupts it") | **Action changed without evidence.** The new check tests the claim; no explanation moved. |
| Benchmark (synthetic shuttle, deterministic) | decision points with new engineer results | **3 points, 1 changed.** On 10-29, "inverter not the issue" (reported, so deprioritised, not excluded) switches the plan from the offline temperature check to the segment resistance, which needs review. |

## Validated, and what still needs real OEM cases

**Validated here**
- The state, provenance, safety and impact logic, by tests on these two cases.
- The micro-ROS log and thread are real.

**Not validated**
- The shuttle is synthetic, and the engineer messages in both discussions were scripted.
- The hazard classes come from keyword and measurement rules. They have not been checked against an OEM's own safety
  procedures or rated equipment lists.
- The 60 V threshold is a general rule. An OEM's own voltage classes and site rules take precedence.
- How well the hazard classifier recalls on real field procedures is unknown.
- Whether "status rests on an engineer report" matches how engineers judge their own evidence needs real cases with
  engineers reviewing it.
