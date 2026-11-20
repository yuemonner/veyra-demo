"""inquiry-eval-v1, held-out SYNTHETIC incidents (PREREGISTRATION.md).

  python research/inquiry-eval-v1/heldout.py seal  --key ~/Veyra-private/inquiry-eval-v1/heldout-KEY.json
        draws the causes and parameters AFTER the freeze from os.urandom, writes the key outside the repository and prints
        only its SHA-256 (record it in a commit before running). Nothing about the causes is printed.
  VEYRA_AGENT_BACKEND=claude-code python research/inquiry-eval-v1/heldout.py run --key ... --run N --work <dir>
        generates each package from the key, uploads it through the standard decoded-table path and lets the main
        investigator (inquiry loop on) investigate it on arrival. The agent never sees the key. Writes results-heldout-runN/.
  python research/inquiry-eval-v1/heldout.py score --key ... --run N      scores (criteria.heldout) and writes the table.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TAG = "inquiry-eval-v1-frozen"
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(HERE))


def _gen():
    spec = importlib.util.spec_from_file_location("heldout_gen", ROOT / "scripts" / "make_synthetic_heldout.py")
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    return g


def frozen(smoke: bool) -> str:
    if smoke:
        return "SMOKE (not frozen)"
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    tag = subprocess.run(["git", "-C", str(ROOT), "rev-parse", f"{TAG}^{{commit}}"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", "backend", "scripts", "research/inquiry-eval-v1/criteria.py",
                            "research/inquiry-eval-v1/heldout.py"], capture_output=True, text=True).stdout.strip()
    if not tag or head != tag or dirty:
        raise SystemExit(f"refusing to run: HEAD {head[:9]} is not {TAG} ({tag[:9] or 'missing'}) or the code has changes:\n{dirty}")
    return head


def seal(key: Path, n_extra: int = 1) -> None:
    g = _gen()
    rnd = random.Random(int.from_bytes(os.urandom(16), "big"))
    causes = list(g.CAUSES) + [rnd.choice(g.CAUSES) for _ in range(n_extra)]
    rnd.shuffle(causes)
    cases = [{"label": f"H{i + 1}", **g.draw(c, rnd.randrange(10 ** 9))} for i, c in enumerate(causes)]
    key.parent.mkdir(parents=True, exist_ok=True)
    if key.exists():
        raise SystemExit(f"{key} exists: a sealed key is never redrawn")
    body = json.dumps({"version": "inquiry-eval-v1-heldout", "cases": cases}, indent=1, sort_keys=True).encode()
    key.write_bytes(body)
    os.chmod(key, 0o600)
    print("sealed", len(cases), "cases; sha256", hashlib.sha256(body).hexdigest())


def run(key: Path, n: int, work: Path, smoke: bool) -> None:
    commit = frozen(smoke)
    body = key.read_bytes()
    cases = json.loads(body)["cases"]
    out_dir = (work / "results") if smoke else (HERE / f"results-heldout-run{n}")
    if not smoke and out_dir.exists():
        raise SystemExit(f"{out_dir} exists: a recorded run is never overwritten")
    if work.exists():
        shutil.rmtree(work)
    (work / "recordings").mkdir(parents=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    os.environ.update(VEYRA_ENGINE_DB=str(work / "engine.db"), VEYRA_AUTO_DEBRIEF="true", VEYRA_PRIVATE_CASES_DIR=str(work / "private"))
    os.environ.pop("VEYRA_PRIVATE_ALLOW_AGENT", None)
    if smoke:                                    # no model may run: API backend with no key is unavailable
        os.environ["VEYRA_AGENT_BACKEND"] = "api"
        os.environ.pop("ANTHROPIC_API_KEY", None)
    from app.agent import investigator as MAIN
    from app.ingest import decoded_csv
    from app.physchecks import ingested, investigation as INV
    from app.physchecks.store import Ledger
    from app.store.sqlite_store import CaseStore
    ingested.recordings_root = lambda: work / "recordings"
    led = Ledger(work / "engineering.db")
    g = _gen()
    for i, c in enumerate(cases):
        rid = f"rec-{0xE0 + i:016x}"
        d = work / "recordings" / rid
        d.mkdir()
        z = g.write(c, d / f"SYNTHETIC-{c['label']}.zip")        # the file name carries only the neutral label
        (d / "recording.json").write_text(json.dumps({"id": rid, "filename": z.name, "stored_as": z.name, "inspection": decoded_csv.inspect(z)}))
        t0 = time.time()
        ingested.auto_scan(led, rid)
        cid = ingested.auto_debrief(rid)
        entry = INV.run(led, rid, *ingested.as_case(rid), trigger="ingest")
        st = CaseStore(work / "engine.db")
        try:
            main_rec = MAIN.latest(st, cid) if cid else None
        finally:
            st.close()
        rec = {"label": c["label"], "commit": commit, "key_sha256": hashlib.sha256(body).hexdigest(), "seconds": round(time.time() - t0, 1),
               "profile": (ingested.chosen(ingested.recording_meta(rid)) or {}).get("profile"), "engine_case": cid,
               "agent_mode": entry["payload"]["agent"].get("mode"), "main_investigator_record": main_rec}
        (out_dir / f"{c['label']}.json").write_text(json.dumps(rec, indent=1, default=str, ensure_ascii=False))
        print(c["label"], rec["profile"], rec["agent_mode"], rec["seconds"], "s", flush=True)


def score(key: Path, n: int) -> None:
    import criteria as CR
    cases = json.loads(key.read_bytes())["cases"]
    rows = []
    for c in cases:
        p = HERE / f"results-heldout-run{n}" / f"{c['label']}.json"
        rec = json.loads(p.read_text())
        mr = rec.get("main_investigator_record") or {}
        s = CR.heldout(mr, c) if mr.get("mode") == "agent" else {"not run": rec.get("agent_mode")}
        rows.append({"label": c["label"], "cause": c["cause"], "n_series": c["n_series"], "curve": c["curve"], **s})
    (HERE / f"RESULTS-heldout-run{n}.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    print(json.dumps(rows, indent=1, ensure_ascii=False))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["seal", "run", "score"])
    ap.add_argument("--key", required=True)
    ap.add_argument("--run", type=int, default=1)
    ap.add_argument("--work", default=None)
    ap.add_argument("--smoke", action="store_true")
    a = ap.parse_args()
    key = Path(a.key).expanduser()
    if a.cmd == "seal":
        seal(key)
    elif a.cmd == "run":
        run(key, a.run, Path(a.work).expanduser().resolve(), a.smoke)
    else:
        score(key, a.run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
