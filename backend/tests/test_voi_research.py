"""Research VOI planner (backend/research/voi): constraints first, explicit ranges, stop rule. Not part of the product."""
from research.voi.planner import Check, Scenario, plan


def _sc(check_delay=(10, 20), lik_good=(0.9, 0.95), excluded=None):
    hyps = {"remote": (1, 1), "hands": (1, 1)}
    actions = {"remote fix": {"remote": (100, 100), "hands": (3000, 3000)}, "dispatch": {"remote": (3000, 3000), "hands": (1000, 1000)}}
    check = Check(id="LOG", label="log", cost=(10, 10), delay=check_delay,
                  outcomes={"says_remote": {"remote": lik_good, "hands": (1 - lik_good[1], 1 - lik_good[0])},
                            "says_hands": {"remote": (1 - lik_good[1], 1 - lik_good[0]), "hands": lik_good}})
    return Scenario(case="t", hypotheses=hyps, actions=actions, checks=[check], excluded=excluded or {})


def test_cheap_decisive_evidence_is_worth_getting_first():
    out = plan(_sc(), n=300)
    assert out["decision"]["kind"] == "gather" and out["decision"]["option"] == "check: LOG"


def test_stop_rule_when_evidence_is_slow_or_uninformative():
    slow = plan(_sc(check_delay=(5000, 6000)), n=300)
    useless = plan(_sc(lik_good=(0.5, 0.5)), n=300)
    assert slow["decision"]["kind"] in ("act", "toss_up") and not slow["decision"]["option"].startswith("check")
    assert not useless["decision"]["option"].startswith("check")


def test_excluded_actions_are_never_scored():
    sc = _sc(excluded={"act: keep operating": "safety"})
    out = plan(sc, n=100)
    assert all(o["option"] != "act: keep operating" for o in out["options"]) and out["excluded"] == {"act: keep operating": "safety"}
