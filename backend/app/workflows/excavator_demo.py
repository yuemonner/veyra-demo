"""SYNTHETIC demo scenario: from incident to dispatch decision, on a remote-operated excavator.

Remote-operated excavator EX1: the operator reports the boom reacting late. The data is the synthetic EX1 fixture
set (fixtures/synthetic/remote_delay); no private data.

The case stops after the investigation (step 1), so the presenter runs steps 2 and 3 live in the Next action tab:
propose checks, approve one, record the site's result, generate the dispatch readiness report.
"""
from __future__ import annotations

import json

from app.workflows.demo import FILES, FIXTURES, ensure_machine_class
from app.ingest.profile import parse_profile

TITLE = "Excavator EX1: boom reacts late under remote control (SYNTHETIC demo)"
OLD_TITLES = ("Excavator EX1: boom reacts late under remote control",)
ACTOR = "Demo (synthetic)"


def seed(store, fresh: bool = False) -> str:
    """Returns the demo case, creating it once. fresh=True always creates a new one (to present from the start)."""
    for c in store.cases():
        if not fresh and (c["title"] == TITLE or c["title"] in OLD_TITLES):
            return c["id"]
    ensure_machine_class(store)
    case = store.create_case(machine_id="EX1", title=TITLE, actor=ACTOR, synthetic=True,
                             symptom_start="2026-03-04T09:06:00Z", symptom_end="2026-03-04T09:07:30Z",
                             symptom_source="Synthetic: remote operator reports the boom reacting late")
    cid = case["id"]
    for data, profile in FILES:
        store.commit_import(cid, (FIXTURES / data).read_bytes(), data, parse_profile(json.loads((FIXTURES / profile).read_text())), actor=ACTOR)
    for key in ("oem", "model", "kit_version", "control_version", "firmware_version", "vehicle_interface"):
        value = store.context("EX1")["attributes"][key]["value"]
        store.assert_context({"machine_id": "EX1", "key": key, "value": value, "status": "engineer_confirmed", "actor": ACTOR,
                              "source": "Synthetic: machine plate and kit inventory", "effective_from": "2026-03-01T00:00:00Z", "evidence": ["synthetic-photo-001"]})
    store.append(cid, "intervention_report", {"occurred_at": "2026-03-04T09:06:40Z", "intervention_type": "other", "operator": "Synthetic remote operator",
                                              "stated_reason": "Boom reacts late to the joystick", "reported_via": "Synthetic support call",
                                              "symptom": "late_response", "affected_function": "boom"}, actor=ACTOR, source="synthetic support call")
    store.investigate(cid, actor=ACTOR)
    return cid
