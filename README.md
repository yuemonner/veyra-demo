# Veyra · Field Decision Support

**Understand → Decide → Act → Verify → Reuse**

When a robot breaks, the hard part is deciding what to do next. Veyra helps engineering and delivery teams compare responses to field incidents, record a human decision and actual intervention, verify what happened, and carry forward experience with its applicability limits. Investigation is the entry point into this workflow.

> The intelligence is not just in understanding what happened. It is in learning what was worth doing next.

[Open the product demo](https://frontend-livid-seven-41.vercel.app/product-demo) · [Engineering architecture](docs/VEYRA_1_1.md) · [Pilot setup](docs/PILOT_SETUP.md)

## The engineering problem

A healthy-looking machine is not automatically a valid control. EX11 may share a software configuration with failed machines but may never have experienced the same operating conditions. Missing alerts are not proof of health.

The engine separates configuration from actual operating exposure, preserves unknowns and contradictory evidence, and recommends an explicit next check. It can say:

> Current evidence cannot distinguish software regression from environmental interaction.

The product goal is the next defensible engineering decision. It does not claim a root-cause probability or established causal inference.


## Decision workflow · implemented slice

The existing workspace navigation is retained. Decision now contains operational options and a human decision form; Hypotheses remains a separate engineering view. Outcome contains separately persisted action and verification records. New records are append-only `CaseEvent` entries, linked as decision → action → verification. No graph database is required for these relationships.

- A decision stores the options considered, selected option, owner, rationale, constraints, reported approval and an evidence snapshot that later diagnostic observations do not rewrite.
- Actual execution is never inferred from intent. The legacy decision endpoint no longer creates a fabricated executed action. Required approval is a human-reported workflow guard, not authenticated organizational authorization.
- Verification distinguishes recovery during a window, partial recovery, no recovery, recurrence and unknown. Updates must extend the observation window. These are sourced human reports, not independent telemetry attestation or proof of causality.
- Cross-case retrieval compares explicit conditions with the new case's recorded context. Actual operating exposure remains unknown unless independently established; configuration never supplies it. Failures and recurrence are retained as cautionary experience, and no candidate is an automatic fix recommendation.
- The earlier `/analysis/precedent` and fixed-scenario memory endpoints are legacy demo interfaces. The current Decision/Outcome UI uses `/workflow` and `/workflow/reuse`; old records are not silently promoted into this lifecycle.

Decision–Outcome relationships are a proposed compounding data advantage, not an established moat. This synthetic demo does not demonstrate customer adoption, improved decisions, reduced downtime or economic value. Production needs authenticated approvals, durable storage, source verification and customer workflow validation. A local SQLite database or an ephemeral hosting filesystem does not guarantee records survive infrastructure replacement; configure durable `DATABASE_URL` for a pilot.

| API | Behavior |
| --- | --- |
| `GET /investigations/{id}/workflow` | Read options, decisions, actual actions, verification updates and known context |
| `POST /investigations/{id}/workflow/decisions` | Save intent and decision-time evidence; never execute |
| `POST /investigations/{id}/workflow/actions` | Report actual execution linked to a decision in the same case |
| `POST /investigations/{id}/workflow/verifications` | Append outcome evidence linked to an action |
| `GET /investigations/{id}/workflow/reuse` | Retrieve other cases with matching, mismatched and unknown conditions |

## Architecture

```mermaid
flowchart TD
    Sources[Logs · Telemetry · Configs · Human reports · Maintenance history] --> Case[Field Case · Evidence timeline]
    Case --> Context[Context Model · Comparability · Actual exposure]
    Context --> Hypothesis[Evidence & Hypothesis State]
    Hypothesis --> Planner[Diagnostic Planner · Feasibility · Cost · Risk]
    Planner --> Test[Human diagnostic check]
    Test --> NewEvidence[New sourced observation]
    NewEvidence --> Case
    Case --> Options[Operational options · Trade-offs · Constraints]
    Options --> Decision[Human decision · Evidence snapshot]
    Decision --> Action[Reported actual intervention]
    Action --> Memory[Outcome verification · Recurrence · Unknowns]
    Memory --> Precedent[Conditional reuse · Matching / Conflicting / Unknown conditions]
    Precedent --> Options
    Case --> Analysis[Shared deterministic analysis]
    Analysis --> UI[Field Case UI]
    Analysis --> API[API]
    Analysis -. planned adapter .-> MCP[MCP / Reports]
```

| Layer | Current implementation |
| --- | --- |
| Evidence ingestion | CSV, JSON, JSONL and NDJSON exports; source references and evidence timing |
| Context Model | Matching release/configuration/firmware/map; explicit unknown/same/different exposure; incomplete telemetry and confounder warnings |
| Hypothesis state | Structured support, contradiction, missing evidence, possible confounders, proposed checks, engineer challenges and revision events |
| Diagnostic Planner | Deterministic heuristic with explicit cost, risk, approval and feasibility; no calibrated Expected Information Gain |
| Intervention memory | Human decision, actual action, observed outcome, verification window/source, concurrent-change uncertainty and applicable conditions |
| Interfaces | Existing Next.js Field Case demo and FastAPI endpoints; MCP adapter remains planned |

The SQLAlchemy-backed rules in `backend/app/investigation_engine.py` are the analysis source of truth. The UI calls that engine. An unavailable backend is shown as unavailable, not replaced by invented investigation results.

LLMs may eventually parse engineer language, propose variables/hypotheses or explain selected evidence. They do not decide whether evidence exists, whether a control is valid or whether recovery has been verified. This slice does not require an LLM.

## Evidence boundaries

- Configuration matches are `context_evidence`, never direct support for an operating-exposure hypothesis. The baseline has 3/3 affected and 1/9 no-known-signal machines (including EX11) configured for L4 + Zone B; none has verified comparable operating exposure.
- Hypothesis links validate actual payload fields. Firmware context must explicitly contain `firmware_version=lidar-fw-5.3`; nearby localization records without that field do not qualify. Metadata remains context, not causal proof.
- EX11 starts as a candidate control. Investigation priority is separate from evidence strength. Preset recovery belongs to a clearly separated historical reference and is excluded from current assessment. Event and known times have distinct columns (EX08: 14:25 / 14:26 UTC).

## Try the investigation loop

1. **Fleet:** EX11 starts with `Exposure not verified`. Inspect context, control validity and missing data.
2. **Decision:** compare rollback, EX11 records review and field dispatch. Inspect qualitative trade-offs and constraints, then save the human owner, rationale and approval status with the current evidence snapshot.
3. **Outcome · actual action:** separately report what was executed, when, on which machines and from which source. Required approval must be reported before an action can be recorded. Veyra does not send machine commands.
4. **Outcome · verification:** append an observed result, completed observation window, evidence source, remaining unknowns, applicability conditions and exclusions. A recurrence remains visible alongside the earlier recovery.
5. **Conditional reuse:** open a new investigation and inspect experience from other cases in Decision or Outcome. The latest verification per action governs reuse. Matching, conflicting and unknown conditions are shown; exclusions and source quality still require human review.

Each browser receives an isolated synthetic investigation session. Starting a new investigation creates a fresh session without resetting another visitor's case. Browser local storage retains its session ID; database records survive browser reloads. Durability across server restarts depends on the configured database.

The scenario buttons are synthetic evidence inputs, not physical machine execution. Exposure and outcome verification are supplied by the engineer; Veyra does not independently attest a source simply because it was entered. Conflicting results remain conflicting. The planner explains that testing EX11 in Zone B alone cannot cleanly separate L4-alone from an environmental interaction.

## Run locally

Backend (Python 3.11 recommended):

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
VEYRA_AUTO_SEED=true uvicorn app.main:app --host 127.0.0.1 --port 8050
```

Frontend, from another terminal:

```bash
cd frontend
npm ci
NEXT_PUBLIC_API_URL=http://127.0.0.1:8050 npm run dev
```

Open `http://localhost:3005/product-demo`. Use the existing `/try` path for file import. Configure the hosted API address before building a deployed frontend.

## API and persistence

| Endpoint | Purpose |
| --- | --- |
| `POST /demo/investigation-sessions` | Create an isolated synthetic Field Case |
| `GET /investigations/{id}/analysis` | Shared cohorts, hypotheses, ranked checks, uncertainty and precedents |
| `GET /investigations/{id}/analysis?known_at=...` | Replay only what was knowable at a specified time |
| `POST /investigations/{id}/diagnostic-checks/{check_id}/observation` | Validate, persist and reassess a sourced result |
| `POST /investigations/{id}/hypotheses/{hypothesis_id}/challenge` | Record an engineer challenge with validated evidence references |
| `POST /investigations/{id}/analysis/precedent` | Save human decision, observed outcome and applicability boundaries |

Observation fields include `asset_id`, `zone`, `safe_stop`, `exposure_relation` (`unknown`, `same`, `different`), `exposure_verified`, `telemetry_complete`, `source` and `event_time`. Empty, mismatched and future-dated observations are rejected. Sources remain traceable by event time and known time. Historical sealed decision packages are not rewritten by new evidence.

SQLite is the local/demo default. Use `DATABASE_URL` with Postgres for durable hosted pilot state. R2/S3-compatible upload storage is optional; see the pilot setup. The included Render free-tier configuration uses ephemeral SQLite and is suitable for demonstration, not durable customer evidence.

## Validation and evaluation

```bash
PYTHONPATH=backend python -m pytest backend/tests -q
cd frontend
npx tsc --noEmit --incremental false
npm run build
```

Regression tests cover invalid controls, missing data, contradictory results, unknown versus different exposure, hypothesis revision, isolated sessions, historical evidence cutoffs, conditional precedent persistence and existing ingestion/sealing behavior.

A proper comparative benchmark remains planned: compare Veyra with an LLM + RAG baseline on the same cases, evidence and budget, measuring evidence grounding, invalid-comparison detection, check quality, hypothesis revision and outcome verification. Engineer time saved requires trials with engineers. This repository makes no measured efficiency or benchmark superiority claim.

## Scope and ownership

Build the context/exposure model, structured hypothesis state, diagnostic planner and conditional engineering memory. Reuse established parsing, storage, search, visualization and foundation-model infrastructure where appropriate.

The current slice is a bounded synthetic EX11 case, not a general causal-inference platform. MCAP/ROS parsing, formal causal estimation, automatic telemetry attestation, full hypothesis authoring, MCP and autonomous intervention execution are not implemented. Robot real-time control and a general predictive-maintenance platform are outside this scope. Pump Drift can become another Field Case type later.

See [architecture and implementation boundaries](docs/VEYRA_1_1.md), [domain model](docs/DOMAIN_MODEL.md), and [historical implementation notes](docs/LEGACY_README.md).

## First-load availability

The workspace renders a bundled read-only analysis captured from the case API before the live backend responds. Returning browsers can show their own case-keyed cached view, labelled with its saved time. Neither preview nor cache is treated as a live decision record; recording checks, decisions and actions is locked until connection succeeds. Requests time out after 12 seconds; initial connection retries after 10 seconds. Mutations are never automatically replayed. A timed-out save has unknown status and must be checked by reloading records.

The configured free Render backend can sleep after idle time, so live writes can still wait for service startup. The read-only preview removes the blank first screen; it does not make the API always available. An always-on hosting instance and durable database remain deployment work. The preview is refreshed deliberately, not automatically at page load.

Loading regression tests (Node 24+): `node --test frontend/tests/case-data.test.cjs`.
