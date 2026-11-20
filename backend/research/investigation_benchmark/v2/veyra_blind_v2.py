"""Veyra's side of the blind replay, protocol v2. Run isolated (python -I) on one frozen pack directory.

The pack holds only: log.txt, report.json, hypotheses.json, prior_steps.json, cutoff.txt, artifacts.json (frozen).
Writes state.json (telemetry + artifacts) and state_telemetry.json (telemetry only, for the comparison).
"""
import json
import os
import sys
from datetime import date

BACKEND = sys.argv[2]
sys.path.insert(0, BACKEND)

from app.agent import evidence as EV  # noqa: E402
from app.agent import step_judge as J  # noqa: E402

pack = sys.argv[1]
allowed = {"log.txt", "report.json", "hypotheses.json", "prior_steps.json", "cutoff.txt", "artifacts.json"}
extra = set(os.listdir(pack)) - allowed - {"state.json", "state_telemetry.json"}
if extra:
    raise SystemExit(f"pack holds files Veyra must not see: {sorted(extra)}")
rd = lambda n: json.load(open(os.path.join(pack, n), encoding="utf-8"))  # noqa: E731
lines = open(os.path.join(pack, "log.txt"), encoding="utf-8").read().split("\n")
orig = rd("prior_steps.json")["orig_lines"]
space = rd("hypotheses.json")
report = rd("report.json")
arts = rd("artifacts.json")["artifacts"]
cutoff = date.fromisoformat(open(os.path.join(pack, "cutoff.txt")).read().strip())
tele = J.State(lines if any(l.strip() for l in lines) else [], orig, space, cutoff).to_json()
onset = date.fromisoformat(report["report_date"])
full = EV.merge(tele, EV.assess(arts, space, cutoff, onset))
json.dump(tele, open(os.path.join(pack, "state_telemetry.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
json.dump(full, open(os.path.join(pack, "state.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
