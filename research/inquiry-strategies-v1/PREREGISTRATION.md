# inquiry-strategies-v1: which investigation policy removes the most uncertainty? (preregistration)

Written and committed before any run of this comparison. The code is frozen at tag `inquiry-strategies-v1-frozen`.
`compare.py` refuses to run on any other commit or on a changed tree. Recorded runs are never overwritten or edited.

## Why

In inquiry-eval-v1, the three CASE A runs each executed exactly one shallow computation and then submitted. Giving the
agent calculation tools and a "test before you rank" rule did not, by itself, make it remove uncertainty between
competing causes.

This experiment holds the agent, tools, model and budget fixed, and varies only the policy that drives the
investigation:

| strategy | what drives the agent | when it may submit |
|---|---|---|
| free | "test the competing causes with what you judge useful" (inquiry-v1's rule) | after one executed, non-post-hoc prediction test |
| coverage | every pair of competing causes, listed by `inquiry_plan` | every pair is separated by a test, or declared not discriminable after every applicable computation has run |
| infogain | `inquiry_plan` ranks the registered predictions by expected_bits (expected shrinkage of the set of causes still tied) | every pair still tied was tested by a prediction that differed for it, or declared not discriminable; the agent decides when no test can split more |

Fixed for all three:
- the model (claude-sonnet-5-5, Claude Code backend);
- the tools (`inquiry_*` and the physical layer);
- 30 turns and 6 fix attempts;
- the Safety Gate and every physical-action rule, unchanged.

## Change made before the freeze, after one development run

One development case (SYNTHETIC current_scale, coverage strategy, run in the container) showed the agent predicting the
wrong direction for a current sensor that reads low. It predicted a smaller volts-per-amp, when a low-reading sensor makes
it larger. Its prediction rested on an assumed "typical" cell resistance, so the test contradicted the true mechanism.

The frozen code therefore adds the measurement-fault algebra (`app/physchecks/effects.py`), the same for every strategy:
- a prediction that moves a quantity against a fault's known effect, or that separates a fault from a quantity it cannot
  change, is returned to the model;
- a separation that rests on a no-fault baseline depending on an unstated machine parameter is
  `contradicted_if_assumed`. It weakens; it does not place one cause below another.

## Cases

A fresh SYNTHETIC set is drawn after the freeze by `compare.py seal` (`os.urandom`). There are five cases, one per
generator cause: soc_drift, capacity_fade, current_scale, sensor_bypass, drift_weak_cell (`scripts/make_synthetic_heldout.py`).
- The key lives outside the repository. Only its SHA-256 is printed and committed before the first run.
- The agent never sees the key; case files carry neutral labels.
- This set is NOT the inquiry-eval-v1 held-out set. That set stays sealed for its own preregistered use.
- CASE A is not used here; it remains a development regression only.

By construction (no OCV curve, no independent meter in the profile):
- for the A/B-type causes, A and B cannot be separated, and C can be contradicted;
- for sensor_bypass, C can be placed alone in first place;
- for current_scale, how far C can be separated is recorded only.

## Measures, per case (scored only after every strategy's run is finished)

- **top:** the causes in first place. That is the first ranking tier mapped to A/B/C, with mechanisms C1/C2 counted as
  C. With no ranking, it is every cause no counted test contradicted.
- **correct:** the true cause is in top.
- **bits = log2(3 / |top|)** when correct, else 0. This is the uncertainty removed among A, B and C.
- **false_exclusion:** the true cause is not in top.
- **truth_contradicted_by:** counted tests that contradicted the true cause or its true mechanism outright (C1 for current_scale, C2 for sensor_bypass). Each counts as an H3 failure.
- **H3 / H4 / H5:** from inquiry-eval-v1's criteria.
  - H3: the truth was not demoted.
  - H4: A and B were not falsely separated by an observation.
  - H5: discovery.
- **Cost:** model calls, tool calls, tokens, seconds.

## Selection rule (fixed now)

1. Keep only the strategies with zero false exclusions, zero H3 failures, and every case run by the agent.
2. Among those, pick the most total bits.
3. Break ties by fewest model calls.

If no strategy qualifies, take the fewest false exclusions, then the most bits. The selected strategy becomes Veyra's
default. It is then run ONCE on a second, freshly sealed set (`confirm`). That confirmation result is reported as it
comes; it may contradict the selection.

## Limits stated now

- Five cases per strategy is a small sample. The comparison shows direction, not a reliable effect size.
- All cases are SYNTHETIC. Real solved cases (requested from the customer) are the only test that matters for a customer.
- A fair comparison with Alloy needs the same data, the same access and a comparable model on Alloy's actual agent.
  Nothing here measures Alloy.
