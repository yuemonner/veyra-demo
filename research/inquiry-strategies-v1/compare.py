"""inquiry-strategies-v1 (PREREGISTRATION.md): the same agent, tools, model and budget under three investigation
policies (free / coverage / infogain), on sealed SYNTHETIC incidents. Measures how much uncertainty about the cause each
policy removes, and at what cost, without ever falsely demoting the true cause.

  python compare.py seal  --key K --n 5            draw the cases AFTER the freeze (os.urandom); prints only the key's SHA-256
  python compare.py run   --key K --strategy S --out DIR       investigate every case with VEYRA_INQUIRY_STRATEGY=S
  python compare.py score --key K --out DIR                    score all strategies found in DIR (reads the key only now)

The key lives outside the repository; the agent never sees it (file names carry neutral labels only).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TAG = "inquiry-strategies-v1-frozen"
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "research" / "inquiry-eval-v1"))
CAUSES = ("A", "B", "C")
MECH = {"C1": "C", "C2": "C"}
LEAVES = {"soc_drift": {"A"}, "capacity_fade": {"B"}, "current_scale": {"C", "C1"}, "sensor_bypass": {"C", "C2"}, "drift_weak_cell": {"A"}}


def truth_contradicted(mr: dict, cause: str) -> list:
    """Counted tests that contradicted the true cause or its true mechanism outright (not conditionally, not on a conditional value)."""
    iq = mr.get("inquiry") or {}
    return [t["id"] for t in iq.get("tests") or [] if t["update"].get("counts") and t.get("value_kind") != "conditional"
            and LEAVES[cause] & set(t["update"].get("contradicted") or [])]


def _gen():
    spec = importlib.util.spec_from_file_location("heldout_gen", ROOT / "scripts" / "make_synthetic_heldout.py")
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    return g


def frozen() -> str:
    """The agent's code (backend, scripts) must be exactly the tag's, with no local changes. Harness-only commits after the tag
    (orchestration, e.g. resuming after an infrastructure failure) are allowed and recorded."""
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    tag = subprocess.run(["git", "-C", str(ROOT), "rev-parse", f"{TAG}^{{commit}}"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", "backend", "scripts", "research/inquiry-strategies-v1"],
                           capture_output=True, text=True).stdout.strip()
    changed = subprocess.run(["git", "-C", str(ROOT), "diff", "--name-only", tag, head, "--", "backend", "scripts"], capture_output=True, text=True).stdout.strip() if tag else "?"
    if not tag or dirty or changed:
        raise SystemExit(f"refusing to run: the agent code differs from {TAG} ({tag[:9] or 'missing'}) or has local changes:\n{dirty}\n{changed}")
    return head


def seal(key: Path, n: int) -> None:
    g = _gen()
    rnd = random.Random(int.from_bytes(os.urandom(16), "big"))
    causes = list(g.CAUSES)[:n] + [rnd.choice(g.CAUSES) for _ in range(max(0, n - len(g.CAUSES)))]
    rnd.shuffle(causes)
    cases = [{"label": f"S{i + 1}", **g.draw(c, rnd.randrange(10 ** 9))} for i, c in enumerate(causes)]
    if key.exists():
        raise SystemExit(f"{key} exists: a sealed key is never redrawn")
    key.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"version": "inquiry-strategies-v1", "cases": cases}, indent=1, sort_keys=True).encode()
    key.write_bytes(body)
    os.chmod(key, 0o600)
    print("sealed", len(cases), "cases; sha256", hashlib.sha256(body).hexdigest())


def run(key: Path, strat: str, out: Path, resume: bool = False) -> None:
    """resume: re-run only the cases whose recorded run did not reach the agent (an infrastructure failure, e.g. a usage
    limit); the failed record is kept as <label>.failed-<k>.json. A case the agent completed is never re-run."""
    commit = frozen()
    body = key.read_bytes()
    cases = json.loads(body)["cases"]
    dest = out / strat
    if dest.exists() and not resume:
        raise SystemExit(f"{dest} exists: a recorded run is never overwritten (use --resume after an infrastructure failure)")
    dest.mkdir(parents=True, exist_ok=True)
    if resume:
        todo = []
        for c in cases:
            f = dest / f"{c['label']}.json"
            if f.exists() and json.loads(f.read_text()).get("agent_mode") == "agent":
                continue
            if f.exists():
                k = len(list(dest.glob(f"{c['label']}.failed-*.json"))) + 1
                f.rename(dest / f"{c['label']}.failed-{k}.json")
            todo.append(c)
        cases = todo
    work = out / f".work-{strat}"
    shutil.rmtree(work, ignore_errors=True)
    (work / "recordings").mkdir(parents=True)
    os.environ.update(VEYRA_ENGINE_DB=str(work / "engine.db"), VEYRA_AUTO_DEBRIEF="true", VEYRA_PRIVATE_CASES_DIR=str(work / "private"),
                      VEYRA_INQUIRY="on", VEYRA_INQUIRY_STRATEGY=strat)
    os.environ.pop("VEYRA_PRIVATE_ALLOW_AGENT", None)
    from app.agent import investigator as MAIN
    from app.ingest import decoded_csv
    from app.physchecks import ingested, investigation as INV
    from app.physchecks.store import Ledger
    from app.store.sqlite_store import CaseStore
    ingested.recordings_root = lambda: work / "recordings"
    led = Ledger(work / "engineering.db")
    g = _gen()
    for i, c in enumerate(cases):
        rid = f"rec-{0xF0 + i:016x}"
        d = work / "recordings" / rid
        d.mkdir()
        z = g.write(c, d / f"SYNTHETIC-{c['label']}.zip")
        (d / "recording.json").write_text(json.dumps({"id": rid, "filename": z.name, "stored_as": z.name, "inspection": decoded_csv.inspect(z)}))
        t0 = time.time()
        ingested.auto_scan(led, rid)
        cid = ingested.auto_debrief(rid)
        entry = INV.run(led, rid, *ingested.as_case(rid), trigger="ingest")
        st = CaseStore(work / "engine.db")
        try:
            mr = MAIN.latest(st, cid) if cid else None
        finally:
            st.close()
        rec = {"label": c["label"], "strategy": strat, "commit": commit, "key_sha256": hashlib.sha256(body).hexdigest(),
               "seconds": round(time.time() - t0, 1), "agent_mode": entry["payload"]["agent"].get("mode"), "main_investigator_record": mr}
        (dest / f"{c['label']}.json").write_text(json.dumps(rec, indent=1, default=str, ensure_ascii=False))
        print(strat, c["label"], rec["agent_mode"], rec["seconds"], "s", flush=True)
    shutil.rmtree(work, ignore_errors=True)


def top_set(mr: dict) -> set:
    """The causes (A, B, C) the investigation leaves in first place: the first ranking tier, else every cause no counted test
    contradicted."""
    res = mr.get("result") or {}
    iq = mr.get("inquiry") or {}
    ranking = res.get("ranking") or []
    if ranking:
        first = {MECH.get(h, h) for h in ranking[0].get("hypotheses") or []} & set(CAUSES)
        if first:
            return first
    alive = {MECH.get(h["id"], h["id"]) for h in iq.get("hypotheses") or [] if h.get("standing") != "contradicted" and not h.get("combines")}
    return (alive & set(CAUSES)) or set(CAUSES)


def score_case(mr: dict, truth: dict) -> dict:
    import criteria as CR
    true_ids = CR.TRUTH_TO_IDS[truth["cause"]] & set(CAUSES)
    top = top_set(mr)
    correct = bool(true_ids & top)
    h = CR.heldout(mr, truth) if mr.get("mode") == "agent" else {}
    usage = mr.get("usage") or {}
    iq = (mr.get("inquiry") or {}).get("summary") or {}
    demoted = truth_contradicted(mr, truth["cause"])
    return {"cause": truth["cause"], "mode": mr.get("mode"), "top": sorted(top), "correct": correct, "truth_contradicted_by": demoted,
            "bits": round(math.log2(len(CAUSES) / len(top)), 3) if correct else 0.0, "false_exclusion": not correct,
            "H3_truth_not_demoted": h.get("H3_truth_not_demoted"), "H4_no_false_certainty": h.get("H4_no_false_certainty"),
            "H5_discovery": h.get("H5_discovery"), "tests_counted": iq.get("counted"), "discriminating": iq.get("discriminating"),
            "explorations": iq.get("explorations"), "tool_calls": len(mr.get("steps") or []), "model_calls": usage.get("model_calls"),
            "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"), "seconds": mr.get("seconds"),
            "outstanding_at_acceptance": len((mr.get("inquiry") or {}).get("outstanding_at_acceptance") or [])}


def score(key: Path, out: Path) -> None:
    cases = {c["label"]: c for c in json.loads(key.read_bytes())["cases"]}
    table, totals = {}, {}
    for sd in sorted(p for p in out.iterdir() if p.is_dir() and not p.name.startswith(".")):
        rows = {}
        for f in sorted(x for x in sd.glob("S*.json") if ".failed-" not in x.name):
            rec = json.loads(f.read_text())
            rows[rec["label"]] = score_case(rec.get("main_investigator_record") or {}, cases[rec["label"]])
        table[sd.name] = rows
        rs = list(rows.values())
        totals[sd.name] = {"cases": len(rs), "agent_runs": sum(r["mode"] == "agent" for r in rs), "bits": round(sum(r["bits"] for r in rs), 3),
                           "false_exclusions": sum(r["false_exclusion"] for r in rs), "H3_fail": sum(r["H3_truth_not_demoted"] is False or bool(r["truth_contradicted_by"]) for r in rs),
                           "H4_fail": sum(r["H4_no_false_certainty"] is False for r in rs), "H5_pass": sum(r["H5_discovery"] is True for r in rs),
                           "model_calls": sum(r["model_calls"] or 0 for r in rs), "tool_calls": sum(r["tool_calls"] or 0 for r in rs),
                           "seconds": round(sum(r["seconds"] or 0 for r in rs), 1)}
    ok = [s for s, t in totals.items() if t["false_exclusions"] == 0 and t["H3_fail"] == 0 and t["agent_runs"] == t["cases"]]
    pool = ok or list(totals)
    chosen = sorted(pool, key=lambda s: (totals[s]["false_exclusions"], -totals[s]["bits"], totals[s]["model_calls"]))[0] if pool else None
    body = {"key_sha256": hashlib.sha256(key.read_bytes()).hexdigest(), "totals": totals, "cases": table, "selected": chosen,
            "selection_rule": "zero false exclusions, zero truth demotions, every case run by the agent; then most bits; then fewest model calls"}
    (out / "RESULTS.json").write_text(json.dumps(body, indent=1))
    print(json.dumps({"totals": totals, "selected": chosen}, indent=1))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["seal", "run", "score"])
    ap.add_argument("--key", required=True)
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--strategy", choices=["free", "coverage", "infogain"])
    ap.add_argument("--out")
    ap.add_argument("--resume", action="store_true")
    a = ap.parse_args()
    key = Path(a.key).expanduser()
    if a.cmd == "seal":
        seal(key, a.n)
    elif a.cmd == "run":
        run(key, a.strategy, Path(a.out).expanduser(), a.resume)
    else:
        score(key, Path(a.out).expanduser())
    return 0


if __name__ == "__main__":
    sys.exit(main())
