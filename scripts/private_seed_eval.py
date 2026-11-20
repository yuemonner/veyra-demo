"""Adds the SYNTHETIC agent-evaluation cases (legged robot QD-7) to the local test workspace, once.
Local preview only. Usage: python scripts/private_seed_eval.py [workspace_id] [--fresh-demo]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.accounts import workspace_db  # noqa: E402
from app.compute import evalcases  # noqa: E402
from app.workflows import excavator_demo  # noqa: E402
from app.store.sqlite_store import CaseStore  # noqa: E402

args = [a for a in sys.argv[1:] if not a.startswith("--")]
store = CaseStore(workspace_db(args[0] if args else "veyra-test"))
if any("SYNTHETIC eval case" in c["title"] for c in store.cases()):
    print("SYNTHETIC eval cases already present")
else:
    print("Added SYNTHETIC eval cases:", evalcases.build(store, actor="Test Engineer"))
print("SYNTHETIC excavator demo case:", excavator_demo.seed(store, fresh="--fresh-demo" in sys.argv))
store.close()
