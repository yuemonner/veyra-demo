"""An uploaded text log with an ERROR line is never reported as 'no failure found'."""
from pathlib import Path

from app.agent import investigator
from app.store.sqlite_store import CaseStore
from app.workflows.debrief import debrief_recording

LOG = Path(__file__).resolve().parents[2] / "fixtures/real/public/rplidar-no-scans/log.txt"


def test_rule_based_reading_leads_with_the_first_error(tmp_path):
    store = CaseStore(tmp_path / "t.db")
    case = debrief_recording(store, path=LOG, mapping={}, machine_id="RPLIDAR-A2M8", title="RPLIDAR never streams", actor="test",
                             source_name="rplidar.log")["case_id"]
    out = investigator.deterministic(store, case, "no model")
    assert out["status"] == "cause_unknown"
    assert out["headline"].startswith("First error at line 32")
    assert out["evidence"][0]["line"] == 32 and "Health error" in out["evidence"][0]["quote"]


def test_upload_job_has_a_steps_list():
    src = (Path(__file__).resolve().parents[1] / "app/recordings_api.py").read_text()
    debrief = src[src.index("def start_debrief"):src.index("def _run_job")]
    assert '"steps": []' in debrief
