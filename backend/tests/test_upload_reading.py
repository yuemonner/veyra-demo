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


def test_a_log_that_only_warns_is_not_reported_as_no_failure(tmp_path):
    """Found 2026-11-09: the real micro-ROS log (session closed right after it opened, 'invalid client key' warning) read as
    'no failure found'. A log that warns now leads with its first warning and stays cause_unknown."""
    store = CaseStore(tmp_path / "t.db")
    log = LOG.parents[1] / "microros-stm32-session-drops/log.txt"
    case = debrief_recording(store, path=log, mapping={}, machine_id="STM32H7", title="Session drops", actor="test", source_name="agent.log")["case_id"]
    out = investigator.deterministic(store, case, "no model")
    assert out["status"] == "cause_unknown" and out["headline"].startswith("No error line; first warning at line 15")


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


def test_the_instance_says_whether_uploads_get_live_ai(tmp_path, monkeypatch):
    """The public demo must not let a visitor think any upload gets a full AI investigation."""
    import importlib
    from fastapi.testclient import TestClient
    monkeypatch.setenv("VEYRA_ENABLE_RUNTIME", "true")
    monkeypatch.setenv("VEYRA_ENGINE_DB", str(tmp_path / "engine.db"))
    monkeypatch.delenv("VEYRA_ENGINE_SEED", raising=False)
    monkeypatch.setenv("VEYRA_INVESTIGATOR", "rules")
    main = importlib.import_module("app.main")
    with TestClient(main.app) as c:
        r = c.get("/engine/mode")
        assert r.status_code == 200 and r.json() == {"mode": "rules_only", "live_ai": False, "model": None, "ai_reads": [], "hosted_demo": False}


def test_every_investigation_view_names_its_mode():
    from pathlib import Path
    fe = Path(__file__).resolve().parents[2] / "frontend" / "app" / "field-case"
    inv = (fe / "Investigation.tsx").read_text()
    assert 'current="replay"' in inv and inv.count("<ModeStrip") >= 3
    page = (fe / "page.tsx").read_text()
    assert "<UploadModeNote" in page and "Run rules-only analysis" in page
    mode = (fe / "Mode.tsx").read_text()
    for name in ("Interactive replay", "Rules-only analysis", "Live AI investigation", "scheduled technical demo"):
        assert name in mode
