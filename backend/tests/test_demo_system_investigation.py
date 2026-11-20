"""The public demo's system-level investigation is regenerated from the engine on a SYNTHETIC recording, never edited by hand."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("make_system_investigation", ROOT / "scripts" / "demo" / "make_system_investigation.py")
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def test_committed_demo_data_matches_the_engine():
    body = json.dumps(M.build(), indent=1, ensure_ascii=False, default=str, sort_keys=True) + "\n"
    assert M.OUT.read_text() == body, "run scripts/demo/make_system_investigation.py"


def test_demo_data_is_synthetic_and_shows_both_layers():
    d = json.loads(M.OUT.read_text())
    assert d["synthetic"] is True
    sysl, phys = d["system"], d["physics"]
    assert sysl["sequence"]["events"] and sysl["thresholds"]["rows"] and sysl["restarts"]["rows"]
    assert any(f["id"] == "protection_not_latched" for f in sysl["restarts"]["findings"])
    assert {c["id"] for c in phys["checks"]} >= {"charge_accounting", "restart_eligibility"}
    assert phys["alignment"]["value"]["timing_only"] is True
    assert d["explanations"]["leading"] is None and d["explanations"]["conditional_leading"]["hypothesis"] == "A"
