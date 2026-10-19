# Veyra — BuilderX pilot production plan

Status date: 2026-10-19. Scope: a bounded remote-support pilot for remote-operated heavy equipment.
This document separates **implemented**, **remaining** and **customer-dependent** work. It is not a claim that
Veyra is production-ready, that BuilderX has supplied data, or that a pilot is agreed.

## Post-review verification

The previously unexecuted API tests now pass in the installed local environment. Full suite: **146 backend tests**, **9 connection tests**; isolated frontend Webpack production build/type checking passes. Three P1 ledger integrity findings are fixed with regression tests: trusted automatic receipts, serialized/atomic writes, and actual samples in outcome windows. Details: [Engine v2 review fixes](ENGINE_V2_REVIEW_FIXES.md). Historical session limitations below describe the original implementation session, not the current validation result. M2–M8 and customer acceptance gates remain open.

## 1. Pilot hypothesis (provisional until BuilderX confirms)

- Failure class: **remote-control response delay** on heavy equipment.
- Fault domains kept distinct and open until evidence separates them: `network`, `control_suite`
  (application/controller), `vehicle_interface`, `vehicle_body`. A fault domain is not a root cause.
- Product loop: Industrial Data Adapter → Machine Context Model → Investigation Engine → Next-Best-Check →
  Human Decision & Dispatch Readiness → Actual Intervention → Verified Outcome → Conditional Experience Reuse.
- Pilot value to measure (section 8): time to first evidence-backed check, engineer review burden, dispatch
  preparation completeness, recurrence, verified resolution. No time-saving claim without a comparable baseline.

## 2. Audit of the current working tree (2026-10-19)

Baseline recorded by the previous engineer: 63 backend tests, 9 frontend connection tests, passing build.
**Not re-verified in this session**: PyPI and npm are blocked for this session (cloud workspace and the
Mac's Linux VM), so FastAPI, SQLAlchemy and Next.js cannot be installed here. Re-run on the Mac
(section 10) or after package-manager access is allowed.

### 2.1 Two parallel applications share one database

| Area | Legacy workflow | Executable runtime (`/runtime`) |
| --- | --- | --- |
| Entry modules | `main.py`, `services.py` (1,599 lines), `decision_workflow.py`, `field_capabilities.py`, `diagnostic_engine.py`, `investigation_engine.py`, `automatic_analysis.py` | `runtime_engine.py`, `runtime_adapters.py`, `runtime_diagnostics.py`, `runtime_tools.py`, `runtime_summary.py` |
| Case container | `Investigation`, `Evidence`, `Hypothesis`, `DiagnosticCheck`, `DecisionPackage`, `Outcome`… | `RuntimeCase/Source/Run` + `CaseEvent(kind=runtime_*)`; also creates a legacy `Investigation` row as container |
| Ingestion | `/recordings/upload` (EX11 CSV), field-tool text parsing, 100-row imports | JSON body with file text; explicit column mapping; 5 MB / 50k rows |
| Dispatch planning | `field_capabilities.py` | `runtime_diagnostics.plan()` dispatch block |
| Frontend client | `lib/api.ts` **returns mock success fallbacks** on failure | `lib/case-data.ts`, explicit failures, no replay of writes |
| Seeding/reset | `reset_demo()` = `Base.metadata.drop_all()` on **all tables** | none; `/demo/reset` refused when `VEYRA_ENABLE_RUNTIME=true` |

Decision: the runtime is the shared core. Legacy routes stay readable for existing demos but receive no new
features. New capabilities attach to the runtime contracts; legacy screens migrate one at a time
(section 6, M6). No third application.

### 2.2 What is sound and is kept

- Immutable investigation runs with source hashes, normalized-sample hashes, tool traces with real durations.
- Reference/evaluation data withheld from operational tools.
- Context versions with source, actor, effective time, recorded time and confirmation status; optimistic
  concurrency; context applicability evaluated at the evidence window.
- Check reviews require reasons; stale context/evidence blocks execution and dispatch preparation.
- Outcome chain decision → action → outcome → distinct-person review; simulated records excluded from reuse.
- Memory eligibility is chronological, context-qualified and excludes self/future/unverified cases.

### 2.3 Gaps and risks found

| # | Finding | Severity | Plan |
| --- | --- | --- | --- |
| A1 | `reset_demo()` drops every table, including runtime case records; guarded only by an env var on one endpoint and by startup auto-seed logic | High (data loss) | M1: refuse reset when runtime records exist, regardless of env |
| A2 | Test DB isolation depends on `test_veyra_v0.py` being imported before `app.db`; a shared temp path; nothing refuses a non-temporary URL | High (could wipe local records) | M1: `conftest.py` forces a per-run temp DB and refuses others |
| A3 | Industrial adapter assigns semantics by **signal name** (`rtt`, `packet_loss`), contrary to "never infer equivalence from similar names" | High (correctness) | M1: versioned signal catalog + explicit per-profile signal map |
| A4 | Whole file rejected on first bad row; no per-row report, no review queue, no duplicate-upload detection, no encoding/BOM/NUL checks | High (onboarding) | M1: structured ingestion report |
| A5 | Events, controller resets, fault codes and heartbeats must be finite numbers; command send/receive pairing unsupported | High (pilot evidence) | M1 parse; M3 analysis |
| A6 | No clock-domain or clock-uncertainty model; mixed sources compared on one axis | Medium–high | M1 record; M3 enforce in comparisons |
| A7 | Original files stored as DB text; samples as one JSON column per source | Medium (scale) | M2 object storage + row table |
| A8 | No authentication, tenancy, roles; `actor` is free text; CORS `*` on the legacy app | Blocking for customer data | M4 |
| A9 | `Base.metadata.create_all`, no migrations; SQLite default | Blocking for production | M2 Alembic + PostgreSQL |
| A10 | Synchronous ingestion and investigation in request handlers | Medium | M5 jobs |
| A11 | `datetime.utcnow()` naive timestamps mixed with aware ones | Medium | M2 normalize to aware UTC |
| A12 | Check policy is four hard-coded templates with a fixed ordering | Medium | M3 rules table with versioned conditions |
| A13 | Append-only is a convention; no DB constraint | Medium | M2 insert-only repository + DB trigger on PostgreSQL |
| A14 | Legacy `lib/api.ts` mock-success fallback could mask failures if reused | Medium | Never used by runtime views; M6 remove from pilot routes |
| A15 | `render.yaml` points at ephemeral free SQLite with auto-seed | Low (not used for pilot) | Leave untouched; pilot deploy is a separate reviewed change |
| A16 | One visible Chinese placeholder in legacy `FieldTools.tsx` | Low | Fix to English |

## 3. Competitive engineering patterns (Alloy) — observed vs. inferred

Vendor identity verified: **Alloy** (usealloy.ai, "the AI Robotics Engineer"), docs at docs.usealloy.ai.
Only publicly documented behaviour is listed as *observed*. Nothing here describes Alloy's internal
implementation, and Veyra's implementations are independent.

| Pattern | Observed in Alloy's public docs | What Veyra implements (independently) | Source |
| --- | --- | --- | --- |
| Explicit supported formats; unsupported files rejected, not guessed | Supported-format table; "a single unsupported file stops the whole upload"; per-file Ready/Failed/Skipped with reason | Per-file status **and** per-row rejection with reasons; unsupported formats go to a review queue instead of silently failing | [Supported log formats](https://docs.usealloy.ai/docs/integrations/log-formats) |
| Original data kept; copy, not move | "Your bucket stays the original" | Immutable originals with content hash; normalization version recorded | [llms.txt](https://docs.usealloy.ai/llms.txt) |
| Findings linked to evidence; observed vs inferred | Each finding links to its evidence; can ask what was observed vs inferred | Every finding references tool-trace entries, source IDs and row indices; observation and explanation stored separately | [Security and trust](https://docs.usealloy.ai/docs/get-started/trust) |
| Support case with next step and outcome | Replies with likely cause and next step; records the case and its outcome; optional follow-up on whether the fix held | Next checks ranked with explicit ordinal rules, required signals, permissions and disruption; decision, action, outcome and recurrence recorded separately | [Support Agent](https://docs.usealloy.ai/docs/use-cases/support-agent) |
| Patterns defined once, checked on new data | Scenarios: plain-language checks over recent and new missions; matches carry reason, window, mission | (Later, M3+) versioned detection rules with threshold provenance; matches carry rule version, window and rows | [Scenarios](https://docs.usealloy.ai/docs/agent-platform/navigating-alloy/scenarios) |
| Agents act as the signed-in person | MCP tools act with the person's access; destructive actions reserved to people | Server-enforced authorization on every artifact and execution path; no delete via agent/tool path | [Security and trust](https://docs.usealloy.ai/docs/get-started/trust) |
| Stated limits | Works on recordings after upload; not a safety system; engineers make the call | Same boundary: read-only, human-supervised; no actuation, no remote parameter writes | [When to use Alloy](https://docs.usealloy.ai/docs/get-started/when-to-use-alloy) |

Where Veyra intends to differ (to be **measured**, not claimed): industrial machine context as a typed,
time-applicable model; fault-specific comparability; checks separated by objective (reduce uncertainty vs
restore operations); dispatch preparation linked to the same investigation; decision–action–outcome lineage
with recurrence. Alloy is robot-log-native (MCAP/ROS/PX4); CSV/JSON is "contact Alloy" in its public format
table. That is an observation about documentation, not a claim that Alloy cannot do it.

## 4. Target architecture

```
                 ┌──────────── authorized exports (CSV/JSON, agreed log formats) ────────────┐
                 ▼                                                                             │
 Import profile (versioned: column map, signal map, units, clock domain, asset identity)       │
                 ▼                                                                             │
 Ingestion core (pure functions): bytes → decode → parse → per-row validate → normalize        │
   └─► Ingestion report (accepted / warnings / needs_review / rejected, per-row reasons)       │
                 ▼                                                                             │
 Object store (immutable original, sha256) + normalized rows (row table, versioned)            │
                 ▼                                                                             │
 Machine context (typed, versioned, effective/recorded time, confirmation, unknowns) ──────────┘
                 ▼
 Investigation run (immutable): allowlisted tools → findings (observations) → domains (open) → checks
                 ▼
 Check review (accept/reject/edit + reason) → read-only execution (allowlisted) or manual result
                 ▼
 Decision → dispatch readiness → action (actual) → outcome → independent review → recurrence watch
                 ▼
 Eligibility-filtered case memory (chronological, context-qualified, verified only)
```

Principles: pure-function domain core (no I/O) with thin API/persistence adapters; every write is an append-only
event with actor, tenant, request ID and the frozen snapshot it relied on; derived views are recomputed from
immutable inputs, never stored as truth.

### 4.1 Data model direction (relational; no graph database)

- `organizations`, `sites`, `machines`, `memberships(role)` — ownership on every row (`org_id`).
- `source_artifacts` (object key, sha256, bytes, media type, uploaded_by, retention class) — immutable.
- `imports` (artifact, profile version, normalization version, status, report JSON).
- `normalized_rows` (import_id, row_index, ts_utc, clock_domain, canonical_signal, kind, value, unit,
  original_value, original_unit, quality, raw JSON) — partitioned by import.
- `context_versions` (machine, version, attributes JSON with per-attribute provenance), unique (machine, version).
- `runs` (inputs: import IDs + hashes, context version, policy version, parameters; outputs JSON) — insert-only.
- `events` (kind, payload, refs, actor, request_id) — insert-only; DB trigger forbids UPDATE/DELETE on PostgreSQL.
- `jobs` (kind, idempotency key, status, attempts, lease, error).

## 5. Ingestion contract (M1, implemented in `backend/app/ingest/`)

- Accepted media: `text/csv` (UTF-8 / UTF-8 with BOM), `application/json` (array of objects) and JSON Lines.
  Anything else → `unsupported_format` with a review-queue entry; never parsed heuristically.
- Limits: core defaults 25 MB/file, 200,000 rows, 80 columns, 4 KB/field (configurable). The local runtime API
  keeps its existing 5.1 MB request cap, so a file sent as base64 is limited to about 3.8 MB and 50,000 rows
  until streaming upload lands with object storage (M2/M5).
- Encoding: strict UTF-8; NUL bytes and invalid sequences reject the file with the byte offset.
- Profile: explicit column map (timestamp, asset, signal, value/unit or event/code fields), **explicit signal
  map from customer signal names to canonical catalog signals**, timestamp format, clock domain, asset identity.
  Unmapped customer signals are retained with `canonical_signal = null` and flagged `unmapped` — statistics only,
  no anomaly semantics.
- Units: only catalog-declared conversions; original value and unit always retained; unknown unit → row rejected
  for that signal with reason.
- Rows: each row is accepted, accepted with quality flags, or rejected with reasons. The file status is derived
  from configurable tolerances (default: any rejected row → `needs_review`; >20% rejected → `rejected`).
- Duplicates: identical file content (sha256) in the same machine scope → `duplicate_upload` (no new import);
  duplicate rows (same ts, signal, value, event) → flagged, kept once.
- Time: timezone required for ISO; numeric epochs require explicit unit; non-monotonic order flagged per signal;
  future timestamps (beyond declared clock skew) rejected; clock domain recorded per import.
- Output carries `normalization_version`, `catalog_version`, `profile_version`, input sha256, normalized sha256.

## 6. Milestones and acceptance gates

| Milestone | Content | Acceptance gate |
| --- | --- | --- |
| **M1 — Ingestion core + safety** (this session) | Signal catalog v1 for remote-delay; import profiles; validation and per-row report; events/fault codes/heartbeats/command records; duplicates; review queue; reset guard; test DB isolation; labeled synthetic fixtures | Core tests pass; reset refuses when runtime records exist; tests cannot target a non-temporary DB. **API wiring tests pass on an environment with FastAPI/SQLAlchemy** (blocked here) |
| M2 — Durable storage | Alembic migrations; PostgreSQL; object store adapter (local dir + S3-compatible) with private ACL; normalized row table; aware UTC timestamps; insert-only events with DB trigger; backup/restore script | Migrate up/down on empty and on a copy of the local DB; restore drill reproduces run hashes; no UPDATE/DELETE on events possible |
| M3 — Context + checks v3 | Fault-specific comparability (required attributes + exclusions per check); clock-uncertainty in comparisons; command-chain latency tool (send→receive→output→feedback); heartbeat-gap and controller-reset detectors with threshold provenance; rules table for check ranking | Changing evidence changes findings; missing endpoints keep domains open; no root-cause probability anywhere in output; regression fixtures |
| M4 — Identity + tenancy | OIDC/session auth; org/site/machine ownership; roles (viewer, engineer, approver, admin); server-side authorization on every route, artifact and tool; request IDs; rate limits; redacted structured logs | Cross-tenant access tests fail closed on every route; approval requires approver role; unauthenticated runtime disabled |
| M5 — Jobs | Background ingestion/investigation with status, cancel, bounded retries, idempotency keys; uncertain mutations surfaced, never replayed silently | Kill-and-restart test; duplicate submissions produce one import |
| M6 — Workflow + UI consolidation | Import onboarding wizard (profile → dry-run report → commit); dispatch readiness integrated in the runtime; recurrence watch; legacy routes read-only | Browser acceptance on narrow/desktop; English UI; no mock fallback on pilot routes |
| M7 — Pilot evaluation | Blind historical replay harness with evaluation-only labels kept out of decision-time evidence; metrics report with "unavailable" where data is missing | Replay on customer-permitted incidents; leakage test passes |
| M8 — Optional model boundary | Provider-neutral interface; typed allowlisted tools; budget, refusal, malformed-output handling; prompt-injection fixtures; disabled by default | Deterministic path unchanged with no key; no customer data sent without recorded authorization |

Production readiness = M1–M7 gates passed **plus** customer-dependent items in section 7.

### 6.1 M1 status (2026-10-19)

| Item | State | Verified how |
| --- | --- | --- |
| `app/ingest/` catalog `remote-delay-catalog-v1`, import profiles, validation pipeline `industrial-normalization-v1` | Implemented | `tests/test_ingest.py` (32 tests) pass on Python 3.13 with pytest |
| `app/analysis/command_chain.py` stage latency with clock-domain rules (`command-chain-latency-v1`) | Implemented; not yet a run tool | `tests/test_command_chain.py` (4 tests) pass |
| Reset guard (`app/safety.py`, A1) and test DB isolation (`tests/conftest.py`, A2) | Implemented | `tests/test_safety.py` (5 tests) pass; API-level guard test written |
| `query_window` CSV original record shifted by one line (header shown for row 1) | Fixed | API test written |
| `/runtime/catalog/signals`, `/runtime/cases/{id}/imports/preview`, `/runtime/cases/{id}/imports` | Implemented, additive | `tests/test_runtime_imports.py` written; **not executed** (FastAPI/SQLAlchemy unavailable in this session) |
| `scripts/validate_export.py` — offline export validation for onboarding | Implemented | Run on the synthetic fixtures |
| Synthetic fixtures (`fixtures/synthetic/remote_delay/`) with two clocks and an undeclared offset | Generated deterministically | Used by tests |
| Legacy English placeholder (A16) | Fixed | String change only; frontend build not re-run |
| Import UI, stage-latency in runs, context comparability v3 | Not started | M3/M6 |

M1 gate remaining: run the full backend suite (`pytest backend/tests -q`) and the frontend build on a machine
with dependencies installed. Until then the API wiring is **unverified**.

### 6.2 Investigation Engine v2: Machine Context, Intervention Ledger, Input–Response Baseline (2026-10-19)

One shared core, three surfaces. The core is pure Python (stdlib only) so it runs identically in the FDE CLI,
the API and tests, and survives the move to PostgreSQL unchanged.

```
backend/app/ingest/      catalog v2, profiles, validation          ┐
backend/app/context/     Machine Context Model (bitemporal)         │  pure core
backend/app/analysis/    screening, command chain, input–response   │  (no web/ORM)
backend/app/engine/      pipeline, check policy, dispatch           │
backend/app/ledger/      Intervention Ledger, episodes              ┘
backend/app/store/       append-only store (sqlite3 now; same SQL → PostgreSQL in M2)
surfaces:  backend/app/fde/ (CLI + HTML report + demo)   backend/app/engine_api.py (/engine/*)   [UI: M6]
```

| Component | What makes it reusable later |
| --- | --- |
| Machine Context Model (`machine-context-v1`) | Machine-scoped (not case-scoped) assertions with effective and recorded time, source, actor, evidence and status. Replays "what we knew then"; corrections never rewrite history; conflicts surfaced; purpose-specific comparability rules (`comparability-rules-v1`). This is the conditioning context any future model needs. |
| Intervention Ledger (`intervention-ledger-v1`) | Typed, validated, hash-chained per case; DB triggers forbid UPDATE/DELETE. Stores belief (domain statuses), the candidate set with features, the human choice, execution, action, outcome, independent review and recurrence. `veyra-episode-v1` export separates decision-time data from hindsight: a leakage-resistant evaluation/training set without rebuilding anything. |
| Input–Response Baseline (`input-response-delay-gain-v1`) | Optional module; runs only with same-clock input/response, enough coverage, real excitation and a minimum correlation, and compares only when context is confirmed comparable. It is the first per-machine system-identification artifact, the seed of the interventional-model direction, with honest "unavailable" reasons. |
| Engine v2 (`investigation-engine-v2`, `domain-rules-v1`, `next-check-policy-v1`) | Every run records its inputs' hashes, the context snapshot hash and the version of every rule set, so results stay comparable when rules change. |

Relationship to the existing `/runtime` workflow: unchanged and still serving the current UI. Engine v2 has its own
store file (`.local-runtime/engine.db` for the API; a workspace file for the CLI). Migration: M6 moves the workspace UI
screens to `/engine/*` one at a time; M2 moves both stores into PostgreSQL with migrations. No third application.

Verified in this session (Python 3.13, pytest): 67 core tests including the CLI end to end and the demo; report
rendered and inspected in a browser; Python 3.10 compatibility checked on the Mac VM. Written but **not executed**
here: `tests/test_engine_api.py` and `tests/test_runtime_imports.py` (need FastAPI/SQLAlchemy). Not started: UI
integration, PostgreSQL, authentication.

## 7. Customer dependencies (BuilderX)

- Confirm the fault type and bounded equipment set for the pilot.
- Supply or authorize one export format with a field dictionary; confirm which signals exist (network RTT/loss,
  application heartbeats, controller events/resets, decoded fault codes, command send/receive/ack, feedback).
- Clock sources per signal (console, vehicle controller, router) and known skew.
- Measurement endpoints (what the RTT probe actually measures vs the command path).
- Data-processing agreement: retention, deletion, residency, whether any model provider may be used.
- Historical incidents permitted for blind replay, with outcomes withheld from decision-time evidence.
- Named roles for approval of checks and dispatch.
No private OEM CAN/DBC reverse engineering is assumed or required.

## 8. Measurement plan (honest by construction)

Report observed values first; mark unavailable metrics as unavailable.
- Time to first evidence-backed check (case created → first accepted check with evidence IDs).
- Engineer review burden (reviews per case; edits/rejections with reasons).
- Investigation duration (reported milestones; conflicts surfaced, not resolved silently).
- Dispatch preparation completeness (blockers resolved before dispatch).
- Recurrence within an agreed window; verified resolution with independent review.
Reduced handling time is claimed only against a comparable baseline with case-mix and coverage limits stated.

## 9. Security and data boundaries (all milestones)

Imported records are untrusted data; no source text becomes instructions, code, SQL or file paths. No
generated shell/Python/SQL execution. No customer data to external models without explicit authorization and
an agreed data policy. Local preview only; no deployment, push, purchase or paid tooling in this phase.

## 10. Re-verifying the baseline on the Mac

```sh
cd veyra-v0-demo
python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt
PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests -q
node --test frontend/tests/case-data.test.cjs
cd frontend && npx tsc --noEmit && npm run build
```

`backend/tests/conftest.py` forces a fresh temporary database per run and refuses any other `DATABASE_URL`,
so the suite cannot touch `.local-runtime/veyra.db`.
