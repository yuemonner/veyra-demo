"""Loads the REAL public-data cases shipped in fixtures/seed/ into the local test workspace, once.

- ALFA (CMU AirLab, CC0): a fixed-wing flight with a rudder fault, plus the aircraft's 10 normal flights as known-good runs.
- GrandTour (ETH Zurich RSL, MIT per the dataset card): one real ANYmal D mission with no reported fault, and the same
  mission with a SYNTHETIC knee-drive fault injected (that case stays flagged synthetic).

Cases are copied verbatim, so their hash-chained ledgers still verify. Nothing is sent to a model: real cases need a
recorded model_authorization before the AI agent may read them.
Local preview only. Usage: python scripts/private_load_real.py [workspace_id]
"""
import gzip
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.accounts import workspace_db  # noqa: E402
from app.engine_seed import SEED_DIR, _copy_cases  # noqa: E402
from app.store.sqlite_store import CaseStore  # noqa: E402


def seed_ids(seed_file: Path):
    with tempfile.TemporaryDirectory() as tmp:
        plain = Path(tmp) / "seed.sqlite"
        with gzip.open(seed_file, "rb") as packed, open(plain, "wb") as raw:
            shutil.copyfileobj(packed, raw)
        db = sqlite3.connect(plain)
        try:
            return [(r[0], r[1], bool(r[2])) for r in db.execute("SELECT id, title, synthetic FROM cases")]
        finally:
            db.close()


def load(store) -> None:
    have = {c["id"] for c in store.cases()}
    for seed_file in sorted(SEED_DIR.glob("*.sqlite.gz")):
        cases = seed_ids(seed_file)
        present = [c for c in cases if c[0] in have]
        if len(present) == len(cases):
            print(f"{seed_file.name}: already loaded")
            continue
        if present:
            print(f"{seed_file.name}: partly present ({len(present)} of {len(cases)}); left as is")
            continue
        _copy_cases(store, seed_file)
        for case_id, title, synthetic in cases:
            check = store.verify(case_id)
            ok = check["ledger_chain"]["valid"] and check["imports_intact"] and check["runs_intact"]
            print(f"  {'REAL' if not synthetic else 'SYNTHETIC'} {case_id} {title[:70]!r} verified={ok}")
            if not ok:
                raise SystemExit(f"{case_id} failed verification: {check}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    store = CaseStore(workspace_db(args[0] if args else "veyra-test"))
    try:
        load(store)
    finally:
        store.close()
