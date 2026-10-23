"""Model clients for the investigation agent (standard library HTTP; no SDK needed).

AnthropicModel calls the Claude Messages API with tool use. The key is read from the environment
(ANTHROPIC_API_KEY by default) at call time and never stored. ScriptedModel replays fixed responses
for tests; it is never used to produce a report shown as AI output.
"""
from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

DEFAULT_MODEL = "claude-sonnet-5-5"
API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


class ModelError(RuntimeError):
    pass


class _BadJSON(ModelError):
    pass


class AnthropicModel:
    provider = "anthropic"

    def __init__(self, model: Optional[str] = None, *, api_key_env: str = "ANTHROPIC_API_KEY", max_tokens: int = 2500, timeout_s: float = 120.0):
        self.model = model or os.environ.get("VEYRA_AGENT_MODEL") or DEFAULT_MODEL
        self.api_key_env = api_key_env
        self.max_tokens = max_tokens
        self.timeout_s = timeout_s

    def available(self) -> bool:
        return bool(os.environ.get(self.api_key_env))

    def create(self, *, system: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        key = os.environ.get(self.api_key_env)
        if not key:
            raise ModelError(f"Set {self.api_key_env} in the API's environment to run the agent")
        body = json.dumps({"model": self.model, "max_tokens": self.max_tokens, "system": system, "messages": messages, "tools": tools}).encode()
        request = urllib.request.Request(API_URL, data=body, method="POST",
                                         headers={"content-type": "application/json", "x-api-key": key, "anthropic-version": API_VERSION})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s, context=ssl.create_default_context()) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            detail = error.read()[:400].decode("utf-8", errors="replace")
            raise ModelError(f"Model API returned HTTP {error.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise ModelError(f"Model API unreachable: {getattr(error, 'reason', error)}") from None


CLAUDE_CODE_PROTOCOL = """You are the reasoning step of a tool-using agent. You cannot call tools yourself in this
environment; instead, reply with ONE JSON object and nothing else:
  {"note": "<one short sentence on what you are doing>", "tool_calls": [{"name": "<tool name>", "input": {...}}, ...]}
Use only the tools listed below, with inputs that match their schemas. Request one or more tool calls per reply.
The results come back in the next message, each labelled with its tool call id (for example cc_3).
When you finish, request exactly one call to submit_report and cite tool call ids exactly as labelled."""


class ClaudeCodeModel:
    """Runs the agent's model step through a locally installed Claude Code CLI (`claude -p`).

    For a local demo on a person's own computer: usage counts against that person's Claude
    subscription instead of an API key. Each step is stateless; the full transcript is sent every
    turn. Claude Code's own tools are disabled; the agent's read-only tools run here, in Veyra.
    Not for production: production use goes through the API (AnthropicModel).
    """
    provider = "anthropic"
    _FALLBACKS = ("~/.local/bin/claude", "~/.claude/local/claude", "/opt/homebrew/bin/claude", "/usr/local/bin/claude")

    def __init__(self, model: Optional[str] = None, *, timeout_s: float = 300.0):
        self.cli_model = model or os.environ.get("VEYRA_AGENT_MODEL") or "sonnet"
        self.model = f"claude-code:{self.cli_model}"
        self.timeout_s = timeout_s
        self._ids = 0

    def binary(self) -> Optional[str]:
        import shutil
        explicit = os.environ.get("VEYRA_CLAUDE_BIN")
        for candidate in ([explicit] if explicit else []) + [shutil.which("claude") or ""] + [os.path.expanduser(p) for p in self._FALLBACKS]:
            if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        return None

    def available(self) -> bool:
        return self.binary() is not None

    @staticmethod
    def _render(messages: List[Dict[str, Any]]) -> str:
        parts = []
        for message in messages:
            content = message["content"]
            if isinstance(content, str):
                parts.append(f"[{message['role']}]\n{content}")
                continue
            for block in content:
                kind = block.get("type")
                if kind == "text" and block.get("text"):
                    parts.append(f"[{message['role']}]\n{block['text']}")
                elif kind == "tool_use":
                    parts.append(f"[assistant requested {block['id']}] {block['name']} {json.dumps(block.get('input') or {})}")
                elif kind == "tool_result":
                    flag = " (error)" if block.get("is_error") else ""
                    parts.append(f"[result of {block['tool_use_id']}{flag}]\n{block.get('content')}")
        return "\n\n".join(parts)

    @staticmethod
    def _extract_json(text: str) -> Dict[str, Any]:
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
            text = text.rsplit("```", 1)[0]
        start = text.find("{")
        if start < 0:
            raise ModelError("Claude Code reply contained no JSON object")
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
        if not isinstance(obj, dict):
            raise ModelError("Claude Code reply was not a JSON object")
        return obj

    def create(self, *, system: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        binary = self.binary()
        if not binary:
            raise ModelError("Claude Code is not installed on this computer (looked for the `claude` command)")
        tool_text = "\n".join(f"- {t['name']}: {t.get('description', '')}\n  input schema: {json.dumps(t.get('input_schema', {}))}" for t in tools)
        system_prompt = f"{system}\n\n{CLAUDE_CODE_PROTOCOL}\n\nTools:\n{tool_text}"
        prompt = self._render(messages) + "\n\nReply now with the JSON object only."
        try:
            return self._step(binary, system_prompt, prompt)
        except _BadJSON as error:  # one retry: a malformed reply is a protocol slip, not an answer
            return self._step(binary, system_prompt, prompt + f"\n\nYour previous reply could not be read ({error}). Reply again with exactly one valid JSON object, double-quoted keys, nothing else.")

    def _step(self, binary: str, system_prompt: str, prompt: str) -> Dict[str, Any]:
        import subprocess
        import tempfile
        env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}  # use the subscription sign-in
        command = [binary, "-p", "--output-format", "json", "--model", self.cli_model, "--system-prompt", system_prompt,
                   "--max-turns", "1", "--disallowedTools", "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,NotebookEdit,Task,TodoWrite"]
        try:
            with tempfile.TemporaryDirectory(prefix="veyra-agent-") as cwd:
                done = subprocess.run(command, input=prompt, capture_output=True, text=True, timeout=self.timeout_s, cwd=cwd, env=env)
        except subprocess.TimeoutExpired:
            raise ModelError(f"Claude Code did not answer within {self.timeout_s:.0f} s") from None
        except OSError as error:
            raise ModelError(f"Could not start Claude Code: {error}") from None
        try:
            envelope = json.loads(done.stdout)
        except ValueError:
            detail = (done.stderr or done.stdout or "").strip()[:400]
            raise ModelError(f"Claude Code failed (exit {done.returncode}): {detail or 'no output'}. Is it signed in? Run `claude` once in Terminal.") from None
        if envelope.get("is_error"):
            raise ModelError(f"Claude Code returned an error: {str(envelope.get('result'))[:400]}")
        try:
            reply = self._extract_json(str(envelope.get("result") or ""))
        except ValueError as error:
            raise _BadJSON(f"Could not read Claude Code's reply as JSON: {error}") from None
        content: List[Dict[str, Any]] = []
        if reply.get("note"):
            content.append({"type": "text", "text": str(reply["note"])})
        for call in reply.get("tool_calls") or []:
            if isinstance(call, dict) and call.get("name"):
                self._ids += 1
                content.append({"type": "tool_use", "id": f"cc_{self._ids}", "name": str(call["name"]), "input": call.get("input") or {}})
        usage = envelope.get("usage") or {}
        return {"content": content, "usage": {"input_tokens": int(usage.get("input_tokens") or 0) + int(usage.get("cache_read_input_tokens") or 0),
                                              "output_tokens": int(usage.get("output_tokens") or 0)}}


def from_environment():
    """VEYRA_AGENT_BACKEND=claude-code uses the local Claude Code CLI; anything else uses the API."""
    if os.environ.get("VEYRA_AGENT_BACKEND", "").strip().lower() in ("claude-code", "claude_code", "subscription"):
        return ClaudeCodeModel()
    return AnthropicModel()


class ScriptedModel:
    """Deterministic stand-in for tests: returns the given responses in order."""
    provider = "scripted-test"

    def __init__(self, responses: List[Dict[str, Any]], model: str = "scripted-test"):
        self.responses = list(responses)
        self.model = model
        self.calls: List[Dict[str, Any]] = []

    def available(self) -> bool:
        return True

    def create(self, *, system: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        self.calls.append({"messages": json.loads(json.dumps(messages)), "tools": [t["name"] for t in tools]})
        if not self.responses:
            raise ModelError("Scripted model has no more responses")
        return self.responses.pop(0)
