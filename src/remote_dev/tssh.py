"""Small standalone SSH resolver used by the release ``tssh`` command."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

try:
    from .lease_config import load as load_lease_settings
except ImportError:  # Direct ``python src/remote_dev/tssh.py`` development entrypoint.
    from remote_dev.lease_config import load as load_lease_settings


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


def _cleanup() -> int:
    try:
        result = _request({"op": "cleanup"}, 5)
    except (OSError, TimeoutError, ValueError, ConnectionError) as exc:
        print(f"tssh: 本地编排器不可用：{exc}", file=os.sys.stderr)
        return 1
    if not result.get("ok"):
        print(f"tssh: 清理失败：{result.get('error', 'unknown')}", file=os.sys.stderr)
        return 1
    print(f"tssh: 已清理 {result.get('cleaned_endpoints', 0)} 个代理 endpoint")
    return 0


def _endpoint_digest(host: str, port: int) -> str:
    return hashlib.sha256(f"{host}:{port}|".encode()).hexdigest()[:32]


def _status() -> int:
    try:
        result = _request({"op": "status"}, 5)
    except (OSError, TimeoutError, ValueError, ConnectionError) as exc:
        print(f"tssh: 本地编排器不可用：{exc}", file=sys.stderr)
        return 1
    if not result.get("ok"):
        print(f"tssh: 状态查询失败：{result.get('error', 'unknown')}", file=sys.stderr)
        return 1
    endpoints = result.get("endpoints", [])
    print("tssh endpoint status")
    print("─" * 112)
    print(f"{'HOST':<28} {'SSH PORT':>8}  {'TYPE':<10} {'STATE':<10} {'OWNER':<16} {'SESSIONS':>8}  {'DIGEST':<12}")
    print("─" * 112)
    if not endpoints:
        print("(no active tssh proxy leases)")
    for item in endpoints:
        endpoint = item.get("endpoint", {})
        host = str(endpoint.get("hostname", "?"))
        port = str(endpoint.get("port", "?"))
        print(f"{host:<28} {port:>8}  {str(item.get('mode', 'SESSION')):<10} {str(item.get('state', '?')):<10} {str(item.get('owner') or '—'):<16} {int(item.get('session_count', 0)):>8}  {str(item.get('endpoint_digest', ''))[:12]}")
        forwards = item.get("forwards")
        if not isinstance(forwards, list):
            # Compatibility with an older orchestrator response.  These are
            # the two forwards created by the managed endpoint unit.
            forwards = [
                {"kind": "proxy", "remote_port": 4227, "local_port": 4227, "state": item.get("state", "?")},
                {"kind": "event", "remote_port": 4228, "local_port": 4230, "state": item.get("state", "?")},
            ]
        for index, forward in enumerate(forwards):
            if not isinstance(forward, dict):
                continue
            branch = "└─" if index == len(forwards) - 1 else "├─"
            kind = str(forward.get("kind", "forward"))
            remote = f"127.0.0.1:{forward.get('remote_port', '?')}"
            local = f"127.0.0.1:{forward.get('local_port', '?')}"
            state = str(forward.get("state", item.get("state", "?")))
            print(f"{'':<28} {'':>8}  {branch} {kind:<8} remote {remote:<21} ← local {local:<18} {state}")
    print("─" * 112)
    print("说明：remote 是目标设备上的监听端口；local 是本机端口。proxy=4227 代理，event=4228→本机4230 回调通道。")
    return 0


def _service_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "systemd/user"


def _persistent_command(destination: str, identity: str | None, port: int) -> list[str]:
    launcher = Path(sys.argv[0]).resolve()
    command = [str(launcher)] if launcher.suffix != ".py" and os.access(launcher, os.X_OK) else [sys.executable, str(launcher)]
    args = command + ["--persistent-run", destination]
    if identity:
        args.extend(["--identity", identity])
    if port != 22:
        args.extend(["--port", str(port)])
    return args


def _persist(raw: list[str], stop: bool = False) -> int:
    parser = argparse.ArgumentParser(prog=f"tssh {'stop' if stop else 'persist'}")
    parser.add_argument("destination", help="目标，格式为 user@host 或 host")
    parser.add_argument("-i", "--identity")
    parser.add_argument("-p", "--port", type=int, default=22)
    args = parser.parse_args(raw[1:])
    try:
        user, host = _parse_destination(args.destination)
    except ValueError as exc:
        parser.error(str(exc))
    if not 1 <= args.port <= 65535:
        parser.error("SSH 端口必须位于 1-65535")
    name = f"tssh-session-{_endpoint_digest(host, args.port)}.service"
    path = _service_path() / name
    if stop:
        subprocess.run(["systemctl", "--user", "disable", "--now", name], check=False)
        path.unlink(missing_ok=True)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        print(f"tssh: 已停止持久代理 {user}@{host}:{args.port}")
        return 0
    identity = _resolve_identity(args.destination, args.identity)
    command = _persistent_command(args.destination, identity, args.port)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text("""[Unit]\nDescription=tssh persistent proxy (%s)\nAfter=network-online.target\nWants=network-online.target\n\n[Service]\nType=simple\nExecStart=%s\nRestart=on-failure\nRestartSec=10\nNoNewPrivileges=yes\nMemoryMax=64M\nTasksMax=16\n\n[Install]\nWantedBy=default.target\n""" % (host, shlex.join(command)), encoding="utf-8")
    path.chmod(0o600)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", name], check=True)
    print(f"tssh: 已启用持久代理 {user}@{host}:{args.port}")
    print(f"      unit: {name}")
    return 0


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
    raw_argv = list(argv if argv is not None else os.sys.argv[1:])
    if not raw_argv or raw_argv == ["help"] or raw_argv == ["--help"]:
        print("用法：tssh user@host [SSH 参数]\n\n管理命令：\n  tssh list                         查看代理 endpoint\n  tssh persist user@host [-i KEY]  后台持久保持代理\n  tssh stop user@host              关闭指定持久代理\n  tssh cleanup                     清理全部会话级代理\n  tssh --version                   查看版本")
        return 0
    if raw_argv[0] == "list":
        return _status()
    if raw_argv[0] == "persist":
        return _persist(raw_argv)
    if raw_argv[0] == "stop":
        return _persist(raw_argv, stop=True)
    if raw_argv == ["cleanup"] or raw_argv == ["clear"]:
        return _cleanup()
    parser = argparse.ArgumentParser(prog="tssh", description="使用 remote-dev 4227 转发连接 SSH 目标")
    parser.add_argument("--version", action="version", version=f"%(prog)s {_version()}")
    parser.add_argument("destination", help="SSH 目标，格式为 user@host 或 host")
    parser.add_argument("-i", "--identity", help="SSH 私钥路径")
    parser.add_argument("-p", "--port", type=int, default=22, help="SSH 端口")
    parser.add_argument("--persistent-run", action="store_true", help=argparse.SUPPRESS)
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
    lease_settings = load_lease_settings()

    def heartbeat() -> None:
        while not stop.wait(lease_settings.heartbeat_interval):
            try:
                heartbeat_request = {"op": "heartbeat", "endpoint": endpoint, "session_id": session_id}
                response = _request(heartbeat_request, 5)
                # The orchestrator intentionally keeps lease state in memory.
                # If its socket service was restarted, re-acquire this still
                # live tssh process instead of leaving it with a dead tunnel.
                if response.get("error") == "LEASE_CLEARED":
                    return
                if not response.get("ok"):
                    retry = {"op": "acquire", "endpoint": endpoint, "candidate": candidate, "session_id": session_id}
                    if args.persistent_run:
                        retry["event_forward"] = False
                        retry["persistent"] = True
                    _request(retry, 5)
            except (OSError, TimeoutError, ValueError, ConnectionError):
                # A transient restart is harmless; the next heartbeat retries
                # and the explicit acquire above restores the lease.
                try:
                    retry = {"op": "acquire", "endpoint": endpoint, "candidate": candidate, "session_id": session_id}
                    if args.persistent_run:
                        retry["event_forward"] = False
                        retry["persistent"] = True
                    _request(retry, 5)
                except (OSError, TimeoutError, ValueError, ConnectionError):
                    pass

    try:
        acquire_payload: dict[str, Any] = {"op": "acquire", "endpoint": endpoint, "candidate": candidate, "session_id": session_id}
        if args.persistent_run:
            # A persistent proxy is intentionally HTTP-only.  Codex OAuth and
            # event forwarding belongs to an interactive tssh session.
            acquire_payload["event_forward"] = False
            acquire_payload["persistent"] = True
        acquired = _request(acquire_payload, 5)
        if not acquired.get("ok"):
            print(f"tssh: 无法申请代理会话：{acquired.get('error', 'unknown')}", file=os.sys.stderr)
            return 1
        heartbeat_thread = threading.Thread(target=heartbeat, name="tssh-lease-heartbeat", daemon=True)
        heartbeat_thread.start()
        if args.persistent_run:
            # A persistent proxy has no interactive terminal.  Keep only the
            # lease process alive; the actual SSH -R tunnel is owned by the
            # endpoint unit managed by the orchestrator.
            signal.signal(signal.SIGTERM, lambda *_: stop.set())
            signal.signal(signal.SIGINT, lambda *_: stop.set())
            while not stop.wait(3600):
                pass
            return 0
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
