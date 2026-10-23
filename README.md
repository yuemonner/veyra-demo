# Veyra

Engineering decision support for robots and remote-operated machines in the field.

When a machine misbehaves, Veyra reads its logs and recordings, shows where to look first, proposes the next check
that best separates the possible causes, and records what people decided, did and confirmed. It never declares a
root cause on its own; people do.

**Status:** pre-production engineering build. Not production-ready. no private data is in this repository.
Proprietary: shared for review; ask the owner before any other use.

---

## For reviewers: start here

| Read | Why |
|---|---|
| This README | What runs where, how to run it, what to look at |
| [docs/PRIVATE_INSTANCE.md](docs/PRIVATE_INSTANCE.md) | Recordings upload, accounts, workspaces, share links |
| [docs/COMPUTE_LAYER.md](docs/COMPUTE_LAYER.md) | Agent tools, sandboxed Python, known-answer evals |
| [docs/NEXT_ACTION.md](docs/NEXT_ACTION.md) | Investigate → discriminating check → dispatch readiness |
| [docs/DECISION_CHECK.md](docs/DECISION_CHECK.md) | Checking a proposed decision against the evidence, before it is committed |
| [docs/AUTOPILOT.md](docs/AUTOPILOT.md) | Veyra investigates, proposes, checks itself and prepares; people approve and decide |
| [docs/ALFA_BENCHMARK.md](docs/ALFA_BENCHMARK.md) · [docs/PRECEDENT_EXPERIMENT.md](docs/PRECEDENT_EXPERIMENT.md) · [docs/CASE_MEMORY_TEST.md](docs/CASE_MEMORY_TEST.md) | Evaluations and their caveats |

Where most of the logic is:

| Area | Code | Tests |
|---|---|---|
| Import and validation | `backend/app/ingest/` (`pipeline.py`, `profile.py`, `catalog.py`, `bags.py`) | `test_ingest.py`, `test_recordings.py` |
| Investigation engine | `backend/app/engine/investigate.py`, `checks.py`, `triage.py`, `precedents.py` | `test_investigation_engine.py`, `test_engine_ledger_e2e.py`, `test_precedents.py` |
| Case record (ledger) | `backend/app/ledger/model.py`, `backend/app/store/sqlite_store.py` | `test_ledger_integrity_regressions.py` |
| Machine types | `backend/app/machines/` | `test_machine_profiles.py`, `test_legged_robot.py` |
| AI agent | `backend/app/agent/` (`runner.py`, `tools.py`, `llm.py`) | `test_connectors_agent.py` |
| Compute layer | `backend/app/compute/` (`data.py`, `methods.py`, `sandbox.py`, `_child.py`) | `test_compute.py` |
| Next action | `backend/app/engine/next_action.py`, `backend/app/workflows/next_action.py` | `test_next_action.py` |
| Decision check | `backend/app/engine/decision_check.py`, `backend/app/workflows/decision_check.py` | `test_decision_check.py` |
| Autopilot | `backend/app/workflows/autopilot.py` | `test_autopilot.py` |
| Private instance | `backend/app/private.py`, `accounts.py`, `recordings_api.py` | `test_private_instance.py`, `test_accounts.py` |
| HTTP API | `backend/app/engine_api.py`, `backend/app/main.py` | `test_engine_api.py` |
| Main screen | `frontend/app/field-case/page.tsx` | type check |

Questions worth a reviewer's time:

1. Can a caller write a ledger record it shouldn't (automatic results, agent reports, next-action reports)?
2. Can one workspace read another's cases, share links or files?
3. Can sandboxed agent code reach the network, other files or the API process? (`compute/sandbox.py`, `_child.py`)
4. Do the agent's report checks catch made-up numbers and confirmed-cause claims? (`agent/runner.py`)
5. Are timing claims across different clocks refused when the offset is unknown? (`compute/methods.py`, `analysis/command_chain.py`)

---

## Run it locally

Requirements: Python 3.11+ (3.12 tested), Node 20+.

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt -r backend/requirements-recordings.txt
cd frontend && npm ci && cd ..
```

**Private instance** (recordings, workspaces, AI agent):

```bash
scripts/private_preview.sh .venv/bin/python
# open http://127.0.0.1:3029/field-case
```

- API on `127.0.0.1:8029`, web on `127.0.0.1:3029`, data in `.local-private/` (ignored by git).
- Creates test accounts (`.local-private/test-accounts.txt`, test values only) and SYNTHETIC sample recordings.
- Seeds SYNTHETIC cases: the legged-robot eval cases and the excavator next-action demo.
- Sign-in is skipped for requests from your own computer (`VEYRA_LOCAL_SIGNIN_AS`). Unset it to test sign-in.
- The AI agent uses a local Claude Code sign-in (`VEYRA_AGENT_BACKEND=claude-code`) or `ANTHROPIC_API_KEY`.
  Without either, everything else works and the agent buttons say it isn't configured.

**Public demo mode** (synthetic stories, no sign-in):

```bash
VEYRA_ENABLE_RUNTIME=true PYTHONPATH=backend .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8019
cd frontend && NEXT_PUBLIC_ENABLE_RUNTIME=true NEXT_PUBLIC_API_URL=/local-api npx next dev --hostname 127.0.0.1 -p 3019
# open http://127.0.0.1:3019/field-case
```

**Command line, no server** (standard library only):

```bash
python3 scripts/veyra.py demo        # full loop on SYNTHETIC data, writes an HTML report
python3 scripts/veyra.py --help
```

## Tests

```bash
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests -q    # each test uses a throwaway database
cd frontend && npx tsc --noEmit
```

- Sandbox tests skip if the computer has no OS sandbox (Linux network namespaces or macOS `sandbox-exec`).
- Model-in-the-loop evaluation (needs a model; SYNTHETIC data only):
  `python scripts/validation/agent_evals.py docs/results/agent_eval_results.json`

---

## How it works

```
Upload (CSV / JSON / ROS 1 bag / ROS 2 bag / MCAP)
  → import profile: which column or topic is which signal, units, clock          app/ingest
  → validated, fingerprinted, stored as-is                                       app/store
  → investigation run: compare with a declared normal period, scan every sample,
    command tracking, frozen outputs, command-chain timing, events               app/engine, app/analysis
  → parts of the machine: evidence / open / ruled out (by people)
  → ranked next checks, dispatch readiness, similar earlier cases
  → people: approve checks, record results, decide, act, verify, close           app/ledger
  → optional AI agent: queries data, runs computations, writes a cited report     app/agent, app/compute
  → next action: discriminating check → dispatch readiness report               app/engine/next_action.py
  → decision check: before committing, facts · gaps · stated claims             app/engine/decision_check.py
```

Principles the code enforces:

- **Meaning is explicit.** A signal means what its import profile says. Unknown units, clocks and message types stay unknown.
- **The record is append-only.** Database triggers refuse UPDATE/DELETE; every case is a SHA-256 hash chain.
  Records about a run become stale when its evidence or machine context changes.
- **People decide.** Decisions, actions and verified outcomes are separate records. A second person reviews where it matters.
- **Machine-written records are protected.** Automatic results, agent reports and next-action reports can only be written by the engine.
- **No root cause by machine.** The engine reports evidence per part; the agent ranks hypotheses; closing a case is a person's call.

## AI agent and data boundaries

- The agent only reads. Its tools query stored readings, run Veyra's methods, or run its own Python in a sandbox.
- Real (non-synthetic) case data goes to a model provider only after a person records a `model_authorization` for that provider.
  The same applies to other cases the agent wants to compare with.
- On a private instance the agent is off unless the operator sets `VEYRA_PRIVATE_ALLOW_AGENT=true`.
- Every report is checked before it is stored: each claim must cite tool calls from that run, and each number must
  appear in the cited output. No confirmed root cause is accepted.
- Sandbox: a separate `python -I` process in an empty temp folder; no network (Linux network namespace or macOS
  `sandbox-exec`), CPU / memory / file / time limits, and an audit hook blocking sockets, processes, ctypes and file
  access outside its folder. It is not a VM. Limits in [docs/COMPUTE_LAYER.md](docs/COMPUTE_LAYER.md).

## Private instance: accounts and workspaces

- `VEYRA_PRIVATE=true`: only `/auth`, `/engine`, `/share` and `/health` respond.
- One SQLite database per workspace; a person only ever reaches their own workspace.
- HttpOnly session cookie; writes also need an `x-veyra-request` header (blocks cross-site forms).
- Every record is attributed to the signed-in person, whatever the browser sends.
- Share links: read-only report of one case, expiring, revocable, stored as hashes.

## Data in this repository

| Data | Source | Notes |
|---|---|---|
| `fixtures/synthetic/` | Generated | SYNTHETIC; labelled and refused in real cases |
| `fixtures/seed/alfa_carbonz_rudder_case.sqlite.gz` | CMU AirLab ALFA dataset (public, real flights with injected faults) | Cite the ALFA dataset (Keipour et al., 2021) |
| `fixtures/seed/anymal_d_grandtour.sqlite.gz` | ETH Zurich RSL GrandTour dataset, CC BY-SA 4.0 | One real mission; the fault case is a SYNTHETIC fault injected into it and labelled so |
| `docs/results/` | Our evaluation runs | JSON results behind the evaluation docs |

Not in the repository (ignored): local databases (`.local-private/`, `.local-runtime/`), uploads, `work/`, `.env`.

## Configuration

| Variable | Effect |
|---|---|
| `VEYRA_ENABLE_RUNTIME=true` | Turns on `/engine` and `/runtime` routes |
| `VEYRA_PRIVATE=true` | Private instance: sign-in, workspaces, share links; demo routes off |
| `VEYRA_DATA_DIR` | Where private-instance data lives (default `.local-private/`) |
| `VEYRA_LOCAL_SIGNIN_AS=<email>` | Local demos only: requests from 127.0.0.1 act as that person |
| `VEYRA_PRIVATE_ALLOW_AGENT=true` | Allows the AI agent on a private instance |
| `VEYRA_AUTOPILOT=true` | Veyra advances cases on its own after investigations, approvals and site results |
| `VEYRA_AGENT_BACKEND=claude-code` / `ANTHROPIC_API_KEY` | Model for the agent (local CLI sign-in, or API key) |
| `VEYRA_COMPUTE_ALLOW_WEAK_SANDBOX=true` | Allows computations without an OS sandbox (not recommended) |
| `VEYRA_ACCESS_CODE` | Hosted demo: code required for writes |
| `VEYRA_COOKIE_SECURE=true` | Secure cookies behind HTTPS |

See `.env.example` for storage options. Secrets are never committed.

## Repository layout

```
backend/app/
  ingest/      catalog, import profiles, validation, ROS bag / MCAP reading
  context/     machine context: what was true when, and who said so
  analysis/    screening, command chain, input–response, tracking, transients
  engine/      investigation pipeline, check ranking, triage, precedents, next action
  ledger/      record validation, episode export
  store/       append-only SQLite store
  machines/    machine types (excavator, legged robot, fixed-wing aircraft, agricultural robot)
  agent/       AI agent: runner, tools, model clients
  compute/     agent data access, methods, sandbox, eval cases
  connectors/  read-only connectors (fleet API, folders, S3, ROS bags)
  automation/  webhook triggers → case → investigation
  workflows/         CLI, HTML report, demos, case memory test, next-action flow
  engine_api.py, private.py, accounts.py, recordings_api.py, main.py
  runtime_*.py and older modules: Engine v1 runtime and the legacy demo workflow
backend/tests/   pytest
frontend/        Next.js 16 / React 19 (main screen: app/field-case/page.tsx)
scripts/         CLI, local preview, generators, benchmarks, validation
fixtures/        synthetic fixtures, public-dataset seeds
docs/            design notes, evaluations, plans
edge-agent/, workers/   prototypes (heartbeat agent, keep-alive cron)
```

## Known limits

- Storage is SQLite per workspace; no migrations, no background workers beyond threads, no backups.
- Readings are held in memory per case; very large recordings need a columnar store.
- The AI agent and evaluations are smoke-tested on small synthetic sets; not a reliability estimate.
- Older modules (`runtime_*`, `/investigations`, `/product-demo`) remain for the public demo and are not part of the private instance.
- No MCP server, fleet monitoring or SSO yet.

## Deployment

The public demo runs on Vercel (frontend, deployed by hand with the Vercel CLI) and Render (API, free tier, deployed
from the branch `engine-v2-machine-context-ledger`, kept equal to `main`). The free API instance loses its disk on
every restart; the demo cases reload from `fixtures/seed/`.
