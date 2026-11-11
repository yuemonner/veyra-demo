"""ALFA evaluation v2 (see PREREGISTRATION.md). Usage:
  VEYRA_AGENT_BACKEND=claude-code PYTHONPATH=backend python research/alfa-ai-blind-v2/run2.py <arm: veyra|alone> <processed dir> <worker> <workers> [aliases]"""
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
sys.path.insert(0, str(HERE.parent / "alfa-ai-blind-v1"))
from run import REQUEST, labels, sha256  # noqa: E402  (v1 helpers: neutral request, label reader used only after a run)

FROZEN = "30f459b"
FROZEN_FILES = ["backend/app/agent/recording_tools.py", "backend/app/agent/investigator.py", "backend/app/ingest/bags.py",
                "backend/app/workflows/debrief.py", "backend/app/agent/llm.py"]
ALONE_SYSTEM = ("You are an expert robotics and UAV diagnostics engineer. You are given one recorded flight: a table of the "
                "recorded signals sampled once per second (seconds from the start), and the logged events. Answer the request "
                "from this data only. Be specific about what happened, when (in seconds), and where on the aircraft. If the data "
                "shows no fault, say so. Reply by calling submit_report once.")


def sig(x: float) -> str:
    return f"{x:.3g}"


def table(rec) -> str:
    import bisect
    keys = list(rec.series)
    out = ["t_s," + ",".join(f"{k} ({rec.series[k]['unit']})" for k in keys)]
    for t in range(0, int(rec.span) + 1):
        row = [str(t)]
        for k in keys:
            s = rec.series[k]
            i = min(bisect.bisect_left(s["t"], t), len(s["t"]) - 1)
            if i > 0 and abs(s["t"][i - 1] - t) < abs(s["t"][i] - t):
                i -= 1
            row.append(sig(s["v"][i]) if abs(s["t"][i] - t) <= 1.0 else "")
        out.append(",".join(row))
    ev = "\n".join(f"t={e['t']:.1f} s {e['key']} {e['event'] or ''} {e['code'] or ''}".strip() for e in rec.events[:150])
    return "SIGNALS (1 Hz)\n" + "\n".join(out) + "\n\nEVENTS\n" + (ev or "none")


def run_one(arm: str, folder: Path, alias: str, out: Path) -> dict:
    from app.agent import investigator as inv
    from app.agent.llm import from_environment
    from app.agent.recording_tools import RecordingTools
    from app.ingest import bags
    from app.store.sqlite_store import CaseStore
    from app.workflows.debrief import debrief_recording
    src = next(folder.glob("*.bag"))
    tmp = Path(tempfile.mkdtemp(prefix="alfa-v2-"))
    bag = tmp / f"{alias}.bag"
    shutil.copyfile(src, bag)
    store = CaseStore(tmp / "e.db")
    mapping = bags.suggest(bags.inspect(bag))
    cid = debrief_recording(store, bag, mapping, machine_id=alias.replace("flight", "UAV"), title=f"{alias}: control fault check", actor="Evaluation",
                            source_name=bag.name, report={"stated_reason": REQUEST})["case_id"]
    model = from_environment()
    tools = RecordingTools(store, cid)
    rec = {"arm": arm, "alias": alias, "folder": folder.name, "input_sha256": sha256(src), "frozen_commit": FROZEN, "request": REQUEST,
           "cli": subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip(), "attempts": []}
    if arm == "veyra":
        store.append(cid, "model_authorization", {"provider": model.provider, "scope": "public CC0 research data (ALFA)", "authorized_by": "Evaluation",
                                                  "evidence": "public dataset"}, actor="Evaluation")
        for _ in range(3):
            t0 = time.time()
            r = inv.investigate(store, cid, model=model, keep_outputs=True)
            rec["attempts"].append({"mode": r["mode"], "fallback_reason": r.get("fallback_reason"), "wall_s": round(time.time() - t0, 1)})
            if r["mode"] == "agent":
                break
        rec["sheet"] = {"version": tools.sheet["version"], "sha256": tools.sheet["sha256"], "lines": tools.raw}
        rec["mapping"] = mapping
        rec["investigation"] = {k: r.get(k) for k in ("mode", "model", "fallback_reason", "steps", "result", "dropped", "usage", "model_calls",
                                                      "seconds", "tool_outputs", "version")}
    else:
        prompt = f"Request: {REQUEST}\n\n{table(tools.rec)}"
        result, err, usage = None, None, None
        for _ in range(3):
            t0 = time.time()
            try:
                reply = model.create(system=ALONE_SYSTEM, messages=[{"role": "user", "content": prompt}], tools=[inv.SUBMIT_SPEC])
                call = next((b for b in reply.get("content") or [] if b.get("type") == "tool_use" and b.get("name") == "submit_report"), None)
                text = " ".join(b.get("text", "") for b in reply.get("content") or [] if b.get("type") == "text")
                result = call["input"] if call else {"headline": text[:300], "explanation": text, "status": "cause_unknown", "hypotheses": []}
                usage, err = reply.get("usage"), None
            except Exception as exc:  # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"[:300]
            rec["attempts"].append({"mode": "agent" if result else "failed", "fallback_reason": err, "wall_s": round(time.time() - t0, 1)})
            if result:
                break
        rec["prompt_chars"] = len(prompt)
        rec["investigation"] = {"mode": "agent" if result else "failed", "model": getattr(model, "model", None), "fallback_reason": err, "steps": [],
                                "result": result, "usage": usage, "seconds": rec["attempts"][-1]["wall_s"]}
    rec["labels"] = labels(src, tools.rec.t0)   # read only now, after the run
    (out / f"{alias}.json").write_text(json.dumps(rec, indent=1, default=str))
    store.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return rec


def main(arm: str, processed: Path, worker: int, workers: int, only=None) -> int:
    if subprocess.run(["git", "diff", "--quiet", FROZEN, "--", *FROZEN_FILES], cwd=ROOT).returncode != 0:
        print(f"Refusing to run: the evaluated files differ from {FROZEN}")
        return 2
    out = HERE / f"results-{arm}"
    out.mkdir(exist_ok=True)
    folders = sorted(p for p in processed.iterdir() if p.is_dir() and any(p.glob("*.bag")))
    for i, folder in enumerate(folders):
        alias = f"flight-{i + 1:02d}"
        if i % workers != worker or (out / f"{alias}.json").exists() or (only and alias not in only):
            continue
        try:
            rec = run_one(arm, folder, alias, out)
            iv = rec["investigation"]
            if iv["mode"] != "agent":   # infrastructure failure: kept aside, rerun later
                (out / "infrastructure_failures").mkdir(exist_ok=True)
                shutil.move(str(out / f"{alias}.json"), str(out / "infrastructure_failures" / f"{alias}.{int(time.time())}.json"))
            print(json.dumps({"arm": arm, "alias": alias, "mode": iv["mode"], "seconds": iv.get("seconds"), "headline": ((iv.get("result") or {}).get("headline") or "")[:110]}), flush=True)
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"arm": arm, "alias": alias, "error": f"{type(exc).__name__}: {exc}"[:200]}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], Path(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), set(sys.argv[5].split(",")) if len(sys.argv) > 5 else None))
