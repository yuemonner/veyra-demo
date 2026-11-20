"""Engineering ledger: append-only, hash-chained records for scenarios, scan results and outcome chains.

Nothing is updated in place. A change is a new record; a correction names the record it supersedes and why, so the
history of what was believed when stays readable. Every record names its actor and the actor's kind (engineer, agent,
system): rules elsewhere depend on it (an agent can never verify, a reviewer cannot review their own scenario).

The default database lives with the private cases (~/Veyra-private/engineering.db, or VEYRA_ENGINEERING_DB), outside
the repository, because records can describe a customer's case.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ACTOR_KINDS = ("engineer", "agent", "system")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  id TEXT UNIQUE NOT NULL,
  kind TEXT NOT NULL,
  subject TEXT NOT NULL,
  payload TEXT NOT NULL,
  actor TEXT NOT NULL,
  actor_kind TEXT NOT NULL,
  created_at TEXT NOT NULL,
  supersedes TEXT,
  prev_hash TEXT NOT NULL,
  hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS records_subject ON records(subject, kind);
"""


def default_path() -> Path:
    return Path(os.environ.get("VEYRA_ENGINEERING_DB") or Path.home() / "Veyra-private" / "engineering.db")


class LedgerError(ValueError):
    pass


class Ledger:
    def __init__(self, path: Optional[Path] = None, *, clock=None):
        self.path = Path(path) if path else default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()     # one connection shared by request and background threads: reads are locked too
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)

    def close(self) -> None:
        self.db.close()

    def append(self, kind: str, subject: str, payload: Dict[str, Any], *, actor: str, actor_kind: str,
               supersedes: Optional[str] = None, prefix: str = "rec") -> Dict[str, Any]:
        if actor_kind not in ACTOR_KINDS:
            raise LedgerError(f"actor_kind must be one of {ACTOR_KINDS}")
        if not actor or not actor.strip():
            raise LedgerError("every record needs a named actor")
        with self._lock:
            if supersedes and not self.get(supersedes):
                raise LedgerError(f"cannot supersede unknown record {supersedes}")
            prev = self.db.execute("SELECT hash FROM records ORDER BY seq DESC LIMIT 1").fetchone()
            prev_hash = prev["hash"] if prev else "genesis"
            rid = f"{prefix}-{uuid.uuid4().hex[:12]}"
            created = self._clock().isoformat()
            body = json.dumps(payload, sort_keys=True, default=str)
            h = hashlib.sha256(json.dumps([prev_hash, rid, kind, subject, body, actor, actor_kind, created, supersedes]).encode()).hexdigest()
            self.db.execute("INSERT INTO records(id,kind,subject,payload,actor,actor_kind,created_at,supersedes,prev_hash,hash) VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (rid, kind, subject, body, actor, actor_kind, created, supersedes, prev_hash, h))
            self.db.commit()
        return self.get(rid)

    @staticmethod
    def _row(r: sqlite3.Row) -> Dict[str, Any]:
        return {"id": r["id"], "kind": r["kind"], "subject": r["subject"], "payload": json.loads(r["payload"]), "actor": r["actor"],
                "actor_kind": r["actor_kind"], "created_at": r["created_at"], "supersedes": r["supersedes"], "hash": r["hash"], "seq": r["seq"]}

    def get(self, rid: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            r = self.db.execute("SELECT * FROM records WHERE id=?", (rid,)).fetchone()
        return self._row(r) if r else None

    def records(self, *, subject: Optional[str] = None, kinds: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
        q, args = "SELECT * FROM records WHERE 1=1", []
        if subject is not None:
            q += " AND subject=?"
            args.append(subject)
        kinds = list(kinds or [])
        if kinds:
            q += f" AND kind IN ({','.join('?' * len(kinds))})"
            args += kinds
        with self._lock:
            rows = self.db.execute(q + " ORDER BY seq", args).fetchall()
        return [self._row(r) for r in rows]

    def superseded_ids(self, subject: Optional[str] = None) -> set:
        return {r["supersedes"] for r in self.records(subject=subject) if r["supersedes"]}

    def current(self, *, subject: Optional[str] = None, kinds: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
        """Records not superseded by a later record."""
        gone = self.superseded_ids(subject)
        return [r for r in self.records(subject=subject, kinds=kinds) if r["id"] not in gone]

    def verify_chain(self) -> Dict[str, Any]:
        prev = "genesis"
        with self._lock:
            rows = self.db.execute("SELECT * FROM records ORDER BY seq").fetchall()
        for r in rows:
            h = hashlib.sha256(json.dumps([prev, r["id"], r["kind"], r["subject"], r["payload"], r["actor"], r["actor_kind"], r["created_at"],
                                           r["supersedes"]]).encode()).hexdigest()
            if r["prev_hash"] != prev or r["hash"] != h:
                return {"ok": False, "broken_at": r["id"]}
            prev = r["hash"]
        return {"ok": True}
