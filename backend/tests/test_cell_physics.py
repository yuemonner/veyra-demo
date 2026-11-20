"""Cell-physics checks (external EE advisor's proposal, 2026-11-16) on the SYNTHETIC series-pack model.
These test the checks' logic on injected faults; they do not show the signatures identify real degradation."""
import pytest

from app.physchecks import base
from app.physchecks import synthetic_cells as C

CHECKS = ("pack_cell_consistency", "weak_cell_attribution", "relaxation", "ocv_consistency")


def run(rec, prof, **pp):
    return base.run_all(rec, prof, {k: pp for k in CHECKS})


def test_healthy_pack_passes_every_relationship():
    r = run(C.drive_then_rest(C.Pack()), C.cells_profile(curve="sloped"))
    assert [r[k]["outcome"] for k in CHECKS] == ["PASS"] * 4


def test_extra_resistance_is_attributed_to_resistance_by_two_independent_routes():
    r = run(C.drive_then_rest(C.Pack(cell_r0={3: 0.008})), C.cells_profile(curve="sloped"))
    assert r["weak_cell_attribution"]["outcome"] == "VIOLATION"
    assert "resistance_or_connection" in [s["explanation"] for s in r["weak_cell_attribution"]["windows"]["attribution"]["supported"]]
    assert r["relaxation"]["outcome"] == "VIOLATION" and "ohmic resistance" in r["relaxation"]["summary"]


def test_capacity_fade_points_to_capacity_or_imbalance_never_resistance():
    r = run(C.drive_then_rest(C.Pack(cell_capacity={3: 35.0})), C.cells_profile(curve="sloped"))
    sup = [s["explanation"] for s in r["weak_cell_attribution"]["windows"]["attribution"]["supported"]]
    assert "capacity_or_imbalance" in sup and "resistance_or_connection" not in sup
    assert "DATA-METERED-CYCLE" in [x["template"] for x in r["weak_cell_attribution"]["resolve_by"]]


def test_a_sensing_step_is_seen_as_sensing_and_relaxation_cannot_exclude_sensing():
    r = run(C.drive_then_rest(C.Pack(sense_step=(3, 930, -0.06))), C.cells_profile(curve="sloped"))
    assert [s["explanation"] for s in r["weak_cell_attribution"]["windows"]["attribution"]["supported"]] == ["sensing"]
    assert "sensing offset" in r["relaxation"]["summary"]
    assert any("independent voltage reference" in x["measurement"] for x in r["relaxation"]["resolve_by"])


def test_constant_current_abstains_on_resistance_and_names_what_would_resolve_it():
    r = run(C.simulate(C.Pack(cell_r0={3: 0.008}), [(1800, 40.0)]), C.cells_profile(curve="sloped"))
    w = r["weak_cell_attribution"]
    assert w["outcome"] == "INDETERMINATE" and "cannot be seen" in w["summary"]
    assert "DATA-POST-SHUTDOWN" in [x["template"] for x in w["resolve_by"]]


def test_pack_voltage_derived_from_cells_is_not_evidence():
    r = run(C.drive_then_rest(C.Pack(pack_voltage_independent=False)), C.cells_profile(derived_pack_voltage=True))
    p = r["pack_cell_consistency"]
    assert p["outcome"] == "INDETERMINATE" and p["independence"][0]["independent"] is False


def test_pack_cell_disagreement_is_seen():
    pack = C.Pack()
    rec = C.drive_then_rest(pack)
    prof = C.cells_profile(curve="sloped", series_count=11)        # wrong boundary: one cell too few
    assert run(rec, prof)["pack_cell_consistency"]["outcome"] == "VIOLATION"


def test_without_a_curve_or_series_count_each_check_abstains_on_its_own():
    p = C.CELLS_PROFILE
    import copy
    from app.physchecks.profile import Profile
    q = copy.deepcopy(p)
    q["parameters"] = {}
    r = run(C.drive_then_rest(C.Pack()), Profile(q))
    assert r["ocv_consistency"]["outcome"] == "INDETERMINATE" and "DOC-OCV-CURVE" in [x["template"] for x in r["ocv_consistency"]["resolve_by"]]
    assert r["pack_cell_consistency"]["outcome"] == "INDETERMINATE" and "DOC-THRESHOLDS" in [x["template"] for x in r["pack_cell_consistency"]["resolve_by"]]
    assert r["relaxation"]["outcome"] in ("INDETERMINATE", "PASS")          # describes the lowest cell, cannot compare


def test_soc_counted_from_the_same_current_is_not_independent_evidence():
    r = run(C.drive_then_rest(C.Pack()), C.cells_profile(curve="sloped"))
    ind = r["charge_accounting"]["independence"]
    assert ind and ind[0]["independent"] is False


def test_flat_ocv_region_does_not_establish_soc():
    rec = C.simulate(C.Pack(curve="flat", soc0=60.0), [(120, 0.0), (300, 20.0), (1800, 0.0)])
    r = run(rec, C.cells_profile(curve="flat"))
    assert r["ocv_consistency"]["outcome"] == "INDETERMINATE" and "flat" in r["ocv_consistency"]["summary"]


def test_an_offset_soc_is_seen_at_rest_and_the_growth_pattern_is_not_overclaimed():
    segs = [(600, 30.0), (1800, 0.0), (300, 60.0), (1800, 0.0), (1200, 15.0), (1800, 0.0)]
    r = run(C.simulate(C.Pack(current_offset_a=-3.0), segs), C.cells_profile(curve="sloped"), rest_max_a=4.0)
    o = r["ocv_consistency"]
    assert o["outcome"] == "VIOLATION" and o["windows"]["pattern"]["pattern"]


@pytest.mark.parametrize("check", CHECKS)
def test_every_new_check_records_its_expert_provenance(check):
    r = run(C.drive_then_rest(C.Pack()), C.cells_profile(curve="sloped"))
    assert r[check]["provenance"]["kind"] == "expert_proposal"


def test_cell_physics_adds_evidence_to_the_hypotheses_but_never_changes_a_status():
    from app.physchecks import hypotheses as Hy
    from app.physchecks import synthetic as S
    rec = S.incident()
    checks = base.run_all(rec, S.profile())
    from app.physchecks import facts as F
    facts = F.derive(rec, S.profile(), checks)
    before = Hy.assess(checks, facts, machine=Hy.machine_of(S.profile()))
    extra = dict(checks)
    extra["pack_cell_consistency"] = {"outcome": "VIOLATION", "summary": "pack/N outside the cell range", "findings": []}
    extra["weak_cell_attribution"] = {"outcome": "VIOLATION", "windows": {"attribution": {"supported": [
        {"explanation": "resistance_or_connection", "meaning": "the deficit scales with current"}], "not_separable": []}}}
    after = Hy.assess(extra, facts, machine=Hy.machine_of(S.profile()))
    st = lambda A: {h["id"]: h["status"] for h in A["hypotheses"]}  # noqa: E731
    assert st(before) == st(after)
    e = next(h for h in after["hypotheses"] if h["id"] == "E")
    d = next(h for h in after["hypotheses"] if h["id"] == "D")
    assert any(i["basis"] == "check:pack_cell_consistency" for i in e["supporting"])
    assert any(i["basis"] == "check:weak_cell_attribution" for i in d["supporting"])
    assert any("independent voltage reference" in m for m in e["missing"])


# ---------------------------------------------------------------- defects found on CASE A (v4), fixed after the frozen run
def test_an_unresolvable_instant_step_is_never_reported_as_a_resistance():
    """CASE A v4 reported R0 -0.33 / -1.19 mohm at 6.1 / 8.4 A steps: within sensing resolution, not a resistance."""
    r = run(C.simulate(C.Pack(), [(120, 0.0), (600, 3.0), (900, 0.0)]), C.cells_profile(curve="sloped"), load_min_a=2.0)["relaxation"]
    assert r["windows"]["interruptions"]["items"]                     # 3 A x 2 mohm = 6 mV: within +-10 mV
    for it in r["windows"]["interruptions"]["items"]:
        for part in (it.get("lowest_cell"), it.get("average_cell")):
            if part:
                assert part["r_ohmic_ohm"] is None and part["r_ohmic_note"].startswith("below resolution")
    assert "ratio" not in r["summary"] and "-0." not in r["findings"][0]["statement"]


@pytest.mark.parametrize("seed", range(1, 9))
def test_no_resistance_is_ever_negative(seed):
    r = run(C.simulate(C.Pack(seed=seed, noise_v=0.003), [(120, 0.0), (600, 6.0), (900, 0.0), (300, 8.0), (80, 0.0)]),
            C.cells_profile(curve="sloped"))["relaxation"]
    for it in r["windows"]["interruptions"]["items"]:
        for part in (it.get("lowest_cell"), it.get("average_cell")):
            if part and part["r_ohmic_ohm"] is not None:
                assert part["r_ohmic_ohm"] > 0


def test_a_resolvable_step_still_gives_a_resistance():
    r = run(C.drive_then_rest(C.Pack(cell_r0={3: 0.008})), C.cells_profile(curve="sloped"))["relaxation"]
    assert any((it.get("lowest_cell") or {}).get("r_ohmic_ohm") for it in r["windows"]["interruptions"]["items"])


def test_temperature_recorded_as_max_min_temp_is_used_not_reported_missing():
    """CASE A's profile records max_temp / min_temp; v4 said 'no cell temperature recorded'."""
    import copy
    from app.physchecks.profile import Profile
    p = copy.deepcopy(C.CELLS_PROFILE)
    t = p["roles"].pop("cell_temperature")
    p["roles"]["max_temp"], p["roles"]["min_temp"] = dict(t), dict(t)
    r = run(C.drive_then_rest(C.Pack()), Profile(p))["relaxation"]
    assert not any("no temperature" in a or "no cell temperature" in a for a in r["assumptions"])
    assert r["parameters_used"]["temperature_roles"] == ["max_temp", "min_temp"]
    assert set(r["windows"]["interruptions"]["items"][0]["temperature_c"]) == {"max_temp", "min_temp"}
    p["roles"].pop("max_temp"), p["roles"].pop("min_temp")
    r = run(C.drive_then_rest(C.Pack()), Profile(p))["relaxation"]
    assert any("no temperature recorded" in a for a in r["assumptions"])


def test_an_undeclared_soc_estimator_makes_independence_unknown_not_independent():
    """CASE A's profile marks SOC as an estimate without saying how it is computed; v4 silently treated it as independent."""
    import copy
    from app.physchecks.profile import Profile
    p = copy.deepcopy(C.CELLS_PROFILE)
    p["roles"]["soc"].pop("estimator")
    prof = Profile(p)
    assert prof.independence("soc", "pack_current") is None and prof.independent("soc", "pack_current") is True
    ca = run(C.drive_then_rest(C.Pack()), prof)["charge_accounting"]
    assert ca["independence"][0]["independent"] is None
    assert "DOC-SOC-ALGORITHM" in [x["template"] for x in ca["resolve_by"]]
    declared = C.cells_profile()
    assert declared.independence("soc", "pack_current") is False                     # coulomb counting: not independent
    p["roles"]["soc"]["derived_from"] = []                                            # declared: a separate estimator
    assert Profile(p).independence("soc", "pack_current") is True


def test_unknown_independence_is_listed_as_missing_evidence_for_A():
    from app.physchecks import facts as F, hypotheses as Hy, synthetic as S
    import copy
    from app.physchecks.profile import Profile
    p = copy.deepcopy(S.PROFILE)
    p["roles"]["soc"].pop("estimator", None)
    prof = Profile(p)
    rec = S.incident()
    checks = base.run_all(rec, prof)
    A = Hy.assess(checks, F.derive(rec, prof, checks), machine=Hy.machine_of(prof))
    a = next(h for h in A["hypotheses"] if h["id"] == "A")
    assert any(m.startswith("How the BMS computes SOC") for m in a["missing"])
