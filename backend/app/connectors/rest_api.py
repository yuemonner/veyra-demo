"""Generic read-only REST connector for fleet / telemetry APIs.

Configuration (per connector):
  base_url, allowed_hosts            where to read from; GET only
  auth: {header, prefix, token_env}  optional; the token comes from the environment
  streams: [{name, path, records_key?, profile}]
     path may use {machine}, {start}, {end} (ISO 8601, URL-encoded) and {start_ms}, {end_ms}
     records_key: where the record list sits in the response object (dotted), omitted = response is the list
     profile: the explicit import profile for one record (format "json")
"""
from __future__ import annotations

import hashlib
import json
import urllib.parse
from typing import Any, Dict, List

from app.connectors.base import Batch, Connector, ConnectorError, Window, env_secret, http_get


def _dig(data: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        if not isinstance(data, dict) or part not in data:
            raise ConnectorError(f"Response has no '{dotted}'")
        data = data[part]
    return data


class RestApiConnector(Connector):
    kind = "rest_api"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        for key in ("base_url", "allowed_hosts", "streams"):
            if not config.get(key):
                raise ConnectorError(f"{self.id}: '{key}' is required")
        if not isinstance(config["streams"], list):
            raise ConnectorError(f"{self.id}: 'streams' must be a list")
        for stream in config["streams"]:
            profile = self._profile(stream.get("profile"))
            if profile.get("format") != "json":
                raise ConnectorError(f"{self.id}/{stream.get('name')}: REST stream profiles use format 'json'")

    def describe(self) -> Dict[str, Any]:
        return {**super().describe(), "base_url": self.config["base_url"], "streams": [s["name"] for s in self.config["streams"]],
                "auth": bool(self.config.get("auth"))}

    def _headers(self) -> Dict[str, str]:
        auth = self.config.get("auth")
        if not auth:
            return {}
        token = env_secret(auth.get("token_env"))
        return {auth.get("header", "Authorization"): f"{auth.get('prefix', 'Bearer ')}{token}"}

    def fetch(self, machine_id: str, window: Window) -> List[Batch]:
        start, end = window.iso()
        start_ms, end_ms = (int(t * 1000) for t in window.epoch())
        batches = []
        for stream in self.config["streams"]:
            path = stream["path"].format(machine=urllib.parse.quote(machine_id, safe=""), start=urllib.parse.quote(start), end=urllib.parse.quote(end),
                                         start_ms=start_ms, end_ms=end_ms)
            url = self.config["base_url"].rstrip("/") + path
            raw = http_get(url, allowed_hosts=self.config["allowed_hosts"], headers=self._headers(), timeout=float(self.config.get("timeout_s", 20)))
            try:
                data = json.loads(raw)
            except ValueError:
                raise ConnectorError(f"{self.id}/{stream['name']}: response is not JSON") from None
            records = _dig(data, stream["records_key"]) if stream.get("records_key") else data
            if not isinstance(records, list):
                raise ConnectorError(f"{self.id}/{stream['name']}: expected a list of records")
            if not records:
                continue
            content = raw if not stream.get("records_key") else json.dumps(records, separators=(",", ":")).encode()
            notes = [f"GET {urllib.parse.urlsplit(url).path} returned {len(records)} record(s)"]
            if stream.get("records_key"):
                notes.append(f"Records extracted from '{stream['records_key']}'; full response sha256 {hashlib.sha256(raw).hexdigest()}")
            safe_start = start.replace(":", "").replace("-", "")
            batches.append(Batch(filename=f"{self.id}_{stream['name']}_{machine_id}_{safe_start}.json", content=content,
                                 profile=self._profile(stream["profile"]), source="{0.scheme}://{0.netloc}{0.path}".format(urllib.parse.urlsplit(url)), notes=notes))
        return batches
