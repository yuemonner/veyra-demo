"""Case-page correctness on the two showcase cases: the synthetic shuttle (Chinese) and the real public micro-ROS case
(English). Builds each case's canonical state and reports: what is current and what is history, what each explanation's
status rests on, which statements are shown as reported rather than established, the hazard of every proposed check,
and whether each engineer input changed the next action.

For micro-ROS it then adds what the public thread later reported (the reporter: "it was indeed a cache coherence issue";
a teammate's D-cache clean patch; a second user: the patch works on a Nucleo F7) as an engineer's closure, to show that
a fix reported to work stays engineer-reported: there is no post-fix data in the case.

    python research/case_state_eval/run.py      -> results.json, prints a summary
"""
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

from app.agent import case_state as CS  # noqa: E402
from app.agent import experiment_safety as S  # noqa: E402
from app.agent import investigator as inv  # noqa: E402
from app.agent import discussion  # noqa: E402
from app.store.sqlite_store import CaseStore  # noqa: E402
from app.workflows import public_samples  # noqa: E402

THREAD_OUTCOME = {
    "investigation_status": "cause_confirmed", "cause_domain": "network", "confirmed_by": "fix_resolved_it",
    "cause": "STM32H7 LwIP Ethernet port does not clean the D-cache before Ethernet DMA transmits a frame; the DMA sends stale (zero) bytes",
    "evidence_note": "Public thread (micro_ros_stm32cubemx_utils #139): the reporter wrote that it was indeed a cache coherence issue; a teammate's "
                     "commit adds SCB_CleanDCache_by_Addr on the Tx buffers in low_level_output(); another user reported the patch works on a Nucleo F7.",
    "resolution_status": "resolved", "fix": "Clean the D-cache (32-byte aligned) for Tx buffers before Ethernet DMA", "fix_held": "held",
}


def summary(s):
    c = s["current"] or {}
    return {
        "state_version": s["state_version"]["id"],
        "current_from": c.get("from"), "status": c.get("status"), "counts": c.get("counts"),
        "explanations": [{"id": h["id"], "area": h.get("area"), "status": h["status"], "role": h.get("role"), "status_basis": h["status_basis"],
                          "standing": h["standing"]} for h in c.get("hypotheses") or []],
        "next_action": (c.get("next_action") or {}).get("action"),
        "next_action_safety": {k: (c.get("next_action") or {}).get("safety", {}).get(k) for k in ("classes", "worst", "requires_review", "review_by", "safer_alternative")},
        "history": [{k: h[k] for k in ("source", "n", "status", "leading")} | {"superseded_by": f"{h['superseded_by']['source']} {h['superseded_by']['n']}"} for h in s["history"]],
        "engineer_inputs": [{"kind": x["kind"], "round": x.get("round"), "source": x["source"], "text": x["text"][:160], "verdict": x["verdict"],
                             "genuine_change": x.get("genuine_change"), "action_similarity": x.get("action_similarity"),
                             "before": (x.get("before") or {}).get("next_action"), "after": (x.get("after") or {}).get("next_action"),
                             "hazard": [(x.get("before") or {}).get("hazard"), (x.get("after") or {}).get("hazard")],
                             "moved": [f"{m['area']}: {m['from']} -> {m['to']}" for m in x.get("moved") or [] if m["from"] != m["to"]],
                             "new": [n["area"] for n in x.get("new") or []], "rests_on_engineer_report": x.get("rests_on_engineer_report"),
                             "remaining": [r["area"] for r in x.get("remaining") or []]} for x in s["engineer_inputs"]],
        "flags": s["provenance"]["flags"], "verified_outcomes": s["provenance"]["verified_outcomes"], "safety": s["safety"],
    }


def main():
    store = CaseStore(Path(tempfile.mkdtemp()) / "eval.db")
    try:
        ids = public_samples.seed_showcase(store)
        out = {}
        for cid in ids:
            title = store.case(cid)["title"]
            s = CS.build(store, cid)
            if not s["current"]:
                continue
            row = {"title": title, "synthetic": s["synthetic"], "state": summary(s),
                   "all_checks": [{"round": r.get("result") and i + 1, "check": r["result"]["next_check"]["action"],
                                   "safety": S.classify(r["result"]["next_check"], hv_system=s["safety"]["high_voltage_system"])["classes"]}
                                  for i, r in enumerate(discussion.rounds_of(store, cid)) if r.get("result")]}
            if title.startswith("micro-ROS"):
                store.append(cid, "case_closure", THREAD_OUTCOME, actor="Public thread (reported)", source="case_state_eval")
                after = CS.build(store, cid)
                lad = inv.verification_ladder(store, cid)
                row["after_thread_outcome"] = {"ladder_level": lad["level"], "engineer_reported_confirmation": lad["engineer_reported_confirmation"],
                                               "status": after["current"]["status"], "verified_outcomes": after["provenance"]["verified_outcomes"],
                                               "flags": after["provenance"]["flags"],
                                               "leading_now": [h["area"] for h in after["current"]["hypotheses"] if h["status"] == "leading"]}
            out[title] = row
    finally:
        store.close()
    json.dump(out, open(os.path.join(HERE, "results.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for t, r in out.items():
        st = r["state"]
        print("##", t[:60], "| synthetic" if r["synthetic"] else "| real")
        print("  current:", st["current_from"]["source"], st["current_from"]["n"], "| status", st["status"], "| counts", st["counts"])
        print("  next:", st["next_action"], "|", st["next_action_safety"]["classes"], "review" if st["next_action_safety"]["requires_review"] else "")
        print("  bases:", [(e["id"], e["status"], e["status_basis"]) for e in st["explanations"]])
        for x in st["engineer_inputs"]:
            print("  input", x["kind"], x["round"], x["verdict"], x["moved"], x["new"])
        print("  flags:", [f["source"] for f in st["flags"]])
        if "after_thread_outcome" in r:
            print("  after thread outcome:", {k: r["after_thread_outcome"][k] for k in ("ladder_level", "status", "leading_now")}, "flags", [f["source"] for f in r["after_thread_outcome"]["flags"]])


if __name__ == "__main__":
    main()
