# Compute layer: what the AI agent can query and compute

## Capabilities

| Capability | Before | Now |
|---|---|---|
| Read robot recordings | ROS 1 / ROS 2 / MCAP into the case ledger (private instance) | same |
| Query any data | 13 fixed tools over one case's latest run | `signal_catalog`, `query_signal` read any recorded signal, any window, any case in the workspace |
| New computation | none: only precomputed findings | `run_python` in a separate sandboxed process; numpy (and pandas if installed) |
| Cross-run comparison | `machine_history` (earlier cases, no data) | `find_cases` + `compare_runs`; other cases' data only when authorised for the model |
| Saved team methods | none | `Library` of team methods (versioned by code fingerprint), run in the sandbox; `POST /engine/methods` |
| Follow-up questions | none (one investigation per run) | `question` on `POST /engine/cases/{id}/agent`; "Ask about this case" in the AI agent panel |
| Evidence for results | tool calls cited, numbers checked against cited output | plus every computation stored: code, method version, inputs (rows, first/last source row, sha256), isolation, limits |
| Timing and clocks | clock caveats in the command chain | `onset_order` refuses an order across clocks with no declared offset, or inside the timing uncertainty |
| Units | converted at import | catalogue shows the converted-from unit; `compare_runs` converts only known pairs and lists what it left out |
| Model testing | citation and root-cause checks on every report | plus a known-answer eval (6 questions) with -2 for a wrong answer and 0 for an honest "cannot tell" |
| MCP for external AI clients | no | no (deliberately later) |
| Fleet-scale storage, parallel sub-agents | no | no |

## What changed

- `backend/app/compute/data.py`: read-only readings of any case: catalogue, search, series with provenance.
- `backend/app/compute/methods.py`: `onset`, `onset_order` (with `a_minus` for tracking error), `lag`, `compare_runs`; team method library.
- `backend/app/compute/sandbox.py` + `_child.py`: runs agent-written or team Python.
- `backend/app/agent/tools.py`: six new tools; `runner.py` (agent v3): follow-up questions with a required, cited premise check and answer.
- `backend/app/compute/evalcases.py`, `scripts/validation/agent_evals.py`: SYNTHETIC known-answer cases and the scored evaluation.
- Frontend: one **Upload data** button (recordings and CSV/JSON); "Ask about this case"; "How it was computed".

## Sandbox

Every computation runs in a new process started with `python -I -B` in an empty temporary folder that is deleted
afterwards, with:
- operating-system isolation: a network namespace with no interfaces (Linux), or `sandbox-exec` with network denied,
  writes only to its folder and Veyra's data folder unreadable (macOS). No OS isolation → computations are refused
  unless the operator sets `VEYRA_COMPUTE_ALLOW_WEAK_SANDBOX=true`;
- limits: 20 s CPU, 30 s wall clock (process group killed), 1.5 GB memory where the OS enforces it, 32 MB files, 256 open files;
- an audit hook: no sockets, processes, ctypes, or file access outside its folder and the Python libraries.

Tested refusals: `import socket`, `importlib.import_module('socket')`, `__import__('subprocess')`, `os.system`,
writing `/tmp`, reading `/etc/passwd`, listing `/`, an endless loop, a 4 GB allocation.

It is not a virtual machine. On Linux the file restriction is the audit hook only; for a hosted multi-tenant
service, computations should move to a separate container or microVM per workspace.

## Known-answer evaluation (SYNTHETIC)

Two cases on legged robot QD-7. Torque drops at 40.0 s; tracking is lost at 40.5 s (robot clock, 2 ms bound);
network RTT jumps at 39.0 s on the console clock with no declared offset; position was recorded in radians.

| Question | Known answer |
|---|---|
| order_same_clock | torque first, by 0.5 s |
| order_unknown_clocks | cannot tell (no declared offset) |
| units | normal-period command median ≈ 30° |
| false_premise | "torque increased" is false: it dropped |
| unconfirmed_cause | no confirmed cause |
| cross_run | no: the earlier run never departs |

Deterministic checks of the tools on these answers run in `backend/tests/test_compute.py`. The model-in-the-loop
score is produced by `scripts/validation/agent_evals.py` and written to `docs/results/agent_eval_results.json`.

## Limits

- Six questions on synthetic data are a smoke test, not a benchmark. The next step is the same scoring on blind
  replays of real cases (ALFA, then real data with the owner's agreement).
- Readings are held in memory per case; very large recordings (> a few million readings) need a columnar store.
- No MCP server, fleet monitoring or parallel sub-agents yet.
- The agent never confirms a root cause; an engineer decides.
