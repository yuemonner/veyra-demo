"""Blind evaluation of evidence discovery + the signal-chain layer on ALFA (docs/research/DISCOVERY_CAUSAL_EVAL.md).

Usage: python scripts/research/discovery_eval.py <folder of ALFA processed bags> <alfa_results_v3.json> <out.json>

Phase 1 reads only Veyra's stored detections (truth fields stripped on load), runs the product code, writes predictions
and their SHA-256. Phase 2 reads the labels and scores.
"""
import copy
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.discovery import discover  # noqa: E402
from app.engine import mechanisms  # noqa: E402
from app.ingest import bags  # noqa: E402
from app.machines import profiles  # noqa: E402

START_TOPICS = ["/mavros/rc/out", "/mavros/nav_info/roll", "/mavros/nav_info/pitch", "/mavros/nav_info/yaw", "/mavros/nav_info/airspeed",
                "/mavros/vfr_hud", "/mavros/state"]
DOWNSTREAM = ("output_path", "physical_response", "sensor_error")


def recording_start(path: Path) -> float:
    """The benchmark's start: the earliest record of the topics it imported (rostopic CSV %time = bag record time)."""
    rec = bags.open_recording(path)
    try:
        return min(m.t_ns for m in _first_of_each(rec)) / 1e9
    finally:
        rec.close()


def _first_of_each(rec):
    seen = {}
    for m in bags.iter_messages(rec, [t for t in START_TOPICS if t in rec.topics], keep=lambda topic, t: topic not in seen):
        seen.setdefault(m.topic, m)
        if len(seen) == len([t for t in START_TOPICS if t in rec.topics]):
            break
    return seen.values()


def run_like(det: dict, start: float) -> dict:
    v, trk = det["veyra"], det.get("tracking_departure")
    findings = {"actuator_outputs": {"findings": []}, "tracking": {"findings": []}}
    if v.get("rule"):
        findings["actuator_outputs"]["findings"].append({"rule": v["rule"], "channel": v["channel"], "t_start": start + v["onset_s"], "text": v.get("text") or ""})
    if trk:
        findings["tracking"]["findings"].append({"axis": trk["axis"], "t_start": start + trk["onset_s"], "text": f"{trk['axis']} stopped following its commands"})
    return {"findings": findings}


def without_parameters(disc: dict) -> dict:
    d = copy.deepcopy(disc)
    for inc in d["incidents"]:
        inc["facts"] = [f for f in inc.get("facts", []) if f["kind"] != "parameter_reported"]
    d["inventory"]["roles_present"] = [r for r in d["inventory"]["roles_present"] if r != "controller_parameters"]
    return d


def statuses(assessment: dict) -> dict:
    if not assessment.get("applicable"):
        return {}
    ev = assessment["events"][0]
    return {m["id"]: m["status"] for m in ev["mechanisms"]} | {"_lead_point": ev["lead"]["point"],
                                                                "_consequences": [m["key"] for m in ev["members"] if m["relation"] == "consequence"]}


def main(bag_dir: str, results_path: str, out_path: str) -> int:
    chain = profiles.load_all()["fixed_wing_uav"]["signal_chain"]
    raw = json.load(open(results_path))["flights"]
    blind = [{k: f[k] for k in ("flight", "veyra", "tracking_departure")} for f in raw]  # phase 1 sees no truth
    predictions = []
    for det in blind:
        name = det["flight"]
        bag = Path(bag_dir) / name / f"{name}.bag"
        if not det["veyra"].get("rule") and not det.get("tracking_departure"):
            predictions.append({"flight": name, "incident": None})
            continue
        start = recording_start(bag)
        run = run_like(det, start)
        lead = min([f for fam in run["findings"].values() for f in fam["findings"]], key=lambda f: f["t_start"])
        incident = {"id": "first-finding", "t": lead["t_start"], "label": lead.get("text", ""), "channel": lead.get("channel")}
        disc = discover(bag, [incident], chain)
        assert not set(disc["inventory"]["read_by_probes"]) & {e["stream"] for e in disc["inventory"]["excluded"]}
        full = mechanisms.assess(run, chain, disc)
        abl = mechanisms.assess(run, chain, without_parameters(disc))
        inc = disc["incidents"][0]
        predictions.append({"flight": name, "incident": {"rule": det["veyra"].get("rule"), "channel": det["veyra"].get("channel"),
                                                         "onset_s": round(lead["t_start"] - start, 2)},
                            "excluded_streams": [e["stream"] for e in disc["inventory"]["excluded"]],
                            "not_understood": [s["stream"] for s in disc["inventory"]["not_understood"]],
                            "controller_facts_near_onset": [f["text"] + f" ({f['t_rel_s']:+g} s)" for f in inc.get("facts", [])
                                                            if f["point"] == "controller" and f.get("near_onset")],
                            "operator": inc.get("operator"), "gaps": [g["missing"] for g in disc["gaps"]],
                            "summary": full["events"][0]["summary"] if full.get("applicable") else None,
                            "mechanisms": statuses(full), "mechanisms_without_parameters": statuses(abl)})
        print(f"{name:<75} {predictions[-1]['mechanisms'].get('controller_behaviour')}")
    blob = json.dumps(predictions, sort_keys=True).encode()
    sealed = hashlib.sha256(blob).hexdigest()
    Path(out_path).with_suffix(".predictions.json").write_bytes(blob)
    print("predictions sha256", sealed)

    # ---- phase 2: labels
    truth = {f["flight"]: f for f in raw}

    def score(key):
        rows = [p for p in predictions if p["incident"] and truth[p["flight"]]["truth"]["kinds"] and truth[p["flight"]]["outcome"] == "detected"]
        get = lambda p: p[key]  # noqa: E731
        return {"incidents": len(rows),
                "M1_controller_supported": sum(1 for p in rows if get(p).get("controller_behaviour") == "supported"),
                "M2_no_other_supported": sum(1 for p in rows if not any(s == "supported" for k, s in get(p).items() if not k.startswith("_") and k != "controller_behaviour")),
                "M3_downstream_all_unlikely": sum(1 for p in rows if all(get(p).get(k) == "unlikely" for k in DOWNSTREAM)),
                "M4_operator_not_supported": sum(1 for p in rows if get(p).get("operator_command") != "supported"),
                "controller_status_counts": {s: sum(1 for p in rows if get(p).get("controller_behaviour") == s) for s in ("supported", "possible", "cannot_tell", "unlikely")},
                "per_flight": [{"flight": p["flight"], "truth": truth[p["flight"]]["truth"]["kinds"], **{k: v for k, v in get(p).items()}} for p in rows]}
    other = [{"flight": p["flight"], "outcome": truth[p["flight"]]["outcome"], "truth": truth[p["flight"]]["truth"]["kinds"],
              "mechanisms": p["mechanisms"], "controller_facts_near_onset": p["controller_facts_near_onset"]}
             for p in predictions if p["incident"] and not (truth[p["flight"]]["truth"]["kinds"] and truth[p["flight"]]["outcome"] == "detected")]
    out = {"protocol": "docs/research/DISCOVERY_CAUSAL_EVAL.md", "predictions_sha256": sealed,
           "with_discovery": score("mechanisms"), "ablation_without_parameter_records": score("mechanisms_without_parameters"),
           "other_incidents": other, "flights_without_incident": sum(1 for p in predictions if not p["incident"]),
           "predictions": predictions}
    Path(out_path).write_text(json.dumps(out, indent=1))
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "per_flight"} for k, v in out.items() if k.startswith(("with", "ablation"))}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
