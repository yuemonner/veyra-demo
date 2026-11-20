"""Scenario registry: Investigation finding -> reviewed reusable check -> run on other recordings.

A SCENARIO is a versioned, reviewed configuration of one physical check, created from a finding of a deterministic
check in a real investigation, so the same question can be asked of every compatible recording:

  check_id / check_version   which check, at which version
  params                     the check's parameters (no data, no customer values beyond thresholds)
  applicability              required roles, machine class, profile conditions that must match
  created_from               case id, the finding's kind and statement digest, the check result's input digest
  status                     draft -> approved | rejected; a revision returns it to draft (re-review needed)

Rules:
  * only a finding of a deterministic check can seed a scenario (never an agent's statement);
  * an approval needs an ENGINEER who is not the scenario's author (two-person rule); an agent cannot approve;
  * only approved scenarios run in the cross-run scanner; a draft can be previewed, and its results say "unreviewed";
  * a run says NOT_APPLICABLE (with which roles or conditions are missing) on incompatible recordings, and INDETERMINATE
    when the roles are bound but the recording carries no samples of them.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from app.physchecks import base
from app.physchecks.base import INDETERMINATE, NOT_APPLICABLE
from app.physchecks.profile import Profile
from app.physchecks.recording import Recording
from app.physchecks.store import Ledger, LedgerError

SCENARIO_SCHEMA = "veyra-scenario-v1"


class ScenarioError(ValueError):
    pass


def _digest(x: Any) -> str:
    return hashlib.sha256(json.dumps(x, sort_keys=True, default=str).encode()).hexdigest()


def create_from_finding(led: Ledger, *, case_id: str, check_result: Dict[str, Any], finding_kind: str, title: str,
                        author: str, author_kind: str = "engineer", params: Optional[Dict[str, Any]] = None,
                        applicability: Optional[Dict[str, Any]] = None, description: str = "") -> Dict[str, Any]:
    if not check_result.get("deterministic"):
        raise ScenarioError("a scenario can only be created from a deterministic check result")
    if author_kind == "agent":
        raise ScenarioError("an agent cannot author a scenario; an engineer turns a finding into one")
    finding = next((f for f in check_result.get("findings") or [] if f["kind"] == finding_kind), None)
    if finding is None:
        raise ScenarioError(f"the check result has no finding of kind {finding_kind!r}")
    check = base.get(check_result["check_id"])
    app = applicability or {}
    app.setdefault("required_roles", [i.role for i in check.spec.inputs if i.required])
    payload = {"schema": SCENARIO_SCHEMA, "title": title, "description": description, "check_id": check_result["check_id"],
               "check_version": check_result["check_version"], "params": params or {}, "applicability": app,
               "created_from": {"case_id": case_id, "finding_kind": finding_kind, "finding_digest": _digest(finding["statement"]),
                                "check_input_digest": check_result.get("input_digest"), "source_synthetic": check_result.get("synthetic")},
               "expect_outcome_on_match": check_result["outcome"], "status": "draft", "version": 1}
    # the scenario's stable id is its first record's id; later versions and reviews use it as their subject
    rec = led.append("scenario", f"case:{case_id}", payload, actor=author, actor_kind=author_kind, prefix="scn")
    return get(led, rec["id"])


def _versions(led: Ledger, sid: str) -> List[Dict[str, Any]]:
    first = led.get(sid)
    if not first or first["kind"] != "scenario":
        raise ScenarioError(f"unknown scenario {sid}")
    return [first] + led.records(subject=sid, kinds=["scenario_version"])


def get(led: Ledger, sid: str) -> Dict[str, Any]:
    vs = _versions(led, sid)
    cur = vs[-1]
    reviews = led.records(subject=sid, kinds=["scenario_review"])
    p = dict(cur["payload"])
    v_reviews = [r for r in reviews if r["payload"]["version"] == p["version"]]
    status = "draft"
    if v_reviews:
        status = "approved" if v_reviews[-1]["payload"]["decision"] == "approve" else "rejected"
    p.update({"id": sid, "status": status, "author": vs[0]["actor"], "reviews": [{"by": r["actor"], **r["payload"], "at": r["created_at"]} for r in reviews],
              "history": [{"version": v["payload"]["version"], "by": v["actor"], "at": v["created_at"], "change": v["payload"].get("change")} for v in vs]})
    return p


def list_all(led: Ledger, status: Optional[str] = None) -> List[Dict[str, Any]]:
    out = [get(led, r["id"]) for r in led.records(kinds=["scenario"])]
    return [s for s in out if status is None or s["status"] == status]


def review(led: Ledger, sid: str, *, reviewer: str, reviewer_kind: str, decision: str, notes: str = "") -> Dict[str, Any]:
    if decision not in ("approve", "reject"):
        raise ScenarioError("decision must be approve or reject")
    if reviewer_kind != "engineer":
        raise ScenarioError("only an engineer can review a scenario")
    s = get(led, sid)
    if reviewer.strip().lower() == s["author"].strip().lower():
        raise ScenarioError("the author cannot review their own scenario (two-person rule)")
    if decision == "approve" and not notes.strip():
        raise ScenarioError("an approval states what the reviewer checked")
    led.append("scenario_review", sid, {"version": s["version"], "decision": decision, "notes": notes}, actor=reviewer, actor_kind=reviewer_kind)
    return get(led, sid)


def revise(led: Ledger, sid: str, *, author: str, change: str, params: Optional[Dict[str, Any]] = None,
           applicability: Optional[Dict[str, Any]] = None, title: Optional[str] = None) -> Dict[str, Any]:
    if not change.strip():
        raise ScenarioError("a revision states what changed and why")
    s = get(led, sid)
    p = {k: s[k] for k in ("schema", "title", "description", "check_id", "check_version", "params", "applicability", "created_from",
                           "expect_outcome_on_match")}
    p.update({"version": s["version"] + 1, "change": change, "status": "draft"})
    if params is not None:
        p["params"] = params
    if applicability is not None:
        p["applicability"] = applicability
    if title:
        p["title"] = title
    led.append("scenario_version", sid, p, actor=author, actor_kind="engineer")
    return get(led, sid)


def applicability(s: Dict[str, Any], rec: Recording, profile: Profile) -> Tuple[str, List[str]]:
    """('applies'|'not_applicable'|'signals_unavailable', reasons)."""
    app = s.get("applicability") or {}
    reasons: List[str] = []
    missing = [r for r in app.get("required_roles") or [] if not profile.has_role(r)]
    if missing:
        reasons.append("the machine profile binds no signal for: " + ", ".join(missing))
    mc = app.get("machine_class")
    if mc and profile.data.get("machine_class") != mc:
        reasons.append(f"machine class {profile.data.get('machine_class')!r} is not {mc!r}")
    for k, v in (app.get("conditions") or {}).items():
        if profile.conditions.get(k) != v:
            reasons.append(f"configuration {k}={profile.conditions.get(k)!r}, scenario requires {v!r}")
    if reasons:
        return "not_applicable", reasons
    empty = [r for r in app.get("required_roles") or [] if not any(rec.has(w, profile.role(r)["field"]) for w in rec.windows)]
    if empty:
        return "signals_unavailable", ["the recording carries no samples of: " + ", ".join(empty)]
    return "applies", []


def run(s: Dict[str, Any], rec: Recording, profile: Profile, *, preview: bool = False) -> Dict[str, Any]:
    if s["status"] != "approved" and not preview:
        raise ScenarioError(f"scenario {s['id']} is {s['status']}: only approved scenarios run (use preview for a draft)")
    head = {"scenario_id": s["id"], "scenario_version": s["version"], "scenario_title": s["title"], "scenario_status": s["status"],
            "reviewed": s["status"] == "approved", "recording": rec.id, "recording_synthetic": rec.synthetic, "profile": profile.id}
    state, reasons = applicability(s, rec, profile)
    if state != "applies":
        return {**head, "outcome": NOT_APPLICABLE if state == "not_applicable" else INDETERMINATE, "applicability": state, "reasons": reasons,
                "summary": ("Not applicable: " if state == "not_applicable" else "Indeterminate: ") + "; ".join(reasons), "findings": []}
    check = base.get(s["check_id"])
    if check.spec.version != s["check_version"]:
        reasons.append(f"scenario was reviewed for {s['check_version']}; engine has {check.spec.version}: results need re-review")
    r = check.run(rec, profile, s.get("params")).as_dict()
    keep = [f for f in r["findings"] if f["severity"] in ("violation", "review")][:10]
    return {**head, "outcome": r["outcome"], "applicability": state, "reasons": reasons + r["reasons"], "summary": r["summary"],
            "matched_original": r["outcome"] == s.get("expect_outcome_on_match"), "findings": keep, "input_digest": r["input_digest"],
            "check_version": r["check_version"]}


def scan(led: Ledger, targets: List[Tuple[Recording, Profile]], *, actor: str = "veyra-scanner", persist: bool = True,
         include_drafts: bool = False, scenario_ids: Optional[List[str]] = None, trigger: str = "manual") -> Dict[str, Any]:
    """Run every approved scenario (drafts too, as previews, when include_drafts) on every target recording.
    `scenario_ids` limits the run (e.g. back-filling one newly approved scenario); `trigger` is stored with each result."""
    scen = [s for s in list_all(led) if s["status"] == "approved" or (include_drafts and s["status"] == "draft")]
    if scenario_ids is not None:
        scen = [s for s in scen if s["id"] in scenario_ids]
    rows: List[Dict[str, Any]] = []
    for s in scen:
        for rec, prof in targets:
            r = run(s, rec, prof, preview=s["status"] != "approved")
            rows.append(r)
            if persist:
                led.append("scan_result", s["id"], {k: v for k, v in r.items() if k != "findings"} | {"finding_kinds": [f["kind"] for f in r["findings"]],
                                                                                                    "trigger": trigger},
                           actor=actor, actor_kind="system")
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    return {"scenarios": [s["id"] for s in scen], "recordings": [rec.id for rec, _ in targets], "results": rows, "counts": counts}
