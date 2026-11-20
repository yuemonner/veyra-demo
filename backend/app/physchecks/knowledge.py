"""Verified outcome -> future investigation: which earlier, engineer-verified results apply to THIS recording, why, and
how they are used.

Retrieval (outcomes.knowledge) finds earlier cases whose deterministic signature overlaps. This module decides
APPLICABILITY per verified result and says why:

  applies         the earlier case shares at least one finding kind (not just an outcome word), was recorded on the
                  same machine class, and the checks ran at the same versions
  partly applies  shared finding, but something differs or is unknown (machine class not recorded, engine version
                  changed, or this recording's own evidence weakens the hypothesis here)
  does not apply  a different machine class: shown so the engineer sees it was considered, never used

How it is used (and nowhere else):
  * the replay lists it next to the candidate causes, with the verification, who verified it and the basis;
  * the planner puts the check that SETTLED it there first among the checks the requester may do, and says so;
  * it never changes a status here: a cause verified elsewhere stays 'open' in this case until verified in this case.
Unverified hypotheses from earlier cases are listed separately as context and are never used for ranking.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.physchecks import outcomes as O
from app.physchecks.store import Ledger

RULE = ("An earlier verified result applies when it shares a finding kind with this recording, on the same machine class, "
        "with the checks at the same versions. It is used to order the next checks and is shown with its basis; it never "
        "confirms or excludes anything in this case.")


def _findings(signature: List[str]) -> set:
    """Signature entries that name a finding kind ('charge_accounting:unexplained_soc_correction'), not an outcome word."""
    return {s for s in signature if not s.split(":", 1)[1].isupper()}


def _settled_by(led: Ledger, case_id: str, verification: Dict[str, Any]) -> List[str]:
    out = []
    for oid in verification.get("observation_ids") or []:
        o = led.get(oid)
        if not o:
            continue
        p = led.get(o["payload"].get("performed_id", ""))
        if p and p["payload"].get("template_id") and p["payload"]["template_id"] not in out:
            out.append(p["payload"]["template_id"])
    return out


def context_of(checks: Dict[str, Dict[str, Any]], profile, synthetic: bool) -> Dict[str, Any]:
    """What a case records about itself when it is opened, so later cases can test applicability."""
    return {"machine_class": profile.data.get("machine_class"), "profile": profile.id, "synthetic": bool(synthetic),
            "check_versions": {k: v["check_version"] for k, v in checks.items()}}


def for_case(led: Optional[Ledger], case_id: str, checks: Dict[str, Dict[str, Any]], assessment: Dict[str, Any], profile,
             synthetic: bool) -> Dict[str, Any]:
    sig = O.signature_of(checks)
    mine = _findings(sig)
    out = {"rule": RULE, "signature": sig, "items": [], "unverified": [], "considered_cases": 0}
    if led is None or not mine:
        return out
    here = {h["id"]: h for h in assessment.get("hypotheses", [])}
    now_versions = {k: v["check_version"] for k, v in checks.items()}
    mc_now = profile.data.get("machine_class")
    for opened in led.records(kinds=["case_opened"]):
        p = opened["payload"]
        cid = p["case_id"]
        if cid == case_id:
            continue
        shared = sorted(mine & _findings(p.get("signature") or []))
        if not shared:
            continue
        out["considered_cases"] += 1
        ctx = p.get("context") or {}
        st = O.state(led, cid)
        for hid, h in st["hypotheses"].items():
            row = {"case_id": cid, "case_title": st["title"], "hypothesis_id": hid, "stage": h["stage"], "shared_findings": shared,
                   "statement": h["statements"][-1]["statement"] if h["statements"] else "", "source_synthetic": bool(ctx.get("synthetic"))}
            if h["stage"] not in ("verified_root_cause", "excluded"):
                row["note"] = "not verified there: context only, not used"
                out["unverified"].append(row)
                continue
            v = h["verification"]
            reasons, status = [f"shares {', '.join(shared)}"], "applies"
            mc_then = ctx.get("machine_class")
            if mc_then is None:
                status = "partly applies"
                reasons.append("the earlier case did not record its machine class")
            elif mc_now is None:
                status = "partly applies"
                reasons.append("this recording's profile states no machine class")
            elif mc_then != mc_now:
                status = "does not apply"
                reasons.append(f"machine class {mc_then} there, {mc_now} here")
            else:
                reasons.append(f"same machine class ({mc_now})")
            then_versions = ctx.get("check_versions") or {}
            changed = [k for k, ver in then_versions.items() if k in now_versions and now_versions[k] != ver]
            if changed:
                status = "partly applies" if status == "applies" else status
                reasons.append("check version changed since: " + ", ".join(f"{k} {then_versions[k]} -> {now_versions[k]}" for k in changed))
            cur = here.get(hid)
            if cur is None:
                status = "does not apply" if status != "does not apply" else status
                reasons.append(f"{hid} is not a candidate in this case")
            elif v["result"] == "confirmed" and cur["status"] in ("weakened", "excluded"):
                status = "partly applies" if status == "applies" else status
                reasons.append(f"this recording's evidence has {hid} {cur['status']} here")
            if ctx.get("synthetic") and not synthetic:
                status = "does not apply"
                reasons.append("the earlier case is SYNTHETIC; it is never used for a real recording")
            row.update({"result": v["result"], "verified_by": v["by"], "basis": v["basis"], "verified_at": v.get("at"),
                        "settled_by": _settled_by(led, cid, v), "applicability": status, "reasons": reasons,
                        "status_here": cur["status"] if cur else None,
                        "use": ("orders the next checks; does not change any status here" if status != "does not apply" else "not used")})
            out["items"].append(row)
    order = {"applies": 0, "partly applies": 1, "does not apply": 2}
    out["items"].sort(key=lambda r: (order[r["applicability"]], r["case_id"], r["hypothesis_id"]))
    return out


def prior_for_planner(k: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """template id -> the applicable verified results it settled (used by planner.plan to order checks)."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for it in k.get("items", []):
        if it["applicability"] == "does not apply":
            continue
        for t in it.get("settled_by") or []:
            out.setdefault(t, []).append({k2: it[k2] for k2 in ("case_id", "hypothesis_id", "result", "verified_by", "applicability")})
    return out
