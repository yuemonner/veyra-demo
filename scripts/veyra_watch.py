"""Anticipate from the command line: watch one recording for a deteriorating relationship between comparable components
and print what Veyra found and which check it recommends. Deterministic, no model, nothing sent anywhere.

  python scripts/veyra_watch.py --synthetic battery drift 2001 [--as-of 900]
  python scripts/veyra_watch.py --zip TABLES.zip --profiles DIR [--as-of T]
  python scripts/veyra_watch.py --case-dir PRIVATE_CASE --profiles PRIVATE_PROFILES [--groups bms,vcu] [--as-of T]   (output stays local)

--as-of T reads only data up to T (what a monitor running at T could have seen). Prints JSON (--json) or a short report.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))


def _ingested_ctx(z: Path, profiles: Path | None, case_profile: Path | None):
    work = Path(tempfile.mkdtemp(prefix="veyra-watch-"))
    (work / "private" / "profiles").mkdir(parents=True)
    for p in profiles.glob("*.json") if profiles else []:
        shutil.copy(p, work / "private" / "profiles" / p.name)
    if case_profile:
        shutil.copy(case_profile, work / "private" / "profiles" / "case-profile.json")
    os.environ.update(VEYRA_PRIVATE_CASES_DIR=str(work / "private"))
    from app.ingest import decoded_csv
    from app.physchecks import compute as C, ingested
    ingested.recordings_root = lambda: work / "recordings"
    rid = "rec-00000000000000e1"
    d = work / "recordings" / rid
    d.mkdir(parents=True)
    shutil.copy(z, d / z.name)
    (d / "recording.json").write_text(json.dumps({"id": rid, "filename": z.name, "stored_as": z.name, "inspection": decoded_csv.inspect(d / z.name)}))
    rec, prof, _ = ingested.load(rid)
    return C.Context(rec, prof), work


def report(out: dict) -> str:
    lines = [f"Veyra Anticipate ({out['version']}): {out['summary']}" + (f", as of t = {out['as_of']} s" if out.get("as_of") is not None else "")]
    for g in out["groups"]:
        lines.append(f"\n[{g['title']}] window {g['window']}: {g['status'].replace('_', ' ')}")
        if g["status"] == "not_assessable":
            lines.append(f"  reason: {g.get('reason')}")
            continue
        if g.get("reason"):
            lines.append(f"  ({g['reason']})")
        n = g.get("numbers") or {}
        ids = g.get("identity_stable")
        lines.append(f"  limiting member: {g.get('limiting_member')}" + ("" if ids is None else f" = component #{int((g.get('limiting_component') or {}).get('index') or 0)}"
                     f" (the same physical component throughout: {'yes' if ids else 'NO, the extreme moved between components'})"))
        lines.append(f"  spread departure z = {n.get('diff_departure_z')}, own share of the excursion = {n.get('differential_share')}")
        if g.get("first_observable_t") is not None:
            lines.append(f"  first observable at t = {g['first_observable_t']} s")
        for s in g.get("signatures") or []:
            lines.append(f"  - {s['quantity']} = {s['value']}: {s['reading']}")
        if g.get("leans_to"):
            lines.append("  leans to: " + ", ".join(x["label"] for x in g["leans_to"]))
        if g.get("recommended_check"):
            lines.append(f"  recommended check: {g['recommended_check']['record']}")
    lines.append("\n" + out["note"])
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", nargs=3, metavar=("DOMAIN", "CLASS", "SEED"))
    ap.add_argument("--zip")
    ap.add_argument("--profiles")
    ap.add_argument("--case-dir")
    ap.add_argument("--groups", default="bms,vcu")
    ap.add_argument("--as-of", type=float)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    from app.physchecks import compute as C, watch as W
    work = None
    if a.synthetic:
        import make_synthetic_dynamics as G
        rec, prof = G.to_recording(G.draw(a.synthetic[0], a.synthetic[1], int(a.synthetic[2])), "synthetic")
        ctx = C.Context(rec, prof)
    elif a.case_dir:
        from app.ingest import decoded_csv
        from app.physchecks import casedir
        cd = casedir.load(Path(a.case_dir))
        data_dir = Path(a.case_dir) / cd.meta.get("data_dir", ".")
        tmpz = Path(tempfile.mkdtemp(prefix="veyra-watch-zip-")) / "case_tables.zip"
        with zipfile.ZipFile(tmpz, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(data_dir.glob("*.csv")):
                if p.name == cd.meta.get("manifest") or decoded_csv.split_name(p)[1] in a.groups.split(","):
                    zf.write(p, p.name)
        ctx, work = _ingested_ctx(tmpz, Path(a.profiles) if a.profiles else None, Path(a.case_dir) / cd.meta["profile"])
        shutil.rmtree(tmpz.parent, ignore_errors=True)
    elif a.zip:
        ctx, work = _ingested_ctx(Path(a.zip), Path(a.profiles) if a.profiles else None, None)
    else:
        ap.error("give --synthetic, --zip or --case-dir")
    out = W.watch(ctx, a.as_of)
    print(json.dumps(out, indent=1, default=str) if a.json else report(out))
    if work:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
