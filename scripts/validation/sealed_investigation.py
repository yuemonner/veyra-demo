"""Local frozen-tool experiment. Prepare -> run -> score are separate invocations.

The run phase never opens evaluator-labels.json and never invokes the investigation engine.
The score phase is a separate process without model access. Automated checks verify execution,
not semantic truth or general-purpose autonomy; engineering review remains mandatory.
"""
import argparse
import hashlib
import json
import random
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.agent import investigation_loop as loop
from app.agent.llm import from_environment
from app.ingest.profile import parse_profile
from app.store.sqlite_store import CaseStore


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False))


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def system_manifest():
    return {"app": loop.code_manifest(), "tools": loop.digest(loop.TOOLS), "prompt": loop.digest(loop.PROMPT),
            "evaluator_and_runner": file_hash(Path(__file__))}


def prepare(base):
    base.mkdir(parents=True, exist_ok=False)
    # Freeze before generating any scenario data or labels. Nothing is tuned after seeing results.
    write(base / "frozen-system.json", system_manifest())
    rng = random.Random(secrets.randbits(128))
    scenarios = ["network", "controller", "normal", "unrelated_clocks"]
    rng.shuffle(scenarios)
    entries, labels = [], {}
    mapping = {"rtt": "network.rtt", "heartbeat": "app.heartbeat_interval", "cycle": "controller.cycle_time"}
    for index, scenario in enumerate(scenarios):
        key = "recording-" + secrets.token_hex(4)
        onset = rng.randint(120, 170)
        files = []
        for signal in mapping:
            rows = []
            for t in range(360):
                value = {"rtt": 42, "heartbeat": 50, "cycle": 10}[signal] + rng.uniform(-.5, .5)
                if onset <= t < onset + 65:
                    if scenario == "network" and signal == "rtt": value += 420
                    if scenario == "controller" and signal == "cycle": value += 180
                    if scenario in ("network", "controller") and signal == "heartbeat" and t >= onset + 3: value += 430
                    if scenario == "unrelated_clocks" and signal in ("rtt", "heartbeat"): value += 300
                rows.append({"timestamp": 1700000000 + t, "asset": "M", "signal": signal, "value": round(value, 3), "unit": "ms"})
            name = f"{key}/{signal}.jsonl"
            path = base / "evidence" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(json.dumps(r) for r in rows))
            clock = "device-b" if scenario == "unrelated_clocks" and signal == "heartbeat" else "device-a"
            files.append({"path": name, "sha256": file_hash(path), "clock_domain": clock})
        entries.append({"id": key, "files": files})
        labels[key] = {"scenario": scenario, "synthetic_onset_epoch": 1700000000 + onset,
                       "interpretation_boundary": "Network measurements identify the probe path, not a confirmed root cause; controller and physical feedback remain incomplete.",
                       "expected_caution": "No credible departure" if scenario == "normal" else "No temporal ordering across unrelated clocks" if scenario == "unrelated_clocks" else "Candidate mechanism only"}
    write(base / "inputs.json", {"signal_map": mapping, "cases": entries, "synthetic": True,
        "protocol": "No incident window, baseline, diagnosis or labels supplied to Agent. Source mapping uses published canonical concepts."})
    write(base / "evaluator-labels.json", labels)
    (base / "evaluator-labels.json").chmod(0o600)
    write(base / "seal.json", {"frozen_system": file_hash(base / "frozen-system.json"), "inputs": file_hash(base / "inputs.json"),
                               "labels": file_hash(base / "evaluator-labels.json")})
    print(json.dumps({"prepared": len(entries), "output": str(base), "agent_executed": False}))


def verify(base):
    frozen = json.loads((base / "frozen-system.json").read_text())
    seal = json.loads((base / "seal.json").read_text())
    if frozen != system_manifest() or file_hash(base / "frozen-system.json") != seal["frozen_system"] or file_hash(base / "inputs.json") != seal["inputs"]:
        raise ValueError("Code/tool contract/protocol changed after freeze. Create a NEW experiment; preserve the old one.")
    inputs = json.loads((base / "inputs.json").read_text())
    for item in inputs["cases"]:
        for spec in item["files"]:
            path = (base / "evidence" / spec["path"]).resolve()
            if not path.is_relative_to((base / "evidence").resolve()) or file_hash(path) != spec["sha256"]:
                raise ValueError("Source changed or escaped the evidence directory")
    return inputs


def run(base):
    inputs = verify(base)
    output = base / "runs"
    output.mkdir(exist_ok=False)
    model = from_environment()
    index = []
    for item in inputs["cases"]:
        store = CaseStore(output / (item["id"] + ".db"))
        try:
            case = store.create_case(machine_id="M", title="Frozen recording · synthetic acceptance", actor="Experiment", synthetic=True)
            for spec in item["files"]:
                profile = parse_profile({"profile_id": "frozen-standard-mapping", "version": 1, "format": "jsonl", "synthetic": True,
                    "clock_domain": spec["clock_domain"], "timestamp_format": "epoch", "epoch_unit": "s", "signal_map": inputs["signal_map"],
                    "columns": {k: k for k in ("timestamp", "asset", "signal", "value", "unit")}})
                store.commit_import(case["id"], (base / "evidence" / spec["path"]).read_bytes(), Path(spec["path"]).name, profile, actor="Experiment")
            path = output / (item["id"] + ".json")
            session = loop.prepare(store, case["id"], model, save=lambda value: write(path, value))
            write(output / (item["id"] + ".evidence.json"), session.snapshot)
            if session.result["state"] == "running": session.run(model)
            verify(base)
            index.append({"input": item["id"], "case_id": case["id"], "report": path.name, "sha256": file_hash(path),
                          "engine_runs": len(store.runs(case["id"])), "state": session.result["state"]})
            print(json.dumps(index[-1]), flush=True)
        finally:
            store.close()
    write(output / "index.json", index)


def score(base):
    verify(base)
    seal = json.loads((base / "seal.json").read_text())
    if file_hash(base / "evaluator-labels.json") != seal["labels"]: raise ValueError("Labels changed")
    # Labels are opened ONLY here, after the run artifacts have been sealed.
    labels = json.loads((base / "evaluator-labels.json").read_text())
    index = json.loads((base / "runs/index.json").read_text())
    checks = []
    for item in index:
        path = base / "runs" / item["report"]
        if file_hash(path) != item["sha256"]: raise ValueError("Run artifact changed after seal")
        r = json.loads(path.read_text())
        payload_hash = loop.digest({k: v for k, v in r.items() if k != "artifact_sha256"})
        calls = {c["id"]: c for c in r["calls"]}
        good_analyses = [c for c in calls.values() if c["analysis"] and c["status"] == "completed"]
        revisions = r["revisions"]
        result_used = any(set(h["support"] + h["against"]) & {c["id"] for c in good_analyses} for rev in revisions[1:] for h in rev["hypotheses"])
        outcomes = [{"method": c["output"].get("provenance", {}).get("method"), "result": c["output"].get("result")} for c in good_analyses]
        actual_model = r["mode"] == "live_agent" and r["provider"] in ("openai", "anthropic") and r["model_calls"] > 0
        stopped = r["state"] == "finished" and bool((r.get("report") or {}).get("minimum_missing_information"))
        checks.append({"input": item["input"], "revealed_label": labels[item["input"]], "state": r["state"],
            "model_executed": actual_model, "engine_precomputed_runs": item["engine_runs"],
            "artifact_intact": r["artifact_sha256"] == payload_hash,
            "tool_outputs_intact": all(c["output_sha256"] == loop.digest(c["output"]) for c in calls.values()),
            "completed_calculations": len(good_analyses), "result_used_in_revision": result_used,
            "recorded_stop_and_missing_information": stopped, "calculation_results": outcomes,
            "hypothesis_status_changed": any(any(h["status"] != old["status"] for h, old in zip(rev["hypotheses"], rev["previous"])) for rev in revisions[1:]),
            "semantic_review": "REQUIRED: check that each inference and stop is justified, source endpoints/units are meaningful, and the report respects this scenario's limits."})
    ran = sum(c["model_executed"] for c in checks)
    result = {"status": "BLOCKED" if ran == 0 else "REQUIRES_ENGINEERING_REVIEW", "model_runs": ran, "cases": checks,
        "agent_autonomy_validated": False,
        "boundary": "Separate-process label scoring with restricted Agent tools, not hardware/OS isolation of the whole evaluator. Synthetic contract checks do not establish unfamiliar real-fault competence. No success is awarded merely for finishing."}
    write(base / "evaluation.json", result)
    print(json.dumps({"status": result["status"], "model_runs": ran, "autonomy_validated": False}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare", "run", "score"])
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    {"prepare": prepare, "run": run, "score": score}[args.phase](args.directory.resolve())
