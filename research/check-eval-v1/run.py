"""Cell-physics check evaluation v1 (method proposed by an external EE advisor, 2026-11-16):
  1. fit tolerances on healthy packs (FIT set),
  2. set thresholds on a second healthy set (THRESHOLD set),
  3. count false alarms per pack-hour on a third healthy set (TEST set), and inject faults into the TEST packs, including
     pairs that should tie (the same fault in a record that cannot separate it), measuring missed detections too.
Everything here is SYNTHETIC (app.physchecks.synthetic_cells, invented OCV curves): it tests the checks' LOGIC, not whether
the same signatures identify real degradation. Real healthy fleet logs and resolved real incidents are the next step.

  PYTHONPATH=backend python research/check-eval-v1/run.py > research/check-eval-v1/results.json
"""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.physchecks import base  # noqa: E402
from app.physchecks import synthetic_cells as C  # noqa: E402

CHECKS = ("pack_cell_consistency", "weak_cell_attribution", "relaxation", "ocv_consistency")
# v1 used test seeds 201-240; after fixing the checks on what v1 showed, v1.1 is measured on fresh test seeds 301-340
SPLITS = {"fit": range(1, 21), "threshold": range(101, 121), "test": range(301, 341)}


def healthy_pack(seed: int) -> C.Pack:
    rng = random.Random(seed)
    n = 12
    return C.Pack(n=n, capacity_ah=50.0, soc0=rng.uniform(55, 90), r0=0.002, curve="sloped", seed=seed,
                  cell_capacity={i: 50.0 * rng.uniform(0.985, 1.015) for i in range(n)},       # manufacturing spread
                  cell_r0={i: 0.002 * rng.uniform(0.9, 1.1) for i in range(n)},
                  cell_soc0={},
                  noise_v=rng.uniform(0.0005, 0.0012), noise_i=rng.uniform(0.02, 0.1))


def drive(seed: int, *, varied: bool = True, rests: bool = True):
    rng = random.Random(seed * 7 + 3)
    segs = [(120.0, 0.0)] if rests else []
    total = rng.uniform(1500, 2400)
    if varied:
        while sum(d for d, _ in segs) < total:
            segs.append((rng.choice([30.0, 60.0, 90.0]), rng.uniform(5, 90)))
    else:
        segs.append((total, rng.uniform(30, 50)))
    if rests:
        segs.append((rng.uniform(1300, 2400), 0.0))
    return segs


FAULTS = {
    # name: (mutate pack, check that must see it, explanation it should support or None)
    "resistance x3.5 (one cell)": (lambda p: p.cell_r0.__setitem__(3, p.r0 * 3.5), "weak_cell_attribution", "resistance_or_connection"),
    "capacity fade 70% (one cell)": (lambda p: p.cell_capacity.__setitem__(3, 35.0), "weak_cell_attribution", "capacity_or_imbalance"),
    "imbalance -10 pp (one cell)": (lambda p: p.cell_soc0.__setitem__(3, p.soc0 - 10), "relaxation", None),
    "cell sensing step -50 mV": (lambda p: setattr(p, "sense_step", (3, 960.0, -0.05)), "weak_cell_attribution", "sensing"),
    "current offset -3 A": (lambda p: setattr(p, "current_offset_a", -3.0), "ocv_consistency", None),
}


def run(rec, prof, params):
    return base.run_all(rec, prof, {k: params for k in CHECKS})


def stats(r):
    """Statistics the tolerances are fitted on (healthy only)."""
    out = {}
    att = ((r["weak_cell_attribution"].get("windows") or {}).get("fit") or {})
    if att.get("coef"):
        out["weak_effect_v"] = max(abs(att["coef"][k] * att["range"][k]) for k in att["coef"] if not k.startswith("step"))
    for ev in ((r["relaxation"].get("windows") or {}).get("interruptions") or {}).get("items") or []:
        if ev.get("resistance_ratio"):
            out["r_ratio"] = max(out.get("r_ratio", 0), ev["resistance_ratio"])
        lo, av = ev.get("lowest_cell"), ev.get("average_cell")
        if lo and av and lo.get("v_relaxed") is not None and av.get("v_relaxed") is not None:
            out["relaxed_deficit_v"] = max(out.get("relaxed_deficit_v", 0), av["v_relaxed"] - lo["v_relaxed"])
    for p_ in ((r["ocv_consistency"].get("windows") or {}).get("rest_points") or {}).get("items") or []:
        if p_.get("error_pp") is not None:
            out["ocv_err_pp"] = max(out.get("ocv_err_pp", 0), abs(p_["error_pp"]))
    return out


def main():
    prof = C.cells_profile(curve="sloped")
    # 1. FIT: healthy statistics
    fit = [stats(run(C.simulate(healthy_pack(s), drive(s), rid=f"fit-{s}"), prof, {})) for s in SPLITS["fit"]]
    tol = {k: round(max(x.get(k, 0) for x in fit) * 1.25, 4) for k in ("weak_effect_v", "r_ratio", "ocv_err_pp", "relaxed_deficit_v")}
    # 2. THRESHOLD: parameters from the fitted tolerances, checked on a second healthy set (false alarms must be 0 there)
    params = {"resistance_ratio_flag": max(1.5, tol["r_ratio"]), "tolerance_pp": max(5.0, tol["ocv_err_pp"]),
              "cell_voltage_accuracy_v": max(0.005, tol["weak_effect_v"] / 3), "relaxed_deficit_flag_v": max(0.01, tol["relaxed_deficit_v"])}
    # threshold set: raise the flags of any check that alarms on these healthy packs (x1.25 per round, at most 4 rounds)
    flag_of = {"relaxation": ["relaxed_deficit_flag_v", "resistance_ratio_flag"], "ocv_consistency": ["tolerance_pp"],
               "weak_cell_attribution": ["cell_voltage_accuracy_v"], "pack_cell_consistency": []}
    thr_rounds = []
    for _round in range(4):
        thr_alarms = []
        for s in SPLITS["threshold"]:
            r = run(C.simulate(healthy_pack(s), drive(s), rid=f"thr-{s}"), prof, params)
            thr_alarms += [(s, k) for k in CHECKS if r[k]["outcome"] == "VIOLATION"]
        thr_rounds.append({"params": dict(params), "alarms": thr_alarms})
        if not thr_alarms:
            break
        for k in {k for _s, k in thr_alarms}:
            for f in flag_of[k]:
                params[f] = round(params[f] * 1.25, 4)
    # 3. TEST: false alarms per pack-hour on healthy packs, then injected faults (detection + attribution) and ties
    hours, alarms = 0.0, []
    for s in SPLITS["test"]:
        segs = drive(s)
        hours += sum(d for d, _ in segs) / 3600
        r = run(C.simulate(healthy_pack(s), segs, rid=f"test-{s}"), prof, params)
        alarms += [{"seed": s, "check": k, "summary": r[k]["summary"][:160]} for k in CHECKS if r[k]["outcome"] == "VIOLATION"]
    faults = {}
    for name, (mutate, check, expl) in FAULTS.items():
        det, attr, wrong, ties_abstain, ties_claim = 0, 0, [], 0, []
        for s in SPLITS["test"]:
            p = healthy_pack(s)
            mutate(p)
            r = run(C.simulate(p, drive(s), rid=f"f-{s}"), prof, dict(params, rest_max_a=4.0) if "offset" in name else params)
            hit = r[check]["outcome"] == "VIOLATION"
            det += hit
            if expl:
                sup = [x["explanation"] for x in (((r[check].get("windows") or {}).get("attribution") or {}).get("supported") or [])]
                attr += expl in sup
                if hit and expl not in sup:
                    wrong.append({"seed": s, "supported": sup})
            # tie: the same fault in a record that cannot separate it (constant current, no rests)
            if name.startswith(("resistance", "capacity")):
                p2 = healthy_pack(s)
                mutate(p2)
                rt = run(C.simulate(p2, drive(s, varied=False, rests=False), rid=f"tie-{s}"), prof, params)
                sup = [x["explanation"] for x in (((rt["weak_cell_attribution"].get("windows") or {}).get("attribution") or {}).get("supported") or [])]
                if expl not in sup:
                    ties_abstain += 1
                else:
                    ties_claim.append(s)
        n = len(SPLITS["test"])
        faults[name] = {"check": check, "detected": f"{det}/{n}", "missed": f"{n - det}/{n}",
                        **({"correct_attribution": f"{attr}/{n}", "other_attribution_examples": wrong[:3]} if expl else {}),
                        **({"tie_records_abstained": f"{ties_abstain}/{n}", "tie_records_claimed": ties_claim[:5]} if name.startswith(("resistance", "capacity")) else {})}
    out = {"synthetic": True, "caveat": "tests the checks' logic on injected faults in a SYNTHETIC pack model; not evidence about real degradation",
           "splits": {k: [v.start, v.stop - 1] for k, v in SPLITS.items()},
           "fit_tolerances": tol, "parameters_used": params,
           "threshold_rounds": thr_rounds,
           "test_healthy": {"pack_hours": round(hours, 1), "false_alarms": len(alarms), "per_pack_hour": round(len(alarms) / hours, 3), "examples": alarms[:5]},
           "test_faults": faults}
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
