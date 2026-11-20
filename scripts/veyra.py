"""Run the Veyra ENGINEER command line from a checkout: python3 scripts/veyra.py --help"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.workflows.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
