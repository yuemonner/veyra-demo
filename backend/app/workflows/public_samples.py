"""REAL public failure logs (fixtures/real/public) that anyone can open in the demo with one click.

Each is a log a reporter posted in a public issue thread (link in source.json), run through the ordinary upload path
(debrief_recording) as a real, non-synthetic case. They are not blind test cases: they were part of the first sealed
evaluation batch.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

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


def recordings(sample_dir: Path) -> List[Dict[str, Any]]:
    """Every walkthrough recording of a sample, oldest first: v1 is the original recorded_investigation.json."""
    out = []
    first = sample_dir / "recorded_investigation.json"
    if first.exists():
        out.append({"version": "v1", "path": first})
    for p in sorted(sample_dir.glob("recorded_investigation.v*.json"), key=lambda p: int(re.sub(r"\D", "", p.stem.split(".")[-1]) or 0)):
        out.append({"version": p.stem.split(".")[-1], "path": p})
    return out


def recording(sample_dir: Path, version: Optional[str] = None) -> Optional[Path]:
    if version:
        return next((r["path"] for r in recordings(sample_dir) if r["version"] == version), None)
    """The walkthrough a sample shows: the newest versioned recording (recorded_investigation.v2.json, v3...), else the
    original recorded_investigation.json. Older recordings stay in the folder, unchanged, for comparison."""
    versions = sorted(sample_dir.glob("recorded_investigation.v*.json"), key=lambda p: int(re.sub(r"\D", "", p.stem.split(".")[-1]) or 0))
    if versions:
        return versions[-1]
    first = sample_dir / "recorded_investigation.json"
    return first if first.exists() else None


def listing() -> List[Dict[str, Any]]:
    return [{"name": k, "walkthrough": recording(v["dir"]) is not None, **{f: v[f] for f in ("title", "platform", "symptom", "source_url", "project")}}
            for k, v in SAMPLES.items()]


def run_sample(store, name: str, progress=lambda step: None) -> Dict[str, Any]:
    s = SAMPLES[name]
    return debrief_recording(store, s["dir"] / "log.txt", {}, machine_id=s["machine_id"], title=s["title"],
                             actor="Engineer (sample)", report={"stated_reason": s["symptom"]}, source_name=f"{name}.log", synthetic=False,
                             progress=progress)


def seed_showcase(store, version: Optional[str] = None) -> List[str]:
    """Hosted demo: each sample with a recorded investigation (recorded_investigation.json) becomes a case showing that
    investigation as it was recorded on a local instance (with the live model), labelled as recorded, so a visitor sees
    the real result without the demo running a model. Returns the case ids."""
    ids = []
    for name, s in SAMPLES.items():
        rec = recording(s["dir"], version)
        if rec is None or any(c["title"] == s["title"] for c in store.cases()):
            continue
        recorded = json.loads(rec.read_text())
        cid = run_sample(store, name)["case_id"]
        run_id = store.latest_run(cid)["id"]
        last = max(i for i, e in enumerate(recorded["entries"]) if e["kind"] == "agent_report")
        for i, e in enumerate(recorded["entries"]):
            if e["kind"] == "agent_report":
                payload = {**e["payload"], "run_id": run_id, "recorded": {"at": e["recorded_at"], "note": recorded["note"], "file": rec.name,
                                                                          **({"close": recorded["close"]} if i == last and recorded.get("close") else {})}}
                store.append_trusted(cid, "agent_report", payload, actor=e["actor"], source="recorded investigation (replayed)")
            elif e["kind"] == "engineer_finding":
                p = {k: v for k, v in e["payload"].items() if k in ("action", "outcome_selected", "result", "observations")}
                store.append(cid, "engineer_finding", p, actor=e["actor"], source="recorded investigation (replayed)")
        ids.append(cid)
    return ids


def recording_versions(name: str) -> List[Dict[str, Any]]:
    """For comparison: each recording's version, when and with which rules it was made (the files are never edited)."""
    out = []
    for r in recordings(SAMPLES[name]["dir"]):
        d = json.loads(r["path"].read_text())
        out.append({"version": r["version"], "file": r["path"].name, "note": d.get("note"), "integrity_rules": d.get("integrity_rules"),
                    "investigator": d.get("investigator"), "shown": r["path"] == recording(SAMPLES[name]["dir"])})
    return out
