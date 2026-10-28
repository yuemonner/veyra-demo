"""First-boot seed for a hosted demo engine (VEYRA_ENGINE_SEED=demo).

Only runs when the engine database holds no cases, so it never touches recorded work. It loads:
- the real-data ALFA flight case shipped in fixtures/seed/ (public CMU AirLab dataset, CC0), copied
  verbatim so its hash-chained ledger still verifies;
- the SYNTHETIC field-case story, built through the normal validated store;
- the SYNTHETIC case memory pair on EX2 (incident 1 closed; incident 2 with the same complaint and a different cause).
"""
from __future__ import annotations

import gzip
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Dict, List

SEED_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "seed"
# Real-data cases this process loaded from the seed. On the small hosted demo instance their recorded investigations
# are shown as they are; re-running them is refused (it needs more memory than the free instance has).
SEEDED_IDS: set = set()


def _copy_cases(store, seed_file: Path) -> List[str]:
    with tempfile.TemporaryDirectory() as tmp:
        plain = Path(tmp) / "seed.sqlite"
        with gzip.open(seed_file, "rb") as packed, open(plain, "wb") as raw:
            shutil.copyfileobj(packed, raw)
        db = store.db
        db.execute("ATTACH DATABASE ? AS seed", (str(plain),))
        try:
            ids = [r[0] for r in db.execute("SELECT id FROM seed.cases")]
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO cases SELECT * FROM seed.cases")
            db.execute("INSERT INTO imports SELECT * FROM seed.imports ORDER BY rowid")
            db.execute("INSERT INTO runs SELECT * FROM seed.runs ORDER BY rowid")
            db.execute("INSERT INTO ledger(id, case_id, kind, body_json, entry_hash, prev_hash) "
                       "SELECT id, case_id, kind, body_json, entry_hash, prev_hash FROM seed.ledger ORDER BY seq")
            db.execute("INSERT OR IGNORE INTO context_assertions SELECT * FROM seed.context_assertions ORDER BY rowid")
            db.execute("COMMIT")
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.execute("DETACH DATABASE seed")
    return ids


def seed_if_empty(store) -> Dict[str, object]:
    if store.db.execute("SELECT COUNT(*) FROM cases").fetchone()[0]:
        return {"seeded": False, "reason": "engine database already holds cases"}
    from app.workflows.demo import build_field_case_story
    from app.memory import release
    story = build_field_case_story(store)
    release()
    from app.workflows.case_memory import build_case_memory_demo
    memory = build_case_memory_demo(store)
    release()
    real = []
    for seed_file in sorted(SEED_DIR.glob("*.sqlite.gz")):
        real += _copy_cases(store, seed_file)
    SEEDED_IDS.update(real)  # the real-data cases; the small synthetic EX1 story can be re-investigated
    for case_id in real:
        check = store.verify(case_id)
        if not (check["ledger_chain"]["valid"] and check["imports_intact"] and check["runs_intact"]):
            raise RuntimeError(f"Seed case {case_id} failed verification: {check}")
    extra = _seed_review_cases(store)
    from app.workflows.public_samples import seed_showcase
    showcase = seed_showcase(store)  # a real public log with the investigation recorded with the live model
    SEEDED_IDS.update(showcase)
    advanced = _advance_all(store)
    return {"seeded": True, "real_cases": real, "story": story.get("case_id") if isinstance(story, dict) else None, "case_memory": memory,
            **extra, "showcase": showcase, "autopilot": advanced}


QD1_SEED = SEED_DIR / "synthetic" / "qd1_quadruped.sqlite.gz"


def _seed_review_cases(store) -> Dict[str, object]:
    """The two SYNTHETIC cases the local preview shows: EX1 (dispatch flow) and QD-1 (walking robot, from a generated
    recording, stored as a seed). Best effort: a failure here never stops the engine from starting."""
    out: Dict[str, object] = {}
    from app.memory import release
    try:
        from app.workflows import excavator_demo
        out["ex1"] = excavator_demo.seed(store)
    except Exception as error:  # noqa: BLE001
        out["ex1"] = f"not seeded: {type(error).__name__}: {error}"
    release()
    try:
        # Built once from the generated SYNTHETIC recording (scripts/make_synthetic_recordings.py) and copied verbatim:
        # reading the recording at start would double the free instance's memory.
        out["qd1"] = _copy_cases(store, QD1_SEED)
    except Exception as error:  # noqa: BLE001
        out["qd1"] = f"not seeded: {type(error).__name__}: {error}"
    release()
    return out


def _advance_all(store) -> Dict[str, str]:
    """With the autopilot on, Veyra prepares each case once at start, so visitors see where it stands without writing."""
    import os
    if os.getenv("VEYRA_AUTOPILOT") != "true":
        return {}
    from app.workflows import autopilot
    from app.memory import release
    out = {}
    for c in store.cases():
        if c.get("fault_type") == "known_good_run":
            continue
        try:
            out[c["id"]] = autopilot.advance(store, c["id"]).get("stage")
        except Exception as error:  # noqa: BLE001
            out[c["id"]] = f"failed: {type(error).__name__}"
        release()
    return out
