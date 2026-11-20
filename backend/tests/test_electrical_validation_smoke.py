"""Smoke run of the blind validation (research/electrical_validation) on a few dev seeds, so a regression in the
engine shows in the test suite. The full preregistered run is research/electrical_validation/run.py."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1] / "research" / "electrical_validation"
sys.path.insert(0, str(HERE))

import generate  # noqa: E402
import run  # noqa: E402

from app.physics import electrical as E  # noqa: E402


def test_dev_smoke():
    sealed, eng = {}, {}
    for sc in generate.SCENARIOS:
        for seed in (0, 1, 2):
            lines, truth = generate.generate(sc, seed)
            cid = f"{sc}-{seed}"
            sealed[cid] = truth
            eng[cid] = E.analyse(lines)
    s = run.score(sealed, eng, "v2")
    assert s["fabrication"]["cases"] == 0
    assert s["false_positive"]["cases"] == 0
    assert s["attribution"]["pct"] == 100.0
    assert not s["crashes"]
