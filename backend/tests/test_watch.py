"""Anticipate (app.physchecks.watch): watching a recording before any fault. SYNTHETIC, seeded, reproducible."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import make_synthetic_dynamics as G  # noqa: E402
from app.physchecks import anticipate as ANT, compute as C, dynamics as D, watch as W  # noqa: E402


def _ctx(domain, cls, seed):
    rec, prof = G.to_recording(G.draw(domain, cls, seed))
    return C.Context(rec, prof)


def _event_t(ctx):
    g = ANT.groups(ctx)[0]
    e = ANT._first_event(ctx, g)
    return D._event_time(ctx, g["window"], e, None)["t"]


def test_a_drifting_actuator_is_flagged_before_its_protective_stop_with_a_check():
    ctx = _ctx("robot", "drift", 2001)
    te = _event_t(ctx)
    out = W.watch(ctx, as_of=0.5 * te)
    g = out["groups"][0]
    assert g["status"] == "material_departure" and g["as_of"] == 0.5 * te
    assert [x["class"] for x in g["leans_to"]] == ["drift"]
    assert g["recommended_check"] and g["first_observable_t"] < 0.5 * te
    assert any("candidate precursor" in c for c in g["caveats"])


def test_watch_reads_nothing_after_as_of():
    ctx = _ctx("battery", "drift", 2001)
    te = _event_t(ctx)
    g = W.watch(ctx, as_of=0.75 * te)["groups"][0]
    assert all(e.get("t", 0) <= 0.75 * te + 1e-6 for e in g["evidence"] if isinstance(e.get("t"), (int, float)))


def test_healthy_and_pack_wide_cases_raise_no_material_departure():
    for dom, cls in (("battery", "healthy_varying"), ("robot", "healthy_varying"), ("battery", "common")):
        for g in W.watch(_ctx(dom, cls, 2001))["groups"]:
            assert g["status"] != "material_departure", (dom, cls, g["status"])


def test_a_group_wide_bend_is_reported_as_significant_not_material():
    g = W.watch(_ctx("battery", "common", 2001))["groups"][0]
    assert g["status"] == "significant_not_material" and g["recommended_check"] is None


# ---------------------------------------------------------------- watch-v2 / dynamics-v3 (dev seeds, unsealed)
def test_a_growing_load_coupling_is_flagged_although_the_spread_share_is_small():
    ctx = _ctx("battery", "load", 5001)
    g = W.watch(ctx, as_of=_event_t(ctx) - 1.0)["groups"][0]
    assert "coupling_growth" in g["flags"] and g["materiality"]["coupling"]["verdict"] == "material"
    assert [x["class"] for x in g["leans_to"]] == ["load"]


def test_intermittent_glitches_are_flagged_against_peers():
    ctx = _ctx("battery", "sensing", 5002)
    g = W.watch(ctx, as_of=_event_t(ctx) - 1.0)["groups"][0]
    assert "intermittent_transients" in g["flags"] and "sensing" in [x["class"] for x in g["leans_to"]]


def test_controls_raise_no_flag_under_any_route():
    for dom, cls, seed in (("robot", "timing", 5004), ("robot", "noise", 5003), ("battery", "noise", 5004), ("robot", "healthy_varying", 5002)):
        assert W.watch(_ctx(dom, cls, seed))["groups"][0]["flags"] == [], (dom, cls, seed)


def test_materiality_is_an_interval_and_a_borderline_share_is_undetermined_not_dismissed():
    assert D.materiality([(0.47, 0.005)])["verdict"] == "undetermined"       # just under 1/2: neither counted nor dismissed
    assert D.materiality([(0.2, 0.01)])["verdict"] == "immaterial"
    assert D.materiality([(0.8, 0.05), (0.45, 0.01)])["verdict"] == "undetermined"   # baseline choices disagree
    assert D.materiality([(0.8, 0.05), (0.7, 0.05)])["verdict"] == "material"


def test_a_group_wide_ramp_present_from_the_start_is_still_seen_for_a_static_response():
    ctx = _ctx("robot", "common", 5001)
    g = ANT.focus(ctx)
    q = C.run(ctx, "divergence", ANT._args(g, g["event"]))["quantities"]
    assert q["response"] == "static" and q["far_relation"]["z"] > D.Z and q["far_relation"]["predictors"] == ["drive_command"]
    assert q["far_material_change_z"] > D.Z and q["far_materiality"]["verdict"] == "material"   # v2 read 0 and contradicted it
    ext = C.Context(*G.to_recording(G.draw("robot", "external", 5001)))
    ge = ANT.focus(ext)
    assert C.run(ext, "divergence", ANT._args(ge, ge["event"]))["quantities"]["far_material_change_z"] == 0.0


def test_a_sensing_fault_that_trips_before_it_returns_shows_as_an_event_on_a_jump():
    ctx = _ctx("robot", "sensing", 5004)
    g = ANT.focus(ctx)
    q = C.run(ctx, "divergence", ANT._args(g, g["event"]))["quantities"]
    assert q["event_on_jump"] == 1 and q["sensing_signatures"] >= 1
