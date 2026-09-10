# Architecture

## Product Loop

```text
CAPTURE
  -> RECONSTRUCT
  -> COMPARE
  -> DECISION PACKAGE
  -> HUMAN DECISION
  -> OUTCOME
  -> OPERATIONAL MEMORY
```

## Deterministic Reconstruction

The backend calculates:

- last-known healthy state
- changes since that state
- first abnormal evidence
- human discovery time
- detection latency
- current machine state
- evidence gaps

The frontend displays API results. It does not hard-code the investigation answers.

## Evidence Timing

Every evidence record separates:

- `event_time`: when the machine event happened
- `known_at`: when Veyra could have known it during ingestion or reconstruction
- `ingested_at`: when Veyra actually received it

Late-arriving evidence can change the current investigation without rewriting what was known at an earlier decision point.

Runtime systems should only be responsible for the runtime occurrence time:

```text
runtime path
  -> event_time

ingestion / reconstruction path
  -> known_at
  -> ingested_at
```

Example:

```text
event_time: 14:09
known_at: 14:31
ingested_at: 14:31:04
```

A Decision Package sealed at 14:27 cannot be backfilled with evidence that only became knowable at 14:31, even when that evidence describes something that happened at 14:09.

## Append-only Evidence

Evidence records are append-only. Historical records may be superseded by later records, but not mutated.

## AI Boundary

Correct architecture:

```text
Raw operational data
  -> deterministic reconstruction
  -> structured context
  -> LLM explanation
```

Incorrect architecture:

```text
raw logs
  -> LLM
  -> guessed root cause
```

## Sealed Decision Package

When a human decision is recorded, the Decision Package can be sealed.

V0 uses:

- canonical JSON
- SHA-256 digest
- Ed25519 signature

The seal proves that the package snapshot was not silently rewritten after the decision.
