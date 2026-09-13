"""Dedicated SSH forwarder processes, separate from interactive SSH sessions."""

from __future__ import annotations

import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .model import TargetKey


@dataclass(slots=True)
class ForwardProcess:
    target: TargetKey
    kind: str
    process: subprocess.Popen[bytes]
    remote_port: int
    local_port: int


class ProxyForwarder:
    """Own one standalone ``ssh -N -R`` process.

    It intentionally never uses ControlMaster: proxy traffic must not share
    a TCP transport with user terminals.  The default remote port remains
    4227 for compatibility; callers may provide a per-target allocation.
    """

    def __init__(self, *, remote_port: int = 4227, local_port: int = 4227, port_range: tuple[int, int] = (40000, 60000)) -> None:
        self.remote_port = remote_port
        self.local_port = local_port
        self.port_range = port_range

    def command(self, target: TargetKey, identity: Path, *, remote_port: int | None = None) -> list[str]:
        port = remote_port or self.remote_port
        command = [
            "ssh", "-F", "/dev/null", "-i", str(identity.expanduser()),
            "-o", "ControlMaster=no", "-o", "ControlPath=none",
            "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3", "-N",
            "-R", f"127.0.0.1:{port}:127.0.0.1:{self.local_port}",
            f"{target.user}@{target.hostname}",
        ]
        if target.port != 22:
            command[3:3] = ["-p", str(target.port)]
        return command

    def start(self, target: TargetKey, identity: Path, *, remote_port: int | None = None) -> ForwardProcess:
        port = remote_port or self.remote_port
        # Do not pipe a long-lived SSH stderr stream: warnings could fill the
        # pipe and block the forwarder, which would reintroduce backpressure.
        process = subprocess.Popen(self.command(target, identity, remote_port=port), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.15)
        if process.poll() is not None:
            raise RuntimeError("代理 forwarder 启动失败（SSH 进程已退出）")
        return ForwardProcess(target, "proxy", process, port, self.local_port)

    @staticmethod
    def stop(forward: ForwardProcess | None) -> None:
        if forward is None:
            return
        if forward.process.poll() is None:
            forward.process.send_signal(signal.SIGTERM)
            try:
                forward.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                forward.process.kill()
                try:
                    forward.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    # The process is no longer useful; avoid allowing a
                    # stubborn child to block OAuth/session cleanup forever.
                    pass


class EventForwarder(ProxyForwarder):
    """Dedicated reverse forward for the Codex lifecycle event channel."""

    def __init__(self, *, remote_port: int = 4228, local_port: int = 4230) -> None:
        super().__init__(remote_port=remote_port, local_port=local_port)

    def start(self, target: TargetKey, identity: Path, *, remote_port: int | None = None) -> ForwardProcess:
        forward = super().start(target, identity, remote_port=remote_port)
        forward.kind = "event"
        return forward
