"""Machine profiles: the machine type decides how an investigation is named, routed and checked.

The engine reasons over four fixed fault-source ROLES, which exist on any remotely operated or autonomous
machine:
  network            the link between operator/cloud and the machine
  control_suite      the software that decides what the machine should do
  vehicle_interface  the outputs that turn decisions into actuator commands
  vehicle_body       the physical machine that should move
A profile (one JSON file per machine type) gives each role its name and owner team on that kind of
machine, can route signals to roles, can reword or switch off checks, and lists which machine-context
attributes must be confirmed before two runs are compared (context_keys). Adding a machine type is a
new JSON file in this folder (or in VEYRA_MACHINE_PROFILES_DIR); no engine code changes.

The profile is chosen by the machine context attribute `machine_class` (reported or confirmed), as of
the incident. Without it the generic profile applies, and the run says so.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

ROLES = ("network", "control_suite", "vehicle_interface", "vehicle_body")
DEFAULT_ID = "generic"
BUILTIN_DIR = Path(__file__).resolve().parent / "profiles"
CHECK_FIELDS = ("question", "why", "who", "tools", "applicable")


class ProfileError(ValueError):
    pass


def _validate(raw: Dict[str, Any], source: str) -> Dict[str, Any]:
    pid = raw.get("id")
    if not isinstance(pid, str) or not pid.strip():
        raise ProfileError(f"{source}: id is required")
    domains = raw.get("domains") or {}
    if set(domains) != set(ROLES):
        raise ProfileError(f"{source}: domains must name exactly these roles: {', '.join(ROLES)}")
    for role, d in domains.items():
        if not (isinstance(d, dict) and isinstance(d.get("label"), str) and isinstance(d.get("owner"), str)):
            raise ProfileError(f"{source}: domains.{role} needs a label and an owner")
    for signal, role in (raw.get("signal_roles") or {}).items():
        if role not in ROLES:
            raise ProfileError(f"{source}: signal_roles.{signal} must be one of {', '.join(ROLES)}")
    for check_id, override in (raw.get("checks") or {}).items():
        unknown = set(override) - set(CHECK_FIELDS)
        if unknown:
            raise ProfileError(f"{source}: checks.{check_id} has unknown fields {', '.join(sorted(unknown))}")
    for i, rule in enumerate(raw.get("routine_events") or []):
        if not (isinstance(rule, dict) and isinstance(rule.get("signal"), str) and isinstance(rule.get("pattern"), str)):
            raise ProfileError(f"{source}: routine_events[{i}] needs a signal and a pattern")
        unknown = set(rule) - {"signal", "pattern", "min_scopes", "within_s", "why"}
        if unknown:
            raise ProfileError(f"{source}: routine_events[{i}] has unknown fields {', '.join(sorted(unknown))}")
        try:
            re.compile(rule["pattern"])
        except re.error as exc:
            raise ProfileError(f"{source}: routine_events[{i}].pattern is not a valid pattern ({exc})") from None
    keys = raw.get("context_keys")
    if keys is not None and not (isinstance(keys, list) and all(isinstance(k, str) for k in keys)):
        raise ProfileError(f"{source}: context_keys must be a list of machine-context attribute names")
    return {"id": pid, "name": raw.get("name") or pid, "description": raw.get("description", ""), "domains": domains,
            "signal_roles": raw.get("signal_roles") or {}, "checks": raw.get("checks") or {}, "context_keys": keys,
            "routine_events": raw.get("routine_events") or [], "source": source}


def load_all() -> Dict[str, Dict[str, Any]]:
    folders = [BUILTIN_DIR] + ([Path(os.environ["VEYRA_MACHINE_PROFILES_DIR"])] if os.environ.get("VEYRA_MACHINE_PROFILES_DIR") else [])
    out: Dict[str, Dict[str, Any]] = {}
    for folder in folders:
        for path in sorted(folder.glob("*.json")):
            profile = _validate(json.loads(path.read_text()), path.name)
            out[profile["id"]] = profile  # a later folder may replace a built-in profile
    if DEFAULT_ID not in out:
        raise ProfileError("The generic profile is missing")
    return out


def resolve(context_incident: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Pick the profile from machine_class in the machine context at the incident."""
    profiles = load_all()
    attr = ((context_incident or {}).get("attributes") or {}).get("machine_class") or {}
    value = attr.get("value") if attr.get("status") in ("reported", "engineer_confirmed") else None
    if value in profiles:
        basis = f"machine_class '{value}' ({attr.get('status').replace('_', ' ')}) in the machine context"
        chosen = profiles[value]
    else:
        chosen = profiles[DEFAULT_ID]
        basis = (f"machine_class '{value}' has no profile; generic profile used" if value
                 else "No machine_class in the machine context; generic profile used")
    return {**chosen, "basis": basis}


def summary(profile: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": profile["id"], "name": profile["name"], "basis": profile["basis"],
            "domains": {r: {"label": d["label"], "owner": d["owner"]} for r, d in profile["domains"].items()}}
