"""The original bytes of an uploaded text log, kept next to the case database and addressed by their SHA-256.

Imports keep canonical rows (templates for indexing); an engineer and the investigator must see the original lines,
numbers and line numbers. Reading back verifies the hash, so what is shown is what was uploaded.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import List, Optional


def _dir(db_path: Path) -> Path:
    d = Path(db_path).parent / "raw-logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save(db_path: Path, source: Path) -> str:
    data = Path(source).read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    target = _dir(db_path) / f"{sha}.log"
    if not target.exists():
        target.write_bytes(data)
    return sha


def lines(db_path: Path, sha: str) -> Optional[List[str]]:
    target = _dir(db_path) / f"{sha}.log"
    if not target.exists():
        return None
    data = target.read_bytes()
    if hashlib.sha256(data).hexdigest() != sha:
        raise ValueError("The stored log no longer matches its fingerprint")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    return text.splitlines()
