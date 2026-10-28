"""REAL public failure logs (fixtures/real/public) that anyone can open in the demo with one click.

Each is a log a reporter posted in a public issue thread (link in source.json), run through the ordinary upload path
(debrief_recording) as a real, non-synthetic case. They are not blind test cases: they were part of the first sealed
evaluation batch.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from app.workflows.debrief import debrief_recording

ROOT = Path(__file__).resolve().parents[3] / "fixtures" / "real" / "public"


def _load() -> Dict[str, Dict[str, Any]]:
    out = {}
    if ROOT.exists():
        for d in sorted(ROOT.iterdir()):
            if (d / "source.json").exists() and (d / "log.txt").exists():
                out[d.name] = {**json.loads((d / "source.json").read_text()), "dir": d}
    return out


SAMPLES = _load()


def listing() -> List[Dict[str, Any]]:
    return [{"name": k, **{f: v[f] for f in ("title", "platform", "symptom", "source_url", "project")}} for k, v in SAMPLES.items()]


def run_sample(store, name: str, progress=lambda step: None) -> Dict[str, Any]:
    s = SAMPLES[name]
    return debrief_recording(store, s["dir"] / "log.txt", {}, machine_id=s["machine_id"], title=s["title"],
                             actor="Engineer (sample)", report={"stated_reason": s["symptom"]}, source_name=f"{name}.log", synthetic=False,
                             progress=progress)


def seed_showcase(store) -> List[str]:
    """Hosted demo: each sample with a recorded investigation (recorded_investigation.json) becomes a case showing that
    investigation as it was recorded on a local instance (with the live model), labelled as recorded, so a visitor sees
    the real result without the demo running a model. Returns the case ids."""
    ids = []
    for name, s in SAMPLES.items():
        rec = s["dir"] / "recorded_investigation.json"
        if not rec.exists() or any(c["title"] == s["title"] for c in store.cases()):
            continue
        recorded = json.loads(rec.read_text())
        cid = run_sample(store, name)["case_id"]
        run_id = store.latest_run(cid)["id"]
        for e in recorded["entries"]:
            if e["kind"] == "agent_report":
                payload = {**e["payload"], "run_id": run_id, "recorded": {"at": e["recorded_at"], "note": recorded["note"]}}
                store.append_trusted(cid, "agent_report", payload, actor=e["actor"], source="recorded investigation (replayed)")
            elif e["kind"] == "engineer_finding":
                p = {k: v for k, v in e["payload"].items() if k in ("action", "result", "observations")}
                store.append(cid, "engineer_finding", p, actor=f"{e['actor']} (recorded)", source="recorded investigation (replayed)")
        ids.append(cid)
    return ids
