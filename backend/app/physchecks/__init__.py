"""Physical Check Engine: versioned, deterministic engineering checks over decoded machine recordings.

Layers (each its own module, each usable alone):

  recording   per-frame decoded data, columnar per (window, field), evidence references back to file + row
  profile     signal semantics: which field plays which role, units, sign convention, valid range, placeholders,
              resolution, maximum sample age, and which signals are NOT independent of each other
  base        the check interface (spec, inputs, parameters, outcome, evidence, calculation provenance) and registry
  checks      charge_accounting (A), protection_sequence (B), restart_eligibility (C)
  hypotheses  structured engineering hypotheses constrained by check results (no numeric probabilities)
  planner     next decisive check: which open hypotheses a check separates, what each predicts, safety as a hard gate
  scenarios   reviewed reusable checks (finding -> reviewed scenario -> run on other recordings) and a cross-run scanner
  outcomes    case -> hypothesis -> recommended check -> performed check -> observation -> verification -> outcome
  case_replay the five-section guided walkthrough built from all of the above

Nothing here calls a model. Same recording + same profile + same parameters -> same result (input digest recorded).
"""
