"""Decoded CAN / signal tables (.zip of per-frame CSVs) through the standard recording path: inspection, manifest
verification, profile compatibility by column, loading into the Physical Check Engine. All data here is SYNTHETIC."""
import csv
import hashlib
import json
import shutil
import zipfile

import pytest

from app.ingest import decoded_csv as D
from app.physchecks import base, hypotheses, ingested

PROFILE = {
    "schema": "veyra-signal-profile-v1", "id": "synthetic-bev-tables", "version": "1", "synthetic": True,
    "title": "SYNTHETIC decoded-table vehicle", "machine_class": "battery_electric_vehicle", "conditions": {"has_hv_contactors": True},
    "recording": {"formats": ["decoded_csv_package"], "bind": {"bms": {"topics": ["bms"]}}, "time": "t_rel_s per file"},
    "max_sample_age_s": 0.5, "max_gap_s": 2.0,
    "roles": {
        "pack_current": {"field": "bms:i_pack_a", "unit": "A", "sign": "discharge_positive", "kind": "measurement", "valid": [-1000, 1000], "max_age_s": 0.5},
        "soc": {"field": "bms:soc_pct", "unit": "%", "kind": "estimate", "valid": [0, 100], "resolution": 0.4},
        "rated_capacity": {"field": "bms:cap_ah", "unit": "Ah", "kind": "reported_parameter", "valid": [1, 1000]},
    },
    "parameters": {},
}


def _bms_rows(t0, t1, *, soc0, correction_at=None, drop=30.0, current=40.0, cap=100.0, blank_every=0):
    rows, q, t, n = [], 0.0, t0, 0
    while t <= t1 + 1e-9:
        soc = soc0 - q / (cap * 3600) * 100
        if correction_at is not None:
            soc -= drop * min(1.0, max(0.0, (t - correction_at) / 30))
        soc_q = round(round(soc / 0.4) * 0.4, 3)
        blank = blank_every and n % blank_every == 0
        rows.append({"t_rel_s": f"{t:.3f}", "can_id": "0x100", "i_pack_a": "" if blank else f"{current}", "soc_pct": f"{soc_q}", "cap_ah": f"{cap}",
                     "segment_id": "S1"})
        q += current * 0.5
        t = t0 + (n := n + 1) * 0.5
    return rows


def _write_csv(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def _package(tmp_path, *, tamper=False, extra_subset=True):
    src = tmp_path / "src"
    src.mkdir()
    _write_csv(src / "reference_bms.csv", _bms_rows(0, 1500, soc0=80.0))
    _write_csv(src / "fault_bms.csv", _bms_rows(2000, 2600, soc0=60.0, correction_at=2300))
    if extra_subset:   # a convenience subset of the same frames: the profile does not bind it, so it is never double counted
        _write_csv(src / "fault_hv.csv", _bms_rows(2000, 2010, soc0=60.0))
    files = sorted(src.glob("*.csv"))
    with open(src / "manifest.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["file", "sha256"])
        for p in files:
            w.writerow([p.name, hashlib.sha256(p.read_bytes()).hexdigest()])
    if tamper:
        with open(files[0], "a") as f:
            f.write("9999.0,0x100,1,1,100,S1\n")
    rid = "rec-0123456789abcdef"
    folder = tmp_path / "recordings" / rid
    folder.mkdir(parents=True)
    z = folder / "SYNTHETIC-tables.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for p in sorted(src.iterdir()):
            zf.write(p, f"export/{p.name}")
    return rid, folder, z


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(ingested, "recordings_root", lambda: tmp_path / "recordings")
    monkeypatch.setenv("VEYRA_PRIVATE_CASES_DIR", str(tmp_path / "private"))
    (tmp_path / "private" / "profiles").mkdir(parents=True)
    (tmp_path / "private" / "profiles" / "tables.json").write_text(json.dumps(PROFILE))
    ingested._load_cached.cache_clear()
    return tmp_path


def _register(folder, z):
    insp = D.inspect(z)
    meta = {"id": folder.name, "filename": z.name, "stored_as": z.name, "bytes": z.stat().st_size, "uploaded_at": 0, "inspection": insp}
    (folder / "recording.json").write_text(json.dumps(meta))
    return meta


def test_a_package_is_recognised_and_inspected(env):
    rid, folder, z = _package(env)
    assert D.is_package(z)
    insp = D.inspect(z)
    assert insp["format"] == D.FORMAT and insp["windows"] == ["fault", "reference"]
    groups = {t["topic"]: t for t in insp["topics"]}
    assert set(groups) == {"bms", "hv"} and {"soc_pct", "i_pack_a"} <= {f["path"] for f in groups["bms"]["fields"]}
    assert sorted(groups["bms"]["windows"]) == ["fault", "reference"]
    mc = insp["manifest_check"]
    assert len(mc["verified"]) == 3 and not mc["mismatched"] and not mc["missing"]


def test_a_ros2_bag_zip_is_not_a_table_package(tmp_path):
    z = tmp_path / "bag.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("bag/metadata.yaml", "x")
        zf.writestr("bag/extra.csv", "t_rel_s,a\n0,1\n")
    assert not D.is_package(z)


def test_a_file_that_does_not_match_the_manifest_is_refused(env):
    rid, folder, z = _package(env, tamper=True)
    with pytest.raises(D.PackageError, match="manifest"):
        D.inspect(z)


def test_a_path_outside_the_archive_is_refused(tmp_path):
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../evil_bms.csv", "t_rel_s,a\n0,1\n")
    with pytest.raises(D.PackageError):
        D.extract(z)


def test_blank_cells_are_no_value_never_zero(env):
    rid, folder, z = _package(env)
    vals = [x for x in D.read(z, {"bms": ["i_pack_a"]})]
    assert vals and all(x["value"] == 40.0 for x in vals)


def test_profile_compatibility_checks_columns(env):
    rid, folder, z = _package(env)
    meta = _register(folder, z)
    assert ingested.match(meta, PROFILE)["status"] == "compatible"
    bad = json.loads(json.dumps(PROFILE))
    bad["roles"]["soc"]["field"] = "bms:soc_missing"
    m = ingested.match(meta, bad)
    assert m["status"] == "incompatible" and "soc_missing" in m["reasons"][0]


def test_tables_go_through_the_physical_checks_by_window(env):
    rid, folder, z = _package(env)
    _register(folder, z)
    rec, prof, meta = ingested.load(rid)
    assert prof.id == "synthetic-bev-tables" and set(rec.windows) == {"fault", "reference"}
    checks = base.run_all(rec, prof)
    ca = checks["charge_accounting"]
    assert ca["outcome"] == "VIOLATION"                       # the estimator was shown counting over the 1500 s reference window
    assert ca["windows"]["reference"]["outcome"] == "PASS" and ca["windows"]["fault"]["outcome"] == "VIOLATION"
    ev = next(f for f in ca["findings"] if f["kind"] == "unexplained_soc_correction")["evidence"][0]
    assert ev["file"] == "fault_bms.csv" and ev["row"] > 1     # evidence points at the line in the customer's own file
    A = hypotheses.assess(checks, {}, [], machine=hypotheses.machine_of(prof))
    assert A["family_scope"]["applies"] is True


# ---------------------------------------------------------------- tables -> a case for the main investigator (one investigator)
STATE_PROFILE = json.loads(json.dumps(PROFILE))
STATE_PROFILE["roles"]["bms_fault_level"] = {"field": "bms:fault", "unit": "", "kind": "state", "enum": {"0": "normal", "2": "level 2"}}


def _with_fault(env):
    rid, folder, z = _package(env, extra_subset=False)
    # add a fault column to the fault table and rebuild the zip (manifest dropped: the column changes the file)
    import io
    with zipfile.ZipFile(z) as zf:
        names = zf.namelist()
        files = {n: zf.read(n).decode() for n in names}
    out = {}
    for n, text in files.items():
        if n.endswith("manifest.csv"):
            continue
        lines = text.splitlines()
        lines[0] += ",fault"
        lines[1:] = [l + (",2" if n.endswith("fault_bms.csv") and float(l.split(",")[0]) >= 2300 else ",0") for l in lines[1:]]
        out[n] = "\n".join(lines) + "\n"
    z.unlink()
    shutil.rmtree(folder / "package", ignore_errors=True)
    with zipfile.ZipFile(z, "w") as zf:
        for n, text in out.items():
            zf.writestr(n, text)
    (env / "private" / "profiles" / "tables.json").write_text(json.dumps(STATE_PROFILE))
    return rid, folder, z


def test_tables_become_canonical_rows_with_states_on_change(env):
    rid, folder, z = _with_fault(env)
    _register(folder, z)
    m = ingested.chosen(ingested.recording_meta(rid))
    got = D.extract_rows(z, ingested._concrete(STATE_PROFILE, m["bindings"]), asset="VEH-SYN")
    assert got["t0_s"] == 0.0 and got["counts"]["battery.soc"] > 100 and got["counts"]["battery.capacity_parameter"] > 0
    events = [r for r in got["rows"] if r["signal"] == "battery.state"]
    # per window, on change: fault window 0 -> 2, reference window 0
    assert [e["event"] for e in events] == ["bms_fault_level=0 (normal)", "bms_fault_level=2 (level 2)", "bms_fault_level=0 (normal)"]
    # time placement: recording second = table time - t0
    soc = [r for r in got["rows"] if r["signal"] == "battery.soc"]
    assert (max(r["t"] for r in soc) - D.ANCHOR_NS) / 1e9 == pytest.approx(2600.0)


def test_a_table_upload_debriefs_into_a_case_with_an_evidence_sheet_and_physical_layer(env, monkeypatch):
    from app.agent import physical_layer as PL, recording_tools as RT
    from app.store.sqlite_store import CaseStore
    from app.workflows.debrief import debrief_recording
    rid, folder, z = _with_fault(env)
    _register(folder, z)
    store = CaseStore(env / "w.db")
    try:
        out = debrief_recording(store, z, {}, machine_id="VEH-SYN", title="SYNTHETIC table case", actor="t", synthetic=True, source_name=z.name)
        assert out["recording"]["tables"]["profile"] == "synthetic-bev-tables" and "skipped" in out["discovery"]
        PL.link_case(folder, out["case_id"])
        sheet = RT.RecordingTools(store, out["case_id"])
        assert sheet.available() and any("battery.soc" in l for l in sheet.raw) and any("EVENT" in l and "bms_fault_level=2" in l for l in sheet.raw)
        layer = PL.for_case(store, out["case_id"])
        assert layer is not None and layer.describe()["checks"]["charge_accounting"]["outcome"] == "VIOLATION"
    finally:
        store.close()


def test_arrival_runs_the_main_investigator_on_the_debriefed_case(env, monkeypatch):
    monkeypatch.setenv("VEYRA_INQUIRY", "off")      # scripted report without inquiry tests; the loop is tested in test_inquiry
    """Upload (tables) -> automatic debrief -> the arrival investigation runs Veyra's MAIN investigator (evidence sheet +
    physical layer) and stores it in the engineering ledger. SYNTHETIC data, scripted model."""
    from app.agent import recording_tools as RT
    from app.agent.llm import ScriptedModel
    from app.physchecks import investigation as INV
    from app.physchecks.store import Ledger
    from app.store.sqlite_store import CaseStore
    monkeypatch.setenv("VEYRA_AUTO_DEBRIEF", "true")
    monkeypatch.setenv("VEYRA_ENGINE_DB", str(env / "engine.db"))
    rid, folder, z = _with_fault(env)
    _register(folder, z)
    cid = ingested.auto_debrief(rid)
    assert cid and INV.linked_case(rid) == cid
    store = CaseStore(env / "engine.db")
    raw = RT.RecordingTools(store, cid).raw
    store.close()
    n = next(i for i, l in enumerate(raw, 1) if l.startswith("SERIES battery.soc"))
    from app.agent import action_gate
    from app.physchecks import agent_bridge
    rec_, prof_, _m, _p = ingested.as_case(rid)
    refs = [{"ref": m["ref"], "statement": "noted"} for m in action_gate.material_refs(agent_bridge.PhysicalCheckTools(rec_, prof_))]
    report = {"safety_findings": refs, "headline": "SOC fell by about 30 points that measured charge does not explain", "status": "cause_unknown", "confidence": "medium",
              "explanation": "Check A finds an SOC correction; the upstream cause is open.",
              "evidence": [{"line": n, "quote": raw[n - 1][:40], "shows": "the SOC series", "calls": ["c1"]}],
              "checked": [], "unknowns": ["capacity"], "events": [],
              "next_action": {"action": "Read the BMS charge history", "why": "separates A from B", "how": ["export"], "where": "remote", "template_id": "DOC-BMS-HISTORY",
                              "steps": [{"kind": "read_data", "text": "Export the BMS charge history"}],
                              "outcomes": [{"label": "old calibration", "means": "A", "keeps": ["h1"], "rules_out": []},
                                           {"label": "recent calibration", "means": "B", "keeps": ["h2"], "rules_out": []}]},
              "hypotheses": [{"id": "h1", "area": "SOC estimation", "claim": "estimator drift", "status": "open", "role": "main_cause", "because": "Check A", "physical_ref": "A"},
                             {"id": "h2", "area": "Capacity", "claim": "capacity below parameter", "status": "open", "role": "main_cause", "because": "Check A", "physical_ref": "B"}]}

    class M(ScriptedModel):
        provider = "anthropic"
    model = M([{"content": [{"type": "tool_use", "id": "c1", "name": "overview", "input": {}},
                            {"type": "tool_use", "id": "c2", "name": "physical_checks", "input": {}}]},
               {"content": [{"type": "tool_use", "id": "s1", "name": "submit_report", "input": report}]}])
    led = Ledger(env / "eng.db")
    rec, prof, meta, params = ingested.as_case(rid)
    entry = INV.run(led, rid, rec, prof, meta, params, trigger="ingest", model=model)
    ag = entry["payload"]["agent"]
    assert ag["investigator"] == "main" and ag["mode"] == "agent" and ag["engine_case_id"] == cid
    assert [t["tool"] for t in ag["trace"]] == ["overview", "physical_checks"]
    assert ag["constrained_claims"] and all(c["verdict"] == "accepted" for c in ag["constrained_claims"] if c.get("kind") == "structured")
    assert entry["payload"]["deterministic"]["checks"]["charge_accounting"]["outcome"] == "VIOLATION"


def test_engineering_memory_reaches_the_main_investigator(env, monkeypatch):
    """The arrival investigation hands earlier verified knowledge to the main investigator as physical_earlier_knowledge."""
    from app.agent import physical_layer as PL
    from app.physchecks import agent_bridge
    rid, folder, z = _package(env)
    _register(folder, z)
    rec, prof, _m, _p = ingested.as_case(rid)
    ctx = {"knowledge": {"rule": "r", "items": [{"case_id": "EARLIER", "hypothesis_id": "A", "result": "confirmed", "applicability": "applies"}], "unverified": []},
           "scenario_results": {"here": [], "other_recordings": []}}
    layer = PL.PhysicalLayer(rid, rec, prof, agent_bridge.PhysicalCheckTools(rec, prof, context=ctx))
    names = {s["name"] for s in layer.specs()}
    assert {"physical_earlier_knowledge", "physical_scenario_results"} <= names
    assert layer.call("physical_earlier_knowledge", {})["items"][0]["case_id"] == "EARLIER"
    bare = PL.PhysicalLayer(rid, rec, prof, agent_bridge.PhysicalCheckTools(rec, prof))
    assert "physical_earlier_knowledge" not in {s["name"] for s in bare.specs()}
