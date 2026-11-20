"""Does a case read through the standard upload path (a .zip of decoded tables + a recording profile) give the same
physical-check results as the same case read from its case directory? Local only; reads private data in place and
prints only outcomes, counts and digests, never values from the data.

  python scripts/validation/casedir_vs_package.py <case dir> <recording profile id> [--groups bms,vcu]

It zips the case's decoded CSVs for the given groups plus its manifest into a temporary folder OUTSIDE the repository
(next to the case), registers it as an uploaded recording there, loads it with the recording profile and compares the
three checks' outcomes, finding kinds and the charge-accounting episodes with the case-directory reading.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.ingest import decoded_csv  # noqa: E402
from app.physchecks import base, casedir, ingested  # noqa: E402


def summary(checks):
    out = {}
    for cid, r in checks.items():
        ep = [(round(e["soc_before_pct"], 2), round(e["soc_after_pct"], 2), round(e["unexplained_pp"], 2))
              for w in (r.get("windows") or {}).values() for e in (w.get("episodes") or [])]
        out[cid] = {"outcome": r["outcome"], "kinds": sorted(f["kind"] for f in r["findings"]), "episodes": sorted(ep)}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("case_dir")
    ap.add_argument("profile_id")
    ap.add_argument("--groups", default="bms,vcu")
    a = ap.parse_args()
    cd = casedir.load(Path(a.case_dir))
    direct = summary(base.run_all(cd.recording, cd.profile, cd.check_parameters()))
    groups = a.groups.split(",")
    data_dir = Path(a.case_dir) / cd.meta.get("data_dir", ".")
    work = Path(tempfile.mkdtemp(prefix=".veyra-package-check-", dir=a.case_dir))
    try:
        rid = "rec-00000000000000aa"
        folder = work / "recordings" / rid
        folder.mkdir(parents=True)
        z = folder / "package.zip"
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(data_dir.glob("*.csv")):
                if p.name == cd.meta.get("manifest") or decoded_csv.split_name(p)[1] in groups:
                    zf.write(p, p.name)
        insp = decoded_csv.inspect(z)
        (folder / "recording.json").write_text(json.dumps({"id": rid, "filename": z.name, "stored_as": z.name, "inspection": insp}))
        ingested.recordings_root = lambda: work / "recordings"
        rec, prof, _ = ingested.load(rid, a.profile_id)
        via_upload = summary(base.run_all(rec, prof, cd.check_parameters()))
        same = direct == via_upload
        print(json.dumps({"manifest": {k: len(v) if isinstance(v, list) else v for k, v in insp["manifest_check"].items()},
                          "windows": rec.windows, "same_results": same,
                          "outcomes": {k: (direct[k]["outcome"], via_upload[k]["outcome"]) for k in direct},
                          "differences": None if same else {k: {"kinds": (direct[k]["kinds"], via_upload[k]["kinds"]),
                                                                 "episodes_equal": direct[k]["episodes"] == via_upload[k]["episodes"],
                                                                 "episode_counts": (len(direct[k]["episodes"]), len(via_upload[k]["episodes"]))}
                                                             for k in direct if direct[k] != via_upload[k]}},
                         indent=1))
        return 0 if same else 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
