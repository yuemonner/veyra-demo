"""Next action: from an investigation to a dispatch decision, in three steps.

1. assess   What the logs support, and which candidate explanations they cannot tell apart.
2. plan     The one or two remote checks that best split those candidates. A person approves each one; people
            at the site carry it out and report the result.
3. decide   With the site's results in, a dispatch readiness report: what can be done now, what cannot yet,
            and why, every line tied to a record.

All rules are written here and versioned. Nothing is decided automatically: the report prepares a human
decision; it never approves a dispatch or names a failed part that nobody has confirmed.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

POLICY = "next-action-v2"  # v2: neutral outcome wording (no presumed events), drive logs, localized-departure rule, explicit check comparison
EVIDENCE = ("delay_observed", "anomaly_observed")

# What each named outcome of a check would say about each part. These are written, reviewable assumptions, not
# validated probabilities. Labels describe what the site would FIND; they never presume an event the case hasn't shown.
SPLITS: Dict[str, Dict[str, Dict[str, Any]]] = {
    "CHK-CONTROLLER-LOGS": {
        "controller_action_first": {"label": "The controller recorded a fault, protective stop, mode change or output limit before the problem started",
                                    "supports": ["control_suite"], "weakens": ["network"]},
        "link_loss_first": {"label": "The controller recorded a lost operator link before the problem started", "supports": ["network"], "weakens": ["control_suite"]},
        "nothing_before": {"label": "Nothing relevant recorded before the problem started (the log covers the window)", "supports": [], "weakens": ["control_suite", "network"]},
    },
    "CHK-DRIVE-LOGS": {
        "drive_hw_fault": {"label": "The drive recorded a hardware fault (temperature, encoder or power) before the problem started",
                           "supports": ["vehicle_interface"], "weakens": []},
        "drive_no_hw_fault": {"label": "The drive recorded only the tracking error, no hardware fault (the log covers the window)", "supports": [], "weakens": ["vehicle_interface"]},
    },
    "CHK-NETWORK-LOGS": {
        "network_event": {"label": "Link drops or handovers during the problem", "supports": ["network"], "weakens": []},
        "no_event": {"label": "No link events during the problem (logs cover the whole window)", "supports": [], "weakens": ["network"]},
    },
    "CHK-PROBE-PATH": {
        "same_path": {"label": "The probe uses the command path", "supports": ["network"], "weakens": []},
        "different_path": {"label": "The probe uses a different path", "supports": [], "weakens": ["network"]},
    },
    "CHK-IO-BASELINE": {
        "unchanged": {"label": "The machine responds to received commands as before", "supports": [], "weakens": ["vehicle_interface", "vehicle_body"]},
        "changed": {"label": "The machine responds differently than before", "supports": ["vehicle_interface", "vehicle_body"], "weakens": []},
    },
}
# Earlier outcome names (next-action-v1), still read so older records keep their meaning.
ALIASES = {"CHK-CONTROLLER-LOGS": {"application_fault": "controller_action_first", "external_trigger": "link_loss_first"}}


def outcome_of(check_id: str, key: Optional[str]) -> Optional[Dict[str, Any]]:
    key = ALIASES.get(check_id, {}).get(key or "", key or "")
    return SPLITS.get(check_id, {}).get(key)


YIELDS_RECORD = {"CHK-CONTROLLER-LOGS", "CHK-NETWORK-LOGS", "CHK-DRIVE-LOGS"}  # produces a log the case can keep, not only an answer
REMOTE_PARTS = {"network", "control_suite"}                  # can be changed without travelling
PHYSICAL_PARTS = {"vehicle_interface", "vehicle_body"}       # needs hands on the machine
REMOTE_FIX = {"network": "Change the link remotely (for example pin the cell or the access point) and watch the next shift",
              "control_suite": "Restart or roll back the control software remotely and watch the next shift"}


def _label(run: Dict[str, Any], domain: str) -> str:
    return ((run.get("machine_profile") or {}).get("domains") or {}).get(domain, {}).get("label") or domain.replace("_", " ")


def _owner(run: Dict[str, Any], domain: str) -> str:
    return ((run.get("machine_profile") or {}).get("domains") or {}).get(domain, {}).get("owner") or _label(run, domain)


def _evidence_lines(run: Dict[str, Any], domain: str, most: int = 3) -> List[str]:
    found = [o for o in run["domains"].get(domain, {}).get("observations", []) if o.get("kind") in ("anomaly", "delay")]
    found.sort(key=lambda o: 0 if o.get("kind") == "delay" else 1)  # a measured delay says more than a screening flag
    return [o["text"] for o in found][:most]


def site_results(ledger: List[Dict[str, Any]], run_id: str) -> Dict[str, Dict[str, Any]]:
    """Site results for this run, plus performed results from earlier runs of the same case (a site result stays a
    fact after the case is re-investigated). Earlier ones are marked; a result for this run replaces an earlier one."""
    out: Dict[str, Dict[str, Any]] = {}
    for e in ledger:
        if e["kind"] != "check_execution":
            continue
        p = e["payload"]
        here = p.get("run_id") == run_id
        if here or p.get("status") == "performed":
            if here or not out.get(p["check_id"], {}).get("this_run"):
                out[p["check_id"]] = {**p, "entry_id": e["id"], "this_run": here}
    return out


# Signals that describe how the machine as a whole moved. A change in them is usually the symptom itself (a limping
# robot walks slower), so on their own they don't implicate the part they are filed under.
EFFECT_SIGNALS = {"vehicle.velocity", "vehicle.ground_speed", "vehicle.yaw_rate", "vehicle.airspeed", "vehicle.altitude", "vehicle.attitude"}


def _effect_only(run: Dict[str, Any], domain: str) -> bool:
    found = [o for o in run["domains"][domain].get("observations", []) if o.get("kind") in ("anomaly", "delay")]
    return bool(found) and all(o.get("kind") == "anomaly" and ((o.get("key") or {}).get("signal") in EFFECT_SIGNALS) for o in found)


def _localized(run: Dict[str, Any]) -> Optional[str]:
    axes = [a for a in ((run.get("findings") or {}).get("tracking") or {}).get("axes", []) if a.get("pair") == "joint_position"]
    departed = [a["axis"] for a in axes if a.get("status") == "departed"]
    normal = [a["axis"] for a in axes if a.get("status") == "tracking_within_baseline"]
    if len(departed) == 1 and len(normal) >= 2:
        return (f"Only {departed[0]} stopped following its commands while {len(normal)} other joints tracked normally; "
                "a lost operator link would usually affect every joint")
    return None


# ---------- 1. assess ----------
def assess(run: Dict[str, Any]) -> Dict[str, Any]:
    board = {b["domain"]: b for b in run.get("hypotheses") or []}
    open_parts = [d for d in run["domains"] if (board.get(d) or {}).get("column") != "ruled_out"]
    effects = [d for d in open_parts if run["domains"][d]["status"] in EVIDENCE and _effect_only(run, d)
               and any(run["domains"][x]["status"] in EVIDENCE and not _effect_only(run, x) for x in open_parts if x != d)]
    with_evidence = [d for d in open_parts if run["domains"][d]["status"] in EVIDENCE and d not in effects]
    unmeasured = [d for d in open_parts if run["domains"][d]["status"] == "not_observed"]
    quiet = [d for d in open_parts if run["domains"][d]["status"] == "no_delay_observed_in_measured_part"]
    # Comparative evidence: one joint departed while the others tracked normally. A lost operator link reaches every
    # joint, so an unmeasured link becomes less likely (not excluded). The controller stays open: it can limit one joint.
    local = _localized(run)
    less = [{"part": d, "label": _label(run, d), "why": "No delay where it was measured"} for d in quiet]
    if local and "network" in unmeasured:
        unmeasured.remove("network")
        less.append({"part": "network", "label": _label(run, "network"), "why": local})
    supported = [{"part": d, "label": _label(run, d), "status": run["domains"][d]["status"], "evidence": _evidence_lines(run, d)} for d in with_evidence]
    candidates = with_evidence if len(with_evidence) >= 2 else with_evidence + unmeasured
    if len(candidates) >= 2:
        why = ("Both show something wrong in the same window, and no record shows which came first or which caused the other."
               if len(with_evidence) >= 2 else "Only one part shows evidence, but the others were not measured, so they are not excluded.")
    elif candidates:
        why = "One part explains what was recorded, and nothing else is left open by the data."
    else:
        why = "Nothing in the logs points at a part yet."
    return {"policy": POLICY, "run_id": run["id"], "supported": supported,
            "cannot_tell_apart": [{"part": d, "label": _label(run, d)} for d in candidates] if len(candidates) >= 2 else [],
            "why_not_separable": why, "less_likely": less,
            "effects": [{"part": d, "label": _label(run, d), "evidence": _evidence_lines(run, d, 1),
                         "why": "Only shows how the machine moved (likely the symptom itself), not a fault in this part"} for d in effects],
            "ruled_out": [{"part": d, "label": _label(run, d), "by": (board[d].get("human") or {}).get("by")} for d in run["domains"] if (board.get(d) or {}).get("column") == "ruled_out"]}


# ---------- 2. plan ----------
ACCESS = {"ready_automatic": (0, "Veyra runs it"), "ready_remote": (1, "remote, by a person"), "needs_context": (2, "remote, after confirming context"),
          "needs_authorization": (2, "remote, needs the site owner's permission"), "needs_data": (3, "needs data first"), "onsite_required": (4, "on site")}
DISRUPTION = {"none": 0, "low": 1, "high": 2}


def _scores(run: Dict[str, Any], candidates: List[str], results: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, int]:
    score = {d: (1 if run["domains"][d]["status"] in EVIDENCE else 0) for d in candidates}
    for cid, r in (results or {}).items():
        o = outcome_of(cid, r.get("outcome"))
        if r.get("status") == "performed" and o:
            for d in o["supports"]:
                if d in score:
                    score[d] += 2
            for d in o["weakens"]:
                if d in score:
                    score[d] -= 2
    return score


def _top(score: Dict[str, int]) -> Optional[str]:
    ranked = sorted(score.items(), key=lambda kv: -kv[1])
    return ranked[0][0] if ranked and (len(ranked) == 1 or ranked[0][1] > ranked[1][1]) else None


def compare(run: Dict[str, Any], assessment: Dict[str, Any], results: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Every check that could inform the open explanations, compared on stated criteria. Nothing here is validated
    against outcomes yet: these are the reasons Veyra uses, shown so a person can disagree."""
    candidates = [c["part"] for c in assessment["cannot_tell_apart"]]
    c = set(candidates)
    base = _scores(run, candidates, results)
    now = _top(base)
    present = {x.get("signal") for x in (run.get("findings") or {}).get("coverage") or []}
    rows = []
    for check in run.get("checks") or []:
        cid = check["check_id"]
        if cid not in SPLITS or check.get("previously_performed") or check["availability"]["state"] in ("answered_in_run", "not_applicable"):
            continue
        outcomes, changes, settles, travel = [], [], [], []
        for key, o in SPLITS[cid].items():
            up, down = set(o["supports"]) & c, set(o["weakens"]) & c
            trial = dict(base)
            for d in up:
                trial[d] += 2
            for d in down:
                trial[d] -= 2
            moved = _top(trial) != now
            if moved:
                changes.append(o["label"])
                if now and _top(trial) and ((now in REMOTE_PARTS) != (_top(trial) in REMOTE_PARTS)):
                    travel.append(o["label"])  # the likelier part moves between "fixable remotely" and "needs hands on the machine"
            if len(up) == 1 and down >= c - up and len(c) > 1:
                settles.append(o["label"])
            meaning = "; ".join(x for x in ([f"{', '.join(_label(run, d) for d in sorted(up))} more likely"] if up else [])
                                + ([f"{', '.join(_label(run, d) for d in sorted(down))} less likely"] if down else []))
            outcomes.append({"key": key, "label": o["label"], "meaning": meaning or "doesn't change which explanation is more likely"})
        informs = sorted({d for o in SPLITS[cid].values() for d in o["supports"] + o["weakens"]} & c)
        access = ACCESS.get(check["availability"]["state"], (5, check["availability"]["state"]))
        note = None
        if cid == "CHK-DRIVE-LOGS" and "actuator.status" in present:
            note = "The recording already has the drive's status messages; the drive's own log adds temperature, encoder and power errors."
        if cid == "CHK-CONTROLLER-LOGS" and not ({"controller.reset", "controller.event", "app.heartbeat", "fault.code"} & present):
            note = "No controller events are in the recording, so the log is new information, not a re-read."
        rows.append({"check_id": cid, "question": check["question"], "who": check.get("who"), "why": check.get("why"),
                     "availability": check["availability"]["state"], "access": access[1], "disruption": check.get("disruption"),
                     "on_site": check["availability"]["state"] == "onsite_required", "done": bool((results or {}).get(cid, {}).get("status") == "performed"),
                     "informs": [_label(run, d) for d in informs], "could_change": changes, "could_change_travel": travel, "settles_alone": settles,
                     "settles_note": (f"Under Veyra's written (not yet validated) rules, one result would settle it: {settles[0]}" if settles
                                      else "No single result settles it alone"),
                     "outcomes": outcomes, "note": note,
                     "_key": (0 if travel else 1, 0 if changes else 1, -len(informs), access[0], DISRUPTION.get(check.get("disruption"), 1),
                              0 if cid in YIELDS_RECORD else 1, check.get("rank", 99))})
    rows.sort(key=lambda r: r["_key"])
    for r in rows:  # same on every stated criterion: the order between them is arbitrary, and said so
        r["tied_with"] = [x["check_id"] for x in rows if x is not r and x["_key"][:5] == r["_key"][:5]]
    for i, r in enumerate(rows, 1):
        r["position"] = i
        r["ranking_basis"] = [("could change whether someone needs to travel" if r["could_change_travel"]
                               else "could change which explanation is more likely" if r["could_change"] else "would not change which explanation is more likely"),
                              f"informs {len(r['informs'])} open explanation(s)", r["access"], f"disruption {r['disruption'] or 'unknown'}"]
        del r["_key"]
    return rows


def plan(run: Dict[str, Any], assessment: Dict[str, Any], most: int = 2, results: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """The checks to ask the site for, in one request: the best-compared ones that could change the decision, remote
    only (going on site is the decision being prepared), at most `most`, with different effects. Asking for two at once
    is deliberate: when no single result settles it, waiting for one answer before asking for the next costs a round trip."""
    picked, seen = [], set()
    for row in compare(run, assessment, results):
        if row["on_site"] or row["done"] or not row["could_change"] or len(picked) >= most:
            continue
        effect = frozenset((o["key"], o["meaning"]) for o in row["outcomes"])
        if effect in seen:
            continue
        seen.add(effect)
        picked.append({**{k: row[k] for k in ("check_id", "question", "who", "why", "availability", "outcomes", "informs", "could_change", "could_change_travel",
                                              "settles_alone", "settles_note", "note", "access", "position", "tied_with")},
                       "split_score": len(row["could_change"]),
                       "needs": "A person approves it; someone at the site carries it out and sends the result."})
    return picked


def c_rank(check: Dict[str, Any]) -> Tuple[int, int]:
    return (0 if check["check_id"] in YIELDS_RECORD else 1, check.get("rank", 99))


# ---------- 3. decide ----------
def decide(run: Dict[str, Any], assessment: Dict[str, Any], proposed: List[Dict[str, Any]], results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """results: check_execution payloads (with 'outcome') of the proposed checks, plus their ledger ids."""
    candidates = [c["part"] for c in assessment["cannot_tell_apart"]] or [s["part"] for s in assessment["supported"]]
    score = {d: (1 if run["domains"][d]["status"] in EVIDENCE else 0) for d in candidates}
    why: List[Dict[str, str]] = []
    for d in candidates:
        for line in _evidence_lines(run, d, 1):
            why.append({"text": f"{_label(run, d)}: {line}", "source": f"investigation run {run['id']}"})
    done = {}
    pointed = set()  # parts that some site result made more likely
    for r in results:
        outcome = outcome_of(r["check_id"], r.get("outcome"))
        if r.get("status") != "performed" or not outcome:
            continue
        done[r["check_id"]] = r
        for d in outcome["supports"]:
            if d in score:
                score[d] += 2
                pointed.add(d)
        for d in outcome["weakens"]:
            if d in score:
                score[d] -= 2
        why.append({"text": f"Site result for {r['check_id']}: {outcome['label']}. {r.get('observations', '')}".strip(),
                    "source": f"ledger {r.get('entry_id')} · {r.get('performer', 'site')}" + ("" if r.get("this_run", True) else " · from an earlier investigation")})
    ranked = sorted(score.items(), key=lambda kv: -kv[1])
    lead, margin = (ranked[0][0], ranked[0][1] - (ranked[1][1] if len(ranked) > 1 else 0)) if ranked else (None, 0)
    pending = [p for p in proposed if p["check_id"] not in done]
    can, cannot = [], []
    disagree = len(pointed) > 1
    if lead and margin >= 2 and not disagree:
        if lead in REMOTE_PARTS:
            status = "fix_remotely_first"
            headline = f"Points to {_label(run, lead)}. Try the remote fix first; no one needs to travel yet."
            can.append({"text": REMOTE_FIX[lead], "who": _owner(run, lead)})
            can.append({"text": "Keep a technician on standby only if the fix does not hold", "who": "Service lead"})
            cannot.append({"text": "Send a technician to the machine", "why": f"The evidence points to {_label(run, lead)}, which can be changed remotely."})
        else:
            status = "dispatch_candidate"
            headline = f"Points to {_label(run, lead)}. A visit is likely needed; prepare it."
            can.append({"text": f"Prepare a visit by {_owner(run, lead)}", "who": "Service lead"})
            for t in (run.get("dispatch") or {}).get("candidate_preparation", [])[:3]:
                can.append({"text": f"Bring: {t['item']}", "who": "Technician"})
    else:
        status = "hold_for_evidence"
        headline = ("The site results point to different parts. Don't send anyone yet; look at both results together."
                    if disagree else "Can't tell yet. Get the site result before deciding.")
        for p in pending:
            can.append({"text": f"Get the result of: {p['question']}", "who": p.get("who") or "Site"})
        cannot.append({"text": "Choose between " + " and ".join(_label(run, d) for d in candidates),
                       "why": "Site results point to both." if disagree else "The data does not separate them yet."})
    cannot.append({"text": "Name a failed part", "why": "No component fault has been confirmed by inspection or a fix."})
    for d in candidates:
        if d in PHYSICAL_PARTS and d != lead:
            cannot.append({"text": f"Send a {_owner(run, d)} technician", "why": f"Nothing points to {_label(run, d)}."})
    for d in assessment.get("less_likely", []):
        if d["part"] in PHYSICAL_PARTS and lead != d["part"]:
            cannot.append({"text": f"Send a {_owner(run, d['part'])} technician", "why": f"{d['label']}: no delay where it was measured."})
            break
    settled = set(done) | {p["check_id"] for p in proposed if p.get("approval")}
    blockers = [b["item"] for b in (run.get("dispatch") or {}).get("blockers", [])
                if b["status"] in ("not_recorded", "authorization_required", "reported_only") and not any(cid in b["item"] for cid in settled)][:3]
    unknown = [p["question"] for p in pending] + blockers
    return {"policy": POLICY, "run_id": run["id"], "status": status, "headline": headline, "leading": _label(run, lead) if status != "hold_for_evidence" else None,
            "scores": {_label(run, d): s for d, s in ranked}, "can_do_now": can, "cannot_do_yet": cannot, "why": why, "still_unknown": unknown,
            "checks_used": sorted(done), "checks_pending": [p["check_id"] for p in pending],
            "boundary": "Prepares a human decision. Not a dispatch approval, not a confirmed failed part, not a safety clearance."}
