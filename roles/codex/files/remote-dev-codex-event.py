#!/usr/bin/env python3
"""Send one bounded event to the local remote-dev orchestrator forwarding."""
from __future__ import annotations

import json
import socket
import sys

if len(sys.argv) != 5:
    raise SystemExit(2)
event, target, nonce, value = sys.argv[1:]
payload = {"v": 1, "event": event, "target": target, "nonce": nonce}
if event == "CODEX_START":
    payload["pid"] = int(value)
    payload["args_mode"] = "login_or_normal"
elif event == "CODEX_OAUTH_URL":
    if not (value.startswith("https://auth.openai.com/") or value.startswith("http://localhost:1455/")):
        raise SystemExit(0)
    payload["oauth_url"] = value
else:
    payload["pid"] = int(value.split(":", 1)[0])
    payload["exit_code"] = int(value.split(":", 1)[1])
raw = (json.dumps(payload, separators=(",", ":")) + "\n").encode()
try:
    with socket.create_connection(("127.0.0.1", 4228), timeout=0.15) as conn:
        conn.sendall(raw)
        conn.recv(1024)
except (OSError, ValueError):
    pass
