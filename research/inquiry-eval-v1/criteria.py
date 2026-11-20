"""Scoring for inquiry-eval-v1 (PREREGISTRATION.md). Reads only the recorded investigation (result.inquiry, result.result)
and, for held-out cases, the sealed key. Never prints recording values."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional

FORBIDDEN = ("POST-RUN", "discriminate.py", "A > B", "one record separates", "c0cd923a", "e2aa7f94", "CASE-A-prediction",
             "揭示答案前写定的预测", "团队运行后分析")
TRUTH_TO_IDS = {"soc_drift": {"A"}, "capacity_fade": {"B"}, "current_scale": {"C"}, "sensor_bypass": {"C"}, "drift_weak_cell": {"A", "D"}}
AB_TIED_BY_CONSTRUCTION = {"soc_drift", "capacity_fade", "drift_weak_cell"}


def computations(iq: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every computation the agent ran: explorations and tests, with op, args, quantities and whether it counted."""
    out = []
    for e in iq.get("entries") or []:
        p = e["payload"]
        if e["kind"] == "compute":
            out.append({"id": p["id"], "op": p["op"], "args": p["args"], "quantities": p["quantities"], "test": False, "counts": False})
        elif e["kind"] == "test":
            pred = next((x for x in iq.get("predictions") or [] if x["id"] == p["prediction_id"]), {})
            out.append({"id": p["id"], "op": p["result"]["op"], "args": (pred.get("test") or {}).get("args") or {}, "quantities": p["result"]["quantities"],
                        "test": True, "counts": p["update"]["counts"], "predicted": [x["hypothesis"] for x in pred.get("predictions") or []],
                        "update": p["update"], "value_kind": p.get("value_kind")})
    return out


def chain_ok(iq: Dict[str, Any]) -> bool:
    es = iq.get("entries") or []
    for i, e in enumerate(es):
        prev = es[i - 1]["hash"] if i else "0" * 64
        h = hashlib.sha256((prev + json.dumps(e["payload"], sort_keys=True, default=str) + e["kind"]).encode()).hexdigest()
        if e["prev"] != prev or e["hash"] != h:
            return False
    seq = {e["payload"].get("id"): e["seq"] for e in es if e["kind"] == "prediction"}
    return all(seq.get(e["payload"]["prediction_id"], 10 ** 9) < e["seq"] for e in es if e["kind"] == "test")


def tiers(result: Dict[str, Any]) -> List[List[str]]:
    return [list(t.get("hypotheses") or []) for t in result.get("ranking") or []]


def tier_of(ts: List[List[str]], h: str) -> Optional[int]:
    return next((i for i, t in enumerate(ts) if h in t), None)


def separated_by_observation(cs: List[Dict[str, Any]], a: str, b: str) -> bool:
    """A counted test separated a and b and its value is an observation (not conditional on a stated anchor)."""
    return any(c["test"] and c["counts"] and c.get("value_kind") != "conditional" and
               ([a, b] in c["update"]["separated"] or [b, a] in c["update"]["separated"]) for c in cs)


def common(rec: Dict[str, Any]) -> Dict[str, Any]:
    iq = rec.get("inquiry") or {}
    res = rec.get("result") or {}
    cs = computations(iq)
    counted = [c for c in cs if c["test"] and c["counts"]]
    s: Dict[str, Any] = {}
    s["L1_loop"] = bool(counted) and any(c["update"]["discriminating"] for c in counted) and chain_ok(iq)
    s["ops_run"] = sorted({c["op"] for c in cs})
    s["tests_counted"] = len(counted)
    s["post_hoc"] = sum(1 for c in cs if c["test"] and not c["counts"])
    s["ranking"] = tiers(res)
    s["standing"] = {h["id"]: h.get("standing") for h in iq.get("hypotheses") or []}
    s["records_by_kind"] = iq.get("records_by_kind")
    s["_cs"] = cs
    return s


def case_a(rec: Dict[str, Any], *, cutoff_windows: List[str], prompt_text: str) -> Dict[str, Any]:
    s = common(rec)
    cs = s.pop("_cs")
    res = rec.get("result") or {}
    text = json.dumps(res, ensure_ascii=False).lower()
    s["L2_no_leakage"] = not any(f.lower() in prompt_text.lower() for f in FORBIDDEN)
    s["P1_topology"] = any(c["op"] == "topology" and c["quantities"].get("inferred_series_count") for c in cs)
    s["P2_charge_to_cutoff"] = any(c["op"] in ("integrate", "capacity") and (c["args"].get("role") or c["args"].get("current_role")) == "pack_current"
                                   and c["args"].get("window") in cutoff_windows for c in cs)
    s["P3_step_relation"] = any(c["test"] and c["counts"] and c["op"] == "step_response" and c["args"].get("x_role") == "pack_current"
                                and "C" in c.get("predicted", []) for c in cs)
    s["P4_bypass"] = any(c["test"] and c["counts"] and c["op"] in ("unexplained_changes", "transitions") and "C" in c.get("predicted", [])
                         and "pack_current" in json.dumps(c["args"]) for c in cs)
    anchors = [{a.get("kind") for a in c["args"].get("anchors") or []} for c in cs if c["op"] == "capacity"]
    s["P5a_capacity_anchors_computed"] = any("counter_since_anchor" in k and len(k) >= 2 for k in anchors)
    s["P5b_capacity_framing_stated"] = bool(re.search(r"(anchor|full[- ]charge|last (full )?calibrat|满充|标定|锚)", text)) and bool(re.search(r"(capacity|容量)", text))
    s["P5_capacity_framing"] = s["P5a_capacity_anchors_computed"] and s["P5b_capacity_framing_stated"]
    ts = s["ranking"]
    ta, tb = tier_of(ts, "A"), tier_of(ts, "B")
    s["R1_A_B_not_falsely_separated"] = (ta == tb) or not separated_by_observation(cs, "A", "B")
    s["N1_decisive_record"] = bool(re.search(r"(meter|charger|charg(e|ing) (record|history|log)|calibrat|充电机|充电记录|计量|标定)",
                                             json.dumps({k: res.get(k) for k in ("next_action", "data_requests", "field_questions")}, ensure_ascii=False).lower()))
    return s


def heldout(rec: Dict[str, Any], truth: Dict[str, Any]) -> Dict[str, Any]:
    s = common(rec)
    cs = s.pop("_cs")
    cause = truth["cause"]
    true_ids = TRUTH_TO_IDS[cause]
    ts = s["ranking"]
    tops = [x for x in true_ids if x in ("A", "B", "C")]
    false_ids = {"A", "B", "C"} - true_ids
    s["H1_loop"] = s["L1_loop"]
    topo = [c["quantities"].get("inferred_series_count") for c in cs if c["op"] == "topology" and c["quantities"].get("inferred_series_count")]
    s["H2_topology"] = "not run" if not topo else ("correct" if all(n == truth["n_series"] for n in topo) else "wrong")
    wrong_demote = False
    for t in tops:
        tt = tier_of(ts, t)
        for f in false_ids:
            tf = tier_of(ts, f)
            if tt is not None and tf is not None and tf < tt:
                wrong_demote = True
        if any(c["test"] and c["counts"] and c.get("value_kind") != "conditional" and t in c["update"]["contradicted"] for c in cs):
            wrong_demote = True
    s["H3_truth_not_demoted"] = not wrong_demote
    s["H4_no_false_certainty"] = (not separated_by_observation(cs, "A", "B")) if cause in AB_TIED_BY_CONSTRUCTION else "n/a"
    if cause == "sensor_bypass":
        tc = tier_of(ts, "C")
        s["H5_discovery"] = tc == 0
    elif cause in AB_TIED_BY_CONSTRUCTION:
        s["H5_discovery"] = s["standing"].get("C") == "contradicted"
    else:
        s["H5_discovery"] = "recorded only"
    return s
