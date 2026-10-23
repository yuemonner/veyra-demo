# Connectors, automatic triggers and the investigation agent

How a field case starts on its own: a signed event arrives, Veyra opens a case, pulls evidence through
read-only connectors, runs the deterministic engine, then the AI agent investigates with read-only tools
and writes a cited report. People decide what to check and do.

```
alert / ticket / takeover ──signed webhook──▶ case + trigger record (+ intervention report)
                                              │
               connectors (read-only) ◀───────┘  fleet REST API · export folder · S3 · ROS bag / MCAP
                       │ files, unchanged or converted, through the normal import checks
                       ▼
              investigation run (deterministic, no model)
                       ▼
              AI agent ── read-only tools ──▶ agent_report (every claim cites tool calls)
```

## Configure

Put a JSON file at `.local-runtime/connectors.json` (or point `VEYRA_CONNECTORS` at one). Secrets never go in
the file: name environment variables with `*_env` keys.

```json
{
  "connectors": [
    {"id": "fleet-api", "kind": "rest_api", "machines": ["EX1"], "base_url": "https://fleet.example.com",
     "allowed_hosts": ["fleet.example.com"], "auth": {"header": "Authorization", "prefix": "Bearer ", "token_env": "FLEET_API_TOKEN"},
     "streams": [{"name": "console", "path": "/v1/machines/{machine}/console?from={start}&to={end}", "records_key": "records",
                  "profile": {"profile_id": "fleet-console", "version": 1, "format": "json", "...": "an import profile"}}]},
    {"id": "exports", "kind": "folder", "path": "/srv/exports", "patterns": [{"glob": "{machine}_*_console.csv", "profile": {"...": "..."}}]},
    {"id": "s3", "kind": "s3", "bucket": "fleet-logs", "region": "eu-central-1", "prefix": "exports/{machine}/",
     "access_key_env": "S3_ACCESS_KEY", "secret_key_env": "S3_SECRET_KEY", "patterns": [{"glob": "*.jsonl", "profile": {"...": "..."}}]},
    {"id": "robot-logs", "kind": "robot_logs", "path": "/srv/bags", "globs": ["*.bag", "*.mcap"], "clock_domain": "robot_clock",
     "time_source": "header", "signals": [{"topic": "/joint_states", "field": "velocity[boom_joint]", "signal": "vehicle.actuator_response", "unit": "rad/s", "scope": "boom"}]}
  ],
  "webhooks": {"secret_env": "VEYRA_WEBHOOK_SECRET", "sources": {
    "fleet-alerts": {"fields": {"event_id": "id", "machine_id": "asset.id", "occurred_at": "time", "type": "kind", "title": "summary",
                                "operator": "operator.name", "stated_reason": "operator.reason"},
                     "type_map": {"OPERATOR_TAKEOVER": "takeover", "ALARM": "alert"}}}},
  "automation": {"auto_agent": true, "acquire_before_s": 600, "acquire_after_s": 300}
}
```

Webhook: `POST /engine/webhooks/{source}` with headers `X-Veyra-Timestamp` (Unix seconds) and
`X-Veyra-Signature: sha256=HMAC_SHA256(secret, timestamp + "." + body)`. Events older than 5 minutes are refused;
the same event id never opens a second case.

Agent: set `ANTHROPIC_API_KEY` (and optionally `VEYRA_AGENT_MODEL`) for the API. Locally, put them in
`.local-runtime/agent.env`; `restart-veyra-api.command` loads it. `POST /engine/cases/{id}/agent` runs it on demand.

Local demo on a Claude subscription: set `VEYRA_AGENT_BACKEND=claude-code` instead of a key. The agent's model step then
runs through the locally installed Claude Code CLI (`claude -p`, its own tools disabled, the API key removed from its
environment so the subscription sign-in is used; `VEYRA_CLAUDE_BIN` overrides the path). The agent's tools, citation
checks and egress guard are unchanged. This is for a person trying the demo on their own computer; deployments
use the API.

## Boundaries enforced in code

- Connectors: GET only, allowlisted hosts, HTTPS except localhost, size and time limits, no secrets in config.
- Every fetched file is committed through the standard import path (hashes, validation, rejected-row reasons,
  synthetic/real separation). Converted robot logs keep the original file's SHA-256 in the import notes.
- `trigger_event`, `acquisition` and `agent_report` records can only be written by Veyra itself.
- The agent's tools are read-only. Non-synthetic case data goes to a model provider only after a person records a
  `model_authorization` for that provider.
- Every observation, hypothesis and proposed check in an agent report must cite tool calls from that run; reports
  claiming a confirmed root cause are sent back. Hypotheses are not causes; people decide.

## Supported robot-log content

ROS 1 bag v2.0 (uncompressed or bz2 chunks; any message type whose definition is embedded) and MCAP (uncompressed,
`json` or `ros1` encoded). zstd/lz4 compression and ROS 2 CDR are reported as unsupported; decompress or export first.

## Try it (synthetic)

On the field-case page, **Simulate live takeover** posts a signed synthetic event from a stand-in fleet platform:
the mock fleet API (`/mock-fleet`), an export folder and an MCAP recording supply the evidence. None of it is
private data.
