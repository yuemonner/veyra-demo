"""Run a live AI model as the step judge, under the evidence verifier, on the benchmark's own frozen judgments.

    VEYRA_AGENT_BACKEND=claude-code python research/investigation_benchmark/ai_judge_run.py results/<case>.json [v2/results/<case>.json ...]

For every engineer step that tests a hypothesis, the model gets only the facts Veyra had at that decision point (the
frozen state stored in the results file) and must submit cited claims. The verifier accepts or rejects them (up to two
attempts). Recorded per claim: text, cited facts, tier, verdict. Also flagged, without rejecting: numbers in a claim that
appear in none of the facts it cites (a cheap check for invented values; the verifier does not read meaning).
Writes ai_judge_<case>.json next to each results file. The sealed truth is never sent to the model.
"""
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

from app.agent import llm  # noqa: E402
from app.agent import step_judge as J  # noqa: E402

NUM = re.compile(r"\d+(?:\.\d+)?")


def numbers_not_in_facts(claim, facts):
    cited = " ".join(facts[f]["text"] for f in claim.get("fact_ids") or [] if f in facts)
    have = set(NUM.findall(cited))
    return [n for n in NUM.findall(claim.get("text", "")) if n not in have and not re.fullmatch(r"[0-9]", n)]


class Recorder:
    """Passes calls through and records which model actually answered and what it cost."""
    def __init__(self, m):
        self.m, self.model, self.provider, self.served, self.calls = m, getattr(m, "model", "?"), getattr(m, "provider", "?"), set(), 0

    def available(self):
        return self.m.available()

    def create(self, **kw):
        r = self.m.create(**kw)
        self.calls += 1
        if r.get("model_id"):
            self.served.add(r["model_id"])
        return r


def run(path, model):
    r = json.load(open(path, encoding="utf-8"))
    out = {"case": r["case"], "source": os.path.relpath(path, HERE), "model": getattr(model, "model", "?"), "provider": getattr(model, "provider", "?"), "steps": []}
    for p in r["points"]:
        if p.get("void"):
            continue
        st = p["state"]
        facts = {f["id"]: f for f in st["facts"]}
        for j in p["judgments"]:
            if not j["targets"]:
                continue
            t = time.time()
            try:
                res = J.ai_interpret(model, j, st)
            except Exception as e:  # noqa: BLE001
                res = {"status": "error", "error": str(e)[:300], "interpretation": None, "rejected": []}
            claims = ((res.get("interpretation") or {}).get("claims")) or []
            out["steps"].append({"cutoff": p["cutoff"], "step": j["id"], "check": j["check"], "status": res["status"], "attempts": len(res.get("rejected") or []) + (1 if res["status"] == "accepted" else 0),
                                 "rejections": res.get("rejected"), "error": res.get("error"), "seconds": round(time.time() - t, 1),
                                 "claims": [dict(c, numbers_not_in_cited_facts=numbers_not_in_facts(c, facts)) for c in claims],
                                 "eliminates": ((res.get("interpretation") or {}).get("eliminates")) or []})
            print(p["cutoff"], j["id"], res["status"], flush=True)
    s = out["steps"]
    out["summary"] = {"steps": len(s), "accepted": sum(x["status"] == "accepted" for x in s), "accepted_first_try": sum(x["status"] == "accepted" and x["attempts"] == 1 for x in s),
                      "rejected": sum(x["status"] == "rejected" for x in s), "errors": sum(x["status"] == "error" for x in s),
                      "claims": sum(len(x["claims"]) for x in s), "claims_with_unmatched_numbers": sum(1 for x in s for c in x["claims"] if c["numbers_not_in_cited_facts"]),
                      "rejection_reasons": [p for x in s for rr in (x["rejections"] or []) for p in rr]}
    out["served_by"] = sorted(model.served) if hasattr(model, "served") else None
    out["model_calls"] = getattr(model, "calls", None)
    dst = os.path.join(os.path.dirname(path), f"ai_judge_{r['case']}.json")
    json.dump(out, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(dst, json.dumps(out["summary"], ensure_ascii=False))


if __name__ == "__main__":
    m = Recorder(llm.from_environment())
    if not m.available():
        raise SystemExit("no model available: set VEYRA_AGENT_BACKEND=claude-code (signed-in Claude Code) or an API key")
    for p in sys.argv[1:]:
        run(os.path.abspath(p), m)
