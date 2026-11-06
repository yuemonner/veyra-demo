"""Safety-aware experiment planning: what a proposed check would expose people and the machine to, who must review it,
and whether a safer check can answer the same question.

Hazard classes, safest first:
  offline              data already recorded, log replay, analysis, simulation: no physical action
  bench                a bench or hardware-in-the-loop test, or a de-energised part removed from the machine
  low_risk_physical    a look, reading codes, a low-voltage logger or debugger, a board on a desk
  hv_deenergised       work on a high-voltage system after it is isolated (isolation, lock-out, proving it dead)
  machine_operation    the machine moves or runs a modified behaviour: a driven launch, a ramp start, new software on it
  live_hv              measuring or probing an energised high-voltage circuit, at rest or under load
  fault_reproduction   deliberately recreating the failure (the stop, the undervoltage, a fault re-introduced)

hv_deenergised, machine_operation, live_hv and fault_reproduction are hazardous: Veyra marks them as requiring review by
a qualified engineer before anyone runs them, names the qualification, and offers the safer check that answers the same
question where one exists. Veyra never marks a hazardous check as approved; only a person can record that.

High voltage here means above 60 V DC or 30 V AC (voltage class B in ISO 6469-3 / UN ECE R100). Whether a case's machine
has such a system is taken from its data (a recorded pack or bus voltage) or its description; without either, electrical
words alone do not make a check high-voltage.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

VERSION = "experiment-safety-v1"
ORDER = ["offline", "bench", "low_risk_physical", "hv_deenergised", "machine_operation", "live_hv", "fault_reproduction"]
HAZARDOUS = {"hv_deenergised", "machine_operation", "live_hv", "fault_reproduction"}
HV_LIMIT_V_DC = 60.0

LABEL = {
    "offline": ("Offline: uses data or simulation only", "离线：只用已有数据或仿真"),
    "bench": ("Bench test: off the machine or on a test rig", "台架测试：不在车上，或在测试台上"),
    "low_risk_physical": ("Low-risk physical check", "低风险的现场检查"),
    "hv_deenergised": ("High-voltage work, de-energised: isolation and proving dead first", "高压作业（断电）：先隔离、验电"),
    "machine_operation": ("Machine in operation: it moves or runs changed behaviour", "车辆运行：会动车或运行改动后的功能"),
    "live_hv": ("Live high-voltage measurement", "带电高压测量"),
    "fault_reproduction": ("Deliberate fault reproduction", "故意复现故障"),
}
REVIEWER = {
    "hv_deenergised": ("A qualified high-voltage technician (isolation, lock-out/tag-out, proving dead)", "有高压资质的技术员（断电隔离、上锁挂牌、验电）"),
    "live_hv": ("A qualified high-voltage technician, with rated insulated tools and PPE, under a written procedure", "有高压资质的技术员，使用绝缘工具和防护装备，按书面规程操作"),
    "machine_operation": ("The test engineer responsible for the machine, with a cleared area and a safety driver or remote stop", "负责这台车的测试工程师，场地清空，配安全员或远程急停"),
    "fault_reproduction": ("The test engineer and the site safety lead: a written test plan with stop criteria, on a closed area", "测试工程师和现场安全负责人：书面测试方案、明确停止条件，在封闭场地"),
}

# Words that describe what a check does, English and Chinese. Kept explicit so every classification can be explained.
_P = {
    "offline": r"\blog(s|ged)?\b|\bpull\b|\breplay\b|\bsimulat|\banaly[sz]e\b|\bcompare (the )?(logs?|data|snapshots?)\b|\bshift of logs\b|"
               r"\bdata request\b|\bfreeze[- ]frame\b|\bfrom the data\b|日志|回放|仿真|离线|调取|调一整班|对比.*数据",
    "bench": r"\bbench\b|\bon the rig\b|\btest rig\b|\bhardware[- ]in[- ]the[- ]loop\b|\bHIL\b|\bSIL\b|\bremoved\b|\bon a desk\b|台架|测试台|拆下|离车",
    "low_risk_physical": r"\binspect|\blook at\b|\bvisual\b|\bread (the )?(codes?|dtc)\b|\bphotograph|\blogic analy[sz]er\b|\bdebugger\b|\bserial\b|\bUART\b|"
                         r"\bwireshark\b|\btcpdump\b|\bpacket capture\b|\bflash\b|\bfirmware\b|\bpower[- ]cycle\b|目视|拍照|读取.*码|抓包|串口",
    "hv_work": r"\bhigh[- ]voltage\b|\bHV\b|\bpack\b|\binverter (input |dc )?terminal|\bbusbar\b|\bdc bus\b|\bHV connector\b|\bcontactor\b|"
               r"\bsegment\b|\bfour[- ]wire\b|\bresistance of each\b|高压|母线|逆变器.*端子|接插件|端子|电池包|分段.*电阻|四线",
    "deenergised": r"\bisolat|\bde-?energi[sz]|\bdisconnect(ed)? (the )?(pack|battery|HV)|\block[- ]?out\b|\bpowered (off|down)\b|\bwith (the )?high voltage isolated\b|"
                   r"断开高压|断电|下电|隔离",
    "live_measure": r"\bmeasure\b|\bmeasur|\bmultimeter\b|\bmeter\b|\bprobe\b|\bclamp\b|\bscope\b|\boscilloscope\b|\bvoltage drop\b|\bunder load\b|"
                    r"万用表|实测|测量|电流钳|示波器|压降|测.{0,8}电[压流]",
    "under_load": r"\bunder load\b|\bduring (the )?(launch|start|drive)\b|\bwhile (driving|launching)\b|\bat \d+ ?A\b|负载下|起步时|行驶时|运行时",
    "operation": r"\blaunch\b|\bdrive\b|\bdriven\b|\bramp start\b|\bhill start\b|\bon the route\b|\btest drive\b|\brun the machine\b|"
                 r"\bdeploy (the )?(software|firmware|update)\b|\binstall (the )?(software|update)\b|\bupdate the (vcu|software)\b|"
                 r"起步|行驶|试车|坡道|上坡|改软件|刷写|升级",
    "reproduction": r"\breproduc|\brecreate\b|\btrigger (the )?(fault|stop|failure)\b|\bre-?introduce\b|\bsame (ramp|conditions)\b|\bcold (start|launch|morning)\b|"
                    r"\bHSA (on|off)\b|\bwith and without\b|复现|同坡道|冷车|冷电池|开/关|关/开|故意",
}
_R = {k: re.compile(v, re.I) for k, v in _P.items()}

# The electrical module's measurements (app/physics/electrical.py), classified by what they physically involve.
MEASUREMENT_CLASS = {
    "t_inv_trend": "offline",
    "segment_r": "hv_deenergised",
    "meter_v": "live_hv",
    "branch_i": "live_hv",
    "segment_dv_loaded": "live_hv",
}
MEASUREMENT_ALSO = {"branch_i": ["machine_operation"], "segment_dv_loaded": ["machine_operation"]}

SAFER = {
    "fault_reproduction": ("Replay the logged commands through a model of the controller, or run the changed software on a "
                           "hardware-in-the-loop bench, before any reproduction on the machine",
                           "先把记录下来的指令放进控制器模型里回放，或在硬件在环台架上跑改动后的软件，再考虑在车上复现"),
    "live_hv": ("Measure the same path de-energised with a four-wire resistance meter, or ask for the signal to be logged "
                "(for example per-branch DC current) so the next occurrence records it",
                "改为断电后用四线法量同一路电阻，或让工程团队把这个信号记录到日志里（例如每一路的直流电流），下次发生时就有数据"),
    "machine_operation": ("Pull existing logs of the same operating conditions from the fleet first",
                          "先从车队调取相同工况下已有的日志"),
    "hv_deenergised": ("Check what the existing logs can still separate first; if the measurement is needed, plan it with the "
                       "high-voltage technician", "先看已有日志还能区分什么；确需测量时，与高压技术员一起安排"),
}


def has_hv(system_voltage_v: Optional[float] = None, description: str = "") -> bool:
    if system_voltage_v is not None:
        return system_voltage_v > HV_LIMIT_V_DC
    m = re.search(r"(\d{2,4})\s*V\b", description or "")
    return bool(m and float(m.group(1)) > HV_LIMIT_V_DC) or bool(re.search(r"high[- ]voltage|高压", description or "", re.I))


def classify(check: Dict[str, Any], *, hv_system: bool) -> Dict[str, Any]:
    """Hazard classes of one proposed check. `check` may hold action/what, how, conditions, where, record, needs_approval,
    and an electrical measurement id. Returns the classes found, the worst one, why, and the review it needs."""
    mid = check.get("measurement") or check.get("id")
    text = " ".join(str(x) for x in [check.get("action"), check.get("what"), check.get("why"), " ".join(check.get("how") or []),
                                     check.get("conditions"), check.get("baseline")] if x)
    found: Dict[str, str] = {}
    if mid in MEASUREMENT_CLASS:
        found[MEASUREMENT_CLASS[mid]] = f"electrical measurement '{mid}'"
        for c in MEASUREMENT_ALSO.get(mid, []):
            found[c] = f"electrical measurement '{mid}' is taken during a launch"
    hv_words = bool(_R["hv_work"].search(text))
    if hv_system and hv_words:
        if _R["deenergised"].search(text) and not _R["under_load"].search(text):
            found.setdefault("hv_deenergised", "works on the high-voltage system, isolated")
        elif _R["live_measure"].search(text):
            found.setdefault("live_hv", "measures on the high-voltage system without stating it is isolated" if not _R["under_load"].search(text)
                             else "measures on the energised high-voltage system under load")
        else:
            found.setdefault("hv_deenergised", "touches the high-voltage system")
    if _R["operation"].search(text) or (check.get("where") == "on_site" and _R["under_load"].search(text)):
        found.setdefault("machine_operation", "the machine moves or runs changed behaviour")
    if _R["reproduction"].search(text) and ("machine_operation" in found or check.get("where") == "on_site"):
        found.setdefault("fault_reproduction", "recreates the conditions of the failure on the machine")
    if not found:
        if _R["bench"].search(text) or check.get("where") == "lab":
            found["bench"] = "bench or lab"
        elif _R["low_risk_physical"].search(text) or check.get("where") == "on_site":
            found["low_risk_physical"] = "a look or a low-voltage connection"
        elif _R["offline"].search(text) or check.get("where") == "remote":
            found["offline"] = "data or analysis only"
        else:
            found["low_risk_physical"] = "not classified from the description: treated as a physical check"
    worst = max(found, key=ORDER.index)
    hazardous = sorted([c for c in found if c in HAZARDOUS], key=ORDER.index)
    return {"version": VERSION, "classes": sorted(found, key=ORDER.index), "worst": worst, "why": found,
            "hazardous": bool(hazardous), "requires_review": bool(hazardous),
            "review_by": [REVIEWER[c][0] for c in hazardous], "review_by_zh": [REVIEWER[c][1] for c in hazardous],
            "label": LABEL[worst][0], "label_zh": LABEL[worst][1],
            "safer_alternative": SAFER.get(worst, (None, None))[0] if hazardous else None,
            "safer_alternative_zh": SAFER.get(worst, (None, None))[1] if hazardous else None,
            "approved": False, "approval_note": "Only a person can record approval; Veyra never approves a hazardous check." if hazardous else None}


def plan(candidates: List[Dict[str, Any]], open_ids: List[str], *, hv_system: bool, deprioritised: Optional[List[str]] = None) -> Dict[str, Any]:
    """Order candidate checks: first those that separate the open explanations, then the safest, then the cheapest.

    Each candidate: {id, what, cost_tier, strength, predicts: {hypothesis_id: outcome_class}, ...}. A candidate
    separates when the open hypotheses it predicts fall into at least two outcome classes. Returns the recommended check,
    the safest check that still separates something, every candidate with its safety, and why the order is what it is.
    deprioritised: hypotheses an engineer reported as not the issue. They stay open (a report does not exclude), but a
    check counts as separating only if it separates the others, unless nothing else is left to separate.
    """
    dep = set(deprioritised or [])
    focus = [h for h in open_ids if h not in dep]
    if len(focus) >= 2:
        open_ids = focus
    rows = []
    for c in candidates:
        s = classify(c, hv_system=hv_system)
        pred = {h: k for h, k in (c.get("predicts") or {}).items() if h in open_ids}
        classes = {}
        for h, k in pred.items():
            classes.setdefault(k, []).append(h)
        splits = len(classes) > 1
        rows.append({**c, "safety": s, "separates": splits, "groups": list(classes.values()),
                     "rank": (0 if splits else 1, 0 if c.get("strength") == "strong" else 1, ORDER.index(s["worst"]), c.get("cost_tier", 9))})
    if not rows:
        return {"recommended": None, "candidates": [], "why": "no candidate checks"}
    strong = sorted(rows, key=lambda r: (r["rank"][0], r["rank"][1], r["rank"][2], r["rank"][3]))
    by_safety = sorted([r for r in rows if r["separates"]], key=lambda r: (ORDER.index(r["safety"]["worst"]), r["rank"][1], r["rank"][3]))
    best = strong[0]
    safest = by_safety[0] if by_safety else None
    order_note = []
    first = best
    if best["safety"]["hazardous"] and safest is not None and not safest["safety"]["hazardous"] and safest["id"] != best["id"]:
        first = safest
        order_note.append(f"'{best['id']}' separates the explanations most strongly but is {best['safety']['worst'].replace('_', ' ')}; "
                          f"'{safest['id']}' is {safest['safety']['worst']} and still separates {', '.join('/'.join(g) for g in safest['groups'])}, so it comes first")
    if first["safety"]["hazardous"]:
        order_note.append("no non-hazardous check separates the open explanations: the recommended one needs review by a qualified engineer before it is run")
    then = best if first is not best else None
    return {"recommended": {k: first[k] for k in ("id", "what", "cost_tier", "strength", "safety", "groups") if k in first},
            "then": ({k: then[k] for k in ("id", "what", "cost_tier", "strength", "safety", "groups") if k in then} if then else None),
            "considered_open": open_ids, "deprioritised": sorted(dep),
            "strongest": {k: best[k] for k in ("id", "what", "cost_tier", "strength", "safety", "groups") if k in best},
            "safest_separating": ({k: safest[k] for k in ("id", "what", "cost_tier", "strength", "safety", "groups") if k in safest} if safest else None),
            "candidates": [{k: r[k] for k in ("id", "what", "cost_tier", "strength", "safety", "separates", "groups") if k in r} for r in strong],
            "why": order_note}


def system_voltage(lines: List[str]) -> Optional[float]:
    """The highest pack or bus voltage recorded in a decoded log, if any."""
    best = None
    for m in re.finditer(r"\b(?:Pack_V|DC_V|Bus_V|HV_V)=(\d+(?:\.\d+)?)", "\n".join(lines[:200000])):
        v = float(m.group(1))
        best = v if best is None or v > best else best
    return best


def annotate_electrical(r: Optional[Dict[str, Any]], *, hv_system: bool = True) -> Optional[Dict[str, Any]]:
    """Add each measurement's safety, and a safety-aware order, to the electrical module's assessments (in place; the
    electrical module's own `recommended` is left as computed, so its validated behaviour is unchanged)."""
    if not r or not r.get("assessments"):
        return r
    for a in r["assessments"]:
        open_ids = [c["id"] for c in a["candidates"] if c["status"] == "consistent"]
        cands = []
        for m in a["measurements"]:
            m["safety"] = classify({"id": m["id"], "what": m["what"]}, hv_system=hv_system)
            pred = {cid: k for k, grp in enumerate(m.get("splits") or []) for cid in grp}
            cands.append({"id": m["id"], "what": m["what"], "cost_tier": m["cost"], "strength": m["strength"], "predicts": pred})
        p = plan(cands, open_ids, hv_system=hv_system)
        a["safety_plan"] = {"recommended": (p["recommended"] or {}).get("id"), "strongest": (p.get("strongest") or {}).get("id"),
                            "then": (p.get("then") or {}).get("id"), "then_requires_review": bool(((p.get("then") or {}).get("safety") or {}).get("requires_review")),
                            "requires_review": bool((p["recommended"] or {}).get("safety", {}).get("requires_review")),
                            "why": p["why"], "module_recommended": a.get("recommended")}
    return r
