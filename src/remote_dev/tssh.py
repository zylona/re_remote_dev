"""Small standalone SSH resolver used by the release ``tssh`` command."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import secrets
import signal
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

try:
    from .lease_config import load as load_lease_settings
    from .file_transfer import EndpointIdentity
    from .download import DownloadManager, EndpointConnection
    from .preview_bridge import PreviewBridge, new_context
    from .upload import UploadConnection, UploadError, put_file
except ImportError:  # Direct ``python src/remote_dev/tssh.py`` development entrypoint.
    from remote_dev.lease_config import load as load_lease_settings
    from remote_dev.file_transfer import EndpointIdentity
    from remote_dev.download import DownloadManager, EndpointConnection
    from remote_dev.preview_bridge import PreviewBridge, new_context
    from remote_dev.upload import UploadConnection, UploadError, put_file


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


def _downloads_status() -> int:
    """Show local rget/rdo status files independently of the active PTY."""
    root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "tssh" / "rdo"
    jobs = sorted(root.glob("*/jobs/*.json"), key=lambda path: path.stat().st_mtime, reverse=True) if root.is_dir() else []
    print("tssh download status")
    print("─" * 116)
    print(f"{'STATE':<20} {'PROGRESS':>18} {'SPEED':>12} {'ETA':>10}  {'REMOTE PATH'}")
    print("─" * 116)
    if not jobs:
        print("(no download jobs)")
    for path in jobs[:30]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        done = int(value.get("bytes_done", 0) or 0)
        total = int(value.get("bytes_total", 0) or 0)
        progress = f"{done}/{total}" if total else f"{done}/?"
        speed = float(value.get("speed_bps", 0) or 0)
        eta = value.get("eta_seconds")
        speed_text = f"{speed / 1024:.1f} KiB/s" if speed else "—"
        eta_text = f"{float(eta):.1f}s" if eta is not None else "—"
        state = str(value.get("state", "UNKNOWN"))
        error = value.get("error")
        if error:
            state = f"{state}: {error}"
        print(f"{state:<20} {progress:>18} {speed_text:>12} {eta_text:>10}  {value.get('remote_path', '?')}")
    print("─" * 116)
    print("说明：rget/rdo 通常会在当前命令中显示进度并等待完成；状态文件位于 ~/.cache/tssh/rdo/*/jobs/。")
    return 0


def _service_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "systemd/user"


def _persistent_command(destination: str, identity: str | None, port: int) -> list[str]:
    # ``python -m remote_dev.tssh`` sets ``sys.argv[0]`` to the module file.
    # Persisting that path directly breaks relative imports in a systemd unit.
    # Prefer the release wrapper, which exports the package PYTHONPATH, and
    # keep an explicit module fallback for source/dev installs.
    package_root = Path(__file__).resolve().parents[1]
    candidates = [Path(__file__).resolve().parents[2] / "bin/tssh"]
    installed = shutil.which("tssh")
    if installed:
        candidates.append(Path(installed).resolve())
    launcher = next((candidate for candidate in candidates if candidate.is_file() and os.access(candidate, os.X_OK)), None)
    if launcher is not None:
        command = [str(launcher)]
    else:
        command = ["/usr/bin/env", f"PYTHONPATH={package_root}", sys.executable, "-m", "remote_dev.tssh"]
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


def _put(raw: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="tssh put", description="上传本地文件到远程固定目录")
    parser.add_argument("local_path", help="本地普通文件路径")
    parser.add_argument("destination", help="目标，格式为 user@host 或 host")
    parser.add_argument("-i", "--identity", help="SSH 私钥路径")
    parser.add_argument("-p", "--port", type=int, default=22, help="SSH 端口")
    args = parser.parse_args(raw[1:])
    if not 1 <= args.port <= 65535:
        parser.error("SSH 端口必须位于 1-65535")
    try:
        user, host = _parse_destination(args.destination)
    except ValueError as exc:
        parser.error(str(exc))
    local_path = Path(args.local_path).expanduser()
    identity = _resolve_identity(args.destination, args.identity)
    print(f"tssh put: {local_path.resolve()}")
    print(f"目标：{user}@{host}:{args.port}")
    print("远程目录：~/Uploads/remote-dev")
    try:
        remote_path = put_file(UploadConnection(host, args.port, user, identity), local_path)
    except (UploadError, OSError, subprocess.SubprocessError) as exc:
        print(f"tssh put: {exc}", file=sys.stderr)
        return 1
    print(f"远程路径：{remote_path}")
    return 0


def _parse_destination(value: str) -> tuple[str, str]:
    if "@" in value:
        user, host = value.split("@", 1)
    else:
        user, host = os.environ.get("USER", "unknown"), value
    if not user or not host or any(char.isspace() for char in (user + host)):
        raise ValueError("目标必须是 user@host 或 host")
    return user, host


def _ssh_command(destination: str, identity: str | None, port: int, passthrough: list[str], remote_command: str | None = None) -> list[str]:
    """Build native ssh argv with the destination before an optional command."""
    command = ["ssh"]
    if identity:
        command.extend(["-i", identity])
    if port != 22:
        command.extend(["-p", str(port)])
    if remote_command is not None:
        command.append("-t")
    command.append(destination)
    if remote_command is not None:
        command.append(remote_command)
    command.extend(passthrough)
    return command


def _preview_forward(destination: str, identity: str | None, port: int, local_port: int) -> tuple[subprocess.Popen[bytes], int] | None:
    """Start a best-effort per-session reverse leg for rget/rdo."""
    if not identity and not os.environ.get("SSH_AUTH_SOCK"):
        return None
    for _ in range(8):
        remote_port = secrets.randbelow(20_000) + 40_000
        command = [
            "ssh", "-F", "/dev/null", "-N", "-T",
            "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
        ]
        if identity:
            command.extend(["-i", identity])
        if port != 22:
            command.extend(["-p", str(port)])
        command.extend(["-R", f"127.0.0.1:{remote_port}:127.0.0.1:{local_port}", destination])
        try:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            return None
        time.sleep(0.15)
        if process.poll() is None:
            return process, remote_port
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
    return None


def _stop_preview_forward(forward: tuple[subprocess.Popen[bytes], int] | None) -> None:
    if forward is None:
        return
    process, _ = forward
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()


def _preview_shell_command(
    endpoint_digest: str,
    session_id: str,
    nonce: str,
    remote_port: int,
    proxy_port: int = 4227,
) -> str:
    values = {
        "RDO_ENDPOINT": endpoint_digest,
        "RDO_SESSION": session_id,
        "RDO_NONCE": nonce,
        "RDO_PORT": str(remote_port),
        # The tssh lease already guarantees the remote loopback proxy.  The
        # marker lets zsh export proxy variables without depending on `ss`,
        # which is not present on every supported distribution.
        "REMOTE_DEV_TSSH_PROXY_PORT": str(proxy_port),
        "http_proxy": f"http://127.0.0.1:{proxy_port}",
        "https_proxy": f"http://127.0.0.1:{proxy_port}",
        "HTTP_PROXY": f"http://127.0.0.1:{proxy_port}",
        "HTTPS_PROXY": f"http://127.0.0.1:{proxy_port}",
        "no_proxy": "127.0.0.1,localhost",
        "NO_PROXY": "127.0.0.1,localhost",
    }
    exports = "; ".join(f"export {key}={shlex.quote(value)}" for key, value in values.items())
    return f"{exports}; exec \"${{SHELL:-/bin/sh}}\" -l"


def _proxy_shell_command(proxy_port: int = 4227) -> str:
    """Start a native login shell while carrying the active proxy marker."""
    values = {
        "REMOTE_DEV_TSSH_PROXY_PORT": str(proxy_port),
        "http_proxy": f"http://127.0.0.1:{proxy_port}",
        "https_proxy": f"http://127.0.0.1:{proxy_port}",
        "HTTP_PROXY": f"http://127.0.0.1:{proxy_port}",
        "HTTPS_PROXY": f"http://127.0.0.1:{proxy_port}",
        "no_proxy": "127.0.0.1,localhost",
        "NO_PROXY": "127.0.0.1,localhost",
    }
    exports = "; ".join(f"export {key}={shlex.quote(value)}" for key, value in values.items())
    return f"{exports}; exec \"${{SHELL:-/bin/sh}}\" -l"


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
        print("用法：tssh user@host [SSH 参数]\n\n管理命令：\n  tssh list                         查看代理 endpoint\n  tssh downloads                    查看 rget/rdo 下载进度\n  tssh put FILE user@host          上传到 ~/Uploads/remote-dev\n  tssh persist user@host [-i KEY]  后台持久保持代理\n  tssh stop user@host              关闭指定持久代理\n  tssh cleanup                     清理全部会话级代理\n  tssh --version                   查看版本")
        return 0
    if raw_argv[0] == "list":
        return _status()
    if raw_argv[0] in {"downloads", "download-status"}:
        return _downloads_status()
    if raw_argv[0] == "put":
        return _put(raw_argv)
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
    stop = threading.Event()
    heartbeat_thread: threading.Thread | None = None
    preview_bridge: PreviewBridge | None = None
    preview_forward: tuple[subprocess.Popen[bytes], int] | None = None
    download_manager: DownloadManager | None = None
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
        # P2 adds a separate reverse leg.  Failure is intentionally soft: the
        # SSH session remains a native interactive session without preview.
        preview_context = new_context(EndpointIdentity(host, args.port, user).digest(), session_id)
        control_dir = Path(os.environ.get("XDG_RUNTIME_DIR", f"/tmp/tssh-{os.getuid()}")) / "tssh" / "preview-cm"
        control_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        control_path = control_dir / hashlib.sha256(f"{host}:{args.port}|{user}".encode()).hexdigest()[:32]
        download_manager = DownloadManager(EndpointConnection(host, args.port, user, identity, str(control_path)))
        preview_bridge = PreviewBridge(preview_context, download_manager.submit)
        preview_context = preview_bridge.start()
        preview_forward = None if args.ssh_args else _preview_forward(args.destination, identity, args.port, preview_context.local_port)
        if preview_forward is not None:
            preview_context = type(preview_context)(
                preview_context.endpoint_digest,
                preview_context.session_id,
                preview_context.nonce,
                preview_context.local_port,
                preview_forward[1],
            )
            preview_bridge.context = preview_context
            acquire_preview = {
                "endpoint_digest": preview_context.endpoint_digest,
                "session_id": preview_context.session_id,
                "nonce": preview_context.nonce,
                "local_port": preview_context.local_port,
                "remote_port": preview_context.remote_port,
            }
            try:
                _request({"op": "preview_register", "session_id": session_id, "endpoint": endpoint, "preview": acquire_preview}, 2)
            except (OSError, TimeoutError, ValueError, ConnectionError):
                pass
            remote_command = _preview_shell_command(
                preview_context.endpoint_digest,
                preview_context.session_id,
                preview_context.nonce,
                preview_context.remote_port,
            )
            ssh_args = _ssh_command(args.destination, identity, args.port, args.ssh_args, remote_command)
        else:
            preview_bridge.stop()
            preview_bridge = None
            # Even when the optional preview reverse leg cannot be created,
            # the normal 4227 lease is still valid.  Carry its marker into a
            # native login shell so Nvim and Git inherit the HTTP proxy.
            remote_command = _proxy_shell_command() if not args.ssh_args else None
            ssh_args = _ssh_command(args.destination, identity, args.port, args.ssh_args, remote_command)
        return subprocess.run(ssh_args, check=False).returncode
    except (OSError, TimeoutError, ValueError, ConnectionError) as exc:
        print(f"tssh: 本地编排器不可用：{exc}", file=os.sys.stderr)
        return 1
    finally:
        stop.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=1)
        if preview_bridge is not None:
            preview_bridge.stop()
        _stop_preview_forward(preview_forward)
        if download_manager is not None:
            download_manager.shutdown()
        try:
            _request({"op": "release", "endpoint": endpoint, "session_id": session_id}, 2)
        except (OSError, TimeoutError, ValueError, ConnectionError):
            pass


if __name__ == "__main__":
    raise SystemExit(main())
