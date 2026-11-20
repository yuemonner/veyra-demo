"""Re-apply the deterministic action gate to an investigation report that was produced BEFORE the gate existed, with no
model call: does the gate catch what that report got wrong? Local only; prints verdicts and refs, never data values.

  python scripts/validation/gate_replay.py <case dir> <stored record json> [--path main_investigator_record.result]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.agent import action_gate  # noqa: E402
from app.physchecks import agent_bridge, casedir  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("case_dir")
    ap.add_argument("record")
    ap.add_argument("--path", default="main_investigator_record.result")
    a = ap.parse_args()
    cd = casedir.load(Path(a.case_dir))
    tools = agent_bridge.PhysicalCheckTools(cd.recording, cd.profile, cd.check_parameters())
    rep = json.loads(Path(a.record).expanduser().read_text())
    for k in a.path.split("."):
        rep = rep[k]
    out = action_gate.enforce(rep, tools, hv_system=bool(tools.assessment["family_scope"].get("applies")))
    g = out["gate"]
    print(json.dumps({"gate": g["version"], "original_action_template": (rep.get("next_action") or {}).get("template_id"),
                      "action_verdict": g["action_verdict"], "rules_fired": [(r["rule"], r["reason"][:160]) for r in g["rules_fired"]],
                      "replacement_template": out["report"]["next_action"].get("template_id") if g["action_verdict"] == "blocked" else None,
                      "material_refs": [m["ref"] for m in action_gate.material_refs(tools)],
                      "covered_by_model": g["covered_by_model"], "inserted_by_gate": g["inserted_findings"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
