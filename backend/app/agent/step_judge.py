"""Evidence-based step judge: what the data said before each engineer action, and what the action established.

For one decision point (a cutoff date) the judge builds Veyra's state from the frozen evidence only:
  facts      every statement with its tier (confirmed: read from cited lines or computed from them by a deterministic
             tool; inferred: needs a stated assumption, or carries a state forward more than a day; reported: an
             engineer's word; not identifiable), the date it is valid for, and its original line numbers;
  hypotheses each hypothesis in the case's space as supported, weakened, no support, or not identifiable, with the facts
             behind it (a tool's "weakened" is never an elimination);
  plan       the recommended check, with the outcome each hypothesis predicts, and data requests for what the data
             cannot show.

For each engineer step it then reconstructs what was known before it, what the engineer expected (as they reported),
what the step established (the engineer's result; and, where the data after it can show it, whether a relation changed),
which hypotheses remained, and what the step is worth in hindsight, split into verified and estimated.

Deterministic tools decide; an AI may add interpretation, but every claim it makes must cite fact ids, may not be
stronger than what it cites, and may not eliminate a hypothesis the facts do not eliminate. Each step also yields a
knowledge candidate (symptom, conditions, hypothesis, check, outcome, cost, applicability) for engineers to approve
later; nothing here learns from them yet.
"""
from __future__ import annotations

import json
import re
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.physics import electrical as E
from app.physics import expected as X

VERSION = "step-judge-v1"
STALE_DAYS = 1
TIERS = ["not_identifiable", "reported", "inferred", "confirmed"]   # weakest to strongest
CODE_READING = re.compile(r"\bdtc\b|fault code|error code|remote code|故障码|报警码|读取", re.I)


def _tier_min(ts: Sequence[str]) -> str:
    return min(ts, key=TIERS.index) if ts else "not_identifiable"


def _snap_dates(lines: Sequence[str]) -> List[Optional[date]]:
    return [date.fromtimestamp(s["rows"][0][0]) if s["rows"] else None for s in E.parse(list(lines))]


def _snap_spans(lines: Sequence[str]) -> List[Tuple[int, int]]:
    """(first, last) frozen-pack line of the rows of each snapshot."""
    return [(s["rows"][0][3], s["rows"][-1][3]) for s in E.parse(list(lines)) if s["rows"]]


class State:
    """Veyra's view at one cutoff, from the frozen pack."""

    def __init__(self, frozen: Sequence[str], orig: Sequence[int], space: Dict[str, Any], cutoff: date):
        self.cutoff = cutoff
        self.facts: List[Dict[str, Any]] = []
        self.hyp: Dict[str, Dict[str, Any]] = {}
        self.plan: Dict[str, Any] = {"recommended": None, "data_requests": []}
        self._orig = list(orig)
        lines = list(frozen)
        self.el = E.analyse(lines) if lines else None
        self.ex = X.analyse(lines) if lines else None
        dates = _snap_dates(lines)
        self.spans = _snap_spans(lines)
        self.latest = max([d for d in dates if d], default=None)
        self._build(space, lines, dates)

    def _o(self, n: Optional[int]) -> Optional[int]:
        return self._orig[n - 1] if n and 0 < n <= len(self._orig) else None

    def fact(self, text: str, tier: str, as_of: Optional[date], lines: Sequence[Optional[int]] = (), source: str = "",
             spans: Sequence[Tuple[int, int]] = ()) -> str:
        """A statement with its tier, the date it is valid for, and the original lines it rests on (single lines, or
        row ranges of the snapshots a pooled computation used)."""
        fid = f"f{len(self.facts) + 1}"
        self.facts.append({"id": fid, "text": text, "tier": tier, "as_of": as_of.isoformat() if as_of else None,
                           "lines": [x for x in (self._o(n) for n in lines) if x],
                           "line_ranges": [[self._o(a), self._o(b)] for a, b in spans], "source": source})
        return fid

    def _build(self, space: Dict[str, Any], lines: List[str], dates: List[Optional[date]]) -> None:
        el, ex = self.el or {}, self.ex or {}
        branch = (el.get("relations") or {}).get("branch") or {}
        flagged = branch.get("flagged") or []
        assess = {a["wheel"]: a for a in el.get("assessments") or []}
        bfacts: Dict[str, str] = {}
        for f in el.get("findings") or []:
            if f["relation"].startswith(("B_", "R_", "O_")):
                tier = "confirmed" if f["tier"] == "measured" else "inferred"
                bfacts[f["relation"]] = self.fact(f["text"], tier, self.latest, [], "electrical", self.spans)
        expres: Dict[str, List[Tuple[int, Dict[str, Any]]]] = {}
        for i, s in enumerate(ex.get("snapshots") or []):
            for r in s.get("results") or []:
                expres.setdefault(r["id"], []).append((i, r))
        for h in space["hypotheses"]:
            hid, maps = h["id"], h.get("maps") or {}
            st: Dict[str, Any] = {"status": "not_identifiable", "facts": [], "why": "No recorded signal can show this."}
            if "electrical" in maps:
                w, cand = maps["electrical"]["wheel"], maps["electrical"]["candidate"]
                if branch.get("status") != "ok":
                    st = {"status": "not_identifiable", "facts": [], "why": f"Branch relation: {branch.get('reason') or branch.get('status') or 'no data'}."}
                elif w in flagged and w in assess:
                    a = assess[w]
                    c = next((c for c in a["candidates"] if c["id"] == cand), None)
                    ids = [x for x in (bfacts.get(f"B_{w}"), bfacts.get(f"R_{w}")) if x]
                    if c and c["status"] == "consistent":
                        st = {"status": "supported", "facts": ids, "why": c["why"]}
                    elif c:
                        st = {"status": "weakened", "facts": ids + [self.fact(c["why"], "inferred", self.latest, [], "electrical", self.spans)], "why": c["why"]}
                else:
                    st = {"status": "no_support", "facts": [self.fact(f"{w}'s voltage drop per amp is within the healthy spread of the other wheels.", "confirmed", self.latest, [], "electrical", self.spans)],
                          "why": "No branch anomaly at this wheel in the recorded launches (a fault that appears only under other conditions is not excluded)."}
            elif "expected" in maps:
                eid, req = maps["expected"]["id"], maps["expected"].get("requires")
                rs = expres.get(eid) or []
                if req and not any(re.search(rf"\b{re.escape(req['signal'].split('.', 1)[1])}={re.escape(str(req['value']))}\b", l)
                                   for l in lines if l.split(None, 2)[1:2] and l.split(None, 2)[1] == req["signal"].split(".", 1)[0]):
                    st = {"status": "not_identifiable", "facts": [], "why": f"{req['signal']} was never {req['value']} in the recorded data, so this cannot be checked yet."}
                elif not rs:
                    st = {"status": "not_identifiable", "facts": [], "why": "No episode in the recorded data where this expectation applies."}
                else:
                    viol = [(i, r) for i, r in rs if r["status"] == "violated"]
                    if viol:
                        i, r = viol[0]
                        fr = r["first"]
                        val = f" ({fr.get('value')} against {fr.get('ref')})" if fr.get("value") is not None else ""
                        fid = self.fact(f"{r['text']}: broken at +{fr.get('rel_s')} s{val}", "confirmed", dates[i] if i < len(dates) else self.latest,
                                        [fr.get("line")], "expected")
                        st = {"status": "supported", "facts": [fid], "why": f"{r['text']} did not hold in {len(viol)} of {len(rs)} recorded launches."}
                    else:
                        fid = self.fact(f"{rs[0][1]['text']}: held in all {len(rs)} recorded launches.", "confirmed", self.latest, [], "expected", self.spans)
                        st = {"status": "no_support", "facts": [fid], "why": "The expectation held whenever it could be checked."}
                if maps.get("electrical_pack"):
                    p = next((f for f in el.get("findings") or [] if f["relation"] == "P"), None)
                    if p:
                        st["facts"].append(self.fact(p["text"], "confirmed", self.latest, [], "electrical", self.spans))
            st["observe"] = h.get("observe")
            self.hyp[hid] = st
        # recommended check: the electrical plan for a flagged wheel, mapped onto the space
        e2h = {(h["maps"]["electrical"]["wheel"], h["maps"]["electrical"]["candidate"]): h["id"] for h in space["hypotheses"] if "electrical" in (h.get("maps") or {})}
        for w in flagged:
            a = assess.get(w)
            if not a or not a.get("recommended"):
                continue
            m = next(x for x in a["measurements"] if x["id"] == a["recommended"])
            preds = {}
            for meas in a["measurements"]:
                # predicted outcome class of each hypothesis: the index of the split group its candidate falls in
                preds[meas["id"]] = {e2h[(w, cid)]: k for k, grp in enumerate(meas.get("splits") or []) for cid in grp if (w, cid) in e2h}
            self.plan["recommended"] = {"id": m["id"], "what": m["what"], "cost_tier": m["cost"], "cost_label": m["cost_label"], "strength": m["strength"],
                                        "blind_spot": m["blind_spot"], "then": a.get("then"), "wheel": w}
            self.plan["recommended"]["predicts"] = preds[m["id"]]
            self.plan["measurements"] = [{"id": x["id"], "what": x["what"], "cost_tier": x["cost"], "strength": x["strength"], "blind_spot": x["blind_spot"],
                                          "predicts": preds.get(x["id"], {})} for x in a["measurements"]]
            break
        for hid, st in self.hyp.items():
            if st["status"] == "not_identifiable" and st.get("observe"):
                self.plan["data_requests"].append({"hypothesis": hid, **st["observe"]})

    def open_set(self) -> List[str]:
        return [h for h, s in self.hyp.items() if s["status"] == "supported"]

    def to_json(self) -> Dict[str, Any]:
        return {"version": VERSION, "cutoff": self.cutoff.isoformat(), "latest_data": self.latest.isoformat() if self.latest else None,
                "facts": self.facts, "hypotheses": self.hyp, "plan": self.plan, "open": self.open_set(),
                "weakened": [h for h, s in self.hyp.items() if s["status"] == "weakened"]}


def targets(step: Dict[str, Any], space: Dict[str, Any]) -> List[str]:
    text = f"{step.get('check', '')} {step.get('part') or ''}"
    if CODE_READING.search(text) and step.get("kind", "check") == "check":
        return []                     # reading codes gathers information; it does not test a hypothesis
    return [h["id"] for h in space["hypotheses"] if any(k.lower() in text.lower() for k in h.get("keywords") or [])]


def judge_step(step: Dict[str, Any], state: Dict[str, Any], space: Dict[str, Any], prior: List[Dict[str, Any]],
               report: Dict[str, Any], spec_id: Optional[str] = None) -> Dict[str, Any]:
    """One engineer step, judged from Veyra's state at its cutoff (built from the frozen pack only)."""
    when = date.fromisoformat(step["when"]) if step.get("when") else None
    tg = targets(step, space)
    facts = {f["id"]: f for f in state["facts"]}

    def stale(fid: str) -> Optional[int]:
        f = facts[fid]
        if not (when and f["as_of"]):
            return None
        d = (when - date.fromisoformat(f["as_of"])).days
        return d if d > STALE_DAYS else None
    known = []
    for t in tg:
        h = state["hypotheses"].get(t) or {}
        for fid in h.get("facts") or []:
            f = facts[fid]
            s = stale(fid)
            known.append({"fact": fid, "text": f["text"], "tier": f["tier"], "as_of": f["as_of"], "lines": f["lines"],
                          "stale_days": s, "tier_for_now": "inferred" if s and f["tier"] == "confirmed" else f["tier"]})
    for p in prior:
        known.append({"fact": None, "text": f"Engineers reported: {p['check']} → {p['result']}" + (f" ({p['decided_by']})" if p.get("decided_by") else ""),
                      "tier": "reported", "as_of": p.get("when"), "lines": []})
    verdicts = {}
    group = [h for h in state["open"] if any(h == x["id"] for x in space["hypotheses"] if "electrical" in (x.get("maps") or {}))]
    for t in tg:
        h = state["hypotheses"].get(t) or {"status": "not_identifiable"}
        v = {"supported": "pointed_to", "weakened": "argued_against", "no_support": "no_support", "not_identifiable": "not_identifiable"}[h["status"]]
        tier = _tier_min([facts[f]["tier"] for f in h.get("facts") or []]) if h.get("facts") else "not_identifiable"
        if any(stale(f) for f in h.get("facts") or []) and tier == "confirmed":
            tier = "inferred"
        verdicts[t] = {"data_before": v, "tier": tier, "why": h.get("why"),
                       "not_separable_from": [g for g in group if g != t] if t in group and len(group) > 1 else []}
    eliminated_before = {t for p in prior if p["result"] == "not_the_issue" for t in p.get("targets") or []}
    eliminated_now = set(tg) if step["result"] == "not_the_issue" else set()
    remaining = [h["id"] for h in space["hypotheses"] if h["id"] not in eliminated_before | eliminated_now]
    rec = state["plan"].get("recommended")
    # hindsight: verified only with a confirmed, non-stale fact that excludes the target under an approved rule
    verified = step["result"] == "not_the_issue" and any(
        verdicts[t]["data_before"] == "no_support" and verdicts[t]["tier"] == "confirmed"
        and next((x for x in space["hypotheses"] if x["id"] == t), {}).get("excluded_when_relation_holds") for t in tg)
    estimated = []
    if step["result"] == "not_the_issue":
        for t in tg:
            v = verdicts[t]
            if v["not_separable_from"] and rec and rec["cost_tier"] <= 1 and (step.get("kind") in ("swap", "repair") or (step.get("cost") or 0) > 0):
                estimated.append({"kind": "cheaper_discriminating_check_existed", "check": rec["what"], "cost_tier": rec["cost_tier"], "tier": "inferred"})
            if v["data_before"] in ("argued_against", "no_support"):
                estimated.append({"kind": "data_argued_against_target", "tier": v["tier"]})
    knowledge = {"status": "unapproved", "symptom": report.get("symptom"), "machine": report.get("machine"), "applies_to": spec_id,
                 "conditions_as_of": state.get("latest_data"), "hypotheses_tested": tg, "check": step["check"], "kind": step.get("kind"),
                 "outcome": step["result"], "outcome_tier": "reported", "cost": {"minutes": step.get("minutes"), "parts": step.get("cost")},
                 "engineer_expected": step.get("why"), "decided_by": step.get("decided_by"),
                 "evidence_facts": [k["fact"] for k in known if k["fact"]], "approved_by": None}
    return {"id": step["id"], "check": step["check"], "kind": step.get("kind"), "when": step.get("when"), "targets": tg,
            "information_gathering": not tg,
            "engineer_expected": {"text": step.get("why"), "tier": "reported"} if step.get("why") else {"text": None, "tier": "not_identifiable", "note": "not recorded"},
            "known_before": known, "data_before": verdicts,
            "result_established": {"text": step["result"] + (f": {step['decided_by']}" if step.get("decided_by") else ""), "tier": "reported"},
            "alternatives_remaining": remaining, "veyra_recommended_before": rec,
            "hindsight": {"verified_avoidable": bool(verified), "estimated": estimated}, "knowledge_candidate": knowledge}


# ---------------------------------------------------------------- AI interpretation, held to the facts
AI_SPEC = {"name": "submit_step_judgment", "description": "Interpret one engineer step using only the facts given. Every claim cites fact ids.",
           "input_schema": {"type": "object", "properties": {
               "claims": {"type": "array", "items": {"type": "object", "properties": {
                   "text": {"type": "string"}, "fact_ids": {"type": "array", "items": {"type": "string"}},
                   "tier": {"type": "string", "enum": ["confirmed", "inferred", "reported", "not_identifiable"]}}, "required": ["text", "fact_ids", "tier"]}},
               "eliminates": {"type": "array", "items": {"type": "object", "properties": {"hypothesis": {"type": "string"}, "fact_ids": {"type": "array", "items": {"type": "string"}}},
                                                          "required": ["hypothesis", "fact_ids"]}}},
               "required": ["claims"]}}


def verify_ai(out: Dict[str, Any], judgment: Dict[str, Any], state: Dict[str, Any]) -> List[str]:
    """Problems with an AI interpretation: uncited claims, unknown facts, claims above their evidence, unsupported eliminations."""
    facts = {f["id"]: f for f in state["facts"]}
    stale = {k["fact"] for k in judgment["known_before"] if k.get("fact") and k.get("stale_days")}
    problems = []
    for i, c in enumerate(out.get("claims") or []):
        ids = c.get("fact_ids") or []
        if not ids and c.get("tier") != "not_identifiable":
            problems.append(f"claim {i + 1} cites no fact: {c.get('text', '')[:80]}")
            continue
        unknown = [x for x in ids if x not in facts]
        if unknown:
            problems.append(f"claim {i + 1} cites facts that do not exist: {', '.join(unknown)}")
            continue
        tiers = ["inferred" if x in stale and facts[x]["tier"] == "confirmed" else facts[x]["tier"] for x in ids]
        if ids and TIERS.index(c.get("tier", "confirmed")) > TIERS.index(_tier_min(tiers)):
            problems.append(f"claim {i + 1} is {c.get('tier')} but its weakest evidence is {_tier_min(tiers)}")
    for e in out.get("eliminates") or []:
        h = e.get("hypothesis")
        st = (state["hypotheses"].get(h) or {}).get("status")
        engineer = judgment["result_established"]["text"].startswith("not_the_issue") and h in judgment["targets"]
        if st != "weakened" and not engineer:
            problems.append(f"eliminates '{h}' without a fact that contradicts it (its status is {st or 'unknown'})")
    return problems


def ai_interpret(model, judgment: Dict[str, Any], state: Dict[str, Any], attempts: int = 2) -> Dict[str, Any]:
    """Ask the model to interpret one step from the facts; keep it only if the verifier finds no problems."""
    facts = [f for f in state["facts"]]
    msg = {"step": {k: judgment[k] for k in ("check", "kind", "when", "targets")}, "engineer_expected": judgment["engineer_expected"],
           "result_reported": judgment["result_established"], "data_before": judgment["data_before"], "facts": facts,
           "hypotheses": {h: {"status": s["status"], "why": s.get("why")} for h, s in state["hypotheses"].items()}}
    system = ("You judge one step of an engineering investigation. Use only the facts given; cite fact ids for every claim; "
              "a claim's tier may not exceed the weakest fact it cites; do not state technical rules that are not in the facts; "
              "eliminate a hypothesis only if a cited fact contradicts it or the engineers reported it was not the issue.")
    messages = [{"role": "user", "content": json.dumps(msg, ensure_ascii=False)}]
    rejected = []
    for _ in range(attempts):
        reply = model.create(system=system, messages=messages, tools=[AI_SPEC])
        call = next((c for c in reply.get("content") or [] if c.get("type") == "tool_use" and c.get("name") == "submit_step_judgment"), None)
        if not call:
            rejected.append(["no judgment submitted"])
            break
        problems = verify_ai(call["input"], judgment, state)
        if not problems:
            return {"status": "accepted", "interpretation": call["input"], "rejected": rejected}
        rejected.append(problems)
        messages += [{"role": "assistant", "content": reply["content"]},
                     {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call["id"], "content": "Rejected: " + "; ".join(problems), "is_error": True}]}]
    return {"status": "rejected", "interpretation": None, "rejected": rejected}
