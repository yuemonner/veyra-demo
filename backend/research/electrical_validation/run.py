"""Blind validation of app/physics/electrical.py. See PREREGISTRATION.md.

  python research/electrical_validation/run.py --split dev|heldout --out DIR [--engine v2|v1]

1. Generates every case into DIR/logs/<id>.txt (log only) and DIR/sealed/<id>.json (truth).
2. Runs the engine in a separate, isolated Python process that is handed only DIR/logs; it writes DIR/engine_<v>.json.
3. Scores the engine output against the sealed truth; writes DIR/score_<v>.json and prints a summary.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(os.path.dirname(HERE))
SPLITS = {"dev": range(0, 20), "heldout": range(1000, 1030)}


# ---------------------------------------------------------------- engine process (sees logs only)
def engine_main(logdir: str, out: str, which: str) -> None:
    sys.path.insert(0, BACKEND)
    if which == "v1":
        import importlib.util
        spec = importlib.util.spec_from_file_location("baseline_v1", os.path.join(HERE, "baseline_v1.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    else:
        from app.physics import electrical as mod  # noqa: F401
    res = {}
    for name in sorted(os.listdir(logdir)):
        with open(os.path.join(logdir, name)) as f:
            lines = f.read().splitlines()
        try:
            res[name[:-4]] = mod.analyse(lines)
        except Exception as exc:  # noqa: BLE001  a crash is a result too
            res[name[:-4]] = {"crash": f"{type(exc).__name__}: {exc}"}
    with open(out, "w") as f:
        json.dump(res, f, default=str)


# ---------------------------------------------------------------- scoring
FAULT_MECH = {"contact": "contact", "conductor": "conductor", "inverter_draw": "inverter_draw", "sensor_gain": "sensor_gain"}


def _branch_truth(t: Dict[str, Any]) -> Dict[str, str]:
    """Wheels with a fault-sized branch anomaly, and the mechanism."""
    out = {}
    for f in t["faults"]:
        if f["type"] in ("contact", "conductor") and f["size_ohm"] >= 0.004:
            out[f["wheel"]] = f["type"]
        elif f["type"] == "inverter_draw":
            out[f["wheel"]] = "inverter_draw"
        elif f["type"] == "sensor_gain":
            out[f["wheel"]] = "sensor_gain"
    return out


def _simulate_measurement(mid: str, wheel: str, t: Dict[str, Any], band: List[float]) -> Optional[str]:
    """What the measurement would read on the true vehicle, as an outcome class."""
    W = t["wheels"]
    others = [x for x in W if x != wheel]
    ref_contact = sorted(W[x]["contact_static_ohm"] for x in others)[1]
    ref_cable = sorted(W[x]["cable_ohm"] for x in others)[1]
    ref_loss = sorted(W[x]["loss_coeff"] for x in others)[1]
    w = W[wheel]
    contact_static = w["contact_static_ohm"] - ref_contact
    contact_loaded = contact_static + w["contact_load_extra_ohm"]
    cable = w["cable_ohm"] - ref_cable
    loss = w["loss_coeff"] / ref_loss
    shares = [s["share"][wheel] for s in t["snapshots"] if s.get("share")]
    share = sum(shares) / len(shares) if shares else None
    if mid == "segment_r":
        return "connector_high" if contact_static >= 0.003 else "cable_high" if cable >= 0.003 else "normal"
    if mid == "segment_dv_loaded":
        return "connector_high" if contact_loaded >= 0.003 else "cable_high" if cable >= 0.003 else "all_scaled" if loss >= 1.3 else "normal"
    if mid == "branch_i":
        if share is None:
            return None
        return "high" if share > band[1] + 0.03 else "in_band"
    if mid == "meter_v":
        v = 70.0
        return "mismatch" if abs((w["v_gain"] - 1) * v + w["v_off"]) >= 0.3 or abs(w["v_gain"] - 1) >= 0.01 else "match"
    if mid == "t_inv_trend":
        return "hotter" if loss >= 1.3 else "similar"
    return None


def score(sealed: Dict[str, Dict[str, Any]], eng: Dict[str, Dict[str, Any]], which: str) -> Dict[str, Any]:
    by: Dict[str, Dict[str, Any]] = {}
    cases: List[Dict[str, Any]] = []
    for cid, t in sorted(sealed.items()):
        e = eng.get(cid) or {}
        sc = t["scenario"]
        r: Dict[str, Any] = {"id": cid, "scenario": sc}
        if e.get("crash"):
            r["crash"] = e["crash"]
            cases.append(r)
            continue
        truth_b = _branch_truth(t)
        if which == "v1":
            flagged = sorted({x[2:] for x in (e or {}).get("relations_broken", []) if x.startswith("B_")}) if e else []
        else:
            flagged = ((e.get("relations") or {}).get("branch") or {}).get("flagged") or [] if e else []
        r["flagged"] = flagged
        r["truth"] = truth_b
        missing = t.get("missing")
        # false positives / misses (missing-signal and low-load cases scored for fabrication only)
        if not missing and sc != "low_load":
            r["fp_wheels"] = [w for w in flagged if w not in truth_b]
            big = {w: m for w, m in truth_b.items() if not (m in ("contact", "conductor") and next(f["size_ohm"] for f in t["faults"] if f["wheel"] == w and f["type"] == m) < 0.008)}
            r["missed"] = [w for w in big if w not in flagged and big[w] != "sensor_gain"]
            r["small_contact"] = [{"wheel": f["wheel"], "size_mohm": round(f["size_ohm"] * 1000, 1), "flagged": f["wheel"] in flagged}
                                  for f in t["faults"] if f["type"] in ("contact", "conductor") and f["size_ohm"] < 0.008]
        if which == "v2" and e:
            # fabrication
            rel = e.get("relations") or {}
            fab = []
            if missing in ("current", "wheel_v") or sc == "low_load":
                if (e.get("model") or {}).get("branches"):
                    fab.append("branch resistance reported")
                if any(f["relation"].startswith(("R_", "B_")) for f in e.get("findings") or []):
                    fab.append("branch finding reported")
                if (rel.get("branch") or {}).get("status") == "ok":
                    fab.append("branch relation marked ok")
            if missing == "current" or sc == "low_load":
                if (rel.get("pack") or {}).get("status") == "ok" or any(f["relation"] == "P" for f in e.get("findings") or []):
                    fab.append("pack resistance reported")
            if missing == "temp" and any(f["relation"] == "P" for f in e.get("findings") or []):
                fab.append("temperature comparison without temperature")
            if missing == "torque" and any((a.get("band_basis") == "torque") for a in e.get("assessments") or []):
                fab.append("torque-based share without torque")
            r["fabrication"] = fab
            # never "measured" resistance
            for f in e.get("findings") or []:
                if f["relation"].startswith("R_") and f.get("tier") != "model":
                    fab.append("resistance not labelled model-derived")
            # attribution and measurement
            r["attribution"] = []
            r["measurement"] = []
            for a in e.get("assessments") or []:
                w = a["wheel"]
                if w not in truth_b:
                    continue
                m = truth_b[w]
                ok = m in a["open"]
                r["attribution"].append({"wheel": w, "true": m, "kept": ok, "open": a["open"],
                                         "weakened": [c["id"] for c in a["candidates"] if c["status"] == "weakened"]})
                rec = a.get("recommended")
                if rec:
                    meas = next(x for x in a["measurements"] if x["id"] == rec)
                    o = _simulate_measurement(rec, w, t, a["band"])
                    pred = {c: _cls(meas, c) for c in a["open"]}
                    keeps = pred.get(m) == o if m in pred else False
                    elim = [c for c, pc in pred.items() if c != m and pc != o]
                    r["measurement"].append({"wheel": w, "recommended": rec, "outcome": o, "keeps_true": keeps, "eliminates": elim,
                                             "misleading": (m in pred and pred[m] != o)})
            # numerical accuracy of measured k (physical sensors only)
            kerr = []
            sensor_w = {f["wheel"] for f in t["faults"] if f["type"] in ("sensor_gain",)}
            for st, se in zip(t["snapshots"], e.get("snapshots") or []):
                if not st.get("k"):
                    continue
                for w, b in (se.get("branches") or {}).items():
                    if b.get("status") == "ok" and w not in sensor_w and st["k"].get(w):
                        kerr.append(abs(b["k_v_per_a"] - st["k"][w]) / st["k"][w])
            r["k_rel_err"] = kerr
            # coverage of the true branch resistance
            cov = []
            for w, mb in ((e.get("model") or {}).get("branches") or {}).items():
                rl = [s["r_loaded"][w] for s in t["snapshots"] if s.get("r_loaded")]
                if rl:
                    true_r = sum(rl) / len(rl)
                    cov.append({"wheel": w, "true_mohm": round(true_r * 1000, 2), "lo": round(mb["r_interval_ohm"][0] * 1000, 2), "hi": round(mb["r_interval_ohm"][1] * 1000, 2),
                                "inside": mb["r_interval_ohm"][0] <= true_r <= mb["r_interval_ohm"][1], "fault": truth_b.get(w)})
            r["coverage"] = cov
            # battery
            bat = []
            for st, se in zip(t["snapshots"], e.get("snapshots") or []):
                pk = se.get("pack") or {}
                if pk.get("status") == "ok":
                    lo, hi = st["r0_ohm"] * 0.85, (st["r0_ohm"] + st["r1_ohm"]) * 1.15
                    bat.append({"apparent": pk["apparent_r_ohm"], "r0": round(st["r0_ohm"], 5), "r0r1": round(st["r0_ohm"] + st["r1_ohm"], 5),
                                "inside": lo <= pk["apparent_r_ohm"] <= hi})
            r["battery"] = bat
            pf = next((f for f in e.get("findings") or [] if f["relation"] == "P"), None)
            if pf:
                v = pf["values"]
                soc_diff = abs((v.get("cold_soc") or 0) - (v.get("warm_soc") or 0)) if v.get("cold_soc") is not None else None
                r["confound"] = {"soc_diff": soc_diff, "flagged": "soc" in (v.get("confounds") or []), "should": soc_diff is not None and soc_diff >= 10}
            # sensor offset
            offs = [f for f in t["faults"] if f["type"] == "sensor_offset" and abs(f["offset_v"]) >= 0.5]
            if offs and not missing:
                got = ((e.get("relations") or {}).get("sensor") or {}).get("flagged") or []
                r["offset"] = [{"wheel": f["wheel"], "v": round(f["offset_v"], 2), "flagged": f["wheel"] in got} for f in offs]
            r["offset_fp"] = [w for w in (((e.get("relations") or {}).get("sensor") or {}).get("flagged") or [])
                              if not any(f["wheel"] == w and f["type"] in ("sensor_offset", "sensor_gain") for f in t["faults"])] if not missing else []
        cases.append(r)
    return summarise(cases, which)


def _cls(meas: Dict[str, Any], cand: str) -> Optional[str]:
    from_expected = {
        "segment_r": {"contact": "connector_high", "conductor": "cable_high"},
        "segment_dv_loaded": {"contact": "connector_high", "conductor": "cable_high", "inverter_draw": "all_scaled", "uneven_load": "all_scaled"},
        "branch_i": {"inverter_draw": "high", "uneven_load": "in_band"},
        "meter_v": {"sensor_gain": "mismatch"},
        "t_inv_trend": {"inverter_draw": "hotter"},
    }
    default = {"segment_r": "normal", "segment_dv_loaded": "normal", "branch_i": "in_band", "meter_v": "match", "t_inv_trend": "similar"}
    return from_expected.get(meas["id"], {}).get(cand, default.get(meas["id"]))


def summarise(cases: List[Dict[str, Any]], which: str) -> Dict[str, Any]:
    def pct(a: int, b: int) -> Optional[float]:
        return round(100 * a / b, 1) if b else None
    healthy = [c for c in cases if c["scenario"] in ("healthy_even", "healthy_uneven", "sensor_offset", "cold_battery")]
    fp = [c for c in healthy if c.get("fp_wheels")]
    fault_cases = [c for c in cases if "missed" in c and (c.get("missed") is not None)]
    big_total = sum(len([w for w, m in c["truth"].items() if m != "sensor_gain"]) - len([s for s in c.get("small_contact") or []]) for c in fault_cases)
    missed = sum(len(c["missed"]) for c in fault_cases)
    wrong_wheel = [c for c in cases if c["scenario"] not in ("healthy_even", "healthy_uneven", "sensor_offset", "cold_battery") and c.get("fp_wheels")]
    out: Dict[str, Any] = {"engine": which, "cases": len(cases), "crashes": [c["id"] for c in cases if c.get("crash")],
                           "false_positive": {"cases": len(fp), "of": len(healthy), "pct": pct(len(fp), len(healthy)),
                                              "by_scenario": {s: f"{sum(1 for c in fp if c['scenario'] == s)}/{sum(1 for c in healthy if c['scenario'] == s)}"
                                                              for s in ("healthy_even", "healthy_uneven", "sensor_offset", "cold_battery")}},
                           "missed": {"faults": missed, "of": big_total, "pct": pct(missed, big_total),
                                      "by_scenario": {s: f"{sum(len(c['missed']) for c in fault_cases if c['scenario'] == s)}" for s in
                                                      ("connector", "connector_intermittent", "conductor", "inverter_loss", "combined", "sensor_gain")}},
                           "wrong_wheel_in_fault_cases": len(wrong_wheel),
                           "small_contact_4_8_mohm": {"flagged": sum(1 for c in cases for s in c.get("small_contact") or [] if s["flagged"]),
                                                      "of": sum(len(c.get("small_contact") or []) for c in cases)}}
    if which == "v2":
        att = [a for c in cases for a in c.get("attribution") or []]
        meas = [m for c in cases for m in c.get("measurement") or []]
        kerr = sorted(x for c in cases for x in c.get("k_rel_err") or [])
        cov_fault = [x for c in cases if c["scenario"] in ("connector", "conductor", "combined") for x in c.get("coverage") or [] if x["fault"] in ("contact", "conductor")]
        cov_healthy = [x for c in cases if not c["scenario"].startswith("missing") for x in c.get("coverage") or [] if not x["fault"]]
        bat = [b for c in cases for b in c.get("battery") or []]
        conf = [c["confound"] for c in cases if c.get("confound")]
        offs = [o for c in cases for o in c.get("offset") or []]
        fab = [c["id"] + ": " + "; ".join(c["fabrication"]) for c in cases if c.get("fabrication")]
        out.update({
            "fabrication": {"cases": len(fab), "examples": fab[:5]},
            "attribution": {"kept_true": sum(1 for a in att if a["kept"]), "of": len(att), "pct": pct(sum(1 for a in att if a["kept"]), len(att)),
                            "errors": [a for a in att if not a["kept"]][:6],
                            "by_mechanism": {m: f"{sum(1 for a in att if a['true'] == m and a['kept'])}/{sum(1 for a in att if a['true'] == m)}"
                                             for m in ("contact", "conductor", "inverter_draw", "sensor_gain")},
                            "mean_open_candidates": round(sum(len(a["open"]) for a in att) / len(att), 2) if att else None},
            "k_accuracy": {"median_rel_err_pct": round(100 * kerr[len(kerr) // 2], 1) if kerr else None,
                           "p90_rel_err_pct": round(100 * kerr[int(len(kerr) * 0.9)], 1) if kerr else None, "n": len(kerr)},
            "coverage": {"fault_branches_inside": sum(1 for x in cov_fault if x["inside"]), "of": len(cov_fault), "pct": pct(sum(1 for x in cov_fault if x["inside"]), len(cov_fault)),
                         "healthy_branches_inside": sum(1 for x in cov_healthy if x["inside"]), "healthy_of": len(cov_healthy),
                         "fault_misses": [x for x in cov_fault if not x["inside"]][:6]},
            "battery": {"apparent_inside": sum(1 for b in bat if b["inside"]), "of": len(bat), "pct": pct(sum(1 for b in bat if b["inside"]), len(bat)),
                        "outside_examples": [b for b in bat if not b["inside"]][:5],
                        "soc_confound_flagged": f"{sum(1 for x in conf if x['should'] and x['flagged'])}/{sum(1 for x in conf if x['should'])}",
                        "soc_confound_false_flags": f"{sum(1 for x in conf if not x['should'] and x['flagged'])}/{sum(1 for x in conf if not x['should'])}"},
            "sensor_offset": {"flagged": sum(1 for o in offs if o["flagged"]), "of": len(offs), "pct": pct(sum(1 for o in offs if o["flagged"]), len(offs)),
                              "false_flags": sum(len(c.get("offset_fp") or []) for c in cases)},
            "measurement": {"good": sum(1 for m in meas if m["keeps_true"] and m["eliminates"]), "of": len(meas),
                            "pct": pct(sum(1 for m in meas if m["keeps_true"] and m["eliminates"]), len(meas)),
                            "misleading": sum(1 for m in meas if m["misleading"]),
                            "misleading_examples": [m for m in meas if m["misleading"]][:5],
                            "recommended": {k: sum(1 for m in meas if m["recommended"] == k) for k in sorted({m["recommended"] for m in meas})}},
        })
        crit = {
            "1_fabrication": out["fabrication"]["cases"] == 0,
            "2_false_positive_le_5pct": (out["false_positive"]["pct"] or 0) <= 5,
            "3_missed_le_10pct": (out["missed"]["pct"] or 0) <= 10,
            "4_attribution_ge_95pct": (out["attribution"]["pct"] or 0) >= 95,
            "5a_k_median_le_10pct": (out["k_accuracy"]["median_rel_err_pct"] or 99) <= 10,
            "5b_coverage_ge_90pct": (out["coverage"]["pct"] or 0) >= 90,
            "6a_confound_100pct": _frac_ok(out["battery"]["soc_confound_flagged"], 1.0),
            "6b_apparent_ge_90pct": (out["battery"]["pct"] or 0) >= 90,
            "7_offset_ge_90pct": (out["sensor_offset"]["pct"] or 0) >= 90,
            "8_measurement_ge_90pct": (out["measurement"]["pct"] or 0) >= 90,
        }
        out["criteria"] = crit
    out["case_results"] = cases
    return out


def _frac_ok(s: str, need: float) -> bool:
    a, b = s.split("/")
    return int(b) == 0 or int(a) / int(b) >= need


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=list(SPLITS))
    ap.add_argument("--out")
    ap.add_argument("--engine", default="v2", choices=["v1", "v2"])
    ap.add_argument("--engine-process", nargs=2, metavar=("LOGDIR", "OUT"))
    a = ap.parse_args()
    if a.engine_process:
        engine_main(a.engine_process[0], a.engine_process[1], a.engine)
        return
    sys.path.insert(0, HERE)
    import generate
    logs, sealed_dir = os.path.join(a.out, "logs"), os.path.join(a.out, "sealed")
    os.makedirs(logs, exist_ok=True)
    os.makedirs(sealed_dir, exist_ok=True)
    if not os.listdir(logs):
        for sc in generate.SCENARIOS:
            for seed in SPLITS[a.split]:
                lines, truth = generate.generate(sc, seed)
                cid = f"{sc}-{seed}"
                with open(os.path.join(logs, cid + ".txt"), "w") as f:
                    f.write("\n".join(lines) + "\n")
                with open(os.path.join(sealed_dir, cid + ".json"), "w") as f:
                    json.dump(truth, f)
    eng_out = os.path.join(a.out, f"engine_{a.engine}.json")
    subprocess.run([sys.executable, "-I", os.path.abspath(__file__), "--engine", a.engine, "--engine-process", logs, eng_out], check=True, cwd=logs)
    with open(eng_out) as f:
        eng = json.load(f)
    sealed = {}
    for name in os.listdir(sealed_dir):
        with open(os.path.join(sealed_dir, name)) as f:
            sealed[name[:-5]] = json.load(f)
    res = score(sealed, eng, a.engine)
    with open(os.path.join(a.out, f"score_{a.engine}.json"), "w") as f:
        json.dump(res, f, indent=1, default=str)
    show = {k: v for k, v in res.items() if k != "case_results"}
    print(json.dumps(show, indent=1, default=str))


if __name__ == "__main__":
    main()
