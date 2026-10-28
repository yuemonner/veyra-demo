"""SYNTHETIC demo: two Crazyflie watchdog restarts, reconstructed from public reports (fixtures/synthetic/crazyflie).

Case A (closed, the earlier experience): modelled on crazyflie-firmware issue #531. On reboot the console reported a
watchdog timeout and a failed FreeRTOS queue assert; the cause was a stack overflow of the high-level commander task;
reverting the firmware fixed it. Built through the normal upload path, then closed with that conclusion.

Case B (the new incident an engineer uploads): a restart in flight with a watchdog timeout and NO assert. Veyra reads
the log, states the reset cause the log gives, finds case A as a lesson, and shows why it only partly applies (the
assert at queue.c:1429 that defined A is absent here), so A's check is reused, not its fix.

Both logs are reconstructions in the firmware's standard message formats, not the original logs.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict

from app.workflows.debrief import debrief_recording

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "synthetic" / "crazyflie"
MACHINE_A, MACHINE_B = "CF-LAB-07", "CF-LAB-12"
TITLE_A = "Crazyflie CF-LAB-07 fell mid-air and restarted (SYNTHETIC reconstruction of issue #531)"
ACTOR = "Demo (synthetic)"
SAMPLES: Dict[str, Dict[str, str]] = {
    "crazyflie-restart": {"file": "cf_B_inflight_restart.log", "machine_id": MACHINE_B,
                          "title": "Crazyflie CF-LAB-12 restarted in flight (SYNTHETIC reconstruction of a forum report)",
                          "reason": "Restarted in flight, about 10 s after takeoff. Stack overflow already ruled out by checking stack high-water marks."},
}


def ensure_lesson(store) -> str:
    """Case A, uploaded and closed once. Returns its id."""
    for c in store.cases():
        if c["title"] == TITLE_A:
            return c["id"]
    out = debrief_recording(store, FIXTURES / "cf_A_hlcommander_watchdog.log", {}, machine_id=MACHINE_A, title=TITLE_A, actor=ACTOR,
                            report={"stated_reason": "Fell about 3 s after takeoff on the second run; restarted", "symptom": "stopped_unexpectedly",
                                    "affected_function": "flight", "intervention_type": "other"},
                            source_name="cf_A_hlcommander_watchdog.log", synthetic=True)
    cid = out["case_id"]
    store.assert_context({"machine_id": MACHINE_A, "key": "firmware_version", "value": "2020.06 +41", "status": "reported", "actor": ACTOR,
                          "source": "Build line in the console log (synthetic)", "effective_from": "2020-01-01T00:00:00Z"})
    store.append(cid, "case_closure", {
        "investigation_status": "cause_confirmed", "confirmed_by": "fix_resolved_it", "cause_domain": "control_suite",
        "cause": "Stack overflow of the high-level commander task (its stack had been moved to static memory) corrupted the semaphore "
                 "next to it; the failed queue assert stopped the firmware and the watchdog reset it",
        "evidence_note": "Reverting to the firmware before the stack change stopped the failures over the following flights "
                         "(reconstructed from the public issue thread; synthetic record)",
        "resolution_status": "resolved", "fix": "Reverted the firmware change that moved the commander stack (later: larger stack)", "fix_held": "held",
        "decisive_check": "Per-task stack high-water marks during high-level commander moves, and the memory next to the commander stack",
        "applies_when": "Watchdog restart reported together with a failed FreeRTOS queue assert (queue.c:1429) during high-level commander moves",
        "hours_to_cause": 30, "would_have_saved_time": "Logging stack high-water marks from the first failure"}, actor=ACTOR, source="synthetic closure")
    return cid


def run_sample(store, name: str, progress=lambda step: None) -> Dict[str, object]:
    sample = SAMPLES[name]
    progress("Loading the earlier, closed case")
    ensure_lesson(store)
    return debrief_recording(store, FIXTURES / sample["file"], {}, machine_id=sample["machine_id"], title=sample["title"], actor=ACTOR,
                             report={"stated_reason": sample["reason"], "symptom": "stopped_unexpectedly", "affected_function": "flight",
                                     "intervention_type": "other"},
                             source_name=sample["file"], synthetic=True, progress=progress)
