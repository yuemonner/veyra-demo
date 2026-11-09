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


def test_a_ros_launch_on_the_bench_is_not_a_driven_launch():
    from app.agent import experiment_safety as S
    lidar = {"action": "Compare the new driver's port and baud defaults with the old package's, offline first.",
             "how": ["Launch rplidar_with_args.launch.py with the baud value the old package uses, a few times.",
                     "Run the old rplidar_ros launch right after and confirm it still reads S/N."],
             "where": "lab", "conditions": "Same Jetson and lidar on the bench. Cold start and warm start."}
    assert not S.classify(lidar, hv_system=False)["hazardous"]
    shuttle = {"action": "Launch the shuttle on the same ramp at a cold start", "where": "on_site"}
    assert S.classify(shuttle, hv_system=True)["worst"] == "fault_reproduction"
