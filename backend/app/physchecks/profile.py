"""Signal semantics: what each recorded field MEANS, so checks are written against roles, not against one vendor's names.

A profile binds ROLES (pack_current, soc, min_cell_voltage, main_positive_closed ...) to recorded fields and states,
for each role:

  field          recorded column name
  unit           unit of the recorded value after decoding (converted to the unit a check asks for)
  sign           for currents: "discharge_positive" or "charge_positive" (the check never guesses)
  valid          [lo, hi] physically meaningful range; values outside are excluded and counted, never used
  placeholders   exact raw values that mean "not ready / not available" (e.g. 65.535 V, 0 V at power-up)
  map            raw -> normalised value (e.g. BMS fault raw 0x10 -> level 2); unmapped raw values are excluded
  enum           raw -> state name, for readable evidence
  resolution     quantisation step of the recorded value (e.g. SOC 0.4 %)
  max_age_s      oldest a held value may be when it is paired with another message's sample
  kind           measurement | estimate | reported_parameter | state | command
  same_source_as another role this one is derived from (e.g. VCU's copy of the BMS current): not independent evidence
  derived_from   roles (or named quantities such as "cell_voltages") this signal is computed from. A coulomb-counted SOC is
                 derived from pack_current unless the profile says otherwise. A check never treats agreement between a
                 signal and its own inputs as evidence (provenance rule; external EE advisor's proposal, 2026-11-16)

Profiles are data (JSON). A customer's real profile stays with the customer's data (local-only), never in the repository.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.physchecks.recording import Column, EvidenceRef, Recording, Sample

PROFILE_SCHEMA = "veyra-signal-profile-v1"

_UNIT = {  # unit -> (dimension, factor to the dimension's base unit)
    "A": ("current", 1.0), "mA": ("current", 1e-3), "kA": ("current", 1e3),
    "V": ("voltage", 1.0), "mV": ("voltage", 1e-3),
    "%": ("fraction", 0.01), "fraction": ("fraction", 1.0), "pct": ("fraction", 0.01),
    "Ah": ("charge", 3600.0), "mAh": ("charge", 3.6), "As": ("charge", 1.0), "C": ("charge", 1.0),
    "s": ("time", 1.0), "ms": ("time", 1e-3),
    "m/s": ("speed", 1.0), "km/h": ("speed", 1 / 3.6),
    "degC": ("temperature", 1.0), "": ("none", 1.0),
}


class ProfileError(ValueError):
    pass


def convert(value: float, unit_from: str, unit_to: str) -> float:
    if unit_from == unit_to:
        return value
    a, b = _UNIT.get(unit_from), _UNIT.get(unit_to)
    if a is None or b is None or a[0] != b[0]:
        raise ProfileError(f"cannot convert {unit_from!r} to {unit_to!r}")
    return value * a[1] / b[1]


@dataclass
class RoleSeries:
    """Valid samples of one role in one window, in the check's unit, plus what was excluded and why."""
    role: str
    window: str
    field: str
    unit: str
    samples: List[Sample]
    excluded: Dict[str, int]            # reason -> count
    excluded_examples: List[EvidenceRef]
    resolution: Optional[float]
    max_age_s: float

    @property
    def t(self) -> List[float]:
        return [s.t for s in self.samples]


class Profile:
    def __init__(self, data: Dict[str, Any]):
        if data.get("schema") != PROFILE_SCHEMA:
            raise ProfileError(f"profile schema must be {PROFILE_SCHEMA!r}")
        self.data = data
        self.id = data["id"]
        self.version = str(data.get("version", "1"))
        self.synthetic = bool(data.get("synthetic", False))
        self.roles: Dict[str, Dict[str, Any]] = data.get("roles") or {}
        self.parameters: Dict[str, Dict[str, Any]] = data.get("parameters") or {}
        self.default_max_age_s = float(data.get("max_sample_age_s", 0.5))
        self.max_gap_s = float(data.get("max_gap_s", 1.0))
        self.conditions: Dict[str, Any] = data.get("conditions") or {}
        for role, spec in self.roles.items():
            if "field" not in spec:
                raise ProfileError(f"role {role!r} has no field")
            if spec.get("unit", "") not in _UNIT:
                raise ProfileError(f"role {role!r}: unknown unit {spec.get('unit')!r}")
            if role.endswith("current") and spec.get("kind", "measurement") == "measurement" and spec.get("sign") not in ("discharge_positive", "charge_positive"):
                raise ProfileError(f"role {role!r}: a current needs sign 'discharge_positive' or 'charge_positive'")

    @classmethod
    def load(cls, path: Path) -> "Profile":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def role(self, role: str) -> Dict[str, Any]:
        return self.roles.get(role) or {}

    def parameter(self, name: str) -> Tuple[Optional[Any], str]:
        """(value, source). Value None when not stated."""
        p = self.parameters.get(name)
        if p is None:
            return None, "not stated"
        return p.get("value"), p.get("source", "profile")

    def max_age(self, role: str) -> float:
        return float(self.role(role).get("max_age_s", self.default_max_age_s))

    def derived_from(self, role: str) -> List[str]:
        """Direct inputs of a signal: declared derived_from / same_source_as, else the estimator's known inputs."""
        r = self.role(role)
        if "derived_from" in r:
            out = list(r.get("derived_from") or [])
        else:
            est = (r.get("estimator") or "").lower()
            out = {"coulomb_counting": ["pack_current"], "mixed": ["pack_current", "cell_voltages"],
                   "voltage_based": ["cell_voltages"]}.get(est, [])
        if r.get("same_source_as"):
            out.append(r["same_source_as"])
        return out

    def inputs_closure(self, role: str) -> set:
        seen, todo = set(), list(self.derived_from(role))
        while todo:
            x = todo.pop()
            if x not in seen:
                seen.add(x)
                todo += self.derived_from(x) if x in self.roles else []
        return seen

    def provenance(self, role: str) -> Dict[str, Any]:
        r = self.role(role)
        d = self.derived_from(role)
        return {"role": role, "kind": r.get("kind", "measurement"), "measured": r.get("kind", "measurement") == "measurement" and not d,
                "derived_from": d, "estimator": r.get("estimator")}

    def inputs_declared(self, role: str) -> bool:
        """False for an estimate whose inputs the profile does not state (no estimator, no derived_from, no same_source_as)."""
        r = self.role(role)
        if r.get("kind", "measurement") != "estimate":
            return True
        return "derived_from" in r or bool(r.get("estimator")) or bool(r.get("same_source_as"))

    def independence(self, a: str, b: str) -> Optional[bool]:
        """True / False as independent() decides, or None when either signal is an estimate whose inputs are not declared:
        then whether they share an input is unknown, and agreement between them cannot be counted as evidence."""
        if not self.independent(a, b):
            return False
        if not (self.inputs_declared(a) and self.inputs_declared(b)):
            return None
        return True

    def independent(self, a: str, b: str) -> bool:
        """False when one signal is derived from the other, or both from a shared input (directly or transitively)."""
        sa, sb = self.role(a).get("same_source_as"), self.role(b).get("same_source_as")
        if sa == b or sb == a or (sa is not None and sa == sb):
            return False
        ca, cb = self.inputs_closure(a), self.inputs_closure(b)
        return not (b in ca or a in cb or (ca & cb))

    def describe(self, role: str) -> str:
        r = self.role(role)
        bits = [f"{r.get('field')} [{r.get('unit', '')}]"]
        if r.get("sign"):
            bits.append(r["sign"].replace("_", " "))
        if r.get("kind"):
            bits.append(r["kind"].replace("_", " "))
        return ", ".join(bits)

    def series(self, rec: Recording, window: str, role: str, unit: Optional[str] = None) -> RoleSeries:
        spec = self.role(role)
        fld = spec.get("field", "")
        col: Column = rec.column(window, fld)
        src_unit = spec.get("unit", "")
        to = unit if unit is not None else src_unit
        lo, hi = (spec.get("valid") or [-math.inf, math.inf])
        placeholders = [float(p) for p in spec.get("placeholders") or []]
        vmap = {float(k): v for k, v in (spec.get("map") or {}).items()}
        out: List[Sample] = []
        excluded: Dict[str, int] = {}
        examples: List[EvidenceRef] = []

        def drop(reason: str, i: int) -> None:
            excluded[reason] = excluded.get(reason, 0) + 1
            if len(examples) < 5:
                examples.append(rec.ref(window, fld, i))

        for i in range(len(col)):
            raw = col.v[i]
            if math.isnan(raw) or math.isinf(raw):
                drop("not a number", i)
                continue
            if any(abs(raw - p) < 1e-9 for p in placeholders):
                drop("placeholder value", i)
                continue
            val = raw
            if vmap:
                if raw not in vmap:
                    drop("raw value not in the declared map", i)
                    continue
                val = float(vmap[raw])
            if not (lo <= val <= hi):
                drop("outside the declared valid range", i)
                continue
            out.append(Sample(col.t[i], convert(val, src_unit, to), i))
        res = spec.get("resolution")
        return RoleSeries(role, window, fld, to, out, excluded, examples,
                          convert(float(res), src_unit, to) if res is not None else None, self.max_age(role))

    def state_name(self, role: str, raw: float) -> Optional[str]:
        enum = self.role(role).get("enum") or {}
        key = str(int(raw)) if float(raw).is_integer() else str(raw)
        return enum.get(key)
