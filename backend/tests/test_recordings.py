"""Robot recordings read directly: ROS 1 bag, ROS 2 bag (zipped folder), MCAP. SYNTHETIC recordings."""
import sys
from pathlib import Path

import pytest

pytest.importorskip("rosbags")
pytest.importorskip("mcap")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import make_synthetic_recordings as gen  # noqa: E402
from app.workflows.debrief import debrief_recording  # noqa: E402
from app.ingest import bags, catalog  # noqa: E402
from app.store.sqlite_store import CaseStore  # noqa: E402

SIGNALS = [s["id"] for s in catalog.describe()["signals"]]


@pytest.fixture(scope="module")
def recordings(tmp_path_factory):
    return gen.write_all(tmp_path_factory.mktemp("rec"))


@pytest.mark.parametrize("kind", ["ros1", "ros2", "mcap"])
def test_every_format_reads_the_same_readings(recordings, kind):
    ins = bags.inspect(recordings[kind])
    assert ins["format"] == {"ros1": "ros1_bag", "ros2": "ros2_bag", "mcap": "mcap"}[kind]
    assert {t["topic"] for t in ins["topics"]} == set(gen.TYPES) and abs(ins["duration_s"] - 120) < 0.1
    mapping = bags.validate_mapping(bags.suggest(ins), SIGNALS, [t["topic"] for t in ins["topics"]])
    used = {e["signal"] for t in mapping["topics"] for e in t["entries"]}
    assert {"joint.position", "joint.position_command", "joint.torque", "control.velocity_setpoint", "vehicle.velocity", "vehicle.attitude", "actuator.status"} <= used
    ex = bags.extract(recordings[kind], mapping, asset="QD1")
    assert ex.counts["joint.position"] == ex.counts["joint.position_command"] and 9000 < ex.counts["joint.position"] < 10000  # 4 joints, 20 Hz, 120 s
    knee = [r for r in ex.rows if r["signal"] == "joint.position" and r["scope"] == "LF_KFE"]
    assert all(r["unit"] == "rad" for r in knee)
    events = [r for r in ex.rows if r["signal"] == "actuator.status"]
    assert len(events) == 1 and events[0]["scope"] == "drive/LF_KFE" and "ERROR" in events[0]["event"]


def test_suggested_mapping_never_guesses_unknown_types():
    ins = {"topics": [{"topic": "/my_robot/custom", "type": "my_pkg/msg/Thing", "count": 10, "fields": []},
                      {"topic": "/cmd_vel", "type": "geometry_msgs/msg/Twist", "count": 10, "fields": []}]}
    sug = bags.suggest(ins)
    assert [t["topic"] for t in sug["topics"]] == ["/cmd_vel"] and "my_pkg/Thing" in sug["not_mapped"][0]
    assert sug["topics"][0]["entries"][0]["signal"] == "control.velocity_setpoint"


def test_mapping_is_validated():
    with pytest.raises(bags.RecordingError):
        bags.validate_mapping({"topics": [{"topic": "/x", "entries": [{"kind": "series", "field": "a", "signal": "made.up"}]}]}, SIGNALS)
    with pytest.raises(bags.RecordingError):
        bags.validate_mapping({"topics": [{"topic": "/x", "entries": [{"kind": "series", "field": "a;drop", "signal": "joint.position"}]}]}, SIGNALS)
    with pytest.raises(bags.RecordingError):
        bags.validate_mapping({"topics": [{"topic": "/missing", "entries": [{"kind": "series", "field": "a", "signal": "joint.position"}]}]}, SIGNALS, ["/x"])


def test_not_a_recording_is_refused(tmp_path):
    p = tmp_path / "notes.bag"
    p.write_bytes(b"hello")
    with pytest.raises(bags.RecordingError):
        bags.inspect(p)


def test_zip_paths_cannot_escape(tmp_path):
    import zipfile
    p = tmp_path / "evil.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("../../escape.txt", "x")
    with pytest.raises(bags.RecordingError):
        bags.inspect(p)


def test_upload_to_debrief_finds_the_knee(recordings, tmp_path):
    store = CaseStore(tmp_path / "e.db")
    path = recordings["mcap"]
    out = debrief_recording(store, path, bags.suggest(bags.inspect(path)), machine_id="QD1", title="Left front leg dragging (SYNTHETIC)", actor="Engineer",
                            machine_class="legged_robot", problem_from_s=55, problem_to_s=119, normal_from_s=5, normal_to_s=50,
                            report={"stated_reason": "Robot started limping", "symptom": "erratic_motion", "affected_function": "left front leg", "at_s": 62})
    run = store.latest_run(out["case_id"])
    tracking = [o for o in run["domains"]["vehicle_interface"]["observations"] if o["rule"] == "R-TRACKING"]
    assert tracking and "LF_KFE" in tracking[0]["text"]
    assert out["recording"]["sha256"] == bags.sha256_file(path)
    imp = store.imports(out["case_id"], include_rows=False)[0]
    assert out["recording"]["sha256"] in imp["profile"]["notes"] if "profile" in imp else True
    store.close()
