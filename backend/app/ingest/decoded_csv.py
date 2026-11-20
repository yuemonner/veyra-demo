"""Decoded CAN / signal tables as a recording: a .zip of per-frame CSVs, uploaded through the standard recording path.

Many machine teams do not hand over bags: they export their CAN (or other bus) traffic already decoded, one CSV per
message group and time window, often with a manifest of SHA-256 hashes. Veyra reads such a package as it is:

  package.zip
    manifest.csv              optional: columns file, sha256 (every listed file is verified before it is read)
    <window>_<group>.csv      one row per frame: a time column (t_rel_s, t_s, time_s or timestamp_s) and decoded fields
    <group>.csv               a file without a window prefix belongs to window "recording"

Nothing is resampled, interpolated or filled: a blank cell is "no value" (never 0). Which columns are which physical
roles (and which groups to read at all, so that subsets that repeat frames are not double counted) is decided by a signal
profile, never guessed here. Topic = group: a profile binds an alias to a group ("bms") and its roles to "bms:<column>".
"""
from __future__ import annotations

import csv
import hashlib
import os
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

FORMAT = "decoded_csv_package"
TIME_COLUMNS = ("t_rel_s", "t_s", "time_s", "timestamp_s")
DEFAULT_WINDOW = "recording"
_NAME = re.compile(r"^(?P<window>[A-Za-z0-9]+)_(?P<group>[A-Za-z0-9_]+)$")
MAX_EXPANDED = 20 * 1024 ** 3


class PackageError(ValueError):
    pass


def is_package(path: Path) -> bool:
    """A .zip whose members include at least one CSV and no ROS 2 bag metadata."""
    path = Path(path)
    if path.suffix.lower() != ".zip" or not zipfile.is_zipfile(path):
        return False
    with zipfile.ZipFile(path) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
    if any(Path(n).name == "metadata.yaml" for n in names):
        return False
    return any(n.lower().endswith(".csv") and Path(n).name.lower() != "manifest.csv" for n in names)


def folder_for(path: Path) -> Path:
    return Path(path).parent / "package"


def extract(path: Path) -> Path:
    """Extract next to the upload (no absolute paths, no '..', bounded size); returns the folder. Idempotent."""
    into = folder_for(path)
    if into.exists():
        return into
    tmp = into.with_name("package.partial")
    tmp.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as zf:
        total = 0
        for info in zf.infolist():
            target = (tmp / info.filename).resolve()
            if not str(target).startswith(str(tmp.resolve()) + os.sep):
                raise PackageError("The .zip contains a path outside the archive")
            total += info.file_size
            if total > MAX_EXPANDED:
                raise PackageError("The .zip expands to more than 20 GB")
        zf.extractall(tmp)
    tmp.rename(into)
    return into


def _csvs(folder: Path) -> List[Path]:
    return sorted(p for p in folder.rglob("*.csv") if p.name.lower() != "manifest.csv" and not p.name.startswith("."))


def split_name(p: Path) -> Tuple[str, str]:
    m = _NAME.match(p.stem)
    return (m.group("window"), m.group("group")) if m else (DEFAULT_WINDOW, p.stem)


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_manifest(folder: Path) -> Dict[str, Any]:
    man = next(iter(sorted(folder.rglob("manifest.csv"))), None)
    out: Dict[str, Any] = {"manifest": None, "verified": [], "mismatched": [], "not_in_manifest": [], "missing": []}
    if man is None:
        return out
    with open(man, newline="", encoding="utf-8-sig") as f:
        rows = {Path(r.get("file") or r.get("filename") or "").name: (r.get("sha256") or "").lower()
                for r in csv.DictReader(f)}
    rows = {k: v for k, v in rows.items() if k and v}
    out["manifest"] = man.name
    present = {p.name: p for p in folder.rglob("*") if p.is_file()}
    for name, want in sorted(rows.items()):
        p = present.get(name)
        if p is None:
            out["missing"].append(name)
        elif _sha256(p) != want:
            out["mismatched"].append(name)
        else:
            out["verified"].append(name)
    out["not_in_manifest"] = sorted(p.name for p in _csvs(folder) if p.name not in rows)
    return out


def _header(p: Path) -> List[str]:
    with open(p, newline="", encoding="utf-8-sig") as f:
        return next(csv.reader(f), [])


def _count_rows(p: Path) -> int:
    with open(p, "rb") as f:
        return max(0, sum(1 for _ in f) - 1)


def inspect(path: Path) -> Dict[str, Any]:
    """What the package holds: groups (as topics) with their columns, windows, row counts, the manifest check."""
    folder = extract(path)
    files = _csvs(folder)
    if not files:
        raise PackageError("The .zip holds no CSV tables")
    check = verify_manifest(folder)
    if check["mismatched"]:
        raise PackageError("Files do not match the manifest's SHA-256: " + ", ".join(check["mismatched"]))
    groups: Dict[str, Dict[str, Any]] = {}
    out_files = []
    for p in files:
        cols = _header(p)
        tcol = next((c for c in TIME_COLUMNS if c in cols), None)
        window, group = split_name(p)
        n = _count_rows(p)
        out_files.append({"file": p.name, "window": window, "group": group, "rows": n, "time_column": tcol, "columns": len(cols)})
        if tcol is None:
            continue
        g = groups.setdefault(group, {"topic": group, "type": "decoded_table", "count": 0, "rate_hz": None, "windows": [], "fields": []})
        g["count"] += n
        g["windows"].append(window)
        for c in cols:
            if c not in g["fields"]:
                g["fields"].append(c)
    if not groups:
        raise PackageError(f"No CSV has a time column (one of {', '.join(TIME_COLUMNS)})")
    for g in groups.values():
        g["fields"] = [{"path": c} for c in g["fields"]]
    return {"format": FORMAT, "duration_s": None, "start_ns": None, "topics": sorted(groups.values(), key=lambda g: g["topic"]),
            "files": out_files, "windows": sorted({f["window"] for f in out_files}), "manifest_check": check,
            "message_count": sum(g["count"] for g in groups.values()),
            "notes": ["Decoded tables are read as they are: no resampling, no interpolation, blanks are not zero."]}


def _num(s: str) -> Optional[float]:
    s = (s or "").strip()
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v == v and v not in (float("inf"), float("-inf")) else None


def read(path: Path, wanted: Dict[str, List[str]]) -> Iterator[Dict[str, Any]]:
    """Yield one sample per (row, wanted column) for the groups in `wanted` (group -> columns), every window, in file order:
    {window, group, column, t, value, row (1-based data row + 1 = line number), segment, source, file}."""
    folder = extract(path)
    for p in _csvs(folder):
        window, group = split_name(p)
        cols = wanted.get(group)
        if not cols:
            continue
        with open(p, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames or []
            tcol = next((c for c in TIME_COLUMNS if c in header), None)
            if tcol is None:
                continue
            keep = [c for c in cols if c in header]
            for line, r in enumerate(reader, start=2):
                t = _num(r.get(tcol, ""))
                if t is None:
                    continue
                w = r.get("window_id") or window
                msg = r.get("can_id") or r.get("message") or ""
                src = f"{r['channel']}/{msg}" if r.get("channel") else (msg or group)
                for c in keep:
                    v = _num(r.get(c, ""))
                    if v is not None:
                        yield {"window": w, "group": group, "column": c, "t": t, "value": v, "row": line,
                               "segment": r.get("segment_id") or "", "source": src, "file": p.name}


# ---------------------------------------------------------------- tables -> canonical rows (the debrief / evidence-sheet path)
# Physical roles of a signal profile -> Veyra catalog signals (catalog v6). A role not listed is not imported into the
# case (the Physical Check Engine still reads it from the tables); roles of kind "state" become battery.state events.
ROLE_CATALOG = {
    "pack_current": ("battery.pack_current", None), "vcu_pack_current": ("battery.pack_current", "as_received_by_vcu"),
    "soc": ("battery.soc", "bms"), "rated_capacity": ("battery.capacity_parameter", None),
    "pack_voltage": ("battery.pack_voltage", "pack"), "min_cell_voltage": ("battery.cell_voltage", "min"),
    "max_cell_voltage": ("battery.cell_voltage", "max"), "min_cell_index": ("battery.cell_index", "min"),
    "max_cell_index": ("battery.cell_index", "max"), "max_temp": ("battery.temperature", "max"), "min_temp": ("battery.temperature", "min"),
    "low_voltage": ("vehicle.low_voltage", None), "speed": ("vehicle.ground_speed", None),
}
UNIT_ALIASES = {"℃": "degC", "°C": "degC", "C": "degC", "fraction": "%"}
ANCHOR_NS = 946_684_800 * 10 ** 9          # 2000-01-01T00:00:00Z: tables carry relative seconds, not wall-clock time
TABLE_MAPPING_VERSION = "decoded-tables-v1"


def _generic(role: str, spec: Dict[str, Any]) -> Optional[Tuple[str, Any]]:
    """A role outside the battery catalogue: a command, or a current of one actuator (scope = the role name)."""
    if spec.get("kind") == "command" and spec.get("unit") in ("%", "fraction"):
        return ("vehicle.command_input", role)
    if spec.get("kind", "measurement") == "measurement" and spec.get("unit") in ("A", "mA"):
        return ("actuator.current", role)
    return None


def extract_rows(path: Path, profile: Dict[str, Any], *, asset: str, max_hz: float = 20.0) -> Dict[str, Any]:
    """Canonical rows {t (ns), asset, signal, value, unit, scope, event} from the groups and roles a recording profile binds
    (aliases already resolved to groups). Time: recording second 0 = the earliest time in the bound tables (t0); the
    rows are placed at ANCHOR_NS + (t - t0). Numeric roles keep every frame; state roles become an event on each change."""
    wanted: Dict[str, List[str]] = {}
    plan: Dict[Tuple[str, str], Tuple[str, Any]] = {}
    skipped_roles = []
    for role, spec in profile["roles"].items():
        group, _, col = spec["field"].partition(":")
        if spec.get("kind") == "state":
            plan[(group, col)] = ("state", role)
        elif role in ROLE_CATALOG or _generic(role, spec):
            plan[(group, col)] = ("measure", role)      # roles outside the battery catalogue: a generic canonical signal, scope = role
        else:
            skipped_roles.append(role)
            continue
        wanted.setdefault(group, [])
        if col not in wanted[group]:
            wanted[group].append(col)
    t0 = None
    for x in read(path, wanted):                     # pass 1: the earliest time (no samples are kept)
        t0 = x["t"] if t0 is None or x["t"] < t0 else t0
    if t0 is None:
        raise PackageError("The profile's columns hold no values in these tables")
    rows: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {}
    last_state: Dict[Tuple[str, str], float] = {}
    last_kept: Dict[Tuple[str, str, str], float] = {}
    min_dt = 1.0 / max_hz if max_hz else 0.0
    roles = profile["roles"]
    for x in read(path, wanted):                     # pass 2: rows in file order (each table is in time order)
        kind, role = plan[(x["group"], x["column"])]
        t_ns = ANCHOR_NS + int(round((x["t"] - t0) * 1e9))
        spec = roles[role]
        if kind == "measure":
            signal, scope = ROLE_CATALOG.get(role) or _generic(role, spec)
            k = (x["window"], signal, scope or "")
            if min_dt and k in last_kept and 0 <= x["t"] - last_kept[k] < min_dt:
                continue
            last_kept[k] = x["t"]
            unit = UNIT_ALIASES.get(spec.get("unit") or "", spec.get("unit"))
            value = x["value"] * 100.0 if spec.get("unit") == "fraction" else x["value"]
            rows.append({"t": t_ns, "asset": asset, "signal": signal, "value": value, "unit": unit, "scope": scope})
            counts[signal] = counts.get(signal, 0) + 1
        else:
            key = (x["window"], role)
            if last_state.get(key) == x["value"]:
                continue
            last_state[key] = x["value"]
            raw = f"{x['value']:g}"
            meaning = (spec.get("enum") or {}).get(raw)
            rows.append({"t": t_ns, "asset": asset, "signal": "battery.state", "scope": role,
                         "event": f"{role}={raw}" + (f" ({meaning})" if meaning else "")})
            counts["battery.state"] = counts.get("battery.state", 0) + 1
    ts = [r["t"] for r in rows]
    return {"rows": rows, "counts": counts, "skipped_roles": skipped_roles, "t0_s": t0, "start_ns": min(ts), "end_ns": max(ts),
            "mapping": {"mapping_id": f"{TABLE_MAPPING_VERSION}-{profile['id']}"[:60], "version": TABLE_MAPPING_VERSION, "max_hz": max_hz,
                        "clock_domain": "table_relative_seconds",
                        "topics": [{"topic": g, "entries": [{"signal": (ROLE_CATALOG.get(plan[(g, c)][1]) or _generic(plan[(g, c)][1], profile["roles"][plan[(g, c)][1]]))[0] if plan[(g, c)][0] == "measure" else "battery.state"}
                                                           for c in cols]} for g, cols in wanted.items()]}}
