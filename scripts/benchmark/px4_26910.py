"""Veyra Public Incident Benchmark, case 1: PX4-Autopilot issue #26910 (VOXL2, height-estimate instability).

Real, public flight logs (logs uploaded by the reporter to review.px4.io and linked in the public issue). Local only:
the logs and all outputs stay under .local-private/ (git-ignored) and are not redistributed.

Two strictly separate packages under .local-private/benchmark/px4-26910/:
  evidence/problem/*.ulg   the four flights the reporter linked as affected      -> given to Veyra
  evidence/after/*.ulg     four flights after the reporter changed parameters    -> NOT used in the blind run
  sealed/                  the issue text and replies (the reporter's and maintainers' explanations) -> never read here
What Veyra is told (EVIDENCE_REPORT below) is only the symptom as first reported, without the reporter's
"disabling the barometer appeared to resolve it" and without any reply.

Windows are fixed by a rule written before the run (no tuning to the case):
  takeoff = the log's "Takeoff detected" message; end = "Kill engaged" or "Landing detected", whichever is first;
  normal (assumed, labelled as such) = the first 10 s after takeoff; problem = from takeoff + 10 s to the end.
Machine type: fixed_wing_uav, the only aircraft profile Veyra has (this is a multicopter: a known mismatch).

Usage: python scripts/benchmark/px4_26910.py <benchmark folder> <output json>
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.engine import next_action as na  # noqa: E402
from app.ingest import bags, ulog  # noqa: E402
from app.store.sqlite_store import CaseStore  # noqa: E402
from app.workflows import autopilot  # noqa: E402
from app.workflows.debrief import debrief_recording  # noqa: E402

EVIDENCE_REPORT = ("Height estimate unstable during a mission: the vehicle abruptly lost height (sudden drops or crashes) "
                   "or climbed rapidly to unsafe altitude without pilot input. VOXL2, PX4 1.17 beta, Mission mode.")
NORMAL_S = 10.0


def timeline(path: Path):
    log = ulog.read(path, set())
    takeoff = end = None
    for t, _level, text in log["messages"]:
        if "Takeoff detected" in text and takeoff is None:
            takeoff = t
        if takeoff is not None and end is None and ("Kill engaged" in text or "Landing detected" in text):
            end = t
    return takeoff, end


def rel(ex, meta, boot_us) -> float:
    """Seconds from the start of the extracted readings to a time on the log's boot clock."""
    return ((boot_us + meta["offset_us"]) * 1000 - ex.start_ns) / 1e9


def main(folder: Path, out: Path, subset: str = "problem"):
    """subset "problem": the blind run on the reported flights. subset "after": the same rule on the flights flown after
    the reporter's change, with no problem report, as a false-alarm check (never used as a baseline for the blind run)."""
    db = folder / "runs" / f"{subset}.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        db.unlink()
    store = CaseStore(db)
    results = []
    for path in sorted((folder / "evidence" / subset).glob("*.ulg")):
        ex, meta = ulog.extract(path, asset="VOXL2-26910")
        if meta["clock"] == "px4_boot":  # no wall-clock time anywhere in the log: Veyra's import refuses 1970 times, and we don't invent a date
            results.append({"log": path.stem, "sha256": bags.sha256_file(path), "skipped": "No GPS UTC and no logger file name: no wall-clock time in the log"})
            continue
        takeoff, end = timeline(path)
        if end is None:  # flights that ended normally: "Landing detected" is in the list above; else the last reading
            end = ex.end_ns / 1000 - meta["offset_us"]
        start_rel, end_rel = rel(ex, meta, takeoff), rel(ex, meta, end)
        made = debrief_recording(store, path, {}, machine_id="VOXL2-26910", title=f"VOXL2 flight {path.stem[:8]} (PX4 issue #26910, public log)",
                                 actor="Benchmark runner", machine_class="fixed_wing_uav",
                                 problem_from_s=start_rel + NORMAL_S, problem_to_s=end_rel,
                                 normal_from_s=start_rel, normal_to_s=start_rel + NORMAL_S,
                                 normal_basis="Assumed normal: the first 10 s after takeoff (rule fixed before the run)",
                                 report=({"stated_reason": EVIDENCE_REPORT, "symptom": "erratic_motion", "affected_function": "altitude",
                                          "at_s": start_rel + NORMAL_S} if subset == "problem" else None), source_name=path.name, synthetic=False)
        cid = made["case_id"]
        run = store.latest_run(cid)
        view = autopilot.advance(store, cid)
        a = na.assess(run)
        results.append({
            "log": path.stem, "sha256": bags.sha256_file(path), "case_id": cid, "clock": meta["clock"], "dropouts": meta["dropouts"],
            "windows_s_from_log_start": {"normal": [round(start_rel, 1), round(start_rel + NORMAL_S, 1)], "problem": [round(start_rel + NORMAL_S, 1), round(end_rel, 1)]},
            "rows": made["counts"],
            "domains": {d: {"status": v["status"], "observations": [o["text"] for o in v["observations"]][:12]} for d, v in run["domains"].items()},
            "hypotheses": [{"domain": h["domain"], "column": h.get("column"), "statement": h.get("statement") or h.get("title")} for h in run.get("hypotheses") or []],
            "checks_top": [{"check_id": c["check_id"], "rank": c.get("rank"), "question": c["question"], "availability": c["availability"]["state"]}
                           for c in sorted(run.get("checks") or [], key=lambda c: c.get("rank", 99))[:6]],
            "assessment": {"supported": [s["label"] for s in a["supported"]], "cannot_tell_apart": [c["label"] for c in a["cannot_tell_apart"]],
                           "effects": [x["label"] for x in a.get("effects", [])], "less_likely": a["less_likely"]},
            "autopilot": {"stage": view.get("stage"), "say": view.get("say"), "checks": [c["check_id"] for c in view.get("checks") or []],
                          "request": (view.get("request") or {}).get("body")},
            "triage": run.get("triage", {}).get("proposed"),
            "root_cause": run.get("root_cause"),
            "departures_s_from_log_start": [{"axis": f["axis"], "at": round((f["t_start"] * 1e9 - ex.start_ns) / 1e9, 1), "text": f["text"]}
                                            for f in ((run.get("findings") or {}).get("tracking") or {}).get("findings", [])],
            "evaluator_only_params": {k: meta["params"].get(k) for k in ("MPC_Z_P", "MPC_Z_VEL_P_ACC", "MPC_Z_VEL_MAX_DN", "MPC_Z_VEL_MAX_UP",
                                                                          "EKF2_BARO_NOISE", "EKF2_RNG_DELAY", "EKF2_BARO_CTRL", "EKF2_HGT_REF")},
        })
    store.close()
    out.write_text(json.dumps({"benchmark": "px4-26910", "engine_versions": None, "evidence_report": EVIDENCE_REPORT, "results": results}, indent=1, default=str))
    print(f"wrote {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else "problem")
