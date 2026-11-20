"""Signal dynamics (app.physchecks.dynamics), the anticipate screen and the generated tests in the inquiry (inquiry-v3).
SYNTHETIC data only (scripts/make_synthetic_dynamics.py); inputs are seeded, so outputs are reproducible."""
import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import make_synthetic_dynamics as G  # noqa: E402
from app.physchecks import anticipate as ANT, compute as C, dynamics as D, effects as FX  # noqa: E402
from app.physchecks.profile import Profile  # noqa: E402
from app.physchecks.recording import Recording  # noqa: E402


def _ctx(domain, cls, seed):
    rec, prof = G.to_recording(G.draw(domain, cls, seed))
    return C.Context(rec, prof)


def _line_ctx(slope=-0.002, n=600, dt=0.5, q=0.001, sd=0.0, hold=1, gap_at=None):
    rnd = np.random.default_rng(1)
    rec = Recording("t", synthetic=True)
    v = 3.6
    for k in range(n):
        t = k * dt
        if gap_at is not None and gap_at[0] <= t < gap_at[1]:
            continue
        if k % hold == 0:
            v = round((3.6 + slope * t + rnd.normal(0, sd)) / q) * q
        rec.add("w", "x", t, v, row=k + 2, file="f.csv")
    prof = Profile({"schema": "veyra-signal-profile-v1", "id": "t", "synthetic": True, "max_gap_s": 2.0, "max_sample_age_s": 1.0,
                    "roles": {"x": {"field": "x", "unit": "V", "kind": "measurement", "resolution": q}}})
    return C.Context(rec.finalize(), prof)


def test_rate_recovers_a_known_slope_on_timestamps_with_a_standard_error():
    ctx = _line_ctx(slope=-0.002, sd=0.0005)
    q = C.run(ctx, "rate", {"signal": "x", "window": "w"})["quantities"]
    assert abs(q["rate_last_tenth_median"] - (-0.002)) < 0.0003
    assert q["rate_min_se"] > 0 and q["unit"] == "V/s"


def test_rate_never_crosses_a_gap():
    ctx = _line_ctx(gap_at=(100, 110))
    r = C.run(ctx, "rate", {"signal": "x", "window": "w"})
    assert not any(100 <= it["t"] <= 110 for it in r["items"])
    qual = C.run(ctx, "quality", {"signal": "x", "window": "w"})["quantities"]
    assert qual["gaps"] == 1 and qual["segments"] == 2


def test_quality_infers_the_quantisation_and_reports_held_values():
    ctx = _line_ctx(slope=-0.0005, hold=3, q=0.001)
    r = C.run(ctx, "quality", {"signal": "x", "window": "w"})
    assert r["quantities"]["resolution"] == 0.001
    assert r["quantities"]["update_interval"] > r["quantities"]["dt_median"]
    assert any("held" in n for n in r["notes"])


def test_changepoint_finds_an_acceleration_and_brackets_it():
    rec = Recording("t", synthetic=True)
    for k in range(2000):
        t = k * 0.5
        v = 3.6 - 0.0002 * t - (0.002 * (t - 700) if t > 700 else 0.0)
        rec.add("w", "x", t, round(v, 3), row=k + 2, file="f.csv")
    prof = Profile({"schema": "veyra-signal-profile-v1", "id": "t", "synthetic": True, "max_gap_s": 2.0,
                    "roles": {"x": {"field": "x", "unit": "V", "kind": "measurement"}}})
    q = C.run(C.Context(rec.finalize(), prof), "changepoint", {"signal": "x", "window": "w"})["quantities"]
    br = max(q["breaks"], key=lambda b: abs(b["slope_change"]))
    assert br["t_interval"][0] <= 700 <= br["t_interval"][1] + 10
    assert br["slope_change"] < 0


def test_an_estimate_is_labelled_as_such():
    ctx = _ctx("battery", "drift", 3)
    r = C.run(ctx, "rate", {"signal": "soc", "window": "run"})
    assert any("ESTIMATE" in n for n in r["notes"])


@pytest.mark.parametrize("domain", G.DOMAINS)
def test_same_engine_both_domains_one_member_drifting_departs_before_the_event(domain):
    ctx = _ctx(domain, "drift", 5)
    scr = ANT.screen(ctx)
    run = scr["groups"][0]["runs"][0]
    q = run["divergence"]
    assert q["ordering"] == "precedes" and q["lead_s"] > 0
    assert q["diff_departure_z"] >= D.Z
    assert run["warning"]["raised"]


@pytest.mark.parametrize("domain", G.DOMAINS)
def test_healthy_controls_raise_no_warning(domain):
    for cls in ("healthy_varying", "noise"):
        scr = ANT.screen(_ctx(domain, cls, 4))
        assert not any(r["warning"]["raised"] for g in scr["groups"] for r in g["runs"] if "warning" in r), (domain, cls)


def test_timing_skew_bound_and_identity_are_reported():
    q = ANT.screen(_ctx("battery", "drift", 5))["groups"][0]["runs"][0]["divergence"]
    assert q["timing_skew"] is not None and q["identity"] and q["identity_stable"] in (0, 1)
    assert 0 <= q["common_share"] <= 1 and abs(q["common_share"] + q["differential_share"] - 1) < 1e-6


def test_generated_tests_are_registered_before_they_run_and_count():
    from app.agent import inquiry as IQ
    ctx = _ctx("robot", "load", 7)
    iq = IQ.Inquiry(ctx.rec, ctx.profile, {"hypotheses": []})
    assert iq.dyn and len(iq.dyn["generated_tests"]) == 4
    assert all(p["source"] == "veyra_generated" and not p["post_hoc"] for p in iq.predictions.values())
    assert all(t["update"]["counts"] for t in iq.tests.values())
    ids = set(iq.hypotheses)
    assert {"L1", "L2", "L2a", "L2b", "L2c", "L3"} <= ids
    pairs = {tuple(d["pair"]) for d in iq.distinctions()}
    assert ("L1", "L2a") not in pairs                     # group-wide and one member may act together: not rivals


def test_tie_rule_refuses_a_separation_on_signals_neither_cause_acts_on():
    a = FX.audit("divergence", {"roles": ["min_cell_voltage", "max_cell_voltage"]}, "diff_departure_z",
                 [{"hypothesis": "A", "expect": {"gt": 2}}, {"hypothesis": "B", "expect": {"lt": 2}}],
                 {"A": {"soc": "estimate_high"}, "B": {"rated_capacity": "capacity_low"}}, [])
    assert a["errors"] and "manufacture" in a["errors"][0]


def test_next_check_addresses_the_unresolved_pair():
    g = {"library": next(d for d in ANT.domains() if d["id"] == "battery_cells")}
    assert ANT.next_check(g, [("drift", "load")])["id"] == "load_step_response"
    assert ANT.next_check(g, [("component", "external")])["id"] == "first_fault_record"


def test_first_tier_cannot_keep_a_cause_a_test_contradicted_while_a_rival_survived():
    from app.agent import inquiry as IQ
    ctx = _ctx("robot", "load", 7)
    iq = IQ.Inquiry(ctx.rec, ctx.profile, {"hypotheses": []})
    leaves = ["L1", "L2a", "L2b", "L2c", "L3"]
    bad = [(u, o) for u in leaves for o in leaves if u != o and iq.standing(u)["standing"] == "contradicted" and iq.separated(o, u)]
    assert bad, "the generated tests separate at least one pair on this case"
    u, o = bad[0]
    probs = iq.problems({"ranking": [{"hypotheses": [o, u], "tests": [], "basis": "x"}]})
    assert any(f"ranking[0]: {u} is in the first tier" in p for p in probs)
    assert not any("is in the first tier" in p for p in iq.problems({"ranking": [{"hypotheses": [o], "tests": [], "basis": "x"},
                                                                                 {"hypotheses": [u], "tests": [], "basis": "y"}]}))


# ---- dynamics-v2 (anticipate-v2): explained jumps and materiality ----------------------------------------------------
def _div(domain, cls, seed):
    ctx = _ctx(domain, cls, seed)
    g = ANT.focus(ctx) or ANT.groups(ctx)[0]
    return C.run(ctx, "divergence", ANT._args(g, g.get("event")))["quantities"]


@pytest.mark.parametrize("domain", ["battery", "robot"])
def test_jumps_the_load_explains_are_not_a_sensing_signature(domain):
    # a growing resistance / falling torque constant makes the spread step AT load steps; those steps are not glitches
    for seed in (2001, 2002):
        assert (_div(domain, "load", seed)["jump_excess_z"] or 0) < D.Z


def test_a_group_wide_bend_is_not_a_component_departure_materiality():
    # the whole pack running empty amplifies small healthy cell differences: significant, but not one member's own
    q = _div("battery", "common", 2001)
    assert abs(q["diff_departure_z"]) >= D.Z and q["differential_share"] < 0.5 and q["own_departure_z"] == 0.0
    run = ANT.screen(_ctx("battery", "common", 2001))["groups"][0]["runs"][0]
    assert run["warning"]["raised"] is False


def test_a_drifting_member_is_a_material_own_departure():
    q = _div("battery", "drift", 2001)
    assert q["differential_share"] >= 0.5 and q["own_departure_z"] >= D.Z
