"""Decoded recordings as columns, with evidence references back to the exact source row.

A recording is one or more WINDOWS on one shared time axis (seconds). Each window holds per-frame decoded rows: one row
per received message, only the fields that message carries are filled. A blank is "not carried in this frame", never
zero. Nothing is resampled, interpolated or carried across a gap.

Storage is columnar per (window, field): time, value, source row, segment and message id, so a 100k-frame window costs
a few MB. Every sample can be turned into an EvidenceRef that names the file, the 1-based line (header = line 1), the
message, the field and the raw value, so a reviewer can open the CSV and find the number.

Loader: `load_decoded_csv(paths_by_window, fields=...)` reads the generic decoded-frame CSV layout
  t_rel_s (or t), [window_id], [segment_id], [channel], [can_id or message], <field columns...>, [quality_flags]
which is what a DBC decode (or Veyra's own canonical CSV export) produces. Only requested fields are kept.
"""
from __future__ import annotations

import bisect
import csv
import hashlib
from array import array
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class EvidenceRef:
    recording: str
    window: str
    file: str
    row: int          # 1-based line number in the source file (header is line 1); 0 for in-memory fixtures
    t: float
    source: str       # message identifier, e.g. "BMS_STATUS" or "<channel>/<can id>"
    field: str
    value: Optional[float]

    def as_dict(self) -> Dict[str, Any]:
        return {"recording": self.recording, "window": self.window, "file": self.file, "row": self.row,
                "t": round(self.t, 3), "source": self.source, "field": self.field, "value": self.value}


@dataclass
class Column:
    """All samples of one field in one window, time-ordered (stable for equal times)."""
    t: array = field(default_factory=lambda: array("d"))
    v: array = field(default_factory=lambda: array("d"))
    row: array = field(default_factory=lambda: array("l"))
    seg: array = field(default_factory=lambda: array("h"))
    src: array = field(default_factory=lambda: array("h"))

    def __len__(self) -> int:
        return len(self.t)


@dataclass
class Sample:
    t: float
    value: float
    index: int        # index into the column


class Recording:
    """A set of windows on one time axis. `synthetic` must be True for every generated fixture."""

    def __init__(self, recording_id: str, *, synthetic: bool, label: str = "", provenance: Optional[Dict[str, Any]] = None):
        self.id = recording_id
        self.synthetic = synthetic
        self.label = label or recording_id
        self.provenance: Dict[str, Any] = dict(provenance or {})
        self._cols: Dict[Tuple[str, str], Column] = {}
        self._files: Dict[Tuple[str, str], str] = {}     # (window, field) -> file name
        self._segments: List[str] = []
        self._sources: List[str] = []
        self.windows: List[str] = []

    # ---- building -------------------------------------------------------------------------------------------------
    def _intern(self, table: List[str], key: str) -> int:
        try:
            return table.index(key)
        except ValueError:
            table.append(key)
            return len(table) - 1

    def add(self, window: str, field_name: str, t: float, value: float, *, row: int = 0, segment: str = "",
            source: str = "", file: str = "") -> None:
        if window not in self.windows:
            self.windows.append(window)
        col = self._cols.setdefault((window, field_name), Column())
        col.t.append(float(t))
        col.v.append(float(value))
        col.row.append(int(row))
        col.seg.append(self._intern(self._segments, segment))
        col.src.append(self._intern(self._sources, source))
        if file:
            self._files[(window, field_name)] = file

    def finalize(self) -> "Recording":
        """Sort every column by time (stable), so lookups can bisect. Windows are ordered by first sample time."""
        for key, col in self._cols.items():
            order = sorted(range(len(col)), key=lambda i: col.t[i])
            if order != list(range(len(col))):
                for name in ("t", "v", "row", "seg", "src"):
                    a = getattr(col, name)
                    setattr(col, name, array(a.typecode, (a[i] for i in order)))
        first = {w: min((c.t[0] for (ww, _), c in self._cols.items() if ww == w and len(c)), default=0.0) for w in self.windows}
        self.windows.sort(key=lambda w: first[w])
        return self

    # ---- reading --------------------------------------------------------------------------------------------------
    def has(self, window: str, field_name: str) -> bool:
        c = self._cols.get((window, field_name))
        return bool(c and len(c))

    def column(self, window: str, field_name: str) -> Column:
        return self._cols.get((window, field_name)) or Column()

    def fields(self, window: Optional[str] = None) -> List[str]:
        return sorted({f for (w, f) in self._cols if window is None or w == window})

    def span(self, window: str) -> Optional[Tuple[float, float]]:
        ts = [(c.t[0], c.t[-1]) for (w, _), c in self._cols.items() if w == window and len(c)]
        return (min(a for a, _ in ts), max(b for _, b in ts)) if ts else None

    def segment_of(self, window: str, field_name: str, i: int) -> str:
        return self._segments[self.column(window, field_name).seg[i]]

    def source_of(self, window: str, field_name: str, i: int) -> str:
        return self._sources[self.column(window, field_name).src[i]]

    def ref(self, window: str, field_name: str, i: int) -> EvidenceRef:
        c = self.column(window, field_name)
        return EvidenceRef(self.id, window, self._files.get((window, field_name), ""), int(c.row[i]), float(c.t[i]),
                           self._sources[c.src[i]], field_name, float(c.v[i]))

    def digest(self) -> str:
        """Content digest of every column (order-independent of how they were added)."""
        h = hashlib.sha256()
        for key in sorted(self._cols):
            c = self._cols[key]
            h.update(repr(key).encode())
            h.update(c.t.tobytes())
            h.update(c.v.tobytes())
        return h.hexdigest()


def latest_at_or_before(col: Column, t: float) -> Optional[int]:
    """Index of the latest sample with time <= t, or None."""
    i = bisect.bisect_right(col.t, t) - 1
    return i if i >= 0 else None


# ---- generic decoded-CSV loader --------------------------------------------------------------------------------------
TIME_COLUMNS = ("t_rel_s", "t", "time_s", "timestamp_s")


def _num(text: str) -> Optional[float]:
    text = (text or "").strip()
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_decoded_csv(recording_id: str, files: Dict[str, Sequence[Path]], *, fields: Iterable[str], synthetic: bool,
                     label: str = "", flag_column: str = "quality_flags") -> Recording:
    """Read decoded per-frame CSVs. `files` maps window -> list of CSV paths (e.g. the BMS and VCU decodes). Do not pass
    overlapping convenience subsets of the same frames (that would double count)."""
    want = set(fields)
    rec = Recording(recording_id, synthetic=synthetic, label=label,
                    provenance={"files": {}, "loader": "decoded-csv-v1"})
    for window, paths in files.items():
        for path in paths:
            path = Path(path)
            rec.provenance["files"][path.name] = {"sha256": sha256_file(path), "window": window}
            with open(path, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                cols = reader.fieldnames or []
                tcol = next((c for c in TIME_COLUMNS if c in cols), None)
                if tcol is None:
                    raise ValueError(f"{path.name}: no time column (one of {TIME_COLUMNS})")
                keep = [c for c in cols if c in want]
                for line, r in enumerate(reader, start=2):
                    t = _num(r.get(tcol, ""))
                    if t is None:
                        continue
                    w = r.get("window_id") or window
                    msg = r.get("can_id") or r.get("message") or ""
                    src = f"{r['channel']}/{msg}" if r.get("channel") else msg
                    seg = r.get("segment_id") or ""
                    for c in keep:
                        v = _num(r.get(c, ""))
                        if v is not None:
                            rec.add(w, c, t, v, row=line, segment=seg, source=src, file=path.name)
    return rec.finalize()
