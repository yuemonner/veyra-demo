"""Veyra's side of the blind replay. Run in a separate, isolated process (python -I) with one frozen pack directory.

The pack holds only: log.txt (frozen), report.json, hypotheses.json, prior_steps.json (the engineers' earlier steps as
they were known at the cutoff), cutoff.txt. This script never sees the case folder or the sealed truth.
Writes state.json into the pack.
"""
import json
import os
import sys
from datetime import date

BACKEND = sys.argv[2]
sys.path.insert(0, BACKEND)

from app.agent import step_judge as J  # noqa: E402

pack = sys.argv[1]
allowed = {"log.txt", "report.json", "hypotheses.json", "prior_steps.json", "cutoff.txt"}
extra = set(os.listdir(pack)) - allowed - {"state.json"}
if extra:
    raise SystemExit(f"pack holds files Veyra must not see: {sorted(extra)}")
lines = open(os.path.join(pack, "log.txt"), encoding="utf-8").read().split("\n")
orig = json.load(open(os.path.join(pack, "prior_steps.json"), encoding="utf-8"))["orig_lines"]
space = json.load(open(os.path.join(pack, "hypotheses.json"), encoding="utf-8"))
cutoff = date.fromisoformat(open(os.path.join(pack, "cutoff.txt")).read().strip())
state = J.State(lines if any(l.strip() for l in lines) else [], orig, space, cutoff).to_json()
json.dump(state, open(os.path.join(pack, "state.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
