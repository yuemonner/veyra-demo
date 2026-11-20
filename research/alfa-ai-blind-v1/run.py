"""Run the preregistered ALFA blind evaluation (see PREREGISTRATION.md). Labels are NOT read here except after each
investigation, into the stored record, for the scorer.

Usage: VEYRA_AGENT_BACKEND=claude-code PYTHONPATH=backend python research/alfa-ai-blind-v1/run.py <processed dir> <worker> <workers>
"""
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "backend"))
FROZEN = "e9bc6ce"
FROZEN_FILES = ["backend/app/agent/recording_tools.py", "backend/app/agent/investigator.py", "backend/app/ingest/bags.py",
                "backend/app/workflows/debrief.py", "backend/app/ingest/ulog.py", "backend/app/agent/llm.py"]
REQUEST = "Check this flight: did a control fault occur, when, and where on the aircraft?"
LABEL_TOPICS = ["/failure_status/aileron", "/failure_status/rudder", "/failure_status/elevator", "/failure_status/engines"]


def frozen_ok() -> bool:
    return subprocess.run(["git", "diff", "--quiet", FROZEN, "--", *FROZEN_FILES], cwd=ROOT).returncode == 0


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def labels(bag: Path, t0: float) -> dict:
    from rosbags.highlevel import AnyReader
    out = {}
    with AnyReader([bag]) as r:
        cons = [c for c in r.connections if c.topic in LABEL_TOPICS]
        # rosbags reads EVERY topic when given an empty list: a flight without label topics has none to read
        for c, t, raw in (r.messages(connections=cons) if cons else []):
            v = r.deserialize(raw, c.msgtype).data
            if v and c.topic not in out:
                out[c.topic] = {"first_nonzero_s": round(t / 1e9 - t0, 3), "value": int(v)}
        present = sorted({c.topic for c in r.connections if c.topic in LABEL_TOPICS})
    return {"present_topics": present, "nonzero": out,
            "onset_s": min((x["first_nonzero_s"] for x in out.values()), default=None)}


def run_one(folder: Path, alias: str, out: Path) -> dict:
    from app.agent import investigator as inv
    from app.agent.llm import from_environment
    from app.agent.recording_tools import RecordingTools
    from app.ingest import bags
    from app.store.sqlite_store import CaseStore
    from app.workflows.debrief import debrief_recording
    src = next(folder.glob("*.bag"))
    tmp = Path(tempfile.mkdtemp(prefix="alfa-blind-"))
    bag = tmp / f"{alias}.bag"
    shutil.copyfile(src, bag)
    store = CaseStore(tmp / "e.db")
    mapping = bags.suggest(bags.inspect(bag))
    machine = alias.replace("flight", "UAV")
    cid = debrief_recording(store, bag, mapping, machine_id=machine, title=f"{alias}: control fault check", actor="Evaluation",
                            source_name=bag.name, report={"stated_reason": REQUEST})["case_id"]
    model = from_environment()
    store.append(cid, "model_authorization", {"provider": model.provider, "scope": "public CC0 research data (ALFA)", "authorized_by": "Evaluation",
                                              "evidence": "public dataset"}, actor="Evaluation")
    tools = RecordingTools(store, cid)
    attempts = []
    for _ in range(3):
        t0 = time.time()
        r = inv.investigate(store, cid, model=model, keep_outputs=True)
        attempts.append({"mode": r["mode"], "fallback_reason": r.get("fallback_reason"), "wall_s": round(time.time() - t0, 1)})
        if r["mode"] == "agent":
            break
    rec = {"alias": alias, "folder": folder.name, "input_sha256": sha256(src), "frozen_commit": FROZEN, "request": REQUEST,
           "cli": subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip(),
           "mapping": mapping, "sheet": {"version": tools.sheet["version"], "sha256": tools.sheet["sha256"], "lines": tools.raw},
           "attempts": attempts, "investigation": {k: r.get(k) for k in ("mode", "model", "fallback_reason", "steps", "result", "dropped",
                                                                          "usage", "model_calls", "seconds", "tool_outputs", "version")},
           "labels": labels(src, tools.rec.t0)}   # read only now, after the investigation
    (out / f"{alias}.json").write_text(json.dumps(rec, indent=1, default=str))
    store.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return rec


def main(processed: Path, worker: int, workers: int, only=None) -> int:
    if not frozen_ok():
        print(f"Refusing to run: the evaluated files differ from {FROZEN}")
        return 2
    out = HERE / "results"
    out.mkdir(exist_ok=True)
    folders = sorted(p for p in processed.iterdir() if p.is_dir() and any(p.glob("*.bag")))
    for i, folder in enumerate(folders):
        alias = f"flight-{i + 1:02d}"
        if i % workers != worker or (out / f"{alias}.json").exists():
            continue
        if only and alias not in only:
            continue
        try:
            rec = run_one(folder, alias, out)
            inv_ = rec["investigation"]
            print(json.dumps({"alias": alias, "mode": inv_["mode"], "steps": len(inv_.get("steps") or []), "seconds": inv_.get("seconds"),
                              "headline": (inv_.get("result") or {}).get("headline")}), flush=True)
        except Exception as exc:  # noqa: BLE001  recorded, never hidden
            (out / f"{alias}.error.json").write_text(json.dumps({"alias": alias, "folder": folder.name, "error": f"{type(exc).__name__}: {exc}"}))
            print(json.dumps({"alias": alias, "error": str(exc)[:200]}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), set(sys.argv[4].split(",")) if len(sys.argv) > 4 else None))
