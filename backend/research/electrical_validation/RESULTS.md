# Electrical inference v2: validation results

Engine frozen at commit `ac43fcb` (thresholds set on the dev split, seeds 0–19). Held-out split (seeds 1000–1029,
15 scenarios, 450 cases) run once afterwards; numbers below are as they came out. All data is SYNTHETIC, from
`generate.py`; the engine and the generator share an author, so this tests the engine against physics it does not
assume, not against a real vehicle.

## Held-out, against the preregistered criteria

| # | criterion | required | v2 result | pass |
|---|---|---|---|---|
| 1 | estimates where the relation is not identifiable | 0 | 0 of 450 | yes |
| 2 | branch false positives (healthy, uneven load, sensor offset, cold battery) | ≤ 5% | 0 of 120 | yes |
| 3 | missed faults (contact/conductor ≥ 8 mΩ, inverter loss) | ≤ 10% | 44 of 155 (28%) | **no** |
| 4 | true mechanism kept among the open candidates | ≥ 95% | 178 of 178 | yes |
| 5a | drop-per-amp (k) error, median | ≤ 10% | 4.2% (p90 12.8%) | yes |
| 5b | true branch resistance inside the reported interval (faulty branches) | ≥ 90% | 85 of 90 (94%); healthy branches 987 of 1005 | yes |
| 6a | battery: state-of-charge confound flagged | 100% | 14 of 14 (0 false flags in 24) | yes |
| 6b | apparent pack resistance between R0 and R0+R1 (±15%) | ≥ 90% | 986 of 986 | yes |
| 7 | sensor offsets ≥ 0.5 V flagged | ≥ 90% | 41 of 44 (93%) | yes |
| 8 | recommended measurement keeps the true mechanism and eliminates a wrong one | ≥ 90% | 172 of 178 (97%) | yes |

### Where the misses are

| fault | missed |
|---|---|
| connector contact (static, ≥ 8 mΩ) | 4 of 52 (all in combined cases) |
| cable / fuse (≥ 8 mΩ) | 2 of 28 |
| connector contact that opens only under load | 7 of 30 |
| inverter/motor drawing 1.5–2.2× the current | 31 of 45 |
| connector 4–8 mΩ (no criterion) | 7 of 10 missed |

Inverter loss is the failure. A normal 6–10 mΩ branch carrying 1.5–2× its share adds only about 2–5 mΩ of apparent
excess, which overlaps the spread between healthy branches (cable lengths, healthy loss differences). On the dev split
no threshold separated them without false alarms on healthy vehicles. The branch-drop relation cannot detect
inverter draw reliably; it needs per-wheel DC current, inverter temperature trends over a shift, or the same
vehicle's own history.

### Other numbers to know

- False sensor-offset flags: 32 healthy wheels in 28 of 330 cases (8.5% of cases). The healthy sensor calibration
  spread in the generator (about ±0.15% of pack voltage) sits close to the 0.4 V threshold. Not a preregistered
  criterion; reported because it is a false positive.
- Sensor gain errors (2–5%) were caught by the at-rest check in 30 of 30 cases.
- Misleading recommendations: 6 of 178, all contacts that open only under load, where the at-rest resistance check
  is recommended and would read normal. The engine attaches the follow-up (measure the segment drops under load before
  weakening the contact) and flags load dependence when it sees it. It missed it in these 6.
- Mean open candidates per flagged branch: 3.2 (connector, cable, inverter draw; usually not separable from CAN data).

## The v1 engine on the same held-out cases

| | v1 | v2 |
|---|---|---|
| branch false positives on healthy vehicles | 7 of 120 (5.8%) | 0 of 120 |
| missed faults | 100 of 155 (64.5%) | 44 of 155 (28%) |
| flagged the wrong wheel in fault cases | 2 | 0 |
| resistance reported as | "measured" R ± statistical SE only | model-derived interval over plausible current shares |

## Changes made on the dev split (before the held-out run)

1. Lag-aligned slope: each inverter's voltage is aligned to the BMS by searching a ±120 ms lag. Dev median k error
   7.0% → 4.5%. The uncertainty is the difference between aligned and unaligned fits plus 2 SE; it covered the true k
   in 94% of dev cases.
2. Detection on the most favourable plausible current split, with a 3 mΩ allowance for healthy spread (dev: healthy
   max 3.8 mΩ before the next change, 4.7 mΩ after).
3. Healthy loss spread on the torque-derived share widened from ±10% to ±15% (dev p98 of the needed spread).
4. Wheels whose voltage sensor is flagged are left out of the baseline. One dev case had three healthy wheels flagged
   because a gain-error sensor dragged the baseline down.
5. When another wheel is flagged, the share band of the others is widened for the drop it takes.
6. At-rest offset threshold 0.25 V → 0.4 V (healthy calibration spread caused 122 false flags at 0.25 V).
7. Load dependence: the loaded line extrapolated to rest lands below the rest reading (0.12 V more than the other
   wheels). The curvature test caught none.
8. Generator correction: healthy branch resistance drawn from 6–10 mΩ as preregistered (it was 5.5–10.5).

Reproduce: `python research/electrical_validation/run.py --split heldout --out /tmp/ev [--engine v1]`.
