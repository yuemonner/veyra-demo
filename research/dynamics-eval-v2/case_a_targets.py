"""CASE A development-regression targets (PREREGISTRATION.md), scored from an agent record's final report text. Fixed
before the CASE A runs of dynamics-eval-v1 (reused unchanged by dynamics-eval-v2). Prints booleans only (no data values).

  python case_a_targets.py RECORD.json [RECORD.json ...]
"""
from __future__ import annotations

import json
import re
import sys

T = {
    "T1_min_cell_acceleration": [r"(lowest|minimum|min|weakest|limiting)[- ]cell[^.]{0,120}(accelerat|steepen|faster|mv/s|rate of (decline|change))",
                                 r"accelerat[^.]{0,120}(lowest|minimum|min|weakest|limiting)[- ]cell"],
    "T2_spread_and_growth_rate": [r"spread[^.]{0,140}(mv/s|rate|grew|growth|increas|widen)", r"(diverg)[^.]{0,140}(mv/s|rate|grew|growth)"],
    "T3_relates_to_current_or_state": [r"(current|load|stationary|operating state|dc-?dc|constant)[^.]{0,140}(spread|lowest cell|diverg|cell 20)",
                                       r"(spread|lowest cell|diverg)[^.]{0,140}(current|load|stationary|operating state|dc-?dc)"],
    "T4_how_early_before_protection": [r"\d+(\.\d+)?\s*(s|sec|seconds|min|minutes)\b[^.]{0,100}(before|ahead of|prior to|earlier than)",
                                       r"(observable|visible|detectable|departed|depart(ure|s))[^.]{0,140}\d+(\.\d+)?\s*(s|sec|seconds|min|minutes)\b"],
    "T5_cell_vs_pack_level": [r"(cell-level|one cell|lowest cell|weak(est)? cell|cell 20|single cell)", r"(pack-level|whole pack|pack-wide|highest cell|group-wide|all cells)"],
    "T6_more_informative_next_check": [r"first.?fault|event record|protection code|trigger(ing)? cell|load step|known current|step response|independent (voltage|measurement)|all (individual )?cell|every cell|balanc"],
}


def text_of(mr: dict) -> str:
    res = (mr or {}).get("result") or {}
    parts = [res.get("headline", ""), res.get("explanation", ""), json.dumps(res.get("next_action") or {}),
             " ".join(f"{h.get('area', '')} {h.get('claim', '')} {h.get('because', '')}" for h in res.get("hypotheses") or []),
             " ".join(t.get("basis", "") for t in res.get("ranking") or [])]
    return " ".join(str(p) for p in parts).lower()


def score(mr: dict) -> dict:
    tx = text_of(mr)
    nxt = json.dumps(((mr or {}).get("result") or {}).get("next_action") or {}).lower()
    out = {}
    for k, rxs in T.items():
        if k == "T5_cell_vs_pack_level":
            out[k] = all(re.search(r, tx) for r in rxs)
        elif k == "T6_more_informative_next_check":
            out[k] = any(re.search(r, nxt) for r in rxs)
        else:
            out[k] = any(re.search(r, tx) for r in rxs)
    return out


if __name__ == "__main__":
    for f in sys.argv[1:]:
        rec = json.load(open(f))
        mr = rec.get("main_investigator_record") or {}
        print(f, rec.get("agent_mode"), json.dumps(score(mr)))
