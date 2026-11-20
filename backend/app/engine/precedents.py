"""Precedents: earlier closed cases that looked like this one, and what they turned out to be.

A run's SIGNATURE is the set of things the engine flagged: (part of the machine, rule, signal, scope) for every
anomaly or delay. Approved "normal for this machine" observations and routine events are not part of it.

Similarity between two signatures is a weighted Jaccard over three levels, so two cases that flag the same part
for different channels still count as related, and the same channel counts most:
    0.2 x same parts of the machine + 0.3 x same rule per part + 0.5 x same rule, signal and scope
Matches below MIN_SIMILARITY are not shown, with one exception (precedent-rules-v2): an earlier case on the same
machine (or machine type) where the operator REPORTED THE SAME SYMPTOM on the same function is shown even when the
engine's findings differ. That is the case most likely to be copied wrongly ("same complaint as last week, so same
cause"), so it is shown with what differs first, and its settling check is NOT moved up in Next Checks.

A precedent is a LEAD, never a diagnosis. It is not used by the hypothesis board, triage or root cause. What it does:
- shows the earlier conclusion with its investigation status and evidence strength, and how it was resolved;
- says what is the same and what is different from this case;
- puts the check that settled the earlier case forward in Next Checks, with the reason.
Synthetic and real cases never mix.

precedent-rules-v3 adds the machine context. Same findings on a different firmware, controller software, retrofit kit,
harness, vehicle interface or workload can have a different cause, so each match carries a context comparison over
CONTEXT_KEYS (values as resolved at each incident, reported or confirmed) and a configuration hash of those values:
- "same": every key recorded on either side has the same value on both;
- "differs": at least one key has different values -> shown with what differs; its settling check is NOT moved up;
- "unverified": some key is recorded for one case only -> shown, check NOT moved up (we can't tell it applies);
- "not_recorded": neither case records any of the keys -> shown as before, labelled "context not recorded".
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

PRECEDENT_RULES_VERSION = "precedent-rules-v3"
CONTEXT_KEYS = ("oem", "model", "kit_version", "harness_revision", "control_version", "firmware_version", "vehicle_interface", "operating_conditions")
MIN_SIMILARITY = 0.35
MAX_SHOWN = 3
WEIGHTS = (0.2, 0.3, 0.5)
FLAGGED = ("anomaly", "delay")


_STRENGTH_RANK = {"direct": 0, "indirect": 1, "circumstantial": 2, "judgement": 3}


def _neg_time(iso: Optional[str]) -> str:
    """Sort key that puts later ISO timestamps first."""
    return "".join(chr(0x10FFFF - ord(ch)) for ch in (iso or ""))


def signature(domains: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = set()
    for domain, assessment in (domains or {}).items():
        for o in assessment.get("observations", []):
            if o.get("kind") in FLAGGED:
                key = o.get("key") or {}
                out.add((domain, o["rule"], key.get("signal"), key.get("scope")))
    return [{"domain": d, "rule": r, "signal": s, "scope": sc} for d, r, s, sc in sorted(out, key=lambda x: tuple(str(v) for v in x))]


def reported(entries: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The structured symptoms operators reported in a case: [{symptom, function}] from intervention reports."""
    out = {(e["payload"].get("symptom"), e["payload"].get("affected_function")) for e in entries
           if e.get("kind") == "intervention_report" and e["payload"].get("symptom") and e["payload"]["symptom"] != "other"}
    return [{"symptom": a, "function": b} for a, b in sorted(out, key=lambda x: (x[0], x[1] or ""))]


def _same_report(now: Sequence[Dict[str, Any]], then: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Reported symptoms in both cases: same symptom, and the same function when both name one."""
    out = []
    for a in now or []:
        for b in then or []:
            if a["symptom"] == b["symptom"] and (not a.get("function") or not b.get("function") or a["function"] == b["function"]):
                item = {"symptom": a["symptom"], "function": a.get("function") or b.get("function")}
                if item not in out:
                    out.append(item)
    return out


def _parts(sig: Iterable[Dict[str, Any]], names: Dict[str, str]) -> List[str]:
    return sorted({names.get(i["domain"], i["domain"]) for i in sig})


def _levels(sig: Iterable[Dict[str, Any]]) -> Tuple[Set, Set, Set]:
    items = [(i["domain"], i["rule"], i.get("signal"), i.get("scope")) for i in sig]
    return ({i[0] for i in items}, {i[:2] for i in items}, set(items))


def _jaccard(a: Set, b: Set) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def context_values(snapshot: Optional[Dict[str, Any]]) -> Dict[str, Optional[str]]:
    """The CONTEXT_KEYS values of a resolved context snapshot (reported or confirmed, not conflicting); None when unknown."""
    attrs = (snapshot or {}).get("attributes") or {}
    out = {}
    for key in CONTEXT_KEYS:
        a = attrs.get(key) or {}
        ok = a.get("status") in ("reported", "engineer_confirmed") and a.get("value") is not None and not a.get("conflicting_values")
        out[key] = str(a["value"]).strip() if ok else None
    return out


def context_hash(values: Dict[str, Optional[str]]) -> Optional[str]:
    """Configuration hash over the recorded CONTEXT_KEYS values (None when nothing is recorded)."""
    known = {k: v.casefold() for k, v in values.items() if v is not None}
    return hashlib.sha256(json.dumps(known, sort_keys=True).encode()).hexdigest()[:16] if known else None


def compare_context(now: Optional[Dict[str, Any]], then: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    a, b = context_values(now), context_values(then)
    differs = [{"key": k, "now": a[k], "then": b[k]} for k in CONTEXT_KEYS if a[k] is not None and b[k] is not None and a[k].casefold() != b[k].casefold()]
    one_side = [k for k in CONTEXT_KEYS if (a[k] is None) != (b[k] is None)]
    status = ("differs" if differs else "unverified" if one_side else "same" if any(v is not None for v in a.values()) else "not_recorded")
    return {"status": status, "differs": differs, "recorded_for_one_case_only": one_side,
            "hash_now": context_hash(a), "hash_then": context_hash(b), "same_hash": context_hash(a) is not None and context_hash(a) == context_hash(b)}


def similarity(a: Sequence[Dict[str, Any]], b: Sequence[Dict[str, Any]]) -> float:
    la, lb = _levels(a), _levels(b)
    return round(sum(w * _jaccard(x, y) for w, x, y in zip(WEIGHTS, la, lb)), 3)


RULE_WORDS = {
    "R-SEGMENT-RELATIVE": "command delay grew in one stretch of the command chain", "R-SEGMENT": "delay in one stretch of the command chain",
    "R-IO": "response to its input changed", "R-TRACKING": "stopped following its command", "R-OUTPUT-STUCK": "output stuck",
    "R-OUTPUT-MIRROR": "outputs mirroring each other", "R-HEARTBEAT": "heartbeat gap", "R-EVENT": "event recorded",
    "R-REFERENCE": "differs from the known-good window", "R-REFERENCE-COMMANDED": "differs from the known-good window",
    "R-SCREEN": "changed inside the symptom window", "R-BRIEF": "brief event",
}


def _describe(item: Tuple, names: Dict[str, str]) -> str:
    domain, rule, signal, scope = item
    where = f" on {signal}" + (f" ({scope})" if scope else "") if signal else ""
    words = RULE_WORDS.get(rule) or (RULE_WORDS["R-OUTPUT-STUCK"] if rule.startswith("R-OUTPUT") else None)
    return f"{names.get(domain, domain)}: {words + ' ' if words else ''}{'(' + rule + ')' if words else rule}{where}"


def match(sig_now: Sequence[Dict[str, Any]], candidates: Sequence[Dict[str, Any]], *, machine_id: str, profile_id: str,
          synthetic: bool, names: Optional[Dict[str, str]] = None, reported_now: Optional[Sequence[Dict[str, Any]]] = None,
          status_now: Optional[Dict[str, str]] = None, context_now: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """status_now: this run's status per part of the machine, to say what this case shows where the earlier one found its problem."""
    """candidates: closed earlier cases, each {case_id, title, machine_id, profile_id, synthetic, signature, closure, reported?}."""
    names = names or {}
    now = _levels(sig_now)[2]
    matches = []
    for c in candidates:
        if bool(c.get("synthetic")) != bool(synthetic) or not c.get("closure"):
            continue
        same_machine = c.get("machine_id") == machine_id
        if not same_machine and c.get("profile_id") != profile_id:
            continue
        score = similarity(sig_now, c.get("signature") or [])
        same_report = _same_report(reported_now or [], c.get("reported") or [])
        by_findings = score >= MIN_SIMILARITY
        if not by_findings and not same_report:
            continue
        then = _levels(c.get("signature") or [])[2]
        cl = c["closure"]
        ctx = compare_context(context_now, c.get("context"))
        parts_now, parts_then = _parts(sig_now, names), _parts(c.get("signature") or [], names)
        matches.append({
            "case_id": c["case_id"], "title": c.get("title"), "machine_id": c.get("machine_id"), "same_machine": same_machine,
            "similarity": score,
            "match_basis": "findings and reported symptom" if by_findings and same_report else "findings" if by_findings else "reported symptom only",
            "same_reported_symptom": same_report, "context": ctx,
            "parts_now": parts_now, "parts_then": parts_then,
            "evidence_points_elsewhere": not set(parts_now) & set(parts_then) if parts_now and parts_then else None,
            "earlier_parts_now": [{"part": names.get(d, d), "status_now": (status_now or {}).get(d)}
                                  for d in sorted({i["domain"] for i in c.get("signature") or []}) if status_now and d in status_now],
            "same": [_describe(i, names) for i in sorted(now & then, key=str)],
            "only_now": [_describe(i, names) for i in sorted(now - then, key=str)],
            "only_then": [_describe(i, names) for i in sorted(then - now, key=str)],
            "closure": {k: cl.get(k) for k in ("investigation_status", "cause_domain", "cause", "confirmed_by", "evidence_strength", "resolution_status",
                                               "fix", "fix_held", "decisive_check", "hours_to_cause", "would_have_saved_time", "closed_by", "closed_at")},
        })
    # Ties (common when a case flags only one or two things) go to the same machine, then the strongest evidence
    # behind the earlier conclusion, then the most recently closed. The rest are counted, not hidden silently.
    matches.sort(key=lambda m: (m["match_basis"] == "reported symptom only", -m["similarity"], not m["same_machine"], _STRENGTH_RANK.get(m["closure"].get("evidence_strength"), 9),
                                _neg_time(m["closure"].get("closed_at")), m["case_id"]))
    shown = matches[:MAX_SHOWN]
    checks: Dict[str, Dict[str, Any]] = {}
    for m in shown:
        check = m["closure"].get("decisive_check")
        if m["match_basis"] == "reported symptom only":
            # Same complaint, different findings: moving the earlier case's check up would carry its cause over.
            m["check_not_moved_up"] = ("Not moved up in Next checks: this case's findings differ from the earlier case's"
                                       if check else None)
            continue
        if check and m["context"]["status"] in ("differs", "unverified"):
            # A different (or unknown) firmware, software, kit or workload: the earlier settling check may not apply here.
            what = ", ".join(f"{d['key'].replace('_', ' ')} {d['then']} then vs {d['now']} now" for d in m["context"]["differs"]) or \
                   "recorded for one case only: " + ", ".join(k.replace("_", " ") for k in m["context"]["recorded_for_one_case_only"])
            m["check_not_moved_up"] = f"Not moved up in Next checks: machine context {'differs' if m['context']['differs'] else 'cannot be compared'} ({what})"
            continue
        if check and check not in checks:
            checks[check] = {"case_id": m["case_id"], "title": m["title"], "similarity": m["similarity"]}
    return {"rules_version": PRECEDENT_RULES_VERSION, "rule": (f"weighted Jaccard {WEIGHTS} over part / rule / rule+signal+scope; shown when >= {MIN_SIMILARITY}, "
                     "or when an operator reported the same symptom on the same function (then shown with what differs, and its check not moved up); "
                     f"a settling check is moved up only when the machine context ({', '.join(CONTEXT_KEYS)}) is the same or recorded for neither case"),
            "candidates": len(candidates), "matches": shown, "not_shown": len(matches) - len(shown), "decisive_checks": checks,
            "boundary": "Similar earlier cases are leads, not a diagnosis. Their causes are not applied to this case; this case's own evidence decides."}
