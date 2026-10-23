"""Export-folder and S3 connectors: pick up log exports a site already produces.

Folder:  {"kind": "folder", "path": "/exports", "patterns": [{"glob": "{machine}_*_console.csv", "profile": {...}}]}
S3:      {"kind": "s3", "bucket": "...", "region": "eu-central-1", "prefix": "exports/{machine}/",
          "access_key_env": "...", "secret_key_env": "...", "session_token_env": "...?", "endpoint": "https://...?",
          "allowed_hosts": [...?], "patterns": [{"glob": "*_console.csv", "profile": {...}}]}

Files are imported whole and unchanged; the store rejects exact duplicates, so re-running is safe.
S3 requests are signed with AWS Signature Version 4 (standard library only) and are GET-only.
"""
from __future__ import annotations

import datetime as _dt
import fnmatch
import hashlib
import hmac
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.connectors.base import Batch, Connector, ConnectorError, Window, env_secret, http_get

MAX_FILES = 50
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def _patterns(connector: Connector) -> List[Tuple[str, Dict[str, Any]]]:
    patterns = connector.config.get("patterns")
    if not isinstance(patterns, list) or not patterns:
        raise ConnectorError(f"{connector.id}: 'patterns' must list at least one {{glob, profile}}")
    out = []
    for item in patterns:
        if not isinstance(item, dict) or not isinstance(item.get("glob"), str):
            raise ConnectorError(f"{connector.id}: each pattern needs a 'glob'")
        out.append((item["glob"], connector._profile(item.get("profile"))))
    return out


def _match(name: str, patterns: List[Tuple[str, Dict[str, Any]]], machine_id: str) -> Optional[Dict[str, Any]]:
    for glob, profile in patterns:
        if fnmatch.fnmatchcase(name, glob.replace("{machine}", machine_id)):
            return profile
    return None


class FolderConnector(Connector):
    kind = "folder"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        if not config.get("path"):
            raise ConnectorError(f"{self.id}: 'path' is required")
        self.root = Path(config["path"]).expanduser().resolve()
        self.patterns = _patterns(self)

    def describe(self) -> Dict[str, Any]:
        return {**super().describe(), "path": str(self.root), "patterns": [g for g, _ in self.patterns]}

    def fetch(self, machine_id: str, window: Window) -> List[Batch]:
        if not self.root.is_dir():
            raise ConnectorError(f"{self.id}: folder {self.root} does not exist")
        batches = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            if self.root not in path.resolve().parents:
                continue  # never follow anything out of the configured folder
            profile = _match(path.name, self.patterns, machine_id)
            if profile is None:
                continue
            if len(batches) >= int(self.config.get("max_files", MAX_FILES)):
                break
            batches.append(Batch(filename=path.name, content=path.read_bytes(), profile=profile,
                                 source=f"folder:{path.relative_to(self.root)}", notes=[f"Read from export folder ({path.stat().st_size} bytes)"]))
        return batches


# ---------- S3 (AWS Signature Version 4, GET only) ----------
def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _uri_encode(value: str, safe: str = "-_.~") -> str:
    return urllib.parse.quote(value, safe=safe)


def sigv4_headers(method: str, url: str, *, access_key: str, secret_key: str, region: str, now: _dt.datetime,
                  extra_headers: Optional[Dict[str, str]] = None, session_token: Optional[str] = None,
                  payload_sha256: str = EMPTY_SHA256, service: str = "s3") -> Dict[str, str]:
    parsed = urllib.parse.urlsplit(url)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date = now.strftime("%Y%m%d")
    headers = {"host": parsed.netloc, "x-amz-content-sha256": payload_sha256, "x-amz-date": amz_date}
    if session_token:
        headers["x-amz-security-token"] = session_token
    for key, value in (extra_headers or {}).items():
        headers[key.lower()] = value.strip()
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    canonical_query = "&".join(f"{_uri_encode(k)}={_uri_encode(v)}" for k, v in sorted(query))
    canonical_uri = _uri_encode(urllib.parse.unquote(parsed.path or "/"), safe="/-_.~")
    signed = sorted(headers)
    canonical_headers = "".join(f"{name}:{headers[name]}\n" for name in signed)
    canonical_request = "\n".join([method, canonical_uri, canonical_query, canonical_headers, ";".join(signed), payload_sha256])
    scope = f"{date}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest()])
    key = _hmac(_hmac(_hmac(_hmac(("AWS4" + secret_key).encode(), date), region), service), "aws4_request")
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    out = {k: v for k, v in headers.items() if k != "host"}
    out["Authorization"] = f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={';'.join(signed)}, Signature={signature}"
    return out


class S3Connector(Connector):
    kind = "s3"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        for key in ("bucket", "region", "access_key_env", "secret_key_env"):
            if not config.get(key):
                raise ConnectorError(f"{self.id}: '{key}' is required")
        self.patterns = _patterns(self)
        self.endpoint = (config.get("endpoint") or f"https://{config['bucket']}.s3.{config['region']}.amazonaws.com").rstrip("/")
        self.allowed_hosts = config.get("allowed_hosts") or [urllib.parse.urlsplit(self.endpoint).hostname]

    def describe(self) -> Dict[str, Any]:
        return {**super().describe(), "bucket": self.config["bucket"], "prefix": self.config.get("prefix", ""), "patterns": [g for g, _ in self.patterns]}

    def _get(self, path_and_query: str) -> bytes:
        url = self.endpoint + path_and_query
        headers = sigv4_headers("GET", url, access_key=env_secret(self.config["access_key_env"]), secret_key=env_secret(self.config["secret_key_env"]),
                                region=self.config["region"], now=_dt.datetime.now(_dt.timezone.utc),
                                session_token=env_secret(self.config["session_token_env"]) if self.config.get("session_token_env") else None)
        return http_get(url, allowed_hosts=self.allowed_hosts, headers={**headers, "Accept": "*/*"})

    def _list(self, prefix: str) -> List[Dict[str, str]]:
        objects, token = [], None
        path_style = bool(self.config.get("endpoint"))  # custom endpoints (MinIO etc.) use path-style addressing
        base = f"/{self.config['bucket']}" if path_style else ""
        while True:
            query = {"list-type": "2", "prefix": prefix}
            if token:
                query["continuation-token"] = token
            body = self._get(f"{base}/?{urllib.parse.urlencode(sorted(query.items()), quote_via=urllib.parse.quote)}")
            root = ET.fromstring(body)
            ns = {"s3": root.tag.split("}")[0].strip("{")} if root.tag.startswith("{") else {}
            find = (lambda el, tag: el.find(f"s3:{tag}", ns)) if ns else (lambda el, tag: el.find(tag))
            for item in (root.findall("s3:Contents", ns) if ns else root.findall("Contents")):
                objects.append({"key": find(item, "Key").text, "size": find(item, "Size").text})
            truncated = find(root, "IsTruncated")
            next_token = find(root, "NextContinuationToken")
            if truncated is None or truncated.text != "true" or next_token is None or len(objects) >= 1000:
                return objects
            token = next_token.text

    def fetch(self, machine_id: str, window: Window) -> List[Batch]:
        prefix = self.config.get("prefix", "").replace("{machine}", machine_id)
        path_style = bool(self.config.get("endpoint"))
        batches = []
        for obj in self._list(prefix):
            name = obj["key"].rsplit("/", 1)[-1]
            profile = _match(name, self.patterns, machine_id)
            if profile is None:
                continue
            if len(batches) >= int(self.config.get("max_files", MAX_FILES)):
                break
            key_path = "/" + _uri_encode(obj["key"], safe="/-_.~")
            content = self._get((f"/{self.config['bucket']}" if path_style else "") + key_path)
            batches.append(Batch(filename=name, content=content, profile=profile, source=f"s3://{self.config['bucket']}/{obj['key']}",
                                 notes=[f"Read from S3 ({len(content)} bytes)"]))
        return batches
