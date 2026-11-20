"""One investigator, two kinds of evidence: the main AI investigator on a recording case also gets the Physical Check
Engine's tools when the case's uploaded recording has a compatible signal profile, and its report is held to the
physical assessment. All data SYNTHETIC; the model is scripted."""
import copy
import json

import pytest

from app.agent import investigator as inv
from app.agent import physical_layer as PL
from app.agent.llm import ScriptedModel
from app.physchecks import agent_bridge, synthetic as S
from app.physchecks.profile import Profile
from tests.test_recording_investigation import _csv, _profile  # noqa: F401  (SYNTHETIC roll recording)
from app.store.sqlite_store import CaseStore


class _Model(ScriptedModel):
    provider = "anthropic"


@pytest.fixture()
def case(tmp_path):
    store = CaseStore(tmp_path / "w.db")
    c = store.create_case(machine_id="UAV-T", title="SYNTHETIC case", actor="t", synthetic=True)
    store.commit_import(c["id"], _csv(), "uav.csv", _profile(), actor="t", review_reason="test")
    store.investigate(c["id"], actor="t")
    yield store, c["id"]
    store.close()


def _layer(profile):
    rec = S.incident()
    return PL.PhysicalLayer("rec-00000000000000ab", rec, profile, agent_bridge.PhysicalCheckTools(rec, profile))


def _report(headline, explanation, hypotheses, tools, layer=None):
    n = next(i for i, l in enumerate(tools.raw, 1) if l.startswith("SERIES"))
    from app.agent import action_gate
    refs = [{"ref": m["ref"], "statement": "noted"} for m in action_gate.material_refs(layer.tools)] if layer else []
    return {"safety_findings": refs, "headline": headline, "status": "cause_unknown", "confidence": "low", "explanation": explanation,
            "evidence": [{"line": n, "quote": tools.raw[n - 1][:40], "shows": "the series", "calls": ["p1"]}],
            "checked": [], "next_action": {"action": "Read the BMS history", "why": "separates", "how": ["export"], "where": "remote",
                                           "steps": [{"kind": "read_data", "text": "Export the BMS history"}],
                                           "outcomes": [{"label": "a", "means": "x", "keeps": [], "rules_out": []}, {"label": "b", "means": "y", "keeps": [], "rules_out": []}]},
            "unknowns": ["x"], "events": [], "hypotheses": hypotheses}


def test_physical_tools_are_offered_and_the_report_is_held_to_them(case, monkeypatch):
    monkeypatch.setenv("VEYRA_INQUIRY", "off")      # the physical-layer contract alone; the inquiry loop is tested in test_inquiry
    store, cid = case
    layer = _layer(S.profile())
    monkeypatch.setattr(PL, "for_case", lambda store, case_id, context=None: layer)
    tools = inv.tools_for(store, cid)
    bad = _report("HV was lost", "The HV loss was caused by the contactors opening.",
                  [{"id": "h1", "area": "Protection", "claim": "the root cause is the contactor opening", "status": "leading", "because": "x"}], tools, layer)
    good = _report("HV was lost after an SOC correction the measured charge does not explain",
                   "The contactors opened as the protective response; the upstream cause is open (SOC estimation, capacity or current measurement).",
                   [{"id": "h1", "area": "SOC estimation", "claim": "SOC estimation drift is a candidate upstream cause", "status": "open", "because": "Check A",
                     "physical_ref": "A"}], tools, layer)
    model = _Model([
        {"content": [{"type": "tool_use", "id": "p1", "name": "physical_checks", "input": {}},
                     {"type": "tool_use", "id": "p2", "name": "physical_hypotheses", "input": {}}]},
        {"content": [{"type": "tool_use", "id": "s1", "name": "submit_report", "input": bad}]},
        {"content": [{"type": "tool_use", "id": "s2", "name": "submit_report", "input": good}]},
    ])
    r = inv.investigate(store, cid, model=model)
    assert r["mode"] == "agent" and [s["tool"] for s in r["steps"]] == ["physical_checks", "physical_hypotheses"]
    assert {"physical_checks", "physical_hypotheses", "window"} <= set(model.calls[0]["tools"])
    returned = model.calls[2]["messages"][-1]["content"][0]["content"]
    assert "held to the physical checks and rejected" in returned and "protective response" in returned
    assert r["result"]["headline"] == good["headline"]
    ph = r["physical"]
    assert ph["profile"] == "synthetic-bev" and ph["family_applies"] is True and ph["checks"]["charge_accounting"]["outcome"] == "VIOLATION"
    assert all(c["verdict"] != "rejected" for c in ph["constrained_claims"])


def test_on_a_drone_hv_vehicle_statements_are_returned(case, monkeypatch):
    monkeypatch.setenv("VEYRA_DYNAMICS", "off")        # a scripted run of family scoping; the generated dynamics tests are covered in test_dynamics.py
    store, cid = case
    p = copy.deepcopy(S.PROFILE)
    p.update(id="synthetic-drone", machine_class="px4_vehicle")
    layer = _layer(Profile(p))
    monkeypatch.setattr(PL, "for_case", lambda store, case_id, context=None: layer)
    tools = inv.tools_for(store, cid)
    bad = _report("Battery fault", "Check the BMS and the main contactors.", [], tools, layer)
    good = _report("Remaining charge fell faster than measured current explains", "The estimator type is not stated; this is an observation.", [], tools, layer)
    model = _Model([
        {"content": [{"type": "tool_use", "id": "p1", "name": "physical_hypotheses", "input": {}}]},
        {"content": [{"type": "tool_use", "id": "s1", "name": "submit_report", "input": bad}]},
        {"content": [{"type": "tool_use", "id": "s2", "name": "submit_report", "input": good}]},
    ])
    r = inv.investigate(store, cid, model=model)
    assert "battery-electric-vehicle concept" in model.calls[2]["messages"][-1]["content"][0]["content"]
    assert r["physical"]["family_applies"] is False and r["result"]["headline"] == good["headline"]


def test_without_a_compatible_recording_nothing_changes(case, monkeypatch):
    store, cid = case
    monkeypatch.setattr(PL, "for_case", lambda store, case_id, context=None: None)
    model = _Model([{"content": [{"type": "tool_use", "id": "s1", "name": "submit_report",
                                  "input": _report("x", "y", [], inv.tools_for(store, cid))}]}])
    r = inv.investigate(store, cid, model=model)
    assert "physical" not in r and not any(t.startswith("physical_") for t in model.calls[0]["tools"])


def test_link_case_records_the_source_recording(tmp_path, monkeypatch):
    from app.physchecks import ingested
    d = tmp_path / "rec-00000000000000cd"
    d.mkdir()
    (d / "recording.json").write_text(json.dumps({"id": d.name, "filename": "x.bag", "inspection": {"format": "ros1_bag"}}))
    monkeypatch.setattr(ingested, "recordings_root", lambda: tmp_path)
    PL.link_case(d, "case-1")
    PL.link_case(d, "case-1")
    assert json.loads((d / "recording.json").read_text())["case_ids"] == ["case-1"]
    assert PL.source_recording("case-1") == d.name and PL.source_recording("case-2") is None


# ---------------------------------------------------------------- structured validation, by reference
def _assessment(profile=None):
    return _layer(profile or S.profile()).tools.assessment


def _h(ref, status="open", role="main_cause"):
    return {"id": "h1", "area": "x", "claim": "y", "status": status, "role": role, "physical_ref": ref}


def test_a_hypothesis_must_name_its_physical_reference():
    assert PL.structured({"id": "h1", "area": "x", "claim": "y", "status": "open"}, _assessment())["verdict"] == "rejected"


def test_a_protective_response_cannot_be_the_main_cause_by_id():
    r = PL.structured(_h("P1", "leading"), _assessment())
    assert r["verdict"] == "rejected" and "protective response" in r["reasons"][0]


def test_ruled_out_without_a_verified_outcome_is_rejected_and_open_is_accepted():
    A = _assessment()
    assert PL.structured(_h("B", "ruled_out"), A)["verdict"] == "rejected"
    ok = PL.structured(_h("B", "open"), A)
    assert ok["verdict"] == "accepted" and ok["assessment_status"] == "open"


def test_unknown_reference_is_rejected():
    assert "not in the assessment" in PL.structured(_h("Z"), _assessment())["reasons"][0]


def test_insufficient_alone_cannot_lead_as_main_cause():
    A = _assessment()
    d = next(x for x in A["hypotheses"] if x["id"] == "D")
    r = PL.structured(_h("D", "leading", "main_cause"), A)
    assert d["status"] == "insufficient_alone"           # the SYNTHETIC incident's highest cell falls too (pack-wide depletion)
    assert r["verdict"] == "rejected" and "cannot be the main cause" in r["reasons"][0]
    assert PL.structured(_h("D", "open", "amplifier"), A)["verdict"] == "accepted"


def test_on_a_drone_physical_references_and_hv_words_are_rejected():
    p = copy.deepcopy(S.PROFILE)
    p.update(id="synthetic-drone", machine_class="px4_vehicle")
    A = _assessment(Profile(p))
    assert PL.structured(_h("A"), A)["verdict"] == "rejected"
    hv = dict(_h("none"), claim="the main contactor welded")
    assert PL.structured(hv, A)["verdict"] == "rejected"
    assert PL.structured(dict(_h("none"), claim="remaining charge estimate jumped"), A)["verdict"] == "accepted"
