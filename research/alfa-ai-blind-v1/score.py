"""Score the stored results exactly as PREREGISTRATION.md defines. Reads results/*.json only; writes baseline.json and
baseline.md. Usage: python research/alfa-ai-blind-v1/score.py"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from split import split  # noqa: E402

SIGNALS = {
    "aileron": ["vehicle.attitude[roll]", "control.attitude_setpoint[roll]"],
    "rudder": ["vehicle.attitude[yaw]", "control.attitude_setpoint[yaw]", "vehicle.yaw_rate"],
    "elevator": ["vehicle.attitude[pitch]", "control.attitude_setpoint[pitch]"],
    "engine": ["propulsion.throttle", "vehicle.altitude", "vehicle.velocity", "vehicle.airspeed"],
}
STRICT = {"aileron": r"aileron", "rudder": r"rudder", "elevator": r"elevator", "engine": r"engine|motor|propulsion|thrust"}
AXIS = {"aileron": r"aileron|roll", "rudder": r"rudder|yaw", "elevator": r"elevator|pitch", "engine": r"engine|motor|propulsion|thrust|throttle|power"}
ANY_COMPONENT = r"aileron|rudder|elevator|engine|motor|propulsion|thrust|servo|actuator|sensor"
TOPIC = {"/failure_status/aileron": "aileron", "/failure_status/rudder": "rudder", "/failure_status/elevator": "elevator", "/failure_status/engines": "engine"}
WINDOW_TOOLS = {"window", "changes", "relation", "sequence", "events"}
FACT_KINDS = ("CHANGE", "TRACKING", "GAP", "CONSTANT", "EVENT")


def names_signal(text: str, comps) -> bool:
    return any(s in text for c in comps for s in SIGNALS[c])


def times(text: str):
    return [float(x) for x in re.findall(r"t=(-?[\d.]+)", text)]


def concerns(line: str, comps, onset: float) -> bool:
    return names_signal(line, comps) and any(t >= onset - 1 for t in times(line))


def call_range(step, end: float):
    a = step["input"]
    lo = a.get("start_s")
    hi = a.get("end_s")
    return (0.0 if lo is None else float(lo), end if hi is None else float(hi))


def _matches(name: str, signal: str) -> bool:
    """A tool's series argument (as the agent wrote it) against a component signal: same base, same scope if any."""
    base, _, scope = signal.partition("[")
    return name.startswith(base) and (not scope or scope.rstrip("]") in name)


def call_on_axis(step, comps) -> bool:
    a = step["input"]
    named = [x for k in ("series", "a", "b") for x in ([a[k]] if isinstance(a.get(k), str) else a.get(k) or [])]
    if not named:
        return step["tool"] in ("sequence", "events")
    return any(_matches(n, s) for n in named for c in comps for s in SIGNALS[c])


def coverage(ranges, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0
    segs = sorted((max(a, lo), min(b, hi)) for a, b in ranges if min(b, hi) > max(a, lo))
    tot, cur = 0.0, None
    for a, b in segs:
        if cur is None or a > cur[1]:
            if cur:
                tot += cur[1] - cur[0]
            cur = [a, b]
        else:
            cur[1] = max(cur[1], b)
    if cur:
        tot += cur[1] - cur[0]
    return tot / (hi - lo)


def score(rec: dict) -> dict:
    inv = rec["investigation"]
    res = inv.get("result") or {}
    sheet = rec["sheet"]["lines"]
    end = max((t for l in sheet for t in times(l)), default=0.0)
    steps = inv.get("steps") or []
    usage = inv.get("usage") or {}
    out = {"alias": rec["alias"], "folder": rec["folder"], "split": split(rec["folder"]), "mode": inv["mode"],
           "attempts": len(rec.get("attempts") or []), "status": res.get("status"), "headline": res.get("headline"),
           "tool_calls": len(steps), "model_calls": len(inv.get("model_calls") or []), "seconds": inv.get("seconds"),
           "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
           "cache_read_tokens": usage.get("cache_read_input_tokens"), "cost_usd": usage.get("cost_usd"),
           "dropped": len(inv.get("dropped") or []), "models": sorted({c.get("model") for c in inv.get("model_calls") or [] if c.get("model")})}
    lab = rec["labels"]
    comps = sorted({TOPIC[t] for t in lab["nonzero"]})
    out["components"], out["onset_s"] = comps, lab["onset_s"]
    if "no_ground_truth" in rec["folder"]:
        out["kind"] = "unlabelled"
        return out
    if inv["mode"] != "agent":
        out["kind"] = "infrastructure_failure"
        return out
    text_all = " ".join([res.get("headline") or "", res.get("explanation") or ""] +
                        [f"{h.get('area', '')} {h.get('claim', '')}" for h in res.get("hypotheses") or [] if h.get("status") == "leading"]).lower()
    if not comps:
        out["kind"] = "healthy"
        out["fp_strict"] = res.get("status") == "cause_probable"
        lead = [f"{h.get('area', '')} {h.get('claim', '')}".lower() for h in res.get("hypotheses") or [] if h.get("status") == "leading"]
        out["fp_flag"] = any(re.search(ANY_COMPONENT, x) for x in lead)
        return out
    out["kind"] = "faulty"
    onset = lab["onset_s"]
    out["G"] = any(l.startswith(FACT_KINDS) and concerns(l, comps, onset) for l in sheet)
    out["R"] = any(l.startswith("ANOMALY") and concerns(l, comps, onset) for l in sheet)
    win_steps = [s for s in steps if s["tool"] in WINDOW_TOOLS]
    out["coverage"] = round(coverage([call_range(s, end) for s in win_steps], onset, end), 3)
    out["T"] = any(call_on_axis(s, comps) and coverage([call_range(s, end)], onset, end) * (end - onset) >= 1.0 for s in win_steps)
    out["evidence_recall"] = any(1 <= e.get("line", 0) <= len(sheet) and concerns(sheet[e["line"] - 1], comps, onset) for e in res.get("evidence") or [])
    ok = res.get("status") != "no_failure_found"
    out["D_strict"] = ok and any(re.search(STRICT[c], text_all) for c in comps)
    out["D_axis"] = ok and any(re.search(AXIS[c], text_all) for c in comps)
    if not out["D_axis"]:
        out["failure_stage"] = ("evidence generation" if not out["G"] else
                                ("tool selection" + (" (anomaly ranking also missed it)" if not out["R"] else "")) if not out["T"] else "reasoning")
    return out


def rate(rows, key):
    xs = [r[key] for r in rows if key in r]
    return f"{sum(xs)}/{len(xs)}" if xs else "–"


def main() -> int:
    recs = [json.loads(p.read_text()) for p in sorted((HERE / "results").glob("flight-*.json")) if not p.name.endswith(".error.json")]
    errors = [json.loads(p.read_text()) for p in sorted((HERE / "results").glob("*.error.json"))]
    rows = [score(r) for r in recs]
    faulty = [r for r in rows if r["kind"] == "faulty"]
    healthy = [r for r in rows if r["kind"] == "healthy"]
    infra = [r for r in rows if r["kind"] == "infrastructure_failure"]
    summary = {}
    for name, subset in (("all", faulty), ("development", [r for r in faulty if r["split"] == "development"]),
                         ("validation", [r for r in faulty if r["split"] == "validation"])):
        summary[name] = {k: rate(subset, k) for k in ("G", "R", "T", "evidence_recall", "D_strict", "D_axis")}
        cov = [r["coverage"] for r in subset]
        summary[name]["mean_coverage"] = round(sum(cov) / len(cov), 3) if cov else None
    summary["healthy"] = {"fp_strict": rate(healthy, "fp_strict"), "fp_flag": rate(healthy, "fp_flag")}
    stages = {}
    for r in faulty:
        if r.get("failure_stage"):
            stages[r["failure_stage"]] = stages.get(r["failure_stage"], 0) + 1
    summary["failure_stages"] = stages
    ran = [r for r in rows if r["mode"] == "agent"]
    def med(k):
        xs = sorted(r[k] for r in ran if isinstance(r.get(k), (int, float)))
        return xs[len(xs) // 2] if xs else None
    summary["cost"] = {k: med(k) for k in ("tool_calls", "model_calls", "seconds", "input_tokens", "output_tokens", "cache_read_tokens")}
    summary["cost"]["total_cost_usd"] = round(sum(r["cost_usd"] for r in ran if isinstance(r.get("cost_usd"), (int, float))), 2) if any(isinstance(r.get("cost_usd"), (int, float)) for r in ran) else None
    summary["counts"] = {"flights": len(rows) + len(errors), "faulty_scored": len(faulty), "healthy_scored": len(healthy),
                         "infrastructure_failures": len(infra), "errors": len(errors), "unlabelled": sum(r["kind"] == "unlabelled" for r in rows)}
    (HERE / "baseline.json").write_text(json.dumps({"summary": summary, "rows": rows, "errors": errors}, indent=1))
    L = ["# ALFA blind evaluation v1 — baseline (frozen at e9bc6ce)", "",
         "Preregistered in PREREGISTRATION.md before any run. Every flight is listed, failures included.", "",
         f"Flights: {summary['counts']}", "",
         "| Set | G evidence generated | R anomaly ranked | T tool on the fault window | Evidence recall | D-strict | D-axis | Mean window coverage |",
         "|---|---|---|---|---|---|---|---|"]
    for k in ("all", "development", "validation"):
        s = summary[k]
        L.append(f"| {k} | {s['G']} | {s['R']} | {s['T']} | {s['evidence_recall']} | {s['D_strict']} | {s['D_axis']} | {s['mean_coverage']} |")
    L += ["", f"Healthy flights: FP-strict {summary['healthy']['fp_strict']}, FP-flag {summary['healthy']['fp_flag']}.", "",
          f"Failure stages (faulty flights that miss D-axis): {stages}", "",
          f"Median per flight: {summary['cost']}", "",
          "| Flight | Folder | Split | Kind | Components | Onset s | G | R | T | Cov | Recall | D-strict | D-axis | Stage | Status | Tools | s | Headline |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    yn = lambda v: "" if v is None else ("yes" if v else "no")  # noqa: E731
    for r in rows:
        L.append(f"| {r['alias']} | {r['folder'].replace('carbonZ_', '')} | {r['split']} | {r['kind']} | {','.join(r['components'])} | {r['onset_s']} | "
                 f"{yn(r.get('G'))} | {yn(r.get('R'))} | {yn(r.get('T'))} | {r.get('coverage', '')} | {yn(r.get('evidence_recall'))} | {yn(r.get('D_strict'))} | "
                 f"{yn(r.get('D_axis'))} | {r.get('failure_stage', '') or ('FP' if r.get('fp_strict') else ('flag' if r.get('fp_flag') else ''))} | {r.get('status')} | "
                 f"{r['tool_calls']} | {r.get('seconds')} | {(r.get('headline') or '').replace('|', '/')[:90]} |")
    for e in errors:
        L.append(f"| {e['alias']} | {e['folder'].replace('carbonZ_', '')} | | error | | | | | | | | | | | | | | {e['error'][:90]} |")
    (HERE / "baseline.md").write_text("\n".join(L) + "\n")
    print("\n".join(L[:16]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
