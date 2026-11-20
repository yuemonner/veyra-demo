"""Run the deterministic engineering pipeline + the expert layer on one case directory, and write the result as JSON.

    python scripts/expert_case.py --case-dir DIR [--profile FILE] [--rules FILE] [--reported "..."] --out OUT.json

No model is called. For a confidential case the output stays where --out points (keep it in the customer's private
directory); nothing here is written to the repository.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.physchecks import base, casedir, expert_layer, facts as facts_mod, hypotheses, plausibility, planner  # noqa: E402
from app.physchecks.profile import Profile  # noqa: E402


def run(case_dir: Path, profile_path: Path | None, rules_path: Path | None, reported: str | None) -> dict:
    cd = casedir.load(case_dir, verify=False)
    prof = Profile.load(profile_path) if profile_path else cd.profile
    rec = _reload(case_dir, cd, prof) if profile_path else cd.recording
    params = cd.check_parameters()
    checks = base.run_all(rec, prof, params)
    F = facts_mod.derive(rec, prof, checks)
    A = hypotheses.assess(checks, F, [], machine=hypotheses.machine_of(prof))
    rules = plausibility.load_rules(rules_path) if rules_path else {}
    ex = expert_layer.enrich(rec, prof, checks, F, A, rules=rules, reported_symptom=reported)
    plan = planner.plan(A, checks, requester_qualification="none")
    return {"case_id": cd.meta.get("case_id"), "synthetic": rec.synthetic, "profile": prof.id,
            "checks": {k: {"outcome": v["outcome"], "summary": v["summary"]} for k, v in checks.items()},
            "assessment": A, "audit": ex["audit"], "plausibility": ex["plausibility"],
            "plan_first": [r["id"] for r in plan.get("recommended") or []][:5], "safety_holds": [h["id"] for h in plan.get("safety_holds") or []]}


def _reload(case_dir: Path, cd, prof: Profile):
    from app.physchecks.recording import load_decoded_csv
    meta = cd.meta
    data = case_dir / meta.get("data_dir", ".")
    files = {w: [data / f for f in fs] for w, fs in meta["windows"].items()}
    fields = sorted({spec["field"] for spec in prof.roles.values()})
    return load_decoded_csv(meta["case_id"], files, fields=fields, synthetic=bool(meta.get("synthetic")), label=meta.get("title", ""))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case-dir", required=True, type=Path)
    ap.add_argument("--profile", type=Path)
    ap.add_argument("--rules", type=Path)
    ap.add_argument("--reported")
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args()
    res = run(a.case_dir, a.profile, a.rules, a.reported)
    a.out.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    lead = (res["assessment"].get("leading") or {}).get("statement")
    print("leading:", lead or "none")
    print("grades:", {k: v.get("grade") for k, v in (res["plausibility"].get("grades") or {}).items()})
    print("plan first:", res["plan_first"][:3], "holds:", res["safety_holds"])


if __name__ == "__main__":
    main()
