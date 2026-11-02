"""Record a conversational-debugging session with the live AI investigator (local only; needs the Claude Code sign-in).

  python scripts/demo/record_discussion.py <sample-name> <version> <messages.json>

messages.json: [{"kind": "guess" | "result", "message": "..."}, ...] — the engineer's side, scripted for the demo.

1. A fresh database; the sample's real log through the upload path; Veyra's first investigation round is replayed from
   the sample's recorded walkthrough (round 1 only), so the discussion starts where the public demo starts.
2. Each engineer message runs one live discussion round (app.agent.discussion.discuss): Veyra tests it against the data
   with read-only tools, says which parts hold, updates the fault space and proposes the next check.
3. Written to fixtures/real/public/<sample>/recorded_discussion.<version>.json. Earlier versions are never touched.
Veyra's side is the model's real output, verified like any live round; nothing is edited.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.agent import discussion  # noqa: E402
from app.agent.llm import from_environment  # noqa: E402
from app.store.sqlite_store import CaseStore  # noqa: E402
from app.workflows import public_samples  # noqa: E402


def main():
    name, version, script = sys.argv[1], sys.argv[2], json.loads(Path(sys.argv[3]).read_text())
    sample = public_samples.SAMPLES[name]
    target = sample["dir"] / f"recorded_discussion.{version}.json"
    if target.exists():
        raise SystemExit(f"{target} exists: recordings are versioned, pick a new version")
    model = from_environment()
    assert model.available(), "No model: run with VEYRA_AGENT_BACKEND=claude-code and a Claude Code sign-in"
    store = CaseStore(Path(tempfile.mkdtemp()) / "discussion.db")
    cid = public_samples.run_sample(store, name)["case_id"]
    store.append(cid, "model_authorization", {"provider": model.provider, "model": model.model, "scope": "Public log, demo discussion",
                                             "authorized_by": "Demo recording", "evidence": "Log from a public issue thread"}, actor="Demo recording")
    walk = json.loads(public_samples.recording(sample["dir"]).read_text())
    first = next(e for e in walk["entries"] if e["kind"] == "agent_report")
    store.append_trusted(cid, "agent_report", {**first["payload"], "run_id": store.latest_run(cid)["id"]}, actor=first["actor"], source="walkthrough round 1 (replayed)")
    say = lambda s: print(f"    {s['label']} -> {s['summary']}", flush=True)  # noqa: E731
    for i, turn in enumerate(script, 1):
        print(f"round {i} ({turn['kind']}): {turn['message']}", flush=True)
        out = discussion.discuss(store, cid, turn["message"], kind=turn["kind"], model=model, on_step=say)
        if out.get("failure"):
            raise SystemExit(f"round {i} failed: {out['failure']}")
        r = out["result"]
        print(f"  {out['seconds']} s · {r['reply']}", flush=True)
        for p in r["parts"]:
            print(f"   [{p['verdict']}] {p['claim']}", flush=True)
        print(f"  next: {r['next_check']['action']}", flush=True)
        time.sleep(1)
    entries = [{"kind": e["kind"], "recorded_at": e["recorded_at"], "actor": e["actor"], "payload": e["payload"]} for e in store.ledger(cid)
               if e["kind"] == "agent_report" and e["payload"].get("kind") == "discussion_round"]
    target.write_text(json.dumps({"version": version, "discussion": discussion.DISCUSSION_VERSION,
                                  "note": f"Recorded {time.strftime('%Y-%m-%d %H:%M')} with the live AI investigator ({model.model}) on a local instance. "
                                          "The engineer's messages are scripted for this demo (not from the original issue thread); Veyra's replies are the model's real output.",
                                  "engineer_messages": "scripted for this demo", "starts_from": "walkthrough round 1", "entries": entries}, indent=1))
    print("WROTE", target, flush=True)
    store.close()


if __name__ == "__main__":
    main()
