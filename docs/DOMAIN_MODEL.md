# Domain Model

## Asset

A physical system such as a robot, remote-operated machine, drone, industrial machine or software-defined physical asset.

## Evidence

Append-only operational record with separate `event_time`, `known_at` and `ingested_at`.

Runtime producers are only responsible for `event_time`: when something happened.

Veyra assigns or preserves `known_at`: when the evidence became knowable to the reconstruction layer.

Veyra records `ingested_at`: when the evidence was actually received.

Evidence classes:

- `OBSERVED`
- `INFERRED`
- `HUMAN_ASSERTED`
- `UNKNOWN`
- `SUPERSEDED`

## StateSnapshot

Reconstructed machine state at a specific event time and knowledge time.

## Change

A source-backed transition between two states.

## Trigger

An event that opens a review, such as safe-stop behavior, planner fallback, deployment risk or a human escalation.

## Investigation

The workspace around one operational question.

## Comparison

Affected and unaffected peer analysis over shared operational context.

## DecisionPackage

The primary product artifact. It packages evidence, belief state, approval policy, human identity, action scope and follow-up requirements for a consequential operational decision.

## Decision

The human action or judgment made from the package, including owner, rationale and decision-time context.

## Outcome

What happened after the decision. Outcomes are what make operational memory compound.

## DecisionPrecedent

An outcome-linked prior decision that can be surfaced when a similar operational context appears again.
