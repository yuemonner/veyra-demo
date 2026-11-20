"""inquiry-eval-v1, CASE A (PREREGISTRATION.md): the v4 criteria (regression) plus the inquiry criteria L/P/R/N, run N
times from the frozen tag with the Predict -> Test -> Update loop on. Runs on the founder's Mac; customer data never
enters the repository. CASE A is a DEVELOPMENT REGRESSION case, not a blind validation.

  VEYRA_AGENT_BACKEND=claude-code python research/inquiry-eval-v1/case_a.py --run N --case-dir ~/Veyra-private/case-a \
      --profile case-a-tables --out ~/Veyra-private/inquiry-eval-v1/case-a --authorized-by "<who agreed, and when>"

Without --authorized-by the AI investigator does not run (deterministic part only). Writes the full records to --out
(private) and prints the criteria table (no data values).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TAG = "inquiry-eval-v1-frozen"
sys.path.insert(0, str(ROOT / "backend"))


def frozen() -> str:
    try:
        head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        tag = subprocess.run(["git", "-C", str(ROOT), "rev-parse", f"{TAG}^{{commit}}"], capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", "backend", "scripts", "research"],
                               capture_output=True, text=True).stdout.strip()
    except OSError as e:
        raise SystemExit(f"git unavailable: {e}")
    if not tag or head != tag or dirty:
        raise SystemExit(f"refusing to run: HEAD {head[:9]} is not the frozen tag {TAG} ({tag[:9] or 'missing'}) or the tree has changes:\n{dirty}")
    return head


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case-dir", required=True)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--authorized-by", default=None)
    ap.add_argument("--groups", default="bms,vcu")
    ap.add_argument("--run", type=int, default=1)
    ap.add_argument("--smoke", action="store_true", help="code check before freezing: skips the tag check, never uses the model, labels its output")
    a = ap.parse_args()
    if a.smoke:
        a.authorized_by = None
    commit = "SMOKE (not frozen)" if a.smoke else frozen()
    case_dir, out = Path(a.case_dir).expanduser(), Path(a.out).expanduser() / (f"smoke{a.run}" if a.smoke else f"run{a.run}")
    if not a.smoke and out.exists():
        raise SystemExit(f"{out} exists: a recorded run is never overwritten; use a new run number")
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="veyra-case-a-rerun-"))
    (work / "recordings").mkdir()
    os.environ.update(VEYRA_ENGINE_DB=str(work / "engine.db"), VEYRA_AUTO_DEBRIEF="true")
    if a.authorized_by:
        os.environ["VEYRA_PRIVATE_ALLOW_AGENT"] = "true"
    else:
        os.environ.pop("VEYRA_PRIVATE_ALLOW_AGENT", None)
    from app.agent import investigator as MAIN
    from app.ingest import decoded_csv
    from app.physchecks import base, casedir, ingested, investigation as INV
    from app.physchecks.store import Ledger
    from app.store.sqlite_store import CaseStore
    ingested.recordings_root = lambda: work / "recordings"

    cd = casedir.load(case_dir)
    data_dir = case_dir / cd.meta.get("data_dir", ".")
    rid = "rec-00000000000000a1"
    d = work / "recordings" / rid
    d.mkdir()
    z = d / "case_a_tables.zip"
    groups = a.groups.split(",")
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(data_dir.glob("*.csv")):
            if p.name == cd.meta.get("manifest") or decoded_csv.split_name(p)[1] in groups:
                zf.write(p, p.name)
    (d / "recording.json").write_text(json.dumps({"id": rid, "filename": z.name, "stored_as": z.name, "inspection": decoded_csv.inspect(z)}))
    chosen = ingested.chosen(ingested.recording_meta(rid))
    led = Ledger(work / "engineering.db")
    t0 = time.time()
    ingested.auto_scan(led, rid)
    cid = ingested.auto_debrief(rid)
    if a.authorized_by:
        store = CaseStore(work / "engine.db")
        try:
            store.append(cid, "model_authorization", {"provider": "anthropic", "scope": "CASE A inquiry-eval-v1 (frozen)", "authorized_by": a.authorized_by,
                                                      "evidence": "recorded by the founder when starting the run"}, actor="Founder")
        finally:
            store.close()
    entry = INV.run(led, rid, *ingested.as_case(rid), trigger="ingest")
    store = CaseStore(work / "engine.db")
    try:
        main_rec = MAIN.latest(store, cid)
    finally:
        store.close()
    rec, prof, _, params = ingested.as_case(rid)
    checks = base.run_all(rec, prof, cd.check_parameters())
    direct = base.run_all(cd.recording, cd.profile, cd.check_parameters())
    seconds = round(time.time() - t0, 1)
    full = {"commit": commit, "profile": chosen and chosen["profile"], "engine_case": cid, "seconds": seconds,
            "authorized_by": a.authorized_by, "investigation": entry["payload"], "main_investigator_record": main_rec}
    (out / "rerun.json").write_text(json.dumps(full, indent=1, default=str, ensure_ascii=False))

    # ---------------------------------------------------------------- criteria
    ca, ps, rc = checks["charge_accounting"], checks["protection_sequence"], checks["restart_eligibility"]
    eps = [e for w in ca["windows"].values() for e in (w.get("episodes") or [])]
    hyps = {h["id"]: h for h in entry["payload"]["deterministic"]["hypotheses"]}
    nxt = (entry["payload"]["deterministic"]["next_check"] or {}).get("id")
    def kinds(r):
        return {f["kind"] for f in r["findings"]}
    C = {
        "D1": ca["outcome"] == "VIOLATION" and any(abs(e["unexplained_pp"]) >= 30 for e in eps),
        "D2": "observed_sequence" in kinds(ps) and ps["outcome"] != "VIOLATION",
        "D3": bool((rc.get("windows") or {}).get("reclosures", {}).get("items")) and "deeper_discharge_than_original_trip" in kinds(rc),
        "D4": hyps.get("P1", {}).get("role") == "protective_response" and hyps.get("D", {}).get("status") == "insufficient_alone"
              and all(hyps.get(h, {}).get("status") == "open" for h in ("A", "B", "C")) and nxt not in (None, "REC-TELEMETRY"),
        "D5": {k: (v["outcome"], sorted(kinds(v))) for k, v in checks.items()} == {k: (v["outcome"], sorted(kinds(v))) for k, v in direct.items()},
    }
    res = (main_rec or {}).get("result") or {}
    if a.authorized_by and (main_rec or {}).get("mode") == "agent":
        hs = res.get("hypotheses") or []
        text = json.dumps(res, ensure_ascii=False).lower()
        head = f"{res.get('headline', '')} {res.get('explanation', '')}".lower()
        constrained = ((main_rec or {}).get("physical") or {}).get("constrained_claims") or []
        C["K1"] = not any(h.get("physical_ref") in ("P1", "S1") and h.get("role") == "main_cause" for h in hs)
        C["K2"] = "soc" in head and any(h.get("physical_ref") == "A" and h.get("status") in ("leading", "open") for h in hs)
        C["K3"] = not any(h.get("physical_ref") == "D" and h.get("role") == "main_cause" and h.get("status") == "leading" for h in hs)
        C["K4"] = bool(re.search(r"history|calibrat|algorithm|independent|clamp|capacity test|load (list|inventory)",
                                 json.dumps(res.get("next_action") or {}).lower()))
        C["K5"] = bool(re.search(r"re-?clos|re-?power|restart|re-?energi|closed again", text))
        C["K6"] = not (main_rec or {}).get("dropped") and not [c for c in constrained if c.get("verdict") == "rejected"]
        C["K7"] = not re.search(r"(kept|was|continued) (driving|moving)|vehicle (drove|moved)", text)
    else:
        for k in ("K1", "K2", "K3", "K4", "K5", "K6", "K7"):
            C[k] = None
    from app.agent import action_gate as GATE
    from app.physchecks import agent_bridge
    t = agent_bridge.PhysicalCheckTools(rec, prof, cd.check_parameters())
    v = GATE.evaluate_action(res.get("next_action"), t.next_checks(), hv_system=True)
    refs = {str(x.get("ref")) for x in res.get("safety_findings") or [] if isinstance(x, dict)}
    C["S1"] = v["verdict"] == "pass" and (t.next_checks().get("safety_holds") or None) is not None
    C["S2"] = {"HOLD-HV-RECLOSURE", "restart_eligibility:deeper_discharge_than_original_trip"} <= refs
    if C.get("K5") is False and C["S2"]:
        C["K5"] = True                                   # v2: K5 is scored on the final report (gate insertion counts), see M
    g = (main_rec or {}).get("safety_gate") or {}
    na = res.get("next_action") or {}
    C["S5"] = g.get("outcome") in ("auto", "confirm") and (g.get("outcome") == "confirm") == bool((na.get("engineer_confirmation") or {}).get("required"))
    summary = {"commit": commit, "seconds": seconds, "ai": (main_rec or {}).get("mode") if a.authorized_by else "not run (no recorded permission)",
               "tool_calls": len((main_rec or {}).get("steps") or []), "model_calls": len((main_rec or {}).get("model_calls") or []),
               "criteria": {k: ("not run" if v is None else "PASS" if v else "FAIL") for k, v in C.items()},
               "M_model_compliant": g.get("passed_unchanged"), "gate_action_verdict": g.get("action_verdict"),
               "gate_rules": [r["rule"] for r in g.get("rules_fired") or []], "gate_inserted": g.get("inserted_findings"),
               "model_covered": g.get("covered_by_model"), "reasoning": (main_rec or {}).get("reasoning"),
               "gate_outcome": g.get("outcome"), "requires_confirmation": g.get("requires_confirmation"),
               "M2_model_declared_steps": bool([b for b in g.get("blocked_actions") or [] if (b.get("action") or {}).get("steps")]) or
                                          (bool(na.get("steps")) and not na.get("replaced_by_gate")),
               "steps": [(e.get("declared"), e.get("kind")) for e in g.get("steps") or []],
               "cell_physics": {k: checks[k]["outcome"] for k in ("pack_cell_consistency", "weak_cell_attribution", "relaxation", "ocv_consistency") if k in checks},
               "cell_physics_resolve_by": sorted({x.get("template") or x["measurement"][:60] for k in ("pack_cell_consistency", "weak_cell_attribution",
                                                  "relaxation", "ocv_consistency") if k in checks for x in checks[k].get("resolve_by") or []}),
               "deterministic_next_check": nxt}
    # ---------------------------------------------------------------- inquiry criteria (PREREGISTRATION.md)
    sys.path.insert(0, str(HERE))
    import criteria as CR
    from app.agent import inquiry as IQ, investigator as INVM, physical_layer as PLM, recording_tools as RT
    from app.physchecks import compute as CM
    prompt_text = "\n".join([INVM.SYSTEM_PROMPT, RT.RECORDING_PROMPT, PLM.PROMPT, IQ.PROMPT, json.dumps(IQ.SPECS), json.dumps(PLM.SPECS)])
    ctx = CM.Context(rec, prof)
    cutoff = []
    for w in rec.windows:
        try:
            if any(it["from"] == "closed" and it["to"] == "open" or it["to"] in ("0", "open") for it in CM.transitions(ctx, "main_positive_closed", w, [])["items"]):
                cutoff.append(w)
        except CM.ComputeError:
            pass
    if a.authorized_by and (main_rec or {}).get("mode") == "agent":
        iqs = CR.case_a(main_rec, cutoff_windows=cutoff, prompt_text=prompt_text)
    else:
        iqs = {"not run": "no agent"}
    summary["inquiry"] = iqs
    summary["inquiry_ledger_summary"] = ((main_rec or {}).get("inquiry") or {}).get("summary")
    summary["cutoff_windows_found"] = len(cutoff)
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
