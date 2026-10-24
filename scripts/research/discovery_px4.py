"""Evidence discovery on a different autopilot and format: the four PX4 logs of public issue #26910 (VOXL2 multicopter).

Usage: python scripts/research/discovery_px4.py <folder with the problem .ulg files> <out.json>

Runs the PRODUCT upload path (debrief_recording: import, investigate, then evidence discovery on the whole log) with the
same windows rule as scripts/benchmark/px4_26910.py (normal = first 10 s after takeoff; problem = to landing / kill).
No machine_class is given (no multicopter profile exists): the generic signal chain applies, and the output says so.
Descriptive only: no labels exist, and the sealed issue replies are not read.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.engine import mechanisms  # noqa: E402
from app.ingest import ulog  # noqa: E402
from app.store.sqlite_store import CaseStore  # noqa: E402
from app.workflows.debrief import debrief_recording  # noqa: E402
from app.workflows.discovery import profile_for_run  # noqa: E402

NORMAL_S = 10.0


def main(folder: str, out: str) -> int:
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        store = CaseStore(Path(tmp) / "px4.db")
        for path in sorted(Path(folder).glob("*.ulg")):
            log = ulog.read(path, set())
            takeoff = end = None
            for t, _level, text in log["messages"]:
                if "Takeoff detected" in text and takeoff is None:
                    takeoff = t
                if takeoff is not None and end is None and ("Kill engaged" in text or "Landing detected" in text):
                    end = t
            ex, meta = ulog.extract(path, asset="VOXL2-26910")
            rel = lambda us: ((us + meta["offset_us"]) * 1000 - ex.start_ns) / 1e9  # noqa: E731
            end = end if end is not None else ex.end_ns / 1000 - meta["offset_us"]
            made = debrief_recording(store, path, {}, machine_id="VOXL2-26910", title=f"VOXL2 {path.stem[:8]}", actor="Discovery evaluation",
                                     problem_from_s=rel(takeoff) + NORMAL_S, problem_to_s=rel(end), normal_from_s=rel(takeoff),
                                     normal_to_s=rel(takeoff) + NORMAL_S, normal_basis="Assumed normal: first 10 s after takeoff", source_name=path.name)
            cid = made["case_id"]
            run = store.latest_run(cid)
            entry = next(e for e in reversed(store.ledger(cid)) if e["kind"] == "evidence_discovery")
            disc = entry["payload"]
            chain = profile_for_run(run)["signal_chain"]
            rows = [r for i in store.imports(cid) for r in i["rows"] if r.get("signal") in (chain.get("redundant_signals") or [])]
            assessed = mechanisms.assess(run, chain, disc, rows)
            inv = disc["inventory"]
            results.append({
                "log": path.stem, "upload_discovery_status": made["discovery"], "profile": (run.get("machine_profile") or {}).get("id"),
                "streams": len(inv["streams"]), "roles_present": inv["roles_present"],
                "roles_count": {r: sum(1 for s in inv["streams"] if s["role"] == r) for r in inv["roles_present"]},
                "not_understood": len(inv["not_understood"]), "not_understood_examples": [s["stream"] for s in inv["not_understood"][:12]],
                "excluded": inv["excluded"], "gaps": [g["missing"] for g in disc["gaps"]],
                "incidents": [{"incident": {k: i["incident"].get(k) for k in ("id", "label", "basis")},
                               "controller_facts_near_onset": [f"{f['text']} ({f['t_rel_s']:+g} s)" for f in i.get("facts", []) if f["point"] == "controller" and f.get("near_onset")][:8],
                               "other_facts": [f"{f['kind']}: {f['text']}" + (f" ({f['t_rel_s']:+g} s)" if f.get("t_rel_s") is not None else "")
                                               for f in i.get("facts", []) if f["point"] != "controller"][:8]} for i in disc["incidents"]],
                "events": [{"lead": e["lead"]["text"], "summary": e["summary"],
                            "mechanisms": {m["id"]: m["status"] for m in e["mechanisms"]},
                            "evidence": {m["id"]: [x["text"] for x in m["evidence"]] for m in e["mechanisms"]}} for e in assessed.get("events", [])],
            })
            print(path.stem, made["discovery"])
        store.close()
    Path(out).write_text(json.dumps({"logs": results, "note": "Generic signal chain (no multicopter profile). Descriptive; no labels."}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
