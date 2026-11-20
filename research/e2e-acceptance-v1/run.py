"""End-to-end acceptance v1: runs PROTOCOL.md exactly and writes results/ and RESULTS.md.

  VEYRA_AGENT_BACKEND=claude-code python research/e2e-acceptance-v1/run.py --ulog <UAV-SEAD 2021-10-06--_11_54_32.ulg> --work <dir outside the repo>

Everything is SYNTHETIC except R4, a public PX4 log. Engineer steps are scripted and labelled as such.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ulog", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--run", type=int, required=True, help="run number; an earlier run's outputs are never overwritten")
    ap.add_argument("--smoke", action="store_true", help="code check without a model; writes only under --work, never results/")
    a = ap.parse_args()
    if not a.smoke and (HERE / f"results-run{a.run}").exists():
        raise SystemExit(f"run {a.run} already exists; use a new run number")
    work = Path(a.work).expanduser().resolve()
    if work.exists():
        shutil.rmtree(work)
    (work / "recordings").mkdir(parents=True)
    (work / "private").mkdir()
    os.environ.update(VEYRA_ENGINE_DB=str(work / "engine.db"), VEYRA_AUTO_DEBRIEF="true", VEYRA_PRIVATE_CASES_DIR=str(work / "private"))
    os.environ.pop("VEYRA_PRIVATE_ALLOW_AGENT", None)

    from app.agent import investigator as MAIN
    from app.ingest import decoded_csv, ulog
    from app.physchecks import base, hypotheses, ingested, investigation as INV, knowledge, outcomes as O, scenarios as SC
    from app.physchecks.store import Ledger
    from app.store.sqlite_store import CaseStore
    ingested.recordings_root = lambda: work / "recordings"
    spec = importlib.util.spec_from_file_location("gen", ROOT / "scripts" / "make_synthetic_bev_tables.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    led = Ledger(work / "engineering.db")
    out_dir = (work / "results") if a.smoke else (HERE / f"results-run{a.run}")
    out_dir.mkdir(exist_ok=True)
    log = []

    def upload(name: str, make) -> str:
        rid = f"rec-{len(log):016x}"
        d = work / "recordings" / rid
        d.mkdir()
        p = make(d / name)
        if p.suffix == ".ulg":
            lg = ulog.read(p, set())
            insp = {"format": "px4_ulog", "topics": [{"topic": t, "type": "px4", "count": n} for (t, _m), n in sorted(lg["counts"].items())]}
        else:
            insp = decoded_csv.inspect(p)
        (d / "recording.json").write_text(json.dumps({"id": rid, "filename": p.name, "stored_as": p.name, "inspection": insp}))
        return rid

    def arrive(label: str, rid: str, *, agent_ok_public: bool = False) -> dict:
        t0 = time.time()
        scan = ingested.auto_scan(led, rid)
        cid = ingested.auto_debrief(rid)
        if agent_ok_public:
            os.environ["VEYRA_PRIVATE_ALLOW_AGENT"] = "true"    # public dataset (not confidential): the agent may read it
            st = CaseStore(work / "engine.db")
            try:                                                 # the engine case's own permission record (run 1 lacked it)
                st.append(cid, "model_authorization", {"provider": "anthropic", "scope": "public dataset (UAV-SEAD), acceptance run",
                                                       "authorized_by": "public data; no data owner restriction",
                                                       "evidence": "dataset published openly by its authors"}, actor="Founder")
            finally:
                st.close()
        try:
            entry = INV.run(led, rid, *ingested.as_case(rid), trigger="ingest")
        finally:
            os.environ.pop("VEYRA_PRIVATE_ALLOW_AGENT", None)
        store = CaseStore(work / "engine.db")
        try:
            main_rec = MAIN.latest(store, cid) if cid else None
        finally:
            store.close()
        chosen = ingested.chosen(ingested.recording_meta(rid))
        rec = {"label": label, "recording": rid, "profile": chosen and chosen["profile"], "engine_case": cid, "seconds": round(time.time() - t0, 1),
               "scan": scan.get("payload") if isinstance(scan, dict) and "payload" in scan else scan,
               "investigation": entry["payload"], "main_investigator_record": main_rec}
        (out_dir / f"{label}.json").write_text(json.dumps(rec, indent=1, default=str))
        log.append(rec)
        print(label, rec["profile"], entry["payload"]["agent"].get("mode"), rec["seconds"], "s", flush=True)
        return rec

    # R1
    r1 = upload("SYNTHETIC-R1-correction.zip", lambda p: gen.write("correction", p, 1))
    R1 = arrive("R1", r1)
    rec1, prof1, _, _ = ingested.as_case(r1)
    checks1 = base.run_all(rec1, prof1)
    s = SC.create_from_finding(led, case_id=r1, check_result=checks1["charge_accounting"], finding_kind="unexplained_soc_correction",
                               title="SOC correction not explained by measured charge (from R1)", author="Engineer A (scripted)",
                               applicability={"required_roles": ["pack_current", "soc"], "machine_class": "battery_electric_vehicle"})
    s = SC.review(led, s["id"], reviewer="Engineer B (scripted)", reviewer_kind="engineer", decision="approve",
                  notes="scripted acceptance step: checked the finding's rows and the threshold")
    backfill1 = ingested.backfill(led, s["id"])
    O.open_case(led, r1, title="R1 (SYNTHETIC)", signature=O.signature_of(checks1), actor="Engineer A (scripted)", confidential=False,
                context=knowledge.context_of(checks1, prof1, True))
    O.propose(led, r1, hypothesis_id="A", statement="SOC estimation drift (from the deterministic assessment)", origin="deterministic",
              actor="Engineer A (scripted)", actor_kind="engineer")
    pc = O.performed(led, r1, template_id="DOC-BMS-HISTORY", method="BMS charge and calibration history export (scripted)",
                     performed_by="Engineer A (scripted)", qualification="none", actor="Engineer A (scripted)", actor_kind="engineer")
    ob = O.observe(led, r1, performed_id=pc["id"], summary="no full-charge calibration for 40 days; SOC set high after an incomplete charge (scripted)",
                   measurements={"days_since_calibration": 40}, supports=["A"], contradicts=[], actor="Engineer A (scripted)", actor_kind="engineer")
    O.verify(led, r1, hypothesis_id="A", result="confirmed", observation_ids=[ob["id"]], basis="BMS history (scripted acceptance step)",
             actor="Engineer B (scripted)", actor_kind="engineer")
    # R2, R3, R4
    R2 = arrive("R2", upload("SYNTHETIC-R2-healthy.zip", lambda p: gen.write("healthy", p, 2)))
    R3 = arrive("R3", upload("SYNTHETIC-R3-correction.zip", lambda p: gen.write("correction", p, 3)))
    R4 = arrive("R4", upload(Path(a.ulog).name, lambda p: Path(shutil.copy(a.ulog, p))), agent_ok_public=True)

    # ---------------------------------------------------------------- criteria (PROTOCOL.md)
    def rejected(claims):
        return [c for c in claims or [] if c.get("verdict") == "rejected"]

    def status(R):
        return ((R["main_investigator_record"] or {}).get("result") or {}).get("status")

    def scen_result(rid):
        return [x for x in ingested.results_for(led, rid) if x.get("scenario_id") == s["id"]]

    C = {}
    C["C1"] = all(R["profile"] == p for R, p in ((R1, "synthetic-bev-tables"), (R2, "synthetic-bev-tables"), (R3, "synthetic-bev-tables"), (R4, "px4-battery-status")))
    C["C2"] = all(R["investigation"]["agent"].get("investigator") == "main" and R["investigation"]["agent"].get("mode") == "agent"
                  and R["investigation"]["deterministic"]["checks"]["charge_accounting"]["outcome"] == "VIOLATION"
                  and not rejected(R["investigation"]["agent"].get("constrained_claims")) and status(R) not in (None, "no_failure_found") for R in (R1, R3))
    r2s, r3s = scen_result(R2["recording"]), scen_result(R3["recording"])
    C["C3"] = bool(r2s) and all(x.get("outcome") == "PASS" for x in r2s) and bool(r3s) and r3s[-1].get("outcome") == "VIOLATION"
    ku = R3["investigation"].get("knowledge_used") or []
    hyp3 = {h["id"]: h for h in R3["investigation"]["deterministic"]["hypotheses"]}
    C["C4"] = (any(k["case_id"] == r1 and k["hypothesis_id"] == "A" and k["applicability"] == "applies" for k in ku)
               and (R3["investigation"]["deterministic"]["next_check"] or {}).get("id") == "DOC-BMS-HISTORY"
               and hyp3.get("A", {}).get("status") != "confirmed")
    res4 = (R4["main_investigator_record"] or {}).get("result") or {}
    ph4 = (R4["main_investigator_record"] or {}).get("physical") or {}
    text4 = f"{res4.get('headline', '')} {res4.get('explanation', '')}"
    C["C5"] = (ph4.get("family_applies") is False and R4["investigation"]["deterministic"]["checks"]["charge_accounting"]["outcome"] != "VIOLATION"
               and not hypotheses.HV_VEHICLE_TERMS.search(text4) and not rejected(R4["investigation"]["agent"].get("constrained_claims")))
    C["C6"] = all(R["investigation"]["agent"].get("mode") == "agent" and (R["main_investigator_record"] or {}).get("steps")
                  and not (R["main_investigator_record"] or {}).get("dropped") for R in (R1, R2, R3, R4))
    rows = []
    for R in (R1, R2, R3, R4):
        m = R["main_investigator_record"] or {}
        rows.append(f"| {R['label']} | {R['profile']} | {R['investigation']['agent'].get('investigator')} | {R['investigation']['agent'].get('mode')} | "
                    f"{R['investigation']['deterministic']['checks']['charge_accounting']['outcome']} | {status(R)} | {len(m.get('steps') or [])} | "
                    f"{len(m.get('model_calls') or [])} | {m.get('seconds')} | {len(rejected(R['investigation']['agent'].get('constrained_claims')))} | "
                    f"{((m.get('result') or {}).get('headline') or '').replace('|', '/')[:110]} |")
    models = sorted({c.get("model_id") for R in (R1, R2, R3, R4) for c in (R["main_investigator_record"] or {}).get("model_calls") or [] if c.get("model_id")})
    md = ["# End-to-end acceptance v1: results", "", "Protocol: PROTOCOL.md (committed before this run). Engineer steps scripted and labelled. "
          f"Model ids reported by the CLI: {', '.join(models) or 'none'}.", "",
          "| Criterion | Result |", "|---|---|"] + [f"| {k} | {'PASS' if v else 'FAIL'} |" for k, v in C.items()] + ["",
          "| Run | Profile | Investigator | Mode | Check A | Report status | Tool calls | Model calls | s | Rejected claims (final) | Headline |",
          "|---|---|---|---|---|---|---|---|---|---|---|"] + rows + ["",
          f"Scenario {s['id']} back-fill after approval: {json.dumps(backfill1, default=str)[:300]}", "",
          f"R2 scenario results: {[x.get('outcome') for x in r2s]}; R3: {[x.get('outcome') for x in r3s]}", "",
          f"R3 earlier knowledge used: {ku}"]
    (HERE / f"RESULTS-run{a.run}.md" if not a.smoke else work / "RESULTS.md").write_text("\n".join(md) + "\n")
    print("\n".join(md[:12]))
    return 0 if all(C.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
