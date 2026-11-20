"""expert-methods-eval-v1 (PREREGISTRATION.md). Deterministic engine only; no model is called.

    python research/expert-methods-eval-v1/evaluate.py seal  --key ~/Veyra-private/expert-methods-eval-v1/KEY.json
    python research/expert-methods-eval-v1/evaluate.py run   --key ... --out <dir>      (resumable: one JSON per case)
    python research/expert-methods-eval-v1/evaluate.py score --key ... --out <dir>

`seal` draws the causes' seeds AFTER the freeze from os.urandom, refuses to overwrite a key, and prints only the SHA-256.
`run` refuses to run unless HEAD is the frozen tag and backend/ scripts/ and this harness are unchanged (--smoke skips it).
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import random
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TAG = "expert-methods-eval-v1-frozen"
sys.path.insert(0, str(ROOT / "backend"))
CAUSES = ("soc_drift", "capacity_fade", "current_scale", "sensor_bypass", "drift_weak_cell")
TRUTH = {"soc_drift": "A", "drift_weak_cell": "A", "capacity_fade": "B", "current_scale": "C", "sensor_bypass": "C"}
PER_CAUSE = 12


def _gen():
    spec = importlib.util.spec_from_file_location("heldout_gen", ROOT / "scripts" / "make_synthetic_heldout.py")
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    return g


def frozen(smoke: bool) -> str:
    if smoke:
        return "SMOKE (not frozen)"
    git = lambda *a: subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True).stdout.strip()
    head, tag = git("rev-parse", "HEAD"), git("rev-parse", f"{TAG}^{{commit}}")
    dirty = git("status", "--porcelain", "--", "backend", "scripts", "research/expert-methods-eval-v1/evaluate.py")
    if not tag or head != tag or dirty:
        raise SystemExit(f"refusing to run: HEAD {head[:9]} is not {TAG} ({tag[:9] or 'missing'}) or the code has changes:\n{dirty}")
    return head


def seal(key: Path) -> None:
    g = _gen()
    if key.exists():
        raise SystemExit(f"{key} exists: a sealed key is never redrawn")
    rnd = random.Random(int.from_bytes(os.urandom(16), "big"))
    causes = [c for c in CAUSES for _ in range(PER_CAUSE)]
    rnd.shuffle(causes)
    cases = [{"label": f"E{i + 1:03d}", "cause": c, "seed": rnd.randrange(10 ** 9)} for i, c in enumerate(causes)]
    for c in cases:
        c["params"] = g.draw(c["cause"], c["seed"])
    key.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"version": "expert-methods-eval-v1", "cases": cases}, indent=1, sort_keys=True).encode()
    key.write_bytes(body)
    print("sealed", len(cases), "cases; sha256", hashlib.sha256(body).hexdigest())


def _profile(g_profile_json, shape):
    from app.physchecks.profile import Profile
    pj = copy.deepcopy(g_profile_json)
    for r in pj["roles"].values():
        r["field"] = r["field"].split(":", 1)[-1]
    if shape:
        pj.setdefault("conditions", {})["ocv_shape"] = shape
    return Profile(pj)


def run_case(case, condition):
    from app.physchecks import base, expert_layer, facts as facts_mod, hypotheses, planner
    from app.physchecks.recording import load_decoded_csv
    g = _gen()
    p = dict(case["params"], label=case["label"])
    d = Path(tempfile.mkdtemp())
    g.write(p, d / "pkg.zip")
    zipfile.ZipFile(d / "pkg.zip").extractall(d)
    pj = json.loads((ROOT / "backend/app/physchecks/profiles/synthetic-bev-heldout.json").read_text())
    prof = _profile(pj, p["curve"] if condition == "D" else None)
    rec = load_decoded_csv(case["label"], {"drain": sorted(d.glob("drain_h*.csv"))}, fields=sorted({s["field"] for s in prof.roles.values()}), synthetic=True)
    checks = base.run_all(rec, prof)
    F = facts_mod.derive(rec, prof, checks)
    out = {"label": case["label"], "condition": condition}
    for engine in ("v3", "v4"):
        A = hypotheses.assess(checks, F, [], machine=hypotheses.machine_of(prof))
        ex = expert_layer.enrich(rec, prof, checks, F, A, rules={}) if engine == "v4" else None
        plan = planner.plan(A, checks)
        rec_ = plan.get("recommended") or []
        open_up = [h["id"] for h in A.get("hypotheses") or [] if h["role"] == "upstream_cause" and h["status"] in ("open", "insufficient_alone")]
        first = rec_[0] if rec_ else None
        o = {"open_upstream": open_up, "first_check": first and first["id"],
             "first_distinguishes": first and first["distinguishes"], "first_predictions": first and sorted(first["predictions"])}
        if ex:
            pl = ex["plausibility"]
            o["leading"] = pl.get("leading") and {k: pl["leading"][k] for k in ("hypothesis", "basis_kind")}
            o["conditional_leading"] = pl.get("conditional_leading") and pl["conditional_leading"]["hypothesis"]
            o["grades"] = {k: v.get("grade") for k, v in (pl.get("grades") or {}).items()}
            o["weakening"] = {k: [e["claim"][:120] for e in (v.get("record_evidence") or []) if e.get("direction") == "weakens"]
                              for k, v in (pl.get("grades") or {}).items() if k in ("A", "B", "C")}
            lt = ex["audit"].get("load_transitions")
            o["load_transitions"] = lt and lt["value"]["verdict"]
        out[engine] = o
    return out


def run(key: Path, out: Path, smoke: bool) -> None:
    head = frozen(smoke)
    cases = json.loads(key.read_text())["cases"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "RUN.json").write_text(json.dumps({"head": head, "n": len(cases)}))
    for c in cases:
        for cond in ("U", "D"):
            f = out / f"{c['label']}-{cond}.json"
            if f.exists():
                continue
            try:
                f.write_text(json.dumps(run_case(c, cond), indent=1, default=str))
            except Exception as e:  # kept, never overwritten
                k = len(list(out.glob(f"{c['label']}-{cond}.failed-*.json")))
                (out / f"{c['label']}-{cond}.failed-{k}.json").write_text(json.dumps({"error": f"{type(e).__name__}: {e}"}))
        print(c["label"], "done", flush=True)


def _separates(r, truth):
    if not r.get("first_check") or truth not in (r.get("open_upstream") or []):
        return False
    pairs = {tuple(sorted(p)) for p in r.get("first_distinguishes") or []}
    others = [h for h in r["open_upstream"] if h != truth]
    return all(tuple(sorted((truth, o))) in pairs for o in others)


def score(key: Path, out: Path) -> None:
    cases = {c["label"]: c for c in json.loads(key.read_text())["cases"]}
    S = {"by_condition": {}}
    for cond in ("U", "D"):
        m = {"n": 0, "M1_v3": 0, "M1_v4": 0, "harm": 0, "evidence_leads": 0, "evidence_correct": 0, "prior_leads": 0, "prior_correct": 0,
             "conditional_leads": 0, "bypass_not_seen": 0, "bypass_abstain": 0, "bypass_n": 0, "weakcell_ok": 0, "weakcell_n": 0,
             "prior_by_cause": {c: {"leads": 0, "correct": 0, "n": 0} for c in CAUSES}, "failed": 0}
        for label, c in cases.items():
            f = out / f"{label}-{cond}.json"
            if not f.exists():
                m["failed"] += 1
                continue
            r = json.loads(f.read_text())
            truth = TRUTH[c["cause"]]
            m["n"] += 1
            m["M1_v3"] += _separates(r["v3"], truth)
            m["M1_v4"] += _separates(r["v4"], truth)
            v4 = r["v4"]
            m["harm"] += bool((v4.get("weakening") or {}).get(truth))
            pc = m["prior_by_cause"][c["cause"]]
            pc["n"] += 1
            lead = v4.get("leading")
            if lead:
                if lead["basis_kind"] == "evidence":
                    m["evidence_leads"] += 1
                    m["evidence_correct"] += lead["hypothesis"] == truth
                else:
                    m["prior_leads"] += 1
                    m["prior_correct"] += lead["hypothesis"] == truth
                    pc["leads"] += 1
                    pc["correct"] += lead["hypothesis"] == truth
            m["conditional_leads"] += bool(v4.get("conditional_leading"))
            if c["cause"] == "sensor_bypass":
                m["bypass_n"] += 1
                m["bypass_not_seen"] += v4.get("load_transitions") == "not_seen"
                m["bypass_abstain"] += v4.get("load_transitions") is None
            if c["cause"] == "drift_weak_cell":
                m["weakcell_n"] += 1
                m["weakcell_ok"] += (v4.get("grades") or {}).get("D") == "contributing_timing_only" and (lead or {}).get("hypothesis") != "D"
        S["by_condition"][cond] = m
    U, D = S["by_condition"]["U"], S["by_condition"]["D"]
    ev = U["evidence_leads"] + D["evidence_leads"]
    evc = U["evidence_correct"] + D["evidence_correct"]
    S["criteria"] = {
        "P1_no_harm": U["harm"] == 0 and D["harm"] == 0,
        "P2_evidence_leads_right": ev >= 3 and evc >= 0.9 * ev,
        "P3_no_prior_without_chemistry": U["prior_leads"] == 0,
        "P4_no_planning_regression": U["M1_v4"] >= U["M1_v3"] and D["M1_v4"] >= D["M1_v3"],
    }
    (out / "RESULTS.json").write_text(json.dumps(S, indent=1))
    print(json.dumps(S, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["seal", "run", "score"])
    ap.add_argument("--key", type=Path, required=True)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--smoke", action="store_true")
    a = ap.parse_args()
    if a.cmd == "seal":
        seal(a.key.expanduser())
    elif a.cmd == "run":
        run(a.key.expanduser(), a.out, a.smoke)
    else:
        score(a.key.expanduser(), a.out)


if __name__ == "__main__":
    main()
