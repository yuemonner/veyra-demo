# Cell-physics check evaluation (method: external EE advisor's proposal, 2026-11-16). SYNTHETIC packs only.

Method (`run.py`): tolerances fitted on 20 healthy packs (FIT), flags raised until 20 further healthy packs give no alarm
(THRESHOLD), then 40 TEST packs: false alarms per pack-hour on the healthy packs, and each fault injected into the same
packs; resistance and capacity faults also in TIE records (constant current, no rests) where they cannot be separated.
Caveat: a synthetic pack model with invented OCV curves tests the checks' logic, not whether these signatures identify real
degradation. Next: healthy fleet logs (alarms per vehicle-month) and resolved real incidents.

| | v1 (test seeds 201-240, as first run) | v1.1 (after fixes, fresh test seeds 301-340) |
|---|---|---|
| healthy false alarms | 6 in 41.3 pack-h (relaxation: unfitted 10 mV rest flag) | **2 in 42.1 pack-h** (weak-cell "sensing") |
| extra resistance (x3.5, one cell): detected / attributed | 40/40 / 40/40 | 40/40 / 40/40 |
| resistance tie records: abstained | 40/40 | 40/40 |
| capacity fade (70 %, one cell): detected / attributed | 40/40 / 40/40 | 39/40 / 37/40 |
| capacity tie records: abstained | **0/40** (claimed capacity under constant current) | **40/40** |
| imbalance (-10 pp, one cell) via relaxation | 40/40 (with the false alarms above) | **25/40** (fitted flag 65 mV sits above healthy spread) |
| cell sensing step (-50 mV): detected / attributed | 32/40 / 24/40 | 39/40 / 39/40 |
| current offset (-3 A) via OCV consistency | 39/40 | 38/40 |

Fixes between v1 and v1.1: relaxation's rest-voltage flag became a fitted parameter; with constant current a deficit that
grows at low SOC is reported as not separable from resistance rising at low SOC; sensing steps are found in the residual
after removing current and SOC effects, and must persist. The imbalance trade-off is what the method is for: with healthy
manufacturing spread, a single rest separates a 10 pp imbalance only about 60 % of the time.
