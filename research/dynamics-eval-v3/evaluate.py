"""dynamics-eval-v3: matched before/after evaluation of dynamics-v3 / watch-v2 / inquiry-v4 (PREREGISTRATION.md).

  python evaluate.py seal   --key K                                       draw the sealed set (once; never redrawn)
  python evaluate.py engine --key K --name v2|v3 --code-root R --out DIR  deterministic engine of one code root (no model)
  python evaluate.py agent  --key K --name v2|v3 --code-root R --out DIR [--only A01]   agents (model), rerun-safe
  python evaluate.py case-a --name v3 --code-root R --run N --out DIR --authorized-by TEXT   one CASE A run
  python evaluate.py score  --key K --out DIR

Rules: the harness refuses to run unless this checkout's backend and scripts equal tag dynamics-eval-v3-frozen; a finished
agent record is never overwritten (a failed one is kept as *.failed-<k>.json); the key is opened only by the commands
that generate cases and by score.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TAG = "dynamics-eval-v3-frozen"
# written after the tag by design (the seal's SHA, the results): never code, so exempt from the freeze check
POST_FREEZE = [":(exclude)research/dynamics-eval-v3/SEALED.md", ":(exclude)research/dynamics-eval-v3/RESULTS.md"]
GEN = ROOT / "scripts" / "make_synthetic_dynamics.py"
sys.path.insert(0, str(ROOT / "scripts"))
import make_synthetic_dynamics as G  # noqa: E402

TRUTH_ID = {"drift": "L2a", "load": "L2b", "sensing": "L2c", "common": "L1", "external": "L3"}
LEAVES = ("L1", "L2a", "L2b", "L2c", "L3")
FRACTIONS = ("0.5", "0.75", "0.9", "1.0")
CASE_A_DIR = Path.home() / "Veyra-private" / "case-a"
CASE_A_PROFILES = Path.home() / "Veyra-private" / "profiles"


def frozen() -> str:
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    tag = subprocess.run(["git", "-C", str(ROOT), "rev-parse", f"{TAG}^{{commit}}"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", "backend", "scripts", "research/dynamics-eval-v3", *POST_FREEZE],
                           capture_output=True, text=True).stdout.strip()
    changed = subprocess.run(["git", "-C", str(ROOT), "diff", "--name-only", tag, head, "--", "backend", "scripts", "research/dynamics-eval-v3", *POST_FREEZE],
                             capture_output=True, text=True).stdout.strip() if tag else "?"
    if not tag or dirty or changed:
        raise SystemExit(f"refusing to run: the code differs from {TAG} ({tag[:9] or 'missing'}) or has local changes:\n{dirty}\n{changed}")
    return head


def seal(key: Path, seeds: int) -> None:
    if key.exists():
        raise SystemExit(f"{key} exists: a sealed key is never redrawn")
    rnd = random.Random(int.from_bytes(os.urandom(16), "big"))
    eng = [{"domain": d, "cls": c, "seed": rnd.randrange(10 ** 9)} for d in G.DOMAINS for c in G.CLASSES for _ in range(seeds)]
    rnd.shuffle(eng)
    agt = [{"domain": d, "cls": c, "seed": rnd.randrange(10 ** 9)} for d in G.DOMAINS for c in G.FAULT + G.EVENT_BENIGN]
    rnd.shuffle(agt)
    body = json.dumps({"version": "dynamics-eval-v3", "engine": [{"label": f"E{i + 1:03d}", **c} for i, c in enumerate(eng)],
                       "agent": [{"label": f"A{i + 1:02d}", **c} for i, c in enumerate(agt)]}, indent=1, sort_keys=True).encode()
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_bytes(body)
    os.chmod(key, 0o600)
    print("sealed", len(eng), "engine cases and", len(agt), "agent cases; sha256", hashlib.sha256(body).hexdigest())


def engine(key: Path, name: str, code_root: Path, out: Path) -> None:
    frozen()
    cases = json.loads(key.read_bytes())["engine"]
    tmp = out / ".cases.json"
    out.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps([{k: c[k] for k in ("label", "domain", "cls", "seed")} for c in cases]))
    r = subprocess.run([sys.executable, str(HERE / "engine_runner.py"), "--code-root", str(code_root), "--generator", str(GEN),
                        "--cases", str(tmp), "--out", str(out / f"engine-{name}")])
    tmp.unlink()
    if r.returncode:
        raise SystemExit(r.returncode)


def agent(key: Path, name: str, code_root: Path, out: Path, only: Optional[str]) -> None:
    commit = frozen()
    cases = [c for c in json.loads(key.read_bytes())["agent"] if not only or c["label"] == only]
    dest = out / f"agent-{name}"
    dest.mkdir(parents=True, exist_ok=True)
    pdir, tmp = out / ".profiles", out / ".zips"
    pdir.mkdir(exist_ok=True)
    tmp.mkdir(exist_ok=True)
    for d in G.DOMAINS:       # the same profiles for both agents (the frozen v3 checkout's)
        (pdir / f"synthetic-dyn-{d}.json").write_text((ROOT / "backend" / "app" / "physchecks" / "profiles" / f"synthetic-dyn-{d}.json").read_text())
    for c in cases:
        f = dest / f"{c['label']}.json"
        if f.exists():
            if json.loads(f.read_text()).get("agent_mode") == "agent":
                continue
            k = len(list(dest.glob(f"{c['label']}.failed-*.json"))) + 1
            f.rename(dest / f"{c['label']}.failed-{k}.json")
        z = G.write(G.draw(c["domain"], c["cls"], c["seed"]), tmp / f"SYNTHETIC-{c['label']}.zip")
        r = subprocess.run([sys.executable, str(HERE / "run_case.py"), "--code-root", str(code_root), "--zip", str(z), "--profiles", str(pdir),
                            "--label", c["label"], "--out", str(f)], capture_output=True, text=True)
        if r.returncode != 0 or not f.exists():
            f.write_text(json.dumps({"label": c["label"], "agent_mode": "harness_error", "stderr": r.stderr[-4000:], "harness_commit": commit}))
        else:
            rec = json.loads(f.read_text())
            rec["harness_commit"] = commit
            f.write_text(json.dumps(rec, indent=1, default=str, ensure_ascii=False))
        print(name, c["label"], json.loads(f.read_text()).get("agent_mode"), flush=True)


def case_a(name: str, code_root: Path, run: int, out: Path, authorized_by: str) -> None:
    commit = frozen()
    dest = out / "case-a"
    dest.mkdir(parents=True, exist_ok=True)
    f = dest / f"{name}-run{run}.json"
    if f.exists():
        if json.loads(f.read_text()).get("agent_mode") == "agent":
            raise SystemExit(f"{f} is a finished agent run: never overwritten")
        k = len(list(dest.glob(f"{name}-run{run}.failed-*.json"))) + 1
        f.rename(dest / f"{name}-run{run}.failed-{k}.json")
    r = subprocess.run([sys.executable, str(HERE / "run_case.py"), "--code-root", str(code_root), "--case-dir", str(CASE_A_DIR),
                        "--profiles", str(CASE_A_PROFILES), "--authorized-by", authorized_by, "--label", f"case-a-{name}-run{run}", "--out", str(f)])
    if f.exists():
        rec = json.loads(f.read_text())
        rec["harness_commit"] = commit
        f.write_text(json.dumps(rec, indent=1, default=str, ensure_ascii=False))
        print(name, run, rec.get("agent_mode"))
    if r.returncode:
        raise SystemExit(r.returncode)


# ---- scoring (fixed in PREREGISTRATION.md before any run) --------------------------------------------------------------
def top_set(stand: Dict[str, str]) -> List[str]:
    alive = [h for h in LEAVES if stand.get(h) and stand[h] != "contradicted"]
    sup = [h for h in alive if stand[h] == "survived"]
    return sup or alive


def engine_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    ok = [r for r in rows if "error" not in r]
    fault = [r for r in ok if r["cls"] in G.FAULT]
    other = [r for r in ok if r["cls"] not in G.FAULT]
    flag = lambda r, fr: bool(((r.get("watch") or {}).get(fr) or {}).get("flags"))
    w = {}
    for fr in FRACTIONS:
        w[fr] = {"component_flagged": sum(flag(r, fr) for r in fault), "false_alarms": sum(flag(r, fr) for r in other),
                 "flagged_by_mechanism": {m: sum(flag(r, fr) for r in fault if r["cls"] == m) for m in G.FAULT},
                 "false_alarms_by_class": {c: sum(flag(r, fr) for r in other if r["cls"] == c) for c in sorted({r["cls"] for r in other})},
                 "lean_correct_when_single": sum(1 for r in fault if flag(r, fr) and (r["watch"][fr].get("leans") or []) == [r["cls"]]),
                 "single_lean": sum(1 for r in fault if flag(r, fr) and len(r["watch"][fr].get("leans") or []) == 1)}
    earliest = []
    for r in fault:
        fr = next((x for x in FRACTIONS if flag(r, x)), None)
        if fr is not None and r.get("event_t"):
            earliest.append((1 - float(fr)) * r["event_t"] if fr != "1.0" else 1.0)
    ev = [r for r in ok if r["cls"] in G.FAULT + G.EVENT_BENIGN and r.get("investigated")]
    acc = tc = single = single_ok = nchk = 0
    sizes = []
    for r in ev:
        top = top_set(r["standing"])
        truth = TRUTH_ID[r["cls"]]
        acc += truth in top
        sizes.append(len(top))
        if len(top) == 1:
            single += 1
            single_ok += truth in top
        tc += r["standing"].get(truth) == "contradicted"
        nchk += bool(r.get("next_check")) and any(r["cls"] in p or ("component" in p and r["cls"] in G.FAULT) for p in (r.get("unresolved") or []))
    return {"cases": len(rows), "errors": len(rows) - len(ok),
            "watch": w, "lead_s_median_earliest_flag": sorted(earliest)[len(earliest) // 2] if earliest else None,
            "investigate": {"event_cases": len(ev), "truth_in_top_set": acc, "truth_contradicted_false_exclusion": tc,
                            "single_answer_cases": single, "single_answer_correct": single_ok, "false_certainty": single - single_ok,
                            "mean_top_set_size": round(sum(sizes) / len(sizes), 3) if sizes else None,
                            "next_check_addresses_an_unresolved_pair_with_truth": nchk}}


def score(key: Path, out: Path) -> None:
    import importlib.util                                   # the v2 harness, by path: agent_top, next_check_ids, CHECK_SEPARATES (unchanged)
    spec = importlib.util.spec_from_file_location("evaluate_v2", ROOT / "research" / "dynamics-eval-v2" / "evaluate.py")
    E2 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(E2)
    K = json.loads(key.read_bytes())
    rep: Dict[str, Any] = {"key_sha256": hashlib.sha256(key.read_bytes()).hexdigest()}
    eng = {c["label"]: c for c in K["engine"]}
    for d in sorted(out.glob("engine-*")):
        rows = []
        for f in sorted(d.glob("E*.json")):
            r = json.loads(f.read_text())
            r.update(domain=eng[r["label"]]["domain"], cls=eng[r["label"]]["cls"])
            rows.append(r)
        rep[d.name] = {"all": engine_summary(rows), **{dm: engine_summary([r for r in rows if r["domain"] == dm]) for dm in G.DOMAINS}}
    agt = {c["label"]: c for c in K["agent"]}
    for d in sorted(p for p in out.glob("agent-*") if p.is_dir()):
        per = {}
        for f in sorted(d.glob("A*.json")):
            if ".failed-" in f.name:
                continue
            rec = json.loads(f.read_text())
            c = agt[rec["label"]]
            mr = rec.get("main_investigator_record") or {}
            top = E2.agent_top(mr)
            res = mr.get("result") or {}
            nx = E2.next_check_ids(json.dumps(res.get("next_action") or {}))
            u = mr.get("usage") or {}
            per[rec["label"]] = {"domain": c["domain"], "cls": c["cls"], "mode": rec.get("agent_mode"), "top": top,
                                 "correct": c["cls"] in top["classes"], "top_size": len(top["classes"]),
                                 "false_certainty": len(top["classes"]) == 1 and c["cls"] not in top["classes"],
                                 "next_check": nx, "next_check_addresses_truth": any(c["cls"] in E2.CHECK_SEPARATES.get(x, set()) for x in nx),
                                 "truth_after_tests": ((mr.get("assessment_after_tests") or {}).get("hypotheses") and
                                                       {h["id"]: h["after_tests"] for h in mr["assessment_after_tests"]["hypotheses"]}.get(TRUTH_ID[c["cls"]])),
                                 "model_calls": u.get("model_calls"), "cost_usd": u.get("cost_usd"), "tool_calls": len(mr.get("steps") or []),
                                 "seconds": rec.get("seconds")}
        rs = list(per.values())
        n = len(rs) or 1
        rep[d.name] = {"cases": per, "totals": {
            "runs": len(rs), "agent_runs": sum(r["mode"] == "agent" for r in rs), "truth_in_top": sum(r["correct"] for r in rs),
            "false_certainty": sum(r["false_certainty"] for r in rs), "mean_top_size": round(sum(r["top_size"] for r in rs) / n, 3),
            "next_check_addresses_truth": sum(r["next_check_addresses_truth"] for r in rs),
            "seconds_median": sorted(r["seconds"] or 0 for r in rs)[len(rs) // 2] if rs else None,
            "cost_usd_total": round(sum(r["cost_usd"] or 0 for r in rs), 3), "model_calls": sum(r["model_calls"] or 0 for r in rs)}}
    ca = out / "case-a"
    if ca.is_dir():
        sys.path.insert(0, str(HERE))
        import case_a_targets as CT
        rep["case-a"] = {}
        for f in sorted(ca.glob("*.json")):
            if ".failed-" in f.name:
                continue
            rec = json.loads(f.read_text())
            mr = rec.get("main_investigator_record") or {}
            hs = (mr.get("assessment_after_tests") or {}).get("hypotheses") or []
            at = {h["id"]: h["after_tests"] for h in hs}
            above = {h["id"]: h.get("above") or [] for h in hs}
            if not hs and mr.get("inquiry"):
                # a record without assessment_after_tests (v2): the same labels from its own ledger (standing + separations)
                iq = mr["inquiry"]
                for h in iq.get("hypotheses") or []:
                    at[h["id"]] = {"contradicted": "weakened", "survived": "consistent"}.get(h.get("standing"), "untested")
                for sp in iq.get("separations") or []:
                    a_, b_ = sp["pair"]
                    if sp.get(f"{a_}_over_{b_}"):
                        above.setdefault(a_, []).append(b_)
                    if sp.get(f"{b_}_over_{a_}"):
                        above.setdefault(b_, []).append(a_)
                for h_, ab in above.items():
                    if at.get(h_) not in ("weakened",) and ab and not any(h_ in v for v in above.values()):
                        at[h_] = "supported"
                at["_derived_from_ledger"] = True
            tg = CT.score(mr)
            nx_ids = E2.next_check_ids(json.dumps((mr.get("result") or {}).get("next_action") or {}))
            dyn = next((e["payload"] for e in (mr.get("inquiry") or {}).get("entries") or [] if e.get("kind") == "dynamics"), None)
            unresolved = (dyn or {}).get("unresolved_class_pairs")
            if unresolved is None:
                unresolved = ((mr.get("dynamics_findings") or {}).get("next_check") or {}).get("addresses") or []
            sep = {tuple(sorted(p)) for p in unresolved or []}
            sep_ok = any(sep & {tuple(sorted(x)) for x in [(c, d) for c in E2.CHECK_SEPARATES.get(i, set()) for d in
                                                            ("drift", "load", "sensing", "common", "external", "component") if c != d]}
                         for i in nx_ids) if sep else bool(nx_ids)
            rep["case-a"][f.stem] = {"mode": rec.get("agent_mode"), "targets": tg,
                                     "external_L3_after_tests": at.get("L3"), "cell_level_L2_after_tests": at.get("L2"),
                                     "group_level_L1_after_tests": at.get("L1"), "L3_above": above.get("L3"),
                                     "next_check_ids": nx_ids, "engine_unresolved_pairs": unresolved,
                                     "question": {"i_discovery": bool(tg["T1_min_cell_acceleration"] and tg["T2_spread_and_growth_rate"] and tg["T4_how_early_before_protection"]),
                                                  "ii_reconciliation": bool(tg["T5_cell_vs_pack_level"] and at.get("L2") not in (None, "weakened", "weakened_post_hoc")
                                                                            and "L2" not in (above.get("L3") or []) and not any(m in (above.get("L3") or []) for m in ("L2a", "L2b", "L2c"))),
                                                  "iii_next_decision": bool(tg["T6_more_informative_next_check"] and sep_ok)},
                                     "seconds": rec.get("seconds"), "cost_usd": (mr.get("usage") or {}).get("cost_usd")}
    (out / "RESULTS.json").write_text(json.dumps(rep, indent=1, default=str))
    print(json.dumps({k: (v.get("totals") if isinstance(v, dict) and "totals" in v else v) for k, v in rep.items()}, indent=1, default=str)[:20000])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["seal", "engine", "agent", "case-a", "score"])
    ap.add_argument("--key")
    ap.add_argument("--seeds", type=int, default=6)
    ap.add_argument("--name")
    ap.add_argument("--code-root")
    ap.add_argument("--out")
    ap.add_argument("--only")
    ap.add_argument("--run", type=int, default=1)
    ap.add_argument("--authorized-by")
    a = ap.parse_args()
    if a.cmd == "seal":
        seal(Path(a.key).expanduser(), a.seeds)
    elif a.cmd == "engine":
        engine(Path(a.key).expanduser(), a.name, Path(a.code_root).expanduser(), Path(a.out).expanduser())
    elif a.cmd == "agent":
        agent(Path(a.key).expanduser(), a.name, Path(a.code_root).expanduser(), Path(a.out).expanduser(), a.only)
    elif a.cmd == "case-a":
        if not a.authorized_by:
            raise SystemExit("CASE A needs --authorized-by (the customer's recorded permission)")
        case_a(a.name, Path(a.code_root).expanduser(), a.run, Path(a.out).expanduser(), a.authorized_by)
    else:
        score(Path(a.key).expanduser(), Path(a.out).expanduser())
    return 0


if __name__ == "__main__":
    sys.exit(main())
