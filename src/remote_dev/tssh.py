"""Small standalone SSH resolver used by the release ``tssh`` command."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any


def _version() -> str:
    try:
        return (Path(__file__).resolve().parents[2] / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


def _socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/tmp/tssh-{os.getuid()}")
    return Path(runtime) / "tssh" / "orchestrator.sock"


def _request(payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    raw = (json.dumps(payload, separators=(",", ":")) + "\n").encode()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(timeout)
        conn.connect(str(_socket_path()))
        conn.sendall(raw)
        response = bytearray()
        while not response.endswith(b"\n"):
            chunk = conn.recv(4096)
            if not chunk:
                break
            response.extend(chunk)
    if not response:
        raise ConnectionError("编排器没有返回响应")
    value = json.loads(response.decode())
    if not isinstance(value, dict):
        raise ValueError("编排器响应必须是 JSON object")
    return value


def _parse_destination(value: str) -> tuple[str, str]:
    if "@" in value:
        user, host = value.split("@", 1)
    else:
        user, host = os.environ.get("USER", "unknown"), value
    if not user or not host or any(char.isspace() for char in (user + host)):
        raise ValueError("目标必须是 user@host 或 host")
    return user, host


def _ssh_command(destination: str, identity: str | None, port: int, passthrough: list[str]) -> list[str]:
    """Build native ssh argv with the destination before an optional command."""
    command = ["ssh"]
    if identity:
        command.extend(["-i", identity])
    if port != 22:
        command.extend(["-p", str(port)])
    command.append(destination)
    command.extend(passthrough)
    return command


def _resolve_identity(destination: str, explicit: str | None) -> str | None:
    """Resolve the first usable IdentityFile from OpenSSH's effective config."""
    if explicit:
        return str(Path(explicit).expanduser())
    try:
        result = subprocess.run(
            ["ssh", "-G", destination],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in result.stdout.splitlines():
        key, _, value = line.partition(" ")
        if key != "identityfile" or not value:
            continue
        candidate = Path(value).expanduser()
        if candidate.is_file():
            return str(candidate)
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tssh", description="使用 remote-dev 4227 转发连接 SSH 目标")
    parser.add_argument("--version", action="version", version=f"%(prog)s {_version()}")
    parser.add_argument("destination", help="SSH 目标，格式为 user@host 或 host")
    parser.add_argument("-i", "--identity", help="SSH 私钥路径")
    parser.add_argument("-p", "--port", type=int, default=22, help="SSH 端口")
    args, passthrough = parser.parse_known_args(argv)
    args.ssh_args = passthrough
    if not 1 <= args.port <= 65535:
        parser.error("SSH 端口必须位于 1-65535")
    try:
        user, host = _parse_destination(args.destination)
    except ValueError as exc:
        parser.error(str(exc))
    session_id = uuid.uuid4().hex
    endpoint = {"hostname": host, "port": args.port}
    candidate: dict[str, str] = {"user": user}
    identity = _resolve_identity(args.destination, args.identity)
    if identity:
        candidate["identity_file"] = identity
    ssh_args = _ssh_command(args.destination, identity, args.port, args.ssh_args)
    stop = threading.Event()
    heartbeat_thread: threading.Thread | None = None

    def heartbeat() -> None:
        while not stop.wait(30):
            try:
                if not _request({"op": "heartbeat", "endpoint": endpoint, "session_id": session_id}, 5).get("ok"):
                    return
            except (OSError, TimeoutError, ValueError, ConnectionError):
                continue

    try:
        acquired = _request({"op": "acquire", "endpoint": endpoint, "candidate": candidate, "session_id": session_id}, 5)
        if not acquired.get("ok"):
            print(f"tssh: 无法申请代理会话：{acquired.get('error', 'unknown')}", file=os.sys.stderr)
            return 1
        heartbeat_thread = threading.Thread(target=heartbeat, name="tssh-lease-heartbeat", daemon=True)
        heartbeat_thread.start()
        return subprocess.run(ssh_args, check=False).returncode
    except (OSError, TimeoutError, ValueError, ConnectionError) as exc:
        print(f"tssh: 本地编排器不可用：{exc}", file=os.sys.stderr)
        return 1
    finally:
        stop.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=1)
        try:
            _request({"op": "release", "endpoint": endpoint, "session_id": session_id}, 2)
        except (OSError, TimeoutError, ValueError, ConnectionError):
            pass


if __name__ == "__main__":
    raise SystemExit(main())
