"""Local preview only: rebuilds the QD-1 test case from the SYNTHETIC sample recording, labelled synthetic.
(An earlier upload of this sample was stored without the synthetic label; the case record is append-only, so the
fix is a correctly labelled case in a fresh test workspace.) Usage: python scripts/private_recreate_qd1.py [workspace_id]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.accounts import data_dir, workspace_db  # noqa: E402
from app.workflows.debrief import debrief_recording  # noqa: E402
from app.ingest import bags  # noqa: E402
from app.store.sqlite_store import CaseStore  # noqa: E402

TITLE = "Left front leg dragging (SYNTHETIC test recording)"
store = CaseStore(workspace_db(sys.argv[1] if len(sys.argv) > 1 else "veyra-test"))
if any(c["title"] == TITLE and c["synthetic"] for c in store.cases()):
    print("QD-1 synthetic case already present")
else:
    sample = data_dir() / "samples" / "synthetic_quadruped.bag"
    mapping = bags.suggest(bags.inspect(sample))
    out = debrief_recording(store, sample, mapping, machine_id="QD-1", title=TITLE, actor="Test Engineer", machine_class="legged_robot",
                            problem_from_s=55, problem_to_s=119, normal_from_s=0, normal_to_s=39,
                            normal_basis="Assumed normal: the start of the recording, before the problem.",
                            report={"stated_reason": "Robot started limping on the left front leg", "symptom": "erratic_motion",
                                    "affected_function": "left front leg", "at_s": 55}, source_name=sample.name, synthetic=True)
    print("QD-1 rebuilt as SYNTHETIC:", out["case_id"])
store.close()
