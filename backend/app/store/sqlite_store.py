"""Append-only case store on the Python standard library (sqlite3).

Imports, context assertions, runs and ledger entries can be inserted, never updated or deleted:
database triggers enforce it. Original file bytes are kept and re-verified before every investigation.
Plain SQL keeps the schema portable to PostgreSQL (M2) without changing the engine.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.context import model as context_model
from app.engine import investigate as engine
from app.ingest.pipeline import canonical_rows_sha256, ingest, Limits, NEEDS_REVIEW
from app.ingest.profile import ImportProfile
from app.ledger import model as ledger_model

STORE_SCHEMA_VERSION = 1
_APPEND_ONLY = ("imports", "context_assertions", "runs", "ledger")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cases (
  id TEXT PRIMARY KEY, machine_id TEXT NOT NULL, title TEXT NOT NULL, fault_type TEXT NOT NULL,
  synthetic INTEGER NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
  symptom_start REAL, symptom_end REAL, symptom_source TEXT
);
CREATE TABLE IF NOT EXISTS imports (
  id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), filename TEXT NOT NULL, sha256 TEXT NOT NULL,
  content BLOB NOT NULL, profile_json TEXT NOT NULL, report_json TEXT NOT NULL, rows_json TEXT NOT NULL,
  normalized_sha256 TEXT NOT NULL, status TEXT NOT NULL, clock_domain TEXT NOT NULL, max_clock_skew_s REAL,
  actor TEXT NOT NULL, review_reason TEXT, created_at TEXT NOT NULL, UNIQUE(case_id, sha256)
);
CREATE TABLE IF NOT EXISTS context_assertions (
  id TEXT PRIMARY KEY, machine_id TEXT NOT NULL, body_json TEXT NOT NULL, recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS context_by_machine ON context_assertions(machine_id);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), created_at TEXT NOT NULL, run_hash TEXT NOT NULL, body_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, case_id TEXT NOT NULL REFERENCES cases(id),
  kind TEXT NOT NULL, body_json TEXT NOT NULL, entry_hash TEXT NOT NULL, prev_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ledger_by_case ON ledger(case_id, seq);
"""


class StoreError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class CaseStore:
    def __init__(self, path: Path, *, clock=_now):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(_SCHEMA)
        for table in _APPEND_ONLY:
            for op in ("UPDATE", "DELETE"):
                self.db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{op.lower()} BEFORE {op} ON {table} "
                                f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;")
        self.db.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(STORE_SCHEMA_VERSION),))
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # ---------- cases ----------
    def create_case(self, *, machine_id: str, title: str, actor: str, synthetic: bool, symptom_start: Optional[str] = None,
                    symptom_end: Optional[str] = None, symptom_source: Optional[str] = None, fault_type: str = engine.FAULT_TYPE) -> Dict[str, Any]:
        for label, value in (("machine_id", machine_id), ("title", title), ("actor", actor)):
            if not isinstance(value, str) or not value.strip():
                raise StoreError(f"{label} is required")
        start = end = None
        if symptom_start or symptom_end:
            if not (symptom_start and symptom_end and symptom_source):
                raise StoreError("A symptom window needs start, end and its source (who reported it, where)")
            start = context_model.parse_time(symptom_start, "symptom_start").timestamp()
            end = context_model.parse_time(symptom_end, "symptom_end").timestamp()
            if end <= start:
                raise StoreError("symptom_end must be after symptom_start")
        case_id = _id("case")
        self.db.execute("INSERT INTO cases VALUES (?,?,?,?,?,?,?,?,?,?)", (case_id, machine_id.strip(), title.strip(), fault_type, int(synthetic),
                                                                      self.clock().isoformat(), actor.strip(), start, end, symptom_source))
        self.db.commit()
        self.append(case_id, "support_milestone", {"milestone": "triage_started", "observed_at": self.clock().isoformat(), "note": "Case opened in Veyra"},
                    actor=actor, source="veyra://case-created")
        return self.case(case_id)

    def case(self, case_id: str) -> Dict[str, Any]:
        row = self.db.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        if not row:
            raise StoreError(f"Case '{case_id}' not found")
        return dict(row)

    def cases(self) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM cases ORDER BY created_at")]

    # ---------- imports ----------
    def preview_import(self, case_id: str, raw: bytes, filename: str, profile: ImportProfile, limits: Limits = Limits()):
        case = self.case(case_id)
        existing = {r["sha256"]: r["id"] for r in self.db.execute("SELECT id, sha256 FROM imports WHERE case_id = ?", (case_id,))}
        return ingest(raw, filename, profile, case["machine_id"], now=self.clock(), existing_hashes=existing, limits=limits)

    def commit_import(self, case_id: str, raw: bytes, filename: str, profile: ImportProfile, *, actor: str,
                      review_reason: Optional[str] = None, limits: Limits = Limits()) -> Tuple[Dict[str, Any], Any]:
        case = self.case(case_id)
        result = self.preview_import(case_id, raw, filename, profile, limits)
        if not result.committable:
            raise StoreError(f"Import not stored: {result.status}. " + "; ".join(result.file_errors or result.warnings))
        if result.status == NEEDS_REVIEW and not (review_reason or "").strip():
            raise StoreError(f"{result.rejected_row_count} row(s) were rejected. Record a review reason to store the accepted rows.")
        if profile.synthetic != bool(case["synthetic"]):
            raise StoreError("Synthetic profiles only go into synthetic cases, and real data only into real cases")
        import_id = _id("imp")
        rows = [{**r, "import_id": import_id} for r in result.rows]
        report = result.to_dict(include_rows=False)
        self.db.execute("INSERT INTO imports VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            import_id, case_id, filename, result.input["sha256"], raw, json.dumps(profile.to_dict()), json.dumps(report),
            json.dumps(rows, separators=(",", ":")), canonical_rows_sha256(rows), result.status, profile.clock_domain, profile.max_clock_skew_s,
            actor, review_reason, self.clock().isoformat()))
        self.db.commit()
        # Reported context from the profile becomes machine-scoped assertions (never confirmed automatically).
        effective = datetime.fromtimestamp(min(r["t"] for r in result.rows), timezone.utc).isoformat()
        for key, value in profile.source_metadata.items():
            if key in context_model.SCHEMA:
                self.assert_context({"machine_id": case["machine_id"], "key": key, "value": value, "status": context_model.REPORTED,
                                     "source": f"import profile {profile.profile_id} v{profile.version} ({filename})", "actor": actor,
                                     "effective_from": effective, "evidence": [import_id]})
        return self.import_record(import_id, include_rows=False), result

    def import_record(self, import_id: str, include_rows: bool = True) -> Dict[str, Any]:
        row = self.db.execute("SELECT * FROM imports WHERE id = ?", (import_id,)).fetchone()
        if not row:
            raise StoreError(f"Import '{import_id}' not found")
        return self._import(row, include_rows)

    def _import(self, row: sqlite3.Row, include_rows: bool) -> Dict[str, Any]:
        report = json.loads(row["report_json"])
        out = {"id": row["id"], "case_id": row["case_id"], "filename": row["filename"], "sha256": row["sha256"], "status": row["status"],
               "clock_domain": row["clock_domain"], "max_clock_skew_s": row["max_clock_skew_s"], "normalized_sha256": row["normalized_sha256"],
               "actor": row["actor"], "review_reason": row["review_reason"], "created_at": row["created_at"], "report": report,
               "missing_information": report.get("missing_information", []), "synthetic": json.loads(row["profile_json"]).get("synthetic", False)}
        rows = json.loads(row["rows_json"])
        out["integrity_ok"] = hashlib.sha256(row["content"]).hexdigest() == row["sha256"] and canonical_rows_sha256(rows) == row["normalized_sha256"]
        out["span"] = [min(r["t"] for r in rows), max(r["t"] for r in rows)] if rows else None
        if include_rows:
            out["rows"] = rows
        return out

    def imports(self, case_id: str, include_rows: bool = True) -> List[Dict[str, Any]]:
        return [self._import(r, include_rows) for r in self.db.execute("SELECT * FROM imports WHERE case_id = ? ORDER BY created_at, id", (case_id,))]

    def original_bytes(self, import_id: str) -> bytes:
        row = self.db.execute("SELECT content FROM imports WHERE id = ?", (import_id,)).fetchone()
        if not row:
            raise StoreError(f"Import '{import_id}' not found")
        return bytes(row["content"])

    # ---------- context ----------
    def assert_context(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        recorded = self.clock()
        assertion = context_model.validate_assertion(raw, assertion_id=_id("ctx"), recorded_at=recorded)
        self.db.execute("INSERT INTO context_assertions VALUES (?,?,?,?)", (assertion.id, assertion.machine_id, json.dumps(assertion.to_dict()), assertion.recorded_at))
        self.db.commit()
        return assertion.to_dict()

    def assertions(self, machine_id: str) -> List[context_model.Assertion]:
        out = []
        for row in self.db.execute("SELECT body_json FROM context_assertions WHERE machine_id = ? ORDER BY recorded_at, id", (machine_id,)):
            body = json.loads(row["body_json"])
            body["evidence"] = tuple(body.get("evidence", []))
            out.append(context_model.Assertion(**body))
        return out

    def context(self, machine_id: str, at: Optional[str] = None, known_at: Optional[str] = None) -> Dict[str, Any]:
        effective = context_model.parse_time(at, "at").timestamp() if at else self.clock().timestamp()
        known = context_model.parse_time(known_at, "known_at").timestamp() if known_at else self.clock().timestamp()
        return context_model.resolve(self.assertions(machine_id), machine_id, effective_at=effective, known_at=known)

    # ---------- ledger ----------
    def ledger(self, case_id: str) -> List[Dict[str, Any]]:
        return [json.loads(r["body_json"]) for r in self.db.execute("SELECT body_json FROM ledger WHERE case_id = ? ORDER BY seq", (case_id,))]

    def _state(self, case: Dict[str, Any]) -> Dict[str, Any]:
        imports = self.imports(case["id"], include_rows=False)
        context_now = self.context(case["machine_id"])
        return {"input_hashes": {i["id"]: i["sha256"] for i in imports}, "context_hash_current": context_now["hash"],
                "import_ids": [i["id"] for i in imports], "import_spans": {i["id"]: i["span"] for i in imports}}

    def append(self, case_id: str, kind: str, payload: Dict[str, Any], *, actor: str, source: str = "") -> Dict[str, Any]:
        case = self.case(case_id)
        entries = self.ledger(case_id)
        validated = ledger_model.validate(kind, payload, actor, entries, self._state(case), self.clock())
        entry = ledger_model.build_entry(entry_id=_id("led-" + kind.replace("_", "-")), case_id=case_id, kind=kind,
                                         actor=actor, source=source, recorded_at=self.clock(), payload=validated["payload"], links=validated["links"],
                                         prev_hash=entries[-1]["hash"] if entries else ledger_model.GENESIS)
        self.db.execute("INSERT INTO ledger(id, case_id, kind, body_json, entry_hash, prev_hash) VALUES (?,?,?,?,?,?)",
                        (entry["id"], case_id, kind, json.dumps(entry, default=str), entry["hash"], entry["prev_hash"]))
        self.db.commit()
        return entry

    # ---------- runs ----------
    def investigate(self, case_id: str, *, actor: str, goal: str = "reduce_uncertainty",
                    baseline_window: Optional[Tuple[str, str]] = None) -> Dict[str, Any]:
        case = self.case(case_id)
        imports = self.imports(case_id)
        if not imports:
            raise StoreError("Import at least one authorized file before investigating")
        broken = [i["id"] for i in imports if not i["integrity_ok"]]
        if broken:
            raise StoreError("Stored import integrity check failed; investigation refused: " + ", ".join(broken))
        window = None
        if baseline_window:
            window = (context_model.parse_time(baseline_window[0], "baseline start").timestamp(), context_model.parse_time(baseline_window[1], "baseline end").timestamp())
            if window[1] <= window[0]:
                raise StoreError("Baseline window end must be after its start")
        run_id = _id("run")
        run = engine.investigate(case=case, imports=imports, assertions=self.assertions(case["machine_id"]), ledger=self.ledger(case_id),
                                 goal=goal, actor=actor, now=self.clock(), run_id=run_id, baseline_window=window)
        self.db.execute("INSERT INTO runs VALUES (?,?,?,?,?)", (run_id, case_id, run["created_at"], run["run_hash"], json.dumps(run, default=str)))
        self.db.commit()
        self.append(case_id, "investigation_run", {"run_id": run_id, "run_hash": run["run_hash"], "input_hashes": run["input_hashes"],
                                                   "context_hash_current": run["context"]["current_hash"], "context_hash_incident": run["context"]["incident"]["hash"],
                                                   "belief": engine.belief_summary(run), "candidates": engine.candidate_summary(run), "goal": goal,
                                                   "policy_versions": run["versions"], "dispatch_status": run["dispatch"]["status"]},
                    actor=actor, source="veyra://investigation-engine")
        return run

    def runs(self, case_id: str) -> List[Dict[str, Any]]:
        return [json.loads(r["body_json"]) for r in self.db.execute("SELECT body_json FROM runs WHERE case_id = ? ORDER BY created_at, id", (case_id,))]

    def latest_run(self, case_id: str) -> Optional[Dict[str, Any]]:
        runs = self.runs(case_id)
        return runs[-1] if runs else None

    def execute_automatic(self, case_id: str, check_id: str, *, actor: str) -> Dict[str, Any]:
        run = self.latest_run(case_id)
        if run is None:
            raise StoreError("Investigate first")
        check = next((c for c in run["checks"] if c["check_id"] == check_id), None)
        if check is None or check["mode"] != "automatic_read_only":
            raise StoreError(f"{check_id} is not an automatic read-only check; record its manual execution instead")
        if check["availability"]["state"] not in ("ready_automatic", "answered_in_run"):
            raise StoreError(f"{check_id} cannot run now: {check['availability']['state']} — {check['availability']['detail']}")
        if check_id == "CHK-SOURCE-ROWS":
            result = engine.window_rows(run, self.imports(case_id))
        elif check_id == "CHK-CMD-CHAIN":
            result = {"tool": "command_chain", "answered_in_run": True, "finding": run["findings"]["command_chain"]}
        else:
            result = {"tool": "input_response", "answered_in_run": True, "finding": run["findings"]["input_response"]}
        summary = {"tool": result["tool"], "digest": hashlib.sha256(json.dumps(result, sort_keys=True, default=str).encode()).hexdigest(),
                   "count": result.get("count")}
        entry = self.append(case_id, "check_execution", {"run_id": run["id"], "check_id": check_id, "status": "automatic_read_only", "tool_result": summary},
                            actor=actor, source="veyra://read-only-tool")
        return {"entry": entry, "result": result}

    def episodes(self, case_id: str) -> List[Dict[str, Any]]:
        return ledger_model.episodes(self.case(case_id), self.ledger(case_id), {r["id"]: r for r in self.runs(case_id)})

    def verify(self, case_id: str) -> Dict[str, Any]:
        chain = ledger_model.verify_chain(self.ledger(case_id))
        imports = self.imports(case_id, include_rows=False)
        runs_ok = all(engine._digest({k: v for k, v in r.items() if k not in ("trace", "duration_ms", "run_hash")}) == r["run_hash"] for r in self.runs(case_id))
        return {"ledger_chain": chain, "imports_intact": all(i["integrity_ok"] for i in imports), "imports": {i["id"]: i["integrity_ok"] for i in imports},
                "runs_intact": runs_ok}
