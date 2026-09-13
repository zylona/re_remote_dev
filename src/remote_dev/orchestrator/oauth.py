"""Codex OAuth callback forwarding over an existing SSH ControlMaster (P4).

The manager deliberately does not proxy OAuth data or open a browser.  Codex keeps
its original callback URL (localhost:1455); this module only asks OpenSSH to add
and later remove a temporary LocalForward on the already authenticated master.
"""

from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass

from .master import MasterManager
from .model import TargetKey


class OAuthError(RuntimeError):
    """Stable, user-actionable OAuth forwarding failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class OAuthSession:
    target: TargetKey
    local_port: int = 1455
    remote_port: int = 1455


class OAuthManager:
    """Maintain at most one temporary Codex callback forward per controller."""

    def __init__(self, master: MasterManager | None = None, *, local_port: int = 1455, remote_port: int = 1455, timeout: float = 8.0) -> None:
        self.master = master or MasterManager()
        self.local_port = local_port
        self.remote_port = remote_port
        self.timeout = timeout
        self._active: OAuthSession | None = None
        self._lock = threading.Lock()

    @property
    def active(self) -> OAuthSession | None:
        with self._lock:
            return self._active

    def _forward_args(self, target: TargetKey, control_path: str | None = None) -> list[str]:
        return [
            "ssh", "-F", "/dev/null", "-S", control_path or str(self.master.control_path(target)),
            "-O", "forward", "-L",
            f"127.0.0.1:{self.local_port}:127.0.0.1:{self.remote_port}",
            self.master.destination(target),
        ]

    def _cancel_args(self, target: TargetKey, control_path: str | None = None) -> list[str]:
        args = self._forward_args(target, control_path)
        args[args.index("forward")] = "cancel"
        return args

    def start(self, target: TargetKey, *, control_path: str | None = None) -> OAuthSession:
        """Add the callback forward, rejecting a second active target."""
        with self._lock:
            if self._active is not None:
                if self._active.target == target:
                    return self._active
                raise OAuthError("OAUTH_BUSY", "已有另一个目标正在进行 OAuth 登录，请先完成或取消它")
            result = subprocess.run(
                self._forward_args(target, control_path), check=False, capture_output=True,
                text=True, timeout=self.timeout,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "SSH 未接受 callback 转发").strip()
                raise OAuthError("PORT_CONFLICT", f"无法建立本地 1455→远端 1455 转发：{detail}")
            self._active = OAuthSession(target, self.local_port, self.remote_port)
            return self._active

    def finish(self, target: TargetKey | None = None) -> bool:
        """Cancel the active callback forward; safe to call repeatedly."""
        with self._lock:
            session = self._active
            if session is None:
                return False
            if target is not None and session.target != target:
                raise OAuthError("TARGET_NOT_FOUND", "目标不是当前活跃 OAuth 会话")
            result = subprocess.run(
                self._cancel_args(session.target), check=False,
                capture_output=True, text=True, timeout=self.timeout,
            )
            self._active = None
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "SSH 未能取消 callback 转发").strip()
                raise OAuthError("CLEANUP_FAILED", f"OAuth 转发清理失败：{detail}")
            return True

    cancel = finish
