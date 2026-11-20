"""Add Veyra's evidence-discovery record to the ALFA demo case in the hosted seed, using the PRODUCT code path.

Usage: python scripts/seed/alfa_discovery.py <the case's original ALFA .bag> fixtures/seed/alfa_carbonz_rudder_case.sqlite.gz

The demo case was imported from a subset of the flight's topics. On a live upload, discovery runs automatically after the
investigation (app/workflows/debrief.py). The hosted demo cannot read the 5 MB bag at start-up, so this script runs the
same function (app/workflows/discovery.discover_and_record) once and appends its record to the seed's hash-chained ledger
through the normal store. Replaces the hand-written alfa_source_coverage file.
"""
import gzip
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.ingest import bags  # noqa: E402
from app.store.sqlite_store import CaseStore  # noqa: E402
from app.workflows.discovery import discover_and_record  # noqa: E402


def main(bag: Path, seed: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        plain = Path(tmp) / "seed.sqlite"
        with gzip.open(seed, "rb") as packed, open(plain, "wb") as raw:
            shutil.copyfileobj(packed, raw)
        store = CaseStore(plain)
        case = next(c for c in store.cases() if c.get("fault_type") != "known_good_run")
        if any(e["kind"] == "evidence_discovery" for e in store.ledger(case["id"])):
            raise SystemExit("The seed case already has an evidence-discovery record")
        run = store.latest_run(case["id"])
        entry = discover_and_record(store, case["id"], run, bag, actor="Veyra evidence discovery", source_name=bag.name, sha256=bags.sha256_file(bag))
        check = store.verify(case["id"])
        assert check["ledger_chain"]["valid"] and check["imports_intact"] and check["runs_intact"], check
        store.close()
        with open(plain, "rb") as raw, gzip.open(seed, "wb", compresslevel=9) as packed:
            shutil.copyfileobj(raw, packed)
    print(f"appended {entry['id']} to {seed}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
