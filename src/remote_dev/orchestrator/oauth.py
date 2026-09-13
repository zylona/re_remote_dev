"""短生命周期、独立于 ControlMaster 的 Codex OAuth callback 转发。"""

from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from .forwarder import ForwardProcess, ProxyForwarder
from .model import TargetKey


class OAuthError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class OAuthSession:
    target: TargetKey
    local_port: int = 1455
    remote_port: int = 1455
    forward: ForwardProcess | None = None
    ipv6_forward: ForwardProcess | None = None


class OAuthManager:
    """Own one temporary callback forward without touching ControlMaster."""

    def __init__(self, master: object | None = None, *, local_port: int = 1455, remote_port: int = 1455, timeout: float = 8.0) -> None:
        # ``master`` is accepted for source compatibility with P2 callers;
        # P4 intentionally never uses it for forwarding.
        del master
        self.local_port = local_port
        self.remote_port = remote_port
        self.timeout = timeout
        self._active: OAuthSession | None = None
        self._lock = threading.Lock()

    @property
    def active(self) -> OAuthSession | None:
        with self._lock:
            return self._active

    def _command(self, target: TargetKey, identity_file: Path, bind: str) -> list[str]:
        command = [
            "ssh", "-F", "/dev/null", "-i", str(identity_file.expanduser()),
            "-o", "ControlMaster=no", "-o", "ControlPath=none",
            "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3", "-N", "-L",
            f"{bind}:{self.local_port}:127.0.0.1:{self.remote_port}",
            f"{target.user}@{target.hostname}",
        ]
        if target.port != 22:
            command[3:3] = ["-p", str(target.port)]
        return command

    def start(self, target: TargetKey, *, identity_file: Path | None = None, control_path: str | None = None) -> OAuthSession:
        """Start required IPv4 and best-effort IPv6 callback forwarders."""
        del control_path  # compatibility with the pre-P4 API
        if identity_file is None:
            raise OAuthError("MASTER_UNAVAILABLE", "OAuth 回调需要目标 SSH 私钥路径；请重新建立受管会话")
        with self._lock:
            if self._active is not None:
                if self._active.target == target:
                    return self._active
                raise OAuthError("OAUTH_BUSY", "已有另一个目标正在进行 OAuth 登录，请先完成或取消它")
            try:
                primary = self._start_process(target, identity_file, "127.0.0.1")
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                raise OAuthError("PORT_CONFLICT", f"无法建立本地 1455→远端 1455 转发：{exc}") from exc
            try:
                ipv6 = self._start_process(target, identity_file, "[::1]")
            except (OSError, RuntimeError, subprocess.SubprocessError):
                ipv6 = None
            self._active = OAuthSession(target, self.local_port, self.remote_port, primary, ipv6)
            return self._active

    def _start_process(self, target: TargetKey, identity_file: Path, bind: str) -> ForwardProcess:
        process = subprocess.Popen(
            self._command(target, identity_file, bind), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            process.wait(timeout=0.15)
        except subprocess.TimeoutExpired:
            return ForwardProcess(target, "oauth", process, self.remote_port, self.local_port)
        raise RuntimeError("SSH OAuth forwarder 启动失败（端口冲突或 SSH 拒绝转发）")

    def finish(self, target: TargetKey | None = None) -> bool:
        with self._lock:
            session = self._active
            if session is None:
                return False
            if target is not None and session.target != target:
                raise OAuthError("TARGET_NOT_FOUND", "目标不是当前活跃 OAuth 会话")
            ProxyForwarder.stop(session.ipv6_forward)
            ProxyForwarder.stop(session.forward)
            self._active = None
            return True

    cancel = finish
