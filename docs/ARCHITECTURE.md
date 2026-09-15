# Architecture

## Product Loop

```text
EVIDENCE
  -> BELIEF
  -> DECISION
  -> ACTION
  -> OUTCOME
  -> PRECEDENT
```

Veyra models operational decisions across physical systems.

## Deterministic Reconstruction

The backend calculates:

- last-known healthy state
- changes since that state
- first abnormal evidence
- human discovery time
- detection latency
- current machine state
- evidence gaps

The frontend displays API results from the backend reconstruction services.

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

A Decision Package sealed at 14:27 preserves the evidence knowable at that moment. Evidence that becomes knowable at 14:31 updates the current investigation as a later record.

## Append-only Evidence

Evidence records are append-only. Later records supersede historical records through versioning.

## Reconstruction Boundary

Core architecture:

```text
Raw operational data
  -> deterministic reconstruction
  -> structured context
  -> LLM explanation
```

Deferred architecture:

```text
raw logs
  -> LLM
  -> unsupported root-cause claim
```

## Sealed Decision Package

When a human decision is recorded, the Decision Package can be sealed.

Before and after that decision, Veyra also maintains a substantiation state:

```text
Are we allowed and justified to take this operational action yet?
```

V0 checks affected scope, exposed-but-stable systems, named decision owner, preserved unknowns, inference boundaries and outcome follow-up.

V0 uses:

- canonical JSON
- SHA-256 digest
- Ed25519 signature
- named human owner
- independent trusted timestamp
- standalone verification endpoint

The seal proves that the package snapshot stayed intact after the decision.

The product artifact is:

```text
Evidence -> Belief -> Decision -> Action -> Outcome
```

That record becomes precedent for the next similar operational decision.
