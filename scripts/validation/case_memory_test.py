"""Case Memory Test runner (SYNTHETIC EX2). The test itself lives in backend/app/workflows/case_memory.py.

Usage: python scripts/validation/case_memory_test.py [out.json]
Exit code 0 only if every criterion of every variant holds.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.engine import precedents  # noqa: E402
from app.workflows import case_memory  # noqa: E402
from app.store.sqlite_store import CaseStore  # noqa: E402


def run_all(tmp: Path) -> list:
    results = []
    for variant in case_memory.VARIANTS:
        store = CaseStore(tmp / f"{variant}.db")
        results.append(case_memory.scenario(store, variant))
        store.close()
    return results


def main(out: str | None = None) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        results = run_all(Path(tmp))
    if out:
        Path(out).write_text(json.dumps({"synthetic": True, "test": "case memory", "rules": precedents.PRECEDENT_RULES_VERSION, "results": results},
                                        indent=1, default=str))
    for r in results:
        print(f"{r['variant']:<10} {'PASS' if r['passed'] else 'FAIL'}")
        for k, v in r["criteria"].items():
            print(f"   {'ok ' if v else 'NO '} {k}")
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
