"""Run the product's investigation (the same path as an upload) on sealed holdout cases, in a fresh database.

  python scripts/validation/holdout_investigate.py <cases_dir> <out_dir> [--rules-only]

<cases_dir>/<id>/log.txt and symptom.json. No answer keys are read. Each case: debrief_recording (as the upload form),
a model_authorization (public data), then app.agent.investigator.investigate with the configured model
(VEYRA_AGENT_BACKEND=claude-code uses the local Claude Code sign-in). Writes <out_dir>/<id>.json with the full result,
the steps Veyra ran, timing and the model's token use.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.agent import investigator  # noqa: E402
from app.agent.llm import from_environment  # noqa: E402
from app.store.sqlite_store import CaseStore  # noqa: E402
from app.workflows.debrief import debrief_recording  # noqa: E402


def main():
    cases, out = Path(sys.argv[1]), Path(sys.argv[2])
    rules_only = "--rules-only" in sys.argv
    out.mkdir(parents=True, exist_ok=True)
    store = CaseStore(out / f"holdout-{int(time.time())}.db")
    model = None if rules_only else from_environment()
    print("model:", None if model is None else (model.model, model.available()), flush=True)
    for d in sorted(p for p in cases.iterdir() if (p / "log.txt").exists()):
        target = out / f"{d.name}.json"
        if target.exists():
            continue
        sym = json.loads((d / "symptom.json").read_text())
        t0 = time.time()
        rec = {"id": d.name}
        try:
            res = debrief_recording(store, d / "log.txt", {}, machine_id=f"HOLDOUT-{d.name}", title=f"{sym['platform']}: {sym['symptom']}"[:200],
                                    actor="Holdout evaluation", synthetic=False, report={"stated_reason": sym["symptom"]}, source_name="log.txt")
            cid = res["case_id"]
            if model is not None:
                store.append(cid, "model_authorization", {"provider": model.provider, "model": model.model, "scope": "Public log, sealed holdout evaluation",
                                                         "authorized_by": "Evaluation operator", "evidence": "Log copied from a public thread"},
                             actor="Evaluation operator", source="evaluation")
            t1 = time.time()
            r = investigator.investigate(store, cid, model=model, on_step=lambda s: print(f"  {d.name} {s['label']} -> {s['summary']}", flush=True))
            rec.update(case_id=cid, ingest_s=round(t1 - t0, 2), investigate_s=round(time.time() - t1, 1), **{k: r[k] for k in ("mode", "fallback_reason", "model", "steps", "result", "dropped", "usage")})
        except Exception as error:  # noqa: BLE001
            rec.update(error=f"{type(error).__name__}: {error}")
        target.write_text(json.dumps(rec, indent=1, default=str))
        print(d.name, rec.get("mode"), rec.get("investigate_s"), rec.get("error") or rec.get("fallback_reason") or "", flush=True)
    store.close()
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
