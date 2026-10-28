"""Veyra's evidence engine as an MCP server (Model Context Protocol, stdio transport), for any agent.

Claude Code, Claude Desktop, the Claude Agent SDK, or a LangGraph / other agent can investigate a Veyra case with the
same read-only tools Veyra's own agent uses: case overview, evidence discovery of the original recording, text-log
lines, the signal chain, earlier lessons, signals, statistics, methods and sandboxed Python. Nothing here writes to a
case: conclusions stay with the engineer in Veyra (the ledger).

Data egress: the client agent may send tool results to its model provider. Real (non-synthetic) cases are served only
after a person recorded a `model_authorization` in that case's ledger, as for Veyra's own agent.

Run:  python -m app.mcp_server            (uses VEYRA_ENGINE_DB, default .local-runtime/engine.db)
Claude Code:  claude mcp add veyra -- python -m app.mcp_server   (from the backend folder)

Dependency-free JSON-RPC 2.0 over stdio (newline-delimited), protocol revision 2025-06-18; tools only.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

PROTOCOL = "2025-06-18"
SERVER = {"name": "veyra-evidence-engine", "version": "1.0.0"}
EXCLUDED = {"submit_report"}           # the agent's own finishing tool; not an evidence tool
CASE_PARAM = {"case_id": {"type": "string", "description": "Veyra case id (from list_cases)"}}


DEFAULT_DB = Path(__file__).resolve().parents[2] / ".local-runtime" / "engine.db"   # same default as app/engine_api.py


def _store():
    from app.store.sqlite_store import CaseStore
    return CaseStore(Path(os.getenv("VEYRA_ENGINE_DB") or DEFAULT_DB))


def tools() -> List[Dict[str, Any]]:
    from app.agent.tools import TOOL_SPECS
    out = [{"name": "list_cases", "description": "Cases in this Veyra workspace: id, machine, title, whether synthetic, whether closed, and whether its data may be shared with an agent.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}]
    for spec in TOOL_SPECS:
        if spec["name"] in EXCLUDED:
            continue
        schema = json.loads(json.dumps(spec["input_schema"]))
        props = {**CASE_PARAM, **(schema.get("properties") or {})}
        schema["properties"] = props
        schema["required"] = ["case_id"] + [r for r in schema.get("required", []) if r != "case_id"]
        out.append({"name": spec["name"], "description": spec["description"], "inputSchema": schema, "annotations": {"readOnlyHint": True}})
    return out


def _may_share(store, case_id: str) -> bool:
    case = store.case(case_id)
    return bool(case.get("synthetic")) or any(e["kind"] == "model_authorization" for e in store.ledger(case_id))


def call(name: str, args: Dict[str, Any]) -> Any:
    store = _store()
    try:
        if name == "list_cases":
            return [{"case_id": c["id"], "machine_id": c["machine_id"], "title": c["title"], "synthetic": bool(c.get("synthetic")),
                     "closed": any(e["kind"] == "case_closure" for e in store.ledger(c["id"])), "shareable": _may_share(store, c["id"])}
                    for c in store.cases() if c.get("fault_type") != "known_good_run"]
        case_id = args.pop("case_id", None)
        if not case_id:
            raise ValueError("case_id is required")
        if not _may_share(store, case_id):
            raise PermissionError("This is a real case and no one has recorded a model_authorization for it in Veyra; its data is not sent to agents.")
        from app.agent.tools import Toolbox
        box = Toolbox(store, case_id, may_share=lambda cid: _may_share(store, cid))
        return box.call(name, args)
    finally:
        store.close()


def handle(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:  # a notification (e.g. notifications/initialized): no reply
        return None
    try:
        if method == "initialize":
            result: Any = {"protocolVersion": (msg.get("params") or {}).get("protocolVersion") or PROTOCOL,
                           "capabilities": {"tools": {"listChanged": False}}, "serverInfo": SERVER,
                           "instructions": "Read-only tools over Veyra cases. Start with list_cases, then case_overview, recording_discovery, signal_chain and lessons. Conclusions are recorded by engineers in Veyra, not here."}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": tools()}
        elif method == "tools/call":
            params = msg.get("params") or {}
            try:
                value = call(params.get("name"), dict(params.get("arguments") or {}))
                result = {"content": [{"type": "text", "text": json.dumps(value, default=str)[:60_000]}], "isError": False}
            except Exception as error:  # tool errors go back to the agent as results, not protocol errors
                result = {"content": [{"type": "text", "text": f"{type(error).__name__}: {error}"}], "isError": True}
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}
    except Exception as error:  # noqa: BLE001
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(error)}}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            reply = handle(msg)
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
