"""Does experience from one closed case help the next one? Two-case experiment on REAL ALFA flights.

Question: when an engineer has closed an earlier case, does Veyra, on a new similar case,
  1. find the similar earlier case,
  2. show which check settled it,
  3. say what is the same and what is different, and
  4. NOT apply the earlier cause to the new case?

Data: CMU AirLab ALFA (Keipour, Mousaei, Scherer, IJRR 2021, doi:10.1184/R1/12707963; CC0 per README), real
flights of one Carbon Z aircraft with faults injected in the autopilot. The ground-truth fault kind of each flight
(failure_status topics) stands in for the engineer's confirmed cause when a case is closed. That is an assumption
of the experiment: on a real fleet the cause comes from inspection. It is stated in every closure ("ALFA label").

Protocol (fixed before running; precedent-rules-v1 are not tuned on these flights):
  A. Every flight becomes a case (first 20 s declared known-good; the normal flights are known-good runs) and is
     investigated blind, exactly as in scripts/alfa_benchmark.py. Each run records its signature.
  B. Leave-one-out retrieval: for each fault flight, the library is every OTHER fault flight, closed with its labelled
     fault kind. Score: is the top precedent the same fault kind? Is one of the shown precedents? Normal flights are
     also queried: any precedent shown there is a false lead.
  C. Replay in time order through the real engine path (ledger, store, check ranking): each fault flight, in the order
     flown, is investigated with the cases closed so far, then closed (case_closure). Recorded: precedents shown,
     same/different, Next Checks, and whether the flight's hypotheses, triage and root cause are identical to its
     blind run in A (they must be: precedents are leads only).
  (First version, run 2026-10-21: C was a single pre-chosen pair, the first two aileron flights by date. The second
   showed NO precedent: its only finding matches engine-failure flights, not the first aileron flight. Kept in the
   write-up; the replay generalises it.)

Usage: python scripts/validation/precedent_experiment_alfa.py <ALFA processed/ folder> <out folder>
"""
from __future__ import annotations

import glob
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alfa_benchmark import MACHINE, PROFILE, REFERENCE_S, convert, ground_truth  # noqa: E402
from app.engine import precedents as P  # noqa: E402
from app.ingest.profile import parse_profile  # noqa: E402
from app.store.sqlite_store import CaseStore  # noqa: E402

DECISIVE = {"aileron": "CHK-ONSITE-ACTUATOR", "elevator": "CHK-ONSITE-ACTUATOR", "rudder": "CHK-ONSITE-ACTUATOR", "engines": "CHK-ONSITE-ACTUATOR"}
DOMAIN_OF = {"aileron": "vehicle_interface", "elevator": "vehicle_interface", "rudder": "vehicle_interface", "engines": "vehicle_body"}


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def closure_payload(kinds, run_id=None):
    k = "+".join(kinds)
    return {**({"run_id": run_id} if run_id else {}), "investigation_status": "cause_confirmed", "cause_domain": DOMAIN_OF[kinds[0]],
            "cause": f"{k} failure (ALFA label)", "confirmed_by": "inspection", "evidence_note": "ALFA dataset failure_status label stands in for an inspection",
            "resolution_status": "resolved", "decisive_check": DECISIVE[kinds[0]]}


def comparable(run):
    return {"hypotheses": [(h["domain"], h["engine_status"], h["column"]) for h in run["hypotheses"]],
            "triage": run["triage"]["proposed"], "root_cause": run["root_cause"]}


def main(processed: str, out_dir: str) -> int:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    db = out / "precedents.db"
    if db.exists():
        db.unlink()
    store = CaseStore(db)
    store.assert_context({"machine_id": MACHINE, "key": "machine_class", "value": "fixed_wing_uav", "status": "reported", "actor": "precedent experiment",
                          "source": "CMU AirLab ALFA dataset description", "effective_from": "2018-01-01T00:00:00Z"})
    profile = parse_profile(PROFILE)
    folders = sorted(glob.glob(os.path.join(processed, "*/")))
    for folder in folders:  # known-good runs: the normal flights
        name = os.path.basename(folder.rstrip("/"))
        if "no_failure" not in name:
            continue
        good = store.create_case(machine_id=MACHINE, title=f"Known-good flight {name}", actor="precedent experiment", synthetic=False, fault_type="known_good_run")
        _, rep = store.commit_import(good["id"], convert(folder), f"{name}.jsonl", profile, actor="precedent experiment", review_reason="experiment")
        store.append(good["id"], "reference_declared", {"window_from": iso(min(r["t"] for r in rep.rows)), "window_to": iso(max(r["t"] for r in rep.rows)),
                                                         "basis": "Normal flight (dataset label)", "reference_source": "ALFA"}, actor="precedent experiment")
    flights, created = [], []
    for folder in folders:  # A. every flight a case; all reference windows declared BEFORE any investigation
        name = os.path.basename(folder.rstrip("/"))  # (each declared window is also machine baseline for the other cases)
        case = store.create_case(machine_id=MACHINE, title=f"ALFA flight {name}", actor="precedent experiment", synthetic=False)
        _, rep = store.commit_import(case["id"], convert(folder), f"{name}.jsonl", profile, actor="precedent experiment", review_reason="experiment")
        start = min(r["t"] for r in rep.rows)
        store.append(case["id"], "reference_declared", {"window_from": iso(start), "window_to": iso(start + REFERENCE_S),
                                                         "basis": "First 20 s", "reference_source": "ALFA benchmark protocol"}, actor="precedent experiment")
        created.append((folder, name, case))
    for folder, name, case in created:  # then investigated blind
        run = store.investigate(case["id"], actor="precedent experiment")
        _, kinds = ground_truth(folder)  # read after the blind run
        flights.append({"name": name, "case_id": case["id"], "kinds": kinds, "signature": run["signature"], "run_id": run["id"]})
        print(f"investigated {name} {kinds or 'normal'} signature={len(run['signature'])}", flush=True)

    # B. leave-one-out retrieval (pure matcher, same function the engine uses)
    faults = [f for f in flights if f["kinds"]]
    rows, false_leads = [], 0
    for q in flights:
        library = [{"case_id": f["case_id"], "title": f["name"], "machine_id": MACHINE, "profile_id": "fixed_wing_uav", "synthetic": False,
                    "signature": f["signature"],
                    "closure": {**closure_payload(f["kinds"]), "evidence_strength": "direct", "closed_at": f["name"][8:27]}} for f in faults if f["case_id"] != q["case_id"]]
        m = P.match(q["signature"], library, machine_id=MACHINE, profile_id="fixed_wing_uav", synthetic=False)["matches"]
        kind_of = {f["case_id"]: f["kinds"] for f in faults}
        shown = [{"flight": x["title"], "kinds": kind_of[x["case_id"]], "similarity": x["similarity"], "different": len(x["only_now"]) + len(x["only_then"])} for x in m]
        if not q["kinds"]:
            false_leads += bool(shown)
        rows.append({"flight": q["name"], "kinds": q["kinds"], "signature_size": len(q["signature"]), "shown": shown,
                     "top1_same_kind": bool(shown) and bool(set(shown[0]["kinds"]) & set(q["kinds"])) if q["kinds"] else None,
                     "any_same_kind": any(set(s["kinds"]) & set(q["kinds"]) for s in shown) if q["kinds"] else None})
    def rate(sel):
        qs = [r for r in rows if r["kinds"] and sel(r)]
        return {"queries": len(qs), "top1_same_kind": sum(1 for r in qs if r["top1_same_kind"]),
                "any_shown_same_kind": sum(1 for r in qs if r["any_same_kind"]), "nothing_shown": sum(1 for r in qs if not r["shown"]),
                "top1_different_kind": sum(1 for r in qs if r["shown"] and not r["top1_same_kind"])}
    summary = {"fault_flights": rate(lambda r: True), "by_kind": {k: rate(lambda r, k=k: k in r["kinds"]) for k in ("aileron", "elevator", "rudder", "engines")},
               "normal_flights_queried": sum(1 for r in rows if not r["kinds"]), "normal_flights_with_a_precedent_shown": false_leads,
               "chance_top1_same_kind": round(sum((sum(1 for g in faults if set(g["kinds"]) & set(f["kinds"]) and g is not f)) / (len(faults) - 1) for f in faults) / len(faults), 3)}

    # C. Replay in time order through the real engine path (store, ledger, check ranking): flights are taken in the
    # order they were flown; each is investigated with the cases closed so far, then closed by "the engineer" with its
    # labelled fault kind. This is how experience accumulates on a real fleet. Checked on every flight: the hypotheses,
    # triage and root cause are identical to the blind run in A (earlier causes are never applied).
    blind = {f["case_id"]: comparable(store.latest_run(f["case_id"])) for f in flights}
    replay = []
    for f in faults:  # names sort by date
        run = store.investigate(f["case_id"], actor="precedent experiment")
        ms = run["precedents"]["matches"]
        top = ms[0] if ms else None
        first_check = run["checks"][0] if run["checks"] else None
        replay.append({"flight": f["name"], "kinds": f["kinds"], "signature": f["signature"], "closed_before": sum(1 for x in replay),
                       "shown": [{"title": m["title"], "similarity": m["similarity"], "earlier_cause": m["closure"]["cause"],
                                  "status": m["closure"]["investigation_status"], "evidence_strength": m["closure"]["evidence_strength"],
                                  "decisive_check": m["closure"]["decisive_check"], "same": m["same"], "only_now": m["only_now"], "only_then": m["only_then"]} for m in ms],
                       "top_same_kind": bool(top) and any(k in top["closure"]["cause"].split(" ")[0].split("+") for k in f["kinds"]),
                       "next_checks": [(c["check_id"], c["trigger"]) for c in run["checks"][:3]],
                       "precedent_check_first_actionable": next((c["check_id"] for c in run["checks"] if c.get("precedent")), None),
                       "own_evidence_unchanged": comparable(run) == blind[f["case_id"]], "root_cause": run["root_cause"],
                       "not_shown": run["precedents"].get("not_shown", 0)})
        if not replay[-1]["own_evidence_unchanged"]:
            replay[-1]["difference"] = {"blind": blind[f["case_id"]], "now": comparable(run)}
        store.append(f["case_id"], "case_closure", closure_payload(f["kinds"], run["id"]), actor="Engineer (experiment)")
        print(f"replay {f['name']} shown={len(ms)} top_same_kind={replay[-1]['top_same_kind']} unchanged={replay[-1]['own_evidence_unchanged']}", flush=True)
    rs = [r for r in replay if r["closed_before"] > 0]
    walk = {"flights_replayed": len(replay), "with_earlier_closed_cases": len(rs),
            "a_precedent_shown": sum(1 for r in rs if r["shown"]), "top_precedent_same_kind": sum(1 for r in rs if r["top_same_kind"]),
            "top_precedent_different_kind": sum(1 for r in rs if r["shown"] and not r["top_same_kind"]),
            "nothing_shown": sum(1 for r in rs if not r["shown"]),
            "own_evidence_unchanged_on_every_flight": all(r["own_evidence_unchanged"] for r in replay),
            "root_cause_still_unconfirmed_on_every_flight": all(r["root_cause"] == "unconfirmed" for r in replay)}
    for f in flights:
        f.pop("run_id", None)
    store.close()
    report = {"dataset": "CMU AirLab ALFA, real flights (CC0 per README)", "rules": P.PRECEDENT_RULES_VERSION,
              "assumption": "ALFA labels stand in for the engineer's confirmed cause when a case is closed",
              "retrieval": summary, "replay_in_time_order": walk, "replay": replay, "flights": rows,
              "signatures": {f["name"]: f["signature"] for f in flights}}
    (out / "precedent_results.json").write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps({"retrieval": summary, "replay_in_time_order": walk}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
