"""Known-answer evaluation of the investigation agent (SYNTHETIC cases; see backend/app/compute/evalcases.py).

Runs a real model through every question: clock alignment, units, a false premise, an unconfirmed cause, a
cross-run comparison and a same-clock timing order. Each answer is graded from its structured report:
correct +1, honest "cannot tell" 0, wrong -2 (a confident wrong answer costs more than abstaining).

Model: the agent's configured backend (ANTHROPIC_API_KEY, or VEYRA_AGENT_BACKEND=claude-code for the local CLI).
Usage: python scripts/validation/agent_evals.py [out.json] [--repeat N] [--only id,id]
Synthetic data only: no private data is sent to the model.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.agent.llm import from_environment  # noqa: E402
from app.agent.runner import AgentError, run_agent  # noqa: E402
from app.compute import evalcases, sandbox  # noqa: E402
from app.store.sqlite_store import CaseStore  # noqa: E402


def main(argv: list) -> int:
    out = next((a for a in argv if a.endswith(".json")), None)
    repeat = int(argv[argv.index("--repeat") + 1]) if "--repeat" in argv else 1
    only = set(argv[argv.index("--only") + 1].split(",")) if "--only" in argv else None
    model = from_environment()
    if not model.available():
        print("No model configured: set ANTHROPIC_API_KEY, or VEYRA_AGENT_BACKEND=claude-code with Claude Code installed.")
        return 2
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        store = CaseStore(Path(tmp) / "eval.db")
        ids = evalcases.build(store)
        for q in evalcases.QUESTIONS:
            if only and q["id"] not in only:
                continue
            for attempt in range(repeat):
                started = time.time()
                try:
                    entry = run_agent(store, ids["timing"], model=model, question=q["question"])
                    payload = entry["payload"]
                    grade = evalcases.score(q, payload)
                    report = payload["report"]
                    row = {"id": q["id"], "kind": q["kind"], "attempt": attempt + 1, **grade, "premise": (report.get("premise") or {}).get("status"),
                           "answer": (report.get("answer") or {}).get("text"), "truth": q["truth"],
                           "tools": [c["tool"] for c in payload["tool_calls"]], "computations": len(payload["computations"]),
                           "citations_valid": payload["citations_valid"], "seconds": round(time.time() - started, 1), "model": payload["model"]}
                except AgentError as error:
                    row = {"id": q["id"], "kind": q["kind"], "attempt": attempt + 1, "score": -2, "grade": "failed", "why": str(error)[:300],
                           "truth": q["truth"], "seconds": round(time.time() - started, 1)}
                rows.append(row)
                print(f"{row['id']:22} {row['grade']:9} {row['score']:+d}  {row.get('why', '')[:90]}", flush=True)
        store.close()
    total = sum(r["score"] for r in rows)
    summary = {"model": getattr(model, "model", "?"), "questions": len(rows), "score": total, "max_score": len(rows),
               "correct": sum(r["grade"] == "correct" for r in rows), "abstained": sum(r["grade"] == "abstained" for r in rows),
               "wrong": sum(r["grade"] in ("wrong", "failed") for r in rows), "sandbox": sandbox.isolation()["label"],
               "data": "SYNTHETIC evaluation cases (backend/app/compute/evalcases.py)", "rows": rows,
               "run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    print(f"\nScore {total} / {len(rows)}: {summary['correct']} correct, {summary['abstained']} abstained, {summary['wrong']} wrong")
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
