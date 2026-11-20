"""Case Memory Test (SYNTHETIC EX2): two related incidents; the second repeats, changes one key condition, or is unrelated."""
import pytest

from app.workflows import case_memory
from app.store.sqlite_store import CaseStore


@pytest.mark.parametrize("variant", case_memory.VARIANTS)
def test_case_memory(tmp_path, variant):
    store = CaseStore(tmp_path / "memory.db")
    r = case_memory.scenario(store, variant)
    store.close()
    assert r["passed"], [k for k, v in r["criteria"].items() if not v]


def test_case_memory_demo_builds(tmp_path):
    store = CaseStore(tmp_path / "demo.db")
    ids = case_memory.build_case_memory_demo(store)
    run = store.latest_run(ids["incident_2"])
    store.close()
    assert run["precedents"]["matches"][0]["case_id"] == ids["incident_1"]
    assert run["precedents"]["matches"][0]["match_basis"] == "reported symptom only"
