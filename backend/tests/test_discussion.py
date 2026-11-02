"""Conversational debugging (app/agent/discussion.py) on the REAL micro-ROS public log: the engineer's guess is split into
parts, each tested against the data with read-only tools, and every part is held to the evidence rules (quotes, numbers,
claim scope). No model is called: a scripted stand-in plays the model (never shown as AI output)."""
import pytest

from app.agent import discussion as disc
from app.agent import investigator as inv
from app.agent.llm import ScriptedModel
from app.store.sqlite_store import CaseStore
from app.workflows import public_samples

NAME = "microros-stm32-session-drops"
ZERO_LINE = "[1707258509.539450] debug    | UDPv4AgentLinux.cpp | recv_message             | [==>> UDP <<==]        | client_key: 0x2A2ED975, len: 36, data: "


@pytest.fixture()
def case(tmp_path):
    s = CaseStore(tmp_path / "d.db")
    cid = public_samples.run_sample(s, NAME)["case_id"]
    s.append(cid, "model_authorization", {"provider": "anthropic", "scope": "test", "authorized_by": "t", "evidence": "test"}, actor="t")
    yield s, cid
    s.close()


def _model(responses):
    m = ScriptedModel(responses)
    m.provider = "anthropic"
    return m


def _tool(i, name, args=None):
    return {"content": [{"type": "tool_use", "id": i, "name": name, "input": args or {}}]}


HYPS = [{"id": "h1", "area": "Host / agent side", "claim": "The agent mishandles valid data", "status": "weakened", "because": "replies match input", "basis": "indirect"},
        {"id": "h2", "area": "Board Ethernet / lwIP DMA", "claim": "Zeroed below the transport callback", "status": "leading", "because": "fits"}]
NEXT = {"action": "Break at the transport write callback", "why": "Splits buffers from DMA", "how": ["Set a breakpoint"], "where": "on_site",
        "outcomes": [{"label": "Zeros at write", "means": "client buffers", "keeps": ["h2"], "rules_out": []},
                     {"label": "Valid at write", "means": "below the callback", "keeps": ["h2"], "rules_out": []}]}


def _round(parts, hyps=HYPS):
    return {"content": [{"type": "tool_use", "id": "s1", "name": "submit_round", "input": {"reply": "The port did not stop the session.", "parts": parts, "hypotheses": hyps, "next_check": NEXT}}]}


def test_packets_are_decoded_from_the_agents_side():
    raw = (public_samples.SAMPLES[NAME]["dir"] / "log.txt").read_text().splitlines()
    p = disc.decode_packets(raw)["packets"]
    assert len(p) == 13 and p[2]["line"] == 10 and p[2]["direction"] == "received" and p[2]["all_zero"] and p[2]["length"] == 36
    assert p[0]["submessage"]["name"] == "CREATE_CLIENT" and p[4]["submessage"]["fits"] is False


def test_a_guess_is_split_into_parts_tested_on_the_data(case):
    store, cid = case
    good = [{"claim": "The agent replied OK to the create", "verdict": "holds", "why": "STATUS_AGENT result OK after the create", "kind": "observed",
             "evidence": [{"line": 10, "quote": ZERO_LINE}], "calls": ["c1"]},
            {"claim": "The port broke the session", "verdict": "fails", "why": "A session was established on the wrapped port", "kind": "inferred", "calls": ["c2"]}]
    m = _model([_tool("c1", "packets"), _tool("c2", "read_lines", {"start": 1, "end": 8}), _round(good)])
    out = disc.discuss(store, cid, "I think the port broke it and the board retries forever", model=m)
    assert out["failure"] is None and [p["verdict"] for p in out["result"]["parts"]] == ["holds", "fails"]
    assert [s["tool"] for s in out["steps"]] == ["packets", "read_lines"]
    assert disc.rounds_of(store, cid)[0]["message"].startswith("I think the port")
    assert {h["id"] for h in disc.current_hypotheses(store, cid)} == {"h1", "h2"}


def test_claim_scope_and_quotes_are_enforced_then_fixed(case):
    store, cid = case
    overreach = [{"claim": "The board sent 36 zero bytes", "verdict": "holds", "why": "zeros on the wire", "kind": "observed", "calls": ["c1"]}]
    badquote = [{"claim": "Zero packet received", "verdict": "holds", "why": "seen", "kind": "observed", "evidence": [{"line": 10, "quote": "not in the log"}], "calls": ["c1"]}]
    fixed = [{"claim": "The agent received 36 zero bytes", "verdict": "holds", "why": "all bytes zero in the agent's log", "kind": "observed",
              "evidence": [{"line": 10, "quote": ZERO_LINE}], "calls": ["c1"]}]
    m = _model([_tool("c1", "packets"), _round(overreach), _round(badquote), _round(fixed)])
    out = disc.discuss(store, cid, "Did the board really send zeros?", model=m)
    sent_back = [c["messages"][-1]["content"][0]["content"] for c in m.calls[2:]]
    assert "claim scope" in sent_back[0] and "quote is not in line 10" in sent_back[1]
    assert out["result"]["parts"][0]["claim"] == "The agent received 36 zero bytes"


def test_an_engineers_report_cannot_rule_an_area_out(case):
    store, cid = case
    hyps = HYPS + [{"id": "h3", "area": "Network path", "claim": "Switch corrupts", "status": "ruled_out", "because": "engineer says so", "basis": "direct", "excluded_by": "engineer_report"}]
    part = [{"claim": "Capture at the PC shows zeros", "verdict": "holds", "why": "reported by the engineer", "kind": "reported", "calls": ["c1"]}]
    m = _model([_tool("c1", "packets"), _round(part, hyps), _round(part, hyps), _round(part, hyps)])
    out = disc.discuss(store, cid, "tcpdump on the PC shows zeros", kind="result", model=m)
    assert next(h for h in out["result"]["hypotheses"] if h["id"] == "h3")["status"] == "weakened"
    assert [e for e in store.ledger(cid) if e["kind"] == "engineer_finding"][-1]["payload"]["observations"] == "tcpdump on the PC shows zeros"


def test_no_model_or_no_permission_no_round(tmp_path):
    s = CaseStore(tmp_path / "n.db")
    cid = public_samples.run_sample(s, NAME)["case_id"]
    with pytest.raises(ValueError):
        disc.discuss(s, cid, "guess", model=_model([]))  # no model_authorization recorded
    s.close()
