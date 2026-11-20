"""Import profiles: the explicit, versioned contract between a source export and the catalog.

Nothing is inferred from column or signal names. A profile states which column holds what,
which source signal name map to which canonical signals, how timestamps are written, which
clock produced them, and which reported machine context applies to the whole file.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from app.context import model as context_schema
from app.ingest import catalog

FORMATS = ("csv", "json", "jsonl")
TIMESTAMP_FORMATS = ("iso8601", "epoch")
EPOCH_UNITS = {"s": 1.0, "ms": 1e3, "us": 1e6, "ns": 1e9}
COLUMN_ROLES = (
    "timestamp", "asset", "signal", "value", "unit", "event_value", "code", "code_description",
    "code_source", "command_id", "scope", "quality",
)
REQUIRED_COLUMNS = ("timestamp", "asset", "signal")
# Reported machine context a profile may attach to every row of one import (never auto-confirmed).
# Single source of truth: any Machine Context attribute (reported status only; never confirmed by a profile).
SOURCE_METADATA_FIELDS = tuple(context_schema.SCHEMA)
_OFFSET = re.compile(r"^[+-](\d{2}):(\d{2})$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")


class ProfileError(ValueError):
    pass


@dataclass(frozen=True)
class ImportProfile:
    profile_id: str
    version: int
    format: str
    columns: Dict[str, str]
    signal_map: Dict[str, str]
    clock_domain: str
    timestamp_format: str = "iso8601"
    epoch_unit: Optional[str] = None
    # Only used when ISO timestamps lack an offset. Must be stated explicitly (e.g. "+00:00").
    naive_timestamp_offset: Optional[str] = None
    # Declared maximum clock skew of this clock domain, seconds. None = unknown.
    max_clock_skew_s: Optional[float] = None
    asset_aliases: Dict[str, str] = field(default_factory=dict)
    # Source unit spelling -> catalog unit spelling, per canonical signal: {"network.rtt": {"msec": "ms"}}
    unit_aliases: Dict[str, Dict[str, str]] = field(default_factory=dict)
    # Source fault system -> fault domain, e.g. {"VCU": "vehicle_interface"}
    fault_source_domains: Dict[str, str] = field(default_factory=dict)
    default_scope: Optional[str] = None
    unmapped_signals: str = "retain"  # "retain" (statistics only) or "reject"
    max_rejected_fraction: float = 0.2
    source_metadata: Dict[str, str] = field(default_factory=dict)
    synthetic: bool = False
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def sha256(self) -> str:
        canonical = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


def _text_map(raw, label: str, max_items: int = 200) -> Dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict) or len(raw) > max_items:
        raise ProfileError(f"{label} must be an object with at most {max_items} entries")
    out = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str) or not key.strip() or not value.strip():
            raise ProfileError(f"{label} keys and values must be non-empty strings")
        out[key] = value
    return out


def parse_profile(raw: dict) -> ImportProfile:
    """Validate a profile document. Raises ProfileError with an actionable message."""
    if not isinstance(raw, dict):
        raise ProfileError("Profile must be a JSON object")
    known = set(ImportProfile.__dataclass_fields__)
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ProfileError(f"Unknown profile fields: {', '.join(unknown)}")

    profile_id = raw.get("profile_id")
    if not isinstance(profile_id, str) or not _NAME.match(profile_id):
        raise ProfileError("profile_id must be 1–120 characters of letters, digits, . _ : -")
    version = raw.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ProfileError("version must be a positive integer")
    fmt = raw.get("format")
    if fmt not in FORMATS:
        raise ProfileError(f"format must be one of {', '.join(FORMATS)}")

    columns = _text_map(raw.get("columns"), "columns", len(COLUMN_ROLES))
    bad_roles = sorted(set(columns) - set(COLUMN_ROLES))
    if bad_roles:
        raise ProfileError(f"Unknown column roles: {', '.join(bad_roles)}")
    missing = [role for role in REQUIRED_COLUMNS if role not in columns]
    if missing:
        raise ProfileError(f"Map required columns explicitly: {', '.join(missing)}")
    if len(set(columns.values())) != len(columns):
        raise ProfileError("Each source column may be assigned to only one role")

    signal_map = _text_map(raw.get("signal_map"), "signal_map")
    if not signal_map:
        raise ProfileError("signal_map must map at least one source signal name to a canonical signal")
    for source_name, canonical in signal_map.items():
        try:
            spec = catalog.get(canonical)
        except ValueError as error:
            raise ProfileError(f"signal_map['{source_name}']: {error}") from None
        if spec.kind == catalog.MEASUREMENT and not ("value" in columns and "unit" in columns):
            raise ProfileError(f"{canonical} is a measurement; map both value and unit columns")
        if spec.kind == catalog.COMMAND and "command_id" not in columns:
            raise ProfileError(f"{canonical} is a command stage; map a command_id column")
        if spec.kind == catalog.FAULT_CODE and "code" not in columns and "value" not in columns:
            raise ProfileError("fault.code needs a code (or value) column")

    clock_domain = raw.get("clock_domain")
    if not isinstance(clock_domain, str) or not _NAME.match(clock_domain):
        raise ProfileError("clock_domain is required (name the clock that produced these timestamps, e.g. console_clock)")

    timestamp_format = raw.get("timestamp_format", "iso8601")
    if timestamp_format not in TIMESTAMP_FORMATS:
        raise ProfileError("timestamp_format must be iso8601 or epoch")
    epoch_unit = raw.get("epoch_unit")
    if timestamp_format == "epoch" and epoch_unit not in EPOCH_UNITS:
        raise ProfileError("epoch timestamps need an explicit epoch_unit: s, ms, us or ns")
    if timestamp_format == "iso8601" and epoch_unit is not None:
        raise ProfileError("epoch_unit applies only to epoch timestamps")
    offset = raw.get("naive_timestamp_offset")
    if offset is not None and (not isinstance(offset, str) or not _OFFSET.match(offset)):
        raise ProfileError("naive_timestamp_offset must look like +00:00 or -05:30")

    skew = raw.get("max_clock_skew_s")
    if skew is not None and (isinstance(skew, bool) or not isinstance(skew, (int, float)) or not 0 <= skew <= 86400):
        raise ProfileError("max_clock_skew_s must be between 0 and 86400 seconds, or omitted when unknown")

    unit_aliases_raw = raw.get("unit_aliases") or {}
    if not isinstance(unit_aliases_raw, dict):
        raise ProfileError("unit_aliases must be an object keyed by canonical signal")
    unit_aliases = {}
    for canonical, aliases in unit_aliases_raw.items():
        spec = catalog.get(canonical) if canonical in catalog.CATALOG else None
        if spec is None or spec.kind != catalog.MEASUREMENT:
            raise ProfileError(f"unit_aliases: {canonical} is not a canonical measurement")
        mapping = _text_map(aliases, f"unit_aliases['{canonical}']", 20)
        for source_unit, catalog_unit in mapping.items():
            if catalog_unit not in spec.conversions:
                raise ProfileError(f"unit_aliases['{canonical}']['{source_unit}'] targets unaccepted unit '{catalog_unit}'")
        unit_aliases[canonical] = mapping

    fault_domains = _text_map(raw.get("fault_source_domains"), "fault_source_domains", 50)
    for system, domain in fault_domains.items():
        if domain not in catalog.DOMAINS:
            raise ProfileError(f"fault_source_domains['{system}'] must be one of {', '.join(catalog.DOMAINS)}")

    unmapped = raw.get("unmapped_signals", "retain")
    if unmapped not in ("retain", "reject"):
        raise ProfileError("unmapped_signals must be retain or reject")
    tolerance = raw.get("max_rejected_fraction", 0.2)
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or not 0 <= tolerance <= 1:
        raise ProfileError("max_rejected_fraction must be between 0 and 1")

    metadata = _text_map(raw.get("source_metadata"), "source_metadata", len(SOURCE_METADATA_FIELDS))
    bad_meta = sorted(set(metadata) - set(SOURCE_METADATA_FIELDS))
    if bad_meta:
        raise ProfileError(f"Unknown source_metadata fields: {', '.join(bad_meta)}")

    synthetic = raw.get("synthetic", False)
    if not isinstance(synthetic, bool):
        raise ProfileError("synthetic must be true or false")
    default_scope = raw.get("default_scope")
    if default_scope is not None and (not isinstance(default_scope, str) or not default_scope.strip() or len(default_scope) > 200):
        raise ProfileError("default_scope must be a non-empty measurement endpoint name")
    notes = raw.get("notes", "")
    if not isinstance(notes, str) or len(notes) > 2000:
        raise ProfileError("notes must be text up to 2000 characters")

    return ImportProfile(
        profile_id=profile_id, version=version, format=fmt, columns=columns, signal_map=signal_map,
        clock_domain=clock_domain, timestamp_format=timestamp_format, epoch_unit=epoch_unit,
        naive_timestamp_offset=offset, max_clock_skew_s=float(skew) if skew is not None else None,
        asset_aliases=_text_map(raw.get("asset_aliases"), "asset_aliases", 50), unit_aliases=unit_aliases,
        fault_source_domains=fault_domains, default_scope=default_scope, unmapped_signals=unmapped,
        max_rejected_fraction=float(tolerance), source_metadata=metadata, synthetic=synthetic, notes=notes,
    )


def required_columns(profile: ImportProfile) -> List[str]:
    return [profile.columns[role] for role in REQUIRED_COLUMNS]
