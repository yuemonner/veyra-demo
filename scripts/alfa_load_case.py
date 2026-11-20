"""Load one REAL ALFA flight into the local engine store as a field case, the way it would arrive in operation.

0. The aircraft's normal flights are loaded as KNOWN-GOOD RUNS (fault_type known_good_run): the
   machine baseline the engine compares against. The flight under investigation is never among them.
1. Veyra's output-health check runs blind over the whole flight (no ground truth is read).
2. If it raises an alert, a case opens around the alert time with the first 20 s declared as the
   known-good reference, the evidence is imported and the engine investigates.
The flight's own ground-truth file is never read by this script.

Usage: python scripts/alfa_load_case.py <ALFA processed/ folder> <flight folder name> [engine.db]
Dataset: CMU AirLab ALFA, doi:10.1184/R1/12707963 (CC0 per README; CC BY 4.0 on the repository page).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from alfa_benchmark import MACHINE, PROFILE, REFERENCE_S, convert  # noqa: E402
from app.analysis import outputs  # noqa: E402
from app.ingest.pipeline import ingest  # noqa: E402
from app.ingest.profile import parse_profile  # noqa: E402
from app.store.sqlite_store import CaseStore  # noqa: E402


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def main(processed: str, flight: str, db: str) -> int:
    content = convert(os.path.join(processed, flight))
    profile = parse_profile(PROFILE)
    rows = ingest(content, f"{flight}.jsonl", profile, MACHINE).rows
    start = min(r["t"] for r in rows)
    alert = outputs.detect(rows, (start, start + REFERENCE_S))["first"]
    if not alert:
        print("No output-health alert on this flight; nothing to open.")
        return 1
    store = CaseStore(Path(db))
    store.assert_context({"machine_id": MACHINE, "key": "machine_class", "value": "fixed_wing_uav", "status": "reported", "actor": "ALFA loader",
                          "source": "CMU AirLab ALFA dataset description (Carbon Z T-28 fixed-wing UAV)", "effective_from": "2018-01-01T00:00:00Z"})
    for other in sorted(os.listdir(processed)):
        if "no_failure" not in other or other == flight:
            continue
        good_content = convert(os.path.join(processed, other))
        good_rows = ingest(good_content, f"{other}.jsonl", profile, MACHINE).rows
        good = store.create_case(machine_id=MACHINE, title=f"Known-good flight {other.split('_', 1)[1][:21]}", actor="ALFA loader",
                                 synthetic=False, fault_type="known_good_run")
        store.commit_import(good["id"], good_content, f"alfa_{other.split('_', 1)[1][:21]}.jsonl", profile, actor="ALFA loader",
                            review_reason="Public dataset import; rejected rows listed in the import report")
        store.append(good["id"], "reference_declared", {"window_from": iso(min(r["t"] for r in good_rows)), "window_to": iso(max(r["t"] for r in good_rows)),
                                                         "basis": "Normal flight of the same aircraft (dataset label: no failure injected); whole flight known-good",
                                                         "reference_source": "CMU AirLab ALFA"}, actor="ALFA loader")
    at = alert["t_start"]
    case = store.create_case(machine_id=MACHINE, title=f"{MACHINE}: actuator output alert ({alert['channel']}) — real ALFA flight",
                             actor="Veyra output-health monitor", synthetic=False, symptom_start=iso(at - 30), symptom_end=iso(at + 30),
                             symptom_source=f"Veyra output-health alert ({outputs.OUTPUT_RULES_VERSION}): {alert['text']}. "
                                            f"Real flight {flight.split('_', 1)[1][:21]} from CMU AirLab ALFA (public dataset).")
    cid = case["id"]
    store.commit_import(cid, content, f"alfa_{flight.split('_', 1)[1][:21]}.jsonl", profile, actor="ALFA loader",
                        review_reason="Public dataset import; rejected rows listed in the import report")
    store.append(cid, "reference_declared", {"window_from": iso(start), "window_to": iso(start + REFERENCE_S),
                                             "basis": "First 20 s of the autonomous flight segment, before the alert",
                                             "reference_source": "ALFA benchmark protocol"}, actor="ALFA loader")
    store.append(cid, "model_authorization", {"provider": "anthropic", "scope": "Public CC0 dataset (CMU AirLab ALFA); no private data",
                                              "authorized_by": "Public dataset (CC0)", "evidence": "doi:10.1184/R1/12707963"}, actor="ALFA loader")
    run = store.investigate(cid, actor="ALFA loader")
    print(f"Opened {cid}; run {run['id']}; alert {alert['text']} at {alert['start']}")
    store.close()
    return 0


if __name__ == "__main__":
    default_db = str(Path(__file__).resolve().parents[1] / ".local-runtime" / "engine.db")
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else os.environ.get("VEYRA_ENGINE_DB", default_db)))
