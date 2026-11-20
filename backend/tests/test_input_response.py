"""Input–Response Baseline on SYNTHETIC series: identification, eligibility gates and change detection."""
import importlib.util
from pathlib import Path

from app.analysis import input_response as ir

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("synthetic_generator", ROOT / "scripts" / "make_synthetic_remote_delay.py")
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)
START = gen.START.timestamp() + gen.VEHICLE_CLOCK_OFFSET_S


def rows(slowdown_after=None, clock="vehicle_controller_clock", input_clock=None):
    out = []
    for r in gen.io_rows(slowdown_after=slowdown_after):
        signal = ir.INPUT_SIGNAL if r["name"] == "rx_cmd_boom" else ir.RESPONSE_SIGNAL
        out.append({"t": r["ts"] / 1000.0, "signal": signal, "scope": r["ch"], "value": r["val"],
                    "clock_domain": (input_clock or clock) if signal == ir.INPUT_SIGNAL else clock})
    return out


def test_identifies_delay_and_gain_of_the_synthetic_channel():
    model = ir.identify(rows(), "boom", START, START + 300)
    assert model["status"] == "identified", model
    m = model["model"]
    assert 0.15 <= m["delay_s"] <= 0.25 and abs(m["gain"] - gen.IO_GAIN) < 0.02 and m["correlation"] > 0.99
    assert model["excitation"]["input_std"] > ir.DEFAULTS["min_input_std"]


def test_slowdown_is_detected_and_unchanged_behaviour_is_not():
    data = rows(slowdown_after=600)
    baseline = ir.identify(data, "boom", START, START + 540)
    incident = ir.identify(data, "boom", START + 660, START + 840)
    comparison = ir.compare(baseline, incident, context_comparability={"comparable": True})
    assert comparison["status"] == "changed" and comparison["observations"][0]["observation"] == "response_delay_increased"
    assert 0.25 <= comparison["delay_change_s"] <= 0.35
    steady = rows()
    same = ir.compare(ir.identify(steady, "boom", START, START + 540), ir.identify(steady, "boom", START + 660, START + 840), context_comparability={"comparable": True})
    assert same["status"] == "no_change_detected" and not same["observations"]


def test_comparison_refused_when_context_not_comparable():
    data = rows()
    result = ir.compare(ir.identify(data, "boom", START, START + 300), ir.identify(data, "boom", START + 400, START + 700),
                        context_comparability={"comparable": False, "missing_confirmation": {"firmware_version": ["first"]}})
    assert result["status"] == "not_comparable"


def test_eligibility_gates():
    flat = [{**r, "value": 10.0} if r["signal"] == ir.INPUT_SIGNAL else r for r in rows()]
    assert "Insufficient input excitation" in ir.identify(flat, "boom", START, START + 300)["reason"]
    assert "one shared clock domain" in ir.identify(rows(input_clock="console_clock"), "boom", START, START + 300)["reason"]
    assert "Not enough overlapping coverage" in ir.identify(rows(), "boom", START, START + 5)["reason"]
    assert ir.identify(rows(), "stick", START, START + 300)["status"] == "unavailable"
    noisy = [{**r, "value": ((i * 7919) % 200) - 100} if r["signal"] == ir.RESPONSE_SIGNAL else r for i, r in enumerate(rows())]
    assert ir.identify(noisy, "boom", START, START + 300)["status"] == "no_stable_relation"
