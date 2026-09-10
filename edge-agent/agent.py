import json
import os
import socket
import time
from datetime import datetime, timezone
from urllib import request

API_URL = os.getenv("VEYRA_API_URL", "http://127.0.0.1:8050/events")
ASSET_ID = os.getenv("VEYRA_ASSET_ID", socket.gethostname())
AGENT_VERSION = "0.1.0"


def now():
    return datetime.now(timezone.utc).isoformat()


def heartbeat():
    return {
        "asset_id": ASSET_ID,
        "event_type": "heartbeat",
        "event_time": now(),
        "source": "edge_agent",
        "evidence_class": "OBSERVED",
        "payload": {
            "hostname": socket.gethostname(),
            "agent_version": AGENT_VERSION,
            "application_version": os.getenv("APP_VERSION", "unknown"),
            "config_hash": os.getenv("CONFIG_HASH", "unknown"),
            "process_health": "healthy",
            "uptime_seconds": int(time.monotonic()),
        },
        "confidence": 1.0,
        "provenance": {"collector": "veyra-edge-agent"},
    }


def send(event):
    body = json.dumps(event).encode()
    req = request.Request(API_URL, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with request.urlopen(req, timeout=10) as resp:
        return resp.read().decode()


if __name__ == "__main__":
    print(send(heartbeat()))
