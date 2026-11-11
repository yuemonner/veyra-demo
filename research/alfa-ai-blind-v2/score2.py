"""Score all arms with the v2 measure (PREREGISTRATION.md). Reads stored results only; writes results.json and results.md."""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "alfa-ai-blind-v1"))
from split import split  # noqa: E402

TOPIC = {"/failure_status/aileron": "aileron", "/failure_status/rudder": "rudder", "/failure_status/elevator": "elevator", "/failure_status/engines": "engine"}
STRICT = {"aileron": r"aileron", "elevator": r"elevator", "rudder": r"rudder", "engine": r"engine|motor|propulsion|thrust|throttle"}
AXIS = {"aileron": r"roll", "elevator": r"pitch", "rudder": r"yaw", "engine": r"$^"}
CH = {"aileron": (0, 4, 5), "elevator": (1,), "rudder": (3,), "engine": (2,)}
ARMS = {"Veyra v1 (frozen baseline)": HERE.parent / "alfa-ai-blind-v1" / "results", "Veyra v2": HERE / "results-veyra", "Model alone": HERE / "results-alone"}


def ch_rx(comp: str) -> str:
    return "|".join(rf"ch\[{n}\]|\bch ?{n}\b|channel ?{n}\b" for n in CH[comp])


def times(text: str):
    return [float(x) for x in re.findall(r"(?:t ?[=≈~] ?|\bat |\bfrom |\bafter |\bby )~?(\d+(?:\.\d+)?)|(?<![\w.\[])(\d+(?:\.\d+)?) ?s\b", text) for x in x if x]


def score(rec: dict) -> dict:
    inv = rec["investigation"]
    res = inv.get("result") or {}
    lab = rec["labels"]
    comps = sorted({TOPIC[t] for t in lab["nonzero"]})
    usage = inv.get("usage") or {}
    row = {"alias": rec["alias"], "folder": rec["folder"], "split": split(rec["folder"]), "components": comps, "onset_s": lab["onset_s"],
           "status": res.get("status"), "headline": res.get("headline"), "tool_calls": len(inv.get("steps") or []), "seconds": inv.get("seconds"),
           "cost_usd": usage.get("cost_usd"), "output_tokens": usage.get("output_tokens")}
    if "no_ground_truth" in rec["folder"]:
        row["kind"] = "unlabelled"
        return row
    if not comps:
        row.update(kind="healthy", false_alarm=res.get("status") == "cause_probable", clean=res.get("status") == "no_failure_found")
        return row
    lead = [f"{h.get('area', '')}: {h.get('claim', '')}" for h in res.get("hypotheses") or [] if h.get("status") == "leading"]
    # statements: each sentence of the headline, explanation and leading hypotheses; each evidence line with its note; each event
    statements = []
    for chunk in [res.get("headline") or "", res.get("explanation") or ""] + lead:
        statements += [x for x in re.split(r"(?<=[a-z\)\]])[.;](?:\s|$)|\n", chunk) if x.strip()]
    statements += [f"{e.get('quote', '')} {e.get('shows', '')}" for e in res.get("evidence") or []]
    statements += [str(e.get("what", "")) + " " + str(e.get("why", "")) for e in res.get("events") or []]
    on = lab["onset_s"]

    def hit(rx_for) -> bool:
        return any(any(on - 3 <= t <= on + 15 for t in times(st.lower())) and any(re.search(rx_for(c), st.lower()) for c in comps) for st in statements)

    strict = hit(lambda c: STRICT[c] + "|" + ch_rx(c))
    named = strict or hit(lambda c: AXIS[c])
    ok = res.get("status") != "no_failure_found"
    row.update(kind="faulty", found=bool(ok and named), found_strict=bool(ok and strict))
    return row


def main() -> int:
    out, L = {}, ["# ALFA evaluation v2 — results", "", "Measure and arms: PREREGISTRATION.md. Every flight is listed below, failures included.", ""]
    table = ["| Arm | FOUND all | FOUND development | FOUND validation | FOUND-strict all | Healthy: false alarm | Healthy: clean | Median s | Runs |", "|---|---|---|---|---|---|---|---|---|"]
    per = {}
    for arm, folder in ARMS.items():
        rows = [score(json.loads(p.read_text())) for p in sorted(folder.glob("flight-*.json"))]
        infra = len(list((folder / "infrastructure_failures").glob("*.json"))) if (folder / "infrastructure_failures").exists() else 0
        f = [r for r in rows if r["kind"] == "faulty"]
        h = [r for r in rows if r["kind"] == "healthy"]
        frac = lambda xs, k: f"{sum(r[k] for r in xs)}/{len(xs)}"  # noqa: E731
        secs = sorted(r["seconds"] for r in rows if isinstance(r.get("seconds"), (int, float)))
        out[arm] = {"rows": rows, "infrastructure_failure_records": infra}
        table.append(f"| {arm} | {frac(f, 'found')} | {frac([r for r in f if r['split'] == 'development'], 'found')} | {frac([r for r in f if r['split'] == 'validation'], 'found')} | "
                     f"{frac(f, 'found_strict')} | {frac(h, 'false_alarm')} | {frac(h, 'clean')} | {secs[len(secs) // 2] if secs else ''} | {len(rows)} |")
        for r in rows:
            per.setdefault(r["alias"], {})[arm] = r
    L += table + ["", "| Flight | Split | Fault | Onset s | " + " | ".join(ARMS) + " |", "|---|---|---|---|" + "---|" * len(ARMS)]
    for alias in sorted(per):
        any_r = next(iter(per[alias].values()))
        cells = []
        for arm in ARMS:
            r = per[alias].get(arm)
            if not r:
                cells.append("not run")
            elif r["kind"] == "faulty":
                cells.append(("FOUND" if r["found"] else "missed") + f" · {(r['headline'] or '')[:70]}".replace("|", "/"))
            elif r["kind"] == "healthy":
                cells.append(("FALSE ALARM" if r["false_alarm"] else "clean" if r["clean"] else "no cause claimed") + f" · {(r['headline'] or '')[:60]}".replace("|", "/"))
            else:
                cells.append((r["headline"] or "")[:70].replace("|", "/"))
        L.append(f"| {alias} | {any_r['split']} | {','.join(any_r['components']) or any_r['kind']} | {any_r['onset_s'] or ''} | " + " | ".join(cells) + " |")
    (HERE / "results.json").write_text(json.dumps(out, indent=1))
    (HERE / "results.md").write_text("\n".join(L) + "\n")
    print("\n".join(L[:4] + table))
    return 0


if __name__ == "__main__":
    sys.exit(main())
