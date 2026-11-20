"""Evidence beyond telemetry: software versions, controller configuration, hardware revisions and intervention history.

Every artifact carries `available_from`, the moment it could first have been read by the people investigating (an OTA
record when it was pushed, a calibration file when it was released, a build record, a service entry when it was
written). The same leakage rules as for logs apply: an artifact enters a decision point only if it was available
before the cutoff; its text is audited for later dates, outcome words and sealed phrases; an intervention's result is
shown only from the date it became known.

The deterministic tools here relate artifacts to the hypothesis space only through rules the space states
(engineer-approved for real cases): a configuration comparison, a version window, an affected-revision list, or work
done on a component. They never invent a technical rule; a hypothesis with no rule is untouched.
"""
from __future__ import annotations

import operator
import re
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.agent import timeline as T

KINDS = ("software_version", "controller_config", "hardware_revision", "intervention", "note")
OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le, "==": operator.eq, "!=": operator.ne}
RECENT_WORK_DAYS = 30
FREE_TEXT = ("text", "note", "action", "decided_by", "source")


def _d(s: Optional[str]) -> Optional[date]:
    return T.parse_date(s) if s else None


def validate(artifacts: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    issues, ids = [], set()
    for a in artifacts:
        aid = a.get("id")
        if not aid or aid in ids:
            issues.append({"severity": "error", "artifact": aid, "issue": "missing or duplicate id"})
        ids.add(aid)
        if a.get("kind") not in KINDS:
            issues.append({"severity": "error", "artifact": aid, "issue": f"kind must be one of {KINDS}"})
        if not a.get("available_from"):
            issues.append({"severity": "error", "artifact": aid, "issue": "no available_from: every artifact needs the moment it could first be read"})
        if a.get("kind") == "intervention" and a.get("result") and not (a.get("result_known") or a.get("when")):
            issues.append({"severity": "error", "artifact": aid, "issue": "an intervention with a result needs result_known (or when)"})
        if a.get("valid_from") and a.get("available_from") and _d(a["valid_from"]) > _d(a["available_from"]):
            issues.append({"severity": "warning", "artifact": aid, "issue": "valid_from is after available_from"})
    return issues


def _text(a: Dict[str, Any]) -> str:
    skip = {"id", "kind", "available_from", "result", "result_known", "source"}
    out = []
    for k, v in a.items():
        if k in skip:
            continue
        out.append(" ".join(f"{kk} {vv}" for kk, vv in v.items()) if isinstance(v, dict) else str(v))
    return " ".join(out)


def freeze(artifacts: Sequence[Dict[str, Any]], cutoff: date) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Artifacts available before the cutoff, with intervention results hidden until known."""
    kept, dropped = [], []
    for a in artifacts:
        af = _d(a.get("available_from"))
        if af is None or af >= cutoff:
            dropped.append({"artifact": a.get("id"), "reason": "not available yet" if af else "no available_from"})
            continue
        b = dict(a)
        if b.get("kind") == "intervention" and b.get("result") is not None:
            known = _d(b.get("result_known") or b.get("when"))
            if known is None or known >= cutoff:
                b["result"], b["decided_by"] = "pending", None
                b.pop("result_known", None)
        # free text that names a later date was edited after the fact: drop the artifact, as for log comments
        later = [d for d in T._dates(_text(b)) if d >= cutoff]
        if later:
            dropped.append({"artifact": a.get("id"), "reason": "names a date on or after the cutoff"})
            continue
        if b.get("kind") == "note" and T.OUTCOME.search(_text(b)):
            dropped.append({"artifact": a.get("id"), "reason": "outcome word in an undated note"})
            continue
        kept.append(b)
    return kept, {"cutoff": cutoff.isoformat(), "kept": [a["id"] for a in kept], "dropped": dropped}


def leak_check(frozen: Sequence[Dict[str, Any]], cutoff: date, phrases: Sequence[str] = ()) -> List[Dict[str, Any]]:
    leaks = []
    for a in frozen:
        af = _d(a.get("available_from"))
        if af is None or af >= cutoff:
            leaks.append({"artifact": a.get("id"), "kind": "artifact not available before the cutoff"})
        txt = _text(a) + " " + str(a.get("decided_by") or "")
        if any(d >= cutoff for d in T._dates(txt)):
            leaks.append({"artifact": a.get("id"), "kind": "artifact names a later date"})
        if a.get("kind") == "intervention" and a.get("result") not in (None, "pending"):
            known = _d(a.get("result_known") or a.get("when"))
            if known is None or known >= cutoff:
                leaks.append({"artifact": a.get("id"), "kind": "intervention result shown before it was known"})
        if a.get("kind") == "note" and T.OUTCOME.search(_text(a)):
            leaks.append({"artifact": a.get("id"), "kind": "outcome word in an undated note"})
        # sealed phrases are looked for in free text (what someone wrote), not in identity fields (component, part,
        # revision, version): an inventory record that names a component is not the answer, just as the hypothesis
        # names in the space are not
        low = " ".join(str(a.get(k) or "") for k in FREE_TEXT).lower()
        for p in phrases:
            if p.lower() in low:
                leaks.append({"artifact": a.get("id"), "kind": "sealed phrase", "phrase": p})
    return leaks


def _version_tuple(v: str) -> Tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", str(v)))


def assess(frozen: Sequence[Dict[str, Any]], space: Dict[str, Any], cutoff: date, onset: Optional[date]) -> Dict[str, List[Dict[str, Any]]]:
    """Per hypothesis id: facts from artifacts, each {text, tier, as_of, artifact, effect: supports|argues_against|context}.

    Rules come only from the hypothesis's `artifacts` map:
      config:   {"param": P, "op": ">", "ref_param": Q, "component": C}     compare two released parameters
      version:  {"component": C, "from": "2.4.1"}                          the hypothesis applies from that version on
      hardware: {"component": C, "affected": ["rev A"]}                    installed revision in an affected list
      work_on:  [keywords]                                                 recent intervention on the component
    A rule with its own available_from is used only at cutoffs after it.
    """
    out: Dict[str, List[Dict[str, Any]]] = {}
    by_kind: Dict[str, List[Dict[str, Any]]] = {}
    for a in frozen:
        by_kind.setdefault(a["kind"], []).append(a)

    def latest(kind: str, component: str) -> Optional[Dict[str, Any]]:
        xs = [a for a in by_kind.get(kind, []) if a.get("component") == component and (_d(a.get("valid_from")) or _d(a["available_from"])) < cutoff]
        return max(xs, key=lambda a: _d(a.get("valid_from")) or _d(a["available_from"])) if xs else None

    for h in space["hypotheses"]:
        # a rule is used only from the date it was known (a supplier bulletin, a released spec): a hypothesis space
        # written after the case must not carry later knowledge back into it
        rules = {k: r for k, r in ((h.get("maps") or {}).get("artifacts") or {}).items()
                 if not (isinstance(r, dict) and r.get("available_from") and _d(r["available_from"]) >= cutoff)}
        facts = []
        c = rules.get("config")
        if c:
            cfg = latest("controller_config", c["component"])
            if cfg:
                params = cfg.get("params") or {}
                p, q = params.get(c["param"]), params.get(c["ref_param"])
                if p is not None and q is not None:
                    holds = OPS[c["op"]](float(p), float(q))
                    facts.append({"text": f"{cfg['component']} configuration {cfg.get('version', '')}: {c['param']} = {p} {'is' if holds else 'is not'} {c['op']} {c['ref_param']} = {q}",
                                  "tier": "confirmed", "as_of": cfg.get("valid_from") or cfg["available_from"], "artifact": cfg["id"],
                                  "effect": "supports" if holds else "argues_against"})
        v = rules.get("version")
        if v:
            sw = latest("software_version", v["component"])
            if sw:
                applies = _version_tuple(sw["version"]) >= _version_tuple(v["from"])
                vf = _d(sw.get("valid_from")) or _d(sw["available_from"])
                before_onset = onset is None or vf <= onset
                facts.append({"text": f"{v['component']} runs {sw['version']} since {vf}" + (f", {'before' if before_onset else 'after'} the first reported symptom ({onset})" if onset else "")
                                      + ("" if applies else f"; the hypothesis needs {v['from']} or later"),
                              "tier": "inferred" if applies else "confirmed",   # a version window is necessary, not sufficient
                              "as_of": vf.isoformat(), "artifact": sw["id"],
                              "effect": "context" if applies and before_onset else "argues_against"})
        hw = rules.get("hardware")
        if hw:
            rev = latest("hardware_revision", hw["component"])
            if rev:
                hit = rev.get("revision") in (hw.get("affected") or [])
                facts.append({"text": f"{hw['component']} installed revision {rev.get('revision')}" + (" is in the affected list" if hit else " is not in the affected list"),
                              "tier": "inferred", "as_of": rev.get("valid_from") or rev["available_from"], "artifact": rev["id"],
                              "effect": "supports" if hit else "context"})
        kws = rules.get("work_on") or []
        for a in by_kind.get("intervention", []):
            when = _d(a.get("when"))
            txt = f"{a.get('action', '')} {a.get('part', '')}"
            if kws and when and any(k.lower() in txt.lower() for k in kws) and onset and 0 <= (onset - when).days <= RECENT_WORK_DAYS:
                facts.append({"text": f"Work on it {(onset - when).days} days before the first symptom: {txt.strip()} ({when})",
                              "tier": "inferred", "as_of": when.isoformat(), "artifact": a["id"], "effect": "supports"})
            if kws and when and any(k.lower() in txt.lower() for k in kws) and a.get("result") == "not_the_issue":
                facts.append({"text": f"Intervention on it reported not the issue: {txt.strip()} ({when})" + (f": {a['decided_by']}" if a.get("decided_by") else ""),
                              "tier": "reported", "as_of": (a.get("result_known") or a.get("when")), "artifact": a["id"], "effect": "reported_not_the_issue"})
        if facts:
            out[h["id"]] = facts
    return out


def merge(state: Dict[str, Any], art: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Add artifact facts to a state from step_judge.State.to_json(). Status changes only by stated rules:
    a confirmed 'supports' makes a not-identifiable or no-support hypothesis supported; a confirmed 'argues_against'
    makes a supported hypothesis weakened only if no confirmed telemetry fact supports it. Inferred facts are attached
    as context and never change a status. Nothing is eliminated."""
    st = {**state, "facts": list(state["facts"]), "hypotheses": {k: dict(v, facts=list(v["facts"])) for k, v in state["hypotheses"].items()}}
    for hid, facts in art.items():
        h = st["hypotheses"].get(hid)
        if h is None:
            continue
        for f in facts:
            fid = f"a{len([x for x in st['facts'] if x['id'].startswith('a')]) + 1}"
            st["facts"].append({"id": fid, "text": f["text"], "tier": f["tier"], "as_of": f["as_of"], "lines": [], "line_ranges": [],
                                "artifact": f["artifact"], "source": "artifact", "effect": f["effect"]})
            h["facts"].append(fid)
            if f["tier"] == "confirmed" and f["effect"] == "supports" and h["status"] in ("not_identifiable", "no_support"):
                h["status"], h["why"] = "supported", f["text"]
            elif f["tier"] == "confirmed" and f["effect"] == "argues_against" and h["status"] == "supported":
                tele = [x for x in st["facts"] if x["id"] in h["facts"] and x["source"] != "artifact" and x["tier"] == "confirmed"]
                if not tele:
                    h["status"], h["why"] = "weakened", f["text"]
    st["open"] = [k for k, v in st["hypotheses"].items() if v["status"] == "supported"]
    st["weakened"] = [k for k, v in st["hypotheses"].items() if v["status"] == "weakened"]
    st["plan"] = {**st["plan"], "data_requests": [r for r in st["plan"].get("data_requests") or [] if st["hypotheses"][r["hypothesis"]]["status"] == "not_identifiable"]}
    st["evidence_model"] = "v2: telemetry + artifacts"
    return st
