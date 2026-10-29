"""Record the public demo's walkthrough with the live investigator (local only; needs the Claude Code sign-in).

  python scripts/demo/record_walkthrough.py <sample-name> <version> "<observation>; <observation>; ..." [keyword ...]

1. A fresh database; the sample's real log through the upload path; the investigator runs (round 1).
2. The engineer's result is recorded against round 1's next action: the outcome that best matches the keywords is the
   result selected, and the observations are only what the engineer observed (kept apart, as the product does).
3. The investigator runs again (round 2).
4. Written to fixtures/real/public/<sample>/recorded_investigation.<version>.json. Earlier versions are never touched.
Everything recorded is the investigator's real output; nothing is edited.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.agent import investigator  # noqa: E402
from app.agent.llm import from_environment  # noqa: E402
from app.store.sqlite_store import CaseStore  # noqa: E402
from app.workflows import public_samples  # noqa: E402


def main():
    name, version, observed, keywords = sys.argv[1], sys.argv[2], sys.argv[3], [k.lower() for k in sys.argv[4:]]
    model = from_environment()
    assert model.available(), "No model: run with VEYRA_AGENT_BACKEND=claude-code and a Claude Code sign-in"
    store = CaseStore(Path(tempfile.mkdtemp()) / "walkthrough.db")
    cid = public_samples.run_sample(store, name)["case_id"]
    store.append(cid, "model_authorization", {"provider": model.provider, "model": model.model, "scope": "Public log, demo walkthrough",
                                             "authorized_by": "Demo recording", "evidence": "Log from a public issue thread"}, actor="Demo recording")
    say = lambda s: print(f"  {s['label']} -> {s['summary']}", flush=True)  # noqa: E731
    r1 = investigator.investigate(store, cid, model=model, on_step=say)
    print("round 1:", r1["mode"], r1["seconds"], "s", r1["result"]["headline"], flush=True)
    assert r1["mode"] == "agent", r1["fallback_reason"]
    na = r1["result"]["next_action"]
    score = lambda o: sum(k in (o["label"] + " " + o["means"]).lower() for k in keywords)  # noqa: E731
    pick = max(na["outcomes"], key=score)
    print("engineer picks:", pick["label"], flush=True)
    store.append(cid, "engineer_finding", {"action": na["action"][:500], "outcome_selected": pick["label"][:200], "result": "found",
                                           "observations": observed}, actor="Engineer")
    time.sleep(1)
    r2 = investigator.investigate(store, cid, model=model, on_step=say)
    print("round 2:", r2["mode"], r2["seconds"], "s", r2["result"]["headline"], flush=True)
    assert r2["mode"] == "agent", r2["fallback_reason"]
    entries = [{"kind": e["kind"], "recorded_at": e["recorded_at"], "actor": e["actor"], "payload": e["payload"]} for e in store.ledger(cid)
               if (e["kind"] == "agent_report" and e["payload"].get("mode") == "agent") or e["kind"] == "engineer_finding"]
    target = public_samples.SAMPLES[name]["dir"] / f"recorded_investigation.{version}.json"
    if target.exists():
        raise SystemExit(f"{target} exists: recordings are versioned, pick a new version")
    from app.agent.investigator import INTEGRITY_VERSION, INVESTIGATOR_VERSION
    target.write_text(json.dumps({"version": version, "note": f"Recorded {time.strftime('%Y-%m-%d %H:%M')} with the live investigator ({model.model}) on a local instance.",
                                  "investigator": INVESTIGATOR_VERSION, "integrity_rules": INTEGRITY_VERSION, "entries": entries}, indent=1))
    print("WROTE", target, flush=True)
    store.close()


if __name__ == "__main__":
    main()
