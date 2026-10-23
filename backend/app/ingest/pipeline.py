"""Industrial ingestion: bytes -> validated, normalized rows plus an honest report.

Pure functions only: no database, network or filesystem access. Imported content is treated
strictly as data. Formats are declared by the import profile and never guessed. Every rejected
row keeps its reasons; accepted rows keep their original values, units and raw record.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.ingest import catalog
from app.ingest.profile import EPOCH_UNITS, ImportProfile

NORMALIZATION_VERSION = "industrial-normalization-v1"

# File-level statuses
ACCEPTED = "accepted"
ACCEPTED_WITH_WARNINGS = "accepted_with_warnings"
NEEDS_REVIEW = "needs_review"
REJECTED = "rejected"
DUPLICATE_UPLOAD = "duplicate_upload"
UNSUPPORTED_FORMAT = "unsupported_format"

_EXTENSIONS = {"csv": (".csv",), "json": (".json",), "jsonl": (".jsonl", ".ndjson")}
_KNOWN_UNSUPPORTED = {
    ".mcap": "MCAP recording", ".bag": "ROS 1 bag", ".db3": "ROS 2 SQLite bag", ".ulg": "PX4 ULog",
    ".ulog": "PX4 ULog", ".bin": "binary log", ".tlog": "MAVLink telemetry log", ".xlsx": "Excel workbook",
    ".xls": "Excel workbook", ".parquet": "Parquet file", ".zip": "ZIP archive", ".gz": "compressed file",
    ".pcap": "packet capture", ".pcapng": "packet capture", ".dbc": "CAN database", ".blf": "CAN log",
    ".asc": "CAN log", ".pdf": "PDF document", ".docx": "Word document", ".txt": "plain text",
}
_MAGIC = (
    (b"\x89MCAP", "MCAP recording"), (b"PK\x03\x04", "ZIP container (Excel or archive)"),
    (b"SQLite format 3\x00", "SQLite database"), (b"ULog", "PX4 ULog"), (b"\x1f\x8b", "gzip-compressed file"),
    (b"%PDF", "PDF document"), (b"#ROSBAG", "ROS 1 bag"), (b"\xd4\xc3\xb2\xa1", "packet capture"),
    (b"\x0a\x0d\x0d\x0a", "packet capture"),
)
_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_ISO = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:[.,](\d{1,9}))?"
    r"(Z|z|[+-]\d{2}:?\d{2})?$"
)
_EPOCH_MIN = 631152000.0   # 1990-01-01: earlier values usually mean a wrong epoch unit
_EPOCH_MAX = 4102444800.0  # 2100-01-01


@dataclass(frozen=True)
class Limits:
    max_bytes: int = 25_000_000
    max_rows: int = 200_000
    max_columns: int = 80
    max_field_chars: int = 4096
    future_tolerance_s: float = 300.0
    max_rejections_listed: int = 500


@dataclass
class IngestionResult:
    status: str
    input: Dict[str, Any]
    profile: Dict[str, Any]
    rows: List[Dict[str, Any]] = field(default_factory=list)
    rejected_rows: List[Dict[str, Any]] = field(default_factory=list)
    rejected_row_count: int = 0
    duplicate_row_count: int = 0
    file_errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    missing_information: List[str] = field(default_factory=list)
    signals: List[Dict[str, Any]] = field(default_factory=list)
    unmapped_signals: List[str] = field(default_factory=list)
    reported_context: Dict[str, Any] = field(default_factory=dict)
    review_queue: List[Dict[str, Any]] = field(default_factory=list)
    duplicate_of: Optional[str] = None
    normalized_sha256: Optional[str] = None
    catalog_version: str = catalog.CATALOG_VERSION
    normalization_version: str = NORMALIZATION_VERSION

    @property
    def committable(self) -> bool:
        return self.status in (ACCEPTED, ACCEPTED_WITH_WARNINGS, NEEDS_REVIEW) and bool(self.rows)

    def to_dict(self, include_rows: bool = True) -> Dict[str, Any]:
        out = {
            "status": self.status, "committable": self.committable, "input": self.input, "profile": self.profile,
            "catalog_version": self.catalog_version, "normalization_version": self.normalization_version,
            "accepted_row_count": len(self.rows), "rejected_row_count": self.rejected_row_count,
            "duplicate_row_count": self.duplicate_row_count, "rejected_rows": self.rejected_rows,
            "file_errors": self.file_errors, "warnings": self.warnings,
            "missing_information": self.missing_information, "signals": self.signals,
            "unmapped_signals": self.unmapped_signals, "reported_context": self.reported_context,
            "review_queue": self.review_queue, "duplicate_of": self.duplicate_of,
            "normalized_sha256": self.normalized_sha256,
            "boundary": "Validation and normalization only. Signal meaning comes from the explicit profile; "
                        "no anomaly, cause or recovery is inferred at import.",
        }
        if include_rows:
            out["rows"] = self.rows
        return out


class _RowError(ValueError):
    pass


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical_rows_sha256(rows: Sequence[Dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(list(rows), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _extension(filename: str) -> str:
    name = (filename or "").strip().lower()
    dot = name.rfind(".")
    return name[dot:] if dot > 0 else ""


def _finite(value: Any) -> float:
    if isinstance(value, bool):
        raise _RowError("boolean where a number is required")
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise _RowError("empty number")
        if not _NUMBER.match(text):
            raise _RowError(f"not a plain decimal number: '{text[:40]}'")
        number = float(text)
    else:
        raise _RowError("number required")
    if not math.isfinite(number):
        raise _RowError("non-finite number")
    return number


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        raise _RowError("nested value where text is required")
    text = str(value).strip()
    return text or None


def _parse_timestamp(value: Any, profile: ImportProfile) -> Tuple[float, List[str]]:
    flags: List[str] = []
    if profile.timestamp_format == "epoch":
        seconds = _finite(value) / EPOCH_UNITS[profile.epoch_unit]
    else:
        text = _text(value)
        if text is None:
            raise _RowError("empty timestamp")
        match = _ISO.match(text)
        if not match:
            raise _RowError(f"timestamp is not ISO 8601: '{text[:40]}'")
        year, month, day, hour, minute, second, fraction, zone = match.groups()
        if zone is None:
            if profile.naive_timestamp_offset is None:
                raise _RowError("timestamp has no timezone and the profile declares none")
            zone = profile.naive_timestamp_offset
            flags.append("timezone_from_profile")
        if zone in ("Z", "z"):
            tz = timezone.utc
        else:
            sign = 1 if zone[0] == "+" else -1
            digits = zone[1:].replace(":", "")
            hours, minutes = int(digits[:2]), int(digits[2:])
            if hours > 23 or minutes > 59:
                raise _RowError("invalid timezone offset")
            tz = timezone(sign * timedelta(hours=hours, minutes=minutes))
        try:
            moment = datetime(int(year), int(month), int(day), int(hour), int(minute), int(second), tzinfo=tz)
        except ValueError as error:
            raise _RowError(f"invalid calendar time: {error}") from None
        seconds = moment.timestamp() + (int(fraction) / 10 ** len(fraction) if fraction else 0.0)
        if fraction and len(fraction) > 6:
            flags.append("sub_microsecond_precision_truncated")
    if not _EPOCH_MIN <= seconds <= _EPOCH_MAX:
        raise _RowError("timestamp outside 1990–2100; check the epoch unit or clock")
    return seconds, flags


def _iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace("+00:00", "Z")


def _detect_unsupported(raw: bytes, filename: str, profile: ImportProfile) -> Optional[str]:
    for magic, label in _MAGIC:
        if raw.startswith(magic):
            return f"{label} detected from file content; only {profile.format.upper()} is declared for this profile"
    extension = _extension(filename)
    if extension in _KNOWN_UNSUPPORTED:
        return f"{_KNOWN_UNSUPPORTED[extension]} ({extension}) is not a supported import format yet"
    if extension not in _EXTENSIONS[profile.format]:
        expected = " or ".join(_EXTENSIONS[profile.format])
        return f"File extension '{extension or '(none)'}' does not match the profile format {profile.format} (expected {expected})"
    return None


def _decode(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise ValueError("UTF-16 encoding detected; export as UTF-8")
    if b"\x00" in raw:
        offset = raw.index(b"\x00")
        raise ValueError(f"NUL byte at offset {offset}; file is binary or corrupt")
    body = raw[3:] if raw.startswith(b"\xef\xbb\xbf") else raw
    try:
        return body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError(f"Invalid UTF-8 at byte offset {error.start}; export as UTF-8") from None


def _reject_constant(constant: str):
    raise ValueError(f"non-finite JSON constant {constant}")


def _records(text: str, profile: ImportProfile, limits: Limits) -> Tuple[List[Tuple[int, int, Any]], List[str], List[Dict[str, Any]]]:
    """Return (row_index, source_line, record) triples, file-level warnings and structural row rejections."""
    warnings: List[str] = []
    structural: List[Dict[str, Any]] = []
    records: List[Tuple[int, int, Any]] = []
    if profile.format == "csv":
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        try:
            header = next(reader)
        except StopIteration:
            raise ValueError("CSV file has no header row") from None
        except csv.Error as error:
            raise ValueError(f"Malformed CSV header: {error}") from None
        header = [h.strip() for h in header]
        if not header or any(not h for h in header):
            raise ValueError("CSV header contains an empty column name")
        if len(header) != len(set(header)):
            duplicates = sorted({h for h in header if header.count(h) > 1})
            raise ValueError(f"CSV header has duplicate column names: {', '.join(duplicates)}")
        if len(header) > limits.max_columns:
            raise ValueError(f"CSV has {len(header)} columns; limit is {limits.max_columns}")
        missing = sorted(set(profile.columns.values()) - set(header))
        if missing:
            raise ValueError(f"Mapped columns not found in the CSV header: {', '.join(missing)}")
        index = 0
        blank = 0
        try:
            for values in reader:
                if not values or all(not v.strip() for v in values):
                    blank += 1
                    continue
                index += 1
                if index > limits.max_rows:
                    raise ValueError(f"More than {limits.max_rows} data rows; split the export")
                if len(values) != len(header):
                    structural.append({"row": index, "line": reader.line_num, "reasons": [f"row has {len(values)} fields; header has {len(header)}"], "raw": ",".join(values)[:300]})
                    continue
                records.append((index, reader.line_num, dict(zip(header, values))))
        except csv.Error as error:
            raise ValueError(f"Malformed CSV near line {reader.line_num}: {error}") from None
        if blank:
            warnings.append(f"{blank} blank line(s) ignored")
    elif profile.format == "json":
        try:
            data = json.loads(text, parse_constant=_reject_constant)
        except (ValueError, RecursionError) as error:
            raise ValueError(f"Invalid JSON: {str(error)[:200]}") from None
        if not isinstance(data, list):
            raise ValueError("JSON import must be an array of objects")
        if len(data) > limits.max_rows:
            raise ValueError(f"More than {limits.max_rows} records; split the export")
        for index, item in enumerate(data, 1):
            if not isinstance(item, dict):
                structural.append({"row": index, "line": None, "reasons": ["array element is not an object"], "raw": json.dumps(item)[:300]})
                continue
            records.append((index, None, item))
    else:  # jsonl
        index = 0
        for line_number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            index += 1
            if index > limits.max_rows:
                raise ValueError(f"More than {limits.max_rows} records; split the export")
            try:
                item = json.loads(line, parse_constant=_reject_constant)
            except (ValueError, RecursionError) as error:
                structural.append({"row": index, "line": line_number, "reasons": [f"invalid JSON: {str(error)[:120]}"], "raw": line[:300]})
                continue
            if not isinstance(item, dict):
                structural.append({"row": index, "line": line_number, "reasons": ["line is not a JSON object"], "raw": line[:300]})
                continue
            records.append((index, line_number, item))
    return records, warnings, structural


def _cell(record: Dict[str, Any], profile: ImportProfile, role: str, limits: Limits) -> Any:
    column = profile.columns.get(role)
    if column is None:
        return None
    value = record.get(column)
    if isinstance(value, str) and len(value) > limits.max_field_chars:
        raise _RowError(f"{role} field exceeds {limits.max_field_chars} characters")
    return value


def _normalize_row(record: Dict[str, Any], profile: ImportProfile, expected_asset: str, now_s: float, limits: Limits) -> Dict[str, Any]:
    reasons: List[str] = []
    flags: List[str] = []
    for role in ("timestamp", "asset", "signal"):
        if profile.columns[role] not in record:
            reasons.append(f"missing mapped field '{profile.columns[role]}' ({role})")
    if reasons:
        raise _RowError("; ".join(reasons))

    raw_asset = _text(_cell(record, profile, "asset", limits))
    if raw_asset is None:
        raise _RowError("empty asset identity")
    asset = profile.asset_aliases.get(raw_asset, raw_asset)
    if asset != expected_asset:
        raise _RowError(f"asset '{raw_asset[:60]}' is not machine '{expected_asset}' (declare an explicit asset alias if it is the same machine)")

    t, time_flags = _parse_timestamp(_cell(record, profile, "timestamp", limits), profile)
    flags.extend(time_flags)
    allowance = max(limits.future_tolerance_s, profile.max_clock_skew_s or 0.0)
    if t > now_s + allowance:
        raise _RowError("timestamp is in the future beyond the declared clock tolerance")

    source_signal = _text(_cell(record, profile, "signal", limits))
    if source_signal is None:
        raise _RowError("empty signal name")
    canonical = profile.signal_map.get(source_signal)
    scope = _text(_cell(record, profile, "scope", limits)) or profile.default_scope
    quality = _text(_cell(record, profile, "quality", limits))
    row: Dict[str, Any] = {
        "t": t, "time_utc": _iso(t), "clock_domain": profile.clock_domain, "asset": asset,
        "signal": canonical, "source_signal": source_signal, "kind": None, "domain": None,
        "value": None, "unit": None, "original_value": None, "original_unit": None,
        "event_value": None, "code": None, "code_description": None, "code_source": None,
        "command_id": None, "scope": scope, "quality": quality, "flags": flags,
    }
    raw_value = _cell(record, profile, "value", limits)
    raw_unit = _text(_cell(record, profile, "unit", limits))
    if raw_value is not None and not isinstance(raw_value, (dict, list)):
        row["original_value"] = str(raw_value).strip() if isinstance(raw_value, str) else raw_value
    row["original_unit"] = raw_unit

    if canonical is None:
        if profile.unmapped_signals == "reject":
            raise _RowError(f"signal '{source_signal[:60]}' is not mapped in the profile")
        row["kind"] = "unmapped"
        row["domain"] = catalog.UNSCOPED
        flags.append("unmapped_signal_statistics_only")
        if raw_value not in (None, ""):
            try:
                row["value"] = _finite(raw_value)
                row["unit"] = raw_unit
            except _RowError:
                flags.append("unmapped_non_numeric_value")
        return row

    spec = catalog.get(canonical)
    row["kind"] = spec.kind
    row["domain"] = spec.domain
    if spec.kind == catalog.MEASUREMENT:
        if raw_value in (None, ""):
            raise _RowError(f"{canonical} requires a numeric value")
        if raw_unit is None:
            raise _RowError(f"{canonical} requires a unit; units are never assumed")
        number = _finite(raw_value)
        unit_for_catalog = profile.unit_aliases.get(canonical, {}).get(raw_unit, raw_unit)
        try:
            converted, unit = spec.convert(number, unit_for_catalog)
        except ValueError as error:
            raise _RowError(str(error)) from None
        if spec.minimum is not None and converted < spec.minimum or spec.maximum is not None and converted > spec.maximum:
            raise _RowError(f"{canonical} value {converted:g} {unit} outside the physically valid range")
        row["value"] = converted
        row["unit"] = unit
        if unit_for_catalog != raw_unit:
            flags.append("unit_alias_from_profile")
    elif spec.kind in (catalog.EVENT, catalog.HEARTBEAT):
        event_value = _text(_cell(record, profile, "event_value", limits))
        if event_value is None and raw_value not in (None, "") and spec.kind == catalog.EVENT:
            event_value = _text(raw_value)
        row["event_value"] = event_value
    elif spec.kind == catalog.FAULT_CODE:
        code = _text(_cell(record, profile, "code", limits)) or _text(raw_value)
        if code is None:
            raise _RowError("fault.code requires a code")
        row["code"] = code
        row["code_description"] = _text(_cell(record, profile, "code_description", limits))
        source_system = _text(_cell(record, profile, "code_source", limits))
        row["code_source"] = source_system
        if source_system and source_system in profile.fault_source_domains:
            row["domain"] = profile.fault_source_domains[source_system]
        else:
            flags.append("fault_source_domain_unknown")
        if row["code_description"] is None:
            flags.append("fault_code_not_decoded")
    elif spec.kind == catalog.COMMAND:
        command_id = _text(_cell(record, profile, "command_id", limits))
        if command_id is None:
            raise _RowError(f"{canonical} requires a command_id")
        row["command_id"] = command_id
    return row


def _signal_summaries(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[str, Optional[str], Optional[str]], List[Dict[str, Any]]] = {}
    for row in rows:
        key = (row["signal"] or "unmapped:" + row["source_signal"], row["scope"], row["unit"])
        groups.setdefault(key, []).append(row)
    summaries = []
    for (signal, scope, unit), items in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1] or "", kv[0][2] or "")):
        times = sorted(r["t"] for r in items)
        gaps = [b - a for a, b in zip(times, times[1:])]
        numeric = [r["value"] for r in items if isinstance(r["value"], (int, float))]
        summaries.append({
            "signal": signal, "scope": scope, "unit": unit, "kind": items[0]["kind"], "domain": items[0]["domain"],
            "count": len(items), "start_utc": _iso(times[0]), "end_utc": _iso(times[-1]),
            "max_gap_s": max(gaps) if gaps else None,
            "median_interval_s": sorted(gaps)[len(gaps) // 2] if gaps else None,
            "min": min(numeric) if numeric else None, "max": max(numeric) if numeric else None,
            "distinct_values": sorted({r["event_value"] or r["code"] for r in items if (r["event_value"] or r["code"])})[:20] or None,
        })
    return summaries


def _missing_information(rows: Sequence[Dict[str, Any]], profile: ImportProfile) -> List[str]:
    signals = {r["signal"] for r in rows if r["signal"]}
    notes: List[str] = []
    network_measured = [r for r in rows if r["domain"] == catalog.NETWORK and r["kind"] == catalog.MEASUREMENT]
    if network_measured and any(not r["scope"] for r in network_measured):
        notes.append("Measurement endpoint not stated for some network probes; their values describe an unknown path, not necessarily the command path.")
    elif network_measured and "measurement_endpoints" not in profile.source_metadata:
        notes.append("Probe endpoints are named per row but not documented in the profile; confirm whether they share the command path.")
    if profile.max_clock_skew_s is None:
        notes.append(f"Clock skew of '{profile.clock_domain}' is unknown; latency across different clocks will be reported as unavailable.")
    stages = [s.id for s in sorted(catalog.CATALOG.values(), key=lambda s: s.stage_order or 0) if s.kind == catalog.COMMAND]
    present = [s for s in stages if s in signals]
    if not present:
        notes.append("No command send/receive/output/feedback records; the delay cannot be localized along the command chain from this file.")
    elif len(present) < len(stages):
        notes.append("Command chain incomplete in this file; missing stages: " + ", ".join(s for s in stages if s not in present) + ".")
    if "app.heartbeat" not in signals and "app.heartbeat_interval" not in signals:
        notes.append("No application heartbeat records in this file.")
    if not signals & {"controller.event", "controller.reset", "fault.code"}:
        notes.append("No controller events, resets or fault codes in this file.")
    context_keys = ("oem", "model", "kit_version", "control_version", "vehicle_interface")
    absent = [k for k in context_keys if k not in profile.source_metadata]
    if absent:
        notes.append("Reported machine context missing from the profile: " + ", ".join(absent) + ".")
    return notes


def ingest(
    raw: bytes,
    filename: str,
    profile: ImportProfile,
    expected_asset: str,
    *,
    now: Optional[datetime] = None,
    existing_hashes: Optional[Dict[str, str]] = None,
    limits: Limits = Limits(),
) -> IngestionResult:
    """Validate and normalize one file. Never raises for bad input; the result carries the reasons."""
    now_dt = now or datetime.now(timezone.utc)
    if now_dt.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    digest = sha256_bytes(raw)
    info = {"filename": filename, "bytes": len(raw), "sha256": digest}
    profile_info = {"profile_id": profile.profile_id, "version": profile.version, "sha256": profile.sha256,
                    "format": profile.format, "clock_domain": profile.clock_domain, "synthetic": profile.synthetic}
    result = IngestionResult(status=REJECTED, input=info, profile=profile_info,
                             reported_context={k: {"value": v, "status": "reported", "source": f"import profile {profile.profile_id} v{profile.version}"}
                                               for k, v in sorted(profile.source_metadata.items())})

    def fail(status: str, message: str) -> IngestionResult:
        result.status = status
        result.file_errors.append(message)
        if status in (UNSUPPORTED_FORMAT, REJECTED):
            result.review_queue.append({"reason": status, "detail": message, "filename": filename, "sha256": digest, "state": "open"})
        return result

    if existing_hashes and digest in existing_hashes:
        result.status = DUPLICATE_UPLOAD
        result.duplicate_of = existing_hashes[digest]
        result.warnings.append("Identical file content was already imported for this machine; no new rows created.")
        return result
    if not raw:
        return fail(REJECTED, "File is empty")
    if len(raw) > limits.max_bytes:
        return fail(REJECTED, f"File is {len(raw)} bytes; limit is {limits.max_bytes}")
    unsupported = _detect_unsupported(raw, filename, profile)
    if unsupported:
        return fail(UNSUPPORTED_FORMAT, unsupported)
    try:
        text = _decode(raw)
        records, warnings, structural = _records(text, profile, limits)
    except ValueError as error:
        return fail(REJECTED, str(error))
    result.warnings.extend(warnings)

    rejected: List[Dict[str, Any]] = list(structural)
    accepted: List[Dict[str, Any]] = []
    for index, line, record in records:
        try:
            row = _normalize_row(record, profile, expected_asset, now_dt.timestamp(), limits)
        except _RowError as error:
            rejected.append({"row": index, "line": line, "reasons": [str(error)], "raw": json.dumps(record, default=str)[:300]})
            continue
        row["row"] = index
        if line is not None:
            row["line"] = line
        row["raw"] = record
        accepted.append(row)

    # Duplicate rows: identical observation repeated in the same file. Kept once, counted, never silently merged across files.
    seen = set()
    unique: List[Dict[str, Any]] = []
    for row in accepted:
        key = (row["t"], row["signal"] or "unmapped:" + row["source_signal"], row["value"], row["event_value"], row["code"], row["command_id"], row["scope"])
        if key in seen:
            result.duplicate_row_count += 1
            continue
        seen.add(key)
        unique.append(row)
    if result.duplicate_row_count:
        result.warnings.append(f"{result.duplicate_row_count} duplicate row(s) in this file were kept once.")

    # Order: report non-monotonic time per signal/scope in file order, then sort deterministically.
    last: Dict[Tuple[Any, Any], float] = {}
    out_of_order = 0
    for row in unique:
        key = (row["signal"] or row["source_signal"], row["scope"])
        if key in last and row["t"] < last[key]:
            out_of_order += 1
            row["flags"].append("out_of_order_in_source")
        last[key] = max(row["t"], last.get(key, row["t"]))
    if out_of_order:
        result.warnings.append(f"{out_of_order} row(s) arrived out of time order in the source; original row numbers are preserved.")
    unique.sort(key=lambda r: (r["t"], r["row"]))

    total = len(records) + len(structural)
    result.rejected_row_count = len(rejected)
    result.rejected_rows = sorted(rejected, key=lambda r: r["row"])[: limits.max_rejections_listed]
    result.rows = unique
    result.unmapped_signals = sorted({r["source_signal"] for r in unique if r["signal"] is None})
    if result.unmapped_signals:
        result.warnings.append("Unmapped signals kept for statistics only: " + ", ".join(result.unmapped_signals[:20]))
    if any("timezone_from_profile" in r["flags"] for r in unique):
        result.warnings.append(f"Some timestamps had no offset; {profile.naive_timestamp_offset} was applied from the profile.")
    result.signals = _signal_summaries(unique)
    result.missing_information = _missing_information(unique, profile)
    result.normalized_sha256 = canonical_rows_sha256([{k: v for k, v in r.items() if k != "raw"} for r in unique]) if unique else None

    if total == 0:
        return fail(REJECTED, "File contains no data rows")
    if not unique:
        return fail(REJECTED, "No row passed validation")
    fraction = len(rejected) / total
    if fraction > profile.max_rejected_fraction:
        return fail(REJECTED, f"{len(rejected)} of {total} rows rejected ({fraction:.0%}), above the profile tolerance of {profile.max_rejected_fraction:.0%}")
    if rejected:
        result.status = NEEDS_REVIEW
        result.review_queue.append({"reason": "rejected_rows", "detail": f"{len(rejected)} of {total} rows rejected; review before relying on coverage",
                                    "filename": filename, "sha256": digest, "state": "open"})
    elif result.warnings:
        result.status = ACCEPTED_WITH_WARNINGS
    else:
        result.status = ACCEPTED
    return result


def iter_measurements(rows: Iterable[Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
    """Numeric rows only (mapped measurements and numeric unmapped rows)."""
    for row in rows:
        if isinstance(row.get("value"), (int, float)):
            yield row
