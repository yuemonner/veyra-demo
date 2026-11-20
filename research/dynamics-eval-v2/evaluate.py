"""dynamics-eval-v2 (PREREGISTRATION.md; harness copied from dynamics-eval-v1, unchanged except the tag): does the signal-dynamics capability make Veyra better at anticipating and
investigating faults on machines it has not seen, in two categories (battery, robot)?

  python evaluate.py seal   --key K [--seeds 6]                         draw the sealed sets AFTER the freeze (os.urandom)
  python evaluate.py engine --key K --out DIR                           deterministic engine evaluation (no model)
  python evaluate.py agent  --key K --agent new|old --code-root R --out DIR [--resume]   one agent over the agent set
  python evaluate.py score  --key K --out DIR                           score engine + agent results (reads the truth only now)

The key lives outside the repository. Case files carry neutral labels only. Recorded runs are never overwritten.
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
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TAG = "dynamics-eval-v2-frozen"
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))
import make_synthetic_dynamics as G  # noqa: E402

TRUTH_ID = {"drift": "L2a", "load": "L2b", "sensing": "L2c", "common": "L1", "external": "L3"}
LEAVES = ("L1", "L2a", "L2b", "L2c", "L3")


def frozen() -> str:
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    tag = subprocess.run(["git", "-C", str(ROOT), "rev-parse", f"{TAG}^{{commit}}"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", "backend", "scripts"], capture_output=True, text=True).stdout.strip()
    changed = subprocess.run(["git", "-C", str(ROOT), "diff", "--name-only", tag, head, "--", "backend", "scripts"], capture_output=True, text=True).stdout.strip() if tag else "?"
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
    body = json.dumps({"version": "dynamics-eval-v2", "engine": [{"label": f"E{i + 1:03d}", **c} for i, c in enumerate(eng)],
                       "agent": [{"label": f"A{i + 1:02d}", **c} for i, c in enumerate(agt)]}, indent=1, sort_keys=True).encode()
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_bytes(body)
    os.chmod(key, 0o600)
    print("sealed", len(eng), "engine cases and", len(agt), "agent cases; sha256", hashlib.sha256(body).hexdigest())


# ---- engine (deterministic, no model) ---------------------------------------------------------------------------------
def engine_case(c: Dict[str, Any]) -> Dict[str, Any]:
    """What Veyra computes on its own for one case: the anticipate screen and the generated tests (no model, no truth)."""
    from app.agent import inquiry as IQ
    from app.physchecks import anticipate as ANT, compute as C
    p = G.draw(c["domain"], c["cls"], c["seed"])
    rec, prof = G.to_recording(p, c["label"])
    ctx = C.Context(rec, prof)
    t0 = time.time()
    scr = ANT.screen(ctx)
    runs = [r for g in scr["groups"] for r in g["runs"]]
    first = runs[0] if runs else {}
    q = first.get("divergence") or {}
    iq = IQ.Inquiry(rec, prof, {"hypotheses": []})
    st = {h: iq.standing(h) for h in LEAVES if h in iq.hypotheses}
    out = {"label": c["label"], "groups": len(scr["groups"]), "event": first.get("event"), "event_t": first.get("event_t"),
           "warning": (first.get("warning") or {}).get("raised"), "lead_s": (first.get("warning") or {}).get("lead_s"),
           "first_observable_t": (first.get("warning") or {}).get("first_observable_t"),
           "diff_departure_z": q.get("diff_departure_z"), "far_departure_abs_z": q.get("far_departure_abs_z"), "differential_share": q.get("differential_share"),
           "load_coupling_change_z": q.get("load_coupling_change_z"), "jump_excess_z": q.get("jump_excess_z"), "baseline_robust": q.get("baseline_robust"),
           "skew_qualified": q.get("skew_qualified"), "limiting_member": q.get("limiting_member"), "identity_stable": q.get("identity_stable"),
           "investigated": bool(iq.dyn), "standing": {h: s["standing"] for h, s in st.items()},
           "contradicted_by": {h: s["contradicted_by"] for h, s in st.items()},
           "next_check": (iq.dyn or {}).get("next_check", {}) and (iq.dyn or {}).get("next_check", {}).get("id"),
           "unresolved": (iq.dyn or {}).get("unresolved_class_pairs"), "seconds": round(time.time() - t0, 2)}
    return out


def engine(key: Path, out: Path) -> None:
    commit = frozen()
    cases = json.loads(key.read_bytes())["engine"]
    dest = out / "engine"
    dest.mkdir(parents=True, exist_ok=True)
    for c in cases:
        f = dest / f"{c['label']}.json"
        if f.exists():
            continue
        try:
            r = engine_case(c)
        except Exception as e:                                  # recorded, never hidden
            r = {"label": c["label"], "error": f"{type(e).__name__}: {e}"}
        r["commit"] = commit
        f.write_text(json.dumps(r, indent=1, default=str))
        print(c["label"], r.get("warning"), r.get("error", ""), flush=True)


# ---- agent ------------------------------------------------------------------------------------------------------------
def agent(key: Path, which: str, code_root: Path, out: Path, resume: bool, only: Optional[str] = None) -> None:
    commit = frozen()
    cases = [c for c in json.loads(key.read_bytes())["agent"] if not only or c["label"] == only]
    dest = out / f"agent-{which}"
    dest.mkdir(parents=True, exist_ok=True)
    profiles = ROOT / "backend" / "app" / "physchecks" / "profiles"
    tmp = out / ".zips"
    tmp.mkdir(exist_ok=True)
    pdir = out / ".profiles"
    pdir.mkdir(exist_ok=True)
    for d in G.DOMAINS:
        (pdir / f"synthetic-dyn-{d}.json").write_text((profiles / f"synthetic-dyn-{d}.json").read_text())
    for c in cases:
        f = dest / f"{c['label']}.json"
        if f.exists():
            rec = json.loads(f.read_text())
            if rec.get("agent_mode") == "agent":
                continue
            if not resume:
                raise SystemExit(f"{f} exists and is not an agent run: use --resume (the failed record is kept)")
            k = len(list(dest.glob(f"{c['label']}.failed-*.json"))) + 1
            f.rename(dest / f"{c['label']}.failed-{k}.json")
        z = G.write(G.draw(c["domain"], c["cls"], c["seed"]), tmp / f"SYNTHETIC-{c['label']}.zip")
        cmd = [sys.executable, str(HERE / "run_case.py"), "--code-root", str(code_root), "--zip", str(z), "--profiles", str(pdir),
               "--label", c["label"], "--out", str(f)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not f.exists():
            f.write_text(json.dumps({"label": c["label"], "agent_mode": "harness_error", "stderr": r.stderr[-4000:], "harness_commit": commit}))
        else:
            rec = json.loads(f.read_text())
            rec["harness_commit"] = commit
            f.write_text(json.dumps(rec, indent=1, default=str, ensure_ascii=False))
        print(which, c["label"], json.loads(f.read_text()).get("agent_mode"), flush=True)


# ---- scoring ----------------------------------------------------------------------------------------------------------
KEYWORDS = [  # fixed before any run (PREREGISTRATION.md): statement text -> class
    ("sensing", r"sens(e|ing)|measurement (fault|error)|glitch|spike|wiring|harness|channel|reading error"),
    ("load", r"resistan|interconnect|connection|torque constant|demagnet|winding|gain loss|per unit of command|follows the (load|current|command)"),
    ("drift", r"capacity|imbalanc|weak cell|weaker|balanc|friction|wear|bearing|gearbox|drag|lower initial|state of charge of (that|one|the lowest) cell"),
    ("external", r"external|outside|operator|supervisor|e-?stop|another protection|commanded (stop|shutdown)|contactor.*first|vcu.*first|insulation"),
    ("common", r"pack-level|whole pack|group-wide|payload|terrain|all (wheels|actuators|cells)|every actuator|pack (soc|state of charge)|soc estimat|usable capacity of the pack"),
]
ID_MAP = {"L1": "common", "L2": "component", "L2a": "drift", "L2b": "load", "L2c": "sensing", "L3": "external",
          "A": "common", "B": "common", "D": "drift", "E": "sensing", "F": "external"}


def classes_of(hid: str, statement: str) -> List[str]:
    if hid in ID_MAP:
        return [ID_MAP[hid]]
    out = [c for c, rx in KEYWORDS if re.search(rx, statement or "", re.I)]
    return out[:1]


def agent_top(mr: Dict[str, Any]) -> Dict[str, Any]:
    res = (mr or {}).get("result") or {}
    iq = (mr or {}).get("inquiry") or {}
    stm = {h["id"]: h.get("statement", "") for h in iq.get("hypotheses") or []}
    for h in res.get("hypotheses") or []:
        stm.setdefault(h.get("physical_ref") or h.get("id"), f"{h.get('area', '')}: {h.get('claim', '')}")
    tier = []
    if res.get("ranking"):
        tier = [str(x) for x in res["ranking"][0].get("hypotheses") or []]
        how = "ranking tier 1"
    else:
        lead = [h for h in res.get("hypotheses") or [] if h.get("status") == "leading" and h.get("role") in ("main_cause", None)]
        lead = lead or [h for h in res.get("hypotheses") or [] if h.get("role") == "main_cause" and h.get("status") in ("open", "leading")]
        tier = [str(h.get("physical_ref") or h.get("id")) for h in lead]
        how = "leading / open main causes"
        for h in lead:
            stm[str(h.get("physical_ref") or h.get("id"))] = f"{h.get('area', '')}: {h.get('claim', '')}"
    cls = sorted({c for hid in tier for c in classes_of(hid, stm.get(hid, ""))})
    if "component" in cls:
        cls = sorted((set(cls) - {"component"}) | {"drift", "load", "sensing"})
    return {"tier": tier, "classes": cls, "how": how}


def next_check_ids(text: str) -> List[str]:
    t = (text or "").lower()
    out = []
    for cid, rx in (("first_fault", r"first.?fault|fault log|event record|protection code|trigger(ing)? (cell|axis)"),
                    ("step", r"step|known (current|command) change|step response"),
                    ("independent", r"independent|clamp|multimeter|sense.?wir|direct measurement"),
                    ("all_members", r"all (individual )?cell|every (individual )?cell|each cell|all (wheel|actuator|motor)"),
                    ("history", r"balanc|history|capacity (test|assessment)"),
                    ("payload", r"payload|terrain")):
        if re.search(rx, t):
            out.append(cid)
    return out


CHECK_SEPARATES = {"first_fault": {"external"}, "step": {"drift", "load"}, "independent": {"sensing"}, "all_members": {"common"},
                   "history": {"drift"}, "payload": {"common"}}


def auc(pos: List[float], neg: List[float]) -> Optional[float]:
    pos, neg = [x for x in pos if x is not None], [x for x in neg if x is not None]
    if not pos or not neg:
        return None
    s = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return round(s / (len(pos) * len(neg)), 4)


def score(key: Path, out: Path, engine_only: bool = False) -> None:
    K = json.loads(key.read_bytes())
    report: Dict[str, Any] = {"key_sha256": hashlib.sha256(key.read_bytes()).hexdigest()}
    # engine
    eng = {c["label"]: c for c in K["engine"]}
    rows = []
    for f in sorted((out / "engine").glob("E*.json")):
        r = json.loads(f.read_text())
        c = eng[r["label"]]
        r.update(domain=c["domain"], cls=c["cls"])
        rows.append(r)
    def summ(rs: List[Dict[str, Any]]) -> Dict[str, Any]:
        fault = [r for r in rs if r["cls"] in G.FAULT and "error" not in r]
        other = [r for r in rs if r["cls"] not in G.FAULT and "error" not in r]
        w2 = lambda r: bool(r.get("warning")) and (r.get("differential_share") or 0) >= 0.5
        ev = [r for r in rs if r["cls"] in G.FAULT + G.EVENT_BENIGN and r.get("investigated")]
        acc, sizes, bits, single, single_ok, tc, nchk = 0, [], [], 0, 0, 0, 0
        for r in ev:
            stand = r["standing"]
            alive = [h for h in LEAVES if stand.get(h) and stand[h] != "contradicted"]
            sup = [h for h in alive if stand[h] == "survived"]
            top = sup or alive
            truth = TRUTH_ID[r["cls"]]
            ok = truth in top
            acc += ok
            sizes.append(len(top))
            bits.append(math.log2(len(LEAVES) / len(top)) if ok and top else 0.0)
            if len(top) == 1:
                single += 1
                single_ok += ok
            tc += stand.get(truth) == "contradicted"
            nchk += bool(r.get("next_check")) and any(r["cls"] in p or ("component" in p and r["cls"] in G.FAULT) for p in (r.get("unresolved") or []))
        return {"cases": len(rs), "errors": sum("error" in r for r in rs),
                "anticipate": {"component_fault_cases": len(fault), "W1_detected": sum(bool(r.get("warning")) for r in fault),
                               "W1_false_alarms": sum(bool(r.get("warning")) for r in other), "non_component_cases": len(other),
                               "W1_false_alarms_by_class": {cl: sum(bool(r.get("warning")) for r in other if r["cls"] == cl) for cl in sorted({r["cls"] for r in other})},
                               "W1_missed_by_class": {cl: sum(not r.get("warning") for r in fault if r["cls"] == cl) for cl in G.FAULT},
                               "W2_detected": sum(w2(r) for r in fault), "W2_false_alarms": sum(w2(r) for r in other),
                               "lead_s_median_W1": (sorted(x["lead_s"] for x in fault if x.get("lead_s") is not None) or [None])[len([x for x in fault if x.get("lead_s") is not None]) // 2],
                               "AUC_diff_departure_z": auc([r.get("diff_departure_z") for r in fault], [r.get("diff_departure_z") for r in other]),
                               "AUC_differential_share": auc([r.get("differential_share") for r in fault], [r.get("differential_share") for r in other])},
                "investigate": {"event_cases_investigated": len(ev), "truth_in_top_set": acc, "mean_top_set_size": round(sum(sizes) / len(sizes), 3) if sizes else None,
                                "bits_total": round(sum(bits), 3), "single_answer_cases": single, "single_answer_correct": single_ok,
                                "truth_contradicted": tc, "next_check_addresses_an_unresolved_pair_with_truth": nchk}}
    report["engine"] = {"all": summ(rows), **{d: summ([r for r in rows if r["domain"] == d]) for d in G.DOMAINS}}
    if engine_only:
        (out / "RESULTS-engine.json").write_text(json.dumps(report, indent=1, default=str))
        print(json.dumps(report["engine"], indent=1, default=str))
        return
    # agents
    agt = {c["label"]: c for c in K["agent"]}
    for sd in sorted(p for p in out.glob("agent-*") if p.is_dir()):
        per = {}
        for f in sorted(sd.glob("A*.json")):
            if ".failed-" in f.name:
                continue
            rec = json.loads(f.read_text())
            c = agt[rec["label"]]
            mr = rec.get("main_investigator_record") or {}
            top = agent_top(mr)
            res = mr.get("result") or {}
            nx = next_check_ids(json.dumps(res.get("next_action") or {}))
            iq = mr.get("inquiry") or {}
            ops = [s.get("input", {}).get("op") for s in mr.get("steps") or [] if s.get("tool") in ("inquiry_compute", "inquiry_predict")]
            dyn_ops = [o for o in ops if o in ("rate", "changepoint", "departure", "divergence", "quality")]
            per[rec["label"]] = {"domain": c["domain"], "cls": c["cls"], "mode": rec.get("agent_mode"), "top": top,
                                 "correct": c["cls"] in top["classes"], "top_size": len(top["classes"]),
                                 "false_certainty": len(top["classes"]) == 1 and c["cls"] not in top["classes"],
                                 "next_check": nx, "next_check_addresses_truth": any(c["cls"] in CHECK_SEPARATES.get(x, set()) for x in nx),
                                 "agent_dynamics_ops": dyn_ops, "generated_tests": len((iq.get("summary") or {}) and [t for t in iq.get("predictions") or [] if t.get("source") == "veyra_generated"]),
                                 "rejected_claims": sum(1 for x in (mr.get("physical") or {}).get("constrained_claims") or [] if x.get("verdict") == "rejected"),
                                 "model_calls": (mr.get("usage") or {}).get("model_calls"), "tool_calls": len(mr.get("steps") or []), "seconds": rec.get("seconds")}
        rs = list(per.values())
        report[sd.name] = {"cases": per, "totals": {"runs": len(rs), "agent_runs": sum(r["mode"] == "agent" for r in rs),
                                                     "truth_in_top": sum(r["correct"] for r in rs), "false_certainty": sum(r["false_certainty"] for r in rs),
                                                     "mean_top_size": round(sum(r["top_size"] for r in rs) / len(rs), 3) if rs else None,
                                                     "next_check_addresses_truth": sum(r["next_check_addresses_truth"] for r in rs),
                                                     "by_domain": {d: sum(r["correct"] for r in rs if r["domain"] == d) for d in G.DOMAINS},
                                                     "model_calls": sum(r["model_calls"] or 0 for r in rs), "tool_calls": sum(r["tool_calls"] for r in rs)}}
    (out / "RESULTS.json").write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps({k: (v.get("totals") if isinstance(v, dict) and "totals" in v else v) for k, v in report.items() if k != "engine"}, indent=1, default=str))
    print(json.dumps(report["engine"], indent=1, default=str))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["seal", "engine", "agent", "score"])
    ap.add_argument("--key", required=True)
    ap.add_argument("--seeds", type=int, default=6)
    ap.add_argument("--agent", choices=["new", "old"])
    ap.add_argument("--code-root")
    ap.add_argument("--out")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--only", help="agent: run one case label only (interleaving old and new after a usage-limit stop)")
    ap.add_argument("--engine-only", action="store_true", help="score: the deterministic engine set only")
    a = ap.parse_args()
    key = Path(a.key).expanduser()
    if a.cmd == "seal":
        seal(key, a.seeds)
    elif a.cmd == "engine":
        engine(key, Path(a.out).expanduser())
    elif a.cmd == "agent":
        agent(key, a.agent, Path(a.code_root).expanduser(), Path(a.out).expanduser(), a.resume, a.only)
    else:
        score(key, Path(a.out).expanduser(), a.engine_only)
    return 0


if __name__ == "__main__":
    sys.exit(main())
