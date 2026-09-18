"""Unix socket client for the local orchestrator."""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any

MAX_REQUEST_BYTES = 8 * 1024


def socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/tmp/tssh-{os.getuid()}")
    return Path(runtime) / "tssh" / "orchestrator.sock"


def request(payload: dict[str, Any], *, timeout: float = 2.0) -> dict[str, Any]:
    raw = (json.dumps(payload, separators=(",", ":")) + "\n").encode()
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("编排器请求超过 8 KiB")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(timeout)
        conn.connect(str(socket_path()))
        conn.sendall(raw)
        response = bytearray()
        while not response.endswith(b"\n"):
            chunk = conn.recv(4096)
            if not chunk:
                break
            response.extend(chunk)
            if len(response) > MAX_REQUEST_BYTES:
                raise ValueError("编排器响应超过 8 KiB")
    if not response:
        raise ConnectionError("编排器没有返回响应")
    data = json.loads(response.decode())
    if not isinstance(data, dict):
        raise ValueError("编排器响应必须是 JSON object")
    return data
