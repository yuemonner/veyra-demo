"""Read-only connectors: fetch evidence from a the operator's systems into the validated import path.

A connector never writes to the store and never talks to a machine. It returns `Batch`es: original
bytes plus the explicit import profile that describes them. The pipeline then commits each batch
through `CaseStore.commit_import`, so hashing, validation, per-row rejection reasons and the
synthetic/real separation apply exactly as for a file a person uploads.

Network access is GET-only, limited to hosts the configuration allowlists, with a size cap and a
timeout. Secrets are read from environment variables named in the configuration, never stored.
"""
from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

CONNECTOR_SCHEMA_VERSION = "veyra-connectors-v1"
MAX_RESPONSE_BYTES = 25_000_000
DEFAULT_TIMEOUT_S = 20.0


class ConnectorError(ValueError):
    pass


@dataclass
class Batch:
    """One unit of evidence to import: original bytes and the profile that describes them."""
    filename: str
    content: bytes
    profile: Dict[str, Any]
    source: str                      # where it came from (URL, path, object key) — never a secret
    notes: List[str] = field(default_factory=list)


@dataclass
class Window:
    start: datetime
    end: datetime

    def iso(self) -> Tuple[str, str]:
        return (self.start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                self.end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"))

    def epoch(self) -> Tuple[float, float]:
        return self.start.timestamp(), self.end.timestamp()


def env_secret(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    value = os.environ.get(name)
    if not value:
        raise ConnectorError(f"Environment variable {name} is not set (secrets are read from the environment, never from configuration)")
    return value


def http_get(url: str, *, allowed_hosts: Iterable[str], headers: Optional[Dict[str, str]] = None,
             timeout: float = DEFAULT_TIMEOUT_S, max_bytes: int = MAX_RESPONSE_BYTES) -> bytes:
    """GET only. The host must be allowlisted. Redirects to other hosts are refused."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("https", "http"):
        raise ConnectorError(f"Unsupported URL scheme '{parsed.scheme}'")
    allowed = {h.lower() for h in allowed_hosts}
    host = (parsed.hostname or "").lower()
    if host not in allowed:
        raise ConnectorError(f"Host '{host}' is not in this connector's allowed_hosts")
    if parsed.scheme == "http" and host not in ("127.0.0.1", "localhost", "::1"):
        raise ConnectorError("Plain HTTP is only allowed for local test servers; use HTTPS")

    class _NoCrossHostRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            target = (urllib.parse.urlsplit(newurl).hostname or "").lower()
            if target not in allowed:
                raise ConnectorError(f"Redirect to non-allowlisted host '{target}' refused")
            return super().redirect_request(req, fp, code, msg, hdrs, newurl)

    opener = urllib.request.build_opener(_NoCrossHostRedirect, urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    request = urllib.request.Request(url, method="GET", headers={"Accept": "application/json", "User-Agent": "veyra-connector/1", **(headers or {})})
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(max_bytes + 1)
    except urllib.error.HTTPError as error:
        raise ConnectorError(f"GET {parsed.path} returned HTTP {error.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ConnectorError(f"GET {parsed.path} failed: {getattr(error, 'reason', error)}") from None
    if len(body) > max_bytes:
        raise ConnectorError(f"Response larger than {max_bytes} bytes; narrow the time window")
    return body


class Connector:
    kind = "base"

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.id = config["id"]
        self.machines = list(config.get("machines") or [])
        self.synthetic = bool(config.get("synthetic", False))

    def serves(self, machine_id: str) -> bool:
        return not self.machines or machine_id in self.machines

    def describe(self) -> Dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "machines": self.machines, "synthetic": self.synthetic, "read_only": True}

    def fetch(self, machine_id: str, window: Window) -> List[Batch]:  # pragma: no cover - interface
        raise NotImplementedError

    def _profile(self, profile: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(profile, dict):
            raise ConnectorError(f"{self.id}: profile must be an object")
        if bool(profile.get("synthetic", False)) != self.synthetic:
            raise ConnectorError(f"{self.id}: profile 'synthetic' must match the connector's 'synthetic' flag")
        return dict(profile)


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Configuration file: VEYRA_CONNECTORS or .local-runtime/connectors.json. Missing file = no connectors."""
    target = Path(path or os.environ.get("VEYRA_CONNECTORS") or Path(__file__).resolve().parents[3] / ".local-runtime" / "connectors.json")
    if not target.exists():
        return {"schema": CONNECTOR_SCHEMA_VERSION, "connectors": [], "webhooks": {}, "path": str(target), "loaded": False}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ConnectorError(f"{target.name} is not valid JSON: {error}") from None
    validate_config(raw)
    return {**raw, "path": str(target), "loaded": True}


def validate_config(raw: Dict[str, Any]) -> None:
    if not isinstance(raw, dict) or not isinstance(raw.get("connectors", []), list):
        raise ConnectorError("Configuration must be an object with a 'connectors' list")
    seen = set()
    for item in raw.get("connectors", []):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip():
            raise ConnectorError("Every connector needs an 'id'")
        if item["id"] in seen:
            raise ConnectorError(f"Duplicate connector id '{item['id']}'")
        seen.add(item["id"])
        if item.get("kind") not in KINDS:
            raise ConnectorError(f"Connector '{item['id']}': kind must be one of {', '.join(KINDS)}")
        for key, value in item.items():
            if isinstance(value, str) and any(word in key.lower() for word in ("secret", "token", "password", "key")) and not key.endswith("_env"):
                raise ConnectorError(f"Connector '{item['id']}': '{key}' looks like a secret. Put secrets in environment variables and name them with '{key}_env'")


KINDS = ("rest_api", "folder", "s3", "robot_logs")


def build(config: Dict[str, Any]) -> List[Connector]:
    from app.connectors.files import FolderConnector, S3Connector
    from app.connectors.rest_api import RestApiConnector
    from app.connectors.robotlogs import RobotLogConnector
    classes = {"rest_api": RestApiConnector, "folder": FolderConnector, "s3": S3Connector, "robot_logs": RobotLogConnector}
    return [classes[item["kind"]](item) for item in config.get("connectors", [])]
