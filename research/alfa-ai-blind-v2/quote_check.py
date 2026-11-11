"""POST-HOC (not preregistered), no model calls: can a reader check what each report quotes?
For the model-alone arm, rebuild the exact table the model was given and test every evidence item: is the quoted text
verbatim on the cited line (after whitespace normalisation), or anywhere in the table? For Veyra v2, the same test against
its evidence sheet (the product's verifier already enforced it during the run). Usage: PYTHONPATH=backend python quote_check.py <processed dir>"""
import json, shutil, sys, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "backend")); sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent / "alfa-ai-blind-v1"))
import run2
from run import REQUEST

def norm(s): return " ".join(str(s).split())

def main(processed: Path):
    from app.agent.recording_tools import RecordingTools
    from app.ingest import bags
    from app.store.sqlite_store import CaseStore
    from app.workflows.debrief import debrief_recording
    out = {"alone": [], "veyra": []}
    folders = sorted(p for p in processed.iterdir() if p.is_dir() and any(p.glob("*.bag")))
    for i, folder in enumerate(folders):
        alias = f"flight-{i + 1:02d}"
        a = json.loads((HERE / "results-alone" / f"{alias}.json").read_text())
        v = json.loads((HERE / "results-veyra" / f"{alias}.json").read_text())
        tmp = Path(tempfile.mkdtemp()); bag = tmp / f"{alias}.bag"; shutil.copyfile(next(folder.glob("*.bag")), bag)
        store = CaseStore(tmp / "e.db")
        cid = debrief_recording(store, bag, bags.suggest(bags.inspect(bag)), machine_id=alias.replace("flight", "UAV"), title=f"{alias}: control fault check",
                                actor="Evaluation", source_name=bag.name, report={"stated_reason": REQUEST})["case_id"]
        prompt = f"Request: {REQUEST}\n\n{run2.table(RecordingTools(store, cid).rec)}"
        assert len(prompt) == a["prompt_chars"], (alias, len(prompt), a["prompt_chars"])   # the same input the model saw
        store.close(); shutil.rmtree(tmp, ignore_errors=True)
        lines = prompt.split("\n")
        for arm, rec, src in (("alone", a, lines), ("veyra", v, v["sheet"]["lines"])):
            ev = (rec["investigation"]["result"] or {}).get("evidence") or []
            whole = norm("\n".join(src))
            row = {"alias": alias, "items": len(ev), "on_cited_line": 0, "somewhere": 0, "nowhere": 0}
            for e in ev:
                q = norm(e.get("quote", ""))
                try: n = int(float(e.get("line") or 0))
                except (TypeError, ValueError): n = 0
                # the model-alone prompt has 3 lines before the first table row; accept the cited line +-3
                near = " ".join(norm(src[j]) for j in range(max(0, n - 4), min(len(src), n + 3)))
                if q and q in near: row["on_cited_line"] += 1
                elif q and q in whole: row["somewhere"] += 1
                else: row["nowhere"] += 1
            out[arm].append(row)
    summary = {}
    for arm, rows in out.items():
        t = sum(r["items"] for r in rows)
        summary[arm] = {"reports": len(rows), "evidence_items": t, "verbatim_on_cited_line": sum(r["on_cited_line"] for r in rows),
                        "verbatim_elsewhere": sum(r["somewhere"] for r in rows), "not_in_the_data_as_quoted": sum(r["nowhere"] for r in rows),
                        "reports_with_an_uncheckable_quote": sum(1 for r in rows if r["nowhere"]), "reports_with_no_evidence": sum(1 for r in rows if not r["items"])}
    (HERE / "quote_check.json").write_text(json.dumps({"note": "post-hoc, not preregistered", "summary": summary, "rows": out}, indent=1))
    print(json.dumps(summary, indent=1))

main(Path(sys.argv[1]))
