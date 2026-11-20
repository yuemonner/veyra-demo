"""The engineering-check interface.

A check is a versioned, deterministic function of (recording, profile, parameters). Its SPEC states up front:

  inputs        roles it reads, with unit, meaning and (for currents) the sign convention it needs
  parameters    values it needs that are not signals (capacity, an expected-behaviour spec, a restart policy), whether
                each is required, its tolerance, and where its value must come from
  applies_when  machine/configuration conditions; when they fail the outcome is NOT_APPLICABLE
  alignment     maximum sample age when pairing messages, the largest gap bridged by integration (none is interpolated)
  limitations   what the check cannot see

Its RESULT carries one outcome:

  PASS            the recorded data satisfies the check within the stated tolerances
  VIOLATION       the data contradicts the check's relation (or a VERIFIED spec), with evidence
  INDETERMINATE   the check applies but cannot decide: missing / stale / invalid data, an unverified spec, a parameter
                  that is absent or invalid. The reasons say what would make it decidable.
  NOT_APPLICABLE  the machine/configuration or the recording does not meet the check's preconditions

plus findings (each with evidence references to file + row), calculations (formula, inputs, value, unit), uncertainty,
data-quality counts, assumptions, and an input digest so the same inputs can be shown to give the same result.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from app.physchecks.profile import Profile
from app.physchecks.recording import EvidenceRef, Recording

PASS, VIOLATION, INDETERMINATE, NOT_APPLICABLE = "PASS", "VIOLATION", "INDETERMINATE", "NOT_APPLICABLE"
OUTCOMES = (PASS, VIOLATION, INDETERMINATE, NOT_APPLICABLE)
# when several windows are checked, the recording-level outcome is the most informative one
_RANK = {VIOLATION: 3, INDETERMINATE: 2, PASS: 1, NOT_APPLICABLE: 0}
ENGINE_VERSION = "veyra-check-engine-v1"


@dataclass(frozen=True)
class InputSpec:
    role: str
    unit: str
    meaning: str
    required: bool = True
    sign_convention: Optional[str] = None


@dataclass(frozen=True)
class ParamSpec:
    name: str
    unit: str
    meaning: str
    required: bool
    default: Any = None
    tolerance: Optional[str] = None
    source_rule: str = ""


@dataclass(frozen=True)
class CheckSpec:
    id: str
    version: str
    title: str
    purpose: str
    inputs: List[InputSpec]
    parameters: List[ParamSpec]
    applies_when: List[str]
    alignment: Dict[str, Any]
    limitations: List[str]

    def as_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "version": self.version, "title": self.title, "purpose": self.purpose,
                "inputs": [i.__dict__ for i in self.inputs], "parameters": [p.__dict__ for p in self.parameters],
                "applies_when": self.applies_when, "alignment": self.alignment, "limitations": self.limitations}


@dataclass
class Finding:
    kind: str
    statement: str
    evidence: List[EvidenceRef] = field(default_factory=list)
    values: Dict[str, Any] = field(default_factory=dict)
    severity: str = "info"          # info | review | violation

    def as_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "statement": self.statement, "severity": self.severity, "values": self.values,
                "evidence": [e.as_dict() for e in self.evidence]}


@dataclass
class Calculation:
    name: str
    formula: str
    inputs: Dict[str, Any]
    value: Any
    unit: str
    provenance: str            # what the inputs came from (signals, parameters, which rows)

    def as_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class CheckResult:
    check_id: str
    check_version: str
    recording: str
    synthetic: bool
    outcome: str
    summary: str
    windows: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    findings: List[Finding] = field(default_factory=list)
    calculations: List[Calculation] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)          # why INDETERMINATE / NOT_APPLICABLE
    uncertainty: Dict[str, Any] = field(default_factory=dict)
    data_quality: Dict[str, Any] = field(default_factory=dict)
    assumptions: List[str] = field(default_factory=list)
    inputs_used: Dict[str, str] = field(default_factory=dict)
    parameters_used: Dict[str, Any] = field(default_factory=dict)
    input_digest: str = ""
    # what would decide an INDETERMINATE (or confirm a result that rests on non-independent signals): [{measurement, template}]
    resolve_by: List[Dict[str, Any]] = field(default_factory=list)
    # pairs of signals compared that are not independent of each other, and what that means for the result
    independence: List[Dict[str, Any]] = field(default_factory=list)
    provenance: Dict[str, Any] = field(default_factory=dict)        # where the check's rule comes from

    def as_dict(self) -> Dict[str, Any]:
        return {"check_id": self.check_id, "check_version": self.check_version, "engine": ENGINE_VERSION,
                "recording": self.recording, "synthetic": self.synthetic, "outcome": self.outcome, "summary": self.summary,
                "windows": self.windows, "findings": [f.as_dict() for f in self.findings],
                "calculations": [c.as_dict() for c in self.calculations], "reasons": self.reasons,
                "uncertainty": self.uncertainty, "data_quality": self.data_quality, "assumptions": self.assumptions,
                "inputs_used": self.inputs_used, "parameters_used": self.parameters_used, "input_digest": self.input_digest,
                "resolve_by": self.resolve_by, "independence": self.independence, "provenance": self.provenance,
                "deterministic": True}


def combine(outcomes: List[str]) -> str:
    return max(outcomes, key=lambda o: _RANK[o]) if outcomes else NOT_APPLICABLE


def input_digest(rec: Recording, profile: Profile, check_id: str, version: str, params: Dict[str, Any]) -> str:
    h = hashlib.sha256()
    h.update(f"{check_id}@{version}".encode())
    h.update(rec.digest().encode())
    h.update(json.dumps(profile.data, sort_keys=True, default=str).encode())
    h.update(json.dumps(params, sort_keys=True, default=str).encode())
    return h.hexdigest()


class Check:
    spec: CheckSpec

    def run(self, rec: Recording, profile: Profile, params: Optional[Dict[str, Any]] = None) -> CheckResult:
        raise NotImplementedError

    def missing_roles(self, profile: Profile) -> List[str]:
        return [i.role for i in self.spec.inputs if i.required and not profile.has_role(i.role)]


REGISTRY: Dict[str, Check] = {}


def register(check: Check) -> Check:
    REGISTRY[check.spec.id] = check
    return check


def get(check_id: str) -> Check:
    _load_builtin()
    if check_id not in REGISTRY:
        raise KeyError(f"unknown check {check_id!r}")
    return REGISTRY[check_id]


def all_checks() -> List[Check]:
    _load_builtin()
    return [REGISTRY[k] for k in sorted(REGISTRY)]


def run_all(rec: Recording, profile: Profile, params: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Dict[str, Any]]:
    """Run every registered check; `params` maps check id -> its parameters. Returns check id -> result dict."""
    params = params or {}
    return {c.spec.id: c.run(rec, profile, params.get(c.spec.id)).as_dict() for c in all_checks()}


def _load_builtin() -> None:
    from app.physchecks import charge_accounting, protection_sequence, restart_eligibility  # noqa: F401
    from app.physchecks import ocv_consistency, pack_cell_consistency, relaxation, weak_cell  # noqa: F401  (cell physics)


def fmt(x: Optional[float], nd: int = 3) -> Optional[float]:
    return None if x is None else round(float(x), nd)


Factory = Callable[[], Check]
