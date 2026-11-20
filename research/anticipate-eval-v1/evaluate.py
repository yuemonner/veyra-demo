"""anticipate-eval-v1 (PREREGISTRATION.md): does Veyra's Anticipate watch flag a deteriorating component BEFORE its protective
event, using only data up to that moment, without flagging healthy machines, pack/payload-wide loads or external stops?

  python evaluate.py seal  --key K [--seeds 8]     draw the sealed set AFTER the freeze (os.urandom); prints the key's SHA-256
  python evaluate.py run   --key K --out DIR       watch every case at each as_of fraction (rerun skips finished cases)
  python evaluate.py score --key K --out DIR       score (reads the truth only now)

Deterministic: no model. The key lives outside the repository.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TAG = "anticipate-eval-v1-frozen"
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))
import make_synthetic_dynamics as G  # noqa: E402

FRACTIONS = (0.5, 0.75, 0.9, 1.0)
FLAG_M = {"material_departure"}
FLAG_S = {"material_departure", "significant_not_material"}
CHECK_FOR = {"drift": "drift", "load": "load", "sensing": "sensing"}


def frozen() -> str:
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    tag = subprocess.run(["git", "-C", str(ROOT), "rev-parse", f"{TAG}^{{commit}}"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", "backend", "scripts"], capture_output=True, text=True).stdout.strip()
    changed = subprocess.run(["git", "-C", str(ROOT), "diff", "--name-only", tag, head, "--", "backend", "scripts"], capture_output=True, text=True).stdout.strip() if tag else "?"
    if not tag or dirty or changed:
        raise SystemExit(f"refusing to run: the code differs from {TAG} or has local changes:\n{dirty}\n{changed}")
    return head


def seal(key: Path, seeds: int) -> None:
    if key.exists():
        raise SystemExit(f"{key} exists: a sealed key is never redrawn")
    rnd = random.Random(int.from_bytes(os.urandom(16), "big"))
    cases = [{"domain": d, "cls": c, "seed": rnd.randrange(10 ** 9)} for d in G.DOMAINS for c in G.CLASSES for _ in range(seeds)]
    rnd.shuffle(cases)
    body = json.dumps({"version": "anticipate-eval-v1", "cases": [{"label": f"W{i + 1:03d}", **c} for i, c in enumerate(cases)]},
                      indent=1, sort_keys=True).encode()
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_bytes(body)
    os.chmod(key, 0o600)
    print("sealed", len(cases), "cases; sha256", hashlib.sha256(body).hexdigest())


def watch_case(c: dict) -> dict:
    """What a monitor would have reported at each as_of: fractions of the time of the first protective event when there is
    one (Veyra finds it from the data, not from the truth), else of the record's end."""
    from app.physchecks import anticipate as ANT, compute as C, dynamics as D, watch as W
    rec, prof = G.to_recording(G.draw(c["domain"], c["cls"], c["seed"]), c["label"])
    ctx = C.Context(rec, prof)
    gs = ANT.groups(ctx)
    if not gs:
        return {"label": c["label"], "error": "no comparable group"}
    g = gs[0]
    e = ANT._first_event(ctx, g)
    te = D._event_time(ctx, g["window"], e, None)["t"] if e else None
    t0, t1 = rec.span(g["window"])
    end = te if te is not None else t1
    out = {"label": c["label"], "event_t": te, "span": [t0, t1], "at": []}
    for f in FRACTIONS:
        as_of = t0 + f * (end - t0)
        if te is not None and f == 1.0:
            as_of = te - 1e-6                      # just before the event: still nothing of the event itself
        w = W.watch_group(ctx, g, as_of)
        out["at"].append({"fraction": f, "as_of": as_of, "status": w["status"], "leans_to": [x["class"] for x in w.get("leans_to") or []],
                          "check": (w.get("recommended_check") or {}).get("id"), "check_separates": (w.get("recommended_check") or {}).get("separates"),
                          "first_observable_t": w.get("first_observable_t"), "numbers": w.get("numbers")})
    return out


def run(key: Path, out: Path) -> None:
    commit = frozen()
    dest = out / "watch"
    dest.mkdir(parents=True, exist_ok=True)
    for c in json.loads(key.read_bytes())["cases"]:
        f = dest / f"{c['label']}.json"
        if f.exists():
            continue
        t = time.time()
        try:
            r = watch_case(c)
        except Exception as e:                       # recorded, never hidden
            r = {"label": c["label"], "error": f"{type(e).__name__}: {e}"}
        r["commit"], r["seconds"] = commit, round(time.time() - t, 2)
        f.write_text(json.dumps(r, indent=1, default=str))
        print(c["label"], [a["status"][:8] for a in r.get("at", [])], r.get("error", ""), flush=True)


def score(key: Path, out: Path) -> None:
    truth = {c["label"]: c for c in json.loads(key.read_bytes())["cases"]}
    rows = []
    for f in sorted((out / "watch").glob("W*.json")):
        r = json.loads(f.read_text())
        rows.append((truth[r["label"]], r))
    res = {"key_sha256": hashlib.sha256(key.read_bytes()).hexdigest(), "cases": len(rows),
           "errors": sum("error" in r for _, r in rows), "by": {}}
    for dom in ("all",) + tuple(G.DOMAINS):
        sel = [(c, r) for c, r in rows if "error" not in r and (dom == "all" or c["domain"] == dom)]
        fault = [(c, r) for c, r in sel if c["cls"] in G.FAULT]
        other = [(c, r) for c, r in sel if c["cls"] not in G.FAULT]
        d = {"component_fault_cases": len(fault), "other_cases": len(other), "at": {}}
        for k, fr in enumerate(FRACTIONS):
            for rule, flag in (("M_material", FLAG_M), ("S_significant_only", FLAG_S)):
                det = [c for c, r in fault if r["at"][k]["status"] in flag]
                fa = [c for c, r in other if r["at"][k]["status"] in flag]
                d["at"].setdefault(str(fr), {})[rule] = {
                    "detected": len(det), "false_alarms": len(fa),
                    "false_alarms_by_class": {cl: sum(c["cls"] == cl for c in fa) for cl in sorted({c["cls"] for c, _ in other})},
                    "missed_by_class": {cl: sum(c["cls"] == cl and r["at"][k]["status"] not in flag for c, r in fault) for cl in G.FAULT}}
            flagged = [(c, r["at"][k]) for c, r in fault if r["at"][k]["status"] in FLAG_M]
            single = [(c, a) for c, a in flagged if len(a["leans_to"]) == 1]
            d["at"][str(fr)]["M_material"].update({
                "lean_given": len([1 for _, a in flagged if a["leans_to"]]),
                "single_lean": len(single), "single_lean_correct": sum(a["leans_to"][0] == c["cls"] for c, a in single),
                "lean_contains_truth": sum(c["cls"] in a["leans_to"] for c, a in flagged),
                "check_addresses_truth": sum(any(c["cls"] in p for p in (a.get("check_separates") or [])) for c, a in flagged)})
        first = []
        for c, r in fault:
            fl = [a["fraction"] for a in r["at"] if a["status"] in FLAG_M]
            first.append(min(fl) if fl else None)
        d["earliest_fraction_M"] = {str(fr): sum(x == fr for x in first) for fr in FRACTIONS} | {"never": sum(x is None for x in first)}
        res["by"][dom] = d
    (out / "RESULTS.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["seal", "run", "score"])
    ap.add_argument("--key", required=True)
    ap.add_argument("--seeds", type=int, default=8)
    ap.add_argument("--out")
    a = ap.parse_args()
    key = Path(a.key).expanduser()
    if a.cmd == "seal":
        seal(key, a.seeds)
    elif a.cmd == "run":
        run(key, Path(a.out).expanduser())
    else:
        score(key, Path(a.out).expanduser())
    return 0


if __name__ == "__main__":
    sys.exit(main())
