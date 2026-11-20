"""PX4 logs through the engineering path must never get battery-electric-vehicle (HV / BMS / contactor) hypotheses or
checks, and Check A must not call a PX4 'remaining' estimate a violation unless the log shows it counting charge.

  python scripts/validation/px4_applicability.py <folder with .ulg files> [--limit N]

Each log is registered as an uploaded recording in a temporary folder (nothing is copied into the repository), read with
the recording profile Veyra chooses, and run through checks -> hypotheses -> planner. Prints one line per log and a
summary; exit 1 if any log received HV-vehicle hypotheses or recommendations.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.ingest import ulog  # noqa: E402
from app.physchecks import base, facts, hypotheses, ingested, planner  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--limit", type=int, default=50)
    a = ap.parse_args()
    logs = sorted(Path(a.folder).expanduser().glob("*.ulg"))[: a.limit]
    work = Path(tempfile.mkdtemp(prefix="veyra-px4-check-"))
    ingested.recordings_root = lambda: work
    rows, bad = [], 0
    try:
        for i, p in enumerate(logs):
            rid = f"rec-{i:016x}"
            d = work / rid
            d.mkdir()
            (d / p.name).symlink_to(p.resolve())
            log = ulog.read(p, set())
            insp = {"format": "px4_ulog", "topics": [{"topic": t, "type": "px4", "count": n} for (t, _m), n in sorted(log["counts"].items())]}
            (d / "recording.json").write_text(json.dumps({"id": rid, "filename": p.name, "stored_as": p.name, "inspection": insp}))
            c = ingested.chosen(ingested.recording_meta(rid))
            if c is None:
                rows.append({"log": p.name, "profile": None})
                continue
            rec, prof, _ = ingested.load(rid)
            checks = base.run_all(rec, prof)
            A = hypotheses.assess(checks, facts.derive(rec, prof, checks), [], machine=hypotheses.machine_of(prof))
            P = planner.plan(A, checks, requester_qualification="hv_qualified")
            hv = bool(A["hypotheses"] or P["recommended"] or P["handoff"] or P["safety_holds"])
            bad += hv
            ca = checks["charge_accounting"]
            rows.append({"log": p.name, "profile": prof.id, "machine_class": A["family_scope"]["machine_class"],
                         "charge_accounting": ca["outcome"], "counting_shown": ca.get("parameters_used", {}).get("counting_shown_in_recording"),
                         "soc_changes_not_from_charge": sum(f["kind"] == "soc_change_not_from_charge" for f in ca["findings"]),
                         "family_applies": A["family_scope"]["applies"], "hv_vehicle_output": hv})
    finally:
        shutil.rmtree(work, ignore_errors=True)
    for r in rows:
        print(json.dumps(r))
    outcomes = {}
    for r in rows:
        outcomes[r.get("charge_accounting")] = outcomes.get(r.get("charge_accounting"), 0) + 1
    print(json.dumps({"logs": len(rows), "read_by_a_profile": sum(r["profile"] is not None for r in rows),
                      "charge_accounting_outcomes": outcomes, "logs_with_hv_vehicle_output": bad}))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
