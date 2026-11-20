# Local Agent investigation experiment

Status: implemented experimental loop and real local tool validation. **Real Agent acceptance: BLOCKED**, as requested by the owner. No model keys, model calls, push or deployment were used for this delivery.

## Open tomorrow's preview

The separate local preview is at **http://127.0.0.1:3049/field-case?case=case-7fa04e50dee9&view=investigation**.
It uses the existing workspace and private test account; the previous 3019/3029 previews are left alone. The interface remains English.

If the processes have stopped, open `.local-private/start-investigation.command`, or run:

```sh
VEYRA_PYTHON=<python with the backend requirements> sh scripts/local_investigation_preview.sh
```

The local auto-sign-in is the existing test-account mechanism and listens on loopback only. Keep this preview local; it is not a customer-hosted instance.

## Demonstrate accurately

1. Select one of the four **Investigation lab · recording N · SYNTHETIC** cases and open **Investigation lab**.
2. **Check Agent readiness** freezes the evidence and tool contract and records **Blocked**, with zero model calls. No scripted Agent is substituted.
3. **Validate local tools · no Agent** performs an actual, deterministic tool smoke check on that case's imported data. It inventories signals, invokes candidate discovery, reads samples, proposes an exploratory onset computation, executes it, records evidence references and stops with insufficient evidence for diagnosis. This is fixed software selection triggered by a person, **not autonomous investigation**.
4. Expand **Execute the calculation → Inspect inputs, result & provenance**. Show the calculation result, input-row hashes, baseline assumption, output hash and frozen evidence identity.
5. Show the revision record and **Why stop / Minimum evidence needed next**. The initial fraction is only an exploratory comparison, not a verified healthy baseline.
6. Download the execution JSON. A finished tool-validation run does not make the Agent accepted or the fault resolved.

A defensible description: “The tools execute on source data and retain an auditable record. We have implemented the model-controlled loop, but this session deliberately has no configured model, so autonomous investigation is not yet demonstrated.”

## Implemented architecture

`backend/app/agent/investigation_loop.py` is a separate path from the existing engine-first Agent. It freezes integrity-checked imported canonical evidence, source hashes, tool schemas, system prompt and application code hashes. Existing engine runs, findings, case titles, closures, healthy-reference declarations, incident windows and evaluator labels are not exposed to the model.

This boundary is **imported canonical evidence**, not arbitrary access to every undecoded byte/topic of an original recording. Unsupported original fields remain outside the available analytical surface. Semantic mappings and known units still require engineering validation.

The model can choose inventory, generic candidate discovery, source samples/events, competing hypotheses, a calculation proposal, execution, revision and stopping. A proposal names competing hypotheses, engineering justification and contrasting possible outcomes before execution. Existing onset/onset-order/lag methods are reused; optional Python uses the existing OS sandbox. Sandbox failure stays failure; isolation is not bypassed.

Enforced boundaries include:

- One frozen case; no live engine answers, other cases, equipment actions or arbitrary file/network tool.
- Known finite measurement units, actual input availability and explicit exploratory-reference selection.
- Current-run citation IDs, proposal before calculation, one execution per proposal, analysis before evidence-ranked hypotheses.
- A result-based revision before a candidate report. Missing information remains mandatory at stopping.
- Failed tools, interrupted jobs and exhausted budgets do not become successful investigations.
- Provider-specific data permission for real data. Real Agent remains disabled by `VEYRA_INVESTIGATION_AGENT_BLOCKED=true`.

The check that a citation exists does not prove its semantic entailment. Agent reports remain unreviewed hypotheses. Successful computation and a well-formed report are not independent validation.

## Sealed experiment

`scripts/validation/sealed_investigation.py` has three separate invocations:

```sh
PYTHONPATH=backend python scripts/validation/sealed_investigation.py prepare NEW_OUTPUT_DIRECTORY
PYTHONPATH=backend python scripts/validation/sealed_investigation.py run NEW_OUTPUT_DIRECTORY
PYTHONPATH=backend python scripts/validation/sealed_investigation.py score NEW_OUTPUT_DIRECTORY
```

Prepare freezes application/tool/prompt/evaluator hashes **before** generating randomized synthetic cases. The run phase never opens the label file and creates **zero engine runs**. Score runs in a separate process, reveals the sealed labels, verifies records, and reports execution and review requirements. Agent tools cannot access the evaluator file. This is a logical/tool boundary with separate-process scoring, not a separate security principal or independent external reviewer.

Four scenarios cover network-path departure, local controller departure, no injected departure and unrelated clocks. They are generic synthetic validation, not customer data or a blind real-fault benchmark. No automatic overall autonomy “pass” is emitted: inference quality and justified stopping require engineering review.

Observed artifacts: local outputs directory `sealed-investigation-2026-10-26-v2/` (not in the repository).
The result is **BLOCKED: 0 model runs**, not a positive acceptance. The local workspace copies have a legacy run solely because the current workspace requires one to render; the experimental Agent never reads it. The CLI experiment databases have none.

The initial `sealed-investigation-2026-10-26/` attempt is preserved. Its generated timestamps were in the future and the existing ingestion guard rejected them. Fixture timestamps were corrected in a newly frozen experiment; no product validation or diagnostic rule was weakened.

Once a model is authorized/configured, generate a new experiment directory and run the genuine model with `VEYRA_INVESTIGATION_AGENT_BLOCKED=false`. Preserve all earlier artifacts. Existing exposed incidents are regressions, not fresh blind benchmarks. Finishing a loop still requires independent assessment before claiming it works on unfamiliar faults.

## Operational limits

This is a local experimental worker, not a durable distributed job system. Receipts are saved after every tool; a process restart marks unfinished jobs interrupted. The code hash detects local changes but is not external attestation against an administrator. Built-in calculations were exercised; real model reasoning and successful custom sandbox execution were not established by this delivery.
