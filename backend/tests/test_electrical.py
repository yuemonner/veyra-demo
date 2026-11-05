"""Electrical relations (app/physics/electrical.py) on the SYNTHETIC shuttle log, whose generator sets each branch's
resistance (FL 8, FR 26, RL 9, RR 8 mΩ) and the pack's temperature dependence. This checks that the estimator recovers
what the generator put in and that the isolability step keeps apart what the signals cannot separate. Because we wrote
the generator, it is a development check, not evidence that it works on real vehicles."""
import re
from pathlib import Path

from app.physics import electrical as E

ROOT = Path(__file__).resolve().parents[2]
LOG = (ROOT / "fixtures/synthetic/demo/shuttle-hill-start-stop/log.txt").read_text().split("\n")
TRUE_R = {"FL": 0.008, "FR": 0.026, "RL": 0.009, "RR": 0.008}


def test_branch_and_pack_resistance_are_recovered_with_their_uncertainty():
    r = E.analyse(LOG)
    for s in r["snapshots"]:
        for w, b in s["branches"].items():
            assert abs(b["r_ohm"] - TRUE_R[w]) < 0.004, (s["label"], w, b)
            assert b["r_se_ohm"] < 0.002
    cold = min((s for s in r["snapshots"] if s["pack"]), key=lambda s: s["cell_temp_c"])
    warm = max((s for s in r["snapshots"] if s["pack"]), key=lambda s: s["cell_temp_c"])
    assert cold["pack"]["r_ohm"] > 1.8 * warm["pack"]["r_ohm"]
    assert r["relations_broken"] == ["B_FR", "L", "P"]


def test_what_the_signals_cannot_separate_comes_with_the_measurement_that_would():
    iso = E.analyse(LOG)["isolability"]
    fits = {f["id"] for f in iso["fits"]}
    assert {"terminal", "cable", "share", "pack", "limit"} <= fits and not {"offset", "gain"} & fits
    group = next(g for g in iso["not_separable"] if g["relations"] == ["B_FR"])
    assert set(group["ids"]) == {"terminal", "cable", "share"}
    separated = {i for m in group["measurements"] for i in m["separates"]}
    assert separated == {"terminal", "cable", "share"}
    assert any("torque" in n for n in iso["notes"])


def test_a_voltage_sensor_offset_shows_at_rest_and_is_not_mistaken_for_resistance():
    def shift(line):
        if " MCU_RL " not in line:
            return line
        return re.sub(r"DC_V=([\d.]+)", lambda m: f"DC_V={float(m.group(1)) - 1.0:.2f}", line)
    r = E.analyse([shift(l) for l in LOG])
    assert "O_RL" in r["relations_broken"] and "B_RL" not in r["relations_broken"]
    assert "offset" in {f["id"] for f in E.isolability(set(r["relations_broken"]), "RL")["fits"]}


def test_only_logs_with_pack_and_branch_voltages_are_analysed():
    micro = (ROOT / "fixtures/real/public/microros-stm32-session-drops/log.txt").read_text().split("\n")
    assert E.analyse(micro) is None and E.detect(LOG) is not None
