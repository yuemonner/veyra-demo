"""Debug replay: how engineers actually debugged an ambiguous case, replayed against the data.

An engineer describes, step by step, what they checked or swapped and what each step showed (an interview, a chat thread
or a service record). For every step Veyra asks two questions of the data:

  before  Could the data recorded BEFORE the step have settled it? (settled by a computed relation, answerable from
          signals in the log, needs data that exists elsewhere, or no signal can show it.)
  after   Does the data recorded AFTER the step confirm what the engineers concluded? (e.g. a part was swapped and the
          relation that did not add up is unchanged: the swap did not touch the cause.)

and names what would have settled the step earlier: a cheaper discriminating measurement, or a signal to log.

Across cases this is a feasibility measure for a fleet's systems: what share of the checks engineers ran (especially
the ones that were not the issue) the data could have answered, the hours and parts that would have saved, and which
added signals would unlock the most. The approach follows an advisor's suggestion: ask engineers how they debug
ambiguous logs, then work back to see whether the parts they checked that were not the issue can be determined from
the logs. Elicitation follows the Critical Decision Method (Klein); ordering checks by cost and probability follows
decision-theoretic troubleshooting (Heckerman, Breese & Rommelse); "which signal unlocks most" is a greedy cover.

Deterministic. Verdicts are classed by what decided them: a computed relation, a signal present in the log (an
engineer or the AI still reads it), or nothing in the data. Nothing here is a confirmed cause.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from app.physics import electrical as E

VERSION = "debug-replay-v1"

WHEEL_WORDS = {"FL": r"\bFL\b|front[- ]left|左前", "FR": r"\bFR\b|front[- ]right|右前", "RL": r"\bRL\b|rear[- ]left|左后", "RR": r"\bRR\b|rear[- ]right|右后"}
PHYSICAL = re.compile(r"inspect|visual|look(ed)? at|re-?torque|torqu(ed|ing) the|reseat|re-?crimp|crimp|clean|corros|discolou?r|burn|multimeter|"
                      r"insulation|megger|wiggle|continuity|four[- ]wire|4[- ]wire|probe|scope|oscilloscope|拆|目视|紧固|重新插|压接|万用表|绝缘|腐蚀|发黑|烧", re.I)
# words in a step -> names a decoded log might use for the signal that answers it
SYNONYMS = [
    (r"\bsoc\b|state of charge|charge level|电量", ["SOC"]),
    (r"cell|电芯", ["Cell_V", "T_cell"]),
    (r"battery|pack|bms|电池", ["Pack_V", "Pack_I", "SOC", "BMS", "DCL"]),
    (r"temperature|thermal|overheat|温度|过热", ["T_inv", "T_cell", "Temp", "T_"]),
    (r"software|firmware|version|ota|update|rollback|软件|版本|升级|回滚", ["_SW", "Version", "SW="]),
    (r"\bdtc\b|fault code|error code|remote code|code 7|故障码|报警码", ["DTC", "Remote_Code", "FaultLevel", "ERROR", "WARN"]),
    (r"torque|扭矩", ["Tq", "Torque"]),
    (r"hill[- ]?start|hsa|坡起|驻坡", ["HSA"]),
    (r"brake|epb|parking|制动|驻车", ["EPB", "Brake"]),
    (r"speed|速度|车速", ["Speed", "Spd"]),
    (r"voltage|undervolt|电压|欠压", ["_V=", "DC_V", "Pack_V", "under-voltage"]),
    (r"current|电流", ["_I=", "Pack_I"]),
    (r"inverter|motor|mcu|逆变器|电机", ["MCU_"]),
    (r"\bvcu\b|vehicle control|整车控制", ["VCU_"]),
    (r"autonomy|\bad\b|planner|route|自动驾驶|路线", ["AD_"]),
    (r"can bus|bus load|communication|timeout|lost|通信|超时|丢帧", ["timeout", "lost", "missing"]),
]


def _snap_dates(lines: List[str]) -> List[Tuple[int, int, Optional[date]]]:
    """(first line, last line, date) of each snapshot."""
    snaps = E.parse(lines)
    out = []
    for i, s in enumerate(snaps):
        start = s["first_line"]
        end = (snaps[i + 1]["first_line"] - 1) if i + 1 < len(snaps) else len(lines)
        t = s["rows"][0][0] if s["rows"] else None
        out.append((start, end, date.fromtimestamp(t) if t is not None else None))
    return out


def _subset(lines: List[str], spans: List[Tuple[int, int, Optional[date]]], keep) -> List[str]:
    head_end = (spans[0][0] - 1) if spans else len(lines)
    out = lines[:head_end]
    for a, b, d in spans:
        if keep(d):
            out += lines[a - 1:b]
    return out


def _wheel(text: str) -> Optional[str]:
    for w, pat in WHEEL_WORDS.items():
        if re.search(pat, text, re.I):
            return w
    return None


STEP_CAND = {"contact": r"connector|terminal|contact|crimp|plug|接插件|端子|接触|压接|插头",
             "conductor": r"cable|busbar|fuse|harness|wiring|线缆|母排|保险丝|线束",
             "inverter_draw": r"inverter|\bMCU\b|MCU_|motor controller|逆变器|电机控制器",
             "sensor_gain": r"voltage sensor|DC_V sensor|sensor calibration|电压传感器|电压采样"}


def _candidate(text: str) -> Optional[str]:
    for cid, pat in STEP_CAND.items():
        if re.search(pat, text, re.I):
            return cid
    return None


def _signal_lines(lines: List[str], text: str, part: str = "", limit: int = 3) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Signal names in the log that answer a step (those named by the part first), and a few lines showing them."""
    names: List[str] = []
    for src in (part, text):
        for pat, cands in SYNONYMS:
            if src and re.search(pat, src, re.I):
                names += cands
    found, ev = [], []
    for name in dict.fromkeys(names):
        hit = [(i, l) for i, l in enumerate(lines, 1) if not l.startswith("#") and name.lower() in l.lower()]
        if not hit:
            continue
        label = name.strip("_=")
        if len(label) >= 2 and label not in found:
            found.append(label)
        for i, l in (hit[:1] + hit[-1:]) if len(hit) > 1 else hit[:1]:
            if len(ev) < limit and all(e["line"] != i for e in ev):
                ev.append({"line": i, "quote": l.strip()[:200]})
    return found, ev


def _ratio(snap: Dict[str, Any], w: str) -> Optional[float]:
    b = (snap.get("branches") or {}).get(w) or {}
    others = [(snap["branches"].get(x) or {}).get("k_v_per_a") for x in snap["branches"] if x != w]
    others = [o for o in others if o]
    if b.get("status") != "ok" or not others:
        return None
    return b["k_v_per_a"] / (sum(others) / len(others))


def replay_step(step: Dict[str, Any], lines: List[str], spans, elec_all: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    text = f"{step.get('check', '')} {step.get('part') or ''}"
    when = date.fromisoformat(step["when"]) if step.get("when") else None
    out: Dict[str, Any] = {"id": step["id"], "check": step["check"], "result": step["result"], "kind": step.get("kind", "check"),
                           "before": None, "after": None, "evidence": [], "signals": [], "cheaper": None, "needs": []}
    before_lines = _subset(lines, spans, lambda d: when is None or (d is not None and d < when)) if spans else lines
    after_spans = [s for s in spans if when is not None and s[2] is not None and s[2] > when]
    n_before = sum(1 for s in spans if when is None or (s[2] is not None and s[2] < when)) if spans else 0
    out["snapshots_before"], out["snapshots_after"] = n_before, len(after_spans)
    wheel = _wheel(text)
    reads_codes = bool(re.search(SYNONYMS[5][0], text, re.I))
    cand = None if reads_codes else _candidate(text)

    # 1. electrical relations, when the step concerns a branch the physics can speak to
    if elec_all and cand and (elec_all.get("relations") or {}).get("branch", {}).get("status") == "ok":
        w = wheel or elec_all.get("wheel")
        eb = E.analyse(before_lines) if n_before else None
        ab = next((a for a in (eb or {}).get("assessments") or [] if a["wheel"] == w), None)
        if ab is not None:
            c = next((c for c in ab["candidates"] if c["id"] == cand), None)
            rec = next((m for m in ab["measurements"] if m["id"] == ab.get("recommended")), None)
            if c and c["status"] == "consistent":
                others = [x["id"] for x in ab["candidates"] if x["status"] == "consistent" and x["id"] != cand]
                out["before"] = {"verdict": "not_separable", "how": "electrical",
                                 "text": f"Before this step the data already showed {w}'s branch did not add up, but could not tell {E.MECHANISMS[cand].format(w=w)} "
                                         f"apart from {', '.join(E.MECHANISMS[o].format(w=w) for o in others)}."}
                if "inverter_draw" in [cand] + others and any(x in ("contact", "conductor") for x in [cand] + others):
                    out["needs"].append("per-branch DC current on CAN (lets the data separate branch resistance from current draw)")
                if rec:
                    out["cheaper"] = {"measurement": rec["id"], "what": rec["what"], "cost_tier": rec["cost"], "cost_label": rec["cost_label"],
                                      "expected": rec["expected"], "needs_approval": rec["needs_approval"]}
            elif c and c["status"] == "weakened":
                out["before"] = {"verdict": "weakened", "how": "electrical", "text": f"Before this step the data already weakened it: {c['why']}"}
        elif n_before and eb and (eb.get("relations") or {}).get("branch", {}).get("status") == "ok" and w not in ((eb["relations"]["branch"].get("flagged")) or []):
            out["before"] = {"verdict": "no_anomaly", "how": "electrical", "text": f"Before this step {w}'s branch drop matched the other wheels'."}
        # after: did the relation change?
        if after_spans and n_before:
            snaps = elec_all.get("snapshots") or []
            dated = list(zip(spans, snaps))
            rb = [_ratio(s, w) for (sp, s) in dated if sp[2] is not None and when is not None and sp[2] < when]
            ra = [_ratio(s, w) for (sp, s) in dated if sp[2] is not None and when is not None and sp[2] > when]
            rb, ra = [x for x in rb if x], [x for x in ra if x]
            if rb and ra:
                b_, a_ = sum(rb) / len(rb), sum(ra) / len(ra)
                lines_ = [l for (sp, s) in dated for l in ((s.get("branches") or {}).get(w) or {}).get("lines", [])[:1]]
                out["evidence"] += [{"line": l, "quote": lines[l - 1].strip()[:200]} for l in lines_[:3]]
                if abs(a_ - b_) / b_ <= 0.15 and b_ > 1.3:
                    out["after"] = {"verdict": "unchanged", "how": "electrical",
                                    "text": f"After this step {w}'s drop per amp is still {a_:.1f}× the other wheels' (it was {b_:.1f}×): "
                                            f"what was {'changed' if step.get('kind') in ('swap', 'repair') else 'checked'} did not change the relation that did not add up.",
                                    "values": {"before_ratio": round(b_, 2), "after_ratio": round(a_, 2)}}
                elif b_ > 1.3 and a_ <= 1.15:
                    out["after"] = {"verdict": "resolved", "how": "electrical",
                                    "text": f"After this step {w}'s drop per amp matches the other wheels' again ({a_:.1f}×, was {b_:.1f}×): consistent with the fix holding.",
                                    "values": {"before_ratio": round(b_, 2), "after_ratio": round(a_, 2)}}

    # 2. signals in the log that answer the step
    if out["before"] is None:
        found, ev = _signal_lines(before_lines, text, step.get("part") or "")
        if found:
            out["before"] = {"verdict": "answerable", "how": "signals",
                             "text": f"The log before this step records {', '.join(found[:4])}: this could be read from the data instead of on the vehicle "
                                     "(someone still has to read it; Veyra does not judge it automatically)."}
            out["signals"], out["evidence"] = found, ev + out["evidence"]
        elif PHYSICAL.search(text):
            out["before"] = {"verdict": "physical_only", "how": "none", "text": "No recorded signal shows this; it needs a person at the vehicle."}
        else:
            found_all, ev_all = _signal_lines(lines, text, step.get("part") or "")
            if found_all and when is not None:
                out["before"] = {"verdict": "later_data", "how": "signals",
                                 "text": f"The signals that answer it ({', '.join(found_all[:4])}) appear only in data recorded after this step."}
                out["evidence"] = ev_all
            else:
                out["before"] = {"verdict": "not_in_data", "how": "none",
                                 "text": "Nothing in this log answers it. If a signal on the vehicle would, it should be logged."}
                out["needs"] = ["a signal for: " + (step.get("part") or step["check"])[:80]]
    # 3. what the step is worth in hindsight
    avoidable = step["result"] == "not_the_issue" and out["before"]["verdict"] in ("answerable", "weakened", "no_anomaly")
    redirectable = step["result"] == "not_the_issue" and out["before"]["verdict"] == "not_separable" and out["cheaper"] is not None
    out["hindsight"] = ("avoidable" if avoidable else "cheaper_first" if redirectable else "needed" if step["result"] in ("fault_found", "partly") else "not_decidable")
    return out


def replay(lines: List[str], walkthrough: Dict[str, Any]) -> Dict[str, Any]:
    spans = _snap_dates(lines) if lines else []
    if spans and all(s[2] is None for s in spans):
        spans = []
    elec = E.analyse(lines) if lines else None
    steps = [replay_step(st, lines, spans, elec) for st in walkthrough.get("steps") or []]
    return {"version": VERSION, "steps": steps, "summary": summarise(walkthrough, steps),
            "electrical": bool(elec and elec.get("assessments")),
            "boundary": "Replays the engineers' account against the recorded data. 'Answerable' means the signals are in the log, "
                        "not that Veyra has read them correctly; nothing here confirms a cause."}


def summarise(w: Dict[str, Any], steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_id = {s["id"]: s for s in w.get("steps") or []}
    nti = [s for s in steps if s["result"] == "not_the_issue"]
    avoid = [s for s in nti if s["hindsight"] == "avoidable"]
    cheaper = [s for s in nti if s["hindsight"] == "cheaper_first"]
    mins = lambda xs: sum((by_id[s["id"]].get("minutes") or 0) for s in xs)  # noqa: E731
    cost = lambda xs: sum((by_id[s["id"]].get("cost") or 0) for s in xs)  # noqa: E731
    counts: Dict[str, int] = {}
    for s in steps:
        counts[s["before"]["verdict"]] = counts.get(s["before"]["verdict"], 0) + 1
    data_could = sum(counts.get(k, 0) for k in ("answerable", "weakened", "no_anomaly", "not_separable"))
    adds: Dict[str, List[str]] = {}
    for s in steps:
        for n in s["needs"]:
            adds.setdefault(n, []).append(s["id"])
    return {"steps": len(steps), "not_the_issue": len(nti), "avoidable": len(avoid), "cheaper_first": len(cheaper),
            "verdicts": counts, "data_speaks_to": data_could,
            "minutes_not_the_issue": mins(nti), "minutes_avoidable": mins(avoid) + mins(cheaper),
            "cost_not_the_issue": cost(nti), "cost_avoidable": cost(avoid) + cost(cheaper), "currency": w.get("currency"),
            "confirmed_after": sum(1 for s in steps if (s.get("after") or {}).get("verdict") in ("unchanged", "resolved")),
            "signal_additions": [{"what": k, "steps": v} for k, v in sorted(adds.items(), key=lambda kv: -len(kv[1]))]}


def fleet_summary(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Across cases with walkthroughs: the feasibility of log-first diagnosis, per system and architecture."""
    groups: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for c in cases:
        w, s = c["walkthrough"], c["replay"]["summary"]
        key = (w.get("system") or "unspecified", w.get("architecture") or "unknown")
        g = groups.setdefault(key, {"system": key[0], "architecture": key[1], "cases": 0, "steps": 0, "not_the_issue": 0, "avoidable": 0,
                                    "cheaper_first": 0, "data_speaks_to": 0, "minutes_avoidable": 0, "minutes_not_the_issue": 0})
        g["cases"] += 1
        for k in ("steps", "not_the_issue", "avoidable", "cheaper_first", "data_speaks_to", "minutes_avoidable", "minutes_not_the_issue"):
            g[k] += s[k]
    rows = sorted(groups.values(), key=lambda g: -g["cases"])
    for g in rows:
        g["share_data_speaks_to"] = round(g["data_speaks_to"] / g["steps"], 2) if g["steps"] else None
        g["share_wasted_avoidable"] = round((g["avoidable"] + g["cheaper_first"]) / g["not_the_issue"], 2) if g["not_the_issue"] else None
    return {"version": VERSION, "systems": rows, "cases": len(cases),
            "note": "Feasibility from the engineers' own walkthroughs. Few cases per system means wide uncertainty; read it as a direction."}


# ---------------------------------------------------------------- the case store
def walkthrough_of(store, case_id: str) -> Optional[Dict[str, Any]]:
    for e in reversed(store.ledger(case_id)):
        if e["kind"] == "debug_walkthrough":
            return {**e["payload"], "entry_id": e["id"], "actor": e["actor"], "recorded_at": e["recorded_at"]}
    return None


def replay_case(store, case_id: str) -> Optional[Dict[str, Any]]:
    from app.agent.investigator import LogTools
    w = walkthrough_of(store, case_id)
    if not w:
        return None
    raw = LogTools(store, case_id).raw or []
    return {"walkthrough": w, "replay": replay(raw, w)}


def fleet(store) -> Dict[str, Any]:
    out = []
    for c in store.cases():
        r = replay_case(store, c["id"])
        if r:
            out.append(r)
    return fleet_summary(out)


# ---------------------------------------------------------------- pasted text -> steps (heuristic; the engineer corrects it)
_DATE = re.compile(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?")
_MIN = re.compile(r"(\d+(?:\.\d+)?)\s*(min|mins|minutes|分钟|h|hr|hrs|hours|小时)\b", re.I)
_COST = re.compile(r"(?:€|EUR|\$|USD|¥|CNY|RMB)\s*(\d[\d,]*(?:\.\d+)?)|(\d[\d,]*(?:\.\d+)?)\s*(?:€|EUR|USD|CNY|RMB|元)", re.I)
_FOUND = re.compile(r"found|fixed|faulty|was the (cause|problem)|root cause|resolved|找到|修好|就是|原因是|发黑|烧蚀", re.I)
_NOT = re.compile(r"not the (issue|cause|problem)|no change|didn'?t help|did not help|came back|still (happen|fail|stop)|normal|ok\b|fine|没问题|正常|无效|又出现|还是", re.I)
_PART = re.compile(r"partly|partial|contribut|部分|有关", re.I)
_SWAP = re.compile(r"replac|swap|换了|更换", re.I)
_REPAIR = re.compile(r"repair|re-?crimp|re-?torque|fix|reseat|tighten|维修|重新压接|紧固|重新插", re.I)


def parse_text(text: str) -> Dict[str, Any]:
    """Each bullet or line that describes an action becomes a step, with date, time, cost, kind and result guessed from
    its words. A first draft only: the engineer checks every field before it is recorded."""
    raw = [re.sub(r"^\s*(?:[-*•·]|\d+[.)、])\s*", "", l).strip() for l in re.split(r"[\n;；]+", text or "")]
    steps = []
    for l in raw:
        if len(l) < 6:
            continue
        d = _DATE.search(l)
        m = _MIN.search(l)
        c = _COST.search(l)
        minutes = None
        if m:
            v = float(m.group(1))
            minutes = v * 60 if m.group(2).lower() in ("h", "hr", "hrs", "hours", "小时") else v
        result = "fault_found" if _FOUND.search(l) else "partly" if _PART.search(l) else "not_the_issue" if _NOT.search(l) else "inconclusive"
        check = _DATE.sub("", l).strip(" :-,，。")
        steps.append({"check": check[:300], "kind": "swap" if _SWAP.search(l) else "repair" if _REPAIR.search(l) else "check", "result": result,
                      "when": f"{int(d.group(1)):04d}-{int(d.group(2)):02d}-{int(d.group(3)):02d}" if d else None,
                      "minutes": minutes, "cost": float((c.group(1) or c.group(2)).replace(",", "")) if c else None, "guessed": True})
    return {"steps": steps[:30], "note": "Guessed from the text: check every step's result, date, time and cost before recording it."}
