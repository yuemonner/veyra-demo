"""Copy selected cases (with their imports, runs and hash-chained ledger) from an engine DB into a seed file.

Used to ship the public demo's real-data case. Rows are copied verbatim, so each case's ledger chain
still verifies. Only the listed cases are copied; nothing else in the source database leaves it.

Usage: python scripts/export_case_seed.py <engine.db> <out.sqlite.gz> <case id> [<case id> ...]
"""
from __future__ import annotations

import gzip
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.store.sqlite_store import CaseStore  # noqa: E402


def main(src: str, out: str, case_ids: list) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        seed = Path(tmp) / "seed.sqlite"
        CaseStore(seed).close()  # same schema and append-only triggers as production
        db = sqlite3.connect(f"file:{seed}", uri=True)  # URI mode so the source can be attached read-only
        db.execute("ATTACH DATABASE ? AS src", (f"file:{Path(src).resolve()}?mode=ro",))
        marks = ",".join("?" * len(case_ids))
        found = [r[0] for r in db.execute(f"SELECT id FROM src.cases WHERE id IN ({marks})", case_ids)]
        missing = sorted(set(case_ids) - set(found))
        if missing:
            print("Unknown case ids:", ", ".join(missing))
            return 1
        db.execute(f"INSERT INTO cases SELECT * FROM src.cases WHERE id IN ({marks})", case_ids)
        for table in ("imports", "runs"):
            db.execute(f"INSERT INTO {table} SELECT * FROM src.{table} WHERE case_id IN ({marks}) ORDER BY rowid", case_ids)
        db.execute(f"INSERT INTO ledger(id, case_id, kind, body_json, entry_hash, prev_hash) "
                   f"SELECT id, case_id, kind, body_json, entry_hash, prev_hash FROM src.ledger WHERE case_id IN ({marks}) ORDER BY seq", case_ids)
        # Machine context (e.g. machine_class) is machine-scoped; the cases need it to be investigated again the same way.
        db.execute(f"INSERT INTO context_assertions SELECT * FROM src.context_assertions WHERE machine_id IN "
                   f"(SELECT DISTINCT machine_id FROM src.cases WHERE id IN ({marks})) ORDER BY rowid", case_ids)
        db.commit()
        db.execute("DETACH DATABASE src")
        db.execute("VACUUM")
        db.close()
        store = CaseStore(seed)
        for cid in case_ids:
            result = store.verify(cid)
            ok = result["ledger_chain"]["valid"] and result["imports_intact"] and result["runs_intact"]
            print(cid, "intact (ledger, imports, runs)" if ok else f"VERIFY FAILED: {result}")
            if not ok:
                return 1
        store.close()
        with open(seed, "rb") as raw, gzip.open(out, "wb", compresslevel=9) as packed:
            shutil.copyfileobj(raw, packed)
    print(f"Wrote {out} ({os.path.getsize(out) / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3:]))
