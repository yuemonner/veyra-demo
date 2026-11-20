"""Run ONE investigation with the agent code at --code-root (old or new), on one recording, with matched settings:
same model backend (VEYRA_AGENT_BACKEND from the environment), same profiles (copied into a private profile folder, so the
old code reads the same signals), the same inquiry strategy (coverage) and the same turn budget (the code's own
INQUIRY_MAX_TURNS). Writes the full record as JSON. Used by evaluate.py and case_a.py; run as a subprocess so each
code root imports its own `app`.

  python run_case.py --code-root R --zip Z --profiles DIR --out F.json [--case-dir CASE --authorized-by TEXT]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code-root", required=True)
    ap.add_argument("--zip")
    ap.add_argument("--case-dir", help="a private case folder (case.json): its tables are zipped like case_a.py does")
    ap.add_argument("--groups", default="bms,vcu")
    ap.add_argument("--profiles", help="profiles to make available (copied into the private profile folder)")
    ap.add_argument("--authorized-by")
    ap.add_argument("--label", default="case")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    root = Path(a.code_root).resolve()
    sys.path.insert(0, str(root / "backend"))
    work = Path(tempfile.mkdtemp(prefix="veyra-dyneval-"))
    (work / "recordings").mkdir()
    (work / "private" / "profiles").mkdir(parents=True)
    for p in Path(a.profiles).glob("*.json") if a.profiles else []:
        shutil.copy(p, work / "private" / "profiles" / p.name)
    os.environ.update(VEYRA_ENGINE_DB=str(work / "engine.db"), VEYRA_AUTO_DEBRIEF="true", VEYRA_PRIVATE_CASES_DIR=str(work / "private"),
                      VEYRA_INQUIRY="on", VEYRA_INQUIRY_STRATEGY="coverage")
    if a.authorized_by:
        os.environ["VEYRA_PRIVATE_ALLOW_AGENT"] = "true"
    else:
        os.environ.pop("VEYRA_PRIVATE_ALLOW_AGENT", None)
    from app.agent import investigator as MAIN
    from app.ingest import decoded_csv
    from app.physchecks import ingested, investigation as INV
    from app.physchecks.store import Ledger
    from app.store.sqlite_store import CaseStore
    ingested.recordings_root = lambda: work / "recordings"
    rid = "rec-00000000000000d1"
    d = work / "recordings" / rid
    d.mkdir()
    if a.case_dir:
        from app.physchecks import casedir
        cd = casedir.load(Path(a.case_dir))
        data_dir = Path(a.case_dir) / cd.meta.get("data_dir", ".")
        z = d / "case_tables.zip"
        groups = a.groups.split(",")
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(data_dir.glob("*.csv")):
                if p.name == cd.meta.get("manifest") or decoded_csv.split_name(p)[1] in groups:
                    zf.write(p, p.name)
        shutil.copy(Path(a.case_dir) / cd.meta["profile"], work / "private" / "profiles" / "case-profile.json")
    else:
        z = d / Path(a.zip).name
        shutil.copy(a.zip, z)
    (d / "recording.json").write_text(json.dumps({"id": rid, "filename": z.name, "stored_as": z.name, "inspection": decoded_csv.inspect(z)}))
    t0 = time.time()
    ingested.auto_scan(Ledger(work / "engineering.db"), rid)
    cid = ingested.auto_debrief(rid)
    if a.authorized_by and cid:
        st = CaseStore(work / "engine.db")
        try:
            st.append(cid, "model_authorization", {"provider": "anthropic", "scope": "CASE A dynamics-eval-v1", "authorized_by": a.authorized_by,
                                                   "evidence": "recorded by the founder"}, actor="Founder")
        finally:
            st.close()
    led = Ledger(work / "engineering.db")
    entry = INV.run(led, rid, *ingested.as_case(rid), trigger="ingest")
    st = CaseStore(work / "engine.db")
    try:
        mr = MAIN.latest(st, cid) if cid else None
    finally:
        st.close()
    import subprocess
    commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    rec = {"label": a.label, "code_root": str(root), "commit": commit, "seconds": round(time.time() - t0, 1), "engine_case": cid,
           "profile": (ingested.chosen(ingested.recording_meta(rid)) or {}).get("profile"),
           "agent_mode": (entry["payload"].get("agent") or {}).get("mode"), "authorized_by": a.authorized_by,
           "investigation": entry["payload"], "main_investigator_record": mr}
    Path(a.out).write_text(json.dumps(rec, indent=1, default=str, ensure_ascii=False))
    print(a.label, rec["agent_mode"], rec["seconds"], "s", flush=True)
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
