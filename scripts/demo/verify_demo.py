"""Re-verify a demo pack from its input file, with no model: anyone can run this.

1. The input file's sha256 matches the pack.
2. The evidence sheet recomputed from the file (same mapping) is byte-identical (same sha256).
3. Every data tool call the AI made, re-executed with its recorded input, returns exactly the recorded output.
4. Every quoted line in the result is on the recomputed sheet, and every number in its checks is in the cited outputs
   (the product's own verifier, the one that ran during the investigation).

Usage: PYTHONPATH=backend python scripts/demo/verify_demo.py <pack.json> <input file>
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def main(pack_path: Path, bag: Path) -> int:
    import alfa_aileron_demo as demo
    from app.agent import investigator as inv
    from app.agent.recording_tools import RECORDING_TOOLS, RecordingTools
    from app.store.sqlite_store import CaseStore
    pack = json.loads(pack_path.read_text())
    checks = []
    digest = demo.sha256(bag)
    checks.append(("input sha256 matches", digest == pack["input"]["sha256"], digest))
    store = CaseStore(Path(tempfile.mkdtemp(prefix="veyra-verify-")) / "v.db")
    from app.workflows.debrief import debrief_recording
    cid = debrief_recording(store, bag, pack["mapping"], machine_id=pack["request"]["machine_id"], title=pack["request"]["title"],
                            actor="Verifier", source_name=bag.name, report={"stated_reason": pack["request"]["report"]})["case_id"]
    tools = RecordingTools(store, cid)
    checks.append(("evidence sheet recomputed identically", tools.sheet["sha256"] == pack["sheet"]["sha256"] and tools.raw == pack["sheet"]["lines"],
                   tools.sheet["sha256"]))
    recorded = pack["investigation"].get("tool_outputs") or {}
    same, differ = 0, []
    for step in pack["investigation"]["steps"]:
        if step["tool"] not in RECORDING_TOOLS:
            continue
        try:
            value = getattr(tools, step["tool"])(**step["input"])
            text = json.dumps(value, default=str)[:24000]
        except Exception as exc:  # noqa: BLE001  the recorded call errored too: compare the error text
            text = f"{type(exc).__name__}: {exc}"
        if text == recorded.get(step["id"]):
            same += 1
        else:
            differ.append(step["id"])
    checks.append((f"data tool calls re-executed with identical output ({same})", not differ, differ))
    v = inv.verify(pack["investigation"]["result"], tools.raw, recorded)
    checks.append(("every quote and number in the result verifies", not v["problems"], v["problems"][:5]))
    for name, ok, detail in checks:
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else f": {detail}"))
    store.close()
    return 0 if all(ok for _n, ok, _d in checks) else 1


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
