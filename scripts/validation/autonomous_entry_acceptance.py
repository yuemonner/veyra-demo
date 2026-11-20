"""Reproducible acceptance on a supported original recording, without supplied windows.

python scripts/validation/autonomous_entry_acceptance.py recording.ulg new-output-directory
Optional --agent --allow-public-model sends this public case to the configured provider.
No source-specific mappings, precomputed findings, healthy baseline, or timestamp arguments.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.agent.llm import from_environment  # noqa: E402
from app.agent.runner import run_agent  # noqa: E402
from app.ingest import bags, ulog  # noqa: E402
from app.store.sqlite_store import CaseStore  # noqa: E402
from app.workflows import autopilot  # noqa: E402
from app.workflows.debrief import debrief_recording  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--agent", action="store_true")
    parser.add_argument("--allow-public-model", action="store_true", help="Explicit authorization for this public recording only")
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=False)  # never overwrite a previous acceptance baseline

    def save(name, value):
        (args.output / name).write_text(json.dumps(value, indent=2, default=str))

    frozen = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted((ROOT / "backend/app").rglob("*.py")) + sorted((ROOT / "backend/app/machines/profiles").glob("*.json"))}
    save("protocol.json", {"product_hashes": frozen, "source_sha256": bags.sha256_file(args.recording),
         "supplied_window": None, "supplied_baseline": None, "model_requested": args.agent})
    summary = {"agent": "blocked: not requested", "errors": {}, "root_cause": "unconfirmed"}
    store = CaseStore(args.output / "acceptance.db")
    try:
        mapping = {} if ulog.is_ulog(args.recording) else bags.suggest(bags.inspect(args.recording))
        uploaded = debrief_recording(store, args.recording, mapping, machine_id="UNFAMILIAR-RECORDING",
            title="Unfamiliar recording acceptance", actor="Acceptance runner", synthetic=False,
            progress=lambda step: print(step, flush=True))
        save("upload.json", uploaded)
        cid = uploaded["case_id"]
        run = store.latest_run(cid)
        save("run.json", run)
        save("discovery.json", store.discovery(cid))
        save("questions.json", store.questions(cid))
        save("autopilot.json", autopilot.advance(store, cid))
        summary.update(case_id=cid, run_id=run["id"], entry=run["entry"], diagnostic_execution=run["diagnostic_execution"])
        if args.agent:
            model = from_environment()
            if not args.allow_public_model:
                summary["agent"] = "blocked: public-data model authorization not supplied"
            elif not model.available():
                summary["agent"] = "blocked: configured provider credentials/model unavailable"
            else:
                store.append(cid, "model_authorization", {"provider": model.provider, "model": model.model,
                    "scope": "This public recording, one acceptance run", "authorized_by": "CLI --allow-public-model",
                    "evidence": "Explicit CLI flag; no other cases shared"}, actor="Acceptance runner")
                try:
                    result = run_agent(store, cid, model=model)
                    save("agent_report.json", result)
                    summary["agent"] = "ran; inspect tool_calls and citations_valid before claiming validation"
                except Exception as error:
                    summary["agent"] = "blocked/failed: " + type(error).__name__ + ": " + str(error)
        save("ledger.json", store.ledger(cid))
        save("integrity.json", store.verify(cid))
    except Exception as error:
        summary["errors"]["execution"] = type(error).__name__ + ": " + str(error)
    finally:
        store.close()
        summary["frozen_product_unchanged"] = all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == h for p, h in frozen.items())
        save("summary.json", summary)
        save("seal.json", {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in args.output.glob("*.json") if p.name != "seal.json"})
    print(json.dumps({"entry_status": summary.get("entry", {}).get("status"), "agent": summary["agent"], "errors": summary["errors"]}))
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
