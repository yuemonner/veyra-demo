"""Reproducible technical demo: a REAL fault, investigated by Veyra's AI through its recording evidence tools, with a pack
anyone can re-verify from the input file (scripts/demo/verify_demo.py).

Input: CMU AirLab ALFA, flight carbonZ_2018-10-05-14-37-22_3_left_aileron_failure (Carbon Z T-28 fixed-wing UAV),
Keipour, Mousaei, Scherer, IJRR 2021, doi:10.1184/R1/12707963, CC0. The researchers injected a left-aileron failure in
flight; the bag's /failure_status/aileron topic records when. That label is NEVER mapped and never shown to the model
(the product's label guard); this script reads it only AFTER the investigation, to compare the result with the truth.

The model gets the flight, the mapping Veyra suggests, and a neutral request. No hint of the fault, its time or its axis.
Nothing here is tuned to this flight: the evidence sheet and tools are the product's, unchanged.

Usage (needs the AI model: VEYRA_AGENT_BACKEND=claude-code, or ANTHROPIC_API_KEY):
  PYTHONPATH=backend python scripts/demo/alfa_aileron_demo.py <path to the .bag> <out dir>
Writes <out dir>/alfa_aileron_demo.json (the pack) and prints a summary.
"""
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

BAG_NAME = "carbonZ_2018-10-05-14-37-22_3_left_aileron_failure.bag"
BAG_SHA256 = "ac5cf020f20bc2a044f0ce60369c80d25ce688bc460184f01b0ae5f1bd748c27"
MACHINE = "ALFA-CarbonZ"
TITLE = "Carbon Z fixed-wing UAV: check this flight for a control fault (real ALFA flight)"
REPORT = "Check this flight: did a control fault occur, when, and where on the aircraft?"
SOURCE = {"dataset": "ALFA: A Dataset for UAV Fault and Anomaly Detection (CMU AirLab)", "citation": "Keipour, Mousaei, Scherer, IJRR 2021",
          "doi": "10.1184/R1/12707963", "licence": "CC0 (dataset README)", "flight": BAG_NAME.removesuffix(".bag"),
          "fault": "injected by the researchers in flight; recorded in the bag's /failure_status/aileron label topic"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def prepare(bag: Path, store):
    """Upload path, as the product does it: inspect, Veyra's suggested mapping, debrief. Returns (case_id, mapping)."""
    from app.ingest import bags
    from app.workflows.debrief import debrief_recording
    mapping = bags.suggest(bags.inspect(bag))
    out = debrief_recording(store, bag, mapping, machine_id=MACHINE, title=TITLE, actor="Demo", source_name=bag.name,
                            report={"stated_reason": REPORT})
    return out["case_id"], mapping


def ground_truth(bag: Path, t0_unix: float) -> dict:
    """Read AFTER the investigation: when the label topic first reports a failure, in the sheet's seconds."""
    from rosbags.highlevel import AnyReader
    with AnyReader([bag]) as r:
        cons = [c for c in r.connections if c.topic == "/failure_status/aileron"]
        for c, t, raw in r.messages(connections=cons):
            v = r.deserialize(raw, c.msgtype).data
            if v:
                return {"topic": "/failure_status/aileron", "first_nonzero_value": int(v), "onset_s": round(t / 1e9 - t0_unix, 3),
                        "meaning": "ALFA labels: the aileron failure is active from this time (value 2 in this flight: left aileron)",
                        "shown_to_model": False}
    return {"topic": "/failure_status/aileron", "onset_s": None}


def compare(result: dict, truth: dict, sheet: list) -> dict:
    """Plain facts, no score: did the result point at roll / the aileron, and at a time at or after the onset?"""
    import re
    text = json.dumps({k: result.get(k) for k in ("headline", "explanation", "hypotheses", "evidence", "checked", "events", "next_action")}).lower()
    leading = [h for h in result.get("hypotheses") or [] if h.get("status") == "leading"]
    times = []
    for e in result.get("evidence") or []:
        line = sheet[e["line"] - 1] if 1 <= e["line"] <= len(sheet) else ""
        times += [float(x) for x in re.findall(r"t=([\d.]+)", line)]
    onset = truth.get("onset_s")
    return {"mentions_roll": "roll" in text, "mentions_aileron": "aileron" in text,
            "leading_hypotheses": [{"area": h.get("area"), "claim": h.get("claim")} for h in leading],
            "evidence_times_s": sorted(set(round(t, 3) for t in times)),
            "evidence_at_or_after_onset": bool(onset is not None and any(t >= onset - 1 for t in times)),
            "status": result.get("status"), "note": "Facts for a reader to judge. Veyra never claims a confirmed root cause."}


def main(bag: Path, out_dir: Path) -> int:
    from app.agent import investigator as inv
    from app.agent.llm import from_environment
    from app.agent.recording_tools import RecordingTools
    from app.store.sqlite_store import CaseStore
    digest = sha256(bag)
    if digest != BAG_SHA256:
        print(f"Input mismatch: sha256 {digest}, expected {BAG_SHA256}")
        return 2
    model = from_environment()
    if model is None or not model.available():
        print("No AI model configured (VEYRA_AGENT_BACKEND=claude-code or ANTHROPIC_API_KEY)")
        return 2
    store = CaseStore(Path(tempfile.mkdtemp(prefix="veyra-demo-")) / "demo.db")
    cid, mapping = prepare(bag, store)
    store.append(cid, "model_authorization", {"provider": model.provider, "scope": "public CC0 research data (ALFA)", "authorized_by": "Demo",
                                              "evidence": "public dataset"}, actor="Demo")
    tools = RecordingTools(store, cid)
    t0 = time.time()
    r = inv.investigate(store, cid, model=model, keep_outputs=True)
    truth = ground_truth(bag, tools.rec.t0)
    pack = {"version": "veyra-demo-pack-v1", "kind": "reproducible technical demo", "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "input": {**SOURCE, "file": bag.name, "sha256": digest, "bytes": bag.stat().st_size},
            "request": {"machine_id": MACHINE, "title": TITLE, "report": REPORT},
            "mapping": mapping,
            "sheet": {"version": tools.sheet["version"], "sha256": tools.sheet["sha256"], "lines": tools.raw},
            "investigation": {k: r.get(k) for k in ("mode", "model", "fallback_reason", "steps", "result", "dropped", "usage", "seconds", "tool_outputs", "version")},
            "ground_truth": truth, "comparison": compare(r.get("result") or {}, truth, tools.raw),
            "how_to_verify": "PYTHONPATH=backend python scripts/demo/verify_demo.py <this pack> <the .bag>: recomputes the sheet from the bag, "
                             "re-runs every data tool call with its recorded input, and re-checks every quote and number in the result."}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "alfa_aileron_demo.json").write_text(json.dumps(pack, indent=1, default=str))
    res = r.get("result") or {}
    print(json.dumps({"mode": r["mode"], "model": r.get("model"), "fallback": r.get("fallback_reason"), "seconds": round(time.time() - t0, 1),
                      "steps": [s["label"] for s in r.get("steps") or []], "headline": res.get("headline"), "status": res.get("status"),
                      "truth": truth, "comparison": pack["comparison"]}, indent=1))
    store.close()
    return 0 if r["mode"] == "agent" else 1


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
