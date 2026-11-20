"""OpenAI wire protocol and authorization tests. Mock transport; not real Agent validation."""
import io
import json

import pytest

from app.agent import llm
from app.agent.runner import AgentError, run_agent
from app.store.sqlite_store import CaseStore


def test_responses_tool_results_and_reasoning_are_replayed(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.setenv("VEYRA_OPENAI_MODEL", "test-model")
    monkeypatch.setenv("VEYRA_AGENT_BACKEND", "openai")
    client = llm.from_environment()
    assert isinstance(client, llm.OpenAIModel) and client.available()
    sent = []
    reasoning = {"id": "r1", "type": "reasoning", "encrypted_content": "opaque", "summary": []}
    call = {"type": "function_call", "call_id": "call_1", "name": "case_overview", "arguments": "{}"}
    def request(req, **kwargs):
        sent.append(json.loads(req.data))
        assert req.full_url == "https://api.openai.com/v1/responses"
        assert req.get_header("Authorization") == "Bearer test-secret"
        return io.BytesIO(json.dumps({"status": "completed", "output": [reasoning, call], "usage": {"input_tokens": 10, "output_tokens": 2}}).encode())
    monkeypatch.setattr(llm.urllib.request, "urlopen", request)
    specs = [{"name": "case_overview", "description": "Read", "input_schema": {"type": "object", "properties": {}}}]
    first = client.create(system="Read only", messages=[{"role": "user", "content": "Investigate"}], tools=specs)
    assert first["content"][0]["id"] == "call_1"
    client.create(system="Read only", messages=[{"role": "user", "content": "Investigate"},
        {"role": "assistant", "content": first["content"]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "public evidence"}]}], tools=specs)
    assert sent[1]["input"][1:3] == [reasoning, call]
    assert sent[1]["input"][-1] == {"type": "function_call_output", "call_id": "call_1", "output": "public evidence"}
    assert sent[0]["store"] is False and sent[0]["tools"][0]["strict"] is False
    assert "test-secret" not in json.dumps(sent)


def test_openai_never_falls_back_to_fake_model_or_uses_an_unspecified_model(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("VEYRA_OPENAI_MODEL", raising=False)
    model = llm.OpenAIModel()
    assert not model.available()
    with pytest.raises(llm.ModelError, match="Configure"):
        model.create(system="", messages=[], tools=[])


def test_openai_real_data_requires_its_own_provider_authorization(tmp_path):
    store = CaseStore(tmp_path / "authorize.db")
    try:
        cid = store.create_case(machine_id="M", title="Public recording", actor="Test", synthetic=False)["id"]
        with pytest.raises(AgentError, match="model_authorization"):
            run_agent(store, cid, model=llm.OpenAIModel("test-model"))
        store.append(cid, "model_authorization", {"provider": "openai", "model": "test-model", "scope": "One public acceptance run",
                     "authorized_by": "Test", "evidence": "Synthetic authorization test"}, actor="Test")
        assert store.ledger(cid)[-1]["payload"]["provider"] == "openai"
    finally:
        store.close()


@pytest.mark.parametrize("reply", [{"status": "incomplete", "output": []},
    {"status": "completed", "output": [{"type": "function_call", "call_id": "x", "name": "case_overview", "arguments": "invalid"}]}])
def test_openai_incomplete_or_invalid_calls_fail_closed(monkeypatch, reply):
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(json.dumps(reply).encode()))
    with pytest.raises(llm.ModelError):
        llm.OpenAIModel("test-model").create(system="", messages=[], tools=[])
